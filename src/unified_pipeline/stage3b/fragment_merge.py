"""Fold each tagged fragment's text into the entry it belongs to (#1256).

`classify.reconnect_fragments` asks the model whether a short T line belongs
with the entry before it or after it, and tags it `is_fragment` with
`fragment_of`. Stage 4 skips every fragment, so until this module the line's
text reached neither its parent nor the output: JNATFN lost the last word of a
lecture title, BFSUMA a patent's assignee, MQJAVH the four dated sessions of an
examiner row.

`merge_fragment_text` appends a `previous` fragment's text to its parent and
prepends a `next` fragment's text, so stage 4 extracts from the whole record.
The fragment keeps `is_fragment`, so stage 4 still skips it and the text is
extracted once. Two kinds of fragment are left alone, and say why in
`fragment_merge_skipped`:

- a line the model's own reasoning calls a header, column label or page marker
  ("2007", "Institution/Entity Title Inclusive Dates", "7/16/18"): it is not
  part of either neighbour's record.
- a fragment whose parent also takes a mergeable fragment from its other side. The model
  has read the list's pattern both ways, so one of the two is wrong: WWSEWY's
  posters each follow their own date line, and 543 was attached backwards to
  the poster 539 already introduces.
- a fragment opening with a field label ("Current position:") its parent
  already carries: it is the same field of a sibling record (RBHRFR 468).
- a fragment opening with a list number: it is an item of its own (RWBQKF 94).
- a bare organisation name ("Purdue University") next to a coded record. On the
  corpus these are sub-headings over the records that follow them (TXTATQ,
  VQFSDI, ZGLAAD); the model picks one neighbour, and is wrong as often as
  right (TXTATQ 783 joined the course before it). #985's context stamp is the
  home for those. Into a T parent the merge only reaches the Appendix verbatim,
  so it is safe there (BFSUMA's assignee).

Runs once, at the end of stage 3b, after every pass that reads entry text, so
replaying it over a stored `_classified.json` gives exactly what the stage
would have written. Pure: no LLM, no I/O.
"""

import re
from collections import Counter
from typing import Any

from ..core.text_norm import squash

#: Code stage 3b gives an unclassified line, which stage 6 sends to the Appendix verbatim.
UNCLASSIFIED_CODE = "T"

#: `reconnect_fragments`'s two attachment directions, as stored by position.
BELONGS_TO_PREVIOUS = "previous"
BELONGS_TO_NEXT = "next"

#: Flag set on a fragment whose text is now in its parent, merged or already there.
MERGED_FLAG = "fragment_merged"

#: Field naming why a fragment was not merged; one of the SKIP_* values.
SKIPPED_FIELD = "fragment_merge_skipped"
SKIP_LABEL = "header_or_label"
SKIP_ORGANIZATION = "organization_line"
SKIP_NO_PARENT = "no_parent"
SKIP_BOTH_SIDES = "parent_claimed_from_both_sides"
SKIP_REPEATED_FIELD = "repeated_field_label"
SKIP_LIST_ITEM = "numbered_list_item"

#: The fragment pass's reasoning naming the line a structural label. Corpus
#: (108 CVs, 42 fragments): every header, column label, year divider, drug-name
#: divider and page stamp the model attached says one of these words.
#: "header-like" is a hedge, not a verdict: BFSUMA's assignee line is
#: "a header-like component of the next entry".
_LABEL_REASONING_RE = re.compile(
    r"\b(?:header|heading|column|label|marker|stamp)s?\b(?!-like)", re.IGNORECASE
)

#: A fragment opening with a field label ("Current position:"). When its parent
#: already carries that label, the fragment is the same field of a sibling
#: record (RBHRFR 468, a second mentee's current position; X6's IEUPKK shape).
_LEADING_FIELD_LABEL_RE = re.compile(r"^\s*([A-Za-z][A-Za-z /&-]{0,39}):")

#: A fragment opening with a list number ("1. Collaboration with ...") is an
#: item of its own; gluing it to item 2 makes one record of two (RWBQKF 94).
_LIST_NUMBER_RE = re.compile(r"^\s*\d{1,3}[.)]\s")

#: Words that make a short, digit-free line an organisation name.
_ORGANIZATION_WORD_RE = re.compile(
    r"\b(?:University|College|Institute|Center|Centre|Hospital|School|Clinic|Foundation|Society)\b"
)

#: Longest organisation-name line, in words. Corpus: the longest is 5
#: ("Yale University / Hospital System" counts the slash).
MAX_ORGANIZATION_WORDS = 6

#: Parent text ending in one of these takes the appended fragment on a new line.
_SENTENCE_END = (".", "!", "?")


def _is_organization_line(text: str) -> bool:
    """A short digit-free line naming an institution ("UF College of Nursing")."""
    return (
        not any(ch.isdigit() for ch in text)
        and len(text.split()) <= MAX_ORGANIZATION_WORDS
        and bool(_ORGANIZATION_WORD_RE.search(text))
    )


def _repeats_field_label(fragment_text: str, parent_text: str) -> bool:
    """Whether the fragment opens with a "Label:" the parent already has."""
    match = _LEADING_FIELD_LABEL_RE.match(fragment_text)
    return (
        bool(match)
        and f"{match.group(1).strip().casefold()}:" in parent_text.casefold()
    )


