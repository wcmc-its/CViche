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

Those four are presence checks, reported at WARN. Four per-claim checks
(YUYVIG and NECRGC, #1554) then judge a sentence against the one grant it
names, found by its title's words or, for a status word, its agency, and
report at INFO:

- a status word ("currently", "completed", "earlier") that the grant's own
  end date, heading or lack of either contradicts;
- an award the CV marks declined, withdrawn or not funded, counted as held;
- "funded by X" or "X-funded" over a grant whose own line does not name X;
- a lead-role claim ("I led", "I serve as PI on") over a grant whose role
  does not give it.

Only a summary the model generated is judged: an existing M1 statement kept
as-is is the CV's own text. Still not a fact check: a claim about no grant
(an invented outcome, a role span, inflated mentoring) is not reported.
"""
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import NamedTuple

from unified_pipeline.stage6.normalization.records import (
    grant_heading_rebucket_target,
    grant_status_rebucket_target,
)
from unified_pipeline.stage6.sections.research_support import (
    PENDING_GRANT_CODE,
    RESEARCH_SUPPORT_SECTIONS,
    grant_end_date,
    grant_has_ended,
)
from unified_pipeline.stage_4_5_research_summary import (
    GENERATION_METHOD_LLM,
    GRANT_STATUS_BY_CODE,
    GRANT_STATUS_COMPLETED,
    GRANT_STATUS_CURRENT,
    GRANT_STATUS_NOT_RECEIVED,
    GRANT_STATUS_NOT_STATED,
    ONGOING_PATTERN,
    grant_status,
)

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
#: A research activity the owner mentored, not one they were mentored on:
#: "Faculty mentored project with resident <name>" (HBALTD, NECRGC, #1554).
_OWNER_MENTORED_RE = re.compile(
    r"\bfaculty[-\s]+(?:mentored|advised|supervised)\b"
    r"|\bproject\s+with\s+(?:(?:dental|medical|graduate)\s+)?(?:residents?|students?|fellows?|trainees?)\b",
    re.IGNORECASE)
#: Advising and supervising students is mentoring the CV records: HNQBOI
#: (YUYVIG) lists faculty-advisor supervision of five student projects, and
#: the lint called its mentoring sentence unsupported (#1554). Not a bare
#: "Supervisor:", which names the owner's own supervisor (held-out web206).
_MENTORING_SUPPORT_RE = re.compile(
    r"\b(?:mentor\w*|mentee\w*|trainee\w*|precept\w*|advisee\w*"
    r"|(?:faculty|thesis|research|academic)\s+advis[eo]r\w*|student\s+projects?"
    r"|supervis(?:ed|ing|ion\s+of)\s+(?:\w+\s+){0,3}"
    r"(?:students?|residents?|fellows?|trainees?|theses|thesis|projects?)\b)", re.IGNORECASE)


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
    _acronym_funder("PCORI", r"Patient[-\s]Centered\s+Outcomes\s+Research\s+Institute"),
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
    """An N-coded entry, mentoring words in an entry other than research
    activities, or a research activity the owner mentored."""
    for entry in _fields_entries(stage_4):
        if entry.code.startswith(_MENTORING_CODE_PREFIX):
            return True
        if entry.code == _RESEARCH_ACTIVITIES_CODE:
            if _OWNER_MENTORED_RE.search(entry.text):
                return True
        elif _MENTORING_SUPPORT_RE.search(entry.text):
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


# ------------------------------------------------------------ per-claim checks
#
# The presence checks above pass any claim of a kind the CV holds. After #1628
# the claims that remain are of that kind but wrong about one grant (YUYVIG and
# NECRGC, #1554): a status the grant's dates contradict, a funder another line
# holds, a lead role the grant does not give, an award the CV says was declined.
# Each check below finds the grant a sentence names, by its title's words or its
# agency, and judges the sentence's word against that grant's own record.

CLAIM_GRANT_STATUS = "grant_status"
CLAIM_NOT_RECEIVED = "not_received_award"
CLAIM_FUNDER_LINK = "funder_link"
CLAIM_LEAD_ROLE = "lead_role"
#: The per-claim kinds report at INFO: their rules were written from the
#: YUYVIG and NECRGC autopsies, so their precision is in-sample (#1554).
_PER_CLAIM_SEVERITY = "INFO"
_PRESENCE_SEVERITY = "WARN"

#: A title word: four or more letters or digits, compared on its first six
#: characters so "delivering" names "delivery".
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")
_STEM_CHARS = 6
#: Words a grant title shares with any research sentence; they name no grant.
_TITLE_STOPWORDS = frozenset((
    "with", "from", "that", "this", "these", "their", "into", "using", "among", "between",
    "through", "within", "during", "based", "versus", "study", "studies", "trial", "trials",
    "grant", "grants", "project", "projects", "program", "programs", "research", "phase",
    "randomized", "controlled", "patients", "subjects", "effects", "effect", "role",
))
#: A sentence names a grant when one run of its words holds at least this many
#: of the title's distinct words, and at least this share of them; a run breaks
#: where more than `_MAX_WORD_GAP` other words separate two title words.
#: ponytail: calibrated on the YUYVIG and NECRGC summaries (#1554); revisit if a
#: title-match FP shows up on a held-out batch.
_MIN_SHARED_TITLE_WORDS = 3
_MIN_TITLE_SHARE = 0.5
_MAX_WORD_GAP = 4
#: How far, in characters, a status word reaches to the grant it describes:
#: one before it ("completed studies of <title words>") further than one after
#: it ("<title words>, completed in 2020"). And how far a lead-role claim
#: reaches to the grant it names next ("I served as PI on a study of <title>").
_STATUS_REACH_BEFORE = 90
_STATUS_REACH_AFTER = 25
_ROLE_REACH = 120
#: Two grants' mentions are of the same words when they share this share of
#: the smaller one's title words.
_SAME_WORDS_SHARE = 0.5
#: How far before "funded by X" the list it credits may end, and how far after
#: "X-funded" the grant it credits may start.
_FUNDER_REACH = 25
_PRE_FUNDER_REACH = 40
#: Where a funder phrase stops covering grants: a new clause, or the next
#: role in a list ("..., and as Site PI on ...", held-out GHWROW).
_CLAUSE_BREAK_RE = re.compile(
    r";|\balongside\b|\bas\s+well\s+as\b|\bwhile\b|\bwhereas\b|\bin\s+addition\b"
    r"|,\s+and\s+(?:I|my|as|on)\b")
#: Stage-4 fields that date an entry's start or its single year: the latest
#: such year is how recent the CV is.
_CV_DATE_FIELDS = ("year", "start_date", "date")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
#: A count over awards ("three consecutive awards", "two fellowships"), with no
#: comma or semicolon between it and the end of the words searched.
_COUNT_RE = re.compile(
    r"\b(?:two|three|four|five|six|several|multiple|consecutive|successive|\d+)\b(?![^,;]*[,;])",
    re.IGNORECASE)
_COUNT_REACH = 30

#: Status words. Not "continuous", "prior" or "past": "continuous nerve block",
#: "prior formal education" and "the past decade" name no grant's status.
_CURRENT_CLAIM_RE = re.compile(
    r"\b(?:currently|current|ongoing|continu(?:e|es|ed|ing)|active)\b", re.IGNORECASE)
_COMPLETED_CLAIM_RE = re.compile(r"\b(?:completed|previous(?:ly)?|earlier|formerly)\b", re.IGNORECASE)
#: Between a grant and a status word after it: the word starts a new clause
#: ("<title words>, and completed a study of ...").
_NEW_CLAUSE_RE = re.compile(r",\s*and\b|;|\band\s+(?:I|my)\b")
#: A status word that frames a research topic, not a grant: "my current
#: research", "a continuing focus on", "current and recent work", "currently
#: focuses on" (NJHLUR, XNWSZN, and held-out AQCLHS, HZGJFM, QZWBKQ).
_TOPICAL_NOUN_RE = re.compile(
    r"(?:\s+and\s+\w+)?\s+(?:research|work|efforts?|focus\w*|emphasis|interests?|areas?|directions?)\b",
    re.IGNORECASE)
#: Except after a verb: "I continue work on <title>" is a claim about that
#: work (ZDOAZO-01), not a topic.
_CONTINUE_VERB_RE = re.compile(r"continue[sd]?", re.IGNORECASE)
_CLAIM_CONTRADICTED_BY = {
    GRANT_STATUS_CURRENT: frozenset((GRANT_STATUS_COMPLETED, GRANT_STATUS_NOT_STATED,
                                     GRANT_STATUS_NOT_RECEIVED)),
    GRANT_STATUS_COMPLETED: frozenset((GRANT_STATUS_CURRENT, GRANT_STATUS_NOT_STATED,
                                       GRANT_STATUS_NOT_RECEIVED)),
}
#: A word that makes an agency named near it a funder, and how near.
_GRANT_CUE_RE = re.compile(
    r"\b(?:grants?|funded|funding|support\w*|sponsor\w*|awards?|stud(?:y|ies)|trials?|projects?"
    r"|fellowships?)\b", re.IGNORECASE)
_CUE_REACH = 40
#: The statuses of an application that funded nothing.
_UNFUNDED = frozenset((GRANT_STATUS_BY_CODE[PENDING_GRANT_CODE], GRANT_STATUS_NOT_RECEIVED))
#: A sentence that says an award was not received is not counting it as one.
_NOT_RECEIVED_WORDS_RE = re.compile(
    r"\b(?:declin\w*|withdr[ae]w\w*|not\s+(?:funded|received|awarded)|unfunded|rejected)\b",
    re.IGNORECASE)

#: A funder phrase: "funded by X" (X follows) or "X-funded" (X precedes). X is a
#: run of capitalised words and the joiners inside a name ("Blue & You
#: Foundation", "Department of Justice").
_NAME_WORD = r"[A-Z][\w.'&/]*"
_NAME_JOINER = r"(?:of|and|for|the|&)"
_NAME = rf"{_NAME_WORD}(?:\s+(?:{_NAME_JOINER}\s+)*{_NAME_WORD})*"
_FUNDED_BY_RE = re.compile(rf"\b(?:funded|supported|sponsored)\s+by\s+(?:the\s+)?(?P<funder>{_NAME})")
_SHARED_FUNDING_RE = re.compile(r"-\s+(?:and|or)\s+\Z")
_PRE_FUNDED_RE = re.compile(rf"(?P<funder>{_NAME})-(?:funded|supported|sponsored)\b")

#: A lead-role claim: "I led", "I direct", "I founded", "I serve as PI". Not a
#: bare "leading": "a leading investigator in" is praise, and "co-leading" a
#: shared role.
_LEAD_VERB_RE = re.compile(r"\bI\s+(?:have\s+)?(?:led|lead|direct(?:ed)?|founded|coordinate[ds]?)\b")
_PI_CLAIM_RE = re.compile(
    r"\bserv(?:e|ed|ing)\s+as\s+(?:the\s+)?(?:Multiple\s+)?(?:Principal\s+Investigator|PI)\b")
#: A grant role that supports a lead-role claim, and one that supports a PI claim.
_LEAD_ROLE_RE = re.compile(
    r"\b(?:PI|MPI|princip(?:al|le)\s+investigator|director|founder|founding|chair\w*|lead\w*|head"
    r"|coordinator)\b", re.IGNORECASE)
_PI_ROLE_RE = re.compile(r"(?<![-\w])(?:PI|MPI|princip(?:al|le)\s+investigator)\b", re.IGNORECASE)
#: The words between a lead-role claim and the grant that is its object hold
#: no clause punctuation: "I served as PI on a study of <title>".
_ROLE_OBJECT_RE = re.compile(r"[^,;:.]*\Z")
_NOT_OWN_PI_RE = re.compile(r"\b(?:co|sub|site)[-\s]*(?:PI|principal|investigator)", re.IGNORECASE)


class _Grant(NamedTuple):
    """One stage-4 grant (M2A, M2B or M2C), read once for every sentence."""
    title_stems: frozenset[str]
    agency: str
    role: str
    record_text: str
    status_then: str | None
    status_now: str | None


class _Mention(NamedTuple):
    """Where in a sentence its words name one grant: the span, and the
    positions of the sentence words that are the grant's title words."""
    grant: _Grant
    start: int
    end: int
    words: frozenset[int] = frozenset()


#: The grants one stretch of a sentence may name: two grants with near-identical
#: titles (a renewal, a supplement) are both named by the same words, and the
#: sentence is right if it is right about either.
_MentionGroup = list[_Mention]


def _stem(word: str) -> str:
    return word.lower()[:_STEM_CHARS]


def _title_stems(title: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in _WORD_RE.findall(title) if w.lower() not in _TITLE_STOPWORDS)


def _field(fields: Mapping, name: str) -> str:
    value = str(fields.get(name) or "").strip()
    return "" if value.lower() == "none" else value


def _summary_date(stage_4_5: Mapping) -> date | None:
    """The day the summary was written. None when the artifact gives no timestamp."""
    summary = stage_4_5.get("research_summary")
    stamp = str(summary.get("generation_timestamp") or "") if isinstance(summary, Mapping) else ""
    try:
        return date.fromisoformat(stamp[:10])
    except ValueError:
        return None


def _cv_date(stage_4: Mapping, written: date | None) -> date | None:
    """The day the CV was last current: the end of its latest year, or the day
    the summary was written when the CV reaches that year. The latest year is
    read from start and single dates only: an end date may be a projection."""
    if written is None:
        return None
    years = [int(y) for entry in _fields_entries(stage_4) for name in _CV_DATE_FIELDS
             for y in _YEAR_RE.findall(_field(entry.fields, name)) if int(y) <= written.year]
    if not years or max(years) >= written.year:
        return written
    return date(max(years), 12, 31)


def _grant_ended(fields: Mapping, as_of: date | None) -> bool | None:
    """Whether the grant's end date has passed by `as_of`, at the precision the
    date states (#1713's comparison); None when it states no end."""
    end_text = _field(fields, "end_date")
    if as_of is None or not end_text:
        return None
    if ONGOING_PATTERN.search(end_text):
        return False
    end = grant_end_date(end_text, as_of, _field(fields, "start_date") or None)
    return None if end is None else grant_has_ended(end, as_of)


def _grants(stage_4: Mapping, written: date | None) -> list[_Grant]:
    """Every grant entry, with the status its own record gives it (stage 4.5's
    grant_status: its end date, else its heading, else not stated) twice: as of
    the CV's date and as of the day the summary was written. A 2018 CV's grant
    ending 2020 was current when the CV was written (HXBPCT-17, JUTBUZ), and a
    2013 CV's grant has ended since (YSXCFU): a summary calling it current, or
    completed, is right either way, so each claim is judged on the date that
    favours it (§7.4: never the day the doctor runs)."""
    cv_date = _cv_date(stage_4, written)
    grants = []
    for entry in _fields_entries(stage_4):
        if entry.code not in _GRANT_CODES:
            continue
        fields = entry.fields
        record = {"extracted_fields": dict(fields), "hierarchy": list(entry.hierarchy)}
        agency = _field(fields, "agency")
        grants.append(_Grant(
            _title_stems(_field(fields, "title")), agency,
            _field(fields, "pi_role") or _field(fields, "role"),
            f"{agency}\n{entry.text}",
            grant_status(entry.code, record, _grant_ended(fields, cv_date)),
            grant_status(entry.code, record, _grant_ended(fields, written))))
    return grants


def _title_mention(grant: _Grant, words: Sequence[re.Match]) -> _Mention | None:
    """Where the sentence names the grant's title, else None: the run of its
    words holding the most title words, as `words`, and as the span, that run
    cut back to the fewest words that still name the title."""
    if len(grant.title_stems) < 2:
        return None
    needed = max(min(_MIN_SHARED_TITLE_WORDS, len(grant.title_stems)),
                 _MIN_TITLE_SHARE * len(grant.title_stems))

    def enough(run: Sequence[int]) -> bool:
        return len({_stem(words[i].group()) for i in run}) >= needed

    hits = [i for i, word in enumerate(words) if _stem(word.group()) in grant.title_stems]
    runs: list[list[int]] = []
    for i in hits:
        if runs and i - runs[-1][-1] <= _MAX_WORD_GAP:
            runs[-1].append(i)
        else:
            runs.append([i])
    named = [run for run in runs if enough(run)]
    if not named:
        return None
    best = max(named, key=lambda r: len({_stem(words[i].group()) for i in r}))
    run = best
    while len(run) > 1 and enough(run[:-1]):
        run = run[:-1]
    while len(run) > 1 and enough(run[1:]):
        run = run[1:]
    return _Mention(grant, words[run[0]].start(), words[run[-1]].end(), frozenset(best))


def _same_words(a: _Mention, b: _Mention) -> bool:
    """Two mentions share at least half of the smaller one's title words."""
    return len(a.words & b.words) >= _SAME_WORDS_SHARE * min(len(a.words), len(b.words))


def _title_groups(sentence: str, grants: Sequence[_Grant]) -> list[_MentionGroup]:
    """The grants the sentence names by title, mentions of the same words
    grouped. A mention joins a group only when it shares words with every
    member: chaining through a long mention would merge two listed grants. A
    group inside another's words is dropped."""
    words = list(_WORD_RE.finditer(sentence))
    mentions = sorted((m for m in (_title_mention(g, words) for g in grants) if m is not None),
                      key=lambda m: m.start)
    groups: list[_MentionGroup] = []
    for mention in mentions:
        group = next((g for g in groups if all(_same_words(mention, m) for m in g)), None)
        if group is None:
            groups.append([mention])
        else:
            group.append(mention)
    return sorted((g for g in groups if not any(h is not g and _inside(g, h) for h in groups)),
                  key=lambda g: _span(g)[0])


def _inside(group: _MentionGroup, other: _MentionGroup) -> bool:
    """The group's words are a part of the other's: "advanced prostate cancer"
    inside a registry's full name names nothing on its own (held-out web205)."""
    words = frozenset().union(*(m.words for m in group))
    other_words = frozenset().union(*(m.words for m in other))
    return words < other_words


def _agency_groups(sentence: str, grants: Sequence[_Grant]) -> list[_MentionGroup]:
    """Grants the sentence names by their agency, one group per agency named:
    "completed St. Widget Hospital studies" names every grant that agency funds.
    Only near a funding word: "directing X at Widget University" names a post,
    not the university's grant (held-out UYQRUN)."""
    groups: dict[tuple[int, int], _MentionGroup] = {}
    lowered = sentence.lower()
    for grant in grants:
        agency = grant.agency.strip(" .").lower()
        if len(_title_stems(agency)) < 2:
            continue
        at = lowered.find(agency)
        if at >= 0 and _GRANT_CUE_RE.search(sentence, max(at - _CUE_REACH, 0), at + len(agency) + _CUE_REACH):
            span = (at, at + len(agency))
            groups.setdefault(span, []).append(_Mention(grant, *span))
    return list(groups.values())


def _span(group: _MentionGroup) -> tuple[int, int]:
    return min(m.start for m in group), max(m.end for m in group)


def _claimed_status(sentence: str, group: _MentionGroup) -> str | None:
    """The status word that describes the group: the nearest one before it
    within reach, else the nearest one after it within reach in the same
    clause; current or completed. A status word that is one of a grant's own
    title words is the title's. A current one that frames a topic ("my current
    research investigates <title words>") claims nothing, and hides a farther
    word; "my earlier work on <title words>" still dates it (CKDDEG-10)."""
    start, end = _span(group)
    title_stems = frozenset().union(*(m.grant.title_stems for m in group))
    before: list[tuple[int, str | None]] = []
    after: list[tuple[int, str | None]] = []
    for pattern, status in ((_CURRENT_CLAIM_RE, GRANT_STATUS_CURRENT),
                            (_COMPLETED_CLAIM_RE, GRANT_STATUS_COMPLETED)):
        for match in pattern.finditer(sentence):
            if _stem(match.group()) in title_stems:
                continue
            topical = (status == GRANT_STATUS_CURRENT and not _CONTINUE_VERB_RE.fullmatch(match.group())
                       and _TOPICAL_NOUN_RE.match(sentence, match.end()))
            claimed = None if topical else status
            if match.end() <= start and start - match.end() <= _STATUS_REACH_BEFORE:
                before.append((start - match.end(), claimed))
            elif (match.start() >= end and match.start() - end <= _STATUS_REACH_AFTER
                  and not _NEW_CLAUSE_RE.search(sentence, end, match.start())):
                after.append((match.start() - end, claimed))
    nearest = min(before or after, key=lambda found: found[0], default=None)
    return None if nearest is None else nearest[1]


def _status_contradicted(sentence: str, group: _MentionGroup) -> bool:
    """Every grant of the group has a status the sentence's word contradicts:
    "current" as of the CV's date, "completed" as of the summary's."""
    claimed = _claimed_status(sentence, group)
    if claimed is None:
        return False
    contradicting = _CLAIM_CONTRADICTED_BY[claimed]
    return all((m.grant.status_then if claimed == GRANT_STATUS_CURRENT else m.grant.status_now)
               in contradicting for m in group)


def _status_sentences(sentences: Sequence[str], grants: Sequence[_Grant]) -> tuple[str, ...]:
    """A sentence calling a grant current or completed against its own record:
    its end date, its heading, or no stated status at all (FMIGLR-07, RLADNC-10)."""
    return tuple(s for s in sentences if any(
        _status_contradicted(s, group) for group in _title_groups(s, grants) + _agency_groups(s, grants)))


def _not_received_sentences(sentences: Sequence[str], grants: Sequence[_Grant]) -> tuple[str, ...]:
    """A sentence counting an award the CV marks declined or withdrawn, without
    saying so ("three consecutive awards" over a DECLINED year, ATRKVV-02): it
    names that award alone, or among others with a count just before or among
    the words that name them."""
    found = []
    for sentence in sentences:
        if _NOT_RECEIVED_WORDS_RE.search(sentence) or not _FUNDING_RE.search(sentence):
            continue
        for group in _title_groups(sentence, grants):
            refused = [m.grant.status_now == GRANT_STATUS_NOT_RECEIVED for m in group]
            start, end = _span(group)
            counted = _COUNT_RE.search(sentence, max(start - _COUNT_REACH, 0), end)
            if all(refused) or (any(refused) and counted):
                found.append(sentence)
                break
    return tuple(found)


def _squeezed(text: str) -> str:
    """Lowercase letters and digits only: "N.I.H./NHLBI" reads as "N.I.H/NHLBI"."""
    return re.sub(r"[\W_]+", "", text).lower()


def _names_funder(funder: str, record_text: str) -> bool:
    """The record names the funder: its text, its words, a known funder's
    spellings, its acronym, or its initials ("National Widget Institute" for an
    "NWI" agency)."""
    if _squeezed(funder) in _squeezed(record_text):
        return True
    if any(f.claim.search(funder) and f.support.search(record_text) for f in _FUNDERS):
        return True
    stems = _title_stems(funder)
    if stems and stems <= _title_stems(record_text):
        return True
    acronym = funder if not stems else "".join(w[0] for w in funder.split() if w[:1].isupper())
    return len(acronym) >= 2 and re.search(rf"\b{re.escape(acronym)}\b", record_text) is not None


class _FunderPhrase(NamedTuple):
    """One "funded by X" (`covers_before`: it credits the grants named before
    it) or "X-funded" (it credits the grant named after it)."""
    start: int
    end: int
    funder: str
    covers_before: bool


def _funder_phrases(sentence: str) -> list[_FunderPhrase]:
    """The sentence's funder phrases, in order. "NIH- and NSF-funded projects"
    names two funders for one list, so neither alone is judged (held-out NJIKGI)."""
    phrases = [_FunderPhrase(m.start(), m.end(), m.group("funder").rstrip("."), True)
               for m in _FUNDED_BY_RE.finditer(sentence)]
    phrases += [_FunderPhrase(m.start(), m.end(), m.group("funder"), False)
                for m in _PRE_FUNDED_RE.finditer(sentence)
                if not _SHARED_FUNDING_RE.search(sentence, 0, m.start())]
    return sorted(phrases)


def _funder_scope(sentence: str, phrases: Sequence[_FunderPhrase], i: int,
                  groups: Sequence[_MentionGroup]) -> list[_MentionGroup]:
    """The grants phrase `i` credits. "funded by X" covers the list of grants
    that ends just before it, back to the previous funder phrase or clause
    break; "X-funded" the one grant it names next ("an X-funded study of
    <title words>")."""
    phrase = phrases[i]
    breaks = [m.start() for m in _CLAUSE_BREAK_RE.finditer(sentence)]
    if phrase.covers_before:
        before = [g for g in groups if _span(g)[1] <= phrase.start]
        if not before or phrase.start - _span(before[-1])[1] > _FUNDER_REACH:
            return []
        low = max([phrases[i - 1].end if i else 0, *(b for b in breaks if b < phrase.start)])
        return [g for g in before if _span(g)[0] >= low]
    end = phrase.end
    high = min([phrases[i + 1].start if i + 1 < len(phrases) else len(sentence),
                *(b for b in breaks if b > end)])
    after = [g for g in groups if _span(g)[0] >= end and _span(g)[1] <= high]
    return after[:1] if after and _span(after[0])[0] - end <= _PRE_FUNDER_REACH else []


def _funder_link_sentences(sentences: Sequence[str], grants: Sequence[_Grant]) -> tuple[str, ...]:
    """A sentence crediting a named grant to a funder its own line does not name
    (WPJHYT-05: three projects "funded by" the charity that funds only two).
    A pending or refused application funds nothing, so it is not judged."""
    found = []
    for sentence in sentences:
        groups = [funded for funded in ([m for m in g if m.grant.status_now not in _UNFUNDED]
                                        for g in _title_groups(sentence, grants)) if funded]
        phrases = _funder_phrases(sentence)
        for i, phrase in enumerate(phrases):
            if any(not any(_names_funder(phrase.funder, m.grant.record_text) for m in group)
                   for group in _funder_scope(sentence, phrases, i, groups)):
                found.append(sentence)
                break
    return tuple(found)


def _gives_role(role_re: re.Pattern, role: str) -> bool:
    return bool(role_re.search(role)) and not _NOT_OWN_PI_RE.search(role)


def _lead_role_sentences(sentences: Sequence[str], grants: Sequence[_Grant]) -> tuple[str, ...]:
    """A lead-role claim ("I led", "I serve as PI on") over the grant that is
    its object, when no grant those words may name gives that role (FCAAUV-07:
    no role on the grant lines). "..., on which I served as PI" points back,
    and is not judged."""
    found = []
    for sentence in sentences:
        groups = _title_groups(sentence, grants)
        claims = [(m.end(), _LEAD_ROLE_RE) for m in _LEAD_VERB_RE.finditer(sentence)]
        claims += [(m.end(), _PI_ROLE_RE) for m in _PI_CLAIM_RE.finditer(sentence)]
        for claim_end, role_re in claims:
            named = next((g for g in groups if _span(g)[0] >= claim_end), None)
            if named is None or not _ROLE_OBJECT_RE.match(sentence, claim_end, _span(named)[0]) \
                    or _span(named)[0] - claim_end > _ROLE_REACH:
                continue
            if not any(_gives_role(role_re, m.grant.role) for m in named):
                found.append(sentence)
                break
    return tuple(found)


def _per_claim_claims(sentences: Sequence[str], stage_4_5: Mapping,
                      stage_4: Mapping) -> list[SummaryClaim]:
    """The claims about one named grant that its own record contradicts."""
    written = _summary_date(stage_4_5)
    grants = _grants(stage_4, written)
    status_check = (CLAIM_GRANT_STATUS, _status_sentences,
                    "calls a grant current or completed against its own dates or stated status")
    checks = (
        # With no date to judge a grant's end date against, every dated grant
        # would read "not stated": no status claim is judged.
        *((status_check,) if written else ()),
        (CLAIM_NOT_RECEIVED, _not_received_sentences,
         "counts an award the CV marks declined, withdrawn or not funded"),
        (CLAIM_FUNDER_LINK, _funder_link_sentences,
         "credits a grant to a funder its own CV line does not name"),
        (CLAIM_LEAD_ROLE, _lead_role_sentences,
         "claims a lead role on a grant whose CV line gives no such role"),
    )
    claims = []
    for kind, find, message in checks:
        found = find(sentences, grants)
        if found:
            claims.append(SummaryClaim(kind, message, found))
    return claims


def lint_summary_unsupported_claim(stage_4_5: dict, stage_4: dict,
                                   source_lines: list[str] | None = None) -> list[dict]:
    """One finding per kind of claim the generated research summary makes and
    the CV gives no ground for (#1554): WARN for a claim of a kind the CV has
    none of, INFO for a claim its named grant's own record contradicts. The
    paragraph is signed by the owner, so an invented claim reaches a document
    they attest to; the score is not capped, since the rest of the document is
    unaffected."""
    stage_4_5 = stage_4_5 or {}
    sentences = summary_sentences(generated_summary(stage_4_5))
    presence = (_kind_claims(sentences, stage_4)
                + _funder_claims(sentences, _cv_text(stage_4, source_lines)))
    per_claim = _per_claim_claims(sentences, stage_4_5, stage_4) if sentences else []
    return [_finding("summary_unsupported_claim", severity, f"The research summary {claim.message}",
                     [f"claim={claim.kind}",
                      *(f"sentence={s[:_EVIDENCE_CHARS]}" for s in claim.sentences)])
            for severity, claims in ((_PRESENCE_SEVERITY, presence), (_PER_CLAIM_SEVERITY, per_claim))
            for claim in claims]
