"""scripts/review_labels.py (#1654): one review label per run with a corrected
copy, in doctor_vs_autopsy.py's schema. Synthetic data only."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

from app.models import Run, User
from app.services import review_loop_service as svc
from app.timeutil import utc_now

_BACKEND = Path(__file__).parent.parent
_ROOT = _BACKEND.parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load("review_labels", _BACKEND / "scripts" / "review_labels.py")
doctor_vs_autopsy = _load("doctor_vs_autopsy", _ROOT / "scripts" / "doctor_vs_autopsy.py")

_DIFF = {"uid": "LBL001", "delivered_blocks": 4, "corrected_blocks": 3,
         "delivered_revisions": {"insertions": 0, "deletions": 0, "moves": 0},
         "corrected_revisions": {"insertions": 1, "deletions": 0, "moves": 0},
         "by_type": {"deleted": 1, "added": 0, "moved": 0, "value_edited": 1},
         "changes": [
             {"change_type": "deleted", "section_before": "BIBLIOGRAPHY", "section_after": None,
              "block_before": 2, "block_after": None, "element_idx": 41, "before_chars": 80, "after_chars": 0},
             {"change_type": "value_edited", "section_before": "BIBLIOGRAPHY", "section_after": "BIBLIOGRAPHY",
              "block_before": 3, "block_after": 2, "element_idx": None, "before_chars": 80, "after_chars": 80}]}
_VERDICTS = {"findings": [
    {"comment_id": 0, "lint": "implausible_year", "shape": None, "severity": "ERROR", "entry_index": 41,
     "verdict": "fixed"},
    {"comment_id": 1, "lint": "stage6_render_warnings", "shape": "reroute_refused", "severity": "WARN",
     "entry_index": None, "verdict": "not_a_problem"},
    {"comment_id": 2, "lint": "pipe_leaks", "shape": None, "severity": "WARN", "entry_index": None,
     "verdict": "unknown"}]}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.storage.local_storage import LocalRunStorage

    store = LocalRunStorage(str(tmp_path / "store"))
    monkeypatch.setattr(script, "get_storage", lambda: store)
    monkeypatch.setattr(svc, "get_storage", lambda: store)
    return store


def _seed_runs(db, *run_ids):
    user = User(email="reviewer@example.com", display_name="Reviewer", role="user")
    db.add(user)
    db.flush()
    for run_id in run_ids:
        db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status="complete", user_id=user.id))
    db.commit()


def _docx(*paragraphs):
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_labels_load_in_the_autopsy_label_store(db, store, tmp_path):
    """LBL001 has a diff and verdicts, stored before #1754 under the run's id
    alone (uploader None); LBL002 has neither and gets no label."""
    _seed_runs(db, "LBL001", "LBL002")
    store.put_file("LBL001", "corrected/LBL001_diff.json", json.dumps(_DIFF).encode())
    store.put_file("LBL001", "corrected/LBL001_verdicts.json", json.dumps(_VERDICTS).encode())

    labels = script.write_labels(db, tmp_path / "labels")

    assert [label["uid"] for label in labels] == ["LBL001"]
    loaded = doctor_vs_autopsy.load_labels(tmp_path / "labels")
    assert loaded.uids == {"LBL001"}
    assert [(v.id, v.batch, v.batch_class, v.idxs) for v in loaded.verified] == [
        ("LBL001-R01", "review", "deleted", frozenset({41})), ("LBL001-R02", "review", "value_edited", frozenset())]
    assert [(r.key, r.verdict) for r in loaded.reviews] == [
        ("implausible_year", "TP"), ("stage6_render_warnings:reroute_refused", "FP")]


def test_a_copy_uploaded_before_its_verdicts_gets_them_on_the_way(db, store, tmp_path):
    _seed_runs(db, "LBL003")
    store.put_file("LBL003", "outputs/LBL003_wcm.docx", _docx("BIBLIOGRAPHY", "A synthetic line."))
    svc.record_corrected_docx(db, "LBL003", 5, _docx("BIBLIOGRAPHY"), utc_now())

    label, = script.write_labels(db, tmp_path / "labels")

    assert [f["batch_class"] for f in label["findings"]] == ["deleted"] and label["doctor_review"] == []
    assert json.loads(store.get_file("LBL003", svc.corrected_verdicts_key("LBL003", 5)))["note"]
    assert store.get_file("LBL003", svc.corrected_doctor_key("LBL003", 5))


def test_one_label_holds_every_uploaders_copy_and_a_pre_1754_one(db, store, tmp_path):
    """#1754: LBL004 has a copy under the run's id alone and two users' copies
    (owner and staff); its one label carries all three copies' changes and
    verdicts, numbered once."""
    _seed_runs(db, "LBL004")
    for uploader_id in (None, 3, 8):
        store.put_file("LBL004", svc.corrected_diff_key("LBL004", uploader_id), json.dumps(_DIFF).encode())
        store.put_file("LBL004", svc.corrected_verdicts_key("LBL004", uploader_id),
                       json.dumps(_VERDICTS).encode())

    label, = script.write_labels(db, tmp_path / "labels")

    assert [f["id"] for f in label["findings"]] == [f"LBL004-R{n:02d}" for n in range(1, 7)]
    assert len(label["doctor_review"]) == 6
    loaded = doctor_vs_autopsy.load_labels(tmp_path / "labels")
    assert loaded.uids == {"LBL004"} and len(loaded.verified) == 6


def test_a_run_whose_verdicts_are_missing_is_skipped_and_logged(db, store, tmp_path, caplog):
    """LBL005 has a diff but neither verdicts nor the copy to make them from."""
    _seed_runs(db, "LBL001", "LBL005")
    for run_id in ("LBL001", "LBL005"):
        store.put_file(run_id, svc.corrected_diff_key(run_id, 5), json.dumps(_DIFF).encode())
    store.put_file("LBL001", svc.corrected_verdicts_key("LBL001", 5), json.dumps(_VERDICTS).encode())

    labels = script.write_labels(db, tmp_path / "labels")

    assert [label["uid"] for label in labels] == ["LBL001"]
    assert "LBL005" in caplog.text and "skipped" in caplog.text


def test_precision_lines_show_the_gate_beside_the_verdicts_folded_in(monkeypatch):
    from unified_pipeline.doctor.precision import LintPrecision

    monkeypatch.setattr(script, "load_gate_ledger",
                        lambda: {("pipe_leaks", None): LintPrecision("pipe_leaks", 2, 5, "M1")})
    counts = script.verdict_counts([{"doctor_review": [
        {"lint": "pipe_leaks", "shape": None, "verdict": "TP"},
        {"lint": "pipe_leaks", "shape": None, "verdict": "FP"},
        {"lint": "new_lint", "shape": "s", "verdict": "TP"}]}])
    assert counts == {("pipe_leaks", None): {"fixed": 1, "not_a_problem": 1}, ("new_lint", "s"): {"fixed": 1}}
    lines = script.precision_lines(counts)
    assert lines[1].split() == ["new_lint:s", "unmeasured", "1/1"]
    assert lines[2].split() == ["pipe_leaks", "2/5", "3/7"]


def test_no_corrected_copy_anywhere_exits_2(db, store, tmp_path, monkeypatch):
    _seed_runs(db, "LBL004")
    monkeypatch.setattr("app.database.SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    assert script.main([str(tmp_path / "labels")]) == script.EXIT_NOTHING_TO_LABEL
