#!/usr/bin/env python3
"""
Stage 5c: Teaching/Educational Contributions Formatter

Uses an LLM to reformat K-code entries (Educational Contributions) into a
polished, highly readable format with consistent styling across entries.

Applies to:
- K1: Didactic teaching
- K2: Clinical teaching
- K3: Administrative teaching
- K4: Continuing education / professional development
- K5: Other education/outreach activities

Input: Stage 5b enriched JSON (or earlier stage output)
Output: *_teaching_formatted.json with reformatted K entries

Author: Scholar Signals CV Pipeline
Date: 2025-12-02
"""

import os
import sys
import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from datetime import datetime
from typing import Any

from unified_pipeline.llm_client import call_llm
from unified_pipeline.llm.retry import LLMOutageError
from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY

logger = logging.getLogger(__name__)

# Paths
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5c_teaching_formatted"

# Taxonomy codes for teaching/educational contributions
TEACHING_CODES = ['K1', 'K2', 'K3', 'K4', 'K5']

# Subsection labels in WCM template
K_SUBSECTION_LABELS = {
    'K1': 'Didactic teaching (lectures, seminars, tutorials)',
    'K2': 'Clinical teaching (bedside teaching, teaching rounds, precepting)',
    'K3': 'Administrative teaching (leadership role as director, course director)',
    'K4': 'Continuing education and professional education',
    'K5': 'Other education/outreach activities',
}

# The prompt's own example of one entry that lists several dated items, and
# the merged form it forbids (#1345, DPEHSZ 269-297: 49 dated lectures went
# out as one date list ahead of one title list). `postcheck_line` accepts the
# first and rejects the second (`date_not_beside_title`); the tests hold the
# prompt and the post-check to the same rule.
DATED_ITEMS_EXAMPLE = '- [EC-0124] **6/2012** - "Topic A"; **8/2012** - "Topic B" (School of Medicine; residents)'
MERGED_DATES_EXAMPLE = '- [EC-0124] **6/2012, 8/2012** - "Topic A"; "Topic B" (School of Medicine; residents)'

# The roles the model used to supply when an entry named none (#1345:
# 'Presenter' on DPEHSZ's talks, 'Attendee' on a workshop OIYKZE's owner
# gave). The prompt forbids each by name; they are the roles `_ROLE_EVIDENCE`
# below rejects in a line whose source does not state them.
DEFAULT_ROLES_FORBIDDEN = ('Presenter', 'Attendee', 'Participant')

