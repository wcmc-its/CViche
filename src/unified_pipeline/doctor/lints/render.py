"""Lints for defects visible in the rendered WCM document (#493).

One responsibility: read the stage 6 output and report what went wrong on the
page -- content that never made it, template scaffolding that did, table rows
that lost their shape, passages emitted twice. Every lint here reads the
rendered blocks; none of them inspects an upstream stage in isolation.

These seven were the largest group in run_doctor.py, and the twenty-eight
constants, regexes and helpers below them are used by nothing else in the
module, so they move together and stop being module-global.

Bodies are unmodified. `run_doctor` re-exports every name it exported before.
"""
import functools
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Dict, List, NamedTuple, Tuple

from unified_pipeline.core.docx_structure_extractor import _is_date_only_text
from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.core.template_boilerplate import (
    is_foreign_template_instruction,
    is_source_boilerplate,
    is_template_instruction,
)
from unified_pipeline.core.text_norm import (
    SUBSTANTIVE_LINE_CHARS,
    looks_like_record,
    norm,
)
from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY
from unified_pipeline.stage6.fan_out import _is_date_key as _fan_out_date_key
from unified_pipeline.stage6.sections.honors import _ORG_ROLE_WORDS

from ..shared import (
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    TABLE_ROW_JOINER,
    _entry_pieces,
    _fields_entries,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
    _template_haystack,
    _NAME_WORD_RE,
    _owner_surname_words,
)


# Lint 7: a source section with at least this many substantive lines whose
# output section is empty did not just "have nothing to say".
DEAD_SECTION_MIN_LINES = 3


# Lint 8: an entry is a fused multi-record candidate at this many record-like
# lines. looks_like_record only sees pipe/tab rows; employment/appointment
# records are date-range-prefixed comma lines ("Jun 2020-Jun 2025, Assistant
# Professor"), caught by `_is_date_record_line` when the line carries a
# payload beyond the bare date range. A bare date line (`_is_bare_date_line`)
# counts toward this floor too -- it is a split-off date column, so the entry
# fuses several records -- but is not itself a record to verify (#446 review
# T1.6 / #746: on the farm those lines used to be counted AND reported as
# records, so a Bostwick committee entry read '1 of 3 records absent' when
# two of the three were '2006-2010, 2012, 2013' and '2004 –2020 2004-2010').
UNRENDERED_MIN_RECORD_LINES = 2


RECORD_DATE_LINE_MIN_CHARS = 20


_RECORD_DATE_PREFIX_RE = re.compile(r"^(?:[A-Za-z]{3,9}\.? )?\d{4}\s*[-–—]")


# What may follow the leading date's dash on a RECORD line (#446 review
# T1.6 / #746): the end of the range -- a 1-4 digit year, because stage 2
# glues the payload onto a two-digit end year ('2008-15Associate
# Professor', '1996-8<TAB>Research Fellowship') -- or an open-ended word,
# a field separator, or a capitalised payload word (a role, a title, a
# name). Running prose that merely opens with a date continues in
# lowercase ('2020 - the year our program expanded ...'), which is what a
# length-only rule admits. Measured over the 66-CV farm's stage-4 text
# (#725 review r3923589271 pt 6, re-measured 2026-09-05): the length-only
# rule admits 226 date-prefixed lines; this one keeps 203 and drops
# exactly the 23 that carry no worded payload at all -- every one a bare
# date list ('February 2018 – Present', '2004 –2020 2004-2010').
# ponytail: a prose sentence whose first word after the dash is
# capitalised still passes -- no corpus instance yet, and the opposite
# failure (a real record no longer counted, so never re-verified) is the
# worse one; revisit with a corpus instance.
_RECORD_DATE_CONTINUATION_RE = re.compile(
    r"^(?:[A-Za-z]{3,9}\.? )?\d{4}\s*[-–—]\s*"
    r"(?:\d{1,4}|(?i:present|current|ongoing|now|to date|date)(?![a-z])"
    r"|[\t,:;|]|[A-Z])")


# The leading date or date range of a record line, stripped before asking
# whether a worded payload follows it.
_RECORD_DATE_RANGE_RE = re.compile(
    r"^(?:[A-Za-z]{3,9}\.? )?\d{4}\s*[-–—]\s*"
    r"(?:(?:[A-Za-z]{3,9}\.? )?\d{1,4}"
    r"|(?i:present|current|ongoing|now|to date|date)(?![a-z]))?")


_RECORD_PAYLOAD_RE = re.compile(r"[A-Za-z]{2,}")


def _date_payload(line: str) -> str | None:
    """What follows the leading date range of a date-prefixed line of at
    least RECORD_DATE_LINE_MIN_CHARS, or None when the line is not
    date-prefixed at all."""
    if len(line) < RECORD_DATE_LINE_MIN_CHARS:
        return None
    if not _RECORD_DATE_PREFIX_RE.match(line):
        return None
    date_range = _RECORD_DATE_RANGE_RE.match(line)
    return line[date_range.end():] if date_range else line


def _is_date_record_line(line: str) -> bool:
    """A date-prefixed line that is a record rather than prose or a bare
    date range: continuing past the dash the way a record does
    (`_RECORD_DATE_CONTINUATION_RE`) and carrying a worded payload beyond
    the date range itself."""
    payload = _date_payload(line)
    if payload is None or not _RECORD_DATE_CONTINUATION_RE.match(line):
        return False
    return bool(_RECORD_PAYLOAD_RE.search(payload))


def _is_bare_date_line(line: str) -> bool:
    """A date-prefixed line that is dates and nothing else ('2006-2010,
    2012, 2013', 'February 2018 – Present'): a record's date column that
    stage 2 split from its payload. Evidence that the entry fuses several
    records -- so it counts toward the lint-8 floor -- but never a record
    that can be verified against the output on its own."""
    payload = _date_payload(line)
    return payload is not None and not _RECORD_PAYLOAD_RE.search(payload)


# ---------------------------------------------------------------------------
# Magnitude thresholds: WARN means "unusual", not "present" (#438).
#
# Three lints used to emit a flat WARN whenever they fired at all. Because they
# also fire on most runs, 77% of the corpus landed on the identical WARN verdict
# and the verdict carried no information -- an unchanged verdict was never
# evidence of no regression, because it was never going to change.
#
# The rule is one sentence: a lint WARNs when this run sits in the corpus's
# worst quartile for it, and is INFO when the run is typical. The numbers below
# are the measured p75 of each magnitude over the 73 scored runs of the
# 2026-07-25 batch (sha 9aec6a6), which moves the largest verdict bucket from
# 77% to 58%:
#
#   magnitude                                   n    p50   p75   p90   max
#   table_shape malformed-row ratio            41   0.18  0.27  0.40  0.60  (retired, see below)
#   table_shape defects per table              41      2     4     7     10  (retired, see below)
#   missed_headers per run                     21      2     6     9     13
#   classified_unrendered entries lost/run     21      1     2     3      4
#
# Reproduce: join ~/worktrees/batch-slices/slice{2,3,4}/_batch_runs/scores.tsv
# with run_doctor re-run over the batch worktree outputs.
#
# These are static constants measured once, so they go stale as the pipeline
# improves -- #440 replaces them with a live corpus baseline. Until then, a
# threshold that drifts is still strictly better than no threshold: today every
# one of these fires WARN at magnitude 1.
#
# table_shape's own two thresholds (TABLE_SHAPE_WARN_ROW_RATIO,
# TABLE_SHAPE_WARN_DEFECTS) were retired by #816: it fired on 24 of 40 runs in
# the 2026-09-11 batch, the same "constant condition carries no information"
# shape #438 named -- the malformed-row count is now the doctor's `metrics`
# block (honors_malformed_rows/honors_rows) and the finding stays INFO always.
# missed_headers and classified_unrendered are unaffected; they still WARN on
# the corpus's worst quartile.


# '• [M2A] ...' style taxonomy-code leak (the pre-#214 appendix format).
# The shape alone also matches the source CV's own text ("[H2O]", "[CO2]",
# "[AI]"), so a hit only counts when the token is a real taxonomy code (#888).
_BRACKET_CODE_RE = re.compile(r"\[([A-Z]\d?[A-Z]?\d?)\]")

_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "core" / "taxonomy_v7.json"


def _load_taxonomy_codes() -> frozenset[str]:
    """Every code a leak could carry: the live v7 codes plus the retired
    `invalid_codes` stage 3b must never emit."""
    taxonomy = json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8"))
    codes = {entry["code"] for entry in taxonomy["codes"]}
    codes.update(taxonomy["invalid_codes"]["codes"])
    return frozenset(codes)


_TAXONOMY_CODES = _load_taxonomy_codes()


def _has_taxonomy_code_leak(line: str) -> bool:
    return any(m.group(1) in _TAXONOMY_CODES
               for m in _BRACKET_CODE_RE.finditer(line))


_APPENDIX_HEADER = "T. APPENDIX"


# Appendix entries render as "• text" (bullets) or "1. text" (numbered).
_APPENDIX_ENTRY_RE = re.compile(r"^(•|\d+\.)\s+")


def _is_appendix_noise(text: str) -> bool:
    normed = " ".join(str(text or "").split())
    if not normed:
        return True
    if is_template_instruction(normed) or is_foreign_template_instruction(normed):
        return True
    return is_source_boilerplate(normed)


def _appendix_contents(blocks: list[tuple[str, str]]
                       ) -> tuple[list[str], int] | None:
    """The appendix's entry lines and its non-paragraph block count, or None
    when the document has no appendix section at all. Factored out of
    `lint_output_hygiene` so the doctor's `metrics` block (#816's
    `appendix_entries`/`appendix_share`) reads the SAME count the finding
    below is built from, rather than a second derivation of "appendix
    entry" that could disagree with it.

    Paragraph-only invariant (#446 review T1.9 / #725 review r3923589271
    pt 9), enforced, not just documented: stage 6 renders the appendix --
    the catch-all for content that did not map to a template section --
    as numbered/bulleted PARAGRAPHS only, never as a table or list block.
    Measured directly against the 65 real *_wcm.docx stage_6_wcm_documents
    farm outputs: 33 of them carry a "T. APPENDIX" section, and 0 of those
    33 contain a table block anywhere inside it -- but scanning only
    `kind == "p"` blocks to find entries would silently miss one on a
    future document that DOES break the invariant, so the non-paragraph
    count is returned alongside the entries rather than folded into them.
    """
    paras = [text for kind, text in blocks if kind == "p"]
    appendix_at = next((i for i, t in enumerate(paras)
                        if t.strip() == _APPENDIX_HEADER), None)
    if appendix_at is None:
        return None

    non_paragraph_count = 0
    in_appendix = False
    for kind, text in blocks:
        stripped = str(text).strip()
        if kind == "p":
            if stripped == _APPENDIX_HEADER:
                in_appendix = True
                continue
            if in_appendix and _output_section_header(stripped):
                in_appendix = False
        elif in_appendix:
            non_paragraph_count += 1

    entries = []
    for text in paras[appendix_at + 1:]:
        stripped = text.strip()
        if _output_section_header(stripped):
            break
        if _APPENDIX_ENTRY_RE.match(stripped):
            entries.append(_APPENDIX_ENTRY_RE.sub("", stripped).strip())
    return entries, non_paragraph_count


def appendix_entry_count(blocks: list[tuple[str, str]]) -> int | None:
    """`appendix_entries` for the doctor's `metrics` block (#816) -- None
    when the document has no appendix section at all, distinguishing "no
    appendix" from "appendix, but empty" the same way a missing artifact is
    kept distinct from an empty one elsewhere in the doctor."""
    found = _appendix_contents(blocks)
    return len(found[0]) if found is not None else None


def lint_output_hygiene(blocks: list[tuple[str, str]]) -> list[dict]:
    """Bracketed taxonomy-code leaks anywhere in the output, plus appendix
    size and boilerplate lines rendered as appendix entries."""
    findings = []
    leaks = []
    for _, text in blocks:
        for line in str(text).split("\n"):
            if _has_taxonomy_code_leak(line):
                leaks.append(line.strip())
    if leaks:
        findings.append(_finding(
            "output_hygiene", "ERROR",
            f"{len(leaks)} bracketed taxonomy-code leak(s) in output text",
            [leak[:100] for leak in leaks[:5]]))

    found = _appendix_contents(blocks)
    if found is None:
        return findings
    entries, non_paragraph_count = found

    if non_paragraph_count:
        findings.append(_finding(
            "output_hygiene", "WARN",
            f"{non_paragraph_count} non-paragraph block(s) inside the "
            "appendix -- entries there are only scanned as paragraphs"))

    boiler = [e for e in entries if _is_appendix_noise(e)]
    if boiler:
        findings.append(_finding(
            "output_hygiene", "WARN",
            f"{len(boiler)} boilerplate line(s) rendered in the appendix",
            [b[:100] for b in boiler[:5]]))
    # #816: this finding is now always INFO. It fired on 37 of 40 runs in
    # the 2026-09-11 batch -- an appendix count is a corpus-level metric
    # (moved to the doctor's `metrics` block as appendix_entries/
    # appendix_share), not a per-run WARN; a threshold on it carried no
    # per-run information (#438's class, generalized by #816).
    findings.append(_finding(
        "output_hygiene", "INFO",
        f"appendix holds {len(entries)} unmapped entr"
        + ("y" if len(entries) == 1 else "ies")))
    return findings


# A leading list-label ("B. ", "3) ") on one side only, stripped before
# splitting into coordinate segments.
_NAME_LABEL_RE = re.compile(r"^[A-Za-z0-9]{1,3}[.)]\s+")


_NAME_TOKEN_RE = re.compile(r"[^\W_]+")


def _name_tokens(text: str) -> set[str]:
    stripped = _NAME_LABEL_RE.sub("", norm(text))
    return {tok for tok in _NAME_TOKEN_RE.findall(stripped) if len(tok) > 1}


def _names_match(a: str, b: str) -> bool:
    """Whole-WORD token containment (#446 review T1.3), not a bare 6-char
    substring: the old rule matched 'education' as a fragment INSIDE the
    single word 'educational' -- a cross-word-boundary partial match, not a
    real name relationship -- confirmed live: the farm's own 'education' and
    'educational contributions' output headers collide under it. Splitting
    into whole-word tokens and requiring the shorter name's token set to be
    a SUBSET of the longer's closes that (different words, no shared token)
    while still matching a name against a longer, more specific header that
    legitimately carries it as one of its own words: 'Honors' inside
    'B. Honors and Awards', and -- corpus-verified via the doctor A/B gate,
    see the D1(b) note in the PR body -- 'Research Presentations' inside a
    'RESEARCH' output section it must still be recognized as belonging to.

    This alone does not decide dead_sections: a spurious match CAN fabricate
    a finding when it is the only candidate a bare single-word source name
    picks up (#725 review r3923589271 pt 3 -- 'Research' coincidentally
    matching an unrelated, genuinely empty 'Research Administration' output
    section fires a WARN even when the real content rendered correctly
    under a differently-named section, since nothing else was checked).
    lint_dead_sections guards against exactly that case; see its docstring.

    Since 13e9e0d, lint_dead_sections consults this function at all only for
    a multi-word source name -- a single-word source name matches only on an
    exact normalized name, never on this token-containment fallback. So the
    'Honors' inside 'B. Honors and Awards' pair above no longer counts for
    dead_sections: a single-word source whose only output candidate is that
    kind of compound heading is not reported dead through this path."""
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return shorter <= longer


