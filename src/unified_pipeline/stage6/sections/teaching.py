"""Section K: teaching activities, K1 through K5 (#398).

Five subsections, and the writer's only structural job is finding each one:

    K1  Didactic teaching
    K2  Clinical teaching
    K3  Administrative teaching
    K4  Continuing education and professional education
    K5  Other education / outreach activities

Each K-code carries a LIST of candidate header strings rather than one, because
the template wording has drifted across revisions ("Clinical teaching" vs
"bedside teaching", "outreach activities" vs "community education or patient").
A code whose headers all miss falls back to the parent "EDUCATIONAL
CONTRIBUTIONS" heading rather than dropping its entries.

Entries render as a FLAT bulleted list under the template's own header. The
source CV's sub-headings are deliberately not carried over: the WCM subsections
already are the taxonomy, and re-emitting the original hierarchy produced two
competing levels of grouping.

Insertion runs backwards. `_insert_bulleted_entry` inserts BEFORE the index it
is given, so the list is walked in reverse and each entry lands above the one
inserted before it, leaving reverse-chronological order on the page. That is
also why `is_first_visible` is computed as the LAST index of the reversed list
-- the entry that ends up visually first is the one that gets the blank line
above it.

WHAT HAPPENS WHEN TWO CODES SHARE ONE HEADING
Every K-code resolves its heading independently, so when several codes all miss
their own wording they all fall back to the SAME "EDUCATIONAL CONTRIBUTIONS"
paragraph. That is intended -- losing the entries would be worse -- and the
resulting page order is fixed, not incidental, so it is written down here
rather than left to be rediscovered:

- Each code's entries stay CONTIGUOUS. Two codes are never interleaved: a
  code's whole block is inserted before the next code is looked up.
- Within a block, order is reverse-chronological, newest first, exactly as
  under a dedicated heading.
- Blocks stack in REVERSE code order. Codes are processed K1..K5 in
  `TEACHING_SECTION_HEADERS` order and every block is inserted directly under
  the shared heading, pushing the previous block down, so the page reads K5,
  K4, K3, K2, K1 top to bottom.
- Each block still gets its own blank line above it, because
  `is_first_visible` is computed per code.

Against the shipped template this is dormant: all five K headings exist
(`key_files/wcm_cv_template_faculty_october_2022_final.docx` carries them at
paragraphs 85/87/89/91/93, under EDUCATIONAL CONTRIBUTIONS at 82), so no code
takes the fallback. It describes what a template revision would get.

DELIMITER CONTRACT
`|` is NEVER an item separator in this section. It joins fields inside one item
("Role | Institution"), so

    "Role A | Institution A\\tRole B | Institution B"

reconstructs as TWO items, not four. Newlines always separate items. Tabs
separate items only in Stage 5c's own `formatted_text` (`_item_parts`).

The reason a separator can be left in place is that `_insert_bulleted_entry`
already renders both of them readably: `_clean_inline_tabs`
(`normalization/text.py:258`) rejoins a residual "|" with " — " and a residual
tab as "Label: Value — Value". So in RAW source text a tab marks the CELLS of
one table row, and leaving it alone yields one correctly punctuated bullet;
splitting on it would replace that bullet with fragments. The farm says the
same thing in counts: 456 of 722 K entries are a single record shaped
"Title\\tDate\\tDescription", and tab-splitting the raw fallback would turn 19
entries into 41 bullets, 16 of them a bare date. Raw text therefore keeps the
newline-only split (`entry_lines`) on both of the paths that read it.

WHERE THE TEXT COMES FROM
`_teaching_entry_lines` is the whole reconstruction, as a pure function: fields
in, the bullet strings out, no Word object touched. It picks between three
sources of text, in order:

- `formatted_text` from stage 5c, when the raw entry is a single item. ISO dates
  the LLM left behind are normalized first, and markdown is stripped, since Word
  runs have no markdown. A `formatted_text` that strips down to nothing is not
  bulleted blank; the raw line is used instead, and the entry is reported when
  there is no raw line either.
- the RAW lines, when the raw entry has several of them. Stage 5c routinely
  merges a multi-item teaching block into one sentence, so the original line
  breaks are the more faithful record; only one of the bullets carries the
  entry's comments, so an entry is not annotated N times. The exception is ONE
  table row whose cells wrap over paragraphs (#987): the extractor joins a
  cell's own paragraphs with a newline, so that row's raw text has several
  lines although it is one course, and those lines are not items. Such a row
  is rejoined into ONE bullet of its own raw text (every cell's paragraphs
  joined with a space; stage 5c's text is not used, it drops words); see
  `wrapped_row_text`.
- the extracted fields (course code, title, institution, role), when stage 5c
  produced nothing at all. Both code and title arrive as lists often enough that
  each is joined before use.

`_insert_teaching_entry` keeps only what is not text: the two content gates, the
warning for an entry that reconstructs to nothing, and the insertion order.

Structural labels and orphan fragments are dropped at the top -- a bare "Course
Title" or a dangling continuation line is source-table furniture, not a
teaching activity.
"""
import logging
from typing import TypedDict

