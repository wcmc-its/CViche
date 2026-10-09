"""Each lint's measured precision, read from PRECISION.md (#819).

PRECISION.md is the one source of truth: a hand-check that does not update it
did not happen. This module reads its "Per-lint precision" table so the doctor
can print a lint's precision next to its name (doctor.tsv, the Teams card) and
so #813's remediation can ask whether a lint has earned the right to drive a
retry. Nothing here changes what a lint finds or how severe it is.

What is parsed, and nothing more: the first markdown table under the heading
that starts with "Per-lint precision". Its header row must have a `lint`
column and a `TP / judged` column; a `measured` column is read when present.
Columns are found by header name, so a column added or reordered does not
break the read. A `TP / judged` cell is either `none` or `<tp> / <judged>`
(an optional `(NN%)` after it is ignored). A lint cell is a backticked key,
optionally followed by `: ` and a backticked message shape
(`stage6_render_warnings`: `appendix_no_route`). Shape rows of one lint key are
pooled into that key's precision, since a finding carries the key, not the
shape. A lint listed twice keeps its first row.

The review copy asks one finding at a time (#1589), so it reads the rows
unpooled: `finding_precision` takes the row of the finding's own shape. A
`stage6_render_warnings` message is named by `_STAGE6_SHAPES`; any other
lint's shape is the row's shape name written in its message
("... (owner_pi_role_empty, #1403)"), and a message naming none reads as its
lint's pooled rows.

The gate also reads the "Held-out precision (YUY-HO)" table: `load_gate_ledger`
adds its held-out verdicts to the in-sample rows of every lint whose code has
not changed since the dev-259 doctor they judged (`HELD_OUT_CHANGED`). It is
the one ledger for #1589's consumers to share: the review copy's gate, the
run page's Fix list and the quality score's precision weights (#1595).

A missing or unreadable file is logged and yields an empty ledger: the doctor
still runs, and every lint reads as unmeasured.

Comment-fate verdicts (#1654) can be folded into the rows by
`fold_review_verdicts`, at a stated weighting; the gate does not do so.

Imports: the standard library only. Imported by `run_doctor`, by the review
copy (`web_interface/backend/app/services/review_comments.py`, for
`shown_in_place`, `load_gate_ledger` and `finding_shape`), by
`doctor/comment_fate.py` and the backend's review_loop_service (the verdict
names, `finding_shape`), by `web_interface/backend/scripts/review_labels.py`
(`fold_review_verdicts`), by the run page's Fix list
(`web_interface/backend/app/services/run_quality_report.py`, for the same
gate and `finding_precision`), by `quality_score` (for `finding_precision`
and `load_gate_ledger`), by `scripts/doctor_vs_autopsy.py` (for `stage6_shape`)
and by `scripts/doctor_one.py`.
"""
from __future__ import annotations

import functools
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The ledger this module reads, next to it in the package.
PRECISION_LEDGER_PATH = Path(__file__).with_name("PRECISION.md")

#: The label for a lint with no hand-checked verdicts.
UNMEASURED_LABEL = "p unmeasured"

#: The lint key that re-emits about twenty unrelated stage-6 checks. Its rows
#: are per message shape, and the shapes share nothing, so a message of no
#: known shape is unmeasured rather than read as the pool of the others.
STAGE6_LINT = "stage6_render_warnings"
STAGE6_MESSAGE_PREFIX = "stage 6 self-check: "
STAGE6_OTHER_SHAPE = "other"
#: stage6_render_warnings message -> shape, first match wins. Read from the
#: emitters: stage6/sections/appendix.py `_diversion_message`,
#: stage_6_word_template.py `reroute_warnings` and `_validate_output`,
#: stage6/fan_out.py, stage6/sections/{memberships,board_certification}.py.
#: scripts/doctor_vs_autopsy.py scores by the same table.
_STAGE6_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (shape, re.compile(pattern)) for shape, pattern in (
        ("reroute_refused", r"reroute .*refused"),
        ("reroute_cross_family", r"reroute .*accepted cross family"),
        ("reroute_same_family", r"reroute .*accepted same family"),
        ("appendix_recovered_A", r"^A: .*recovered into the Appendix"),
        ("appendix_recovered", r"recovered into the Appendix"),
        ("appendix_no_route_T", r"^T: .*no stage 6 section is routed"),
        ("appendix_no_route", r"no stage 6 section is routed"),
        ("appendix_grant_too_sparse", r"too sparse to table"),
        ("appendix_t_validation_recoded", r"T-validation recoded"),
        ("appendix_invalid_code", r"quarantined"),
        ("appendix_passthrough_refused", r"refused by the passthrough writer"),
        ("appendix_section_declined", r"not placed by the section routed"),
        ("appendix_no_research_summary", r"no research summary rendered"),
        ("appendix_m1_not_in_summary", r"research summary does not reproduce"),
        ("no_teaching_content", r"No visible bulleted content"),
        ("semicolon_fused_bullets", r"combined with semicolons"),
        ("bare_dates_in_table", r"rows have bare dates"),
        ("board_cert_row_skipped", r"reconstructed board certification row"),
        ("memberships_header_row_dropped", r"dropped as a source table header row"),
        ("fanout_list_not_split", r"holds (?:several )?records that were not split"),
        ("geo_scope_failed", r"geographic scope classification"),
        ("appendix_reclassification_failed", r"appendix entry reclassification"),
        ("section_failed", r"^section .* failed:"),
    ))

