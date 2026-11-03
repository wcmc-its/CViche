"""Which CV lines name a licence, a board certification or another credential,
and where a misfiled one belongs: one definition for stage 3b, stage 6 and the
doctor (#1577 shape 3).

Stage 3b codes a fluoroscopy permit, a laser-procedure credential, a
technologist certificate, an ECFMG certificate or a life-support card F1
(Licensure) or F2 (Board Certification), and stage 6 renders it as a licence
or board row with its name stripped (ZDOAZO 62, HXBPCT 138-155, ECXGAT 48/50,
VTRKUF 28/65, JPNUDX 46, PPMTJH 28). The reverse also happens: a licence to
practise coded B2, a diplomate line coded I, which leaves Licensure or Board
Certification empty (LTTYWI 56/58). Stage 3b's credential pin, the WCM table
corrector, stage 6's licensure guard and the doctor's `credential_misfiled`
lint read the same tests, defined once here (CODING_STANDARDS.md §1.5), so a
fix and the lint that measures it read a line the same way.

The homes follow the October 2022 WCM template, `core/taxonomy_v7.json` and
the #1577 body (owner-authored), with the 2026-11-02 defaults:

- A licence to practise (any profession, active or past, temporary or
  limited), a state controlled-substance registration, the DEA number and the
  NPI are F1.
- A specialty or subspecialty board certification, a diplomate of a specialty
  board, and a registry or technologist certification (ASCP, NRCM, ARDMS) are
  F2. A late board-exam step that confers certification stays F2.
- Everything else a Licensure or Board Certification list holds is B2, Other
  Educational Experiences ("i.e., certificates, etc"): the ECFMG certificate,
  licensing exams (USMLE, COMLEX, NBME, FLEX), an early board-exam step, an
  educational-equivalency evaluation, a fluoroscopy or radiation permit, a
  laser or other procedure credential, an accreditation, a life-support
  course, a teaching certificate, and a course, training or certificate of
  completion.
- A fellowship or membership line never moves (the owner's ruling of
  2026-11-02: a fellowship under a certification heading with no election
  wording stays I), nor does a line that names a role or an award.

`misfiled_credential_home(text, code, in_board_block)` is the one entry
point: for a line given `code`, the `CredentialHome` it belongs under
instead, or None. It never moves a line that names a licence out of F1
(a licensing exam or the ECFMG certificate beside a licence on one line is
the licence's row), a line that names an identifier (NPI, DEA, CDS, UPIN,
Medicare) anywhere, or a line it cannot read; and it moves into F1 or F2
only from the codes `MOVABLE_INTO_CREDENTIAL_CODES` names (other education,
membership, honors: never T, D, a position, a training or an affiliation
code), and into F1 never from honors (`LICENCE_STATEMENT_SOURCE_CODES`).
With `in_board_block` (the row before, in the same heading group,
is a board certification inside a board-certification block,
`next_in_board_block`) it also reads an unworded membership line as the
block's next certification (`_is_board_block_line`, FCAAUV 15).

The WCM table corrector (`core.validators.wcm_table_corrector`) is the one
reader that moves a T line: it already promoted a T line carrying a board or
licence signature to F2 or F1, and it now sends that line to the home this
module gives it read as F2 or F1 instead.

Pure: data in, data out; imports nothing but `re`, `typing` and
`core.honor_lines`'s membership code and year pattern.
"""

import re
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from .honor_lines import MEMBERSHIP_CODE, YEAR_RE

#: The codes this module sends a line to.
LICENCE_CODE = "F1"
BOARD_CODE = "F2"
OTHER_EDUCATION_CODE = "B2"
#: The codes a licence or board statement may be moved out of into F1 or F2:
#: other education, membership and honors. Never T (a heading, a scaffold or
#: a table cell: an earlier draft moved 3 heading lines and a mentee-table
#: cell to F2), never D (a position, another family), never an F code, and
#: not C, G or E: a training, affiliation or employment row that mentions a
#: licence ("Physician, private practice; <state> medical license") is that
#: row, and none moved on the #1243 farm.
MOVABLE_INTO_CREDENTIAL_CODES = frozenset({OTHER_EDUCATION_CODE, MEMBERSHIP_CODE, "H"})
#: The codes a licence statement may be moved out of into F1: other education
#: and membership, not honors. An honors line that states a licence stays
#: where it is, and goes to no other code either (the owner's decision of
#: 2026-11-03 on #1577).
LICENCE_STATEMENT_SOURCE_CODES = frozenset({OTHER_EDUCATION_CODE, MEMBERSHIP_CODE})
#: The codes a credential line is moved out of.
CREDENTIAL_CODES = frozenset({LICENCE_CODE, BOARD_CODE})
#: Every code a credential recode starts from.
CREDENTIAL_SOURCE_CODES = CREDENTIAL_CODES | MOVABLE_INTO_CREDENTIAL_CODES

