#!/usr/bin/env python3
"""
Stage 5d: Citation Formatter for Non-Enriched Publications

Uses an LLM to reformat S-code entries (publications) that could not be enriched
via PubMed into proper Vancouver citation format.

This stage:
1. Identifies S-code entries with enrichment_status != 'enriched'
2. Sends the raw text to an LLM to extract and format as Vancouver citation
3. Stores the formatted citation in extracted_fields.formatted_citation

Input: Stage 5c output (or earlier stage output)
Output: *_citation_formatted.json with reformatted non-enriched citations

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
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5d_citation_formatted"

# Publication codes that may need formatting
PUBLICATION_CODES = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']

# Publication type descriptions for LLM context
PUBLICATION_TYPE_DESCRIPTIONS = {
    'S1': 'Peer-reviewed research article (journal article)',
    'S2': 'Review article or editorial',
    'S3': 'Book (authored)',
    'S4': 'Book chapter (contributed chapter in edited volume)',
    'S5': 'Monograph',
    'S6': 'Letter or correspondence',
    'S7': 'Manuscript in review/submitted (unpublished)',
    'S8': 'Abstract or conference proceeding',
    'S9': 'Other publication (media coverage, podcast, etc.)',
}

# LLM prompt for reformatting citations
CITATION_FORMATTER_PROMPT = '''You are a citation formatter. Convert the raw citation text into proper Vancouver (biomedical) citation format.

PUBLICATION TYPES:
Each entry includes a taxonomy code indicating the publication type:
- S1: Peer-reviewed research article (journal article)
- S2: Review article or editorial
- S3: Book (authored)
- S4: Book chapter (contributed chapter in edited volume)
- S5: Monograph
- S6: Letter or correspondence
- S7: Manuscript in review/submitted (unpublished)
- S8: Abstract or conference proceeding
- S9: Other publication (media coverage, podcast, etc.)

VANCOUVER FORMAT RULES:
1. Authors: LastName INITIALS (no periods, no commas between last name and initials)
   - Example: Smith JA, Jones MB, Brown CK
   - List all authors, or first 6 followed by "et al." if more than 6
2. Title: Sentence case, ending with period
3. Journal/Book: Title case or official abbreviation
4. Year;Volume(Issue):Pages.
5. Identifiers: doi:xxx. PMID:xxx. PMCID:xxx.

FORMAT BY PUBLICATION TYPE:

FOR JOURNAL ARTICLES (S1, S2, S6):
- Format: Authors. Title. Journal. Year;Volume(Issue):Pages. doi:xxx.

FOR BOOKS (S3, S5):
- Format: Authors. Book Title. Edition. Location: Publisher; Year.

FOR BOOK CHAPTERS (S4):
- Format: Authors. Chapter title. In: Editors, eds. Book Title. Location: Publisher; Year:Pages.

FOR MANUSCRIPTS IN REVIEW (S7):
- Format: Authors. Title. Journal (if known). [Submitted/In review]. Year.

FOR ABSTRACTS/PROCEEDINGS (S8):
- Format: Authors. Abstract title. Conference Name; Year Month Day; Location.

FOR OTHER (S9 - media, podcasts, etc.):
- Format appropriately based on content (e.g., "Title. Publication/Outlet. Year.")

INPUT FORMAT:
You will receive entries with their taxonomy code (indicating publication type) and raw text.

OUTPUT FORMAT:
Return a JSON object with entry IDs mapped to formatted citations:
{{
  "CIT-0001": {{
    "authors": "Smith JA, Jones MB",
    "title": "Article title here",
    "journal": "Journal Name",
    "year": "2020",
    "volume": "45",
    "issue": "3",
    "pages": "123-145",
    "doi": "10.1234/example",
    "formatted_citation": "Smith JA, Jones MB. Article title here. Journal Name. 2020;45(3):123-145. doi:10.1234/example."
  }},
  "CIT-0002": {{ ... }}
}}

IMPORTANT:
- Extract ALL available fields from the raw text
- If a field is not present, omit it (don't guess)
- The formatted_citation should be the complete Vancouver-style citation
- Preserve author names exactly as they appear (don't invent initials)
- For book chapters, include "In:" before the book title

Now format these citations:

<<<RAW_CITATIONS
{raw_content}
RAW_CITATIONS>>>'''


def build_raw_content(entries: List[Dict]) -> Tuple[str, Dict[str, Dict]]:
    """
    Build raw content string for LLM prompt and a mapping of entry IDs to entries.

    Returns:
        Tuple of (raw_content_string, id_to_entry_mapping)
    """
    lines = []
    id_to_entry = {}

    for i, entry in enumerate(entries):
        entry_id = f"CIT-{i+1:04d}"
        id_to_entry[entry_id] = entry

        code = entry.get('taxonomy_code', 'S1')
        raw_text = entry.get('text', '')

        # Include publication type description for better LLM context
        type_desc = PUBLICATION_TYPE_DESCRIPTIONS.get(code, 'Publication')

        lines.append(f"[{entry_id}] ({code}: {type_desc})")
        lines.append(raw_text)
        lines.append("")

    return '\n'.join(lines), id_to_entry


def parse_llm_output(llm_output: str, id_to_entry: Dict[str, Dict], verbose: bool = True) -> Dict[str, Dict]:
    """
    Parse LLM JSON output and extract formatted citations per entry ID.

    Returns:
        Dict mapping entry_id -> parsed fields dict
    """
    id_to_formatted = {}

    if not llm_output:
        return id_to_formatted

    # Try to parse as JSON directly first (for json_object response format)
    try:
        parsed = json.loads(llm_output)
        for entry_id, fields in parsed.items():
            if entry_id in id_to_entry:
                id_to_formatted[entry_id] = fields
        return id_to_formatted
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from the response
    try:
        # Find JSON object in response
        json_match = re.search(r'\{[\s\S]*\}', llm_output)
        if json_match:
            parsed = json.loads(json_match.group())

            for entry_id, fields in parsed.items():
                if entry_id in id_to_entry:
                    id_to_formatted[entry_id] = fields
    except json.JSONDecodeError as e:
        if verbose:
            print(f"  Warning: Could not parse LLM JSON output: {e}")
            # Print first 500 chars for debugging
            print(f"  Response preview: {llm_output[:500]}...")

    return id_to_formatted


def call_llm_formatter(raw_content: str, verbose: bool = True) -> tuple:
    """
    Call LLM to reformat citations.

    Args:
        raw_content: Raw content string with entry IDs
        verbose: Whether to print progress

    Returns:
        Tuple of (LLM response string, usage dict) or (None, None) if failed
    """
    try:
        prompt = CITATION_FORMATTER_PROMPT.format(raw_content=raw_content)
        messages = [{"role": "user", "content": prompt}]

        if verbose:
            print(f"  Calling LLM for citation formatting...")

        llm_result = call_llm(
            stage="stage_5d",
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
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
            # What actually served the call. The `model` parameter below is a
            # default no orchestrator passes, so recording it stamped every
            # artifact with a model the run never used (#459).
            'model': llm_result.get("model"),
        }

        return result_text, usage

    except Exception as e:
        if verbose:
            import traceback
            print(f"  Warning: LLM formatting failed: {type(e).__name__}: {e}")
            traceback.print_exc()
        return None, None


def run_stage_5d(input_path: str, output_path: str = None, model: str = "gpt-5.1",
                 verbose: bool = True, batch_size: int = 20) -> str:
    """
    Run Stage 5d: Citation Formatter for non-enriched publications.

    Args:
        input_path: Path to input JSON (Stage 5c or earlier)
        output_path: Optional output path
        model: OpenAI model for formatting
        verbose: Whether to print progress
        batch_size: Number of citations to process per LLM call

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
        print(f"Stage 5d: Citation Formatter (Non-Enriched)")
        print(f"{'='*60}")
        print(f"Document: {document_uid}")

    # Find non-enriched S-code entries
    non_enriched = []
    for entry in entries:
        code = entry.get('taxonomy_code', '')
        if code in PUBLICATION_CODES:
            status = entry.get('enrichment_status', '')
            if status != 'enriched':
                non_enriched.append(entry)

    if verbose:
        total_pubs = sum(1 for e in entries if e.get('taxonomy_code', '') in PUBLICATION_CODES)
        print(f"  Total publications: {total_pubs}")
        print(f"  Non-enriched (need formatting): {len(non_enriched)}")

    if not non_enriched:
        if verbose:
            print("  No non-enriched citations to format")
        # Just copy input to output
        if not output_path:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / f"{document_uid}_citation_formatted.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return str(output_path)

    # Process in batches
    formatted_count = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    total_cost = 0.0
    observed_model = None

    for batch_start in range(0, len(non_enriched), batch_size):
        batch = non_enriched[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(non_enriched) + batch_size - 1) // batch_size

        if verbose:
            print(f"\n  Processing batch {batch_num}/{total_batches} ({len(batch)} citations)...")

        # Build raw content for this batch
        raw_content, id_to_entry = build_raw_content(batch)

        # Call LLM
        llm_output, usage = call_llm_formatter(raw_content, verbose=verbose)

        # Accumulate tokens + the per-batch cost from llm_result['cost'],
        # which calculate_cost() prices per-provider and per-model and
        # accounts for Bedrock prompt-cache discounts. Don't recompute
        # cost here from a hardcoded $/M-token figure.
        if usage:
            total_prompt_tokens += usage.get('prompt_tokens', 0)
            total_completion_tokens += usage.get('completion_tokens', 0)
            total_cache_read_tokens += usage.get('cache_read_tokens', 0)
            total_cache_write_tokens += usage.get('cache_write_tokens', 0)
            total_cost += usage.get('cost', 0.0)
            observed_model = usage.get('model') or observed_model

        if llm_output:
            # Parse LLM output
            id_to_formatted = parse_llm_output(llm_output, id_to_entry, verbose=verbose)

            if verbose:
                print(f"  Parsed {len(id_to_formatted)} formatted citations")

            # Update entries with formatted data
            for entry_id, formatted_data in id_to_formatted.items():
                entry = id_to_entry.get(entry_id)
                if entry:
                    if 'extracted_fields' not in entry:
                        entry['extracted_fields'] = {}

                    # Store the formatted citation
                    if 'formatted_citation' in formatted_data:
                        entry['extracted_fields']['formatted_citation'] = formatted_data['formatted_citation']
                        entry['extracted_fields']['formatting_source'] = 'stage_5d_llm'
                        formatted_count += 1

                    # Also update individual fields if they were extracted
                    for field in ['authors', 'title', 'journal', 'year', 'volume', 'issue', 'pages', 'doi', 'book_title']:
                        if field in formatted_data and formatted_data[field]:
                            # Only update if we don't already have this field or it's empty
                            existing = entry['extracted_fields'].get(field, '')
                            if not existing or existing == 'NONE':
                                entry['extracted_fields'][field] = formatted_data[field]

    # Add stage metadata
    data['stage_5d'] = {
        'stage': '5d',
        'stage_name': 'Citation Formatter (Non-Enriched)',
        'input_file': input_path,
        'non_enriched_count': len(non_enriched),
        'formatted_count': formatted_count,
        'model': observed_model or model,
        'timestamp': datetime.now().isoformat(),
        'total_cost': total_cost,
        'prompt_tokens': total_prompt_tokens,
        'completion_tokens': total_completion_tokens,
        'total_tokens': total_prompt_tokens + total_completion_tokens,
        'cache_read_tokens': total_cache_read_tokens,
        'cache_write_tokens': total_cache_write_tokens,
    }

    # Write output
    if not output_path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{document_uid}_citation_formatted.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n  Citations formatted: {formatted_count}/{len(non_enriched)}")
        print(f"  Output: {output_path}")

    return str(output_path)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 5d: Citation Formatter for Non-Enriched Publications"
    )
    parser.add_argument('input_path', help='Path to input JSON file')
    parser.add_argument('-o', '--output', help='Output path (optional)')
    parser.add_argument('-m', '--model', default='gpt-5.1',
                        help='OpenAI model for formatting (default: gpt-5.1)')
    parser.add_argument('-b', '--batch-size', type=int, default=20,
                        help='Citations per LLM call (default: 20)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Quiet mode (minimal output)')

    args = parser.parse_args()

    output = run_stage_5d(
        args.input_path,
        output_path=args.output,
        model=args.model,
        batch_size=args.batch_size,
        verbose=not args.quiet
    )

    print(f"\nStage 5d complete: {output}")


if __name__ == '__main__':
    main()
