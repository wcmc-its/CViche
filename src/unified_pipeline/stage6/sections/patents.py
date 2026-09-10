"""Section M2D: patents and inventions (#398).

The only section that renders one TABLE PER RECORD instead of one row per
record. A patent has eight possible attributes and most CVs supply three or
four, so a fixed column grid would be mostly empty; a two-column label/value
table per patent shows only the attributes that exist. The label order is fixed
-- title, patent number, inventors, status, filing date, issue date, assignee,
description -- so patents stay comparable even though no two carry the same set.

Two layers (#739 review). `_normalize_patent` resolves one stage-4 dict into a
frozen `Patent` and `_build_patent_rows` turns a `Patent` into its
(label, value) rows; neither touches a document, so the row logic is tested
without python-docx. `_renderable_patents` is the whole decision for the
section: sorted, normalized, and with the sparse entries already dropped, so
the render loop knows how many tables it will place before it places any.

Because each table is created by `add_table` it lands at the END of the
document, and each one has to be moved back under the heading afterwards --
through `_insert_after`, against a cursor element that starts as the section
heading, becomes each table as it is placed, and becomes the spacing paragraph
between tables when one is added. Without it every patent would be inserted
directly after the heading and the list would render backwards. The cursor
advances only when a table was actually placed: a detached cursor is logged,
counted in `tables_misplaced`, and left where it was, so one failure cannot
drag the tables after it away from the section (#547).

The template's own instruction line ("Please include inventors, title of
invention and patent number.") sits directly under the heading and is blanked
before anything is written, so it does not read as part of the first patent.

An entry with neither a title nor a patent number is skipped: patents arrive
with sparse partial records fairly often, and a table of nothing but a filing
date is noise. A `narrative` of MIN_NARRATIVE_CHARS or fewer is dropped for the
same reason.

Rendering twice into the same document used to append a second set of tables.
`_remove_rendered_patent_tables` now clears the tables this writer built --
recognised by their first label, which no template table carries -- and the
spacing paragraphs between them, before rendering again.
"""
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

try:
    from docx.oxml.ns import qn
    from docx.oxml.xmlchemy import BaseOxmlElement
    from docx.table import Table
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import (
    DetachedAnchorError,
    _insert_after,
    _set_cell_vertical_alignment,
    _set_font,
    _set_table_border,
    format_date_for_section,
)
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)

PATENT_TAXONOMY_CODE = 'M2D'

# Heading text, tried in this order (substring, case-insensitive): the full
# WCM heading first, since the bare word also matches it.
PATENT_SECTION_HEADINGS = ("Patents & Inventions", "Patents")

# The template's instruction paragraph directly under the heading starts
# with this; it is blanked so it does not read as part of the first patent.
PATENT_INSTRUCTION_PREFIX = "Please include"

# A narrative this short is a fragment, not a description worth a row.
MIN_NARRATIVE_CHARS = 10

# Row labels in template order. `_remove_rendered_patent_tables` recognises a
# table this writer built by its first label; the template has no table under
# the heading, so no template table can match.
TITLE_LABEL = 'Title of invention:'
PATENT_NUMBER_LABEL = 'Patent number:'
INVENTORS_LABEL = 'Inventors:'
STATUS_LABEL = 'Status:'
FILING_DATE_LABEL = 'Filing date:'
ISSUE_DATE_LABEL = 'Issue date:'
ASSIGNEE_LABEL = 'Assignee:'
DESCRIPTION_LABEL = 'Description:'
PATENT_ROW_LABELS = frozenset({
    TITLE_LABEL, PATENT_NUMBER_LABEL, INVENTORS_LABEL, STATUS_LABEL,
    FILING_DATE_LABEL, ISSUE_DATE_LABEL, ASSIGNEE_LABEL, DESCRIPTION_LABEL,
})


@dataclass(frozen=True)
class Patent:
    """One patent, fully resolved. Built only by `_normalize_patent`.

    The two dates are already formatted for M2D. `source_entry` is the
    stage-4 dict, carried through only so `_add_entry_comments` can attach
    the upstream pipeline comments to the table.
    """

    title: str = ''
    patent_number: str = ''
    inventors: str = ''
    status: str = ''
    filing_date: str = ''
    issue_date: str = ''
    assignee: str = ''
    narrative: str = ''
    source_entry: Mapping[str, Any] | None = None

    @property
    def is_renderable(self) -> bool:
        """A patent with neither a title nor a number is a sparse partial
        record; a table of its remaining attributes would be noise."""
        return bool(self.title or self.patent_number)


