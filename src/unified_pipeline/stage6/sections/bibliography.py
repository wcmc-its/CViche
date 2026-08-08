"""Section S: bibliography -- publications S1 through S9 (#398).

Nine subsections, one loop. Each taxonomy code maps to a template header string
that must match the official WCM template exactly (they are matched by text, not
by index), numbering restarts at 1 under every header, and entries are inserted
before the paragraph that follows the header so the list builds downward.

Two things happen to the entry list before it is numbered:

- `sort_entries_reverse_chronological` puts newest first.
- `split_fused_citation_entries` (#208) undoes upstream fusion. Several
  citations that arrived collapsed into one entry would otherwise render as a
  single number with `<w:br/>` continuation lines -- visually a list, but with
  one citation numbered and the rest not.

The two `_add_citation_with_bold_author*` helpers exist because the CV owner's
name has to be bold inside the citation, and Word offers no way to say that
without splitting the text into runs. They are the same algorithm twice over
different substrates:

- `_add_citation_with_bold_author` writes plain runs via python-docx, and is
  what a non-enriched citation uses.
- `_add_citation_with_bold_author_as_insertion` builds the same runs as raw
  `w:r` elements inside a `w:ins`, because an enriched citation is rendered as a
  tracked insertion paired with a deletion of the original text, and python-docx
  cannot add a run *into* a revision element. It falls back to the plain path if
  the XML build raises, so an enrichment can never cost the citation itself.

Author bolding targets `target_name` from `_format_citation`, falling back to
the CV owner's last name -- taken from `cv_owner`, or recovered from the
document uid when the pipeline never resolved an owner.
"""
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
except ImportError:  # pragma: no cover - mirrors stage_6_word_template
    print("Error: python-docx not installed. Install with: pip install python-docx lxml")
    sys.exit(1)

from ..formatting import _format_citation, _set_font
from ..normalization import split_fused_citation_entries
from ..parsing import _extract_last_name_from_uid
from ..sorting import sort_entries_reverse_chronological


