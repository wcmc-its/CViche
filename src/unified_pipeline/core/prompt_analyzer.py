#!/usr/bin/env python3
"""
Prompt Analyzer - Find patterns in LLM prompt successes and failures

This tool analyzes logged prompts to identify:
- Common characteristics of low-confidence results
- Patterns in successful vs. failed extractions
- Token usage trends
- Prompt effectiveness by purpose
- Specific failure modes (empty fields, parse errors, etc.)

Usage:
    python3 prompt_analyzer.py
    python3 prompt_analyzer.py --purpose education_parsing
    python3 prompt_analyzer.py --min-confidence 0.7
    python3 prompt_analyzer.py --show-failures
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, Counter
from datetime import datetime
import statistics

# Default prompt log directory
PROMPT_LOG_DIR = Path(os.getenv("PROMPT_LOG_DIR", "prompt_logs"))


class PromptAnalyzer:
    """Analyzes logged prompts to find patterns and insights."""

    def __init__(self, log_dir: Path = PROMPT_LOG_DIR):
        self.log_dir = log_dir
        self.prompts = []
        self.responses = []
        self.prompt_response_pairs = []

    def load_logs(self, purpose_filter: str | None = None) -> int:
        """
        Load all prompt logs from directory.

        Args:
            purpose_filter: Only load logs matching this purpose

        Returns:
            Number of logs loaded
        """
        if not self.log_dir.exists():
            print(f"⚠️  Log directory not found: {self.log_dir}")
            return 0

        # Load all JSON files (skip READABLE.txt and RESPONSE.json for now)
        json_files = list(self.log_dir.glob("*.json"))

        # Filter out RESPONSE files
        prompt_files = [f for f in json_files if not f.name.endswith('_RESPONSE.json')]

        for prompt_file in prompt_files:
            try:
                with open(prompt_file, 'r') as f:
                    prompt_data = json.load(f)

                # Filter by purpose if specified
                if purpose_filter and prompt_data.get('purpose') != purpose_filter:
                    continue

                self.prompts.append(prompt_data)

                # Try to load corresponding response
                log_id = prompt_data.get('log_id')
                response_pattern = f"*_{log_id}_RESPONSE.json"
                response_files = list(self.log_dir.glob(response_pattern))

                if response_files:
                    with open(response_files[0], 'r') as f:
                        response_data = json.load(f)

                    self.responses.append(response_data)
                    self.prompt_response_pairs.append({
                        'prompt': prompt_data,
                        'response': response_data
                    })

            except Exception as e:
                print(f"⚠️  Error loading {prompt_file.name}: {e}")
                continue

        return len(self.prompts)

    def analyze_by_purpose(self) -> dict:
        """Analyze prompts grouped by purpose."""
        by_purpose = defaultdict(list)

        for prompt in self.prompts:
            purpose = prompt.get('purpose', 'unknown')
            by_purpose[purpose].append(prompt)

        stats = {}
        for purpose, prompts in by_purpose.items():
            stats[purpose] = {
                'count': len(prompts),
                'avg_char_count': statistics.mean([p.get('character_count', 0) for p in prompts]) if prompts else 0,
                'avg_message_count': statistics.mean([p.get('message_count', 0) for p in prompts]) if prompts else 0,
                'models': Counter([p.get('api_parameters', {}).get('model') for p in prompts])
            }

        return stats

    def analyze_token_usage(self) -> dict:
        """Analyze token usage patterns from responses."""
        if not self.responses:
            return {}

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        by_purpose = defaultdict(lambda: {'prompt': 0, 'completion': 0, 'total': 0, 'count': 0})

        for response in self.responses:
            usage = response.get('response', {}).get('usage', {})
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)

            total_prompt_tokens += prompt_tokens
            total_completion_tokens += completion_tokens
            total_tokens += usage.get('total_tokens', 0)

            purpose = response.get('purpose', 'unknown')
            by_purpose[purpose]['prompt'] += prompt_tokens
            by_purpose[purpose]['completion'] += completion_tokens
            by_purpose[purpose]['total'] += usage.get('total_tokens', 0)
            by_purpose[purpose]['count'] += 1

        return {
            'total': {
                'prompt_tokens': total_prompt_tokens,
                'completion_tokens': total_completion_tokens,
                'total_tokens': total_tokens,
                'avg_per_call': total_tokens / len(self.responses) if self.responses else 0
            },
            'by_purpose': dict(by_purpose)
        }

    def find_low_confidence_patterns(self, threshold: float = 0.7) -> list[dict]:
        """
        Find patterns in low-confidence results.

        Args:
            threshold: Confidence threshold (results below this are considered low)

        Returns:
            List of low-confidence cases with analysis
        """
        low_confidence_cases = []

        for pair in self.prompt_response_pairs:
            try:
                response_content = pair['response'].get('response', {}).get('choices', [{}])[0].get('message', {}).get('content', '{}')
                parsed_response = json.loads(response_content)

                # Extract confidence if present
                confidence = parsed_response.get('confidence', 1.0)

                if confidence < threshold:
                    low_confidence_cases.append({
                        'log_id': pair['prompt'].get('log_id'),
                        'purpose': pair['prompt'].get('purpose'),
                        'confidence': confidence,
                        'timestamp': pair['prompt'].get('timestamp'),
                        'prompt_length': pair['prompt'].get('character_count', 0),
                        'response': parsed_response,
                        'context': pair['prompt'].get('context', {})
                    })

            except (json.JSONDecodeError, KeyError, IndexError) as e:
                # Skip malformed responses
                continue

        return sorted(low_confidence_cases, key=lambda x: x['confidence'])

    def find_parse_errors(self) -> list[dict]:
        """Find prompts that resulted in parse errors or malformed responses."""
        errors = []

        for pair in self.prompt_response_pairs:
            try:
                response_content = pair['response'].get('response', {}).get('choices', [{}])[0].get('message', {}).get('content', '{}')
                parsed_response = json.loads(response_content)

                # Check for common error indicators
                has_error = False
                error_type = None

                # Check for missing required fields (varies by purpose)
                purpose = pair['prompt'].get('purpose')

                if purpose == 'education_parsing':
                    required = ['degree', 'institution']
                    missing = [f for f in required if not parsed_response.get(f)]
                    if missing:
                        has_error = True
                        error_type = f"missing_fields: {', '.join(missing)}"

                elif purpose == 'publication_parsing':
                    required = ['title', 'authors']
                    missing = [f for f in required if not parsed_response.get(f)]
                    if missing:
                        has_error = True
                        error_type = f"missing_fields: {', '.join(missing)}"

                # Check for very low confidence (often indicates parsing issues)
                if parsed_response.get('confidence', 1.0) < 0.3:
                    has_error = True
                    error_type = error_type or "very_low_confidence"

                if has_error:
                    errors.append({
                        'log_id': pair['prompt'].get('log_id'),
                        'purpose': pair['prompt'].get('purpose'),
                        'error_type': error_type,
                        'timestamp': pair['prompt'].get('timestamp'),
                        'response': parsed_response
                    })

            except json.JSONDecodeError:
                # Malformed JSON response
                errors.append({
                    'log_id': pair['prompt'].get('log_id'),
                    'purpose': pair['prompt'].get('purpose'),
                    'error_type': 'json_decode_error',
                    'timestamp': pair['prompt'].get('timestamp'),
                    'response': None
                })
            except Exception as e:
                continue

        return errors

    def analyze_prompt_length_vs_quality(self) -> dict:
        """Analyze correlation between prompt length and result quality."""
        length_buckets = {
            'short': {'range': (0, 1000), 'confidences': [], 'count': 0},
            'medium': {'range': (1000, 3000), 'confidences': [], 'count': 0},
            'long': {'range': (3000, 10000), 'confidences': [], 'count': 0},
            'very_long': {'range': (10000, float('inf')), 'confidences': [], 'count': 0}
        }

        for pair in self.prompt_response_pairs:
            try:
                prompt_length = pair['prompt'].get('character_count', 0)
                response_content = pair['response'].get('response', {}).get('choices', [{}])[0].get('message', {}).get('content', '{}')
                parsed_response = json.loads(response_content)
                confidence = parsed_response.get('confidence', 1.0)

                # Find appropriate bucket
                for bucket_name, bucket_data in length_buckets.items():
                    min_len, max_len = bucket_data['range']
                    if min_len <= prompt_length < max_len:
                        bucket_data['confidences'].append(confidence)
                        bucket_data['count'] += 1
                        break

            except Exception:
                continue

        # Calculate statistics for each bucket
        stats = {}
        for bucket_name, bucket_data in length_buckets.items():
            if bucket_data['confidences']:
                stats[bucket_name] = {
                    'count': bucket_data['count'],
                    'avg_confidence': statistics.mean(bucket_data['confidences']),
                    'min_confidence': min(bucket_data['confidences']),
                    'max_confidence': max(bucket_data['confidences']),
                    'std_dev': statistics.stdev(bucket_data['confidences']) if len(bucket_data['confidences']) > 1 else 0
                }
            else:
                stats[bucket_name] = {'count': 0}

        return stats

    def generate_report(self, min_confidence: float | None = None, show_failures: bool = False) -> str:
        """Generate comprehensive analysis report."""
        report = []
        report.append("=" * 80)
        report.append("PROMPT ANALYSIS REPORT")
        report.append("=" * 80)
        report.append(f"Log Directory: {self.log_dir}")
        report.append(f"Total Prompts: {len(self.prompts)}")
        report.append(f"Prompt-Response Pairs: {len(self.prompt_response_pairs)}")
        report.append("")

        # Analysis by purpose
        report.append("-" * 80)
        report.append("ANALYSIS BY PURPOSE")
        report.append("-" * 80)
        purpose_stats = self.analyze_by_purpose()
        for purpose, stats in sorted(purpose_stats.items()):
            report.append(f"\n{purpose.upper()}:")
            report.append(f"  Count: {stats['count']}")
            report.append(f"  Avg Character Count: {stats['avg_char_count']:.0f}")
            report.append(f"  Avg Messages: {stats['avg_message_count']:.1f}")
            report.append(f"  Models: {dict(stats['models'])}")

        # Token usage
        report.append("\n" + "-" * 80)
        report.append("TOKEN USAGE ANALYSIS")
        report.append("-" * 80)
        token_stats = self.analyze_token_usage()
        if token_stats:
            total = token_stats['total']
            report.append(f"Total Prompt Tokens: {total['prompt_tokens']:,}")
            report.append(f"Total Completion Tokens: {total['completion_tokens']:,}")
            report.append(f"Total Tokens: {total['total_tokens']:,}")
            report.append(f"Avg Tokens per Call: {total['avg_per_call']:.0f}")

            report.append("\nBy Purpose:")
            for purpose, stats in sorted(token_stats['by_purpose'].items()):
                if stats['count'] > 0:
                    avg_total = stats['total'] / stats['count']
                    report.append(f"  {purpose}: {avg_total:.0f} avg tokens ({stats['count']} calls)")

        # Prompt length vs quality
        report.append("\n" + "-" * 80)
        report.append("PROMPT LENGTH vs QUALITY")
        report.append("-" * 80)
        length_stats = self.analyze_prompt_length_vs_quality()
        for bucket_name, stats in sorted(length_stats.items()):
            if stats['count'] > 0:
                report.append(f"\n{bucket_name.upper()} ({stats['count']} samples):")
                report.append(f"  Avg Confidence: {stats['avg_confidence']:.3f}")
                report.append(f"  Range: {stats['min_confidence']:.3f} - {stats['max_confidence']:.3f}")
                report.append(f"  Std Dev: {stats['std_dev']:.3f}")

        # Low confidence patterns
        if min_confidence:
            report.append("\n" + "-" * 80)
            report.append(f"LOW CONFIDENCE CASES (< {min_confidence})")
            report.append("-" * 80)
            low_conf = self.find_low_confidence_patterns(min_confidence)
            report.append(f"Found {len(low_conf)} low-confidence cases\n")

            for case in low_conf[:10]:  # Show top 10
                report.append(f"Log ID: {case['log_id']}")
                report.append(f"  Purpose: {case['purpose']}")
                report.append(f"  Confidence: {case['confidence']:.3f}")
                report.append(f"  Prompt Length: {case['prompt_length']} chars")
                if case.get('context'):
                    report.append(f"  Context: {case['context']}")
                report.append("")

        # Parse errors
        if show_failures:
            report.append("-" * 80)
            report.append("PARSE ERRORS AND FAILURES")
            report.append("-" * 80)
            errors = self.find_parse_errors()
            report.append(f"Found {len(errors)} errors\n")

            # Group by error type
            error_by_type = defaultdict(list)
            for error in errors:
                error_by_type[error['error_type']].append(error)

            for error_type, error_list in sorted(error_by_type.items()):
                report.append(f"\n{error_type.upper()}: {len(error_list)} cases")
                for error in error_list[:3]:  # Show first 3 of each type
                    report.append(f"  - {error['log_id']} ({error['purpose']})")

        report.append("\n" + "=" * 80)
        return "\n".join(report)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze logged LLM prompts to find patterns and failures"
    )
    parser.add_argument(
        '--log-dir',
        type=Path,
        default=PROMPT_LOG_DIR,
        help=f'Prompt log directory (default: {PROMPT_LOG_DIR})'
    )
    parser.add_argument(
        '--purpose',
        type=str,
        help='Filter by purpose (e.g., education_parsing, taxonomy_mapping)'
    )
    parser.add_argument(
        '--min-confidence',
        type=float,
        help='Show low-confidence cases below this threshold (e.g., 0.7)'
    )
    parser.add_argument(
        '--show-failures',
        action='store_true',
        help='Show parse errors and failures'
    )
    parser.add_argument(
        '--output',
        type=Path,
        help='Save report to file instead of printing'
    )

    args = parser.parse_args()

    print(f"🔍 Loading prompts from {args.log_dir}...")
    analyzer = PromptAnalyzer(args.log_dir)

    count = analyzer.load_logs(purpose_filter=args.purpose)
    print(f"✓ Loaded {count} prompt logs")
    print()

    if count == 0:
        print("No logs found. Run the pipeline first to generate prompt logs.")
        return

    # Generate report
    report = analyzer.generate_report(
        min_confidence=args.min_confidence,
        show_failures=args.show_failures
    )

    if args.output:
        with open(args.output, 'w') as f:
            f.write(report)
        print(f"📄 Report saved to {args.output}")
    else:
        print(report)


if __name__ == '__main__':
    main()
