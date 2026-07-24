"""Pass 2 LLM content segmentation + chunk merge for chunked Word segmentation.

Split out of word_chunked.py (#315 follow-up).
"""

import json
from typing import Any, Dict, List

from unified_pipeline.llm_client import call_llm

try:
    from .chunking import table_to_text
except (ImportError, ValueError):
    from chunking import table_to_text


def segment_chunk_with_llm(chunk: List[Dict], section_label: str, section_level: int) -> Dict[str, Any]:
    """
    Pass 2: Use LLM to segment a chunk into entries.

    Since chunks are <12K chars, we can safely process with API.
    Uses structured outputs for guaranteed valid JSON.

    Args:
        chunk: List of document elements (paragraphs/tables) to segment
        section_label: Human-readable section name (e.g., "Publications")
        section_level: Hierarchy level (1, 2, or 3)

    Returns segmented entries for this chunk.
    """

    # Build compact representation of chunk - handle BOTH paragraphs and tables
    # NOTE: We provide RAW text without [N] element indices to avoid confusing the LLM
    chunk_lines = []
    for elem in chunk:
        if elem.get('type') == 'table':
            # Convert table to text
            table_text = table_to_text(elem)
            if table_text.strip():
                chunk_lines.append(table_text)
        else:
            # Regular paragraph
            text = elem.get('text', '').strip()
            if text:
                chunk_lines.append(text)

    chunk_text = "\n\n".join(chunk_lines)

    # Simplified schema for chunk-level segmentation with self-confidence scoring
    CHUNK_SCHEMA = {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text_snippet": {
                            "type": "string",
                            "description": "The COMPLETE text for this specific entry ONLY (not the entire chunk)"
                        },
                        "segmentation_confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "Confidence (0.0-1.0) that this represents a unique, correctly-segmented CV entry (not oversplit or undersplit)"
                        }
                    },
                    "required": ["text_snippet", "segmentation_confidence"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["entries"],
        "additionalProperties": False
    }

    system_prompt = f"""You are segmenting a section of an academic CV labeled "{section_label}".

Your task: Break this section into individual entries (publications, grants, positions, etc.).

RULES:
1. Each entry should be ONE distinct item (one publication, one grant, one position, etc.)
2. Combine consecutive paragraphs that belong together (e.g., multi-line publications)
3. Extract the COMPLETE text for each entry into text_snippet - this is your ONLY task

4. TABLES: If text contains a table with multiple rows where the rows describe
   ONE conceptual item (e.g., "Position: Professor\\nDepartment: Biology\\nYears: 2010-2020"),
   treat the ENTIRE table as ONE entry.

   However, if the rows are distinct items in a list (e.g., "2015 | Award A\\n2016 | Award B"),
   each row is a SEPARATE entry (see RULE 5).

4a. If a multi-line block describes ONE conceptual item (one talk, one award, one publication,
    one grant, one position, etc.), keep ALL of its lines together as ONE entry. Metadata fields
    such as dates, identifiers, amounts, authors, roles, or descriptions DO NOT become separate entries.

4b. CONCEPTUAL ITEM ANCHOR RULE (CRITICAL):
    Every entry MUST begin with an anchor line: a title, citation, grant ID, position title,
    award name, presentation title, or other primary label that would appear as the main
    heading of that item on a CV.

    Lines that are NOT anchors (e.g., dates, ISBNs, amounts, roles, descriptions, PIs,
    locations, sponsors, publishers) MUST attach to the nearest previous anchor and
    CANNOT start a new entry.

5. MULTI-ITEM BLOCKS: If the text contains MULTIPLE distinct items separated by
   newlines or delimiters (e.g., a list of 45 presentations with dates),
   you MUST split it into MULTIPLE entries. Look for:
   - Date anchors (e.g., "May 26, 1994 | Title...")
   - Author/citation patterns
   - Sequential numbering or bullet markers

   Each distinct item gets its OWN entry with ONLY its own text in text_snippet.

   EXAMPLE REQUIRING SPLIT:
   "May 26, 1994 | Anthony, T. R. NORM in Pulp...
    Mar 6, 1997 | Anthony, T. R. Health Hazards...
    Jun 13, 1997 | Anthony, T. R. Evaluating..."

   → This should create 3 entries, each with ONLY their own date/title/text:
     Entry 1 text_snippet: "May 26, 1994 | Anthony, T. R. NORM in Pulp..."
     Entry 2 text_snippet: "Mar 6, 1997 | Anthony, T. R. Health Hazards..."
     Entry 3 text_snippet: "Jun 13, 1997 | Anthony, T. R. Evaluating..."

   EXAMPLE NOT REQUIRING SPLIT (qualifier for a single item):
   "Position: Associate Professor
    (with tenure and promotion to Full Professor pending)"

   → This is ONE entry describing one position with a qualifier.

5a. RULE 5 applies ONLY when the text contains MULTIPLE independent items. Do NOT split a block
    simply because it contains multiple lines—split only when those lines represent separate items.

    A block may only be split when it contains MULTIPLE anchor lines. Without multiple anchor
    lines, it MUST remain ONE entry.

{'='*80}
⚠️  CRITICAL: TEXT_SNIPPET UNIQUENESS REQUIREMENT
{'='*80}

For each entry, the value of `text_snippet` MUST contain ONLY the text
of THAT specific entry, NOT the entire section chunk.

❌ DO NOT repeat the same snippet across multiple entries.
❌ DO NOT copy the entire chunk text to every entry.
✅ Each entry MUST have a DISTINCT text_snippet containing only its own content.

If two entries have identical text_snippet, that indicates a segmentation failure.
Ensure every entry has unique text that corresponds to that entry alone.

When segmenting multi-item blocks (RULE 8), identify the boundaries between items
using date anchors, author names, or other separators, and extract ONLY the text
for each individual item.

{'='*80}
⚠️  SEGMENTATION CONFIDENCE SCORING
{'='*80}

For EACH entry you create, provide a segmentation_confidence score (0.0-1.0) indicating
how confident you are that this entry represents a unique, correctly-segmented CV item.

HIGH CONFIDENCE (0.8-1.0):
  ✓ Entry has a clear anchor (title, citation, grant ID, position, award name)
  ✓ Includes all related metadata (dates, identifiers, amounts, roles, descriptions)
  ✓ Not missing any parts that should be included
  ✓ Not including multiple distinct items

MEDIUM CONFIDENCE (0.5-0.7):
  ⚠ Entry might be missing metadata that appears elsewhere
  ⚠ Boundary between this and adjacent entries is somewhat ambiguous
  ⚠ Could potentially be merged with or split from neighbors

LOW CONFIDENCE (0.0-0.4):
  ✗ Entry appears to be metadata-only (e.g., standalone ISBN, date, identifier)
  ✗ Entry might be missing its anchor/title
  ✗ Entry clearly contains multiple distinct items that should be split
  ✗ Entry is likely oversplit or undersplit

Use this score to flag entries that may need manual review or correction.

{'='*80}

Context: This is a level {section_level} section in the CV."""

    user_prompt = f"""Section: {section_label}

Content:
{chunk_text}

Please segment into individual entries."""

    try:
        llm_result = call_llm(
            stage="segmentation_word_chunked",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "chunk_segmentation",
                    "strict": True,
                    "schema": CHUNK_SCHEMA
                }
            },
            max_tokens=8000,
            temperature=0.1
        )

        result = json.loads(llm_result["content"])

        result['token_usage'] = {
            'prompt_tokens': llm_result["prompt_tokens"],
            'completion_tokens': llm_result["completion_tokens"],
            'total_tokens': llm_result["total_tokens"]
        }

        result['cost'] = llm_result["cost"]
        result['model_used'] = llm_result["model"]

        # Note: entry_type removed in V6 - classification happens in Stage 2 (taxonomy mapping)
        # segmentation_confidence is now provided by the model (required in schema)

        # Detect duplication (segmentation failure indicator)
        entry_texts = [e.get('text_snippet', '') for e in result.get('entries', [])]
        unique_texts = set(entry_texts)
        duplication_ratio = len(unique_texts) / len(entry_texts) if entry_texts else 0.0

        result['duplication_stats'] = {
            'total_entries': len(entry_texts),
            'unique_entries': len(unique_texts),
            'duplication_ratio': duplication_ratio,
            'has_duplicates': len(unique_texts) < len(entry_texts)
        }

        # Log segmentation metrics
        print(f"      Segmented: {len(result['entries'])} entries")
        print(f"      Model: {llm_result['model']}")
        print(f"      Tokens: {llm_result['prompt_tokens']} in, {llm_result['completion_tokens']} out")
        print(f"      Cost: ${llm_result['cost']:.4f}")

        if entry_texts:  # Only show uniqueness stats if there are entries
            print(f"      Unique: {len(unique_texts)}/{len(entry_texts)} ({duplication_ratio:.1%})")

            # Report low-confidence entries (likely segmentation issues)
            low_confidence = [
                e for e in result.get('entries', [])
                if e.get('segmentation_confidence', 1.0) < 0.5
            ]
            if low_confidence:
                print(f"      ⚠️  Low confidence: {len(low_confidence)} entries flagged (confidence < 0.5)")

            if duplication_ratio < 0.5:
                print(f"      ⚠️  WARNING: Severe duplication detected (only {duplication_ratio:.1%} unique)")
                print(f"      This indicates a segmentation failure - entries have identical text_snippet")
        else:
            print(f"      (No entries - likely a section header only)")

        return result

    except Exception as e:
        print(f"Error segmenting chunk: {e}")
        print(f"Section: {section_label}")
        # Return fallback: treat entire chunk as one entry (no confidence score - we don't know)
        return {
            "entries": [{
                "text_snippet": chunk_text,
                "entry_type": "other"
                # No segmentation_confidence - this is an error fallback, not actual segmentation
            }],
            'token_usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
            'cost': 0.0,
            'model_used': 'fallback',
            'duplication_stats': {
                'total_entries': 1,
                'unique_entries': 1,
                'duplication_ratio': 1.0,
                'has_duplicates': False
            }
        }