#: The credential kinds a line can name, as `CredentialHome.kind` reports them.
KIND_ECFMG = "ecfmg"
KIND_LICENSING_EXAM = "licensing_exam"
KIND_BOARD_EXAM_EARLY = "board_exam_early"
KIND_EQUIVALENCY = "equivalency"
KIND_LIFE_SUPPORT = "life_support"
KIND_TEACHING_CERTIFICATE = "teaching_certificate"
KIND_RADIATION_PERMIT = "radiation_permit"
KIND_PROCEDURE_CREDENTIAL = "procedure_credential"
KIND_ACCREDITATION = "accreditation"
KIND_TECH_REGISTRY = "tech_registry"
KIND_BOARD_CERT = "board_cert"
KIND_COURSE_CERTIFICATE = "course_certificate"
KIND_CONTROLLED_SUBSTANCE = "controlled_substance"
KIND_LICENCE_STATEMENT = "licence_statement"
KIND_BOARD_STATEMENT = "board_statement"
KIND_BOARD_BLOCK_LINE = "board_block_line"


class CredentialHome(NamedTuple):
    """Where a credential line belongs: the code, and the kind it names."""
    code: str
    kind: str


#: The reasoning stage 3b's credential pin writes on a recode, by the code
#: it recodes to (`stage3b.header_pin.credential_pin_code`), and the one it
#: writes for an unworded board-block line. Stage 6 and the doctor read them
#: to tell the pin's recode from another step's (`is_credential_pin`).
CREDENTIAL_PIN_WHY = {
    OTHER_EDUCATION_CODE: "A credential that is no licence and no board certification is other education",
    LICENCE_CODE: "A licence to practise or a controlled-substance registration is licensure",
    BOARD_CODE: "A board, registry or specialty certification is board certification",
}
BOARD_BLOCK_WHY = "A credential line listed in a board-certification block is board certification"
_CREDENTIAL_PIN_WHYS = (*CREDENTIAL_PIN_WHY.values(), BOARD_BLOCK_WHY)


#: The National Board of (Osteopathic) Medical Examiners. It names a
#: licensing exam, not a licensing board, so its "medical examiners" is no
#: licence word and its "Diplomate" no board certification.
_NBME = re.compile(
    r"national\s+board\s+of\s+(?:medical|osteopathic(?:\s+medical)?)\s+examiners|\bNBO?ME\b", re.I)
#: An exam named for licensing ("United States Medical Licensing
#: Examination", "Federal Licensing Examination") names no licence.
_LICENSING_EXAM_PHRASE = re.compile(r"licen[cs]ing\s+exam\w*", re.I)
#: A licence word, a licensing board or a registration to practise. A
#: training licence is one whatever it is called: a limited, temporary,
#: training or in-training permit or registration ("Limited Training
#: Permit", "Physician-in-Training Permit", "Postgraduate Training
#: Registration"), so its "training" reads as no course. California's
#: "Physician's and Surgeon's Certificate" is its medical licence.
_LICENCE_WORD = re.compile(r"""
    \blicen[cs](?:e|ed|es|ure|ing)\b | \bliscensure\b
  | \bregistration\s+(?:in\s+medicine|to\s+practi[cs]e)\b
  | \bboard\s+of\s+(?:medical\s+examiners|medicine|registration|physicians|medical\s+licensure|healing\s+arts
                     |pharmacy|nursing|dentistry|psychology|social\s+work|osteopathic)
  | \bmedical\s+(?:board|council)\b | \bmedical\s+examiners\b | \bstate\s+board\b
  | \bphysician(?:['’]s)?\s+and\s+surgeon | \bmedicine\s+and\s+surgery\b | \bhealing\s+arts\b
  | \bregistered\s+(?:nurse|pharmacist|dietitian|physician|psychologist)\b
  | \bGMC\b | \bgeneral\s+medical\s+council\b
  | \bcolegio\s+de\s+m[eé]dicos\b | \bhealth\s+authority\b | (?<!royal\s)\bcollege\s+of\s+physicians\b
  | \blimited\s+permit\b | \bpermit\s+to\s+practi | \btemporary\s+(?:licen|permit|certificate)\b
  | \bphysician\s+permit\b
  | \b(?:limited|temporary|educational|postgraduate|training|in-training)\s+(?:training\s+)?(?:permit|registration)\b
""", re.I | re.X)
#: A statement that the owner holds a licence: "Licensed to practise ...",
#: "<kind> License", "License #<number>", "Lic # <number>", "Licensure:", a
#: licensing board's name with a number after it. Read with the NBME
#: removed. A licensing board's name alone ("<State> Board of Medicine,
#: 2010-present") is no statement: under a membership or honors code it may
#: be a seat on that board.
_LICENCE_STATEMENT = re.compile(r"""
    \blicensed\s+(?:to\s+practi[cs]e|in\s+(?:the\s+)?(?:state|commonwealth)|as\s+an?\b|by\s+the\b)
  | \b(?:medical|physician|nursing|pharmacy|pharmacist|dental|veterinary|state|professional|full|unrestricted
       |permanent|temporary|psycholog\w*|practi[cs]e|practitioner)\s+licen[cs]e\b
  | \blicen[cs]e\s*(?:\#|no\.?|number)\s*:?\s*[A-Z0-9.-]*\d | \bLic\.?\s*\#\s*[A-Z0-9.-]*\d
  | ^\W*(?:[\d/.\s-]+)?licensure\s*:
  | \bboard\s+of\s+(?:medical\s+examiners|medicine|registration\s+in\s+medicine|physicians)\b
       [^\#\d]{0,40}(?:\#|\bno\.?|\bnumber)\s*:?\s*[A-Z]{0,3}\d
""", re.I | re.X)
#: A heading-like label at the head of a line ("Licensure:", "Licenses and
#: Certifications:"): the list's name, not a licence the line names.
_LEAD_LABEL = re.compile(
    r"^\W*(?:licen[cs](?:ure|es?)|certifications?|credentials?)"
    r"(?:\s*(?:and|&|/)\s*(?:licen[cs](?:ure|es?)|certifications?|credentials?))*\s*:\s*", re.I)
