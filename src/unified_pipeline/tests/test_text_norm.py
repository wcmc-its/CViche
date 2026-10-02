"""core.text_norm: the public helpers."""

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
    assert not text_norm.looks_like_record("  " + "x" * 56 + " | y  ")  # 60 stripped


def test_fold_quotes_maps_typographic_apostrophes_and_quotes_to_ascii():
    """#1232: a source header 'LOCAL (CONT<U+2019>D)' and the hierarchy node
    'LOCAL (CONT'D)' must compare equal."""
    curly = "LOCAL (CONT\u2019D) \u2018a\u2019 \u201cb\u201d \u02bc \u2032 \u201e \u2033"
    assert text_norm.fold_quotes(curly) == "LOCAL (CONT'D) 'a' \"b\" ' ' \" \""
    assert text_norm.fold_quotes("plain 'ascii' text") == "plain 'ascii' text"
    assert text_norm.fold_quotes(None) == ""


def test_norm_leaves_typographic_apostrophes_alone():
    """The fold is a separate helper on purpose: widening `norm` would move
    every coverage and render metric built on it (#1232)."""
    assert text_norm.norm("O\u2019Neil") == "o\u2019neil"

