"""Lints for what the extraction stages pulled out, and what became of it (#493).

One responsibility: compare the entries and fields stages 3b/4 produced against
the source they came from and the document they landed in -- grants filed under
the wrong funding heading, entries whose field extraction covered almost none of
their text, taxonomy codes that vanished between classification and render,
dedup drops that were not duplicates, records fabricated from the template's
own scaffolding, values filed under a key no renderer reads, years given the
wrong century, entries holding several records that stage 4 returned as
one, grant lists cut into records at the wrong line, grants filed under a
funding heading their own record contradicts, separate years rendered as
one range over them, and stage-3b fragments whose text no record holds.

The line against `render.py` is which side of the comparison is the subject.
These five are about the extracted record; the render lints are about the page.
`lint_classified_unrendered` reads the output blocks, but only to decide whether
a 3b classification survived -- the finding is about the classification.
`lint_offschema_fields` reads them only to grade the stage-4 values it found.

The thirteen constants, regexes and helpers below them are used by nothing else
in `run_doctor.py`, so they move together and stop being module-global.
`run_doctor` re-exports every name it exported before; `_entry_rendered` and
`_funding_haystacks` have since been fixed in place (#537, #492) -- see their
docstrings and comments for what changed.
"""
import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import NamedTuple

from unified_pipeline.core.docx_structure_extractor import _is_date_only_text
from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.core.retired_taxonomy_codes import live_taxonomy_code
from unified_pipeline.core.template_boilerplate import (
    _MIN_EXACT_LEN,
    is_foreign_template_instruction,
    is_near_template_instruction,
    is_template_instruction,
    is_template_label_line,
)
from unified_pipeline.core.text_norm import (
    SUBSTANTIVE_LINE_CHARS,
    looks_like_record,
    norm,
    squash,
)
from unified_pipeline.core.two_digit_year import (
    TWO_DIGIT_YEAR_PIVOT,
    expand_two_digit_year,
)
from unified_pipeline.core.validators.grant_status_corrector import (
    _AWARDED_HEADING_RE,
    _PENDING_HEADING_RE,
)
from unified_pipeline.stage3b.fragment_merge import (
    SKIP_LABEL,
    SKIP_ORGANIZATION,
    fragment_text_in_parent,
    merge_skip_reason,
)
from unified_pipeline.stage4.coercion import (
    DATE_RANGE_TAXONOMY_CODES,
    GRANT_EFFORT_TAXONOMY_PREFIX,
    IDENTIFIER_TAXONOMY_CODES,
    find_single_closed_range,
)
from unified_pipeline.stage4.schemas import (
    FIELD_SCHEMA_CONFIG_PATH,
    FIELD_SCHEMAS,
    NUMBERED_FIELD_RE,
    STAGE4_RECORDS_KEY,
)
from unified_pipeline.stage6.dedup import (
    _PART_NUMBER_RE,
    _TRIAL_PHASE_RE,
    _citation_identity_text,
    _dates_compatible,
    _different_rank,
    _part_numbers,
    _trial_phases,
)
from unified_pipeline.stage6.fan_out import (
    _FORMATTED_KEYS,
    _RENDERED_FIELDS,
    _TEXT_RENDERED_CODES,
    FANNED_OUT_FROM,
    LAST_STAGE4_RECORD,
    _is_blank,
    fan_out_multi_record_entries,
)
from unified_pipeline.stage6.formatting.dates import (
    EXTRA_SPAN_CODES,
    EXTRA_SPAN_KEYS,
    format_date_range,
)
from unified_pipeline.stage6.normalization.institutions import (
    _get_cleaned_institution_name,
)
from unified_pipeline.stage6.normalization.pii import (
    CAT_HOME_CONTACT,
    SCOPE_PERSONAL_AND_APPENDIX,
    WITHHOLD_POLICY,
    _pii_matches,
)
from unified_pipeline.stage6.normalization.publication import resolve_publication
from unified_pipeline.stage6.parsing import split_appointment_title
from unified_pipeline.stage6.pii_pass import PERSONAL_DATA_CODE
from unified_pipeline.stage6.record_dedup import RECORD_RULE_METRIC_PREFIX
from unified_pipeline.stage6.sections.positions import POSITION_TAXONOMY_CODES
from unified_pipeline.stage6.sections.research_support import (
    PI_NAME_LABEL,
    PROJECT_TITLE_LABEL,
    YOUR_ROLE_LABEL,
    grant_end_year,
    year_at_or_after,
)
from unified_pipeline.stage_5c_teaching_formatter import TEACHING_CODES
from unified_pipeline.stage_5d_citation_formatter import PUBLICATION_CODES
from unified_pipeline.stage_6_word_template import (
    RENDER_ROUTED_CODES,
    grant_status_rebucket_target,
    rendered_extraction_coverage,
)

