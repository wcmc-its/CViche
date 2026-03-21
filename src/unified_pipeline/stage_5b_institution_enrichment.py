#!/usr/bin/env python3
"""
Stage 5b: Institution Enrichment via LLM

Enriches entries with institution location data (city, state, country) by sending
institution names to an LLM in batches of up to 10. Uses cv_owner_location from
Stage 4 for disambiguation (e.g., "OU College of Medicine" → Oklahoma vs Ohio).

Also returns a cleaned_name that separates embedded location from the institution
name, eliminating duplication like "Durham, NC, Durham, NC" in Stage 6 output.

Applies to:
- B1, B2: Education (university locations)
- C, C1, C2: Postdoctoral Training
- D1, D2, D3: Professional Positions

Input: Stage 5 enriched JSON (or Stage 4 fields JSON)
Output: *_institution_enriched.json with location data added

Author: Scholar Signals CV Pipeline
Date: 2025-11-29
"""

import os
import sys
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# OpenAI for LLM-mediated institution resolution
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Import prompt logger for tracking LLM calls
try:
    from core.prompt_logger import log_prompt_before_call, log_prompt_response
except ImportError:
    def log_prompt_before_call(*args, **kwargs):
        return None
    def log_prompt_response(*args, **kwargs):
        pass

# Paths
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5b_institution_enrichment"
CACHE_FILE = Path(__file__).parent / "config" / "institution_cache.json"
OLD_CACHE_FILE = Path(__file__).parent / "config" / "ror_cache.json"

# Global cache for institution lookups
INSTITUTION_CACHE: Dict[str, Optional[Dict]] = {}

# Taxonomy codes that need institution location enrichment
INSTITUTION_CODES = ['B1', 'B2', 'C', 'C1', 'C2', 'D1', 'D2', 'D3']

# LLM batch size
BATCH_SIZE = 10

# System prompt for institution resolution
INSTITUTION_SYSTEM_PROMPT = """You resolve institution names to their official names and geographic locations.

RULES:
1. CLEAN: If the input contains embedded location info (e.g., "Duke Medical Center, Durham, NC"), separate it:
   - cleaned_name = just the institution name without the location
   - city/state/country = the geographic info
2. RESOLVE: Use world knowledge to determine city, state, country for each institution.
3. DISAMBIGUATE: Use the CV OWNER CONTEXT to pick the correct institution when names are ambiguous.
   For example, "OU College of Medicine" could be University of Oklahoma or Ohio University —
   use the owner's location history and career trajectory to pick the right one.
4. For cleaned_name: remove trailing location fragments but preserve the meaningful institution name.
   "Duke University Medical Center, Durham, NC" → cleaned_name = "Duke University Medical Center"
   "Northeastern State University" → cleaned_name = "Northeastern State University" (no change needed)
5. For official_name: return the formal institutional name that would appear in official directories.
6. Always return country as the full name (e.g., "United States" not "USA" or "US").
7. Always return the two-letter ISO country_code (e.g., "US", "GB", "CA").
8. For US states, return the full state name (e.g., "New York" not "NY").

Return ONLY a JSON object with institution IDs as keys. No markdown fences, no extra text."""


def load_institution_cache():
    """Load institution cache from disk, migrating from old ror_cache.json if needed."""
    global INSTITUTION_CACHE

    # Try new cache file first
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                INSTITUTION_CACHE = json.load(f)
            return
        except Exception as e:
            print(f"Warning: Could not load institution cache: {e}")
            INSTITUTION_CACHE = {}
            return

    # Migrate from old ror_cache.json if it exists
    if OLD_CACHE_FILE.exists():
        try:
            with open(OLD_CACHE_FILE, 'r', encoding='utf-8') as f:
                INSTITUTION_CACHE = json.load(f)
            # Save under new name immediately
            save_institution_cache()
            print(f"Migrated cache: {OLD_CACHE_FILE.name} → {CACHE_FILE.name} ({len(INSTITUTION_CACHE)} entries)")
        except Exception as e:
            print(f"Warning: Could not migrate old cache: {e}")
            INSTITUTION_CACHE = {}
        return

    INSTITUTION_CACHE = {}


