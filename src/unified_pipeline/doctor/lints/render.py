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
from pathlib import Path
from types import MappingProxyType
from typing import Dict, List, NamedTuple, Tuple

from unified_pipeline.core.docx_structure_extractor import _is_date_only_text
from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)
from unified_pipeline.segmentation_regression import (
    SUBSTANTIVE_LINE_CHARS,
    _looks_like_record,
    _norm,
)

from ..shared import (
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    _entry_pieces,
    _finding,
    _haystacks,
    _long_word_tokens,
    _magnitude_severity,
    _output_section_header,
    _template_haystack,
)


# Lint 7: a source section with at least this many substantive lines whose
# output section is empty did not just "have nothing to say".
DEAD_SECTION_MIN_LINES = 3


# Lint 8: an entry is a fused multi-record candidate at this many record-like
# lines. _looks_like_record only sees pipe/tab rows; employment/appointment
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
    if is_template_instruction(normed):
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


_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _name_tokens(text: str) -> set[str]:
    stripped = _NAME_LABEL_RE.sub("", _norm(text))
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
        top = _norm((e.get("hierarchy") or ["(none)"])[0]) or "(none)"
        lines = sum(1 for line in str(e.get("text", "")).split("\n")
                    if len(_norm(line)) >= SUBSTANTIVE_LINE_CHARS)
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
            if is_template_instruction(line):
                continue
            if kind == "table":
                if _norm(line):
                    current[2] += 1
            elif len(_norm(line)) >= SUBSTANTIVE_LINE_CHARS:
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
            if _looks_like_record(line) or _is_date_record_line(line.strip())]


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


def lint_pipe_leaks(blocks: List[Tuple[str, str]]) -> List[Dict]:
    """Verbatim-fallback formatting reaching the output document: paragraphs
    carrying multiple raw ' | ' field separators, clusters of single-pipe
    bullets under one section, and numbered citations fusing several
    venue-date patterns (#208 rendered costs). Paragraph blocks only:
    _table_lines synthesizes ' | ' row joins by design. The appendix is
    excluded — it is verbatim-by-contract."""
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
                and len(_VENUE_DATE_RE.findall(line)) >= 2):
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


# What may remain of a name once its org is removed for the org to count as
# "fabricated from the name" (#889): only an award word and/or digits (a year).
# Stripped with sub() and tested for emptiness, never fullmatch() over a
# starred alternation -- that shape backtracks exponentially on runs of years.
_AWARD_ORG_LEFTOVER_RE = re.compile(
    r"award|prize|fellow(?:ship)?|scholarship|list"
    r"|\d+|[\s,.;:()\-\u2013\u2014/&]+",
    re.IGNORECASE)


def _org_fabricated_from_name(org: str, name: str) -> bool:
    """True when `name` is `org` plus nothing but an award word and/or a
    year -- the org column was copied out of the name (#229/#889). An award
    merely NAMED AFTER its grantor ("<org> Postdoc Travel Award") has other
    words left over and is legitimate."""
    org_n, name_n = _norm(org), _norm(name)
    if org_n not in name_n:
        return False
    leftover = name_n.replace(org_n, " ", 1)
    return not _AWARD_ORG_LEFTOVER_RE.sub("", leftover)


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


def _honors_table_shape(tbl: list[list[str]]) -> "_HonorsTableShape | None":
    """One table's honors-shape defects (#229), or None when it is not an
    honors/awards table at all (no 'name of award'/'date awarded' header).
    Factored out of `lint_table_shape` so the doctor's `metrics` block
    (#816's `honors_malformed_rows`/`honors_rows`) sums the SAME per-row
    predicate the finding below is built from, not a second definition of
    'malformed'."""
    if len(tbl) < 2 or not tbl[0]:
        return None
    header = [_norm(cell) for cell in tbl[0]]
    header_all = " ".join(header)
    if "name of award" not in header_all and "date awarded" not in header_all:
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


def lint_table_shape(tables: list[list[list[str]]]) -> list[dict]:
    """Honors-like tables whose rows are mis-shaped (#229): the stage-6
    multi-award fallback puts citation blobs in the name cell, leaks state
    abbreviations into the organization column, leaves the date column empty
    while the year sits in the name, and duplicates the organization inside
    the name."""
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


_PASSAGE_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def _passage_key(text) -> str:
    """Comparison key for one block: leading enumerator dropped from every
    line, punctuation folded, whitespace collapsed, casefolded. Empty for a
    blank spacer paragraph, which is then dropped from the sequence entirely --
    a spacer can neither match another spacer nor break a run."""
    lines = [_PASSAGE_ENUMERATOR_RE.sub("", line)
             for line in str(text or "").split("\n")]
    return " ".join(_PASSAGE_PUNCT_RE.sub(" ", _norm("\n".join(lines))).split())


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


def lint_duplicate_records(blocks: list[tuple[str, str]]) -> list[dict]:
    """A single numbered/bulleted paragraph block whose normalized body
    repeats at a different list position within DUPLICATE_RECORD_WINDOW
    enumerated blocks of its first occurrence, in the SAME output section
    (#446) -- the shape duplicate_passages cannot see because it requires
    >=2 consecutive repeated blocks, and 55.2% of substantive records in the
    corpus occupy exactly one.

    Section scope is tracked with `_output_section_header`; the match state
    is reset on every section change, so a publication legitimately listed
    under two different headings never fires -- that is the same record two
    sections chose to carry, not a duplicate. A blank spacer paragraph does
    not match the enumerator prefix, so it is skipped rather than consuming a
    window slot or breaking one, same as `lint_duplicate_passages`.
    """
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
        match = next((r for r in recent if r[0] == key), None)
        if match is not None:
            pairs.append((match[2], i))
        recent.append((key, enum_index, i))

    if len(pairs) < DUPLICATE_RECORD_WARN_COUNT:
        return []

    evidence = [f"block {first} repeats at {second}: "
                f"{str(blocks[first][1])[:100]}" for first, second in pairs[:5]]
    return [_finding(
        "duplicate_records", "WARN",
        f"{len(pairs)} duplicated record(s) — the same enumerated entry "
        f"appears twice within the same output section",
        evidence)]


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
