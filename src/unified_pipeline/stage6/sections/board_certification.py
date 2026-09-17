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

An adversarial re-gate (2026-08-25) found `_is_fused_certification` still
over-triggers on real single certifications that say "board" twice ("Sub-
board of the American Board of Psychiatry and Neurology"; "American Board
of X, Board Eligible", which the template's own certificate column invites
by reading "Certificate # (indicate if board eligible)"). Rather than chase
the predicate further, `_backfill_missing_fields_from_structured` makes a
wrong dispatch harmless: a single reparsed row missing a certificate number
or date is filled from the entry's structured fields when they are
unambiguous, independent of why the row came up short.
"""
import logging
import re
from typing import Literal

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

CertTokenType = Literal['year', 'cert_number', 'specialty'] | None


def _classify_cert_token(token: str) -> CertTokenType:
    """Classify one token of a flattened board-certification table.

    A bare 4-digit token is a year, an all-digits-and-hyphens token is a
    certificate number, "MOC" marks a maintenance-of-certification date (kept
    with the years), and anything not starting with a digit is a specialty.
    Empty tokens and digit-led tokens matching none of the shapes classify as
    None and are dropped. A non-empty rejection (e.g. "123-", a bare "-") is
    logged at debug level so the drop is visible somewhere other than
    nowhere (§5.3/§5.10) -- empty tokens are the routine case from splitting
    on "|" and are not logged.
    """
    if YEAR_PATTERN.match(token):
        return 'year'
    if CERTIFICATE_NUMBER_PATTERN.match(token):
        return 'cert_number'
    if _MOC_TOKEN_PATTERN.match(token):
        return 'year'
    if token and _HAS_LETTER.search(token) and not _LEADING_DIGIT.match(token):
        return 'specialty'
    if token:
        logger.debug(
            "board certification: rejected token %r -- matches no known "
            "shape (year, certificate number, MOC, or specialty)", token,
        )
    return None


def _split_multi(value) -> list[str]:
    """Split a comma/semicolon-delimited field into its non-empty parts.

    Accepts a list (already split, e.g. `certificate_number` sometimes
    arrives as one from LLM extraction) or a string; always returns
    stripped, non-empty parts.
    """
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value or '').replace(';', ',').split(',') if p.strip()]


# Real ABMS board names name exactly one board and routinely contain a
# comma of their own: "American Board of Psychiatry and Neurology, Inc.",
# "American Board of Internal Medicine, Cardiovascular Disease". Splitting
# `certifying_board` on commas to detect fusion (an earlier version of
# `_is_fused_certification` did this) can't tell those apart from a genuine
# two-board fusion like "Board A, Board B" -- both split into exactly two
# comma-delimited parts. The word "board" itself is the more reliable
# signal: a real single board name contains it once ("American Board of
# ..."), so a SECOND occurrence is evidence a second board name was packed
# into the same field, independent of how many commas are in the string.
_BOARD_WORD_RE = re.compile(r'\bboard\b', re.IGNORECASE)


def _is_fused_certification(fields: dict, cert_numbers: list[str]) -> bool:
    """True when an entry's extracted fields describe more than one certification.

    The dispatch that decides between the single-row path and
    `_parse_and_add_multiple_certifications` used to be `len(cert_numbers) >
    1` alone. An entry with several specialties or dates but only one
    extracted certificate number took the single-cert path and could render a
    flattened, mixed entry as one row (#625 thread 3850459808). This also
    treats more than one `year_certified` as fusion evidence, and treats a
    second occurrence of the word "board" inside `certifying_board` as
    evidence a second board name was flattened in (see `_BOARD_WORD_RE`).

    What this predicate does NOT do, on purpose, is treat a comma inside
    `certifying_board` as fusion evidence by itself. An earlier version
    split `certifying_board` on commas/semicolons the same way as a fused
    `certificate_number`; real board names routinely contain a comma of
    their own ("American Board of Psychiatry and Neurology, Inc."), so that
    treated every genuine single-board entry with a comma in its name as
    fused and discarded its structured `certificate_number` and
    `year_certified` for a destructive text reparse -- a data-loss defect
    found reproducing HEAD~1 vs HEAD on exactly that board name (2026-08-25).
    Punctuation in a single free-text name is not reliable evidence of how
    many things are named in it; a count (of certificate numbers, of years,
    or of board-name occurrences) is.

    This still cannot see a fusion signalled only by the raw `text` (a third
    specialty with neither its own certificate number nor board name) --
    that would need extraction/normalization changes outside this file.
    """
    if len(cert_numbers) > 1:
        return True
    if len(_split_multi(fields.get('year_certified', ''))) > 1:
        return True
    board = str(fields.get('certifying_board') or '')
    return len(_BOARD_WORD_RE.findall(board)) > 1


def _format_certification_date_str(fields: dict) -> str:
    """Build the F2 "yyyy-yyyy" (or "yyyy-Present") date string for one
    entry's structured fields.

    Prefers `start_date`/`end_date`; falls back to `year_certified`/
    `recertification_date` when those are absent. Factored out so the
    single-certification path and `_parse_and_add_multiple_certifications`'s
    structured-fallback (HARD SAFETY NET, see that function) build the same
    string the same way instead of two copies drifting apart.
    """
    start_date = fields.get('start_date') or fields.get('year_certified') or ''
    end_date = fields.get('end_date') or fields.get('recertification_date') or ''

    start_fmt = format_date_for_section(str(start_date), 'F2') if start_date else ''
    end_fmt = format_date_for_section(str(end_date), 'F2') if end_date else ''

    if start_fmt and end_fmt:
        if end_fmt.lower() == 'present':
            return f"{start_fmt}-Present"
        if end_fmt != start_fmt:
            return f"{start_fmt}-{end_fmt}"
        return start_fmt
    if start_fmt:
        return start_fmt
    if end_fmt:
        return end_fmt
    return ''


def _is_reconstruction_confident(
    specialties: list[str], cert_numbers: list[str], years: list[str],
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


def _backfill_missing_fields_from_structured(
    rows: list[tuple], fields: dict, structured_cert: str,
) -> list[tuple]:
    """Fill a blank certificate number or date in a reparsed row from the
    entry's own structured fields, when it is unambiguous to do so.

    DATA-LOSS GATE, per-FIELD/per-ROW (2026-08-25, adversarial re-gate of
    #625): `_is_fused_certification` still over-triggers on real single
    certifications -- "Sub-board of the American Board of Psychiatry and
    Neurology" (real ABPN subspecialty phrasing) and "American Board of X,
    Board Eligible" (the template's own certificate column reads
    "Certificate # (indicate if board eligible)") both contain "board"
    twice. Routed to the text reparse, when it recovers a year but no
    certificate number for that one row, the whole-entry safety net in
    `_parse_and_add_multiple_certifications` does not fire -- it only
    rescues the case where NOTHING was recovered -- so the row rendered
    with its certificate number blanked. This makes that class of mistake
    harmless instead of trying to make the predicate perfect: it does not
    matter WHY the reparse under- or over-produced rows, a row that is
    missing a field the entry's structured data already answered gets it
    filled in here.

    Safe only when there is exactly ONE reparsed row: with more than one
    row, a single structured `certificate_number`/`year_certified` cannot
    be attributed to a particular row without guessing which one it
    belongs to -- copying it into one row (or worse, all of them) would
    invent an association the entry never stated. That case is left
    exactly as the reparse produced it and logged instead, so the miss is
    visible (§5.3/§5.10) rather than silently declined. A structured field
    that is ITSELF a comma-separated list of more than one value (a
    genuine multi-certification entry) is ambiguous on its own terms and is
    never treated as a candidate, regardless of row count.
    """
    cert_candidates = _split_multi(structured_cert)
    cert_candidate = cert_candidates[0] if len(cert_candidates) == 1 else ''
    year_candidates = _split_multi(fields.get('year_certified', ''))
    date_candidate = (
        _format_certification_date_str(fields) if len(year_candidates) <= 1 else ''
    )

    if len(rows) != 1:
        missing = [
            row for row in rows
            if (not row[1] and cert_candidate) or (not row[2] and date_candidate)
        ]
        if missing:
            logger.warning(
                "board certification: %d of %d reparsed row(s) are missing "
                "a certificate number or date that the entry's structured "
                "fields cannot be attributed to a single row -- leaving "
                "blank rather than guessing which row it belongs to "
                "(certificate_number=%r, year_certified=%r)",
                len(missing), len(rows), structured_cert,
                fields.get('year_certified', ''),
            )
        return rows

    specialty, cert_num, year = rows[0]
    if not cert_num and cert_candidate:
        cert_num = cert_candidate
    if not year and date_candidate:
        year = date_candidate
    return [(specialty, cert_num, year)]


def _reconstruct_certification_rows(
    specialties: list[str], cert_numbers: list[str], years: list[str],
) -> list[tuple]:
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
#
# The three entries below (name of specialty / board certificate[ #] / date
# of certification) do not match the WCM template's OWN F2 header row --
# verified 2026-08-25 via python-docx against
# key_files/wcm_cv_template_faculty_october_2022_final.docx, table 10, row
# 0: ['Full Name of Board', 'Certificate # \n(indicate if board eligible)',
# 'Dates of Certification \n(yyyy–yyyy)']. That's not a regression --
# the old substring check didn't match the real cells either -- but it does
# mean a source CV that reused the template's own header row verbatim was
# never being filtered. The three 'full name of board' / 'certificate # ...'
# / 'dates of certification ...' entries below are that real header, added
# alongside the old ones rather than in place of them: purely additive, so
# it cannot turn a previously-kept data line into a dropped one, and is
# covered by test_data_line_merely_mentioning_a_header_phrase_is_kept plus
# the new TestRealTemplateHeaderCellsAreFilteredWithoutRejectingData below.
#
# Known gap, out of this defect's scope: `entry_lines` (this module's only
# caller of this filter goes through it) splits strictly on "\n", so a
# header cell that keeps its OWN embedded line break -- as the two
# multi-line cells above do in the template -- is already broken into more
# than one physical line before this function ever sees a whole row to
# match against. Widening the set does not, and cannot, fix that; it only
# helps when the header row reaches here already flattened to one line per
# cell (see TestRealTemplateHeaderCellsAreFilteredWithoutRejectingData's
# comment for why).
_CERTIFICATION_HEADER_CELLS = {
    'name of specialty',
    'board certificate',
    'board certificate #',
    'date of certification',
    'full name of board',
    'certificate # (indicate if board eligible)',
    'dates of certification (yyyy–yyyy)',
}

_HEADER_CELL_WHITESPACE_RE = re.compile(r'\s+')


def _normalize_header_cell(cell: str) -> str:
    """Case- and whitespace-fold one flattened table cell for header matching.

    Folds any run of whitespace -- including the embedded newline the real
    WCM template header cells carry (e.g. "Certificate # \\n(indicate if
    board eligible)") -- to a single space, so a header cell that survives
    text-flattening with its line break intact still matches the
    single-line phrase in `_CERTIFICATION_HEADER_CELLS`.
    """
    return _HEADER_CELL_WHITESPACE_RE.sub(' ', cell).strip().lower()


def _is_certification_header_line(line: str) -> bool:
    """True when every cell of a flattened line is a known header phrase.

    A pipe-separated line is a header only when ALL of its cells match a
    known header phrase exactly (case-, whitespace-folded); one real data
    cell keeps the whole line. A bare (non-pipe) line is a header only when
    the whole line, normalized, matches one exactly.
    """
    cells = [_normalize_header_cell(c) for c in line.split('|')] if '|' in line else [_normalize_header_cell(line)]
    cells = [c for c in cells if c]
    if not cells:
        return False
    return all(cell in _CERTIFICATION_HEADER_CELLS for cell in cells)


class BoardCertificationSection:
    """Section F2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_board_certification(self, entries: list[dict]):
        """Fill F2. BOARD CERTIFICATION section.

        WCM template has table with: Name of specialty | Board Certificate # | Date of Certification

        Handles cases where multiple certifications are merged into one entry.

        No F2 entries still locates the table and clears it (#708): the WCM
        template ships a blank placeholder data row in this table, and this
        used to return before `_clear_table_data` ever ran, so that row
        survived into the delivered document on every CV with zero F2
        entries. The clear now always runs once the table is found; the
        row-writing loop below is simply a no-op on an empty `entries`, so
        no separate early return is needed. `tables_populated` only counts
        an actual write -- a cleared placeholder is not a populated table.
        """
        if self.verbose and entries:
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
        if entries:
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
                    date_str = _format_certification_date_str(fields)
                    self._add_board_cert_row(table, certifying_board, certificate_number, date_str)
            else:
                # No structured fields - try to parse from text
                self._parse_and_add_multiple_certifications(table, original_text, entry)

    def _parse_and_add_multiple_certifications(self, table, text: str, entry: dict):
        """Parse multiple board certifications from raw text and add rows.

        HARD SAFETY NET (data-loss gate, 2026-08-25): every caller reaches
        this because something -- `_is_fused_certification`, or the "no
        structured fields at all" branch -- decided a text reparse was
        needed. Either decision can be wrong for an entry that DOES carry a
        usable `certifying_board`/`certificate_number`/`year_certified`: an
        empty (or header-only) `text` used to make this return immediately
        with no log, silently dropping the whole row; and a reparse that
        recovers no certificate number or year for ANY row would otherwise
        discard ones the entry already had. Both now fall back to rendering
        the entry's own structured fields as a single row instead of
        rendering less than the entry already provides, and log why. This
        is independent of how good `_is_fused_certification`'s predicate
        is -- it holds even if a future change to that predicate misfires.
        """
        fields = entry.get('extracted_fields', {}) or {}
        structured_board = fields.get('certifying_board', '')
        structured_cert = fields.get('certificate_number', '')
        if isinstance(structured_cert, list):
            structured_cert = ', '.join(str(c).strip() for c in structured_cert if str(c).strip())
        else:
            structured_cert = str(structured_cert) if structured_cert else ''
        has_structured_data = bool(
            structured_board or structured_cert
            or fields.get('year_certified') or fields.get('recertification_date')
        )

        def _render_structured_fallback(reason: str):
            logger.warning(
                "board certification: %s -- rendering the entry's already-"
                "extracted fields instead of losing them (certifying_board="
                "%r, certificate_number=%r)",
                reason, structured_board, structured_cert,
            )
            self._add_board_cert_row(
                table, structured_board, structured_cert,
                _format_certification_date_str(fields),
            )

        if not text:
            if has_structured_data:
                _render_structured_fallback("no free text to reparse")
            else:
                logger.debug(
                    "board certification: entry has neither text nor "
                    "structured fields -- nothing to render",
                )
            return

        # Handle pipe-separated format: "Specialty | CertNum | Year"
        # First, split on newlines and filter empty lines
        lines = entry_lines(text)

        # Skip header lines (#625 thread 3850468238: exact per-cell match, not
        # "header phrase occurs somewhere in the line" -- see
        # _is_certification_header_line).
        lines = [l for l in lines if not _is_certification_header_line(l)]

        if not lines:
            if has_structured_data:
                _render_structured_fallback(
                    "text had no data lines left after header filtering")
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
        rows = _reconstruct_certification_rows(specialties, cert_numbers, years)

        # PER-ROW SAFETY NET (2026-08-25, adversarial re-gate): the
        # whole-entry fallback right below only fires when NO row recovered
        # anything. A single row that recovered a year but not a
        # certificate number (or vice versa) slips past it -- backfill that
        # from the entry's own structured fields when unambiguous, before
        # that all-or-nothing check runs. See
        # _backfill_missing_fields_from_structured for what "unambiguous"
        # means and why multi-row entries are excluded.
        rows = _backfill_missing_fields_from_structured(rows, fields, structured_cert)

        # HARD SAFETY NET, continued: a reparse that recovered no
        # certificate number and no year anywhere is strictly worse than
        # the structured fields the entry already had -- prefer those over
        # a specialty-only guess.
        if has_structured_data and not any(cert_num or year for _, cert_num, year in rows):
            _render_structured_fallback(
                "reparse recovered no certificate number or year for any row")
            return

        for specialty, cert_num, year in rows:
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

        That guard used to live only in the too-narrow (<2 column) branch,
        which meant a caller that supplies no content at all still wrote a
        fully blank row on the >=3 and >=2 branches, with `entries_inserted`
        incremented for it (#663 item 8). The only reproduced trigger is
        synthetic -- a caller passing all-empty or whitespace-only strings
        directly, as the tests below do -- not a confirmed corpus path; no
        production caller has been shown to reach this function with
        `specialty`, `cert_number`, and `dates` all blank. The blank check
        now runs once, before any column-count branch, so every path through
        this function shares it instead of each branch needing its own copy.
        """
        row = table.add_row()
        num_cols = len(row.cells)

        if not (
            (specialty or '').strip()
            or (cert_number or '').strip()
            or (dates or '').strip()
        ):
            row._element.getparent().remove(row._element)
            logger.warning(
                "board certification: skipping blank row -- specialty, "
                "certificate number, and dates were all empty",
            )
            return

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
