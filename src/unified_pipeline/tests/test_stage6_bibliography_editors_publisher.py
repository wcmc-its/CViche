"""#481 + #659: `_format_citation` (`stage6/formatting/values.py`) drops a
book/chapter's editors and publisher, and crashes on an explicit-None
`extracted_fields`.

#481's own 843-value figure is measured over a different, larger corpus;
re-measured on the 66-CV local farm the premise "the prompt never asks for
editors/publisher" is false -- stage 5d's prompt already asks, and most
stage-5d-formatted entries already carry both in their `formatted_citation`
text (see the PR body for the farm numbers). What the farm's one exception
(HU4DXA's "Welcome to Parenting" entry, publisher "GNYHA") shows is that the
LLM sometimes drops one, and nothing downstream can add it back -- stage 5d's
own copy-back list (`stage_5d_citation_formatter.py:355`) never writes
`editors`/`publisher` onto the entry, so a later pass has no field to read.

Two independent gaps, four cases:

1. The non-stage-5d fallback branch (`_format_citation`'s ``elif book_title``
   and trailer) never read `editors`/`publisher` at all -- a synthetic-only
   defect on this farm, since every farm S3/S4 entry is stage-5d-formatted.
2. The stage-5d path's safety net: append an extracted publisher/editors the
   LLM's own formatted text does not already reference, using a whole-word
   casefolded token test so a reworded-but-present value isn't duplicated.
3. `extracted_fields` explicit `None` (#659, one of eleven sites the issue
   names; this is the `_format_citation` site -- `values.py:24` as it stands
   on `origin/dev` -- only. The other ten, all under `stage6/sections/`, are
   sibling PRs in this wave.)

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_bibliography_editors_publisher.py -p no:cacheprovider
"""

import inspect
import os
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal

import pytest

from unified_pipeline.stage6.formatting import values as values_module
from unified_pipeline.stage6.formatting.values import (
    _format_citation,
    _format_currency,
    _format_mentee_duration,
)


def _entry(fields, enrichment=None, enriched=None):
    return {
        'extracted_fields': fields,
        'enrichment_data': enrichment or {},
        'enriched_fields': enriched or [],
    }


# ---------------------------------------------------------------------------
# #659: extracted_fields explicit None
# ---------------------------------------------------------------------------

def test_format_citation_none_extracted_fields_does_not_raise():
    """Positive control (#659): raises AttributeError on dev (on `origin/dev`
    this line is `values.py:24`, `entry.get('extracted_fields', {})`, which
    returns None -- not the default -- when the key is present and explicitly
    None)."""
    entry = {'extracted_fields': None, 'enrichment_data': {}, 'enriched_fields': []}

    citation, target_name, enriched_fields = _format_citation(entry, 1)

    assert citation.startswith('1. ')
    assert target_name is None
    assert enriched_fields == []


def test_format_citation_none_enrichment_data_does_not_raise():
    """The same #659 defect on the adjacent line: `enrichment_data` carrying
    an explicit None reaches `enrichment.get('pubmed_authors')` and raises
    AttributeError. Asked for in round-2 review of this PR."""
    entry = {
        'extracted_fields': {'authors': 'Smith J', 'year': '2021'},
        'enrichment_data': None,
        'enriched_fields': [],
    }

    citation, _, enriched_fields = _format_citation(entry, 1)

    assert citation == '1. Smith J. 2021.'
    assert enriched_fields == []


def test_format_citation_none_enriched_fields_is_returned_as_an_empty_list():
    """Third line of the same trio: an explicit None `enriched_fields` was
    returned to the caller as None, and every caller iterates it."""
    entry = {
        'extracted_fields': {'authors': 'Smith J', 'year': '2021'},
        'enrichment_data': {},
        'enriched_fields': None,
    }

    _, _, enriched_fields = _format_citation(entry, 1)

    assert enriched_fields == []


def test_format_citation_missing_extracted_fields_key_still_works():
    """The pre-existing default-args case (no key at all) keeps working
    after the guard changes from a dict default to `or {}`."""
    entry = {'enrichment_data': {}, 'enriched_fields': []}

    citation, target_name, enriched_fields = _format_citation(entry, 3)

    assert citation == '3. '


# ---------------------------------------------------------------------------
# #481: the non-stage-5d fallback branch -- S4 book chapters
# ---------------------------------------------------------------------------

def test_fallback_s4_entry_renders_book_title_editors_and_publisher():
    """Positive control: a non-enriched S4 entry (book_title/editors/
    publisher all set, no journal, no stage-5d formatted_citation) currently
    drops editors and publisher entirely on dev -- FAILS on dev."""
    fields = {
        'authors': 'Smith J, Doe A',
        'year': '2021',
        'book_title': 'The Big Book of Pediatrics',
        'editors': 'Editor X, Editor Y',
        'publisher': 'Acme Press',
        'pages': '10-20',
        'target_name': 'Smith J',
    }

    citation, _, _ = _format_citation(_entry(fields), 5)

    assert 'In: Editor X, Editor Y, eds. The Big Book of Pediatrics.' in citation
    assert 'Acme Press; 2021:10-20.' in citation


def test_fallback_s4_entry_without_editors_omits_the_eds_clause():
    """editors is optional -- when stage 4 didn't extract it, the book_title
    line stays the plain "In: Book Title." form rather than rendering an
    empty ", eds." clause."""
    fields = {
        'authors': 'Smith J',
        'year': '2021',
        'book_title': 'The Big Book of Pediatrics',
        'publisher': 'Acme Press',
        'target_name': 'Smith J',
    }

    citation, _, _ = _format_citation(_entry(fields), 5)

    assert 'In: The Big Book of Pediatrics.' in citation
    assert ', eds.' not in citation
    assert 'Acme Press; 2021.' in citation


