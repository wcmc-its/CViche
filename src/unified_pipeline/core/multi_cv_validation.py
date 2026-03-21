"""
Multi-CV Validation Testing

Tests hierarchical batch classification with confusion matrices on multiple CVs.
Collects statistics and generates comparison report.

Usage:
    python multi_cv_validation.py
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Any
from preprocess_segmented_cv import filter_valid_groups
from taxonomy_mapper_v2 import map_cv_sections_v2

# Test CVs (diverse sizes)
TEST_CVS = [
    "CV_2018_Cook_Dane_PhD_segmented_gold.json",       # Small (33K)
    "CV_2002_Holtz_Heidi_PhD_segmented_gold.json",     # Medium (47K)
    "CV_2007_Lau_Frank_MD_segmented_gold.json",        # Medium (88K)
    "CV_2003_Albrecht_Jennifer_PhD_segmented_gold.json",  # Large (134K)
    "CV_2009_Mucci_Lorelei_ScD_segmented_gold.json"    # Very Large (209K)
]

GOLD_STANDARD_DIR = Path("../../../archive/backups_2025-10-29/stage_1_segmentation_ARCHIVED_20251023/gold_standard/gold_standard_cvs")


def validate_cv(cv_path: Path) -> Dict[str, Any]:
    """
    Run full validation on a single CV.

    Returns:
        {
            'cv_name': str,
            'file_size': int,
            'preprocessing': Dict,
            'mapping': Dict,
            'success': bool,
            'error': Optional[str]
        }
    """
    cv_name = cv_path.name
    print(f"\n{'='*80}")
    print(f"VALIDATING: {cv_name}")
    print(f"{'='*80}")

    result = {
        'cv_name': cv_name,
        'file_size': cv_path.stat().st_size,
        'success': False,
        'error': None
    }

    try:
        # Load CV
        with open(cv_path, 'r') as f:
            cv_data = json.load(f)

        print(f"✓ Loaded CV ({result['file_size']:,} bytes)")

        # Preprocessing
        print("\n--- PREPROCESSING ---")
        preprocessed = filter_valid_groups(cv_data, verbose=False, preserve_headers=True)

        result['preprocessing'] = {
            'original_groups': preprocessed['meta']['preprocessing']['original_groups'],
            'content_groups': preprocessed['meta']['preprocessing']['content_groups'],
            'preserved_headers': preprocessed['meta']['preprocessing']['preserved_headers'],
            'total_entries': preprocessed.get('meta', {}).get('total_entries', 0)
        }

        print(f"  Original groups: {result['preprocessing']['original_groups']}")
        print(f"  Content groups: {result['preprocessing']['content_groups']}")
        print(f"  Preserved headers: {result['preprocessing']['preserved_headers']}")
        print(f"  Total entries: {result['preprocessing']['total_entries']}")

        # Save preprocessed
        preprocessed_path = Path(f"validation_{cv_name.replace('_segmented_gold', '_preprocessed')}")
        with open(preprocessed_path, 'w') as f:
            json.dump(preprocessed, f, indent=2)

        # Taxonomy Mapping
        print("\n--- TAXONOMY MAPPING ---")
        start_time = time.time()

        mapping_result = map_cv_sections_v2(
            str(preprocessed_path),
            output_path=str(preprocessed_path.parent / f"validation_{cv_name.replace('_segmented_gold', '_mapped')}")
        )

        elapsed_time = time.time() - start_time

        result['mapping'] = {
            'total_sections': mapping_result['stats']['total_sections'],
            'skipped_headers': mapping_result['stats']['skipped_structural_headers'],
            'high_confidence': mapping_result['stats']['high_confidence'],
            'avg_confidence': mapping_result['stats']['avg_confidence'],
            'pass1_calls': mapping_result['stats']['pass1_calls'],
            'pass2_calls': mapping_result['stats']['pass2_calls'],
            'total_api_calls': mapping_result['stats']['total_api_calls'],
            'prompt_tokens': mapping_result['token_usage']['prompt_tokens'],
            'completion_tokens': mapping_result['token_usage']['completion_tokens'],
            'total_tokens': mapping_result['token_usage']['total_tokens'],
            'elapsed_time': elapsed_time
        }

        print(f"\n✓ Mapping complete in {elapsed_time:.1f}s")

        result['success'] = True

    except Exception as e:
        result['error'] = str(e)
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

    return result


def generate_report(results: List[Dict[str, Any]]) -> str:
    """Generate markdown validation report."""

    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]

    report = """# Multi-CV Validation Report

**Date**: {date}
**Test CVs**: {total}
**Successful**: {success}
**Failed**: {fail}

---

## Executive Summary

""".format(
        date=time.strftime('%Y-%m-%d'),
        total=len(results),
        success=len(successful),
        fail=len(failed)
    )

    if not successful:
        report += "❌ **All CVs failed validation**\n\n"
        for r in failed:
            report += f"- {r['cv_name']}: {r['error']}\n"
        return report

    # Calculate aggregate statistics
    total_groups = sum(r['preprocessing']['original_groups'] for r in successful)
    total_content = sum(r['preprocessing']['content_groups'] for r in successful)
    total_headers = sum(r['preprocessing']['preserved_headers'] for r in successful)
    total_entries = sum(r['preprocessing']['total_entries'] for r in successful)

    avg_confidence = sum(r['mapping']['avg_confidence'] for r in successful) / len(successful)
    total_api_calls = sum(r['mapping']['total_api_calls'] for r in successful)
    total_tokens = sum(r['mapping']['total_tokens'] for r in successful)

    report += f"""**Aggregate Statistics**:
