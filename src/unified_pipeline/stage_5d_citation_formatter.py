#!/usr/bin/env python3
"""
Stage 5d: Citation Formatter for Non-Enriched Publications

Uses an LLM to reformat S-code entries (publications) that could not be enriched
via PubMed into proper Vancouver citation format.

This stage:
1. Identifies S-code entries with enrichment_status != 'enriched'
2. Sends the raw text to an LLM to extract and format as Vancouver citation
3. Stores the formatted citation in extracted_fields.formatted_citation

Input: Stage 5c output (or earlier stage output)
Output: *_citation_formatted.json with reformatted non-enriched citations

Author: Scholar Signals CV Pipeline
Date: 2025-12-02
"""

import difflib
import itertools
import json
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from unified_pipeline.core.batch_pool import (
    make_batches,
    make_progress_printer,
    map_in_order,
    workers_from_config,
)
from unified_pipeline.core.text_norm import is_placeholder_title
from unified_pipeline.llm.retry import LLMOutageError
from unified_pipeline.llm_client import call_llm

logger = logging.getLogger(__name__)

# Paths
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5d_citation_formatted"

# I/O-bound stage (LLM round trips, not CPU); default sized under the
# per-pod semaphore so one run cannot starve the others admitted alongside
# it (#881 step 6). Knob: CVICHE_STAGE5D_BATCH_WORKERS, env var or llm yaml
# key -- same resolution order and default as stages 2/3b/4.
STAGE5D_BATCH_WORKERS = workers_from_config("CVICHE_STAGE5D_BATCH_WORKERS")

# Publication codes that may need formatting
PUBLICATION_CODES = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']

# Publication type descriptions for LLM context
PUBLICATION_TYPE_DESCRIPTIONS = {
    'S1': 'Peer-reviewed research article (journal article)',
    'S2': 'Review article or editorial',
    'S3': 'Book (authored)',
    'S4': 'Book chapter (contributed chapter in edited volume)',
    'S5': 'Monograph',
    'S6': 'Letter or correspondence',
    'S7': 'Manuscript in review/submitted (unpublished)',
    'S8': 'Abstract or conference proceeding',
    'S9': 'Other publication (media coverage, podcast, etc.)',
}

# LLM prompt for reformatting citations
CITATION_FORMATTER_PROMPT = '''You are a citation formatter. Convert the raw citation text into proper Vancouver (biomedical) citation format.

PUBLICATION TYPES:
Each entry includes a taxonomy code indicating the publication type:
- S1: Peer-reviewed research article (journal article)
- S2: Review article or editorial
- S3: Book (authored)
- S4: Book chapter (contributed chapter in edited volume)
- S5: Monograph
- S6: Letter or correspondence
- S7: Manuscript in review/submitted (unpublished)
- S8: Abstract or conference proceeding
- S9: Other publication (media coverage, podcast, etc.)

VANCOUVER FORMAT RULES:
1. Authors: LastName INITIALS (no periods, no commas between last name and initials)
   - Example: Smith JA, Jones MB, Brown CK
   - List every author the source lists, in the source's order. Never shorten the list: write "et al." only where the source itself does
2. Title: Sentence case, ending with period
3. Journal/Book: Title case or official abbreviation
4. Year;Volume(Issue):Pages.
5. Identifiers: doi:xxx. PMID:xxx. PMCID:xxx.

FORMAT BY PUBLICATION TYPE:

FOR JOURNAL ARTICLES (S1, S2, S6):
- Format: Authors. Title. Journal. Year;Volume(Issue):Pages. doi:xxx.

FOR BOOKS (S3, S5):
- Format: Authors. Book Title. Edition. Location: Publisher; Year.

FOR BOOK CHAPTERS (S4):
- Format: Authors. Chapter title. In: Editors, eds. Book Title. Location: Publisher; Year:Pages.

FOR MANUSCRIPTS IN REVIEW (S7):
- Format: Authors. Title. Journal (if known). [Submitted/In review]. Year.

FOR ABSTRACTS/PROCEEDINGS (S8):
- Format: Authors. Abstract title. Conference Name; Year Month Day; Location.

FOR OTHER (S9 - media, podcasts, etc.):
- Format appropriately based on content (e.g., "Title. Publication/Outlet. Year.")

INPUT FORMAT:
You will receive entries with their taxonomy code (indicating publication type) and raw text.

OUTPUT FORMAT:
Return a JSON object with entry IDs mapped to formatted citations:
{{
  "CIT-0001": {{
    "authors": "Smith JA, Jones MB",
    "title": "Article title here",
    "journal": "Journal Name",
    "year": "2020",
    "volume": "45",
    "issue": "3",
    "pages": "123-145",
    "doi": "10.1234/example",
    "formatted_citation": "Smith JA, Jones MB. Article title here. Journal Name. 2020;45(3):123-145. doi:10.1234/example."
  }},
  "CIT-0002": {{ ... }}
}}

IMPORTANT:
- Extract ALL available fields from the raw text
- If a field is not present, omit it (don't guess)
- The formatted_citation should be the complete Vancouver-style citation
- Preserve author names exactly as they appear (don't invent initials)
- For book chapters, include "In:" before the book title

Now format these citations:

<<<RAW_CITATIONS
{raw_content}
RAW_CITATIONS>>>'''


