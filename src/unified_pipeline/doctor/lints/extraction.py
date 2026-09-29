"""Lints for what the extraction stages pulled out, and what became of it (#493).

One responsibility: compare the entries and fields stages 3b/4 produced against
the source they came from and the document they landed in -- grants filed under
the wrong funding heading, entries whose field extraction covered almost none of
their text, taxonomy codes that vanished between classification and render,
dedup drops that were not duplicates, records fabricated from the template's
own scaffolding.

The line against `render.py` is which side of the comparison is the subject.
These five are about the extracted record; the render lints are about the page.
`lint_classified_unrendered` reads the output blocks, but only to decide whether
a 3b classification survived -- the finding is about the classification.

The thirteen constants, regexes and helpers below them are used by nothing else
in `run_doctor.py`, so they move together and stop being module-global.
`run_doctor` re-exports every name it exported before; `_entry_rendered` and
`_funding_haystacks` have since been fixed in place (#537, #492) -- see their
docstrings and comments for what changed.
"""
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.core.template_boilerplate import (
    _MIN_EXACT_LEN,
    is_near_template_instruction,
    is_template_instruction,
    is_template_label_line,
)
from unified_pipeline.stage4.coercion import (
    DATE_RANGE_TAXONOMY_CODES,
    find_single_closed_range,
)
from unified_pipeline.segmentation_regression import (
    SUBSTANTIVE_LINE_CHARS,
    _looks_like_record,
    _norm,
)
from unified_pipeline.stage_6_word_template import (
    RENDER_ROUTED_CODES,
    grant_status_rebucket_target,
)

from ..shared import (
    Haystack,
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    _entry_pieces,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
)


# --------------------------------------------------------------------------
# Grant status vs the funding subsection the grant rendered under.

# Stage 4 extracts 'status' for M2A/M2B/M2C (#982), but only when the entry
# states one and the LLM fills it; `_entry_status` prefers that field and falls
# back to the labelled fragment in the raw entry text ("Status: Not funded").
_STATUS_LABEL_RE = re.compile(r"status\s*[:\-]\s*([^|\n]+)", re.IGNORECASE)


# The funding subsection headers stage 6 renders grant tables beneath.
_FUNDING_SECTIONS = (
    ("M2A", "current research funding"),
    ("M2B", "past (completed) funding"),
    ("M2C", "pending funding"),
)


# Segment boundaries that are not themselves funding headers but still close
# a funding section. `_output_section_header` only recognises a lettered
# "X. " prefix or an ALL-CAPS paragraph; the M2D heading `_fill_patents`
# writes into the template (stage_6_word_template.py's generate() calls
# `_fill_patents` immediately after `_fill_research_support`, so M2D always
# sits directly after M2A/B/C in render order) is Title-Case ("Patents &
# Inventions") and matches neither, so it used to fall through and keep
# accumulating into whichever funding bucket was still open -- the M2C
# haystack absorbed the entire patents section on every corpus CV that had
# one (#492). Normalised exactly like `_FUNDING_SECTIONS` titles are
# compared (`_norm`, trailing colon stripped).
_FUNDING_BOUNDARY_TITLES = frozenset({
    "patents & inventions",
})


def _entry_status(entry: Dict) -> Optional[str]:
    status = (entry.get("extracted_fields") or {}).get("status")
    if status:
        return str(status)
    match = _STATUS_LABEL_RE.search(str(entry.get("text", "")))
    return match.group(1).strip() if match else None


def _funding_haystacks(blocks: list[tuple[str, str]]) -> dict[str, Haystack]:
    """Per-bucket Haystack of everything rendered under each of stage 6's
    funding subsection headers."""
    segments: Dict[str, List[Tuple[str, str]]] = {c: [] for c, _ in _FUNDING_SECTIONS}
    current = None
    for kind, text in blocks:
        stripped = str(text).strip()
        if kind == "p":
            normed = _norm(stripped).rstrip(":")
            code = next((c for c, title in _FUNDING_SECTIONS if normed == title), None)
            if code:
                current = code
                continue
            if normed in _FUNDING_BOUNDARY_TITLES or _output_section_header(stripped):
                current = None
                continue
        if current:
            segments[current].append((kind, text))
    return {code: _haystacks(seg) for code, seg in segments.items()}


