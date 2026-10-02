"""PR #737 round 4, points 1/2/3 and 8-15: one resolver, before any rendering.

`stage6/normalization/publication.py` is where a raw bibliography entry
becomes a `ResolvedPublication` whose every field is already the text a
renderer prints. This file is its contract:

- the None guards (#659) on all three top-level keys, plus the type behind
  each. Measured, no farm entry exercises either guard -- `enrichment_data`
  is *absent* in 18,913 of the 20,582 local entries and a dict in the other
  1,669, never `''` and never null -- so both are hardening against a shape
  nothing upstream enforces, and this file is where that stays true;
- enrichment-over-extracted precedence, in both directions, for each of the
  six PubMed keys;
- non-text coercion for every field the renderer prints, which is what
  replaced the per-field `isinstance` guards (points 13 and the off-diff
  item: `authors`/`title`/`journal`/`book_title` had none at all);
- author normalization happening exactly once, at resolve time, not inside
  the render loop (point 14);
- the drift guard against `stage4/schemas.py`, so a stage-4 field the
  bibliography cannot render is a failing test rather than silent content
  loss.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_publication_resolution.py -p no:cacheprovider

Self-contained: pure functions over dicts. No DB, no network, no LLM, no PII.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization import publication  # noqa: E402
from unified_pipeline.stage6.normalization.publication import (  # noqa: E402
    PublicationFields,
    PubMedEnrichment,
    ResolvedPublication,
    resolve_publication,
)


# ---------------------------------------------------------------------------
# #659: the three top-level keys, absent / explicitly null / wrongly typed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('entry', [
    {},
    {'extracted_fields': None, 'enrichment_data': None, 'enriched_fields': None},
    {'extracted_fields': {}, 'enrichment_data': {}, 'enriched_fields': []},
    # No farm entry carries these two as '' -- an unenriched entry omits the
    # key entirely (measured: enrichment_data absent 18,913 / dict 1,669,
    # enriched_fields absent 18,369 / list 2,213). Kept because '' has no
    # .get and nothing upstream forbids it.
    {'extracted_fields': {}, 'enrichment_data': '', 'enriched_fields': ''},
    # Nothing writes these, but nothing type-checks a json.load either.
    {'extracted_fields': ['a'], 'enrichment_data': ['b'], 'enriched_fields': {'c': 1}},
    {'extracted_fields': 7, 'enrichment_data': 7, 'enriched_fields': 7},
])
def test_resolve_publication_never_raises_on_a_malformed_entry(entry):
    """Every top-level shape the pipeline can hand this resolves to the empty
    record rather than raising. The `or` handles absent and null; the
    isinstance behind it handles a value of the wrong type."""
    assert resolve_publication(entry) == ResolvedPublication()


def test_resolve_publication_reads_a_non_dict_extracted_fields_as_empty():
    """A list-shaped `extracted_fields` must not reach `.get` -- and must not
    take any field from the entry either."""
    resolved = resolve_publication(
        {'extracted_fields': [{'authors': 'Smith J'}], 'enrichment_data': {}}
    )

    assert resolved.authors == ''
    assert resolved == ResolvedPublication()


# ---------------------------------------------------------------------------
# Non-text coercion: the fix for points 13 and the off-diff item, by
# construction rather than by four more isinstance calls
# ---------------------------------------------------------------------------

#: Every field the renderer prints as prose. A number is not a title and a
#: list is not a publisher, so none of these may survive as anything but a
#: string. `authors`/`title`/`journal`/`book_title` are the four that carried
#: no guard at all before this module existed.
_PROSE_FIELDS = (
    'authors', 'title', 'journal', 'book_title', 'editors', 'publisher',
    'formatted_citation',
)

#: The shapes raw stage-4 LLM JSON has actually been observed to produce for
#: a field the prompt asked for as a string (#442, #450, #554), plus the
#: scalars that would render a number where a name belongs.
_NON_TEXT_VALUES = (None, 42, 4.5, True, False, ['a', 'b'], {'a': 1}, (), Decimal('3'))


@pytest.mark.parametrize('field', _PROSE_FIELDS)
@pytest.mark.parametrize('value', _NON_TEXT_VALUES)
def test_prose_field_that_is_not_a_string_resolves_to_empty(field, value):
    resolved = resolve_publication({'extracted_fields': {field: value}})

    assert getattr(resolved, field) == '', f"{field}={value!r}"


#: The same fields minus `formatted_citation`, whose positive direction
#: depends on `formatting_source` and is covered in its own section below.
_PROSE_FIELDS_CARRIED_VERBATIM = tuple(
    f for f in _PROSE_FIELDS if f != 'formatted_citation')


@pytest.mark.parametrize('field', _PROSE_FIELDS_CARRIED_VERBATIM)
def test_prose_field_that_is_a_string_is_carried_through(field):
    """The other half: the coercion must not eat a real value. `authors` is
    normalized on the way through, so it gets a name already in Vancouver
    form; the rest are verbatim."""
    value = 'Smith J' if field == 'authors' else 'A Real Value'
    resolved = resolve_publication({'extracted_fields': {field: value}})

    assert getattr(resolved, field) == value


#: The fields that are a number or an identifier rather than prose. Stage 4
#: writes an int for these (69 int `year` and 1 int `volume` across the
#: 20,582-entry farm), so unlike the prose fields a number is a real value.
#:
#: `year` is in this tuple as of round 5 and that is the point of the tuple:
#: it was the one field routed around both coercers, so every shape below
#: reached the page as its own repr. Nothing about it is special, and the
#: table is what says so.
_SCALAR_FIELDS = ('year', 'volume', 'issue', 'pages', 'doi', 'pmid', 'pmcid')


@pytest.mark.parametrize('field', _SCALAR_FIELDS)
@pytest.mark.parametrize('value,expected', [
    ('12', '12'), (12, '12'), (12.5, '12.5'), (Decimal('12'), '12'),
    (None, ''), ('', ''), (0, ''), (True, ''), (False, ''),
    (['12'], ''), ({'v': 12}, ''),
])
def test_scalar_field_renders_a_number_and_nothing_else(field, value, expected):
    resolved = resolve_publication({'extracted_fields': {field: value}})

    assert getattr(resolved, field) == expected, f"{field}={value!r}"


def test_target_name_keeps_its_none_and_drops_a_non_string():
    """`target_name` is the one field that stays `str | None`: the caller
    distinguishes "no author to bold" from "an empty name". An empty string
    is a real, distinct value and is preserved; a list is not a name."""
    assert resolve_publication({}).target_name is None
    assert resolve_publication(
        {'extracted_fields': {'target_name': None}}).target_name is None
    assert resolve_publication(
        {'extracted_fields': {'target_name': ''}}).target_name == ''
    assert resolve_publication(
        {'extracted_fields': {'target_name': 'Smith J'}}).target_name == 'Smith J'
    assert resolve_publication(
        {'extracted_fields': {'target_name': ['Smith J']}}).target_name is None


def test_enriched_fields_is_a_tuple_and_a_non_list_is_dropped():
    """A frozen record cannot carry a list without lying about being frozen.
    A non-list value resolves to empty rather than being iterated character
    by character. (The farm shape is absent-or-list: 18,369 entries omit the
    key and 2,213 carry a non-empty list. `''` is the hardening case.)"""
    assert resolve_publication(
        {'enriched_fields': ['title', 'journal']}).enriched_fields == ('title', 'journal')
    assert resolve_publication({'enriched_fields': ''}).enriched_fields == ()
    assert resolve_publication({'enriched_fields': 'title'}).enriched_fields == ()
    assert resolve_publication({'enriched_fields': None}).enriched_fields == ()


def test_resolved_publication_cannot_be_mutated_by_a_renderer():
    resolved = resolve_publication({'extracted_fields': {'title': 'A Paper'}})

    with pytest.raises(Exception):
        resolved.title = 'Something Else'  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The year: the last field that bypassed both coercers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('value,expected', [
    ('2021', '2021'),
    (2021, '2021'),
    ('', ''),
])
def test_year_renders_a_string_or_an_int(value, expected):
    assert resolve_publication({'extracted_fields': {'year': value}}).year == expected


#: What `str(fields.get('year', ''))` -- the expression this replaced -- put
#: on the page for each shape, against what `_scalar_text` puts there now.
#: Every row is a repr the old expression rendered into a citation.
_YEAR_REPR_LEAKS = (
    (None, 'None'),
    ([2021], '[2021]'),
    ({'year': 2021}, "{'year': 2021}"),
    (True, 'True'),
    (0, '0'),
    (0.0, '0.0'),
)


@pytest.mark.parametrize('value,old_repr', _YEAR_REPR_LEAKS)
def test_a_non_year_shape_no_longer_reaches_the_page_as_its_repr(value, old_repr):
    """`year` was carried across the split as a bare `str()`, which is not a
    coercion: it printed the repr of whatever stage 4 wrote. The docstring
    that guarded it claimed the two expressions "differ on exactly one
    input"; measured, they differ on all six rows of this table.

    The corpus-visible one is `None`. 381 of the 20,582 farm entries carry
    `year: null` and 299 of them rendered the literal text "None" into a
    citation -- many reading exactly "18. None.". After this they render no
    year, which is also the cosmetic defect PR #737's own body noted on
    2082_Dr_Scot's "Legislative Norms in the Twenty-First Century" entry."""
    resolved = resolve_publication({'extracted_fields': {'year': value}})

    assert str(value) == old_repr, 'the table must state the real old output'
    assert resolved.year == ''