def build_raw_content(entries: list[dict]) -> tuple[str, dict[str, dict]]:
    """
    Build raw content string for LLM prompt and a mapping of entry IDs to entries.

    Returns:
        Tuple of (raw_content_string, id_to_entry_mapping)
    """
    lines = []
    id_to_entry = {}

    for i, entry in enumerate(entries):
        entry_id = f"CIT-{i+1:04d}"
        id_to_entry[entry_id] = entry

        code = entry.get('taxonomy_code', 'S1')
        raw_text = entry.get('text', '')

        # Include publication type description for better LLM context
        type_desc = PUBLICATION_TYPE_DESCRIPTIONS.get(code, 'Publication')

        lines.append(f"[{entry_id}] ({code}: {type_desc})")
        lines.append(raw_text)
        lines.append("")

    return '\n'.join(lines), id_to_entry


def parse_llm_output(llm_output: str, id_to_entry: dict[str, dict]) -> dict[str, dict]:
    """
    Parse LLM JSON output and extract formatted citations per entry ID.

    Returns:
        Dict mapping entry_id -> parsed fields dict
    """
    id_to_formatted = {}

    if not llm_output:
        return id_to_formatted

    # Try to parse as JSON directly first (for json_object response format)
    try:
        parsed = json.loads(llm_output)
        for entry_id, fields in parsed.items():
            if entry_id in id_to_entry:
                id_to_formatted[entry_id] = fields
        return id_to_formatted
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from the response
    try:
        # Find JSON object in response
        json_match = re.search(r'\{[\s\S]*\}', llm_output)
        if json_match:
            parsed = json.loads(json_match.group())

            for entry_id, fields in parsed.items():
                if entry_id in id_to_entry:
                    id_to_formatted[entry_id] = fields
    except json.JSONDecodeError as e:
        # parse_llm_output can run on a pool thread (#881 step 6), so this
        # log call is the only trace a malformed batch leaves in the run log.
        logger.warning("Could not parse LLM JSON output: %s (response preview: %s...)", e, llm_output[:500])

    return id_to_formatted


# The extracted fields 5d copies onto an entry that lacks them.
_COPIED_FIELDS = ('authors', 'title', 'journal', 'year', 'volume', 'issue', 'pages', 'doi', 'book_title')
# Two or more periods with only spacing between: what removing a title leaves.
_REPEATED_PERIODS_RE = re.compile(r'(?:\s*\.){2,}')


def _without_placeholder(citation: str, placeholder: str) -> str:
    """`citation` with the placeholder title cut out, or '' when nothing is left."""
    cleaned = _REPEATED_PERIODS_RE.sub('.', citation.replace(placeholder, '')).strip(' .')
    return f"{cleaned}." if cleaned else ''


# #1570: the grounding check. Stage 6 prints a 5d citation in place of the
# CV's own line, so a citation naming an author, an initial or a meeting
# ordinal the line does not hold puts invented text in the document
# (YUYVIG: FLBFRK 25, SIJYJZ 732, QQGKXR 481/483). The doctor's
# `citation_grounding` lint applies it to every 5d citation that renders.
# Stage 5d does not yet reject on it: the stage-4 render a rejected entry
# would fall back to drops an S8 entry's meeting and place (#1570).

