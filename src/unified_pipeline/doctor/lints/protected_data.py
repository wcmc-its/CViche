"""Protected personal data reaching the rendered WCM document (#820 piece 3).

The last line of defence. Pieces 1 (the detector, `stage6/normalization/
pii.py`) and 2 (the pre-render pass, `stage6/pii_pass.py`) can miss each
other's gap -- a shape neither recognizes, or a code path this ticket did
not anticipate -- and this is the only artifact where "did it actually
reach the page a reader opens" can be asked at all.

One responsibility: scan the RENDERED docx's paragraphs and table cells
for the policy's label and value shapes, plus a bare full date inside the
Personal Data block, plus this lint's own label-and-value shapes that do
not go through the withhold's matcher (#1223: the independent check,
tracked deletions included), and say only the block/section and the policy
CATEGORY in the finding -- NEVER the matched value, because this JSON is
copied into Teams cards and issues (#820's own report was found by
grepping a docx by hand for exactly this reason).

Scope mirrors the pass (§1.5, one definition of "where the ambiguous rows
apply"): the Personal Data block and the Appendix get the FULL policy; every
other section gets the rows unambiguous as a label, so a rendered
book-chapter title "Children: ..." in Book Chapters is not a finding while
the same line in the Appendix is. `quality_score.score_protected_data`
calls this same function on the same blocks (#825), so the doctor and the
scorer cannot disagree about a document.
"""
import re

from unified_pipeline.stage6.normalization.pii import (
    _MONTH_NAMES,
    CAT_CHILDREN,
    CAT_DATE_OF_BIRTH,
    CAT_DEA,
    CAT_FAMILY,
    CAT_HOME_CONTACT,
    CAT_INSTITUTIONAL_ID,
    CAT_MARITAL_STATUS,
    CAT_PLACE_OF_BIRTH,
    CAT_SPOUSE,
    CAT_TAX_ID,
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    _pii_matches,
)
from unified_pipeline.stage6.pii_pass import PII_REDACTED_NOTICE

from ..shared import _finding, _output_section_header

# The WCM template's own blank "DEA number: (optional)" slot, rendered on
# EVERY output in the Licensure section, needs no exclusion of its own: DEA
# is a PERSONAL_AND_APPENDIX row, and the Licensure section is scanned at
# ALL_CODES scope. Round 1 excluded it by exact text match; that filter
# became dead code with the scope split (0 of 105 rendered farm docx change
# with or without it) and was removed.
#
# #821 withholds the template's DEA slot too, but deliberately not by
# widening this row's scope (`stage6/normalization/pii.py`'s own comment on
# the row explains why) -- `stage6/sections/licensure.py` enforces it at
# render time instead. That means the generic scan below still cannot see
# a DEA leak in the Licensure section by construction, so `_DEA_VALUE_RE`
# below is this lint's OWN probe for it, scoped to the Licensure section
# only and gated on the block ALSO naming "DEA" (the identifiers table's
# own cell text, "DEA number: (optional)") -- a bare value-shape scan alone
# would also fire on a state licence number of the same 2-letter/7-alnum
# shape sitting in the Licensure table's OTHER (state-licence) block, which
# never mentions "DEA" at all. A real DEA-shaped VALUE reaching a block
# that names DEA is a regression (the renderer never writes one there any
# more); the bare "DEA number: (optional)" label is a template artifact on
# every output and must never be a finding by itself -- "optional" is 8
# characters, one short of the 9 this shape requires, so it never matches.
_DEA_VALUE_RE = re.compile(r'\b[A-Za-z]{2}[A-Za-z0-9]{7}\b')
_DEA_LABEL_PRESENT_RE = re.compile(r'\bdea\b', re.IGNORECASE)

