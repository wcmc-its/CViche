"""Render-presence primitives shared between the offline run doctor
(unified_pipeline.run_doctor) and stage 6's own #221 recovery pass
(stage_6_word_template). Both decide whether an entry's text surfaced in the
rendered output by breaking it into fragments; keeping that one splitter here
stops the two copies drifting apart (they were verbatim-duplicated with no
enforced sync)."""

from collections.abc import Mapping
from typing import Any


def entry_fragments(text: str | None) -> list[str]:
    """An entry's fragments: per line, per '|' cell, and per tab cell.

    `str(text or "")` rather than a bare split: entry['text'] is absent on some
    stage-5 records and None on others, and every one of the ten call sites
    this replaces coerced defensively. The annotation states the contract;
    the coercion is what survives a record that breaks it.
    """
    return [frag for line in str(text or "").split("\n")
            for cell in line.split("|") for frag in cell.split("\t")]


def entry_lines(text: str | None) -> list[str]:
    """An entry's non-empty lines, stripped.

    Newline only -- deliberately NOT the same split as `entry_fragments`. This
    is the exact idiom that was copy-pasted ten times across stage 6's section
    renderers; it lives here so the next change to it happens once.

    Note what it does NOT see, because callers have been surprised by this:
    only 612 of the corpus's 36,933 entries contain a newline at all, while
    8,672 are multi-part once '|' and tab are counted. So for 8,060 entries
    (21.8%, spread across all 100 corpus CVs) this returns a single blob for
    content that actually carries 2-8 separated parts.

    That is preserved behaviour, not an endorsement: consolidating first makes
    switching a call site to `entry_fragments` a one-line change instead of a
    ten-line one, and each such switch is a rendering change that needs its own
    corpus gate. Do not "fix" this function to split on more separators --
    every current caller would change output at once.
    """
    return [line.strip() for line in str(text or "").split("\n") if line.strip()]


# The extractor's join between the cells of one table row
# (`core/docx_structure_extractor.py`); a cell's own paragraphs are joined with
# a newline inside it.
CELL_SEPARATOR = " | "


def rejoin_wrapped_row(text: str | None) -> str | None:
    """One table row whose cells wrap over paragraphs, read back as that ONE
    row (#987); None when `text` is not such a row.

    The extractor joins a cell's own paragraphs with a newline and the row's
    cells with `CELL_SEPARATOR`, so a single course whose title cell wraps has
    several `entry_lines` lines although it is one entry. The row is wrapped
    when its non-empty cells do not all have the same number of lines: a row of
    N courses stacked in its cells has N paragraphs in EVERY cell, a wrapped
    cell makes the counts differ. Empty cells (a blank column) are not
    counted. A single cell, or cells that all have the same count, is None and
    the caller keeps the lines as they are.

    Each cell's paragraphs rejoin with a space and the cells with
    `CELL_SEPARATOR`, so every token of `text` is kept, in order; empty cells
    are dropped, as `_clean_inline_tabs` drops them when it renders the row.
    Whether `text` is ONE row (not several rows fused into one entry) is the
    caller's to decide from the element indices; this only reads the text.
    """
    cells = [entry_lines(cell) for cell in str(text or "").split(CELL_SEPARATOR)]
    if len({len(cell) for cell in cells if cell}) < 2:
        return None
    return CELL_SEPARATOR.join(" ".join(cell) for cell in cells if cell)


def wrapped_row_text(entry: Mapping[str, Any]) -> str | None:
    """The raw text of ONE table row whose cells wrap over paragraphs, rejoined
    into a single line (#987); None for any other entry.

    "One row" is a single `table_row` element (`element_idx_start ==
    element_idx_end`); whether its cells wrap, as opposed to holding several
    stacked records, is `rejoin_wrapped_row`'s test. Shared by every stage-6
    section that renders an entry's raw lines one bullet each.
    """
    start, end = entry.get('element_idx_start'), entry.get('element_idx_end')
    if entry.get('element_type') != 'table_row' or start is None or start != end:
        return None
    return rejoin_wrapped_row(entry.get('text'))
