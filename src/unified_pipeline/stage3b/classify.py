"""Stage 3b LLM classification passes (#522).

Moved verbatim from `stage_3b_entry_classifier.py`: the batch classification
loop (`classify_entries_batch` with its #604/#605 helpers `_BatchStats`,
`_build_taxonomy_ref_for_batch`, `_classify_one_batch`, and
`group_entries_by_hierarchy`) and the post-classification passes that follow
it in `run_stage_3b` (`validate_t_classifications`, `reconnect_fragments`,
`detect_duplicates`).

The LLM-calling post-passes move here WITH the batch loop, not just the loop
itself, so that every `call_llm` call site of the stage lives in one module
and a test that stubs the LLM patches exactly one attribute,
`stage3b.classify.call_llm` (#496). Do not import `call_llm` into another
stage3b module.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..llm_client import call_llm
from .context import TaxonomyContext
from .io import _safe_float
from .prompt import _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE, build_taxonomy_codes_for_prompt

logger = logging.getLogger(__name__)


@dataclass
class _BatchStats:
    """One batch's contribution to classify_entries_batch's accumulators.

    _classify_one_batch returns one of these instead of mutating outer
    accumulator variables; classify_entries_batch sums them across batches.
    Field names mirror the loop-local variables the inline code used to
    increment (#604).
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    llm_batches: int = 0  # 1 if this batch attempted an LLM call, else 0
    observed_model: Optional[str] = None  # model id the API actually served (#459)
    failed_batches: int = 0  # 1 if this batch's LLM call raised, else 0


def _build_taxonomy_ref_for_batch(
    taxonomy_context: TaxonomyContext,
    taxonomy: Dict,
) -> Tuple[List[str], str]:
    """Resolve the suggested codes and taxonomy reference text shared by every
    batch in one classify_entries_batch call.

    Returns:
        Tuple of (all_suggested_codes, taxonomy_ref)
    """
    # Get ALL suggested codes from ALL hierarchy levels for taxonomy filtering
    # This ensures we don't miss codes suggested at parent levels
    all_suggested_codes = taxonomy_context.get_all_suggested_codes()
    relevant_families = set(c[0] for c in all_suggested_codes) if all_suggested_codes else None

    # Build taxonomy reference with disambiguation info for all suggested codes
    if relevant_families and len(relevant_families) <= 5:
        # Include suggested families plus a few common alternatives
        relevant_families.update(['H', 'T'])  # Always include honors and other
        # Pass ALL suggested codes so they get full disambiguation notes
        taxonomy_ref = build_taxonomy_codes_for_prompt(
            taxonomy,
            relevant_families=list(relevant_families),
            context_codes=all_suggested_codes
        )
    else:
        # Full taxonomy, but still include disambiguation for suggested codes
        taxonomy_ref = build_taxonomy_codes_for_prompt(
            taxonomy,
            context_codes=all_suggested_codes
        )

    return all_suggested_codes, taxonomy_ref


def _classify_one_batch(
    batch_entries: List[Dict],
    batch_start: int,
    taxonomy_context: TaxonomyContext,
    all_suggested_codes: List[str],
    taxonomy_ref: str,
    model: str,
) -> Tuple[List[Dict], _BatchStats]:
    """Classify a single batch against a taxonomy_ref built once by the caller.

    Returns this batch's own results list and its own stats contribution --
    it never appends to a shared list or mutates an outer accumulator, so
    classify_entries_batch can extend/sum the return values after the call.
    """
    stats = _BatchStats()

    # Skip empty entries
    entries_with_text = [(i, e) for i, e in enumerate(batch_entries) if e.get("text", "").strip()]

    if not entries_with_text:
        # All empty - assign parent code with low confidence
        results = []
        for entry in batch_entries:
            primary = all_suggested_codes[0] if all_suggested_codes else "T"
            results.append({
                **entry,
                "taxonomy_code": primary,
                "taxonomy_confidence": 0.0,
                "classification_source": "empty_entry"
            })
        return results, stats

    # Build prompt
    context_str = taxonomy_context.format_context_string()

    system_prompt = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str=context_str, taxonomy_ref=taxonomy_ref
    )

    # Build entries list for user message (include per-entry hierarchy)
    entries_lines = []
    for i, e in entries_with_text:
        hierarchy_path = " > ".join(e.get("hierarchy", [])) or "unknown"
        entries_lines.append(f"[{i}] (Section: {hierarchy_path}) {e['text'][:500]}")
    entries_text = "\n".join(entries_lines)

    user_message = f"""Classify these {len(entries_with_text)} entries:

{entries_text}

Return ONLY valid JSON with the classifications array."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    # Call LLM
    stats.llm_batches = 1
    try:
        llm_result = call_llm(
            stage="stage_3b",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=2000
        )

        # Parse response
        content = llm_result["content"]
        result = json.loads(content)
        classifications = result.get("classifications", [])

        # Track tokens/cost
        stats.input_tokens = llm_result["prompt_tokens"]
        stats.output_tokens = llm_result["completion_tokens"]
        stats.cost = llm_result["cost"]
        stats.observed_model = llm_result.get("model")

    except Exception:
        # Every entry in this batch falls back to the default code below;
        # the caller aggregates failed_batches and fails the run if NO
        # batch ever produced a real classification (#61).
        stats.failed_batches = 1
        hierarchy_path = " > ".join(batch_entries[0].get("hierarchy", [])) or "(no hierarchy)"
        logger.exception(
            "Stage 3b batch classification failed; %d entries fall back to "
            "default codes (batch at offset %d, hierarchy: %s)",
            len(entries_with_text), batch_start, hierarchy_path
        )
        classifications = []

    # Build index lookup for classifications.
    #
    # This runs OUTSIDE the try/except above, so anything raised here
    # escapes classify_entries_batch and run_stage_3b entirely: the
    # orchestrator fails the whole web run, while the CLI prints
    # "Warning: Stage N failed" and lets every later stage run on
    # unclassified entries. The response is requested as a bare
    # json_object with no schema, so an object without "index" (KeyError)
    # or a non-dict element (TypeError) is a real possibility. Skip those
    # loudly instead -- they fall back to the default code below, which is
    # what a missing classification already does (#521).
    class_by_idx = {}
    malformed = 0
    for c in classifications:
        if isinstance(c, dict) and "index" in c:
            class_by_idx[c["index"]] = c
        else:
            malformed += 1
    if malformed:
        logger.warning(
            "Stage 3b: skipped %d malformed classification object(s) in the "
            "batch at offset %d; those entries fall back to the default code",
            malformed, batch_start
        )

    # Map results back to entries
    results = []
    for orig_idx, entry in enumerate(batch_entries):
        if not entry.get("text", "").strip():
            # Empty entry
            primary = all_suggested_codes[0] if all_suggested_codes else "T"
            results.append({
                **entry,
                "taxonomy_code": primary,
                "taxonomy_confidence": 0.0,
                "classification_source": "empty_entry"
            })
        else:
            # class_by_idx is keyed by the ORIGINAL index within
            # batch_entries: the prompt labels each entry "[{i}]" using the
            # i carried in entries_with_text, which came from
            # enumerate(batch_entries), and the model echoes those labels
            # back as "index". Looking up a POSITION within entries_with_text
            # instead only agrees when nothing was filtered out -- and stage 2
            # emits empty "break" entries throughout the list on purpose
            # (filter_extraction_noise keeps them; "breaks are legitimately
            # empty"). One break in a batch shifted every later entry, so an
            # entry was persisted with its neighbour's code and confidence,
            # indistinguishable downstream from a correct classification (#520).
            c = class_by_idx.get(orig_idx)
            if c is not None:
                results.append({
                    **entry,
                    # Coalesce an explicit-null/empty LLM code to the fallback,
                    # and coerce a stringified confidence to float, so the
                    # persisted values never crash downstream .startswith / < 0.7.
                    "taxonomy_code": c.get("code") or (all_suggested_codes[0] if all_suggested_codes else "T"),
                    "taxonomy_confidence": _safe_float(c.get("confidence"), 0.5),
                    "classification_reasoning": c.get("reasoning"),
                    "classification_source": "llm"
                })
            else:
                # Fallback to primary code
                primary = all_suggested_codes[0] if all_suggested_codes else "T"
                results.append({
                    **entry,
                    "taxonomy_code": primary,
                    "taxonomy_confidence": 0.5,
                    "classification_source": "fallback"
                })

    return results, stats


def classify_entries_batch(
    entries: List[Dict],
    taxonomy_context: TaxonomyContext,
    taxonomy: Dict,
    model: str = "gpt-5.1",
    batch_size: int = 15
) -> Tuple[List[Dict], Dict]:
    """
    Classify a batch of entries with the same taxonomy context.

    Args:
        entries: List of entry dicts with 'text' field
        taxonomy_context: Shared taxonomy context for these entries
        taxonomy: Full taxonomy reference
        model: OpenAI model to use
        batch_size: Max entries per LLM call

    Returns:
        Tuple of (classified_entries, stats)
    """
    all_results = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    llm_batches = 0  # batches that attempted an LLM call
    observed_model = None  # model id the API actually served (#459)
    failed_batches = 0  # batches whose LLM call raised (entries fell back)

    all_suggested_codes, taxonomy_ref = _build_taxonomy_ref_for_batch(taxonomy_context, taxonomy)

    # Process in batches
    for batch_start in range(0, len(entries), batch_size):
        batch_entries = entries[batch_start:batch_start + batch_size]

        batch_results, batch_stats = _classify_one_batch(
            batch_entries, batch_start, taxonomy_context, all_suggested_codes, taxonomy_ref, model
        )
        all_results.extend(batch_results)
        total_input_tokens += batch_stats.input_tokens
        total_output_tokens += batch_stats.output_tokens
        total_cost += batch_stats.cost
        llm_batches += batch_stats.llm_batches
        failed_batches += batch_stats.failed_batches
        observed_model = batch_stats.observed_model or observed_model

    stats = {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "cost": total_cost,
        # What actually served the calls. The `model` parameter is a default no
        # orchestrator passes, so recording it stamped 100/100 corpus artifacts
        # with a model the run never used -- and 3b is the one deliberately on
        # Haiku, which is exactly the comparison the field exists for (#459).
        "model": observed_model,
        "entries_classified": len(all_results),
        "llm_batches": llm_batches,
        "failed_batches": failed_batches,
        "llm_classified": sum(1 for r in all_results if r.get("classification_source") == "llm"),
        "fallback_entries": sum(1 for r in all_results if r.get("classification_source") == "fallback"),
        "empty_entries": sum(1 for r in all_results if r.get("classification_source") == "empty_entry"),
    }

    return all_results, stats


def group_entries_by_hierarchy(entries: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group entries by their hierarchy path (for batching).

    Returns dict mapping hierarchy_key -> list of entries
    """
    groups = {}

    for entry in entries:
        hierarchy = entry.get("hierarchy", [])
        key = " > ".join(hierarchy) if hierarchy else "(no hierarchy)"

        if key not in groups:
            groups[key] = []
        groups[key].append(entry)

    return groups


