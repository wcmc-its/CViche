"""
WCM template-scaffold corrector.

CViche source CVs are very often the official WCM faculty template filled in by
the author, who leaves the template's instructional/scaffold text in place
(e.g. "Clinical teaching (bedside teaching, teaching rounds, ...)",
"Award Source: (funding agency ...)", "Title | Institution | Dates (yyyy)").
When the segmenter mis-routes these scaffold lines they get a real content code
(observed: K2/K3 teaching) and render into the CV as if the author had written
them -- the "prompt-echo" defect -- and they also inflate the T bucket and the
all-null field-extraction rate.

This validator recodes entries whose text matches the BLANK WCM template to T
(structural/other), so downstream stages drop them instead of treating them as
content. Matching is conservative -- exact normalized match, or a high
similarity ratio for longer instruction sentences only -- so it cannot recode a
candidate's genuine content (which never *exactly* equals a template
instruction). The match set is the distinct strings of the blank template,
extracted to wcm_template_scaffold_strings.json (reviewable, no runtime docx
dependency).

Runs POST-classification, like the other validators in this package.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Tuple, Set

_STRINGS_PATH = Path(__file__).with_name("wcm_template_scaffold_strings.json")
_FUZZY_MIN_LEN = 30      # only fuzzy-match longer instruction sentences
_FUZZY_RATIO = 0.95      # near-exact, to avoid clobbering real content

_norm_re = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _norm_re.sub(" ", (s or "").strip()).lower()


def _load_scaffold_strings() -> set[str]:
    try:
        return {_norm(s) for s in json.loads(_STRINGS_PATH.read_text())}
    except Exception:
        return set()


_SCAFFOLD: set[str] = _load_scaffold_strings()
_SCAFFOLD_LONG = [s for s in _SCAFFOLD if len(s) >= _FUZZY_MIN_LEN]


def _is_scaffold(text: str) -> bool:
    t = _norm(text)
    if not t or t not in _SCAFFOLD and len(t) < 5:
        return False
    if t in _SCAFFOLD:
        return True
    if len(t) >= _FUZZY_MIN_LEN:
        for s in _SCAFFOLD_LONG:
            # cheap length pre-filter before the O(n*m) ratio
            if abs(len(s) - len(t)) <= 6 and SequenceMatcher(None, t, s).ratio() >= _FUZZY_RATIO:
                return True
    return False


def apply_template_scaffold_corrections(entries: list[dict]) -> tuple[list[dict], dict]:
    """Recode blank-template scaffold entries to T. Returns (entries, stats)."""
    if not _SCAFFOLD:
        return entries, {"entries_checked": len(entries), "corrections_made": 0,
                         "correction_details": [], "note": "scaffold string set empty"}

    corrections_made = 0
    details = []
    for entry in entries:
        if entry.get("taxonomy_code") == "T":
            continue
        if _is_scaffold(entry.get("text", "")):
            original = entry.get("taxonomy_code")
            entry["taxonomy_code"] = "T"
            entry["taxonomy_confidence"] = 0.95
            entry["template_scaffold_correction"] = {"original_code": original}
            existing = entry.get("classification_reasoning", "")
            entry["classification_reasoning"] = f"[Template-scaffold -> T from {original}] {existing}"
            corrections_made += 1
            details.append({
                "text_preview": (entry.get("text", "") or "")[:60],
                "original": original,
            })

    stats = {
        "entries_checked": len(entries),
        "corrections_made": corrections_made,
        "correction_details": details,
    }
    return entries, stats
