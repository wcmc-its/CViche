#!/usr/bin/env python3
"""
Stage 4.5: Research Summary Generation

Generates a biosketch-style research summary statement (M1) by:
1. Scoring any existing M1 content for biosketch quality (0-1)
2. If score >= 0.8, use existing content
3. Otherwise, synthesize a new summary from high-value CV sections

Input: Stage 4 field extraction output (*_fields.json) or Stage 5 enriched output
Output: Same JSON with M1 entry replaced by generated research summary
"""

import argparse
import json
import re
import os
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

# Output directory
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_4_5_research_summary"

from unified_pipeline.llm_client import call_llm

# Section weights for biosketch relevance (0 = essential, -1 = not useful)
# Based on NIH biosketch requirements
SECTION_WEIGHTS = {
    # Tier 1 - Essential
    'M1': 0.0,      # Research Activities (core)
    'M2A': 0.0,     # Current Funding
    'S1': -0.05,    # Peer-reviewed articles
    'S0': -0.05,    # Bibliometric profile
    # NOTE: M4 clinical trial codes removed - clinical trials now use M2A/M2B/M2C based on status
    'N4': -0.1,     # Mentorship outputs
    'H': -0.2,      # Honors & Awards

    # Tier 2 - Context-dependent
    'M2B': -0.25,   # Completed Funding
    'N3A': -0.3,    # Current Mentees
    'N3B': -0.3,    # Past Mentees
    'O': -0.3,      # Institutional Leadership
    'Q4': -0.4,     # Editorial Activities
    'Q3': -0.4,     # Grant Reviewing
    'K1': -0.4,     # Teaching
    'K2': -0.4,
    'K3': -0.4,
    'R': -0.5,      # Invited Talks

    # Tier 3 - Peripheral
    'M2D': -0.6,    # Patents & Innovations
    'Q1': -0.6,     # External Leadership
    'M2C': -0.6,    # Pending Funding
    'S2': -0.8,     # Reviews/Editorials
    'S8': -0.8,     # Abstracts

    # Tier 4 - Rarely included
    'B1': -0.9,     # Education
    'D1': -0.9,     # Positions
    'T': -1.0,      # Appendix
}

# Entry limits per section (to manage token usage for large CVs)
ENTRY_LIMITS = {
    'S1': 15,       # Top 15 publications
    'M2A': 8,       # Top 8 current grants
    'M2B': 5,       # Top 5 completed grants
    'H': 10,        # Top 10 honors
    'R': 5,         # Top 5 invited talks
    'N3A': 5,       # Top 5 current mentees
    'N3B': 5,       # Top 5 past mentees
    'default': 5,   # Default limit for other sections
}

# Seniority bonuses
PI_BONUS = 0.15           # Bonus for PI role on grants
SENIOR_AUTHOR_BONUS = 0.1  # Bonus for senior/first author on pubs

# Taxonomy code groups (see docs/CODING_STANDARDS.md §8.2)
GRANT_TAXONOMY_PREFIX = 'M2'  # M2A/M2B/M2C/M2D -- all grant/funding entries
PUBLICATION_TAXONOMY_CODES = ('S1', 'S2', 'S7', 'S8')  # publication-like codes that need a title
# Current by definition (active funding, current mentees) unless the entry's end_date has passed.
CURRENT_TAXONOMY_CODES = ('M2A', 'N3A')
RESEARCH_ACTIVITIES_CODE = 'M1'  # an undated M1 narrative is the owner's present research statement