from ..shared import (
    _LINE_SENTINEL,
    _NAME_WORD_RE,
    OWNER_SURNAME_MIN_CHARS,
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    TABLE_ROW_JOINER,
    Haystack,
    _entry_pieces,
    _fields_entries,
    _FieldsEntry,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
    _owner_surname_words,
    _piece_in_template,
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
# compared (`norm`, trailing colon stripped).
_FUNDING_BOUNDARY_TITLES = frozenset({
    "patents & inventions",
})


def _entry_status(entry: dict) -> str | None:
    """The entry's grant status: the stage-4 field when the vocabulary
    `grant_status_rebucket_target` (what `lint_bucket_status` judges by)
    recognises it, else the labelled fragment in the raw text. Stage 4 has no
    response schema, so a stray value must not mask a status the text carries
    (#720)."""
    status = (entry.get("extracted_fields") or {}).get("status")
    if status and grant_status_rebucket_target(str(status))[0] is not None:
        return str(status)
    match = _STATUS_LABEL_RE.search(str(entry.get("text", "")))
    return match.group(1).strip() if match else None


def _funding_haystacks(blocks: list[tuple[str, str]]) -> dict[str, Haystack]:
    """Per-bucket Haystack of everything rendered under each of stage 6's
    funding subsection headers."""
    segments: dict[str, list[tuple[str, str]]] = {c: [] for c, _ in _FUNDING_SECTIONS}
    current = None
    for kind, text in blocks:
        stripped = str(text).strip()
        if kind == "p":
            normed = norm(stripped).rstrip(":")
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


def lint_bucket_status(stage4: dict, blocks: list[tuple[str, str]]) -> list[dict]:
    """Grant status (extracted field, else the 'Status:' label in the raw
    entry text) vs the funding subsection the grant actually rendered under.
    Stage 6 rebuckets mis-bucketed grants at render time (#214), so the
    stage-4 code alone proves nothing; a WARN here means the rendered
    document files the grant under the wrong funding heading, or lost it."""
    titles = dict(_FUNDING_SECTIONS)
    rendered = _funding_haystacks(blocks)
    shared = _shared_entry_pieces(stage4.get("entries", []))
    findings = []
    for e in stage4.get("entries", []):
        code = e.get("taxonomy_code")
        if code not in ("M2A", "M2B", "M2C"):
            continue
        status = _entry_status(e)
        target, _note = grant_status_rebucket_target(status or "")
        if not target or target == code:
            continue
        verdicts = {bucket: _entry_rendered(e.get("text"), h.text, h.tokens, shared)
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
#
# Owner: the doctor's under_extraction lint; provenance and any re-derivation
# are tracked at #721. Derivation: hand-set, not measured. All three values
# date from the doctor's first commit (823ef4b7, 2026-07-02), tuned against
# real CVs during the 89HQVQ forensic (#208-#213) to keep false positives near
# zero; no distribution was recorded. The one check since: the 2026-07-09
# sweep (docs/analysis/doctor-sweep-results-2026-07-09.md) fired on 2 of 12
# distinct CVs, both verified real losses (2 TP / 0 FP). The 2Q1_ZQ honors
# mega-entry at 19% coverage (#229) is the reference true positive. Re-derive
# against a labelled corpus before changing any of the three.
UNDER_EXTRACTION_MAX_PCT = 40.0
UNDER_EXTRACTION_MIN_CHARS = 800
UNDER_EXTRACTION_MIN_RECORDS = 2


# looks_like_record only sees pipe/tab rows; fused award/honor lines are
# plain newline lines carrying a leading or trailing year ("2020 AECT ...",
# "... August 2025.") — the 2Q1_ZQ honors mega-entry (19% coverage) was
# invisible without counting them (#229).
_YEAR_EDGE_LINE_RE = re.compile(
    r"^\s*(?:19|20)\d{2}\b|\b(?:19|20)\d{2}\s*[.)]?\s*$")


def lint_under_extraction(stage4: dict) -> list[dict]:
    """Large multi-record entries whose stage-4 field extraction covered
    almost none of the text: the rest of the records silently vanish."""
    findings = []
    for e in stage4.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        # The coverage stage 6 acts on: stage 4's records fanned out first,
        # so records no renderer writes don't count as extracted (#1299).
        rendered = fan_out_multi_record_entries(
            [e], FIELD_SCHEMAS, records_key=STAGE4_RECORDS_KEY)[-1]
        pct = (rendered_extraction_coverage(rendered) or {}).get("extraction_coverage_percent")
        if pct is None or pct >= UNDER_EXTRACTION_MAX_PCT:
            continue
        text = str(e.get("text", ""))
        if len(text) <= UNDER_EXTRACTION_MIN_CHARS:
            continue
        records = sum(
            1 for line in text.split("\n")
            if looks_like_record(line)
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
# Owner: the doctor's classified_unrendered lint; provenance tracked at #721.
CLASSIFIED_UNRENDERED_WARN_ENTRIES = 2


def _shared_entry_pieces(entries: list[dict]) -> frozenset:
    """Pieces that occur in more than one entry of the same document: the
    boilerplate ('Department of Medicine', a repeated institution line) that
    surfaces verbatim in the output for reasons unrelated to any one entry
    (#744). "More than one" counts entries with DIFFERENT text: two entries
    with identical text are one record listed twice (stage 4 dedups them into
    the one rendered record), and that record's own text is real content, not
    boilerplate."""
    texts_by_piece: dict[str, set] = {}
    for e in entries:
        squashed = squash(e.get("text"))
        for piece in _entry_pieces(e.get("text")):
            texts_by_piece.setdefault(piece, set()).add(squashed)
    return frozenset(p for p, texts in texts_by_piece.items() if len(texts) > 1)


def _only_boilerplate_hit(text: str | None, pieces: list[str],
                          distinctive: list[str], haystack: str) -> bool:
    """True when the entry's verbatim hit in the output is boilerplate and
    nothing else: it has no distinctive piece, a boilerplate piece found in
    the haystack, and no fragment too short to be a piece (a short value such
    as the phone number after an 'Office telephone:' label is content this
    check cannot see, so the entry stays unverifiable rather than lost)."""
    if distinctive or not any(p in haystack for p in pieces):
        return False
    return sum(1 for f in entry_fragments(text) if squash(f)) == len(pieces)


def _entry_rendered(text: str | None, haystack: str, haystack_tokens: set,
                    shared_pieces: frozenset = frozenset()) -> bool | None:
    """Whether an entry's text surfaces in the output: verbatim piece
    containment first, then distinctive-token overlap over the whole text and
    each fragment (stages 4-6 re-render entries from extracted fields, so no
    verbatim piece survives the 5c/5d formatters, and stage 6 keeps the
    title/institution fields while dropping long narratives). None = too
    short to verify either way.

    A piece that is boilerplate does not count as containment evidence: one in
    `shared_pieces` (see `_shared_entry_pieces`) or one present in the pristine
    WCM template (#744). An entry whose ONLY evidence was such a hit is
    verifiable-and-unmatched (False); see `_only_boilerplate_hit`.
    """
    pieces = _entry_pieces(text)
    distinctive = [p for p in pieces
                   if p not in shared_pieces and not _piece_in_template(p)]
    if any(piece in haystack for piece in distinctive):
        return True
    # Seeded False, not bool(pieces): a short label-prefixed entry ("Email:
    # x@y.org") produces a piece but every chunk below falls under
    # RENDER_TOKEN_MIN_COUNT long-word tokens, so the loop never runs and
    # this used to fall through to a hard False (definitively unrendered)
    # instead of None (too short to verify). Matches _record_rendered's
    # sibling pattern in render.py, which never sets verifiable from pieces
    # alone (#537). The one exception is an entry whose only evidence was a
    # boilerplate hit (#744): that hit is what made it look rendered.
    verifiable = _only_boilerplate_hit(text, pieces, distinctive, haystack)
    for chunk in [str(text or "")] + entry_fragments(text):
        tokens = _long_word_tokens(chunk)
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        verifiable = True
        if len(tokens & haystack_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP:
            return True
    return False if verifiable else None


#: Codes `lint_classified_unrendered` does not judge; see its docstring.
_CLASSIFIED_UNRENDERED_SKIP_CODES = frozenset({"T", "M1"})

#: A stage-4 record judges an entry rendered (#890) only from at least this
#: many CONTENT values -- non-empty string fields that are not dates. Stage 6
#: writes an entry from its extracted fields, so a field-parsed table row, a
#: 5c/5d-reformatted citation and a stage-5 abbreviation expansion keep the
#: content and lose the raw tokens the overlap test counts. Two, not one: a
#: record whose only content value is a label ("program_name") is exactly the
#: #1092 shape -- the detail was lost and one hit proves nothing -- so fewer
#: than two falls through to the token test.
RENDERED_FIELDS_MIN_VALUES = 2
#: More than this fraction of an entry's content values must sit on ONE rendered
#: line. A majority, not "two of them": an authors-only line ("1. Doe J, Roe K.
#: 2020.") carries one of a citation's authors/title/book/publisher, and the
#: rest is lost. Dates never count, either way: "07/1987-07/1994" alone is a
#: date column, not the entry it belonged to.
RENDERED_FIELDS_MAJORITY = 0.5
#: A value this long (squashed) matches as a substring and is the only kind
#: `Stage4Evidence.shared_values` and the template filter judge; a shorter one
#: ("MA", "Ohio") matches on word/cell boundaries only, since raw containment
#: finds "ma" inside "pharmacology".
RENDERED_FIELDS_MIN_VALUE_CHARS = 6
#: Stage-4 fields whose value is a category label, not a piece of the entry,
#: and that no section writes: S9's `media_type` ("News Broadcast/Online
#: News") is in no `stage6.fan_out._RENDERED_FIELDS` set and on no rendered
#: line (web225 S9), so counting it lets one unwritten label outvote the title
#: and venue that DID render. Dropped from the content values, never from the
#: record: what is left must still be a majority on one line. Pinned against
#: the renderer's field sets by `test_category_fields_are_written_by_no_section`.
UNWRITTEN_CATEGORY_FIELDS = frozenset({"media_type"})

#: The colon-less opener of the policy's home-address/phone row. A source line
#: reads "Home Phone (914) ..." with no colon, so `_pii_matches` (whose label
#: rows end in a colon) cannot see it, yet `_fill_personal_data` withholds
#: home contact unconditionally at write time (#821). Built from the policy
#: row itself so the two cannot name different labels.
_HOME_CONTACT_LABEL_RE = re.compile(
    "|".join(f"(?:{rule.label})" for rule in WITHHOLD_POLICY
             if rule.category == CAT_HOME_CONTACT and rule.label),
    re.X | re.I)


class Stage4Evidence(NamedTuple):
    """What `lint_classified_unrendered` reads from stage 4: each record by
    its `(element_idx_start, element_idx_end)` span -- the start alone is not
    unique (43 records of one batch-3 CV share one); a span two records share
    is dropped, not guessed -- and the field values (squashed) that occur in more
    than one record -- boilerplate ("Weill Cornell Medicine", "New York")
    that surfaces in the output for reasons unrelated to any one entry, the
    field-level twin of `_shared_entry_pieces` (#744). Only values of
    RENDERED_FIELDS_MIN_VALUE_CHARS or more: a date range's years are shared
    by many records and are still evidence beside a distinctive value."""
    records: dict
    shared_values: frozenset
    #: span -> {squashed stage-4 `institution` -> squashed name stage 6 writes
    #: for it} (`_get_cleaned_institution_name`, from the stage-5b artifact).
    #: Stage 5b turns "Depts. Neurology, U. of Iowa" into "Departments of
    #: Neurology, University of Iowa ...", after which the raw string is on
    #: no rendered line (web196 C).
    institution_names: dict


def _span(entry: dict) -> tuple:
    """The key a stage-3b entry and its stage-4 record share."""
    return (entry.get("element_idx_start"), entry.get("element_idx_end"))


def _record_values(record: dict) -> set[str]:
    """The record's distinct, squashed, non-empty extracted string values."""
    return {squash(v) for v in _nonempty_field_values(
        record.get("extracted_fields") or {})} - {""}


def _institution_names(stage5b: dict | None) -> dict:
    """{span: {squashed raw institution: squashed name stage 6 writes}} for the
    stage-5b records that carry a name. A span two 5b records share is dropped,
    as in `_stage4_evidence`."""
    entries = (stage5b or {}).get("entries", [])
    span_counts = Counter(_span(e) for e in entries)
    names = {}
    for e in entries:
        fields = e.get("extracted_fields")
        raw = squash(fields.get("institution")) if isinstance(fields, dict) else ""
        written = squash(_get_cleaned_institution_name(e))
        if span_counts[_span(e)] == 1 and written:
            names[_span(e)] = {raw: written}
    return names


def _stage4_evidence(stage4: dict | None,
                     stage5b: dict | None = None) -> Stage4Evidence | None:
    """Index stage 4 for the field-value test; None when stage 4 is absent,
    which keeps the pre-#890 behaviour exactly. `stage5b` (optional) adds the
    institution names stage 6 writes in place of stage 4's raw ones."""
    if not stage4:
        return None
    entries = stage4.get("entries", [])
    span_counts = Counter(_span(e) for e in entries)
    # A span two records share cannot say which one a stage-3b entry is: no
    # record, so no field evidence, rather than whichever came last.
    records = {_span(e): e for e in entries if span_counts[_span(e)] == 1}
    counts = Counter(v for r in entries for v in _record_values(r)
                     if len(v) >= RENDERED_FIELDS_MIN_VALUE_CHARS)
    return Stage4Evidence(records, frozenset(v for v, n in counts.items() if n > 1),
                          _institution_names(stage5b))


def _value_on_line(value: str, line: str) -> bool:
    """Whether a squashed field value is on a squashed rendered line. A value
    under RENDERED_FIELDS_MIN_VALUE_CHARS ("MA", "M.D.") must sit on a
    word/cell boundary; a longer one is distinctive enough to match as a
    substring."""
    if len(value) >= RENDERED_FIELDS_MIN_VALUE_CHARS:
        return value in line
    return re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", line) is not None


def _content_values(record: dict, evidence: Stage4Evidence) -> set[str]:
    """The record's squashed values that say WHAT the entry is: not a date
    ("1987-07", "May 2019 - Present"), not boilerplate shared with other
    records, not a scaffolding phrase of the output template."""
    fields = {k: v for k, v in (record.get("extracted_fields") or {}).items()
              if k not in UNWRITTEN_CATEGORY_FIELDS}
    raw = _nonempty_field_values(fields)
    values = {squash(v) for v in raw if not _is_date_only_text(v)} - {""}
    return {v for v in values - evidence.shared_values
            if len(v) < RENDERED_FIELDS_MIN_VALUE_CHARS or not _piece_in_template(v)}


def _fields_rendered(entry: dict, lines: list[str], evidence: Stage4Evidence) -> bool:
    """Whether the entry's stage-4 record was extracted successfully and ONE
    rendered line (a paragraph, or a table row joined across its cells --
    `_table_lines`) carries a majority of its content values (#890).
    Co-location is the point: a state name can turn up on any line of the
    document, but a record's title and its book on the same line is that
    record. False also when the record has too few content values to judge.
    An institution value counts as on a line when the name stage 5b gave it
    is (`Stage4Evidence.institution_names`), unless that name is boilerplate
    shared across records."""
    span = _span(entry)
    record = evidence.records.get(span)
    if not record or not record.get("extraction_success"):
        return False
    values = _content_values(record, evidence)
    if len(values) < RENDERED_FIELDS_MIN_VALUES:
        return False
    names = {raw: written
             for raw, written in evidence.institution_names.get(span, {}).items()
             if written not in evidence.shared_values}

    def on_line(value: str, line: str) -> bool:
        return (_value_on_line(value, line)
                or (value in names and _value_on_line(names[value], line)))

    return any(sum(1 for v in values if on_line(v, line))
               > RENDERED_FIELDS_MAJORITY * len(values) for line in lines)


#: A letter or digit run of a squashed rendered line.
_LINE_RUN_RE = re.compile(r"[a-z]+|\d+")

#: Entry words (5+ letters) a one-value record's line may lack and still be
#: its rendering: one, a role or label word its section heading carries
#: ("reviewer" under "Journal Reviewing"). Two would clear the #1092 shape,
#: a label whose few-word detail ("Over 100 procedures performed") was lost.
SOLE_VALUE_MAX_MISSING_TOKENS = 1


def _sole_value_rendered(entry: dict, lines: list[str],
                         evidence: Stage4Evidence) -> bool:
    """Whether a record with ONE value outside its date fields -- too few for
    `_fields_rendered` -- still rendered: a line made only of the entry's own
    text (every letter and digit run of it is in the entry) carries the
    value, and lacks at most `SOLE_VALUE_MAX_MISSING_TOKENS` of the entry's
    words. The value may be one other records share: the line is this
    entry's.
    EBYSBC OTBUCZ Q4D 110, a role cell then a journal and its years, renders
    as the reviewing table's "<journal> | <year>-Present" row, missing only
    the role word "reviewer", which the section heading says; the journal is
    also one its citations name. A label whose detail was dropped (#1092)
    leaves two or more of the entry's words missing."""
    record = evidence.records.get(_span(entry))
    if not record or not record.get("extraction_success"):
        return False
    fields = {k: v for k, v in (record.get("extracted_fields") or {}).items()
              if k not in UNWRITTEN_CATEGORY_FIELDS and not _DATE_NAMED_KEY_RE.search(k)}
    values = {squash(v) for v in _nonempty_field_values(fields)
              if not _is_date_only_text(v)} - {""}
    if len(values) != 1:
        return False
    value = next(iter(values))
    text = str(entry.get("text") or "")
    own, tokens = squash(text), _long_word_tokens(text)
    return any(_value_on_line(value, line)
               and all(run in own for run in _LINE_RUN_RE.findall(line))
               and sum(1 for t in tokens if t not in line) <= SOLE_VALUE_MAX_MISSING_TOKENS
               for line in lines)


def _personal_data_withheld(entry: dict) -> bool:
    """A Personal Data ('A') entry that carries a value the withhold policy
    removes at render time (date of birth, marital status, home contact):
    stage 6 renders that entry's remainder from named fields into a fixed
    table, so an absent entry here is withheld on purpose, not lost (#890,
    #820/#821). Stage 6 records the withheld items only as the docx notice and
    comment, never per entry, so the doctor re-asks the policy itself.
    Judgement call: ANY policy hit excludes the entry, not only an entry whose
    whole text is policy-covered -- the 'Name, address, Home Phone' block is
    partly withheld by design and its name rendering elsewhere is not visible
    to a text-overlap test."""
    if entry.get("taxonomy_code") != PERSONAL_DATA_CODE:
        return False
    text = str(entry.get("text") or "")
    return bool(_pii_matches(text, SCOPE_PERSONAL_AND_APPENDIX)
                or _HOME_CONTACT_LABEL_RE.search(text))


def _classified_entry_rendered(entry: dict, haystacks: Haystack, lines: list[str],
                               shared: frozenset,
                               evidence: Stage4Evidence | None) -> bool | None:
    """`_entry_rendered`'s verdict for a stage-3b entry, widened by what stage 4
    knows: None (not judged) for a policy-withheld Personal Data entry, True
    when its extracted field values surfaced (#890), or its one value did
    with nothing worth a verdict missing (`_sole_value_rendered`)."""
    if _personal_data_withheld(entry):
        return None
    if evidence is not None and (_fields_rendered(entry, lines, evidence)
                                 or _sole_value_rendered(entry, lines, evidence)):
        return True
    return _entry_rendered(entry.get("text"), haystacks.text, haystacks.tokens, shared)


def lint_classified_unrendered(stage3b: dict,
                               blocks: list[tuple[str, str]],
                               stage4: dict | None = None,
                               stage5b: dict | None = None) -> list[dict]:
    """Taxonomy codes classified at 3b none of whose entries appear anywhere
    in the stage-6 output (paragraphs or tables). Skipped: 'T' (appendix
    catch-all) and 'M1', which stage 6 never renders verbatim when a research
    summary rendered -- the summary paraphrases it, by design -- and routes to
    the Appendix, where this lint does see it, when none did (YTPMZK's 2 M1
    entries were flagged on every doctor run).

    `stage4` (optional; absent keeps the old behaviour) lets an entry count as
    rendered when its extracted field values did, and lets a Personal Data
    entry the withhold policy removes stay out of the verdict (#890).
    `stage5b` (optional, read only beside `stage4`) supplies the institution
    names stage 6 writes in place of stage 4's abbreviated ones."""
    h = _haystacks(blocks)
    shared = _shared_entry_pieces(stage3b.get("entries", []))
    evidence = _stage4_evidence(stage4, stage5b)
    lines = h.text.split(_LINE_SENTINEL)
    by_code: dict[str, list[dict]] = {}
    for e in stage3b.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        code = e.get("taxonomy_code")
        if not code or code in _CLASSIFIED_UNRENDERED_SKIP_CODES:
            continue
        by_code.setdefault(code, []).append(e)

    findings = []
    lost_codes = []
    lost = 0
    for code in sorted(by_code):
        entries = by_code[code]
        verdicts = [(_classified_entry_rendered(e, h, lines, shared, evidence), e)
                    for e in entries]
        verifiable = [(v, e) for v, e in verdicts if v is not None]
        if not verifiable or any(v for v, _ in verifiable):
            continue
        findings.append(_finding(
            "classified_unrendered", "INFO",
            f"taxonomy code {code}: none of its {len(entries)} classified "
            f"entries appear in the output document",
            [str(e.get("text", ""))[:80] for _, e in verifiable[:3]]))
        lost_codes.append(code)
        lost += len(entries)
    if not findings:
        return findings
    # As with missed_headers, the magnitude that matters is how much of the
    # document went missing across all codes, not that one code did (#438).
    # Exactly one finding carries that run-level severity (#719): the lone
    # per-code finding when one code was lost, else a run-level finding ahead
    # of per-code INFO evidence -- a code that lost one entry must not read
    # WARN because a different code on the same run lost several.
    severity = _magnitude_severity(lost, CLASSIFIED_UNRENDERED_WARN_ENTRIES)
    if len(findings) == 1:
        findings[0]["severity"] = severity
        return findings
    return [_finding(
        "classified_unrendered", severity,
        f"{lost} classified entries across {len(findings)} taxonomy codes "
        f"appear nowhere in the output document",
        [f"taxonomy code {code}" for code in lost_codes])] + findings


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
        # Stage 6 renders a retired code under its live one (#291).
        code = live_taxonomy_code(e.get("taxonomy_code"))
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


def lint_taxonomy_code_coverage(stage3b: dict) -> list[dict]:
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
# Owner: the doctor's dedup_drops lint; provenance tracked at #721.
DEDUP_SAFE_CONTAINMENT = 0.9
_DEDUP_TOKEN_RE = re.compile(r"[^\W_]+")


def _alphanumeric_tokens(text) -> Counter:
    """Alphanumeric (Unicode) token multiset for one string (lint 11 dedup-containment
    coverage). A Counter, not a set, so a dropped passage that repeats a
    word is not fully covered by a kept passage that says it once (#718)."""
    return Counter(_DEDUP_TOKEN_RE.findall(norm(text)))


# A dropped record that is fully token-covered by the kept entry passes the
# coverage check above whether it is a duplicate or a different record whose
# name is a sub-phrase of the kept one ("Optics" beside "European Optics",
# #666): the words alone cannot tell them apart. What can is the extracted name
# the two entries carry and what the page shows. Stage 6 writes only the name
# fields worth comparing into the decision (`_decision_fields`).
# An INFO finding listing this many drops stays readable; a WARN lists every
# drop, since each is a record the reader may have to restore (#666: one run's
# five lost yearly awards would crowd out the rest).
DEDUP_EVIDENCE_LIMIT = 6
#: Characters of each dropped and kept text a dedup evidence line quotes:
#: enough for the whole of a one-line record, since the review copy shows the
#: two side by side and the difference is often at the end ("4 hrs" vs "5 hrs").
#: The review copy marks a quote this long as cut (#1388).
DEDUP_TEXT_CHARS = 300


def _identity_conflicts(decision: Mapping) -> list[str]:
    """Normalised values the dropped entry filled under a name field the kept
    entry also filled with a different value. Empty when the decision carries
    no fields (a sidecar written before #666) or none conflict."""
    dropped = decision.get("dropped_fields") or {}
    kept = decision.get("kept_fields") or {}
    conflicts = []
    for key in dropped:
        if key not in kept:
            continue
        value = " ".join(_DEDUP_TOKEN_RE.findall(norm(dropped[key])))
        if value and value != " ".join(_DEDUP_TOKEN_RE.findall(norm(kept[key]))):
            conflicts.append(value)
    return conflicts


def _rendered_item_set(blocks: list[tuple[str, str]]) -> set[str]:
    """Every rendered paragraph and table cell or row, token-normalised."""
    items = set()
    for _kind, text in blocks:
        for line in text.split("\n"):
            items.add(" ".join(_DEDUP_TOKEN_RE.findall(norm(line))))
    return items


def _unrendered_identity(decision: Mapping, rendered_items: set[str]) -> str | None:
    """The first identity value the dropped entry carries that the kept entry
    does not, and that appears in the document as no paragraph or table cell
    of its own; None when the dropped record's name is on the page."""
    for value in _identity_conflicts(decision):
        if value not in rendered_items:
            return value
    return None


# A Roman numeral standing on its own tells two records of one series apart
# ("Example Seminar II" beside "Example Seminar III", "Sample Course I"
# beside "Sample Course II")
# where no "Part" or "Phase" word precedes it, so stage 6's `_part_numbers`
# and `_trial_phases` cannot read it (EBYSBC E4: OIYKZE-01). Upper case only,
# and V and X never alone: a citation's author initials are single capitals.
# A lone "I" counts only where a title segment ends after it, never before a
# lower-case word ("I taught"). A numeral after "Part" or "Phase" is left to
# those readers, which also read a listing ("Parts I and II") as a whole.
_STANDALONE_NUMERAL_RE = re.compile(
    r"\b(?:II|III|IV|VI|VII|VIII|IX)\b|\bI(?=\s*(?:$|[\t,.;:)\"\u201d\u2019]))")


def _standalone_numerals(text: str) -> set[str]:
    bare = _TRIAL_PHASE_RE.sub(" ", _PART_NUMBER_RE.sub(" ", text))
    return set(_STANDALONE_NUMERAL_RE.findall(bare))


# What tells two occasions of one record apart, each read the way stage 6's
# dedup reads it where stage 6 has a reader (#666, EBYSBC E4).
_OCCASION_MARKS = (("part", _part_numbers), ("phase", _trial_phases),
                   ("numeral", _standalone_numerals))


# A class label ("Class of 2031") names the cohort an award honours, not the
# year it was given, so two awards of one name differ when their classes do,
# and a year inside the label vouches for no award year (#666, YUYVIG
# LTTYWI-02: five of 28 yearly teaching awards dropped beside another class's).
_CLASS_YEAR_RE = re.compile(r"\bclass\s+of\s+((?:19|20)\d{2})\b", re.IGNORECASE)
_DASH = "-\u2013\u2014"
# A year that opens a range with no end year after the dash ("2031 - Board
# certified", "2031-present"), and a closed range's end year ("2020 - 2031").
_OPEN_RANGE_START_RE = re.compile(
    rf"\b((?:19|20)\d{{2}})\s*[{_DASH}](?!\s*(?:19|20)?\d{{2}}\b)")
_CLOSED_RANGE_END_RE = re.compile(rf"\b(?:19|20)\d{{2}}\s*[{_DASH}]\s*((?:19|20)\d{{2}})\b")


def _class_years(text: str) -> set[str]:
    return set(_CLASS_YEAR_RE.findall(text))


_BARE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _reopened_years(dropped_text: str, kept_text: str) -> set[str]:
    """Years the dropped text opens a range at where the kept text only
    closes one: a certification renewed in 2031 and still current, beside
    the term that ended in 2031 (#666, YUYVIG HXBPCT-01). `_dates_compatible`
    reads the open start as a bare year inside the kept range. A kept text
    that also states the year on its own ("... 2031.") vouches for it."""
    kept_elsewhere = set(_BARE_YEAR_RE.findall(_CLOSED_RANGE_END_RE.sub(" ", kept_text)))
    return ((set(_OPEN_RANGE_START_RE.findall(dropped_text))
             & set(_CLOSED_RANGE_END_RE.findall(kept_text)))
            - set(_OPEN_RANGE_START_RE.findall(kept_text)) - kept_elsewhere)


def _occasion_apart(dropped_text: str, kept_text: str) -> str | None:
    """Why the dropped text names another occasion than the kept text: a
    date stated to the month or a year the kept text does not carry, another
    class label or a year it carries only inside one, a range left open where
    the kept one closes, or a part, phase or numeral it lacks. None when
    nothing tells them apart.

    The same predicate stage 6 now refuses a drop on (`_dates_compatible`,
    `_part_numbers`, `_trial_phases`), re-checked here because a sidecar may
    predate it and a record-rule or non-date-aware code skips it (DPEHSZ-01,
    KDAZOM-03, XWNZWW-03)."""
    if not _dates_compatible(dropped_text, kept_text):
        return "a date the kept entry does not carry"
    classes = _class_years(dropped_text) ^ _class_years(kept_text)
    if classes:
        return f"class of {', '.join(sorted(classes))} on one entry only"
    if not _dates_compatible(_CLASS_YEAR_RE.sub(" ", dropped_text),
                             _CLASS_YEAR_RE.sub(" ", kept_text)):
        return "a year the kept entry carries only as a class"
    reopened = _reopened_years(dropped_text, kept_text)
    if reopened:
        return f"a range open from {', '.join(sorted(reopened))} where the kept one ends"
    for mark, read in _OCCASION_MARKS:
        missing = read(dropped_text) - read(kept_text)
        if missing:
            return f"{mark} {', '.join(sorted(missing))} the kept entry lacks"
    return None


def _entry_index_by_text(stage_5d: Mapping | None) -> dict[str, object]:
    """Whitespace-folded entry text -> its element_idx_start, for the texts
    exactly one entry carries. A dedup decision records the dropped TEXT and no
    index, so this is how a finding names the entry it is about."""
    if not stage_5d:
        return {}
    by_text: dict[str, list[object]] = {}
    for entry in _fields_entries(stage_5d):
        for text in (entry.text, entry.fields.get("formatted_citation")):
            if isinstance(text, str) and text.strip():
                by_text.setdefault(" ".join(text.split()), []).append(entry.element_idx)
    return {text: indices[0] for text, indices in by_text.items()
            if len(set(map(str, indices))) == 1}


def _drop_evidence(decision: Mapping, index_by_text: Mapping[str, object],
                   detail: str, entries: list[object] | None = None) -> str:
    """One evidence line for a drop, led by `entry N` when the dropped text
    names exactly one entry, or by every entry in `entries` when it does not,
    so the precision harness and the review copy can locate it."""
    dropped = str(decision.get("dropped_text", ""))
    index = index_by_text.get(" ".join(dropped.split()))
    if index is None and entries:
        index = ", ".join(map(str, entries))
    prefix = f"entry {index}: " if index is not None else ""
    return (f"{prefix}{decision.get('code', '?')} ({detail}): "
            f"dropped '{dropped[:DEDUP_TEXT_CHARS]}' vs kept "
            f"'{str(decision.get('kept_text', ''))[:DEDUP_TEXT_CHARS]}'")


def _named_apart(decision: Mapping, rendered_items: set[str] | None) -> bool:
    """INFO test (#666): the dropped entry's name differs from the kept one's,
    and either that name is no cell of its own on the page, or the two names
    carry different rank or qualifier words (stage 6's `_different_rank`:
    "Assistant Professor" beside "Clinical Assistant Professor", SEKQUI-01;
    an "Outreach" award beside the plain one, NDXXAD-01), which tells two
    records apart even when the dropped name renders for another year."""
    if _different_rank(decision.get("code"), decision.get("dropped_fields") or {},
                       decision.get("kept_fields") or {}):
        return True
    return rendered_items is not None and bool(_unrendered_identity(decision, rendered_items))


# Words two extracted names may differ by and still be one name ("The Example
# Journal" beside "Example Journal"), and a parenthetical, which is most often
# an acronym ("Example Society (ES)").
_NAME_FILLER_WORDS = frozenset({"a", "an", "and", "at", "for", "in", "of", "on", "the", "to"})
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
#: The name field of a code whose entry is a bare name, so a dropped entry
#: without the field is named by its text (a journal list, YUYVIG WPJHYT-03).
_BARE_NAME_FIELD = "journal_name"


def _name_words(name: str) -> list[str]:
    """The words of an extracted name that tell it from another, in order,
    a parenthetical and filler words left out."""
    words = _DEDUP_TOKEN_RE.findall(norm(_PARENTHETICAL_RE.sub(" ", name)))
    return [word for word in words if word not in _NAME_FILLER_WORDS]


def _same_word(word: str, other: str) -> bool:
    """One word, or one the start of the other: an abbreviation ("Prof" for
    "Professor") or a plural ("Committees" beside "Committee")."""
    return word.startswith(other) or other.startswith(word)


def _words_lacking(words: list[str], other: list[str]) -> set[str]:
    """Words of `words` that no word of `other` matches (`_same_word`)."""
    return {word for word in words if not any(_same_word(word, o) for o in other)}


def _inserts_a_word(dropped: list[str], kept: list[str]) -> bool:
    """The kept name holds every dropped word with a word of its own between
    two of them, not at either end: "Example Operations Committee" beside
    "Example Committee" is a narrower body (YUYVIG BLBVPD-04), where
    "Example Society, Example Chapter" extends the same name. Each dropped word is read where it first occurs, so a kept name
    that repeats the dropped one later ("X, formerly Y") inserts nothing."""
    positions = [next((i for i, other in enumerate(kept) if _same_word(word, other)), None)
                 for word in dropped]
    if not positions or None in positions:
        return False
    return any(_words_lacking([word], dropped)
               for word in kept[min(positions) + 1:max(positions)])


def _name_pairs(decision: Mapping) -> list[tuple[str, str, str]]:
    """(field, dropped name, kept name) for each name field both entries
    fill. A bare-name code's dropped entry without the field is named by its
    text when the text is words of the kept name ("Cell" beside "Cancer
    Cell"); a text sharing none is a stray fragment ("Reviewer"), no name."""
    dropped = dict(decision.get("dropped_fields") or {})
    kept = decision.get("kept_fields") or {}
    text = str(decision.get("dropped_text") or "")
    if (_BARE_NAME_FIELD in kept and not dropped.get(_BARE_NAME_FIELD)
            and _name_words(text)
            and not _words_lacking(_name_words(text), _name_words(str(kept[_BARE_NAME_FIELD])))):
        dropped[_BARE_NAME_FIELD] = text
    return [(key, str(dropped[key]), str(kept[key]))
            for key in dropped if key in kept and str(dropped[key]).strip()]


def _distinct_name(decision: Mapping, rendered_items: set[str] | None) -> str | None:
    """The field whose dropped value names another record than the kept
    one, and that is not on the page as an item of its own: two names each
    carrying a word the other lacks (another department, another award,
    YUYVIG OKRTPJ-01, IZABPD-06), a word inserted inside the dropped name
    (a text drop only: a record rule matched the two on purpose despite
    rewording), or another bare journal name (WPJHYT-03). None otherwise."""
    field_matched = str(decision.get("metric", "")).startswith(RECORD_RULE_METRIC_PREFIX)
    for key, dropped_name, kept_name in _name_pairs(decision):
        dropped, kept = _name_words(dropped_name), _name_words(kept_name)
        if key == _BARE_NAME_FIELD:
            apart = set(dropped) != set(kept)
        else:
            apart = ((_words_lacking(dropped, kept) and _words_lacking(kept, dropped))
                     or (not field_matched and _inserts_a_word(dropped, kept)))
        on_page = (rendered_items is not None
                   and " ".join(_DEDUP_TOKEN_RE.findall(norm(dropped_name))) in rendered_items)
        if apart and not on_page and (key != _BARE_NAME_FIELD or rendered_items is not None):
            return key
    return None


#: Fields that tell two deliveries of one talk apart when its title, venue
#: and year match (YUYVIG LTTYWI-01: one talk given in three cities in 2006).
_DELIVERY_FIELDS = ("date", "location")
_CITATION_METRIC_PREFIX = "citation_"


#: The two texts stage 6 writes for a citation drop (`_citation_identity_text`):
#: the formatted citation for a `citation` match, title and year for the rest.
_CITATION_IDENTITY_KINDS = ("citation", "title_journal_year")


def _citation_copies(stage_5d: Mapping | None) -> dict[tuple[str, str], list[_FieldsEntry]]:
    """(code, identity text) -> the stage-5d entries carrying it, so a
    citation drop is checked against every copy it matched, not only the two
    the decision quotes."""
    copies: dict[tuple[str, str], list[_FieldsEntry]] = {}
    if not stage_5d:
        return copies
    for raw, entry in zip(stage_5d.get("entries", []), _fields_entries(stage_5d),
                          strict=True):
        pub = resolve_publication(raw)
        for text in {_citation_identity_text(kind, pub) for kind in _CITATION_IDENTITY_KINDS}:
            copies.setdefault((entry.code, text), []).append(entry)
    return copies


def _deliveries_apart(decision: Mapping, copies: Mapping[tuple[str, str], list[_FieldsEntry]]) -> list[object]:
    """The entries a citation drop matched on, when they state different
    dates or places, so they are deliveries of one talk, not one record
    listed twice. Empty for a non-citation drop or copies that agree."""
    if not str(decision.get("metric", "")).startswith(_CITATION_METRIC_PREFIX):
        return []
    group = copies.get((decision.get("code"), decision.get("dropped_text")), [])
    for key in _DELIVERY_FIELDS:
        values = {" ".join(_DEDUP_TOKEN_RE.findall(norm(e.fields.get(key))))
                  for e in group if isinstance(e.fields.get(key), str) and e.fields[key].strip()}
        if len(values) > 1:
            return [e.element_idx for e in group]
    return []


def _drop_suspicion(decision: Mapping, rendered_items: set[str] | None,
                    copies: Mapping) -> tuple[str, str, list[object]] | None:
    """(severity, detail, entries) for one dedup decision, or None when it
    reads as a duplicate. `entries` names the matched copies when the drop's
    own text names no single entry."""
    dropped = _alphanumeric_tokens(decision.get("dropped_text", ""))
    kept = _alphanumeric_tokens(decision.get("kept_text", ""))
    if not dropped:
        return None
    coverage = sum((dropped & kept).values()) / sum(dropped.values())
    metric = decision.get("metric", "?")
    field_matched = str(metric).startswith(RECORD_RULE_METRIC_PREFIX)
    if coverage < DEDUP_SAFE_CONTAINMENT and not field_matched:
        return "WARN", f"{metric}, {coverage:.0%} covered by kept", []
    occasion = _occasion_apart(str(decision.get("dropped_text", "")),
                               str(decision.get("kept_text", "")))
    if occasion:
        return "WARN", f"{metric}, {occasion}", []
    deliveries = _deliveries_apart(decision, copies)
    if deliveries:
        return "WARN", f"{metric}, copies differ in date or place", deliveries
    name = _distinct_name(decision, rendered_items)
    if name:
        return "WARN", f"{metric}, {name} names another record", []
    if _named_apart(decision, rendered_items):
        return "INFO", str(metric), []
    return None


def lint_dedup_drops(report: dict,
                     blocks: list[tuple[str, str]] | None = None,
                     stage_5d: Mapping | None = None) -> list[dict]:
    """Stage-6 dedup decisions that may have dropped a distinct record.

    WARN, one evidence line per drop with none left out (`_drop_suspicion`):
    - the dropped text is NOT near-fully contained in the kept entry, so at
      these loose similarity thresholds it is a distinct record, not a
      duplicate (#227);
    - it names another occasion than the kept entry: a date, class, open
      range, part, phase or numeral the kept text lacks (`_occasion_apart`,
      #666);
    - a citation drop whose matched copies in stage 5d state different dates
      or places (`_deliveries_apart`, needs `stage_5d`);
    - the two entries' extracted names name two records (`_distinct_name`).

    INFO (#666): the dropped text IS contained, but the two entries carry
    different names, and either the dropped name is on the page as no cell of
    its own (needs the rendered document) or the names differ by a rank or
    qualifier word (`_named_apart`). A duplicate reworded by the kept entry
    also lands here, so it stays INFO.

    A record-rule drop (`stage6/record_dedup.py`, metric
    `RECORD_RULE_METRIC_PREFIX`) matched the two records on their fields, not
    their text: a header line beside a table row shares few words with it by
    construction, so it skips the coverage test and takes the others.

    `stage_5d`, when given, names each drop's entry (`_entry_index_by_text`)."""
    suspect = []
    named_apart = []
    rendered_items = _rendered_item_set(blocks) if blocks is not None else None
    index_by_text = _entry_index_by_text(stage_5d)
    copies = _citation_copies(stage_5d)
    for d in report.get("dedup_decisions", []):
        verdict = _drop_suspicion(d, rendered_items, copies)
        if verdict is None:
            continue
        severity, detail, entries = verdict
        evidence = _drop_evidence(d, index_by_text, detail, entries)
        (suspect if severity == "WARN" else named_apart).append(evidence)
    findings = []
    if suspect:
        findings.append(_finding(
            "dedup_drops", "WARN",
            f"{len(suspect)} dedup drop(s) poorly covered by the kept entry, "
            f"naming another date, class, part or numeral, or another record "
            f"by name — possible distinct records lost (#227, #666)",
            suspect))
    if named_apart:
        findings.append(_finding(
            "dedup_drops", "INFO",
            f"{len(named_apart)} dedup drop(s) fully covered by the kept entry "
            f"but named differently: another rank or qualifier, or a name that "
            f"is no cell of its own on the page — possible distinct records "
            f"lost (#666)",
            named_apart[:DEDUP_EVIDENCE_LIMIT]))
    return findings


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
            cells = frozenset(norm(c) for c in row if c and str(c).strip())
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
            if frozenset(norm(v) for v in values) in rendered:
                findings.append(_finding(
                    "invented_records", "WARN",
                    f"entry {e.get('element_idx_start')} ({code}): every "
                    f"extracted field value is a known WCM template label, "
                    f"rendered as if it were a real record (#829)",
                    [f"{k}: {v}" for k, v in fields.items() if v]))
        if code == INVENTED_RECORD_LICENSURE_CODE:
            text = str(e.get("text", ""))
            if (is_template_instruction(text) or is_near_template_instruction(text)
                    or is_foreign_template_instruction(text)):
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
    any other disagreement. A grant renders "-Present" only where its source
    leaves the start year open; otherwise stage 6 writes the bare start, so
    the lint asks stage 6's own `format_date_range` with the entry's text,
    as the grant renderer does (RCBKFG FLYBMX 157, 174, 175: 0 of 3).
    Report-only by decision (2026-09-09): the extracted value is never
    changed."""
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
        code = e.get("taxonomy_code")
        if code in GRANT_CODES and not format_date_range(start, "", code, text).endswith("Present"):
            continue
        severity = "WARN" if start == range_end else "INFO"
        findings.append(_finding(
            "wrong_start_date", severity,
            f"entry {e.get('element_idx_start')} ({e.get('taxonomy_code')}): "
            f"start_date '{start}' with an empty end_date, but the text "
            f"carries the single closed range {range_start}-{range_end} -- "
            f"renders '{start}-Present' (#729)",
            [text[:120]]))
    return findings


# --------------------------------------------------------------------------
# Field values stage 4 extracted that the document cannot show: a value filed
# under a key no renderer reads, and a year given the wrong century or taken
# from somewhere other than the entry's text. All find the value in stage 4 --
# it is already wrong or unreachable there. The year lints never read the
# docx: matching rendered text could only lose them precision (four-digit
# numbers below 1930 sit in citation page ranges). They do read the text a
# stage-5 formatter wrote for the whole entry, which is what stage 6 renders in
# place of its date fields (`_year_renders`). `offschema_fields` reads the
# docx, when the run has one, only to grade what stage 4 showed it (#1245):
# see `_grade_value`.


#: A key that names a date or a year: `date`, `start_date`, `year_certified`,
#: `dates_attended`, and the shapes `fan_out._is_date_key` (suffix-based)
#: does not see, `date_range` and `start_date_1`. Matched as a whole
#: underscore-separated word, so `candidate_name` is not a date key.
_DATE_NAMED_KEY_RE = re.compile(r"(?:^|_)(?:dates?|years?)(?:_|$)")


def _is_date_key(key: str) -> bool:
    """A date-named key (`_DATE_NAMED_KEY_RE`), or one stage 6 writes into a
    record's date cell as a further span (`EXTRA_SPAN_KEYS`): its value is a
    date of its record, never a record or a fact of its own -- even
    `additional_periods`, a list of `{start_date, end_date}` objects that
    would otherwise read as whole records (EBYSBC E22, #1245)."""
    return bool(_DATE_NAMED_KEY_RE.search(key)) or key in EXTRA_SPAN_KEYS


#: Characters of one value, and values per finding, quoted as evidence.
FIELD_EVIDENCE_VALUE_CHARS = 100
FIELD_EVIDENCE_MAX_VALUES = 3


def _evidence_value(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:FIELD_EVIDENCE_VALUE_CHARS]


# --- offschema_fields --------------------------------------------------------
#
# Stage 4 asks for JSON with no schema enforced, so the model sometimes files
# a value, or a whole second record, under a key the code's schema does not
# define (`organization_2`, `additional_entry`, `honors`, `outcome`). Every
# section renderer reads fixed keys, so the value is dropped with no warning
# (pilot batch 2026-10-02: whole records lost in TXTATQ, YOXXOH and BFSUMA, a
# fact lost in CYOFWJ, JNATFN and WIANVH). #817 is the field-level loss
# measurement this is the cheap, key-set-only slice of.

#: Codes the lint does not inspect. The text-rendered codes
#: (`fan_out._TEXT_RENDERED_CODES`, which include T): their section writes the
#: entry's text, not its fields, so a value under any key is in the document
#: already. The codes a stage-5 formatter rewrites whole from the entry's text
#: -- teaching (5c's `formatted_text`) and publications (5d's
#: `formatted_citation`): the off-schema keys stage 4 leaves on them
#: (`other_id`, `journal_or_source`, a K1 `description`) travel inside that
#: rendering. Known miss: a contribution note under S1 `notes` (PFBSNH).
#: Personal Data (A) is inspected, but only against the document (see
#: `_personal_value_withheld`).
_OFFSCHEMA_SKIPPED_CODES = (_TEXT_RENDERED_CODES | frozenset(TEACHING_CODES)
                            | frozenset(PUBLICATION_CODES))

#: Keys stage 4's own post-processing writes onto entries whose schema may
#: not declare them -- bookkeeping, not a value the model misfiled:
#: `coercion.apply_regex_post_processing` sets the identifiers on S and
#: `IDENTIFIER_TAXONOMY_CODES`, and `percent_effort` on M2*. Only what this
#: lint can see is listed: S, N4 and S0 are skipped above, and
#: `owner_name.add_target_names`' `target_name` lands only on S and on R codes
#: that either declare it (R) or have no schema at all. The `orcid` the same
#: pass sets on A is not listed: no section writes an A entry's ORCID, so it
#: is lost like any other off-schema value (SJWASY in batch EBYSBC).
_IDENTIFIER_KEYS = frozenset({"pmid", "pmcid", "doi"})
_PERCENT_EFFORT_KEY = "percent_effort"

#: The one date key the schema of a code whose section writes a single date
#: declares (H, R). A `start_date`/`end_date` range or a `dates` list on such a
#: code renames no key the schema has, and the honors renderer reads `date`
#: alone: H ranges filed that way rendered with an empty date cell (batch
#: EBYSBC class E15: KDAZOM, VVRTUC, EOSAFF, OTBUCZ). Every other code's
#: date-named keys stay out unless they hold a list (`_is_offschema_date`).
_SINGLE_DATE_KEY = "date"

#: How the rendered document bears on one off-schema value (`_grade_value`).
#: SHOWN: on a line of its own record -- not lost, so not reported. ELSEWHERE:
#: the document shows it, but not with its record. ABSENT: the document does
#: not show it where it belongs. UNGRADED: no document to grade against, or
#: a whole record, which is reported whatever the page shows (#1187).
GRADE_SHOWN = "shown"
GRADE_ELSEWHERE = "elsewhere"
GRADE_ABSENT = "absent"
GRADE_UNGRADED = "ungraded"

#: The words of the WCM Personal Data table's address and telephone rows
#: (Office address, Office telephone, Cell phone; Home address is withheld).
#: An A value under a key carrying one as a word (`office_phone`,
#: `street_address`, `research_address`) had a row to fill, and in batch
#: EBYSBC those rows rendered empty (YYVHNN, ZCTARO). One under `fax`,
#: `website` or `orcid` had no row. Nor, in effect, did one under an email
#: key: the renderer reads every email key it knows, so an email left over
#: is a second address of a kind whose row the first one fills
#: (`secondary_email`, the low-severity AQAJHD-12). `_absence_is_a_loss`.
PERSONAL_DATA_ROW_WORDS = frozenset({"address", "phone", "telephone"})

#: Evidence for a Personal Data value, in place of the value itself: the
#: document goes to S3 with the doctor report, and the #820 PII pass, not
#: this lint, decides what of the owner's data may be shown.
PERSONAL_VALUE_EVIDENCE = "(Personal Data value, not quoted)"


class OffschemaValue(NamedTuple):
    """One non-empty value under a key no renderer reads. `records` is how
    many whole records it holds (each object of a record list), 0 for one
    fact; `grade` is `_grade_value`'s verdict; `warn` is whether it alone
    makes its finding WARN -- a whole record, or `_absence_is_a_loss`."""
    element_idx: object
    value: object
    records: int
    grade: str
    warn: bool
    personal: bool


class OutputLine(NamedTuple):
    """One rendered unit of text -- a paragraph, a table cell, a table row
    joined across its cells (`_table_lines`), or a whole form table
    (`_is_form_table`) -- in the forms a value is matched in: squashed, for a
    value whole (`_value_on_line`); its letters and digits alone, in order
    (`_alnum`), for an anchor value a renderer split across cells or
    re-punctuated (the honors renderer moves an award name's tail into the
    organization cell, XWNZWW); and its word set, for a value the document re-punctuates
    (stage 5b writes a B1 `location` of "Town, Country" into the institution
    cell as "Town City, Country": ZCTARO)."""
    squashed: str
    alnum: str
    words: frozenset[str]


class RenderedDocument(NamedTuple):
    """What `_grade_value` reads besides the entry: the document's lines and
    form tables (`OutputLine`); every stage-4 record's string values
    (`_alnum`, one set per record), which say whether an anchor value is the
    entry's alone (`_distinctive`); and the squashed values the Personal
    Data entries hold -- the owner's own contact details, which Personal
    Data renders."""
    lines: tuple[OutputLine, ...]
    record_values: tuple[frozenset[str], ...]
    owner_values: frozenset[str]


def _alnum(text: str) -> str:
    """The text's letters and digits alone, lowercased, in order."""
    return "".join(_DEDUP_TOKEN_RE.findall(norm(text)))


def _alnum_values(fields: Mapping[str, object]) -> frozenset[str]:
    return frozenset(filter(None, map(_alnum, _nonempty_field_values(dict(fields)))))


def _output_line(text: str) -> OutputLine:
    return OutputLine(squash(text), _alnum(text),
                      frozenset(_alphanumeric_tokens(text)))


def _is_form_table(kind: str, lines: list[str]) -> bool:
    """A table every joined row of which is a "Label: | value" pair: one
    record's form -- a grant, a mentee -- whose rows belong together, so a
    value in one row is shown with the name in another (ZCTARO's N3B
    `awards` in the Project/Accomplishments row of its mentee's table)."""
    rows = [line for line in lines if TABLE_ROW_JOINER in line]
    return kind == "table" and bool(rows) and all(
        row.split(TABLE_ROW_JOINER)[0].rstrip().endswith(":") for row in rows)


def _form_unit(lines: list[OutputLine]) -> OutputLine:
    """A form table's lines as one unit. Joined with `_LINE_SENTINEL`, so an
    anchor or a value still matches inside one line only -- across rows,
    "Program" ending one and "Award source:" opening the next read as a
    "Program Award" that is in neither (OTBUCZ). No word set: every word
    of the form at once would show most short values somewhere in it."""
    return OutputLine(_LINE_SENTINEL.join(line.squashed for line in lines),
                      _LINE_SENTINEL.join(line.alnum for line in lines), frozenset())


def _rendered_document(stage4: dict, blocks: list[tuple[str, str]] | None,
                       ) -> RenderedDocument | None:
    """None without the docx: every value is then UNGRADED."""
    if blocks is None:
        return None
    units: list[OutputLine] = []
    for kind, text in blocks:
        texts = [line for line in str(text).split("\n") if line.strip()]
        lines = [_output_line(line) for line in texts]
        units.extend(lines)
        if _is_form_table(kind, texts):
            units.append(_form_unit(lines))
    entries = _fields_entries(stage4)
    owner = frozenset(value for entry in entries if entry.code == PERSONAL_DATA_CODE
                      for value in map(squash, _nonempty_field_values(dict(entry.fields))))
    return RenderedDocument(tuple(units), tuple(_alnum_values(e.fields) for e in entries),
                            owner)


def _declared_fields() -> dict[str, frozenset[str]]:
    """Every field name each code declares in either schema definition: the
    built-in `FIELD_SCHEMAS` and the versioned config file, whatever the
    field's `extract` flag. Read here rather than through
    `load_field_schemas_from_config`, which drops `extract: false` fields by
    design -- a key declared but not extracted is #817's other half, not an
    off-schema key. A missing or corrupt config file raises, so the lint
    reports ERROR rather than flagging every config-only key (§5.5)."""
    declared = {code: set(schema.get("fields", ()))
                for code, schema in FIELD_SCHEMAS.items()}
    config = json.loads(FIELD_SCHEMA_CONFIG_PATH.read_text(encoding="utf-8"))
    for code, schema in config.get("schemas", {}).items():
        if isinstance(schema, dict):
            declared.setdefault(code, set()).update(schema.get("fields") or {})
    return {code: frozenset(names) for code, names in declared.items()}


def _stage4_bookkeeping_keys(code: str) -> frozenset[str]:
    keys: set[str] = set()
    if code in IDENTIFIER_TAXONOMY_CODES:
        keys |= _IDENTIFIER_KEYS
    if code.startswith(GRANT_EFFORT_TAXONOMY_PREFIX):
        keys.add(_PERCENT_EFFORT_KEY)
    return frozenset(keys)


def _fanned_out_keys(entry: _FieldsEntry) -> frozenset[str]:
    """The keys stage 6's fan-out splits this entry on, each record then
    rendering as its own child -- run through the same call and the same
    built-in schema `stage_6_word_template` hands it, so the doctor cannot
    disagree with the renderer about which lists render. The last of stage
    4's records counts too: when every earlier record would print the same
    row as a later one, it is the only child left (#1445)."""
    probe = {"taxonomy_code": entry.code, "text": entry.text,
             "extracted_fields": dict(entry.fields)}
    children = fan_out_multi_record_entries([probe], FIELD_SCHEMAS,
                                            records_key=STAGE4_RECORDS_KEY)
    return frozenset((child.get(FANNED_OUT_FROM) or child[LAST_STAGE4_RECORD])["key"]
                     for child in children
                     if FANNED_OUT_FROM in child or LAST_STAGE4_RECORD in child)


def _is_record_shaped(key: str, value: object, declared: frozenset[str]) -> bool:
    """A whole record rather than one fact: a list of objects, an object that
    shares a key with the code's schema, or a numbered schema field."""
    if isinstance(value, list):
        return all(isinstance(item, Mapping) for item in value)
    if isinstance(value, Mapping):
        return bool(set(value) & declared)
    numbered = NUMBERED_FIELD_RE.match(key)
    return bool(numbered and numbered.group("field") in declared)


def _record_count(key: str, value: object, declared: frozenset[str]) -> int:
    """Whole records under this key: every object of a record list, else one
    for any other record shape, 0 for one fact (#1245: XELRLZ's `appointments`
    list of three was reported as one record). A date-named key holds a date
    of its record, never a record of its own."""
    if _is_date_key(key) or not _is_record_shaped(key, value, declared):
        return 0
    return len(value) if isinstance(value, list) else 1


def _holds_a_non_date_value(entry: _FieldsEntry, keys: frozenset[str]) -> bool:
    """Whether any of `keys` other than a date-named one holds a value. When
    none does, the section renderers have no name, title or role to write and
    fall back to the entry's raw text (see `fan_out`'s module docstring). That
    text usually carries a one-fact value: on the 163-CV wave-1 farm the
    rendered docx held every string of 151 of the 169 such values. It does
    not reliably carry a record-shaped one: only 9 of the 20 there, and whole
    appointments, consultantships and degrees under an off-schema key were
    missing from the pilot's and wave-1's rendered docx (#1187). So
    `_offschema_values` uses this to drop only the one-fact values of such
    an entry."""
    return any(not _is_blank(entry.fields.get(key)) for key in keys
               if not _DATE_NAMED_KEY_RE.search(key))


def _is_offschema_date(key: str, value: object, declared: frozenset[str]) -> bool:
    """Whether a date-named key is a candidate at all. Most are left out: they
    name a date the schema declares under another name, and the renderers
    fall back to them (R reads `start_date` when `date` is empty). Two shapes
    are not renames: any date key on a code whose schema declares only
    `_SINGLE_DATE_KEY`, and a list of dates or periods on any code -- a
    second term, which no renderer reads (batch EBYSBC: VNUAHA's O
    `additional_dates`, BZZNRL's H `dates`). A key stage 6 writes as a
    record's further span (`EXTRA_SPAN_KEYS`, EBYSBC E22) is always one,
    whatever its shape: HZGJFM's P `additional_period_start` is a string,
    and the section now renders it, so a span is reported exactly when its
    years are not on its record's line."""
    if key in EXTRA_SPAN_KEYS:
        return True
    schema_dates = {name for name in declared if _DATE_NAMED_KEY_RE.search(name)}
    return schema_dates == {_SINGLE_DATE_KEY} or isinstance(value, list)


def _personal_value_withheld(key: str, value: object) -> bool:
    """A Personal Data value the withhold policy keeps out of the document
    (date or place of birth, spouse, children, home contact, ...), asked of
    the policy as the labelled line it would be ("place of birth: <value>"):
    its absence is the PII pass working, not a loss (#820, #821)."""
    label = key.replace("_", " ")
    return bool(_pii_matches(f"{label}: {' '.join(_leaf_strings(value))}",
                             SCOPE_PERSONAL_AND_APPENDIX))


def _anchor_values(entry: _FieldsEntry, keys: frozenset[str]) -> list[str]:
    """The entry's values under `keys` -- the ones its section writes -- that
    can say which line is its own: not a date, not template scaffolding."""
    return [leaf for key in keys if not _DATE_NAMED_KEY_RE.search(key)
            for leaf in _leaf_strings(entry.fields.get(key))
            if leaf.strip() and not _is_date_only_text(leaf)
            and not _piece_in_template(squash(leaf))]


def _carries(line: OutputLine, value: str) -> bool:
    """Whether the line carries an anchor value: in order, letters and
    digits alone, when it is at least `RENDERED_FIELDS_MIN_VALUE_CHARS`
    long; as words of the line when shorter ("PhD")."""
    anchor = _alnum(value)
    if len(anchor) >= RENDERED_FIELDS_MIN_VALUE_CHARS:
        return anchor in line.alnum
    words = set(_alphanumeric_tokens(value))
    return bool(words) and words <= line.words


def _distinctive(value: str, entry: _FieldsEntry, document: RenderedDocument) -> bool:
    """An anchor value at least `RENDERED_FIELDS_MIN_VALUE_CHARS` long that
    sits inside no other stage-4 record's value. Wider than
    `Stage4Evidence.shared_values`' equality on purpose: one of ZCTARO's N3A
    entries has a `site_position` that sits inside its siblings' longer
    ones ("<program>" beside "<degree>, <program> - <school>"), and their
    rows show the very institution the entry lost."""
    anchor = _alnum(value)
    if len(anchor) < RENDERED_FIELDS_MIN_VALUE_CHARS:
        return False
    holders = sum(any(anchor in held for held in values)
                  for values in document.record_values)
    return holders <= int(any(anchor in held for held in _alnum_values(entry.fields)))


def _independent_values(values: list[str]) -> list[str]:
    """`values` less any that another of them holds (`_alnum`): a
    `mentee_level` of "<degree>" beside a `site_position` of "<degree>
    Program" is one piece of evidence, not two, and on ZCTARO both sit on
    every sibling row that names the same program."""
    forms = [_alnum(value) for value in values]
    return [value for i, (value, form) in enumerate(zip(values, forms))
            if not any(form in other and (form != other or j < i)
                       for j, other in enumerate(forms) if j != i)]


def _record_lines(entry: _FieldsEntry, keys: frozenset[str],
                  document: RenderedDocument) -> list[OutputLine]:
    """The entry's own lines, wherever stage 6 put them: every line carrying
    one of its `_distinctive` anchor values; failing any, every line
    carrying more than `RENDERED_FIELDS_MAJORITY` of its anchor values, when
    it has at least `RENDERED_FIELDS_MIN_VALUES` (`_fields_rendered`'s
    co-location test: ZCTARO's B1 degree and institution, each held by other
    records too, sit together only on the degree's row). Empty when neither
    finds a line."""
    anchors = _anchor_values(entry, keys)
    distinctive = [value for value in anchors if _distinctive(value, entry, document)]
    if distinctive:
        return [line for line in document.lines
                if any(_carries(line, value) for value in distinctive)]
    anchors = _independent_values(anchors)
    if len(anchors) < RENDERED_FIELDS_MIN_VALUES:
        return []
    return [line for line in document.lines
            if sum(_carries(line, value) for value in anchors)
            > RENDERED_FIELDS_MAJORITY * len(anchors)]


def _value_leaves(value: object) -> list[str]:
    """The value's non-blank strings (`_leaf_strings`), or the value itself
    written out when it holds none (a number `_leaf_strings` does not read)."""
    return [leaf for leaf in _leaf_strings(value) if leaf.strip()] or [str(value)]


def _value_years(value: object) -> frozenset[str]:
    return frozenset(year for leaf in _leaf_strings(value)
                     for year in _FOUR_DIGIT_YEAR_RE.findall(leaf))


def _shown_on(value: object, line: OutputLine, date_key: bool) -> bool:
    """Whether the line shows the value. A date: every four-digit year in it,
    as a word of the line (one with none has nothing to lose). Anything
    else: every string in it, whole (`_value_on_line`) or as all of its
    words."""
    if date_key:
        return _value_years(value) <= line.words
    return all(_value_on_line(squash(leaf), line.squashed)
               or set(_alphanumeric_tokens(leaf)) <= line.words
               for leaf in _value_leaves(value))


def _grade_value(entry: _FieldsEntry, key: str, value: object, keys: frozenset[str],
                 document: RenderedDocument) -> str | None:
    """How the document bears on one off-schema value (`GRADE_*`), or None
    when it cannot say. A Personal Data value's own line is any line:
    Personal Data is one table of the owner's details. An owner's contact
    detail on another record (a page header's email fused into an O entry,
    MRJDWE) is SHOWN when the document shows it anywhere. A date is judged
    only on its record's lines, since its year on any other line is a
    coincidence: None when the record's line cannot be found."""
    date_key = _is_date_key(key)
    personal = entry.code == PERSONAL_DATA_CODE
    own = document.lines if personal else _record_lines(entry, keys, document)
    if any(_shown_on(value, line, date_key) for line in own):
        return GRADE_SHOWN
    if date_key:
        return GRADE_ABSENT if own else None
    shown_anywhere = any(_shown_on(value, line, False) for line in document.lines)
    if shown_anywhere and isinstance(value, str) and squash(value) in document.owner_values:
        return GRADE_SHOWN
    return GRADE_ELSEWHERE if shown_anywhere else GRADE_ABSENT


def _absence_is_a_loss(entry: _FieldsEntry, key: str, value: object) -> bool:
    """Whether a `GRADE_ABSENT` value is CV content the document lost (WARN)
    rather than only a value the page does not carry (INFO). A date: yes --
    it was judged on its own record's line. A Personal Data value: when its
    key names a row of the Personal Data table (`PERSONAL_DATA_ROW_WORDS`).
    Anything else: when the entry's own text states it -- at least
    `RENDER_TOKEN_OVERLAP` of the words of each of its strings, not every
    word, since stage 4 rewrites a date inside a value (WIANVH's F2
    `notes`). The model's own remark on an entry (BMAMWE's N3B `note` that
    no mentee was named) is not content of the CV."""
    if _is_date_key(key):
        return True
    if entry.code == PERSONAL_DATA_CODE:
        return bool(set(key.split("_")) & PERSONAL_DATA_ROW_WORDS)
    text_words = set(_alphanumeric_tokens(entry.text))
    leaves = [set(_alphanumeric_tokens(leaf)) for leaf in _value_leaves(value)]
    return all(words and len(words & text_words) / len(words) >= RENDER_TOKEN_OVERLAP
               for words in leaves)


def _offschema_candidates(entry: _FieldsEntry, declared: frozenset[str],
                          readable: frozenset[str], graded: bool) -> dict[str, object]:
    """The entry's non-empty values under a key nothing reads: in neither
    schema, not rendered for its code, not stage-4 bookkeeping, not a record
    list stage 6 fans out, and -- for a date-named key -- one of
    `_is_offschema_date`'s shapes. A date and a Personal Data value are only
    candidates when there is a document to grade them against (`graded`):
    without it a date is far more often a rename than a loss, and a Personal
    Data value more often withheld than lost."""
    if entry.code == PERSONAL_DATA_CODE and not graded:
        return {}
    candidates: dict[str, object] = {}
    for key, value in entry.fields.items():
        if key in readable or _is_blank(value):
            continue
        if _is_date_key(key) and not (
                graded and _is_offschema_date(key, value, declared)):
            continue
        if entry.code == PERSONAL_DATA_CODE and _personal_value_withheld(key, value):
            continue
        candidates[key] = value
    for key in _fanned_out_keys(entry):
        candidates.pop(key, None)
    return candidates


def _offschema_values(entry: _FieldsEntry, declared_by_code: dict[str, frozenset[str]],
                      document: RenderedDocument | None) -> dict[str, OffschemaValue]:
    """`{key: value}` for this entry's `_offschema_candidates`, each counted
    (`_record_count`) and graded (`_grade_value`), less what the document
    shows on its record's own line. On an entry none of whose schema or
    rendered keys holds a value other than a date, a one-fact value is left
    out, because the raw text the entry renders from usually carries it; a
    record-shaped value is always reported, because that text does not
    carry a whole record (see `_holds_a_non_date_value`)."""
    if entry.code in _OFFSCHEMA_SKIPPED_CODES:
        return {}
    declared = declared_by_code.get(entry.code, frozenset())
    schema_keys = declared | _RENDERED_FIELDS.get(entry.code, frozenset())
    readable = schema_keys | _stage4_bookkeeping_keys(entry.code)
    personal = entry.code == PERSONAL_DATA_CODE
    renders_from_text = not personal and not _holds_a_non_date_value(entry, schema_keys)
    hits: dict[str, OffschemaValue] = {}
    for key, value in _offschema_candidates(entry, declared, readable,
                                            document is not None).items():
        records = _record_count(key, value, declared)
        if records:
            grade = GRADE_UNGRADED
        elif renders_from_text:
            continue
        elif document is None:
            grade = GRADE_UNGRADED
        else:
            grade = _grade_value(entry, key, value, schema_keys, document)
        if grade in (GRADE_SHOWN, None):
            continue
        warn = bool(records) or (grade == GRADE_ABSENT
                                 and _absence_is_a_loss(entry, key, value))
        hits[key] = OffschemaValue(entry.element_idx, value, records, grade, warn,
                                   personal)
    return hits


class OffschemaSummary(NamedTuple):
    """One (code, key) group's severity, message and evidence, in `_finding`'s
    positional order."""
    severity: str
    message: str
    evidence: list[str]


#: Why an off-schema value is unwritten: no renderer reads its key, or -- for
#: a further span (`EXTRA_SPAN_KEYS`) on a code whose date cell reads it
#: (`EXTRA_SPAN_CODES`) -- the span still did not reach its record's date
#: cell (EBYSBC E22). On any other code (ZDCXIV's H `additional_dates`) the
#: key is unread like any other.
_UNREAD_KEY = "no renderer reads it"
_UNREAD_SPAN = "its span did not reach the record's date cell"


def _unwritten_reason(code: str, key: str) -> str:
    """`_UNREAD_SPAN` for a span key on a code whose date cell reads it,
    `_UNREAD_KEY` otherwise."""
    return _UNREAD_SPAN if key in EXTRA_SPAN_KEYS and code in EXTRA_SPAN_CODES else _UNREAD_KEY


def _one_fact_outcome(key: str, hits: list[OffschemaValue]) -> str:
    """The message's account of one-fact values: lost without a document to
    say otherwise, or what the document showed."""
    absent = sum(hit.grade == GRADE_ABSENT for hit in hits)
    if absent:
        where = ("not on its record's line" if _is_date_key(key)
                 else "nowhere in the document")
        return f"{absent} of them {where}"
    if all(hit.grade == GRADE_UNGRADED for hit in hits):
        return "missing from the output"
    return "the document shows it, but not with its record"


def _offschema_summary(code: str, key: str,
                       hits: list[OffschemaValue]) -> OffschemaSummary:
    """WARN when any value is a whole record or a lost fact (`OffschemaValue
    .warn`), INFO when each is one fact the document shows elsewhere, that
    no document graded, or whose absence `_absence_is_a_loss` discounts."""
    records = sum(hit.records for hit in hits)
    noun = "entry" if len(hits) == 1 else "entries"
    if records:
        lost = (f"{records} whole record{'' if records == 1 else 's'} under it, "
                f"missing from the output")
    else:
        lost = f"each holds one fact of its record, {_one_fact_outcome(key, hits)}"
    return OffschemaSummary(
        "WARN" if any(hit.warn for hit in hits) else "INFO",
        f"{len(hits)} {code} {noun}: `{key}` is outside the {code} schema and "
        f"{_unwritten_reason(code, key)} -- {lost} (#817)",
        [f"entry {hit.element_idx}: "
         f"{PERSONAL_VALUE_EVIDENCE if hit.personal else _evidence_value(hit.value)}"
         for hit in hits[:FIELD_EVIDENCE_MAX_VALUES]])


def lint_offschema_fields(stage4: dict,
                          blocks: list[tuple[str, str]] | None = None) -> list[dict]:
    """A non-empty stage-4 value under a key that is in neither field schema
    (built-in or config, any `extract` flag), not in `fan_out._RENDERED_FIELDS`
    for its code, not stage-4 bookkeeping, and not a record list fan-out
    splits: nothing reads it, so it never reaches the document. One finding
    per (code, key). Date-named keys are left out unless
    `_is_offschema_date` says otherwise (two-thirds of the corpus's off-schema
    keys are dates a schema names differently).

    `blocks` (optional: the rendered docx, `w:ins` text included) grades each
    one-fact value (`_grade_value`): one its record's own line shows is not
    reported, one the document shows nowhere raises the finding to WARN
    (#1245). Without it the lint reads stage 4 alone, as before, and leaves
    out dates and Personal Data."""
    declared_by_code = _declared_fields()
    document = _rendered_document(stage4, blocks)
    groups: dict[tuple[str, str], list[OffschemaValue]] = {}
    for entry in _fields_entries(stage4):
        for key, hit in _offschema_values(entry, declared_by_code, document).items():
            groups.setdefault((entry.code, key), []).append(hit)
    return [_finding("offschema_fields", *_offschema_summary(code, key, hits))
            for (code, key), hits in sorted(groups.items())]


# --- implausible_year --------------------------------------------------------
#
# Given a two-digit year (`m/yy`, `m/d/yy`, `m/yy-yyyy`), the stage-4 model
# sometimes picks the wrong century: dates from the 2000s came back in the
# 1900s and rendered that way (pilot batch 2026-10-02: YOXXOH, WIANVH).
# Nothing after stage 4 can see it -- stage 6's own century pivot applies
# only to a raw two-digit string, and the value is four digits by then.

#: Under `TWO_DIGIT_YEAR_PIVOT` a two-digit yy <= 30 means 20yy, so a 19yy year
#: below 1930 is exactly a year no two-digit date can produce correctly -- and
#: no faculty member's own record predates it.
IMPLAUSIBLE_YEAR_FLOOR = 1900 + TWO_DIGIT_YEAR_PIVOT

#: A record more than this many years before the owner's earliest degree year
#: is implausible too, when that degree year is known.
DEGREE_YEAR_LEAD = 10

#: Academic degrees (PIPELINE_README.md): the code the owner's earliest degree
#: year is read from.
ACADEMIC_DEGREE_CODE = "B1"

_FOUR_DIGIT_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


class YearFloor(NamedTuple):
    """The earliest plausible year for this owner, and where it came from."""
    year: int
    reason: str


def _leaf_strings(value: object) -> list[str]:
    """Every string and integer inside a field value, lists and objects
    flattened (`dates_attended` is sometimes `{start_date, end_date}`)."""
    if isinstance(value, Mapping):
        return [s for item in value.values() for s in _leaf_strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _leaf_strings(item)]
    if isinstance(value, (str, int)):
        return [str(value)]
    return []


def _date_field_years(fields: Mapping[str, object]) -> list[tuple[str, int]]:
    """`(key, year)` for every four-digit year in a date-named field."""
    return [(key, int(match))
            for key, value in fields.items() if _DATE_NAMED_KEY_RE.search(key)
            for leaf in _leaf_strings(value)
            for match in _FOUR_DIGIT_YEAR_RE.findall(leaf)]


def _year_in_text(year: int, text: str) -> bool:
    """The entry's own text writes this year in four digits: the value is the
    source's, whatever it says. Bounded on the left only, because a range
    whose dash the source reader dropped fuses into one digit run
    ("1985-89" read as "198589"; web200's school and degree rows)."""
    return re.search(rf"(?<!\d){year}", text) is not None


#: "1985-89", "2011-2", "1993-994", "1986,88": a range or list whose later
#: year is written as the last one to three digits of its four-digit start's.
_RANGE_SHORTHAND_RE = re.compile(r"(?<!\d)(\d{4})\s*[-\u2013\u2014,]\s*(\d{1,3})(?!\d)")

#: A two-digit year token: two digits ending a number, right after a slash
#: (stage 4's own `_TWO_DIGIT_YEAR_PREFIX` shapes, `stage4/coercion.py`,
#: widened to any slash: "3/14/06", "0415/08", "6//12"), an apostrophe
#: ("'04"), a fiscal year ("FY97"), or a dot after a digit ("5.21.09").
#: Lookbehinds, so the day of "m/d/yy" does not consume the year's prefix.
_TWO_DIGIT_YEAR_TOKEN_RE = re.compile(
    r"(?:(?<=/)|(?<=['\u2018\u2019])|(?<=FY)|(?<=FY )|(?<=\d\.))(\d{2})(?!\d)",
    re.IGNORECASE)

#: "64-66": a range of two two-digit years standing alone -- not a run of
#: dash-joined numbers, where "3-24-11-26" (Mar 2024 to Nov 2026) has no
#: year "11".
_TWO_DIGIT_RANGE_RE = re.compile(r"(?<![\d\-/.])(\d{2})\s*[-\u2013]\s*(\d{2})(?![\d\-/])")

#: A two-digit year after a season word: "(Fall Sem., 06)".
_SEASON_TWO_DIGIT_YEAR_RE = re.compile(
    r"\b(?:spring|summer|fall|autumn|winter)\b\D{0,8}?(\d{2})(?!\d)", re.IGNORECASE)

#: A letter l or capital I that a scanned source has in place of a year's
#: leading 1 ("l987").
_OCR_LEADING_ONE_RE = re.compile(r"(?<![A-Za-z])[lI](?=\d{3}(?!\d))")


def _range_shorthand_ends(text: str) -> set[int]:
    """The end years of every range shorthand in `text`: the start's
    leading digits, completed by the written tail, moved one decade or
    century on when that falls before the start ("1998-02" ends 2002)."""
    ends = set()
    for start, tail in _RANGE_SHORTHAND_RE.findall(text):
        unit = 10 ** len(tail)
        end = int(start) // unit * unit + int(tail)
        ends.add(end + unit if end < int(start) else end)
    return ends


def _two_digit_years(text: str) -> set[int]:
    """Every two-digit year (0-99) `text` writes as a date token."""
    years = {int(yy) for pattern in (_TWO_DIGIT_YEAR_TOKEN_RE, _SEASON_TWO_DIGIT_YEAR_RE)
             for yy in pattern.findall(text)}
    for first, second in _TWO_DIGIT_RANGE_RE.findall(text):
        years |= {int(first), int(second)}
    return years


def _year_written(year: int, text: str) -> bool:
    """The entry's text states this year in its century: in four digits
    (`_year_in_text`), as a two-digit year token the shared century pivot
    reads as this year, or as a range shorthand's end. "9/68" states 1968 --
    the right century, so not implausible_year's (s7ab QZWBKQ: all 7 of its
    hits were such dates) -- and "11/02" states 2002, never 1902."""
    yy = year % 100
    return (_year_in_text(year, text)
            or (expand_two_digit_year(yy) == year and yy in _two_digit_years(text))
            or year in _range_shorthand_ends(text))


#: A month or term word, as dates write it.
_MONTH_PATTERN = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?"
                  r"|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?"
                  r"|dec(?:ember)?|spring|summer|fall|autumn|winter)\.?")
#: Two-digit year shapes only `_year_in_source` reads (#1585, batch YUYVIG:
#: 40 of its 44 false positives). Looser than `_two_digit_years`, whose
#: tokens also vouch for a century in implausible_year; here a wrongly read
#: token can only silence a finding. A dash-joined m-d-yy date ("3-30-00"),
#: alone, so "3-24-11-26" (Mar 2024 to Nov 2026) still has no year "11".
_DASHED_DATE_YEAR_RE = re.compile(r"(?<![\d\-/.])\d{1,2}-\d{1,2}-\s?(\d{2})(?![\d\-/])")
#: The two-digit tails of a list after a four-digit year, its range
#: shorthand or a two-digit range: "2014,15,16", "1975, 76, 78",
#: "2005-06, 16", "98-00,02,04" (`_RANGE_SHORTHAND_RE` reads only the first).
_YEAR_LIST_TAILS_RE = re.compile(
    r"(?<!\d)(?:\d{4}(?:-\d{2})?|\d{2}-\d{2})((?:\s*,\s*\d{2}(?!\d))+)")
#: A two-digit year after a month or term word ("Oct. 98", "June 05"), not
#: a day: one a comma, a four-digit year or a word follows ("May 13,",
#: "June 05 2010", "May 13 at").
_MONTH_TWO_DIGIT_YEAR_RE = re.compile(
    rf"(?<![a-z]){_MONTH_PATTERN}\s*(\d{{2}})(?!\d|\s*,|\s*\d{{4}})(?=\s*(?:$|[^\w\s]))",
    re.IGNORECASE)
#: A two-digit year opening a range left open: "16- present", "16-", not a
#: number before a dash that opens a title ("Volume 20 - Title").
_OPEN_TWO_DIGIT_YEAR_RE = re.compile(
    r"(?<![\d\-/.])(\d{2})\s*[-–—]\s*(?:present|current|now|date|today|$|\))",
    re.IGNORECASE)
_SOURCE_ONLY_TWO_DIGIT_YEAR_RES = (_DASHED_DATE_YEAR_RE, _MONTH_TWO_DIGIT_YEAR_RE,
                                   _OPEN_TWO_DIGIT_YEAR_RE)


def _source_two_digit_years(text: str) -> set[int]:
    """`_two_digit_years`, plus the shapes only `_year_in_source` reads."""
    years = _two_digit_years(text)
    years |= {int(yy) for pattern in _SOURCE_ONLY_TWO_DIGIT_YEAR_RES
              for yy in pattern.findall(text)}
    for tails in _YEAR_LIST_TAILS_RE.findall(text):
        years |= {int(yy) for yy in re.findall(r"\d{2}", tails)}
    return years


def _year_in_source(year: int, text: str) -> bool:
    """The entry's text states this year in any century -- whether the
    century is right is implausible_year's question: its four digits
    anywhere, a scanned "l987" included, so a year fused into a longer digit
    run on either side counts ("04/081997", "Example20232024Total"); its
    last two digits as a two-digit year token, a stand-alone two-digit
    range or one of `_source_two_digit_years`'s shapes ("5/31/34" states
    2034, "64-66" both years, "3-30-00" 2000); or a range shorthand's end
    ("2011-2")."""
    text = _OCR_LEADING_ONE_RE.sub("1", text)
    return (str(year) in text or year % 100 in _source_two_digit_years(text)
            or year in _range_shorthand_ends(text))


def _earliest_degree_year(entries: list[_FieldsEntry]) -> int | None:
    """The earliest plausible degree year a B1 entry both extracts and writes
    in its own text -- a degree year that is itself a wrong century, or that
    the text never states, cannot set the floor."""
    years = [year for entry in entries if entry.code == ACADEMIC_DEGREE_CODE
             for _, year in _date_field_years(entry.fields)
             if year >= IMPLAUSIBLE_YEAR_FLOOR and _year_written(year, entry.text)]
    return min(years, default=None)


def _year_floor(entries: list[_FieldsEntry]) -> YearFloor:
    degree_year = _earliest_degree_year(entries)
    if (degree_year is not None
            and degree_year - DEGREE_YEAR_LEAD > IMPLAUSIBLE_YEAR_FLOOR):
        return YearFloor(degree_year - DEGREE_YEAR_LEAD,
                         f"{DEGREE_YEAR_LEAD} years before the earliest degree "
                         f"year, {degree_year}")
    return YearFloor(IMPLAUSIBLE_YEAR_FLOOR,
                     "no two-digit year resolves below it")


def _formatter_texts(stage5d: dict | None) -> dict[tuple, str]:
    """{span: the text a stage-5 formatter wrote for the whole entry} from
    the stage-5d artifact, stage 6's input, which also carries 5c's
    teaching prose. A span two entries share is dropped, as in
    `_stage4_evidence`; no artifact is no texts."""
    entries = (stage5d or {}).get("entries", [])
    span_counts = Counter(_span(e) for e in entries)
    texts = {}
    for e in entries:
        fields = e.get("extracted_fields")
        if not isinstance(fields, Mapping) or span_counts[_span(e)] != 1:
            continue
        text = " ".join(str(fields[key]) for key in _FORMATTED_KEYS if fields.get(key))
        if text:
            texts[_span(e)] = text
    return texts


def _year_renders(year: int, entry: _FieldsEntry,
                  formatted: Mapping[tuple, str]) -> bool:
    """Whether a stage-4 year can reach the page: not when a stage-5
    formatter rewrote the whole entry without it, since stage 6 renders that
    text and not the date field (EBYSBC MIFYLG 234: stage 4 wrote a 1900
    date the text does not have, 5c wrote the text's own year, and the docx
    shows 5c's)."""
    text = formatted.get((entry.element_idx, entry.element_idx_end))
    return text is None or _year_in_text(year, text)


#: A year above this is no date a CV record can carry: no grant, term or
#: appointment ends in the 22nd century. Fixed, not "now + N", so a run's
#: findings do not depend on the day it is doctored (§7.4); a typo just past
#: the current year ("2091") is not caught.
IMPLAUSIBLE_YEAR_CEILING = 2100


def _ceiling_finding(entry: _FieldsEntry, above: list[tuple[str, int]]) -> dict:
    """INFO when the entry's text writes every such year -- a source typo
    stage 4 copied faithfully, which a reviewer should still see (EBYSBC
    VNUAHA 175: an end year mistyped by its first digit) -- WARN when stage
    4 wrote one the text does not."""
    typo = all(_year_in_text(year, entry.text) for _, year in above)
    return _finding(
        "implausible_year", "INFO" if typo else "WARN",
        f"entry {entry.element_idx} ({entry.code}): "
        f"{', '.join(f'{key}={year}' for key, year in above)} -- after "
        f"{IMPLAUSIBLE_YEAR_CEILING}, no year a record can carry; "
        + ("the entry's text writes it, a source typo" if typo
           else "not written in the entry's text"),
        [entry.text[:FIELD_EVIDENCE_VALUE_CHARS]])


def lint_implausible_year(stage4: dict, stage5d: dict | None = None) -> list[dict]:
    """A year in a stage-4 date-named field that is below the owner's floor
    (`YearFloor`) and that the entry's own text never states
    (`_year_written`): a two-digit year given the wrong century, rendered as
    extracted. WARN, one finding per entry. Also a year above
    `IMPLAUSIBLE_YEAR_CEILING` (`_ceiling_finding`). A year a stage-5
    formatter replaced is not judged (`_year_renders`; optional `stage5d`).
    Report-only: the value is not repaired. Code A is skipped, since its
    dates are personal data, not records."""
    entries = _fields_entries(stage4)
    floor = _year_floor(entries)
    formatted = _formatter_texts(stage5d)
    findings = []
    for entry in entries:
        if entry.code == PERSONAL_DATA_CODE:
            continue
        years = [(key, year) for key, year in _date_field_years(entry.fields)
                 if _year_renders(year, entry, formatted)]
        bad = [f"{key}={year}" for key, year in years
               if year < floor.year and not _year_written(year, entry.text)]
        if bad:
            findings.append(_finding(
                "implausible_year", "WARN",
                f"entry {entry.element_idx} ({entry.code}): {', '.join(bad)} -- "
                f"before {floor.year} ({floor.reason}) and not written in the "
                f"entry's text; most likely a two-digit year given the wrong "
                f"century",
                [entry.text[:FIELD_EVIDENCE_VALUE_CHARS]]))
        above = [(key, year) for key, year in years if year > IMPLAUSIBLE_YEAR_CEILING]
        if above:
            findings.append(_ceiling_finding(entry, above))
    return findings


# --- year_not_in_source -------------------------------------------------------
#
# The stage-4 model sometimes writes a year the entry never states, inside the
# plausible band implausible_year leaves alone, so it renders looking right:
# the start year of the entry before it in the same batch (EBYSBC VVRTUC
# 43/45: an open-ended "Month YYYY -" start), or a misread short date
# (HFAJCC 392: an mm/dd/yy start; YYVHNN 480: an M-YY-MM-YY range). Stage
# 4 now repairs part of this itself: the century repair (#1267) moves a 19yy
# year whose two digits the text carries, and `repair_year_not_in_source`
# (stage4/coercion.py, #1348) re-derives a dated year from a source date in
# the same month, which fixes HFAJCC 392 and VVRTUC 43 on new runs. This lint
# is the safety net for what those repairs cannot reach: bare years, M-YY-MM-
# YY runs (YYVHNN 480), and a dated value whose month the text gives no date
# for.

#: Codes year_not_in_source does not judge: code A (personal data, as in
#: implausible_year), and the publication codes. A citation's year renders
#: through stage 5d's citation and stage 5's PubMed record, and on the
#: 63-run EBYSBC farm none of the 8 publication hits was a wrong year: each
#: was a citation split across entries (its year on the next one), a
#: corrected source typo (a dropped or transposed digit) or a bare two-digit
#: year.
_YEAR_NOT_IN_SOURCE_SKIPPED_CODES = frozenset({PERSONAL_DATA_CODE, *PUBLICATION_CODES})


def lint_year_not_in_source(stage4: dict, stage5d: dict | None = None) -> list[dict]:
    """A year in a stage-4 date-named field, from the owner's floor up to
    `IMPLAUSIBLE_YEAR_CEILING` (implausible_year reports the years outside
    that band), that the entry's text does not state (`_year_in_source`):
    stage 4 took it from somewhere else. WARN, one finding per entry.
    Skipped: `_YEAR_NOT_IN_SOURCE_SKIPPED_CODES`, and a year a stage-5
    formatter replaced (`_year_renders`). Report-only."""
    entries = _fields_entries(stage4)
    floor = _year_floor(entries)
    formatted = _formatter_texts(stage5d)
    findings = []
    for entry in entries:
        if entry.code in _YEAR_NOT_IN_SOURCE_SKIPPED_CODES:
            continue
        bad = [f"{key}={year}" for key, year in _date_field_years(entry.fields)
               if floor.year <= year <= IMPLAUSIBLE_YEAR_CEILING
               and not _year_in_source(year, entry.text)
               and _year_renders(year, entry, formatted)]
        if bad:
            findings.append(_finding(
                "year_not_in_source", "WARN",
                f"entry {entry.element_idx} ({entry.code}): {', '.join(bad)} -- "
                f"the entry's text states no such year, in four digits or as "
                f"a two-digit year; stage 4 took it from elsewhere",
                [entry.text[:FIELD_EVIDENCE_VALUE_CHARS]]))
    return findings


# --- multi_record_coverage ---------------------------------------------------
#
# Stage 4 asks for one record per entry. An entry whose text holds several --
# two dated roles in one society, two talks run together in one paragraph, a
# hospital post with the concurrent faculty rank in the next cell, three
# mentees on one line -- comes back as ONE record with no `stage4_records`,
# and nothing renders the rest (#1243; batch EBYSBC, 2026-10-02, class E1: 19
# verified findings on 17 CVs). `unrendered_records` cannot see it: it splits
# an entry on newlines, and these records share a line. This lint cuts the
# entry's text into record-shaped clauses, takes the clause the record covers
# best as the one it stands for, and looks for every other clause in the
# rendered document. A clause whose words the record holds is the record's
# own unless it writes a span the record lacks: a committee membership kept
# only as the office held inside it (X6, 2026-10-04: RINASX-10's three
# entries; VPMMFM-10, an entry stage 4 split into its two offices). That
# shape is the one judged on an entry stage 4 split into `stage4_records`.
# Report-only: nothing is split or asked again.

#: A clause the record does not stand for carries at least this many words
#: or years the record lacks, and they make up at least this share of the
#: clause's own words and years. Below either, the clause is the record's own
#: detail (a city, a second date), not another record. Tuned on the 63-run
#: EBYSBC/s7ab/pilot farm: a 0.5 share missed two verified losses whose second
#: clause repeats most of the first one's words (a national term after a
#: regional one in the same office, a second search committee in the same
#: department).
MULTI_RECORD_MIN_NEW = 2
MULTI_RECORD_NEW_SHARE = 0.4

#: A clause with at least this many lowercase words, and more lowercase words
#: than capitalised ones, is a sentence about the record ("in recognition of
#: ...", "who then went on to ..."), not a record. Titles, roles and
#: institutions are capitalised; a short role phrase in sentence case
#: ("Clinical skills tutor") stays below the floor.
MULTI_RECORD_PROSE_MIN_LOWERCASE = 4

#: How a left-out clause was found; the message names every shape but the
#: first. CLAUSE_NEW_WORDS: it carries words or years the record lacks.
#: CLAUSE_SPAN_OUTSIDE: the record holds each of its words, but it writes a
#: year outside every span the record's dates cover. That is a committee's
#: membership span with an office inside it, where stage 4 kept the office
#: ("<committee>, 2011-2016, Chair 2014-15"; X6 RINASX-10, three entries), or
#: an entry stage 4 split into its offices only (X6 VPMMFM-10). On a one-record
#: entry the clause the record stands for must also name a role the left-out
#: clause lacks: the same role written again at another time stays
#: unreported, as it is when its only new words are years.
CLAUSE_NEW_WORDS = "new_words"
CLAUSE_SPAN_OUTSIDE = "span_outside_record"
#: Codes CLAUSE_SPAN_OUTSIDE and the dated-event clause (`_is_dated_event`)
#: do not judge: a grant writes its own period beside the owner's role
#: period inside it, and its credit line beside its award line, and neither
#: is a second record (BMHBJZ 668, NTCULM 62, web31 455 on the farms).
_GRANT_CODES_UNJUDGED = frozenset(code for code, _title in _FUNDING_SECTIONS)

#: A year as written in a date: 1900-2099, not inside a longer number (a
#: patent or licence number).
_CLAUSE_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
#: The same, with a one- or two-digit end year ("1996-98", "2009-11") kept
#: in the date rather than opening the next clause. Two more shapes write a
#: year in two digits: a month and year ("7/05", "10/08"; not a day of
#: "m/d/yy", and not one run of slashed numbers) and an apostrophe year
#: ("Jul '96", with a straight or curly mark). RCBKFG CAOACN 32 and 64 date
#: every record that way, so the lint read no clause at all. Only in an entry
#: that writes no four-digit year: one that does dates its records that way,
#: and its two-digit dates are notes on the record ("no-cost extension
#: through 06/09", "Degree conferred 6/83", "the aftermath of 9/11"; three
#: false positives on the 126-run farm/batch corpus).
_DATE_YEAR_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})(?:\s*[-–—]\s*\d{1,2})?(?!\d)"
    r"|(?<![\d/])(?:0?[1-9]|1[0-2])/(?P<slash_yy>\d{2})(?![\d/])"
    r"|(?<!\w)['\u2018\u2019](?P<apostrophe_yy>\d{2})(?!\d)")
#: A word that belongs to a date, not to what the record is.
_DATE_WORD_RE = re.compile(rf"^(?:{_MONTH_PATTERN}|present|current|ongoing|date|now)$",
                           re.IGNORECASE)
#: What may sit between two years of ONE date: "1990-1992", "March 2001 -
#: June 2004", "1987, 1988", "2003 and 2005".
_DATE_GAP_RE = re.compile(
    rf"^(?:[\s,\-–—/.:()&]|\d|(?:to|through|thru|and|present|current|ongoing"
    rf"|{_MONTH_PATTERN})\b)*$", re.IGNORECASE)
#: A word for `_has_payload`: three or more letters.
_PAYLOAD_WORD_RE = re.compile(r"[^\W\d_]{3,}")
_PAYLOAD_FILLER = frozenset({"and", "the"})
#: Where a source table cell, line or pipe-joined cell ends: one part of an
#: entry holds one record or a run of them, never half of one.
_STRONG_SEPARATOR_RE = re.compile(r"\t|\n|\s\|\s")
#: The same, plus ';', for undated parts named by a title and an institution.
_SOFT_SEPARATOR_RE = re.compile(r"\t|\n|;|\s\|\s")
#: An aside that opens a clause: "(Chair). ...", " - (see ...".
_PAREN_OPENING_RE = re.compile(r"^[\s\-–—,.:;]*\(")
_PAREN_LEAD_CHARS = frozenset(",.;:-–—")
#: An undated part names a record when it carries a role or rank and an
#: institution: "Assistant Professor, <university> College of Medicine" beside
#: the hospital post the record kept (TAUBPU-01). A PI named in a project's
#: credit line is not the owner's role, so "investigator" is not a title.
_TITLE_WORD_RE = re.compile(
    r"\b(?:professor|instructor|lecturer|fellow|director|chief|chair(?:man|person)?"
    r"|attending|surgeon|physician|resident|member|president|dean|head|scientist"
    r"|consultant|officer|editor|coordinator|manager|advisor|associate|assistant)\b",
    re.IGNORECASE)
_INSTITUTION_WORD_RE = re.compile(
    r"\b(?:university|college|hospital|institute|school|cent(?:er|re)|society"
    r"|association|academy|foundation|council|committee|board|department|clinic"
    r"|program|agency|organization)\b", re.IGNORECASE)
#: A bracketed note ("[6 CME credits]") qualifies its record.
_BRACKET_NOTE_RE = re.compile(r"\[[^\]]*\]")
#: A label opening a part ("Role:", "Location:") names a field, not a value.
_PART_LABEL_RE = re.compile(r"(?:^|[\t\n;|])\s*([^\W\d_]+(?:\s+[^\W\d_]+)?)\s*:")
#: Words that say how the owner took part, or what happened to a record later,
#: never which record it is: a clause whose new words are only these is the
#: record's own detail ("Invited Speaker", "renewed <year>").
_NOT_A_RECORD_WORDS = frozenset({
    "invited", "speaker", "keynote", "presenter", "presented", "presentation",
    "nominated", "selected", "recognized", "renewal", "renewed", "recertified",
    "recertification", "expires", "expiration", "expired", "maintenance",
    "published", "edition"})
#: Connectives long enough to be a distinctive token ("through", "between").
_CLAUSE_CONNECTIVES = frozenset({
    "through", "about", "after", "before", "under", "while", "within", "which",
    "their", "there", "these", "those", "where", "other", "since", "until",
    "among", "between", "including", "during"})
_PROSE_WORD_RE = re.compile(r"[^\W\d_]{4,}")

#: The field a mentee code's record names its mentee in, and a person's name
#: there: two or more capitalised words ("Jane Q. Doe", "Ana Ruiz-Lee").
MENTEE_NAME_FIELD = "mentee_name"
_PERSON_NAME_RE = re.compile(r"^[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)+$")
_NAME_LIST_SEPARATOR_RE = re.compile(r";|,|\band\b|&")
#: The field a degree record files its year under.
DEGREE_YEAR_FIELD = "year"
#: The field each numbered credential's record files its one number under,
#: and such a number: five or more digits in a row.
_IDENTIFIER_FIELDS = MappingProxyType({"F1": "license_number", "M2D": "patent_number"})
_IDENTIFIER_NUMBER_RE = re.compile(r"\d{5,}")


class Clause(NamedTuple):
    """One record-shaped stretch of an entry's text and the years it writes."""
    text: str
    years: frozenset[str]


class DateAnchor(NamedTuple):
    """One written date: its span in the text and the years it holds."""
    start: int
    end: int
    years: frozenset[str]


class YearSpan(NamedTuple):
    """The first and last year one stage-4 record's values write."""
    first: int
    last: int


class RecordWords(NamedTuple):
    """The distinctive words and the years of a stage-4 record's values, and
    the year span of each record: one span, or one per `stage4_records`
    child that writes a year."""
    words: frozenset[str]
    years: frozenset[str]
    spans: tuple[YearSpan, ...]


class UncoveredClause(NamedTuple):
    """A clause the record does not stand for, the words the render check
    looks for (its words the record lacks when there are enough of them,
    else all its words), and which shape found it: CLAUSE_NEW_WORDS, or
    CLAUSE_SPAN_OUTSIDE for a clause whose words the record holds."""
    text: str
    years: frozenset[str]
    words: frozenset[str]
    shape: str = CLAUSE_NEW_WORDS


class OutputLines(NamedTuple):
    """Per rendered line, its distinctive words and its years."""
    words: list[set[str]]
    years: list[set[str]]


class MultiRecordVerdict(NamedTuple):
    """One entry's severity, message and evidence, in `_finding`'s positional
    order."""
    severity: str
    message: str
    evidence: list[str]


def _has_payload(text: str) -> bool:
    """Whether a stretch of entry text names anything besides a date."""
    return any(not _DATE_WORD_RE.match(word) and word.lower() not in _PAYLOAD_FILLER
               for word in _PAYLOAD_WORD_RE.findall(text))


def _anchor_year(match: re.Match[str]) -> str:
    """The four-digit year a `_DATE_YEAR_RE` match writes; a two-digit year
    takes the shared century pivot, as stage 4 reads it."""
    if match.group("year"):
        return match.group("year")
    two_digits = match.group("slash_yy") or match.group("apostrophe_yy")
    return str(expand_two_digit_year(int(two_digits)))


def _date_anchors(text: str, two_digit_years: bool) -> list[DateAnchor]:
    """Each written date of the text: a run of years joined by date-only
    gaps, reading two-digit years only when `two_digit_years`. A month or
    day written before a year stays in the text around it; `_has_payload`
    and `_clause_words` read past date words."""
    anchors: list[DateAnchor] = []
    for match in _DATE_YEAR_RE.finditer(text):
        if not (match.group("year") or two_digit_years):
            continue
        year = _anchor_year(match)
        if anchors and _DATE_GAP_RE.match(text[anchors[-1].end:match.start()]):
            last = anchors[-1]
            anchors[-1] = DateAnchor(last.start, match.end(), last.years | {year})
        else:
            anchors.append(DateAnchor(match.start(), match.end(), frozenset({year})))
    return anchors


def _is_parenthetical(text: str) -> bool:
    """An aside of the clause before it: the clause opens a parenthesis that
    never closes, or that closes with nothing after it."""
    opening = _PAREN_OPENING_RE.match(text)
    if not opening:
        return False
    close = text.find(")", opening.end())
    return close < 0 or not _has_payload(text[close + 1:])


def _opens_inside_parens(part: str, position: int) -> bool:
    """Whether the words after `position` sit inside a parenthesis an earlier
    clause opened -- "(won an award <year>, <meeting>, <city>)" is that
    clause's aside, not a record. Punctuation, digits and closing parentheses after
    `position` are stepped over first."""
    depth = part.count("(", 0, position) - part.count(")", 0, position)
    for char in part[position:]:
        if char == ")":
            depth -= 1
        elif not (char.isspace() or char.isdigit() or char in _PAREN_LEAD_CHARS):
            break
    return depth > 0


def _part_clauses(part: str, two_digit_years: bool) -> list[Clause]:
    """One part's dated clauses. A part whose dates are not separated by any
    words is one clause (a record with a list of dates); otherwise each date
    takes the words after it when the part opens with a date ("<year> <talk>
    <year> <talk>"), else the words before it ("<role>, <years> <role>,
    <years>")."""
    anchors = _date_anchors(part, two_digit_years)
    if not anchors:
        return []
    between = [part[a.end:b.start] for a, b in zip(anchors, anchors[1:])]
    if not any(_has_payload(gap) for gap in between):
        return [Clause(part, frozenset().union(*(a.years for a in anchors)))]
    leading = not _has_payload(part[:anchors[0].start])
    clauses = []
    for i, anchor in enumerate(anchors):
        if leading:
            end = anchors[i + 1].start if i + 1 < len(anchors) else len(part)
            text = part[anchor.start:end]
        else:
            text = part[anchors[i - 1].end if i else 0:anchor.end]
        if i and (_is_parenthetical(text)
                  or (leading and _opens_inside_parens(part, anchor.end))):
            continue
        clauses.append(Clause(text, anchor.years))
    return clauses


def _names_title_and_institution(part: str) -> bool:
    return bool(_TITLE_WORD_RE.search(part) and _INSTITUTION_WORD_RE.search(part))


def _is_dated_event(part: str, record_words: frozenset[str] | None) -> bool:
    """A part that writes a year and MULTI_RECORD_MIN_NEW or more distinctive
    words, none of them the record's: an event beside a role ("<committee>
    member; <named> Symposium in March, 2019", X6 RVTAQT-08). A part that
    shares a word with the record is the record's own head (a mentee's name
    and degree year, a residency's dates beside its director's line).
    `record_words` None judges no part an event."""
    if record_words is None or not _CLAUSE_YEAR_RE.search(part):
        return False
    words = _clause_words(part)
    return len(words) >= MULTI_RECORD_MIN_NEW and not words & record_words


def _record_clauses(text: str, record_words: frozenset[str] | None) -> list[Clause]:
    """The record-shaped clauses of an entry's text: its dated clauses when
    there are two or more, else its parts that each name a title and an
    institution or are a dated event (see `_is_dated_event`). A text with
    fewer than two dated clauses holds at most one dated event, so an event
    is a clause only beside a named part. Fewer than two means the text
    reads as one record."""
    two_digit_years = not _CLAUSE_YEAR_RE.search(text)
    dated = [clause for part in _STRONG_SEPARATOR_RE.split(text)
             for clause in _part_clauses(part, two_digit_years)]
    if len(dated) >= 2:
        return dated
    return [Clause(part, frozenset(_CLAUSE_YEAR_RE.findall(part)))
            for part in _SOFT_SEPARATOR_RE.split(text)
            if _names_title_and_institution(part) or _is_dated_event(part, record_words)]


def _clause_words(text: str) -> set[str]:
    """A clause's distinctive words (the render lints' 5+-letter tokens) less
    date words, connectives, a part's opening label and a bracketed note."""
    text = _BRACKET_NOTE_RE.sub(" ", text)
    labels = {token for match in _PART_LABEL_RE.finditer(text)
              for token in _long_word_tokens(match.group(1))}
    return {token for token in _long_word_tokens(text)
            if token not in labels and token not in _CLAUSE_CONNECTIVES
            and not _DATE_WORD_RE.match(token)}


def _is_prose(text: str) -> bool:
    """A sentence about a record rather than a record; see
    MULTI_RECORD_PROSE_MIN_LOWERCASE."""
    words = [word for word in _PROSE_WORD_RE.findall(text) if not _DATE_WORD_RE.match(word)]
    lowercase = sum(1 for word in words if word[0].islower())
    return (lowercase >= MULTI_RECORD_PROSE_MIN_LOWERCASE
            and lowercase > len(words) - lowercase)


def _years_of(value: object) -> frozenset[str]:
    return frozenset(year for leaf in _leaf_strings(value)
                     for year in _CLAUSE_YEAR_RE.findall(leaf))


def _year_span(years: frozenset[str]) -> YearSpan | None:
    return YearSpan(int(min(years)), int(max(years))) if years else None


def _record_spans(fields: Mapping[str, object]) -> tuple[YearSpan, ...]:
    """The record's year span, or each `stage4_records` child's."""
    children = fields.get(STAGE4_RECORDS_KEY)
    records = ([child for child in children if isinstance(child, Mapping)]
               if isinstance(children, list) and children else [fields])
    return tuple(span for span in map(_year_span, map(_years_of, records)) if span)


def _record_words(fields: Mapping[str, object]) -> RecordWords:
    leaves = _leaf_strings(fields)
    return RecordWords(
        frozenset(token for leaf in leaves for token in _long_word_tokens(leaf)),
        _years_of(fields), _record_spans(fields))


def _outside_every_span(years: frozenset[str], spans: tuple[YearSpan, ...]) -> bool:
    return bool(spans) and any(
        not any(span.first <= int(year) <= span.last for span in spans) for year in years)


def _dated_own_clause(clauses: list[Clause], words: list[set[str]],
                      record: RecordWords) -> int | None:
    """The clause the record's dates stand for: of the clauses whose years
    the record holds, the one sharing most words with it. It can differ from
    the clause sharing most words: a committee's membership span shares more
    words with the record than the office inside it whose dates the record
    holds ("<committee>, 2011-2016, Chair 2014-15")."""
    dated = [i for i, clause in enumerate(clauses)
             if clause.years and clause.years <= record.years]
    return max(dated, key=lambda i: len(words[i] & record.words)) if dated else None


def _span_outside(clause: Clause, clause_words: set[str], own_words: set[str] | None,
                  record: RecordWords) -> bool:
    """CLAUSE_SPAN_OUTSIDE for a clause whose words the record holds; on a
    one-record entry (`own_words` given) the clause the record's dates stand
    for names a role this one lacks."""
    return (bool(clause_words) and _outside_every_span(clause.years, record.spans)
            and not _is_prose(clause.text)
            and (own_words is None or any(_TITLE_WORD_RE.fullmatch(word)
                                          for word in own_words - clause_words)))


def _uncovered_clauses(clauses: list[Clause], record: RecordWords,
                       judge_spans: bool) -> list[UncoveredClause]:
    """The clauses other than the one the record covers best that carry enough
    of their own (see MULTI_RECORD_MIN_NEW) and are not prose; and, when
    `judge_spans`, the clauses other than the one the record's dates stand
    for whose words the record holds but whose span it lacks
    (CLAUSE_SPAN_OUTSIDE)."""
    words = [_clause_words(clause.text) for clause in clauses]
    own = max(range(len(clauses)), key=lambda i: len(words[i] & record.words))
    dated_own = _dated_own_clause(clauses, words, record) if judge_spans else None
    uncovered = []
    for i, clause in enumerate(clauses):
        new_words = words[i] - record.words
        if not new_words:
            if dated_own is not None and _span_outside(
                    clause, words[i], words[dated_own], record):
                uncovered.append(UncoveredClause(
                    clause.text, clause.years, frozenset(words[i]), CLAUSE_SPAN_OUTSIDE))
            continue
        if i == own or not new_words - _NOT_A_RECORD_WORDS:
            continue
        new = len(new_words) + len(clause.years - record.years)
        if (new < MULTI_RECORD_MIN_NEW
                or new < MULTI_RECORD_NEW_SHARE * (len(words[i]) + len(clause.years))
                or _is_prose(clause.text)):
            continue
        checked = new_words if len(new_words) >= MULTI_RECORD_MIN_NEW else words[i]
        uncovered.append(UncoveredClause(clause.text, clause.years, frozenset(checked)))
    return uncovered


def _split_uncovered_clauses(clauses: list[Clause],
                             record: RecordWords) -> list[UncoveredClause]:
    """On an entry stage 4 split into `stage4_records`, the clauses whose
    words the records hold and that write a year outside every record's
    span (CLAUSE_SPAN_OUTSIDE). A clause with words of its own is not judged
    here: stage 4 splits an entry by its records, and leaves a record's
    detail words unfiled."""
    uncovered = []
    for clause in clauses:
        words = _clause_words(clause.text)
        if not words - record.words and _span_outside(clause, words, None, record):
            uncovered.append(UncoveredClause(
                clause.text, clause.years, frozenset(words), CLAUSE_SPAN_OUTSIDE))
    return uncovered


def _output_lines(blocks: list[tuple[str, str]]) -> OutputLines:
    lines = [line for _, text in blocks for line in str(text).split("\n") if line.strip()]
    return OutputLines([_long_word_tokens(line) for line in lines],
                       [set(_CLAUSE_YEAR_RE.findall(line)) for line in lines])


def _clause_rendered(clause: UncoveredClause, output: OutputLines) -> bool:
    """Whether one rendered line carries the clause: RENDER_TOKEN_OVERLAP of
    its words and, for a dated clause, every year it writes -- the same words
    beside other years are the same title held at another time, a different
    record."""
    return any(len(clause.words & words) >= RENDER_TOKEN_OVERLAP * len(clause.words)
               and clause.years <= years
               for words, years in zip(output.words, output.years))


def _clause_evidence(clauses: list[UncoveredClause]) -> list[str]:
    return [clause.text.strip(" \t,;.)-–—")[:FIELD_EVIDENCE_VALUE_CHARS]
            for clause in clauses[:FIELD_EVIDENCE_MAX_VALUES]]


def _shape_note(clauses: list[UncoveredClause]) -> str:
    """The shapes other than CLAUSE_NEW_WORDS that found these clauses, as a
    message suffix."""
    shapes = sorted({clause.shape for clause in clauses} - {CLAUSE_NEW_WORDS})
    return f" [{', '.join(shapes)}]" if shapes else ""


def _clause_verdict(entry: _FieldsEntry, output: OutputLines) -> MultiRecordVerdict | None:
    """WARN when a clause the record (or, on a split entry, each of its
    records) does not stand for is on no rendered line, INFO when each is
    on one (rendered by another entry, or fused into the record's row)."""
    record = _record_words(entry.fields)
    judged = entry.code not in _GRANT_CODES_UNJUDGED
    clauses = _record_clauses(entry.text, record.words if judged else None)
    if len(clauses) < 2:
        return None
    children = entry.fields.get(STAGE4_RECORDS_KEY)
    if children:
        uncovered = _split_uncovered_clauses(clauses, record) if judged else []
        records = f"{len(children)} stage-4 records"
    else:
        uncovered = _uncovered_clauses(clauses, record, judge_spans=judged)
        records = "one stage-4 record"
    if not uncovered:
        return None
    absent = [clause for clause in uncovered if not _clause_rendered(clause, output)]
    head = (f"entry {entry.element_idx} ({entry.code}): {len(clauses)} record-shaped "
            f"clauses, {records}")
    if absent:
        return MultiRecordVerdict(
            "WARN", f"{head}; {len(absent)} other clause(s) on no line of the output "
            f"(#1243){_shape_note(absent)}", _clause_evidence(absent))
    return MultiRecordVerdict(
        "INFO", f"{head}; the other clause(s) are in the output (#1243){_shape_note(uncovered)}",
        _clause_evidence(uncovered))


def _person_names(value: object) -> list[str]:
    """The items of a `mentee_name` value that read as a person's name."""
    if not isinstance(value, str):
        return []
    return [item for item in _NAME_LIST_SEPARATOR_RE.split(value)
            if _PERSON_NAME_RE.match(item.strip())]


def _identifier_numbers(text: str) -> set[str]:
    return set(_IDENTIFIER_NUMBER_RE.findall(text))


def _fused_values_verdict(entry: _FieldsEntry, head: str,
                          absent: list[str]) -> MultiRecordVerdict:
    """WARN when a value the one record fuses is on no rendered line, INFO
    when each is on one."""
    evidence = [entry.text[:FIELD_EVIDENCE_VALUE_CHARS]]
    if absent:
        return MultiRecordVerdict(
            "WARN", f"{head}; {len(absent)} on no line of the output (#1243)", evidence)
    return MultiRecordVerdict(
        "INFO", f"{head}; each is on a line of the output (#1243)", evidence)


def _identifier_verdict(entry: _FieldsEntry, head: str,
                        output_digits: str) -> MultiRecordVerdict | None:
    """Two or more licence or patent numbers in the text of an F1/M2D entry
    whose number field holds fewer: WARN when one is in neither the record
    nor the document."""
    number_field = _IDENTIFIER_FIELDS.get(entry.code)
    if not number_field:
        return None
    numbers = _identifier_numbers(entry.text)
    filed = _identifier_numbers(str(entry.fields.get(number_field) or ""))
    if len(numbers) < 2 or len(filed) >= len(numbers):
        return None
    held = {n for leaf in _leaf_strings(entry.fields) for n in _identifier_numbers(leaf)}
    lost = [n for n in numbers - held if n not in output_digits]
    return MultiRecordVerdict(
        "WARN" if lost else "INFO",
        f"{head} for {len(numbers)} numbers; {len(lost)} in neither the record nor "
        f"the output (#1243)", [entry.text[:FIELD_EVIDENCE_VALUE_CHARS]])


def _mentee_verdict(entry: _FieldsEntry, head: str,
                    output: OutputLines) -> MultiRecordVerdict | None:
    """Two or more mentees in one record's `mentee_name`. A mentee is on the
    output when one rendered line carries RENDER_TOKEN_OVERLAP of their
    name's 5+-letter words; a name with none cannot be checked and counts as
    on it."""
    names = _person_names(entry.fields.get(MENTEE_NAME_FIELD))
    if len(names) < 2:
        return None
    absent = [name for name in names if not _clause_rendered(
        UncoveredClause(name, frozenset(), frozenset(_long_word_tokens(name))), output)]
    return _fused_values_verdict(entry, f"{head} naming {len(names)} mentees", absent)


def _degree_year_verdict(entry: _FieldsEntry, head: str,
                         output: OutputLines) -> MultiRecordVerdict | None:
    """Two or more years in a degree record's `year`. A year is on the output
    when one rendered line carries it and RENDER_TOKEN_OVERLAP of the
    record's other words (its degree, institution, discipline)."""
    if entry.code != ACADEMIC_DEGREE_CODE:
        return None
    years = sorted(set(_CLAUSE_YEAR_RE.findall(str(entry.fields.get(DEGREE_YEAR_FIELD) or ""))))
    if len(years) < 2:
        return None
    words = frozenset(token for key, value in entry.fields.items() if key != DEGREE_YEAR_FIELD
                      for leaf in _leaf_strings(value) for token in _long_word_tokens(leaf))
    absent = [year for year in years
              if not _clause_rendered(UncoveredClause(year, frozenset({year}), words), output)]
    return _fused_values_verdict(entry, f"{head} for {len(years)} degree years", absent)


def _fused_verdict(entry: _FieldsEntry, output: OutputLines,
                   output_digits: str) -> MultiRecordVerdict | None:
    """One record that holds several in its values: licence or patent
    numbers, mentees, or degree years. WARN when one of them is on no line
    of the rendered document, INFO when they render fused into the record."""
    head = f"entry {entry.element_idx} ({entry.code}): stage 4 returned one record"
    return (_identifier_verdict(entry, head, output_digits)
            or _mentee_verdict(entry, head, output)
            or _degree_year_verdict(entry, head, output))


#: Codes the multi-record lint does not judge: offschema_fields' set, plus
#: Personal Data named on its own, so the lint keeps skipping A's contact lines
#: even if offschema_fields starts judging A (#1245).
_MULTI_RECORD_SKIPPED_CODES = _OFFSCHEMA_SKIPPED_CODES | frozenset({PERSONAL_DATA_CODE})


def lint_multi_record_coverage(stage4: dict, blocks: list[tuple[str, str]]) -> list[dict]:
    """A stage-4 entry whose text holds several records while stage 4
    returned one record and no `stage4_records` (#1243): two or more dated
    clauses, or parts that each name a title and an institution (with any
    dated event beside them), of which the record stands for one; or one
    record holding several mentees, degree years or licence/patent numbers.
    On an entry with `stage4_records`, only a clause whose words the records
    hold and whose year lies outside every record's span (CLAUSE_SPAN_OUTSIDE).
    One finding per entry; WARN when
    a clause it left out, or one of the values it fused, is on no line of the
    rendered document (track changes included), INFO otherwise. Skips the
    codes `offschema_fields` skips (the section writes the entry's text, a
    stage-5 formatter rewrites it whole, or it is personal data). An entry
    stage 6's fan-out splits needs no skip of its own: fan-out splits a
    record list only when the list's values hold every word of the entry's
    text, so every clause is covered; it also splits `stage4_records`,
    whose clauses the span shape reads against the rows fan-out renders."""
    output = _output_lines(blocks)
    output_digits = _LINE_SENTINEL.join(
        re.sub(r"\D", "", line) for _, text in blocks for line in str(text).split("\n"))
    findings = []
    for entry in _fields_entries(stage4):
        if not entry.code or entry.code in _MULTI_RECORD_SKIPPED_CODES or not entry.fields:
            continue
        verdict = _clause_verdict(entry, output)
        if not verdict and not entry.fields.get(STAGE4_RECORDS_KEY):
            verdict = _fused_verdict(entry, output, output_digits)
        if verdict:
            findings.append(_finding("multi_record_coverage", *verdict))
    return findings


# --- grant_boundary ----------------------------------------------------------
#
# Stage 2 cuts a grant list into records, and a cut one line or row off makes
# every grant after it carry the next grant's title, or the PI, effort and
# period of the grant before it (#1226; batch EBYSBC, 2026-10-02, class E5: 11
# CVs). Each record still extracts and renders, so no loss lint sees it. The
# shapes below are what the cut leaves in the stage-4 entries of one grant
# list. Report-only: nothing is re-cut.

#: The codes stage 6 renders as grants, in the funding subsections' order.
GRANT_CODES = tuple(code for code, _title in _FUNDING_SECTIONS)
#: A label opening an entry's first line or cell ("Agency:", "Grant Title:",
#: "P.I.:", "% Effort:", "Current position:"), and the same label anywhere a
#: line or cell starts. Shared by `grant_boundary` and `record_boundary`.
#: The label cannot run across a tab, newline or " | ", so matching the
#: whole text reads only its first line or cell.
_ENTRY_LABEL = r"[\s*]*([A-Za-z%][A-Za-z.%#/ ]{0,24}?)\s*:"
_ENTRY_HEAD_LABEL_RE = re.compile(rf"^{_ENTRY_LABEL}")
_ENTRY_LINE_LABEL_RE = re.compile(rf"(?:^|\t|\n|\s\|\s){_ENTRY_LABEL}")
#: Labels that name a grant's people or effort. A grant record opens with its
#: sponsor, number, title or dates; an entry whose first line is one of these
#: holds the tail of the record before it (EBYSBC CXRYCF, CTWLTR).
GRANT_PERSONNEL_LABELS = frozenset({
    "pi", "p.i.", "pi name", "personnel", "percent effort", "% effort", "effort"})
#: The fields that name a grant's sponsor, its title, and its period.
GRANT_SOURCE_FIELDS = ("agency", "grant_number")
GRANT_TITLE_FIELD = "title"
GRANT_DATE_FIELDS = ("start_date", "end_date")
#: The fields that hold a record's detail rather than name it.
GRANT_DETAIL_FIELDS = ("pi_name", "pi_role", "percent_effort", *GRANT_DATE_FIELDS,
                       "total_funding", "total_funding_requested")
#: A grant list's record shape is its first entry's opening label, carried by
#: at least GRANT_HEAD_LABEL_MIN_COUNT entries and GRANT_HEAD_LABEL_MIN_SHARE
#: of the list. A list this long can end in a title its record lost. On the 63-run EBYSBC/s7ab/pilot
#: farm a drifted list keeps under half its entries on the first label (ZCTARO:
#: 12 of 26), so the share is a third.
GRANT_LIST_MIN_ENTRIES = 3
GRANT_HEAD_LABEL_MIN_COUNT = 2
GRANT_HEAD_LABEL_MIN_SHARE = 1 / 3
#: An entry after the first of its list that names no sponsor, number or title
#: is a record's stray tail when it carries this many words (a description
#: paragraph) or this many detail fields; shorter, it is a year or a
#: sub-heading line ("Funded Training Grants").
GRANT_ORPHAN_MIN_WORDS = 12
GRANT_ORPHAN_MIN_DETAIL_FIELDS = 2
#: A list's line-order shape is a sponsor or number line, then the title,
#: held by as many entries as a label shape needs. Any entry may hold it, not
#: the first only: RVTAQT's first grant is a whole record of another order.
#: A title is found by its first GRANT_TITLE_PREFIX_CHARS characters with
#: whitespace dropped, and only when it has GRANT_TITLE_MIN_CHARS: stage 4
#: trims or re-punctuates a long title's tail, not its opening words.
GRANT_TITLE_PREFIX_CHARS = 20
GRANT_TITLE_MIN_CHARS = 8
#: A sponsor or number shorter than this ("VA", "NIH") is found inside too
#: many titles to place a line.
GRANT_SOURCE_MIN_CHARS = 3
#: A line or cell break inside an entry's text: tab, newline, or " | ".
_GRANT_SEGMENT_RE = re.compile(r"\t|\n|\s\|\s")


class ListEntry(NamedTuple):
    """One stage-4 entry of a heading's list as the boundary lints read it
    (`grant_boundary` over the grant lists, `record_boundary` over the
    others): its first line's label (lowercased, '' when it opens with none)
    and every label that starts one of its lines or cells."""
    element_idx: object
    code: str
    text: str
    fields: Mapping[str, object]
    head_label: str
    line_labels: frozenset[str]


def _normalized_label(label: str) -> str:
    return " ".join(label.lower().split())


def _list_entry(raw: dict) -> ListEntry:
    text = str(raw.get("text") or "")
    fields = raw.get("extracted_fields")
    head = _ENTRY_HEAD_LABEL_RE.match(text.strip())
    return ListEntry(
        raw.get("element_idx_start"), str(raw.get("taxonomy_code") or ""), text,
        fields if isinstance(fields, Mapping) else {},
        _normalized_label(head.group(1)) if head else "",
        frozenset(_normalized_label(m.group(1)) for m in _ENTRY_LINE_LABEL_RE.finditer(text)))


def _heading_lists(stage4: dict, grants: bool) -> list[list[ListEntry]]:
    """Runs of consecutive entries filed under one heading, the lists stage 2
    cut into records: of grant codes when `grants`, else of every other code.
    An entry of the other kind closes a run."""
    lists: list[list[ListEntry]] = []
    heading = None
    for raw in stage4.get("entries", []):
        code = raw.get("taxonomy_code")
        if not code or (code in GRANT_CODES) != grants:
            heading = None
            continue
        this = tuple(raw.get("hierarchy") or [])
        if heading is None or this != heading:
            lists.append([])
        heading = this
        lists[-1].append(_list_entry(raw))
    return lists


def _grant_lists(stage4: dict) -> list[list[ListEntry]]:
    return _heading_lists(stage4, grants=True)


def _filled(fields: Mapping[str, object], keys: tuple[str, ...] | str) -> bool:
    keys = (keys,) if isinstance(keys, str) else keys
    return any(fields.get(key) not in (None, "", [], {}) for key in keys)


def _source_half(fields: Mapping[str, object]) -> bool:
    """A sponsor or number with no title and no period: half a record."""
    return (_filled(fields, GRANT_SOURCE_FIELDS) and not _filled(fields, GRANT_TITLE_FIELD)
            and not _filled(fields, GRANT_DATE_FIELDS))


def _title_half(fields: Mapping[str, object]) -> bool:
    return _filled(fields, GRANT_TITLE_FIELD) and not _filled(fields, "agency")


def _split_pairs(grants: list[ListEntry]) -> dict[int, str]:
    """Two neighbours, one holding a sponsor or number with no title or
    period, the other a title with no sponsor: one record cut in two."""
    reasons = {}
    for i, (first, second) in enumerate(zip(grants, grants[1:])):
        if ((_source_half(first.fields) and _title_half(second.fields))
                or (_title_half(first.fields) and _source_half(second.fields))):
            reasons[i] = (f"it and entry {second.element_idx} split one record: one has "
                          f"the sponsor or number, the other the title")
    return reasons


def _personnel_heads(grants: list[ListEntry]) -> dict[int, str]:
    return {i: f"its first line is the '{grant.head_label}' line, which closes a record"
            for i, grant in enumerate(grants) if grant.head_label in GRANT_PERSONNEL_LABELS}


def _head_label_drift(grants: list[ListEntry]) -> dict[int, str]:
    """In a list whose records open with the first entry's label, an entry
    that opens instead with a label those records carry mid-record."""
    first = grants[0].head_label
    if not first:
        return {}
    opening = [grant for grant in grants if grant.head_label == first]
    if len(opening) < max(GRANT_HEAD_LABEL_MIN_COUNT, GRANT_HEAD_LABEL_MIN_SHARE * len(grants)):
        return {}
    inner = frozenset().union(*(grant.line_labels for grant in opening)) - {first}
    return {i: (f"its records open with '{first}:', this entry with '{grant.head_label}:', "
                f"a label from inside them")
            for i, grant in enumerate(grants) if grant.head_label in inner}


class GrantLineOrder(NamedTuple):
    """Where a grant entry's own values sit among its lines: does its first
    line hold its title, its sponsor or number, and does a later line hold
    the title or a sponsor or number."""
    title_first: bool
    source_first: bool
    title_later: bool
    source_later: bool


def _grant_line_order(grant: ListEntry) -> GrantLineOrder:
    raw_segments = _GRANT_SEGMENT_RE.split(grant.text.strip())
    segments = [squash(segment) for segment in raw_segments]
    head = _ENTRY_HEAD_LABEL_RE.match(raw_segments[0])
    first = squash(raw_segments[0][head.end():]) if head else segments[0]
    rest = "".join(segments[1:])
    title = squash(grant.fields.get(GRANT_TITLE_FIELD) or "")[:GRANT_TITLE_PREFIX_CHARS]
    if len(title) < GRANT_TITLE_MIN_CHARS:
        title = ""
    sources = [squash(grant.fields.get(key) or "") for key in GRANT_SOURCE_FIELDS]
    sources = [source for source in sources if len(source) >= GRANT_SOURCE_MIN_CHARS]
    return GrantLineOrder(
        bool(title) and first.startswith(title),
        any(source in segments[0] for source in sources),
        bool(title) and title in rest,
        any(source in rest for source in sources))


def _titleless(grant: ListEntry) -> bool:
    return (_filled(grant.fields, GRANT_SOURCE_FIELDS)
            and not _filled(grant.fields, GRANT_TITLE_FIELD))


def _line_order_drift(grants: list[ListEntry]) -> dict[int, str]:
    """In a list whose records open with a sponsor or number line and carry
    the title on a later line, a run of entries that each open with a title
    and then a sponsor or number line, starting right after an entry that
    names a sponsor or number but no title: the cut gave each title to the
    next record's sponsor (X6 RVTAQT-01). The titleless entry is the record
    whose title opens the run; an entry of that order with no titleless
    record before the run is a record of its own."""
    orders = [_grant_line_order(grant) for grant in grants]
    shaped = sum(order.source_first and order.title_later for order in orders)
    if shaped < max(GRANT_HEAD_LABEL_MIN_COUNT, GRANT_HEAD_LABEL_MIN_SHARE * len(grants)):
        return {}
    reasons = {}
    for i, order in enumerate(orders[1:], start=1):
        if not (order.title_first and order.source_later and not order.source_first):
            continue
        if i - 1 in reasons:
            reasons[i] = ("its records open with a sponsor or number line; this entry, like "
                          "the one before it, opens with a title and then a sponsor line")
        elif _titleless(grants[i - 1]):
            reasons[i - 1] = f"it holds no title, and entry {grants[i].element_idx} opens with one"
            reasons[i] = ("its records open with a sponsor or number line, this entry with a "
                          "title and then a sponsor line: the title of the record before it")
    return reasons


def _orphans(grants: list[ListEntry]) -> dict[int, str]:
    """An entry after the first that names no sponsor, number or title but
    carries a record's detail; and a list's last entry, when it holds only a
    title while its siblings name a sponsor, number or period: the title its
    record lost."""
    reasons = {}
    for i, grant in enumerate(grants[1:], start=1):
        if _filled(grant.fields, (*GRANT_SOURCE_FIELDS, GRANT_TITLE_FIELD)):
            continue
        details = sum(_filled(grant.fields, key) for key in GRANT_DETAIL_FIELDS)
        if (len(grant.text.split()) >= GRANT_ORPHAN_MIN_WORDS
                or details >= GRANT_ORPHAN_MIN_DETAIL_FIELDS):
            reasons[i] = "it names no sponsor, number or title: another record's tail"
    last = grants[-1]
    if (len(grants) >= GRANT_LIST_MIN_ENTRIES
            and {key for key in last.fields if _filled(last.fields, key)} == {GRANT_TITLE_FIELD}
            and any(_filled(grant.fields, (*GRANT_SOURCE_FIELDS, *GRANT_DATE_FIELDS))
                    for grant in grants[:-1])):
        reasons.setdefault(len(grants) - 1,
                           "the list's last entry holds only a title, which its record lost")
    return reasons


def lint_grant_boundary(stage4: dict) -> list[dict]:
    """A grant list stage 2 cut one line or row off (#1226; EBYSBC class E5):
    neighbours that split one record between them (`_split_pairs`), an entry
    whose first line is a PI or effort line (`_personnel_heads`), an entry
    opening with a label its siblings carry mid-record (`_head_label_drift`),
    or a stray tail or title (`_orphans`). WARN, one finding per entry,
    naming every shape it shows. Reads stage 4 only: the grants render, with
    each other's values. Not judged: a list cut at a stage-2 batch boundary
    as such, since stage 2 records no batch cuts."""
    findings = []
    for grants in _grant_lists(stage4):
        reasons: dict[int, list[str]] = {}
        for shape in (_split_pairs, _personnel_heads, _head_label_drift, _line_order_drift,
                      _orphans):
            for i, reason in shape(grants).items():
                reasons.setdefault(i, []).append(reason)
        for i in sorted(reasons):
            grant = grants[i]
            findings.append(_finding(
                "grant_boundary", "WARN",
                f"entry {grant.element_idx} ({grant.code}): grant record boundary off -- "
                f"{'; '.join(reasons[i])} (#1226)",
                [grant.text[:FIELD_EVIDENCE_VALUE_CHARS]]))
    return findings


# --- record_boundary ---------------------------------------------------------
#
# The same stage-2 cut outside the grant lists: an entry that opens with a
# labelled line ("Current position:", "Description:", "Inventors:") its list's
# records carry inside them holds the tail of the record before it (X6 IEUPKK
# 438/444/466/467, a mentee's current position opening the next mentee's
# entry, class E5). The record before renders without that line, or both
# render with it. Report-only. Grant lists are `grant_boundary`'s.

#: A label is a list's inner line when this many of its other entries carry
#: it after their first line, and this share of the list, and at least twice
#: as many as open with it (a mentee list that drifted twice: 6 carry it,
#: 2 open with it).
RECORD_INNER_LABEL_MIN_COUNT = 2
RECORD_INNER_LABEL_MIN_SHARE = 1 / 3
RECORD_INNER_LABEL_MIN_RATIO = 2


def _inner_label_heads(entries: list[ListEntry]) -> dict[int, str]:
    reasons = {}
    for i, entry in enumerate(entries[1:], start=1):
        label = entry.head_label
        others = [other for j, other in enumerate(entries) if j != i]
        carriers = sum(label in other.line_labels and other.head_label != label
                       for other in others)
        openers = sum(other.head_label == label for other in others)
        if (carriers >= max(RECORD_INNER_LABEL_MIN_COUNT,
                            RECORD_INNER_LABEL_MIN_SHARE * len(entries))
                and carriers >= RECORD_INNER_LABEL_MIN_RATIO * openers):
            reasons[i] = (f"it opens with '{label}:', a line {carriers} of its list's records "
                          f"carry inside them: the tail of the record before it")
    return reasons


def lint_record_boundary(stage4: dict) -> list[dict]:
    """A non-grant list stage 2 cut one line off (X6 class E5): an entry after
    the first that opens with a labelled line its siblings carry mid-record.
    WARN, one finding per entry. Reads stage 4 only. Not judged: an
    unlabelled line on the wrong side of a cut (IEUPKK 113, 924), whose
    field-order test measured mostly false positives (doctor/PRECISION.md,
    X6-grant)."""
    findings = []
    for entries in _heading_lists(stage4, grants=False):
        for i, reason in sorted(_inner_label_heads(entries).items()):
            entry = entries[i]
            findings.append(_finding(
                "record_boundary", "WARN",
                f"entry {entry.element_idx} ({entry.code}): record boundary off -- {reason}",
                [entry.text[:FIELD_EVIDENCE_VALUE_CHARS]]))
    return findings


# --- grant_bucket ------------------------------------------------------------
#
# Stage 6 files a grant under Current, Past (Completed) or Pending from its
# code, status, heading and end date (`research_support.py`). Two filings
# still contradict the grant's own record (#1343; EBYSBC class E7): an
# application under a heading that says so, rendered as an award (ZDCXIV: 15
# grants under an "applied" heading), and a Current grant whose end date stage
# 6 cannot read but that ended (KYOPUV 589: a one-digit end year).
# `lint_bucket_status` judges the status field; this judges the heading and
# the end date. Report-only.

#: Codes rendered as awards: an application filed under one is credited as a
#: grant the CV says was only applied for.
_AWARD_BUCKETS = frozenset({"M2A", "M2B"})
_CURRENT_BUCKET, _PAST_BUCKET, _PENDING_BUCKET = GRANT_CODES
#: A trailing one- or two-digit year after a date separator ("8/30/1"),
#: read looser than `grant_end_year`, which takes a one-digit year only in
#: an mm/dd/y date and only when given the start date.
_SHORT_END_YEAR_RE = re.compile(r"[-/.](\d{1,2})\s*$")
#: "Not funded" names an application, so its "funded" is no award word.
_NOT_FUNDED_RE = re.compile(r"\b(?:not|non)[\s-]?funded\b")


def _application_heading(hierarchy: list[str]) -> bool:
    """The heading names applications and names no award bucket -- 3b's
    own vocabulary (`grant_status_corrector`), so the two cannot drift."""
    heading = " > ".join(hierarchy).lower()
    return (bool(_PENDING_HEADING_RE.search(heading))
            and not _AWARDED_HEADING_RE.search(_NOT_FUNDED_RE.sub(" ", heading)))


def _short_end_year(end_date: str, start_date: str, current_year: int) -> int | None:
    """A truncated end year read as the first year at or after the start year
    that ends in its digits ("9/1/07" to "8/30/1" is 2011); None without a
    start year."""
    short = _SHORT_END_YEAR_RE.search(end_date)
    start = grant_end_year(start_date, current_year)
    if not short or start is None:
        return None
    return year_at_or_after(short.group(1), start)


def _ended_year(fields: Mapping[str, object], current_year: int) -> int | None:
    """The year a grant's end date names, when it is before `current_year`."""
    end_date = str(fields.get("end_date") or "").strip()
    year = grant_end_year(end_date, current_year)
    if year is None:
        year = _short_end_year(end_date, str(fields.get("start_date") or ""), current_year)
    return year if year is not None and year < current_year else None


def _bucket_contradiction(entry: dict, buckets: set[str], current_year: int | None) -> str | None:
    """The contradiction's reason, or None. A None `current_year` judges the
    heading only, never the end date."""
    hierarchy = [str(level) for level in entry.get("hierarchy") or []]
    if (buckets & _AWARD_BUCKETS and _PENDING_BUCKET not in buckets
            and _application_heading(hierarchy)):
        return (f"rendered as an award ({'/'.join(sorted(buckets))}) under the heading "
                f"'{hierarchy[-1]}', which files applications")
    if current_year is None:
        return None
    fields = entry.get("extracted_fields")
    ended = _ended_year(fields if isinstance(fields, Mapping) else {}, current_year)
    if _CURRENT_BUCKET in buckets and _PAST_BUCKET not in buckets and ended is not None:
        return f"rendered under Current, but its end date reads as {ended}, before {current_year}"
    return None


def lint_grant_bucket(stage4: dict, blocks: list[tuple[str, str]],
                      current_year: int | None = None, *,
                      check_end_date: bool = True) -> list[dict]:
    """A grant rendered in a funding subsection its own record contradicts
    (#1343): an award subsection (Current or Past) under a heading that
    files applications, or Current with an end date before `current_year`,
    a truncated end year read against the start year. WARN, one finding per
    grant; a grant too short to locate in the document is not judged.
    `current_year` defaults to this year, as stage 6's own rebucket does, so
    re-doctoring an old render can newly flag a grant that ended since.
    `check_end_date=False` judges the heading only: the quality score's
    grant-bucket cap reads that shape alone (`quality_score.
    score_grant_application_as_award`), so a later rescore cannot move it."""
    year = None
    if check_end_date:
        year = current_year if current_year is not None else datetime.now().year
    rendered = _funding_haystacks(blocks)
    shared = _shared_entry_pieces(stage4.get("entries", []))
    findings = []
    for entry in stage4.get("entries", []):
        if entry.get("taxonomy_code") not in GRANT_CODES:
            continue
        buckets = {bucket for bucket, hay in rendered.items()
                   if _entry_rendered(entry.get("text"), hay.text, hay.tokens, shared)}
        reason = _bucket_contradiction(entry, buckets, year)
        if reason:
            findings.append(_finding(
                "grant_bucket", "WARN",
                f"entry {entry.get('element_idx_start')} ({entry.get('taxonomy_code')}): "
                f"{reason} (#1343)",
                [str(entry.get("text", ""))[:FIELD_EVIDENCE_VALUE_CHARS]]))
    return findings


# --- span_count --------------------------------------------------------------
#
# Stage 4 sometimes writes a record of separate years or terms ("2003, 2013",
# "1982-86, 1989-1995, 2004-2012") as one start_date-end_date pair, their
# min-max, and the row renders a continuous range the CV does not claim
# (batch RCBKFG, UYFRTL 33/34/48/49; #1245). Stage 6 shows the separate spans
# when stage 4 also kept them under a further-span key (`envelope_date_spans`),
# but not when it kept only the envelope, and no other lint compares the
# source's spans with the row's. Report-only.

#: A dash between the two ends of a span, any of the ways a CV types one.
_SPAN_DASH = r"[-‐-―−─]"
#: A written span: a year (a dotted or slashed month after it is read past),
#: then optionally a dash and an end year, a one- or two-digit end, or an
#: ongoing word.
_WRITTEN_SPAN_RE = re.compile(
    r"(?<![\d/.])((?:19|20)\d{2})(?:[./]\d{1,2}(?!\d))?(?![\d/])"
    rf"(?:\s*{_SPAN_DASH}\s*((?:19|20)\d{{2}}(?:[./]\d{{1,2}})?(?!\d)|\d{{1,2}}(?!\d)"
    r"|present|current|ongoing|now))?", re.IGNORECASE)
#: A month or term word, and the day after it, read past so "Fall 2014 -
#: Spring 2018" is one span; with the short term names ("Spr 2015") course
#: lists use. A comma straight after the word goes with it: "June, 2018" is
#: no list (#1585, batch YUYVIG: 14 of its 22 false positives).
_SPAN_MONTH_RE = re.compile(
    rf"(?:{_MONTH_PATTERN}|spr|sum|fa|win|wint)(?![a-z])"
    r"(?:,|\s*(?:\d{1,2}(?:st|nd|rd|th)?\b,?)?)",
    re.IGNORECASE)
#: Words that join the two ends of one span: "1980 to 1987", "between 1978
#: and 1988".
_SPAN_CONNECTOR_RE = re.compile(r"\s+(?:to|through|thru|until|till)\s*(?=\d)", re.IGNORECASE)
_SPAN_BETWEEN_RE = re.compile(r"\bbetween\s+((?:19|20)\d{2})\s+and\s+", re.IGNORECASE)
#: What separates two spans of one list: "2003, 2013", "2001; 2004", "2015
#: and 2017". Two years with none of these between them are a range split
#: across cells or lines ("2014<tab>...2016"), not a list.
_SPAN_LIST_SEPARATOR_RE = re.compile(r"[,;&]|\band\b", re.IGNORECASE)
#: The end of a span still running (`present`), for envelope comparison.
_SPAN_OPEN_END = 10_000
#: Codes whose line names the mentee's own milestones ("M.S. 1982; Ph.D.
#: 1986") rather than the record's span: the row's range is the mentorship.
_SPAN_COUNT_SKIPPED_PREFIXES = ("N",)


class WrittenSpan(NamedTuple):
    """One span of years as written: its first and last year, and where the
    text it was read from holds it."""
    first: int
    last: int
    start: int
    end: int


def _span_source(text: str) -> str:
    """`text` with month and term words read past and "X to Y" / "between X
    and Y" written as one dashed span, the form `_written_spans` reads."""
    return _SPAN_BETWEEN_RE.sub(r"\1-", _SPAN_CONNECTOR_RE.sub("-", _SPAN_MONTH_RE.sub("", text)))


def _written_spans(text: str) -> list[WrittenSpan]:
    """The year spans of `text` (already `_span_source`), in order. A one- or
    two-digit end is completed from the start year; one that would end before
    it (a month: "2019-05") leaves the start year alone."""
    spans: list[WrittenSpan] = []
    for match in _WRITTEN_SPAN_RE.finditer(text):
        first, end = int(match[1]), match[2]
        if end is None:
            last = first
        elif end.isalpha():
            last = _SPAN_OPEN_END
        elif len(end) <= 2:
            last = first - first % 10 ** len(end) + int(end)
        else:
            last = int(end[:4])
        spans.append(WrittenSpan(first, max(first, last), match.start(), match.end()))
    return spans


#: The gap between two lone years of one range: a dash right after the first
#: that no end follows ("2007– Professor of X, Y\t2012"), or one right before
#: the second that no start precedes ("2014 Professor of X, Y\tto 2019",
#: once `_span_source` has made "to" a dash). Not a gap a semicolon ends an
#: item in, or whose dash a comma follows ("2003 - Talk; 2013", "2003 -, 2013").
_SPAN_WRAPPED_GAP_RE = re.compile(
    rf"^(?!.*;)(?:\s*{_SPAN_DASH}(?!\s*,)|.*{_SPAN_DASH}\s*$)", re.DOTALL)
#: A dash right after a lone year: the year opens a span of its own.
_SPAN_OPEN_DASH_RE = re.compile(rf"\s*{_SPAN_DASH}")


def _join_wrapped_ranges(text: str, spans: list[WrittenSpan]) -> list[WrittenSpan]:
    """`spans` with a range a table cell or line wrapped joined into one: two
    consecutive lone years, the later second, whose dash sits in the gap with
    no year on its other side (`_SPAN_WRAPPED_GAP_RE`), the later one opening
    no dash of its own ("2011 - Talk, 2013 - Talk" is two items). Nothing
    lies between them that `_WRITTEN_SPAN_RE` reads as a year (#1585, batch
    YUYVIG: 12 of its 22 false positives). Source text only: a rendered
    "2011 - Title 2013 - Title" line is that list."""
    joined: list[WrittenSpan] = []
    for span in spans:
        before = joined[-1] if joined else None
        if (before is not None and before.first == before.last
                and span.first == span.last and span.first > before.first
                and _SPAN_WRAPPED_GAP_RE.search(text[before.end:span.start])
                and not _SPAN_OPEN_DASH_RE.match(text, span.end)):
            joined[-1] = WrittenSpan(before.first, span.last, before.start, span.end)
        else:
            joined.append(span)
    return joined


def _is_a_list(text: str, spans: list[WrittenSpan]) -> bool:
    """Whether consecutive spans of `text` are separated as items of one list."""
    return all(_SPAN_LIST_SEPARATOR_RE.search(text[before.end:after.start])
               for before, after in zip(spans, spans[1:]))


def _leaves_a_gap(spans: list[tuple[int, int]], envelope: tuple[int, int]) -> bool:
    """Whether a year inside `envelope` lies in none of `spans`."""
    reached = envelope[0] - 1
    for first, last in sorted(spans):
        if first > reached + 1:
            return True
        reached = max(reached, last)
    return False


def _envelope_of_separate_spans(text: str) -> tuple[tuple[int, int], int] | None:
    """The min-max of the text's spans and how many there are, when the text
    lists two or more spans that leave a year of that min-max uncovered."""
    source = _span_source(text)
    spans = _join_wrapped_ranges(source, _written_spans(source))
    years = list(dict.fromkeys((span.first, span.last) for span in spans))
    if not years:
        return None
    envelope = (min(first for first, _ in years), max(last for _, last in years))
    # One span, or spans that fill their min-max, leave no gap.
    if not _leaves_a_gap(years, envelope) or not _is_a_list(source, spans):
        return None
    return envelope, len(years)


def _renders_only_the_envelope(line: OutputLine, envelope: tuple[int, int]) -> bool:
    """Whether the line's one span inside `envelope` is the envelope itself."""
    inside = {(span.first, span.last) for span in _written_spans(_span_source(line.squashed))
              if envelope[0] <= span.first and span.last <= envelope[1]}
    return inside == {envelope}


def _span_text(envelope: tuple[int, int]) -> str:
    last = "present" if envelope[1] == _SPAN_OPEN_END else str(envelope[1])
    return f"{envelope[0]}-{last}"


def lint_span_count(stage4: dict, blocks: list[tuple[str, str]]) -> list[dict]:
    """A record whose source lists separate years or terms ("2003, 2013")
    while its rendered row shows one continuous range over them, their
    min-max (#1245, batch RCBKFG: UYFRTL 33/34/48/49). The row is the
    record's own line (`_record_lines`); the source is the entry's text,
    whose spans must be written as a list (`_SPAN_LIST_SEPARATOR_RE`) and
    leave a year of the range uncovered. Skips the codes `multi_record_coverage`
    skips except teaching, whose rows stage 5c rewrites from the same dates,
    and mentoring codes (`_SPAN_COUNT_SKIPPED_PREFIXES`). WARN, one finding
    per entry: 111 of 118 hits real on 245 stored runs (doctor/PRECISION.md,
    RCB-SC)."""
    document = _rendered_document(stage4, blocks)
    declared_by_code = _declared_fields()
    skipped = _MULTI_RECORD_SKIPPED_CODES - frozenset(TEACHING_CODES)
    findings = []
    for entry in _fields_entries(stage4):
        if (not entry.code or entry.code in skipped or not entry.fields
                or entry.code.startswith(_SPAN_COUNT_SKIPPED_PREFIXES)):
            continue
        found = _envelope_of_separate_spans(entry.text)
        if found is None:
            continue
        envelope, count = found
        keys = declared_by_code.get(entry.code, frozenset()) | _RENDERED_FIELDS.get(
            entry.code, frozenset())
        if any(_renders_only_the_envelope(line, envelope)
               for line in _record_lines(entry, keys, document)):
            findings.append(_finding(
                "span_count", "WARN",
                f"entry {entry.element_idx} ({entry.code}): the source lists {count} "
                f"separate dates, the row shows one range {_span_text(envelope)} (#1245)",
                [f"entry {entry.element_idx}: {count} source spans, 1 rendered"]))
    return findings


# --- role_consistency --------------------------------------------------------
#
# Stage 6 renders a grant's "Your role:" cell from `pi_role` (or `role`). When
# the grant's own text names the CV owner under a PI label and `pi_role` says
# co-investigator, or the reverse, the table credits the owner with the wrong
# role (#1403; batch RCBKFG, KUUKNJ 243 and 247: "PIs: <owner>, <other>,
# co-Is: ..." rendered co-I, "PI: <other>, co-Is: ..., <owner>" rendered PI).
# Every value renders, so no loss lint sees it. Report-only.

#: A role label in a grant's text. A co-PI is neither the PI nor a co-I, so
#: it names no role this lint judges; "Co PI" with a space is one too (farm
#: web26 705). Nor does a subcontract PI, who leads a subaward rather than
#: the grant: "(PI: <other>, Subcontract PI: <owner>)" over "Role:
#: Co-Investigator" is consistent (farm web241 150).
_ROLE_LABEL = (r"(?<![\w-])(subcontract pis?|co[- ]?pis?|co-?is?|co-?investigators?|mpis?|pis?"
               r"|principal investigators?)(?![\w-])")
#: A label that opens a list of names: "PIs: A, B" or "co-Is: C, D".
_ROLE_LIST_LABEL_RE = re.compile(_ROLE_LABEL + r"\s*:")
#: A label right after a name: "<name>, PI" or "<name> (co-I)", allowing a
#: few initials or a credential between them. Not a label that opens the
#: next list: in "PIs: Other B, <owner> C, co-Is: ..." the "co-Is:" names
#: the people after it.
_ROLE_AFTER_NAME_RE = re.compile(
    r"[\w .]{0,12}?(?:,\s*|\s*\(\s*)" + _ROLE_LABEL + r"(?!\s*:)")
#: A grant's text splits into cells and lines here; a label never reaches
#: across one.
_GRANT_CELL_SPLIT_RE = re.compile(r"\t|\n| \| ")
ROLE_PI = "PI"
ROLE_CO_I = "co-I"
#: `pi_role` values read as each role, after `norm` and dropping '-' and '.'.
_PI_ROLE_VALUE_RE = re.compile(r"pi|mpi|multi ?pi|contact pi|principal investigator")
_CO_I_ROLE_VALUE_RE = re.compile(r"(?:co ?i|co ?investigator|coinvestigator)s?")


def _label_role(label: str) -> str | None:
    """The role a matched label names, or None for a co-PI or a subcontract PI."""
    bare = label.replace("-", "").replace(" ", "")
    if bare.startswith(("copi", "subcontract")):
        return None
    return ROLE_CO_I if bare.startswith("co") else ROLE_PI


def _stated_role(value: object) -> str | None:
    """The role a `pi_role` value states: PI, co-I, or None for any other
    (empty, a co-PI, "Mentor", "Site PI (subcontract)")."""
    bare = norm(str(value or "")).replace("-", "").replace(".", "")
    if _PI_ROLE_VALUE_RE.fullmatch(bare):
        return ROLE_PI
    if _CO_I_ROLE_VALUE_RE.fullmatch(bare):
        return ROLE_CO_I
    return None


def _owner_roles_in_cell(cell: str, owner: frozenset[str]) -> set[str]:
    """The roles one normalised cell gives an owner surname word: the label
    right after the name, else the nearest list label before it."""
    roles: set[str] = set()
    for word in owner:
        for match in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", cell):
            after = _ROLE_AFTER_NAME_RE.match(cell, match.end())
            before = list(_ROLE_LIST_LABEL_RE.finditer(cell, 0, match.start()))
            label = after or (before[-1] if before else None)
            role = _label_role(label.group(1)) if label else None
            if role:
                roles.add(role)
    return roles


def _source_owner_role(text: str, owner: frozenset[str]) -> str | None:
    """The one role the grant's text gives the owner, or None when it gives
    none or both."""
    roles: set[str] = set()
    for cell in _GRANT_CELL_SPLIT_RE.split(text):
        roles |= _owner_roles_in_cell(norm(cell), owner)
    return roles.pop() if len(roles) == 1 else None


# --- role_consistency: the #1410 shapes (EOAHMI recheck) --------------------
#
# Four more ways a grant table misstates who led the grant, each a #1410
# regression the EOAHMI recheck verified and no lint flagged (#1403):
#
# - pi_cell_empty: "Your role:" says PI and "Name of Principal Investigator:"
#   is empty (JIJRSN 516 and 10 sibling trials). Read off the render, since
#   stage 6 fills that cell from the owner on a PI role (#1446) and stage 4's
#   empty `pi_name` is not what the reader sees.
# - pi_also_co_i: `pi_name` is one of the people `co_investigators` lists, so
#   the table names one person as both (JIJRSN 156, which the next shape
#   reports first; farm KDAZOM 380, MQSUIC 153).
# - pi_from_collaborator: `pi_name` is a person the text names only as a
#   "with Dr. X" collaborator, where no role label names anyone and no role is
#   stated for the owner (JIJRSN 148-156).
# - owner_lead_as_co_i: an unlabelled author-list grant whose first or only
#   name is the owner, rendered with an empty PI cell and the owner among the
#   co-investigators (QTATUP 529, 533, 537).
#
# Five more from the X6 batch autopsy (class E32, wrong-field values on
# grants), each verified there and missed by the dev-246 doctor:
#
# - owner_also_co_i: the owner is listed under Co-Investigators while the
#   role row states a lead role (a PI of any kind, a director or a mentor)
#   and no co-I role (IEUPKK 257-342: Co-PI or Mentor), or states none and
#   the owner's own co-investigator item carries a lead role ("<owner> (Site
#   PI)", RVTAQT 223).
# - role_in_title: `title` holds the owner's roles as list items ("<project>,
#   Director <unit>, Mentor <subproject>") and no role is stated (RINASX 405).
# - owner_pi_role_empty: the rendered PI cell names the owner and "Your
#   role:" is empty (RVTAQT 260, 273).
# - owner_pi_other_role: the rendered PI cell names only the owner beside a
#   role that makes the owner someone other than the grant's PI: a co-,
#   site- or sub-PI, or a co-I (KJJVVO 168-178, the stage-6 PI auto-fill
#   #1457 stopped). A mentor is spared: that role does not say who the PI is.
# - pi_cell_from_title: the rendered PI cell is a run of the project title's
#   own words (RINASX 396: "<title words>, PI" read as a PI label, the
#   parse #1418 stopped).
#
# The last two are regression guards on current dev: the stage-6 causes are
# fixed, and they fire on documents rendered before the fix.

ROLE_SHAPE_CONTRADICTED = "contradicted"
ROLE_SHAPE_PI_CELL_EMPTY = "pi_cell_empty"
ROLE_SHAPE_PI_ALSO_CO_I = "pi_also_co_i"
ROLE_SHAPE_PI_FROM_COLLABORATOR = "pi_from_collaborator"
ROLE_SHAPE_OWNER_LEAD_AS_CO_I = "owner_lead_as_co_i"
ROLE_SHAPE_OWNER_ALSO_CO_I = "owner_also_co_i"
ROLE_SHAPE_ROLE_IN_TITLE = "role_in_title"
ROLE_SHAPE_OWNER_PI_ROLE_EMPTY = "owner_pi_role_empty"
ROLE_SHAPE_OWNER_PI_OTHER_ROLE = "owner_pi_other_role"
ROLE_SHAPE_PI_CELL_FROM_TITLE = "pi_cell_from_title"
#: Each shape's severity, from its measured precision (RC-ROLE2 and X6-role
#: in doctor/PRECISION.md): WARN at 80% or more on 10 or more hand-checked
#: hits, INFO below either bar.
ROLE_SHAPE_SEVERITY = MappingProxyType({
    ROLE_SHAPE_CONTRADICTED: "WARN",
    ROLE_SHAPE_PI_CELL_EMPTY: "WARN",
    ROLE_SHAPE_PI_ALSO_CO_I: "INFO",
    ROLE_SHAPE_PI_FROM_COLLABORATOR: "INFO",
    ROLE_SHAPE_OWNER_LEAD_AS_CO_I: "INFO",
    ROLE_SHAPE_OWNER_ALSO_CO_I: "WARN",
    ROLE_SHAPE_ROLE_IN_TITLE: "INFO",
    ROLE_SHAPE_OWNER_PI_ROLE_EMPTY: "WARN",
    ROLE_SHAPE_OWNER_PI_OTHER_ROLE: "WARN",
    ROLE_SHAPE_PI_CELL_FROM_TITLE: "INFO",
})

#: Any word that labels a role on a grant, in normalised text: "PI", "P.I.",
#: "PD/PI", "MPI", "co-I", "Investigator", "Role", "Director". Broader than
#: `_ROLE_LABEL` on purpose: the two shapes that read an unlabelled grant
#: stay quiet on any text that labels anyone.
_ANY_ROLE_WORD_RE = re.compile(
    r"(?<![a-z])(?:p\.?\s?i|pis|mpi|pd|co-?is?|investigators?|role|director)(?![a-z])")
#: "with Dr. X", "with Drs. X and Y": X is a collaborator, not a PI.
_WITH_COLLABORATOR_RE = re.compile(
    r"(?<![a-z])with\s+(?:(?:drs?|mr|mrs|ms|prof|professor)\.?\s+)?(?P<names>[^;:$()]{1,80})")
#: The fewest words a PI cell read as a run of the title needs: one word of
#: a title is as often a person's surname ("The Lee Cohort").
_PI_FROM_TITLE_MIN_WORDS = 2
#: Splits a `co_investigators` value or a rendered PI cell into one name per
#: person. A slash joins co-PIs ("<owner>/<other>"): unsplit, the pair reads
#: as one person who is the owner (X6-role verification, BYFQBG 322). It also
#: cuts a slash inside a co_investigators item ("<owner> (Co-I/PI)"), leaving
#: the owner's item the role before the slash: a co-I named first then spares
#: `_owner_also_co_i`, as a stated "Co-I/PI" role does.
_PERSON_SPLIT_RE = re.compile(r"\s*(?:;|,|&|/|\band\b)\s*")
#: Words in a person's name that do not identify them.
_NAME_NOISE_WORDS = frozenset({"drs", "prof", "professor", "phd", "mph", "msc", "pharmd",
                               "dds", "dmd", "facp", "student"})


def _person_key(name: str) -> frozenset[str]:
    """The words that identify one named person, for comparing two names."""
    return frozenset(word for word in _NAME_WORD_RE.findall(norm(name))
                     if len(word) >= OWNER_SURNAME_MIN_CHARS and word not in _NAME_NOISE_WORDS)


def _co_investigator_keys(fields: Mapping[str, object]) -> list[frozenset[str]]:
    """One `_person_key` per person `co_investigators` lists, in its order."""
    keys = (_person_key(name)
            for name in _PERSON_SPLIT_RE.split(str(fields.get("co_investigators") or "")))
    return [key for key in keys if key]


def _first_index(text: str, key: frozenset[str]) -> int | None:
    """Where the earliest word of `key` occurs in normalised `text`."""
    hits = [match.start() for word in key
            for match in [re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text)] if match]
    return min(hits) if hits else None


