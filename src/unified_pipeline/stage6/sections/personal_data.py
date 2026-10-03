"""Section A: personal data -- name, addresses, phones, emails (#398).

The shortest section in the template and one of the longest writers, because
almost nothing about a CV's contact block is structured. The work is in order:

1. Resolve the name. `cv_owner` first, then a LinkedIn slug in the A entries,
   then the document uid via `_extract_name_from_uid` (empty for a run id, #457).
   A bare surname does not count as complete and keeps the fallbacks running.
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
above). If it goes stale again, the other copies are
`stage_6_word_template.py`'s SAMPLE_CV_DIR constant, `run_stage6()`'s
docstring, `scripts/render_gate.py`'s module docstring and
`docs/guides/render-doctor-gates.md`.

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
from collections.abc import Callable
from dataclasses import dataclass
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
    from docx.table import _Row as _TableRow
    from lxml.etree import XMLSyntaxError
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import _set_cell_text, _set_font
from ..normalization import (
    CAT_HOME_CONTACT,
    CAT_PLACE_OF_BIRTH,
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
# Home-labelled rows (#730): never a candidate for an Office/Work slot.
# Address and phone are withheld by policy (#821, `CAT_HOME_CONTACT`); a home
# email is NOT withheld (#821: personal email renders) but this scan has no
# personal-email slot, so it is skipped rather than rendered as Work email.
_FIELD_HOME_ADDRESS = 'home_address'
_FIELD_HOME_PHONE = 'home_phone'
_FIELD_HOME_EMAIL = 'home_email'
_HOME_FIELDS = frozenset({_FIELD_HOME_ADDRESS, _FIELD_HOME_PHONE,
                          _FIELD_HOME_EMAIL})
_FIELD_OFFICE_PHONE = 'office_phone'
_FIELD_WORK_EMAIL = 'work_email'

_EMAIL_LABEL_WORDS = ('e-mail', 'email')
# An email line inside an address cell (#730). "Email address:" is included:
# with a home row now skipped, web198's Business Address cell is reached and
# opens with that line, which would otherwise render as the Office address.
_EMAIL_LINE_MARKERS = ('e-mail:', 'email:', 'e-mail\t', 'email address:',
                       'e-mail address:')
_PHONE_LABEL_WORDS = ('phone', 'telephone')
# 'business' stays an address word: a bare "BUSINESS:" cell holding the whole
# business-address block is a real corpus shape. It is reached only after the
# email and phone words have been ruled out, which is what makes it safe.
_ADDRESS_LABEL_WORDS = ('address', 'business')
# Whole words only: "Homepage address" and "Homeland Security address" are not
# home addresses. "Home" wins over "business"/"office" in the same label
# ("Home/Office Address:"): an ambiguous label costs an empty Office cell, the
# opposite mistake renders a home address. "Permanent" is a home qualifier for
# an ADDRESS only (US CV convention); "Permanent email" is an alumni address.
_FAX_LABEL = re.compile(r'\bfax\b')
_HOME_LABEL = re.compile(r'\b(?:home|residence|residential)\b')
_HOME_ADDRESS_LABEL = re.compile(r'\b(?:home|residence|residential)\b')
_PERMANENT_LABEL = re.compile(r'\bpermanent\b')
# "Permanent Office/Business/Work Address:" is an office address: those words
# outrank "permanent" (but not "home": "Home/Office Address:" stays home).
_OFFICE_QUALIFIER = re.compile(r'\b(?:office|business|work|professional)\b')
# An embedded office-phone value is only a number when it has this many digits
# ("Phone: (office) | 212-555-0100" and "Phone: ext. 1234" carry none).
_MIN_PHONE_DIGITS = 7
# An extension's digits are not part of the number ("x1234567").
_EXTENSION = re.compile(r'\b(?:x|ext|extension)\.?\s*\d+', re.IGNORECASE)
# One recovered phone value can carry several numbers. Separators: ; , newline
# and a slash with spaces around it (a bare slash is inside "212/555-0100").
_PHONE_SEGMENT_SPLIT = re.compile(r'[;,\n]|\s/\s')
# A segment naming a home number: whole-word home / res / residence /
# residential, "(h)" / "(h/o)" (a shared home line is still a home number),
# or a bare "H:" -- not "(hosp)", "Hospital", "Homer St",
# "Hr:". Personal/cell numbers are not protected by #821 and are kept.
_HOME_PHONE_MARKER = re.compile(
    r'\b(?:home|res|residence|residential)\b|\(\s*(?:h|h\s*/\s*o|o\s*/\s*h)\s*\)'
    r'|(?<![a-z])h\s*:',
    re.IGNORECASE)

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
        return (_FIELD_HOME_EMAIL if _HOME_LABEL.search(head)
                else _FIELD_WORK_EMAIL)
    if ':' not in text:
        return None
    if any(word in head for word in _PHONE_LABEL_WORDS):
        return (_FIELD_HOME_PHONE if _HOME_LABEL.search(head)
                else _FIELD_OFFICE_PHONE)
    if _FAX_LABEL.search(head):
        # No slot takes a fax number; "Business Fax:" must not reach the
        # address branch through its 'business' word (#730, web198).
        return None
    if any(word in head for word in _ADDRESS_LABEL_WORDS):
        if _HOME_ADDRESS_LABEL.search(head) or (
                _PERMANENT_LABEL.search(head)
                and not _OFFICE_QUALIFIER.search(head)):
            return _FIELD_HOME_ADDRESS
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


# #946: the label written IMMEDIATELY before a phone number, which outranks
# the block heading the number sits under. The block-level tests below read
# only whole words ('cell', 'mobile', 'home') anywhere in the entry, so a
# "HOME ADDRESS" block carrying "(c) 212.555.0100" routed its cell number to
# home_phone, which #821 withholds -- the owner's own Cell phone row stayed
# empty. Single letters count only in the two shapes that make them a label,
# "(c)" or "c:"/"c." -- a bare "c" or "m" is too often an initial, a suite or
# a unit to route on, and even "m:"/"c." is not a label straight after a word
# ("room m:", "building c."), so a single letter must not follow a letter or
# a word and one space. A combined "cell/home" label is a cell label, as it
# was before #946. Only cell and home are recognised here: cell because it
# is the row the block heading was hiding, home because a number labelled
# "(h)" must stay withheld even inside a block that also says "cell".
#
# Built from named parts so each rule above is one line to read or change.
# A single letter is a label only when it does not follow a letter, or a
# word and one space ("room m:", "building c.").
_NOT_AFTER_A_WORD = r'(?<![a-z])(?<![a-z] )'
_CELL_LETTER_LABEL = rf'\([cm]\):?|{_NOT_AFTER_A_WORD}[cm][:.]'
_HOME_LETTER_LABEL = rf'\(h\):?|{_NOT_AFTER_A_WORD}h[:.]'
_CELL_WORDS = r'cell(?:ular)?|mobile'
# "cell/home" and "mobile/work" are still cell labels.
_COMBINED_WITH_ANOTHER_KIND = r'(?:\s*/\s*(?:home|work|office))?'
_CELL_NOUN = r'(?:\s*(?:phone|tel|no))?'
_HOME_NOUN = r'(?:\s*(?:phone|telephone|tel|no))?'
_LABEL_PUNCTUATION = r'\.?:?'
_CELL_WORD_LABEL = (rf'\b(?:{_CELL_WORDS}|mob)\b{_COMBINED_WITH_ANOTHER_KIND}'
                    rf'{_CELL_NOUN}{_LABEL_PUNCTUATION}')
_HOME_WORD_LABEL = rf'\bhome\b{_HOME_NOUN}{_LABEL_PUNCTUATION}'
_CELL_LABEL_BEFORE_NUMBER = rf'(?:{_CELL_LETTER_LABEL}|{_CELL_WORD_LABEL})'
_HOME_LABEL_BEFORE_NUMBER = rf'(?:{_HOME_LETTER_LABEL}|{_HOME_WORD_LABEL})'
_LABELLED_NUMBER_RE = re.compile(
    rf'(?:(?P<cell>{_CELL_LABEL_BEFORE_NUMBER})|(?P<home>{_HOME_LABEL_BEFORE_NUMBER}))'
    rf'\s*(?P<number>{_PHONE_NUMBER_PATTERN})',
    re.IGNORECASE,
)
# A cell label written AFTER the number, "212-555-0100 (cell)". It outranks a
# home label before the number, as the block-level 'cell' word did before
# #946. Words only, and never when another number follows: in
# "(o) 212-555-0100 (c) 917-555-0101" the "(c)" labels the number after it.
# A colon makes it a label for what follows: "(office): 212-555-0100
# (cell): 917-555-0101" labels the second number cell, not the first.
_NO_NUMBER_FOLLOWS = r'(?![ \t]*:)(?!\s*[+(]?\d)'
_CELL_LABEL_AFTER_NUMBER_RE = re.compile(
    rf'(?P<number>{_PHONE_NUMBER_PATTERN})\s*\((?:{_CELL_WORDS})\){_NO_NUMBER_FOLLOWS}',
    re.IGNORECASE,
)
# ... except in a run whose every number carries its label AFTER it (#1222,
# EBYSBC EQGGRB-04): in "212-555-0100 (cell) 917-555-0101 (office)" the
# next number has its own trailing label, so "(cell)" labels the number
# before it. A "(c)" straight before a number with no trailing label after
# that number still labels the number after it, as above.
_TRAILING_KIND_LABEL = r'\((?:cell(?:ular)?|mobile|office|work|home|business|lab|fax)\)'
_CELL_LABEL_IN_A_TRAILING_RUN_RE = re.compile(
    rf'(?P<number>{_PHONE_NUMBER_PATTERN})\s*\((?:{_CELL_WORDS})\)'
    rf'(?=\s*{_PHONE_NUMBER_PATTERN}\s*{_TRAILING_KIND_LABEL})',
    re.IGNORECASE,
)
_PHONE_LABEL_CELL = 'cell'
_PHONE_LABEL_HOME = 'home'
# A cell keyword counts at the block level only when it is written as a label
# (#1222): "(cell)", "Cellphone:", "Cell - 212-...", "Mobile Ph:", "Cell
# (preferred):", or trailing a number "212-555-0100 cell" -- never as a word
# inside an organisation or department name ("Department of Cellular Example
# Studies", "Cell Biology"), which routed the office phone to Cell phone.
# Blanks inside a label stay on one line.
_CELL_KEYWORD = rf'(?:{_CELL_WORDS}|mob)'
_CELL_BLOCK_COMBINED = r'(?:[ \t]*/[ \t]*(?:home|work|office|text|sms))?'
_CELL_BLOCK_NOUNS = r'(?:[ \t]*(?:phone|telephone|tel|ph|no|number|#))*'
_CELL_BLOCK_QUALIFIER = r'(?:[ \t]*\([^)\n]{0,20}\))?'
_CELL_BLOCK_LABEL_END = r'[ \t]*(?:[:.]|(?:[-\u2013\u2014][ \t]*)?(?=[+(]?\d))'
_CELL_KEYWORD_AS_LABEL_RE = re.compile(
    rf'\({_CELL_KEYWORD}\)'
    rf'|\b{_CELL_KEYWORD}{_CELL_BLOCK_COMBINED}{_CELL_BLOCK_NOUNS}'
    rf'{_CELL_BLOCK_QUALIFIER}{_CELL_BLOCK_LABEL_END}'
    rf'|\d\)?[ ]*[-,;]?[ ]*{_CELL_KEYWORD}\b(?![ ]+[a-z])',
    re.IGNORECASE,
)
# A work-contact label names its own slot (#1222): an address noun for the
# Office address, an address or phone noun for the Office telephone (a work
# address block carries its phone). The noun must directly follow the work
# word, so "Business School, Tel." and "Office Fax Number" are not labels, and
# a residence or mailing line never ranks an address. The phone label leaves
# out "business" because the slot's has_work gate never reaches it.
_WORK_ADDRESS_LABEL_RE = re.compile(
    r'\b(?:office|work|business)\s+address\b',
    re.IGNORECASE)
_WORK_PHONE_LABEL_RE = re.compile(
    r'\b(?:office|work)\s+(?:address|phone|telephone|tel)\b',
    re.IGNORECASE)
# A number found in the text is the extracted one when the two digit strings
# agree once a country prefix (at most three digits, ITU E.164) is ignored.
# Below the minimum the suffix test could pair two unrelated short numbers;
# without the prefix bound a value holding two numbers would pair with the
# second one it ends in.
_MIN_PHONE_DIGITS_TO_PAIR = 7
_MAX_COUNTRY_CODE_DIGITS = 3
_NON_DIGIT_RE = re.compile(r'\D')

# One stage-4 field value. `coerce_field_value_types` joins a list of scalars
# into a string and leaves everything else as the LLM wrote it: a JSON number,
# a dict naming its own slots (#450), a list of dicts.
type _JsonValue = str | int | float | bool | list[_JsonValue] | dict[str, _JsonValue]


def _digits(value: str) -> str:
    """`value` with every non-digit removed."""
    return _NON_DIGIT_RE.sub('', value)


@dataclass(frozen=True, slots=True)
class _PhoneNumber:
    """One phone number by its digits -- what two spellings of the same
    number share ("212.555.0142" and "+1 (212) 555-0142")."""
    digits: str

    @classmethod
    def parse(cls, value: _JsonValue) -> _PhoneNumber:
        """A dict or list value stringifies whole, so one holding several
        numbers pairs with no single number in the text."""
        return cls(_digits(str(value)))

    def is_same_number(self, other: _PhoneNumber) -> bool:
        """Whether both are the same number once a country prefix on either
        side is ignored."""
        return (len(self.digits) >= _MIN_PHONE_DIGITS_TO_PAIR
                and len(other.digits) >= _MIN_PHONE_DIGITS_TO_PAIR
                and abs(len(self.digits) - len(other.digits)) <= _MAX_COUNTRY_CODE_DIGITS
                and (self.digits.endswith(other.digits)
                     or other.digits.endswith(self.digits)))


def _nearest_phone_label(phone: _PhoneNumber, text: str) -> str | None:
    """`_PHONE_LABEL_CELL` or `_PHONE_LABEL_HOME` when that label sits
    immediately before `phone`'s number in `text`, else None.

    None means "no label at the number", not "not a cell": the caller then
    falls back to the block-level words. A phone value holding several
    numbers pairs with no single number in the text and returns None."""
    if any(phone.is_same_number(_PhoneNumber.parse(match.group('number')))
           for pattern in (_CELL_LABEL_AFTER_NUMBER_RE, _CELL_LABEL_IN_A_TRAILING_RUN_RE)
           for match in pattern.finditer(text)):
        return _PHONE_LABEL_CELL
    for match in _LABELLED_NUMBER_RE.finditer(text):
        if phone.is_same_number(_PhoneNumber.parse(match.group('number'))):
            return _PHONE_LABEL_CELL if match.group('cell') else _PHONE_LABEL_HOME
    return None


def _cell_and_home_signals(phone: _JsonValue, text: str,
                           pii_fragments: list[str]) -> tuple[bool, bool]:
    """(is cell, is home) for one stage-4 phone value -- the label nearest
    the number when there is one (#946), else the entry's block-level words."""
    number = _PhoneNumber.parse(phone)
    nearest = _nearest_phone_label(number, text)
    if nearest is not None:
        return nearest == _PHONE_LABEL_CELL, nearest == _PHONE_LABEL_HOME
    return (bool(_CELL_KEYWORD_AS_LABEL_RE.search(text))
            and not _cell_label_is_at_another_number(phone, text),
            _label_word_present('home', text, pii_fragments))


def _cell_label_is_at_another_number(phone: _JsonValue, text: str) -> bool:
    """Whether the entry's cell label sits right before a number that is
    not `phone`, so it is that number's label (the #952 rule; #1222, EBYSBC
    ZCTARO-06): in "Tel: 317-555-0100 Cell: 317-555-0101" the Tel number
    took the Cell phone row from the block-level 'cell'. Only a value
    holding one number: a joined "a; b" pairs with no single number, and
    the mixed branch in `_route_phone` labels each of its parts."""
    if not isinstance(phone, str) or len(_PHONE_SEGMENT_SPLIT.split(phone)) > 1:
        return False
    # `_nearest_phone_label` found no cell label at `phone`, so any number
    # these match is another one.
    return (any(match.group('cell') for match in _LABELLED_NUMBER_RE.finditer(text))
            or _CELL_LABEL_AFTER_NUMBER_RE.search(text) is not None)


#: The words that place an entry's address at a workplace: the Office
#: catch-all in `_fill_personal_data` routes on them, and an entry carrying
#: one is never read as a home banner (`_address_is_home`).
_WORK_PLACE_WORDS = ('office', 'work', 'business')


def _labels_a_number_home(phone: _JsonValue, text: str) -> bool:
    """Whether the entry labels one of its phone numbers home: a home-marked
    segment of the stage-4 value ("555-0100 (h); 555-0101 (w)",
    `_HOME_PHONE_MARKER`), or a home label right before a number in the text
    ("Ph (h): 555-0100", `_LABELLED_NUMBER_RE`)."""
    if isinstance(phone, str) and _HOME_PHONE_MARKER.search(phone):
        return True
    return any(match.group('home') for match in _LABELLED_NUMBER_RE.finditer(text))


def _address_is_home(text: str, pii_fragments: list[str], phone: _JsonValue) -> bool:
    """Whether the entry's address goes to the home slot, which #821
    withholds: the entry says "home", or it labels one of its numbers home
    and names no workplace (`_WORK_PLACE_WORDS`). A banner that gives a home
    number beside an unlabelled street address gives the home address; the
    Office catch-all rendered it as the Office address (#1223, EBYSBC
    MRJDWE-01)."""
    if _label_word_present('home', text, pii_fragments):
        return True
    return (_labels_a_number_home(phone, text)
            and not any(word in text for word in _WORK_PLACE_WORDS))


#: A birth word in an entry's text or its withheld fragments: "Born",
#: "Date of Birth", "Birthplace", "DOB".
_BIRTH_WORD_RE = re.compile(r'\b(?:born\b|birth|dob\b)', re.IGNORECASE)


def _address_is_birthplace(text: str, pii_fragments: list[str]) -> bool:
    """Whether the entry's only address is the place on its birth line, so
    it fills no Office cell (#1223, EBYSBC EQADVR-02): a birth word in the
    text or in a fragment the pii pass cut, and no address or workplace word
    (`_WORK_PLACE_WORDS`) to say the value is a contact address. "Born:
    <date>" on one line and the place on the next, or "Born <date>; <place>"
    with no colon, leave the place in the residual, and the Office catch-all
    rendered it as the Office address."""
    if any(word in text for word in (*_WORK_PLACE_WORDS, 'address')):
        return False
    return any(_BIRTH_WORD_RE.search(part) for part in (text, *pii_fragments))


def _without_home_numbers(entry: dict) -> dict:
    """`entry`, or a copy whose text lacks every number a home label ("(h)",
    "h:", "Home tel") introduces (#1222). The pii pass cuts only the home
    WORDS, so a displaced entry handed to the Appendix whole would print a
    number this module withholds as home."""
    text = entry.get('text', '') or ''
    scrubbed = _LABELLED_NUMBER_RE.sub(
        lambda m: '' if m.group('home') else m.group(0), text)
    return entry if scrubbed == text else {**entry, 'text': scrubbed}


@dataclass
class _SlotRank:
    """An Office slot's ranking state: its own work-contact label, whether the
    current value carries it, and the entry that supplied that value."""
    label_re: re.Pattern[str]
    cell_text: Callable[[_JsonValue, str], str | None]
    unconsumed: list[dict]
    labelled: bool = False
    source: dict | None = None

    def offer(self, current: str | None, extracted: _JsonValue,
              entry: dict) -> str | None:
        """The slot value after offering `extracted`, taken from `entry`.

        The first value still wins its slot, except that a value whose entry
        carries the slot's own label outranks an unlabelled one (#1222): a
        banner line or a bare institution name read as an address came first
        and blocked the labelled Work address that followed. A displaced
        value's entry is handed back to `unconsumed`, so the post-render
        recovery can place it."""
        is_labelled = bool(self.label_re.search(entry.get('text', '')))
        candidate = self.cell_text(extracted, 'office')
        if not candidate:
            return current
        if current and not (is_labelled and not self.labelled
                            and self.source is not None):
            return current
        if self.source is not None:
            handed_back = _without_home_numbers(self.source)
            if all(e is not self.source and e.get('text') != handed_back.get('text')
                   for e in self.unconsumed):
                self.unconsumed.append(handed_back)
        self.labelled, self.source = is_labelled, entry
        return candidate


def _office_slot_ranks(unconsumed: list[dict]) -> tuple[_SlotRank, _SlotRank]:
    """The (address, telephone) ranking state for the two Office slots."""
    return (_SlotRank(_WORK_ADDRESS_LABEL_RE, _address_cell_text, unconsumed),
            _SlotRank(_WORK_PHONE_LABEL_RE, _phone_cell_text, unconsumed))


class _AddressContext(NamedTuple):
    """What the address routing reads from one entry: its lowercased text,
    the fragments the pii pass cut from it, its stage-4 phone value, the
    entry itself (the Office slot ranking records it), and the lowercased
    text of every non-A entry (where a department or school name is
    carried by a record of its own)."""
    text: str
    pii_fragments: list[str]
    phone: _JsonValue
    entry: dict
    other_entries_text: str = ''


def _route_address(address: _JsonValue, ctx: _AddressContext, home_address: str | None,
                   office_address: str | None, address_rank: _SlotRank,
                   withheld: list[WithheldItem]) -> tuple[str | None, str | None, bool]:
    """(home_address, office_address, set_aside) after routing one
    entry's stage-4 `address`; lifted out of `_fill_personal_data` (#1223).

    A dict that names its own halves fills both slots from them instead of
    forcing the whole thing into whichever one the raw text happened to label
    (#442); a dict that names no slot is one address and is routed by the
    raw text, same as a string. A place of birth fills no slot: it is
    recorded on `withheld`, and the True third value makes the caller count
    the entry consumed, since the recovery pass would render its residual,
    the place (#1223, EBYSBC EQADVR-02). A value with no street, number or
    state (`_names_a_street_or_number`) is a department or school name, not
    an address, and fills no slot either (#1222, EBYSBC RVROVQ-04). When a
    non-A entry's text carries the same name and the line holds nothing
    else but a label, a record of its own renders it and the True third
    value keeps the header line out of the Appendix; otherwise the entry is
    left to the Appendix recovery, as a displaced banner address is."""
    if _labels_its_own_address_slots(address):
        return (home_address or _address_cell_text(address, 'home'),
                office_address or _address_cell_text(address, 'office'), False)
    if _address_is_home(ctx.text, ctx.pii_fragments, ctx.phone):
        return home_address or _address_cell_text(address, 'home'), office_address, False
    if _address_is_birthplace(ctx.text, ctx.pii_fragments):
        withheld.append(WithheldItem(CAT_PLACE_OF_BIRTH, _PERSONAL_DATA_SECTION_LABEL, None))
        return home_address, office_address, True
    office_text = _address_cell_text(address, 'office')
    if not _names_a_street_or_number(office_text):
        name = office_text.lower()
        return (home_address, office_address,
                name in ctx.other_entries_text and _holds_only_a_label_and(name, ctx.text))
    if any(word in ctx.text for word in _WORK_PLACE_WORDS) or not office_address:
        office_address = address_rank.offer(office_address, address, ctx.entry)
    return home_address, office_address, False


#: What may sit beside a department name on a line that is set aside: a
#: label of at most two words and punctuation ("Department:", "School -").
_LABEL_ONLY_RE = re.compile(r'\W*(?:[^\W\d_]+(?:\s+[^\W\d_]+)?\W*)?')


def _holds_only_a_label_and(name: str, text: str) -> bool:
    """Whether `text` is `name` with at most a short label beside it. A
    line holding more ("WEBPAGES <department>: <url> <url>") is not set
    aside when a record carries the name: its other content would be lost
    (#1222, CTXOTY)."""
    return name in text and _LABEL_ONLY_RE.fullmatch(text.replace(name, '', 1)) is not None


class _PhoneSlots(NamedTuple):
    """The three phone slots `_fill_personal_data` fills from its entries."""
    office: str | None
    cell: str | None
    home: str | None


def _phone_text_for_routing(phone: _JsonValue, text: str) -> str:
    """The part of `text` whose label routes `phone`: the whole entry, or,
    when one line carries both a home and an office label ("Home: ...
    Office: ..."), the labelled part that holds the number (#1222, EBYSBC
    EQADVR-03). The block-level 'home' word sent the office number to the
    withheld home slot."""
    number = _digits(str(phone))
    for _kind, part in _home_and_office_parts(text):
        if len(number) >= _MIN_PHONE_DIGITS_TO_PAIR and number in _digits(part):
            return part
    return text


def _route_phone(phone: _JsonValue, text: str, pii_fragments: list[str], entry: dict,
                 slots: _PhoneSlots, phone_rank: _SlotRank) -> _PhoneSlots:
    """`slots` after routing one stage-4 phone value by the label the
    entry's text gives it; lifted out of `_fill_personal_data` (#1222)."""
    office_phone, cell_phone, home_phone = slots
    routing_text = _phone_text_for_routing(phone, text)
    if routing_text is not text:
        pii_fragments = []
    # Check if text contains multiple phone type labels
    has_mobile, has_home = _cell_and_home_signals(phone, routing_text, pii_fragments)
    has_work = 'office' in routing_text or 'work' in routing_text

    if _labels_its_own_phone_slots(phone):
        # A structured phone names its own halves, so trust those
        # rather than the entry's raw text label (#450). web147's
        # contact block is labelled "Home" but the dict carries
        # cell/office/fax; the raw-text routing sent all three to
        # home_phone, which the WCM template has no row for, so
        # every number was dropped.
        cell_phone = cell_phone or _phone_cell_text(phone, 'cell')
        office_phone = office_phone or _phone_cell_text(phone, 'office')
    elif has_mobile and has_work and ';' in str(phone):
        # Both types in the same entry: each number takes the label it
        # carries, else the label nearest it in the text on either side
        # (#952; #1222, EBYSBC EQGGRB-04).
        mixed_cell, mixed_office = _cell_and_office_numbers(str(phone), routing_text)
        cell_phone = cell_phone or mixed_cell
        office_phone = office_phone or mixed_office
    elif has_mobile:
        if not cell_phone:
            cell_phone = _phone_cell_text(phone, 'cell')
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
            home_phone = _phone_cell_text(phone, 'home')
    elif has_work or not office_phone:
        office_phone = phone_rank.offer(office_phone, phone, entry)
    return _PhoneSlots(office_phone, cell_phone, home_phone)


def _cell_and_office_numbers(phone: str, text: str) -> tuple[str | None, str | None]:
    """(cell number, office number) out of a ';'-joined phone value. Each
    part is a cell number when it carries a cell label itself ("(cell)")
    or the text puts one at its number (`_nearest_phone_label`, either
    side); a fax part is skipped and any other part is the office number.
    The first of each kind wins. The scans this replaced took the first
    number AFTER the word 'cell' and the word 'office', so a label written
    after its number ("<n> (cell) <n> (office)") put the office number in
    the Cell phone row."""
    cell = office = None
    for part in phone.split(';'):
        number = re.search(_PHONE_NUMBER_PATTERN, part)
        if number is None or _FAX_LABEL.search(part.lower()):
            continue
        if (_CELL_KEYWORD_AS_LABEL_RE.search(part)
                or _nearest_phone_label(_PhoneNumber.parse(part), text) == _PHONE_LABEL_CELL):
            cell = cell or number.group(0)
        else:
            office = office or number.group(0)
    return cell, office


#: A "Home:" / "Office:" label inside one contact line (#1222, EBYSBC
#: EQADVR-03). The colon is required: "Home Office" and "office hours" are
#: not labels.
_HOME_OR_OFFICE_LABEL_RE = re.compile(
    r'\b(?P<kind>home|office|work|business)(?:[ \t]+address)?[ \t]*:', re.IGNORECASE)
_LABEL_KIND_HOME = 'home'


def _home_and_office_parts(text: str) -> list[tuple[str, str]]:
    """(kind, part) for each labelled part of a line that carries both a
    home and a work label, kind 'home' or 'office', each part starting at
    its own label; [] for any other line."""
    labels = list(_HOME_OR_OFFICE_LABEL_RE.finditer(text))
    kinds = [_LABEL_KIND_HOME if m.group('kind').lower() == _LABEL_KIND_HOME else 'office'
             for m in labels]
    if _LABEL_KIND_HOME not in kinds or set(kinds) == {_LABEL_KIND_HOME}:
        return []
    ends = [m.start() for m in labels[1:]] + [len(text)]
    return [(kind, text[m.start():end]) for kind, m, end in zip(kinds, labels, ends)]


def _address_halves(address: _JsonValue, text: str) -> list[tuple[_JsonValue, str]]:
    """(address, routing text) pairs to route one stage-4 address by: the
    value and the entry's text, or, when the value itself is a "Home: ...;
    Office: ..." string, each half with its own label as its text, so the
    home half stays withheld and the office half reaches Office address
    (#1222, EBYSBC EQADVR-03)."""
    if not address:
        return []
    if not isinstance(address, str):
        return [(address, text)]
    parts = _home_and_office_parts(address)
    if not parts:
        return [(address, text)]
    return [(_HOME_OR_OFFICE_LABEL_RE.sub('', part, count=1).strip(' \t;,'),
             part.lower()) for _kind, part in parts]


#: Stage-4 keys outside the A schema (`config/field_schemas_v1.1.json`:
#: name, email, phone, address) that name their own contact slot (#1222,
#: #741; EBYSBC ZCTARO-06, YYVHNN-02). Personal Data read only `phone` and
#: `address`, so a value under one of these rendered nowhere.
_OFFSCHEMA_PHONE_KEYS = {
    'office': ('office_phone', 'work_phone', 'business_phone', 'research_phone', 'clinic_phone'),
    'cell': ('cell_phone', 'mobile_phone'),
}
_OFFSCHEMA_OFFICE_ADDRESS_KEYS = ('office_address', 'work_address', 'business_address',
                                  'research_address', 'clinic_address')
#: A street address stage 4 split into parts, joined "street, city, state zip".
_STREET_KEYS = ('street_address', 'street')
_CITY_KEYS = ('city',)
_STATE_KEYS = ('state',)
_ZIP_KEYS = ('zip_code', 'zip', 'postal_code')


class _OffSchemaContact(NamedTuple):
    """The off-schema contact values of one A entry, as values that name
    their own slots: a phone dict {'office': ..., 'cell': ...} and an
    address dict {'office_address': ...}, or None."""
    phone: dict[str, str] | None
    address: dict[str, str] | None


def _first_value(fields: dict, keys: tuple[str, ...]) -> str:
    """The first non-empty string value among `keys`, else ''."""
    return next((str(fields[key]).strip() for key in keys
                 if isinstance(fields.get(key), (str, int)) and str(fields[key]).strip()), '')


def _offschema_contact(fields: dict, pii_fragments: list[str]) -> _OffSchemaContact:
    """One A entry's contact values under off-schema keys. A value cut from
    a protected-data fragment is skipped, as for `phone` and `address`. The
    address is read only when stage 4 set no `address`."""
    phone = {slot: value for slot, keys in _OFFSCHEMA_PHONE_KEYS.items()
             if (value := _first_value(fields, keys))
             and not _from_pii_fragment(value, pii_fragments)}
    address = ''
    if not fields.get('address'):
        street = _first_value(fields, _STREET_KEYS)
        address = _first_value(fields, _OFFSCHEMA_OFFICE_ADDRESS_KEYS) or (street and ', '.join(
            part for part in (street, _first_value(fields, _CITY_KEYS),
                              ' '.join(p for p in (_first_value(fields, _STATE_KEYS),
                                                   _first_value(fields, _ZIP_KEYS)) if p))
            if part))
    if address and _from_pii_fragment(address, pii_fragments):
        address = ''
    return _OffSchemaContact(phone or None, {'office_address': address} if address else None)


#: What makes a value a place someone can be reached at rather than the
#: name of a department or school (#1222, EBYSBC RVROVQ-04): a digit, a
#: street or building word, or a ", ST" state abbreviation.
_PLACE_WORD_RE = re.compile(
    r'\b(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln|way|place|pl|plaza'
    r'|court|ct|parkway|pkwy|highway|hwy|circle|terrace|suite|ste|room|rm|floor|building'
    r'|bldg|hall|box)\b\.?', re.IGNORECASE)
_STATE_ABBREVIATION_RE = re.compile(r',\s*[A-Z]{2}\b')


def _names_a_street_or_number(address: str) -> bool:
    """Whether `address` holds a digit, a street or building word, or a
    state abbreviation. "Department of Example Studies" and "Example
    University School of Medicine" hold none: they filled Office address
    and blocked the real one."""
    return bool(re.search(r'\d', address) or _PLACE_WORD_RE.search(address)
                or _STATE_ABBREVIATION_RE.search(address))


#: The keys stage 4 puts an A entry's ORCID iD under.
_ORCID_KEYS = ('orcid', 'orcid_id')


def _orcid_of(fields: dict, pii_fragments: list[str]) -> str | None:
    """The ORCID iD stage 4 found on an A contact line, or None. Personal
    Data has no ORCID row and the S0 renderer read only S0 entries, so it
    rendered nowhere (#817, EBYSBC SJWASY-05)."""
    orcid = _first_value(fields, _ORCID_KEYS)
    return orcid if orcid and not _from_pii_fragment(orcid, pii_fragments) else None


def _orcid_profile_entry(orcid: str, s0_text: str) -> dict | None:
    """An S0 researcher-profile entry for `orcid`, or None when an S0 entry
    (or an earlier A entry's profile) already carries the same iD."""
    if _digits(orcid)[-8:] in _digits(s0_text):
        return None
    return {'text': f'ORCID: {orcid}', 'taxonomy_code': 'S0', 'extracted_fields': {}}


#: A work phone label straight before a number in a non-A entry's text:
#: "Phone:", "Tel.", "Telephone", "Office:". Fax, pager, cell and home
#: numbers carry other labels and are never taken.
_WORK_PHONE_IN_TEXT_RE = re.compile(
    rf'\b(?:phone|tel|telephone|office|work)\b[ \t.:#-]*(?P<number>{_PHONE_NUMBER_PATTERN})',
    re.IGNORECASE)
#: Words before a phone label that make it another kind of number.
_OTHER_PHONE_KIND_RE = re.compile(r'\b(?:home|cell|mobile|fax|pager|residence)\W*$', re.IGNORECASE)
_STREET_LINE_RE = re.compile(
    r'^\d+[A-Za-z]?\s+(?:.*\s)?(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|lane|ln'
    r'|way|place|pl|plaza|court|ct|parkway|pkwy|highway|hwy|circle|terrace)\b', re.IGNORECASE)
_UNIT_LINE_RE = re.compile(r'\b(?:room|suite|floor|building|bldg|box)\b', re.IGNORECASE)
_CITY_LINE_RE = re.compile(
    r'\b\d{5}(?:-\d{4})?\b|,\s*[A-Z]{2}\b|^[A-Z][a-z]+(?: [A-Z][a-z]+)*, [A-Z][a-z]+(?: [A-Z][a-z]+)?$')
#: A street line, then at most this many more lines to reach the city line.
_MAX_LINES_AFTER_STREET = 2


def _street_address_in(text: str) -> str | None:
    """The first street address in a tab- or newline-separated text: a
    unit line straight before the street line, the street line, and the
    lines after it through the city line; None when no line is a street."""
    lines = [line.strip() for line in re.split(r'[\t\n]', text)]
    for i, line in enumerate(lines):
        street = _STREET_LINE_RE.match(line)
        if not street:
            continue
        start = i - 1 if i and _UNIT_LINE_RE.search(lines[i - 1]) else i
        end = i
        # The street number itself can be five digits, so only the text
        # after the street word can say the city is on this line.
        if not _CITY_LINE_RE.search(line[street.end():]):
            for j in range(i + 1, min(i + _MAX_LINES_AFTER_STREET + 1, len(lines))):
                if _CITY_LINE_RE.search(lines[j]):
                    end = j
                    break
        return '\n'.join(lines[start:end + 1])
    return None


def _labelled_work_number_in(text: str) -> str | None:
    """The first number a work phone label introduces in `text`."""
    for match in _WORK_PHONE_IN_TEXT_RE.finditer(text):
        if (len(_digits(match.group('number'))) >= _MIN_PHONE_DIGITS
                and not _OTHER_PHONE_KIND_RE.search(text[:match.start()])):
            return match.group('number')
    return None


def _work_contact_in_entries(entries: list[dict], work_email: str | None
                             ) -> tuple[str | None, str | None]:
    """(office phone, office address) from the first non-A entry that holds
    the owner's work email -- a contact block stage 3b coded as a position
    (#1222, EBYSBC E10: BZZNRL 7, HZGJFM 729, MUHLLD 10, GJXIWD 2); (None,
    None) when there is none. The email is the anchor that makes it the
    owner's block and not a referee's. An entry carrying a home, residence
    or birth word is skipped whole, and a value cut from a protected-data
    fragment is not taken (#1367 must keep holding)."""
    if not work_email:
        return None, None
    for entry in entries:
        text = entry.get('text') or ''
        pii_fragments = entry.get('_pii_fragments', [])
        if (entry.get('taxonomy_code') == 'A' or work_email.lower() not in text.lower()
                or _HOME_LABEL.search(text.lower())
                or any(_BIRTH_WORD_RE.search(part) for part in (text, *pii_fragments))):
            continue
        phone, address = _labelled_work_number_in(text), _street_address_in(text)
        return ((phone if phone and not _from_pii_fragment(phone, pii_fragments) else None),
                (address if address and not _from_pii_fragment(address, pii_fragments) else None))
    return None, None


# #946: consumer mail domains. An address at one of these is the owner's
# personal email whatever block it sits in, unless the label right before it
# says it is a work address -- the only thing that routed to Personal email
# before was the literal word "personal", so a Gmail address in a HOME
# ADDRESS block rendered as the owner's Work email. #821 decided personal
# email RENDERS, so this moves a value between two rendered rows and
# withholds nothing. Exact domains, not a prefix match: "outlook.office365"
# style tenant domains and a university's own "mail." hosts are institutional.
_CONSUMER_EMAIL_DOMAINS = frozenset({
    'gmail.com', 'googlemail.com',
    'yahoo.com', 'ymail.com', 'yahoo.co.uk',
    'hotmail.com', 'hotmail.co.uk', 'outlook.com', 'live.com', 'msn.com',
    'icloud.com', 'me.com', 'mac.com',
    'aol.com', 'protonmail.com', 'proton.me',
    'comcast.net', 'verizon.net', 'att.net',
})
_WORK_EMAIL_LABEL_WORDS = ('work', 'office', 'business', 'institution')
# Where the label that owns an email can start: the entry's own separators.
_EMAIL_LABEL_BOUNDARY_RE = re.compile(r'[\t\n;|]')
# A work label written after the address, "jdoe@gmail.com (work)". Only a
# parenthetical right after it: ", Office: ..." after an address is the next
# field's label, not this one's.
_WORK_LABEL_AFTER_EMAIL_RE = re.compile(
    r'\s*\((?:' + '|'.join(_WORK_EMAIL_LABEL_WORDS) + r')\b')
# The stage-4 fields an email is read from, in precedence order. A
# `work_email` key outranks the domain rule. A `personal_email` key does not:
# stage 4 reads it off the block heading, the same mistake as #946's -- the
# corpus has an institutional .edu address keyed `personal_email` because it
# sits in a Home block.
_FIELD_PERSONAL_EMAIL = 'personal_email'
_EMAIL_FIELD_KEYS = ('email', 'primary_email', 'institutional_email',
                     _FIELD_WORK_EMAIL, _FIELD_PERSONAL_EMAIL)


@dataclass(frozen=True, slots=True)
class _EmailAddress:
    """An email address stripped and lowercased -- the form the entry's
    lowercased text carries it in."""
    address: str

    @classmethod
    def parse(cls, value: _JsonValue) -> _EmailAddress:
        return cls(str(value).strip().lower())

    @property
    def is_consumer(self) -> bool:
        """Whether the address is at a consumer mail domain (#946)."""
        return self.address.rsplit('@', 1)[-1] in _CONSUMER_EMAIL_DOMAINS


def _is_personal_email(email: _EmailAddress, text: str, field_key: str | None) -> bool:
    """Whether an email belongs in the Personal email row.

    `text` is the entry's lowercased text and `field_key` the stage-4 field
    the email came from (None for one found in the text). A `work_email` key
    decides. Otherwise the word "personal" anywhere in the text still
    decides, as before; otherwise a consumer-domain address does, unless a
    work word labels it (#946)."""
    if field_key == _FIELD_WORK_EMAIL:
        return False
    if 'personal' in text:
        return True
    if not email.is_consumer:
        return False
    return not _work_label_owns_email(email.address, text)


def _work_label_owns_email(address: str, text: str) -> bool:
    """Whether a work word labels `address`: in the text between the
    previous separator and the address, or in a parenthetical right after
    it. An address missing from the text is judged on the whole text."""
    at = text.find(address)
    if at < 0:
        return any(word in text for word in _WORK_EMAIL_LABEL_WORDS)
    before = text[:at]
    boundaries = list(_EMAIL_LABEL_BOUNDARY_RE.finditer(before))
    if boundaries:
        before = before[boundaries[-1].end():]
    return (any(word in before for word in _WORK_EMAIL_LABEL_WORDS)
            or _WORK_LABEL_AFTER_EMAIL_RE.match(text, at + len(address)) is not None)


def _route_email(email: str, text: str, field_key: str | None,
                 work_email: str | None,
                 personal_email: str | None) -> tuple[str | None, str | None]:
    """(work_email, personal_email) once `email` is offered to its row. A
    row that is already filled keeps its first value."""
    if _is_personal_email(_EmailAddress.parse(email), text, field_key):
        return work_email, personal_email or email
    return work_email or email, personal_email


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


def _phone_digit_count(text: str) -> int:
    """Digits in `text` outside an extension ("ext. 1234", "x1234567")."""
    return sum(c.isdigit() for c in _EXTENSION.sub('', text))


def _is_home_segment(segment: str, text: str) -> bool:
    """Whether the source labels one number of a phone value home: a home
    marker in the segment itself ("555-0100 (h)", `_HOME_PHONE_MARKER`), or,
    given the entry's `text`, a home label right before the number there
    (`_nearest_phone_label`, the label a single number is routed by)."""
    return bool(_HOME_PHONE_MARKER.search(segment)) or (
        bool(text) and _nearest_phone_label(_PhoneNumber.parse(segment), text) == _PHONE_LABEL_HOME)


def _drop_home_phone_segments(value: _JsonValue | None, withheld: list[WithheldItem],
                              text: str = '') -> _JsonValue | None:
    """`value` with every home number segment removed (#730, #821;
    `_is_home_segment`, which reads `text` when given), each recorded on
    `withheld`; None when nothing non-home remains. A value with no home
    segment is returned untouched, so a well-formed number is never
    re-joined or reformatted. A segment carrying both an office and a home
    marker with no separator between them is dropped whole. A structured
    (dict or list) value names its own slots and is returned untouched
    (`_labels_its_own_phone_slots`).

    The entry path passes its text (#1223, EBYSBC MRJDWE-01): a combined
    value ("555-0100 (h); 555-0101 (w)") pairs with no single number in the
    text, so it took the Office telephone whole, home number included."""
    if not value or not isinstance(value, str):
        return value
    segments = _PHONE_SEGMENT_SPLIT.split(value)
    kept = [seg for seg in segments if not _is_home_segment(seg, text)]
    if len(kept) == len(segments):
        return value
    for seg in segments:
        if _is_home_segment(seg, text):
            _withhold_home_row(_FIELD_HOME_PHONE, seg, seg, withheld)
    kept = [seg.strip() for seg in kept if seg.strip()]
    return '; '.join(kept) or None


def _withhold_home_row(field: str, value: str, source_text: str,
                       withheld: list[WithheldItem]) -> None:
    """Record one home-labelled source row as withheld (#730, #821). Nothing
    is returned: the row never fills a slot. The category is the row's own
    protected-data category when the value carries one (a "Home Address:" label
    over a birth-place value stays a birth-place withhold), else home contact.
    A home email records nothing (#821 renders personal email).

    A home-contact item is recorded once per document: the stage-4 entry pass
    has usually withheld the same home address already, and the docx row
    repeating it would double the notice's count. `WithheldItem` carries no
    value, so this dedups by category, not by value -- a home phone that only
    the docx carries is folded into the same item."""
    if not value or field == _FIELD_HOME_EMAIL:
        return
    category = _pii_category_of(value, source_text) or CAT_HOME_CONTACT
    if category == CAT_HOME_CONTACT and any(
            item.category == category
            and item.section_label == _PERSONAL_DATA_SECTION_LABEL
            for item in withheld):
        return
    withheld.append(WithheldItem(category, _PERSONAL_DATA_SECTION_LABEL, None))


def _row_label_value_pairs(row: _TableRow) -> list[tuple[str, str]]:
    """The (label text, value) pairs one source-table row carries.

    Normally one: label in cell 0, value in the first later cell that differs
    from it (a gridSpan label cell repeats itself across `row.cells` --
    NSUJZG_2027_Eil_Robert's "Professional Address:" row is label merged over
    columns 0-1 with the value in column 2). A HOME-labelled cell 0 that
    carries its own value ("Home Address: 12 Elm St | Business Phone: ...")
    is a side-by-side layout: cell 0 is the home pair, and the next cell is a
    second, unrelated label/value pair, so skipping the home row skips only
    its own cell (#730)."""
    label_text = row.cells[0].text
    value_cell = row.cells[1]
    for candidate_cell in row.cells[1:]:
        if candidate_cell.text.strip() != label_text.strip():
            value_cell = candidate_cell
            break
    value = value_cell.text.strip()
    embedded = label_text.partition(':')[2].strip()
    field = _classify_contact_label(label_text)
    # An office phone takes its embedded number only in a real side-by-side
    # row (cell 1 non-empty) and only when it is one line: a label cell that
    # holds a whole multi-line block over an empty cell 1 is left as before.
    if embedded and (field in _HOME_FIELDS or (
            field == _FIELD_OFFICE_PHONE and value and '\n' not in embedded
            and _phone_digit_count(embedded) >= _MIN_PHONE_DIGITS)):
        pairs = [(label_text, embedded)]
        if ':' in value:
            pairs.append((value, value.partition(':')[2].strip()))
        return pairs
    return [(label_text, value)]


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
            logger.info("\nFilling Personal Data...")

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
        address_rank, phone_rank = _office_slot_ranks(unconsumed)
        # An A entry's ORCID iD, for the S0 researcher-profile renderer.
        self._a_researcher_profiles: list[dict] = []
        s0_text = ' '.join(e.get('text', '') for e in all_entries or [] if e.get('taxonomy_code') == 'S0')
        other_entries_text = '\n'.join(e.get('text') or '' for e in all_entries or []
                                       if e.get('taxonomy_code') != 'A').lower()

        for entry in entries:
            fields = entry.get('extracted_fields', {}) or {}
            text = entry.get('text', '').lower()
            slots_before = (work_email, personal_email, office_phone,
                            cell_phone, office_address, home_address)
            address_set_aside = False

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
            extracted_phone = _drop_home_phone_segments(fields.get('phone'), self._pii_result.withheld, text)
            extracted_address = fields.get('address')
            offschema = _offschema_contact(fields, pii_fragments)
            email_key = next((key for key in _EMAIL_FIELD_KEYS if fields.get(key)), None)
            extracted_email = fields.get(email_key) if email_key else None

            if pii_fragments:
                if _from_pii_fragment(extracted_phone, pii_fragments):
                    extracted_phone = None
                if _from_pii_fragment(extracted_address, pii_fragments):
                    extracted_address = None
                if _from_pii_fragment(extracted_email, pii_fragments):
                    extracted_email = None

            # Classify phone by type; then the numbers stage 4 put under
            # slot-named keys of their own (#1222, EBYSBC ZCTARO-06, YYVHNN-02).
            phones = _PhoneSlots(office_phone, cell_phone, home_phone)
            for phone_value in (extracted_phone, offschema.phone):
                if phone_value:
                    phones = _route_phone(phone_value, text, pii_fragments, entry,
                                          phones, phone_rank)
            office_phone, cell_phone, home_phone = phones

            # Classify address by type, each half of a "Home: ...; Office: ..." on its own
            for address, address_text in _address_halves(extracted_address or offschema.address, text):
                home_address, office_address, set_aside = _route_address(
                    address, _AddressContext(address_text, pii_fragments, fields.get('phone'), entry,
                                             other_entries_text),
                    home_address, office_address, address_rank, self._pii_result.withheld)
                address_set_aside = address_set_aside or set_aside
            orcid = _orcid_of(fields, pii_fragments)
            profile = _orcid_profile_entry(orcid, s0_text) if orcid else None
            if profile:
                self._a_researcher_profiles.append(profile)
                s0_text += ' ' + profile['text']

            # Classify email by type
            if extracted_email:
                work_email, personal_email = _route_email(
                    extracted_email, text, email_key, work_email, personal_email)

            # Also check entry text for email pattern (fallback)
            if not work_email and not personal_email:
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', entry.get('text', ''))
                if email_match and not _from_pii_fragment(email_match.group(0),
                                                          pii_fragments):
                    work_email, personal_email = _route_email(
                        email_match.group(0), text, None, work_email, personal_email)

            # home_phone is deliberately absent from this tuple: it is written
            # and never read (the template has no home-phone row), so an entry
            # that sets only home_phone reaches the document nowhere and is
            # genuinely unconsumed. Two corpus entries are in exactly that
            # state.
            if not address_set_aside and not orcid and (work_email, personal_email, office_phone, cell_phone,
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
        if not (office_phone and office_address):
            block_phone, block_address = _work_contact_in_entries(all_entries or [], work_email)
            office_phone, office_address = office_phone or block_phone, office_address or block_address

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
                        logger.debug("  Found email from paragraph: %s", work_email)
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
                for label_text, value in _row_label_value_pairs(row):
                    field = _classify_contact_label(label_text)
                    # The row as one text, for the protected-data gate below:
                    # a value is denied by provenance against the fragments of
                    # the line it came from, exactly as the entry path does.
                    row_text = f"{label_text}\t{value}"

                    # Extract name if not yet found (or only have last name)
                    if field == _FIELD_NAME and not name_is_complete:
                        if value and len(value) > 2:
                            recovered_name = _withhold_recovered(value, row_text, withheld)
                            if recovered_name is not None:
                                name = recovered_name
                                name_is_complete = True
                                if self.verbose:
                                    logger.debug("  Found name from table: %s", name)

                    # A home-labelled row is withheld and skipped (#730): it
                    # neither fills Office address nor counts as a withheld
                    # Office row, so a business row after it still fills the slot.
                    elif field in _HOME_FIELDS:
                        _withhold_home_row(field, value, row_text, withheld)

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
                                office_phone = _withhold_recovered(
                                    _drop_home_phone_segments(block_phone, withheld),
                                    row_text, withheld)
                            if not work_email:
                                work_email = _withhold_recovered(block_email, row_text, withheld)

                    # Extract phone if not yet found
                    elif field == _FIELD_OFFICE_PHONE and not office_phone:
                        if value and len(value) > 5:
                            office_phone = _withhold_recovered(
                                _drop_home_phone_segments(value, withheld),
                                row_text, withheld)
                            if self.verbose and office_phone:
                                logger.debug("  Found phone from table: %s", office_phone)

                    # Extract email if not yet found
                    elif field == _FIELD_WORK_EMAIL and not work_email:
                        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', value)
                        if email_match:
                            work_email = _withhold_recovered(
                                email_match.group(0), row_text, withheld)
                            if self.verbose and work_email:
                                logger.debug("  Found email from table: %s", work_email)

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
                        logger.debug("  Found phone from address block: %s", office_phone)
                continue

            # Extract email if embedded in address
            if any(marker in line_lower for marker in _EMAIL_LINE_MARKERS):
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', line)
                if email_match and not work_email:
                    work_email = email_match.group(0)
                    if self.verbose:
                        logger.debug("  Found email from address block: %s", work_email)
                continue

            # Skip fax lines
            if 'fax:' in line_lower or 'fax\t' in line_lower:
                continue

            # Keep other lines as address
            if line:
                address_lines.append(line)

        address = '\n'.join(address_lines)
        if self.verbose:
            logger.debug("  Found address from table: %s...", address[:50])
        return address, office_phone, work_email
