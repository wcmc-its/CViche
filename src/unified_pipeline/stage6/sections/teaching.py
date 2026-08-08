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

`_insert_teaching_entry` picks between three sources of text, in order:

- `formatted_text` from stage 5c, when the raw entry is a single item. ISO dates
  the LLM left behind are normalized first, and markdown is stripped, since Word
  runs have no markdown.
- the RAW lines, when the raw entry has several of them. Stage 5c routinely
  merges a multi-item teaching block into one sentence, so the original line
  breaks are the more faithful record; only the first line carries the entry's
  comments, so an entry is not annotated N times.
- the extracted fields (course code, title, institution, role), when stage 5c
  produced nothing at all. Both code and title arrive as lists often enough that
  each is joined before use.

Structural labels and orphan fragments are dropped at the top -- a bare "Course
Title" or a dangling continuation line is source-table furniture, not a
teaching activity.
"""
from typing import Dict, List

from ..formatting import normalize_iso_dates_in_text
from ..normalization import _strip_markdown_for_word
from ..parsing import _is_orphan_fragment, _is_structural_label
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines


class TeachingSection:
    """Section K writers, mixed into `WCMTemplateGenerator`."""

    def _fill_teaching(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill K. TEACHING ACTIVITIES section.

        Routes K-codes to their appropriate WCM subsections:
        - K1 (didactic) -> "Didactic teaching"
        - K2 (clinical) -> "Clinical teaching"
        - K3 (administrative) -> "Administrative teaching"
        - K4 (CME) -> "Continuing education and professional education"
        - K5 (community) -> "Other education/outreach activities"

        Entries are inserted as a flat chronological list under each K-code section.
        The WCM template provides the structure; Stage 5c handles per-entry formatting.
        Original CV hierarchy labels are not carried over.
        """
        # K-code to candidate header strings, tried in order (see module docstring)
        k_section_map = {
            'K1': ['Didactic teaching', 'Didactic'],
            'K2': ['Clinical teaching', 'bedside teaching'],
            'K3': ['Administrative teaching', 'leadership role'],
            'K4': ['Continuing education', 'professional education'],
            'K5': ['outreach activities', 'Other education/outreach', 'community education or patient'],
        }

        # Count total entries
        total_entries = sum(len(entries_by_code.get(code, [])) for code in k_section_map.keys())
        if total_entries == 0:
            return

        if self.verbose:
            print(f"Filling Teaching ({total_entries} entries)...")

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
                # Fall back to general teaching section
                section_idx = self._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
                if section_idx is None:
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

    def _insert_teaching_entry(self, insert_idx: int, entry: Dict, is_first_visible: bool = False):
        """Insert a single teaching entry as a bulleted item.

        Handles Stage 5c formatted text (with track changes), structured fields,
        and raw text fallback.
        """
        # Skip structural labels from source CV
        if _is_structural_label(entry):
            return

        fields = entry.get('extracted_fields', {}) or {}
        formatted_text = fields.get('formatted_text', '')
        original_text = entry.get('text', '')

        if _is_orphan_fragment(fields, formatted_text, original_text):
            return

        # Normalize any raw ISO dates the LLM left in formatted text
        if formatted_text:
            formatted_text = normalize_iso_dates_in_text(formatted_text)

        if formatted_text and original_text:
            # Check if original has multiple distinct items (newline-separated list)
            original_lines = entry_lines(original_text)

            if len(original_lines) > 1:
                # Multi-item entry: use original lines (Stage 5c may have over-combined)
                # Insert in reverse order since we're inserting before insert_idx
                for j, line_text in enumerate(reversed(original_lines)):
                    if not line_text:
                        continue
                    add_blank = is_first_visible and (j == len(original_lines) - 1)
                    self._insert_bulleted_entry(
                        insert_idx, line_text, entry if j == 0 else None,
                        add_blank_before=add_blank, list_level=0
                    )
            else:
                # Single item: use formatted_text
                new_text = _strip_markdown_for_word(formatted_text, preserve_newlines=True)
                self._insert_bulleted_entry(
                    insert_idx, new_text, entry,
                    add_blank_before=is_first_visible, list_level=0
                )

        elif formatted_text:
            new_text = _strip_markdown_for_word(formatted_text, preserve_newlines=True)
            lines = entry_lines(new_text)
            combined_text = '. '.join(lines) if len(lines) > 1 else (lines[0] if lines else '')
            self._insert_bulleted_entry(
                insert_idx, combined_text, entry,
                add_blank_before=is_first_visible, list_level=0
            )

        else:
            # No Stage 5c formatting - fall back to building text from fields
            course_code = fields.get('course_code', '')
            course_title = fields.get('course_title', '')
            institution = fields.get('institution', '')
            role = fields.get('role', '')

            if isinstance(course_title, list):
                course_title = '; '.join(course_title)
            if isinstance(course_code, list):
                course_code = '; '.join(course_code)

            if course_code and course_title:
                text = f"{course_code}: {course_title}"
                if institution and institution not in text:
                    text += f", {institution}"
                if role and role not in text:
                    text += f" ({role})"
                self._insert_bulleted_entry(insert_idx, text, entry, add_blank_before=is_first_visible, list_level=0)
            elif course_title:
                text = course_title
                if institution and institution not in text:
                    text += f", {institution}"
                if role and role not in text:
                    text += f" ({role})"
                self._insert_bulleted_entry(insert_idx, text, entry, add_blank_before=is_first_visible, list_level=0)
            else:
                lines = []
                for line in original_text.split('\n'):
                    line = line.strip()
                    if not line or line.lower() in ['title', 'institution', 'dates', 'role']:
                        continue
                    if ';' in line and len(line) > 100:
                        lines.extend([item.strip() for item in line.split(';') if item.strip()])
                    else:
                        lines.append(line)

                for j, line_text in enumerate(reversed(lines)):
                    if not line_text:
                        continue
                    add_blank = is_first_visible and (j == len(lines) - 1)
                    self._insert_bulleted_entry(insert_idx, line_text, entry if j == 0 else None, add_blank_before=add_blank, list_level=0)
