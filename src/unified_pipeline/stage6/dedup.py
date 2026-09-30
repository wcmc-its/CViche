"""Near-duplicate removal within a taxonomy-code group (#398).

Pure move out of `stage_6_word_template.py` (which re-exports every name here,
so existing callers are unchanged). `deduplicate_entries` is called once per
code group before rendering; `_drop_is_safe` is the #227 guard that only lets
a drop through when the loss is provably recoverable. The docstrings and the
constants' comments carry the accuracy history (#227, #208, C0ZGFW, 2Q1_ZQ)
and are the spec.

Depends one level down on `render_check` (`_record_lines` and its
fused-multi-record threshold) because "is this a fused blob" is the same
question both answer, and on `fan_out` for the fields each section renderer
writes (`_RENDERED_FIELDS`, `_FORMATTED_KEYS`), because "would this record
reach the page" is the question #983's fan-out answers too. Nothing here may
import `stage_6_word_template`.
"""
import re

from .fan_out import _FORMATTED_KEYS, _RENDERED_FIELDS, FANNED_OUT_FROM
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


# #666: every text-similarity signal `deduplicate_entries` uses is blind to a
# date difference, so "Chair, X Committee, 2015-2018" is token-contained in a
# later "Chair, X Committee, 2021-2024 ..." entry that also mentions 2015 and
# 2018 in passing. `_drop_is_safe` therefore compares the year spans the two
# texts state. A span is a range ("2015-2018", "2015 to 2018", "2015-18",
# "2012-present"; an open end reaches `_YEAR_OPEN_END`) or a bare year. Text,
# not `extracted_fields`, because a fused or prose entry has no single
# start/end and the text is what would be lost.
_YEAR_PATTERN = r'(?:19|20)\d{2}'
_YEAR_RE = re.compile(rf'\b{_YEAR_PATTERN}\b')
# The end of a range may carry a month before its year ("Jun 2021", "06/2009");
# a bare two-digit end ("2015-18") must not be the month of "2008 - 06/2009" or
# the month/day of a full date range ("07/01/2021-06/30/2024" is not 2021-2030).
_YEAR_RANGE_RE = re.compile(
    rf'\b({_YEAR_PATTERN})\s*(?:[-\u2013\u2014]|to|through|until)\s*'
    rf'(?:(?:[A-Za-z]{{3,9}}\.?|\d{{1,2}}[/.])\s*)?'
    rf'({_YEAR_PATTERN}|\d{{2}}(?![/\d])|present|current|now|ongoing)\b',
    re.IGNORECASE)
_YEAR_OPEN_END = 9999


def _year_ranges(text: str) -> list[tuple[int, int]]:
    """(first year, last year) of every year range the text states."""
    ranges = []
    for match in _YEAR_RANGE_RE.finditer(text):
        start, end = match.group(1), match.group(2).lower()
        if end.isdigit():
            end_year = int(start[:2] + end) if len(end) == 2 else int(end)
        else:
            end_year = _YEAR_OPEN_END
        ranges.append((int(start), end_year))
    return ranges


# "Phase I" / "phase 2" / "Phase II/III": the one mark that tells two trials of
# the same drug apart (#1106). A one-character numeral is not a significant
# word, so the token-containment branch below cannot see it. A combined-phase
# design names every numeral in its list, so it covers a copy naming any one.
_PHASE_NUMERAL = r'(?:[1-4]|iv|i{1,3})'
_TRIAL_PHASE_RE = re.compile(
    rf'\bphases?\s*({_PHASE_NUMERAL}(?:\s*(?:/|-|&|,|and)\s*{_PHASE_NUMERAL})*)\b',
    re.IGNORECASE)
_ROMAN_PHASE = {'i': '1', 'ii': '2', 'iii': '3', 'iv': '4'}


def _trial_phases(text: str) -> set[str]:
    """Every trial phase the text names, Roman numerals as digits."""
    return {_ROMAN_PHASE.get(n.lower(), n)
            for group in _TRIAL_PHASE_RE.findall(text)
            for n in re.findall(_PHASE_NUMERAL, group, re.IGNORECASE)}


def _dates_compatible(dropped_text: str, kept_text: str) -> bool:
    """False when the dropped text states a date the kept text does not carry.

    Each dropped range must overlap a RANGE the kept text states (a kept entry
    that only mentions the two boundary years in passing does not vouch for it);
    each bare dropped year must appear in the kept text or fall inside one of
    its ranges. Undated dropped text is compatible with anything. Doubt keeps
    the entry: a surviving duplicate is visible to a reader, a lost record is not.
    """
    kept_ranges = _year_ranges(kept_text)
    for low, high in _year_ranges(dropped_text):
        if not any(low <= kept_high and kept_low <= high
                   for kept_low, kept_high in kept_ranges):
            return False
    kept_years = {int(y) for y in _YEAR_RE.findall(kept_text)}
    for year in _YEAR_RE.findall(_YEAR_RANGE_RE.sub(' ', dropped_text)):
        if int(year) not in kept_years and not any(
                low <= int(year) <= high for low, high in kept_ranges):
            return False
    return True


