"""Supplementary prose as a tracked-deleted sub-point (#1205). On by default
since the owner's Word review of 2026-10-09; CVICHE_SUPPLEMENTARY_SUBPOINTS=0
turns it off.

An entry's supplementary prose -- a course description, a grant's aims, the
duties under an appointment -- has no field stage 4 extracts, so no section
renderer writes it and it reaches no part of the document. This pass runs
after every section and the #221 recovery have rendered. For each entry of a
code in `SUBPOINT_CODES`, it takes the entry's prose fragments that the
document provably does not contain and writes them under the entry's rendered
line as a Word tracked DELETION: the text came from the faculty member's own
CV and the WCM template has no slot for it, so "proposed removal" says what it
is. Reject the deletion to keep it; accept all changes to get the template.

Where it goes, by what the entry's rendered line is:

- a body paragraph (a teaching entry, a clinical leadership role, a
  publication's citation): a paragraph after it, one list level or indent
  step deeper (a citation, neither a list paragraph nor indented, keeps its
  indent), whose run AND paragraph mark are deleted, so accepting leaves no
  empty bullet;
- a table row (an appointment, a grant table, a service row): a row
  spanning the table, marked as a deleted row, after the entry's row -- or,
  for a label/value table holding one grant or one mentee, at the table's
  end. Accepting removes the row.

Only a fragment `_record_rendered` verifies absent is written, never one
that is merely reformatted, so a section that already prints its entry's
text gets nothing twice. A fragment that opens with a date range is a
record, not prose, and is left to `_recover_unrendered_records`. So is a
fragment that is, holds or is part of a record of the entry's own beside
it, a line that recovery reads with a year the entry's fields do not hold
(`is_sub_record`) and re-emits with sub-points off (`RecordLine.lost`): an
office, a sub-role, a committee or another appointment
(`_left_to_recovery`).

The pass runs BEFORE the content-overflow routing and that recovery. The
overflow pass skips an entry that has a sub-point (a clinical leadership
role, whose overflow replaces its line in the section, has one only when it
and the line hold every part of the overflow, `yields_to_overflow`), and the recovery reads a
sub-point as a record line, of any entry, that is no sub-record and that
its prose is part of (`held_by_subpoint`), so an entry whose prose is now
a sub-point is not also re-emitted whole -- as a blue overflow bullet or
into the Appendix (owner decision 2026-10-08: duty prose goes under the
appointment, not to the Appendix). The recovery reads a sub-point as no sub-record: prose repeating
a dated record's words does not stand for that record.

The duties #1641 splits off an appointment title are written by the
positions writer itself, as a deleted row under the appointment
(`write_row_subpoint`), and recorded in the same lines and entry ids before
this pass runs, so they join its haystack and are not planned again.

The doctor reads the sub-points through one shared test
(`doctor.shared.offered_as_subpoint`): under_extraction and
unrendered_records count the prose as delivered for review, offschema_fields
still reports a value stage 4 misfiled, as INFO with a note. The
render-warnings sidecar records whose each sub-point is
(`SUBPOINT_PLACEMENTS_KEY`), so the doctor counts a sub-point only for the
entry it was written for, not for another entry it shares words with. The
render gate compares arms; its XML fingerprint sees every sub-point as a
change.

Codes: K1-K5 and M2A-M2D first, where loss is highest (#1205 body), plus
D1-D3 duties (owner decision 2026-10-08, lifting the D exclusion for this
variant). Then one code family at a time, each the one a stage-6 replay
measured losing the most prose of those left: Q1-Q2 extramural service next
(the remit, the meeting or the session title behind a committee or panel
row), then H honors (an award's criteria, or the paper it was for), then
N3A-N3B mentees (the mentee's awards and outputs, the owner's part), then
B1 degrees (the thesis or dissertation, a minor, a research topic), then O
institutional leadership (what a role oversaw or achieved), then P
institutional committees (a committee's remit, the member's part on it),
then R invited presentations (the audience, the session and its
co-presenters, what the owner organized), then C postdoctoral training (the
mentor, advisor or program director, the research topic, the duties), then
I professional memberships (the offices and sub-roles held within the
society, what the membership entailed), then L3 clinical leadership (what a
director's, chief's or head's role oversaw, built or achieved), then Q4B-Q4C
editorial board memberships (the section or special issue edited and its
theme, the board's or the publication's scope, a role the row does not
print), then S1-S2 publications (the owner's part in a paper, its findings,
where it was presented, an award or press coverage for it). The decision's
exclusions (A, T, M1, E/G/J/N1/N4/S0/L1/L2, F1, Q4D,
B2) are not in `SUBPOINT_CODES`, so nothing else is touched. A Q2 entry
the service section reroutes into the Journal Reviewing table keeps its Q2
code, so Q4D's exclusion is also applied by where an entry rendered: the
generator passes that table as `excluded_tables`.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from typing import NamedTuple

from docx.document import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.table import Table
from docx.text.paragraph import Paragraph

from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)

from .dedup import _is_sibling_of
from .fan_out import _RENDERED_FIELDS, LAST_STAGE4_RECORD, _segments
from .formatting import (
    PRESENTATION_DATE_FIELDS,
    PRESENTATION_END_DATE_FIELD,
    clear_entry_end,
    is_entry_end,
    mark_entry_end_properties,
    plain_entry_left_twips,
    plain_entry_level,
    presentation_date_spans,
)
from .formatting.dates import _span_years
from .normalization import _cell_text, _pii_fragments, _squash
from .normalization.owner_alias import (
    SURNAME_PARTICLES,
    author_segment,
    is_initials,
    name_parts,
    parse_authors,
)
from .normalization.pii import _BARE_PHONE_SHAPE
from .parsing import _looks_like_multiple_records, _refold_shattered
from .parsing.dates import YEAR
from .parsing.text import (
    _RECORD_DATE_MONTH,
    _RECORD_YEAR,
    DATE_LED_MIN_RECORDS,
    _line_carries_record_date,
    _opens_dated_record,
)
from .pii_pass import PII_WITHHELD_KEY
from .render_check import (
    _OPEN_TERM_WORDS,
    _RECORD_DATE_PREFIX_RE,
    _RENDER_TOKEN_RE,
    _field_strings,
    _norm,
    _record_rendered,
)
from .resolution.institution import FALLBACK_LOCATION_FIELD

#: The switch both drivers read. Unset, it is `SUBPOINT_FLAG_DEFAULT` (on); an
#: off word (`_SUBPOINT_OFF_VALUES`, "0" for ops) renders exactly as before the
#: pass existed.
SUBPOINT_FLAG_ENV = "CVICHE_SUPPLEMENTARY_SUBPOINTS"
#: On: tracked deletion is the default once the spike passed (owner decisions
#: on #1205, 2026-10-02 and 2026-10-09).
SUBPOINT_FLAG_DEFAULT = "1"
#: The values that turn the switch off, read case-blind. "false" and "no"
#: are what an unquoted YAML `false` / `no` reach the reader as.
_SUBPOINT_OFF_VALUES = frozenset({"0", "false", "no", "off"})

TEACHING_CODES = frozenset({'K1', 'K2', 'K3', 'K4', 'K5'})
GRANT_CODES = frozenset({'M2A', 'M2B', 'M2C', 'M2D'})
APPOINTMENT_CODES = frozenset({'D1', 'D2', 'D3'})
#: Extramural service: leadership in an organization (Q1) and service on a
#: board or committee (Q2). Both render as table rows in Q's tables, so a
#: sub-point is a deleted row under the entry's row. Q4D stays excluded, and
#: so does a Q2 entry the section reroutes into the Journal Reviewing table:
#: `plan_subpoints` skips an anchor in an `excluded_tables` table.
SERVICE_CODES = frozenset({'Q1', 'Q2'})
#: Honors and awards: the award's criteria, what it was for, the paper or
#: poster it recognised. The honors section writes every H entry as a row of
#: its three-column table, so a sub-point is a deleted row under the award's
#: row. An entry rerouted to another code before rendering is grouped under
#: that code and follows its rule; one rerouted into H follows this one.
HONOR_CODES = frozenset({'H'})
#: Mentees, current (N3A) and past (N3B): the owner's part in the mentoring,
#: the mentee's programme, awards and outputs. The mentoring section writes
#: each named mentee as a six-row "Label:" table of its own, so a sub-point
#: is a deleted row at the end of that table (`_insert_row_after`), and only
#: that table's rows can anchor it (`_own_mentee_rows`): an entry naming no
#: mentee renders as a line of its whole text and gets none. A line listing
#: several mentees offers each one only its own part (`_own_mentee_line`).
#: The section files a mentee under either heading by its dates, not its
#: code, so both codes are placed over both headings' spans. An entry
#: rerouted to another code before rendering is grouped under that code and
#: follows its rule (N1 is excluded); one rerouted into N3A or N3B follows
#: this one.
MENTEE_CODES = frozenset({'N3A', 'N3B'})
#: Academic degrees: the thesis or dissertation, a minor, a research topic.
#: The degree's cell already prints its field, honors and advisor, so a
#: fragment mostly restating them is not offered (`_DEGREE_CELL_FIELDS`). The
#: education section writes each B1 entry as a row of
#: its four-column table, so a sub-point is a deleted row under the degree's
#: row. Only a row the education writer recorded for that entry can anchor
#: it (`_own_degree_rows`, the row-receipt ledger): two degrees of one
#: institution share every distinctive word of their rows, and an entry the
#: section skips (no degree and no institution) writes no row. That one row
#: is taken without a word test (`_pick_anchor`). An entry naming no degree
#: gets none: its row is an institution or a field alone. The last of
#: stage 4's records offers only its own segment of the line, and a line
#: holding several dated records offers none (`_own_record_line`). An entry
#: rerouted to another code before rendering is grouped under that code and
#: follows its rule (B2 is excluded); one rerouted into B1 follows this one.
EDUCATION_CODES = frozenset({'B1'})
#: Institutional leadership (WCM table 20): what the role oversaw or
#: achieved -- the programmes led, a budget, the staff, an initiative and its
#: outcome, the scope of a directorship. The leadership section writes each O
#: entry as a row of its three-column table (role, the division and
#: institution, dates), so a sub-point is a deleted row under the role's row.
#: The row also prints stage 4's off-schema role and organization when the
#: schema's are empty (`LEADERSHIP_ROLE_FIELDS`). As for a degree, only a row
#: the leadership writer recorded for that entry can anchor it (`_own_rows`,
#: the row-receipt ledger): one title held twice at one institution shares
#: every distinctive word of its rows, as does a later title holding an
#: earlier one's words ("Assistant Director" over "Director"), and the dates
#: that tell them apart are digits. One such row is taken without a word test
#: (`_pick_anchor`), and the several rows the writer splits a flattened table
#: into are read by words. An entry the section wrote no row for (a dedup
#: loser, a table it would not bind to) gets none. The last of stage 4's
#: records offers only its own segment of the line, and a line holding
#: several dated records offers none (`_own_record_line`). An entry rerouted
#: to another code before rendering is grouped under that code and follows
#: its rule (N1 is excluded, and F2 is not offered: its heading lookup finds
#: the title block, not its section); one rerouted into O follows this one.
LEADERSHIP_CODES = frozenset({'O'})
#: Institutional administrative activities and committee service (WCM table
#: 21): what the committee or activity did and what the member did on it --
#: its remit or purpose, the duties and terms, its sub-committees, the
#: constituency represented, the cases heard. The administrative-activities
#: section writes each P entry as a row of its three-column table (the
#: committee with its institution folded in, the role, the dates), so a
#: sub-point is a deleted row under the committee's row. The row prints stage
#: 4's off-schema committee names when the schema's is empty
#: (`ADMINISTRATIVE_ACTIVITY_FIELDS`). Only the one row the writer recorded
#: for that entry can anchor it (`_one_own_row`, the row-receipt
#: ledger): committees of one institution, or held under one title, share
#: their rows' words. An entry holding several records gets none, as which of
#: them a line is about cannot be shown: one the writer prints as several
#: rows (a list of records under a committee name, #208/#248; a block of
#: dated lines it re-parses, #660), and one that one row prints though it
#: holds several (`_own_committee_line`): its parts hold several dated
#: records, a part names another committee, or stage 4 listed several, one
#: naming no committee and so printing no row (#256). The last of stage
#: 4's records offers only its own segment of the line. An entry rerouted to
#: another code before rendering is grouped under that code and follows its
#: rule (N1 is excluded, and F2 is not offered: its heading lookup finds the
#: title block, not its section); one rerouted into P follows this one.
ADMINISTRATIVE_CODES = frozenset({'P'})
#: Invitations to speak and present: what a talk was beyond its fields --
#: its audience (how many, who they were), the session or panel and its
#: co-presenters, a continuing-education or sponsor note, a visit the owner
#: organized. The presentations section writes each R entry as one row of
#: its Regional, National or International table (the title, the venue with
#: the role and meeting folded in, the dates), so a sub-point is a deleted
#: row under the talk's row. The row prints stage 4's off-schema title,
#: venue, meeting and date names when the schema's are empty
#: (`PRESENTATION_TITLE_FIELDS` and the three after it), and a talk with no
#: title is titled from its line (`_untitled_talk_title`), which the document
#: then holds, so it is not offered again. Only the one row the writer
#: recorded for that entry can anchor it (`_own_rows`, the row-receipt
#: ledger, taken with no word test): two talks at one venue, or one title
#: given twice, share their rows' words. An entry holding several talks gets
#: none, as which of them a line is about cannot be shown
#: (`_own_talk_line`): the last of stage 4's records reads only its own
#: segment, and a line holding talks the row does not print offers nothing.
#: One talk given on several dates is one record when its row prints those
#: dates. An entry rerouted to another code before rendering is grouped under
#: that code and follows its rule (an excluded code offers nothing, and F2
#: is not offered: its heading lookup finds the title block, not its
#: section); one rerouted into R follows this one.
PRESENTATION_CODES = frozenset({'R'})
#: Postdoctoral training -- a residency, a fellowship, a postdoctoral
#: position: what the training involved beyond its fields -- the mentor,
#: advisor or program director, the research topic or project, the duties
#: and rotations. The postdoctoral-training section writes each C entry as
#: one row of its three-column table (the training type with its field of
#: study and a role folded in, the institution and its location, the dates),
#: so a sub-point is a deleted row under the position's row. The row prints
#: stage 4's off-schema type, field of study and location when the schema's
#: (and stage 5b's) are empty (`_training_row_values`), which a line mostly
#: restating is not offered for. Only the one row
#: the writer recorded for that entry can anchor it (`_own_rows`, the
#: row-receipt ledger, taken with no word test): two residencies at one
#: hospital, or a residency and the combined residency-and-fellowship row
#: above it, share their rows' words. The last of stage 4's records offers
#: only its own segment of the line, and a line holding several dated
#: records offers none, nor does an entry stage 4 listed positions under,
#: nor a line holding a year outside the span its row prints
#: (`_own_training_line`). The section is found by the
#: heading its writer looks up (the generator's `_SUBPOINT_SECTION_HEADERS`):
#: `_get_wcm_section_header` names none for C. C1-C3 render in the same table
#: and are not offered (no farm entry carries one). A family joining after R
#: yields a fragment a family before it is offered (`_LATER_FAMILIES`). An
#: entry rerouted to another code before rendering is grouped under that
#: code and follows its rule (an excluded code offers nothing, and F2 is not
#: offered: its heading lookup finds the title block, not its section); one
#: rerouted into C follows this one.
POSTDOCTORAL_CODES = frozenset({'C'})
#: Professional organizations and society memberships: what a membership
#: involved beyond its fields -- the offices and sub-roles held within the
#: society (a session chaired, a committee, a working group, a chapter, a
#: delegate's seat) and what the membership entailed. The memberships
#: section writes each I entry as a row of its two-column table (the
#: organization with the membership type prefixed, the dates), so a
#: sub-point is a deleted row under the membership's row. The row prints
#: stage 4's off-schema organization and type names when the schema's
#: organization is empty (`membership_cell_values`), which a line mostly
#: restating is not offered for. Only the one row the writer recorded for
#: that entry can anchor it (`_one_own_row`, the row-receipt ledger, taken
#: with no word test): memberships of one society, or held with one type,
#: share their rows' words, and an entry the writer parses into several
#: memberships, a row each, holds several records. A line holding another
#: membership offers none (`_own_membership_line`): the last of stage 4's
#: records offers only its own segment, and a line holding several dated
#: records, or naming another society, offers nothing, nor does an entry
#: stage 4 listed memberships under. The entry's records the #221 recovery
#: reads stay the recovery's, dated or not (`RECOVERY_RECORD_CODES`): dev
#: re-emits a dated office to the Appendix, and the sub-points must not stop
#: it. The section is
#: found by the heading its writer looks up (the generator's
#: `_SUBPOINT_SECTION_HEADERS`): `_get_wcm_section_header` names none for I.
#: A family joining after C yields a fragment a family before it is offered
#: (`_LATER_FAMILIES`). An entry rerouted to another code before rendering
#: is grouped under that code and follows its rule (an excluded code offers
#: nothing, and F2 is not offered: its heading lookup finds the title block,
#: not its section); one rerouted into I follows this one.
MEMBERSHIP_CODES = frozenset({'I'})
#: Clinical leadership roles -- a director, a chief, a head of a service,
#: clinic, laboratory or programme: what the role oversaw beyond its fields
#: -- its services and units, its staff and volumes, the programmes it built
#: or led, what it achieved. The clinical-practice section writes each L3
#: entry as one line, "Role, Unit, Institution, Dates", without a bullet
#: (the WCM template ships no table under the heading), or as a row of a
#: "Title | Institution/Location | Dates" table a template ships there
#: (`_clinical_header_match`), so a sub-point is a deleted paragraph one
#: indent step under the role's line, or a deleted row under its row. The
#: line prints stage 4's off-schema role and institution names when the
#: schema's are empty (`clinical_leadership_cell_values`), which a line
#: mostly restating is not offered for. Only the line the writer recorded
#: for that entry can anchor it (`_own_lines`, the row-receipt ledger: the
#: comment hook records the line's paragraph, the table writer its row),
#: taken with no word test: roles of one hospital, or held under one title,
#: share their lines' words, and the section's L1 and L2 lines sit beside
#: them. An entry the section wrote no line for (a dedup loser, a structural
#: label) gets none. The last of stage 4's records offers only its own
#: segment of the line, and a line holding several dated records offers none
#: (`_own_record_line`). A role's description is the class of an
#: appointment's duties (owner decision 2026-10-08): a tracked deletion
#: under its line, not an Appendix line. Unlike an appointment's, a
#: low-coverage role's content overflow is not sent to the Appendix: it
#: replaces the role's line, in the section, with the role's whole source
#: text. So a role's sub-point is written in its place only when the
#: sub-point and the line hold every part of that text
#: (`yields_to_overflow`). Otherwise the overflow is written, as with
#: sub-points off, and the role gets no sub-point. A part the planner does
#: not offer (a short one, a label, a date-led line, one restating a field
#: the line does not print) would otherwise be in neither document (#1205
#: review). A fragment opening in lower case is not offered: a sentence's
#: tail whose start is in a part holding the side column's names, which reads
#: as nothing alone (`_is_sentence_tail`; YUYVIG RLADNC 524, a PDF's two
#: columns read across). The section is the one the code's heading lookup names
#: (`_get_wcm_section_header`), the heading the writer looks up. A family
#: joining after I yields a fragment a family before it is offered
#: (`_LATER_FAMILIES`). An entry rerouted to another code before rendering
#: is grouped under that code and follows its rule (an excluded code offers
#: nothing, and F2 is not offered: its heading lookup finds the title block,
#: not its section); one rerouted into L3 (a split hospital record titled as
#: a leadership role, #1731) follows this one.
CLINICAL_LEADERSHIP_CODES = frozenset({'L3'})
#: Editorial board memberships -- a seat as an associate, section or guest
#: editor (Q4B) or on a journal's editorial board (Q4C): what the seat
#: involved beyond its fields -- the section or special issue edited and its
#: theme, the co-editors, the board's or the publication's scope, the
#: member's duties, a role the row does not print. The service section's
#: Other Service writer writes each Q4B and Q4C entry as one row of the
#: template's Editorial Board Membership table, "Board / Organization Name |
#: Dates" (the role, the journal and its organization, and a Q4B section,
#: joined in the first cell), so a sub-point is a deleted row under the
#: seat's row. The row prints the role stage 4 names, or else the one the
#: writer reads from the entry's text (`editorial_board_role`), and the
#: first organization of `OTHER_SERVICE_ORGANIZATION_FIELDS`, off-schema
#: names `_RENDERED_FIELDS` cannot list; a line mostly restating them is not
#: offered. Nor is one whose other words are only the table heading's, a
#: generic seat the row prints no role for ("Editorial Board Member,
#: <journal>, <years>"; `_restates_row`): Reject All would print the row
#: twice and add nothing. A line restating the row with any other role the
#: row does not print is offered as written: the Q4C schema has no role,
#: and that role says what kind of board it was (an advisory, a review
#: board) or the seat's standing (honorary, inaugural, invited), which no
#: cell or heading holds. Only the one row the writer recorded for that
#: entry can anchor it (`_own_rows`, the row-receipt ledger, taken with no
#: word test): two seats on one journal's board, or one title held on
#: several, share their rows' words, and an acronym journal holds too few.
#: An entry the section wrote no row for (a dedup loser, a template naming
#: neither Editorial Board Membership nor Editorial Activities) gets none.
#: The last of stage 4's records offers only its own segment of the line,
#: and a line holding several dated records offers none
#: (`_own_record_line`). No entry of these codes reaches the content
#: overflow: the writer records its row itself, not through the comment
#: hook that collects the overflow. Journal reviewing (Q4D) stays excluded,
#: and so does any row in the table its writer fills, whatever code the
#: entry written there carries (`excluded_tables`). Q4 and Q4A share the
#: section and are not offered. The span is the whole EXTRAMURAL section
#: (`_get_wcm_section_header`'s 'Q' fallback), which holds every row the
#: writer writes. A family joining after L3 yields a fragment a family
#: before it is offered (`_LATER_FAMILIES`). An entry rerouted to another
#: code before rendering is grouped under that code and follows its rule (an
#: excluded code offers nothing, and F2 is not offered: its heading lookup
#: finds the title block, not its section). None is rerouted into Q4B or
#: Q4C: `TAXONOMY_TO_SECTION` names no section for Q4A-Q4D, so no reroute
#: targets one. The Other Service writer reads these codes from here as
#: its editorial board rows' (`service.EDITORIAL_BOARD_CODES`).
EDITORIAL_BOARD_SUBPOINT_CODES = frozenset({'Q4B', 'Q4C'})
#: Peer-reviewed research articles (S1) and reviews and editorials (S2): what
#: a CV says about a paper beyond its citation -- the owner's role in it or
#: contribution to it (designed the study, drafted the paper), a summary
#: of its findings, its significance, where it was presented, an award, a
#: selection or press coverage for it. The bibliography writer writes each S1
#: and S2 entry as one citation paragraph under its subsection label
#: ("Peer-reviewed Research Articles:", "Reviews and Editorials:"), so a
#: sub-point is a deleted paragraph directly under the citation. Only the
#: paragraph the writer recorded for that entry can anchor it (`_own_lines`,
#: the row-receipt ledger: the comment hook records each citation's
#: paragraph), taken with no word test: papers of one group share their
#: authors' and journal's words, and an enriched citation sits inside w:ins,
#: which python-docx's text, and so the word test, cannot read (the
#: 2026-10-21 re-rank: 21 of 64 placements under another paper, mostly an
#: abstract by the same authors). An entry the writer recorded no paragraph
#: for gets none: one it split into several citations (#208), declined as no
#: citation (#446) or dropped as an in-press duplicate, and a dedup loser.
#: Nor does a citation written as a tracked change (`_holds_tracked_change`):
#: an enriched citation deletes the CV's whole line beside PubMed's inserted
#: one, so Reject All already gives the line back, prose and all, and a
#: sub-point would print the prose twice; an insertion with no deletion beside
#: it would leave the prose under the paper before it. Not offered either: a
#: fragment that is the bibliography's own matter (`_is_bibliography_matter`:
#: the entry's author list in another form, a heading of the CV's own
#: bibliography, its author-marker legend), nothing from a line holding
#: several numbered citations (`_own_citation_line`), and nothing from a line
#: one of whose fragments is another paper's citation with no list number
#: (`_cites_another_paper`), which would stand for that paper's record in
#: the doctor's reading of the document. A line restating the citation
#: with a note the citation does not print (where it was presented, the
#: owner's part in it) is offered as written. The last of stage 4's records
#: offers only its own segment of the line, and a line holding several
#: dated records offers none (`_own_record_line`). No entry of these codes
#: reaches the content overflow (`is_overflow_candidate` turns every S code
#: away), so #2013's neither-document gap cannot arise. The span is the whole
#: Bibliography (`_get_wcm_section_header`'s 'S' fallback), which holds every
#: citation the writer writes. S3-S9 share the section and are not offered.
#: A family joining after Q4B/Q4C yields a fragment a family before it is
#: offered (`_LATER_FAMILIES`). An entry rerouted to another code before
#: rendering is grouped under that code and follows its rule (an excluded
#: code offers nothing, and F2 is not offered: its heading lookup finds the
#: title block, not its section); one rerouted into S1 or S2 (an abstract
#: under a peer-reviewed heading) follows this one.
PUBLICATION_SUBPOINT_CODES = frozenset({'S1', 'S2'})
#: The families whose writer records each entry's rows in the row-receipt
#: ledger (for a clinical leadership role written as a line, its line's
#: paragraph; for a publication, its citation's), and whose prose goes only
#: under one of those (`_anchor_scope`).
_OWN_ROW_CODES = (EDUCATION_CODES | LEADERSHIP_CODES | ADMINISTRATIVE_CODES | PRESENTATION_CODES
                  | POSTDOCTORAL_CODES | MEMBERSHIP_CODES | CLINICAL_LEADERSHIP_CODES
                  | EDITORIAL_BOARD_SUBPOINT_CODES | PUBLICATION_SUBPOINT_CODES)
#: The codes whose prose is offered. An allowlist, not the decision's
#: exclusion list: codes join one family at a time, as each is measured.
SUBPOINT_CODES = (TEACHING_CODES | GRANT_CODES | APPOINTMENT_CODES | SERVICE_CODES
                  | HONOR_CODES | MENTEE_CODES | EDUCATION_CODES | LEADERSHIP_CODES
                  | ADMINISTRATIVE_CODES | PRESENTATION_CODES | POSTDOCTORAL_CODES
                  | MEMBERSHIP_CODES | CLINICAL_LEADERSHIP_CODES | EDITORIAL_BOARD_SUBPOINT_CODES
                  | PUBLICATION_SUBPOINT_CODES)
#: The families that joined after R, in the order they joined. Each is
#: offered after every family before it (`_offer_order`): a fragment two
#: entries carry is offered once, to the first, so a family joining later
#: never takes one a family before it is offered. Both a residency (C) and
#: the appointment after it (D1) can hold one duty line, each on its own
#: line (YUYVIG LTTYWI 76 and 80): the appointment keeps it, as before C.
#: Q4B and Q4C sort before R by name, so without this they would take a
#: line a talk shares, and S1 and S2 would be offered before C, I, L3, Q4B
#: and Q4C.
_LATER_FAMILIES = (POSTDOCTORAL_CODES, MEMBERSHIP_CODES, CLINICAL_LEADERSHIP_CODES,
                   EDITORIAL_BOARD_SUBPOINT_CODES, PUBLICATION_SUBPOINT_CODES)
#: The families every record line of which stays the #221 recovery's,
#: dated or not, read as lost or not (`_left_to_recovery`): a membership's
#: offices ("Chair, <name> Committee, <years>"), each a record the recovery
#: re-emits to the Appendix when no row prints it. A fragment that is,
#: holds or is part of one of them is not offered, so Reject All does not
#: print it twice. Every other family leaves the recovery only the records
#: of its own beside the entry's (`is_sub_record`) that the recovery
#: re-emits (`RecordLine.lost`): held back, a fragment merely part of one
#: the recovery reads as rendered would be in neither document, and an
#: undated row, or one dated only by the entry's own years, is the entry's
#: duty or descriptive prose, which stays its sub-point (owner decision
#: 2026-10-08).
#: The recovery reads a sub-point as no sub-record (`held_by_subpoint`), and
#: a membership's prose is part of no record line of its own. So no
#: membership sub-point changes a line of its own the recovery re-emits.
RECOVERY_RECORD_CODES = MEMBERSHIP_CODES

#: A fragment needs this many distinctive tokens (`_RENDER_TOKEN_RE`, 5+
#: letters) to count as prose rather than a date, a label or a name.
SUBPOINT_MIN_TOKENS = 4

#: The anchor must hold at least this share of the entry's rendered field
#: tokens, so a sub-point never lands under an unrelated line.
SUBPOINT_ANCHOR_MIN_OVERLAP = 0.5
#: ... and at least this many of them: an entry whose only rendered token is
#: the owner's surname would otherwise anchor under the Personal Data name.
SUBPOINT_ANCHOR_MIN_TOKENS = 2

#: A fragment whose distinctive tokens are at least this share the entry's own
#: rendered field values is the record line itself, reformatted (a grant's
#: "Agency, Principal Investigator 2005-2007" line, rendered as table rows),
#: not prose: it is not offered.
SUBPOINT_FIELD_TOKEN_MAX_SHARE = 0.5

#: Revision author in Word's review pane, so a reviewer can tell these from
#: the enrichment and formatter changes and from their own edits.
SUBPOINT_AUTHOR = 'CViche: source text with no template field'

#: The render-warnings sidecar's key for the sub-points stage 6 wrote, one
#: `SubpointPlacement` record each. The doctor reads it so that a sub-point
#: vouches only for its own entry (`doctor.shared.offered_as_subpoint`). A
#: sidecar without it, written before the record existed, leaves the doctor
#: matching every sub-point in the document.
SUBPOINT_PLACEMENTS_KEY = 'subpoint_placements'

#: A link, an e-mail address or a phone number -- a contact line fused into a
#: position entry, a webinar's tracking URL (EBYSBC) -- is not prose, and an
#: address or a number is not offered back to the reader in a review pane. A
#: routed code's PII scope withholds no phone number in the middle of a line
#: ("... staff. Home phone: <number>"), so the shape is read here.
_LINK_OR_CONTACT_RE = re.compile(rf'https?://|www\.|mailto:|HYPERLINK|\S@\S+\.\w|{_BARE_PHONE_SHAPE}',
                                 re.IGNORECASE)

#: The entry text's own line and cell separators.
_FRAGMENT_SEPARATORS = ('\t', '\n', '|')

#: A table whose every first cell ends with this is a label/value table (one
#: grant): its sub-point goes at the table's end, not after the anchor row.
_LABEL_SUFFIX = ':'

#: A mentee table's name row label (`mentoring._mentee_table_rows`), and the
#: field keys its value is read from, in `_normalize_mentee`'s order. Two
#: mentees' tables often share every other row's words ("Site/Position: PhD
#: student"), so the name row is what says whose table a row is.
_MENTEE_NAME_LABEL = 'Name:'
_MENTEE_NAME_FIELDS = ('name', 'mentee_name')

#: The fields the education writer prints in a degree's cell that
#: `_RENDERED_FIELDS` cannot list, as its probe marks only schema fields:
#: `major` or `field_of_study`, stage 4's off-schema `honors` (#817), and the
#: advisor under its two off-schema names (#1245, `ADVISOR_LABELS`). A
#: fragment restating them is the degree's row reformatted
#: (`_holds_field_values`). Literals, as `_MENTEE_NAME_FIELDS` are: importing
#: `sections.education` here imports `sections.positions`, which imports this
#: module.
_DEGREE_CELL_FIELDS = ('major', 'field_of_study', 'honors', 'major_professor', 'mentor')
#: The fields the leadership writer reads a role's role cell and its
#: institution cell from, each the first that holds text: the schema's, then
#: stage 4's off-schema names. The writer reads them from here, so the row
#: and `_value_tokens` cannot drift apart.
LEADERSHIP_ROLE_FIELDS = ('leadership_role', 'role', 'position')
LEADERSHIP_INSTITUTION_FIELDS = ('institution', 'organization')
#: The off-schema ones among them, which `_RENDERED_FIELDS` cannot list (its
#: probe marks only schema fields): `_value_tokens` adds them for O.
_LEADERSHIP_ROW_FIELDS = tuple(name for name in (*LEADERSHIP_ROLE_FIELDS,
                                                 *LEADERSHIP_INSTITUTION_FIELDS)
                               if name not in _RENDERED_FIELDS['O'])
#: The fields the administrative-activities writer reads a committee's name
#: cell from, in its one precedence (#625): the schema's, then stage 4's two
#: off-schema names. The writer reads them from here, so the row and
#: `_value_tokens` cannot drift apart. The institution it folds into that
#: cell (#985) is a schema field, which `_RENDERED_FIELDS` lists.
ADMINISTRATIVE_ACTIVITY_FIELDS = ('committee_name', 'committee', 'activity')
#: The off-schema ones among them: `_value_tokens` adds them for P.
_ADMINISTRATIVE_ROW_FIELDS = tuple(name for name in ADMINISTRATIVE_ACTIVITY_FIELDS
                                   if name not in _RENDERED_FIELDS['P'])
#: The kinds of body a record of committee service names, capitalised as a
#: name is ("Admissions Appeals Committee", "Faculty Council"). A part of a
#: committee's line naming another one is another record
#: (`_names_another_committee`), dated or not.
_COMMITTEE_BODIES = ('Committee', 'Subcommittee', 'Council', 'Board', 'Panel', 'Commission',
                     'Senate', 'Task Force', 'Taskforce', 'Working Group', 'Workgroup')
_NAME_WORD = r"[A-Z][\w'’&.-]*"


def _body_name_re(bodies: Iterable[str]) -> re.Pattern[str]:
    """A body's name as a line writes it: the capitalised words before one of
    `bodies`, and those an "on", "of" or "for" adds after it ("Committee on
    Admissions", "Society for Example Studies"). A word that only begins
    with one ("Senator", "Panelist", "Collegiate") names none."""
    return re.compile(rf"(?:{_NAME_WORD}\s+)*(?:{'|'.join(bodies)})\b"
                      rf"(?:\s+(?:on|of|for)\s+(?:the\s+)?{_NAME_WORD}(?:\s+{_NAME_WORD})*)?")


#: A committee's name as a line writes it (`_body_name_re`).
_COMMITTEE_NAME_RE = _body_name_re(_COMMITTEE_BODIES)
#: A committee name more than this share of whose distinctive words are in
#: the entry's own committee name is that committee: one the CV typed with a
#: typo, or wrapped onto the next part ("<words> Steering\tCommittee,
#: <institution>"), still shares most of its words, and another committee
#: little more than the word for its kind ("Committee").
_OWN_COMMITTEE_MIN_SHARE = 0.5
#: The fields the presentations writer reads a talk's row from, each cell's
#: in its one precedence: the Title cell's (the schema's, then stage 4's
#: off-schema name), the venue's (`institution`, then the schema's
#: `location`, then `venue`), the meeting it folds into the venue (R's own,
#: then S8's, for an abstract a hierarchy mismatch moved here, #1570), and
#: the talk's own date (the first that holds one, which an `end_date` makes
#: a range). The writer reads the first three from here, and its Dates cell
#: from `formatting` (`presentation_date_spans`), which defines the date
#: fields, so the row and `_value_tokens` cannot drift apart. Its `role` is a
#: schema field, which `_RENDERED_FIELDS` lists.
PRESENTATION_TITLE_FIELDS = ('title', 'presentation_title')
PRESENTATION_VENUE_FIELDS = ('institution', 'location', 'venue')
PRESENTATION_EVENT_FIELDS = ('event_name', 'conference_name')
#: The off-schema ones among them: `_value_tokens` adds them for R.
_PRESENTATION_ROW_FIELDS = tuple(
    name for name in (*PRESENTATION_TITLE_FIELDS, *PRESENTATION_VENUE_FIELDS,
                      *PRESENTATION_EVENT_FIELDS, *PRESENTATION_DATE_FIELDS,
                      PRESENTATION_END_DATE_FIELD)
    if name not in _RENDERED_FIELDS['R'])
#: The fields the postdoctoral-training writer reads a position's type cell
#: from, each in its one precedence: its type (the schema's, then stage 4's
#: off-schema name, then the code's default) and the field of study it adds
#: (stage 4's off-schema name, then the schema's). The cell prints the first
#: of each that holds a value and no other (`first_field_value`), which
#: the writer reads and `_value_tokens` counts.
POSTDOC_TYPE_FIELDS = ('training_type', 'title')
POSTDOC_FIELD_OF_STUDY_FIELDS = ('field_of_study', 'specialty')
#: The start and the end the Dates cell prints (`format_date_range`). The
#: writer reads them from here, and so does `_dates_another_position`.
POSTDOC_DATE_FIELDS = ('start_date', 'end_date')
#: The other fields the row prints, whatever the rest hold: C's rendered
#: fields outside the two aliased cells (the role it folds into the type
#: cell, the institution, the dates) and the location the institution cell
#: prints when stage 5b found none (`resolution`'s fallback field).
_POSTDOC_PLAIN_FIELDS = (*sorted(_RENDERED_FIELDS['C'] - {*POSTDOC_TYPE_FIELDS,
                                                          *POSTDOC_FIELD_OF_STUDY_FIELDS}),
                         FALLBACK_LOCATION_FIELD)
#: The fields naming a training position, as a record stage 4 listed under
#: one of an entry's fields names it (`_own_training_line`). Dates name none.
_POSTDOC_RECORD_FIELDS = (*POSTDOC_TYPE_FIELDS, *POSTDOC_FIELD_OF_STUDY_FIELDS, 'institution')
#: A year a training position's line holds (`_dates_another_position`): one
#: of this century or the last, standing alone ("2006-2009", "06/2012"), not
#: the digits of a licence or phone number.
_LINE_YEAR_RE = re.compile(rf'\b{_RECORD_YEAR}\b')
#: The fields the memberships writer reads a membership's Organization cell
#: from, each in its one precedence: the organization (the schema's, then
#: stage 4's off-schema names for it) and the membership type it prefixes
#: (the schema's, then stage 4's off-schema names). The writer reads the
#: off-schema names only for a row whose schema organization is empty
#: (`memberships._organization_fallback`), and the cell prints the first of
#: each that holds text (`membership_cell_values`), which the writer reads
#: and `_value_tokens` counts.
MEMBERSHIP_ORGANIZATION_FIELDS = ('organization', 'institution', 'organization_name', 'society')
MEMBERSHIP_TYPE_FIELDS = ('membership_type', 'fellowship_designation', 'role')
#: The other fields the row prints: I's rendered fields outside the
#: Organization cell, the dates.
_MEMBERSHIP_PLAIN_FIELDS = tuple(sorted(_RENDERED_FIELDS['I'] - {*MEMBERSHIP_ORGANIZATION_FIELDS,
                                                                 *MEMBERSHIP_TYPE_FIELDS}))
#: The fields the clinical-practice writer reads a leadership role's line
#: from, each in its one precedence: the role (stage 4's off-schema `role`,
#: then the schema's, then `title`) and the institution (the schema's, then
#: `organization`). The line prints the first of each that holds a value
#: (`clinical_leadership_cell_values`), which the writer reads and
#: `_value_tokens` counts. The description or programme the table form adds
#: is not counted: the line form, the one the WCM template takes, prints
#: neither, so a fragment restating one is the role's prose there, and in
#: the table form the document holds it (`_record_rendered`).
CLINICAL_LEADERSHIP_ROLE_FIELDS = ('role', 'leadership_role', 'title')
CLINICAL_LEADERSHIP_INSTITUTION_FIELDS = ('institution', 'organization')
#: The other fields the line prints: L3's rendered fields outside the role
#: and the institution, the unit it names and the dates.
_CLINICAL_LEADERSHIP_PLAIN_FIELDS = tuple(sorted(
    _RENDERED_FIELDS['L3'] - {*CLINICAL_LEADERSHIP_ROLE_FIELDS, *CLINICAL_LEADERSHIP_INSTITUTION_FIELDS}))
#: The fields the Other Service writer reads a row's organization from, in
#: its one precedence, the first that holds a value: stage 4's off-schema
#: names on an editorial board row, whose schema names only the journal,
#: and Q3's `agency`. The writer reads them from here
#: (`service._journal_or_organization_text`), and `_value_tokens` counts
#: the one it prints for Q4B and Q4C.
OTHER_SERVICE_ORGANIZATION_FIELDS = ('organization', 'committee_name', 'agency')
#: A role an editorial board line writes as a cell of its own, between pipes
#: ("<year> | Associate Editor | <journal>"), which the Other Service writer
#: prints when stage 4 names no role (`editorial_board_role`).
_EDITORIAL_ROLE_CELL_RE = re.compile(
    r'\|\s*((?:Associate|Guest|Deputy|Senior|Managing|Executive|Review|Reviewing)\s*'
    r'(?:Editor|editorial\s*board\s*member)|Editorial\s*(?:Board|board)\s*(?:Member|member)?)\s*\|',
    re.IGNORECASE)
#: The roles that writer reads from anywhere in such a line, in order, when
#: no cell holds one; matched as written, case and all.
_EDITORIAL_LINE_ROLES = ('Associate Editor', 'Guest Editor')
#: The words of the Editorial Board Membership table's heading, which every
#: one of its rows sits under, and the member a seat on that board is. They
#: name a generic seat ("Editorial Board Member", "Member, Editorial
#: Board"), which a row printing no role needs no cell to say: a line whose
#: words beyond the row's are only these restates the row under its heading
#: (`_restates_row`).
_EDITORIAL_BOARD_HEADING_WORDS = frozenset({'editorial', 'board', 'member', 'membership'})
#: The kinds of body a membership is held in, capitalised as a name is
#: ("American Statistical Association", "Society of Example Studies",
#: "Federation of Example Societies", "Union for Example Studies"), each
#: singular and plural. A membership's line naming another one names
#: another membership (`_names_another_society`). A committee, task force,
#: working group, chapter or section is not one -- the society's own, it is
#: the membership's detail -- nor is an institute, a centre or a university.
_SOCIETY_BODIES = ('Society', 'Societies', 'Association', 'Associations', 'College', 'Colleges',
                   'Academy', 'Academies', 'Federation', 'Federations', 'Union', 'Unions')
#: A society's name as a line writes it (`_body_name_re`).
_SOCIETY_NAME_RE = _body_name_re(_SOCIETY_BODIES)
#: The words for a society's kind, which every society of that kind holds:
#: not the name's own words (`_society_name_words`).
_SOCIETY_KIND_WORDS = frozenset(body.casefold() for body in _SOCIETY_BODIES)
#: A society name more than this share of whose own words
#: (`_society_name_words`) are in the membership's organization is that
#: organization, as the line words it ("Example Society of Estuarine
#: Ecologists" for "... Ecology", or with another type than the row's run
#: into it). Another society of the same kind and place shares half of
#: them: "Norvale" of a "Norvale College of Physicians" against a "Norvale
#: College of Cardiology" membership.
_OWN_SOCIETY_MIN_SHARE = 0.5
#: A part of a talk's line split before each date it holds, so a date
#: inside the part can be read as opening a record (`_opens_dated_record`),
#: as one opening the part is: a CV that writes its talks in one paragraph
#: ("2003 <venue>: <title>, <city> 2003 <venue>: <title>") reaches stage 6 as
#: one part, which stage 2's tabs never split.
_BEFORE_A_DATE_RE = re.compile(rf'\s+(?={_RECORD_DATE_MONTH}{_RECORD_YEAR})')
#: A part ending in its year in brackets ("<title>, <venue> (2014)."), which
#: `_line_carries_record_date` does not read: it reads a year that ends the
#: part bare, and a range in brackets anywhere.
_BRACKETED_YEAR_END_RE = re.compile(rf'\(\s*{YEAR}\s*\)[.,;\s]*$')
#: A title as a line quotes it, in curly or straight quotes.
_QUOTED_RE = re.compile(r'“([^”]+)”|"([^"]+)"')
#: A quoted phrase of at least this many distinctive words can be a title;
#: a shorter one is a word or a name the line puts in quotes ("a 'Student
#: Choice' seminar").
_QUOTED_TITLE_MIN_TOKENS = 3
#: A quoted title more than this share of whose distinctive words are the
#: talk's own title or meeting is that title, punctuated or typed
#: differently; another talk's title shares little more than a topic word.
_OWN_TITLE_MIN_SHARE = 0.5
#: A word as a content-overflow part is read against the clinical leadership
#: line it would replace (`_carries_overflow`): any run of letters or
#: digits, short ones included, so a part's count, budget or year ("12
#: nurses", "$4M") is held only by a line that prints it.
_LINE_WORD_RE = re.compile(r'\w+')
#: A publication's fragment naming this many of the entry's own stage-4
#: authors (all of them when it has fewer), every word of it written as a
#: name is (`_is_author_list`), is the entry's author list in another form:
#: the CV's full given names beside the citation's initials (EBYSBC HFAJCC
#: 56, 71, 104, 111 and 126, HZGJFM 690). A line naming them in a sentence,
#: or going on into the title, holds a word in lower case and is offered.
SUBPOINT_AUTHOR_LIST_MIN_NAMES = 2
#: A word of a fragment as `_written_as_names` reads its case: letters, with
#: the apostrophes, periods and hyphens a name or an initial holds.
_NAME_LIST_WORD_RE = re.compile(r"[^\W\d_][\w'’.-]*")
#: The words an author list writes in lower case: the joiners before its
#: last name ("and", "et al.") and a surname's particles ("van", "de la").
_AUTHOR_LIST_LOWER_WORDS = frozenset({'and', 'et', 'al'}) | SURNAME_PARTICLES
#: A publication's fragment whose author list (`author_segment`) names this
#: many people (`parse_authors`) with as many initials, every word of it
#: written as a name is, opens like a citation (`_opens_with_author_list`).
SUBPOINT_CITATION_MIN_AUTHORS = 2
#: A fragment opening like a citation that holds at least this share of the
#: distinctive words of the entry's own title is the entry's citation again
#: (`_cites_another_paper`): the CV's own line, offered as written (#1205
#: comment 6282590626, option 1).
SUBPOINT_OWN_TITLE_MIN_SHARE = 0.5
#: A piece of an author-marker legend: a marker and what it stands for
#: ("† joint first authors", "‡ joint senior authors").
_MARKER_LEGEND_PIECE_RE = re.compile(r'\s*([*†‡§¶#]+)\s*[=:–-]?\s*[^\W\d_]')
#: What separates a legend's pieces.
_MARKER_LEGEND_SEPARATOR_RE = re.compile(r'[;,]')
#: A legend explains at least this many markers (`_is_marker_legend`); one
#: marker and its note can be the paper's own ("*co-first author").
SUBPOINT_LEGEND_MIN_MARKERS = 2
#: The kinds of publication a CV's bibliography heads a list with, plural as
#: a heading names them ("Short Reports, Methods Notes, Letters", "Reviews
#: and Editorials"): a phrase ending in one (`_is_bibliography_heading`).
_PUBLICATION_KINDS = frozenset({'abstracts', 'articles', 'books', 'chapters', 'commentaries',
                                'comments', 'editorials', 'letters', 'manuscripts', 'monographs',
                                'notes', 'papers', 'proceedings', 'publications', 'reports', 'reviews'})
#: A heading's outline marker ("2.", "(b)", "IV.").
_HEADING_MARKER_RE = re.compile(r'^\s*\(?(?:\d{1,2}|[A-Za-z]|[IVXivx]{1,4})[.)]\s+')
#: What separates a heading's phrases.
_HEADING_SEPARATOR_RE = re.compile(r'\s*(?:[,;&/]|\band\b)\s*')
#: A heading's phrase is at most this many words ("Peer-Reviewed Original
#: Research Articles").
_HEADING_PHRASE_MAX_WORDS = 4
#: A part of a publication's line that opens with a list number and an
#: author ("22. Doe JM, ...", "(3) Roe, A. ..."): a numbered citation. Not a
#: numbered list of what the owner did, whose verb an acronym follows and
#: then a word in lower case ("1) Designed MRI protocols"); an author's
#: initials are followed by a comma, a period, the next author or "and" /
#: "et al.".
_NUMBERED_CITATION_RE = re.compile(r"^\s*(?:\(\d{1,3}\)|\[\d{1,3}\]|\d{1,3}[.)])\s+"
                                   r"[^\W\d_][\w'’-]*,?\s+[A-Z]{1,3}\b(?!\s+(?!(?:and|et)\b)[a-z])")
#: A publication's line holding this many numbered citations
#: (`_NUMBERED_CITATION_RE`) holds several papers (`_own_citation_line`).
SUBPOINT_NUMBERED_CITATIONS_MIN = 2
#: The revision elements a paragraph's runs sit in when stage 6 wrote it as
#: a tracked change (`_holds_tracked_change`).
_TRACKED_CHANGE_TAGS = frozenset({qn('w:ins'), qn('w:del')})

_XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'

#: The elements a section writer recorded for an entry, by identity
#: (`RowReceiptLedger.elements_for`): its rows, or its first cells'
#: paragraphs, or a body paragraph (a clinical leadership role's line, a
#: publication's citation). Read for the families that anchor only on those
#: (`_OWN_ROW_CODES`).
RowsWritten = Callable[[Mapping], Iterable[BaseOxmlElement]]


class RecordLine(NamedTuple):
    """A record line the #221 recovery reads in an entry, as the generator's
    recovery verdict reads it before any sub-point is written: `lost` when
    the recovery re-emits it, as it does with sub-points off (no row prints
    it); `sub_record` when it is a record of its own beside the entry's
    (`is_sub_record`)."""
    text: str
    lost: bool
    sub_record: bool


#: The record lines the #221 recovery (`_recover_unrendered_records`) looks
#: for in an entry of a code (`sections.appendix.recovery_candidates`), each
#: with its verdict on the document rendered so far (`RecordLine`). Read for
#: every family: a fragment that is, holds or is part of one the recovery
#: keeps (`_left_to_recovery`) is not offered.
RecordLines = Callable[[str, Mapping], Sequence[RecordLine]]


class AnchorCandidate(NamedTuple):
    """A rendered line a sub-point can go under: a body paragraph, or a table
    row read as one line (its first paragraph stands for the row).
    `position` is its top-level body element's index, for `SectionSpan`."""
    paragraph: Paragraph
    text: str
    tokens: set[str]
    position: int


class SectionSpan(NamedTuple):
    """The body element indexes a code's section runs over: from its heading
    up to, not including, the next major section's heading (`end` None: to
    the Appendix)."""
    start: int
    end: int | None

    def holds(self, candidate: AnchorCandidate) -> bool:
        return candidate.position >= self.start and (self.end is None or candidate.position < self.end)


class SubpointPlacement(NamedTuple):
    """One sub-point stage 6 wrote, as the sidecar records it
    (`SUBPOINT_PLACEMENTS_KEY`, one `_asdict()` per sub-point): the
    `element_idx_start` of the entry it was written for, which is the index
    the doctor reads stage 4's entries by (fan-out children keep it); the
    code the entry rendered as; and the paragraphs as written.

    The index is not an identity: stage-4 entries split from one source
    element share it, and their sub-points then count for each other, as
    fan-out siblings' do. The doctor matches on the index and the text only.
    The code is for a reader of the sidecar, such as a run autopsy: it names
    the table the sub-point sits in, which for a rerouted entry (a K2
    trainee rendered as N3B) is not its stage-4 code."""
    element_idx_start: object
    code: str
    paragraphs: tuple[str, ...]

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> SubpointPlacement:
        """A placement read back from its sidecar record."""
        paragraphs = record.get('paragraphs')
        return cls(record.get('element_idx_start'), str(record.get('code') or ''),
                   tuple(map(str, paragraphs)) if isinstance(paragraphs, (list, tuple)) else ())


class SubPoint(NamedTuple):
    """Prose an entry's render leaves out, and the line it goes under.
    `entry_id` is `id()` of the entry dict, for the overflow pass to skip;
    `code` is the code the entry rendered as and `element_idx_start` its
    index, for its `SubpointPlacement`."""
    anchor: AnchorCandidate
    paragraphs: tuple[str, ...]
    entry_id: int
    code: str
    element_idx_start: object


def subpoints_enabled(value: object) -> bool:
    """`SUBPOINT_FLAG_ENV`'s value read as the switch: off only for an off word
    (`_SUBPOINT_OFF_VALUES`); unset or blank is `SUBPOINT_FLAG_DEFAULT`. A
    config file can hand it an unquoted YAML int or bool, so it is read as
    text first."""
    text = str(value if value is not None else '').strip().lower() or SUBPOINT_FLAG_DEFAULT
    return text not in _SUBPOINT_OFF_VALUES


def _tokens(text: str) -> set[str]:
    return set(_RENDER_TOKEN_RE.findall(_norm(text)))


def _split_fragments(text: str) -> list[str]:
    parts = [str(text or '')]
    for separator in _FRAGMENT_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [' '.join(part.split()) for part in parts if part.strip()]


def prose_fragments(text: str) -> list[str]:
    """The entry text's fragments long enough to be prose, after joining a
    paragraph its source broke at printed lines back into one
    (`_refold_shattered`). Template instructions and source boilerplate are
    never offered."""
    return [fragment for fragment in _refold_shattered(_split_fragments(text))
            if len(_tokens(fragment)) >= SUBPOINT_MIN_TOKENS
            and not _RECORD_DATE_PREFIX_RE.match(fragment)
            and not _LINK_OR_CONTACT_RE.search(fragment)
            and not is_template_instruction(fragment)
            and not is_source_boilerplate(fragment)]


def _row_candidates(table: Table, position: int) -> Iterator[AnchorCandidate]:
    for row in table.rows:
        cells = list(dict.fromkeys(row.cells))  # a merged cell repeats
        paragraphs = [p for cell in cells for p in cell.paragraphs]
        text = ' '.join(p.text for p in paragraphs)
        if text.strip():
            yield AnchorCandidate(paragraphs[0], ' '.join(text.split()), _tokens(text), position)
        for cell in cells:
            for nested in cell.tables:
                yield from _row_candidates(nested, position)


def anchor_candidates(doc: Document, stop_before: BaseOxmlElement | None = None,
                      ) -> Iterator[AnchorCandidate]:
    """Body paragraphs and table rows (nested tables included) in document
    order, up to the body element `stop_before` -- the Appendix heading,
    whose bullets repeat entry text and are no place for a sub-point. A row,
    not a cell: an appointment's title and employer sit in different cells,
    and a cell alone can share more words with another row's employer."""
    for position, element in enumerate(doc.element.body.iterchildren()):
        if element is stop_before:
            return
        if element.tag == qn('w:p'):
            paragraph = Paragraph(element, doc._body)
            if paragraph.text.strip():
                yield AnchorCandidate(paragraph, paragraph.text, _tokens(paragraph.text), position)
        elif element.tag == qn('w:tbl'):
            yield from _row_candidates(Table(element, doc._body), position)


def best_anchor(entry_tokens: set[str],
                candidates: Iterable[AnchorCandidate]) -> AnchorCandidate | None:
    """The first candidate holding the largest share of `entry_tokens`, or
    None when none reaches `SUBPOINT_ANCHOR_MIN_OVERLAP` with at least
    `SUBPOINT_ANCHOR_MIN_TOKENS` of them."""
    if len(entry_tokens) < SUBPOINT_ANCHOR_MIN_TOKENS:
        return None
    best, best_share = None, SUBPOINT_ANCHOR_MIN_OVERLAP
    for candidate in candidates:
        shared = len(entry_tokens & candidate.tokens)
        share = shared / len(entry_tokens)
        if shared >= SUBPOINT_ANCHOR_MIN_TOKENS and (
                share > best_share or (best is None and share >= best_share)):
            best, best_share = candidate, share
    return best


class _Haystack(NamedTuple):
    """The rendered document as `_record_rendered` reads it."""
    text: str
    line_tokens: list[set[str]]


def first_field_value(fields: Mapping, names: Sequence[str]) -> object:
    """The value of the first of `names` that holds one in `fields`, None
    when none does: what a cell or a line prints of a field's aliases -- a
    training row's (`POSTDOC_TYPE_FIELDS`, `POSTDOC_FIELD_OF_STUDY_FIELDS`),
    a clinical leadership role's line's (`clinical_leadership_cell_values`)."""
    return next((fields[name] for name in names if fields.get(name)), None)


def _training_row_values(fields: Mapping) -> list[object]:
    """The values a training position's row prints: of each aliased cell
    only the alias it prints (`first_field_value`), so a fragment restating
    one it does not print is not taken for the row, then the fields it
    prints whatever the rest hold (`_POSTDOC_PLAIN_FIELDS`)."""
    return [*(first_field_value(fields, names)
              for names in (POSTDOC_TYPE_FIELDS, POSTDOC_FIELD_OF_STUDY_FIELDS)),
            *(fields.get(name) for name in _POSTDOC_PLAIN_FIELDS)]


def membership_cell_value(fields: Mapping, names: Sequence[str]) -> str:
    """The first of `names` (`MEMBERSHIP_ORGANIZATION_FIELDS`,
    `MEMBERSHIP_TYPE_FIELDS`) whose value in `fields` holds text, stripped;
    '' when none does."""
    return next((text for name in names if (text := str(fields.get(name) or '').strip())), '')


def membership_cell_values(fields: Mapping) -> tuple[str, str]:
    """(membership type, organization): what a membership row's Organization
    cell prints of stage 4's fields. With the schema's organization, the
    schema's two only; without it, the first of each precedence that holds
    text (`memberships._organization_fallback`, which reads the entry's text
    for what none holds). When that recovery would drop a word of the text,
    the cell prints the whole text instead (`_recovery_covers_text`): a name
    counted here then does not print, and a line restating it is not
    offered, which errs toward offering less."""
    names = (MEMBERSHIP_TYPE_FIELDS, MEMBERSHIP_ORGANIZATION_FIELDS)
    if membership_cell_value(fields, MEMBERSHIP_ORGANIZATION_FIELDS[:1]):
        names = tuple(precedence[:1] for precedence in names)
    membership_type, organization = (membership_cell_value(fields, precedence) for precedence in names)
    return membership_type, organization


def _membership_row_values(fields: Mapping) -> list[object]:
    """The values a membership's row prints: of the Organization cell only
    the names it prints (`membership_cell_values`), so a fragment restating
    one it does not print is not taken for the row, then its dates
    (`_MEMBERSHIP_PLAIN_FIELDS`)."""
    return [*membership_cell_values(fields), *(fields.get(name) for name in _MEMBERSHIP_PLAIN_FIELDS)]


def clinical_leadership_cell_values(fields: Mapping) -> tuple[object, object]:
    """(role, institution): what a clinical leadership role's line prints of
    stage 4's fields, the first of each precedence that holds a value
    (`CLINICAL_LEADERSHIP_ROLE_FIELDS`, `CLINICAL_LEADERSHIP_INSTITUTION_FIELDS`,
    read by `first_field_value`), None when none does."""
    return (first_field_value(fields, CLINICAL_LEADERSHIP_ROLE_FIELDS),
            first_field_value(fields, CLINICAL_LEADERSHIP_INSTITUTION_FIELDS))


def _clinical_leadership_row_values(fields: Mapping) -> list[object]:
    """The values a clinical leadership role's line prints: of the role and
    the institution only the name it prints (`clinical_leadership_cell_values`),
    so a fragment restating one it does not print is not taken for the line,
    then the unit and the dates (`_CLINICAL_LEADERSHIP_PLAIN_FIELDS`)."""
    return [*clinical_leadership_cell_values(fields),
            *(fields.get(name) for name in _CLINICAL_LEADERSHIP_PLAIN_FIELDS)]


def editorial_board_role(fields: Mapping, text: str) -> str:
    """The role an editorial board row (Q4B, Q4C) prints: stage 4's `role`
    (off-schema for Q4C, whose schema has none), else the one its line
    writes in a cell of its own (`_EDITORIAL_ROLE_CELL_RE`), else the first
    of `_EDITORIAL_LINE_ROLES` it holds, else none. The Other Service writer
    reads it from here, and `_value_tokens` counts it."""
    role = _cell_text(fields.get('role', ''))
    if role:
        return role
    cell = _EDITORIAL_ROLE_CELL_RE.search(text)
    if cell:
        return cell.group(1).strip()
    return next((line_role for line_role in _EDITORIAL_LINE_ROLES if line_role in text), '')


def _editorial_board_row_values(fields: Mapping, text: str, names: Iterable[str]) -> list[object]:
    """The values an editorial board row prints: its role
    (`editorial_board_role`), the organization it joins to the journal (the
    first of `OTHER_SERVICE_ORGANIZATION_FIELDS` that holds one), and the
    schema's fields its cells print (`names`: the journal, a Q4B section,
    the dates). A two-column row whose role opens the organization's text
    prints the role alone, so counting the organization there errs toward
    offering less."""
    return [editorial_board_role(fields, text), first_field_value(fields, OTHER_SERVICE_ORGANIZATION_FIELDS),
            *(fields.get(name) for name in names)]


def _value_tokens(entry: Mapping, code: str) -> set[str]:
    """Distinctive tokens of the entry's field values its section renders
    (`_RENDERED_FIELDS`, and for a degree `_DEGREE_CELL_FIELDS`, for a
    leadership role `_LEADERSHIP_ROW_FIELDS`, for a committee
    `_ADMINISTRATIVE_ROW_FIELDS`, for a talk `_PRESENTATION_ROW_FIELDS`; for
    a training position, a membership, a clinical leadership role or an
    editorial board membership, what its row prints, `_training_row_values`,
    `_membership_row_values`, `_clinical_leadership_row_values`,
    `_editorial_board_row_values`), or of every scalar field for a code
    rendered from its text."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return set()
    names = _RENDERED_FIELDS.get(code) or fields.keys()
    if code in EDUCATION_CODES:
        names = (*names, *_DEGREE_CELL_FIELDS)
    elif code in LEADERSHIP_CODES:
        names = (*names, *_LEADERSHIP_ROW_FIELDS)
    elif code in ADMINISTRATIVE_CODES:
        names = (*names, *_ADMINISTRATIVE_ROW_FIELDS)
    elif code in PRESENTATION_CODES:
        names = (*names, *_PRESENTATION_ROW_FIELDS)
    if code in POSTDOCTORAL_CODES:
        values = _training_row_values(fields)
    elif code in MEMBERSHIP_CODES:
        values = _membership_row_values(fields)
    elif code in CLINICAL_LEADERSHIP_CODES:
        values = _clinical_leadership_row_values(fields)
    elif code in EDITORIAL_BOARD_SUBPOINT_CODES:
        values = _editorial_board_row_values(fields, str(entry.get('text') or ''), names)
    else:
        values = [fields.get(name) for name in names]
    return set().union(*(_tokens(str(value)) for value in values
                         if isinstance(value, (str, int, float)) and value))


def _holds_field_values(fragment: str, value_tokens: set[str]) -> bool:
    tokens = _tokens(fragment)
    return len(tokens & value_tokens) >= SUBPOINT_FIELD_TOKEN_MAX_SHARE * len(tokens)


def _restates_row(fragment: str, code: str, value_tokens: set[str]) -> bool:
    """Whether `fragment` is the entry's row reformatted: it mostly holds
    the values the row prints (`_holds_field_values`), or, for an editorial
    board membership, its other words are only its table heading's
    (`_EDITORIAL_BOARD_HEADING_WORDS`), "Editorial Board Member, <journal>,
    <years>" under a row printing the journal and the years. Reject All
    would print the row twice, and the second adds nothing."""
    return _holds_field_values(fragment, value_tokens) or (
        code in EDITORIAL_BOARD_SUBPOINT_CODES
        and _tokens(fragment) - value_tokens <= _EDITORIAL_BOARD_HEADING_WORDS)


def _author_name_parts(entry: Mapping) -> list[set[str]]:
    """Each author in the entry's stage-4 `authors` field, as the folded
    parts of its surname (`parse_authors`, `name_parts`); none when the field
    is not text."""
    fields = entry.get('extracted_fields')
    authors = fields.get('authors') if isinstance(fields, Mapping) else None
    if not isinstance(authors, str):
        return []
    return [parts for author in parse_authors(authors) if (parts := name_parts(author.surname))]


def _written_as_names(text: str) -> bool:
    """Whether every word of `text` is written as a name or an initial is:
    capitalized, or one of `_AUTHOR_LIST_LOWER_WORDS`."""
    return not any(word[0].islower() and word.rstrip('.').casefold() not in _AUTHOR_LIST_LOWER_WORDS
                   for word in _NAME_LIST_WORD_RE.findall(text))


def _is_author_list(fragment: str, author_parts: Sequence[set[str]]) -> bool:
    """Whether a publication's fragment is its author list in another form:
    written as names (`_written_as_names`), the surnames it lists
    (`parse_authors`) naming `SUBPOINT_AUTHOR_LIST_MIN_NAMES` of the entry's
    authors (`author_parts`), or all of them when it has fewer. An author's
    surname elsewhere in a fragment is not its list's: "Young Investigator
    Award, Smith Foundation Annual Meeting" lists no "Young K, Smith J".
    With no authors to name, whose list it is cannot be shown."""
    if not author_parts or not _written_as_names(fragment):
        return False
    named = set().union(*(name_parts(author.surname) for author in parse_authors(fragment)))
    return sum(1 for parts in author_parts if parts & named) >= min(SUBPOINT_AUTHOR_LIST_MIN_NAMES,
                                                                     len(author_parts))


def _opens_with_author_list(fragment: str) -> bool:
    """Whether a publication's fragment opens like a citation: its author
    list (`author_segment`, up to its title or its year) is written as names
    (`_written_as_names`), names `SUBPOINT_CITATION_MIN_AUTHORS` people
    (`parse_authors`) and gives as many initials (`is_initials`), as a
    citation writes its authors ("Fenn CD", "Fenn, C. D.", "C. D. Fenn"). A
    run of capitalized words naming a prize, a meeting or a society
    ("Runner-up, Example Prize, Example Surgical Society, 1999"), or a
    citation's journal and volume on a line of their own, gives none."""
    authors = author_segment(fragment)
    initials = sum(map(is_initials, _NAME_LIST_WORD_RE.findall(authors)))
    return (_written_as_names(authors)
            and min(len(parse_authors(authors)), initials) >= SUBPOINT_CITATION_MIN_AUTHORS)


def _title_tokens(entry: Mapping) -> set[str]:
    """The distinctive words of the entry's stage-4 `title`; none when the
    field is not text."""
    fields = entry.get('extracted_fields')
    title = fields.get('title') if isinstance(fields, Mapping) else None
    return _tokens(title) if isinstance(title, str) else set()


def _cites_another_paper(fragment: str, entry: Mapping) -> bool:
    """Whether a publication's fragment is another paper's citation: it opens
    like one (`_opens_with_author_list`), states a year (`_LINE_YEAR_RE`) and
    holds less than `SUBPOINT_OWN_TITLE_MIN_SHARE` of the words of the
    entry's own title (`_title_tokens`): the CV's next paper joined to the
    entry's line by a tab, a line break or a pipe with no list number, which
    `_own_citation_line` does not see (#1205 review). The entry's own
    citation restated holds its title and is offered as written (#1205
    comment 6282590626, option 1). For an entry with no title, whose
    citation cannot be told from another, one is read as another's:
    offered, another paper's citation would stand for that paper's record.
    Read the same: the first printed line of the entry's own citation, its
    authors and year, offered because it is written unlike the rendered one
    while its title runs on to the next line (EBYSBC XWNZWW 606). Not seen:
    a paper of one author, and another paper written "<authors>: <title>"
    with a word in lower case before its year, whose author list runs on
    into the title."""
    title = _title_tokens(entry)
    return (_opens_with_author_list(fragment) and bool(_LINE_YEAR_RE.search(fragment))
            and (not title or len(_tokens(fragment) & title) < SUBPOINT_OWN_TITLE_MIN_SHARE * len(title)))


def _is_marker_legend(fragment: str) -> bool:
    """Whether a publication's fragment is the legend of the bibliography's
    author markers that stage 2 joined to the entry, "(† joint first
    authors; ‡ joint senior authors)" (YUYVIG SLYBST 159's shape): every
    piece a marker and what it stands for (`_MARKER_LEGEND_PIECE_RE`), at
    least `SUBPOINT_LEGEND_MIN_MARKERS` markers. It is about the whole list,
    not the paper it is joined to."""
    text = fragment.strip().strip('()[]')
    pieces = [piece for piece in _MARKER_LEGEND_SEPARATOR_RE.split(text) if piece.strip()]
    markers = [match.group(1) for piece in pieces if (match := _MARKER_LEGEND_PIECE_RE.match(piece))]
    return len(markers) == len(pieces) and len(set(markers)) >= SUBPOINT_LEGEND_MIN_MARKERS


def _names_publication_kind(phrase: str) -> bool:
    """Whether `phrase` is one a bibliography heading lists: at most
    `_HEADING_PHRASE_MAX_WORDS` capitalized words, the last a kind of
    publication (`_PUBLICATION_KINDS`)."""
    words = phrase.split()
    return (len(words) <= _HEADING_PHRASE_MAX_WORDS and all(word[:1].isupper() for word in words)
            and words[-1].casefold() in _PUBLICATION_KINDS)


def _is_bibliography_heading(fragment: str) -> bool:
    """Whether a publication's fragment is a heading of the CV's own
    bibliography that stage 2 joined to the entry: after any outline marker
    (`_HEADING_MARKER_RE`), phrases each naming a kind of publication
    (`_names_publication_kind`), "3. Short Reports, Methods Notes, Letters"
    (EBYSBC HTNNHG 153's shape). A line saying anything of a paper holds a
    word in lower case, or a phrase that names no kind of publication
    ("Highlighted in Nature Reviews ...")."""
    text = _HEADING_MARKER_RE.sub('', fragment).strip().rstrip(':.').strip()
    phrases = [phrase for phrase in _HEADING_SEPARATOR_RE.split(text) if phrase.strip()]
    return bool(phrases) and all(map(_names_publication_kind, phrases))


def _is_bibliography_matter(fragment: str, author_parts: Sequence[set[str]]) -> bool:
    """Whether a publication's fragment is the bibliography's own matter, not
    the paper's prose: the entry's author list in another form
    (`_is_author_list`), a heading of the CV's bibliography
    (`_is_bibliography_heading`) or its author-marker legend
    (`_is_marker_legend`)."""
    return (_is_author_list(fragment, author_parts) or _is_bibliography_heading(fragment)
            or _is_marker_legend(fragment))


def unrendered_prose(entry: Mapping, code: str, haystack: _Haystack,
                     text: str | None = None) -> list[str]:
    """The prose fragments of `text` (by default the entry's own) that do
    not restate the entry's row (`_restates_row`), that for a publication
    are not the bibliography's own matter (`_is_bibliography_matter`), and
    that the document provably does not contain. A publication offers none
    when one of them is another paper's citation (`_cites_another_paper`):
    offered, it would print as this paper's prose and stand for the other
    paper's record, which the doctor would then not report lost, and which
    paper the line's other prose is about cannot be told."""
    value_tokens = _value_tokens(entry, code)
    publication = code in PUBLICATION_SUBPOINT_CODES
    authors = _author_name_parts(entry) if publication else []
    fragments = [fragment for fragment in prose_fragments(entry.get('text') or '' if text is None else text)
                 if not _restates_row(fragment, code, value_tokens)
                 and not (publication and _is_bibliography_matter(fragment, authors))
                 and _record_rendered(fragment, haystack.text, haystack.line_tokens) is False]
    if publication and any(_cites_another_paper(fragment, entry) for fragment in fragments):
        return []
    return fragments


def _in_tables(anchor: AnchorCandidate, tables: Collection[BaseOxmlElement]) -> bool:
    # `is`, not `in`: the caller holds each table's proxy, so lxml hands back
    # that same object for the ancestor; `id()` of a dropped proxy is not
    # identity.
    return any(ancestor is table for ancestor in anchor.paragraph._p.iterancestors(qn('w:tbl'))
               for table in tables)


def _section_candidates(code: str, candidates: list[AnchorCandidate],
                        spans: Mapping[str, SectionSpan]) -> list[AnchorCandidate]:
    """The candidates in `code`'s section, or for a mentee code in either
    mentee heading's: the section files a mentee by its dates, not its code."""
    codes = MENTEE_CODES if code in MENTEE_CODES else {code}
    held = [spans[c] for c in codes if c in spans]
    return [c for c in candidates if any(span.holds(c) for span in held)]


def _mentee_name(entry: Mapping) -> str:
    """The entry's mentee name (`_MENTEE_NAME_FIELDS`), case-folded and its
    spacing collapsed, as a name row is compared; '' when it names none."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return ''
    name = next((fields[key] for key in _MENTEE_NAME_FIELDS if fields.get(key)), '')
    return ' '.join(str(name).split()).casefold()


def _own_mentee_rows(entry: Mapping, candidates: list[AnchorCandidate]) -> list[AnchorCandidate]:
    """The candidates that are rows of a table whose name row names the
    entry's mentee (`_MENTEE_NAME_LABEL`): none when the entry names no
    mentee or no table names it."""
    name = _mentee_name(entry)
    if not name:
        return []
    name_row = f'{_MENTEE_NAME_LABEL} {name}'.casefold()
    tables = [table for c in candidates if c.text.casefold() == name_row
              for table in c.paragraph._p.iterancestors(qn('w:tbl'))]
    return [c for c in candidates if _in_tables(c, tables)]


def _own_segment(entry: Mapping) -> str | None:
    """The entry's own text, except for the last of stage 4's records
    (`LAST_STAGE4_RECORD`). That one keeps its source line's whole text, the
    other records in it, though each of them renders on its own. It reads its
    own tab segment when the line's segments pair one to one with its records,
    as fan-out pairs the others, and is None when they do not: its family's
    rule decides (`_own_mentee_line`, `_own_record_line`)."""
    text = str(entry.get('text') or '')
    marker = entry.get(LAST_STAGE4_RECORD)
    if not isinstance(marker, Mapping):
        return text
    segments = _segments(text)
    return segments[-1] if len(segments) == marker.get('count') else None


def _own_mentee_line(entry: Mapping, entries_by_code: Mapping[str, list[Mapping]]) -> str:
    """The text a mentee's prose is read from (`_own_segment`). The last of a
    line's records whose segments do not pair with them (YUYVIG XNWSZN 188:
    twelve postdocs on one line, offered under the twelfth) reads the whole
    line when every record names its mentee (one mentee's two periods, YUYVIG
    ECXGAT 1479), and nothing otherwise, as which mentee a word is about
    cannot be told."""
    own = _own_segment(entry)
    if own is not None:
        return own
    others = [other for group in entries_by_code.values() for other in group
              if _is_sibling_of(other, entry)]
    text = str(entry.get('text') or '')
    return text if all(_mentee_name(other) == _mentee_name(entry) for other in others) else ''


def _row_of(element: BaseOxmlElement) -> BaseOxmlElement | None:
    """`element` when it is a table row, else the nearest row holding it."""
    return element if element.tag == qn('w:tr') else next(element.iterancestors(qn('w:tr')), None)


def _names_degree(entry: Mapping) -> bool:
    """Whether stage 4's `degree` for the entry holds any text."""
    fields = entry.get('extracted_fields')
    return isinstance(fields, Mapping) and bool(str(fields.get('degree') or '').strip())


