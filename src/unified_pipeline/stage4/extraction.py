"""LLM field extraction for stage 4: prompts, batch dispatch, recovery.

Moved verbatim from `stage_4_field_extractor.py` (#498), which re-exports every
name here.

`extract_fields_from_mapped_entries` calls `extract_fields_batch`,
`extract_cv_owner_name` and `infer_cv_owner_location` through THIS module's
globals. A test that stubs one of them must rebind it on
`unified_pipeline.stage4.extraction`, not on the facade -- the facade's
re-export is a second binding, and rebinding it there silently leaves the real
function in play (the #496 split-state lesson).
"""

import json
import logging
from collections import defaultdict
from typing import Any, Callable, NamedTuple, NotRequired, TypedDict

from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from unified_pipeline.core.batch_pool import make_batches, make_progress_printer, map_in_order, workers_from_config
from unified_pipeline.llm_client import LlmUsage, call_llm
from unified_pipeline.llm.retry import LLMOutageError
from unified_pipeline.llm_provenance import FALLBACK_SERVED_KEY, STAGE4_ENTRY_FALLBACK_KEY

from unified_pipeline.stage4.code_check import quarantine_invalid_taxonomy_codes
from unified_pipeline.stage4.coercion import (
    ReformattedFields,
    apply_regex_post_processing,
    coerce_field_value_types,
    normalize_dates,
)
from unified_pipeline.stage4.error_codes import (
    LLM_PROVIDER_ERROR,
    LLM_RESPONSE_INVALID,
    LLM_TIMEOUT,
    NO_MATCHING_EXTRACTION,
)
from unified_pipeline.stage4.owner_name import (
    add_target_names,
    extract_cv_owner_name,
    infer_cv_owner_location,
)
from unified_pipeline.stage4.schemas import (
    FIELD_DESCRIPTIONS,
    FIELD_SCHEMA_VERSION,
    NUMBERED_FIELD_RE,
    STAGE4_RECORDS_KEY,
    STAGE4_RECORDS_RETURNED_KEY,
    STAGE4_UNPLACED_ITEMS_KEY,
    get_active_schemas,
    get_field_schema,
    get_taxonomy_label,
)
from unified_pipeline.stage4.year_groups import date_year_group_members

logger = logging.getLogger(__name__)

# I/O-bound stage (LLM round trips, not CPU); the default is sized under the
# per-pod semaphore so one run cannot starve the others admitted alongside it
# (#881). Knob: CVICHE_STAGE4_BATCH_WORKERS, env var or llm yaml key.
STAGE4_BATCH_WORKERS = workers_from_config("CVICHE_STAGE4_BATCH_WORKERS")


class _ExtractedEntryFields(BaseModel):
    """Shape of one item in the batch extraction LLM's `entries` list.

    Validated at the external trust boundary before being merged into
    pipeline state: `entry_index` is the only field this module depends on
    structurally. Every other key is an arbitrary taxonomy-schema field name
    the LLM decided to include, so extras are allowed through rather than
    enumerated here.
    """
    model_config = ConfigDict(extra="allow")

    entry_index: int


class _RecoveredEntry(BaseModel):
    """Shape of one item in the recovery LLM's `recovered_entries` list."""
    model_config = ConfigDict(extra="allow")

    entry_id: str
    fields: dict[str, Any] = Field(default_factory=dict)


class _RecoveryResponse(BaseModel):
    """Shape of the recovery LLM's JSON response, validated before use."""

    recovered_entries: list[_RecoveredEntry] = Field(default_factory=list)
    recovery_notes: str = ""


# The `entry` dict (`{'taxonomy_code': ..., 'extracted_fields': ..., ...}`)
# stays dict[str, Any] throughout this module: it is produced by stage 3b and
# consumed by stages 5 and 6, so a typed contract for it is cross-module and
# belongs to a follow-up ticket, not this file.


class UnextractedContentReport(TypedDict):
    """Return shape of `calculate_unextracted_content`."""

    unextracted_words: list[str]
    extraction_coverage_percent: float
    total_original_words: int
    total_extracted_words: int


class BatchExtractionResult(TypedDict):
    """Return shape of `extract_fields_batch`.

    `entries` is left as list[dict[str, Any]]: each item is the cross-stage
    entry record described above, not a shape this module owns.
    """

    entries: list[dict[str, Any]]
    cost: float
    tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    success: bool
    failed_groups: int


class ExtractionStats(TypedDict):
    """Shape of the `stats` dict nested in `ExtractionResult`."""

    total_entries: int
    attempted: int
    extracted: int
    extraction_failed: int
    skipped: int
    batches_processed: int
    failed_batches: int
    had_extraction_errors: bool
    entries_reformatted: int
    cache_read_tokens: int
    cache_write_tokens: int
    invalid_code_entries: int
    invalid_taxonomy_codes: dict[str, int]


class ExtractionResult(TypedDict):
    """Return shape of `extract_fields_from_mapped_entries`.

    The "no valid entries" early-out only sets entries/total_cost/
    total_tokens/success (see that function's first `return`); the other
    keys are NotRequired because that path never populates them.
    """

    entries: list[dict[str, Any]]
    total_cost: float
    total_tokens: int
    success: bool
    cv_owner: NotRequired[dict[str, str]]
    cv_owner_location: NotRequired[dict[str, Any] | None]
    cache_read_tokens: NotRequired[int]
    cache_write_tokens: NotRequired[int]
    stats: NotRequired[ExtractionStats]
    partial_success: NotRequired[bool]


def calculate_unextracted_content(original_text: str, extracted_fields: dict[str, Any]) -> UnextractedContentReport:
    """
    Calculate what content from the original text was not extracted into any field.

    Args:
        original_text: Original CV entry text
        extracted_fields: Dictionary of extracted field values

    Returns:
        Dictionary with:
        - unextracted_words: List of words from original not found in any extracted field
        - extraction_coverage_percent: Percentage of original words that were extracted
        - total_original_words: Total content words in original text
        - total_extracted_words: Total content words found in extracted fields
    """
    import re

    # Tokenize original text (alphanumeric words only, lowercase)
    def tokenize(text):
        if not text or not isinstance(text, str):
            return set()
        # Extract alphanumeric tokens (ignore pure numbers, keep words with numbers like "2023")
        tokens = re.findall(r'\b[a-z]+[a-z0-9]*\b', text.lower())
        # Filter out common stop words and very short tokens
        stop_words = {'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                     'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be',
                     'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                     'would', 'should', 'could', 'may', 'might', 'must', 'can', 'it', 'this',
                     'that', 'these', 'those', 'i', 'you', 'he', 'she', 'we', 'they', 'my',
                     'your', 'his', 'her', 'our', 'their'}
        return set(t for t in tokens if len(t) > 2 and t not in stop_words)

    # Get tokens from original text
    original_tokens = tokenize(original_text)

    if not original_tokens:
        return {
            "unextracted_words": [],
            "extraction_coverage_percent": 100.0,
            "total_original_words": 0,
            "total_extracted_words": 0
        }

    # Collect all tokens from extracted fields
    extracted_tokens = set()
    for field_name, field_value in extracted_fields.items():
        if field_value is not None:
            # Handle lists (e.g., years_taught)
            if isinstance(field_value, list):
                for item in field_value:
                    extracted_tokens.update(tokenize(str(item)))
            else:
                extracted_tokens.update(tokenize(str(field_value)))

    # Find unextracted words
    unextracted = original_tokens - extracted_tokens
    extracted_from_original = original_tokens & extracted_tokens

    # Calculate coverage
    coverage_percent = (len(extracted_from_original) / len(original_tokens) * 100) if original_tokens else 100.0

    return {
        "unextracted_words": sorted(list(unextracted)),
        "extraction_coverage_percent": round(coverage_percent, 1),
        "total_original_words": len(original_tokens),
        "total_extracted_words": len(extracted_from_original)
    }