def _pi_also_co_i(fields: Mapping[str, object], pi_key: frozenset[str],
                  owner: frozenset[str]) -> bool:
    """`pi_name` is one of the people `co_investigators` lists, and the two
    values differ, so stage 6 renders both rows (it drops a Co-Investigators
    value identical to the PI). Not an author list headed by the PI that also
    names the owner ("<pi>, G., <owner>"): that is the source's own line
    copied whole, and the PI cell still names the right person (farm web204,
    30 grants)."""
    co_text = norm(str(fields.get("co_investigators") or ""))
    keys = _co_investigator_keys(fields)
    if not pi_key or co_text == norm(str(fields.get("pi_name") or "")) or pi_key not in keys:
        return False
    return not (keys[0] == pi_key and any(key & owner for key in keys[1:]))


def _pi_from_collaborator(text: str, fields: Mapping[str, object],
                          pi_key: frozenset[str]) -> bool:
    """`pi_name` is named only after "with", the text labels no role, and no
    role is stated for the owner."""
    if not pi_key or fields.get("pi_role") or fields.get("role") or _ANY_ROLE_WORD_RE.search(text):
        return False
    return any(pi_key <= _person_key(match.group("names"))
               for match in _WITH_COLLABORATOR_RE.finditer(text))


def _owner_lead_as_co_i(text: str, fields: Mapping[str, object],
                        owner: frozenset[str]) -> bool:
    """No `pi_name`, no stated role, no role label, and the owner is the first
    person `co_investigators` lists and the first of them the text names. A
    stated role (a program title that is not a lead role, NDXXAD 411) is what
    renders, so the owner is not shown as only a co-investigator."""
    keys = _co_investigator_keys(fields)
    if fields.get("pi_name") or fields.get("pi_role") or fields.get("role"):
        return False
    if not keys or _ANY_ROLE_WORD_RE.search(text):
        return False
    owner_at = _first_index(text, keys[0] & owner)
    others = [_first_index(text, key) for key in keys[1:] if not key & owner]
    return owner_at is not None and all(at is None or owner_at < at for at in others)


