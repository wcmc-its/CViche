"""Cheap, no-LLM detection of blank/near-blank WCM template uploads.

Each pipeline run costs real money (~$2.71 of LLM calls), so a user who uploads
the *unfilled* WCM faculty CV template -- or one barely filled in -- is almost
always tire-kicking: the pipeline will faithfully process the template's own
instructional scaffold text and hand back a document whose formatting has, if
anything, regressed. This module flags that case at upload time so the UI can
warn and require an acknowledgement before spending a run.

Detection reuses the blank-template string set + matcher already shipped for the
post-classification ``template_scaffold`` validator (PR #85): we feed each line
of the extracted document text through ``apply_template_scaffold_corrections``
as a one-line pseudo-entry and read back how many lines matched the blank
template. A truly blank template matches almost every non-trivial line; a real
filled CV matches barely any (the author's genuine content never *exactly*
equals a template instruction). The fraction of matched lines is the score.

This is intentionally best-effort: any failure (missing pipeline on the path,
empty string set, unexpected error) returns "no warning" so an upload is never
blocked by the heuristic.
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Lines shorter than this are too generic to be evidence either way (single
# words like a name fragment, a stray bullet), so they are not counted toward
# the ratio's denominator. Matches the spirit of the validator's own length
# guards without coupling to its internals.
_MIN_LINE_LEN = 5

# Fraction of non-trivial lines that must match the blank template before we
# warn. A filled CV sits near zero; an unfilled template sits near one. 0.55 is
# deliberately high to avoid warning on a real CV that merely retained a few
# section headers from the template.
_WARN_RATIO = 0.55


def _ensure_pipeline_on_path() -> None:
    """Put the repo's ``src/`` on sys.path so ``unified_pipeline`` imports.

    The orchestrator already does this at startup, but the upload handler can be
    the first thing to touch the pipeline namespace (e.g. in tests, or before a
    run has ever been launched), so we make this self-contained. PARENT_DIR is
    the repo root: app/services/ -> app/ -> backend/ -> web_interface/ -> repo.
    """
    parent_dir = Path(__file__).parent.parent.parent.parent.parent
    src_dir = str(parent_dir / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def detect_wcm_template(extracted_text: str | None) -> tuple[bool, float | None]:
    """Decide whether ``extracted_text`` looks like a blank WCM template.

    Returns ``(warning, match_ratio)`` where ``warning`` is True when the
    fraction of non-trivial lines matching the blank-template string set is at
    or above the warn threshold, and ``match_ratio`` is that fraction (or
    ``None`` when it could not be computed). Best-effort: every failure path
    returns ``(False, None)`` so the upload is never blocked by this check.
    """
    if not extracted_text or not extracted_text.strip():
        return False, None

    try:
        _ensure_pipeline_on_path()
        from unified_pipeline.core.validators.template_scaffold import (
            apply_template_scaffold_corrections,
        )

        # Treat each non-trivial line as a one-line pseudo-entry and let the
        # validator's matcher (exact normalized match, or >=0.95 ratio for long
        # instruction sentences) decide which are scaffold. This reuses the
        # blank-template string set verbatim -- no strings duplicated here.
        lines = [
            ln.strip()
            for ln in extracted_text.splitlines()
            if len(ln.strip()) >= _MIN_LINE_LEN
        ]
        if not lines:
            return False, None

        entries = [{"text": ln, "taxonomy_code": "X"} for ln in lines]
        _, stats = apply_template_scaffold_corrections(entries)

        # An empty scaffold set can't tell us anything -> no warning.
        if stats.get("note") == "scaffold string set empty":
            return False, None

        checked = stats.get("entries_checked", 0)
        matched = stats.get("corrections_made", 0)
        if not checked:
            return False, None

        ratio = matched / checked
        return ratio >= _WARN_RATIO, round(ratio, 3)
    except Exception as e:  # pragma: no cover - defensive: never block an upload
        logger.warning("WCM template detection skipped (non-fatal): %s", e)
        return False, None
