"""Section F1: licensure, plus the DEA and NPI numbers (#398).

The licensure table is four columns -- state, number, date of issue, date of
last registration -- but the section's real work is that three different kinds
of identifier arrive under one taxonomy code. A state medical licence belongs in
the table; a DEA registration and an NPI belong in a separate two-row table
further down the template, which is why `_fill_dea_npi` lives here rather than
anywhere else: it is the second half of one section's output.

Sorting the two out is done by LABEL first and shape second, because shape
alone cannot do it (#573):

- the `license_type` field, when stage 4 extracted one;
- else the word "NPI" or "DEA" in the entry's raw text, at a word start and
  not running on into letters (a fused "NPI15180546000" still counts) -- the
  old substring test fired on any entry mentioning "Dean";
- else, and only when the entry names no state, the number's shape: an NPI
  is 10 or 11 digits, a DEA number two letters plus seven alphanumerics. An
  all-numeric 10-11 digit state licence number is indistinguishable from an
  NPI by shape, which is why a stated jurisdiction disables the tiebreak.

Either match consumes the entry -- it is pulled out of the licence list, not
copied -- so a DEA number never also appears as a row in the state table. The
unstructured fallback is guarded by the same two label tests for the same
reason. The NPI and DEA slots are single-valued; a second, different
candidate is ignored with a warning rather than silently overwriting the
first (#573).

`_fill_dea_npi` finds its table by scanning every table in the document for a
cell containing "DEA number", not by position. The template revision history has
moved it, and matching on content survives that. It then matches each row by
its own label, so a template that lists NPI first still fills correctly.

The row writer copes with a template whose licensure table has been narrowed:
four columns get the full record, two get state and number, and anything
narrower is left alone rather than raising.

`_fill_licensure` is pinned on the class surface by
`tests/test_stage6_import_surface.py`, and `tests/test_stage6_unrendered_recovery.py`
calls it on an instance. The mixin keeps it resolving through the MRO, which is
what that guard checks.
"""
import logging
import re
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font, format_date_for_section
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)

# NPI / DEA detection (#573). A label is the acronym at a word start and
# not running on into letters: the old substring test
# ('DEA' in text.upper()) consumed any licence row whose text mentioned
# "Dean". A trailing DIGIT is allowed because extraction sometimes fuses
# the label into the number itself ("NPI15180546000" on corpus CV HU4DXA),
# and a plain \b would reject exactly those. Shape tests are a tiebreak
# only -- see _classify_licensure_entry.
_NPI_LABEL_RE = re.compile(r'\bNPI(?![A-Za-z])', re.IGNORECASE)
_DEA_LABEL_RE = re.compile(r'\bDEA(?![A-Za-z])', re.IGNORECASE)
_NPI_SHAPE_RE = re.compile(r'^\d{10,11}$')
_DEA_SHAPE_RE = re.compile(r'^[A-Za-z]{2}[A-Za-z0-9]{7}$')

# Classification results for one F1 entry.
KIND_LICENSE = 'license'
KIND_NPI = 'npi'
KIND_DEA = 'dea'


def _classify_licensure_entry(state: str, license_number: str,
                              license_type: str, original_text: str) -> str:
    """Classify one F1 entry as KIND_NPI, KIND_DEA or KIND_LICENSE.

    Routes on the label first: the `license_type` field when stage 4
    extracted one, else an explicit NPI/DEA word in the raw text. The
    number-shape tests run only for unlabelled entries that name no state,
    because a 10-11 digit state licence number is indistinguishable from an
    NPI by shape alone -- shape-first classification consumed real state
    licences (#573).
    """
    label = str(license_type or '').lower()
    text = str(original_text or '')
    if 'npi' in label:
        return KIND_NPI
    if 'dea' in label:
        return KIND_DEA
    if _NPI_LABEL_RE.search(text):
        return KIND_NPI
    if _DEA_LABEL_RE.search(text):
        return KIND_DEA
    if not license_number:
        return KIND_LICENSE
    if not state:
        if _NPI_SHAPE_RE.match(license_number):
            return KIND_NPI
        if _DEA_SHAPE_RE.match(license_number):
            return KIND_DEA
    return KIND_LICENSE


def _claim_identifier_slot(slot_name: str, current: str | None,
                           candidate: str, original_text: str) -> str:
    """Fill the single-valued NPI or DEA slot.

    When a second, different candidate arrives, keep the first (entries are
    already sorted most-recent-first) and warn instead of silently
    overwriting (#573)."""
    if current and current != candidate:
        logger.warning(
            "F1: second %s candidate %r (entry text %r) ignored; keeping %r",
            slot_name, candidate, original_text[:40], current)
        return current
    return candidate


