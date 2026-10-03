"""#481 + PR #737 point 7: the citation-matching component, as a table.

`_value_referenced` and `_append_missing_stage5d_values` moved out of
`stage6/formatting/values.py` into `stage6/normalization/citation_matching.py`
at the round-4 review of PR #737. The reviewer's point was that the stop-word
list encodes citation-domain semantics inside what reads as a generic token
matcher, and that the pair should be a named component with table-driven
tests. This file is that table; the tests in it are the ones that were in
`test_stage6_bibliography_editors_publisher.py`, moved with their subject and
not rewritten, plus the rows the extraction asked for.

The wire-level tests -- the same rules observed through `_format_citation` --
stay in `test_stage6_bibliography_editors_publisher.py` on purpose: they are
about the renderer's behaviour, not about the matcher's.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_citation_matching.py -p no:cacheprovider

Self-contained: pure functions over two strings. No DB, no network, no LLM,
no PII.
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.citation_matching import (  # noqa: E402
    _append_missing_stage5d_values,
    _owner_words,
    _prints_junk_author,
    _restore_stage5d_owner,
    _stage5d_author_segment,
    _value_referenced,
)


# ---------------------------------------------------------------------------
# `_value_referenced`: the individual cases, each naming what it pins
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


def test_citation_stopwords_are_all_at_or_above_the_length_floor():
    """A stop word shorter than `_CITATION_TOKEN_MIN_LEN` would be dead
    weight -- the floor already drops it -- so the two constants are pinned
    together rather than drifting apart silently.

    The two constants are imported here rather than at module scope on
    purpose: a mutation check that swaps this module's source for a baseline
    without them must fail on the *behaviour* tests above, not on a
    collection-time ImportError that hides which assertions would have run.
    """
    from unified_pipeline.stage6.normalization.citation_matching import (
        _CITATION_STOPWORDS,
        _CITATION_TOKEN_MIN_LEN,
    )

    assert all(len(w) >= _CITATION_TOKEN_MIN_LEN for w in _CITATION_STOPWORDS)
    assert _CITATION_STOPWORDS == frozenset(w.casefold() for w in _CITATION_STOPWORDS)


# ---------------------------------------------------------------------------
# `_value_referenced`: the ratio, as a table
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
    # Rows added with the extraction (PR #737 point 7), covering the parts of
    # the rule the eleven above leave to inference.
    ('empty haystack', 'Acme Press', '', False),
    ('casefold both ways', 'ACME PRESS', 'In: acme press; 2020.', True),
    ('whole words only', 'Press', 'In: Impressionism; 2020.', False),
    ('digits are tokens', '2020 Committee', 'A report by the 2020 Committee.', True),
    ('punctuation splits', 'Springer/Verlag', 'In: Springer; 2021.', True),
    ('underscore is not a token char', 'Acme_Press', 'In: Acme; 2020.', True),
]) 
def test_value_referenced_ratio_table(ratio, value, citation, expected):
    assert _value_referenced(value, citation) is expected, ratio


def test_citation_match_ratio_threshold_is_bracketed_from_both_sides():
    """Pins the threshold itself, not just the cases either side of it.

    1-of-3 must read as absent, so lowering the rule back to "any token
    overlaps" fails here; 1-of-2 must read as present, so raising it above a
    half (to two thirds, or to every token) fails there. That brackets the
    threshold into (1/3, 1/2], and 0.5 is the value in the module."""
    from unified_pipeline.stage6.normalization.citation_matching import (
        _CITATION_MATCH_MIN_RATIO,
    )

    assert _value_referenced(
        'Oxford University Press', 'Smith J. A paper. Oxford Medical Journal. 2020.'
    ) is False
    assert _value_referenced(
        'Springer-Verlag, NY', 'In: Springer; 2021.'
    ) is True
    assert _CITATION_MATCH_MIN_RATIO == 0.5


# ---------------------------------------------------------------------------
# `_append_missing_stage5d_values`, as a table
# ---------------------------------------------------------------------------

def test_append_missing_stage5d_values_appends_both_when_both_absent():
    """Moved with its subject. The call now takes the two values as text
    rather than a stage-4 field dict -- the type guard it used to carry moved
    to `resolve_publication`, which is the only reader of raw stage-4 JSON --
    but the assertion is the one it always made."""
    result = _append_missing_stage5d_values(
        'Smith J. A Chapter. 2021.',
        'Totally Different Names',
        'Totally Different Press',
    )

    assert result == (
        'Smith J. A Chapter. 2021. Totally Different Names, eds. '
        'Totally Different Press.'
    )


#: (case, citation, editors, publisher, expected). One row per branch of the
#: safety net, so the ordering rule ("editors clause first"), each half firing
#: alone, and every reason not to fire are all stated in one place.
_APPEND_ROWS = [
    ('neither value carried',
     'Doe J. A chapter. In: Book. 2020.', '', '',
     'Doe J. A chapter. In: Book. 2020.'),
    ('editors alone, absent from the text',
     'Yount K. Family and modernity. In: Book. 2008.',
     'M. Moaddel and M. Gelfand', '',
     'Yount K. Family and modernity. In: Book. 2008. '
     'M. Moaddel and M. Gelfand, eds.'),
    ('publisher alone, absent from the text',
     'Welcome to parenting. 2010.', '', 'GNYHA',
     'Welcome to parenting. 2010. GNYHA.'),
    ('both absent -- editors clause comes first',
     'Doe J. A chapter. In: Book. 2020.', 'Adams Q, Baker R', 'Zenith House',
     'Doe J. A chapter. In: Book. 2020. Adams Q, Baker R, eds. Zenith House.'),
    ('editors already referenced, publisher not',
     'Doe J. In: Adams Q, Baker R, eds. Book. 2020.',
     'Adams Q, Baker R', 'Zenith House',
     'Doe J. In: Adams Q, Baker R, eds. Book. 2020. Zenith House.'),
    ('publisher already referenced, editors not',
     'Doe J. A chapter. In: Book. Zenith House; 2020.',
     'Adams Q, Baker R', 'Zenith House',
     'Doe J. A chapter. In: Book. Zenith House; 2020. Adams Q, Baker R, eds.'),
    ('both already referenced -- citation untouched',
     'Doe J. In: Adams Q, Baker R, eds. Book. Zenith House; 2020.',
     'Adams Q, Baker R', 'Zenith House',
     'Doe J. In: Adams Q, Baker R, eds. Book. Zenith House; 2020.'),
    ('reworded publisher still counts as present',
     'Smith J. A Chapter. In: Springer; 2021.', '', 'Springer-Verlag, NY',
     'Smith J. A Chapter. In: Springer; 2021.'),
]


@pytest.mark.parametrize(
    'case,citation,editors,publisher,expected',
    _APPEND_ROWS,
    ids=[row[0] for row in _APPEND_ROWS],
)
def test_append_missing_stage5d_values_table(
    case, citation, editors, publisher, expected
):
    assert _append_missing_stage5d_values(citation, editors, publisher) == expected, case


# ---------------------------------------------------------------------------
# #1259: the CV owner back in a citation stage 5d cut to "first 6, et al."
# Every name below is invented. The owner is Wren.
# ---------------------------------------------------------------------------

_CUT = 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, et al. A title. J Wood. 2020;1:2-3.'
_LINE = '4. Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W. A title. J Wood 2020;1:2-3.'


@pytest.mark.parametrize('case, citation, authors, source, expected', [
    ('stage 4\'s list is intact: printed whole, as #1292 shipped',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W', '',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W. A title. J Wood. 2020;1:2-3.'),
    ('stage 4 split an author (lone initials): the line supplies the authors after the kept six',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, G, Gorse, Wren W', _LINE,
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W. A title. J Wood. 2020;1:2-3.'),
    ('stage 4\'s list starts unlike 5d\'s (5d fixed a spelling): the kept six keep 5d\'s',
     _CUT, 'Ash A, Brich B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W', _LINE.replace('Birch', 'Brich'),
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W. A title. J Wood. 2020;1:2-3.'),
    ('stage 4 read a group credit as an author: not printed',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W, Network LRSN',
     _LINE.replace('Wren W.', 'Wren W; for the Lantern Research Network.'),
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W. A title. J Wood. 2020;1:2-3.'),
    ('stage 4 put a suffix first in an item: not printed',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, 3rd GI, Wren W', _LINE.replace('Gorse G', 'Gorse GI 3rd'),
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse GI 3rd, Wren W. A title. J Wood. 2020;1:2-3.'),
    ('a four-letter initials group the line prints is a real author: stage 4\'s list stands',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse GHIJ, Wren W', _LINE.replace('Gorse G', 'Gorse GHIJ'),
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse GHIJ, Wren W. A title. J Wood. 2020;1:2-3.'),
    ('the owner only in a note after the title is still cut from the authors',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, et al. A title. Venue; 2020. Presented by Wren W.',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Wren W, Gorse G', '',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Wren W, Gorse G. A title. Venue; 2020. Presented by Wren W.'),
    ('the line says "et al." after the owner',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, G, Gorse, Wren W, et al',
     _LINE.replace('Wren W.', 'Wren W, et al.'),
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W, et al. A title. J Wood. 2020;1:2-3.'),
    ('the line goes on past the owner with a person the reader cannot read: "et al.", not a short list',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, G, Gorse, Wren W, S, Holly Rowan',
     _LINE.replace('Wren W.', 'Wren W, S. Holly Rowan.'),
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W, et al. A title. J Wood. 2020;1:2-3.'),
    ('co-authors whose surnames are particle words are read, before and after the owner',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, G, Le, Wren W, Du H',
     _LINE.replace('Gorse G, Wren W.', 'Le G, Wren W, Du H.'),
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Le G, Wren W, Du H. A title. J Wood. 2020;1:2-3.'),
    ('5d doubled the period after "et al.": the restore prints one',
     _CUT.replace('et al.', 'et al..'), '', _LINE,
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G, Wren W. A title. J Wood. 2020;1:2-3.'),
])
def test_restore_stage5d_owner_gives_the_owner_back(
    case: str, citation: str, authors: str, source: str, expected: str,
) -> None:
    assert _restore_stage5d_owner(citation, authors, source, 'Wren W') == expected, case


@pytest.mark.parametrize('case, citation, authors, source', [
    ('the owner is among 5d\'s authors',
     'Ash A, Wren W, Cedar C, Daly D, Elm E, Fir F, et al. A title. 2020.',
     'Ash A, Wren W, Cedar C, Daly D, Elm E, Fir F, Gorse G', ''),
    ('a CV\'s own elided list names the owner between two "et al."',
     'Ash A, et al., Wren W, et al., Holly H. A title. 2020.', 'Ash A, Wren W, Holly H', ''),
    ('a 5d list with no "et al." was not cut, even when the line goes on to the owner',
     'Ash A, Birch B, Fir F', 'Ash A, Birch B, Fir F, G, Gorse, Wren W', 'Ash A, Birch B, Fir F, Wren W. A title.'),
    ('stage 4 is damaged and there is no source line',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, G, Gorse, Wren W', ''),
    ('the line\'s run ends before the owner (a group credit closes it)',
     _CUT, 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, G, Gorse, Wren W',
     _LINE.replace('Gorse G,', 'Gorse G; for the Lantern Network. A title,')),
    ('5d corrected a swap the next co-author\'s surname shares (ZCTARO 810): no trusted anchor',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, Rowan F, et al. A title. J Wood. 2020;1:2-3.',
     'Ash A, Birch B, Cedar C, Daly D, Elm E, G, Rowna, Rowan C, Wren W',
     '4. Ash A, Birch B, Cedar C, Daly D, Elm E, Rowna F, Rowan C, and Wren W. A title. J Wood 2020;1:2-3.'),
])
def test_restore_stage5d_owner_leaves_the_citation_alone(
    case: str, citation: str, authors: str, source: str,
) -> None:
    assert _restore_stage5d_owner(citation, authors, source, 'Wren W') == citation, case


def test_a_credential_in_capitals_in_the_owners_name_is_not_their_surname() -> None:
    """The owner ("Wren W, FAAP") is among 5d's authors. Stage 4's list
    carries the credential, 5d's does not; that is not a cut owner."""
    citation = 'Ash A, Wren W, Birch B, Cedar C, Daly D, Elm E, et al. A title. 2020.'
    authors = 'Ash A, Wren W FAAP, Birch B, Cedar C, Daly D, Elm E, Fir F'
    source = 'Ash A, Wren W FAAP, Birch B, Cedar C, Daly D, Elm E, Fir F. A title. 2020.'

    assert _restore_stage5d_owner(citation, authors, source, 'Wren W, FAAP') == citation


