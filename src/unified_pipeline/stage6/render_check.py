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
from unified_pipeline.core.template_boilerplate import is_template_label_line
from unified_pipeline.core.text_norm import tab_record_segments

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

# Owner: this module (the shared definition, #825); provenance and any
# re-derivation are tracked at #721. Derivation: hand-set, not measured. Both
# values date from the doctor's first commit (823ef4b7, 2026-07-02), tuned
# against real CVs during the 89HQVQ forensic (#208-#213); #221 copied them here
# unchanged. Labelled evidence since: on the 126-CV re-render of 2026-09-30
# (#717's comment), classified_unrendered fired 25 times -- 10 real losses,
# 3 false warnings cleared by #890, 1 undetermined, and 11 false warnings
# from entries rendered as a reformatted citation or a table row, which the
# 0.7 overlap cannot see. Re-derive against that 10 TP / 11 FP set
# before changing either value.
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
# The output line that vouches for an M1 record must also repeat its wording:
# at least this many of the record's adjacent distinctive-token pairs (two
# consecutive `_RENDER_TOKEN_RE` words) sit adjacent in that line, or all of
# them when the record has fewer (#1572). Token overlap alone lets a
# same-topic row vouch: a project title and a publication on a related
# question, or a short list item and a long summary paragraph, share 70-86%
# of their words in a different order. Measured on the 38 M1 records the
# overlap alone vouched for across 306 corpus runs: the 5 the autopsies
# confirmed lost (#1572: two projects matched by a related publication, three
# isolates matched by the summary or a publication) share 0-2 pairs with
# their vouching line; 32 of the other 33 share 3 or more. The one at 2 is a
# laboratory heading the summary names only by topic, and is now diverted.
M1_RECORD_MIN_SHARED_PAIRS = 3
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


def tab_aware_record_lines(text: object) -> list[str]:
    """`_record_lines`, with a line whose tab parts state two or more dated
    records read as those parts, one record each (`tab_record_segments`).
    Stage 2 fuses the paragraphs of one entry with tabs, and `_record_lines`
    counts the fused line once, so such an entry never reached the recovery
    pass (#1243). Only the recovery pass reads this: dedup weighs an entry by
    `_record_lines`, and splitting there made it drop a fused entry whose
    row the recovery bullets then replaced."""
    lines = []
    for line in str(text or "").split("\n"):
        lines.extend(tab_record_segments(line) or _record_lines(line))
    return lines


def _field_strings(value: object) -> list[str]:
    """Every string inside a field value, objects flattened. A list is left out:
    the list fields of an entry (`additional_roles`) are the ones a section may
    not render (fan-out declined, or a field the renderer does not name), so
    their words prove nothing was written (#1243)."""
    if isinstance(value, dict):
        return [s for item in value.values() for s in _field_strings(item)]
    return [value] if isinstance(value, str) else []


def segment_held_by_fields(segment: str, extracted_fields: dict) -> bool:
    """True when every distinctive word of `segment` sits in a scalar field value.

    A tab part of a one-record entry (a mentee's name and level, then his
    fellowship, then his papers, each part dated) is the record the structured
    row renders cell by cell, so no output line carries its words together and
    the per-line check reads it as missing (#1243: the recovery pass wrote the
    mentee line a second time). A part whose words stage 4 holds in a scalar
    field is the extracted record, never a lost one; a part with a word no
    scalar field holds is still checked, and so is a part held only by a list
    field (`additional_roles` items that stage 6 does not print)."""
    words = set(_RENDER_TOKEN_RE.findall(_norm(segment)))
    return bool(words) and words <= _scalar_field_words(extracted_fields)


def _scalar_field_words(extracted_fields: dict) -> set[str]:
    """The distinctive words of an entry's scalar field values (`_field_strings`)."""
    return set(_RENDER_TOKEN_RE.findall(_norm(" ".join(_field_strings(extracted_fields)))))


# The dates of a short tab part, with any month number: "2009-2011", "1996-97",
# "5/2017-10/2020", "2008". What is left once they go is the part's record.
_PART_DATE_RE = re.compile(
    r"(?:\b\d{1,2}/)?\b(?:19|20)\d{2}\b(?:\s*[-–—]\s*(?:\d{1,2}/)?\d{2,4}\b)?")
