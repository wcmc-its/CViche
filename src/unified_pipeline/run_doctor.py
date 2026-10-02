"""Cross-stage run doctor: offline lints over one run's stage artifacts.

Each lint encodes an observed production failure class (run 89HQVQ lost 7 of
8 grants across several of them at once). The doctor reads the artifacts a
run leaves in the standard outputs layout (stage_*/<uid>*_*.json plus the
source and stage-6 WCM docx), applies pure structural checks — no LLM calls,
no network — and reports findings ranked ERROR/WARN/INFO.

Lints, ranked by the severity of the failure class they catch:

1. segmentation           coverage / lost lines / mega-entries / dups via the
                          segmentation_regression metrics (8 grants fused
                          into one table-cell entry); its sibling
                          `table_lost` scopes the same coverage to each
                          source table (web207's lost personal-data table)
2. missed_headers         ALL-CAPS bold header-like source lines absent from
                          the 1a hierarchy AND every entry hierarchy path
                          ('PROFESSIONAL EXPERIENCE' demoted to content)
3. bucket_status          grant status vs the funding subsection the grant
                          actually rendered under in the stage-6 document
                          (the #214 rebucketing rules)
4. under_extraction       big multi-record entries with very low stage-4
                          extraction coverage (14.9%-coverage mega-entry)
5. classified_unrendered  3b taxonomy codes none of whose entries surface in
                          the stage-6 output document (text or tables)
5a. stage3b_fallback_ratio a hard-fail gate (see the bottom of this list): a
                          large share of stage 3b's classification batches
                          failed, or entries fell back to a default code --
                          recorded as NUMBERS in meta.stats, invisible to
                          pipeline_errors_present's error-string scan (#810:
                          a partial Bedrock outage defaulted 510 of 1019
                          entries and scored 91 GREEN, doctor WARN only)
6. output_hygiene         bracketed taxonomy-code leaks ('• [M2A]'), appendix
                          size (moved to the `metrics` block, #816),
                          boilerplate rendered in the appendix
7. dead_sections          substantive source sections whose name-matched WCM
                          output section is empty
8. unrendered_records     record lines of a fused multi-record stage-4 entry
                          definitively absent from the stage-6 output — the
                          structured-fields-only render paths drop the
                          unextracted remainder with no bullet fallback (#221)
9. enrichment_failures    stage-5 PubMed enrichment lookups that failed, so
                          those citations degrade to CV-extracted fields (#222)
10. stage6_render_warnings stage 6's own post-generation self-check findings,
                          re-emitted from the render-warnings sidecar — they
                          used to die in the pod log (#228)
11. dedup_drops           stage-6 dedup decisions whose dropped text is not
                          near-fully contained in the kept entry — at loose
                          thresholds these are distinct records lost, not
                          duplicates (#227: 7 of 8 drops on 2Q1_ZQ were real)
12. pipe_leaks            raw ' | '-delimited source lines rendered as output
                          paragraphs — verbatim-fallback formatting reaching
                          the faculty-facing document (#208 costs)
13. table_shape           honors-table rows that are mis-shaped: citation
                          blobs in the name cell, empty date column with a
                          year in the name, state-abbrev organizations,
                          organization duplicated inside the name (#229) --
                          the malformed-row count moved to the `metrics`
                          block (#816); the finding itself stays INFO
14. duplicate_passages    stretches of 3+ CONSECUTIVE rendered blocks that
                          appear twice in the output document — one record
                          reaching the faculty-facing docx more than once
                          (#439: C0ZGFW rendered whole teaching records twice)
14a. duplicate_records     a single enumerated paragraph block whose
                          normalized body repeats at a different list
                          position within the same output section — the
                          ONE-block shape duplicate_passages cannot see by
                          construction (#446)
14b. invented_records     a rendered stage-4 record built entirely from the
                          WCM template's own field labels rather than real
                          content (the F2 board-certification header row
                          rendered as a certification), plus an F1 entry
                          whose source text is a known template instruction
                          rather than a real licence — the failure class
                          #959 fixed one instance of, generalized to every
                          taxonomy code (A5IZ6Q, #829)
14c. wrong_start_date     a stage-4 entry whose schema declares both dates,
                          `end_date` empty, and whose text carries exactly one
                          closed year range -- it renders "<start>-Present"
                          (FSMB "2025-2026" extracted as start_date=2026);
                          report-only, the value is not repaired (#729)

14d. date_only_lines      body paragraphs (outside the Appendix, never table
                          cells) whose whole text is a date -- a record's date
                          column split from its payload and rendered as its
                          own bullet (#259: ZXVGAC, 28 under EDUCATIONAL
                          CONTRIBUTIONS); WARN at a corpus-derived count

14e. stage3b_second_pass_error a stage-3b second pass (t_validation,
                          fragment_reconnection) recorded `error` in
                          meta.stats: it failed and left its entries
                          unchanged; WARN, the run completes (#818)

14f. offschema_fields     a stage-4 value under a key that is in neither
                          field schema, not rendered for its code, not
                          stage-4 bookkeeping, and not a record list fan-out
                          splits -- no renderer reads it, so it never reaches
                          the document (TXTATQ's `organization_2`: 6
                          memberships); WARN for a whole record, INFO for one
                          fact (#817)

14g. implausible_year     a stage-4 date-named field whose year is below
                          1930 (or 10 years before the owner's earliest
                          degree) and that the entry's text never writes --
                          a two-digit year given the wrong century (YOXXOH's
                          2002-2004 talks rendered as 1902-1904); WARN

Lints 14-17 (plus 5a, stage3b_fallback_ratio, above) are the quality-score
HARD-FAIL gates and sit outside that ranking: they are the only ERROR-by-
construction lints, because each one on its own caps quality_score.py's final
score into the RED do-not-deliver band.
Without them an undeliverable run reported worst=WARN like every healthy one
(#437). Each calls quality_score.py's own predicate over the same artifacts
the scorer reads, so the doctor reports the gate rather than a second
definition of it -- what that buys is that the two cannot drift apart, NOT
independent confirmation that the gate itself is calibrated:

14. owner_contact_missing the stage-4 'cv_owner' block carries no usable name,
                          or a run that produced output has no stage-4
                          artifact at all — either way the score is capped at
                          25. The one lint that does not skip on a missing
                          artifact, because the gate trips on that too.
15. pipeline_errors_present a fatal error (NameError, traceback) recorded in an
                          'error' field of stage_2/stage_3b/stage_4 — the JSON
                          the deployed scorer globs — capped at 40
16. no_output             a run that reached stage 4 but produced NEITHER a
                          stage-6 docx nor its render-warnings report at all
                          — nothing to deliver — capped at 20 (#745: web204
                          scored 88 GREEN with this reported only as an INFO
                          'skipped: missing stage_6_docx')
17. protected_data_in_output a date of birth, SSN, or other protected-data
                          label/value shape reaches the rendered docx's
                          paragraphs or table cells — the last line of
                          defence behind the pre-render detector and deny
                          pass (#820); capped at 25, same as the owner gate.
                          Unlike 14/15 this one IS an ordinary
                          `LINT_REGISTRY` row (it only needs the docx, not a
                          missing-artifact special case), but it is still
                          ERROR-by-construction and still caps the score, so
                          it is listed here rather than in the ranked list
                          above.

The doctor also returns a `metrics` dict alongside `findings` (#816): numbers
a batch layer can trend over many runs -- Appendix share, the honors-table
malformed-row rate, unrouted taxonomy codes, stage 3b's fallback ratio,
source coverage %, and the T-validation/fragment-reconnection yields -- with
no severity of their own. See `_build_metrics`.

Usage:

    PYTHONPATH=src python -m unified_pipeline.run_doctor <root> <uid> \
        [--source cv.docx] [--out report.json]

The CLI writes <uid>_doctor.json into the root (or --out), prints a summary,
and exits 1 if any finding is WARN or worse. The library entry point
run_doctor(root, uid, source=None) -> dict never calls sys.exit; missing or
unreadable artifacts skip their lints with an INFO note instead of crashing,
and a lint that raises becomes one ERROR finding under its own key while the
remaining lints still run (#748).
"""

