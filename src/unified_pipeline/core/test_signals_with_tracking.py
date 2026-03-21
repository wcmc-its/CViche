"""
Test Comprehensive Signal Library with Effectiveness Tracking

This script:
1. Runs taxonomy mapping with all 19 signals enabled
2. Tracks which signals fire for each entry
3. Generates audit report for ChatGPT review
4. Compares results to baseline (without signals)
"""

import json
import sys
from pathlib import Path
from taxonomy_mapper_v2 import map_cv_sections_v2
from confusion_matrix import compute_structural_hints

def analyze_signal_effectiveness(cv_path, output_path):
    """Run mapping and track signal effectiveness."""

    print(f"\n{'='*70}")
    print(f"TESTING COMPREHENSIVE SIGNAL LIBRARY")
    print(f"{'='*70}")
    print(f"Input: {cv_path}")
    print(f"Output: {output_path}")

    # Load preprocessed CV
    with open(cv_path, 'r') as f:
        cv_data = json.load(f)

    # Track signal stats across all entries
    signal_stats = {
        'total_entries': 0,
        'entries_with_hints': 0,
        'signal_fire_counts': {},
        'signal_by_section': {},
        'examples_by_signal': {}
    }

    # Analyze all entries to see which signals would fire
    print(f"\n{'='*70}")
    print(f"PRE-ANALYSIS: Signal Detection")
    print(f"{'='*70}")

    for group in cv_data.get('groups', []):
        section_label = group.get('label', 'Unknown')
        signal_stats['signal_by_section'][section_label] = {
            'total_entries': 0,
            'entries_with_hints': 0,
            'fired_signals': {}
        }

        for entry in group.get('entries', []):
            signal_stats['total_entries'] += 1
            signal_stats['signal_by_section'][section_label]['total_entries'] += 1

            entry_text = entry.get('text_snippet', '')
            hints = compute_structural_hints(entry_text, section_label, track_effectiveness=True)

            if hints['triggered_hints']:
                signal_stats['entries_with_hints'] += 1
                signal_stats['signal_by_section'][section_label]['entries_with_hints'] += 1

            # Track which signals fired
            if 'signal_summary' in hints:
                for signal_name in hints['signal_summary']['fired_signal_names']:
                    # Global counts
                    signal_stats['signal_fire_counts'][signal_name] = \
                        signal_stats['signal_fire_counts'].get(signal_name, 0) + 1

                    # Per-section counts
                    section_signals = signal_stats['signal_by_section'][section_label]['fired_signals']
                    section_signals[signal_name] = section_signals.get(signal_name, 0) + 1

                    # Store examples
                    if signal_name not in signal_stats['examples_by_signal']:
                        signal_stats['examples_by_signal'][signal_name] = []

                    if len(signal_stats['examples_by_signal'][signal_name]) < 3:
                        signal_stats['examples_by_signal'][signal_name].append({
                            'section': section_label,
                            'entry': entry_text[:150] + '...' if len(entry_text) > 150 else entry_text
                        })

    # Print signal summary
    print(f"\nSignal Detection Summary:")
    print(f"  Total entries: {signal_stats['total_entries']}")
    print(f"  Entries with hints: {signal_stats['entries_with_hints']} "
          f"({100*signal_stats['entries_with_hints']/signal_stats['total_entries']:.1f}%)")

    print(f"\nSignal Fire Counts (sorted by frequency):")
    for signal, count in sorted(signal_stats['signal_fire_counts'].items(),
                                  key=lambda x: x[1], reverse=True):
        pct = 100 * count / signal_stats['total_entries']
        print(f"  {signal:30} {count:3} ({pct:5.1f}%)")

        # Show examples
        if signal in signal_stats['examples_by_signal']:
            for i, ex in enumerate(signal_stats['examples_by_signal'][signal][:2], 1):
                print(f"    Ex{i}: [{ex['section']}] {ex['entry']}")

    # Run full mapping
    print(f"\n{'='*70}")
    print(f"RUNNING TAXONOMY MAPPING WITH ALL SIGNALS")
    print(f"{'='*70}")

    result = map_cv_sections_v2(cv_path, output_path)

    # Generate audit report
    print(f"\n{'='*70}")
    print(f"GENERATING AUDIT REPORT")
    print(f"{'='*70}")

    audit_report = {
        'cv_file': Path(cv_path).name,
        'output_file': Path(output_path).name,
        'mapping_stats': {
            'total_sections': result['stats']['classified_sections'],
            'high_confidence': result['stats']['high_confidence'],
            'avg_confidence': result['stats']['avg_confidence'],
            'total_api_calls': result['stats']['total_api_calls'],
            'total_tokens': result['token_usage']['total_tokens']
        },
        'signal_effectiveness': {
            'total_entries': signal_stats['total_entries'],
            'entries_with_hints': signal_stats['entries_with_hints'],
            'hint_coverage_pct': 100 * signal_stats['entries_with_hints'] / signal_stats['total_entries'],
            'signal_fire_counts': signal_stats['signal_fire_counts'],
            'examples_by_signal': signal_stats['examples_by_signal']
        },
        'section_breakdown': signal_stats['signal_by_section']
    }

    # Save audit report
    audit_path = output_path.replace('.json', '_AUDIT.json')
    with open(audit_path, 'w') as f:
        json.dump(audit_report, f, indent=2)

    print(f"\n✓ Audit report saved to: {audit_path}")

    return audit_report


