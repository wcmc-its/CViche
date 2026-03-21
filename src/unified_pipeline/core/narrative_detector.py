"""
Narrative Content Detection

Detects when content is descriptive/narrative vs. structured extractable data.

Use cases:
1. High-confidence classification but extraction fails (missing structured fields)
2. Content that doesn't fit extraction schema but belongs in section
3. Research overview paragraphs, teaching philosophy statements, etc.

Strategy: Preserve as descriptive text rather than forcing into structured format.
"""

import re
from typing import Dict, List, Optional, Any


def has_structured_markers(text: str) -> bool:
    """
    Check if text has structural markers typical of extractable content.

    Args:
        text: Content to analyze

    Returns:
        True if structured markers found
    """
    # Date patterns (years, date ranges)
    if re.search(r'\b(19|20)\d{2}\b', text):
        return True

    # Institution indicators
    institution_patterns = [
        r'\bUniversity\b',
        r'\bCollege\b',
        r'\bInstitute\b',
        r'\bHospital\b',
        r'\bSchool of\b',
        r'\bDepartment of\b'
    ]
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in institution_patterns):
        return True

    # Role/position indicators
    if any(word in text.lower() for word in ['professor', 'director', 'chair', 'fellow', 'resident', 'investigator']):
        return True

    # Publication markers (journal names, DOI, PMID)
    if re.search(r'\b(doi:|pmid:|pmcid:)', text, re.IGNORECASE):
        return True

    # List formatting (bullets, numbering)
    if re.match(r'^\s*[•\-\*\d]+[\.\)]\s', text, re.MULTILINE):
        return True

    return False


def detect_narrative_language(text: str) -> List[str]:
    """
    Detect narrative/descriptive language patterns.

    Args:
        text: Content to analyze

    Returns:
        List of detected narrative indicators
    """
    indicators = []

    # First-person narrative
    first_person_patterns = [
        r'\bmy research\b',
        r'\bmy work\b',
        r'\bmy interests\b',
        r'\bmy approach\b',
        r'\bmy teaching\b',
        r'\bI focus\b',
        r'\bI study\b',
        r'\bI investigate\b'
    ]
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in first_person_patterns):
        indicators.append('first_person_narrative')

    # Descriptive phrases
    descriptive_phrases = [
        'interests include',
        'approach to',
        'philosophy is',
        'committed to',
        'focuses on',
        'encompasses',
        'involves',
        'dedicated to',
        'specializes in',
        'concentrates on'
    ]
    if any(phrase in text.lower() for phrase in descriptive_phrases):
        indicators.append('descriptive_language')

    # Statement/summary language
    if any(word in text.lower() for word in ['overview', 'summary', 'statement', 'description']):
        indicators.append('summary_language')

    # Qualitative adjectives (not typical in structured data)
    qualitative_words = [
        'innovative', 'groundbreaking', 'comprehensive', 'extensive',
        'pioneering', 'leading', 'prominent', 'distinguished'
    ]
    if sum(1 for word in qualitative_words if word in text.lower()) >= 2:
        indicators.append('qualitative_adjectives')

    return indicators


def get_required_fields(section_id: str) -> List[str]:
    """
    Get required fields for a given section for extraction.

    Args:
        section_id: Section identifier (e.g., 'bibliography', 'education_and_training')

    Returns:
        List of required field names
    """
    # Map sections to their required fields
    required_fields_map = {
        'bibliography': ['authors', 'title', 'year'],
        'education_and_training': ['degree', 'institution', 'year'],
        'postdoctoral_training': ['position', 'institution', 'dates'],
        'professional_positions_employment': ['title', 'institution', 'dates'],
        'honors_and_awards': ['award_name', 'year'],
        'research_overview': ['title', 'description'],
        'mentoring': ['mentee_name', 'project', 'dates'],
        'educational_contributions': ['activity_type', 'description'],
        'invitations_to_speak': ['title', 'venue', 'date']
    }

    return required_fields_map.get(section_id, [])