from ..formatting import normalize_iso_dates_in_text
from ..normalization import _strip_markdown_for_word
from ..parsing import _is_orphan_fragment, _is_structural_label
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines, wrapped_row_text

logger = logging.getLogger(__name__)


class _TeachingFields(TypedDict, total=False):
    """`extracted_fields` as section K actually reads it (review thread
    3927100368), in place of an unparameterized `Dict` that told a reader
    nothing about which keys exist or what they hold.

    `total=False` because stage 4's JSON has no schema enforcing any of these
    keys: this documents the contract the renderer relies on, it is not a
    validation layer -- see `normalization/fields.py` for why this codebase
    coerces at read time instead of rejecting.

    `course_code` and `course_title` are `str | list[str]` because extraction
    emits both shapes for the same field; the `'; '.join(...)` normalization in
    `_teaching_entry_lines` exists for the list case, and this union is the
    fact that makes it necessary rather than defensive noise.
    """
    formatted_text: str
    course_code: str | list[str]
    course_title: str | list[str]
    institution: str
    role: str


class _TeachingEntry(TypedDict, total=False):
    """One stage-4/5c entry as the K writers read it. Same `total=False`
    reasoning as `_TeachingFields`."""
    text: str
    extracted_fields: _TeachingFields
    taxonomy_code: str
    element_type: str
    element_idx_start: int
    element_idx_end: int


# K-code to candidate header strings, tried in order (see module docstring and
# docs/CODING_STANDARDS.md §8.2). K1-K5: didactic, clinical, administrative,
# continuing education, and outreach teaching, respectively.
TEACHING_SECTION_HEADERS: dict[str, list[str]] = {
    'K1': ['Didactic teaching', 'Didactic'],
    'K2': ['Clinical teaching', 'bedside teaching'],
    'K3': ['Administrative teaching', 'leadership role'],
    'K4': ['Continuing education', 'professional education'],
    'K5': ['outreach activities', 'Other education/outreach', 'community education or patient'],
}

# The source CV's own table column labels. A raw line that is nothing but one of
# these is furniture, not a teaching activity (#574). One frozenset rather than
# a list literal rebuilt per line: it is this section's vocabulary for "column
# header", and it now has one definition (§8.2).
_STRUCTURAL_COLUMN_LABELS = frozenset({'title', 'institution', 'dates', 'role'})

# A raw line at least this long that also carries a ';' is a run-together list
# of activities rather than one activity; below it, a ';' is punctuation inside
# a single item. Named because the bare 100 said nothing about which of the two
# it was guarding against (§8.2).
_SEMICOLON_SPLIT_MIN_CHARS = 100