# Recency (#946 item 6). An entry's recency score is 1.0 when it is ongoing and
# falls linearly to 0 over RECENCY_WINDOW_YEARS; RECENCY_WEIGHT scales it into
# both the within-section ranking and the cross-section context order, so
# current work outranks a decade-old first-author paper.
RECENCY_WEIGHT = 0.4
RECENCY_WINDOW_YEARS = 20
DATE_FIELDS = ('year', 'start_date', 'end_date', 'date')
YEAR_PATTERN = re.compile(r'\b(?:19|20)\d{2}\b')
# Matches an end_date value that names no date of its own, only that the
# entry is ongoing: the whole value ("Present"), a range's open end
# ("2024 - Present", "to present"), or "Currently Working" (8 occurrences in
# the local stage-4 farm). Anchored at the start so a trailing qualifier
# ("2019, not current") does not count, and at the end so trailing text after
# the open word ("2019 - present, renewed 2024") does not count either (#947).
ONGOING_PATTERN = re.compile(
    r'(?:^|[-–—]|\bto)\s*\b(?:present|current|ongoing|now|currently\s+working)\b\s*$', re.IGNORECASE)
OPEN_RANGE_PATTERN = re.compile(r'\b(?:19|20)\d{2}\s*[-–—]+\s*(?:present|current|ongoing|now)\b', re.IGNORECASE)
CURRENT_CONTEXT_TAG = '[CURRENT]'
# A leading "YYYY" or "YYYY-YYYY" token in free text, for latest_entry_year's
# text fallback: group 2 is empty when there is no range.
LEADING_YEAR_OR_RANGE_PATTERN = re.compile(r'\b((?:19|20)\d{2})\b(?:\s*[-–—]+\s*\b((?:19|20)\d{2})\b)?')

_UNCOMPUTED = object()  # sentinel: is_current_entry() should compute latest_entry_year itself


def latest_entry_year(entry: dict, current_year: int) -> int | None:
    """Latest year across the entry's date fields (trusted, so not capped);
    else the entry's own date from its free text -- the first year-or-range
    token, taking a range's END year ("Project A, 2021-2023" -> 2023, a
    2004-2024 range is recent work, not 2004 (#947 round 3)), not the max of
    every year mentioned (a later aside, such as a renewal year, must not
    make an old entry look recent). A range straddling current_year (start
    <= current_year < end, e.g. "Project 2022-2028" at current_year=2026) is
    an entry still in progress and is capped at current_year, not skipped in
    favour of a later token. A bare year after current_year, or a range
    whose START is after current_year, is a typo or forward-looking
    projection and is skipped in favour of the next token in the text."""
    fields = entry.get('extracted_fields') or {}
    field_years = [int(y) for name in DATE_FIELDS for y in YEAR_PATTERN.findall(str(fields.get(name) or ''))]
    if field_years:
        return max(field_years)
    for start, end in LEADING_YEAR_OR_RANGE_PATTERN.findall(entry.get('text') or ''):
        if end:
            start_year, end_year = int(start), int(end)
            if start_year > current_year:
                continue
            return min(end_year, current_year)
        candidate = int(start)
        if candidate <= current_year:
            return candidate
    return None


def resolve_current_year(current_year: int | None) -> int:
    """The given year, or the wall-clock year when None (production's default)."""
    return datetime.now().year if current_year is None else current_year


def ended_before(entry: dict, current_year: int) -> bool:
    """True when the entry's end_date names a year earlier than current_year.
    An end_date matching ONGOING_PATTERN ("2024 - Present") has not ended,
    whatever year it names: is_current_entry's CURRENT_TAXONOMY_CODES branch
    returns ``not ended_before(...)`` before ONGOING_PATTERN is ever checked,
    so without this an open-ended M2A/N3A read as ended (#947 round 3)."""
    fields = entry.get('extracted_fields') or {}
    end_date = str(fields.get('end_date') or '')
    if ONGOING_PATTERN.search(end_date):
        return False
    end_years = [int(y) for y in YEAR_PATTERN.findall(end_date)]
    return bool(end_years) and max(end_years) < current_year


