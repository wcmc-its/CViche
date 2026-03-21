# WCM Faculty CV Structure - Complete Reference

**Weill Cornell Medicine Faculty CV Template (October 2022)**

This document provides a comprehensive guide to all 66 sections and subsections defined in the WCM Faculty CV Template, which serves as the target schema for the CV Parsing Pipeline.

---

## Table of Contents

- [Overview](#overview)
- [Section A: Personal Data](#section-a-personal-data)
- [Section B: Education](#section-b-education)
- [Section C: Postdoctoral Training](#section-c-postdoctoral-training)
- [Section D: Professional Positions](#section-d-professional-positions)
- [Section E: Employment Status](#section-e-employment-status)
- [Section F: Licensure & Board Certification](#section-f-licensure--board-certification)
- [Section G: Institutional & Hospital Affiliations](#section-g-institutional--hospital-affiliations)
- [Section H: Honors & Awards](#section-h-honors--awards)
- [Section I: Professional Organizations](#section-i-professional-organizations)
- [Section J: Percent Effort & Responsibilities](#section-j-percent-effort--responsibilities)
- [Section K: Educational Contributions](#section-k-educational-contributions)
- [Section L: Clinical Practice & Leadership](#section-l-clinical-practice--leadership)
- [Section M: Research](#section-m-research)
- [Section N: Mentoring](#section-n-mentoring)
- [Section O: Institutional Leadership](#section-o-institutional-leadership)
- [Section P: Administrative Activities](#section-p-administrative-activities)
- [Section Q: Extramural Professional Responsibilities](#section-q-extramural-professional-responsibilities)
- [Section R: Invitations to Speak/Present](#section-r-invitations-to-speakpresent)
- [Section S: Bibliography](#section-s-bibliography)
- [Section T: Supplemental Information](#section-t-supplemental-information)

---

## Overview

The WCM Faculty CV Template organizes academic career information into **20 primary sections (A-T)** with a total of **66 sections/subsections**. Each section captures specific aspects of an academic professional's career.

### Structure Hierarchy

```
Primary Section (e.g., B: Education)
  ├─ Subsection 1 (e.g., B1: Degrees)
  │   └─ Nested Subsection (e.g., B1a: Doctoral Degrees) [if applicable]
  └─ Subsection 2 (e.g., B2: Other Education)
```

### Extraction ID Format

- Simple sections: `A`, `C`, `E`, `G`, `H`, `I`, `J`, `O`, `P`, `R`
- Flat subsections: `B1`, `B2`, `D1`, `F1`, `L3`
- Nested subsections: `K1a`, `S1a`, `M2a`

---

## Section A: Personal Data

**ID**: `A` | **Type**: Simple Section | **Fields**: 20

### Purpose
Captures faculty member's identity, contact information, and professional identifiers.

### Key Fields
- Full Name (including middle names/initials)
- Degrees/Post-nominals (e.g., PhD, MD, RN)
- Academic/Professional Title
- Department/Unit
- Primary Institution
- Office Address, Telephone, Email
- Personal identifiers: ORCID iD, Scopus Author ID, ResearcherID
- NIH eRA Commons Username
- LinkedIn URL
- Citizenship (optional)
- Date of Birth (optional, internal use)
- Visa eligibility information

### Extraction Notes
- **Header extraction**: Name and credentials extracted from CV header (first ~200 characters)
- Middle initials and all post-nominal credentials should be captured
- ORCID and other identifiers are increasingly important for disambiguation

### Example Entry
```json
{
  "Full Name": "Heidi K. Holtz",
  "Degrees/Post-nominals": "PhD, RN",
  "Office Address": "4483 Duncan Avenue, St. Louis, MO. 63110",
  "Work Email": "heidi.holtz@bjc.org",
  "ORCID iD": "0000-0002-7634-2854"
}
```

---

## Section B: Education

### B1: Degrees

**ID**: `B1` | **Type**: Subsection | **Fields**: 13

#### Purpose
Documents all earned academic degrees (undergraduate through doctoral).

#### Key Fields
- Degree Type (BA, BS, MD, PhD, etc.)
- Field of Study/Major
- Institution Name
- City, State/Province, Country
- Date Conferred (or anticipated)
- Honors/Distinctions
- Thesis/Dissertation Title
- Advisor(s)

#### Example
```json
{
  "Degree": "PhD",
  "Field of Study": "Molecular Biology",
  "Institution": "Stanford University",
  "City": "Stanford",
  "State": "CA",
  "Country": "USA",
  "Date Conferred": "2010",
  "Honors": "Summa Cum Laude"
}
```

### B2: Other Education

**ID**: `B2` | **Type**: Subsection | **Fields**: 10

#### Purpose
Non-degree training, certificates, professional development courses.

#### Key Fields
- Program/Course Name
- Institution
- Location
- Dates (Start - End)
- Certificate/Credential Awarded
- Description

#### Example
```json
{
  "Program": "Advanced Clinical Research Methods",
  "Institution": "Harvard T.H. Chan School of Public Health",
  "Dates": "2015",
  "Certificate": "Certificate in Clinical Research"
}
```

### B3: Professional Development

**ID**: `B3` | **Type**: Subsection | **Fields**: 8

#### Purpose
Continuing education, workshops, seminars attended for professional growth.

#### Key Fields
- Activity Name
- Organization
- Location
- Date
- Credits (if applicable)
- Description

---

## Section C: Postdoctoral Training

**ID**: `C` | **Type**: Simple Section | **Fields**: 11

### Purpose
Documents postdoctoral fellowships and research training positions.

### Key Fields
- Position Title (e.g., "Postdoctoral Fellow")
- Department/Program
- Institution
- Location
- Dates (Start - End)
- Supervisor/Mentor Name
- Research Focus/Area
- Funding Source (if applicable)

### Example
```json
{
  "Position": "Postdoctoral Research Fellow",
  "Department": "Department of Neuroscience",
  "Institution": "Johns Hopkins University",
  "Location": "Baltimore, MD",
  "Dates": "2012-2015",
  "Supervisor": "Dr. Jane Smith",
  "Research Focus": "Neural circuit development"
}
```

---

## Section D: Professional Positions

### D1: Academic Appointments

**ID**: `D1` | **Type**: Subsection | **Fields**: 12

#### Purpose
Faculty positions at academic institutions.

#### Key Fields
- Rank/Title (Assistant Professor, Associate Professor, Professor, etc.)
- Track (Tenure, Clinician-Educator, Research, etc.)
- Department
- Institution
- Dates (Start - End or "Present")
- Percent Time (FTE)
- Location

#### Example
```json
{
  "Rank": "Associate Professor",
  "Track": "Tenure-Track",
  "Department": "Department of Biology",
  "Institution": "Columbia University",
  "Dates": "2018 - Present",
  "Percent Time": "100%"
}
```

### D2: Hospital Appointments

**ID**: `D2` | **Type**: Subsection | **Fields**: 11

#### Purpose
Clinical appointments at hospitals or healthcare systems.

#### Key Fields
- Title
- Hospital/Health System
- Department/Division
- Location
- Dates
- Clinical FTE
- Admitting Privileges (Yes/No)

### D3: Other Positions

**ID**: `D3` | **Type**: Subsection | **Fields**: 10

#### Purpose
Non-academic, non-hospital professional positions (industry, government, non-profit).

### D4: Visiting Appointments

**ID**: `D4` | **Type**: Subsection | **Fields**: 9

#### Purpose
Temporary or visiting positions at other institutions.

---

## Section E: Employment Status

**ID**: `E` | **Type**: Simple Section | **Fields**: 6

### Purpose
Current employment classification and status.

### Key Fields
- Primary Appointment Status (Full-time, Part-time, Adjunct, etc.)
- Tenure Status (Tenured, Tenure-Track, Non-Tenure-Track)
- FTE Percentage
- Start Date of Current Position
- Contract Type (if applicable)

---

## Section F: Licensure & Board Certification

### F1: Licensure

**ID**: `F1` | **Type**: Subsection | **Fields**: 8

#### Purpose
Professional licenses (medical, nursing, psychology, etc.).

#### Key Fields
- License Type (MD, RN, PhD Psychologist, etc.)
- License Number
- State/Country
- Date Issued
- Expiration Date
- Status (Active, Inactive, Expired)

#### Example
```json
{
  "License Type": "Medical License (MD)",
  "License Number": "123456",
  "State": "New York",
  "Date Issued": "2010-06-15",
  "Status": "Active"
}
```

### F2: Board Certification

**ID**: `F2` | **Type**: Subsection | **Fields**: 9

#### Purpose
Medical specialty board certifications.

#### Key Fields
- Board Name (e.g., American Board of Internal Medicine)
- Specialty/Subspecialty
- Certification Date
- Recertification Date
- Certificate Number
- Status

---

## Section G: Institutional & Hospital Affiliations

**ID**: `G` | **Type**: Simple Section | **Fields**: 9

### Purpose
Formal affiliations with institutions beyond primary appointment.

### Key Fields
- Institution Name
- Affiliation Type (Adjunct, Affiliate, Collaborating, etc.)
- Department/Unit
- Location
- Dates
- Description of Affiliation

---

## Section H: Honors & Awards

**ID**: `H` | **Type**: Simple Section | **Fields**: 9

### Purpose
Recognition, prizes, honors, and awards received.

### Key Fields
- Award/Honor Name
- Awarding Organization
- Date Awarded
- Location
- Description
- Award Amount (if applicable)
- Criteria/Significance

### Example
```json
{
  "Award": "Presidential Early Career Award for Scientists and Engineers (PECASE)",
  "Organization": "National Science Foundation",
  "Date": "2020",
  "Description": "Highest honor bestowed by the U.S. government on outstanding scientists and engineers"
}
```

---

## Section I: Professional Organizations

**ID**: `I` | **Type**: Simple Section | **Fields**: 10

### Purpose
Membership in professional societies and organizations.

### Key Fields
- Organization Name
- Membership Type (Member, Fellow, Board Member, etc.)
- Role/Position (if leadership)
- Dates (Start - End or "Present")
- Election/Appointment Date (for leadership roles)
- Description of Activities

### Example
```json
{
  "Organization": "American Association for the Advancement of Science (AAAS)",
  "Membership Type": "Fellow",
  "Date Elected": "2019",
  "Description": "Elected for distinguished contributions to molecular biology"
}
```

---

## Section J: Percent Effort & Institutional Responsibilities

**ID**: `J` | **Type**: Simple Section | **Fields**: 12

### Purpose
Breakdown of time allocation across different responsibilities.

### Key Fields
- Research (%)
- Teaching (%)
- Clinical Care (%)
- Administration (%)
- Service (%)
- Other (%)
- Description of Responsibilities
- Effective Date

### Example
```json
{
  "Research": "50%",
  "Teaching": "30%",
  "Administration": "15%",
  "Service": "5%",
  "Effective Date": "2024-07-01"
}
```

---

## Section K: Educational Contributions

### K1: Teaching Responsibilities - Classroom

**ID**: `K1` | **Type**: Subsection | **Fields**: 14

#### Purpose
Formal classroom teaching activities.

#### Key Fields
- Course Title
- Course Number
- Level (Undergraduate, Graduate, Professional)
- Department/School
- Role (Instructor, Co-Instructor, Guest Lecturer)
- Contact Hours per Term
- Enrollment
- Academic Year/Term
- Description

### K2: Teaching Responsibilities - Clinical

**ID**: `K2` | **Type**: Subsection | **Fields**: 13

#### Purpose
Clinical teaching activities (bedside, rounds, clinics).

### K3: Teaching Responsibilities - Laboratory

**ID**: `K3` | **Type**: Subsection | **Fields**: 12

#### Purpose
Laboratory-based teaching and training.

### K4: Clerkship/Clinical Rotations Director

**ID**: `K4` | **Type**: Subsection | **Fields**: 11

#### Purpose
Leadership of clinical rotations.

### K5: Curriculum Development

**ID**: `K5` | **Type**: Subsection | **Fields**: 12

#### Purpose
Development of new courses, curricula, educational programs.

### K6: Educational Program Director

**ID**: `K6` | **Type**: Subsection | **Fields**: 13

#### Purpose
Leadership of educational programs (residency, fellowship, degree programs).

### K7: Educational Scholarship

**ID**: `K7` | **Type**: Subsection | **Fields**: 10

#### Purpose
Scholarship about medical/health professions education (not research publications).

### K8: Educational Awards & Recognition

**ID**: `K8` | **Type**: Subsection | **Fields**: 9

#### Purpose
Teaching awards and educational honors.

### K9: Other Educational Contributions

**ID**: `K9` | **Type**: Subsection | **Fields**: 8

#### Purpose
Educational activities not captured elsewhere.

---

## Section L: Clinical Practice & Leadership

### L1: Clinical Practice

**ID**: `L1` | **Type**: Subsection | **Fields**: 13

#### Purpose
Direct patient care activities.

#### Key Fields
- Practice Setting (Hospital, Clinic, Private Practice, etc.)
- Specialty/Focus Area
- Location
- Dates
- Clinical FTE
- Patient Volume
- Services Provided

### L2: Clinical Leadership

**ID**: `L2` | **Type**: Subsection | **Fields**: 12

#### Purpose
Leadership roles in clinical settings.

#### Key Fields
- Title/Role (Chief, Director, Chair, etc.)
- Department/Unit
- Institution
- Dates
- Responsibilities
- Size of Unit/Team

### L3: Clinical Quality & Safety

**ID**: `L3` | **Type**: Subsection | **Fields**: 11

#### Purpose
Quality improvement, patient safety initiatives.

### L4: Other Clinical Contributions

**ID**: `L4` | **Type**: Subsection | **Fields**: 9

#### Purpose
Clinical activities not captured elsewhere.

---

## Section M: Research

### M1: Research Activities

**ID**: `M1` | **Type**: Subsection | **Fields**: 13

#### Purpose
Descriptions of research programs, focus areas, methodologies.

#### Key Fields
- Research Focus/Area
- Description
- Start Date
- Status (Active, Completed, In Progress)
- Collaborators
- Key Findings (if completed)

### M2: Research Support

**ID**: `M2` | **Type**: Subsection | **Fields**: 18 (most complex section)

#### Purpose
Extramural and intramural funding for research.

#### Key Fields
- Grant Title
- Funding Agency
- Grant Number
- Role (PI, Co-PI, Co-Investigator, Consultant, etc.)
- Total Award Amount
- Direct Costs
- Percent Effort
- Project Period (Start - End)
- Status (Active, Completed, Pending)
- Abstract/Aims

#### Example
```json
{
  "Grant Title": "Neural Mechanisms of Learning and Memory",
  "Agency": "National Institutes of Health (NIH)",
  "Grant Number": "R01-NS123456",
  "Role": "Principal Investigator",
  "Total Award": "$2,500,000",
  "Percent Effort": "25%",
  "Project Period": "2020-2025",
  "Status": "Active"
}
```

### M3: Patents & Intellectual Property

**ID**: `M3` | **Type**: Subsection | **Fields**: 14

#### Purpose
Patents, copyrights, licenses, inventions.

#### Key Fields
- Title of Invention
- Patent Number (or Application Number)
- Inventors
- Filing Date
- Grant Date
- Status (Pending, Issued, Licensed)
- Assignee
- Description

### M4: Clinical Trials

**ID**: `M4` | **Type**: Subsection | **Fields**: 16

#### Purpose
Clinical trials and research studies involving human subjects.

#### Key Fields
- Trial Title
- ClinicalTrials.gov ID
- Phase (I, II, III, IV)
- Role (PI, Co-PI, Site PI, etc.)
- Sponsor
- Start Date
- Completion Date
- Status
- Enrollment Target
- Primary Outcome

---

## Section N: Mentoring

### N1: Postdoctoral Mentoring

**ID**: `N1` | **Type**: Subsection | **Fields**: 10

#### Purpose
Mentorship of postdoctoral fellows.

#### Key Fields
- Mentee Name
- Department/Program
- Dates
- Current Position (outcome)
- Description of Mentorship

### N2: Graduate Student Mentoring

**ID**: `N2` | **Type**: Subsection | **Fields**: 10

#### Purpose
Dissertation/thesis committee service and primary mentorship.

### N3: Medical/Professional Student Mentoring

**ID**: `N3` | **Type**: Subsection | **Fields**: 9

#### Purpose
Mentorship of medical, dental, nursing, pharmacy students.

### N4: Resident/Fellow Mentoring

**ID**: `N4` | **Type**: Subsection | **Fields**: 9

#### Purpose
Clinical training mentorship.

### N5: Faculty Mentoring

**ID**: `N5` | **Type**: Subsection | **Fields**: 9

#### Purpose
Mentorship of junior faculty.

### N6: Other Mentoring

**ID**: `N6` | **Type**: Subsection | **Fields**: 8

#### Purpose
Mentoring not captured elsewhere (K-12, undergraduates, etc.).

---

## Section O: Institutional Leadership Activities

**ID**: `O` | **Type**: Simple Section | **Fields**: 11

### Purpose
Leadership roles within primary institution.

### Key Fields
- Title/Role (Director, Chair, Dean, Vice Provost, etc.)
- Department/Unit/Program
- Institution
- Dates
- Reporting Structure
- Responsibilities/Scope
- Budget Oversight (if applicable)
- Team Size

### Example
```json
{
  "Title": "Chair, Department of Biological Sciences",
  "Institution": "Columbia University",
  "Dates": "2020 - Present",
  "Responsibilities": "Strategic leadership, faculty recruitment, budget oversight",
  "Department Size": "45 faculty, 150 graduate students"
}
```

---

## Section P: Institutional Administrative Activities

**ID**: `P` | **Type**: Simple Section | **Fields**: 10

### Purpose
Administrative service within institution (committee work, taskforces).

### Key Fields
- Committee/Activity Name
- Role (Chair, Member, Ex Officio, etc.)
- Institution/Organization
- Dates
- Frequency of Meetings
- Description of Responsibilities

---

## Section Q: Extramural Professional Responsibilities

### Q1: Leadership Positions in Professional Organizations

**ID**: `Q1` | **Type**: Subsection | **Fields**: 11

#### Purpose
Elected or appointed leadership roles outside home institution.

#### Key Fields
- Title/Position
- Organization
- Dates
- Election/Appointment Date
- Responsibilities
- Scope (Local, National, International)

### Q2: Editorial Activities

**ID**: `Q2` | **Type**: Subsection | **Fields**: 10

#### Purpose
Journal editorial boards, guest editorships, peer review.

#### Key Fields
- Journal Name
- Role (Editor-in-Chief, Associate Editor, Editorial Board, Reviewer)
- Dates
- Publisher
- Impact Factor (optional)
- Description

#### Example
```json
{
  "Journal": "Nature Neuroscience",
  "Role": "Associate Editor",
  "Dates": "2019 - Present",
  "Publisher": "Nature Publishing Group",
  "Responsibilities": "Handle 20-30 manuscripts per year"
}
```

### Q3: Review Panels & Study Sections

**ID**: `Q3` | **Type**: Subsection | **Fields**: 11

#### Purpose
Grant review service (NIH, NSF, private foundations).

#### Key Fields
- Panel/Study Section Name
- Organization (NIH, NSF, etc.)
- Role (Chair, Member, Ad Hoc Reviewer)
- Dates
- Meeting Frequency

### Q4: Consulting & Advisory Roles

**ID**: `Q4` | **Type**: Subsection | **Fields**: 12

#### Purpose
Scientific advisory boards, consulting engagements.

#### Key Fields
- Organization/Company
- Role/Title
- Type (Advisory Board, Consultant, Expert Witness, etc.)
- Dates
- Description
- Compensation Status (Paid, Unpaid, In-Kind)

### Q5: Other Extramural Professional Service

**ID**: `Q5` | **Type**: Subsection | **Fields**: 9

#### Purpose
External service not captured elsewhere.

---

## Section R: Invitations to Speak/Present

**ID**: `R` | **Type**: Simple Section | **Fields**: 13

### Purpose
Invited lectures, keynote addresses, symposia presentations.

### Key Fields
- Presentation Title
- Event/Conference Name
- Invitation Type (Keynote, Plenary, Invited Talk, etc.)
- Location
- Date
- Organization/Host
- Audience Size/Type
- Honorarium (if applicable)

### Example
```json
{
  "Title": "Advances in Neural Circuit Mapping",
  "Event": "Society for Neuroscience Annual Meeting",
  "Type": "Keynote Address",
  "Location": "San Diego, CA",
  "Date": "2023-11-15"
}
```

---

## Section S: Bibliography

### S1: Peer-Reviewed Original Research Articles

**ID**: `S1` | **Type**: Subsection | **Fields**: 16 (most complex bibliography entry)

#### Purpose
Published original research in peer-reviewed journals.

#### Key Fields
- Authors (full list or "et al." format)
- Article Title
- Journal Name
- Year
- Volume
- Issue
- Pages (or article number)
- DOI
- PMID
- PMC ID
- Role (First Author, Corresponding Author, Senior Author, etc.)
- Impact Factor (optional)
- Citations (optional)

#### Example
```json
{
  "Authors": "Smith J, Doe A, Johnson B",
  "Title": "Novel mechanisms of synaptic plasticity in the hippocampus",
  "Journal": "Nature Neuroscience",
  "Year": "2022",
  "Volume": "25",
  "Pages": "1234-1245",
  "DOI": "10.1038/s41593-022-01234-5",
  "PMID": "36123456",
  "Role": "Corresponding Author"
}
```

### S2: Peer-Reviewed Review Articles

**ID**: `S2` | **Type**: Subsection | **Fields**: 15

#### Purpose
Review articles, systematic reviews, meta-analyses.

### S3: Peer-Reviewed Book Chapters

**ID**: `S3` | **Type**: Subsection | **Fields**: 14

#### Purpose
Chapters in edited academic books.

#### Additional Fields
- Book Title
- Editors
- Publisher
- ISBN

### S4: Peer-Reviewed Published Abstracts/Posters

**ID**: `S4` | **Type**: Subsection | **Fields**: 13

#### Purpose
Conference abstracts published in journals or proceedings.

### S5: Peer-Reviewed Case Reports

**ID**: `S5` | **Type**: Subsection | **Fields**: 14

#### Purpose
Clinical case reports in peer-reviewed journals.

### S6: Books/Monographs

**ID**: `S6` | **Type**: Subsection | **Fields**: 12

#### Purpose
Authored or edited books.

#### Key Fields
- Authors/Editors
- Book Title
- Publisher
- Publication Year
- ISBN
- Edition (if applicable)
- Page Count

### S7: Non-Peer-Reviewed Publications

**ID**: `S7` | **Type**: Subsection | **Fields**: 11

#### Purpose
Editorials, commentaries, opinion pieces, white papers.

### S8: Published Works in Progress

**ID**: `S8` | **Type**: Subsection | **Fields**: 10

#### Purpose
Articles accepted for publication but not yet published.

### S9: Manuscripts Under Review

**ID**: `S9` | **Type**: Subsection | **Fields**: 9

#### Purpose
Manuscripts currently under peer review.

### S10: Preprints

**ID**: `S10` | **Type**: Subsection | **Fields**: 12

#### Purpose
Preprints on arXiv, bioRxiv, medRxiv, etc.

#### Key Fields
- All standard publication fields plus:
- Preprint Server
- Preprint ID
- Date Posted
- Version
- Status (Not submitted, Under review, Published)

### S11: Patents as Publications

**ID**: `S11` | **Type**: Subsection | **Fields**: 13

#### Purpose
Issued patents (distinct from M3 which covers all IP).

### S12: Other Publications

**ID**: `S12` | **Type**: Subsection | **Fields**: 10

#### Purpose
Publications not captured elsewhere.

### S13: Bibliography Summary Statistics

**ID**: `S13` | **Type**: Subsection | **Fields**: 10

#### Purpose
Summary metrics of publication record.

#### Key Fields
- Total Publications
- Total Peer-Reviewed
- Total First/Senior Author
- h-index
- i10-index
- Total Citations
- Date of Metrics

### S14: Publicly Available Datasets/Code

**ID**: `S14` | **Type**: Subsection | **Fields**: 11

#### Purpose
Shared research data, code repositories.

#### Key Fields
- Dataset/Code Title
- Repository (GitHub, Zenodo, etc.)
- DOI
- URL
- Date Made Available
- License Type
- Description

### S15: Media & Outreach

**ID**: `S15` | **Type**: Subsection | **Fields**: 10

#### Purpose
Popular press, science communication, public outreach.

---

## Section T: Supplemental/Optional

### T1: Research Statement

**ID**: `T1` | **Type**: Subsection | **Fields**: 3

#### Purpose
Narrative description of research program.

#### Key Fields
- Statement Text (open-ended)
- Last Updated Date
- Word Count

### T2: Teaching Philosophy

**ID**: `T2` | **Type**: Subsection | **Fields**: 3

#### Purpose
Narrative description of teaching approach.

### T3: Service Statement

**ID**: `T3` | **Type**: Subsection | **Fields**: 3

#### Purpose
Narrative description of service contributions.

### T4: Additional Information

**ID**: `T4` | **Type**: Subsection | **Fields**: Variable

#### Purpose
Any information not captured in standard sections.

---

## Extraction Priority by Use Case

### For Promotion/Tenure Review
**Essential**: A, B1, C, D1, H, M2 (grants), S1-S6 (publications), N1-N5 (mentoring)
**Important**: K1-K9 (teaching), O, P, Q1-Q3

### For Grant Applications
**Essential**: A, B1, C, D1, M2 (current support), S1-S3 (publications), S13 (metrics)
**Important**: M1 (research description), N1-N2 (training record)

### For Faculty Recruiting
**Essential**: A, B1, C, D1, H, S1-S3, S13
**Supporting**: M1, Q2 (editorial), R (invited talks)

### For Annual Faculty Reports
**Essential**: A, J (effort), K (teaching), L (clinical), M2 (grants), O-P (service), S (publications)

---

## Data Quality Notes

### Common Issues in Extraction

1. **Inconsistent Date Formats**: CVs use varied formats (2020, 2020-2023, June 2020, etc.)
2. **Missing Location Data**: Often need to infer from institution names
3. **Ambiguous Roles**: "Co-author" vs. "Contributing Author" vs. specific ordering
4. **Incomplete Citations**: Some CVs omit DOI, PMID, or even page numbers
5. **Merged Sections**: Faculty may combine sections (e.g., K1-K3 all under "Teaching")

### Validation Strategies

1. **Cross-reference**: Compare extracted name (Section A) with author name in publications (Section S)
2. **Identifier Validation**: Validate ORCID format (XXXX-XXXX-XXXX-XXXX)
3. **Date Logic**: Start date should precede end date
4. **Completeness**: Check for required fields per section

---

## Updates & Versioning

**Current Version**: WCM CV Template October 2022 (v1.0.0)
**Last Schema Update**: 2022-10-15
**Pipeline Compatibility**: All 66 sections supported in Stage 2C extraction

### Future Enhancements

- Section T5: DEI (Diversity, Equity, Inclusion) Statement
- Section M5: Data Management Plans
- Section S16: Software/Tools Developed
- Enhanced support for international degree types (Section B1)

---

## Resources

- **Official Template**: `WCM CV template/wcm_cv_structure-verbose-v1.json`
- **Extraction Scripts**: `outputs/stage_1_segmentation/gold_standard/batch_extract_sections.py`
- **Field Definitions**: See JSON schema in template for complete field specifications
- **Examples**: `outputs/stage_1_segmentation/gold_standard/extraction_results_stage2c/`

---

**Document Version**: 1.0
**Last Updated**: 2025-10-23
**Maintained by**: CV Parsing Pipeline Team
