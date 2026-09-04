"""Regression tests for #560: `_normalize_author_names` silently deleted
authors and initials instead of keeping them.

`_normalize_author_names` (`stage6/normalization/authors.py`) has exactly one
caller, `_format_citation` (`stage6/formatting/values.py:17`), reached via
`authors = fields.get('authors', '')` -> `_normalize_author_names(authors)`
whenever `formatting_source != 'stage_5d_llm'`. These tests assert through
that wire, not just the helper, since that is the only path that reaches
the rendered page.

The pair detector (`^[A-Z]{1,4}$` / `^[A-Z](-[A-Z])+$`, ASCII-uppercase
only) rejects a "Surname, Initials" list the instant one element doesn't
fit that shape -- a lowercase or non-ASCII single initial, a full given
name, a name suffix, or one missing comma anywhere in the list. Before this
fix, the fallback path that runs next then discarded every comma-split
token it didn't recognise as a standalone name (an initials group, a
suffix, a bare 1-2 character fragment) instead of keeping it. Because one
missing comma anywhere in the list is enough to reject the whole string,
that discard rule was capable of stripping every later author's initials
from a citation, not just the one malformed entry -- reproduced live in the
delivered document (W0MTVW / HU4DXA, "24. Sanguino SM, Konopasek, Raszka,
Bostwick, Smith...").

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_author_name_normalization.py -p no:cacheprovider
"""

import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.formatting.values import _format_citation  # noqa: E402
from unified_pipeline.stage6.normalization.authors import (  # noqa: E402
    _parse_author_fallback,
    _parse_surname_initial_pairs,
)


def _cite(authors: str) -> str:
    """The rendered citation string for a minimal publication entry whose
    only variable is the raw `authors` field -- the wire `_format_citation`
    actually walks, not a direct call into the normalizer."""
    entry = {
        'extracted_fields': {'authors': authors, 'title': 'A Study'},
        'enrichment_data': {},
        'enriched_fields': [],
    }
    citation, _target_name, _enriched = _format_citation(entry, 1)
    return citation


# --------------------------------------------------------------------------
# The 8 reproduction lines from #560, plus the docstring's own broken third
# example. None may drop a letter that was present in the input authors
# string (allowing for punctuation/case normalization); the live #560
# defect is deletion, not reformatting.
# --------------------------------------------------------------------------

def test_correct_pairs_are_unaffected():
    """The one reproduction line #560 itself marks '(correct)' must stay
    exactly as it was -- a regression guard on the pairs branch."""
    assert _cite('Chen, IY, Gheysens, O, Ray, S, Wang, Q') == (
        '1. Chen IY, Gheysens O, Ray S, Wang Q. A Study.'
    )


def test_one_missing_comma_no_longer_strips_every_later_initial():
    """The amplification case: 'Wen S' written without its comma flips the
    pair detector's parity for the WHOLE list, not just that one author.
    Before the fix every initial after it was deleted."""
    assert _cite('Chen, IY, Gheysens, O, Wen S, Wang, Q') == (
        '1. Chen IY, Gheysens O, Wen S, Wang Q. A Study.'
    )


def test_trailing_initials_group_is_kept_not_dropped():
    citation = _cite('Garcia, Maria, Lopez, AB')
    assert 'AB' in citation
    assert citation == '1. Garcia, Maria, Lopez AB. A Study.'


def test_suffix_and_trailing_initials_both_kept():
    citation = _cite('Smith, John, Jr., Brown, AB')
    assert 'AB' in citation
    assert 'Jr' in citation
    assert citation == '1. Smith, John Jr, Brown AB. A Study.'


def test_two_character_surname_is_not_deleted():
    """'Li' is a real 2-character surname (as are 'Wang', 'Wei' here), not
    an initials fragment to discard -- the old case-blind <=2-character
    skip could not tell a short surname from a short initials group and
    dropped both alike. Round-1 fix: only an ALL-CAPS 1-2 character token
    is initials-shaped; a mixed-case one is kept as its own author, merged
    or standalone exactly as `_looks_like_initials` already treats it
    everywhere else in this function."""
    citation = _cite('Chen, Li, Wang, Wei')
    assert 'Li' in citation
    assert citation == '1. Chen, Li, Wang, Wei. A Study.'


