"""
Signature-Based CV Segmentation with LLM Classification

Pipeline:
1. Extract all paragraphs with comprehensive formatting metadata
2. Build normalized format signatures (hash-able)
3. Group paragraphs by identical signatures
4. Compute prominence score per signature group
5. Send signature groups to LLM for H1/H2/H3 classification
6. Build final hierarchical structure

Key insight: Let the LLM classify ~10-20 format signature groups, not 200+ paragraphs.
"""

import json
import logging
import hashlib
import re
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict
from docx import Document
from docx.shared import RGBColor, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from unified_pipeline.llm_client import call_llm
from unified_pipeline.llm.retry import LLMOutageError

# Import locked headers for secondary confidence boost
try:
    from .locked_headers_v6 import LOCKED_CV_HEADERS
except ImportError:
    from locked_headers_v6 import LOCKED_CV_HEADERS

logger = logging.getLogger(__name__)

# Indent width the normalization prompts ask for: [H2] = 2 spaces, [H3] = 4.
OUTLINE_INDENT_WIDTH = 2

_OUTLINE_LINE_RE = re.compile(r'\[H([123])\]\s+(.+)')


# ============================================================================
# Helper: Extract All Paragraphs (Including Tables)
# ============================================================================

def extract_all_paragraphs(doc: Document) -> list:
    """
    Extract all paragraphs from document in order, including paragraphs within tables.

    Standard doc.paragraphs iteration ONLY returns body paragraphs, missing table content.
    This function iterates through document body elements (paragraphs and tables) in order
    and extracts all paragraph objects.

    Deduplication:
    - Skips merged table cells (detected by same cell object ID)
    - Skips consecutive paragraphs with identical text

    Returns:
        List of Paragraph objects in document order (deduplicated)
    """
    all_paragraphs = []
    last_text = None
    seen_cells = set()  # Track cell IDs to avoid duplicates from merged cells

    for element in doc.element.body:
        if element.tag.endswith('p'):  # Paragraph element
            # Find the corresponding Paragraph object
            for para in doc.paragraphs:
                if para._element == element:
                    current_text = para.text.strip()
                    # Skip if identical to previous paragraph (merged table cells)
                    if current_text != last_text or not current_text:
                        all_paragraphs.append(para)
                    last_text = current_text
                    break
        elif element.tag.endswith('tbl'):  # Table element
            # Find the corresponding Table object and extract all cell paragraphs
            for table in doc.tables:
                if table._element == element:
                    # Extract paragraphs from table cells in row/column order
                    # Track cell IDs to avoid processing the same merged cell multiple times
                    seen_cells.clear()  # Reset for each table
                    for row in table.rows:
                        for cell in row.cells:
                            cell_id = id(cell)
                            # Skip if we've already processed this cell (merged cells)
                            if cell_id in seen_cells:
                                continue
                            seen_cells.add(cell_id)

                            for para in cell.paragraphs:
                                current_text = para.text.strip()
                                # Skip if identical to previous paragraph
                                if current_text != last_text or not current_text:
                                    all_paragraphs.append(para)
                                last_text = current_text
                    break

    return all_paragraphs


# ============================================================================
# STEP 1-2: Extract Paragraphs + Build Format Signatures
# ============================================================================

@dataclass
class FormatSignature:
    """Normalized, hashable format signature for a paragraph."""

    # Font properties
    font_family: str
    font_size_pt: float
    bold: bool
    italic: bool
    underline: str
    all_caps: bool
    small_caps: bool
    color: str

    # Paragraph properties
    alignment: str
    indent_left_in: float
    indent_right_in: float
    line_spacing: float
    space_before_pt: float
    space_after_pt: float

    # Borders
    has_border_top: bool
    has_border_bottom: bool
    has_border_left: bool
    has_border_right: bool
    border_top_width_pt: float
    border_bottom_width_pt: float

    # Background
    background_color: str | None

    # Style name (for style-based formatting)
    style_name: str

    # Mixed formatting flag (indicates paragraph has multiple formatting styles)
    has_mixed_formatting: bool

    def to_hash(self) -> str:
        """Create stable hash for grouping."""
        # Round floats to avoid minor differences
        normalized = {
            k: round(v, 2) if isinstance(v, float) else v
            for k, v in asdict(self).items()
        }
        json_str = json.dumps(normalized, sort_keys=True)
        return hashlib.md5(json_str.encode()).hexdigest()


def rgb_to_hex(rgb: RGBColor | None) -> str:
    """Convert RGBColor to hex string."""
    if rgb is None:
        return "#000000"
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def pt_to_inches(pt: Pt | None) -> float:
    """Convert points to inches."""
    if pt is None:
        return 0.0
    return pt.inches if hasattr(pt, 'inches') else 0.0


def extract_border_info(para) -> dict[str, Any]:
    """Extract border information from paragraph XML."""
    borders = {
        'top': None,
        'bottom': None,
        'left': None,
        'right': None
    }

    if para._p.pPr is None:
        return borders

    # Use find() to safely check for pBdr element
    pBdr = para._p.pPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pBdr', para._p.pPr.nsmap if hasattr(para._p.pPr, 'nsmap') else None)
    if pBdr is None:
        return borders

    for side in ['top', 'bottom', 'left', 'right']:
        # Find border element for this side
        border_elem = pBdr.find(f'{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{side}', pBdr.nsmap if hasattr(pBdr, 'nsmap') else None)
        if border_elem is not None:
            # Extract border width in points (default 0.5 if not specified)
            sz_val = border_elem.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sz')
            width_pt = float(sz_val) / 8.0 if sz_val else 0.5  # eighths of a point

            borders[side] = {
                'type': border_elem.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val', 'single'),
                'pt': width_pt,
                'color': border_elem.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}color', '#000000'),
                'space_twips': border_elem.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}space', 1)
            }

    return borders


def _normalise_header_text(text: str) -> str:
    """Normalise header text for locked-header matching (#857, #871).

    Strips a trailing parenthetical (e.g. "(CV)"), then trailing colons/
    semicolons/periods and whitespace -- the single normalisation both the
    signature step's locked-header match and rescue_locked_headers'
    EXCLUDED_HEADERS check must share, so they can't drift apart.
    """
    return re.sub(r'\([^)]*\)', '', text).strip().lower().rstrip(':;.').strip()