def _item_parts(text: str | None) -> list[str]:
    """Stripped, non-empty ITEMS of `text`: newlines and tabs separate items,
    `|` does not (#476, review thread 3927100368).

    The module docstring's delimiter contract in one function, so the rule is
    testable without a Word document and cannot drift between call sites. Note
    what this is NOT: `entry_fragments` splits on `|` as well, and swapping it
    in here would silently turn "Role | Institution" into two items instead of
    the one bullet `_clean_inline_tabs` renders it as ("Role — Institution").

    Its one caller is `_teaching_entry_lines`' Stage-5c-prose branch (the
    second `if formatted_text:`), which runs only when `original_text` is
    empty -- Stage 5c's own prose carries no source-table furniture, so a tab
    in it is a fused item rather than a cell boundary. Raw source text is
    deliberately split by `entry_lines` instead; the module docstring says
    why, with the farm counts.
    """
    return [part.strip()
            for line in str(text or '').split('\n')
            for part in line.split('\t')
            if part.strip()]



def _teaching_entry_lines(fields: _TeachingFields, original_text: str,
                          row_text: str | None = None) -> list[str]:
    """The bullet strings one teaching entry renders as, in page order.

    Pure: no document, no logging, no insertion -- extracted out of
    `_insert_teaching_entry` (review thread 3927100368) so the delimiter rule
    and the three-way source fallback are testable on their own, and so the
    renderer is left with one insertion loop instead of seven.

    An empty list means the entry reconstructs to nothing at all; the caller
    logs that (§5.4 -- it is recorded, never swallowed). It is never a list
    holding one empty string: a `formatted_text` of nothing but whitespace or
    markup used to be bulleted blank, which both put an empty list paragraph
    on the page AND discarded whatever raw line the entry still carried, so
    such a `formatted_text` now falls through to the raw line and, when there
    is none, to the empty list the caller reports.

    `row_text` (`wrapped_row_text`, #987) is the raw text of a row whose raw
    lines are one course's wrapped cells rather than several items: it is the
    one bullet in their place. Stage 5c's text is NOT used for it, because
    5c drops words the raw cells carry. With no `formatted_text` the field
    fallback below already applies, as it does for every entry.
    """
    formatted_text = fields.get('formatted_text', '') or ''
    if formatted_text:
        # Normalize any raw ISO dates the LLM left in formatted text
        formatted_text = normalize_iso_dates_in_text(formatted_text)

    if formatted_text and original_text:
        # Multi-item entry: use original lines (Stage 5c may have over-combined).
        # Newline-only on purpose -- see the module's delimiter contract.
        original_lines = entry_lines(original_text)
        if len(original_lines) > 1:
            return [row_text] if row_text else original_lines
        stripped = _strip_markdown_for_word(formatted_text, preserve_newlines=True)
        if stripped.strip():
            return [stripped]
        # Stage 5c sent a formatted_text with no words in it ("   ", "**  **").
        # Bulleting it renders an empty list paragraph and throws away the raw
        # line this entry still has, so take the raw line instead; when the raw
        # text was blank too this is [] and the caller warns (#476).
        return original_lines

    if formatted_text:
        parts = _item_parts(_strip_markdown_for_word(formatted_text, preserve_newlines=True))
        if len(parts) > 1:
            return ['. '.join(parts)]
        # Same rule with no raw text to fall back to: `parts[:1]` is [] when the
        # formatted text reduced to nothing, so the caller reports the entry
        # rather than the renderer emitting a blank bullet for it.
        return parts[:1]

    # No Stage 5c formatting - fall back to building text from fields
    course_code = fields.get('course_code', '')
    course_title = fields.get('course_title', '')
    institution = fields.get('institution', '')
    role = fields.get('role', '')

    if isinstance(course_title, list):
        course_title = '; '.join(course_title)
    if isinstance(course_code, list):
        course_code = '; '.join(course_code)

    if course_title:
        text = f"{course_code}: {course_title}" if course_code else course_title
        if institution and institution not in text:
            text += f", {institution}"
        if role and role not in text:
            text += f" ({role})"
        return [text]

    lines: list[str] = []
    for line in original_text.split('\n'):
        line = line.strip()
        if not line or line.lower() in _STRUCTURAL_COLUMN_LABELS:
            continue
        if ';' in line and len(line) > _SEMICOLON_SPLIT_MIN_CHARS:
            lines.extend([item.strip() for item in line.split(';') if item.strip()])
        else:
            lines.append(line)

    if lines:
        return lines

    # Every line was a bare column label ("Title"/"Institution"/etc.) -- the
    # filter above left nothing to bullet. Institution and role are right there
    # in the extracted fields even though this branch only exists because both
    # formatted_text and course_title came back empty, so fall back to those
    # before giving up on the entry (#574).
    text = ', '.join(part for part in (institution, role) if part)
    return [text] if text else []