def lint_bucket_status(stage4: Dict, blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Grant status (extracted field, else the 'Status:' label in the raw
    entry text) vs the funding subsection the grant actually rendered under.
    Stage 6 rebuckets mis-bucketed grants at render time (#214), so the
    stage-4 code alone proves nothing; a WARN here means the rendered
    document files the grant under the wrong funding heading, or lost it."""
    titles = dict(_FUNDING_SECTIONS)
    rendered = _funding_haystacks(blocks)
    findings = []
    for e in stage4.get("entries", []):
        code = e.get("taxonomy_code")
        if code not in ("M2A", "M2B", "M2C"):
            continue
        status = _entry_status(e)
        target, _note = grant_status_rebucket_target(status or "")
        if not target or target == code:
            continue
        verdicts = {bucket: _entry_rendered(e.get("text"), h.text, h.tokens)
                    for bucket, h in rendered.items()}
        if verdicts[target]:
            continue  # stage 6 rebucketed it correctly
        if all(v is None for v in verdicts.values()):
            continue  # too short to locate in the output either way
        hits = [b for b, v in verdicts.items() if v]
        where = (f"it rendered under {hits[0]} ('{titles[hits[0]]}')" if hits
                 else "the entry is under no funding heading at all")
        findings.append(_finding(
            "bucket_status", "WARN",
            f"entry {e.get('element_idx_start')}: status '{status}' implies "
            f"{target} ('{titles[target]}') but {where}",
            [str(e.get("text", ""))[:120]]))
    return findings


# --------------------------------------------------------------------------
# Field extraction that covered almost none of a big entry.

# Lint 4: an entry this big, with this many record-like lines, extracting
# under this coverage is a mass-loss smell, not LLM wobble.
UNDER_EXTRACTION_MAX_PCT = 40.0
UNDER_EXTRACTION_MIN_CHARS = 800
UNDER_EXTRACTION_MIN_RECORDS = 2


# _looks_like_record only sees pipe/tab rows; fused award/honor lines are
# plain newline lines carrying a leading or trailing year ("2020 AECT ...",
# "... August 2025.") — the 2Q1_ZQ honors mega-entry (19% coverage) was
# invisible without counting them (#229).
_YEAR_EDGE_LINE_RE = re.compile(
    r"^\s*(?:19|20)\d{2}\b|\b(?:19|20)\d{2}\s*[.)]?\s*$")


def lint_under_extraction(stage4: Dict) -> List[Dict]:
    """Large multi-record entries whose stage-4 field extraction covered
    almost none of the text: the rest of the records silently vanish."""
    findings = []
    for e in stage4.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        pct = (e.get("extraction_coverage") or {}).get("extraction_coverage_percent")
        if pct is None or pct >= UNDER_EXTRACTION_MAX_PCT:
            continue
        text = str(e.get("text", ""))
        if len(text) <= UNDER_EXTRACTION_MIN_CHARS:
            continue
        records = sum(
            1 for line in text.split("\n")
            if _looks_like_record(line)
            or (len(line.strip()) >= SUBSTANTIVE_LINE_CHARS
                and _YEAR_EDGE_LINE_RE.search(line)))
        if records < UNDER_EXTRACTION_MIN_RECORDS:
            continue
        findings.append(_finding(
            "under_extraction", "WARN",
            f"entry {e.get('element_idx_start')}: extraction coverage {pct}% "
            f"on a {len(text)}-char entry with {records} record-like lines",
            [text[:120]]))
    return findings


# --------------------------------------------------------------------------
# 3b classifications that reached no part of the output.
#
# The threshold is the measured p75 of entries-lost-per-run over the
# 73 scored runs of the 2026-07-25 batch -- the same measurement as
# the render magnitudes. WARN means this run sits in the corpus's
# worst quartile, not that the lint fired at all (#438).
CLASSIFIED_UNRENDERED_WARN_ENTRIES = 2


def _entry_rendered(text: str | None, haystack: str, haystack_tokens: set) -> bool | None:
    """Whether an entry's text surfaces in the output: verbatim piece
    containment first, then distinctive-token overlap over the whole text and
    each fragment (stages 4-6 re-render entries from extracted fields, so no
    verbatim piece survives the 5c/5d formatters, and stage 6 keeps the
    title/institution fields while dropping long narratives). None = too
    short to verify either way."""
    pieces = _entry_pieces(text)
    if any(piece in haystack for piece in pieces):
        return True
    # Seeded False, not bool(pieces): a short label-prefixed entry ("Email:
    # x@y.org") produces a piece but every chunk below falls under
    # RENDER_TOKEN_MIN_COUNT long-word tokens, so the loop never runs and
    # this used to fall through to a hard False (definitively unrendered)
    # instead of None (too short to verify). Matches _record_rendered's
    # sibling pattern in render.py, which never sets verifiable from pieces
    # alone (#537).
    verifiable = False
    for chunk in [str(text or "")] + entry_fragments(text):
        tokens = _long_word_tokens(chunk)
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        verifiable = True
        if len(tokens & haystack_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP:
            return True
    return False if verifiable else None


def lint_classified_unrendered(stage3b: Dict,
                               blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Taxonomy codes classified at 3b none of whose entries appear anywhere
    in the stage-6 output (paragraphs or tables); 'T' is skipped (appendix
    catch-all)."""
    h = _haystacks(blocks)
    by_code: Dict[str, List[Dict]] = {}
    for e in stage3b.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        code = e.get("taxonomy_code")
        if not code or code == "T":
            continue
        by_code.setdefault(code, []).append(e)

    findings = []
    lost = 0
    for code in sorted(by_code):
        entries = by_code[code]
        verdicts = [(_entry_rendered(e.get("text"), h.text, h.tokens), e)
                    for e in entries]
        verifiable = [(v, e) for v, e in verdicts if v is not None]
        if not verifiable or any(v for v, _ in verifiable):
            continue
        findings.append(_finding(
            "classified_unrendered", "WARN",
            f"taxonomy code {code}: none of its {len(entries)} classified "
            f"entries appear in the output document",
            [str(e.get("text", ""))[:80] for _, e in verifiable[:3]]))
        lost += len(entries)
    # As with missed_headers, the magnitude that matters is how much of the
    # document went missing across all codes, not that one code did (#438).
    severity = _magnitude_severity(lost, CLASSIFIED_UNRENDERED_WARN_ENTRIES)
    for f in findings:
        f["severity"] = severity
    return findings


# --------------------------------------------------------------------------
# Taxonomy codes stage 3b can assign that stage 6 has no render route for.

# Codes a `_fill_*` method renders directly but that are not in
# RENDER_ROUTED_CODES: E/G/J match on the source heading rather than a
# taxonomy code (stage6/sections/passthrough.py), so they have no code
# dispatch to add them to. Their writers report back exactly which entry
# dicts they wrote (by object identity, not by code), which `generate()`
# uses to exclude those specific entries from the appendix a second time
# (#294, #260), so their entries do NOT duplicate. (N4 used to be a fourth
# member: it renders via mentoring.py but had never been added to
# RENDER_ROUTED_CODES, so it duplicated into the appendix -- fixed in #587 by
# routing it, which is why it is no longer exempted here.)
#
# This is itself a second, hand-maintained source of truth for stage-6
# routing (review on #588) -- a code silently added here without a real
# passthrough route would make this lint wrongly stay quiet about it.
# test_taxonomy_code_render_coverage.py's
# test_render_exceptions_still_wired_into_generate() is a cheap guard
# against the hook these three codes depend on being removed without
# updating this set; it can't prove a *new* addition is correct, only that
# the existing ones haven't silently gone stale.
_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES = frozenset({'E', 'G', 'J'})


def unrouted_code_counts(stage3b: dict) -> dict[str, int]:
    """{taxonomy code: entry count} for every code classified at 3b that
    stage 6 has no render route for -- the doctor's `metrics` block (#816's
    `unrouted_code_entries`) reads this SAME dict rather than re-deriving
    which codes are unrouted a second way. Factored out of
    `lint_taxonomy_code_coverage`, which builds its findings from it."""
    by_code: dict[str, int] = {}
    for e in stage3b.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        code = e.get("taxonomy_code")
        if not code or code == "T" or code == "M1":
            # M1 is normally routed; it only falls through when the Stage
            # 4.5 summary itself didn't render, a distinct, already-covered
            # case (#317). Flagging it here would false-positive the common
            # path.
            continue
        if code in RENDER_ROUTED_CODES or code in _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES:
            continue
        by_code[code] = by_code.get(code, 0) + 1
    return by_code


def lint_taxonomy_code_coverage(stage3b: Dict) -> List[Dict]:
    """Entries classified into a taxonomy code stage 6 has no render route
    for at all -- they land in the Appendix by construction, regardless of
    confidence or content (#529, e.g. N2 "Institutional Training Grants and
    Mentored Trainee Grants").

    Distinct from lint_classified_unrendered just above: that lint asks
    whether an entry's text appears ANYWHERE in the rendered output, and
    appendix content passes that check (the text is there, just in the
    Appendix), so it cannot see this class -- #529's own investigation hit
    exactly that blind spot. This lint instead asks a structural question
    that needs no rendered document at all: does this code have a dispatch
    path in generate().

    #816: always INFO now -- it fired on 10 of 40 runs in the 2026-09-11
    batch reporting the same handful of known orphan codes (N1/N2/N3/M4A)
    every time, which is a routing-coverage METRIC (moved to the doctor's
    `metrics` block as `unrouted_code_entries`), not a per-run WARN."""
    by_code = unrouted_code_counts(stage3b)
    return [
        _finding(
            "taxonomy_code_coverage", "INFO",
            f"taxonomy code {code}: {count} entries classified but stage 6 "
            f"has no render route for this code -- routed to the Appendix "
            f"by construction, not by content or confidence",
            [code],
        )
        for code, count in sorted(by_code.items())
    ]


# --------------------------------------------------------------------------
# Stage 6 dedup drops that were not duplicates.

# A dropped entry this well contained (token-multiset-wise, #718) in the kept entry is a
# true duplicate; anything below carries content the kept entry lacks. On
# 2Q1_ZQ the one true duplicate scored 1.00 and the seven real losses
# 0.60-0.89 (#227).
DEDUP_SAFE_CONTAINMENT = 0.9
_DEDUP_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _alphanumeric_tokens(text) -> Counter:
    """a-z0-9 token multiset for one string (lint 11 dedup-containment
    coverage). A Counter, not a set, so a dropped passage that repeats a
    word is not fully covered by a kept passage that says it once (#718)."""
    return Counter(_DEDUP_TOKEN_RE.findall(_norm(text)))


def lint_dedup_drops(report: Dict) -> List[Dict]:
    """Stage-6 dedup decisions whose dropped text is NOT near-fully contained
    in the kept entry: at loose similarity thresholds these are distinct
    records lost, not duplicates (#227)."""
    suspect = []
    for d in report.get("dedup_decisions", []):
        dropped = _alphanumeric_tokens(d.get("dropped_text", ""))
        kept = _alphanumeric_tokens(d.get("kept_text", ""))
        if not dropped:
            continue
        coverage = sum((dropped & kept).values()) / sum(dropped.values())
        if coverage >= DEDUP_SAFE_CONTAINMENT:
            continue
        suspect.append(
            f"{d.get('code', '?')} ({d.get('metric', '?')}, {coverage:.0%} "
            f"covered by kept): dropped '{d.get('dropped_text', '')[:80]}' "
            f"vs kept '{d.get('kept_text', '')[:80]}'")
    if not suspect:
        return []
    return [_finding(
        "dedup_drops", "WARN",
        f"{len(suspect)} dedup drop(s) poorly covered by the kept entry — "
        f"possible distinct records lost (#227)",
        suspect[:6])]


# --------------------------------------------------------------------------
# Records fabricated from the WCM template's own scaffolding text (#829).
#
# #959 fixed one instance of this: stage 6's board-certification writer now
# skips an F2 entry whose extracted_fields are the table's own header cells
# ("Full Name of Board", "Certificate #") read back by stage 4 as if they
# were a real certification (A5IZ6Q). That fix is local to one section --
# `stage6/sections/board_certification.py`'s own `_CERTIFICATION_HEADER_
# CELLS` -- so nothing catches the same LLM misread recurring on a
# different section's header row. This generalizes the check to every
# taxonomy code, reusing the SAME phrase set `is_template_label_line`
# already loads (`core/template_boilerplate.py`) rather than a new list.
#
# A single short matched value is not enough evidence: "Total" and "100%"
# are themselves registered template phrases (the blank %-effort table's own
# worked example), and a real, fully-filled J (Percent Effort) table
# legitimately ends in a "Total | 100%" row -- measured on A5IZ6Q's own
# effort table, whose four real percentages actually sum to 100. Two guards,
# both required, keep that row from matching: at least
# INVENTED_RECORD_MIN_VALUES populated fields, ALL of them recognized
# labels, and their combined text at least `_MIN_EXACT_LEN` chars long --
# the same distinctiveness floor `is_template_instruction`'s own exact-match
# rule uses, for the same reason (a short generic label collides with real
# content; a longer, more specific one essentially never does). Measured
# over 278 unique local stage-4 artifact sets (the rg_farm, batch-3,
# batch-4, the local _autopsy stage_4 set, src/unified_pipeline/outputs,
# and A5IZ6Q -- corpus paths listed with run_doctor.py's LINT_PREVALENCE
# comment; 149 of the 278 have a locally retained rendered docx, needed to
# evaluate this guard's render-match requirement): zero false positives,
# but the lint is not a one-incident-only detector -- it fires on 3 uids /
# 4 records total, not on A5IZ6Q alone. A5IZ6Q supplies both of the shapes
# below (its own F2 header row here, plus its F1 licensure record via part
# (b)); 976WPY (entry 48) and IO4DEA (entry 208) are two further, organic
# part-(b) hits, each the identical fabricated "New York State" F1 record
# built from the same unfilled licensure-instruction paragraph as A5IZ6Q's
# -- the same #829 misread recurring verbatim on two more CVs.
INVENTED_RECORD_MIN_VALUES = 2


# F1 is Licensure (PIPELINE_README.md) -- the one taxonomy code this lint
# also checks against the entry's raw SOURCE TEXT rather than its extracted
# fields. A5IZ6Q's invented licence extracted only one real-looking value
# (`state_country: "New York State"`, itself lifted from inside the
# instruction paragraph, not a template label) from an unfilled licensure
# PROMPT paragraph -- `_is_invented_record` cannot see a single non-label
# value, but the entry's source text is a near-verbatim copy of a known
# instruction (#829).
INVENTED_RECORD_LICENSURE_CODE = "F1"


def _nonempty_field_values(fields: dict) -> list[str]:
    """Every non-empty STRING value a stage-4 entry's extracted_fields
    holds, flattening a list-valued field. A dict-valued field
    (`additional_award`, `additional_grant`, ...) is not flattened -- an
    entry whose only values live there reports none at all, which the
    caller reads as "not enough evidence", the same precision-biased
    default `template_boilerplate.py` uses throughout."""
    values: list[str] = []
    for value in fields.values():
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and item.strip():
                values.append(item)
    return values


def _is_invented_record(fields: dict) -> bool:
    """True when a stage-4 record is built entirely from the WCM template's
    own labels rather than real CV content -- see the module comment above
    for why both guards (value count, combined length) are needed."""
    values = _nonempty_field_values(fields)
    if len(values) < INVENTED_RECORD_MIN_VALUES:
        return False
    joined = "|".join(values)
    if len(joined) < _MIN_EXACT_LEN:
        return False
    return all(is_template_label_line(v) for v in values)


def _rendered_row_value_sets(
    table_rows: list[list[list[str]]],
) -> set[frozenset[str]]:
    """The normalized, non-empty-cell VALUE SET of every rendered table row,
    across every table in the document -- order- and position-independent,
    so a fabricated row missing its trailing column (`['Full Name of
    Board', 'Certificate #', '']`) compares as a set of 2 and is never
    confused with the table's own 3-cell header row, which carries the
    columns' parentheticals and so never collides with a data row's set."""
    rows: set[frozenset[str]] = set()
    for tbl in table_rows:
        for row in tbl:
            cells = frozenset(_norm(c) for c in row if c and str(c).strip())
            if cells:
                rows.add(cells)
    return rows


def lint_invented_records(stage4: dict,
                          table_rows: list[list[list[str]]]) -> list[dict]:
    """A record built from the WCM template's own scaffolding rather than
    real CV content, reaching the delivered document (#829):

    (a) a RENDERED stage-4 record whose extracted field values are all
        known template labels -- #959's board-certification header-row fix,
        generalized to every taxonomy code (`_is_invented_record`).
    (b) an F1 (Licensure) entry whose SOURCE TEXT is a known template
        instruction, exact or near-match -- A5IZ6Q's invented New York
        licence, extracted from a licensure prompt paragraph the faculty
        member never filled in.
    """
    rendered = _rendered_row_value_sets(table_rows)
    findings = []
    for e in stage4.get("entries", []):
        code = e.get("taxonomy_code")
        if code == "T":
            continue
        fields = e.get("extracted_fields") or {}
        if _is_invented_record(fields):
            values = _nonempty_field_values(fields)
            if frozenset(_norm(v) for v in values) in rendered:
                findings.append(_finding(
                    "invented_records", "WARN",
                    f"entry {e.get('element_idx_start')} ({code}): every "
                    f"extracted field value is a known WCM template label, "
                    f"rendered as if it were a real record (#829)",
                    [f"{k}: {v}" for k, v in fields.items() if v]))
        if code == INVENTED_RECORD_LICENSURE_CODE:
            text = str(e.get("text", ""))
            if is_template_instruction(text) or is_near_template_instruction(text):
                findings.append(_finding(
                    "invented_records", "WARN",
                    f"entry {e.get('element_idx_start')} (F1): source text "
                    f"is a known WCM template instruction, not a real "
                    f"licence (#829)",
                    [text[:120]]))
    return findings


# --------------------------------------------------------------------------
# A wrong start date left beside an empty end date (#729, split from #556).

def lint_wrong_start_date(stage4: dict) -> list[dict]:
    """An entry whose schema declares both dates, whose `end_date` is empty,
    and whose own text carries exactly one closed 4-digit range with no
    present/ongoing marker: the range is in the source but the entry renders
    "<start>-Present". `reconcile_date_range` repairs the blank-start and
    agreeing-start shapes; what reaches this lint is the shape it
    deliberately leaves alone, chiefly the FSMB one (text "2025-2026"
    extracted as start_date=2026). WARN when the extracted start equals the
    range's END year (the model took the wrong end of the range), INFO for
    any other disagreement. Report-only by decision (2026-09-09): the
    extracted value is never changed."""
    findings = []
    for e in stage4.get("entries", []):
        if e.get("taxonomy_code") not in DATE_RANGE_TAXONOMY_CODES:
            continue
        fields = e.get("extracted_fields") or {}
        if fields.get("end_date"):
            continue
        text = str(e.get("text", ""))
        closed_range = find_single_closed_range(text)
        if closed_range is None:
            continue
        range_start, range_end = closed_range
        start = str(fields.get("start_date") or "").strip()
        severity = "WARN" if start == range_end else "INFO"
        findings.append(_finding(
            "wrong_start_date", severity,
            f"entry {e.get('element_idx_start')} ({e.get('taxonomy_code')}): "
            f"start_date '{start}' with an empty end_date, but the text "
            f"carries the single closed range {range_start}-{range_end} -- "
            f"renders '{start}-Present' (#729)",
            [text[:120]]))
    return findings
