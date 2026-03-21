"""
Process Already-Segmented CVs with Repair Pipeline

For CVs that are already segmented, run through:
1. Repair segmentation
2. Preprocess (filter empty groups)
3. Map with v3 signals

Usage:
    python process_already_segmented_cvs.py
"""

import json
import sys
from pathlib import Path
from repair_segmentation import repair_segmentation
from preprocess_segmented_cv import filter_valid_groups
from test_signals_with_tracking import analyze_signal_effectiveness

# Already segmented CVs
SEGMENTED_CVS = [
    {
        'name': 'Holtz',
        'id': '2002',
        'segmented_file': '2002_Holtz_segmented.json'
    },
    {
        'name': 'Simpson',
        'id': 'Simpson',
        'segmented_file': "Kathleen Simpson, M.D.'s CV 2020 updated_word_segmented.json"
    },
    {
        'name': 'Blakely',
        'id': '2074',
        'segmented_file': '2074_Blakely_Cv_segmented.json'
    }
]

WORD_DIR = Path('/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project/data/sample_cvs/word')


def main():
    print("="*80)
    print("PROCESSING ALREADY-SEGMENTED CVS WITH REPAIR PIPELINE")
    print("="*80)
    print()

    results = []

    for cv_info in SEGMENTED_CVS:
        print(f"\n{'='*80}")
        print(f"Processing: {cv_info['name']} ({cv_info['id']})")
        print(f"{'='*80}")

        segmented_path = WORD_DIR / cv_info['segmented_file']
        repaired_path = Path(f"validation_CV_{cv_info['id']}_{cv_info['name']}_segmented_repaired.json")
        preprocessed_path = Path(f"validation_CV_{cv_info['id']}_{cv_info['name']}_preprocessed.json")
        mapped_path = Path(f"validation_CV_{cv_info['id']}_{cv_info['name']}_with_signals_v3.json")

        # Step 1: Repair
        print(f"  [1/3] Repairing segmentation...")
        try:
            with open(segmented_path) as f:
                segmented_cv = json.load(f)

            repaired_cv = repair_segmentation(segmented_cv, verbose=False)

            with open(repaired_path, 'w') as f:
                json.dump(repaired_cv, f, indent=2)

            orig_groups = segmented_cv['meta']['num_top_level_groups']
            final_groups = repaired_cv['meta']['repair']['repaired_groups']
            print(f"    ✓ Repaired: {orig_groups} → {final_groups} top-level groups")

        except Exception as e:
            print(f"    ✗ Repair failed: {str(e)}")
            results.append({'cv': cv_info['name'], 'status': 'ERROR', 'step': 'repair'})
            continue

        # Step 2: Preprocess
        print(f"  [2/3] Preprocessing...")
        try:
            with open(repaired_path) as f:
                repaired_cv = json.load(f)

            preprocessed_cv = filter_valid_groups(repaired_cv, verbose=False)

            with open(preprocessed_path, 'w') as f:
                json.dump(preprocessed_cv, f, indent=2)

            stats = preprocessed_cv['meta']['preprocessing']
            print(f"    ✓ Preprocessed: {stats['content_groups']} content groups, "
                  f"{stats['preserved_headers']} headers preserved")

        except Exception as e:
            print(f"    ✗ Preprocessing failed: {str(e)}")
            results.append({'cv': cv_info['name'], 'status': 'ERROR', 'step': 'preprocessing'})
            continue

        # Step 3: Map
        print(f"  [3/3] Mapping with v3 signals...")
        try:
            audit_report = analyze_signal_effectiveness(
                str(preprocessed_path),
                str(mapped_path)
            )

            stats = audit_report['signal_effectiveness']
            print(f"    ✓ Mapped: {stats['total_entries']} entries, "
                  f"{stats['hint_coverage_pct']:.1f}% hint coverage, "
                  f"avg conf={audit_report['mapping_stats']['avg_confidence']:.3f}")

            results.append({
                'cv': cv_info['name'],
                'id': cv_info['id'],
                'status': 'SUCCESS',
                'stats': {
                    'total_entries': stats['total_entries'],
                    'hint_coverage_pct': stats['hint_coverage_pct'],
                    'avg_confidence': audit_report['mapping_stats']['avg_confidence'],
                    'total_api_calls': audit_report['mapping_stats']['total_api_calls'],
                    'total_tokens': audit_report['mapping_stats']['total_tokens']
                }
            })

        except Exception as e:
            print(f"    ✗ Mapping failed: {str(e)}")
            import traceback
            traceback.print_exc()
            results.append({'cv': cv_info['name'], 'status': 'ERROR', 'step': 'mapping'})

    # Summary
    print(f"\n{'='*80}")
    print("PROCESSING SUMMARY")
    print(f"{'='*80}")

    successful = [r for r in results if r['status'] == 'SUCCESS']
    failed = [r for r in results if r['status'] == 'ERROR']

    print(f"\nSuccessful: {len(successful)}/{len(results)}")
    print(f"Failed: {len(failed)}/{len(results)}")

    if successful:
        print(f"\n✅ SUCCESSFULLY PROCESSED:")
        for result in successful:
            print(f"\n  {result['cv']} ({result['id']}):")
            print(f"    Entries: {result['stats']['total_entries']}")
            print(f"    Hint coverage: {result['stats']['hint_coverage_pct']:.1f}%")
            print(f"    Avg confidence: {result['stats']['avg_confidence']:.3f}")
            print(f"    API calls: {result['stats']['total_api_calls']}")
            print(f"    Tokens: {result['stats']['total_tokens']:,}")

    if failed:
        print(f"\n❌ FAILED:")
        for result in failed:
            step = result.get('step', 'unknown')
            print(f"  {result['cv']}: Failed at {step}")

    print()
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