# One Vancouver author: surname, 1-4 capital initials, optional generational suffix.
_VANCOUVER_AUTHOR_RE = re.compile(
    r"^(?P<surname>[^\W\d_][^\d,;:.]*?)\s+(?P<initials>[A-Z]{1,4})"
    r"(?:\s+(?:Jr|Sr|II|III|IV|2nd|3rd|4th))?$")
_ET_AL_ITEM_RE = re.compile(r"^et\.?\s+al\.?$", re.IGNORECASE)
# The author segment ends at the first period followed by a space or the end.
_AUTHOR_SEGMENT_END_RE = re.compile(r"\.(?:\s|$)")
# Lowercase words a surname may hold ("de Quill", "van der Rook"). Any other
# lowercase word, or a capitalised acronym, means the segment is a title or
# a venue, not an author list, and the author check does not apply.
_SURNAME_PARTICLES = frozenset({
    'al', 'bin', 'da', 'de', 'del', 'della', 'den', 'der', 'di', 'dos', 'du',
    'e', 'la', 'le', 'ten', 'ter', 'van', 'von', 'y'})
_SURNAME_MAX_WORDS = 4
# Source tokens: letter runs, digit runs, and the separators that end a name.
# Periods, hyphens, quotes and brackets are not tokens, so "M.C. Rookery",
# "J-H" and "Quill (Sable)" read as plain words.
_SOURCE_TOKEN_RE = re.compile(r"[^\W\d_]+|\d+|[,;:&]")
_NAME_BREAK_WORDS = frozenset({'and'})
# A suffix the source may write between a surname and its initials ("Rook
# Jr., J.M."); it may stand for no initial at all.
_GENERATIONAL_SUFFIXES = frozenset({'jr', 'sr', 'ii', 'iii', 'iv'})
# Given names or initials read on each side of a surname.
_GIVEN_NAME_RUN_MAX = 4
# A source word this short may be initials written together ("ZQ", "BWCA"),
# or initials with a footnote or role marker of up to two letters after
# them ("AEf", "JTr", "MJRF", "PEJr").
_GLUED_INITIALS_MAX_LEN = 4
_INITIALS_MARKER_MAX_LEN = 2
# A number this short between a surname and its initials is an affiliation
# marker ("Quill1 R", "Rook 3S"), not part of the name.
_NAME_MARKER_MAX_DIGITS = 2
# Consecutive source words one surname may span: "Mc Rookery", "M.C. Rookery",
# "Quill Sable De Rook".
_SURNAME_MAX_SOURCE_WORDS = 5
# ponytail: a surname glued to the word beside it ("AnnaRook",
# "TBQuill") is matched by containment, and one the source holds only
# misspelt ("Sabel" for "Sable") by similarity -- the latter only when no
# exact or glued match exists, so an exact "Rook" is never read through a
# "Brook" beside it. Calibrated on YUYVIG and 238
# held-out runs (doctor/PRECISION.md, YUY-CG); if either ever admits an
# invented author, drop it rather than tune it.
_SURNAME_FUZZY_MIN_RATIO = 0.8
_SURNAME_LOOSE_MIN_LEN = 4
_ORDINAL_RE = re.compile(r"\b(\d+)\s*(?:st|nd|rd|th)\b", re.IGNORECASE)
_NEXT_WORD_RE = re.compile(r"\s*([^\W\d_]+)")
# How a source may write ordinal N, folded: with any ordinal suffix, misspelt
# or glued to the next word ("35rd", "21th", "106h", "58thAnnual"), as edition
# shorthand ("2ed", "2E"), or with a Spanish indicator ("2ª", "3º" fold to
# "2a", "3o").
_SOURCE_ORDINAL_TEMPLATE = r"(?<!\d){n}(?:st|nd|rd|th|h|\s+(?:st|nd|rd|th)\b|\s*(?:ed|edn)\b|[eao]\b)"
# An edition written after its label: "ed 6", "(ed. 5)", "Edition 2".
_SOURCE_EDITION_TEMPLATE = r"\b(?:ed|edn|edition)\.?\s*{n}(?!\d)"
_ORDINAL_UNITS = (
    '', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth',
    'tenth', 'eleventh', 'twelfth', 'thirteenth', 'fourteenth', 'fifteenth', 'sixteenth',
    'seventeenth', 'eighteenth', 'nineteenth')
