"""Regression guard: restart inherits the user's render-option choices (issue #153).

``POST /run/{id}/restart`` creates a brand-new Run from a previous one. It
already inherits ``submission_type``; issue #153 added the per-run output flags
``show_track_changes`` / ``show_pipeline_comments``. If restart does NOT carry
them over, a run created with (say) track changes OFF or classification comments
ON silently reverts to the column defaults (track ON / comments OFF) on restart,
discarding a choice the user made at upload.

This test drives ``restart_run`` directly against the in-memory ``db`` fixture,
stubbing only the file-I/O and rate-limit side effects, and asserts the new Run
row inherits the original's non-default render options.
"""

import asyncio
from unittest.mock import patch


def test_restart_inherits_render_options(db):
    from app.api import runs as runs_api
    from app.models import Run, User

    user = User(email="restart@example.com", display_name="Restart", role="user")
    db.add(user)
    db.commit()
    db.refresh(user)

    # Original run with BOTH flags flipped away from the column defaults so an
    # accidental fall-back to defaults (1/0) would be caught.
    original = Run(
        id="ORIG001",
        filename="cv.docx",
        file_type="docx",
        status="completed",
        user_id=user.id,
        submission_type="standard",
        show_track_changes=0,
        show_pipeline_comments=1,
    )
    db.add(original)
    db.commit()

    # Neutralize the side-effecting helpers: access check returns the original
    # run, rate limit passes, and the file-presence/copy steps are no-ops so we
    # don't touch the real UPLOAD_DIR.
    with patch.object(runs_api, "check_run_access", return_value=original), \
         patch.object(runs_api, "check_rate_limit", return_value=None), \
         patch.object(runs_api, "_materialize_input_if_missing", return_value=None), \
         patch("shutil.copy2", return_value=None), \
         patch("pathlib.Path.exists", return_value=True):
        result = asyncio.run(
            runs_api.restart_run(run_id="ORIG001", db=db, current_user=user)
        )

    new_run = db.query(Run).filter(Run.id == result["run_id"]).first()
    assert new_run is not None
    assert new_run.id != "ORIG001"
    # The whole point: the user's choices survive the restart.
    assert new_run.show_track_changes == 0, "restart must inherit track-changes OFF"
    assert new_run.show_pipeline_comments == 1, "restart must inherit comments ON"
    # And the previously-inherited field still works.
    assert new_run.submission_type == "standard"
