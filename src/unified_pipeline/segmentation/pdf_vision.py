"""
Three-Pass Vision Segmentation - ROBUST VERSION

Strategy:
Pass 1: GESTALT - See entire document, identify all sections (all pages @ 150 DPI)
Pass 2: TRIAGE - Count items per section, decide if chunking needed (per section @ 150 DPI)
Pass 3: EXTRACTION - Extract entries, chunking large sections (@ 250 DPI)

Key insight: We avoid JSON failures by never asking for >40 entries in one call.
"""

import os
import json
import math
from pathlib import Path
from pdf2image import convert_from_path
from io import BytesIO
from openai import OpenAI
import base64

# Use default environment context to avoid expensive SKU mapping
client = OpenAI()

# Thresholds for chunking decisions
MAX_ENTRIES_PER_CALL = 40  # Never extract more than this in one call
MAX_PAGES_PER_CALL = 6     # Never process more than this many pages at once


def pdf_to_images(pdf_path: str, pages: list = None, dpi: int = 200) -> list:
    """
    Convert PDF pages to images.

    Args:
        pdf_path: Path to PDF
        pages: Specific page numbers (1-indexed), or None for all
        dpi: Resolution

    Returns:
        List of base64-encoded images
    """
    if pages:
        # Convert specific pages only
        images = convert_from_path(pdf_path, dpi=dpi, first_page=min(pages), last_page=max(pages))
        # Filter to exact pages requested
        page_indices = [p - min(pages) for p in pages]
        images = [images[i] for i in page_indices if i < len(images)]
    else:
        # Convert all pages
        images = convert_from_path(pdf_path, dpi=dpi)

    # Convert to base64 JPEG
    base64_images = []
    for image in images:
        buffered = BytesIO()
        image.save(buffered, format="JPEG", quality=75)
        img_str = base64.b64encode(buffered.getvalue()).decode()
        base64_images.append(img_str)

    return base64_images


def pass1_gestalt(pdf_path: str) -> dict:
    """
    Pass 1: GESTALT - Analyze entire CV for global structure.

    Goal: See the whole document, identify all sections and page spans.
    This preserves the "gestalt" insight before we chunk anything.
    """
    print("\n" + "="*80)
    print("PASS 1: GESTALT (Global Structure)")
    print("="*80)

    # Convert all pages at low resolution
    print("Converting all pages (low res)...")
    images = pdf_to_images(pdf_path, dpi=150)  # Low DPI for overview
    print(f"  Converted {len(images)} pages")

    system_prompt = """You are analyzing the OVERALL STRUCTURE of an academic CV.

Your task:
1. Identify ALL major section headers (EDUCATION, PUBLICATIONS, TEACHING, etc.)
2. Note the page span for each section
3. Make a rough estimate of entry count

DO NOT extract individual entries - just map out where things are.

Return JSON:
{
  "num_pages": N,
  "sections": [
    {
      "section_id": "S1",
      "label": "Education and Training",
      "page_start": 1,
      "page_end": 1,
      "estimated_entry_count": 4
    }
  ]
}"""

    user_prompt = f"""Analyze this {len(images)}-page academic CV.

Identify ALL sections and their page spans.
Focus on STRUCTURE, not content."""

    # Build content with all images (low res)
    content = [{"type": "text", "text": user_prompt}]
    for img in images:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{img}",
                "detail": "low"
            }
        })

    print("Sending to GPT-4o...")
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content}
        ],
        max_tokens=4000,
        temperature=0.1
    )

    # Parse response
    response_text = response.choices[0].message.content

    # Extract JSON
    if "```json" in response_text:
        json_start = response_text.find("```json") + 7
        json_end = response_text.rfind("```")
        json_text = response_text[json_start:json_end].strip()
    elif "```" in response_text:
        json_start = response_text.find("```") + 3
        json_end = response_text.rfind("```")
        json_text = response_text[json_start:json_end].strip()
    else:
        json_text = response_text.strip()

    structure = json.loads(json_text)
    print(f"✓ Detected {len(structure.get('sections', []))} sections")

    return structure


