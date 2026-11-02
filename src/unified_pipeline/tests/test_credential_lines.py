"""core/credential_lines.py (#1577 shape 3): which lines name a licence, a
board certification or another credential, and where a misfiled one belongs.

Invented lines only: no name, number or institution here comes from a CV.

    python3 -m pytest src/unified_pipeline/tests/test_credential_lines.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.credential_lines import (  # noqa: E402
    BOARD_CODE,
    LICENCE_CODE,
    LIFE_SUPPORT,
    OTHER_EDUCATION_CODE,
    CredentialHome,
    credential_kind,
    is_credential_recode,
    misfiled_credential_home,
    names_identifier,
    names_licence,
    names_state_controlled_substance,
)


def _home(text, code, after_board_row=False):
    home = misfiled_credential_home(text, code, after_board_row)
    return (home.code, home.kind) if home else None


# --- F1 or F2 lines that belong in B2 (ZDOAZO 62, HXBPCT 138-155, VTRKUF 28/65, JPNUDX 46, PPMTJH 28) ---

@pytest.mark.parametrize("code,text,kind", [
    ("F1", "2031 Certified by Educational Commission for Foreign Medical Graduates (ECFMG)", "ecfmg"),
    ("F1", "ECFMG Certificate #0-000-000-0, 2031", "ecfmg"),
    ("F1", "E.C.F.M.G. certificate, 2031", "ecfmg"),
    ("F2", "2031 Example Board of Surgery Part I written examination, passed", "board_exam_early"),
    ("F2", "American Board of Example Surgery Qualifying Exam, passed 2031", "board_exam_early"),
    ("F2", "2031 ABXY Part 1 examination passed", "board_exam_early"),
    ("F2", "American Board of Example Medicine in-training exam, 2031", "board_exam_early"),
    ("F2", "Example Physics exam for radiology residents, passed 2031", "board_exam_early"),
    ("F2", "Board candidate, American Board of Example Medicine, 2031", "board_exam_early"),
    ("F2", "American Board of Example Medicine core exam, 2031", "board_exam_early"),
    ("F2", "Example Board computer-based exam, 2031", "board_exam_early"),
    ("F1", "USMLE Step 1, 2031", "licensing_exam"),
    ("F1", "United States Medical Licensing Examination Steps 1-3, 2031", "licensing_exam"),
    ("F1", "COMLEX Level 2, 2031", "licensing_exam"),
    ("F2", "Diplomate, National Board of Medical Examiners, 2031", "licensing_exam"),
    ("F2", "Diplomate, National Board of Osteopathic Medical Examiners, 2031", "licensing_exam"),
    ("F1", "NBME Parts I, II and III, 2031", "licensing_exam"),
    ("F1", "FLEX, Example State, 2031", "licensing_exam"),
    ("F1", "F.L.E.X. examination passed 2031", "licensing_exam"),
    ("F1", "FMGEMS 2031", "licensing_exam"),
    ("F1", "VQE passed 2031", "licensing_exam"),
    ("F1", "PLAB parts 1 and 2, 2031", "licensing_exam"),
    ("F1", "MCCQE Part I, 2031", "licensing_exam"),
    ("F1", "LMCC 2031", "licensing_exam"),
    ("F1", "NCLEX-RN 2031", "licensing_exam"),
    ("F1", "NAPLEX 2031", "licensing_exam"),
    ("F1", "Federal licensing exam, 2031", "licensing_exam"),
    ("F2", "National Board exam, 2031", "licensing_exam"),
    ("F2", "National Board Part II, 2031", "licensing_exam"),
    ("F1", "National Boards, 2031", "licensing_exam"),
    ("F1", "Step 2 CK, 2031", "licensing_exam"),
    ("F1", "Step III, 2031", "licensing_exam"),
    ("F1", "Clinical Skills Assessment, 2031", "licensing_exam"),
    ("F1", "Clinical Competency Test, 2031", "licensing_exam"),
    ("F1", "Test of Spoken English, 2031", "licensing_exam"),
    ("F1", "TOEFL 2031", "licensing_exam"),
    ("F2", "US Educational Equivalent Degree: M.D.\tCredentials assessed by Example Evaluators, Inc.",
     "equivalency"),
    ("F2", "Equivalency evaluation of foreign degree, 2031", "equivalency"),
    ("F2", "Educational equivalence, Example Agency, 2031", "equivalency"),
    ("F2", "Credentials evaluated by Example Services, 2031", "equivalency"),
    ("F2", "World Education Services review, 2031", "equivalency"),
    ("F2", "WES report 2031", "equivalency"),
    ("F2", "Foreign credential review, 2031", "equivalency"),
    ("F2", "Part I written exam, Example College, 2031", "board_exam_early"),
    ("F1", "ACLS 2031-2033", "life_support"),
    ("F2", "Basic Cardiac Life Support, 2031", "life_support"),
    ("F2", "Neonatal Resuscitation Program provider, 2031", "life_support"),
    ("F2", "Heartsaver card 2031", "life_support"),
    ("F2", "First Aid instructor card 2031", "life_support"),
    ("F2", "Stop the Bleed, 2031", "life_support"),
    ("F2", "Tactical Combat Casualty Care, 2031", "life_support"),
    ("F2", "Advanced Trauma Operative Management - current", "life_support"),
    ("F2", "Advanced Surgical Skills for Exposure in Trauma, 2031", "life_support"),
    ("F2", "Basic Endovascular Skills for Trauma, 2031", "life_support"),
    ("F2", "Pediatric Emergency Assessment, Recognition and Stabilization, 2031", "life_support"),
    ("F1", "Example State Teacher Certificate, 2031", "teaching_certificate"),
    ("F1", "Licensed as a teacher, Example State, 2031", "teaching_certificate"),
    ("F1", "2031- Example State Fluoroscopy Operator and Supervisor (XX000000)", "radiation_permit"),
    ("F1", "Radiation safety officer permit, Example State, 2031", "radiation_permit"),
    ("F1", "X-ray supervisor and operator, Example State, 2031", "radiation_permit"),
    ("F1", "Radioactive materials user, 2031", "radiation_permit"),
    ("F1", "Authorized physicist, Example Radiation Program, 2031", "radiation_permit"),
    ("F1", "Example RHC 12345, 2031", "radiation_permit"),
    ("F1", "Example Nuclear Regulatory permit 2031", "radiation_permit"),
    ("F1", "2031 Excimer laser credential, Example Laser Systems", "procedure_credential"),
    ("F1", "LASIK provider, Example Center, 2031", "procedure_credential"),
    ("F1", "PRK credential 2031", "procedure_credential"),
    ("F1", "PTK credential 2031", "procedure_credential"),
    ("F1", "Refractive procedures credential, 2031", "procedure_credential"),
    ("F1", "Example CustomVue system 2031", "procedure_credential"),
    ("F1", "VISX system 2031", "procedure_credential"),
    ("F1", "Technolas system 2031", "procedure_credential"),
    ("F1", "Robotic surgery credential, Example Hospital, 2031", "procedure_credential"),
    ("F1", "da Vinci system credential, 2031", "procedure_credential"),
    ("F1", "Moderate sedation privileges, 2031", "procedure_credential"),
    ("F1", "Example State, USA Level II accreditation, 2031", "accreditation"),
    ("F1", "Level 2 certification, Example Division, 2031", "accreditation"),
    ("F1", "Workers' Compensation consultant, Example State, 2031", "accreditation"),
    ("F2", "Example Center accredited sleep laboratory 2031", "accreditation"),
    ("F2", "Certificate of Completion, Example Leadership Program, 2031", "course_certificate"),
    ("F2", "Certificate program in example methods, 2031", "course_certificate"),
    ("F2", "Example Course, 2031", "course_certificate"),
    ("F2", "Example Training, 2031", "course_certificate"),
    ("F2", "Example Workshop, 2031", "course_certificate"),
    ("F2", "Example Seminar, 2031", "course_certificate"),
    ("F2", "CITI Program, 2031", "course_certificate"),
    ("F2", "FEMA ICS-100, 2031", "course_certificate"),
    ("F2", "Good Clinical Practice, 2031", "course_certificate"),
    ("F2", "GCP 2031", "course_certificate"),
    ("F2", "HIPAA 2031", "course_certificate"),
    ("F2", "TeamSTEPPS Master, 2031", "course_certificate"),
    ("F2", "Six Sigma Green Belt, 2031", "course_certificate"),
    ("F2", "Lean Green Belt, 2031", "course_certificate"),
    ("F2", "Quality Matters reviewer certificate, 2031", "course_certificate"),
    ("F2", "IHI Open School, 2031", "course_certificate"),
    ("F2", "Completed example certificate, 2031", "course_certificate"),
    ("F2", "Example certificate, completion 2031", "course_certificate"),
    ("F2", "Protection of human research subjects, 2031", "course_certificate"),
    ("F2", "Certificate of attendance, Example Meeting, 2031", "course_certificate"),
    ("F2", "Certificate of training, Example Program, 2031", "course_certificate"),
])
def test_a_credential_coded_f1_or_f2_belongs_in_b2(code, text, kind):
    assert _home(text, code) == (OTHER_EDUCATION_CODE, kind)


@pytest.mark.parametrize("text,kind", [
    ("1980   M(ASCP), Technologist in Example Science, Example Society, certificate #X000000", "tech_registry"),
    ("RM(NRCM), Registered Microbiologist, Example Registry of Certified Microbiologists, 2031", "tech_registry"),
    ("Example Registry of Certified Specialists, 2031", "tech_registry"),
    ("RDMS 2031", "tech_registry"),
    ("RDCS 2031", "tech_registry"),
    ("RVT 2031", "tech_registry"),
    ("RPVI 2031", "tech_registry"),
    ("ARRT 2031", "tech_registry"),
    ("Registered Diagnostic Example Sonographer, 2031", "tech_registry"),
    ("Example sonographer credential 2031", "tech_registry"),
    ("Clinical Laboratory Scientist, generalist, 2031", "tech_registry"),
    ("National Board of Public Health Examiners, Certified in Public Health, 2031", "board_cert"),
])
def test_a_registry_or_board_certification_coded_f1_is_f2(text, kind):
    assert _home(text, LICENCE_CODE) == (BOARD_CODE, kind)


@pytest.mark.parametrize("text", [
    "Example State Controlled Substance Registration, 2031",
    "Example State Controlled Dangerous Substances, 2031",
    "DEA registration, 2031",
    "Drug Enforcement Administration, 2031",
    "Example State CDS, 2031",
    "Example State CSR 2031",
    "X-waiver 2031",
    "DATA 2000 waiver, 2031",
    "DATA waiver, 2031",
    "Buprenorphine prescriber, 2031",
    "Suboxone prescriber, 2031",
    "Narcotic registration, 2031",
])
def test_a_controlled_substance_registration_coded_f2_is_f1(text):
    assert _home(text, BOARD_CODE) == (LICENCE_CODE, "controlled_substance")


def test_a_board_line_naming_a_substance_stays_the_boards():
    # A board line that also mentions a substance stays the board's.
    assert _home("American Board of Example Medicine, controlled substances, 2031", BOARD_CODE) is None


@pytest.mark.parametrize("code,text", [
    ("B2", "June 2031 Licensed to practice Example Medicine and Surgery in the state of Example"),
    ("B2", "Licensed to practice in Example State #XX 00000"),
    ("I", "Example State Physician License #00000000A 2031-Pres"),
    ("I", "Example State  Lic # 00.000000    Exp. 07-2031"),
    ("H", "Example State Medical License, active"),
    ("C", "Licensure: Example State, 2031"),
    ("G", "Example State Board of Medicine, 2031"),
    ("E", "Licensed in the state of Example, 2031"),
    ("B2", "Licensed by the Example Board, 2031"),
    ("B2", "License No. 12345, Example State, 2031"),
    ("B2", "Permanent license, Example State, 2031"),
    ("B2", "Temporary license, Example State, 2031"),
    ("I", "Psychologist license, Example State, 2031"),
    ("B2", "Practice license, Example State, 2031"),
    ("B2", "Nurse practitioner license, Example State, 2031"),
    ("B2", "Full license, Example State, 2031"),
    ("B2", "Unrestricted license, Example State, 2031"),
    ("B2", "Professional license, Example State, 2031"),
    ("B2", "Dental license, Example State, 2031"),
    ("B2", "Veterinary license, Example State, 2031"),
    ("B2", "Pharmacist license, Example State, 2031"),
    ("B2", "Licensed to practise, Example State, 2031"),
    ("B2", "Example State Board of Physicians, 2031"),
    ("B2", "Example State Board of Registration in Medicine, 2031"),
])
def test_a_licence_statement_coded_elsewhere_is_f1(code, text):
    assert _home(text, code) == (LICENCE_CODE, "licence_statement")


@pytest.mark.parametrize("code,text", [
    ("I", "1993-Present Diplomate, Example College of Veterinary Example Medicine"),
    ("I", "2031 Board Certification Diplomate, Example Board"),
    ("H", "Board certified in Example Medicine, 2031"),
    ("I", "Example Board Certification in Example Medicine, 2031"),
    ("I", "American Board of Example Medicine (#12345) | 1990-Present"),
    ("I", "National Board of Example Specialists #1234"),
    ("I", "American Board of Example Medicine 3/1/2031"),
    ("I", "ABXY, Example Subspecialty | 2031-Present"),
])
def test_a_board_statement_coded_elsewhere_is_f2(code, text):
    assert _home(text, code) == (BOARD_CODE, "board_statement")


@pytest.mark.parametrize("code,text", [
    # A licence named on the line keeps it in Licensure, whatever else it names.
    ("F1", "Example State Medical License #12345; ACLS current"),
    ("F1", "Example State medical license, 2031; course director"),
    ("F1", "Example State Board of Medical Examiners, Fluoroscopy, 2031"),
    ("F1", "Example Medical Council registration, 2031, accredited"),
    ("F1", "Physician and Surgeon, Example State, laser 2031"),
    ("F1", "Medicine and Surgery, Example State, training licence 2031"),
    ("F1", "Registered Nurse, Example State, BLS 2031"),
    ("F1", "Example GMC registration, completed 2031"),
    ("F1", "General Medical Council, Example training 2031"),
    ("F1", "Colegio de Medicos de Example, course 2031"),
    ("F1", "Example Health Authority, training 2031"),
    ("F1", "Example College of Physicians, practicing physician, training 2031"),
    ("F1", "Limited permit, Example State, training 2031"),
    ("F1", "Permit to practise, Example State, course 2031"),
    ("F1", "Temporary licence, Example State, training 2031"),
    ("F1", "Physician permit, Example State, course 2031"),
    ("F1", "Registration to practise, Example State, course 2031"),
    ("F1", "Liscensure, Example State, course 2031"),
    ("F1", "Example State Board of Registration in Medicine, workshop 2031"),
    ("F1", "Example State Board of Healing Arts, workshop 2031"),
    # a licensing board named without "State Board"
    ("F1", "Example Board of Registration, training 2031"),
    ("F1", "Example Board of Pharmacy, training 2031"),
    ("F1", "Example Board of Nursing, training 2031"),
    ("F1", "Example Board of Dentistry, training 2031"),
    ("F1", "Example Board of Psychology, training 2031"),
    ("F1", "Example Board of Social Work, training 2031"),
    ("F1", "Example Board of Osteopathic Examiners, training 2031"),
    ("F1", "Example State Medical Board, ACLS 2031"),
    ("F1", "Example State Medical Examiners, ACLS 2031"),
    ("F1", "Example State Board, ACLS 2031"),
    ("F1", "Example Healing Arts, ACLS 2031"),
    ("F1", "Lic # 12345, ACLS 2031"),
    # An identifier keeps any line where it is.
    ("F1", "NPI 0000000000; BLS 2031"),
    ("F1", "DEA registration, ACLS 2031"),
    ("F1", "CDS registration, ACLS 2031"),
    ("F1", "Example State Controlled Substance permit, ACLS 2031"),
    ("F1", "UPIN X00000; BLS 2031"),
    ("F1", "BNDD registration; BLS 2031"),
    ("F1", "Narcotic registration; BLS 2031"),
    ("F1", "X-waiver; BLS 2031"),
    ("F1", "DATA 2000 waiver; BLS 2031"),
    ("F1", "Buprenorphine waiver; BLS 2031"),
    ("F1", "Suboxone provider; BLS 2031"),
    ("F1", "Medicare provider; BLS 2031"),
    ("F1", "Medicaid provider; BLS 2031"),
    ("F1", "National provider identifier; BLS 2031"),
    ("F1", "Drug Enforcement Administration; BLS 2031"),
    # A board certification beside a course, permit, procedure or accreditation keeps F2.
    ("F2", "American Board of Example Surgery, certified 2031; laser course"),
    ("F2", "Board certified, Example Medicine; accredited 2031"),
    # A late board-exam step confers certification and stays F2.
    ("F2", "American Board of Example Surgery oral examination, passed 2031"),
    ("F2", "American Board of Example Medicine Part II exam, passed 2031"),
    ("F2", "Example certifying exam, passed 2031"),
    ("F2", "Example subspecialty exam, passed 2031"),
    ("F2", "Board certified, written exam passed 2031"),
    ("F2", "Example certification exam, passed 2031 (MOC)"),
    ("F2", "Example recertification written exam, 2031"),
    ("F2", "Example Diplomate, written exam 2031"),
    ("F2", "Maintenance of Certification, written exam 2031"),
    # each certification statement on its own keeps an early-step word's line in F2
    ("F2", "Board certificate holder, written exam 2031"),
    ("F2", "Written exam passed 2031; MOC current"),
    ("F2", "Maintenance of certification written exam, passed 2031"),
    ("F2", "Certifying exam, written portion, passed 2031"),
    ("F2", "Oral and written exams, passed 2031"),
    ("F2", "Part I and Part II exams passed 2031"),
    ("F2", "Part 1 and Part 2 exams passed 2031"),
    ("F2", "Part one and part two exams passed 2031"),
    ("F2", "Subspecialty written exam passed 2031"),
    # A specialty board certification, and a line naming nothing, stay F2.
    ("F2", "American Board of Example Medicine, 2031"),
    ("F2", "Example Medicine, 2031"),
    # A role on a licensing exam's board is no exam taken.
    ("F2", "NBME item writer, 2031"),
    # A registry certification naming a licence stays in F1.
    ("F1", "Clinical Laboratory Scientist (CLS) License, Example State, 2031"),
    # A licence line naming a board stays F1.
    ("F1", "Example State Medical License; American Board of Example Medicine 2031"),
    ("F1", "Example State license; Royal College of Example 2031"),
])
def test_a_line_where_it_belongs_or_unreadable_stays(code, text):
    assert _home(text, code) is None


@pytest.mark.parametrize("code,text", [
    # The owner's ruling of 2026-11-02: a fellowship or membership never moves.
    ("I", "2031 Example Society, Fellow"),
    ("I", "Fellow, Example College of Physicians, 2031"),
    ("I", "Fellow of the Royal College of Physicians of Example (FRCPC), 2031"),
    ("I", "Diplomate and Member, Example Academy, 2031"),
    ("I", "Board certified member, Example Society, 2031"),
    ("H", "FACXY, American Board of Example Medicine 2031"),
    ("I", "FAAXY, American Board of Example Medicine 2031"),
    ("I", "FRCXY, American Board of Example Medicine 2031"),
    # A role, an office, an award: not the owner's credential.
    ("I", "Member, Example State Board of Medicine, 2031"),
    ("H", "Board Certification Excellence Award, 2031"),
    ("I", "Example State Board of Medical Examiners, Chair, 2031"),
    ("I", "Licensed to practice; Example Committee, 2031"),
    ("I", "Licensed to practice; Example President, 2031"),
    ("I", "Licensed to practice; Example Director, 2031"),
    ("I", "Licensed to practice; Example Consultant, 2031"),
    ("I", "Licensed to practice; Oral examiner, 2031"),
    ("I", "Licensed to practice; Reviewer, 2031"),
    ("I", "Licensed to practice; Example Panel, 2031"),
    ("I", "Licensed to practice; appointed 2031"),
    ("I", "Licensed to practice; served 2031"),
    ("I", "Licensed to practice; Advisor 2031"),
    ("I", "Licensed to practice; Liaison 2031"),
    ("I", "Licensed to practice; Delegate 2031"),
    ("I", "Licensed to practice; Representative 2031"),
    ("I", "Licensed to practice; Commissioner 2031"),
    ("I", "Licensed to practice; Trustee 2031"),
    ("I", "Licensed to practice; Officer 2031"),
    ("I", "Licensed to practice; Secretary 2031"),
    ("I", "Licensed to practice; Treasurer 2031"),
    ("I", "Licensed to practice; Governor 2031"),
    ("I", "Licensed to practice; item writer 2031"),
    ("I", "Licensed to practice; question writer 2031"),
    ("I", "Licensed to practice; faculty 2031"),
    ("I", "Licensed to practice; speaker 2031"),
    ("I", "Licensed to practice; instructor for the course 2031"),
    ("I", "Licensed to practice; prize 2031"),
    ("I", "Licensed to practice; honours 2031"),
    ("I", "Licensed to practice; grant 2031"),
    ("I", "Licensed to practice; Example Foundation 2031"),
    ("I", "Licensed to practice; lecture 2031"),
    ("I", "Licensed to practice; scholar 2031"),
    # An identifier, a teaching certificate, a course, an exam.
    ("I", "Licensed to practice, NPI 0000000000"),
    ("B2", "Licensed as a teacher, Example State, 2031"),
    ("B2", "Licensed to practice: review course, 2031"),
    ("B2", "Licensed to practice, examination passed 2031"),
    # A board statement that is an early exam step, a life-support course or the NBME.
    ("B2", "Board certification written exam, passed 2031"),
    ("B2", "Board certification, ACLS 2031"),
    ("B2", "Diplomate, National Board of Medical Examiners, 2031"),
    ("I", "Diplomate, NBME, 2031"),
    # T, D and the F codes are never moved into F1 or F2 by a statement.
    ("T", "Licensed to practice in Example State"),
    ("D1", "Diplomate, American Board of Example Medicine"),
    ("S1", "Board certified, Example Medicine, 2031"),
])
def test_what_never_moves(code, text):
    assert _home(text, code) is None


def test_a_board_abbreviation_reads_only_at_the_head_and_in_capitals():
    assert _home("ABXY, Example Subspecialty, 2031", "I") == (BOARD_CODE, "board_statement")
    assert _home("Abxy, Example Subspecialty, 2031", "I") is None
    assert credential_kind("(D)ABXY Example Specialty 2031") == CredentialHome(BOARD_CODE, "board_cert")
    assert credential_kind("About example specialty 2031") is None
    assert credential_kind("Example line, ABXY 2031") is None


@pytest.mark.parametrize("text", [
    "FRCPXY 2031", "FRCSXY 2031", "MRCPXY 2031", "Royal College of Example 2031",
    "American Osteopathic Board of Example 2031", "Board eligible, Example 2031",
    "Recertified 2031", "Maintenance of Certification, Example 2031", "MOC 2031",
    "Example subspecialty 2031", "Added qualification in Example 2031", "CAQ Example 2031",
    "Board-certified, Example 2031", "Diplomates, Example 2031",
])
def test_each_board_certification_form_is_f2(text):
    assert credential_kind(text) == CredentialHome(BOARD_CODE, "board_cert")


@pytest.mark.parametrize("text", [
    "Example State Physician License #00000000A 2031-Pres",
    "Example State  Lic # 00.000000",
    "United States Medical Licensing Examination, licensing exam",
])
def test_names_licence_reads_statements_and_not_exam_names(text):
    assert names_licence(text) is (not text.startswith("United"))


def test_the_nbme_names_no_licence_and_no_board():
    assert not names_licence("Diplomate, National Board of Medical Examiners, 2031")
    assert names_licence("Example State Board of Medical Examiners, 2031")
    assert not names_licence("NBOME 2031")


def test_names_identifier_reads_every_identifier():
    assert names_identifier("NPI 0000000000") and names_identifier("Medicaid ID 00")
    assert not names_identifier("Example State Medical License 12345")


@pytest.mark.parametrize("text,named", [
    ("Example State Controlled Substance Registration #00000000B 2031- current", True),
    ("Example State Controlled Dangerous Substances, 2031", True),
    ("Example CSR 2031", True),
    ("Example CDS 2031", True),
    ("Example State Medical/Controlled Substance License #00000 2031", False),
    ("Example State medical license and controlled substance registration", False),
    ("Licensed to practice; controlled substance registration", False),
    ("License to practise and CSR, Example State", False),
    ("Example DEA registration 2031", False),
])
def test_a_state_controlled_substance_registration_is_named_without_a_practice_licence(text, named):
    assert names_state_controlled_substance(text) is named


def test_life_support_acronyms_are_capitals_and_english_words_count_by_name():
    for acronym in ("BCLS", "BLS", "ACLS", "PALS", "NRP", "ATLS", "NALS", "ENLS", "FCCS", "TNCC",
                    "ENPC", "ASLS", "CPR", "AED", "TECC", "PCLS", "APLS"):
        assert LIFE_SUPPORT.search(f"{acronym} 2031"), acronym
    assert not LIFE_SUPPORT.search("Acls 2031")
    assert not LIFE_SUPPORT.search("EXAMPLE STATE MEDICAL LICENSE, ALSO EXAMPLE STATE")
    assert LIFE_SUPPORT.search("advanced cardiac life support")


@pytest.mark.parametrize("text,after,home", [
    # FCAAUV 15's shape: the third line of an inline Board Certification block.
    ("Example Academy of Example Medicine 2031", True, (BOARD_CODE, "board_block_line")),
    ("Example Academy of Example Medicine 2031", False, None),
    ("Example Academy of Example Medicine 2031-2033", True, None),
    ("Example Academy of Example Medicine", True, None),
    ("Example Society of Example Medicine 2031", True, None),
    ("Example Association of Example Medicine 2031", True, None),
    ("Fellow, Example Academy 2031", True, None),
    ("Member, Example Academy 2031", True, None),
    ("Example Academy Award 2031", True, None),
    ("Example Academy, NPI 2031", True, None),
    ("Example State Medical License 2031", True, (LICENCE_CODE, "licence_statement")),
    ("Example State license 2031", True, (LICENCE_CODE, "licence_statement")),
    ("Example Academy licensing 2031", True, None),
])
def test_an_unworded_line_after_a_board_row_is_the_blocks_certification(text, after, home):
    assert _home(text, "I", after) == home
    assert _home(text, "H", after) in (None, (LICENCE_CODE, "licence_statement"))


@pytest.mark.parametrize("previous,code,recode", [
    ("F1", "B2", True), ("F2", "B2", True), ("F1", "F2", True), ("F2", "F1", True),
    ("B2", "F1", True), ("I", "F2", True), ("H", "F2", True), ("C", "F1", True),
    ("G", "F1", True), ("E", "F2", True),
    ("I", "B2", False), ("K4", "B2", False), ("Q2", "B2", False), ("F1", "F1", False),
    ("B2", "B2", False), ("I", "H", False), (None, "F1", False), ("T", "F2", False),
])
def test_a_credential_recode_is_one_this_module_makes(previous, code, recode):
    assert is_credential_recode(previous, code) is recode


def test_an_empty_line_moves_nowhere():
    assert misfiled_credential_home("", "F1") is None
    assert misfiled_credential_home(None, "I", True) is None