def extract_paragraph_signature(para, para_idx: int, total_paras: int) -> dict[str, Any]:
    """
    Extract comprehensive format signature for a paragraph.

    Returns both the signature and additional metadata for analysis.
    """
    text = para.text.strip()

    # Text metadata
    tokens = text.split()
    is_single_line = '\n' not in text

    # ALL_CAPS detection: check both text content AND formatting
    # Text-based: actual uppercase characters (e.g., "GRANTS")
    text_is_all_caps = text.isupper() if text else False

    # Format-based: all_caps font property (e.g., JHead1 style with all_caps=True)
    format_has_all_caps = False
    if para.runs:
        first_run = None
        for run in para.runs:
            if run.text.strip():
                first_run = run
                break
        if first_run:
            # Check run formatting first
            if first_run.font.all_caps is not None:
                format_has_all_caps = first_run.font.all_caps
            # Then check style formatting
            elif para.style and hasattr(para.style, 'font') and para.style.font.all_caps is not None:
                format_has_all_caps = para.style.font.all_caps

    # Combined: either text is uppercase OR formatting applies all_caps
    is_all_caps = text_is_all_caps or format_has_all_caps
    is_title_case = text.istitle() if text else False

    case_type = 'UNKNOWN'
    if is_all_caps:
        case_type = 'ALL_CAPS'
    elif is_title_case:
        case_type = 'TitleCaseWords'
    elif text and text[0].isupper():
        case_type = 'MixedCase'
    elif text and text.islower():
        case_type = 'lowercase'

    # Font properties from first NON-EMPTY run (dominant formatting)
    font_family = "Unknown"
    font_size_pt = 12.0
    bold = False
    italic = False
    underline = "none"
    small_caps = False
    color = "#000000"

    if para.runs:
        # Find first non-empty run (skip empty runs that might have None formatting)
        first_run = None
        for run in para.runs:
            if run.text.strip():  # Non-empty text
                first_run = run
                break

        # Fallback to first run if all are empty
        if first_run is None and para.runs:
            first_run = para.runs[0]

        if first_run:
            # Font family (with inheritance resolution)
            if first_run.font.name:
                font_family = first_run.font.name
            elif para.style and hasattr(para.style, 'font') and para.style.font.name:
                font_family = para.style.font.name

            # Font size
            if first_run.font.size:
                font_size_pt = first_run.font.size.pt
            elif para.style and hasattr(para.style, 'font') and para.style.font.size:
                font_size_pt = para.style.font.size.pt

            # Bold/Italic/Underline - check run first, then inherit from style
            if first_run.bold is not None:
                bold = first_run.bold
            elif para.style and hasattr(para.style, 'font') and para.style.font.bold is not None:
                bold = para.style.font.bold
            else:
                bold = False

            if first_run.italic is not None:
                italic = first_run.italic
            elif para.style and hasattr(para.style, 'font') and para.style.font.italic is not None:
                italic = para.style.font.italic
            else:
                italic = False

            if first_run.underline:
                underline = str(first_run.underline)

            # Small caps - check run first, then inherit from style
            if first_run.font.small_caps is not None:
                small_caps = first_run.font.small_caps
            elif para.style and hasattr(para.style, 'font') and para.style.font.small_caps is not None:
                small_caps = para.style.font.small_caps
            else:
                small_caps = False

            # Color
            if first_run.font.color and first_run.font.color.rgb:
                color = rgb_to_hex(first_run.font.color.rgb)

    # Detect mixed formatting: check if paragraph has runs with different bold/italic values
    # Real headers typically have uniform formatting throughout
    has_mixed_formatting = False
    if para.runs and len(para.runs) > 1:
        # Get the dominant formatting from first non-empty run
        dominant_bold = bold
        dominant_italic = italic

        # Check if any subsequent runs have different formatting
        text_char_count = 0
        for run in para.runs:
            run_text = run.text.strip()
            if not run_text:  # Skip empty runs
                continue

            text_char_count += len(run_text)

            # Resolve run's bold/italic (with style inheritance)
            run_bold = run.bold
            if run_bold is None and para.style and hasattr(para.style, 'font'):
                run_bold = para.style.font.bold if para.style.font.bold is not None else False
            elif run_bold is None:
                run_bold = False

            run_italic = run.italic
            if run_italic is None and para.style and hasattr(para.style, 'font'):
                run_italic = para.style.font.italic if para.style.font.italic is not None else False
            elif run_italic is None:
                run_italic = False

            # If this run's formatting differs from dominant AND has substantial text (>10% of total)
            # then mark as mixed formatting
            if text_char_count > 3:  # At least 3 chars before checking
                if run_bold != dominant_bold or run_italic != dominant_italic:
                    has_mixed_formatting = True
                    break

    # Paragraph properties
    alignment = "left"
    if para.paragraph_format.alignment is not None:
        alignment_map = {
            WD_ALIGN_PARAGRAPH.LEFT: "left",
            WD_ALIGN_PARAGRAPH.CENTER: "center",
            WD_ALIGN_PARAGRAPH.RIGHT: "right",
            WD_ALIGN_PARAGRAPH.JUSTIFY: "justify"
        }
        alignment = alignment_map.get(para.paragraph_format.alignment, "left")

    indent_left_in = pt_to_inches(para.paragraph_format.left_indent)
    indent_right_in = pt_to_inches(para.paragraph_format.right_indent)

    # Line spacing (normalize to float)
    line_spacing = 1.0
    if para.paragraph_format.line_spacing:
        line_spacing = float(para.paragraph_format.line_spacing)

    space_before_pt = 0.0
    space_after_pt = 0.0
    if para.paragraph_format.space_before:
        space_before_pt = para.paragraph_format.space_before.pt
    if para.paragraph_format.space_after:
        space_after_pt = para.paragraph_format.space_after.pt

    # Borders
    borders = extract_border_info(para)

    # Background color
    background_color = None
    if para._p.pPr is not None:
        shd = para._p.pPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}shd', para._p.pPr.nsmap if hasattr(para._p.pPr, 'nsmap') else None)
        if shd is not None:
            shd_fill = shd.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fill')
            if shd_fill and shd_fill != 'auto':
                background_color = f"#{shd_fill}"

    # Style name (CRITICAL for style-based formatting)
    style_name = "Normal"
    if para.style and para.style.name:
        style_name = para.style.name

    # Build normalized signature
    signature = FormatSignature(
        font_family=font_family,
        font_size_pt=round(font_size_pt, 1),
        bold=bold,
        italic=italic,
        underline=underline,
        all_caps=is_all_caps,
        small_caps=small_caps,
        color=color,
        alignment=alignment,
        indent_left_in=round(indent_left_in, 3),
        indent_right_in=round(indent_right_in, 3),
        line_spacing=round(line_spacing, 2),
        space_before_pt=round(space_before_pt, 1),
        space_after_pt=round(space_after_pt, 1),
        has_border_top=borders['top'] is not None,
        has_border_bottom=borders['bottom'] is not None,
        has_border_left=borders['left'] is not None,
        has_border_right=borders['right'] is not None,
        border_top_width_pt=borders['top']['pt'] if borders['top'] else 0.0,
        border_bottom_width_pt=borders['bottom']['pt'] if borders['bottom'] else 0.0,
        background_color=background_color,
        style_name=style_name,
        has_mixed_formatting=has_mixed_formatting
    )

    # Check if text matches locked headers (secondary signal)
    # See _normalise_header_text for the parenthetical/punctuation strip.
    text_lower = _normalise_header_text(text)
    matches_locked_header = text_lower in LOCKED_CV_HEADERS

    return {
        'paragraph_index': para_idx,
        'text': text,
        'text_metadata': {
            'case_type': case_type,
            'tokens': tokens,
            'tokens_count': len(tokens),
            'all_caps': is_all_caps,
            'small_caps': small_caps,
            'single_line': is_single_line,
            'matches_locked_header': matches_locked_header
        },
        'format_signature': signature,
        'signature_hash': signature.to_hash(),
        'position_in_doc': para_idx / total_paras if total_paras > 0 else 0.0,
        'borders_detail': borders  # Keep for detailed output
    }


# ============================================================================
# STEP 3-4: Group Signatures + Compute Prominence Scores
# ============================================================================

def group_by_signature(paragraphs: list[dict]) -> dict[str, list[dict]]:
    """Group paragraphs by identical format signatures."""
    groups = {}

    for para in paragraphs:
        sig_hash = para['signature_hash']
        if sig_hash not in groups:
            groups[sig_hash] = []
        groups[sig_hash].append(para)

    return groups


def compute_prominence_score(signature_group: list[dict]) -> float:
    """
    Compute visual prominence score for a signature group.

    Based on your formula:
      score = w1*font_size + w2*has_border + w3*is_bold + w4*space_before +
              w5*casing_rank + w6*early_position + w7*locked_header_match
    """

    # Get canonical signature (same for all paragraphs in group)
    canonical = signature_group[0]['format_signature']

    # Statistics across group
    count = len(signature_group)
    avg_position = sum(p['position_in_doc'] for p in signature_group) / count
    locked_header_matches = sum(p['text_metadata']['matches_locked_header'] for p in signature_group)

    # Normalize font size (assume 12pt is baseline, 14pt+ is prominent)
    normalized_font_size = (canonical.font_size_pt - 12.0) / 4.0  # 0.0 at 12pt, 1.0 at 16pt
    normalized_font_size = max(0.0, min(1.0, normalized_font_size))

    # Border presence (strong signal)
    has_border = 1.0 if (canonical.has_border_top or canonical.has_border_bottom) else 0.0

    # Bold presence
    is_bold = 1.0 if canonical.bold else 0.0

    # Italic presence (also a heading signal)
    is_italic = 1.0 if canonical.italic else 0.0

    # Spacing before (normalize to 0-1 range, 12pt+ is significant)
    normalized_space_before = canonical.space_before_pt / 12.0
    normalized_space_before = max(0.0, min(1.0, normalized_space_before))

    # Casing rank
    casing_rank = 0.0
    if canonical.all_caps:
        casing_rank = 1.0
    elif signature_group[0]['text_metadata']['case_type'] == 'TitleCaseWords':
        casing_rank = 0.7
    elif signature_group[0]['text_metadata']['case_type'] == 'MixedCase':
        casing_rank = 0.3

    # Early position bonus (appears in first 20% of document)
    early_position_bonus = 1.0 if avg_position < 0.2 else 0.0

    # Centered alignment (often for major headers)
    is_centered = 1.0 if canonical.alignment == 'center' else 0.0

    # Locked header match (secondary signal)
    locked_header_ratio = locked_header_matches / count

    # Low count (headers are typically rare, 3-30 instances)
    # Body text typically 100+ instances
    low_count_bonus = 1.0 if 3 <= count <= 30 else 0.0

    # Indentation penalty (CRITICAL for hierarchy!)
    # Headers with indentation are subordinate (H2/H3), not major (H1)
    # Normalize indent: 0 = no penalty, 0.5+ inches = significant penalty
    indent_penalty = min(1.0, canonical.indent_left_in / 0.5)  # 0.5" = full penalty

    # Mixed formatting penalty (CRITICAL!)
    # Paragraphs with mixed bold/italic are almost never headers
    # Real headers have uniform formatting throughout
    mixed_formatting_penalty = 0.8 if canonical.has_mixed_formatting else 0.0

    # Weighted sum (adjust weights as needed)
    score = (
        0.15 * normalized_font_size +
        0.25 * has_border +           # Strong signal
        0.15 * is_bold +
        0.10 * is_italic +
        0.10 * normalized_space_before +
        0.10 * casing_rank +
        0.05 * early_position_bonus +
        0.05 * is_centered +
        0.15 * locked_header_ratio +   # Secondary boost
        0.10 * low_count_bonus -
        0.20 * indent_penalty -        # PENALTY for indentation (subordinate headers)
        mixed_formatting_penalty      # STRONG PENALTY for mixed formatting
    )

    return round(score, 3)