_CARDINAL_TENS = ('', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')
_ORDINAL_TENS = ('', '', 'twentieth', 'thirtieth', 'fortieth', 'fiftieth', 'sixtieth',
                 'seventieth', 'eightieth', 'ninetieth')
# "Rook R 2nd" for the source's "R. Rook II".
_ORDINAL_ROMAN = {2: 'ii', 3: 'iii', 4: 'iv'}


def _fold(text: str) -> str:
    """`text` casefolded, with accents removed ("Quillé" -> "quille")."""
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _is_surname(text: str) -> bool:
    """Whether `text` reads as a surname rather than a title or a venue."""
    words = text.split()
    if len(words) > _SURNAME_MAX_WORDS:
        return False
    for word in words:
        core = word.strip('()')
        if not core:
            return False
        if core.casefold() in _SURNAME_PARTICLES:
            continue
        if not core[0].isupper() or (len(core) > 1 and core.isupper()):
            return False
    return True


def _vancouver_authors(citation: str) -> list[tuple[str, str]] | None:
    """`(surname, initials)` per author in `citation`'s author segment, or
    None when the segment is not a Vancouver author list (a citation that
    opens with its title, a group author, initials written with periods).
    A closing "et al." is not an author."""
    end = _AUTHOR_SEGMENT_END_RE.search(citation)
    if end is None:
        return None
    items = [item.strip() for item in citation[:end.start()].split(',')]
    if _ET_AL_ITEM_RE.match(items[-1]):
        items.pop()
    authors = []
    for item in items:
        match = _VANCOUVER_AUTHOR_RE.match(item)
        if match is None or not _is_surname(match['surname']):
            return None
        authors.append((match['surname'], match['initials']))
    return authors or None


class CitationOwner(NamedTuple):
    """The CV owner as a Vancouver author: folded surname, and the initials
    of their given names."""
    surname: str
    initials: str


def citation_owner(cv_owner: object) -> CitationOwner | None:
    """The owner from an artifact's `cv_owner` block, or None without a
    last name."""
    if not isinstance(cv_owner, dict) or not isinstance(cv_owner.get('last_name'), str):
        return None
    given = ' '.join(str(cv_owner.get(key) or '') for key in ('first_name', 'middle_name'))
    initials = ''.join(word[0] for word in _SOURCE_TOKEN_RE.findall(_fold(given)) if word[0].isalpha())
    surname = ''.join(_SOURCE_TOKEN_RE.findall(_fold(cv_owner['last_name'])))
    return CitationOwner(surname, initials) if surname else None


def _is_name_word(token: str) -> bool:
    return token[0].isalpha() and token not in _NAME_BREAK_WORDS


def _is_name_marker(token: str) -> bool:
    return token.isdigit() and len(token) <= _NAME_MARKER_MAX_DIGITS


def _initials_readings(words: list[str]) -> set[str]:
    """Every initials string `words` (given names or initials, in order) can
    stand for: each word gives its first letter, or, when short enough to be
    initials written together, all its letters (less a marker); a
    generational suffix may give nothing."""
    options = []
    for word in words:
        readings = {word[0]}
        if len(word) <= _GLUED_INITIALS_MAX_LEN:
            readings.update(word[:len(word) - cut] for cut in range(min(_INITIALS_MARKER_MAX_LEN, len(word) - 1) + 1))
        if word in _GENERATIONAL_SUFFIXES:
            readings.add('')
        options.append(readings)
    return {''.join(parts) for parts in itertools.product(*options)}


class _SurnameSpan(NamedTuple):
    """Where the source writes a surname: tokens `start` to `stop`, and the
    letters glued before or after it in the same word ("Anna" of
    "AnnaRook"), which are name words of their own."""
    start: int
    stop: int
    glued_before: str = ''
    glued_after: str = ''


def _surname_spans(surname: str, tokens: list[str]) -> list[_SurnameSpan]:
    """Where `tokens` spell `surname` (letters only, spaces ignored), or a
    word holds it glued to another; a misspelt match only when neither."""
    target = ''.join(_SOURCE_TOKEN_RE.findall(_fold(surname)))
    found, misspelt = [], []
    for start in range(len(tokens)):
        joined = ''
        for stop in range(start + 1, min(start + _SURNAME_MAX_SOURCE_WORDS, len(tokens)) + 1):
            if not _is_name_word(tokens[stop - 1]):
                break
            joined += tokens[stop - 1]
            if joined == target:
                found.append(_SurnameSpan(start, stop))
            elif len(target) < _SURNAME_LOOSE_MIN_LEN:
                continue
            elif stop == start + 1 and target in joined:
                before, _, after = joined.partition(target)
                found.append(_SurnameSpan(start, stop, before, after))
            elif difflib.SequenceMatcher(None, joined, target).ratio() >= _SURNAME_FUZZY_MIN_RATIO:
                misspelt.append(_SurnameSpan(start, stop))
    return found or misspelt


def _given_names_before(tokens: list[str], start: int) -> list[str]:
    """The name words just before `tokens[start]` ("ZQ Rook", "S, Rook")."""
    index = start - 1
    while index >= 0 and tokens[index] == ',':
        index -= 1
    run: list[str] = []
    while index >= 0 and _is_name_word(tokens[index]) and len(run) < _GIVEN_NAME_RUN_MAX:
        run.insert(0, tokens[index])
        index -= 1
    return run


def _given_names_after(tokens: list[str], stop: int) -> list[str]:
    """The name words just after a surname ending before `tokens[stop]`
    ("Rook, J.A.", "de Rook, C.,S.", "Rook, Jr, R", "Rook1 R")."""
    index = stop
    while index < len(tokens) and (tokens[index] == ',' or _is_name_marker(tokens[index])):
        index += 1
    run: list[str] = []
    while index < len(tokens) and len(run) < _GIVEN_NAME_RUN_MAX:
        token = tokens[index]
        if _is_name_word(token):
            run.append(token)
        elif not (token == ',' and run and _comma_inside_initials(run[-1], tokens, index)):
            break
        index += 1
    return run


def _comma_inside_initials(previous: str, tokens: list[str], comma: int) -> bool:
    """Whether the comma at `tokens[comma]` sits inside one author's initials:
    after a generational suffix, or between two single letters."""
    following = tokens[comma + 1] if comma + 1 < len(tokens) else ''
    return previous in _GENERATIONAL_SUFFIXES or (
        len(previous) == 1 and len(following) == 1 and following.isalpha())


def _author_reason(position: int, surname: str, initials: str, tokens: list[str],
                   owner: CitationOwner | None) -> str | None:
    """Why the source (`tokens`) does not ground author `position`, or None.

    Grounded means the source holds the surname, and holds `initials` in the
    given names or initials written next to it, on either side ("Rook ZQ"
    from "ZQ Rook", "Quill JA" from "Quill, John A."). Initials the source
    gives with fewer letters ("Sable SR" from "Sable, Sven"), or gives to
    a neighbouring author ("Dale BG" from "BG, H Dale"), are not.

    The CV owner is exempt: a line of the owner's own CV that names nobody
    ("Letter to the Editor, ...") is still the owner's, and 5d crediting
    them with their own initials invents nothing."""
    wanted = initials.casefold()
    folded = ''.join(_SOURCE_TOKEN_RE.findall(_fold(surname)))
    if owner and folded == owner.surname and owner.initials.startswith(wanted):
        return None
    spans = _surname_spans(surname, tokens)
    if not spans:
        return f'author_{position}:surname_not_in_source'
    for span in spans:
        before = _given_names_before(tokens, span.start) + [span.glued_before] * bool(span.glued_before)
        if any(wanted in _initials_readings(before[cut:]) for cut in range(len(before))):
            return None
        after = [span.glued_after] * bool(span.glued_after) + _given_names_after(tokens, span.stop)
        if any(wanted in _initials_readings(after[:cut]) for cut in range(1, len(after) + 1)):
            return None
    return f'author_{position}:initials_not_in_source'


def _ordinal_words(number: int) -> str | None:
    """`number` as an English ordinal ("tenth", "thirty fourth"), below 100."""
    if number < len(_ORDINAL_UNITS):
        return _ORDINAL_UNITS[number] or None
    if number >= 100:
        return None
    tens, units = divmod(number, 10)
    return _ORDINAL_TENS[tens] if units == 0 else f'{_CARDINAL_TENS[tens]} {_ORDINAL_UNITS[units]}'


def _ordinal_in_source(number: str, next_word: str, source: str) -> bool:
    """Whether `source` (folded) holds ordinal `number`: with a suffix
    (`_SOURCE_ORDINAL_TEMPLATE`), after an edition label, as a bare number
    before the word the citation puts after it ("124 Annual Meeting"), as
    words ("Third Edition"), or as a roman suffix ("Rook II"). "10. Annual
    Meeting" is none of these: there the number is the CV's list number."""
    patterns = [_SOURCE_ORDINAL_TEMPLATE.format(n=number), _SOURCE_EDITION_TEMPLATE.format(n=number)]
    if next_word:
        patterns.append(rf"(?<!\d){number}\s+{re.escape(next_word)}\b")
    if any(re.search(pattern, source) for pattern in patterns):
        return True
    spelled = _ordinal_words(int(number))
    if spelled and spelled.replace(' ', '') in re.sub(r'[^a-z]', '', source):  # "FirstEdition"
        return True
    roman = _ORDINAL_ROMAN.get(int(number))
    return bool(roman and f' {roman} ' in f" {' '.join(re.findall(r'[a-z]+', source))} ")


def ungrounded_reason(citation: str, source_text: str, owner: CitationOwner | None = None) -> str | None:
    """Why a stage-5d `citation` adds text its entry's `source_text` lacks,
    or None when it adds none that this checks (#1570).

    Two checks. Every ordinal ("10th", "34th") must be in the source: a CV
    list number "10." must not become "10th Annual Meeting", and a meeting
    the source does not number must not gain one. And when the citation
    opens with a Vancouver author list, every author but the CV `owner`
    must be grounded (`_author_reason`). A citation whose author segment
    does not parse as one is checked for ordinals only."""
    source = _fold(source_text)
    for match in _ORDINAL_RE.finditer(citation):
        following = _NEXT_WORD_RE.match(citation, match.end())
        if not _ordinal_in_source(match[1], _fold(following[1]) if following else '', source):
            return f'ordinal_not_in_source:{match[0]}'
    tokens = _SOURCE_TOKEN_RE.findall(source)
    for position, (surname, initials) in enumerate(_vancouver_authors(citation) or [], start=1):
        reason = _author_reason(position, surname, initials, tokens, owner)
        if reason:
            return reason
    return None


def apply_formatted_fields(entry: dict, formatted_data: dict) -> bool:
    """Write one LLM reply onto its entry; True when it stored a citation.

    The citation always; each extracted field only where the entry has none.
    A bracketed placeholder title the source does not carry ("[Title not
    provided]") is never written, and is cut out of the citation (#446:
    EBYSBC HFAJCC-05, XWNZWW-02): stage 6 then sees an entry with no title."""
    fields = entry.setdefault('extracted_fields', {})
    formatted_data = dict(formatted_data)
    title = formatted_data.get('title')
    citation = formatted_data.get('formatted_citation')
    if is_placeholder_title(title, entry.get('text') or ''):
        formatted_data.pop('title')
        if isinstance(citation, str):
            formatted_data['formatted_citation'] = _without_placeholder(citation, title.strip().rstrip('.'))
            if not formatted_data['formatted_citation']:
                formatted_data.pop('formatted_citation')
    stored = 'formatted_citation' in formatted_data
    if stored:
        fields['formatted_citation'] = formatted_data['formatted_citation']
        fields['formatting_source'] = 'stage_5d_llm'
    for field in _COPIED_FIELDS:
        if formatted_data.get(field):
            existing = fields.get(field, '')
            if not existing or existing == 'NONE':
                fields[field] = formatted_data[field]
    return stored


def call_llm_formatter(raw_content: str) -> tuple:
    """
    Call LLM to reformat citations.

    Args:
        raw_content: Raw content string with entry IDs

    Returns:
        Tuple of (LLM response string, usage dict) or (None, None) if failed
    """
    try:
        prompt = CITATION_FORMATTER_PROMPT.format(raw_content=raw_content)
        messages = [{"role": "user", "content": prompt}]

        llm_result = call_llm(
            stage="stage_5d",
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"}
        )

        result_text = llm_result["content"]

        usage = {
            'prompt_tokens': llm_result["prompt_tokens"],
            'completion_tokens': llm_result["completion_tokens"],
            'total_tokens': llm_result["total_tokens"],
            'cache_read_tokens': llm_result.get("cache_read_tokens", 0),
            'cache_write_tokens': llm_result.get("cache_write_tokens", 0),
            'cost': llm_result.get("cost", 0.0),
            # What actually served the call. The `model` parameter below is a
            # default no orchestrator passes, so recording it stamped every
            # artifact with a model the run never used (#459).
            'model': llm_result.get("model"),
        }

        return result_text, usage

    except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
        raise
    except Exception as e:
        # call_llm_formatter can run on a pool thread (#881 step 6), so this
        # log call is the only trace a swallowed batch (#810) leaves in the
        # run log.
        logger.exception("LLM formatting failed: %s: %s", type(e).__name__, e)
        return None, None


class _BatchResult(NamedTuple):
    """One batch's LLM call and parse, computed without writing to any
    shared dict (#881 step 6).

    id_to_formatted is None when no parse ran -- distinct from {} (parsed,
    found nothing). usage is None only when the call itself failed; usage
    and llm_output are NOT both-or-neither (a billed call can still return
    empty llm_output).
    """
    id_to_formatted: dict[str, dict] | None
    id_to_entry: dict[str, dict]
    usage: dict | None


def _format_batch(batch: list[dict]) -> _BatchResult:
    """Pure body of run_stage_5d's loop, run inside map_in_order (#881
    step 6). Never prints (call_llm_formatter / parse_llm_output take no
    verbose param) so a pool thread can't interleave stdout; failures are
    logged instead. Parsing gates on llm_output truthiness, not on usage
    being non-None -- see _BatchResult.
    """
    raw_content, id_to_entry = build_raw_content(batch)
    llm_output, usage = call_llm_formatter(raw_content)
    id_to_formatted = parse_llm_output(llm_output, id_to_entry) if llm_output else None
    return _BatchResult(id_to_formatted, id_to_entry, usage)


def _batch_progress_printer(total_batches: int) -> Callable[[int, _BatchResult], None]:
    """``[N/M] batches formatted (K citations)`` then ``Parsed N`` when a
    parse ran, via the shared make_progress_printer (#923). The ``[N/M]``
    line is a parsed contract -- orchestrator.py's PROGRESS_PATTERNS read
    it into the progress bar; pinned by
    test_5d_batch_progress_line_is_read_by_progress_patterns.
    """
    def format_lines(done: int, _index: int, result: _BatchResult) -> list[str]:
        lines = [f"\n  [{done}/{total_batches}] batches formatted ({len(result.id_to_entry)} citations)"]
        if result.id_to_formatted is not None:
            lines.append(f"  Parsed {len(result.id_to_formatted)} formatted citations")
        return lines

    return make_progress_printer(format_lines)


def run_stage_5d(input_path: str, output_path: str | None = None,
                 verbose: bool = True, batch_size: int = 20,
                 workers: int = STAGE5D_BATCH_WORKERS) -> str:
    """
    Run Stage 5d: Citation Formatter for non-enriched publications.

    Args:
        input_path: Path to input JSON (Stage 5c or earlier)
        output_path: Optional output path
        verbose: Whether to print progress
        batch_size: Number of citations to process per LLM call
        workers: Batches formatted at once (default STAGE5D_BATCH_WORKERS).
            1 reproduces the pre-#881 serial loop via map_in_order's true
            serial path.

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
        print(f"Stage 5d: Citation Formatter (Non-Enriched)")
        print(f"{'='*60}")
        print(f"Document: {document_uid}")

    # Find non-enriched S-code entries
    non_enriched = []
    for entry in entries:
        code = entry.get('taxonomy_code', '')
        if code in PUBLICATION_CODES:
            status = entry.get('enrichment_status', '')
            if status != 'enriched':
                non_enriched.append(entry)

    if verbose:
        total_pubs = sum(1 for e in entries if e.get('taxonomy_code', '') in PUBLICATION_CODES)
        print(f"  Total publications: {total_pubs}")
        print(f"  Non-enriched (need formatting): {len(non_enriched)}")

    if not non_enriched:
        if verbose:
            print("  No non-enriched citations to format")
        # Just copy input to output
        if not output_path:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / f"{document_uid}_citation_formatted.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return str(output_path)

    # Process in batches
    formatted_count = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    total_cost = 0.0
    observed_model = None

    batches = make_batches(non_enriched, batch_size)
    printer = _batch_progress_printer(len(batches)) if verbose else None

    # map_in_order's RETURN VALUE is in submission order regardless of
    # workers (its own contract); `on_result` is only for the progress
    # line, which is allowed to print in completion order. Accumulating
    # from `on_result` instead of this return value would silently switch
    # to completion order under workers>1.
    results = map_in_order(_format_batch, [(batch,) for batch in batches], workers, on_result=printer)

    # Accumulation happens ONLY here, on the calling thread, over `results`
    # in submission order -- never inside _format_batch -- so total_cost's
    # float summation order and which section's data wins a tied entry id
    # stay identical to the pre-#881 serial loop.
    for result in results:
        # Accumulate tokens + the per-batch cost from llm_result['cost'],
        # which calculate_cost() prices per-provider and per-model and
        # accounts for Bedrock prompt-cache discounts. Don't recompute
        # cost here from a hardcoded $/M-token figure.
        if result.usage is not None:
            total_prompt_tokens += result.usage.get('prompt_tokens', 0)
            total_completion_tokens += result.usage.get('completion_tokens', 0)
            total_cache_read_tokens += result.usage.get('cache_read_tokens', 0)
            total_cache_write_tokens += result.usage.get('cache_write_tokens', 0)
            total_cost += result.usage.get('cost', 0.0)
            observed_model = result.usage.get('model') or observed_model

        # Update entries with formatted data. id_to_formatted is None when
        # no parse ran (see _BatchResult) -- `or {}` makes that a no-op,
        # same as the {} (parsed, found nothing) case.
        for entry_id, formatted_data in (result.id_to_formatted or {}).items():
            entry = result.id_to_entry.get(entry_id)
            if entry and apply_formatted_fields(entry, formatted_data):
                formatted_count += 1

    # Add stage metadata
    data['stage_5d'] = {
        'stage': '5d',
        'stage_name': 'Citation Formatter (Non-Enriched)',
        'input_file': input_path,
        'non_enriched_count': len(non_enriched),
        'formatted_count': formatted_count,
        'model': observed_model,
        'timestamp': datetime.now().isoformat(),
        'total_cost': total_cost,
        'prompt_tokens': total_prompt_tokens,
        'completion_tokens': total_completion_tokens,
        'total_tokens': total_prompt_tokens + total_completion_tokens,
        'cache_read_tokens': total_cache_read_tokens,
        'cache_write_tokens': total_cache_write_tokens,
    }

    # Write output
    if not output_path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"{document_uid}_citation_formatted.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n  Citations formatted: {formatted_count}/{len(non_enriched)}")
        print(f"  Output: {output_path}")

    return str(output_path)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Stage 5d: Citation Formatter for Non-Enriched Publications"
    )
    parser.add_argument('input_path', help='Path to input JSON file')
    parser.add_argument('-o', '--output', help='Output path (optional)')
    parser.add_argument('-b', '--batch-size', type=int, default=20,
                        help='Citations per LLM call (default: 20)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Quiet mode (minimal output)')

    args = parser.parse_args()

    output = run_stage_5d(
        args.input_path,
        output_path=args.output,
        batch_size=args.batch_size,
        verbose=not args.quiet
    )

    print(f"\nStage 5d complete: {output}")


if __name__ == '__main__':
    main()
