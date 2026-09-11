# `stage6/sections/` — a map of the package

One module per WCM CV template section (#398). Each module holds that section's
`_fill_*` writer plus only the helpers that section calls, as a mixin class that
`WCMTemplateGenerator` inherits from. The rationale for one-module-per-section
and for mixins-not-composition is in `__init__.py`; this file is the index.

## The modules

| Module | WCM section | Renders | Writers |
| --- | --- | --- | --- |
| `personal_data.py` | A | Name, addresses, phones, emails | `_fill_personal_data` |
| `education.py` | B1 | Conferred academic degrees | `_fill_education`, `_degree_is_in_progress` |
| `other_education.py` | B2 | Non-degree training, certificates, workshops | `_fill_other_education` |
| `postdoc_training.py` | C, C1, C2, C3 | Postdoctoral training, residency, fellowship | `_fill_postdoc_training` |
| `positions.py` | D1–D3 | Academic, hospital and other appointments | `_fill_positions`, `_merge_grouped_appointments`, `_add_position_row`, … |
| `passthrough.py` | E, G and J | Employment status; institutional/hospital affiliation; percent effort | `_fill_passthrough_sections`, `_fill_employment_status`, `_fill_hospital_affiliation`, `_fill_percent_effort` |
| `licensure.py` | F1 | State licences, plus the DEA and NPI numbers | `_fill_licensure`, `_fill_dea_npi` |
| `board_certification.py` | F2 | Specialty board certifications | `_fill_board_certification`, `_parse_and_add_multiple_certifications`, `_add_board_cert_row` |
| `honors.py` | H | Honors and awards | `_fill_honors`, `_split_award_year`, `_extract_organization_from_award`, `_add_honors_row` |
| `memberships.py` | I | Professional organizations and society memberships | `_fill_memberships`, `_add_table_row` |
| `teaching.py` | K1–K5 | Teaching activities: didactic, clinical, administrative, CME, outreach | `_fill_teaching`, `_insert_teaching_entry` |
| `clinical_practice.py` | L1–L3 | Clinical practice, innovation, clinical leadership | `_fill_clinical_practice`, `_insert_multiline_as_bullets` |
| `research_summary.py` | M1 | The stage-4.5 synthesized research summary | `_fill_research_summary` |
| `research_support.py` | M2A–M2C | Grants: current, completed, pending | `_fill_research_support`, `_create_grant_table`, `_format_grant_duration` |
| `patents.py` | M2D | Patents and inventions | `_fill_patents` |
| `mentoring.py` | N3A, N3B, N4 | Current and past mentees, mentoring outcomes | `_fill_mentoring`, `_create_mentee_table`, `_insert_mentoring_summaries`, … |
| `leadership.py` | O | Institutional leadership activities | `_fill_leadership`, `_add_leadership_row`, `_add_multiline_leadership_rows` |
| `administrative_activities.py` | P | Institutional administrative activities and committees | `_fill_administrative_activities`, `_add_committee_row`, `_add_multiline_committee_rows` |
| `service.py` | Q1–Q4D | Extramural professional responsibilities, boards, journal reviewing | `_fill_service`, `_fill_service_boards`, `_fill_extramural_leadership`, `_fill_journal_reviewing`, … |
| `presentations.py` | R | Invitations to speak: regional, national, international | `_fill_presentations` |
| `researcher_profiles.py` | S0 | ORCID and other researcher profile identifiers | `_fill_researcher_profiles` |
| `bibliography.py` | S1–S9 | Publications | `_fill_bibliography`, `_add_citation_with_bold_author`, `_add_citation_with_bold_author_as_insertion` |
| `appendix.py` | T | Content that reached no other section | `_fill_appendix` |

`passthrough.py` selects its input by source hierarchy rather than by taxonomy
code — E, G and J have no code of their own on the live path. J (Percent
Effort) joined E and G at #260: it writes into the template's fixed
Teaching/Clinical/Administrative/Research/Total rows, matched by label, never
adding or clearing rows. See `passthrough.py`'s module docstring for the
parse/match rules and the real-run traps (a source column-header row, and
`Total | 100% |`) its parser has to survive.

## What is NOT here

A helper reached from two or more sections is not exclusive to either, so it
stays on `WCMTemplateGenerator` in `stage_6_word_template.py` — that is the
shared rendering machinery, not section logic. The widely shared ones are
`_find_paragraph_with_text`, `_find_table_after_paragraph`,
`_add_entry_comments`, `_insert_bulleted_entry`,
`_add_table_row_with_mixed_content`, `_add_word_comment`,
`_classify_geographic_scope`, `_add_spacing_paragraph`,
`_find_table_with_cell_text` and `_find_paragraph_exact`.

Also staying behind: the `generate` driver and the whole-document passes it runs
after the sections have written (`_route_overflow_entries`,
`_reconsider_appendix_entries`, `_recover_unrendered_records`,
`_validate_output`, `_finalize_comments`). Those operate across sections by
definition and belong to no one section.

Format-level primitives live one level up, in `stage6/formatting/`,
`stage6/normalization/`, `stage6/parsing/`, `stage6/resolution/` and
`stage6/sorting/`.

## Rules when adding or editing a section

Nothing under this package may import `stage_6_word_template`. That module
imports this one, so a back-edge is an import cycle and fails at load rather
than at render. Every name a section writer needs must be importable from
`unified_pipeline.stage6.*`, from `unified_pipeline.core.*`, or from the
standard library.

Section classes are re-exported from `stage_6_word_template` with
`# noqa: F401`. `run_full_pipeline.py` and the backend
`app/pipeline/orchestrator.py` are parallel drivers sharing no code, so a name
that moves without a re-export lands silently in whichever driver nobody ran.

`tests/test_stage6_import_surface.py` pins both surfaces — 21 module symbols and
17 methods that the suite calls on an instance. It uses `hasattr`, so a method
on a mixin satisfies it through the MRO. If it fires, the fix is a re-export or
a delegating method, never a smaller pin list.
