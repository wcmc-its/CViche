"""Near-duplicate removal within a taxonomy-code group (#398).

Pure move out of `stage_6_word_template.py` (which re-exports every name here,
so existing callers are unchanged). `deduplicate_entries` is called once per
code group before rendering; `_drop_is_safe` is the #227 guard that only lets
a drop through when the loss is provably recoverable. The docstrings and the
constants' comments carry the accuracy history (#227, #208, C0ZGFW, 2Q1_ZQ)
and are the spec.

Depends one level down on `render_check` (`_record_lines` and its
fused-multi-record threshold) because "is this a fused blob" is the same
question both answer. Nothing here may import `stage_6_word_template`.
"""
import re

from .normalization import _squash
from .parsing import _dates_overlap_or_match
from .render_check import UNRENDERED_MIN_RECORD_LINES, _record_lines


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
    gaps: it needs no field list, no length floor, and no rendered-document
    lookup, because a recovered row's every field comes from the SAME table
    cell text the parent's fused entry already carries. A row NOT contained
    in its parent (one the model's delimiter genuinely skipped, the case
    `recover_unclaimed_table_rows` exists for) returns False and is left to
    the normal appendix/recovery path -- callers must not treat False as
    proof the row is missing, only as "not this parent's own duplicate".
    """
    if not entry.get('recovered_row') or parent is None:
        return False
    row_text = _squash(entry.get('text', ''))
    return bool(row_text) and row_text in _squash(parent.get('text', ''))


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
