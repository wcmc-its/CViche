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


# Why a taxonomy code's entries were diverted to the Appendix (#531,
# #531-R2). Named constants rather than inline strings because they are a
# comparison target for `_appendix_diversion_reason` and, once emitted, a
# re-emission key for the doctor -- a typo in one spelling and the other
# would silently create a third, undocumented reason.
REASON_NO_RENDER_ROUTE = "no_render_route"
REASON_RENDERER_DECLINED = "renderer_declined"
# A record `_add_remaining_to_appendix` bulleted on behalf of
# `_reconsider_appendix_entries` / `_recover_unrendered_records` (#531-R2
# finding F1) -- distinct from the two reasons above because it is not
# about routing at all: the code MAY be fully routed (`RENDER_ROUTED_CODES`
# member), the structured render for it simply did not find this specific
# record. Applies regardless of the code's own routing status.
REASON_RECOVERED_UNRENDERED = "recovered_unrendered"

# E, G and J -- the three passthrough sections. None of the three is in
# `RENDER_ROUTED_CODES` (they have no taxonomy-code dispatch of their own --
# the passthrough writers select by source hierarchy, not code), so an
# unconsumed E/G/J entry would otherwise read as REASON_NO_RENDER_ROUTE,
# which is false: `_fill_passthrough_sections` IS their renderer, and it
# declined this specific entry (label or table shape did not match) rather
# than there being no route at all (#531-R2 finding F2).
#
# `PASSTHROUGH_CODES` is `stage6/sections/passthrough.py`'s own constant
# (#531-R3 task 4 / finding F-R2-4) -- but `appendix.py` does NOT import it:
# both modules are `stage6/sections/*` peers, and CODING_STANDARDS.md 1.3
# ("Peers do not import peers" -- [gate]) forbids exactly that edge (a real
# regression this round hit: `check_standards.py` flagged "1.3 peers do not
# import peers 0 -> 1" the first time this was written as a direct import;
# reverted to this parameter-threading approach, the same one
# `render_routed_codes` below already uses for the same reason --
# `RENDER_ROUTED_CODES` lives in `stage_6_word_template.py`, which is not a
# `sections/*` peer and is free to import both `appendix.py` and
# `passthrough.py` and pass each module's constant down as an argument).
# No existing constant elsewhere covers exactly the E/G/J triple without
# also pulling in N4 (`doctor/lints/extraction.py`'s
# `_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES` is a DIFFERENT set, for a
# different lint, and N4 is not a passthrough section -- reusing it here
# would misclassify N4 the same way F2 is fixing for E/G/J).

# Human-readable text for the two routing-based reasons, used only inside
# `message` -- the `reason` field itself stays the stable machine key above.
# REASON_RENDERER_DECLINED covers two different mechanisms with two
# different messages (the M1 no-research-summary discard, and the E/G/J
# passthrough refusal) so it is NOT looked up here; see `_diversion_message`.
# REASON_RECOVERED_UNRENDERED's message is also code-specific (repeats the
# code) so it is built directly in `_diversion_message` too.
_REASON_TEXT = {
    REASON_NO_RENDER_ROUTE: "no stage 6 section is routed to render this taxonomy code",
    REASON_RENDERER_DECLINED: "no research summary rendered",
}


def _plural_entries(count: int) -> str:
    """'entry' for 1, 'entries' otherwise -- the noun in `_diversion_message`."""
    return "entry" if count == 1 else "entries"


def _plural_was(count: int) -> str:
    """'was' for 1, 'were' otherwise -- REASON_RECOVERED_UNRENDERED's verb,
    the one message shape whose grammar needs subject-verb agreement."""
    return "was" if count == 1 else "were"


def _diversion_message(code: str, count: int, reason: str,
                        passthrough_codes: frozenset[str]) -> str:
    """The Appendix-diversion warning's human-readable `message` (#531,
    #531-R2). Three shapes, by *reason*:

    - REASON_RECOVERED_UNRENDERED: always names *code* twice (the code that
      classified the record, spelled out rather than left implicit, since
      this reason has nothing to do with routing).
    - REASON_RENDERER_DECLINED for a passthrough code (E/G/J,
      *passthrough_codes* -- `stage6/sections/passthrough.py`'s
      `PASSTHROUGH_CODES`, passed in rather than imported; see the module
      docstring comment above `_REASON_TEXT` for why): names the passthrough
      writer specifically -- the generic `_REASON_TEXT` string is for the
      OTHER REASON_RENDERER_DECLINED case (M1) and would misdescribe this
      one. Phrased passive ("refused by...") rather than "...declined
      them": the pronoun read wrong in the singular ("1 entry ... declined
      them") (#531-R3 finding r11).
    - Everything else: the shared `_REASON_TEXT` lookup.
    """
    noun = _plural_entries(count)
    if reason == REASON_RECOVERED_UNRENDERED:
        verb = _plural_was(count)
        return (f"{code}: {count} {noun} classified {code} {verb} not "
                f"found in the rendered document and {verb} recovered into "
                f"the Appendix")
    if reason == REASON_RENDERER_DECLINED and code in passthrough_codes:
        return (f"{code}: {count} {noun} diverted to the Appendix — "
                f"refused by the passthrough writer for {code} (source "
                f"section label did not match)")
    return (f"{code}: {count} {noun} diverted to the Appendix — "
            f"{_REASON_TEXT[reason]}")


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


