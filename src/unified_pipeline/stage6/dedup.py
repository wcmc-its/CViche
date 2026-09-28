"""Near-duplicate removal within a taxonomy-code group (#398).

Pure move out of `stage_6_word_template.py` (which re-exports every name here,
so existing callers are unchanged). `deduplicate_entries` is called once per
code group before rendering; `_drop_is_safe` is the #227 guard that only lets
a drop through when the loss is provably recoverable. The docstrings and the
constants' comments carry the accuracy history (#227, #208, C0ZGFW, 2Q1_ZQ)
and are the spec.

Depends one level down on `render_check` (`_record_lines` and its
fused-multi-record threshold, plus `_IDENTIFYING_FIELDS`/`_value_is_datelike`
for `recovered_row_content_rendered`'s parent-scoping below) because "is this
a fused blob" and "is this value substantial enough to anchor a search" are
both questions `segment_already_rendered` already answers the same way.
Nothing here may import `stage_6_word_template`.
"""
import re

from .normalization import _squash
from .parsing import _dates_overlap_or_match
from .render_check import (
    UNRENDERED_MIN_RECORD_LINES,
    _IDENTIFYING_FIELDS,
    _record_lines,
    _value_is_datelike,
)

# A table row's cell separator, as this codebase's raw-text extraction
# renders it -- " | " in the convention `recover_unclaimed_table_rows`
# itself uses for a recovered row's own text, but a tab where a multi-cell
# line is instead built by joining `full_text_parts` with "\t"
# (stage_2_entry_extraction.py). `recovered_row_duplicates_parent` strips
# both before comparing so the SAME cell boundary matches regardless of
# which convention produced it (A5IZ6Q: "Your percent (%) effort: | 1%" vs
# the parent's own "...effort:\t1%" for the identical source cell).
_CELL_SEPARATOR_RE = re.compile(r'[|\t]')


_STOP_WORDS = frozenset({
    'a', 'an', 'and', 'as', 'at', 'be', 'by', 'for', 'from', 'i', 'in',
    'is', 'it', 'of', 'on', 'or', 'the', 'to', 'was', 'with',
})


def _significant_words(text: str) -> set:
    """Extract significant words from text, stripping stop words and punctuation."""
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _entry_signature_words(entry: dict) -> set:
    """Extract significant words from an entry's full text."""
    return _significant_words(entry.get('text') or '')


def _entry_title_words(entry: dict) -> set:
    """Extract significant words from the title/activity portion of an entry.

    Tries multiple strategies to isolate the meaningful title:
    1. Text before first tab (structured entries)
    2. Quoted text (presentation titles often in quotes)
    3. extracted_fields 'title' or 'activity_title'
    4. Fallback to first 100 chars
    """
    text = (entry.get('text') or '')
    if '\t' in text:
        title = text.split('\t')[0]
    else:
        # Try to find quoted title (common for presentations)
        quoted = re.findall(r'["\u201c](.+?)["\u201d]', text)
        if quoted:
            title = ' '.join(quoted)
        else:
            # Try extracted fields
            fields = entry.get('extracted_fields', {}) or {}
            title = (fields.get('title') or fields.get('activity_title') or
                     fields.get('presentation_title') or '')
            if not title:
                title = text[:100]
    return _significant_words(title)


# _drop_is_safe: a reworded true duplicate ("Associate Professor, HPE, USUHS"
# inside "...Department of Health Professions Education (HPE) Uniformed
# Services University...") has EVERY significant word contained in the kept
# entry — but so does a 3-token degree line whose distinguishing token the
# tokenizer destroyed ('M.S' vs 'PhD', the 2Q1_ZQ B1 loss). Full containment
# only proves duplication when the dropped entry carries enough tokens.
DEDUP_FULL_CONTAINMENT_MIN_TOKENS = 5