def pass2_triage(pdf_path: str, section: dict) -> dict:
    """
    Pass 2: TRIAGE - Count items in section, decide if chunking needed.

    Goal: Determine if this section is small enough to extract in one call,
    or needs to be chunked for Pass 3.

    Uses LOW resolution to keep tokens down - we're just counting, not extracting.
    """
    section_id = section['section_id']
    label = section['label']
    page_start = section['page_start']
    page_end = section['page_end']
    page_count = page_end - page_start + 1

    print(f"\n  Triaging: {label} ({page_count} pages)")

    # Convert section pages at low resolution (just for counting)
    pages = list(range(page_start, page_end + 1))
    images = pdf_to_images(pdf_path, pages=pages, dpi=150)  # Low res = fast + cheap

    system_prompt = f"""You are triaging an academic CV section to decide extraction strategy.

Your task: COUNT the number of individual entries in this section.

For example:
- Education section with 4 degrees → count = 4
- Publications section with 87 papers → count = 87
- Awards section with 12 honors → count = 12

DO NOT extract the entries - just COUNT them.

Return JSON:
{{
  "entry_count": N,
  "section_type": "publications" | "positions" | "education" | "awards" | "other"
}}"""

    user_prompt = f"""Count individual entries in: "{label}" (pages {page_start}-{page_end})

How many distinct items are in this section?"""

    # Build content
    content = [{"type": "text", "text": user_prompt}]
    for img in images:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{img}",
                "detail": "low"
            }
        })

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content}
        ],
        max_tokens=500,  # Very small - just need a count
        temperature=0.1
    )

    # Parse response
    response_text = response.choices[0].message.content

    if "```json" in response_text:
        json_start = response_text.find("```json") + 7
        json_end = response_text.rfind("```")
        json_text = response_text[json_start:json_end].strip()
    elif "```" in response_text:
        json_start = response_text.find("```") + 3
        json_end = response_text.rfind("```")
        json_text = response_text[json_start:json_end].strip()
    else:
        json_text = response_text.strip()

    triage_result = json.loads(json_text)
    entry_count = triage_result.get('entry_count', 0)

    # Decide: extract now, or chunk for Pass 3?
    needs_chunking = (entry_count > MAX_ENTRIES_PER_CALL) or (page_count > MAX_PAGES_PER_CALL)

    if needs_chunking:
        # Calculate chunk strategy
        # Target: ~30 entries per chunk, or 3 pages per chunk (whichever is more conservative)
        entries_per_chunk = min(MAX_ENTRIES_PER_CALL, 30)
        num_chunks_by_entries = math.ceil(entry_count / entries_per_chunk)
        num_chunks_by_pages = math.ceil(page_count / 3)
        num_chunks = max(num_chunks_by_entries, num_chunks_by_pages)

        print(f"    → {entry_count} entries, {page_count} pages → CHUNKING into {num_chunks} chunks")

        return {
            'section': section,
            'entry_count': entry_count,
            'needs_chunking': True,
            'num_chunks': num_chunks
        }
    else:
        print(f"    → {entry_count} entries, {page_count} pages → Extract now")

        return {
            'section': section,
            'entry_count': entry_count,
            'needs_chunking': False
        }


