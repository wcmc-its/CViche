"""
Test Batch Classification with Progressive Sizes

Tests how many entries can be classified together before quality degrades.
Measures: success rate, confidence, consistency, token usage, latency.

Usage:
    python test_batch_classification.py CV_2022_Afifi_Rima_PhD_preprocessed_preserve.json
"""

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Any
import os
from openai import OpenAI

# Import classification functions
try:
    from taxonomy_mapper_v2 import classify_pass1_parent
    from taxonomy_contexts import get_section_context, PARENT_SECTIONS
except ImportError:
    print("Error: Could not import from taxonomy_mapper_v2")
    sys.exit(1)

# Use default environment context to avoid expensive SKU mapping
client = OpenAI()


# Batch classification schema
BATCH_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "description": "Classification for each entry in the batch",
            "items": {
                "type": "object",
                "properties": {
                    "entry_index": {
                        "type": "integer",
                        "description": "Index of entry in batch (0-based)"
                    },
                    "child_section_id": {
                        "type": "string",
                        "description": "WCM child section ID"
                    },
                    "child_canonical_name": {
                        "type": "string",
                        "description": "Full name of child section"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score 0.0-1.0"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation of classification"
                    }
                },
                "required": ["entry_index", "child_section_id", "child_canonical_name", "confidence", "reasoning"],
                "additionalProperties": False
            }
        }
    },
    "required": ["entries"],
    "additionalProperties": False
}


def classify_batch_pass2(
    parent_section_id: str,
    section_header: str,
    subsection_header: str,
    entries: List[str],
    model: str = "gpt-4o-mini"
) -> Dict[str, Any]:
    """
    Classify a batch of entries together (for consistency).

    Args:
        parent_section_id: Parent section from Pass 1
        section_header: Top-level section header (e.g., "PROFESSIONAL PRACTICE")
        subsection_header: Immediate parent header (e.g., "Advisory Board Member")
        entries: List of entry texts to classify
        model: Model to use

    Returns:
        {
            'success': bool,
            'classifications': List[Dict],
            'avg_confidence': float,
            'token_usage': Dict,
            'elapsed_time': float,
            'error': Optional[str]
        }
    """
    # Get section context
    section_context = get_section_context(parent_section_id)

    # Build prompt
    system_prompt = f"""You are a CV taxonomy expert. Classify entries from an academic CV into the WCM taxonomy.

PARENT SECTION: {section_context.get('section', 'Unknown')}

HIERARCHICAL CONTEXT:
  Section: {section_header}
  Subsection: {subsection_header}

These entries are from the same CV section and should be classified consistently if they describe similar activities.

Available child sections for this parent:
"""

    # Add child sections
    if 'related_sections' in section_context:
        for child_id, child_info in section_context['related_sections'].items():
            system_prompt += f"\n{child_id}: {child_info.get('title', 'Unknown')}"
            if 'description' in child_info:
                system_prompt += f"\n  {child_info['description']}"

    system_prompt += "\n\nReturn structured JSON with classification for each entry."

    # Build user prompt
    user_prompt = "Classify these entries:\n\n"
    for i, entry in enumerate(entries):
        preview = entry[:300] + "..." if len(entry) > 300 else entry
        user_prompt += f"[{i}] {preview}\n\n"

    user_prompt += "Classify each entry to the most specific child section. Maintain consistency for similar entries."

    # Call API
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "batch_classification",
            "strict": True,
            "schema": BATCH_CLASSIFICATION_SCHEMA
        }
    }

    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format=response_format,
            temperature=0.1,
            max_tokens=2000
        )
        elapsed_time = time.time() - start_time

        # Parse result
        result = json.loads(response.choices[0].message.content)
        classifications = result['entries']

        # Calculate average confidence
        avg_confidence = sum(c['confidence'] for c in classifications) / len(classifications)

        return {
            'success': True,
            'classifications': classifications,
            'avg_confidence': avg_confidence,
            'token_usage': {
                'prompt_tokens': response.usage.prompt_tokens,
                'completion_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens
            },
            'elapsed_time': elapsed_time,
            'error': None
        }

    except Exception as e:
        return {
            'success': False,
            'classifications': [],
            'avg_confidence': 0.0,
            'token_usage': {},
            'elapsed_time': 0.0,
            'error': str(e)
        }


def calculate_consistency_score(classifications: List[Dict]) -> float:
    """
    Calculate how consistent the classifications are.

    Returns:
        0.0-1.0 where 1.0 = all entries classified to same section
    """
    if len(classifications) <= 1:
        return 1.0

    # Count most common classification
    section_counts = {}
    for c in classifications:
        section_id = c['child_section_id']
        section_counts[section_id] = section_counts.get(section_id, 0) + 1

    max_count = max(section_counts.values())
    return max_count / len(classifications)


