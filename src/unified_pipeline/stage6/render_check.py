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
from collections import Counter

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
# A reclassify reply must carry at least this share of its source entry's
# distinct tokens, pooled across segments, to be believed (#1230). Measured on
# the three stored replies in the IPXFBA batch: a reply that stood a bracketed
# one-line summary in for 54 publications covered 0.44 of the source; two
# faithful splits covered 0.94 and 0.98.
RECLASSIFY_MIN_TOKEN_COVERAGE = 0.8
# A T-coded record line counts as rendered only when ALL its letter and number
# tokens sit in ONE output line (#1230). Sibling rows share their words and
# differ in an amount, a year or a single name (six committee rows that differ
# only in the committee name); any partial-overlap threshold lets the complete
# row on the page vouch for its siblings. A row reformatted by the render that
# drops a token is covered instead by the entry-line check in
# `_recover_unrendered_records`.
T_RECORD_OVERLAP = 1.0
# Unicode letters only (no digits/underscore): [a-z]{5,} on ASCII input (#541).
#
# CJK is EXCLUDED, not measured (#722). The 5-letter floor and
# RENDER_TOKEN_MIN_COUNT were never tuned against CJK text (the local farm has
# 0 CJK CVs). Chinese and Japanese have no whitespace word delimiters, so a
# run of their letters is a clause, not a word. Korean does use spaces, but
# its words are counted in syllables and rarely reach 5; the floor was never
# tuned for that unit either, so Korean is excluded with the rest of CJK.
# CJK characters therefore never form a token: a chunk that is all CJK has
# too few tokens, so every render-overlap check returns None ("not
# verifiable"), never False ("missing"). Latin/Cyrillic/Greek words inside a
# mixed-script chunk still count. A script-aware floor needs real CJK data.
#
# Coverage is pinned by a walk over every code point: each letter whose
# Unicode name is CJK/Hiragana/Katakana/Hentaigana/Hangul (incl. halfwidth)
# is excluded, and no other letter is. Known gap, NOT excluded: Bopomofo
# (U+3100-312F, U+31A0-31BF).
_CJK_CLASS = (
    "\u1100-\u11ff"          # Hangul Jamo
    "\u3040-\u30ff"          # Hiragana, Katakana
    "\u3130-\u318f"          # Hangul compatibility Jamo
    "\u31f0-\u31ff"          # Katakana phonetic extensions
    "\u3400-\u4dbf"          # CJK extension A
    "\u4e00-\u9fff"          # CJK unified ideographs
    "\ua960-\ua97f"          # Hangul Jamo extended-A
    "\uac00-\ud7ff"          # Hangul syllables, Jamo extended-B
    "\uf900-\ufaff"          # CJK compatibility ideographs
    "\uff66-\uff9f"          # Halfwidth Katakana
    "\uffa0-\uffdc"          # Halfwidth Hangul
    "\U0001aff0-\U0001b16f"  # Kana extended-B, supplement, extended-A, small
    "\U00020000-\U0003ffff"  # CJK extensions B and later
)
_RENDER_TOKEN_RE = re.compile(rf"(?:(?![{_CJK_CLASS}])[^\W\d_]){{5,}}")
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


def _record_tokens(text: str | None) -> set[str]:
    """Letter tokens plus number runs (amounts, years) of a line, lowercased.
    Digits separate sibling records that share their vocabulary (#1230)."""
    norm = re.sub(r"(?<=\d)[,.](?=\d)", "", _norm(text))
    return set(_RENDER_TOKEN_RE.findall(norm)) | set(re.findall(r"\d{2,}", norm))


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _collapse_repeated_cells(line: str) -> str:
    """A pipe row's distinct non-empty cells in order, joined by " | ".

    A merged table cell is flattened once per grid column it spans, so one
    description reads "text | text | text | text" (#1230). The collapsed row is
    the record's real content; a row with no repeated cell is returned as is.
    """
    cells = [cell.strip() for cell in line.split("|") if cell.strip()]
    distinct = list(dict.fromkeys(cells))
    return line if len(distinct) == len(cells) else " | ".join(distinct)


def _is_record_or_dated(line: str) -> bool:
    return bool(_looks_like_record(line) or _RECORD_DATE_PREFIX_RE.match(line)
                or _YEAR_RE.search(line))


