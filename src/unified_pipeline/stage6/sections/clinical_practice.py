"""Section L: clinical practice, innovation and leadership (#398).

One writer over three subsections -- L1 practice, L2 innovations, L3 leadership
-- because each is the same two-branch shape and only the field names and the
header text differ:

    find the subsection header -> find the table under it -> validate the table
    -> fill rows, or fall back to bullets when there is no usable table.

The validation step is the part worth knowing about. `_find_table_after_paragraph`
returns whatever table comes next in the document, and in CVs where a clinical
subsection is empty that is somebody ELSE's table -- the blank WCM template
ships no table of its own under any of L1/L2/L3 (verified via python-docx,
#625 review), so "the next table" is always foreign: a mentee placeholder,
N2's funding table, or Section O's leadership table. Each of the three
branches therefore classifies row 0 via `_clinical_header_match` and accepts
the table ONLY on a positive match against the real header ("Title |
Institution/Location | Dates"); a confident non-match ("Award Source" /
"Funding") and an ambiguous header both reject it, falling back to the
bullet path below. (An earlier version accepted ambiguous headers too -- the
#625 review's "failing closed would silently drop real clinical content"
premise was backwards: the bullet fallback already renders the content, in
the right place, so the permissive accept only ever clobbered another
section's table. 21 of 105 corpus CVs lost a mentee's rendered table this
way; see #841.)

`_insert_multiline_as_bullets` is the bullet fallback used by all three
subsections (L2 joined L1 and L3 in #572; it used the single-bullet inserter,
which collapsed a multi-line entry into one list paragraph with soft line
breaks). It segments through `_bullet_parts` and attaches the entry's Word
comments to the first bullet only.

`_bullet_parts` deliberately diverges from
`unified_pipeline.core.render_check.entry_lines`, and exactly one input class
separates them -- text containing a tab:

    input             entry_lines       _bullet_parts   verdict
    "A\\nB"            ["A", "B"]        ["A", "B"]      same
    "A\\n\\n\\nB"        ["A", "B"]        ["A", "B"]      same
    "  A  \\n  B  "    ["A", "B"]        ["A", "B"]      same
    "" / None         []                []              same
    "A|B"             ["A|B"]           ["A|B"]         same
    "A\\tB"            ["A\\tB"]          ["A", "B"]      DIVERGES

Tab is the divergence and the whole point of #476: the readers flatten a
source CV's tab-aligned row into one string, and each fragment is a separate
item. '|' is NOT a bullet boundary here -- it is this file's own column
separator (see `_bullet_parts`), so a "Title | Institution | Dates" entry
stays one item and is welded to " — " downstream by `_clean_inline_tabs`.
That table is pinned by test_stage6_clinical_practice_fragments.py, which
asserts the "same" rows against the real `entry_lines` rather than a copy of
its rules.

All three subsections hand their raw entry text straight to this helper. They
did not always: L1 and L2 welded every tab away first and L3 kept only
`split('\\t')[0]`, so the fragment split was dormant on L1/L2 and L3 deleted
every fragment after the first from the rendered document (#476 review).

Recovering those fragments is only half the contract; the other half is not
recovering the same text twice, which a loss-only corpus census cannot see:

- L3 composes its bullet from the extracted `institution`/date fields and the
  fragments after the role are usually those very fields again, flattened out
  of one source row. `_fragment_is_new` drops a fragment the composed bullet
  already says and keeps everything else.
- Splitting an entry into N bullets also changed how much text the FIRST
  bullet holds, and stage 6's low-coverage overflow router judged an entry by
  that one paragraph -- so a fully rendered entry looked 1/N covered and was
  re-emitted whole. `_insert_multiline_as_bullets` therefore hands the
  sibling bullets it inserted to `_insert_bulleted_entry`, and the check in
  `_add_entry_comments` weighs the entry's whole rendered text.
"""
import logging
import re
from collections.abc import Sequence

