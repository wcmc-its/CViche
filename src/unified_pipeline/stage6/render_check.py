"""Render-check: has this content already reached the rendered output? (#398)

Pure move out of `stage_6_word_template.py` (which re-exports every name here,
so existing callers are unchanged). Two render-time safety nets share these
helpers:

  - `segment_already_rendered` keeps the appendix reconsideration path from
    duplicating a record that DID make it into a table (#209, #221).
  - the unrendered-record recovery pass (#221) uses `_record_lines` /
    `_record_rendered` to find record lines of a fused multi-record entry that
    the structured-fields-only renderers silently dropped.

`normalize_retired_code` lives here because it is the pre-render gate on the
same question: a retired taxonomy code has no render route, so entries keep it
only until this rewrite decides what the renderer will actually see.

run_doctor's lint 8 imports the render-overlap constants and `_entry_pieces`
below from here rather than keeping its own copies (`doctor/shared.py`, #825)
-- the doctor may import the pipeline; no stage module imports the doctor
(quality_score.py imports two doctor leaf modules -- #820, pre-#825 -- and is
not a stage). `doctor/lints/render.py` still keeps its own copies of the
record-line-shape constants further down (UNRENDERED_MIN_RECORD_LINES,
RECORD_DATE_LINE_MIN_CHARS, _RECORD_DATE_PREFIX_RE), and its `_record_lines`/
`_record_rendered` already differ in AST from this module's -- that pair is
#825's ceiling until its second step, not a permanent arrangement; see the
comment above them.
"""
import re

from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.core.retired_taxonomy_codes import RETIRED_TAXONOMY_CODES

from .normalization import _squash


# The retired-code map lives in core so stage 3b and this module share one
# list (#291 added the M4 clinical-trial codes to it).


def normalize_retired_code(entry: dict) -> str:
    """Rewrite a retired taxonomy code on ``entry`` to its live equivalent.

    Mutates ``entry`` IN PLACE when its code is retired: sets
    ``taxonomy_code`` to the live code and preserves the pre-normalization
    code under ``taxonomy_code_original`` (same convention as the #261
    mismatch path). Returns the effective code either way. A non-retired
    code is returned unchanged and the entry is left untouched -- callers
    that need the original entry preserved must copy it first.
    """
    code = entry.get('taxonomy_code', 'T')
    live = RETIRED_TAXONOMY_CODES.get(code)
    if live:
        entry['taxonomy_code_original'] = code
        entry['taxonomy_code'] = live
        return live
    return code


# Fields whose values identify a specific record (vs. generic values like a
# status string shared by many records). Used by segment_already_rendered.
_IDENTIFYING_FIELDS = (
    'title', 'project_title', 'agency', 'award_source',
    'mentee_name', 'organization', 'grant_number',
)

# A title this short can't identify a record on its own ("Professor",
# "Chair") -- see the conjunction rule below, which requires it alongside
# both date endpoints and (when present) the institution.
SHORT_TITLE_MAX_CHARS = 15

# Month words (>=4 alphabetic chars) allowed inside a date-like field value.
_MONTH_WORDS = frozenset((
    'january', 'february', 'march', 'april', 'june', 'july', 'august',
    'september', 'sept', 'october', 'november', 'december',
))


def _value_is_datelike(v: str) -> bool:
    """True when a normalized field value carries no identifying prose —
    only date/number/punctuation content (month names allowed). Date ranges
    are shared across the sibling records of a fused entry, and bare
    alphanumeric codes ('1F30AG032861-01A1') read the same wherever they
    land, so such values must never vouch on their own that a specific
    record rendered (#221 post-review: on corpus CV 2054 entry 66.16 they
    outvoted a genuinely absent grant record line)."""
    return all(word in _MONTH_WORDS for word in re.findall(r'[a-z]{4,}', v))