# ============================================================================
# STEP 5: LLM Classification of Signature Groups
# ============================================================================

def classify_signature_groups_with_llm(signature_groups: dict[str, list[dict]], max_groups: int = 100) -> dict[str, dict]:
    """
    Send signature groups to LLM for H1/H2/H3 classification.

    Args:
        signature_groups: Dict of signature hash -> list of paragraphs
        max_groups: Maximum number of signature groups to send to LLM (default 100)
                   Groups are pre-filtered by prominence score to stay within limits

    Returns: {signature_hash: {level, confidence, reasoning}}
    """

    # Build summaries for LLM
    group_summaries = []

    for sig_hash, paragraphs in signature_groups.items():
        canonical_sig = paragraphs[0]['format_signature']
        count = len(paragraphs)
        avg_position = sum(p['position_in_doc'] for p in paragraphs) / count

        # Collect example strings (up to 10)
        example_strings = [p['text'] for p in paragraphs[:10] if p['text'].strip()]

        # Prominence score
        prominence = compute_prominence_score(paragraphs)

        group_summaries.append({
            'signature_id': sig_hash,
            'format': {
                'font_family': canonical_sig.font_family,
                'font_size_pt': canonical_sig.font_size_pt,
                'bold': canonical_sig.bold,
                'italic': canonical_sig.italic,
                'underline': canonical_sig.underline,
                'all_caps': canonical_sig.all_caps,
                'small_caps': canonical_sig.small_caps,
                'color': canonical_sig.color,
                'alignment': canonical_sig.alignment,
                'indent_left_in': canonical_sig.indent_left_in,
                'space_before_pt': canonical_sig.space_before_pt,
                'space_after_pt': canonical_sig.space_after_pt,
                'has_border_top': canonical_sig.has_border_top,
                'has_border_bottom': canonical_sig.has_border_bottom,
                'border_top_width_pt': canonical_sig.border_top_width_pt,
                'border_bottom_width_pt': canonical_sig.border_bottom_width_pt,
                'background_color': canonical_sig.background_color
            },
            'statistics': {
                'count': count,
                'avg_position_in_doc': round(avg_position, 3),
                'appears_in_first_20_percent': avg_position < 0.2,
                'prominence_score': prominence
            },
            'example_strings': example_strings
        })

    # Sort by prominence score (helps LLM see hierarchy)
    group_summaries.sort(key=lambda x: x['statistics']['prominence_score'], reverse=True)

    # Filter to top N groups if needed (to avoid token limits)
    total_groups = len(group_summaries)
    if total_groups > max_groups:
        print(f"⚠️  WARNING: {total_groups} signature groups detected, filtering to top {max_groups} by prominence score")
        print(f"   (Groups with prominence < {group_summaries[max_groups-1]['statistics']['prominence_score']:.3f} will be auto-classified as NOT_HEADER)")

        # Keep top N groups for LLM classification
        filtered_summaries = group_summaries[:max_groups]

        # Auto-classify remaining low-prominence groups as NOT_HEADER
        auto_classified = {}
        for summary in group_summaries[max_groups:]:
            auto_classified[summary['signature_id']] = {
                'level': 'NOT_HEADER',
                'confidence': 0.95,
                'reasoning': f'Auto-classified: Low prominence score ({summary["statistics"]["prominence_score"]:.3f}), filtered from LLM classification'
            }

        group_summaries = filtered_summaries
    else:
        auto_classified = {}

    # LLM prompt
    system_prompt = """You are analyzing format signature groups from an academic CV.

Each group represents paragraphs with IDENTICAL visual formatting (font, size, borders, spacing, etc.).

Your task: classify each signature group as:
- "H1": Major section headers (Education, Publications, Grants, Teaching, etc.)
- "H2": Subsection headers (Peer-Reviewed, Reviews, National, International, etc.)
- "H3": Sub-subsection headers (rare, very specific subdivisions)
- "NOT_HEADER": Body text, contact information, or other non-header content

Classification guidelines:

1. USE ONLY FORMATTING CUES (ignore text semantics):
   - Font size, bold, italic, underline, all_caps, small_caps
   - Borders (top/bottom are VERY strong header signals)
   - Spacing before/after (extra space = prominence)
   - Alignment (centered vs left)
   - **INDENTATION** (CRITICAL: indent_left_in > 0 = subordinate header, likely H2/H3)
   - Background color/shading
   - Prominence score (pre-computed visual distinctiveness)

2. IGNORE TEXT CONTENT for classification decisions:
   - example_strings are for context only
   - Don't rely on seeing words like "Education" or "Publications"
   - Classify purely on visual appearance

3. IDENTIFY HIERARCHY:
   - H1 = Most visually prominent (highest prominence_score) + NO INDENTATION
   - H2 = Secondary prominence, subordinate to H1
   - H3 = Third-level (often INDENTED, italic, or less prominent)
   - **KEY RULE**: Indented headers (indent_left_in > 0) are subordinate (H2/H3, NOT H1)
   - Each level should have CONSISTENT formatting

4. FILTER OUT NON-HEADERS:
   - Contact info (email, phone, address) → NOT_HEADER even if centered/bold
   - Person names at CV top → NOT_HEADER
   - Body text (high count, low prominence) → NOT_HEADER
   - Random emphasized text → NOT_HEADER

5. USE STATISTICS:
   - Headers: low count (3-30 instances), high prominence
   - Body text: high count (50-500 instances), low prominence
   - Contact info: appears in first 5%, often centered

6. HIERARCHY REASONING:
   - Groups are pre-sorted by prominence_score (high → low)
   - Typically: top 1-2 groups = H1, next 1-3 groups = H2
   - Consider: Does this formatting look subordinate to a more prominent style?

Return JSON array with ALL groups classified:
{
  "classifications": [
    {
      "signature_id": "abc123",
      "classification": "H1",
      "confidence": 0.95,
      "reasoning": "Italic + top/bottom borders + low count (8) + high prominence (0.85)"
    },
    {
      "signature_id": "def456",
      "classification": "NOT_HEADER",
      "confidence": 0.90,
      "reasoning": "Centered + appears in first 5% + looks like CV title/name"
    }
  ]
}"""

    user_prompt = f"""Classify these {len(group_summaries)} format signature groups:

{json.dumps(group_summaries, indent=2)}

Document context:
- Total paragraphs: {sum(g['statistics']['count'] for g in group_summaries)}
- Unique signature groups: {len(group_summaries)}
- Groups sorted by prominence_score (high → low)

Classify each group based on FORMATTING ONLY."""

    # Schema
    SCHEMA = {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "signature_id": {"type": "string"},
                        "classification": {
                            "type": "string",
                            "enum": ["H1", "H2", "H3", "NOT_HEADER"]
                        },
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "reasoning": {"type": "string"}
                    },
                    "required": ["signature_id", "classification", "confidence", "reasoning"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["classifications"],
        "additionalProperties": False
    }

    llm_result = call_llm(
        stage="segmentation_signature",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "signature_classification",
                "strict": True,
                "schema": SCHEMA
            }
        },
        temperature=0.1
    )

    result = json.loads(llm_result["content"])

    # Build mapping from LLM classifications
    classifications = {
        cls['signature_id']: {
            'level': cls['classification'],
            'confidence': cls['confidence'],
            'reasoning': cls['reasoning']
        }
        for cls in result['classifications']
    }

    # Merge auto-classified low-prominence groups
    classifications.update(auto_classified)

    return classifications


# ============================================================================
# STEP 5b: Rescue Known Headers from NOT_HEADER Groups
# ============================================================================

def rescue_locked_headers(signature_groups: dict, classifications: dict) -> dict:
    """
    Scan through NOT_HEADER groups and rescue paragraphs that match known CV section headers.

    Uses the `matches_locked_header` flag that was already computed during signature extraction.
    This allows us to recover headers like "TEACHING and MENTORING" or "Review articles:" that
    were lumped into body-text signature groups.

    Returns: Updated classifications dict
    """
    rescued_count = 0
    rescued_headers = []

    # Headers to exclude from rescue (document titles, not section headers)
    EXCLUDED_HEADERS = {
        'curriculum vitae', 'cv', 'vita', 'resume', 'résumé',
        'biosketch', 'biographical sketch', 'bio'
    }

    # Scan each NOT_HEADER group
    for sig_hash, cls in list(classifications.items()):
        if cls['level'] != 'NOT_HEADER':
            continue

        group_paras = signature_groups[sig_hash]

        for para in group_paras:
            # Check if this paragraph was flagged as matching a known header
            if para.get('text_metadata', {}).get('matches_locked_header', False):
                text = (para.get('text') or '').strip()
                if not text:
                    continue
                text_lower = _normalise_header_text(text)

                # Skip document titles (not section headers)
                if text_lower in EXCLUDED_HEADERS:
                    continue

                # Additional safety check: skip if too long (likely false positive)
                if len(text) > 100:
                    continue

                # Promote to H1 level (top-level section, will be normalized by LLM passes later)
                para['classification'] = 'H1'
                para['classification_confidence'] = 0.90
                para['classification_reasoning'] = f'Rescued: Matches known CV section header'
                para['is_rescued_locked_header'] = True  # Mark as rescued to skip validation

                rescued_headers.append(text[:50])
                rescued_count += 1

    if rescued_count > 0:
        print(f"  ✓ Rescued {rescued_count} known section headers from NOT_HEADER groups:")
        for header in rescued_headers[:10]:  # Show first 10
            print(f"    - {header}")
        if len(rescued_headers) > 10:
            print(f"    ... and {len(rescued_headers) - 10} more")

    return classifications