import argparse
import json
import logging
import math
import os
import re
import sys
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from unified_pipeline.core.template_boilerplate import is_source_boilerplate
from unified_pipeline.quality_score import stage3b_fallback_ratios
from unified_pipeline.segmentation_regression import compute_metrics, iter_source_block_lines

# Lint rules and their primitives now live in the doctor/ package (#493).
# Re-exported here rather than updating callers: five files import 33 names
# from this module, including the backend orchestrator, and a moved address
# that is not re-exported fails at IMPORT time -- which reads as a lost fix.
# test_run_doctor_contract.py pins that surface.
from unified_pipeline.doctor.shared import (  # noqa: F401,E402
    FINDING_STATUSES,
    STATUS_RAN,
    STATUS_SKIPPED,
    STATUS_UNREADABLE,
    Haystack,
    RENDER_PIECE_MIN_CHARS,
    RENDER_PIECE_WINDOW,
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    _LINE_SENTINEL,
    _RENDER_TOKEN_RE,
    _SECTION_HEADER_RE,
    _entry_pieces,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
    _cell_text,
    _docx_text,
    _table_lines,
    docx_body_blocks,
)
from unified_pipeline.doctor.lints.extraction import (  # noqa: F401,E402
    CLASSIFIED_UNRENDERED_WARN_ENTRIES,
    DEDUP_SAFE_CONTAINMENT,
    INVENTED_RECORD_LICENSURE_CODE,
    INVENTED_RECORD_MIN_VALUES,
    UNDER_EXTRACTION_MAX_PCT,
    UNDER_EXTRACTION_MIN_CHARS,
    UNDER_EXTRACTION_MIN_RECORDS,
    _DEDUP_TOKEN_RE,
    _FUNDING_SECTIONS,
    _STATUS_LABEL_RE,
    _YEAR_EDGE_LINE_RE,
    _alphanumeric_tokens,
    _entry_rendered,
    _entry_status,
    _funding_haystacks,
    _is_invented_record,
    _nonempty_field_values,
    _rendered_row_value_sets,
    lint_bucket_status,
    lint_classified_unrendered,
    lint_dedup_drops,
    lint_implausible_year,
    lint_invented_records,
    lint_offschema_fields,
    lint_taxonomy_code_coverage,
    lint_under_extraction,
    lint_wrong_start_date,
    unrouted_code_counts,
)
from unified_pipeline.doctor.lints.enrichment import (  # noqa: F401,E402
    _OWNER_CAP,
    _OWNER_GATE,
    lint_enrichment_failures,
    lint_owner_contact_missing,
)
from unified_pipeline.doctor.lints.render import (  # noqa: F401,E402
    DEAD_SECTION_MIN_LINES,
    DUPLICATE_PASSAGE_MIN_BLOCKS,
    DUPLICATE_PASSAGE_WARN_COUNT,
    DUPLICATE_RECORD_MIN_CHARS,
    DUPLICATE_RECORD_WARN_COUNT,
    DUPLICATE_RECORD_WINDOW,
    HONORS_NAME_BLOB_CHARS,
    PIPE_CLUSTER_MIN,
    PIPE_LEAK_MIN_SEPS,
    RECORD_DATE_LINE_MIN_CHARS,
    UNRENDERED_MIN_RECORD_LINES,
    _APPENDIX_ENTRY_RE,
    _APPENDIX_HEADER,
    _BRACKET_CODE_RE,
    _NUMBERED_LINE_RE,
    _PASSAGE_ENUMERATOR_RE,
    _PASSAGE_PUNCT_RE,
    _RECORD_DATE_PREFIX_RE,
    _SENTENCE_BOUNDARY_RE,
    _US_STATE_ABBREVS,
    _VENUE_DATE_RE,
    _YEAR_RE,
    _is_appendix_noise,
    _line_token_sets,
    _names_match,
    _passage_key,
    _record_lines,
    _record_rendered,
    appendix_entry_count,
    honors_table_totals,
    lint_dead_sections,
    lint_date_only_lines,
    lint_duplicate_passages,
    lint_duplicate_records,
    lint_output_hygiene,
    lint_pipe_leaks,
    lint_section_lost,
    lint_stage6_warnings,
    lint_table_shape,
    lint_unrendered_records,
)
from unified_pipeline.doctor.lints.segmentation import (  # noqa: F401,E402
    MISSED_HEADERS_WARN_COUNT,
    _header_key,
    _hierarchy_titles,
    lint_missed_headers,
    lint_segmentation,
    lint_table_lost,
)
from unified_pipeline.doctor.lints.protected_data import (  # noqa: F401,E402
    lint_protected_data_in_output,
)
from unified_pipeline.doctor.lints.runtime import (  # noqa: F401,E402
    lint_no_output,
    lint_pipeline_errors,
    lint_stage3b_fallback_ratio,
    lint_stage3b_second_pass_errors,
)


logger = logging.getLogger(__name__)


SEVERITY_ORDER = ("ERROR", "WARN", "INFO")  # most to least severe


