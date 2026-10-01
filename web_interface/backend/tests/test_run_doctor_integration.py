"""Auto-run doctor integration (post-completion artifact lints).

Covers the orchestrator hook that runs unified_pipeline.run_doctor after a
successful run:

  * CVICHE_RUN_DOCTOR=0 disables it (no doctor call, no report file);
  * default-on: the report JSON is written under the pipeline outputs dir
    (stage_7_doctor/<uid>_doctor.json), registered on the final step's
    output_files, and summarized as a "Doctor" fact on the terminal Teams card;
  * a doctor crash is logged and swallowed -- the run still completes and the
    terminal notification still goes out (without the Doctor line).
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

# Make unified_pipeline.* importable directly (tests/ -> backend/ ->
# web_interface/ -> project_root/ -> src/), matching test_run_duration_coverage.
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

import unified_pipeline.run_doctor as run_doctor_mod

from app.services import notifications


def _doctor_payload():
    """A canned run_doctor report with one substantive (WARN) finding."""
    return {
        "document_uid": "DOC_ON",
        "root": "unused",
        "artifacts": {},
        "findings": [
            {"lint": "segmentation", "severity": "WARN",
             "message": "1 substantive source line(s) unaccounted for",
             "evidence": []},
        ],
        "counts": {"ERROR": 0, "WARN": 1, "INFO": 0},
        "worst_severity": "WARN",
    }


def _orchestrator(monkeypatch, tmp_path, db, run_id):
    """A run-to-completion orchestrator with the real pipeline stubbed out
    (mirrors test_run_duration_coverage) and pipeline outputs redirected to
    tmp_path so the doctor report never lands in the repo's outputs dir."""
    from app.pipeline import orchestrator as orch
    from app.models import Run, Step

    db.add(Run(
        id=run_id, filename="cv.docx", file_type="docx", status="running",
        started_at=datetime(2026, 7, 1, 12, 0, 0),
    ))
    # The final (stage 6) step row the doctor report gets attached to.
    db.add(Step(
        run_id=run_id, step_number=12, step_name="WCM Template Population",
        status="complete", output_files=json.dumps(["/x/cv_wcm.docx"]),
    ))
    db.commit()

    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    monkeypatch.setattr(
        orch, "STEP_REGISTRY",
        [SimpleNamespace(number=1, stage_id="1a", name="Hierarchy Extraction")],
    )

    o = orch.PipelineOrchestrator(run_id, tmp_path / f"{run_id}.docx", db)
    monkeypatch.setattr(o, "_copy_to_pipeline_input", lambda: str(tmp_path / "cv.docx"))
    monkeypatch.setattr(o, "execute_step", AsyncMock())
    monkeypatch.setattr(o, "pipeline_output_dir", tmp_path / "outputs")
    return o


def _capture_posts(monkeypatch):
    """Record every Teams webhook POST payload (started + terminal cards)."""
    posts = []

    def _ok_post(url, json=None, timeout=None):
        posts.append(json)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _ok_post)
    return posts


def _facts(payload):
    """{name: value} from the card's FactSet (as in test_notifications)."""
    card = payload["attachments"][0]["content"]
    factset = next(b for b in card["body"] if b["type"] == "FactSet")
    return {f["title"]: f["value"] for f in factset["facts"]}


def test_knob_off_skips_doctor(monkeypatch, tmp_path, db):
    """CVICHE_RUN_DOCTOR=0: run_doctor is never called and no report is written."""
    from app.models import Run

    monkeypatch.setenv("CVICHE_RUN_DOCTOR", "0")
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    called = []
    monkeypatch.setattr(
        run_doctor_mod, "run_doctor",
        lambda *a, **k: called.append(a) or _doctor_payload(),
    )

    o = _orchestrator(monkeypatch, tmp_path, db, "DOC_OFF")
    asyncio.run(o.execute())

    db.expire_all()
    assert db.query(Run).filter(Run.id == "DOC_OFF").first().status == "complete"
    assert called == []
    assert not (tmp_path / "outputs" / "stage_7_doctor").exists()


