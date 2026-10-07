"""
Legacy Enrichment Orchestrator

Orchestrates legacy Stage 2D enrichment using proven enrichment scripts.

Enrichment adds critical metadata that improves template population:
- PubMed API: Looks up PMIDs, PMCIDs, DOIs, and publication types
- ROR API: Adds Research Organization Registry IDs for institutions

This metadata is ESSENTIAL for:
- Accurate publication subsection categorization (S1-S15)
- Complete citations in final WCM template
- Institution identification and linking

Author: CV Parsing Pipeline
Date: November 4, 2025
"""

import json
import sys
from pathlib import Path
from typing import Any

# Add legacy scripts to path
LEGACY_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "legacy" / "stage_based_extraction" / "scripts" / "production"
sys.path.insert(0, str(LEGACY_SCRIPTS_DIR))


class LegacyEnrichmentOrchestrator:
    """
    Orchestrates enrichment of extracted data using legacy enrichment scripts.

    Runs:
    1. PubMed enrichment (publications)
    2. ROR enrichment (institutions) - if available
    """

    def __init__(self, extracted_dir: Path, output_dir: Path, verbose: bool = True):
        """
        Initialize enrichment orchestrator.

        Args:
            extracted_dir: Directory with extracted section files
            output_dir: Directory to save enriched files
            verbose: Print progress messages
        """
        self.extracted_dir = extracted_dir
        self.output_dir = output_dir
        self.verbose = verbose

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def enrich_publications(self, max_workers: int = 10) -> dict[str, Any]:
        """
        Enrich publication sections with PubMed API lookups.

        Finds all Section S files (bibliography) and enriches them with:
        - PMIDs, PMCIDs, DOIs
        - PubMed publication types (critical for subsection categorization!)

        Args:
            max_workers: Max concurrent API requests

        Returns:
            Enrichment summary dictionary
        """
        if self.verbose:
            print("="*80)
            print("PUBLICATION ENRICHMENT (PubMed API)")
            print("="*80)

        # Find all Section S files (publications)
        section_s_files = list(self.extracted_dir.glob("section_S_*_extracted.json"))
        section_s_files.extend(self.extracted_dir.glob("section_s_*_extracted.json"))

        # Also check for individual subsections if they exist
        for i in range(1, 16):
            section_s_files.extend(self.extracted_dir.glob(f"section_S{i}_*_extracted.json"))
            section_s_files.extend(self.extracted_dir.glob(f"section_s{i}_*_extracted.json"))

        section_s_files = list(set(section_s_files))  # Deduplicate

        if not section_s_files:
            if self.verbose:
                print("  ℹ️  No publication sections found to enrich")
                print()
            return {
                'status': 'no_publications',
                'files_processed': 0,
                'total_enriched': 0
            }

        if self.verbose:
            print(f"  Found {len(section_s_files)} publication file(s) to enrich")
            print()

        # Import enrichment function
        try:
            from enrich_publication_ids import enrich_publication_file
        except ImportError as e:
            if self.verbose:
                print(f"  ✗ Failed to import PubMed enricher: {e}")
                print()
            return {
                'status': 'import_error',
                'error': str(e),
                'files_processed': 0
            }

        # Enrich each file
        total_enriched = 0
        files_processed = 0

        for section_file in section_s_files:
            try:
                # Output to enriched directory
                output_file = self.output_dir / section_file.name.replace('_extracted.json', '_enriched.json')

                if self.verbose:
                    print(f"  Enriching: {section_file.name}")

                # Run enrichment
                stats = enrich_publication_file(
                    section_file,
                    output_file=output_file,
                    verbose=False,  # Suppress individual file verbosity
                    max_workers=max_workers
                )

                total_enriched += stats.get('enriched', 0)
                files_processed += 1

                if self.verbose:
                    print(f"    ✓ Enriched {stats.get('enriched', 0)}/{stats.get('total_publications', 0)} publications")
                    print()

            except Exception as e:
                if self.verbose:
                    print(f"    ✗ Error: {e}")
                    print()

        if self.verbose:
            print(f"✓ Publication enrichment complete: {total_enriched} publications enriched across {files_processed} file(s)")
            print()

        return {
            'status': 'complete',
            'files_processed': files_processed,
            'total_enriched': total_enriched
        }

    def enrich_institutions(self) -> dict[str, Any]:
        """
        Enrich institution data with ROR IDs.

        This is currently a placeholder - ROR enrichment may be implemented later.

        Returns:
            Enrichment summary dictionary
        """
        if self.verbose:
            print("="*80)
            print("INSTITUTION ENRICHMENT (ROR API)")
            print("="*80)
            print("  ℹ️  ROR enrichment not yet integrated")
            print()

        # TODO: Implement ROR enrichment when needed
        # For now, just copy extracted files to enriched dir for non-publication sections

        return {
            'status': 'not_implemented',
            'files_processed': 0
        }

    def copy_non_publication_files(self):
        """
        Copy non-publication extracted files to enriched directory.

        Non-publication sections (A, B, C, D, etc.) don't need enrichment,
        but they need to be in the enriched directory for populate_cv.py to find them.
        """
        # Find all extracted files
        all_extracted = list(self.extracted_dir.glob("section_*_extracted.json"))

        # Filter out Section S (publications) - those were already enriched
        non_pub_files = [f for f in all_extracted if not f.stem.startswith('section_S') and not f.stem.startswith('section_s')]

        for extracted_file in non_pub_files:
            # Copy to enriched directory with _enriched.json suffix
            output_file = self.output_dir / extracted_file.name.replace('_extracted.json', '_enriched.json')

            # Load and save (this also validates JSON)
            try:
                with open(extracted_file, encoding="utf-8") as f:
                    data = json.load(f)

                with open(output_file, 'w', encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

                if self.verbose:
                    section_id = data.get('section_id', 'Unknown')
                    print(f"  ✓ Copied Section {section_id}: {output_file.name}")

            except Exception as e:
                if self.verbose:
                    print(f"  ✗ Error copying {extracted_file.name}: {e}")

    def run_all_enrichment(self, max_workers: int = 10) -> dict[str, Any]:
        """
        Run all enrichment steps.

        Args:
            max_workers: Max concurrent API requests

        Returns:
            Summary dictionary
        """
        if self.verbose:
            print("="*80)
            print("LEGACY ENRICHMENT - STAGE 2D")
            print("="*80)
            print(f"Input directory: {self.extracted_dir}")
            print(f"Output directory: {self.output_dir}")
            print()

        # Step 1: Enrich publications
        pub_result = self.enrich_publications(max_workers)

        # Step 2: Enrich institutions (placeholder for now)
        inst_result = self.enrich_institutions()

        # Step 3: Copy non-publication files to enriched dir
        if self.verbose:
            print("="*80)
            print("COPYING NON-PUBLICATION SECTIONS")
            print("="*80)

        self.copy_non_publication_files()

        if self.verbose:
            print()
            print("="*80)
            print("ENRICHMENT SUMMARY")
            print("="*80)
            print(f"Publications enriched: {pub_result.get('total_enriched', 0)}")
            print(f"Publication files processed: {pub_result.get('files_processed', 0)}")
            print(f"Output directory: {self.output_dir}")
            print("="*80)
            print()

        return {
            'status': 'complete',
            'publications': pub_result,
            'institutions': inst_result,
            'output_dir': str(self.output_dir)
        }


def run_legacy_enrichment(
    extracted_dir: Path,
    output_dir: Path,
    max_workers: int = 10,
    verbose: bool = True
) -> dict[str, Any]:
    """
    Convenience function to run legacy enrichment.

    Args:
        extracted_dir: Directory with extracted files
        output_dir: Directory for enriched files
        max_workers: Max concurrent API requests
        verbose: Print progress

    Returns:
        Summary dictionary
    """
    orchestrator = LegacyEnrichmentOrchestrator(extracted_dir, output_dir, verbose)
    return orchestrator.run_all_enrichment(max_workers)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run legacy enrichment on extracted sections')
    parser.add_argument('extracted_dir', help='Directory with extracted section files')
    parser.add_argument('--output-dir', '-o', required=True, help='Output directory for enriched files')
    parser.add_argument('--max-workers', type=int, default=10, help='Max concurrent API requests')
    parser.add_argument('--quiet', action='store_true', help='Suppress progress messages')

    args = parser.parse_args()

    result = run_legacy_enrichment(
        Path(args.extracted_dir),
        Path(args.output_dir),
        max_workers=args.max_workers,
        verbose=not args.quiet
    )

    if result['status'] == 'complete':
        sys.exit(0)
    else:
        sys.exit(1)
