"""Section T: the appendix -- content that reached no other section (#398).

Not a WCM template section in the sense the others are: T is appended to the end
of the document, and everything in it is a placement failure the pipeline is
choosing to admit to rather than swallow. The section exists so that a
classification miss costs the reader a scroll instead of costing them the
content, and its formatting deliberately mirrors S. BIBLIOGRAPHY so it reads as
part of the document.

The work is split along its natural boundary (review on #736). The module-level
functions decide *what* the appendix holds and are pure over the entry dicts:
`_filter_unmapped_entries` (which entries survive, and why each of the rest did
not), `_group_by_source_heading` (the appendix's model: heading -> numbered
lines), `_truncate_appendix_text` and `_describe_dropped`. `AppendixSection`'s
methods only write that model into the Word document. The rules are therefore
testable without a document, and the writer carries no rule of its own.

Five checks run before anything is written, in `_appendix_drop_reason`'s order.
Every drop is counted under the check that caught it, so the summary comment
and the log line say what was removed instead of calling every drop
"boilerplate":

- blank text;
- WCM template instructions and source-CV furniture (title pages, date stamps
  -- #213). These reach the unmapped pile precisely because they are not CV
  content, and letting them through produced the large spurious appendix dumps
  `core.template_boilerplate` was written to stop;
- entries that render to nothing once the readers' cell separators are collapsed
  by `_clean_inline_tabs`. A blank template table row ("|  |  |") is non-empty as
  raw text and empty on the page. Tested before the header-row check below,
  which would otherwise claim it (a row with no words is trivially "all
  column-label vocabulary");
- table column-header rows that reached the unmapped pile T-coded (#424):
  `_is_column_header_row` (`..render_check`) flags a row as a header when at
  least half its words are column-label vocabulary (title, institution, role,
  name, date, ...), catching shapes like "Year: Degree | Discipline |
  Institution/Location" and bare "NAME:" before they reach the appendix as a
  spurious numbered line and shift the numbering of the genuine entries after
  it.

The first four checks are precision-biased -- an entry that is merely suspicious
survives. The header-row check is not strictly so: a short entry at least half
of whose words (digits count as words) are column-label vocabulary (a two-word
"Committee Chair" T-coded row, "Date: 2019") trips the same vocabulary-majority
heuristic and is dropped, a known false-positive class the 66-CV corpus does not
currently exercise. `test_stage6_appendix_header_row_filter.py` pins the real
header shapes, the numeric-token and short-entry negatives, and that
false-positive class, so a change to the heuristic is visible.

What was dropped is reported ONCE, as a single Word comment on the introductory
paragraph, rather than per entry -- N comments saying so is itself noise.

Entries are grouped under the source CV heading they were found beneath, since
"From ..." is usually enough for a reader to see what the pipeline missed and
where it belonged. The key is the top-level heading text (`hierarchy[0]`), by
design: the label the reader sees IS the heading text, so two source sections
sharing a heading would be indistinguishable as separate groups anyway. That
key is NOT proven collision-free: measured 2026-09-05 over the local corpus,
3 of 111 stage-1b files carry a duplicated top-level heading (one, 2025_Denckla's
"Conference Activities", at two genuinely distinct source positions -- the
merge-and-renumber scenario this key risks). Only one of the three,
2068_Yount ("GRANT SUPPORT", two synthetic nodes), is among the 66 stage-6
inputs, and its current appendix has a single entry under that heading, so
`_group_by_source_heading` has not yet been exercised on a real collision.
Numbering restarts under each heading. Bodies are capped at
`APPENDIX_MAX_CHARS` characters, the marker that shows the cut included: the
appendix is a pointer back to the original document, not a second copy of it.
"""
import logging
from collections import Counter
from collections.abc import Sequence
from typing import TypedDict

from ...core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)
from ..formatting import _set_font
from ..normalization import _clean_inline_tabs
from ..render_check import _is_column_header_row

logger = logging.getLogger(__name__)

