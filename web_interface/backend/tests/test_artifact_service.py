"""Unit tests for app.services.artifact_service (#780 review r3965862896).

Covers the security-critical file-access code that used to be untested inside
steps.py: validate_run_id / resolve_artifact boundary and traversal cases
(#1), the cross-run prefix-collision isolation regression (#2), preview
generation over the filesystem (#4), and parse_json_to_preview's branches
plus the row cap (#5).
"""
from __future__ import annotations

import json
import logging

import pytest
from fastapi import HTTPException

from app.models import Run, Step
from app.services import artifact_service as svc


# --- #1: validate_run_id boundary lengths, traversal, absolute, control chars ---


class TestValidateRunId:
    def test_shortest_accepted(self):
        svc.validate_run_id("A")  # 1 char -- the regex floor

    def test_longest_accepted(self):
        svc.validate_run_id("A" * 64)  # 64 chars -- the regex ceiling

    def test_one_over_rejected(self):
        with pytest.raises(HTTPException) as exc:
            svc.validate_run_id("A" * 65)
        assert exc.value.status_code == 400

    def test_empty_rejected(self):
        with pytest.raises(HTTPException) as exc:
            svc.validate_run_id("")
        assert exc.value.status_code == 400

    def test_traversal_rejected(self):
        with pytest.raises(HTTPException) as exc:
            svc.validate_run_id("../../etc")
        assert exc.value.status_code == 400

    def test_slash_rejected(self):
        with pytest.raises(HTTPException) as exc:
            svc.validate_run_id("a/b")
        assert exc.value.status_code == 400

    def test_absolute_looking_rejected(self):
        with pytest.raises(HTTPException) as exc:
            svc.validate_run_id("/etc/passwd")
        assert exc.value.status_code == 400

    def test_control_char_rejected(self):
        with pytest.raises(HTTPException) as exc:
            svc.validate_run_id("run\x00")
        assert exc.value.status_code == 400

    def test_ordinary_ids_accepted(self):
        for ok in ["A1B2C3", "run-fallback-1", "run_fallback_2"]:
            svc.validate_run_id(ok)  # must not raise


# --- #1: resolve_artifact traversal/absolute/invalid id/missing ---


class TestResolveArtifact:
    def test_invalid_run_id_raises_400(self, db):
        with pytest.raises(HTTPException) as exc:
            svc.resolve_artifact(db, "a/b", "file.json")
        assert exc.value.status_code == 400

    def test_absolute_filename_raises_400(self, db):
        with pytest.raises(HTTPException) as exc:
            svc.resolve_artifact(db, "RUN1", "/etc/passwd")
        assert exc.value.status_code == 400

    def test_traversal_filename_raises_400(self, db):
        with pytest.raises(HTTPException) as exc:
            svc.resolve_artifact(db, "RUN1", "../../etc/passwd")
        assert exc.value.status_code == 400

    def test_non_printable_filename_raises_400(self, db):
        with pytest.raises(HTTPException) as exc:
            svc.resolve_artifact(db, "RUN1", "stage1a\x00.json")
        assert exc.value.status_code == 400

    def test_missing_file_resolves_to_none_not_an_exception(self, db, monkeypatch, tmp_path):
        """A local miss is NOT an HTTPException -- callers decide whether to
        fall back to storage or 404 (#780 review r3965749610/r3965808294)."""
        monkeypatch.setattr(svc, "_WEB_OUTPUTS_ROOT", tmp_path / "outputs")
        monkeypatch.setattr(svc, "_PIPELINE_OUTPUTS_ROOT", tmp_path / "pipeline_outputs")
        resolved = svc.resolve_artifact(db, "RUN1", "nope.json")
        assert resolved.local_path is None
        assert resolved.basename == "nope.json"
        assert resolved.storage_key == "outputs/nope.json"

    def test_subpath_component_cannot_reach_a_sibling_runs_directory(
        self, db, monkeypatch, tmp_path
    ):
        """Mutation M2 (restore `output_dir / filename` instead of the
        resolver): a filename carrying a directory component (but no literal
        `..`, so the traversal guard alone doesn't catch it) must not be able
        to address another run's file -- resolve_artifact basenames every
        lookup, so "OTHERRUN/secret.json" can only ever resolve to
        RUN1/secret.json, which doesn't exist."""
        web_root = tmp_path / "outputs"
        monkeypatch.setattr(svc, "_WEB_OUTPUTS_ROOT", web_root)
        monkeypatch.setattr(svc, "_PIPELINE_OUTPUTS_ROOT", tmp_path / "pipeline_outputs")

        victim_dir = web_root / "OTHERRUN"
        victim_dir.mkdir(parents=True)
        (victim_dir / "secret.json").write_text('{"leak": true}')

        resolved = svc.resolve_artifact(db, "RUN1", "OTHERRUN/secret.json")
        assert resolved.local_path is None


# --- #2: cross-run isolation -- RUN123 vs RUN1234 prefix collision ---