def t_recovery_lines(text: str | None) -> list[str]:
    """Lines of a long T entry the recovery pass should look for (#1230).

    Every non-blank line that is record-shaped (`_looks_like_record`, or a
    date-range prefix) or carries a year, plus the one line directly after such
    a record when it has neither (a grant's description row follows its row).
    Merged-cell repeats are collapsed (`_collapse_repeated_cells`). A line with
    no year and no row shape that does not follow a record (an objective
    statement, a template instruction, an abbreviation key) is not a record and
    is not looked for: recovering it re-inserts template text as often as it
    saves content. The Appendix keeps it: a T body renders there whole unless
    the low-coverage overflow re-splits the entry.
    """
    lines = [_collapse_repeated_cells(line.strip())
             for line in str(text or "").split("\n") if line.strip()]
    kept = []
    after_record = False
    for line in lines:
        is_record = _is_record_or_dated(line)
        if is_record or after_record:
            kept.append(line)
        after_record = is_record
    return kept


def _line_carried(line: str, carried: set[str]) -> bool:
    """Whether `RECLASSIFY_MIN_TOKEN_COVERAGE` of a line's letter and number
    tokens are in `carried`. A line too short to verify counts as carried."""
    if len(set(_RENDER_TOKEN_RE.findall(_norm(line)))) < RENDER_TOKEN_MIN_COUNT:
        return True
    tokens = _record_tokens(line)
    return len(tokens & carried) / len(tokens) >= RECLASSIFY_MIN_TOKEN_COVERAGE


def segments_cover_source(segments: list[str], source_text: str) -> bool:
    """Whether a reclassify reply's segments carry the source entry's text.

    `segments` is a list of segment strings. Two checks, both must pass:
    pooled distinct-token coverage (`_RENDER_TOKEN_RE` tokens of the source
    found in some segment) of at least `RECLASSIFY_MIN_TOKEN_COVERAGE`, and
    every source line that occurs ONCE carried at that same share by the
    segments' pooled letter and number tokens. The pooled check catches a reply
    that stands a one-line summary in for a list ("[all 54 entries follow
    ...]"); the per-line check catches a reply that keeps most lines and
    summarises a few, which pooled coverage forgives (#209, #1230). A line that
    repeats in the source is skipped: a page-break header the reply rightly
    drops repeats, and a repeated record that matters is carried by its first
    copy. A rejected reply is safe: the caller keeps the entry whole. A source
    with fewer than `RENDER_TOKEN_MIN_COUNT` tokens cannot be verified either
    way and is accepted.
    """
    source_tokens = set(_RENDER_TOKEN_RE.findall(_norm(source_text)))
    if len(source_tokens) < RENDER_TOKEN_MIN_COUNT:
        return True
    carried = set()
    for segment in segments:
        carried.update(_RENDER_TOKEN_RE.findall(_norm(segment)))
    if len(source_tokens & carried) / len(source_tokens) < RECLASSIFY_MIN_TOKEN_COVERAGE:
        return False
    pooled = set().union(*(_record_tokens(segment) for segment in segments))
    lines = [line.strip() for line in str(source_text or "").split("\n") if line.strip()]
    counts = Counter(lines)
    return all(_line_carried(line, pooled) for line in lines if counts[line] == 1)


def _entry_pieces(text) -> list[str]:
    """Squashed fragments of an entry long enough to be looked up verbatim in
    the rendered-output haystack."""
    pieces = []
    for frag in entry_fragments(text):
        squashed = _squash(frag)
        if len(squashed) >= RENDER_PIECE_MIN_CHARS:
            pieces.append(squashed[:RENDER_PIECE_WINDOW])
    return pieces


def _whole_record_rendered(line: str, haystack: str,
                           line_token_sets: list[set],
                           cut_token_sets: list[set] | None = None) -> bool | None:
    """`_record_rendered` judged on the WHOLE line, for T-coded entries (#1230).

    `_record_rendered` lets any one verbatim cell piece vouch for the line, so
    a grant row whose title cell renders elsewhere reads as rendered while its
    funder, role and amount cells appear nowhere. A T entry reaches the
    Appendix capped, so that leniency loses the cells. Here the squashed line
    must sit in the output verbatim, or share `T_RECORD_OVERLAP` of its letter
    and number tokens with ONE output line (a rendered row is emitted joined,
    so a structured row still matches; numbers keep sibling rows that differ
    only in amount or years apart). `line_token_sets` are `_record_tokens` of
    each output line. `cut_token_sets` are those of output lines the Appendix
    cap cut: they only vouch for a line whose tokens ALL sit in them, since a
    cut line may have lost the tail of the record. A line with too few distinctive letter tokens is None,
    as in `_record_rendered`.
    """
    if _squash(line) in haystack:
        return True
    if len(set(_RENDER_TOKEN_RE.findall(_norm(line)))) < RENDER_TOKEN_MIN_COUNT:
        return None
    tokens = _record_tokens(line)
    return (any(len(tokens & line_tokens) / len(tokens) >= T_RECORD_OVERLAP
                for line_tokens in line_token_sets)
            or any(tokens <= cut for cut in cut_token_sets or ()))


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