def segment_already_rendered(segment_text: str, extracted_fields: dict) -> bool:
    """True if an overflow segment duplicates content already rendered from
    this entry's extracted fields — e.g. the first grant of an under-extracted
    multi-record entry, which DID make it into a funding table (#209).

    Matches only identifying fields (title/agency/name), never generic ones
    (a status like "Submitted 2026, Under review" is shared across records
    and would wrongly mark unrendered siblings as duplicates). Within those
    fields, values with no alphabetic word beyond month names (date ranges,
    bare grant numbers) never vouch either — dates are shared across sibling
    records (#221 post-review). A title too
    short to identify a record on its own ("Professor", "Chair") counts only
    together with the record's other anchors: BOTH extracted date endpoints
    (fused career-progression siblings share a boundary date and title
    suffixes — "Associate Professor" contains "Professor" — but not both
    endpoints) plus the extracted institution/organization when there is one.

    ponytail: normalized substring match; upgrade to token-overlap scoring if
    false positives appear.
    """
    if not extracted_fields:
        return False
    seg = re.sub(r'\s+', ' ', segment_text or '').lower()

    def _norm_val(value) -> str:
        if not isinstance(value, str):
            return ''
        return re.sub(r'\s+', ' ', value).lower().strip()

    def _word_in_seg(v: str) -> bool:
        # Word-bounded so 'present' can't match inside 'presentation'.
        return bool(v) and bool(
            re.search(r'(?<!\w)' + re.escape(v) + r'(?!\w)', seg))

    for key in _IDENTIFYING_FIELDS:
        v = _norm_val(extracted_fields.get(key))
        if len(v) >= 15 and v in seg and not _value_is_datelike(v):
            return True

    # Short-title conjunction (#221 review): the extracted record's source
    # line often carries department/descriptor tokens the table render omits,
    # so the recovery token check alone can't recognize it as rendered.
    title = _norm_val(extracted_fields.get('title')
                      or extracted_fields.get('project_title'))
    if not (title and len(title) < SHORT_TITLE_MAX_CHARS and _word_in_seg(title)):
        return False
    dates = [_norm_val(d) for d in (extracted_fields.get('start_date'),
                                    extracted_fields.get('end_date'))]
    if not all(dates) or not all(_word_in_seg(d) for d in dates):
        return False
    org = _norm_val(extracted_fields.get('institution')
                    or extracted_fields.get('organization')
                    or extracted_fields.get('agency'))
    return not org or org in seg


# ---------------------------------------------------------------------------
# Unrendered-record recovery (#221).
#
# The structured-fields-only render paths (positions, licensure, honors,
# committees, presentations, ...) render ONE row/bullet from an entry's
# extracted_fields and silently drop the unextracted remainder record lines of
# a fused multi-record entry. The constants and helpers below mirror
# run_doctor's lint 8 ("unrendered_records") so the offline doctor and this
# render-time safety net agree on what "a record line" and "rendered" mean.
#
# This module IS the shared definition of the render-overlap constants right
# below (#825): `doctor/shared.py` imports them from here rather than keeping
# its own copies. The "run_doctor must not become a pipeline import" rule is
# retired in that direction only -- the doctor may import the pipeline; no
# stage module imports the doctor (quality_score.py imports two doctor leaf
# modules -- #820, pre-#825 -- and is not a stage).

RENDER_TOKEN_MIN_COUNT = 3
RENDER_TOKEN_OVERLAP = 0.7
_RENDER_TOKEN_RE = re.compile(r"[a-z]{5,}")
RENDER_PIECE_MIN_CHARS = 15
RENDER_PIECE_WINDOW = 40

