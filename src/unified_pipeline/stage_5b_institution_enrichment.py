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
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# The implementation was split into unified_pipeline/stage5b/ (#523). Every
# name that used to live in this module is re-exported below so existing
# imports keep working -- the surface is pinned by
# tests/test_stage5b_import_surface.py, per the stage 6 split precedent (#500).
from unified_pipeline.stage5b import cache
from unified_pipeline.stage5b.cache import (  # noqa: F401  (re-exports)
    CACHE_FILE,
    OLD_CACHE_FILE,
    _institution_cache_key,
    _owner_context_hash,
    load_institution_cache,
    save_institution_cache,
)
from unified_pipeline.stage5b.lookup import (  # noqa: F401  (re-exports)
    INSTITUTION_SYSTEM_PROMPT,
    _build_context_string,
    _build_owner_context,
    lookup_institutions_llm,
)
from unified_pipeline.stage5b.normalize import (  # noqa: F401  (re-exports)
    format_location,
    is_likely_internal_unit,
    normalize_institution_name,
)

# Paths
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_5b_institution_enrichment"

# Taxonomy codes that need institution location enrichment
INSTITUTION_CODES = ['B1', 'B2', 'C', 'C1', 'C2', 'D1', 'D2', 'D3']

# LLM batch size
BATCH_SIZE = 10


