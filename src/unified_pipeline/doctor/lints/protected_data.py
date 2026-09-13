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
"""
import re

from unified_pipeline.stage6.normalization.pii import _pii_fragments

from ..shared import _finding, _output_section_header

# The WCM template's own blank DEA slot, rendered on EVERY output whether
# or not the source CV named one (#821: the template put it there, not
# this pipeline) -- confirmed during the #820 corpus scan: scanning all
# 95 batch-farm + 65 corpus-farm rendered docx, every one of 182
# "DEA number:" label hits was this exact string, verbatim or with a
# trailing space. Excluded by exact text match, the same device
# `quality_score.py`'s `_TEMPLATE_TAB_CELL_TEXT` uses for the template's
# own incidental raw tab: penalizing the template's own boilerplate is not
# a genuine leak.
_TEMPLATE_DEA_SLOT_TEXT = "DEA number: (optional)"

# A bare SSN, label or not -- piece 3's OWN named requirement (#820: "a
# bare SSN value shape ... anywhere (label or not)"), independent of
# whatever `pii.py`'s label vocabulary catches. `pii.py` gains its own copy
# of this same shape in the #820 piece-1 commit, for `_pii_fragments`'
# unrelated job (value-provenance denial on an ENTRY, before render) --
# the two are deliberately not shared, so that this lint's contract does
# not depend on commit order: piece 3 lands FIRST (this ticket's own "3,
# then 2, then 1" sequencing) and must be independently correct and green
# before piece 1 exists at all. Distinguishing shape from a phone number
# (3-3-4) is the middle group's width (2 digits); guarded against matching
# inside a longer dash-run (an ISBN, an ORCID) on both sides, not just
# `\b` -- see `pii.py`'s copy for the corpus false-positive this closes.
_SSN_VALUE_SHAPE_RE = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")

# The placeholder `_pii_label_text` returns for a fragment with no label at
# all (a bare SSN value shape) -- those are reported once, by the dedicated
# SSN-shape scan below, not a second time here under a placeholder "label".
_BARE_VALUE_PLACEHOLDER = "(bare SSN value shape)"


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

_MONTH_NAMES = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?"
    r"|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

#: A bare full date, ANY of the three shapes the issue names -- checked only
#: inside the Personal Data block (see `_personal_data_block_indices`); a
#: labeled date anywhere else in the document is already `_pii_fragments`'s
#: job. Defined here, not in `pii.py`: this is a value-shape check over
#: RENDERED TEXT with no label at all, a different job from the label
#: vocabulary `pii.py` owns, and importing it would not save a second copy
#: of anything -- there is only one copy either way.
_BARE_DATE_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\b" + _MONTH_NAMES + r"\s+\d{1,2},\s*\d{4}\b"
    r"|\b\d{1,2}\s+" + _MONTH_NAMES + r"\s+\d{4}\b",
    re.I,
)


def _personal_data_block_indices(blocks: list[tuple[str, str]]) -> set[int]:
    """Indices into `blocks` that fall inside the Personal Data section:
    from its header paragraph (exclusive) to the next recognized WCM
    section header (exclusive), or the end of the document.

    Reuses `_output_section_header` -- the SAME section-boundary primitive
    `lint_dead_sections` already uses to bound a source section against the
    rendered document (`doctor/lints/render.py`) -- rather than inventing a
    second locator. Verified empirically against a real render
    (`web057_wcm.docx`): the "PERSONAL DATA" paragraph is its own block,
    immediately followed by the one table that is the Personal Data table,
    then the next section's header paragraph ("EDUCATION").
    """
    indices: set[int] = set()
    in_block = False
    for i, (kind, text) in enumerate(blocks):
        stripped = str(text).strip()
        if kind == "p":
            name = _output_section_header(stripped)
            if name is not None:
                in_block = name.strip().lower() == "personal data"
                continue
        if in_block:
            indices.add(i)
    return indices


def lint_protected_data_in_output(blocks: list[tuple[str, str]]) -> list[dict]:
    """Protected personal data visible in the rendered document.

    Hard-fail, like `owner_contact_missing`/`pipeline_errors_present`:
    `quality_score.score_protected_data` reuses this same scan (#825: the
    doctor and the scorer's gate lists must not diverge) and caps a run
    carrying this finding into the RED band.
    """
    findings: list[dict] = []
    pd_indices = _personal_data_block_indices(blocks)

    for i, (kind, text) in enumerate(blocks):
        stripped = str(text)
        section = ("Personal Data table" if i in pd_indices
                   else "Appendix/body table" if kind == "table"
                   else "body paragraph")

        for fragment in _pii_fragments(stripped):
            if fragment.strip() == _TEMPLATE_DEA_SLOT_TEXT:
                continue
            label = _pii_label_text(fragment)
            if label == _BARE_VALUE_PLACEHOLDER:
                continue  # reported once, below, by the dedicated SSN scan
            findings.append(_finding(
                "protected_data_in_output", "ERROR",
                f"protected personal data label {label!r} found in {section} "
                f"-- value withheld from this finding"))

        for _match in _SSN_VALUE_SHAPE_RE.finditer(stripped):
            findings.append(_finding(
                "protected_data_in_output", "ERROR",
                f"an SSN-shaped value found in {section}"))

        if i in pd_indices:
            for _match in _BARE_DATE_RE.finditer(stripped):
                findings.append(_finding(
                    "protected_data_in_output", "ERROR",
                    "a bare date found in the Personal Data block"))

    return findings