# _drop_is_safe token-containment is a TRUE-DUPLICATE signal only when the kept
# entry is itself a single record. When the kept entry is a FUSED multi-record
# blob (a whole layout table captured atomically, #208), a distinct single
# record is fully token-contained in it merely because the blob swallowed it —
# dropping it is real content loss, not deduplication (C0ZGFW: 35 invited
# presentations + 3 teaching records dropped into "Title/Institution/Dates"
# table blobs of 52 and 13 record-lines). A blob this size is the fusion bug,
# not a duplicate. ponytail: gate on record-line count; the source fix is
# de-fusing the table in stage 2 (#208/#248).
DEDUP_FUSED_BLOB_RECORD_LINES = 5


def _drop_is_safe(dropped_entry: dict, kept_entry: dict) -> bool:
    """#227 guard: only drop an entry when the loss is provably recoverable.

    Safe when the dropped text is verbatim-contained in the kept entry, or
    every significant word of a token-rich dropped entry appears in the kept
    entry (both are true-duplicate shapes) AND the kept entry is not a fused
    multi-record blob, or the dropped entry is a fused multi-record candidate —
    those the #221/#225 recovery pass (`WCMTemplateGenerator._recover_unrendered_records`,
    `stage_6_word_template.py`, called on the PRE-dedup snapshot so a dropped
    entry's lines are still checked) re-verifies line by line against the
    rendered document. A single-line entry that merely SCORES similar is the
    #227 loss class: distinct records sharing role/date/venue boilerplate (7 of
    8 drops on 2Q1_ZQ were real content loss, all single-line); a distinct
    record swallowed by a fused table blob is the same loss class (C0ZGFW).

    #666 (adversarially re-checked, not yet fixed): all three branches below
    can approve an unsafe drop for realistic prose CV entries, not just the
    final fallback. The shared root cause is _record_lines()'s narrow shape
    (pipe/tab row, or a line-initial date-range prefix) -- an ordinary
    single-paragraph entry (a mentee mention, a committee-succession
    sentence) has ZERO record lines, so _recover_unrendered_records skips it
    entirely and the "#221/#225 will catch it" assumption below never
    engages for that entry at all, regardless of which branch dropped it.
    Confirmed with repros: two distinct mentees fused into one un-split
    entry defeats the verbatim-containment branch (one mentee's text is a
    literal substring of the fused pair's text); two distinct multi-year
    committee/board memberships defeat the subset-containment branch when
    one entry's narrative prose mentions the other's identifying nouns and
    years in passing (successor-committee framing). The final fallback has
    its own additional, narrower hole: the recovery pass's own token-overlap
    check (`_RENDER_TOKEN_RE`) is digit-blind, so even a fused entry that
    DOES clear the record-line threshold can still evade recovery if two
    records differ only by date. Corpus-verified fix needed before any of
    this changes; see the issue for candidate directions."""
    dropped_squashed = _squash(dropped_entry.get('text', ''))
    if dropped_squashed and dropped_squashed in _squash(kept_entry.get('text', '')):
        return True
    dropped_sig = _entry_signature_words(dropped_entry)
    if (len(dropped_sig) >= DEDUP_FULL_CONTAINMENT_MIN_TOKENS
            and dropped_sig <= _entry_signature_words(kept_entry)
            and len(_record_lines(kept_entry.get('text', ''))) < DEDUP_FUSED_BLOB_RECORD_LINES):
        return True
    return len(_record_lines(dropped_entry.get('text', ''))) >= UNRENDERED_MIN_RECORD_LINES


