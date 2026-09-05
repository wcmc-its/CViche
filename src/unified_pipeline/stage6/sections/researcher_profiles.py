"""Section S0: researcher profile identifiers (#398).

ORCID, Google Scholar, Scopus, ResearcherID -- the persistent identifiers a CV
lists near its publications. The WCM template has no heading for them, so this
is the one section that writes CONTENT WITHOUT A HEADER: the bullets are placed
directly above "Peer-reviewed Research Articles", where a reader looking for
publication identity will already be, with a blank line either side standing in
for the heading the template does not provide.

Placement is by `insert_paragraph_before` against that anchor, which means
everything is inserted at the same index and each new bullet pushes the previous
one down. The entry list is therefore walked in REVERSE so the rendered order
matches the source. The two blank paragraphs are added for the same reason at
opposite ends of the loop: the one after the loop body ends up above the
bullets.

The anchor falls back to the "BIBLIOGRAPHY" heading, and if neither exists the
section returns rather than appending the identifiers somewhere arbitrary.

Text is passed through `_strip_taxonomy_code` before `_clean_inline_tabs`,
because these entries arrive from the classifier still carrying their "S0:"
prefix more often than most -- an identifier line is short enough that the code
is a visible fraction of it.
"""

from ..formatting import _set_font
from ..normalization import _clean_inline_tabs, _strip_taxonomy_code


class ResearcherProfilesSection:
    """Section S0 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_researcher_profiles(self, s0_entries: list[dict]):
        """Fill S0 researcher profile info (ORCID, Google Scholar, etc).

        Inserts right before "Peer-reviewed Research Articles" without a header.
        Content is formatted as a bulleted list.
        """
        if not s0_entries:
            return

        if self.verbose:
            print(f"Filling Researcher Profiles ({len(s0_entries)} entries)...")

        # Find "Peer-reviewed Research Articles" to insert directly above it
        peer_reviewed_idx = self._find_paragraph_with_text("Peer-reviewed Research Articles")
        if peer_reviewed_idx is None:
            # Fallback to BIBLIOGRAPHY
            peer_reviewed_idx = self._find_paragraph_with_text("BIBLIOGRAPHY")
        if peer_reviewed_idx is None:
            return

        # Insert a blank line before "Peer-reviewed" first
        self.doc.paragraphs[peer_reviewed_idx].insert_paragraph_before("")

        # Insert entries in REVERSE order so they appear in correct order
        # Format as bulleted list
        for entry in reversed(s0_entries):
            text = entry.get('text', '').strip()
            entry_para = self.doc.paragraphs[peer_reviewed_idx].insert_paragraph_before("")
            run = entry_para.add_run(_clean_inline_tabs(_strip_taxonomy_code(text)))
            _set_font(run)
            self._apply_list_bullet(entry_para, level=0)
            self.stats['entries_inserted'] += 1

        # Add a blank line before the S0 content
        self.doc.paragraphs[peer_reviewed_idx].insert_paragraph_before("")