def test_doctor_runs_by_default_and_publishes(monkeypatch, tmp_path, db):
    """Knob unset: the report is written where the stage-JSON viewer serves
    pipeline outputs, attached to the final step's output_files, and the
    terminal Teams card carries the Doctor summary line."""
    from app.models import Run, Step

    monkeypatch.delenv("CVICHE_RUN_DOCTOR", raising=False)
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = _doctor_payload()
    monkeypatch.setattr(run_doctor_mod, "run_doctor", lambda *a, **k: payload)
    posts = _capture_posts(monkeypatch)

    o = _orchestrator(monkeypatch, tmp_path, db, "DOC_ON")
    asyncio.run(o.execute())

    db.expire_all()
    assert db.query(Run).filter(Run.id == "DOC_ON").first().status == "complete"

    # Report written under the pipeline outputs dir (document_uid = run id).
    report = tmp_path / "outputs" / "stage_7_doctor" / "DOC_ON_doctor.json"
    assert json.loads(report.read_text()) == payload

    # Registered on the final step's output_files (what the viewer lists),
    # without clobbering the existing stage-6 output.
    step = db.query(Step).filter(Step.run_id == "DOC_ON").first()
    files = json.loads(step.output_files)
    assert files == ["/x/cv_wcm.docx", str(report)]

    # Two cards posted (started + terminal); the terminal one has the line.
    notifications.flush()
    assert len(posts) == 2
    assert _facts(posts[-1])["Doctor"] == "1 findings (top: segmentation)"


def test_doctor_crash_never_fails_run(monkeypatch, tmp_path, db, caplog):
    """A doctor exception is logged and swallowed: the run stays complete and
    the terminal notification still goes out, just without a Doctor fact."""
    from app.models import Run

    monkeypatch.delenv("CVICHE_RUN_DOCTOR", raising=False)
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    def _boom(*args, **kwargs):
        raise RuntimeError("doctor exploded")

    monkeypatch.setattr(run_doctor_mod, "run_doctor", _boom)
    posts = _capture_posts(monkeypatch)

    o = _orchestrator(monkeypatch, tmp_path, db, "DOC_BOOM")
    with caplog.at_level(logging.WARNING):
        asyncio.run(o.execute())

    db.expire_all()
    assert db.query(Run).filter(Run.id == "DOC_BOOM").first().status == "complete"
    assert any("Run doctor failed" in r.message for r in caplog.records)
    notifications.flush()
    assert len(posts) == 2
    assert "Doctor" not in _facts(posts[-1])


def test_doctor_source_is_the_runs_private_input_copy(monkeypatch, tmp_path, db):
    """#299: the doctor reads the same per-run copy _copy_to_pipeline_input wrote."""
    from app.pipeline import orchestrator as orch

    monkeypatch.setattr(orch, "PARENT_DIR", tmp_path / "repo")
    o = _orchestrator(monkeypatch, tmp_path, db, "DOC_SRC")
    copied = o._pipeline_input_path()
    copied.parent.mkdir(parents=True)
    copied.write_bytes(b"x")

    seen = {}
    monkeypatch.setattr(
        run_doctor_mod, "run_doctor",
        lambda out_dir, uid, source=None: seen.update(source=source) or _doctor_payload(),
    )
    o._doctor_report()

    assert seen["source"] == copied


def test_completed_run_gets_its_quality_columns(monkeypatch, tmp_path, db):
    """The run-end score is copied onto runs.quality_* on the orchestrator's thread."""
    from app.models import Run
    from app.services import quality_score_service

    monkeypatch.setenv("CVICHE_RUN_DOCTOR", "0")
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)
    score = {"totalScore": 62, "raw_score_before_caps": 62.0, "hard_fail_caps_applied": []}
    monkeypatch.setattr(quality_score_service, "compute_and_cache_score", lambda _rid: score)

    o = _orchestrator(monkeypatch, tmp_path, db, "DOC_SCORE")
    asyncio.run(o.execute())

    db.expire_all()
    run = db.query(Run).filter(Run.id == "DOC_SCORE").first()
    assert (run.quality_score, run.quality_band, run.quality_cap) == (62, "YELLOW", None)
