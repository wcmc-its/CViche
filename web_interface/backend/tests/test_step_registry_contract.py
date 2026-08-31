"""Contract tests for app.pipeline.step_registry (mrj4001's #642 review, point 1
and point 3): the registry's comment claiming it "matches run_full_pipeline.py
exactly" was never enforced anywhere, so one driver adding/reordering/removing
a stage without the other could silently break progress reporting.

test_registry_order_matches_run_full_pipeline_driver is that enforcement: it
imports run_full_pipeline's own get_stage_order() -- the function it already
uses to validate its --stage CLI argument -- and diffs it against
STEP_REGISTRY's order directly, rather than hand-duplicating a literal stage
list that could itself drift from either driver.

The rest of this file exercises validate_registry() (module-load-time
invariant checks added for the same review's point 3): each test breaks one
invariant at a time via dataclasses.replace() and asserts it is caught, so a
regression in validate_registry() itself fails with a specific assertion
here instead of only surfacing as an opaque ValueError somewhere at import.
"""
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from app.pipeline.step_registry import STEP_REGISTRY, validate_registry

# repo_root/web_interface/backend/tests/test_x.py -> parents[3] == repo_root,
# matching the sys.path setup other backend tests use for `src/` (e.g.
# test_run_duration_coverage.py) and the pipeline suite's own
# src/unified_pipeline/tests/test_run_full_pipeline_exit_status.py for the
# repo-root-level run_full_pipeline module.
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import run_full_pipeline  # noqa: E402


def test_registry_order_matches_run_full_pipeline_driver():
    """STEP_REGISTRY's order must agree, stage for stage, with
    run_full_pipeline.get_stage_order().

    '3' is excluded from the CLI's list: per run_full_pipeline.py's own
    --stage help text it means "run 3a then 3b together", a CLI-only alias
    for two existing stages, not a distinct pipeline stage -- it has no
    STEP_REGISTRY entry and no stage_3 output file.
    """
    cli_stage_order = [s for s in run_full_pipeline.get_stage_order() if s != '3']
    registry_stage_order = [step.stage_id for step in STEP_REGISTRY]
    assert registry_stage_order == cli_stage_order


def test_step_registry_is_a_tuple():
    assert isinstance(STEP_REGISTRY, tuple)


def test_step_definition_is_frozen():
    step = STEP_REGISTRY[0]
    with pytest.raises(FrozenInstanceError):
        step.weight = 999.0  # type: ignore[misc]


def test_registry_passes_its_own_validation():
    validate_registry(STEP_REGISTRY)  # must not raise


def test_validation_catches_duplicate_stage_id():
    bad = (STEP_REGISTRY[0], replace(STEP_REGISTRY[0], number=99))
    with pytest.raises(ValueError, match="duplicate stage_id"):
        validate_registry(bad)


def test_validation_catches_duplicate_number():
    bad = (STEP_REGISTRY[0], replace(STEP_REGISTRY[1], number=STEP_REGISTRY[0].number))
    with pytest.raises(ValueError, match="duplicate step number"):
        validate_registry(bad)


def test_validation_catches_non_contiguous_numbers():
    bad = (replace(STEP_REGISTRY[0], number=1), replace(STEP_REGISTRY[1], number=5))
    with pytest.raises(ValueError, match="contiguous"):
        validate_registry(bad)


def test_validation_catches_non_positive_weight():
    bad = (replace(STEP_REGISTRY[0], number=1, weight=0.0),)
    with pytest.raises(ValueError, match="non-positive weight"):
        validate_registry(bad)


def test_validation_catches_total_weight_not_100():
    bad = (replace(STEP_REGISTRY[0], number=1, weight=1.0),)
    with pytest.raises(ValueError, match="total weight"):
        validate_registry(bad)