try:
    from docx.table import Table, _Cell, _Row
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import (
    _clear_table_data,
    _set_font,
    format_date_for_section,
    format_date_range,
)
from ..normalization import _committee_cell_text
from ..parsing import _is_structural_label
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)


def _bullet_parts(text: str) -> list[str]:
    """Stripped, non-empty parts of a clinical-practice bullet-fallback text
    (#476), split on '\\n' and '\\t' -- deliberately NOT '|'.

    '|' is this file's own column separator, used at every table-fill branch
    below to pull Title/Location/Dates apart as fields of ONE entry, never as
    a signal of separate entries -- splitting on it here, inside the shared
    bullet writer all three subsections use, would turn a single
    "Role | Institution | Dates" bullet into three wrong ones. It stays one
    part here and `_clean_inline_tabs` welds it to " — " on the way into the
    paragraph.

    '\\t' is the opposite: it is how the readers flatten a source CV's
    tab-aligned row, and each fragment is its own item. All three of this
    file's bullet-fallback branches now pass their raw entry text in, so this
    split reaches production; see the module docstring for the exact table of
    where this contract diverges from `entry_lines`.
    """
    parts = []
    for line in str(text or "").split("\n"):
        for cell in line.split("\t"):
            cell = cell.strip()
            if cell:
                parts.append(cell)
    return parts


# A "content word" for the duplication check below: a run of letters or
# digits, case-folded. Punctuation is not part of the comparison because the
# composed bullet writes its own (", ") between fields while the flattened
# fragment it duplicates carries none.
_CONTENT_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _content_words(text: str) -> list[str]:
    """`text` reduced to its case-folded content words."""
    return _CONTENT_WORD_RE.findall(str(text or "").casefold())


def _fragment_is_new(fragment: str, composed: str) -> bool:
    """True when `fragment` says something `composed` does not already say.

    L3's bullet fallback composes "Role, Institution, Dates" out of the
    extracted fields and then appends the tab fragments that followed the
    role in the source text. Those are usually two views of the SAME row --
    the reader flattened one tab-aligned line, and stage 4 extracted its
    columns as fields -- so appending every fragment rendered the institution
    and the dates a second time, as their own bullets (#476 review; one
    corpus CV grew a third paragraph whose entire text was already inside
    the first).

    The comparison is on content words in contiguous order, so "2015-2020"
    is recognised inside "Attending Physician, NYP Weill Cornell, 2015-2020"
    across the comma the composer added, and a short fragment cannot match
    the middle of a longer word the way a plain substring test would. A
    fragment with no content words at all -- stray punctuation left by the
    flattening -- says nothing new by definition.

    Genuinely new text is the case this must NOT swallow: a fragment such as
    a programme or rotation name that appears nowhere in the composed bullet
    still gets its own bullet, which is the data loss the review found in
    the first place.
    """
    words = _content_words(fragment)
    if not words:
        return False
    composed_words = _content_words(composed)
    span = len(words)
    return not any(composed_words[i:i + span] == words
                   for i in range(len(composed_words) - span + 1))

# Section L taxonomy codes (docs/CODING_STANDARDS.md §8.2): L1 Clinical
# Practice, L2 Clinical Innovations, L3 Clinical Leadership.
CLINICAL_TAXONOMY_CODES = ('L1', 'L2', 'L3')
CLINICAL_PRACTICE_CODE, CLINICAL_INNOVATION_CODE, CLINICAL_LEADERSHIP_CODE = CLINICAL_TAXONOMY_CODES

# Positive-match target header for the L1/L2/L3 subsection tables.
#
# Verified against key_files/wcm_cv_template_faculty_october_2022_final.docx
# via python-docx (#625 review): the *blank* template ships no table at all
# under Clinical Practice/Innovations/Leadership -- paragraphs 96-107 are
# pure instruction prose, and the next actual table in the document is the
# "Award Source" funding table the guard below already excludes. So this
# exact string cannot be read off an in-place Clinical Practice table the
# way, say, Section O's canonical header can. It is the WCM template's
# standard 3-column "Title | Institution/Location | Dates" convention used
# by every other single-item-per-row subsection table in the same document
# (byte-identical on doc.tables[30:33], the Invitations to Speak Regional/
# National/International tables; the sibling convention 'Role(s)/Position |
# Institution/Location | Dates' is Section O's own table, see leadership.py)
# -- the best available real-header source, not a byte-exact Clinical
# Practice header. Because of that, a mismatch here is treated as merely
# ambiguous, never as a rejection (see `_clinical_header_match`).
_CLINICAL_TABLE_HEADER = ('Title', 'Institution/Location', 'Dates (yyyy)')