def test_fallback_s4_entry_without_publisher_matches_pre_fix_trailer():
    """No publisher -> the trailer is the original plain Year:Pages form,
    unchanged from before this fix."""
    fields = {
        'authors': 'Smith J',
        'year': '2021',
        'book_title': 'The Big Book of Pediatrics',
        'pages': '10-20',
        'target_name': 'Smith J',
    }

    citation, _, _ = _format_citation(_entry(fields), 5)

    assert citation == '5. Smith J. In: The Big Book of Pediatrics. 2021:10-20.'


# ---------------------------------------------------------------------------
# #481: the non-stage-5d fallback branch -- S3 books (no book_title field)
# ---------------------------------------------------------------------------

def test_fallback_s3_entry_renders_title_and_publisher():
    """S3 (books) has no `book_title` field -- its own `title` already
    renders via the generic title branch. This only has to stop dropping
    `publisher` from the trailer; it must not duplicate the title as a
    second "In: ..." clause."""
    fields = {
        'authors': 'Smith J',
        'year': '2019',
        'title': 'My Book',
        'publisher': 'Acme Press',
        'target_name': 'Smith J',
    }

    citation, _, _ = _format_citation(_entry(fields), 6)

    assert citation == '6. Smith J. My Book. Acme Press; 2019.'
    assert citation.count('My Book') == 1
    assert 'In:' not in citation


def test_fallback_journal_present_ignores_publisher():
    """A journal article (S1/S2/S6) that happens to also carry a stray
    `publisher` key must not switch to the book trailer -- journal wins,
    exactly as before this fix."""
    fields = {
        'authors': 'Smith J',
        'year': '2021',
        'journal': 'NEJM',
        'volume': '10',
        'issue': '2',
        'pages': '1-5',
        'publisher': 'Should Not Appear',
        'target_name': 'Smith J',
    }

    citation, _, _ = _format_citation(_entry(fields), 7)

    assert citation == '7. Smith J. NEJM. 2021;10(2):1-5.'
    assert 'Should Not Appear' not in citation


def test_fallback_journal_beats_a_book_title_on_the_same_entry():
    """The source line is one slot and the journal owns it. An entry
    carrying both -- a misclassified chapter, or an article whose stage-4
    extraction picked up a series title -- renders the journal and no "In:"
    clause.

    Added after a mutation check: swapping the two branches changed 3 farm
    entries and no test, so the precedence was load-bearing and unpinned."""
    fields = {
        'authors': 'Smith J',
        'year': '2021',
        'journal': 'NEJM',
        'book_title': 'A Book That Must Not Appear',
        'editors': 'Editor X',
    }

    citation, _, _ = _format_citation(_entry(fields), 16)

    assert citation == '16. Smith J. NEJM. 2021.'
    assert 'In:' not in citation
    assert 'A Book That Must Not Appear' not in citation


def test_fallback_title_ending_in_a_period_does_not_double_it():
    """The renderer owns the period after the title, so a stage-4 title that
    already ends in one must not render "A Paper..".

    Also from the mutation check: dropping the `rstrip('.')` changed 2,523 of
    the 20,582 farm entries and no test at all -- titles arrive with their
    own terminal period that often."""
    fields = {'authors': 'Smith J', 'title': 'A Paper.', 'journal': 'NEJM'}

    citation, _, _ = _format_citation(_entry(fields), 17)

    assert citation == '17. Smith J. A Paper. NEJM.'


@pytest.mark.parametrize('title,expected', [
    ('A Paper', '1. A Paper. NEJM.'),
    ('A Paper.', '1. A Paper. NEJM.'),
    ('A Paper...', '1. A Paper. NEJM.'),
    ('Is it a paper?', '1. Is it a paper?. NEJM.'),
    ('.', '1. . NEJM.'),
])
def test_title_terminal_punctuation_table(title, expected):
    """Only a period is stripped, and every trailing period is -- a question
    mark keeps its own and gains the renderer's, which is what the farm
    already renders."""
    citation, _, _ = _format_citation(
        _entry({'title': title, 'journal': 'NEJM'}), 1)

    assert citation == expected


# ---------------------------------------------------------------------------
# #481: the stage-5d safety net (the HU4DXA "GNYHA" case)
# ---------------------------------------------------------------------------

def test_stage5d_entry_with_missing_publisher_token_gets_it_appended():
    """The exact HU4DXA "Welcome to Parenting" shape: stage 5d formatted the
    citation without the "GNYHA" publisher token anywhere in the text, even
    though stage 4 extracted it. The editors string ("Melanie Wilson Taylor;
    Susan Bostwick; Evelyn Lipper") IS referenced -- its surnames (Wilson,
    Taylor, Bostwick, Lipper) already appear in the LLM's abbreviated author
    list -- so only publisher is appended, not a redundant editors clause."""
    fields = {
        'authors': 'Taylor MW, Bostwick S, Lipper E',
        'editors': 'Melanie Wilson Taylor; Susan Bostwick; Evelyn Lipper',
        'year': '2010',
        'title': 'Welcome to Parenting',
        'publisher': 'GNYHA',
        'target_name': 'Susan Bostwick',
        'formatted_citation': (
            'Wilson Taylor M, Bostwick S, Lipper E. Welcome to parenting. 2010.'
        ),
        'formatting_source': 'stage_5d_llm',
    }

    citation, target_name, _ = _format_citation(_entry(fields), 2)

    assert citation == (
        '2. Wilson Taylor M, Bostwick S, Lipper E. Welcome to parenting. 2010. GNYHA.'
    )
    assert target_name == 'Susan Bostwick'