class BibliographySection:
    """Section S writers, mixed into `WCMTemplateGenerator`."""

    def _fill_bibliography(self, entries_by_code: Dict[str, List[Dict]], cv_owner: Dict, document_uid: str = ''):
        """Fill bibliography section with formatted citations.

        Uses the official WCM template section headers and restarts numbering
        within each subsection.
        """
        # Map taxonomy codes to WCM template section header text
        # These must match the exact text in the official WCM template
        section_headers = {
            'S1': 'Peer-reviewed Research Articles:',
            'S2': 'Reviews and Editorials:',
            'S3': 'Books:',
            'S4': 'Chapters:',
            'S5': 'Non-peer-reviewed Research Publications:',
            'S6': 'Case Reports',
            'S7': 'In review',
            'S8': 'Abstracts',
            'S9': 'Other (media, podcasts, etc.):',
        }

        # Get CV owner last name for fallback bolding
        cv_owner_last_name = ''
        if cv_owner and cv_owner.get('last_name'):
            cv_owner_last_name = cv_owner['last_name']
        elif document_uid:
            # Fallback: extract from document_uid (e.g., "2015_Wende" -> "Wende")
            cv_owner_last_name = _extract_last_name_from_uid(document_uid)

        # Count total publications
        pub_codes = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']
        total_pubs = sum(len(entries_by_code.get(code, [])) for code in pub_codes)

        if self.verbose:
            print(f"Filling Bibliography ({total_pubs} publications)...")

        # Process each publication type
        for code in pub_codes:
            pubs = entries_by_code.get(code, [])
            if not pubs:
                continue

            header_text = section_headers.get(code, '')
            if not header_text:
                continue

            # Find the section header in the template
            section_idx = self._find_paragraph_with_text(header_text)
            if section_idx is None:
                if self.verbose:
                    print(f"  Warning: Could not find section header '{header_text}'")
                continue

            if self.verbose:
                print(f"  {code}: {len(pubs)} entries -> '{header_text[:30]}...'")

            # Sort reverse chronologically (most recent first)
            pubs_sorted = sort_entries_reverse_chronological(pubs)
            # Un-fuse any entry that collapsed several citations into one
            # (#208), so each is numbered instead of rendering as unnumbered
            # <w:br/> continuation lines under one number.
            pubs_sorted = split_fused_citation_entries(pubs_sorted)

            # Insert after the section header - numbering restarts at 1 for each section
            insert_idx = section_idx + 1

            # Add blank line before first citation in each section
            if pubs_sorted:
                self.doc.paragraphs[insert_idx].insert_paragraph_before("")
                insert_idx += 1

            for citation_num, pub in enumerate(pubs_sorted, start=1):
                citation_text, target_name, enriched_fields = _format_citation(pub, citation_num)
                original_text = pub.get('text', '')
                enrichment_status = pub.get('enrichment_status', '')

                # Insert citation paragraph before the next element
                para = self.doc.paragraphs[insert_idx].insert_paragraph_before("")

                # Check if this entry was enriched - if so, use track changes
                if enrichment_status == 'enriched' and original_text:
                    # Show original as deleted, enriched citation as inserted
                    # First add the deletion (original text)
                    self._add_track_change_deletion(para, original_text, author="PubMed Enrichment")
                    # Then add the insertion (enriched citation) with bold author
                    self._add_citation_with_bold_author_as_insertion(
                        para, citation_text, target_name, cv_owner_last_name,
                        author="PubMed Enrichment"
                    )
                else:
                    # No enrichment - add citation normally with target name bolded
                    self._add_citation_with_bold_author(para, citation_text, target_name, cv_owner_last_name)

                # Add comment explaining enrichment (no inline text)
                if enriched_fields:
                    enrichment_source = pub.get('enrichment_source', '')
                    if enrichment_source:
                        comment = f"Data enriched from {enrichment_source.upper()}. Fields updated: {', '.join(enriched_fields)}"
                        self._add_word_comment(para, comment, author="PubMed Enrichment")

                # Add comments from upstream pipeline processes
                self._add_entry_comments(para, pub)

                insert_idx += 1
                self.stats['entries_inserted'] += 1

    def _add_citation_with_bold_author(self, para: Paragraph, citation: str, target_name: Optional[str], cv_owner_last_name: str = ''):
        """
        Add citation text to paragraph, bolding the target author name.

        If target_name is not found, falls back to searching for cv_owner_last_name.
        """
        para.clear()

        # Determine what to bold
        name_to_bold = None
        if target_name and target_name in citation:
            name_to_bold = target_name
        elif cv_owner_last_name:
            # Fallback: find cv_owner's name in the citation using regex
            # Look for patterns like "Wende ME", "Wende, M", "Wende M.", etc.
            pattern = rf'\b{re.escape(cv_owner_last_name)}\s*[A-Z]{{0,3}}\.?\b'
            match = re.search(pattern, citation, re.IGNORECASE)
            if match:
                name_to_bold = match.group(0).rstrip('.,')

        if name_to_bold and name_to_bold in citation:
            # Split around target name
            idx = citation.index(name_to_bold)
            before = citation[:idx]
            after = citation[idx + len(name_to_bold):]

            # Add before (normal)
            if before:
                run1 = para.add_run(before)
                _set_font(run1)

            # Add target name (bold)
            run2 = para.add_run(name_to_bold)
            _set_font(run2, bold=True)
            self.stats['target_names_bolded'] += 1

            # Add after (normal)
            if after:
                run3 = para.add_run(after)
                _set_font(run3)
        else:
            # No target name to bold
            run = para.add_run(citation)
            _set_font(run)

    def _add_citation_with_bold_author_as_insertion(self, para: Paragraph, citation: str,
                                                     target_name: Optional[str], cv_owner_last_name: str = '',
                                                     author: str = "PubMed Enrichment"):
        """
        Add citation as a track change insertion, bolding the target author name.

        This creates proper Word track change structure with w:ins element,
        and includes bold formatting for the target author within the insertion.
        """
        # Issue #153: when track changes are disabled, render the citation as a
        # plain (non-tracked) paragraph with the target author bolded.
        if not self.emit_track_changes:
            self._add_citation_with_bold_author(para, citation, target_name, cv_owner_last_name)
            return
        try:
            revision_id = str(self._revision_id)
            self._revision_id += 1

            # Create the insertion element
            ins = OxmlElement('w:ins')
            ins.set(qn('w:id'), revision_id)
            ins.set(qn('w:author'), author)
            ins.set(qn('w:date'), datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'))

            # Determine what to bold
            name_to_bold = None
            if target_name and target_name in citation:
                name_to_bold = target_name
            elif cv_owner_last_name:
                pattern = rf'\b{re.escape(cv_owner_last_name)}\s*[A-Z]{{0,3}}\.?\b'
                match = re.search(pattern, citation, re.IGNORECASE)
                if match:
                    name_to_bold = match.group(0).rstrip('.,')

            def create_run_element(text: str, bold: bool = False) -> Any:
                """Create a w:r element with text and optional bold."""
                run_elem = OxmlElement('w:r')
                rPr = OxmlElement('w:rPr')
                rFonts = OxmlElement('w:rFonts')
                rFonts.set(qn('w:ascii'), 'Arial')
                rFonts.set(qn('w:hAnsi'), 'Arial')
                rPr.append(rFonts)
                sz = OxmlElement('w:sz')
                sz.set(qn('w:val'), '22')  # 11pt
                rPr.append(sz)
                if bold:
                    b = OxmlElement('w:b')
                    rPr.append(b)
                run_elem.append(rPr)
                t = OxmlElement('w:t')
                t.text = text
                if text.startswith(' ') or text.endswith(' '):
                    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                run_elem.append(t)
                return run_elem

            if name_to_bold and name_to_bold in citation:
                # Split around target name
                idx = citation.index(name_to_bold)
                before = citation[:idx]
                after = citation[idx + len(name_to_bold):]

                # Add before (normal)
                if before:
                    ins.append(create_run_element(before, bold=False))

                # Add target name (bold)
                ins.append(create_run_element(name_to_bold, bold=True))
                self.stats['target_names_bolded'] += 1

                # Add after (normal)
                if after:
                    ins.append(create_run_element(after, bold=False))
            else:
                # No target name to bold - single run
                ins.append(create_run_element(citation, bold=False))

            # Append insertion to paragraph
            para._p.append(ins)
            self.stats['track_changes_added'] += 1

        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not add citation as insertion: {e}")
            # Fall back to normal citation
            self._add_citation_with_bold_author(para, citation, target_name, cv_owner_last_name)
