"""Section O: institutional leadership activities (#398).

Three columns -- role/position, institution/location, dates -- and the writer's
real job is deciding whether an entry is one leadership role or several.

Source CVs list leadership as a table, and when entry extraction reads that
table it frequently returns the whole block as ONE entry whose
`extracted_fields` describe only the first row. Rendering that straight loses
every row but the first, so the writer looks past the fields at the raw text and
counts lines: 3+ lines, or 2 lines with no extracted role, means re-parse
rather than trust the fields.

`_add_multiline_leadership_rows` is that re-parse. The line parser itself is
`_parse_flattened_committee_lines` in `stage6.parsing`, shared with section P
next door (#572 -- P used to run a drifted copy that lost dates); its docstring
describes the flattened-table shapes it handles. O's own job is folding the
parsed role titles back into the activity text, because its table has no role
column for them.
"""
import logging
import re
from typing import List

from ..formatting import _clear_table_data, _set_font, format_date_range
from ..normalization import _committee_cell_text
from ..parsing import _parse_flattened_committee_lines
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import CELL_SEPARATOR, entry_lines, wrapped_row_text

logger = logging.getLogger(__name__)

# Canonical Section O header, verbatim from
# key_files/wcm_cv_template_faculty_october_2022_final.docx paragraph 156.
# Exact match only (#625 review): the old fallback searched for the bare
# substring "Leadership", which also matches "Clinical Leadership" (para 104),
# "Leadership and mentoring in programs" (para 138), and "Leadership in
# Extramural Organizations" (para 168) -- all of which appear in the same
# template and precede or follow the real O section. A substring hit on any
# of those would bind this table to the wrong section and silently write
# leadership rows into someone else's content.
_LEADERSHIP_SECTION_HEADER = "INSTITUTIONAL LEADERSHIP ACTIVITIES"

# A role/institution/dates cell wraps over a few paragraphs at most (3 in the
# 126-CV farm). A cell of 20+ lines is a stacked list of committees whose
# columns simply differ in length, which the multi-line parser must still split.
_MAX_WRAPPED_CELL_LINES = 3


def _wrapped_leadership_row(entry: dict) -> str | None:
    """The entry's raw text rejoined into ONE line when it is one table row whose
    cells wrap (#987, `wrapped_row_text`), else None.

    `wrapped_row_text` tells a wrapped row from stacked records by unequal cell
    line counts only, so a long stacked list with columns of different lengths
    passes it too; cells longer than `_MAX_WRAPPED_CELL_LINES` are not wrapping
    and return None here.
    """
    row_text = wrapped_row_text(entry)
    if row_text is None:
        return None
    cells = str(entry.get('text') or '').split(CELL_SEPARATOR)
    if max(len(entry_lines(cell)) for cell in cells) > _MAX_WRAPPED_CELL_LINES:
        return None
    return row_text


# A run of letters or digits, case-folded: what `_with_division` compares, so
# the ", " it writes and the source's own punctuation do not count as content.
_DIVISION_WORD_RE = re.compile(r"[^\W_]+")
# Function words that do not make a division a different thing: "Division of
# Cardiology" is already said by "Chief, Cardiology Division". The same set
# sibling P uses to fold its institution into a name cell (#985).
_DIVISION_STOPWORDS = frozenset({'of', 'the', 'for', 'and', 'at', 'in'})


def _division_words(text: str) -> set[str]:
    """The content words of `text`, case-folded, function words dropped."""
    return {word for word in _DIVISION_WORD_RE.findall(text.casefold())
            if word not in _DIVISION_STOPWORDS}


def _with_division(institution: object, division_department: object, role: object) -> str:
    """The Institution/Location cell: the division, department or program the
    role sits in, then the institution ("Division, Institution").

    Stage 4 fills `division_department` on O entries although the schema marks
    it `extract: false`, and the cell used to read it only when the institution
    was empty -- so in the normal case, institution set, the program or
    division that tells one leadership row from the next was dropped. It is
    left out when every one of its words is already in the role or the
    institution, so "Director, Cardiology Program" does not repeat its program.
    """
    institution_text = _committee_cell_text(institution)
    division = _committee_cell_text(division_department)
    if not institution_text:
        return division
    said = _division_words(f"{_committee_cell_text(role)} {institution_text}")
    if _division_words(division) <= said:
        return institution_text
    return f"{division}, {institution_text}"


def _looks_like_leadership_table(table) -> bool:
    """True when `table`'s header row looks like Section O's own table.

    The real template's header row is `Role(s)/Position | Institution/Location
    | Dates (yyyy-yyyy)` (key_files/wcm_cv_template_faculty_october_2022_final.docx
    paragraph 156's table) -- checked loosely, on the first cell containing
    "role", so an exact wording change to the other two columns doesn't
    false-negative this. #664 item 2: this is the validation
    `_find_table_after_paragraph` itself does not do.
    """
    if not table.rows:
        return False
    header_cells = table.rows[0].cells
    if not header_cells:
        return False
    return 'role' in header_cells[0].text.strip().lower()


