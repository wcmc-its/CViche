#!/usr/bin/env python3
"""
Publication Classification Service

High-level API for classifying publications in bulk.
Integrates BulkPubMedFetcher + Unified Classifier + History tracking.

Author: Scholar Signals CV Pipeline
Date: 2025-11-09
"""

import hashlib
import json
from typing import List, Dict, Optional
from datetime import datetime

from .bulk_pubmed_fetcher import BulkPubMedFetcher
from .unified_publication_classifier import classify_publication_any, ClassificationResult


class PublicationClassificationService:
    """
    Service for bulk publication classification with caching and history tracking.

    Features:
    - Bulk PubMed fetching with caching
    - Unified classification (PubMed + text fallback)
    - Classification history tracking
    - Performance monitoring

    Usage:
        service = PublicationClassificationService(db_connection=db)
        results = service.classify_publications_bulk(publications)
    """

    def __init__(self, db_connection, api_key: str = None, classifier_version: str = "pubs-v1.0", verbose: bool = True):
        """
        Initialize the service.

        Args:
            db_connection: Database connection
            api_key: NCBI E-utilities API key (optional)
            classifier_version: Version tag for tracking
            verbose: Print progress messages
        """
        self.db = db_connection
        self.classifier_version = classifier_version
        self.verbose = verbose

        # Initialize components
        self.fetcher = BulkPubMedFetcher(
            api_key=api_key,
            db_connection=db_connection,
            verbose=verbose
        )

    def classify_publications_bulk(self, publications: list[dict]) -> list[ClassificationResult]:
        """
        Classify a batch of publications efficiently.

        Strategy:
        1. Collect all PMIDs
        2. Bulk-fetch PubMed records (from cache or API)
        3. Classify each publication using unified logic
        4. Store classification history
        5. Return results

        Args:
            publications: List of publication dicts with keys:
                - id: publication ID (required)
                - pmid: PMID (optional)
                - doi: DOI (optional)
                - citation_text: raw citation string (optional)

        Returns:
            List of ClassificationResult objects
        """
        if not publications:
            return []

        if self.verbose:
            print(f"\n{'='*80}")
            print(f"BULK PUBLICATION CLASSIFICATION")
            print(f"{'='*80}")
            print(f"Publications: {len(publications)}")
            print(f"Classifier version: {self.classifier_version}")
            print(f"{'='*80}\n")

        # Step 1: Collect PMIDs
        pmids = [p['pmid'] for p in publications if p.get('pmid')]

        # Step 2: Bulk fetch PubMed records (uses cache automatically)
        pubmed_records = {}
        if pmids:
            if self.verbose:
                print(f"📚 Fetching PubMed records for {len(pmids)} publications...")
            pubmed_records = self.fetcher.fetch_records_bulk(pmids)

        # Step 3: Classify each publication
        if self.verbose:
            print(f"\n🔍 Classifying {len(publications)} publications...")

        results = []
        for i, pub in enumerate(publications):
            if self.verbose and (i + 1) % 50 == 0:
                print(f"  Progress: {i + 1}/{len(publications)}")

            # Build input payload
            input_payload = {
                'pmid': pub.get('pmid'),
                'pmcid': pub.get('pmcid'),
                'doi': pub.get('doi'),
                'citation_text': pub.get('citation_text', ''),
                'pubmed_record': pubmed_records.get(pub.get('pmid'))
            }

            # Classify
            result = classify_publication_any(input_payload)

            # Step 4: Store in history
            self._store_classification_history(
                publication_id=pub['id'],
                input_payload=input_payload,
                result=result
            )

            results.append(result)

        # Step 5: Report statistics
        if self.verbose:
            self._print_classification_stats(results)

        return results

    def _store_classification_history(self, publication_id: str, input_payload: dict, result: ClassificationResult):
        """
        Record classification in history table.

        Args:
            publication_id: Publication ID
            input_payload: Input data used for classification
            result: Classification result
        """
        try:
            # Determine input type
            if input_payload.get('pubmed_record'):
                input_type = 'pubmed_rich'
            elif input_payload.get('doi'):
                input_type = 'doi_only'
            else:
                input_type = 'text_only'

            # Compute hash of input (to detect changes)
            input_hash = hashlib.sha256(
                json.dumps(input_payload, sort_keys=True).encode()
            ).hexdigest()

            # Insert into history
            self.db.execute("""
                INSERT INTO publication_classification_history
                (publication_id, pmid, input_type, input_hash,
                 assignment, subtype, confidence_pct,
                 classifier_version, signals_json, applied_rules, note,
                 classified_at, classified_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                publication_id,
                input_payload.get('pmid'),
                input_type,
                input_hash,
                result.assignment,
                result.subtype,
                result.confidence_pct,
                self.classifier_version,
                json.dumps(result.signals) if result.signals else None,
                json.dumps(result.applied_rules) if result.applied_rules else None,
                result.note,
                datetime.now().isoformat(),
                'system'
            ])

            self.db.commit()

        except Exception as e:
            if self.verbose:
                print(f"  ⚠️  History storage error: {e}")
            # Don't fail classification if history fails

    def _print_classification_stats(self, results: list[ClassificationResult]):
        """Print summary statistics."""
        print(f"\n{'='*80}")
        print(f"CLASSIFICATION RESULTS")
        print(f"{'='*80}")

        # Count by assignment
        assignment_counts = {}
        for r in results:
            assignment_counts[r.assignment] = assignment_counts.get(r.assignment, 0) + 1

        # Count by confidence range
        high_conf = sum(1 for r in results if r.confidence_pct >= 80)
        med_conf = sum(1 for r in results if 60 <= r.confidence_pct < 80)
        low_conf = sum(1 for r in results if r.confidence_pct < 60)

        # Average confidence
        avg_conf = sum(r.confidence_pct for r in results) / len(results) if results else 0

        print(f"\nBy Assignment:")
        for assignment, count in sorted(assignment_counts.items()):
            pct = count / len(results) * 100
            print(f"  {assignment:40s}: {count:4d} ({pct:5.1f}%)")

        print(f"\nBy Confidence:")
        print(f"  High (≥80%):    {high_conf:4d} ({high_conf/len(results)*100:5.1f}%)")
        print(f"  Medium (60-79%): {med_conf:4d} ({med_conf/len(results)*100:5.1f}%)")
        print(f"  Low (<60%):     {low_conf:4d} ({low_conf/len(results)*100:5.1f}%)")
        print(f"  Average:        {avg_conf:5.1f}%")

        print(f"{'='*80}\n")

    def get_classification_performance(self, days: int = 7) -> dict:
        """
        Get classification performance metrics for the last N days.

        Args:
            days: Number of days to look back

        Returns:
            Dict with performance metrics
        """
        try:
            result = self.db.execute(f"""
                SELECT
                    assignment,
                    COUNT(*) as count,
                    AVG(confidence_pct) as avg_confidence,
                    MIN(confidence_pct) as min_confidence,
                    MAX(confidence_pct) as max_confidence,
                    COUNT(CASE WHEN confidence_pct < 60 THEN 1 END) as low_confidence_count,
                    COUNT(CASE WHEN corrected_to IS NOT NULL THEN 1 END) as correction_count
                FROM publication_classification_history
                WHERE classified_at > datetime('now', '-{days} days')
                  AND classifier_version = ?
                GROUP BY assignment
                ORDER BY count DESC
            """, [self.classifier_version]).fetchall()

            performance = {}
            for row in result:
                performance[row[0]] = {
                    'count': row[1],
                    'avg_confidence': row[2],
                    'min_confidence': row[3],
                    'max_confidence': row[4],
                    'low_confidence_count': row[5],
                    'correction_count': row[6]
                }

            return performance

        except Exception as e:
            print(f"Error getting performance metrics: {e}")
            return {}

    def record_user_correction(self, publication_id: str, correct_assignment: str, reason: str = None, user_id: str = "user"):
        """
        Record a user correction of a classification.

        Args:
            publication_id: Publication ID
            correct_assignment: What it should be (e.g., "S2")
            reason: Optional reason for correction
            user_id: User who made correction
        """
        try:
            # Find most recent classification for this publication
            recent = self.db.execute("""
                SELECT id, assignment
                FROM publication_classification_history
                WHERE publication_id = ?
                ORDER BY classified_at DESC
                LIMIT 1
            """, [publication_id]).fetchone()

            if not recent:
                print(f"No classification found for publication {publication_id}")
                return

            # Update with correction
            self.db.execute("""
                UPDATE publication_classification_history
                SET corrected_to = ?,
                    corrected_at = CURRENT_TIMESTAMP,
                    correction_reason = ?,
                    corrected_by = ?
                WHERE id = ?
            """, [correct_assignment, reason, user_id, recent[0]])

            self.db.commit()

            if self.verbose:
                print(f"✓ Recorded correction: {recent[1]} → {correct_assignment}")

        except Exception as e:
            print(f"Error recording correction: {e}")


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_classification_accuracy(db_connection, classifier_version: str) -> dict:
    """
    Calculate classification accuracy based on user corrections.

    Args:
        db_connection: Database connection
        classifier_version: Version to analyze

    Returns:
        Dict with accuracy metrics
    """
    try:
        result = db_connection.execute("""
            SELECT
                assignment,
                COUNT(*) as total,
                COUNT(CASE WHEN corrected_to IS NULL THEN 1 END) as correct,
                COUNT(CASE WHEN corrected_to IS NOT NULL THEN 1 END) as corrected
            FROM publication_classification_history
            WHERE classifier_version = ?
              AND classified_at > datetime('now', '-30 days')
            GROUP BY assignment
        """, [classifier_version]).fetchall()

        accuracy = {}
        for row in result:
            assignment = row[0]
            total = row[1]
            correct = row[2]
            corrected = row[3]

            accuracy[assignment] = {
                'total': total,
                'correct': correct,
                'corrected': corrected,
                'accuracy_pct': (correct / total * 100) if total > 0 else 0
            }

        return accuracy

    except Exception as e:
        print(f"Error calculating accuracy: {e}")
        return {}


def check_classification_drift(db_connection, classifier_version: str, threshold: float = 0.70) -> bool:
    """
    Alert if average confidence has dropped below threshold.

    Args:
        db_connection: Database connection
        classifier_version: Version to check
        threshold: Minimum acceptable average confidence (0-1)

    Returns:
        True if drift detected, False otherwise
    """
    try:
        result = db_connection.execute("""
            SELECT AVG(confidence_pct) / 100.0 as avg_confidence
            FROM publication_classification_history
            WHERE classifier_version = ?
              AND classified_at > datetime('now', '-7 days')
        """, [classifier_version]).fetchone()

        if result and result[0] is not None:
            avg_confidence = result[0]
            if avg_confidence < threshold:
                print(f"⚠️  DRIFT ALERT: Average confidence dropped to {avg_confidence*100:.1f}%")
                return True

        return False

    except Exception as e:
        print(f"Error checking drift: {e}")
        return False