#: A lead role on the owner's own co-investigator item: "(Site PI)", "PI",
#: "Co-PI", "Principal Investigator", "Director", "Mentor". Not a co-I or a
#: bare "Investigator", which is what the Co-Investigators row says anyway.
_LEAD_ROLE_WORD_RE = re.compile(
    r"(?<![a-z])(?:p\.?\s?i|pis|mpi|principal investigators?|director|mentor)(?![a-z])")
#: A co-investigator role anywhere in a stated role ("Mentor/Co-I", an
#: investigator who also directs a unit, IZJADE 433): the owner then
#: belongs in the Co-Investigators row.
_CO_I_ROLE_WORD_RE = re.compile(
    r"(?<![a-z])(?:co-?(?:investigators?|is?)|(?<!principal )investigators?)(?![a-z])")
#: A list item of a title that is a role, not part of the project's name:
#: ", Director Unit C", "; Mentor Track 2", ", Co-Investigator". Not
#: "Investigator-Initiated", which names a kind of trial, nor a bare "PI",
#: which a title uses to name a subproject's PI (", PI <other>)", XELRLZ 138).
_TITLE_ROLE_ITEM_RE = re.compile(
    r"[,;]\s*(?:co-?)?(?:director|mentor|principal investigator|investigator"
    r"|project leader|core leader)(?![\w-])")


