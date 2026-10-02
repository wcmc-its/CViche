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

# --- detection signatures ---------------------------------------------------
_MENTEE_PERIOD = re.compile(r"mentoring\s*period", re.I)
_MENTEE_SUPPORT = re.compile(r"type\s*of\s*supervision|site\s*/\s*position", re.I)
_OPEN_PERIOD = re.compile(r"present|current|ongoing", re.I)
_CLOSED_RANGE = re.compile(r"\b(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}\b")

_BOARD = re.compile(
    r"american board of|full name of board|certificate\s*#|board eligible",
    re.I,
)
# A board-certification shape beyond the board's name (#1235): a certification
# word, or a board name plus a year outside an awards/honors/committee/
# membership context.
_BOARD_NAME = re.compile(r"american board of", re.I)
_BOARD_CERT_WORD = re.compile(
    r"board[\s-]*(?:certif|eligible)|\bdiplomat(?:e|s|es)?\b|re-?certif|\bcertifi(?:ed|cation)\b"
    r"|certificate\s*(?:#|no\b|number)",
    re.I,
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_NON_CERT_HIERARCHY = re.compile(
    r"award|honou?r|committee|member|societ|organi[sz]ation", re.I
)
_NON_CERT_TEXT = re.compile(r"\b(?:awards?|prizes?|committees?)\b", re.I)
_LICENSURE = re.compile(
    r"\b(dea|npi)\s*number\b|license\s*number|\blicensure\b|medical\s+license",
    re.I,
)

# Only override these known-wrong source codes (never touch a confident,
# already-plausible classification).
_MENTEE_FROM = {"K1", "K2", "K3", "K4", "K5", "T", "N4"}
_BOARD_FROM = {"I", "H", "C", "T"}
_LICENSE_FROM = {"I", "H", "C", "T", "A", "F2"}


def _is_board_certification(entry: dict) -> bool:
    """True when the entry has a board-certification shape, not just a board name."""
    text = entry.get("text", "") or ""
    if not _BOARD.search(text):
        return False
    # WCM table labels / "board eligible" / certificate # with data (any digit);
    # the empty table-header row has no digit and is not promoted.
    if not _BOARD_NAME.search(text):
        return bool(re.search(r"\d", text))
    if _NON_CERT_TEXT.search(text):
        return False
    if _BOARD_CERT_WORD.search(text):
        return True
    return _is_board_name_with_year(entry, text)


def _is_board_name_with_year(entry: dict, text: str) -> bool:
    """Board name plus a year, unless the section says award/committee/membership."""
    if not _YEAR.search(text):
        return False
    return not _NON_CERT_HIERARCHY.search(" ".join(entry.get("hierarchy") or []))


def _correct(entry: dict):
    """Return (new_code, reason) or (None, None) if no correction applies."""
    text = entry.get("text", "") or ""
    code = entry.get("taxonomy_code", "") or ""

    # Mentee table (most specific -- two required keywords).
    if _MENTEE_PERIOD.search(text) and _MENTEE_SUPPORT.search(text):
        if code not in ("N3A", "N3B"):
            is_past = bool(_CLOSED_RANGE.search(text)) and not _OPEN_PERIOD.search(text)
            return ("N3B" if is_past else "N3A"), "WCM mentee table (Mentoring Period + supervision)"

    # Board certification (check before licensure: both carry a number).
    # Require actual certification data, not the board's name alone (#1235), so
    # the empty WCM table-header row and an awards/committee/membership line
    # naming a board are NOT promoted to a content code.
    if code in _BOARD_FROM and _is_board_certification(entry):
        return "F2", "WCM board-certification signature"

    # Licensure.
    if _LICENSURE.search(text):
        if code in _LICENSE_FROM:
            return "F1", "WCM licensure signature (DEA/NPI/license number)"

    return None, None


def apply_wcm_table_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
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
