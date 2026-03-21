"""
Automated Validation: Verify Fixes + Expand Coverage

Phase 1: Re-run same 5 CVs with fixed signals
Phase 2: Process 5 new CVs to expand coverage
"""

import json
import sys
from pathlib import Path
from test_signals_with_tracking import analyze_signal_effectiveness
from glob import glob

def compare_signal_effectiveness(old_audit_path, new_audit_path):
    """Compare signal effectiveness before/after fixes."""
    with open(old_audit_path) as f:
        old = json.load(f)
    with open(new_audit_path) as f:
        new = json.load(f)

    old_fires = old['signal_effectiveness']['signal_fire_counts']
    new_fires = new['signal_effectiveness']['signal_fire_counts']

    changes = {}
    for signal in set(list(old_fires.keys()) + list(new_fires.keys())):
        old_count = old_fires.get(signal, 0)
        new_count = new_fires.get(signal, 0)
        if old_count != new_count:
            changes[signal] = {'old': old_count, 'new': new_count, 'delta': new_count - old_count}

    return changes


def main():
    print("=" * 80)
    print("AUTOMATED VALIDATION: VERIFY FIXES + EXPAND COVERAGE")
    print("=" * 80)

    # ===== PHASE 1: Re-run same 5 CVs with fixed signals =====
    print("\n" + "=" * 80)
    print("PHASE 1: RE-RUNNING 5 CVs WITH FIXED SIGNALS")
    print("=" * 80)

    phase1_cvs = [
        {
            'name': 'Holtz',
            'input': 'validation_CV_2002_Holtz_Heidi_PhD_preprocessed.json',
            'output_v1': 'holtz_with_all_signals.json',
            'output_v2': 'holtz_with_fixed_signals_v2.json'
        },
        {
            'name': 'Cook',
            'input': 'validation_CV_2018_Cook_Dane_PhD_preprocessed.json',
            'output_v1': 'validation_CV_2018_Cook_Dane_PhD_with_signals.json',
            'output_v2': 'validation_CV_2018_Cook_Dane_PhD_with_fixed_signals_v2.json'
        },
        {
            'name': 'Lau',
            'input': 'validation_CV_2007_Lau_Frank_MD_preprocessed.json',
            'output_v1': 'validation_CV_2007_Lau_Frank_MD_with_signals.json',
            'output_v2': 'validation_CV_2007_Lau_Frank_MD_with_fixed_signals_v2.json'
        },
        {
            'name': 'Albrecht',
            'input': 'validation_CV_2003_Albrecht_Jennifer_PhD_preprocessed.json',
            'output_v1': 'validation_CV_2003_Albrecht_Jennifer_PhD_with_signals.json',
            'output_v2': 'validation_CV_2003_Albrecht_Jennifer_PhD_with_fixed_signals_v2.json'
        },
        {
            'name': 'Mucci',
            'input': 'validation_CV_2009_Mucci_Lorelei_ScD_preprocessed.json',
            'output_v1': 'validation_CV_2009_Mucci_Lorelei_ScD_with_signals.json',
            'output_v2': 'validation_CV_2009_Mucci_Lorelei_ScD_with_fixed_signals_v2.json'
        }
    ]

    phase1_results = []
    for cv in phase1_cvs:
        print(f"\n{'='*80}")
        print(f"Processing {cv['name']} with fixed signals...")
        print(f"{'='*80}")

        if not Path(cv['input']).exists():
            print(f"  ❌ ERROR: Input file not found: {cv['input']}")
            continue

        try:
            # Run with fixed signals
            audit_report = analyze_signal_effectiveness(cv['input'], cv['output_v2'])

            # Compare to v1
            if Path(cv['output_v1'].replace('.json', '_AUDIT.json')).exists():
                changes = compare_signal_effectiveness(
                    cv['output_v1'].replace('.json', '_AUDIT.json'),
                    cv['output_v2'].replace('.json', '_AUDIT.json')
                )
            else:
                changes = {}

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
                print(f"  Signal changes from v1:")
                for signal, change in sorted(changes.items(), key=lambda x: abs(x[1]['delta']), reverse=True)[:5]:
                    delta_str = f"+{change['delta']}" if change['delta'] > 0 else str(change['delta'])
                    print(f"    {signal}: {change['old']} → {change['new']} ({delta_str})")

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
                'output': preprocessed_path.replace('_preprocessed.json', '_with_signals.json')
            })

    # Take up to 5 new CVs
    new_cvs = new_cvs[:5]

    if not new_cvs:
        print("\n  No new CVs found. All available CVs have been processed.")
    else:
        print(f"\n  Found {len(new_cvs)} new CVs to process")

        phase2_results = []
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
    print("VALIDATION SUMMARY")
    print("=" * 80)

    print(f"\nPHASE 1 (Re-run with fixes): {len([r for r in phase1_results if r['status'] == 'SUCCESS'])}/{len(phase1_results)} successful")
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
        'phase1_revalidation': phase1_results,
        'phase2_expansion': phase2_results if new_cvs else []
    }

    summary_path = 'validation_summary_v2.json'
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n📊 Full summary saved to: {summary_path}")

    return 0 if all(r['status'] == 'SUCCESS' for r in phase1_results) else 1


if __name__ == '__main__':
    sys.exit(main())
