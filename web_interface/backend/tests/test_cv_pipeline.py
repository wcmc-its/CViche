"""
Tests for CVPipeline orchestrator construction-time invariants.

These are fast unit tests — no LLM calls, no sample CV required. They
cover structural contracts between the constructor and the run_* methods
so a regression in one half is caught without paying for an E2E run.
"""
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

# Add src/ to path so unified_pipeline.core is importable.
# tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))


@pytest.fixture
def fresh_pipeline(tmp_path):
    """Construct a CVPipeline against a sentinel input file."""
    from unified_pipeline.core.cv_pipeline import CVPipeline

    fake_cv = tmp_path / "fake.docx"
    fake_cv.write_bytes(b"")  # CVPipeline.__init__ only checks .exists()

    out_dir = tmp_path / "out"
    return CVPipeline(str(fake_cv), output_dir=str(out_dir))


def test_stage_dirs_has_per_entity_keys_for_section_parser(fresh_pipeline):
    """run_stage_3_section_parsing iterates parsed_data entity types and
    looks up self.stage_dirs[f'stage_3_{section_type}'] to choose an output
    directory. The constructor must register one key per entity type, or
    the parser raises KeyError on the first iteration."""
    for entity_type in fresh_pipeline.entity_to_section_code:
        key = f"stage_3_{entity_type}"
        assert key in fresh_pipeline.stage_dirs, (
            f"Missing {key!r} in stage_dirs — run_stage_3_section_parsing "
            f"will KeyError when writing parsed {entity_type} output"
        )
        assert fresh_pipeline.stage_dirs[key].exists(), (
            f"stage_dirs[{key!r}] = {fresh_pipeline.stage_dirs[key]} "
            f"is registered but not created on disk"
        )


def test_stage_3_parsing_dir_layout(fresh_pipeline):
    """The entity-files fallback reader in run_stage_4_template_generation
    expects per-entity subdirs under stage_dirs['stage_3_parsing']. Keep
    writer and reader on the same layout."""
    parsing_root = fresh_pipeline.stage_dirs["stage_3_parsing"]
    for entity_type in fresh_pipeline.entity_to_section_code:
        per_entity = fresh_pipeline.stage_dirs[f"stage_3_{entity_type}"]
        assert per_entity == parsing_root / entity_type, (
            f"stage_3_{entity_type} should live under stage_3_parsing/; "
            f"got {per_entity}"
        )


def test_all_stage_dirs_subscript_lookups_resolve(fresh_pipeline):
    """Enumerates every literal key used in a self.stage_dirs[...] subscript
    across cv_pipeline.py. Each must be registered in __init__ — a missing
    key raises KeyError mid-pipeline (the bug class that masked an unrelated
    str/str TypeError until that was fixed)."""
    # Keep this list in lockstep with cv_pipeline.py subscript usages.
    expected_keys = {
        "stage_1",
        "stage_3",
        "stage_3_parsing",
        "stage_4",
    }
    # run_stage_3_section_parsing iterates entity_to_section_code and
    # subscripts stage_dirs[f"stage_3_{entity_type}"].
    for entity_type in fresh_pipeline.entity_to_section_code:
        expected_keys.add(f"stage_3_{entity_type}")

    missing = sorted(k for k in expected_keys if k not in fresh_pipeline.stage_dirs)
    assert not missing, (
        f"stage_dirs is missing keys that runners subscript with []: {missing}"
    )