def _direction(idx: int, parent_idx: int) -> str:
    return BELONGS_TO_PREVIOUS if parent_idx < idx else BELONGS_TO_NEXT


def resolve_parent(entries: list[dict[str, Any]], idx: int) -> int | None:
    """Index of the non-fragment entry fragment `idx` belongs to.

    Follows `fragment_of` through a chain of fragments in one direction
    (MQJAVH's four session lines each point at the one before). None when a
    link is out of range, turns back on itself, or changes direction
    (ZGLAAD 137 and 138 point at each other)."""
    seen = {idx}
    current = idx
    direction = None
    while entries[current].get("is_fragment"):
        target = entries[current].get("fragment_of")
        if (
            not isinstance(target, int)
            or isinstance(target, bool)
            or not 0 <= target < len(entries)
        ):
            return None
        step = _direction(current, target)
        if target in seen or direction not in (None, step):
            return None
        seen.add(target)
        direction, current = step, target
    return current


def _own_skip_reason(
    entries: list[dict[str, Any]], idx: int, parent_idx: int
) -> str | None:
    """Why fragment `idx` must not join `parent_idx`, judged on the two alone."""
    entry = entries[idx]
    text = str(entry.get("text") or "")
    if _LIST_NUMBER_RE.match(text):
        return SKIP_LIST_ITEM
    if _repeats_field_label(text, str(entries[parent_idx].get("text") or "")):
        return SKIP_REPEATED_FIELD
    if _LABEL_REASONING_RE.search(str(entry.get("fragment_reasoning") or "")):
        return SKIP_LABEL
    parent_code = entries[parent_idx].get("taxonomy_code")
    if parent_code != UNCLASSIFIED_CODE and _is_organization_line(text.strip()):
        return SKIP_ORGANIZATION
    return None


def _merge_sides(entries: list[dict[str, Any]], parent_idx: int) -> set[str]:
    """The directions fragments that pass `_own_skip_reason` join `parent_idx` from."""
    sides = set()
    for idx, entry in enumerate(entries):
        if (
            entry.get("is_fragment")
            and resolve_parent(entries, idx) == parent_idx
            and _own_skip_reason(entries, idx, parent_idx) is None
        ):
            sides.add(_direction(idx, parent_idx))
    return sides


def merge_skip_reason(entries: list[dict[str, Any]], idx: int) -> str | None:
    """Why fragment `idx` must not be merged; None when it should be."""
    parent_idx = resolve_parent(entries, idx)
    if parent_idx is None:
        return SKIP_NO_PARENT
    own = _own_skip_reason(entries, idx, parent_idx)
    if own is not None:
        return own
    if len(_merge_sides(entries, parent_idx)) > 1:
        return SKIP_BOTH_SIDES
    return None


def _joined(parent_text: str, fragment_text: str, direction: str) -> str:
    """`fragment_text` added to `parent_text` on the side it belongs.

    A new line after sentence punctuation, else one space."""
    first, second = (
        (parent_text, fragment_text)
        if direction == BELONGS_TO_PREVIOUS
        else (fragment_text, parent_text)
    )
    first = first.rstrip()
    separator = "\n" if first.endswith(_SENTENCE_END) else " "
    return f"{first}{separator}{second.strip()}"


def _merge_order(entries: list[dict[str, Any]]) -> list[int]:
    """Fragment indices in the order that keeps a chain in document order:
    `previous` fragments forward (each appended after the last), `next`
    fragments backward (each prepended before the one after it)."""
    forward, backward = [], []
    for idx, entry in enumerate(entries):
        if not entry.get("is_fragment"):
            continue
        target = entry.get("fragment_of")
        bucket = backward if isinstance(target, int) and target > idx else forward
        bucket.append(idx)
    return forward + backward[::-1]


def merge_fragment_text(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fold each mergeable fragment's text into its parent, in place.

    A fragment whose text its parent already holds is flagged without a
    second copy (JIJRSN 614's drug name), so a replay over this function's
    own output changes no text. Returns the entries and `{"fragments_merged": n,
    "fragments_already_in_parent": n, "fragments_not_merged": {reason: n}}`."""
    merged = already = 0
    skipped: Counter[str] = Counter()
    for idx in _merge_order(entries):
        entry = entries[idx]
        if fragment_text_in_parent(entries, idx):
            entry[MERGED_FLAG] = True
            already += 1
            continue
        reason = merge_skip_reason(entries, idx)
        if reason is not None:
            entry[SKIPPED_FIELD] = reason
            skipped[reason] += 1
            continue
        parent_idx = resolve_parent(entries, idx)
        parent = entries[parent_idx]
        parent["text"] = _joined(
            str(parent.get("text") or ""),
            str(entry.get("text") or ""),
            _direction(idx, parent_idx),
        )
        entry[MERGED_FLAG] = True
        merged += 1
    return entries, {
        "fragments_merged": merged,
        "fragments_already_in_parent": already,
        "fragments_not_merged": dict(sorted(skipped.items())),
    }


def fragment_text_in_parent(entries: list[dict[str, Any]], idx: int) -> bool:
    """Whether fragment `idx`'s text is inside its parent's text, ignoring
    whitespace and case: the doctor's definition of a reconnected fragment."""
    parent_idx = resolve_parent(entries, idx)
    if parent_idx is None:
        return False
    fragment = squash(entries[idx].get("text"))
    return bool(fragment) and fragment in squash(entries[parent_idx].get("text"))
