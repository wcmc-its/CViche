"""
Word CV Segmentation - Chunked Approach (V6)

Four-pass architecture for processing large Word CVs:
- Pass 1a: HEADER DETECTION - Identify section boundaries using 11 structural/heuristic signals
- Pass 1b: HEADER VALIDATION - Filter false headers + score header-likeness (0.0-1.0)
- Pass 2: CONTENT SEGMENTATION - Process each section's content into entries with LLM
- Pass 3: HIERARCHY BUILDING - Merge results into final hierarchical JSON structure

V6 Enhancements:
- ~850 locked headers (never removed)
- Header-likeness scoring (0.0-1.0)
- Broader filtering (fragments, citations, data blobs)
- entry_type removed from schema

Cost: ~$0.13-0.15 per CV (V6: +$0.01-0.02 for header validation)
Speed: ~30-60 seconds per CV
Scale: Unlimited (no rate limit issues)
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from openai import OpenAI

# Handle both relative and absolute imports for flexible usage
try:
    from ..core.docx_structure_extractor import extract_docx_structure, extract_unified_elements
except (ImportError, ValueError):
    # Fallback to absolute import when called from batch scripts
    from core.docx_structure_extractor import extract_docx_structure, extract_unified_elements

# Use default environment context to avoid expensive SKU mapping
client = OpenAI()

# Chunking thresholds
MAX_CHARS_PER_SECTION = 12000  # Conservative to stay under token limits
MAX_ENTRIES_PER_CALL = 50      # Similar to PDF approach

# V6: WCM Taxonomy-based known CV section headers
# Used as signal #11 in header detection to boost confidence for recognized section titles
KNOWN_CV_HEADERS = {
    # Section A: Contact/Personal Data
    'contact information', 'personal data', 'contact info', 'personal information',
    'name', 'address', 'email', 'phone', 'office',

    # Section B: Education
    'education', 'education and training', 'academic background',
    'degrees', 'graduate education', 'undergraduate education',
    'doctoral degree', 'medical degree', 'other education', 'professional development',

    # Section C: Postdoctoral Training
    'postdoctoral training', 'postdoctoral fellowship', 'post-doctoral',
    'residency training', 'fellowship training', 'internships',
    'post graduate training and fellowship appointments',

    # Section D: Professional Positions
    'professional positions', 'employment', 'academic appointments',
    'professional experience', 'work experience', 'positions held',
    'academic positions', 'clinical positions', 'administrative positions',
    'positions', 'positions: academic', 'positions: professional', 'positions: other',
    'previous positions', 'current position', 'current positions',

    # Section E: Employment Status
    'employment status', 'current appointment', 'appointment status',

    # Section F: Licensure & Certification
    'licensure', 'board certification', 'certifications', 'licenses',
    'licensure and certification', 'professional licenses',
    'certification and licensure', 'license to practice',

    # Section G: Institutional Affiliations
    'institutional affiliations', 'hospital affiliations', 'affiliations',

    # Section H: Honors & Awards
    'honors and awards', 'honors', 'awards', 'recognitions', 'distinctions',
    'prizes', 'fellowships', 'named lectureships',
    'honors: scholarships/grants', 'honors: other', 'scholarships', 'scholarships/grants',

    # Section I: Professional Organizations
    'professional organizations', 'memberships', 'societies',
    'professional societies', 'professional memberships',
    'scientific appointments: memberships', 'organizations and professional societies',

    # Section J: Percent Effort
    'percent effort', 'institutional responsibilities', 'effort distribution',

    # Section K: Educational Contributions
    'educational contributions', 'teaching', 'teaching experience', 'teaching experiences',
    'course teaching', 'medical student teaching', 'resident teaching',
    'curriculum development', 'educational leadership',
    'institutional teaching activities',

    # Section L: Clinical Practice
    'clinical practice', 'clinical leadership', 'clinical activities',
    'patient care', 'clinical work',

    # Section M: Research
    'research', 'research overview', 'research interests', 'research experience',
    'grant support', 'funding', 'research support', 'grants', 'research grants',
    'current funding', 'past funding', 'pending funding',
    'active grants', 'completed grants', 'current research studies',
    'clinical trials', 'research projects',

    # Section N: Mentoring
    'mentoring', 'mentorship', 'mentoring and supervision', 'student supervision',
    'trainees', 'mentees', 'current mentees', 'past mentees',
    'training grants', 'mentoring philosophy',

    # Section O: Institutional Leadership
    'institutional leadership', 'leadership activities', 'leadership roles',
    'committee service', 'institutional service',
    'committees and work groups',

    # Section P: Administrative Activities
    'administrative activities', 'administration', 'administrative roles',
    'institutional administration', 'departmental service',

    # Section Q: Extramural Professional Activities
    'extramural professional activities', 'professional responsibilities',
    'editorial activities', 'editorial boards', 'reviewer activities',
    'grant reviewing', 'peer review', 'national committees',
    'consulting', 'advisory boards',
    'scientific appointments: assistant reviewer', 'editorial review',

    # Section R: Invitations to Speak
    'invitations to speak', 'presentations', 'invited talks',
    'speaking engagements', 'seminars', 'lectures',
    'orals', 'oral presentations', 'invited lectures and oral presentations',

    # Section S: Bibliography/Publications
    'bibliography', 'publications', 'scholarly works',
    'peer-reviewed articles', 'peer reviewed publications', 'journal articles',
    'peer-reviewed journal publications',
    'books', 'book chapters', 'reviews and editorials',
    'non-peer-reviewed publications', 'conference proceedings',
    'abstracts', 'posters', 'poster presentations', 'in review', 'submitted',
    'manuscripts in preparation', 'other publications',

    # Section T: Supplemental
    'supplemental information', 'additional information', 'other',
    'references', 'languages', 'skills', 'public outreach',
    'media appearances', 'bibliometric summary', 'courses attended',
    'professional development courses',

    # Compound section headers (common in structured CVs)
    'positions, scientific appointments, honors',
    'relevant work experience',

    # Common variations and organizational headers
    'curriculum vitae', 'cv', 'resume', 'vita',
    'current', 'past', 'active', 'completed', 'pending',
    'selected', 'representative', 'major', 'significant',

    # Year-based organizational headers (common in CVs)
    '2024', '2023', '2022', '2021', '2020', '2019', '2018', '2017', '2016', '2015',
    '2014', '2013', '2012', '2011', '2010'
}

# Convert to lowercase set for fast O(1) lookup
KNOWN_CV_HEADERS_SET = {h.lower() for h in KNOWN_CV_HEADERS}


def detect_section_headers(structure: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Pass 1: Detect section headers using Word document structure.

    Identifies headers based on VISUAL CUES (not just text):
    - Heading styles (Heading 1, Heading 2, etc.)
    - ALL CAPS paragraphs
    - Bold text followed by non-bold content
    - Centered alignment (major headers)
    - Font size differences (larger = headers)
    - Underline formatting
    - Indentation patterns
    - Ends with colon

    Returns list of headers with metadata:
    - text: Header text
    - idx: Paragraph index in document
    - level: Hierarchical level (1=main section, 2=subsection)
    - confidence: How certain this is a header (0.0-1.0)
    """

    headers = []
    elements = structure['elements']

    # Calculate average font size for context (helps detect "larger" headers)
    font_sizes = [e.get('font_size') for e in elements if e.get('type') == 'paragraph' and e.get('font_size')]
    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12.0

    for i, elem in enumerate(elements):
        # Process paragraphs AND table_header elements (from extract_unified_elements)
        if elem['type'] not in ('paragraph', 'table_header'):
            continue

        text = elem['text'].strip()
        if not text or len(text) < 2:
            continue

        # COMPOSITE SCORING: Start with base, add signals
        confidence = 0.0
        level = 1
        signals_detected = []

        # === TABLE HEADER SIGNAL (from extract_unified_elements) ===
        # Table headers already passed header detection, so give them a boost
        if elem['type'] == 'table_header':
            confidence = elem.get('header_confidence', 0.60)
            signals_detected.append('table_header')
            # Continue to apply other signals to potentially boost further

        # === STRONGEST SIGNALS (decisive) ===

        # 1. Heading style (STRONGEST - Word's native structure)
        if elem.get('outline_level') is not None:
            confidence = 0.95
            level = elem['outline_level']
            signals_detected.append('heading_style')

        # 2. Style name contains "Heading"
        elif 'Heading' in elem.get('style', ''):
            confidence = 0.90
            try:
                level = int(elem['style'].split()[-1])
            except:
                level = 1
            signals_detected.append('heading_style_name')

        # === STRONG VISUAL SIGNALS (additive if no heading style) ===
        else:
            # Start with base confidence for short text
            # BUT don't reset if we already have confidence from table_header
            if len(text) < 150 and confidence == 0.0:
                confidence = 0.30  # Base score for potential header

            # 3. Centered alignment (major headers like "CURRICULUM VITAE")
            if elem.get('alignment') == 'center' and len(text) < 100:
                confidence += 0.50
                level = 1
                signals_detected.append('centered')

            # 4. ALL CAPS (common for section headers)
            if text.isupper() and len(text) < 150:
                confidence += 0.45
                signals_detected.append('all_caps')

            # 5. Bold text
            if elem.get('bold') and len(text) < 150 and elem.get('list_level') is None:
                # Check if next element is not bold (header followed by content)
                if i + 1 < len(elements):
                    next_elem = elements[i + 1]
                    if next_elem.get('type') == 'paragraph' and not next_elem.get('bold'):
                        confidence += 0.40
                        # Determine level: Major sections (longer, descriptive) are L1
                        # Short subsection headers (single words) are L2
                        if text.endswith(':'):
                            # Remove colon and whitespace for length check
                            text_core = text.rstrip(':').strip()
                            # Major sections: "Academic Appointments", "Postdoctoral Training"
                            # Subsections: "Local", "National", "International"
                            if len(text_core) > 15 or ' ' in text_core:
                                level = 1  # Major section (multi-word or long)
                            else:
                                level = 2  # Subsection (single short word)
                        else:
                            level = 2  # Default subsection
                        signals_detected.append('bold_before_plain')
                    else:
                        confidence += 0.25
                        signals_detected.append('bold')
                else:
                    confidence += 0.30
                    signals_detected.append('bold')

            # 6. Ends with colon (common CV pattern: "Education:")
            if text.endswith(':') and len(text) < 100:
                confidence += 0.35
                signals_detected.append('ends_colon')

            # 7. Underline formatting
            if elem.get('underline') and len(text) < 150:
                confidence += 0.25
                signals_detected.append('underline')

            # 8. Larger font size (headers often bigger)
            if elem.get('font_size') and elem.get('font_size') > avg_font_size * 1.2:
                confidence += 0.30
                signals_detected.append('large_font')

            # 9. Zero indentation + short (left-aligned headers)
            if elem.get('indent_left') == 0.0 and len(text) < 80 and elem.get('list_level') is None:
                confidence += 0.15
                signals_detected.append('no_indent')

            # 10. Unusual spacing/whitespace patterns (lots of spaces = formatted header)
            if '   ' in text or '\t' in text:  # Multiple spaces or tabs
                confidence += 0.10
                signals_detected.append('whitespace')

            # 11. Known CV section header (V6: WCM taxonomy-based)
            text_lower = text.lower().rstrip(':').strip()
            if text_lower in KNOWN_CV_HEADERS_SET:
                confidence += 0.45  # Strong boost for recognized section titles
                signals_detected.append('known_header')

        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)

        # Only include if confidence above threshold
        if confidence >= 0.60:
            headers.append({
                'text': text,
                # Use unified_idx if available (from extract_unified_elements), else fall back to idx
                'idx': elem.get('unified_idx', elem.get('idx')),
                'level': level,
                'confidence': round(confidence, 3),
                'style': elem.get('style'),
                'bold': elem.get('bold', False),
                'signals': signals_detected,  # For debugging
                'source_type': elem['type'],  # Track if from paragraph or table_header
                'table_index': elem.get('table_index'),  # Track table index if from table
            })

    # Post-process: Enforce consistent hierarchy for known subsection patterns
    headers = _enforce_hierarchy_consistency(headers)

    return headers