def pass3_extract(pdf_path: str, section: dict, page_start: int, page_end: int,
                  chunk_label: str = None) -> dict:
    """
    Pass 3: EXTRACTION - Extract detailed entries from a section or chunk.

    This is called either:
    1. Directly after Pass 2 (if section is small)
    2. Multiple times for chunks (if section is large)

    Uses HIGH resolution for quality extraction.
    """
    label = section['label']
    display_label = chunk_label if chunk_label else label

    print(f"    Extracting: {display_label} (pages {page_start}-{page_end})")

    # Convert pages at high resolution
    pages = list(range(page_start, page_end + 1))
    images = pdf_to_images(pdf_path, pages=pages, dpi=250)  # High DPI for quality

    system_prompt = f"""Extract ALL individual entries from this CV section with metadata.

CRITICAL: EXTREME GRANULARITY
- Each publication, position, award gets its own entry
- Do NOT summarize or group items

METADATA FIELDS:
- entry_type: Classify as "publication", "lecture", "degree", "position", "license", "certification", "award", "society", "appointment", "editorial", "teaching", "clinical", "research", "grant", "presentation", or "other"
- order_index: Sequential number within this section (1, 2, 3...)
- confidence: Your confidence score (0.0-1.0) that this is a correctly identified entry

Return JSON:
{{
  "groups": [
    {{
      "id": "G1",
      "level": 1,
      "label_inferred": "{label}",
      "entries": [
        {{
          "id": "G1-E1",
          "text_snippet": "First 200 chars of entry...",
          "page": {page_start},
          "entry_type": "publication",
          "order_index": 1,
          "confidence": 0.95
        }}
      ]
    }}
  ]
}}

Keep entries concise but complete. Provide accurate entry_type classification for downstream processing."""

    user_prompt = f"""Extract all entries from: "{display_label}"

Return granular JSON with EVERY individual item."""

    # Build content
    content = [{"type": "text", "text": user_prompt}]
    for img in images:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{img}",
                "detail": "high"
            }
        })

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content}
        ],
        max_tokens=12000,
        temperature=0.1
    )

    # Parse response
    response_text = response.choices[0].message.content

    if "```json" in response_text:
        json_start = response_text.find("```json") + 7
        json_end = response_text.rfind("```")
        json_text = response_text[json_start:json_end].strip()
    elif "```" in response_text:
        json_start = response_text.find("```") + 3
        json_end = response_text.rfind("```")
        json_text = response_text[json_start:json_end].strip()
    else:
        json_text = response_text.strip()

    extracted = json.loads(json_text)

    # Count entries
    entry_count = 0
    for group in extracted.get('groups', []):
        entry_count += len(group.get('entries', []))

    print(f"      → {entry_count} entries")

    return extracted


