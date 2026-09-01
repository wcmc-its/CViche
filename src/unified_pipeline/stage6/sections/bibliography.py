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
without splitting the text into runs. The author-matching and citation-splitting
rule lives once, in `_citation_author_split` (#572); the writers are thin tails
over two different substrates:

- `_add_citation_with_bold_author` writes plain runs via python-docx, and is
  what a non-enriched citation uses.
- `_add_citation_with_bold_author_as_insertion` builds the same runs as raw
  `w:r` elements inside a `w:ins`, because an enriched citation is rendered as a
  tracked insertion paired with a deletion of the original text, and python-docx
  cannot add a run *into* a revision element. It falls back to the plain path if
  the XML build raises. That fallback is not a universal net: raw control
  characters, the one input class known to make the XML build raise, are now
  stripped inside the tracked-insertion writer itself (#552), so that
  specific failure no longer reaches the fallback -- but the plain writer
  does not sanitize, so a control character reaching it by some other route
  would still raise there too.

Author bolding targets `target_name` from `_format_citation`, falling back to
the CV owner's last name -- taken from `cv_owner`, or recovered from the
document uid when the pipeline never resolved an owner.
"""
import inspect
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import _format_citation, _set_font
from ..normalization import split_fused_citation_entries
from ..parsing import _extract_last_name_from_uid
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)

# Tracked-insertion citation runs are built as raw w:r XML -- python-docx has
# no API to add a run *into* a w:ins element, so this path cannot call
# _set_font() like every other run in this file (#625). A prior fix (#625
# round 2) named these two constants so a diverging value would at least be
# visible at both call sites; that only holds if someone remembers to update
# both by hand. #662 item 4 closes the actual gap: read straight out of
# _set_font's own default parameters, so a policy change in
# stage6/formatting/docx.py reaches this path automatically instead of
# silently drifting out of step. w:sz is expressed in half-points.
_SET_FONT_DEFAULTS = inspect.signature(_set_font).parameters
TRACKED_INSERTION_FONT_NAME = _SET_FONT_DEFAULTS['name'].default
TRACKED_INSERTION_FONT_SIZE_PT = _SET_FONT_DEFAULTS['size'].default
TRACKED_INSERTION_FONT_SIZE_HALF_POINTS = str(TRACKED_INSERTION_FONT_SIZE_PT * 2)

# #552: raw `w:t` / `w:delText` assignment is lxml's own `.text` setter, not
# python-docx's `Run.text` -- it raises ValueError on the same control-code
# range python-docx itself rejects ("All strings must be XML compatible: ...
# no NULL bytes or control characters"), for any of the three run-text
# writes in stage 6 that build revision XML by hand (the other two are
# stage_6_word_template.py's `_add_track_change_insertion` /
# `_add_track_change_deletion`; this module's own is `create_run_element`
# below). Source .docx text cannot carry these codepoints (lxml rejects them
# at parse time), so the exposure is LLM-written fields -- a stage-4.5/5c/5d
# field reaching a citation or a tracked-change run. `\t`, `\n` and `\r` are
# valid XML characters and must NOT be stripped: `_clean_inline_tabs`'s
# label/value contract (test_cell_separators.py:34-37) depends on tab
# characters surviving into rendered text.
_CONTROL_CHAR_PATTERN = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def _enrichment_field_text(value: Any) -> str:
    """Coerce one raw enrichment field to plain text.

    A missing key already becomes '' via `dict.get`'s own default before
    this is ever called; this covers the other two cases: an explicit
    ``None`` (some stage-5 writers set the key rather than omit it), and a
    field that came back as something other than a string. Never raises --
    stringifying an unexpected shape keeps the citation rendering instead of
    aborting it."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return str(value)


@dataclass
class _CitationEnrichment:
    """One bibliography entry's enrichment fields, the way this renderer
    actually reads them (review thread 3850155382): `enrichment_status`,
    `enrichment_source`, and the original `text` a track-changes deletion
    needs, read together at one boundary instead of three separate
    `pub.get(...)` calls scattered through the loop body below. Mirrors
    `_CommitteeRecord` in `administrative_activities.py` from this same PR
    round -- a dataclass local to this renderer, not a stage6-wide
    convention.

    `from_raw` never raises: an unfamiliar `pub` shape, or a field that came
    back as something other than a string, degrades to the empty-string
    default for that field rather than aborting the citation."""
    enrichment_status: str = ''
    enrichment_source: str = ''
    text: str = ''

    @classmethod
    def from_raw(cls, raw) -> '_CitationEnrichment':
        """Build a record from one raw stage-5 publication entry."""
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            enrichment_status=_enrichment_field_text(raw.get('enrichment_status')),
            enrichment_source=_enrichment_field_text(raw.get('enrichment_source')),
            text=_enrichment_field_text(raw.get('text')),
        )


def _citation_author_split(citation: str, target_name: Optional[str],
                           cv_owner_last_name: str) -> tuple[str, str, str]:
    """Split a citation around the author name that should render bold.

    The one home for the author-matching rule shared by the plain and the
    tracked-insertion citation writers (#572): a change to the rule (suffixes
    like "Jr.", hyphenated surnames, particles) must reach both writers, or
    enriched and non-enriched citations in the same bibliography bold
    different text.

    Prefers `target_name` when it appears verbatim in the citation; otherwise
    falls back to finding `cv_owner_last_name` with trailing initials, e.g.
    "Wende ME", "Wende, M", "Wende M.".

    Returns `(before, name_to_bold, after)`. When nothing matches,
    `name_to_bold` is '' and the whole citation is in `before`.
    """
    name_to_bold = None
    if target_name and target_name in citation:
        name_to_bold = target_name
    elif cv_owner_last_name:
        pattern = rf'\b{re.escape(cv_owner_last_name)}\s*[A-Z]{{0,3}}\.?\b'
        match = re.search(pattern, citation, re.IGNORECASE)
        if match:
            name_to_bold = match.group(0).rstrip('.,')

    if not (name_to_bold and name_to_bold in citation):
        return citation, '', ''

    idx = citation.index(name_to_bold)
    return citation[:idx], name_to_bold, citation[idx + len(name_to_bold):]


class BibliographySection:
    """Section S writers, mixed into `WCMTemplateGenerator`."""

    @staticmethod
    def _sanitize_run_text(text: str) -> str:
        """Strip the control characters lxml's `.text` setter rejects from
        one run's text, before it reaches a raw `w:t`/`w:delText` element.

        The one sanitiser for stage 6's three run-text writes (#552) --
        `WCMTemplateGenerator._add_track_change_insertion` and
        `_add_track_change_deletion` in stage_6_word_template.py reach this
        through the mixin (`self._sanitize_run_text`); this module's own
        `create_run_element`, below, calls it directly. `\\t`, `\\n` and
        `\\r` are valid XML and are left untouched.
        """
        return _CONTROL_CHAR_PATTERN.sub('', text)

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

        logger.info("Filling Bibliography (%d publications)...", total_pubs)

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
                logger.warning("Could not find section header %r", header_text)
                continue

            logger.info("%s: %d entries -> '%s...'", code, len(pubs), header_text[:30])

            # Sort reverse chronologically (most recent first)
            pubs_sorted = sort_entries_reverse_chronological(pubs)
            # Un-fuse any entry that collapsed several citations into one
            # (#208), so each is numbered instead of rendering as unnumbered
            # <w:br/> continuation lines under one number.
            pubs_sorted = split_fused_citation_entries(pubs_sorted)

            # Resolve the insertion anchor once. Every paragraph for this
            # section is inserted immediately before this one anchor object,
            # rather than by re-indexing self.doc.paragraphs (which re-walks
            # the document body on every access) after each insertion -- that
            # re-indexing is what made the old insert_idx pattern fragile.
            # A bibliography header is allowed to be the template's last
            # paragraph, in which case there is no following paragraph to
            # anchor on; new content is then appended to the end of the
            # document instead (#625).
            paragraphs = self.doc.paragraphs
            anchor = paragraphs[section_idx + 1] if section_idx + 1 < len(paragraphs) else None

            def _insert_before_anchor(text: str = "") -> Paragraph:
                if anchor is not None:
                    return anchor.insert_paragraph_before(text)
                return self.doc.add_paragraph(text)

            # Add blank line before first citation in each section
            if pubs_sorted:
                _insert_before_anchor("")

            for citation_num, pub in enumerate(pubs_sorted, start=1):
                citation_text, target_name, enriched_fields = _format_citation(pub, citation_num)
                enrichment = _CitationEnrichment.from_raw(pub)
                original_text = enrichment.text
                enrichment_status = enrichment.enrichment_status

                # Insert citation paragraph before the anchor
                para = _insert_before_anchor("")

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
                    enrichment_source = enrichment.enrichment_source
                    if enrichment_source:
                        comment = f"Data enriched from {enrichment_source.upper()}. Fields updated: {', '.join(enriched_fields)}"
                        self._add_word_comment(para, comment, author="PubMed Enrichment")

                # Add comments from upstream pipeline processes
                self._add_entry_comments(para, pub)

                self.stats['entries_inserted'] += 1

    def _add_citation_with_bold_author(self, para: Paragraph, citation: str, target_name: Optional[str], cv_owner_last_name: str = ''):
        """
        Add citation text to paragraph, bolding the target author name.

        If target_name is not found, falls back to searching for cv_owner_last_name.
        """
        para.clear()

        before, name_to_bold, after = _citation_author_split(
            citation, target_name, cv_owner_last_name)

        if name_to_bold:
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

            before, name_to_bold, after = _citation_author_split(
                citation, target_name, cv_owner_last_name)

            def create_run_element(text: str, bold: bool = False) -> Any:
                """Create a w:r element with text and optional bold."""
                run_elem = OxmlElement('w:r')
                rPr = OxmlElement('w:rPr')
                rFonts = OxmlElement('w:rFonts')
                rFonts.set(qn('w:ascii'), TRACKED_INSERTION_FONT_NAME)
                rFonts.set(qn('w:hAnsi'), TRACKED_INSERTION_FONT_NAME)
                rPr.append(rFonts)
                sz = OxmlElement('w:sz')
                sz.set(qn('w:val'), TRACKED_INSERTION_FONT_SIZE_HALF_POINTS)
                rPr.append(sz)
                if bold:
                    b = OxmlElement('w:b')
                    rPr.append(b)
                run_elem.append(rPr)
                t = OxmlElement('w:t')
                # The xml:space guard reads the sanitized string, not the raw
                # one: stripping a control character can expose a leading or
                # trailing space that the raw text did not start or end with,
                # and without xml:space="preserve" Word collapses it.
                clean_text = self._sanitize_run_text(text)
                t.text = clean_text
                if clean_text.startswith(' ') or clean_text.endswith(' '):
                    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                run_elem.append(t)
                return run_elem

            if name_to_bold:
                # Add before (normal)
                if before:
                    ins.append(create_run_element(before, bold=False))

                # Add target name (bold)
                ins.append(create_run_element(name_to_bold, bold=True))

                # Add after (normal)
                if after:
                    ins.append(create_run_element(after, bold=False))
            else:
                # No target name to bold - single run
                ins.append(create_run_element(citation, bold=False))

            # Append insertion to paragraph. Stats are updated only now, once
            # the tracked-insertion path is known to have actually succeeded.
            # Incrementing target_names_bolded earlier (previously right
            # after the bold run was built, before the XML build finished)
            # meant an exception raised later here still left the increment
            # in place, and the except block's fallback to
            # _add_citation_with_bold_author counted the same citation a
            # second time (#625).
            para._p.append(ins)
            self.stats['track_changes_added'] += 1
            if name_to_bold:
                self.stats['target_names_bolded'] += 1

        except Exception as e:
            logger.warning("Could not add citation as insertion: %s", e)
            # Fall back to normal citation
            self._add_citation_with_bold_author(para, citation, target_name, cv_owner_last_name)
