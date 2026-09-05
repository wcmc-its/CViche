"""
Legacy Extractor Orchestrator

Orchestrates legacy Stage 2C extraction using the 71 specialized extractor scripts.

This module bridges the unified pipeline (Stages 1-2) to the proven legacy extraction
system that has WCM-specific prompts and optimizations for each section.

Flow:
    1. Unified Stage 1: Segmentation → segmented.json
    2. Unified Stage 2: Taxonomy Mapping → mapped.json
    3. Convert to classified.json (this enables legacy extractors)
    4. **Run legacy extractors** (this module) → extracted files
    5. Run legacy enrichment → enriched files
    6. Run legacy populate_cv.py → WCM template

Author: CV Parsing Pipeline
Date: November 4, 2025
"""

import json
import sys
import asyncio
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util

# Add legacy scripts to path
LEGACY_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "legacy" / "stage_based_extraction" / "scripts" / "production"
sys.path.insert(0, str(LEGACY_SCRIPTS_DIR))


# Map wcm_section_type → list of section IDs to extract
# This determines which extractors run for each type
WCM_TYPE_TO_SECTIONS = {
    'personal_data': ['a'],  # Section A: Personal Data
    'education': ['b1', 'b2', 'b3'],  # Section B: Education
    'certifications': ['c'],  # Section C: Certifications
    'positions': ['d1', 'd2', 'd3', 'd4'],  # Section D: Positions
    'honors': ['i'],  # Section I: Honors & Awards
    'teaching': ['k1', 'k2', 'k3', 'k4', 'k5', 'k6', 'k7', 'k8', 'k9'],  # Section K: Educational Activities
    'service': ['o'],  # Section O: Service
    'memberships': ['p'],  # Section P: Professional Memberships
    'presentations': ['q1', 'q2', 'q3', 'q4', 'q5'],  # Section Q: Presentations
    'research': ['m', 'm1', 'm2', 'm3', 'm4'],  # Section M: Research
    'grants': ['m', 'm1', 'm2', 'm3', 'm4'],  # Grants are part of Section M
    'mentoring': ['n1', 'n2', 'n3', 'n4', 'n5', 'n6'],  # Section N: Mentoring
    'bibliography': ['s', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9', 's10', 's11', 's12', 's13', 's14', 's15'],  # Section S: Publications
}

# Special function name mappings for sections with non-standard naming
SPECIAL_FUNCTION_NAMES = {
    's': 'extract_publications_from_cv',  # Section S uses publications not s
    'm': 'extract_research_from_cv',      # Section M uses research not m
    'd1': 'extract_positions_from_cv',     # Section D1 uses positions not d1
}


class LegacyExtractorOrchestrator:
    """
    Orchestrates the execution of legacy extraction scripts.

    Determines which extractors to run based on wcm_section_type in classified data,
    then runs them in parallel with proper error handling.
    """

    def __init__(self, classified_file: Path, output_dir: Path, verbose: bool = True):
        """
        Initialize orchestrator.

        Args:
            classified_file: Path to classified.json (from unified→classified converter)
            output_dir: Directory to save extracted results
            verbose: Print progress messages
        """
        self.classified_file = classified_file
        self.output_dir = output_dir
        self.verbose = verbose

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Load classified data
        with open(classified_file) as f:
            self.classified_data = json.load(f)

        self.cv_id = self._extract_cv_id()

    def _extract_cv_id(self) -> str:
        """Extract CV ID from classified data or filename."""
        # Try document_uid first
        cv_id = self.classified_data.get('document_uid', '')

        # If not found, use filename
        if not cv_id:
            cv_id = self.classified_file.stem.replace('_classified', '')

        # Ensure it starts with CV_
        if not cv_id.startswith('CV_'):
            cv_id = f"CV_{cv_id}"

        return cv_id

    def determine_extractors_to_run(self) -> set[str]:
        """
        Determine which extractors to run based on wcm_section_types in classified data.

        Returns:
            Set of section IDs (lowercase, e.g., {'a', 's1', 's2', 'm1', ...})
        """
        section_ids = set()

        # Recursively find all wcm_section_types
        def find_types(group: dict) -> set[str]:
            types = set()
            wcm_type = group.get('wcm_section_type')
            if wcm_type:
                types.add(wcm_type)
            for subgroup in group.get('subgroups', []):
                types.update(find_types(subgroup))
            return types

        wcm_types = set()
        for group in self.classified_data.get('groups', []):
            wcm_types.update(find_types(group))

        if self.verbose:
            print(f"Found wcm_section_types: {sorted(wcm_types)}")

        # Map types to section IDs
        for wcm_type in wcm_types:
            sections = WCM_TYPE_TO_SECTIONS.get(wcm_type, [])
            section_ids.update(sections)

        # Always run Section A (personal data) if we have any data
        if self.classified_data.get('groups'):
            section_ids.add('a')

        if self.verbose:
            print(f"Will run extractors for sections: {sorted(section_ids)}")

        return section_ids

    def _has_relevant_data(self, section_id: str) -> bool:
        """
        Quick check if section has any relevant data in classified file.
        Avoids expensive LLM calls for empty sections.

        Returns:
            True if section might have data, False if definitely empty
        """
        # Map section_id to wcm_section_type
        for wcm_type, sections in WCM_TYPE_TO_SECTIONS.items():
            if section_id in sections:
                # Check if any group has this wcm_section_type
                def has_type(group):
                    if group.get('wcm_section_type') == wcm_type:
                        # Check if it has entries
                        if group.get('entries'):
                            return True
                    for subgroup in group.get('subgroups', []):
                        if has_type(subgroup):
                            return True
                    return False

                for group in self.classified_data.get('groups', []):
                    if has_type(group):
                        return True

        return False

    def _run_extractor(self, section_id: str) -> dict[str, Any]:
        """
        Run a single extractor script.

        Args:
            section_id: Section ID (lowercase, e.g., 'a', 's1', 'm2')

        Returns:
            Result dictionary with status and extracted data
        """
        result = {
            'section_id': section_id,
            'status': 'pending',
            'output_file': None,
            'error': None
        }

        try:
            # Early exit: Check if section has any relevant data
            # This avoids expensive LLM calls for empty sections
            if not self._has_relevant_data(section_id):
                result['status'] = 'no_data'
                if self.verbose:
                    print(f"    ⊘ Section {section_id.upper()}: No relevant data (skipped)")
                return result

            # Import the extractor module
            extractor_file = LEGACY_SCRIPTS_DIR / f"extract_section_{section_id}.py"

            if not extractor_file.exists():
                result['status'] = 'extractor_not_found'
                result['error'] = f"Extractor script not found: {extractor_file.name}"
                return result

            # Dynamically import the module
            spec = importlib.util.spec_from_file_location(f"extract_section_{section_id}", extractor_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the extraction function (pattern: extract_{section_id}_from_cv)
            # Check for special function names first
            if section_id in SPECIAL_FUNCTION_NAMES:
                func_name = SPECIAL_FUNCTION_NAMES[section_id]
            else:
                func_name = f"extract_{section_id}_from_cv"

            if not hasattr(module, func_name):
                # Try alternative pattern for subsections
                func_name = f"extract_{section_id.upper()}_from_cv"
                if not hasattr(module, func_name):
                    result['status'] = 'function_not_found'
                    result['error'] = f"Function {func_name} not found in {extractor_file.name}"
                    return result

            extract_func = getattr(module, func_name)

            # Call the extraction function
            if self.verbose:
                print(f"  Running extractor for Section {section_id.upper()}...")

            extracted_data = extract_func(
                classified_file=self.classified_file,
                verbose=False,  # Suppress individual extractor verbosity
                model="gpt-4o-mini"
            )

            if extracted_data:
                # Save extracted data
                output_file = self.output_dir / f"section_{section_id.upper()}_{self.cv_id}_extracted.json"
                with open(output_file, 'w') as f:
                    json.dump(extracted_data, f, indent=2)

                result['status'] = 'success'
                result['output_file'] = str(output_file)
                result['num_entries'] = extracted_data.get('num_entries', 0)

                if self.verbose:
                    print(f"    ✓ Section {section_id.upper()}: {result['num_entries']} entries extracted")

            else:
                result['status'] = 'no_data'
                if self.verbose:
                    print(f"    ℹ️  Section {section_id.upper()}: No relevant data found")

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)
            if self.verbose:
                print(f"    ✗ Section {section_id.upper()}: Error - {str(e)}")

        return result

    def run_all_extractors(self, max_workers: int = 5) -> dict[str, Any]:
        """
        Run all relevant extractors in parallel.

        Args:
            max_workers: Maximum number of concurrent extractors

        Returns:
            Summary dictionary with results from all extractors
        """
        if self.verbose:
            print("="*80)
            print("LEGACY EXTRACTORS - STAGE 2C")
            print("="*80)
            print(f"CV ID: {self.cv_id}")
            print(f"Classified file: {self.classified_file.name}")
            print(f"Output directory: {self.output_dir}")
            print()

        # Determine which extractors to run
        section_ids = self.determine_extractors_to_run()

        if not section_ids:
            if self.verbose:
                print("⚠️  No extractors to run (no recognized wcm_section_types)")
            return {
                'status': 'no_extractors',
                'sections_processed': 0,
                'sections_extracted': 0,
                'results': []
            }

        if self.verbose:
            print(f"Running {len(section_ids)} extractors in parallel (max {max_workers} concurrent)...")
            print()

        # Run extractors in parallel
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_section = {
                executor.submit(self._run_extractor, section_id): section_id
                for section_id in section_ids
            }

            for future in as_completed(future_to_section):
                result = future.result()
                results.append(result)

        # Summarize results
        successful = [r for r in results if r['status'] == 'success']
        no_data = [r for r in results if r['status'] == 'no_data']
        errors = [r for r in results if r['status'] not in ('success', 'no_data')]

        if self.verbose:
            print()
            print("="*80)
            print("EXTRACTION SUMMARY")
            print("="*80)
            print(f"Sections processed: {len(results)}")
            print(f"Successfully extracted: {len(successful)}")
            print(f"No data found: {len(no_data)}")
            print(f"Errors: {len(errors)}")

            if successful:
                print()
                print("Extracted sections:")
                for r in successful:
                    print(f"  ✓ Section {r['section_id'].upper()}: {r.get('num_entries', 0)} entries")

            if errors:
                print()
                print("Errors:")
                for r in errors:
                    print(f"  ✗ Section {r['section_id'].upper()}: {r.get('error', 'Unknown error')}")

            print()

        return {
            'status': 'complete',
            'cv_id': self.cv_id,
            'sections_processed': len(results),
            'sections_extracted': len(successful),
            'sections_with_no_data': len(no_data),
            'sections_with_errors': len(errors),
            'output_dir': str(self.output_dir),
            'results': results
        }

    async def run_all_extractors_async(self, max_workers: int = 10) -> dict[str, Any]:
        """
        Async wrapper for run_all_extractors.

        Runs extractors in thread pool to avoid blocking async event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run_all_extractors, max_workers)


def run_legacy_extractors(
    classified_file: Path,
    output_dir: Path,
    max_workers: int = 10,
    verbose: bool = True
) -> dict[str, Any]:
    """
    Convenience function to run legacy extractors.

    Args:
        classified_file: Path to classified.json
        output_dir: Directory for extracted files
        max_workers: Maximum concurrent extractors
        verbose: Print progress

    Returns:
        Summary dictionary
    """
    orchestrator = LegacyExtractorOrchestrator(classified_file, output_dir, verbose)
    return orchestrator.run_all_extractors(max_workers)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run legacy extraction on classified CV')
    parser.add_argument('classified_file', help='Path to classified.json')
    parser.add_argument('--output-dir', '-o', required=True, help='Output directory for extracted files')
    parser.add_argument('--max-workers', type=int, default=10, help='Max concurrent extractors')
    parser.add_argument('--quiet', action='store_true', help='Suppress progress messages')

    args = parser.parse_args()

    result = run_legacy_extractors(
        Path(args.classified_file),
        Path(args.output_dir),
        max_workers=args.max_workers,
        verbose=not args.quiet
    )

    if result['status'] == 'complete':
        sys.exit(0)
    else:
        sys.exit(1)