#: A role that makes the owner someone other than the grant's PI: a co-,
#: site- or sub-PI, or a co-investigator.
_NOT_THE_PI_ROLE_RE = re.compile(
    r"(?<![a-z])(?:(?:co|site|sub|subcontract)[\s-]*(?:p\.?\s?i|principal investigator)"
    r"|principal investigator of (?:an? |the )?sub-?contract"
    r"|co-?investigators?|co-?is?)(?![a-z])")
#: A PI-equivalent role left once the qualified forms are cut out: "PI",
#: "MPI", "PD/PI", "Program Director", "Principle Investigator". With one,
#: the owner in the PI cell is right ("co-investigator; PI of project 2").
_PI_EQUIVALENT_ROLE_RE = re.compile(
    r"(?<![a-z])(?:p\.?\s?i|mpi|pd|principa?le? investigators?|program director)(?![a-z])")


def _role_not_the_pi(role: str) -> bool:
    """`role` names the owner as a co-, site- or sub-PI or a co-I, and as no
    kind of PI besides."""
    text = norm(role)
    if not _NOT_THE_PI_ROLE_RE.search(text):
        return False
    return not _PI_EQUIVALENT_ROLE_RE.search(_NOT_THE_PI_ROLE_RE.sub(" ", text))


def _stated_role_text(fields: Mapping[str, object]) -> str:
    """The role stage 6 renders in "Your role:" (`pi_role or role`)."""
    return str(fields.get("pi_role") or fields.get("role") or "").strip()


