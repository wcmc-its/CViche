"""Section F2: board certification (#398).

Three columns -- specialty, certificate number, date -- and the section is small
enough that all of its difficulty is in one place: several certifications
routinely arrive merged into a single entry.

The writer detects that by splitting `certificate_number` on commas and
semicolons. More than one number means the entry describes more than one
certification, and its `extracted_fields` are a flattened mixture that cannot be
rendered as a single row, so it is handed to
`_parse_and_add_multiple_certifications`, which goes back to the raw text. The
same helper is the fallback when there are no structured fields at all.

That parser cannot rely on order or on separators, because the source is a table
that lost its columns, so it classifies each token by shape and rebuilds the
columns by position: a bare 4-digit token is a year, an all-digits-and-hyphens
token is a certificate number, "MOC" marks a maintenance-of-certification date,
and anything not starting with a digit is a specialty. Header rows ("Name of
Specialty", "Board Certificate #", "Date of Certification") are dropped first,
since extraction keeps them.

The section header is located by scanning for a paragraph that IS "Board
Certification" rather than by `_find_paragraph_with_text`. The template's parent
header reads "LICENSURE, BOARD CERTIFICATION", so a substring search matches it
first and would attach the certifications to the licensure table.

PR #625 review (2026-08-25) added five more pieces, all still local to this
file: entries are sorted `sort_entries_reverse_chronological` before
rendering, like the 15 sibling section writers already do; the multi-cert
dispatch decision is an explicit `_is_fused_certification()` predicate instead
of `len(cert_numbers) > 1`; the positional specialty/cert-number/year pairing
inside `_parse_and_add_multiple_certifications` is factored into
`_reconstruct_certification_rows()`, which now logs when the token counts
disagree instead of silently presenting a guess as fact (the actual pairing
math, including the row count, is unchanged -- see the HARD SAFETY GATE note
on that function); the certificate-number grammar and the MOC-token check are
each anchored instead of matching anywhere in the string; and header-line
filtering matches whole flattened-table cells exactly instead of testing
whether a header phrase merely occurs somewhere in the line.
"""
import logging
import re
from typing import Dict, List, Literal, Optional

from ..formatting import _clear_table_data, _set_font, format_date_for_section
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines

logger = logging.getLogger(__name__)

# Token shapes for `_classify_cert_token`. Named module constants because each
# used to be written out twice, once per branch of
# `_parse_and_add_multiple_certifications` (#572).
YEAR_PATTERN = re.compile(r'^\d{4}$')
# Anchored digit groups joined by single hyphens: "123", "123-456". Was
# `^[\d\-]+$`, which also matched "-", "--", "123-" and "-123" -- bare or
# trailing/leading hyphens with no actual number either side (#625 thread
# 3850174961). Those are dropped by `_classify_cert_token` now instead of
# rendering as a certificate number.
CERTIFICATE_NUMBER_PATTERN = re.compile(r'^\d+(?:-\d+)*$')
# `\d`, not str.isdigit(): isdigit() also accepts superscripts and circled
# digits, which the original inline `re.match(r'^\d', ...)` did not.
_LEADING_DIGIT = re.compile(r'^\d')
# A specialty name has letters in it. Tightening CERTIFICATE_NUMBER_PATTERN
# (#625 thread 3850174961) means a punctuation-only token like a bare "-" or
# "--" no longer matches it, and such a token doesn't start with a digit
# either -- without this check it would fall through to the specialty
# fallback below and pollute the specialty bucket instead of being dropped.
_HAS_LETTER = re.compile(r'[A-Za-z]')
# "MOC" alone, or paired with a year ("MOC 2015", "MOC: 2015", "moc-2015"),
# case-insensitive and anchored on the whole token. Was `'MOC' in token`,
# unanchored and case-sensitive, so "NONMOC" or "Board MOC Status" -- MOC
# occurring as a substring rather than as the token's own value -- were
# misclassified as a certification year (#625 thread 3850184512).
_MOC_TOKEN_PATTERN = re.compile(r'^MOC(?:[\s:.\-]*\d{4})?$', re.IGNORECASE)

