"""`core/run_id.py`: the shape of a web run id (#457).

    python3 -m pytest src/unified_pipeline/tests/test_run_id.py -p no:cacheprovider

Self-contained: pure strings. The backend pins `generate_run_id` to this shape in
web_interface/backend/tests/test_upload_run_id_collision.py.
"""

import string
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.run_id import is_run_id  # noqa: E402


@pytest.mark.parametrize("uid", ["QZKMRT", "AAAAAA", "ZZZZZZ", "AB1CDE", "I5NKUG", "000000", "AB-CDE", "AB_CDE"])
def test_six_uppercase_letters_digits_dash_or_underscore_is_a_run_id(uid):
    assert is_run_id(uid)


def test_every_letter_of_the_generator_alphabet_is_accepted():
    assert all(is_run_id(letter * 6) for letter in string.ascii_uppercase)


@pytest.mark.parametrize("uid", [
    "",
    "Zephyr",         # six characters, mixed case: a filename-style stem
    "zephyr",
    "QZKMR",          # one short
    "QZKMRTU",        # one long
    "QZKMRT\n",       # fullmatch, not match-with-$: a trailing newline is not a run id
    "QZKMRT_cv",
    "2015_Doe",
    "web151",
    "ab-cde",
])
def test_anything_else_is_not_a_run_id(uid):
    assert not is_run_id(uid)