# The label gate above is also what let two real leaks through (#1217): a DEA
# number written into a state-licence row has no "DEA" token in its block --
# the row's first column spelled out the agency, or the reader fused the
# number into another credential's number cell. Without a label the loose
# shape would match every nine-letter word, so a block that does NOT name DEA
# is probed with the real format only: two letters then seven DIGITS, bounded
# so it cannot be cut out of a longer token. Calibrated before it was made
# ungated: rendered over the 163-CV corpus, the only bare tokens of this
# shape inside the Licensure section were the two leaked DEA numbers; every
# other hit sat in the Bibliography, Research or Mentoring sections. A state
# licence or certificate number that happens to share the shape WOULD be a
# finding here -- the cost of a false positive is a hard-fail the reviewer
# can read in the document, the cost of a miss is a leaked identifier.
_DEA_NUMBER_VALUE_RE = re.compile(
    r'(?<![A-Za-z0-9])[A-Za-z]{2}\d{7}(?![A-Za-z0-9])')

# The Personal Data table's own "Home address:" row is a static template
# label too, unlike every OTHER row of this lint's full policy (DOB, SSN,
# spouse, ... only ever appear because the SOURCE cv's free text used that
# label -- the template itself never prints one). `_withhold_home_contact`
# (personal_data.py) leaves the value cell EMPTY when there is nothing to
# render, and `_pii_matches`' label span still matches the bare label with
# nothing after it (a #442-shaped false positive discovered rendering the
# 66-CV farm for this ticket: EVERY output fired once on the bare row).
# Matched only against the merged fragment `_pii_matches` itself returns --
# not a second scan -- so a genuine leak with real content after the label
# is unaffected.
_HOME_CONTACT_LABEL_ONLY_RE = re.compile(
    r'^\s*home\s*(?:address|phone|telephone|tel\.?)\s*:\s*$', re.IGNORECASE)

# The generic scan above is ALSO blind to a genuine home-address/phone leak
# in the Personal Data TABLE specifically -- discovered on the same farm
# render, not hypothetical: `read_docx_blocks` dumps a table row as
# "<label>:\n<value>\n<label>: | <value>\n<next label>:\n...", and `\n` is
# one of `_pii_matches`'s own hard fragment boundaries, so the label's span
# stops before a value on the very next "line" ever becomes part of the
# same fragment -- a farm uid's real street-address value (a street number
# followed by a street name) matched nothing at all under the generic scan
# alone, bare label exclusion or not. Scoped
# to the Personal Data section (mirroring the DEA probe's Licensure scope)
# and keyed off a digit rather than hand-listing every other row label
# ("Cell phone:", "Work email:", ...) this table can put right after an
# EMPTY home-address/phone row: a real address or phone value always
# carries one (a street number, a zip, the phone digits themselves), the
# next row's bare label never does.
_HOME_CONTACT_LABEL_RE = re.compile(
    r'home\s*(?:address|phone|telephone|tel\.?)\s*:', re.IGNORECASE)


def _dea_value_matches(block_text: str) -> list[re.Match[str]]:
    """DEA-shaped values in one Licensure-section block: the loose shape when
    the block names DEA, the real format alone when it does not (#1217)."""
    probe = (_DEA_VALUE_RE if _DEA_LABEL_PRESENT_RE.search(block_text)
             else _DEA_NUMBER_VALUE_RE)
    return list(probe.finditer(block_text))


def _home_contact_value_leaked(block_text: str) -> bool:
    for m in _HOME_CONTACT_LABEL_RE.finditer(block_text):
        after = block_text[m.end():].split('\n')
        same_line = after[0]
        if same_line.strip():
            # A value inline with its label on ONE "line" (no `\n` between
            # them) is exactly what the generic scan above already
            # matches as one merged fragment -- counting it here too
            # would double the finding for the same leak.
            continue
        next_line = after[1] if len(after) > 1 else ''
        if re.search(r'\d', next_line):
            return True
    return False