# How often each lint fires at all, over the same 73 scored runs. Used only to
# ORDER what gets shown, never to decide severity. `top_lints` used to be
# `most_common(4)`, which ranks by raw finding count -- so output_hygiene
# (87.7% of runs) and table_shape (56.2%) consumed half the slots in every
# report and pushed the 1-3% lints, the ones that actually distinguish this run
# from every other run, out of view (#438).
#
# Same staleness caveat as the thresholds above: #440 replaces this with a live
# baseline. A lint absent from this table is treated as maximally surprising,
# which is the safe direction -- a newly added lint surfaces rather than hides.
#: Every lint key `run_doctor` can emit, in the order the lints run (#268).
#:
#: This is the canonical list. Consumers must import it rather than hardcoding
#: their own copy -- `scripts/corpus_doctor_sweep.py` kept one and it drifted.
#:
#: It cannot be derived from the `lint_*` function names, and that is not a
#: style preference. Two functions emit a key that is not their own name:
#:
#:     lint_stage6_warnings  -> "stage6_render_warnings"
#:     lint_pipeline_errors  -> "pipeline_errors_present"
#:
#: A name-derived list would invent two keys no finding ever carries (so they
#: report as "ran on 0 CVs") and drop the two that are real. A name-derived
#: COUNT is wrong for a third reason: `lint_surprise` matches the `lint_`
#: prefix but is a ranking helper, not a rule, and emits no findings at all.
#:
#: Order is load-bearing. The sweep breaks ranking ties on index, so reordering
#: this changes its report even when every finding is identical. Keep it in
#: dispatch order: `LINT_REGISTRY`'s rows, then the two hard-fail gates
#: `run_doctor()` dispatches by hand.
#:
#: `test_run_doctor_contract.py` checks this against the keys the lint
#: bodies actually pass to `_finding`/`_ready`, so adding a lint without
#: registering it here fails in CI.
KNOWN_LINTS = (
    "segmentation",
    "missed_headers",
    "bucket_status",
    "under_extraction",
    "classified_unrendered",
    "taxonomy_code_coverage",
    "stage3b_fallback_ratio",
    "output_hygiene",
    "dead_sections",
    "unrendered_records",
    "section_lost",
    "enrichment_failures",
    "stage6_render_warnings",
    "dedup_drops",
    "pipe_leaks",
    "table_shape",
    "duplicate_passages",
    "duplicate_records",
    "protected_data_in_output",
    "invented_records",
    "wrong_start_date",
    "table_lost",
    "date_only_lines",
    "stage3b_second_pass_error",
    "offschema_fields",
    "implausible_year",
    "owner_contact_missing",
    "pipeline_errors_present",
    "no_output",
)


# duplicate_records' prevalence below was measured on the 66-uid doctor-gate
# farm (scripts/doctor_gate.py), a DIFFERENT and smaller corpus than the one
# every other entry in this table was measured on (#438's 73 scored runs /
# #446's 125 rendered corpus outputs) -- the two are not comparable counts,
# only comparable ROUGH ORDER-OF-MAGNITUDE signals for `lint_surprise`.
# protected_data_in_output's is a THIRD, smaller, more recent basis still:
# the #820 retro-scan of 89 canonical batch outputs + 65 farm outputs (one
# uid), 1/154.
LINT_PREVALENCE = {
    "output_hygiene": 0.877,
    "table_shape": 0.562,
    "missed_headers": 0.288,
    "classified_unrendered": 0.288,
    # 32 of 126 corpus renders fired at any severity (33 with prod run
    # ZXVGAC), from a fresh dev render of the same farm + 2026-09-11/-17
    # batches section_lost was calibrated on (#259).
    "date_only_lines": 0.254,
    "stage6_render_warnings": 0.123,
    "dedup_drops": 0.110,
    "segmentation": 0.082,
    # 30 of 165 corpus runs (farm + 2026-09-11/-17 batches, stored stage-2
    # artifacts, measured 2026-09-29); 19 of the 30 also trip `segmentation`.
    "table_lost": 0.182,
    "enrichment_failures": 0.082,
    # 9 of 126 corpus renders from dev (farm + 2026-09-11/-17 batches,
    # re-rendered 2026-09-29), the corpus section_lost was calibrated on.
    "section_lost": 0.071,
    "owner_contact_missing": 0.068,
    "duplicate_records": 0.061,
    "pipe_leaks": 0.055,
    "unrendered_records": 0.027,
    "dead_sections": 0.027,
    "duplicate_passages": 0.014,
    "bucket_status": 0.014,
    "under_extraction": 0.014,
    "pipeline_errors_present": 0.001,
    # #818: 6 of 183 stored stage-3b artifacts (farm25 + outputs99 + the
    # 2026-09-11/-17 batches, measured 2026-09-29) carry a second-pass
    # `error`; all 6 are older builds -- 0 of the 60 batch artifacts do.
    "stage3b_second_pass_error": 0.033,
    # #810: zero of the 2026-09-11 batch's 40 uids tripped the gate -- every
    # ratio stayed under STAGE3B_FALLBACK_RATIO_THRESHOLD (max observed
    # 0.0004, three orders of magnitude under it). The only known real
    # positive is web30's own partial-outage numbers, from a DIFFERENT
    # slice-batch corpus (~worktrees/batch-slices) not otherwise represented
    # in this table. Same zero-observed rarity class as
    # pipeline_errors_present above -- same floor, made explicit here rather
    # than left to lint_surprise's absent-key default (which is also 0.001,
    # so this row changes no ranking; it only stops the value from reading
    # as an oversight).
    "stage3b_fallback_ratio": 0.001,
    # Measured on the 2026-09-11 batch's clean re-run (40 CVs, a DIFFERENT
    # and much smaller corpus than the 73/125-run measurements above -- same
    # rough-order-of-magnitude caveat as duplicate_records): web204 is the
    # one uid of 40 with no stage-6 output at all (1/40 = 0.025).
    "no_output": 0.025,
    "protected_data_in_output": 0.006,
    # Re-measured over 278 unique local stage-4 artifact sets (the rg_farm,
    # batch-3, batch-4, the local _autopsy stage_4 set at
    # data/sample_cvs/word/web_harvest/_batch_runs/_autopsy_artifacts/stage_4,
    # src/unified_pipeline/outputs, and the A5IZ6Q incident this lint was
    # written for; 149 of the 278 have a locally retained rendered docx):
    # NOT the zero-observed rarity class the original introducing commit
    # claimed. 3 of 278 uids fire (3/278 = 0.011) -- A5IZ6Q itself, plus two
    # organic corpus hits, 976WPY and IO4DEA, each the same fabricated "New
    # York State" F1 record as A5IZ6Q's, built from the identical unfilled
    # licensure-instruction paragraph (part (b) of lint_invented_records).
    # All three are true positives -- the corpus fire count is 0.011, not
    # pipeline_errors_present/stage3b_fallback_ratio's true zero-observed
    # 0.001 floor; this row now carries its own measured value rather than
    # borrowing theirs.
    "invented_records": 0.011,
    # Both measured 2026-10-02 on the 163-CV wave-1 stage-4 farm, one fire
    # per CV at any severity: offschema_fields 30/163 (16 of 23 sampled
    # findings a value missing from the rendered docx), implausible_year
    # 6/163 (every one of its 17 findings a hand-checked wrong century).
    "offschema_fields": 0.184,
    "implausible_year": 0.037,
}


def lint_surprise(lint: str) -> float:
    """How informative it is that THIS lint fired, in bits.

    A lint that fires on 88% of runs says almost nothing about the run in front
    of you; one that fires on 1.4% says a great deal. Ranking by raw count gets
    this exactly backwards, because the ubiquitous lints are also the ones that
    fire many times."""
    return math.log2(1.0 / max(LINT_PREVALENCE.get(lint, 0.001), 0.001))


