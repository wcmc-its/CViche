"""
Batch Process CVs with Segmentation Repair Pipeline

Full pipeline:
1. Segment Word CVs (if needed)
2. Repair segmentation (fix structural issues)
3. Preprocess (filter empty groups)
4. Map with v3 signals

Usage:
    python batch_process_with_repair.py
"""

import json
import sys
from pathlib import Path
from repair_segmentation import repair_segmentation
from preprocess_segmented_cv import filter_valid_groups
from test_signals_with_tracking import analyze_signal_effectiveness

# PHASE 1: Process 20 CVs with current repair pipeline
CVS_TO_PROCESS = [
    # Batch 1: CVs 1-5
    {'name': 'Denckla', 'word_file': '2025_Denckla_Cv.docx', 'id': '2025'},
    {'name': 'Dabelko-Schoeny', 'word_file': '2024_Dabelko_Schoeny.docx', 'id': '2024'},
    {'name': 'Almasri', 'word_file': '2004_Almasri_Mahmoud.docx', 'id': '2004'},
    {'name': 'Albrecht', 'word_file': '2003_Albrechtjs_Cv.docx', 'id': '2003'},
    {'name': 'Bush', 'word_file': '2006_Bush.docx', 'id': '2006'},

    # Batch 2: CVs 6-10
    {'name': 'Frank_Lau', 'word_file': '2007_Frank_Lau.docx', 'id': '2007'},
    {'name': 'Dunkel_Schetter', 'word_file': '2008_Dunkel_Schetter.docx', 'id': '2008'},
    {'name': 'Mucci_March', 'word_file': '2009_Mucci_March.docx', 'id': '2009'},
    {'name': 'Oliver_Hobert', 'word_file': '2010_Oliver_Hobert.docx', 'id': '2010'},
    {'name': 'Cvsir', 'word_file': '2011_Cvsir.docx', 'id': '2011'},

    # Batch 3: CVs 11-15
    {'name': 'Ncebner_Nov', 'word_file': '2012_Ncebner_Nov.docx', 'id': '2012'},
    {'name': 'Petersen', 'word_file': '2013_Petersen.docx', 'id': '2013'},
    {'name': 'Pardini', 'word_file': '2014_Pardini.docx', 'id': '2014'},
    {'name': 'Wende', 'word_file': '2015_Wende.docx', 'id': '2015'},
    {'name': 'Yisheng', 'word_file': '2016_Yisheng.docx', 'id': '2016'},

    # Batch 4: CVs 16-20
    {'name': 'Christopher_Contag', 'word_file': '2017_Christopher_Contag.docx', 'id': '2017'},
    {'name': 'Cook_Cv', 'word_file': '2018_Cook_Cv.docx', 'id': '2018'},
    {'name': 'Ut_Format', 'word_file': '2001_Ut_Format.docx', 'id': '2001'},
    {'name': 'Holtz', 'word_file': '2002_Holtz.docx', 'id': '2002'},
    {'name': 'Bpg', 'word_file': '2005_Bpg.docx', 'id': '2005'},
]

BASE_DIR = Path('/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project')
WORD_DIR = BASE_DIR / 'data' / 'sample_cvs' / 'word'