# ============================================================================
# STEP 6b: Ensure PERSONAL DATA is the First Section
# ============================================================================

def ensure_personal_data_first(hierarchy: list[dict]) -> list[dict]:
    """
    Ensure PERSONAL DATA is the first H1 section in the hierarchy when needed.

    This function:
    1. Removes document title headers (CURRICULUM VITAE, CV, Resume, Biosketch, etc.)
    2. If there's still an early header (paragraph_index < 3), assumes it captures preamble
    3. Otherwise, inserts synthetic PERSONAL DATA as the first H1 section

    Args:
        hierarchy: The hierarchy from build_hierarchy_from_classifications()

    Returns:
        Modified hierarchy with PERSONAL DATA as the first section (if needed)
    """

    # -------------------------------------------------------------------------
    # Document titles to exclude - these are NOT section headers
    # -------------------------------------------------------------------------

    # Exact matches (after lowercasing and stripping punctuation)
    EXACT_TITLE_MATCHES = {
        # Standard
        'cv', 'vita', 'resume', 'résumé', 'vitae',
        'c.v', 'c.v.',
        # International
        'lebenslauf', 'bio-data', 'biodata',
        # Short variants
        'cv profile', 'cv experience', 'cv overview',
        'professional overview', 'info sheet', 'experience record',
        # Dossier variants
        'personal dossier', 'professional dossier',
    }

    # Substring matches - if header CONTAINS any of these, it's a document title
    TITLE_SUBSTRINGS = [
        'curriculum vitae', 'curriculum vitæ',
        'biosketch', 'bio-sketch', 'bio sketch',
        'professional resume', 'academic resume', 'executive resume',
        'technical resume', 'clinical resume', 'medical resume', 'engineering resume',
        'professional profile', 'academic profile', 'scholarly profile', 'faculty profile',
        'research portfolio', 'professional portfolio',
        'career summary', 'career story', 'career narrative', 'professional journey',
        'professional snapshot', 'professional background',
        'qualifications & experience', 'qualifications and experience',
        'experience summary', 'my work & experience', 'my work and experience',
        'tenure & promotion', 'tenure and promotion',
        'for grant application', 'for promotion review',
        'teaching portfolio', 'research focus', 'clinical practice', 'industry position',
        'research statement included', 'professional experience',
        'portfolio highlights', 'research, teaching, and service',
        'investigator curriculum', 'scientific curriculum', 'clinical curriculum',
        'scholarly curriculum', 'faculty curriculum', 'academic curriculum',
        'leadership curriculum', 'executive curriculum',
        'candidate curriculum', 'application curriculum',
    ]

    def is_document_title(text: str) -> bool:
        """Check if text is a document title (not a real section header)."""
        # Normalize: lowercase, strip whitespace and common punctuation
        normalized = text.lower().strip().rstrip(':').rstrip('.').strip()

        # Check exact matches
        if normalized in EXACT_TITLE_MATCHES:
            return True

        # Check substring matches
        for substring in TITLE_SUBSTRINGS:
            if substring in normalized:
                return True

        return False

    # -------------------------------------------------------------------------
    # Step 1: Filter out document titles from top level
    # -------------------------------------------------------------------------
    filtered = []
    removed_titles = []

    for node in hierarchy:
        if is_document_title(node['text']):
            removed_titles.append(node['text'])
        else:
            filtered.append(node)

    if removed_titles:
        print(f"  ⚠️  Removed {len(removed_titles)} document title(s):")
        for title in removed_titles:
            print(f"      - '{title}'")

    # -------------------------------------------------------------------------
    # Step 2: Check if there's already an early header capturing preamble
    # -------------------------------------------------------------------------
    # Only check paragraph_index if it exists (signature-based extraction)
    # For chunked extraction (no paragraph_index), always add PERSONAL DATA
    if filtered:
        first_real_idx = filtered[0].get('paragraph_index')

        # If paragraph_index exists and is 0, 1, or 2, it's already capturing preamble
        if first_real_idx is not None and first_real_idx < 3:
            print(f"  ✓ First section '{filtered[0]['text']}' starts at index {first_real_idx} - no synthetic header needed")
            return filtered

        # If no paragraph_index (chunked extraction), check if first header is already
        # a personal data type section
        if first_real_idx is None:
            first_text_lower = filtered[0]['text'].lower().strip().rstrip(':')
            personal_data_variants = {
                'personal data', 'personal information', 'contact information',
                'contact', 'contact details', 'personal details', 'profile',
                'biographical information', 'biography', 'bio', 'about me',
                'personal profile', 'personal', 'identifying information',
                'identification', 'header', 'name and contact', 'name & contact'
            }
            if first_text_lower in personal_data_variants:
                print(f"  ✓ First section '{filtered[0]['text']}' is already a personal data section - no synthetic header needed")
                return filtered

    # -------------------------------------------------------------------------
    # Step 3: Insert synthetic PERSONAL DATA as first H1
    # -------------------------------------------------------------------------
    # There's meaningful preamble content (3+ paragraphs before first section)
    # OR we're in chunked mode and there's no personal data section

    personal_data_node = {
        'text': 'PERSONAL DATA',
        'level': 'H1',
        'paragraph_index': -1,  # Synthetic - indicates "before any real paragraph"
        'format_signature': {},
        'text_metadata': {'synthetic': True},
        'classification_confidence': 1.0,
        'classification_reasoning': 'Synthetic header for preamble content (name, contact info, etc.)',
        'is_rescued_locked_header': False,
        'children': []
    }

    if filtered:
        first_real_idx = filtered[0].get('paragraph_index')
        if first_real_idx is not None:
            print(f"  ✓ Inserted synthetic 'PERSONAL DATA' section (preamble content before index {first_real_idx})")
        else:
            print(f"  ✓ Inserted synthetic 'PERSONAL DATA' section (first section: '{filtered[0]['text']}')")
    else:
        print(f"  ✓ Inserted synthetic 'PERSONAL DATA' section (no other sections found)")

    return [personal_data_node] + filtered


# ============================================================================
# STEP 6: Build Final Hierarchy
# ============================================================================