def validate_t_classifications(
    entries: List[Dict],
    taxonomy: Dict,
    model: str = "gpt-5.1"
) -> Tuple[List[Dict], Dict]:
    """
    T-validation gate: Re-evaluate any entries classified as T (miscellaneous).

    T should be used in <2% of cases. This gate takes all T classifications
    and asks the LLM to reconsider with the FULL taxonomy and strong guidance
    that T is an absolute last resort.

    Args:
        entries: List of classified entries (some may have taxonomy_code="T")
        taxonomy: Full taxonomy reference
        model: OpenAI model to use

    Returns:
        Tuple of (updated_entries, stats) where T entries may be reclassified
    """
    # Find entries classified as exactly "T" (miscellaneous)
    # NOTE: We only review "T", not T-family codes like T1 (Community Engagement)
    # which are valid specific classifications
    t_entries = [(i, e) for i, e in enumerate(entries) if e.get("taxonomy_code") == "T"]

    if not t_entries:
        return entries, {"t_entries_reviewed": 0, "t_entries_reclassified": 0, "cost": 0.0}

    # Build FULL taxonomy reference for maximum context
    taxonomy_ref = build_taxonomy_codes_for_prompt(taxonomy)

    # Build the validation prompt
    system_prompt = f"""You are an expert CV classifier performing a CRITICAL REVIEW of entries that were initially classified as "T" (Miscellaneous/Other).

IMPORTANT CONTEXT:
- T (Miscellaneous/Other) should be used in LESS THAN 2% of CV entries
- T is an ABSOLUTE LAST RESORT when NO other code applies
- Most entries initially classified as T are actually misclassified and belong elsewhere

YOUR TASK:
For each entry below, determine if T is truly correct, or if a more specific code applies.

FULL TAXONOMY (use this to find a better code):
{taxonomy_ref}

═══════════════════════════════════════════════════════════════════════════════
COMMON MISCLASSIFICATIONS TO T (check these first!):
═══════════════════════════════════════════════════════════════════════════════

1. COMMUNITY SERVICE / OUTREACH → Q2 (not T)
   - Advisory boards, committees, task forces → Q2
   - Expert testimony, consulting → Q2
   - Community advisory participation → Q2
   - Public health outreach → Q2 or K5
   - Pro bono professional service → Q2

2. INVITED PRESENTATIONS → R (not T)
   - Grand rounds, colloquia, seminars → R
   - Keynote lectures, named lectures → R
   - Departmental or institutional talks → R
   - "Invited" anything at an academic venue → R

3. PROFESSIONAL SERVICE → P or Q2 (not T)
   - Internal committees → P
   - External committees/boards → Q2
   - Review panels → Q3 (grants) or Q4D (manuscripts)

4. GRANTS/FUNDING → M2A/M2B/M2C (not T)
   - Any entry with grant numbers, dollar amounts, PI roles → M2

5. EDITORIAL WORK → Q4A/Q4B/Q4C/Q4D (not T)
   - Editor, associate editor → Q4A/Q4B
   - Editorial board → Q4C
   - Peer review → Q4D

6. RESEARCH ACTIVITIES → M1 (not T)
   - Research interests, themes, areas → M1
   - Fieldwork, excavations → M1
   - Lab descriptions → M1

7. POSITIONS/APPOINTMENTS → D1/D2/D3/O (not T)
   - Academic titles → D1
   - Hospital appointments → D2
   - Staff positions → D3
   - Leadership/administrative roles → O

8. PROFESSIONAL MEMBERSHIPS → I (not T)
   - Society memberships → I
   - Professional organization membership → I

9. EDUCATIONAL OUTREACH → K5 (not T)
   - Public lectures, science communication → K5
   - Media appearances about science → K5

10. HONORS/AWARDS → H (not T)
    - Recognition, prizes, competitive awards → H

DECISION CRITERIA:
- If the entry fits ANY of the above patterns → use that code, NOT T
- If the entry contains keywords like "committee", "board", "advisory", "invited", "lecture", "presentation", "grant", "review" → almost certainly NOT T
- T should ONLY be used for genuinely miscellaneous items like reference lists, appendix materials, or items that truly cannot fit anywhere else

For each entry, respond with:
{{"entry_index": N, "new_code": "XX", "confidence": 0.XX, "reasoning": "brief explanation"}}

If T is genuinely correct, keep it: {{"entry_index": N, "new_code": "T", "confidence": 0.XX, "reasoning": "why no other code fits"}}
"""

    # Format entries for review
    entries_text = []
    for idx, entry in t_entries:
        text = entry.get("text", "")[:500]  # Truncate long entries
        hierarchy = " > ".join(entry.get("hierarchy", [])) or "(no hierarchy)"
        original_reasoning = entry.get("classification_reasoning", "none provided")
        entries_text.append(f"""
Entry {idx}:
  Hierarchy: {hierarchy}
  Text: {text}
  Original reasoning for T: {original_reasoning}
""")

    user_prompt = f"""Review these {len(t_entries)} entries that were classified as T (Miscellaneous).
For each one, determine if T is correct or if a more specific code should be used.

{chr(10).join(entries_text)}

Respond with a JSON array of objects, one per entry:
[
  {{"entry_index": N, "new_code": "XX", "confidence": 0.XX, "reasoning": "..."}},
  ...
]
"""

    # Log prompt
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        llm_result = call_llm(
            stage="stage_3b",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1
        )

        # Parse response
        content = llm_result["content"]

        # Handle both array and object responses
        result = json.loads(content)
        if isinstance(result, dict):
            # If wrapped in an object, try to find the array
            if "results" in result:
                reclassifications = result["results"]
            elif "entries" in result:
                reclassifications = result["entries"]
            elif "classifications" in result:
                reclassifications = result["classifications"]
            else:
                # Assume the dict values are the results
                reclassifications = list(result.values())[0] if result else []
        else:
            reclassifications = result

        # Apply reclassifications
        reclassified_count = 0
        for reclass in reclassifications:
            entry_idx = reclass.get("entry_index")
            new_code = reclass.get("new_code") or "T"
            confidence = _safe_float(reclass.get("confidence"), 0.5)
            reasoning = reclass.get("reasoning", "")

            if entry_idx is not None and 0 <= entry_idx < len(entries):
                old_code = entries[entry_idx].get("taxonomy_code")
                if old_code == "T" and new_code != "T":
                    entries[entry_idx]["taxonomy_code"] = new_code
                    entries[entry_idx]["taxonomy_confidence"] = confidence
                    entries[entry_idx]["classification_reasoning"] = f"[T-validation reclassified from T] {reasoning}"
                    entries[entry_idx]["t_validation_applied"] = True
                    reclassified_count += 1
                elif old_code == "T" and new_code == "T":
                    # T was confirmed - add note
                    entries[entry_idx]["classification_reasoning"] = f"[T-validation confirmed] {reasoning}"
                    entries[entry_idx]["t_validation_applied"] = True

        # Calculate cost (call_llm already returns token counts and priced cost)
        input_tokens = llm_result["prompt_tokens"]
        output_tokens = llm_result["completion_tokens"]
        cost = llm_result["cost"]

        stats = {
            "t_entries_reviewed": len(t_entries),
            "t_entries_reclassified": reclassified_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost
        }

        return entries, stats

    except Exception as e:
        print(f"    ⚠ T-validation error: {e}")
        # Report reclassifications already applied to entries before the error,
        # so meta.stats reflects reality even on a partial failure.
        return entries, {"t_entries_reviewed": len(t_entries), "t_entries_reclassified": locals().get("reclassified_count", 0), "cost": 0.0, "error": str(e)}


