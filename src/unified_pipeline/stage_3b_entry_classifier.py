"""
Stage 3b: Entry Classification

Classifies individual CV entries to taxonomy codes using:
1. Entry content (primary signal)
2. Hierarchy context from Stage 3a (guidance/constraints)
3. Taxonomy confusion matrix (edge case handling)

Input:
  - Stage 2 entries (JSON)
  - Stage 3a header taxonomy mappings (JSON)
Output:
  - Classified entries with taxonomy codes (JSON)

The entry content drives classification, but hierarchy context helps
resolve ambiguity and provides constraints.
"""

import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime
from collections.abc import Callable
# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from unified_pipeline.llm_client import call_llm

# Post-classification auto-correction validators
from core.validators.structural_header import apply_structural_corrections
from core.validators.committee_position_corrector import apply_committee_corrections
from core.validators.reasoning_consistency_checker import apply_reasoning_corrections
from core.validators.grant_status_corrector import apply_grant_status_corrections
from core.validators.hierarchy_mismatch_flagger import flag_hierarchy_mismatches, get_mismatch_summary
from core.validators.teaching_leadership_corrector import apply_teaching_leadership_corrections
from core.validators.leadership_level_corrector import apply_leadership_level_corrections
from core.validators.adjunct_position_corrector import apply_adjunct_position_corrections
from core.validators.position_subcode_reconciler import apply_position_subcode_reconciliation
from core.validators.training_compliance_corrector import apply_training_compliance_corrections
from core.validators.invited_talk_corrector import apply_invited_talk_corrections
from core.validators.grant_position_corrector import apply_grant_position_corrections
from core.validators.wcm_table_corrector import apply_wcm_table_corrections
from core.validators.prose_mentee_corrector import apply_prose_mentee_corrections
from core.validators.template_scaffold import apply_template_scaffold_corrections
from core.validators.block_coherence_corrector import apply_block_coherence_corrections
from core.validators.appointment_funding_corrector import apply_appointment_funding_corrections
from core.validators.event_volunteer_corrector import apply_event_volunteer_corrections

# Every name below is re-exported from this module by being imported here: it
# is the public import surface of stage 3b, pinned by
# tests/test_stage3b_import_surface.py (#522; pattern from the stage6 split,
# #398/#500). run_full_pipeline.py and the backend orchestrator.py are parallel
# drivers over the same stage_* modules and share no code, so a name that moves
# without a re-export lands silently in whichever driver nobody ran. The
# blanket `noqa: F401` is deliberate -- some of these have no caller left in
# this file and exist purely to keep that surface intact.
#
# NOTE: `call_llm` above is NOT part of that surface -- nothing imports it from
# here. Tests that stub the LLM must patch `unified_pipeline.stage3b.classify`,
# where every classification call site now lives; patching this module's copy
# would cover only the flag-gated block-coherence closure in run_stage_3b.
from unified_pipeline.stage3b.context import (  # noqa: F401
    TaxonomyContext,
    build_mapping_index,
    get_taxonomy_context,
)
from unified_pipeline.stage3b.io import (  # noqa: F401
    _normalize_taxonomy_mappings,
    _safe_float,
    load_stage_2_entries,
    load_stage_3a_mappings,
    load_taxonomy,
)
from unified_pipeline.stage3b.prompt import (  # noqa: F401
    _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE,
    build_taxonomy_codes_for_prompt,
)
from unified_pipeline.stage3b.header_pin import apply_header_pin
from unified_pipeline.core.batch_pool import map_in_order, workers_from_config
from unified_pipeline.stage3b.classify import (  # noqa: F401
    _BatchStats,
    _build_taxonomy_ref_for_batch,
    _classify_one_batch,
    ClassificationStats,
    classify_entries_batch,
    detect_duplicates,
    group_entries_by_hierarchy,
    NO_HIERARCHY_KEY,
    reconnect_fragments,
    validate_t_classifications,
)

logger = logging.getLogger(__name__)

# I/O-bound stage (LLM round trips, not CPU); the default is sized under the
# per-pod semaphore so one run cannot starve the others admitted alongside it
# (#881). Knob: CVICHE_STAGE3B_GROUP_WORKERS, env var or llm yaml key.
STAGE3B_GROUP_WORKERS = workers_from_config("CVICHE_STAGE3B_GROUP_WORKERS")


