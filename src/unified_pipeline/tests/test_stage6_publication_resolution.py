"""PR #737 round 4, points 1/2/3 and 8-15: one resolver, before any rendering.

`stage6/normalization/publication.py` is where a raw bibliography entry
becomes a `ResolvedPublication` whose every field is already the text a
renderer prints. This file is its contract:

- the None guards (#659) on all three top-level keys, plus the type behind
  each, because `enrichment_data` is the empty *string* in 18,913 of the
  20,582 local farm entries and `.get` on a string raises;
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
    # The shape the farm actually carries: stage 5 writes '' rather than {}
    # for an unenriched entry, and '' has no .get.
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
_SCALAR_FIELDS = ('volume', 'issue', 'pages', 'doi', 'pmid', 'pmcid')


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
    A non-list value -- '' in 18,369 farm entries -- resolves to empty rather
    than being iterated character by character."""
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
# The year: preserved verbatim, and pinned so #767 is visible rather than lost
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('value,expected', [
    ('2021', '2021'),
    (2021, '2021'),
    ('', ''),
])
def test_year_renders_a_string_or_an_int(value, expected):
    assert resolve_publication({'extracted_fields': {'year': value}}).year == expected


def test_year_absent_is_empty_but_an_explicit_null_renders_the_text_None():
    """The one field NOT routed through `_scalar_text`, pinned so the
    difference is a visible decision rather than an oversight.

    `str(fields.get('year', ''))` renders an explicit `None` as the literal
    four-character string "None", which reaches 299 of the 20,582 farm
    citations today (many of them read exactly "18. None."). It is carried
    across unchanged because PR #737's structural round is a refactor whose
    gate is byte-identity with the pre-split renderer -- fixing it here would
    change 299 rendered citations inside a change whose gate cannot see them.
    Tracked, with the measurement and the one-line fix, in #767: when that
    lands, this test flips to `== ''` and `_legacy_year_text` is deleted."""
    assert resolve_publication({'extracted_fields': {}}).year == ''
    assert resolve_publication({'extracted_fields': {'year': None}}).year == 'None'


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
#: `chapter_title` is the live defect in this list, not an intentional
#: omission: stage 4's own S4 schema writes the chapter's title there and the
#: renderer reads `title`, which S4 never writes, so 53 farm book chapters
#: render the book and never the chapter. Tracked in #768. `narrative` and
#: the rest are genuinely not part of a Vancouver citation.
_SCHEMA_KEYS_NOT_RENDERED = frozenset({
    'abstract_number', 'chapter_title', 'conference_name', 'edition', 'isbn',
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
