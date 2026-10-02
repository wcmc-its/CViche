"""Regression guard: restart inherits the user's render-option choices (issue #153).

``POST /run/{id}/restart`` creates a brand-new Run from a previous one. It
already inherits ``submission_type``; issue #153 added the per-run output flags
``show_track_changes`` / ``show_pipeline_comments`` and #199 added
``strip_template_instructions``. If restart does NOT carry them over, a run
created with (say) track changes OFF or strip-instructions OFF silently reverts
to the column defaults on restart -- and because the restarted run can execute
on any pod, the user's upload-time choice is lost cross-pod.

This test drives ``restart_run`` directly against the in-memory ``db`` fixture,
stubbing only the file-I/O and rate-limit side effects, and asserts the new Run
row inherits the original's non-default render options.
"""

import asyncio
from unittest.mock import patch, MagicMock


def test_restart_inherits_render_options(db, tmp_path):
    from app.api import runs as runs_api
    from app.models import Run, User

    user = User(email="restart@example.com", display_name="Restart", role="user")
    db.add(user)
    db.commit()
    db.refresh(user)

    # Original run with ALL option flags flipped away from the column defaults
    # so an accidental fall-back to defaults (1/0/1) would be caught.
    original = Run(
        id="ORIG001",
        filename="cv.docx",
        file_type="docx",
        status="completed",
        user_id=user.id,
        submission_type="standard",
        show_track_changes=0,
        show_pipeline_comments=1,
        strip_template_instructions=0,
    )
    db.add(original)
    db.commit()

    # Neutralize the side-effecting helpers: access check returns the original
    # run, rate limit passes, the file-presence/read steps are no-ops, and
    # UPLOAD_DIR points at a temp dir -- restart's pod-local write is a real
    # exclusive open() now, so it must not land in the repo's uploads/.
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    with patch.object(runs_api, "check_run_access", return_value=original), \
         patch.object(runs_api, "check_rate_limit", return_value=None), \
         patch.object(runs_api, "_materialize_input_if_missing", return_value=None), \
         patch.object(runs_api, "get_storage", return_value=MagicMock()), \
         patch.object(runs_api, "UPLOAD_DIR", upload_dir), \
         patch("app.services.run_creation.UPLOAD_DIR", upload_dir), \
         patch("app.services.run_creation.get_storage", return_value=MagicMock()), \
         patch("pathlib.Path.read_bytes", return_value=b"PK\x03\x04fake-docx"), \
         patch("pathlib.Path.unlink", return_value=None), \
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
    assert new_run.strip_template_instructions == 0, "restart must inherit strip-instructions OFF"
    # And the previously-inherited field still works.
    assert new_run.submission_type == "standard"