def recovered_row_duplicates_parent(entry: dict, parent: dict | None) -> bool:
    """True when a stage-2 structurally-recovered table row is a verbatim
    duplicate of its own parent entry's text (A5IZ6Q).

    `recover_unclaimed_table_rows` (stage_2_entry_extraction.py) emits one
    entry per table row no delimiter claimed, INDEPENDENTLY of whichever
    delimiter DID end up claiming the surrounding table -- so a wide,
    multi-row table delimiter (one grant's whole label/value block, spanning
    several element indices) and the very rows inside it can both survive as
    separate final entries, carrying the SAME content under two different
    taxonomy codes: the fused parent is confidently classified into a
    render-routed code (e.g. M2B) and renders structurally, while each
    single-field recovered row ("Award Source: | ...") is too sparse to
    classify as anything but the T catch-all and is otherwise headed
    straight for the Appendix as its own numbered line -- a duplicate of
    content the reader already saw in the body. `parent_idx` (set on every
    recovered row) is the link back to the entry it was split from.

    This is deliberately narrower than `_drop_is_safe`'s Jaccard/containment
    dedup above: that pass only ever compares entries WITHIN one taxonomy-code
    group, so a parent and its recovered rows -- classified into two
    different codes -- are never even compared. `segment_already_rendered`
    (render_check.py) doesn't cover this either: it checks a segment against
    the SAME entry's own `extracted_fields`, keyed on a short, fixed field
    list (`_IDENTIFYING_FIELDS`) that excludes most of a grant's row labels
    (PI name, dates, cost, effort) and requires a 15-character value -- too
    narrow to recognize most recovered rows (a bare "LYRASIS"-length agency
    name never clears that floor) even when reused across entries.

    Verbatim containment against the parent's own raw text sidesteps both
    gaps: it needs no field list and no length floor, because a recovered
    row's every field comes from the SAME table cell text the parent's fused
    entry already carries. Comparing after `_CELL_SEPARATOR_RE` strips both
    cell-separator conventions (on top of `_squash`'s own whitespace
    stripping) means this only ever WIDENS a match relative to plain
    `_squash`: removing a shared character from both sides cannot turn a true
    containment into a false one, so every case the narrower comparison
    already caught still matches. A row NOT contained in its parent (one the
    model's delimiter genuinely skipped, the case `recover_unclaimed_table_rows`
    exists for) returns False and is left to the normal appendix/recovery
    path -- callers must not treat False as proof the row is missing, only as
    "not this parent's own duplicate".

    This is a SCOPING check, not a rendered-proof: True says only "this row
    is a candidate duplicate of THIS parent's captured text", never "this
    row's content reached the rendered document" -- a blind review of the
    A5IZ6Q fix caught callers that treated it as both (reproduced: adding a
    'Grant number: | ...' line to a fused M2B entry's raw text, with no
    matching `extracted_fields['grant_number']`, made this return True for
    the matching recovered row even though `_create_grant_table`
    (stage6/sections/research_support.py) never renders a field it never
    receives -- stage 6's fixed-slot renderers drop any field they do not
    name, CLAUDE.md "Stage 6 drops unnamed fields"). A caller that drops a
    row on this signal alone can drop content that renders nowhere.
    `recovered_row_content_rendered`, below, is the second, mandatory gate:
    only a row that is BOTH a scoping match here AND confirmed present in
    the already-rendered document may be dropped.
    """
    if not entry.get('recovered_row') or parent is None:
        return False
    row_text = _squash(_CELL_SEPARATOR_RE.sub('', entry.get('text', '') or ''))
    parent_text = _squash(_CELL_SEPARATOR_RE.sub('', parent.get('text', '') or ''))
    return bool(row_text) and row_text in parent_text


# A recovered row's VALUE token, for `recovered_row_content_rendered` below.
# Digit-inclusive, unlike render_check.py's alpha-only `_RENDER_TOKEN_RE`:
# a recovered row's distinguishing content is often a bare year or amount
# ("00/2021-00/2022"), not prose, and stage 6 reformats dates on the way to
# the grant table ("2021-2022") so a verbatim match alone would miss a row
# that DID render.
_VALUE_TOKEN_RE = re.compile(r'[a-z0-9]{4,}')


def _recovered_row_value(text: str) -> str:
    """The VALUE half of a recovered row's single 'Label: | Value' pair
    (`recover_unclaimed_table_rows`'s own shape -- one field per recovered
    row, never more).

    Deliberately not the label. A section's rendered table writes every row
    label unconditionally and leaves only the value cell blank when the
    field has nothing to show (`_create_grant_table`,
    stage6/sections/research_support.py: all 8 grant rows' labels are always
    written) -- so a label alone always finds itself in the rendered
    document, whether or not this row's actual content did.

    `maxsplit=1`: `recover_unclaimed_table_rows` emits exactly one separator
    (label, then value), but a value that itself contains the separator
    character ("Duration of support: | 00/2021 | 00/2022", two cells folded
    into the row's raw text) must not be truncated at the FIRST occurrence
    inside the value -- splitting on every occurrence would silently drop
    everything after the value's own first separator.
    """
    parts = _CELL_SEPARATOR_RE.split(text or '', maxsplit=1)
    return (parts[1] if len(parts) > 1 else (text or '')).strip()


