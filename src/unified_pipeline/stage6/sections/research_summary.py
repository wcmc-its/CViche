"""Section M1: the research summary (#398).

One paragraph, and the only section whose content the pipeline WROTE rather than
relocated. Stage 4.5 synthesizes a biosketch-style summary from the CV; this
writer places it under the RESEARCH ACTIVITIES heading as a tracked insertion
attributed to "LLM Research Summary", so nothing generated is ever
indistinguishable from the author's own words.

The paragraph cannot be appended: `add_paragraph` puts it at the end of the
document, so it is created there and then moved into the body element list
immediately after the heading, with a blank paragraph inserted first to keep the
spacing the template uses elsewhere. If the heading is not in the body list --
it can be inside a table or a content control -- the writer returns rather than
guessing a position.

The RETURN VALUE is load-bearing. Under a 50-character floor, or with no
stage-4.5 output at all, the section renders nothing; before #317 the M1 entries
that would have fed a summary were dropped on that path, so C0ZGFW lost its
research content entirely. `generate` now uses the False return to route those
entries to the appendix instead. `tests/test_m1_appendix_fallback.py` pins each
of the three False cases, and `tests/test_stage6_import_surface.py` pins the
name on the class surface; the mixin keeps it resolving through the MRO, which
is what that guard checks.

The floor is 50 characters rather than a word count because the failure it
catches is a stub -- an empty string, a header echo, a refusal -- not a short
but real summary.
"""
from typing import Dict, Optional

try:
    from docx.shared import Pt
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc


class ResearchSummarySection:
    """Section M1 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_research_summary(self, research_summary_data: dict | None) -> bool:
        """Fill Research Summary section from Stage 4.5 output.

        Inserts a RESEARCH SUMMARY section before RESEARCH SUPPORT with
        the biosketch-style research summary paragraph.

        Args:
            research_summary_data: Stage 4.5 standalone output containing:
                - research_summary.text: The generated summary
                - research_summary.generation_method: "llm_generated" or "existing_content"
                - research_summary.word_count: Word count of summary

        Returns:
            True if a summary paragraph was rendered, False otherwise. Callers use
            this to route M1 entries to the appendix when the summary is absent
            (#317) instead of dropping them.
        """
        if not research_summary_data:
            if self.verbose:
                print("Skipping Research Summary section (no Stage 4.5 output)")
            return False

        # Extract summary from Stage 4.5 structure
        summary_info = research_summary_data.get('research_summary', {})
        summary_text = summary_info.get('text', '')

        if not summary_text or len(summary_text.strip()) < 50:
            if self.verbose:
                print("Skipping Research Summary section (no substantive content)")
            return False

        word_count = summary_info.get('word_count', len(summary_text.split()))
        generation_method = summary_info.get('generation_method', 'unknown')

        if self.verbose:
            print(f"Filling Research Summary ({word_count} words, {generation_method})...")

        # Find RESEARCH ACTIVITIES section (M1) to insert under. The finder
        # lowercases both sides, so casing of the needle carries no information.
        activities_idx = self._find_paragraph_with_text("RESEARCH ACTIVITIES")
        if activities_idx is None:
            if self.verbose:
                print("  Warning: Could not find 'RESEARCH ACTIVITIES' section")
            return False

        # Get the paragraph element to insert after
        activities_para = self.doc.paragraphs[activities_idx]
        body = self.doc.element.body
        body_elements = list(body)

        try:
            insert_idx = body_elements.index(activities_para._element) + 1  # Insert AFTER header
        except ValueError:
            return False

        # Add blank line after RESEARCH ACTIVITIES header
        blank_para = self.doc.add_paragraph()
        blank_para.paragraph_format.space_after = Pt(6)
        body.insert(insert_idx, blank_para._element)
        insert_idx += 1

        # Create summary paragraph (no new header - goes under existing RESEARCH ACTIVITIES)
        # Use track changes since this is LLM-generated content, not from the original CV
        summary_para = self.doc.add_paragraph()
        self._add_track_change_insertion(summary_para, summary_text.strip(), author="LLM Research Summary")
        summary_para.paragraph_format.space_after = Pt(12)

        # Move to correct position (after blank line)
        body.insert(insert_idx, summary_para._element)

        self.stats['entries_inserted'] += 1
        return True
