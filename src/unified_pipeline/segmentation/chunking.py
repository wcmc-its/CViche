"""Section chunking + table flattening for chunked Word segmentation.

Split out of word_chunked.py (#315 follow-up). chunk_section carries the
#315 fix: tables are attributed to sections by document order, not string idx.
"""

from typing import Any, Dict, List

# Chunking thresholds
MAX_CHARS_PER_SECTION = 12000  # Conservative to stay under token limits
MAX_ENTRIES_PER_CALL = 50      # Similar to PDF approach


def chunk_section(elements: List[Dict], start_idx: int, end_idx: int) -> List[List[Dict]]:
    """
    Split a section into chunks if it exceeds MAX_CHARS_PER_SECTION.

    Uses intelligent splitting:
    - Preserves list continuity (don't split mid-list)
    - Splits on paragraph boundaries
    - Keeps related items together

    Returns list of element chunks.
    """
    # Paragraph/empty elements carry an int idx (para_idx); table elements carry
    # a string idx ("table_N", on the body-element counter) that stage_2 needs and
    # that cannot be range-compared. Elements are in document order, so attribute
    # each non-int-idx element to the section of the most recent paragraph.
    # ponytail: boundary fix, not source — stage_2 requires the "table_N" string idx
    # (idx.startswith('table_'), sort keys). Proper fix is nested segmentation (#312 B2).
    section_elements = []
    last_para_idx = None
    for e in elements:
        idx = e.get('idx')
        if isinstance(idx, int):
            last_para_idx = idx
            if start_idx <= idx < end_idx:
                section_elements.append(e)
        elif last_para_idx is not None and start_idx <= last_para_idx < end_idx:
            section_elements.append(e)

    # Helper function to get element text length (handles tables)
    def get_elem_char_count(elem):
        if elem.get('type') == 'table':
            return len(table_to_text(elem))
        return len(elem.get('text', ''))

    # Calculate total chars
    total_chars = sum(get_elem_char_count(e) for e in section_elements)

    # If small enough, return as single chunk
    if total_chars <= MAX_CHARS_PER_SECTION:
        return [section_elements]

    # Otherwise, split into chunks
    chunks = []
    current_chunk = []
    current_chars = 0

    for elem in section_elements:
        elem_chars = get_elem_char_count(elem)

        # If adding this element would exceed limit and we have content, start new chunk
        if current_chars + elem_chars > MAX_CHARS_PER_SECTION and current_chunk:
            chunks.append(current_chunk)
            current_chunk = [elem]
            current_chars = elem_chars
        else:
            current_chunk.append(elem)
            current_chars += elem_chars

    # Add final chunk
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def table_to_text(table_elem: Dict[str, Any]) -> str:
    """
    Convert table element to readable text format for LLM processing.

    Args:
        table_elem: Table element with 'data' field containing rows/cells

    Returns:
        Formatted text representation of table content
    """
    if 'data' not in table_elem:
        return ""

    lines = []
    for row_data in table_elem['data']:
        # Extract cell text from each row
        row_texts = [cell.get('text', '').strip() for cell in row_data]
        # Filter out empty cells
        row_texts = [text for text in row_texts if text]
        # Join with delimiter
        if row_texts:
            lines.append(' | '.join(row_texts))

    return '\n'.join(lines)