CertTokenType = Optional[Literal['year', 'cert_number', 'specialty']]


def _classify_cert_token(token: str) -> CertTokenType:
    """Classify one token of a flattened board-certification table.

    A bare 4-digit token is a year, an all-digits-and-hyphens token is a
    certificate number, "MOC" marks a maintenance-of-certification date (kept
    with the years), and anything not starting with a digit is a specialty.
    Empty tokens and digit-led tokens matching none of the shapes classify as
    None and are dropped.
    """
    if YEAR_PATTERN.match(token):
        return 'year'
    if CERTIFICATE_NUMBER_PATTERN.match(token):
        return 'cert_number'
    if _MOC_TOKEN_PATTERN.match(token):
        return 'year'
    if token and _HAS_LETTER.search(token) and not _LEADING_DIGIT.match(token):
        return 'specialty'
    return None


def _split_multi(value) -> List[str]:
    """Split a comma/semicolon-delimited field into its non-empty parts.

    Accepts a list (already split, e.g. `certificate_number` sometimes
    arrives as one from LLM extraction) or a string; always returns
    stripped, non-empty parts.
    """
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value or '').replace(';', ',').split(',') if p.strip()]


def _is_fused_certification(fields: Dict, cert_numbers: List[str]) -> bool:
    """True when an entry's extracted fields describe more than one certification.

    The dispatch that decides between the single-row path and
    `_parse_and_add_multiple_certifications` used to be `len(cert_numbers) >
    1` alone. An entry with several specialties or dates but only one
    extracted certificate number took the single-cert path and could render a
    flattened, mixed entry as one row (#625 thread 3850459808). This
    considers `certifying_board` too: it carries the same comma/semicolon
    joined shape as a fused `certificate_number` when several boards were
    flattened together, so splitting it the same way catches that case as
    well. It cannot see a fusion signalled only by the raw `text` (a third
    specialty with neither its own certificate number nor board name) --
    that would need extraction/normalization changes outside this file.
    """
    if len(cert_numbers) > 1:
        return True
    return len(_split_multi(fields.get('certifying_board', ''))) > 1


def _is_reconstruction_confident(
    specialties: List[str], cert_numbers: List[str], years: List[str],
) -> bool:
    """True when the recovered token counts support a positional pairing.

    `_classify_cert_token` shape-classifies tokens with no idea which table
    row each came from, so lining them up by index is a guess (#625 thread
    3850165177). The guess is at its most defensible when there is exactly
    one certificate number per specialty, and -- if any years were found at
    all -- exactly one year per specialty too; that is the shape a normal,
    unfused certification table produces. Anything else (a dropped token, a
    reordered pair, an orphaned date) means the counts disagree and the
    pairing is not to be trusted as fact.
    """
    if not specialties or not cert_numbers:
        return False
    if len(specialties) != len(cert_numbers):
        return False
    if years and len(years) != len(specialties):
        return False
    return True


def _reconstruct_certification_rows(
    specialties: List[str], cert_numbers: List[str], years: List[str],
) -> List[tuple]:
    """Pair specialty/cert-number/year tokens recovered from a flattened table.

    HARD SAFETY GATE: this always pairs positionally, padding whichever list
    runs out with '' -- byte-identical to the reconstruction this replaces,
    row count included. That is deliberate: refusing to render a row here
    would drop real certification data, which is a worse outcome than an
    admitted, logged low-confidence row (see docs/DEV_WORKFLOW.md's
    no-content-loss rule). What changed is that
    `_is_reconstruction_confident` now names the disagreement and it gets
    logged, instead of a mismatched pairing being presented with the same
    silent certainty as a clean 1:1 match.
    """
    if not _is_reconstruction_confident(specialties, cert_numbers, years):
        logger.warning(
            "board certification reconstruction: %d specialty token(s), %d "
            "certificate number token(s), %d year token(s) -- counts "
            "disagree, pairing positionally with low confidence rather than "
            "dropping data",
            len(specialties), len(cert_numbers), len(years),
        )

    num_certs = max(len(specialties), len(cert_numbers), 1)
    rows = []
    for i in range(num_certs):
        specialty = specialties[i] if i < len(specialties) else ''
        cert_num = cert_numbers[i] if i < len(cert_numbers) else ''
        year = years[i] if i < len(years) else (years[-1] if years else '')
        rows.append((specialty, cert_num, year))
    return rows


