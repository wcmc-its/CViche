#!/usr/bin/env python3
"""
Backfill PubMed Cache

Fetch and cache PubMed records for publications that have PMIDs but no cache entry.
Run this once to populate cache for existing publications, then run periodically
to refresh stale records.

Usage:
    python backfill_pubmed_cache.py --limit 1000      # Test with 1000 PMIDs
    python backfill_pubmed_cache.py --all             # Backfill all PMIDs
    python backfill_pubmed_cache.py --refresh-stale   # Refresh records older than 6 months

Author: Scholar Signals CV Pipeline
Date: 2025-11-09
"""

import os
import sys
import time
import argparse
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db import make_engine
from unified_pipeline.core.bulk_pubmed_fetcher import BulkPubMedFetcher, get_cache_stats


def backfill_missing_pmids(db, limit: int = None):
    """
    Fetch and cache PubMed records for publications with PMIDs but no cache.

    Args:
        db: Database connection
        limit: Max PMIDs to backfill (None = all)
    """
    print(f"\n{'='*80}")
    print(f"BACKFILL MISSING PUBMED RECORDS")
    print(f"{'='*80}\n")

    # Find publications with PMIDs but no cache
    query = """
        SELECT DISTINCT p.pmid
        FROM publications p
        LEFT JOIN pubmed_cache c ON p.pmid = c.pmid
        WHERE p.pmid IS NOT NULL
          AND p.pmid != ''
          AND c.pmid IS NULL
    """
    if limit:
        query += f" LIMIT {limit}"

    pmids = [row[0] for row in db.execute(query).fetchall()]

    if not pmids:
        print("✓ No PMIDs need backfilling")
        return

    print(f"📋 Found {len(pmids)} PMIDs to backfill\n")

    # Bulk fetch in batches
    fetcher = BulkPubMedFetcher(db_connection=db, verbose=True)

    batch_size = 200
    total_batches = (len(pmids) - 1) // batch_size + 1

    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i+batch_size]
        batch_num = i // batch_size + 1

        print(f"\n{'='*80}")
        print(f"Batch {batch_num}/{total_batches}")
        print(f"{'='*80}")

        records = fetcher.fetch_records_bulk(batch)

        print(f"✓ Cached {len(records)}/{len(batch)} records")

        # Be nice to PubMed (rate limiting already handled by fetcher)
        if i + batch_size < len(pmids):
            time.sleep(0.5)

    print(f"\n{'='*80}")
    print(f"BACKFILL COMPLETE")
    print(f"{'='*80}")
    print(f"Total PMIDs processed: {len(pmids)}")
    print(f"{'='*80}\n")


def refresh_stale_records(db, age_days: int = 180, limit: int = None):
    """
    Refresh PubMed cache records older than specified age.

    Args:
        db: Database connection
        age_days: Age threshold in days (default: 180 = 6 months)
        limit: Max records to refresh (None = all)
    """
    print(f"\n{'='*80}")
    print(f"REFRESH STALE PUBMED RECORDS")
    print(f"{'='*80}\n")

    # Find stale records
    query = f"""
        SELECT pmid
        FROM pubmed_cache
        WHERE fetched_at < datetime('now', '-{age_days} days')
        ORDER BY fetched_at ASC
    """
    if limit:
        query += f" LIMIT {limit}"

    pmids = [row[0] for row in db.execute(query).fetchall()]

    if not pmids:
        print(f"✓ No records older than {age_days} days found")
        return

    print(f"📋 Found {len(pmids)} stale records (>{age_days} days old)\n")

    # Fetch with force_refresh=True
    fetcher = BulkPubMedFetcher(db_connection=db, verbose=True)
    records = fetcher.fetch_records_bulk(pmids, force_refresh=True)

    print(f"\n{'='*80}")
    print(f"REFRESH COMPLETE")
    print(f"{'='*80}")
    print(f"Total PMIDs refreshed: {len(records)}")
    print(f"{'='*80}\n")


def print_cache_statistics(db):
    """Print current cache statistics."""
    print(f"\n{'='*80}")
    print(f"PUBMED CACHE STATISTICS")
    print(f"{'='*80}\n")

    stats = get_cache_stats(db)

    if stats:
        print(f"Total records:      {stats.get('total_records', 0):,}")
        print(f"Fresh (<30 days):   {stats.get('fresh_30d', 0):,} ({stats.get('cache_hit_rate_30d', 0):.1f}%)")
        print(f"Medium (30-180d):   {stats.get('medium_30_180d', 0):,}")
        print(f"Stale (>180 days):  {stats.get('stale_180d', 0):,}")
        print(f"Oldest record:      {stats.get('oldest_record', 'N/A')}")
        print(f"Newest record:      {stats.get('newest_record', 'N/A')}")
    else:
        print("No cache statistics available")

    print(f"{'='*80}\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill PubMed cache for existing publications"
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Max PMIDs to process (for testing)'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Backfill all missing PMIDs'
    )
    parser.add_argument(
        '--refresh-stale',
        action='store_true',
        help='Refresh records older than 180 days'
    )
    parser.add_argument(
        '--age-days',
        type=int,
        default=180,
        help='Age threshold for --refresh-stale (default: 180)'
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Show cache statistics only'
    )

    args = parser.parse_args()

    # Connect to database
    try:
        db = make_engine()
        print("✓ Connected to database")
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        sys.exit(1)

    try:
        # Show stats
        if args.stats or (not args.all and not args.refresh_stale):
            print_cache_statistics(db)

        # Backfill missing
        if args.all or (args.limit and not args.refresh_stale):
            backfill_missing_pmids(db, limit=args.limit)

        # Refresh stale
        if args.refresh_stale:
            refresh_stale_records(db, age_days=args.age_days, limit=args.limit)

        # Show final stats
        if args.all or args.refresh_stale:
            print_cache_statistics(db)

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