def is_current_entry(entry: dict, taxonomy_code: str, current_year: int, latest_year: int | None = _UNCOMPUTED) -> bool:
    """True for an ongoing entry: a current-by-definition section whose
    end_date has not passed, an undated M1 narrative, an end_date of
    'present'/'current'/'ongoing', or (M1 only -- M1 project lines carry
    their dates only in the text) an open 'YYYY-present' range in the text.

    `latest_year`, when given, is the caller's already-computed
    latest_entry_year(entry, current_year), so the M1 undated check does not
    compute it a second time (#947)."""
    if taxonomy_code in CURRENT_TAXONOMY_CODES:
        return not ended_before(entry, current_year)
    if taxonomy_code == RESEARCH_ACTIVITIES_CODE:
        year = latest_entry_year(entry, current_year) if latest_year is _UNCOMPUTED else latest_year
        if year is None:
            return True
    fields = entry.get('extracted_fields') or {}
    if ONGOING_PATTERN.search(str(fields.get('end_date') or '')):
        return True
    if taxonomy_code == RESEARCH_ACTIVITIES_CODE:
        return bool(OPEN_RANGE_PATTERN.search(entry.get('text') or ''))
    return False


@dataclass(frozen=True)
class EntryRecency:
    """One entry's recency facts, computed once (#947: is_current_entry's
    undated-M1 branch and the decay formula each called latest_entry_year
    separately for the same entry)."""
    is_current: bool
    latest_year: int | None
    score: float


def compute_entry_recency(entry: dict, taxonomy_code: str, current_year: int) -> EntryRecency:
    """The entry's is_current/latest_year/score, sharing one latest_entry_year call."""
    year = latest_entry_year(entry, current_year)
    current = is_current_entry(entry, taxonomy_code, current_year, latest_year=year)
    if current:
        score = 1.0
    elif year is None:
        score = 0.0
    else:
        score = min(max(1 - (current_year - year) / RECENCY_WINDOW_YEARS, 0.0), 1.0)
    return EntryRecency(is_current=current, latest_year=year, score=score)


def score_entry_recency(entry: dict, taxonomy_code: str, current_year: int) -> float:
    """0.0-1.0: 1.0 if ongoing, else linear decay over RECENCY_WINDOW_YEARS; undated scores 0."""
    return compute_entry_recency(entry, taxonomy_code, current_year).score


def score_entry_seniority(entry: dict, taxonomy_code: str, cv_owner_name: str = '') -> float:
    """
    Score an entry's seniority/importance.

    Returns a bonus score (0.0 to 0.15) based on:
    - PI role for grants
    - Senior/first author for publications
    """
    fields = entry.get('extracted_fields', {})
    text = entry.get('text', '')

    # Grant seniority (PI vs Co-I)
    if taxonomy_code.startswith(GRANT_TAXONOMY_PREFIX):
        role = fields.get('pi_role', '') or fields.get('role', '')
        if role:
            role_lower = role.lower()
            if 'principal investigator' in role_lower or role_lower in ('pi', 'mpi'):
                return PI_BONUS
            elif 'co-pi' in role_lower or 'co-principal' in role_lower:
                return PI_BONUS * 0.7

    # Publication seniority (first/last author)
    if taxonomy_code == 'S1':
        authors = fields.get('authors', '')
        target_name = fields.get('target_name', '') or cv_owner_name

        if authors and target_name:
            # Check if target is first or last author
            author_list = [a.strip() for a in authors.split(',')]
            if len(author_list) >= 1:
                first_author = author_list[0].lower()
                last_author = author_list[-1].lower() if len(author_list) > 1 else ''
                parts = (target_name or '').split()
                target_lower = parts[0].lower() if parts else ''

                if target_lower and (target_lower in first_author or target_lower in last_author):
                    return SENIOR_AUTHOR_BONUS

    return 0.0


