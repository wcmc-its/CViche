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
from typing import Any, Callable, NotRequired, TypedDict

from openai import APITimeoutError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from unified_pipeline.llm_client import call_llm

from unified_pipeline.stage4.coercion import (
    apply_regex_post_processing,
    coerce_field_value_types,
    normalize_dates,
)
from unified_pipeline.stage4.owner_name import (
    add_target_names,
    extract_cv_owner_name,
    infer_cv_owner_location,
)
from unified_pipeline.stage4.schemas import (
    FIELD_DESCRIPTIONS,
    FIELD_SCHEMA_VERSION,
    get_active_schemas,
    get_field_schema,
    get_taxonomy_label,
)

logger = logging.getLogger(__name__)

# Stable error-code strings for extraction_error / llm_recovery_error fields.
# A caller can branch on these programmatically; str(exception) is for the
# log line only (via logger.exception, which records the full traceback),
# never for a field another stage or the frontend reads.
LLM_RESPONSE_INVALID = "llm_response_invalid"
LLM_TIMEOUT = "llm_timeout"
LLM_PROVIDER_ERROR = "llm_provider_error"


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
) -> dict[str, Any]:
    """
    Attempt LLM-assisted recovery for entries with poor extraction coverage.

    Uses taxonomy context to help the LLM understand the expected data structure
    and parse messy table-like content.

    Args:
        entries: List of entries needing recovery (same taxonomy code)

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
            response_format={"type": "json_object"}
        )

        result_text = llm_result["content"]
        parsed = json.loads(result_text)
        validated = _RecoveryResponse.model_validate(parsed)

        cost = llm_result["cost"]
        tokens = llm_result.get("total_tokens", 0)

        logger.info("LLM Recovery: %d entries recovered | $%.4f", len(validated.recovered_entries), cost)
        if validated.recovery_notes:
            logger.info("Recovery notes: %s", validated.recovery_notes[:100])

        # Match recovered entries back to original entries by exact id.
        recovered_by_id = {rec.entry_id: rec for rec in validated.recovered_entries}

        recovered_entries = []
        for entry in entries:
            matched = recovered_by_id.get(_recovery_entry_id(entry))

            if matched:
                # Coerce off-type LLM values before regex/downstream consumers.
                # Declared `Any`, not `ExtractedFields`: this is the untyped
                # side of the bridge -- `matched.fields` is unvalidated
                # recovery-LLM JSON, and coerce/normalize below rebuild the
                # dict through variable keys, which no TypedDict can express.
                # `apply_regex_post_processing` is where the shape is named
                # (its signature returns `ExtractedFields`).
                recovered_fields: Any = coerce_field_value_types(dict(matched.fields))
                # Normalize dates
                recovered_fields = normalize_dates(recovered_fields)
                entry_text = entry.get("text", "")
                # Apply regex post-processing
                recovered_fields, reformatted = apply_regex_post_processing(
                    entry_text, recovered_fields, taxonomy_code
                )
                # Recalculate coverage
                new_coverage = calculate_unextracted_content(entry_text, recovered_fields)

                entry_result = {
                    **entry,
                    "extracted_fields": recovered_fields,
                    "extraction_success": True,
                    "extraction_coverage": new_coverage,
                    "llm_recovery_applied": True
                }
                if reformatted:
                    entry_result["reformatted_fields"] = reformatted
                recovered_entries.append(entry_result)
            else:
                # No matching recovery - keep original
                recovered_entries.append({
                    **entry,
                    "llm_recovery_attempted": True,
                    "llm_recovery_matched": False
                })

        return {"entries": recovered_entries, "cost": cost, "tokens": tokens}

    except APITimeoutError:
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
            and presentations (codes starting with "S", and "R")
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
        prompt += f"\n[Entry {i}]:\n{entry.get('text', '')}\n"

    # Add code-specific instructions
    code_specific_instructions = ""
    if code.startswith('M2'):
        code_specific_instructions = """
