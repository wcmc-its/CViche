#!/usr/bin/env python3
"""
Aggregate ChatGPT feedback from Phase 1 validation to identify patterns and prioritize fixes.
"""

import json
import glob
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List

FEEDBACK_DIR = "/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project/chatgpt_feedback_2025_11_08"

def load_all_feedback() -> List[Dict]:
    """Load all ChatGPT feedback files."""
    feedback_files = glob.glob(f"{FEEDBACK_DIR}/*.json")
    all_feedback = []

    for filepath in sorted(feedback_files):
        with open(filepath, 'r') as f:
            data = json.load(f)
            all_feedback.append(data)

    return all_feedback

def analyze_feedback(feedbacks: List[Dict]):
    """Aggregate and analyze all feedback."""

    print("="*90)
    print("PHASE 1 CHATGPT FEEDBACK - AGGREGATE ANALYSIS")
    print("="*90)
    print()

    # Basic stats
    total_cvs = len(feedbacks)
    avg_quality = sum(f['overall_quality'] for f in feedbacks) / total_cvs
    avg_confidence = sum(f['confidence_score'] for f in feedbacks) / total_cvs
    avg_hint_coverage = sum(f['hint_coverage'] for f in feedbacks) / total_cvs

    print(f"Total CVs analyzed: {total_cvs}")
    print(f"Average overall quality: {avg_quality:.1f}/10")
    print(f"Average confidence score: {avg_confidence:.3f}")
    print(f"Average hint coverage: {avg_hint_coverage:.1f}%")
    print()

    # Issue type aggregation
    issue_types = defaultdict(lambda: {'count': 0, 'total_severity': 0, 'examples': []})
    repair_issues = defaultdict(list)
    signal_issues = defaultdict(int)
    taxonomy_patterns = defaultdict(int)

    for feedback in feedbacks:
        cv_name = feedback['cv_name']

        # Aggregate issues
        for issue in feedback.get('issues_found', []):
            issue_type = issue['issue_type']
            severity = issue['severity']

            issue_types[issue_type]['count'] += 1
            issue_types[issue_type]['total_severity'] += severity

            if len(issue_types[issue_type]['examples']) < 3:
                issue_types[issue_type]['examples'].append({
                    'cv': cv_name,
                    'description': issue['description'][:100],
                    'severity': severity
                })

        # Aggregate repair assessment
        for ineffective in feedback.get('repair_assessment', {}).get('repairs_ineffective', []):
            repair_issues[ineffective[:50]].append(cv_name)

        # Aggregate signal issues
        for missed in feedback.get('signal_effectiveness', {}).get('signals_that_missed', []):
            signal_issues[missed] += 1

    # Print issue types
    print("="*90)
    print("ISSUE TYPES (sorted by average severity)")
    print("="*90)
    print()

    sorted_issues = sorted(issue_types.items(),
                          key=lambda x: x[1]['total_severity'] / x[1]['count'],
                          reverse=True)

    for issue_type, stats in sorted_issues:
        avg_severity = stats['total_severity'] / stats['count']
        print(f"\n{issue_type.upper()}")
        print(f"  Occurrences: {stats['count']}/{total_cvs} CVs ({stats['count']/total_cvs*100:.0f}%)")
        print(f"  Average severity: {avg_severity:.1f}/10")
        print(f"  Examples:")
        for ex in stats['examples']:
            print(f"    • [{ex['cv']}] (severity {ex['severity']}): {ex['description']}")

    print()

    # Print repair issues
    print("="*90)
    print("REPAIR INEFFECTIVENESS (repairs that failed frequently)")
    print("="*90)
    print()

    sorted_repairs = sorted(repair_issues.items(), key=lambda x: len(x[1]), reverse=True)

    for repair, cvs in sorted_repairs[:10]:
        print(f"  '{repair}...'")
        print(f"    Failed in {len(cvs)} CVs: {', '.join(cvs)}")
        print()

    # Print signal issues
    print("="*90)
    print("SIGNAL DETECTION GAPS (signals that missed frequently)")
    print("="*90)
    print()

    sorted_signals = sorted(signal_issues.items(), key=lambda x: x[1], reverse=True)

    for signal, count in sorted_signals:
        print(f"  {signal}: missed in {count}/{total_cvs} CVs ({count/total_cvs*100:.0f}%)")

    print()

    # Aggregate priority fixes
    print("="*90)
    print("PRIORITY FIXES (aggregated across all CVs)")
    print("="*90)
    print()

    fix_aggregation = defaultdict(lambda: {
        'count': 0,
        'total_effort': 0,
        'total_impact': 0,
        'total_priority_score': 0,
        'cvs': []
    })

    for feedback in feedbacks:
        cv_name = feedback['cv_name']
        for pf in feedback.get('priority_fixes', []):
            fix_key = pf['fix'][:80]  # Use first 80 chars as key
            fix_aggregation[fix_key]['count'] += 1
            fix_aggregation[fix_key]['total_effort'] += pf['effort']
            fix_aggregation[fix_key]['total_impact'] += pf['impact']
            fix_aggregation[fix_key]['total_priority_score'] += pf['priority_score']
            fix_aggregation[fix_key]['cvs'].append(cv_name)

    # Calculate aggregate scores
    fix_recommendations = []
    for fix, stats in fix_aggregation.items():
        avg_effort = stats['total_effort'] / stats['count']
        avg_impact = stats['total_impact'] / stats['count']
        frequency = stats['count'] / total_cvs

        # Aggregate priority: (avg_impact * frequency) / avg_effort
        # Higher is better
        aggregate_priority = (avg_impact * frequency * 10) / avg_effort

        fix_recommendations.append({
            'fix': fix,
            'frequency': stats['count'],
            'frequency_pct': frequency * 100,
            'avg_effort': avg_effort,
            'avg_impact': avg_impact,
            'aggregate_priority': aggregate_priority,
            'cvs_affected': ', '.join(stats['cvs'][:5]) + ('...' if len(stats['cvs']) > 5 else '')
        })

    # Sort by aggregate priority
    fix_recommendations.sort(key=lambda x: x['aggregate_priority'], reverse=True)

    print(f"{'Rank':>4} {'Fix':70} {'Freq':>5} {'Effort':>7} {'Impact':>7} {'Priority':>9}")
    print("-"*115)

    for i, rec in enumerate(fix_recommendations[:20], 1):
        print(f"{i:4} {rec['fix'][:68]:68} {rec['frequency']:5} {rec['avg_effort']:7.1f} {rec['avg_impact']:7.1f} {rec['aggregate_priority']:9.1f}")

    print()
    print("Legend:")
    print("  Freq = Number of CVs where this fix was recommended")
    print("  Effort = Average implementation effort (1-10)")
    print("  Impact = Average expected impact (1-10)")
    print("  Priority = (Impact × Frequency × 10) / Effort (higher is better)")
    print()

    # LLM trigger analysis
    print("="*90)
    print("LLM TRIGGER RECOMMENDATIONS")
    print("="*90)
    print()

    llm_yes = sum(1 for f in feedbacks if f['llm_trigger_recommendation'] == 'YES')
    llm_no = total_cvs - llm_yes

    print(f"Recommend LLM re-processing: {llm_yes}/{total_cvs} CVs ({llm_yes/total_cvs*100:.0f}%)")
    print(f"Fixable deterministically: {llm_no}/{total_cvs} CVs ({llm_no/total_cvs*100:.0f}%)")
    print()

    if llm_yes > 0:
        print("CVs recommended for LLM re-processing:")
        for f in feedbacks:
            if f['llm_trigger_recommendation'] == 'YES':
                print(f"  • {f['cv_name']:20} ({f['hint_coverage']:5.1f}% hint coverage) - {f['llm_trigger_reason']}")

    print()

    # Generate summary recommendations
    print("="*90)
    print("TOP 10 ACTIONABLE FIXES")
    print("="*90)
    print()

    for i, rec in enumerate(fix_recommendations[:10], 1):
        print(f"{i}. {rec['fix']}")
        print(f"   Frequency: {rec['frequency']}/{total_cvs} CVs ({rec['frequency_pct']:.0f}%)")
        print(f"   Effort: {rec['avg_effort']:.1f}/10, Impact: {rec['avg_impact']:.1f}/10")
        print(f"   Priority Score: {rec['aggregate_priority']:.1f}")
        print(f"   Affected CVs: {rec['cvs_affected']}")
        print()

    print("="*90)
    print("NEXT STEPS")
    print("="*90)
    print()
    print("1. Implement top 3-5 fixes from the list above")
    print("2. Re-run batch processing on the same 20 CVs")
    print("3. Compare before/after metrics:")
    print("   - Average hint coverage improvement")
    print("   - Reduction in 'Unknown' groups")
    print("   - Improvement in taxonomy precision")
    print("4. If improvements are significant (>10%), proceed to Phase 2 (broader validation)")
    print()

    return fix_recommendations

if __name__ == "__main__":
    feedbacks = load_all_feedback()
    recommendations = analyze_feedback(feedbacks)

    # Save recommendations to JSON
    output_path = "chatgpt_feedback_aggregated.json"
    with open(output_path, 'w') as f:
        json.dump({
            'total_cvs': len(feedbacks),
            'recommendations': recommendations
        }, f, indent=2)

    print(f"\n✓ Aggregated recommendations saved to: {output_path}")
    print()
