"""Tests for app.services.quality_score_service."""
from pathlib import Path

from app.services import quality_score_service as svc


class _Storage:
    def __init__(self, files):
        self.files = files
        self.put = {}

    def list_files(self, run_id, prefix=""):
        return [k for k in self.files if k.startswith(prefix)]

    def get_file(self, run_id, key):
        return self.files[key]

    def put_file(self, run_id, key, data):
        self.put[key] = data


def test_stage_error_record_reaches_the_scorer(monkeypatch):
    """#745: the orchestrator mirrors <uid>_stage_errors.json to outputs/; the
    scorer only sees a recorded stage failure if it is copied into the dir it
    scores, alongside the stage artifacts."""
    storage = _Storage({
        "outputs/R1_fields.json": b"{}",
        "outputs/R1_stage_errors.json": b"[]",
        "outputs/R1_enriched.json": b"{}",
    })
    seen = {}

    def _score_run(outputs_dir, run_id):
        seen["names"] = sorted(p.name for p in Path(outputs_dir).iterdir())
        return {"totalScore": 40}

    monkeypatch.setattr(svc, "get_storage", lambda: storage)
    monkeypatch.setattr("unified_pipeline.quality_score.score_run", _score_run)

    assert svc.compute_and_cache_score("R1") == {"totalScore": 40}
    assert seen["names"] == ["R1_fields.json", "R1_stage_errors.json"]
