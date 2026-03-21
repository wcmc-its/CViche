#!/usr/bin/env python3
"""
Analyze actual processing data from 20 CVs to validate and calibrate time estimation formulas.
"""

import json
import glob
from pathlib import Path
import re
from datetime import datetime

def parse_timestamp(ts_str):
    """Parse timestamp from log format: 2025-11-08_10-12-45"""
    return datetime.strptime(ts_str, "%Y-%m-%d_%H-%M-%S")

def extract_timing_from_log():
    """Extract processing times from the log file."""
    log_path = "phase1_20cvs.log"

    cv_timings = {}
    current_cv = None
    start_time = None

    with open(log_path, 'r') as f:
        for line in f:
            # Match: Processing: Name (ID)
            if match := re.search(r'Processing: ([^\(]+)\s*\((\d+)\)', line):
                current_cv = {
                    'name': match.group(1).strip(),
                    'id': match.group(2)
                }

            # Match: [4/4] Mapping with v3 signals...
            if '[4/4] Mapping with v3 signals' in line and current_cv:
                # Next line will have the input filename with timestamp
                continue

            # Match: Input: validation_CV_ID_Name_preprocessed.json
            # This is the start of mapping
            if match := re.search(r'Input: validation_CV_(\d+)_([^_]+)', line):
                if current_cv and current_cv['id'] == match.group(1):
                    # Start time will be in the first API call
                    pass

            # Match: 📝 Prompt logged: 2025-11-08_10-12-45_taxonomy_mapping_pass1_hash.json
            if '📝 Prompt logged:' in line and current_cv:
                if match := re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_taxonomy', line):
                    ts = parse_timestamp(match.group(1))
                    if 'first_api_call' not in current_cv:
                        current_cv['first_api_call'] = ts
                    current_cv['last_api_call'] = ts

            # Match: ✓ Results saved to: validation_CV_ID_Name_with_signals_v3.json
            # This marks completion
            if '✓ Results saved to:' in line and 'with_signals_v3.json' in line:
                if current_cv and 'first_api_call' in current_cv:
                    # Calculate elapsed time
                    elapsed_seconds = (current_cv['last_api_call'] - current_cv['first_api_call']).total_seconds()
                    current_cv['elapsed_seconds'] = elapsed_seconds
                    current_cv['elapsed_minutes'] = elapsed_seconds / 60

                    cv_key = f"{current_cv['id']}_{current_cv['name']}"
                    cv_timings[cv_key] = current_cv
                    current_cv = None

    return cv_timings

def load_metrics():
    """Load all metrics files."""
    metrics_files = glob.glob("validation_CV_*_METRICS.json")

    all_metrics = []

    for filepath in metrics_files:
        with open(filepath, 'r') as f:
            data = json.load(f)

            # Extract CV name and ID from filename
            filename = Path(filepath).stem  # Remove .json
            # validation_CV_2006_Bush_METRICS
            parts = filename.replace('validation_CV_', '').replace('_METRICS', '').split('_', 1)
            cv_id = parts[0]
            cv_name = parts[1] if len(parts) > 1 else 'Unknown'

            metrics = {
                'cv_id': cv_id,
                'cv_name': cv_name,
                'groups_before': data['repair']['groups_before'],
                'groups_after': data['repair']['groups_after'],
                'total_entries': data['final_metrics']['total_entries'],
                'api_calls': data['final_metrics']['total_api_calls'],
                'tokens': data['final_metrics']['total_tokens'],
                'avg_confidence': data['final_metrics']['avg_confidence']
            }

            all_metrics.append(metrics)

    return all_metrics

