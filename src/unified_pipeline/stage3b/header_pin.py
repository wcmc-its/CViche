"""Header pin for stage 3b: an author-placed, confidently mapped section wins (#312).

Stage 3a maps a CV's own section header to a taxonomy code, often with
confidence 1.0 (`INVITED PRESENTATIONS` -> R, `INSTITUTIONAL LEADERSHIP
ACTIVITIES` -> O). The stage 3b prompt hands that to the model as a "weak hint",
and on a topically homogeneous CV the model answers K4 at 0.90-0.95 for a talk
the author filed under Invitations, so the section renders empty and the rows
land in teaching. This module inverts the precedence for one narrow, measured
set of confusions: `HEADER_PIN_OVERRIDES` names, per pinned code, the codes a
model answer may NOT override. Everything else -- S1 under the wrong heading,
an S8 with an author list under an invited-presentations heading -- stays the
model's call, which is what the "content overrides hierarchy" rule exists for.

Batch EBYSBC (class E11) added three narrow extensions: an author-less S8 loses
to an R pin; a Q2 grant review loses to a Q3 sub-heading even when its parent
disagrees (`leaf_header_code`); and `content_pin_code`, a handful of shapes
whose code does not depend on the heading at all. `is_note_not_record` names
the T lines stage 3b's T-validation must not turn into records.

Pure functions over one hierarchy group's classified entries and its
`TaxonomyContext`; no LLM, no I/O. Imports only `.context`.
"""

import logging
import re
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

# --- EBYSBC E11 (#312): the pins the agreeing-levels rule above cannot reach ---
#
# S8 under an R pin. A talk the author filed under Invited Lectures with no author
# list in front of it is the owner's invited talk (MIFYLG, KYOPUV). An author list
# or a contributed-work marker keeps the model's S8: that shape is an abstract.
# So does a confident S8: the pin breaks the model's tie, it does not overrule a
# firm answer (the 1990s research-conference sub-lists filed under talks score 0.85+).
_S8_PIN_MAX_MODEL_CONFIDENCE = 0.80
_AUTHOR_LIST = re.compile(
    r"\b[A-Z][A-Za-z'\u2019-]+,?\s+(?:[A-Z]\.\s?){1,3}(?=[,;.\s*]|$)"  # Doe, J.A. / Doe J.
    r"|\b[A-Z][A-Za-z'\u2019-]+\s+[A-Z]{1,3},"                          # Doe JA,
    r"|\bet\s+al\b"
)
# "Washington, D.C." has the shape of "Doe, D.C."; it names a place, not an author.
_PLACE_INITIALS = re.compile(r"\bD\.\s?C\.")
_CONTRIBUTED_WORK = re.compile(
    r"\b(?:poster|abstracts?|contributed|finalist|oral\s+presentation|platform\s+presentation"
    r"|research\s+forum|residents?\s+conference)\b",
    re.I,
)

# Q2 under a heading 3a maps to Q3. Only the most specific level has to say Q3:
# a grant-reviewer sub-heading under a general service heading maps the parent Q2/Q4 at 0.5 (HFAJCC,
# GJXIWD). The row must read as a grant review, from its heading or its own words,
# and must not name a committee, board, advisory group or session, which stay the model's Q2.
_GRANT_REVIEW_PIN = "Q3"
_GRANT_REVIEW_OVERRIDABLE = frozenset({"Q2"})
_GRANT_HEADING = re.compile(r"\bgrants?\b|\bfunding\b", re.I)
_GRANT_REVIEW_TEXT = re.compile(r"\breview(?:er|ers|ed|s|ing)?\b|\bgrants?\b|\bstudy\s+sections?\b", re.I)
_NOT_A_GRANT_REVIEW = re.compile(
    r"(?<!review\s)\bcommittees?\b|\badvisory\b|\bworking\s+group\b|\bco-?chair\b"
    r"|\bsession\b|\bboard\b|\babstracts?\b",
    re.I,
)

