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

import pytest

from unified_pipeline.stage6.formatting.values import (
    _append_missing_stage5d_values,
    _format_citation,
    _format_currency,
    _format_mentee_duration,
    _value_referenced,
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
# Unit coverage on the two new helpers directly
# ---------------------------------------------------------------------------

def test_value_referenced_true_on_partial_token_overlap():
    assert _value_referenced('Springer-Verlag, NY', 'In: Springer; 2021.') is True


def test_value_referenced_false_when_no_significant_token_overlaps():
    assert _value_referenced('GNYHA', 'Welcome to parenting. 2010.') is False


def test_value_referenced_ignores_short_tokens_and_empty_value():
    # "NY" (2 chars) is below the significance floor and shares no other
    # token with the haystack -- correctly reads as not referenced.
    assert _value_referenced('NY', 'Some other city entirely.') is False
    assert _value_referenced('', 'anything') is False


def test_value_referenced_ignores_short_tokens_even_when_the_token_itself_is_present():
    # Pins _CITATION_TOKEN_MIN_LEN itself, not just the no-overlap case above:
    # "NY" (2 chars) appears verbatim, as a whole word, in the haystack --
    # if the significance floor were lowered (or removed), this would flip
    # to True. It must stay False because "NY" alone is too short to mean
    # anything on its own (an initial, a state abbreviation, etc.).
    assert _value_referenced('NY', 'Published in New York, NY.') is False


def test_append_missing_stage5d_values_appends_both_when_both_absent():
    fields = {'editors': 'Totally Different Names', 'publisher': 'Totally Different Press'}

    result = _append_missing_stage5d_values('Smith J. A Chapter. 2021.', fields)

    assert result == (
        'Smith J. A Chapter. 2021. Totally Different Names, eds. '
        'Totally Different Press.'
    )


# ---------------------------------------------------------------------------
# #481: stop words -- a token long enough to clear the floor but meaningless
# ---------------------------------------------------------------------------

def test_value_referenced_false_when_only_a_stopword_overlaps():
    """The length floor alone let "and" (3 chars) carry a match, so any
    two-name editors string read as already present in any citation whose
    text contained the word "and". Only `_CITATION_STOPWORDS` makes this
    False -- with the set emptied it flips back to True."""
    assert _value_referenced(
        'A. Smith and B. Jones',
        'Doe J. Diet and exercise. In: Book. 2020.',
    ) is False


def test_value_referenced_false_when_only_editorial_boilerplate_overlaps():
    """Same shape for the editorial boilerplate the module comment always
    claimed was excluded: len("eds") == 3, so it cleared the floor, and an
    "X (eds.)" value matched every citation carrying an "eds." clause."""
    assert _value_referenced(
        'Smith A (eds.)',
        'Doe J. Chapter. In: Brown B, eds. Book. 2020.',
    ) is False


def test_value_referenced_still_true_when_a_real_token_sits_beside_a_stopword():
    """The stop list must not make the test blind: "Arts" still matches even
    though "the" no longer does. Guards against a stop list so broad that a
    genuinely-present publisher gets appended a second time."""
    assert _value_referenced(
        'University of the Arts',
        'Doe J. A short history of the Arts. 2020.',
    ) is True


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


def test_citation_stopwords_are_all_at_or_above_the_length_floor():
    """A stop word shorter than `_CITATION_TOKEN_MIN_LEN` would be dead
    weight -- the floor already drops it -- so the two constants are pinned
    together rather than drifting apart silently.

    The two constants are imported here rather than at module scope on
    purpose: a mutation check that swaps this module's source for a baseline
    without them must fail on the *behaviour* tests above, not on a
    collection-time ImportError that hides which assertions would have run.
    """
    from unified_pipeline.stage6.formatting.values import (
        _CITATION_STOPWORDS,
        _CITATION_TOKEN_MIN_LEN,
    )

    assert all(len(w) >= _CITATION_TOKEN_MIN_LEN for w in _CITATION_STOPWORDS)
    assert _CITATION_STOPWORDS == frozenset(w.casefold() for w in _CITATION_STOPWORDS)


# ---------------------------------------------------------------------------
# #481: a non-string stage-4 value must not reach the safety net's regex
# ---------------------------------------------------------------------------

def test_stage5d_non_string_editors_value_is_skipped_not_rendered():
    """Stage 4 is raw LLM-shaped JSON with no schema enforcement, so
    `editors` can arrive as a list. `_value_referenced` would raise TypeError
    on it (`re.findall` over a list); the safety net skips a non-string value
    instead, leaving the citation exactly as stage 5d wrote it."""
    fields = {
        'editors': ['Smith A', 'Jones B'],
        'publisher': 'Acme Press',
        'formatted_citation': 'Doe J. A Chapter. In: Book. Acme Press; 2020.',
        'formatting_source': 'stage_5d_llm',
    }

    citation, _, _ = _format_citation(_entry(fields), 10)

    assert citation == '10. Doe J. A Chapter. In: Book. Acme Press; 2020.'


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
    except clause turns this into an uncaught exception; "nan"/"inf" parse as
    Decimals and are held back by the finiteness check."""
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


# ---------------------------------------------------------------------------
# Round-2 review, point 6: "any token overlaps" is too loose
# ---------------------------------------------------------------------------

# Table-driven, as asked for in round-2 review point 7. Every row carries the
# match ratio it exercises, because the ratio is the rule under test: a value
# reads as referenced only when at least half its significant tokens appear.
# The first six rows are the round-1 cases, re-checked against the new rule --
# none of them may move.
@pytest.mark.parametrize('ratio,value,citation,expected', [
    ('1 of 2', 'Springer-Verlag, NY', 'In: Springer; 2021.', True),
    ('0 of 1', 'GNYHA', 'Welcome to parenting. 2010.', False),
    ('no tokens', 'NY', 'Published in New York, NY.', False),
    ('0 of 2', 'A. Smith and B. Jones',
     'Doe J. Diet and exercise. In: Book. 2020.', False),
    ('0 of 1', 'Smith A (eds.)',
     'Doe J. Chapter. In: Brown B, eds. Book. 2020.', False),
    ('1 of 2', 'University of the Arts',
     'Doe J. A short history of the Arts. 2020.', True),
    # The reviewer's own case: "oxford" alone used to carry the match, so a
    # real publisher was dropped from a citation that never named it.
    ('1 of 3', 'Oxford University Press',
     'Smith J. A paper. Oxford Medical Journal. 2020.', False),
    ('2 of 3', 'Oxford University Press',
     'Smith J. A chapter. In: Oxford University; 2020.', True),
    ('3 of 3', 'Oxford University Press',
     'Smith J. A chapter. In: Oxford University Press; 2020.', True),
    ('1 of 1', 'Müller', 'Smith J. A Chapter. In: Müller; 2020.', True),
    ('0 of 3', 'Oxford University Press',
     'Smith J. A paper. NEJM. 2020;10(2):1-5.', False),
])
def test_value_referenced_ratio_table(ratio, value, citation, expected):
    assert _value_referenced(value, citation) is expected, ratio


def test_citation_match_ratio_threshold_is_bracketed_from_both_sides():
    """Pins the threshold itself, not just the cases either side of it.

    1-of-3 must read as absent, so lowering the rule back to "any token
    overlaps" fails here; 1-of-2 must read as present, so raising it above a
    half (to two thirds, or to every token) fails there. That brackets the
    threshold into (1/3, 1/2], and 0.5 is the value in the module."""
    from unified_pipeline.stage6.formatting.values import _CITATION_MATCH_MIN_RATIO

    assert _value_referenced(
        'Oxford University Press', 'Smith J. A paper. Oxford Medical Journal. 2020.'
    ) is False
    assert _value_referenced(
        'Springer-Verlag, NY', 'In: Springer; 2021.'
    ) is True
    assert _CITATION_MATCH_MIN_RATIO == 0.5


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
    helper (where test_append_missing_stage5d_values_appends_both_when_both_absent
    pins it): the editors clause is appended before the publisher, and both
    sit after the LLM's own text."""
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