class TestCrossRunIsolation:
    def test_shorter_run_id_cannot_read_longer_run_ids_shared_artifact(
        self, db, monkeypatch, tmp_path
    ):
        """Mutation M1 (restore `startswith` ownership): RUN1234's artifact in
        the shared tree must not be servable to RUN123 just because RUN123 is
        a string-prefix of RUN1234's basename."""
        monkeypatch.setattr(svc, "_WEB_OUTPUTS_ROOT", tmp_path / "outputs")
        pipeline_root = tmp_path / "pipeline_outputs"
        monkeypatch.setattr(svc, "_PIPELINE_OUTPUTS_ROOT", pipeline_root)

        stage_dir = pipeline_root / "stage_4_wcm_templates"
        stage_dir.mkdir(parents=True)
        (stage_dir / "RUN1234_wcm.docx").write_bytes(b"belongs to RUN1234 only")

        db.add(Run(id="RUN123", filename="a.docx", file_type="docx", status="complete"))
        db.add(Run(id="RUN1234", filename="b.docx", file_type="docx", status="complete"))
        db.add(Step(run_id="RUN1234", step_number=1, stage_id="6", step_name="WCM",
                    status="complete", output_files=json.dumps(["RUN1234_wcm.docx"])))
        db.commit()

        # The true owner resolves it.
        owner_resolved = svc.resolve_artifact(db, "RUN1234", "RUN1234_wcm.docx")
        assert owner_resolved.local_path is not None

        # The prefix-colliding run does not.
        prefix_resolved = svc.resolve_artifact(db, "RUN123", "RUN1234_wcm.docx")
        assert prefix_resolved.local_path is None


# --- #4: generate_preview / generate_preview_from_path ---


class TestGeneratePreview:
    def test_missing_file_returns_none(self, db, monkeypatch, tmp_path):
        monkeypatch.setattr(svc, "_WEB_OUTPUTS_ROOT", tmp_path / "outputs")
        monkeypatch.setattr(svc, "_PIPELINE_OUTPUTS_ROOT", tmp_path / "pipeline_outputs")
        assert svc.generate_preview(db, "RUN1", "missing.json") is None

    def test_non_json_file_returns_none(self, db, monkeypatch, tmp_path):
        web_root = tmp_path / "outputs"
        monkeypatch.setattr(svc, "_WEB_OUTPUTS_ROOT", web_root)
        monkeypatch.setattr(svc, "_PIPELINE_OUTPUTS_ROOT", tmp_path / "pipeline_outputs")
        run_dir = web_root / "RUN1"
        run_dir.mkdir(parents=True)
        (run_dir / "notes.txt").write_text("not json")
        assert svc.generate_preview(db, "RUN1", "notes.txt") is None

    def test_traversal_filename_bypasses_nothing(self, db, monkeypatch, tmp_path):
        """r3965749610: _generate_preview used to build output_dir / filename
        itself, bypassing the traversal guard entirely. It must now go
        through resolve_artifact and raise the same 400."""
        monkeypatch.setattr(svc, "_WEB_OUTPUTS_ROOT", tmp_path / "outputs")
        monkeypatch.setattr(svc, "_PIPELINE_OUTPUTS_ROOT", tmp_path / "pipeline_outputs")
        with pytest.raises(HTTPException) as exc:
            svc.generate_preview(db, "RUN1", "../../etc/passwd")
        assert exc.value.status_code == 400

    def test_valid_json_returns_preview(self, db, monkeypatch, tmp_path):
        web_root = tmp_path / "outputs"
        monkeypatch.setattr(svc, "_WEB_OUTPUTS_ROOT", web_root)
        monkeypatch.setattr(svc, "_PIPELINE_OUTPUTS_ROOT", tmp_path / "pipeline_outputs")
        run_dir = web_root / "RUN1"
        run_dir.mkdir(parents=True)
        (run_dir / "data.json").write_text(json.dumps([{"a": 1}]))
        preview = svc.generate_preview(db, "RUN1", "data.json")
        assert preview is not None
        assert preview.headers == ["a"]
        assert preview.rows == [["1"]]

    def test_malformed_json_returns_none_and_logs_warning(self, tmp_path, caplog):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json")
        with caplog.at_level(logging.WARNING):
            result = svc.generate_preview_from_path(bad)
        assert result is None
        assert any("Error generating preview" in r.getMessage() for r in caplog.records)

    def test_unexpected_error_in_parser_propagates(self, tmp_path, monkeypatch):
        """r3965796995: only malformed-input shapes are swallowed. A genuine
        programming error inside the parser (mutation: broadening the except
        back to `except Exception`) must propagate, not become a clean None."""
        good = tmp_path / "good.json"
        good.write_text(json.dumps([{"a": 1}]))

        def _boom(data):
            raise RuntimeError("not a malformed-input error")

        monkeypatch.setattr(svc, "parse_json_to_preview", _boom)
        with pytest.raises(RuntimeError):
            svc.generate_preview_from_path(good)


