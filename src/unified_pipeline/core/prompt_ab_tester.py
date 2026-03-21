#!/usr/bin/env python3
"""
Prompt A/B Tester - Test prompt variations against logged prompts

This tool allows you to:
1. Load a logged prompt from prompt_logs
2. Create variations of the prompt (different instructions, temperature, etc.)
3. Replay both versions against the LLM
4. Compare results side-by-side

Usage:
    # Test a single prompt with variations
    python3 prompt_ab_tester.py --log-id abc123def456 --temperature 0.0,0.1,0.5

    # Test with modified system prompt
    python3 prompt_ab_tester.py --log-id abc123def456 --system-prompt-file new_prompt.txt

    # Batch test all low-confidence prompts
    python3 prompt_ab_tester.py --batch-low-confidence 0.7 --variations temperature

    # Test with different models
    python3 prompt_ab_tester.py --log-id abc123def456 --models gpt-4o-mini,gpt-4o
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from openai import OpenAI
from datetime import datetime
import hashlib

# Default prompt log directory
PROMPT_LOG_DIR = Path(os.getenv("PROMPT_LOG_DIR", "prompt_logs"))

# Use default environment context to avoid expensive SKU mapping
client = OpenAI()


class PromptABTester:
    """Test prompt variations against logged prompts."""

    def __init__(self, log_dir: Path = PROMPT_LOG_DIR):
        self.log_dir = log_dir
        self.test_results_dir = log_dir / "ab_tests"
        self.test_results_dir.mkdir(exist_ok=True)

    def load_prompt_log(self, log_id: str) -> Optional[Dict]:
        """Load a prompt log by ID."""
        # Find file with this log_id
        matching_files = list(self.log_dir.glob(f"*_{log_id}.json"))

        # Filter out RESPONSE files
        matching_files = [f for f in matching_files if not f.name.endswith('_RESPONSE.json')]

        if not matching_files:
            print(f"❌ No prompt log found with ID: {log_id}")
            return None

        try:
            with open(matching_files[0], 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading prompt log: {e}")
            return None

    def create_variation(
        self,
        original_prompt: Dict,
        variation_type: str,
        variation_params: Dict
    ) -> Dict:
        """
        Create a variation of the original prompt.

        Args:
            original_prompt: Original prompt log
            variation_type: Type of variation (temperature, system_prompt, instructions, model)
            variation_params: Parameters for the variation

        Returns:
            Modified prompt dictionary
        """
        # Deep copy to avoid modifying original
        varied = json.loads(json.dumps(original_prompt))

        if variation_type == 'temperature':
            varied['api_parameters']['temperature'] = variation_params['value']
            varied['variation_description'] = f"Temperature: {variation_params['value']}"

        elif variation_type == 'system_prompt':
            # Replace system prompt content
            for msg in varied['messages']:
                if msg['role'] == 'system':
                    msg['content'] = variation_params['new_system_prompt']
            varied['variation_description'] = "Modified system prompt"

        elif variation_type == 'instructions':
            # Add extra instructions to system prompt
            for msg in varied['messages']:
                if msg['role'] == 'system':
                    msg['content'] = msg['content'] + "\n\n" + variation_params['additional_instructions']
            varied['variation_description'] = f"Added instructions: {variation_params['additional_instructions'][:50]}..."

        elif variation_type == 'model':
            varied['api_parameters']['model'] = variation_params['model']
            varied['variation_description'] = f"Model: {variation_params['model']}"

        elif variation_type == 'max_tokens':
            varied['api_parameters']['max_tokens'] = variation_params['value']
            varied['variation_description'] = f"Max tokens: {variation_params['value']}"

        return varied

    def run_test(
        self,
        prompt: Dict,
        test_name: str = "Test"
    ) -> Dict:
        """
        Run a single test with the given prompt.

        Args:
            prompt: Prompt configuration
            test_name: Name for this test variant

        Returns:
            Test result with response and metadata
        """
        print(f"  🧪 Running: {test_name}...")

        # Extract parameters
        messages = prompt['messages']
        api_params = prompt['api_parameters']

        # Prepare API call
        call_params = {
            'model': api_params.get('model', 'gpt-4o-mini'),
            'messages': messages,
            'temperature': api_params.get('temperature', 0.1)
        }

        if api_params.get('max_tokens'):
            call_params['max_tokens'] = api_params['max_tokens']

        # Only include response_format if it's a complete schema
        if api_params.get('response_format'):
            rf = api_params['response_format']
            # Check if it has the required structure for structured outputs
            if rf.get('type') == 'json_schema' and rf.get('json_schema'):
                call_params['response_format'] = rf
            elif rf.get('type') == 'json_object':
                call_params['response_format'] = {"type": "json_object"}

        # Run API call
        start_time = time.time()
        try:
            response = client.chat.completions.create(**call_params)
            elapsed_time = time.time() - start_time

            # Parse response
            content = response.choices[0].message.content

            # Try to parse as JSON for structured outputs
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError:
                parsed_content = {"content": content}

            result = {
                'test_name': test_name,
                'success': True,
                'response': parsed_content,
                'elapsed_time': elapsed_time,
                'usage': {
                    'prompt_tokens': response.usage.prompt_tokens,
                    'completion_tokens': response.usage.completion_tokens,
                    'total_tokens': response.usage.total_tokens
                },
                'model': response.model,
                'finish_reason': response.choices[0].finish_reason,
                'variation_description': prompt.get('variation_description', 'Original')
            }

            # Extract confidence if present
            if 'confidence' in parsed_content:
                result['confidence'] = parsed_content['confidence']
                conf_emoji = "✅" if parsed_content['confidence'] >= 0.8 else "⚠️" if parsed_content['confidence'] >= 0.6 else "❌"
                print(f"    {conf_emoji} Confidence: {parsed_content['confidence']:.3f} | Tokens: {response.usage.total_tokens} | Time: {elapsed_time:.2f}s")
            else:
                print(f"    ✓ Tokens: {response.usage.total_tokens} | Time: {elapsed_time:.2f}s")

            return result

        except Exception as e:
            elapsed_time = time.time() - start_time
            print(f"    ❌ Error: {e}")

            return {
                'test_name': test_name,
                'success': False,
                'error': str(e),
                'elapsed_time': elapsed_time,
                'variation_description': prompt.get('variation_description', 'Original')
            }

    def compare_results(self, results: List[Dict]) -> Dict:
        """
        Compare results from multiple test runs.

        Returns:
            Comparison analysis
        """
        comparison = {
            'total_tests': len(results),
            'successful_tests': sum(1 for r in results if r['success']),
            'failed_tests': sum(1 for r in results if not r['success']),
            'results': []
        }

        # Sort by confidence (if available) or success
        results_sorted = sorted(
            results,
            key=lambda r: (r['success'], r.get('confidence', 0.0)),
            reverse=True
        )

        for result in results_sorted:
            comparison['results'].append({
                'test_name': result['test_name'],
                'variation': result.get('variation_description', 'Original'),
                'success': result['success'],
                'confidence': result.get('confidence'),
                'tokens': result.get('usage', {}).get('total_tokens', 0),
                'time': result.get('elapsed_time', 0),
                'model': result.get('model', 'unknown')
            })

        # Find best result
        if comparison['successful_tests'] > 0:
            best = max(
                [r for r in results if r['success']],
                key=lambda r: r.get('confidence', 0.5)
            )
            comparison['best_result'] = {
                'test_name': best['test_name'],
                'confidence': best.get('confidence'),
                'variation': best.get('variation_description')
            }

        return comparison

    def run_ab_test(
        self,
        log_id: str,
        variations: List[tuple]
    ) -> Dict:
        """
        Run A/B test with original prompt and variations.

        Args:
            log_id: ID of the original prompt log
            variations: List of (variation_type, params) tuples

        Returns:
            Complete test results with comparison
        """
        # Load original prompt
        original_prompt = self.load_prompt_log(log_id)
        if not original_prompt:
            return None

        print(f"\n{'='*80}")
        print(f"A/B TEST: {original_prompt.get('purpose', 'unknown')}")
        print(f"{'='*80}")
        print(f"Original Log ID: {log_id}")
        print(f"Variations: {len(variations)}")
        print()

        # Run original as baseline
        results = []
        print("Testing Original Prompt...")
        original_result = self.run_test(original_prompt, test_name="Original")
        results.append(original_result)
        print()

        # Run variations
        for idx, (variation_type, params) in enumerate(variations, 1):
            print(f"Testing Variation {idx}: {variation_type}...")
            varied_prompt = self.create_variation(original_prompt, variation_type, params)
            varied_result = self.run_test(varied_prompt, test_name=f"Variation {idx}")
            results.append(varied_result)
            print()

        # Compare results
        print(f"{'='*80}")
        print("COMPARISON")
        print(f"{'='*80}")
        comparison = self.compare_results(results)

        # Print summary
        for result_summary in comparison['results']:
            status = "✅" if result_summary['success'] else "❌"
            conf_str = f"Conf: {result_summary['confidence']:.3f}" if result_summary['confidence'] else "N/A"
            print(f"{status} {result_summary['test_name']:15} | {conf_str:15} | Tokens: {result_summary['tokens']:6} | {result_summary['variation']}")

        if comparison.get('best_result'):
            print()
            print(f"🏆 Best Result: {comparison['best_result']['test_name']}")
            print(f"   Confidence: {comparison['best_result']['confidence']:.3f}")
            print(f"   Variation: {comparison['best_result']['variation']}")

        # Save results
        test_id = hashlib.md5(f"{log_id}_{datetime.now().isoformat()}".encode()).hexdigest()[:12]
        output_file = self.test_results_dir / f"ab_test_{test_id}.json"

        with open(output_file, 'w') as f:
            json.dump({
                'original_log_id': log_id,
                'test_id': test_id,
                'timestamp': datetime.now().isoformat(),
                'purpose': original_prompt.get('purpose'),
                'results': results,
                'comparison': comparison
            }, f, indent=2)

        print()
        print(f"📄 Full results saved to: {output_file.name}")
        print(f"{'='*80}")

        return comparison

    def batch_test_low_confidence(
        self,
        confidence_threshold: float,
        variations: List[tuple],
        max_tests: int = 10
    ) -> List[Dict]:
        """
        Run A/B tests on all low-confidence prompts.

        Args:
            confidence_threshold: Test prompts below this confidence
            variations: Variations to test
            max_tests: Maximum number of prompts to test

        Returns:
            List of test results
        """
        # Find low-confidence prompts
        from prompt_analyzer import PromptAnalyzer

        analyzer = PromptAnalyzer(self.log_dir)
        analyzer.load_logs()
        low_conf_cases = analyzer.find_low_confidence_patterns(confidence_threshold)

        if not low_conf_cases:
            print(f"No prompts found below confidence threshold {confidence_threshold}")
            return []

        print(f"Found {len(low_conf_cases)} low-confidence prompts")
        print(f"Testing up to {max_tests} prompts...\n")

        all_results = []
        for i, case in enumerate(low_conf_cases[:max_tests], 1):
            print(f"\n[{i}/{min(len(low_conf_cases), max_tests)}] Testing log_id: {case['log_id']} (original conf: {case['confidence']:.3f})")
            result = self.run_ab_test(case['log_id'], variations)
            if result:
                all_results.append(result)

            # Rate limiting
            time.sleep(1)

        return all_results


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="A/B test prompt variations against logged prompts"
    )
    parser.add_argument(
        '--log-dir',
        type=Path,
        default=PROMPT_LOG_DIR,
        help=f'Prompt log directory (default: {PROMPT_LOG_DIR})'
    )
    parser.add_argument(
        '--log-id',
        type=str,
        help='Log ID to test'
    )
    parser.add_argument(
        '--temperature',
        type=str,
        help='Test with different temperatures (comma-separated, e.g., "0.0,0.1,0.5")'
    )
    parser.add_argument(
        '--models',
        type=str,
        help='Test with different models (comma-separated, e.g., "gpt-4o-mini,gpt-4o")'
    )
    parser.add_argument(
        '--system-prompt-file',
        type=Path,
        help='Test with system prompt from file'
    )
    parser.add_argument(
        '--additional-instructions',
        type=str,
        help='Add extra instructions to system prompt'
    )
    parser.add_argument(
        '--batch-low-confidence',
        type=float,
        help='Batch test all prompts below this confidence threshold'
    )
    parser.add_argument(
        '--max-tests',
        type=int,
        default=10,
        help='Maximum number of tests in batch mode (default: 10)'
    )

    args = parser.parse_args()

    tester = PromptABTester(args.log_dir)

    # Build variations list
    variations = []

    if args.temperature:
        temps = [float(t) for t in args.temperature.split(',')]
        for temp in temps:
            variations.append(('temperature', {'value': temp}))

    if args.models:
        models = args.models.split(',')
        for model in models:
            variations.append(('model', {'model': model.strip()}))

    if args.system_prompt_file:
        with open(args.system_prompt_file, 'r') as f:
            new_system_prompt = f.read()
        variations.append(('system_prompt', {'new_system_prompt': new_system_prompt}))

    if args.additional_instructions:
        variations.append(('instructions', {'additional_instructions': args.additional_instructions}))

    if not variations:
        print("⚠️  No variations specified. Use --temperature, --models, --system-prompt-file, or --additional-instructions")
        return

    # Run tests
    if args.batch_low_confidence:
        tester.batch_test_low_confidence(args.batch_low_confidence, variations, args.max_tests)
    elif args.log_id:
        tester.run_ab_test(args.log_id, variations)
    else:
        print("❌ Must specify either --log-id or --batch-low-confidence")


if __name__ == '__main__':
    main()
