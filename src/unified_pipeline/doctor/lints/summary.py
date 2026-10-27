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

Those four are presence checks, reported at WARN. Per-claim checks (YUYVIG
and NECRGC, #1554) then judge a sentence against the one record it names,
found by its title's words or, for a status word, its agency, and report at
INFO:

- a status word ("currently", "completed", "earlier") that the grant's own
  end date, heading or lack of either contradicts, or "completed" for a study
  its own line says runs on or closed early; "earlier" for a paper in press;
  "current" is not judged where a current research-activities entry names
  the same words;
- an award the CV marks declined, withdrawn or not funded, counted as held;
- "funded by X" or "X-funded" over a grant whose own line does not name X,
  or X the CV names only as a collaborator;
- a lead-role claim ("I led", "I serve as PI on") over a grant whose role
  does not give it, or gives it as shared; and over a named entry that is no
  grant, when no entry holding the name gives the verb's role;
- a PI role "since" a year before the earliest grant that could give it;
- an agent combined into a grant's design ("in combination with X") that its
  own line lacks and another grant's line holds.

Only a summary the model generated is judged: an existing M1 statement kept
as-is is the CV's own text. Still not a fact check: an invented outcome, a
role sequence, a sponsor list, inflated mentoring, or a claim about no record
is not reported.
"""
import math
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
)
from unified_pipeline.stage_4_5_research_summary import (
    GENERATION_METHOD_LLM,
    GRANT_STATUS_BY_CODE,
    GRANT_STATUS_COMPLETED,
    GRANT_STATUS_CURRENT,
    GRANT_STATUS_NOT_RECEIVED,
    GRANT_STATUS_NOT_STATED,
    PUBLICATION_TAXONOMY_CODES,
    end_date_state,
    grant_status,
    is_current_entry,
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
#: "Supervisor:", which names the owner's own supervisor (held-out web206),
#: nor a "thesis", "research" or "academic" advisor, which usually does too.
#: Not "Faculty Advisory Group", a committee (FVQANK).
_MENTORING_SUPPORT_RE = re.compile(
    r"\b(?:mentor\w*|mentee\w*|trainee\w*|precept\w*|advisee\w*"
    r"|faculty\s+advis[eo]rs?\b|student\s+projects?"
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
# Each check below finds the record a sentence names -- a grant by its title's
# words or its agency, an in-press paper by its title, an entry by its name --
# and judges the sentence's word against that record's own words.

CLAIM_GRANT_STATUS = "grant_status"
CLAIM_NOT_RECEIVED = "not_received_award"
CLAIM_FUNDER_LINK = "funder_link"
CLAIM_LEAD_ROLE = "lead_role"
CLAIM_ROLE_SPAN = "role_span"
CLAIM_DESIGN_DETAIL = "design_detail"
CLAIM_ENTRY_ROLE = "entry_role"
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
#: For a status word, a long trial title is named by this many of its words:
#: a protocol title of 25 words is never restated whole (HVSKET-03). The
#: funder and role checks keep the share rule: at this cap a
#: description of one grant named a neighbour's title and took its funder (a
#: clean NECRGC run, UHBORP), and a cap of 6 merged WPJHYT-05's projects (#1554).
_STATUS_TITLE_WORDS_CAP = 8
#: A one-word title, or a one-word agency, names no grant: a single word
#: recurs in any research sentence.
_MIN_NAMING_WORDS = 2
#: An acronym of one letter matches any capital.
_MIN_ACRONYM_LETTERS = 2
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
#: A CV that reaches only an earlier year is current to that year's last day.
_YEAR_END_MONTH, _YEAR_END_DAY = 12, 31
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
#: A grant's own text saying the study has not finished, whatever its dates
#: say about the owner's part in it: it runs on under another PI (MFJRKW-09),
#: or it stopped early for lack of enrollment (HVSKET-03). Either contradicts
#: "completed". A study the line calls closed, with no reason given, has
#: finished, so a bare "closed" does not count.
_STILL_RUNNING_RE = re.compile(
    r"\b(?:is|are|remains?|still|currently)\s+(?:currently\s+|still\s+)?(?:active|ongoing|open|enrolling|recruiting)\b",
    re.IGNORECASE)
_CLOSED_EARLY_RE = re.compile(
    r"\b(?:closed|terminated|stopped|halted|ended|discontinued)\s+(?:early|prematurely|due\s+to|because\s+of|for\s+lack)\b"
    r"|\b(?:insufficient|inadequate|poor|low|slow)\s+(?:enrollment|accrual|recruitment)\b", re.IGNORECASE)
#: A publication the CV lists as not yet published is current work: calling
#: the CV's newest, in-press paper earlier work got it wrong (QZCHAG-13).
_IN_PRESS_RE = re.compile(r"\b(?:in\s+press|forthcoming|accepted\s+for\s+publication)\b", re.IGNORECASE)
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
_LEAD_VERBS = r"(?:led|lead|direct(?:ed)?|founded|coordinate[ds]?)"
_LEAD_VERB_RE = re.compile(rf"\bI\s+(?:have\s+)?{_LEAD_VERBS}\b")
#: The same claim over an entry the CV names, not a grant, with a second verb
#: it may chain, a founding and a directing claim over one name (OKRTPJ-13).
#: Its object is a capitalised name; the role each verb needs is its own:
#: founding needs a founder, directing a director, coordinating a coordinator.
_ENTRY_LEAD_CLAIM_RE = re.compile(
    rf"\bI\s+(?:have\s+)?(?P<first>{_LEAD_VERBS})(?:\s+and\s+(?:continue\s+to\s+)?(?P<second>{_LEAD_VERBS}))?\b")
_NAMED_OBJECT_RE = re.compile(
    r"\s+(?:the\s+|an?\s+)?(?P<name>[A-Z][\w'’&-]*(?:\s+(?:(?:of|and|for|in|on|the|&)\s+)*[A-Z][\w'’&-]*)*)")
_ROLE_FOR_VERB = (
    ("found", re.compile(r"\bfound\w*", re.IGNORECASE)),
    ("direct", re.compile(r"\bdirect\w*", re.IGNORECASE)),
    ("coordinat", re.compile(r"\bcoordinat\w*", re.IGNORECASE)),
)
#: A named entry is judged only when its name holds this many title words:
#: two capitalised words ("Grand Rounds") name many entries and none.
_MIN_ENTRY_NAME_WORDS = 3
_PI_CLAIM_RE = re.compile(
    r"\bserv(?:e|ed|ing)\s+as\s+(?:the\s+)?(?:Multiple\s+)?(?:Principal\s+Investigator|PI)\b")
#: A grant role that supports a lead-role claim, and one that supports a PI claim.
_LEAD_ROLE_RE = re.compile(
    r"\b(?:PI|MPI|princip(?:al|le)\s+investigator|director|founder|founding|chair\w*|lead\w*|head"
    r"|coordinator)\b", re.IGNORECASE)
_PI_ROLE_RE = re.compile(r"(?<![-\w])(?:PI|MPI|princip(?:al|le)\s+investigator)\b", re.IGNORECASE)
#: The words between a lead-role claim and the grant that is its object hold
#: no clause punctuation: "I served as PI on a study of <title>". A comma that
#: opens a quoted title is the object itself (XNWSZN-15).
_ROLE_OBJECT_RE = re.compile(r"(?:[^,;:.]|,\s*[\"“])*\Z")
_NOT_OWN_PI_RE = re.compile(r"\b(?:co|sub|site)[-\s]*(?:PI|principal|investigator)", re.IGNORECASE)
#: A lead role the grant gives as shared: a co-founder or co-director (its
#: role), or a role its own text says was held jointly with a colleague, was
#: not held alone, so "I founded" or "I direct" overstates it (XNWSZN-15). Only
#: the role is read for a "co-" title: the text may name another person's.
_CO_LEAD_ROLE_RE = re.compile(r"\bco-?(?:found\w*|direct\w*|chair\w*|lead\w*)", re.IGNORECASE)
_LEAD_ROLE_SHARED_WITH_RE = re.compile(
    r"\b(?:found|direct|chair|lead)\w*\s+(?:along|together|jointly)\s+with\b", re.IGNORECASE)
#: A name the CV gives only as a collaborator, never as a funder: a grant line
#: naming a body after a collaboration cue is not that body's funding, although
#: stage 4 put both names in the grant's agency (DYLJXC-16). The name follows
#: the cue directly, after an optional "the".
_COLLABORATION_CUE_RE = re.compile(
    r"\b(?:in\s+(?:collaboration|partnership)\s+with|collaborat\w*\s+(?:research\s+)?with)\s+(?:the\s+)?",
    re.IGNORECASE)
#: A PI role dated by "since <year>": WWRSAL-02 dated it three years before
#: the CV's earliest grant the owner may lead, to a fellowship under another PI.
_PI_SINCE_RE = re.compile(r"\b(?:Principal\s+Investigator|PI)\s+since\s+(?P<year>(?:19|20)\d{2})\b")
#: A design detail a sentence adds to the grant it names: the agent a drug is
#: combined with. TJDPJE-01 gave one trial another trial's co-therapy; the
#: agent names a grant's design only when that grant's own line holds it. Not
#: a comparator that is no agent of its own.
_COMBINED_AGENT_RE = re.compile(
    r"\b(?:in\s+combination\s+with|combined\s+with|plus)\s+(?P<agent>[A-Za-z][\w-]{3,})", re.IGNORECASE)
_NO_AGENT_WORDS = frozenset(("placebo", "standard", "usual", "conventional", "other", "others"))


class _Grant(NamedTuple):
    """One stage-4 grant (M2A, M2B or M2C), read once for every sentence.
    `unfinished`: its own text says the study runs on or closed early
    (`_STILL_RUNNING_RE`, `_CLOSED_EARLY_RE`). An in-press publication is
    read into this shape too, for the status check alone (`_in_press_records`)."""
    title_stems: frozenset[str]
    agency: str
    role: str
    record_text: str
    status_then: str | None
    status_now: str | None
    unfinished: bool = False
    start_year: int | None = None


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
    return date(max(years), _YEAR_END_MONTH, _YEAR_END_DAY)


def _grant_ended(record: Mapping, as_of: date | None) -> bool | None:
    """Stage 4.5's end_date_state() for the grant as of `as_of`, the one
    reading of whether an end date has passed (#1713); None with no date."""
    return None if as_of is None else end_date_state(record, as_of)


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
            grant_status(entry.code, record, _grant_ended(record, cv_date)),
            grant_status(entry.code, record, _grant_ended(record, written)),
            bool(_STILL_RUNNING_RE.search(entry.text) or _CLOSED_EARLY_RE.search(entry.text)),
            _first_year(_field(fields, "start_date"))))
    return grants