def _owner_also_co_i(fields: Mapping[str, object], pi_key: frozenset[str],
                     owner: frozenset[str]) -> bool:
    """The owner is one of the people `co_investigators` lists, while the
    stated role is a lead role (a PI of any kind, a director, a mentor), or
    no role is stated and the owner's own item names one. Another stated role
    (a program title that is not a lead role, NDXXAD 411) may share the row
    with its co-holders. Not a list headed by the PI (the source's author line
    copied whole, farm web204), which `_pi_also_co_i` spares too."""
    items = [item for item in _PERSON_SPLIT_RE.split(str(fields.get("co_investigators") or ""))
             if _person_key(item) & owner]
    keys = _co_investigator_keys(fields)
    if not items or (pi_key and keys and keys[0] == pi_key):
        return False
    stated = norm(_stated_role_text(fields))
    if stated:
        return (_LEAD_ROLE_WORD_RE.search(stated) is not None
                and not _CO_I_ROLE_WORD_RE.search(stated) and ":" not in stated)
    return any(_LEAD_ROLE_WORD_RE.search(norm(item)) for item in items)


def _role_in_title(fields: Mapping[str, object]) -> bool:
    """No role is stated and the title carries a role as a list item."""
    title = norm(str(fields.get("title") or ""))
    return not _stated_role_text(fields) and _TITLE_ROLE_ITEM_RE.search(title) is not None


