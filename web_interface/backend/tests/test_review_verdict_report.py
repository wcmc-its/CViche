"""scripts/review_verdict_report.py (#1587): per-lint precision from stored
verdicts, beside PRECISION.md, and the reported misses."""
import importlib.util
import json
from pathlib import Path

from app.models import Feedback, FeedbackVerdict, Run, User

_SCRIPT = Path(__file__).parent.parent / "scripts" / "review_verdict_report.py"
_spec = importlib.util.spec_from_file_location("review_verdict_report", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _seed(db):
    user = User(email="reviewer@example.com", display_name="Reviewer", role="user")
    db.add(user)
    db.flush()
    for run_id in ("REP001", "REP002"):
        db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status="complete", user_id=user.id))
    first = Feedback(run_id="REP001", user_id=user.id, reviewer_role="self", overall_usefulness=3,
                     manual_conversion_effort="0 minutes", correction_effort="0 minutes",
                     likelihood_to_recommend=3, issue_missing_content="", issue_wrong_section="entry 4")
    second = Feedback(run_id="REP002", user_id=user.id, reviewer_role="self", overall_usefulness=3,
                      manual_conversion_effort="0 minutes", correction_effort="0 minutes",
                      likelihood_to_recommend=3, issue_missing_content="", issue_locations='["H"]')
    db.add_all([first, second])
    db.flush()
    db.add_all([
        FeedbackVerdict(feedback_id=first.id, run_id="REP001", lint="junk_or_header_row",
                        finding_count=3, verdict="fixed"),
        FeedbackVerdict(feedback_id=second.id, run_id="REP002", lint="junk_or_header_row",
                        finding_count=1, verdict="not_a_problem"),
        FeedbackVerdict(feedback_id=second.id, run_id="REP002", lint="dead_sections",
                        finding_count=2, verdict="cant_tell"),
        FeedbackVerdict(feedback_id=second.id, run_id="REP002", lint="junk_or_header_row",
                        finding_count=4, verdict="cant_tell"),
    ])
    db.commit()


def test_precision_counts_findings_and_leaves_cant_tell_unscored(db):
    _seed(db)
    [dead, junk] = script.lint_verdicts(db.query(FeedbackVerdict).all())
    assert (junk.lint, junk.fixed, junk.not_a_problem, junk.cant_tell, junk.precision) == (
        "junk_or_header_row", 3, 1, 4, 0.75)
    assert (dead.lint, dead.cant_tell, dead.precision) == ("dead_sections", 2, None)


def test_ticked_problems_count_who_said_where(db):
    _seed(db)
    ticked = script.ticked_problems(db.query(Feedback).all())
    assert ticked["issue_missing_content"] == (2, 1)  # REP002 picked a section, REP001 said nothing
    assert ticked["issue_wrong_section"] == (1, 1)
    assert ticked["issue_formatting"] == (0, 0)


def test_report_reads_stored_diffs_and_prints_precision_beside_the_ledger(db, tmp_path, monkeypatch):
    from app.storage.local_storage import LocalRunStorage

    _seed(db)
    store = LocalRunStorage(str(tmp_path))
    store.put_file("REP001", "corrected/REP001_diff.json",
                   json.dumps({"by_type": {"deleted": 2, "added": 1}}).encode())
    monkeypatch.setattr(script, "get_storage", lambda: store)

    text = script.build_report(db)

    junk = next(line for line in text.splitlines() if line.startswith("junk_or_header_row"))
    assert junk.split()[:5] == ["junk_or_header_row", "3", "1", "4", "0.75"]
    assert "p~" in junk  # PRECISION.md's own figure beside it
    assert "corrected-copy diffs (1 runs)" in text
    assert [line.split() for line in text.splitlines() if line.startswith(("deleted", "added"))] == [
        ["deleted", "2"], ["added", "1"]]
