"""
Comprehensive Validation V3: Test All ChatGPT Fixes + Process New CVs

This script:
1. Re-runs all 5 validation CVs with v3 fixes (all 4 ChatGPT fixes)
2. Compares signal changes vs v2
3. Processes 5 new CVs to expand coverage
"""

import json
import sys
from pathlib import Path
from test_signals_with_tracking import analyze_signal_effectiveness
from glob import glob

def compare_versions(v2_audit_path, v3_audit_path):
    """Compare signal effectiveness between v2 and v3."""
    if not Path(v2_audit_path).exists():
        return {}

    with open(v2_audit_path) as f:
        v2 = json.load(f)
    with open(v3_audit_path) as f:
        v3 = json.load(f)

    v2_fires = v2['signal_effectiveness']['signal_fire_counts']
    v3_fires = v3['signal_effectiveness']['signal_fire_counts']

    changes = {}
    for signal in set(list(v2_fires.keys()) + list(v3_fires.keys())):
        v2_count = v2_fires.get(signal, 0)
        v3_count = v3_fires.get(signal, 0)
        if v2_count != v3_count:
            changes[signal] = {'v2': v2_count, 'v3': v3_count, 'delta': v3_count - v2_count}

    return changes


def main():
    print("=" * 80)
    print("COMPREHENSIVE VALIDATION V3: ALL CHATGPT FIXES + NEW CVS")
    print("=" * 80)
    print("\nFixes included in v3:")
    print("  ✅ FIX #1: Context-aware signal suppression (award, mentee)")
    print("  ✅ FIX #2: DOI/PMID boost for citation_like")
    print("  ✅ FIX #3: Invited talks header bias (R over S8)")
    print("  ✅ FIX #4: Presentation type detection (plenary/poster → R)")

    # ===== PHASE 1: Re-validate 5 CVs with v3 fixes =====
    print("\n" + "=" * 80)
    print("PHASE 1: RE-VALIDATING 5 CVs WITH V3 FIXES")
    print("=" * 80)

    phase1_cvs = [
        {
            'name': 'Holtz',
            'input': 'validation_CV_2002_Holtz_Heidi_PhD_preprocessed.json',
            'output_v2': 'holtz_with_fixed_signals_v2.json',
            'output_v3': 'holtz_with_fixed_signals_v3.json'
        },
        {
            'name': 'Cook',
            'input': 'validation_CV_2018_Cook_Dane_PhD_preprocessed.json',
            'output_v2': 'validation_CV_2018_Cook_Dane_PhD_with_fixed_signals_v2.json',
            'output_v3': 'validation_CV_2018_Cook_Dane_PhD_with_fixed_signals_v3.json'
        },
        {
            'name': 'Lau',
            'input': 'validation_CV_2007_Lau_Frank_MD_preprocessed.json',
            'output_v2': 'validation_CV_2007_Lau_Frank_MD_with_fixed_signals_v2.json',
            'output_v3': 'validation_CV_2007_Lau_Frank_MD_with_fixed_signals_v3.json'
        },
        {
            'name': 'Albrecht',
            'input': 'validation_CV_2003_Albrecht_Jennifer_PhD_preprocessed.json',
            'output_v2': 'validation_CV_2003_Albrecht_Jennifer_PhD_with_fixed_signals_v2.json',
            'output_v3': 'validation_CV_2003_Albrecht_Jennifer_PhD_with_fixed_signals_v3.json'
        },
        {
            'name': 'Mucci',
            'input': 'validation_CV_2009_Mucci_Lorelei_ScD_preprocessed.json',
            'output_v2': 'validation_CV_2009_Mucci_Lorelei_ScD_with_fixed_signals_v2.json',
            'output_v3': 'validation_CV_2009_Mucci_Lorelei_ScD_with_fixed_signals_v3.json'
        }
    ]

    phase1_results = []
    for cv in phase1_cvs:
        print(f"\n{'='*80}")
        print(f"Processing {cv['name']} with v3 fixes...")
        print(f"{'='*80}")

        if not Path(cv['input']).exists():
            print(f"  ❌ ERROR: Input file not found: {cv['input']}")
            continue

        try:
            # Run with v3 fixes
            audit_report = analyze_signal_effectiveness(cv['input'], cv['output_v3'])

            # Compare to v2
            changes = compare_versions(
                cv['output_v2'].replace('.json', '_AUDIT.json'),
                cv['output_v3'].replace('.json', '_AUDIT.json')
            )

            phase1_results.append({
                'cv': cv['name'],
                'status': 'SUCCESS',
                'stats': {
                    'total_entries': audit_report['signal_effectiveness']['total_entries'],
                    'hint_coverage_pct': audit_report['signal_effectiveness']['hint_coverage_pct'],
                    'signal_changes': changes
                }
            })

            print(f"  ✅ SUCCESS - Coverage: {audit_report['signal_effectiveness']['hint_coverage_pct']:.1f}%")
            if changes:
                print(f"  Signal changes from v2:")
                for signal, change in sorted(changes.items(), key=lambda x: abs(x[1]['delta']), reverse=True)[:5]:
                    delta_str = f"+{change['delta']}" if change['delta'] > 0 else str(change['delta'])
                    print(f"    {signal}: {change['v2']} → {change['v3']} ({delta_str})")

        except Exception as e:
            print(f"  ❌ ERROR: {str(e)}")
            phase1_results.append({
                'cv': cv['name'],
                'status': 'ERROR',
                'error': str(e)
            })

    # ===== PHASE 2: Process 5 new CVs =====
    print("\n" + "=" * 80)
    print("PHASE 2: PROCESSING 5 NEW CVs")
    print("=" * 80)

    # Find all preprocessed CVs
    all_preprocessed = glob('validation_CV_*_preprocessed.json')
    processed_stems = {cv['input'].replace('_preprocessed.json', '') for cv in phase1_cvs}

    # Find unprocessed CVs
    new_cvs = []
    for preprocessed_path in all_preprocessed:
        stem = preprocessed_path.replace('_preprocessed.json', '')
        if stem not in processed_stems:
            new_cvs.append({
                'name': Path(preprocessed_path).stem.replace('validation_CV_', '').replace('_preprocessed', ''),
                'input': preprocessed_path,
                'output': preprocessed_path.replace('_preprocessed.json', '_with_signals_v3.json')
            })

    # Take up to 5 new CVs
    new_cvs = new_cvs[:5]

    phase2_results = []
    if not new_cvs:
        print("\n  No new preprocessed CVs found.")
        print("  All available CVs have been processed.")
    else:
        print(f"\n  Found {len(new_cvs)} new CVs to process")

        for cv in new_cvs:
            print(f"\n{'='*80}")
            print(f"Processing new CV: {cv['name']}")
            print(f"{'='*80}")

            try:
                audit_report = analyze_signal_effectiveness(cv['input'], cv['output'])

                phase2_results.append({
                    'cv': cv['name'],
                    'status': 'SUCCESS',
                    'stats': {
                        'total_entries': audit_report['signal_effectiveness']['total_entries'],
                        'hint_coverage_pct': audit_report['signal_effectiveness']['hint_coverage_pct']
                    }
                })

                print(f"  ✅ SUCCESS - Coverage: {audit_report['signal_effectiveness']['hint_coverage_pct']:.1f}%")

            except Exception as e:
                print(f"  ❌ ERROR: {str(e)}")
                phase2_results.append({
                    'cv': cv['name'],
                    'status': 'ERROR',
                    'error': str(e)
                })

    # ===== SUMMARY =====
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY V3")
    print("=" * 80)

    print(f"\nPHASE 1 (Re-validation with v3 fixes): {len([r for r in phase1_results if r['status'] == 'SUCCESS'])}/{len(phase1_results)} successful")
    for result in phase1_results:
        if result['status'] == 'SUCCESS':
            print(f"  ✅ {result['cv']}: {result['stats']['hint_coverage_pct']:.1f}% coverage")
            if result['stats']['signal_changes']:
                top_change = max(result['stats']['signal_changes'].items(),
                                key=lambda x: abs(x[1]['delta']))
                delta_str = f"+{top_change[1]['delta']}" if top_change[1]['delta'] > 0 else str(top_change[1]['delta'])
                print(f"      Biggest change: {top_change[0]} ({delta_str})")

    if new_cvs:
        print(f"\nPHASE 2 (New CVs): {len([r for r in phase2_results if r['status'] == 'SUCCESS'])}/{len(phase2_results)} successful")
        for result in phase2_results:
            if result['status'] == 'SUCCESS':
                print(f"  ✅ {result['cv']}: {result['stats']['hint_coverage_pct']:.1f}% coverage")

    print(f"\nTotal CVs validated: {len(phase1_results) + (len(phase2_results) if new_cvs else 0)}")

    # Save summary
    summary = {
        'version': 'v3',
        'fixes_included': [
            'Context-aware signal suppression (award, mentee)',
            'DOI/PMID boost for citation_like',
            'Invited talks header bias (R over S8)',
            'Presentation type detection (plenary/poster → R)'
        ],
        'phase1_revalidation': phase1_results,
        'phase2_expansion': phase2_results if new_cvs else []
    }

    summary_path = 'validation_summary_v3.json'
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n📊 Full summary saved to: {summary_path}")

    return 0 if all(r['status'] == 'SUCCESS' for r in phase1_results) else 1


if __name__ == '__main__':
    sys.exit(main())