# A reply holding at least this many items for one entry is a multi-record
# reply: every item is kept under STAGE4_RECORDS_KEY instead of the last one
# overwriting the rest.
_MIN_RECORDS_PER_ENTRY = 2

# The source text an earlier record of a multi-record entry is cleaned
# against: none, so the regex pass fills nothing from the entry's text into it.
_NO_ENTRY_TEXT = ""


class _EntryExtraction(NamedTuple):
    """What the reply's items for one entry become on that entry."""

    fields: dict[str, Any]
    reformatted: ReformattedFields
    coverage: UnextractedContentReport
    records_returned: int


def _clean_item(entry_text: str, item_fields: dict[str, Any],
                taxonomy_code: str) -> tuple[dict[str, Any], ReformattedFields]:
    """One reply item's fields, coerced, date-normalized and regex-completed.

    Declared `dict[str, Any]`, not `ExtractedFields`: the input is unvalidated
    LLM JSON, and coerce/normalize rebuild the dict through variable keys,
    which no TypedDict can express. `apply_regex_post_processing` is where the
    shape is named (its signature returns `ExtractedFields`).
    """
    fields = coerce_field_value_types(item_fields)
    fields = normalize_dates(fields)
    cleaned, reformatted = apply_regex_post_processing(entry_text, fields, taxonomy_code)
    return dict(cleaned), reformatted