def _entry_role_shape(entry: Mapping[str, object], fields: Mapping[str, object],
                      owner: frozenset[str]) -> tuple[str, str] | None:
    """The first role shape one grant entry shows, as (shape, what it says),
    or None."""
    text = norm(str(entry.get("text") or ""))
    stated = _stated_role(fields.get("pi_role") or fields.get("role"))
    source = _source_owner_role(str(entry.get("text") or ""), owner)
    if stated and source and stated != source:
        return (ROLE_SHAPE_CONTRADICTED,
                f"the source names the CV owner as {source}, but the rendered role is {stated}")
    pi_key = _person_key(str(fields.get("pi_name") or ""))
    if pi_key & owner:
        return None
    if _pi_from_collaborator(text, fields, pi_key):
        return (ROLE_SHAPE_PI_FROM_COLLABORATOR,
                "the PI is a person the source names only as a 'with' collaborator")
    if _pi_also_co_i(fields, pi_key, owner):
        return (ROLE_SHAPE_PI_ALSO_CO_I, "the PI is also listed as a co-investigator")
    if owner and _owner_lead_as_co_i(text, fields, owner):
        return (ROLE_SHAPE_OWNER_LEAD_AS_CO_I,
                "the CV owner is the first name on an unlabelled grant but renders only "
                "as a co-investigator, with no PI")
    if _owner_also_co_i(fields, pi_key, owner):
        return (ROLE_SHAPE_OWNER_ALSO_CO_I,
                "the CV owner is listed as a co-investigator, but the owner's role is "
                f"{_stated_role_text(fields) or 'a lead role the co-investigator row carries'}")
    if _role_in_title(fields):
        return (ROLE_SHAPE_ROLE_IN_TITLE,
                "the project title carries the owner's role, and 'Your role:' is empty")
    return None