def test_an_absent_and_a_null_year_are_now_the_same_absence():
    assert resolve_publication({'extracted_fields': {}}).year == ''
    assert resolve_publication({'extracted_fields': {'year': None}}).year == ''


# ---------------------------------------------------------------------------
# #728: the S4 chapter title, which stage 4 writes under a different key
# ---------------------------------------------------------------------------

def test_a_chapter_title_fills_the_title_slot_when_stage4_wrote_no_title():
    """The measured S4 shape, and the whole of #728: stage 4's S4 schema has
    no `title` key -- 53 of the 95 farm S4 entries carry `chapter_title` and
    no `title` at all (zero carry `title: null`).

    49 of those 53 change what they render. The other four are the two
    guards this file pins either side of this test: one carries
    `chapter_title: null`, three already had a PubMed title."""
    resolved = resolve_publication({'extracted_fields': {
        'chapter_title': 'Genomic Instability in Human Premature Aging',
        'book_title': 'Aging at the Molecular Level',
    }})

    assert resolved.title == 'Genomic Instability in Human Premature Aging'
    assert resolved.book_title == 'Aging at the Molecular Level'


@pytest.mark.parametrize('title', ['A Real Title', 'Extracted'])
def test_an_extracted_title_outranks_the_chapter_title(title):
    """The other 42 farm S4 entries carry both, because stage 5d writes a
    `title` back. `chapter_title` is a fall-through, never an override -- the
    reason the fix cannot render the chapter twice."""
    resolved = resolve_publication({'extracted_fields': {
        'title': title,
        'chapter_title': 'The Chapter Title',
    }})

    assert resolved.title == title


