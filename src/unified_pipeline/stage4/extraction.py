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
from typing import Dict, List, Any, Optional, Callable

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


def calculate_unextracted_content(original_text: str, extracted_fields: Dict[str, Any]) -> Dict[str, Any]:
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

def needs_llm_recovery(entry: Dict[str, Any], min_original_chars: int = 200, max_coverage: float = 30.0) -> bool:
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

def attempt_llm_recovery(
    entries: List[Dict[str, Any]],
    model: str = None
) -> List[Dict[str, Any]]:
    """
    Attempt LLM-assisted recovery for entries with poor extraction coverage.

    Uses taxonomy context to help the LLM understand the expected data structure
    and parse messy table-like content.

    Args:
        entries: List of entries needing recovery (same taxonomy code)
        model: Unused -- the model is resolved from llm_config.yaml, not this argument

    Returns:
        List of entries with recovered fields
    """
    if not entries:
        return entries

    # All entries should have the same taxonomy code
    taxonomy_code = entries[0].get("taxonomy_code", "UNKNOWN")
    taxonomy_label = get_taxonomy_label(taxonomy_code)
    schema = get_field_schema(taxonomy_code)

    # Combine all entry texts for context
    combined_text = "\n---ENTRY BOUNDARY---\n".join(
        entry.get("text", "") for entry in entries
    )

    # Build recovery prompt with taxonomy context
    prompt = f"""You are parsing a poorly formatted CV section. The text appears to have table-like structure where items and their attributes (like dates) may be misaligned or separated.

**Section Type**: {taxonomy_code} - {taxonomy_label}

**Expected Fields**: {', '.join(schema['fields'])}

**Field Descriptions**:
{_get_field_descriptions(taxonomy_code)}

**Raw Text to Parse**:
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

**Return JSON**:
{{
  "recovered_entries": [
    {{
      "original_text_snippet": "first 50 chars of the entry text",
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
        result = json.loads(result_text)

        recovered = result.get("recovered_entries", [])
        recovery_notes = result.get("recovery_notes", "")

        cost = llm_result["cost"]

        print(f"    LLM Recovery: {len(recovered)} entries recovered | ${cost:.4f}")
        if recovery_notes:
            print(f"       Notes: {recovery_notes[:100]}...")

        # Match recovered entries back to original entries
        # This is approximate - we try to match by text snippet
        recovered_entries = []
        for entry in entries:
            entry_text = entry.get("text", "")
            matched_recovery = None

            # Try to find a matching recovery by text snippet
            for rec in recovered:
                snippet = rec.get("original_text_snippet", "")
                if snippet and snippet.lower() in entry_text.lower()[:100]:
                    matched_recovery = rec
                    break

            if matched_recovery:
                # Apply recovered fields
                recovered_fields = matched_recovery.get("fields", {})
                # Coerce off-type LLM values before regex/downstream consumers
                recovered_fields = coerce_field_value_types(recovered_fields)
                # Normalize dates
                recovered_fields = normalize_dates(recovered_fields)
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

        return recovered_entries

    except Exception as e:
        print(f"    ⚠ LLM Recovery failed: {e}")
        # Return original entries unchanged
        return [{**entry, "llm_recovery_error": str(e)} for entry in entries]

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

def build_extraction_prompt(entry: Dict[str, Any], schema: Dict[str, Any]) -> str:
    """
    Build LLM prompt for field extraction.
    """
    taxonomy_code = entry.get("taxonomy_code", "UNKNOWN")
    taxonomy_label = entry.get("taxonomy_label", "Unknown")
    text = entry.get("text", "")

    fields = schema["fields"]
    required = schema.get("required", [])

    prompt = f"""Extract structured fields from this CV entry.

**Entry Classification**: {taxonomy_code} - {taxonomy_label}

**Entry Text**:
{text}

**Fields to Extract**:
{', '.join(fields)}

**Required Fields** (must extract if present):
{', '.join(required)}

**Instructions**:
1. Extract all available fields from the text
2. Use null for fields not found
3. For dates: use YYYY-MM-DD format when possible, or YYYY if only year available
4. For authors: extract as a single string (e.g., "Smith J, Doe A, et al.")
5. Be precise - only extract what is explicitly stated
6. Do not infer or guess missing information