# Section names (as `_output_section_header` normalizes them) whose blocks
# get the full policy: the two places the pre-render pass applies it.
_PERSONAL_DATA_SECTION = "personal data"
_APPENDIX_SECTION = "appendix"
#: #821's own probe (see `_DEA_VALUE_RE` above) -- not part of the FULL
#: policy pair above, since the label itself must stay unflagged there.
#: Matched by PREFIX (`_is_licensure_section` below), not `==`: the real
#: WCM template's own header normalizes to "licensure, board certification"
#: (`wcm_template_scaffold_strings.json`), not the bare word alone -- an
#: exact-match probe (round-1 #821-R2) never fired on any real render, only
#: on the unit tests' fabricated `_p("LICENSURE")` header (F2).
_LICENSURE_SECTION = "licensure"


def _is_licensure_section(section: str | None) -> bool:
    """True for the Licensure section under either header shape: the bare
    word (tests, and any narrower template revision) or the real template's
    combined "licensure, board certification" (`_norm`'s own whitespace/
    case folding is already done by `_block_sections` -- this only adds the
    prefix)."""
    return section is not None and section.startswith(_LICENSURE_SECTION)


_SECTION_PERSONAL_DATA_TABLE = "Personal Data table"
_SECTION_APPENDIX = "Appendix"
_SECTION_BODY_TABLE = "body table"
_SECTION_BODY_PARAGRAPH = "body paragraph"


#: A bare full date, ANY of the three shapes the issue names -- checked only
#: inside the Personal Data block; a labeled date anywhere else in the
#: document is already `_pii_fragments`'s job. `_MONTH_NAMES` is `pii.py`'s
#: own (one definition); the composition is this lint's, because it is a
#: value-shape check over RENDERED TEXT with no label at all.
_BARE_DATE_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\b" + _MONTH_NAMES + r"\s+\d{1,2},\s*\d{4}\b"
    r"|\b\d{1,2}\s+" + _MONTH_NAMES + r"\s+\d{4}\b",
    re.I,
)


# --------------------------------------------------------------------------
# The independent check (#1223; EBYSBC class E2)
# --------------------------------------------------------------------------
#
# Everything above finds a value through `_pii_matches`, the SAME matcher the
# render-time withhold (`stage6/pii_pass.py`) cuts with. A shape that matcher
# recognises never reaches the page, and a shape it does not recognise reaches
# the page with this lint unable to see it: the scan inherits every miss of the
# thing it is meant to check. EBYSBC hit exactly that, with the lint silent: a
# "Married:" colon row and a "Grandchildren" en-dash row recovered into the
# Appendix (EQADVR-01), a "(h)"-labelled number in the Office telephone cell
# (MRJDWE-01). The shapes below are this lint's own and share nothing with
# `pii.py` but the category names (the notice's vocabulary): a family, birth or
# home label followed by the VALUE that makes the line protected -- a
# capitalised name, a date or a count after a family word; a date or a place
# after a birth word; a phone number or a street address beside a home label.
# A bare label, a title ("Children - A Review") or an institution ("Children's
# Hospital") carries no such value and does not match.
#
# Scope: the Personal Data block and the Appendix only. The body sections hold
# research titles and citations ("very low birth weight", a publisher "& Sons",
# a co-author surnamed Son) that no label-plus-value shape tells apart from a
# family line; the scan above keeps covering the labels unambiguous anywhere.
# Calibrated on the 63-run EBYSBC/s7ab/pilot farm: every hit hand-checked.

#: A person's or a place's name: a capitalised word of two or more letters
#: that is not a title's function word ("Children - The Forgotten ...").
_NAME = r"(?!(?:The|An|And|Of|In|On|For|With|From|To|At|By)\b)[A-Z][a-z][\w'’.-]*"
#: One or more names in a row ("Pat Lee", "Ann, Bob and Cy") -- the whole run
#: is the value `_locate_entries` looks for in stage 4.
_NAMES = _NAME + r"(?:[ \t]*,?[ \t]+(?:and[ \t]+)?" + _NAME + r")*"
#: A whole date: day, month and year all present.
_FULL_DATE = (r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
              r"|(?i:" + _MONTH_NAMES + r")\.?[ \t]+\d{1,2}(?:st|nd|rd|th)?,?[ \t]+\d{4}\b"
              r"|\d{1,2}[ \t]+(?i:" + _MONTH_NAMES + r")\.?[ \t]+\d{4}\b)")