def compare_to_baseline(new_results_path, baseline_path):
    """Compare new results to baseline (without signals)."""

    print(f"\n{'='*70}")
    print(f"COMPARISON TO BASELINE")
    print(f"{'='*70}")

    with open(new_results_path, 'r') as f:
        new_data = json.load(f)

    with open(baseline_path, 'r') as f:
        baseline_data = json.load(f)

    # Compare mappings
    changes = []
    for new_m, baseline_m in zip(new_data['mappings'], baseline_data['mappings']):
        if new_m['source_label'] != baseline_m['source_label']:
            continue  # Different sections, skip

        if new_m['final_section_id'] != baseline_m['final_section_id']:
            changes.append({
                'section': new_m['source_label'],
                'baseline': baseline_m['final_section_id'],
                'new': new_m['final_section_id'],
                'baseline_conf': baseline_m.get('pass1_confidence', 0),
                'new_conf': new_m.get('pass1_confidence', 0)
            })

    print(f"\nClassification Changes: {len(changes)}")
    for change in changes:
        direction = "✅ FIX" if change['new_conf'] > change['baseline_conf'] else "⚠️  REGRESSION"
        print(f"  {direction} {change['section'][:40]:40}")
        print(f"       Baseline: {change['baseline']} (conf={change['baseline_conf']:.2f})")
        print(f"       New:      {change['new']} (conf={change['new_conf']:.2f})")

    if not changes:
        print("  No changes detected")

    return changes


if __name__ == '__main__':
    # Test on Holtz CV
    cv_path = 'validation_CV_2002_Holtz_Heidi_PhD_preprocessed.json'
    output_path = 'holtz_with_all_signals.json'
    baseline_path = 'holtz_fixed.json'  # Results without new signals

    # Run analysis
    audit_report = analyze_signal_effectiveness(cv_path, output_path)

    # Compare to baseline if available
    if Path(baseline_path).exists():
        changes = compare_to_baseline(output_path, baseline_path)

    print(f"\n{'='*70}")
    print(f"SUMMARY FOR CHATGPT REVIEW")
    print(f"{'='*70}")
    print(f"\nFiles to share with ChatGPT:")
    print(f"  1. Preprocessed CV: {cv_path}")
    print(f"  2. Mapped output: {output_path}")
    print(f"  3. Audit report: {output_path.replace('.json', '_AUDIT.json')}")
    print(f"\nKey Questions for ChatGPT:")
    print(f"  1. Are the classifications correct?")
    print(f"  2. Which signals are helping vs causing regressions?")
    print(f"  3. What additional signals might be needed?")
    print(f"  4. Are there false positives (signals firing incorrectly)?")