def segment_word_cv(word_path: Path, expected_output_path: Path) -> bool:
    """Segment a Word CV using cv_segmenter (direct import)."""
    print(f"  [1/4] Segmenting Word CV...")

    try:
        # Add unified_pipeline directory to sys.path for imports
        # Path: core/ -> unified_pipeline/
        import sys
        unified_pipeline_dir = Path(__file__).parent.parent
        if str(unified_pipeline_dir) not in sys.path:
            sys.path.insert(0, str(unified_pipeline_dir))

        from segmentation.word_chunked import segment_word_cv_chunked

        # Segmentation script creates filename based on input docx
        # Pass the output directory (parent of expected_output_path)
        output_dir = expected_output_path.parent

        # Call segmentation function directly
        seg_data = segment_word_cv_chunked(str(word_path), str(output_dir))

        # Get stats
        num_sections = seg_data.get('meta', {}).get('num_top_level_groups', 0)
        total_entries = seg_data.get('meta', {}).get('total_entries', 0)

        print(f"    ✓ Segmented: {num_sections} sections, {total_entries} entries")
        return True

    except Exception as e:
        print(f"    ✗ Segmentation failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def repair_cv(segmented_path: Path, repaired_path: Path) -> bool:
    """Apply segmentation repairs."""
    print(f"  [2/4] Repairing segmentation...")

    try:
        with open(segmented_path) as f:
            segmented_cv = json.load(f)

        repaired_cv = repair_segmentation(segmented_cv, verbose=False)

        with open(repaired_path, 'w') as f:
            json.dump(repaired_cv, f, indent=2)

        # Get repair stats
        orig_groups = segmented_cv['meta']['num_top_level_groups']
        final_groups = repaired_cv['meta']['repair']['repaired_groups']

        print(f"    ✓ Repaired: {orig_groups} → {final_groups} top-level groups")
        return True

    except Exception as e:
        print(f"    ✗ Repair failed: {str(e)}")
        return False


def preprocess_cv(repaired_path: Path, preprocessed_path: Path) -> bool:
    """Filter empty groups."""
    print(f"  [3/4] Preprocessing...")

    try:
        with open(repaired_path) as f:
            repaired_cv = json.load(f)

        preprocessed_cv = filter_valid_groups(repaired_cv, verbose=False)

        with open(preprocessed_path, 'w') as f:
            json.dump(preprocessed_cv, f, indent=2)

        stats = preprocessed_cv['meta']['preprocessing']
        print(f"    ✓ Preprocessed: {stats['content_groups']} content groups, "
              f"{stats['preserved_headers']} headers preserved")
        return True

    except Exception as e:
        print(f"    ✗ Preprocessing failed: {str(e)}")
        return False


def map_cv(preprocessed_path: Path, mapped_path: Path) -> dict:
    """Map with v3 signals."""
    print(f"  [4/4] Mapping with v3 signals...")

    try:
        audit_report = analyze_signal_effectiveness(
            str(preprocessed_path),
            str(mapped_path)
        )

        stats = audit_report['signal_effectiveness']
        print(f"    ✓ Mapped: {stats['total_entries']} entries, "
              f"{stats['hint_coverage_pct']:.1f}% hint coverage, "
              f"avg conf={audit_report['mapping_stats']['avg_confidence']:.3f}")

        return {
            'status': 'SUCCESS',
            'stats': {
                'total_entries': stats['total_entries'],
                'hint_coverage_pct': stats['hint_coverage_pct'],
                'avg_confidence': audit_report['mapping_stats']['avg_confidence'],
                'total_api_calls': audit_report['mapping_stats']['total_api_calls'],
                'total_tokens': audit_report['mapping_stats']['total_tokens']
            }
        }

    except Exception as e:
        print(f"    ✗ Mapping failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'status': 'ERROR',
            'error': str(e)
        }


def save_structured_metrics(cv_info: dict, result: dict, repaired_path: Path, mapped_path: Path) -> None:
    """Save structured metrics for aggregation."""
    metrics_path = Path(f"validation_CV_{cv_info['id']}_{cv_info['name']}_METRICS.json")

    # Base metrics
    metrics = {
        'cv_id': cv_info['id'],
        'cv_name': cv_info['name'],
        'status': result['status']
    }

    if result['status'] == 'ERROR':
        metrics['failed_stage'] = result.get('step', 'unknown')
        metrics['error_message'] = result.get('error', 'No error message')
    else:
        # Repair stats
        try:
            with open(repaired_path) as f:
                repaired_cv = json.load(f)

            repair_meta = repaired_cv.get('meta', {}).get('repair', {})
            metrics['repair'] = {
                'groups_before': repair_meta.get('original_groups', 0),
                'groups_after': repair_meta.get('repaired_groups', 0),
                'repairs_applied': repair_meta.get('repairs_applied', [])
            }
        except Exception as e:
            metrics['repair'] = {'error': str(e)}

        # Final metrics
        metrics['final_metrics'] = result.get('stats', {})

        # Taxonomy issues from AUDIT file
        audit_path = Path(str(mapped_path).replace('.json', '_AUDIT.json'))
        taxonomy_issues = []
        signals_fired = {}

        try:
            with open(audit_path) as f:
                audit = json.load(f)

            # Low confidence sections
            for mapping in audit.get('mappings', []):
                if mapping.get('final_confidence', 1.0) < 0.80:
                    taxonomy_issues.append({
                        'source_label': mapping.get('source_label', ''),
                        'canonical_name': mapping.get('final_canonical_name', ''),
                        'confidence': mapping.get('final_confidence', 0)
                    })

            # Signals fired
            signal_summary = audit.get('signal_detection_summary', {})
            for signal_name, data in signal_summary.items():
                if signal_name != 'total_entries_with_hints':
                    signals_fired[signal_name] = data.get('count', 0)

        except Exception as e:
            pass  # Audit file optional

        metrics['taxonomy_issues'] = taxonomy_issues
        metrics['signals_fired'] = signals_fired

        # Structural issues detection
        structural_issues = []

        # Check for high Unknown group count
        try:
            with open(mapped_path) as f:
                mapped_cv = json.load(f)

            unknown_groups = sum(1 for m in mapped_cv.get('mappings', [])
                               if 'Unknown' in m.get('source_label', ''))
            total_groups = len(mapped_cv.get('mappings', []))

            if total_groups > 0:
                unknown_pct = (unknown_groups / total_groups) * 100
                if unknown_pct > 40:
                    structural_issues.append(f'high_unknown_groups_{unknown_pct:.0f}pct')

            # Check for low hint coverage
            if metrics['final_metrics'].get('hint_coverage_pct', 0) < 35:
                structural_issues.append('low_hint_coverage')

            # Check for low confidence
            if metrics['final_metrics'].get('avg_confidence', 1.0) < 0.85:
                structural_issues.append('low_confidence')

        except Exception as e:
            pass

        metrics['structural_issues'] = structural_issues

    # Save metrics
    with open(metrics_path, 'w') as f:
        json.dump(metrics, f, indent=2)

    print(f"    📊 Metrics saved: {metrics_path}")


def process_cv(cv_info: dict) -> dict:
    """Process a single CV through the full pipeline."""
    print(f"\n{'='*80}")
    print(f"Processing: {cv_info['name']} ({cv_info['id']})")
    print(f"{'='*80}")

    # Define paths
    word_path = WORD_DIR / cv_info['word_file']
    # Segmentation creates a directory with JSON inside it
    segmented_dir = WORD_DIR / f"{Path(cv_info['word_file']).stem}_segmented.json"
    segmented_path = segmented_dir / f"{Path(cv_info['word_file']).stem}_segmented.json"
    repaired_path = Path(f"validation_CV_{cv_info['id']}_{cv_info['name']}_segmented_repaired.json")
    preprocessed_path = Path(f"validation_CV_{cv_info['id']}_{cv_info['name']}_preprocessed.json")
    mapped_path = Path(f"validation_CV_{cv_info['id']}_{cv_info['name']}_with_signals_v3.json")

    # Step 1: Segment (if not already segmented)
    if not segmented_path.exists():
        if not segment_word_cv(word_path, segmented_path):
            result = {'cv': cv_info['name'], 'id': cv_info['id'], 'status': 'ERROR', 'step': 'segmentation'}
            save_structured_metrics(cv_info, result, repaired_path, mapped_path)
            return result
    else:
        print(f"  [1/4] Segmentation exists, skipping...")

    # Step 2: Repair
    if not repair_cv(segmented_path, repaired_path):
        result = {'cv': cv_info['name'], 'id': cv_info['id'], 'status': 'ERROR', 'step': 'repair'}
        save_structured_metrics(cv_info, result, repaired_path, mapped_path)
        return result

    # Step 3: Preprocess
    if not preprocess_cv(repaired_path, preprocessed_path):
        result = {'cv': cv_info['name'], 'id': cv_info['id'], 'status': 'ERROR', 'step': 'preprocessing'}
        save_structured_metrics(cv_info, result, repaired_path, mapped_path)
        return result

    # Step 4: Map
    map_result = map_cv(preprocessed_path, mapped_path)

    result = {
        'cv': cv_info['name'],
        'id': cv_info['id'],
        'status': map_result['status'],
        'files': {
            'segmented': str(segmented_path),
            'repaired': str(repaired_path),
            'preprocessed': str(preprocessed_path),
            'mapped': str(mapped_path),
            'audit': str(mapped_path).replace('.json', '_AUDIT.json')
        },
        **map_result
    }

    # Save structured metrics
    save_structured_metrics(cv_info, result, repaired_path, mapped_path)

    return result


def main():
    print("="*80)
    print("BATCH CV PROCESSING WITH SEGMENTATION REPAIR")
    print("="*80)
    print(f"\nProcessing {len(CVS_TO_PROCESS)} CVs:")
    for cv in CVS_TO_PROCESS:
        print(f"  • {cv['name']} ({cv['id']})")
    print()

    results = []

    for cv_info in CVS_TO_PROCESS:
        result = process_cv(cv_info)
        results.append(result)

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
            step = result.get('step', result.get('error', 'unknown'))
            print(f"  {result['cv']}: Failed at {step}")

    # Save summary
    summary_path = 'batch_processing_with_repair_summary.json'
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