def lint_dead_sections(stage2: dict,
                       blocks: list[tuple[str, str]]) -> list[dict]:
    """A source section with several substantive lines (grouped by each
    entry's top-level hierarchy header, stage 2) whose name-matched WCM
    output section holds nothing beyond template scaffolding — neither
    paragraphs nor tables.

    Candidate selection (#725 review r3923589271 pt 3): an exact
    normalized-name match is used alone when one exists. Absent that, a
    bare single-word source name (one token: 'Research', 'Honors') is too
    generic to trust on fuzzy token containment by itself -- it can
    coincidentally hit an unrelated, genuinely empty compound section name
    ('Research Administration') while the real content renders correctly
    under a third, differently-named section, fabricating a WARN with
    nothing actually missing -- so a single-word name only matches
    exactly. A multi-word source name ('Research Presentations') still
    falls back to `_names_match`'s token containment, which is what the
    farm's 2100_Mocco true positive (matching the shorter 'RESEARCH' output
    section) needs."""
    per_h1: Dict[str, int] = {}
    for e in stage2.get("entries", []):
        if e.get("element_type") in ("header", "break"):
            continue
        top = norm((e.get("hierarchy") or ["(none)"])[0]) or "(none)"
        lines = sum(1 for line in str(e.get("text", "")).split("\n")
                    if len(norm(line)) >= SUBSTANTIVE_LINE_CHARS)
        per_h1[top] = per_h1.get(top, 0) + lines

    sections: List[List] = []  # [raw title, normalized name, substantive lines]
    current = None
    for kind, text in blocks:
        stripped = str(text).strip()
        name = _output_section_header(stripped) if kind == "p" else None
        if name:
            current = [stripped, name, 0]
            sections.append(current)
            continue
        if current is None:
            continue
        # A table cell carries a name, a year, an amount -- short by design.
        # SUBSTANTIVE_LINE_CHARS is calibrated for prose paragraphs and would
        # read a real table's own rows as empty (#446 review T1.4); any
        # non-blank table line counts as rendered content on its own, while
        # a paragraph line still needs the length floor.
        for line in str(text).split("\n"):
            if is_template_instruction(line) or is_foreign_template_instruction(line):
                continue
            if kind == "table":
                if norm(line):
                    current[2] += 1
            elif len(norm(line)) >= SUBSTANTIVE_LINE_CHARS:
                current[2] += 1

    findings = []
    for h1, n_lines in sorted(per_h1.items()):
        if h1 == "(none)" or n_lines < DEAD_SECTION_MIN_LINES:
            continue
        exact = [s for s in sections if s[1] == h1]
        if exact:
            matched = exact
        elif len(_name_tokens(h1)) > 1:
            matched = [s for s in sections if _names_match(h1, s[1])]
        else:
            matched = []
        if matched and all(s[2] == 0 for s in matched):
            findings.append(_finding(
                "dead_sections", "WARN",
                f"source section '{h1}' has {n_lines} substantive line(s) "
                f"but matching output section '{matched[0][0]}' is empty"))
    return findings


def _record_lines(text: object) -> list[str]:
    """Record-like lines of an entry: pipe/tab rows, plus date-prefixed lines
    that pass `_is_date_record_line` (a record, not prose or a bare range)."""
    return [line.strip() for line in str(text or "").split("\n")
            if looks_like_record(line) or _is_date_record_line(line.strip())]


def _bare_date_lines(text: object) -> int:
    """How many lines of an entry are a bare date column (see
    `_is_bare_date_line`)."""
    return sum(1 for line in str(text or "").split("\n")
               if _is_bare_date_line(line.strip()))


def _line_token_sets(blocks: List[Tuple[str, str]]) -> List[set]:
    """Distinctive-token set per OUTPUT LINE. Lint 8 verifies each record line
    against single output lines: the pooled document tokens of _haystacks let
    common academic words scattered across unrelated sections vouch for a
    dropped record, while a 5c/5d-reformatted citation still matches here
    because its surname/title tokens stay together on one line."""
    return [_long_word_tokens(line)
            for _, text in blocks for line in str(text).split("\n")
            if line.strip()]


# Lint 8, the token-overlap side (#446 review T1.5 / #746). Two records can
# share most of their 5+-letter tokens and still be different records:
# within one CV the owner's surname is on every citation line and the home
# institution on most, so those tokens vouch for nothing, and two citations
# by the same authors in the same journal differ in exactly their year. A
# token on at least this share of the output's lines -- and on at least this
# many lines, so a short document does not disqualify its own content -- is
# UBIQUITOUS and is left out of the overlap. Measured over the 65 rendered
# farm outputs the rule marks ~8 tokens per document: the template's own
# column labels ('dates', 'institution', 'title'), the home institution
# ('weill', 'cornell') and the owner's surname -- 541 of the 65,291
# (token, document) pairs.
RENDER_UBIQUITOUS_LINE_SHARE = 0.05


RENDER_UBIQUITOUS_MIN_LINES = 10


class RenderedLines(NamedTuple):
    """Per-line views of the rendered output that lint 8 scores each record
    against. Read the fields by name, not by position."""
    tokens: list[set[str]]      # distinctive 5+-letter tokens per line
    years: list[set[str]]       # years per line, for the date-agreement check
    ubiquitous: frozenset[str]  # tokens on RENDER_UBIQUITOUS_LINE_SHARE+ of lines


def _ubiquitous_tokens(line_token_sets: list[set[str]]) -> frozenset[str]:
    """Tokens on at least RENDER_UBIQUITOUS_LINE_SHARE of the output lines
    (and at least RENDER_UBIQUITOUS_MIN_LINES of them)."""
    counts: Counter[str] = Counter()
    for tokens in line_token_sets:
        counts.update(tokens)
    floor = max(RENDER_UBIQUITOUS_MIN_LINES,
                RENDER_UBIQUITOUS_LINE_SHARE * len(line_token_sets))
    return frozenset(tok for tok, n in counts.items() if n >= floor)


def _rendered_lines(blocks: list[tuple[str, str]]) -> RenderedLines:
    """The per-line token sets, per-line year sets and ubiquitous-token set
    of the rendered output, built once per document."""
    lines = [line for _, text in blocks for line in str(text).split("\n")
             if line.strip()]
    tokens = [_long_word_tokens(line) for line in lines]
    years = [set(_YEAR_RE.findall(line)) for line in lines]
    return RenderedLines(tokens, years, _ubiquitous_tokens(tokens))


def _line_vouches(tokens: set[str], record_years: set[str],
                  line_tokens: set[str], line_years: set[str]) -> bool:
    """One output line vouches for a record chunk when it carries the
    overlap share of the chunk's distinctive tokens AND does not carry a
    different year: a dated line that disagrees on the year is a different
    record sharing the same words, not this record reformatted. A line with
    no year of its own (stage 5c/5d put the date on a separate bullet)
    cannot disagree, and falls back to the token overlap alone."""
    if len(tokens & line_tokens) / len(tokens) < RENDER_TOKEN_OVERLAP:
        return False
    if record_years and line_years and not (record_years & line_years):
        return False
    return True


def _record_rendered(line: str, haystack: str,
                     output: RenderedLines) -> bool | None:
    """Whether one record line surfaces in the output: verbatim piece first,
    then per-output-line overlap of the record's DISTINCTIVE tokens (the
    output's ubiquitous tokens left out) on a line whose year does not
    contradict the record's. Verbatim absence alone proves nothing (stage 6
    reformats dates/fields), so False requires a token-verifiable miss; a
    line without enough distinctive tokens is None, not missing."""
    if any(piece in haystack for piece in _entry_pieces(line)):
        return True
    record_years = set(_YEAR_RE.findall(line))
    rendered = None
    for chunk in [line] + entry_fragments(line):
        tokens = _long_word_tokens(chunk) - output.ubiquitous
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        if any(_line_vouches(tokens, record_years, line_tokens, line_years)
               for line_tokens, line_years in zip(output.tokens, output.years)):
            return True
        rendered = False
    return rendered


def lint_unrendered_records(stage4: dict,
                            blocks: list[tuple[str, str]]) -> list[dict]:
    """Per-record render check over fused multi-record stage-4 entries: the
    structured-fields-only render paths keep the extracted record and drop
    the unextracted remainder lines with no bullet fallback (#221). No
    element_type filter — the KFGXBW loss was on a 'break' entry; 'T' is
    skipped (appendix catch-all)."""
    # Only h.text (the verbatim-containment haystack) is used here; h.tokens
    # (the pooled document token set) is deliberately not — this lint scores
    # each record against per-OUTPUT-LINE token sets so common academic words
    # scattered across unrelated sections can't vouch for a dropped record.
    h = _haystacks(blocks)
    output = _rendered_lines(blocks)
    findings = []
    for e in stage4.get("entries", []):
        code = e.get("taxonomy_code")
        if code == "T":
            continue
        records = _record_lines(e.get("text"))
        if len(records) + _bare_date_lines(e.get("text")) < UNRENDERED_MIN_RECORD_LINES:
            continue
        absent = [r for r in records
                  if _record_rendered(r, h.text, output) is False]
        if not absent:
            continue
        findings.append(_finding(
            "unrendered_records", "WARN",
            f"entry {e.get('element_idx_start')} ({code}): {len(absent)} of "
            f"{len(records)} records absent from output",
            [r[:100] for r in absent[:5]]))
    return findings


# Lint 8b, section_lost (#817): a taxonomy code whose stage-4 entries leave
# no trace in the code's OWN output section -- lost at render (YME2VA's 5
# institutional-leadership records, section empty) or routed elsewhere
# (BYFQBG's 11 training grants, all in the Appendix). Lint 8 cannot see
# either: it checks only multi-record entries, against the WHOLE document,
# where the same words in lectures or mentee job titles vouch for the lost
# record. Calibrated 2026-09-29 on 126 corpus renders from dev plus the 12
# score-vs-autopsy runs, every flag hand-labelled: each test below alone was
# 15-29% precise; the three together flag 19 codes on stage-4 input, 13 real.
SECTION_LOST_SHARE = 0.25       # the code's tokens present in its section
SECTION_LOST_MIN_TOKENS = 6     # a code with fewer tokens is too thin to judge
SECTION_LOST_ENTRY_HITS = 3     # tokens one entry needs in the section to count as rendered
SECTION_LOST_HEAD_SHARE = 0.5   # share of an entry's first-line tokens that counts as rendered
SECTION_LOST_HEAD_MIN_TOKENS = 2  # a one-token first line matches by coincidence too easily

#: Output section heading prefixes by taxonomy letter, in stage 6's
#: wording. C renders under EDUCATION and L under EDUCATIONAL CONTRIBUTIONS,
#: so neither has a heading of its own.
_SECTION_HEADING_PREFIXES = MappingProxyType({
    "A": "PERSONAL DATA", "B": "EDUCATION", "D": "PROFESSIONAL POSITIONS",
    "E": "EMPLOYMENT STATUS", "F": "LICENSURE", "G": "INSTITUTIONAL/HOSPITAL",
    "H": "HONORS", "I": "PROFESSIONAL ORGANIZATIONS", "J": "PERCENT EFFORT",
    "K": "EDUCATIONAL CONTRIBUTIONS", "M": "RESEARCH", "N": "MENTORING",
    "O": "INSTITUTIONAL LEADERSHIP", "P": "INSTITUTIONAL ADMINISTRATIVE",
    "Q": "EXTRAMURAL", "R": "INVITATIONS TO SPEAK", "S": "BIBLIOGRAPHY",
})
_SECTION_SHARED_WITH = MappingProxyType({"C": "B", "L": "K"})
_SECTION_LETTERS = frozenset(_SECTION_HEADING_PREFIXES) | frozenset(_SECTION_SHARED_WITH)

#: A is withheld by design (protected data), M1 is replaced by the research
#: summary, T is the Appendix catch-all.
_SECTION_LOST_SKIP_CODES = frozenset({"A", "M1", "T"})


@functools.cache
def _template_label_tokens() -> frozenset[str]:
    """Tokens of the template's one-word lines ("Awards", "Institution"):
    the squashed template haystack joins every multi-word line into one
    long run, so only single-word labels come out as real words. They are
    column and section labels every render carries, so they vouch for no
    entry. The calibration measured exactly this set; the template's full
    vocabulary instead doubled the flags at lower precision."""
    return frozenset(_long_word_tokens(_template_haystack()))


def _section_letter(header: str) -> str | None:
    """Taxonomy letter of an output section header ("T" for the Appendix);
    None for a sub-heading, which stays in the section above it. Longest
    prefix first: EDUCATION is a prefix of EDUCATIONAL CONTRIBUTIONS."""
    head = header.strip().upper()
    if head.startswith(_APPENDIX_HEADER):
        return "T"
    for letter, prefix in sorted(_SECTION_HEADING_PREFIXES.items(),
                                 key=lambda item: -len(item[1])):
        if head.startswith(prefix):
            return letter
    return None


def _section_tokens(blocks: list[tuple[str, str]]) -> defaultdict[str | None, set[str]]:
    """Distinctive-token set of each output section, keyed by letter (None
    for anything above the first recognized heading)."""
    tokens: dict[str | None, set[str]] = defaultdict(set)
    letter = None
    for kind, text in blocks:
        if kind == "p" and _output_section_header(text):
            letter = _section_letter(text) or letter
        tokens[letter] |= _long_word_tokens(text)
    for code_letter, host in _SECTION_SHARED_WITH.items():
        tokens[code_letter] = tokens[host]
    return tokens


def _code_absent_from_section(texts: list[str], section: set[str],
                              ubiquitous: frozenset[str]) -> bool:
    """Whether a code's entries leave no trace in its section. All three
    must hold: under SECTION_LOST_SHARE of the code's distinctive tokens are
    there; no entry has SECTION_LOST_ENTRY_HITS of its tokens there; and no
    entry's first line (the record's title/role) is there by
    SECTION_LOST_HEAD_SHARE. The first-line test keeps a rendered role row
    whose long description the section's table has no slot for.

    Not judged at all: a code with under SECTION_LOST_MIN_TOKENS tokens, or
    whose entries all have under SECTION_LOST_ENTRY_HITS (nothing substantial
    enough to call lost). A first line with under
    SECTION_LOST_HEAD_MIN_TOKENS tokens cannot vouch for its entry."""
    union: set[str] = set()
    entry_hits = []
    for text in texts:
        tokens = _long_word_tokens(text) - ubiquitous
        union |= tokens
        if len(tokens) >= SECTION_LOST_ENTRY_HITS:
            entry_hits.append(len(tokens & section))
        first = next((line for line in text.split("\n") if line.strip()), "")
        head = _long_word_tokens(first) - ubiquitous
        if (len(head) >= SECTION_LOST_HEAD_MIN_TOKENS
                and len(head & section) / len(head) >= SECTION_LOST_HEAD_SHARE):
            return False
    return (len(union) >= SECTION_LOST_MIN_TOKENS
            and len(union & section) / len(union) < SECTION_LOST_SHARE
            and bool(entry_hits) and max(entry_hits) < SECTION_LOST_ENTRY_HITS)


def lint_section_lost(stage4: dict,
                      blocks: list[tuple[str, str]]) -> list[dict]:
    """One WARN per taxonomy code whose entries are absent from the code's
    own output section (see `_code_absent_from_section`): lost at render,
    or rendered in another section or the Appendix."""
    texts: dict[str, list[str]] = defaultdict(list)
    for e in stage4.get("entries", []):
        code = e.get("taxonomy_code") or ""
        if code and code not in _SECTION_LOST_SKIP_CODES and code[0] in _SECTION_LETTERS:
            texts[code].append(str(e.get("text") or ""))
    sections = _section_tokens(blocks)
    ubiquitous = _rendered_lines(blocks).ubiquitous | _template_label_tokens()
    findings = []
    for code, entry_texts in texts.items():
        if not _code_absent_from_section(entry_texts, sections[code[0]], ubiquitous):
            continue
        heading = _SECTION_HEADING_PREFIXES.get(
            _SECTION_SHARED_WITH.get(code[0], code[0]), code[0])
        findings.append(_finding(
            "section_lost", "WARN",
            f"{code}: {len(entry_texts)} entr{'y' if len(entry_texts) == 1 else 'ies'} "
            f"absent from the {heading} section (lost, or rendered elsewhere)",
            [t.strip().split("\n")[0][:100] for t in entry_texts[:5]]))
    return findings


# Severities a sidecar warning may carry (#565's section_render_failed
# records add "ERROR"; every pre-existing self-check record has no severity
# key at all). Anything else -- an unrecognized string, or the key present
# but empty/None -- falls back to WARN rather than reaching the doctor
# report unvalidated.
_STAGE6_WARNING_SEVERITIES = {"INFO", "WARN", "ERROR"}


def lint_stage6_warnings(report: Dict) -> List[Dict]:
    """Stage 6's post-generation self-check (_validate_output) findings,
    plus per-section render failures (#565), re-emitted from the
    render-warnings sidecar so they reach the doctor report and the Teams
    card instead of dying in the pod log (#228). A record's own "severity"
    carries through when it's a recognized value; older records with no
    "severity" key (and any unrecognized value) default to WARN, same as
    before this lint had a severity path."""
    findings = []
    for w in report.get("warnings", []):
        severity = w.get("severity") or "WARN"
        if severity not in _STAGE6_WARNING_SEVERITIES:
            severity = "WARN"
        findings.append(_finding(
            "stage6_render_warnings", severity,
            f"stage 6 self-check: {w.get('message', '')}",
            [str(e)[:100] for e in (w.get("evidence") or [])[:3]]))
    return findings


# One legitimate pipe can appear in a title; a cluster of single-pipe bullets
# under one section is the fused-cell fallback shape (19 under K4 on 2Q1_ZQ).
PIPE_LEAK_MIN_SEPS = 2


PIPE_CLUSTER_MIN = 3


_NUMBERED_LINE_RE = re.compile(r"^\s*\d+\.\s")


# "...; 2025 November 20; Orlando, FL." — the venue-date wedge of one
# citation; two or more in a single numbered item means fused citations.
_VENUE_DATE_RE = re.compile(r";\s*(?:19|20)\d{2}\b[^;.\n]*;")