# A field label inside a part ("Dates: 1989-1990"): the CV's layout, not its record.
_FIELD_LABEL_RE = re.compile(r"[^\W\d_]+:")
# Words that state a date, not a record: a render may spell a month or an open
# end its own way ("Aug 2001", "2013-Current").
_DATE_WORDS = _MONTH_WORDS | {'present', 'current', 'ongoing'}


def short_tab_part_words(part: str) -> set[str] | None:
    """The distinctive words of a dated tab part, its dates, date words and
    field labels left out, or None when the part cannot be checked on them.

    A tab part with fewer than `RENDER_TOKEN_MIN_COUNT` distinctive words
    ("Secretary, 2009-2011", an officer post under its society's line) is too
    short for `_record_rendered`'s overlap test, so the recovery pass skipped it
    whether or not it rendered (#1243). Its words plus its years identify it
    instead (`short_tab_part_rendered`). None when nothing is left to identify
    it by: no word, a number outside its dates (a head count, a course number,
    a full date with its day), or every letter upper case (a heading with a year).
    """
    letters = "".join(ch for ch in part if ch.isalpha())
    if not letters or letters.isupper():
        return None
    undated = _PART_DATE_RE.sub(" ", _FIELD_LABEL_RE.sub(" ", part))
    if re.search(r"\d", undated):
        return None
    words = set(_RENDER_TOKEN_RE.findall(_norm(undated))) - _DATE_WORDS
    return words or None


def short_tab_part_rendered(part: str, extracted_fields: dict,
                            output_token_sets: list[set]) -> bool | None:
    """Whether a short dated tab part (`short_tab_part_words`) is in the output.

    True when stage 4 holds all its words in a scalar field: it is the
    extracted record, which the structured row renders cell by cell (a mentee's
    name, with "Dates:" and the years in rows of their own). Otherwise True only
    when one output line holds all its words and its years: `output_token_sets`
    are `_record_tokens` of each line, and the years keep siblings apart
    ("Secretary, 2009-2011" is not "Secretary, 2013-2015"). None when the part
    cannot be checked, as in `_record_rendered`.
    """
    words = short_tab_part_words(part)
    if words is None:
        return None
    if words <= _scalar_field_words(extracted_fields):
        return True
    tokens = words | set(_YEAR_RE.findall(part))
    return any(tokens <= line_tokens for line_tokens in output_token_sets)


def _record_token_counts(text: str | None) -> Counter:
    """Letter tokens plus number runs (amounts, years) of a line, lowercased,
    each with the number of times it occurs (#1230)."""
    norm = re.sub(r"(?<=\d)[,.](?=\d)", "", _norm(text))
    return Counter(_RENDER_TOKEN_RE.findall(norm)) + Counter(re.findall(r"\d{2,}", norm))


def _record_tokens(text: str | None) -> set[str]:
    """Letter tokens plus number runs (amounts, years) of a line, lowercased.
    Digits separate sibling records that share their vocabulary (#1230)."""
    return set(_record_token_counts(text))


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


def _line_carried(tokens: set[str], pooled: set[str], has_sibling: bool) -> bool:
    """Whether a source line's `_record_tokens` survive the reply's segments.

    A line with a near-duplicate sibling needs EVERY token in the pooled
    segment tokens: the sibling's segment carries nearly all of them, so the
    pooled share forgives a dropped row that differs from its sibling in one
    name, amount or year (#1230). The row may be split across segments (the
    prompt asks for one segment per responsibility); only a lost token fails.
    Any other line needs `RECLASSIFY_MIN_TOKEN_COVERAGE` of its tokens.
    """
    if has_sibling:
        return tokens <= pooled
    return len(tokens & pooled) / len(tokens) >= RECLASSIFY_MIN_TOKEN_COVERAGE


