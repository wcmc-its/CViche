"""Header pin for stage 3b: an author-placed, confidently mapped section wins (#312).

Stage 3a maps a CV's own section header to a taxonomy code, often with
confidence 1.0 (`INVITED PRESENTATIONS` -> R, `INSTITUTIONAL LEADERSHIP
ACTIVITIES` -> O). The stage 3b prompt hands that to the model as a "weak hint",
and on a topically homogeneous CV the model answers K4 at 0.90-0.95 for a talk
the author filed under Invitations, so the section renders empty and the rows
land in teaching. This module inverts the precedence for one narrow, measured
set of confusions: `HEADER_PIN_OVERRIDES` names, per pinned code, the codes a
model answer may NOT override. Everything else -- S1 under the wrong heading,
S8 under an invited-presentations heading -- stays the model's call, which is
what the "content overrides hierarchy" rule exists for.

Pure functions over one hierarchy group's classified entries and its
`TaxonomyContext`; no LLM, no I/O. Imports only `.context`.
"""

import logging
from collections.abc import Iterable

from .context import TaxonomyContext

logger = logging.getLogger(__name__)

# A stage 3a mapping this confident is the CV author's own placement, not a guess.
HEADER_PIN_MIN_CONFIDENCE = 0.95

# Section titles that name a scope, not a section. Stage 3a maps a bare one to R
# whatever it sits under (a teaching CV's "Local" -> R), so one of these alone
# carries no authority; it pins only next to a level that names a real section.
GEOGRAPHIC_SCOPE_TITLES = frozenset({"local", "regional", "national", "international"})

# pinned code -> the model answers that lose to it. Each pair is a confusion
# measured on real CVs (#312 C0ZGFW; #312 comment 2): teaching or an honor read
# off an invited talk's content, a course-director title read off a leadership row.
HEADER_PIN_OVERRIDES: dict[str, frozenset[str]] = {
    "R": frozenset({"K1", "K2", "K4", "H"}),
    "O": frozenset({"K3", "K4"}),
}

# classification_source of a real model answer; a fallback or empty entry is not one.
_MODEL_SOURCE = "llm"


def pinned_header_code(context: TaxonomyContext) -> str | None:
    """The code the CV author's own section placement pins, or None.

    Every level of the entry's path that stage 3a mapped must agree on one top
    code, the most specific level must reach HEADER_PIN_MIN_CONFIDENCE, and at
    least one agreeing level must be a real section title, not a bare scope word.
    """
    levels = [
        level for level in (context.subsection, context.section, context.meta_section)
        if level and level.get("taxonomy_options")
    ]
    if not levels:
        return None
    tops = [max(level["taxonomy_options"], key=lambda o: o["confidence"]) for level in levels]
    code = tops[0]["code"]
    if tops[0]["confidence"] < HEADER_PIN_MIN_CONFIDENCE:
        return None
    if any(top["code"] != code for top in tops):
        return None
    if all(str(level.get("title", "")).strip().lower() in GEOGRAPHIC_SCOPE_TITLES for level in levels):
        return None
    return code


def apply_header_pin(entries: Iterable[dict], context: TaxonomyContext) -> tuple[list[dict], int]:
    """Recode model answers that lose to the group's pinned header code.

    Returns (entries, number recoded). A recoded entry keeps the model's answer
    in `pre_pin_code` / `pre_pin_reasoning`; `classification_source` stays
    "llm" (stage 3b's zero-classification gate counts it). The reasoning is
    rewritten because a later reasoning-vs-code pass would otherwise read
    "professional education (K4)" and flip the code straight back.
    """
    entries = list(entries)
    pin = pinned_header_code(context)
    overridable = HEADER_PIN_OVERRIDES.get(pin) if pin else None
    if not overridable:
        return entries, 0
    out, recoded = [], 0
    for entry in entries:
        if entry.get("classification_source") == _MODEL_SOURCE and entry.get("taxonomy_code") in overridable:
            recoded += 1
            entry = {
                **entry,
                "pre_pin_code": entry["taxonomy_code"],
                "pre_pin_reasoning": entry.get("classification_reasoning"),
                "taxonomy_code": pin,
                "classification_reasoning": (
                    f"Kept in the section where the CV author listed it (category {pin}); "
                    f"reading the entry alone had suggested {entry['taxonomy_code']}."
                ),
            }
        out.append(entry)
    if recoded:
        logger.info("Stage 3b header pin: %d entr%s recoded to %s", recoded, "y" if recoded == 1 else "ies", pin)
    return out, recoded
