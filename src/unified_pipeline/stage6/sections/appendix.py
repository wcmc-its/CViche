"""Section T: the appendix -- content that reached no other section (#398).

Not a WCM template section in the sense the others are: T is appended to the end
of the document, and everything in it is a placement failure the pipeline is
choosing to admit to rather than swallow. The section exists so that a
classification miss costs the reader a scroll instead of costing them the
content, and its formatting deliberately mirrors S. BIBLIOGRAPHY so it reads as
part of the document.

Four filters run before anything is written. The first three are
precision-biased -- an entry that is merely suspicious survives:

- empty text, WCM template instructions, and source-CV furniture (title pages,
  date stamps -- #213). These reach the unmapped pile precisely because they are
  not CV content, and letting them through produced the large spurious appendix
  dumps `core.template_boilerplate` was written to stop.
- entries that render to nothing once the readers' cell separators are collapsed
  by `_clean_inline_tabs`. A blank template table row ("|  |  |") is non-empty as
  raw text and empty on the page.
- table column-header rows that reached the unmapped pile T-coded (#424):
  `_is_column_header_row` (`..render_check`) flags a row as a header when at
  least half its words are column-label vocabulary (title, institution, role,
  name, date, ...), catching shapes like "Year: Degree | Discipline |
  Institution/Location" and bare "NAME:" before they reach the appendix as a
  spurious numbered line and shift the numbering of the genuine entries after
  it. Unlike the first three, this one is not strictly precision-biased: a
  short entry made up entirely of column-label words (e.g. a two-word
  "Committee Chair" T-coded row) can trip the same vocabulary-majority
  heuristic and be dropped, a known false-positive class the 66-CV corpus
  does not currently exercise.

What was dropped is reported ONCE, as a single Word comment on the introductory
paragraph, rather than per entry -- the dropped blocks are boilerplate, and N
comments saying so is itself noise.

Entries are grouped under the source CV heading they were found beneath, since
"From ..." is usually enough for a reader to see what the pipeline missed and
where it belonged. Numbering restarts under each heading. Bodies are truncated
at 200 characters: the appendix is a pointer back to the original document, not
a second copy of it.
"""
from typing import Dict, List

from ...core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)
from ..formatting import _set_font
from ..normalization import _clean_inline_tabs
from ..render_check import _is_column_header_row


class AppendixSection:
    """Section T writers, mixed into `WCMTemplateGenerator`."""

    def _fill_appendix(self, unmapped_entries: List[Dict]):
        """Add appendix section for unmapped content.

        Creates a T. Appendix section listing entries that couldn't be mapped
        to the WCM template structure. Format matches S. BIBLIOGRAPHY style.
        """
        if not unmapped_entries:
            return

        # Layer 3 backstop: drop WCM-template instruction boilerplate, source-CV
        # furniture (title lines, date stamps — #213), empty entries, and table
        # column-header rows (#424) that slipped through to the unmapped pile so
        # they do not pollute the Appendix. A T-coded header row ("Title |
        # Institution/Location | Dates", "1. NAME:") is not content -- it is the
        # header of a table the reader emits as a data row -- and would
        # otherwise reach the appendix as a spurious numbered line, shifting the
        # numbering of genuine entries after it. The first three checks are
        # precision-biased; the header-row check can misclassify a short,
        # all-vocabulary phrase as a header and drop it (see the module
        # docstring).
        _pre_filter = len(unmapped_entries)
        unmapped_entries = [
            e for e in unmapped_entries
            if e.get("text", "").strip()
            and not is_template_instruction(e.get("text", ""))
            and not is_source_boilerplate(e.get("text", ""))
            and not _is_column_header_row(e.get("text", ""))
        ]

        # Render each surviving entry once, collapsing the readers' internal cell
        # separators. A table row whose cells are all empty (a blank WCM template
        # row, e.g. "|  |  |") is non-empty as raw text but renders to "" — it
        # carries no information, so it is dropped rather than shown as a bullet.
        rendered_entries = [
            (e, _clean_inline_tabs(e.get("text", "")))
            for e in unmapped_entries
        ]
        rendered_entries = [(e, t) for e, t in rendered_entries if t.strip()]

        _appendix_filtered = _pre_filter - len(rendered_entries)
        if _appendix_filtered:
            print(f"Filtered {_appendix_filtered} boilerplate/empty entries from Appendix")

        if not rendered_entries:
            return

        if self.verbose:
            print(f"Adding Appendix ({len(rendered_entries)} unmapped entries)...")

        # Add blank paragraph before appendix header (matching BIBLIOGRAPHY style)
        self.doc.add_paragraph()

        # Add appendix header - matching BIBLIOGRAPHY style (bold + underline)
        appendix_para = self.doc.add_paragraph()
        run = appendix_para.add_run("T. APPENDIX")
        _set_font(run, bold=True)
        run.underline = True

        # Add explanatory text
        intro_para = self.doc.add_paragraph()
        run = intro_para.add_run(
            "The following content from the original CV was not successfully mapped to this CV format:"
        )
        _set_font(run)

        # Emit ONE summary doc comment for the boilerplate we removed (rather
        # than a per-entry comment for each dropped block).
        if _appendix_filtered:
            self._add_word_comment(
                intro_para,
                f"{_appendix_filtered} boilerplate/empty block"
                f"{'s' if _appendix_filtered != 1 else ''} removed",
                author="Template Filter",
            )

        # Add blank paragraph after intro text
        self.doc.add_paragraph()

        # Group entries by their original CV section header
        entries_by_header = {}
        for entry, text in rendered_entries:
            hierarchy = entry.get('hierarchy', [])
            header = hierarchy[0] if hierarchy else 'Unknown Section'
            if header not in entries_by_header:
                entries_by_header[header] = []
            entries_by_header[header].append((entry, text))

        # List entries grouped by original header
        is_first_section = True
        for header, entries in entries_by_header.items():
            # Add blank paragraph before each section header (except the first one)
            if not is_first_section:
                self.doc.add_paragraph()
            is_first_section = False

            # Add subsection header showing original CV section
            header_para = self.doc.add_paragraph()
            run = header_para.add_run(f"From \"{header}\":")
            _set_font(run, bold=True)

            for i, (entry, text) in enumerate(entries, start=1):
                # Truncate long entries
                if len(text) > 200:
                    text = text[:200] + '...'

                entry_para = self.doc.add_paragraph()
                bullet_text = f"{i}. {text}"
                run = entry_para.add_run(bullet_text)
                _set_font(run)

                # Add comments from entry (e.g., why it was classified as T)
                self._add_entry_comments(entry_para, entry)

                self.stats['entries_inserted'] += 1
