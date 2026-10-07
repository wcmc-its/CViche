"""
Calibration Logger

Tracks LLM confidence vs actual accuracy to identify
calibration issues and improve prompts.
"""

import json
from datetime import datetime
from pathlib import Path


# Log directories
LOGS_DIR = Path(__file__).parent.parent.parent.parent / 'logs'
CLASSIFICATIONS_LOG = LOGS_DIR / 'classifications.jsonl'
CALIBRATION_ISSUES_LOG = LOGS_DIR / 'calibration_issues.jsonl'


def ensure_logs_dir():
    """Ensure logs directory exists."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def log_classification_event(result: dict):
    """
    Log every classification for later calibration analysis.

    Args:
        result: Classification result with:
            - entry_id: Entry identifier
            - section_id: Assigned section
            - confidence: LLM confidence
            - reasoning: LLM explanation
            - guidance_applied: Guidance that was provided
            - validation_check: Post-validation results
            - model: Model used
    """
    ensure_logs_dir()

    event = {
        'timestamp': datetime.now().isoformat(),
        'entry_id': result.get('entry_id'),
        'section_id': result.get('section_id'),
        'confidence': result.get('confidence'),
        'reasoning': result.get('reasoning', ''),
        'guidance_applied': result.get('guidance_applied', {}),
        'validation_check': result.get('validation_check', {}),
        'model': result.get('model', 'unknown'),
        'validation_override': result.get('validation_override', False),
        'original_section_id': result.get('original_section_id')
    }

    with open(CLASSIFICATIONS_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event) + '\n')


def log_calibration_issue(llm_result: dict, validation_check: dict):
    """
    Log when LLM has high confidence but violates clear rules.

    This indicates a calibration problem - the LLM is overconfident
    in an incorrect answer.

    Args:
        llm_result: LLM classification result
        validation_check: Validation check results showing violation
    """
    ensure_logs_dir()

    issue = {
        'timestamp': datetime.now().isoformat(),
        'llm_confidence': llm_result.get('confidence'),
        'llm_choice': llm_result.get('section_id'),
        'llm_reasoning': llm_result.get('reasoning', ''),
        'violated_rules': validation_check.get('violated_rules', []),
        'severity': validation_check.get('severity'),
        'entry_sample': llm_result.get('entry_text', '')[:300],
        'guidance_applied': llm_result.get('guidance_applied', {}),
        'model': llm_result.get('model', 'unknown')
    }

    with open(CALIBRATION_ISSUES_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(issue) + '\n')


def log_validation_event(
    event_type: str,
    llm_result: dict,
    guidance: dict,
    validation_check: dict
):
    """
    Log validation events for monitoring.

    Args:
        event_type: Type of event (violation, override, etc.)
        llm_result: LLM result
        guidance: Guidance provided
        validation_check: Validation check results
    """
    ensure_logs_dir()

    event = {
        'timestamp': datetime.now().isoformat(),
        'event_type': event_type,
        'llm_section_id': llm_result.get('section_id'),
        'llm_confidence': llm_result.get('confidence'),
        'guidance_excluded': guidance.get('excluded_subsections', []),
        'guidance_recommended': guidance.get('recommended_subsections', []),
        'guidance_confidence': guidance.get('confidence_in_guidance'),
        'violation_severity': validation_check.get('severity'),
        'recommended_action': validation_check.get('recommended_action')
    }

    validation_log = LOGS_DIR / 'validation_events.jsonl'
    with open(validation_log, 'a', encoding='utf-8') as f:
        f.write(json.dumps(event) + '\n')


def get_confidence_bucket(confidence: float) -> str:
    """
    Get confidence bucket for a confidence score.

    Args:
        confidence: Confidence score (0.0-1.0)

    Returns:
        Bucket string (e.g., '0.95-1.0')
    """
    if confidence >= 0.95:
        return '0.95-1.0'
    elif confidence >= 0.85:
        return '0.85-0.94'
    elif confidence >= 0.70:
        return '0.70-0.84'
    elif confidence >= 0.50:
        return '0.50-0.69'
    else:
        return '<0.50'
