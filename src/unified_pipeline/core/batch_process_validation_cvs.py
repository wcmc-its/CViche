"""
Batch Process Validation CVs with Comprehensive Signal Library

Processes all validation CVs and generates:
1. Preprocessed CV (already exists)
2. Mapped output with all 19 signals
3. Audit report with signal effectiveness

Output pairs for ChatGPT review.
"""

import json
import sys
from pathlib import Path
from test_signals_with_tracking import analyze_signal_effectiveness

# Validation CVs to process (excluding Holtz which is already done)
CVS_TO_PROCESS = [
    {
        'name': 'Cook',
        'input': 'validation_CV_2018_Cook_Dane_PhD_preprocessed.json',
        'output': 'validation_CV_2018_Cook_Dane_PhD_with_signals.json'
    },
    {
        'name': 'Lau',
        'input': 'validation_CV_2007_Lau_Frank_MD_preprocessed.json',
        'output': 'validation_CV_2007_Lau_Frank_MD_with_signals.json'
    },
    {
        'name': 'Albrecht',
        'input': 'validation_CV_2003_Albrecht_Jennifer_PhD_preprocessed.json',
        'output': 'validation_CV_2003_Albrecht_Jennifer_PhD_with_signals.json'
    },
    {
        'name': 'Mucci',
        'input': 'validation_CV_2009_Mucci_Lorelei_ScD_preprocessed.json',
        'output': 'validation_CV_2009_Mucci_Lorelei_ScD_with_signals.json'
    }
]

def main():
    print("=" * 70)
    print("BATCH PROCESSING VALIDATION CVs WITH COMPREHENSIVE SIGNAL LIBRARY")
    print("=" * 70)
    print(f"\nProcessing {len(CVS_TO_PROCESS)} CVs...")
    print()

    results = []

    for i, cv in enumerate(CVS_TO_PROCESS, 1):
        print(f"\n{'=' * 70}")
        print(f"CV {i}/{len(CVS_TO_PROCESS)}: {cv['name']}")
        print(f"{'=' * 70}")

        if not Path(cv['input']).exists():
            print(f"  ❌ ERROR: Input file not found: {cv['input']}")
            results.append({
                'cv': cv['name'],
                'status': 'ERROR',
                'error': 'Input file not found'
            })
            continue

        try:
            # Run signal effectiveness analysis
            audit_report = analyze_signal_effectiveness(cv['input'], cv['output'])

            results.append({
                'cv': cv['name'],
                'status': 'SUCCESS',
                'input': cv['input'],
                'output': cv['output'],
                'audit': cv['output'].replace('.json', '_AUDIT.json'),
                'stats': {
                    'total_sections': audit_report['mapping_stats']['total_sections'],
                    'total_entries': audit_report['signal_effectiveness']['total_entries'],
                    'entries_with_hints': audit_report['signal_effectiveness']['entries_with_hints'],
                    'hint_coverage_pct': audit_report['signal_effectiveness']['hint_coverage_pct'],
                    'total_api_calls': audit_report['mapping_stats']['total_api_calls'],
                    'total_tokens': audit_report['mapping_stats']['total_tokens']
                }
            })

            print(f"\n  ✅ SUCCESS")
            print(f"     Sections: {audit_report['mapping_stats']['total_sections']}")
            print(f"     Entries: {audit_report['signal_effectiveness']['total_entries']}")
            print(f"     Hint coverage: {audit_report['signal_effectiveness']['hint_coverage_pct']:.1f}%")

        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            results.append({
                'cv': cv['name'],
                'status': 'ERROR',
                'error': str(e)
            })

    # Print summary
    print(f"\n{'=' * 70}")
    print("BATCH PROCESSING SUMMARY")
    print(f"{'=' * 70}")

    successful = [r for r in results if r['status'] == 'SUCCESS']
    failed = [r for r in results if r['status'] == 'ERROR']

    print(f"\nSuccessful: {len(successful)}/{len(results)}")
    print(f"Failed: {len(failed)}/{len(results)}")

    if successful:
        print(f"\n✅ SUCCESSFULLY PROCESSED CVs:")
        for result in successful:
            print(f"\n  {result['cv']}:")
            print(f"    Preprocessed: {result['input']}")
            print(f"    Mapped:       {result['output']}")
            print(f"    Audit:        {result['audit']}")
            print(f"    Stats: {result['stats']['total_sections']} sections, "
                  f"{result['stats']['total_entries']} entries, "
                  f"{result['stats']['hint_coverage_pct']:.1f}% hint coverage")

    if failed:
        print(f"\n❌ FAILED CVs:")
        for result in failed:
            print(f"  {result['cv']}: {result['error']}")

    # Save summary
    summary_path = 'batch_processing_summary.json'
    with open(summary_path, 'w') as f:
        json.dump({
            'total_cvs': len(results),
            'successful': len(successful),
            'failed': len(failed),
            'results': results
        }, f, indent=2)

    print(f"\n📊 Summary saved to: {summary_path}")
    print()

    return 0 if not failed else 1

if __name__ == '__main__':
    sys.exit(main())
