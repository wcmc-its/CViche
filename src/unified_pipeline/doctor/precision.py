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

A missing or unreadable file is logged and yields an empty ledger: the doctor
still runs, and every lint reads as unmeasured.

Imports: the standard library only. Nothing in the pipeline imports this
module except `run_doctor`; the web backend's run page reads `user_visible`.
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

#: The heading (prefix) of the section whose first table is the ledger.
_TABLE_HEADING_PREFIX = "per-lint precision"
_LINT_COLUMN = "lint"
_PRECISION_COLUMN = "tp / judged"
_MEASURED_COLUMN = "measured"

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

#: #1589's visibility gate: a lint hand-checked below this precision is shown
#: to developers only (the run page's Diagnostics tab), never to the person
#: fixing the CV, since it is wrong at least as often as it is right.
USER_VISIBLE_MIN_PRECISION = 0.50


@dataclass(frozen=True)
class LintPrecision:
    """One lint key's hand-checked precision, pooled over its shape rows."""

    lint: str
    true_positives: int
    judged: int
    measured: str  # measurement id(s), e.g. "M1"; "" when the column is absent

    @property
    def precision(self) -> float | None:
        """TP / judged, or None when nothing was judged."""
        return self.true_positives / self.judged if self.judged else None


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _ledger_table_rows(text: str) -> list[dict[str, str]]:
    """The ledger table's data rows as {lowercased header: cell}."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.lstrip("#").strip().lower().startswith(_TABLE_HEADING_PREFIX)
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


def parse_ledger(text: str) -> dict[str, LintPrecision]:
    """Parse PRECISION.md's text into {lint key: LintPrecision}.

    A lint whose rows are all `none` is still listed, with judged == 0, so a
    caller can tell "measured, no verdicts" from "not in the ledger".
    """
    pooled: dict[str, list] = {}
    seen_rows: set[tuple[str, str | None]] = set()
    for row in _ledger_table_rows(text):
        lint_match = _LINT_CELL_RE.match(row.get(_LINT_COLUMN, ""))
        if not lint_match:
            continue
        key = (lint_match["lint"], lint_match["shape"])
        if key in seen_rows:
            continue
        seen_rows.add(key)
        ratio = _RATIO_CELL_RE.match(row.get(_PRECISION_COLUMN, ""))
        tp, judged = (int(ratio["tp"]), int(ratio["judged"])) if ratio else (0, 0)
        entry = pooled.setdefault(lint_match["lint"], [0, 0, []])
        entry[0] += tp
        entry[1] += judged
        measured = row.get(_MEASURED_COLUMN, "")
        if measured and measured not in entry[2]:
            entry[2].append(measured)
    return {lint: LintPrecision(lint, tp, judged, ",".join(measured))
            for lint, (tp, judged, measured) in pooled.items()}


@functools.lru_cache(maxsize=None)
def load_ledger(path: Path = PRECISION_LEDGER_PATH) -> dict[str, LintPrecision]:
    """The parsed ledger, read once per process. Empty (and logged) when the
    file cannot be read."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        logger.warning("lint precision ledger unreadable at %s; every lint reads as unmeasured",
                       path, exc_info=True)
        return {}
    ledger = parse_ledger(text)
    if not ledger:
        logger.warning("lint precision ledger at %s has no parseable per-lint table", path)
    return ledger


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


def user_visible(lint: str, ledger: dict[str, LintPrecision] | None = None) -> bool:
    """#1589's gate: may this lint's findings be shown to the person fixing the
    CV? False only for a lint hand-checked below USER_VISIBLE_MIN_PRECISION. An
    unmeasured lint is shown: the ledger has no evidence it is unreliable, and
    hiding it would hide the rare lints (no_output, protected data) nobody has
    had a hit of to judge."""
    entry = (load_ledger() if ledger is None else ledger).get(lint)
    if entry is None or entry.precision is None:
        return True
    return entry.precision >= USER_VISIBLE_MIN_PRECISION


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