# --- #5: parse_json_to_preview branches + row cap ---


class TestParseJsonToPreview:
    def test_empty_list(self):
        result = svc.parse_json_to_preview([])
        assert result.headers == []
        assert result.rows == []
        assert result.total_rows == 0
        assert result.truncated is False

    def test_object_list(self):
        result = svc.parse_json_to_preview([{"x": 1, "y": 2}, {"x": 3, "y": 4}])
        assert result.headers == ["x", "y"]
        assert result.rows == [["1", "2"], ["3", "4"]]
        assert result.total_rows == 2
        assert result.truncated is False

    def test_scalar_list(self):
        result = svc.parse_json_to_preview([1, 2, 3])
        assert result.headers == ["value"]
        assert result.rows == [["1"], ["2"], ["3"]]

    def test_comprehensive_sections(self):
        data = {
            "publications": [{"title": "P1"}],
            "grants": [{"title": "G1", "amount": "5"}],
        }
        result = svc.parse_json_to_preview(data)
        assert result.headers[:2] == ["Section", "Index"]
        assert set(result.headers[2:]) == {"title", "amount"}
        assert len(result.rows) == 2
        sections_seen = {row[0] for row in result.rows}
        assert sections_seen == {"publications", "grants"}

    def test_dict_containing_a_list(self):
        data = {"meta": {"n": 1}, "items": [{"a": 1}, {"a": 2}]}
        result = svc.parse_json_to_preview(data)
        assert result.headers == ["a"]
        assert result.rows == [["1"], ["2"]]

    def test_key_value_fallback(self):
        data = {"name": "CV", "count": 5}
        result = svc.parse_json_to_preview(data)
        assert result.headers == ["Key", "Value"]
        assert ["name", "CV"] in result.rows
        assert ["count", "5"] in result.rows

    def test_row_cap_truncates_and_reports_total(self):
        """Mutation M3 (remove the row cap): a document with more than
        PREVIEW_MAX_ROWS items must come back capped, with truncated=True and
        the real total preserved."""
        data = [{"i": i} for i in range(svc.PREVIEW_MAX_ROWS + 50)]
        result = svc.parse_json_to_preview(data)
        assert len(result.rows) == svc.PREVIEW_MAX_ROWS
        assert result.truncated is True
        assert result.total_rows == svc.PREVIEW_MAX_ROWS + 50

    def test_cell_char_cap(self):
        data = [{"a": "x" * (svc.PREVIEW_CELL_MAX_CHARS + 50)}]
        result = svc.parse_json_to_preview(data)
        assert len(result.rows[0][0]) == svc.PREVIEW_CELL_MAX_CHARS


class TestParseJsonToPreviewBranchCaps:
    """The row cap must hold in every branch of parse_json_to_preview, not
    just the top-level list branch -- _preview_from_sections,
    _preview_from_single_list_value (dict items and scalar items), and the
    key/value fallback each apply it independently. Each of these dies if
    its own cap is removed (pasted per-branch in the ticket report)."""

    def test_sections_branch_cap(self):
        data = {
            "publications": [{"id": i} for i in range(150)],
            "grants": [{"id": i} for i in range(60)],
        }
        result = svc.parse_json_to_preview(data)
        assert len(result.rows) == svc.PREVIEW_MAX_ROWS
        assert result.truncated is True
        assert result.total_rows == 210

    def test_single_list_value_with_dicts_cap(self):
        data = {"items": [{"a": i} for i in range(201)]}
        result = svc.parse_json_to_preview(data)
        assert result.headers == ["a"]
        assert len(result.rows) == svc.PREVIEW_MAX_ROWS
        assert result.truncated is True
        assert result.total_rows == 201

    def test_single_list_value_with_scalars_cap(self):
        data = {"items": list(range(201))}
        result = svc.parse_json_to_preview(data)
        assert result.headers == ["value"]
        assert len(result.rows) == svc.PREVIEW_MAX_ROWS
        assert result.truncated is True
        assert result.total_rows == 201

    def test_key_value_fallback_cap(self):
        data = {f"k{i}": i for i in range(201)}
        result = svc.parse_json_to_preview(data)
        assert result.headers == ["Key", "Value"]
        assert len(result.rows) == svc.PREVIEW_MAX_ROWS
        assert result.truncated is True
        assert result.total_rows == 201


# --- is_json_artifact: the single normalized JSON-classification rule ---


class TestIsJsonArtifact:
    def test_uppercase_extension_is_json(self):
        """Mutation M4 (restore `.endswith('.json')`): a case-sensitive check
        would miss FOO.JSON."""
        assert svc.is_json_artifact("FOO.JSON") is True

    def test_lowercase_extension_is_json(self):
        assert svc.is_json_artifact("foo.json") is True

    def test_non_json_is_not_json(self):
        assert svc.is_json_artifact("foo.docx") is False
        assert svc.is_json_artifact("foo.txt") is False