def test_run_of_short_surnames_with_no_open_predecessor_is_not_deleted():
    """Round-1 regression (#560 fallback path): consecutive short,
    mixed-case surnames with nothing 'open' to merge into -- each one was
    a case-blind <=2-character 'fragment' and got silently dropped one
    after another, emptying the whole citation. None may be lost."""
    assert _cite('Li, Wu, Ma, Ye') == '1. Li, Wu, Ma, Ye. A Study.'


def test_short_surname_next_to_a_real_initials_pair_is_not_deleted():
    """Mix of a genuine stray-comma 'Surname, Initial' pair (merges, as
    intended) and short surnames with nothing open to merge into (kept
    standalone, not dropped) -- the exact shape of the round-1 finding."""
    assert _cite('Wu, J, Li, X, Chen, Ming') == (
        '1. Wu J, Li X, Chen, Ming. A Study.'
    )


def test_single_lowercase_initial_is_recognised():
    assert _cite('Kelly, r') == '1. Kelly R. A Study.'


def test_single_non_ascii_initial_is_recognised():
    assert _cite('Kelly, Å') == '1. Kelly Å. A Study.'


def test_trailing_odd_author_is_emitted_not_dropped():
    """The pairs branch's old `while i < len(parts) - 1` bound silently
    dropped a trailing unpaired author entirely."""
    citation = _cite('Kelly, R, Pirog')
    assert 'Pirog' in citation
    assert citation == '1. Kelly R, Pirog. A Study.'


def test_docstrings_own_broken_example_is_pinned_as_it_actually_behaves():
    """The docstring promised 'Smith, John A., Jones, Mary B.' ->
    'Smith JA, Jones MB'; on dev it actually produces 'Smith, John A,
    Jones, Mary B' (full given names are not initials, so the pair
    detector correctly declines this shape). This pins the true,
    non-destructive behaviour -- nothing is deleted -- not the promise."""
    assert _cite('Smith, John A., Jones, Mary B.') == (
        '1. Smith, John A, Jones, Mary B. A Study.'
    )


# --------------------------------------------------------------------------
# The exact farm defect (W0MTVW / HU4DXA)
# --------------------------------------------------------------------------

def test_farm_defect_sanguino_citation_regains_its_initials():
    """The live corpus defect: 'Sanguino SM' is already a complete
    "Surname Initials" unit, which throws off the pair detector's parity
    for the rest of a perfectly regular surname/initials list."""
    authors = 'Sanguino SM, Konopasek, L, Raszka, WV, Bostwick, S, Smith, S'
    assert _cite(authors) == (
        '1. Sanguino SM, Konopasek L, Raszka WV, Bostwick S, Smith S. A Study.'
    )


# --------------------------------------------------------------------------
# Pre-existing behaviour that must not regress
# --------------------------------------------------------------------------

def test_double_comma_cleanup_still_works():
    assert _cite('Watson, K.,,') == '1. Watson K. A Study.'


def test_already_vancouver_form_is_unchanged():
    assert _cite('Smith JA, Jones MB') == '1. Smith JA, Jones MB. A Study.'


# --------------------------------------------------------------------------
# Fixed behaviour, not pre-existing: on origin/dev the pairs branch's old
# `while i < len(parts) - 1` bound (see test_trailing_odd_author_is_kept_
# not_dropped above) silently dropped a trailing unpaired element -- and
# "et al" is exactly that shape once "Smith, JA, et al" is comma-split into
# ["Smith", "JA", "et al"] (3 parts, an odd trailing element). Verified by
# mutation: this test FAILS against origin/dev's text.py (dev emits
# '1. Smith JA. A Study.', silently dropping the "et al" marker) and PASSES
# against this branch's fix.
# --------------------------------------------------------------------------

