"""Header pin for stage 3b: an author-placed, confidently mapped section wins (#312).

Stage 3a maps a CV's own section header to a taxonomy code, often with
confidence 1.0 (`INVITED PRESENTATIONS` -> R, `INSTITUTIONAL LEADERSHIP
ACTIVITIES` -> O). The stage 3b prompt hands that to the model as a "weak hint",
and on a topically homogeneous CV the model answers K4 at 0.90-0.95 for a talk
the author filed under Invitations, so the section renders empty and the rows
land in teaching. This module inverts the precedence for one narrow, measured
set of confusions: `HEADER_PIN_OVERRIDES` names, per pinned code, the codes a
model answer may NOT override. Everything else -- S1 under the wrong heading,
an S8 with an author list under an invited-presentations heading -- stays the
model's call, which is what the "content overrides hierarchy" rule exists for.

Batch EBYSBC (class E11) added three narrow extensions: an author-less S8 loses
to an R pin; a Q2 grant review loses to a Q3 sub-heading even when its parent
disagrees (`leaf_header_code`); and `content_pin_code`, a handful of shapes
whose code does not depend on the heading at all. An O or K3 answer under a
sub-heading 3a maps to N1 is N1 (#1938), and an O answer under an L3
sub-heading is L3 (#1977), the same leaf-only way. `is_note_not_record` names
the T lines stage 3b's T-validation must not turn into records. `mentee_pin_code`
(#1251) recodes a mentee's own awards, listed under the owner's mentoring, to N4.
`mentor_grant_pin_code` (#1710) recodes a grant listed under a heading that names
the owner as mentor or sponsor to N2. `someone_elses_pin_code` (#1573) sends a
paper under an acknowledgements heading, and a "mentee" under a review panel's
or a laboratory staff heading, to the Appendix (T), and an abstract that names
its award under an abstracts heading to S8.
An H pin (#1577) keeps an unsure grant answer with no grant marker in Honors.
An S1 pin (#1708) keeps an unsure S8 with a PMID or DOI under a peer-reviewed
journals heading, unless it is a short or meeting item or a proceedings venue.
A content pin (#1695) recodes a per-article citation-metrics line ("<Surname> et
al. (<year>) <journal> JIF: <x>") from a publication or editorial code to S0,
and a teaching code under a student-evaluations heading to T (under a
combined "X and Evaluations" heading, only an evaluation-summary row).
A chief-trainee pin (#1731) recodes a position or leadership code on a line
that opens, after an optional date, with "Chief Resident(cy)" or "Chief Fellow" to C.
A reviewer pin (#1413) recodes an unsure Q4C journal name to Q4D when the
"Reviewer for the following journals:" line heading its list went to the model
in an earlier batch.
`non_seat_pin_code` (#1580) recodes a Q2 or P row that is no seat on a body:
consulting, a company's advisory board and expert work in litigation are D3,
and a meeting the owner attended is B2.

Pure functions over one hierarchy group's classified entries and its
`TaxonomyContext`, except `apply_non_seat_pins`, which reads the whole CV's
entries after T-validation (where some of those rows got their Q2) and needs
no context; no LLM, no I/O. Imports only `.context`.
"""

import logging
import re
from collections.abc import Iterable

from .context import TaxonomyContext

logger = logging.getLogger(__name__)

# A stage 3a mapping this confident is the CV author's own placement, not a guess.
HEADER_PIN_MIN_CONFIDENCE = 0.95

# Section titles that name a scope, not a section. Stage 3a maps a bare one to R
# whatever it sits under (a teaching CV's "Local" -> R), so one of these alone
# carries no authority; it pins only next to a level that names a real section.
GEOGRAPHIC_SCOPE_TITLES = frozenset({"local", "regional", "national", "international"})

# pinned code -> the model answers that lose to it. Each pair is a confusion
# measured on real CVs (#312 C0ZGFW; #312 comment 2): teaching or an honor read
# off an invited talk's content, a course-director title read off a leadership row.
HEADER_PIN_OVERRIDES: dict[str, frozenset[str]] = {
    "R": frozenset({"K1", "K2", "K4", "H"}),
    "O": frozenset({"K3", "K4"}),
}

# classification_source of a real model answer; a fallback or empty entry is not one.
_MODEL_SOURCE = "llm"

# --- EBYSBC E11 (#312): the pins the agreeing-levels rule above cannot reach ---
#
# S8 under an R pin. A talk the author filed under Invited Lectures with no author
# list in front of it is the owner's invited talk (MIFYLG, KYOPUV). An author list
# or a contributed-work marker keeps the model's S8: that shape is an abstract.
# So does a confident S8: the pin breaks the model's tie, it does not overrule a
# firm answer (the 1990s research-conference sub-lists filed under talks score 0.85+).
_S8_PIN_MAX_MODEL_CONFIDENCE = 0.80
_AUTHOR_LIST = re.compile(
    r"\b[A-Z][A-Za-z'\u2019-]+,?\s+(?:[A-Z]\.\s?){1,3}(?=[,;.\s*]|$)"  # Doe, J.A. / Doe J.
    r"|\b[A-Z][A-Za-z'\u2019-]+\s+[A-Z]{1,3},"                          # Doe JA,
    r"|\bet\s+al\b"
)
# "Washington, D.C." has the shape of "Doe, D.C."; it names a place, not an author.
_PLACE_INITIALS = re.compile(r"\bD\.\s?C\.")
_CONTRIBUTED_WORK = re.compile(
    r"\b(?:poster|abstracts?|contributed|finalist|oral\s+presentation|platform\s+presentation"
    r"|research\s+forum|residents?\s+conference)\b",
    re.I,
)

# Q2 under a heading 3a maps to Q3. Only the most specific level has to say Q3:
# a grant-reviewer sub-heading under a general service heading maps the parent Q2/Q4 at 0.5 (HFAJCC,
# GJXIWD). The row must read as a grant review, from its heading or its own words,
# and must not name a committee, board, advisory group or session, which stay the model's Q2.
_GRANT_REVIEW_PIN = "Q3"
_GRANT_REVIEW_OVERRIDABLE = frozenset({"Q2"})

# --- #1938: a role under a mentoring-programs sub-heading is N1 ---
#
# XAXVYB's "Leadership and mentoring in programs" sub-heading (3a: N1 at 1.0,
# under a MENTORING parent mapped N3 0.5 / N1 0.3) held a fellowship director,
# an assistant DIO and a division director of education. The prompt's rule 17
# sends a training-program director to O, so all three came back O at 0.70-0.80,
# rendered in Institutional Leadership and left the N1 slot empty. Like Q3, only
# the most specific level has to say N1. A mentee row is N3A/N3B, not O or K3,
# so the code set alone keeps it out of this pin.
_MENTORING_PROGRAM_PIN = "N1"
_MENTORING_PROGRAM_OVERRIDABLE = frozenset({"O", "K3"})

# --- #1977: a role under a Clinical Leadership sub-heading is L3 ---
#
# XAXVYB's (and RLADNC's) template-layout "Clinical Leadership" sub-heading
# (3a: L3 at >= 0.95) held leadership roles the model coded O, so the L3 slot
# rendered empty. Content can read either way; the author's placement under the
# template's own heading wins, as for N1. Over 362 stored runs this moves 4 rows
# in those 2 runs, nothing else.
_CLINICAL_LEADERSHIP_PIN = "L3"
_CLINICAL_LEADERSHIP_OVERRIDABLE = frozenset({"O"})
_GRANT_HEADING = re.compile(r"\bgrants?\b|\bfunding\b", re.I)
_GRANT_REVIEW_TEXT = re.compile(r"\breview(?:er|ers|ed|s|ing)?\b|\bgrants?\b|\bstudy\s+sections?\b", re.I)
_NOT_A_GRANT_REVIEW = re.compile(
    r"(?<!review\s)\bcommittees?\b|\badvisory\b|\bworking\s+group\b|\bco-?chair\b"
    r"|\bsession\b|\bboard\b|\babstracts?\b",
    re.I,
)