def prioritize_entries(entries: list[dict], taxonomy_code: str, current_year: int, cv_owner_name: str = '',
                       limit: int = None, recency_by_id: dict[int, EntryRecency] | None = None) -> list[dict]:
    """
    Prioritize and limit entries for a taxonomy code.

    Prioritization:
    - Recency (ongoing first, then most recent)
    - Senior author / PI role
    - Has substantive content

    `recency_by_id`, keyed by id(entry), supplies an entry's already-computed
    EntryRecency so this function does not call compute_entry_recency a
    second time for an entry the caller (gather_context_entries) already
    scored (#947 review: recency was computed up to 3x per entry).
    """
    if not entries:
        return []

    # Score each entry
    scored_entries = []
    for entry in entries:
        seniority_score = score_entry_seniority(entry, taxonomy_code, cv_owner_name)
        recency = (recency_by_id or {}).get(id(entry))
        if recency is None:
            recency = compute_entry_recency(entry, taxonomy_code, current_year)

        # Content quality score (has substantive text)
        text_len = len(entry.get('text', ''))
        content_score = min(text_len / 500, 1.0)  # Cap at 1.0

        total_score = seniority_score + (recency.score * RECENCY_WEIGHT) + (content_score * 0.1)
        scored_entries.append((total_score, entry))

    # Sort by score descending
    scored_entries.sort(key=lambda x: x[0], reverse=True)

    # Apply limit
    if limit is None:
        limit = ENTRY_LIMITS.get(taxonomy_code, ENTRY_LIMITS['default'])

    return [entry for _, entry in scored_entries[:limit]]


def gather_context_entries(entries_by_code: dict[str, list[dict]], current_year: int,
                           cv_owner_name: str = '') -> list[tuple[str, dict, float, EntryRecency]]:
    """
    Gather and weight entries from all sections for summary generation.

    An entry's weight is its section weight + seniority bonus + scaled recency,
    so ongoing work sorts ahead of older work from a higher-tier section.

    Each entry's EntryRecency is computed once here and carried through as
    the 4th tuple element, so both prioritize_entries' cut and
    build_context_string's [CURRENT] tag reuse it instead of recomputing
    (#947 review).

    Returns list of (taxonomy_code, entry, weight, recency) tuples, sorted by weight.
    """
    weighted_entries = []

    for code, entries in entries_by_code.items():
        base_weight = SECTION_WEIGHTS.get(code, -0.9)

        # Skip very low value sections
        if base_weight <= -0.85:
            continue

        recency_by_id = {id(entry): compute_entry_recency(entry, code, current_year) for entry in entries}

        # Prioritize and limit entries
        prioritized = prioritize_entries(entries, code, current_year, cv_owner_name=cv_owner_name,
                                          recency_by_id=recency_by_id)

        for entry in prioritized:
            seniority_bonus = score_entry_seniority(entry, code, cv_owner_name)
            recency = recency_by_id[id(entry)]
            final_weight = base_weight + seniority_bonus + (recency.score * RECENCY_WEIGHT)

            weighted_entries.append((code, entry, final_weight, recency))

    # Sort by weight (higher = more important, closer to 0)
    weighted_entries.sort(key=lambda x: x[2], reverse=True)

    return weighted_entries


def is_valid_entry(code: str, entry: dict) -> bool:
    """
    Check if an entry has valid/substantive content worth including.

    Filters out entries with empty titles, "None" values, or no meaningful content.
    """
    fields = entry.get('extracted_fields', {}) or {}
    text = (entry.get('text') or '').strip()

    # Grant entries need a valid title
    if code.startswith(GRANT_TAXONOMY_PREFIX):
        title = (fields.get('title') or '').strip()
        if not title or title.lower() == 'none':
            return False

    # Publications need a title
    if code in PUBLICATION_TAXONOMY_CODES:
        title = (fields.get('title') or '').strip()
        if not title or title.lower() == 'none':
            return False

    # General check: must have some text content
    if not text:
        return False

    return True