#: #1589's gate: a finding whose shape is hand-checked below this precision is
#: not marked on the text it is about (the review copy lists it in its closing
#: box instead). Paul, 2026-10-08: a lint at about 50% is a review-copy
#: comment, so the bar is "below half", not "below the WARN bar".
IN_PLACE_MIN_PRECISION = 0.50

#: The heading (prefix) of the section whose first table is the ledger.
_TABLE_HEADING_PREFIX = "per-lint precision"
_LINT_COLUMN = "lint"
_PRECISION_COLUMN = "tp / judged"
_MEASURED_COLUMN = "measured"
#: The held-out table: YUYVIG's verdicts on the dev-259 doctor (#1586).
_HELD_OUT_HEADING_PREFIX = "held-out precision"
_HELD_OUT_COLUMN = "held-out tp / judged"
_HELD_OUT_IN_SAMPLE_COLUMN = "in-sample tp / judged"
HELD_OUT_MEASUREMENT = "YUY-HO"

#: Held-out rows that judged code since changed, so the gate does not fold
#: them in. Owner decision (Paul, 2026-10-08): fold YUY-HO into a lint's
#: precision only when its code is unchanged since dev-259 (`d1e49e39`), by
#: `git log d1e49e39..origin/dev` on doctor/lints/*.py and the stage-6 code
#: that emits a `stage6_render_warnings` shape. A shape of None is the whole
#: lint. A PR that changes one of the other lints adds it here.
HELD_OUT_CHANGED: frozenset[tuple[str, str | None]] = frozenset({
    ("dedup_drops", None),                 # #666: new WARN classes, every drop listed
    ("year_not_in_source", None),          # #1585: `_source_two_digit_years`
    ("span_count", None),                  # #1585: month comma, wrapped ranges
    ("role_consistency", None),            # #1590: tables matched per entry, counted
    ("summary_unsupported_claim", None),   # #1592: its helpers renamed
    ("owner_attribution", None),           # new after dev-259 (#1573)
    ("source_line_coverage", None),        # new after dev-259 (#1588); its row already pools it
    ("grant_facts", None),                 # new after dev-259 (#1588); its rows already pool it
    (STAGE6_LINT, "appendix_m1_not_in_summary"),  # #1572: `_m1_record_ids`
    (STAGE6_LINT, "appendix_no_route"),    # #1574: a bare N3 now routes to a mentee table
    (STAGE6_LINT, STAGE6_OTHER_SHAPE),     # #1574: adds the INFO bare-mentee message
})

_LINT_CELL_RE = re.compile(r"^`(?P<lint>[a-z0-9_]+)`(?:\s*:\s*`(?P<shape>[^`]+)`)?")
_RATIO_CELL_RE = re.compile(r"^(?P<tp>\d+)\s*/\s*(?P<judged>\d+)\b")

#: #813's scope: the LLM-judgement lints a section retry could fix. The
#: deterministic-defect lints are out of scope there by decision, whatever
#: their precision.
REMEDIATION_CANDIDATE_LINTS = frozenset({"missed_headers", "segmentation", "under_extraction"})
#: #813's gate: a lint may drive a retry only at or above this hand-checked
#: precision ("precision still ~50% -> fix the lint first"). Same bar the
#: EBYSBC wave set for shipping a new lint at WARN.
REMEDIATION_MIN_PRECISION = 0.80
#: ... and only on at least this many hand-checked findings, so one lucky
#: finding cannot open the gate.
REMEDIATION_MIN_JUDGED = 20