def _union_of_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every record's values under a key unique to its record, so coverage is
    measured over all of them and no record's value hides another's."""
    return {f"{index}.{key}": value
            for index, record in enumerate(records)
            for key, value in record.items()}


def _offschema_records(fields: dict[str, Any], taxonomy_code: str,
                       ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """`(fields, extra)`: `fields` without the off-schema keys that hold a
    whole second record, and those records (#1245). Nothing renders a key
    outside the code's schema, so each record was dropped from the output.

    - `<field>_<n>` keys: the numbered fields alone make record n. The
      parent's other fields are not copied: in a two-column list the parent's
      date belongs to the first column only.
    - an object under a key outside the schema, sharing a key with it
      (`additional_entry`, `additional_presentation`, `additional_period`):
      the parent's fields with the object's laid over them, since it most
      often repeats the parent at another venue or period.

    A list of records is left alone: fan-out splits it, or declines it with a
    warning (#1187).
    """
    schema = set(get_field_schema(taxonomy_code).get("fields") or ())
    numbered: dict[str, dict[str, Any]] = {}
    nested: list[dict[str, Any]] = []
    kept: dict[str, Any] = {}
    for key, value in fields.items():
        match = NUMBERED_FIELD_RE.match(key)
        if key in schema:
            kept[key] = value
        elif match and match.group("field") in schema:
            if not _is_blank(value):
                numbered.setdefault(match.group("n"), {})[match.group("field")] = value
        elif isinstance(value, dict) and schema & set(value):
            nested.append(value)
        else:
            kept[key] = value
    extra = [record for _, record in sorted(numbered.items(), key=lambda kv: int(kv[0]))]
    extra += [{**kept, **record} for record in nested]
    return kept, extra


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _with_offschema_records(cleaned: list[tuple[dict[str, Any], ReformattedFields]],
                            taxonomy_code: str,
                            ) -> list[tuple[dict[str, Any], ReformattedFields]]:
    """Each cleaned record followed by the records `_offschema_records` takes
    out of it, each cleaned without the entry's text, as an earlier record of
    a multi-record entry is."""
    out = []
    for fields, reformatted in cleaned:
        kept, extra = _offschema_records(fields, taxonomy_code)
        out.append((kept, reformatted))
        out += [_clean_item(_NO_ENTRY_TEXT, record, taxonomy_code) for record in extra]
    return out


def _extract_entry_items(entry_text: str, items: list[dict[str, Any]],
                         taxonomy_code: str) -> _EntryExtraction:
    """The reply's items for one entry, in reply order, as that entry's fields.

    One item is the entry's fields, exactly as before. With 2+ items the
    entry's scalar fields stay the last item, as they were when the last item
    overwrote the others, every item is also kept under STAGE4_RECORDS_KEY for
    `stage6/fan_out.py` to split, and coverage is measured over all of them.

    The regex pass fills a value it finds once in the entry's text (a PMID, a
    percent effort, the one closed date range). With several records that
    value belongs to one of them at most, so only the last record, the one
    the entry kept before, is offered the text; an earlier one gets the
    text-free cleanups alone.

    A second record the model filed under an off-schema key follows the
    record it came from (`_offschema_records`). `records_returned` stays the
    count of items the model returned.
    """
    cleaned = [_clean_item(_NO_ENTRY_TEXT, dict(item), taxonomy_code) for item in items[:-1]]
    cleaned.append(_clean_item(entry_text, dict(items[-1]), taxonomy_code))
    last_reformatted = cleaned[-1][1]
    cleaned = _with_offschema_records(cleaned, taxonomy_code)
    last_fields = cleaned[-1][0]
    if len(cleaned) < _MIN_RECORDS_PER_ENTRY:
        coverage = calculate_unextracted_content(entry_text, last_fields)
        return _EntryExtraction(last_fields, last_reformatted, coverage, len(items))
    records = [fields for fields, _ in cleaned]
    fields = {**last_fields, STAGE4_RECORDS_KEY: records}
    coverage = calculate_unextracted_content(entry_text, _union_of_records(records))
    return _EntryExtraction(fields, last_reformatted, coverage, len(items))


def _entry_with_extraction(entry: dict[str, Any], extraction: _EntryExtraction,
                           flags: dict[str, Any]) -> dict[str, Any]:
    """`entry` carrying `extraction`, then `flags`, then the reformatted
    fields and the multi-record count when there are any.

    A count an earlier pass wrote is dropped first: the recovery pass replaces
    the batch pass's fields, and its own reply decides the count.
    """
    result = {key: value for key, value in entry.items()
              if key != STAGE4_RECORDS_RETURNED_KEY}
    result.update({
        "extracted_fields": extraction.fields,
        "extraction_success": True,
        "extraction_coverage": extraction.coverage,
        **flags,
    })
    if extraction.reformatted:
        result["reformatted_fields"] = extraction.reformatted
    if extraction.records_returned >= _MIN_RECORDS_PER_ENTRY:
        result[STAGE4_RECORDS_RETURNED_KEY] = extraction.records_returned
    return result


def _fallback_flags(llm_result: dict[str, Any]) -> dict[str, Any]:
    """The entry flag recording which model answered, when the content-filter
    fallback served the group's call (#1174); empty otherwise. Write-only: the
    doctor and the quality score read it, nothing downstream does."""
    model = llm_result.get(FALLBACK_SERVED_KEY)
    return {STAGE4_ENTRY_FALLBACK_KEY: model} if model else {}

# =============================================================================
# LLM-ASSISTED RECOVERY FOR MESSY TABLE STRUCTURES
# =============================================================================

def needs_llm_recovery(entry: dict[str, Any], min_original_chars: int = 200, max_coverage: float = 30.0) -> bool:
    """
    Determine if an entry needs LLM-assisted recovery due to poor extraction.

    Triggers when:
    1. Original text is substantial (>200 chars)
    2. Extraction coverage is low (<30%)
    3. Entry has extractable patterns (dates, names, etc.)

    Also triggers, regardless of length floor or date/structure markers, when
    extraction produced nothing at all from substantive text (>=50 chars) --
    total-extraction loss on short entries (board certifications, languages,
    role lines) otherwise slips under the 200-char floor and vanishes from the
    output silently (#322).

    Args:
        entry: Entry with extraction_coverage information
        min_original_chars: Minimum original text length to consider
        max_coverage: Maximum coverage percentage to trigger recovery

    Returns:
        True if entry should be sent to LLM recovery
    """
    import re

    original_text = entry.get("text", "")
    coverage_info = entry.get("extraction_coverage", {})
    coverage_pct = coverage_info.get("extraction_coverage_percent", 100.0)

    # Total-extraction loss: no field got any value despite substantive text.
    # Sufficient signal by itself -- skip the length/date/structure checks.
    extracted_fields = entry.get("extracted_fields") or {}
    if len(original_text.strip()) >= 50 and not any(extracted_fields.values()):
        return True

    # Check basic conditions
    if len(original_text) < min_original_chars:
        return False
    if coverage_pct > max_coverage:
        return False

    # Check if there's extractable content (dates, structure indicators)
    has_dates = bool(re.search(r'\b(19|20)\d{2}\b', original_text))
    has_structure = bool(re.search(r'[\t|]|\n.*\n', original_text))  # Tabs, pipes, or multiple newlines

    return has_dates or has_structure


def _recovery_entry_id(entry: dict[str, Any]) -> str:
    """Deterministic id for one entry, stable across the recovery round-trip.

    Reuses the same (element_idx_start, element_idx_end) pair the merge-back
    step in `extract_fields_batch` already keys on, instead of minting a new
    field on the entry.
    """
    return f"{entry.get('element_idx_start')}_{entry.get('element_idx_end')}"


def attempt_llm_recovery(
    entries: list[dict[str, Any]],
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """
    Attempt LLM-assisted recovery for entries with poor extraction coverage.

    Uses taxonomy context to help the LLM understand the expected data structure
    and parse messy table-like content.

    Args:
        entries: List of entries needing recovery (same taxonomy code)
        cancel_check: Optional zero-arg callable forwarded to call_llm's own
            cancel_check, so a cancel can also fire between this call's
            retries. Raises to cancel; never returns True.

    Returns:
        Dict with:
        - entries: List of entries with recovered fields (unchanged or
          error-tagged originals for any that failed to recover)
        - cost: USD cost of the recovery LLM call (0.0 if none was billed)
        - tokens: total tokens used by the recovery LLM call
    """
    if not entries:
        return {"entries": entries, "cost": 0.0, "tokens": 0}

    # All entries should have the same taxonomy code
    taxonomy_code = entries[0].get("taxonomy_code", "UNKNOWN")
    taxonomy_label = get_taxonomy_label(taxonomy_code)
    schema = get_field_schema(taxonomy_code)

    # Combine all entry texts for context, each tagged with the deterministic
    # id the LLM must echo back. Matching recovered fields to entries by a
    # lowercase substring check against the first 100 chars of entry text
    # (the prior approach) lets two similar entries satisfy the same snippet,
    # or one recovery result apply to the wrong entry -- an exact id lookup
    # cannot mismap.
    combined_text = "\n---ENTRY BOUNDARY---\n".join(
        f"[entry_id: {_recovery_entry_id(entry)}]\n{entry.get('text', '')}"
        for entry in entries
    )

    # Build recovery prompt with taxonomy context
    prompt = f"""You are parsing a poorly formatted CV section. The text appears to have table-like structure where items and their attributes (like dates) may be misaligned or separated.

**Section Type**: {taxonomy_code} - {taxonomy_label}

**Expected Fields**: {', '.join(schema['fields'])}

**Field Descriptions**:
{_get_field_descriptions(taxonomy_code)}

**Raw Text to Parse** (each entry is preceded by its exact entry_id in brackets):
{combined_text}

**Instructions**:
1. This text likely contains multiple entries that should each have their own set of fields
2. Items and dates may be in separate columns or blocks - match them by position (1st item → 1st date, 2nd → 2nd, etc.)
3. Look for patterns like:
   - Newline-separated lists where items are in one section and dates in another
   - Tab or space-aligned columns
   - Parenthetical information (role, dates, etc.)
4. Extract ALL entries you can identify, even if some fields are missing
5. For each entry, extract: {', '.join(schema['fields'][:5])}{'...' if len(schema['fields']) > 5 else ''}
6. Use "---ENTRY BOUNDARY---" markers to identify separate entry groups if present
7. Echo the entry's exact "entry_id" (the string shown in brackets before its text, e.g. "12_12") back in your response so results can be matched precisely -- do not paraphrase or truncate it

**Return JSON**:
{{
  "recovered_entries": [
    {{
      "entry_id": "the exact entry_id shown in brackets above the entry text",
      "fields": {{
        "field1": "value1",
        "field2": "value2",
        ...
      }}
    }},
    ...
  ],
  "recovery_notes": "Brief explanation of how you parsed the structure"
}}"""

    llm_result = None
    try:
        llm_result = call_llm(
            stage="stage_4",
            messages=[
                {"role": "system", "content": "You are an expert at parsing messy document structures. Extract structured data even from poorly formatted tables and lists."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            cancel_check=cancel_check,
        )

        result_text = llm_result["content"]
        parsed = json.loads(result_text)
        validated = _RecoveryResponse.model_validate(parsed)

        cost = llm_result["cost"]
        tokens = llm_result.get("total_tokens", 0)

        logger.info("LLM Recovery: %d entries recovered | $%.4f", len(validated.recovered_entries), cost)
        if validated.recovery_notes:
            logger.info("Recovery notes: %s", validated.recovery_notes[:100])

        # Match recovered entries back to original entries by exact id. The
        # prompt asks for every record an entry holds, so one id can carry
        # several items; all of them are kept, in reply order.
        recovered_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in validated.recovered_entries:
            recovered_by_id[rec.entry_id].append(rec.fields)

        recovered_entries = []
        for entry in entries:
            matched = recovered_by_id.get(_recovery_entry_id(entry))

            if matched:
                extraction = _extract_entry_items(entry.get("text", ""), matched, taxonomy_code)
                recovered_entries.append(
                    _entry_with_extraction(entry, extraction, {"llm_recovery_applied": True}))
            else:
                # No matching recovery - keep original
                recovered_entries.append({
                    **entry,
                    "llm_recovery_attempted": True,
                    "llm_recovery_matched": False
                })

        return {"entries": recovered_entries, "cost": cost, "tokens": tokens}

    except (ReadTimeoutError, ConnectTimeoutError):
        logger.exception("Stage 4 recovery LLM call timed out for taxonomy %s", taxonomy_code)
        return {
            "entries": [{**entry, "llm_recovery_error": LLM_TIMEOUT} for entry in entries],
            "cost": 0.0,
            "tokens": 0,
        }
    except json.JSONDecodeError:
        logger.exception("Stage 4 recovery response for taxonomy %s was not valid JSON", taxonomy_code)
        return {
            "entries": [{**entry, "llm_recovery_error": LLM_RESPONSE_INVALID} for entry in entries],
            "cost": llm_result["cost"] if llm_result else 0.0,
            "tokens": llm_result.get("total_tokens", 0) if llm_result else 0,
        }
    except ValidationError as exc:
        logger.warning(
            "Stage 4 recovery response for taxonomy %s failed shape validation: %s",
            taxonomy_code, exc,
        )
        return {
            "entries": [{**entry, "llm_recovery_error": LLM_RESPONSE_INVALID} for entry in entries],
            "cost": llm_result["cost"] if llm_result else 0.0,
            "tokens": llm_result.get("total_tokens", 0) if llm_result else 0,
        }
    except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
        raise
    except Exception:
        logger.exception("Stage 4 recovery LLM call failed for taxonomy %s", taxonomy_code)
        return {
            "entries": [{**entry, "llm_recovery_error": LLM_PROVIDER_ERROR} for entry in entries],
            "cost": llm_result["cost"] if llm_result else 0.0,
            "tokens": llm_result.get("total_tokens", 0) if llm_result else 0,
        }

def _get_field_descriptions(taxonomy_code: str) -> str:
    """Get human-readable descriptions of expected fields for a taxonomy code.

    Reads from the shared FIELD_DESCRIPTIONS constant (single source of truth).
    """
    if taxonomy_code in FIELD_DESCRIPTIONS:
        lines = []
        for field_name, desc in FIELD_DESCRIPTIONS[taxonomy_code].items():
            lines.append(f"- {field_name}: {desc}")
        return "\n".join(lines)

    return f"Extract all available fields: {', '.join(get_field_schema(taxonomy_code)['fields'])}"


#: Appended (as instruction 10) when a batch holds an entry stamped with
#: `context_heading` (#985). Leading newline: it follows instruction 8/9.
CONTEXT_HEADING_INSTRUCTION = """
10. **Sub-heading context**: an entry marked "(under: X)" sits beneath the sub-heading X in the CV. Use X to fill institution, role, title, audience, level or status fields when the entry text itself omits them. Never override what the entry text states. When the entry gives its own role, even as a verb or a qualifier, that role wins over X: "Co-directed with ..." under "Course Director" is role "Co-Director", and "Assistant ..." or "Associate ..." stays as the entry words it. Do not copy X into a field it does not describe, and never copy X verbatim when it only names a kind of activity (e.g. "New Course Development")."""


#: Rule 1 of the batch extraction prompt (#1243). An entry can hold several
#: records: two mentees on one line, a hospital post and a faculty rank split by
#: a tab, one society with two roles and their own dates. The prompt used to say
#: only "For each entry, extract all available fields", and the model returned
#: one record for such an entry; the rest never reached the output (EBYSBC: 17 of
#: 40 CVs, 50 records). The reply parser already keeps every item an entry gets
#: (#1265) and stage 6 fans them out, so this asks for that shape.
MULTI_RECORD_INSTRUCTION = """1. For each entry, extract all available fields. One entry can hold several records: several people, roles, positions, committees, memberships, talks, courses, degrees, licences or patents, each usually with its own date or date range, often separated by a tab, a semicolon or a line break. Return one item per record, each with the same "entry_index", and repeat in every item a value the records share (e.g. the organization). Never join two records' values into one field, and never keep only the first, the last or the parent record. An entry about one thing is one item, even when it lists several authors, investigators or dates for that thing."""

#: Appended to rule 1 for the codes whose entry is one work: a citation, an
#: abstract, a chapter, a patent or a grant (S*, M2*, T). The wave-4 A/B
#: (2026-10-05) split NDXXAD 360, one S8 abstract shown as a poster at one
#: meeting and a talk at another, into two items with the same title and
#: authors, which duplicated the abstract. Two works, each with its own title
#: (MYNQRA 63's two patents, EQGGRB 144's two chapters), still split.
SINGLE_WORK_INSTRUCTION = """ Here one citation, abstract, poster, chapter, patent or grant is one work: when the entry gives the same work at several venues, meetings, presentations or dates (e.g. "Poster presentation at Meeting A 2016, and oral presentation at Meeting B 2016"), return ONE item and put every venue or date in that item's fields (e.g. "Meeting A 2016; Meeting B 2016"). Never return two items with the same title. Split only works that each have their own title."""
SINGLE_WORK_CODE_PREFIXES = ('S', 'M2', 'T')

#: Appended to rule 1 for a lecture or talk code (K4, R). The EOAHMI recheck
#: (2026-10-05, #1445) found JBUVYV 346, one K4 lecture series given in three
#: years on four topics, returned as 12 items (each topic in each year), and
#: QTATUP 980, one R talk given on seven dates as a teleconference series to
#: named practitioners, returned as 7 items that all dropped the audience. A
#: talk given in several cities, each with its own date, still splits
#: (QTATUP 800, RVROVQ 129).
ONE_SERIES_INSTRUCTION = """ Never return an item for each combination of two lists (e.g. each topic in each year): one lecture series given in several years on several topics is ONE item, with every year in its date (e.g. "2015; 2016; 2017") and every topic in its title (e.g. "Series: Topic A; Topic B"). When one talk is given on several dates, keep in every item the words that say how and to whom it was given (e.g. "a teleconference series to nurses")."""

#: Appended to rule 1 for K3. The EOAHMI recheck (#1445) found JBUVYV 337,
#: "House Leader, <program>, <school> 2016-2020" then a line naming only the
#: program's leadership council, returned as two items; the second had no
#: role, and stage 5c then copied the first item's role onto it.
CONTEXT_LINE_INSTRUCTION = """ A line that only names a body the program belongs to (e.g. a council), with no role or date of its own, is context for that program's item, not a new item."""

_RULE1_GUARDS = {'K3': CONTEXT_LINE_INSTRUCTION, 'K4': ONE_SERIES_INSTRUCTION, 'R': ONE_SERIES_INSTRUCTION}


def multi_record_rule(code: str) -> str:
    """Rule 1 for `code`: the multi-record rule, with the one-work guard
    appended for a citation, patent or grant code, and the one-series or
    context-line guard for the codes in `_RULE1_GUARDS`."""
    rule = MULTI_RECORD_INSTRUCTION
    if code.startswith(SINGLE_WORK_CODE_PREFIXES):
        rule += SINGLE_WORK_INSTRUCTION
    return rule + _RULE1_GUARDS.get(code, "")


#: Rule 6 of the batch extraction prompt. The tab rule used to say only that a
#: tab separates columns, which merged a tab-joined second record into the first
#: (#1243); the last sentence defers to rule 1 for that case.
TAB_COLUMNS_INSTRUCTION = """6. Tab-separated values: If text contains tabs (\\t) or pipe characters (|), these indicate table columns - extract each column as a separate field value, not as merged text. A column that starts another record (e.g. a second role or person with its own date) is a new item under rule 1."""


# Clinical trials file as current or past funding (#291), never as a pending
# application or a patent, so only these two grant prompts carry the mapping of
# a trial onto the grant fields the grant table renders.
CLINICAL_TRIAL_CODES = frozenset({'M2A', 'M2B'})
CLINICAL_TRIAL_FIELD_MAPPING = """
   - A CLINICAL TRIAL filed here uses the same fields: title = the trial title with its phase (e.g., "Phase II trial of ..."), grant_number = its NCT or protocol number, agency = its sponsor, pi_role = the CV owner's role on the trial (e.g., "Site PI", "Sub-Investigator")"""


def grant_owner_role_rule(cv_owner_name: dict[str, str] | None) -> str:
    """The grant prompt's pi_role line (#1403), naming the CV owner.

    The wave-4 A/B (2026-10-05) showed why the owner has to be named: told
    only that a "PI:" label on *another* person is not the owner's role, a
    model that cannot tell who the owner is cleared pi_role on grants whose
    label names the owner (RVROVQ 9/9 "P.I.: <owner>", XELRLZ "<owner> (PI)",
    CXRYCF "PI: <owner>"). With no owner name there is nothing to compare a
    label against, so the line is left out and the prompt keeps its
    pre-#1403 behaviour.
    """
    last_name = (cv_owner_name or {}).get('last_name')
    if not last_name:
        return ""
    full_name = cv_owner_name.get('full_name') or last_name
    owner = f'"{full_name}" (surname "{last_name}")' if full_name != last_name else f'"{last_name}"'
    return f"""
   - pi_role = the role on this grant of the CV owner, {owner}. A PI label can come before or after a name: "P.I.: <name>", "PI: <name>", "PI <name>", "<name> (PI)", "Principal Investigator: <name>". When that label names the CV owner (in full, by surname, or with initials), pi_role = "PI", or the label as written (e.g., "Site PI"). When it names someone else, that person is pi_name, and pi_role is only a role the entry states for the CV owner (e.g., "{last_name} (Co-I)", "Mentor"); if it states none, leave pi_role null"""


def build_extraction_prompt(
    entries: list[dict[str, Any]],
    schema: dict[str, Any],
    code: str,
    cv_owner_name: dict[str, str] | None = None,
) -> str:
    """
    Build the batch LLM prompt for field extraction for one taxonomy-code group.

    Single source of truth for the production extraction prompt.
    `extract_fields_batch()` calls this rather than building the prompt
    inline -- it previously did both, with this function building an unused,
    simpler single-entry variant that could silently drift from the real one.

    Args:
        entries: Entries for this taxonomy code (already grouped by caller)
        schema: Field schema for `code` (fields, required)
        code: Taxonomy code for this group (e.g. "M2A", "S8")
        cv_owner_name: Dict with 'last_name' and optionally 'full_name' of CV
            owner, used to hint the target_name instruction for publications
            and presentations (codes starting with "S", and "R") and to name
            the owner in the grant pi_role rule (codes starting with "M2")
    """
    code_label = get_taxonomy_label(code)

    # Build target_name instruction for publications/presentations
    target_name_instruction = ""
    if code.startswith('S') or code == 'R':
        if cv_owner_name and cv_owner_name.get('last_name'):
            last_name = cv_owner_name['last_name']
            target_name_instruction = f"""
9. **target_name**: This is the CV owner's publication. Find "{last_name}" (or similar) in the author list and extract their name EXACTLY as it appears (e.g., "{last_name} JA" or "{last_name}, J."). This identifies the CV owner among the authors."""
        else:
            target_name_instruction = """
9. **target_name**: Extract the CV owner's name from the author list. In a CV, the owner is typically the first author, last author, or marked with an asterisk (*). Extract the name exactly as it appears in the author list."""

    # Build field guide from FIELD_DESCRIPTIONS if available for this code
    field_guide_section = ""
    if code in FIELD_DESCRIPTIONS:
        guide_lines = []
        for field_name, desc in FIELD_DESCRIPTIONS[code].items():
            guide_lines.append(f"- {field_name}: {desc}")
        field_guide_section = "\n**Field Guide** (what each field should contain):\n" + "\n".join(guide_lines) + "\n"

    prompt = f"""Extract structured fields from these CV entries.

**Classification**: {code} - {code_label}

**Fields to Extract**: {', '.join(schema['fields'])}
{field_guide_section}
**Required Fields**: {', '.join(schema.get('required', []))}

**Entries**:
"""
    for i, entry in enumerate(entries):
        under = f" (under: {entry['context_heading']})" if entry.get("context_heading") else ""
        prompt += f"\n[Entry {i}]{under}:\n{entry.get('text', '')}\n"

    # #985: only a batch holding a stamped entry carries the extra rule, so an
    # unstamped batch's prompt stays byte-identical.
    context_heading_instruction = CONTEXT_HEADING_INSTRUCTION if any(e.get("context_heading") for e in entries) else ""

    # Add code-specific instructions
    code_specific_instructions = ""
    if code.startswith('M2'):
        code_specific_instructions = f"""
9. **GRANTS (M2A/M2B/M2C)**:
   - pi_name = a PERSON'S NAME (e.g., "Susan Bostwick", "John Smith") - NOT the project title
   - title = the scientific project title - NOT a person's name, NOT FTE information
   - When there is no "Title:" label, an unlabelled name of the project or program that comes before the labelled parts (e.g., before "Program Partner:" or "Funder:") is the title; do not leave title null when the entry names one
   - percent_effort = extract FTE as percentage (e.g., ".08FTE" → "8%", "0.1 FTE" → "10%")
   - Do NOT put the project title in pi_name field
   - If no PI name is found, leave pi_name as null{grant_owner_role_rule(cv_owner_name)}
   - status = the grant's status only when the entry itself states one (e.g., "Update: withdrawn" → "withdrawn"); otherwise null
   - notes = a labelled remark no other field holds (e.g., the text after "Note:"); otherwise null"""
        if code in CLINICAL_TRIAL_CODES:
            code_specific_instructions += CLINICAL_TRIAL_FIELD_MAPPING
    elif code == 'K4':
        code_specific_instructions = """
9. **CONTINUING EDUCATION (K4)** - CRITICAL field separation:
   - If text reads "[Role] of/for [Title]" (e.g., "Creator and Presenter of Insomnia evaluation and management"):
     * role = "Creator and Presenter" (everything BEFORE "of/for")
     * activity_title = "Insomnia evaluation and management" (everything AFTER "of/for")
   - activity_title must NEVER include the person's role
   - role must NEVER include the activity/course name"""
    elif code == 'I':
        code_specific_instructions = """
9. **MEMBERSHIPS (I)** - CRITICAL field separation:
   - If text reads "Fellow | American Academy of Pediatrics" or "Fellow, Organization Name":
     * membership_type = "Fellow" (the designation/level)
     * organization = "American Academy of Pediatrics" (the society name only)
   - Common membership_type values: Fellow, Member, Diplomat, Associate Member, Honorary Member
   - Do NOT merge membership_type into organization - they are separate fields"""
    elif code == 'Q2':
        code_specific_instructions = """
9. **EXTRAMURAL COMMITTEES (Q2)** - CRITICAL three-way field separation:
   - committee_name = the specific committee name ONLY (e.g., "Education Committee")
   - role = ONLY the role word (e.g., "Member", "Chair") - NOT the committee name
   - organization = the parent organization (e.g., "American Academy of Neurology")
   - Example: "Member, Education Committee, American Academy of Neurology"
     * committee_name = "Education Committee"
     * role = "Member"
     * organization = "American Academy of Neurology"
   - Do NOT put the committee name in the role field or vice versa
   - A SESSION, PANEL, SYMPOSIUM or WORKSHOP entry (e.g., one you moderated or chaired) names a title, often in quotes: committee_name = that title (without the quotes), role = the role word(s), organization = the meeting or society
   - Example: "Moderator, Society for Example Medicine Annual Meeting, 'Advances in Example Care'"
     * committee_name = "Advances in Example Care"
     * role = "Moderator"
     * organization = "Society for Example Medicine Annual Meeting"
   - Never leave committee_name null when the entry names such a title, and never put the title in a key not listed above (e.g., "topic")"""
    elif code == 'P':
        code_specific_instructions = """
9. **INSTITUTIONAL COMMITTEES (P)** - CRITICAL field separation:
   - committee_name = the committee/body name ONLY (e.g., "Quality Improvement Committee")
   - role = ONLY the role word(s) (e.g., "Chair", "Member") - NOT the committee name
   - Do NOT merge role into committee_name or vice versa"""
    elif code == 'N3B':
        code_specific_instructions = """
9. **PAST MENTEES (N3B)** - when vs. now:
   - site_position = where and in what the mentee was mentored DURING the mentoring: the school or institution, and the program, project, fellowship or committee the entry names for that period (e.g., "Senior, Example College, 2000" → site_position = "Example College"; "MS, Thesis committee" → site_position = "Thesis committee")
   - current_position = where the mentee is NOW, only when the entry says so (e.g., "now Assistant Professor at ..."); otherwise null
   - Do NOT put the institution of the mentoring period in current_position"""
    elif code == 'S8':
        # A sub-point of the S/R target_name instruction (9), which names the owner.
        code_specific_instructions = """
   - Co-presented: when the entry says it was "co-presented with" other people, the CV owner presented it too. authors = the CV owner's name first, then those co-presenters, and target_name = the owner's name as written there. Never list only the co-presenters"""
    elif code == 'D3':
        code_specific_instructions = """
9. **OTHER POSITIONS (D3)** - consulting engagements:
   - A consulting line (a year, the client organization, the project topic, a client contact) that states no job title: title = the project topic, organization = the client organization
   - Do not leave title null when the entry names the work done
   - Never put the client contact's name or job title in title"""

    prompt += f"""
**Instructions**:
{multi_record_rule(code)}
2. Use null for fields not found
3. Dates:
   - For single dates: use YYYY-MM-DD or YYYY format
   - For date ranges (e.g., "2005-2008"): use start_date and end_date fields
   - For ongoing dates: preserve "present", "ongoing", or "current" exactly as written (do NOT convert to a year)
4. Authors: single string (e.g., "Smith J, Doe A")
5. Emails: extract multiple emails separately (primary_email, secondary_email, institutional_email, personal_email)
{TAB_COLUMNS_INSTRUCTION}
7. Only extract explicitly stated information - do not infer or guess
8. CRITICAL: Include "entry_index" field in each extraction to match the entry number above{target_name_instruction}{code_specific_instructions}{context_heading_instruction}

Return JSON with format:
{{
  "entries": [
    {{
      "entry_index": 0,
      "field1": "value1",
      "field2": "value2",
      ...
    }},
    {{
      "entry_index": 1,
      ...
    }}
  ]
}}"""

    return prompt


def _validate_raw_extractions(raw_extractions: list[Any], code: str) -> dict[int, list[dict[str, Any]]]:
    """Validate raw LLM extraction items at the external trust boundary, and
    group them by `entry_index`, in reply order.

    A malformed item is dropped with a warning. Every valid item is kept: a
    reply that holds several items for one entry (one per record of a
    multi-record entry) used to keep only the last of them.
    """
    extraction_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw_item in raw_extractions:
        try:
            validated_item = _ExtractedEntryFields.model_validate(raw_item)
        except ValidationError as exc:
            logger.warning(
                "Stage 4 batch extraction for %s dropped one malformed LLM entry: %s",
                code, exc,
            )
            continue
        extraction_map[validated_item.entry_index].append(
            validated_item.model_dump(exclude={"entry_index"}))
    return dict(extraction_map)


def _extraction_map(raw_extractions: list[Any], group_size: int, code: str,
                    ) -> tuple[dict[int, list[dict[str, Any]]], dict[str, int]]:
    """The reply's valid items by `entry_index` (`_validate_raw_extractions`),
    with the items whose index names no entry of the group placed where they
    can be, and the entry flag that records the ones no entry could take
    (empty when there are none).

    The model sometimes numbers the records of ONE entry 0, 1, 2, ... as if
    each were an entry (#1243, RCBKFG CAOACN: a one-entry group's reply held
    entry_index 0..4). Only index 0 matched, so the other items were dropped
    without a word. In a one-entry group every item can only belong to that
    entry, so they are folded into entry 0 as further records, in index
    order. In a larger group the owner is unknowable: the caller's merge,
    which reads indices 0..group_size-1 only, leaves them out, so a warning
    names them and the caller stamps the group with the count.
    """
    extraction_map = _validate_raw_extractions(raw_extractions, code)
    out_of_range = sorted(i for i in extraction_map if not 0 <= i < group_size)
    if not out_of_range:
        return extraction_map, {}
    if group_size == 1:
        return {0: [item for i in sorted(extraction_map) for item in extraction_map[i]]}, {}
    unplaced = sum(len(extraction_map[i]) for i in out_of_range)
    logger.warning(
        "Stage 4 batch extraction for %s: %d reply item(s) at entry_index %s fit no entry "
        "of the %d-entry group; left out and stamped %s on the group (#1243)",
        code, unplaced, out_of_range, group_size, STAGE4_UNPLACED_ITEMS_KEY,
    )
    return extraction_map, {STAGE4_UNPLACED_ITEMS_KEY: unplaced}


def extract_fields_batch(
    entries: list[dict[str, Any]],
    batch_idx: int,
    total_batches: int,
    cv_owner_name: dict[str, str] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> BatchExtractionResult:
    """
    Extract fields from a batch of entries using LLM.

    Args:
        entries: List of entries to process
        batch_idx: Current batch index
        total_batches: Total number of batches
        cv_owner_name: Dict with 'last_name' and optionally 'full_name' of CV owner
        cancel_check: Raises to cancel; never returns True. Checked before
            each taxonomy-group's call and forwarded into every call_llm().
    """
    logger.info("Processing batch %d/%d (%d entries)...", batch_idx + 1, total_batches, len(entries))

    # Group by taxonomy code for better prompting
    entries_by_code = {}
    for entry in entries:
        code = entry.get("taxonomy_code", "UNKNOWN")
        if code not in entries_by_code:
            entries_by_code[code] = []
        entries_by_code[code].append(entry)

    all_extracted = []
    total_cost = 0.0
    total_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    # Taxonomy-code groups within this batch whose LLM call or response
    # failed outright (as opposed to individual entries the LLM just didn't
    # return a match for, which are marked per-entry below). Rolled up into
    # this batch's "success"/"failed_groups" so the caller can tell a batch
    # that silently lost an entire code group from one that fully succeeded.
    failed_groups = 0

    for code, code_entries in entries_by_code.items():
        # Outside the try/except below: a raised cancel must propagate, not
        # be caught as a generic provider failure.
        if cancel_check is not None:
            cancel_check()

        schema = get_field_schema(code)
        prompt = build_extraction_prompt(code_entries, schema, code, cv_owner_name)

        # Call LLM
        try:
            messages = [
                {"role": "system", "content": "You are a precise field extraction system for academic CVs. Extract only explicitly stated information."},
                {"role": "user", "content": prompt}
            ]

            llm_result = call_llm(
                stage="stage_4",
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                cancel_check=cancel_check,
            )

            # Parse response
            content = llm_result["content"]
            result = json.loads(content)

            cost = llm_result["cost"]
            total_cost += cost
            total_tokens += llm_result["total_tokens"]
            total_cache_read_tokens += llm_result.get("cache_read_tokens", 0)
            total_cache_write_tokens += llm_result.get("cache_write_tokens", 0)

            # Log cost for this call
            logger.info(
                "[%s] %d entries | %s tokens | $%.4f",
                code, len(code_entries), f"{llm_result['total_tokens']:,}", cost,
            )

            # Merge extracted fields back with entries
            # CRITICAL: Use entry_index from LLM response to match correctly
            raw_extractions = result.get("entries", result.get("extractions", []))
            if not isinstance(raw_extractions, list):
                logger.warning(
                    "Stage 4 batch extraction response for %s was not a list (got %s); treating as empty",
                    code, type(raw_extractions).__name__,
                )
                raw_extractions = []

            # Validate each item at the external trust boundary (see _extraction_map).
            extraction_map, unplaced_flag = _extraction_map(raw_extractions, len(code_entries), code)
            group_flags = {**_fallback_flags(llm_result), **unplaced_flag}

            # Merge using explicit indices to avoid mismapping
            for i, entry in enumerate(code_entries):
                if i in extraction_map:
                    # Coerce, date-normalize and regex-complete every item the
                    # reply holds for this entry -- see _extract_entry_items.
                    extraction = _extract_entry_items(
                        entry.get("text", ""), extraction_map[i], entry.get("taxonomy_code", ""))
                    all_extracted.append(_entry_with_extraction(entry, extraction, group_flags))
                else:
                    # No extraction found - mark as failed
                    all_extracted.append({
                        **entry,
                        "extracted_fields": {},
                        "extraction_success": False,
                        "extraction_error": NO_MATCHING_EXTRACTION, **unplaced_flag
                    })

        except (ReadTimeoutError, ConnectTimeoutError):
            logger.exception("Stage 4 extraction LLM call timed out for code %s", code)
            failed_groups += 1
            for entry in code_entries:
                all_extracted.append({
                    **entry,
                    "extracted_fields": {},
                    "extraction_success": False,
                    "extraction_error": LLM_TIMEOUT
                })
        except json.JSONDecodeError:
            logger.exception("Stage 4 extraction response for code %s was not valid JSON", code)
            failed_groups += 1
            for entry in code_entries:
                all_extracted.append({
                    **entry,
                    "extracted_fields": {},
                    "extraction_success": False,
                    "extraction_error": LLM_RESPONSE_INVALID
                })
        except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
            raise
        except Exception:
            logger.exception("Stage 4 extraction failed for code %s", code)
            failed_groups += 1
            for entry in code_entries:
                all_extracted.append({
                    **entry,
                    "extracted_fields": {},
                    "extraction_success": False,
                    "extraction_error": LLM_PROVIDER_ERROR
                })

    # ==========================================================================
    # LLM RECOVERY PASS: Re-process entries with poor extraction coverage
    # ==========================================================================
    entries_needing_recovery = [e for e in all_extracted if needs_llm_recovery(e)]

    if entries_needing_recovery:
        logger.info("LLM Recovery: %d entries with poor extraction coverage", len(entries_needing_recovery))

        # Group by taxonomy code for better context
        recovery_by_code = {}
        for entry in entries_needing_recovery:
            code = entry.get("taxonomy_code", "UNKNOWN")
            if code not in recovery_by_code:
                recovery_by_code[code] = []
            recovery_by_code[code].append(entry)

        # Attempt recovery for each code group
        recovered_entries = {}
        recovery_cost = 0.0
        recovery_tokens = 0

        for code, code_entries in recovery_by_code.items():
            if cancel_check is not None:
                cancel_check()
            logger.info("[%s] Attempting recovery for %d entries...", code, len(code_entries))
            recovery_result = attempt_llm_recovery(code_entries, cancel_check=cancel_check)
            recovery_cost += recovery_result.get("cost", 0.0)
            recovery_tokens += recovery_result.get("tokens", 0)

            # Track recovered entries by their element_idx for replacement
            for rec_entry in recovery_result.get("entries", code_entries):
                key = (rec_entry.get("element_idx_start"), rec_entry.get("element_idx_end"))
                recovered_entries[key] = rec_entry

        # Replace original entries with recovered versions
        final_extracted = []
        for entry in all_extracted:
            key = (entry.get("element_idx_start"), entry.get("element_idx_end"))
            if key in recovered_entries:
                final_extracted.append(recovered_entries[key])
            else:
                final_extracted.append(entry)

        all_extracted = final_extracted
        # Recovery calls are real LLM spend -- fold them into this batch's
        # reported totals so a run's cost/token accounting isn't silently
        # short by whatever the recovery pass spent.
        total_cost += recovery_cost
        total_tokens += recovery_tokens

        # Log recovery summary
        successful_recoveries = sum(1 for e in all_extracted if e.get("llm_recovery_applied"))
        logger.info("Recovery complete: %d/%d entries improved", successful_recoveries, len(entries_needing_recovery))

    return {
        "entries": all_extracted,
        "cost": total_cost,
        "tokens": total_tokens,
        "cache_read_tokens": total_cache_read_tokens,
        "cache_write_tokens": total_cache_write_tokens,
        "success": failed_groups == 0,
        "failed_groups": failed_groups,
    }


def _usage_total_tokens(usage: LlmUsage) -> int:
    """`call_llm`'s own `total_tokens` definition (prompt + completion) over an
    accumulated LlmUsage, so owner-name tokens add into stage 4's total_tokens."""
    return usage.prompt_tokens + usage.completion_tokens


def _extract_and_log_cv_owner_name(
    document_uid: str,
    mapped_entries: list[dict[str, Any]],
    docx_path: str | None,
    usage: LlmUsage,
) -> dict[str, Any]:
    """Call `extract_cv_owner_name` and log the result when a last name was
    found. Pure move out of `extract_fields_from_mapped_entries` (#456-R3
    §3.2a) -- same call, same logging; `usage` collects the call's cost (#1177)."""
    cv_owner_name = extract_cv_owner_name(document_uid, mapped_entries, docx_path=docx_path, usage=usage)
    if cv_owner_name.get('last_name'):
        logger.info(
            "CV Owner: %s (last name: %s)",
            cv_owner_name.get('full_name', cv_owner_name['last_name']),
            cv_owner_name['last_name'],
        )
    return cv_owner_name


def _extract_batches(
    valid_entries: list[dict[str, Any]],
    batch_size: int,
    cv_owner_name: dict[str, str],
    cancel_check: Callable[[], None] | None,
    workers: int,
) -> list[BatchExtractionResult]:
    """One BatchExtractionResult per batch, in batch order -- NOT a flattened
    list of entries (see extract_fields_batch); the caller extends its own
    entry list from each one's "entries" key.

    cancel_check: raises to cancel, never returns True. Checked once before
    each batch here, and forwarded into extract_fields_batch so a cancel can
    also fire between that batch's per-taxonomy-group LLM calls and retries.
    """
    batches = make_batches(valid_entries, batch_size)

    def run_batch(batch_idx: int, batch: list[dict[str, Any]]) -> BatchExtractionResult:
        if cancel_check is not None:
            cancel_check()
        return extract_fields_batch(
            batch, batch_idx, len(batches), cv_owner_name=cv_owner_name, cancel_check=cancel_check,
        )

    return map_in_order(
        run_batch, list(enumerate(batches)), workers=workers,
        # [N/M] is the parsed progress-bar contract (orchestrator PROGRESS_PATTERNS).
        on_result=make_progress_printer(lambda done, _i, _r: [f"[{done}/{len(batches)}] batches extracted"]),
    )


def _split_skippable_entries(
    mapped_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split entries into (valid, skipped) by text length. Pure move out of
    `extract_fields_from_mapped_entries` (function-size ratchet, #651) -- same
    filter, same skipped-entry shape; not a behavior change."""
    # Filter out entries with empty or minimal text
    valid_entries = []
    skipped_entries = []

    for entry in mapped_entries:
        text = entry.get("text", "").strip()
        # Skip entries with empty text or less than 5 characters
        if text and len(text) >= 5:
            valid_entries.append(entry)
        else:
            skipped_entries.append({
                **entry,
                "extracted_fields": {},
                "extraction_success": False,
                "extraction_skipped": True,
                # This branch only runs when `text` is falsy or shorter than
                # the 5-char floor above -- a falsy (empty) string always has
                # len 0, so `len(text) < 5 else "empty_text"` made the
                # "empty_text" arm dead code (every skip landed on
                # "empty_or_minimal_text", including a truly empty string).
                # Branch on emptiness directly so the two reasons are
                # actually distinguishable downstream.
                "skip_reason": "empty_text" if not text else "empty_or_minimal_text"
            })
    return valid_entries, skipped_entries


def extract_fields_from_mapped_entries(
    mapped_entries: list[dict[str, Any]],
    batch_size: int = 10,
    document_uid: str = "",
    cancel_check: Callable[[], None] | None = None,
    docx_path: str | None = None,  # #456 owner-name side channel; None = pre-#456 behavior
    workers: int = STAGE4_BATCH_WORKERS,
) -> ExtractionResult:
    """
    Extract structured fields from all mapped entries.

    Args:
        mapped_entries: List of taxonomy-mapped entries from Stage 3
        batch_size: Number of entries to process per batch (default: 10)
        document_uid: Document identifier for extracting CV owner name
        cancel_check: Raises to abort; never returns True. Checked at the
            top of each batch and forwarded into every per-taxonomy-group
            LLM call and its retries (_extract_batches / extract_fields_batch
            / llm.retry._call_with_retry). None (CLI default) is a no-op.
        workers: Batches in flight at once (default STAGE4_BATCH_WORKERS).
            1 reproduces the pre-#881 serial loop exactly.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    logger.info("=" * 80)
    logger.info("Stage 4: Intra-Entry Field Extraction")
    logger.info("=" * 80)
    logger.info("Total entries: %d", len(mapped_entries))

    # 3b -> 4 boundary (#651): an unrecognized code is quarantined, not defaulted.
    mapped_entries, invalid_codes = quarantine_invalid_taxonomy_codes(mapped_entries)

    # Load and display schema version
    schemas = get_active_schemas()
    logger.info("Field schemas: v%s (%d taxonomy codes)", FIELD_SCHEMA_VERSION, len(schemas))

    # Extract CV owner's name for target_name identification
    owner_name_usage = LlmUsage()
    cv_owner_name = _extract_and_log_cv_owner_name(document_uid, mapped_entries, docx_path, owner_name_usage)

    # Location inference runs *after* extraction -- see the call site below.

    valid_entries, skipped_entries = _split_skippable_entries(mapped_entries)

    logger.info("  - Valid entries (with text): %d", len(valid_entries))
    logger.info("  - Skipped entries (empty/minimal text): %d", len(skipped_entries))

    if not valid_entries:
        logger.warning("No valid entries to process")
        return {
            "entries": skipped_entries,
            "total_cost": owner_name_usage.cost,
            "total_tokens": _usage_total_tokens(owner_name_usage),
            "success": True
        }

    # Process in batches
    num_batches = (len(valid_entries) + batch_size - 1) // batch_size
    all_entries = []
    total_cost = 0.0
    total_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    # Batches where at least one taxonomy group inside them failed outright
    # (see extract_fields_batch's failed_groups). Surfaced in stats below so
    # a degraded run is visible in the artifact rather than reading identical
    # to a clean one.
    failed_batches = 0

    results = _extract_batches(valid_entries, batch_size, cv_owner_name, cancel_check, workers)
    for batch_idx, result in enumerate(results):
        # Always keep whatever entries came back. A batch that had one failed
        # taxonomy group still successfully extracted every other group in it
        # (see extract_fields_batch's per-code error handling), so gating
        # this on an all-or-nothing "success" would silently drop real data
        # for entries that extracted fine.
        all_entries.extend(result.get("entries", []))
        total_cost += result.get("cost", 0.0)
        total_tokens += result.get("tokens", 0)
        total_cache_read_tokens += result.get("cache_read_tokens", 0)
        total_cache_write_tokens += result.get("cache_write_tokens", 0)

        if not result.get("success", True):
            failed_batches += 1
            logger.warning(
                "Stage 4 batch %d/%d had %d failed taxonomy group(s) (%s)",
                batch_idx + 1, num_batches, result.get("failed_groups", 0), document_uid,
            )

        # Show running total after each batch
        logger.info("Batch %d/%d complete | Running total: $%.4f", batch_idx + 1, num_batches, total_cost)

    # Add back skipped entries
    all_entries.extend(skipped_entries)

    # Sort by original order (element_idx) - convert to int in case values are strings
    def safe_int(val, default=9999):
        try:
            return int(val) if val is not None else default
        except (ValueError, TypeError):
            return default
    all_entries.sort(key=lambda e: (safe_int(e.get("element_idx_start")), safe_int(e.get("element_idx_end"))))
    all_entries = date_year_group_members(all_entries, STAGE4_RECORDS_KEY)  # document order (E26)

    # Fallback: Add target_name via regex matching if LLM didn't extract it
    cv_owner_last_name = cv_owner_name.get('last_name', '')
    if cv_owner_last_name:
        all_entries = add_target_names(all_entries, cv_owner_last_name)
        pub_with_target = sum(1 for e in all_entries if e.get('extracted_fields', {}).get('target_name'))
        if pub_with_target > 0:
            logger.info("target_name identified in %d publication/presentation entries", pub_with_target)

    # Count entries with reformatted fields
    reformatted_count = sum(1 for e in all_entries if e.get('reformatted_fields'))
    if reformatted_count > 0:
        logger.info("Applied reformatting to %d entries", reformatted_count)

    # Infer CV owner's current location(s) for geographic scope classification.
    # This runs after extraction, not before it: the affiliation fallback reads
    # named fields (employer/institution/organization/address), and stage 3b
    # emits no extracted_fields at all -- every entry arrives here with the key
    # absent. Inferring before extraction made that fallback dead code in every
    # real run while still looking correct when replayed over a saved
    # *_fields.json. Nothing in the extraction loop consumes the result; it is
    # carried in the return value for downstream geographic-scope use.
    cv_owner_location = infer_cv_owner_location(all_entries)
    if cv_owner_location.get('inference_success'):
        metro = cv_owner_location.get('metro_area', '')
        primary = cv_owner_location.get('primary_location', {})
        if primary:
            loc_str = f"{primary.get('institution', '')} in {primary.get('city', '')}, {primary.get('state', '')}"
            logger.info("CV Location: %s (metro: %s)", loc_str, metro)
            if cv_owner_location.get('cost'):
                logger.info("Location inference cost: $%.4f", cv_owner_location['cost'])
    else:
        cv_owner_location = None  # Set to None if inference failed

    # Include location inference cost in total
    location_cost = cv_owner_location.get('cost', 0) if cv_owner_location else 0
    location_tokens = cv_owner_location.get('tokens', 0) if cv_owner_location else 0

    # Observability: "extracted" previously counted entries ATTEMPTED
    # (len(valid_entries)) regardless of whether the LLM actually returned a
    # match for them, so a run where every batch failed still reported the
    # same "extracted" count as one where nothing failed. Count real outcomes
    # instead, and keep the attempted count under its own key.
    extracted_ok = sum(
        1 for e in all_entries
        if e.get("extraction_success") and not e.get("extraction_skipped")
    )
    extraction_failed = sum(
        1 for e in all_entries
        if not e.get("extraction_success") and not e.get("extraction_skipped")
    )

    return {
        "entries": all_entries,
        "cv_owner": cv_owner_name,  # Include CV owner info in output
        "cv_owner_location": cv_owner_location,  # Include location context for geographic scope
        "total_cost": total_cost + location_cost + owner_name_usage.cost,
        "total_tokens": total_tokens + location_tokens + _usage_total_tokens(owner_name_usage),
        "cache_read_tokens": total_cache_read_tokens,
        "cache_write_tokens": total_cache_write_tokens,
        "stats": {
            "total_entries": len(all_entries),
            "attempted": len(valid_entries),
            "extracted": extracted_ok,
            "extraction_failed": extraction_failed,
            "skipped": len(skipped_entries),
            "batches_processed": num_batches,
            "failed_batches": failed_batches,
            "had_extraction_errors": failed_batches > 0,
            "entries_reformatted": reformatted_count,
            "cache_read_tokens": total_cache_read_tokens,
            "cache_write_tokens": total_cache_write_tokens,
            "invalid_code_entries": sum(invalid_codes.values()),
            "invalid_taxonomy_codes": invalid_codes,
        },
        "success": True,
        "partial_success": failed_batches > 0,
    }