def format_entry_for_context(code: str, entry: dict) -> str:
    """Format an entry for inclusion in the LLM context (no truncation)."""
    fields = entry.get('extracted_fields', {})
    text = entry.get('text', '')  # No truncation

    # Format based on taxonomy code
    if code == 'S1':  # Publication
        authors = fields.get('authors', '')
        title = fields.get('title', '')
        journal = fields.get('journal', '')
        year = fields.get('year', '')
        return f"[PUB] {authors}. {title}. {journal}. {year}"

    elif code.startswith(GRANT_TAXONOMY_PREFIX):  # Grant
        title = fields.get('title', '')
        role = fields.get('pi_role', '') or fields.get('role', '')
        agency = fields.get('agency', '')
        return f"[GRANT-{code}] {title} | Role: {role} | Agency: {agency}"

    elif code == 'M1':  # Research activities
        return f"[RESEARCH] {text}"

    elif code == 'H':  # Honors
        award = fields.get('award_name', '') or fields.get('title', '') or text
        year = fields.get('year', '')
        return f"[HONOR] {award} ({year})" if year else f"[HONOR] {award}"

    elif code == 'S0':  # Bibliometric
        return f"[METRICS] {text}"

    else:
        # Generic format - no truncation
        return f"[{code}] {text}"


def build_context_string(weighted_entries: list[tuple[str, dict, float, EntryRecency]],
                         max_tokens: int = 4000) -> str:
    """
    Build context string from weighted entries, respecting token limit.

    Entries are:
    1. Filtered to remove invalid/empty entries
    2. Sorted in descending order by weight (most relevant first)

    Each entry's EntryRecency (4th tuple element) is gather_context_entries'
    already-computed value; the [CURRENT] tag reads its `is_current` field
    instead of calling is_current_entry again (#947 review). Unlike
    prioritize_entries/gather_context_entries/is_current_entry, this function
    never resolves a year itself, so it takes no current_year parameter
    (#947 round 3: the earlier "signature parity" parameter was unused).

    Rough estimate: 1 token ≈ 4 characters
    """
    max_chars = max_tokens * 4
    context_parts = []
    total_chars = 0

    # Filter and sort: already sorted by gather_context_entries, but ensure descending order
    # (higher weight = more relevant, closer to 0)
    valid_entries = [(code, entry, weight, recency) for code, entry, weight, recency in weighted_entries
                     if is_valid_entry(code, entry)]

    # Sort by weight descending (higher/closer to 0 = more relevant)
    valid_entries.sort(key=lambda x: x[2], reverse=True)

    for code, entry, weight, recency in valid_entries:
        formatted = format_entry_for_context(code, entry)
        if recency.is_current:
            formatted = f"{CURRENT_CONTEXT_TAG} {formatted}"

        if total_chars + len(formatted) > max_chars:
            break

        context_parts.append(formatted)
        total_chars += len(formatted) + 1  # +1 for newline

    return '\n'.join(context_parts)


def score_existing_m1(m1_content: str) -> tuple[float, str, dict]:
    """
    Score existing M1 content for biosketch summary quality.

    Returns (score, reasoning, usage) where score is 0-1.
    """
    prompt = f"""Score the following research content for its quality as a biosketch-style research summary statement.

A HIGH-QUALITY biosketch summary (score 0.8-1.0):
- Is a cohesive narrative paragraph (not bullet points)
- Describes research themes, methods, and impact
- Mentions funding sources and key contributions
- Is suitable for an NIH biosketch personal statement
- Example: "Dr. Smith's research program focuses on understanding the molecular mechanisms of cancer metastasis. Using advanced imaging and computational approaches, her lab has identified novel biomarkers for early detection. Her work, funded by NCI R01 grants, has resulted in 3 patents and informed clinical guidelines for screening."

A MEDIUM-QUALITY summary (score 0.4-0.7):
- Has some narrative but incomplete
- May be bullet points with good content
- Missing key elements (funding, impact, methods)
- Example: "Research interests include: cancer biology, biomarker discovery, clinical translation"

A LOW-QUALITY summary (score 0.0-0.3):
- Just keywords or topics
- No narrative structure
- Missing context and depth
- Example: "Cancer. Genomics. Public health."

CONTENT TO SCORE:
\"\"\"
{m1_content}
\"\"\"

Respond with JSON only:
{{"score": <float 0-1>, "reasoning": "<brief explanation>"}}"""

    messages = [{"role": "user", "content": prompt}]

    llm_result = call_llm(
        stage="stage_4_5",
        messages=messages,
        temperature=0.1,
        max_tokens=200
    )

    result_text = llm_result["content"].strip()

    usage = {
        'prompt_tokens': llm_result["prompt_tokens"],
        'completion_tokens': llm_result["completion_tokens"],
        'total_tokens': llm_result["total_tokens"],
        'cache_read_tokens': llm_result.get("cache_read_tokens", 0),
        'cache_write_tokens': llm_result.get("cache_write_tokens", 0),
        'cost': llm_result.get("cost", 0.0),
    }

    # Parse JSON response
    try:
        # Handle markdown code blocks
        if '```' in result_text:
            result_text = re.search(r'```(?:json)?\s*(.*?)\s*```', result_text, re.DOTALL).group(1)
        result = json.loads(result_text)
        return float(result.get('score', 0)), result.get('reasoning', ''), usage
    except (json.JSONDecodeError, AttributeError):
        # Fallback: try to extract score
        score_match = re.search(r'"score"\s*:\s*([\d.]+)', result_text)
        if score_match:
            return float(score_match.group(1)), "Score extracted from response", usage
        return 0.0, "Failed to parse response", usage


