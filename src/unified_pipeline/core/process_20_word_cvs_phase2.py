#!/usr/bin/env python3
"""
Process 20 Word CVs through complete pipeline with all Phase 2 improvements.

Pipeline stages:
1. Segmentation (word_chunked.py)
2. Repair (repair_segmentation.py) - Phase 2 Session 2, 3 fixes
3. Preprocessing (confusion_matrix.py) - Phase 2 Session 1 signal detection
4. Taxonomy Mapping (taxonomy_mapper_v2.py) - Phase 2 Session 4, 5 mapping fixes

Output: 5 files per CV (segmented, repaired, preprocessed, mapped JSONs) + comprehensive summary report
"""

import sys
import os
import json
import time
from pathlib import Path
from datetime import datetime
import subprocess
from copy import deepcopy

# Add paths for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / 'unified_pipeline' / 'segmentation'))
sys.path.insert(0, str(project_root / 'unified_pipeline'))  # For 'core' module imports
sys.path.insert(0, str(project_root))

from repair_segmentation import repair_segmentation
from taxonomy_mapper_v2 import map_cv_sections_v2
from confusion_matrix import compute_structural_hints


def segment_word_cv(docx_path: str, output_dir: str) -> str:
    """
    Segment Word CV using word_chunked.py module.

    Returns path to segmented JSON
    """
    print(f"  [1/4] Segmenting...")

    # Import word_chunked module
    from word_chunked import segment_word_cv_chunked

    # Process the docx file
    result = segment_word_cv_chunked(docx_path, output_dir)

    # Return the segmented JSON path
    base_name = Path(docx_path).stem
    segmented_path = Path(output_dir) / f"{base_name}_segmented.json"

    if not segmented_path.exists():
        raise FileNotFoundError(f"Segmentation failed - output not found: {segmented_path}")

    return str(segmented_path)


def add_hints_to_cv(repaired_cv: dict) -> dict:
    """
    Add structural hints/signals to each entry in the CV.

    This applies Phase 2 Session 1 signal detection.
    """
    preprocessed_cv = deepcopy(repaired_cv)

    def add_hints_to_group(group: dict):
        """Recursively add hints to group and subgroups."""
        # Add hints to each entry
        for entry in group.get('entries', []):
            text = entry.get('text_snippet', '')
            header = group.get('label_inferred', '')

            # Compute hints for this entry
            hint_result = compute_structural_hints(text, header)
            entry['hints'] = hint_result

        # Recurse into subgroups
        for subgroup in group.get('subgroups', []):
            add_hints_to_group(subgroup)

    # Add hints to all top-level groups
    for group in preprocessed_cv.get('groups', []):
        add_hints_to_group(group)

    return preprocessed_cv


def process_single_cv(docx_path: str, output_dir: str) -> dict:
    """
    Process a single CV through complete pipeline.

    Returns processing stats and output paths
    """
    start_time = time.time()
    cv_name = Path(docx_path).stem

    print(f"\n{'='*80}")
    print(f"Processing: {cv_name}")
    print(f"{'='*80}")

    try:
        # Stage 1: Segmentation
        segmented_path = segment_word_cv(docx_path, output_dir)
        print(f"  ✓ Segmented: {Path(segmented_path).name}")

        # Stage 2: Repair
        print(f"  [2/4] Repairing...")
        with open(segmented_path, 'r') as f:
            segmented_cv = json.load(f)

        repaired_cv = repair_segmentation(segmented_cv, verbose=False)
        repaired_path = str(Path(output_dir) / f"{cv_name}_repaired.json")

        with open(repaired_path, 'w') as f:
            json.dump(repaired_cv, f, indent=2)

        print(f"  ✓ Repaired: {Path(repaired_path).name}")

        # Stage 3: Preprocessing (add hints/signals)
        print(f"  [3/4] Preprocessing (adding signals)...")
        preprocessed_cv = add_hints_to_cv(repaired_cv)
        preprocessed_path = str(Path(output_dir) / f"{cv_name}_preprocessed.json")

        with open(preprocessed_path, 'w') as f:
            json.dump(preprocessed_cv, f, indent=2)

        print(f"  ✓ Preprocessed: {Path(preprocessed_path).name}")

        # Stage 4: Taxonomy Mapping
        print(f"  [4/4] Taxonomy mapping...")
        mapped_path = str(Path(output_dir) / f"{cv_name}_mapped.json")

        result = map_cv_sections_v2(
            input_path=preprocessed_path,
            output_path=mapped_path,
            verbose=False,
            model='gpt-4o-mini'
        )

        print(f"  ✓ Mapped: {Path(mapped_path).name}")

        # Calculate stats
        elapsed = time.time() - start_time

        return {
            'cv_name': cv_name,
            'success': True,
            'elapsed_time': elapsed,
            'segmented_path': segmented_path,
            'repaired_path': repaired_path,
            'preprocessed_path': preprocessed_path,
            'mapped_path': mapped_path,
            'stats': result.get('stats', {}),
            'token_usage': result.get('token_usage', {}),
            'error': None
        }

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"  ✗ ERROR: {e}")

        return {
            'cv_name': cv_name,
            'success': False,
            'elapsed_time': elapsed,
            'error': str(e)
        }


