"""
LLM-based Taxonomy Mapping using GPT-4o-mini

Maps CV sections to WCM taxonomy using semantic understanding rather than fuzzy matching.

Strategy:
1. For each section from segmented CV, provide:
   - Section label
   - 2-3 sample entries
2. Ask GPT-4o-mini to classify against WCM taxonomy
3. Use Structured Outputs for guaranteed valid JSON
4. Get confidence score for quality control

Benefits over fuzzy matching:
- Semantic understanding (not just string similarity)
- Handles ambiguity ("Service" could be committee service, community service, etc.)
- Fast: ~1-2 seconds per section
- Cheap: GPT-4o-mini is ~10x cheaper than GPT-4o
"""

import os
import json
import sys
import time
from pathlib import Path
from typing import Any

from unified_pipeline.llm_client import call_llm

# Import WCM taxonomy
from ..cv_parser.cv_taxonomy_wcm import CV_SECTIONS
from ..cv_parser.taxonomy_utils import get_sections_by_wcm_number, get_section_by_id


# JSON schema for Structured Outputs
TAXONOMY_MAPPING_SCHEMA = {
    "type": "object",
    "properties": {
        "mapped_section_id": {"type": "string"},
        "mapped_canonical_name": {"type": "string"},
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0"
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of mapping decision"
        },
        "alternative_matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "canonical_name": {"type": "string"},
                    "confidence": {"type": "number"}
                },
                "required": ["section_id", "canonical_name", "confidence"],
                "additionalProperties": False
            }
        }
    },
    "required": ["mapped_section_id", "mapped_canonical_name", "confidence", "reasoning", "alternative_matches"],
    "additionalProperties": False
}


def normalize_mapped_section_id(mapped_id: str) -> str:
    """
    Normalize LLM-returned section ID to proper section_id format.

    The LLM sometimes returns WCM section numbers (e.g., "16") instead of
    section IDs (e.g., "institutional_administration"). This function handles
    the conversion.

    Args:
        mapped_id: The section ID returned by the LLM (could be number or ID)

    Returns:
        Normalized section_id (e.g., "institutional_administration")
    """
    # Check if it's a numeric string (WCM section number)
    if mapped_id.isdigit() or (mapped_id.startswith("#") and mapped_id[1:].isdigit()):
        # Strip # if present
        wcm_num_str = mapped_id.lstrip("#")
        wcm_num = int(wcm_num_str)

        # Look up by WCM section number
        sections = get_sections_by_wcm_number(wcm_num)
        if sections:
            # Return the first (primary) section's ID
            # For parent sections, there should only be one
            primary = sections[0]
            print(f"  Normalized WCM section {wcm_num} → {primary['id']}")
            return primary['id']
        else:
            print(f"  WARNING: No section found for WCM number {wcm_num}, keeping '{mapped_id}'")
            return mapped_id

    # Check if the section_id exists in taxonomy
    section = get_section_by_id(mapped_id)
    if section:
        return mapped_id  # Valid section_id

    # Unknown section ID - keep as-is and let downstream handle it
    print(f"  WARNING: Unknown section ID '{mapped_id}', keeping as-is")
    return mapped_id


def build_taxonomy_options_string() -> str:
    """
    Build a compact string representation of WCM taxonomy for LLM.

    Format:
    ID: section_id
    Name: canonical name
    Description: brief description from notes
    Parent: parent_id (if any)
    Common aliases: first 5 aliases
    """
    options = []

    for section in CV_SECTIONS:
        # Get essential info
        section_id = section['id']
        canonical = section['canonical']
        notes = section.get('notes', '')
        parent = section.get('parent')
        aliases = section.get('aliases', [])[:5]  # First 5 aliases
        wcm_section = section.get('wcm_section_number')

        # Build compact representation
        parts = [
            f"• {section_id}",
            f"  Name: {canonical}",
        ]

        if wcm_section:
            parts.append(f"  WCM Section: #{wcm_section}")

        if parent:
            parts.append(f"  Parent: {parent}")

        if notes:
            parts.append(f"  Description: {notes}")

        if aliases:
            parts.append(f"  Examples: {', '.join(aliases[:3])}")

        options.append('\n'.join(parts))

    return '\n\n'.join(options)


