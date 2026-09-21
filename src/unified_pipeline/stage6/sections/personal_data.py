"""Section A: personal data -- name, addresses, phones, emails (#398).

The shortest section in the template and one of the longest writers, because
almost nothing about a CV's contact block is structured. The work is in order:

1. Resolve the name. `cv_owner` first, then a LinkedIn slug in the A entries,
   then the document uid via `_extract_name_from_uid`. A bare surname does not
   count as complete and keeps the fallbacks running.
2. Classify each A entry into one of six slots by reading the LABEL in the
   source text, not the extracted field -- "Cell phone:" and "Office:" are what
   distinguish two otherwise identical phone numbers, and one entry can carry
   both. An extracted address that names its own halves is routed by
   `_labels_its_own_address_slots` instead, which is #442: a dict naming home
   and office was previously forced whole into whichever slot the raw text
   happened to label.
3. Re-open the ORIGINAL .docx when fields are still missing, in
   `_recover_contact_fields_from_docx`. Contact data frequently lives in a
   source table ("NAME: | Patricia Opresko") that entry extraction never
   turned into entries, and business-address cells embed Phone/Fax/E-mail
   lines that have to be pulled back out line by line. A record that already
   has all four -- a complete name and all three contact values -- never
   opens the document at all.
4. Fill the template's PERSONAL DATA table, located by its "Work email:" cell
   rather than by index.

This is the only section writer that reads the source document directly, which
is why `Document` and `Path` are imported here and nowhere else in this package.

Step 3 runs on a live CV because both drivers hand `run_stage6()` the resolved
source path (`run_full_pipeline.py` `_stage_6`, `orchestrator.py` stage `'6'`;
#550), as do `scripts/render_gate.py --source-dir` and this package's tests.
Before that wiring the web path reached step 3 only by coincidence -- the
orchestrator's `_copy_to_pipeline_input` drops each upload into the same
directory `generate()`'s `SAMPLE_CV_DIR` guess scans, under the same uid --
and the CLI reached it only for a uid-style run from the repo root, never for
a corpus batch on a path or from a worktree.

Earlier versions of this paragraph were wrong twice: "the server always passes
original_doc_path" (false until #550's wiring), then "unreachable in
production until a driver supplies the path" (false for the web path, see
above). If it goes stale again, the two other copies are
`stage_6_word_template.py`'s SAMPLE_CV_DIR constant and `run_stage6()`'s
docstring.

5. There is no step 5 any more, and that is the point. Steps 2, the
   all-entries email scan and 3 all store into the SAME three slot names --
   `work_email`/`office_phone`/`office_address`, the ones step 4 reads.
   Before #550 the recovery stored into a second `email`/`phone`/`address`
   set that nothing bridged, so every value it recovered was computed and
   discarded; #550 bridged the two with three `x = x or recovered` lines, and
   the review that followed removed the second set of names rather than keep
   the bridge. Each recovery is guarded by `if not <slot>`, so a value
   already classified from the A entries is never overwritten by a weaker
   later read -- which is the invariant the negative control in
   `tests/test_stage6_personal_data_fallback_writeback.py` pins.
"""
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, NamedTuple

try:
    from docx import Document
    # The class `Document()` (the factory function above) returns -- aliased
    # so `_recover_contact_fields_from_table_rows` can annotate its
    # `original_doc` parameter without shadowing the factory import (#820 R3).
    from docx.document import Document as _WordDocument
    from docx.opc.exceptions import PackageNotFoundError
    from lxml.etree import XMLSyntaxError
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import _set_cell_text, _set_font
from ..normalization import (
    CAT_HOME_CONTACT,
    WithheldItem,
    _address_cell_text,
    _from_pii_fragment,
    _labels_its_own_address_slots,
    _labels_its_own_phone_slots,
    _phone_cell_text,
    _pii_category_of,
)
from ..parsing import _extract_name_from_uid

logger = logging.getLogger(__name__)


# What opening a source path can raise when it is not a readable .docx.
# Determined by feeding `Document()` each shape rather than guessed: a missing
# file, an empty file, a text file renamed .docx and a truncated zip all raise
# PackageNotFoundError; a valid zip that is not an OPC package raises KeyError
# on '[Content_Types].xml'; a package whose document.xml is malformed raises
# lxml's XMLSyntaxError; a directory named .docx raises FileNotFoundError.
# Every one of them comes out of the OPEN -- none out of the scan that follows
# it -- so only the open is guarded and a programming error in the scan fails
# the run instead of being counted as a source-document problem.
_UNREADABLE_SOURCE_ERRORS = (PackageNotFoundError, KeyError, XMLSyntaxError, OSError)