def rank_lints(counts: Dict[str, int]) -> List[Tuple[str, int]]:
    """Order lints for display: most surprising first, count as the tiebreak."""
    return sorted(counts.items(),
                  key=lambda kv: (-lint_surprise(kv[0]), -kv[1], kv[0]))


# A well-formed report is a few KB. A malformed artifact with huge text fields
# could inflate the findings into a multi-MB file; cap it. Evidence strings are
# already sliced at construction, so the only way to blow the cap is a very
# large NUMBER of findings — truncating the list is the effective lever.
MAX_REPORT_BYTES = 10 * 1024 * 1024
MAX_REPORT_FINDINGS = 1000


# ----------------------------------------------------------------- docx views

def _get_docx_document():
    """Lazy python-docx import. The doctor is optional tooling and must import
    cleanly where python-docx isn't installed (JSON-only lints still run), so
    the import stays out of module scope -- but it's the SAME import in three
    docx views, so centralize it here and give a clear message when missing."""
    try:
        from docx import Document
    except ImportError as e:  # pragma: no cover - only when the extra is absent
        raise ImportError("python-docx is required for the docx lints: "
                          "pip install python-docx") from e
    return Document


#: A credential in name-suffix position (', MD' / ', Ph.D.') -- the marker of a
#: person line rather than a section header. Anchored on the comma so headers
#: that merely contain commas are not swallowed.
_NAME_CREDENTIAL_RE = re.compile(
    r",\s*(?:M\.?D\.?|D\.?O\.?|Ph\.?\s?D\.?|M\.?B\.?B\.?S\.?|MBA|MPH|MSc?|"
    r"FACEP|FAAEM|FACS|FACP|CPE|DDS|DMD|DVM|JD|RN|PA-C)\b",
    re.IGNORECASE,
)

#: A person line with the role marker in FRONT ('PI. Jane Q. Sample', 'Dr.
#: John Example'; #539). Both halves must hold, so a header that merely
#: starts with a marker ('PI. RESPONSIBILITIES') is not swallowed: an exact
#: marker token, then a run of Title-case name words / initials that ends on a
#: real (multi-letter, lower-case-bearing) name word. ALL-CAPS text is never
#: matched -- a caps line after a marker reads as a header, not a name.
_LEADING_ROLE_NAME_RE = re.compile(
    r"^(?:PI|Dr|Prof|Mr|Mrs|Ms)\.\s+(?:[A-Z]\.?\s+){0,2}"
    r"[A-Z][a-z][\w'’-]*(?:\s+(?:[A-Z]\.?|[A-Z][a-z][\w'’-]*)){0,3}$"
)


def _logical_cells(row) -> list:
    """The row's distinct cells. python-docx's ``row.cells`` repeats one
    gridSpan-merged cell once per layout-grid column it spans, so a merged
    row reads as several copies of the same cell; this collapses them on the
    underlying ``<w:tc>`` element."""
    cells: list = []
    for cell in row.cells:
        if not any(cell._tc is kept._tc for kept in cells):
            cells.append(cell)
    return cells


def _is_single_column(tbl) -> bool:
    """Is this table one LOGICAL column -- every row exactly one cell,
    whatever the layout grid says?

    ``len(tbl.columns)`` counts ``w:tblGrid`` layout columns, and CVs
    routinely build a 1x1 section-container table on a two- or three-column
    grid with each row's single cell gridSpan-merged across it (#446 review,
    run_doctor.py thread item 6 / #749: 32 of 183 sample docx carry
    gridSpan), so the grid count read such a table as a data table and the
    section headers inside it were never candidates. The representation this
    commits to: a row with two or more logical cells is a data row wherever
    the grid puts it, so one such row makes the whole table multi-column --
    including a variable-width table whose merged title row sits over data
    rows, which a first-row-only count would misread as single-column."""
    return all(len(_logical_cells(row)) == 1 for row in tbl.rows)


def iter_header_candidates(docx_path: str) -> list[str]:
    """Header-looking source lines: short, letters-only, ALL-CAPS bold (or
    styled as a Heading), from top-level paragraphs and single-column table
    cells (the 1x1 layout tables CVs use as section containers; see
    `_is_single_column` for what single-column means). Multi-column tables
    are data tables — their bold cells are column headers — and document
    furniture ('CURRICULUM VITAE', revision stamps) is not a header either.
    These are what stage 1a should have promoted to hierarchy nodes."""
    Document = _get_docx_document()

    candidates: List[str] = []

    def consider(para):
        text = para.text.strip()
        if not 3 <= len(text) <= 60:
            return
        if not any(ch.isalpha() for ch in text) or any(ch.isdigit() for ch in text):
            return
        if is_source_boilerplate(text):
            return
        # A section header is a standalone label. A tab means 'label<TAB>value'
        # -- a data row that happens to be styled like a heading, e.g. the
        # licensure rows 'Active\t\t\tMaryland' / 'Certification:\t\t\tAmerican
        # Board of Surgery'. Segmentation is right not to promote these.
        if "\t" in text:
            return
        # The owner's name/credential line is document furniture, not a section:
        # 'STANLEY J. SZEFLER, M.D.', 'LEE W. SHOCKLEY, MD, MBA, FACEP, FAAEM,
        # CPE', 'CURRICULUM VITAE - JEFFREY R OLSEN, MD'. Anchored on the
        # comma-suffix position so real headers that merely contain a comma
        # ('ADMINISTRATIVE APPOINTMENTS, SCHOOL OF MEDICINE, CU:') survive.
        if _NAME_CREDENTIAL_RE.search(text) or _LEADING_ROLE_NAME_RE.match(text):
            return
        style = getattr(para.style, "name", "") or ""
        if style.startswith("Heading"):
            candidates.append(text)
            return
        if text != text.upper():
            return
        runs = [r for r in para.runs if r.text.strip()]
        if runs and all(r.bold for r in runs):
            candidates.append(text)

    def walk_table(tbl) -> None:
        if not _is_single_column(tbl):
            return
        for row in tbl.rows:
            for cell in _logical_cells(row):
                for para in cell.paragraphs:
                    consider(para)
                for nested in cell.tables:
                    walk_table(nested)

    doc = Document(docx_path)
    for para in doc.paragraphs:
        consider(para)
    for tbl in doc.tables:
        walk_table(tbl)
    return candidates


# `_docx_text`, `_cell_text`, `_table_lines` and the body-order block walk
# moved to `doctor/shared.py` (#820 round 2) so `quality_score.py` can read a
# rendered document through the SAME reader the lints use without importing
# this module (a cycle: run_doctor -> lints.enrichment -> quality_score).
# Re-exported by name above for the five files that import them from here.


