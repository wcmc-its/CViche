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
from typing import TypedDict

from ..llm_client import call_llm
from .context import TaxonomyContext
from .io import _safe_float
from .prompt import (
    CLASSIFICATION_RULES_VERSION,
    _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE,
    _T_VALIDATION_SYSTEM_PROMPT_TEMPLATE,
    build_taxonomy_codes_for_prompt,
)

logger = logging.getLogger(__name__)

# Taxonomy code prefixes for duplicate-pair resolution (see docs/CODING_STANDARDS.md §8.2):
# an "M"-series classification (grants etc.) is preferred over the unclassified "T" (Appendix) fallback.
APPENDIX_TAXONOMY_PREFIX = "T"
M_SERIES_TAXONOMY_PREFIX = "M"

# Sentinel for "this entry carries no hierarchy path". A module constant --
# not a repeated string literal -- so the producer (group_entries_by_hierarchy
# below) and every consumer (stage_3b_entry_classifier.py's _classify_group,
# validate_t_classifications and _classify_one_batch's except path below)
# agree exactly: a typo in a literal copy on either side would silently
# produce a one-element hierarchy instead of failing at import time.
NO_HIERARCHY_KEY = "(no hierarchy)"


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
    observed_model: str | None = None  # model id the API actually served (#459)
    failed_batches: int = 0  # 1 if this batch's LLM call raised, else 0


class ClassificationStats(TypedDict):
    """The stats dict `classify_entries_batch` returns and `run_stage_3b`
    aggregates across groups -- named so a caller (and a type checker) knows
    the keys up front instead of discovering them via a runtime KeyError.
    """
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    model: str | None
    classification_rules_version: str
    entries_classified: int
    llm_batches: int
    failed_batches: int
    llm_classified: int
    fallback_entries: int
    empty_entries: int
    invalid_code_entries: int


def _valid_taxonomy_codes(taxonomy: dict) -> set[str]:
    """The set of codes `taxonomy` actually defines.

    Every classification below comes from an LLM call made with
    ``response_format={"type": "json_object"}`` and no schema -- the model
    can echo back a code that isn't in taxonomy_v7.json at all (a
    hallucination, a typo, a code from an older taxonomy version). That is
    untrusted input; a code outside this set must not be persisted as a
    real classification.
    """
    return {
        c["code"] for c in taxonomy.get("codes", [])
        if isinstance(c, dict) and isinstance(c.get("code"), str) and c["code"]
    }


def _normalize_confidence(value: object, default: float = 0.5) -> float:
    """Coerce an LLM-provided confidence to a float constrained to [0.0, 1.0].

    `_safe_float` only guarantees the value converts to *some* float (e.g. a
    stringified number); it does not guarantee that float is a valid
    probability. A value outside [0, 1] -- including NaN/inf, which compare
    false against any bound -- falls back to `default` instead of being
    persisted as a confidence, mirroring the clamp stage3b/io.py already
    applies to stage 3a's taxonomy_options (#558).
    """
    confidence = _safe_float(value, default)
    return confidence if 0.0 <= confidence <= 1.0 else default


def _entry_text(entry: dict) -> str:
    """Coerce an entry's "text" field to a string.

    `entry.get("text", "")` only substitutes when the key is ABSENT -- an
    entry with an explicit `"text": None` (or any other non-string value)
    survives that default and reaches a bare `.strip()` call, raising
    AttributeError. Two call sites in `_classify_one_batch` need this check
    and sit outside its try/except, so a malformed entry there must degrade
    (treated the same as empty text) instead of crashing the whole batch.
    """
    text = entry.get("text")
    return text if isinstance(text, str) else ""


def _hierarchy_path(hierarchy: object, default: str) -> str:
    """Render an entry's "hierarchy" field as a " > "-joined path.

    Mirrors the str()-coercion `group_entries_by_hierarchy` already applies:
    hierarchy is normally a list of strings, but a bare `" > ".join(...)`
    raises TypeError on a None/absent hierarchy or a non-string element. Two
    call sites in `_classify_one_batch` build this same path outside its
    try/except (one of them INSIDE the except handler, where raising would
    mask the original exception being handled), so malformed hierarchy there
    must degrade to `default` instead.
    """
    if not hierarchy:
        return default
    return " > ".join(str(h) for h in hierarchy) or default