# One phone number, in the shapes a CV actually carries. The two searches in
# `_fill_personal_data` each inlined `(\(\d{3}\)\s*\d{3}[-.\s]?\d{4})`, which
# recognises exactly one US form: a "+44 20 7946 0958" labelled Cell in an
# entry that also carries an Office number went unassigned while the Office
# number beside it was assigned, so the entry rendered half its numbers. Named
# once because both searches have to agree on it.
#
# The two boundary assertions are the point, not decoration. Without them the
# groups still have to be 2-4 digits each, so "+91 98765 43210" -- the standard
# Indian mobile grouping, one of the shapes this widening was asked for -- fails
# at the "+91 " start, re.search slides forward, and the pattern matches
# "91 9876" out of the middle of it. That renders a truncated number into the
# Cell phone row, which is strictly worse than the US-only pattern it replaced:
# that one matched nothing here and left the row blank. Same shape drops the
# leading digit of "1-800-555-0199".
#
# Ceiling: this matches a digit-group shape, not a dialling plan, so a year
# range or a long identifier standing between a Cell/Office label and its
# number could be captured instead -- reachable now in a way the US-only
# pattern was not, and bounded only by the two searches scanning lazily from
# their own label and never crossing a ';' or a newline. One A-entry in the
# 66-CV corpus reaches this branch at all, and it carries two plain US numbers,
# so the corpus cannot exercise either the widening or its ceiling (6.5).
_PHONE_NUMBER_PATTERN = (
    r'(?<![\d-])'                                      # never start mid-number
    r'(?:\+\d{6,15}'                                   # +442079460958
    r'|(?:\+?\d{1,3}[-.\s])?(?:\(\d{1,4}\)|\d{2,5})'  # +44 20 / 1-800 / (212)
    r'(?:[-.\s]\d{2,7}){1,4})'                         # ... 7946 0958 / 900123
    r'(?!\d)'                                          # never stop mid-number
)


# The four contact fields a source-table label cell can name.
# `_recover_contact_fields_from_docx` routes on these instead of on
# `'business' in label` and `'name' in label'`. Two defects, measured against
# the pre-change code rather than assumed: 'business' ALSO read "Business
# phone:" and "Business email:" as an address -- the four extraction blocks
# were independent `if`s, not an `elif` chain, so the phone and email branches
# did still fill their own slots, and the damage was office_address taking a
# copy of the phone number or the email address on top. 'name' read
# "Username:" and "Department name:" as the person's name. Routing on one
# classifier makes the four branches mutually exclusive, which is what stops
# the address branch taking a second copy.
_FIELD_NAME = 'name'
_FIELD_OFFICE_ADDRESS = 'office_address'
_FIELD_OFFICE_PHONE = 'office_phone'
_FIELD_WORK_EMAIL = 'work_email'

_EMAIL_LABEL_WORDS = ('e-mail', 'email')
_PHONE_LABEL_WORDS = ('phone', 'telephone')
# 'business' stays an address word: a bare "BUSINESS:" cell holding the whole
# business-address block is a real corpus shape. It is reached only after the
# email and phone words have been ruled out, which is what makes it safe.
_ADDRESS_LABEL_WORDS = ('address', 'business')

# An allowlist, not a word match, because "name" ends far more metadata labels
# than person labels. Widening it is a one-line edit when a corpus CV carries
# a person label this misses; the substring test it replaces could not be
# narrowed at all.
_PERSON_NAME_LABELS = frozenset({
    'name', 'full name', 'legal name', 'candidate name', 'applicant name',
})


def _classify_contact_label(label: str) -> str | None:
    """Which contact field a source-table label cell names, or None.

    Reads only the part before the first colon, so a cell that carries its own
    value ("Address: 5117 Centre Avenue / Phone: 412-623-7764" in one cell) is
    classified by its label and not by what is embedded in the value. Email is
    the one kind that does not require a colon at all, so a bare "E-mail"
    header cell classifies.

    Matching 'e-mail' as well as 'email' is NEW behaviour, not a preserved
    one: the test this replaces was `'email' in label`, which cannot match
    through the hyphen, so "E-mail:" and "E-Mail Address:" were read as an
    address (the address branch accepted them on 'address', or on nothing) and
    the email branch never saw them. It is also this change's only
    corpus-visible effect -- the 66-CV render A/B reports exactly one changed
    document, NSUJZG_2027_Eil_Robert gaining eil@ohsu.edu from its
    "E-Mail Address: | eil@ohsu.edu" row, which is the half of #730 this
    closes.
    """
    text = ' '.join(label.strip().lower().split())
    head = text.split(':', 1)[0].strip()
    if any(word in head for word in _EMAIL_LABEL_WORDS):
        return _FIELD_WORK_EMAIL
    if ':' not in text:
        return None
    if any(word in head for word in _PHONE_LABEL_WORDS):
        return _FIELD_OFFICE_PHONE
    if any(word in head for word in _ADDRESS_LABEL_WORDS):
        return _FIELD_OFFICE_ADDRESS
    if head in _PERSON_NAME_LABELS:
        return _FIELD_NAME
    return None


#: The section a docx-recovered value would have rendered in -- the only
#: place `_recover_contact_fields_from_docx` writes to.
_PERSONAL_DATA_SECTION_LABEL = "Personal Data"


