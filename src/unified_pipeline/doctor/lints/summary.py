"""Lint for claims the generated research summary makes that the CV does not support.

One responsibility: read the stage-4.5 Research Activities paragraph the model
wrote, and the stage-4 entries it was written from, and report a sentence that
claims something of a kind the CV has none of (#1554, batch OIEPQD):

- a pending application ("an application under review") when no grant is
  pending: no M2C entry, and no grant whose status or heading says pending;
- funding ("funded", "grants", "supported by") when the CV lists no grant at
  all (M2A, M2B or M2C; M2D patents are not grants);
- mentoring or training of others when no N-coded entry exists and no entry
  outside research activities (M1) names a mentor, mentee, trainee,
  preceptor or advisee;
- a named funder (NIH, NSF, DoD and others in `_FUNDERS`) that no stage-4
  entry and no source line names.

Only a summary the model generated is judged: an existing M1 statement kept
as-is is the CV's own text. A presence check, not a fact check: a claim of
the right kind that is still wrong (a current grant's role, an old letter of
intent called current, "numerous" mentees for two) is not reported.
"""
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from unified_pipeline.stage6.normalization.records import (
    grant_heading_rebucket_target,
    grant_status_rebucket_target,
)
from unified_pipeline.stage6.sections.research_support import (
    PENDING_GRANT_CODE,
    RESEARCH_SUPPORT_SECTIONS,
)
from unified_pipeline.stage_4_5_research_summary import GENERATION_METHOD_LLM

from ..shared import _fields_entries, _FieldsEntry, _finding

#: The stage-4 codes that are grants (current, completed, pending).
_GRANT_CODES = frozenset(code for code, _title in RESEARCH_SUPPORT_SECTIONS)
#: Taxonomy family N: mentoring programmes, mentees, mentoring outputs.
_MENTORING_CODE_PREFIX = "N"
#: Research activities: their mentoring words are the owner being mentored (a
#: research position's "Mentor:" line), not mentoring by the owner. A
#: publication's are kept: an abstract at a trainee symposium, or a mentee
#: author marked as one, is mentoring the CV records.
_RESEARCH_ACTIVITIES_CODE = "M1"
#: How much of a sentence a finding's evidence quotes.
_EVIDENCE_CHARS = 240

# Claim kinds, as each finding's evidence names them.
CLAIM_PENDING = "pending_application"
CLAIM_FUNDING = "funding"
CLAIM_MENTORING = "mentoring"
CLAIM_FUNDER = "funder"

#: A candidate sentence boundary: end punctuation, space, a capital.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
#: Abbreviations whose period is not a sentence end (#1592: YUYVIG's audit
#: split "U.S.", "St." and dotted acronyms mid-sentence). No company suffix:
#: "Inc." ended the sentence at all 3 of its YUYVIG and farm splits.
_TITLE_ABBREVIATIONS = ("St", "Dr", "Mr", "Mrs", "Ms", "Prof", "Jr", "Sr", "Dept", "Univ",
                        "Mt", "vs", "al", "Ph.D")
#: A candidate's text ending in one: a dotted acronym ("U.S.", "e.g."), a lone
#: capital (a middle initial, "A."), or a title abbreviation. The cost is that
#: a real sentence ending in one ("...in the U.S. We...") stays joined to the next.
_ABBREVIATION_END_RE = re.compile(
    r"(?:\b(?:[A-Za-z]\.){2,}|(?<![\w.])[A-Z]\."
    rf"|\b(?:{'|'.join(re.escape(a) for a in _TITLE_ABBREVIATIONS)})\.)\Z")
_PENDING_RE = re.compile(
    r"\b(?:under\s+review|pending|submitted|resubmi(?:tted|ssion)|awaiting\s+(?:a\s+)?"
    r"(?:funding\s+)?decision)\b", re.IGNORECASE)
_APPLICATION_RE = re.compile(
    r"\b(?:applications?|proposals?|grants?|funding|letters?\s+of\s+intent)\b", re.IGNORECASE)
#: A patent application is pending by nature and is not a grant.
_PATENT_RE = re.compile(r"\bpatent", re.IGNORECASE)
#: Reviewing others' grants is not holding one: a sentence naming a review
#: role is not judged for funding ("Funding and service have included a
#: study section", eb-farm YOXXOH).
_GRANT_REVIEW_RE = re.compile(r"\bgrant\s+review\w*|\bstudy\s+sections?\b|\breview\s+panels?\b",
                              re.IGNORECASE)