def detect_narrative_content(
    entry_text: str,
    classification_section_id: str,
    classification_confidence: float,
    extraction_result: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Detect if content is narrative/descriptive vs. extractable structured data.

    Args:
        entry_text: The entry content to analyze
        classification_section_id: Section ID from classification
        classification_confidence: Confidence score from classification (0.0-1.0)
        extraction_result: Optional extraction attempt result

    Returns:
        {
            "is_narrative": bool,
            "confidence": float,
            "indicators": List[str],
            "reasoning": str,
            "recommended_action": str
        }
    """
    indicators = []

    # Indicator 1: Extraction failed but classification was high confidence
    extraction_failed = False
    if extraction_result:
        extraction_failed = (
            extraction_result.get('confidence', 1.0) < 0.60 or
            extraction_result.get('error') is not None
        )

    if extraction_failed and classification_confidence >= 0.85:
        indicators.append("extraction_failure_despite_high_classification")

    # Indicator 2: Missing required structured fields
    missing_fields_count = 0
    if extraction_result and 'data' in extraction_result:
        required_fields = get_required_fields(classification_section_id)
        extracted_data = extraction_result.get('data', {})
        missing_fields = [f for f in required_fields if f not in extracted_data or not extracted_data[f]]
        missing_fields_count = len(missing_fields)

        if len(required_fields) > 0 and missing_fields_count >= len(required_fields) * 0.5:
            indicators.append(f"missing_{missing_fields_count}_of_{len(required_fields)}_required_fields")

    # Indicator 3: Paragraph structure without structural markers
    sentences = entry_text.split('.')
    has_markers = has_structured_markers(entry_text)

    if len(sentences) >= 3 and not has_markers:
        indicators.append("paragraph_structure_no_markers")

    # Indicator 4: Narrative language patterns
    narrative_patterns = detect_narrative_language(entry_text)
    if narrative_patterns:
        indicators.extend(narrative_patterns)

    # Indicator 5: Very long text (>500 chars) without structured elements
    if len(entry_text) > 500 and not has_markers:
        indicators.append("long_text_no_structure")

    # Determine if narrative
    is_narrative = len(indicators) >= 2

    # Calculate confidence
    if len(indicators) >= 4:
        confidence = 0.95
    elif len(indicators) == 3:
        confidence = 0.85
    elif len(indicators) == 2:
        confidence = 0.70
    else:
        confidence = 0.50

    # Build reasoning
    if is_narrative:
        reasoning = f"Content appears to be narrative/descriptive based on: {', '.join(indicators[:3])}"
    else:
        reasoning = "Content appears to be structured/extractable data"

    # Recommended action
    if is_narrative:
        recommended_action = "preserve_as_descriptive_text"
    else:
        if extraction_failed:
            recommended_action = "retry_extraction_with_reclassification"
        else:
            recommended_action = "standard_extraction"

    return {
        "is_narrative": is_narrative,
        "confidence": confidence,
        "indicators": indicators,
        "reasoning": reasoning,
        "recommended_action": recommended_action,
        "extraction_failed": extraction_failed,
        "classification_confidence": classification_confidence,
        "missing_fields_count": missing_fields_count
    }


def preserve_as_narrative(
    entry_text: str,
    classification_section_id: str,
    classification_confidence: float,
    narrative_detection: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Preserve content as narrative/descriptive text with metadata.

    Args:
        entry_text: The content to preserve
        classification_section_id: Section ID from classification
        classification_confidence: Classification confidence
        narrative_detection: Result from detect_narrative_content

    Returns:
        Structured narrative preservation record
    """
    return {
        "section_id": classification_section_id,
        "content_type": "narrative_description",
        "text": entry_text,
        "classification_confidence": classification_confidence,
        "narrative_detection_confidence": narrative_detection['confidence'],
        "metadata": {
            "detection_reason": narrative_detection['indicators'],
            "reasoning": narrative_detection['reasoning'],
            "original_extraction_attempted": narrative_detection.get('extraction_failed', False),
            "preserved_as_narrative": True,
            "timestamp": None  # Will be set by caller
        }
    }