def read_docx_blocks(docx_path: str) -> List[Tuple[str, str]]:
    """Body-order blocks of a docx: ("p", text) per paragraph, ("table",
    _table_lines joined by newlines) per table. Grants render as one Word
    table per grant, so any output check must read tables AND paragraphs."""
    Document = _get_docx_document()
    return docx_body_blocks(Document(docx_path))


def read_docx_table_rows(docx_path: str) -> List[List[List[str]]]:
    """Raw per-row cell texts of every top-level table, EMPTY CELLS INCLUDED
    — _table_lines drops empty cells, which hides an empty date column from
    the shape checks (lint 13)."""
    Document = _get_docx_document()

    doc = Document(docx_path)
    return [[[_cell_text(cell).strip() for cell in row.cells] for row in tbl.rows]
            for tbl in doc.tables]


# --------------------------------------------------------- artifact resolution

class ArtifactSpec(NamedTuple):
    stage_dir: str
    suffix: str
    #: Top-level keys that must be present and hold a list of objects -- the
    #: records every lint on this artifact iterates.
    record_lists: tuple[str, ...] = ()
    #: Top-level keys that, when present, must hold a list of objects; absent
    #: is a valid (empty) artifact of this kind.
    optional_lists: tuple[str, ...] = ()
    #: Top-level keys that, when present and not null, must hold an object.
    object_fields: tuple[str, ...] = ()


#: What "a valid artifact of this kind" means at the loading boundary (#446
#: review, run_doctor.py thread item 2 / #747): the top-level shape every
#: lint that reads the artifact indexes into, measured over the farm's
#: 115/106/98/66/62 files of each JSON kind. Every one is an object; every
#: stage-1a carries a `hierarchy` list of nodes; every stage-2/3b/4/5
#: carries an `entries` list of objects; stage-4's `cv_owner` is an object
#: where present (absent on 2 of 66, which the owner gate scores as missing,
#: not invalid); the render sidecar's `warnings`/`dedup_decisions` are lists
#: of objects where present. Nested shapes stay the lints' business -- this
#: is the boundary check, not a schema.
_ARTIFACTS = {
    "stage_1a": ArtifactSpec("stage_1a_segmentation", "_segmented.json",
                             record_lists=("hierarchy",)),
    "stage_2": ArtifactSpec("stage_2_entry_extraction", "_entries.json",
                            record_lists=("entries",)),
    "stage_3b": ArtifactSpec("stage_3b_classified_entries", "_classified.json",
                             record_lists=("entries",)),
    "stage_4": ArtifactSpec("stage_4_field_extraction", "_fields.json",
                            record_lists=("entries",), object_fields=("cv_owner",)),
    "stage_5_enrichment": ArtifactSpec("stage_5_enrichment", "_enriched.json",
                                       record_lists=("entries",)),
    "stage_5b": ArtifactSpec("stage_5b_institution_enrichment",
                             "_institution_enriched.json", record_lists=("entries",)),
    "stage_6_docx": ArtifactSpec("stage_6_wcm_documents", "_wcm.docx"),
    "stage_6_report": ArtifactSpec("stage_6_wcm_documents", "_render_warnings.json",
                                   optional_lists=("warnings", "dedup_decisions")),
}

#: The artifacts `_load_json` reads, in load order; the docx is read by its
#: own views.
_JSON_ARTIFACTS = tuple(key for key, spec in _ARTIFACTS.items()
                        if spec.suffix.endswith(".json"))


def _artifact_shape_error(data: object, spec: ArtifactSpec) -> str | None:
    """The first way `data` fails to be a valid artifact of `spec`'s kind, or
    None when it is one. Names the offending field, so the ERROR finding a
    reader sees says what is wrong with the file rather than which lint
    happened to trip over it first."""
    if not isinstance(data, dict):
        return f"top level is {type(data).__name__}, not an object"
    for key in spec.record_lists:
        if key not in data:
            return f"missing '{key}'"
    for key in spec.record_lists + spec.optional_lists:
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, list):
            return f"'{key}' is {type(value).__name__}, not a list"
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                return f"'{key}[{i}]' is {type(item).__name__}, not an object"
    for key in spec.object_fields:
        value = data.get(key)
        if value is not None and not isinstance(value, dict):
            return f"'{key}' is {type(value).__name__}, not an object"
    return None

#: The owner gate reports an ABSENT *_fields.json only for a run that got as
#: far as rendering a deliverable, or whose stage-4 file exists but will not
#: parse. An earlier artifact (stage_2/stage_3b) is not enough: a run doctored
#: mid-pipeline, or one that crashed after stage 2, has no owner name YET --
#: reporting "do not deliver" on it would be a false positive (#437).
_DELIVERABLE = ("stage_4", "stage_6_docx")


def _uid_owns(name: str, uid: str) -> bool:
    """Does file ``name`` belong to ``uid`` -- and not to a longer uid that
    merely starts with it?

    ``glob(f"{uid}*")`` is a PREFIX match, so uid 'web05' also matches
    'web050_entries.json'. Sorted, '0' (0x30) sorts before '_' (0x5F), so the
    WRONG CV wins: the 2026-07-15 sweep doctored web04 against web049, web05
    against web050 and web06 against web060 -- 3 of 25 CVs diagnosed entirely
    against another CV's artifacts. Require the uid to end at a non-alphanumeric
    boundary ('web05_entries.json' yes, 'web050_entries.json' no).
    """
    if not uid or not name.startswith(uid):
        return False
    rest = name[len(uid):]
    return bool(rest) and not rest[0].isalnum()


def _find_artifact(root: Path, uid: str, key: str) -> Optional[Path]:
    spec = _ARTIFACTS[key]
    directory = root / spec.stage_dir
    if not directory.is_dir():
        return None
    matches = sorted(p for p in directory.glob(f"{uid}*{spec.suffix}")
                     if _uid_owns(p.name, uid))
    return matches[0] if matches else None


def _find_source(root: Path, uid: str) -> Optional[Path]:
    for directory in (root, root / "uploads"):
        if directory.is_dir():
            matches = sorted(p for p in directory.glob(f"{uid}*.docx")
                             if not p.name.endswith("_wcm.docx")
                             and _uid_owns(p.name, uid))
            if matches:
                return matches[0]
    return None