# Header substrings that confidently mean "this is a funding/grant table, not
# a clinical one" -- the negative check `_clinical_header_match` still runs
# first, unchanged from the pre-existing per-renderer guard (#625 review: was
# duplicated three times, now lives once here).
_FUNDING_HEADER_INDICATORS = ('award source', 'funding')


def _clinical_header_match(first_row_cells: Sequence[_Cell]) -> bool | None:
    """Classify a table's header row against the real subsection header.

    Returns True on a positive, tolerant match to `_CLINICAL_TABLE_HEADER`
    (case-insensitive; a real CV table may have been hand-edited by the
    faculty member, so this only requires the first cell to start with
    "title" and the second to mention "institution" or "location" -- not a
    byte-exact match), False when the header is confidently a funding table
    ("Award Source"/"Funding"), and None otherwise.

    The three call sites (L1/L2/L3) accept a table ONLY on True; both False
    and the ambiguous None are a rejection, and the caller falls back to the
    bullet path. An earlier version treated None as an accept ("HARD SAFETY
    GATE", #625 review) -- see #841: the bullet fallback already renders the
    content in the right place, so accepting an unrecognized header only
    ever meant writing over some OTHER section's table (the WCM template has
    no table of its own under any clinical heading).
    """
    texts = [c.text.strip().lower() for c in first_row_cells]
    joined = ' '.join(texts)
    if any(indicator in joined for indicator in _FUNDING_HEADER_INDICATORS):
        return False
    expected_first_cell = _CLINICAL_TABLE_HEADER[0].lower()  # 'title'
    if len(texts) >= 2 and texts[0].startswith(expected_first_cell) and (
        'institution' in texts[1] or 'location' in texts[1]
    ):
        return True
    return None