def analyze_and_calibrate():
    """Main analysis function."""

    print("="*80)
    print("CV PROCESSING TIME ESTIMATION - CALIBRATION ANALYSIS")
    print("="*80)
    print()

    # Load metrics
    metrics = load_metrics()

    # Extract timing from log
    timings = extract_timing_from_log()

    # Merge timing with metrics
    for m in metrics:
        key = f"{m['cv_id']}_{m['cv_name']}"
        if key in timings:
            m['elapsed_minutes'] = timings[key]['elapsed_minutes']
            m['elapsed_seconds'] = timings[key]['elapsed_seconds']
        else:
            # Try alternative key format
            alt_keys = [k for k in timings.keys() if m['cv_id'] in k]
            if alt_keys:
                m['elapsed_minutes'] = timings[alt_keys[0]]['elapsed_minutes']
                m['elapsed_seconds'] = timings[alt_keys[0]]['elapsed_seconds']

    # Filter to only CVs with timing data
    timed_cvs = [m for m in metrics if 'elapsed_minutes' in m]

    print(f"Total CVs processed: {len(metrics)}")
    print(f"CVs with timing data: {len(timed_cvs)}")
    print()

    # Sort by elapsed time
    timed_cvs.sort(key=lambda x: x['elapsed_minutes'])

    print("="*80)
    print("DETAILED METRICS BY CV")
    print("="*80)
    print()
    print(f"{'CV Name':25} {'Groups':>7} {'Entries':>8} {'API Calls':>10} {'Time (min)':>11} {'Calls/Group':>12}")
    print("-"*80)

    for cv in timed_cvs:
        calls_per_group = cv['api_calls'] / cv['groups_after']
        print(f"{cv['cv_name']:25} {cv['groups_after']:7} {cv['total_entries']:8} {cv['api_calls']:10} {cv['elapsed_minutes']:11.2f} {calls_per_group:12.2f}")

    print()
    print("="*80)
    print("CALIBRATION ANALYSIS")
    print("="*80)
    print()

    # Calculate averages
    total_api_calls = sum(cv['api_calls'] for cv in timed_cvs)
    total_groups = sum(cv['groups_after'] for cv in timed_cvs)
    total_seconds = sum(cv['elapsed_seconds'] for cv in timed_cvs)

    avg_calls_per_group = total_api_calls / total_groups
    avg_seconds_per_call = total_seconds / total_api_calls
    avg_minutes_per_group = (avg_calls_per_group * avg_seconds_per_call) / 60

    print(f"Average API calls per group: {avg_calls_per_group:.2f}")
    print(f"Average seconds per API call: {avg_seconds_per_call:.2f}")
    print(f"Average minutes per group: {avg_minutes_per_group:.2f}")
    print()

    # Original formula vs actual
    print("="*80)
    print("FORMULA VALIDATION")
    print("="*80)
    print()
    print(f"{'CV Name':25} {'Groups':>7} {'Actual (min)':>13} {'Predicted':>11} {'Error':>8} {'Error %':>9}")
    print("-"*80)

    # Original formula: estimated_minutes = (num_groups * 0.14) + 1
    ORIGINAL_MINS_PER_GROUP = 0.14
    ORIGINAL_OVERHEAD = 1

    total_error = 0
    total_abs_error = 0

    for cv in timed_cvs:
        predicted = (cv['groups_after'] * ORIGINAL_MINS_PER_GROUP) + ORIGINAL_OVERHEAD
        error = predicted - cv['elapsed_minutes']
        error_pct = (error / cv['elapsed_minutes']) * 100

        total_error += error
        total_abs_error += abs(error)

        print(f"{cv['cv_name']:25} {cv['groups_after']:7} {cv['elapsed_minutes']:13.2f} {predicted:11.2f} {error:8.2f} {error_pct:8.1f}%")

    avg_error = total_error / len(timed_cvs)
    avg_abs_error = total_abs_error / len(timed_cvs)

    print()
    print(f"Average error: {avg_error:.2f} minutes")
    print(f"Average absolute error: {avg_abs_error:.2f} minutes")
    print()

    # Calculate optimized constants
    print("="*80)
    print("OPTIMIZED CALIBRATION CONSTANTS")
    print("="*80)
    print()

    # Method 1: Direct calculation from aggregate data
    print("Method 1: Aggregate calculation")
    print(f"  API_CALLS_PER_GROUP = {avg_calls_per_group:.2f}")
    print(f"  AVG_API_LATENCY = {avg_seconds_per_call:.2f} seconds")
    print(f"  MINS_PER_GROUP = {avg_minutes_per_group:.3f}")
    print()

    # Method 2: Linear regression (simple y = mx approach, assuming minimal overhead)
    # Sum of (actual_time * groups) / Sum of (groups^2)
    sum_time_groups = sum(cv['elapsed_minutes'] * cv['groups_after'] for cv in timed_cvs)
    sum_groups_squared = sum(cv['groups_after'] ** 2 for cv in timed_cvs)

    optimal_mins_per_group = sum_time_groups / sum_groups_squared

    print("Method 2: Linear regression (zero overhead)")
    print(f"  MINS_PER_GROUP = {optimal_mins_per_group:.3f}")
    print()

    # Test with optimized constant
    print("="*80)
    print("OPTIMIZED FORMULA VALIDATION")
    print("="*80)
    print()
    print(f"{'CV Name':25} {'Groups':>7} {'Actual (min)':>13} {'Predicted':>11} {'Error':>8} {'Error %':>9}")
    print("-"*80)

    total_error_opt = 0
    total_abs_error_opt = 0

    for cv in timed_cvs:
        predicted_opt = cv['groups_after'] * optimal_mins_per_group
        error_opt = predicted_opt - cv['elapsed_minutes']
        error_pct_opt = (error_opt / cv['elapsed_minutes']) * 100

        total_error_opt += error_opt
        total_abs_error_opt += abs(error_opt)

        print(f"{cv['cv_name']:25} {cv['groups_after']:7} {cv['elapsed_minutes']:13.2f} {predicted_opt:11.2f} {error_opt:8.2f} {error_pct_opt:8.1f}%")

    avg_error_opt = total_error_opt / len(timed_cvs)
    avg_abs_error_opt = total_abs_error_opt / len(timed_cvs)

    print()
    print(f"Average error: {avg_error_opt:.2f} minutes")
    print(f"Average absolute error: {avg_abs_error_opt:.2f} minutes")
    print()

    # Improvement
    improvement = ((avg_abs_error - avg_abs_error_opt) / avg_abs_error) * 100
    print(f"Improvement over original formula: {improvement:.1f}%")
    print()

    print("="*80)
    print("RECOMMENDED CALIBRATION CONSTANTS")
    print("="*80)
    print()
    print("Update CV_PROCESSING_TIME_ESTIMATION.md with:")
    print()
    print(f"  AVG_API_LATENCY = {avg_seconds_per_call:.2f}  # seconds (was 2.5)")
    print(f"  API_CALLS_PER_GROUP = {avg_calls_per_group:.2f}  # (was 3.35)")
    print()
    print("Simplified formula (zero overhead):")
    print(f"  estimated_minutes = groups * {optimal_mins_per_group:.3f}")
    print()

if __name__ == "__main__":
    analyze_and_calibrate()