def test_et_al_is_still_recognised():
    assert _cite('Smith, JA, et al') == '1. Smith JA, et al. A Study.'


def test_stage_5d_llm_entries_bypass_normalization_entirely():
    """formatting_source == 'stage_5d_llm' returns the LLM-formatted
    citation untouched -- #560 cannot reach roughly 2417 of the farm's
    3341 publication entries because of this branch, mentioned here so the
    scope of the fix above is not overstated."""
    entry = {
        'extracted_fields': {
            'formatted_citation': 'Smith, J, Random-garbage-XYZ. Some Title.',
            'formatting_source': 'stage_5d_llm',
        },
    }
    citation, _target, _enriched = _format_citation(entry, 3)
    assert citation == '3. Smith, J, Random-garbage-XYZ. Some Title.'


# --------------------------------------------------------------------------
# Round-2 review response. The fallback's orphan branch still DROPPED an
# ALL-CAPS fragment that had no "open" preceding author to merge into, so
# the issue's "nothing on this path should ever reduce the token count" bar
# was not actually met -- and it was live on the farm (2015_Wende, entry 5).
# The same fragment shape was already emitted standalone by the pairs
# branch's trailing-element case, so the two branches also disagreed.
# --------------------------------------------------------------------------

def test_orphan_initials_fragment_with_no_open_predecessor_is_kept():
    """The live farm string (2015_Wende, entry 5). "Shariati H" already
    carries its own initials, so it is not an open merge target; the "F"
    that follows it therefore had nothing to attach to and was dropped --
    a deleted token in a delivered document, which is the whole of #560.
    It is now emitted as its own element instead."""
    authors = (
        'Kaczynski AT, Wende ME, Schipperijn J, Hughey SM, Stowe, EW, '
        'Hipp, JA, Shariati H, F, MJ. K'
    )
    citation = _cite(authors)
    assert ', F,' in citation
    assert citation == (
        '1. Kaczynski AT, Wende ME, Schipperijn J, Hughey SM, Stowe EW, '
        'Hipp JA, Shariati H, F, MJ. K. A Study.'
    )


def test_fallback_token_count_is_never_reduced():
    """The property the issue asks for, on the fallback path specifically:
    "Carlos" in the initials slot makes the pair detector decline the whole
    string, so this runs through the fallback, where "MB" follows an author
    that already carries its own initials ("Smith JA") and therefore had
    nothing open to merge into. It used to vanish."""
    assert _cite('Smith JA, MB, Ramirez, Carlos') == (
        '1. Smith JA, MB, Ramirez, Carlos. A Study.'
    )


def _debug_records(authors: str) -> list:
    """Every DEBUG message the normalization module emits for `authors`."""
    import logging
    module_logger = logging.getLogger(
        'unified_pipeline.stage6.normalization.authors'
    )
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture()
    previous_level = module_logger.level
    module_logger.addHandler(handler)
    module_logger.setLevel(logging.DEBUG)
    try:
        _cite(authors)
    finally:
        module_logger.removeHandler(handler)
        module_logger.setLevel(previous_level)
    return records


def test_fallback_logs_the_token_count_before_and_after():
    """#560 asks for a diagnostic recording the token count "before and
    after" -- the after count is the number that would have made the
    dropped token visible in a log. Round 1 logged the before count only.
    The counts survived the round-2 change that stopped logging the author
    strings themselves, which is the whole point of keeping this test."""
    records = _debug_records('Li, Wu, Ma, Ye')
    assert any('tokens_in=4 tokens_out=4' in m for m in records), records


def test_no_author_text_reaches_the_debug_log():
    """CV author names are personal data and debug logs are retained more
    widely than the application's own storage. Both fallback statements
    used to carry %r of the raw input and of the result; the diagnostic is
    now structural only. Uses distinctive surnames so a leak cannot hide
    behind a common word."""
    authors = 'Quillfeather, Zbygniewski, Xolotlpec, Vandermolenaar'
    records = _debug_records(authors)
    assert records, 'the diagnostic disappeared entirely'
    joined = ' '.join(records)
    for surname in authors.split(', '):
        assert surname not in joined, f'{surname!r} leaked into {joined!r}'
    assert 'branch=fallback' in joined, joined