_GroupResult = tuple[list[dict], ClassificationStats, list[str]]


def _group_progress_printer(hierarchy_keys: list[str]) -> Callable[[int, _GroupResult], None]:
    """Build a map_in_order ``on_result`` callback: one atomic print per
    finished group, numbered by completion.

    map_in_order guarantees ``on_result`` fires only on the CALLING thread,
    one call at a time -- both its serial path and its ``as_completed`` loop
    invoke it inline, never from a pool thread -- so despite the pool
    underneath, this closure is single-threaded: no lock, no ``nonlocal``
    gymnastics beyond the one ``done`` counter needs as a closure variable.
    The ``[N/M]`` line is a parsed contract -- orchestrator.py's
    PROGRESS_PATTERNS read it into the progress bar -- so N counts groups
    *finished*, which stays monotonic however the pool orders completions,
    and the whole block goes out in one print so two groups' lines cannot
    splice (#881).
    """
    done = 0

    def on_result(index: int, result: _GroupResult) -> None:
        nonlocal done
        _, _, lines = result
        done += 1
        print("\n".join([f"[{done}/{len(hierarchy_keys)}] {hierarchy_keys[index][:60]}...", *lines]))

    return on_result


def _classify_group(
    hierarchy_key: str,
    group_entries: list[dict],
    mapping_index: dict,
    taxonomy: dict,
) -> _GroupResult:
    """Classify one hierarchy group; the per-group body of run_stage_3b's loop.

    Returns the progress lines instead of printing them: this runs inside
    map_in_order, on whichever pool thread the call lands on, and that
    thread is never registered with the orchestrator's thread-routed stdout
    (``_RoutedStdout`` dispatches ``write()`` by ``threading.get_ident()``)
    -- a print from here would reach the pod's real stdout instead of the
    run's progress bar and log viewer. run_stage_3b prints the returned
    lines itself, from ``on_result``, which map_in_order guarantees runs on
    the calling thread.
    """
    hierarchy = [] if hierarchy_key == NO_HIERARCHY_KEY else hierarchy_key.split(" > ")
    context = get_taxonomy_context(hierarchy, mapping_index)
    primary_codes = context.get_primary_codes()

    classified, stats = classify_entries_batch(group_entries, context, taxonomy)
    classified, _pinned = apply_header_pin(classified, context)

    lines = [f"    Entries: {len(group_entries)}"]
    if primary_codes:
        codes_display = ", ".join(primary_codes[:3])
        if len(primary_codes) > 3:
            codes_display += f" (+{len(primary_codes) - 3} more)"
        lines.append(f"    Suggested codes: {codes_display}")
    lines.append(f"    ✓ Classified {stats['entries_classified']} entries (${stats['cost']:.4f})")
    return classified, stats, lines


def _resolve_stage_input(stage_label: str, given: str | None, default: Path) -> Path:
    """The given input path, else the stage's default artifact path; raise if it does not exist."""
    path = default if given is None else Path(given)
    if not path.exists():
        raise FileNotFoundError(f"{stage_label} output not found: {path}")
    return path


def _person_name_from_uid(document_uid: str | None) -> str | None:
    """Owner name for structural-header name matching, from a uid like "2071_LastName_FirstName_CV"."""
    name_parts = (document_uid or "").split('_')
    if len(name_parts) < 3:
        return None
    # Skip the numeric prefix and CV/vita/resume suffixes.
    name_parts_clean = [p for p in name_parts if not p.isdigit() and p.lower() not in ('cv', 'vita', 'resume')]
    return ' '.join(name_parts_clean)


