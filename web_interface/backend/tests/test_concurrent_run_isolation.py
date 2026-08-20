"""Concurrent-run cross-tenant isolation (a class-level, proactive guard).

CODING_STANDARDS.md's §4.2/§4.3 name three now-fixed leak vectors (#580,
#581, #582), each closed by a patch scoped to the exact shape a real
incident had already taken. Enumerated bans catch the next bug only if it
repeats a known shape.

This test instead drives 3 real ``PipelineOrchestrator.execute()`` calls
concurrently -- real OS threads, each with its own event loop and its own
DB session, exactly mirroring ``api/runs.py``'s ``run_pipeline()`` -- and
asserts no run's sentinel value crosses into another run's prompt-log
files, ``Log`` rows, or ``INSTITUTION_CACHE`` entries. It exercises the
actual concurrency primitives (``_RoutedStdout``'s thread-id dispatch, a
real ``asyncio.to_thread`` worker thread, ``contextvars`` propagation
into it, the shared ``INSTITUTION_CACHE`` dict under genuine concurrent
writes) instead of reimplementing them, so it would have caught
#580/#581/#582 before anyone filed them -- and it catches the next
process-global leak in this shape, not just these three.

Caveat, stated plainly: a full 12-stage, LLM-hitting concurrent run isn't
feasible in CI (stage 2 alone issues ~86 real LLM calls per CV, and no
existing test in this suite drives more than one stubbed stage). This test
trims ``STEP_REGISTRY`` to stage 1a and stubs ``get_cv_hierarchy_chunked``
-- the same stubbing shape ``test_run_full_pipeline_exit_status.py`` and
``test_run_doctor_integration.py`` already use -- so it is a regression
guard on the three named vectors, not a from-scratch discovery tool. A
future process-global cache this stub's call site doesn't reach would not
be caught here.
"""
import asyncio
import shutil
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

RUN_IDS = ["LEAKRUNA", "LEAKRUNB", "LEAKRUNC"]


