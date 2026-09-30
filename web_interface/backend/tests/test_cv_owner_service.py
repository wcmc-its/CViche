"""cv_owner_service: which stage-4 file and which name field feed
runs.cv_owner_name (shared by the orchestrator and the backfill script)."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import cv_owner_service as svc
from app.services.artifact_service import ResolvedArtifact


class TestExtractCvOwnerName:
    def test_prefers_full_name_stripped(self):
        assert svc.extract_cv_owner_name(
            {"cv_owner": {"full_name": "  Jane Testperson ", "first_name": "X", "last_name": "Y"}}
        ) == "Jane Testperson"

    def test_falls_back_to_first_and_last(self):
        assert svc.extract_cv_owner_name(
            {"cv_owner": {"first_name": "Jane", "last_name": " Testperson "}}
        ) == "Jane Testperson"

    def test_blank_full_name_falls_back(self):
        assert svc.extract_cv_owner_name(
            {"cv_owner": {"full_name": "   ", "first_name": "Jane", "last_name": "Testperson"}}
        ) == "Jane Testperson"

    def test_single_name_part_is_kept(self):
        assert svc.extract_cv_owner_name({"cv_owner": {"last_name": "Testperson"}}) == "Testperson"

    @pytest.mark.parametrize("fields", [
        None, [], {}, {"cv_owner": None}, {"cv_owner": "Jane"},
        {"cv_owner": {}}, {"cv_owner": {"full_name": "  ", "first_name": None}},
        {"cv_owner": {"full_name": 7}},
    ])
    def test_unusable_owner_is_none(self, fields):
        assert svc.extract_cv_owner_name(fields) is None

    def test_truncated_to_column_width(self):
        name = svc.extract_cv_owner_name({"cv_owner": {"full_name": "N" * 400}})
        assert len(name) == svc.CV_OWNER_NAME_MAX_LENGTH == 255


class TestFindFieldsJsonName:
    def test_picks_basename_of_fields_file(self):
        files = json.dumps(["/out/a_entries.json", "/out/abc123_fields.json"])
        assert svc.find_fields_json_name(files) == "abc123_fields.json"

    @pytest.mark.parametrize("value", [None, "", "not json", "{}", json.dumps(["/out/a.json"]),
                                       json.dumps([5])])
    def test_none_when_absent_or_malformed(self, value):
        assert svc.find_fields_json_name(value) is None


class TestLoadFieldsJson:
    def test_reads_local_file(self, tmp_path, db):
        path = tmp_path / "abc123_fields.json"
        path.write_text(json.dumps({"cv_owner": {"full_name": "Jane Testperson"}}))
        resolved = ResolvedArtifact(local_path=path, storage_key="outputs/abc123_fields.json",
                                    basename=path.name)
        with patch.object(svc.artifact_service, "resolve_artifact", return_value=resolved):
            assert svc.read_cv_owner_name(db, "abc123", json.dumps([str(path)])) == "Jane Testperson"

    def test_falls_back_to_storage(self, db):
        resolved = ResolvedArtifact(local_path=None, storage_key="outputs/abc123_fields.json",
                                    basename="abc123_fields.json")
        storage = type("S", (), {"get_file": lambda self, rid, key: json.dumps(
            {"cv_owner": {"first_name": "Jane", "last_name": "Testperson"}}).encode()})()
        with patch.object(svc.artifact_service, "resolve_artifact", return_value=resolved), \
                patch.object(svc, "get_storage", return_value=storage):
            assert svc.read_cv_owner_name(db, "abc123", json.dumps(["/x/abc123_fields.json"])) \
                == "Jane Testperson"

    def test_missing_everywhere_is_none(self, db):
        resolved = ResolvedArtifact(local_path=None, storage_key="outputs/abc123_fields.json",
                                    basename="abc123_fields.json")

        class Missing:
            def get_file(self, rid, key):
                raise FileNotFoundError(key)

        with patch.object(svc.artifact_service, "resolve_artifact", return_value=resolved), \
                patch.object(svc, "get_storage", return_value=Missing()):
            assert svc.read_cv_owner_name(db, "abc123", json.dumps(["/x/abc123_fields.json"])) is None

    def test_no_fields_output_skips_io(self, db):
        with patch.object(svc.artifact_service, "resolve_artifact") as resolve:
            assert svc.read_cv_owner_name(db, "abc123", json.dumps(["/x/a.json"])) is None
        resolve.assert_not_called()