# Ceiling on a rendered appendix body, INCLUDING the marker that shows a cut
# was made -- a line the reader sees is never longer than this. The old inline
# `text[:200] + '...'` produced 203-character lines against a documented 200
# (review on #736).
APPENDIX_MAX_CHARS = 200
_TRUNCATION_MARKER = "..."

# Group label for an entry that carries no source hierarchy at all.
_UNKNOWN_SECTION = "Unknown Section"

# Why an entry did not reach the appendix, named for the check that caught it.
# These are the words the summary Word comment and the log line report, in
# `DROP_REASONS` order, so a reader can tell two dropped header rows from two
# dropped blanks (review on #736: one undifferentiated count called every
# drop "boilerplate/empty").
DROP_BLANK = "blank"
DROP_TEMPLATE_INSTRUCTION = "template-instruction"
DROP_SOURCE_BOILERPLATE = "source-boilerplate"
DROP_RENDERS_EMPTY = "renders-empty"
DROP_COLUMN_HEADER = "column-header"
DROP_REASONS = (
    DROP_BLANK,
    DROP_TEMPLATE_INSTRUCTION,
    DROP_SOURCE_BOILERPLATE,
    DROP_RENDERS_EMPTY,
    DROP_COLUMN_HEADER,
)


class UnmappedEntry(TypedDict, total=False):
    """One pipeline entry as the appendix reads it (review on #736) -- the
    contract behind the old `List[Dict]` signature. `text` and `hierarchy` are
    what this module itself reads; the five named fields are not the whole
    entry, only what this module needs -- `_add_entry_comments`
    (stage_6_word_template.py:2407-2559) reads several more of an entry's
    fields for the per-line Word comment, among them `taxonomy_confidence`,
    `confidence`, `is_fragment`, `fragment_reasoning`, `fragment_of` and
    `t_validation_applied`. `total=False`: the stage-6 input JSON has no
    schema enforcing any key exists, so this documents the shape rather than
    validating it -- the read sites coerce a missing or None value to the
    empty case, as the rest of stage 6 does."""

    text: str
    hierarchy: list[str]
    taxonomy_code: str
    extracted_fields: dict[str, object]
    classification_reasoning: str


# One appendix line: the entry (kept for its per-entry comments) and its body,
# with the readers' cell separators already collapsed.
AppendixLine = tuple[UnmappedEntry, str]


# Why a taxonomy code's entries were diverted to the Appendix (#531). Named
# constants rather than inline strings because they are a comparison target
# for `_appendix_diversion_reason` and, once emitted, a re-emission key for
# the doctor -- a typo in one spelling and the other would silently create a
# third, undocumented reason.
REASON_NO_RENDER_ROUTE = "no_render_route"
REASON_RENDERER_DECLINED = "renderer_declined"

# Human-readable text for each reason, used only inside `message` -- the
# `reason` field itself stays the stable machine key above. Only M1 produces
# REASON_RENDERER_DECLINED today (the discard in `generate()` when no
# research summary rendered); if a second renderer-declined case is ever
# added, this needs a per-code (not per-reason) message instead of a shared
# string.
_REASON_TEXT = {
    REASON_NO_RENDER_ROUTE: "no stage 6 section is routed to render this taxonomy code",
    REASON_RENDERER_DECLINED: "no research summary rendered",
}


class AppendixDiversionWarning(TypedDict):
    """One `_validate_output`-shaped warning naming a taxonomy code's
    Appendix diversion (#531) -- same `check`/`section`/`message`/`evidence`
    keys the three existing sidecar checks use
    (`stage_6_word_template.py:_validate_output`), plus the two structured
    keys `lint_stage6_warnings` cannot recover from `message` alone: `code`
    and `count`. `evidence` is always `[]` by design -- this check reports a
    count, never entry text (PII surface; the Appendix itself already
    carries the text)."""

    check: str
    code: str
    section: str
    count: int
    reason: str
    message: str
    evidence: list[str]