# LLM prompt for reformatting educational contributions
EDUCATIONAL_CONTRIBUTIONS_PROMPT = '''You are a CV formatter. The input is raw, inconsistently formatted text that has ALREADY been mapped into subsections of a CV section called "Educational Contributions." Your job is to rewrite it into a polished, highly readable format.

NON-NEGOTIABLES
1) Output ONLY the formatted Markdown for this section. No commentary, no analysis.
2) Preserve meaning; DO NOT add facts or infer missing details.
3) Preserve EVERY entry ID EXACTLY as given (character-for-character). Never delete, reorder IDs, or generate new IDs.
4) These are NOT citations. Do not label anything as citations and do not add citation formatting.
5) Never write a role the entry does not state (see ROLE RULE).
6) Every date and year the input gives for an ID appears in that ID's line.

SECTION FRAME
- Use this exact top-level heading:
  # Educational Contributions
- Keep the provided subsection headings exactly as given in the input and in the same order.

PRIMARY GOAL
Maximize readability while maintaining strong consistency across entries.

CORE STRATEGY (important)
- Choose ONE "dominant" entry style per subsection to maximize consistency.
- Only fall back to an alternate style when an entry lacks the minimum fields to fit the dominant style.
- Within a subsection, keep punctuation, ordering of fields, and bold usage consistent.

ENTRY ID RULE (hard)
- Each entry must begin with its ID, formatted like:
  - `[ID] ` followed immediately by the entry content on the same line.
- The ID must be the first token on the bullet line.
- Example: `- [EC-0123] **2021-2022** - Faculty Advisor, SPRINGBOARD Program (Health Sciences)`

ONE ID, ONE RECORD (hard)
- Each ID's line describes only that ID's input. Never move a date, title or role from one ID's line to another's, and never put two IDs on one bullet.
- Consecutive IDs followed by one shared `Original:` line are separate records of one entry: give each its own bullet with its own date.
- An `Original:` line is the source text of the entry above it, given for context only. Never copy it into your output, not even as a sub-bullet.

DATED ITEMS (hard)
- When one entry lists several items with their own dates (talks, lectures, sessions), write each date immediately before its own title:
  ''' + DATED_ITEMS_EXAMPLE + '''
- Never gather the dates into one list ahead of the titles, as in:
  ''' + MERGED_DATES_EXAMPLE + '''
  The reader can no longer tell which date goes with which title.
- An item given more than once keeps every date, each beside its title.

ROLE RULE (hard)
- Write a role only when the entry's own text names one (e.g., Lecturer, Course Director, Moderator).
- Never supply a default role. Do not write ''' + ', '.join(DEFAULT_ROLES_FORBIDDEN) + ''' or any other role the entry does not state; an entry with no stated role gets no role.

FIELD EXTRACTION (use only what is present)
From each entry, extract any of the following if present:
- Dates (range or single)
- Activity/Course/Program/Event title
- Role, only when the entry states it (e.g., Lecturer, Mentor, Preceptor, Co-Director, Facilitator, Faculty Advisor)
- Audience (e.g., graduate students, 1st-year SOM, MD/PhD)
- Institution/Unit (e.g., EOH Graduate Program, GSPH, SOM)
- Quantities (hours, credits, number of sessions, number of students)
- Notes (e.g., article discussed, workshop topic)
If a field is missing, omit it. Never guess.

PUNCTUATION + TYPOGRAPHY RULES
- Prefer en dashes/em dashes for separation, in this hierarchy:
  1) Dates first, then em dash "-" to main content
  2) Use commas for compact lists inside a clause
  3) Use parentheses for secondary clarifiers (audience, institution, counts, notes)
- Bold:
  - Bold dates when present.
  - Bold true leadership roles only when they are the main point (e.g., **Co-Director**, **Director**).
- Quotes:
  - Use quotes ONLY for titles of articles/talks/session titles when explicitly present.
  - Do not introduce quotes if none exist.
- Line breaks:
  - Keep each entry to ONE line when possible.
  - If an entry is long (e.g., contains an article title + journal + year + multiple roles), use a second line with an indented sub-bullet for "Notes:" or "Article discussed:" rather than cramming.

SUB-BULLETS (allowed, but controlled)
Use sub-bullets only for:
- Very long "article discussed" details
- Multiple structured quantities (hours/credits/sessions/students)
- Lists of co-directors
When used:
- Keep the parent line readable and short.
- Use at most 2 sub-bullets per entry.

ALLOWED ENTRY TEMPLATES (choose per subsection)
Pick a dominant template PER subsection:

Template A (Dates available + teaching role)
- [ID] **[Dates]** - [Activity/Course/Program], [Role] ([Institution/Unit]; [Audience])

Template B (No dates, but role exists)
- [ID] [Activity/Course/Program] - [Role] ([Institution/Unit]; [Audience])

Template C (Administrative teaching with metrics)
- [ID] **[Dates]** - **[Leadership Role]**, [Course/Program]: [Title]
  - (Hours: [x]; Credits: [x]; Sessions: [x]; Learners: [x]; Audience: [x])
  - (Co-leads: [names])

Template D (Continuing education / professional development)
- [ID] **[Date]** - [Role, only if stated], [Event/Topic] ([Org/Location])

CONSISTENCY RULES (readability-first)
- Within each subsection:
  - Use the same ordering of parentheses content (Institution first, then Audience, then Notes).
  - Use the same separator style (prefer "-" between major parts).
  - Standardize role capitalization (Title Case).
  - Remove duplicated institution fragments while preserving the correct institution once.
  - Fix obvious typos (spelling/punctuation), but do not rewrite technical titles or proper nouns.

ORDERING
- Keep entries in the SAME order as provided unless the input explicitly provides dates for most entries in that subsection.
- If >=70% of entries in a subsection have dates, reorder that subsection reverse-chronologically by date.
- Otherwise, preserve original order.

INPUT FORMAT ASSUMPTIONS
The raw input will be provided with explicit subsection labels and each entry will include an entry ID token.
Do not invent subsection labels or entry IDs.

Now format the following raw content:

<<<RAW_CV
{raw_content}
RAW_CV>>>'''


