#!/usr/bin/env python3
"""The review loop's report (#1587): per-lint precision from reviewers'
verdicts, beside doctor/PRECISION.md's hand-checked figure, then the misses
reviewers reported.

    cd web_interface/backend
    python3 scripts/review_verdict_report.py

Precision counts findings, not groups: a verdict covers every finding of its
group (`feedback_verdicts.finding_count`), and "Can't tell" is listed but not
scored. Misses are the problems reviewers ticked on the feedback form (with
how many said where) and the changes in stored corrected-copy diffs
(`review_loop_service.corrected_diff_key`), by change type.

Read-only. Run it where the app's DB and storage settings are configured (a
prod/dev pod). stdout is for people, not a contract.
"""
import json
import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.models import Feedback, FeedbackVerdict, ReviewVerdict  # noqa: E402
from app.services.review_loop_service import corrected_diff_key  # noqa: E402
from app.storage import get_storage  # noqa: E402
from unified_pipeline.doctor.docx_diff import CHANGE_TYPES  # noqa: E402
from unified_pipeline.doctor.precision import load_ledger, precision_label  # noqa: E402

logger = logging.getLogger("review_verdict_report")

#: The feedback form's problem fields (feedbackQuestions.ts ISSUE_FIELDS),
#: each with its reader.
ISSUE_COLUMNS: dict[str, Callable[[Feedback], str | None]] = {
    "issue_missing_content": lambda row: row.issue_missing_content,
    "issue_split_merged": lambda row: row.issue_split_merged,
    "issue_wrong_section": lambda row: row.issue_wrong_section,
    "issue_inaccurate": lambda row: row.issue_inaccurate,
    "issue_ai_enrichment": lambda row: row.issue_ai_enrichment,
    "issue_formatting": lambda row: row.issue_formatting,
}


@dataclass
class LintVerdicts:
    """Findings per verdict for one lint, summed over its groups."""
    lint: str
    fixed: int = 0
    not_a_problem: int = 0
    cant_tell: int = 0

    @property
    def precision(self) -> float | None:
        judged = self.fixed + self.not_a_problem
        return self.fixed / judged if judged else None


def lint_verdicts(rows: Iterable[FeedbackVerdict]) -> list[LintVerdicts]:
    """Per-lint finding counts by verdict, lints in name order."""
    by_lint: dict[str, LintVerdicts] = {}
    for row in rows:
        entry = by_lint.setdefault(row.lint, LintVerdicts(row.lint))
        verdict = ReviewVerdict(row.verdict)  # a stored value outside the enum raises
        if verdict is ReviewVerdict.FIXED:
            entry.fixed += row.finding_count
        elif verdict is ReviewVerdict.NOT_A_PROBLEM:
            entry.not_a_problem += row.finding_count
        else:
            entry.cant_tell += row.finding_count
    return [by_lint[lint] for lint in sorted(by_lint)]


def ticked_problems(feedback: Iterable[Feedback]) -> dict[str, tuple[int, int]]:
    """Per problem field: (reviews that ticked it, of those how many said
    where -- a "Which entries?" answer or a section picked)."""
    out = {column: (0, 0) for column in ISSUE_COLUMNS}
    for row in feedback:
        for column, read in ISSUE_COLUMNS.items():
            answer = read(row)
            if answer is None:
                continue
            ticked, located = out[column]
            out[column] = (ticked + 1, located + int(bool(answer.strip() or row.issue_locations)))
    return out


def diff_changes(run_ids: Iterable[str]) -> tuple[int, dict[str, int]]:
    """How many of these runs have a stored corrected-copy diff, and their
    changes summed by type."""
    storage = get_storage()
    runs, totals = 0, dict.fromkeys(CHANGE_TYPES, 0)
    for run_id in sorted(set(run_ids)):
        try:
            report = json.loads(storage.get_file(run_id, corrected_diff_key(run_id)))
        except FileNotFoundError:
            continue
        runs += 1
        for kind in CHANGE_TYPES:
            totals[kind] += int(report.get("by_type", {}).get(kind, 0))
    return runs, totals


def render(verdicts: list[LintVerdicts], ticked: dict[str, tuple[int, int]],
           diffs: tuple[int, dict[str, int]]) -> str:
    """The report as text."""
    ledger = load_ledger()
    lines = ["Per-lint precision from review verdicts (findings)",
             f"{'lint':<32} {'fixed':>6} {'not':>6} {'cant':>6} {'precision':>9}  PRECISION.md"]
    for v in verdicts:
        p = "-" if v.precision is None else f"{v.precision:.2f}"
        lines.append(f"{v.lint:<32} {v.fixed:>6} {v.not_a_problem:>6} {v.cant_tell:>6} {p:>9}  "
                     f"{precision_label(ledger.get(v.lint))}")
    if not verdicts:
        lines.append("(no verdicts stored)")
    lines += ["", "Misses: problems reviewers ticked", f"{'problem':<32} {'ticked':>6} {'where':>6}"]
    lines += [f"{column:<32} {t:>6} {w:>6}" for column, (t, w) in ticked.items()]
    runs, totals = diffs
    lines += ["", f"Misses: corrected-copy diffs ({runs} runs)"]
    lines += [f"{kind:<32} {count:>6}" for kind, count in totals.items()]
    return "\n".join(lines) + "\n"


def build_report(db: Session) -> str:
    feedback = db.query(Feedback).all()
    return render(lint_verdicts(db.query(FeedbackVerdict).all()), ticked_problems(feedback),
                  diff_changes(row.run_id for row in feedback))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        sys.stdout.write(build_report(db))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
