"""
Chunked Chat Completions API hierarchy extraction with existing normalization.

This hybrid approach:
1. Converts DOCX to text and splits into ~10k token chunks
2. Extracts headers from each chunk independently via GPT-4o
3. Combines all chunk outlines
4. Applies existing Step 7 normalization (synthetic headers, semantic grouping)
5. Applies existing Step 9 normalization (final cleanup, no synthetic headers)

Advantages:
- Handles CVs of any size (no token limit violations)
- Uses existing proven normalization logic
- More cost-effective than Assistants API
- Deterministic and controllable

Cost estimate: ~$0.05-$0.15 per CV for most CVs, ~$0.30-$0.50 for very large CVs
"""

import os
import sys
from typing import List, Dict, Tuple
from dataclasses import dataclass, field
from openai import OpenAI
from openai import RateLimitError
import re
from docx import Document
import tiktoken
import time

# Add parent directory to path to import from signature_based_segmentation
sys.path.insert(0, os.path.dirname(__file__))
from signature_based_segmentation import normalize_hierarchy_with_llm, validate_headers_vs_entries, ensure_personal_data_first


def format_hierarchy_outline(hierarchy: List[Dict]) -> str:
    """
    Format a hierarchy tree as an indented outline string.

    Args:
        hierarchy: List of dicts with 'level', 'text', and 'children' keys

    Returns:
        Formatted outline text
    """
    lines = []

    def walk(node: Dict, indent_level: int = 0):
        """Recursively walk the tree and format each node."""
        level = node['level']
        text = node['text']
        indent = "  " * indent_level
        lines.append(f"{indent}[{level}] {text}")

        # Recursively process children
        for child in node.get('children', []):
            walk(child, indent_level + 1)

    # Process all root nodes
    for root in hierarchy:
        walk(root)

    return "\n".join(lines)


# Initialize OpenAI client
client = OpenAI()

# Initialize tiktoken encoder for token counting
encoder = tiktoken.encoding_for_model("gpt-4o")


@dataclass
class HeaderNode:
    """Represents a header in the CV hierarchy."""
    level: int  # 1, 2, or 3
    text: str
    children: List["HeaderNode"] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'level': f'H{self.level}',
            'text': self.text,
            'children': [child.to_dict() for child in self.children]
        }


def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken."""
    return len(encoder.encode(text))


def extract_text_from_docx(docx_path: str) -> List[str]:
    """
    Extract all text from a DOCX file as a list of paragraphs and table content.

    Preserves document order by iterating through the document element tree.

    Args:
        docx_path: Path to the DOCX file

    Returns:
        List of text elements (paragraphs and table rows) in document order
    """
    doc = Document(docx_path)
    text_elements = []

    # Iterate through document body elements to preserve order
    for element in doc.element.body:
        # Check if it's a paragraph
        if element.tag.endswith('}p'):
            # Find the corresponding paragraph object
            for para in doc.paragraphs:
                if para._element == element:
                    text_elements.append(para.text)
                    break
        # Check if it's a table
        elif element.tag.endswith('}tbl'):
            # Find the corresponding table object
            for table in doc.tables:
                if table._element == element:
                    # Extract all rows from this table
                    for row in table.rows:
                        # Join all cells in the row with tab separator
                        row_text = "\t".join(cell.text.strip() for cell in row.cells)
                        if row_text.strip():  # Only add non-empty rows
                            text_elements.append(row_text)
                    break

    return text_elements


def split_into_chunks(paragraphs: List[str], max_tokens: int = 10000) -> List[str]:
    """
    Split paragraphs into chunks of approximately max_tokens each.

    Chunks are created at paragraph boundaries to preserve document structure.

    Args:
        paragraphs: List of paragraph texts
        max_tokens: Maximum tokens per chunk (default: 10000)

    Returns:
        List of chunk texts
    """
    chunks = []
    current_chunk = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)

        # If adding this paragraph would exceed limit, start new chunk
        if current_tokens + para_tokens > max_tokens and current_chunk:
            chunk_text = "\n".join(current_chunk)
            chunks.append(chunk_text)
            current_chunk = []
            current_tokens = 0

        current_chunk.append(para)
        current_tokens += para_tokens

    # Add final chunk if not empty
    if current_chunk:
        chunk_text = "\n".join(current_chunk)
        chunks.append(chunk_text)

    return chunks


def extract_headers_from_chunk(chunk_text: str, chunk_num: int, total_chunks: int, model: str = "gpt-5.1") -> str:
    """
    Extract CV headers from a single chunk of text.

    Args:
        chunk_text: Text content of this chunk
        chunk_num: Chunk number (1-indexed)
        total_chunks: Total number of chunks
        model: OpenAI model to use

    Returns:
        Text outline in [H1]/[H2]/[H3] format for this chunk
    """
    system_prompt = """You are an expert in academic CV structure.
You will receive a **portion** of a CV (not the entire document).

Your task is to:
1) Identify ONLY section and subsection HEADERS in this chunk.
2) Ignore all body text, bullets, dates, job titles, publication entries, and paragraph content.
3) Output a hierarchical outline using [H1], [H2], [H3].