def save_institution_cache():
    """Save institution cache to disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(INSTITUTION_CACHE, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Could not save institution cache: {e}")


def normalize_institution_name(name: str) -> str:
    """Normalize institution name for better matching."""
    if not name:
        return ""

    # Remove extra whitespace
    name = ' '.join(name.split())

    # Common abbreviation expansions
    expansions = {
        r'\bUniv\.?\s+': 'University ',
        r'\bU\.?\s+of\s+': 'University of ',
        r'\bMed\.?\s+': 'Medical ',
        r'\bCtr\.?\s+': 'Center ',
        r'\bColl\.?\s+': 'College ',
        r'\bDept\.?\s+': 'Department ',
        r'\bInst\.?\s+': 'Institute ',
        r'\bHosp\.?\s+': 'Hospital ',
        r'\bSch\.?\s+': 'School ',
    }

    for pattern, replacement in expansions.items():
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)

    return name.strip()


def is_likely_internal_unit(name: str) -> bool:
    """
    Check if institution name is likely an internal university unit rather than
    a standalone organization.

    These should NOT be looked up as they'll get false positives.
    """
    name_lower = name.lower()

    # Common internal unit patterns
    internal_patterns = [
        'center for ',
        'centre for ',
        'office of ',
        'department of ',
        'division of ',
        'school of ',  # When standalone (not "X School of Medicine")
        'institute for ',
        'program in ',
        'laboratory of ',
        'lab of ',
    ]

    # Check if it starts with an internal pattern AND doesn't contain
    # a major institution indicator
    major_indicators = ['university', 'college', 'hospital', 'medical center']

    for pattern in internal_patterns:
        if name_lower.startswith(pattern):
            # Check if it also mentions a major institution
            if not any(ind in name_lower for ind in major_indicators):
                return True

    return False


def format_location(city: str, state: str, country: str, country_code: str) -> str:
    """Format city, state, country into a location string."""
    parts = []

    if city:
        parts.append(city)

    if state:
        # For US, use state abbreviation if we have country_code
        if country_code == 'US' and state:
            # Common state name to abbreviation mapping
            state_abbrevs = {
                'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
                'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
                'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
                'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
                'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
                'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
                'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
                'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
                'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
                'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
                'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT',
                'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
                'Wisconsin': 'WI', 'Wyoming': 'WY', 'District of Columbia': 'DC'
            }
            state_abbrev = state_abbrevs.get(state, state)
            parts.append(state_abbrev)
        else:
            parts.append(state)

    # Only add country if not US (common assumption for US CVs)
    if country and country_code != 'US':
        parts.append(country)

    return ', '.join(parts)


def _build_context_string(entry: Dict) -> str:
    """Build a context string for an entry to help LLM disambiguate."""
    fields = entry.get('extracted_fields', {})
    code = entry.get('taxonomy_code', '')
    parts = []

    if code:
        parts.append(code)

    # Include degree/title for context
    degree = fields.get('degree', '') or fields.get('title', '') or fields.get('training_type', '')
    if degree:
        parts.append(degree)

    # Include dates for temporal context
    start = fields.get('start_date', '')
    end = fields.get('end_date', '')
    if start or end:
        date_str = f"{start}" if start else ""
        if end:
            date_str = f"{date_str}-{end}" if date_str else end
        parts.append(date_str)

    return ', '.join(parts) if parts else ''


def _build_owner_context(cv_owner_location: Optional[Dict]) -> str:
    """Build a CV owner context string for the LLM prompt."""
    if not cv_owner_location or not cv_owner_location.get('inference_success'):
        return "No location context available for CV owner."

    parts = []
    metro = cv_owner_location.get('metro_area', '')
    primary = cv_owner_location.get('primary_location', {})

    if primary:
        inst = primary.get('institution', '')
        city = primary.get('city', '')
        state = primary.get('state', '')
        country = primary.get('country', '')
        loc_parts = [p for p in [city, state, country] if p]
        loc_str = ', '.join(loc_parts)
        if inst:
            parts.append(f"Currently at {inst} in {loc_str}")
        elif loc_str:
            parts.append(f"Currently based in {loc_str}")

    if metro:
        parts.append(f"Metro area: {metro}")

    # Include other locations for career trajectory
    locations = cv_owner_location.get('locations', [])
    if len(locations) > 1:
        other_locs = []
        for loc in locations[1:]:
            city = loc.get('city', '')
            state = loc.get('state', '')
            if city and state:
                other_locs.append(f"{city}, {state}")
            elif city:
                other_locs.append(city)
        if other_locs:
            parts.append(f"Career locations: {'; '.join(other_locs)}")

    return ' | '.join(parts) if parts else "No location context available for CV owner."


def lookup_institutions_llm(
    batch: List[Tuple[str, str, str]],
    cv_owner_location: Optional[Dict],
    model: str = "gpt-5.1",
    verbose: bool = False
) -> Tuple[Optional[Dict[str, Dict]], float]:
    """
    Look up a batch of institutions using an LLM.

    Args:
        batch: List of (inst_id, institution_name, context_string) tuples
        cv_owner_location: CV owner location dict for disambiguation
        model: LLM model to use
        verbose: Print progress

    Returns:
        Tuple of (results_dict, cost):
        - results_dict: {inst_id: {cleaned_name, official_name, city, state, country, country_code}}
          Returns None (not {}) on failure so callers can distinguish transient errors
          from legitimate empty results.
        - cost: API call cost in dollars
    """
    if not OPENAI_AVAILABLE:
        print("Warning: OpenAI not available. Skipping LLM institution lookup.")
        return None, 0.0

    # Build user prompt
    owner_context = _build_owner_context(cv_owner_location)

    lines = [f"CV OWNER CONTEXT: {owner_context}", "", "INSTITUTIONS TO RESOLVE:"]
    for inst_id, inst_name, context in batch:
        if context:
            lines.append(f"[{inst_id}] {inst_name}  (context: {context})")
        else:
            lines.append(f"[{inst_id}] {inst_name}")

    user_prompt = '\n'.join(lines)

    if verbose:
        print(f"    LLM batch: {len(batch)} institutions")

    try:
        client = OpenAI()

        # Build messages and log the prompt
        messages = [
            {"role": "system", "content": INSTITUTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]
        call_id = log_prompt_before_call(
            messages=messages,
            model=model,
            purpose="institution_enrichment",
            temperature=0.0
        )

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"}
        )

        # Calculate cost
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        # Token pricing for common models (per 1M tokens)
        pricing = {
            'gpt-5.1': {'input': 2.00, 'output': 8.00},
            'gpt-5-mini': {'input': 0.30, 'output': 1.20},
            'gpt-4.1': {'input': 2.00, 'output': 8.00},
            'gpt-4.1-mini': {'input': 0.40, 'output': 1.60},
            'gpt-4.1-nano': {'input': 0.10, 'output': 0.40},
        }
        model_pricing = pricing.get(model, {'input': 2.00, 'output': 8.00})
        cost = (prompt_tokens * model_pricing['input'] / 1_000_000 +
                completion_tokens * model_pricing['output'] / 1_000_000)

        # Log the response
        log_prompt_response(
            log_id=call_id,
            response=response,
            purpose="institution_enrichment"
        )

        # Parse response
        raw_text = response.choices[0].message.content.strip()
        results = json.loads(raw_text)

        if verbose:
            print(f"    LLM resolved {len(results)} institutions (cost: ${cost:.4f})")

        return results, cost

    except Exception as e:
        if verbose:
            print(f"    LLM institution lookup error: {e}")
        return None, 0.0


def enrich_entry_with_result(entry: Dict, result: Dict) -> Dict:
    """
    Apply a pre-looked-up institution result to an entry.

    Args:
        entry: Entry dictionary with extracted_fields
        result: LLM result dict with cleaned_name, official_name, city, state, country, country_code

    Returns:
        Entry with enriched location data
    """
    fields = entry.get('extracted_fields', {})

    # Store enrichment data (backward-compatible with Stage 6 readers)
    entry['institution_enrichment'] = {
        'official_name': result.get('official_name', ''),
        'cleaned_name': result.get('cleaned_name', ''),
        'city': result.get('city', ''),
        'state': result.get('state', ''),
        'country': result.get('country', ''),
        'country_code': result.get('country_code', ''),
        'source': 'llm'
    }

    # Update extracted_fields with location if missing
    existing_city = fields.get('city', '')
    existing_state = fields.get('state', '')
    existing_location = fields.get('location', '')

    enriched_fields = entry.get('enriched_fields', [])
    entry['enriched_fields'] = enriched_fields

    if not existing_city and result.get('city'):
        fields['city'] = result['city']
        if 'city' not in enriched_fields:
            enriched_fields.append('city')

    if not existing_state and result.get('state'):
        fields['state'] = result['state']
        if 'state' not in enriched_fields:
            enriched_fields.append('state')

    if not fields.get('country') and result.get('country'):
        fields['country'] = result['country']
        if 'country' not in enriched_fields:
            enriched_fields.append('country')

    # Create formatted location string if missing
    if not existing_location:
        location_str = format_location(
            result.get('city', ''),
            result.get('state', ''),
            result.get('country', ''),
            result.get('country_code', '')
        )
        if location_str:
            fields['location'] = location_str
            if 'location' not in enriched_fields:
                enriched_fields.append('location')

    entry['extracted_fields'] = fields
    return entry


def run_stage5b(input_path: str, output_path: str = None, verbose: bool = True,
                model: str = "gpt-5.1", refresh_cache: bool = False) -> str:
    """
    Run Stage 5b: Institution Enrichment via LLM.

    Args:
        input_path: Path to Stage 5 (or Stage 4) JSON
        output_path: Optional output path
        verbose: Print progress
        model: LLM model to use for institution resolution
        refresh_cache: If True, ignore existing cache and re-lookup all institutions

    Returns:
        Path to enriched output file
    """
    # Load input
    with open(input_path, 'r') as f:
        data = json.load(f)

    document_uid = data.get('document_uid', 'unknown')
    entries = data.get('entries', [])
    cv_owner_location = data.get('cv_owner_location')

    if verbose:
        print(f"\n{'='*60}")
        print(f"Stage 5b: Institution Enrichment (LLM) - {document_uid}")
        print(f"{'='*60}")
        print(f"Model: {model}")

    # Load cv_owner_location from Stage 4 if not in input
    if not cv_owner_location or not cv_owner_location.get('inference_success'):
        stage4_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"
        # Extract base UID (without stage suffixes)
        base_uid = document_uid.replace('_enriched', '').replace('_institution_enriched', '')
        candidates = list(stage4_dir.glob(f"*{base_uid}*_fields.json"))
        if not candidates:
            # Try broader match
            parts = base_uid.split('_', 1)
            if len(parts) > 1:
                candidates = list(stage4_dir.glob(f"{parts[0]}*_fields.json"))
        if candidates:
            try:
                with open(candidates[0], 'r') as f:
                    stage4_data = json.load(f)
                cv_owner_location = stage4_data.get('cv_owner_location')
                if cv_owner_location and cv_owner_location.get('inference_success') and verbose:
                    print(f"Loaded cv_owner_location from Stage 4 output")
            except Exception:
                pass

    if verbose:
        if cv_owner_location and cv_owner_location.get('inference_success'):
            metro = cv_owner_location.get('metro_area', '')
            primary = cv_owner_location.get('primary_location', {})
            print(f"CV owner context: {metro} ({primary.get('city', '')}, {primary.get('state', '')})")
        else:
            print("CV owner context: not available")

    # Count entries by code
    code_counts = {}
    for entry in entries:
        code = entry.get('taxonomy_code', 'T')
        code_counts[code] = code_counts.get(code, 0) + 1

    institution_entries = sum(
        code_counts.get(code, 0) for code in INSTITUTION_CODES
    )

    if verbose:
        print(f"Total entries: {len(entries)}")
        print(f"Entries needing institution lookup: {institution_entries}")
        print(f"  (Codes: {', '.join(INSTITUTION_CODES)})")
        if refresh_cache:
            print(f"  ** Refresh mode: ignoring cached entries **")

    # Load cache
    load_institution_cache()

    # Collect institutions needing lookup
    # Map: cache_key -> (entry indices list, institution_name, context_string)
    institutions_to_lookup = {}
    entry_to_cache_key = {}  # entry index -> cache_key

    for idx, entry in enumerate(entries):
        code = entry.get('taxonomy_code', '')
        if code not in INSTITUTION_CODES:
            continue

        fields = entry.get('extracted_fields', {})
        institution = fields.get('institution', '')
        if not institution:
            continue

        # Skip if city/state came from the original CV extraction (not from enrichment).
        # Enrichment-added fields are tracked in enriched_fields — if city was added by
        # a previous enrichment run (possibly from old ROR data), re-evaluate it.
        enriched_fields = entry.get('enriched_fields', [])
        if fields.get('city') and fields.get('state') and 'city' not in enriched_fields:
            continue

        # Use normalized name as cache key for better deduplication
        # "Duke Univ. Medical Center" and "Duke University Medical Center" → same key
        cache_key = normalize_institution_name(institution).lower().strip()
        # Also check the raw key for backward compat with old ROR cache entries
        raw_key = institution.lower().strip()
        entry_to_cache_key[idx] = cache_key

        # Check cache (skip if refreshing)
        if not refresh_cache:
            cached = INSTITUTION_CACHE.get(cache_key) or INSTITUTION_CACHE.get(raw_key)
            if cached is not None or cache_key in INSTITUTION_CACHE or raw_key in INSTITUTION_CACHE:
                # Skip old ROR-format entries — they lack cleaned_name and may have
                # wrong matches (e.g., Northeastern State University → Magadan, Russia).
                # Force re-lookup via LLM for accurate disambiguation.
                is_old_ror = cached and 'ror_id' in cached and cached.get('source') != 'llm'
                if is_old_ror:
                    pass  # Fall through to LLM lookup below
                else:
                    # Key exists in cache (even if value is None = negative result)
                    if cached:
                        result = {
                            'cleaned_name': cached.get('cleaned_name', ''),
                            'official_name': cached.get('official_name', cached.get('name', '')),
                            'city': cached.get('city', ''),
                            'state': cached.get('state', ''),
                            'country': cached.get('country', ''),
                            'country_code': cached.get('country_code', '')
                        }
                        entries[idx] = enrich_entry_with_result(entries[idx], result)
                    continue

        # Need LLM lookup
        context = _build_context_string(entry)
        if cache_key not in institutions_to_lookup:
            institutions_to_lookup[cache_key] = {
                'entry_indices': [],
                'institution_name': normalize_institution_name(institution),
                'raw_name': institution,
                'context': context
            }
        institutions_to_lookup[cache_key]['entry_indices'].append(idx)

    uncached_count = len(institutions_to_lookup)
    cached_count = sum(1 for idx in entry_to_cache_key if idx not in
                       [i for info in institutions_to_lookup.values() for i in info['entry_indices']])

    if verbose:
        print(f"\nCache hits: {cached_count}")
        print(f"Institutions needing LLM lookup: {uncached_count}")

    # Batch LLM lookups
    total_cost = 0.0
    llm_calls = 0

    if uncached_count > 0:
        # Build batches
        items = list(institutions_to_lookup.items())
        batches = []
        for i in range(0, len(items), BATCH_SIZE):
            batch_items = items[i:i + BATCH_SIZE]
            batch = []
            for j, (cache_key, info) in enumerate(batch_items):
                inst_id = f"INST-{i + j + 1:04d}"
                batch.append((inst_id, info['institution_name'], info['context'], cache_key, info))
            batches.append(batch)

        if verbose:
            print(f"LLM batches: {len(batches)} (batch size: {BATCH_SIZE})")

        for batch_idx, batch in enumerate(batches):
            # Prepare for LLM call
            llm_batch = [(inst_id, name, ctx) for inst_id, name, ctx, _, _ in batch]

            results, cost = lookup_institutions_llm(
                llm_batch,
                cv_owner_location,
                model=model,
                verbose=verbose
            )
            total_cost += cost
            llm_calls += 1

            # None means the LLM call itself failed — don't cache anything
            if results is None:
                if verbose:
                    print(f"    Batch failed — skipping cache writes for {len(batch)} institutions")
                continue

            # Process results (LLM call succeeded)
            for inst_id, name, ctx, cache_key, info in batch:
                result = results.get(inst_id, {})
                if result:
                    # Cache the result
                    INSTITUTION_CACHE[cache_key] = {
                        'cleaned_name': result.get('cleaned_name', ''),
                        'official_name': result.get('official_name', ''),
                        'city': result.get('city', ''),
                        'state': result.get('state', ''),
                        'country': result.get('country', ''),
                        'country_code': result.get('country_code', ''),
                        'source': 'llm'
                    }

                    # Apply to all entries that share this institution
                    for entry_idx in info['entry_indices']:
                        entries[entry_idx] = enrich_entry_with_result(entries[entry_idx], result)

                    if verbose:
                        city = result.get('city', '')
                        state = result.get('state', '')
                        print(f"    {name[:40]:40s} → {city}, {state}")
                else:
                    # LLM succeeded but didn't return this institution — safe to cache negative
                    INSTITUTION_CACHE[cache_key] = None
                    if verbose:
                        print(f"    {name[:40]:40s} → (no result)")

    # Save cache
    save_institution_cache()

    # Count enriched entries
    enriched_count = sum(
        1 for entry in entries
        if entry.get('taxonomy_code', '') in INSTITUTION_CODES
        and entry.get('institution_enrichment')
    )

    if verbose:
        print(f"\nInstitutions enriched: {enriched_count}/{institution_entries}")
        print(f"LLM calls: {llm_calls}")
        print(f"Total cost: ${total_cost:.4f}")

    # Prepare output
    data['entries'] = entries
    data['stage'] = '5b'
    data['institution_enrichment_stats'] = {
        'entries_processed': institution_entries,
        'entries_enriched': enriched_count,
        'cache_hits': cached_count,
        'llm_lookups': uncached_count,
        'llm_calls': llm_calls,
        'cost': total_cost,
        'model': model,
        'timestamp': datetime.now().isoformat()
    }

    # Determine output path
    if output_path is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(OUTPUT_DIR / f"{document_uid}_institution_enriched.json")

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\nSaved to: {output_path}")

    return output_path


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Stage 5b: Institution Enrichment via LLM')
    parser.add_argument('input', help='Stage 5 enriched JSON file or document UID')
    parser.add_argument('--output', '-o', help='Output JSON path')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress progress output')
    parser.add_argument('--model', '-m', default='gpt-5.1', help='LLM model (default: gpt-5.1)')
    parser.add_argument('--refresh-cache', action='store_true',
                        help='Ignore existing cache and re-lookup all institutions via LLM')

    args = parser.parse_args()

    # Resolve input path
    input_path = args.input
    if not os.path.exists(input_path):
        # Try Stage 5 outputs first, then Stage 4
        stage5_dir = Path(__file__).parent / "outputs" / "stage_5_enrichment"
        stage4_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"

        candidates = list(stage5_dir.glob(f"*{input_path}*_enriched.json"))
        if not candidates:
            candidates = list(stage4_dir.glob(f"*{input_path}*_fields.json"))

        if candidates:
            input_path = str(candidates[0])
        else:
            print(f"Error: Could not find input file: {args.input}")
            sys.exit(1)

    output_path = run_stage5b(
        input_path, args.output,
        verbose=not args.quiet,
        model=args.model,
        refresh_cache=args.refresh_cache
    )
    print(f"\nGenerated: {output_path}")


if __name__ == '__main__':
    main()
