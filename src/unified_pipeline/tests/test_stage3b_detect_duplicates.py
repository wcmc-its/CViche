"""Coverage-gap tests for `stage3b/classify.py::detect_duplicates` (PR #644
review). Split out from test_stage3b_classify.py so the reply on the PR can
point at one file per gap-closing pass without touching the tests already
there.

Every case here was named by the reviewer and is either UNCOVERED or PARTIAL
per the coverage audit -- see the case-to-test table in the PR reply. Cases
already covered in test_stage3b_classify.py (two identical entries, T vs M2
preference, same non-M2 classification, three duplicates, short text
exclusion, different-first-100-chars near-duplicates) are NOT repeated here.

`_dup_entry`'s default `code="S1"` puts the SAME taxonomy code on both sides
of every pair unless a test overrides it -- that default is what makes
detect_duplicates's M2/T preference branches (the `if`/`elif` pair on
code1/code2.startswith) invisible unless a test passes `code=` explicitly on
both sides, which the two preference tests below do.

Self-contained: no LLM, no DB, no network, no PII.
"""
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage3b.classify as classify  # noqa: E402

# A pair of already-normalized (lowercase, single-spaced, no punctuation)
# texts whose SequenceMatcher ratio sits just under the default 0.9
# threshold. Computed once so the "just below" and "exactly at" tests share
# one fixed, known ratio instead of each guessing at a boundary value.
_NEAR_THRESHOLD_TEXT_A = "a randomized trial of a new asthma intervention for children"
_NEAR_THRESHOLD_TEXT_B = "a randomized trial of a new asthma intervention for adults"
_NEAR_THRESHOLD_RATIO = SequenceMatcher(
    None, _NEAR_THRESHOLD_TEXT_A, _NEAR_THRESHOLD_TEXT_B
).ratio()


def _dup_entry(text, code="S1"):
    return {"text": text, "taxonomy_code": code, "hierarchy": ["PUBLICATIONS"]}


# ---------------------------------------------------------------------------
# Empty input / trivial sizes
# ---------------------------------------------------------------------------

def test_no_entries_returns_empty_lists_without_raising():
    result = classify.detect_duplicates([])
    assert result == ([], [])


def test_single_entry_produces_no_pairs_and_is_not_marked():
    entries = [_dup_entry("A randomized controlled trial of a new intervention for asthma")]
    updated, pairs = classify.detect_duplicates(entries)
    assert pairs == []
    assert "is_duplicate" not in updated[0]


# ---------------------------------------------------------------------------
# normalize_text drivers: punctuation / whitespace / case
# ---------------------------------------------------------------------------

def test_punctuation_only_difference_is_still_flagged_duplicate():
    """Only punctuation (each word quoted, a run of trailing '!') differs
    between the two entries -- enough of it that the raw (unstripped)
    similarity ratio sits BELOW the default 0.9 threshold, so the pair only
    matches because normalize_text's re.sub(r'[^\\w\\s]', '', text) strips it
    (a single stray comma is too small a change to prove the strip actually
    drives the match, since SequenceMatcher tolerates it either way)."""
    base = "randomized trial of a novel asthma drug therapy"
    quoted = " ".join(f'"{word}"' for word in base.split()) + "!" * 10
    entries = [_dup_entry(base), _dup_entry(quoted)]
    assert entries[0]["text"] != entries[1]["text"]
    assert SequenceMatcher(None, entries[0]["text"], entries[1]["text"]).ratio() < 0.9

    _, pairs = classify.detect_duplicates(entries)
    assert len(pairs) == 1


def test_whitespace_only_difference_is_still_flagged_duplicate():
    """Only whitespace (a doubled space) differs; normalize_text's
    re.sub(r'\\s+', ' ', text) collapse must make the pair match anyway."""
    entries = [
        _dup_entry("A randomized controlled  trial of a new intervention for asthma"),
        _dup_entry("A randomized controlled trial of a new intervention for asthma"),
    ]
    assert entries[0]["text"] != entries[1]["text"]

    _, pairs = classify.detect_duplicates(entries)
    assert len(pairs) == 1


def test_case_only_difference_is_still_flagged_duplicate():
    """Only letter case differs; normalize_text's .lower() must make the
    pair match anyway."""
    entries = [
        _dup_entry("A RANDOMIZED CONTROLLED TRIAL OF A NEW INTERVENTION FOR ASTHMA"),
        _dup_entry("a randomized controlled trial of a new intervention for asthma"),
    ]
    assert entries[0]["text"] != entries[1]["text"]

    _, pairs = classify.detect_duplicates(entries)
    assert len(pairs) == 1


# ---------------------------------------------------------------------------
# M2 vs T preference (detect_duplicates's `elif` branch, the ordering never covered)
# ---------------------------------------------------------------------------

def test_m2_first_t_second_still_marks_the_t_entry_as_duplicate():
    """Same M2-vs-T preference as test_m2_preferred_over_t_when_marking_the_duplicate
    in test_stage3b_classify.py, but with the M2-coded entry listed FIRST and
    the T-coded entry SECOND -- this is detect_duplicates's
    `elif code2.startswith("T") and code1.startswith("M")` branch, the
    mirror image of the `if` branch that file already exercises."""
    text = "Funded research grant description that is long enough to compare properly here"
    entries = [_dup_entry(text, code="M2A"), _dup_entry(text, code="T")]
    updated, pairs = classify.detect_duplicates(entries)

    assert len(pairs) == 1
    assert updated[1]["is_duplicate"] is True  # T-coded, second, marked duplicate
    assert "is_duplicate" not in updated[0]  # M2A-coded, first, survives