# #666: the verbatim branch below proves the dropped TEXT sits inside the kept
# entry's text, but most sections render the kept entry from its extracted
# FIELDS (`_RENDERED_FIELDS`, read off each renderer), and those describe one
# record. When the kept text fused several records (a row of grants, board
# memberships or citations captured as one entry), the others never reached
# the page: web26 lost two M2A sub-grants and an S1 citation that way. So a
# verbatim drop also needs the kept entry's rendered fields to carry the
# dropped record's name: the one of these fields the code's section writes
# (its title-like FIELD_SCHEMAS key; no field-rendered code writes two). A code
# whose section renders the entry's text is not in `_RENDERED_FIELDS`, and its
# verbatim drops stand; F1, I and Q1 write no name field, and theirs stand too.
_RECORD_NAME_FIELDS = (
    'title', 'chapter_title', 'grant_title', 'course_title', 'award_name',
    'leadership_role', 'committee_name', 'panel_name', 'program_name',
    'mentee_name', 'journal_name', 'degree', 'specialty')
# A grant whose amount or number the kept entry also carries is the same grant,
# whatever its title field holds (a description can land in `title`).
_RECORD_ID_FIELDS = (
    'grant_number', 'total_funding', 'annual_funding', 'doi', 'pmid', 'pmcid',
    'isbn', 'patent_number', 'license_number', 'abstract_number')
# Fewer digits than this is a year or a volume, not an identifier.
_RECORD_ID_MIN_DIGITS = 4


def _record_name(fields: dict, rendered: frozenset[str]) -> str | None:
    """The record's name: its first filled `_RECORD_NAME_FIELDS` value the
    section renders, or None when it fills none."""
    for key in _RECORD_NAME_FIELDS:
        value = fields.get(key)
        if key in rendered and isinstance(value, str) and value.strip():
            return value
    return None


def _shares_record_id(dropped_fields: dict, kept_fields: dict) -> bool:
    """True when both entries carry the same amount, grant number, DOI or other id."""
    for key in _RECORD_ID_FIELDS:
        dropped_id = re.sub(r'\D', '', str(dropped_fields.get(key) or ''))
        if (len(dropped_id) >= _RECORD_ID_MIN_DIGITS
                and dropped_id == re.sub(r'\D', '', str(kept_fields.get(key) or ''))):
            return True
    return False


def _rendered_words(fields: dict, rendered: frozenset[str]) -> set[str]:
    """Every significant word the section writes for an entry with `fields`.

    All rendered fields together, not one: "X Committee, Y Society" is the
    kept record when its fields split it into committee "X Committee" and
    organization "Y Society", and keeping the dropped copy would print that
    row twice. A stage-5 rendering of the whole entry (`_FORMATTED_KEYS`)
    counts as written too. A rendered key holding a list or number vouches
    with the words of its printed form: over-vouching only lets a drop stand,
    as it did before #666, where under-vouching could print a record twice.
    """
    return set().union(*(_significant_words(str(value))
                         for key, value in fields.items()
                         if (key in rendered or key in _FORMATTED_KEYS) and value))


def _fused_record_would_be_lost(dropped_entry: dict, kept_entry: dict,
                                code: str | None,
                                document: list[dict] | None) -> bool:
    """True when dropping a verbatim-contained entry would lose its record.

    It would when the dropped entry names a record (`_record_name`) whose
    words the kept entry's rendered fields do not all carry, no shared id ties
    the two together, and the name appears in no other entry of `document`.
    That last clause keeps the check from creating a duplicate: a record the
    CV also lists somewhere else is already rendered, or rides on that entry,
    and keeping this copy could print it twice. Doubt drops, as it did before.
    """
    rendered = _RENDERED_FIELDS.get(code or '', frozenset())
    dropped_fields = dropped_entry.get('extracted_fields') or {}
    kept_fields = kept_entry.get('extracted_fields') or {}
    name = _record_name(dropped_fields, rendered)
    if (not name or not kept_fields
            or _shares_record_id(dropped_fields, kept_fields)
            or _significant_words(name) <= _rendered_words(kept_fields, rendered)):
        return False
    return not any(_names_record(entry.get('text') or '', name)
                   for entry in document or ()
                   if entry is not dropped_entry and entry is not kept_entry)