def _value_contained_in_line(value: str, line: str) -> bool:
    """Whitespace-insensitive containment of `value` in `line`, but a match
    may not begin or end in the middle of an unrelated token: an
    alphanumeric edge of the squashed `value` may not sit against another
    alphanumeric character in the squashed `line`.

    This is the fix for the round-2 defect a plain `squashed_value in line`
    substring check has: '5%' is a literal substring of '25%' (and '2021' of
    '12021'), so a $5 grant's percent-effort value would wrongly read as
    confirmed by a DIFFERENT grant's '25%' cell. Same shape as
    `normalization/pii.py`'s `_pii_containment_pattern` (a value must not be
    denied, there, or confirmed, here, because its characters merely run
    through the middle of an unrelated longer value) -- kept as its own
    small copy rather than imported: that one decides whether a value is
    PROTECTED personal data, a data-governance question; this one decides
    whether a value RENDERED, a content-loss question, and the two must stay
    free to diverge (module docstring: nothing here may import
    `stage_6_word_template`, and pulling in the PII gate's helper for an
    unrelated purpose would blur exactly the boundary that keeps this module
    testable in isolation).

    No minimum length and no non-empty requirement beyond `value` itself --
    callers that need a length floor apply it themselves, since the floor
    differs by caller (`recovered_row_content_rendered`'s VALUE has none
    -- see `test_recovered_row_content_rendered_short_value_not_verifiable`;
    `_parent_identifying_values` below requires >=15 chars, the same floor
    `segment_already_rendered` uses for the same reason: a short value
    cannot anchor a search to one specific block without risking a
    coincidental hit in an unrelated one).
    """
    squashed_value = _squash(value)
    if not squashed_value:
        return False
    squashed_line = _squash(line)
    body = re.escape(squashed_value)
    lead = r'(?<![a-z0-9])' if squashed_value[0].isalnum() else ''
    trail = r'(?![a-z0-9])' if squashed_value[-1].isalnum() else ''
    return re.search(lead + body + trail, squashed_line) is not None


# The floor `_parent_identifying_values` requires of a field value before
# trusting it to anchor a search to ONE rendered block -- mirrors
# `segment_already_rendered`'s (render_check.py) inline 15-character floor
# for the identical reason, named here because this module's own new code
# reads it more than once (CODING_STANDARDS.md 8.2, named constants over
# inline literals; the sibling inline literal in render_check.py is
# untouched -- this PR doesn't touch that function, so extracting its
# constant too would be a drive-by rewrite of code the PR has no other
# reason to change).
_PARENT_ANCHOR_MIN_CHARS = 15


def _parent_identifying_values(parent: dict | None) -> list[str]:
    """The parent entry's own field values substantial enough to identify
    WHICH of the document's many rendered blocks is this specific parent's
    own render -- the same `_IDENTIFYING_FIELDS` list and the same
    date-like exclusion `segment_already_rendered` (render_check.py) uses to
    decide a value can vouch for a record, applied here to locate a block
    instead of to confirm one. A value below the floor, or one carrying no
    identifying prose (`_value_is_datelike` -- a bare date range or grant
    number is shared across a fused entry's sibling records and identifies
    no ONE of them), is never used to pick a block: `_parent_rendered_block`
    must return None rather than gamble a wrong block on a weak anchor.
    """
    fields = (parent or {}).get('extracted_fields') or {}
    values = []
    for key in _IDENTIFYING_FIELDS:
        v = fields.get(key)
        if not isinstance(v, str):
            continue
        v = v.strip()
        if len(v) >= _PARENT_ANCHOR_MIN_CHARS and not _value_is_datelike(v):
            values.append(v)
    return values


