"""Persistence guard for output-rendering options (issue #153).

The ``/upload`` endpoint now accepts ``include_track_changes`` /
``include_classification_comments`` form fields and records them on the Run row
as the ``show_track_changes`` / ``show_pipeline_comments`` int columns. The
orchestrator later reads those columns (truthy ints) to drive Stage 6 rendering.

These tests pin the contract the orchestrator depends on:
  - the column defaults match the desired product defaults (track ON, comments OFF),
  - explicit choices round-trip, and
  - the truthy-int read the orchestrator performs maps correctly.

Uses the in-memory SQLite ``db`` fixture from conftest -- no HTTP stack needed.
"""


def test_run_render_option_column_defaults(db):
    """A Run created without the flags falls back to track ON / comments OFF."""
    from app.models import Run

    run = Run(id="DEF001", filename="cv.docx", file_type="docx", status="created")
    db.add(run)
    db.commit()
    db.refresh(run)

    assert run.show_track_changes == 1, "track changes must default ON"
    assert run.show_pipeline_comments == 0, "classification comments must default OFF"
    assert run.strip_template_instructions == 1, "strip WCM instructions must default ON"


def test_run_render_options_persist_explicit_values(db):
    """The booleans the upload endpoint maps (track off, comments on) round-trip."""
    from app.models import Run

    # Mirror upload.py: include_track_changes=False, include_classification_comments=True,
    # strip_wcm_instructions=False
    run = Run(
        id="EXP001",
        filename="cv.docx",
        file_type="docx",
        status="created",
        show_track_changes=1 if False else 0,
        show_pipeline_comments=1 if True else 0,
        strip_template_instructions=1 if False else 0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    assert run.show_track_changes == 0
    assert run.show_pipeline_comments == 1
    assert run.strip_template_instructions == 0

    # The orchestrator reads these as truthy ints to build the Stage 6 flags.
    emit_track_changes = bool(run.show_track_changes) if run.show_track_changes is not None else True
    emit_comments = bool(run.show_pipeline_comments) if run.show_pipeline_comments is not None else False
    strip_instructions = bool(run.strip_template_instructions) if run.strip_template_instructions is not None else True
    assert emit_track_changes is False
    assert emit_comments is True
    assert strip_instructions is False