def __getattr__(name: str):
    """Serve INSTITUTION_CACHE from the cache module's CURRENT binding.

    load_institution_cache() REASSIGNS the global (``global`` + rebind), so a
    static ``from ...cache import INSTITUTION_CACHE`` alias here would freeze
    the pre-load dict and silently split state between the old and new import
    paths (the #496 lesson). PEP 562 delegation always returns the live one.
    """
    if name == "INSTITUTION_CACHE":
        return cache.INSTITUTION_CACHE
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def enrich_entry_with_result(entry: dict, result: dict) -> dict:
    """
    Apply a pre-looked-up institution result to an entry.

    Mutates `entry` in place (and its nested `extracted_fields`/`enriched_fields`)
    and also returns it -- callers that need an unmodified copy of the input
    must copy.deepcopy() it first.

    Args:
        entry: Entry dictionary with extracted_fields
        result: LLM result dict with cleaned_name, official_name, city, state, country, country_code

    Returns:
        The same entry dict, with enriched location data added
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
        logger.info("%s", '=' * 60)
        logger.info("Stage 5b: Institution Enrichment (LLM) - %s", document_uid)
        logger.info("%s", '=' * 60)
        logger.info("Model: %s", model)

    # Load cv_owner_location from Stage 4 if not in input
    if not cv_owner_location or not cv_owner_location.get('inference_success'):
        stage4_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"
        # Extract base UID (without stage suffixes)
        base_uid = document_uid.replace('_enriched', '').replace('_institution_enriched', '')
        candidates = sorted(stage4_dir.glob(f"*{base_uid}*_fields.json"))
        if not candidates:
            # Try broader match
            parts = base_uid.split('_', 1)
            if len(parts) > 1:
                candidates = sorted(stage4_dir.glob(f"{parts[0]}*_fields.json"))
        if candidates:
            if len(candidates) > 1:
                logger.warning(
                    "Ambiguous Stage 4 context match for %s: %d candidates, using %s",
                    base_uid, len(candidates), candidates[0].name,
                )
            try:
                with open(candidates[0], 'r') as f:
                    stage4_data = json.load(f)
                cv_owner_location = stage4_data.get('cv_owner_location')
                if cv_owner_location and cv_owner_location.get('inference_success') and verbose:
                    logger.info("Loaded cv_owner_location from Stage 4 output")
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Could not load Stage 4 context from %s: %s", candidates[0], e)

    if verbose:
        if cv_owner_location and cv_owner_location.get('inference_success'):
            metro = cv_owner_location.get('metro_area', '')
            primary = cv_owner_location.get('primary_location') or {}
            if not isinstance(primary, dict):
                primary = {}
            logger.info(
                "CV owner context: %s (%s, %s)",
                metro, primary.get('city', ''), primary.get('state', ''),
            )
        else:
            logger.info("CV owner context: not available")

    # Institutions are disambiguated using this owner context (see
    # INSTITUTION_SYSTEM_PROMPT rule 3), so it has to be part of the cache
    # key, not just part of the prompt -- see _institution_cache_key (#582).
    owner_context = _build_owner_context(cv_owner_location)

    # Count entries by code
    code_counts = {}
    for entry in entries:
        code = entry.get('taxonomy_code', 'T')
        code_counts[code] = code_counts.get(code, 0) + 1

    institution_entries = sum(
        code_counts.get(code, 0) for code in INSTITUTION_CODES
    )

    if verbose:
        logger.info("Total entries: %d", len(entries))
        logger.info("Entries needing institution lookup: %d", institution_entries)
        logger.info("  (Codes: %s)", ', '.join(INSTITUTION_CODES))
        if refresh_cache:
            logger.info("  ** Refresh mode: ignoring cached entries **")

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
        # Owner context is folded in so one CV owner's disambiguation can't
        # be served to a different owner (#582).
        cache_key = _institution_cache_key(normalize_institution_name(institution), owner_context)
        # Also check the key built from the raw (non-normalized) name. Both
        # already fold in owner_context via _institution_cache_key above, so
        # this can't reintroduce #582's cross-owner leak -- it only guards
        # against normalize_institution_name changing between when an entry
        # was cached and when it's looked up again.
        raw_key = _institution_cache_key(institution, owner_context)
        entry_to_cache_key[idx] = cache_key

        # Check cache (skip if refreshing)
        if not refresh_cache:
            found, cached = cache.lookup(cache_key, raw_key)
            if found:
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
    needs_lookup_indices = {i for info in institutions_to_lookup.values() for i in info['entry_indices']}
    cached_count = sum(1 for idx in entry_to_cache_key if idx not in needs_lookup_indices)

    if verbose:
        logger.info("Cache hits: %d", cached_count)
        logger.info("Institutions needing LLM lookup: %d", uncached_count)

    # Batch LLM lookups
    total_cost = 0.0
    observed_model = None
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
            logger.info("LLM batches: %d (batch size: %d)", len(batches), BATCH_SIZE)

        for batch_idx, batch in enumerate(batches):
            # Prepare for LLM call
            llm_batch = [(inst_id, name, ctx) for inst_id, name, ctx, _, _ in batch]

            results, cost, call_model = lookup_institutions_llm(
                llm_batch,
                cv_owner_location,
                model=model,
                verbose=verbose
            )
            total_cost += cost
            observed_model = call_model or observed_model
            llm_calls += 1

            # None means the LLM call itself failed — don't cache anything
            if results is None:
                if verbose:
                    logger.info("Batch failed — skipping cache writes for %d institutions", len(batch))
                continue

            # Process results (LLM call succeeded)
            for inst_id, name, ctx, cache_key, info in batch:
                result = results.get(inst_id, {})
                if result:
                    # Cache the result
                    cache.set_cached(cache_key, {
                        'cleaned_name': result.get('cleaned_name', ''),
                        'official_name': result.get('official_name', ''),
                        'city': result.get('city', ''),
                        'state': result.get('state', ''),
                        'country': result.get('country', ''),
                        'country_code': result.get('country_code', ''),
                        'source': 'llm'
                    })

                    # Apply to all entries that share this institution
                    for entry_idx in info['entry_indices']:
                        entries[entry_idx] = enrich_entry_with_result(entries[entry_idx], result)

                    if verbose:
                        city = result.get('city', '')
                        state = result.get('state', '')
                        logger.info("%-40s → %s, %s", name[:40], city, state)
                else:
                    # LLM succeeded but didn't return this institution — safe to cache negative
                    cache.set_cached(cache_key, None)
                    if verbose:
                        logger.info("%-40s → (no result)", name[:40])

    # Save cache
    save_institution_cache()

    # Count enriched entries
    enriched_count = sum(
        1 for entry in entries
        if entry.get('taxonomy_code', '') in INSTITUTION_CODES
        and entry.get('institution_enrichment')
    )

    if verbose:
        logger.info("Institutions enriched: %d/%d", enriched_count, institution_entries)
        logger.info("LLM calls: %d", llm_calls)
        logger.info("Total cost: $%.4f", total_cost)

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
        'model': observed_model or model,
        'timestamp': datetime.now().isoformat()
    }

    # Determine output path
    if output_path is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(OUTPUT_DIR / f"{document_uid}_institution_enriched.json")

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if verbose:
        logger.info("Saved to: %s", output_path)

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
            logger.error("Could not find input file: %s", args.input)
            sys.exit(1)

    output_path = run_stage5b(
        input_path, args.output,
        verbose=not args.quiet,
        model=args.model,
        refresh_cache=args.refresh_cache
    )
    logger.info("Generated: %s", output_path)


if __name__ == '__main__':
    main()
