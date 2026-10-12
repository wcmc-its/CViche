"""scripts/corrected_copy_report.py (#1587): stored corrected-copy diffs,
summed by change type over every run. Synthetic data only."""
import importlib.util
import json
from pathlib import Path

from app.models import Run, User

_SCRIPT = Path(__file__).parent.parent / "scripts" / "corrected_copy_report.py"
_spec = importlib.util.spec_from_file_location("corrected_copy_report", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _seed_runs(db, *run_ids):
    user = User(email="reviewer@example.com", display_name="Reviewer", role="user")
    db.add(user)
    db.flush()
    for run_id in run_ids:
        db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status="complete", user_id=user.id))
    db.commit()


def test_report_sums_stored_diffs_by_type_including_runs_without_feedback(db, tmp_path, monkeypatch):
    """No run here has a feedback row: an upload without the form still counts."""
    from app.storage.local_storage import LocalRunStorage

    _seed_runs(db, "REP001", "REP002", "REP003")
    store = LocalRunStorage(str(tmp_path))
    store.put_file("REP001", "corrected/REP001_diff.json",
                   json.dumps({"by_type": {"deleted": 2, "added": 1}}).encode())
    store.put_file("REP003", "corrected/REP003_diff.json",
                   json.dumps({"by_type": {"deleted": 1, "value_edited": 4}}).encode())
    monkeypatch.setattr(script, "get_storage", lambda: store)

    text = script.build_report(db)

    assert "Corrected-copy changes (2 runs)" in text
    rows = {line.split()[0]: int(line.split()[1]) for line in text.splitlines()[1:]}
    assert rows["deleted"] == 3
    assert rows["added"] == 1
    assert rows["value_edited"] == 4
    assert rows["moved"] == 0
