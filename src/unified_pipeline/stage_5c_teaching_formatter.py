#!/usr/bin/env python3
"""
Stage 5c: Teaching/Educational Contributions Formatter

Uses an LLM to reformat K-code entries (Educational Contributions) into a
polished, highly readable format with consistent styling across entries.

Applies to:
- K1: Didactic teaching
- K2: Clinical teaching
- K3: Administrative teaching
- K4: Continuing education / professional development
- K5: Other education/outreach activities

Input: Stage 5b enriched JSON (or earlier stage output)
Output: *_teaching_formatted.json with reformatted K entries

Author: Scholar Signals CV Pipeline
Date: 2025-12-02
"""

import os
import sys
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from unified_pipeline.llm_client import call_llm

# Paths
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5c_teaching_formatted"

# Taxonomy codes for teaching/educational contributions
TEACHING_CODES = ['K1', 'K2', 'K3', 'K4', 'K5']

# Subsection labels in WCM template
K_SUBSECTION_LABELS = {
    'K1': 'Didactic teaching (lectures, seminars, tutorials)',
    'K2': 'Clinical teaching (bedside teaching, teaching rounds, precepting)',
    'K3': 'Administrative teaching (leadership role as director, course director)',
    'K4': 'Continuing education and professional education',
    'K5': 'Other education/outreach activities',
}

# LLM prompt for reformatting educational contributions
EDUCATIONAL_CONTRIBUTIONS_PROMPT = '''You are a CV formatter. The input is raw, inconsistently formatted text that has ALREADY been mapped into subsections of a CV section called "Educational Contributions." Your job is to rewrite it into a polished, highly readable format.

NON-NEGOTIABLES
1) Output ONLY the formatted Markdown for this section. No commentary, no analysis.
2) Preserve meaning; DO NOT add facts or infer missing details.
3) Preserve EVERY entry ID EXACTLY as given (character-for-character). Never delete, reorder IDs, or generate new IDs.
4) These are NOT citations. Do not label anything as citations and do not add citation formatting.

SECTION FRAME
- Use this exact top-level heading:
  # Educational Contributions
- Keep the provided subsection headings exactly as given in the input and in the same order.

PRIMARY GOAL
Maximize readability while maintaining strong consistency across entries.

CORE STRATEGY (important)
- Choose ONE "dominant" entry style per subsection to maximize consistency.
- Only fall back to an alternate style when an entry lacks the minimum fields to fit the dominant style.
- Within a subsection, keep punctuation, ordering of fields, and bold usage consistent.

ENTRY ID RULE (hard)
- Each entry must begin with its ID, formatted like:
  - `[ID] ` followed immediately by the entry content on the same line.
- The ID must be the first token on the bullet line.
- Example: `- [EC-0123] **2021-2022** - Faculty Advisor, SPRINGBOARD Program (Health Sciences)`

FIELD EXTRACTION (use only what is present)
From each entry, extract any of the following if present:
- Dates (range or single)
- Activity/Course/Program/Event title
- Role (e.g., Lecturer, Mentor, Preceptor, Co-Director, Facilitator, Faculty Advisor, Attendee, Participant)
- Audience (e.g., graduate students, 1st-year SOM, MD/PhD)
- Institution/Unit (e.g., EOH Graduate Program, GSPH, SOM)
- Quantities (hours, credits, number of sessions, number of students)
- Notes (e.g., article discussed, workshop topic)
If a field is missing, omit it. Never guess.

PUNCTUATION + TYPOGRAPHY RULES
- Prefer en dashes/em dashes for separation, in this hierarchy:
  1) Dates first, then em dash "-" to main content
  2) Use commas for compact lists inside a clause
  3) Use parentheses for secondary clarifiers (audience, institution, counts, notes)
- Bold:
  - Bold dates when present.
  - Bold true leadership roles only when they are the main point (e.g., **Co-Director**, **Director**).
- Quotes:
  - Use quotes ONLY for titles of articles/talks/session titles when explicitly present.
  - Do not introduce quotes if none exist.
- Line breaks:
  - Keep each entry to ONE line when possible.
  - If an entry is long (e.g., contains an article title + journal + year + multiple roles), use a second line with an indented sub-bullet for "Notes:" or "Article discussed:" rather than cramming.

SUB-BULLETS (allowed, but controlled)
Use sub-bullets only for:
- Very long "article discussed" details
- Multiple structured quantities (hours/credits/sessions/students)
- Lists of co-directors
When used:
- Keep the parent line readable and short.
- Use at most 2 sub-bullets per entry.

ALLOWED ENTRY TEMPLATES (choose per subsection)
Pick a dominant template PER subsection:

Template A (Dates available + teaching role)
- [ID] **[Dates]** - [Activity/Course/Program], [Role] ([Institution/Unit]; [Audience])

Template B (No dates, but role exists)
- [ID] [Activity/Course/Program] - [Role] ([Institution/Unit]; [Audience])

Template C (Administrative teaching with metrics)
- [ID] **[Dates]** - **[Leadership Role]**, [Course/Program]: [Title]
  - (Hours: [x]; Credits: [x]; Sessions: [x]; Learners: [x]; Audience: [x])
  - (Co-leads: [names])

Template D (Continuing education / professional development)
- [ID] **[Date]** - [Role: Attendee/Participant], [Event/Topic] ([Org/Location])

CONSISTENCY RULES (readability-first)
- Within each subsection:
  - Use the same ordering of parentheses content (Institution first, then Audience, then Notes).
  - Use the same separator style (prefer "-" between major parts).
  - Standardize role capitalization (Title Case).
  - Remove duplicated institution fragments while preserving the correct institution once.
  - Fix obvious typos (spelling/punctuation), but do not rewrite technical titles or proper nouns.

ORDERING
- Keep entries in the SAME order as provided unless the input explicitly provides dates for most entries in that subsection.
- If >=70% of entries in a subsection have dates, reorder that subsection reverse-chronologically by date.
- Otherwise, preserve original order.

INPUT FORMAT ASSUMPTIONS
The raw input will be provided with explicit subsection labels and each entry will include an entry ID token.
Do not invent subsection labels or entry IDs.

Now format the following raw content:

<<<RAW_CV
{raw_content}
RAW_CV>>>'''