def reconnect_fragments(
    entries: List[Dict],
    model: str = "gpt-5.1"
) -> Tuple[List[Dict], Dict]:
    """
    Reconnect fragment entries (classified as T) to their adjacent entries.

    Some entries are fragments of larger entries that got split during extraction:
    - Orphaned location lines: "University, Columbus, OH"
    - Orphaned budget lines: "$1,326,480, Ohio Department of Medicaid"
    - Continuation lines without context

    This function identifies likely fragments and determines if they belong
    with the previous or next entry.

    Args:
        entries: List of classified entries (sorted by element_idx_start)
        model: OpenAI model to use

    Returns:
        Tuple of (updated_entries, stats) where fragments are annotated
    """
    # Find T entries that look like fragments (short, low confidence)
    fragment_candidates = []
    for i, entry in enumerate(entries):
        if entry.get("taxonomy_code") != "T":
            continue

        text = entry.get("text", "").strip()
        confidence = entry.get("taxonomy_confidence", 1.0)

        # Fragment signals:
        # - Very short text (< 100 chars)
        # - Low confidence (< 0.7)
        # - Contains only: location, dollar amount, institution name, or partial info
        is_short = len(text) < 100
        is_low_conf = _safe_float(confidence, 1.0) < 0.7

        # Check for fragment patterns
        import re
        is_location_only = bool(re.match(r'^[A-Z][a-z]+,?\s+[A-Z]{2}$', text))  # "Columbus, OH"
        is_dollar_only = bool(re.match(r'^\$[\d,]+', text)) and 'PI' not in text and 'Co-I' not in text
        is_institution_fragment = (
            len(text.split()) <= 5 and
            any(kw in text for kw in ['University', 'College', 'Institute', 'Center', 'Hospital']) and
            not any(kw in text for kw in ['Professor', 'Director', 'Chair', 'Fellow'])
        )

        if is_short and (is_low_conf or is_location_only or is_dollar_only or is_institution_fragment):
            # Need adjacent entries to compare
            prev_entry = entries[i - 1] if i > 0 else None
            next_entry = entries[i + 1] if i < len(entries) - 1 else None

            if prev_entry or next_entry:
                fragment_candidates.append({
                    "index": i,
                    "entry": entry,
                    "prev_entry": prev_entry,
                    "next_entry": next_entry
                })

    if not fragment_candidates:
        return entries, {"fragments_reviewed": 0, "fragments_reconnected": 0, "cost": 0.0}

    # Build prompt for fragment analysis
    system_prompt = """You are analyzing CV entries to identify fragments that belong with adjacent entries.

Some CV entries get incorrectly split during extraction, creating orphaned fragments like:
- Location-only lines: "University, Columbus, OH"
- Budget-only lines: "$1,326,480, Ohio Department of Medicaid"
- Partial institution names without roles

For each fragment, determine if it belongs with the PREVIOUS entry, NEXT entry, or is STANDALONE.

DECISION CRITERIA:
1. If the fragment completes information from the previous entry (e.g., location for a position, budget for a grant) → PREVIOUS
2. If the fragment introduces the next entry (e.g., header-like content) → NEXT
3. If the fragment is genuinely standalone or unclear → STANDALONE

Respond with JSON:
{
  "fragments": [
    {"index": N, "belongs_to": "previous|next|standalone", "reasoning": "brief explanation"}
  ]
}
"""

    # Format fragments for review
    fragments_text = []
    for fc in fragment_candidates:
        idx = fc["index"]
        entry = fc["entry"]
        prev_entry = fc["prev_entry"]
        next_entry = fc["next_entry"]

        prev_text = prev_entry.get("text", "")[:200] if prev_entry else "(none)"
        prev_code = prev_entry.get("taxonomy_code", "?") if prev_entry else "?"
        next_text = next_entry.get("text", "")[:200] if next_entry else "(none)"
        next_code = next_entry.get("taxonomy_code", "?") if next_entry else "?"
        frag_text = entry.get("text", "")

        fragments_text.append(f"""
Fragment at index {idx}:
  Text: "{frag_text}"

  Previous entry [{prev_code}]: "{prev_text}"
  Next entry [{next_code}]: "{next_text}"
""")

    user_prompt = f"""Analyze these {len(fragment_candidates)} fragments and determine where they belong:

{chr(10).join(fragments_text)}
"""

    # Log prompt
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        llm_result = call_llm(
            stage="stage_3b",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1
        )

        content = llm_result["content"]
        result = json.loads(content)
        fragment_decisions = result.get("fragments", [])

        # Apply reconnections
        reconnected_count = 0
        for decision in fragment_decisions:
            idx = decision.get("index")
            belongs_to = decision.get("belongs_to", "standalone")
            reasoning = decision.get("reasoning", "")

            if idx is not None and 0 <= idx < len(entries):
                entry = entries[idx]

                if belongs_to == "previous" and idx > 0:
                    prev_entry = entries[idx - 1]
                    entry["fragment_of"] = idx - 1
                    entry["fragment_reasoning"] = reasoning
                    entry["taxonomy_code"] = prev_entry.get("taxonomy_code", "T")
                    entry["taxonomy_confidence"] = 0.3  # Low confidence for fragments
                    entry["is_fragment"] = True
                    reconnected_count += 1

                elif belongs_to == "next" and idx < len(entries) - 1:
                    next_entry = entries[idx + 1]
                    entry["fragment_of"] = idx + 1
                    entry["fragment_reasoning"] = reasoning
                    entry["taxonomy_code"] = next_entry.get("taxonomy_code", "T")
                    entry["taxonomy_confidence"] = 0.3
                    entry["is_fragment"] = True
                    reconnected_count += 1

                elif belongs_to == "standalone":
                    entry["fragment_reasoning"] = f"[Confirmed standalone] {reasoning}"

        # Calculate cost (call_llm already returns token counts and priced cost)
        input_tokens = llm_result["prompt_tokens"]
        output_tokens = llm_result["completion_tokens"]
        cost = llm_result["cost"]

        stats = {
            "fragments_reviewed": len(fragment_candidates),
            "fragments_reconnected": reconnected_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost
        }

        return entries, stats

    except Exception as e:
        print(f"    ⚠ Fragment reconnection error: {e}")
        # Report reconnections already applied to entries before the error.
        return entries, {"fragments_reviewed": len(fragment_candidates), "fragments_reconnected": locals().get("reconnected_count", 0), "cost": 0.0, "error": str(e)}