def normalize_hierarchy_with_llm(headers: list[dict], pass_number: int = 1) -> list[dict]:
    """
    Use GPT to normalize the hierarchy based on semantic meaning.

    Takes the initial hierarchy and asks GPT to correct the H1/H2/H3 levels
    based on CV structure conventions, not just formatting.

    Args:
        headers: The hierarchy to normalize
        pass_number: 1 for first pass (can add synthetic headers), 2 for second pass (no synthetic headers)
    """

    # Build the input list for GPT
    input_lines = []

    def add_headers_to_list(header_list, indent_level=0):
        """Recursively build the input list with proper indentation"""
        for header in header_list:
            level = header['level']
            text = header['text']
            spaces = "  " * indent_level
            input_lines.append(f"{spaces}[{level}] {text}")
            if header.get('children'):
                add_headers_to_list(header['children'], indent_level + 1)

    add_headers_to_list(headers)
    input_text = "\n".join(input_lines)

    # Select prompt based on pass number
    if pass_number == 1:
        system_prompt = """You are an expert in academic CV structure and outline normalization.

You will receive a list of headings using [H1], [H2], and [H3].
All headings are genuine headers. Your task is to produce a corrected hierarchy with:

- semantically valid parent–child nesting,
- optional synthetic **[H1] grouping headers** when needed,
- strict preservation of **order** and **text**.

=======================================
CORE PRINCIPLES
=======================================

1. PRESERVE ALL INPUT HEADINGS EXACTLY (CRITICAL)
   - Do NOT rename, delete, merge, rewrite, or reorder headings.
   - Only indentation ([H1]/[H2]/[H3]) may change.
   - EVERY heading from the input MUST appear in the output.
   - If you create a synthetic "EDUCATION" parent and the input has an "Education"
     heading, BOTH must appear: the synthetic EDUCATION as [H1] parent, and the
     original "Education" as [H2] child. Do NOT drop the original.
   - Count: output must have >= input headings (can add synthetic, cannot remove any).

2. SYNTHETIC HEADERS (ONLY [H1])
   You may add NEW [H1] headers ONLY when:
     - multiple adjacent headings clearly belong to the same domain, AND
     - grouping would match standard academic/clinical CV conventions.

   Allowed synthetic [H1] headers include:
     EDUCATION, TEACHING, GRANT SUPPORT, PUBLICATIONS,
     SERVICE, PROFESSIONAL ACTIVITIES, APPOINTMENTS,
     CERTIFICATION AND LICENSURE, CLINICAL ACTIVITIES

   Rules for synthetic headers:
     - Insert them **immediately before** their first child.
     - Do NOT nest unrelated headings under them.
     - Use them sparingly.
     - Do NOT insert a synthetic header if the input already provides a valid parent
       for that block of headings. Synthetic [H1] headers must only be added when
       the input lacks any usable parent for the domain.

3. NO REORDERING (CRITICAL)
   You MUST preserve the exact order of the input list.
   You may ONLY change indentation.

4. SEMANTIC GROUPING (MEANING > FORMAT)
   Place headings into appropriate parents based on meaning:
   - Education-related → EDUCATION
   - Teaching/advising/supervision → TEACHING
   - Grants/funding → GRANT SUPPORT
   - Publications/book chapters/articles → PUBLICATIONS
   - Service/committee roles → SERVICE
   - Clinical responsibilities → CLINICAL ACTIVITIES
   - Appointments/positions → APPOINTMENTS

   DO NOT combine unrelated domains.

5. "OTHER …" HEADINGS ARE PEERS
   Example: OTHER ACADEMIC APPOINTMENTS stays at the same level as ACADEMIC APPOINTMENTS.

6. TIME-WORDS DO NOT DEFINE PARENTS
   Current / Past / Previous / Recent do NOT create hierarchical relationships.
   These headings remain siblings.

7. USE STRICT INDENTATION
   [H1] = no spaces
   [H2] = 2 spaces
   [H3] = 4 spaces

=======================================
PUBLICATIONS & GRANTS SPECIAL RULE
=======================================

8. If multiple publication-related headings appear, group them under **PUBLICATIONS** (real or synthetic).

9. If multiple grant/funding headings appear, group them under **GRANT SUPPORT** (real or synthetic).


=======================================
STRICT INDENTATION & GROUPING CONTROL
=======================================

The most important rule in this task is:
**Headings may only change level — never order.**

To ensure correct indentation:

1. Process headings in order (top to bottom), but you MAY and SHOULD look ahead
   to ensure that your indentation choices:
   - do not pull unrelated future headings into a parent,
   - correctly identify runs of related headings,
   - support introducing synthetic headers only where a full block requires them,
   - avoid creating an indentation structure that conflicts with later sections.

   In other words:
   **Look ahead for grouping, but NEVER reorder.**

2. A heading may be nested only when ALL of the following are true:
   - It is semantically a subtype of the nearest logically valid parent above it
     (not necessarily the immediately previous heading).
   - It belongs to the same domain category (education, teaching, publications,
     grants, service, clinical, appointments).
   - Nesting does NOT "pull in" unrelated headings that come after it.

3. You MUST avoid "indentation cascades."
   If changing one heading's level would incorrectly reclassify subsequent siblings,
   keep it at its current level.

4. You MUST prefer a **flatter structure** over a deeper one when ambiguous.
   If two parent choices are possible, you MUST choose the higher-level parent.
   Only indent when the semantic relationship is unmistakably parent–child.

5. When in doubt between two possible indentations:
   - Choose the **less indented** (higher-level) option.

=======================================
OUTPUT
=======================================

Output ONLY the corrected outline.
Do not include commentary or explanations."""
    else:  # pass_number == 2
        system_prompt = """You are performing a final hierarchy normalization pass on an outline that has already undergone initial restructuring.

Your job:
- Fix incorrect indentation.
- Enforce semantic correctness.
- Ensure parent/peer relationships are consistent.
- Produce a stable final hierarchy.

=======================================
CORE RULES
=======================================

1. PRESERVE EXACT TEXT & ORDER
   Do NOT change wording.
   Do NOT reorder.
   Only modify indentation: [H1], [H2], [H3].

2. NO SYNTHETIC HEADERS
   All necessary top-level categories already exist.
   - You may NOT simulate a synthetic header by increasing indentation or by
     artificially nesting headings to act as a parent.

3. SEMANTIC CONSISTENCY
   - Siblings must remain siblings.
   - Only semantically valid children may be nested.
   - Nothing may be nested under an unrelated or narrower category.

4. TIME-WORDS DO NOT DEFINE HIERARCHY
   "Current", "Past", "Previous", "Recent" → do NOT imply parent–child.

5. "OTHER …" HEADINGS ARE PEERS
   Do not nest these under specific subcategories.

6. PUBLICATIONS & GRANTS RULE
   - All publication types must be under PUBLICATIONS.
   - All grant types must be under GRANT SUPPORT if that header is present.

7. PRESERVE ALL HEADINGS (CRITICAL)
   - Do NOT remove any headings from the input.
   - Even if a parent and child have similar names (e.g., "EDUCATION" and "Education"),
     keep BOTH. The child is an original CV heading that must be preserved.
   - Count: output must have exactly the same number of headings as input.

=======================================
INDENTATION
=======================================

[H1] = no indent
[H2] = 2 spaces
[H3] = 4 spaces

=======================================
STRICT INDENTATION & GROUPING CONTROL
=======================================

The most important rule in this task is:
**Headings may only change level — never order.**

To ensure correct indentation:

1. Process headings in order (top to bottom), but you MAY and SHOULD look ahead
   to ensure that your indentation choices:
   - do not pull unrelated future headings into a parent,
   - correctly identify runs of related headings,
   - avoid creating an indentation structure that conflicts with later sections.

   In other words:
   **Look ahead for grouping, but NEVER reorder.**

2. A heading may be nested only when ALL of the following are true:
   - It is semantically a subtype of the nearest logically valid parent above it
     (not necessarily the immediately previous heading).
   - It belongs to the same domain category (education, teaching, publications,
     grants, service, clinical, appointments).
   - Nesting does NOT "pull in" unrelated headings that come after it.

3. You MUST avoid "indentation cascades."
   If changing one heading's level would incorrectly reclassify subsequent siblings,
   keep it at its current level.

4. You MUST prefer a **flatter structure** over a deeper one when ambiguous.
   If two parent choices are possible, you MUST choose the higher-level parent.
   Only indent when the semantic relationship is unmistakably parent–child.

5. When in doubt between two possible indentations:
   - Choose the **less indented** (higher-level) option.

=======================================
OUTPUT
=======================================

Output ONLY the corrected outline.
No commentary."""

    logger.info("Step %d: Normalizing hierarchy with LLM...", 7 if pass_number == 1 else 9)
    logger.info("  Input hierarchy (%d lines)", len(input_lines))
    # debug, not info: the hierarchy is CV text and must not reach the prod log.
    logger.debug("INPUT TO LLM:\n%s", input_text)

    try:
        llm_result = call_llm(
            stage="segmentation_signature",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": input_text}
            ]
        )

        corrected_text = llm_result["content"].strip()

        logger.debug("OUTPUT FROM LLM:\n%s", corrected_text)

        # Parse the corrected hierarchy back into our data structure
        corrected_headers = parse_normalized_hierarchy(corrected_text, headers)

        logger.info("  ✓ Hierarchy normalized (%d lines)", len(corrected_text.splitlines()))
        return corrected_headers

    except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
        raise
    except Exception:
        logger.exception("GPT normalization failed; using original hierarchy")
        return headers


def _llm_dropped_indentation(lines: list[str]) -> bool:
    """True when no outline line is indented, so the [Hn] tags are the only signal.

    The chunked extractor feeds the normalizer an unindented list, and the
    model sometimes echoes that format back with correct [H2]/[H3] tags but
    zero indentation. Indentation is the parser's nesting signal, so without
    this fallback every header collapses to H1 (#526). Any indented line
    means the model did indent, so its indentation stays authoritative. An
    all-[H1] reply maps to indent 0 either way, so it needs no special case.
    """
    return not any(
        line.startswith((' ', '\t')) and _OUTLINE_LINE_RE.match(line.lstrip())
        for line in lines
    )


