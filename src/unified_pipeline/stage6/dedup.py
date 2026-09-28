"""Near-duplicate removal within a taxonomy-code group (#398).

Pure move out of `stage_6_word_template.py` (which re-exports every name here,
so existing callers are unchanged). `deduplicate_entries` is called once per
code group before rendering; `_drop_is_safe` is the #227 guard that only lets
a drop through when the loss is provably recoverable. The docstrings and the
constants' comments carry the accuracy history (#227, #208, C0ZGFW, 2Q1_ZQ)
and are the spec.

Depends one level down on `render_check` (`_record_lines` and its
fused-multi-record threshold) because "is this a fused blob" and "is this
value substantial enough to anchor a search" are both questions
`segment_already_rendered` already answers the same way.
Nothing here may import `stage_6_word_template`.
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
        quoted = re.findall(r'["“](.+?)["”]', text)
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


# A recovered table row's cell separator, as `recover_unclaimed_table_rows`
# (stage_2_entry_extraction.py, #420) always renders one: " | " when a
# physical table row's cells are joined, or a bare "\t" on the
# tab-separated fallback path. A row with more than two physical columns
# (Label, StartDate, EndDate) survives as one string with MORE than one
# separator in it -- `_recovered_row_value_cells` below relies on that to
# split every value cell out on its own, not just the first.
_CELL_SEPARATOR_RE = re.compile(r'[|\t]')


def _recovered_row_value_cells(text: str) -> list[str]:
    """Every VALUE cell of a stage-2 structurally recovered table row's raw
    text (`recover_unclaimed_table_rows`, #420): every cell after the row's
    own first cell (its label), split on `_CELL_SEPARATOR_RE`.

    A recovered row is usually one label and one value ("Award Source: |
    Fictional Research Foundation"), but `recover_unclaimed_table_rows`
    joins the WHOLE physical table row regardless of column count -- a
    3-column row (Label, StartDate, EndDate) survives as one string with
    TWO separators in it ("Duration of support: | 00/2021 | 00/2022"), and
    both halves of that date range are their own value cell, checked
    independently by `recovered_row_already_rendered` below rather than
    rejoined into one string: a rejoined "00/2021 | 00/2022" can never match
    the rendered document verbatim, since nothing renders the raw separator
    character, so treating it as a single cell would only ever hide a real
    match, never produce a false one.

    A row with no separator at all (malformed -- `recover_unclaimed_table_rows`
    always emits label|value) has no label to split off, so the whole text
    is itself the one value cell: there is no safer fallback, and returning
    no cells at all would make the caller treat the row as vacuously safe to
    drop (see `recovered_row_already_rendered`'s own `if not cells` guard).
    """
    cells = [c.strip() for c in _CELL_SEPARATOR_RE.split(text or '')]
    return cells[1:] if len(cells) > 1 else cells


def _is_trivial_value_cell(cell: str) -> bool:
    """A value cell with no alphanumeric character (blank, or
    separator/punctuation-only -- e.g. the empty second cell of
    "Non-financial support: | ") carries no content that dropping the row
    could lose, so it never has to be found rendered anywhere. Symmetrically,
    it must never by itself justify a drop either: a row whose every cell is
    trivial has nothing confirmed rendered and stays (the `if not cells`
    guard in `recovered_row_already_rendered`)."""
    return not any(ch.isalnum() for ch in cell)


def _collapse_and_fold(text: str) -> str:
    """Casefold + whitespace-COLLAPSED (never whitespace-deleted)
    normalization for `recovered_row_already_rendered`'s containment check.

    Deliberately not this module's own `_squash` (whitespace-FREE, used by
    `_drop_is_safe` above): `recovered_row_already_rendered` joins every
    already-rendered line of the document into ONE string before searching
    it, and a `_squash`-style join would delete the very whitespace that
    keeps two unrelated adjacent lines apart -- gluing "...Foundation" and
    "1%..." into "...Foundation1%..." risks a match that never existed as
    contiguous rendered text. Collapsing each run of whitespace to a single
    space keeps a real word boundary at every line join instead of removing
    it. Kept as its own small copy rather than importing
    `normalization/pii.py`'s near-identical `_collapse_whitespace`, for the
    same reason `_value_contained_in_text` below doesn't import that
    module's containment helper either: that module decides whether a value
    is PROTECTED personal data, a data-governance question; this one decides
    whether a value RENDERED, a content-loss question, and the two must stay
    free to diverge (module docstring: nothing here may import
    `stage_6_word_template`, and the same boundary applies one level down to
    the PII module).
    """
    return re.sub(r'\s+', ' ', str(text or '')).strip().casefold()


def _value_contained_in_text(value: str, text: str) -> bool:
    """Whitespace-collapsed, case-folded containment of `value` in `text`,
    aligned on a word boundary at BOTH ends: an alphanumeric edge of `value`
    may not sit against another alphanumeric character in `text`.

    Without the boundary, '5%' is a literal substring of '25%', and '2021'
    is a literal substring of '20215' at the TRAILING edge -- a short value
    from one record could read as "already rendered" merely because a
    longer, unrelated value happens to contain the same characters, at
    either end. Same shape as `normalization/pii.py`'s
    `_pii_containment_pattern`, kept as its own copy for the reason
    `_collapse_and_fold` above gives.
    """
    folded_value = _collapse_and_fold(value)
    if not folded_value:
        return False
    folded_text = _collapse_and_fold(text)
    body = re.escape(folded_value)
    lead = r'(?<![a-z0-9])' if folded_value[0].isalnum() else ''
    trail = r'(?![a-z0-9])' if folded_value[-1].isalnum() else ''
    return re.search(lead + body + trail, folded_text) is not None


def recovered_row_already_rendered(entry: dict, rendered_lines: list[str]) -> bool:
    """True when a stage-2 structurally-recovered table row (#420,
    `recover_unclaimed_table_rows`) is safe to drop from the Appendix because
    EVERY non-trivial value cell of its own raw text already appears,
    verbatim (word-boundary, whitespace-collapsed, case-folded), somewhere
    in the document's ALREADY-RENDERED body -- never the Appendix itself,
    which has not been written yet when this runs (`_drop_recovered_row_duplicates`,
    stage_6_word_template.py, always calls this before
    `_add_remaining_to_appendix`).

    A row with zero non-trivial value cells (its only content is a bare
    label) is never dropped by this: there is no value to confirm, so
    "confirmed rendered" cannot be true, and the row stays. Because this
    only ever REQUIRES more matches before allowing a drop, it can never
    treat an unrendered value as rendered -- the one failure mode that
    would actually lose content.

    Deliberately provenance-blind (round 3): earlier rounds required a
    row's raw text to ALSO be a verbatim substring of one specific parent
    entry (`recovered_row_duplicates_parent`, now removed), then scoped the
    render check to that one parent's own rendered block
    (`_parent_rendered_block`, also removed) -- both meant to stop an
    unrelated entry's render from vouching for a row it had nothing to do
    with. That scoping was itself the bug: it picked the first rendered
    block carrying ANY one of a parent's identifying field values, and a
    value shared across records -- most commonly a funding agency, shared
    across a faculty member's own grants -- resolved two different parents
    to the SAME block. A grant whose own table rendered nothing for a field
    could still have its recovered row dropped because a same-agency
    sibling's block happened to match: real content loss, the exact failure
    this function exists to prevent, and no content-keyed scoping is safe
    against it, because `extracted_fields` carries no way to tell two
    records' shared values apart.

    The fix drops the identity question entirely: this never asks WHICH
    entry rendered a value, only whether the value is somewhere in the
    document the reader will already see. That is also the actual guarantee
    an Appendix drop needs -- a duplicate line adds noise, a missing one
    loses content, and "printed by a different record" is still printed.
    A row can therefore be dropped even when the match is coincidental (two
    grants that happen to share one field's exact text); the trade is a
    little provenance precision for a rule that is unconditionally simpler
    and unconditionally content-safe. The inverse case -- a value stage 6
    REFORMATS on the way to a render slot (a raw "00/2021" cell rendered as
    "2021") -- will not verbatim-match and so is correctly NOT confirmed:
    the row stays, printed once more than strictly necessary. Content
    duplication, never content loss, is the only direction this function is
    allowed to be wrong in.

    `entry.get('recovered_row')` gates this exactly as every earlier round
    did: only stage 2's structural backstop sets that flag, so an ordinary
    model-attested entry that happens to be a text subset of another still
    goes through `deduplicate_entries`'s Jaccard/containment path above,
    never this one.
    """
    if not entry.get('recovered_row'):
        return False
    cells = [c for c in _recovered_row_value_cells(entry.get('text', '') or '')
             if not _is_trivial_value_cell(c)]
    if not cells:
        return False
    rendered_text = '\n'.join(rendered_lines)
    return all(_value_contained_in_text(cell, rendered_text) for cell in cells)


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