def segments_cover_source(segments: list[str], source_text: str) -> bool:
    """Whether a reclassify reply's segments carry the source entry's text.

    `segments` is a list of segment strings. Three checks, all must pass:
    pooled distinct-token coverage (`_RENDER_TOKEN_RE` tokens of the source
    found in some segment) of at least `RECLASSIFY_MIN_TOKEN_COVERAGE`; every
    source line that occurs ONCE carried by the segments' letter and number
    tokens (`_line_carried`); and the sibling rows carried by count
    (`_siblings_carried_by_count`). The pooled check catches a reply that
    stands a one-line summary in for a list ("[all 54 entries follow ...]");
    the per-line check catches a reply that keeps most lines and summarises a
    few, which pooled coverage forgives (#209, #1230); the count check catches
    a reply that drops a sibling row whose every token recurs in the rows it
    kept. A line that repeats in the source is skipped: a page-break header the
    reply rightly drops repeats, and a repeated record that matters is carried
    by its first copy. A line that is too short to verify counts as carried. A
    rejected reply is safe: the caller keeps the entry whole. A source with
    fewer than `RENDER_TOKEN_MIN_COUNT` tokens cannot be verified either way
    and is accepted.
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
    all_line_tokens = [_record_tokens(line) for line in counts]
    checked = [tokens for line, tokens in zip(counts, all_line_tokens, strict=True)
               if counts[line] == 1
               and len(set(_RENDER_TOKEN_RE.findall(_norm(line)))) >= RENDER_TOKEN_MIN_COUNT]
    return (all(_line_carried(tokens, pooled, _has_sibling(tokens, all_line_tokens))
                for tokens in checked)
            and _siblings_carried_by_count(checked, all_line_tokens, segments))


def _siblings_carried_by_count(checked: list[set[str]], all_line_tokens: list[set[str]],
                               segments: list[str]) -> bool:
    """Whether the reply holds a copy of each sibling row's tokens per source
    line that holds them (#1230).

    `checked` are the `_record_tokens` of the source lines `segments_cover_source`
    verifies, `all_line_tokens` those of every distinct source line.
    `_line_carried` asks only that a sibling row's tokens appear SOMEWHERE in
    the reply. A row whose every token also sits in other rows passes that
    test with nothing of its own on the page: one committee served in two
    terms and a second committee in the same two terms is a 2x2 grid, and any
    one of the four rows can be dropped. So the rows are counted. The family
    is every checked sibling row plus every checked line it is a sibling of.
    A token of a sibling row that N family lines hold must occur at least N
    times (`_record_token_counts`) in the segments that are a piece of a
    family line or hold one whole: a segment from an unrelated line cannot
    pay for a dropped row with a shared word ("chair", "committee"). Rows
    split across segments or merged into one keep their counts; a reply that
    writes two rows as one ("member, 2010-2012 and 2014-2016") does not, and
    is rejected like a dropped row, which leaves the entry whole.
    """
    family: list[set[str]] = []
    sibling_tokens: set[str] = set()
    for index, tokens in enumerate(checked):
        if not _has_sibling(tokens, all_line_tokens):
            continue
        sibling_tokens |= tokens
        family += [other for other_index, other in enumerate(checked)
                   if other_index == index or _is_sibling(tokens, other)]
    rows = list({id(row): row for row in family}.values())
    if not rows:
        return True
    reply_counts = sum((_record_token_counts(segment) for segment in segments
                        if any(_record_tokens(segment) <= row or row <= _record_tokens(segment)
                               for row in rows)), Counter())
    needed = Counter(token for row in rows for token in row & sibling_tokens)
    return all(reply_counts[token] >= n for token, n in needed.items())


def _is_sibling(tokens: set[str], other: set[str]) -> bool:
    """Whether `other` holds `RECLASSIFY_MIN_TOKEN_COVERAGE` of `tokens`
    while differing from them: a sibling row (#1230)."""
    return (bool(tokens) and other != tokens
            and len(tokens & other) / len(tokens) >= RECLASSIFY_MIN_TOKEN_COVERAGE)


def _siblings(tokens: set[str], all_line_tokens: list[set[str]]) -> list[set[str]]:
    """The other source lines that hold `RECLASSIFY_MIN_TOKEN_COVERAGE` of
    `tokens` while differing from them: its sibling rows (#1230)."""
    return [other for other in all_line_tokens if _is_sibling(tokens, other)]


def _has_sibling(tokens: set[str], all_line_tokens: list[set[str]]) -> bool:
    """Whether `tokens`, one of `all_line_tokens`, has a sibling row among
    them (#1230) or a twin: another distinct line with the same tokens.
    `_record_tokens` keeps no word under five letters, so "Committee A" and
    "Committee B", or "NYP" and "WCM", give two rows one token set, and a
    reply that dropped either was accepted (#1956)."""
    return (bool(_siblings(tokens, all_line_tokens))
            or all_line_tokens.count(tokens) > 1)


def _sibling_record_tokens(text: str | None) -> set[str]:
    """`_record_tokens` without month names (#1230). A render may write
    "March 2015" as "03/2015", so a month name cannot tell a row apart: rows
    that differ only in one are not siblings, and keep `_record_rendered`'s
    verdict."""
    return _record_tokens(text) - _MONTH_WORDS


# Words that end an open term ("2014-Present"): a term's date like a year
# number, not a cell of the row (#1230). "now" and "to date" need no entry:
# `_RENDER_TOKEN_RE` takes no word under five letters.
_OPEN_TERM_WORDS = frozenset(('present', 'current', 'ongoing'))


def _number_sibling(tokens: set[str], other: set[str]) -> bool:
    """Whether `other` has exactly the letter tokens of `tokens`, so it can
    differ only in its number tokens and open-term words (`_OPEN_TERM_WORDS`):
    the same row in another term (#1230). Such a pair can share less than
    `RECLASSIFY_MIN_TOKEN_COVERAGE` of its tokens ("Member | Admissions
    Committee | <college> | 2010-2012" and its 2014-2016 or 2014-Present row
    share 7 of 9)."""
    def cells(row: set[str]) -> set[str]:
        return {token for token in row if not token.isdigit()} - _OPEN_TERM_WORDS
    return cells(tokens) == cells(other)


def record_siblings(records: list[str]) -> dict[str, list[set[str]]]:
    """Each record line of one entry that has sibling rows in it, mapped to
    those siblings' `_sibling_record_tokens` (#1230). A sibling is a line that
    holds `RECLASSIFY_MIN_TOKEN_COVERAGE` of the line's tokens, or one that
    differs from it only in numbers (`_number_sibling`). A sibling that holds
    every token of the line is left out: the line is part of it, so its
    render carries the line."""
    tokens = {line: _sibling_record_tokens(line) for line in dict.fromkeys(records)}
    all_tokens = list(tokens.values())
    siblings = {line: [other for other in all_tokens
                       if (_is_sibling(own, other) or _number_sibling(own, other))
                       and not own <= other]
                for line, own in tokens.items()}
    return {line: others for line, others in siblings.items() if others}


def record_twins(records: list[str]) -> dict[str, list[str]]:
    """Each record line of one entry mapped to the `_squash`ed text of its
    twins: the other lines with the same `_sibling_record_tokens` (#1956).

    `record_siblings` leaves twins out: no token tells them apart, since
    `_record_tokens` keeps no word under five letters ("Committee A" and
    "Committee B", "NYP" and "WCM"). Their text does, so
    `sibling_record_rendered` compares it. Lines with the same squashed text
    are the same record, not twins."""
    lines = list(dict.fromkeys(records))
    tokens = {line: _sibling_record_tokens(line) for line in lines}
    twins = {line: list(dict.fromkeys(
                 _squash(other) for other in lines
                 if tokens[other] == tokens[line]
                 and _squash(other) != _squash(line)))
             for line in lines}
    return {line: others for line, others in twins.items() if others}


def _only_twins_vouch(line: str, twins: list[str], haystack: str) -> bool:
    """Whether every `haystack` line that vouches for `line` holds one of its
    `twins` (`record_twins`) verbatim: the line's words are on the page only
    as a twin's row (#1956).

    A haystack line vouches as in `_record_rendered`: it holds one of the
    line's `record_pieces`, or `RENDER_TOKEN_OVERLAP` of one chunk's letter
    tokens, looked up in its squashed text. A vouching line that holds no
    twin may be the line itself, reformatted, so the verdict stands; so it
    does when no haystack line vouches (a recovered tab part did)."""
    pieces = record_pieces(line)
    chunks = [chunk for chunk in (set(_RENDER_TOKEN_RE.findall(_norm(text)))
                                  for text in record_chunks(line))
              if len(chunk) >= RENDER_TOKEN_MIN_COUNT]
    vouching = [out for out in haystack.split("\x00")
                if any(piece in out for piece in pieces)
                or any(sum(token in out for token in chunk) / len(chunk)
                       >= RENDER_TOKEN_OVERLAP for chunk in chunks)]
    return bool(vouching) and all(any(twin in out for twin in twins) for out in vouching)


def sibling_record_rendered(line: str, siblings: list[set[str]] | None, haystack: str,
                            line_token_sets: list[set],
                            record_token_sets: list[set],
                            twins: list[str] | None = None) -> bool | None:
    """`_record_rendered` for a record line of an entry that holds its sibling
    rows (`record_siblings`), counted one output line per row (#1230).

    `_record_rendered` lets an output line vouch for the line through one
    verbatim piece (`record_pieces`) or `RENDER_TOKEN_OVERLAP` of one chunk's
    letter tokens (`record_chunks`), and ignores numbers. One committee in two
    terms renders as one row, and that row then vouches for the other term
    too. Here a `_record_rendered` True is turned to False only when the
    line is not in the output verbatim, no output line that vouches for it
    the same way (a squashed `haystack` line holding one of its pieces, or a
    `record_token_sets` line holding that overlap of one of its chunks) holds,
    against each sibling, a letter or number token the sibling lacks
    (`_holds_own_token`), and one of those vouching lines does hold a token
    of a sibling's own: it is positively that sibling's row. For the same
    row in another term, those tokens are the rows' own numbers. Measuring
    the vouching as `_record_rendered` does keeps the verdict for a render
    that writes only some cells of each row on its line ("Admissions
    Committee | Member | 2010-2012"); a render that drops the numbers of
    every term leaves no line positively a sibling's, and keeps it too.
    `line_token_sets` are the output lines' letter tokens, for
    `_record_rendered`; `record_token_sets` are `_record_tokens` of the
    output lines that may vouch here (the recovery pass passes its uncut
    lines plus the lines it has recovered so far). A line with no siblings
    and no twins gets `_record_rendered` unchanged, and this never turns a
    False or None into True.

    `twins` (`record_twins`) are rows no token tells apart from the line: a
    True turns False when every output line that vouches for the line is a
    twin's row verbatim (`_only_twins_vouch`, #1956).
    """
    rendered = _record_rendered(line, haystack, line_token_sets)
    if not rendered or not (siblings or twins) or _squash(line) in haystack:
        return rendered
    if twins and _only_twins_vouch(line, twins, haystack):
        return False
    if not siblings:
        return rendered
    tokens = _sibling_record_tokens(line)
    own = [tokens - sibling for sibling in siblings]
    pieces = record_pieces(line)
    chunks = [chunk for chunk in (set(_RENDER_TOKEN_RE.findall(_norm(text)))
                                  for text in record_chunks(line))
              if len(chunk) >= RENDER_TOKEN_MIN_COUNT]
    vouching = ([(out, True) for out in haystack.split("\x00")
                 if any(piece in out for piece in pieces)]
                + [(out, False) for out in record_token_sets
                   if any(len(chunk & out) / len(chunk) >= RENDER_TOKEN_OVERLAP
                          for chunk in chunks)])
    if any(_holds_own_token(own, out, squashed) for out, squashed in vouching):
        return True
    # No vouching line is a sibling's row either (a render that dropped the
    # years of every term): nothing tells the rows apart, keep the verdict.
    return not any(_holds_own_token([sibling - tokens], out, squashed, whole=True)
                   for sibling in siblings for out, squashed in vouching)


_CENTURY_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _holds_own_token(own: list[set[str]], out: str | set[str], squashed: bool = False,
                     whole: bool = False) -> bool:
    """Whether output line `out` holds a token of each set in `own`: the
    tokens a record line has and one of its siblings lacks, or the reverse
    (#1230). `out` is
    a `_squash`ed line when `squashed`, else its `_record_tokens`. A year
    also counts by its last two digits standing alone ("2012" in "2010-12" or
    "'10-'12"), so a render that shortens a range keeps `_record_rendered`'s
    verdict; with `whole`, only the token itself counts, and in a squashed
    line a number counts only standing alone."""
    def held(token: str) -> bool:
        short = None if whole or not _CENTURY_YEAR_RE.fullmatch(token) else token[2:]
        if not squashed:
            return token in out or short in out
        if whole and token.isdigit():
            return bool(re.search(rf"(?<!\d){token}(?!\d)", out))
        return token in out or bool(short and re.search(rf"(?<!\d){short}(?!\d)", out))
    return all(any(held(token) for token in distinct) for distinct in own)


def _entry_pieces(text) -> list[str]:
    """Squashed fragments of an entry long enough to be looked up verbatim in
    the rendered-output haystack."""
    pieces = []
    for frag in entry_fragments(text):
        squashed = _squash(frag)
        if len(squashed) >= RENDER_PIECE_MIN_CHARS:
            pieces.append(squashed[:RENDER_PIECE_WINDOW])
    return pieces


def record_pieces(line: str) -> list[str]:
    """`_entry_pieces` of a record line, minus every template-label fragment
    (`is_template_label_line`): the verbatim side of `_record_rendered`.

    The label cell of a "Goals/expected Outcomes | <goal>" row sits in the
    output wherever any record's label does, so it vouched for every record
    carrying that label. YDSVDB: one stray "Goals/expected Outcomes" fragment
    hid the unrendered mentee goals from the doctor (#829). Scoped to record
    lines: `_entry_pieces` itself also feeds the doctor's whole-entry checks,
    which judge a template piece on their own (#744).
    """
    return [piece for frag in entry_fragments(line)
            if not is_template_label_line(frag) for piece in _entry_pieces(frag)]


def record_chunks(line: str) -> list[str]:
    """The texts `_record_rendered` scores by token overlap: the whole line,
    then each of its fragments. A line holding a template-label fragment is
    scored on its other fragments alone.

    The `record_pieces` rule, on the token side: once its verbatim piece no
    longer vouched, the bare "Goals/expected Outcomes" cell still carried 3
    distinctive tokens, enough to match the one stray output line that held the
    label, so the YDSVDB goals still read as rendered (#829). The whole line
    goes too: with the label in it, "Type of Supervision (Research, clinical,
    teaching, leadership) | Leadership" scores 5 label tokens to the answer's
    1 and matches any line that repeats the label; with the label out, its
    answers belong to different labels, which a form renders as different rows
    ("Name of Principal Investigator: | <name> | Your role* | <role>"), so no
    one output line holds them all.
    """
    fragments = entry_fragments(line)
    kept = [frag for frag in fragments if not is_template_label_line(frag)]
    if len(kept) == len(fragments):
        return [line] + fragments
    return kept


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


def token_pairs(text: str) -> set[tuple[str, str]]:
    """Adjacent pairs of a text's distinctive letter tokens, in reading order:
    the wording `m1_record_rendered` asks a vouching line to repeat."""
    tokens = _RENDER_TOKEN_RE.findall(_norm(text))
    return set(zip(tokens, tokens[1:], strict=False))


def m1_record_rendered(text: str, haystack: str, line_token_sets: list[set],
                       line_pair_sets: list[set]) -> bool | None:
    """Whether a dated M1 record surfaces on the page as a WHOLE (#1429).

    `_record_rendered` lets any one fragment vouch for a line. An M1 record's
    leading "2015 to 2017 Department of Psychiatry, <university>" fragment
    matches an appointment or teaching row, its "Principal Investigator: <name>"
    fragment matches a grant table, and its project title alone matches a long
    research-summary paragraph that only shares its topic words (NDMRSO: 24
    records on 4 CVs lost that way). Here the record is judged as one unit:
    its squashed text sits in the output verbatim, or ONE output line holds
    `RENDER_TOKEN_OVERLAP` of ALL its distinctive letter tokens AND repeats
    `M1_RECORD_MIN_SHARED_PAIRS` of its adjacent token pairs (#1572: overlap
    alone let a same-topic publication or summary vouch for a different
    study). `line_token_sets` and `line_pair_sets` are those tokens and
    `token_pairs` of each output line, index-aligned. A record with too few
    distinctive tokens is None, as in `_record_rendered`.
    """
    if _squash(text) in haystack:
        return True
    tokens = set(_RENDER_TOKEN_RE.findall(_norm(text)))
    if len(tokens) < RENDER_TOKEN_MIN_COUNT:
        return None
    pairs = token_pairs(text)
    min_shared_pairs = min(M1_RECORD_MIN_SHARED_PAIRS, len(pairs))
    return any(len(tokens & line_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP
               and len(pairs & line_pairs) >= min_shared_pairs
               for line_tokens, line_pairs in zip(line_token_sets, line_pair_sets, strict=True))


def _record_rendered(line: str, haystack: str,
                     line_token_sets: list[set]) -> bool | None:
    """Whether one record line surfaces in the output: verbatim piece first,
    then per-output-line token overlap (per-line, not pooled, so common
    academic words scattered across unrelated sections can't vouch for a
    dropped record). Verbatim absence alone proves nothing — stage 6
    reformats dates/fields — so False requires a token-verifiable miss; a
    line without enough distinctive tokens is None, not missing."""
    if any(piece in haystack for piece in record_pieces(line)):
        return True
    rendered = None
    for chunk in record_chunks(line):
        tokens = set(_RENDER_TOKEN_RE.findall(_norm(chunk)))
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        if any(len(tokens & line_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP
               for line_tokens in line_token_sets):
            return True
        rendered = False
    return rendered