def run_stage_3b(
    document_uid: str,
    stage_2_path: str | None = None,
    stage_3a_path: str | None = None,
    output_dir: str | None = None,
    workers: int = STAGE3B_GROUP_WORKERS,
) -> dict:
    """
    Run Stage 3b entry classification.

    Args:
        document_uid: Document identifier
        stage_2_path: Path to Stage 2 entries (optional, will auto-detect)
        stage_3a_path: Path to Stage 3a mappings (optional, will auto-detect)
        output_dir: Output directory (optional, will auto-detect)
        workers: Hierarchy groups classified at once (default
            STAGE3B_GROUP_WORKERS). 1 reproduces the pre-#881 serial loop.

    No `model` parameter: it used to exist here purely to be silently
    dropped -- never forwarded to call_llm() -- so it was removed rather
    than forwarded (#644 review). Stage 3b's model is pinned per-stage in
    llm_config.yaml; the model that actually served each call is recorded
    in the returned stats/output instead (see "model" below, #459).

    Returns:
        Result dict with classified entries, stats, and output path
    """
    print("=" * 80)
    print("STAGE 3b: ENTRY CLASSIFICATION")
    print("=" * 80)
    print()

    base_dir = Path(__file__).parent / "outputs"

    stage_2_path = _resolve_stage_input(
        "Stage 2", stage_2_path, base_dir / "stage_2_entry_extraction" / f"{document_uid}_entries.json")
    stage_3a_path = _resolve_stage_input(
        "Stage 3a", stage_3a_path, base_dir / "stage_3a_header_mappings" / f"{document_uid}_header_taxonomy.json")

    print(f"Stage 2 entries: {stage_2_path}")
    print(f"Stage 3a mappings: {stage_3a_path}")

    # Load inputs
    content_entries, all_entries = load_stage_2_entries(stage_2_path)
    stage_3a_data = load_stage_3a_mappings(stage_3a_path)
    taxonomy = load_taxonomy()

    print(f"Loaded {len(all_entries)} total entries from Stage 2")
    print(f"  Content entries to classify: {len(content_entries)}")
    print(f"  Headers/breaks skipped: {len(all_entries) - len(content_entries)}")

    # Use content entries for classification
    entries = content_entries
    print(f"Loaded taxonomy v{taxonomy['meta']['version']}")
    print()

    # Build mapping index
    mappings = stage_3a_data.get("mappings", [])
    mapping_index = build_mapping_index(mappings)

    # Group entries by hierarchy
    groups = group_entries_by_hierarchy(entries)
    print(f"Grouped entries into {len(groups)} hierarchy groups")
    print()

    # Classify each group
    all_classified = []
    total_stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "entries_classified": 0,
        "groups_processed": 0,
        "llm_batches": 0,
        "failed_batches": 0,
        "llm_classified": 0,
        "fallback_entries": 0,
        "empty_entries": 0,
        "model": None
    }

    results = map_in_order(
        _classify_group,
        [(key, group_entries, mapping_index, taxonomy) for key, group_entries in groups.items()],
        workers,
        on_result=_group_progress_printer(list(groups)),
    )
    for classified, stats, _lines in results:
        all_classified.extend(classified)

        # Update totals
        total_stats["input_tokens"] += stats["input_tokens"]
        total_stats["output_tokens"] += stats["output_tokens"]
        total_stats["total_tokens"] += stats["total_tokens"]
        total_stats["cost"] += stats["cost"]
        total_stats["entries_classified"] += stats["entries_classified"]
        total_stats["groups_processed"] += 1
        total_stats["llm_batches"] += stats["llm_batches"]
        total_stats["failed_batches"] += stats["failed_batches"]
        total_stats["llm_classified"] += stats["llm_classified"]
        total_stats["fallback_entries"] += stats["fallback_entries"]
        total_stats["empty_entries"] += stats["empty_entries"]
        total_stats["model"] = stats.get("model") or total_stats.get("model")

    print()
    print(f"Total: {total_stats['entries_classified']} entries classified")
    print(f"Cost: ${total_stats['cost']:.4f}")
    print(f"Tokens: {total_stats['total_tokens']:,}")

    # A run whose every LLM batch failed emits all-default codes that look
    # like real data (#61: invalid Bedrock model id classified an entire A/B
    # run to fallbacks at $0 with no error). Failing the run is strictly
    # better than completing it with meaningless classifications.
    if total_stats["llm_batches"] > 0 and total_stats["llm_classified"] == 0:
        raise RuntimeError(
            f"Stage 3b produced zero LLM classifications across "
            f"{total_stats['llm_batches']} batches ({total_stats['failed_batches']} raised "
            f"errors) for {total_stats['entries_classified']} entries ({document_uid}). "
            f"Refusing to emit all-fallback default codes; see batch errors above."
        )

    # Partial batch failures stay non-fatal, but must be visible per-run
    if total_stats["failed_batches"] > 0:
        msg = (
            f"{total_stats['failed_batches']} of {total_stats['llm_batches']} classification "
            f"batches failed; {total_stats['fallback_entries']} entries fell back to default codes"
        )
        print(f"⚠️ {msg}")
        logger.warning("Stage 3b (%s): %s", document_uid, msg)

    # T-validation gate: re-evaluate any T classifications
    t_count_before = sum(1 for e in all_classified if e.get("taxonomy_code") == "T")
    if t_count_before > 0:
        print()
        print(f"T-validation gate: reviewing {t_count_before} entries classified as T...")
        all_classified, t_validation_stats = validate_t_classifications(
            all_classified,
            taxonomy
        )
        t_count_after = sum(1 for e in all_classified if e.get("taxonomy_code") == "T")
        reclassified = t_validation_stats.get("t_entries_reclassified", 0)

        if reclassified > 0:
            print(f"  ✓ Reclassified {reclassified} entries from T to more specific codes")
            print(f"  T entries: {t_count_before} → {t_count_after}")
        else:
            print(f"  ✓ All {t_count_before} T classifications confirmed as correct")

        # Update stats
        total_stats["t_validation"] = t_validation_stats
        total_stats["cost"] += t_validation_stats.get("cost", 0.0)
        total_stats["input_tokens"] += t_validation_stats.get("input_tokens", 0)
        total_stats["output_tokens"] += t_validation_stats.get("output_tokens", 0)
        total_stats["total_tokens"] += t_validation_stats.get("input_tokens", 0) + t_validation_stats.get("output_tokens", 0)

    # Fragment reconnection: link orphaned T entries to adjacent entries
    t_count_remaining = sum(1 for e in all_classified if e.get("taxonomy_code") == "T")
    if t_count_remaining > 0:
        print()
        print(f"Fragment reconnection: checking {t_count_remaining} remaining T entries...")
        all_classified, fragment_stats = reconnect_fragments(
            all_classified
        )
        fragments_reviewed = fragment_stats.get("fragments_reviewed", 0)
        fragments_reconnected = fragment_stats.get("fragments_reconnected", 0)

        if fragments_reviewed > 0:
            if fragments_reconnected > 0:
                print(f"  ✓ Reconnected {fragments_reconnected}/{fragments_reviewed} fragments to adjacent entries")
            else:
                print(f"  ✓ Reviewed {fragments_reviewed} potential fragments, none reconnected")

            # Update stats
            total_stats["fragment_reconnection"] = fragment_stats
            total_stats["cost"] += fragment_stats.get("cost", 0.0)
            total_stats["input_tokens"] += fragment_stats.get("input_tokens", 0)
            total_stats["output_tokens"] += fragment_stats.get("output_tokens", 0)
            total_stats["total_tokens"] += fragment_stats.get("input_tokens", 0) + fragment_stats.get("output_tokens", 0)
        else:
            print(f"  ✓ No fragment candidates found")

    # Detect and flag duplicates
    print()
    print("Detecting duplicates...")
    all_classified, duplicate_pairs = detect_duplicates(all_classified)
    duplicate_count = sum(1 for e in all_classified if e.get("is_duplicate"))
    if duplicate_count > 0:
        print(f"  ⚠️ Found {duplicate_count} duplicate entries (flagged, not removed)")
        for dp in duplicate_pairs[:5]:  # Show first 5
            print(f"    - {dp['text_preview'][:50]}... ({dp['entry1_code']} vs {dp['entry2_code']})")
        if len(duplicate_pairs) > 5:
            print(f"    ... and {len(duplicate_pairs) - 5} more")
    else:
        print("  ✓ No duplicates detected")

    # ═══════════════════════════════════════════════════════════════════════════
    # POST-CLASSIFICATION AUTO-CORRECTION PASS
    # ═══════════════════════════════════════════════════════════════════════════
    # These deterministic validators catch and fix common LLM misclassifications
    # without additional API calls. They run in order of priority.
    print()
    print("=" * 60)
    print("POST-CLASSIFICATION AUTO-CORRECTIONS")
    print("=" * 60)

    post_correction_stats = {}

    # 1. Structural header corrections (CV title, page numbers, etc. → T)
    person_name = _person_name_from_uid(document_uid)

    print()
    print("1. Structural header corrections...")
    all_classified, structural_stats = apply_structural_corrections(all_classified, person_name)
    post_correction_stats['structural'] = structural_stats
    if structural_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {structural_stats['corrections_made']} structural elements to T")
        for detail in structural_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → T: {detail['text_preview'][:40]}...")
        if len(structural_stats['correction_details']) > 3:
            print(f"     ... and {len(structural_stats['correction_details']) - 3} more")
    else:
        print("   ✓ No structural header corrections needed")

    # 2. Committee vs position corrections (committee service → P/Q2, not D codes)
    print()
    print("2. Committee vs position corrections...")
    all_classified, committee_stats = apply_committee_corrections(all_classified)
    post_correction_stats['committee'] = committee_stats
    if committee_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {committee_stats['corrections_made']} committee entries")
        print(f"     - To P (internal service): {committee_stats['corrected_to_P']}")
        print(f"     - To Q2 (external service): {committee_stats['corrected_to_Q2']}")
        for detail in committee_stats['correction_details'][:2]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['text_preview'][:50]}...")
    else:
        print("   ✓ No committee/position corrections needed")

    # 3. Reasoning-code consistency corrections (when LLM reasoning disagrees with code)
    print()
    print("3. Reasoning-code consistency corrections...")
    all_classified, reasoning_stats = apply_reasoning_corrections(all_classified, min_confidence=0.80)
    post_correction_stats['reasoning'] = reasoning_stats
    if reasoning_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {reasoning_stats['corrections_made']} reasoning/code conflicts")
        for detail in reasoning_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['evidence'][:60]}...")
    elif reasoning_stats['conflicts_found'] > 0:
        print(f"   ⚠ Found {reasoning_stats['conflicts_found']} conflicts, {reasoning_stats['skipped_low_confidence']} skipped (low confidence)")
    else:
        print("   ✓ No reasoning/code conflicts detected")

    # 4. Grant-to-position corrections (catch position/leadership misclassified as M2A/M2B)
    print()
    print("4. Grant-to-position corrections (M2A/M2B → D2/O/L3)...")
    all_classified, grant_pos_stats = apply_grant_position_corrections(all_classified)
    post_correction_stats['grant_position'] = grant_pos_stats
    if grant_pos_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {grant_pos_stats['corrections_applied']} position entries misclassified as grants")
        for detail in grant_pos_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - {corr['from']} → {corr['to']}: {corr['position_match'][:40]}...")
    else:
        print("   ✓ No grant-to-position corrections needed")

    # 4b. (#946 item 5) M2 under an appointments heading with no funding evidence → T.
    #     BEFORE step 5, which rewrites only M2 codes and so cannot route it back.
    all_classified, appointment_funding_stats = apply_appointment_funding_corrections(all_classified)
    post_correction_stats['appointment_funding'] = appointment_funding_stats
    logger.info("Stage 3b: %d appointment row(s) without funding evidence M2 -> T",
                appointment_funding_stats['corrections_applied'])

    # 5. Grant status corrections (date-based M2A/M2B/M2C override)
    print()
    print("5. Grant status corrections (date-based)...")
    all_classified, grant_stats = apply_grant_status_corrections(all_classified)
    post_correction_stats['grant_status'] = grant_stats
    if grant_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {grant_stats['corrections_applied']} grant status codes")
        for detail in grant_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - {corr['from']} → {corr['to']}: {corr['reason'][:60]}...")
    else:
        print("   ✓ No grant status corrections needed")

    # 6. Teaching leadership corrections (K1 → K3 for Course Directors)
    print()
    print("6. Teaching leadership corrections (K1 → K3)...")
    all_classified, teaching_stats = apply_teaching_leadership_corrections(all_classified)
    post_correction_stats['teaching_leadership'] = teaching_stats
    if teaching_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {teaching_stats['corrections_applied']} teaching leadership codes")
        for detail in teaching_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - K1 → K3: {corr['reason'][:60]}...")
    else:
        print("   ✓ No teaching leadership corrections needed")

    # 7. Leadership level corrections (O → P for non-executive roles)
    print()
    print("7. Leadership level corrections (O → P)...")
    all_classified, leadership_stats = apply_leadership_level_corrections(all_classified)
    post_correction_stats['leadership_level'] = leadership_stats
    if leadership_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {leadership_stats['corrections_applied']} leadership level codes")
        for detail in leadership_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - O → P: {corr['reason'][:60]}...")
    else:
        print("   ✓ No leadership level corrections needed")

    # 8. Adjunct position corrections (D1 → D3 for non-faculty)
    print()
    print("8. Adjunct position corrections (D1 → D3)...")
    all_classified, adjunct_stats = apply_adjunct_position_corrections(all_classified)
    post_correction_stats['adjunct_position'] = adjunct_stats
    if adjunct_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {adjunct_stats['corrections_applied']} adjunct position codes")
        for detail in adjunct_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - D1 → D3: {corr['reason'][:60]}...")
    else:
        print("   ✓ No adjunct position corrections needed")

    # 8b. Cross-code position reconciliation (stray D1/D2/D3 title fragment →
    #     the subcode of the appointment group it is embedded in). Runs AFTER
    #     the per-entry position correctors (#4 grant→position, #8 adjunct) so
    #     it reconciles against already-stabilised D-codes. Deterministic; pairs
    #     with the Stage 6 grouped-appointment merge (#156) to rebuild the row.
    print()
    print("8b. Position subcode reconciliation (stray D1/D2/D3 fragment)...")
    all_classified, position_reconcile_stats = apply_position_subcode_reconciliation(all_classified)
    post_correction_stats['position_reconcile'] = position_reconcile_stats
    if position_reconcile_stats['corrections_applied'] > 0:
        print(f"   ✓ Reconciled {position_reconcile_stats['corrections_applied']} stray position fragment(s) to their appointment group")
        for detail in position_reconcile_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - {corr['from']} → {corr['to']}: {corr['reason'][:60]}...")
    else:
        print("   ✓ No stray position fragments to reconcile")

    # 9. Training/compliance corrections (P → B2 for trainings received)
    print()
    print("9. Training/compliance corrections (P → B2)...")
    all_classified, training_stats = apply_training_compliance_corrections(all_classified)
    post_correction_stats['training_compliance'] = training_stats
    if training_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {training_stats['corrections_applied']} training/compliance codes")
        for detail in training_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - P → B2: {corr['reason'][:60]}...")
    else:
        print("   ✓ No training/compliance corrections needed")

    # 9b. (#946 item 4) Extramural event medical volunteering P → T. After every
    #     corrector that can produce P (2: D → P, 7: O → P).
    all_classified, event_volunteer_stats = apply_event_volunteer_corrections(all_classified)
    post_correction_stats['event_volunteer'] = event_volunteer_stats
    logger.info("Stage 3b: %d event medical-volunteer row(s) P -> T",
                event_volunteer_stats['corrections_applied'])

    # 10. Invited talk corrections (S8 → R for invited conference talks)
    print()
    print("10. Invited talk corrections (S8 → R)...")
    all_classified, invited_stats = apply_invited_talk_corrections(all_classified)
    post_correction_stats['invited_talk'] = invited_stats
    if invited_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {invited_stats['corrections_applied']} invited talk codes")
        for detail in invited_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - S8 → R: {corr['reason'][:60]}...")
    else:
        print("   ✓ No invited talk corrections needed")

    # 10b. WCM structured-table corrections (mentee → N3A/N3B, board cert → F2, licensure → F1)
    print()
    print("10b. WCM structured-table corrections...")
    all_classified, wcm_table_stats = apply_wcm_table_corrections(all_classified)
    post_correction_stats['wcm_table'] = wcm_table_stats
    if wcm_table_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {wcm_table_stats['corrections_made']} WCM table codes")
        for detail in wcm_table_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['reason']}")
    else:
        print("   ✓ No WCM table corrections needed")

    # 10b-2. Prose named-mentee corrections (named individual mentee under an
    #        advising/mentoring section, misclassified as a K teaching code,
    #        -> N3A/N3B). Runs AFTER 10b so structured mentee rows are already
    #        N3A/N3B, and BEFORE 10c so a real named mentee is never demoted to T.
    print()
    print("10b-2. Prose named-mentee corrections...")
    all_classified, prose_mentee_stats = apply_prose_mentee_corrections(all_classified)
    post_correction_stats['prose_mentee'] = prose_mentee_stats
    if prose_mentee_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {prose_mentee_stats['corrections_made']} prose mentee codes")
        for detail in prose_mentee_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['reason']}")
    else:
        print("   ✓ No prose mentee corrections needed")

    # 10c. Template-scaffold suppression (filled-template instruction text → T)
    #      Runs AFTER 10b so pure template strings (e.g. the board-table header
    #      row) end as T rather than being promoted to a content code.
    print()
    print("10c. Template-scaffold corrections...")
    all_classified, scaffold_stats = apply_template_scaffold_corrections(all_classified)
    post_correction_stats['template_scaffold'] = scaffold_stats
    if scaffold_stats['corrections_made'] > 0:
        print(f"   ✓ Recoded {scaffold_stats['corrections_made']} template-scaffold entries to T")
    else:
        print("   ✓ No template-scaffold entries detected")

    # 10c. Block-coherence repair (#198): third-party records (mentees, lab staff,
    #      students) misrouted into the SUBJECT's own sections when a body-styled
    #      sub-header was flattened. Notices incoherent / orphaned-header people
    #      blocks and punts each to an LLM judge; applies only self-family -> N
    #      reattributions. Default OFF (one LLM call per flagged block) until the
    #      gold-set regression lands -- enable with CVICHE_BLOCK_COHERENCE_REPAIR=1.
    if os.getenv("CVICHE_BLOCK_COHERENCE_REPAIR", "0") == "1":
        print()
        print("10c. Block-coherence repair (subject vs third-party)...")

        def _block_coherence_llm(prompt: str) -> str:
            return call_llm(
                stage="stage_3b_block_coherence",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=1500,
            )["content"]

        all_classified, block_coherence_stats = apply_block_coherence_corrections(
            all_classified, llm=_block_coherence_llm, apply=True, subject_name=person_name
        )
        post_correction_stats['block_coherence'] = block_coherence_stats
        if block_coherence_stats['corrections_made'] > 0:
            print(f"   ✓ Reattributed {block_coherence_stats['corrections_made']} third-party rows to N")
            for f in block_coherence_stats['flagged'][:3]:
                v = f.get('verdict') or {}
                print(f"     - block n={f['n']} {f['families']} -> {v.get('verdict')} (conf {v.get('confidence')})")
        else:
            print("   ✓ No third-party misroutes corrected")

    # 11. Hierarchy-taxonomy mismatch flagging (QA review flags)
    print()
    print("11. Hierarchy-taxonomy mismatch flagging (QA)...")
    all_classified, mismatch_stats = flag_hierarchy_mismatches(all_classified)
    post_correction_stats['hierarchy_mismatches'] = mismatch_stats
    if mismatch_stats['entries_flagged'] > 0:
        print(f"   ⚠ Flagged {mismatch_stats['entries_flagged']} entries for QA review")
        for detail in mismatch_stats['flagged_details'][:3]:
            print(f"     - {detail['assigned_code']} under '{' > '.join(detail['hierarchy'][:2])}': {detail['text_preview'][:40]}...")
        if mismatch_stats['entries_flagged'] > 3:
            print(f"     ... and {mismatch_stats['entries_flagged'] - 3} more")
    else:
        print("   ✓ No hierarchy-taxonomy mismatches detected")

    # Summary
    total_post_corrections = (
        structural_stats['corrections_made'] +
        committee_stats['corrections_made'] +
        reasoning_stats['corrections_made'] +
        grant_stats['corrections_applied'] +
        appointment_funding_stats['corrections_applied'] +
        event_volunteer_stats['corrections_applied'] +
        teaching_stats['corrections_applied'] +
        leadership_stats['corrections_applied'] +
        adjunct_stats['corrections_applied'] +
        position_reconcile_stats['corrections_applied'] +
        training_stats['corrections_applied'] +
        invited_stats['corrections_applied']
    )
    print()
    print(f"Post-correction summary: {total_post_corrections} total corrections applied")
    print("=" * 60)

    # Add to total stats
    total_stats['post_classification_corrections'] = post_correction_stats
    total_stats['total_post_corrections'] = total_post_corrections

    # Prepare output
    if output_dir is None:
        output_dir = base_dir / "stage_3b_classified_entries"
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document_uid}_classified.json"

    # Build output document
    output_doc = {
        "document_uid": document_uid,
        "stage": "3b",
        "stage_name": "Entry Classification",
        "source_files": {
            "stage_2": str(stage_2_path),
            "stage_3a": str(stage_3a_path)
        },
        "entries": all_classified,
        "meta": {
            # No `or model` fallback (#644, param removed); total_stats["model"] is the OBSERVED model, None when no calls were made (#459).
            "model": total_stats.get("model"),
            "taxonomy_version": taxonomy["meta"]["version"],
            "total_entries": len(all_classified),
            "duplicate_entries": duplicate_count,
            "unique_entries": len(all_classified) - duplicate_count,
            "hierarchy_groups": len(groups),
            "stats": total_stats,
            # LLM-vs-fallback provenance for the initial classification pass
            # (#61); monitoring reads fallback_rate / had_classification_errors
            "classification_stats": {
                "total_entries": total_stats["entries_classified"],
                "llm_classified": total_stats["llm_classified"],
                "fallback_entries": total_stats["fallback_entries"],
                "empty_entries": total_stats["empty_entries"],
                "llm_batches": total_stats["llm_batches"],
                "failed_batches": total_stats["failed_batches"],
                "fallback_rate": round(
                    total_stats["fallback_entries"] / total_stats["entries_classified"], 4
                ) if total_stats["entries_classified"] else 0.0,
                "had_classification_errors": total_stats["failed_batches"] > 0
            },
            "post_correction_summary": {
                "total_corrections": total_post_corrections,
                "structural_corrections": structural_stats['corrections_made'],
                "committee_corrections": committee_stats['corrections_made'],
                "reasoning_corrections": reasoning_stats['corrections_made'],
                "grant_status_corrections": grant_stats['corrections_applied'],
                "teaching_leadership_corrections": teaching_stats['corrections_applied'],
                "leadership_level_corrections": leadership_stats['corrections_applied'],
                "adjunct_position_corrections": adjunct_stats['corrections_applied'],
                "training_compliance_corrections": training_stats['corrections_applied'],
                "invited_talk_corrections": invited_stats['corrections_applied']
            },
            "qa_flags": {
                "hierarchy_mismatches": mismatch_stats['entries_flagged'],
                "mismatch_summary": get_mismatch_summary(all_classified) if mismatch_stats['entries_flagged'] > 0 else None
            },
            "generated_at": datetime.now().isoformat()
        }
    }

    # Add duplicate pairs info if any found
    if duplicate_pairs:
        output_doc["meta"]["duplicate_pairs"] = duplicate_pairs

    # Compute code distribution
    code_counts = {}
    for entry in all_classified:
        code = entry.get("taxonomy_code", "?")
        code_counts[code] = code_counts.get(code, 0) + 1

    output_doc["meta"]["code_distribution"] = dict(sorted(code_counts.items()))

    # Write output
    with open(output_path, 'w') as f:
        json.dump(output_doc, f, indent=2)

    print()
    print(f"Output: {output_path}")
    print("=" * 80)

    # Print code distribution
    print()
    print("CODE DISTRIBUTION:")
    print("-" * 40)
    for code, count in sorted(code_counts.items(), key=lambda x: -x[1]):
        print(f"  {code}: {count}")

    return {
        "document_uid": document_uid,
        "output_path": str(output_path),
        "total_entries": len(all_classified),
        "stats": total_stats,
        "code_distribution": code_counts
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stage_3b_entry_classifier.py <document_uid>")
        print("  document_uid: Document identifier (e.g., 2086_Jones_Webb)")
        print()
        print("Prerequisites:")
        print("  - Stage 2 output: outputs/stage_2_entry_extraction/{uid}_entries.json")
        print("  - Stage 3a output: outputs/stage_3a_header_mappings/{uid}_header_taxonomy.json")
        sys.exit(1)

    document_uid = sys.argv[1]
    # No optional [model] CLI arg any more: run_stage_3b's `model` parameter
    # was removed, not forwarded (#644 review) -- it never reached
    # call_llm(); stage_3b's model is pinned in llm_config.yaml.
    result = run_stage_3b(document_uid)