def _build_taxonomy_ref_for_batch(
    taxonomy_context: TaxonomyContext,
    taxonomy: dict,
) -> tuple[list[str], str]:
    """Resolve the suggested codes and taxonomy reference text shared by every
    batch in one classify_entries_batch call.

    Returns:
        Tuple of (all_suggested_codes, taxonomy_ref)
    """
    # Get ALL suggested codes from ALL hierarchy levels for taxonomy filtering
    # This ensures we don't miss codes suggested at parent levels
    all_suggested_codes = taxonomy_context.get_all_suggested_codes()
    # Drop non-string/empty codes here (not just for c[0] below): this is also the
    # list _classify_one_batch indexes at [0] as an unvalidated fallback taxonomy_code.
    all_suggested_codes = [c for c in all_suggested_codes if isinstance(c, str) and c]
    relevant_families = set(c[0] for c in all_suggested_codes) if all_suggested_codes else None

    # Build taxonomy reference with disambiguation info for all suggested codes
    if relevant_families and len(relevant_families) <= 5:
        # Include suggested families plus a few common alternatives
        relevant_families.update(['H', 'T'])  # Always include honors and other
        # Pass ALL suggested codes so they get full disambiguation notes
        taxonomy_ref = build_taxonomy_codes_for_prompt(
            taxonomy,
            relevant_codes_or_families=list(relevant_families),
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
    batch_entries: list[dict],
    batch_start: int,
    taxonomy_context: TaxonomyContext,
    all_suggested_codes: list[str],
    taxonomy_ref: str,
    valid_codes: set[str],
) -> tuple[list[dict], _BatchStats]:
    """Classify a single batch against a taxonomy_ref built once by the caller.

    Returns this batch's own results list and its own stats contribution --
    it never appends to a shared list or mutates an outer accumulator, so
    classify_entries_batch can extend/sum the return values after the call.

    call_llm() below is called with no `model` argument at all: stage_3b's
    model is pinned per-stage in llm_config.yaml (Haiku 4.5, both more
    accurate and ~2.9x cheaper than gpt-5.1 -- #459). Every function here
    used to take a `model` parameter that was never forwarded; it was
    removed rather than wired up, since an argument that cannot affect
    behaviour is worse than none (#644 review). `observed_model`/
    stats["model"] is what actually served the call -- trust that for audit.
    """
    stats = _BatchStats()

    # Skip empty entries; _entry_text degrades malformed "text" (outside try/except below).
    entries_with_text = [(i, e) for i, e in enumerate(batch_entries) if _entry_text(e).strip()]

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
        # _hierarchy_path degrades malformed hierarchy too (also outside try/except).
        hierarchy_path = _hierarchy_path(e.get("hierarchy"), "unknown")
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
        # `.get(..., [])` only substitutes when the key is absent -- an
        # explicit `"classifications": null` (or any non-list value) survives
        # that default and reaches the "for c in classifications" loop below,
        # which is deliberately outside this try/except (see comment there)
        # so it can't be caught as a batch failure. Coerce here so a
        # malformed container degrades the same way a malformed element
        # already does: a loud skip, not an uncaught TypeError that crashes
        # the whole run.
        if not isinstance(classifications, list):
            logger.warning(
                "Stage 3b: response's \"classifications\" was %s, not a list "
                "(batch at offset %d); treating as empty -- every entry in "
                "this batch falls back to the default code",
                type(classifications).__name__, batch_start
            )
            classifications = []

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
        # _hierarchy_path INSIDE the except handler: raising here would mask
        # the original exception, not just fail to log it.
        hierarchy_path = _hierarchy_path(batch_entries[0].get("hierarchy"), NO_HIERARCHY_KEY)
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
        # _entry_text again: re-walks ALL of batch_entries, so a malformed
        # "text" would crash this unguarded .strip() too.
        if not _entry_text(entry).strip():
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
                fallback_code = all_suggested_codes[0] if all_suggested_codes else "T"
                code = c.get("code")
                # isinstance-guard before the set membership check: `code`
                # is untrusted LLM output and could be any JSON type (e.g. a
                # list), which would raise TypeError: unhashable type on
                # `in valid_codes` instead of degrading to the fallback.
                # No `code and`: an omitted key is falsy, skipped this check,
                # and persisted "llm" as the source. Mirrors `new_code_rejected`.
                code_rejected = not isinstance(code, str) or code not in valid_codes
                if code_rejected:
                    # The LLM answered with a code that doesn't exist in
                    # taxonomy_v7.json (or isn't a string at all) --
                    # untrusted model output, not trustworthy enough to
                    # persist as a real classification.
                    logger.warning(
                        "Stage 3b: LLM returned unknown taxonomy code %r for "
                        "entry %d (batch at offset %d); falling back to the "
                        "default code", code, orig_idx, batch_start
                    )
                    code = None
                results.append({
                    **entry,
                    # Coalesce an explicit-null/empty/unknown LLM code to the
                    # fallback, and clamp confidence to [0, 1], so the
                    # persisted values never crash downstream .startswith /
                    # < 0.7 and never store a hallucinated code as if it were
                    # a real classification.
                    "taxonomy_code": code or fallback_code,
                    "taxonomy_confidence": _normalize_confidence(c.get("confidence"), 0.5),
                    "classification_reasoning": c.get("reasoning"),
                    # A rejected code is a degraded result, not a clean LLM
                    # classification -- tagging it "llm" would make this
                    # fallback indistinguishable from success in the artifact
                    # (CODING_STANDARDS.md #5.3/#5.10: "accept anyway" isn't
                    # a silent option; the degradation has to be visible
                    # somewhere other than a log line).
                    "classification_source": "llm_invalid_code" if code_rejected else "llm"
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
    entries: list[dict],
    taxonomy_context: TaxonomyContext,
    taxonomy: dict,
    batch_size: int = 15
) -> tuple[list[dict], ClassificationStats]:
    """
    Classify a batch of entries with the same taxonomy context.

    Args:
        entries: List of entry dicts with 'text' field
        taxonomy_context: Shared taxonomy context for these entries
        taxonomy: Full taxonomy reference
        batch_size: Max entries per LLM call

    Returns:
        Tuple of (classified_entries, stats)
    """
    if batch_size <= 0:
        # range(0, len(entries), batch_size) raises ValueError("range() arg 3
        # must not be zero") for 0, and silently returns an empty range for
        # negative batch_size (every entry skipped, no error at all) -- fail
        # loudly and specifically instead of either.
        raise ValueError("batch_size must be greater than zero")

    all_results = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    llm_batches = 0  # batches that attempted an LLM call
    observed_model = None  # model id the API actually served (#459)
    failed_batches = 0  # batches whose LLM call raised (entries fell back)

    all_suggested_codes, taxonomy_ref = _build_taxonomy_ref_for_batch(taxonomy_context, taxonomy)
    valid_codes = _valid_taxonomy_codes(taxonomy)

    # Process in batches
    for batch_start in range(0, len(entries), batch_size):
        batch_entries = entries[batch_start:batch_start + batch_size]

        batch_results, batch_stats = _classify_one_batch(
            batch_entries, batch_start, taxonomy_context, all_suggested_codes,
            taxonomy_ref, valid_codes
        )
        all_results.extend(batch_results)
        total_input_tokens += batch_stats.input_tokens
        total_output_tokens += batch_stats.output_tokens
        total_cost += batch_stats.cost
        llm_batches += batch_stats.llm_batches
        failed_batches += batch_stats.failed_batches
        observed_model = batch_stats.observed_model or observed_model

    stats: ClassificationStats = {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "cost": total_cost,
        # What actually served the calls. This function used to also accept a
        # `model` parameter no orchestrator ever passed a non-default value
        # for, so recording IT stamped 100/100 corpus artifacts with a model
        # the run never used (#459) -- and 3b is the one deliberately on
        # Haiku, which is exactly the comparison the field exists for. That
        # dead parameter was removed rather than forwarded (#644 review).
        "model": observed_model,
        # Which revision of the classification policy (prompt.py's
        # CLASSIFICATION_RULES_VERSION) produced this run's classifications --
        # a rules change should be identifiable in artifacts the same way a
        # model change already is via "model" above.
        "classification_rules_version": CLASSIFICATION_RULES_VERSION,
        "entries_classified": len(all_results),
        "llm_batches": llm_batches,
        "failed_batches": failed_batches,
        "llm_classified": sum(1 for r in all_results if r.get("classification_source") == "llm"),
        "fallback_entries": sum(1 for r in all_results if r.get("classification_source") == "fallback"),
        "empty_entries": sum(1 for r in all_results if r.get("classification_source") == "empty_entry"),
        # A hallucinated/unknown taxonomy code the LLM answered with, rejected
        # and coalesced to the fallback code (#5.10) -- counted separately
        # from "llm" (clean) and "fallback" (the whole batch's call failed)
        # so this specific degradation mode is visible in the artifact.
        "invalid_code_entries": sum(1 for r in all_results if r.get("classification_source") == "llm_invalid_code"),
    }

    return all_results, stats


def group_entries_by_hierarchy(entries: list[dict]) -> dict[str, list[dict]]:
    """
    Group entries by their hierarchy path (for batching).

    Returns dict mapping hierarchy_key -> list of entries
    """
    groups = {}

    for entry in entries:
        hierarchy = entry.get("hierarchy", [])
        # str()-coerce each element: hierarchy is normally a list of strings,
        # but " > ".join() raises TypeError on a non-string element (e.g. a
        # stray int), which would otherwise crash the whole classification
        # run over a single malformed hierarchy entry.
        key = " > ".join(str(h) for h in hierarchy) if hierarchy else NO_HIERARCHY_KEY

        if key not in groups:
            groups[key] = []
        groups[key].append(entry)

    return groups


def validate_t_classifications(
    entries: list[dict],
    taxonomy: dict
) -> tuple[list[dict], dict]:
    """
    T-validation gate: Re-evaluate any entries classified as T (miscellaneous).

    T should be used in <2% of cases. This gate takes all T classifications
    and asks the LLM to reconsider with the FULL taxonomy and strong guidance
    that T is an absolute last resort.

    Args:
        entries: List of classified entries (some may have taxonomy_code="T")
        taxonomy: Full taxonomy reference

    Returns:
        Tuple of (updated_entries, stats) where T entries may be reclassified.
        `entries` itself is never mutated -- a fresh list of copies is built
        and returned instead (see the "Apply reclassifications" comment
        below), so a caller holding onto its original list is unaffected by
        this call.
    """
    # Find entries classified as exactly "T" (miscellaneous)
    # NOTE: We only review "T", not T-family codes like T1 (Community Engagement)
    # which are valid specific classifications
    t_entries = [(i, e) for i, e in enumerate(entries) if e.get("taxonomy_code") == "T"]

    if not t_entries:
        return entries, {"t_entries_reviewed": 0, "t_entries_reclassified": 0, "cost": 0.0}

    # Build FULL taxonomy reference for maximum context
    taxonomy_ref = build_taxonomy_codes_for_prompt(taxonomy)

    # Build the validation prompt -- the template lives in prompt.py
    # (the #601 hoist pattern applied here too), not inline, so it reads
    # and is tested the same way the main classification prompt is.
    system_prompt = _T_VALIDATION_SYSTEM_PROMPT_TEMPLATE.format(taxonomy_ref=taxonomy_ref)

    # Format entries for review
    entries_text = []
    for idx, entry in t_entries:
        text = entry.get("text", "")[:500]  # Truncate long entries
        hierarchy = " > ".join(entry.get("hierarchy", [])) or NO_HIERARCHY_KEY
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
                # An unrecognized wrapper shape is malformed, not a puzzle to
                # guess at: reaching for "the first dict value" made an
                # unexpected response shape look like a valid one instead of
                # a visible, logged failure.
                logger.warning(
                    "Stage 3b T-validation: response object has none of "
                    "results/entries/classifications (keys=%s); treating as "
                    "no reclassifications", list(result.keys())
                )
                reclassifications = []
        else:
            reclassifications = result

        if not isinstance(reclassifications, list):
            logger.warning(
                "Stage 3b T-validation: reclassifications was %s, not a "
                "list; treating as none", type(reclassifications).__name__
            )
            reclassifications = []

        valid_codes = _valid_taxonomy_codes(taxonomy)

        # Apply reclassifications to copies, never the caller's own entries:
        # this is untrusted LLM output, and a caller shouldn't be able to
        # observe a half-applied mutation of its own list on partial failure.
        updated_entries = [dict(e) for e in entries]

        reclassified_count = 0
        malformed = 0
        for reclass in reclassifications:
            if not isinstance(reclass, dict):
                malformed += 1
                continue

            entry_idx = reclass.get("entry_index")
            # bool is a subclass of int (isinstance(True, int) is True), so a
            # boolean entry_index must be excluded explicitly or it would
            # pass this check and index entries[True]/entries[False].
            if (not isinstance(entry_idx, int) or isinstance(entry_idx, bool)
                    or not (0 <= entry_idx < len(updated_entries))):
                malformed += 1
                continue

            raw_new_code = reclass.get("new_code") or "T"
            # isinstance-guard before the set membership check: `new_code` is
            # untrusted LLM output and could be any JSON type, which would
            # raise TypeError: unhashable type on `in valid_codes` instead of
            # degrading to "T".
            new_code_rejected = not isinstance(raw_new_code, str) or raw_new_code not in valid_codes
            if new_code_rejected:
                logger.warning(
                    "Stage 3b T-validation: LLM returned unknown taxonomy "
                    "code %r for entry %d; keeping T", raw_new_code, entry_idx
                )
                new_code = "T"
            else:
                new_code = raw_new_code
            confidence = _normalize_confidence(reclass.get("confidence"), 0.5)
            reasoning = reclass.get("reasoning", "")
            if not isinstance(reasoning, str):
                reasoning = ""

            old_code = updated_entries[entry_idx].get("taxonomy_code")
            if old_code == "T" and new_code != "T":
                updated_entries[entry_idx]["taxonomy_code"] = new_code
                updated_entries[entry_idx]["taxonomy_confidence"] = confidence
                updated_entries[entry_idx]["classification_reasoning"] = f"[T-validation reclassified from T] {reasoning}"
                updated_entries[entry_idx]["t_validation_applied"] = True
                reclassified_count += 1
            elif old_code == "T" and new_code == "T":
                # T was confirmed for real, or the LLM proposed a code that
                # doesn't exist in the taxonomy and got coerced back to T --
                # these are different outcomes (one is agreement, the other
                # is a rejected hallucination) and must read differently in
                # the artifact, not both as "confirmed" (#5.3/#5.10: a
                # rejected result silently relabelled as a clean one is the
                # exact failure those rules exist to catch).
                tag = "T-validation: unknown code rejected, kept T" if new_code_rejected else "T-validation confirmed"
                updated_entries[entry_idx]["classification_reasoning"] = f"[{tag}] {reasoning}"
                updated_entries[entry_idx]["t_validation_applied"] = True

        if malformed:
            logger.warning(
                "Stage 3b T-validation: skipped %d malformed reclassification "
                "object(s)", malformed
            )

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

        return updated_entries, stats

    except Exception as exc:
        logger.exception(
            "Stage 3b T-validation failed",
            extra={"t_entries_reviewed": len(t_entries)},
        )
        # Nothing was applied: updated_entries only gets built after a
        # successful parse above, so the caller's original entries -- never
        # mutated -- come back untouched on any failure here.
        return entries, {
            "t_entries_reviewed": len(t_entries),
            "t_entries_reclassified": 0,
            "cost": 0.0,
            "error": str(exc),
        }


def reconnect_fragments(
    entries: list[dict]
) -> tuple[list[dict], dict]:
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
            if not isinstance(decision, dict):
                continue
            idx = decision.get("index")
            belongs_to = decision.get("belongs_to", "standalone")
            reasoning = decision.get("reasoning", "")
            if not isinstance(reasoning, str):
                reasoning = ""

            # bool is a subclass of int (isinstance(True, int) is True), so a
            # boolean index must be excluded explicitly or it would pass this
            # check and index entries[True]/entries[False].
            valid_idx = (isinstance(idx, int) and not isinstance(idx, bool)
                         and 0 <= idx < len(entries))
            if valid_idx:
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
        logger.exception(
            "Stage 3b fragment reconnection failed",
            extra={"fragments_reviewed": len(fragment_candidates)},
        )
        # Report reconnections already applied to entries before the error.
        return entries, {"fragments_reviewed": len(fragment_candidates), "fragments_reconnected": locals().get("reconnected_count", 0), "cost": 0.0, "error": str(e)}


def detect_duplicates(entries: list[dict], similarity_threshold: float = 0.9) -> tuple[list[dict], list[dict]]:
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

    def normalized_similarity(n1: str, n2: str) -> float:
        """Similarity ratio between two ALREADY-normalized texts."""
        if not n1 or not n2:
            return 0.0
        # Use SequenceMatcher for fuzzy matching
        return SequenceMatcher(None, n1, n2).ratio()

    # Candidates for comparison: entries with enough text to be meaningfully
    # compared (skip near-empty fragments). normalize_text runs once per
    # candidate here rather than repeatedly inside the comparison loop below.
    candidates = []
    for idx, entry in enumerate(entries):
        text = entry.get("text", "")
        # `.get(..., "")` only substitutes when "text" is ABSENT -- an entry
        # with an explicit `"text": None` (or any other non-string value)
        # survives that default and reaches `len(text)` below, raising
        # TypeError. Such an entry has no content to meaningfully compare,
        # so it is simply excluded from duplicate detection, the same as an
        # entry that is merely too short.
        if not isinstance(text, str) or len(text) < 20:  # Skip very short/non-string entries
            continue
        candidates.append((idx, entry, normalize_text(text)))

    # Find duplicates
    duplicate_pairs = []
    seen_duplicates = set()  # Track which indices have been marked as duplicates

    # O(n^2) over `candidates`. This used to bucket entries by their first
    # 100 normalized characters and only compare within a bucket -- a real
    # correctness bug, not just an optimization: two near-duplicate entries
    # that diverged in that prefix (different opening wording, a prepended
    # date/title) landed in different buckets and were NEVER compared,
    # silently missing real duplicates. Compare every eligible pair instead.
    # CV entry counts are small (at most a few hundred per document), so the
    # quadratic comparison is fine in practice; do not reintroduce prefix
    # bucketing (or any other blocking key) without proving it can't split a
    # genuinely similar pair the way the prefix key did.
    for i, (idx1, entry1, norm1) in enumerate(candidates):
        for idx2, entry2, norm2 in candidates[i + 1:]:
            if idx1 in seen_duplicates and idx2 in seen_duplicates:
                continue

            sim = normalized_similarity(norm1, norm2)
            if sim >= similarity_threshold:
                # A duplicate pair is recorded once per (idx1, idx2)
                # comparison that clears the threshold -- the loop above
                # only *skips* a pair when BOTH sides are already marked
                # duplicate, so one entry CAN appear in more than one
                # recorded pair (e.g. three near-identical entries A/B/C
                # produce pairs (A,B) and (A,C), both B and C marked
                # duplicate, A left as the surviving original).
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

                if code1.startswith(APPENDIX_TAXONOMY_PREFIX) and code2.startswith(M_SERIES_TAXONOMY_PREFIX):
                    # Second one is better classified, mark first as duplicate
                    seen_duplicates.add(idx1)
                elif code2.startswith(APPENDIX_TAXONOMY_PREFIX) and code1.startswith(M_SERIES_TAXONOMY_PREFIX):
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
