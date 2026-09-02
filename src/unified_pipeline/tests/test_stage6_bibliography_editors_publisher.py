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

from unified_pipeline.stage6.formatting.values import (
    _append_missing_stage5d_values,
    _format_citation,
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