def build_raw_content(entries_by_k_code: dict[str, list[dict]]) -> tuple[str, dict[str, dict]]:
    """
    Build raw content string for LLM prompt and a mapping of entry IDs to entries.

    Returns:
        Tuple of (raw_content_string, id_to_entry_mapping)
    """
    lines = []
    id_to_entry = {}
    entry_counter = 0

    for k_code in TEACHING_CODES:
        entries = entries_by_k_code.get(k_code, [])
        if not entries:
            continue

        # Add subsection header
        subsection_label = K_SUBSECTION_LABELS.get(k_code, k_code)
        lines.append(f"\n## {subsection_label}\n")

        for entry in entries:
            entry_counter += 1
            entry_id = f"EC-{entry_counter:04d}"

            # Store mapping
            id_to_entry[entry_id] = entry

            # Build raw text from entry
            fields = entry.get('extracted_fields', {}) or {}
            raw_text = entry.get('text', '')

            # Include structured field info if available
            parts = []

            # Dates
            start_date = fields.get('start_date', '')
            end_date = fields.get('end_date', '')
            if start_date or end_date:
                date_str = f"{start_date or ''}-{end_date or ''}".strip('-')
                if date_str:
                    parts.append(date_str)

            # Course info
            course_code = fields.get('course_code', '')
            course_title = fields.get('course_title', '')
            if course_code and course_title:
                parts.append(f"{course_code}: {course_title}")
            elif course_title:
                parts.append(course_title)
            elif course_code:
                parts.append(course_code)

            # Role
            role = fields.get('role', '')
            if role:
                parts.append(role)

            # Institution
            institution = fields.get('institution', '')
            if institution:
                parts.append(institution)

            # Audience
            audience = fields.get('audience', '')
            if audience:
                parts.append(audience)

            # Fall back to raw text if no structured fields
            if not parts:
                entry_text = raw_text
            else:
                entry_text = ' | '.join(parts)
                # Include original text as context if different
                if raw_text and raw_text.strip() != entry_text.strip():
                    entry_text = f"{entry_text}\n  Original: {raw_text}"

            lines.append(f"[{entry_id}] {entry_text}")

    return '\n'.join(lines), id_to_entry


def parse_llm_output(llm_output: str, id_to_entry: dict[str, dict]) -> dict[str, str]:
    """
    Parse LLM formatted output and extract formatted text per entry ID.

    Returns:
        Dict mapping entry_id -> formatted_text
    """
    id_to_formatted = {}

    # Find all entries with their IDs
    # Pattern: - [EC-XXXX] formatted content (possibly multi-line with sub-bullets)
    pattern = r'-\s*\[([A-Z]+-\d+)\]\s*(.+?)(?=\n-\s*\[|\n##|\n#|\Z)'

    matches = re.findall(pattern, llm_output, re.DOTALL)

    for entry_id, content in matches:
        # Clean up the content
        content = content.strip()
        # Handle sub-bullets (preserve them as part of the content)
        id_to_formatted[entry_id] = content

    return id_to_formatted


def call_llm_formatter(raw_content: str, verbose: bool = True) -> tuple:
    """
    Call LLM to reformat the educational contributions.

    Args:
        raw_content: Raw content string with entry IDs
        verbose: Whether to print progress

    Returns:
        Tuple of (formatted_text, usage_dict) or (None, None) if failed
    """
    try:
        prompt = EDUCATIONAL_CONTRIBUTIONS_PROMPT.format(raw_content=raw_content)
        messages = [{"role": "user", "content": prompt}]

        if verbose:
            print(f"  Calling LLM for educational contributions formatting...")

        llm_result = call_llm(
            stage="stage_5c",
            messages=messages,
            temperature=0.3,
            max_tokens=8000
        )

        result_text = llm_result["content"]

        usage = {
            'prompt_tokens': llm_result["prompt_tokens"],
            'completion_tokens': llm_result["completion_tokens"],
            'total_tokens': llm_result["total_tokens"],
            'cache_read_tokens': llm_result.get("cache_read_tokens", 0),
            'cache_write_tokens': llm_result.get("cache_write_tokens", 0),
            'cost': llm_result.get("cost", 0.0),
            # What actually served the call (#459).
            'model': llm_result.get("model"),
        }

        return result_text, usage

    except Exception as e:
        if verbose:
            print(f"  Warning: LLM formatting failed: {e}")
        return None, None


