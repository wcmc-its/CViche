"""Lints for what the stage-5 formatters wrote over stage 4's records.

One responsibility: compare a formatter's rewritten line with the stage-4
record it stands for, and report where the line misstates or drops it. Stage
6 renders the formatted line instead of the record, so a misstatement here
reaches the document.

`teaching_postcheck` (EBYSBC E20) re-applies stage 5c's own deterministic
post-check (`stage_5c_teaching_formatter.postcheck_line`, #1349) to every
teaching line that renders, and reads the rejections 5c records in its
artifact. It does not re-implement the post-check: the checks are imported.

`citation_grounding` (#1570) applies stage 5d's grounding check
(`stage_5d_citation_formatter.ungrounded_reason`) to every citation 5d
wrote: an author, an initial or an ordinal the entry's source line lacks.
"""
import re
from collections.abc import Mapping
from typing import Any

from unified_pipeline.stage_5c_teaching_formatter import (
    _ENTRY_ID_TAG,
    _ROLE_EVIDENCE,
    _YEAR,
    TEACHING_CODES,
    _normalized,
    _record_dates,
    _years_shown,
    entry_records,
    postcheck_line,
)
from unified_pipeline.stage_5d_citation_formatter import (
    citation_owner,
    ungrounded_reason,
)

from ..shared import _finding

# What stage 5c writes onto an entry it formatted, and the metadata block it
# adds to its artifact (stage 5d copies both forward).
_FORMATTED_TEXT_KEY = "formatted_text"
_FORMATTING_SOURCE_KEY = "formatting_source"
_STAGE_5C_SOURCE = "stage_5c_llm"
_STAGE_5C_META_KEY = "stage_5c"
_REJECTED_KEY = "entries_rejected"
# What stage 5d writes onto a publication it formatted, and where the
# artifact names the CV owner.
_FORMATTED_CITATION_KEY = "formatted_citation"
_STAGE_5D_SOURCE = "stage_5d_llm"
_CV_OWNER_KEY = "cv_owner"

# Post-check reasons are "<shape>" or "<shape>:<detail>".
_REASON_SEPARATOR = ":"
_ROLE_INVENTED = "role_invented"
# The shapes this lint adds to the post-check's own.
SHAPE_RECORD_UNFORMATTED = "record_unformatted"
SHAPE_ID_TAG = "id_tag"

# A role word with no evidence in the entry is often the right role anyway
# (a talk to residents read as 'Presenter'), so it is reported at INFO; the
# other shapes drop or misplace a fact the record holds, and are WARN.
_INFO_SHAPES = frozenset({_ROLE_INVENTED})

# The title-like fields of one teaching record, across the K schemas.
_RECORD_TITLE_KEYS = ("course_title", "program_name", "activity_title", "teaching_role")
# A record of a multi-record entry counts as formatted when at least this
# share of its title words is in the formatted text.
_TITLE_WORDS_SHOWN_MIN = 0.5
_TITLE_WORD_MIN_CHARS = 4
_TITLE_WORD_RE = re.compile(rf"[^\W\d_]{{{_TITLE_WORD_MIN_CHARS},}}")
_EVIDENCE_CHARS = 120


def _top_level_lines(text: str) -> list[str]:
    """The formatted text's record lines: 5c joins one line per record, and a
    line's own sub-bullets are indented under it."""
    return [line for line in text.split("\n") if line.strip() and not line[0].isspace()]


def _unformatted_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The entry's fields as 5c's post-check saw them, before 5c added its
    own text: otherwise every year of the line is 'in the record'."""
    return {k: v for k, v in fields.items()
            if k not in (_FORMATTED_TEXT_KEY, _FORMATTING_SOURCE_KEY)}


def _record_missing(text: str, record: Mapping[str, Any], entry_text: str) -> bool:
    """Whether one record of a multi-record entry is absent from the
    formatted text: a year its dates hold (and the source states) is not
    shown, or fewer than half of its title words are."""
    shown = _years_shown(text)
    for date in _record_dates(record):
        if any(year in entry_text and year not in shown for year in _YEAR.findall(date)):
            return True
    title = " ".join(str(record.get(key) or "") for key in _RECORD_TITLE_KEYS)
    words = set(_TITLE_WORD_RE.findall(_normalized(title)))
    if not words:
        return False
    present = sum(1 for word in words if word in text)
    return present / len(words) < _TITLE_WORDS_SHOWN_MIN


def _role_in_headings(reason: str, entry: Mapping[str, Any]) -> bool:
    """Whether a role the post-check calls invented is named by the entry's
    section headings ('Courses Attended' for an 'Attendee' line). The
    post-check reads the entry's text and fields only."""
    role = reason.partition(_REASON_SEPARATOR)[2]
    headings = _normalized(" ".join(str(h) for h in entry.get("hierarchy") or []))
    return bool(role in _ROLE_EVIDENCE and re.search(_ROLE_EVIDENCE[role], headings))