_DATE = r"(?:" + _FULL_DATE + r"|(?:19|20)\d{2}\b)"
#: A small head count: "three children", "Sons: 2". A single digit only, so a
#: study's "40 children" never reads as a family.
_COUNT = r"(?:[1-9]|(?i:one|two|three|four|five|six|seven|eight|nine|ten))\b"
#: Label-to-value separator: a colon, a dash with a space after it (so a
#: compound like "Son-in-law" or "Child-Parent" is not a label), or an open
#: parenthesis.
_SEP = r"[ \t]*(?::|[-–—](?=[ \t])|\()[ \t]*"
_CHILD_WORDS = r"(?i:children|sons?|daughters?)"
_GRANDCHILD_WORDS = r"(?i:(?:great[- ]?)?grand(?:children|child|sons?|daughters?))"
#: A North American number, with or without its area-code parentheses.
_PHONE = r"(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\b\d{3}[ .-])\d{3}[ .-]\d{4}\b"
_STREET = (r"\b\d{1,6}[ \t]+(?:[A-Z][\w.'’-]*[ \t]+){1,4}"
           r"(?i:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct"
           r"|circle|cir|place|pl|terrace|ter|parkway|pkwy|highway|hwy|trail|trl)\b")
_HOME_LABEL = (r"(?:\b(?i:home|residence)\b"
               r"(?:[ \t]+(?i:phone|telephone|tel|ph|address|addr|number|no|fax)\b\.?)?"
               r"|\((?i:h|home|res)\))")
_HOME_TAG_AFTER = r"(?:\((?i:h|home|res|residence)\)|\b(?i:home)\b)"
#: NDMRSO ND1: the owner's institutional or tax ID number. A label naming whose
#: ID it is, then four or more digits. A bare "ID #" (a board certificate's) or
#: an institution name before a grant's "#<number>" is not such a label; NPI and
#: ORCID are public and render (#821).
_INSTITUTIONAL_ID_LABEL = (
    r"(?i:(?:employee|staff|student|faculty|personnel|payroll|badge)[ \t]*(?:id|identification|number|no\.?)"
    r"|(?:university|institution(?:al)?|campus)[ \t]*(?:id|identification)"
    r"|ufid|cwid|emplid)")
_TAX_ID_LABEL = (r"(?i:(?:federal[ \t]*)?tax[ \t]*(?:payer[ \t]*)?(?:id|identification)"
                 r"|employer[ \t]*identification|f?ein|i?tin)")
#: The label's own trailing "number", "no." or "#", then a colon or dash, blanks,
#: tabs or a table-cell pipe.
_ID_LABEL_TAIL = r"(?![A-Za-z])(?:[ \t]*(?i:number|no\.?|#))?[ \t|]*(?:[:\-–—][ \t|]*)?"
_ID_NUMBER = r"[A-Za-z]{0,4}-?\d(?:[ -]?\d){3,}"