9. **GRANTS (M2A/M2B/M2C)**:
   - pi_name = a PERSON'S NAME (e.g., "Susan Bostwick", "John Smith") - NOT the project title
   - title = the scientific project title - NOT a person's name, NOT FTE information
   - percent_effort = extract FTE as percentage (e.g., ".08FTE" → "8%", "0.1 FTE" → "10%")
   - Do NOT put the project title in pi_name field
   - If no PI name is found, leave pi_name as null"""
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
   - Do NOT put the committee name in the role field or vice versa"""
    elif code == 'P':
        code_specific_instructions = """
9. **INSTITUTIONAL COMMITTEES (P)** - CRITICAL field separation:
   - committee_name = the committee/body name ONLY (e.g., "Quality Improvement Committee")
   - role = ONLY the role word(s) (e.g., "Chair", "Member") - NOT the committee name
   - Do NOT merge role into committee_name or vice versa"""

    prompt += f"""
**Instructions**:
1. For each entry, extract all available fields
2. Use null for fields not found
3. Dates:
   - For single dates: use YYYY-MM-DD or YYYY format
   - For date ranges (e.g., "2005-2008"): use start_date and end_date fields
   - For ongoing dates: preserve "present", "ongoing", or "current" exactly as written (do NOT convert to a year)
4. Authors: single string (e.g., "Smith J, Doe A")
5. Emails: extract multiple emails separately (primary_email, secondary_email, institutional_email, personal_email)
6. Tab-separated values: If text contains tabs (\\t) or pipe characters (|), these indicate table columns - extract each column as a separate field value, not as merged text
7. Only extract explicitly stated information - do not infer or guess
8. CRITICAL: Include "entry_index" field in each extraction to match the entry number above{target_name_instruction}{code_specific_instructions}

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

def extract_fields_batch(
    entries: list[dict[str, Any]],
    batch_idx: int,
    total_batches: int,
    cv_owner_name: dict[str, str] | None = None
) -> BatchExtractionResult:
    """
    Extract fields from a batch of entries using LLM.

    Args:
        entries: List of entries to process
        batch_idx: Current batch index
        total_batches: Total number of batches
        cv_owner_name: Dict with 'last_name' and optionally 'full_name' of CV owner
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
                response_format={"type": "json_object"}
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

            # Validate each item at the external trust boundary -- raw LLM
            # JSON -- before it is merged into pipeline state. A malformed
            # item (missing/non-int entry_index, not an object) is dropped
            # with a warning rather than crashing the whole batch or being
            # merged in with an unvalidated shape.
            extraction_map: dict[int, dict[str, Any]] = {}
            for raw_item in raw_extractions:
                try:
                    validated_item = _ExtractedEntryFields.model_validate(raw_item)
                except ValidationError as exc:
                    logger.warning(
                        "Stage 4 batch extraction for %s dropped one malformed LLM entry: %s",
                        code, exc,
                    )
                    continue
                extraction_map[validated_item.entry_index] = validated_item.model_dump(exclude={"entry_index"})

            # Merge using explicit indices to avoid mismapping
            for i, entry in enumerate(code_entries):
                if i in extraction_map:
                    extracted_fields: Any = extraction_map[i]  # untyped LLM JSON -- see attempt_llm_recovery

                    # Coerce off-type LLM values (e.g. list-valued strings) before
                    # any string/number consumer (regex post-processing, downstream
                    # stages) touches them -- see coerce_field_value_types().
                    extracted_fields = coerce_field_value_types(extracted_fields)

                    # Apply date normalization to split ranges into start_date/end_date
                    extracted_fields = normalize_dates(extracted_fields)

                    # Apply regex post-processing and track reformatted values
                    original_text = entry.get("text", "")
                    taxonomy_code = entry.get("taxonomy_code", "")
                    extracted_fields, reformatted_fields = apply_regex_post_processing(
                        original_text, extracted_fields, taxonomy_code
                    )

                    # Calculate unextracted content for quality assurance
                    unextracted_info = calculate_unextracted_content(original_text, extracted_fields)

                    entry_result = {
                        **entry,
                        "extracted_fields": extracted_fields,
                        "extraction_success": True,
                        "extraction_coverage": unextracted_info
                    }

                    # Add reformatted_fields if any reformatting occurred
                    if reformatted_fields:
                        entry_result["reformatted_fields"] = reformatted_fields

                    all_extracted.append(entry_result)
                else:
                    # No extraction found - mark as failed
                    all_extracted.append({
                        **entry,
                        "extracted_fields": {},
                        "extraction_success": False,
                        "extraction_error": "No matching extraction in LLM response"
                    })

        except APITimeoutError:
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
            logger.info("[%s] Attempting recovery for %d entries...", code, len(code_entries))
            recovery_result = attempt_llm_recovery(code_entries)
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