def test_a_given_name_5d_shortened_is_not_a_cut_owner() -> None:
    """The owner (Juno Wren) is among 5d's authors as "Wren J"; only the
    given name is missing. A co-author surnamed Juno later in the line must
    not read as the owner and pull the rest of the list back in."""
    citation = 'Ash A, Birch B, Cedar C, Daly D, Elm E, Wren J, et al. A title. 2020.'
    source = 'Ash A, Birch B, Cedar C, Daly D, Elm E, Wren J, Juno AB, Gorse G. A title. 2020.'

    assert _restore_stage5d_owner(citation, '', source, 'Juno Wren') == citation


@pytest.mark.parametrize('authors, source, junk', [
    ('Ash A, Network LRSN', 'Ash A; for the Lantern Research Network.', True),
    ('Ash A, 3rd GI, Wren W', 'Ash A, Gorse GI 3rd, Wren W.', True),
    ('Ash A, Jr, Wren W', 'Ash A, Jr, Wren W.', True),
    ('Ash A, Sr GI, Wren W', 'Ash A, Gorse GI Sr, Wren W.', True),
    ('Ash A, Gorse GHIJ', 'Ash A, Gorse GHIJ.', False),
    # a three-letter initials group is never checked against the line
    ('Ash A, Gorse GHI, Wren W', 'Ash A, Gorse G.H.I., Wren W.', False),
    ('Ash A, Gorse GI Jr, Wren W', 'Ash A, Gorse GI Jr, Wren W.', False),
    ('Jr AB, Wren W', 'Gorse AB Jr, Wren W.', True),
])
def test_prints_junk_author(authors: str, source: str, junk: bool) -> None:
    assert _prints_junk_author(authors, source) is junk


@pytest.mark.parametrize('citation, segment', [
    ('Ash A, Birch B, et al. A title. 2020.', 'Ash A, Birch B, '),
    # the "et al." is the editors', after the title
    ('Ash A. A chapter. In: Birch B, et al., eds. 2020.', 'Ash A. A chapter. In: Birch B, et al., eds. 2020.'),
    # the same, with no comma after the "et al."
    ('Ash A. A chapter. In: Birch B, et al. A book. 2020.', 'Ash A. A chapter. In: Birch B, et al. A book. 2020.'),
    # a CV's own elided list goes on after the "et al."
    ('Ash A, et al., Wren W. A title. 2020.', 'Ash A, et al., Wren W. A title. 2020.'),
    ('Ash A, et al.; Wren W. A title. 2020.', 'Ash A, et al.; Wren W. A title. 2020.'),
    ('Ash A, Birch B. A title. 2020.', 'Ash A, Birch B. A title. 2020.'),
])
def test_stage5d_author_segment(citation: str, segment: str) -> None:
    assert _stage5d_author_segment(citation) == segment



def test_owner_words_leave_out_credentials() -> None:
    assert _owner_words('Juno Wren PhD') == {'juno', 'wren'}