#: (shape, policy category). Every shape names its value `value`. Same line
#: only (`[ \t]`, never `\s`): a Personal Data row's label and value sit on
#: two "lines" of a table block, and `_home_contact_value_leaked` above owns
#: that case.
_INDEPENDENT_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?i:spouse|wife|husband)\b" + _SEP
                + r"(?P<value>" + _NAMES + "|" + _DATE + ")"), CAT_SPOUSE),
    (re.compile(r"\b(?i:married)\b(?:" + _SEP + r"|[ \t]+(?i:to)[ \t]+)"
                + r"(?P<value>" + _NAMES + "|" + _DATE + ")"), CAT_MARITAL_STATUS),
    # "& Sons" / "and Sons" is a publisher ("Example & Sons: Hoboken").
    (re.compile(r"(?<!& )(?<!and )\b" + _CHILD_WORDS + r"\b" + _SEP
                + r"(?P<value>" + _NAMES + "|" + _DATE + "|" + _COUNT + ")"), CAT_CHILDREN),
    (re.compile(r"\b" + _GRANDCHILD_WORDS + r"\b" + _SEP
                + r"(?P<value>" + _NAMES + "|" + _DATE + "|" + _COUNT + ")"), CAT_FAMILY),
    # A count ending its fragment ("Nationality: ...; three children"), not one
    # opening a sentence ("five children with asthma were enrolled").
    (re.compile(r"\b(?P<value>" + _COUNT + r"[ \t]+(?:" + _GRANDCHILD_WORDS + "|"
                + _CHILD_WORDS + r"))\b(?=[ \t]*(?:[;,(]|\.?[ \t]*(?:\n|$)))"), CAT_CHILDREN),
    (re.compile(r"(?:\b(?i:born|date[ \t]+of[ \t]+birth|birth[ \t]*date|dob)\b|(?i:d\.o\.b)\.?)"
                + r"(?:" + _SEP + r"|[ \t]*,?[ \t]+(?:(?i:in|on)[ \t]+)?)"
                + r"(?P<value>" + _DATE + ")"), CAT_DATE_OF_BIRTH),
    # A bare "Birth" label (NDMRSO: BNYLDF, HUOGDE) opens a line, and a whole
    # date follows it -- never a year: "Birth: <date> in <place>", "BIRTH
    # <date>". Line-initial, so "preterm birth 03/15/2019" in a title is not one.
    (re.compile(r"(?:^|(?<=[\n\t|;]))[ \t]*(?:[•·*–—-][ \t]*)?(?i:birth)"
                + r"(?:[ \t]*[:\-–—][ \t]*|[ \t]+)(?P<value>" + _FULL_DATE + ")"),
     CAT_DATE_OF_BIRTH),
    (re.compile(r"\b(?i:born|birthplace|place[ \t]+of[ \t]+birth)\b"
                + r"(?:" + _SEP + r"|[ \t]+(?i:in)[ \t]+)(?P<value>" + _NAMES + ")"),
     CAT_PLACE_OF_BIRTH),
    (re.compile(_HOME_LABEL + r"[ \t]*(?:[:\-–—][ \t]*)?(?P<value>" + _PHONE + "|" + _STREET + ")"),
     CAT_HOME_CONTACT),
    (re.compile(r"(?P<value>" + _PHONE + "|" + _STREET + r")[ \t]*[,;:]?[ \t]*" + _HOME_TAG_AFTER),
     CAT_HOME_CONTACT),
    (re.compile(r"(?<![\w-])" + _INSTITUTIONAL_ID_LABEL + _ID_LABEL_TAIL
                + r"(?P<value>" + _ID_NUMBER + ")"), CAT_INSTITUTIONAL_ID),
    (re.compile(r"(?<![\w-])" + _TAX_ID_LABEL + _ID_LABEL_TAIL
                + r"(?P<value>" + _ID_NUMBER + ")"), CAT_TAX_ID),
)

#: `_locate_entries`: a value shorter than this (a lone given name, a year)
#: is in too many stage-4 entries to name one; longer than the probe cap, a
#: value is matched on its first 40 characters so a renderer's tail edit
#: ("..., Esq." -> "..., Esq") does not lose the entry.
_LOCATOR_MIN_CHARS = 8
_LOCATOR_PROBE_CHARS = 40
#: The harness (`doctor_vs_autopsy.py`) reads at most this many entry indices.
_LOCATED_MAX = 3


def _alnum(text: str) -> str:
    """Letters and digits only, casefolded: the rendered value and the stage-4
    source text it came from differ in punctuation, dashes and spacing."""
    return re.sub(r"[\W_]+", "", text.casefold())