@dataclass(frozen=True)
class LintPrecision:
    """One lint key's hand-checked precision, pooled over its shape rows."""

    lint: str
    true_positives: int
    judged: int
    measured: str  # measurement id(s), e.g. "M1"; "" when the column is absent
    shape: str | None = None  # the row's message shape; None for a lint's own or pooled row

    @property
    def precision(self) -> float | None:
        """TP / judged, or None when nothing was judged."""
        return self.true_positives / self.judged if self.judged else None


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _ledger_table_rows(text: str, heading: str = _TABLE_HEADING_PREFIX) -> list[dict[str, str]]:
    """The data rows of the first table under the heading starting with
    ``heading``, as {lowercased header: cell}."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.lstrip("#").strip().lower().startswith(heading)
                  and line.startswith("#")), None)
    if start is None:
        return []
    table = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            break
        if line.strip().startswith("|"):
            table.append(_cells(line))
        elif table:
            break  # the first table has ended
    if not table:
        return []
    header = [cell.lower() for cell in table[0]]
    # The |---| separator row falls out later: its lint cell is no lint key.
    return [dict(zip(header, row)) for row in table[1:]]


#: A ledger row's key: its lint, and its message shape (None for a lint's own row).
ShapeKey = tuple[str, str | None]


def parse_shape_ledger(text: str) -> dict[ShapeKey, LintPrecision]:
    """Parse PRECISION.md's text into {(lint, shape): LintPrecision}, one per
    row, unpooled. A row listed twice keeps its first entry; a `none` row is
    listed with judged == 0."""
    rows: dict[ShapeKey, LintPrecision] = {}
    for row in _ledger_table_rows(text):
        lint_match = _LINT_CELL_RE.match(row.get(_LINT_COLUMN, ""))
        if not lint_match:
            continue
        key = (lint_match["lint"], lint_match["shape"])
        if key in rows:
            continue
        tp, judged = _ratio(row.get(_PRECISION_COLUMN, ""))
        rows[key] = LintPrecision(key[0], tp, judged, row.get(_MEASURED_COLUMN, ""), key[1])
    return rows


def _ratio(cell: str) -> tuple[int, int]:
    """(tp, judged) of a `TP / judged` cell; (0, 0) for `none`."""
    match = _RATIO_CELL_RE.match(cell)
    return (int(match["tp"]), int(match["judged"])) if match else (0, 0)


#: A held-out table row: its in-sample and held-out (tp, judged).
HeldOutRow = tuple[tuple[int, int], tuple[int, int]]


def parse_held_out(text: str) -> dict[ShapeKey, HeldOutRow]:
    """PRECISION.md's held-out table as {(lint, shape): (in-sample, held-out)},
    each a (tp, judged) pair. A `none` cell is (0, 0)."""
    rows: dict[ShapeKey, HeldOutRow] = {}
    for row in _ledger_table_rows(text, _HELD_OUT_HEADING_PREFIX):
        lint_match = _LINT_CELL_RE.match(row.get(_LINT_COLUMN, ""))
        if lint_match:
            rows.setdefault((lint_match["lint"], lint_match["shape"]),
                            (_ratio(row.get(_HELD_OUT_IN_SAMPLE_COLUMN, "")),
                             _ratio(row.get(_HELD_OUT_COLUMN, ""))))
    return rows


def fold_held_out(rows: dict[ShapeKey, LintPrecision],
                  held_out: dict[ShapeKey, HeldOutRow]) -> dict[ShapeKey, LintPrecision]:
    """``rows`` with each held-out verdict added to the in-sample row it
    measured: combined TP / judged = in-sample + held-out, for a lint not in
    HELD_OUT_CHANGED; in-sample only for one that is.

    A held-out row with no in-sample row of its key becomes one, with the
    held-out table's own in-sample figures (`owner_missing_from_citation`'s
    are the M3 cap table's). A lint-level held-out row of a lint the
    in-sample table splits by shape is not folded: the gate reads one shape
    at a time, and the pooled verdicts cannot be split between them.
    """
    combined = dict(rows)
    split = {lint for lint, shape in rows if shape is not None}
    for key, (in_sample, (tp, judged)) in held_out.items():
        lint, shape = key
        if (not judged or key in HELD_OUT_CHANGED or (lint, None) in HELD_OUT_CHANGED
                or (shape is None and lint in split)):
            continue
        base = combined.get(key) or LintPrecision(lint, *in_sample, "", shape)
        measured = ",".join(m for m in (base.measured, HELD_OUT_MEASUREMENT) if m)
        combined[key] = LintPrecision(lint, base.true_positives + tp, base.judged + judged,
                                      measured, shape)
    return combined


#: Comment-fate verdicts (#1654, `doctor/comment_fate.py`): what a reviewer
#: did at a review-copy comment, read from their corrected copy.
REVIEW_FIXED = "fixed"
REVIEW_NOT_A_PROBLEM = "not_a_problem"
REVIEW_UNKNOWN = "unknown"
REVIEW_VERDICTS = (REVIEW_FIXED, REVIEW_NOT_A_PROBLEM, REVIEW_UNKNOWN)
#: The measurement id a row carries once review verdicts are folded into it.
REVIEW_MEASUREMENT = "REVIEW"


def fold_review_verdicts(rows: dict[ShapeKey, LintPrecision],
                         counts: dict[ShapeKey, dict[str, int]]) -> dict[ShapeKey, LintPrecision]:
    """``rows`` with comment-fate verdict counts added, per (lint, shape).

    The weighting: one verdict counts as one hand-checked verdict. `fixed`
    (the reviewer changed the text the comment was on) is a true positive;
    `not_a_problem` (the comment dismissed, the text untouched) is a false
    positive; `unknown` is not judged and adds nothing. A key with no ledger
    row becomes one, measured on its verdicts alone.

    Off for the gate: `load_gate_ledger` does not call this, so the review
    copy, the Fix list and the quality score read hand-checked verdicts only.
    Nothing reads these counts until a hand-checked sample of comment-fate
    verdicts has measured them (#1654).
    """
    combined = dict(rows)
    for key, verdicts in counts.items():
        tp = verdicts.get(REVIEW_FIXED, 0)
        judged = tp + verdicts.get(REVIEW_NOT_A_PROBLEM, 0)
        if not judged:
            continue
        base = combined.get(key) or LintPrecision(key[0], 0, 0, "", key[1])
        measured = ",".join(m for m in (base.measured, REVIEW_MEASUREMENT) if m)
        combined[key] = LintPrecision(key[0], base.true_positives + tp, base.judged + judged,
                                      measured, key[1])
    return combined


def _pooled(rows: dict[ShapeKey, LintPrecision]) -> dict[str, LintPrecision]:
    """Each lint's rows summed into one entry, its measurement ids joined in row order."""
    pooled: dict[str, tuple[int, int, list[str]]] = {}
    for (lint, _shape), row in rows.items():
        tp, judged, measured = pooled.get(lint, (0, 0, []))
        if row.measured and row.measured not in measured:
            measured = [*measured, row.measured]
        pooled[lint] = (tp + row.true_positives, judged + row.judged, measured)
    return {lint: LintPrecision(lint, tp, judged, ",".join(measured))
            for lint, (tp, judged, measured) in pooled.items()}


def parse_ledger(text: str) -> dict[str, LintPrecision]:
    """Parse PRECISION.md's text into {lint key: LintPrecision}, shape rows pooled.

    A lint whose rows are all `none` is still listed, with judged == 0, so a
    caller can tell "measured, no verdicts" from "not in the ledger".
    """
    return _pooled(parse_shape_ledger(text))


@functools.lru_cache(maxsize=None)
def _ledger_text(path: Path) -> str:
    """The ledger file's text, read once per process; '' (and logged) when
    it cannot be read."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        logger.warning("lint precision ledger unreadable at %s; every lint reads as unmeasured",
                       path, exc_info=True)
        return ""


@functools.lru_cache(maxsize=None)
def load_shape_ledger(path: Path = PRECISION_LEDGER_PATH) -> dict[ShapeKey, LintPrecision]:
    """The parsed ledger's rows, unpooled, read once per process. Empty (and
    logged) when the file cannot be read."""
    text = _ledger_text(path)
    if not text:
        return {}
    rows = parse_shape_ledger(text)
    if not rows:
        logger.warning("lint precision ledger at %s has no parseable per-lint table", path)
    return rows


@functools.lru_cache(maxsize=None)
def load_gate_ledger(path: Path = PRECISION_LEDGER_PATH) -> dict[ShapeKey, LintPrecision]:
    """The rows #1589's gate reads: `load_shape_ledger`'s, with the held-out
    verdicts of unchanged lints folded in (`fold_held_out`). Call this, not
    `load_shape_ledger`, to judge a finding as the review copy does."""
    return fold_held_out(load_shape_ledger(path), parse_held_out(_ledger_text(path)))


@functools.lru_cache(maxsize=None)
def load_ledger(path: Path = PRECISION_LEDGER_PATH) -> dict[str, LintPrecision]:
    """The parsed ledger, shape rows pooled per lint key."""
    return _pooled(load_shape_ledger(path))


def stage6_shape(message: str) -> str:
    """The `_STAGE6_SHAPES` name of one stage-6 warning message."""
    text = message.removeprefix(STAGE6_MESSAGE_PREFIX)
    for shape, pattern in _STAGE6_SHAPES:
        if pattern.search(text):
            return shape
    return STAGE6_OTHER_SHAPE


def finding_precision(lint: str, message: str,
                      rows: dict[ShapeKey, LintPrecision] | None = None) -> LintPrecision | None:
    """The ledger row that measured findings like this one, or None when none
    did. ``rows`` defaults to the gate's (`load_gate_ledger`).

    A stage-6 message reads its `_STAGE6_SHAPES` row only. Another lint reads
    the row of the shape its message names, else its own row, else its shape
    rows pooled (a message that names no shape).
    """
    rows = load_gate_ledger() if rows is None else rows
    if lint == STAGE6_LINT:
        return rows.get((lint, stage6_shape(message)))
    own = {key: row for key, row in rows.items() if key[0] == lint}
    named = next((row for (_, shape), row in own.items()
                  if shape and re.search(rf"(?<!\w){re.escape(shape)}(?!\w)", message)), None)
    return named or own.get((lint, None)) or _pooled(own).get(lint)


def finding_shape(lint: str, message: str,
                  rows: dict[ShapeKey, LintPrecision] | None = None) -> str | None:
    """The message shape a finding is judged by: a `stage6_render_warnings`
    message's `_STAGE6_SHAPES` name, else the shape of the ledger row
    `finding_precision` reads for it (None for a lint's own or pooled row)."""
    if lint == STAGE6_LINT:
        return stage6_shape(message)
    row = finding_precision(lint, message, rows)
    return row.shape if row else None


def shown_in_place(lint: str, message: str,
                   rows: dict[ShapeKey, LintPrecision] | None = None) -> bool:
    """#1589's gate: may this finding be marked on the text it is about?

    Not when its row's hand-checked precision is below IN_PLACE_MIN_PRECISION.
    An unmeasured finding may: no verdicts is not a low precision.
    """
    entry = finding_precision(lint, message, rows)
    return entry is None or entry.precision is None or entry.precision >= IN_PLACE_MIN_PRECISION


def remediation_allowed(lint: str, ledger: dict[str, LintPrecision] | None = None) -> bool:
    """#813's entry gate: may this lint's finding drive a section retry?

    Only a remediation-candidate lint, hand-checked on at least
    REMEDIATION_MIN_JUDGED findings at a precision of at least
    REMEDIATION_MIN_PRECISION. An unmeasured lint is never allowed.
    """
    if lint not in REMEDIATION_CANDIDATE_LINTS:
        return False
    entry = (load_ledger() if ledger is None else ledger).get(lint)
    if entry is None or entry.judged < REMEDIATION_MIN_JUDGED:
        return False
    return entry.precision is not None and entry.precision >= REMEDIATION_MIN_PRECISION


def precision_label(entry: LintPrecision | None) -> str:
    """A short ASCII label for a lint's precision: `p~0.50 n=8` (TP / judged,
    and judged), or `p unmeasured` when the ledger has no verdicts for it."""
    if entry is None or entry.precision is None:
        return UNMEASURED_LABEL
    return f"p~{entry.precision:.2f} n={entry.judged}"


def precision_payload(lints: Iterable[str], ledger: dict[str, LintPrecision] | None = None) -> dict[str, dict]:
    """The run report's `lint_precision` block: one entry per lint key given.

    `precision` is None for a lint the ledger has no verdicts for. `label` is
    what scripts/doctor_one.py (doctor.tsv) and the backend's Teams card print
    next to the lint name, formatted here so the two cannot drift.
    """
    ledger = load_ledger() if ledger is None else ledger
    payload = {}
    for lint in sorted(set(lints)):
        entry = ledger.get(lint)
        payload[lint] = {
            "precision": entry.precision if entry else None,
            "judged": entry.judged if entry else 0,
            "measured": entry.measured if entry else "",
            "label": precision_label(entry),
            "remediation_allowed": remediation_allowed(lint, ledger),
        }
    return payload