def _appendix_diversion_reason(code: str, render_routed_codes: frozenset[str]) -> str:
    """Which of the two ways *code*'s entries ended up diverted to the
    Appendix. *render_routed_codes* is `RENDER_ROUTED_CODES`
    (`stage_6_word_template.py`) -- the AUTHORITATIVE routed-code set, not
    the per-call `mapped_codes` copy `generate()` mutates (the M1 discard).
    A code absent from `render_routed_codes` never had a renderer at all
    (`REASON_NO_RENDER_ROUTE`); a code present in it still reached the
    Appendix only because this run's `mapped_codes` copy discarded it
    (`REASON_RENDERER_DECLINED`) -- passed the frozenset rather than the
    discard reason itself because today there is exactly one discard case
    (M1) and the two-way split is all `generate()` needs to convey.
    """
    if code not in render_routed_codes:
        return REASON_NO_RENDER_ROUTE
    return REASON_RENDERER_DECLINED


def build_appendix_diversion_warnings(
    written: Sequence[UnmappedEntry], render_routed_codes: frozenset[str],
) -> list[AppendixDiversionWarning]:
    """One `appendix_diversion` warning per taxonomy code actually present in
    *written* -- the entries `_fill_appendix` put on the page as numbered
    lines, i.e. AFTER `_appendix_drop_reason` filtering (#531). Sorted by
    code so the sidecar is deterministic. An entry with no `taxonomy_code`
    groups under `"?"` rather than being silently skipped -- the count must
    still reconcile against the docx line total (EXPECTED OUTCOME 5).
    """
    counts: Counter[str] = Counter()
    for entry in written:
        counts[entry.get("taxonomy_code") or "?"] += 1

    warnings: list[AppendixDiversionWarning] = []
    for code in sorted(counts):
        count = counts[code]
        reason = _appendix_diversion_reason(code, render_routed_codes)
        warnings.append({
            "check": "appendix_diversion",
            "code": code,
            "section": "T. APPENDIX",
            "count": count,
            "reason": reason,
            "message": (f"{code}: {count} entries diverted to the Appendix "
                        f"— {_REASON_TEXT[reason]}"),
            "evidence": [],
        })
    return warnings


def _appendix_drop_reason(text: str, rendered: str) -> str | None:
    """The `DROP_*` reason *text* stays out of the appendix, or None to keep it.

    *rendered* is *text* with the readers' cell separators collapsed; it is
    passed in rather than recomputed so each survivor is rendered once.
    """
    if not text.strip():
        return DROP_BLANK
    if is_template_instruction(text):
        return DROP_TEMPLATE_INSTRUCTION
    if is_source_boilerplate(text):
        return DROP_SOURCE_BOILERPLATE
    if not rendered.strip():
        return DROP_RENDERS_EMPTY
    if _is_column_header_row(text):
        return DROP_COLUMN_HEADER
    return None


def _filter_unmapped_entries(
    entries: Sequence[UnmappedEntry],
) -> tuple[list[AppendixLine], Counter[str]]:
    """Split *entries* into the lines the appendix will show and a count of
    drops per `DROP_*` reason."""
    kept: list[AppendixLine] = []
    dropped: Counter[str] = Counter()
    for entry in entries:
        text = entry.get("text") or ""
        rendered = _clean_inline_tabs(text)
        reason = _appendix_drop_reason(text, rendered)
        if reason is None:
            kept.append((entry, rendered))
        else:
            dropped[reason] += 1
    return kept, dropped


def _group_by_source_heading(
    lines: Sequence[AppendixLine],
) -> dict[str, list[AppendixLine]]:
    """Group *lines* under their top-level source heading, in first-seen order.

    The key is `hierarchy[0]`, the heading text the reader sees in the
    `From "...":` label -- see the module docstring for why that is the
    intended key and the corpus measurement behind it. An entry with no
    hierarchy files under `_UNKNOWN_SECTION`.
    """
    groups: dict[str, list[AppendixLine]] = {}
    for entry, text in lines:
        hierarchy = entry.get("hierarchy") or []
        heading = hierarchy[0] if hierarchy else _UNKNOWN_SECTION
        groups.setdefault(heading, []).append((entry, text))
    return groups