_FUNDING_RE = re.compile(
    r"\b(?:funded|funding|grants?|supported\s+by|sponsored\s+by|awards?\s+from)\b"
    r"|\b(?:R0[13]|R21|U01|P01|P30|P50|K\d{2})\b", re.IGNORECASE)
_MENTORING_CLAIM_RE = re.compile(
    r"\b(?:mentor(?:s|ed|ing)?|mentees?|trainees?)\b"
    r"|\btraining\s+(?:of\s+|the\s+next\s+generation|early-career)", re.IGNORECASE)
_MENTORING_SUPPORT_RE = re.compile(
    r"\b(?:mentor\w*|mentee\w*|trainee\w*|precept\w*|advisee\w*)", re.IGNORECASE)


class Funder(NamedTuple):
    """A funder the summary may name: `claim` finds it in the summary,
    `support` in the CV. Acronyms match by case, long names do not."""
    name: str
    claim: re.Pattern
    support: re.Pattern


#: NIH institutes and centres: naming one supports an NIH claim, and the
#: summary naming one needs that one (or a spelled-out institute) in the CV.
_NIH_INSTITUTES = ("NCI", "NHLBI", "NIA", "NIAID", "NIAAA", "NIAMS", "NIBIB", "NICHD", "NIDA",
                   "NIDCD", "NIDCR", "NIDDK", "NIEHS", "NIGMS", "NIMH", "NIMHD", "NINDS",
                   "NINR", "NEI", "NHGRI", "NCATS", "NCCIH")
_SPELLED_OUT_INSTITUTE = r"(?i:National\s+(?:Institutes?|Cancer\s+Institute|Heart,?\s+Lung))"


def _acronym_funder(acronym: str, long_name: str, *also_supported_by: str) -> Funder:
    claim = rf"\b{acronym}\b|(?i:{long_name})"
    return Funder(acronym, re.compile(claim),
                  re.compile("|".join((claim, *also_supported_by))))


_FUNDERS: tuple[Funder, ...] = (
    _acronym_funder("NIH", r"National\s+Institutes?\s+of\s+Health",
                    rf"\b(?:{'|'.join(_NIH_INSTITUTES)})\b", _SPELLED_OUT_INSTITUTE),
    *(Funder(acronym, re.compile(rf"\b{acronym}\b"),
             re.compile(rf"\b{acronym}\b|{_SPELLED_OUT_INSTITUTE}"))
      for acronym in _NIH_INSTITUTES),
    _acronym_funder("NSF", r"National\s+Science\s+Foundation"),
    _acronym_funder("DoD", r"Department\s+of\s+Defen[cs]e", r"\bDOD\b",
                    r"\b(?:CDMRP|PRMRP)\b", r"(?i:Congressionally\s+Directed|\bArmy\b)"),
    _acronym_funder("AHRQ", r"Agency\s+for\s+Healthcare\s+Research"),
    _acronym_funder("PCORI", r"Patient-Centered\s+Outcomes\s+Research\s+Institute"),
    _acronym_funder("CDC", r"Centers?\s+for\s+Disease\s+Control"),
    _acronym_funder("HRSA", r"Health\s+Resources\s+and\s+Services\s+Administration"),
    _acronym_funder("DARPA", r"Defense\s+Advanced\s+Research\s+Projects"),
    _acronym_funder("VA", r"Veterans\s+Affairs", r"\bVHA\b", r"(?i:\bveteran)"),
    _acronym_funder("AHA", r"American\s+Heart\s+Association"),
    _acronym_funder("ACS", r"American\s+Cancer\s+Society"),
)


class SummaryClaim(NamedTuple):
    """One claim kind the summary makes that the CV does not support."""
    kind: str
    message: str
    sentences: tuple[str, ...]


def generated_summary(stage_4_5: Mapping) -> str:
    """The summary text when the model wrote it, else ''."""
    summary = stage_4_5.get("research_summary")
    if not isinstance(summary, Mapping) or summary.get("generation_method") != GENERATION_METHOD_LLM:
        return ""
    return str(summary.get("text") or "")


