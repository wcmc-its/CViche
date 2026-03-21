#!/usr/bin/env python3
"""
Quick analysis of actual vs estimated processing times from the log.
"""

import re
from datetime import datetime

# Parse log to extract actual timings
log_path = "phase1_20cvs.log"

cvs = []
current_cv = None

with open(log_path, 'r') as f:
    for line in f:
        # Match: Processing: Name (ID)
        if match := re.search(r'Processing: ([^\(]+)\s*\((\d+)\)', line):
            if current_cv and 'start_time' in current_cv and 'end_time' in current_cv:
                cvs.append(current_cv)

            current_cv = {
                'name': match.group(1).strip(),
                'id': match.group(2)
            }

        # Match: Repaired: X → Y top-level groups
        if current_cv and (match := re.search(r'Repaired: \d+ → (\d+) top-level groups', line)):
            current_cv['groups'] = int(match.group(1))

        # Match first timestamp: 📝 Prompt logged: 2025-11-08_10-12-45...
        if current_cv and '📝 Prompt logged:' in line:
            if match := re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_taxonomy', line):
                ts = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
                if 'start_time' not in current_cv:
                    current_cv['start_time'] = ts

        # Match: Total: X  (API calls summary)
        if current_cv:
            match = re.search(r'Total:\s+(\d+)', line)
            if match and 'api_calls' not in current_cv:
                current_cv['api_calls'] = int(match.group(1))

        # Match: ✓ Results saved to: validation_CV_ID_Name_with_signals_v3.json
        if current_cv and '✓ Results saved to:' in line and 'with_signals_v3.json' in line:
            # End time should be close to now
            if 'start_time' in current_cv:
                # Look back for last timestamp
                pass

        # Match: 📝 Response logged: 2025-11-08_XX-XX-XX...
        if current_cv and '📝 Response logged:' in line:
            if match := re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_taxonomy', line):
                ts = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S")
                current_cv['end_time'] = ts

# Add last CV
if current_cv and 'start_time' in current_cv and 'end_time' in current_cv:
    cvs.append(current_cv)

# Calculate elapsed time for each
for cv in cvs:
    elapsed = (cv['end_time'] - cv['start_time']).total_seconds()
    cv['elapsed_minutes'] = elapsed / 60
    cv['elapsed_seconds'] = elapsed

# Filter to CVs with all required data
complete_cvs = [cv for cv in cvs if all(k in cv for k in ['name', 'groups', 'api_calls', 'elapsed_minutes'])]

print("="*90)
print("CV PROCESSING TIME ANALYSIS - ACTUAL DATA FROM BATCH RUN")
print("="*90)
print()
print(f"Total CVs with complete data: {len(complete_cvs)}")
print()

# Sort by elapsed time
complete_cvs.sort(key=lambda x: x['elapsed_minutes'])

print(f"{'CV Name':28} {'Groups':>7} {'API Calls':>10} {'Time (min)':>11} {'Calls/Group':>12} {'Min/Group':>11}")
print("-"*90)

for cv in complete_cvs:
    calls_per_group = cv['api_calls'] / cv['groups']
    mins_per_group = cv['elapsed_minutes'] / cv['groups']
    print(f"{cv['name']:28} {cv['groups']:7} {cv['api_calls']:10} {cv['elapsed_minutes']:11.2f} {calls_per_group:12.2f} {mins_per_group:11.3f}")

print()
print("="*90)
print("AGGREGATE STATISTICS")
print("="*90)
print()

total_groups = sum(cv['groups'] for cv in complete_cvs)
total_api_calls = sum(cv['api_calls'] for cv in complete_cvs)
total_minutes = sum(cv['elapsed_minutes'] for cv in complete_cvs)

avg_calls_per_group = total_api_calls / total_groups
avg_seconds_per_call = (total_minutes * 60) / total_api_calls
avg_mins_per_group = total_minutes / total_groups

print(f"Total CVs: {len(complete_cvs)}")
print(f"Total groups: {total_groups}")
print(f"Total API calls: {total_api_calls}")
print(f"Total time: {total_minutes:.1f} minutes ({total_minutes/60:.1f} hours)")
print()
print(f"Average API calls per group: {avg_calls_per_group:.2f}")
print(f"Average seconds per API call: {avg_seconds_per_call:.2f}")
print(f"Average minutes per group: {avg_mins_per_group:.3f}")
print()

# Compare to original estimate
print("="*90)
print("FORMULA VALIDATION")
print("="*90)
print()

# Original: (groups * 0.14) + 1
ORIGINAL_MINS_PER_GROUP = 0.14
ORIGINAL_OVERHEAD = 1

print(f"{'CV Name':28} {'Groups':>7} {'Actual':>10} {'Est (old)':>11} {'Error':>8} {'Error %':>9}")
print("-"*90)

errors = []
for cv in complete_cvs:
    estimated = (cv['groups'] * ORIGINAL_MINS_PER_GROUP) + ORIGINAL_OVERHEAD
    error = estimated - cv['elapsed_minutes']
    error_pct = (error / cv['elapsed_minutes']) * 100
    errors.append(abs(error))

    print(f"{cv['name']:28} {cv['groups']:7} {cv['elapsed_minutes']:10.2f} {estimated:11.2f} {error:8.2f} {error_pct:8.1f}%")

print()
print(f"Mean absolute error: {sum(errors)/len(errors):.2f} minutes")
print()

# Optimized formula
print("="*90)
print("OPTIMIZED CALIBRATION CONSTANTS")
print("="*90)
print()

print("For CV_PROCESSING_TIME_ESTIMATION.md:")
print()
print(f"  AVG_API_LATENCY = {avg_seconds_per_call:.2f}  # seconds (was 2.5)")
print(f"  API_CALLS_PER_GROUP = {avg_calls_per_group:.2f}  # (was 3.35)")
print()
print("Simple formula (recommended):")
print(f"  estimated_minutes = groups * {avg_mins_per_group:.3f}")
print()

# Test optimized
print("="*90)
print("OPTIMIZED FORMULA ACCURACY")
print("="*90)
print()

print(f"{'CV Name':28} {'Groups':>7} {'Actual':>10} {'Est (new)':>11} {'Error':>8} {'Error %':>9}")
print("-"*90)

errors_opt = []
for cv in complete_cvs:
    estimated_opt = cv['groups'] * avg_mins_per_group
    error_opt = estimated_opt - cv['elapsed_minutes']
    error_pct_opt = (error_opt / cv['elapsed_minutes']) * 100
    errors_opt.append(abs(error_opt))

    print(f"{cv['name']:28} {cv['groups']:7} {cv['elapsed_minutes']:10.2f} {estimated_opt:11.2f} {error_opt:8.2f} {error_pct_opt:8.1f}%")

print()
print(f"Mean absolute error: {sum(errors_opt)/len(errors_opt):.2f} minutes")
print()

# Improvement
improvement = ((sum(errors)/len(errors) - sum(errors_opt)/len(errors_opt)) / (sum(errors)/len(errors))) * 100
print(f"Improvement: {improvement:.1f}% reduction in error")
print()