def _label_word_present(word: str, text: str, pii_fragments: list[str]) -> bool:
    """Whether `word` (already lowercase) labels this entry -- read from
    `text` AND from the pass's own pre-strip `pii_fragments`.

    #821's home-address/phone policy row can consume the WHOLE entry text
    (an entry whose sole content is "Home Address: 12 Elm St" has no other
    delimiter for the label span to stop at), destroying the "home"/
    "office" signal the phone/address classification below depends on
    BEFORE it ever runs -- an entry whose `extracted_fields` value differs
    from the raw text's own (stage 4 adding a city/state the label line
    never had, `test_a_structured_address_naming_both_slots_fills_both_
    rows`'s sibling test) then fails `_from_pii_fragment`'s containment
    check too, so nothing upstream catches it either: `text` reads empty,
    the address falls through the now-blind 'home' check to the OFFICE
    catch-all, and a home address renders in the OFFICE cell -- worse than
    the #442 drop this same file's docstring already calls out. The
    fragments hold the ORIGINAL text `run_pii_pass` matched, exactly what
    is needed to recover the signal."""
    if word in text:
        return True
    return any(word in frag.lower() for frag in pii_fragments)


class _VisaAnswers(NamedTuple):
    """The faculty's answers to the template's two visa rows, or empty."""
    eligibility: str
    visa_type: str


def _visa_slot_answers(entries: list[dict]) -> _VisaAnswers:
    """Read the template's two visa rows back out of the A-coded entries.

    A faculty CV already in the WCM template carries them as label|value
    rows ("Is your eligibility to work in the U.S. based on an employment
    visa?: | No"). Nothing consumed them before #897: the answer was dropped
    and the slot rendered as the template's "Yes/No" placeholder, so a "No"
    read back as unanswered. Matched by the label's two stable words; the
    value is the cell after the first pipe. A placeholder ("Yes/No") or a
    blank cell is treated as no answer.
    """
    eligibility = visa_type = ''
    for entry in entries:
        text = entry.get('text') or ''
        if '|' not in text:
            continue
        label, value = (part.strip() for part in text.split('|', 1))
        label = label.lower()
        if not value or value.lower() == 'yes/no':
            continue
        if 'employment visa' in label:
            eligibility = value
        elif 'visa type' in label:
            visa_type = value
    return _VisaAnswers(eligibility, visa_type)


def _withhold_home_contact(
        withheld: list[WithheldItem], home_address: str | None,
        home_phone: str | None) -> tuple[str | None, str | None]:
    """(home_address, home_phone) with either withheld to None (#821),
    recorded on `withheld` -- the SAME list the document-wide notice
    paragraph and Word comment are built from.

    Unconditional, not a re-check against a PII fragment: `home_address`
    can already have arrived with no text label to match at all -- a
    structured `address: {home_address: ..., office_address: ...}` dict is
    routed by its own key names (`_labels_its_own_address_slots`), not by
    reading the entry's text -- so the #821 policy row in `pii.py` (which
    still closes the Appendix leak for a home-phone-only unconsumed
    orphan, since the template has no home-phone row at all for it to
    reach otherwise) cannot be the only guard on these two destinations.
    `home_phone` never reaches a template cell either way -- this makes
    that a recorded policy fact instead of an accident. Office
    address/phone are unaffected; lifted out of `_fill_personal_data` as a
    pure move (§3.2) so its own length does not carry this block.
    """
    if home_address:
        withheld.append(
            WithheldItem(CAT_HOME_CONTACT, _PERSONAL_DATA_SECTION_LABEL, None))
        home_address = None
    if home_phone:
        withheld.append(
            WithheldItem(CAT_HOME_CONTACT, _PERSONAL_DATA_SECTION_LABEL, None))
        home_phone = None
    return home_address, home_phone


def _withhold_recovered(value: str | None, source_text: str,
                        withheld: list[WithheldItem]) -> str | None:
    """`value`, unless it was lifted out of a protected-data fragment of
    `source_text` -- the docx row or paragraph it came from -- in which
    case None, with the policy category recorded on `withheld`.

    The SAME gate the entry path applies (`_from_pii_fragment`, via
    `_pii_category_of`): #820's own "Related" note says the #550/#730
    recovery must go through it, and it did not -- with `--source-dir`,
    web198 rendered a `Birth Place:` line into the Office-address cell
    because its source table's value cell WAS that line and this scan took
    it verbatim (the doctor lint fired on both arms). Recorded, not just
    dropped, so the notice and the Word comment name it.
    """
    if not value:
        return value
    category = _pii_category_of(value, source_text)
    if category is None:
        return value
    withheld.append(WithheldItem(category, _PERSONAL_DATA_SECTION_LABEL, None))
    return None


def _withhold_recovered_lines(block: str | None, source_text: str,
                              withheld: list[WithheldItem]) -> str | None:
    """A multi-line recovered address with every protected line removed
    (`_withhold_recovered` per line); None when nothing is left. The caller
    tells a genuinely address-empty row apart from a fully-withheld one by
    checking whether `withheld` grew, and stops trying further rows for this
    slot in the latter case (#820 R3 finding 1)."""
    if not block:
        return block
    kept = [line for line in block.split('\n')
            if _withhold_recovered(line, source_text, withheld) is not None]
    return '\n'.join(kept) or None