def test_surname_in_a_shifted_initials_slot_is_not_upper_cased():
    """The pairs branch's old surname-slot skip advanced by one, which
    shifted the loop off the parity the detector validated -- a real
    surname could then land in the initials slot and was rendered in
    capitals ("Smith" -> "SMITH"). Upper-casing is applied only to a token
    that is itself initials-shaped.

    Round 2: the same input also pins that the shift no longer costs a
    token. The skip used to advance past the leading "AB" entirely, so one
    of the two "AB"s in 'AB, A-B, Smith, AB' never reached the citation --
    round 1 asserted that loss as an accepted side effect. The orphan
    branch now places the token instead of stepping over it."""
    citation = _cite('AB, A-B, Smith, AB')
    assert 'SMITH' not in citation
    assert citation == '1. AB A-B, Smith AB. A Study.'


# --------------------------------------------------------------------------
# Round-2 review response (#735 review, items 2 and 6). One initials rule for
# both parsing paths, and the two parsers reachable on their own.
# --------------------------------------------------------------------------

def test_a_spaced_initials_group_is_initials_on_the_fallback_path_too():
    """The duplication the review found: `_looks_like_initials` strips the
    space out of "N J" and calls it initials, while the fallback's own
    `^[A-Z]{1,3}\\.?$` test saw a 3-character string containing a space and
    called it a standalone author. "Wei" in the last initials slot makes the
    pair detector decline, so this string parses on the fallback path, where
    "N J" now merges into the open surname before it instead of being
    emitted as an author of its own."""
    assert _cite('Alpha, I, Bravo, N J, Charlie, Wei') == (
        '1. Alpha I, Bravo N J, Charlie, Wei. A Study.'
    )


def test_a_four_letter_initials_group_is_initials_on_the_fallback_path_too():
    """Same divergence, other shape: the fallback's regex stopped at three
    uppercase letters and its length test at two characters, so a 4-letter
    group -- which `_looks_like_initials` accepts -- was a standalone
    author on one path and initials on the other."""
    assert _cite('Alpha, ABCD, Bravo, Wei') == (
        '1. Alpha ABCD, Bravo, Wei. A Study.'
    )


def test_an_authorship_marker_does_not_stop_a_token_being_initials():
    """Strict unification alone would have LOST a case the old fallback got
    right: its case-blind length test happened to accept "L*" (a co-first
    author marker) because `str.isupper()` ignores the asterisk, while
    `_looks_like_initials` rejected it on `str.isalpha()`. The marks are
    stripped in the one predicate instead, so both paths accept them --
    4 of the farm's 1,711 distinct author strings are this shape, the same
    number `_INITIALS_TRAILING_MARKS` cites, regenerated with
    `scripts/measure_normalization_claims.py --only marks`."""
    assert _cite('Alpha, PL*, Bravo, JW*') == '1. Alpha PL*, Bravo JW*. A Study.'


def test_pair_parser_is_reachable_and_never_drops_a_token():
    """`_parse_surname_initial_pairs` extracted from `_normalize_author_names`
    (#735 review item 6): testable without driving the whole citation."""
    assert _parse_surname_initial_pairs(['Kelly', 'R', 'Pirog', 'R']) == [
        'Kelly R', 'Pirog R',
    ]
    # trailing element with nothing to pair with is emitted, not dropped
    assert _parse_surname_initial_pairs(['Kelly', 'R', 'Pirog']) == [
        'Kelly R', 'Pirog',
    ]
    # a suffix in the initials slot belongs to the surname before it
    assert _parse_surname_initial_pairs(['Smith', 'John', 'Jr.', 'Brown']) == [
        'Smith John', 'Jr Brown',
    ]


