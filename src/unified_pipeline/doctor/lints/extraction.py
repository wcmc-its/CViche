"""Lints for what the extraction stages pulled out, and what became of it (#493).

One responsibility: compare the entries and fields stages 3b/4 produced against
the source they came from and the document they landed in -- grants filed under
the wrong funding heading, entries whose field extraction covered almost none of
their text, taxonomy codes that vanished between classification and render,
dedup drops that were not duplicates, records fabricated from the template's
own scaffolding, values filed under a key no renderer reads, and years given
the wrong century.

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
import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Dict, List, NamedTuple, Optional, Tuple

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
from unified_pipeline.stage4.coercion import (
    DATE_RANGE_TAXONOMY_CODES,
    GRANT_EFFORT_TAXONOMY_PREFIX,
    IDENTIFIER_TAXONOMY_CODES,
    find_single_closed_range,
)
from unified_pipeline.stage4.schemas import FIELD_SCHEMA_CONFIG_PATH, FIELD_SCHEMAS
from unified_pipeline.stage_5c_teaching_formatter import TEACHING_CODES
from unified_pipeline.stage_5d_citation_formatter import PUBLICATION_CODES
from unified_pipeline.core.text_norm import (
    SUBSTANTIVE_LINE_CHARS,
    looks_like_record,
    norm,
    squash,
)
from unified_pipeline.stage6.normalization.institutions import (
    _get_cleaned_institution_name,
)
from unified_pipeline.stage6.fan_out import (
    FANNED_OUT_FROM,
    _RENDERED_FIELDS,
    _TEXT_RENDERED_CODES,
    _is_blank,
    fan_out_multi_record_entries,
)
from unified_pipeline.stage6.parsing.dates import TWO_DIGIT_YEAR_PIVOT
from unified_pipeline.stage6.normalization.pii import (
    CAT_HOME_CONTACT,
    SCOPE_PERSONAL_AND_APPENDIX,
    WITHHOLD_POLICY,
    _pii_matches,
)
from unified_pipeline.stage6.pii_pass import PERSONAL_DATA_CODE
from unified_pipeline.stage_6_word_template import (
    RENDER_ROUTED_CODES,
    grant_status_rebucket_target,
)

from ..shared import (
    Haystack,
    _LINE_SENTINEL,
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    _entry_pieces,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
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


def _entry_status(entry: Dict) -> Optional[str]:
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
    segments: Dict[str, List[Tuple[str, str]]] = {c: [] for c, _ in _FUNDING_SECTIONS}
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


def lint_bucket_status(stage4: Dict, blocks: List[Tuple[str, str]]) -> List[Dict]:
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
UNDER_EXTRACTION_MAX_PCT = 40.0
UNDER_EXTRACTION_MIN_CHARS = 800
UNDER_EXTRACTION_MIN_RECORDS = 2


# looks_like_record only sees pipe/tab rows; fused award/honor lines are
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
    when its extracted field values surfaced (#890)."""
    if _personal_data_withheld(entry):
        return None
    if evidence is not None and _fields_rendered(entry, lines, evidence):
        return True
    return _entry_rendered(entry.get("text"), haystacks.text, haystacks.tokens, shared)


def lint_classified_unrendered(stage3b: Dict,
                               blocks: List[Tuple[str, str]],
                               stage4: dict | None = None,
                               stage5b: dict | None = None) -> List[Dict]:
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
    by_code: Dict[str, List[Dict]] = {}
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
_DEDUP_TOKEN_RE = re.compile(r"[^\W_]+")


def _alphanumeric_tokens(text) -> Counter:
    """Alphanumeric (Unicode) token multiset for one string (lint 11 dedup-containment
    coverage). A Counter, not a set, so a dropped passage that repeats a
    word is not fully covered by a kept passage that says it once (#718)."""
    return Counter(_DEDUP_TOKEN_RE.findall(norm(text)))


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


# --------------------------------------------------------------------------
# Field values stage 4 extracted that the document cannot show: a value filed
# under a key no renderer reads, and a year given the wrong century. Both read
# stage 4 only, never the docx -- the value is already wrong or unreachable
# there, and reading the rendered text would add a match step that can only
# lose precision (four-digit numbers below 1930 sit in citation page ranges).


