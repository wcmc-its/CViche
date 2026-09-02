"""Section M2D: patents and inventions (#398).

The only section that renders one TABLE PER RECORD instead of one row per
record. A patent has eight possible attributes and most CVs supply three or
four, so a fixed column grid would be mostly empty; a two-column label/value
table per patent shows only the attributes that exist. The label order is fixed
-- title, patent number, inventors, status, filing date, issue date, assignee,
description -- so patents stay comparable even though no two carry the same set.

Because each table is created by `add_table` it lands at the END of the
document, and each one has to be moved back under the heading afterwards.
`last_element` is the cursor that makes the sequence come out in order: it
starts as the section heading, becomes each table as it is placed, and becomes
the spacing paragraph between tables when one is added. Without it every patent
would be inserted directly after the heading and the list would render
backwards.

The template's own instruction line ("Please include inventors, title of
invention and patent number.") sits directly under the heading and is blanked
before anything is written, so it does not read as part of the first patent.

An entry with neither a title nor a patent number is skipped: patents arrive
with sparse partial records fairly often, and a table of nothing but a filing
date is noise. A `narrative` shorter than ten characters is dropped for the same
reason.
"""
import logging
from typing import Dict, List

from ..formatting import (
    _set_cell_vertical_alignment,
    _set_font,
    _set_table_border,
    format_date_for_section,
)
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)


class PatentsSection:
    """Section M2D writers, mixed into `WCMTemplateGenerator`."""

    def _fill_patents(self, entries: List[Dict]):
        """Fill Patents & Inventions section (M2D entries).

        Creates an individual 2-column label/value table per patent, inserted
        after the "Patents & Inventions" heading in the template.
        WCM template instruction: "Please include inventors, title of invention
        and patent number."

        Fields from Stage 4: patent_number, title, inventors, filing_date,
        issue_date, status, assignee, narrative.
        """
        if not entries:
            return

        # Find the section in the template
        section_idx = self._find_paragraph_with_text("Patents & Inventions")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Patents")
        if section_idx is None:
            if self.verbose:
                print(f"  Warning: Could not find 'Patents & Inventions' section in template")
            return

        # Remove the instruction paragraph (the one after the header) if it starts with "Please include"
        if section_idx + 1 < len(self.doc.paragraphs):
            next_para = self.doc.paragraphs[section_idx + 1]
            if next_para.text.strip().startswith("Please include"):
                next_para.text = ""

        if self.verbose:
            print(f"Filling Patents & Inventions ({len(entries)} entries)...")

        # Sort entries reverse chronologically
        sorted_entries = sort_entries_reverse_chronological(entries)

        # Insert tables after the section header
        last_element = self.doc.paragraphs[section_idx]._element

        for i, entry in enumerate(sorted_entries):
            fields = entry.get('extracted_fields') or {}

            title = fields.get('title', '')
            patent_number = fields.get('patent_number', '')
            inventors = fields.get('inventors', '')
            filing_date = fields.get('filing_date', '')
            issue_date = fields.get('issue_date', '')
            status = fields.get('status', '')
            assignee = fields.get('assignee', '')
            narrative = fields.get('narrative', '')

            # Skip entries with no meaningful content
            if not title and not patent_number:
                if self.verbose:
                    text = entry.get('text', '')[:50]
                    print(f"  Skipping sparse patent entry: '{text}...'")
                continue

            # Format dates
            formatted_filing = format_date_for_section(filing_date, 'M2D') if filing_date else ''
            formatted_issue = format_date_for_section(issue_date, 'M2D') if issue_date else ''

            # Build rows — only include rows that have data
            rows = []
            if title:
                rows.append(('Title of invention:', title))
            if patent_number:
                rows.append(('Patent number:', patent_number))
            if inventors:
                rows.append(('Inventors:', inventors))
            if status:
                rows.append(('Status:', status))
            if formatted_filing:
                rows.append(('Filing date:', formatted_filing))
            if formatted_issue:
                rows.append(('Issue date:', formatted_issue))
            if assignee:
                rows.append(('Assignee:', assignee))
            if narrative and len(narrative.strip()) > 10:
                rows.append(('Description:', narrative))

            if not rows:
                continue

            # Create a 2-column table
            table = self.doc.add_table(rows=len(rows), cols=2)
            _set_table_border(table, color='808080', size=4)

            # Fill the table
            first_cell_para = None
            for ri, (label, value) in enumerate(rows):
                row = table.rows[ri]
                # Label cell (bold)
                label_cell = row.cells[0]
                label_cell.text = label
                _set_cell_vertical_alignment(label_cell, 'center')
                for para in label_cell.paragraphs:
                    if ri == 0 and first_cell_para is None:
                        first_cell_para = para
                    for run in para.runs:
                        _set_font(run, bold=True)

                # Value cell
                value_cell = row.cells[1]
                value_cell.text = str(value) if value else ''
                _set_cell_vertical_alignment(value_cell, 'center')
                for para in value_cell.paragraphs:
                    for run in para.runs:
                        _set_font(run)

            # Add comments from entry
            if first_cell_para:
                self._add_entry_comments(first_cell_para, entry)

            # Move table to correct position (after the last inserted element)
            body = self.doc.element.body
            body_elements = list(body)
            try:
                elem_idx = body_elements.index(last_element)
                body.insert(elem_idx + 1, table._tbl)
                self.stats['tables_populated'] += 1
            except (ValueError, IndexError):
                # last_element is no longer in the body list, so the table
                # stays wherever add_table put it (the end of the document)
                # instead of under the "Patents & Inventions" heading -- the
                # content is not lost, only misplaced (#547). Counted
                # separately from tables_populated so the stat keeps meaning
                # "landed where it should have".
                logger.warning(
                    "Patents & Inventions: table reposition failed for "
                    "entry %d of %d; table left at document end instead of "
                    "under the section heading", i + 1, len(sorted_entries))
                self.stats['tables_misplaced'] = self.stats.get('tables_misplaced', 0) + 1

            self.stats['entries_inserted'] += 1
            last_element = table._tbl

            # Add spacing between patent tables (except after the last one)
            if i < len(sorted_entries) - 1:
                spacing_para = self._add_spacing_paragraph(after_element=table._tbl)
                if spacing_para is not None:
                    last_element = spacing_para