def test_progressive_batches(
    entries: List[str],
    parent_section_id: str,
    section_header: str,
    subsection_header: str,
    batch_sizes: List[int] = [5, 10, 15, 20, 25, 30],
    model: str = "gpt-4o-mini"
) -> List[Dict[str, Any]]:
    """
    Test classification with progressively larger batches.

    Args:
        entries: List of entry texts
        parent_section_id: Parent section
        section_header: Section header
        subsection_header: Subsection header
        batch_sizes: List of batch sizes to test
        model: Model to use

    Returns:
        List of test results
    """
    results = []

    print(f"Testing {len(batch_sizes)} batch sizes on {len(entries)} entries...")
    print(f"Model: {model}")
    print(f"Parent: {parent_section_id}")
    print(f"Context: {section_header} > {subsection_header}")
    print()

    for batch_size in batch_sizes:
        if batch_size > len(entries):
            print(f"⚠️  Skipping batch size {batch_size} (only {len(entries)} entries available)")
            continue

        print(f"Testing batch size: {batch_size}")

        # Take first N entries
        batch = entries[:batch_size]

        # Estimate character count
        total_chars = sum(len(e) for e in batch)

        # Classify
        result = classify_batch_pass2(
            parent_section_id=parent_section_id,
            section_header=section_header,
            subsection_header=subsection_header,
            entries=batch,
            model=model
        )

        # Calculate consistency
        consistency = 0.0
        if result['success'] and result['classifications']:
            consistency = calculate_consistency_score(result['classifications'])

        # Get confidence stats
        confidences = [c['confidence'] for c in result['classifications']] if result['success'] else []
        min_conf = min(confidences) if confidences else 0.0
        max_conf = max(confidences) if confidences else 0.0

        test_result = {
            'batch_size': batch_size,
            'total_chars': total_chars,
            'success': result['success'],
            'avg_confidence': result['avg_confidence'],
            'min_confidence': min_conf,
            'max_confidence': max_conf,
            'consistency_score': consistency,
            'token_usage': result['token_usage'],
            'elapsed_time': result['elapsed_time'],
            'error': result['error'],
            'classifications': result['classifications']
        }

        results.append(test_result)

        # Print result
        if result['success']:
            print(f"  ✓ Success")
            print(f"    Avg confidence: {result['avg_confidence']:.3f} (min: {min_conf:.3f}, max: {max_conf:.3f})")
            print(f"    Consistency: {consistency:.3f}")
            print(f"    Tokens: {result['token_usage']['total_tokens']:,}")
            print(f"    Time: {result['elapsed_time']:.2f}s")
            print(f"    Chars: {total_chars:,}")
        else:
            print(f"  ✗ Failed: {result['error']}")

        print()

        # Wait between calls
        time.sleep(1)

    return results


def analyze_results(results: List[Dict]) -> Dict[str, Any]:
    """Analyze test results and recommend threshold."""

    successful = [r for r in results if r['success']]

    if not successful:
        return {
            'recommended_batch_size': 5,
            'reason': 'All tests failed',
            'max_successful_size': 0
        }

    # Find largest successful batch
    max_successful = max(r['batch_size'] for r in successful)

    # Find where quality starts to degrade
    quality_threshold = None
    for r in successful:
        if r['avg_confidence'] < 0.85 or r['consistency_score'] < 0.7:
            quality_threshold = r['batch_size']
            break

    # Recommend 70-80% of max, or before quality degrades
    if quality_threshold:
        recommended = int(quality_threshold * 0.7)
        reason = f"Quality degrades at {quality_threshold} entries"
    else:
        recommended = int(max_successful * 0.75)
        reason = f"Safety margin below max ({max_successful})"

    return {
        'recommended_batch_size': max(5, recommended),  # Minimum 5
        'reason': reason,
        'max_successful_size': max_successful,
        'successful_tests': len(successful),
        'total_tests': len(results)
    }


