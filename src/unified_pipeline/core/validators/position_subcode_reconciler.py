"""
Position Subcode Reconciler

Post-classification validator that reconciles a *stray* position subcode
(D1 Academic / D2 Hospital / D3 Other Professional) back to the subcode of the
appointment group it physically belongs to.

Problem (issue #161): Stage 3b classifies each entry independently. When field
extraction splits one appointment into separate entries -- an employer/date
header on one line and the bare role title on another -- the bare title loses
its clinical context, so the LLM defaults an ambiguous title to a different
position subcode than the rest of its group.

Real example (run I5NKUG, Hospital Appointments):

    D2  New York Presbyterian Hospital ... (September 2004-February 2013)
    D3  Nurse Practitioner            <-- stray: lost its hospital context
    D2  Greenberg 14 South-Medical/Surgical Inpatient Unit (04/2010-02/2013)

"Nurse Practitioner" alone is genuinely ambiguous between a hospital
appointment (D2) and a generic professional position (D3); read in isolation
the model picked D3, scattering it into the wrong output table.

Rule (deterministic, no API calls): within the document-ordered run of
position-family entries, a *context-poor* fragment (a short role title with no
date and no institution of its own) whose nearest position-family neighbours on
BOTH sides carry the SAME subcode is reconciled to that subcode. The fragment
is then reassembled into a complete appointment row by the Stage 6 grouped-
appointment merge (#156 / PR #160).

This is intentionally conservative -- it only moves a fragment WITHIN the
position family, only when it is sandwiched between two entries that agree, and
only when the fragment carries no self-sufficient context of its own. A
self-contained appointment (with its own dates or institution) and a fragment
at a genuine subsection boundary (neighbours disagree, or it sits at the edge)
are left untouched.
"""

import re

POSITION_CODES = {"D1", "D2", "D3"}

# A four-digit year, or an explicit ongoing marker -> the entry carries its own
# date context and is therefore NOT a context-poor fragment.
_DATE_SIGNAL = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:present|current|ongoing|to date)\b", re.IGNORECASE
)

# Institution / place-of-work signal -> the entry carries its own employer
# context and is therefore NOT a context-poor fragment.
_INSTITUTION_SIGNAL = re.compile(
    r"\b(?:Hospital|University|College|Institut|Center|Centre|Clinic|School|"
    r"Department|Medical|Laborator|Foundation|Incorporated|Inc\.?|Corporation|"
    r"Company|Unit|Division|Program|Society|Association|Network|Health|"
    r"System|Pharmaceutical|Ltd\.?|LLC)\b",
    re.IGNORECASE,
)

# A bare role title is short; a longer line is more likely real prose we should
# not touch. The stray fragments we target are role titles (e.g. "Nurse
# Practitioner", "Staff Nurse", "Attending Physician").
_MAX_FRAGMENT_WORDS = 8


def _idx(entry: dict) -> int:
    """Document-order key for an entry (falls back gracefully)."""
    for key in ("element_idx_start", "element_idx_end"):
        val = entry.get(key)
        if isinstance(val, int):
            return val
    return 0


def is_context_poor_position(entry: dict) -> bool:
    """A position entry that is just a role title -- no date, no institution.

    These are the fragments that lose their clinical context and get
    misclassified in isolation. Detection is text-based because at Stage 3b the
    entry has no extracted_fields yet (those are produced by Stage 4).
    """
    if entry.get("taxonomy_code") not in POSITION_CODES:
        return False
    text = (entry.get("text") or "").strip()
    if not text:
        return False
    if len(text.split()) > _MAX_FRAGMENT_WORDS:
        return False
    if _DATE_SIGNAL.search(text):
        return False
    if _INSTITUTION_SIGNAL.search(text):
        return False
    return True


def apply_position_subcode_reconciliation(
    entries: list[dict],
) -> tuple[list[dict], dict]:
    """Reconcile stray position subcodes for context-poor title fragments.

    Args:
        entries: classified entry dicts (any order; mutated in place).

    Returns:
        (entries, stats) where stats reports how many fragments were reviewed
        and reconciled, with per-correction detail -- matching the other
        post-classification correctors' shape.
    """
    stats: dict = {
        "position_fragments_reviewed": 0,
        "corrections_applied": 0,
        "correction_details": [],
    }

    # Document-ordered subsequence of position-family entries. These are
    # references to the same dicts in ``entries``, so mutating them here mutates
    # the returned list too; original list order is preserved.
    pos = sorted(
        (e for e in entries if e.get("taxonomy_code") in POSITION_CODES),
        key=_idx,
    )

    for i, entry in enumerate(pos):
        if not is_context_poor_position(entry):
            continue
        stats["position_fragments_reviewed"] += 1

        left: dict | None = pos[i - 1] if i > 0 else None
        right: dict | None = pos[i + 1] if i < len(pos) - 1 else None
        if not (left and right):
            continue  # edge of the run -> not enough evidence

        code = entry.get("taxonomy_code")
        left_code = left.get("taxonomy_code")
        right_code = right.get("taxonomy_code")

        # Only reconcile when the two neighbours AGREE on a subcode that differs
        # from the fragment's -- i.e. the fragment is sandwiched inside a single
        # appointment group of another subcode.
        if not (left_code == right_code and left_code in POSITION_CODES):
            continue
        if left_code == code:
            continue

        reason = (
            f"Context-poor position fragment '{(entry.get('text') or '').strip()[:60]}' "
            f"sits between {left_code} appointments (idx {_idx(left)} and {_idx(right)}); "
            f"reconciled {code} -> {left_code} to match its surrounding appointment group."
        )
        entry["original_taxonomy_code"] = code
        entry["taxonomy_code"] = left_code
        correction = {"from": code, "to": left_code, "reason": reason}
        entry["position_subcode_reconciliation"] = correction

        stats["corrections_applied"] += 1
        stats["correction_details"].append(
            {
                "element_idx": _idx(entry),
                "text_preview": (entry.get("text") or "")[:100],
                "correction": correction,
            }
        )

    return entries, stats