def test_fallback_parser_is_reachable_and_reports_et_al_separately():
    """`_parse_author_fallback` extracted from `_normalize_author_names`
    (#735 review item 6). "et al" is returned as a flag rather than as an
    author element, which is what lets the caller re-attach it."""
    assert _parse_author_fallback(['Alpha', 'I', 'Bravo', 'Wei']) == (
        ['Alpha I', 'Bravo', 'Wei'], False,
    )
    assert _parse_author_fallback(['Alpha', 'et al']) == (['Alpha'], True)
    # an initials fragment with no open predecessor is kept, not dropped
    assert _parse_author_fallback(['Alpha B', 'C']) == (['Alpha B', 'C'], False)


# --------------------------------------------------------------------------
# The pair parser's own token deletion, on a shape the fallback never sees:
# an initials group standing where a surname should be was skipped over
# rather than placed. Same defect as #560, other parser; one farm string.
# --------------------------------------------------------------------------

def test_an_initials_group_in_a_surname_slot_is_not_deleted():
    """"AB, PL*, Smith, JA" rendered as "PL* Smith, JA": the leading "AB"
    was skipped over and never emitted. Widening the initials rule to
    accept the co-first-author asterisk is what routed this string to the
    pair parser in the first place, so the skip has to stop deleting."""
    citation = _cite('AB, PL*, Smith, JA')
    assert 'AB' in citation
    assert citation == '1. AB PL*, Smith JA. A Study.'


def test_the_pair_parser_keeps_every_alphabetic_character_of_its_input():
    """The property, not one example: for the shapes that reach the pair
    parser at all, every letter and digit of the input survives into the
    output, counted as a multiset -- so a repeated token cannot go missing
    behind an identical one. The old skip broke this on any list missing a
    surname."""
    for authors in (
        'AB, PL*, Smith, JA',
        'AB, PL, Smith, JA',
        'AB, A-B, Smith, AB',
        'Kelly, R, Pirog, R',
        'Smith, John, Jr., Brown',
    ):
        parts = [p.strip() for p in authors.split(',') if p.strip()]
        joined = ''.join(_parse_surname_initial_pairs(parts))
        expected = Counter(c for token in parts for c in token if c.isalnum())
        actual = Counter(c for c in joined if c.isalnum())
        assert actual == expected, (
            f'{authors!r} lost {dict(expected - actual)} '
            f'and gained {dict(actual - expected)}'
        )


def test_the_two_parsers_place_a_second_consecutive_orphan_differently():
    """The orphan placement is NOT identical across the two parsers, which
    the pair parser's docstring claimed it was (#735 review). Both merge an
    orphan into an open element and both emit it alone when nothing is
    open; the pair parser then leaves that emitted orphan open, so a second
    consecutive orphan coalesces into it, while the fallback closes it, so
    a second fragment stands alone. Pinned on both sides so the difference
    stays a decision rather than becoming a drift."""
    assert _parse_surname_initial_pairs(
        ['Alpha', 'B', 'C', 'D', 'Echo', 'F']
    ) == ['Alpha B', 'C D', 'Echo F']
    assert _parse_author_fallback(
        ['Alpha B', 'C', 'D', 'Echo F']
    ) == (['Alpha B', 'C', 'D', 'Echo F'], False)


def test_consecutive_orphan_initials_coalesce_into_one_author():
    """The farm's one live occurrence of the skip places two tokens back to
    back, each a single uppercase letter, both in surname slots because the
    list around them is a clean pair sequence (measured with
    `scripts/measure_normalization_claims.py --only orphans`: shapes
    {'A': 2}, runs {2: 1}). Skipping deleted both. This pins what placing
    them does instead -- consecutive orphans coalesce, so the two letters
    render as one element rather than two. Shape reproduced synthetically
    -- "A, E, L, B" between two ordinary pairs."""
    assert _cite('Alpha, A, E, L, Bravo, B') == (
        '1. Alpha A, E L, Bravo B. A Study.'
    )
    assert _parse_surname_initial_pairs(
        ['Alpha', 'A', 'E', 'L', 'Bravo', 'B']
    ) == ['Alpha A', 'E L', 'Bravo B']