def _shape_hits(text: str, claimed: list[tuple[int, int]]) -> list[tuple[str, str]]:
    """(category, normalised value) per independent-shape match in one block,
    leftmost first and, at one start, longest first ("Born: March 3, 1950" is
    a date of birth, not the place "March"). A match overlapping a span the
    matcher scan already reported (`claimed`), or an earlier independent
    match, is skipped: one leak, one finding."""
    matches = sorted((m.start(), -m.end(), category, _alnum(m.group("value")))
                     for pattern, category in _INDEPENDENT_SHAPES
                     for m in pattern.finditer(text))
    taken = list(claimed)
    hits: list[tuple[str, str]] = []
    for start, neg_end, category, value in matches:
        end = -neg_end
        if any(start < t_end and t_start < end for t_start, t_end in taken):
            continue
        taken.append((start, end))
        hits.append((category, value))
    return hits


def _stage4_entry_texts(stage_4: dict | None) -> list[tuple[int | float, str]]:
    """(element_idx_start, `_alnum` text) per stage-4 entry; [] without stage 4."""
    if not isinstance(stage_4, dict):
        return []
    texts = []
    for entry in stage_4.get("entries") or []:
        idx, text = entry.get("element_idx_start"), entry.get("text")
        if isinstance(idx, (int, float)) and isinstance(text, str):
            texts.append((idx, _alnum(text)))
    return texts


def _locate_entries(value: str, entry_texts: list[tuple[int | float, str]]) -> list[str]:
    """`entry N` for the stage-4 entries whose source text holds this value --
    the index the autopsy labels and the harness match on, never the value."""
    if len(value) < _LOCATOR_MIN_CHARS:
        return []
    probe = value[:_LOCATOR_PROBE_CHARS]
    return [f"entry {idx}" for idx, text in entry_texts if probe in text][:_LOCATED_MAX]


def _independent_findings(text: str, deleted_text: str, claimed: list[tuple[int, int]],
                          where: str, entry_texts: list[tuple[int | float, str]]) -> list[dict]:
    """One ERROR per distinct (category, value) the independent shapes find in a
    block's accepted text or its tracked deletions. Distinct, because a table
    block repeats every cell in its joined-row line. The message says which:
    an accepted-text hit is a shape `_pii_matches` did not match (its spans are
    `claimed`); the matcher never reads the deletions at all."""
    origin: dict[tuple[str, str], str] = {}
    for hit in _shape_hits(text, claimed):
        origin.setdefault(hit, "; the withhold's own matcher does not recognise this shape")
    for hit in _shape_hits(deleted_text, []):
        origin.setdefault(hit, "; it is in a tracked deletion, still shown struck through")
    return [_finding(
        "protected_data_in_output", "ERROR",
        f"protected personal data ({category}) found in {where} -- value withheld "
        f"from this finding{why}",
        _locate_entries(value, entry_texts)) for (category, value), why in origin.items()]


def _block_sections(blocks: list[tuple[str, str]]) -> list[str | None]:
    """The normalized WCM section name each block falls under: from its
    section header paragraph (exclusive) to the next recognized header, or
    None before the first header.

    Reuses `_output_section_header` -- the SAME section-boundary primitive
    `lint_dead_sections` already uses to bound a source section against the
    rendered document (`doctor/lints/render.py`) -- rather than inventing a
    second locator. Verified empirically against a real render
    (`web057_wcm.docx`): the "PERSONAL DATA" paragraph is its own block,
    immediately followed by the one table that is the Personal Data table,
    then the next section's header paragraph ("EDUCATION").
    """
    sections: list[str | None] = []
    current: str | None = None
    for kind, text in blocks:
        if kind == "p":
            name = _output_section_header(str(text).strip())
            if name is not None:
                current = name.strip().lower()
                sections.append(None)  # the header paragraph itself
                continue
        sections.append(current)
    return sections


def _scan_scope(section: str | None) -> str:
    """The policy scope a rendered block is checked at: the full policy in
    the Personal Data block and the Appendix, the unambiguous rows elsewhere
    -- the same split `stage6/pii_pass.py` renders under."""
    if section in (_PERSONAL_DATA_SECTION, _APPENDIX_SECTION):
        return SCOPE_PERSONAL_AND_APPENDIX
    return SCOPE_ALL_CODES