Return JSON with the extracted fields."""

    return prompt

def extract_fields_batch(
    entries: List[Dict[str, Any]],
    batch_idx: int,
    total_batches: int,
    model: str = None,
    cv_owner_name: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Extract fields from a batch of entries using LLM.

    Args:
        entries: List of entries to process
        batch_idx: Current batch index
        total_batches: Total number of batches
        model: Unused -- the model is resolved from llm_config.yaml, not this argument
        cv_owner_name: Dict with 'last_name' and optionally 'full_name' of CV owner
    """
    print(f"  Processing batch {batch_idx + 1}/{total_batches} ({len(entries)} entries)...")

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

    for code, code_entries in entries_by_code.items():
        schema = get_field_schema(code)

        # Build batch prompt
        batch_items = []
        for i, entry in enumerate(code_entries):
            item = {
                "entry_index": i,
                "text": entry.get("text", ""),
                "element_idx_start": entry.get("element_idx_start"),
                "element_idx_end": entry.get("element_idx_end")
            }
            batch_items.append(item)

        # Get label for this taxonomy code
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
        for item in batch_items:
            prompt += f"\n[Entry {item['entry_index']}]:\n{item['text']}\n"

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
            print(f"    [{code}] {len(code_entries)} entries | {llm_result['total_tokens']:,} tokens | ${cost:.4f}")

            # Merge extracted fields back with entries
            # CRITICAL: Use entry_index from LLM response to match correctly
            extractions = result.get("entries", result.get("extractions", []))

            # Create index map for safe merging
            extraction_map = {}
            for extracted in extractions:
                entry_idx = extracted.get("entry_index")
                if entry_idx is not None:
                    extraction_map[entry_idx] = extracted

            # Merge using explicit indices to avoid mismapping
            for i, entry in enumerate(code_entries):
                if i in extraction_map:
                    # Remove entry_index from extracted fields (it's just for matching)
                    extracted_fields = {k: v for k, v in extraction_map[i].items()
                                       if k != "entry_index"}

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

        except Exception as e:
            print(f"    ⚠ Error extracting fields for code {code}: {e}")
            # Fallback: mark as failed
            for entry in code_entries:
                all_extracted.append({
                    **entry,
                    "extracted_fields": {},
                    "extraction_success": False,
                    "extraction_error": str(e)
                })

    # ==========================================================================
    # LLM RECOVERY PASS: Re-process entries with poor extraction coverage
    # ==========================================================================
    entries_needing_recovery = [e for e in all_extracted if needs_llm_recovery(e)]

    if entries_needing_recovery:
        print(f"\n  🔧 LLM Recovery: {len(entries_needing_recovery)} entries with poor extraction coverage")

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

        for code, code_entries in recovery_by_code.items():
            print(f"    [{code}] Attempting recovery for {len(code_entries)} entries...")
            recovered = attempt_llm_recovery(code_entries, model=model)

            # Track recovered entries by their element_idx for replacement
            for rec_entry in recovered:
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

        # Log recovery summary
        successful_recoveries = sum(1 for e in all_extracted if e.get("llm_recovery_applied"))
        print(f"  ✓ Recovery complete: {successful_recoveries}/{len(entries_needing_recovery)} entries improved")

    return {
        "entries": all_extracted,
        "cost": total_cost,
        "tokens": total_tokens,
        "cache_read_tokens": total_cache_read_tokens,
        "cache_write_tokens": total_cache_write_tokens,
        "success": True
    }

def extract_fields_from_mapped_entries(
    mapped_entries: List[Dict[str, Any]],
    batch_size: int = 10,
    model: str = None,
    document_uid: str = "",
    cancel_check: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    Extract structured fields from all mapped entries.

    Args:
        mapped_entries: List of taxonomy-mapped entries from Stage 3
        batch_size: Number of entries to process per batch (default: 10)
        model: Unused -- the model is resolved from llm_config.yaml, not this argument
        document_uid: Document identifier for extracting CV owner name
        cancel_check: Optional zero-arg callable invoked at the top of each
            batch iteration. It should raise to abort the run (the web
            orchestrator passes its check_cancelled). None (the standalone CLI
            default) is a no-op.
    """
    print(f"\n{'='*80}")
    print("Stage 4: Intra-Entry Field Extraction")
    print(f"{'='*80}")
    print(f"Total entries: {len(mapped_entries)}")

    # Load and display schema version
    schemas = get_active_schemas()
    print(f"Field schemas: v{FIELD_SCHEMA_VERSION} ({len(schemas)} taxonomy codes)")

    # Extract CV owner's name for target_name identification
    cv_owner_name = extract_cv_owner_name(document_uid, mapped_entries)
    if cv_owner_name.get('last_name'):
        print(f"CV Owner: {cv_owner_name.get('full_name', cv_owner_name['last_name'])} (last name: {cv_owner_name['last_name']})")

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

    print(f"  - Valid entries (with text): {len(valid_entries)}")
    print(f"  - Skipped entries (empty/minimal text): {len(skipped_entries)}")

    if not valid_entries:
        print("\n⚠ No valid entries to process")
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

    for batch_idx in range(num_batches):
        # Check for cancellation before each batch's LLM calls so an aborted
        # run terminates promptly rather than running every batch to completion.
        if cancel_check is not None:
            cancel_check()

        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(valid_entries))
        batch = valid_entries[start_idx:end_idx]

        result = extract_fields_batch(batch, batch_idx, num_batches, model=model, cv_owner_name=cv_owner_name)

        if result.get("success"):
            all_entries.extend(result.get("entries", []))
            total_cost += result.get("cost", 0.0)
            total_tokens += result.get("tokens", 0)
            total_cache_read_tokens += result.get("cache_read_tokens", 0)
            total_cache_write_tokens += result.get("cache_write_tokens", 0)
            # Show running total after each batch
            print(f"  Batch {batch_idx + 1}/{num_batches} complete | Running total: ${total_cost:.4f}")

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
            print(f"\n✓ target_name identified in {pub_with_target} publication/presentation entries")

    # Count entries with reformatted fields
    reformatted_count = sum(1 for e in all_entries if e.get('reformatted_fields'))
    if reformatted_count > 0:
        print(f"✓ Applied reformatting to {reformatted_count} entries")

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
            print(f"CV Location: {loc_str} (metro: {metro})")
            if cv_owner_location.get('cost'):
                print(f"  Location inference cost: ${cv_owner_location['cost']:.4f}")
    else:
        cv_owner_location = None  # Set to None if inference failed

    # Include location inference cost in total
    location_cost = cv_owner_location.get('cost', 0) if cv_owner_location else 0
    location_tokens = cv_owner_location.get('tokens', 0) if cv_owner_location else 0

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
            "extracted": len(valid_entries),
            "skipped": len(skipped_entries),
            "batches_processed": num_batches,
            "entries_reformatted": reformatted_count,
            "cache_read_tokens": total_cache_read_tokens,
            "cache_write_tokens": total_cache_write_tokens
        },
        "success": True
    }