def test_concurrent_runs_do_not_cross_contaminate(monkeypatch, tmp_path):
    from app.database import Base
    from app.pipeline import orchestrator as orch
    from app.models import Run, Log
    from unified_pipeline.core import prompt_logger as prompt_logger_mod
    from unified_pipeline.stage_5b_institution_enrichment import (
        INSTITUTION_CACHE,
        _institution_cache_key,
    )

    # A real, file-backed SQLite database, not the shared in-memory
    # StaticPool the `db` fixture uses: that pool hands every session the
    # SAME physical connection, so two threads committing at once raise
    # "cannot start a transaction within a transaction" -- a limitation of
    # sharing one connection, not of the pipeline. A file gets SQLite's own
    # per-connection locking, which is what genuine concurrent sessions need.
    engine = create_engine(f"sqlite:///{tmp_path}/leak_test.db")
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine)

    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    monkeypatch.setattr(
        orch, "STEP_REGISTRY",
        [SimpleNamespace(number=1, stage_id="1a", name="Hierarchy Extraction")],
    )
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("CVICHE_STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(prompt_logger_mod, "PROMPT_LOG_DIR", tmp_path / "prompt_logs")
    INSTITUTION_CACHE.clear()

    barrier = threading.Barrier(len(RUN_IDS))

    def _shared_stub(cv_path=None):
        # One function object, installed once below -- reassigning
        # orch.get_cv_hierarchy_chunked per-thread would itself be the
        # process-global race this test exists to catch. Instead this
        # reads the run id off the SAME contextvar prompt_logger already
        # uses for scoping (set by execute() before any stage runs, and
        # correctly propagated into this asyncio.to_thread worker by
        # stdlib context-copy semantics) -- so identifying "which run is
        # this call for" is exactly the mechanism under test, not a
        # test-harness workaround for it.
        run_id = prompt_logger_mod._current_run_id.get()
        barrier.wait(timeout=5)  # force genuine interleaving of all 3
        print(f"SENTINEL-STDOUT-{run_id}")  # -> Log rows, #581's path
        prompt_logger_mod.log_prompt_before_call(  # -> prompt_logger, #580's path
            messages=[{"role": "user", "content": f"SENTINEL-PROMPT-{run_id}"}],
            model="test-model", purpose="leak_test", context={"run_id": run_id},
        )
        key = _institution_cache_key(  # -> INSTITUTION_CACHE, #582's path
            "OU College of Medicine",
            f"Currently at run {run_id}'s institution, {run_id}",
        )
        INSTITUTION_CACHE[key] = {"sentinel_run": run_id}
        return (
            [{"level": "H1", "text": "Education", "children": []}],
            {"extraction_cost": 0.01},
        )

    monkeypatch.setattr(orch, "get_cv_hierarchy_chunked", _shared_stub)

    errors = []

    def _drive(run_id):
        session = SessionFactory()
        try:
            session.add(Run(
                id=run_id, filename="cv.docx", file_type="docx",
                status="running", started_at=datetime.now(),
            ))
            session.commit()

            o = orch.PipelineOrchestrator(run_id, tmp_path / f"{run_id}.docx", session)
            # Instance attributes -- each thread only ever touches its own
            # orchestrator object, so plain assignment (not monkeypatch,
            # whose bookkeeping is main-thread-only) is safe here.
            o.pipeline_output_dir = tmp_path / run_id / "outputs"
            o._copy_to_pipeline_input = lambda: str(tmp_path / f"{run_id}.docx")
            asyncio.run(o.execute())
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append((run_id, exc))
        finally:
            session.close()

    threads = [threading.Thread(target=_drive, args=(rid,)) for rid in RUN_IDS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    for rid in RUN_IDS:
        # __init__ writes a real (non-tmp_path) web_output_dir as a side
        # effect (Path(__file__)-relative, not self.pipeline_output_dir) --
        # clean up what this test created, same as production would on pod
        # recycle, so a full run of the suite doesn't litter the checkout.
        real_dir = Path(__file__).parent.parent.parent / "outputs" / rid
        shutil.rmtree(real_dir, ignore_errors=True)

    assert not errors, f"orchestrator run(s) raised: {errors}"

    db = SessionFactory()
    for rid in RUN_IDS:
        assert db.query(Run).filter(Run.id == rid).first().status == "complete"

    # 1. prompt_logger -- each run's directory contains only its own sentinel.
    for rid in RUN_IDS:
        run_dir = tmp_path / "prompt_logs" / rid
        assert run_dir.is_dir(), f"no prompt-log directory for {rid}"
        blob = "\n".join(p.read_text() for p in run_dir.glob("*.json"))
        assert f"SENTINEL-PROMPT-{rid}" in blob
        for other in RUN_IDS:
            if other != rid:
                assert f"SENTINEL-PROMPT-{other}" not in blob, (
                    f"{rid}'s prompt log contains {other}'s sentinel"
                )

    # 2. stdout -> Log rows -- each run's rows carry only its own sentinel.
    for rid in RUN_IDS:
        rows = db.query(Log).filter(Log.message.like("%SENTINEL-STDOUT%")).all()
        own = [r for r in rows if r.run_id == rid]
        assert own, f"no SENTINEL-STDOUT Log row recorded for {rid}"
        for row in own:
            assert row.message == f"SENTINEL-STDOUT-{rid}", (
                f"Log row for run {rid} carries another run's text: {row.message!r}"
            )

    # 3. INSTITUTION_CACHE -- distinct owner-scoped keys, no clobbered value.
    keys = {
        rid: _institution_cache_key(
            "OU College of Medicine", f"Currently at run {rid}'s institution, {rid}"
        )
        for rid in RUN_IDS
    }
    assert len(set(keys.values())) == len(RUN_IDS), "owner-scoped cache keys collided"
    for rid, key in keys.items():
        assert INSTITUTION_CACHE[key]["sentinel_run"] == rid, (
            f"INSTITUTION_CACHE entry for {rid} was overwritten by another run"
        )