def _section_label(kind: str, section: str | None) -> str:
    if section == _PERSONAL_DATA_SECTION:
        return _SECTION_PERSONAL_DATA_TABLE
    if section == _APPENDIX_SECTION:
        return _SECTION_APPENDIX
    return _SECTION_BODY_TABLE if kind == "table" else _SECTION_BODY_PARAGRAPH


def lint_protected_data_in_output(blocks: list[tuple[str, str]],
                                  deleted_blocks: list[tuple[str, str]] | None = None,
                                  stage_4: dict | None = None) -> list[dict]:
    """Protected personal data visible in the rendered document.

    Hard-fail, like `owner_contact_missing`/`pipeline_errors_present`:
    `quality_score.score_protected_data` calls this same scan (#825: the
    doctor and the scorer's gate lists must not diverge) and caps a run
    carrying any finding into the RED band.

    `deleted_blocks` is the same document's tracked-deletion view
    (`docx_body_blocks(..., deleted=True)`, block for block); the independent
    shapes scan it as well as the accepted text. `stage_4` only locates an
    independent hit (its finding's evidence names the source entry index); it
    never adds or removes a finding, so the scorer, which passes none, counts
    the same hits.

    The withheld notice paragraph (`PII_REDACTED_NOTICE`) is skipped: it
    names categories, and a category name that ever coincides with a label
    pattern must not turn the notice itself into a finding. The Word
    comment that accompanies it lives in the comments part, which is not a
    body block and so is never scanned here.
    """
    findings: list[dict] = []
    sections = _block_sections(blocks)
    deleted_texts = ([str(text) for _, text in deleted_blocks] if deleted_blocks is not None
                     else [""] * len(blocks))
    if len(deleted_texts) != len(blocks):
        raise ValueError("deleted_blocks is not the same document's block list")
    entry_texts = _stage4_entry_texts(stage_4)

    for i, (kind, text) in enumerate(blocks):
        stripped = str(text)
        if PII_REDACTED_NOTICE in stripped:
            continue
        section = sections[i]
        where = _section_label(kind, section)
        claimed: list[tuple[int, int]] = []

        # One finding per merged match, named by its POLICY CATEGORY -- the
        # same vocabulary the notice and the Word comment use -- never by
        # the matched text: a label-text excerpt of a colon-less fragment
        # ("Born January 2, 1970" -> "Born January") would carry part of
        # the value into a Teams card. The bare-SSN and visa value shapes
        # are ALL_CODES policy rows, so they are found here in any section.
        for match in _pii_matches(stripped, _scan_scope(section),
                                  personal_data=section == _PERSONAL_DATA_SECTION):
            if (match.category == CAT_HOME_CONTACT
                    and _HOME_CONTACT_LABEL_ONLY_RE.match(
                        stripped[match.start:match.end])):
                continue
            claimed.append((match.start, match.end))
            findings.append(_finding(
                "protected_data_in_output", "ERROR",
                f"protected personal data ({match.category}) found in {where} "
                f"-- value withheld from this finding"))

        if section in (_PERSONAL_DATA_SECTION, _APPENDIX_SECTION):
            findings.extend(_independent_findings(
                stripped, deleted_texts[i], claimed, where, entry_texts))

        if section == _PERSONAL_DATA_SECTION:
            for _match in _BARE_DATE_RE.finditer(stripped):
                findings.append(_finding(
                    "protected_data_in_output", "ERROR",
                    "a bare date found in the Personal Data block"))

            if _home_contact_value_leaked(stripped):
                findings.append(_finding(
                    "protected_data_in_output", "ERROR",
                    f"protected personal data ({CAT_HOME_CONTACT}) found in "
                    f"{where} -- value withheld from this finding"))

        if _is_licensure_section(section):
            for _match in _dea_value_matches(stripped):
                findings.append(_finding(
                    "protected_data_in_output", "ERROR",
                    f"protected personal data ({CAT_DEA}) found in {where} "
                    f"-- value withheld from this finding"))

    return findings