# The known WCM board-certification header-row cell text (case-folded), from
# the three columns named in this module's docstring. Matched per-cell,
# exactly, rather than as a substring of the whole line -- a substring test
# ('date of certification' in line) also matched real content that happened
# to mention one of these phrases, dropping it (#625 thread 3850468238).
_CERTIFICATION_HEADER_CELLS = {
    'name of specialty',
    'board certificate',
    'board certificate #',
    'date of certification',
}


def _is_certification_header_line(line: str) -> bool:
    """True when every cell of a flattened line is a known header phrase.

    A pipe-separated line is a header only when ALL of its cells match a
    known header phrase exactly (case-, whitespace-folded); one real data
    cell keeps the whole line. A bare (non-pipe) line is a header only when
    the whole line, normalized, matches one exactly.
    """
    cells = [c.strip().lower() for c in line.split('|')] if '|' in line else [line.strip().lower()]
    cells = [c for c in cells if c]
    if not cells:
        return False
    return all(cell in _CERTIFICATION_HEADER_CELLS for cell in cells)


class BoardCertificationSection:
    """Section F2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_board_certification(self, entries: List[Dict]):
        """Fill F2. BOARD CERTIFICATION section.

        WCM template has table with: Name of specialty | Board Certificate # | Date of Certification

        Handles cases where multiple certifications are merged into one entry.
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Board Certification ({len(entries)} entries)...")

        # Find Board Certification section - need to find the subsection header,
        # not the main "LICENSURE, BOARD CERTIFICATION" section header
        section_idx = None
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            # Look for exact match or starts with "Board Certification"
            if text == "Board Certification" or text.startswith("Board Certification:"):
                section_idx = i
                break
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # #625 thread 3850478580: the 15 sibling section writers under this
        # same header (e.g. licensure.py) sort reverse-chronologically before
        # rendering; this one didn't. The shared sorter now also reads F2's
        # own `recertification_date` and `year_certified` (both declared by
        # F2 alone, so no other section's order can shift), which is what
        # real board-certification entries actually carry -- a certification
        # issued in 2005 and recertified in 2020 sorts as 2020.
        for entry in sort_entries_reverse_chronological(entries):
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            certifying_board = fields.get('certifying_board', '')
            certificate_number = fields.get('certificate_number', '')
            year_certified = fields.get('year_certified', '')
            recertification_date = fields.get('recertification_date', '')

            # Handle certificate_number being a list (from LLM extraction) or string
            if isinstance(certificate_number, list):
                cert_numbers = [str(n).strip() for n in certificate_number if n]
                certificate_number = ', '.join(cert_numbers)  # Also update for single cert case
            else:
                certificate_number = str(certificate_number) if certificate_number else ''

            # Check if we have structured fields
            if certifying_board or certificate_number:
                # Multiple certifications merged into one entry? (#625 thread
                # 3850459808: considers certifying_board too, not just the
                # certificate-number count -- see _is_fused_certification.)
                cert_numbers = _split_multi(certificate_number)

                if _is_fused_certification(fields, cert_numbers):
                    # Multiple certifications merged - try to parse from original text
                    # Original text pattern: "Specialty1\n\nSpecialty2 | CertNum1\n\nCertNum2 | Year1\nYear2"
                    self._parse_and_add_multiple_certifications(table, original_text, entry)
                else:
                    # Single certification - format dates as yyyy-yyyy per WCM template
                    # Use start_date/end_date if available, fall back to year_certified/recertification_date
                    start_date = fields.get('start_date') or year_certified
                    end_date = fields.get('end_date') or recertification_date

                    start_fmt = format_date_for_section(str(start_date), 'F2') if start_date else ''
                    end_fmt = format_date_for_section(str(end_date), 'F2') if end_date else ''

                    # Build date range string
                    if start_fmt and end_fmt:
                        if end_fmt.lower() == 'present':
                            date_str = f"{start_fmt}-Present"
                        elif end_fmt != start_fmt:
                            date_str = f"{start_fmt}-{end_fmt}"
                        else:
                            date_str = start_fmt
                    elif start_fmt:
                        date_str = start_fmt
                    elif end_fmt:
                        date_str = end_fmt
                    else:
                        date_str = ''

                    self._add_board_cert_row(table, certifying_board, certificate_number, date_str)
            else:
                # No structured fields - try to parse from text
                self._parse_and_add_multiple_certifications(table, original_text, entry)

    def _parse_and_add_multiple_certifications(self, table, text: str, entry: Dict):
        """Parse multiple board certifications from raw text and add rows."""
        if not text:
            return


        # Handle pipe-separated format: "Specialty | CertNum | Year"
        # First, split on newlines and filter empty lines
        lines = entry_lines(text)

        # Skip header lines (#625 thread 3850468238: exact per-cell match, not
        # "header phrase occurs somewhere in the line" -- see
        # _is_certification_header_line).
        lines = [l for l in lines if not _is_certification_header_line(l)]

        if not lines:
            return

        # Try to parse pipe-separated rows first
        # Format might be: "Specialty | CertNum | Year" on each line
        # Or mixed format from extraction artifacts

        specialties: list[str] = []
        cert_numbers: list[str] = []
        years: list[str] = []
        buckets: dict[str, list[str]] = {
            'specialty': specialties, 'cert_number': cert_numbers, 'year': years,
        }

        for line in lines:
            # A pipe-separated line carries several tokens; a bare line is one
            tokens = [p.strip() for p in line.split('|')] if '|' in line else [line.strip()]
            for token in tokens:
                token_type = _classify_cert_token(token)
                if token_type is not None:
                    buckets[token_type].append(token)

        # Match specialties with cert numbers. #625 threads 3850165177 /
        # 3850459808: reconstruction now goes through an explicit helper that
        # logs when the token counts disagree instead of silently pairing by
        # position as if it were certain -- see _reconstruct_certification_rows.
        for specialty, cert_num, year in _reconstruct_certification_rows(specialties, cert_numbers, years):
            # Format year as yyyy per WCM template
            if year:
                year = format_date_for_section(year, 'F2')

            if specialty or cert_num:
                self._add_board_cert_row(table, specialty, cert_num, year)

    def _add_board_cert_row(self, table, specialty: str, cert_number: str, dates: str):
        """Add a single board certification row to the table.

        The WCM template has 3 columns; a template narrowed to 2 still gets a
        usable row (cert number and date folded into one cell). A template
        narrowed below 2 columns can't carry both a specialty and a
        certificate number, and used to leave the just-added row in the
        table with nothing written into it -- a blank row with no record of
        why (#625 thread 3850492866). It's removed and the row is skipped
        instead, with a warning logged so the loss is visible; not raised,
        because a raised exception here would abort the rest of the document
        (docs/DEV_WORKFLOW.md's no-content-loss rule -- one narrow table
        should not cost every section after it).
        """
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = specialty or ''
            row.cells[1].text = cert_number or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = specialty or ''
            row.cells[1].text = f"{cert_number} ({dates})" if dates else (cert_number or '')
        else:
            row._element.getparent().remove(row._element)
            logger.warning(
                "board certification table has %d column(s), too narrow to "
                "render a row (specialty=%r, cert_number=%r, dates=%r) -- "
                "skipping",
                num_cols, specialty, cert_number, dates,
            )
            return

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1