# Generation-prompt requirements (#946 item 6), named so tests pin them. Kept next to
# generate_research_summary rather than the recency constants above: these are prompt
# content, not recency logic, and NEUTRAL_REFERENCE_REQUIREMENT has nothing to do with dates.
CURRENT_WORK_REQUIREMENT = (
    f"Open with the researcher's CURRENT research and ongoing projects (entries tagged {CURRENT_CONTEXT_TAG} "
    "and the most recent years); mention older work only briefly, as background, after the current work"
)
NEUTRAL_REFERENCE_REQUIREMENT = (
    "Refer to the researcher by name or with gender-neutral phrasing (e.g. \"this research program\", \"this work\"); "
    "never use gendered pronouns (he/she/his/her/him) and never infer gender from the name"
)


def generate_research_summary(context: str, cv_owner_name: str) -> tuple[str, dict]:
    """
    Generate a biosketch-style research summary from CV context.

    Returns (summary_text, usage_dict).
    """
    prompt = f"""Generate a concise, NIH biosketch-style research summary paragraph for {cv_owner_name or 'this researcher'}.

REQUIREMENTS:
- Write ONE cohesive narrative paragraph of approximately 150-200 words (do NOT exceed 200 words)
- Focus on research themes, methods, and scientific contributions
- Mention key funding sources and roles (PI vs Co-I)
- Highlight impact (clinical translation, policy, mentorship outcomes if relevant)
- Write in third person
- {CURRENT_WORK_REQUIREMENT}
- {NEUTRAL_REFERENCE_REQUIREMENT}
- Do NOT list publications or include citations
- Do NOT include education or job titles
- Be specific about research areas, not generic
- Keep it concise - prioritize quality over comprehensiveness

CV CONTEXT (ranked by relevance; ongoing entries tagged {CURRENT_CONTEXT_TAG}):
{context}

Generate only the research summary paragraph (150-200 words max), no additional text or formatting."""

    messages = [{"role": "user", "content": prompt}]

    llm_result = call_llm(
        stage="stage_4_5",
        messages=messages,
        temperature=0.3,
        max_tokens=350
    )

    result_text = llm_result["content"].strip()

    usage = {
        'prompt_tokens': llm_result["prompt_tokens"],
        'completion_tokens': llm_result["completion_tokens"],
        'total_tokens': llm_result["total_tokens"],
        'cache_read_tokens': llm_result.get("cache_read_tokens", 0),
        'cache_write_tokens': llm_result.get("cache_write_tokens", 0),
        'cost': llm_result.get("cost", 0.0),
    }

    return result_text, usage


