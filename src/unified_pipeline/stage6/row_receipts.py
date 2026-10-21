"""Per-render row receipts: which entries each section renderer wrote (#534).

Stage 6 hands every section renderer its entries and never asks what came
back. A renderer that drops an entry -- a skipped branch, a table cleared by a
second filler after the first had written to it (#454, run GNONHA lost 7 Q1
records) -- leaves the docx valid and the run silent: the entry is not in the
Appendix, no dedup decision names it, and the doctor had no instrument that
compares what a renderer was given with what it wrote (NECRGC autopsy on
#534: GNONHA Q1, CKDDEG H entry 90).

A receipt is that comparison, done with bookkeeping rather than by searching
the docx for text (the trap #534's body warns about: `w:ins` text and
reformatted citations defeat phrase matching). Each row or paragraph a
renderer writes is recorded against the entry it came from, and an entry
counts as written only when one of its recorded elements is still attached to
the document body when the render ends -- so a row written and later wiped
does not count.

Recording happens in two places. `_add_entry_comments` is the shared hook
that most renderers already call with the entry for the row they just wrote,
so it records with the entry in hand, as the presentations writer does
itself (`wrote` with its entry). Renderers that write rows without that
hook (honors, the Q service tables, leadership, administrative activities)
mark the entry they are on with `begin_entry`, and their row writers record
against it. Each such renderer calls `end_entry` when its loop ends, and the
generator also ends the cursor after every section, so a row written outside
an entry loop is never attributed to a stale entry.

The lint over these receipts lives in the doctor (`doctor/lints/render.py`,
`lint_renderer_row_receipts`); this module only produces them. No docx
import: an element is anything with `iterancestors()` and a `tag`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

# The WordprocessingML body element. An element whose ancestors include it is
# in the delivered document; a row removed by `_clear_table_data` has no
# parent at all.
BODY_TAG = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body"

# Sections whose receipts are complete enough to lint on (#534). A section is
# listed only once every path that writes one of its entries records a
# receipt, measured on the stored corpus (see the PR that added this); the
# others still get a receipt in the sidecar, flagged `linted: false`, so their
# numbers are visible without being acted on.
LINTED_SECTIONS = frozenset({
    "honors",
    "memberships",
    "mentoring",
    "service",
})

# How much of a lost entry's text the sidecar keeps -- enough to find the
# record in the source, short enough that the sidecar stays a summary.
LOST_TEXT_CHARS = 120


class _Element(Protocol):
    tag: str

    def iterancestors(self) -> Iterable[_Element]: ...


@dataclass(frozen=True)
class LostEntry:
    """One entry a renderer was given that is nowhere in the document."""

    code: str
    element_idx_start: ElementIndex | None
    text: str

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "element_idx_start": self.element_idx_start,
                "text": self.text}


@dataclass(frozen=True)
class SectionReceipt:
    """What one section was given and where its entries ended up: `written`
    in a section's own rows, `elsewhere` (the Appendix, a recovery bullet, a
    deliberate skip), or `lost` -- nowhere in the document."""

    section: str
    linted: bool
    given: int
    written: int
    elsewhere: int
    lost: tuple[LostEntry, ...]

    def to_json(self) -> dict[str, object]:
        return {"section": self.section, "linted": self.linted,
                "given": self.given, "written": self.written,
                "elsewhere": self.elsewhere,
                "lost": [entry.to_json() for entry in self.lost]}


def _in_body(element: _Element) -> bool:
    return any(ancestor.tag == BODY_TAG for ancestor in element.iterancestors())


#: The source position of an entry: an int, or for a table row the string
#: stage 2 writes ("96.3" is element 96, row 3; 1,446 of 31,449 corpus entries).
ElementIndex = int | str


def _element_idx(entry: Mapping[str, object]) -> ElementIndex | None:
    idx = entry.get("element_idx_start")
    if isinstance(idx, bool) or not isinstance(idx, int | str) or idx == "":
        return None
    return idx


def _lost_entry(entry: Mapping[str, object]) -> LostEntry:
    text = " ".join(str(entry.get("text") or "").split())
    return LostEntry(code=str(entry.get("taxonomy_code") or ""),
                     element_idx_start=_element_idx(entry),
                     text=text[:LOST_TEXT_CHARS])


class _EntrySet:
    """Entries matched by identity or by element_idx_start. The index lets a
    renderer's derived copy of an entry (a rerouted or split line) vouch for
    its source; the cost is that one fan-out sibling also vouches for a lost
    sibling, which errs toward silence."""

    def __init__(self, entries: Iterable[Mapping[str, object]] = ()) -> None:
        self._ids: set[int] = set()
        self._idxs: set[ElementIndex] = set()
        for entry in entries:
            self.add(entry)

    def add(self, entry: Mapping[str, object]) -> None:
        self._ids.add(id(entry))
        idx = _element_idx(entry)
        if idx is not None:
            self._idxs.add(idx)

    def __contains__(self, entry: Mapping[str, object]) -> bool:
        return id(entry) in self._ids or _element_idx(entry) in self._idxs


class RowReceiptLedger:
    """Every (section, entry, element) a renderer wrote this render, plus
    the entries it set aside on purpose. One per render: `generate` builds a
    fresh one, so nothing leaks between renders of the same generator."""

    def __init__(self) -> None:
        self._written: list[tuple[str | None, Mapping[str, object], _Element]] = []
        self._set_aside: list[Mapping[str, object]] = []
        self._section: str | None = None
        self._cursor: Mapping[str, object] | None = None

    def begin_section(self, section: str) -> None:
        """Writes from here on are `section`'s own rows."""
        self._section = section

    def end_section(self) -> None:
        """Ends the section and its entry cursor: a write after this (the
        Appendix recovery passes) counts as `elsewhere`, never as a row of
        the last section, and is never credited to that section's last entry."""
        self._section = None
        self._cursor = None

    def begin_entry(self, entry: Mapping[str, object]) -> None:
        """Rows written from here on belong to `entry`."""
        self._cursor = entry

    def end_entry(self) -> None:
        """Rows written from here on belong to no entry, until the next
        `begin_entry`: a renderer calls it when its entry loop ends, so a
        later row in the same section is not credited to the last entry."""
        self._cursor = None

    def wrote(self, element: _Element, entry: Mapping[str, object] | None = None) -> None:
        """Record that `element` (a `w:tr` or `w:p`) renders `entry`, or the
        entry `begin_entry` marked when none is passed. With neither, the
        write belongs to no entry and is not recorded."""
        owner = entry if entry is not None else self._cursor
        if owner is not None:
            self._written.append((self._section, owner, element))

    def set_aside(self, entry: Mapping[str, object]) -> None:
        """Record that a renderer deliberately writes no row for `entry` (a
        source column-header row, a withheld record): not a loss."""
        self._set_aside.append(entry)

    def elements_for(self, entry: Mapping[str, object]) -> list[_Element]:
        """The rows and paragraphs recorded for `entry` itself, by identity and
        in write order: not a fan-out sibling's, which shares its index. The
        #1205 sub-point pass anchors a degree's, a leadership role's, a
        committee's or a talk's prose only under one of them."""
        return [element for _section, owner, element in self._written if owner is entry]

    def _live(self, appendix_section: str) -> tuple[_EntrySet, _EntrySet]:
        """(entries with a live row written by a body section, entries with
        any live element at all) -- live meaning still in the document body."""
        in_section, anywhere = _EntrySet(), _EntrySet()
        for section, entry, element in self._written:
            if _in_body(element):
                anywhere.add(entry)
                if section is not None and section != appendix_section:
                    in_section.add(entry)
        return in_section, anywhere

    def receipts(self, given_by_section: Mapping[str, Sequence[Mapping[str, object]]],
                 appendix_section: str = "appendix") -> list[SectionReceipt]:
        """One receipt per section. An entry is written when a body section
        wrote it a live row; lost when no live element renders it at all and
        it was not set aside; anything else is elsewhere. The Appendix needs
        no list of its own: its writer records each line through the same
        comment hook, under `appendix_section`."""
        written_set, live_set = self._live(appendix_section)
        accounted_set = _EntrySet(self._set_aside)
        out: list[SectionReceipt] = []
        for section, entries in given_by_section.items():
            written = [e for e in entries if e in written_set]
            lost = tuple(_lost_entry(e) for e in entries
                         if e not in live_set and e not in accounted_set)
            out.append(SectionReceipt(section=section, linted=section in LINTED_SECTIONS,
                                      given=len(entries), written=len(written),
                                      elsewhere=len(entries) - len(written) - len(lost),
                                      lost=lost))
        return out


class DiscardingLedger(RowReceiptLedger):
    """Records nothing. The generator's class-level default, for an instance
    built without `__init__` (several tests do, to call one writer): it can
    write rows without a ledger, and no state is shared between such
    instances. `__init__` and every `generate` replace it with a real one."""

    def begin_section(self, section: str) -> None:
        return None

    def begin_entry(self, entry: Mapping[str, object]) -> None:
        return None

    def wrote(self, element: _Element, entry: Mapping[str, object] | None = None) -> None:
        return None

    def set_aside(self, entry: Mapping[str, object]) -> None:
        return None