def _appendix_diversion_reason(code: str, render_routed_codes: frozenset[str],
                                passthrough_codes: frozenset[str]) -> str:
    """Which of the two ROUTING-based ways *code*'s entries ended up
    diverted to the Appendix as NUMBERED lines (`_fill_appendix`'s own
    output -- the third reason, REASON_RECOVERED_UNRENDERED, is not routing
    -based and is assigned directly by its caller, never through this
    function). *render_routed_codes* is `RENDER_ROUTED_CODES`
    (`stage_6_word_template.py`) -- the AUTHORITATIVE routed-code set, not
    the per-call `mapped_codes` copy `generate()` mutates (the M1 discard).
    *passthrough_codes* is `stage6/sections/passthrough.py`'s
    `PASSTHROUGH_CODES`, likewise passed in rather than imported (see the
    module docstring comment near `_REASON_TEXT`). A passthrough code is
    always REASON_RENDERER_DECLINED -- checked FIRST, since E/G/J are never
    in `render_routed_codes` and would otherwise fall into the next branch
    (#531-R2 finding F2). Otherwise: a code absent from `render_routed_codes`
    never had a renderer at all (`REASON_NO_RENDER_ROUTE`); a code present
    in it still reached the Appendix only because this run's `mapped_codes`
    copy discarded it (`REASON_RENDERER_DECLINED`, the M1 case) -- passed
    the frozenset rather than the discard reason itself because today there
    is exactly one such discard case and the two-way split is all
    `generate()` needs to convey.
    """
    if code in passthrough_codes:
        return REASON_RENDERER_DECLINED
    if code not in render_routed_codes:
        return REASON_NO_RENDER_ROUTE
    return REASON_RENDERER_DECLINED


def build_appendix_diversion_warnings(
    written: Sequence[UnmappedEntry],
    recovered_codes: Sequence[str],
    render_routed_codes: frozenset[str],
    passthrough_codes: frozenset[str],
) -> list[AppendixDiversionWarning]:
    """One `appendix_diversion` warning per (taxonomy code, reason) pair
    actually present in the Appendix (#531, #531-R2 finding F1). Two input
    streams, both post-filter (nothing dropped survives either):

    - *written*: the entries `_fill_appendix` put on the page as NUMBERED
      lines, i.e. AFTER `_appendix_drop_reason` filtering. Reason is
      `_appendix_diversion_reason`'s routing-based split.
    - *recovered_codes*: one taxonomy code per "bullet" line
      `_add_remaining_to_appendix` wrote on behalf of
      `_reconsider_appendix_entries` / `_recover_unrendered_records` --
      always REASON_RECOVERED_UNRENDERED, regardless of the code's own
      routing status, since these exist because a specific record did not
      render, not because its code lacks a route.

    *passthrough_codes* (`stage6/sections/passthrough.py`'s
    `PASSTHROUGH_CODES`, #531-R3 task 4) is threaded through to
    `_appendix_diversion_reason` and `_diversion_message` rather than
    imported here -- `appendix.py` and `passthrough.py` are both
    `stage6/sections/*` peers, and CODING_STANDARDS.md 1.3 ("Peers do not
    import peers", `[gate]`) forbids that import edge; the caller
    (`generate()` in `stage_6_word_template.py`, which is not a `sections/*`
    peer) imports both modules' constants and passes them down, the same
    way it already does for `render_routed_codes`.

    Sorted by (code, reason) so the sidecar is deterministic and, when one
    code has entries in both streams (e.g. some T lines numbered, others
    bulleted), its two warnings are adjacent. An entry/code with no
    `taxonomy_code` groups under `"?"` rather than being silently skipped --
    the total count must still reconcile against the docx line total
    (EXPECTED OUTCOME 5 / F1 self-consistency).
    """
    counts: Counter[tuple[str, str]] = Counter()
    for entry in written:
        code = entry.get("taxonomy_code") or "?"
        reason = _appendix_diversion_reason(code, render_routed_codes, passthrough_codes)
        counts[(code, reason)] += 1
    for code in recovered_codes:
        counts[(code or "?", REASON_RECOVERED_UNRENDERED)] += 1

    warnings: list[AppendixDiversionWarning] = []
    for code, reason in sorted(counts):
        count = counts[(code, reason)]
        warnings.append({
            "check": "appendix_diversion",
            "code": code,
            "section": "T. APPENDIX",
            "count": count,
            "reason": reason,
            "message": _diversion_message(code, count, reason, passthrough_codes),
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