def parse_normalized_hierarchy(corrected_text: str, original_headers: list[dict]) -> list[dict]:
    """
    Parse the GPT-corrected hierarchy text back into our data structure.

    Preserves all metadata from original headers, only updates level and parent/child relationships.
    """
    lines = corrected_text.strip().splitlines()

    # Create a mapping from text to original header data
    text_to_header = {}
    def map_headers(header_list):
        for h in header_list:
            text_to_header[h['text']] = h
            if h.get('children'):
                map_headers(h['children'])
    map_headers(original_headers)

    tags_are_only_signal = _llm_dropped_indentation(lines)

    # Parse the corrected structure
    result = []
    stack = [(result, -1)]  # (current_list, indent_level)

    for line in lines:
        # Count leading spaces to determine indent
        stripped = line.lstrip()
        if not stripped:
            continue

        indent = len(line) - len(stripped)

        # Extract level and text
        match = _OUTLINE_LINE_RE.match(stripped)
        if not match:
            continue

        if tags_are_only_signal:
            # The LLM echoed the flat input format with no indentation at all,
            # so the [Hn] tag is the only nesting signal left (#526).
            indent = (int(match.group(1)) - 1) * OUTLINE_INDENT_WIDTH

        # Derive level from indentation depth, NOT from LLM's level tag
        # The LLM sometimes outputs wrong level tags (e.g., [H1] with 2-space indent)
        # Indentation is the source of truth for nesting structure
        if indent == 0:
            level = "H1"
        elif indent <= 2:
            level = "H2"
        else:
            level = "H3"

        text = match.group(2).strip()

        # Find the original header data (try exact match first, then fuzzy)
        original = text_to_header.get(text)
        if not original:
            # Try fuzzy match - strip trailing colons/punctuation
            text_clean = text.rstrip(':').strip()
            for orig_text, orig_header in text_to_header.items():
                if orig_text.rstrip(':').strip() == text_clean:
                    original = orig_header
                    break

        if not original:
            # This is a synthetic header created by the LLM - create minimal metadata
            print(f"  ℹ️  Creating synthetic header: '{text}'")
            new_header = {
                'text': text,
                'level': level,
                'paragraph_index': -1,  # Synthetic, not from original document
                'format_signature': {},
                'text_metadata': {'synthetic': True},
                'classification_confidence': 1.0,
                'classification_reasoning': 'Synthetic grouping header created by LLM',
                'children': []
            }
        else:
            # Create new header with corrected level
            new_header = {
                **original,  # Preserve all original metadata
                'level': level,  # Update level
                'children': []  # Reset children, will be rebuilt
            }

        # Find the right parent based on indentation
        while stack and stack[-1][1] >= indent:
            stack.pop()

        # Add to current parent's list
        stack[-1][0].append(new_header)

        # Push this header onto stack as potential parent
        stack.append((new_header['children'], indent))

    return result


# The bare geographic sub-labels the chunk prompt says are never [H1] (#429).
# Single definition: the chunk extraction prompt interpolates this tuple.
GEOGRAPHIC_SUB_LABELS = ('International', 'National', 'Regional', 'Local', 'State', 'Institutional')


@dataclass
class _PlacedHeader:
    """One header of a hierarchy walk: the node, the list holding it, and its docx line."""
    node: dict
    siblings: list
    depth: int
    line: int | None


# A footnote marker a docx puts after a header ("Regional*", "National†",
# "International¹") and the LLM drops from the outline (#429).
_TRAILING_FOOTNOTE_MARKERS = re.compile(r'[\s*†‡§¹²³⁰⁴-⁹]+$')
# A plain footnote number run straight onto a word ("Regional1"); "Section 2" keeps its number.
_ATTACHED_FOOTNOTE_NUMBER = re.compile(r'(?<=[^\W\d_])\d{1,2}$')


def _document_line_key(text: str) -> str:
    """Compare key for a header vs a docx line.

    Whitespace-collapsed, no trailing colon, no trailing footnote marker, casefolded.
    """
    key = ' '.join(text.split()).rstrip(':').strip()
    key = _ATTACHED_FOOTNOTE_NUMBER.sub('', _TRAILING_FOOTNOTE_MARKERS.sub('', key))
    return key.casefold()


_GEOGRAPHIC_SUB_LABEL_KEYS = frozenset(_document_line_key(label) for label in GEOGRAPHIC_SUB_LABELS)


def _place_headers(hierarchy: list[dict], keys: list[str]) -> list[_PlacedHeader]:
    """Pre-order walk giving each header the first docx line key equal to it after the previous header's line.

    A header with no such line (renamed, synthetic, or listed after a header that
    follows it in the document) gets None and does not advance the search.
    """
    placed: list[_PlacedHeader] = []
    stack = [(node, hierarchy, 0) for node in reversed(hierarchy)]
    cursor = -1
    while stack:
        node, siblings, depth = stack.pop()
        key = _document_line_key(node['text'])
        line = next((i for i in range(cursor + 1, len(keys)) if keys[i] == key), None)
        if line is not None:
            cursor = line
        placed.append(_PlacedHeader(node, siblings, depth, line))
        children = node.get('children') or []
        stack.extend((child, children, depth + 1) for child in reversed(children))
    return placed


def _misplaced_sub_label(
    placed: list[_PlacedHeader], keys: list[str],
) -> tuple[_PlacedHeader, int, _PlacedHeader] | None:
    """The first geographic sub-label listed after a header that follows it in the document.

    Returns the label, its docx line, and the anchor: the header found nearest
    before that line. Only acts when the label's text is once in the outline and
    on exactly one docx line, the header listed just before it was found at a
    later line, and an anchor exists. A label that is merely unfound, or whose
    text the outline repeats (a second group the docx spells differently), is
    left alone. (A label found by the forward walk is past its predecessor's
    line, so it never qualifies.)
    """
    outline_keys = [_document_line_key(p.node['text']) for p in placed]
    for prev, cur, key in zip(placed, placed[1:], outline_keys[1:]):
        if key not in _GEOGRAPHIC_SUB_LABEL_KEYS or prev.line is None or outline_keys.count(key) != 1:
            continue
        hits = [i for i, k in enumerate(keys) if k == key]
        if len(hits) != 1 or hits[0] >= prev.line:
            continue
        anchor = max((p for p in placed if p.line is not None and p.line < hits[0]),
                     key=lambda p: p.line, default=None)
        if anchor is not None:
            return cur, hits[0], anchor
    return None


def _index_of(nodes: list, node: dict) -> int:
    """Position of `node` in `nodes` by identity, not equality."""
    return next(i for i, candidate in enumerate(nodes) if candidate is node)


def _set_levels(node: dict, depth: int) -> None:
    """Retag a moved subtree's levels ([H1] at depth 0) to its new depth."""
    stack = [(node, depth)]
    while stack:
        current, d = stack.pop()
        current['level'] = f'H{d + 1}'
        stack.extend((child, d + 1) for child in current.get('children') or [])


def restore_sub_label_document_order(hierarchy: list[dict], lines: list[str]) -> list[dict]:
    """Move a geographic sub-label the LLM listed after a later section back into document order (#429).

    The normalisation passes may not reorder, but a bare "International" has come
    back after the section that follows it in the docx, where downstream stages
    nest or order it under the wrong parent. The label moves to directly after the
    header found nearest before its own docx line: as that header's first child
    when it has children (the label's sibling run), else as its next sibling.
    Mutates and returns `hierarchy`.
    """
    keys = [_document_line_key(line) for line in lines]
    for _ in range(len(keys)):
        placed = _place_headers(hierarchy, keys)
        found = _misplaced_sub_label(placed, keys)
        if found is None:
            break
        label, line, anchor = found
        # By equality is safe here: an equal dict has the same text, and a label
        # whose text the outline repeats is never moved.
        label.siblings.remove(label.node)
        if anchor.node.get('children'):
            anchor.node['children'].insert(0, label.node)
            _set_levels(label.node, anchor.depth + 1)
        else:
            anchor.siblings.insert(_index_of(anchor.siblings, anchor.node) + 1, label.node)
            _set_levels(label.node, anchor.depth)
        logger.info("  Moved a geographic sub-label back to document line %d (#429)", line)
    return hierarchy


