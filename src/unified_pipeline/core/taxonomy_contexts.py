"""
Enhanced WCM Section Contexts with Routing Rules, Examples, and Confusion Detection

Ported from extract_unextracted_fallback.py (lines 36-527) with enhancements for
two-pass hierarchical classification.

Each section context includes:
- Description of what belongs in this section
- Confusion risk level (low/medium/high)
- Routing rules for disambiguation
- Related sections with examples
- Trigger patterns for confusion detection
"""

from typing import Dict, List, Optional

# WCM Section Contexts with routing rules, examples, and confusion detection
WCM_SECTION_CONTEXTS = {
    'contact_information': {
        'section': 'A - Personal Data / Contact Information',
        'description': 'Personal contact details, identifiers (ORCID, NPI), web presence',
        'confusion_risk': 'low',
        'disambiguation': {},
        'related_sections': {
            'A1': {
                'title': 'Name',
                'examples': [
                    'Jane Doe, MD, PhD',
                    'John Smith, ScD'
                ]
            },
            'A2': {
                'title': 'Email Address',
                'examples': [
                    'jane.doe@example.edu',
                    'jsmith@hospital.org'
                ]
            },
            'A3': {
                'title': 'Phone Numbers',
                'examples': [
                    'Office: (212) 555-1234',
                    'Lab: (212) 555-5678'
                ]
            }
        }
    },
    'education_and_training': {
        'section': 'B - Education',
        'description': 'CV owner\'s formal education. NOT research about education.',
        'confusion_risk': 'low',
        'disambiguation': {
            'undergraduate_vs_graduate': 'B1 for Bachelor\'s degrees. B2 for ALL graduate degrees.',
            'education_vs_training': 'B for degrees. C for postdoctoral training.'
        },
        'related_sections': {
            'B1': {
                'title': 'Undergraduate Education',
                'examples': [
                    'BA Biology, Harvard University, 2010',
                    'BS Chemistry, MIT, 2008'
                ]
            },
            'B2': {
                'title': 'Graduate Education (Master\'s, PhD, ScD, MD, DO)',
                'examples': [
                    'MPH Epidemiology, Johns Hopkins, 2012',
                    'MS Biostatistics, UCLA, 2011',
                    'PhD Biochemistry, Stanford University, 2015',
                    'ScD Environmental Health, Harvard, 2016',
                    'MD, Yale School of Medicine, 2013',
                    'DO, Philadelphia College of Osteopathic Medicine, 2014'
                ]
            }
        }
    },
    'postdoctoral_training': {
        'section': 'C - Postdoctoral Training',
        'description': 'Postdoc research, residency, fellowship, internship positions',
        'confusion_risk': 'low',
        'disambiguation': {
            'postdoc_vs_faculty': 'C for trainee positions. D for faculty/employment.',
            'research_vs_clinical': 'C1 for research postdocs. C2 for residency.'
        },
        'related_sections': {
            'C1': {
                'title': 'Postdoctoral Research Positions',
                'examples': [
                    'Postdoctoral Fellow, Cancer Biology, Stanford, 2015-2018',
                    'Research Fellow, Immunology, Harvard Medical School, 2016-2019'
                ]
            },
            'C2': {
                'title': 'Residency Training',
                'examples': [
                    'Internal Medicine Residency, Massachusetts General Hospital, 2013-2016',
                    'Surgery Residency, Johns Hopkins Hospital, 2014-2019'
                ]
            },
            'C3': {
                'title': 'Fellowship Training',
                'examples': [
                    'Cardiology Fellowship, Cleveland Clinic, 2016-2019',
                    'Oncology Fellowship, MD Anderson, 2015-2018'
                ]
            }
        }
    },
    'professional_positions_employment': {
        'section': 'D - Professional Positions & Employment',
        'description': 'Faculty appointments, employment history, professional positions',
        'confusion_risk': 'medium',
        'disambiguation': {
            'faculty_vs_leadership': 'D for academic appointments. O for director/chair roles with authority.',
            'positions_vs_mentees': 'D for YOUR positions. N for positions of people YOU mentored.'
        },
        'related_sections': {
            'D1': {
                'title': 'Academic Positions',
                'examples': [
                    'Associate Professor, Department of Medicine, 2018-present',
                    'Assistant Professor (tenure-track), Oncology, 2015-2018'
                ]
            },
            'D2': {
                'title': 'Clinical Positions',
                'examples': [
                    'Attending Physician, Internal Medicine, 2016-present',
                    'Clinical Director, Cardiology Clinic, 2019-present'
                ]
            }
        }
    },
    'honors_and_awards': {
        'section': 'H - Honors and Awards',
        'description': 'Academic honors, awards, prizes, recognitions. NOT funding grants or membership categories.',
        'confusion_risk': 'high',
        'disambiguation': {
            'H_vs_M2': 'H if symbolic recognition. M2 if research funding with PI role, budget, grant number.',
            'H_vs_I': 'H if competitive honor/selection. I if ongoing membership category/status.',
            'H_vs_R': 'H if focus on being selected/named. R if focus on talk details.',
            'fellowship_test': '"Fellowship" can be funding (M2), honor (H), or membership (I) - check context.',
            'award_test': '"Award" with grant mechanics → M2. Symbolic recognition → H.',
            'named_lecture_test': 'Named lecture AS award → H. Regular invited lecture → R.'
        },
        'related_sections': {
            'H': {
                'title': 'Honors and Awards',
                'examples': [
                    'NIH Early Career Award for Excellence, 2018 (symbolic, not funded)',
                    'Best Paper Award, American Cancer Society, 2020',
                    'Elected Fellow, American Psychopathological Association, 2015 (competitive honor)',
                    'Young Investigator Award, $1,000, 2019',
                    'John Smith Distinguished Lecture Award (honor), 2023',
                    'Alpha Omega Alpha Honor Society, inducted 2012'
                ]
            },
            'M2': {
                'title': 'Research Support (if actually funded)',
                'examples': [
                    'NIH K23 Career Development Award, PI, 2019-2024, $600K (research funding)',
                    'AHA Predoctoral Fellowship, 2023-2025, $100K (funded training)',
                    'NIH Loan Repayment Program, 2005-2012, $50K/year (funding mechanism)'
                ]
            },
            'I': {
                'title': 'Professional Organizations (if membership status)',
                'examples': [
                    'Fellow, American College of Physicians since 2015 (ongoing membership)',
                    'Member, Fellow class, APHA (membership category)'
                ]
            }
        }
    },
    'professional_orgs_societies': {
        'section': 'I - Professional Organizations and Society Memberships',
        'description': 'Professional society memberships (NOT leadership roles)',
        'confusion_risk': 'high',
        'disambiguation': {
            'I_vs_Q1': 'I if just "Member" or "Fellow". Q1 if role title (Chair, President, Officer).',
            'membership_test': 'Simple membership listing → I. Leadership role → Q1.'
        },
        'related_sections': {
            'I': {
                'title': 'Professional Organizations & Society Memberships',
                'examples': [
                    'American Public Health Association (APHA) member',
                    'Fellow, American College of Physicians',
                    'Member, American Sociological Association since 2015'
                ]
            },
            'Q1': {
                'title': 'Leadership in Extramural Organizations (if leadership)',
                'examples': [
                    'Treasurer, International Society for Clinical Research 2021–2024',
                    'President-Elect, State Medical Association 2023–2024'
                ]
            }
        }
    },
    'educational_contributions': {
        'section': 'K - Educational Contributions',
        'description': 'Teaching activities, course instruction, curriculum development. MUST specify subsection (K1, K2, K4, etc.)',
        'confusion_risk': 'high',
        'disambiguation': {
            'K_vs_B3': 'K if person TEACHES. B2 if person LEARNS/ATTENDS training.',
            'K1_vs_K4': 'K1 for formal courses (undergrad/grad). K4 for CME/professional education.',
            'K2_vs_D': 'K2 for clinical teaching. D2 for clinical positions without teaching focus.'
        },
        'related_sections': {
            'K1': {
                'title': 'Didactic Teaching (courses taught)',
                'examples': [
                    'Instructor, SOC213 Sociology of the Family 2011-present',
                    'Guest lecture "Ethics in Medicine" 2022'
                ]
            },
            'K2': {
                'title': 'Clinical Teaching',
                'examples': [
                    'Attending physician, Internal Medicine inpatient service 2020–present',
                    'Clinical preceptor, Family Medicine clerkship 2018–2023'
                ]
            },
            'K3': {
                'title': 'Administrative Teaching',
                'examples': [
                    'Course Director, Evidence-Based Medicine (Year 2) 2017–2022',
                    'Chair, Curriculum Committee overseeing MD program revision 2022–present'
                ]
            },
            'K4': {
                'title': 'Continuing/Professional Education (CME taught)',
                'examples': [
                    'CME workshop "Gender-Based Violence Training" 2023',
                    'Online webinar for practicing physicians 2021'
                ]
            }
        }
    },
    'research_overview': {
        'section': 'M - Research',
        'description': 'Research activities, grants, funding, patents, clinical trials',
        'confusion_risk': 'high',
        'disambiguation': {
            'M1_vs_M2': 'M1 for research focus/methods. M2A/M2B/M2C for grants/funding based on status.',
            'M2D_vs_M2': 'M2D for patents/inventions. M2A/M2B/M2C for grant support.',
            'M2_vs_N': 'M2 if documenting funding (grant mechanics, PI role, budget). N if documenting mentoring (mentees supervised via training grant).',
            'training_grants': 'T32 with trainee list → N. K23/F32 as own funding → M2A/M2B/M2C.',
            'M2D_vs_S11': 'M2D for patents (legal filings, IP). S11 for code releases (GitHub, PyPI).',
            'M1_vs_S12': 'M1 for research activity description. S12 for dataset deposit (DOI, repository).'
        },
        'related_sections': {
            'M1': {
                'title': 'Research Activities (Summary)',
                'examples': [
                    'Principal Investigator, Immunogenomics of Cancer Study 2022–present',
                    'Developed novel bioinformatics pipeline for single-cell RNA-seq 2021'
                ]
            },
            'M2': {
                'title': 'Research Support (Grants/Funding)',
                'examples': [
                    'NIH R01 CA123456 "Tumor PD-L1 Pathways" (PI) 2022–2027 $1.2M',
                    'AHA Grant 23POST102 Postdoctoral Fellowship 2023–2025 $158K'
                ]
            },
            'M2D': {
                'title': 'Patents & Innovations',
                'examples': [
                    'Patent US1234567B2 "Nanoparticle-based PD-L1 inhibitor" filed 2023',
                    'Provisional patent "Wearable ECG patch with AI analysis" filed 2022'
                ]
            }
            # NOTE: M4 clinical trial codes removed - clinical trials now use M2A/M2B/M2C based on status
        }
    },
    'mentoring': {
        'section': 'N - Mentoring',
        'description': 'Formal supervision and guidance of trainees/junior faculty',
        'confusion_risk': 'high',
        'disambiguation': {
            'N_vs_K': 'N for sustained supervision/mentoring. K for teaching/instruction.',
            'N_vs_D': 'CRITICAL: If section header says "MENTEES", entries show mentee positions (their titles), NOT your appointments.',
            'mentee_positions_vs_own': 'Example: "Research Assistant Professor" under "MENTEES" means YOU mentored that person.',
            'N_vs_M2': 'N if documenting mentees (names, theses, outcomes). M2 if documenting funding (your grant, PI role, budget).',
            'N_vs_S': 'N if mentee record (section header "MENTEES", supervision context). S if YOUR publication with student co-author.',
            'training_grants': 'T32 as director listing trainees → N. K23/F32 as own funding → M2.'
        },
        'related_sections': {
            'N1': {
                'title': 'Leadership & Mentoring Programs',
                'examples': [
                    'Director, Faculty Mentoring Program 2020–present',
                    'Coordinator, Resident Mentoring Pilot Project 2019–2021'
                ]
            },
            'N3': {
                'title': 'Current Mentees (Research)',
                'examples': [
                    'Primary mentor, PhD candidate in Immunology (2019–present)',
                    'Advisor, MSc thesis on Clinical Data Mining (2021–present)'
                ]
            },
            'N4': {
                'title': 'Past Mentees',
                'examples': [
                    'Anderson, Kate (2018). Abortion Care Thesis. Behavioral Sciences',
                    'PhD supervision: Machine Learning Applications 2017-2021'
                ]
            }
        }
    },
    'institutional_leadership': {
        'section': 'O - Institutional Leadership Activities',
        'description': 'Executive/oversight roles INSIDE institution with formal authority',
        'confusion_risk': 'high',
        'disambiguation': {
            'O_vs_P': 'O if: President, Chair (sole), Dean, Director, Chief, Vice Chair. P if: Member, Representative, Co-Chair (shared).',
            'authority_test': 'O requires decision-making authority over people, programs, or budgets',
            'president_chair_rule': 'Senate PRESIDENT or Council CHAIR = O (executive). Member = P (committee).',
            'exec_titles': 'President, Chair (sole), Vice President, Dean, Director, Chief = O',
            'committee_titles': 'Member, Representative, Committee Member, Co-Chair (shared) = P'
        },
        'related_sections': {
            'O': {
                'title': 'Institutional Leadership (executive authority)',
                'examples': [
                    'Program Director, Residency Training 2020–present',
                    'Section Chief, Hospital Medicine 2019–2023',
                    'Vice Chair for Education, Department of Medicine 2021–present'
                ]
            },
            'P': {
                'title': 'Institutional Admin Activities (committees, if no authority)',
                'examples': [
                    'Elected member, Faculty Senate Executive Committee 2021–present',
                    'Co-Chair, Curriculum Committee 2022–present',
                    'Search Committee member for Department Chair 2022'
                ]
            }
        }
    },
    'institutional_administration': {
        'section': 'P - Institutional Administrative Activities',
        'description': 'Committee service, governance roles WITHOUT executive authority',
        'confusion_risk': 'medium',
        'disambiguation': {
            'P_vs_O': 'P for committee participation. O for chairs/directors with formal authority.'
        },
        'related_sections': {
            'P': {
                'title': 'Institutional Administrative Activities',
                'examples': [
                    'Elected member, Faculty Senate Executive Committee 2021–present',
                    'Representative, Department to University Council 2020–2023'
                ]
            }
        }
    },
    'extramural_professional_activities': {
        'section': 'Q - Extramural Professional Responsibilities',
        'description': 'Service/leadership in EXTERNAL organizations',
        'confusion_risk': 'high',
        'disambiguation': {
            'Q1_vs_I': 'Q1 if role title (Chair, President, Officer). I if just "Member" or "Fellow".'
        },
        'related_sections': {
            'Q1': {
                'title': 'Leadership in Extramural Organizations',
                'examples': [
                    'Treasurer, International Society for Clinical Research 2021–2024',
                    'Board Member, National Foundation for Medical Research 2020–present'
                ]
            },
            'Q2': {
                'title': 'Grant Reviewing',
                'examples': [
                    'NIH Study Section member, 2020-2024',
                    'Ad hoc reviewer, National Science Foundation 2021'
                ]
            },
            'Q3': {
                'title': 'Editorial Activities',
                'examples': [
                    'Associate Editor, Journal of Clinical Oncology 2019–present',
                    'Reviewer, Nature Medicine 2018–present'
                ]
            }
        }
    },
    'invitations_to_speak': {
        'section': 'R - Invitations to Speak/Present',
        'description': 'Invited presentations and speaking engagements',
        'confusion_risk': 'low',
        'disambiguation': {},
        'related_sections': {
            'R1': {
                'title': 'Regional Invitations',
                'examples': [
                    'Keynote, Boston Medical Association Annual Meeting, 2023',
                    'Grand rounds, Massachusetts General Hospital, 2022'
                ]
            },
            'R2': {
                'title': 'National Invitations',
                'examples': [
                    'Plenary speaker, American Society of Clinical Oncology, 2024',
                    'Invited talk, National Cancer Institute, 2023'
                ]
            }
        }
    },
    'bibliography': {
        'section': 'S - Bibliography',
        'description': 'Publicly disseminated scholarly works across all publication types. Map to ~21+ detailed subsections initially.',
        'confusion_risk': 'high',
        'disambiguation': {
            'core_principles': [
                'S1 = Original empirical research (new data, methods/results)',
                'S2 = Peer-reviewed interpretive/methodological (reviews, editorials)',
                'Prefer specificity: use S1–S9 subsections when clear',
                'Published vs pre-publication: check DOI and status'
            ],
            'decision_order': [
                '1) PRE-PUBLICATION? → S7 (includes preprints, in-review, in-prep)',
                '2) JOURNAL ARTICLE? If original data → S1; if review/editorial → S2',
                '3) SINGLE-PATIENT? → S6 (case reports)',
                '4) ABSTRACT only? → S8',
                '5) Books/Chapters → S3/S4'
            ],
            'S1_vs_S2': 'S1 if original research with methods/results. S2 if review/editorial/commentary.',
            'S1_vs_S6': 'S1 for research studies. S6 for single patient case descriptions.',
            'S1_vs_S7': 'S1 if published in journal. S7 if preprint (bioRxiv/medRxiv DOI 10.1101) or in review.',
            'S1_vs_S8': 'S8 if conference abstract/poster - CHECK FIRST. Pattern: volume:abstract# or (Suppl)',
            'abstract_detection': 'CRITICAL: volume:abstract# (e.g., "30:1153") → S8. Suppl/Supplement → S8.',
            'S_vs_N': 'S if YOUR publication (citation format). N if MENTEE documentation (section header "MENTEES", supervision context).',
            'S11_vs_M2D': 'S11 for code releases (GitHub, PyPI, Zenodo). M2D for patents (patent numbers, filings, licensing).',
            'S12_vs_M1': 'S12 for dataset deposits (DOI, repository accession). M1 for research activity description.',
            'S3_S4_vs_K': 'S3/S4 for published books/chapters (ISBN, publisher). K5/K6 for teaching materials (syllabi, course content).'
        },
        'related_sections': {
            'S1': {
                'title': 'Published Peer-Reviewed Research Articles (original research)',
                'description': 'Articles that have been PUBLISHED in peer-reviewed journals. Must have publication metadata.',
                'key_indicators': [
                    'Volume/issue/pages (e.g., "2023;12(4):455-462")',
                    'Publisher DOI (NOT bioRxiv/medRxiv)',
                    'PMID (PubMed ID)',
                    'Complete journal citation (Author. Title. Journal. Year;Vol:Pages)',
                    '"Epub ahead of print" or "In press" with journal name',
                    'Journal name + year + publisher DOI (even if pages missing)'
                ],
                'examples': [
                    'Chen L, Patel A. "Single-cell mapping of renal carcinoma." Nature Medicine. 2024;30:1123–1135.',
                    'Smith JA, Doe JQ. "Epigenetic regulation of T-cells." Cell Reports. 2022;40(7):110412.',
                    'Jones R. "Cancer genomics." Cell. 2024. doi:10.1016/j.cell.2024.01.015. Epub ahead of print.'
                ]
            },
            'S2': {
                'title': 'Reviews & Editorials (synthesis, commentary)',
                'examples': [
                    'Lopez MT. "Advances in cardio-oncology." Circulation Reviews. 2024;15(2):55–68.',
                    'Doe JQ. "Commentary on real-world data." BMJ Opinion. 2021.'
                ]
            },
            'S3': {
                'title': 'Books (authored or edited)',
                'examples': [
                    'Rossi E (Ed.). Principles of Translational Oncology. Springer; 2023.',
                    'Doe JQ. Foundations of Clinical Bioinformatics. Elsevier; 2022.'
                ]
            },
            'S4': {
                'title': 'Chapters (book chapters)',
                'examples': [
                    'Doe JQ. "Deep learning in medical imaging." In: AI in Medicine; Elsevier; 2024:55–80.'
                ]
            },
            'S6': {
                'title': 'Case Reports (single patient)',
                'examples': [
                    'Chen L. "Rare cardiac sarcoidosis presentation." Chest. 2023;164(1):e15–e18.'
                ]
            },
            'S7': {
                'title': 'Unpublished Works (In Review / Submitted / In Preparation / Preprints)',
                'description': 'Manuscripts NOT yet published in final form. Includes submissions, preprints, and works in progress.',
                'key_indicators': [
                    'Status keywords: "submitted", "in review", "under review", "revise and resubmit", "in preparation"',
                    'Preprint servers: bioRxiv, medRxiv, arXiv, Research Square, SSRN',
                    'Target journal mentioned without publication metadata',
                    'Missing ALL publication metadata (no volume/pages/DOI)',
                    'Future dates: "Expected submission: 2025"'
                ],
                'examples': [
                    'Doe JQ. "Spatial transcriptomics in RCC", submitted to Nature Cancer, Jan 2025.',
                    'Patel A. "Automated ECG triage", under review at Circulation, Mar 2024.',
                    'Chen L. "Immune checkpoint pathways", bioRxiv 2025; doi:10.1101/2025.02.18.123456.',
                    'Smith J. "Novel therapeutic approach", in preparation.',
                    'Jones R. "Clinical trial analysis", revise and resubmit at JAMA.'
                ]
            },
            'S8': {
                'title': 'Abstracts & Conference Proceedings',
                'description': 'Conference abstracts without full articles. Indicators: (Suppl), volume:abstract#, abstract journals',
                'examples': [
                    'Kim SY. "AI-driven CT analysis." RSNA Annual Meeting; 2022; Abstract A432.',
                    'Shaikh N. "Food Behavior Survey." FASEB Journal. 2016;30(1 Suppl):33-7.',
                    'Johnson A. "Cardiac biomarkers." Circulation. 2019;140(Suppl 2):A12345.'
                ]
            },
            'N': {
                'title': 'Mentoring (if student advising, NOT bibliography)',
                'examples': [
                    'Ojuri, Victoria, Women\'s Leadership in Global Health (directed study) 2022F',
                    'PhD thesis supervision: Machine Learning Applications 2021-2024'
                ]
            }
        }
    },
    'unknown': {
        'section': 'T - Appendix / Other',
        'description': 'Additional professional information not fitting standard categories',
        'confusion_risk': 'medium',
        'disambiguation': {},
        'related_sections': {
            'T5': {
                'title': 'Other Professional Information - Languages',
                'examples': [
                    'English (Native); French (Fluent); Arabic (Intermediate)',
                    'Spanish (Fluent); Mandarin (Beginning)'
                ]
            }
        }
    }
}


