"""Lint for WCM template fields the submitter left for someone else to fill.

One responsibility: read the rendered mentee tables (N3A/N3B, one two-column
table per mentee, `stage6/sections/mentoring.py`) and report a field the WCM
template asks for that the document leaves blank or fills with a placeholder
("N/A", "none"). WCM wants every mentee field completed; a reviewer of run
XAXVYB, whose CV wrote N/A for every past mentee's Current Position, said the
copy should have asked for it (#1941).

The finding is a request to the submitter, not a CViche defect: INFO, and
review-copy only (`run_quality_report.REVIEW_COPY_ONLY_LINTS`), where it is a
comment on the first blank cell. One finding per heading and field, not per
table, so twenty-nine blank Current Position cells are one comment that says
the same field is blank in the other tables too.
"""
import re
from collections.abc import Sequence

from unified_pipeline.stage6.sections.mentoring import (
    CURRENT_MENTEES_HEADING,
    MENTEE_CURRENT_POSITION_LABEL,
    MENTEE_NAME_LABEL,
    MENTEE_PROJECT_LABEL,
    MENTEE_TABLE_LABELS,
    PAST_MENTEES_HEADING,
)

from ..shared import _finding

MENTEE_FIELD_LINT = "mentee_field_incomplete"
MENTEE_FIELD_SEVERITY = "INFO"
#: What a cell may hold and still be unfilled: blank, or a placeholder for
#: "nothing to say", compared casefolded with surrounding dots and spaces cut.
UNFILLED_CELL_VALUES = frozenset({
    "", "n/a", "na", "n.a", "none", "not applicable", "-", "–", "—",
})
#: Rows not asked for. The template's optional row ("**Optional: List
#: publications, awards ..."), and the name: a table whose name is blank or
#: "N/A" is no mentee but the submitter's "N/A" for a subsection with none,
#: which WCM asks for (XAXVYB's reviewer, on other empty subsections), so the
#: whole table is skipped (`_mentee_table`).
_OPTIONAL_LABELS = frozenset({MENTEE_PROJECT_LABEL, MENTEE_NAME_LABEL})
#: Rows the template asks for under Past Mentees only. Under Current Mentees
#: the template's row there is "Goals/expected Outcomes", which stage 6 does
#: not render, so a current mentee's blank Current Position is not asked for.
_PAST_ONLY_LABELS = frozenset({MENTEE_CURRENT_POSITION_LABEL})
#: The heading a group of mentee tables sits under, as the message names it.
PAST_GROUP = "Past Mentees"
CURRENT_GROUP = "Current Mentees"
#: Tables under neither heading (an older template, a fallback anchor): the
#: past-only rows are not asked for, since nothing says the mentee is past.
UNPLACED_GROUP = "mentee"
_GROUP_BY_HEADING = {
    PAST_MENTEES_HEADING.casefold(): PAST_GROUP,
    CURRENT_MENTEES_HEADING.casefold(): CURRENT_GROUP,
}
#: The message's locator, which the review copy anchors the comment by: the
#: index of the first table (among the document's top-level tables, as
#: `docx_table_rows` lists them) and the label of the row left blank.
MENTEE_FIELD_LOCATOR_RE = re.compile(r"^table (?P<table>\d+), row '(?P<label>[^']+)':")
#: Mentee names quoted as evidence per finding.
MENTEE_FIELD_EVIDENCE_LIMIT = 5


def _is_unfilled(value: str) -> bool:
    """True when a cell holds nothing, or only a placeholder for nothing."""
    return value.strip().strip(".").strip().casefold() in UNFILLED_CELL_VALUES


def _mentee_table(rows: list[list[str]]) -> dict[str, str] | None:
    """{label: value} of a rendered mentee table, or None when the table's
    label column is not exactly `MENTEE_TABLE_LABELS`, or it names no
    mentee (a placeholder table: every field "N/A" for an empty subsection)."""
    if any(len(row) != 2 for row in rows):
        return None
    if tuple(row[0].strip() for row in rows) != MENTEE_TABLE_LABELS:
        return None
    table = {row[0].strip(): row[1] for row in rows}
    return None if _is_unfilled(table[MENTEE_NAME_LABEL]) else table


def _table_groups(blocks: Sequence[tuple[str, str]] | None, table_count: int) -> list[str]:
    """The mentee heading each top-level table sits under, by body order:
    the last Current/Past Mentees heading paragraph before it. All
    `UNPLACED_GROUP` when there are no blocks, or their tables do not line
    up one to one with the table rows."""
    groups: list[str] = []
    group = UNPLACED_GROUP
    for kind, text in blocks or ():
        if kind == "p":
            group = _GROUP_BY_HEADING.get(text.strip().casefold(), group)
        elif kind == "table":
            groups.append(group)
    return groups if len(groups) == table_count else [UNPLACED_GROUP] * table_count


def _required_labels(group: str) -> tuple[str, ...]:
    """The rows the template asks for in a mentee table under ``group``."""
    skipped = _OPTIONAL_LABELS | (frozenset() if group == PAST_GROUP else _PAST_ONLY_LABELS)
    return tuple(label for label in MENTEE_TABLE_LABELS if label not in skipped)


def lint_mentee_field_incomplete(table_rows: list[list[list[str]]],
                                 blocks: list[tuple[str, str]] | None) -> list[dict]:
    """One INFO finding per mentee heading and required row that some mentee
    table under that heading leaves blank or N/A (#1941). The message names
    the first such table and row (`MENTEE_FIELD_LOCATOR_RE`) and how many of
    the heading's tables leave it unfilled; the evidence names their mentees."""
    groups = _table_groups(blocks, len(table_rows))
    unfilled: dict[tuple[str, str], list[tuple[int, str]]] = {}
    tables_per_group: dict[str, int] = {}
    for at, rows in enumerate(table_rows):
        table = _mentee_table(rows)
        if table is None:
            continue
        group = groups[at]
        tables_per_group[group] = tables_per_group.get(group, 0) + 1
        for label in _required_labels(group):
            if _is_unfilled(table[label]):
                unfilled.setdefault((group, label), []).append((at, table[MENTEE_NAME_LABEL]))
    findings = []
    for (group, label), hits in unfilled.items():
        first = hits[0][0]
        findings.append(_finding(  # the key as a literal: the registry test reads it by AST
            "mentee_field_incomplete", MENTEE_FIELD_SEVERITY,
            f"table {first}, row '{label}': blank or N/A in {len(hits)} of "
            f"{tables_per_group[group]} {group} tables -- WCM asks for every mentee field",
            [name.strip() for _, name in hits[:MENTEE_FIELD_EVIDENCE_LIMIT] if name.strip()]))
    return findings
