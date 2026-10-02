"""Was the uploaded CV written in the WCM faculty CV template, or in some other format?

Recorded on every run (``runs.input_format``) so quality scores can be compared
between template-format CVs and everything else. This is the opposite question
to ``template_warning.detect_wcm_template``, which flags a *blank* template: a
filled-in template CV scores near zero there, and is the case this module exists
to find.

Signal: how many of the template's own scaffolding lines survive in the text. A
line counts when it is one of the template's distinctive section headings, or one
of its instruction sentences that the author left in. Headings repeat across a
CV's tables, so each heading counts once; instruction lines count each time.
Generic headings that any CV has ("Education", "Research") are excluded, they
say nothing about the template.

The threshold comes from a calibration run over 1,166 real source CVs: every
non-template CV scored 2 or fewer, every template-format CV scored 14 or more,
and nothing fell in between (see the PR description for the histogram).
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

INPUT_FORMAT_WCM = "wcm"
INPUT_FORMAT_OTHER = "other"

# Signals at or above which the CV is called a WCM-template CV. Calibrated on 1,166
# source CVs: highest score on a non-template CV 2, lowest on a template CV 14.
WCM_MIN_SIGNALS = 6

# Template headings that ordinary CVs also use, so they prove nothing.
_GENERIC_HEADINGS = frozenset({
    "education", "research", "bibliography", "mentoring", "honors and awards",
    "research support", "professional memberships", "general information",
    "educational background", "invited presentations", "personal data",
    "professional positions and employment", "institutional responsibilities",
    "percent effort",
})


def _ensure_pipeline_on_path() -> None:
    """Put the repo's ``src/`` on sys.path so ``unified_pipeline`` imports (same
    reason and layout as template_warning._ensure_pipeline_on_path)."""
    src_dir = str(Path(__file__).parent.parent.parent.parent.parent / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def count_template_signals(extracted_text: str) -> int:
    """Distinct distinctive WCM headings plus left-in template instruction lines."""
    _ensure_pipeline_on_path()
    from unified_pipeline.core.template_boilerplate import (
        is_template_instruction, normalize_template_text, template_section_headers,
    )

    headings = template_section_headers() - _GENERIC_HEADINGS
    headings_seen: set[str] = set()
    instruction_lines = 0
    for line in extracted_text.splitlines():
        if not line.strip():
            continue
        key = normalize_template_text(line)
        if key in headings:
            headings_seen.add(key)
        elif is_template_instruction(line):
            instruction_lines += 1
    return len(headings_seen) + instruction_lines


def detect_input_format(extracted_text: str | None) -> tuple[str | None, int | None]:
    """``(format, signal_count)``: format is "wcm" or "other"; ``(None, None)``
    when there is no text to judge. Raises if the pipeline package cannot be
    imported: the caller decides how a failure is recorded."""
    if not extracted_text or not extracted_text.strip():
        return None, None
    signals = count_template_signals(extracted_text)
    fmt = INPUT_FORMAT_WCM if signals >= WCM_MIN_SIGNALS else INPUT_FORMAT_OTHER
    return fmt, signals


def detect_input_format_or_none(extracted_text: str | None) -> tuple[str | None, int | None]:
    """``detect_input_format`` that never raises: a failure is logged and recorded
    as "undetermined" (``(None, None)``), so the heuristic cannot block an upload."""
    try:
        return detect_input_format(extracted_text)
    except Exception:
        logger.warning("Input-format detection failed; leaving it undetermined", exc_info=True)
        return None, None