def summary_sentences(text: str) -> list[str]:
    """The summary split into sentences, the unit every summary check judges.
    A candidate that ends in an abbreviation is joined to the next one."""
    sentences: list[str] = []
    for piece in (p.strip() for p in _SENTENCE_SPLIT_RE.split(text)):
        if not piece:
            continue
        if sentences and _ABBREVIATION_END_RE.search(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    return sentences


def _is_pending_grant(entry: _FieldsEntry) -> bool:
    """A pending grant: filed as M2C, or a grant whose status or heading says pending."""
    if entry.code == PENDING_GRANT_CODE:
        return True
    if entry.code not in _GRANT_CODES:
        return False
    status_target, _note = grant_status_rebucket_target(str(entry.fields.get("status") or ""))
    heading_target, _note = grant_heading_rebucket_target(list(entry.hierarchy))
    return PENDING_GRANT_CODE in (status_target, heading_target)


def _has_pending_grant(stage_4: Mapping) -> bool:
    return any(_is_pending_grant(entry) for entry in _fields_entries(stage_4))


def _has_mentoring(stage_4: Mapping) -> bool:
    """An N-coded entry, or mentoring words in an entry other than research activities."""
    for entry in _fields_entries(stage_4):
        if entry.code.startswith(_MENTORING_CODE_PREFIX):
            return True
        if entry.code != _RESEARCH_ACTIVITIES_CODE and _MENTORING_SUPPORT_RE.search(entry.text):
            return True
    return False


def _cv_text(stage_4: Mapping, source_lines: Iterable[str] | None) -> str:
    """Every stage-4 entry's text and string field values, and the source's lines."""
    pieces: list[str] = []
    for entry in _fields_entries(stage_4):
        pieces.append(entry.text)
        pieces.extend(str(v) for v in entry.fields.values() if isinstance(v, str))
    pieces.extend(source_lines or ())
    return "\n".join(pieces)


def _pending_sentences(sentences: Sequence[str]) -> tuple[str, ...]:
    return tuple(s for s in sentences if _PENDING_RE.search(s) and _APPLICATION_RE.search(s)
                 and not _PATENT_RE.search(s))


def _funding_sentences(sentences: Sequence[str]) -> tuple[str, ...]:
    return tuple(s for s in sentences if _FUNDING_RE.search(s) and not _GRANT_REVIEW_RE.search(s))


def _mentoring_sentences(sentences: Sequence[str]) -> tuple[str, ...]:
    return tuple(s for s in sentences if _MENTORING_CLAIM_RE.search(s))


def _unsupported_funders(sentences: Sequence[str], cv_text: str) -> list[tuple[str, str]]:
    """(funder name, first sentence naming it) for each funder the CV never names."""
    unsupported = []
    for funder in _FUNDERS:
        naming = next((s for s in sentences if funder.claim.search(s)), None)
        if naming is not None and not funder.support.search(cv_text):
            unsupported.append((funder.name, naming))
    return unsupported


def _kind_claims(sentences: Sequence[str], stage_4: Mapping) -> list[SummaryClaim]:
    """The pending, funding and mentoring claims of a kind the CV has none of."""
    codes = {entry.code for entry in _fields_entries(stage_4)}
    checks = (
        (CLAIM_PENDING, _has_pending_grant(stage_4), _pending_sentences,
         "describes a pending application, but the CV lists no pending grant"),
        (CLAIM_FUNDING, bool(codes & _GRANT_CODES), _funding_sentences,
         "describes funding, but the CV lists no grant"),
        (CLAIM_MENTORING, _has_mentoring(stage_4), _mentoring_sentences,
         "describes mentoring or training others, but the CV lists no mentoring"),
    )
    claims = []
    for kind, supported, find, message in checks:
        found = () if supported else find(sentences)
        if found:
            claims.append(SummaryClaim(kind, message, found))
    return claims


def _funder_claims(sentences: Sequence[str], cv_text: str) -> list[SummaryClaim]:
    return [SummaryClaim(CLAIM_FUNDER, f"names the funder {name}, which the CV never mentions",
                         (sentence,))
            for name, sentence in _unsupported_funders(sentences, cv_text)]


def lint_summary_unsupported_claim(stage_4_5: dict, stage_4: dict,
                                   source_lines: list[str] | None = None) -> list[dict]:
    """One WARN per kind of claim the generated research summary makes and
    the CV gives no ground for (#1554). The paragraph is signed by the owner,
    so an invented claim reaches a document they attest to; the score is not
    capped, since the rest of the document is unaffected."""
    sentences = summary_sentences(generated_summary(stage_4_5 or {}))
    claims = (_kind_claims(sentences, stage_4)
              + _funder_claims(sentences, _cv_text(stage_4, source_lines)))
    return [_finding("summary_unsupported_claim", "WARN", f"The research summary {claim.message}",
                     [f"claim={claim.kind}",
                      *(f"sentence={s[:_EVIDENCE_CHARS]}" for s in claim.sentences)])
            for claim in claims]
