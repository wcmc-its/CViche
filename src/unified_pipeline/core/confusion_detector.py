"""
Confusion Trigger Detection for Taxonomy Classification

Detects keywords and patterns that suggest confusion risk between sections.
Ported from extract_unextracted_fallback.py (lines 530-665) with enhancements.

Triggers are used to:
1. Escalate example display (show more examples for high confusion)
2. Include routing rules in prompts
3. Upgrade model selection (gpt-4o-mini → gpt-4o for high confusion)
"""

import re
from typing import List


def detect_confusion_triggers(
    entry_text: str,
    parent_section_id: str,
    section_header: str = ""
) -> List[str]:
    """
    Detect keywords that suggest confusion risk between sections.

    Args:
        entry_text: The entry content to analyze
        parent_section_id: The parent taxonomy classification (e.g., 'bibliography', 'mentoring')
        section_header: The original CV section label (e.g., 'FACULTY MENTEES')

    Returns:
        List of trigger types detected (e.g., ['abstract_indicator', 'preprint_indicator'])
    """
    triggers = []
    text_lower = entry_text.lower()
    header_lower = section_header.lower()

    # O vs P triggers (institutional_leadership vs institutional_administration)
    if parent_section_id in ['institutional_leadership', 'institutional_administration']:
        # Executive role indicators (O)
        exec_keywords = ['president', 'dean', 'director', 'chief', 'vice chair', 'associate dean']
        if any(word in text_lower for word in exec_keywords):
            triggers.append('executive_role')

        # Chair is O unless it's "Co-Chair" (shared)
        if 'chair' in text_lower and 'co-chair' not in text_lower and 'co chair' not in text_lower:
            # Check if it's "Chair," or "Chair " to avoid matching "chairman"
            if 'chair,' in text_lower or 'chair ' in text_lower or text_lower.endswith('chair'):
                triggers.append('executive_role')

        # Committee/member indicators (P)
        committee_keywords = ['member', 'representative', 'committee member', 'participant', 'elected member']
        if any(word in text_lower for word in committee_keywords):
            triggers.append('committee_role')

        if 'co-chair' in text_lower or 'co chair' in text_lower:
            triggers.append('committee_role')  # Shared leadership → P

    # Q1 vs I triggers (extramural leadership vs professional memberships)
    if parent_section_id in ['extramural_professional_activities', 'professional_orgs_societies']:
        # Simple membership indicators (I)
        if any(word in text_lower for word in ['member,', 'fellow,', 'affiliate']):
            triggers.append('membership_list')

        # Leadership role indicators (Q1)
        if any(word in text_lower for word in ['officer', 'chair', 'president', 'council', 'treasurer', 'board member']):
            triggers.append('leadership_role')

    # K vs B2 triggers (educational_contributions vs professional_development)
    if parent_section_id in ['educational_contributions', 'education_and_training']:
        # Teaching role indicators (K)
        if any(word in text_lower for word in ['instructor', 'taught', 'teaching', 'course', 'lecture', 'preceptor']):
            triggers.append('teaching_role')

        # Learner role indicators (B2)
        if any(word in text_lower for word in ['attended', 'participant', 'completed', 'training']):
            triggers.append('learner_role')

    # Bibliography confusion triggers (S1 vs S2 vs S6 vs S8 vs S10)
    if parent_section_id == 'bibliography':
        # Preprint indicators (S10)
        preprint_keywords = ['biorxiv', 'medrxiv', 'arxiv', 'preprint', 'osf preprint']
        if any(word in text_lower for word in preprint_keywords) or '10.1101/' in entry_text:
            triggers.append('preprint_indicator')

        # Conference abstract indicators (S8) - CRITICAL FOR ACCURACY
        # Pattern 1: Common abstract journals
        abstract_journals = ['faseb journal', 'circulation', 'jama', 'aha ', 'asco ', 'aacr ', 'rsna']
        if any(journal in text_lower for journal in abstract_journals):
            triggers.append('abstract_indicator')

        # Pattern 2: "Suppl" or "Supplement" in citation
        if 'suppl' in text_lower or 'supplement' in text_lower:
            triggers.append('abstract_indicator')

        # Pattern 3: Abstract number patterns (e.g., "30:1153" or "30(1 Suppl):33")
        # Matches volume:abstract or volume(issue):abstract patterns
        if re.search(r'\d+\(\d+\s*Suppl\)|:\d{3,4}\.?\d*(?:\s|$|\))', entry_text):
            triggers.append('abstract_indicator')

        # Pattern 4: Explicit abstract/poster mentions with meeting/conference
        if ('abstract' in text_lower or 'poster' in text_lower) and \
           any(word in text_lower for word in ['meeting', 'conference', 'symposium', 'congress', 'session']):
            triggers.append('abstract_indicator')

        # Case report indicators (S6)
        if any(word in text_lower for word in ['case report', 'case study', 'rare presentation', 'patient description']):
            triggers.append('case_report_indicator')

        # Review article indicators (S2)
        if any(word in text_lower for word in ['review', 'editorial', 'commentary', 'perspective', 'opinion']):
            triggers.append('review_indicator')

        # Unpublished manuscript indicators (S7)
        unpublished_keywords = ['submitted', 'under review', 'in preparation', 'in press', 'accepted', 'revise and resubmit']
        if any(word in text_lower for word in unpublished_keywords):
            triggers.append('unpublished_indicator')

        # Data paper/dataset indicators (S16/S12)
        data_keywords = ['scientific data', 'gigascience', 'data in brief', 'data service',
                        'zenodo', 'dryad', 'figshare', 'dataset', 'data repository']
        if any(word in text_lower for word in data_keywords):
            triggers.append('data_indicator')

        # Institutional report indicators (S5)
        report_terms = ['discussion paper', 'policy brief', 'working paper', 'white paper',
                       'technical report', 'briefing paper', 'research report', 'issue brief']
        if any(term in text_lower for term in report_terms):
            triggers.append('institutional_report_indicator')

        # Regulatory submission indicators (S21)
        if any(word in text_lower for word in ['clinicaltrials.gov', 'nct', 'ind', 'ide', 'irb']):
            triggers.append('regulatory_indicator')

        # Protocol/methods indicators (S13)
        if any(word in text_lower for word in ['star protocols', 'jove', 'nature protocols', 'protocol exchange', 'protocols.io']):
            triggers.append('protocol_indicator')

        # Guideline indicators (S14)
        if any(word in text_lower for word in ['guideline', 'consensus', 'recommendation', 'statement']) and \
           any(word in text_lower for word in ['acc/aha', 'who', 'cdc', 'uspstf', 'society', 'association', 'college']):
            triggers.append('guideline_indicator')

        # Mentoring misclassification detection
        mentoring_patterns = [
            'directed study', 'directed studies', 'independent study', 'independent studies',
            'thesis supervision', 'dissertation supervision', 'thesis committee', 'dissertation committee',
            'student advising', 'mentee', 'supervisee', 'research supervision'
        ]
        if any(pattern in text_lower for pattern in mentoring_patterns):
            triggers.append('mentoring_misclassified_as_bibliography')

    # CRITICAL: Mentee section header detection (group-level signal)
    # Detects when section header indicates this is about people CV OWNER mentored
    if parent_section_id in ['mentoring', 'professional_positions_employment', 'bibliography']:
        mentee_section_patterns = [
            'mentees', 'advisees', 'mentored', 'supervised',
            'students advised', 'students supervised', 'students mentored',
            'trainees supervised', 'trainees mentored',
            'postdoctoral fellows supervised', 'postdoctoral fellows mentored',
            'junior faculty mentored', 'faculty mentored',
            'dissertation students', 'thesis students',
            'research advisees', 'doctoral advisees'
        ]
        if any(pattern in header_lower for pattern in mentee_section_patterns):
            triggers.append('mentee_section_header')

    return triggers