Rules:
- Preserve the order of appearance in this chunk.
- Do NOT reorder or invent headings.
- Be conservative: if uncertain whether a line is a header, do NOT include it.
- [H1] = major CV sections (Education, Training, Appointments, Research, Teaching,
        Service, Licensure, Certifications, Honors, Awards, Committees, etc.)
- [H2] = subsections under H1
- [H3] = sub-subsections (e.g., "Peer-Reviewed Journal Articles", "Poster Presentations")
- Do NOT create synthetic headers during chunk extraction.
- Do NOT include table column headers (e.g., 'Year', 'Discipline', 'Institution').
- Do NOT include entry items, publication titles, organization names, or dates as headers.

HIERARCHY CONSISTENCY:
- These terms are NEVER [H1] - they are always [H2] or [H3] subsections:
  * Geographic: International, National, Regional, Local, State, Institutional
  * Time-based: Current, Past, Completed, Active, Pending, Ongoing
  * Role-based: Principal Investigator, Co-Investigator, Consultant, Mentor, Student
- If you see "Regional" or "National" as a header, it belongs under a parent section.

- Output ONLY headers using this format:

[H1] TEXT
  [H2] TEXT
    [H3] TEXT

Do not add any explanatory text, just the outline."""

    user_prompt = f"""Extract all headers from this CV chunk (chunk {chunk_num} of {total_chunks}).