def test_a_pubmed_title_still_outranks_a_chapter_title():
    """Precedence order is unchanged: enrichment, then extracted, then the
    schema-specific name."""
    resolved = resolve_publication({
        'extracted_fields': {'chapter_title': 'The Chapter Title'},
        'enrichment_data': {'pubmed_title': 'The PubMed Title'},
    })

    assert resolved.title == 'The PubMed Title'


@pytest.mark.parametrize('value', _NON_TEXT_VALUES)
def test_a_non_text_chapter_title_resolves_to_empty_like_every_other_field(value):
    """It goes through `_text` like the rest -- a new field is not a new hole
    in the coercion."""
    assert resolve_publication(
        {'extracted_fields': {'chapter_title': value}}).title == ''


@pytest.mark.parametrize('title', ['', None, []])
def test_an_empty_title_of_any_shape_falls_through_to_the_chapter_title(title):
    """`''`, an explicit null and a non-text shape all mean "no title", so
    all three reach the chapter title rather than only the absent key."""
    resolved = resolve_publication({'extracted_fields': {
        'title': title,
        'chapter_title': 'The Chapter Title',
    }})

    assert resolved.title == 'The Chapter Title'


# ---------------------------------------------------------------------------
# Enrichment precedence, in both directions
# ---------------------------------------------------------------------------