def validate_headers_vs_entries(headers: list[dict]) -> list[dict]:
    """
    Validate each header to assess confidence that it's truly a header vs an entry.
    Uses LLM to provide header_likelihood and entry_likelihood percentages.
    Filters out headers with entry_likelihood > 60%.
    """

    system_prompt = """You are an expert classifier whose job is to determine whether each line is a true CV section header
or a content entry.

Think like someone designing a standard institutional CV template for many faculty. Your goal is to KEEP
only those labels that a third party could reasonably map to standard CV sections (or subsections), such as:

- EDUCATION / EDUCATIONAL BACKGROUND
- POSTGRADUATE TRAINING / RESIDENCY / FELLOWSHIPS
- ACADEMIC APPOINTMENTS / HOSPITAL APPOINTMENTS
- LICENSURE / BOARD CERTIFICATION
- HONORS AND AWARDS
- GRANT SUPPORT / FUNDED RESEARCH
- TEACHING / SUPERVISION / MENTORING
- PROFESSIONAL SERVICE / COMMITTEE SERVICE
- PROFESSIONAL SOCIETIES (as a category)
- PUBLICATIONS / ORIGINAL ARTICLES / BOOK CHAPTERS / ABSTRACTS
- INVITED LECTURES / CONFERENCE PRESENTATIONS

A "HEADER" (KEEP) is:
- a short, conceptual category label that describes a type of content (not a specific instance), and
- something that could plausibly appear as a reusable section or subsection label on a generic CV template.

An "ENTRY" (DROP) is:
- a specific instance within a category (a particular job, grant, talk, award, society, hospital, etc.),
- or any line that primarily names a single organization, role, person, or event.

Classify a line as ENTRY (DROP) when it clearly represents content rather than structure, for example:
- individual jobs or roles with dates
- individual grants with titles and amounts
- specific awards given to a specific person
- publication titles
- detailed positions with dates
- rows of data, tables, or bullet-like content

Soft rules for distinguishing headers from entries:

1. KEEP a line as a HEADER when it looks like a generic category that describes a *type* of content,
   rather than a specific instance. Ask yourself:
   "Could this appear as a standard section or subsection label on an institutional CV template
    used by many people?"

   If yes, KEEP. Examples:
   - "Academic Supervision of Postgraduate Students"
   - "Awards Won by Fellows Based on Supervised Research"
   - "Funded Research"
   - "Professional Societies"
   - "Invited Lectureships"
   - "National and International Conference Organization"
   - "Book Chapters" / "Original Articles" / "Review Articles"

2. DROP lines that primarily look like specific entities or instances, even if capitalized, such as:
   - the name of a single organization, society, hospital, university, or committee
   - the name of a single grant, project, or study
   - the name of a single award or lecture
   - the name of a single person (with or without degrees)

3. DROP year-only or number-only lines (e.g., "2005", "2021–2023"), and any line that is obviously
   a publication citation or fragment of one.

4. In ambiguous cases, ask: "Is this closer to a reusable template section, or closer to a concrete
   instance?" If it feels more like a concrete instance, DROP it. If it clearly describes a reusable
   category of content, KEEP it.

OUTPUT REQUIREMENTS
For each line in the input:
1. Repeat the original line
2. Output "Header likelihood: X%"
3. Output "Entry likelihood: X%"
The percentages must sum to 100%.

Do NOT explain your reasoning.
Do NOT add extra commentary."""

    # Collect all header texts with their levels (skip rescued locked headers)
    input_lines = []
    rescued_headers_map = {}  # text -> header object

    def collect_headers(header_list):
        for header in header_list:
            # Skip validation for rescued locked headers
            if header.get('is_rescued_locked_header', False):
                rescued_headers_map[header['text']] = header
                if header.get('children'):
                    collect_headers(header['children'])
                continue

            level = header['level']
            text = header['text']
            input_lines.append(f"[{level}] {text}")
            if header.get('children'):
                collect_headers(header['children'])

    collect_headers(headers)

    if not input_lines:
        return headers

    input_text = "Classify the following lines as headers or entries according to the rules:\n\n" + "\n".join(input_lines)

    logger.info("Step 8: Validating headers vs entries with LLM...")
    logger.info("  Input: %d headers to validate", len(input_lines))

    try:
        llm_result = call_llm(
            stage="segmentation_signature",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": input_text}
            ]
        )

        output_text = llm_result["content"].strip()

        # Parse the output to extract likelihoods
        # Format expected:
        # Input line: [H1] EDUCATION:
        # Header likelihood: 95%
        # Entry likelihood: 5%

        likelihoods = {}  # text -> (header_likelihood, entry_likelihood)
        lines = output_text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('Input line:') or line.startswith('['):
                # Extract the header text
                if line.startswith('Input line:'):
                    header_text = line.replace('Input line:', '').strip()
                else:
                    header_text = line

                # Remove the [H1], [H2], [H3] prefix
                import re
                match = re.match(r'\[H[123]\]\s+(.+)', header_text)
                if match:
                    header_text = match.group(1).strip()

                # Look ahead for Header likelihood and Entry likelihood
                header_pct = None
                entry_pct = None

                for j in range(i + 1, min(i + 4, len(lines))):
                    if 'Header likelihood:' in lines[j]:
                        pct_match = re.search(r'(\d+)%', lines[j])
                        if pct_match:
                            header_pct = int(pct_match.group(1))
                    elif 'Entry likelihood:' in lines[j]:
                        pct_match = re.search(r'(\d+)%', lines[j])
                        if pct_match:
                            entry_pct = int(pct_match.group(1))

                if header_pct is not None and entry_pct is not None:
                    likelihoods[header_text] = (header_pct, entry_pct)

            i += 1

        # Filter headers based on entry likelihood > 60%
        filtered_count = 0

        def filter_headers(header_list):
            nonlocal filtered_count
            filtered = []
            for header in header_list:
                text = header['text']

                # Always keep rescued locked headers (skip validation)
                if header.get('is_rescued_locked_header', False):
                    new_header = {**header}
                    if header.get('children'):
                        new_header['children'] = filter_headers(header['children'])
                    filtered.append(new_header)
                    continue

                # Try exact match first
                likelihood = likelihoods.get(text)

                # Try fuzzy match if exact fails
                if not likelihood:
                    text_clean = text.rstrip(':').strip()
                    for key in likelihoods.keys():
                        if key.rstrip(':').strip() == text_clean:
                            likelihood = likelihoods[key]
                            break

                if likelihood:
                    header_pct, entry_pct = likelihood
                    if entry_pct > 60:
                        filtered_count += 1
                        # debug: `text` is likely an entry, i.e. CV content, not a header.
                        logger.debug("  Filtered out %r (entry likelihood: %s%%)", text, entry_pct)
                        continue

                # Keep this header and recursively filter children
                new_header = {**header}
                if header.get('children'):
                    new_header['children'] = filter_headers(header['children'])
                filtered.append(new_header)

            return filtered

        filtered_headers = filter_headers(headers)

        logger.info("  ✓ Validation complete: %d entries filtered out", filtered_count)

        # Remove duplicates (same paragraph_index in parent-child relationship)
        deduped_headers = remove_duplicate_children(filtered_headers)

        return deduped_headers

    except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
        raise
    except Exception:
        logger.exception("Header validation failed; using unfiltered headers")
        return headers


def remove_duplicate_children(headers: list[dict]) -> list[dict]:
    """
    Remove child headers that have the same paragraph_index as their parent.
    This handles cases where the same header appears as both H1 and H2.
    """
    def dedupe_recursive(header_list, parent_index=None):
        result = []
        for header in header_list:
            current_index = header.get('paragraph_index')

            # Skip if this header has same paragraph_index as parent
            if parent_index is not None and current_index == parent_index:
                print(f"  ⚠️  Removed duplicate child '{header['text']}' (same paragraph as parent)")
                continue

            # Recursively dedupe children
            new_header = {**header}
            if header.get('children'):
                new_header['children'] = dedupe_recursive(header['children'], current_index)
            result.append(new_header)

        return result

    return dedupe_recursive(headers)


def build_hierarchy_from_classifications(
    signature_groups: dict[str, list[dict]],
    classifications: dict[str, dict]
) -> list[dict]:
    """
    Build hierarchical structure from classified signature groups.

    Returns:
    [
      {
        "text": "Education",
        "level": "H1",
        "paragraph_index": 7,
        "format_signature": {...},
        "children": [...]
      }
    ]
    """

    # Collect all headers (apply classifications to paragraphs)
    headers = []

    for sig_hash, paragraphs in signature_groups.items():
        classification = classifications.get(sig_hash, {
            'level': 'NOT_HEADER',
            'confidence': 0.0,
            'reasoning': 'Not classified'
        })

        # Process signature groups that were classified as headers
        if classification['level'] in ['H1', 'H2', 'H3']:
            for para in paragraphs:
                headers.append({
                    'text': para['text'],
                    'level': classification['level'],
                    'paragraph_index': para['paragraph_index'],
                    'format_signature': asdict(para['format_signature']),
                    'text_metadata': para['text_metadata'],
                    'borders_detail': para['borders_detail'],
                    'classification_confidence': classification['confidence'],
                    'classification_reasoning': classification['reasoning'],
                    'is_rescued_locked_header': para.get('is_rescued_locked_header', False)
                })

        # Also check for individually rescued paragraphs in NOT_HEADER groups
        elif classification['level'] == 'NOT_HEADER':
            for para in paragraphs:
                # Check if this specific paragraph was rescued
                para_level = para.get('classification')
                if para_level and para_level in ['H1', 'H2', 'H3']:
                    headers.append({
                        'text': para['text'],
                        'level': para_level,
                        'paragraph_index': para['paragraph_index'],
                        'format_signature': asdict(para['format_signature']),
                        'text_metadata': para['text_metadata'],
                        'borders_detail': para['borders_detail'],
                        'classification_confidence': para.get('classification_confidence', 0.90),
                        'classification_reasoning': para.get('classification_reasoning', 'Rescued header'),
                        'is_rescued_locked_header': para.get('is_rescued_locked_header', False)
                    })

    # Sort by document order
    headers.sort(key=lambda h: h['paragraph_index'])

    # Build hierarchy
    hierarchy = []
    current_h1 = None
    current_h2 = None

    for h in headers:
        level = h['level']

        node = {
            'text': h['text'],
            'level': level,
            'paragraph_index': h['paragraph_index'],
            'format_signature': h['format_signature'],
            'text_metadata': h['text_metadata'],
            'borders_detail': h['borders_detail'],
            'classification_confidence': h['classification_confidence'],
            'classification_reasoning': h['classification_reasoning'],
            'is_rescued_locked_header': h.get('is_rescued_locked_header', False),
            'children': []
        }

        if level == 'H1':
            hierarchy.append(node)
            current_h1 = node
            current_h2 = None

        elif level == 'H2':
            if current_h1:
                current_h1['children'].append(node)
                current_h2 = node
            else:
                # Orphan H2 - promote to H1
                hierarchy.append(node)
                current_h2 = None

        elif level == 'H3':
            if current_h2:
                current_h2['children'].append(node)
            elif current_h1:
                current_h1['children'].append(node)
            else:
                hierarchy.append(node)

    return hierarchy


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def _write_hierarchy(f, nodes: list[dict], depth: int = 0) -> None:
    """Write ``[LEVEL] text`` lines, indented two spaces per depth, to ``f``.

    Iterative pre-order walk so a deep header chain cannot hit the recursion limit.
    """
    stack = [(node, depth) for node in reversed(nodes)]
    while stack:
        node, d = stack.pop()
        f.write(f"{'  ' * d}[{node['level']}] {node['text']}\n")
        children = node.get('children')
        if children:
            stack.extend((child, d + 1) for child in reversed(children))