def _grant_tables(table_rows: list[list[list[str]]]) -> list[tuple[int, dict[str, str]]]:
    """Each rendered grant table (a table with a PI row) as its index among
    the document's tables and {row label: value}."""
    tables = []
    for at, table in enumerate(table_rows):
        cells = {row[0]: (row[1] if len(row) > 1 else "") for row in table if row}
        if PI_NAME_LABEL in cells:
            tables.append((at, cells))
    return tables


def _grant_entries_by_title(stage4: dict) -> dict[str, list[Mapping[str, object]]]:
    """Every grant entry per normalised title, in stage-4 order."""
    by_title: dict[str, list[Mapping[str, object]]] = {}
    for entry in stage4.get("entries", []):
        fields = entry.get("extracted_fields")
        if entry.get("taxonomy_code") in GRANT_CODES and isinstance(fields, Mapping):
            title = norm(str(fields.get("title") or fields.get("study_title") or ""))
            if title:
                by_title.setdefault(title, []).append(entry)
    return by_title


def _table_entries(stage4: dict,
                   tables: list[dict[str, str]]) -> list[Mapping[str, object]]:
    """The grant entry each rendered table renders, matched by title. Several
    entries can share a title (an organisation's name as the title of each
    of its grants, YUYVIG SQMWHM: seven), and stage 6 renders them in date
    order, so each table takes the first entry with its title it has not
    already matched, preferring one whose stated role is the table's "Your
    role:" cell. A table left with no unmatched entry (more tables than
    entries) falls back to the first entry with its title, and to {} when
    no entry has it."""
    by_title = _grant_entries_by_title(stage4)
    matched: set[int] = set()
    resolved = []
    for cells in tables:
        candidates = by_title.get(norm(cells.get(PROJECT_TITLE_LABEL, "")), [])
        free = [entry for entry in candidates if id(entry) not in matched]
        role = norm(cells.get(YOUR_ROLE_LABEL, ""))
        same_role = [entry for entry in free
                     if norm(_stated_role_text(entry["extracted_fields"])) == role]
        entry = (same_role or free or candidates or [{}])[0]
        matched.add(id(entry))
        resolved.append(entry)
    return resolved


def _table_role_shape(cells: Mapping[str, str], owner: frozenset[str],
                      extracted_pi: str) -> tuple[str, str] | None:
    """The first role shape one rendered grant table shows, as (shape, what
    it says), or None: pi_cell_empty, then the three render shapes the X6
    batch added. The owner is read off the PI cell by surname word, the way
    `_entry_role_shape` reads `pi_name`. `extracted_pi` is the table's
    entry's `pi_name`: a PI cell stage 4 extracted as a person is not a
    title run, even where the title names that person (a fellowship titled
    after its holder, NDMRSO CAGLNY 118)."""
    pi_cell, role = cells[PI_NAME_LABEL], cells.get(YOUR_ROLE_LABEL, "")
    if not pi_cell:
        if _stated_role(role) == ROLE_PI:
            return (ROLE_SHAPE_PI_CELL_EMPTY,
                    f"'Your role:' is {role}, but 'Name of Principal Investigator:' is empty")
        return None
    pi_keys = [key for key in map(_person_key, _PERSON_SPLIT_RE.split(pi_cell)) if key]
    names_owner = [bool(key & owner) for key in pi_keys]
    if any(names_owner) and not role:
        return (ROLE_SHAPE_OWNER_PI_ROLE_EMPTY,
                "'Name of Principal Investigator:' names the CV owner, and 'Your role:' is empty")
    if names_owner and all(names_owner) and _role_not_the_pi(role):
        return (ROLE_SHAPE_OWNER_PI_OTHER_ROLE,
                f"'Name of Principal Investigator:' names only the CV owner, but 'Your role:' "
                f"is {role}")
    title = norm(cells.get(PROJECT_TITLE_LABEL, ""))
    pi_text = norm(pi_cell)
    if (not any(names_owner) and pi_text != norm(extracted_pi)
            and len(pi_text.split()) >= _PI_FROM_TITLE_MIN_WORDS
            and re.search(rf"(?<!\w){re.escape(pi_text)}(?!\w)", title)):
        return (ROLE_SHAPE_PI_CELL_FROM_TITLE,
                "'Name of Principal Investigator:' is a run of the project title's words")
    return None


class _TableRoleHit(NamedTuple):
    """One rendered grant table the lint reports: its index among the
    document's tables, its entry's index, its shape and what it says, and
    its title."""
    table: int
    idx: object
    shape: tuple[str, str]
    title: str


def _table_role_hits(stage4: dict, table_rows: list[list[list[str]]],
                     owner: frozenset[str], reported: set[object]) -> list[_TableRoleHit]:
    """Each rendered grant table that shows a role shape (each table matched
    to its entry by `_table_entries`). A table whose entry already has an
    entry finding counts only for pi_cell_empty, which predates the others
    and was always reported beside them."""
    tables = _grant_tables(table_rows)
    hits = []
    for (at, cells), entry in zip(tables, _table_entries(stage4, [cells for _, cells in tables])):
        fields = entry.get("extracted_fields", {})
        shape = _table_role_shape(cells, owner, str(fields.get("pi_name") or ""))
        idx = entry.get("element_idx_start")
        if not shape or (idx in reported and shape[0] != ROLE_SHAPE_PI_CELL_EMPTY):
            continue
        hits.append(_TableRoleHit(at, idx, shape, cells.get(PROJECT_TITLE_LABEL, "")))
    return hits


def _table_role_groups(hits: list[_TableRoleHit]) -> dict[tuple[object, tuple[str, str], str],
                                                          list[_TableRoleHit]]:
    """The hits one finding reports: those whose tables still resolve to one
    entry and show one shape (#1590), keyed by entry index, shape and title."""
    groups: dict[tuple[object, tuple[str, str], str], list[_TableRoleHit]] = {}
    for hit in hits:
        groups.setdefault((hit.idx, hit.shape, hit.title), []).append(hit)
    return groups


def _table_role_message(idx: object, shape: tuple[str, str], tables_hit: int) -> str:
    """What the finding for one group of `_table_role_groups` says."""
    where = f"entry {idx}" if idx is not None else "a grant table"
    count = f" in {tables_hit} grant tables" if tables_hit > 1 else ""
    return f"{where}: {shape[1]}{count} ({shape[0]}, #1403)"


def _table_role_findings(stage4: dict, table_rows: list[list[list[str]]],
                         owner: frozenset[str], reported: set[object]) -> list[dict]:
    """A finding per grant entry whose rendered table shows a role shape.
    Tables that still resolve to one entry and show one shape give one
    finding that counts them (#1590)."""
    groups = _table_role_groups(_table_role_hits(stage4, table_rows, owner, reported))
    return [_finding("role_consistency", ROLE_SHAPE_SEVERITY[shape[0]],
                     _table_role_message(idx, shape, len(group)),
                     [title[:FIELD_EVIDENCE_VALUE_CHARS]])
            for (idx, shape, title), group in groups.items()]


def lint_role_consistency(stage4: dict,
                          table_rows: list[list[list[str]]] | None = None) -> list[dict]:
    """A grant table that misstates who led the grant (#1403). One finding
    per grant entry, for the first shape it shows (see the shapes above),
    plus, only when the docx was read, one per grant entry and render shape
    its rendered tables show, counting the tables (`_table_role_findings`,
    #1590). The owner is
    `cv_owner.last_name`; with none, no shape that names the owner fires.
    Not judged from stage 4: an empty `pi_role` against the source's label
    (the render shape owner_pi_role_empty reads that off the table), a
    co-PI, or text that gives the owner both roles."""
    owner = _owner_surname_words(stage4)
    shaped = _entry_role_shapes(stage4, owner)
    findings = [_finding(
        "role_consistency", ROLE_SHAPE_SEVERITY[shape[0]],
        f"entry {entry.get('element_idx_start')} ({entry.get('taxonomy_code')}): "
        f"{shape[1]} ({shape[0]}, #1403)",
        [str(entry.get("text", ""))[:FIELD_EVIDENCE_VALUE_CHARS]]) for entry, shape in shaped]
    if table_rows is not None:
        reported = {entry.get("element_idx_start") for entry, _ in shaped}
        findings.extend(_table_role_findings(stage4, table_rows, owner, reported))
    return findings


def _entry_role_shapes(stage4: dict, owner: frozenset[str]) -> list[tuple[dict, tuple[str, str]]]:
    """Each grant entry whose stage-4 fields show a role shape, with the shape."""
    shaped = []
    for entry in stage4.get("entries", []):
        fields = entry.get("extracted_fields")
        if entry.get("taxonomy_code") not in GRANT_CODES or not isinstance(fields, Mapping):
            continue
        shape = _entry_role_shape(entry, fields, owner)
        if shape:
            shaped.append((entry, shape))
    return shaped


def owner_pi_role_empty_tables(stage4: dict, table_rows: list[list[list[str]]]) -> dict[int, str]:
    """The grant tables lint_role_consistency reports as owner_pi_role_empty
    (the PI cell names the CV owner and "Your role:" is empty), by their
    index among the document's top-level tables, each with the message of
    the finding that reports it. The review copy suggests "PI" there as a
    tracked insertion (#1591): the shape's certain fix. A finding can
    report several tables, so its comment goes only once all are fixed."""
    owner = _owner_surname_words(stage4)
    reported = {entry.get("element_idx_start") for entry, _ in _entry_role_shapes(stage4, owner)}
    groups = _table_role_groups(_table_role_hits(stage4, table_rows, owner, reported))
    return dict(sorted((hit.table, _table_role_message(idx, shape, len(group)))
                       for (idx, shape, _), group in groups.items()
                       if shape[0] == ROLE_SHAPE_OWNER_PI_ROLE_EMPTY for hit in group))


# --- orphaned_fragments ------------------------------------------------------
#
# Stage 3b's fragment pass tags a short line `is_fragment` with the neighbour
# it belongs to, and stage 4 skips every fragment, so a fragment whose text is
# not in that neighbour's text reaches no record (#1256: JNATFN's title tail,
# BFSUMA's assignee, MQJAVH's examiner sessions). `stage3b.fragment_merge`
# folds the text in at the end of stage 3b and leaves a header, column label
# or organisation sub-heading out on purpose; those report INFO. Any other
# fragment left outside its parent is content lost, WARN. Reads stage 3b only:
# stage 4 extracts from the 3b text, so a line missing there is missing from
# every field.

#: Merge-skip reasons that leave a line out on purpose: a structural label, or
#: an organisation sub-heading left to #985's context stamp.
INTENDED_FRAGMENT_SKIPS = frozenset({SKIP_LABEL, SKIP_ORGANIZATION})


def lint_orphaned_fragments(stage3b: dict) -> list[dict]:
    """One finding per `is_fragment` entry whose text is not in its parent's
    text: INFO when stage 3b left it out on purpose, else WARN. Silent on a
    list holding a non-entry: `fragment_of` indexes the list as written."""
    entries = stage3b.get("entries") or []
    if not all(isinstance(e, dict) for e in entries):
        return []
    findings = []
    for idx, entry in enumerate(entries):
        if not entry.get("is_fragment") or fragment_text_in_parent(entries, idx):
            continue
        reason = merge_skip_reason(entries, idx) or "never merged"
        severity = "INFO" if reason in INTENDED_FRAGMENT_SKIPS else "WARN"
        findings.append(_finding(
            "orphaned_fragments", severity,
            f"entry {entry.get('element_idx_start')}: fragment of list index "
            f"{entry.get('fragment_of')} whose text no record holds ({reason})",
            [str(entry.get("text", ""))[:FIELD_EVIDENCE_VALUE_CHARS]]))
    return findings


# --- appointment_title_overlong ----------------------------------------------
#
# Section D's schema has no description field, so stage 4 can write an
# appointment's duties into `title`, and the appointments table's Title column
# then carries the role plus a paragraph (#1205: YUYVIG DYLJXC 661/668, 310
# and 433 characters, which nothing flagged). Stage 6 moves the duty prose to
# a row under the appointment when it can find where the role ends
# (`split_appointment_title`); this lint reports the title either way, since
# the stage-4 record is wrong whether or not the render recovered from it.

#: An appointment role longer than this is not a role (#1205, owner decision
#: 2026-10-08). Measured on 1,763 D1-D3 titles over six farms (YUYVIG,
#: EBYSBC, NDMRSO, X6, EOAHMI, the 66-CV local farm): 6 are longer, and the
#: three a ';'-list of roles explains (EBYSBC QNZADH 0/35/39: a rank, a
#: deanship and a directorship, each under 100 characters) are judged by
#: their longest role instead, which is under it.
APPOINTMENT_TITLE_WARN_CHARS = 150

#: CVs list several concurrent roles in one title with this separator.
_APPOINTMENT_ROLE_SEPARATOR = ";"


def lint_appointment_title_overlong(stage4: dict) -> list[dict]:
    """A D1-D3 `title` one of whose ';'-separated roles is over
    `APPOINTMENT_TITLE_WARN_CHARS`: duty prose stage 4 packed into the role.
    WARN, one finding per entry. Says whether stage 6 can move the duties
    under the row or renders the title whole in the Title column."""
    findings = []
    for entry in _fields_entries(stage4):
        if entry.code not in POSITION_TAXONOMY_CODES:
            continue
        title = str(entry.fields.get("title") or "").strip()
        longest = max(len(role.strip()) for role in title.split(_APPOINTMENT_ROLE_SEPARATOR))
        if longest <= APPOINTMENT_TITLE_WARN_CHARS:
            continue
        role, duties = split_appointment_title(title)
        outcome = (f"stage 6 shows '{role[:FIELD_EVIDENCE_VALUE_CHARS]}' in the Title "
                   f"column and the rest on a row under it" if duties
                   else "rendered whole in the Title column")
        findings.append(_finding(
            "appointment_title_overlong", "WARN",
            f"entry {entry.element_idx} ({entry.code}): title is {len(title)} "
            f"characters, its longest '{_APPOINTMENT_ROLE_SEPARATOR}' part {longest}, "
            f"over {APPOINTMENT_TITLE_WARN_CHARS} -- duty prose in the role "
            f"field; {outcome}",
            [title[:FIELD_EVIDENCE_VALUE_CHARS]]))
    return findings
