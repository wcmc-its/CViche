"""Coverage-gap tests for `stage3b/classify.py::detect_duplicates` (PR #644
review). Split out from test_stage3b_classify.py so the reply on the PR can
point at one file per gap-closing pass without touching the tests already
there.

Every case here was named by the reviewer and is either UNCOVERED or PARTIAL
per the coverage audit -- see the case-to-test table in the PR reply. Cases
already covered in test_stage3b_classify.py (two identical entries, T vs M2
preference, same non-M2 classification, three duplicates, short text
exclusion, duplicates with different openings) are NOT repeated here.

The #945 section below pins the rule itself: only text that is identical
once case, punctuation and whitespace are ignored is a duplicate.

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

# Two sibling records that share a long template and differ by ONE word.
# Their SequenceMatcher ratio sits just under 0.9; the old rule flagged any
# pair at >= 0.9, so pairs a hair more similar than this one were dropped
# (#945). Kept to prove a near-threshold pair is still not flagged.
_NEAR_THRESHOLD_TEXT_A = "a randomized trial of a new asthma intervention for children"
_NEAR_THRESHOLD_TEXT_B = "a randomized trial of a new asthma intervention for adults"
_NEAR_THRESHOLD_RATIO = SequenceMatcher(
    None, _NEAR_THRESHOLD_TEXT_A, _NEAR_THRESHOLD_TEXT_B
).ratio()

# The #945 bar for what the old 0.9 similarity rule used to call a
# duplicate -- only used to prove a test pair WOULD have been dropped.
_OLD_SIMILARITY_THRESHOLD = 0.9


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
# Same non-M2 classification on both sides (neither M2/T preference branch
# applies -- the plain `else` fallback decides)
# ---------------------------------------------------------------------------

def test_same_non_m2_code_on_both_sides_marks_the_second_entry_duplicate():
    """Reviewer's case 9, "Same non-M2 classification": both entries share
    identical text AND the identical non-M2, non-T taxonomy code (S1), so
    neither of detect_duplicates's M2/T preference branches (`if`/`elif` on
    code1/code2.startswith) has anything to prefer -- the plain `else:
    mark second as duplicate` fallback decides, and the first entry (the
    earlier index) survives untouched."""
    text = "A randomized controlled trial of a new intervention for pediatric asthma"
    entries = [_dup_entry(text, code="S1"), _dup_entry(text, code="S1")]
    updated, pairs = classify.detect_duplicates(entries)

    assert len(pairs) == 1
    assert updated[1]["is_duplicate"] is True  # second entry, marked duplicate
    assert "is_duplicate" not in updated[0]  # first entry, survives


# ---------------------------------------------------------------------------
# Sibling records are NOT duplicates (#945): only key-identical text is
# ---------------------------------------------------------------------------

def _old_rule_similarity(a, b):
    """The normalized SequenceMatcher ratio the pre-#945 rule compared
    against 0.9 -- lowercase, whitespace collapsed, punctuation stripped."""
    def norm(t):
        return " ".join("".join(c for c in t.lower() if c.isalnum() or c.isspace()).split())
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def test_near_threshold_one_word_difference_is_not_flagged():
    assert _NEAR_THRESHOLD_RATIO < _OLD_SIMILARITY_THRESHOLD

    entries = [_dup_entry(_NEAR_THRESHOLD_TEXT_A), _dup_entry(_NEAR_THRESHOLD_TEXT_B)]
    _, pairs = classify.detect_duplicates(entries)
    assert pairs == []


@pytest.mark.parametrize(
    "text_a, text_b",
    [
        # One distinguishing word, identical years -- the workshop shape.
        (
            "2017-2021   Instructor, Medical Student Airway Workshop, Springfield, IL",
            "2017-2021   Instructor, Medical Student Suture Workshop, Springfield, IL",
        ),
        # Only the year differs -- a recurring award or annual course.
        (
            "Annual Regional Teaching Excellence Award for Clinical Faculty, 2019",
            "Annual Regional Teaching Excellence Award for Clinical Faculty, 2020",
        ),
        # A word AND the year differ -- the Junior/Senior award shape.
        (
            "2019   Best Junior Resident Team Player Award, Recipient",
            "2020   Best Senior Resident Team Player Award, Recipient",
        ),
        # Same article title, different publication date.
        (
            "Doe A. Case Series: Wrist Injuries. Example EM Pearls. Published January 27, 2024.",
            "Doe A. Case Series: Wrist Injuries. Example EM Pearls. Published May 25, 2024.",
        ),
    ],
    ids=["one-word", "year-only", "word-and-year", "same-title-different-date"],
)
def test_sibling_records_above_the_old_threshold_are_not_flagged(text_a, text_b):
    """Each pair clears the old 0.9 similarity bar -- asserted, so the test
    proves the pair WOULD have been dropped before #945 -- yet is two
    distinct records. Neither may be marked duplicate: stage 4 filters a
    duplicate out before extraction, so a false flag is a silent loss."""
    assert _old_rule_similarity(text_a, text_b) >= _OLD_SIMILARITY_THRESHOLD

    updated, pairs = classify.detect_duplicates([_dup_entry(text_a), _dup_entry(text_b)])
    assert pairs == []
    assert not any(e.get("is_duplicate") for e in updated)


def test_token_split_by_spacing_is_still_a_duplicate():
    """"2014-present" and "2014 - present" differ only in spacing around the
    punctuation, but stripping punctuation leaves them as one token vs two
    ("2014present" vs "2014  present"). The key drops whitespace as well as
    punctuation, so the pair still matches."""
    entries = [
        _dup_entry("Associate Professor of Medicine, 2014-present"),
        _dup_entry("Associate Professor of Medicine, 2014 - present"),
    ]
    updated, pairs = classify.detect_duplicates(entries)
    assert len(pairs) == 1
    assert pairs[0]["similarity"] == 1.0  # artifact shape kept; always 1.0 now
    assert updated[1]["is_duplicate"] is True


def test_non_ascii_letter_difference_is_not_a_duplicate():
    """Two records that differ only by one non-ASCII letter (a different
    accented vowel in a surname) are two records. The key keeps every
    Unicode word character, so the letters stay in and the keys differ; a
    key built from ASCII [a-z0-9] only would drop both letters and collapse
    the pair into a false duplicate."""
    entries = [
        _dup_entry("Keynote lecture, Nordic Example Society, hosted by Dr. Hölm, 2019"),
        _dup_entry("Keynote lecture, Nordic Example Society, hosted by Dr. Hålm, 2019"),
    ]
    updated, pairs = classify.detect_duplicates(entries)
    assert pairs == []
    assert not any(e.get("is_duplicate") for e in updated)


def test_sibling_near_an_exact_pair_leaves_one_copy_of_the_pair():
    """A sibling record followed by two identical copies of its neighbour.
    Under the old similarity rule the sibling "matched" BOTH copies, so both
    were marked duplicate and every copy of that record was lost. Now the
    sibling is untouched and exactly one copy of the identical pair
    survives."""
    sibling = "2017-2021   Instructor, Medical Student Suture Workshop, Springfield, IL"
    copy = "2017-2021   Instructor, Medical Student Airway Workshop, Springfield, IL"
    updated, pairs = classify.detect_duplicates(
        [_dup_entry(sibling), _dup_entry(copy), _dup_entry(copy)]
    )
    assert [(p["entry1_idx"], p["entry2_idx"]) for p in pairs] == [(1, 2)]
    assert "is_duplicate" not in updated[0]
    assert "is_duplicate" not in updated[1]
    assert updated[2]["is_duplicate"] is True


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