def main():
    """Run batch classification tests."""

    if len(sys.argv) < 2:
        print("Usage: python test_batch_classification.py <segmented_cv.json>")
        print()
        print("Tests progressive batch sizes for classification.")
        sys.exit(1)

    cv_path = Path(sys.argv[1])

    # Load CV
    with open(cv_path, 'r') as f:
        cv = json.load(f)

    # Find educational contributions section (G14 has 19 entries based on earlier analysis)
    # This tests a section with many similar entries that should classify together
    target_groups = ['G14']  # Educational Contributions - teaching courses over years

    # Collect all entries from these groups
    test_entries = []
    groups_by_id = {}

    def index_groups(groups):
        for group in groups:
            groups_by_id[group.get('id')] = group
            if group.get('subgroups'):
                index_groups(group.get('subgroups', []))

    index_groups(cv.get('groups', []))

    for group_id in target_groups:
        group = groups_by_id.get(group_id)
        if group:
            entries = group.get('entries', [])
            for entry in entries:
                text = entry.get('text_snippet', entry.get('text', ''))
                if text:
                    test_entries.append(text)

    print("="*80)
    print("BATCH CLASSIFICATION TEST")
    print("="*80)
    print(f"CV: {cv_path.name}")
    print(f"Test entries collected: {len(test_entries)}")
    print()

    if len(test_entries) < 5:
        print("Error: Not enough test entries found")
        sys.exit(1)

    # Run tests
    results = test_progressive_batches(
        entries=test_entries,
        parent_section_id="educational_contributions",
        section_header="TEACHING",
        subsection_header="At the American University of Beirut, Faculty of Health Sciences",
        batch_sizes=[5, 10, 15, 20, 25, 30, 40],
        model="gpt-4o-mini"
    )

    # Analyze
    print("="*80)
    print("ANALYSIS")
    print("="*80)

    analysis = analyze_results(results)

    print(f"Max successful batch size: {analysis['max_successful_size']}")
    print(f"Recommended batch size: {analysis['recommended_batch_size']}")
    print(f"Reason: {analysis['reason']}")
    print()

    # Show quality trends
    print("QUALITY TRENDS:")
    print(f"{'Batch':>6} {'Success':>8} {'Avg Conf':>9} {'Min Conf':>9} {'Consistency':>12} {'Tokens':>8}")
    print("-"*80)

    for r in results:
        success = "✓" if r['success'] else "✗"
        avg_conf = f"{r['avg_confidence']:.3f}" if r['success'] else "—"
        min_conf = f"{r['min_confidence']:.3f}" if r['success'] else "—"
        consistency = f"{r['consistency_score']:.3f}" if r['success'] else "—"
        tokens = f"{r['token_usage'].get('total_tokens', 0):,}" if r['success'] else "—"

        print(f"{r['batch_size']:>6} {success:>8} {avg_conf:>9} {min_conf:>9} {consistency:>12} {tokens:>8}")

    print()

    # Save detailed results
    output_path = cv_path.parent / "batch_classification_test_results.json"
    with open(output_path, 'w') as f:
        json.dump({
            'test_metadata': {
                'cv_file': str(cv_path),
                'test_date': time.strftime('%Y-%m-%d %H:%M:%S'),
                'model': 'gpt-4o-mini',
                'entries_tested': len(test_entries)
            },
            'results': results,
            'analysis': analysis
        }, f, indent=2)

    print(f"✓ Detailed results saved to: {output_path}")
    print()

    # Confidence calibration analysis
    print("="*80)
    print("CONFIDENCE CALIBRATION ANALYSIS")
    print("="*80)

    all_confidences = []
    for r in results:
        if r['success']:
            all_confidences.extend([c['confidence'] for c in r['classifications']])

    if all_confidences:
        avg = sum(all_confidences) / len(all_confidences)
        min_val = min(all_confidences)
        max_val = max(all_confidences)

        # Count by range
        high = sum(1 for c in all_confidences if c >= 0.90)
        medium = sum(1 for c in all_confidences if 0.80 <= c < 0.90)
        low = sum(1 for c in all_confidences if c < 0.80)

        print(f"Total classifications: {len(all_confidences)}")
        print(f"Average confidence: {avg:.3f}")
        print(f"Min confidence: {min_val:.3f}")
        print(f"Max confidence: {max_val:.3f}")
        print()
        print("Distribution:")
        print(f"  High (≥0.90): {high} ({high/len(all_confidences)*100:.1f}%)")
        print(f"  Medium (0.80-0.89): {medium} ({medium/len(all_confidences)*100:.1f}%)")
        print(f"  Low (<0.80): {low} ({low/len(all_confidences)*100:.1f}%)")
        print()

        if avg < 0.85:
            print("⚠️  Average confidence below 0.85 suggests:")
            print("    1. Ambiguous content (expected for journal reviewers)")
            print("    2. Insufficient context in prompts")
            print("    3. Need for Pass 2 with more examples")

        if high < len(all_confidences) * 0.7:
            print("⚠️  <70% high confidence classifications")
            print("    Consider raising confidence threshold to 0.90 for 'reliable' classifications")


if __name__ == '__main__':
    main()