def _parent_rendered_block(parent: dict | None,
                           rendered_blocks: list[list[str]]) -> list[str] | None:
    """The ONE rendered block (one paragraph, or one top-level table
    including any tables nested in it -- `_rendered_output_blocks`,
    stage_6_word_template.py) that carries this parent's own identifying
    content, or None when no block does.

    This is the scoping `recovered_row_content_rendered` was missing
    (round-2 finding): searching `rendered_blocks` flattened into one list
    of lines -- the whole document -- let an entirely UNRELATED entry's
    rendered line vouch for a recovered row it has nothing to do with,
    merely because some value happened to coincide (reproduced: two M2B
    grants, A and B; A's own rendered row confirmed B's recovered
    percent-effort and duration rows as "rendered" even though B's own
    grant table never wrote either value). Restricting the search to the
    ONE block this parent's OWN identifying fields resolve to closes that:
    a sibling entry's block is never even inspected.

    None (not "search everything") is the correct answer when no block
    anchors to this parent at all -- the parent itself may not have
    rendered (a declined grant, `_create_grant_table` returning None for a
    too-sparse entry), and falling back to a document-wide search in that
    case would reopen the exact bug this closes. Ambiguity is a named,
    narrower residual, not eliminated: two distinct parents sharing
    identical title/agency text could still resolve to each other's block.
    That is a real but far narrower failure mode than "any line in the
    document" -- unlike the round-2 bug, it requires the SAME identifying
    text on two different records, not merely a coincidental token.
    """
    anchors = _parent_identifying_values(parent)
    if not anchors:
        return None
    for block in rendered_blocks:
        if any(_value_contained_in_line(anchor, line)
               for anchor in anchors for line in block):
            return block
    return None


def recovered_row_content_rendered(entry: dict, parent: dict | None,
                                   rendered_blocks: list[list[str]]) -> bool:
    """True when a recovered row's own VALUE -- never its label, and never
    the parent's raw stage-2 text -- is verifiably present in the ALREADY
    RENDERED document, SCOPED to the parent's own rendered block.

    The second, mandatory half of the A5IZ6Q drop (see
    `recovered_row_duplicates_parent`'s docstring for the gap this closes):
    `recover_unclaimed_table_rows` (stage_2_entry_extraction.py) splits a
    table row out of the fused parent's raw JSON text independently of
    whether ANY renderer ever reads the matching `extracted_fields` key, so
    the row's words sitting in the parent's `text` is not proof they reached
    a render slot -- stage 6's section renderers are fixed-slot and drop any
    field they do not name (CLAUDE.md "Stage 6 drops unnamed fields"), and a
    field can be captured into the parent's raw text while never reaching
    its `extracted_fields` at all.

    Round 2 (blind review): the original version searched EVERY rendered
    line in the whole document, not just this parent's own. That let a
    sibling entry under the same taxonomy code vouch for a row it never
    produced -- a distinct cross-entry duplication bug the caller's raw-text
    scoping gate (`recovered_row_duplicates_parent`) does not catch, because
    that gate only checks the row against the PARENT `find_recovered_row_parent`
    resolved, and says nothing about which document-wide lines
    `recovered_row_content_rendered` itself may then search. `parent` is now
    a required argument for exactly that reason: `_parent_rendered_block`
    resolves it to the one block that is THIS parent's own render (its own
    table or paragraph), via the same `extracted_fields` values the caller
    already has, and everything below only ever looks inside that block.
    `rendered_blocks` is `_rendered_output_blocks()` (stage_6_word_template.py),
    called AFTER every mapped section -- including the grant tables -- has
    rendered and BEFORE this row would be dropped, so it already reflects
    whatever the parent actually wrote.

    Within that one block, matched two ways, both scoped to a SINGLE
    rendered line and both token-aligned (`_value_contained_in_line`, not a
    plain squashed substring -- round 2's second finding: '5%' is a literal
    substring of '25%', so a value-only check without a token boundary could
    still cross-match within the CORRECT block, e.g. two grants that happen
    to render in the same block after a future layout change, or simply two
    percent-effort values that happen to share a digit run): verbatim, after
    whitespace-free squashing (an agency, title, PI name or percent-effort
    value reaches its cell unchanged); or, when stage 6 reformats the value
    (a date range: "00/2021-00/2022" renders "2021-2022"), every
    >=4-character alnum token the value carries, each checked with the same
    token-aligned containment, all inside that one line. A value with no
    verbatim match and no qualifying token is NOT confirmed -- this returns
    False, never a guess, because a false positive here is exactly the
    content loss this check exists to prevent (reproduced: a bare grant
    identifier or a value the parent's `extracted_fields` never carried,
    like `non_financial_support` here, clears neither check and correctly
    returns False). `parent` resolving to no block at all (None) is the same
    "not confirmed" answer, for the same reason -- see
    `_parent_rendered_block`.
    """
    value = _recovered_row_value(entry.get('text', '') or '')
    if not _squash(value):
        return False
    block = _parent_rendered_block(parent, rendered_blocks)
    if block is None:
        return False
    if any(_value_contained_in_line(value, line) for line in block):
        return True
    tokens = _VALUE_TOKEN_RE.findall(value.lower())
    return bool(tokens) and any(
        all(_value_contained_in_line(token, line) for token in tokens)
        for line in block)