class LicensureSection:
    """Section F1 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_licensure(self, entries: List[Dict]):
        """Fill F1. LICENSURE section.

        WCM template has table with: State | Number | Date of issue | Date of last registration
        Also fills DEA and NPI numbers in a separate table (Table 9).
        """

        if not entries:
            return

        if self.verbose:
            print(f"Filling Licensure ({len(entries)} entries)...")

        # Find Licensure section
        section_idx = self._find_paragraph_with_text("Licensure")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("LICENSURE")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(entries)

        # Track NPI and DEA numbers to fill separately
        npi_number = None
        dea_number = None
        regular_licenses = []

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = str(entry.get('text') or '')

            state = fields.get('state_country') or fields.get('state') or fields.get('jurisdiction') or ''
            license_number = str(fields.get('license_number') or fields.get('number') or '').strip()
            issue_date = fields.get('issue_date') or fields.get('date') or ''
            # WCM's template column is "Date of last registration"; stage 4's
            # extraction schema has no separate field for it, so the closest
            # available value -- expiration_date -- is what fills it.
            expiration_date = fields.get('expiration_date') or ''

            # NPI/DEA vs state licence: label first, shape as tiebreak (#573)
            kind = _classify_licensure_entry(
                state, license_number,
                fields.get('license_type') or '', original_text)
            if kind == KIND_NPI:
                npi_number = _claim_identifier_slot(
                    'NPI', npi_number, license_number, original_text)
                continue
            if kind == KIND_DEA:
                dea_number = _claim_identifier_slot(
                    'DEA', dea_number, license_number, original_text)
                continue

            # Regular license entry - format dates as mm/dd/yyyy per WCM template
            if state or license_number:
                regular_licenses.append({
                    'state': state,
                    'license_number': license_number,
                    'issue_date': format_date_for_section(issue_date, 'F1') if issue_date else '',
                    'expiration_date': format_date_for_section(expiration_date, 'F1') if expiration_date else ''
                })
            elif original_text:
                # Fallback to raw text for unstructured entries. No NPI/DEA
                # label re-check here: _classify_licensure_entry() already
                # tests original_text for both labels before returning
                # KIND_LICENSE, so reaching this branch already proves
                # neither label is present -- re-testing it here would only
                # duplicate the classifier's own answer (#573 review).
                regular_licenses.append({
                    'state': original_text[:100],
                    'license_number': '',
                    'issue_date': '',
                    'expiration_date': ''
                })

        # Fill regular licenses table
        for lic in regular_licenses:
            row = table.add_row()
            num_cols = len(row.cells)
            if num_cols >= 4:
                row.cells[0].text = lic['state'] or ''
                row.cells[1].text = lic['license_number'] or ''
                row.cells[2].text = lic['issue_date'] or ''
                row.cells[3].text = lic['expiration_date'] or ''
            elif num_cols >= 2:
                row.cells[0].text = lic['state'] or ''
                row.cells[1].text = lic['license_number'] or ''
            else:
                # Table narrower than the fallback can address -- nothing
                # was written, so don't count it as inserted.
                continue

            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        _set_font(run)
            self.stats['entries_inserted'] += 1

        # Fill DEA/NPI table (Table 9 in template)
        self._fill_dea_npi(dea_number, npi_number)

    def _fill_dea_npi(self, dea_number: str | None, npi_number: str | None):
        """Fill DEA and NPI numbers in their dedicated table.

        The WCM template has a 2-row table:
        Row 0: DEA number: (optional) | [value]
        Row 1: NPI number: (optional) | [value]

        Always finds the table and sets both cells (blanking whichever value
        is absent) rather than returning early when both are empty, so a
        second call on a reused document/generator can't leave a stale
        value from an earlier call in place.
        """
        # Find the DEA/NPI table by looking for a table containing "DEA number"
        dea_npi_table = None
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if 'dea number' in cell.text.lower():
                        dea_npi_table = table
                        break
                if dea_npi_table:
                    break
            if dea_npi_table:
                break

        if not dea_npi_table:
            return

        # Fill in the values
        for row in dea_npi_table.rows:
            if len(row.cells) >= 2:
                label = row.cells[0].text.lower()
                if 'dea' in label:
                    row.cells[1].text = dea_number or ''
                    for para in row.cells[1].paragraphs:
                        for run in para.runs:
                            _set_font(run)
                elif 'npi' in label:
                    row.cells[1].text = npi_number or ''
                    for para in row.cells[1].paragraphs:
                        for run in para.runs:
                            _set_font(run)
