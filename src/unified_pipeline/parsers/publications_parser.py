"""
Publications Parser - Phase 4

Extracts structured data from publication entries using:
1. Word metadata (when available) for structure hints
2. GPT-4o-mini with Structured Outputs for field extraction
3. Regex patterns for DOI, PMID, PMC extraction

Strategy:
- For Word docs: Use table structure metadata to guide parsing
- For PDF docs: Use text patterns only
- For both: LLM extracts authors, title, journal, year, DOI, PMID

Output: Structured publication records ready for database insertion
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from unified_pipeline.llm_client import call_llm


# Publication schema for Structured Outputs
PUBLICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "authors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of author names in order"
        },
        "title": {
            "type": "string",
            "description": "Publication title"
        },
        "journal": {
            "type": "string",
            "description": "Journal or venue name"
        },
        "year": {
            "type": "integer",
            "description": "Publication year"
        },
        "volume": {
            "type": "string",
            "description": "Volume number if available, empty string if not"
        },
        "issue": {
            "type": "string",
            "description": "Issue number if available, empty string if not"
        },
        "pages": {
            "type": "string",
            "description": "Page range if available, empty string if not"
        },
        "doi": {
            "type": "string",
            "description": "DOI if available, empty string if not"
        },
        "pmid": {
            "type": "string",
            "description": "PubMed ID if available, empty string if not"
        },
        "pmcid": {
            "type": "string",
            "description": "PubMed Central ID if available, empty string if not"
        },
        "publication_type": {
            "type": "string",
            "enum": ["journal_article", "review", "editorial", "book_chapter", "conference", "abstract", "preprint", "other"],
            "description": "Type of publication"
        },
        "confidence": {
            "type": "number",
            "description": "Confidence score 0.0-1.0 for extraction quality"
        }
    },
    "required": ["authors", "title", "journal", "year", "volume", "issue", "pages", "doi", "pmid", "pmcid", "publication_type", "confidence"],
    "additionalProperties": False
}


def extract_identifiers_with_regex(text: str) -> Dict[str, Optional[str]]:
    """
    Extract DOI, PMID, PMCID using regex patterns.

    These are fast and deterministic - try these first before LLM.
    """
    identifiers = {
        "doi": None,
        "pmid": None,
        "pmcid": None
    }

    # DOI patterns
    doi_patterns = [
        r'doi:?\s*(\S+)',
        r'https?://doi\.org/(\S+)',
        r'10\.\d{4,}/\S+'
    ]

    for pattern in doi_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            doi = match.group(1) if len(match.groups()) > 0 else match.group(0)
            # Clean up common artifacts
            doi = doi.rstrip('.,;)')
            identifiers["doi"] = doi
            break

    # PMID patterns
    pmid_patterns = [
        r'PMID:?\s*(\d{7,8})',
        r'pubmed[:/]\s*(\d{7,8})'
    ]

    for pattern in pmid_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            identifiers["pmid"] = match.group(1)
            break

    # PMCID patterns
    pmcid_patterns = [
        r'PMCID:?\s*(PMC\d+)',
        r'PMC\d+'
    ]

    for pattern in pmcid_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            pmcid = match.group(1) if len(match.groups()) > 0 else match.group(0)
            identifiers["pmcid"] = pmcid
            break

    return identifiers


def parse_publication_entry(text: str, element_metadata: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Parse a single publication entry into structured fields.

    Args:
        text: Publication citation text
        element_metadata: Optional Word metadata (table structure, etc.)

    Returns:
        Structured publication record
    """
    # Step 1: Extract identifiers with regex (fast, deterministic)
    identifiers = extract_identifiers_with_regex(text)

    # Step 2: Use GPT-4o-mini to extract remaining fields
    system_prompt = """You are a bibliographic citation parser. Extract structured data from academic publication citations.

GUIDELINES:

1. AUTHOR PARSING:
   - Extract all authors in order
   - Format: "Last FM" or "Last First Middle"
   - Handle "et al." by noting all listed authors

2. TITLE EXTRACTION:
   - Complete article title
   - Remove quotes if present

3. JOURNAL PARSING:
   - Full journal name or standard abbreviation
   - For books: book title
   - For conferences: conference name

4. YEAR:
   - Publication year as integer

5. VOLUME/ISSUE/PAGES:
   - Extract if present in standard formats:
     - "2020;15(3):123-45"
     - "Vol 15, No 3, pp 123-45"
   - Use empty string "" if not available

6. IDENTIFIERS:
   - DOI, PMID, PMCID if present
   - Clean format (no labels)
   - Use empty string "" if not available

7. TYPE CLASSIFICATION:
   - journal_article: Standard research paper
   - review: Review article or systematic review
   - editorial: Editorial or commentary
   - book_chapter: Book chapter
   - conference: Conference proceeding
   - abstract: Conference abstract
   - preprint: Preprint or manuscript
   - other: Other types

8. CONFIDENCE:
   - 0.9-1.0: Complete, well-formatted citation
   - 0.7-0.89: Most fields present, some ambiguity
   - 0.5-0.69: Incomplete or poorly formatted
   - 0.0-0.49: Very incomplete or unrecognizable

Return structured JSON matching the schema."""

    user_prompt = f"""Parse this publication citation:

{text}

Extract all available fields following the schema."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "publication_record",
            "strict": True,
            "schema": PUBLICATION_SCHEMA
        }
    }

    result = call_llm(stage="parser_publications", messages=messages, response_format=response_format)

    # Parse response
    publication = json.loads(result["content"])

    publication['token_usage'] = {
        'prompt_tokens': result["prompt_tokens"],
        'completion_tokens': result["completion_tokens"],
        'total_tokens': result["total_tokens"]
    }

    # Step 3: Merge regex identifiers with LLM output (prefer regex for identifiers)
    if identifiers["doi"]:
        publication["doi"] = identifiers["doi"]
    if identifiers["pmid"]:
        publication["pmid"] = identifiers["pmid"]
    if identifiers["pmcid"]:
        publication["pmcid"] = identifiers["pmcid"]

    # Add source metadata if available
    if element_metadata:
        publication["source_metadata"] = element_metadata

    return publication


def extract_target_author_from_uid(document_uid: str) -> Optional[str]:
    """
    Extract the target author name from document UID.

    Example: "CV_Kathleen_E_Simpson_MD" → "Simpson KE"
    """
    if not document_uid:
        return None

    # Remove common prefixes/suffixes
    uid = document_uid.replace("CV_", "").replace("_MD", "").replace("_PhD", "").replace("_DO", "")

    # Split by underscore
    parts = uid.split("_")

    if len(parts) < 2:
        return None

    # Last part is likely last name
    last_name = parts[-1]

    # First/middle initials from remaining parts
    initials = "".join([p[0].upper() for p in parts[:-1] if p])

    return f"{last_name} {initials}"


def find_target_author_in_list(authors: List[str], target_author: str) -> Optional[int]:
    """
    Find the index of the target author in the author list using multi-level heuristics.

    Matching strategy (in order of priority):
    1. Exact full name match (e.g., "Simpson KE" == "Simpson KE")
    2. Last name + first initial match (e.g., "Simpson K" matches "Simpson KE")
    3. Partial substring match (e.g., "Simpson" in "Simpson KE")

    Returns index (0-based) or None if:
    - Not found
    - Multiple ambiguous matches at the same level

    Args:
        authors: List of author strings
        target_author: Target author name (e.g., "Simpson KE")

    Returns:
        Index of matched author or None
    """
    if not target_author or not authors:
        return None

    target_parts = target_author.split()
    if len(target_parts) < 2:
        return None

    target_last = target_parts[0]
    target_first_initial = target_parts[1][0] if len(target_parts) > 1 else ""

    # Level 1: Exact match
    exact_matches = []
    for idx, author in enumerate(authors):
        author_norm = author.strip().replace(",", "").replace(".", "")
        target_norm = target_author.replace(",", "").replace(".", "")
        if author_norm == target_norm:
            exact_matches.append(idx)

    if len(exact_matches) == 1:
        return exact_matches[0]
    elif len(exact_matches) > 1:
        # Multiple exact matches - ambiguous, return None
        return None

    # Level 2: Last name + first initial match
    last_first_matches = []
    for idx, author in enumerate(authors):
        author_norm = author.strip().replace(",", "").replace(".", "")

        # Check if last name is in author string
        if target_last not in author_norm:
            continue

        # Check if first initial is present
        if target_first_initial and target_first_initial in author_norm:
            # Additional check: make sure last name comes before or near the initial
            last_pos = author_norm.find(target_last)
            initial_pos = author_norm.find(target_first_initial)

            # Allow last name first (Simpson KE) or initial first (KE Simpson)
            if abs(last_pos - initial_pos) < len(target_last) + 5:
                last_first_matches.append(idx)

    if len(last_first_matches) == 1:
        return last_first_matches[0]
    elif len(last_first_matches) > 1:
        # Multiple matches - ambiguous, return None
        return None

    # Level 3: Partial substring match (last name only)
    substring_matches = []
    for idx, author in enumerate(authors):
        author_norm = author.strip().replace(",", "").replace(".", "")
        if target_last in author_norm:
            substring_matches.append(idx)

    if len(substring_matches) == 1:
        return substring_matches[0]
    elif len(substring_matches) > 1:
        # Multiple matches - ambiguous, return None
        return None

    # No matches found
    return None


def parse_publications_section(
    entries: List[Dict[str, Any]],
    section_metadata: Optional[Dict] = None,
    target_author: Optional[str] = None
) -> Dict[str, Any]:
    """
    Parse all entries in a publications section.

    Args:
        entries: List of publication entries from segmentation
        section_metadata: Section-level metadata (taxonomy mapping, etc.)
        target_author: Target author name (e.g., "Simpson KE") to identify in publications

    Returns:
        Dictionary with:
        - publications: List of structured publication records
        - token_usage: Token usage statistics
    """
    print(f"Parsing {len(entries)} publication entries...")
    if target_author:
        print(f"  Target author: {target_author}")

    parsed_publications = []

    # Track token usage
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    for idx, entry in enumerate(entries, 1):
        text = entry.get("text_snippet", "")
        element_idx = entry.get("element_idx")

        if not text or len(text.strip()) < 20:
            # Skip empty or very short entries
            continue

        print(f"  [{idx}/{len(entries)}] Parsing entry...")

        try:
            # Parse entry
            publication = parse_publication_entry(
                text=text,
                element_metadata={"element_idx": element_idx} if element_idx else None
            )

            # Add original entry metadata
            publication["entry_id"] = entry.get("id")
            publication["order_index"] = entry.get("order_index")
            publication["original_text"] = text

            # Add target author metadata
            if target_author:
                author_idx = find_target_author_in_list(publication.get("authors", []), target_author)
                if author_idx is not None:
                    publication["target_author_index"] = author_idx
                    publication["is_first_author"] = (author_idx == 0)
                    publication["is_last_author"] = (author_idx == len(publication.get("authors", [])) - 1)
                else:
                    publication["target_author_index"] = None
                    publication["is_first_author"] = False
                    publication["is_last_author"] = False

            # Aggregate token usage
            if 'token_usage' in publication:
                usage = publication['token_usage']
                total_prompt_tokens += usage.get('prompt_tokens', 0)
                total_completion_tokens += usage.get('completion_tokens', 0)
                total_tokens += usage.get('total_tokens', 0)

            parsed_publications.append(publication)

            # Show progress
            confidence = publication.get("confidence", 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️" if confidence >= 0.6 else "✗"
            print(f"      {conf_emoji} {publication['title'][:60]}... (conf: {confidence:.2f})")

        except Exception as e:
            print(f"      ✗ Error: {e}")
            # Add unparsed entry with error info
            parsed_publications.append({
                "entry_id": entry.get("id"),
                "order_index": entry.get("order_index"),
                "original_text": text,
                "parse_error": str(e),
                "confidence": 0.0
            })

    return {
        'publications': parsed_publications,
        'token_usage': {
            'prompt_tokens': total_prompt_tokens,
            'completion_tokens': total_completion_tokens,
            'total_tokens': total_tokens
        }
    }


def main():
    """
    Test publications parser on sample data.
    """
    import sys

    if len(sys.argv) < 2:
        print("Publications Parser - Phase 4")
        print()
        print("Usage: python publications_parser.py <segmented_cv_path>")
        print()
        print("Extracts structured publication data from segmented CV JSON.")
        print()
        sys.exit(1)

    segmented_cv_path = sys.argv[1]

    # Load segmented CV
    with open(segmented_cv_path, 'r') as f:
        segmented_cv = json.load(f)

    # Extract target author from document UID
    document_uid = segmented_cv.get("document_uid", "")
    target_author = extract_target_author_from_uid(document_uid)

    # Find publications sections
    print("="*80)
    print("PUBLICATIONS PARSER")
    print("="*80)
    print(f"Input: {segmented_cv_path}")
    if target_author:
        print(f"Target author: {target_author}")
    print()

    all_parsed = []

    for group in segmented_cv.get("groups", []):
        label = group.get("label_inferred", "")

        # Check if this is a publications-related section
        if any(keyword in label.lower() for keyword in ["publication", "article", "paper", "bibliography"]):
            print(f"Processing: {label}")
            print(f"Entries: {len(group.get('entries', []))}")
            print()

            parsed = parse_publications_section(
                entries=group.get("entries", []),
                section_metadata={"section_label": label},
                target_author=target_author
            )

            all_parsed.extend(parsed)
            print()

        # Check subgroups
        for subgroup in group.get("subgroups", []):
            sub_label = subgroup.get("label_inferred", "")
            if any(keyword in sub_label.lower() for keyword in ["publication", "article", "paper"]):
                print(f"Processing: {label} → {sub_label}")
                print(f"Entries: {len(subgroup.get('entries', []))}")
                print()

                parsed = parse_publications_section(
                    entries=subgroup.get("entries", []),
                    section_metadata={"section_label": label, "subsection_label": sub_label},
                    target_author=target_author
                )

                all_parsed.extend(parsed)
                print()

    # Write output
    output_path = Path(segmented_cv_path).parent / (Path(segmented_cv_path).stem.replace('_segmented', '') + '_publications_parsed.json')

    output_data = {
        "source_file": segmented_cv_path,
        "total_publications": len(all_parsed),
        "high_confidence": sum(1 for p in all_parsed if p.get("confidence", 0) >= 0.8),
        "medium_confidence": sum(1 for p in all_parsed if 0.6 <= p.get("confidence", 0) < 0.8),
        "low_confidence": sum(1 for p in all_parsed if p.get("confidence", 0) < 0.6),
        "publications": all_parsed
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print("="*80)
    print("RESULTS")
    print("="*80)
    print(f"Total publications: {len(all_parsed)}")
    print(f"  High confidence (≥0.8): {output_data['high_confidence']}")
    print(f"  Medium confidence (0.6-0.79): {output_data['medium_confidence']}")
    print(f"  Low confidence (<0.6): {output_data['low_confidence']}")
    print()
    print(f"✓ Results saved to: {output_path}")
    print()


if __name__ == '__main__':
    main()