def _first_year(text: str) -> int | None:
    match = _YEAR_RE.search(text)
    return int(match.group()) if match else None


def _in_press_records(stage_4: Mapping) -> list[_Grant]:
    """Each publication the CV lists as in press, as a current record for the
    status check: no agency or role, so no other check reads it."""
    records = []
    for entry in _fields_entries(stage_4):
        if entry.code in PUBLICATION_TAXONOMY_CODES and _IN_PRESS_RE.search(entry.text):
            records.append(_Grant(_title_stems(_field(entry.fields, "title")), "", "", entry.text,
                                  GRANT_STATUS_CURRENT, GRANT_STATUS_CURRENT))
    return records


def _current_research_stems(stage_4: Mapping, cv_date: date) -> list[frozenset[str]]:
    """The words of each research-activities entry stage 4.5 calls current as
    of the CV's date (is_current_entry: undated, or open to the present). The
    context gave the model those lines as [CURRENT] research."""
    return [_title_stems(entry.text) for entry in _fields_entries(stage_4)
            if entry.code == _RESEARCH_ACTIVITIES_CODE and is_current_entry(
                {"extracted_fields": dict(entry.fields), "text": entry.text}, entry.code, cv_date)]


def _title_mention(grant: _Grant, words: Sequence[re.Match], most_needed: float) -> _Mention | None:
    """Where the sentence names the grant's title, else None: the run of its
    words holding the most title words, as `words`, and as the span, that run
    cut back to the fewest words that still name the title. A title naming
    needs no more than `most_needed` of its words."""
    if len(grant.title_stems) < _MIN_NAMING_WORDS:
        return None
    needed = max(min(_MIN_SHARED_TITLE_WORDS, len(grant.title_stems)),
                 min(_MIN_TITLE_SHARE * len(grant.title_stems), most_needed))

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