def segment_cv_with_signatures(docx_path: str, output_path: str | None = None) -> dict:
    """
    Complete signature-based segmentation pipeline.

    Steps:
    1-2. Extract paragraphs → Build format signatures
    3-4. Group by signature → Compute prominence scores
    5.   LLM classification of signature groups
    6.   Build hierarchical structure
    """

    print("=" * 80)
    print("SIGNATURE-BASED CV SEGMENTATION")
    print("=" * 80)
    print(f"Input: {docx_path}\n")

    # Step 1-2: Extract paragraphs (including from tables)
    print("Step 1-2: Extracting paragraphs and building format signatures...")
    doc = Document(docx_path)

    # Extract ALL paragraphs including those in tables
    all_paras = extract_all_paragraphs(doc)
    total_paras = len(all_paras)

    paragraphs = []
    for idx, para in enumerate(all_paras):
        para_data = extract_paragraph_signature(para, idx, total_paras)
        # Filter empty paragraphs
        if para_data['text'].strip():
            paragraphs.append(para_data)

    print(f"  ✓ Extracted {len(paragraphs)} non-empty paragraphs (including table content)")

    # Step 3: Group by signature
    print("\nStep 3: Grouping paragraphs by format signature...")
    signature_groups = group_by_signature(paragraphs)
    print(f"  ✓ Found {len(signature_groups)} unique format signatures")

    # Show top groups
    groups_with_prominence = [
        (sig_hash, paras, compute_prominence_score(paras))
        for sig_hash, paras in signature_groups.items()
    ]
    groups_with_prominence.sort(key=lambda x: x[2], reverse=True)

    print("\n  Top signature groups by prominence:")
    for sig_hash, paras, prominence in groups_with_prominence[:8]:
        examples = [p['text'][:40] for p in paras[:3]]
        print(f"    {sig_hash[:8]}: {len(paras):3d} paras, prominence={prominence:.3f}")
        print(f"      Examples: {examples}")

    # Step 4-5: LLM classification
    print(f"\nStep 5: Classifying {len(signature_groups)} signature groups with LLM...")
    classifications = classify_signature_groups_with_llm(signature_groups)

    print("  ✓ Classifications:")
    for sig_hash, cls in classifications.items():
        if cls['level'] != 'NOT_HEADER':
            count = len(signature_groups[sig_hash])
            print(f"    [{cls['level']}] {count:3d} instances - {cls['reasoning'][:70]}")

    # Step 5b: Rescue known section headers from NOT_HEADER groups
    print(f"\nStep 5b: Scanning for known section headers in NOT_HEADER groups...")
    classifications = rescue_locked_headers(signature_groups, classifications)

    # Step 6: Build hierarchy
    print("\nStep 6: Building hierarchical structure...")
    hierarchy = build_hierarchy_from_classifications(signature_groups, classifications)

    print(f"  ✓ Built hierarchy with {len(hierarchy)} top-level sections")

    # Step 6b: Ensure PERSONAL DATA is the first section (remove CV titles, add synthetic if needed)
    print("\nStep 6b: Ensuring PERSONAL DATA is first section...")
    hierarchy = ensure_personal_data_first(hierarchy)

    # Step 7: Validate headers vs entries (filter out table headers and specific items first)
    hierarchy = validate_headers_vs_entries(hierarchy)
    print()

    # Step 8: Normalize hierarchy with GPT (after filtering, so GPT only sees clean headers)
    hierarchy = normalize_hierarchy_with_llm(hierarchy)
    print()

    # Step 9: Second pass of hierarchy normalization to further refine structure
    print("\nStep 9: Second pass of hierarchy normalization...")
    hierarchy = normalize_hierarchy_with_llm(hierarchy, pass_number=2)
    print()

    # Show structure
    print("Final Structure:")
    for section in hierarchy:
        print(f"  [{section['level']}] {section['text']}")
        for child in section['children']:
            print(f"    [{child['level']}] {child['text']}")
            for grandchild in child.get('children', []):
                print(f"      [{grandchild['level']}] {grandchild['text']}")

    # Build output
    result = {
        'document_uid': Path(docx_path).stem,
        'meta': {
            'total_paragraphs': len(paragraphs),
            'unique_signatures': len(signature_groups),
            'total_headers': sum(
                len(signature_groups[sig_hash])
                for sig_hash, cls in classifications.items()
                if cls['level'] != 'NOT_HEADER'
            ),
            'top_level_sections': len(hierarchy),
            'processing_method': 'signature_based_segmentation_with_llm'
        },
        'hierarchy': hierarchy,
        'signature_groups_summary': [
            {
                'signature_id': sig_hash,
                'count': len(signature_groups[sig_hash]),
                'classification': classifications.get(sig_hash, {}).get('level', 'NOT_HEADER'),
                'prominence': compute_prominence_score(signature_groups[sig_hash]),
                'examples': [p['text'][:60] for p in signature_groups[sig_hash][:5]]
            }
            for sig_hash in signature_groups.keys()
        ]
    }

    # Save JSON output using OutputManager for consistent paths
    from ..core.output_manager import OutputManager

    if output_path:
        output_file = Path(output_path)
        text_output_file = output_file.with_suffix('.txt')
    else:
        # Use OutputManager for standard structure
        om = OutputManager(docx_path)
        output_file = om.get_stage1_json_path()
        text_output_file = om.get_stage1_txt_path()

    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"\n✓ Saved JSON to: {output_file}")

    # Save human-readable text output with hierarchy
    with open(text_output_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("CV SEGMENTATION RESULTS - INFERRED HIERARCHY\n")
        f.write("=" * 80 + "\n")
        f.write(f"Document: {Path(docx_path).name}\n")
        f.write(f"Total paragraphs: {len(paragraphs)}\n")
        f.write(f"Unique format signatures: {len(signature_groups)}\n")
        f.write(f"Total headers detected: {result['meta']['total_headers']}\n")
        f.write(f"Top-level sections: {len(hierarchy)}\n")
        f.write("=" * 80 + "\n\n")

        f.write("INFERRED HEADER HIERARCHY:\n")
        f.write("-" * 80 + "\n\n")

        _write_hierarchy(f, hierarchy)

        f.write("\n" + "=" * 80 + "\n")
        f.write("SIGNATURE GROUPS SUMMARY:\n")
        f.write("=" * 80 + "\n\n")

        # Sort by prominence
        groups_sorted = sorted(
            result['signature_groups_summary'],
            key=lambda x: x['prominence'],
            reverse=True
        )

        for group in groups_sorted:
            f.write(f"Signature ID: {group['signature_id']}\n")
            f.write(f"  Classification: {group['classification']}\n")
            f.write(f"  Count: {group['count']} paragraphs\n")
            f.write(f"  Prominence Score: {group['prominence']:.3f}\n")
            f.write(f"  Examples:\n")
            for example in group['examples']:
                f.write(f"    - {example}\n")
            f.write("\n")

    print(f"✓ Saved text summary to: {text_output_file}")
    print("=" * 80)

    return result


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python signature_based_segmentation.py <docx_file> [output_file]")
        print("\nExample:")
        print("  python signature_based_segmentation.py cv.docx")
        print("  python signature_based_segmentation.py cv.docx output.json")
        sys.exit(1)

    docx_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        result = segment_cv_with_signatures(docx_path, output_path)

        print("\n" + "=" * 80)
        print("SEGMENTATION COMPLETE")
        print("=" * 80)
        print(f"Total headers: {result['meta']['total_headers']}")
        print(f"Top-level sections: {result['meta']['top_level_sections']}")
        print(f"Unique signatures: {result['meta']['unique_signatures']}")
        print("=" * 80)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
