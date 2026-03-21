"""
Process New CVs with V3 Signals

This script processes the 2 preprocessed CVs (Blakely and Simpson) with v3 signal library.
"""

import json
import sys
from pathlib import Path
from test_signals_with_tracking import analyze_signal_effectiveness

def main():
    print("=" * 80)
    print("PROCESSING 2 NEW CVs WITH V3 SIGNALS")
    print("=" * 80)
    print("\nProcessing newly preprocessed CVs:")
    print("  1. Blakely (2074)")
    print("  2. Simpson (Kathleen M.D.)")

    cvs_to_process = [
        {
            'name': 'Blakely',
            'input': 'validation_CV_2074_Blakely_preprocessed.json',
            'output': 'validation_CV_2074_Blakely_with_signals_v3.json'
        },
        {
            'name': 'Simpson',
            'input': 'validation_CV_Simpson_Kathleen_MD_preprocessed.json',
            'output': 'validation_CV_Simpson_Kathleen_MD_with_signals_v3.json'
        }
    ]

    results = []
    for i, cv in enumerate(cvs_to_process, 1):
        print(f"\n{'='*80}")
        print(f"CV {i}/{len(cvs_to_process)}: {cv['name']}")
        print(f"{'='*80}")

        if not Path(cv['input']).exists():
            print(f"  ❌ ERROR: Input file not found: {cv['input']}")
            results.append({
                'cv': cv['name'],
                'status': 'ERROR',
                'error': 'Input file not found'
            })
            continue

        try:
            # Run with v3 signals
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
            import traceback
            traceback.print_exc()
            results.append({
                'cv': cv['name'],
                'status': 'ERROR',
                'error': str(e)
            })

    # Print summary
    print(f"\n{'='*80}")
    print("PROCESSING SUMMARY")
    print(f"{'='*80}")

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
    summary_path = 'new_cvs_processing_summary.json'
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
