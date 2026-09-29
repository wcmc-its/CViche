# Academic CV Taxonomy Reference Guide

**Version**: 7.5
**Last Updated**: 2025-12-03
**Purpose**: Comprehensive reference for WCM CV classification taxonomy with complete code definitions

**Source**: WCM CV Template (October 2022 Final) + User Corrections
**Definitive Code List**: `src/unified_pipeline/core/taxonomy_v7.json`

---

## Table of Contents

1. [Taxonomy Overview](#taxonomy-overview)
2. [Complete Code Listing](#complete-code-listing)
3. [Section Definitions](#section-definitions)
4. [Common Confusions](#common-confusions)
5. [Disambiguation Rules](#disambiguation-rules)

---

## Taxonomy Overview

The WCM CV taxonomy consists of **60 valid codes** organized into **20 top-level categories** (A-T) with hierarchical subcategories.

### Code Statistics

- **Total valid codes**: 60
- **Parent codes** (1-char): 11 codes (A, C, E, G, H, I, J, O, P, R, T)
- **Child codes** (2-char): 36 codes (B1-B2, D1-D3, F1-F2, K1-K5, L1-L3, M1-M2, N1-N4, Q1-Q4, S0-S9)
- **Sub-child codes** (3-char): 9 codes (M2A-M2D, N3A-N3B, Q4A-Q4D)

**NOTE**: Clinical trials are classified under M2A (no end date) or M2B (ended), by end date (#291). The M4 codes are removed.

### Category Groups

**Identity & Background** (A-B)
- A: Personal/Contact Information
- B1: Academic Degrees
- B2: Other Educational Experiences

**Training & Positions** (C-D)
- C: Postdoctoral Training
- D1-D3: Professional Positions & Employment

**Credentials & Recognition** (E-H)
- E: Employment Status
- F1-F2: Licensure & Board Certification
- G: Institutional & Hospital Affiliations
- H: Honors & Awards

**Professional Activities** (I-J)
- I: Professional Organizations & Society Memberships
- J: Percent Effort & Institutional Responsibilities

**Educational Contributions** (K)
- K1-K5: Teaching & Educational Activities

**Clinical Activities** (L)
- L1-L3: Clinical Practice, Innovation & Leadership

**Research & Scholarship** (M-S)
- M1-M2: Research Activities (including M2A-M2D: grants, clinical trials, patents)
- N1-N4: Mentoring & Advising (including N3A-N3B)
- O: Institutional Leadership
- P: Institutional Administrative Activities
- Q1-Q4: Professional Service (including Q4A-Q4D)
- R: Invitations to Speak/Present
- S1-S9: Bibliography (Publications, Books, Media)

**Other** (T)
- T: Appendix/Other

---

## Complete Code Listing

### A - Personal Data / Contact Information
- **A**: Personal/Contact Information

### B - Education
- **B1**: Academic Degrees (MD, PhD, etc.)
- **B2**: Other Educational Experiences (includes Professional Development/T3)

### C - Postdoctoral Training
- **C**: Postdoctoral Training (no subcodes)

### D - Professional Positions & Employment
- **D1**: Academic Appointments
- **D2**: Hospital Appointments
- **D3**: Other Professional Positions & Employment

### E - Employment Status
- **E**: Employment Status (RARELY USED)

### F - Licensure & Board Certification
- **F1**: Licensure
- **F2**: Board Certification

### G - Institutional & Hospital Affiliations
- **G**: Institutional & Hospital Affiliations

### H - Honors & Awards
- **H**: Honors & Awards

### I - Professional Organizations & Society Memberships
- **I**: Professional Organizations & Society Memberships

### J - Percent Effort & Institutional Responsibilities
- **J**: Percent Effort & Institutional Responsibilities

### K - Educational Contributions
- **K1**: Didactic teaching (Courses, Lectures, Group Teaching)
- **K2**: Clinical teaching (Precepting, Rotations)
- **K3**: Administrative teaching (Curriculum Development, Leadership)
- **K4**: Continuing education and professional education
- **K5**: Community education or patient outreach

### L - Clinical Practice, Innovation, and Leadership
- **L1**: Clinical Practice
- **L2**: Clinical Innovations
- **L3**: Clinical Leadership

### M - Research
- **M1**: Research Activities (Summary)
- **M2**: Research Support (Grants, Clinical Trials, and Patents)
  - **M2A**: Current/Active Funding (includes active clinical trials)
  - **M2B**: Past/Completed Funding (includes completed clinical trials)
  - **M2C**: Pending Funding (includes pending clinical trials)
  - **M2D**: Patents & Inventions (includes Technology Transfer/T2)

**NOTE**: Clinical trials are classified under M2A (no end date) or M2B (ended), by end date (#291). The M4 codes (M4A, M4B, M4C) are removed.

### N - Mentoring
- **N1**: Leadership and mentoring in programs
- **N2**: Institutional Training Grants and Mentored Trainee Grants
- **N3**: Mentees
  - **N3A**: Current Mentees
  - **N3B**: Past Mentees
- **N4**: Scholarly Outputs Resulting From Mentorship

### O - Institutional Leadership Activities
- **O**: Institutional Leadership Activities

### P - Institutional Administrative Activities
- **P**: Institutional Administrative Activities

### Q - Extramural Professional Responsibilities
- **Q1**: Leadership in Extramural Organizations
- **Q2**: Service on Boards/Committees
- **Q3**: Grant Reviewing / Study Sections
- **Q4**: Editorial Activities
  - **Q4A**: Editor/Co-Editor
  - **Q4B**: Journals/Textbooks/Books (Editorial roles)
  - **Q4C**: Editorial Board Membership
  - **Q4D**: Journal Reviewing/Ad hoc Reviewing

**NOTE**: Q5 does NOT exist and should never be used.

### R - Invitations to Speak/Present
- **R**: Invitations to Speak/Present

### S - Bibliography
- **S0**: Researcher Profile & Bibliometric Summary (ORCID, h-index, citation counts)
- **S1**: Peer-reviewed Research Articles
- **S2**: Reviews and Editorials
- **S3**: Books
- **S4**: Chapters
- **S5**: Non-peer-reviewed Research Publications
- **S6**: Case Reports
- **S7**: In review (manuscripts submitted or in preparation)
- **S8**: Abstracts
- **S9**: Other (media, podcasts, etc.)

**NOTE**: S0 captures researcher identifiers (ORCID, ResearchGate, Google Scholar, Scopus) and bibliometric summaries (h-index, citation counts) that precede publication lists. S10-S15 are processing intermediates only, not valid taxonomy codes.

### T - Appendix
- **T**: Appendix/Other
  - T1 (Community & Public Engagement) - routes to T
  - T2 (Technology Transfer) - routes to M2D
  - T3 (Professional Development) - routes to B2
  - T4 (References) - routes to T

---

## Section Definitions

### A - Personal/Contact Information

**Purpose**: Core identifying information

**Typical Entries**:
- Name, email, phone, address
- ORCID, institutional affiliations
- Personal narrative or research statement

**Common Confusions**: None (straightforward)

---

### B - Education

**Purpose**: Formal degree-granting education

**B1 - Academic Degrees**:
- MD, PhD, MS, MPH, MBA
- Graduation dates, institutions, majors
- Thesis titles

**B2 - Other Educational Experiences**:
- Non-degree graduate education
- Professional development programs (formerly T3)
- Certificate programs

**Common Confusions**:
- **B ↔ C**: Degree programs vs postdoctoral training
- **Key Rule**: Degree awarded → B. Training without degree → C.

---

### C - Postdoctoral Training

**Purpose**: Post-degree training positions

**Typical Entries**:
- Postdoctoral Fellow, 2015–2017
- Resident, Internal Medicine, 2019–2022
- Clinical Fellow, Cardiology, 2022–2024

**Common Confusions**:
- **C ↔ B**: Fellowship as part of degree vs post-degree
- **C ↔ D**: Training position vs faculty position
- **C ↔ N**: Own training vs mentee training

**Key Rules**:
- Training with mentor attribution → C
- Degree program → B
- Faculty appointment → D
- Someone you supervised → N

---

### D - Professional Positions & Employment

**Purpose**: Employment positions (clinical, academic, administrative)

**D1 - Academic Appointments**:
- Assistant/Associate/Full Professor
- Research Scientist positions

**D2 - Hospital Appointments**:
- Attending Physician
- Clinical appointments

**D3 - Other Professional Positions & Employment**:
- Non-academic professional roles
- Industry positions
- Consulting roles

**Common Confusions**:
- **D ↔ O**: Position vs leadership role
- **Key Rule**: Position = job title. Leadership = authority over programs/people/budget.

---

### E - Employment Status

**Purpose**: Current employment classification

**Typical Entries**:
- Full-time faculty
- Part-time appointment
- Emeritus status

**Common Confusions**: Rare (clear category)

---

### F - Licensure & Board Certification

**F1 - Licensure**:
- Medical license, State of California, 2020–present
- Professional licenses

**F2 - Board Certification**:
- Board Certified, Internal Medicine, ABIM, 2022
- Specialty certifications

**Common Confusions**: None (clear categories)

---

### G - Institutional & Hospital Affiliations

**Purpose**: Formal institutional relationships

**Typical Entries**:
- Hospital privileges
- Department affiliations
- Research center memberships

**Common Confusions**: None (clear category)

---

### H - Honors & Awards

**Purpose**: Recognition, prizes, honors

**Typical Entries**:
- Best Paper Award, 2022
- Elected to National Academy of Sciences, 2020
- Young Investigator Award

**Common Confusions** (HIGH frequency):
- **H ↔ I**: Fellowship as honor vs membership
- **H ↔ M2**: Small awards vs funded grants
- **H ↔ Q1**: Award from society vs leadership role
- **H ↔ R**: Named lectureship as honor vs presentation

**Key Rules**:
- One-time recognition → H
- Ongoing membership → I
- Large funded project → M2
- Active governance → Q1
- Presentation event → R

---

### I - Professional Organizations & Society Memberships

**Purpose**: Memberships in professional societies

**Typical Entries**:
- Member, American Medical Association, 2015–present
- Fellow, American College of Physicians (FACP), since 2019

**Common Confusions**:
- **I ↔ H**: Ongoing fellowship vs honor
- **I ↔ Q1**: Membership vs leadership role

**Key Rules**:
- Post-nominal abbreviations (FACP, FRCP) → I
- "Since YYYY", "current member" → I
- "Elected", "honorary" WITHOUT ongoing signals → H
- Officer/board role → Q1

---

### J - Percent Effort & Institutional Responsibilities

**Purpose**: Time allocation and formal responsibilities

**Typical Entries**:
- Clinical: 40% FTE
- Research: 30% FTE
- Teaching: 20% FTE
- Administrative: 10% FTE

**Common Confusions**: None (clear category)

---

### K - Educational Contributions

**K1 - Didactic Teaching**:
- Course Director, Introduction to Genomics
- Formal lectures and courses

**K2 - Clinical Teaching**:
- Preceptor, Family Medicine Clerkship
- Clinical instruction during service

**K3 - Administrative Teaching**:
- Curriculum development
- Educational program leadership

**K4 - Continuing Education**:
- CME courses
- Professional education programs

**K5 - Community Education**:
- Patient outreach
- Public health education
- Community engagement

**Common Confusions**:
- **K2 ↔ L**: Clinical teaching vs clinical service
- **K ↔ S3/S4**: Teaching materials vs published books
- **K ↔ N**: Informal mentoring vs formal advising

**Key Rules**:
- Formal courses → K
- Published textbooks → S3
- Named mentees with outcomes → N

---

### L - Clinical Practice, Innovation & Leadership

**L1 - Clinical Practice**:
- Clinical attending, patient care
- FTE allocation to clinical service

**L2 - Clinical Innovations**:
- Quality improvement projects
- Clinical process improvements
- New clinical programs

**L3 - Clinical Leadership**:
- Medical director roles
- Clinical program leadership

**Common Confusions** (HIGH frequency):
- **L ↔ M1**: QI vs research
- **L ↔ M2A/M2B/M2C**: Clinical service vs trial participation
- **L ↔ K2**: Clinical service vs teaching

**Key Rules**:
- Local quality improvement → L
- Systematic evaluation with publication → M1
- Trial participation → M2A/M2B (by end date)
- Educational supervision → K2

---

### M - Research Activities

**M1 - Research Activities**:
- Ongoing research projects
- Lab leadership, PI roles
- Research infrastructure

**M2 - Research Support** (Grants, Clinical Trials, Patents):
- **M2A - Current/Active**: Active grants AND active clinical trials
- **M2B - Past/Completed**: Completed grants AND completed clinical trials
- **M2C - Pending**: Submitted grants AND pending clinical trials
- **M2D - Patents & Inventions**: Patent numbers (US1234567), licensed IP, technology transfer (formerly T2)

**NOTE**: Clinical trials are unified with grants under M2A (no end date) or M2B (ended), by end date (#291). The former M3 (Patents) is now M2D. M4 codes are removed.

**Common Confusions**:
- **M1 ↔ L**: Research vs QI
- **M2 ↔ H**: Funded grants vs small awards
- **M2 ↔ N**: Training grants (document in BOTH)
- **M2A/M2B/M2C ↔ S**: Trial conduct vs publications

---

### N - Mentoring & Advising

**N1 - Leadership & Mentoring Programs**:
- Program Director, T32 Training Program
- Mentoring program leadership

**N2 - Training Grants**:
- T32 grants
- Institutional training programs
- Training grant leadership

**N3 - Mentees**:
- **N3A - Current Mentees**: Active mentorship relationships
- **N3B - Past Mentees**: Former mentees with outcomes

**N4 - Scholarly Outputs**:
- Publications resulting from mentorship
- Mentee achievements
- Mentee career outcomes

**Common Confusions**:
- **N ↔ M2**: Training grants (document in BOTH M2 AND N)
- **N ↔ S**: Mentee publications vs own publications
- **N ↔ K**: Formal advising vs informal teaching
- **N ↔ C**: Mentee vs own training

**Key Rules**:
- Named individuals with dates/outcomes → N
- Training grants → M2 AND N
- Mentee publications → N
- Own publications → S

---

### O - Institutional Leadership Activities

**Purpose**: Leadership with executive authority over institutional programs, budgets, personnel

**Typical Entries**:
- Chair, Department of Medicine
- Vice Chair for Research
- Division Chief
- Director (with budget authority)

**Common Confusions** (HIGH frequency):
- **O ↔ D**: Leadership vs position
- **O ↔ Q1**: Internal vs external leadership

**Key Rules**:
- Authority over budget/personnel/programs → O
- Job title without governance → D
- External organization → Q1

---

### P - Institutional Administrative Activities

**Purpose**: Administrative service within institution

**Typical Entries**:
- Committee membership
- Administrative service
- Institutional task forces

**Common Confusions**:
- **P ↔ O**: Administrative service vs leadership
- **P ↔ Q**: Internal vs external service

---

### Q - Extramural Professional Responsibilities

**Q1 - Leadership in External Organizations**:
- President, American Society of X
- Board Member, National Foundation
- Officer roles in external organizations

**Q2 - Service on Boards/Committees**:
- Committee membership in external organizations
- Advisory boards
- Professional panels

**Q3 - Grant Reviewing / Study Sections**:
- Study section member, NIH
- Grant reviewer for NSF
- Funding panel service

**Q4 - Editorial Activities**:
- **Q4A - Editor/Co-Editor**: Editor-in-Chief, Co-Editor roles
- **Q4B - Journals/Textbooks/Books**: Editorial roles for publications
- **Q4C - Editorial Board Membership**: Board member for journals
- **Q4D - Journal Reviewing/Ad hoc Reviewing**: Manuscript peer review

**NOTE**: Q5 does NOT exist and should never be used.

**Common Confusions**:
- **Q1 ↔ H**: Leadership vs honor
- **Q1 ↔ O**: External vs internal
- **Q1 ↔ I**: Leadership vs membership

---

### R - Invitations to Speak/Present

**Purpose**: Invited presentations, lectures, talks

**Typical Entries**:
- Keynote speaker, ASCO Annual Meeting
- Named lectureship: Jerry Dolovich Memorial Lecture
- Grand Rounds, Massachusetts General Hospital
- Invited departmental seminars

**Common Confusions**:
- **R ↔ H**: Presentation vs honor
- **R ↔ S8**: Invited talk vs conference abstract

**Key Rules**:
- "Invited", "keynote", "plenary" → R
- Abstract submission → S8
- Named lectureships: context-dependent

---

### S - Bibliography

**S0 - Researcher Profile & Bibliometric Summary**:
- ORCID, Google Scholar, ResearchGate, Scopus Author ID
- h-index, g-index, i10-index statements
- Citation counts and publication metrics
- Publication volume summaries ("Over X publications")
- Precedes S1-S9 publication lists

**S1 - Peer-Reviewed Research Articles**:
- Original research in peer-reviewed journals
- Clinical studies
- Basic science research

**S2 - Reviews & Editorials**:
- Review articles
- Editorials
- Perspectives

**S3 - Books**:
- Authored books
- Edited volumes
- ISBN required

**S4 - Chapters**:
- Book chapters
- Contributions to edited volumes

**S5 - Non-Peer-Reviewed Publications**:
- White papers
- Technical reports
- Non-peer-reviewed articles

**S6 - Case Reports**:
- Published case reports
- Case series

**S7 - In Review / Submitted / In Preparation**:
- Manuscripts under review
- Submitted manuscripts
- Manuscripts in preparation

**S8 - Abstracts & Conference Proceedings**:
- Conference abstracts
- Poster presentations
- Meeting proceedings

**S9 - Other Media**:
- Podcasts
- Blogs
- Videos
- Online media
- Public scholarship

**NOTE**: S10-S15 are processing intermediates and are NOT valid taxonomy codes.

**Common Confusions**:
- **S ↔ N**: Own publications vs mentee publications
- **S1 ↔ M2A/M2B/M2C**: Trial publications vs trial conduct
- **S3/S4 ↔ K**: Books vs teaching materials
- **S8 ↔ R**: Abstracts vs invited talks

---

## Common Confusions

### High-Priority Disambiguation Areas

1. **H (Honors) ↔ I (Professional Organizations)**
   - Post-nominal abbreviations → I
   - One-time recognition → H

2. **H (Honors) ↔ M2 (Research Funding)**
   - >$100K + PI role → M2
   - Small awards → H

3. **L (Clinical) ↔ M1 (Research)**
   - Local QI → L
   - IRB + publication intent → M1

4. **M2 (Funding) ↔ N (Mentoring)**
   - Training grants → BOTH M2 AND N

5. **D (Positions) ↔ O (Leadership)**
   - Budget/personnel authority → O
   - Job title → D

6. **Q1 (External) ↔ O (Institutional)**
   - External organization → Q1
   - Home institution → O

---

## Disambiguation Rules

### General Principles

1. **Primary Purpose Test**: What is the MAIN objective?
   - Recognition → H
   - Service delivery → L
   - Education → K
   - Research → M
   - Governance → O or Q1

2. **Documentation Test**: What evidence is present?
   - Grant number → M2A/M2B/M2C (based on status)
   - NCT number → M2A/M2B/M2C (based on trial status)
   - ISBN → S3/S4
   - DOI → S1
   - Patent number → M2D

3. **Relationship Test**: Who is the focus?
   - CV owner's own work → appropriate category
   - Someone CV owner supervises → N
   - Peer/colleague relationship → Q

4. **Authority Test**: Does role carry executive power?
   - Budget/personnel authority → O
   - External governance → Q1
   - Job title without authority → D

5. **Dual Documentation**: Some activities warrant multiple entries
   - Training grants → M2 AND N
   - Trial result papers → M2A/M2B/M2C AND S1
   - Textbooks used in courses → S3 AND K

---

## Version History

- **v7.2** (2025-11-27): Major disambiguation rules update
  - Added disambiguation_notes section for overloaded terms: chair, fellow, professor, director, curator, affiliated, chief, editor, grant
  - Updated C: Graduate Research Assistant → C (not D1); multi-year institutional fellowships → C (not H)
  - Updated D1: Affiliated Faculty → D1; museum appointments → D1; endowed chairs → D1
  - Updated D3: Non-faculty research staff (Biostatistician, Research Analyst) → D3
  - Updated H: Honorary Professor → H; endowed chairs → D1 (not H)
  - Updated I: Professional society fellowships (FACP, FAHA) → I; National Academy → H
  - Updated M1: Fieldwork/excavations → M1
  - Updated M2: Grants ALWAYS M2 regardless of CV section header
  - Updated N4: Clarified mentorship outputs vs bibliography cross-references
  - Updated O: Department Chair → O; Endowed Chair → D1
  - Updated T: Extensive warnings against misuse; editorial roles → Q4, grants → M2
  - Added noise entry filtering in repair_segmentation.py
- **v7.1** (2025-11-26): Classification fixes for 2067, 2075 CVs
  - Updated L2, L3 for committee membership clarity
  - Updated P, Q2, Q3 for grant review distinctions
  - Updated R for "Invited Participant" inclusion
- **v7.5** (2025-12-03): Taxonomy restructuring - clinical trials unified with grants
  - Removed M4A/M4B/M4C codes - clinical trials now use M2A/M2B/M2C based on status
  - Renamed M3 (Patents) to M2D - patents now under Research Support
  - Updated all disambiguation rules and confusion areas accordingly
- **v7.0** (2025-11-25): Updated to reflect consolidated definitive taxonomy with complete 59-code structure
  - Added all subcodes: Q4A-Q4D, M2A-M2C, N3A-N3B (M4A-M4C deprecated in v7.5)
  - Removed invalid codes: Q5, S10-S15 (processing intermediates)
  - Added D3, F1-F2, K5, L1-L3
  - Aligned with taxonomy_definitions.py
- **v6.0** (2025-11-17): Added confusion areas, comprehensive documentation
- **v5.0** (2025-11-15): Added validators for trials, books, datasets
- **v4.0** (2025-11-10): Initial comprehensive taxonomy reference

---

## Related Documentation

- **`src/unified_pipeline/core/taxonomy_definitions.py`** - Definitive code source
- **`CONFUSION_MATRIX_V6.md`** - Complete confusion area documentation
- **`CV_PIPELINE_ARCHITECTURE_V5.md`** - System architecture

---

**Document Maintainer**: CV Taxonomy Pipeline Team
**Review Cycle**: After taxonomy changes
**Canonical Source**: `src/unified_pipeline/core/taxonomy_definitions.py`
