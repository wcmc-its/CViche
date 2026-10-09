# Doctor coverage contract

Which kinds of defect the run doctor can see, and how well it sees them held out. Asked for in #1588: the doctor grew one incident detector at a time, and nobody could say which kinds of defect it covers at all. `PRECISION.md`, beside this file, answers "how often is a lint right"; this file answers "what does no lint look at".

## How to read the matrix

- **Rows** are the defect type: missing (content the CV has and the document lacks), invented (content the document has and the CV lacks), wrong value, wrong place, duplicated, malformed (the content is there but broken: a raw line in a cell, prose cut into bullets, page furniture as a record), and privacy (protected or third-party personal data printed).
- **Columns** are the level: section, entry (a stage-2/3b entry: one heading's block, or one line of it), record (one record inside an entry), field (one value inside a record).
- **A covered cell** gives held-out recall as caught / verified findings in that cell, on the YUYVIG v2 labels (`_labels_yuyvig_v2`, 37 runs, 444 findings), and the lints that caught them. The runs are held out for every lint written before 2026-10-08 and for `source_line_coverage`, whose thresholds were fitted on the EBYSBC/s7ab/pilot farm. The doctor PRs merged on 2026-10-08 (`owner_attribution`, `citation_grounding`, `shattered_prose`, and #1607 and #1611's changes to `year_not_in_source`, `span_count` and `dedup_drops`) were written from these runs, so what they add is in-sample (`PRECISION.md`, YUY-HO). "Caught" is the YUY-HO definition in `PRECISION.md`: the YUYVIG verifier credited the dev-259 doctor (yes or partial), or a finding of origin/dev `8e29dd24`'s doctor names the finding's entry (`scripts/doctor_vs_autopsy.py`). The figure in brackets is the same without `source_line_coverage` (#1588), which this contract's PR adds; its gains are index matches and can credit a hit on the right entry for another reason.
- **BLIND** means held-out findings sit in the cell and no lint caught one, or no lint looks at the cell and an autopsy found the shape. Each BLIND cell has one sentence under "Not checked", for the reader of the document.
- **unmeasured** means a lint looks at the cell, but no held-out finding sits in it, so its recall is unknown. The mutation harness (#1588's residual) is what measures these.
- **no class** means no autopsy class maps to the cell and no lint looks at it. It is not a blind spot anyone has seen; a defect of that shape would be one.

The level of a finding was assigned by the issue it cites, one cell per issue (the class map below); a finding marked NEW was read and placed by hand where its report names the shape, otherwise by the stage the verifier blamed (3b: wrong place / entry, 4: wrong value / field, 6: malformed / field, 2 and 1a: malformed / record). A cell is therefore approximate where one issue holds two shapes: #666 holds a few duplicates kept beside its distinct records dropped, and #985 a few header rows rendered as records beside its lost context.

## Matrix

| type | section | entry | record | field |
|---|---|---|---|---|
| missing | unmeasured: `section_lost`, `dead_sections`, `table_lost`, `segmentation` | unmeasured: `unrendered_records`, `classified_unrendered`, `segmentation` | 27 / 52 (22): `dedup_drops`, `identical_rendered_rows`, `multi_record_coverage`, `source_line_coverage`, `duplicate_records`, `group_header_context` | 32 / 80 (29): `junk_or_header_row`, `group_header_context`, `source_line_coverage`, `offschema_fields`, `under_extraction`, `multi_record_coverage` |
| invented | BLIND: 1 / 20, an incidental `python_repr_in_output`; `summary_unsupported_claim` caught none | BLIND | 2 / 6 (2): `owner_attribution` | 7 / 17 (4): `source_line_coverage`, `citation_grounding`, `date_cell_shape` |
| wrong value | no class | no class | unmeasured: `teaching_postcheck` | 29 / 81 (27): `role_consistency`, `span_count`, `year_not_in_source`, `date_cell_shape`, `identical_rendered_rows`, `grant_boundary`, `source_line_coverage` |
| wrong place | 1 / 1 (1): `segmentation_collapse` | 22 / 92 (20): `section_consistency`, `junk_or_header_row`, `stage6_render_warnings`, `source_line_coverage` | 7 / 18 (7): `stage6_render_warnings` (reroute and Appendix shapes), `section_consistency` | 12 / 17 (12): `contact_slot_lost`, `offschema_fields`, `junk_or_header_row` |
| duplicated | no class | no class | 4 / 7 (4): `duplicate_records`, `fanout_cell_residue`, `teaching_postcheck`, `section_consistency` | unmeasured: `fanout_cell_residue` |
| malformed | BLIND: 0 / 2 | no class | 8 / 23 (8): `teaching_postcheck`, `multi_record_coverage`, `shattered_prose` | 3 / 25 (3): `section_lost`, `classified_unrendered`, `identical_rendered_rows` |
| privacy | no class | no class | no class | BLIND: 0 / 3; `protected_data_in_output` caught none |

All cells together: 155 of 444 (35%) with `source_line_coverage`, 140 without; HIGH 25 of 53 (20 without). The 4 unmeasured cells are the ones only the mutation harness can measure.

## Class map

Every autopsy class, by cell. EBYSBC's are `E1`..`E36` (`docs/analysis/AUTOPSY-EBYSBC-batch-2026-10-02.md`), s7ab's `s7ab-1`..`s7ab-21`, YUYVIG's `Y1`..`Y29` (its "Defect classes" table, in rank order), and NDMRSO's new classes `ND1`..`ND15`. The pilot, RCBKFG, X6, OIEPQD and EOAHMI autopsies file their classes under these ids or the issues below. The autopsy documents are local; a class is cited here by id only. E36 (the harness's own input conversion) is not a pipeline defect and has no cell.

| cell | classes | issues the YUYVIG findings cite |
|---|---|---|
| missing / section | none seen | |
| missing / entry | ND5 | |
| missing / record | E1, E4, E6, E27, s7ab-3, s7ab-4, s7ab-4b, s7ab-11, s7ab-17, s7ab-18, Y8, Y10, Y20, Y21, Y28, ND8, ND13 | #1243, #666, #1572, #1435, #1254, #1252 |
| missing / field | E3, E8, E12, E13, E14, E22, E23, s7ab-2, s7ab-5, s7ab-8, s7ab-12, s7ab-16, Y1, Y5, Y18, ND3 | #985, #1205, #817, #530, #1259, #212, #1576 |
| invented / section | Y4 | #1554 |
| invented / entry | s7ab-10 (a note rendered as a pending grant) | |
| invented / record | Y23 | #1573, #1251 |
| invented / field | E26, E33, Y15, Y22 | #1445, #1570 |
| wrong value / record | E20, s7ab-20, ND14 | |
| wrong value / field | E5, E9, E15, E19, E30, E31, E32, s7ab-1, s7ab-9, s7ab-13, s7ab-15, Y2, Y3, Y17, Y29, ND10 | #1403, #1245, #1342, #1257, #1226, #1571, #1575, #1584, #1578 |
| wrong place / section | E17, s7ab-6 | #916 |
| wrong place / entry | E11, s7ab-10, s7ab-14, Y9, Y11, Y12, Y13, Y14, Y24 | #312, #1580, #1577, #1344, #1581, #1415, #534, #1574, #1413 |
| wrong place / record | E7, E18, Y16, Y25 | #1579, #1428, #1439, #1438, #1436 |
| wrong place / field | E25, Y7 | #1222 |
| duplicated / record | E10, E16, E28, s7ab-19, Y19, ND2 | #446, #1433 |
| duplicated / field | E34 | |
| malformed / section | a YUYVIG finding marked NEW (a section's broken sort, HXBPCT-16) | #1582 |
| malformed / record | E24, E29, s7ab-7, Y26, ND7, ND12 | #1345, #1551, #1414, #1583, #986, #983, #1187 |
| malformed / field | E21, E35, Y6 | #1346, #829 |
| privacy / field | E2, s7ab-21, Y27, ND1 | #847 |

How to re-measure: score both arms with `scripts/doctor_vs_autopsy.py --json` over the YUYVIG farm (`PRECISION.md`, "How to re-measure"), then place each label finding in its cell by the issue map above and count a finding caught as defined under "How to read the matrix". The cell script used for this table was a one-off (local, beside the labels' converters); a PR that moves a cell re-runs it and updates the cell.

## Not checked

What the document's reader is told the doctor did not check, one sentence per BLIND cell. `doctor/blind_spots.py` reads these bullets for the review copy's "not checked" box (#1589); keep the shape "- `<type> / <level>`: <sentence>." and add or remove a bullet in the PR that changes a cell.

- `invented / section`: The doctor does not check whether the research summary's claims are supported by your CV.
- `invented / entry`: The doctor cannot see an entry CViche made up from a note or comment in your CV.
- `malformed / section`: The doctor does not check the order of entries within a section.
- `privacy / field`: The doctor cannot reliably see other people's personal details, such as a family member's name, printed in the document.