# Content pins: shapes whose code does not depend on the heading they sit under.
# Only the owner-attended forms: "attending" is a physician title and "attendees" or
# a bare "attendance" counts the audience of a course the owner taught.
_ATTENDED = re.compile(r"\b(?:attended|attendee|attendance\s+at)\b", re.I)
_TEACHING_ROLE = re.compile(
    r"\b(?:director|faculty|speaker|instructor|lecturer|moderator|organi[sz]er|presenter|taught|chair)\b",
    re.I,
)
# A training title at the head of the row, after an optional date range:
# "2031-2032 Intern, Example Medicine", "Chief Resident, ...", "Postdoctoral Fellow in ...".
# "Faculty Fellow", "Senior Staff Fellow", "Teaching Fellow", "Fellow of ...", a bare
# "Research Fellow, <body>" ("Research Fellow in <field>" is training) and "Resident
# Director" do not match, nor does a "Senior ... Fellow" affiliate title
# (`_SENIOR_FELLOW`) or an honorific "Fellow, <college/academy/society>"
# (`_HONORIFIC_FELLOW`): those are positions or honours, not training.
_TRAINEE_TITLE = re.compile(
    r"^[\s\d/\-\u2013\u2014,.]*(?:present\s*)?[,\s]*"
    r"(?:(?:assistant|junior|senior|chief)\s+(?:and\s+(?:senior|chief)\s+)?)?"
    r"(?:(?:post-?doctoral|clinical)\s+(?:research\s+)?|research\s+(?=fellow\s+in\b))?"
    r"(?:intern|resident(?!\s+director\b)|fellow)\b(?!\s+of\b)",
    re.I,
)
_SENIOR_FELLOW = re.compile(r"\bsenior\s+(?:\w+\s+)?fellow\b", re.I)
_HONORIFIC_FELLOW = re.compile(
    r"^[\s\d/\-\u2013\u2014,.]*(?:present\s*)?[,\s]*fellow\b,?\s+(?:the\s+)?"
    r"(?:[\w&'\u2019-]+\s+){0,4}(?:college|academy|society)\b",
    re.I,
)
_POSITION_CODES = frozenset({"D1", "D2", "D3"})
_LIFE_SUPPORT = re.compile(
    r"\b(?:BCLS|BLS|ACLS|PALS|NRP|ATLS|basic\s+life\s+support"
    r"|advanced\s+(?:cardiac|cardiovascular|trauma|pediatric)\s+life\s+support)\b",
    re.I,
)
# A teaching certificate or licence, not any row that says "teaching" ("teaching hospital").
_TEACHING_CERTIFICATE = re.compile(
    r"\bteach(?:er|ers|ing)?\s+(?:certif|licen[cs]|credential)"
    r"|\b(?:certif\w*|licen[cs]\w*|credential\w*)\s+(?:of\s+|as\s+(?:an?\s+)?|in\s+)?teach(?:er|ers|ing)?\b",
    re.I,
)
# A note that points at a numbered publication listed elsewhere ("see publication
# #88", "(publication #90)") is not a second copy of that citation.
_PUBLICATION_POINTER = re.compile(r"\bpublications?\s*#\s*\d+", re.I)
_PUBLICATION_CODE = re.compile(r"^S\d$")

# A line that only points elsewhere ("<label> - see section N") or is only a
# URL is a note, not a record: a T it was given must survive T-validation.
_POINTER = re.compile(r"\(?\bsee\s+(?:sections?|items?|pages?|above|below|publications?)\b[^)]*\)?", re.I)
_POINTER_LINE_MAX_WORDS = 4
_BARE_URL = re.compile(r"^(?:https?://|www\.)\S+$", re.I)


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


def leaf_header_code(context: TaxonomyContext) -> str | None:
    """The top code of the most specific mapped level, when it reaches the floor, else None.

    Unlike `pinned_header_code` the parent levels need not agree: a grant-reviewer
    sub-heading under a general service heading mapped Q2/Q4 still says Q3.
    Used only for the grant-review pin, whose override set is one code wide.
    """
    for level in (context.subsection, context.section, context.meta_section):
        if level and level.get("taxonomy_options"):
            top = max(level["taxonomy_options"], key=lambda o: o["confidence"])
            return top["code"] if top["confidence"] >= HEADER_PIN_MIN_CONFIDENCE else None
    return None


def _has_author_list(text: str) -> bool:
    return bool(_AUTHOR_LIST.search(_PLACE_INITIALS.sub("", text)))


def _safe_confidence(entry: dict) -> float:
    """The model's confidence as a float; anything unreadable counts as certain (no pin)."""
    try:
        return float(entry.get("taxonomy_confidence"))
    except (TypeError, ValueError):
        return 1.0


def _r_pin_takes(entry: dict) -> bool:
    """Whether an R pin recodes this model answer: the measured set, or an unsure, author-less S8."""
    code, text = entry.get("taxonomy_code"), entry.get("text") or ""
    if code in HEADER_PIN_OVERRIDES["R"]:
        return True
    return (
        code == "S8"
        and _safe_confidence(entry) <= _S8_PIN_MAX_MODEL_CONFIDENCE
        and not _has_author_list(text)
        and not _CONTRIBUTED_WORK.search(text)
    )


def _is_grant_review(entry: dict) -> bool:
    text = entry.get("text") or ""
    if _NOT_A_GRANT_REVIEW.search(text):
        return False
    heading = " ".join(str(h) for h in entry.get("hierarchy") or [])
    return bool(_GRANT_HEADING.search(heading) or _GRANT_REVIEW_TEXT.search(text))