_PRECEDENCE_PAIRS = (
    ('pubmed_authors', 'authors'),
    ('pubmed_title', 'title'),
    ('pubmed_journal', 'journal'),
    ('pubmed_volume', 'volume'),
    ('pubmed_issue', 'issue'),
    ('pubmed_pages', 'pages'),
)


@pytest.mark.parametrize('enriched_key,field', _PRECEDENCE_PAIRS)
def test_the_pubmed_value_outranks_the_extracted_one(enriched_key, field):
    resolved = resolve_publication({
        'extracted_fields': {field: 'Extracted'},
        'enrichment_data': {enriched_key: 'Enriched'},
    })

    assert getattr(resolved, field) == 'Enriched'


#: Stands for "the key is not in the enrichment dict at all", which is a
#: third case distinct from a null value and an empty one.
_KEY_ABSENT = object()


@pytest.mark.parametrize('enriched_key,field', _PRECEDENCE_PAIRS)
@pytest.mark.parametrize(
    'enrichment_value', [_KEY_ABSENT, None, ''],
    ids=['absent', 'null', 'empty'],
)
def test_absent_null_and_empty_enrichment_all_fall_back(
    enriched_key, field, enrichment_value
):
    """An empty enrichment value is what stage 5 writes when PubMed had no
    such field -- it means "no enrichment", not "render nothing"."""
    enrichment = (
        {} if enrichment_value is _KEY_ABSENT else {enriched_key: enrichment_value})
    resolved = resolve_publication({
        'extracted_fields': {field: 'Extracted'},
        'enrichment_data': enrichment,
    })

    assert getattr(resolved, field) == 'Extracted'


@pytest.mark.parametrize('enriched_key,field', _PRECEDENCE_PAIRS)
def test_a_non_text_enrichment_value_falls_back_rather_than_poisoning_the_field(
    enriched_key, field
):
    """The enrichment side gets the same coercion as the extracted side, so a
    list-shaped PubMed value cannot render its repr -- and the good extracted
    value is used instead of being shadowed by it."""
    resolved = resolve_publication({
        'extracted_fields': {field: 'Extracted'},
        'enrichment_data': {enriched_key: ['Enriched']},
    })

    assert getattr(resolved, field) == 'Extracted'


# ---------------------------------------------------------------------------
# Point 14: author normalization runs once, here, not during rendering
# ---------------------------------------------------------------------------

def test_authors_are_normalized_while_the_record_is_built():
    resolved = resolve_publication(
        {'extracted_fields': {'authors': 'Kelly, R, Pirog, R'}})

    assert resolved.authors == 'Kelly R, Pirog R'


def test_the_enriched_author_list_is_normalized_too():
    """Precedence first, normalization second -- a PubMed author list is not
    exempt from the Vancouver rule."""
    resolved = resolve_publication({
        'extracted_fields': {'authors': 'Extracted, A'},
        'enrichment_data': {'pubmed_authors': 'Watson, K.,,'},
    })

    assert resolved.authors == 'Watson K'