def _as_float(value: int | float | str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def find_recovered_row_parent(parent_idx: int | float | str | None,
                              entries_by_element_idx: dict,
                              span_entries: list[dict]) -> dict | None:
    """Resolve a recovered row's `parent_idx` to the entry it was split from.

    Most recovered rows key `parent_idx` to an entry's own
    `element_idx_start` -- a direct hit against `entries_by_element_idx`.
    But the delimiter a row was carved out of can itself span several
    element indices (A5IZ6Q: one grant's whole label/value block runs
    `element_idx_start` 242 to `element_idx_end` 244 as ONE table entry),
    and a row recovered from anywhere in that range carries whichever index
    it structurally sits at as `parent_idx` -- 244, say -- even though no
    entry's OWN `element_idx_start` is 244; 242 (the table entry) is the one
    that actually carries this row's text. A direct-hit miss falls back to
    the first already-computed `span_entries` (callers pass the entries
    whose start/end differ, i.e. genuinely multi-index spans) whose
    [start, end] range contains `parent_idx` numerically. This only ever
    widens which entry `recovered_row_duplicates_parent` gets to compare
    against -- the verbatim-containment check there is the actual gate, so
    a wrong guess here just fails that check instead of suppressing
    anything unsafely.
    """
    direct = entries_by_element_idx.get(str(parent_idx))
    if direct is not None:
        return direct
    idx_num = _as_float(parent_idx)
    if idx_num is None:
        return None
    for candidate in span_entries:
        start = _as_float(candidate.get('element_idx_start'))
        end = _as_float(candidate.get('element_idx_end'))
        if start is not None and end is not None and start <= idx_num <= end:
            return candidate
    return None


def deduplicate_entries(entries: list[dict], verbose: bool = False,
                        require_date_overlap: bool = False,
                        decisions: list[dict] | None = None) -> list[dict]:
    """Remove near-duplicate entries within a code group.

    Uses two metrics to catch duplicates:
    1. Jaccard similarity (symmetric) — catches similar-length entries
    2. Containment (asymmetric) — catches when a short entry is a subset
       of a longer one (e.g., brief mention vs. detailed description)

    When two entries are duplicates, the longer / more detailed one is kept.

    If require_date_overlap is True, text-similar entries are only deduped when
    their date ranges match or overlap.  This prevents false positives on career
    progression sequences (e.g., Intern -> Resident -> Chief Resident at same
    institution) where word overlap is high but dates differ.

    If decisions is a list, every drop is appended to it as a dict (metric
    values plus dropped/kept text) so the caller can persist the decision
    trail for the run doctor (#227: at these thresholds a drop is not always
    a true duplicate).

    Pairwise and order-dependent by design, not clustered: entries are
    compared left-to-right and a drop removes that index from further
    comparison (see the `break` below), so for A~B~C where A and C aren't
    themselves similar enough to pair directly, which of {A, B} survives
    depends on iteration order. Deliberate trade-off, not an oversight —
    building duplicate clusters and picking one canonical record per cluster
    would need its own corpus-verified safety pass; documented here instead
    of changed blind.
    """
    if len(entries) <= 1:
        return entries

    sigs = [_entry_signature_words(e) for e in entries]
    titles = [_entry_title_words(e) for e in entries]
    drop_indices = set()

    for i in range(len(entries)):
        if i in drop_indices:
            continue
        for j in range(i + 1, len(entries)):
            if j in drop_indices:
                continue
            if not sigs[i] or not sigs[j]:
                continue
            intersection = sigs[i] & sigs[j]
            union = sigs[i] | sigs[j]
            smaller = min(len(sigs[i]), len(sigs[j]))

            jaccard = len(intersection) / len(union) if union else 0
            containment = len(intersection) / smaller if smaller else 0

            # Also check title-only similarity (text before first tab).
            # This catches cases where both entries describe the same activity
            # but have very different narrative descriptions.
            # Require at least 4 significant words in the smaller title to avoid
            # false positives from short generic titles like "Emergency Medicine".
            title_containment = 0.0
            if titles[i] and titles[j]:
                title_smaller = min(len(titles[i]), len(titles[j]))
                if title_smaller >= 4:
                    title_inter = titles[i] & titles[j]
                    title_containment = len(title_inter) / title_smaller if title_smaller else 0

            is_dup = jaccard >= 0.6 or containment >= 0.75 or title_containment >= 0.8

            # Safety check: if full-text metrics trigger but titles are clearly
            # different, these are likely distinct items at the same venue (e.g.,
            # two different talks at the same grand rounds session).
            if is_dup and title_containment < 0.8 and titles[i] and titles[j]:
                title_union = titles[i] | titles[j]
                title_jaccard = (len(titles[i] & titles[j]) / len(title_union)
                                 if title_union else 0)
                if title_jaccard <= 0.25 and min(len(titles[i]), len(titles[j])) >= 3:
                    if verbose:
                        print(f"    Dedup: skipping (different titles, "
                              f"title_jaccard={title_jaccard:.2f}) "
                              f"[{entries[i].get('text', '')[:50]}...]")
                    is_dup = False

            # For career-progression codes, require date overlap to confirm
            if is_dup and require_date_overlap:
                if not _dates_overlap_or_match(entries[i], entries[j]):
                    if verbose:
                        print(f"    Dedup: skipping (dates differ) "
                              f"[{entries[i].get('text', '')[:50]}...]")
                    is_dup = False
            if is_dup:
                # Keep the longer (more detailed) entry
                len_i = len(entries[i].get('text', ''))
                len_j = len(entries[j].get('text', ''))
                drop = j if len_i >= len_j else i
                kept = i if drop == j else j
                if not _drop_is_safe(entries[drop], entries[kept]):
                    if verbose:
                        print(f"    Dedup: skipping (similar but not "
                              f"verbatim-contained, single record — keeping "
                              f"both, #227) "
                              f"[{entries[drop].get('text', '')[:50]}...]")
                    continue
                if jaccard >= 0.6:
                    metric = f"jaccard={jaccard:.2f}"
                elif containment >= 0.75:
                    metric = f"containment={containment:.2f}"
                else:
                    metric = f"title={title_containment:.2f}"
                if verbose:
                    print(f"    Dedup: dropping entry ({metric}), "
                          f"keeping [{entries[kept].get('text', '')[:60]}...]")
                if decisions is not None:
                    decisions.append({
                        "metric": metric,
                        "jaccard": round(jaccard, 2),
                        "containment": round(containment, 2),
                        "title_containment": round(title_containment, 2),
                        "dropped_text": entries[drop].get('text', '')[:500],
                        "kept_text": entries[kept].get('text', '')[:500],
                    })
                drop_indices.add(drop)
                if drop == i:
                    # i is gone: it must not keep vouching to drop later j's
                    # (observed over-drop vector in the 2Q1_ZQ S8 trace, #227)
                    break

    if drop_indices:
        return [e for idx, e in enumerate(entries) if idx not in drop_indices]
    return entries
