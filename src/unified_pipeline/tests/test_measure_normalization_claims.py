"""`scripts/measure_normalization_claims.py` must not print CV text.

The script's whole purpose is to be pointed at the real CV farm and have its
output pasted into a pull request, so its module docstring promises it
"prints counts and character-class SHAPES, never a CV-derived value". Round 2
shipped it with that promise already broken: `measure_taxonomy_shape` printed
`sorted(bracketed)`, the bracketed tokens harvested from the farm, verbatim.

These tests run the script over a SYNTHETIC farm whose values AND one of
whose filenames carry tokens that occur nowhere else, and fail if any of
those tokens reaches stdout.

    python3 -m pytest src/unified_pipeline/tests/test_measure_normalization_claims.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx, and no access to the real
farm -- every artifact is written into pytest's tmp_path.
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_SCRIPT = _SRC.parent / "scripts" / "measure_normalization_claims.py"
_spec = importlib.util.spec_from_file_location("measure_normalization_claims", _SCRIPT)
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)


# Tokens that appear in no CV, no fixture and no other test, so a substring
# check for them is a leak check and nothing else.
_BRACKETED_TOKEN = "Zyzzyva"
_SURNAME = "Qwghlm"
_INITIALS = "VX"
# Initials-shaped, so they reach the surname slot, and distinctive enough
# that finding one in the output can only mean the orphan measure printed a
# token instead of its shape.
_ORPHANS = ("QZ", "XJ")
# The farm's real filenames embed the CV owner's name, so a filename is as
# leakable as a value. This one is written unparseable to force `_load`'s
# error path, the only place a filename could reach stdout.
_UNREADABLE_STEM = "Vhorlspruit"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _synthetic_farm(root: Path) -> Path:
    """A farm-shaped directory holding only invented names."""
    farm = root / "outputs"
    _write(farm / "stage_3b_classified_entries" / "AAAAAA.json", {
        "entries": [{"text": f"[{_BRACKETED_TOKEN}] Journal of Examples"}],
    })
    _write(farm / "stage_4_field_extraction" / "AAAAAA.json", {
        "entries": [
            {
                "taxonomy_code": "S6",
                "text": "1. A Study.",
                "extracted_fields": {
                    "authors": f"{_SURNAME}, {_INITIALS}, {_SURNAME}, {_INITIALS}",
                },
            },
            {
                # The farm's one surname-slot orphan, in invented names: two
                # single-letter tokens standing where a surname should be,
                # between two ordinary pairs.
                "taxonomy_code": "S6",
                "text": "2. Another Study.",
                "extracted_fields": {
                    "authors": f"{_SURNAME}, {_INITIALS}, K, T, {_SURNAME}2, RR",
                },
            },
            {
                # The same shape with traceable orphan tokens, so a measure
                # that printed the token rather than its shape is caught.
                "taxonomy_code": "S6",
                "text": "3. A Third Study.",
                "extracted_fields": {
                    "authors": (f"{_SURNAME}3, {_INITIALS}, {_ORPHANS[0]}, "
                                f"{_ORPHANS[1]}, {_SURNAME}4, RR"),
                },
            },
        ],
    })
    (farm / "stage_4_field_extraction" / f"{_UNREADABLE_STEM}.json").write_text(
        "{not json"
    )
    return farm


def test_the_taxonomy_measure_prints_a_shape_and_not_the_token(tmp_path, capsys):
    """The regressed line (round 2's `print(... sorted(bracketed))`): a
    bracketed token lifted out of a CV was printed verbatim, which put CV
    text into output written to be pasted into a pull request."""
    measure.measure_taxonomy_shape(_synthetic_farm(tmp_path))
    out = capsys.readouterr().out
    assert _BRACKETED_TOKEN not in out
    assert "values opening with a [token]    : 1" in out
    assert "distinct [token]s                : 1" in out
    assert "those tokens, by shape           : {'Aaaaaaa': 1}" in out


def test_the_orphan_measure_prints_a_shape_and_not_the_author_token(tmp_path, capsys):
    """The same promise on the measure added for the surname-slot orphan:
    the tokens it counts are author-name tokens, so it may report their
    shapes and their run lengths and nothing else."""
    measure.measure_pair_parser_orphans(_synthetic_farm(tmp_path))
    out = capsys.readouterr().out
    assert _SURNAME not in out
    assert _INITIALS not in out
    for orphan in _ORPHANS:
        assert orphan not in out
    assert "distinct author strings          : 3" in out
    assert "reaching the pair parser         : 3" in out
    assert "strings hitting the surname slot : 2" in out
    assert "tokens placed by that branch     : 4" in out
    assert "those tokens, by shape           : {'A': 2, 'AA': 2}" in out
    assert "consecutive-orphan runs, by len  : {2: 2}" in out
    assert "mirror/dispatcher disagreements  : 0" in out


def test_no_measure_prints_any_synthetic_farm_value(tmp_path, capsys):
    """The promise across the whole script, not one measure at a time: a
    full run over a farm whose every value is invented must print none of
    those values back."""
    farm = _synthetic_farm(tmp_path)
    assert measure.main([str(farm)]) == 0
    out = capsys.readouterr().out
    for token in (_BRACKETED_TOKEN, _SURNAME, _INITIALS,
                  "Journal of Examples", _UNREADABLE_STEM, *_ORPHANS):
        assert token not in out, f"{token!r} leaked into the script's output"


def test_an_unreadable_artifact_is_named_by_a_masked_id_not_by_its_filename(
        tmp_path, capsys):
    """`_load`'s error path is the one place a farm FILENAME could reach
    stdout, and every farm filename embeds a person's name. It is reported
    as the stage directory plus a digest, and the exception as its type --
    an OSError's message would carry the path it failed on."""
    farm = _synthetic_farm(tmp_path)
    assert measure.main([str(farm)]) == 0
    out = capsys.readouterr().out
    assert _UNREADABLE_STEM not in out
    digest = hashlib.sha256(f"{_UNREADABLE_STEM}.json".encode()).hexdigest()[:8]
    assert (f"! unreadable, skipped: stage_4_field_extraction/{digest} "
            "(JSONDecodeError)") in out


def test_token_shape_keeps_length_and_case_and_drops_the_characters():
    """`_token_shape` is the redaction boundary every per-token line goes
    through, so its mapping is pinned rather than assumed."""
    assert measure._token_shape("Editor") == "Aaaaaa"
    assert measure._token_shape("M2B") == "A9A"
    assert measure._token_shape("d.o.b") == "a.a.a"
    assert measure._token_shape("") == ""


def test_the_orphan_mirror_reproduces_the_real_parser():
    """`_surname_slot_orphans` is a copy of `_parse_surname_initial_pairs`
    with the orphan tokens recorded, and the measure only trusts it when
    the two agree. The shapes it reports are the corpus fact authors.py
    cites, so the copy is pinned here too."""
    from unified_pipeline.stage6.normalization.authors import (
        _parse_surname_initial_pairs,
    )
    for parts in (
        ["Alpha", "A", "E", "L", "Bravo", "B"],
        ["Alpha", "R", "Bravo", "R"],
        ["AB", "PL*", "Smith", "JA"],
        ["Smith", "John", "Jr.", "Brown"],
    ):
        elements, orphans, runs = measure._surname_slot_orphans(parts)
        assert elements == _parse_surname_initial_pairs(parts)
        assert all(o in parts for o in orphans)
        assert sum(runs) == len(orphans)
    _elements, orphans, runs = measure._surname_slot_orphans(
        ["Alpha", "A", "E", "L", "Bravo", "B"]
    )
    assert orphans == ["E", "L"]
    assert runs == [2]