# Known subsection terms that should NEVER be H1 (top-level)
# These are organizational subdivisions, not major CV sections
KNOWN_SUBSECTION_TERMS = {
    # Geographic scope (common under Presentations, Service, etc.)
    'international', 'national', 'regional', 'local', 'state', 'institutional',
    # Time-based
    'current', 'past', 'completed', 'active', 'pending', 'ongoing',
    # Role-based
    'principal investigator', 'co-investigator', 'co-pi', 'consultant',
    'advisor', 'mentor', 'co-mentor', 'coordinator', 'student',
    # Publication types (when under Publications)
    'peer-reviewed', 'non-peer-reviewed', 'in preparation', 'submitted', 'in review',
}

def _enforce_hierarchy_consistency(headers: List[Dict]) -> List[Dict]:
    """
    Post-process headers to enforce consistent hierarchy levels.

    Fixes common issues:
    1. Geographic terms (International/National/Regional/Local) orphaned as H1
       should be demoted to H2 or H3 based on context
    2. Repeated subsection patterns should maintain consistent levels

    Example fix:
        Before:
            [H2] Scientific presentations
              [H3] International
              [H2] National      ← inconsistent
            [H1] Regional        ← orphaned
        After:
            [H2] Scientific presentations
              [H3] International
              [H3] National      ← fixed
              [H3] Regional      ← re-parented
    """
    if not headers:
        return headers

    # Build lookup of text to expected level based on first occurrence
    # This ensures consistency: if "National" first appears as L3, subsequent ones are also L3
    first_occurrence_level = {}

    for i, header in enumerate(headers):
        text_lower = header['text'].lower().rstrip(':').strip()

        # Check if this is a known subsection term that shouldn't be H1
        if text_lower in KNOWN_SUBSECTION_TERMS and header['level'] == 1:
            # Look backward for the most recent non-subsection header to find parent
            parent_level = None
            for j in range(i - 1, -1, -1):
                prev_text = headers[j]['text'].lower().rstrip(':').strip()
                if prev_text not in KNOWN_SUBSECTION_TERMS:
                    parent_level = headers[j]['level']
                    break

            # Demote to one level below parent (or L2 if no parent found)
            if parent_level is not None:
                header['level'] = min(parent_level + 1, 3)
            else:
                header['level'] = 2

            if 'signals' in header:
                header['signals'].append('demoted_subsection')

        # Track first occurrence for consistency
        if text_lower not in first_occurrence_level:
            first_occurrence_level[text_lower] = header['level']
        else:
            # Enforce consistency with first occurrence
            expected_level = first_occurrence_level[text_lower]
            if header['level'] != expected_level:
                # Only adjust if current level is HIGHER (more top-level) than expected
                # This prevents demoting intentionally different structures
                if header['level'] < expected_level:
                    header['level'] = expected_level
                    if 'signals' in header:
                        header['signals'].append('level_normalized')

    return headers