class _FieldsEntry(NamedTuple):
    """The four things the field lints read off one stage-4 entry, read once
    at the artifact boundary instead of by `.get()` in every helper (§8.1).
    `fields` is empty when `extracted_fields` is absent or not an object."""
    element_idx: object
    code: str
    text: str
    fields: Mapping[str, object]


def _fields_entries(stage4: dict) -> list[_FieldsEntry]:
    entries = []
    for raw in stage4.get("entries", []):
        fields = raw.get("extracted_fields")
        entries.append(_FieldsEntry(
            raw.get("element_idx_start"), str(raw.get("taxonomy_code") or ""),
            str(raw.get("text") or ""),
            fields if isinstance(fields, Mapping) else {}))
    return entries


#: A key that names a date or a year: `date`, `start_date`, `year_certified`,
#: `dates_attended`, and the shapes `fan_out._is_date_key` (suffix-based)
#: does not see, `date_range` and `start_date_1`. Matched as a whole
#: underscore-separated word, so `candidate_name` is not a date key.
_DATE_NAMED_KEY_RE = re.compile(r"(?:^|_)(?:dates?|years?)(?:_|$)")

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

#: Codes the lint does not inspect. A (personal data): the #820 PII pass owns
#: it, and quoting its values as evidence would put protected data in a report
#: mirrored to S3. The text-rendered codes (`fan_out._TEXT_RENDERED_CODES`,
#: which include T): their section writes the entry's text, not its fields,
#: so a value under any key is in the document already. The codes a stage-5
#: formatter rewrites whole from the entry's text -- teaching (5c's
#: `formatted_text`) and publications (5d's `formatted_citation`): the
#: off-schema keys stage 4 leaves on them (`other_id`, `journal_or_source`,
#: a K1 `description`) travel inside that rendering. Known miss: a
#: contribution note under S1 `notes` (PFBSNH).
_OFFSCHEMA_SKIPPED_CODES = (frozenset({PERSONAL_DATA_CODE}) | _TEXT_RENDERED_CODES
                            | frozenset(TEACHING_CODES) | frozenset(PUBLICATION_CODES))

#: Keys stage 4's own post-processing writes onto entries whose schema may
#: not declare them -- bookkeeping, not a value the model misfiled:
#: `coercion.apply_regex_post_processing` sets the identifiers on S and
#: `IDENTIFIER_TAXONOMY_CODES`, and `percent_effort` on M2*. Only what this
#: lint can see is listed: S, N4 and the `orcid` codes A and S0 are skipped
#: above, and `owner_name.add_target_names`' `target_name` lands only on S
#: and on R codes that either declare it (R) or have no schema at all.
_IDENTIFIER_KEYS = frozenset({"pmid", "pmcid", "doi"})
_PERCENT_EFFORT_KEY = "percent_effort"

#: `<declared field>_<n>`: a numbered second copy of a schema field, which is
#: a second record (`organization_2` held the second column of a two-column
#: memberships list).
_NUMBERED_FIELD_RE = re.compile(r"^(?P<field>.+)_\d+$")


class OffschemaValue(NamedTuple):
    """One non-empty value under a key no renderer reads."""
    element_idx: object
    value: object
    record_shaped: bool


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
    disagree with the renderer about which lists render."""
    probe = {"taxonomy_code": entry.code, "text": entry.text,
             "extracted_fields": dict(entry.fields)}
    children = fan_out_multi_record_entries([probe], FIELD_SCHEMAS)
    return frozenset(child[FANNED_OUT_FROM]["key"] for child in children
                     if FANNED_OUT_FROM in child)


def _is_record_shaped(key: str, value: object, declared: frozenset[str]) -> bool:
    """A whole record rather than one fact: a list of objects, an object that
    shares a key with the code's schema, or a numbered schema field."""
    if isinstance(value, list):
        return all(isinstance(item, Mapping) for item in value)
    if isinstance(value, Mapping):
        return bool(set(value) & declared)
    numbered = _NUMBERED_FIELD_RE.match(key)
    return bool(numbered and numbered.group("field") in declared)


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