# UNRENDERED_MIN_RECORD_LINES / RECORD_DATE_LINE_MIN_CHARS /
# _RECORD_DATE_PREFIX_RE below, and `_record_lines`/`_record_rendered`
# further down, are still a parallel COPY of `doctor/lints/render.py`'s own,
# kept in sync by name, not by import --
# this is #825's ceiling UNTIL #825's second step, not a permanent
# arrangement: the wider "measurement functions into one module" half of
# that decision lands when #822 needs it.
#
# An entry is a fused multi-record candidate at this many record-like lines.
# _looks_like_record only sees pipe/tab rows; employment/appointment records
# are date-range-prefixed comma lines ("Jun 2020-Jun 2025, Assistant
# Professor"), caught by the prefix pattern when the line carries a payload
# beyond the bare date range.
UNRENDERED_MIN_RECORD_LINES = 2
RECORD_DATE_LINE_MIN_CHARS = 20
# ponytail: deliberately recall-favoring -- a line that merely LOOKS like a
# date-range record (e.g. a sentence that happens to open "1969 - the year
# the department was founded...") can false-positive into _record_lines. No
# corpus-observed instance of that yet; tightening it risks the opposite,
# worse failure (a genuine record line no longer counted, so it's silently
# never re-verified by the recovery pass at all -- see _record_rendered).
# Revisit with corpus evidence before narrowing.
_RECORD_DATE_PREFIX_RE = re.compile(r"^(?:[A-Za-z]{3,9}\.? )?\d{4}\s*[-–—]")


def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def _looks_like_record(line: str) -> bool:
    line = line.strip()
    return len(line) > 60 and (" | " in line or "\t" in line)


# Column-label vocabulary for the no-digit row filter below: a multi-cell row
# with no year/number payload is only header furniture when a majority of its
# words are table labels — dateless multi-cell rows can be real records
# ("Member | Committee on X | Organization Y | description").
_COLUMN_HEADER_WORDS = frozenset({
    'state', 'country', 'license', 'number', 'status', 'date', 'dates',
    'issue', 'issued', 'expiration', 'expires', 'title', 'organization',
    'role', 'committee', 'type', 'location', 'institution', 'certification',
    'name', 'year', 'years', 'description',
})


def _is_column_header_row(line: str) -> bool:
    """True when a majority of the row's words are column-label vocabulary
    ("State/Country  License Number  Status  Date of Issue ...")."""
    words = [w.strip('.,;:()') for w in re.split(r'[\s\t|/]+', _norm(line))]
    words = [w for w in words if w]
    if not words:
        return True
    hits = sum(1 for w in words if w in _COLUMN_HEADER_WORDS)
    return hits / len(words) >= 0.5


def _record_lines(text) -> list[str]:
    """Record-like lines of an entry: pipe/tab rows plus date-range-prefixed
    lines that carry a payload beyond the bare date range."""
    return [line.strip() for line in str(text or "").split("\n")
            if _looks_like_record(line)
            or (len(line.strip()) >= RECORD_DATE_LINE_MIN_CHARS
                and _RECORD_DATE_PREFIX_RE.match(line.strip()))]


def _entry_pieces(text) -> list[str]:
    """Squashed fragments of an entry long enough to be looked up verbatim in
    the rendered-output haystack."""
    pieces = []
    for frag in entry_fragments(text):
        squashed = _squash(frag)
        if len(squashed) >= RENDER_PIECE_MIN_CHARS:
            pieces.append(squashed[:RENDER_PIECE_WINDOW])
    return pieces


def _record_rendered(line: str, haystack: str,
                     line_token_sets: list[set]) -> bool | None:
    """Whether one record line surfaces in the output: verbatim piece first,
    then per-output-line token overlap (per-line, not pooled, so common
    academic words scattered across unrelated sections can't vouch for a
    dropped record). Verbatim absence alone proves nothing — stage 6
    reformats dates/fields — so False requires a token-verifiable miss; a
    line without enough distinctive tokens is None, not missing."""
    if any(piece in haystack for piece in _entry_pieces(line)):
        return True
    rendered = None
    for chunk in [line] + entry_fragments(line):
        tokens = set(_RENDER_TOKEN_RE.findall(_norm(chunk)))
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        if any(len(tokens & line_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP
               for line_tokens in line_token_sets):
            return True
        rendered = False
    return rendered