def generate_summary_report(results: list, output_dir: str):
    """Generate comprehensive summary report."""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Calculate totals
    total_cvs = len(results)
    successful = sum(1 for r in results if r['success'])
    failed = total_cvs - successful
    total_time = sum(r['elapsed_time'] for r in results)
    avg_time = total_time / total_cvs if total_cvs > 0 else 0

    # Calculate API stats (only for successful CVs)
    successful_results = [r for r in results if r['success']]
    total_api_calls = sum(r.get('stats', {}).get('total_api_calls', 0) for r in successful_results)
    total_tokens = sum(r.get('token_usage', {}).get('total_tokens', 0) for r in successful_results)

    # Generate report
    report = f"""# 20 Word CVs - Phase 2 Processing Report

**Date**: {timestamp}
**Total CVs**: {total_cvs}
**Successful**: {successful}
**Failed**: {failed}
**Total Processing Time**: {total_time:.1f}s
**Average Time per CV**: {avg_time:.1f}s

---

## API Usage

**Total API Calls**: {total_api_calls:,}
**Total Tokens**: {total_tokens:,}
**Average Calls per CV**: {total_api_calls / successful if successful > 0 else 0:.1f}
**Average Tokens per CV**: {total_tokens / successful if successful > 0 else 0:,.0f}

---

## Phase 2 Improvements Applied

### Repair Stage (repair_segmentation.py)
- ✓ Fix #12: Repeated header deduplication
- ✓ Fix #15: Roman numeral header deduplication
- ✓ Fix #8: Institution header merging
- ✓ Fix #9: Address block merging
- ✓ Fix #10: Grant containment under Research Support
- ✓ Fix #13: N/A placeholder demotion

### Preprocessing Stage (confusion_matrix.py)
- ✓ Fix #7: URL/ORCID detection
- ✓ Fix #11: Grant number patterns
- ✓ Fix #14: Date-only line detection
- ✓ Fix #19: Conference pattern detection

### Taxonomy Mapping Stage (taxonomy_mapper_v2.py)
- ✓ Fix #16: Keynote/invited talk promotion
- ✓ Fix #17: Committee/service mapping
- ✓ Fix #18: Signal-to-taxonomy override
- ✓ Fix #20: Enhanced award/honor and teaching signals
- ✓ Fix #22: Research narrative vs output distinction
- ✓ Fix #23: A vs D disambiguation guidance

---

## Per-CV Results

"""

    # Add individual CV results
    for i, result in enumerate(results, 1):
        status = "✓" if result['success'] else "✗"
        report += f"\n### {i}. {result['cv_name']} {status}\n\n"

        if result['success']:
            stats = result.get('stats', {})
            tokens = result.get('token_usage', {})

            report += f"- **Time**: {result['elapsed_time']:.1f}s\n"
            report += f"- **API Calls**: {stats.get('total_api_calls', 0)}\n"
            report += f"- **Tokens**: {tokens.get('total_tokens', 0):,}\n"
            report += f"- **Avg Confidence**: {stats.get('avg_confidence', 0):.3f}\n"
            report += f"- **Output**: `{Path(result['mapped_path']).name}`\n"
        else:
            report += f"- **Error**: {result['error']}\n"
            report += f"- **Time**: {result['elapsed_time']:.1f}s\n"

    # Add footer
    report += f"""
---

## Output Files

All processed CVs are saved in:
`{output_dir}`

File naming convention:
- `*_segmented.json` - Segmented CV
- `*_repaired.json` - After structural repairs (Phase 2 Session 2, 3)
- `*_preprocessed.json` - After signal detection (Phase 2 Session 1)
- `*_mapped.json` - Final taxonomy-mapped CV (Phase 2 Session 4, 5)

---

**Report Generated**: {timestamp}
**Pipeline Version**: Phase 2 Complete (18/18 fixes implemented)
"""

    # Save report
    report_path = Path(output_dir) / 'PHASE2_20CVS_PROCESSING_REPORT.md'
    with open(report_path, 'w') as f:
        f.write(report)

    print(f"\n{'='*80}")
    print(f"Summary report saved: {report_path}")
    print(f"{'='*80}\n")

    # Print summary to console
    print(report)


def main():
    """Main processing function."""

    # Configuration
    word_cv_dir = Path("/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project/data/sample_cvs/word")
    output_dir = Path("/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project/src/unified_pipeline/core/.outputs/phase2_20cvs")

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find Word CVs
    docx_files = list(word_cv_dir.glob("*.docx"))
    docx_files = sorted(docx_files)[:20]  # First 20 CVs

    if not docx_files:
        print("No Word CVs found!")
        return 1

    print(f"\n{'='*80}")
    print(f"PHASE 2 - 20 WORD CVS BATCH PROCESSING")
    print(f"{'='*80}")
    print(f"Found {len(docx_files)} Word CVs")
    print(f"Output directory: {output_dir}")
    print(f"{'='*80}\n")

    # Process each CV
    results = []
    start_time = time.time()

    for i, docx_path in enumerate(docx_files, 1):
        print(f"\n[{i}/{len(docx_files)}] Processing...")
        result = process_single_cv(str(docx_path), str(output_dir))
        results.append(result)

        # Print progress
        success_count = sum(1 for r in results if r['success'])
        print(f"\nProgress: {success_count}/{i} successful")

    total_time = time.time() - start_time

    # Generate summary report
    print(f"\n\n{'='*80}")
    print(f"BATCH PROCESSING COMPLETE")
    print(f"{'='*80}")
    print(f"Total time: {total_time:.1f}s")
    print(f"Successful: {sum(1 for r in results if r['success'])}/{len(results)}")
    print(f"{'='*80}\n")

    generate_summary_report(results, str(output_dir))

    return 0


if __name__ == '__main__':
    exit(main())