def _text(value: object) -> str:
    """A field value as the string that will appear in a cell: '' for any
    falsy value (None, '', 0), `str()` of anything else."""
    return str(value) if value else ''


def _normalize_patent(entry: Mapping[str, Any]) -> Patent:
    """Resolve one raw stage-4 M2D dict into a `Patent`.

    `extracted_fields` is sometimes explicitly `None` rather than absent
    (#659), which a bare `.get('extracted_fields', {})` does not cover.
    Stage-4 fields: patent_number, title, inventors, filing_date, issue_date,
    status, assignee, narrative.
    """
    fields = entry.get('extracted_fields') or {}
    filing_date = _text(fields.get('filing_date'))
    issue_date = _text(fields.get('issue_date'))
    return Patent(
        title=_text(fields.get('title')),
        patent_number=_text(fields.get('patent_number')),
        inventors=_text(fields.get('inventors')),
        status=_text(fields.get('status')),
        filing_date=(format_date_for_section(filing_date, PATENT_TAXONOMY_CODE)
                     if filing_date else ''),
        issue_date=(format_date_for_section(issue_date, PATENT_TAXONOMY_CODE)
                    if issue_date else ''),
        assignee=_text(fields.get('assignee')),
        narrative=_text(fields.get('narrative')),
        source_entry=entry,
    )


def _build_patent_rows(patent: Patent) -> tuple[tuple[str, str], ...]:
    """The (label, value) rows of one patent table, in template order,
    including only the attributes that have a value."""
    candidates = (
        (TITLE_LABEL, patent.title),
        (PATENT_NUMBER_LABEL, patent.patent_number),
        (INVENTORS_LABEL, patent.inventors),
        (STATUS_LABEL, patent.status),
        (FILING_DATE_LABEL, patent.filing_date),
        (ISSUE_DATE_LABEL, patent.issue_date),
        (ASSIGNEE_LABEL, patent.assignee),
        (DESCRIPTION_LABEL,
         patent.narrative if len(patent.narrative.strip()) > MIN_NARRATIVE_CHARS else ''),
    )
    return tuple((label, value) for label, value in candidates if value)


def _renderable_patents(entries: Sequence[Mapping[str, Any]]) -> tuple[Patent, ...]:
    """Decide the whole of section M2D without touching a document: reverse
    chronological, normalized, sparse records dropped. The render loop
    counts spacing against this tuple, so a skipped entry never leaves a
    trailing spacing paragraph after the last rendered table."""
    return tuple(
        patent for patent in
        (_normalize_patent(entry) for entry in sort_entries_reverse_chronological(entries))
        if patent.is_renderable
    )


def _is_rendered_patent_table(element: BaseOxmlElement, doc: Any) -> bool:
    """True for a body table this writer built: a `w:tbl` whose first cell
    is one of the patent row labels."""
    if element.tag != qn('w:tbl'):
        return False
    table = Table(element, doc)
    if not table.rows or not table.rows[0].cells:
        return False
    return table.rows[0].cells[0].text.strip() in PATENT_ROW_LABELS


def _is_blank_paragraph(element: BaseOxmlElement) -> bool:
    return element.tag == qn('w:p') and not ''.join(
        t.text or '' for t in element.iter(qn('w:t'))).strip()