def test_stage5d_entry_with_publisher_already_present_is_untouched():
    """Negative control, same farm CV: the "AAP Breastfeeding Resident
    Curriculum" entry's formatted_citation already spells out the publisher
    in full ("American Academy of Pediatrics") -- nothing is appended."""
    fields = {
        'authors': 'Chairperson LF, Developer SBEAC',
        'editors': None,
        'year': '2008',
        'title': 'AAP Breastfeeding Resident Curriculum',
        'publisher': 'American Academy of Pediatrics',
        'target_name': 'Susan Bostwick',
        'formatted_citation': (
            'Feldman-Winter L, Bostwick S, et al. AAP Breastfeeding Resident '
            'Curriculum. e-published. American Academy of Pediatrics; 2008.'
        ),
        'formatting_source': 'stage_5d_llm',
    }
    expected = '1. ' + fields['formatted_citation']

    citation, _, _ = _format_citation(_entry(fields), 1)

    assert citation == expected


def test_stage5d_entry_with_non_ascii_publisher_already_present_is_untouched():
    """A publisher whose only significant token is non-ASCII ("Müller") must
    still count as already present -- the old `[A-Za-z0-9]+` token regex
    could not match the "ü", so the token test always failed and the
    already-present publisher was appended a second time
    ("... Müller; 2020. Müller.")."""
    fields = {
        'publisher': 'Müller',
        'formatted_citation': 'Smith J. A Chapter. In: Müller; 2020.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 6)

    assert citation == '6. Smith J. A Chapter. In: Müller; 2020.'


def test_stage5d_entry_with_reworded_publisher_is_not_duplicated():
    """A publisher reworded rather than dropped ("Springer" standing in for
    "Springer-Verlag, NY") must still count as present -- this is what the
    narrow, whole-word token test exists to protect against appending
    twice."""
    fields = {
        'publisher': 'Springer-Verlag, NY',
        'formatted_citation': 'Smith J. A Chapter. In: Springer; 2021.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 4)

    assert citation == '4. Smith J. A Chapter. In: Springer; 2021.'


def test_stage5d_entry_with_no_editors_or_publisher_fields_is_untouched():
    """No extracted editors/publisher at all (e.g. a journal article stage 5d
    formatted) -- nothing to append, citation passes through unchanged."""
    fields = {
        'formatted_citation': 'Smith J. A Paper. NEJM. 2021;10(2):1-5.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 9)

    assert citation == '9. Smith J. A Paper. NEJM. 2021;10(2):1-5.'


# ---------------------------------------------------------------------------
# #481: stop words at the wire -- the unit table is in
# test_stage6_citation_matching.py, which owns the matcher itself
# ---------------------------------------------------------------------------

def test_stage5d_editors_matching_only_on_a_stopword_are_appended():
    """The wire case, not just the helper: an editors value whose only
    overlap with the LLM's citation is the word "and" must still be treated
    as missing and appended. This is the entry shape #481 exists for."""
    fields = {
        'editors': 'M. Moaddel and M. Gelfand',
        'formatted_citation': 'Yount K. Family and modernity. In: Book. 2008.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 8)

    assert citation == (
        '8. Yount K. Family and modernity. In: Book. 2008. '
        'M. Moaddel and M. Gelfand, eds.'
    )


# ---------------------------------------------------------------------------
# #481: a non-string stage-4 value must not reach the safety net's regex
# ---------------------------------------------------------------------------

def test_stage5d_non_string_editors_value_is_skipped_not_rendered():
    """Stage 4 is raw LLM-shaped JSON with no schema enforcement, so
    `editors` can arrive as a list. `_value_referenced` would raise TypeError
    on it (`re.findall` over a list); `resolve_publication` turns a non-string
    value into `''` before the safety net is called, so the citation is left
    exactly as stage 5d wrote it."""
    fields = {
        'editors': ['Smith A', 'Jones B'],
        'publisher': 'Acme Press',
        'formatted_citation': 'Doe J. A Chapter. In: Book. Acme Press; 2020.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 10)

    assert citation == '10. Doe J. A Chapter. In: Book. Acme Press; 2020.'


# ---------------------------------------------------------------------------
# PR #737 round 4, points 1/2/10/11/14: what the renderer is allowed to know
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('literal', [
    'extracted_fields', 'enrichment_data', 'formatting_source', 'stage_5d_llm',
])
def test_the_renderer_module_names_no_pipeline_key(literal):
    """Points 2/10/11 as a check rather than a claim.

    The renderer receives already-reconciled structured data, so no stage
    key, no enrichment key and no formatting-source value may appear in its
    source at all -- not in code, not in a comment that would tempt the next
    reader to reach for one. `stage6/normalization/publication.py` is the
    only module in stage 6 that reads those shapes."""
    source = inspect.getsource(values_module)

    assert literal not in source


def test_rendering_does_not_normalize_author_names(monkeypatch):
    """Point 14 at the wire: `_normalize_author_names` used to be called from
    inside the assembly loop, which made rendering more than representational.
    Rendering a whole citation must now call it exactly once, from the
    resolver, before any part is assembled."""
    from unified_pipeline.stage6.normalization import publication

    calls = []
    real = publication._normalize_author_names

    def counting(authors):
        calls.append(authors)
        return real(authors)

    monkeypatch.setattr(publication, '_normalize_author_names', counting)

    citation, _, _ = _format_citation(_entry({
        'authors': 'Kelly, R, Pirog, R',
        'title': 'A Paper',
        'journal': 'NEJM',
        'year': '2021',
    }), 1)

    assert citation == '1. Kelly R, Pirog R. A Paper. NEJM. 2021.'
    assert calls == ['Kelly, R, Pirog, R']


# ---------------------------------------------------------------------------
# Round-2 review, points 4 and 5: `_format_currency`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('value', [0, 0.0, '0', '0.00', ' 0 ', '$0'])
def test_format_currency_zero_is_an_amount_not_an_absence(value):
    """`if not value: return ''` made a real $0 award render as nothing.
    A zero-dollar line is a fact about a grant, not a missing value. Only
    int/float 0 were broken -- the string "0" is truthy and already worked,
    and is pinned here so the guard cannot regress in the other direction."""
    assert _format_currency(value) == '$0'


@pytest.mark.parametrize('value', [None, '', '   ', '\t'])
def test_format_currency_absent_values_still_render_empty(value):
    """The other half of point 4: making zero render must not make an
    absent value render "$0"."""
    assert _format_currency(value) == ''


@pytest.mark.parametrize('value', [[], {}, ['14876'], True, False])
def test_format_currency_non_amount_types_render_empty(value):
    """The guard is on type, not truthiness, so raw stage-4 JSON junk (a
    bool, a list) renders nothing rather than its repr. `[]` and `False`
    rendered "" before this change too (they are falsy); `['14876']` and
    `True` previously reached the parser and returned their own repr."""
    assert _format_currency(value) == ''


@pytest.mark.parametrize('value', ['not a number', 'TBD', 'pending', 'nan', 'inf'])
def test_format_currency_unparseable_value_returns_the_original_string(value):
    """Pre-existing behaviour, pinned because the parser changed: an
    unparseable value falls through to the original string. `decimal` raises
    InvalidOperation where `float` raised ValueError, so dropping the added
    except clause turns this into an uncaught exception.

    "nan" and "inf" parse as Decimals and are held back by the finiteness
    check instead. Under float only "nan" behaved this way (ValueError out of
    int()); "inf" raised OverflowError, which the except clause never caught,
    so it escaped the function -- the one row here that is a fix rather than a
    pin."""
    assert _format_currency(value) == value


@pytest.mark.parametrize('value,expected', [
    (14876, '$14,876'),
    ('14876', '$14,876'),
    ('14876.00', '$14,876'),
    ('$14,876', '$14,876'),
    ('14,876', '$14,876'),
    (1234.5, '$1,234.50'),
    (-5.5, '$-5.50'),
])
def test_format_currency_renders_whole_and_fractional_amounts(value, expected):
    """The whole-number branch keeps rendering with no decimals ("$14,876",
    not "$14,876.00") -- the shape every corpus grant row uses today."""
    assert _format_currency(value) == expected


def test_format_currency_keeps_cents_a_float_cannot_represent():
    """Point 5. 12345678901234567.89 has 19 significant digits; the nearest
    double is 12345678901234568.0, which is whole, so the float parser took
    the whole-number branch and rendered a different amount. Decimal keeps
    every digit. The float rendering is computed here rather than pasted, so
    this fails the moment the parser goes back to float."""
    value = '12345678901234567.89'

    assert _format_currency(value) == '$12,345,678,901,234,567.89'

    as_float = float(value)
    float_rendering = (
        f"${int(as_float):,}" if as_float == int(as_float) else f"${as_float:,.2f}"
    )
    assert float_rendering == '$12,345,678,901,234,568'
    assert _format_currency(value) != float_rendering


def test_format_currency_rounds_half_up_not_half_to_even():
    """The rounding policy is now stated (ROUND_HALF_UP) rather than
    inherited: 0.125 is exactly representable in binary and `f"{0.125:,.2f}"`
    rounds it to even, giving "$0.12". A half-cent on a dollar figure rounds
    up."""
    assert _format_currency(0.125) == '$0.13'
    assert f"${0.125:,.2f}" == '$0.12'


def test_format_currency_brackets_the_magnitude_bound_from_both_sides():
    """Round 2 found `quantize` signalling InvalidOperation out of this
    function at 27 integer digits, and scoped a fallback to that branch; round
    3 found the *other* branch hanging on the same class of input. One
    magnitude bound now answers both, so it is pinned from both sides rather
    than only from above: the largest magnitude that renders, and the first
    that does not.

    The fallback value is pinned, not just the absence of an exception: an
    amount past the bound takes the same route an unparseable string takes and
    comes back as its own text."""
    # Imported inside the test, as the ratio threshold above is. It also keeps
    # this file importable against a values.py that predates the constant, so a
    # differential run against the baseline reports a real assertion failure
    # rather than a collection-time ImportError.
    from unified_pipeline.stage6.formatting.values import (
        _CURRENCY_MAX_ADJUSTED_EXPONENT,
    )

    fits = '9' * 25 + '.55'                             # 25 integer digits
    assert Decimal(fits).adjusted() == _CURRENCY_MAX_ADJUSTED_EXPONENT
    assert _format_currency(fits) == '$9,999,999,999,999,999,999,999,999.55'

    over = '1' + '0' * 25 + '.55'                       # 26 integer digits
    assert Decimal(over).adjusted() == _CURRENCY_MAX_ADJUSTED_EXPONENT + 1
    assert _format_currency(over) == over


@pytest.mark.parametrize('value', [
    '1' + '0' * 26 + '.55',      # 27 integer digits
    '1' + '0' * 29 + '.55',      # 30
    '1' + '0' * 39 + '.55',      # 40
    '1e5000',                    # int() ValueError, 4300-digit conversion limit
    '-' + '1' + '0' * 39 + '.55',
])
def test_format_currency_extreme_magnitudes_return_the_original_text(value):
    """Renamed from `test_format_currency_is_total_over_extreme_magnitudes`
    (round-3 review of #481). The old name claimed totality over every
    str/int/float/Decimal and the tree does not have it: an `int` at or past
    10**4301 raises inside `str(value)`, before the guarded region -- see
    `test_format_currency_int_past_the_str_digit_limit_raises_as_it_always_did`
    below, which pins that residual instead of denying it.

    Every row here is cheap to materialise, which is why they all passed at
    df43344 while the same function hung on an input eleven characters long.
    The magnitudes that are not cheap belong in
    `test_format_currency_returns_inside_a_wall_clock_budget`, which runs them
    out of process: putting them here would hang the whole suite the moment the
    bound regressed, instead of failing it -- measured, that file took 45.5s to
    report three failures against df43344 rather than never returning."""
    assert _format_currency(value) == value


# ---------------------------------------------------------------------------
# Round-3 review: the guard turned a fast raise into an unbounded hang
# ---------------------------------------------------------------------------

# A hang is not a raise. `assert _format_currency(v) == v` passes against a
# call that eventually returns, however long "eventually" is, and there is no
# in-process way to interrupt one: the time is spent inside a single C-level
# int conversion, so a SIGALRM handler does not get a bytecode boundary to run
# on. The call therefore runs in a child process under a hard wall-clock
# timeout, which turns a hang into a failed test rather than a stalled suite.
_CURRENCY_CALL_BUDGET_SECONDS = 0.5
# Generous next to the budget above, because it also covers interpreter
# startup and the import (~0.16s measured); the budget, timed inside the
# child around the call alone, is what actually pins the cost.
_CURRENCY_CHILD_TIMEOUT_SECONDS = 15.0

_CURRENCY_CHILD = """
import sys, time
from unified_pipeline.stage6.formatting.values import _format_currency
t0 = time.perf_counter()
out = _format_currency(sys.argv[1])
sys.stdout.write("%.6f\\n%s" % (time.perf_counter() - t0, out))
"""


def _time_format_currency_out_of_process(value):
    """Return `(result, seconds)` for `_format_currency(value)` in a child.

    Fails the test if the child does not finish, rather than waiting on it.
    """
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(entry for entry in sys.path if entry),
        PYTHONDONTWRITEBYTECODE='1',
    )
    try:
        done = subprocess.run(
            [sys.executable, '-c', _CURRENCY_CHILD, value],
            capture_output=True, text=True, env=env,
            timeout=_CURRENCY_CHILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"_format_currency({value!r}) did not return within "
            f"{_CURRENCY_CHILD_TIMEOUT_SECONDS}s -- the magnitude is being "
            f"materialised again (round-3 review of #481)."
        )
    assert done.returncode == 0, done.stderr
    seconds, _, out = done.stdout.partition('\n')
    return out, float(seconds)


# The measured cost of `int(Decimal(v))` on the commit this pins against
# (df43344) grows with the square of the exponent -- 1e50000 0.043s, 1e200000
# 0.696s, 1e1000000 17.289s, 1e2000000 69.173s -- so the first row, eleven
# characters of input, is hours of CPU. b77d766 raised OverflowError on all
# four in 0.000s.
@pytest.mark.parametrize('value,history', [
    ('1e999999999', 'no return in 15s at df43344'),
    ('1e5000000', 'no return in 15s at df43344'),
    ('1e1000000', '17.289s at df43344'),
    ('1' + '0' * 39 + '.55', '40 integer digits'),
])
def test_format_currency_returns_inside_a_wall_clock_budget(value, history):
    """The round-3 guard converted a fast, loud OverflowError into silent
    unbounded CPU inside a stage the orchestrator does not time out, and
    `except (ValueError, ArithmeticError)` cannot catch that. The cost is now
    bounded by `Decimal.adjusted()`, which reads the exponent in O(1) without
    materialising a digit, so these all return immediately.

    Both halves are asserted, because either alone is worthless here: the
    value, so a guard that returns the wrong thing fails; the elapsed time, so
    a guard that is merely slower than it looks fails too."""
    out, seconds = _time_format_currency_out_of_process(value)

    assert out == value, history
    assert seconds < _CURRENCY_CALL_BUDGET_SECONDS, (
        f"{value[:16]}... ({history}) took {seconds:.3f}s, budget "
        f"{_CURRENCY_CALL_BUDGET_SECONDS}s")


def test_format_currency_int_past_the_str_digit_limit_raises_as_it_always_did():
    """The one input `_format_currency` does not survive, pinned rather than
    claimed away (round-3 review of #481). `str(value)` sits before the try,
    and CPython refuses to render an int wider than
    `sys.get_int_max_str_digits()` (4300). It is left there deliberately: the
    fallback every guarded path returns *is* `value_str`, so the conversion
    cannot be inside the region whose handler needs it.

    Not a regression -- b77d766 raises the identical ValueError -- and not
    reachable through `json.loads`, which refuses the same literal at the same
    limit while parsing it, as the second half asserts."""
    import json

    with pytest.raises(ValueError, match='Exceeds the limit'):
        _format_currency(10 ** 4301)

    with pytest.raises(ValueError, match='Exceeds the limit'):
        json.loads('1' + '0' * 4301)

    # One digit under the limit is fine on both, and takes the ordinary
    # oversized-magnitude fallback rather than raising.
    just_under = '1' + '0' * 4298
    assert len(str(int(just_under))) == 4299
    assert _format_currency(int(just_under)) == just_under


def test_format_currency_bound_applies_to_the_whole_branch_too():
    """Replaces `..._whole_amounts_are_not_capped_by_the_context`, whose
    docstring said "a whole amount of any size still formats, because `int()`
    is exact and no quantize runs". Exact is not the same as cheap, and that
    sentence was the round-3 defect written down as a feature: `int()` on
    Decimal('1e999999999') is exact and materialises a billion digits while it
    gets there. The two branches now share one bound, so a whole amount and a
    fractional amount of the same magnitude agree about where rendering
    stops."""
    from unified_pipeline.stage6.formatting.values import (
        _CURRENCY_MAX_ADJUSTED_EXPONENT,
    )

    whole_fits = '9' * 25
    assert Decimal(whole_fits).adjusted() == _CURRENCY_MAX_ADJUSTED_EXPONENT
    assert _format_currency(whole_fits) == '$9,999,999,999,999,999,999,999,999'

    whole_over = '1' + '0' * 25                         # 26 integer digits
    assert _format_currency(whole_over) == whole_over
    assert _format_currency(whole_over + '.55') == whole_over + '.55'


def test_currency_bound_subsumes_the_quantize_signal_rather_than_splitting_it():
    """The bound and the round-2 InvalidOperation fallback must not divide the
    magnitude range between them, or the same "too big" input renders through
    two different code paths depending on where it lands.

    The bound is the smaller of the two candidate numbers for exactly this
    reason: 25 integer digits, plus 2 for cents, plus 1 for a rounding carry,
    is 28 -- the default decimal context precision -- so every magnitude the
    bound admits can still be quantized, carry and all, and no magnitude
    reaches the except clause. Raising the constant breaks this test before it
    can silently re-split the range."""
    import decimal

    from unified_pipeline.stage6.formatting.values import (
        _CURRENCY_CENTS,
        _CURRENCY_MAX_ADJUSTED_EXPONENT,
    )

    integer_digits = _CURRENCY_MAX_ADJUSTED_EXPONENT + 1
    cents = -_CURRENCY_CENTS.as_tuple().exponent
    carry = 1
    assert integer_digits == 25
    assert cents == 2
    assert integer_digits + cents + carry <= decimal.getcontext().prec

    # The worst admitted case: every digit a nine, so quantizing to cents
    # rounds up and grows the integer part by one.
    worst = '9' * integer_digits + '.999'
    assert Decimal(worst).adjusted() == _CURRENCY_MAX_ADJUSTED_EXPONENT
    assert Decimal(worst).quantize(_CURRENCY_CENTS, rounding=ROUND_HALF_UP) == (
        Decimal('1' + '0' * integer_digits + '.00'))
    assert _format_currency(worst) == '$10,000,000,000,000,000,000,000,000.00'


# ---------------------------------------------------------------------------
# Round-2 review, point 6: "any token overlaps" is too loose -- at the wire
# ---------------------------------------------------------------------------

def test_stage5d_publisher_sharing_one_token_of_three_is_appended():
    """The wire consequence of point 6, not just the helper: a citation that
    names "Oxford Medical Journal" does not name the publisher "Oxford
    University Press", and under the round-1 rule the publisher was silently
    dropped from the rendered citation."""
    fields = {
        'publisher': 'Oxford University Press',
        'formatted_citation': 'Smith J. A paper. Oxford Medical Journal. 2020.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 11)

    assert citation == (
        '11. Smith J. A paper. Oxford Medical Journal. 2020. '
        'Oxford University Press.'
    )


# ---------------------------------------------------------------------------
# Round-2 review, point 12: one owner for the identifier punctuation
# ---------------------------------------------------------------------------

def _identifier_run_as_round_1_built_it(doi='', pmid='', pmcid=''):
    """The pre-change expression, verbatim from `values.py` before this
    commit, so the equality below is a differential proof rather than a
    pasted string that could have been copied from the new output."""
    ids = []
    if doi:
        ids.append(f"doi:{doi}.")
    if pmid:
        ids.append(f"PMID:{pmid}.")
    if pmcid:
        ids.append(f"PMCID:{pmcid}.")
    return " ".join(ids).rstrip('.') + "." if ids else ''


@pytest.mark.parametrize('ids', [
    {'doi': '10.1000/abc'},
    {'pmid': '12345678'},
    {'pmcid': 'PMC1234567'},
    {'doi': '10.1000/abc', 'pmid': '12345678'},
    {'pmid': '12345678', 'pmcid': 'PMC1234567'},
    {'doi': '10.1000/abc', 'pmcid': 'PMC1234567'},
    {'doi': '10.1000/abc', 'pmid': '12345678', 'pmcid': 'PMC1234567'},
])
def test_identifier_run_is_byte_identical_to_the_two_owner_form(ids):
    """1-, 2- and 3-identifier runs render exactly as they did when each id
    carried its own period and the join stripped the tail back off."""
    fields = {'authors': 'Smith J', 'year': '2021', **ids}

    citation, _, _ = _format_citation(_entry(fields), 1)

    assert citation.endswith(_identifier_run_as_round_1_built_it(**ids))


def test_identifier_run_renders_the_expected_literal_shapes():
    """The same three cases spelled out, so a change to both the code and
    the round-1 helper above cannot pass unnoticed."""
    def run(**ids):
        return _format_citation(_entry({**ids}), 1)[0]

    assert run(doi='10.1000/abc') == '1. doi:10.1000/abc.'
    assert run(doi='10.1000/abc', pmid='123') == '1. doi:10.1000/abc. PMID:123.'
    assert run(doi='10.1000/abc', pmid='123', pmcid='PMC9') == (
        '1. doi:10.1000/abc. PMID:123. PMCID:PMC9.'
    )


def test_identifier_value_that_ends_in_a_period_keeps_its_own_period():
    """The one shape where the single-owner form differs, recorded rather
    than hidden: a stage-4 doi extracted with a trailing period used to have
    it stripped by the `rstrip('.')` (which ate the value's character, not
    just the separator's) and now keeps it, so the run reads "abc..". It
    differs only when the *last* identifier ends in a period -- with a pmid
    or pmcid following, both forms already agreed. No farm entry carries such
    a value: 0 of the 2,003 doi/pmid/pmcid values in the 66 local stage-4
    field-extraction outputs, and 0 of the 4,035 in the 61 stage-5d outputs,
    end in a period."""
    citation, _, _ = _format_citation(_entry({'doi': '10.1000/abc.'}), 1)

    assert citation == '1. doi:10.1000/abc..'
    assert _identifier_run_as_round_1_built_it(doi='10.1000/abc.') == 'doi:10.1000/abc.'


# ---------------------------------------------------------------------------
# Round-2 review, point 13: a non-string value on the fallback path
# ---------------------------------------------------------------------------

def test_fallback_non_string_editors_value_is_skipped_not_rendered():
    """The stage-5d net guards with isinstance; the deterministic fallback
    branch did not, and it interpolates the value straight into "In: {editors},
    eds." -- so a list from raw stage-4 JSON rendered its repr into the
    document. (The stage-5d half of this is already pinned by
    test_stage5d_non_string_editors_value_is_skipped_not_rendered.)"""
    fields = {
        'authors': 'Smith J',
        'year': '2021',
        'book_title': 'The Big Book of Pediatrics',
        'editors': ['Editor X', 'Editor Y'],
    }

    citation, _, _ = _format_citation(_entry(fields), 12)

    assert citation == '12. Smith J. In: The Big Book of Pediatrics. 2021.'
    assert 'Editor X' not in citation
    assert '[' not in citation


def test_fallback_non_string_publisher_value_is_skipped_not_rendered():
    """The publisher half, which the reviewer asked for by name: a non-string
    publisher must not select the book trailer and must not render its repr.
    The year trailer falls back to the plain Year:Pages form."""
    fields = {
        'authors': 'Smith J',
        'year': '2021',
        'title': 'My Book',
        'publisher': {'name': 'Acme Press'},
        'pages': '10-20',
    }

    citation, _, _ = _format_citation(_entry(fields), 13)

    assert citation == '13. Smith J. My Book. 2021:10-20.'
    assert 'Acme Press' not in citation
    assert '{' not in citation


@pytest.mark.parametrize('value', [None, 42, ['a'], {'a': 1}])
def test_fallback_non_string_editors_and_publisher_never_reach_the_page(value):
    """Table over the shapes raw stage-4 JSON can produce for either field:
    none of them may appear in the rendered citation."""
    fields = {
        'authors': 'Smith J',
        'year': '2021',
        'book_title': 'A Book',
        'editors': value,
        'publisher': value,
    }

    citation, _, _ = _format_citation(_entry(fields), 14)

    assert citation == '14. Smith J. In: A Book. 2021.'


# ---------------------------------------------------------------------------
# Round-2 review, test-coverage list: `_format_mentee_duration`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('fields,expected', [
    ({'start_date': '2019', 'end_date': '2021'}, '2019-2021'),
    ({'start_date': '2019', 'end_date': ''}, '2019-present'),
    ({'start_date': '2019'}, '2019-present'),
    ({'start_date': '', 'end_date': ''}, ''),
    ({}, ''),
    # An end with no start reads as no duration at all rather than
    # "-2021" -- current behaviour, pinned because nothing else states it.
    ({'end_date': '2021'}, ''),
])
def test_format_mentee_duration_covers_every_branch(fields, expected):
    """The helper had no test of any kind. All three branches (start+end,
    start alone, neither) plus the end-alone shape that falls into the last
    one."""
    assert _format_mentee_duration(fields) == expected


# ---------------------------------------------------------------------------
# Round-2 review, test-coverage list: the two stage-5d entry conditions
# ---------------------------------------------------------------------------

def test_empty_formatted_citation_falls_through_to_deterministic_assembly():
    """`formatted_citation` present but empty, with the stage-5d source: the
    branch is guarded on the text, not the source, so the entry is assembled
    from its extracted fields instead of rendering "1. "."""
    fields = {
        'authors': 'Smith J',
        'title': 'A Paper',
        'journal': 'NEJM',
        'year': '2021',
        'formatted_citation': '',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 1)

    assert citation == '1. Smith J. A Paper. NEJM. 2021.'


def test_non_stage5d_formatting_source_falls_through_to_deterministic_assembly():
    """The other half of the same condition: a `formatted_citation` written
    by anything other than stage 5d is not trusted, and the entry is
    assembled from its fields -- the LLM string does not appear."""
    fields = {
        'authors': 'Smith J',
        'title': 'A Paper',
        'journal': 'NEJM',
        'year': '2021',
        'formatted_citation': 'Some other formatter wrote this.',
        'formatting_source': 'stage_4_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 1)

    assert citation == '1. Smith J. A Paper. NEJM. 2021.'
    assert 'Some other formatter' not in citation


# ---------------------------------------------------------------------------
# Round-2 review, test-coverage list: the stage-5d single-field branches
# ---------------------------------------------------------------------------

def test_stage5d_editors_only_already_referenced_are_not_appended():
    """The editors-only branch in its negative direction. (Its positive
    direction -- editors alone, absent, appended -- is already pinned by
    test_stage5d_editors_matching_only_on_a_stopword_are_appended; the
    publisher-only branch by
    test_stage5d_publisher_sharing_one_token_of_three_is_appended and
    test_stage5d_entry_with_reworded_publisher_is_not_duplicated.)"""
    fields = {
        'editors': 'Moaddel M, Gelfand M',
        'formatted_citation': 'Yount K. A chapter. In: Moaddel M, Gelfand M, eds. Book. 2008.',
        'formatting_source': 'stage_5d_llm',
    }
    expected = '1. ' + fields['formatted_citation']

    citation, _, _ = _format_citation(_entry(fields), 1)

    assert citation == expected


def test_stage5d_both_values_absent_are_appended_editors_first():
    """Both branches firing on one entry, at the wire rather than on the
    helper (where test_stage6_citation_matching.py's
    test_append_missing_stage5d_values_appends_both_when_both_absent pins it):
    the editors clause is appended before the publisher, and both sit after
    the LLM's own text."""
    fields = {
        'editors': 'Adams Q, Baker R',
        'publisher': 'Zenith House',
        'formatted_citation': 'Doe J. A chapter. In: Book. 2020.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 15)

    assert citation == (
        '15. Doe J. A chapter. In: Book. 2020. Adams Q, Baker R, eds. Zenith House.'
    )


# ---------------------------------------------------------------------------
# Round-2 review, test-coverage list: enrichment precedence and fallback
# ---------------------------------------------------------------------------

_BASE_ENRICHMENT_FIELDS = {
    'authors': 'Extracted A',
    'title': 'Extracted Title',
    'journal': 'Extracted Journal',
    'year': '2021',
    'volume': '11',
    'issue': '22',
    'pages': '33-44',
}


@pytest.mark.parametrize('enrichment_key,enriched_value,expected', [
    ('pubmed_authors', 'Enriched A', '1. Enriched A. Extracted Title. Extracted Journal. 2021;11(22):33-44.'),
    ('pubmed_title', 'Enriched Title', '1. Extracted A. Enriched Title. Extracted Journal. 2021;11(22):33-44.'),
    ('pubmed_journal', 'Enriched Journal', '1. Extracted A. Extracted Title. Enriched Journal. 2021;11(22):33-44.'),
    ('pubmed_volume', '99', '1. Extracted A. Extracted Title. Extracted Journal. 2021;99(22):33-44.'),
    ('pubmed_issue', '88', '1. Extracted A. Extracted Title. Extracted Journal. 2021;11(88):33-44.'),
    ('pubmed_pages', '77-78', '1. Extracted A. Extracted Title. Extracted Journal. 2021;11(22):77-78.'),
])
def test_enriched_pubmed_value_wins_over_the_extracted_one(
    enrichment_key, enriched_value, expected
):
    """Each of the six enrichment keys the citation reads, one row each:
    the PubMed value replaces the extracted one and nothing else moves."""
    entry = _entry(dict(_BASE_ENRICHMENT_FIELDS), {enrichment_key: enriched_value})

    citation, _, _ = _format_citation(entry, 1)

    assert citation == expected


@pytest.mark.parametrize('enrichment_key', [
    'pubmed_authors', 'pubmed_title', 'pubmed_journal',
    'pubmed_volume', 'pubmed_issue', 'pubmed_pages',
])
def test_extracted_value_is_used_when_the_enriched_one_is_absent_or_empty(
    enrichment_key
):
    """The fallback half of the same six. Absent, None and empty-string all
    fall back -- the `or` chain treats an empty enrichment value as no
    enrichment, which is what stage 5 writes when PubMed had no such field."""
    all_extracted = (
        '1. Extracted A. Extracted Title. Extracted Journal. 2021;11(22):33-44.'
    )

    for enrichment in ({}, {enrichment_key: None}, {enrichment_key: ''}):
        entry = _entry(dict(_BASE_ENRICHMENT_FIELDS), enrichment)

        citation, _, _ = _format_citation(entry, 1)

        assert citation == all_extracted, enrichment


def test_enriched_fields_list_is_passed_through_to_the_caller():
    """The third return value is the entry's own `enriched_fields`, which the
    caller uses to mark enriched text -- unchanged by either branch."""
    entry = _entry({'authors': 'Smith J', 'year': '2021'}, enriched=['title', 'journal'])

    _, _, enriched_fields = _format_citation(entry, 1)

    assert enriched_fields == ['title', 'journal']