#: A federal or payer identifier, or a controlled-substance registration:
#: Licensure's own slots (NPI, DEA) and lines it keeps (#821).
_IDENTIFIER = re.compile(
    r"\bNPI\b|national\s+provider|\bDEA\b|drug\s+enforcement|\bCDS\b|controlled\s+(?:dangerous\s+)?substances?"
    r"|\bUPIN\b|\bBNDD\b|narcotic|\bX-?waiver|DATA\s+(?:2000\s+)?waiver|buprenorphine|suboxone|\bmedicare\b"
    r"|\bmedicaid\b", re.I)
#: A controlled-substance registration or waiver, the DEA's included: its
#: home is Licensure, the template's only controlled-substance slot.
CONTROLLED_SUBSTANCE = re.compile(
    r"\bDEA\b|drug\s+enforcement|\bCDS\b|controlled\s+(?:dangerous\s+)?substances?|\bCSR\b|\bX-?waiver"
    r"|DATA\s+(?:2000\s+)?waiver|buprenorphine|suboxone|narcotic", re.I)
#: A state controlled-substance registration by name (not the DEA's, which
#: Licensure withholds, nor a waiver), with the word naming the document
#: after it: the row stage 6 names in Licensure.
_STATE_CONTROLLED_SUBSTANCE = re.compile(
    r"(?:controlled\s+(?:dangerous\s+)?substances?|\bCSR\b|\bCDS\b)"
    r"(?:\s+(?:registration|licen[cs]e|permit|certificate))?", re.I)
#: The profession a licence to practise names, when it is not medicine:
#: the Licensure table has no column for it, so stage 6 writes it after
#: the state ("<state> (Veterinary Medicine and Surgery)").
_LICENSED_PROFESSION = re.compile(
    r"(?i:\bveterinary(?:\s+medicine(?:\s+and\s+surgery)?)?|\bdentistry\b|\bdental\s+surgery\b"
    r"|\bregistered\s+nurse\b|\bnurse\s+practitioner\b|\bnursing\b|\bpharmacy\b|\bpharmacist\b"
    r"|\bpsycholog(?:y|ist)\b|\bphysical\s+therap(?:y|ist)\b|\boccupational\s+therap(?:y|ist)\b"
    r"|\bsocial\s+work(?:er)?\b|\bchiropractic\b|\boptometry\b|\bpodiatr(?:y|ic\s+medicine)\b"
    r"|\bphysician\s+assistant\b|\bmidwifery\b|\bdietitian\b|\bspeech[- ]language\s+pathology\b"
    r"|\baudiology\b)|\bPT\b")
#: A licence to practise a profession named on the line: "<state> Medical
#: License", "Medical/Controlled Substance License", "Licensed to practise".
_PRACTICE_LICENCE = re.compile(
    r"\b(?:medical|physician|psycholog\w*|nursing|dental|pharmacy)\b[\w\s/]{0,40}?\blicen[cs]"
    r"|\blicen[cs](?:ed|e)\s+to\s+practi", re.I)

#: A life-support or resuscitation course: the acronyms case-sensitively, so
#: an ordinary word never reads as one, and the spelled-out names ("Basic
#: Cardiac Life Support", "Neonatal Resuscitation Program") in any case. The
#: courses whose acronym is an English word (ALSO, ATOM, ASSET, PEARS) count
#: by their names only, so an all-capitals line saying "ALSO" is no course.
#: The doctor's `life_support_as_license` lint reads this pattern too.
LIFE_SUPPORT = re.compile(
    r"\b(?:BCLS|BLS|ACLS|PALS|NRP|ATLS|NALS|ENLS|FCCS|TNCC|ENPC|ASLS|CPR|AED|TECC|PCLS|APLS|ABLS)\b"
    r"|(?i:life\s+support|cardiac\s+life|neonatal\s+resuscitation|heartsaver|first\s+aid|stop\s+the\s+bleed"
    r"|casualty\s+care|trauma\s+operative|surgical\s+skills\s+for\s+exposure|endovascular\s+skills"
    r"|pediatric\s+emergency\s+assessment)")