def _own_rows(entry: Mapping, candidates: list[AnchorCandidate],
              rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """The candidates that are a row a writer recorded for `entry` itself
    (`rows_written`, the row-receipt ledger): none when it recorded none, or
    with no ledger. Words cannot tell apart two rows that share every
    distinctive word: two degrees of one institution ("Ph.D." and "M.S."
    hold no distinctive token, nor does a year; YUYVIG FMIGLR 19, an M.S.
    thesis under the Ph.D. row), or two leadership roles of one title and
    institution held in different years. Nor can they see that a section
    skipped an entry, which leaves that entry no row."""
    if rows_written is None:
        return []
    rows = [row for row in map(_row_of, rows_written(entry)) if row is not None]
    return [c for c in candidates if any(_row_of(c.paragraph._p) is row for row in rows)]


def _own_degree_rows(entry: Mapping, candidates: list[AnchorCandidate],
                     rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """A degree's own rows (`_own_rows`): the education section skips an
    entry naming no degree and no institution.

    An entry naming no degree (`_names_degree`) has none either. Its row is
    an institution or a field alone, and its prose is of no degree there: a
    thesis citation stage 4 coded B1, whose master's degree renders as a row
    of its own (EBYSBC VGHNZD 1861)."""
    return _own_rows(entry, candidates, rows_written) if _names_degree(entry) else []


def _holds_dated_records(text: str) -> bool:
    """Whether `DATE_LED_MIN_RECORDS` or more of `text`'s parts each open a
    dated record (`_opens_dated_record`, #983): one source line holding two
    roles or two degrees, each with its own dates, of which stage 4
    extracted one."""
    return sum(map(_opens_dated_record, _split_fragments(text))) >= DATE_LED_MIN_RECORDS


def _own_record_line(entry: Mapping) -> str:
    """The text a degree's or a leadership role's prose is read from
    (`_own_segment`), and nothing for the last of a line's records whose
    segments do not pair with them, or for a line holding several dated
    records (`_holds_dated_records`): which degree a thesis is of, or which
    role a line is about, cannot be told, and the other record's prose would
    go under the entry's row."""
    own = _own_segment(entry)
    return '' if own is None or _holds_dated_records(own) else own


def _committee_records(entry: Mapping) -> list[object]:
    """The records the entry's committee fields hold, as the writer reads
    them: the list stage 4 packed under the first of
    `ADMINISTRATIVE_ACTIVITY_FIELDS` that holds one (#208/#248), else the
    fields themselves, one record."""
    fields = entry.get('extracted_fields')
    fields = fields if isinstance(fields, Mapping) else {}
    listed = next((value for name in ADMINISTRATIVE_ACTIVITY_FIELDS
                   if isinstance(value := fields.get(name), list)), None)
    return listed or [fields]


def _committee_name_tokens(record: object) -> set[str]:
    """The distinctive tokens of the record's committee name, its
    `ADMINISTRATIVE_ACTIVITY_FIELDS` text; none for a record that is not a
    mapping (a bare name in a list)."""
    if not isinstance(record, Mapping):
        return set()
    return _tokens(' '.join(str(record.get(name) or '') for name in ADMINISTRATIVE_ACTIVITY_FIELDS))


def _names_another_committee(part: str, own_name: set[str]) -> bool:
    """Whether a part of a committee's line names another committee
    (`_COMMITTEE_NAME_RE`) than the entry's own (`own_name`,
    `_committee_name_tokens`): one no more than `_OWN_COMMITTEE_MIN_SHARE` of
    whose words are the entry's. A part holding every word of the entry's
    own name is its record, and a sub-committee it names one of its details.
    With no name to hold, whose a named committee is cannot be shown."""
    if own_name and own_name <= _tokens(part):
        return False
    for match in _COMMITTEE_NAME_RE.finditer(part):
        named = _tokens(match.group())
        if len(named & own_name) <= _OWN_COMMITTEE_MIN_SHARE * len(named):
            return True
    return False


def _own_committee_line(entry: Mapping) -> str:
    """The text a committee's prose is read from (`_own_record_line`), and
    nothing when it holds another record than the one the writer prints
    for it, as that record's text would be offered as the committee's prose,
    under its row:

    - its parts hold several dated records by the writer's own #660 test
      (`_looks_like_multiple_records`), read per tab, line and pipe part
      rather than per line. Stage 2 joins a list's paragraphs with tabs, so
      committees each followed by their years reach stage 6 as one line,
      which the writer prints as one row, of the record stage 4 extracted
      (EBYSBC YOXXOH 74: six committees, two of them offered under the
      sixth's row);
    - a part names another committee (`_names_another_committee`), dated or
      not: an undated sibling, or one dated sibling next to a committee
      whose own years are in its fields only, which that test reads as one
      record;
    - stage 4 packed several records in a list (`_committee_records`,
      #208/#248). The writer prints a row for each one naming a committee,
      and `_one_own_row` then sees several, but none for one naming
      none (#256): the entry has one row though it holds two.

    A sibling record in the same part as the committee's own ("<other>,
    2001-2004; <own>, 2008-2012") is not seen."""
    own = _own_record_line(entry)
    records = _committee_records(entry)
    parts = _split_fragments(own)
    if len(records) > 1 or _looks_like_multiple_records(parts):
        return ''
    own_name = _committee_name_tokens(records[0])
    return '' if any(_names_another_committee(part, own_name) for part in parts) else own


def _one_own_row(entry: Mapping, candidates: list[AnchorCandidate],
                 rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """A committee's or a membership's own row (`_own_rows`) when the writer
    recorded exactly one for it. Several are several records the entry held
    -- for a committee, a list of records under its committee name
    (#208/#248) or a block of dated lines the writer re-parsed one row per
    line (#660, `_multiline_committee_rows`); for a membership, the
    memberships the writer parsed out of a line holding several, a row each
    (#476, `_parse_multi_membership_entry`) -- and which of them a prose line
    is about cannot be shown."""
    rows = _own_rows(entry, candidates, rows_written)
    return rows if len(rows) == 1 else []


def _own_lines(entry: Mapping, candidates: list[AnchorCandidate],
               rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """A clinical leadership role's own lines, or a publication's
    (`_own_citation`): the candidates that are a body paragraph its writer
    recorded for `entry` (the comment hook records the first paragraph of
    the role's line, and each citation's paragraph), or a row it recorded (a
    table's, `_own_rows`). None when it recorded neither, or with no
    ledger: roles of one hospital, or held under one title, share their
    lines' words, the section's L1 and L2 lines sit beside them, and a role
    the section skipped (a dedup loser, a structural label) has no line."""
    if rows_written is None:
        return []
    paragraphs = [element for element in rows_written(entry) if _row_of(element) is None]
    rows = _own_rows(entry, candidates, rows_written)
    return [c for c in candidates
            if any(c is row for row in rows)
            or any(c.paragraph._p is paragraph for paragraph in paragraphs)]


def _holds_tracked_change(paragraph: BaseOxmlElement) -> bool:
    """Whether a paragraph's runs sit in a tracked insertion or deletion
    (`_TRACKED_CHANGE_TAGS`): an enriched citation's, the CV's line deleted
    beside PubMed's inserted."""
    return any(child.tag in _TRACKED_CHANGE_TAGS for child in paragraph)


def _own_citation(entry: Mapping, candidates: list[AnchorCandidate],
                  rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """A publication's own citation: the paragraph its writer recorded for
    it (`_own_lines`), unless written as a tracked change
    (`_holds_tracked_change`). Reject All then gives back the CV's whole
    line, which holds every fragment of the entry's text, so a sub-point
    would print its prose twice; and without the deletion, Reject All would
    drop the citation and leave the prose under the paper before it. A
    citation inside w:ins is no candidate in any case: python-docx's text
    reads none of it."""
    return [c for c in _own_lines(entry, candidates, rows_written)
            if not _holds_tracked_change(c.paragraph._p)]


def _own_citation_line(entry: Mapping) -> str:
    """The text a publication's prose is read from (`_own_record_line`), and
    nothing when its parts hold several numbered citations
    (`_NUMBERED_CITATION_RE`): the next paper of the CV's list fused into
    the entry ("21. <authors>. <title> ...\t22. <authors>. <title> ...",
    EBYSBC CYOFWJ 344), whose citation would print as this one's prose,
    under this one's citation. Another paper's citation with no list number
    is read with the fragments the line offers (`unrendered_prose`,
    `_cites_another_paper`). Not seen: one in the same part as the entry's
    own."""
    own = _own_record_line(entry)
    numbered = sum(bool(_NUMBERED_CITATION_RE.match(part)) for part in _split_fragments(own))
    return '' if numbered >= SUBPOINT_NUMBERED_CITATIONS_MIN else own


def _is_sentence_tail(fragment: str) -> bool:
    """Whether a clinical leadership role's fragment opens in lower case:
    the rest of a sentence whose start is in a part that is not offered
    because it holds the line's names. A PDF's two columns read across put
    the role, unit and years into the prose's lines, and the tail alone
    reads as nothing (YUYVIG RLADNC 524)."""
    return fragment[:1].islower()


def _talk_records(line: str) -> int:
    """How many talks the dates of a talk's line mark: its tab, line and pipe
    parts the writer-side #660 test reads as dated
    (`_line_carries_record_date`, a list of talks each ending in its date)
    or that end in a bracketed year (`_BRACKETED_YEAR_END_RE`), or the
    pieces a date opens (`_opens_dated_record`, read before each date of a
    part, `_BEFORE_A_DATE_RE`), whichever is more."""
    parts = _split_fragments(line)
    dated_parts = sum(bool(_line_carries_record_date(part) or _BRACKETED_YEAR_END_RE.search(part))
                      for part in parts)
    opened = sum(_opens_dated_record(piece) for part in parts
                 for piece in _BEFORE_A_DATE_RE.split(part))
    return max(dated_parts, opened)


def _talk_dates(fields: Mapping, code: str) -> int:
    """How many dates the talk's Dates cell prints: those the presentations
    writer joins into it (`presentation_date_spans`), read from the one
    builder, so a date the cell drops (a further span repeating the talk's
    own, a "None" string) is not counted."""
    return len(presentation_date_spans(fields, code))


def _quotes_another_talk(line: str, fields: Mapping) -> bool:
    """Whether the line quotes a title (`_QUOTED_RE`) that is not the talk's
    own: one no more than `_OWN_TITLE_MIN_SHARE` of whose distinctive words
    are in its title or meeting fields. With no title or meeting to hold,
    whose a quoted title is cannot be shown."""
    own = _tokens(' '.join(str(fields.get(name) or '') for name in
                           (*PRESENTATION_TITLE_FIELDS, *PRESENTATION_EVENT_FIELDS)))
    for match in _QUOTED_RE.finditer(line):
        quoted = _tokens(match.group(1) or match.group(2))
        if (len(quoted) >= _QUOTED_TITLE_MIN_TOKENS
                and len(quoted & own) <= _OWN_TITLE_MIN_SHARE * len(quoted)):
            return True
    return False


def _lists_records(fields: Mapping, names: Collection[str]) -> bool:
    """Whether stage 4 listed records under one of the entry's fields: a list
    holding a record that names one of `names`, which fan-out keeps whole
    when the text holds words no record does (#983). A list of date spans
    names none."""
    return any(isinstance(item, Mapping) and any(item.get(name) for name in names)
               for value in fields.values() if isinstance(value, list) for item in value)


def _lists_talks(fields: Mapping) -> bool:
    """Whether stage 4 listed talks under one of the entry's fields
    (`_lists_records`): a record naming a title, a meeting or a venue (R's
    `additional_presentations`). The writer prints the entry's own fields
    only (`_LIST_LOST_WHEN_KEPT_WHOLE`)."""
    return _lists_records(fields, (*PRESENTATION_TITLE_FIELDS, *PRESENTATION_EVENT_FIELDS,
                                   *PRESENTATION_VENUE_FIELDS))


def _own_talk_line(entry: Mapping, code: str) -> str:
    """The text a talk's prose is read from (`_own_segment`: the last of
    stage 4's records reads its own segment, and nothing when the segments
    do not pair with them), and nothing when it holds another talk than the
    one the writer prints for it, as that talk's text would be offered as
    this one's prose, under its row:

    - its dates mark more talks than its row prints dates (`_talk_records`
      against `_talk_dates`): talks each with its own date, joined by tabs or
      written in one paragraph (EBYSBC BMAMWE 1440: three talks of one year,
      offered under the first one's row). One talk given on several dates or
      at several venues is one record when stage 4 kept those dates, as the
      Dates cell then prints each of them, so the parts' dates are read
      against that cell rather than by `_own_record_line`'s count alone;
    - it quotes another title than the talk's own (`_quotes_another_talk`);
    - stage 4 listed several talks under one entry (`_lists_talks`).

    Not seen: a talk the line names with no date and no quotes ("<title A>,
    <venue>. <title B>, <venue>, 2012"), and one whose date neither ends nor
    opens a part: inside it ("<title A> (2014), <venue>"), or in a part
    joined by semicolons ("<title A>, 2014; <title B>, 2016"). Read at its
    semicolons, a talk whose line opens with its year and ends in its full
    date ("2016 <title>; <venue>, May 2016") would read as two."""
    own = _own_segment(entry)
    fields = entry.get('extracted_fields')
    fields = fields if isinstance(fields, Mapping) else {}
    if (own is None
            or _talk_records(own) >= max(DATE_LED_MIN_RECORDS, _talk_dates(fields, code) + 1)
            or _quotes_another_talk(own, fields) or _lists_talks(fields)):
        return ''
    return own


def _dates_another_position(line: str, fields: Mapping) -> bool:
    """Whether `line` holds a year outside the span the position's Dates cell
    prints (`POSTDOC_DATE_FIELDS`, read as `_span_years` reads a span: a
    running one has no last year, one with no end is its start's year, one
    with no start its end's): another position's, wherever its part dates
    it, at its start, its end or inside it -- the residency before the
    fellowship, or the fellowship after it, "<fellowship>, <hospital>,
    2006-2009". A row printing no year shows no span, so any year is
    another's. A year inside the span (a chief residency year) is the
    position's own. Not seen: another position held within the span, or
    named with no year."""
    start, end = (fields.get(name) for name in POSTDOC_DATE_FIELDS)
    span = _span_years(start, end) or _span_years(end, end)
    years = [int(year) for year in _LINE_YEAR_RE.findall(line)]
    if span is None:
        return bool(years)
    first, last = span
    return any(not first <= year <= last for year in years)


def _own_training_line(entry: Mapping) -> str:
    """The text a training position's prose is read from (`_own_record_line`),
    and nothing when it holds another position than the one its row prints,
    as that position's text would be offered as this one's prose, under its
    row:

    - stage 4 listed positions under one of its fields (`_lists_records`,
      `_POSTDOC_RECORD_FIELDS`): the writer prints the entry's own fields
      only;
    - it holds a year outside the span the row prints
      (`_dates_another_position`): that position would print as this
      one's prose, under this one's row, on Reject All. A record line of it
      the #221 recovery re-emits as a record of its own is the recovery's
      in any case (`_left_to_recovery`, #1205 comment 6275741663); this also
      keeps out a line of it the recovery reads as no record, or as
      rendered."""
    fields = entry.get('extracted_fields')
    fields = fields if isinstance(fields, Mapping) else {}
    own = _own_record_line(entry)
    if _lists_records(fields, _POSTDOC_RECORD_FIELDS) or _dates_another_position(own, fields):
        return ''
    return own


def _society_name_words(name: str, own_type: Collection[str]) -> set[str]:
    """A named society's own distinctive words. Not the word for its kind
    (`_SOCIETY_KIND_WORDS`), which every society of that kind holds, nor the
    words a line writes beside a name that `_SOCIETY_NAME_RE` takes in with
    it: an open term's end ("<year>-Present Society of ...",
    `_OPEN_TERM_WORDS`) and the membership's own type (`own_type`, run into
    the name or after a tab: "<Society>\tAssociate Member")."""
    return _tokens(name) - _SOCIETY_KIND_WORDS - _OPEN_TERM_WORDS - set(own_type)


def _names_another_society(line: str, own_name: set[str], own_type: Collection[str] = ()) -> bool:
    """Whether `line` names a society (`_SOCIETY_NAME_RE`) other than the
    membership's organization (`own_name`): one no more than
    `_OWN_SOCIETY_MIN_SHARE` of whose own words (`_society_name_words`, the
    membership's type `own_type` aside) are the organization's. A name with
    no own word ("the Society's meeting") names no other society. Read name
    by name: a sentence listing the entry's own society among three others
    holds every word of its own name and is still four memberships. With no
    organization to hold, whose a named society is cannot be shown."""
    return any(named and len(named & own_name) <= _OWN_SOCIETY_MIN_SHARE * len(named)
               for named in (_society_name_words(match.group(), own_type)
                             for match in _SOCIETY_NAME_RE.finditer(line)))


def _own_membership_line(entry: Mapping) -> str:
    """The text a membership's prose is read from (`_own_record_line`), and
    nothing when it holds another membership than the one its row prints,
    as that membership's text would be offered as this one's prose, under
    its row:

    - it names another society than the row's organization
      (`_names_another_society`): one sentence listing four memberships, a
      chapter's line fused with another college's fellowship, or a line
      interleaving columns that names other colleges;
    - stage 4 listed memberships under one of its fields (`_lists_records`,
      `MEMBERSHIP_ORGANIZATION_FIELDS`): the writer prints the entry's own
      fields only.

    An office, a committee, a working group or a chapter of the same society
    is the membership's own detail. Not seen: another society named without
    one of `_SOCIETY_BODIES`' words (an acronym, a "<name> Foundation", a
    "<name> Guild")."""
    fields = entry.get('extracted_fields')
    fields = fields if isinstance(fields, Mapping) else {}
    own = _own_record_line(entry)
    membership_type, organization = membership_cell_values(fields)
    if (_lists_records(fields, MEMBERSHIP_ORGANIZATION_FIELDS)
            or _names_another_society(own, _tokens(organization), _tokens(membership_type))):
        return ''
    return own


def _held_by_records(fragment: str, records: Collection[str]) -> bool:
    """Whether `fragment` holds one of `records` or is part of one, read
    without spacing or case (`_squash`, as the #221 recovery reads its
    haystack)."""
    squashed = _squash(fragment)
    return any(record and (record in squashed or squashed in record) for record in map(_squash, records))


def _own_years(entry: Mapping) -> set[str]:
    """The years the entry's own fields hold (`_LINE_YEAR_RE` over
    `_field_strings`): the dates its row prints. A list field is left out,
    as `_field_strings` leaves it: the records stage 4 listed there are
    others."""
    fields = entry.get('extracted_fields')
    values = _field_strings(fields if isinstance(fields, Mapping) else {})
    return set(_LINE_YEAR_RE.findall(' '.join(values)))


def is_sub_record(line: str, tab_parts: Collection[str], entry: Mapping) -> bool:
    """Whether a record line the #221 recovery reads in `entry` is a record
    of its own beside the entry's: an office, a sub-role, a committee or
    another appointment, which the recovery re-emits with sub-points off
    when no row prints it. It is dated by the recovery's own reading -- one
    of the tab parts it splits a line into, each a dated record
    (`tab_parts`, `tab_record_segments`), or a line a date opens
    (`_RECORD_DATE_PREFIX_RE`) -- and holds a year the entry's own fields do
    not (`_own_years`).

    Not one: a row the recovery reads by its shape alone (a tab or pipe line
    over 60 characters), whatever year its prose ends on, nor a line dated
    only by the entry's own years: the entry's own line, with its duties or
    description beside its dates (FINSIS 511, SQMWHM 29), which are its
    sub-point (owner decision 2026-10-08). Not seen: another record held in
    exactly the entry's own years."""
    dated = line in tab_parts or bool(_RECORD_DATE_PREFIX_RE.match(line))
    return dated and not set(_LINE_YEAR_RE.findall(line)) <= _own_years(entry)


def _left_to_recovery(fragment: str, code: str, records: Sequence[RecordLine]) -> bool:
    """Whether `fragment` is, holds or is part of one of the entry's record
    lines (`records`, `_held_by_records`) that stays the #221 recovery's: in
    every family a record of its own beside the entry's (`sub_record`) that
    the recovery re-emits (`lost`), and for a membership any
    (`RECOVERY_RECORD_CODES`). Offered, such a line would print twice on
    Reject All. A fragment merely part of a line the recovery reads as
    rendered is offered: held back, it would be in neither document."""
    kept = [record.text for record in records
            if code in RECOVERY_RECORD_CODES or (record.lost and record.sub_record)]
    return _held_by_records(fragment, kept)


def held_by_subpoint(line: str, prose: Collection[str]) -> bool:
    """Whether the #221 recovery reads the record line `line`, which is no
    record of its own (`is_sub_record`), as rendered by a written sub-point:
    `line` holds, or is part of, one of the paragraphs of any sub-point
    written (`prose`) -- the appointment's duties as their own row, or
    beside its own dates, now under the appointment (owner decision
    2026-10-08). The line may be any entry's: read only for the sub-point's
    own entry, another entry's row holding the duties was re-emitted with
    them, and Reject All printed them twice."""
    return _held_by_records(line, prose)


def _prose_text(code: str, entry: Mapping,
                entries_by_code: Mapping[str, list[Mapping]]) -> str | None:
    """The text `code`'s rule reads the entry's prose from; None for its own."""
    if code in MENTEE_CODES:
        return _own_mentee_line(entry, entries_by_code)
    if code in ADMINISTRATIVE_CODES:
        return _own_committee_line(entry)
    if code in PRESENTATION_CODES:
        return _own_talk_line(entry, code)
    if code in POSTDOCTORAL_CODES:
        return _own_training_line(entry)
    if code in MEMBERSHIP_CODES:
        return _own_membership_line(entry)
    if code in PUBLICATION_SUBPOINT_CODES:
        return _own_citation_line(entry)
    if code in _OWN_ROW_CODES:
        return _own_record_line(entry)
    return None


def _anchor_scope(code: str, entry: Mapping, in_section: list[AnchorCandidate],
                  rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """The candidates of the entry's section that `code`'s rule lets it
    anchor under."""
    if code in MENTEE_CODES:
        return _own_mentee_rows(entry, in_section)
    if code in EDUCATION_CODES:
        return _own_degree_rows(entry, in_section, rows_written)
    if code in LEADERSHIP_CODES:
        return _own_rows(entry, in_section, rows_written)
    if code in ADMINISTRATIVE_CODES or code in MEMBERSHIP_CODES:
        return _one_own_row(entry, in_section, rows_written)
    if code in PRESENTATION_CODES or code in POSTDOCTORAL_CODES or code in EDITORIAL_BOARD_SUBPOINT_CODES:
        return _own_rows(entry, in_section, rows_written)
    if code in CLINICAL_LEADERSHIP_CODES:
        return _own_lines(entry, in_section, rows_written)
    if code in PUBLICATION_SUBPOINT_CODES:
        return _own_citation(entry, in_section, rows_written)
    return in_section


def _pick_anchor(code: str, entry_tokens: set[str],
                 scope: list[AnchorCandidate]) -> AnchorCandidate | None:
    """The line of `scope` the entry's prose goes under. A degree's, a
    leadership role's, a committee's, a talk's, a training position's, a
    membership's or an editorial board membership's one own row
    (`_own_rows`), or a clinical leadership role's or a publication's one
    own line (`_own_lines`), is taken as it is: the writer's record already
    says whose it is, and a short row ("PhD", "Kell
    University", "2010"; a "Dean" of an acronym) holds too few distinctive
    words for `best_anchor`, as does a degree's row whose stage-5b
    institution name shares few words with the field. Any other scope is
    read by words."""
    if code in _OWN_ROW_CODES and len(scope) == 1:
        return scope[0]
    return best_anchor(entry_tokens, scope)


def _withholds_prose(entry: Mapping) -> bool:
    """Whether the entry's prose is kept off the page as protected data. The
    PII pass touched the entry (`PII_WITHHELD_KEY`): it cut a fragment of its
    text, or dropped a field named for protected data ("spouse_name") or one
    holding it (#892). What is left can still name the withheld item, which
    is why the honors writer renders no row for an award it emptied, and its
    words could anchor it under another record's line. Or its text holds a
    fragment the full Personal Data / Appendix scope withholds
    (`_pii_fragments`), as `_split_date_led_siblings` reads it: a routed
    code's scope cuts only the rows unambiguous as a label, enough while its
    section prints only fields, but a sub-point prints text no section did,
    as the Appendix does."""
    return bool(entry.get(PII_WITHHELD_KEY)) or bool(_pii_fragments(str(entry.get('text') or '')))


def _offer_order(code: str) -> tuple[int, str]:
    """`plan_subpoints`' order: the codes of the families before
    `_LATER_FAMILIES` by name, then each later family's, in the order they
    joined."""
    rank = next((n for n, family in enumerate(_LATER_FAMILIES, 1) if code in family), 0)
    return rank, code


def plan_subpoints(entries_by_code: Mapping[str, list[Mapping]], haystack: _Haystack,
                   candidates: list[AnchorCandidate],
                   spans: Mapping[str, SectionSpan],
                   excluded_tables: Collection[BaseOxmlElement] = (),
                   rows_written: RowsWritten | None = None,
                   record_lines: RecordLines | None = None) -> list[SubPoint]:
    """One `SubPoint` per entry of a `SUBPOINT_CODES` code that has unrendered
    prose and a rendered line in its own section (`spans`; a code with no
    span is not placed) to hang it under. A fragment is offered once, under
    the first entry that carries it in `_offer_order` (fan-out children and
    dedup survivors share their parent's text). A mentee's prose is read
    from its own line (`_own_mentee_line`), and its line must be a row of
    its own table (`_own_mentee_rows`), under either mentee heading. A degree's or a
    leadership role's prose is read from its own line (`_own_record_line`),
    and its line must be a row its writer recorded for it (`rows_written`,
    `_own_rows`), which it takes with no word test when it is the only one
    (`_pick_anchor`). A committee's is read the same way, and none is
    offered when its line holds another record than the one its row prints
    (`_own_committee_line`) or its writer recorded it several rows
    (`_one_own_row`). A talk's is read the same way, and none is
    offered when its line holds another talk (`_own_talk_line`). A training
    position's is read as a degree's is, and none is offered when stage 4
    listed positions under its fields or its line holds a year outside the
    span its row prints (`_own_training_line`). A membership's is read as a
    committee's is, and none is offered when its line names another society
    or stage 4 listed memberships under its fields (`_own_membership_line`).
    A clinical leadership role's is read as a degree's is, and its line must
    be the paragraph or row its writer recorded for it (`_own_lines`). An
    editorial board membership's is read as a degree's is too. A
    publication's is read as a degree's is, and none is offered when its
    line holds several numbered citations (`_own_citation_line`) or another
    paper's citation (`unrendered_prose`); its line must be the citation its
    writer recorded for it, not written as a tracked change
    (`_own_citation`).
    In every family a fragment that is, holds or is part of one of the
    entry's records the #221 recovery reads (`record_lines`) that is a record
    of its own beside the entry's and that the recovery re-emits, and for a
    membership any of them, is left to that recovery (`_left_to_recovery`).
    `record_lines` is asked only for an entry with a fragment to offer.

    An entry whose line is a row of an `excluded_tables` table gets none: it
    rendered as an excluded code (a Q2 entry rerouted to Journal Reviewing
    still carries Q2), so what rendered decides, not the code. Its prose is
    not offered under any later entry either.

    Nor does an entry holding protected data (`_withholds_prose`)."""
    planned, offered = [], set()
    for code in sorted(entries_by_code, key=_offer_order):
        if code not in SUBPOINT_CODES or code not in spans:
            continue
        in_section = _section_candidates(code, candidates, spans)
        for entry in entries_by_code[code]:
            text = _prose_text(code, entry, entries_by_code)
            fragments = [f for f in unrendered_prose(entry, code, haystack, text)
                         if f not in offered and not (code in CLINICAL_LEADERSHIP_CODES and _is_sentence_tail(f))]
            records = record_lines(code, entry) if fragments and record_lines else ()
            fragments = [f for f in fragments if not _left_to_recovery(f, code, records)]
            if not fragments or _withholds_prose(entry):
                continue
            anchor_tokens = _value_tokens(entry, code) or (
                _tokens(entry.get('text') or '') - set().union(*(_tokens(f) for f in fragments)))
            anchor = _pick_anchor(code, anchor_tokens,
                                  _anchor_scope(code, entry, in_section, rows_written))
            if anchor is None:
                continue
            offered.update(fragments)
            if excluded_tables and _in_tables(anchor, excluded_tables):
                continue
            planned.append(SubPoint(anchor, tuple(fragments), id(entry), code,
                                    entry.get('element_idx_start')))
    return planned


def _carries_overflow(subpoint: SubPoint, text: str | None) -> bool:
    """Whether `subpoint` and the line it goes under hold every part
    (`_split_fragments`) of `text`: each part is a piece of one of the
    sub-point's paragraphs, read without spacing or case (`_squash`), or
    every word of it is one the line prints (`_LINE_WORD_RE`)."""
    line_words = set(_LINE_WORD_RE.findall(subpoint.anchor.text.lower()))
    paragraphs = [_squash(paragraph) for paragraph in subpoint.paragraphs]
    return all(any(_squash(part) in paragraph for paragraph in paragraphs)
               or set(_LINE_WORD_RE.findall(part.lower())) <= line_words
               for part in _split_fragments(text))


def yields_to_overflow(subpoint: SubPoint, overflow_text: str | None) -> bool:
    """Whether `subpoint` is left unwritten for the content overflow: a
    clinical leadership role's that, with the role's line, does not hold
    every part of the text the overflow writes for its entry
    (`overflow_text`, the entry's whole source text; None, nothing to hold,
    when the overflow does not take the entry; `_carries_overflow`). The
    overflow writes that text in the section in place of the role's line and
    skips an entry with a sub-point, so a part the planner does not offer
    would be in neither document (#1205 review). Another family's sub-point
    stands for its entry's overflow as before: a table row's goes to the
    Appendix, which the owner decision of 2026-10-08 replaces with the
    sub-point."""
    return subpoint.code in CLINICAL_LEADERSHIP_CODES and not _carries_overflow(subpoint, overflow_text)


def rendered_haystack(lines: list[str], doc: Document) -> _Haystack:
    """`_Haystack` over the generator's rendered lines, built the way
    `_recover_unrendered_records` builds its own, plus one token set per
    label/value table: a grant renders one fact per row, so a line naming
    the grant's funder, number and role is rendered though no single row
    holds most of its tokens (YUYVIG DYLJXC: a "<funder> award (<number>),
    <role> <years>" line, rendered as its own grant table)."""
    token_sets = [_tokens(line) for line in lines]
    token_sets += [_tokens(' '.join(t.text or '' for t in tbl.iter(qn('w:t'))))
                   for tbl in doc.element.body.iter(qn('w:tbl')) if _is_label_table(tbl)]
    return _Haystack("\x00".join(_squash(line) for line in lines), token_sets)


# --- writing -----------------------------------------------------------------

class Revision:
    """Hands out the w:id, author and date every tracked change carries."""

    def __init__(self, next_id: int, date: str) -> None:
        self.next_id = next_id
        self.date = date

    def element(self, tag: str) -> BaseOxmlElement:
        elem = OxmlElement(tag)
        elem.set(qn('w:id'), str(self.next_id))
        elem.set(qn('w:author'), SUBPOINT_AUTHOR)
        elem.set(qn('w:date'), self.date)
        self.next_id += 1
        return elem


def _deleted_mark_ppr(ppr: BaseOxmlElement | None, revision: Revision) -> BaseOxmlElement:
    """A copy of `ppr` (or a bare w:pPr) whose paragraph mark is a tracked deletion."""
    ppr = copy.deepcopy(ppr) if ppr is not None else OxmlElement('w:pPr')
    for old in ppr.findall(qn('w:rPr')):
        ppr.remove(old)
    mark = OxmlElement('w:rPr')
    mark.append(revision.element('w:del'))
    ppr.append(mark)
    return ppr


def _deleted_paragraph(text: str, revision: Revision,
                       ppr: BaseOxmlElement | None = None) -> BaseOxmlElement:
    """A w:p whose paragraph mark and one run are tracked deletions, so
    accepting it removes the whole paragraph."""
    p = OxmlElement('w:p')
    p.append(_deleted_mark_ppr(ppr, revision))
    deletion = revision.element('w:del')
    run = OxmlElement('w:r')
    rpr = OxmlElement('w:rPr')
    fonts = OxmlElement('w:rFonts')
    fonts.set(qn('w:ascii'), 'Arial')
    fonts.set(qn('w:hAnsi'), 'Arial')
    rpr.append(fonts)
    size = OxmlElement('w:sz')
    size.set(qn('w:val'), '22')  # 11pt, as `_add_track_change_deletion`
    rpr.append(size)
    run.append(rpr)
    del_text = OxmlElement('w:delText')
    del_text.text = text
    del_text.set(_XML_SPACE, 'preserve')
    run.append(del_text)
    deletion.append(run)
    p.append(deletion)
    return p


def _is_subpoint(elem: BaseOxmlElement | None) -> bool:
    """A paragraph or row this module wrote: its mark, row or run is deleted
    by `SUBPOINT_AUTHOR`."""
    if elem is None:
        return False
    if elem.tag == qn('w:p'):
        marks = [elem.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}"), elem.find(qn('w:del'))]
    elif elem.tag == qn('w:tr'):
        marks = [elem.find(f"{qn('w:trPr')}/{qn('w:del')}")]
    else:
        return False
    return any(mark is not None and mark.get(qn('w:author')) == SUBPOINT_AUTHOR
               for mark in marks)


def _after_subpoints(elem: BaseOxmlElement) -> BaseOxmlElement:
    """`elem`, or the last sub-point written straight after it, so several
    sub-points under one anchor keep their order."""
    while _is_subpoint(elem.getnext()):
        elem = elem.getnext()
    return elem


def _has_deleted_mark(p: BaseOxmlElement) -> bool:
    return p.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}") is not None


def _keep_mark(p: BaseOxmlElement) -> None:
    """Undelete `p`'s mark and drop its numbering. Accepting a deleted mark
    merges the paragraph into the next one; with a table or the section
    properties next there is none, so the last sub-point keeps its mark and
    accepting leaves one empty plain paragraph, never an empty bullet."""
    ppr = p.find(qn('w:pPr'))
    for tag in ('w:rPr', 'w:numPr'):
        for old in ppr.findall(qn(tag)):
            ppr.remove(old)


def _deepen_plain_entry(ppr: BaseOxmlElement) -> None:
    """One level deeper for an entry written without a bullet (#1943: K and
    L), which shows its level by a left indent on the bullet-text grid
    rather than `w:ilvl`. Any other indent is left as it is."""
    ind = ppr.find(qn('w:ind'))
    left = None if ind is None else ind.get(qn('w:left'))
    level = plain_entry_level(int(left)) if left is not None and left.isdigit() else None
    if level is not None:
        ind.set(qn('w:left'), str(plain_entry_left_twips(level + 1)))


def _insert_after_paragraph(anchor: Paragraph, texts: tuple[str, ...],
                            revision: Revision) -> None:
    """Deleted paragraphs after `anchor`, with its paragraph properties; a
    list paragraph goes one level deeper, so it reads as a sub-bullet, and so
    does an unbulleted entry (`_deepen_plain_entry`). The last one keeps its
    mark when no paragraph follows it (`_keep_mark`). The gap that ends an
    entry (`mark_entry_end`) moves from the line it is on, the anchor or the
    last sub-point already written, to the new last sub-point, so it still
    falls between this entry and the next and not inside the entry."""
    ppr = copy.deepcopy(anchor._p.pPr) if anchor._p.pPr is not None else None
    if ppr is not None:
        level = ppr.find(f"{qn('w:numPr')}/{qn('w:ilvl')}")
        if level is not None:
            level.set(qn('w:val'), str(int(level.get(qn('w:val'), '0')) + 1))
        else:
            _deepen_plain_entry(ppr)
        clear_entry_end(ppr)
    after = _after_subpoints(anchor._p)
    last_ppr = ppr
    if is_entry_end(after.pPr):
        last_ppr = copy.deepcopy(ppr) if ppr is not None else OxmlElement('w:pPr')
        mark_entry_end_properties(last_ppr)
        clear_entry_end(after.pPr)  # no longer the entry's last line
    if after is not anchor._p and not _has_deleted_mark(after):
        # An earlier sub-point kept its mark; one now follows it, so delete it again.
        after.replace(after.find(qn('w:pPr')), _deleted_mark_ppr(ppr, revision))
    for index, text in enumerate(texts):
        last = index == len(texts) - 1
        p = _deleted_paragraph(text, revision, last_ppr if last else ppr)
        after.addnext(p)
        after = p
    following = after.getnext()
    if following is None or following.tag != qn('w:p'):
        _keep_mark(after)


def _first_cell_text(row: BaseOxmlElement) -> str:
    cell = row.find(qn('w:tc'))
    return '' if cell is None else ''.join(t.text or '' for t in cell.iter(qn('w:t')))


def _is_label_table(tbl: BaseOxmlElement) -> bool:
    """Every row (sub-points aside) opens with a "Label:" cell: one grant."""
    rows = [row for row in tbl.findall(qn('w:tr')) if not _is_subpoint(row)]
    return bool(rows) and all(_first_cell_text(row).strip().endswith(_LABEL_SUFFIX)
                              for row in rows)


def _grid_columns(tbl: BaseOxmlElement) -> int:
    grid = tbl.find(qn('w:tblGrid'))
    return max(1, len(grid.findall(qn('w:gridCol'))) if grid is not None else 1)


def _insert_row_after(row: BaseOxmlElement, texts: tuple[str, ...],
                      revision: Revision) -> None:
    """A deleted row spanning the table after `row`: accepting removes it."""
    tbl = row.getparent()
    if _is_label_table(tbl):
        row = tbl.findall(qn('w:tr'))[-1]
    tr = OxmlElement('w:tr')
    tr_pr = OxmlElement('w:trPr')
    tr_pr.append(revision.element('w:del'))
    tr.append(tr_pr)
    tc = OxmlElement('w:tc')
    tc_pr = OxmlElement('w:tcPr')
    span = OxmlElement('w:gridSpan')
    span.set(qn('w:val'), str(_grid_columns(tbl)))
    tc_pr.append(span)
    tc.append(tc_pr)
    for text in texts:
        tc.append(_deleted_paragraph(text, revision))
    tr.append(tc)
    _after_subpoints(row).addnext(tr)


def write_row_subpoint(row: BaseOxmlElement, texts: tuple[str, ...],
                       revision: Revision) -> str:
    """A deleted row spanning the table after `row`, for a writer that already
    holds its entry's row: the duties #1641 splits off an appointment title.
    Returns the entry as a reader who rejects the deletion sees it: the
    row's cells, then the duties."""
    _insert_row_after(row, texts, revision)
    cells = (''.join(t.text or '' for t in tc.iter(qn('w:t'))) for tc in row.iter(qn('w:tc')))
    return ' '.join((*(' '.join(cell.split()) for cell in cells if cell.strip()), *texts))


def write_subpoint(subpoint: SubPoint, revision: Revision) -> None:
    """Write one `SubPoint` under its anchor (module docstring)."""
    anchor = subpoint.anchor.paragraph
    parent = anchor._p.getparent()
    if parent is not None and parent.tag == qn('w:tc'):
        _insert_row_after(parent.getparent(), subpoint.paragraphs, revision)
    else:
        _insert_after_paragraph(anchor, subpoint.paragraphs, revision)