def _formatted_line_reasons(entry: Mapping[str, Any]) -> list[str]:
    """Why the rendered 5c text of one teaching entry misstates its stage-4
    records: the post-check's own reasons per record line, plus a record the
    text leaves out and an id tag left in it."""
    fields = entry.get("extracted_fields") or {}
    formatted = str(fields.get(_FORMATTED_TEXT_KEY) or "")
    entry_text = str(entry.get("text") or "")
    records = entry_records({**entry, "extracted_fields": _unformatted_fields(fields)})
    reasons = []
    if _ENTRY_ID_TAG.search(formatted):
        reasons.append(SHAPE_ID_TAG)
    lines = _top_level_lines(formatted)
    if len(records) == 1:
        pairs = [(formatted, records[0])]
    elif len(lines) == len(records):
        pairs = list(zip(lines, records))
    else:
        pairs = []
        normalized = _normalized(formatted)
        missing = [i for i, record in enumerate(records)
                   if _record_missing(normalized, record, entry_text)]
        if missing:
            reasons.append(f"{SHAPE_RECORD_UNFORMATTED}{_REASON_SEPARATOR}"
                           f"{len(missing)} of {len(records)}")
    for line, record in pairs:
        reason = postcheck_line(line, record, entry_text)
        if reason and not (reason.startswith(_ROLE_INVENTED) and _role_in_headings(reason, entry)):
            reasons.append(reason)
    return reasons


def _rejection_findings(stage_5d: Mapping[str, Any]) -> list[dict]:
    """INFO per line stage 5c's post-check rejected: the entry renders from
    its stage-4 fields instead, unformatted."""
    meta = stage_5d.get(_STAGE_5C_META_KEY)
    rejected = (meta.get(_REJECTED_KEY) or []) if isinstance(meta, Mapping) else []
    return [_finding(
        "teaching_postcheck", "INFO",
        f"entry {item.get('element_idx_start')} ({item.get('taxonomy_code')}): "
        f"stage 5c rejected its formatted line ({item.get('reason')}); the entry "
        f"renders from its stage-4 fields, unformatted")
        for item in rejected if isinstance(item, Mapping)]


def lint_teaching_postcheck(stage_5d: dict) -> list[dict]:
    """Teaching lines stage 5c wrote that misstate their stage-4 records
    (EBYSBC E20): a date apart from its title, a year dropped or not in the
    source, a role nothing names, an echoed 'Original:' line, a record left
    out of a multi-record entry, an id tag left in. One finding per entry,
    INFO when the only reason is a role, WARN otherwise. Lines 5c's post-check
    (#1349) already rejected are reported at INFO from its own record."""
    findings = _rejection_findings(stage_5d)
    for entry in stage_5d.get("entries") or []:
        fields = entry.get("extracted_fields") or {}
        if (entry.get("taxonomy_code") not in TEACHING_CODES
                or fields.get(_FORMATTING_SOURCE_KEY) != _STAGE_5C_SOURCE
                or not fields.get(_FORMATTED_TEXT_KEY)):
            continue
        reasons = _formatted_line_reasons(entry)
        if not reasons:
            continue
        shapes = {reason.partition(_REASON_SEPARATOR)[0] for reason in reasons}
        findings.append(_finding(
            "teaching_postcheck", "INFO" if shapes <= _INFO_SHAPES else "WARN",
            f"entry {entry.get('element_idx_start')} ({entry.get('taxonomy_code')}): "
            f"{', '.join(reasons)} -- the stage 5c line that renders misstates "
            f"its stage-4 record",
            [str(fields[_FORMATTED_TEXT_KEY])[:_EVIDENCE_CHARS]]))
    return findings


def lint_citation_grounding(stage_5d: dict) -> list[dict]:
    """Stage-5d citations that name an author, an initial or an ordinal the
    entry's source line lacks (#1570, YUYVIG FLBFRK 25, SIJYJZ 732, QQGKXR
    481 and 483): an initial given to an author the source gives none, a
    bare initials token joined to the next surname, an author the line
    does not name, a CV list number "10." printed as "10th Annual Meeting".
    One INFO finding per citation, naming the first author or ordinal that
    fails. INFO, not WARN: about half the hits are such an invention; the
    rest are mostly 5d spelling out a name the source misspells or garbles,
    or an entry whose source line is a fragment of a split citation
    (`doctor/PRECISION.md`, YUY-CG). So the run page never shows it and the
    score never counts it: the review copy comments the citation as a
    possibility to check (`review_comments`, REVIEW_COPY_ONLY_LINTS)."""
    owner = citation_owner(stage_5d.get(_CV_OWNER_KEY))
    findings = []
    for entry in stage_5d.get("entries") or []:
        fields = entry.get("extracted_fields") or {}
        citation = fields.get(_FORMATTED_CITATION_KEY)
        if fields.get(_FORMATTING_SOURCE_KEY) != _STAGE_5D_SOURCE or not isinstance(citation, str):
            continue
        reason = ungrounded_reason(citation, str(entry.get("text") or ""), owner)
        if reason:
            findings.append(_finding(
                "citation_grounding", "INFO",
                f"entry {entry.get('element_idx_start')} ({entry.get('taxonomy_code')}): "
                f"{reason} -- the stage 5d citation names text its source line lacks",
                [citation[:_EVIDENCE_CHARS]]))
    return findings