def map_section_to_taxonomy(
    section_label: str,
    sample_entries: list[str],
    taxonomy_options: str
) -> dict[str, Any]:
    """
    Map a CV section to WCM taxonomy using GPT-4o-mini.

    Args:
        section_label: Section label from CV (e.g., "Education and Training")
        sample_entries: 2-3 sample entry snippets from this section
        taxonomy_options: Pre-built string of WCM taxonomy options

    Returns:
        Dictionary with:
        - mapped_section_id: WCM taxonomy ID
        - mapped_canonical_name: WCM canonical name
        - confidence: Confidence score 0.0-1.0
        - reasoning: Brief explanation
        - alternative_matches: List of alternative possibilities
    """
    system_prompt = """You are a CV taxonomy expert mapping academic CV sections to the Weill Cornell Medicine (WCM) CV template taxonomy.

Your task: Given a section label and sample entries from an academic CV, identify the BEST matching WCM taxonomy section.

GUIDELINES:

1. SEMANTIC UNDERSTANDING:
   - Look beyond string similarity - understand the actual content
   - "Service" could be committee service, community service, or professional service
   - "Research" could be research overview, grants, clinical trials, or patents

2. HIERARCHY AWARENESS:
   - Prefer more specific child sections over general parents when possible
   - Example: "Peer-reviewed Articles" is better than general "Bibliography"
   - Example: "Board Certification" is better than general "Licensure and Certification"

3. CONFIDENCE SCORING:
   - 0.95-1.0: Perfect match, unambiguous
   - 0.80-0.94: Strong match, minor ambiguity
   - 0.60-0.79: Reasonable match, some ambiguity
   - 0.40-0.59: Weak match, significant ambiguity
   - 0.00-0.39: Poor match, consider unmapped

4. USE SAMPLE ENTRIES:
   - Sample entries provide critical context
   - "Appointments" with dates/committees → institutional_administration
   - "Appointments" with faculty titles → professional_positions_employment

5. WCM SECTION NUMBERS:
   - WCM sections 1-19 are the template standard
   - Give slight preference to WCM template sections when confidence is similar

6. ALTERNATIVE MATCHES:
   - Provide 1-2 alternative possibilities when ambiguous
   - Order by confidence (highest first)

Return your classification as structured JSON matching the provided schema."""

    # Build user prompt with section info
    user_prompt = f"""Classify this CV section into WCM taxonomy:

SECTION LABEL: "{section_label}"

SAMPLE ENTRIES (first 2-3 items from this section):
"""

    for i, entry in enumerate(sample_entries[:3], 1):
        # Truncate very long entries
        entry_text = entry[:300] + "..." if len(entry) > 300 else entry
        user_prompt += f"{i}. {entry_text}\n"

    user_prompt += f"""

AVAILABLE WCM TAXONOMY SECTIONS:

{taxonomy_options}

Based on the section label and sample entries, identify the BEST matching WCM taxonomy section.
Return structured JSON with your classification and confidence score."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "taxonomy_mapping",
            "strict": True,
            "schema": TAXONOMY_MAPPING_SCHEMA
        }
    }

    # Call LLM
    result_llm = call_llm(
        stage="core_taxonomy_mapper",
        messages=messages,
        response_format=response_format,
        max_tokens=1000,
    )

    # Parse response - guaranteed valid JSON
    mapping = json.loads(result_llm["content"])

    # Normalize the mapped_section_id (convert WCM numbers to section IDs)
    original_id = mapping['mapped_section_id']
    normalized_id = normalize_mapped_section_id(original_id)
    if original_id != normalized_id:
        mapping['mapped_section_id'] = normalized_id
        mapping['original_mapped_id'] = original_id  # Keep for debugging

    # Capture token usage
    mapping['token_usage'] = {
        'prompt_tokens': result_llm["prompt_tokens"],
        'completion_tokens': result_llm["completion_tokens"],
        'total_tokens': result_llm["total_tokens"]
    }

    return mapping


def map_cv_sections(segmented_cv_path: str, output_path: str | None = None) -> dict[str, Any]:
    """
    Map all sections from a segmented CV to WCM taxonomy.

    Args:
        segmented_cv_path: Path to segmented CV JSON (from cv_segmenter.py)
        output_path: Where to save mapped output (defaults to same dir with _mapped.json)

    Returns:
        Dictionary with:
        - mappings: List of section mappings
        - stats: Statistics about mapping quality
        - output_file: Path to output file
    """
    print("="*80)
    print("LLM-BASED TAXONOMY MAPPING")
    print("="*80)
    print(f"Input: {segmented_cv_path}")
    print(f"Using: GPT-4o-mini with Structured Outputs")
    print()

    # Load segmented CV
    with open(segmented_cv_path, 'r') as f:
        segmented_cv = json.load(f)

    # Build taxonomy options once (reuse for all sections)
    print("Building WCM taxonomy reference...")
    taxonomy_options = build_taxonomy_options_string()
    print(f"  ✓ Loaded {len(CV_SECTIONS)} taxonomy sections")
    print()

    # Map each section
    print("Mapping CV sections to WCM taxonomy...")
    print()

    mappings = []
    groups = segmented_cv.get('groups', [])

    # Track token usage
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    def map_group_recursive(group, level=1, parent_path=""):
        """Recursively map a group and all its subgroups."""
        nonlocal total_prompt_tokens, total_completion_tokens, total_tokens

        section_label = group.get('label_inferred', 'Unknown')
        group_id = group.get('id', 'Unknown')
        entries = group.get('entries', [])
        subgroups = group.get('subgroups', [])

        # Get sample entries (first 3) from this group
        sample_entries = []
        for entry in entries[:3]:
            text = entry.get('text_snippet', '')
            if text:
                sample_entries.append(text)

        # Also get sample entries from subgroups if we don't have enough
        if len(sample_entries) < 3:
            for subgroup in subgroups:
                sub_entries = subgroup.get('entries', [])
                for entry in sub_entries[:3]:
                    text = entry.get('text_snippet', '')
                    if text and len(sample_entries) < 3:
                        sample_entries.append(text)

        indent = "  " * level
        path = f"{parent_path}/{section_label}" if parent_path else section_label

        print(f"{indent}[{len(mappings)+1}] Mapping: {section_label}")
        print(f"{indent}    Entries: {len(entries)}, Subgroups: {len(subgroups)}")

        # Map this section
        try:
            mapping = map_section_to_taxonomy(
                section_label=section_label,
                sample_entries=sample_entries,
                taxonomy_options=taxonomy_options
            )

            # Add source info
            mapping['source_label'] = section_label
            mapping['source_group_id'] = group_id
            mapping['num_entries'] = len(entries)
            mapping['num_subgroups'] = len(subgroups)
            mapping['level'] = level
            mapping['path'] = path

            # Aggregate token usage
            if 'token_usage' in mapping:
                usage = mapping['token_usage']
                total_prompt_tokens += usage.get('prompt_tokens', 0)
                total_completion_tokens += usage.get('completion_tokens', 0)
                total_tokens += usage.get('total_tokens', 0)

            mappings.append(mapping)

            print(f"{indent}    → {mapping['mapped_canonical_name']}")
            print(f"{indent}       Confidence: {mapping['confidence']:.2f}")
            if mapping['confidence'] < 0.8:
                print(f"{indent}       ⚠️  Low confidence - {mapping['reasoning']}")

        except Exception as e:
            print(f"{indent}    ✗ Error: {e}")
            # Add unmapped entry
            mappings.append({
                'source_label': section_label,
                'source_group_id': group_id,
                'mapped_section_id': 'UNMAPPED',
                'mapped_canonical_name': 'UNMAPPED',
                'confidence': 0.0,
                'reasoning': f"Error during mapping: {str(e)}",
                'alternative_matches': [],
                'num_entries': len(entries),
                'num_subgroups': len(subgroups),
                'level': level,
                'path': path
            })

        print()

        # Recursively map all subgroups
        for subgroup in subgroups:
            map_group_recursive(subgroup, level + 1, path)

    # Map all top-level groups recursively
    for group in groups:
        map_group_recursive(group, level=1)

    # Calculate stats
    total_sections = len(mappings)
    high_confidence = sum(1 for m in mappings if m['confidence'] >= 0.8)
    medium_confidence = sum(1 for m in mappings if 0.6 <= m['confidence'] < 0.8)
    low_confidence = sum(1 for m in mappings if m['confidence'] < 0.6)
    unmapped = sum(1 for m in mappings if m['mapped_section_id'] == 'UNMAPPED')

    avg_confidence = sum(m['confidence'] for m in mappings) / total_sections if total_sections > 0 else 0.0

    stats = {
        'total_sections': total_sections,
        'high_confidence': high_confidence,
        'medium_confidence': medium_confidence,
        'low_confidence': low_confidence,
        'unmapped': unmapped,
        'avg_confidence': round(avg_confidence, 3)
    }

    # Build output
    output_data = {
        'document_uid': segmented_cv.get('document_uid'),
        'source_file': segmented_cv_path,
        'meta': {
            **segmented_cv.get('meta', {}),
            'mapping_stats': stats,
            'mapping_approach': 'LLM-based (GPT-4o-mini with Structured Outputs)'
        },
        'mappings': mappings
    }

    # Write output
    if output_path is None:
        input_path = Path(segmented_cv_path)
        output_path = input_path.parent / (input_path.stem.replace('_segmented', '') + '_mapped.json')

    output_path = Path(output_path)

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    # Print results
    print("="*80)
    print("MAPPING RESULTS")
    print("="*80)
    print(f"Total sections: {total_sections}")
    print(f"  High confidence (≥0.8): {high_confidence} ({high_confidence/total_sections*100:.1f}%)")
    print(f"  Medium confidence (0.6-0.79): {medium_confidence} ({medium_confidence/total_sections*100:.1f}%)")
    print(f"  Low confidence (<0.6): {low_confidence} ({low_confidence/total_sections*100:.1f}%)")
    if unmapped > 0:
        print(f"  Unmapped (errors): {unmapped}")
    print(f"Average confidence: {avg_confidence:.3f}")
    print()
    print(f"✓ Results saved to: {output_path}")

    file_size_kb = output_path.stat().st_size / 1024
    print(f"File size: {file_size_kb:.1f} KB")
    print()

    return {
        'mappings': mappings,
        'stats': stats,
        'output_file': str(output_path),
        'token_usage': {
            'prompt_tokens': total_prompt_tokens,
            'completion_tokens': total_completion_tokens,
            'total_tokens': total_tokens
        }
    }


def main():
    """
    Command-line interface for taxonomy mapping.

    Usage:
        python taxonomy_mapper.py <segmented_cv_path> [output_path]

    Examples:
        python taxonomy_mapper.py cv_word_segmented.json
        python taxonomy_mapper.py cv_word_segmented.json ./output/cv_mapped.json
    """
    if len(sys.argv) < 2:
        print("LLM-based Taxonomy Mapper")
        print()
        print("Usage: python taxonomy_mapper.py <segmented_cv_path> [output_path]")
        print()
        print("Maps segmented CV sections to WCM taxonomy using GPT-4o-mini")
        print()
        print("Examples:")
        print("  python taxonomy_mapper.py cv_word_segmented.json")
        print("  python taxonomy_mapper.py cv_word_segmented.json ./output/cv_mapped.json")
        print()
        sys.exit(1)

    segmented_cv_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(segmented_cv_path):
        print(f"Error: File not found: {segmented_cv_path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = map_cv_sections(segmented_cv_path, output_path)
        sys.exit(0)

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