# ---------------------------------------------------------------------------
# Similarity threshold boundary (detect_duplicates's `sim >= similarity_threshold` check)
# ---------------------------------------------------------------------------

def test_similarity_just_below_default_threshold_is_not_flagged():
    """_NEAR_THRESHOLD_TEXT_A/B's ratio is a known value under the default
    0.9 threshold -- not merely dissimilar text, an actual near-boundary
    ratio -- so this exercises the `>=` cutoff itself, not just "unrelated
    text doesn't match"."""
    assert _NEAR_THRESHOLD_RATIO < 0.9

    entries = [_dup_entry(_NEAR_THRESHOLD_TEXT_A), _dup_entry(_NEAR_THRESHOLD_TEXT_B)]
    _, pairs = classify.detect_duplicates(entries)
    assert pairs == []


def test_similarity_exactly_at_threshold_is_flagged():
    """Same pair as the "just below" test above, but with
    similarity_threshold set to the pair's own computed ratio: sim ==
    threshold must still count as a match, proving detect_duplicates's
    `sim >= similarity_threshold` comparison is `>=` and not strict `>`."""
    entries = [_dup_entry(_NEAR_THRESHOLD_TEXT_A), _dup_entry(_NEAR_THRESHOLD_TEXT_B)]
    _, pairs = classify.detect_duplicates(entries, similarity_threshold=_NEAR_THRESHOLD_RATIO)
    assert len(pairs) == 1


# ---------------------------------------------------------------------------
# Excluded / unusual text values
# ---------------------------------------------------------------------------

def test_empty_text_entry_excluded_and_does_not_break_other_pairs():
    """An entry with text="" (the key IS present) must be excluded from
    comparison and must not raise or corrupt indices for a genuine duplicate
    pair recorded elsewhere in the same list."""
    text = "A randomized controlled trial of a new intervention for asthma"
    entries = [_dup_entry(""), _dup_entry(text), _dup_entry(text)]
    updated, pairs = classify.detect_duplicates(entries)

    assert len(pairs) == 1
    assert "is_duplicate" not in updated[0]
    assert updated[2]["is_duplicate"] is True


def test_none_text_entry_excluded_and_does_not_raise():
    """An entry with `"text": None` (the key is present with a None value)
    used to reach `len(None)` and raise TypeError, because `.get("text",
    "")`'s default only applies when the key is ABSENT. It must now be
    treated as a non-candidate instead -- excluded like any other entry with
    no usable text, with the rest of the list unaffected."""
    text = "A randomized controlled trial of a new intervention for asthma"
    entries = [_dup_entry(None), _dup_entry(text), _dup_entry(text)]

    updated, pairs = classify.detect_duplicates(entries)

    assert len(pairs) == 1
    assert "is_duplicate" not in updated[0]
    assert updated[2]["is_duplicate"] is True


def test_shared_first_100_chars_with_different_tails_not_flagged():
    """Two entries share an identical opening clause of >=100 normalized
    characters but diverge materially afterward. If the comparison only
    looked at a truncated preview (or a first-100-char blocking key) this
    would false-positive; comparing the FULL normalized text keeps it
    unflagged."""
    shared = (
        "This is a long shared opening clause that repeats verbatim across "
        "two entirely different entries and goes on for a while to make "
        "sure it clears one hundred characters exactly here now"
    )
    assert len(shared) >= 100

    tail_a = (
        " describing a randomized clinical trial of a novel asthma therapy "
        "administered to pediatric patients across several independent "
        "research centers nationwide over a five year period"
    )
    tail_b = (
        " concerning an entirely unrelated editorial appointment to the "
        "board of a peer reviewed journal focused on epidemiology and "
        "public health policy matters"
    )
    text_a = shared + tail_a
    text_b = shared + tail_b
    assert text_a[:100] == text_b[:100]
    assert text_a != text_b

    entries = [_dup_entry(text_a), _dup_entry(text_b)]
    _, pairs = classify.detect_duplicates(entries)
    assert pairs == []


# ---------------------------------------------------------------------------
# One entry recurring across multiple recorded pairs
# ---------------------------------------------------------------------------

def test_repeated_entry_recurs_across_both_pair_records():
    """Three identical entries produce two pair records (0,1) and (0,2), per
    detect_duplicates's own comment on the duplicate-pairs loop: one entry
    (index 0) CAN appear in more than one recorded pair. Inspect entry1_idx/entry2_idx directly
    rather than inferring the claim from len(pairs) and the is_duplicate
    flags alone."""
    text = "A randomized controlled trial of a new intervention for pediatric asthma"
    entries = [_dup_entry(text), _dup_entry(text), _dup_entry(text)]
    _, pairs = classify.detect_duplicates(entries)

    assert len(pairs) == 2
    # Index 0 (the surviving original) is the entry that recurs.
    assert {pair["entry1_idx"] for pair in pairs} == {0}
    assert {pair["entry2_idx"] for pair in pairs} == {1, 2}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