# --- YUYVIG (#1577): an award under an honors heading is not current funding ---
#
# Under a heading 3a pins to H, an unsure grant answer (M2, M2A/B/C) on a line
# that carries no grant marker is the author's award: a "1997 Example Investigator
# Award" or "2007 Example Group Grant, Example Fund" rendered as a one-year Current
# Research Funding table with no title and no PI (UVZNIC, ZDOAZO, IXJMKS; model
# confidence 0.50-0.55). A grant or activity number, a dollar amount, a period
# with two ends, or a PI role at the head of the row keeps the model's grant code.
_GRANT_CODES = frozenset({"M2", "M2A", "M2B", "M2C"})
_H_PIN_MAX_MODEL_CONFIDENCE = 0.80
_GRANT_MARKER = re.compile(
    r"\b[A-Z]\d{2}\b"                                                       # R01, K23, T32
    r"|\b(?=[A-Z0-9]*\d{2})(?=[A-Z0-9]*[A-Z]{2})[A-Z0-9]{6,}\b"            # 2T32GM000000
    r"|\$\s?\d|\b\d[\d,.]*\s?(?i:USD|dollars)\b"                            # an amount
    r"|\b(?:19|20)\d{2}\s*(?:[-\u2013\u2014]|\b(?i:to)\b)\s*(?:\d{1,2}/)?"  # two-ended period
    r"(?:[A-Za-z]{3,9}\.?\s*)?(?:(?:19|20)\d{2}\b|\d{2}\b(?!/)|(?i:present|current|date)\b)"
)
_PI_ROLE_LEAD = re.compile(
    r"^[\s\d/\-\u2013\u2014,.:;*\u2022]*(?:(?i:(?:principal|co-?)\s*investigator)\b|(?i:co-?)?PI\b)"
)

# --- #1708: a journal article under a peer-reviewed journals heading is not an abstract ---
#
# Under a heading 3a pins to S1, an unsure S8 for a row with a PubMed ID or DOI is
# the author's journal article: a 17-page paper whose title says "proceedings of
# a meeting" rendered as the owner's first Abstract (RBPXCD, model at 0.65). The
# model keeps its S8 for a confident answer, a one- or two-page item (the
# supplement abstracts the corpus files under research articles), a row with no
# page range that names a meeting or a supplement, and a proceedings venue
# ("Annu Symp Proc. 2031", "Int Conf", "Lecture Notes"), which S8 names too.
# The PMID or DOI is read off the row's own text: stage 3b runs before stage 4
# extracts one or stage 5 looks one up.
_S1_PIN = "S1"
_ABSTRACT_CODE = "S8"
_S1_PIN_MAX_MODEL_CONFIDENCE = 0.80
_PUBLICATION_ID = re.compile(
    r"\bPMID\b\W{0,3}\d"                    # PMID: 12345678
    r"|\bpubmed\.ncbi\.nlm\.nih\.gov/\d"     # a PubMed link carries the PMID
    r"|\b10\.\d{4,9}/\S",                    # a DOI, bare, after "doi:", or in a doi.org link
    re.I,
)
# A page range after a volume's colon ("12:340-52") or "pp." ("pp. 340-352").
# An end page shorter than the start page shares the start's leading digits.
_PAGE_RANGE = re.compile(r"(?::\s*|\bpp?\.\s*)(\d{1,6})\s*[-\u2013\u2014]\s*(\d{1,6})\b", re.I)
_MIN_ARTICLE_PAGES = 3
_MEETING_MARKER = re.compile(r"\b(?:abstracts?|posters?|presented\s+at|suppl(?:ement)?)\b", re.I)
# A venue that is itself a proceedings: "Proc"/"Proceedings" or "Symp" straight
# before a year or volume, an abbreviated "Conf", "Lecture Notes". A title's
# "Proceedings of the <meeting>" and a journal named "Proc <Society>" do not match.
_PROCEEDINGS_VENUE = re.compile(
    r"\b(?:Proc(?:eedings)?|Symp(?:osium)?)\b\.?,?\s*\d"
    r"|\bConf\b|\bLecture\s+Notes\b",
    re.I,
)

# Content pins: shapes whose code does not depend on the heading they sit under.
# Only the owner-attended forms: "attending" is a physician title and "attendees" or
# a bare "attendance" counts the audience of a course the owner taught.
_ATTENDED = re.compile(r"\b(?:attended|attendee|attendance\s+at)\b", re.I)
_TEACHING_ROLE = re.compile(
    r"\b(?:director|faculty|speaker|instructor|lecturer|moderator|organi[sz]er|presenter|taught|chair)\b",
    re.I,
)
# A training title at the head of the row, after an optional date range:
# "2031-2032 Intern, Example Medicine", "Chief Resident, ...", "Postdoctoral Fellow in ...".
# "Faculty Fellow", "Senior Staff Fellow", "Teaching Fellow", "Fellow of ...", a bare
# "Research Fellow, <body>" ("Research Fellow in <field>" is training) and "Resident
# Director" do not match, nor does a "Senior ... Fellow" affiliate title
# (`_SENIOR_FELLOW`) or an honorific "Fellow, <college/academy/society>"
# (`_HONORIFIC_FELLOW`): those are positions or honours, not training.
_TRAINEE_TITLE = re.compile(
    r"^[\s\d/\-\u2013\u2014,.]*(?:present\s*)?[,\s]*"
    r"(?:(?:assistant|junior|senior|chief)\s+(?:and\s+(?:senior|chief)\s+)?)?"
    r"(?:(?:post-?doctoral|clinical)\s+(?:research\s+)?|research\s+(?=fellow\s+in\b))?"
    r"(?:intern|resident(?!\s+director\b)|fellow)\b(?!\s+of\b)",
    re.I,
)
_SENIOR_FELLOW = re.compile(r"\bsenior\s+(?:\w+\s+)?fellow\b", re.I)
_HONORIFIC_FELLOW = re.compile(
    r"^[\s\d/\-\u2013\u2014,.]*(?:present\s*)?[,\s]*fellow\b,?\s+(?:the\s+)?"
    r"(?:[\w&'\u2019-]+\s+){0,4}(?:college|academy|society)\b",
    re.I,
)
# A trainee title qualified as a non-training role is a position (#1581, YUYVIG):
# "Fellow (undergraduate faculty advisor), <residential college>" is a faculty
# appointment, and "Resident Veterinarian, <horse farm>" is employment. A resident
# veterinarian, artist or scholar is training only at a college, university,
# hospital or school ("Resident Veterinarian: <college> animal hospital").
# The role must BE the title's qualifier, the first words of the parenthetical or
# comma clause, and end that clause (or go on "to ..."): a row naming its
# supervisor ("..., <institution>, Faculty Advisor: Dr <N>") is training and pins.
_NON_TRAINING_ROLE_AFTER_TITLE = re.compile(
    r"^\s*[(,]\s*(?:[\w'\u2019-]+\s+){0,3}?"
    r"faculty\s+advis[eo]r(?=\s*(?:[),]|to\b))",
    re.I,
)
_RESIDENT_PROFESSION_AFTER_TITLE = re.compile(r"^\s+(?:veterinarian|artist|scholar)s?\b", re.I)
_TRAINING_SETTING = re.compile(r"\b(?:college|universit(?:y|ies)|hospital|school|residency)\b", re.I)
_POSITION_CODES = frozenset({"D1", "D2", "D3"})