def chunk_section(elements: List[Dict], start_idx: int, end_idx: int) -> List[List[Dict]]:
    """
    Split a section into chunks if it exceeds MAX_CHARS_PER_SECTION.

    Uses intelligent splitting:
    - Preserves list continuity (don't split mid-list)
    - Splits on paragraph boundaries
    - Keeps related items together

    Returns list of element chunks.
    """
    section_elements = [e for e in elements if start_idx <= e['idx'] < end_idx]

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


def segment_chunk_with_llm(chunk: List[Dict], section_label: str, section_level: int, segmentation_model: str = "gpt-5.1") -> Dict[str, Any]:
    """
    Pass 2: Use specified model (default: gpt-5.1) to segment a chunk into entries.

    Since chunks are <12K chars, we can safely process with API.
    Uses structured outputs for guaranteed valid JSON.

    Args:
        chunk: List of document elements (paragraphs/tables) to segment
        section_label: Human-readable section name (e.g., "Publications")
        section_level: Hierarchy level (1, 2, or 3)
        segmentation_model: OpenAI model to use (default: gpt-5.1 - full model for best segmentation)

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
        # GPT-5+ models use max_completion_tokens instead of max_tokens
        api_params = {
            "model": segmentation_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "chunk_segmentation",
                    "strict": True,
                    "schema": CHUNK_SCHEMA
                }
            }
        }

        # Model-specific parameter handling
        if segmentation_model.startswith('gpt-5') or segmentation_model.startswith('o1') or segmentation_model.startswith('o3'):
            api_params['max_completion_tokens'] = 8000
            # gpt-5-mini only supports temperature=1 (default), other GPT-5+ models support custom temperatures
            if segmentation_model != 'gpt-5-mini':
                api_params['temperature'] = 0.1
        else:
            api_params['max_tokens'] = 8000
            api_params['temperature'] = 0.1

        response = client.chat.completions.create(**api_params)

        result = json.loads(response.choices[0].message.content)

        # Capture token usage from API response
        usage = response.usage
        result['token_usage'] = {
            'prompt_tokens': usage.prompt_tokens,
            'completion_tokens': usage.completion_tokens,
            'total_tokens': usage.total_tokens
        }

        # Calculate cost (approximate pricing as of 2025-11)
        input_cost_per_1m = {
            'gpt-4o-mini': 0.15,
            'gpt-4o': 2.50,
            'gpt-5-mini': 1.00,  # Estimated
            'gpt-5.1': 5.00,  # Estimated
            'gpt-5': 5.00,  # Estimated
            'o1': 15.00,  # Estimated
            'o3-mini': 1.10  # Estimated
        }
        output_cost_per_1m = {
            'gpt-4o-mini': 0.60,
            'gpt-4o': 10.00,
            'gpt-5-mini': 4.00,  # Estimated
            'gpt-5.1': 15.00,  # Estimated
            'gpt-5': 15.00,  # Estimated
            'o1': 60.00,  # Estimated
            'o3-mini': 4.40  # Estimated
        }

        model_key = segmentation_model
        input_cost = (usage.prompt_tokens / 1_000_000) * input_cost_per_1m.get(model_key, 0)
        output_cost = (usage.completion_tokens / 1_000_000) * output_cost_per_1m.get(model_key, 0)
        total_cost = input_cost + output_cost

        result['cost'] = total_cost
        result['model_used'] = response.model

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
        print(f"      Model: {response.model}")
        print(f"      Tokens: {usage.prompt_tokens} in, {usage.completion_tokens} out")
        print(f"      Cost: ${total_cost:.4f}")

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
        print(f"Model used: {segmentation_model}")
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
            'model_used': segmentation_model,
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


def build_hierarchy(flat_groups: List[Dict]) -> List[Dict]:
    """
    Pass 3b: Build hierarchical structure from flat list of groups.

    Groups are organized by level markers:
    - Level 1 (L1) = top-level groups
    - Level 2 (L2) = subgroups of preceding L1
    - Level 3 (L3) = sub-subgroups of preceding L2

    Example:
        G1 [L1] Major Committee Assignments
        G2 [L2] Local         → becomes G1.1
        G3 [L2] National      → becomes G1.2
        G4 [L2] International → becomes G1.3
        G5 [L1] Awards        → new top-level

    Returns hierarchical list with proper nesting and renumbered IDs.
    """

    hierarchical = []
    current_l1 = None
    current_l2 = None
    subgroup_counter_l1 = {}  # Track subgroup numbering per L1 parent
    subgroup_counter_l2 = {}  # Track sub-subgroup numbering per L2 parent

    for group in flat_groups:
        level = group['level']

        if level == 1:
            # New top-level group
            current_l1 = group.copy()
            current_l1['subgroups'] = []
            hierarchical.append(current_l1)
            current_l2 = None
            subgroup_counter_l1[current_l1['id']] = 0

        elif level == 2:
            # Subgroup of current L1
            if current_l1 is None:
                # No parent L1, treat as top-level
                current_l1 = group.copy()
                current_l1['subgroups'] = []
                hierarchical.append(current_l1)
                current_l2 = None
                subgroup_counter_l1[current_l1['id']] = 0
            else:
                # Create subgroup
                # Initialize counter if not exists
                if current_l1['id'] not in subgroup_counter_l1:
                    subgroup_counter_l1[current_l1['id']] = 0
                subgroup_counter_l1[current_l1['id']] += 1
                subgroup_num = subgroup_counter_l1[current_l1['id']]

                subgroup = group.copy()
                # Renumber ID to show hierarchy: G1 → G1.1, G1.2, etc.
                parent_id = current_l1['id']
                subgroup['id'] = f"{parent_id}.{subgroup_num}"
                subgroup['parent_id'] = parent_id
                subgroup['subgroups'] = []  # For potential L3 children

                current_l1['subgroups'].append(subgroup)
                current_l2 = subgroup
                subgroup_counter_l2[subgroup['id']] = 0

        elif level == 3:
            # Sub-subgroup of current L2
            if current_l2 is None:
                # No parent L2, attach to L1 as L2 instead
                if current_l1 is None:
                    # No parents at all, treat as top-level
                    group_copy = group.copy()
                    group_copy['subgroups'] = []
                    hierarchical.append(group_copy)
                else:
                    # Initialize counter if not exists
                    if current_l1['id'] not in subgroup_counter_l1:
                        subgroup_counter_l1[current_l1['id']] = 0
                    subgroup_counter_l1[current_l1['id']] += 1
                    subgroup_num = subgroup_counter_l1[current_l1['id']]

                    subgroup = group.copy()
                    parent_id = current_l1['id']
                    subgroup['id'] = f"{parent_id}.{subgroup_num}"
                    subgroup['parent_id'] = parent_id
                    subgroup['subgroups'] = []

                    current_l1['subgroups'].append(subgroup)
                    current_l2 = subgroup
                    subgroup_counter_l2[subgroup['id']] = 0
            else:
                # Create sub-subgroup
                # Initialize counter if not exists
                if current_l2['id'] not in subgroup_counter_l2:
                    subgroup_counter_l2[current_l2['id']] = 0
                subgroup_counter_l2[current_l2['id']] += 1
                subsubgroup_num = subgroup_counter_l2[current_l2['id']]

                subsubgroup = group.copy()
                parent_id = current_l2['id']
                subsubgroup['id'] = f"{parent_id}.{subsubgroup_num}"
                subsubgroup['parent_id'] = parent_id
                subsubgroup['subgroups'] = []

                current_l2['subgroups'].append(subsubgroup)

        else:
            # Unexpected level, treat as top-level
            group_copy = group.copy()
            group_copy['subgroups'] = []
            hierarchical.append(group_copy)
            current_l1 = group_copy
            current_l2 = None

    return hierarchical


def segment_word_cv_chunked(docx_path: str, output_dir: str = None, segmentation_model: str = None) -> Dict[str, Any]:
    """
    Main entry point: Segment a Word CV using three-pass chunked approach.

    Args:
        docx_path: Path to Word document to segment
        output_dir: Optional output directory for JSON
        segmentation_model: Model to use for segmentation (default: env var or gpt-5.1)

    Returns dictionary with:
    - num_sections: Number of top-level sections identified
    - total_entries: Total entries extracted
    - output_file: Path to output JSON
    - processing_stats: Detailed statistics
    """

    # Allow model override via environment variable
    if segmentation_model is None:
        segmentation_model = os.getenv('SEGMENTATION_MODEL', 'gpt-5.1')

    print(f"Using model: {segmentation_model}")

    print("="*80)
    print("WORD CV SEGMENTATION - CHUNKED APPROACH (Scalable)")
    print("="*80)
    print(f"Input: {docx_path}")
    print()

    # Extract Word structure
    print("Extracting Word document structure...")
    structure = extract_docx_structure(docx_path)
    print(f"  ✓ Extracted {len(structure['elements'])} elements")
    print()

    # Pass 1a: Detect headers (structural/heuristic signals)
    print("PASS 1a: Header Detection (structural analysis)...")
    headers = detect_section_headers(structure)
    print(f"  ✓ Found {len(headers)} potential section headers")

    # Show detected headers
    for h in headers[:10]:  # Show first 10
        print(f"    [{h['level']}] {h['text']} (confidence: {h['confidence']:.2f})")
    if len(headers) > 10:
        print(f"    ... and {len(headers) - 10} more")
    print()

    # V6: Pass 1b: Header Validation & Scoring (filter false headers + assess confidence)
    print("PASS 1b: Header Validation & Scoring...")
    validation_metadata = None
    try:
        # Import header validation (separate module for clean separation)
        try:
            from .header_validator import filter_false_headers
        except (ImportError, ValueError):
            from header_validator import filter_false_headers

        validation_result = filter_false_headers(
            sections=headers,
            batch_size=20,
            use_llm=True,
            model='gpt-5.1'
        )

        # Update headers with filtered list
        headers = validation_result['filtered_sections']
        removed = validation_result['removed_sections']
        stats = validation_result['stats']
        cost = validation_result['cost']

        # Store validation metadata for final JSON output
        validation_metadata = {
            'validation_applied': True,
            'validation_model': 'gpt-5.1',
            'headers_detected': stats['total_headers_input'],
            'headers_locked': stats['locked_headers'],
            'headers_removed_regex': stats['removed_by_regex'],
            'headers_removed_llm': stats['removed_by_llm'],
            'headers_kept': stats['total_kept'],
            'validation_cost': cost,
            'removed_headers': [
                {
                    'text': r.get('text', ''),
                    'reason': r.get('removal_reason', ''),
                    'filter_method': r.get('filter_method', ''),
                    'score': r.get('header_likeness_score', 0.0)
                }
                for r in removed
            ]
        }

        print(f"  ✓ Validated {stats['total_headers_input']} headers")
        print(f"  ✓ Removed {stats['total_removed']} false headers (${cost:.4f})")
        if removed:
            print(f"    Removed: {', '.join([r['text'][:30] + '...' if len(r['text']) > 30 else r['text'] for r in removed[:5]])}")
            if len(removed) > 5:
                print(f"    ... and {len(removed) - 5} more")
        print(f"  ✓ Final header count: {len(headers)}")
        print()

    except ImportError as e:
        print(f"  ⚠️  Header validation unavailable: {e}")
        print(f"  ⚠️  Continuing with unvalidated headers...")
        validation_metadata = {'validation_applied': False, 'validation_error': str(e)}
        print()

    # Pass 2: Process each section
    print("PASS 2: Processing sections with chunking...")
    groups = []
    group_counter = 1
    total_chunks_processed = 0

    # Track token usage and cost (initialize with Pass 1b validation cost)
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cost = 0.0

    # Add Pass 1b validation cost if available
    if validation_metadata and 'validation_cost' in validation_metadata:
        total_cost += validation_metadata['validation_cost']

    elements = structure['elements']

    for i, header in enumerate(headers):
        # Determine section boundaries
        start_idx = header['idx']
        end_idx = headers[i+1]['idx'] if i+1 < len(headers) else len(elements)

        section_label = header['text']
        print(f"  Processing: {section_label}...")

        # Chunk this section if needed
        chunks = chunk_section(elements, start_idx, end_idx)
        print(f"    → {len(chunks)} chunk(s)")

        # Process each chunk
        chunk_results = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_chars = sum(len(e.get('text', '')) for e in chunk)
            print(f"       Chunk {chunk_idx+1}: {len(chunk)} elements, {chunk_chars:,} characters")

            result = segment_chunk_with_llm(chunk, section_label, header['level'], segmentation_model)
            chunk_results.append(result)
            total_chunks_processed += 1

            # Aggregate token usage and cost
            if 'token_usage' in result:
                usage = result['token_usage']
                total_prompt_tokens += usage.get('prompt_tokens', 0)
                total_completion_tokens += usage.get('completion_tokens', 0)
                total_tokens += usage.get('total_tokens', 0)
            if 'cost' in result:
                total_cost += result['cost']

        # Merge chunks for this section
        group_id = f"G{group_counter}"
        merged_group = merge_chunk_results(chunk_results, header, group_id)
        groups.append(merged_group)

        print(f"    ✓ Extracted {len(merged_group['entries'])} entries")
        group_counter += 1

    print()
    print(f"PASS 3: Building hierarchical structure...")

    # Pass 3b: Build hierarchy from flat groups
    hierarchical_groups = build_hierarchy(groups)

    # Count hierarchical statistics
    def count_all_groups(groups):
        """Recursively count all groups and subgroups"""
        count = len(groups)
        for g in groups:
            count += count_all_groups(g.get('subgroups', []))
        return count

    total_groups_hierarchical = count_all_groups(hierarchical_groups)
    print(f"  ✓ Organized {len(groups)} flat sections into {len(hierarchical_groups)} top-level groups")
    print(f"     (total groups including subgroups: {total_groups_hierarchical})")

    # Calculate total entries (including nested)
    def count_all_entries(groups):
        """Recursively count all entries in groups and subgroups"""
        count = 0
        for g in groups:
            count += len(g.get('entries', []))
            count += count_all_entries(g.get('subgroups', []))
        return count

    total_entries = count_all_entries(hierarchical_groups)

    # Build final JSON structure
    document_uid = Path(docx_path).stem

    result = {
        "document_uid": document_uid,
        "meta": {
            "num_top_level_groups": len(hierarchical_groups),
            "total_groups_including_subgroups": total_groups_hierarchical,
            "total_entries": total_entries,
            "processing_method": "chunked_word_segmentation_hierarchical",
            "chunks_processed": total_chunks_processed
        },
        "groups": hierarchical_groups
    }

    # Add validation metadata if available (V6: Pass 1b)
    if validation_metadata:
        result['meta']['header_validation'] = validation_metadata

    # Save output using OutputManager for consistent paths
    from ..core.output_manager import OutputManager

    if output_dir is None:
        # Use default output structure
        om = OutputManager(docx_path)
        output_file = om.get_stage1_json_path()
        output_txt = om.get_stage1_txt_path()
    else:
        # Use specified output directory
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{document_uid}_segmented.json"
        output_txt = output_dir / f"{document_uid}_segmented.txt"

    # Save JSON output
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"  ✓ Saved JSON to: {output_file}")

    # Also save human-readable text version (hierarchy only, matching signature_based format)
    try:
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(f"CV Hierarchy: {document_uid}\n")
            f.write("=" * 80 + "\n\n")

            def write_group(group, depth=0):
                indent = "  " * depth
                # Use label_inferred (the correct key in the JSON schema)
                label = group.get('label_inferred') or group.get('title', 'NO TITLE')
                # Convert numeric level to H1/H2/H3 format
                level_num = group.get('level', 1)
                level_marker = f"[H{level_num}]"
                f.write(f"{indent}{level_marker} {label}\n")
                if 'subgroups' in group and group['subgroups']:
                    for subgroup in group['subgroups']:
                        write_group(subgroup, depth + 1)

            for group in hierarchical_groups:
                write_group(group)

        print(f"  ✓ Saved TXT to: {output_txt}")
    except Exception as e:
        print(f"  ⚠ Could not save TXT version: {e}")

    print()

    # Return summary with token usage
    return {
        "num_sections": len(hierarchical_groups),
        "num_sections_including_subgroups": total_groups_hierarchical,
        "total_entries": total_entries,
        "output_file": str(output_file),
        "chunks_processed": total_chunks_processed,
        "format": "docx",
        "approach": "chunked-word-segmentation-hierarchical",
        "total_cost": total_cost,
        "token_usage": {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens
        }
    }


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python word_cv_segmentation_chunked.py <docx_file> [output_dir]")
        print()
        print("Example:")
        print("  python word_cv_segmentation_chunked.py cv.docx")
        print("  python word_cv_segmentation_chunked.py cv.docx ./outputs")
        sys.exit(1)

    docx_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        result = segment_word_cv_chunked(docx_path, output_dir)

        print("="*80)
        print("PROCESSING COMPLETE")
        print("="*80)
        print(f"Sections: {result['num_sections']}")
        print(f"Entries: {result['total_entries']}")
        print(f"Chunks: {result['chunks_processed']}")
        print(f"Output: {result['output_file']}")
        print("="*80)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