def _extract_and_log_cv_owner_name(
    document_uid: str,
    mapped_entries: list[dict[str, Any]],
    docx_path: str | None,
) -> dict[str, Any]:
    """Call `extract_cv_owner_name` and log the result when a last name was
    found. Pure move out of `extract_fields_from_mapped_entries` (#456-R3
    §3.2a) -- same call, same kwargs, same logging."""
    cv_owner_name = extract_cv_owner_name(document_uid, mapped_entries, docx_path=docx_path)
    if cv_owner_name.get('last_name'):
        logger.info(
            "CV Owner: %s (last name: %s)",
            cv_owner_name.get('full_name', cv_owner_name['last_name']),
            cv_owner_name['last_name'],
        )
    return cv_owner_name


def extract_fields_from_mapped_entries(
    mapped_entries: list[dict[str, Any]],
    batch_size: int = 10,
    document_uid: str = "",
    cancel_check: Callable[[], None] | None = None,
    docx_path: str | None = None,  # #456 owner-name side channel; None = pre-#456 behavior
) -> ExtractionResult:
    """
    Extract structured fields from all mapped entries.

    Args:
        mapped_entries: List of taxonomy-mapped entries from Stage 3
        batch_size: Number of entries to process per batch (default: 10)
        document_uid: Document identifier for extracting CV owner name
        cancel_check: Optional zero-arg callable invoked at the top of each
            batch iteration. It should raise to abort the run (the web
            orchestrator passes its check_cancelled). None (the standalone CLI
            default) is a no-op.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    logger.info("=" * 80)
    logger.info("Stage 4: Intra-Entry Field Extraction")
    logger.info("=" * 80)
    logger.info("Total entries: %d", len(mapped_entries))

    # Load and display schema version
    schemas = get_active_schemas()
    logger.info("Field schemas: v%s (%d taxonomy codes)", FIELD_SCHEMA_VERSION, len(schemas))

    # Extract CV owner's name for target_name identification
    cv_owner_name = _extract_and_log_cv_owner_name(document_uid, mapped_entries, docx_path)

    # Location inference runs *after* extraction -- see the call site below.

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
                "skip_reason": "empty_or_minimal_text" if len(text) < 5 else "empty_text"
            })

    logger.info("  - Valid entries (with text): %d", len(valid_entries))
    logger.info("  - Skipped entries (empty/minimal text): %d", len(skipped_entries))

    if not valid_entries:
        logger.warning("No valid entries to process")
        return {
            "entries": skipped_entries,
            "total_cost": 0.0,
            "total_tokens": 0,
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

    for batch_idx in range(num_batches):
        # Check for cancellation before each batch's LLM calls so an aborted
        # run terminates promptly rather than running every batch to completion.
        if cancel_check is not None:
            cancel_check()

        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(valid_entries))
        batch = valid_entries[start_idx:end_idx]

        result = extract_fields_batch(batch, batch_idx, num_batches, cv_owner_name=cv_owner_name)

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
        "total_cost": total_cost + location_cost,
        "total_tokens": total_tokens + location_tokens,
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
            "cache_write_tokens": total_cache_write_tokens
        },
        "success": True,
        "partial_success": failed_batches > 0,
    }