def _title_groups(sentence: str, grants: Sequence[_Grant],
                  most_needed: float = math.inf) -> list[_MentionGroup]:
    """The grants the sentence names by title, mentions of the same words
    grouped. A mention joins a group only when it shares words with every
    member: chaining through a long mention would merge two listed grants. A
    group inside another's words is dropped."""
    words = list(_WORD_RE.finditer(sentence))
    mentions = sorted((m for m in (_title_mention(g, words, most_needed) for g in grants) if m is not None),
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
        if len(_title_stems(agency)) < _MIN_NAMING_WORDS:
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


def _named_by_current_research(sentence: str, group: _MentionGroup,
                               research: Sequence[frozenset[str]]) -> bool:
    """Every word the sentence names the group's title by is in one current
    research-activities entry: a "current" claim over a grant that has ended
    restates the CV's own present research statement (IZIXVF)."""
    words = list(_WORD_RE.finditer(sentence))
    stems = frozenset(_stem(words[i].group()) for m in group for i in m.words)
    return bool(stems) and any(stems <= entry for entry in research)


def _contradicts(claimed: str, grant: _Grant) -> bool:
    """The grant's own record contradicts the claimed status: "current" as of
    the CV's date, "completed" as of the summary's, or "completed" for a study
    its own text says runs on or closed early."""
    if claimed == GRANT_STATUS_CURRENT:
        return grant.status_then in _CLAIM_CONTRADICTED_BY[claimed]
    return grant.status_now in _CLAIM_CONTRADICTED_BY[claimed] or grant.unfinished


def _status_contradicted(sentence: str, group: _MentionGroup,
                         research: Sequence[frozenset[str]] = ()) -> bool:
    """Every record of the group has a status the sentence's word contradicts,
    and a "current" word is not about the CV's current research."""
    claimed = _claimed_status(sentence, group)
    if claimed is None:
        return False
    if claimed == GRANT_STATUS_CURRENT and _named_by_current_research(sentence, group, research):
        return False
    return all(_contradicts(claimed, m.grant) for m in group)


def _status_sentences(sentences: Sequence[str], grants: Sequence[_Grant],
                      research: Sequence[frozenset[str]] = ()) -> tuple[str, ...]:
    """A sentence calling a grant current or completed against its own record:
    its end date, its heading, or no stated status at all (FMIGLR-07, RLADNC-10).
    `grants` may hold in-press publications too (`_in_press_records`)."""
    return tuple(s for s in sentences if any(
        _status_contradicted(s, group, research)
        for group in _title_groups(s, grants, _STATUS_TITLE_WORDS_CAP) + _agency_groups(s, grants)))


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
    return len(acronym) >= _MIN_ACRONYM_LETTERS and re.search(rf"\b{re.escape(acronym)}\b", record_text) is not None


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


def _named_only_as_collaborator(funder: str, texts: Sequence[str]) -> bool:
    """At least one stage-4 entry's text names the funder, and every one that
    does names it right after a collaboration cue ("in collaboration with the
    <funder>"). The texts are the CV's own words; the agency field is stage
    4's reading of them, and took the collaborator for a funder (DYLJXC-16)."""
    name = re.compile(r"\s+".join(map(re.escape, funder.split())), re.IGNORECASE)
    named = False
    for text in texts:
        for match in name.finditer(text):
            cue_ends = {cue.end() for cue in _COLLABORATION_CUE_RE.finditer(text, 0, match.start())}
            if match.start() not in cue_ends:
                return False
            named = True
    return named


def _funder_claim_sentences(sentences: Sequence[str], grants: Sequence[_Grant],
                            texts: Sequence[str]) -> tuple[str, ...]:
    """The funder-link sentences, and those crediting funding to a name the
    CV gives only as a collaborator, in summary order."""
    linked = set(_funder_link_sentences(sentences, grants))
    return tuple(s for s in sentences if s in linked or any(
        _named_only_as_collaborator(phrase.funder, texts) for phrase in _funder_phrases(s)))


def _gives_role(role_re: re.Pattern, grant: _Grant) -> bool:
    """The grant's role gives the claimed role as the owner's own: not a co-,
    sub- or site PI, and, for a lead role, not one held with someone else."""
    role = grant.role
    if not role_re.search(role) or _NOT_OWN_PI_RE.search(role):
        return False
    return role_re is not _LEAD_ROLE_RE or not (
        _CO_LEAD_ROLE_RE.search(role) or _LEAD_ROLE_SHARED_WITH_RE.search(grant.record_text))


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
            if not any(_gives_role(role_re, m.grant) for m in named):
                found.append(sentence)
                break
    return tuple(found)


def _entry_words(entry: _FieldsEntry) -> str:
    """An entry's text, string fields and headings: where its name and role are."""
    fields = " ".join(v for v in entry.fields.values() if isinstance(v, str))
    return " ".join((entry.text, fields, " ".join(entry.hierarchy)))


def _role_for_verb(verb: str) -> re.Pattern:
    return next((role for stem, role in _ROLE_FOR_VERB if verb.lower().startswith(stem)), _LEAD_ROLE_RE)


def _gives_entry_role(verb: str, words: str) -> bool:
    """The entry's words give the verb's role as the owner's alone: a "co-"
    title or a role held "along with" someone does not count."""
    own = _LEAD_ROLE_SHARED_WITH_RE.sub(" ", _CO_LEAD_ROLE_RE.sub(" ", words))
    return bool(_role_for_verb(verb).search(own))


def _entry_role_sentences(sentences: Sequence[str], entries: Sequence[_FieldsEntry]) -> tuple[str, ...]:
    """A lead-role claim on a named entry when entries hold the name and none
    gives a verb's role (OKRTPJ-13: the CV names the owner the event's
    founder, never its director). Every
    entry holding the name is a candidate; with none, nothing is judged, and
    a name a grant holds is the grant check's (`_lead_role_sentences`)."""
    words = [(_title_stems(_entry_words(entry)), _entry_words(entry), entry.code in _GRANT_CODES)
             for entry in entries]
    found = []
    for sentence in sentences:
        for claim in _ENTRY_LEAD_CLAIM_RE.finditer(sentence):
            name = _NAMED_OBJECT_RE.match(sentence, claim.end())
            stems = _title_stems(name.group("name")) if name else frozenset()
            if len(stems) < _MIN_ENTRY_NAME_WORDS:
                continue
            holders = [(text, grant) for entry_stems, text, grant in words if stems <= entry_stems]
            if not holders or any(grant for _text, grant in holders):
                continue
            holders = [text for text, _grant in holders]
            verbs = [verb for verb in claim.group("first", "second") if verb]
            if any(not any(_gives_entry_role(verb, text) for text in holders) for verb in verbs):
                found.append(sentence)
                break
    return tuple(found)


def _role_span_sentences(sentences: Sequence[str], grants: Sequence[_Grant]) -> tuple[str, ...]:
    """A PI role dated from before the CV's earliest grant the owner may have
    led: a funded one whose role gives PI or states no role (WWRSAL-02). With
    no such grant dated, nothing is judged."""
    starts = [g.start_year for g in grants if g.start_year is not None and g.status_now not in _UNFUNDED
              and (not g.role or _gives_role(_PI_ROLE_RE, g))]
    if not starts:
        return ()
    earliest = min(starts)
    return tuple(s for s in sentences if any(int(m.group("year")) < earliest for m in _PI_SINCE_RE.finditer(s)))


def _foreign_agent(sentence: str, group: _MentionGroup, grants: Sequence[_Grant]) -> bool:
    """An agent combined into the group's design, in or just after the words
    that name it, that no grant of the group holds and another grant does."""
    start, end = _span(group)
    for match in _COMBINED_AGENT_RE.finditer(sentence, start):
        if match.start() > end + _STATUS_REACH_AFTER:
            break
        agent = match.group("agent")
        if agent.lower() in _NO_AGENT_WORDS:
            continue
        holds = re.compile(rf"\b{re.escape(agent)}\b", re.IGNORECASE).search
        if not any(holds(m.grant.record_text) for m in group) and any(holds(g.record_text) for g in grants):
            return True
    return False


def _design_detail_sentences(sentences: Sequence[str], grants: Sequence[_Grant]) -> tuple[str, ...]:
    """A sentence giving the grant it names another grant's combined agent
    (TJDPJE-01)."""
    return tuple(s for s in sentences if any(
        _foreign_agent(s, group, grants) for group in _title_groups(s, grants)))


def _per_claim_claims(sentences: Sequence[str], stage_4_5: Mapping,
                      stage_4: Mapping) -> list[SummaryClaim]:
    """The claims about one named record that its own words contradict."""
    written = _summary_date(stage_4_5)
    grants = _grants(stage_4, written)
    status_records = grants + _in_press_records(stage_4)
    texts = [entry.text for entry in _fields_entries(stage_4)]
    status_check = (CLAIM_GRANT_STATUS, lambda: _status_sentences(
                        sentences, status_records, _current_research_stems(stage_4, _cv_date(stage_4, written))),
                    "calls a grant current or completed against its own dates or stated status")
    checks = (
        # With no date to judge a grant's end date against, every dated grant
        # would read "not stated": no status claim is judged.
        *((status_check,) if written else ()),
        (CLAIM_NOT_RECEIVED, lambda: _not_received_sentences(sentences, grants),
         "counts an award the CV marks declined, withdrawn or not funded"),
        (CLAIM_FUNDER_LINK, lambda: _funder_claim_sentences(sentences, grants, texts),
         "credits a grant to a funder its own CV line does not name"),
        (CLAIM_LEAD_ROLE, lambda: _lead_role_sentences(sentences, grants),
         "claims a lead role on a grant whose CV line gives no such role"),
        (CLAIM_ENTRY_ROLE, lambda: _entry_role_sentences(sentences, _fields_entries(stage_4)),
         "claims a lead role on a CV entry whose own words give no such role"),
        (CLAIM_ROLE_SPAN, lambda: _role_span_sentences(sentences, grants),
         "dates a PI role from before the CV's earliest grant that gives it"),
        (CLAIM_DESIGN_DETAIL, lambda: _design_detail_sentences(sentences, grants),
         "gives a grant a design detail that another grant's CV line holds, not its own"),
    )
    claims = []
    for kind, find, message in checks:
        found = find()
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