def _resolve_stage_4_5_input_file(input_path: str) -> Path:
    """input_path as a literal path, or else the first *_fields.json-shaped
    match for it under a stage 4/5/5b output directory (#947 round 3: carved
    out of run_stage_4_5 to hold its line count down after restoring the
    cache-pricing comment below)."""
    input_file = Path(input_path)
    if not input_file.exists():
        for stage_dir in ['stage_5b_institution_enrichment', 'stage_5_enrichment', 'stage_4_field_extraction']:
            candidates = list((Path(__file__).parent / "outputs" / stage_dir).glob(f"*{input_path}*.json"))
            if candidates:
                input_file = candidates[0]
                break
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    return input_file


def run_stage_4_5(input_path: str, output_path: str = None, verbose: bool = True) -> str:
    """
    Run Stage 4.5: Research Summary Generation.

    Args:
        input_path: Path to Stage 4 or Stage 5 JSON output
        output_path: Optional output path (default: outputs/stage_4_5_research_summary/)
        verbose: Print progress messages

    Returns:
        Path to output JSON file
    """
    input_file = _resolve_stage_4_5_input_file(input_path)

    # Load data
    with open(input_file, 'r') as f:
        data = json.load(f)

    current_year = resolve_current_year(None)  # #947: resolved once, reused for the whole run
    document_uid = data.get('document_uid', input_file.stem)
    cv_owner = data.get('cv_owner') or {}
    cv_owner_name = f"{cv_owner.get('first_name', '')} {cv_owner.get('last_name', '')}".strip()

    if not cv_owner_name:
        # Extract from document_uid
        parts = document_uid.split('_')
        if len(parts) >= 2:
            cv_owner_name = parts[1].replace('js', '').replace('Cv', '').capitalize()

    if verbose:
        print(f"\n{'='*60}")
        print(f"Stage 4.5: Research Summary Generation - {document_uid}")
        print(f"{'='*60}")
        print(f"CV Owner: {cv_owner_name}")

    # Group entries by taxonomy code
    entries_by_code: dict[str, list[dict]] = {}
    for entry in data.get('entries', []):
        code = entry.get('taxonomy_code', 'T')
        if code not in entries_by_code:
            entries_by_code[code] = []
        entries_by_code[code].append(entry)

    if verbose:
        print(f"Total entries: {len(data.get('entries', []))}")
        print(f"Taxonomy codes: {sorted(entries_by_code.keys())}")

    # Step 1: Check and score existing M1 content
    m1_entries = entries_by_code.get('M1', [])
    existing_m1_content = '\n'.join([e.get('text', '') for e in m1_entries])

    use_existing = False
    m1_score = 0.0
    score_reasoning = ""
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0
    total_cost = 0.0

    if existing_m1_content.strip():
        if verbose:
            print(f"\nScoring existing M1 content ({len(m1_entries)} entries)...")

        m1_score, score_reasoning, score_usage = score_existing_m1(existing_m1_content)

        # Track usage from scoring call
        if score_usage:
            total_prompt_tokens += score_usage.get('prompt_tokens', 0)
            total_completion_tokens += score_usage.get('completion_tokens', 0)
            total_cache_read_tokens += score_usage.get('cache_read_tokens', 0)
            total_cache_write_tokens += score_usage.get('cache_write_tokens', 0)
            total_cost += score_usage.get('cost', 0.0)

        if verbose:
            print(f"  Score: {m1_score:.2f}")
            print(f"  Reasoning: {score_reasoning}")

        if m1_score >= 0.8:
            use_existing = True
            if verbose:
                print(f"  -> Using existing M1 content (score >= 0.8)")
    else:
        if verbose:
            print("\nNo existing M1 content found.")

    # Step 2: Generate summary if needed
    context_entries_used = []
    top_codes_used = []

    if use_existing:
        research_summary = existing_m1_content
        generation_method = "existing_content"
    else:
        if verbose:
            print(f"\nGenerating research summary from CV context...")

        # Gather weighted context
        weighted_entries = gather_context_entries(entries_by_code, current_year, cv_owner_name=cv_owner_name)

        if verbose:
            print(f"  Context entries: {len(weighted_entries)}")
            # Show top entries by weight
            for code, entry, weight, _recency in weighted_entries[:5]:
                text_preview = entry.get('text', '')[:50]
                print(f"    [{code}] (w={weight:.2f}) {text_preview}...")

        # Build context string
        context = build_context_string(weighted_entries)

        # Track what was used
        context_entries_used = len(weighted_entries)
        top_codes_used = list(dict.fromkeys([code for code, _, _, _ in weighted_entries[:20]]))

        if verbose:
            print(f"  Context length: {len(context)} chars")

        # Generate summary
        research_summary, gen_usage = generate_research_summary(context, cv_owner_name)
        generation_method = "llm_generated"

        # Track usage from generation call
        if gen_usage:
            total_prompt_tokens += gen_usage.get('prompt_tokens', 0)
            total_completion_tokens += gen_usage.get('completion_tokens', 0)
            total_cache_read_tokens += gen_usage.get('cache_read_tokens', 0)
            total_cache_write_tokens += gen_usage.get('cache_write_tokens', 0)
            total_cost += gen_usage.get('cost', 0.0)

        if verbose:
            print(f"\nGenerated summary ({len(research_summary)} chars):")
            print(f"  {research_summary[:200]}...")

    # total_cost was accumulated from llm_result['cost'] on each call above,
    # which calculate_cost() prices per-provider and per-model (and accounts
    # for Bedrock prompt-cache reads at 0.1x and writes at 1.25x). Don't
    # recompute it here from a hardcoded $/M-token figure.

    # Build standalone output (not modifying upstream data)
    word_count = len(research_summary.split())

    # Capture original M1 content for traceability
    original_m1_content = None
    if m1_entries and generation_method == "llm_generated":
        # We generated new content, so track what was replaced
        original_m1_content = {
            "entries": [
                {
                    "text": e.get('text', ''),
                    "element_idx_start": e.get('element_idx_start'),
                    "element_idx_end": e.get('element_idx_end'),
                }
                for e in m1_entries
            ],
            "total_entries": len(m1_entries),
            "combined_text": existing_m1_content,
            "reason_not_used": f"Score {m1_score:.2f} below threshold 0.8 - {score_reasoning}"
        }

    output_data = {
        "document_uid": document_uid,
        "stage": "4.5",
        "stage_name": "Research Summary Generation",
        "cv_owner": cv_owner,
        "research_summary": {
            "text": research_summary,
            "word_count": word_count,
            "char_count": len(research_summary),
            "generation_method": generation_method,
            "m1_score": m1_score,
            "score_reasoning": score_reasoning,
            "generation_timestamp": datetime.now().isoformat(),
        },
        "context_used": {
            "entry_count": context_entries_used if isinstance(context_entries_used, int) else len(context_entries_used),
            "top_codes": top_codes_used,
        },
        "original_m1_content": original_m1_content,
        "input_file": str(input_file),
        "total_cost": total_cost,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "cache_read_tokens": total_cache_read_tokens,
        "cache_write_tokens": total_cache_write_tokens,
    }

    # Determine output path
    if output_path is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(OUTPUT_DIR / f"{document_uid}_research_summary.json")

    # Save
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    if verbose:
        print(f"\n{'='*60}")
        print(f"Stage 4.5 Complete")
        print(f"{'='*60}")
        print(f"  Method: {generation_method}")
        print(f"  M1 Score: {m1_score:.2f}")
        print(f"  Word count: {word_count}")
        print(f"  Summary length: {len(research_summary)} chars")
        print(f"\nSaved to: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Stage 4.5: Generate biosketch-style research summary"
    )
    parser.add_argument(
        'input',
        help="Input JSON file or document UID (e.g., '2015_Wende')"
    )
    parser.add_argument(
        '-o', '--output',
        help="Output path (default: outputs/stage_4_5_research_summary/)"
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help="Suppress progress output"
    )

    args = parser.parse_args()

    output_path = run_stage_4_5(args.input, args.output, verbose=not args.quiet)
    print(f"\nGenerated: {output_path}")


if __name__ == '__main__':
    main()