# --- #1731: a chief resident or chief fellow is a trainee, not a leader ---
#
# "1979\u201180 Chief Resident B Internal Medicine, <hospital>" (NJHLUR 25, under
# ACADEMIC APPOINTMENTS) came back D1 or D3 in all four rolls of the #1731 A/B,
# with the prompt saying a chief trainee is C; its non-breaking hyphen also kept
# `_TRAINEE_TITLE`'s date lead from reading the date. Because "Chief" opens it,
# the D2 leadership split can also send such a line to L3 or O. The title must
# OPEN the line after an optional date or date range: "Attending Staff Physician
# and Chief Resident" (RVOPTG 60) is a staff appointment and keeps its D2, and a
# "Chief Residency Selection Committee" line keeps its P (P is not overridable).
_CHIEF_TRAINEE_CODE = "C"
_CHIEF_TRAINEE_OVERRIDABLE = frozenset({"D1", "D2", "D3", "L3", "O"})
_ANY_DASH = r"[-\u2010-\u2015]"
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s+)?(?:\d{{1,2}}/)?(?:(?:19|20)\d{{2}}|['\u2019]?\d{{2}})"
_DATE_LEAD = rf"(?:{_DATE}(?:\s*(?:{_ANY_DASH}|to\b)\s*(?:{_DATE}|present\b|current\b|date\b)?)?[\s,:;.]*)?"
_CHIEF_TRAINEE = re.compile(rf"^[\s\u2022*]*{_DATE_LEAD}chief\s+(?:residen(?:t|cy)|fellow)\b", re.I)
_CHIEF_TRAINEE_WHY = "A chief resident or chief fellow is postdoctoral training"
_LIFE_SUPPORT = re.compile(
    r"\b(?:BCLS|BLS|ACLS|PALS|NRP|ATLS|basic\s+life\s+support"
    r"|advanced\s+(?:cardiac|cardiovascular|trauma|pediatric)\s+life\s+support)\b",
    re.I,
)
# A teaching certificate or licence, not any row that says "teaching" ("teaching hospital").
_TEACHING_CERTIFICATE = re.compile(
    r"\bteach(?:er|ers|ing)?\s+(?:certif|licen[cs]|credential)"
    r"|\b(?:certif\w*|licen[cs]\w*|credential\w*)\s+(?:of\s+|as\s+(?:an?\s+)?|in\s+)?teach(?:er|ers|ing)?\b",
    re.I,
)
# A note that points at a numbered publication listed elsewhere ("see publication
# #88", "(publication #90)") is not a second copy of that citation.
_PUBLICATION_POINTER = re.compile(r"\bpublications?\s*#\s*\d+", re.I)
_PUBLICATION_CODE = re.compile(r"^S\d$")

# --- #1695: a per-article metrics line is a bibliometric record, not a citation ---
#
# Some CVs repeat the bibliography as a metrics table: "<n>. <Surname> et al.
# (<year>) <journal> JIF: <x> cited <n> times", one line per article, under the
# same sub-headings as the bibliography (LFFKVV: 120 lines coded S1, S2 and Q4C
# at 0.95). Each line renders as a title-less duplicate of an article already
# listed in full. A line is a metrics stub when it carries a per-article metric
# and everything before the metric is an author-year lead plus a short venue
# with no digits: no title, no volume, no pages. A full citation that also
# prints its impact factor has a title or a volume before the metric and keeps
# the model's code.
_BIBLIOMETRIC_CODE = "S0"
# Any editorial code (Q4C in LFFKVV; the same lines came back Q4D on a re-run, XNAVXT).
_EDITORIAL_CODE = re.compile(r"^Q4[A-Z]?$")
_PER_ARTICLE_METRIC = re.compile(
    r"\bJIF\b|\bimpact\s+factor\b|\bIF\s*[:=]\s*\d"
    r"|\bcited\s+\d[\d,]*\s+times\b|\btimes\s+cited\b|\bcitations?\s*[:=]\s*\d",
    re.I,
)
_STUB_SURNAME = r"[A-Z][\w'\u2019]+(?:[\s-]+[A-Z][\w'\u2019]+){0,2}"
# Optional list number, one or two surnames, optional "et al.", the year, then the venue.
# The venue holds no digit: a volume or page range after a short title marks a full citation.
_METRICS_STUB_LEAD = re.compile(
    r"^[\s\u2022*\-]*(?:\d{1,4}[.)]\s*)?"
    + _STUB_SURNAME + r"(?:\s+(?:and|&)\s+" + _STUB_SURNAME + r")?(?:,?\s+et\s+al\.?)?"
    r",?\s*\(?(?:19|20)\d{2}(?P<venue>[^\d]*)$"
)
# A journal name; a longer span before the metric reads as a title.
_STUB_MAX_VENUE_WORDS = 8
_YEAR_RANGE_TAIL = re.compile(r"\s*[-\u2013\u2014]")
_METRICS_STUB_WHY = "A per-article citation-metrics line is a bibliometric record, not a citation"

# --- #1695 part 2: a student's evaluation of the owner's teaching is not a course taught ---
#
# An appendix of course evaluations holds verbatim student comments, rated survey
# items ("The course was effectively organized 9") and notes on how the survey was
# run. Each mentions a course, so the model answers K1 (LFFKVV, "Appendix III
# Student Evaluations": 36 K1 and 1 K4 at 0.55-0.75) and they render as Didactic
# Teaching rows. A K answer under a heading level that is itself an evaluations
# heading ("Student Evaluations", "Students' Evaluations of Teaching", optionally
# after an "Appendix III" label) is T.
#
# A combined heading ("Courses Taught and Student Evaluations", "Teaching Awards
# and Evaluations") also lists real courses, so there only a row that opens as an
# evaluation summary ("Teaching evaluations from 1991-1998 ...", JJYBKP) moves. A
# heading naming an evaluations committee or policy is service either way.
_APPENDIX_CODE = "T"
_TEACHING_CODE = re.compile(r"^K\d$")
_EVALUATION_SUBJECT = r"(?:students?|course|teaching|learners?|instructors?)['’]?"
_EVALUATIONS_HEADING = re.compile(
    r"\s*(?:appendix\s+(?:[ivxlc]+|\d+[a-z]?|[a-z])\s*[.:\-\u2013\u2014]?\s*)?"
    rf"(?:{_EVALUATION_SUBJECT}\s+(?:(?:course|teaching)\s+)?(?:self[-\s])?evaluations"
    r"(?:\s+of\s+(?:my\s+)?(?:teaching|instruction))?"
    r"|evaluations\s+(?:of|by)\s+(?:my\s+)?(?:teaching|students|learners))\s*:?\s*",
    re.I,
)
_EVALUATIONS_WORD = re.compile(
    r"\bevaluations\b"
    r"(?!\s+(?:committee|subcommittee|task\s+force|working\s+group|policy))",
    re.I,
)
_EVALUATION_SUMMARY_LEAD = re.compile(rf"\W*{_EVALUATION_SUBJECT}\s+evaluations\b", re.I)
_TEACHING_EVALUATION_WHY = "A student's rating or comment on the owner's teaching is an evaluation, not a course taught"

# --- #1251: a mentee's accomplishments are the mentee's, not the owner's ---
#
# Under a heading that names students, mentees, trainees, advisees or fellows
# mentored, a named-mentee line (N3A/N3B) is followed by that mentee's awards and
# programmes, and the award lines themselves name nobody (JFBPNC, STUDENT
# ACCOMPLISHMENTS: 7 H, 1 K4, 1 K5). Without the preceding mentee line the same
# heading is the owner's own student-era record ("Student/Trainee Awards", web210:
# 8 H that are the owner's), so the pin needs both.
_MENTEE_OUTPUT_CODE = "N4"
_MENTEE_RECORD_CODES = frozenset({"N3A", "N3B"})
_MENTEE_HEADING = re.compile(
    r"\b(?:students?|mentees?|trainees?|advisees?|fellows\s+mentored)\b", re.I
)
# The same people in the plural: a heading listing them, so its rows are theirs
# without a mentee line above (2082_Dr_Scot "Undergraduate Honor Students'
# Achievements", 2068_Yount_Cv "MENTORING MASTERS STUDENT TRAINEES"). The singular
# is the owner's own student-era record ("Student/Trainee Awards", web210).
_MENTEE_PLURAL_HEADING = re.compile(
    r"\b(?:students|mentees|trainees|advisees|fellows\s+mentored)\b", re.I
)
# Only the level holding the row, or its parent, can say the row is a mentee's:
# a plural further up is a section title over the owner's own records.
_MENTEE_PLURAL_LEVELS = 2
# A level about the owner's teaching names students as the people taught, not as
# the owners of its rows ("Teaching of Students in Courses", a Harvard-format CV).
_TEACHING_LEVEL = re.compile(r"\b(?:teach\w*|courses?|class(?:es)?|lectures?|curricul\w*)\b", re.I)
# K and D rows are recoded only when the heading also says these are accomplishments.
_ACCOMPLISHMENT_HEADING = re.compile(r"\b(?:accomplishments?|achievements?|awards?)\b", re.I)
_HONOR_CODE = "H"
_MENTEE_ACTIVITY_FAMILIES = ("K", "D")
# An education row under a level listing students in the plural is the student's
# degree or school, not the owner's (farm 2082 621, B1 "<Student>. 2020.
# Graduated cum Laude ... Attending John Marshall Law School" rendered in the
# owner's EDUCATION table, #1573). Only the plural level pins it: a named-mentee
# line above is not enough evidence on its own.
_EDUCATION_FAMILY = "B"
# A row that opens with another person's name and degree, under a research or
# residency heading, is that person's work (KDAZOM, "Medical Residency Research":
# "<Name>, M.D., Dermatology: <title>, 2nd place"). An honors heading keeps it:
# there the same shape is an eponymous award ("<Name>, MD Teaching Award").
_RESEARCH_HEADING = re.compile(r"\b(?:research|residency|residents?|mentor(?:ing|ed|s)?)\b", re.I)
_HONORS_HEADING = re.compile(r"\b(?:honou?rs?|awards?|prizes?)\b", re.I)
_DEGREE = r"(?:M\.?\s?D|Ph\.?\s?D|D\.?\s?O|Pharm\.?\s?D|M\.?\s?P\.?\s?H|M\.?\s?S|B\.?\s?S|R\.?\s?N)\.?"
_NAMED_PERSON_LEAD = re.compile(
    r"^[\s\u2022*\-\d.)]*[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z'\u2019-]+){1,2},\s*" + _DEGREE + r"(?=[\s,:;]|$)"
)
_MENTEE_PIN_WHY = "Listed as the work of a student or trainee the CV owner mentored"

# --- #1710: a grant listed under the owner's mentor or sponsor role is the trainee's ---
#
# "Grant Support as Mentor" (UAURFU, 29 rows), "As sponsor or primary mentor"
# (LWMSPJ), "Grants as Mentor" (CTWLTR) and "Mentor or Co-mentor" (2087) list the
# F32s, K08s and R03s the owner's trainees won. Coded M2A/M2B, each renders as a
# Research Support table of the owner's own funding with the trainee as PI; the
# template's slot for them is N2. A heading level must name the role: "as
# <role>" anywhere in it, or the role words alone. "Advisor and Mentor" (2087,
# whose group also holds the owner's Co-I grants), "MENTORED GRANTS" (BYFQBG, the
# owner's own grants above it), "Sponsored Research", "Co-Investigator or
# Sponsor on Grants" and "as Mentored Investigator" do not match.
_MENTOR_GRANT_CODE = "N2"
_MENTOR_ROLE = r"(?:primary\s+|co[-\s]?)?(?:mentor|sponsor)"
_MENTOR_GRANT_HEADING = re.compile(
    rf"\bas\s+{_MENTOR_ROLE}\b|^\s*{_MENTOR_ROLE}(?:\s+(?:or|and)\s+{_MENTOR_ROLE})*[\s:]*$", re.I
)
_MENTOR_GRANT_WHY = "A grant listed under the owner's mentor or sponsor role is a mentored trainee grant"

# --- #1573: rows a heading says are someone else's are not the owner's records ---
#
# Batch YUYVIG. Papers listed under an acknowledgements lead line are other
# groups' papers that thank the owner (ECXGAT 2156-2204: S1, S2, S4, S5), and
# rendered as the owner's bibliography. The applicants of an internal
# grant-review panel (ECXGAT 1293-1299, N3B) and a laboratory's technical
# staff (IZIXVF 284-305, N3B and N3) rendered as the owner's past mentees.
# Both go to the Appendix (T), which prints the line as the source wrote it and
# claims nothing for the owner; a T set here is final for T-validation.
# The heading is the row's own hierarchy level or, on a run whose headings
# stage 1b lost (ECXGAT placed none), the nearest lead line above the row in
# its group (`is_heading_line`), read off the rows as the model coded them
# (`next_lead`). A lead line's list holds the rows it makes someone else's,
# and, under a reviewing line, service and administrative rows
# (`_REVIEW_LIST_CODE`: ECXGAT 1289-1291, P). Any other row ends it: a T
# section heading that is dated, numbered or long ("5. Mentoring"), a row of
# another code (web26: a "Reviewer:" line, then a training row and two
# committees, then a postdoc list), a mentee row the line does not move. A row
# the pin moves is never a new lead line, so a short pinned row does not split
# its list (IZIXVF-01's complaint). A lead line is a heading, not a record: a
# line with a comma or semicolon ("Ad hoc grant reviewer, <Foundation>") is
# one, unless it is coded T or ends in a colon.
# Only a level or line that is "Acknowledg(e)ments" alone, or with a
# parenthesised qualifier ("(partial list)"), moves a publication: a
# publications heading that only mentions them ("... (acknowledging grant
# support)", "Acknowledgements and Publications") keeps its rows. A level or
# lead line that names trainees, a thesis, mentoring or training keeps
# a mentee row, whatever else it names (a fellows-and-staff level, a level of
# staff the owner trained, a thesis-review line), and so does a staff level
# under a mentoring or supervision parent ("MENTORING > Staff").
# Reviewing is read off the lead line only: stage 1b's hierarchy drifts a
# student-thesis list under its sibling journal-reviewer and study-section-panel
# levels (wave-1 2030_* 710-738, 2025_* 360),
# while a staff level is the mentoring section's own sub-heading (IZIXVF).
_SOMEONE_ELSES_CODE = "T"
_ACKNOWLEDGEMENTS_HEADING = re.compile(
    r"^\W*(?:[\dA-Za-z]{1,3}[.)]\s*)?acknowledge?ments?\b\W*(?:\([^)]*\)\W*)?$", re.I)
_ACKNOWLEDGED_WHY = "A paper listed under the CV's acknowledgements is another group's work"
_MENTEE_CODES = frozenset({"N3", "N3A", "N3B"})
_REVIEWED_HEADING = re.compile(r"\breview(?:s|er|ers|ing)?\b|\bapplicants?\b|\bpanels?\b", re.I)
_STAFF_HEADING = re.compile(r"\bstaff\b|\btechnicians?\b|\blab(?:oratory)?\s+managers?\b", re.I)
_TRAINEE_WORD = re.compile(
    r"student|fellow|train|mentee|mentor|post-?doc|resident|scholar|advisee|thes[ie]s"
    r"|dissertation|supervis", re.I)
# A parent level that makes a staff level the owner's mentoring (IZIXVF's parent
# level lists its students, postdocs and staff, and names neither).
_MENTORING_PARENT = re.compile(r"mentor|mentee|supervis", re.I)
_NOT_A_MENTEE_WHY = "Listed under a heading of people the owner reviewed or employed, not trained"
# A lead line heads the rows after it: undated, short, not a list item, not a
# mentee, and not punctuated as a record unless it is a note or ends in a colon.
_HEADING_LINE_MAX_WORDS = 12
_FOUR_DIGIT_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_LIST_ITEM_LEAD = re.compile(r"^\W*(?:\(?\d{1,3}[.)]|\(?[A-Za-z][.)]\s|[-\u2013\u2022\u00b7\u25e6*])")
_RECORD_PUNCTUATION = re.compile(r"[,;]")
_LEAD_LINE_END = ":"
# The rows a reviewing lead line heads besides the mentee rows it moves.
_REVIEW_LIST_CODE = re.compile(r"^(?:P|Q\w*)$")

# --- #1573: a trainee's award stated inside an abstract is the abstract's ---
#
# ULHCAP 1415, under PUBLICATIONS > Abstracts: an abstract by a trainee alone
# that ends with the prize it won at a meeting came back H at 0.75 and
# rendered in the owner's Honors, and its title nowhere. A row that
# opens with an author list ("Doe, JD.", "Doe J, Roe K.": a surname, then
# initials that a comma, semicolon or period ends) under a leaf level naming
# abstracts is an abstract (S8). An award line there with no author list ("Best
# Abstract Award, <meeting>"), or one that opens with an award word and a
# society's acronym ("Winner, <ACRONYM> Travel Award"), keeps its H. A mentee's
# abstract under a students level is the mentee pin's (#1251), checked first.
_ABSTRACTS_HEADING = re.compile(r"\babstracts\b", re.I)
_AUTHOR_LEAD = re.compile(
    r"^[\s\u2022*\-\d.)]*"
    r"(?!(?i:winners?|recipients?|finalists?|semi-?finalists?|awardees?|nominees?|honou?rees?|laureates?)\b)"
    r"[A-Z][A-Za-z'\u2019-]+,?\s+(?:[A-Z]\.?\s?){1,3}(?=[,;.])")
_ABSTRACT_WITH_AWARD_WHY = "An abstract listed under the CV's abstracts, whatever award it names"

# --- #1413: a journal listed under a reviewer lead-in is a review, not a board seat ---
#
# "Reviewer for the following journals and conferences:" heads a flat list of
# journal names. Stage 3b classifies a group in batches of CLASSIFY_BATCH_SIZE,
# so a list longer than one batch reaches the model in two calls, and the second
# call holds journal names only, never the lead-in. With nothing saying the owner
# reviews for them, the model answered Q4C (editorial board) at 0.5 for those
# names and they rendered as Editorial Board rows (JJUQDF 342-346, 3 of 4 runs;
# RVTAQT the same). The pin restores the lead-in's reading for that later batch
# only: an unsure Q4C that names no role, in a later batch than the reviewer
# lead-in, while no row since the lead-in was confidently coded outside the
# editorial (Q4) family. An unsure non-editorial row does not end the list: the
# same batch on GINAAP coded "Society for Medical Decision Making" I at 0.6.
_JOURNAL_REVIEWER_CODE = "Q4D"
_EDITORIAL_BOARD_CODE = "Q4C"
_REVIEWER_PIN_MAX_MODEL_CONFIDENCE = 0.80
# A lead-in is a short line ending in a colon; a longer one is a record with a colon in it.
_LEAD_IN_END = ":"
_LEAD_IN_MAX_WORDS = 12
# "Reviewer"/"Ad hoc" name the owner's role; a bare "review" does not ("In review:",
# "Submitted, under review:", "<Meeting> Review, <University>:" head other lists).
_REVIEWER_WORD = re.compile(r"\breviewers?\b|\bad[\s-]hoc\b", re.I)
# A role word in the row or the lead-in keeps the model's answer ("Editorial board
# member and reviewer for:", "Associate Editor, <journal>"); so does a heading that
# names a board. "EDITORIAL ACTIVITIES" names no board: the reviewer list sits under it.
_JOURNAL_ROLE_WORD = re.compile(r"board|editor|member|panel|consult|advis|chair|committee", re.I)
_BOARD_HEADING = re.compile(r"board", re.I)
# A row that names a section, not a journal: its last word is a plural section noun
# ("Journal Service Activities"; "Memberships" already carries the role word "member").
# Only the last word: "Health Services Research" is a journal. A firm Q4 answer on
# such a row, or on one with a role word, opens a new list, so the reviewer list ends.
_HEADING_ROW = re.compile(
    r"\b(?:activities|positions|roles|appointments|services|responsibilities)\W*$", re.I)
_REVIEWER_PIN_WHY = "Listed under a line naming the owner as a reviewer for these journals"

# --- #1580: a Q2 or P row that is no seat on a body ---
#
# The prompt's rule 15 sends advisory consulting to Q2 as external service, so
# consultancies, a company's advisory board and expert work in litigation
# rendered as Service on Boards rows (UVZNIC 97-103, XNWSZN 1252/1256, AOJEQD
# 46/49, CKDDEG 646, HBALTD 38) or, coded P, as an institutional committee
# (WBZIDP 46/47). They are Other Professional Positions (D3). A row is one when:
# - its line names the owner consulting, and no body one sits on, no seat or
#   office, it is no citation, and its heading is not a list of talks (Q2 only: a
#   P "Consultant" row is the owner's own institution's project, EYTHNB 279;
#   "<Company> Consultant Focus Group" under Invited Presentations is a talk);
# - its line names an advisory or consulting board and a company, and no
#   public body, society or school ("<Pharma>, Scientific Advisory Board");
# - its line names litigation or a law firm, and expert or consulting work;
# - a heading above it names consulting, and its line names a company and no
#   public body (EJPAIA 175 under "Scientific Advisory Boards and
#   Consultantships"). A company named without a company word ("Scientific
#   Advisory Board, <Startup>", UVZNIC 105) cannot be told from an academic
#   program's board ("<University Program>, Advisory Board", EJPAIA 181), so it
#   stays Q2.
# Testimony stays Q2 whatever else the line says (the owner's decision on #1580,
# 2026-10-27), and so does "Consultant, <Society> Ethics Committee": a seat.
_NON_SEAT_SOURCE_CODES = frozenset({"Q2", "P"})
_BOARD_SERVICE_CODE = "Q2"
_CONSULTING_CODE = "D3"
# The owner's own consulting: "<Center> Consultants' Conference" and "<Region>
# Consultants Collaborative" name a meeting or a body.
_CONSULTING_WORD = re.compile(r"\bconsult(?:ant|ancy|ancies|antships?|ing|ed)\b(?!['\u2019])", re.I)
_SEAT_BODY = re.compile(
    r"\b(?:(?:sub)?committees?|boards?(?![\s-]+certified)|councils?|commissions?|panels?"
    r"|task\s*forces?|(?:working|advisory|technical|expert|steering)\s*groups?|workgroups?"
    r"|study\s+sections?|coalitions?|alliances?)\b", re.I)
_SEAT_OR_OFFICE = re.compile(r"\b(?:members?|(?:co-?)?chair\w*|president|secretary|treasurer)\b", re.I)
_TALKS_HEADING = re.compile(r"\b(?:presentations?|lectures?|talks?|speeches)\b", re.I)
# A citation: an author list and a year in parentheses ("Doe, J. (2031) ...").
_CITATION_YEAR = re.compile(r"\((?:19|20)\d{2}[a-z]?\)")
_COMPANY = re.compile(
    r"\b(?:inc|llc|llp|ltd|corp|corporation|company|companies|pharma|pharmaceuticals?|therapeutics"
    r"|biotech(?:nology)?|biosciences?|biopharma(?:ceuticals?)?|gmbh|plc)\b", re.I)
_ADVISORY_BOARD = re.compile(r"\b(?:advisory|consulting)\s+(?:board|panel|council|committee)s?\b", re.I)
_PUBLIC_BODY = re.compile(
    r"\b(?:national|federal|state|united\s+states|government|ministry|agency|nih|institutes?"
    r"|society|association|foundation|academy|college|university|school|department|council|commission"
    r"|hospital|cent(?:er|re)|trust|fund|program(?:me)?|network|alliance|coalition|consortium"
    r"|blood\s+banks?)\b", re.I)
_LITIGATION = re.compile(r"\blitigations?\b|\b(?:law|legal)\s+firms?\b", re.I)
_EXPERT_OR_CONSULTING = re.compile(r"\bexpert\b|\bconsult", re.I)
_TESTIMONY = re.compile(r"\btestif(?:y|ied|ies|ying|ier)\b|\btestimon(?:y|ies)\b", re.I)
_CONSULTING_WHY = "Consulting, a company's advisory board or expert work in litigation is a professional position, not a seat on a body"
#
# A meeting the owner attended (#1691's rule, B2): under a heading that names
# attendance ("Scientific Meetings Attended": LTTYWI 1084-1101, coded Q2 by
# T-validation), or on a line that opens as attendance ("Attendee <Day>",
# MQLKPQ 109; "Participant, <Meeting>", LWMSPJ 342), when the line names an
# event, or the attendance heading does (LTTYWI 1095 misspells "Congress"),
# and no part the owner played in it: a chair, organizer, moderator, speaker,
# panelist, a seat, a service provided ("Participant, <Day> - provided
# <a service>", VGHNZD 570), or an author list of two names or more ("<City>
# DC," is a place, LTTYWI 1099).
_ATTENDED_CODE = "B2"
_ATTENDANCE_HEADING = re.compile(r"\b(?:attended|attendance)\b", re.I)
_ATTENDANCE_LEAD = re.compile(
    r"^\W*(?:(?:19|20)\d{2}(?:\s*[-\u2013\u2014]\s*(?:(?:19|20)?\d{2}|present))?\W*)?"
    r"(?:attended|attendee|participant|participated\s+in)\b", re.I)
_EVENT = re.compile(
    r"\b(?:meetings?|conferences?|congress(?:es)?|symposi(?:um|a|ums)|workshops?|courses?|summits?"
    r"|forums?|seminars?|webinars?|conventions?|day)\b", re.I)
_OWNERS_PART = re.compile(
    r"\b(?:(?:co-?)?chair\w*|organi[sz]\w*|moderat\w*|speaker|keynote|present(?:ed|er|ing|ation)"
    r"|lectur\w*|faculty|instructor|facilitat\w*|panelist|discussant|judge|host\w*|delegate"
    r"|representative|members?|committees?|boards?|director|provid(?:ed|ing)|volunteer\w*|served|taught)\b",
    re.I)
_ATTENDED_WHY = "A meeting the owner attended is an educational experience, not service"
_MIN_AUTHOR_NAMES = 2
_ET_AL = re.compile(r"\bet\s+al\b", re.I)


def _is_consulting(code: str, text: str, levels: list[str]) -> bool:
    """A consultancy, a company's advisory board, or expert work in litigation (#1580)."""
    if _TESTIMONY.search(text):
        return False
    company = bool(_COMPANY.search(text)) and not _PUBLIC_BODY.search(text)
    if (_CONSULTING_WORD.search(text) and not _SEAT_BODY.search(text) and not _SEAT_OR_OFFICE.search(text)
            and not (_has_author_list(text) and _CITATION_YEAR.search(text))
            and not (levels and _TALKS_HEADING.search(levels[-1]))):
        return code == _BOARD_SERVICE_CODE or company
    if _ADVISORY_BOARD.search(text) and company:
        return True
    if _LITIGATION.search(text) and _EXPERT_OR_CONSULTING.search(text):
        return True
    return company and any(_CONSULTING_WORD.search(level) for level in levels)


def _is_attended_meeting(text: str, levels: list[str]) -> bool:
    """A meeting or event the owner attended, playing no part in it (#1580, #1691)."""
    headings = [level for level in levels if _ATTENDANCE_HEADING.search(level)]
    attended = _ATTENDANCE_LEAD.match(text) or headings
    event = _EVENT.search(text) or any(_EVENT.search(level) for level in headings)
    return (bool(attended) and bool(event)
            and not _OWNERS_PART.search(text) and not _names_authors(text))


def _names_authors(text: str) -> bool:
    """An author list of two names or more, or "et al" (one name-and-initials
    is as often a place: "<City> DC,")."""
    names = _AUTHOR_LIST.findall(_PLACE_INITIALS.sub("", text))
    return len(names) >= _MIN_AUTHOR_NAMES or bool(_ET_AL.search(text))


def non_seat_pin_code(entry: dict) -> tuple[str, str] | None:
    """(code, why) for a Q2 or P row that is no seat on a body, else None (#1580):
    consulting, a company's advisory board or expert work in litigation is D3
    (`_is_consulting`), a meeting attended is B2 (`_is_attended_meeting`)."""
    code, text = entry.get("taxonomy_code") or "", entry.get("text") or ""
    if code not in _NON_SEAT_SOURCE_CODES:
        return None
    levels = [str(level) for level in entry.get("hierarchy") or []]
    if _is_consulting(code, text, levels):
        return _CONSULTING_CODE, _CONSULTING_WHY
    if _is_attended_meeting(text, levels):
        return _ATTENDED_CODE, _ATTENDED_WHY
    return None


# A line that only points elsewhere ("<label> - see section N") or is only a
# URL is a note, not a record: a T it was given must survive T-validation.
_POINTER = re.compile(r"\(?\bsee\s+(?:sections?|items?|pages?|above|below|publications?)\b[^)]*\)?", re.I)
_POINTER_LINE_MAX_WORDS = 4
_BARE_URL = re.compile(r"^(?:https?://|www\.)\S+$", re.I)


def pinned_header_code(context: TaxonomyContext) -> str | None:
    """The code the CV author's own section placement pins, or None.

    Every level of the entry's path that stage 3a mapped must agree on one top
    code, the most specific level must reach HEADER_PIN_MIN_CONFIDENCE, and at
    least one agreeing level must be a real section title, not a bare scope word.
    """
    levels = [
        level for level in (context.subsection, context.section, context.meta_section)
        if level and level.get("taxonomy_options")
    ]
    if not levels:
        return None
    tops = [max(level["taxonomy_options"], key=lambda o: o["confidence"]) for level in levels]
    code = tops[0]["code"]
    if tops[0]["confidence"] < HEADER_PIN_MIN_CONFIDENCE:
        return None
    if any(top["code"] != code for top in tops):
        return None
    if all(str(level.get("title", "")).strip().lower() in GEOGRAPHIC_SCOPE_TITLES for level in levels):
        return None
    return code


def leaf_header_code(context: TaxonomyContext) -> str | None:
    """The top code of the most specific mapped level, when it reaches the floor, else None.

    Unlike `pinned_header_code` the parent levels need not agree: a grant-reviewer
    sub-heading under a general service heading mapped Q2/Q4 still says Q3.
    Used only for the grant-review (Q3), mentoring-programs (N1) and
    clinical-leadership (L3) pins, whose override sets are one or two codes wide.
    """
    for level in (context.subsection, context.section, context.meta_section):
        if level and level.get("taxonomy_options"):
            top = max(level["taxonomy_options"], key=lambda o: o["confidence"])
            return top["code"] if top["confidence"] >= HEADER_PIN_MIN_CONFIDENCE else None
    return None


def _has_author_list(text: str) -> bool:
    return bool(_AUTHOR_LIST.search(_PLACE_INITIALS.sub("", text)))


def _safe_confidence(entry: dict) -> float:
    """The model's confidence as a float; anything unreadable counts as certain (no pin)."""
    try:
        return float(entry.get("taxonomy_confidence"))
    except (TypeError, ValueError):
        return 1.0


def _r_pin_takes(entry: dict) -> bool:
    """Whether an R pin recodes this model answer: the measured set, or an unsure, author-less S8."""
    code, text = entry.get("taxonomy_code"), entry.get("text") or ""
    if code in HEADER_PIN_OVERRIDES["R"]:
        return True
    return (
        code == "S8"
        and _safe_confidence(entry) <= _S8_PIN_MAX_MODEL_CONFIDENCE
        and not _has_author_list(text)
        and not _CONTRIBUTED_WORK.search(text)
    )


def _heading_text(entry: dict) -> str:
    return " ".join(str(h) for h in entry.get("hierarchy") or [])


def _is_grant_review(entry: dict) -> bool:
    text = entry.get("text") or ""
    if _NOT_A_GRANT_REVIEW.search(text):
        return False
    heading = _heading_text(entry)
    return bool(_GRANT_HEADING.search(heading) or _GRANT_REVIEW_TEXT.search(text))


def _h_pin_takes(entry: dict) -> bool:
    """Whether an H pin recodes this model answer: an unsure grant code on a row with no grant marker."""
    text = entry.get("text") or ""
    return (
        entry.get("taxonomy_code") in _GRANT_CODES
        and _safe_confidence(entry) <= _H_PIN_MAX_MODEL_CONFIDENCE
        and not _GRANT_MARKER.search(text)
        and not _PI_ROLE_LEAD.match(text)
    )


def _page_span(text: str) -> int:
    """The longest page span in the row's citation, 0 when it gives none."""
    spans = [0]
    for first, last in _PAGE_RANGE.findall(text):
        if len(last) < len(first):
            last = first[:len(first) - len(last)] + last
        spans.append(int(last) - int(first) + 1)
    return max(spans)


def _reads_as_journal_article(text: str) -> bool:
    """A cited row with a PMID or DOI, at least three pages or no meeting marker, and no proceedings venue."""
    if not _PUBLICATION_ID.search(text) or _PROCEEDINGS_VENUE.search(text):
        return False
    span = _page_span(text)
    if span:
        return span >= _MIN_ARTICLE_PAGES
    return not _MEETING_MARKER.search(text)


def _s1_pin_takes(entry: dict) -> bool:
    """Whether an S1 pin recodes this model answer: an unsure S8 that reads as a journal article."""
    return (
        entry.get("taxonomy_code") == _ABSTRACT_CODE
        and _safe_confidence(entry) <= _S1_PIN_MAX_MODEL_CONFIDENCE
        and _reads_as_journal_article(entry.get("text") or "")
    )


def header_pin_code(entry: dict, pin: str | None, leaf: str | None) -> str | None:
    """The code the group's header pins this model answer to, or None to keep it."""
    code = entry.get("taxonomy_code")
    if pin == "R" and _r_pin_takes(entry):
        return "R"
    if pin and pin != "R" and code in HEADER_PIN_OVERRIDES.get(pin, ()):
        return pin
    if pin == _HONOR_CODE and _h_pin_takes(entry):
        return _HONOR_CODE
    if pin == _S1_PIN and _s1_pin_takes(entry):
        return _S1_PIN
    if leaf == _GRANT_REVIEW_PIN and code in _GRANT_REVIEW_OVERRIDABLE and _is_grant_review(entry):
        return _GRANT_REVIEW_PIN
    if leaf == _MENTORING_PROGRAM_PIN and code in _MENTORING_PROGRAM_OVERRIDABLE:
        return _MENTORING_PROGRAM_PIN
    if leaf == _CLINICAL_LEADERSHIP_PIN and code in _CLINICAL_LEADERSHIP_OVERRIDABLE:
        return _CLINICAL_LEADERSHIP_PIN
    return None


def _is_trainee_title(text: str) -> bool:
    """An intern, resident or fellow title at the head of the row, not an affiliate, honorific or other role."""
    trainee = _TRAINEE_TITLE.match(text)
    if not trainee or _SENIOR_FELLOW.search(trainee.group(0)) or _HONORIFIC_FELLOW.match(text):
        return False
    return not _is_non_training_role(text[trainee.end():], text)


def _is_non_training_role(after_title: str, text: str) -> bool:
    """A title qualified as a faculty-advisor role, or a resident professional outside a training setting."""
    if _NON_TRAINING_ROLE_AFTER_TITLE.match(after_title):
        return True
    return bool(_RESIDENT_PROFESSION_AFTER_TITLE.match(after_title)) and not _TRAINING_SETTING.search(text)


def content_pin_code(entry: dict) -> tuple[str, str] | None:
    """(code, why) an entry's own shape pins it to whatever its heading, or None.

    Attended courses are B2, never K4, unless the row names a teaching role;
    a training title at the head of a position row is C (postdoctoral training);
    a chief resident or chief fellow at the head of a position or leadership row is C;
    a life-support or teaching certificate is B2, not a medical licence (F1);
    a publication code on a note that points at a numbered publication is T;
    a publication or editorial code on a per-article metrics line is S0;
    a teaching code under a student, course or teaching evaluations heading is T
    (under a combined heading, only a row that opens as an evaluation summary).
    """
    code, text = entry.get("taxonomy_code"), entry.get("text") or ""
    heading = _heading_text(entry)
    if code == "K4" and (_ATTENDED.search(heading) or _ATTENDED.search(text)) and not _TEACHING_ROLE.search(text):
        return "B2", "A course the owner attended is education received"
    if code in _POSITION_CODES and _is_trainee_title(text):
        return "C", "An intern, resident or fellow title is postdoctoral training"
    if code in _CHIEF_TRAINEE_OVERRIDABLE and _CHIEF_TRAINEE.match(text):
        return _CHIEF_TRAINEE_CODE, _CHIEF_TRAINEE_WHY
    if code == "F1" and (_LIFE_SUPPORT.search(text) or _TEACHING_CERTIFICATE.search(text)):
        return "B2", "A life-support or teaching certificate is not a medical licence"
    if code and _PUBLICATION_CODE.match(code) and _PUBLICATION_POINTER.search(text):
        return "T", "A note pointing at a numbered publication is not a second citation"
    if code and (_PUBLICATION_CODE.match(code) or _EDITORIAL_CODE.match(code)) and _is_metrics_stub(text):
        return _BIBLIOMETRIC_CODE, _METRICS_STUB_WHY
    if code and _TEACHING_CODE.match(code) and _is_under_teaching_evaluations(entry):
        return _APPENDIX_CODE, _TEACHING_EVALUATION_WHY
    return None


def _is_under_teaching_evaluations(entry: dict) -> bool:
    """An evaluations heading level, or a combined one over an evaluation-summary row (#1695 part 2)."""
    levels = [str(level) for level in entry.get("hierarchy") or []]
    if any(_EVALUATIONS_HEADING.fullmatch(level) for level in levels):
        return True
    return (any(_EVALUATIONS_WORD.search(level) for level in levels)
            and bool(_EVALUATION_SUMMARY_LEAD.match(entry.get("text") or "")))


def _is_metrics_stub(text: str) -> bool:
    """A per-article metric after only an author-year lead and a short, digit-free venue (#1695)."""
    metric = _PER_ARTICLE_METRIC.search(text)
    if not metric:
        return False
    lead = _METRICS_STUB_LEAD.match(text[:metric.start()])
    # "<role>, 2015-present, <journal> (IF 3.2)" is an editorial row, not a stub.
    return bool(lead) and not _YEAR_RANGE_TAIL.match(lead.group("venue")) and len(re.findall(r"[A-Za-z]+", lead.group("venue"))) <= _STUB_MAX_VENUE_WORDS


def _is_mentee_accomplishment(code: str, heading: str) -> bool:
    """An H, or a K/D row under an accomplishments heading, beneath a mentee heading."""
    if not _MENTEE_HEADING.search(heading):
        return False
    if code == _HONOR_CODE:
        return True
    return code.startswith(_MENTEE_ACTIVITY_FAMILIES) and bool(_ACCOMPLISHMENT_HEADING.search(heading))


def _is_named_trainee_work(code: str, heading: str, text: str) -> bool:
    """An H row that opens with another person's name and degree under a research heading."""
    return (
        code == _HONOR_CODE
        and bool(_RESEARCH_HEADING.search(heading))
        and not _HONORS_HEADING.search(heading)
        and bool(_NAMED_PERSON_LEAD.match(text))
    )


def _level_lists_mentees(entry: dict) -> bool:
    """The row's own heading level, or its parent, names mentees in the plural and is not about teaching."""
    levels = [str(level) for level in (entry.get("hierarchy") or [])[-_MENTEE_PLURAL_LEVELS:]]
    return any(_MENTEE_PLURAL_HEADING.search(level) and not _TEACHING_LEVEL.search(level) for level in levels)


def mentee_pin_code(entry: dict, after_mentee: bool) -> tuple[str, str] | None:
    """(N4, why) when the row is a mentee's accomplishment, not the owner's, else None.

    `after_mentee` says a named-mentee line (N3A/N3B) came earlier in the same
    hierarchy group; without one, only the row's own heading level or its parent
    naming mentees in the plural pins (#1251). An education (B) row pins only on
    that plural level (#1573).
    """
    code, text = entry.get("taxonomy_code") or "", entry.get("text") or ""
    heading = _heading_text(entry)
    level_lists_mentees = _level_lists_mentees(entry)
    if (after_mentee or level_lists_mentees) and _is_mentee_accomplishment(code, heading):
        return _MENTEE_OUTPUT_CODE, _MENTEE_PIN_WHY
    if level_lists_mentees and code.startswith(_EDUCATION_FAMILY):
        return _MENTEE_OUTPUT_CODE, _MENTEE_PIN_WHY
    if _is_named_trainee_work(code, heading, text):
        return _MENTEE_OUTPUT_CODE, _MENTEE_PIN_WHY
    return None


def mentor_grant_pin_code(entry: dict) -> tuple[str, str] | None:
    """(N2, why) for a grant code under a heading level naming the owner as mentor or sponsor (#1710)."""
    if entry.get("taxonomy_code") not in _GRANT_CODES:
        return None
    if not any(_MENTOR_GRANT_HEADING.search(str(level)) for level in entry.get("hierarchy") or []):
        return None
    return _MENTOR_GRANT_CODE, _MENTOR_GRANT_WHY


def is_heading_line(entry: dict) -> bool:
    """An undated, short, unnumbered line that is not a mentee row and not
    punctuated as a record (unless it is a note or ends in a colon): it heads
    the rows after it (#1573)."""
    text = (entry.get("text") or "").strip()
    return (entry.get("taxonomy_code") not in _MENTEE_CODES
            and not _FOUR_DIGIT_YEAR.search(text)
            and len(text.split()) <= _HEADING_LINE_MAX_WORDS
            and not _LIST_ITEM_LEAD.match(text)
            and (entry.get("taxonomy_code") == _APPENDIX_CODE or text.endswith(_LEAD_LINE_END)
                 or not _RECORD_PUNCTUATION.search(text)))


def next_lead(entry: dict, lead: str) -> str:
    """The lead line still open after `entry`, read off the row as the model
    coded it: `lead` while the row is one of its list's rows (one it makes
    someone else's, or a service row under a reviewing line), else the row's
    own text when it is a heading line, else ""."""
    if lead and someone_elses_pin_code(entry, lead):
        return lead
    if is_heading_line(entry):
        return (entry.get("text") or "").strip()
    reviewing = _REVIEWED_HEADING.search(lead)
    return lead if reviewing and _REVIEW_LIST_CODE.match(entry.get("taxonomy_code") or "") else ""


def _non_mentee_heading(levels: list[str], lead: str) -> bool:
    """The leaf level names staff under no mentoring parent, or the lead line
    names reviewing or staff; a leaf, then a lead line, naming trainees settles
    it first."""
    leaf, parents = (levels[-1], levels[:-1]) if levels else ("", [])
    if _TRAINEE_WORD.search(leaf):
        return False
    if _STAFF_HEADING.search(leaf):
        return not any(_MENTORING_PARENT.search(level) for level in parents)
    if _TRAINEE_WORD.search(lead):
        return False
    return bool(_REVIEWED_HEADING.search(lead) or _STAFF_HEADING.search(lead))


def someone_elses_pin_code(entry: dict, lead: str) -> tuple[str, str] | None:
    """(code, why) for a row its heading says is another person's, else None (#1573).

    `lead` is the lead line still open above the row in its group (`next_lead`), or "".
    A publication under an acknowledgements level or line is T; a mentee row
    under a staff leaf, or a reviewing or staff line, is T; an H that opens with
    an author list under an abstracts leaf is S8.
    """
    code, text = entry.get("taxonomy_code") or "", entry.get("text") or ""
    levels = [str(level) for level in entry.get("hierarchy") or []]
    leaf = levels[-1] if levels else ""
    if _PUBLICATION_CODE.match(code) and any(_ACKNOWLEDGEMENTS_HEADING.match(h) for h in [*levels, lead]):
        return _SOMEONE_ELSES_CODE, _ACKNOWLEDGED_WHY
    if code in _MENTEE_CODES and _non_mentee_heading(levels, lead):
        return _SOMEONE_ELSES_CODE, _NOT_A_MENTEE_WHY
    if code == _HONOR_CODE and _ABSTRACTS_HEADING.search(leaf) and _AUTHOR_LEAD.match(text):
        return _ABSTRACT_CODE, _ABSTRACT_WITH_AWARD_WHY
    return None


def is_lead_in(text: str) -> bool:
    """A short line that ends in a colon: it introduces the rows after it (#1413)."""
    stripped = (text or "").strip()
    return stripped.endswith(_LEAD_IN_END) and len(stripped.split()) <= _LEAD_IN_MAX_WORDS


def is_reviewer_lead_in(text: str) -> bool:
    """A lead-in that names reviewing and no editorial role: "Reviewer for the following journals:"."""
    return is_lead_in(text) and bool(_REVIEWER_WORD.search(text)) and not _JOURNAL_ROLE_WORD.search(text)


def reviewer_pin_code(entry: dict, after_unseen_reviewer_lead_in: bool) -> tuple[str, str] | None:
    """(Q4D, why) for an unsure, role-less Q4C after a reviewer lead-in its batch did not hold, else None.

    `after_unseen_reviewer_lead_in` says a reviewer lead-in opened the current
    run of editorial rows in an EARLIER classification batch than this entry, so
    the model never saw it (#1413). The heading must not name a board.
    """
    if not after_unseen_reviewer_lead_in or entry.get("taxonomy_code") != _EDITORIAL_BOARD_CODE:
        return None
    if _safe_confidence(entry) > _REVIEWER_PIN_MAX_MODEL_CONFIDENCE:
        return None
    leaf = str((entry.get("hierarchy") or [""])[-1])
    if _JOURNAL_ROLE_WORD.search(entry.get("text") or "") or _BOARD_HEADING.search(leaf):
        return None
    return _JOURNAL_REVIEWER_CODE, _REVIEWER_PIN_WHY


def _next_reviewer_lead_in(entry: dict, position: int, current: int | None) -> int | None:
    """The position of the reviewer lead-in still open after `entry`, or None.

    A lead-in line opens a run (a reviewer one) or closes it (any other). A
    model row confidently coded outside the editorial family ends the list, and
    so does a confident editorial row that names a role or reads as a heading
    ("Editorial Board Memberships" at Q4C 0.9). An unsure, empty or fallback
    row changes nothing.
    """
    text = entry.get("text") or ""
    if is_lead_in(text):
        return position if is_reviewer_lead_in(text) else None
    if entry.get("classification_source") != _MODEL_SOURCE:
        return current
    if _safe_confidence(entry) <= _REVIEWER_PIN_MAX_MODEL_CONFIDENCE:
        return current
    editorial = bool(_EDITORIAL_CODE.match(entry.get("taxonomy_code") or ""))
    opens_another_list = bool(_JOURNAL_ROLE_WORD.search(text) or _HEADING_ROW.search(text))
    return None if not editorial or opens_another_list else current


def is_note_not_record(text: str, opening_line: str | None = None) -> bool:
    """A T line T-validation must leave T: a bare URL, a pointer-only line, or the running header.

    `opening_line` is the CV's first line (its owner's name, typically); a later
    line identical to it is the page header repeated, not a record (CTWLTR).
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _BARE_URL.match(stripped):
        return True
    if opening_line and stripped == opening_line.strip():
        return True
    if not _POINTER.search(stripped):
        return False
    return len(re.findall(r"\w+", _POINTER.sub("", stripped))) <= _POINTER_LINE_MAX_WORDS


_HEADER_PIN_WHY = "Kept in the section where the CV author listed it"


def _recode(entry: dict, code: str, why: str) -> dict:
    """A copy of `entry` recoded to `code`, keeping the model's answer in pre_pin_*.

    The reasoning is rewritten because a later reasoning-vs-code pass would
    otherwise read "professional education (K4)" and flip the code straight back.
    """
    return {
        **entry,
        "pre_pin_code": entry["taxonomy_code"],
        "pre_pin_reasoning": entry.get("classification_reasoning"),
        "taxonomy_code": code,
        "classification_reasoning": (
            f"{why} (category {code}); "
            f"reading the entry alone had suggested {entry['taxonomy_code']}."
        ),
    }


def apply_non_seat_pins(entries: Iterable[dict]) -> tuple[list[dict], dict]:
    """Recode every Q2 or P row of the CV that `non_seat_pin_code` pins.

    Runs over the whole CV after T-validation, not per group: T-validation is
    where some of these rows got their Q2 (LTTYWI's meetings). A recoded entry
    keeps the model's answer in `pre_pin_code`, as with the other pins.
    Returns (entries, stats) in the post-correction stats shape."""
    out, details = [], []
    for entry in entries:
        pinned = non_seat_pin_code(entry)
        if pinned:
            details.append({"element_idx": entry.get("element_idx_start"),
                            "correction": {"from": entry["taxonomy_code"], "to": pinned[0], "reason": pinned[1]}})
            entry = _recode(entry, *pinned)
        out.append(entry)
    if details:
        logger.info("Stage 3b non-seat pin: %d entr%s recoded", len(details), "y" if len(details) == 1 else "ies")
    return out, {"corrections_applied": len(details), "correction_details": details}


def _pin_for(entry: dict, pin: str | None, leaf: str | None, after_mentee: bool,
             after_unseen_reviewer_lead_in: bool, lead: str) -> tuple[str, str] | None:
    """(code, why) for one model answer: the header pin, then the mentor-grant,
    mentee, someone-else's, content and reviewer pins. A mentee's abstract under
    a students level is the mentee's (N4), not the owner's (S8)."""
    header_code = header_pin_code(entry, pin, leaf)
    if header_code:
        return header_code, _HEADER_PIN_WHY
    return (mentor_grant_pin_code(entry) or mentee_pin_code(entry, after_mentee)
            or someone_elses_pin_code(entry, lead) or content_pin_code(entry)
            or reviewer_pin_code(entry, after_unseen_reviewer_lead_in))


def _in_later_batch(position: int, lead_in_at: int | None, batch_size: int | None) -> bool:
    """Whether the row at `position` went to the model in a later call than the lead-in at `lead_in_at`."""
    if lead_in_at is None or batch_size is None:
        return False
    return position // batch_size > lead_in_at // batch_size


def apply_header_pin(entries: Iterable[dict], context: TaxonomyContext,
                     batch_size: int | None = None) -> tuple[list[dict], int]:
    """Recode model answers that lose to the group's pinned header code or to a content pin.

    Returns (entries, number recoded). A recoded entry keeps the model's answer
    in `pre_pin_code` / `pre_pin_reasoning`; `classification_source` stays
    "llm" (stage 3b's zero-classification gate counts it). The header pin is
    checked first, then the mentor-grant pin (#1710), the mentee pin (#1251),
    the someone-else's pin (#1573), a content pin and the reviewer pin (#1413).

    `batch_size` is the number of entries per classification call the group was
    sent in, in the order given here; None means the group went in one call, so
    the model saw every lead-in and the reviewer pin never applies.
    """
    pin, leaf = pinned_header_code(context), leaf_header_code(context)
    out, recoded, after_mentee, reviewer_lead_in_at, lead = [], 0, False, None, ""
    for position, entry in enumerate(entries):
        as_coded = entry  # the lead line is read off the model's codes, not the pins' (#1573)
        if entry.get("classification_source") == _MODEL_SOURCE:
            unseen_lead_in = _in_later_batch(position, reviewer_lead_in_at, batch_size)
            pinned = _pin_for(entry, pin, leaf, after_mentee, unseen_lead_in, lead)
            if pinned and pinned[0] != entry.get("taxonomy_code"):
                recoded += 1
                entry = _recode(entry, *pinned)
        after_mentee = after_mentee or entry.get("taxonomy_code") in _MENTEE_RECORD_CODES
        reviewer_lead_in_at = _next_reviewer_lead_in(entry, position, reviewer_lead_in_at)
        lead = next_lead(as_coded, lead)
        out.append(entry)
    if recoded:
        logger.info("Stage 3b header pin: %d entr%s recoded", recoded, "y" if recoded == 1 else "ies")
    return out, recoded
