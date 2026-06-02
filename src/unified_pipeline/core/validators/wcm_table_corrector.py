"""
WCM structured-table corrector.

Deterministic, content-keyed post-classification correction for the WCM faculty
template's structured tables, which the LLM classifier systematically mis-routes
when the segmenter places a record under a misleading section header (the
classifier is instructed to defer to the section path on ambiguity).

Three corrections, each gated on an unambiguous content signature so it cannot
fire on unrelated entries:

  * Mentee table  -> N3A (current) / N3B (past)
        signature: "Mentoring Period" AND ("Type of Supervision" OR "Site/position")
        observed failure: mentee tables classified K2 (Clinical teaching).
  * Board certification -> F2
        signature: "American Board of ..." OR the WCM board table labels
        ("Full Name of Board", "Certificate #", "board eligible").
        observed failure: board certs classified I (Society Memberships).
  * Licensure -> F1
        signature: explicit "DEA number" / "NPI number" / "License number" /
        "Licensure" labels.

Runs POST-classification, like the other validators in this package, and only
overrides a small allow-list of known-wrong source codes so it never clobbers a
confident, correct classification. These signatures are the same structured
patterns stage_6 already recognizes when rendering the WCM template, so the
codes are made to agree with how the content is ultimately laid out.
"""

import re
from typing import List, Dict, Tuple

# --- detection signatures ---------------------------------------------------
_MENTEE_PERIOD = re.compile(r"mentoring\s*period", re.I)
_MENTEE_SUPPORT = re.compile(r"type\s*of\s*supervision|site\s*/\s*position", re.I)
_OPEN_PERIOD = re.compile(r"present|current|ongoing", re.I)
_CLOSED_RANGE = re.compile(r"\b(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}\b")

_BOARD = re.compile(
    r"american board of|full name of board|certificate\s*#|board eligible",
    re.I,
)
_LICENSURE = re.compile(
    r"\b(dea|npi)\s*number\b|license\s*number|\blicensure\b|medical\s+license",
    re.I,
)

# Only override these known-wrong source codes (never touch a confident,
# already-plausible classification).
_MENTEE_FROM = {"K1", "K2", "K3", "K4", "K5", "T", "N4"}
_BOARD_FROM = {"I", "H", "C", "T"}
_LICENSE_FROM = {"I", "H", "C", "T", "A", "F2"}


def _correct(entry: Dict):
    """Return (new_code, reason) or (None, None) if no correction applies."""
    text = entry.get("text", "") or ""
    code = entry.get("taxonomy_code", "") or ""

    # Mentee table (most specific -- two required keywords).
    if _MENTEE_PERIOD.search(text) and _MENTEE_SUPPORT.search(text):
        if code not in ("N3A", "N3B"):
            is_past = bool(_CLOSED_RANGE.search(text)) and not _OPEN_PERIOD.search(text)
            return ("N3B" if is_past else "N3A"), "WCM mentee table (Mentoring Period + supervision)"

    # Board certification (check before licensure: both carry a number).
    # Require actual data (an explicit "American Board of ..." or any digit, i.e.
    # a certificate number / date) so the empty WCM table-header row
    # ("Full Name of Board / Certificate #") is NOT promoted to a content code.
    if _BOARD.search(text) and (re.search(r"american board of", text, re.I) or re.search(r"\d", text)):
        if code in _BOARD_FROM:
            return "F2", "WCM board-certification signature"

    # Licensure.
    if _LICENSURE.search(text):
        if code in _LICENSE_FROM:
            return "F1", "WCM licensure signature (DEA/NPI/license number)"

    return None, None


def apply_wcm_table_corrections(entries: List[Dict]) -> Tuple[List[Dict], Dict]:
    """Apply WCM structured-table corrections in place. Returns (entries, stats)."""
    corrections_made = 0
    details = []
    for entry in entries:
        new_code, reason = _correct(entry)
        if new_code and new_code != entry.get("taxonomy_code"):
            original = entry.get("taxonomy_code")
            entry["taxonomy_code"] = new_code
            entry["taxonomy_confidence"] = 0.9
            entry["wcm_table_correction"] = {"original_code": original, "reason": reason}
            existing = entry.get("classification_reasoning", "")
            entry["classification_reasoning"] = f"[WCM-table corrected {original}->{new_code}] {existing}"
            corrections_made += 1
            details.append({
                "text_preview": (entry.get("text", "") or "")[:60],
                "original": original,
                "corrected_to": new_code,
                "reason": reason,
            })

    stats = {
        "entries_checked": len(entries),
        "corrections_made": corrections_made,
        "correction_details": details,
    }
    return entries, stats