- Total groups processed: {total_groups:,}
- Total entries classified: {total_entries:,}
- Average confidence: {avg_confidence:.3f}
- Total API calls: {total_api_calls:,}
- Total tokens: {total_tokens:,}

---

## Results by CV

| CV | Size | Groups | Entries | Confidence | API Calls | Tokens | Time |
|----|------|--------|---------|------------|-----------|--------|------|
"""

    for r in successful:
        size_kb = r['file_size'] / 1024
        report += f"| {r['cv_name'][:40]} | {size_kb:.0f}KB | {r['preprocessing']['content_groups']} | {r['preprocessing']['total_entries']} | {r['mapping']['avg_confidence']:.3f} | {r['mapping']['total_api_calls']} | {r['mapping']['total_tokens']:,} | {r['mapping']['elapsed_time']:.1f}s |\n"

    report += "\n---\n\n## Detailed Results\n\n"

    for r in successful:
        report += f"""### {r['cv_name']}

**File Size**: {r['file_size']:,} bytes ({r['file_size']/1024:.1f} KB)

**Preprocessing**:
- Original groups: {r['preprocessing']['original_groups']}
- Content groups: {r['preprocessing']['content_groups']}
- Preserved headers: {r['preprocessing']['preserved_headers']}
- Total entries: {r['preprocessing']['total_entries']}

**Mapping**:
- Total sections classified: {r['mapping']['total_sections']}
- Skipped headers: {r['mapping']['skipped_headers']}
- High confidence (≥0.85): {r['mapping']['high_confidence']} ({r['mapping']['high_confidence']/r['mapping']['total_sections']*100:.1f}%)
- Average confidence: {r['mapping']['avg_confidence']:.3f}

**API Usage**:
- Pass 1 calls: {r['mapping']['pass1_calls']}
- Pass 2 calls: {r['mapping']['pass2_calls']}
- Total API calls: {r['mapping']['total_api_calls']}
- API calls saved: {r['mapping']['skipped_headers']}

**Token Usage**:
- Prompt tokens: {r['mapping']['prompt_tokens']:,}
- Completion tokens: {r['mapping']['completion_tokens']:,}
- Total tokens: {r['mapping']['total_tokens']:,}

**Performance**:
- Elapsed time: {r['mapping']['elapsed_time']:.1f}s

---

"""

    # Failed CVs
    if failed:
        report += "\n## Failed CVs\n\n"
        for r in failed:
            report += f"### {r['cv_name']}\n\n"
            report += f"**Error**: {r['error']}\n\n"

    # Analysis
    report += "\n## Analysis\n\n"

    if len(successful) >= 2:
        # Token efficiency by size
        report += "### Token Efficiency by CV Size\n\n"
        report += "| CV | Size (KB) | Total Tokens | Tokens/KB |\n"
        report += "|----|-----------|--------------|----------|\n"

        for r in successful:
            size_kb = r['file_size'] / 1024
            tokens_per_kb = r['mapping']['total_tokens'] / size_kb
            report += f"| {r['cv_name'][:40]} | {size_kb:.0f} | {r['mapping']['total_tokens']:,} | {tokens_per_kb:.0f} |\n"

        # Confidence distribution
        confidences = [r['mapping']['avg_confidence'] for r in successful]
        min_conf = min(confidences)
        max_conf = max(confidences)

        report += f"\n### Confidence Distribution\n\n"
        report += f"- Minimum: {min_conf:.3f}\n"
        report += f"- Maximum: {max_conf:.3f}\n"
        report += f"- Average: {avg_confidence:.3f}\n"
        report += f"- Range: {max_conf - min_conf:.3f}\n"

        # Pass 2 utilization
        pass2_rates = [(r['mapping']['pass2_calls'] / r['mapping']['total_sections'] * 100) for r in successful]
        avg_pass2_rate = sum(pass2_rates) / len(pass2_rates)

        report += f"\n### Pass 2 Utilization\n\n"
        report += f"- Average Pass 2 rate: {avg_pass2_rate:.1f}% of sections\n"
        report += f"- This indicates how many sections required child classification\n"

    return report


def main():
    """Run multi-CV validation."""
    print("="*80)
    print("MULTI-CV VALIDATION TEST")
    print("="*80)
    print(f"Testing {len(TEST_CVS)} CVs with hierarchical batch classification")
    print()

    results = []

    for cv_filename in TEST_CVS:
        cv_path = GOLD_STANDARD_DIR / cv_filename

        if not cv_path.exists():
            print(f"⚠️  Skipping {cv_filename} (not found)")
            results.append({
                'cv_name': cv_filename,
                'file_size': 0,
                'success': False,
                'error': 'File not found'
            })
            continue

        result = validate_cv(cv_path)
        results.append(result)

        # Small delay between CVs
        time.sleep(2)

    # Generate report
    print("\n" + "="*80)
    print("GENERATING REPORT")
    print("="*80)

    report = generate_report(results)

    # Save report
    report_path = Path("MULTI_CV_VALIDATION_REPORT.md")
    with open(report_path, 'w') as f:
        f.write(report)

    print(f"\n✓ Report saved to: {report_path}")

    # Print summary
    successful = [r for r in results if r['success']]
    print(f"\n{'='*80}")
    print(f"VALIDATION SUMMARY")
    print(f"{'='*80}")
    print(f"Total CVs: {len(results)}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(results) - len(successful)}")

    if successful:
        avg_conf = sum(r['mapping']['avg_confidence'] for r in successful) / len(successful)
        total_tokens = sum(r['mapping']['total_tokens'] for r in successful)
        print(f"Average confidence: {avg_conf:.3f}")
        print(f"Total tokens used: {total_tokens:,}")

    print()


if __name__ == '__main__':
    main()
