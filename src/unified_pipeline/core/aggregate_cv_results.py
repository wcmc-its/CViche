"""
Aggregate CV Processing Results for Pattern Analysis

Reads all per-CV result JSONs and creates summary statistics for ChatGPT analysis.
"""

import json
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List, Any


def load_all_cv_results(results_dir: Path = Path('.')) -> List[Dict]:
    """Load all validation_CV_*_METRICS.json files."""
    results = []

    for metrics_file in sorted(results_dir.glob('validation_CV_*_METRICS.json')):
        with open(metrics_file) as f:
            data = json.load(f)
            results.append(data)

    return results


def aggregate_results(results: List[Dict]) -> Dict[str, Any]:
    """Aggregate all CV results into summary statistics."""

    total_cvs = len(results)
    successful = [r for r in results if r['status'] == 'SUCCESS']
    failed = [r for r in results if r['status'] == 'ERROR']

    # Stage failures
    failure_stages = Counter(r.get('failed_stage', 'unknown') for r in failed)

    # Repair effectiveness
    repair_stats = {
        'total_repairs_applied': Counter(),
        'avg_group_reduction': 0,
        'groups_before': [],
        'groups_after': []
    }

    for r in results:
        if 'repair' in r:
            for repair_type in r['repair'].get('repairs_applied', []):
                repair_stats['total_repairs_applied'][repair_type] += 1
            repair_stats['groups_before'].append(r['repair'].get('groups_before', 0))
            repair_stats['groups_after'].append(r['repair'].get('groups_after', 0))

    if repair_stats['groups_before']:
        avg_before = sum(repair_stats['groups_before']) / len(repair_stats['groups_before'])
        avg_after = sum(repair_stats['groups_after']) / len(repair_stats['groups_after'])
        repair_stats['avg_group_reduction'] = avg_before - avg_after

    # Final metrics
    confidences = [r['final_metrics']['avg_confidence'] for r in successful if 'final_metrics' in r]
    hint_coverages = [r['final_metrics']['hint_coverage_pct'] for r in successful if 'final_metrics' in r]

    # Low confidence CVs
    low_confidence_cvs = [
        {'cv_id': r['cv_id'], 'cv_name': r['cv_name'], 'confidence': r['final_metrics']['avg_confidence']}
        for r in successful
        if r['final_metrics']['avg_confidence'] < 0.85
    ]

    # Low hint coverage CVs
    low_hint_cvs = [
        {'cv_id': r['cv_id'], 'cv_name': r['cv_name'], 'hint_coverage': r['final_metrics']['hint_coverage_pct']}
        for r in successful
        if r['final_metrics']['hint_coverage_pct'] < 35
    ]

    # Taxonomy issues (sections with low confidence)
    taxonomy_issues = defaultdict(list)
    for r in successful:
        for issue in r.get('taxonomy_issues', []):
            taxonomy_issues[issue['canonical_name']].append({
                'cv_id': r['cv_id'],
                'source_label': issue['source_label'],
                'confidence': issue['confidence']
            })

    # Signal effectiveness
    signal_fires = Counter()
    for r in successful:
        for signal_name, count in r.get('signals_fired', {}).items():
            signal_fires[signal_name] += count

    # Recurring structural issues
    structural_issues = defaultdict(int)
    for r in results:
        for issue in r.get('structural_issues', []):
            structural_issues[issue] += 1

    return {
        'summary': {
            'total_cvs': total_cvs,
            'successful': len(successful),
            'failed': len(failed),
            'success_rate': len(successful) / total_cvs if total_cvs > 0 else 0,
            'avg_confidence': sum(confidences) / len(confidences) if confidences else 0,
            'avg_hint_coverage': sum(hint_coverages) / len(hint_coverages) if hint_coverages else 0,
        },
        'failures': {
            'total': len(failed),
            'by_stage': dict(failure_stages),
            'details': [
                {
                    'cv_id': r['cv_id'],
                    'cv_name': r['cv_name'],
                    'stage': r.get('failed_stage', 'unknown'),
                    'error': r.get('error_message', 'No error message')
                }
                for r in failed
            ]
        },
        'repair_effectiveness': {
            'repairs_applied': dict(repair_stats['total_repairs_applied']),
            'avg_group_reduction': round(repair_stats['avg_group_reduction'], 2),
            'cvs_with_repairs': len([r for r in results if r.get('repair', {}).get('repairs_applied')])
        },
        'low_performers': {
            'low_confidence': sorted(low_confidence_cvs, key=lambda x: x['confidence']),
            'low_hint_coverage': sorted(low_hint_cvs, key=lambda x: x['hint_coverage'])
        },
        'taxonomy_issues': {
            section: {
                'occurrences': len(issues),
                'avg_confidence': sum(i['confidence'] for i in issues) / len(issues),
                'examples': issues[:3]  # First 3 examples
            }
            for section, issues in taxonomy_issues.items()
            if len(issues) >= 3  # Only recurring issues
        },
        'signal_effectiveness': dict(signal_fires.most_common(15)),
        'structural_issues': {
            issue: count
            for issue, count in structural_issues.items()
            if count >= 3  # Only issues appearing in 3+ CVs
        },
        'per_cv_details': [
            {
                'cv_id': r['cv_id'],
                'cv_name': r['cv_name'],
                'status': r['status'],
                'confidence': r.get('final_metrics', {}).get('avg_confidence', 0),
                'hint_coverage': r.get('final_metrics', {}).get('hint_coverage_pct', 0),
                'entries': r.get('final_metrics', {}).get('total_entries', 0),
                'repairs_applied': r.get('repair', {}).get('repairs_applied', []),
                'structural_issues': r.get('structural_issues', [])
            }
            for r in results
        ]
    }


