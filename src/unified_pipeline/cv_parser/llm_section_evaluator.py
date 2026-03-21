"""
LLM-based section header evaluation using GPT-4o-mini.

This module provides functionality to evaluate whether a line of text
is a CV section header using an LLM, replacing complex heuristic scoring.
"""
import json
import logging
from typing import Dict, List, Optional, Any
from openai import OpenAI

logger = logging.getLogger(__name__)


SECTION_HEADER_PROMPT = """You are analyzing a line from an academic CV to determine if it's a section header.

A section header is a title that introduces a major section of the CV, such as:
- Education
- Publications
- Grants and Funding
- Teaching Experience
- Professional Appointments

Section headers are NOT:
- Person names (even if formatted)
- Dates or date ranges
- Publication titles or citations
- Grant details or amounts
- Job descriptions
- Page headers/footers
- Individual publication/grant records

Line text: "{text}"

Formatting context:
- Font size: {font_size}pt (body text is typically {body_size}pt)
- Bold: {is_bold}
- All caps: {is_caps}
- Has colon at end: {has_colon}
- Centered: {is_centered}

Is this a CV section header? Respond with JSON:
{{
  "is_header": true or false,
  "confidence": 0-100 (your confidence in this assessment),
  "reasoning": "brief explanation of your decision"
}}"""


def evaluate_section_header(
    line_text: str,
    font_size: float = 12.0,
    body_size: float = 12.0,
    is_bold: bool = False,
    is_caps: bool = False,
    has_colon: bool = False,
    is_centered: bool = False,
    client: Optional[OpenAI] = None
) -> Dict[str, Any]:
    """
    Evaluate whether a line is a CV section header using GPT-4o-mini.

    Args:
        line_text: The text of the line to evaluate
        font_size: Font size of the line
        body_size: Typical body text font size in the document
        is_bold: Whether the line is bold
        is_caps: Whether the line is all caps
        has_colon: Whether the line ends with a colon
        is_centered: Whether the line is centered
        client: OpenAI client (created if not provided)

    Returns:
        Dict with keys: is_header (bool), confidence (int), reasoning (str), error (str if failed)
    """
    if client is None:
        client = OpenAI()

    # Format the prompt
    prompt = SECTION_HEADER_PROMPT.format(
        text=line_text[:200],  # Truncate very long lines
        font_size=font_size,
        body_size=body_size,
        is_bold=is_bold,
        is_caps=is_caps,
        has_colon=has_colon,
        is_centered=is_centered
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200
        )

        content = response.choices[0].message.content
        result = json.loads(content)

        # Validate and normalize response
        is_header = result.get("is_header", False)
        confidence = max(0, min(100, int(result.get("confidence", 0))))
        reasoning = result.get("reasoning", "").strip()

        return {
            "is_header": is_header,
            "confidence": confidence,
            "reasoning": reasoning,
            "error": None
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response: {e}")
        return {
            "is_header": False,
            "confidence": 0,
            "reasoning": "",
            "error": f"JSON parse error: {e}"
        }
    except Exception as e:
        logger.error(f"LLM evaluation failed: {e}")
        return {
            "is_header": False,
            "confidence": 0,
            "reasoning": "",
            "error": str(e)
        }


def evaluate_batch(
    candidates: List[Dict],
    body_size: float = 12.0,
    min_confidence: int = 70
) -> List[Dict]:
    """
    Evaluate multiple candidate lines in sequence.

    Args:
        candidates: List of dicts with keys: text, font_size, is_bold, is_caps, etc.
        body_size: Typical body text font size
        min_confidence: Minimum confidence threshold to accept as header

    Returns:
        List of candidates with added llm_result key containing evaluation
    """
    client = OpenAI()

    results = []
    for i, candidate in enumerate(candidates):
        logger.info(f"Evaluating candidate {i+1}/{len(candidates)}: {candidate.get('text', '')[:60]}")

        llm_result = evaluate_section_header(
            line_text=candidate.get('text', ''),
            font_size=candidate.get('font_size', body_size),
            body_size=body_size,
            is_bold=candidate.get('is_bold', False),
            is_caps=candidate.get('is_caps', False),
            has_colon=candidate.get('text', '').endswith(':'),
            is_centered=candidate.get('is_centered', False),
            client=client
        )

        # Add LLM result to candidate
        candidate['llm_result'] = llm_result
        candidate['llm_accepted'] = (
            llm_result['is_header'] and
            llm_result['confidence'] >= min_confidence and
            llm_result['error'] is None
        )

        results.append(candidate)

    return results


def format_evaluation_report(candidate: Dict) -> str:
    """
    Format a human-readable report of an LLM evaluation.

    Args:
        candidate: Candidate dict with llm_result key

    Returns:
        Formatted string report
    """
    llm = candidate.get('llm_result', {})
    text = candidate.get('text', '')

    report = []
    report.append(f"Text: {text[:80]}")
    report.append(f"Decision: {'✓ HEADER' if llm.get('is_header') else '✗ NOT HEADER'}")
    report.append(f"Confidence: {llm.get('confidence', 0)}/100")
    report.append(f"Reasoning: {llm.get('reasoning', 'N/A')}")

    if llm.get('error'):
        report.append(f"Error: {llm['error']}")

    formatting = []
    if candidate.get('is_bold'):
        formatting.append('bold')
    if candidate.get('is_caps'):
        formatting.append('CAPS')
    if candidate.get('is_centered'):
        formatting.append('centered')
    if candidate.get('text', '').endswith(':'):
        formatting.append('colon')

    if formatting:
        report.append(f"Formatting: {', '.join(formatting)}")

    return '\n  '.join(report)
