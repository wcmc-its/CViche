"""Protected personal data reaching the rendered WCM document (#820 piece 3).

The last line of defence. Pieces 1 (the detector, `stage6/normalization/
pii.py`) and 2 (the pre-render pass, `stage6/pii_pass.py`) can miss each
other's gap -- a shape neither recognizes, or a code path this ticket did
not anticipate -- and this is the only artifact where "did it actually
reach the page a reader opens" can be asked at all.

One responsibility: scan the RENDERED docx's paragraphs and table cells
for three things, and say only the block/section and the label or shape
class in the finding -- NEVER the matched value, because this JSON is
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
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    _MONTH_NAMES,
    _SSN_VALUE_SHAPE_RE,
    _pii_fragments,
)
from unified_pipeline.stage6.pii_pass import PII_REDACTED_NOTICE

from ..shared import _finding, _output_section_header

# The WCM template's own blank "DEA number: (optional)" slot, rendered on
# EVERY output in the Licensure section (#821: the template put it there),
# needs no exclusion of its own: DEA is a PERSONAL_AND_APPENDIX row, and
# the Licensure section is scanned at ALL_CODES scope. Round 1 excluded it
# by exact text match; that filter became dead code with the scope split
# (0 of 105 rendered farm docx change with or without it) and was removed.

# The placeholder `_pii_label_text` returns for a fragment with no label at
# all (a bare SSN value shape) -- those are reported once, by the dedicated
# SSN-shape scan below, not a second time here under a placeholder "label".
_BARE_VALUE_PLACEHOLDER = "(bare SSN value shape)"

# Section names (as `_output_section_header` normalizes them) whose blocks
# get the full policy: the two places the pre-render pass applies it.
_PERSONAL_DATA_SECTION = "personal data"
_APPENDIX_SECTION = "appendix"

_SECTION_PERSONAL_DATA_TABLE = "Personal Data table"
_SECTION_APPENDIX = "Appendix"
_SECTION_BODY_TABLE = "body table"
_SECTION_BODY_PARAGRAPH = "body paragraph"


def _pii_label_text(fragment: object, max_len: int = 40) -> str:
    """The LABEL half of a PII fragment, safe to put in a message: never the
    protected value. Everything up to and including the first colon; for a
    colon-less fragment (a DOB/SSN value glued straight to its stem, the
    bare "married to" phrase, or a bare SSN value shape with no label at
    all) only the leading alphabetic stem, or `_BARE_VALUE_PLACEHOLDER` when
    there isn't one.

    Lives here, not in `pii.py`: it only reformats a fragment STRING
    `_pii_fragments` already returned, needs no access to the label
    vocabulary, and this is its only caller -- the doctor JSON is copied
    into Teams cards and issues, so the finding names the LABEL class,
    never the matched value (#820 piece 3).
    """
    fragment = str(fragment or "")
    colon = fragment.find(":")
    if colon != -1:
        return fragment[:colon + 1].strip()[:max_len]
    m = re.match(r"\s*[A-Za-z .'’()/-]+", fragment)
    stem = m.group(0).strip() if m else ""
    return stem[:max_len] if stem else _BARE_VALUE_PLACEHOLDER


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

        for fragment in _pii_fragments(stripped, _scan_scope(section)):
            label = _pii_label_text(fragment)
            if label == _BARE_VALUE_PLACEHOLDER:
                continue  # reported once, below, by the dedicated SSN scan
            findings.append(_finding(
                "protected_data_in_output", "ERROR",
                f"protected personal data label {label!r} found in {where} "
                f"-- value withheld from this finding"))

        for _match in _SSN_VALUE_SHAPE_RE.finditer(stripped):
            findings.append(_finding(
                "protected_data_in_output", "ERROR",
                f"an SSN-shaped value found in {where}"))

        if section == _PERSONAL_DATA_SECTION:
            for _match in _BARE_DATE_RE.finditer(stripped):
                findings.append(_finding(
                    "protected_data_in_output", "ERROR",
                    "a bare date found in the Personal Data block"))

    return findings
