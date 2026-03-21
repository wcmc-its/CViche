"""
Stage 2: Entry Extraction from CV Sections

Uses Stage 1 hierarchy to extract individual entries from each section.
Leverages full Word formatting via structured layout JSON.
Returns element indices for cost efficiency.
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Any
from openai import OpenAI

# Import existing Word structure extraction
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.docx_structure_extractor import extract_docx_structure, create_simplified_layout_json

client = OpenAI()


SYSTEM_PROMPT = """You are an expert CV parser specialized in identifying entry boundaries.

You will receive a structured layout JSON representing a section from an academic CV. Your task is to identify individual entries (publications, grants, positions, etc.) by specifying which Word document elements (paragraphs or table rows) constitute each entry.

---

ENTRY DEFINITION

An ENTRY is the smallest self-contained unit describing one distinct professional item:
- One publication (article, book chapter, abstract, conference paper)
- One grant or funded project (unique grant number or project)
- One position or appointment (job title at an institution)
- One degree, fellowship, residency, or postdoctoral program
- One award, prize, or honor
- One course taught
- One mentee (PhD student, Master's student, postdoc)
- One invited talk, keynote, or presentation
- One patent or patent application
- One committee role or professional membership

---

VISUAL AND SEMANTIC CUES

Use BOTH visual structure AND semantic content to determine boundaries:

VISUAL CUES (from layout JSON):
1. Bullets/numbered lists: Each item at the same list_level is typically one entry
2. Indentation: Increased indent_left usually indicates qualifiers, not new entries
3. Paragraph breaks: New paragraphs often signal new entries
4. Tables: Each table row is typically one entry
5. Bold text: May indicate entry start or section headers
6. Spacing: Larger spacing often separates entries

SEMANTIC CUES:
1. Citation patterns: "Author (Year). Title. Venue." structure
2. Date ranges: "2020-2023" or "2020-Present"
3. Grant numbers: "R01 DC017291" or "NIH R21"
4. Institution names: "Harvard University" or "Rutgers School of Medicine"
5. Titles/roles: "Associate Professor" or "Principal Investigator"
6. Degree types: "PhD" or "MD, MPH"

---

QUALIFIERS vs ENTRIES

Many entries include qualifier lines that provide additional detail. Qualifiers MUST be attached to the preceding entry, NOT treated as separate entries.

COMMON QUALIFIERS:
- "Co-authored with 3 mentees"
- Funding amounts: "$2.5M direct costs"
- Roles: "Co-PI", "Site PI", "Collaborator"
- Identifiers: DOI, PMID, patent numbers
- Descriptions: "Developed new curriculum for..."
- Collaborators: "With Drs. Smith, Jones, and Williams"
- Outcomes: "Now Assistant Professor at State University"
- Course details: "Fall 2020, Fall 2021, Fall 2022"

GLOBAL QUALIFIERS (NOT entries):
- "All publications are peer-reviewed unless noted"
- "* indicates corresponding authorship"
- Section descriptions or metadata

---

SPLITTING vs MERGING

SPLIT into multiple entries when:
1. Multiple publications appear with clear citation structure
2. Multiple distinct positions with different dates
3. Semicolons or numbering separate items
4. Table cells contain multiple items with repeated patterns

MERGE into single entry when:
1. Lines are part of same item (continuation of title, author list, description)
2. Indented lines expand on the preceding item
3. Lines wrap due to formatting (long titles, long author lists)
4. Subsequent lines are clearly qualifiers (see above)

---

ELEMENT INDEX SPECIFICATION

For each entry, specify:
- element_idx_start: First element (paragraph or table row) of the entry
- element_idx_end: Last element (paragraph or table row) of the entry (may equal start)

If one entry spans elements 42-44 (e.g., multi-line publication):
  element_idx_start: 42
  element_idx_end: 44

If one entry is contained in element 42 only:
  element_idx_start: 42
  element_idx_end: 42

---

CONFIDENCE SCORING

Assign confidence (0.0 to 1.0) based on:
- 0.95-1.0: Clear entry boundaries (distinct citation, unique grant number, table row)
- 0.80-0.94: Likely entry (good visual/semantic cues, minor ambiguity)
- 0.60-0.79: Uncertain (weak cues, possible qualifier vs entry confusion)
- Below 0.60: Very uncertain (flag for human review)

---

OUTPUT FORMAT

Return a JSON object with this structure:

{
  "section_id": "string (provided in user prompt)",
  "entries": [
    {
      "entry_id": "E1",
      "element_idx_start": 42,
      "element_idx_end": 44,
      "entry_type": "publication|grant|position|education|award|teaching|mentoring|presentation|patent|service|other",
      "text_snippet": "First ~200 characters of entry for verification",
      "confidence": 0.95,
      "notes": "Optional: Any ambiguity or special handling"
    }
  ],
  "metadata": {
    "total_entries": 25,
    "avg_confidence": 0.92,
    "processing_notes": "Optional: Overall observations"
  }
}

---

QUALITY TARGETS

- Aim for high recall: Extract ALL legitimate entries
- Use context from section header (e.g., "PUBLICATIONS", "GRANTS") to inform entry_type
- When uncertain about boundaries, prefer splitting with lower confidence over merging
- Include "notes" field for any ambiguous cases
- Flag entries with confidence < 0.60 for human review

---

IMPORTANT REMINDERS

1. One real-world item = one entry
2. Qualifiers attach to preceding entry
3. Use both visual (layout) and semantic (content) cues
4. Return element indices, not full content (cost efficiency)
5. When in doubt, check: "Could these be from different items?" If yes, split.
"""


def extract_section_layout(full_structure: List[Dict], section_header: str,
                           start_idx: int = None, end_idx: int = None) -> List[Dict]:
    """
    Extract layout elements for a specific section.

    Args:
        full_structure: Full document structure from extract_docx_structure()
        section_header: Section name from Stage 1 (e.g., "PUBLICATIONS")
        start_idx: Optional starting element index
        end_idx: Optional ending element index

    Returns:
        Filtered structure for this section only
    """
    if start_idx is not None and end_idx is not None:
        return full_structure[start_idx:end_idx+1]

    # If no indices provided, try to find section by header text
    # This is a fallback - normally Stage 1 should provide indices
    section_elements = []
    in_section = False

    for elem in full_structure:
        # Check if this is the section header
        if section_header.lower() in elem.get('text', '').lower():
            in_section = True
            continue

        # Check if we hit the next section header (bold, large font, or heading style)
        if in_section:
            is_header = (
                elem.get('bold', False) and
                elem.get('font_size', 0) >= 12 and
                len(elem.get('text', '')) < 100  # Headers are usually short
            ) or 'Heading' in elem.get('style', '')

            if is_header:
                break

            section_elements.append(elem)

    return section_elements


def extract_entries_from_section(section_layout: List[Dict], section_header: str,
                                 section_id: str, model: str = "gpt-5.1") -> Dict[str, Any]:
    """
    Extract entries from a single CV section.

    Args:
        section_layout: Structured layout JSON for this section
        section_header: Section name (e.g., "PUBLICATIONS")
        section_id: Unique section identifier (e.g., "publications_1")
        model: OpenAI model to use

    Returns:
        Dictionary with entries, metadata, and statistics
    """
    print(f"\n{'='*80}")
    print(f"STAGE 2: ENTRY EXTRACTION")
    print(f"{'='*80}")
    print(f"Section: {section_header}")
    print(f"Section ID: {section_id}")
    print(f"Elements: {len(section_layout)}")
    print(f"Model: {model}\n")

    # Create compact layout JSON
    layout_json = json.dumps(section_layout, separators=(',', ':'))

    user_prompt = f"""Extract entries from this CV section.

SECTION HEADER: {section_header}
SECTION ID: {section_id}

LAYOUT JSON:
{layout_json}

Return entries following the specification above."""

    print(f"Step 1: Sending section to {model}...")
    print(f"  Layout size: {len(layout_json):,} characters")
    print(f"  Estimated input tokens: ~{len(layout_json) // 4:,}")

    # Define JSON schema for structured output
    # Note: In strict mode, all properties must be required. Optional fields are removed.
    schema = {
        "type": "object",
        "properties": {
            "section_id": {"type": "string"},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "element_idx_start": {"type": "integer"},
                        "element_idx_end": {"type": "integer"},
                        "text_snippet": {"type": "string"},
                        "confidence": {"type": "number"}
                    },
                    "required": ["entry_id", "element_idx_start", "element_idx_end",
                               "text_snippet", "confidence"],
                    "additionalProperties": False
                }
            },
            "metadata": {
                "type": "object",
                "properties": {
                    "total_entries": {"type": "integer"},
                    "avg_confidence": {"type": "number"}
                },
                "required": ["total_entries", "avg_confidence"],
                "additionalProperties": False
            }
        },
        "required": ["section_id", "entries", "metadata"],
        "additionalProperties": False
    }

    # Call GPT-5.1
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "cv_entry_extraction",
                "strict": True,
                "schema": schema
            }
        },
        temperature=0.1
    )

    result = json.loads(response.choices[0].message.content)

    print(f"  ✓ Extraction completed")
    print(f"  Entries found: {result['metadata']['total_entries']}")
    print(f"  Avg confidence: {result['metadata']['avg_confidence']:.2f}")

    # Add token usage stats
    result['token_usage'] = {
        'input': response.usage.prompt_tokens,
        'output': response.usage.completion_tokens,
        'total': response.usage.total_tokens
    }

    # Calculate cost (GPT-5.1 pricing: $2.50/1M input, $10.00/1M output)
    cost = (response.usage.prompt_tokens * 2.50 / 1_000_000) + \
           (response.usage.completion_tokens * 10.00 / 1_000_000)
    result['cost'] = cost

    print(f"  Input tokens: {response.usage.prompt_tokens:,}")
    print(f"  Output tokens: {response.usage.completion_tokens:,}")
    print(f"  Cost: ${cost:.4f}")

    # Save raw output to outputs directory
    output_dir = Path(__file__).parent.parent / "outputs" / "stage_2_extraction"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename from section_id and timestamp
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"{section_id}_{timestamp}.json"

    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"  ✓ Saved raw output to: {output_file}")

    return result


def test_stage2_on_section(cv_path: str, section_name: str = None, model: str = "gpt-5.1"):
    """
    Test Stage 2 entry extraction on a single section from a CV.

    Args:
        cv_path: Path to CV DOCX file
        section_name: Optional section name to test (if None, uses first H1)
        model: Model to use for extraction
    """
    # Import Stage 1 hierarchy extractor (only needed for testing)
    from chunked_chat_hierarchy_extractor import get_cv_hierarchy_chunked, format_hierarchy_outline

    print(f"\n{'='*80}")
    print(f"STAGE 2 ENTRY EXTRACTION TEST")
    print(f"{'='*80}")
    print(f"CV: {cv_path}")
    print(f"Model: {model}\n")

    # Step 1: Get hierarchy from Stage 1
    print("Step 1: Running Stage 1 hierarchy extraction...")
    hierarchy, stats = get_cv_hierarchy_chunked(cv_path, model=model)

    # Step 2: Extract full Word structure
    print("\nStep 2: Extracting full Word document structure...")
    full_structure_dict = extract_docx_structure(cv_path)
    full_structure = full_structure_dict['elements']
    print(f"  ✓ Extracted {len(full_structure)} elements from document")

    # Step 3: Select section to test
    print("\nStep 3: Selecting section to test...")
    if section_name:
        # Find section by name
        target_section = None
        for item in hierarchy:
            if item['level'] == 'H1' and section_name.lower() in item['text'].lower():
                target_section = item
                break
        if not target_section:
            print(f"  ⚠️  Section '{section_name}' not found, using first H1 instead")
            target_section = next(h for h in hierarchy if h['level'] == 'H1')
    else:
        # Use first H1 section
        target_section = next(h for h in hierarchy if h['level'] == 'H1')

    print(f"  Selected section: {target_section['text']}")

    # Step 4: Extract section layout
    # For now, we'll use a heuristic to find section boundaries
    # TODO: Stage 1 should return element indices for each section
    print("\nStep 4: Extracting section layout...")
    section_layout = extract_section_layout(
        full_structure,
        target_section['text']
    )
    print(f"  ✓ Section has {len(section_layout)} elements")

    # Step 5: Extract entries
    print("\nStep 5: Extracting entries from section...")
    result = extract_entries_from_section(
        section_layout,
        target_section['text'],
        section_id=target_section['text'].lower().replace(' ', '_'),
        model=model
    )

    # Step 6: Display results
    print(f"\n{'='*80}")
    print(f"EXTRACTION RESULTS")
    print(f"{'='*80}\n")

    print(f"Section: {target_section['text']}")
    print(f"Total entries: {result['metadata']['total_entries']}")
    print(f"Avg confidence: {result['metadata']['avg_confidence']:.2f}")
    print(f"Processing notes: {result['metadata'].get('processing_notes', 'N/A')}\n")

    print("Entries:")
    print("-" * 80)
    for entry in result['entries']:
        print(f"\n{entry['entry_id']} ({entry['entry_type']}) - Confidence: {entry['confidence']:.2f}")
        print(f"  Elements: {entry['element_idx_start']}-{entry['element_idx_end']}")
        print(f"  Snippet: {entry['text_snippet'][:150]}...")
        if entry.get('notes'):
            print(f"  Notes: {entry['notes']}")

    print(f"\n{'='*80}")
    print(f"STATISTICS")
    print(f"{'='*80}")
    print(f"Stage 1 cost: ${stats['extraction_cost']:.4f}")
    print(f"Stage 2 cost: ${result['cost']:.4f}")
    print(f"Total cost: ${stats['extraction_cost'] + result['cost']:.4f}")
    print(f"Stage 1 tokens: {stats['extraction_input_tokens'] + stats['extraction_output_tokens']:,}")
    print(f"Stage 2 tokens: {result['token_usage']['total']:,}")

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stage2_entry_extractor.py <cv_file.docx> [section_name] [model]")
        print("  section_name: Optional section to test (e.g., 'publications', 'grants')")
        print("  model: Optional model (default: gpt-5.1)")
        sys.exit(1)

    cv_path = sys.argv[1]
    section_name = sys.argv[2] if len(sys.argv) > 2 else None
    model = sys.argv[3] if len(sys.argv) > 3 else "gpt-5.1"

    result = test_stage2_on_section(cv_path, section_name, model)