class _RecoveredContact(NamedTuple):
    """What `_recover_contact_fields_from_docx` found, or was given (#550).

    Five values in one return rather than five positional results a caller
    can silently transpose; `name_is_complete` rides along because the name
    recovery and the contact recovery read the same table rows. The three
    contact fields carry the template's own slot names, so the recovery and
    the entry classifier name the same concept the same way.
    """
    name: str | None
    name_is_complete: bool
    work_email: str | None
    office_phone: str | None
    office_address: str | None


class PersonalDataSection:
    """Section A writers, mixed into `WCMTemplateGenerator`."""

    def _fill_personal_data(self, entries: List[Dict], cv_owner: Dict, document_uid: str,
                            all_entries: List[Dict] = None, original_doc_path: str = None):
        """Fill personal data section.

        Args:
            entries: A-coded entries specifically
            cv_owner: CV owner data if available
            document_uid: Document identifier
            all_entries: All entries from the CV (to search for email if not in A entries)
            original_doc_path: Path to original Word document (for fallback email extraction)
        """
        if self.verbose:
            print("\nFilling Personal Data...")

        # Get name from cv_owner if available
        # Priority: full_name_with_credentials > full_name
        # Note: last_name alone is not considered a complete name (will try fallback)
        name = None
        name_is_complete = False  # Track if we have a full name or just last name
        if cv_owner and cv_owner.get('full_name_with_credentials'):
            # Take only first line (may contain newline + date prepared)
            name = cv_owner['full_name_with_credentials'].split('\n')[0].strip()
            if name:
                name_is_complete = True
        elif cv_owner and cv_owner.get('full_name'):
            name = cv_owner['full_name']
            if name:
                name_is_complete = True

        # Try to extract from A entries (LinkedIn URL, etc.)
        if not name:
            for entry in entries:
                text = entry.get('text', '')
                # Look for LinkedIn URL pattern: linkedin.com/in/firstname-lastname
                linkedin_match = re.search(r'linkedin\.com/in/([a-z]+-[a-z]+)', text.lower())
                if linkedin_match:
                    parts = linkedin_match.group(1).split('-')
                    name = ' '.join(p.title() for p in parts)
                    name_is_complete = True
                    break

        # Collect different types of contact info from A entries
        # The original text contains labels like "Office address:", "Cell phone:", etc.
        work_email = None
        personal_email = None
        office_phone = None
        cell_phone = None
        home_phone = None
        office_address = None
        home_address = None

        # A entries that reach none of the six slots below are consumed by
        # nothing. 'A' is in mapped_codes, so they were then excluded from the
        # appendix too, and vanished. Detected by ablation rather than by
        # re-listing the fields this loop reads, so it cannot drift out of step
        # when the loop learns to read a new one.
        unconsumed = []

        for entry in entries:
            fields = entry.get('extracted_fields', {}) or {}
            text = entry.get('text', '').lower()
            slots_before = (work_email, personal_email, office_phone,
                            cell_phone, office_address, home_address)

            # Values stage 4 lifted out of a protected-personal-data fragment
            # are not contact details and must not reach the template. web07's
            # Office address row renders "Cincinnati, Ohio" today, taken
            # straight from "PLACE OF BIRTH: Cincinnati, Ohio" by the address
            # catch-all below.
            #
            # Read from the #820 pre-render pass (`_pii_pass.py`), not
            # recomputed here: by this point `entry['text']` has already had
            # its PII fragments STRIPPED by that pass, so re-running
            # `_pii_fragments` against it would find nothing. The pass
            # stores what it found -- computed against the ORIGINAL text --
            # on the entry precisely so this check can still answer "did
            # this extracted_fields value come from a PII fragment?"
            pii_fragments = entry.get('_pii_fragments', [])

            # Determine type based on original text labels
            extracted_phone = fields.get('phone')
            extracted_address = fields.get('address')
            extracted_email = (fields.get('email') or
                              fields.get('primary_email') or
                              fields.get('institutional_email') or
                              fields.get('work_email') or
                              fields.get('personal_email'))

            if pii_fragments:
                if _from_pii_fragment(extracted_phone, pii_fragments):
                    extracted_phone = None
                if _from_pii_fragment(extracted_address, pii_fragments):
                    extracted_address = None
                if _from_pii_fragment(extracted_email, pii_fragments):
                    extracted_email = None

            # Classify phone by type
            # Handle case where multiple phones are in one entry (e.g., "Mobile: X  Work: Y")
            if extracted_phone:
                # Check if text contains multiple phone type labels
                has_mobile = 'cell' in text or 'mobile' in text
                has_work = 'office' in text or 'work' in text
                has_home = _label_word_present('home', text, pii_fragments)

                if _labels_its_own_phone_slots(extracted_phone):
                    # A structured phone names its own halves, so trust those
                    # rather than the entry's raw text label (#450). web147's
                    # contact block is labelled "Home" but the dict carries
                    # cell/office/fax; the raw-text routing sent all three to
                    # home_phone, which the WCM template has no row for, so
                    # every number was dropped.
                    cell_phone = cell_phone or _phone_cell_text(extracted_phone, 'cell')
                    office_phone = office_phone or _phone_cell_text(extracted_phone, 'office')
                elif has_mobile and has_work and ';' in str(extracted_phone):
                    # Both types in the same entry -- only the original text
                    # says which number carries which label. Each scan runs
                    # lazily from its own label and never crosses a ';' or a
                    # newline, so it takes the first number AFTER that label;
                    # what it replaced could not cross a '(' instead, which
                    # worked only because its one shape started with one.
                    mobile_match = re.search(
                        rf'(?:cell|mobile)[^;\n]*?({_PHONE_NUMBER_PATTERN})',
                        text, re.IGNORECASE)
                    work_match = re.search(
                        rf'(?:work|office)[^;\n]*?({_PHONE_NUMBER_PATTERN})',
                        text, re.IGNORECASE)
                    if mobile_match and not cell_phone:
                        cell_phone = mobile_match.group(1)
                    if work_match and not office_phone:
                        office_phone = work_match.group(1)
                elif has_mobile:
                    if not cell_phone:
                        cell_phone = _phone_cell_text(extracted_phone, 'cell')
                elif has_home:
                    # The WCM template has exactly two phone rows, Office
                    # telephone and Cell phone -- there is no home row, so
                    # home_phone is written and never read, and a home-labelled
                    # number is deliberately not rendered.
                    #
                    # Routing it to the office row instead was tried and is
                    # WRONG: on web113 the HOME entry is processed before the
                    # BUSINESS entry, so the office row took the home number and
                    # the real business number was then skipped as already-set.
                    # Recovering a home phone needs a template row to put it in,
                    # not a slot to squat in.
                    if not home_phone:
                        home_phone = _phone_cell_text(extracted_phone, 'home')
                elif has_work or not office_phone:
                    if not office_phone:
                        office_phone = _phone_cell_text(extracted_phone, 'office')

            # Classify address by type
            if extracted_address:
                if _labels_its_own_address_slots(extracted_address):
                    # The dict names its own halves, so fill both slots from it
                    # instead of forcing the whole thing into whichever one the
                    # raw text happened to label (#442). A dict that names no
                    # slot is one address and falls through to the raw-text
                    # routing below, same as a string.
                    if not home_address:
                        home_address = _address_cell_text(extracted_address, 'home')
                    if not office_address:
                        office_address = _address_cell_text(extracted_address, 'office')
                elif _label_word_present('home', text, pii_fragments):
                    if not home_address:
                        home_address = _address_cell_text(extracted_address, 'home')
                elif 'office' in text or 'work' in text or 'business' in text or not office_address:
                    if not office_address:
                        office_address = _address_cell_text(extracted_address, 'office')

            # Classify email by type
            if extracted_email:
                if 'personal' in text:
                    if not personal_email:
                        personal_email = extracted_email
                elif not work_email:
                    work_email = extracted_email

            # Also check entry text for email pattern (fallback)
            if not work_email and not personal_email:
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', entry.get('text', ''))
                if email_match and not _from_pii_fragment(email_match.group(0),
                                                          pii_fragments):
                    work_email = email_match.group(0)

            # home_phone is deliberately absent from this tuple: it is written
            # and never read (the template has no home-phone row), so an entry
            # that sets only home_phone reaches the document nowhere and is
            # genuinely unconsumed. Two corpus entries are in exactly that
            # state.
            if (work_email, personal_email, office_phone, cell_phone,
                    office_address, home_address) == slots_before:
                unconsumed.append(entry)

        # Handed to the post-render recovery pass, which is the only point at
        # which "did this content reach the document?" can actually be asked.
        self._unconsumed_personal_data = unconsumed

        # The duplicate-email guard below applies only to a value one of the
        # two scans produced, and with every path writing the same slot this
        # is the only thing left that tells them apart.
        work_email_from_entries = bool(work_email)

        # If still no email, search all entries for email patterns.
        #
        # Found while fixing #550: this loop feeds the same `work_email` slot
        # the table fill reads and applied no PII filter, unlike the per-entry
        # loop above (:126-132) which blocks exactly this shape. Before #550
        # the value was discarded like everything else the fallback found, so
        # the gap was latent; test_email_regex_fallback_does_not_harvest_from_a_pii_fragment
        # already pins that path and caught this one the moment it went live.
        if not work_email and all_entries:
            for entry in all_entries:
                text = entry.get('text', '')
                # Read from the #820 pass, not recomputed -- see the
                # per-entry loop above for why.
                entry_pii_fragments = entry.get('_pii_fragments', [])
                # Look for email pattern
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
                if email_match and not _from_pii_fragment(email_match.group(0), entry_pii_fragments):
                    work_email = email_match.group(0)
                    break

                # Also check extracted fields
                fields = entry.get('extracted_fields', {}) or {}
                candidate = fields.get('email') or fields.get('primary_email')
                if candidate and not _from_pii_fragment(candidate, entry_pii_fragments):
                    work_email = candidate
                    break

        # Fallback: read personal data from original Word document.
        # This handles cases where personal data is in tables
        # (e.g., NAME: | Patricia Opresko). The scan lives in
        # `_recover_contact_fields_from_docx` below; it writes the same three
        # slots this function has been filling all along, and returns them
        # unchanged when there is nothing left to recover or nothing to read.
        recovered = self._recover_contact_fields_from_docx(
            original_doc_path, document_uid, name, name_is_complete,
            work_email, office_phone, office_address)
        name = recovered.name
        name_is_complete = recovered.name_is_complete
        work_email = recovered.work_email
        office_phone = recovered.office_phone
        office_address = recovered.office_address

        # A recovered email that is already `personal_email` must not also
        # duplicate into work_email (#550 round 1, XLYVYA_sample_vasquez_cv):
        # its sole address is routed to personal_email by the per-entry loop
        # above because that entry's text contains the "Personal Data"
        # section header, not because the person gave two addresses. Both
        # scans that can fill an empty work-email slot rediscover that same
        # address, and without this guard it renders twice.
        #
        # `work_email_from_entries` keeps the guard off a value the per-entry
        # classifier put there itself: a CV naming one address as both its
        # personal and its work email renders it in both rows, which is what
        # the trailing `work_email = work_email or email` restored before the
        # two sets of names were collapsed into one.
        if not work_email_from_entries and work_email and work_email == personal_email:
            work_email = None

        # Fallback to document_uid for name
        if not name:
            name = _extract_name_from_uid(document_uid)

        # Find and fill Name field
        name_idx = self._find_paragraph_with_text("Name:")
        if name_idx is not None:
            para = self.doc.paragraphs[name_idx]
            para.clear()
            run = para.add_run(f"Name: {name}")
            _set_font(run, bold=True)

        # Fill Date of preparation with today's date
        date_idx = self._find_paragraph_with_text("Date of preparation")
        if date_idx is not None:
            para = self.doc.paragraphs[date_idx]
            para.clear()
            today = datetime.now().strftime("%B %-d, %Y")  # e.g., "February 1, 2026"
            run = para.add_run(f"Date of preparation: {today}")
            _set_font(run)
        home_address, home_phone = _withhold_home_contact(self._pii_result.withheld, home_address, home_phone)  # #821
        # Fill email, phone, and address in the PERSONAL DATA table (Table 1).
        # Lifted out to `_write_personal_data_table_cells` (#820 R3, pure
        # move -- §3.2) so this function's own length does not carry it.
        self._write_personal_data_table_cells(
            entries, office_address, office_phone, work_email, home_address,
            cell_phone, personal_email)

    def _write_personal_data_table_cells(
            self, entries: list[dict], office_address: str | None,
            office_phone: str | None, work_email: str | None,
            home_address: str | None, cell_phone: str | None,
            personal_email: str | None) -> None:
        """Write the six contact slots and the two visa rows into Table 1,
        once `_fill_personal_data` has resolved every contact value (entries,
        then the docx recovery fallback).

        Table 1 structure: Office address, Office telephone, Work email, Home
        address, Cell phone, Personal email, the employment-visa question,
        the visa type -- located by its "Work email:" cell rather than by
        index. Split out of `_fill_personal_data` as a PURE move (#820 R3,
        §3.2); the visa rows were added in #897.
        """
        personal_data_table = self._find_table_with_cell_text("Work email:")
        if personal_data_table is None:
            return
        visa = _visa_slot_answers(entries)
        for row in personal_data_table.rows:
            cell_text = row.cells[0].text.strip().lower()

            # Office address
            if office_address and 'office address' in cell_text:
                formatted_address = office_address.replace('\t', '\n').replace('; ', '\n').replace(';', '\n')
                _set_cell_text(row.cells[1], formatted_address)
                self.stats['entries_inserted'] += 1

            # Office telephone
            if office_phone and 'office telephone' in cell_text:
                _set_cell_text(row.cells[1], office_phone)
                self.stats['entries_inserted'] += 1

            # Work email
            if work_email and 'work email' in cell_text:
                _set_cell_text(row.cells[1], work_email)
                self.stats['entries_inserted'] += 1

            # Home address
            if home_address and 'home address' in cell_text:
                formatted_address = home_address.replace('\t', '\n').replace('; ', '\n').replace(';', '\n')
                _set_cell_text(row.cells[1], formatted_address)
                self.stats['entries_inserted'] += 1

            # Cell phone
            if cell_phone and 'cell phone' in cell_text:
                _set_cell_text(row.cells[1], cell_phone)
                self.stats['entries_inserted'] += 1

            # Personal email
            if personal_email and 'personal email' in cell_text:
                _set_cell_text(row.cells[1], personal_email)
                self.stats['entries_inserted'] += 1

            # The template's two visa rows (#897): the faculty's own answers,
            # written over the "Yes/No" placeholder. The PII pass does not
            # withhold these rows -- its visa label is start-anchored and
            # this label starts "Is your eligibility ..." (#821 lists visa
            # status as rendering today).
            if visa.eligibility and 'employment visa' in cell_text:
                _set_cell_text(row.cells[1], visa.eligibility)
                self.stats['entries_inserted'] += 1
            if visa.visa_type and 'visa type' in cell_text:
                _set_cell_text(row.cells[1], visa.visa_type)
                self.stats['entries_inserted'] += 1

    def _recover_contact_fields_from_docx(
            self, original_doc_path: str | None, document_uid: str,
            name: str | None, name_is_complete: bool, work_email: str | None,
            office_phone: str | None,
            office_address: str | None) -> _RecoveredContact:
        """Re-open the ORIGINAL .docx and recover contact fields still missing.

        Step 3 of the module docstring, lifted out of `_fill_personal_data` so
        that function's length does not rise (§3): the values flow in and out
        as arguments instead of as enclosing locals, under the template's own
        slot names. Each recovery is guarded by `if not <field>`, so nothing
        already found by the entry classifier is overwritten here, and a
        document that is complete, absent or unreadable returns every argument
        unchanged.

        `original_doc_path` reaches this method only from
        `scripts/render_gate.py --source-dir` and this package's tests today
        -- see the module docstring for why no live driver passes one.
        """
        given = _RecoveredContact(name, name_is_complete, work_email,
                                  office_phone, office_address)

        # Opening the document is the expensive part and there is no point
        # paying it for a record this scan cannot add to. All four are
        # checked, not just the three contact values: the table scan recovers
        # a name too, so a record complete but for its name still opens.
        if (name_is_complete and work_email and office_phone
                and office_address):
            return given

        # is_file(), not exists(): a directory named *.docx passes exists()
        # and then fails inside python-docx with a FileNotFoundError for a
        # part it could not read, which reads as a corrupt document rather
        # than as the wrong kind of path.
        if not original_doc_path or not Path(original_doc_path).is_file():
            return given

        try:
            original_doc = Document(original_doc_path)
        except _UNREADABLE_SOURCE_ERRORS as e:
            # Ungated -- was verbose-only, so a parsing failure on this
            # fallback left no trace at all outside a verbose run (#550).
            # exc_info=True keeps the traceback out of the message string
            # itself, which is what a downstream reader would otherwise be
            # tempted to regex (#7.1 -- print() is a parsed contract; a
            # logger record is not). Only the open is guarded: an exception
            # out of the scan below is a defect in this method, and counting
            # it as a source-document problem would hide it.
            logger.warning(
                "Could not read original document for personal data "
                "fallback (uid=%s, path=%s): %s",
                document_uid, original_doc_path, e, exc_info=True,
            )
            self.stats['personal_data_fallback_failed'] = (
                self.stats.get('personal_data_fallback_failed', 0) + 1
            )
            return given

        # Every value this scan takes is gated by provenance against the
        # docx line it came from and, when withheld, recorded on the pass
        # result `generate()` holds -- the same list the notice and the
        # Word comment are built from.
        withheld = self._pii_result.withheld

        # Table scan lifted out to `_recover_contact_fields_from_table_rows`
        # (#820 R3, pure move -- §3.2): identical body, no behaviour change.
        (name, name_is_complete, work_email, office_phone,
         office_address) = self._recover_contact_fields_from_table_rows(
            original_doc, name, name_is_complete, work_email,
            office_phone, office_address, withheld)

        # Also check paragraphs for email (if not found in tables). Every
        # paragraph, not the first twenty: where the contact block sits is a
        # layout property, not a paragraph count, and an email in paragraph 21
        # used to be lost.
        if not work_email:
            for para in original_doc.paragraphs:
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+',
                                        para.text.strip())
                if email_match:
                    work_email = _withhold_recovered(
                        email_match.group(0), para.text, withheld)
                    if self.verbose and work_email:
                        print(f"  Found email from paragraph: {work_email}")
                    if work_email:
                        break

        return _RecoveredContact(name, name_is_complete, work_email,
                                 office_phone, office_address)

    def _recover_contact_fields_from_table_rows(
            self, original_doc: _WordDocument, name: str | None, name_is_complete: bool,
            work_email: str | None, office_phone: str | None,
            office_address: str | None, withheld: list[WithheldItem]
    ) -> tuple[str | None, bool, str | None, str | None, str | None]:
        """The table-row half of `_recover_contact_fields_from_docx`'s scan
        (common format: label in col 0, value in col 1) -- split out as a
        PURE move (#820 R3, §3.2): identical body, no behaviour change. See
        that method's docstring for the overall recovery contract.
        """
        # Set once an office_address-classified row's content was policy-
        # withheld in full (#820 R3 finding 1): web198's first office_address
        # row is a withheld birth-place line, and without this flag the
        # `not office_address` guard below stays true and a LATER
        # office_address-classified row (an unrelated "Email address:" line)
        # fills the slot instead. Once this slot has been denied, it renders
        # empty for the rest of the document rather than taking whatever
        # office_address row comes next.
        office_address_withheld = False

        # Every table, not the first three: which table holds the contact
        # block is a layout property of the CV, and a cover or education table
        # in front of it used to cost the whole block.
        for table in original_doc.tables:
            for row in table.rows:
                if len(row.cells) < 2:
                    continue
                label_cell = row.cells[0]
                # A gridSpan label cell repeats itself across row.cells:
                # python-docx hands back the SAME cell object for every column
                # a merge spans, so cells[1] can equal cells[0] instead of
                # holding the value. NSUJZG_2027_Eil_Robert's "Professional
                # Address:" row is exactly this (label merged across columns
                # 0-1, value in column 2) -- read past however many duplicate
                # cells the merge produced to the first one that differs.
                value_cell = row.cells[1]
                for candidate_cell in row.cells[1:]:
                    if candidate_cell.text.strip() != label_cell.text.strip():
                        value_cell = candidate_cell
                        break
                value = value_cell.text.strip()
                field = _classify_contact_label(label_cell.text)
                # The row as one text, for the protected-data gate below:
                # a value is denied by provenance against the fragments of
                # the line it came from, exactly as the entry path does.
                row_text = f"{label_cell.text}\t{value}"

                # Extract name if not yet found (or only have last name)
                if field == _FIELD_NAME and not name_is_complete:
                    if value and len(value) > 2:
                        recovered_name = _withhold_recovered(value, row_text, withheld)
                        if recovered_name is not None:
                            name = recovered_name
                            name_is_complete = True
                            if self.verbose:
                                print(f"  Found name from table: {name}")

                # Extract address if not yet found
                # Note: Business address cells often contain embedded phone/fax/email
                elif (field == _FIELD_OFFICE_ADDRESS and not office_address
                        and not office_address_withheld):
                    if value and len(value) > 5:
                        block_address, block_phone, block_email = (
                            self._parse_address_block(
                                value, office_phone, work_email))
                        withheld_before = len(withheld)
                        office_address = _withhold_recovered_lines(
                            block_address, row_text, withheld)
                        if not office_address and len(withheld) > withheld_before:
                            # Every address line this row supplied was
                            # policy-denied -- stop, don't let a later
                            # office_address row fill the slot instead.
                            office_address_withheld = True
                        # Only a value the block itself supplied is gated:
                        # one already classified from the A entries is not
                        # this row's to withhold.
                        if not office_phone:
                            office_phone = _withhold_recovered(block_phone, row_text, withheld)
                        if not work_email:
                            work_email = _withhold_recovered(block_email, row_text, withheld)

                # Extract phone if not yet found
                elif field == _FIELD_OFFICE_PHONE and not office_phone:
                    if value and len(value) > 5:
                        office_phone = _withhold_recovered(value, row_text, withheld)
                        if self.verbose and office_phone:
                            print(f"  Found phone from table: {office_phone}")

                # Extract email if not yet found
                elif field == _FIELD_WORK_EMAIL and not work_email:
                    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', value)
                    if email_match:
                        work_email = _withhold_recovered(
                            email_match.group(0), row_text, withheld)
                        if self.verbose and work_email:
                            print(f"  Found email from table: {work_email}")

        return name, name_is_complete, work_email, office_phone, office_address

    def _parse_address_block(
            self, value: str, office_phone: str | None,
            work_email: str | None) -> tuple[str, str | None, str | None]:
        """Split a business-address cell into address / phone / email.

        A "BUSINESS ADDRESS:" cell is one cell, not three rows: it carries the
        street address with Phone:, Fax: and E-mail: lines inside it. Every one
        of those lines is consumed as metadata whatever else is already known,
        and only the ASSIGNMENT is conditional -- keying the skip on "did we
        take this value" left the E-mail line standing in the rendered Office
        address whenever an email had already been found somewhere else, which
        put an email address in an address field.

        Returns the address and the two values it was given, each replaced
        only if it was empty and the block supplied one.
        """
        address_lines = []
        for line in value.split('\n'):
            line = line.strip()
            line_lower = line.lower()

            # Extract phone if embedded in address
            if 'phone:' in line_lower or 'phone\t' in line_lower:
                phone_match = re.search(r'(?:phone[:\s]+)(.+)', line, re.IGNORECASE)
                if phone_match and not office_phone:
                    office_phone = phone_match.group(1).strip()
                    if self.verbose:
                        print(f"  Found phone from address block: {office_phone}")
                continue

            # Extract email if embedded in address
            if ('e-mail:' in line_lower or 'email:' in line_lower
                    or 'e-mail\t' in line_lower):
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', line)
                if email_match and not work_email:
                    work_email = email_match.group(0)
                    if self.verbose:
                        print(f"  Found email from address block: {work_email}")
                continue

            # Skip fax lines
            if 'fax:' in line_lower or 'fax\t' in line_lower:
                continue

            # Keep other lines as address
            if line:
                address_lines.append(line)

        address = '\n'.join(address_lines)
        if self.verbose:
            print(f"  Found address from table: {address[:50]}...")
        return address, office_phone, work_email