def segment_cv_three_pass(pdf_path: str, output_dir: str = None) -> dict:
    """
    Main three-pass segmentation pipeline.

    Pass 1: Gestalt (see whole document)
    Pass 2: Triage (count items, decide chunking)
    Pass 3: Extract (get entries, chunking large sections)
    """
    print("="*80)
    print("THREE-PASS VISION SEGMENTATION")
    print("="*80)
    print(f"PDF: {pdf_path}")
    print()
    print("Strategy:")
    print("  Pass 1: GESTALT - Map all sections (preserves context)")
    print("  Pass 2: TRIAGE - Count items, decide chunking")
    print("  Pass 3: EXTRACT - Get entries (chunked if needed)")
    print()

    # Pass 1: Gestalt
    global_structure = pass1_gestalt(pdf_path)

    # Pass 2: Triage all sections
    print("\n" + "="*80)
    print("PASS 2: TRIAGE (Count & Decide)")
    print("="*80)

    sections = global_structure.get('sections', [])
    triage_results = []

    for section in sections:
        triage_result = pass2_triage(pdf_path, section)
        triage_results.append(triage_result)

    # Pass 3: Extract (with chunking as needed)
    print("\n" + "="*80)
    print("PASS 3: EXTRACTION (Chunked)")
    print("="*80)

    all_groups = []
    total_entries = 0

    for triage in triage_results:
        section = triage['section']

        if not triage['needs_chunking']:
            # Small section: extract directly
            print(f"\n  {section['label']} (direct extraction)")
            extracted = pass3_extract(pdf_path, section,
                                     section['page_start'], section['page_end'])
            all_groups.extend(extracted.get('groups', []))
            total_entries += sum(len(g.get('entries', [])) for g in extracted.get('groups', []))
        else:
            # Large section: chunk it
            print(f"\n  {section['label']} (chunked extraction)")

            page_start = section['page_start']
            page_end = section['page_end']
            page_count = page_end - page_start + 1
            num_chunks = triage['num_chunks']
            pages_per_chunk = math.ceil(page_count / num_chunks)

            chunk_groups = []
            for chunk_idx in range(num_chunks):
                chunk_start = page_start + (chunk_idx * pages_per_chunk)
                chunk_end = min(chunk_start + pages_per_chunk - 1, page_end)

                chunk_label = f"{section['label']} (part {chunk_idx + 1}/{num_chunks})"

                try:
                    extracted = pass3_extract(pdf_path, section, chunk_start, chunk_end, chunk_label)
                    chunk_groups.extend(extracted.get('groups', []))
                    total_entries += sum(len(g.get('entries', [])) for g in extracted.get('groups', []))
                except Exception as e:
                    print(f"      ✗ Error: {e}")

            # Merge chunks into single group
            if chunk_groups:
                merged_entries = []
                for g in chunk_groups:
                    merged_entries.extend(g.get('entries', []))

                merged_group = {
                    'id': f"G{len(all_groups) + 1}",
                    'level': 1,
                    'label_inferred': section['label'],
                    'page_span': [page_start, page_end],
                    'entries': merged_entries
                }
                all_groups.append(merged_group)

    # Write output using OutputManager for consistent paths
    from ..core.output_manager import OutputManager

    if output_dir is None:
        # Use OutputManager for standard structure
        om = OutputManager(pdf_path)
        output_file_path = om.get_stage1_json_path()
        output_txt_path = om.get_stage1_txt_path()
    else:
        # Use specified output directory
        output_dir = Path(output_dir)
        stem = Path(pdf_path).stem
        output_file_path = output_dir / f"{stem}_segmented.json"
        output_txt_path = output_dir / f"{stem}_segmented.txt"

    output_data = {
        'document_uid': 'three_pass',
        'meta': {
            'num_pages': global_structure.get('num_pages', 0),
            'num_top_level_groups': len(all_groups),
            'total_entries': total_entries,
            'processing_notes': 'Three-pass: gestalt preserved, chunked extraction'
        },
        'groups': all_groups
    }

    # Save JSON output
    with open(output_file_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print("\n" + "="*80)
    print("RESULTS")
    print("="*80)
    print(f"Total sections: {len(all_groups)}")
    print(f"Total entries: {total_entries}")
    print(f"\n✓ JSON saved to: {output_file_path}")

    # Also save human-readable text version
    try:
        with open(output_txt_path, 'w', encoding='utf-8') as f:
            f.write(f"CV Segmentation (PDF): {Path(pdf_path).stem}\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Total sections: {len(all_groups)}\n")
            f.write(f"Total entries: {total_entries}\n\n")

            for group in all_groups:
                label = group.get('label_inferred', 'NO LABEL')
                page_span = group.get('page_span', [])
                entries = group.get('entries', [])
                f.write(f"[{group.get('level', 'UNKNOWN')}] {label} (Pages {page_span[0]}-{page_span[1]})\n")
                for entry in entries[:10]:  # Show first 10 entries
                    entry_text = entry.get('text', '')[:100]
                    f.write(f"  - {entry_text}...\n")
                if len(entries) > 10:
                    f.write(f"  ... and {len(entries) - 10} more entries\n")
                f.write("\n")

        print(f"✓ TXT saved to: {output_txt_path}")
    except Exception as e:
        print(f"⚠ Could not save TXT version: {e}")

    file_size_kb = output_file_path.stat().st_size / 1024
    print(f"File size: {file_size_kb:.1f} KB")

    return {
        'num_sections': len(all_groups),
        'total_entries': total_entries,
        'output_file': str(output_file_path)
    }


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python three_pass_vision_segmentation.py <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    if not os.path.exists(pdf_path):
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)

    segment_cv_three_pass(pdf_path)


if __name__ == '__main__':
    main()