def merge_chunk_results(chunks_results: List[Dict], section_header: Dict, group_id: str) -> Dict[str, Any]:
    """
    Pass 3a: Merge results from multiple chunks into single section.

    Assigns proper IDs and validates structure.
    """

    all_entries = []
    entry_counter = 1

    for chunk_result in chunks_results:
        for entry in chunk_result.get('entries', []):
            # Get the text content (prefer full_text, fallback to text_snippet)
            text_content = entry.get('full_text', entry.get('text_snippet', ''))

            # Skip entries with empty text (fixes blank entry bug from table parsing)
            if not text_content or not text_content.strip():
                continue

            # Build entry with segmentation_confidence (if available)
            new_entry = {
                "id": f"{group_id}-E{entry_counter}",
                "text_snippet": text_content
            }

            # Include segmentation_confidence if present (don't default to avoid made-up values)
            if 'segmentation_confidence' in entry:
                new_entry['segmentation_confidence'] = entry['segmentation_confidence']

            all_entries.append(new_entry)
            entry_counter += 1

    group_data = {
        "id": group_id,
        "level": section_header['level'],
        "label_inferred": section_header['text'],
        "entries": all_entries,
        "subgroups": []  # Will be populated in Pass 3b
    }

    # Include header validation metadata if present (from Pass 1b)
    if 'header_likeness_score' in section_header:
        group_data['header_likeness_score'] = section_header['header_likeness_score']
    if 'validation_reason' in section_header:
        group_data['validation_reason'] = section_header['validation_reason']
    if 'validated_by' in section_header:
        group_data['validated_by'] = section_header['validated_by']

    return group_data
