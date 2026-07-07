"""The periodic stale-run reaper loop (app.main._stale_run_reaper_loop) must
sweep on its interval, survive a transient sweep error, and cancel cleanly.

The reaping logic itself (reconcile_stale_runs) is covered by
test_auto_retry_reconcile / test_release_guards; this only exercises the
loop plumbing added for the steady-state backstop.
"""
import asyncio

import app.main as main_mod


def test_reaper_loop_sweeps_repeatedly_and_survives_errors(monkeypatch):
    calls = []

    def fake_reconcile(db):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient db blip")   # first sweep fails
        return 2

    monkeypatch.setattr("app.services.run_service.reconcile_stale_runs", fake_reconcile)

    async def run_briefly():
        task = asyncio.create_task(main_mod._stale_run_reaper_loop(0.01))
        await asyncio.sleep(0.1)          # ~10 intervals
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())

    # Survived the first sweep raising and kept going -> multiple sweeps ran.
    assert len(calls) >= 2
