"""The doctor mutation harness (doctor/mutation_harness.py, #1588): one defect
injected into a clean synthetic run per mutation, the catch rate per
COVERAGE.md cell recorded in doctor/MUTATIONS.md and held as a ratchet.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_mutation_harness.py -q -p no:cacheprovider

Self-contained: synthetic CV, real stage 6, no LLM, no network, no PII.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.blind_spots import COVERAGE_PATH  # noqa: E402
from unified_pipeline.doctor.mutation_harness import (  # noqa: E402
    MUTATIONS,
    MUTATIONS_PATH,
    STAGE_3B,
    STAGE_4,
    STAGE_6,
    Measurement,
    artifacts_for,
    cell_counts,
    clean_entries,
    measure,
    recorded_counts,
    render_table,
    rendered_lines,
    write_run,
)

_REGENERATE = "PYTHONPATH=src python3 -m unified_pipeline.doctor.mutation_harness"


@pytest.fixture(scope="module")
def measurement(tmp_path_factory: pytest.TempPathFactory) -> Measurement:
    return measure(tmp_path_factory.mktemp("mutations"))


def test_the_clean_synthetic_run_draws_no_finding(measurement: Measurement) -> None:
    """A caught mutation is a finding the clean run lacks; a clean run with
    findings of its own could hide a mutation behind the same message."""
    assert measurement.clean == frozenset()


def test_every_mutation_changes_the_rendered_document(tmp_path: Path) -> None:
    """A mutation the render cannot show would read as a doctor miss when it
    is the harness that injected nothing."""
    clean = rendered_lines(write_run(tmp_path / "clean", None))
    unchanged = [m.name for m in MUTATIONS
                 if rendered_lines(write_run(tmp_path / m.name, m)) == clean]
    assert unchanged == []


def test_a_mutation_reaches_its_own_stage_and_every_later_one_only() -> None:
    clean = clean_entries()
    by_stage = {m.stage: artifacts_for(m) for m in MUTATIONS}
    assert by_stage[STAGE_3B].stage_3b != clean
    assert by_stage[STAGE_3B].stage_4 != clean
    assert (by_stage[STAGE_4].stage_3b, by_stage[STAGE_4].stage_4 != clean) == (clean, True)
    assert (by_stage[STAGE_6].stage_3b, by_stage[STAGE_6].stage_4) == (clean, clean)
    assert by_stage[STAGE_6].render != clean


_LEVELS = ("section", "entry", "record", "field")


def _matrix_cells() -> set[tuple[str, str]]:
    """(type, level) of every cell of COVERAGE.md's matrix."""
    lines = COVERAGE_PATH.read_text(encoding="utf-8").splitlines()
    cells = set()
    for line in lines[lines.index("## Matrix") + 1:]:
        if line.startswith("## "):
            break
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) == 1 + len(_LEVELS) and parts[0] not in ("type", "---"):
            cells |= {(parts[0], level) for level in _LEVELS}
    return cells


def test_every_mutation_sits_in_a_coverage_matrix_cell() -> None:
    cells = _matrix_cells()
    assert len(cells) == 7 * len(_LEVELS)
    assert [m.name for m in MUTATIONS if (m.defect, m.level) not in cells] == []


def test_no_cell_falls_below_its_recorded_catch_rate(measurement: Measurement) -> None:
    """The ratchet. A cell at 0% recorded stays a pass at 0%; a cell that
    catches fewer mutations than MUTATIONS.md records fails."""
    recorded = recorded_counts(MUTATIONS_PATH.read_text(encoding="utf-8"))
    fell = {cell: (now, recorded[cell]) for cell, now in cell_counts(measurement.outcomes).items()
            if cell in recorded and now[0] * recorded[cell][1] < recorded[cell][0] * now[1]}
    assert fell == {}, f"(caught, mutations) now vs recorded, per cell: {fell}"


def test_mutations_md_is_the_current_measurement(measurement: Measurement) -> None:
    """The table COVERAGE.md links is regenerated whenever a rate, a lint
    that catches a mutation, or the mutation list changes."""
    assert MUTATIONS_PATH.read_text(encoding="utf-8") == render_table(measurement), (
        f"doctor/MUTATIONS.md is stale; regenerate it with: {_REGENERATE}")


def test_recorded_counts_reads_each_cell_row() -> None:
    text = "| cell | caught / mutations | rate |\n|---|---|---|\n| missing / record | 2 / 3 | 67% |\n"
    assert recorded_counts(text) == {("missing", "record"): (2, 3)}


def test_cell_counts_adds_mutations_and_catches_per_cell(measurement: Measurement) -> None:
    counts = cell_counts(measurement.outcomes)
    assert sum(total for _, total in counts.values()) == len(MUTATIONS)
    assert sum(caught for caught, _ in counts.values()) == sum(o.caught for o in measurement.outcomes)


def test_known_catches_and_misses_are_credited_to_the_right_mutation(measurement: Measurement) -> None:
    """Pins two known catches and one known miss, so a harness that credited
    every lint that fires, or none, fails here and not only in the table diff."""
    caught_by = {o.mutation.name: {f.lint for f in o.caught_by} for o in measurement.outcomes}
    assert "owner_missing_from_citation" in caught_by["truncate_authors_dropping_owner"]
    assert "year_not_in_source" in caught_by["shift_grant_end_year"]
    assert caught_by["swap_pi_and_coi"] == set()


def test_the_harness_refuses_an_llm_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A synthetic CV that reached a model would cost money in CI; the
    render's call_llm raises instead."""
    from unified_pipeline import stage_6_word_template

    def reach_the_model(self: object) -> list[object]:
        stage_6_word_template.call_llm(stage="stage_6", messages=[])
        return []

    monkeypatch.setattr(stage_6_word_template.WCMTemplateGenerator,
                        "_reconsider_appendix_entries", reach_the_model)
    with pytest.raises(RuntimeError, match="no LLM call"):
        write_run(tmp_path, None)


def test_mutation_names_are_unique() -> None:
    """Each mutation's run is written to a directory of its name."""
    assert len({m.name for m in MUTATIONS}) == len(MUTATIONS)