CHUNK CONTENT:
{chunk_text}"""

    # Retry logic with exponential backoff for rate limits
    max_retries = 5
    base_delay = 1

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,  # Deterministic output
            )

            outline_text = response.choices[0].message.content.strip()

            # Clean up any markdown code fences
            outline_text = re.sub(r'^```[^\n]*\n', '', outline_text, flags=re.MULTILINE)
            outline_text = re.sub(r'\n```$', '', outline_text)

            return outline_text

        except RateLimitError as e:
            if attempt < max_retries - 1:
                # Extract wait time from error message if available
                error_msg = str(e)
                wait_time = base_delay * (2 ** attempt)  # Exponential backoff

                # Try to parse wait time from error message
                import re as re_module
                match = re_module.search(r'try again in ([\d.]+)s', error_msg)
                if match:
                    wait_time = float(match.group(1)) + 1  # Add 1 second buffer

                print(f"    ⚠️  Rate limit hit, waiting {wait_time:.1f}s before retry {attempt + 1}/{max_retries}...")
                time.sleep(wait_time)
            else:
                # Final attempt failed, re-raise
                raise


def parse_outline_to_hierarchy(outline_text: str) -> List[Dict]:
    """
    Parse [H1]/[H2]/[H3] outline text into hierarchy format expected by normalization.

    Returns a flat list with 'level' and 'text' keys, which is what the normalization
    functions expect.
    """
    lines = [line.rstrip() for line in outline_text.splitlines() if line.strip()]
    hierarchy = []

    for line in lines:
        line = line.lstrip()

        if not line.startswith("[H"):
            continue

        # Extract level (1, 2, or 3)
        if len(line) < 3 or not line[2].isdigit():
            continue
        level = int(line[2])

        # Extract text after "] "
        if "]" not in line:
            continue
        after_bracket = line.split("]", 1)[1].strip()

        hierarchy.append({
            'level': f'H{level}',
            'text': after_bracket
        })

    return hierarchy


def get_cv_hierarchy_chunked(cv_path: str, model: str = "gpt-5.1", max_chunk_tokens: int = 10000) -> Tuple[List[Dict], Dict]:
    """
    Extract CV header hierarchy from a DOCX file using chunked processing and normalization.

    Args:
        cv_path: Path to the CV DOCX file
        model: OpenAI model to use (default: gpt-4o)
        max_chunk_tokens: Maximum tokens per chunk (default: 10000)

    Returns:
        Tuple of (final_hierarchy, stats_dict)
    """
    print(f"\n{'='*80}")
    print(f"CHUNKED CV HIERARCHY EXTRACTION")
    print(f"{'='*80}")
    print(f"File: {cv_path}")
    print(f"Model: {model}")
    print(f"Max chunk size: {max_chunk_tokens} tokens\n")

    # Step 1: Extract paragraphs and tables
    print("Step 1: Extracting paragraphs and tables from DOCX...")
    paragraphs = extract_text_from_docx(cv_path)
    full_text = "\n".join(paragraphs)
    total_tokens = count_tokens(full_text)
    print(f"  ✓ Extracted {len(paragraphs)} text elements (paragraphs + table rows)")
    print(f"  ✓ Total document size: {len(full_text)} characters, {total_tokens} tokens")

    # Step 2: Split into chunks
    print(f"\nStep 2: Splitting into chunks of ~{max_chunk_tokens} tokens...")
    chunks = split_into_chunks(paragraphs, max_tokens=max_chunk_tokens)
    print(f"  ✓ Created {len(chunks)} chunks")
    for i, chunk in enumerate(chunks, 1):
        chunk_tokens = count_tokens(chunk)
        print(f"    Chunk {i}: {chunk_tokens} tokens")

    # Step 3: Extract headers from each chunk
    print(f"\nStep 3: Extracting headers from each chunk...")
    chunk_outlines = []
    total_input_tokens = 0
    total_output_tokens = 0

    for i, chunk in enumerate(chunks, 1):
        print(f"  Processing chunk {i}/{len(chunks)}...", end=" ")
        outline = extract_headers_from_chunk(chunk, i, len(chunks), model=model)
        chunk_outlines.append(outline)

        # Estimate tokens (rough approximation)
        input_tokens = count_tokens(chunk) + 200  # chunk + system prompt
        output_tokens = count_tokens(outline)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

        # Count headers in this chunk
        header_count = outline.count("[H1]") + outline.count("[H2]") + outline.count("[H3]")
        print(f"✓ ({header_count} headers)")

    print(f"  ✓ Extraction completed")
    print(f"  Total tokens: {total_input_tokens} input + {total_output_tokens} output = {total_input_tokens + total_output_tokens} total")
    extraction_cost = (total_input_tokens * 2.50 / 1_000_000) + (total_output_tokens * 10.00 / 1_000_000)
    print(f"  Estimated extraction cost: ${extraction_cost:.4f}")

    # Step 4: Combine chunk outlines
    print(f"\nStep 4: Combining chunk outlines...")
    combined_outline = "\n".join(chunk_outlines)
    combined_hierarchy = parse_outline_to_hierarchy(combined_outline)
    print(f"  ✓ Combined outline has {len(combined_hierarchy)} headers")

    # Step 4b: Ensure PERSONAL DATA is first section (remove CV titles, add synthetic if needed)
    print(f"\nStep 4b: Ensuring PERSONAL DATA is first section...")
    combined_hierarchy = ensure_personal_data_first(combined_hierarchy)

    # Step 5: Apply Step 7 normalization (first pass - can add synthetic headers)
    print(f"\nStep 5: Applying Step 7 normalization (semantic grouping, synthetic headers)...")
    normalized_pass1 = normalize_hierarchy_with_llm(combined_hierarchy, pass_number=1)
    print(f"  ✓ After normalization pass 1: {len(normalized_pass1)} headers")

    # Step 6: Apply Step 8 validation (filter out body text and year labels)
    print(f"\nStep 6: Applying Step 8 validation (filter non-headers)...")
    validated_hierarchy = validate_headers_vs_entries(normalized_pass1)
    print(f"  ✓ After validation: {len(validated_hierarchy)} headers")

    # Step 7: Apply Step 9 normalization (second pass - no synthetic headers)
    print(f"\nStep 7: Applying Step 9 normalization (final cleanup)...")
    normalized_pass2 = normalize_hierarchy_with_llm(validated_hierarchy, pass_number=2)
    print(f"  ✓ After normalization pass 2: {len(normalized_pass2)} headers")

    # Calculate stats
    stats = {
        'document_tokens': total_tokens,
        'num_chunks': len(chunks),
        'extraction_input_tokens': total_input_tokens,
        'extraction_output_tokens': total_output_tokens,
        'extraction_cost': extraction_cost,
        'raw_headers': len(combined_hierarchy),
        'after_pass1': len(normalized_pass1),
        'after_validation': len(validated_hierarchy),
        'final_headers': len(normalized_pass2),
    }

    print(f"\n{'='*80}")
    print(f"EXTRACTION COMPLETE")
    print(f"{'='*80}\n")

    return normalized_pass2, stats


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python chunked_chat_hierarchy_extractor.py <cv_file.docx> [model] [max_chunk_tokens]")
        print("  model: Optional, defaults to gpt-4o")
        print("  max_chunk_tokens: Optional, defaults to 10000")
        sys.exit(1)

    cv_path = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "gpt-5.1"
    max_chunk_tokens = int(sys.argv[3]) if len(sys.argv) > 3 else 10000

    # Extract hierarchy
    final_hierarchy, stats = get_cv_hierarchy_chunked(cv_path, model=model, max_chunk_tokens=max_chunk_tokens)

    # Display results
    print("\nFINAL CV HIERARCHY:")
    print("="*80)
    outline_text = format_hierarchy_outline(final_hierarchy)
    print(outline_text)
    print("="*80)

    # Count top-level sections
    h1_count = sum(1 for h in final_hierarchy if h['level'] == 'H1')
    print(f"\nTotal headers: {len(final_hierarchy)}")
    print(f"Top-level sections (H1): {h1_count}")

    # Display stats
    print(f"\nProcessing Statistics:")
    print(f"  Document size: {stats['document_tokens']} tokens")
    print(f"  Number of chunks: {stats['num_chunks']}")
    print(f"  Extraction cost: ${stats['extraction_cost']:.4f}")
    print(f"  Headers: {stats['raw_headers']} → {stats['after_pass1']} → {stats['after_validation']} → {stats['final_headers']}")