def _load_json(path: Path | None, label: str | None = None,
               on_unreadable: Callable[[str, str], None] | None = None,
               spec: ArtifactSpec | None = None) -> dict | None:
    """Load an artifact JSON, or None if it is absent.

    `path` comes from _find_artifact/_find_source, which glob -- so a non-None
    path always names a file that EXISTS. A load failure on it therefore means
    present-but-unreadable (corrupt JSON, permission error), which is NOT the
    same as absent: report it via on_unreadable so a lint does not silently
    degrade to "skipped: missing <stage>". Absent (path is None) stays quiet.

    With `spec`, the parsed JSON must also be a valid artifact of that kind
    (`_artifact_shape_error`): a file that parses but is not the shape its
    lints index into is reported through the same on_unreadable path as
    corrupt JSON, naming the offending field, instead of failing later inside
    whichever lint reaches it first (#446 review, run_doctor.py thread item
    2 / #747). "JSON parsed" is not "this is a stage-4 artifact".
    """
    if not path:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("run_doctor could not read %s (%s): %s",
                       label or path, type(e).__name__, e)
        if on_unreadable is not None:
            on_unreadable(label or str(path), f"{type(e).__name__}: {e}")
        return None
    problem = _artifact_shape_error(data, spec) if spec is not None else None
    if problem is None:
        return data
    logger.warning("run_doctor: %s is not a valid %s artifact: %s",
                   path, label or "stage", problem)
    if on_unreadable is not None:
        on_unreadable(label or str(path), f"invalid artifact: {problem}")
    return None


def _try(fn, label: str = None, on_unreadable=None):
    """Run a docx-reading loader. Its callers guard on the path existing, so a
    failure here is also present-but-unreadable, reported like _load_json."""
    try:
        return fn()
    except Exception as e:
        logger.warning("run_doctor could not read %s (%s): %s",
                       label or "docx", type(e).__name__, e)
        if on_unreadable is not None:
            on_unreadable(label or "docx", f"{type(e).__name__}: {e}")
        return None


# --------------------------------------------------------------------- doctor

def _ready(lint_id: str, *, unreadable: Dict[str, str], findings: List[Dict],
           **inputs) -> bool:
    """True when every input for a lint is present; else record why it was
    skipped and return False.

    A None input is an ERROR when its file existed but would not parse, and the
    benign INFO "skipped: missing" when it was genuinely absent. The two are
    told apart by looking the input's NAME up in `unreadable`, so each keyword
    name passed here MUST equal the label the matching loader gave `_note`
    (e.g. missed_headers' `candidates` input is loaded with label
    "candidates"). Break that alignment and a broken artifact silently degrades
    to INFO. `unreadable`/`findings` are passed in explicitly rather than closed
    over so that coupling is visible in the signature."""
    missing = [name for name, value in inputs.items() if value is None]
    if not missing:
        return True
    broken = [name for name in missing if name in unreadable]
    absent = [name for name in missing if name not in unreadable]
    if absent:
        findings.append(_finding(
            lint_id, "INFO", "skipped: missing " + ", ".join(absent),
            status=STATUS_SKIPPED, reason=", ".join(absent)))
    if broken:
        findings.append(_finding(
            lint_id, "ERROR", "skipped: unreadable " + ", ".join(
                f"{name} ({unreadable[name]})" for name in broken),
            status=STATUS_UNREADABLE, reason=", ".join(broken)))
    return False


def _run_lint(lint_id: str, rule: Callable[..., list[dict]],
              args: Sequence[object], findings: list[dict]) -> None:
    """Run one lint inside its own fault boundary (#446 review, run_doctor.py
    thread item 3 / #748). A lint that raises becomes ONE ERROR finding
    under its own key -- logged with the traceback, never swallowed -- and
    the lints after it still run. The doctor diagnoses failures; it must not
    become one: the backend calls run_doctor in-process, so an uncaught lint
    exception used to take the quality score and the Teams card with it."""
    try:
        findings.extend(rule(*args))
    except Exception as e:
        logger.exception("run_doctor: lint %s crashed", lint_id)
        findings.append(_finding(
            lint_id, "ERROR", f"lint {lint_id} crashed: {type(e).__name__}: {e}"))


class LintSpec(NamedTuple):
    """One row of the lint registry: the key the lint emits, the rule that
    emits it, and the loaded-input views it takes, in the rule's positional
    order. `optional` views are passed after `inputs` and may be None: the lint
    has a fallback without them, so their absence is not a skip (#890)."""
    lint_id: str
    rule: Callable[..., list[dict]]
    inputs: tuple[str, ...]
    optional: tuple[str, ...] = ()


#: The loader LABEL each input view is checked under by `_ready` -- the key
#: `_note` records an unreadable artifact by, so a None view is traced back
#: to a broken file rather than an absent one. The stage-6 docx feeds two
#: views (body blocks, raw table rows) under one label because they read one
#: file; the source docx feeds two readers under two labels because either
#: can fail alone.
_VIEW_LABELS = {
    "source_lines": "source",
    "source_block_lines": "source",
    "candidates": "candidates",
    "stage_1a": "stage_1a",
    "stage_2": "stage_2",
    "stage_3b": "stage_3b",
    "stage_4": "stage_4",
    "stage_5_enrichment": "stage_5_enrichment",
    "stage_5b": "stage_5b",
    "stage_6_report": "stage_6_report",
    "blocks": "stage_6_docx",
    "table_rows": "stage_6_docx",
}


#: The lint registry (#446 review, run_doctor.py thread item 5; the registry
#: half of #493): one row per artifact-gated lint, in dispatch order,
#: replacing the hand-written `if ready(...): findings.extend(...)` stanza
#: per lint that `run_doctor()` used to grow by two lines per lint. Adding a
#: lint is one row here plus its `KNOWN_LINTS` entry, and
#: `test_run_doctor_contract.py` pins the two against each other. The two
#: quality-score hard-fail gates (`owner_contact_missing`,
#: `pipeline_errors_present`) are not rows: each breaks the "missing
#: artifact -> skip" convention in its own way (see `run_doctor()`), and a
#: row shape that could express both would be a second dispatch language.
LINT_REGISTRY: tuple[LintSpec, ...] = (
    LintSpec("segmentation", lint_segmentation, ("source_lines", "stage_1a", "stage_2")),
    LintSpec("missed_headers", lint_missed_headers, ("candidates", "stage_1a", "stage_2"),
             optional=("stage_4",)),
    LintSpec("bucket_status", lint_bucket_status, ("stage_4", "blocks")),
    LintSpec("under_extraction", lint_under_extraction, ("stage_4",)),
    LintSpec("classified_unrendered", lint_classified_unrendered, ("stage_3b", "blocks"),
             optional=("stage_4", "stage_5b")),
    LintSpec("taxonomy_code_coverage", lint_taxonomy_code_coverage, ("stage_3b",)),
    LintSpec("stage3b_fallback_ratio", lint_stage3b_fallback_ratio, ("stage_3b",)),
    LintSpec("output_hygiene", lint_output_hygiene, ("blocks",)),
    LintSpec("dead_sections", lint_dead_sections, ("stage_2", "blocks")),
    LintSpec("unrendered_records", lint_unrendered_records, ("stage_4", "blocks")),
    LintSpec("section_lost", lint_section_lost, ("stage_4", "blocks")),
    LintSpec("enrichment_failures", lint_enrichment_failures, ("stage_5_enrichment",)),
    LintSpec("stage6_render_warnings", lint_stage6_warnings, ("stage_6_report",)),
    LintSpec("dedup_drops", lint_dedup_drops, ("stage_6_report",)),
    LintSpec("pipe_leaks", lint_pipe_leaks, ("blocks",)),
    LintSpec("table_shape", lint_table_shape, ("table_rows",)),
    LintSpec("duplicate_passages", lint_duplicate_passages, ("blocks",)),
    LintSpec("duplicate_records", lint_duplicate_records, ("blocks",)),
    LintSpec("protected_data_in_output", lint_protected_data_in_output, ("blocks",)),
    LintSpec("invented_records", lint_invented_records, ("stage_4", "table_rows")),
    LintSpec("wrong_start_date", lint_wrong_start_date, ("stage_4",)),
    # Last row, not beside `segmentation`: this order breaks the sweep's
    # ranking ties, so a new lint appends rather than shifting every other.
    LintSpec("table_lost", lint_table_lost, ("source_block_lines", "stage_2")),
    LintSpec("date_only_lines", lint_date_only_lines, ("blocks",)),
    LintSpec("stage3b_second_pass_error", lint_stage3b_second_pass_errors, ("stage_3b",)),
    LintSpec("offschema_fields", lint_offschema_fields, ("stage_4",)),
    LintSpec("implausible_year", lint_implausible_year, ("stage_4",)),
)


