#!/usr/bin/env python3
"""
Example: Classify Publications Using Unified System

Demonstrates:
1. Bulk PubMed fetching with caching
2. Unified classification (PubMed + text fallback)
3. Results analysis
4. User corrections

Author: Scholar Signals CV Pipeline
Date: 2025-11-09
"""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db import make_engine
from unified_pipeline.core.publication_classification_service import (
    PublicationClassificationService,
    get_classification_accuracy,
    check_classification_drift
)


def main():
    """Example workflow."""

    print("="*80)
    print("PUBLICATION CLASSIFICATION EXAMPLE")
    print("="*80)

    # Step 1: Connect to database
    print("\n1. Connecting to database...")
    db = make_engine()
    print("   ✓ Connected")

    # Step 2: Initialize service
    print("\n2. Initializing classification service...")
    service = PublicationClassificationService(
        db_connection=db,
        api_key=None,  # Will use PUBMED_API_KEY env var if set
        classifier_version='pubs-v1.0',
        verbose=True
    )
    print("   ✓ Service initialized")

    # Step 3: Get sample publications
    print("\n3. Fetching sample publications...")

    # Example publications (mix of PMIDs and text-only)
    sample_publications = [
        {
            'id': 'pub_001',
            'pmid': '36724754',  # Real PMID - will use PubMed
            'doi': None,
            'citation_text': ''
        },
        {
            'id': 'pub_002',
            'pmid': None,  # No PMID - will use text parsing
            'doi': '10.1038/s41591-024-12345',
            'citation_text': 'Smith J, Doe A. "Machine learning in clinical diagnosis." Nature Medicine. 2024;30(4):123-135.'
        },
        {
            'id': 'pub_003',
            'pmid': None,
            'doi': '10.1101/2024.01.15.12345',  # bioRxiv preprint
            'citation_text': 'Chen L et al. "Novel imaging technique." bioRxiv 2024. doi:10.1101/2024.01.15.12345'
        },
        {
            'id': 'pub_004',
            'pmid': None,
            'doi': None,
            'citation_text': 'Johnson M. "Cardiac biomarkers in sepsis." Circulation. 2023;140(Suppl 2):A12345.'  # Abstract
        },
        {
            'id': 'pub_005',
            'pmid': '12345678',  # Example PMID (may not exist)
            'doi': None,
            'citation_text': ''
        }
    ]

    print(f"   ✓ Got {len(sample_publications)} sample publications")

    # Step 4: Classify in bulk
    print("\n4. Classifying publications...")
    print("-"*80)

    results = service.classify_publications_bulk(sample_publications)

    # Step 5: Analyze results
    print("\n5. Analyzing results...")
    print("-"*80)

    for i, (pub, result) in enumerate(zip(sample_publications, results)):
        print(f"\nPublication {i+1}:")
        print(f"  ID: {pub['id']}")
        print(f"  PMID: {pub.get('pmid', 'N/A')}")
        print(f"  Assignment: {result.assignment}")
        if result.subtype:
            print(f"  Subtype: {result.subtype}")
        print(f"  Confidence: {result.confidence_pct}%")
        if result.note:
            print(f"  Note: {result.note}")

        # Show key signals
        if result.signals:
            print(f"  Signals:")
            for key, value in result.signals.items():
                if isinstance(value, bool) and value:
                    print(f"    - {key}: {value}")
                elif isinstance(value, list) and value:
                    print(f"    - {key}: {value}")

        # Show applied rules
        if result.applied_rules:
            print(f"  Rules applied: {', '.join(result.applied_rules)}")

    # Step 6: Get performance metrics
    print("\n6. Classification Performance (last 7 days)...")
    print("-"*80)

    performance = service.get_classification_performance(days=7)
    if performance:
        for assignment, metrics in performance.items():
            print(f"\n{assignment}:")
            print(f"  Count: {metrics['count']}")
            print(f"  Avg confidence: {metrics['avg_confidence']:.1f}%")
            print(f"  Range: {metrics['min_confidence']}-{metrics['max_confidence']}%")
            print(f"  Low confidence: {metrics['low_confidence_count']}")
            print(f"  User corrections: {metrics['correction_count']}")
    else:
        print("  (No recent classifications)")

    # Step 7: Example user correction
    print("\n7. Example: Recording user correction...")
    print("-"*80)

    # Suppose user says pub_002 should be S2 (Review) instead of S1
    service.record_user_correction(
        publication_id='pub_002',
        correct_assignment='S2',
        reason='Actually a review article, not original research',
        user_id='example_user'
    )

    # Step 8: Check for confidence drift
    print("\n8. Checking for confidence drift...")
    print("-"*80)

    has_drift = check_classification_drift(
        db_connection=db,
        classifier_version='pubs-v1.0',
        threshold=0.70
    )

    if has_drift:
        print("  ⚠️ Confidence drift detected!")
    else:
        print("  ✓ No drift detected")

    # Step 9: Get accuracy metrics
    print("\n9. Classification Accuracy (based on corrections)...")
    print("-"*80)

    accuracy = get_classification_accuracy(db, classifier_version='pubs-v1.0')
    if accuracy:
        for assignment, metrics in accuracy.items():
            if metrics['total'] > 0:
                print(f"\n{assignment}:")
                print(f"  Total: {metrics['total']}")
                print(f"  Correct: {metrics['correct']}")
                print(f"  Corrected: {metrics['corrected']}")
                print(f"  Accuracy: {metrics['accuracy_pct']:.1f}%")
    else:
        print("  (Not enough data yet)")

    print("\n" + "="*80)
    print("EXAMPLE COMPLETE")
    print("="*80)

    # Cleanup
    db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
