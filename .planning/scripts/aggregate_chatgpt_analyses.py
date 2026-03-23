"""
Aggregate ChatGPT Analyses for Phase 1 Pattern Analysis

After sending all 20 CVs to ChatGPT one at a time, this script aggregates
the responses to identify patterns and prioritize fixes.

Usage:
    python3 aggregate_chatgpt_analyses.py

Expected input files:
    validation_CV_{id}_{name}_CHATGPT_ANALYSIS.json (20 files)

Output files:
    phase1_chatgpt_aggregated.json - Full aggregated data
    phase1_implementation_plan.md - Prioritized action plan
"""

import json
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List, Any


def load_chatgpt_analyses(results_dir: Path = Path('.')) -> List[Dict]:
    """Load all ChatGPT analysis JSON files."""
    analyses = []

    for analysis_file in sorted(results_dir.glob('validation_CV_*_CHATGPT_ANALYSIS.json')):
        try:
            with open(analysis_file) as f:
                data = json.load(f)
                analyses.append(data)
        except Exception as e:
            print(f"⚠️  Warning: Could not load {analysis_file.name}: {e}")

    return analyses


def aggregate_chatgpt_analyses(analyses: List[Dict]) -> Dict[str, Any]:
    """Aggregate all ChatGPT analyses into actionable patterns."""

    total_cvs = len(analyses)

    # Overall quality metrics
    quality_scores = [a['overall_quality'] for a in analyses]
    confidence_scores = [a['confidence_score'] for a in analyses]
    hint_coverages = [a['hint_coverage'] for a in analyses]

    # Issue aggregation by type and description
    issue_patterns = defaultdict(lambda: {
        'count': 0,
        'total_severity': 0,
        'cv_ids': [],
        'examples': [],
        'fix_type': None
    })

    for analysis in analyses:
        cv_id = analysis['cv_id']
        cv_name = analysis['cv_name']

        for issue in analysis.get('issues_found', []):
            # Create a key based on issue_type + description pattern
            issue_key = f"{issue['issue_type']}::{issue['description'][:80]}"

            issue_patterns[issue_key]['count'] += 1
            issue_patterns[issue_key]['total_severity'] += issue['severity']
            issue_patterns[issue_key]['cv_ids'].append(cv_id)
            issue_patterns[issue_key]['fix_type'] = issue['fix_type']

            if len(issue_patterns[issue_key]['examples']) < 3:
                issue_patterns[issue_key]['examples'].append({
                    'cv_id': cv_id,
                    'cv_name': cv_name,
                    'description': issue['description'],
                    'severity': issue['severity']
                })

    # Filter for recurring issues (3+ CVs)
    recurring_issues = {
        key: {
            'issue_type': key.split('::')[0],
            'description': key.split('::')[1],
            'count': data['count'],
            'avg_severity': round(data['total_severity'] / data['count'], 1),
            'fix_type': data['fix_type'],
            'cv_ids': data['cv_ids'],
            'examples': data['examples']
        }
        for key, data in issue_patterns.items()
        if data['count'] >= 3
    }

    # Aggregate all priority fixes across all CVs
    all_fixes = []
    for analysis in analyses:
        for fix in analysis.get('priority_fixes', []):
            all_fixes.append({
                'cv_id': analysis['cv_id'],
                'cv_name': analysis['cv_name'],
                'rank': fix['rank'],
                'fix': fix['fix'],
                'effort': fix['effort'],
                'impact': fix['impact'],
                'priority_score': fix['priority_score']
            })

    # Sort all fixes by priority_score
    all_fixes_sorted = sorted(all_fixes, key=lambda x: -x['priority_score'])

    # Group similar fixes
    fix_patterns = defaultdict(lambda: {
        'fixes': [],
        'total_priority': 0,
        'count': 0,
        'avg_effort': 0,
        'avg_impact': 0
    })

    for fix in all_fixes:
        # Simple grouping by first 60 chars of fix description
        fix_key = fix['fix'][:60]
        fix_patterns[fix_key]['fixes'].append(fix)
        fix_patterns[fix_key]['total_priority'] += fix['priority_score']
        fix_patterns[fix_key]['count'] += 1
        fix_patterns[fix_key]['avg_effort'] += fix['effort']
        fix_patterns[fix_key]['avg_impact'] += fix['impact']

    # Finalize fix pattern averages
    for key, data in fix_patterns.items():
        data['avg_effort'] = round(data['avg_effort'] / data['count'], 1)
        data['avg_impact'] = round(data['avg_impact'] / data['count'], 1)
        data['avg_priority'] = round(data['total_priority'] / data['count'], 1)

    # Sort fix patterns by total_priority
    fix_patterns_sorted = sorted(
        fix_patterns.items(),
        key=lambda x: -x[1]['total_priority']
    )

    # Issue type breakdown
    issue_type_counts = Counter()
    for analysis in analyses:
        for issue in analysis.get('issues_found', []):
            issue_type_counts[issue['issue_type']] += 1

    # Fix type breakdown
    fix_type_counts = Counter()
    for pattern in recurring_issues.values():
        fix_type_counts[pattern['fix_type']] += pattern['count']

    # LLM trigger rate estimation
    llm_needed_count = sum(1 for a in analyses if a['overall_quality'] < 7)
    llm_trigger_rate = (llm_needed_count / total_cvs * 100) if total_cvs > 0 else 0

    # Low performers
    low_quality = sorted(
        [{'cv_id': a['cv_id'], 'cv_name': a['cv_name'], 'quality': a['overall_quality']}
         for a in analyses if a['overall_quality'] < 7],
        key=lambda x: x['quality']
    )

    return {
        'summary': {
            'total_cvs': total_cvs,
            'avg_quality': round(sum(quality_scores) / total_cvs, 1) if total_cvs > 0 else 0,
            'avg_confidence': round(sum(confidence_scores) / total_cvs, 3) if total_cvs > 0 else 0,
            'avg_hint_coverage': round(sum(hint_coverages) / total_cvs, 1) if total_cvs > 0 else 0,
            'llm_trigger_rate': round(llm_trigger_rate, 1)
        },
        'recurring_issues': dict(sorted(
            recurring_issues.items(),
            key=lambda x: (-x[1]['count'], -x[1]['avg_severity'])
        )),
        'issue_type_breakdown': dict(issue_type_counts.most_common()),
        'fix_type_breakdown': dict(fix_type_counts),
        'top_priority_fixes': all_fixes_sorted[:20],  # Top 20 individual fixes
        'fix_patterns': [
            {
                'pattern': key,
                'count': data['count'],
                'avg_effort': data['avg_effort'],
                'avg_impact': data['avg_impact'],
                'avg_priority': data['avg_priority'],
                'total_priority': data['total_priority'],
                'example_cvs': [f['cv_name'] for f in data['fixes'][:3]]
            }
            for key, data in fix_patterns_sorted[:15]  # Top 15 patterns
        ],
        'low_performers': low_quality,
        'per_cv_summary': [
            {
                'cv_id': a['cv_id'],
                'cv_name': a['cv_name'],
                'overall_quality': a['overall_quality'],
                'confidence': a['confidence_score'],
                'hint_coverage': a['hint_coverage'],
                'num_issues': len(a.get('issues_found', [])),
                'top_priority_fix': a.get('priority_fixes', [{}])[0].get('fix', 'N/A')[:60]
            }
            for a in analyses
        ]
    }