def detect_duplicates(entries: List[Dict], similarity_threshold: float = 0.9) -> Tuple[List[Dict], List[Dict]]:
    """
    Detect and flag duplicate entries based on text similarity.

    Duplicates occur when the same content appears under multiple CV sections
    (e.g., grants listed under both "Other Publications" and "Grant Support").

    Args:
        entries: List of classified entries
        similarity_threshold: Minimum similarity ratio to consider duplicate (0-1)

    Returns:
        Tuple of (deduplicated_entries, duplicate_info)
        - deduplicated_entries: Entries with duplicates marked
        - duplicate_info: List of detected duplicate pairs
    """
    import re
    from difflib import SequenceMatcher

    def normalize_text(text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        # Lowercase, remove extra whitespace, strip punctuation
        text = text.lower()
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s]', '', text)
        return text.strip()

    def text_similarity(t1: str, t2: str) -> float:
        """Calculate similarity ratio between two texts."""
        n1, n2 = normalize_text(t1), normalize_text(t2)
        if not n1 or not n2:
            return 0.0
        # Use SequenceMatcher for fuzzy matching
        return SequenceMatcher(None, n1, n2).ratio()

    # Build text index for faster lookup
    text_index = {}  # normalized_text -> list of (idx, entry)
    for idx, entry in enumerate(entries):
        text = entry.get("text", "")
        if len(text) < 20:  # Skip very short entries
            continue
        norm = normalize_text(text)
        # Use first 100 chars as key for grouping similar entries
        key = norm[:100] if len(norm) > 100 else norm
        if key not in text_index:
            text_index[key] = []
        text_index[key].append((idx, entry, norm))

    # Find duplicates
    duplicate_pairs = []
    seen_duplicates = set()  # Track which indices have been marked as duplicates

    for key, items in text_index.items():
        if len(items) < 2:
            continue

        # Compare all pairs in this group
        for i, (idx1, entry1, norm1) in enumerate(items):
            for idx2, entry2, norm2 in items[i+1:]:
                if idx1 in seen_duplicates and idx2 in seen_duplicates:
                    continue

                sim = text_similarity(entry1.get("text", ""), entry2.get("text", ""))
                if sim >= similarity_threshold:
                    # These are duplicates
                    duplicate_pairs.append({
                        "entry1_idx": idx1,
                        "entry2_idx": idx2,
                        "similarity": sim,
                        "entry1_hierarchy": entry1.get("hierarchy", []),
                        "entry2_hierarchy": entry2.get("hierarchy", []),
                        "entry1_code": entry1.get("taxonomy_code"),
                        "entry2_code": entry2.get("taxonomy_code"),
                        "text_preview": entry1.get("text", "")[:100]
                    })

                    # Mark the second one as duplicate (keep the first)
                    # Prefer M2 classification over T
                    code1 = entry1.get("taxonomy_code") or ""
                    code2 = entry2.get("taxonomy_code") or ""

                    if code1.startswith("T") and code2.startswith("M"):
                        # Second one is better classified, mark first as duplicate
                        seen_duplicates.add(idx1)
                    elif code2.startswith("T") and code1.startswith("M"):
                        # First one is better classified, mark second as duplicate
                        seen_duplicates.add(idx2)
                    else:
                        # Default: mark second as duplicate
                        seen_duplicates.add(idx2)

    # Mark duplicates in entries
    for idx, entry in enumerate(entries):
        if idx in seen_duplicates:
            entry["is_duplicate"] = True
            entry["duplicate_note"] = "This entry appears elsewhere in the CV with the same content"

    return entries, duplicate_pairs