class TeachingSection:
    """Section K writers, mixed into `WCMTemplateGenerator`."""

    def _fill_teaching(self, entries_by_code: dict[str, list[_TeachingEntry]]) -> None:
        """Fill K. TEACHING ACTIVITIES section.

        Routes K-codes to their appropriate WCM subsections:
        - K1 (didactic) -> "Didactic teaching"
        - K2 (clinical) -> "Clinical teaching"
        - K3 (administrative) -> "Administrative teaching"
        - K4 (CME) -> "Continuing education and professional education"
        - K5 (community) -> "Other education/outreach activities"

        Entries are inserted as a flat chronological list under each K-code section.
        The WCM template provides the structure; Stage 5c handles per-entry formatting.
        Original CV hierarchy labels are not carried over. When two codes share
        the fallback heading, the module docstring states the order they land in.
        """
        k_section_map = TEACHING_SECTION_HEADERS

        # Count total entries
        total_entries = sum(len(entries_by_code.get(code, [])) for code in k_section_map.keys())
        if total_entries == 0:
            return

        logger.info("Filling Teaching (%d entries)...", total_entries)

        # Fill each K-code section separately
        for code, search_texts in k_section_map.items():
            entries = entries_by_code.get(code, [])
            if not entries:
                continue

            # Find the appropriate section header
            section_idx = None
            for search_text in search_texts:
                section_idx = self._find_paragraph_with_text(search_text)
                if section_idx is not None:
                    break

            if section_idx is None:
                # Fall back to general teaching section. Several codes can land
                # here at once; the module docstring pins the resulting order.
                section_idx = self._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
                if section_idx is None:
                    logger.warning("no template heading for teaching code %s; "
                                   "%d entries not rendered", code, len(entries))
                    continue

            # Flat list: sort chronologically and insert without original CV sub-headers.
            # The WCM template's own K-code sections (Didactic, Clinical, Administrative,
            # CME, Community) provide the structure; Stage 5c handles per-entry formatting.
            sorted_entries = sort_entries_reverse_chronological(entries)
            reversed_entries = list(reversed(sorted_entries))

            for i, entry in enumerate(reversed_entries):
                is_first_in_section = (i == len(reversed_entries) - 1)
                self._insert_teaching_entry(section_idx + 1, entry,
                                            is_first_visible=is_first_in_section)

    def _insert_teaching_entry(self, insert_idx: int, entry: _TeachingEntry,
                               is_first_visible: bool = False) -> None:
        """Insert a single teaching entry as bulleted items.

        What text to render is `_teaching_entry_lines`' job (Stage 5c formatted
        text, raw lines, or structured fields). What is left here is the two
        content gates, the warning for an entry that reconstructs to nothing,
        and the insertion order.
        """
        # Skip structural labels from source CV
        if _is_structural_label(entry):
            return

        fields = entry.get('extracted_fields', {}) or {}
        formatted_text = fields.get('formatted_text', '') or ''
        original_text = entry.get('text', '') or ''

        if _is_orphan_fragment(fields, formatted_text, original_text):
            return

        lines = _teaching_entry_lines(fields, original_text,
                                      row_text=wrapped_row_text(entry))
        if not lines:
            logger.warning("teaching entry produced no renderable line (%s): %r",
                           entry.get('taxonomy_code'), original_text[:80])
            return

        # `_insert_bulleted_entry` inserts BEFORE insert_idx, so walking the
        # list backwards leaves it in source order on the page. Exactly one
        # bullet carries `entry` (the last in source order, inserted first), so
        # a multi-line entry is annotated once rather than N times, and the
        # blank line goes above the bullet that ends up visually first.
        for j, line_text in enumerate(reversed(lines)):
            self._insert_bulleted_entry(
                insert_idx, line_text, entry if j == 0 else None,
                add_blank_before=is_first_visible and j == len(lines) - 1,
                list_level=0
            )
