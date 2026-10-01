"""Tests for app.services.quality_score_service."""
from pathlib import Path

import pytest

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


# --- parsed snapshot and the runs.quality_* columns --------------------------

def _cached(total=72, raw=72.4, caps=(), dims=None, flags=()):
    return {
        "totalScore": total,
        "raw_score_before_caps": raw,
        "hard_fail_caps_applied": list(caps),
        "dimensionScores": dims if dims is not None else [
            {"name": "Sparse tables", "score": 9.5, "max": 12},
            {"name": "A gate", "score": 0.0, "max": 0},
        ],
        "flags": list(flags),
        "data_complete": True,
    }


def test_parse_score_keeps_weighted_dimensions_only():
    snap = svc.parse_score(_cached())
    assert snap.total == 72 and snap.earned == 72.4
    assert snap.dimensions == (svc.DimensionPoints("Sparse tables", 12, 9.5),)
    assert snap.data_complete is True


@pytest.mark.parametrize("raw", [None, [], {}, {"totalScore": "72"}, {"totalScore": True}])
def test_parse_score_rejects_unusable_dicts(raw):
    assert svc.parse_score(raw) is None


def test_parse_score_tolerates_a_cache_written_before_later_fields():
    snap = svc.parse_score({"totalScore": 90})
    assert (snap.earned, snap.caps_triggered, snap.dimensions, snap.data_complete) == (
        90.0, (), (), None)


@pytest.mark.parametrize("total, band", [
    (100, "GREEN"), (85, "GREEN"), (84, "YELLOW"), (60, "YELLOW"), (59, "RED"), (0, "RED")])
def test_band_key_follows_the_scorers_thresholds(total, band):
    assert svc.band_key(total) == band


@pytest.mark.parametrize("raw, caps, expected", [
    (80.0, [], None),
    (80.0, [40], 40),          # lowered the score
    (80.0, [40, 25], 25),      # lowest wins
    (20.0, [25], None),        # cap above the weighted total changed nothing
])
def test_binding_cap(raw, caps, expected):
    assert svc.binding_cap(svc.parse_score(_cached(total=40, raw=raw, caps=caps))) == expected


def test_score_columns_maps_score_band_and_cap():
    cols = svc.score_columns(_cached(total=25, raw=80.0, caps=[25]))
    assert cols == svc.ScoreColumns(25, "RED", 25)
    assert svc.score_columns(_cached(total=91, raw=91.0)) == svc.ScoreColumns(91, "GREEN", None)
    assert svc.score_columns(None) == svc.ScoreColumns(None, None, None)


def test_persist_score_columns_writes_the_row(db):
    from app.models import Run

    db.add(Run(id="Q00001", filename="cv.docx", file_type="docx", status="complete"))
    db.commit()

    svc.persist_score_columns(db, "Q00001", _cached(total=25, raw=80.0, caps=[25]))

    db.expire_all()
    run = db.get(Run, "Q00001")
    assert (run.quality_score, run.quality_band, run.quality_cap) == (25, "RED", 25)


def test_persist_score_columns_ignores_a_missing_score(db):
    from app.models import Run

    db.add(Run(id="Q00002", filename="cv.docx", file_type="docx", status="complete"))
    db.commit()

    svc.persist_score_columns(db, "Q00002", None)

    assert db.get(Run, "Q00002").quality_score is None


def test_persist_score_columns_failure_is_logged_and_rolled_back(caplog):
    class _BrokenDb:
        rolled_back = False

        def query(self, *a):
            raise RuntimeError("db down")

        def rollback(self):
            self.rolled_back = True

    broken = _BrokenDb()
    with caplog.at_level("WARNING"):
        svc.persist_score_columns(broken, "Q00003", _cached())

    assert broken.rolled_back
    assert any(r.exc_info and "Q00003" in r.getMessage() for r in caplog.records)


def test_load_cached_score_tells_absent_from_unreadable(monkeypatch):
    from app.storage.base import StorageKeyNotFound

    class _S:
        def get_file(self, run_id, key):
            if run_id == "ABSENT":
                raise StorageKeyNotFound("nope")
            return b"{not json"

    monkeypatch.setattr(svc, "get_storage", lambda: _S())

    assert svc.load_cached_score("ABSENT") is None
    with pytest.raises(ValueError):
        svc.load_cached_score("BROKEN")


def test_get_doctor_report_reads_the_mirrored_report(monkeypatch):
    storage = _Storage({"outputs/R1_doctor.json": b'{"findings": []}',
                        "outputs/R1_fields.json": b"{}"})
    monkeypatch.setattr(svc, "get_storage", lambda: storage)
    assert svc.get_doctor_report("R1") == {"findings": []}

    monkeypatch.setattr(svc, "get_storage", lambda: _Storage({"outputs/R1_fields.json": b"{}"}))
    assert svc.get_doctor_report("R1") is None