def _offschema_values(entry: _FieldsEntry, declared_by_code: dict[str, frozenset[str]],
                      ) -> dict[str, OffschemaValue]:
    """`{key: value}` for this entry's non-empty, non-date keys that are in
    neither schema, not rendered for its code, not stage-4 bookkeeping, and
    not a record list stage 6 fans out. On an entry none of whose schema or
    rendered keys holds a value other than a date, a one-fact value is left
    out, because the raw text the entry renders from usually carries it; a
    record-shaped value is always reported, because that text does not
    carry a whole record (see `_holds_a_non_date_value`)."""
    if entry.code in _OFFSCHEMA_SKIPPED_CODES:
        return {}
    declared = declared_by_code.get(entry.code, frozenset())
    schema_keys = declared | _RENDERED_FIELDS.get(entry.code, frozenset())
    readable = schema_keys | _stage4_bookkeeping_keys(entry.code)
    candidates = {key: value for key, value in entry.fields.items()
                  if key not in readable and not _is_blank(value)
                  and not _DATE_NAMED_KEY_RE.search(key)}
    for key in _fanned_out_keys(entry):
        candidates.pop(key, None)
    hits = {key: OffschemaValue(entry.element_idx, value,
                                _is_record_shaped(key, value, declared))
            for key, value in candidates.items()}
    renders_from_text = not _holds_a_non_date_value(entry, schema_keys)
    return {key: hit for key, hit in hits.items()
            if hit.record_shaped or not renders_from_text}


class OffschemaSummary(NamedTuple):
    """One (code, key) group's severity, message and evidence, in `_finding`'s
    positional order."""
    severity: str
    message: str
    evidence: list[str]


def _offschema_summary(code: str, key: str,
                       hits: list[OffschemaValue]) -> OffschemaSummary:
    """WARN when any value is a whole record, INFO when each is one fact."""
    records = sum(hit.record_shaped for hit in hits)
    noun = "entry" if len(hits) == 1 else "entries"
    lost = (f"{records} {'holds' if records == 1 else 'hold'} a whole record"
            if records else "each holds one fact of its record")
    return OffschemaSummary(
        "WARN" if records else "INFO",
        f"{len(hits)} {code} {noun}: `{key}` is outside the {code} schema and "
        f"no renderer reads it -- {lost}, missing from the output (#817)",
        [f"entry {hit.element_idx}: {_evidence_value(hit.value)}"
         for hit in hits[:FIELD_EVIDENCE_MAX_VALUES]])


def lint_offschema_fields(stage4: dict) -> list[dict]:
    """A non-empty stage-4 value under a key that is in neither field schema
    (built-in or config, any `extract` flag), not in `fan_out._RENDERED_FIELDS`
    for its code, not stage-4 bookkeeping, and not a record list fan-out
    splits: nothing reads it, so it never reaches the document. One finding
    per (code, key); date-named keys are left out (two-thirds of the corpus's
    off-schema keys are dates a schema names differently)."""
    declared_by_code = _declared_fields()
    groups: dict[tuple[str, str], list[OffschemaValue]] = {}
    for entry in _fields_entries(stage4):
        for key, hit in _offschema_values(entry, declared_by_code).items():
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


def _earliest_degree_year(entries: list[_FieldsEntry]) -> int | None:
    """The earliest plausible degree year a B1 entry both extracts and writes
    in its own text -- a degree year that is itself a wrong century, or that
    the text never states, cannot set the floor."""
    years = [year for entry in entries if entry.code == ACADEMIC_DEGREE_CODE
             for _, year in _date_field_years(entry.fields)
             if year >= IMPLAUSIBLE_YEAR_FLOOR and _year_in_text(year, entry.text)]
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


def lint_implausible_year(stage4: dict) -> list[dict]:
    """A year in a stage-4 date-named field that is below the owner's floor
    (`YearFloor`) and that the entry's own text never writes in four digits:
    a two-digit year given the wrong century, rendered as extracted. WARN,
    one finding per entry. Report-only: the value is not repaired. Code A is
    skipped, since its dates are personal data, not records."""
    entries = _fields_entries(stage4)
    floor = _year_floor(entries)
    findings = []
    for entry in entries:
        if entry.code == PERSONAL_DATA_CODE:
            continue
        bad = [f"{key}={year}" for key, year in _date_field_years(entry.fields)
               if year < floor.year and not _year_in_text(year, entry.text)]
        if bad:
            findings.append(_finding(
                "implausible_year", "WARN",
                f"entry {entry.element_idx} ({entry.code}): {', '.join(bad)} -- "
                f"before {floor.year} ({floor.reason}) and not written in the "
                f"entry's text; most likely a two-digit year given the wrong "
                f"century",
                [entry.text[:FIELD_EVIDENCE_VALUE_CHARS]]))
    return findings
