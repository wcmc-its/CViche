"""Normalization: canonicalising an extracted value before it is rendered.

One responsibility: value in, cleaner value out. Nothing here touches a
python-docx object, decides which WCM section a record belongs to, or reads
pipeline state -- that separation is what makes these safe to reuse and trivial
to test.

The distinction from `formatting` is deliberate and worth keeping. Formatting
decides how a value *looks* in Word (font, borders, spacing); normalization
decides what the value *is* -- which author-name spelling, which institution
string, whether a repeated phrase is dropped. A change to one should not require
a change to the other.

    authors.py       an author list in, one citation spelling out
    institutions.py  an institution or award-organization name in, the
                     duplicated organization removed out
    content.py       a value a merged cell repeated in, one copy out
    rendering.py     a value carrying stage-5c markdown or the readers' cell
                     separators in, Word-ready text out
    taxonomy.py      a bullet with a leaked stage-3b code on the front in, the
                     bullet out
    pii.py           a value and the entry it came from in, whether the value is
                     protected personal data out
    fields.py        a stage-4 field of unpredictable *shape* in, plain text out
    records.py       a record filed wrongly in, the correction out
    citation_matching.py
                     does this citation's text already say that value?
    publication.py   a raw publication entry in, one resolved record out

The first six were one module, `text.py`, until they were split apart. "Value
in, cleaner value out" is a shared signature, not a shared reason to change: an
author-name variant, a new cell separator, a leaked taxonomy code and a new
protected-data label are four independent events, and one file meant any of them
could be edited into any of the others. They are separate modules so that
ownership, blast radius and the tests that pin them line up with the domain.
`pii.py` is the one that most needed it -- it decides whether a value may be
rendered at all, which is a data-governance rule, not a cleanup. The private
`_`-prefixed names are unchanged throughout: renaming and relocating in one
change would make a failure impossible to attribute to either.

`fields.py` is separate because its input is not text yet. Stage 4 stores raw
LLM JSON against no schema, so a field the renderer expects to be a string can
arrive as a dict or a list -- and `cell.text = <dict>` aborts the whole document
(#442, #450). Until a schema layer exists between stage 4 and stage 6, that
absence is absorbed there and nowhere else.

`publication.py` is that same absence absorbed once for the bibliography, and it
is why `formatting/values.py` no longer names a single pipeline key: an entry is
resolved here -- guards, enrichment precedence, non-text coercion, author
normalization, stage-5d reconciliation -- and the renderer receives a record
whose every field is already the text it will print.

Dependencies run one way, inside this package and out of it. `publication`
imports `citation_matching` and `authors`; nothing imports `publication`. Nothing
here imports `formatting/`, `sections/`, or `stage_6_word_template` -- those
import this, so a back-edge would be an import cycle that fails at load rather
than at render.
"""

from .authors import (  # noqa: F401
    _normalize_author_names,
)
from .citation_matching import (  # noqa: F401
    _CITATION_MATCH_MIN_RATIO,
    _CITATION_STOPWORDS,
    _CITATION_TOKEN_MIN_LEN,
    _CITATION_TOKEN_RE,
    _append_missing_stage5d_values,
    _value_referenced,
)
from .content import (  # noqa: F401
    _deduplicate_repeated_content,
)
from .fields import (  # noqa: F401
    _CELL_PHONE_KEYS,
    _OFFICE_PHONE_KEYS,
    _HOME_PHONE_KEYS,
    _ALL_PHONE_SLOT_KEYS,
    _labels_its_own_phone_slots,
    _phone_cell_text,
    _HOME_ADDRESS_KEYS,
    _OFFICE_ADDRESS_KEYS,
    _address_cell_text,
    _committee_cell_text,
    _labels_its_own_address_slots,
)
from .institutions import (  # noqa: F401
    _get_cleaned_institution_name,
    _strip_org_tail,
)
from .pii import (  # noqa: F401
    _PII_LABEL_RE,
    _PII_FIELD_KEY_RE,
    _PII_FRAGMENT_SPLIT_RE,
    _squash,
    _pii_fragments,
    _from_pii_fragment,
)
from .publication import (  # noqa: F401
    PubMedEnrichment,
    PublicationFields,
    ResolvedPublication,
    resolve_publication,
)
from .records import (  # noqa: F401
    grant_status_rebucket_target,
    split_fused_citation_entries,
)
from .rendering import (  # noqa: F401
    _clean_inline_tabs,
    _strip_markdown_for_word,
)
from .taxonomy import (  # noqa: F401
    _TAXONOMY_CODE_PREFIX,
    _strip_taxonomy_code,
)