def test_author_normalization_runs_exactly_once_per_entry(monkeypatch):
    """Point 14's actual claim, not a proxy for it: rendering is no longer
    allowed to normalize, so the normalizer is called once while the record
    is constructed and never again."""
    calls = []
    real = publication._normalize_author_names

    def counting(authors):
        calls.append(authors)
        return real(authors)

    monkeypatch.setattr(publication, '_normalize_author_names', counting)

    resolve_publication({'extracted_fields': {'authors': 'Kelly, R, Pirog, R'}})

    assert calls == ['Kelly, R, Pirog, R']


# ---------------------------------------------------------------------------
# Points 2/10/11: the stage-5d reconciliation happens before any rendering
# ---------------------------------------------------------------------------

def test_a_stage5d_citation_arrives_already_topped_up():
    resolved = resolve_publication({'extracted_fields': {
        'formatted_citation': 'Welcome to parenting. 2010.',
        'formatting_source': 'stage_5d_llm',
        'publisher': 'GNYHA',
    }})

    assert resolved.formatted_citation == 'Welcome to parenting. 2010. GNYHA.'


@pytest.mark.parametrize('fields', [
    {'formatted_citation': '', 'formatting_source': 'stage_5d_llm'},
    {'formatted_citation': 'Some other formatter wrote this.',
     'formatting_source': 'stage_4_llm'},
    {'formatted_citation': 'No source at all.'},
    {'formatted_citation': ['a list'], 'formatting_source': 'stage_5d_llm'},
])
def test_anything_but_a_stage5d_citation_resolves_to_no_citation(fields):
    """`formatted_citation` is non-empty on the record only when stage 5d's
    LLM wrote it. Everything else means "assemble this entry from its own
    fields", and the renderer reads that from the record alone -- it never
    has to know who wrote the string or what the source key is called."""
    assert resolve_publication({'extracted_fields': fields}).formatted_citation == ''


def test_a_non_string_editors_value_cannot_reach_the_stage5d_safety_net():
    """The guard `_append_missing_stage5d_values` used to carry itself. It is
    now upstream of the call, so the matcher never sees a list and the
    citation is left exactly as stage 5d wrote it."""
    resolved = resolve_publication({'extracted_fields': {
        'formatted_citation': 'Doe J. A Chapter. In: Book. Acme Press; 2020.',
        'formatting_source': 'stage_5d_llm',
        'editors': ['Smith A', 'Jones B'],
        'publisher': 'Acme Press',
    }})

    assert resolved.formatted_citation == (
        'Doe J. A Chapter. In: Book. Acme Press; 2020.')


_EIGHT_AUTHORS = 'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse G M, Holly H'


def _stage5d(citation, authors, target_name='Gorse GM'):
    return resolve_publication({'extracted_fields': {
        'formatted_citation': citation, 'formatting_source': 'stage_5d_llm',
        'authors': authors, 'target_name': target_name}}).formatted_citation


def test_a_stage5d_et_al_the_source_lacks_gets_the_whole_source_list():
    """#1259: 5d's "first 6, et al." rule cut the CV owner (here the
    seventh author, Gorse) from their own citation. Stage 4's list replaces
    the author segment; the rest of 5d's citation is kept."""
    assert _stage5d('Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, et al. A title. J Wood. 2020;1:2-3.',
                    _EIGHT_AUTHORS) == (
        'Ash A, Birch B, Cedar C, Daly D, Elm E, Fir F, Gorse GM, Holly H. A title. J Wood. 2020;1:2-3.')