class LeadershipSection:
    """Section O writers, mixed into `WCMTemplateGenerator`."""

    def _fill_leadership(self, entries: list[dict]) -> None:
        """Fill O. INSTITUTIONAL LEADERSHIP ACTIVITIES section.

        WCM template has table with: Role(s)/Position | Institution/Location | Dates
        """
        if not entries:
            return

        if self.verbose:
            logger.info("Filling Institutional Leadership (%s entries)...", len(entries))

        # Find Leadership section by exact canonical header (#625 review) --
        # fail closed rather than risk binding to a near-miss heading. See
        # _LEADERSHIP_SECTION_HEADER above for why a substring match is unsafe
        # here: writing real content into the wrong section is worse than
        # omitting it (entries not rendered here are still picked up by the
        # unrendered-record recovery pass that runs after all sections fill).
        section_idx = self._find_paragraph_exact(_LEADERSHIP_SECTION_HEADER)
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        # #664 item 2: `_find_table_after_paragraph` (stage_6_word_template.py,
        # a different PR's file) is a purely positional "next <w:tbl> after
        # this paragraph" walk with no awareness of what table it lands on.
        # Validate the shape at this call site before `_clear_table_data`
        # mutates it -- fail closed the same way the exact-header match above
        # already does (review thread 3850828607): writing real content into
        # the wrong table is worse than omitting it.
        if not _looks_like_leadership_table(table):
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            taxonomy_code = entry.get('taxonomy_code', 'O')
            original_text = entry.get('text', '')

            # Check for leadership_role field (O code schema) as well as generic role/position
            role = fields.get('leadership_role') or fields.get('role') or fields.get('position') or ''
            institution = _with_division(
                fields.get('institution') or fields.get('organization') or '',
                fields.get('division_department'), role)
            start_date = fields.get('start_date') or ''
            end_date = fields.get('end_date') or ''
            dates = format_date_range(start_date, end_date, taxonomy_code) or ''

            # Check if this entry contains multiple items (newline-separated)
            lines = entry_lines(original_text)

            # #660-equivalent completeness signal, mirrored from sibling P
            # (administrative_activities.py): extraction already gave a
            # usable role+dates, before anything below reroutes on line
            # count or a raw-text pipe.
            fields_complete = bool(role) and bool(dates)

            # Use multi-line parsing when the text contains 3+ lines — this catches
            # mega-blocks where field extraction only captured one item from many.
            # For single/double-line entries, use extracted fields normally.
            #
            # #627: mirrors sibling P -- a 1- or 2-line entry whose raw text
            # still carries an unresolved pipe-separated date column (e.g.
            # "Committee (Chair 2002-present) | 1996-Present") bypassed the
            # shared #572 line parser entirely; there is no pipe-aware
            # fallback here the way P's parenthetical regex is. `fields_complete`
            # guards this so an entry extraction already fully resolved is
            # never rerouted (and its institution, which the multiline path
            # cannot carry for a non-pipe row, lost) just because its raw
            # text happens to contain a `|`.
            has_unresolved_pipe = not fields_complete and '|' in original_text
            # #987: a row whose cells merely wrap is ONE role, not several
            # lines; each branch below that would split it renders the rejoined
            # text as the one row instead.
            row_text = _wrapped_leadership_row(entry)
            would_split = (len(lines) >= 3 or (len(lines) > 1 and not role)
                           or has_unresolved_pipe)
            if row_text is not None and would_split:
                self._add_leadership_row(table, row_text, '', '')
            elif len(lines) >= 3:
                # Multiple items merged - split them into separate rows
                self._add_multiline_leadership_rows(table, lines)
            elif len(lines) > 1 and not role:
                # Two lines, no extracted role - still try multi-line parsing
                self._add_multiline_leadership_rows(table, lines)
            elif has_unresolved_pipe:
                self._add_multiline_leadership_rows(table, lines)
            else:
                if not role and not institution:
                    role = original_text

                self._add_leadership_row(table, role, institution, dates)

    def _add_leadership_row(self, table, role: str, institution: str, dates: str) -> None:
        """Add a single row to leadership table."""
        # Defensive: never write a non-str (dict/list) into a Word cell -- it
        # raises deep in python-docx and aborts the whole document (#256).
        # Sibling P's _add_committee_row already does this (#625 review).
        role = _committee_cell_text(role)
        institution = _committee_cell_text(institution)
        dates = _committee_cell_text(dates)
        row = table.add_row()
        num_cols = len(row.cells)
        if num_cols >= 3:
            row.cells[0].text = role or ''
            row.cells[1].text = institution or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = f"{role}, {institution}" if institution else (role or '')
            row.cells[1].text = dates or ''
        elif num_cols == 1:
            # #664 item 6: a 0- or 1-column table hit neither branch above --
            # nothing was written, yet `entries_inserted` still incremented
            # unconditionally below. Mirror sibling P's single-column
            # fallback (`_add_committee_row`): fold everything into the one
            # cell rather than silently writing nothing.
            combined = ", ".join(part for part in (role, institution) if part)
            row.cells[0].text = f"{combined} - {dates}" if dates else combined
        else:
            # num_cols == 0: no cell exists to write into; nothing to do,
            # and nothing was inserted -- don't count it (#664 item 6).
            return

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1

    def _add_multiline_leadership_rows(self, table, lines: List[str]):
        """Parse multiple leadership/committee lines and add separate rows.

        The line parser is `_parse_flattened_committee_lines`, shared with
        section P (#572). O's table has no role column, so parenthetical role
        titles are folded back into the activity text: "Committee (Chair)".
        Its institution/location column is the parser's `institution` (#664):
        a "Role | Institution | Dates" pipe row keeps its middle cell.
        """
        for item in _parse_flattened_committee_lines(lines, institution_column=True):
            if item.roles:
                activity = f"{item.activity} ({'; '.join(item.roles)})"
            else:
                activity = item.activity
            # Skip if nothing renderable remains -- matches P's guard on the
            # same shared parser's output (#625 review); a role-and-date-free
            # parenthetical (e.g. a bare "(2010-2013)") would otherwise add a
            # blank leadership row.
            if not activity:
                continue
            self._add_leadership_row(table, activity, item.institution, item.dates)
