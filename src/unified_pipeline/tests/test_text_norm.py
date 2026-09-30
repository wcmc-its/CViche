"""core.text_norm: the public helpers, and the segmentation_regression
aliases that keep pre-move importers working."""

from unified_pipeline import segmentation_regression as segreg
from unified_pipeline.core import text_norm


def test_norm_folds_marks_whitespace_and_case():
    assert text_norm.norm("Zoë  Brändström\t") == "zoe brandstrom"
    assert text_norm.norm(None) == ""


def test_squash_drops_all_whitespace():
    assert text_norm.squash(" A b\tC\n") == "abc"


def test_looks_like_record_needs_length_and_a_delimiter():
    assert text_norm.looks_like_record("x" * 57 + " | y")  # 61 chars
    assert not text_norm.looks_like_record("x" * 56 + " | y")  # 60 chars
    assert text_norm.looks_like_record("x" * 60 + "\ty")
    assert not text_norm.looks_like_record("x" * 70)
    assert not text_norm.looks_like_record("a | b")


def test_segmentation_regression_aliases_are_the_public_functions():
    assert segreg._norm is text_norm.norm
    assert segreg._squash is text_norm.squash
    assert segreg._looks_like_record is text_norm.looks_like_record
    assert segreg.SUBSTANTIVE_LINE_CHARS == text_norm.SUBSTANTIVE_LINE_CHARS == 15