#: A teaching certificate or licence, not any row that says "teaching"
#: ("teaching hospital").
TEACHING_CERTIFICATE = re.compile(
    r"\bteach(?:er|ers|ing)?\s+(?:certif|licen[cs]|credential)"
    r"|\b(?:certif\w*|licen[cs]\w*|credential\w*)\s+(?:of\s+|as\s+(?:an?\s+)?|in\s+)?teach(?:er|ers|ing)?\b",
    re.I)
#: A licensing exam: the USMLE, COMLEX, NBME, FLEX and their kin, and the
#: language tests that go with them.
_LICENSING_EXAM = re.compile(
    r"\bUSMLE\b|\bCOMLEX\b|\bNBO?ME\b|\bFLEX\b|F\.L\.E\.X|\bFMGEMS\b|\bVQE\b|\bPLAB\b|\bMCCQE\b|\bLMCC\b"
    r"|\bNCLEX\b|\bNAPLEX\b|licensing\s+exam|national\s+board\s+(?:of\s+(?:medical|osteopathic)|exam|part)"
    r"|national\s+boards\b|\bstep\s*(?:[123]|I{1,3})\b(?:\s*(?:CK|CS))?|clinical\s+skills"
    r"|clinical\s+competency\s+test|test\s+of\s+spoken\s+english|\bTOEFL\b", re.I)
_ECFMG = re.compile(r"\bECFMG\b|E\.C\.F\.M\.G|educational\s+commission\s+(?:for|of)\s+foreign", re.I)
_EQUIVALENCY = re.compile(
    r"equivalen\w*\s+(?:degree|evaluation)|educational\s+equivalen|credentials?\s+(?:assessed|evaluat)"
    r"|world\s+education\s+services|\bWES\b|foreign\s+credential", re.I)
#: An exam word, and an early step of a board's exam: "Part I", "written",
#: "qualifying", "in-training", "candidate". A later step (oral, Part II, a
#: certifying exam) confers the certification and stays F2. "Examiners" (a
#: board of medical examiners) and "example" are no exam words.
_EXAM_WORD = re.compile(r"\bexam(?:s|ination|inations)?\b|\bpass(?:ed)?\b|\bcandidate\b", re.I)
_EARLY_STEP = re.compile(
    r"\bpart\s*(?:I|1|one)\b(?![I\d])|\bwritten\b|\bqualifying\b|\bin-?training\b|\bphysics\b|\bcandidate\b"
    r"|\bstep\s*(?:1|I)\b(?!I)|\bcore\s+exam|computer[- ]based", re.I)
#: A statement of certification: the line says the owner is certified. A
#: "certification exam" or "certification written exam" is an exam.
_CERT_STATEMENT = re.compile(
    r"\bcertified\b|\bdiplomat(?:e|s|es)?\b|\bcertification\b(?!\s+(?:\w+\s+)?exam)|re-?certif"
    r"|board\s+certif(?!ication\s+(?:\w+\s+)?exam)"
    r"|maintenance\s+of\s+certification|\bMOC\b|\bcertifying\s+exam|\boral\b|\bpart\s+(?:II|2|two)\b"
    r"|subspecialt", re.I)
_RADIATION = re.compile(
    r"fluoroscop|radiation\s+(?:safety|permit|user|authori)|x-?ray\s+(?:supervisor|operator|permit|machine)"
    r"|radioactive|authorized\s+(?:user|physicist|medical\s+physicist)|\bRHC\s*\d|nuclear\s+regulatory", re.I)
_PROCEDURE = re.compile(
    r"\blaser|\bLASIK\b|\bPRK\b|\bPTK\b|excimer|refractive|customvue|\bVISX\b|technolas|robotic"
    r"|da\s?vinci|moderate\s+sedation", re.I)
_ACCREDITATION = re.compile(
    r"accredit|\blevel\s+(?:I{1,3}|[123])\s+(?:certif|accredit)|workers?['\u2019]?s?\s+comp", re.I)
_TECH_REGISTRY = re.compile(
    r"technologist|\bASCP\b|\bNRCM\b|registry\s+of\s+certified|\bRDMS\b|\bRDCS\b|\bRVT\b|\bRPVI\b|\bARRT\b"
    r"|registered\s+diagnostic|sonographer|registered\s+microbiologist|clinical\s+laboratory\s+scientist", re.I)
