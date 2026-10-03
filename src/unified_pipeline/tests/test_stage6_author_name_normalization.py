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
from a citation, not just the one malformed entry: #560 measures 595 of
7,137 real author strings losing text at this step. Every author string
below is invented -- corpus shapes, reproduced synthetically.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_author_name_normalization.py -p no:cacheprovider
"""

import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.formatting.values import _format_citation  # noqa: E402
from unified_pipeline.stage6.normalization.authors import (  # noqa: E402
    _looks_like_initials,
    _one_edit_apart,
    _parse_author_fallback,
    _parse_surname_initial_pairs,
    _source_authors_after,
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


def test_the_docstring_example_produces_exactly_what_it_documents() -> None:
    """`_normalize_author_names`'s docstring documents 'Smith, John A.,
    Jones, Mary B.' -> 'Smith, John A, Jones, Mary B' (full given names
    aren't initials, so the pair detector correctly declines this shape
    and the fallback parser carries both names through untouched). This
    pins that the documented input produces exactly the documented
    output -- the docstring and the behaviour agree."""
    assert _cite('Smith, John A., Jones, Mary B.') == (
        '1. Smith, John A, Jones, Mary B. A Study.'
    )


# --------------------------------------------------------------------------
# The farm defect's shape, in invented names
# --------------------------------------------------------------------------

def test_a_leading_complete_unit_does_not_cost_the_rest_their_initials():
    """The live corpus defect in invented names: 'Alpha SM' is already a
    complete "Surname Initials" unit, which throws off the pair detector's
    parity for the rest of a perfectly regular surname/initials list."""
    authors = 'Alpha SM, Bravo, L, Charlie, WV, Delta, S, Echo, S'
    assert _cite(authors) == (
        '1. Alpha SM, Bravo L, Charlie WV, Delta S, Echo S. A Study.'
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
# was not actually met -- and it was live on one of the farm's own entries.
# The same fragment shape was already emitted standalone by the pairs
# branch's trailing-element case, so the two branches also disagreed.
# --------------------------------------------------------------------------

def test_orphan_initials_fragment_with_no_open_predecessor_is_kept():
    """The live farm string's shape, in invented names. "Alpha H" already
    carries its own initials, so it is not an open merge target; the "F"
    that follows it therefore had nothing to attach to and was dropped --
    a deleted token in a delivered document, which is the whole of #560.
    It is now emitted as its own element instead."""
    authors = (
        'Charlie AT, Delta ME, Echo J, Foxtrot SM, Golf, EW, '
        'Hotel, JA, Alpha H, F, MJ. K'
    )
    citation = _cite(authors)
    assert ', F,' in citation
    assert citation == (
        '1. Charlie AT, Delta ME, Echo J, Foxtrot SM, Golf EW, '
        'Hotel JA, Alpha H, F, MJ. K. A Study.'
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
    right: its length test happened to accept "L*" (a co-first author
    marker) because `str.isupper()` ignores the asterisk, while
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


# --------------------------------------------------------------------------
# #735 review item 9: two more assertions past the one pinned example,
# generalising "nothing is deleted" into an actual multiset check on both
# parsers rather than leaving it as prose.
# --------------------------------------------------------------------------

def test_declining_the_pair_shape_for_full_given_names_never_drops_a_character() -> None:
    """Generalised past the one pinned example above. The pair
    detector declines whenever an odd-indexed token is not initials-shaped
    -- a full given name being the case the docstring documents -- and the
    fallback parser then has to carry every token through untouched rather
    than attempt an abbreviation it was never asked to make."""
    for authors in (
        'Smith, John A., Jones, Mary B.',
        'Alpha, Jamie Lee, Bravo, Chris Ann',
        'Charlie, Robin, Delta, Morgan Kay',
    ):
        parts = [p.strip() for p in authors.rstrip('.,;').split(',') if p.strip()]
        cleaned, has_et_al = _parse_author_fallback(parts)
        assert not has_et_al
        expected = Counter(c for token in parts for c in token if c.isalnum())
        actual = Counter(c for token in cleaned for c in token if c.isalnum())
        assert actual == expected, (
            f'{authors!r} lost {dict(expected - actual)} '
            f'and gained {dict(actual - expected)}'
        )


def test_the_two_parsers_second_orphan_divergence_still_drops_no_character() -> None:
    """The two parsers place a second consecutive orphan
    differently (pinned above), and that placement choice is a documented
    decision, not a defect -- but the decision is only acceptable if #560's
    bar still holds on both sides of it: no character of the input is lost
    either way, whichever parser a given input happens to reach."""
    parts = ['Alpha', 'B', 'C', 'D', 'Echo', 'F']
    expected = Counter(c for token in parts for c in token if c.isalnum())

    from_pairs = _parse_surname_initial_pairs(parts)
    actual_pairs = Counter(c for token in from_pairs for c in token if c.isalnum())
    assert actual_pairs == expected, 'pair parser dropped a character'

    fallback_parts = ['Alpha B', 'C', 'D', 'Echo F']
    fallback_expected = Counter(
        c for token in fallback_parts for c in token if c.isalnum())
    from_fallback, has_et_al = _parse_author_fallback(fallback_parts)
    actual_fallback = Counter(
        c for token in from_fallback for c in token if c.isalnum())
    assert not has_et_al
    assert actual_fallback == fallback_expected, 'fallback parser dropped a character'


# --------------------------------------------------------------------------
# #735 review item 10: `_looks_like_initials` Unicode behaviour.
# --------------------------------------------------------------------------

def test_a_single_precomposed_accented_letter_is_an_initial() -> None:
    assert _looks_like_initials('É')


def test_an_nfd_decomposed_letter_is_not_recognised_as_an_initial() -> None:
    """The one genuinely surprising Unicode result: an NFD-decomposed 'É' is
    two code points -- the base letter 'E' and a COMBINING ACUTE ACCENT
    (U+0301). The combining mark is not in `_INITIALS_TRAILING_MARKS`, so it
    survives the rstrip, and `str.isalpha()` is False for a standalone
    combining mark (Unicode category Mn, not a letter category) -- so
    `t.isalpha()` fails on the two-character token as a whole. Precomposed
    and NFD forms of the same visible character are NOT treated alike."""
    nfd = unicodedata.normalize('NFD', 'É')
    assert len(nfd) == 2, 'fixture assumption: NFD form is base + combining mark'
    assert not _looks_like_initials(nfd)


def test_a_two_letter_non_ascii_group_is_recognised() -> None:
    assert _looks_like_initials('ÉÀ')


def test_greek_and_cyrillic_capital_groups_are_recognised() -> None:
    assert _looks_like_initials('ΑΒ')   # Greek capital Alpha, Beta
    assert _looks_like_initials('АБ')   # Cyrillic capital A, Be


def test_a_trailing_mark_after_a_non_ascii_letter_is_stripped_first() -> None:
    assert _looks_like_initials('É.')


def test_fullwidth_latin_letters_are_recognised() -> None:
    assert _looks_like_initials('ＡＢ')  # fullwidth 'AB'


def test_a_lowercase_non_ascii_word_is_not_initials() -> None:
    assert not _looks_like_initials('éa')


def test_non_ascii_digits_are_not_initials() -> None:
    assert not _looks_like_initials('１')  # fullwidth digit '1'


def test_a_mark_only_token_is_not_initials() -> None:
    """Stripping every trailing mark can leave nothing at all -- the `if not
    t: return False` guard, not an accidental match on an empty pattern."""
    assert not _looks_like_initials('.')
    assert not _looks_like_initials('*')


def test_the_precomposed_single_initial_is_recognised_on_both_parsing_paths() -> None:
    """The one Unicode shape threaded through both real parsing paths, per
    the review's ask. On the pairs path (a clean alternating list) the
    precomposed 'É' pairs normally, upper-cased like any initial. On the
    fallback path (forced by its NFD sibling in the third slot, which is
    NOT initials-shaped) the precomposed 'É' is still recognised as
    initials and merges into the open 'Kelly' -- it is the NFD form, not
    the character itself, that fails to be recognised."""
    precomposed = 'É'
    assert _cite(f'Kelly, {precomposed}, Pirog, R') == (
        '1. Kelly É, Pirog R. A Study.'
    )
    nfd = unicodedata.normalize('NFD', precomposed)
    assert not _looks_like_initials(nfd), 'fixture assumption: NFD sibling forces fallback'
    assert _cite(f'Kelly, {precomposed}, {nfd}, Pirog, R') == (
        f'1. Kelly É, {nfd}, Pirog R. A Study.'
    )


# --------------------------------------------------------------------------
# #735 review item 11: author suffix handling with punctuation and case,
# through `_AUTHOR_SUFFIX_RE` via `_normalize_author_names`, on both parsing
# paths.
# --------------------------------------------------------------------------

@pytest.mark.parametrize('suffix,attached', [
    ('Jr', 'Jr'), ('Jr.', 'Jr'), ('JR', 'JR'), ('jr.', 'jr'),
    ('Sr', 'Sr'), ('III', 'III'), ('iii', 'iii'), ('IV', 'IV'), ('IV.', 'IV'),
])
def test_a_suffix_variant_attaches_to_the_surname_on_the_pairs_path(suffix: str, attached: str) -> None:
    """A recognised suffix in the initials slot does not itself have to look
    like initials -- it belongs to the surname before it (#560) -- and its
    case is carried through verbatim, never upper-cased the way a real
    initials group is."""
    assert _cite(f'Smith, {suffix}, Jones, JA') == (
        f'1. Smith {attached}, Jones JA. A Study.'
    )


@pytest.mark.parametrize('suffix,attached', [
    ('Jr', 'Jr'), ('Jr.', 'Jr'), ('JR', 'JR'), ('jr.', 'jr'),
    ('Sr', 'Sr'), ('III', 'III'), ('iii', 'iii'), ('IV', 'IV'), ('IV.', 'IV'),
])
def test_a_suffix_variant_attaches_to_the_surname_on_the_fallback_path(suffix: str, attached: str) -> None:
    """Same suffix set, forced onto the fallback path: a full given name
    ('John') in the surname slot is not initials-shaped, so the pair
    detector declines before it ever reaches the suffix check."""
    assert _cite(f'Smith, John, {suffix}, Brown') == (
        f'1. Smith, John {attached}, Brown. A Study.'
    )


def test_a_suffix_survives_a_stray_double_comma_right_after_it() -> None:
    """Double/triple commas are collapsed to one before the comma-split, so
    a suffix immediately followed by an extra comma is not a distinct
    fragment of its own."""
    assert _cite('Smith, Sr,, Jones, JA') == '1. Smith Sr, Jones JA. A Study.'


def test_a_suffix_survives_extra_internal_whitespace_around_it() -> None:
    assert _cite('Smith, Sr  , Jones, JA') == '1. Smith Sr, Jones JA. A Study.'


def test_junior_spelled_out_is_not_a_recognised_suffix() -> None:
    """'Junior' is 6 letters and mixed case: it fails both the suffix regex
    (only the abbreviated forms are listed) and `_looks_like_initials`
    (too long, not all-uppercase), so it is neither attached as a suffix
    nor folded in as initials -- it becomes its own ordinary element, and
    the pair detector declines the whole string over it."""
    assert _cite('Smith, Junior, Jones, JA') == (
        '1. Smith, Junior, Jones JA. A Study.'
    )


def test_a_near_miss_suffix_spelling_is_not_recognised() -> None:
    """'Jrs' fails the suffix regex (not an exact listed form) and fails
    `_looks_like_initials` (mixed case) -- same fate as 'Junior'."""
    assert _cite('Smith, Jrs, Jones, JA') == '1. Smith, Jrs, Jones JA. A Study.'


def test_a_bare_roman_numeral_v_is_not_a_recognised_suffix() -> None:
    """'V' is not in the enumerated suffix set, but a single letter of any
    case is ALWAYS initials-shaped (`_looks_like_initials`'s own rule), so
    it is folded in as an initials group rather than rejected outright --
    not treated as a suffix, but not lost either."""
    assert _cite('Smith, V, Jones, JA') == '1. Smith V, Jones JA. A Study.'


def test_four_is_are_not_a_recognised_suffix_but_pass_as_initials_shaped() -> None:
    """'IIII' is not a valid roman numeral and not in the suffix set, but it
    IS 4 uppercase letters -- exactly `_MAX_INITIALS_LETTERS` -- so
    `_looks_like_initials` accepts it and it is folded in as initials, the
    same misclassification 'V' gets above."""
    assert _cite('Smith, IIII, Jones, JA') == '1. Smith IIII, Jones JA. A Study.'


# ---------------------------------------------------------------------------
# #1259: a CV's own author run, read back after the authors stage 5d kept.
# Every name below is invented; the shapes are the corpus's.
# ---------------------------------------------------------------------------

_KEPT = ['Ash A', 'Birch B', 'Cedar C', 'Daly D', 'Elm E', 'Fir F']
_LEAD = 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F'


@pytest.mark.parametrize('case, source, kept, expected', [
    ('Vancouver, semicolons and co-first marks',
     '1. Ash A*; Birch B*; Cedar C; Daly D; Elm E; Fir F; Gorse S\u2217; Holly CC†; Wren TM. A title. J Wood 2011',
     _KEPT, (['Gorse S', 'Holly CC', 'Wren TM'], False)),
    ('"Surname, Initials" pairs, "and", a colon closing the run',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, H.-P., Gorse, D., and Wren, M.H.: A title. J Wood.',
     _KEPT[:5] + ['Fir HP'], (['Gorse D', 'Wren MH'], False)),
    ('initials first, a quoted title',
     'A. Ash, B. Birch, C. Cedar, D. Daly, E. Elm, F. Fir, J.R. Vetch Jr., S. Wren, “A title,” J Wood.',
     _KEPT, (['Vetch JR Jr', 'Wren S'], False)),
    ('given name first, a credential',
     'Ann Ash MA, Bo Birch BA, Cy Cedar MD, Di Daly MD, Ed Elm MD, Kim Fir MEd, Juno Wren MD. A title. Venue.',
     ['Ash A', 'Birch B', 'Cedar C', 'Daly D', 'Elm E', 'Fir K'], (['Wren J'], False)),
    ('given name first, an affiliation after the run is not an author',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, Opal V. Yarrow, '
     'Juno R. Wren, Lantern University School of Medicine, Springfield, ST. A title. Venue.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], (['Yarrow OV', 'Wren JR'], False)),
    ('a suffix as its own item, an ordinal suffix, a bare "and"',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse MH, Jr, Rowan ID 3rd, Wren I and Holly SI. A title.',
     _KEPT, (['Gorse MH Jr', 'Rowan ID 3rd', 'Wren I', 'Holly SI'], False)),
    ('the anchor\'s own suffix after a comma is skipped',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Jr ., Wren R, “A title.”',
     _KEPT[:5] + ['Fir F Jr'], (['Wren R'], False)),
    ('an abbreviated initial, an initial whose period also closes the run',
     _LEAD + ', Quill Th., Wren A., and Holly T.. A title.',
     _KEPT, (['Quill TH', 'Wren A', 'Holly T'], False)),
    ('5d corrected the anchor\'s spelling',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Rowen MR, Wren D, Holly L. A title.',
     _KEPT[:5] + ['Rowan MR'], (['Wren D', 'Holly L'], False)),
    ('5d restored the anchor\'s hyphens',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, QuillRowanSorrel P, Wren T.\nVenue 2022',
     _KEPT[:5] + ['Quill-Rowan-Sorrel P'], (['Wren T'], False)),
    ('a surname the kept list repeats anchors on its own occurrence',
     'Ash A, Birch B, Cedar C, Daly D, Elm EC, Elm M, Wren B. A title.',
     ['Ash A', 'Birch B', 'Cedar C', 'Daly D', 'Elm EC', 'Elm M'], (['Wren B'], False)),
    ('a group author inside the run is kept as printed',
     _LEAD + ', The Lantern Study Group, Gorse C, Wren AW. A title.',
     _KEPT, (['The Lantern Study Group', 'Gorse C', 'Wren AW'], False)),
    ('a group credit that closes the run is not an author',
     _LEAD + ', Wren RD; for the Lantern Research Network. A title.',
     _KEPT, (['Wren RD'], False)),
    ('the line says "et al."',
     _LEAD + ', Wren W, et al. A title.',
     _KEPT, (['Wren W'], True)),
    ('a bare-surname anchor takes its given name from the next piece',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, Opal J., & Wren, M. (2010). A title.',
     _KEPT, (['Wren M'], False)),
    ('a two-word surname does not make the run given-name-first',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Quill Rowan M, Wren C, Gorse ER. Computer-aided, a trial.',
     _KEPT[:5] + ['Quill Rowan M'], (['Wren C', 'Gorse ER'], False)),
    ('a line break after the title, the run on the next line',
     'A title.\nAsh A, Birch B, Cedar C, Daly D, Elm E, Fir F, Wren TM, Holly J.\nJ Wood. 2022',
     _KEPT, (['Wren TM', 'Holly J'], False)),
    ('the run ended at the anchor',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F. Results in a trial, Wren W, Holly H.',
     _KEPT, ([], False)),
    ('the run ended at the anchor, at a bracket',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F (2010) Title Case Words, Wren W.',
     _KEPT, ([], False)),
    ('the run ended in the piece after a bare-surname anchor',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, F. Results in a trial, Wren, W.',
     _KEPT, ([], False)),
    ('the run ended inside a piece: the pieces after it are not read',
     _LEAD + ', Wren W. A title, Gorse G, Holly H.',
     _KEPT, (['Wren W'], False)),
    ('a line break with no period ends the run',
     _LEAD + ', Wren TM\nJ Wood 2022', _KEPT, (['Wren TM'], False)),
    ('a quote with no period ends the run',
     _LEAD + ', Wren S “A title,” J Wood.', _KEPT, (['Wren S'], False)),
    ('given name first, the last author closes the run with a period',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, Opal V. Yarrow. A title.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], (['Yarrow OV'], False)),
    ('given name first, a name with no middle initial or credential reads either way: '
     'the run goes on past it, so "et al."',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, Juno Wren, Opal V. Yarrow.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], ([], True)),
    ('surname particles and a camel-cased surname',
     _LEAD + ', van den Gorse PA, deVetch R, Wren W. A title.',
     _KEPT, (['van den Gorse PA', 'deVetch R', 'Wren W'], False)),
    ('a stray period before an initial',
     _LEAD + ', Gorse, G., and Wren, .S. A title.', _KEPT, (['Gorse G', 'Wren S'], False)),
    ('a suffix standing first is not an author, but the run goes on past it',
     _LEAD + ', Jr AB, Wren W.', _KEPT, ([], True)),
    ('a group name running into a quoted title is not an author',
     _LEAD + ', The Lantern Group “A Title,” Wren W.', _KEPT, ([], False)),
    ('the anchor\'s piece runs on past what one author can hold',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F Title Case Words Here Now, Wren W.',
     _KEPT, ([], False)),
    ('the piece after a bare-surname anchor runs on past its given name',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, F. Title Case Words, Wren, W.',
     _KEPT, ([], False)),
    ('given name first, an item that does not start with a name',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, the Yarrow MD.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], ([], False)),
    # A surname that is also a particle word (QITQWH 269 and 283 stopped at
    # one, two and five co-authors short, with no "et al.").
    ('a capitalised particle with initials after it is a surname',
     _LEAD + ', Wren W, Le T, Du N, Van H, Holly H. A title.',
     _KEPT, (['Wren W', 'Le T', 'Du N', 'Van H', 'Holly H'], False)),
    ('a particle surname as a bare surname and initials first',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, F., Du, D., Wren, W., and Di, T. A title.',
     _KEPT, (['Du D', 'Wren W', 'Di T'], False)),
    ('a particle surname after its initials',
     'A. Ash, B. Birch, C. Cedar, D. Daly, E. Elm, F. Fir, T. De, J. Wren. A title.',
     _KEPT, (['De T', 'Wren J'], False)),
    ('two capitalised particles with initials after them are the surname as printed',
     _LEAD + ', Wren W, Van Der T, Holly H. A title.', _KEPT, (['Wren W', 'Van Der T', 'Holly H'], False)),
    ('a particle after a bare-surname anchor\'s initials ("Surname, I. van") is the surname\'s',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, F. van, Wren, W. A title.',
     _KEPT, (['Wren W'], False)),
    ('"et al." with a no-break space',
     _LEAD + ', Wren W, et\u00a0al. A title.', _KEPT, (['Wren W'], True)),
    ('a lower-case particle with no name after it is not a surname; the run goes on past it',
     _LEAD + ', Wren W, de T, Holly H. A title.', _KEPT, (['Wren W'], True)),
    ('"Surname SR" and "Surname JR" are initials, not a suffix',
     _LEAD + ', Wren SR, Gorse JR, Holly JR Jr, Rowan IV. A title.',
     _KEPT, (['Wren SR', 'Gorse JR', 'Holly JR Jr', 'Rowan IV'], False)),
    ('a hyphenated initial after the anchor loses its hyphen',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, F., Gorse, H.-P., and Wren, M.H.: A title.',
     _KEPT, (['Gorse HP', 'Wren MH'], False)),
    ('given name first, a suffix',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, Opal V. Yarrow Jr, Juno R. Wren. A title.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], (['Yarrow OV Jr', 'Wren JR'], False)),
    ('initials first, a two-word name is not read; the run goes on past it',
     'A. Ash, B. Birch, C. Cedar, D. Daly, E. Elm, F. Fir, J. Wren, S. Gorse Holly, H. Rowan. A title.',
     _KEPT, (['Wren J'], True)),
    ('the run ended in a short piece after a bare-surname anchor',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, F. (2010), Wren, W.',
     _KEPT, ([], False)),
    ('a surname-first run does not read a given-name-first item; the run goes on past it',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, F., Juno Wren MD, Holly, H. A title.',
     _KEPT, ([], True)),
    ('the anchor is the last word of a two-word surname, not the first',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Quill Rowan M, Wren C, Gorse ER. Quill pens in a trial.',
     _KEPT[:5] + ['Quill Rowan M'], (['Wren C', 'Gorse ER'], False)),
    ('a particle before a bare-surname anchor does not make the run given-name-first',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., van Fir, Opal F., Wren, W. A title.',
     _KEPT[:5] + ['van Fir F'], (['Wren W'], False)),
    ('a lone credential after the last author is not a person',
     _LEAD + ', Wren W, MD. A title.', _KEPT, (['Wren W'], False)),
    ('a title in capitals with no initials is not a person',
     _LEAD + ', Wren W, The Lantern Effect, J Wood 2020.', _KEPT, (['Wren W'], False)),
    ('given name first, a group credit closing the run is not a person',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, Opal V. Yarrow; '
     'Lantern Study Group. A title.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], (['Yarrow OV'], False)),
    ('given name first, an affiliation longer than one author is not a person',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, Opal V. Yarrow, '
     'Lantern Valley Regional Medical Research Center Springfield. A title.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], (['Yarrow OV'], False)),
    ('given name first, a person before a bracket: the run goes on past them',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Fay Fir, Opal V. Yarrow, '
     'Juno Wren (Lantern University). A title.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir F'], (['Yarrow OV'], True)),
    # Round 3 (#1259 review): the anchor is the kept author, by initials.
    ('5d corrected a swap the next co-author\'s surname shares: the anchor is the one whose initials agree',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Rowen F, Rowan C, Wren W. A title.',
     _KEPT[:5] + ['Rowan F'], (['Rowan C', 'Wren W'], False)),
    ('a kept pair that repeats anchors on the same occurrence in the line, not the first',
     '4. Elm M, Ash A, Elm M, Gorse G, Holly H. A title.',
     ['Elm M', 'Ash A', 'Elm M'], (['Gorse G', 'Holly H'], False)),
    ('only the first initial is checked: 5d often drops a middle initial',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Rowan F, Gorse G, Holly H. A title.',
     _KEPT[:5] + ['Rowan FG'], (['Gorse G', 'Holly H'], False)),
    ('an exact item that agrees wins over a one-edit item that also agrees',
     '4. Ash A, Rowen F, Gorse G, Rowan F, Holly H, Wren W. A title.',
     ['Ash A', 'Rowan F'], (['Holly H', 'Wren W'], False)),
    ('the initials check reads a bare-surname item\'s initials from the next piece',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Rowen, F., Rowan, C., Wren, W. A title.',
     _KEPT[:5] + ['Rowan F'], (['Rowan C', 'Wren W'], False)),
    ('the initials check reads past a suffix on both sides',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Rowen F Jr, Rowan C, Wren W. A title.',
     _KEPT[:5] + ['Rowan F Jr'], (['Rowan C', 'Wren W'], False)),
    ('a kept author with particles and no initials agrees with any item',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, van Fir G, Wren W. A title.',
     _KEPT[:5] + ['van Fir'], (['Wren W'], False)),
    ('given name first, the initials check reads the given name and middle initial',
     'Ann A. Ash, Bo B. Birch, Cy C. Cedar, Di D. Daly, Ed E. Elm, Opal V. Fir, Kim L. Fir, Juno R. Wren. A title.',
     ['Ash AA', 'Birch BB', 'Cedar CC', 'Daly DD', 'Elm EE', 'Fir KL'], (['Wren JR'], False)),
    ('a six-word prefix of a piece that closes the run is one author',
     _LEAD + ', Wren W, van den Holly A B C. Title', _KEPT, (['Wren W', 'van den Holly ABC'], False)),
    ('a kept author with no initials agrees with any item',
     _LEAD + ', Wren W. A title.', _KEPT[:5] + ['Fir'], (['Wren W'], False)),
    ('5d dropped a letter from the anchor: one edit, the CV\'s spelling longer',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Quillian F, Wren W. A title.',
     _KEPT[:5] + ['Quillan F'], (['Wren W'], False)),
    ('5d added a letter to the anchor: one edit, the CV\'s spelling shorter',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Quilan F, Wren W. A title.',
     _KEPT[:5] + ['Quillan F'], (['Wren W'], False)),
    ('a title word that repeats the anchor\'s surname is not the anchor',
     _LEAD + ', Wren W. Fir trees in a trial.', _KEPT, (['Wren W'], False)),
    ('the anchor in capitals is still the anchor',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, FIR F, Wren W. A title.', _KEPT, (['Wren W'], False)),
    ('a line break right after the anchor ends the run, a Title Case title after it',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F\nTitle Case Words, Wren W.', _KEPT, ([], False)),
    ('a hyphenated co-author after the anchor',
     _LEAD + ', Gorse-Holly G, Wren W. A title.', _KEPT, (['Gorse-Holly G', 'Wren W'], False)),
    ('ASCII co-first marks after the anchor',
     _LEAD + ', Gorse S*, Wren W*. A title.', _KEPT, (['Gorse S', 'Wren W'], False)),
    ('a group word before a colon closes the run',
     _LEAD + ', Wren W, Lantern Study Group: a title', _KEPT, (['Wren W'], False)),
    ('a group word at the end of the line closes the run',
     _LEAD + ', Wren W, Lantern Study Group.', _KEPT, (['Wren W'], False)),
    ('a network inside the run is a group author',
     _LEAD + ', The Lantern Network, Wren W. A title.', _KEPT, (['The Lantern Network', 'Wren W'], False)),
    ('six words an author can hold still name a person: "et al."',
     _LEAD + ', Wren W, Opal B C van den Holly, Gorse G. A title.', _KEPT, (['Wren W'], True)),
    ('three words after a bare-surname anchor are its given name and initials',
     'Ash, A., Birch, B., Cedar, C., Daly, D., Elm, E., Fir, Opal J. K., Wren, W. A title.',
     _KEPT, (['Wren W'], False)),
    ('an empty piece in the run is skipped',
     _LEAD + ', Wren W, , Holly H. A title.', _KEPT, (['Wren W', 'Holly H'], False)),
    ('a suffix item with its own period',
     _LEAD + ', Wren W, II., Holly H. A title.', _KEPT, (['Wren W II', 'Holly H'], False)),
    ('initials repeated after the anchor are the anchor\'s',
     _LEAD + ', G., Wren W. A title.', _KEPT, (['Wren W'], False)),
    ('a word with a digit is not a name',
     _LEAD + ', Wren W, Holly2 H. A title.', _KEPT, (['Wren W'], False)),
    ('lower-case particles open a surname',
     _LEAD + ', le Gorse T, du Holly N, Wren W. A title.', _KEPT,
     (['le Gorse T', 'du Holly N', 'Wren W'], False)),
    ('given name first, an MA credential',
     'Ann Ash MA, Bo Birch BA, Cy Cedar MD, Di Daly MD, Ed Elm MD, Kim Fir MEd, Juno Wren MA. A title.',
     ['Ash A', 'Birch B', 'Cedar C', 'Daly D', 'Elm E', 'Fir K'], (['Wren J'], False)),
])
def test_source_authors_after_reads_the_run_after_the_kept_authors(
    case: str, source: str, kept: list[str], expected: tuple[list[str], bool],
) -> None:
    assert _source_authors_after(source, kept) == expected, case


@pytest.mark.parametrize('case, source', [
    ('the anchor is not in the line', 'Gorse G, Holly H, Wren W. A title.'),
    ('initials with no surname to finish: the pairs are misaligned',
     _LEAD + ', Gorse B, S.J., Wren C. A title.'),
    ('a bare word is followed by a whole author, so nothing completes it',
     _LEAD + ', Lantern, D. Wren, Holly H. A title.'),
    ('a bare surname followed by "et al."', _LEAD + ', Wren, et al. A title.'),
    ('a bare surname followed by a piece that is not its initials',
     _LEAD + ', Wren, The Lantern Effect in a trial. A title'),
])
def test_source_authors_after_declines(case: str, source: str) -> None:
    assert _source_authors_after(source, _KEPT) is None, case


@pytest.mark.parametrize('case, source, kept', [
    # ZCTARO 810 on the EBYSBC farm: 5d corrected a swap the next co-author's
    # surname shares. Anchored on that co-author, the read printed a complete-
    # looking list one co-author short.
    ('the only item with the anchor\'s surname has other initials',
     '4. Ash A, Birch B, Cedar C, Daly D, Elm E, Rowna F, Rowan C, and Wren W. A title.',
     _KEPT[:5] + ['Rowan F']),
    ('a four-letter surname is not matched at one edit',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Rowa F, Wren W. A title.', _KEPT[:5] + ['Rowe F']),
])
def test_source_authors_after_declines_an_anchor_it_cannot_trust(
    case: str, source: str, kept: list[str],
) -> None:
    assert _source_authors_after(source, kept) is None, case


@pytest.mark.parametrize('a, b, apart', [
    ('rowan', 'rowen', True),
    ('rowan', 'rowans', True),
    ('rowans', 'rowan', True),
    ('rowan', 'rowan', False),
    ('rowan', 'rowna', False),
    ('rowan', 'rowaned', False),
    ('rowan', 'rxwxn', False),
])
def test_one_edit_apart(a: str, b: str, apart: bool) -> None:
    assert _one_edit_apart(a, b) is apart