# `_names_record`: a long name counts as named by a text that holds this share
# of its significant words. A CV that lists one abstract twice rewords its
# title the second time (a qualifier added, an acronym spelled out): web228
# had two such pairs, and keeping the dropped copy printed each twice.
_NAMED_ELSEWHERE_WORD_SHARE = 0.8


def _names_record(text: str, name: str) -> bool:
    """True when `text` mentions the record called `name`.

    A name of `DEDUP_FULL_CONTAINMENT_MIN_TOKENS` or more significant words is
    mentioned when the text holds `_NAMED_ELSEWHERE_WORD_SHARE` of them; a
    shorter one only as a run of the same letters and digits, since two
    common words ("Widget Services") are in many unrelated entries.
    """
    name_words = _significant_words(name)
    if len(name_words) >= DEDUP_FULL_CONTAINMENT_MIN_TOKENS:
        shared = len(name_words & _significant_words(text))
        return shared >= _NAMED_ELSEWHERE_WORD_SHARE * len(name_words)
    return _alnum(name) in _alnum(text)


def _alnum(text: str) -> str:
    """Lowercased letters and digits only: punctuation and spacing ignored."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _drop_is_safe(dropped_entry: dict, kept_entry: dict,
                  code: str | None = None,
                  document: list[dict] | None = None) -> bool:
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

    #666: the two non-verbatim branches also require `_dates_compatible` --
    a dropped entry that states a date range the kept entry does not carry is a
    different record (a second committee term, a second grant), and no text
    similarity signal can see that. For a `code` whose section renders
    fields, the verbatim branch also requires that the kept entry's rendered
    fields carry the dropped record's name, unless another entry of
    `document` (every pre-dedup entry of the run) names it
    (`_fused_record_would_be_lost`). Still open: the token-containment branch
    against a kept entry that fused the dropped record with a sibling (gating
    it the same way duplicated reworded rows on the corpus), and a short name
    inside a longer kept name ("Widgets" in "Widgets Quarterly") on either branch;
    `_record_lines()` finds no line in prose for the #221/#225 recovery pass
    to re-verify."""
    dropped_squashed = _squash(dropped_entry.get('text', ''))
    verbatim = bool(dropped_squashed
                    and dropped_squashed in _squash(kept_entry.get('text', '')))
    if dropped_entry.get(FANNED_OUT_FROM):
        # #983: a record fanned out of a multi-record entry is a single short
        # line, so the two branches below (token containment; fused-blob
        # recovery) approve dropping it against any longer entry that shares
        # its words -- "Co-Leader, Cancer Epidemiology (2012-)" against a
        # "Co-Leader, Cancer Epidemiology Program (2012-2015)" -- and the
        # recovery pass never re-checks it, because a single segment is not a
        # record line. Its text also carries none of the context it inherited
        # from its parent, so the same mentee listed under two fellowships has
        # identical text and different fields. Only a verbatim copy with the
        # same fields may go; anything else is kept, like the other
        # single-record case (#227).
        return verbatim and (dropped_entry.get('extracted_fields')
                             == kept_entry.get('extracted_fields'))
    if verbatim:
        return not _fused_record_would_be_lost(dropped_entry, kept_entry,
                                               code, document)
    if not _dates_compatible(dropped_entry.get('text') or '',
                             kept_entry.get('text') or ''):
        return False  # #666: a different date is a different record
    if not _trial_phases(dropped_entry.get('text') or '') <= _trial_phases(
            kept_entry.get('text') or ''):
        return False  # #1106: a different trial phase is a different trial
    dropped_sig = _entry_signature_words(dropped_entry)
    if (len(dropped_sig) >= DEDUP_FULL_CONTAINMENT_MIN_TOKENS
            and dropped_sig <= _entry_signature_words(kept_entry)
            and len(_record_lines(kept_entry.get('text', ''))) < DEDUP_FUSED_BLOB_RECORD_LINES):
        return True
    return len(_record_lines(dropped_entry.get('text', ''))) >= UNRENDERED_MIN_RECORD_LINES


def deduplicate_entries(entries: list[dict], verbose: bool = False,
                        require_date_overlap: bool = False,
                        decisions: list[dict] | None = None,
                        code: str | None = None,
                        document: list[dict] | None = None) -> list[dict]:
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

    `code` is the group's taxonomy code and `document` every entry of the
    run, all codes, before dedup: the #666 fused-record check in
    `_drop_is_safe` reads the code's rendered fields and searches the
    document for another mention of a record before refusing a drop. With no
    code that check never refuses; with no document no other entry is known.

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
                if not _drop_is_safe(entries[drop], entries[kept],
                                     code, document):
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