def run_stage_5c(input_path: str, output_path: str = None, model: str = "gpt-4o-mini",
                 verbose: bool = True) -> str:
    """
    Run Stage 5c: Teaching/Educational Contributions Formatter.

    Args:
        input_path: Path to input JSON (Stage 5b or earlier)
        output_path: Optional output path
        model: OpenAI model for formatting
        verbose: Whether to print progress

    Returns:
        Path to output file
    """
    # Load input data
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    document_uid = data.get('document_uid', 'unknown')
    entries = data.get('entries', [])

    if verbose:
        print(f"\n{'='*60}")
        print(f"Stage 5c: Teaching/Educational Contributions Formatter")
        print(f"{'='*60}")
        print(f"Document: {document_uid}")

    # Group entries by K-code
    entries_by_k_code: dict[str, list[dict]] = {}
    for entry in entries:
        code = entry.get('taxonomy_code', '')
        if code in TEACHING_CODES:
            if code not in entries_by_k_code:
                entries_by_k_code[code] = []
            entries_by_k_code[code].append(entry)

    total_k_entries = sum(len(v) for v in entries_by_k_code.values())

    if total_k_entries == 0:
        if verbose:
            print("  No K-code entries found, nothing to format")
        # Just copy input to output
        if not output_path:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / f"{document_uid}_teaching_formatted.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return str(output_path)

    if verbose:
        print(f"  Found {total_k_entries} K-code entries across {len(entries_by_k_code)} subsections")
        for k_code, k_entries in entries_by_k_code.items():
            print(f"    {k_code}: {len(k_entries)} entries")

    # Build raw content for LLM
    raw_content, id_to_entry = build_raw_content(entries_by_k_code)

    # Call LLM for formatting
    llm_output, usage = call_llm_formatter(raw_content, verbose=verbose)

    # Copy all data forward and update K-code entries with formatting
    # Each stage output is self-contained with complete state
    entries_formatted_count = 0
    total_cost = 0.0

    # llm_result['cost'] is the per-provider, per-model cost from
    # calculate_cost(), including Bedrock prompt-cache discounts when
    # caching is on. Don't recompute it from a hardcoded $/M-token figure.
    if usage:
        total_cost = usage.get('cost', 0.0)

    if llm_output:
        # Parse LLM output
        id_to_formatted = parse_llm_output(llm_output, id_to_entry)

        if verbose:
            print(f"  Parsed {len(id_to_formatted)} formatted entries from LLM output")

        # Update K-code entries in place with formatted text
        for entry_id, entry in id_to_entry.items():
            if entry_id in id_to_formatted:
                formatted_text = id_to_formatted[entry_id]

                # Store formatted text in extracted_fields
                if 'extracted_fields' not in entry:
                    entry['extracted_fields'] = {}
                entry['extracted_fields']['formatted_text'] = formatted_text
                entry['extracted_fields']['formatting_source'] = 'stage_5c_llm'
                entries_formatted_count += 1
    else:
        if verbose:
            print("  LLM formatting not available, keeping original text")

    # Add stage metadata to the full data copy
    data['stage_5c'] = {
        'stage': '5c',
        'stage_name': 'Teaching/Educational Contributions Formatter',
        'input_file': input_path,
        'k_entries_processed': total_k_entries,
        'entries_formatted': entries_formatted_count,
        'model': (usage.get('model') if usage else None) or (model if llm_output else None),
        'timestamp': datetime.now().isoformat(),
        'total_cost': total_cost,
        'prompt_tokens': usage.get('prompt_tokens', 0) if usage else 0,
        'completion_tokens': usage.get('completion_tokens', 0) if usage else 0,
        'total_tokens': usage.get('total_tokens', 0) if usage else 0,
        'cache_read_tokens': usage.get('cache_read_tokens', 0) if usage else 0,
        'cache_write_tokens': usage.get('cache_write_tokens', 0) if usage else 0,
    }

    # Write output - full copy with K-code entries updated
    if not output_path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{document_uid}_teaching_formatted.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n  Output: {output_path}")

    return str(output_path)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 5c: Teaching/Educational Contributions Formatter"
    )
    parser.add_argument('input_path', help='Path to input JSON file')
    parser.add_argument('-o', '--output', help='Output path (optional)')
    parser.add_argument('-m', '--model', default='gpt-4o-mini',
                        help='OpenAI model for formatting (default: gpt-4o-mini)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Quiet mode (minimal output)')

    args = parser.parse_args()

    output = run_stage_5c(
        args.input_path,
        output_path=args.output,
        model=args.model,
        verbose=not args.quiet
    )

    print(f"\nStage 5c complete: {output}")


if __name__ == '__main__':
    main()