#: Sentence ends between two venue-date wedges that make the text between
#: them a second citation's body: its author list and its title each end in
#: one. One abstract presented at several meetings has only the next
#: meeting's place and name there, at most one sentence end before an "also
#: presented at". On the 63-run EBYSBC farm the 10 flagged items of EOSAFF
#: and ZGBCIT (both findings verified false) and 3 more on JNATFN and VGHNZD
#: were that shape; the one item left, on BMHBJZ, is two abstracts.
FUSED_CITATION_GAP_SENTENCES = 2


def _fuses_citations(line: str) -> bool:
    """Whether a numbered item holds a second citation, not just a second
    venue: some gap between consecutive venue-date wedges carries
    `FUSED_CITATION_GAP_SENTENCES` sentence ends (`_SENTENCE_BOUNDARY_RE`,
    so an initial or "Dr." is not one)."""
    wedges = list(_VENUE_DATE_RE.finditer(line))
    return any(
        len(_SENTENCE_BOUNDARY_RE.findall(line, left.end(), right.start()))
        >= FUSED_CITATION_GAP_SENTENCES
        for left, right in zip(wedges, wedges[1:]))


def lint_pipe_leaks(blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Verbatim-fallback formatting reaching the output document: paragraphs
    carrying multiple raw ' | ' field separators, clusters of single-pipe
    bullets under one section, and numbered citations fusing several
    citations' venue-date patterns (#208 rendered costs) -- not one abstract
    listing the meetings it was presented at (`_fuses_citations`). Paragraph
    blocks only: _table_lines synthesizes ' | ' row joins by design. The
    appendix is excluded — it is verbatim-by-contract."""
    multi: List[str] = []
    fused: List[str] = []
    clusters: Dict[str, List[str]] = {}
    section = None
    in_appendix = False
    for kind, text in blocks:
        if kind != "p":
            continue
        line = str(text).strip()
        if not line:
            continue
        if line == _APPENDIX_HEADER:
            in_appendix = True
            continue
        header = _output_section_header(line)
        if header is not None:
            section = header
            continue
        if in_appendix or is_template_instruction(line) or is_source_boilerplate(line):
            continue
        seps = line.count(" | ")
        if seps >= PIPE_LEAK_MIN_SEPS:
            multi.append(f"[{section or '?'}] {line[:100]}")
        elif seps == 1 and line.startswith("•"):
            clusters.setdefault(section or "?", []).append(line[:100])
        if (seps < PIPE_LEAK_MIN_SEPS and _NUMBERED_LINE_RE.match(line)
                and _fuses_citations(line)):
            fused.append(f"[{section or '?'}] {line[:100]}")
    findings = []
    if multi:
        findings.append(_finding(
            "pipe_leaks", "WARN",
            f"{len(multi)} rendered line(s) with >={PIPE_LEAK_MIN_SEPS} "
            f"' | ' field separators — verbatim-fallback formatting reached "
            f"the output",
            multi[:5]))
    for sec, lines in clusters.items():
        if len(lines) >= PIPE_CLUSTER_MIN:
            findings.append(_finding(
                "pipe_leaks", "WARN",
                f"{len(lines)} single-pipe bullet(s) under '{sec}' — "
                f"fused-cell fallback shape",
                lines[:5]))
    if fused:
        findings.append(_finding(
            "pipe_leaks", "WARN",
            f"{len(fused)} numbered citation(s) fusing multiple venue-date "
            f"patterns",
            fused[:5]))
    return findings


HONORS_NAME_BLOB_CHARS = 150


# A period only ends a sentence when it is not the dot of a "Dr." title or of
# a single-letter initial ("Robert D. & Alma W. Moreton ...", #889).
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<!\bDr)(?<!\b[A-Z])\.\s+[A-Z]")


# Orgs of at most this many characters are not compared against the name
# (too short to be a reliable fabrication signal).
HONORS_ORG_MIN_CHARS = 8


def _org_fabricated_from_name(org: str, name: str) -> bool:
    """True when the organization was cut out of the award name (#229/#887):
    it occurs inside the name and carries what no grantor's own name does --
    a digit (a year belongs in the date column) or a trailing holder's role
    word (`_ORG_ROLE_WORDS`): "College of Example Studies 2015 Outstanding
    Thesis" from "... Thesis Award". An award named after its grantor ("<org>
    Fellowship" granted by <org>) is legitimate whatever else its name says:
    every such hit was a real award in batch 4 (#889: 20 of 23) and on the
    63-run EBYSBC farm (5 of 5), and stage 6's own fallback has refused to
    derive an org from the award name that way since #979."""
    org_n = norm(org)
    if not org_n or org_n not in norm(name):
        return False
    return (any(ch.isdigit() for ch in org_n)
            or org_n.split()[-1].strip(".,;") in _ORG_ROLE_WORDS)


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


_US_STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC"}


# Explicit ordered alias tuples per honors-table column role, matched on
# word boundaries (#446 review T1.7): unrestricted substring matching
# returned the first COLUMN containing any key, with no precedence between
# roles, so a compound header could in principle map two roles to the same
# column. The fixed stage-6 header ('name of award', 'organization',
# 'date awarded (yyyy)') matches identically under either rule -- these are
# for header variation defense-in-depth, not a live corpus fix (0/65 farm
# honors tables use a header other than the fixed one).
_AWARD_NAME_ALIASES = ("name of award", "award", "honor")


_AWARD_ORG_ALIASES = ("organization", "granting")


_AWARD_DATE_ALIASES = ("date awarded", "date", "yyyy", "year")


def _alias_col(header: list[str], aliases: tuple[str, ...]) -> int | None:
    """First header column whose text contains one of `aliases` as a whole
    word/phrase, in alias order -- the first alias that matches ANY column
    wins, same first-match precedence as the substring rule it replaces."""
    for alias in aliases:
        pattern = re.compile(rf"\b{re.escape(alias)}\b")
        for idx, h in enumerate(header):
            if pattern.search(h):
                return idx
    return None


class _HonorsTableShape(NamedTuple):
    defects: list[str]
    defective_rows: set
    non_blank_rows: int


def _honors_header(tbl: list[list[str]]) -> list[str] | None:
    """The normalized header row of an honors/awards table with at least one
    data row, or None when `tbl` is not one (no 'name of award'/'date
    awarded' header)."""
    if len(tbl) < 2 or not tbl[0]:
        return None
    header = [norm(cell) for cell in tbl[0]]
    header_all = " ".join(header)
    if "name of award" not in header_all and "date awarded" not in header_all:
        return None
    return header


def _honors_table_shape(tbl: list[list[str]]) -> "_HonorsTableShape | None":
    """One table's honors-shape defects (#229), or None when it is not an
    honors/awards table at all (no 'name of award'/'date awarded' header).
    Factored out of `lint_table_shape` so the doctor's `metrics` block
    (#816's `honors_malformed_rows`/`honors_rows`) sums the SAME per-row
    predicate the finding below is built from, not a second definition of
    'malformed'."""
    header = _honors_header(tbl)
    if header is None:
        return None
    name_i = _alias_col(header, _AWARD_NAME_ALIASES)
    org_i = _alias_col(header, _AWARD_ORG_ALIASES)
    date_i = _alias_col(header, _AWARD_DATE_ALIASES)
    if name_i is None:
        return None
    defective_rows: set = set()
    defects: List[str] = []
    non_blank_rows = 0

    def flag(rn, msg):
        defective_rows.add(rn)
        defects.append(f"row {rn}: {msg}")

    # Enumerate the ORIGINAL rows -- not a pre-filtered blanks-removed
    # list -- so a defect's reported row number is the actual source
    # table row, not its position after blank rows above it were
    # dropped (#446 review T1.8).
    for rn, row in enumerate(tbl[1:], start=1):
        if not any(row):
            continue
        non_blank_rows += 1
        name = row[name_i] if name_i < len(row) else ""
        org = row[org_i] if org_i is not None and org_i < len(row) else ""
        date = row[date_i] if date_i is not None and date_i < len(row) else ""
        if (len(name) > HONORS_NAME_BLOB_CHARS
                or len(_SENTENCE_BOUNDARY_RE.findall(name)) >= 2):
            flag(rn, f"name-cell blob ({len(name)} chars): {name[:80]}")
        if date_i is not None and not date and _YEAR_RE.search(name):
            flag(rn, f"empty date but year in name: {name[:80]}")
        if org in _US_STATE_ABBREVS:
            flag(rn, f"organization is a bare state abbrev: '{org}'")
        elif (org and len(org) > HONORS_ORG_MIN_CHARS
                and _org_fabricated_from_name(org, name)):
            flag(rn, f"organization duplicated in name: {org[:60]}")
    return _HonorsTableShape(defects, defective_rows, non_blank_rows)


def honors_table_totals(tables: list[list[list[str]]]) -> tuple[int, int]:
    """(malformed_rows, total_rows) summed over every honors-shaped table in
    the document, for the doctor's `metrics` block (#816: `honors_malformed_
    rows`/`honors_rows`). Non-honors tables contribute nothing to either
    total, same as `lint_table_shape` skipping them entirely."""
    malformed = total = 0
    for tbl in tables:
        shape = _honors_table_shape(tbl)
        if shape is None:
            continue
        malformed += len(shape.defective_rows)
        total += shape.non_blank_rows
    return malformed, total


# --- honors rows split off one award ------------------------------------------
#
# A CV that writes an award on one line and its "Organization - date", or the
# paper it was given for, on the next reaches stage 6 as ONE H entry holding
# ONE stage-4 award, and the honors parser renders every line as an award of
# its own (EBYSBC ZDCXIV-02: 24 entries, 43 rows). A row is traced to its
# entry by text, so the count is a floor: a row whose name more than one
# entry's text carries ("University of X") is traced to none.

#: The taxonomy code whose entries the honors table renders.
HONORS_CODE = "H"

#: Split-off entries a finding lists as evidence (the malformed-row finding
#: lists 6 defects).
HONORS_SPLIT_EVIDENCE_MAX = 6


class _HonorsEntry(NamedTuple):
    """One stage-4 H entry as the split check reads it: its index, the
    punctuation-free keys of its text's lines and tab cells, and how many
    awards stage 4 extracted from it."""
    element_idx: object
    line_keys: tuple[str, ...]
    awards: int


class HonorsSplit(NamedTuple):
    """An H entry that renders as more honors rows than stage 4 has awards."""
    element_idx: object
    rows: int
    awards: int


def _row_key(text: str) -> str:
    return _PASSAGE_PUNCT_RE.sub("", norm(text))


def _stage4_award_count(fields: object) -> int:
    """How many awards stage 4 extracted for one entry: the longest list of
    objects among its fields (`stage4_records`, or an off-schema list such
    as `awards`, which a fused award list fills), else one."""
    if not isinstance(fields, Mapping):
        return 1
    return max((len(value) for value in fields.values()
                if isinstance(value, list) and value
                and all(isinstance(item, Mapping) for item in value)), default=1)


def _honors_entries(stage4: dict) -> list[_HonorsEntry]:
    entries = []
    for raw in stage4.get("entries", []):
        if raw.get("taxonomy_code") != HONORS_CODE:
            continue
        parts = re.split(r"[\n\t]", str(raw.get("text") or ""))
        entries.append(_HonorsEntry(
            raw.get("element_idx_start"),
            tuple(key for key in map(_row_key, parts) if key),
            _stage4_award_count(raw.get("extracted_fields"))))
    return entries


def _row_owner(name: str, entries: list[_HonorsEntry]) -> int | None:
    """Position in `entries` of the one entry whose text has a line holding
    this row's name, or None when no entry, or more than one, does. The
    whole cell is the key, a " — 04/2003" stage 6 kept from the source
    line included: it is what tells one "University of X" line from
    another."""
    key = _row_key(name)
    if not key:
        return None
    owners = [pos for pos, entry in enumerate(entries)
              if any(key in line for line in entry.line_keys)]
    return owners[0] if len(owners) == 1 else None


def honors_split_entries(tables: list[list[list[str]]],
                         stage4: dict) -> list[HonorsSplit]:
    """The H entries whose rows in the honors table outnumber the awards
    stage 4 extracted from them: one award rendered as several rows."""
    entries = _honors_entries(stage4)
    rows: Counter = Counter()
    for tbl in tables:
        header = _honors_header(tbl)
        name_i = None if header is None else _alias_col(header, _AWARD_NAME_ALIASES)
        if name_i is None:
            continue
        for row in tbl[1:]:
            if name_i < len(row) and row[name_i]:
                owner = _row_owner(row[name_i], entries)
                if owner is not None:
                    rows[owner] += 1
    return [HonorsSplit(entry.element_idx, rows[pos], entry.awards)
            for pos, entry in enumerate(entries) if rows[pos] > entry.awards]


def _honors_split_finding(splits: list[HonorsSplit]) -> dict:
    """WARN, unlike the malformed-row finding: each split row is a record
    that is not one (EBYSBC: 4 of 63 farm runs, each a verified finding)."""
    extra = sum(split.rows - split.awards for split in splits)
    return _finding(
        "table_shape", "WARN",
        f"honors table: {extra} row(s) split off {len(splits)} award "
        f"entr{'y' if len(splits) == 1 else 'ies'} -- one stage-4 award "
        f"renders as several rows",
        [f"entry {split.element_idx} ({HONORS_CODE}): {split.rows} rows from "
         f"{split.awards} stage-4 award(s)"
         for split in splits[:HONORS_SPLIT_EVIDENCE_MAX]])


def lint_table_shape(tables: list[list[list[str]]],
                     stage4: dict | None = None) -> list[dict]:
    """Honors-like tables whose rows are mis-shaped (#229): the stage-6
    multi-award fallback puts citation blobs in the name cell, leaks state
    abbreviations into the organization column, leaves the date column empty
    while the year sits in the name, and cuts the organization out of the
    name. With `stage4` (optional), also an award rendered as several rows
    (`honors_split_entries`)."""
    findings = []
    for tbl in tables:
        shape = _honors_table_shape(tbl)
        if shape is None or not shape.defects:
            continue
        # #816: always INFO now, for the same reason as output_hygiene's
        # appendix finding above -- this fired on 24 of 40 runs in the
        # 2026-09-11 batch, so a magnitude-based WARN on it carried no
        # per-run information. The malformed-row count moves to the doctor's
        # `metrics` block (honors_malformed_rows/honors_rows) instead.
        findings.append(_finding(
            "table_shape", "INFO",
            f"honors table: {len(shape.defective_rows)}/{shape.non_blank_rows} "
            f"row(s) malformed ({len(shape.defects)} defect(s))",
            shape.defects[:6]))
    splits = honors_split_entries(tables, stage4) if stage4 else []
    if splits:
        findings.append(_honors_split_finding(splits))
    return findings


# A duplicated RECORD repeats its whole neighbourhood; a legitimately repeated
# FIELD does not. C0ZGFW renders each teaching record as several per-field
# bullets, and its owner taught the same course at nine venues (#439).
#
# Requiring consecutive blocks is NOT what separates those two cases -- it was
# measured and it does not: the nine venues themselves produce five repeated
# runs of 3-4 consecutive blocks, and run length alone reports 21 passages on
# C0ZGFW's original output, only 4 of which are real. The YEAR requirement in
# lint_duplicate_passages is what does the discrimination (21 -> 4). Run length
# supplies the second half: a repeated single field is not a record.
#
# Two blocks is the threshold because it is the smallest that costs nothing.
# Measured over 123 rendered corpus outputs (four batch runs, 2026-07-25
# 21:38 EDT) the longest YEAR-CARRYING repeated run is 2 blocks on exactly one
# CV -- web119, where the same abstract is listed at two adjacent numbers and
# again two later, read by hand and genuinely duplicated. 108 of the 123 have
# no year-carrying repeated run at all and 14 top out at 1, so 2 fires on that
# one true positive and nothing else; 3 would discard it for no measured gain.
#
# The count is absolute, not a ratio, so losing unrelated content cannot
# improve it. The corpus cannot demonstrate that (its counts are already 0, so
# deleting from it is 0 -> 0); C0ZGFW's original output can, and does: deleting
# 20% of its blocks drawn from OUTSIDE the reported passages, 10 seeds, left
# the count at exactly 4 every time.
DUPLICATE_PASSAGE_MIN_BLOCKS = 2


DUPLICATE_PASSAGE_WARN_COUNT = 1


# Stage 5c/5d renumber lists between runs, so the enumerator cannot be part of
# the comparison; everything but letters and digits is folded because stage 6
# varies its own field separator ('acquisition — Morehead' vs 'acquisition:
# Morehead' are the same record twice on C0ZGFW).
_PASSAGE_ENUMERATOR_RE = re.compile(r"^\s*(?:\(?\d{1,3}[.)]|[•·▪◦*]|[-–—](?=\s))\s*")


_PASSAGE_PUNCT_RE = re.compile(r"[\W_]+")


def _passage_key(text) -> str:
    """Comparison key for one block: leading enumerator dropped from every
    line, punctuation folded, whitespace collapsed, casefolded. Empty for a
    blank spacer paragraph, which is then dropped from the sequence entirely --
    a spacer can neither match another spacer nor break a run."""
    lines = [_PASSAGE_ENUMERATOR_RE.sub("", line)
             for line in str(text or "").split("\n")]
    return " ".join(_PASSAGE_PUNCT_RE.sub(" ", norm("\n".join(lines))).split())


def lint_duplicate_passages(blocks: list[tuple[str, str]]) -> list[dict]:
    """Stretches of DUPLICATE_PASSAGE_MIN_BLOCKS+ consecutive rendered blocks
    that appear twice in the output document — one source record reaching the
    faculty-facing docx more than once (#439).

    A counted passage must carry a year, and that requirement -- not the run
    length -- is what makes this precise. A CV record is individuated by its
    date, so a run repeated with the SAME date is the same record twice, while
    the same activity described identically on different occasions differs in
    exactly the date block. On C0ZGFW's original output the rule keeps 4
    passages of 29 -- all four read by hand and genuinely duplicated records --
    and on the post-#418/#420 rerun it keeps 0 of 16.

    Known blind spots, measured over 125 rendered corpus outputs rather than
    assumed: a record occupying ONE block cannot be seen (a duplicated citation
    is the common shape -- 10 of those 125 carry one, which is why this lint
    fires on 1 of them); a duplicated row inside a table is unreachable because
    read_docx_blocks collapses a whole table into a single block; and a record
    rendered once as a bullet and once as a table row shares no text to match.
    """
    keyed = [(i, _passage_key(text)) for i, (_kind, text) in enumerate(blocks)]
    keyed = [(i, key) for i, key in keyed if key]
    keys = [key for _i, key in keyed]
    span = DUPLICATE_PASSAGE_MIN_BLOCKS
    n = len(keys)
    if n < 2 * span:
        return []

    # Only a distance at which some span-block window already repeats can carry
    # a repeated passage. Taking CONSECUTIVE pairs of each window's positions
    # keeps that set small even when one window repeats many times (measured
    # max 17 distances over 123 documents; 121 of them have none at all).
    windows: Dict[Tuple[str, ...], List[int]] = {}
    for i in range(n - span + 1):
        windows.setdefault(tuple(keys[i:i + span]), []).append(i)
    distances = {b - a for positions in windows.values() if len(positions) > 1
                 for a, b in zip(positions, positions[1:])}

    # `distances` is built globally across every repeated window in the
    # document, so a distance earned by one stretch can also happen to match
    # a DIFFERENT stretch's own occurrences (e.g. stretch A repeats at
    # positions 0, 6, 12 -- earning distance 6 -- while an unrelated stretch B
    # repeats at distance 12 elsewhere; the distance-12 scan then re-pairs
    # A's occurrence at 0 with its own occurrence at 12, which distance 6
    # already charged via the 0-6 and 6-12 pairs). Track each match's SECOND
    # occurrence range in index space and skip a match that overlaps a range
    # already charged, so each redundant copy is counted once regardless of
    # how many distances re-derive it (#446 review, fb73705 rework).
    charged_second_ranges: list[tuple[int, int]] = []
    passages: List[Tuple[int, int, int]] = []
    for distance in sorted(distances):
        i = 0
        while i < n - distance:
            if keys[i] != keys[i + distance]:
                i += 1
                continue
            # Extend to the MAXIMAL matching stretch, so a 5-block duplicate is
            # one finding and not the three overlapping 3-block windows in it.
            j = i
            while j + 1 < n - distance and keys[j + 1] == keys[j + 1 + distance]:
                j += 1
            second_range = (i + distance, i + distance + (j - i + 1) - 1)
            if (j - i + 1 >= span
                    and any(_YEAR_RE.search(keys[t]) for t in range(i, j + 1))
                    and not any(s <= second_range[1] and second_range[0] <= e
                                for s, e in charged_second_ranges)):
                passages.append((i, i + distance, j - i + 1))
                charged_second_ranges.append(second_range)
            i = j + 1
    if len(passages) < DUPLICATE_PASSAGE_WARN_COUNT:
        return []

    evidence = []
    # Document order, not scan-distance order: the block numbers in a report a
    # human reads should ascend, and the [:5] cap should keep the first five.
    for first, second, length in sorted(passages)[:5]:
        evidence.append(
            f"blocks {keyed[first][0]}-{keyed[first + length - 1][0]} repeat "
            f"at {keyed[second][0]}-{keyed[second + length - 1][0]}: "
            f"{str(blocks[keyed[first][0]][1])[:100]}")
    return [_finding(
        "duplicate_passages", "WARN",
        f"{len(passages)} passage(s) of >={span} consecutive rendered blocks "
        f"appear twice — one record reached the output document more than once",
        evidence)]


# duplicate_passages requires a run of >=2 CONSECUTIVE repeated blocks, so a
# record occupying exactly ONE block is invisible to it by construction --
# and that is the common shape for the most frequent duplication in the
# corpus: a numbered citation rendered twice at different list numbers (#446).
# This is a separate rule, not a tuning of duplicate_passages: identity here
# is one enumerated paragraph's own normalized body, not a stretch of
# neighbouring blocks, and it is scoped to the output SECTION a
# consecutive-block rule never had to consider -- a CV may legitimately list
# the same work under two different section headings ("Peer-reviewed" and
# "Selected"), so a repeat across a section boundary is not a defect.
#
# Window and floor are both measured choices, not assumed ones: 6 is the
# window #446's own detection methodology used ("within 6 citations of each
# other"), and 20 characters is the floor the corpus probe used on the
# normalized body so a short repeated fragment ("See above.") standing alone
# between two unrelated records cannot count as a duplicated record.
DUPLICATE_RECORD_WINDOW = 6


DUPLICATE_RECORD_MIN_CHARS = 20


DUPLICATE_RECORD_WARN_COUNT = 1

#: Two bodies one letter apart are one record: stage 5d formats each copy of
#: a record the CV lists twice on its own, and can fold one copy one letter
#: differently (RCBKFG UYFRTL N6: two abstracts listed twice, each pair one
#: 's' apart). Only a letter, never a digit: "<talk>, 2019" beside
#: "<talk>, 2018" is the same talk given twice. Only on a body at least this
#: long, where one letter is a slip rather than a different word.
DUPLICATE_RECORD_FUZZY_MIN_CHARS = 60
#: And only on a body that says where the record was published: a number
#: besides its years (a volume or a page, "1998;24:709"). A body of authors,
#: title and year alone is a talk or an abstract the CV lists once per
#: meeting, and the render leaves the meeting out, so two such bodies one
#: letter apart are two records (126-run farm/batch corpus: web218, web227
#: twice and web244, each the same abstract at two meetings).
_NON_YEAR_NUMBER_RE = re.compile(r"(?<!\d)(?!(?:19|20)\d{2}(?!\d))\d+")


def _one_letter_apart(a: str, b: str) -> bool:
    """Whether at most one inserted, deleted or replaced letter (not a
    digit) turns `a` into `b`."""
    start = 0
    while start < min(len(a), len(b)) and a[start] == b[start]:
        start += 1
    end_a, end_b = len(a), len(b)
    while end_a > start and end_b > start and a[end_a - 1] == b[end_b - 1]:
        end_a, end_b = end_a - 1, end_b - 1
    changed = a[start:end_a] + b[start:end_b]
    return end_a - start <= 1 and end_b - start <= 1 and not any(c.isdigit() for c in changed)


def _same_body(a: str, b: str) -> bool:
    """One record's two rendered bodies: equal, or one letter apart when
    both are DUPLICATE_RECORD_FUZZY_MIN_CHARS or longer and name a volume
    or page (_NON_YEAR_NUMBER_RE)."""
    return a == b or (min(len(a), len(b)) >= DUPLICATE_RECORD_FUZZY_MIN_CHARS
                      and bool(_NON_YEAR_NUMBER_RE.search(a))
                      and _one_letter_apart(a, b))


def _duplicate_block_pairs(blocks: list[tuple[str, str]]) -> list[tuple[int, int]]:
    """(first, second) block indices of every enumerated paragraph whose
    normalized body repeats within DUPLICATE_RECORD_WINDOW enumerated blocks,
    in the same output section (`lint_duplicate_records`)."""
    current_section: str | None = None
    recent: list[tuple[str, int, int]] = []  # (key, enum_index, block_index)
    enum_index = 0
    pairs: list[tuple[int, int]] = []

    for i, (kind, text) in enumerate(blocks):
        header = _output_section_header(text) if kind == "p" else None
        if header is not None:
            if header != current_section:
                current_section = header
                recent = []
            continue
        if kind != "p" or not _PASSAGE_ENUMERATOR_RE.match(str(text or "")):
            continue
        enum_index += 1
        key = _passage_key(text)
        if len(key) < DUPLICATE_RECORD_MIN_CHARS:
            continue
        recent = [r for r in recent
                  if enum_index - r[1] <= DUPLICATE_RECORD_WINDOW]
        match = next((r for r in recent if _same_body(r[0], key)), None)
        if match is not None:
            pairs.append((match[2], i))
        recent.append((key, enum_index, i))
    return pairs


# The record-identity half of duplicate_records (EBYSBC E16/E28: SJWASY-01,
# NDXXAD-02, AKPQEB-04). The block rule above sees only a byte-identical body
# a few list numbers away; a CV that lists one article twice in different
# words ("Conn Med" beside "Connecticut Medicine", a one-letter title typo),
# far apart, or one grant under two funding codes, renders it twice past it.
# Entries are paired on what names the record:
#   - an article, review or book (`_DUPLICATE_CITATION_CODES`, same code):
#     the same title, or a shared PMID or DOI with near the same title words
#     or a shared PMID or DOI where one copy has no title (a citation's tail
#     enriched by its PMID into a second full copy, NDXXAD-02). A shared PMID
#     under two different titles is a stage-5 lookup error, not a duplicate
#     (measured 7 such pairs on the 63-run farm, 0 duplicates).
#     Presentations and abstracts (S4, S8) repeat a title by design: one talk
#     given at several meetings, one abstract per meeting.
#   - a grant (`_DUPLICATE_GRANT_CODES`, ANY of the three codes, so a grant
#     listed as current and as past is one group): the same title and start
#     year, and no funder, grant number or PI that differs. A renewal keeps
#     the title under another start year; three "Administrative Supplement"s
#     to three parent grants, or one training grant's two trainees, share a
#     title and year but not a grant number or PI (measured on the farm).
# Two years that differ are two records, even a year apart: on the farm that
# cut 4 true pairs (an e-pub year beside the print year) and 3 false ones (a
# reprint, a series' yearly review, an abstract and its paper), the trade
# that keeps the rule above 80% precision. A pair fires only when the title
# renders in two blocks of the right kind: a copy stage 6 dedup dropped is
# not a duplicate on the page.
_DUPLICATE_CITATION_CODES = frozenset({"S1", "S2", "S3"})
_DUPLICATE_GRANT_CODES = frozenset({"M2A", "M2B", "M2C"})
DUPLICATE_RECORD_TITLE_MIN_CHARS = 30
DUPLICATE_RECORD_ID_TITLE_OVERLAP = 0.8
_RECORD_PMID_MIN_DIGITS = 5
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_YEAR_IN_VALUE_RE = re.compile(r"\b(?:19|20)\d{2}\b")


class _RecordIdentity(NamedTuple):
    """What names one stage-5d entry as a record, read once."""
    element_idx: object
    code: str
    title: str          # `_passage_key` of the title
    year: int | None
    ids: frozenset[str]  # "pmid:..." / "doi:..."
    # Grants only, each a `_passage_key`; empty when the entry fills none.
    funder: str
    grant_number: str
    pi: str


def _value_year(value: object) -> int | None:
    match = _YEAR_IN_VALUE_RE.search(str(value or ""))
    return int(match.group()) if match else None


def _record_ids(fields: Mapping) -> frozenset[str]:
    ids = set()
    pmid = re.sub(r"\D", "", str(fields.get("pmid") or ""))
    if len(pmid) >= _RECORD_PMID_MIN_DIGITS:
        ids.add(f"pmid:{pmid}")
    doi = _DOI_PREFIX_RE.sub("", str(fields.get("doi") or "").strip()).lower()
    if doi.startswith("10."):
        ids.add(f"doi:{doi}")
    return frozenset(ids)


def _record_identities(stage_5d: Mapping) -> list[_RecordIdentity]:
    identities = []
    for entry in _fields_entries(stage_5d):
        if entry.code not in _DUPLICATE_CITATION_CODES | _DUPLICATE_GRANT_CODES:
            continue
        grant = entry.code in _DUPLICATE_GRANT_CODES
        title = _passage_key(entry.fields.get("title") or "")
        if len(title) < DUPLICATE_RECORD_TITLE_MIN_CHARS:
            title = ""
        ids = frozenset() if grant else _record_ids(entry.fields)
        if not title and not ids:
            continue

        def grant_key(name: str) -> str:
            return _passage_key(entry.fields.get(name) or "") if grant else ""
        identities.append(_RecordIdentity(
            entry.element_idx, entry.code, title,
            _value_year(entry.fields.get("start_date" if grant else "year")),
            ids, grant_key("agency"), grant_key("grant_number"), grant_key("pi_name")))
    return identities


def _title_overlap(a: str, b: str) -> float:
    words_a, words_b = set(a.split()), set(b.split())
    return len(words_a & words_b) / len(words_a | words_b)


def _same_grant(a: _RecordIdentity, b: _RecordIdentity) -> str | None:
    if b.code not in _DUPLICATE_GRANT_CODES or a.title != b.title \
            or a.year is None or a.year != b.year:
        return None
    if any(x and y and x != y for x, y in ((a.funder, b.funder),
                                           (a.grant_number, b.grant_number),
                                           (a.pi, b.pi))):
        return None
    return "title, year"


def _same_record(a: _RecordIdentity, b: _RecordIdentity) -> str | None:
    """What makes two entries one record ("title", "pmid:...", ...), or None."""
    if a.code in _DUPLICATE_GRANT_CODES:
        return _same_grant(a, b)
    if a.code != b.code or (a.year is not None and b.year is not None and a.year != b.year):
        return None
    if a.title and a.title == b.title:
        return "title"
    shared = sorted(a.ids & b.ids)
    if shared and (not a.title or not b.title
                   or _title_overlap(a.title, b.title) >= DUPLICATE_RECORD_ID_TITLE_OVERLAP):
        return shared[0]
    return None


def _title_blocks(title: str, code: str, blocks: list[tuple[str, str]],
                  keys: list[str]) -> set[int]:
    """Blocks a record of `code` with `title` renders as: an enumerated
    paragraph for a citation, a table for a grant."""
    if not title:
        return set()
    kind = "table" if code in _DUPLICATE_GRANT_CODES else "p"
    return {i for i, (block_kind, text) in enumerate(blocks)
            if block_kind == kind and title in keys[i]
            and (kind == "table" or _PASSAGE_ENUMERATOR_RE.match(str(text or "")))}


def _duplicate_entry_pairs(blocks: list[tuple[str, str]], stage_5d: Mapping
                           ) -> list[tuple[_RecordIdentity, _RecordIdentity, str, set[int]]]:
    """(first, second, why, rendered blocks) for each pair of entries naming
    one record whose title renders in at least two blocks. A title other
    entries of other codes also carry (an abstract of the article) must render
    once more for each of them."""
    identities = _record_identities(stage_5d)
    keys = [_passage_key(text) for _kind, text in blocks]
    title_codes = Counter((_passage_key(entry.fields.get("title") or ""), entry.code)
                          for entry in _fields_entries(stage_5d))
    pairs = []
    for i, first in enumerate(identities):
        for second in identities[i + 1:]:
            why = _same_record(first, second)
            if why is None:
                continue
            rendered = (_title_blocks(first.title, first.code, blocks, keys)
                        | _title_blocks(second.title, second.code, blocks, keys))
            family = (_DUPLICATE_GRANT_CODES if first.code in _DUPLICATE_GRANT_CODES
                      else {first.code})
            others = sum(count for (title, code), count in title_codes.items()
                         if title and title in (first.title, second.title)
                         and code not in family)
            if len(rendered) >= 2 + others:
                pairs.append((first, second, why, rendered))
    return pairs


def lint_duplicate_records(blocks: list[tuple[str, str]],
                           stage_5d: Mapping | None = None) -> list[dict]:
    """One record rendered twice.

    Block rule (#446): a single numbered/bulleted paragraph block whose
    normalized body repeats at a different list position within
    DUPLICATE_RECORD_WINDOW enumerated blocks of its first occurrence, in the
    SAME output section -- the shape duplicate_passages cannot see because it
    requires >=2 consecutive repeated blocks, and 55.2% of substantive records
    in the corpus occupy exactly one. Section scope is tracked with
    `_output_section_header`; the match state is reset on every section
    change, so a publication legitimately listed under two different headings
    never fires. A blank spacer paragraph does not match the enumerator
    prefix, so it is skipped rather than consuming a window slot or breaking
    one, same as `lint_duplicate_passages`.

    Record rule (EBYSBC E16/E28, needs `stage_5d`): two entries naming one
    record by title, PMID or DOI, or one grant under two funding codes, at
    any distance (`_duplicate_entry_pairs`). Its evidence leads with the
    entry index; a block pair both of whose blocks a record pair already
    names is not counted twice.
    """
    entry_pairs = _duplicate_entry_pairs(blocks, stage_5d) if stage_5d else []
    named_blocks = set().union(*(rendered for *_rest, rendered in entry_pairs))
    block_pairs = [(first, second) for first, second in _duplicate_block_pairs(blocks)
                   if not {first, second} <= named_blocks]
    count = len(entry_pairs) + len(block_pairs)
    if count < DUPLICATE_RECORD_WARN_COUNT:
        return []

    evidence = [f"entry {first.element_idx} repeats as entry {second.element_idx} "
                f"({first.code}/{second.code}, same {why}): "
                f"{(first.title or second.title)[:80]}"
                for first, second, why, _rendered in entry_pairs]
    evidence += [f"block {first} repeats at {second}: "
                 f"{str(blocks[first][1])[:100]}" for first, second in block_pairs]
    return [_finding(
        "duplicate_records", "WARN",
        f"{count} duplicated record(s) — the same entry appears twice: one "
        f"enumerated entry within the same output section, or one article or "
        f"grant listed twice",
        evidence[:5])]


# A rendered paragraph whose whole text is a date ("June 2019", "07/2008 -
# 06/2013") is a record's date column that the reader split from its payload
# and stage 6 then emitted as its own bullet (#259: 28 of them under
# EDUCATIONAL CONTRIBUTIONS on ZXVGAC). Table cells are excluded -- a date
# column cell is legitimate -- as is the Appendix, which is verbatim by
# contract. The WARN floor comes from the corpus distribution: over the 126
# fresh dev renders (farm + 2026-09-11/-17 batches) plus ZXVGAC, 33 renders
# fire; 30 of them carry 1-4 lines (the isolated "N. 2009." / a two-stint
# date pair shape) and only 7, 8 and 28 lines follow, so 5 sits in the empty
# gap and WARNs on the three runs where the shape is systematic (#259).
DATE_ONLY_LINES_WARN_COUNT = 5


DATE_ONLY_LINES_SAMPLES = 3


def _date_only_paragraphs(blocks: list[tuple[str, str]]) -> list[str]:
    """Body paragraphs outside the Appendix whose whole text (list
    enumerator dropped) is a date expression, in document order."""
    found = []
    in_appendix = False
    for kind, text in blocks:
        if kind != "p":
            continue
        line = str(text or "").strip()
        if line == _APPENDIX_HEADER:
            in_appendix = True
            continue
        if _output_section_header(line) is not None:
            in_appendix = False
            continue
        if in_appendix:
            continue
        bare = _PASSAGE_ENUMERATOR_RE.sub("", line).strip()
        if bare and _is_date_only_text(bare):
            found.append(line)
    return found


def lint_date_only_lines(blocks: list[tuple[str, str]]) -> list[dict]:
    """Paragraphs that are nothing but a date, outside the Appendix (#259).
    WARN at DATE_ONLY_LINES_WARN_COUNT or more, INFO below."""
    lines = _date_only_paragraphs(blocks)
    if not lines:
        return []
    severity = _magnitude_severity(len(lines), DATE_ONLY_LINES_WARN_COUNT)
    return [_finding(
        "date_only_lines", severity,
        f"{len(lines)} body paragraph(s) are only a date -- a record's date "
        f"column split from its payload and rendered as its own line",
        [line[:100] for line in lines[:DATE_ONLY_LINES_SAMPLES]])]


# Lints 14f/14g: text that is not CV content reaching the delivered document
# (#1233, #1224). Neither one has a benign form, so both WARN on a single hit
# and carry no threshold: a threshold would only hide the first occurrence.
#
# Both read the same rendered blocks `lint_pipe_leaks` does. `_table_lines`
# lists every cell of a row and then the row joined by `TABLE_ROW_JOINER`, so a
# leaked cell appears twice in a table block; splitting on it and dropping the
# repeat counts each cell once.
OUTPUT_LEAK_EXCERPT_CHARS = 100
OUTPUT_LEAK_EVIDENCE_LIMIT = 5

# A quoted Python/JSON string literal, escapes allowed.
_QUOTED_LITERAL = r"""(?:'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")"""

# What `str()` of a dict or list prints. The dict key is an identifier-shaped
# quoted name followed by a colon (`{'start_date': ...`); the list opens on a
# quoted string and continues or closes (`['a', ...`, `["it's"]`). A list whose
# first string holds an apostrophe prints with double quotes, so both quote
# styles count. Tuples and bare numeric lists are not covered: nothing in the
# corpus renders them, and `('` / `[1, 2]` are common in real citations.
_PYTHON_REPR_RES = (
    re.compile(r"""\{\s*(?:'[A-Za-z_][\w ]{0,40}'|"[A-Za-z_][\w ]{0,40}")\s*:"""),
    re.compile(r"\[\s*" + _QUOTED_LITERAL + r"\s*[,\]]"),
)

# Chat-assistant meta text. Each phrase is specific to a model talking to its
# user, not to a person writing a CV: the bare forms ("I can't", "please
# provide", "as an AI") are not enough on their own -- a publication title can
# open "I can't" or say "ChatGPT as an AI assistant in ...", the WCM template
# itself says "please provide Visa type", and an AI researcher's CV says "as an
# AI researcher". "As an AI language model" counts only in the model's own
# first-person form ("..., I cannot ..."). The first and sixth below are the
# two MYAXRH printed (stage 4.5 called the model with an empty CV context); the
# rest are the standard refusal and request-for-input openers, with no corpus
# hit of their own.
# ponytail: a phrase list. Ceiling: a refusal worded outside these openers
# passes. Upgrade path: the artifact-side check #1224 names (stage 4.5 with
# `context_used.entry_count == 0` and `generation_method == "llm_generated"`),
# which needs no wording at all.
_REFUSAL_VERBS = (r"(?:access|provide|generate|write|create|summari[sz]e|"
                  r"assist|complete|fulfill)")
_LLM_REFUSAL_RES = (
    re.compile(r"\bI (?:do not|don['’]t) have access to\b", re.IGNORECASE),
    re.compile(r"\bI(?: am|['’]m) (?:unable|not able) to " + _REFUSAL_VERBS
               + r"\b", re.IGNORECASE),
    re.compile(r"\bI (?:cannot|can['’]t|can not) " + _REFUSAL_VERBS + r"\b",
               re.IGNORECASE),
    re.compile(r"\bas an AI (?:language model|assistant),?\s+I\b"
               r"|\bas an AI,\s+I\b", re.IGNORECASE),
    re.compile(r"\bI(?:['’]m| am) sorry,? but\b|\bI apologi[sz]e,? but\b",
               re.IGNORECASE),
    re.compile(r"\bCV CONTEXT\b[^.\n]{0,40}\b(?:empty|blank|missing)\b",
               re.IGNORECASE),
    re.compile(r"\bplease provide the (?:actual |complete |full |original )?"
               r"(?:CV|curriculum vitae|resume)\b", re.IGNORECASE),
    re.compile(r"\b(?:included|provided|attached|shared) (?:in|with) your "
               r"(?:message|request|prompt)\b", re.IGNORECASE),
    re.compile(r"\bI(?:['’]ll| will) be (?:happy|glad) to "
               r"(?:draft|write|help|generate|create|assist)\b", re.IGNORECASE),
)


def _distinct_segments(text: object) -> list[str]:
    """The non-empty ' | '-separated pieces of a block's lines, in order, each
    once -- so a table row's joined line repeats nothing its cell lines said."""
    pieces = (piece.strip()
              for line in str(text or "").split("\n")
              for piece in line.split(TABLE_ROW_JOINER))
    return list(dict.fromkeys(piece for piece in pieces if piece))


def _pattern_excerpts(blocks: list[tuple[str, str]],
                      patterns: tuple[re.Pattern, ...]) -> list[str]:
    """One excerpt per distinct matched text, in document order: the next
    `OUTPUT_LEAK_EXCERPT_CHARS` characters from the earliest match in a line or
    table cell, deduplicated per block, so two cells that print the same repr
    after different leading text share one excerpt. The Appendix is scanned
    too: a leak there is as wrong on the page as one in a section."""
    excerpts: list[str] = []
    for _, text in blocks:
        hits: dict[str, None] = {}
        for segment in _distinct_segments(text):
            matches = [m for m in (p.search(segment) for p in patterns) if m]
            if matches:
                start = min(m.start() for m in matches)
                hits[segment[start:start + OUTPUT_LEAK_EXCERPT_CHARS]] = None
        excerpts.extend(hits)
    return excerpts


def lint_python_repr_in_output(blocks: list[tuple[str, str]]) -> list[dict]:
    """A Python dict or list repr rendered as document text (#1233). A date
    field arrived as `{'start_date': ..., 'end_date': ...}` and a renderer
    that expected a string called `str()` on it; `<year>-{'start_date': ...}`
    reached four IPXFBA CVs' board-certification and invited-talk rows with no
    doctor finding. WARN on any hit."""
    leaks = _pattern_excerpts(blocks, _PYTHON_REPR_RES)
    if not leaks:
        return []
    return [_finding(
        "python_repr_in_output", "WARN",
        f"{len(leaks)} distinct Python dict/list repr text(s) in the output "
        "-- a structured field was written with str() instead of being "
        "formatted",
        leaks[:OUTPUT_LEAK_EVIDENCE_LIMIT])]


def lint_llm_refusal_in_output(blocks: list[tuple[str, str]]) -> list[dict]:
    """Model refusal or request-for-input text rendered as document text
    (#1224). Stage 4.5 called the model with an empty CV context and the reply
    ("I don't have access to specific CV details ...") became the Research
    Activities paragraph of MYAXRH; no lint saw it. WARN on any hit."""
    refusals = _pattern_excerpts(blocks, _LLM_REFUSAL_RES)
    if not refusals:
        return []
    return [_finding(
        "llm_refusal_in_output", "WARN",
        f"{len(refusals)} distinct language-model refusal or "
        "request-for-input text(s) rendered in the output",
        refusals[:OUTPUT_LEAK_EVIDENCE_LIMIT])]


# Lint 14n, owner_missing_from_citation, and its sibling etal_added (#1259):
# each publication read against its OWN rendered bibliography line. Stage 5d's
# prompt cuts an author list to "first 6 authors, et al.", and the #1292 owner
# restore declines whenever stage 4's author list is damaged, so the CV owner
# disappears from their own citation; the co-authors after the sixth go on
# every cut list. The whole-page probes above cannot see either, because the
# owner's name is on every other line: batch EBYSBC (2026-10-02) carried 20
# owner-cut citations on 14 CVs and about 150 co-author cuts on 27, and
# nothing fired.

#: The taxonomy letter of a publication, and of the output section stage 6
#: prints every publication under (`_SECTION_HEADING_PREFIXES`).
_BIBLIOGRAPHY_LETTER = "S"

#: A matching token: 3+ letters or digits, so a year, a volume and a page
#: range count and an author's initials do not.
_CITATION_TOKEN_RE = re.compile(r"[^\W_]{3,}")

#: A rendered line and a publication entry pair when they share at least
#: CITATION_MATCH_MIN_TOKENS tokens and those are at least
#: CITATION_MATCH_MIN_SHARE of the line's own: the line reformats the source
#: citation, so nearly every token it prints is the source's (a PubMed
#: rebuild adds a few). The floor keeps a numbered line that is a year and
#: little else from pairing with every citation of that year. On the 63-run
#: EBYSBC/s7ab/pilot farm rendered from origin/dev c3d87c5f, 8,870 of 8,998
#: publication entries pair; for a share anywhere from 0.5 to 0.7 and a floor
#: from 2 to 6, the 112 owner findings change by at most one and the 706
#: etal_added findings by at most two.
CITATION_MATCH_MIN_TOKENS = 4
CITATION_MATCH_MIN_SHARE = 0.6

#: A surname word this long still counts as shown one letter off: a PubMed
#: rebuild prints PubMed's spelling of the owner's name (NDXXAD: a letter
#: doubled), which is the owner, not their absence.
OWNER_SPELLING_TOLERANCE_MIN_CHARS = 6

#: An "et al." that closes an author list. "et al.," does not: a source that
#: elides the middle of its list goes on after it ("A, B, et al., Owner X").
_ET_AL_CLOSE_RE = re.compile(r"\bet\s+al\b(?!\.?\s*,)", re.IGNORECASE)

#: A source that elides authors itself: "et al.", "et.al.", "and colleagues",
#: "and col.", or an ellipsis. A rendered "et al." then reproduces it.
_SOURCE_ELISION_RE = re.compile(
    r"\bet\.?\s*al\b|\band\s+col(?:l(?:eagues)?)?\b|\u2026|\.\.\.", re.IGNORECASE)

#: A talk the source says was given with others ("co-presented with",
#: "co-presenter"): the owner presented it whether or not the line names them.
_CO_PRESENTED_RE = re.compile(r"\bco-?\s?present(?:ed|er|ers|ing)?\b", re.IGNORECASE)

#: Contact data a source line can carry. The owner's surname inside an email
#: address does not credit them (MRJDWE).
_EMAIL_OR_URL_RE = re.compile(r"\S+@\S+|\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE)

#: Characters of the rendered line quoted as evidence.
CITATION_EVIDENCE_CHARS = 160


class RenderedCitation(NamedTuple):
    """One publication entry and the bibliography line it rendered as, read
    once at the artifact boundary (§8.1). `line` is the accepted-changes
    text -- `docx_body_blocks` keeps `w:ins` and drops `w:delText` -- with
    its citation number removed. `authors` is stage 4's own value, or ''
    when it is absent or not text."""
    element_idx: object
    code: str
    source: str
    authors: str
    line: str


def _bibliography_lines(blocks: list[tuple[str, str]]) -> list[str]:
    """The numbered paragraphs under the BIBLIOGRAPHY heading, numbers
    removed: stage 6 prints each publication as one ("12. Author A, ...").
    The Appendix's numbered verbatim entries are not citations it wrote."""
    lines: list[str] = []
    letter = None
    for kind, text in blocks:
        if kind != "p":
            continue
        if _output_section_header(text):
            letter = _section_letter(text) or letter
        elif letter == _BIBLIOGRAPHY_LETTER and _NUMBERED_LINE_RE.match(text):
            lines.append(_NUMBERED_LINE_RE.sub("", text, count=1).strip())
    return lines


def _citation_tokens(text: str) -> frozenset[str]:
    return frozenset(_CITATION_TOKEN_RE.findall(norm(text)))


def _pair_lines(sources: list[frozenset[str]],
                lines: list[frozenset[str]]) -> dict[int, int]:
    """Source index -> line index, one to one. Every pair over both
    thresholds is ranked by its Dice overlap (shared tokens over both sizes,
    so of an abstract and the paper that followed it, each keeps its own
    line) and taken best first; ties go to the earlier source and line."""
    ranked = []
    for i, source in enumerate(sources):
        for j, line in enumerate(lines):
            shared = len(source & line)
            if (shared >= CITATION_MATCH_MIN_TOKENS
                    and shared >= CITATION_MATCH_MIN_SHARE * len(line)):
                ranked.append((-2 * shared / (len(source) + len(line)), i, j))
    pairs: dict[int, int] = {}
    taken: set[int] = set()
    for _, i, j in sorted(ranked):
        if i not in pairs and j not in taken:
            pairs[i] = j
            taken.add(j)
    return pairs


def _rendered_citations(stage4: dict,
                        blocks: list[tuple[str, str]]) -> list[RenderedCitation]:
    """Every stage-4 publication paired with its bibliography line. One that
    pairs with no line (routed elsewhere, dropped as a duplicate, superseded)
    is not judged: whether it rendered at all is classified_unrendered's
    question."""
    entries = [e for e in stage4.get("entries", [])
               if str(e.get("taxonomy_code") or "").startswith(_BIBLIOGRAPHY_LETTER)]
    lines = _bibliography_lines(blocks)
    pairs = _pair_lines([_citation_tokens(str(e.get("text") or "")) for e in entries],
                        [_citation_tokens(line) for line in lines])
    citations = []
    for i, entry in enumerate(entries):
        if i not in pairs:
            continue
        fields = entry.get("extracted_fields")
        authors = fields.get("authors") if isinstance(fields, Mapping) else None
        citations.append(RenderedCitation(
            entry.get("element_idx_start"), str(entry.get("taxonomy_code")),
            str(entry.get("text") or ""), authors if isinstance(authors, str) else "",
            lines[pairs[i]]))
    return citations


def _one_edit_apart(a: str, b: str) -> bool:
    """Whether two different words are one substitution, insertion or
    deletion apart."""
    if len(a) > len(b):
        a, b = b, a
    if a == b or len(b) - len(a) > 1:
        return False
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:]
    return a[i:] == b[i + 1:]


def _names_owner(text: str, owner: frozenset[str]) -> bool:
    """Whether `text` holds a word of the owner's surname, exactly."""
    return bool(owner & set(_NAME_WORD_RE.findall(norm(text))))


def _shows_owner(text: str, owner: frozenset[str]) -> bool:
    """`_names_owner`, or a word one letter off a long surname word
    (OWNER_SPELLING_TOLERANCE_MIN_CHARS). Lenient on the rendered side only:
    the lint fires when the source credits the owner exactly and the line
    shows no form of them."""
    words = set(_NAME_WORD_RE.findall(norm(text)))
    return bool(owner & words) or any(
        _one_edit_apart(word, name)
        for name in owner if len(name) >= OWNER_SPELLING_TOLERANCE_MIN_CHARS
        for word in words)


def _author_list_end(line: str) -> int | None:
    """Where an "et al." closes the line's author list, or None. One after a
    "." is not in the author list -- a Vancouver author list holds no ".",
    and "In: Editor A, et al. eds." follows the title -- the same rule
    `_restore_stage5d_authors` applies to this text."""
    match = _ET_AL_CLOSE_RE.search(line)
    if match is None or "." in line[:match.start()]:
        return None
    return match.start()


def _owner_credit(citation: RenderedCitation, owner: frozenset[str]) -> str | None:
    """How the source credits the CV owner with this publication, or None:
    stage 4's author list, else the source text (a credit such as "(including
    X)" that stage 4 left out of `authors`, or a list it damaged), else a talk
    the source says was co-presented."""
    if _names_owner(citation.authors, owner):
        return "stage 4's author list names the CV owner"
    if _names_owner(_EMAIL_OR_URL_RE.sub(" ", citation.source), owner):
        return "the source citation names the CV owner"
    if _CO_PRESENTED_RE.search(citation.source):
        return "the source says the CV owner co-presented this talk"
    return None


def _owner_missing_message(citation: RenderedCitation,
                           owner: frozenset[str]) -> str | None:
    """The finding's message when the source credits the owner and the
    rendered author list does not show them, else None. Only the author list
    is read when an "et al." closes it, so a name left in a trailing note
    ("Presented by X") does not count as an author."""
    credit = _owner_credit(citation, owner)
    end = _author_list_end(citation.line)
    if credit is None or _shows_owner(citation.line[:end], owner):
        return None
    cut = "" if end is None else " (its author list is cut to 'et al.')"
    return (f"entry {citation.element_idx} ({citation.code}): {credit}, but the "
            f"rendered citation does not name them{cut}")


def lint_owner_missing_from_citation(stage4: dict,
                                     blocks: list[tuple[str, str]]) -> list[dict]:
    """One WARN per publication whose source credits the CV owner
    (`_owner_credit`) and whose own rendered bibliography line does not name
    them (#1259). The owner is `cv_owner.last_name`; with none, nothing is
    judged -- owner_contact_missing reports that run."""
    owner = _owner_surname_words(stage4)
    if not owner:
        return []
    findings = []
    for citation in _rendered_citations(stage4, blocks):
        message = _owner_missing_message(citation, owner)
        if message is not None:
            findings.append(_finding(
                "owner_missing_from_citation", "WARN", message,
                [citation.line[:CITATION_EVIDENCE_CHARS]]))
    return findings


def lint_etal_added(stage4: dict, blocks: list[tuple[str, str]]) -> list[dict]:
    """One INFO per rendered citation whose author list ends in "et al."
    (`_author_list_end`) where the source text elides no author
    (`_SOURCE_ELISION_RE`): co-authors cut, the stage 5d "first 6" rule (#1259).
    A citation that also lost the owner is owner_missing_from_citation's
    WARN, not reported again here."""
    owner = _owner_surname_words(stage4)
    findings = []
    for citation in _rendered_citations(stage4, blocks):
        end = _author_list_end(citation.line)
        if (end is None or _SOURCE_ELISION_RE.search(citation.source)
                or (owner and _owner_missing_message(citation, owner))):
            continue
        kept = len([name for name in citation.line[:end].split(",") if name.strip()])
        findings.append(_finding(
            "etal_added", "INFO",
            f"entry {citation.element_idx} ({citation.code}): the rendered "
            f"citation keeps {kept} author(s) and then 'et al.', where the "
            f"source elides no author",
            [citation.line[:CITATION_EVIDENCE_CHARS]]))
    return findings


# --------------------------------------------------------------------------
# Lint 14p: a rendered date that reads wrong (EBYSBC E9/E21).
#
# Three shapes, read from the date cells of the rendered tables and from the
# date that opens a body paragraph, each tied back to the stage-4 entry it
# renders so the finding names that entry:
#   open_range  "<start>-Present" where the entry's own text has no open
#               marker, so a one-time role reads as current (WARN)
#   raw_value   a stage-4 value printed as stored: "2003-04-2005-09",
#               "2004-10-07", "2009-Summer" (INFO)
#   same_ends   a range whose two ends read the same: "2013-2013",
#               "October 2008 to October 2008" (INFO)
DATE_SHAPE_OPEN_RANGE = "open_range"
DATE_SHAPE_RAW_VALUE = "raw_value"
DATE_SHAPE_SAME_ENDS = "same_ends"

#: Codes whose start-only rows keep "<start>-Present" by decision, so an open
#: range on them is not reported: I, whose memberships are ongoing (the
#: `POINT_IN_TIME_CODES` comment in stage6/formatting/dates.py). D rows follow
#: the source-open-marker rule like any other row (Paul's 2026-10-02 decision
#: on #1342, superseding the 2026-09-24 one on #946), with one exception below.
DATE_CELL_OPEN_BY_DECISION_CODES = frozenset({"I"})

#: The one D code whose latest row keeps "<start>-Present" by that decision:
#: the CV's latest D1 rank, with no later D1 row, is the faculty member's
#: current rank. "Latest" is by start year; D1 rows that share the latest
#: start year are all exempt, so a tie never raises a WARN.
DATE_CELL_LATEST_RANK_CODE = "D1"

#: Word tokens a date's context must share with one stage-4 entry, and with
#: no other entry as many, for the date to be read as that entry's. A date
#: tied to no entry has no source text to judge an open range by, so an
#: unattributed open range is not reported.
DATE_CELL_MIN_SHARED_TOKENS = 2

#: Shortest word that can tie a date to an entry: a surname can have three
#: letters, while "of" and "in" would tie everything to everything.
DATE_CELL_MIN_TOKEN_CHARS = 3
_ATTRIBUTION_STOPWORDS = frozenset({"and", "the", "for", "with", "from"})

#: Rendered dates quoted per finding.
DATE_CELL_EVIDENCE_LIMIT = 3

_MONTH_WORD = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
               r"June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
               r"Nov(?:ember)?|Dec(?:ember)?)\.?")
_SEASON_WORD = r"(?:Spring|Summer|Fall|Autumn|Winter)"
_ISO_DAY = r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)"
_ISO_MONTH = r"\d{4}-(?:0[1-9]|1[0-2])(?![\d/])"
_RAW_SEASON = rf"\d{{4}}-{_SEASON_WORD}\b"
#: A month, or a month and a day, written after the year with dots, as the
#: source wrote it: "2021.07", "2021.07.15" (RCBKFG KUUKNJ N4). Raw even when
#: the source writes it so, since no WCM date format has a dot.
_DOTTED = r"\d{4}\.(?:0?[1-9]|1[0-2])(?:\.(?:0?[1-9]|[12]\d|3[01]))?(?![\d.])"
_DATE_SIDE = (rf"(?:{_ISO_DAY}|{_ISO_MONTH}|{_RAW_SEASON}|{_DOTTED}"
              rf"|\d{{1,2}}/\d{{1,2}}/\d{{4}}|\d{{1,2}}/\d{{2}}(?:\d{{2}})?(?!\d)"
              rf"|\b(?:{_MONTH_WORD}|{_SEASON_WORD})\s+\d{{4}}|\d{{4}})")
_OPEN_END_WORD = r"(?:present|current|ongoing)\b"
_DATE_EXPR = (rf"(?P<start>{_DATE_SIDE})(?:\s*(?:[-\u2013\u2014]|\bto\b)\s*"
              rf"(?P<end>{_DATE_SIDE}|{_OPEN_END_WORD}))?")

#: One line of a table cell that is wholly a date.
_DATE_CELL_RE = re.compile(rf"\s*{_DATE_EXPR}\s*", re.IGNORECASE)
#: A body paragraph that opens with a date: the date, then a separator and
#: text, or nothing. A date inside running text, such as a citation's, is
#: not read.
_DATED_LINE_RE = re.compile(
    rf"\s*(?:[\u2022\u00b7\u25aa\u25e6*]\s*)?{_DATE_EXPR}"
    rf"(?:\s*(?:[-\u2013\u2014:,]|\t)\s*\S|\s*$)", re.IGNORECASE)
_RAW_SIDE_RE = re.compile(rf"{_ISO_DAY}|{_RAW_SEASON}|{_DOTTED}", re.IGNORECASE)
_DOTTED_RE = re.compile(_DOTTED)
_ISO_MONTH_RE = re.compile(_ISO_MONTH)
_OPEN_END_RE = re.compile(_OPEN_END_WORD, re.IGNORECASE)
_FOUR_DIGIT_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

# The open markers of the 2026-10-02 class-13 decision: what makes an
# entry's own text say its range is still open. A word anywhere in the text
# or its headings ("Current Mentees", "since 2006", "2012 - date"); a dash
# after a year with no end year after it ("2020-", "2009.04 -<tab>",
# "(2007- )"); or a line that opens "YYYY(.MM) - <text>", which stage 6
# reads as open because the reader turns the tab of "2008 -<tab>Member" into
# a space. Read loosely on purpose: a marker anywhere spares the row, so a
# false marker costs a missed hit, never a false one.
_OPEN_MARKER_WORD_RE = re.compile(
    r"\b(?:present|current(?:ly)?|now|ongoing|active|since|continu\w*"
    r"|to\s+date)\b|[-\u2013\u2014]\s*date\b", re.IGNORECASE)
_OPEN_MARKER_DASH_RE = re.compile(
    r"(?<!\d)\d{4}(?:[./]\d{1,2})?(?:[-\u2013\u2014](?!\s*\d)"
    r"|\s+[-\u2013\u2014][ \t]*(?:\t|\)|$))"
    r"|^\W*\d{4}(?:[./]\d{1,2})?\s*[-\u2013\u2014]\s", re.MULTILINE)

_DATE_SHAPE_MESSAGES = MappingProxyType({
    DATE_SHAPE_OPEN_RANGE: "renders as an open range ('-Present') but the "
                           "entry's text has no open marker (present, "
                           "current, now, ongoing, to date, or a trailing "
                           "dash)",
    DATE_SHAPE_RAW_VALUE: "the date renders as the raw stored value",
    DATE_SHAPE_SAME_ENDS: "the date renders as a range whose two ends are "
                          "the same",
})


class _DatedEntry(NamedTuple):
    """What the lint reads off one stage-4 entry, read once (§8.1)."""
    element_idx: object
    code: str
    text: str
    headings: str
    tokens: frozenset[str]
    start_year: str | None
    open_by_decision: bool = False


class _RenderedDate(NamedTuple):
    """One wrongly shaped date on the page and the words beside it."""
    rendered: str
    start_year: str
    shapes: tuple[str, ...]
    context: frozenset[str]


def _attribution_tokens(text: str) -> frozenset[str]:
    """Words that can tie a rendered date to an entry: `_name_tokens`
    (names are short words) without numbers and function words."""
    return frozenset(tok for tok in _name_tokens(text)
                     if len(tok) >= DATE_CELL_MIN_TOKEN_CHARS
                     and not tok.isdigit() and tok not in _ATTRIBUTION_STOPWORDS)


def _dated_entries(stage4: dict) -> dict[str, list[_DatedEntry]]:
    """Every stage-4 entry, indexed by each four-digit year its text or its
    extracted fields carry, so a rendered date can be tied only to an entry
    that holds its start year."""
    entries = [_dated_entry(raw) for raw in stage4.get("entries", [])]
    entries = _mark_latest_rank(entries)
    by_year: dict[str, list[_DatedEntry]] = defaultdict(list)
    for entry, years in entries:
        for year in years:
            by_year[year].append(entry)
    return by_year


def _dated_entry(raw: dict) -> tuple[_DatedEntry, set[str]]:
    """One stage-4 entry read into a `_DatedEntry`, with every four-digit
    year its text or extracted fields carry. Its start year is the first
    year of `start_date`, else the first year of its text."""
    fields = raw.get("extracted_fields")
    fields = fields if isinstance(fields, dict) else {}
    values = " ".join(str(v) for v in fields.values() if v)
    text = str(raw.get("text") or "")
    hierarchy = raw.get("hierarchy")
    headings = [str(h) for h in hierarchy] if isinstance(hierarchy, list) else []
    headings.append(str(raw.get("context_heading") or ""))
    start_years = (_FOUR_DIGIT_YEAR_RE.findall(str(fields.get("start_date") or ""))
                   or _FOUR_DIGIT_YEAR_RE.findall(text))
    entry = _DatedEntry(raw.get("element_idx_start"),
                        str(raw.get("taxonomy_code") or ""), text,
                        "\n".join(headings),
                        _attribution_tokens(f"{text} {values}"),
                        start_years[0] if start_years else None)
    return entry, set(_FOUR_DIGIT_YEAR_RE.findall(f"{text} {values}"))


def _mark_latest_rank(entries: list[tuple[_DatedEntry, set[str]]]
                      ) -> list[tuple[_DatedEntry, set[str]]]:
    """Mark the CV's latest DATE_CELL_LATEST_RANK_CODE rows (no later row of
    that code by start year) as keeping an open range by decision."""
    rank_years = [entry.start_year for entry, _ in entries
                  if entry.code == DATE_CELL_LATEST_RANK_CODE and entry.start_year]
    if not rank_years:
        return entries
    latest = max(rank_years)
    return [(entry._replace(open_by_decision=True)
             if entry.code == DATE_CELL_LATEST_RANK_CODE and entry.start_year == latest
             else entry, years)
            for entry, years in entries]


def _date_shapes(match: re.Match) -> tuple[str, ...]:
    """The wrong shapes of one matched date expression; empty if none."""
    start, end = match["start"], match["end"]
    if end is None:
        return (DATE_SHAPE_RAW_VALUE,) if _RAW_SIDE_RE.fullmatch(start) else ()
    shapes = []
    open_end = bool(_OPEN_END_RE.fullmatch(end))
    if open_end:
        shapes.append(DATE_SHAPE_OPEN_RANGE)
    sides = (start,) if open_end else (start, end)
    if any(_RAW_SIDE_RE.fullmatch(side) or _ISO_MONTH_RE.fullmatch(side)
           for side in sides):
        shapes.append(DATE_SHAPE_RAW_VALUE)
    if not open_end and norm(start) == norm(end):
        shapes.append(DATE_SHAPE_SAME_ENDS)
    return tuple(shapes)


def _date_end(match: re.Match) -> int:
    """Where the matched date expression ends in the matched text."""
    return match.end("end") if match["end"] is not None else match.end("start")


def _rendered_date(text: str, match: re.Match, context: str) -> _RenderedDate | None:
    """The date `match` found in `text`, or None when its shape is fine or
    it has no four-digit year to tie it to an entry by."""
    shapes = _date_shapes(match)
    year = _FOUR_DIGIT_YEAR_RE.search(match["start"])
    if not shapes or year is None:
        return None
    return _RenderedDate(text[match.start("start"):_date_end(match)],
                         year.group(0), shapes, _attribution_tokens(context))


def _is_label_cell(cell: str) -> bool:
    return cell.rstrip().endswith(":")


def _table_dates(table_rows: list[list[list[str]]]) -> list[_RenderedDate]:
    """Wrongly shaped dates in table cells. A date's context is the rest of
    its row. A row that labels its date ("Mentoring Period: | 2019-2019")
    is in a label/value table, one record per table (a mentee, a grant), so
    there the context is every value cell of the table."""
    found = []
    for table in table_rows:
        values = [cell for row in table for cell in dict.fromkeys(row)
                  if cell and not _is_label_cell(cell)]
        for row in table:
            cells = list(dict.fromkeys(cell for cell in row if cell))
            neighbours = values if any(map(_is_label_cell, cells)) else cells
            for cell in cells:
                context = " ".join(c for c in neighbours
                                   if c != cell and not _is_label_cell(c))
                for line in cell.split("\n"):
                    match = _DATE_CELL_RE.fullmatch(line)
                    if match is None:
                        continue
                    date = _rendered_date(line, match, context)
                    if date is not None:
                        found.append(date)
    return found


def _paragraph_dates(blocks: list[tuple[str, str]]) -> list[_RenderedDate]:
    """Wrongly shaped dates that open a body paragraph ("2013-2013 - Lecture,
    ..."), outside the Appendix, which is the CV's own text by contract. The
    context is the rest of the paragraph."""
    found = []
    in_appendix = False
    for kind, text in blocks:
        line = str(text or "")
        if kind != "p":
            continue
        if line.strip() == _APPENDIX_HEADER:
            in_appendix = True
            continue
        if _output_section_header(line) is not None:
            in_appendix = False
            continue
        match = None if in_appendix else _DATED_LINE_RE.match(line)
        if match is None:
            continue
        date = _rendered_date(line, match, line[_date_end(match):])
        if date is not None:
            found.append(date)
    return found


def _attributed_entry(date: _RenderedDate,
                      by_year: dict[str, list[_DatedEntry]]) -> _DatedEntry | None:
    """The one entry holding the date's start year that shares the most
    words with its context: at least DATE_CELL_MIN_SHARED_TOKENS, and
    strictly more than any other entry. None when no entry does."""
    candidates = by_year.get(date.start_year, [])
    scores = sorted(((len(entry.tokens & date.context), i)
                     for i, entry in enumerate(candidates)), reverse=True)
    if not scores or scores[0][0] < DATE_CELL_MIN_SHARED_TOKENS:
        return None
    if len(scores) > 1 and scores[1][0] == scores[0][0]:
        return None
    return candidates[scores[0][1]]


def _shape_is_defect(shape: str, date: _RenderedDate, entry: _DatedEntry) -> bool:
    """Whether `shape` is wrong for this entry. A date printed the way the
    entry's own text writes it is the CV's wording, never a defect. An open
    range is a defect only outside DATE_CELL_OPEN_BY_DECISION_CODES and the
    CV's latest D1 rank, and only when neither the entry's text nor its
    headings carry an open marker. A dotted date is raw however the source
    writes it."""
    if date.rendered in entry.text and not _DOTTED_RE.search(date.rendered):
        return False
    if shape != DATE_SHAPE_OPEN_RANGE:
        return True
    if entry.code in DATE_CELL_OPEN_BY_DECISION_CODES or entry.open_by_decision:
        return False
    return not (_OPEN_MARKER_WORD_RE.search(f"{entry.text}\n{entry.headings}")
                or _OPEN_MARKER_DASH_RE.search(entry.text))


def lint_date_cell_shape(stage4: dict, table_rows: list[list[list[str]]],
                         blocks: list[tuple[str, str]] | None = None) -> list[dict]:
    """Rendered dates that read wrong (EBYSBC E9/E21): an open range the
    source never states, a raw stored value, a range whose two ends are the
    same. One finding per entry and shape, WARN for an open range and INFO
    for the other two. A raw value or same-ended range that no entry can be
    tied to is still reported, in one INFO per shape that names no entry."""
    by_year = _dated_entries(stage4)
    per_entry: dict[tuple[object, str, str], list[str]] = defaultdict(list)
    unattributed: dict[str, list[str]] = defaultdict(list)
    for date in _table_dates(table_rows) + _paragraph_dates(blocks or []):
        entry = _attributed_entry(date, by_year)
        for shape in date.shapes:
            if entry is None:
                if shape != DATE_SHAPE_OPEN_RANGE:
                    unattributed[shape].append(date.rendered)
            elif _shape_is_defect(shape, date, entry):
                per_entry[(entry.element_idx, entry.code, shape)].append(date.rendered)
    findings = []
    for (element_idx, code, shape), rendered in per_entry.items():
        findings.append(_finding(
            "date_cell_shape",
            "WARN" if shape == DATE_SHAPE_OPEN_RANGE else "INFO",
            f"entry {element_idx} ({code}): {shape}: {_DATE_SHAPE_MESSAGES[shape]}",
            list(dict.fromkeys(rendered))[:DATE_CELL_EVIDENCE_LIMIT]))
    for shape, rendered in unattributed.items():
        findings.append(_finding(
            "date_cell_shape", "INFO",
            f"{shape}: {len(rendered)} rendered date(s) tied to no stage-4 "
            f"entry: {_DATE_SHAPE_MESSAGES[shape]}",
            list(dict.fromkeys(rendered))[:DATE_CELL_EVIDENCE_LIMIT]))
    return findings


# Lint junk_or_header_row (EBYSBC E8, E10, E29): a stage-4 entry that is no
# record of its own -- a group header, a lead-in label, a date fragment, a
# dateless copy of a dated appointment -- rendered as a row or bullet.
#
#   date_fragment     the entry's text has no word, only a date or number
#                     ('47.', '1983- 1984.', '1997-'): a wrapped line's tail
#   label             a short line ending in ':' ('Widget Advisor:',
#                     'Widget committees:'): a lead-in for the lines below
#   header_only       stage 4 found only an institution or organization,
#                     perhaps with a place (with a date span only under
#                     teaching), and the row shows next to nothing else:
#                     a group header, or a record whose title was lost
#   undated_duplicate a D1 with no date whose title is a dated D1's: the CV
#                     banner's current titles repeated as an appointment
#   role_only         an undated sentence whose row shows only the role
#                     stage 4 read from it: the duty it describes is lost
#                     (RCBKFG GKAQHB 79, 84)
#   description_only  stage 4 found no name, title or role, only a
#                     description, perhaps placed: the sentence fills the
#                     record's name cell (RCBKFG GKAQHB 94). A narrative
#                     alone is a research statement, written as prose.
JUNK_SHAPE_DATE_FRAGMENT = "date_fragment"
JUNK_SHAPE_LABEL = "label"
JUNK_SHAPE_HEADER_ONLY = "header_only"
JUNK_SHAPE_UNDATED_DUPLICATE = "undated_duplicate"
JUNK_SHAPE_ROLE_ONLY = "role_only"
JUNK_SHAPE_DESCRIPTION_ONLY = "description_only"

#: Severity of every finding. Measured 2026-10-04 over the 63-run
#: EBYSBC/s7ab/pilot farm (origin/dev fb466a0f renders): 104 hits, 65 on an
#: entry a verified autopsy finding names; all 104 hand-read, 102 a header,
#: label, fragment or banner row and 2 partly so. PRECISION.md has the row.
JUNK_ROW_SEVERITY = "WARN"

_JUNK_SHAPE_MESSAGES = MappingProxyType({
    JUNK_SHAPE_DATE_FRAGMENT: "a date or number fragment with no words "
                              "renders as a record of its own",
    JUNK_SHAPE_LABEL: "a lead-in label ending in ':' renders as a record "
                      "of its own",
    JUNK_SHAPE_HEADER_ONLY: "renders as a row with only an institution or "
                            "organization: a group header, or a record "
                            "whose title was not extracted",
    JUNK_SHAPE_UNDATED_DUPLICATE: "an undated appointment whose title a "
                                  "dated D1 row already shows renders as "
                                  "a row with no date",
    JUNK_SHAPE_ROLE_ONLY: "a sentence renders as a row with only the "
                          "role stage 4 read from it",
    JUNK_SHAPE_DESCRIPTION_ONLY: "a sentence with no name, title or role "
                                 "renders as a record of its own",
})

#: Taxonomy letters the lint does not judge. A is Personal Data, rendered as
#: fields; T is the Appendix, the CV's own text by contract; S is the
#: bibliography. For B, C, F and I an institution or organization with dates
#: and nothing else IS the record: a school attended, a licensing state, a
#: society one belongs to.
JUNK_ROW_SKIP_LETTERS = frozenset("ABCFIST")

#: Fields that hold or place a record but never name it.
_JUNK_HOLDER_FIELDS = frozenset({"institution", "organization", "unit_program",
                                 "division_department", "department", "school"})
_JUNK_DATE_FIELDS = frozenset({"start_date", "end_date", "date", "year",
                               "dates", "dates_attended"})
#: The schema's other one-date fields (`config/field_schemas_v1.1.json`): an
#: entry dated by one of these is a dated record too. An L2 project row with
#: its `launch_date` and its name on the line above is no lost duty sentence
#: (126-run farm/batch corpus: web40 150 and 154). Only `_has_dated_field`
#: reads them; the header and description shapes read _JUNK_DATE_FIELDS.
_JUNK_OTHER_DATE_FIELDS = frozenset({
    "launch_date", "issue_date", "filing_date", "effective_date",
    "expiration_date", "submission_date", "recertification_date"})
_JUNK_PLACEMENT_FIELDS = (_JUNK_HOLDER_FIELDS | _JUNK_DATE_FIELDS
                          | {"location", "state_country", "setting"})
#: The one taxonomy letter whose dated institution-only entries are headers.
JUNK_DATED_HEADER_LETTER = "K"

#: Words that make another appointment of the same title: an undated
#: 'Assistant Professor' beside a dated 'Clinical Assistant Professor' is a
#: second post, not a repeat (EBYSBC E4).
_RANK_QUALIFIERS = frozenset({"clinical", "adjunct", "visiting", "research",
                              "associate", "assistant", "emeritus", "courtesy",
                              "affiliate", "acting", "interim", "instructor"})
#: Stage-4 bookkeeping, not a value the record carries.
_JUNK_IGNORED_FIELDS = frozenset({"stage4_records"})

#: A label longer than this is a sentence with a list after it, not a
#: lead-in ('Developed and taught the following courses at ...:').
JUNK_LABEL_MAX_WORDS = 10
#: A rendered row that is itself a label may lack this many of the entry's
#: words: stage 6 cut the lead-in's first word (RCBKFG JJUQDF 326).
JUNK_LABEL_CUT_WORDS = 1

#: The fields that name the owner's role, in the order read. Not `title`:
#: for a talk or a grant that is the work's own name.
_JUNK_ROLE_FIELDS = ("leadership_role", "role")
#: A role-only paragraph stands for an entry whose text is a sentence: at
#: least this many lowercase words, and more of them than capitalised ones
#: (the multi-record lint's prose test).
JUNK_ROLE_ONLY_MIN_LOWERCASE = 4
#: ... and at least this many words the role does not hold: a role that is
#: the whole sentence renders the sentence (EBYSBC JFBPNC 79).
JUNK_ROLE_ONLY_MIN_LOST_WORDS = 4
#: The field a description-only record carries besides its placement.
_JUNK_DESCRIPTION_FIELDS = frozenset({"description"})

#: Words a rendered row may carry beyond the entry's own before it reads as
#: another record: a label like "Teaching Activities", or the city and
#: state stage 5b adds to an institution. Numbers do not count.
JUNK_ROW_EXTRA_WORDS = 2

#: A word in a date fragment's sense: three letters or more, other than an
#: open end ("1997-present" is still a fragment).
_JUNK_WORD_RE = re.compile(r"[^\W\d_]{3,}")
_JUNK_OPEN_WORDS = frozenset({"present", "current", "ongoing"})


class _RenderedRow(NamedTuple):
    """One rendered record: a body paragraph, or a table row's distinct
    non-empty cells joined with TABLE_ROW_JOINER."""
    text: str
    tokens: frozenset[str]
    cells: tuple[frozenset[str], ...]
    has_year: bool


class _JunkCandidate(NamedTuple):
    """A stage-4 entry of one junk shape, and the words that must show for
    a rendered row to be it."""
    element_idx: object
    code: str
    shape: str
    core: frozenset[str]
    allowed: frozenset[str]
    extra_words: int
    #: Whether the entry's text or a date field carries a year: its own
    #: row shows one exactly when it does.
    dated: bool


def _junk_rendered_rows(table_rows: list[list[list[str]]],
                        blocks: list[tuple[str, str]]) -> list[_RenderedRow]:
    """Every rendered record line: body paragraphs that are not section
    headings, and table rows."""
    rows = []
    for kind, text in blocks:
        line = str(text or "").strip()
        if kind != "p" or not line or _output_section_header(line):
            continue
        rows.append(_RenderedRow(line, frozenset(_name_tokens(line)),
                                 (frozenset(_name_tokens(line)),),
                                 bool(_FOUR_DIGIT_YEAR_RE.search(line))))
    for table in table_rows:
        for row in table:
            cells = list(dict.fromkeys(cell for cell in row if cell.strip()))
            if not cells:
                continue
            line = TABLE_ROW_JOINER.join(cells)
            rows.append(_RenderedRow(
                line, frozenset(_name_tokens(line)),
                tuple(frozenset(_name_tokens(cell)) for cell in cells),
                bool(_FOUR_DIGIT_YEAR_RE.search(line))))
    return rows


def _filled_fields(raw: dict) -> dict[str, object]:
    fields = raw.get("extracted_fields")
    if not isinstance(fields, dict):
        return {}
    return {key: value for key, value in fields.items()
            if value not in (None, "", [], {}) and key not in _JUNK_IGNORED_FIELDS}


def _field_tokens(fields: dict[str, object], keys: frozenset[str]) -> frozenset[str]:
    return frozenset(_name_tokens(" ".join(str(fields[key]) for key in keys
                                           if key in fields)))


def _has_dated_field(fields: dict[str, object]) -> bool:
    return any(_FOUR_DIGIT_YEAR_RE.search(str(fields.get(key) or ""))
               for key in _JUNK_DATE_FIELDS | _JUNK_OTHER_DATE_FIELDS)


class _DatedRanks(NamedTuple):
    """The CV's D1 entries that carry a year: each one's title and
    institution words, and the first and last of their entry positions."""
    tokens: tuple[frozenset[str], ...]
    first: float
    last: float


def _entry_position(raw: dict) -> float | None:
    try:
        return float(raw.get("element_idx_start"))
    except (TypeError, ValueError):
        return None


def _dated_ranks(stage4: dict) -> _DatedRanks | None:
    """The dated D1 entries, or None when the CV has none."""
    tokens, positions = [], []
    for raw in stage4.get("entries", []):
        fields = _filled_fields(raw)
        position = _entry_position(raw)
        if (raw.get("taxonomy_code") == DATE_CELL_LATEST_RANK_CODE
                and _has_dated_field(fields) and position is not None):
            tokens.append(_field_tokens(fields, frozenset({"title", "institution"})))
            positions.append(position)
    if not tokens:
        return None
    return _DatedRanks(tuple(tokens), min(positions), max(positions))


def _is_header_only(code: str, fields: dict[str, object]) -> bool:
    """Whether stage 4 found only where the entry sits: an institution or
    organization, perhaps with a place, and no title, role or name. With
    dates too, only under teaching (K): a CV groups its courses under an
    institution and its years, while elsewhere "1998-2004 <organization>"
    is how a CV lists a consultancy or a board seat, and the row is the
    source's own wording. And only a span: one dated day at a place is a
    session of its own (a training given at a site), not a header."""
    keys = fields.keys()
    if not keys <= _JUNK_PLACEMENT_FIELDS:
        return False
    if not keys & _JUNK_DATE_FIELDS:
        return True
    return code.startswith(JUNK_DATED_HEADER_LETTER) and "start_date" in keys


def _undated_duplicate_core(position: float | None, fields: dict[str, object],
                            dated: _DatedRanks | None) -> frozenset[str] | None:
    """The first ';'-part of an undated D1 title whose words a dated D1's
    title and institution all carry (the banner joins several titles).
    Only above or below the dated appointments: an undated row inside the
    list is an appointment whose date was lost, not the CV's banner."""
    if (dated is None or position is None or _has_dated_field(fields)
            or dated.first <= position <= dated.last):
        return None
    for part in str(fields.get("title") or "").split(";"):
        tokens = frozenset(_name_tokens(part))
        if tokens and any(tokens <= other and not (other - tokens) & _RANK_QUALIFIERS
                          for other in dated.tokens):
            return tokens
    return None


def _junk_shape(raw: dict, code: str, text: str, fields: dict[str, object],
                dated: _DatedRanks | None) -> _JunkCandidate | None:
    """The entry's junk shape, first match in the order the shapes are
    listed above; None for a record of its own. A date fragment's row, and
    a dated header's, may show no word beyond the entry's own: a dated row
    with one more word is a record."""
    candidate = functools.partial(
        _JunkCandidate, raw.get("element_idx_start"), code,
        dated=bool(_FOUR_DIGIT_YEAR_RE.search(text)) or _has_dated_field(fields))
    text_tokens = frozenset(_name_tokens(text))
    words = [w for w in _JUNK_WORD_RE.findall(text) if w.lower() not in _JUNK_OPEN_WORDS]
    if not words:
        return candidate(JUNK_SHAPE_DATE_FRAGMENT, text_tokens, text_tokens, 0)
    if text.endswith(":") and len(text.split()) <= JUNK_LABEL_MAX_WORDS:
        return candidate(JUNK_SHAPE_LABEL, text_tokens, text_tokens, JUNK_ROW_EXTRA_WORDS)
    if _is_header_only(code, fields):
        dated_header = bool(fields.keys() & _JUNK_DATE_FIELDS)
        return candidate(JUNK_SHAPE_HEADER_ONLY,
                         _field_tokens(fields, _JUNK_HOLDER_FIELDS),
                         _field_tokens(fields, _JUNK_PLACEMENT_FIELDS),
                         0 if dated_header else JUNK_ROW_EXTRA_WORDS)
    core = (_undated_duplicate_core(_entry_position(raw), fields, dated)
            if code == DATE_CELL_LATEST_RANK_CODE else None)
    if core is not None:
        return candidate(JUNK_SHAPE_UNDATED_DUPLICATE, core, core, 0)
    role = _role_only_core(text, fields)
    if role:
        return candidate(JUNK_SHAPE_ROLE_ONLY, role, role, 0)
    if _is_description_only(fields):
        described = _field_tokens(fields, _JUNK_DESCRIPTION_FIELDS)
        return candidate(JUNK_SHAPE_DESCRIPTION_ONLY, described,
                         _field_tokens(fields, _JUNK_DESCRIPTION_FIELDS | _JUNK_PLACEMENT_FIELDS),
                         JUNK_ROW_EXTRA_WORDS)
    return None


def _role_only_core(text: str, fields: dict[str, object]) -> frozenset[str]:
    """The words of the entry's role when its text is an undated sentence
    (see JUNK_ROLE_ONLY_MIN_LOWERCASE); else empty."""
    role_key = next((key for key in _JUNK_ROLE_FIELDS if key in fields), None)
    if role_key is None or _has_dated_field(fields):
        return frozenset()
    words = _JUNK_WORD_RE.findall(text)
    lowercase = sum(1 for word in words if word[0].islower())
    if lowercase < JUNK_ROLE_ONLY_MIN_LOWERCASE or lowercase <= len(words) - lowercase:
        return frozenset()
    role = frozenset(_name_tokens(str(fields[role_key])))
    lost = frozenset(_name_tokens(text)) - role
    return role if len(lost) >= JUNK_ROLE_ONLY_MIN_LOST_WORDS else frozenset()


def _is_description_only(fields: dict[str, object]) -> bool:
    """Whether stage 4 found only a description of the record, perhaps with
    where and when: no name, title or role."""
    keys = fields.keys()
    return bool(keys & _JUNK_DESCRIPTION_FIELDS) and keys <= (
        _JUNK_DESCRIPTION_FIELDS | _JUNK_PLACEMENT_FIELDS)


def _junk_candidate(raw: dict, dated: _DatedRanks | None) -> _JunkCandidate | None:
    """The entry as a junk candidate, or None: a record of its own, a code
    the lint does not judge, or a shape with no word to look for."""
    code = str(raw.get("taxonomy_code") or "")
    text = str(raw.get("text") or "").strip()
    if not text or code[:1] in JUNK_ROW_SKIP_LETTERS:
        return None
    candidate = _junk_shape(raw, code, text, _filled_fields(raw), dated)
    # No core word, nothing to find: a punctuation-only text, or a header
    # with dates and a place but no institution or organization.
    return candidate if candidate is not None and candidate.core else None


def _extra_words(row: _RenderedRow, allowed: frozenset[str]) -> int:
    return sum(1 for tok in row.tokens - allowed
               if not tok.isdigit() and tok not in _JUNK_OPEN_WORDS)


def _junk_row_rendered(candidate: _JunkCandidate,
                       rows: list[_RenderedRow]) -> _RenderedRow | None:
    """The rendered row that is this entry and nothing more. Of the rows
    that match, the first whose year agrees with the entry's, else the
    first: an undated society header's own row shows no year, while the
    dated membership row before it shows the same name and one extra word
    (QTATUP 1252, 1253, 1261)."""
    matches = [row for row in rows if _junk_row_matches(candidate, row)]
    return next((row for row in matches if row.has_year == candidate.dated),
                matches[0] if matches else None)


def _junk_row_matches(candidate: _JunkCandidate, row: _RenderedRow) -> bool:
    """Whether the row shows every core word and at most the candidate's
    extra words. An undated duplicate's row is a table row with no year
    whose title cell carries the title."""
    if candidate.shape == JUNK_SHAPE_UNDATED_DUPLICATE:
        return not row.has_year and len(row.cells) > 1 and any(
            candidate.core <= cell for cell in row.cells)
    if (candidate.core <= row.tokens
            and _extra_words(row, candidate.allowed) <= candidate.extra_words):
        return True
    return candidate.shape == JUNK_SHAPE_LABEL and _is_cut_label_row(candidate, row)


def _role_only_row(candidate: _JunkCandidate, rows: list[_RenderedRow],
                   taken: set[int]) -> _RenderedRow | None:
    """The first row of the role's words alone that no other entry took;
    it joins `taken`. One per entry, so two entries with one role never
    both claim the same row."""
    for i, row in enumerate(rows):
        if i not in taken and row.tokens == candidate.core:
            taken.add(i)
            return row
    return None


def _is_cut_label_row(candidate: _JunkCandidate, row: _RenderedRow) -> bool:
    """A rendered row that is itself the label, less up to
    JUNK_LABEL_CUT_WORDS of its words."""
    return (row.text.rstrip(" |").endswith(":") and bool(row.tokens)
            and row.tokens <= candidate.core
            and len(candidate.core - row.tokens) <= JUNK_LABEL_CUT_WORDS)


def lint_junk_or_header_row(stage4: dict, table_rows: list[list[list[str]]],
                            blocks: list[tuple[str, str]] | None = None) -> list[dict]:
    """Stage-4 entries that are no record of their own, rendered as one
    (EBYSBC E8, E10, E29): a group header, a lead-in label, a date fragment,
    or a dateless repeat of a dated D1 appointment. One finding per entry,
    named by its element_idx_start, quoting the rendered row."""
    dated = _dated_ranks(stage4)
    rows = _junk_rendered_rows(table_rows, blocks or [])
    findings = []
    taken: set[int] = set()
    for raw in stage4.get("entries", []):
        candidate = _junk_candidate(raw, dated)
        if candidate is None:
            continue
        if candidate.shape == JUNK_SHAPE_ROLE_ONLY:
            row = _role_only_row(candidate, rows, taken)
        else:
            row = _junk_row_rendered(candidate, rows)
        if row is None:
            continue
        findings.append(_finding(
            "junk_or_header_row", JUNK_ROW_SEVERITY,
            f"entry {candidate.element_idx} ({candidate.code}): {candidate.shape}: "
            f"{_JUNK_SHAPE_MESSAGES[candidate.shape]}",
            [row.text]))
    return findings


# Lint fanout_cell_residue (#1445; EOAHMI DUTAVD-01, WYMVGU-01, BRUSUZ-01): a
# record stage 6 fanned out of a multi-record entry (`STAGE4_RECORDS_KEY`,
# #1406) whose table row prints leftover text of the parent entry in a name,
# organization or committee cell. Before #1449 the last record kept the
# parent's whole line, and a renderer that fell back to the text for an empty
# cell printed the other records again: the first two terms' years as the
# organization of a third term, or '93 <first role>' (the sibling's role and
# the tail of '1992-93') beside the second role. #1449 fixed the fallbacks it
# knew of; this lint guards the shape in any grid table, for any code.
#
#   sibling_year   a year another record of the entry holds, and this one
#                  does not
#   year_stub      a two-digit tail of one of the entry's years ('93')
#   bare_to        a cell that opens or ends on the range word 'to'
#   built_line     a built record line ('<role> | 1981 | 1981') in one cell
#   sibling_value  another record's value (its role, venue, committee) with
#                  more text around it
#
# A row is a record's when more of its cells equal that record's values (and
# its date cell that record's years) than any other record's; a tie is no
# one's row. A cell is read only when every word of it is in the parent's
# text: the residue came from there, and a cell stage 5b or a default filled
# is not leftover text. A record's own years in its name cell are not read:
# a renderer that prints a whole line in its name cell does that to every
# entry (EBYSBC MQSUIC 611), not only to split ones.
FANOUT_RESIDUE_SIBLING_YEAR = "sibling_year"
FANOUT_RESIDUE_YEAR_STUB = "year_stub"
FANOUT_RESIDUE_BARE_TO = "bare_to"
FANOUT_RESIDUE_BUILT_LINE = "built_line"
FANOUT_RESIDUE_SIBLING_VALUE = "sibling_value"

#: Severity of every finding. Measured 2026-10-05 (FAN-RES in PRECISION.md):
#: every hit on the dev-248 render and on the 102 fresh dev renders was read
#: against its stage-4 entry and its rendered row.
FANOUT_RESIDUE_SEVERITY = "WARN"
#: Stage 6 fans an entry out at this many records (`fan_out._MIN_RECORDS`).
FANOUT_RESIDUE_MIN_RECORDS = 2
#: Rendered rows quoted per finding.
FANOUT_RESIDUE_EVIDENCE_LIMIT = 3
#: A year's tail as a CV abbreviates a range's end ('1992-93').
FANOUT_YEAR_STUB_DIGITS = 2
#: The range word a cut date leaves behind ('1990 to').
FANOUT_RANGE_WORD = "to"
#: A sibling value counts only with a word this long: a bare 'Dr' or a
#: number is no value of its own.
FANOUT_SIBLING_MIN_WORD_CHARS = 3
#: A grid table's date column, by its header ('Dates (yyyy-yyyy)', 'Year
#: Awarded', 'Date of issue'). A table with none is a label/value table, one
#: record per table, which has no row to misplace text in.
_DATE_HEADER_RE = re.compile(r"\bdates?\b|\byears?\b", re.IGNORECASE)
_LETTER_WORD_RE = re.compile(r"[^\W\d_]+")


class _SplitRecord(NamedTuple):
    """One stage-4 record: its non-date values as rendered cells read them
    (`norm`), their words, and its years."""
    values: frozenset[str]
    tokens: frozenset[str]
    years: frozenset[str]
    value_tokens: tuple[frozenset[str], ...]


class _SplitEntry(NamedTuple):
    """A multi-record stage-4 entry: its records, every year they hold, and
    the words of the parent's text."""
    element_idx: object
    code: str
    records: tuple[_SplitRecord, ...]
    years: frozenset[str]
    text_tokens: frozenset[str]


def _split_record(record: Mapping) -> _SplitRecord:
    values = [str(value) for key, value in record.items()
              if isinstance(value, (str, int, float)) and str(value).strip()
              and not _fan_out_date_key(key)]
    years = frozenset(year for key, value in record.items() if _fan_out_date_key(key)
                      for year in _FOUR_DIGIT_YEAR_RE.findall(str(value or "")))
    value_tokens = tuple(frozenset(_name_tokens(value)) for value in values)
    return _SplitRecord(frozenset(norm(value) for value in values if _name_tokens(value)),
                        frozenset().union(*value_tokens), years, value_tokens)


def _split_entries(stage4: dict) -> list[_SplitEntry]:
    """The entries stage 4 kept two or more records for."""
    entries = []
    for raw in stage4.get("entries", []):
        fields = raw.get("extracted_fields")
        records = fields.get(STAGE4_RECORDS_KEY) if isinstance(fields, Mapping) else None
        if (not isinstance(records, list) or len(records) < FANOUT_RESIDUE_MIN_RECORDS
                or not all(isinstance(record, Mapping) for record in records)):
            continue
        split = tuple(_split_record(record) for record in records)
        entries.append(_SplitEntry(
            raw.get("element_idx_start"), str(raw.get("taxonomy_code") or ""), split,
            frozenset().union(*(record.years for record in split)),
            frozenset(_name_tokens(str(raw.get("text") or "")))))
    return entries


def _row_record(entry: _SplitEntry, cells: list[str], date_years: set[str]) -> int | None:
    """The index of the one record this row renders, or None: no record's
    value is a cell of it, or two records match it equally well."""
    scores = []
    for record in entry.records:
        exact = sum(1 for cell in cells if norm(cell) in record.values)
        dated = bool(exact) and bool(record.years) and record.years == date_years
        scores.append(exact + dated)
    best = max(scores)
    if not best or scores.count(best) > 1:
        return None
    index = scores.index(best)
    years = entry.records[index].years
    return None if years and not years & date_years else index


def _sibling_value_in(cell_tokens: frozenset[str], record: _SplitRecord,
                      siblings: list[_SplitRecord]) -> bool:
    """Whether the cell holds another record's value, with more around it:
    a cell that IS that value is a value both records render (a default
    role, a shared organization)."""
    return any(tokens and not tokens <= record.tokens and tokens < cell_tokens
               and any(len(tok) >= FANOUT_SIBLING_MIN_WORD_CHARS and not tok.isdigit()
                       for tok in tokens)
               for sibling in siblings for tokens in sibling.value_tokens)


def _cell_residue(cell: str, entry: _SplitEntry, index: int) -> list[str]:
    """The residue shapes in one cell of the row of record `index`."""
    tokens = frozenset(_name_tokens(cell))
    if not tokens or not tokens <= entry.text_tokens:
        return []
    record = entry.records[index]
    shapes = []
    if set(_FOUR_DIGIT_YEAR_RE.findall(cell)) & (entry.years - record.years - record.tokens):
        shapes.append(FANOUT_RESIDUE_SIBLING_YEAR)
    if any(len(tok) == FANOUT_YEAR_STUB_DIGITS and tok.isdigit() and tok not in record.tokens
           and any(year.endswith(tok) for year in entry.years) for tok in tokens):
        shapes.append(FANOUT_RESIDUE_YEAR_STUB)
    words = _LETTER_WORD_RE.findall(norm(cell))
    if (words and FANOUT_RANGE_WORD in (words[0], words[-1])
            and FANOUT_RANGE_WORD not in record.tokens):
        shapes.append(FANOUT_RESIDUE_BARE_TO)
    parts = [part.strip() for part in cell.split(TABLE_ROW_JOINER.strip())]
    if len(parts) > 1 and any(_FOUR_DIGIT_YEAR_RE.fullmatch(part) for part in parts):
        shapes.append(FANOUT_RESIDUE_BUILT_LINE)
    siblings = [other for i, other in enumerate(entry.records) if i != index]
    if _sibling_value_in(tokens, record, siblings):
        shapes.append(FANOUT_RESIDUE_SIBLING_VALUE)
    return shapes


def _table_residue(entry: _SplitEntry, table: list[list[str]]) -> list[tuple[str, list[str]]]:
    """Each row of a grid table that one of the entry's records renders with
    residue: the row joined, and its shapes."""
    if not table:
        return []
    date_cols = {i for i, head in enumerate(table[0]) if _DATE_HEADER_RE.search(head)}
    if not date_cols:
        return []
    found = []
    for row in table[1:]:
        cells = [cell for i, cell in enumerate(row) if cell.strip() and i not in date_cols]
        date_years = set(_FOUR_DIGIT_YEAR_RE.findall(
            " ".join(cell for i, cell in enumerate(row) if i in date_cols)))
        index = _row_record(entry, cells, date_years) if cells else None
        if index is None:
            continue
        shapes = [shape for cell in cells if norm(cell) not in entry.records[index].values
                  for shape in _cell_residue(cell, entry, index)]
        if shapes:
            found.append((TABLE_ROW_JOINER.join(c for c in row if c.strip()), shapes))
    return found


def lint_fanout_cell_residue(stage4: dict,
                             table_rows: list[list[list[str]]]) -> list[dict]:
    """Records of a multi-record entry whose table row prints the parent's
    leftover text -- another record's years or value, a cut year, a range
    word, a built line -- in a name, organization or committee cell (#1445).
    One finding per entry, named by its element_idx_start, quoting rows."""
    findings = []
    for entry in _split_entries(stage4):
        rows = [found for table in table_rows for found in _table_residue(entry, table)]
        if not rows:
            continue
        shapes = sorted({shape for _, row_shapes in rows for shape in row_shapes})
        findings.append(_finding(
            "fanout_cell_residue", FANOUT_RESIDUE_SEVERITY,
            f"entry {entry.element_idx} ({entry.code}): {', '.join(shapes)}: a record "
            f"split from this entry prints the entry's leftover text in a name, "
            f"organization or committee cell",
            list(dict.fromkeys(text for text, _ in rows))[:FANOUT_RESIDUE_EVIDENCE_LIMIT]))
    return findings