# Parent section configuration for Pass 1
PARENT_SECTIONS = [
    {
        'id': 'contact_information',
        'code': 'A',
        'canonical': 'Personal Data / Contact Information',
        'wcm_section_number': 1,
        'description': 'Personal contact details, identifiers, web presence',
        'passes': 1,
        'direct_to_extraction': True
    },
    {
        'id': 'education_and_training',
        'code': 'B',
        'canonical': 'Education',
        'wcm_section_number': 2,
        'description': 'CV owner\'s formal education (NOT research about education)',
        'passes': 2,
        'child_count': 6,
        'retry_threshold': 0.85
    },
    {
        'id': 'postdoctoral_training',
        'code': 'C',
        'canonical': 'Postdoctoral Training',
        'wcm_section_number': 3,
        'description': 'Postdoc research, residency, fellowship positions',
        'passes': 2,
        'child_count': 4,
        'retry_threshold': 0.85
    },
    {
        'id': 'professional_positions_employment',
        'code': 'D',
        'canonical': 'Professional Positions & Employment',
        'wcm_section_number': 4,
        'description': 'Faculty appointments, employment history',
        'passes': 2,
        'child_count': 4,
        'retry_threshold': 0.85
    },
    {
        'id': 'employment_status',
        'code': 'E',
        'canonical': 'Employment Status',
        'wcm_section_number': 5,
        'description': 'Full-time/part-time status',
        'passes': 1,
        'direct_to_extraction': True
    },
    {
        'id': 'licensure_and_certification',
        'code': 'F',
        'canonical': 'Licensure and Certification',
        'wcm_section_number': 6,
        'description': 'Professional licenses and board certification',
        'passes': 2,
        'child_count': 2,
        'retry_threshold': 0.85
    },
    {
        'id': 'institutional_hospital_affiliation',
        'code': 'G',
        'canonical': 'Institutional / Hospital Affiliation',
        'wcm_section_number': 7,
        'description': 'Hospital staff appointments and privileges',
        'passes': 1,
        'direct_to_extraction': True
    },
    {
        'id': 'honors_and_awards',
        'code': 'H',
        'canonical': 'Honors and Awards',
        'wcm_section_number': 8,
        'description': 'Academic and professional honors',
        'passes': 1,
        'direct_to_extraction': False  # May have subsections
    },
    {
        'id': 'professional_orgs_societies',
        'code': 'I',
        'canonical': 'Professional Organizations and Society Memberships',
        'wcm_section_number': 9,
        'description': 'Professional society memberships (NOT leadership)',
        'passes': 1,
        'direct_to_extraction': False,
        'confusion_note': 'High confusion with Q1 (leadership roles)'
    },
    {
        'id': 'percent_effort_responsibilities',
        'code': 'J',
        'canonical': 'Percent Effort and Institutional Responsibilities',
        'wcm_section_number': 10,
        'description': 'Effort allocation by mission area',
        'passes': 1,
        'direct_to_extraction': True
    },
    {
        'id': 'educational_contributions',
        'code': 'K',
        'canonical': 'Educational Contributions',
        'wcm_section_number': 11,
        'description': 'Teaching and educational activities',
        'passes': 2,
        'child_count': 6,
        'retry_threshold': 0.85,
        'use_sequential_context': True
    },
    {
        'id': 'clinical_practice_innovation_leadership',
        'code': 'L',
        'canonical': 'Clinical Practice, Innovation, and Leadership',
        'wcm_section_number': 12,
        'description': 'Clinical practice, QI, and leadership',
        'passes': 2,
        'child_count': 3,
        'retry_threshold': 0.85
    },
    {
        'id': 'research_overview',
        'code': 'M',
        'canonical': 'Research',
        'wcm_section_number': 13,
        'description': 'Research activities, grants, patents, trials',
        'passes': 2,
        'child_count': 4,
        'retry_threshold': 0.85
    },
    {
        'id': 'mentoring',
        'code': 'N',
        'canonical': 'Mentoring',
        'wcm_section_number': 14,
        'description': 'Mentoring and trainee supervision',
        'passes': 2,
        'child_count': 4,
        'retry_threshold': 0.85,
        'use_sequential_context': True,
        'critical_header_detection': True
    },
    {
        'id': 'institutional_leadership',
        'code': 'O',
        'canonical': 'Institutional Leadership Activities',
        'wcm_section_number': 15,
        'description': 'Executive/oversight roles with formal authority',
        'passes': 2,
        'child_count': 1,  # Just O vs P split
        'retry_threshold': 0.85,
        'routing_rules_critical': True
    },
    {
        'id': 'institutional_administration',
        'code': 'P',
        'canonical': 'Institutional Administrative Activities',
        'wcm_section_number': 16,
        'description': 'Committee service without executive authority',
        'passes': 1,
        'direct_to_extraction': False,
        'confusion_note': 'High confusion with O (leadership)'
    },
    {
        'id': 'extramural_professional_activities',
        'code': 'Q',
        'canonical': 'Extramural Professional Responsibilities',
        'wcm_section_number': 17,
        'description': 'Service/leadership in external organizations',
        'passes': 2,
        'child_count': 5,
        'retry_threshold': 0.85
    },
    {
        'id': 'invitations_to_speak',
        'code': 'R',
        'canonical': 'Invitations to Speak/Present',
        'wcm_section_number': 18,
        'description': 'Invited presentations and speaking engagements',
        'passes': 2,
        'child_count': 3,
        'retry_threshold': 0.85
    },
    {
        'id': 'bibliography',
        'code': 'S',
        'canonical': 'Bibliography',
        'wcm_section_number': 19,
        'description': 'Publications across all types',
        'passes': 2,
        'child_count': 9,
        'retry_threshold': 0.80,
        'use_sequential_context': True,
        'trigger_detection': True
    },
    {
        'id': 'unknown',
        'code': 'T',
        'canonical': 'Appendix / Other',
        'wcm_section_number': 20,
        'description': 'Additional professional information',
        'passes': 1,
        'direct_to_extraction': False
    }
]


def get_parent_section_config(parent_id: str) -> Optional[Dict]:
    """
    Get configuration for a parent section.

    Args:
        parent_id: Either full ID (e.g., 'education_and_training') or code (e.g., 'B')
    """
    for section in PARENT_SECTIONS:
        if section['id'] == parent_id or section['code'] == parent_id:
            return section
    return None


def get_section_context(section_id: str) -> Optional[Dict]:
    """
    Get full context (routing rules, examples) for a section.

    Args:
        section_id: Either full ID (e.g., 'education_and_training') or code (e.g., 'B')
    """
    # Try direct lookup first
    context = WCM_SECTION_CONTEXTS.get(section_id)
    if context:
        return context

    # If not found, try looking up by code
    parent_config = get_parent_section_config(section_id)
    if parent_config:
        return WCM_SECTION_CONTEXTS.get(parent_config['id'])

    return None