def _build_metrics(views: dict) -> dict:
    """Batch-trend numbers, as distinct from the per-run findings above
    (#816): things that are true of the PIPELINE in general -- how big the
    Appendix usually runs, how often stage 3b falls back to default codes --
    belong in a trend line, not a per-run WARN. `output_hygiene`'s appendix
    count, `table_shape`'s honors-malformed-row count and
    `taxonomy_code_coverage`'s unrouted-code counts fired on 37, 24 and 10 of
    40 runs respectively in the 2026-09-11 batch and carried no per-run
    information (#438's class) -- their lints now report those numbers here
    instead of in a WARN, alongside three metrics with no lint of their own
    yet (`stage3b_fallback_ratio` from #810, `t_validation_yield` and
    `fragment_reconnection_yield` and `total_post_corrections` from #818).

    Corpus-outlier promotion (the p75/p90 convention #438 introduced for
    `missed_headers`) is explicitly OUT of scope here -- that is a batch-
    layer concern over many runs' worth of these numbers, not something one
    run's doctor call can compute. Numbers only, no severity.

    Every number is read from the artifact directly or from a function a
    finding above already calls (`appendix_entry_count`, `honors_table_
    totals`, `unrouted_code_counts`, `stage3b_fallback_ratios`,
    `compute_metrics`) -- never a second definition of the same predicate
    (§1.5). A metric whose inputs are absent, or whose denominator is 0, is
    simply omitted rather than reported as a misleading 0. Each of the four
    sections below (appendix, honors tables, stage-3b-derived, source
    coverage) is independent and individually guarded: a stage_3b shaped
    nothing like the real artifact drops only the stage-3b-derived metrics,
    not the appendix or coverage ones computed from other views -- run_doctor()
    additionally wraps the whole call, but that would discard every metric
    over one bad section rather than just the section that broke."""
    metrics: dict[str, object] = {}
    blocks = views.get("blocks")
    stage_3b = views.get("stage_3b")

    try:
        if blocks is not None:
            appendix_entries = appendix_entry_count(blocks)
            if appendix_entries is not None:
                metrics["appendix_entries"] = appendix_entries
                if isinstance(stage_3b, dict):
                    classified = len(stage_3b.get("entries") or [])
                    if classified:
                        metrics["appendix_share"] = round(
                            appendix_entries / classified, 4)
    except Exception:
        logger.exception("run_doctor: appendix metrics crashed")

    try:
        table_rows = views.get("table_rows")
        if table_rows is not None:
            malformed, total = honors_table_totals(table_rows)
            if total:
                metrics["honors_malformed_rows"] = malformed
                metrics["honors_rows"] = total
    except Exception:
        logger.exception("run_doctor: honors-table metrics crashed")

    try:
        if isinstance(stage_3b, dict):
            unrouted = unrouted_code_counts(stage_3b)
            if unrouted:
                metrics["unrouted_code_entries"] = unrouted

            ratios = stage3b_fallback_ratios(stage_3b)
            if ratios:
                metrics["stage3b_fallback_ratio"] = round(max(ratios.values()), 4)

            stats = stage_3b.get("meta") or {}
            stats = stats.get("stats") or {} if isinstance(stats, dict) else {}
            tv = stats.get("t_validation") or {}
            reviewed = tv.get("t_entries_reviewed") if isinstance(tv, dict) else None
            if reviewed:
                metrics["t_validation_yield"] = round(
                    tv.get("t_entries_reclassified", 0) / reviewed, 4)
            corrections = stats.get("total_post_corrections")
            if isinstance(corrections, int) and not isinstance(corrections, bool):
                metrics["total_post_corrections"] = corrections
            fr = stats.get("fragment_reconnection") or {}
            f_reviewed = fr.get("fragments_reviewed") if isinstance(fr, dict) else None
            if f_reviewed:
                metrics["fragment_reconnection_yield"] = round(
                    fr.get("fragments_reconnected", 0) / f_reviewed, 4)
    except Exception:
        logger.exception("run_doctor: stage3b-derived metrics crashed")

    try:
        source_lines, stage_1a, stage_2 = (
            views.get("source_lines"), views.get("stage_1a"), views.get("stage_2"))
        if source_lines is not None and stage_1a is not None and stage_2 is not None:
            metrics["source_coverage_pct"] = compute_metrics(
                source_lines, stage_1a, stage_2)["text_coverage_pct"]
    except Exception:
        logger.exception("run_doctor: source-coverage metric crashed")

    return metrics