# The id prefix this module gives every line it sends (`[EC-0001]`). The
# parser strips EVERY tag of this shape from a reply line, not only the
# leading one: the model sometimes repeats it (`[EC-0021] [EC-0021] ...`),
# and a tag left in the text renders on the page.
ENTRY_ID_PREFIX = 'EC'
_ENTRY_ID_TAG = re.compile(rf'\[{ENTRY_ID_PREFIX}-\d+\]\s*')

# Stage 4 keeps every record of a multi-record entry under STAGE4_RECORDS_KEY
# once the reply held this many (see stage4/schemas.py).
_MIN_STAGE4_RECORDS = 2


def entry_records(entry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The records one entry stands for, each of which gets its own id.

    An entry stage 4 kept as 2+ records (`stage4_records`) is one record per
    item, in stage 4's order; any other entry is the one record its
    `extracted_fields` hold. One id per entry used to let a fused entry's
    second line take the NEXT entry's id, shifting every later id by one, and
    dropped every record but the last from the formatted text (E20).

    A multi-record entry stays ONE record when a record's line would be empty
    or two records' lines are the same: each such id would carry the entry's
    whole text (or a copy of another id's line), and the joined reply would
    render the entry twice.
    """
    fields = entry.get('extracted_fields') or {}
    records = fields.get(STAGE4_RECORDS_KEY)
    if not (isinstance(records, list) and len(records) >= _MIN_STAGE4_RECORDS
            and all(isinstance(r, Mapping) and r for r in records)):
        return [fields]
    lines = [record_line(record) for record in records]
    if not all(lines) or len(set(lines)) < len(lines):
        return [fields]
    return records


def _first_text(fields: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    """The first of `keys` whose value in `fields` is a non-empty string."""
    for key in keys:
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ''


# What one record of a multi-record entry sends beyond a single-record line
# (the K1 fields): the K2-K5 schemas keep the title in program_name (K3) or
# activity_title (K4/K5), the role in teaching_role (K2) and a one-off date
# in `date` (K4/K5). Without them a K4 record's line was empty.
_RECORD_DATE_KEYS = ('date',)
_RECORD_TITLE_KEYS = ('program_name', 'activity_title')
_RECORD_ROLE_KEYS = ('teaching_role',)


def _record_parts(fields: Mapping[str, Any], per_record: bool = False) -> list[str]:
    """The structured fields of one record, in the order the prompt sends them.

    `per_record` adds the K2-K5 date, title and role fields for one record of
    a multi-record entry, which carries no `Original:` line of its own. A
    single-record entry sends only the K1 fields, as it always has: its
    `Original:` line already holds the rest.
    """
    parts = []
    start_date = fields.get('start_date', '')
    end_date = fields.get('end_date', '')
    if start_date or end_date:
        date_str = f"{start_date or ''}-{end_date or ''}".strip('-')
        if date_str:
            parts.append(date_str)
    elif per_record and _first_text(fields, _RECORD_DATE_KEYS):
        parts.append(_first_text(fields, _RECORD_DATE_KEYS))

    course_code = fields.get('course_code', '')
    course_title = fields.get('course_title', '')
    if course_code and course_title:
        parts.append(f"{course_code}: {course_title}")
    elif course_title:
        parts.append(course_title)
    elif course_code:
        parts.append(course_code)
    elif per_record and _first_text(fields, _RECORD_TITLE_KEYS):
        parts.append(_first_text(fields, _RECORD_TITLE_KEYS))

    if fields.get('role'):
        parts.append(fields['role'])
    elif per_record and _first_text(fields, _RECORD_ROLE_KEYS):
        parts.append(_first_text(fields, _RECORD_ROLE_KEYS))
    for key in ('institution', 'audience'):
        if fields.get(key):
            parts.append(fields[key])
    return parts


def record_line(record: Mapping[str, Any]) -> str:
    """The line one record of a multi-record entry sends to the model."""
    return ' | '.join(_record_parts(record, per_record=True))


def build_raw_content(entries_by_k_code: dict[str, list[dict]]) -> tuple[str, dict[str, dict]]:
    """
    Build raw content string for LLM prompt and a mapping of entry IDs to entries.

    Every record of an entry (`entry_records`) gets its own id; the ids of one
    entry are consecutive and all map to that entry. The entry's original text
    follows its last record's line when it differs from what the fields say.

    Returns:
        Tuple of (raw_content_string, id_to_entry_mapping)
    """
    lines = []
    id_to_entry = {}
    entry_counter = 0

    for k_code in TEACHING_CODES:
        entries = entries_by_k_code.get(k_code, [])
        if not entries:
            continue

        # Add subsection header
        subsection_label = K_SUBSECTION_LABELS.get(k_code, k_code)
        lines.append(f"\n## {subsection_label}\n")

        for entry in entries:
            raw_text = entry.get('text', '')
            records = entry_records(entry)
            if len(records) == 1:
                record_texts = [' | '.join(_record_parts(records[0]))]
            else:
                record_texts = [record_line(record) for record in records]
            for i, record_text in enumerate(record_texts):
                entry_counter += 1
                entry_id = f"{ENTRY_ID_PREFIX}-{entry_counter:04d}"
                id_to_entry[entry_id] = entry
                # Fall back to raw text if no structured fields
                entry_text = record_text or raw_text
                # Include original text as context if different, once per entry
                is_last = i == len(record_texts) - 1
                if is_last and record_text and raw_text and raw_text.strip() != record_text.strip():
                    entry_text = f"{entry_text}\n  Original: {raw_text}"
                lines.append(f"[{entry_id}] {entry_text}")

    return '\n'.join(lines), id_to_entry


def parse_llm_output(llm_output: str, id_to_entry: dict[str, dict]) -> dict[str, str]:
    """
    Parse LLM formatted output and extract formatted text per entry ID.

    Every `[EC-NNNN]` tag left in a line's content (a repeated id) is removed.

    Returns:
        Dict mapping entry_id -> formatted_text
    """
    id_to_formatted = {}

    # Find all entries with their IDs
    # Pattern: - [EC-XXXX] formatted content (possibly multi-line with sub-bullets)
    pattern = r'-\s*\[([A-Z]+-\d+)\]\s*(.+?)(?=\n-\s*\[|\n##|\n#|\Z)'

    matches = re.findall(pattern, llm_output, re.DOTALL)

    for entry_id, content in matches:
        # Handle sub-bullets (preserve them as part of the content)
        id_to_formatted[entry_id] = _ENTRY_ID_TAG.sub('', content).strip()

    return id_to_formatted


# ---------------------------------------------------------------------------
# Post-check (E20): a formatted line that misstates its record is not used.
# The entry then renders as it would without stage 5c (stage 6's raw-line or
# field rendering), which is the stage-4 content unrewritten.
# ---------------------------------------------------------------------------

# The fields a record's dates are kept in, across the K schemas.
_DATE_FIELDS = ('start_date', 'end_date', 'date', 'date_start', 'date_end')
# Stage 4's per-activity list on a K4/K5 entry (one dict per dated talk).
_ACTIVITIES_KEY = 'activities'
_ACTIVITY_TITLE = 'activity_title'
_ACTIVITY_DATE = 'date'

_YEAR = re.compile(r'(?<!\d)(?:19|20)\d{2}(?!\d)')
_MONTHS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
# One date as written in a line: `8/2008`, `Aug 2008` / `August, 2008`,
# `2008-08`, or a bare year. Longest forms first, so `8/2008` is one token.
_DATE_TOKEN = re.compile(
    r'(?<!\d)(?P<num_month>1[0-2]|0?[1-9])/(?P<y1>(?:19|20)\d{2})(?!\d)'
    r'|\b(?P<name_month>' + '|'.join(_MONTHS) + r')[a-z]*\.?,?\s+(?P<y2>(?:19|20)\d{2})(?!\d)'
    r'|(?<!\d)(?P<y3>(?:19|20)\d{2})-(?P<iso_month>0[1-9]|1[0-2])(?!\d)'
    r'|(?<!\d)(?P<y4>(?:19|20)\d{2})(?!\d)',
    re.IGNORECASE)

# The roles the prompt's templates hand the model for a talk or a course
# (Template D's "Role: Attendee/Participant", and the speaker it presumes),
# each mapped to what the record's own text or fields must hold for the role
# to be the record's. Each says WHO did the activity, so a wrong one turns a
# talk given into one attended, or the reverse: 'Presenter' on DPEHSZ's
# untitled-role talks and 'Attendee' on a workshop OIYKZE's owner gave.
# `present` alone is not evidence: it is the open end of a date range.
_ROLE_EVIDENCE = {
    'attendee': r'\battend',
    'participant': r'\bparticip',
    'presenter': r'(?:\b|co-?)present(?:er|ed|ing|ation)',
}
_ROLE_WORD = re.compile(r'\b(' + '|'.join(_ROLE_EVIDENCE) + r')s?\b', re.IGNORECASE)
# A sub-bullet echoing the `Original:` context line build_raw_content sends.
_ORIGINAL_ECHO = re.compile(r'(?im)^\s*(?:[-*]\s*)?original\s*:')
_MARKDOWN = re.compile(r'[*_`]')
_QUOTES = str.maketrans({'\u201c': '"', '\u201d': '"', '\u2018': "'", '\u2019': "'"})


def _normalized(text: str) -> str:
    """Lower case, straight quotes, no markdown, single spaces."""
    return ' '.join(_MARKDOWN.sub('', str(text).translate(_QUOTES)).lower().split())


def _strings(value: object) -> list[str]:
    """Every string inside a field value (lists and dicts walked)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _record_dates(record: Mapping[str, Any]) -> list[str]:
    """The record's date values, its activities' dates included."""
    dates = [str(record[k]) for k in _DATE_FIELDS if record.get(k)]
    activities = record.get(_ACTIVITIES_KEY)
    if isinstance(activities, list):
        dates += [str(a[_ACTIVITY_DATE]) for a in activities
                  if isinstance(a, Mapping) and a.get(_ACTIVITY_DATE)]
    return dates


_YEAR_RANGE = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*[-\u2013\u2014]\s*((?:19|20)\d{2})(?!\d)')


def _years_shown(text: str) -> set[str]:
    """Every year `text` names, with the years inside a `1986-1989` range:
    a range stands for the years it spans."""
    years = set(_YEAR.findall(text))
    for start, end in _YEAR_RANGE.findall(text):
        years.update(str(y) for y in range(int(start), int(end) + 1))
    return years


def _date_key(match: re.Match[str]) -> tuple[str, int | None]:
    """(year, month or None) of one `_DATE_TOKEN` match."""
    year = match['y1'] or match['y2'] or match['y3'] or match['y4']
    if match['num_month']:
        return year, int(match['num_month'])
    if match['name_month']:
        return year, _MONTHS.index(match['name_month'][:3].lower()) + 1
    if match['iso_month']:
        return year, int(match['iso_month'])
    return year, None


def _activities(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The record's dated, titled activities: its `activities` list, or the
    record itself when it carries one title and one date."""
    items = record.get(_ACTIVITIES_KEY)
    if not isinstance(items, list):
        items = [record]
    return [a for a in items if isinstance(a, Mapping)
            and isinstance(a.get(_ACTIVITY_TITLE), str) and a.get(_ACTIVITY_TITLE).strip()
            and a.get(_ACTIVITY_DATE)]


def _date_beside_title(line: str, activity: Mapping[str, Any]) -> bool:
    """False when the line holds the activity's title and a date of its own
    but another date sits between them (`8/2008, 9/2008 - "A"; "B"`). A
    title the model reworded is not found and not judged."""
    title = _normalized(activity[_ACTIVITY_TITLE])
    start = line.find(title)
    if start < 0:
        return True
    end = start + len(title)
    wanted = [_date_key(m) for m in _DATE_TOKEN.finditer(str(activity[_ACTIVITY_DATE]))]
    tokens = [(m.start(), m.end(), _date_key(m)) for m in _DATE_TOKEN.finditer(line)]

    def matches(key: tuple[str, int | None]) -> bool:
        return any(key[0] == w[0] and (key[1] is None or w[1] is None or key[1] == w[1])
                   for w in wanted)

    for t_start, t_end, key in tokens:
        if not matches(key):
            continue
        lo, hi = (t_end, start) if t_end <= start else (end, t_start)
        if not any(lo <= o_start and o_end <= hi and not matches(o_key)
                   for o_start, o_end, o_key in tokens):
            return True
    return not any(matches(key) for _, _, key in tokens)


def postcheck_line(line: str, record: Mapping[str, Any], entry_text: str) -> str | None:
    """Why stage 5c's `line` for `record` must not be used, or None.

    Deterministic; `entry_text` is the entry's source text. Rejects a line
    that echoes the `Original:` context line, drops a year the record's date
    fields hold (XWNZWW-06), holds a year neither the record nor the source
    text holds (a line shifted onto the wrong id, EQGGRB-06), separates an
    activity's date from its title (DPEHSZ-02), or names a role the record
    and its source text do not (DPEHSZ-02, OIYKZE-03).
    """
    if _ORIGINAL_ECHO.search(line):
        return 'original_echo'
    text = _normalized(line)
    source_text = _normalized(entry_text or '')
    shown = _years_shown(text)
    for year in sorted({y for d in _record_dates(record) for y in _YEAR.findall(d)}):
        # A year stage 4 read that the source does not hold (a misparsed
        # `1900`, a two-digit `79`) is not one the line can be held to.
        if year in source_text and year not in shown:
            return f'year_missing:{year}'
    source = _normalized(' '.join([entry_text or '', *_strings(record)]))
    for year in _YEAR.findall(text):
        if year not in source:
            return f'year_not_in_source:{year}'
    for activity in _activities(record):
        if not _date_beside_title(text, activity):
            return 'date_not_beside_title'
    for match in _ROLE_WORD.finditer(text):
        if not re.search(_ROLE_EVIDENCE[match[1].lower()], source):
            return f'role_invented:{match[1].lower()}'
    return None


# `accepted_formatted_text`'s reason prefix for an id the reply did not hold:
# not a rejection, the entry was simply not formatted (as before E20).
_MISSING = 'missing:'


def _ids_by_entry(id_to_entry: Mapping[str, dict]) -> list[tuple[dict, list[str]]]:
    """`(entry, its ids in order)` per entry, in id order. An entry's ids
    are consecutive (`build_raw_content`), so a change of entry starts a group."""
    groups: list[tuple[dict, list[str]]] = []
    for entry_id, entry in id_to_entry.items():
        if groups and groups[-1][0] is entry:
            groups[-1][1].append(entry_id)
        else:
            groups.append((entry, [entry_id]))
    return groups


def accepted_formatted_text(entry: Mapping[str, Any], entry_ids: list[str],
                            id_to_formatted: Mapping[str, str]) -> tuple[str | None, str | None]:
    """`(formatted_text, None)` for an entry whose every record's line came
    back and passes `postcheck_line`, else `(None, reason)`.

    The lines of a multi-record entry are joined one per line, in record
    order. One missing or rejected line rejects the entry: stage 6 then
    renders all its records from stage 4 rather than some formatted and the
    rest lost.
    """
    lines = []
    for entry_id, record in zip(entry_ids, entry_records(entry)):
        line = id_to_formatted.get(entry_id)
        if not line:
            return None, f'{_MISSING}{entry_id}'
        reason = postcheck_line(line, record, entry.get('text', '') or '')
        if reason:
            return None, f'{entry_id}:{reason}'
        lines.append(line)
    return '\n'.join(lines), None


def call_llm_formatter(raw_content: str, verbose: bool = True) -> tuple:
    """
    Call LLM to reformat the educational contributions.

    Args:
        raw_content: Raw content string with entry IDs
        verbose: Whether to print progress

    Returns:
        Tuple of (formatted_text, usage_dict) or (None, None) if failed
    """
    try:
        prompt = EDUCATIONAL_CONTRIBUTIONS_PROMPT.format(raw_content=raw_content)
        messages = [{"role": "user", "content": prompt}]

        if verbose:
            print(f"  Calling LLM for educational contributions formatting...")

        llm_result = call_llm(
            stage="stage_5c",
            messages=messages,
            temperature=0.3
        )

        result_text = llm_result["content"]

        usage = {
            'prompt_tokens': llm_result["prompt_tokens"],
            'completion_tokens': llm_result["completion_tokens"],
            'total_tokens': llm_result["total_tokens"],
            'cache_read_tokens': llm_result.get("cache_read_tokens", 0),
            'cache_write_tokens': llm_result.get("cache_write_tokens", 0),
            'cost': llm_result.get("cost", 0.0),
            # What actually served the call (#459).
            'model': llm_result.get("model"),
        }

        return result_text, usage

    except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
        raise
    except Exception as e:
        if verbose:
            print(f"  Warning: LLM formatting failed: {e}")
        return None, None


def run_stage_5c(input_path: str, output_path: str | None = None,
                 verbose: bool = True) -> str:
    """
    Run Stage 5c: Teaching/Educational Contributions Formatter.

    Args:
        input_path: Path to input JSON (Stage 5b or earlier)
        output_path: Optional output path
        verbose: Whether to print progress

    Returns:
        Path to output file
    """
    # Load input data
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    document_uid = data.get('document_uid', 'unknown')
    entries = data.get('entries', [])

    if verbose:
        print(f"\n{'='*60}")
        print(f"Stage 5c: Teaching/Educational Contributions Formatter")
        print(f"{'='*60}")
        print(f"Document: {document_uid}")

    # Group entries by K-code
    entries_by_k_code: dict[str, list[dict]] = {}
    for entry in entries:
        code = entry.get('taxonomy_code', '')
        if code in TEACHING_CODES:
            if code not in entries_by_k_code:
                entries_by_k_code[code] = []
            entries_by_k_code[code].append(entry)

    total_k_entries = sum(len(v) for v in entries_by_k_code.values())

    if total_k_entries == 0:
        if verbose:
            print("  No K-code entries found, nothing to format")
        # Just copy input to output
        if not output_path:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / f"{document_uid}_teaching_formatted.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return str(output_path)

    if verbose:
        print(f"  Found {total_k_entries} K-code entries across {len(entries_by_k_code)} subsections")
        for k_code, k_entries in entries_by_k_code.items():
            print(f"    {k_code}: {len(k_entries)} entries")

    # Build raw content for LLM
    raw_content, id_to_entry = build_raw_content(entries_by_k_code)

    # Call LLM for formatting
    llm_output, usage = call_llm_formatter(raw_content, verbose=verbose)

    # Copy all data forward and update K-code entries with formatting
    # Each stage output is self-contained with complete state
    entries_formatted_count = 0
    rejected: list[dict[str, Any]] = []
    total_cost = 0.0

    # llm_result['cost'] is the per-provider, per-model cost from
    # calculate_cost(), including Bedrock prompt-cache discounts when
    # caching is on. Don't recompute it from a hardcoded $/M-token figure.
    if usage:
        total_cost = usage.get('cost', 0.0)

    if llm_output:
        # Parse LLM output
        id_to_formatted = parse_llm_output(llm_output, id_to_entry)

        if verbose:
            print(f"  Parsed {len(id_to_formatted)} formatted entries from LLM output")

        # Update K-code entries in place with formatted text
        for entry, entry_ids in _ids_by_entry(id_to_entry):
            formatted_text, reason = accepted_formatted_text(entry, entry_ids, id_to_formatted)
            if formatted_text is None:
                if not reason.startswith(_MISSING):
                    rejected.append({'element_idx_start': entry.get('element_idx_start'),
                                     'taxonomy_code': entry.get('taxonomy_code'),
                                     'reason': reason})
                continue

            # Store formatted text in extracted_fields
            if 'extracted_fields' not in entry:
                entry['extracted_fields'] = {}
            entry['extracted_fields']['formatted_text'] = formatted_text
            entry['extracted_fields']['formatting_source'] = 'stage_5c_llm'
            entries_formatted_count += 1
        for item in rejected:
            logger.warning("stage 5c line rejected, entry renders unformatted: %s %s (%s)",
                           item['taxonomy_code'], item['element_idx_start'], item['reason'])
    else:
        if verbose:
            print("  LLM formatting not available, keeping original text")

    # Add stage metadata to the full data copy
    data['stage_5c'] = {
        'stage': '5c',
        'stage_name': 'Teaching/Educational Contributions Formatter',
        'input_file': input_path,
        'k_entries_processed': total_k_entries,
        'entries_formatted': entries_formatted_count,
        # Entries whose formatted line failed `postcheck_line` (E20); each
        # renders from its stage-4 fields and text instead.
        'entries_rejected': rejected,
        'model': usage.get('model') if usage else None,
        'timestamp': datetime.now().isoformat(),
        'total_cost': total_cost,
        'prompt_tokens': usage.get('prompt_tokens', 0) if usage else 0,
        'completion_tokens': usage.get('completion_tokens', 0) if usage else 0,
        'total_tokens': usage.get('total_tokens', 0) if usage else 0,
        'cache_read_tokens': usage.get('cache_read_tokens', 0) if usage else 0,
        'cache_write_tokens': usage.get('cache_write_tokens', 0) if usage else 0,
    }

    # Write output - full copy with K-code entries updated
    if not output_path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{document_uid}_teaching_formatted.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n  Output: {output_path}")

    return str(output_path)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 5c: Teaching/Educational Contributions Formatter"
    )
    parser.add_argument('input_path', help='Path to input JSON file')
    parser.add_argument('-o', '--output', help='Output path (optional)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Quiet mode (minimal output)')

    args = parser.parse_args()

    output = run_stage_5c(
        args.input_path,
        output_path=args.output,
        verbose=not args.quiet
    )

    print(f"\nStage 5c complete: {output}")


if __name__ == '__main__':
    main()