def _truncate_appendix_text(text: str) -> str:
    """Cap *text* at `APPENDIX_MAX_CHARS`, marker included, so a rendered body
    is never longer than the documented limit."""
    if len(text) <= APPENDIX_MAX_CHARS:
        return text
    return text[: APPENDIX_MAX_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _describe_dropped(dropped: Counter[str]) -> str:
    """'3 non-content blocks removed (column-header 2, blank 1)' -- the summary
    comment's text, reasons in `DROP_REASONS` order, zero counts omitted."""
    total = sum(dropped.values())
    breakdown = ", ".join(
        f"{reason} {dropped[reason]}" for reason in DROP_REASONS if dropped[reason]
    )
    plural = "s" if total != 1 else ""
    return f"{total} non-content block{plural} removed ({breakdown})"


class AppendixSection:
    """Section T writers, mixed into `WCMTemplateGenerator`."""

    def _fill_appendix(
        self, unmapped_entries: Sequence[UnmappedEntry]
    ) -> list[UnmappedEntry]:
        """Write Section T for the entries that reached no other section.

        Filtering and grouping are the module-level functions above; this
        method and the two `_write_appendix_*` helpers only put the result on
        the page, in S. BIBLIOGRAPHY's style.

        Returns the entries actually written as numbered Appendix lines --
        *unmapped_entries* minus whatever `_appendix_drop_reason` (or an
        empty *unmapped_entries*) dropped -- the same "report back what was
        actually consumed" contract `_fill_passthrough_sections` already
        uses (#531). `_group_by_source_heading` only reorders `lines`, it
        drops nothing further, so this is exactly the numbered-line set the
        caller can count per taxonomy code.
        """
        if not unmapped_entries:
            return []

        lines, dropped = _filter_unmapped_entries(unmapped_entries)
        if dropped:
            logger.info("Appendix: %s", _describe_dropped(dropped))
        if not lines:
            return []

        if self.verbose:
            logger.info("Adding Appendix (%d unmapped entries)...", len(lines))

        self._write_appendix_intro(dropped)
        groups = _group_by_source_heading(lines)
        for position, (heading, group) in enumerate(groups.items()):
            self._write_appendix_group(heading, group, first=position == 0)

        return [entry for entry, _ in lines]

    def _write_appendix_intro(self, dropped: Counter[str]) -> None:
        """The section header, its explanatory sentence and, when anything was
        dropped, the ONE summary comment saying what and why."""
        # Blank paragraph before the header, matching BIBLIOGRAPHY.
        self.doc.add_paragraph()

        appendix_para = self.doc.add_paragraph()
        run = appendix_para.add_run("T. APPENDIX")
        _set_font(run, bold=True)
        run.underline = True

        intro_para = self.doc.add_paragraph()
        run = intro_para.add_run(
            "The following content from the original CV was not successfully mapped to this CV format:"
        )
        _set_font(run)
        if dropped:
            self._add_word_comment(
                intro_para, _describe_dropped(dropped), author="Template Filter"
            )

        # Blank paragraph after the intro text.
        self.doc.add_paragraph()

    def _write_appendix_group(
        self, heading: str, lines: Sequence[AppendixLine], first: bool
    ) -> None:
        """One `From "<heading>":` block with its lines numbered from 1."""
        # Blank paragraph between groups (not before the first).
        if not first:
            self.doc.add_paragraph()

        header_para = self.doc.add_paragraph()
        run = header_para.add_run(f'From "{heading}":')
        _set_font(run, bold=True)

        for number, (entry, text) in enumerate(lines, start=1):
            entry_para = self.doc.add_paragraph()
            run = entry_para.add_run(f"{number}. {_truncate_appendix_text(text)}")
            _set_font(run)
            # Per-entry comments (e.g. why it was classified T).
            self._add_entry_comments(entry_para, entry)
            self.stats['entries_inserted'] += 1