class PatentsSection:
    """Section M2D writers, mixed into `WCMTemplateGenerator`."""

    def _fill_patents(self, entries: Sequence[Mapping[str, Any]]) -> None:
        """Fill Patents & Inventions section (M2D entries).

        Creates an individual 2-column label/value table per patent, inserted
        after the "Patents & Inventions" heading in the template, most recent
        first, with a spacing paragraph between consecutive tables.

        `entries` are raw stage-4 dicts because `generate()` dispatches the
        same shape to every section; `_renderable_patents` is the boundary
        where they become `Patent`s, and nothing below it reads a dict.
        """
        if not entries:
            return

        heading = self._locate_patent_section()
        if heading is None:
            logger.warning(
                "Patents & Inventions: section heading not found in template; "
                "%d entries not rendered", len(entries))
            return

        logger.info("Filling Patents & Inventions (%d entries)...", len(entries))
        self._remove_rendered_patent_tables(heading)

        patents = _renderable_patents(entries)
        if len(patents) < len(entries):
            logger.info("Patents & Inventions: skipped %d sparse entries "
                        "(no title or patent number)", len(entries) - len(patents))

        cursor = heading
        for i, patent in enumerate(patents):
            table = self._render_patent_table(patent)
            self.stats['entries_inserted'] += 1
            if not self._place_patent_table(table, cursor, i + 1, len(patents)):
                continue
            cursor = table._tbl
            # Spacing between patent tables (not after the last one).
            if i < len(patents) - 1:
                spacing_para = self._add_spacing_paragraph(after_element=table._tbl)
                if spacing_para is not None:
                    cursor = spacing_para

    def _locate_patent_section(self) -> BaseOxmlElement | None:
        """The heading paragraph's element, or None. Blanks the template's
        instruction paragraph directly under it on the way."""
        section_idx = None
        for heading_text in PATENT_SECTION_HEADINGS:
            section_idx = self._find_paragraph_with_text(heading_text)
            if section_idx is not None:
                break
        if section_idx is None:
            return None
        if section_idx + 1 < len(self.doc.paragraphs):
            next_para = self.doc.paragraphs[section_idx + 1]
            if next_para.text.strip().startswith(PATENT_INSTRUCTION_PREFIX):
                next_para.text = ""
        return self.doc.paragraphs[section_idx]._element

    def _remove_rendered_patent_tables(self, heading: BaseOxmlElement) -> int:
        """Drop the patent tables a previous render of this section left
        under the heading, and the spacing paragraphs between them, so a
        second render replaces them instead of adding a second set. Stops at
        the first element that is neither, so the blanked instruction
        paragraph and everything after it are untouched. Returns the number
        of tables removed."""
        removed = 0
        sibling = heading.getnext()
        while sibling is not None:
            following = sibling.getnext()
            if _is_rendered_patent_table(sibling, self.doc):
                removed += 1
            elif not (_is_blank_paragraph(sibling) and following is not None
                      and _is_rendered_patent_table(following, self.doc)):
                break
            sibling.getparent().remove(sibling)
            sibling = following
        if removed:
            logger.info("Patents & Inventions: removed %d previously rendered "
                        "patent tables before rendering again", removed)
        return removed

    def _render_patent_table(self, patent: Patent) -> Table:
        """Build one patent's 2-column label/value table at the end of the
        document (where `add_table` puts it); placement is `_place_patent_table`."""
        rows = _build_patent_rows(patent)
        table = self.doc.add_table(rows=len(rows), cols=2)
        _set_table_border(table, color='808080', size=4)

        first_cell_para = None
        for ri, (label, value) in enumerate(rows):
            row = table.rows[ri]
            label_cell = row.cells[0]
            label_cell.text = label
            _set_cell_vertical_alignment(label_cell, 'center')
            for para in label_cell.paragraphs:
                if ri == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    _set_font(run, bold=True)

            value_cell = row.cells[1]
            value_cell.text = value
            _set_cell_vertical_alignment(value_cell, 'center')
            for para in value_cell.paragraphs:
                for run in para.runs:
                    _set_font(run)

        if patent.source_entry and first_cell_para:
            self._add_entry_comments(first_cell_para, patent.source_entry)
        return table

    def _place_patent_table(
        self,
        table: Table,
        cursor: BaseOxmlElement,
        ordinal: int,
        total: int,
    ) -> bool:
        """Move a built table to follow the cursor. True when it landed.

        A detached cursor means the table stays wherever `add_table` put it
        (the end of the document) instead of under the "Patents & Inventions"
        heading -- the content is not lost, only misplaced (#547). Counted in
        `tables_misplaced`, not `tables_populated`, so the latter keeps
        meaning "landed where it should have"; the caller does not advance
        the cursor, so the tables after it are not dragged along.
        """
        try:
            _insert_after(cursor, table._tbl)
        except DetachedAnchorError:
            logger.warning(
                "Patents & Inventions: table reposition failed for "
                "entry %d of %d; table left at document end instead of "
                "under the section heading", ordinal, total, exc_info=True)
            self.stats['tables_misplaced'] = self.stats.get('tables_misplaced', 0) + 1
            return False
        self.stats['tables_populated'] += 1
        return True