#: A specialty board's abbreviation at the head of the line, read
#: case-sensitively ("ABIM, ..."; "About" is no board), after an optional
#: short parenthetical (the diplomate mark). ABLS is a life-support course.
#: The WCM table corrector's board signature reads it too.
BOARD_ACRONYM_LEAD = r"^[^A-Za-z]*(?:\([A-Za-z]{1,3}\)\s*)?AB(?!LS\b)[A-Z]{1,5}\b"
#: A specialty board or its certification. A board's abbreviation counts
#: only at the head of the line (`BOARD_ACRONYM_LEAD`).
_BOARD_CERT = re.compile(
    r"(?i:american\s+board\s+of|american\s+osteopathic\s+board"
    r"|national\s+board\s+of\s+(?!(?:medical|osteopathic))|board[\s-]*certif|board\s+eligible"
    r"|\bdiplomat(?:e|s|es)?\b|re-?certif|maintenance\s+of\s+certification|\bMOC\b|royal\s+college"
    r"|subspecialt|added\s+qualification|\bCAQ\b)"
    r"|\bFRCP\w*|\bFRCS\w*|\bMRCP\w*"
    rf"|{BOARD_ACRONYM_LEAD}")
#: A specialty board named on the line, which outranks a life-support
#: course the line also names ("Board Certified, American Board of <X>
#: 2010; ACLS current" is the board's row). Narrower than `_BOARD_CERT`:
#: "Recertified, <life-support course>" is the course's.
_SPECIALTY_BOARD = re.compile(
    r"(?i:american\s+board\s+of|american\s+osteopathic\s+board"
    r"|national\s+board\s+of\s+(?!(?:medical|osteopathic))|board[\s-]*certif)"
    rf"|{BOARD_ACRONYM_LEAD}")
#: A course, a training or a certificate of completion: what the owner
#: attended or completed.
_COURSE = re.compile(
    r"certificate\s+of\s+(?:completion|attendance|training)|certificate\s+program|\bcourse\b|\btraining\b"
    r"|\bworkshop\b|\bseminar\b|\bCITI\b|\bFEMA\b|good\s+clinical\s+practice|\bGCP\b|\bHIPAA\b|teamstepps"
    r"|six\s+sigma|\blean\b|quality\s+matters|\bIHI\b|\bcompleted\b|\bcompletion\b"
    r"|human\s+(?:research\s+)?(?:subjects|participants?)", re.I)
#: A role, an office or an honor: not the owner's credential ("Member, State
#: Board of Medicine", "NBME item writer", "ABIM Foundation Award").
_ROLE = re.compile(
    r"\bmember\b|\bchair\w*|\bcommittee|\bpresident\b|\bdirector\b|\bconsultant\b|\bexaminer\b|\breviewer\b"
    r"|\bpanel\b|\bappointed\b|\bserved\b|\badvis[eo]r|\bliaison\b|\bdelegate\b|\brepresentative\b"
    r"|\bcommissioner\b|\btrustee\b|\bofficer\b|\bsecretary\b|\btreasurer\b|\bgovernor\b|\bitem\s+writer"
    r"|\bquestion\s+writer|\bfaculty\b|\bspeaker\b|\binstructor\s+for\b|\baward\b|\bprize\b|\bhonou?r"
    r"|\bgrant\b|\bfoundation\b|\blecture\b|\bscholar", re.I)
#: A fellowship or membership, or a fellowship grade's post-nominal (FACP,
#: FRCPC, FAAP): it never moves (the owner's ruling of 2026-11-02).
_FELLOW_OR_MEMBER = re.compile(r"(?i:\bfellow(?:ship)?\b|\bmember(?:ship)?\b)|\bF(?:AC|RC|AA)[A-Z]{0,4}\b")
#: A society or an association: a membership's body.
_SOCIETY_WORD = re.compile(r"\bsociet\w*|\bassociation\w*", re.I)
#: A statement that the owner is board certified: a line that opens with
#: "Diplomate" (after an optional date or "Board Certification"), says
#: "board certified" or "board certification", or names an American or
#: National Board of <X> with a number or a date.
_BOARD_STATEMENT = re.compile(r"""
    (?i:^[\W\d]*(?:[A-Za-z]{3,9}\.?\s+)?(?:(?:19|20)\d{2}\W*)?(?:board\s+certification\W*)?diplomat(?:e|s)?\b)
  | (?i:\bboard[\s-]+certified\b) | (?i:\bboard\s+certification\b)
  | ^[\W\d]*(?:American|National)\s+Board\s+of\s+[A-Z][\w&\s]+
       (?:\(\#?\s*\d+\)|\#\s*\d+|\b(?:19|20)\d{2}\b|\d{1,2}/\d{1,2}/\d{2,4})
""", re.X)
#: A line opening "AB<X>, <Specialty>": a board statement, unless the line
#: names a society, an association, an academy or another body and no board
#: ("ABS - <Example> Brachytherapy Society" is a membership, `_BODY_WORD`).
_BOARD_ACRONYM_STATEMENT = re.compile(rf"{BOARD_ACRONYM_LEAD}\s*[,:\-\u2013]\s*[A-Z][a-z]+")
_BODY_WORD = re.compile(
    r"\bsociet\w*|\bassociation\w*|\bacadem\w*|\bcollege\b|\bfederation\b|\binstitute\b|\bfoundation\b"
    r"|\bfacilit\w*|\bcouncil\b|\bnetwork\b|\bconsortium\b|\balliance\b|\bcoalition\b|\bforum\b|\bunion\b"
    r"|\bleague\b|\bclub\b", re.I)
