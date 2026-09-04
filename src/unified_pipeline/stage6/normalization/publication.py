"""A raw publication entry in, one resolved record out (#481, #659, PR #737).

Everything `_format_citation` used to do *before* it could render a character
happens here, once: the None guards, enrichment-over-extracted precedence,
coercion of a non-text stage-4 value, author normalization, and the stage-5d
reconciliation. The renderer receives a `ResolvedPublication` whose every
field is already the text it will print, and so no longer names a single
pipeline key -- no `extracted_fields`, no `enrichment_data`, no
`formatting_source`, no `stage_5d_llm` (round-4 review of PR #737, points
1/2/3, 8-11 and 13-15).

Why that separation is worth a module rather than a few helpers in the
renderer:

- **The precedence rule was written seven times.** `enrichment.get('pubmed_x')
  or fields.get('x')` appeared once per field, so adding a field meant
  remembering a rule that lived nowhere. It is now one expression shape
  applied in one place.
- **The type guards were per-field, and two fields had none.** Stage 4 stores
  raw LLM JSON against no schema (#442, #450, #554), so any field can arrive
  as a list or a dict. `editors` and `publisher` were guarded with
  `isinstance` after a list-shaped `editors` rendered its repr into a
  citation; `authors`, `title`, `journal` and `book_title` sat on the same
  branch with no guard at all, where a list would have raised `TypeError`
  out of `_normalize_author_names` or `.rstrip`. The fix is not four more
  `isinstance` calls: it is that a value reaches the renderer as text or not
  at all, which is what `_text`/`_scalar_text` below are for. That sentence
  was not true when it was first written: `year` was still carried across as
  `str(fields.get('year', ''))`, one bare `str()` that turned every shape --
  a list, a dict, `True`, `None` -- into its own repr and shipped it to the
  page. It is routed through `_scalar_text` as of round 5, so the claim now
  holds of every field this module returns, and the corpus effect of closing
  it is measured on `resolve_publication` below.
- **Normalization ran during rendering.** `_normalize_author_names` was
  called inside the assembly loop, so "render" was not a purely
  representational step (point 14). It runs once, here, while the record is
  being constructed.

    PublicationFields     what the renderer expects `extracted_fields` to hold
    PubMedEnrichment      what it expects `enrichment_data` to hold
    ResolvedPublication   what it actually gets: every field already text
    resolve_publication   the one function that reads raw pipeline shapes
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypedDict

from .citation_matching import _append_missing_stage5d_values
from .text import _normalize_author_names

#: The value `extracted_fields['formatting_source']` carries when stage 5d's
#: LLM wrote `formatted_citation`. It is the only source the renderer trusts
#: to have produced a whole citation; anything else means the entry is
#: assembled from its own fields (§8.2 -- the literal decided a branch and
#: appeared in the renderer, `records.py` and the tests with no name).
_STAGE_5D_FORMATTING_SOURCE = 'stage_5d_llm'


class PublicationFields(TypedDict, total=False):
    """The `extracted_fields` keys a bibliography entry is read for.

    `total=False` because stage 4 writes whichever of its S1-S9 schema fields
    the LLM returned, and no key is guaranteed -- 12,109 of the 20,582 local
    farm entries carry no `authors` key at all. The annotations say what the
    renderer *expects*; `resolve_publication` is what enforces it, because
    nothing type-checks a `json.load` (§8.1's four incidents are all this
    shape). A key stage 4 writes and this does not list is a key the
    bibliography cannot render -- `tests/test_stage6_publication_resolution.py`
    pins the difference against `stage4/schemas.py` so it cannot grow
    silently, which is the "stage 6 drops unnamed fields" class.
    """
    authors: str
    title: str
    #: The S4 book-chapter schema writes the chapter's own title here and
    #: never writes `title` at all, so a renderer that reads only `title`
    #: renders the book and silently loses the chapter (#728). Resolved into
    #: the `title` slot below rather than given a slot of its own: it is the
    #: same thing under a schema-specific name, and a second slot would make
    #: every downstream renderer decide between them.
    chapter_title: str
    journal: str
    book_title: str
    editors: str
    publisher: str
    year: str
    volume: str
    issue: str
    pages: str
    doi: str
    pmid: str
    pmcid: str
    target_name: str
    formatted_citation: str
    formatting_source: str


class PubMedEnrichment(TypedDict, total=False):
    """The `enrichment_data` keys that outrank their extracted counterparts.

    Stage 5 writes these from PubMed (`stage_5_pubmed_enrichment.py:655`);
    an entry PubMed could not match carries none of them, which is why the
    precedence rule has to treat absent, `None` and `''` alike.
    """
    pubmed_authors: str
    pubmed_title: str
    pubmed_journal: str
    pubmed_volume: str
    pubmed_issue: str
    pubmed_pages: str


@dataclass(frozen=True, slots=True)
class ResolvedPublication:
    """One publication entry, resolved: every field is the text to print.

    Frozen because a renderer must not be able to edit the record it was
    handed -- the entry it came from is shared with the caller's own
    provenance reads (`enrichment_status`, `text`, the classification
    comment), and #454's class of defect is one section reaching into
    another's state. `enriched_fields` is a tuple for the same reason: a list
    on a frozen dataclass is a mutable field wearing an immutable label.

    An absent value is `''`, never `None`, so every consumer is a truthiness
    test and never a `None` check -- except `target_name`, which stays
    `str | None` because the caller distinguishes "no target to bold" from
    "an empty target name" and `_add_citation_with_bold_author` takes
    `Optional[str]`.
    """
    authors: str = ''
    title: str = ''
    journal: str = ''
    book_title: str = ''
    editors: str = ''
    publisher: str = ''
    year: str = ''
    volume: str = ''
    issue: str = ''
    pages: str = ''
    doi: str = ''
    pmid: str = ''
    pmcid: str = ''
    #: Non-empty only for a stage-5d LLM citation, and already topped up with
    #: the editors/publisher its own text omitted (#481). A renderer that
    #: finds it set prints it and stops; it never has to ask who wrote it.
    formatted_citation: str = ''
    target_name: str | None = None
    enriched_fields: tuple[str, ...] = ()


def _text(value: object) -> str:
    """A stage-4 value that has to be prose -- a name, a title, a publisher.

    A number is not a title and a list is not a publisher, so anything that
    is not already a string is not this value: it becomes `''` and the
    renderer omits it. That is the whole of the fix for the repr-in-the-
    document class (#442, #450, #554) on these fields, and it is why no
    renderer downstream carries an `isinstance` call.
    """
    return value if isinstance(value, str) else ''


def _scalar_text(value: object) -> str:
    """A stage-4 value that is a number or an identifier rendered as text --
    a year, a volume, an issue, a page range, a DOI, a PMID.

    Unlike `_text` this accepts a number, because stage 4 legitimately writes
    one: 69 `int` years and 1 `int` volume across the 20,582-entry local farm.
    A bool is not a number here -- it is an `int` subclass that would print
    "True" -- and a list or dict is not a value at all. A falsy input returns
    `''` so that "no value" and "zero" reach the renderer the same way its
    `or` chain always delivered them.
    """
    if not value:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return ''
    return str(value)


def resolve_publication(entry: dict[str, Any]) -> ResolvedPublication:
    """Read one raw bibliography entry and return the record a renderer prints.

    `entry` is raw stage-4/stage-5 JSON and carries more than this reads
    (`enrichment_status`, `text`, `classification_reasoning`, the taxonomy
    code), which is why it is annotated as the mapping it is rather than a
    TypedDict that would describe only part of it. The three keys taken here
    are `extracted_fields` (`PublicationFields`), `enrichment_data`
    (`PubMedEnrichment`) and `enriched_fields`.

    Each is guarded with `or` and then a type check, not with a `.get`
    default (#659): a default only applies when the key is *absent*, so an
    entry carrying the key with an explicit `None` hands the `None` straight
    back and the first `.get` on it raises `AttributeError`.

    Both guards are hardening on the shapes this corpus actually carries, and
    that is stated rather than assumed. Measured over the 20,582 local farm
    entries: `enrichment_data` is *absent* in 18,913 and a dict in the other
    1,669 -- never `''` and never null (`grep -roh '"enrichment_data": *""'`
    and the same for `null` both return 0); `enriched_fields` is absent in
    18,369 and a non-empty list in 2,213; `extracted_fields` is a dict in all
    20,582. So no farm entry exercises either the `or` or the `isinstance`
    today. They stay because the shape is not enforced anywhere upstream --
    stage 4 writes raw LLM JSON against no schema (#442, #450, #554) and a
    single explicit null would raise `AttributeError` out of the first `.get`
    on the web path, which has no handler.

    Precedence: a PubMed value outranks the extracted one, and absent, `None`
    and `''` all fall through to the extracted value -- an empty enrichment
    field is what stage 5 writes when PubMed had no such field, not an
    instruction to render nothing. `''` is the live one of those three: the
    six `pubmed_*` keys are present on every enrichment dict, and 331 of them
    across the farm are empty strings.
    """
    fields = entry.get('extracted_fields') or {}
    enrichment = entry.get('enrichment_data') or {}
    enriched_fields = entry.get('enriched_fields') or []
    if not isinstance(fields, dict):
        fields = {}
    if not isinstance(enrichment, dict):
        enrichment = {}
    if not isinstance(enriched_fields, list):
        enriched_fields = []

    editors = _text(fields.get('editors'))
    publisher = _text(fields.get('publisher'))

    # The stage-5d reconciliation, done here so the renderer receives a
    # citation that is already whole (points 2/10/11). Stage 5d's copy-back
    # loop never writes editors/publisher back onto the entry, so a citation
    # its LLM formatted without one of them has no later stage that can add
    # it -- except this one (#481).
    formatted_citation = _text(fields.get('formatted_citation'))
    if formatted_citation and fields.get('formatting_source') == _STAGE_5D_FORMATTING_SOURCE:
        formatted_citation = _append_missing_stage5d_values(
            formatted_citation, editors, publisher)
    else:
        formatted_citation = ''

    target_name = fields.get('target_name')

    return ResolvedPublication(
        # Normalized once, here, rather than inside the render loop (point 14).
        authors=_normalize_author_names(
            _text(enrichment.get('pubmed_authors')) or _text(fields.get('authors'))),
        # #728. Stage 4's S4 (book chapter) schema has no `title` key at all:
        # it writes the chapter's title to `chapter_title` and the book's to
        # `book_title`. The renderer read only `title`, so every chapter that
        # reached the deterministic path rendered "Authors. In: Book." and
        # dropped the chapter -- 53 of the 95 farm S4 entries. The other 42
        # carry a `title` written back by stage 5d and take the formatted-
        # citation branch above, so this fall-through cannot double-render:
        # `chapter_title` is read only when there is no title to render.
        #
        # 53 lost it; 49 change. The four that do not: one carries
        # `chapter_title: null`, and three already had a PubMed title, which
        # outranks both extracted names and always did.
        title=(_text(enrichment.get('pubmed_title'))
               or _text(fields.get('title'))
               or _text(fields.get('chapter_title'))),
        journal=_text(enrichment.get('pubmed_journal')) or _text(fields.get('journal')),
        book_title=_text(fields.get('book_title')),
        editors=editors,
        publisher=publisher,
        # Through the same coercer as every other scalar. The renderer this
        # module replaced read `str(fields.get('year', ''))`, which is not a
        # coercion at all: it printed whatever repr the value had. 381 farm
        # entries carry `year: null` and 299 of them rendered the literal
        # text "None" into the citation ("18. None."), and a list or a dict
        # would have rendered its brackets the same way. An absent year and
        # a null year now both render no year.
        year=_scalar_text(fields.get('year')),
        volume=(_scalar_text(enrichment.get('pubmed_volume'))
                or _scalar_text(fields.get('volume'))),
        issue=(_scalar_text(enrichment.get('pubmed_issue'))
               or _scalar_text(fields.get('issue'))),
        pages=(_scalar_text(enrichment.get('pubmed_pages'))
               or _scalar_text(fields.get('pages'))),
        doi=_scalar_text(fields.get('doi')),
        pmid=_scalar_text(fields.get('pmid')),
        pmcid=_scalar_text(fields.get('pmcid')),
        formatted_citation=formatted_citation,
        target_name=target_name if isinstance(target_name, str) else None,
        enriched_fields=tuple(enriched_fields),
    )