def generate_chatgpt_summary(aggregated: Dict[str, Any]) -> str:
    """Generate formatted summary for ChatGPT analysis."""

    summary = aggregated['summary']
    failures = aggregated['failures']
    repair = aggregated['repair_effectiveness']
    low = aggregated['low_performers']
    taxonomy = aggregated['taxonomy_issues']
    signals = aggregated['signal_effectiveness']
    structural = aggregated['structural_issues']

    report = f"""# CV PARSING PIPELINE ANALYSIS - {summary['total_cvs']} CVs

## OVERALL PERFORMANCE
- Success Rate: {summary['success_rate']:.1%} ({summary['successful']}/{summary['total_cvs']} CVs)
- Average Confidence: {summary['avg_confidence']:.3f}
- Average Hint Coverage: {summary['avg_hint_coverage']:.1f}%

## FAILURES ({failures['total']} CVs)
"""

    if failures['details']:
        report += "### Failed CVs:\n"
        for fail in failures['details']:
            report += f"- **{fail['cv_name']}** (ID: {fail['cv_id']}): Failed at {fail['stage']}\n"
            report += f"  Error: {fail['error'][:100]}...\n"
    else:
        report += "No failures!\n"

    report += f"\n## REPAIR EFFECTIVENESS\n"
    report += f"- CVs with repairs: {repair['cvs_with_repairs']}/{summary['total_cvs']}\n"
    report += f"- Avg group reduction: {repair['avg_group_reduction']:.1f} groups\n"
    report += f"### Most Applied Repairs:\n"
    for repair_type, count in sorted(repair['repairs_applied'].items(), key=lambda x: -x[1])[:5]:
        report += f"- **{repair_type}**: {count} CVs\n"

    report += f"\n## LOW PERFORMERS\n"
    report += f"### Low Confidence (<0.85) - {len(low['low_confidence'])} CVs:\n"
    for cv in low['low_confidence'][:10]:
        report += f"- **{cv['cv_name']}** (ID: {cv['cv_id']}): {cv['confidence']:.3f}\n"

    report += f"\n### Low Hint Coverage (<35%) - {len(low['low_hint_coverage'])} CVs:\n"
    for cv in low['low_hint_coverage'][:10]:
        report += f"- **{cv['cv_name']}** (ID: {cv['cv_id']}): {cv['hint_coverage']:.1f}%\n"

    report += f"\n## RECURRING TAXONOMY ISSUES (3+ CVs)\n"
    if taxonomy:
        for section, data in sorted(taxonomy.items(), key=lambda x: -x[1]['occurrences'])[:10]:
            report += f"### {section} - {data['occurrences']} occurrences, avg confidence: {data['avg_confidence']:.3f}\n"
            for ex in data['examples'][:2]:
                report += f"  - CV {ex['cv_id']}: \"{ex['source_label'][:60]}...\" → conf={ex['confidence']:.3f}\n"
    else:
        report += "No recurring taxonomy issues!\n"

    report += f"\n## SIGNAL EFFECTIVENESS (Top 15)\n"
    for signal, count in list(signals.items())[:15]:
        report += f"- **{signal}**: {count} fires across all CVs\n"

    report += f"\n## RECURRING STRUCTURAL ISSUES (3+ CVs)\n"
    if structural:
        for issue, count in sorted(structural.items(), key=lambda x: -x[1]):
            report += f"- **{issue}**: {count} CVs affected\n"
    else:
        report += "No recurring structural issues!\n"

    report += f"\n## PER-CV DETAILS\n"
    report += "| CV ID | Name | Status | Confidence | Hint % | Entries | Repairs | Issues |\n"
    report += "|-------|------|--------|-----------|--------|---------|---------|--------|\n"
    for cv in aggregated['per_cv_details']:
        repairs = ', '.join(cv['repairs_applied'][:2]) if cv['repairs_applied'] else 'None'
        issues = ', '.join(cv['structural_issues'][:2]) if cv['structural_issues'] else 'None'
        report += f"| {cv['cv_id']} | {cv['cv_name'][:12]} | {cv['status']} | {cv['confidence']:.3f} | {cv['hint_coverage']:.1f}% | {cv['entries']} | {repairs[:20]} | {issues[:20]} |\n"

    return report


def main():
    print("="*80)
    print("AGGREGATING CV PROCESSING RESULTS")
    print("="*80)

    # Load all results
    results = load_all_cv_results()
    print(f"\nLoaded {len(results)} CV results")

    # Aggregate
    aggregated = aggregate_results(results)

    # Save aggregated data
    with open('phase1_aggregated_results.json', 'w') as f:
        json.dump(aggregated, f, indent=2)
    print(f"✓ Saved: phase1_aggregated_results.json")

    # Generate ChatGPT summary
    summary = generate_chatgpt_summary(aggregated)

    with open('phase1_chatgpt_summary.md', 'w') as f:
        f.write(summary)
    print(f"✓ Saved: phase1_chatgpt_summary.md")

    print("\n" + "="*80)
    print("SUMMARY FOR CHATGPT")
    print("="*80)
    print(summary)

    print("\n" + "="*80)
    print(f"Send 'phase1_chatgpt_summary.md' to ChatGPT for pattern analysis!")
    print("="*80)


if __name__ == '__main__':
    main()