def header_pin_code(entry: dict, pin: str | None, leaf: str | None) -> str | None:
    """The code the group's header pins this model answer to, or None to keep it."""
    code = entry.get("taxonomy_code")
    if pin == "R" and _r_pin_takes(entry):
        return "R"
    if pin and pin != "R" and code in HEADER_PIN_OVERRIDES.get(pin, ()):
        return pin
    if leaf == _GRANT_REVIEW_PIN and code in _GRANT_REVIEW_OVERRIDABLE and _is_grant_review(entry):
        return _GRANT_REVIEW_PIN
    return None


def _is_trainee_title(text: str) -> bool:
    """An intern, resident or fellow title at the head of the row, not an affiliate or honorific one."""
    trainee = _TRAINEE_TITLE.match(text)
    return bool(trainee) and not _SENIOR_FELLOW.search(trainee.group(0)) and not _HONORIFIC_FELLOW.match(text)


def content_pin_code(entry: dict) -> tuple[str, str] | None:
    """(code, why) an entry's own shape pins it to whatever its heading, or None.

    Attended courses are B2, never K4, unless the row names a teaching role;
    a training title at the head of a position row is C (postdoctoral training);
    a life-support or teaching certificate is B2, not a medical licence (F1);
    a publication code on a note that points at a numbered publication is T.
    """
    code, text = entry.get("taxonomy_code"), entry.get("text") or ""
    heading = " ".join(str(h) for h in entry.get("hierarchy") or [])
    if code == "K4" and (_ATTENDED.search(heading) or _ATTENDED.search(text)) and not _TEACHING_ROLE.search(text):
        return "B2", "A course the owner attended is education received"
    if code in _POSITION_CODES and _is_trainee_title(text):
        return "C", "An intern, resident or fellow title is postdoctoral training"
    if code == "F1" and (_LIFE_SUPPORT.search(text) or _TEACHING_CERTIFICATE.search(text)):
        return "B2", "A life-support or teaching certificate is not a medical licence"
    if code and _PUBLICATION_CODE.match(code) and _PUBLICATION_POINTER.search(text):
        return "T", "A note pointing at a numbered publication is not a second citation"
    return None


def is_note_not_record(text: str, opening_line: str | None = None) -> bool:
    """A T line T-validation must leave T: a bare URL, a pointer-only line, or the running header.

    `opening_line` is the CV's first line (its owner's name, typically); a later
    line identical to it is the page header repeated, not a record (CTWLTR).
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _BARE_URL.match(stripped):
        return True
    if opening_line and stripped == opening_line.strip():
        return True
    if not _POINTER.search(stripped):
        return False
    return len(re.findall(r"\w+", _POINTER.sub("", stripped))) <= _POINTER_LINE_MAX_WORDS


_HEADER_PIN_WHY = "Kept in the section where the CV author listed it"


def _recode(entry: dict, code: str, why: str) -> dict:
    """A copy of `entry` recoded to `code`, keeping the model's answer in pre_pin_*.

    The reasoning is rewritten because a later reasoning-vs-code pass would
    otherwise read "professional education (K4)" and flip the code straight back.
    """
    return {
        **entry,
        "pre_pin_code": entry["taxonomy_code"],
        "pre_pin_reasoning": entry.get("classification_reasoning"),
        "taxonomy_code": code,
        "classification_reasoning": (
            f"{why} (category {code}); "
            f"reading the entry alone had suggested {entry['taxonomy_code']}."
        ),
    }


def apply_header_pin(entries: Iterable[dict], context: TaxonomyContext) -> tuple[list[dict], int]:
    """Recode model answers that lose to the group's pinned header code or to a content pin.

    Returns (entries, number recoded). A recoded entry keeps the model's answer
    in `pre_pin_code` / `pre_pin_reasoning`; `classification_source` stays
    "llm" (stage 3b's zero-classification gate counts it). The header pin is
    checked first; a content pin applies only where the header pin does not.
    """
    pin, leaf = pinned_header_code(context), leaf_header_code(context)
    out, recoded = [], 0
    for entry in entries:
        if entry.get("classification_source") == _MODEL_SOURCE:
            header_code = header_pin_code(entry, pin, leaf)
            pinned = (header_code, _HEADER_PIN_WHY) if header_code else content_pin_code(entry)
            if pinned and pinned[0] != entry.get("taxonomy_code"):
                recoded += 1
                entry = _recode(entry, *pinned)
        out.append(entry)
    if recoded:
        logger.info("Stage 3b header pin: %d entr%s recoded", recoded, "y" if recoded == 1 else "ies")
    return out, recoded