def _run_hand_dispatched_gates(views: dict, paths: dict, uid: str,
                               unreadable: dict[str, str],
                               findings: list[dict], ready: Callable[..., bool]) -> None:
    """The three hard-fail gates `run_doctor()` dispatches by hand rather
    than through `LINT_REGISTRY`, because none of them fits `_ready()`'s
    "this exact loaded artifact is present" convention: owner_contact_missing
    and pipeline_errors_present each read a DIFFERENT completeness condition
    (see their own comments below), and no_output's three inputs are
    artifact PATHS, never loaded content. Split out of `run_doctor()` (round-2
    N1, a pure move: same bodies, same call sites, only the disclosure length
    changes) so that function stays at a glance-able size."""
    stage_2, stage_3b, stage_4 = views["stage_2"], views["stage_3b"], views["stage_4"]
    # score_cv_owner caps at 25 for an ABSENT *_fields.json as well as an empty
    # cv_owner name, so this lint breaks the house "missing artifact -> skip"
    # convention: skipping the absent case would report the more broken run
    # more quietly (#437). It still skips for a run that never reached stage 4
    # -- an incomplete or wrong-uid run has no owner name yet, and the batch
    # runner doctors CVs whose pipeline returned rc!=0.
    if stage_4 is not None or any(paths[k] for k in _DELIVERABLE):
        _run_lint("owner_contact_missing", lint_owner_contact_missing,
                  (stage_4, uid, unreadable.get("stage_4")), findings)
    else:
        ready("owner_contact_missing", stage_4=stage_4)
    # The error scan covers exactly the JSON the DEPLOYED scorer globs:
    # quality_score_service copies *_entries/_classified/_fields.json into the
    # dir it scores, which are stage_2/stage_3b/stage_4 here. It scans whatever
    # subset of those loaded rather than requiring all three -- the scorer
    # scans whatever landed too, so an absent artifact must not hide a fatal
    # recorded in another. `ready` still reports the skip when none loaded, so
    # absent stays INFO and unreadable stays ERROR under real loader labels.
    scored_artifacts = {label: data for label, data in (
        ("stage_2", stage_2), ("stage_3b", stage_3b), ("stage_4", stage_4))
        if data is not None}
    if scored_artifacts:
        _run_lint("pipeline_errors_present", lint_pipeline_errors,
                  (scored_artifacts,), findings)
    else:
        ready("pipeline_errors_present", stage_2=stage_2, stage_3b=stage_3b,
              stage_4=stage_4)

    # no_output (#745): a third hand-dispatched hard-fail gate, alongside the
    # two above. Booleans, not loaded content -- `paths[...]` truthiness is
    # exactly "does this file exist", which is what the gate asks -- so it
    # cannot go through LINT_REGISTRY/`_ready()`, the same reason
    # owner_contact_missing/pipeline_errors_present don't either. It does not
    # replace the render lints' own per-artifact "skipped: missing
    # stage_6_docx"/"missing stage_6_report" INFO -- both still fire.
    _run_lint("no_output", lint_no_output,
              (bool(paths["stage_4"]), bool(paths["stage_6_docx"]),
               bool(paths["stage_6_report"])), findings)


def run_doctor(root: Path, uid: str, source: Path | None = None) -> dict:
    """Run every lint whose artifacts exist under root for this document uid.
    Never raises on missing/unreadable artifacts, never raises out of a lint
    (`_run_lint` turns that into an ERROR finding) and never calls sys.exit
    — the backend calls this in-process; the CLI wraps it."""
    root = Path(root)
    paths = {key: _find_artifact(root, uid, key) for key in _ARTIFACTS}
    source_path = Path(source) if source else _find_source(root, uid)

    # Artifacts that exist but failed to load or validate, keyed by the label
    # their loader passed to _note -- which MUST match the input kwarg name _ready() checks
    # (so a None input is traced back to a broken file vs a genuinely absent
    # one). The source docx feeds two independent readers; they take separate
    # labels so a reader that fails alone is attributed to the right lint.
    unreadable: Dict[str, str] = {}
    def _note(label, detail):
        unreadable[label] = detail

    views: dict[str, object] = {
        key: _load_json(paths[key], key, _note, _ARTIFACTS[key])
        for key in _JSON_ARTIFACTS}
    # One read of the source docx feeds both views: the flat lines are the
    # block-tagged lines with the tag dropped.
    source_block_lines = (_try(lambda: iter_source_block_lines(str(source_path)), "source", _note)
                          if source_path else None)
    views["source_block_lines"] = source_block_lines
    views["source_lines"] = (None if source_block_lines is None
                             else [line for _, line in source_block_lines])
    views["candidates"] = (_try(lambda: iter_header_candidates(str(source_path)), "candidates", _note)
                           if source_path else None)
    views["blocks"] = (_try(lambda: read_docx_blocks(str(paths["stage_6_docx"])), "stage_6_docx", _note)
                       if paths["stage_6_docx"] else None)
    views["table_rows"] = (_try(lambda: read_docx_table_rows(str(paths["stage_6_docx"])), "stage_6_docx", _note)
                           if paths["stage_6_docx"] else None)

    findings: List[Dict] = []
    ready = partial(_ready, unreadable=unreadable, findings=findings)

    for spec in LINT_REGISTRY:
        inputs = {_VIEW_LABELS[view]: views[view] for view in spec.inputs}
        if ready(spec.lint_id, **inputs):
            _run_lint(spec.lint_id, spec.rule,
                      [views[view] for view in spec.inputs + spec.optional], findings)

    _run_hand_dispatched_gates(views, paths, uid, unreadable, findings, ready)

    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for f in findings:
        counts[f["severity"]] += 1
    worst = next((s for s in SEVERITY_ORDER if counts[s]), None)

    try:
        metrics = _build_metrics(views)
    except Exception:
        logger.exception("run_doctor: metrics computation crashed for %s", uid)
        metrics = {}

    return {
        "document_uid": uid,
        "root": str(root),
        "artifacts": {
            "source": str(source_path) if source_path else None,
            **{key: str(p) if p else None for key, p in paths.items()},
        },
        "findings": findings,
        "counts": counts,
        "worst_severity": worst,
        "metrics": metrics,
    }


def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", help="run outputs root (contains the stage_* dirs)")
    parser.add_argument("uid", help="document uid (artifact filename prefix)")
    parser.add_argument("--source", help="source .docx (default: <root>[/uploads]/<uid>*.docx)")
    parser.add_argument("--out", help="report file (default: <root>/<uid>_doctor.json)")
    args = parser.parse_args(argv)

    # Advisory pre-check only: fail fast with a clear message BEFORE the slow
    # docx parsing if the target is already unwritable. It is NOT the
    # correctness guarantee -- the file can still turn unwritable between here
    # and the write (TOCTOU), so the write below is wrapped in its own guard.
    out_path = Path(args.out) if args.out else Path(args.root) / f"{args.uid}_doctor.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not os.access(out_path, os.W_OK):
        parser.error(f"output path not writable: {out_path}")

    payload = run_doctor(Path(args.root), args.uid,
                         Path(args.source) if args.source else None)

    report_json = json.dumps(payload, indent=2)
    if len(report_json.encode("utf-8")) > MAX_REPORT_BYTES:
        logger.warning("doctor report exceeds %d bytes; truncating to %d "
                       "findings", MAX_REPORT_BYTES, MAX_REPORT_FINDINGS)
        payload["findings"] = payload["findings"][:MAX_REPORT_FINDINGS]
        payload["findings_truncated"] = True
        report_json = json.dumps(payload, indent=2)
    try:
        out_path.write_text(report_json, encoding="utf-8")
    except OSError as e:
        print(f"error: could not write report to {out_path}: {e}",
              file=sys.stderr)
        sys.exit(2)

    print(f"run doctor: {args.uid}")
    for f in payload["findings"]:
        print(f"  [{f['severity']}] {f['lint']}: {f['message']}")
        for line in f["evidence"][:3]:
            print(f"      - {line}")
    counts = payload["counts"]
    print(f"\n{counts['ERROR']} error(s), {counts['WARN']} warning(s), "
          f"{counts['INFO']} info -> {out_path}")
    sys.exit(1 if counts["ERROR"] or counts["WARN"] else 0)


if __name__ == "__main__":
    main()