class ClinicalPracticeSection:
    """Section L writers, mixed into `WCMTemplateGenerator`."""

    def _add_clinical_table_row(self, table: Table, three_col: list[str], two_col: list[str], one_col: list[str]) -> _Row:
        """Add a row to `table`, populate it according to its actual column
        count, and apply the standard cell font to every run.

        Centralizes the row-creation/text-write/font-apply sequence that was
        previously duplicated across the L1/L2/L3 renderers below (#625
        review). `three_col`, `two_col`, and `one_col` are the cell values to
        use for a 3-, 2-, and 1-column table respectively -- this module's
        tables are always 3-column in the real WCM template; the narrower
        branches exist only so a differently-shaped table still renders
        something instead of raising. Every value is run through
        `_committee_cell_text` before the write, so a non-string extracted
        field (list/dict; #256, #442, #450) can never reach `cell.text =`
        and abort the whole document.
        """
        row = table.add_row()
        n = len(row.cells)
        if n >= 3:
            values = three_col
        elif n >= 2:
            values = two_col
        else:
            values = one_col
        for cell, value in zip(row.cells, values):
            cell.text = _committee_cell_text(value)
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        return row

    def _fill_clinical_practice(self, entries_by_code: dict[str, list[dict]]) -> None:
        """Fill L. CLINICAL PRACTICE, INNOVATION, and LEADERSHIP section.

        This section has three subsections:
        - L1: Clinical Practice (patient care activities)
        - L2: Clinical Innovations (new approaches to care)
        - L3: Clinical Leadership (director/head roles)

        Each subsection uses a simple bulleted or table format. The three
        subsection bodies are extracted into their own methods below -- see
        each method's docstring for what it renders and how; this method only
        finds the entry lists and dispatches to them.
        """
        l1_entries = entries_by_code.get(CLINICAL_PRACTICE_CODE, [])
        l2_entries = entries_by_code.get(CLINICAL_INNOVATION_CODE, [])
        l3_entries = entries_by_code.get(CLINICAL_LEADERSHIP_CODE, [])

        total = len(l1_entries) + len(l2_entries) + len(l3_entries)
        if total == 0:
            return

        if self.verbose:
            print(f"Filling Clinical Practice ({len(l1_entries)} L1, {len(l2_entries)} L2, {len(l3_entries)} L3)...")

        self._fill_clinical_practice_l1(l1_entries)
        self._fill_clinical_practice_l2(l2_entries)
        self._fill_clinical_practice_l3(l3_entries)

    def _fill_clinical_practice_l1(self, l1_entries: list[dict]) -> None:
        """Fill the L1 Clinical Practice subsection: table rows, or a bullet
        fallback when no valid table is found.

        Extracted from `_fill_clinical_practice` as a pure relocation -- the
        section-finder (exact match, then a "starts with" scan that excludes
        Innovations/Leadership), the pipe-fallback column count, and the table
        column order/merge style are each deliberately different from L2/L3's
        versions below; see the module docstring for why they were not unified.
        """
        if not l1_entries:
            return

        section_idx = self._find_paragraph_exact("Clinical Practice")
        if section_idx is None:
            # Try to find a paragraph that starts with "Clinical Practice" but not the full section header
            for i, para in enumerate(self.doc.paragraphs):
                text = para.text.strip()
                if text == "Clinical Practice" or (
                    text.lower().startswith("clinical practice") and
                    "innovation" not in text.lower() and
                    "leadership" not in text.lower()
                ):
                    section_idx = i
                    break

        if section_idx is not None:
            table = self._find_table_after_paragraph(section_idx)

            # Validate table is actually for Clinical Practice, not a different
            # section. Only a positive match against the real header is
            # accepted; a confident non-match (funding table) rejects it,
            # and an AMBIGUOUS header rejects it too -- accepting an
            # unrecognized header meant writing over another section's
            # table (the WCM template has no table of its own here); the
            # bullet fallback below already renders the content in the
            # right place (#841 review; was permissive-accept, #625).
            table_is_valid = False
            if table and table.rows:
                header_match = _clinical_header_match(table.rows[0].cells)
                if header_match is False:
                    if self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical practice)")
                elif header_match is True:
                    table_is_valid = True

            sorted_entries = sort_entries_reverse_chronological(l1_entries)

            if table and table_is_valid:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1
                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    original_text = entry.get('text', '')

                    # Extract fields - clinical practice entries typically have:
                    # activity/location, institution, dates
                    # Normalize here (not just at the eventual cell write) so a
                    # non-string field never reaches the "Activity - Location"
                    # string-concatenation fallback below either (#625 review).
                    activity = _committee_cell_text(
                        fields.get('activity') or fields.get('role') or fields.get('title') or '')
                    location = _committee_cell_text(
                        fields.get('location') or fields.get('institution') or '')
                    start_date = fields.get('start_date') or ''
                    end_date = fields.get('end_date') or ''
                    dates = format_date_range(start_date, end_date, 'L1') or ''

                    # Fallback to parsing original text if fields are empty
                    if not activity and original_text:
                        # Parse "Activity | Location | Dates" format, all three
                        # columns explicitly -- parts[1] used to be dropped on
                        # the floor here (#625 review).
                        parts = original_text.split('|')
                        if len(parts) >= 2:
                            activity = parts[0].strip()
                            if not location:
                                location = parts[1].strip()
                            if len(parts) >= 3:
                                dates = parts[-1].strip() if not dates else dates

                    if activity or location:
                        # Typical clinical practice table: Activity/Type | Location | Dates
                        self._add_clinical_table_row(
                            table,
                            three_col=[activity, location, dates],
                            two_col=[activity if activity else location, dates],
                            one_col=[f"{activity} - {location} ({dates})" if dates else f"{activity} - {location}"],
                        )
                        self.stats['entries_inserted'] += 1
            else:
                # No valid table found - insert as bullet points after section header
                if self.verbose:
                    print(f"  No Clinical Practice table found, inserting as bullet points")
                bullet_count = 0
                for entry in sorted_entries:
                    original_text = entry.get('text', '').strip()

                    # Skip entries that are structural labels from the source CV
                    if _is_structural_label(entry):
                        continue

                    # L1 clinical practice entries are narrative summaries —
                    # use the full original text rather than just the extracted
                    # clinical_role label, which loses the descriptive detail.
                    # The text is handed through with its tabs intact so
                    # `_insert_multiline_as_bullets` can give each flattened
                    # part its own bullet (#476 review). This line used to weld
                    # them first (`.replace('\t', ' — ', 1).replace('\t', ' ')`),
                    # which turned the first tab into an em-dash and every
                    # later tab into a bare space -- so a three-part row
                    # rendered as ONE bullet with parts two and three run
                    # together, and the fragment split below never fired at all
                    # on this path.
                    bullet_text = original_text

                    if bullet_text:
                        # Use multiline helper to properly split entries with multiple lines
                        inserted = self._insert_multiline_as_bullets(
                            section_idx + 1 + bullet_count, bullet_text, entry,
                            add_blank_before=(bullet_count == 0)
                        )
                        bullet_count += inserted

    def _fill_clinical_practice_l2(self, l2_entries: list[dict]) -> None:
        """Fill the L2 Clinical Innovations subsection: table rows, or a
        bullet fallback when no valid table is found.

        Extracted from `_fill_clinical_practice` as a pure relocation -- see
        `_fill_clinical_practice_l1` and the module docstring for why this
        subsection's table layout and fallback text are not unified with L1/L3.
        """
        if not l2_entries:
            return

        section_idx = self._find_paragraph_with_text("Clinical Innovations")
        if section_idx is not None:
            table = self._find_table_after_paragraph(section_idx)

            # Validate the table is actually for innovations, not a grant/funding
            # table. Only a positive match against the real header is
            # accepted; a confident non-match rejects it, and an AMBIGUOUS
            # header rejects it too -- see `_clinical_header_match` and
            # #841 review (was permissive-accept, #625).
            table_is_valid = False
            if table and table.rows:
                header_match = _clinical_header_match(table.rows[0].cells)
                if header_match is False:
                    if self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical innovations)")
                elif header_match is True:
                    table_is_valid = True

            sorted_entries = sort_entries_reverse_chronological(l2_entries)

            if table and table_is_valid:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1

                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    original_text = entry.get('text', '')

                    # Normalize at extraction (#625 review) so a non-string
                    # field is clean before any string concatenation below.
                    title = _committee_cell_text(fields.get('title') or fields.get('innovation') or '')
                    role = _committee_cell_text(fields.get('role') or '')
                    description = _committee_cell_text(fields.get('description') or '')
                    start_date = fields.get('start_date') or fields.get('date') or ''
                    dates = format_date_for_section(start_date, 'L2') if start_date else ''

                    if not title and original_text:
                        # Preserve the complete source text on the fallback
                        # path -- an arbitrary 100-char cut here used to
                        # discard clinically relevant information with no
                        # business rule behind the number (#625 review).
                        title = original_text.split('|')[0].strip() if '|' in original_text else original_text

                    if title:
                        # Typical innovation table: Date | Title/Location | Role/Description
                        self._add_clinical_table_row(
                            table,
                            three_col=[dates, title, f"{role}. {description}".strip('. ') if role or description else ''],
                            two_col=[dates, title],
                            one_col=[f"{dates}: {title}" if dates else title],
                        )
                        self.stats['entries_inserted'] += 1
            else:
                # No valid table found — insert as bullet points
                if self.verbose:
                    print(f"  No Clinical Innovations table found, inserting as bullet points")
                bullet_count = 0
                for entry in sorted_entries:
                    original_text = entry.get('text', '').strip()
                    if _is_structural_label(entry):
                        continue
                    # Handed through with its tabs intact, exactly as L1
                    # does above and for the same reason (#476 review).
                    bullet_text = original_text
                    if bullet_text:
                        # Use multiline helper so a multi-line entry becomes
                        # one bullet per line, matching L1 and L3 (#572)
                        inserted = self._insert_multiline_as_bullets(
                            section_idx + 1 + bullet_count, bullet_text, entry,
                            add_blank_before=(bullet_count == 0)
                        )
                        bullet_count += inserted

    def _fill_clinical_practice_l3(self, l3_entries: list[dict]) -> None:
        """Fill the L3 Clinical Leadership subsection: table rows, or a
        bullet fallback when no valid table is found.

        Extracted from `_fill_clinical_practice` as a pure relocation -- see
        `_fill_clinical_practice_l1` and the module docstring for why this
        subsection's bullet-fallback text construction is fundamentally
        different from L1/L2's (deliberate, not accidental).
        """
        if not l3_entries:
            return

        section_idx = self._find_paragraph_with_text("Clinical Leadership")
        if section_idx is not None:
            table = self._find_table_after_paragraph(section_idx)

            # Validate the table is actually a leadership table, not a
            # grant/funding table. Only a positive match against the real
            # header is accepted; a confident non-match rejects it, and an
            # AMBIGUOUS header rejects it too -- see `_clinical_header_match`
            # and #841 review (was permissive-accept, #625).
            table_is_valid = False
            if table and table.rows:
                header_match = _clinical_header_match(table.rows[0].cells)
                if header_match is False:
                    if self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical leadership)")
                elif header_match is True:
                    table_is_valid = True

            sorted_entries = sort_entries_reverse_chronological(l3_entries)

            if table and table_is_valid:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1

                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    original_text = entry.get('text', '')

                    # Normalize at extraction (#625 review) so a non-string
                    # field is clean before any string concatenation below.
                    role = _committee_cell_text(
                        fields.get('role') or fields.get('leadership_role') or fields.get('title') or '')
                    institution = _committee_cell_text(
                        fields.get('institution') or fields.get('organization') or '')
                    description = _committee_cell_text(fields.get('description') or fields.get('program') or '')
                    start_date = fields.get('start_date') or ''
                    end_date = fields.get('end_date') or ''
                    dates = format_date_range(start_date, end_date, 'L3') or ''

                    if not role and original_text:
                        parts = original_text.split('|')
                        if len(parts) >= 1:
                            role = parts[0].strip()

                    if role:
                        # Typical leadership table: Year(s) | Role | Description
                        self._add_clinical_table_row(
                            table,
                            three_col=[dates, role, f"{institution}. {description}".strip('. ') if institution or description else ''],
                            two_col=[dates, f"{role} - {institution}" if institution else role],
                            one_col=[f"{dates}: {role}" if dates else role],
                        )
                        self.stats['entries_inserted'] += 1
            else:
                # No valid table found — insert as bullet points
                if self.verbose:
                    print(f"  No Clinical Leadership table found, inserting as bullet points")
                bullet_count = 0
                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    original_text = entry.get('text', '').strip()

                    if _is_structural_label(entry):
                        continue

                    # Normalize at extraction (#625 review): this text feeds
                    # a bullet paragraph, not a table cell, but the same
                    # non-string-field hazard applies to the f-string below.
                    role = _committee_cell_text(
                        fields.get('role') or fields.get('leadership_role') or fields.get('title') or '')
                    institution = _committee_cell_text(
                        fields.get('institution') or fields.get('organization') or '')
                    start_date = fields.get('start_date') or ''
                    end_date = fields.get('end_date') or ''
                    dates = format_date_range(start_date, end_date, 'L3') or ''

                    # Only the FIRST tab-separated fragment can stand in for
                    # a missing role field. Everything after that first tab
                    # used to be dropped on the floor here, so a flattened
                    # "Role<TAB>Institution<TAB>Dates" row rendered as the
                    # bare role and lost the other two parts outright (#476
                    # review). `role_remainder` carries them to the bullet
                    # writer instead, keeping their own '\t'/'\n' structure so
                    # `_bullet_parts` gives each fragment its own bullet.
                    role_remainder = ''
                    if not role and original_text:
                        role, _, role_remainder = original_text.partition('\t')
                        role = role.strip()

                    if role and institution and dates:
                        bullet_text = f"{role}, {institution}, {dates}"
                    elif role and dates:
                        bullet_text = f"{role}, {dates}"
                    elif role:
                        bullet_text = role
                    else:
                        bullet_text = original_text

                    # ... but only the fragments the composed bullet does not
                    # already say. `institution` and `dates` are usually the
                    # SAME text as the fragments after the role -- the readers
                    # flattened one source row and stage 4 extracted its
                    # columns as fields -- so appending them unconditionally
                    # rendered the institution and the dates a second time as
                    # their own bullets (#476 review).
                    new_fragments = [part for part in _bullet_parts(role_remainder)
                                     if _fragment_is_new(part, bullet_text)]
                    if new_fragments:
                        bullet_text = '\t'.join([bullet_text, *new_fragments])
                    elif role_remainder.strip() and self.verbose:
                        logger.info("L3 bullet already carries every fragment after the role")

                    if bullet_text:
                        # Use multiline helper to properly split entries with multiple lines
                        inserted = self._insert_multiline_as_bullets(
                            section_idx + 1 + bullet_count, bullet_text, entry,
                            add_blank_before=(bullet_count == 0)
                        )
                        bullet_count += inserted

    def _insert_multiline_as_bullets(self, insert_idx: int, text: str, entry: dict | None = None,
                                       add_blank_before: bool = False) -> int:
        """Insert multi-part text as separate bullets, one per part.

        This is the STANDARD method for inserting bulleted content. It respects
        the original document's structure - if the source had multiple lines or
        tab-separated parts, each becomes its own bullet. `_bullet_parts` above
        defines what counts as a part.

        Args:
            insert_idx: Index of paragraph to insert before
            text: The text content (may contain newlines and tabs)
            entry: Optional entry dict for adding comments (attached to first bullet only)
            add_blank_before: If True, add a blank line before the first entry

        Returns:
            Number of PARAGRAPHS inserted -- one per bullet, plus the blank
            spacer when `add_blank_before` is set. All three callers use the
            return value to advance their own insertion index, so it has to
            count every paragraph this method added, not only the bullets.
            Counting bullets alone left the first entry's spacer unaccounted
            for, which placed every later entry one paragraph too high and
            interleaved its bullets into the middle of the previous entry's
            (#476 review; found by the end-to-end tests through the real
            template, not by a helper unit test).
        """
        lines = _bullet_parts(text)
        if not lines:
            return 0

        # Insert in reverse order since we're inserting before insert_idx
        inserted_paras = []
        for j, line_text in enumerate(reversed(lines)):
            is_last = (j == len(lines) - 1)  # Last in reversed = first in original
            para = self._insert_bulleted_entry(
                insert_idx, line_text,
                entry if is_last else None,  # Attach entry/comments to first bullet
                add_blank_before=add_blank_before and is_last, list_level=0,
                # Because insertion runs backwards, every later bullet of this
                # entry already exists by the time the first one -- the one the
                # entry rides -- is written. Hand them over with it: the
                # low-coverage overflow check behind `_add_entry_comments`
                # weighs the entry's rendered text against its source text, and
                # measuring only the first bullet scored a fully-rendered
                # N-part entry as 1/N covered, so `_route_overflow_entries`
                # re-emitted the entire entry below bullets that already
                # carried it (#476 review; +36 duplicated word tokens on one
                # corpus CV, invisible to a loss-only census).
                entry_sibling_paras=inserted_paras if is_last else None,
            )
            if para is not None:
                inserted_paras.append(para)

        return len(lines) + (1 if add_blank_before else 0)