_BOARD_WORD = re.compile(r"\bboard\b", re.I)
#: The one year an unworded board-block line names (FCAAUV 15).
_BOARD_BLOCK_LINE_YEARS = 1
#: A heading that names a board-certification list and nothing else: not
#: "Board Certification and Memberships", not "Licensure and Board
#: Certification" (`opens_board_block`).
_BOARD_BLOCK_HEADING = re.compile(r"board\s+certif|diplomat|specialty\s+certif", re.I)
_NOT_ONLY_BOARDS = re.compile(r"member|societ|affiliat|organi[sz]|fellow|honou?r|award|licen", re.I)
#: A "Board Certification" label opening a line: an inline list (FCAAUV 13,
#: "Board Certification" fused to its first certification). "Board
#: certification in <X>" is a statement, no label.
_BOARD_BLOCK_LABEL = re.compile(r"^\W*(?i:board\s+certifications?)\s*(?::|[-\u2013]|(?=[A-Z])|$)")
#: A word that keeps a line out of a board block's certifications: a
#: fellowship grade's post-nominal (FAHA, FSCAI), an honor society, a
#: trainee or faculty title.
_NOT_A_BLOCK_CERTIFICATION = re.compile(
    r"\bF[A-Z]{2,5}\b|(?i:alpha\s+omega\s+alpha|phi\s+beta\s+kappa|sigma\s+xi|phi\s+kappa\s+phi|golden\s+key"
    r"|\bchief\b|\bresiden(?:t|cy)\b|\bintern(?:ship)?\b|\battending\b|\bprofessor\b|\binstructor\b"
    r"|\blecturer\b)|\bAOA\b")

#: The one kind whose line may name a licence word and still not be a
#: licence: a teaching licence is a teaching certificate. A licensing exam
#: or the ECFMG certificate beside a licence ("USMLE Steps 1-3; <State>
#: Medical License #...") is the licence's row; a licensing exam's own name
#: ("Medical Licensing Examination") is no licence word (`names_licence`).
_KIND_DESPITE_A_LICENCE_WORD = KIND_TEACHING_CERTIFICATE
#: Kinds a board certification on the same line outranks (`_BOARD_CERT`):
#: "<board> certified, laser surgery" is the board's row. A life-support
#: course is outranked by a specialty board only (`_SPECIALTY_BOARD`).
_KINDS_UNDER_A_BOARD = frozenset({KIND_ACCREDITATION, KIND_PROCEDURE_CREDENTIAL, KIND_RADIATION_PERMIT})


def names_licence(text: str) -> bool:
    """Whether the line names a licence, a licensing board or a registration
    to practise, or states that the owner holds a licence. A licensing exam's
    name and the NBME are no licence words."""
    without_exams = _LICENSING_EXAM_PHRASE.sub(" ", _NBME.sub(" ", text))
    return bool(_LICENCE_WORD.search(without_exams) or _LICENCE_STATEMENT.search(_NBME.sub(" ", text)))


def names_identifier(text: str) -> bool:
    """Whether the line names an NPI, a DEA or controlled-substance
    registration, a UPIN, a waiver or a payer number."""
    return bool(_IDENTIFIER.search(text))


def names_state_controlled_substance(text: str) -> bool:
    """Whether the line names a state controlled-substance registration and
    no licence beside it: no licence to practise ("Medical/Controlled
    Substance License") and no licence word once the registration's own
    name is left out ("<State> License #12345 and Controlled Substance
    Registration MA12345" is a licence's line; "<State> Controlled
    Substance License" is the registration's)."""
    return (bool(_STATE_CONTROLLED_SUBSTANCE.search(text)) and not _PRACTICE_LICENCE.search(text)
            and not names_licence(_STATE_CONTROLLED_SUBSTANCE.sub(" ", text)))


def licensed_profession(text: str) -> str:
    """The profession other than medicine a licence line names, as the line
    writes it ("Veterinary Medicine and Surgery", "PT"), or ''."""
    found = _LICENSED_PROFESSION.search(text or "")
    return found.group(0) if found else ""


def _is_early_exam_step(text: str) -> bool:
    """An exam step before certification, with no certification statement."""
    return bool(_EXAM_WORD.search(text) and _EARLY_STEP.search(text)) and not _CERT_STATEMENT.search(text)


