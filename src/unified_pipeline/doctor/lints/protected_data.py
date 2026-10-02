"""Protected personal data reaching the rendered WCM document (#820 piece 3).

The last line of defence. Pieces 1 (the detector, `stage6/normalization/
pii.py`) and 2 (the pre-render pass, `stage6/pii_pass.py`) can miss each
other's gap -- a shape neither recognizes, or a code path this ticket did
not anticipate -- and this is the only artifact where "did it actually
reach the page a reader opens" can be asked at all.

One responsibility: scan the RENDERED docx's paragraphs and table cells
for the policy's label and value shapes, plus a bare full date inside the
Personal Data block, and say only the block/section and the policy
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
    CAT_DEA,
    CAT_HOME_CONTACT,
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    _MONTH_NAMES,
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


def lint_protected_data_in_output(blocks: list[tuple[str, str]]) -> list[dict]:
    """Protected personal data visible in the rendered document.

    Hard-fail, like `owner_contact_missing`/`pipeline_errors_present`:
    `quality_score.score_protected_data` calls this same scan (#825: the
    doctor and the scorer's gate lists must not diverge) and caps a run
    carrying any finding into the RED band.

    The withheld notice paragraph (`PII_REDACTED_NOTICE`) is skipped: it
    names categories, and a category name that ever coincides with a label
    pattern must not turn the notice itself into a finding. The Word
    comment that accompanies it lives in the comments part, which is not a
    body block and so is never scanned here.
    """
    findings: list[dict] = []
    sections = _block_sections(blocks)

    for i, (kind, text) in enumerate(blocks):
        stripped = str(text)
        if PII_REDACTED_NOTICE in stripped:
            continue
        section = sections[i]
        where = _section_label(kind, section)

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
            findings.append(_finding(
                "protected_data_in_output", "ERROR",
                f"protected personal data ({match.category}) found in {where} "
                f"-- value withheld from this finding"))

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