@pytest.mark.parametrize('citation, authors', [
    # the owner is still in 5d's citation: a cut list alone is left as 5d wrote it
    ('Ash A, Birch B, Gorse GM, et al. A title. 2020.', 'Ash A, Birch B, Gorse G M, Holly H'),
    # the source itself says "et al."
    ('Ash A, Birch B, et al. A title. 2020.', 'Ash A, Birch B, Gorse G, et al'),
    # the "et al." is the editors', after the title
    ('Ash A. A chapter. In: Birch B, et al., eds. A book. 2020.', 'Ash A, Gorse G'),
    # stage 4 split an author in two ("Perri, G., M"): its list would render that
    ('Ash A, Birch B, et al. A title. 2020.', 'Ash, A, Birch, B, Perri, G., M, Gorse, G'),
    # the source list does not start with the authors 5d kept (a misspelling)
    ('Ash A, Birch B, et al. A title. 2020.', 'Ash A, Brich B, Cedar C, Gorse GM'),
    # no source list at all
    ('Ash A, Birch B, et al. A title. 2020.', ''),
])
def test_a_stage5d_citation_is_left_alone_otherwise(citation, authors):
    assert _stage5d(citation, authors) == citation


# ---------------------------------------------------------------------------
# Drift guard against stage 4's own schemas
# ---------------------------------------------------------------------------

#: The nine publication taxonomy codes `sections/bibliography.py` renders
#: (`pub_codes`, line 179). S0 is the researcher-profile summary and is
#: rendered by a different section.
_PUBLICATION_CODES = ('S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9')

#: Stage-4 S1-S9 fields the bibliography does not read, measured against
#: `stage4/schemas.py` and checked in so the set cannot grow silently -- a
#: renderer that is a fixed enumeration of key names loses any field it does
#: not name, with no warning (`_recover_unrendered_records` checks whole
#: lines and cannot see a missing field).
#:
#: `chapter_title` left this list in round 5 (#728): stage 4's own S4 schema
#: writes the chapter's title there and never writes `title`, so 53 farm book
#: chapters rendered the book and never the chapter. `narrative` and the rest
#: are genuinely not part of a Vancouver citation.
_SCHEMA_KEYS_NOT_RENDERED = frozenset({
    'abstract_number', 'conference_name', 'edition', 'isbn',
    'location', 'media_type', 'narrative', 'publication_venue',
    'report_number', 'status', 'target_journal', 'url', 'venue',
})


def _stage4_publication_schema_keys() -> frozenset[str]:
    """Every field stage 4 may write on an S1-S9 entry.

    Imported from the test rather than from `publication.py` on purpose:
    `src/unified_pipeline/stage6/` imports no `stage4` module anywhere today,
    and adding that edge in source would couple the renderer to the
    extractor's schema at import time for no runtime benefit. Keeping
    `PublicationFields` hand-written and checking it from here gets the drift
    caught without the coupling -- the same trade `stage4/coercion.py` makes
    for the taxonomy codes it names.
    """
    from unified_pipeline.stage4.schemas import FIELD_SCHEMAS

    return frozenset(
        key
        for code in _PUBLICATION_CODES
        for key in FIELD_SCHEMAS[code]['fields']
    )


def test_every_field_the_renderer_reads_is_a_field_stage_4_can_write():
    """A `PublicationFields` key that no S1-S9 schema declares is a key
    nothing will ever populate -- dead code in the renderer, or a typo."""
    declared = set(PublicationFields.__annotations__)
    # Written by stage 5d, not by stage 4's field schemas.
    stage_5d_written = {'formatted_citation', 'formatting_source'}

    assert (declared - stage_5d_written) <= _stage4_publication_schema_keys()


def test_schema_keys_the_bibliography_does_not_read_are_pinned():
    """The other direction, which is the one that loses content: a stage-4
    field the renderer does not name is dropped silently. Adding a field to
    an S1-S9 schema fails here until someone decides whether the bibliography
    should render it."""
    declared = set(PublicationFields.__annotations__)

    assert _stage4_publication_schema_keys() - declared == _SCHEMA_KEYS_NOT_RENDERED


def test_the_pubmed_enrichment_type_lists_exactly_the_keys_that_are_read():
    """`PubMedEnrichment` and the precedence table above are two statements
    of the same fact; pin them together."""
    assert set(PubMedEnrichment.__annotations__) == {
        key for key, _ in _PRECEDENCE_PAIRS
    }