def calculate_confusion_risk(triggers: List[str]) -> str:
    """
    Calculate overall confusion risk level based on detected triggers.

    Args:
        triggers: List of detected trigger types

    Returns:
        'low', 'medium', or 'high' risk level
    """
    if len(triggers) == 0:
        return 'low'
    elif len(triggers) == 1:
        return 'low'
    elif len(triggers) == 2:
        return 'medium'
    else:
        return 'high'


def should_show_full_examples(
    triggers: List[str],
    base_confusion_risk: str
) -> bool:
    """
    Determine if full examples should be shown based on triggers and base risk.

    Args:
        triggers: Detected confusion triggers
        base_confusion_risk: Base confusion risk from section context ('low', 'medium', 'high')

    Returns:
        True if full examples should be displayed
    """
    # Always show full examples for base high confusion
    if base_confusion_risk == 'high':
        return True

    # Show full examples if 2+ triggers detected
    if len(triggers) >= 2:
        return True

    # Show full examples for critical triggers
    critical_triggers = [
        'mentee_section_header',
        'abstract_indicator',
        'mentoring_misclassified_as_bibliography'
    ]
    if any(trigger in triggers for trigger in critical_triggers):
        return True

    return False


def should_upgrade_model(
    triggers: List[str],
    base_confusion_risk: str,
    classification_confidence: float
) -> bool:
    """
    Determine if model should be upgraded from gpt-4o-mini to gpt-4o.

    Args:
        triggers: Detected confusion triggers
        base_confusion_risk: Base confusion risk from section context
        classification_confidence: Confidence score from classification (0.0-1.0)

    Returns:
        True if model should be upgraded to gpt-4o
    """
    # Upgrade if high confusion risk and low confidence
    if base_confusion_risk == 'high' and classification_confidence < 0.85:
        return True

    # Upgrade if 3+ triggers detected
    if len(triggers) >= 3:
        return True

    # Upgrade for critical triggers + low confidence
    critical_triggers = [
        'mentee_section_header',
        'mentoring_misclassified_as_bibliography'
    ]
    if any(trigger in triggers for trigger in critical_triggers) and classification_confidence < 0.90:
        return True

    return False