def credential_kind(text: str) -> CredentialHome | None:
    """The credential a line names and that credential's home, or None.

    Read in order, so a line naming two kinds takes the first: the ECFMG
    certificate; an early board-exam step that names a board; a licensing
    exam (not a role on its board); an equivalency evaluation; any other
    early exam step; a life-support course; a teaching certificate; a
    radiation permit; a procedure credential; an accreditation; a registry
    certification (on a line that names no licence); a board certification;
    a course or certificate of completion.
    """
    if _ECFMG.search(text):
        return CredentialHome(OTHER_EDUCATION_CODE, KIND_ECFMG)
    if _is_early_exam_step(text) and _BOARD_CERT.search(text):
        return CredentialHome(OTHER_EDUCATION_CODE, KIND_BOARD_EXAM_EARLY)
    if _LICENSING_EXAM.search(text) and not _ROLE.search(text):
        return CredentialHome(OTHER_EDUCATION_CODE, KIND_LICENSING_EXAM)
    if _EQUIVALENCY.search(text):
        return CredentialHome(OTHER_EDUCATION_CODE, KIND_EQUIVALENCY)
    if _is_early_exam_step(text):
        return CredentialHome(OTHER_EDUCATION_CODE, KIND_BOARD_EXAM_EARLY)
    for pattern, kind in _OTHER_EDUCATION_KINDS:
        if pattern.search(text):
            return CredentialHome(OTHER_EDUCATION_CODE, kind)
    if _TECH_REGISTRY.search(text) and not names_licence(text):
        return CredentialHome(BOARD_CODE, KIND_TECH_REGISTRY)
    if _BOARD_CERT.search(text):
        return CredentialHome(BOARD_CODE, KIND_BOARD_CERT)
    if _COURSE.search(text):
        return CredentialHome(OTHER_EDUCATION_CODE, KIND_COURSE_CERTIFICATE)
    return None


#: The B2 kinds read after the exams, in order.
_OTHER_EDUCATION_KINDS = (
    (LIFE_SUPPORT, KIND_LIFE_SUPPORT),
    (TEACHING_CERTIFICATE, KIND_TEACHING_CERTIFICATE),
    (_RADIATION, KIND_RADIATION_PERMIT),
    (_PROCEDURE, KIND_PROCEDURE_CREDENTIAL),
    (_ACCREDITATION, KIND_ACCREDITATION),
)


def _outranked_by_a_board(home: CredentialHome, text: str) -> bool:
    """Whether a board certification the line also names makes the line the
    board's row: an accreditation, a procedure credential or a radiation
    permit beside any board certification (`_BOARD_CERT`), a life-support
    course beside a specialty board (`_SPECIALTY_BOARD`)."""
    if home.kind == KIND_LIFE_SUPPORT:
        return bool(_SPECIALTY_BOARD.search(text))
    return home.kind in _KINDS_UNDER_A_BOARD and bool(_BOARD_CERT.search(text))


def _out_of_credential_code(text: str, code: str) -> CredentialHome | None:
    """The home of an F1 or F2 line that names a credential belonging
    elsewhere: a controlled-substance registration coded F2 is F1; a line
    naming an identifier stays; a line naming a licence (a lead label such
    as "Licensure:" left out) stays in F1 unless it names a teaching
    licence; and a line a board certification outranks
    (`_outranked_by_a_board`) is the board's, F2."""
    if code == BOARD_CODE and CONTROLLED_SUBSTANCE.search(text) and not _BOARD_CERT.search(text):
        return CredentialHome(LICENCE_CODE, KIND_CONTROLLED_SUBSTANCE)
    if names_identifier(text):
        return None
    home = credential_kind(text)
    if home is None or home.code == code:
        return None
    if names_licence(_LEAD_LABEL.sub("", text)) and home.kind != _KIND_DESPITE_A_LICENCE_WORD:
        return None
    if _outranked_by_a_board(home, text):
        return None if code == BOARD_CODE else CredentialHome(BOARD_CODE, KIND_BOARD_CERT)
    return home


def _states_board_certification(text: str) -> bool:
    """Whether the line states a board certification (`_BOARD_STATEMENT`),
    or opens "AB<X>, <Specialty>" and names no other body than a board
    (`_BOARD_ACRONYM_STATEMENT`)."""
    if _BOARD_STATEMENT.search(text):
        return True
    return (bool(_BOARD_ACRONYM_STATEMENT.search(text))
            and not (_BODY_WORD.search(text) and not _BOARD_WORD.search(text)))


def _into_credential_code(text: str) -> CredentialHome | None:
    """F1 for a line that states a licence, F2 for one that states a board
    certification, else None. A role, an identifier, a teaching certificate,
    a course, an exam (unless the line also states a certification), a
    fellowship or membership, life support and the NBME keep the line where
    it is."""
    if (_ROLE.search(text) or names_identifier(text) or TEACHING_CERTIFICATE.search(text)
            or _COURSE.search(text)):
        return None
    if _LICENCE_STATEMENT.search(_NBME.sub(" ", text)) and not _EXAM_WORD.search(text):
        return CredentialHome(LICENCE_CODE, KIND_LICENCE_STATEMENT)
    if (_states_board_certification(text) and not _FELLOW_OR_MEMBER.search(text)
            and not (_EXAM_WORD.search(text) and not _CERT_STATEMENT.search(text))
            and not LIFE_SUPPORT.search(text) and not _NBME.search(text)):
        return CredentialHome(BOARD_CODE, KIND_BOARD_STATEMENT)
    return None