def generate_implementation_plan(aggregated: Dict[str, Any]) -> str:
    """Generate markdown implementation plan."""

    summary = aggregated['summary']
    recurring = aggregated['recurring_issues']
    fix_patterns = aggregated['fix_patterns']
    issue_breakdown = aggregated['issue_type_breakdown']
    fix_breakdown = aggregated['fix_type_breakdown']

    report = f"""# PHASE 1 IMPLEMENTATION PLAN - {summary['total_cvs']} CVs Analyzed

## OVERALL ASSESSMENT

- **Average Quality Score**: {summary['avg_quality']}/10
- **Average Confidence**: {summary['avg_confidence']:.3f}
- **Average Hint Coverage**: {summary['avg_hint_coverage']:.1f}%
- **Estimated LLM Trigger Rate**: {summary['llm_trigger_rate']:.1f}% ({int(summary['llm_trigger_rate'] * summary['total_cvs'] / 100)} CVs need Tier 2)

## ISSUE TYPE BREAKDOWN

"""

    for issue_type, count in issue_breakdown.items():
        report += f"- **{issue_type}**: {count} occurrences\n"

    report += f"\n## FIX TYPE DISTRIBUTION\n\n"

    for fix_type, count in fix_breakdown.items():
        report += f"- **{fix_type}**: {count} issues\n"

    report += f"\n## RECURRING ISSUES (3+ CVs)\n\n"
    report += f"Found {len(recurring)} recurring issue patterns:\n\n"

    for i, (key, issue) in enumerate(list(recurring.items())[:10], 1):
        report += f"### {i}. {issue['issue_type'].upper()}: {issue['description'][:70]}...\n\n"
        report += f"- **Occurrences**: {issue['count']} CVs\n"
        report += f"- **Avg Severity**: {issue['avg_severity']}/10\n"
        report += f"- **Fix Type**: {issue['fix_type']}\n"
        report += f"- **Affected CVs**: {', '.join(issue['cv_ids'][:5])}\n"
        report += f"\n**Examples:**\n"
        for ex in issue['examples'][:2]:
            report += f"- CV {ex['cv_id']} ({ex['cv_name']}): {ex['description'][:80]}...\n"
        report += "\n"

    report += f"\n## TOP PRIORITY FIX PATTERNS\n\n"
    report += "Prioritized by total_priority (sum of priority_scores across all CVs):\n\n"

    for i, pattern in enumerate(fix_patterns[:10], 1):
        report += f"### {i}. {pattern['pattern']}...\n\n"
        report += f"- **Occurrences**: {pattern['count']} CVs\n"
        report += f"- **Avg Effort**: {pattern['avg_effort']}/10\n"
        report += f"- **Avg Impact**: {pattern['avg_impact']}/10\n"
        report += f"- **Avg Priority Score**: {pattern['avg_priority']:.1f}\n"
        report += f"- **Total Priority**: {pattern['total_priority']}\n"
        report += f"- **Example CVs**: {', '.join(pattern['example_cvs'])}\n"
        report += "\n"

    report += f"\n## RECOMMENDED ACTION PLAN\n\n"
    report += "### Phase 1A: Deterministic Fixes (Tier 1)\n\n"

    deterministic_count = fix_breakdown.get('DETERMINISTIC', 0)
    report += f"Implement {deterministic_count} deterministic fixes for issues appearing in 3+ CVs:\n\n"

    deterministic_issues = [
        (key, issue) for key, issue in recurring.items()
        if issue['fix_type'] == 'DETERMINISTIC'
    ]

    for i, (key, issue) in enumerate(deterministic_issues[:5], 1):
        report += f"{i}. **{issue['issue_type']}**: {issue['description'][:60]}... ({issue['count']} CVs, severity {issue['avg_severity']}/10)\n"

    report += f"\n### Phase 1B: LLM Trigger Heuristics (Tier 2)\n\n"

    llm_count = fix_breakdown.get('LLM', 0)
    report += f"Implement LLM triggers for {llm_count} ambiguous patterns:\n\n"

    llm_issues = [
        (key, issue) for key, issue in recurring.items()
        if issue['fix_type'] == 'LLM'
    ]

    for i, (key, issue) in enumerate(llm_issues[:5], 1):
        report += f"{i}. **{issue['issue_type']}**: {issue['description'][:60]}... ({issue['count']} CVs, severity {issue['avg_severity']}/10)\n"

    report += f"\n### Phase 1C: Document Edge Cases (Tier 3)\n\n"

    manual_count = fix_breakdown.get('MANUAL', 0)
    report += f"Document {manual_count} edge cases requiring human review:\n\n"

    manual_issues = [
        (key, issue) for key, issue in recurring.items()
        if issue['fix_type'] == 'MANUAL'
    ]

    for i, (key, issue) in enumerate(manual_issues[:3], 1):
        report += f"{i}. **{issue['issue_type']}**: {issue['description'][:60]}... ({issue['count']} CVs, severity {issue['avg_severity']}/10)\n"

    report += f"\n## NEXT STEPS\n\n"
    report += "1. Review this plan and prioritize fixes\n"
    report += "2. Implement Phase 1A deterministic fixes\n"
    report += "3. Test on 3-5 CVs to validate improvements\n"
    report += "4. Implement Phase 1B LLM triggers\n"
    report += "5. Document Phase 1C edge cases\n"
    report += "6. Reprocess 20 CVs to measure improvement\n"
    report += "7. If quality improved, continue to CVs 21-40\n"

    return report


def main():
    print("="*80)
    print("AGGREGATING CHATGPT ANALYSES")
    print("="*80)

    # Load all ChatGPT analyses
    analyses = load_chatgpt_analyses()
    print(f"\nLoaded {len(analyses)} ChatGPT analyses")

    if len(analyses) == 0:
        print("\n❌ No ChatGPT analysis files found!")
        print("Expected files: validation_CV_{id}_{name}_CHATGPT_ANALYSIS.json")
        return 1

    # Aggregate
    aggregated = aggregate_chatgpt_analyses(analyses)

    # Save aggregated data
    with open('phase1_chatgpt_aggregated.json', 'w') as f:
        json.dump(aggregated, f, indent=2)
    print(f"✓ Saved: phase1_chatgpt_aggregated.json")

    # Generate implementation plan
    plan = generate_implementation_plan(aggregated)

    with open('phase1_implementation_plan.md', 'w') as f:
        f.write(plan)
    print(f"✓ Saved: phase1_implementation_plan.md")

    print("\n" + "="*80)
    print("IMPLEMENTATION PLAN")
    print("="*80)
    print(plan)

    print("\n" + "="*80)
    print(f"Review 'phase1_implementation_plan.md' to prioritize fixes!")
    print("="*80)

    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
