"""blind_spots (doctor/blind_spots.py, #1588): the "Not checked" sentences of
doctor/COVERAGE.md, which the review copy's "not checked" box reads (#1589).

    python3 -m pytest src/unified_pipeline/tests/test_doctor_blind_spots.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.blind_spots import (  # noqa: E402
    COVERAGE_PATH,
    BlindSpot,
    blind_spots,
)

_LEVELS = ("section", "entry", "record", "field")


def _blind_cells_in_matrix() -> set[tuple[str, str]]:
    """(type, level) of every matrix cell that opens with BLIND."""
    lines = COVERAGE_PATH.read_text(encoding="utf-8").splitlines()
    start = lines.index("## Matrix")
    cells = set()
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) != 1 + len(_LEVELS) or parts[0] in ("type", "---"):
            continue
        cells |= {(parts[0], level) for level, cell in zip(_LEVELS, parts[1:], strict=True) if cell.startswith("BLIND")}
    return cells


def test_every_blind_cell_has_one_sentence_and_every_sentence_a_blind_cell():
    spots = blind_spots()
    assert spots and all(isinstance(s, BlindSpot) for s in spots)
    named = [(s.type, s.level) for s in spots]
    assert len(named) == len(set(named))
    assert set(named) == _blind_cells_in_matrix()


def test_the_sentences_are_plain_and_whole():
    for spot in blind_spots():
        assert spot.sentence.startswith("CViche ") and spot.sentence.endswith(".")
        assert "`" not in spot.sentence and "#" not in spot.sentence  # no lint keys or issue numbers


def _doc(tmp_path, body):
    path = tmp_path / "COVERAGE.md"
    path.write_text(f"# Contract\n\n## Not checked\n\n{body}\n## After\n\n- `missing / field`: Not read.\n")
    return path


def test_the_section_ends_at_the_next_heading_and_skips_prose(tmp_path):
    path = _doc(tmp_path, "Some prose.\n\n- `wrong value / record`: CViche cannot see it.\n")
    assert blind_spots(path) == (BlindSpot("wrong value", "record", "CViche cannot see it."),)


@pytest.mark.parametrize("body", ["Only prose.\n", "- wrong value / record: no backticks.\n",
                                  "- `wrong value / record`: no full stop\n"])
def test_an_empty_or_malformed_section_fails_closed(tmp_path, body):
    with pytest.raises(ValueError):
        blind_spots(_doc(tmp_path, body))


def test_a_file_without_the_section_fails_closed(tmp_path):
    path = tmp_path / "COVERAGE.md"
    path.write_text("# Contract\n")
    with pytest.raises(ValueError):
        blind_spots(path)