def _is_board_block_line(text: str) -> bool:
    """A line listed inside a board-certification block that names no
    credential word but is one: one year, no credential kind of its own
    (`credential_kind`: a life-support course is its own), and no
    fellowship, membership, society, association, role, award, post-nominal,
    honor society, trainee or faculty title, licence or identifier
    ("<Academy> <field> 2013", FCAAUV 15, the third line of the source's
    inline Board Certification block)."""
    return (len(set(YEAR_RE.findall(text))) == _BOARD_BLOCK_LINE_YEARS
            and credential_kind(text) is None
            and not (_FELLOW_OR_MEMBER.search(text) or _SOCIETY_WORD.search(text) or _ROLE.search(text)
                     or _NOT_A_BLOCK_CERTIFICATION.search(text) or names_identifier(text)
                     or names_licence(text)))


def opens_board_block(text: str, heading_path: Sequence[str]) -> bool:
    """Whether a board row opens a board-certification block: the last level
    of its heading path names a board certification and nothing else
    (`_BOARD_BLOCK_HEADING`, `_NOT_ONLY_BOARDS`), or its line opens with a
    "Board Certification" label (`_BOARD_BLOCK_LABEL`, an inline list)."""
    leaf = str(heading_path[-1]) if heading_path else ""
    return (bool(_BOARD_BLOCK_HEADING.search(leaf) and not _NOT_ONLY_BOARDS.search(leaf))
            or bool(_BOARD_BLOCK_LABEL.search(text or "")))


def next_in_board_block(in_block: bool, code: str, text: str, reasoning: str,
                        heading_path: Sequence[str]) -> bool:
    """Whether the row after this one (coded `code`, its `reasoning`) follows
    a board certification inside a board-certification block: this row is
    F2, not by the block rule itself (its reasoning is not
    `BOARD_BLOCK_WHY`, so unworded lines after one board row do not chain),
    and it opens the block (`opens_board_block`) or the row before it was in
    one (`in_block`). Stage 3b's pin and the doctor's lint read a group's
    rows in order through it."""
    if code != BOARD_CODE or BOARD_BLOCK_WHY in (reasoning or ""):
        return False
    return in_block or opens_board_block(text, heading_path)


def misfiled_credential_home(text: str, code: str, in_board_block: bool = False) -> CredentialHome | None:
    """The `CredentialHome` a line coded `code` belongs under instead, or
    None when it is where it belongs or this module cannot tell.

    An F1 or F2 line moves out when it names a credential whose home is
    another code (`credential_kind`); a line coded one of
    `MOVABLE_INTO_CREDENTIAL_CODES` moves into F1 or F2 when it states a
    licence or a board certification, into F1 only from
    `LICENCE_STATEMENT_SOURCE_CODES` (an honors line that states a licence
    moves nowhere). A membership line with neither moves
    to F2 when `in_board_block` says the row before it in its heading group
    is a board certification inside a board-certification block
    (`next_in_board_block`) and the line is an unworded one
    (`_is_board_block_line`). Nothing else moves.
    """
    text = text or ""
    if code in CREDENTIAL_CODES:
        return _out_of_credential_code(text, code)
    if code not in MOVABLE_INTO_CREDENTIAL_CODES:
        return None
    home = _into_credential_code(text)
    if home is not None and home.code == LICENCE_CODE and code not in LICENCE_STATEMENT_SOURCE_CODES:
        return None
    if home is None and in_board_block and code == MEMBERSHIP_CODE and _is_board_block_line(text):
        return CredentialHome(BOARD_CODE, KIND_BOARD_BLOCK_LINE)
    return home


def is_credential_recode(previous_code: object, code: object) -> bool:
    """Whether a recode from `previous_code` to `code` is one this module's
    readers make: F1 or F2 to B2, or F1, F2 or a movable code to the other
    credential code. Stage 6 reads it off stage 3b's `pre_pin_code` to keep
    the pin's answer."""
    if previous_code == code:
        return False
    if code == OTHER_EDUCATION_CODE:
        return previous_code in CREDENTIAL_CODES
    return code in CREDENTIAL_CODES and previous_code in CREDENTIAL_SOURCE_CODES


def is_credential_pin(entry: Mapping[str, object]) -> bool:
    """Whether stage 3b's credential pin recoded `entry`: a credential
    recode off its `pre_pin_code` (`is_credential_recode`) whose reasoning
    is the pin's own (`CREDENTIAL_PIN_WHY`, `BOARD_BLOCK_WHY`). Another
    step's recode that keeps an earlier pin's `pre_pin_code` (the
    fellowship pin's I to H, then the WCM table corrector's H to F2) is not
    one."""
    reasoning = str(entry.get("classification_reasoning") or "")
    return (is_credential_recode(entry.get("pre_pin_code"), entry.get("taxonomy_code"))
            and any(why in reasoning for why in _CREDENTIAL_PIN_WHYS))
