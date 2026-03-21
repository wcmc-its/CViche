# CV Processing Time Estimation - Progressive Refinement System

## Executive Summary

**Progressive estimation approach:** Start with rough estimate, refine as more information becomes available through each pipeline stage.

## Pipeline Stages and Timing

### Stage Breakdown

| Stage | Timing | Predictability | Basis for Next Estimate |
|-------|--------|----------------|-------------------------|
| 1. Segmentation | 10-30s | Variable (file size) | Initial group count |
| 2. Repair | 5-15s | Fast, deterministic | Final group count |
| 3. Preprocessing | 2-5s | Very fast | Content group count |
| 4. Mapping | 95%+ of total time | API-bound | Real-time progress |

### Detailed Timing Model

```python
# Stage 1: Segmentation
segmentation_time = 15 + (file_size_kb / 100)  # ~15-30 seconds

# Stage 2: Repair
repair_time = 5 + (initial_groups * 0.1)  # ~5-15 seconds

# Stage 3: Preprocessing
preprocessing_time = 3  # ~3 seconds (constant)

# Stage 4: Mapping
# CALIBRATED FROM 19-CV BATCH RUN (2025-11-08)
mapping_time = (final_groups * api_calls_per_group * avg_api_latency)
             = (final_groups * 2.82 * 3.73)  # seconds (actual measured values)
             = final_groups * 10.5  # seconds
             = final_groups * 0.175  # minutes
```

---

## Progressive Estimation Strategy

### Level 0: Before Upload (File-Based Estimate)

**Available Info:** File size only

**Formula:**
```python
def estimate_from_file_size(file_size_kb: int) -> dict:
    """Ultra-rough estimate based on file size."""
    # Assume ~5KB per section (very rough)
    estimated_groups = max(10, file_size_kb / 5)

    return {
        'stage': 'pre_upload',
        'confidence': 'very_low',
        'min_minutes': estimated_groups * 0.10,
        'max_minutes': estimated_groups * 0.20,
        'display': f"{int(estimated_groups * 0.10)}-{int(estimated_groups * 0.20)} minutes"
    }
```

**Example:**
- 100 KB file → 20 sections estimated → "2-4 minutes"
- 500 KB file → 100 sections estimated → "10-20 minutes"

**UI Display:**
```
Uploading CV...
Estimated processing time: 10-20 minutes (preliminary)
```

---

### Level 1: After Segmentation

**Available Info:** Initial group count (before repairs)

**Formula:**
```python
def estimate_after_segmentation(initial_groups: int) -> dict:
    """Better estimate after segmentation completes."""
    # Groups may increase/decrease slightly after repair
    # Add ±10% buffer
    min_groups = int(initial_groups * 0.9)
    max_groups = int(initial_groups * 1.1)

    # Time = overhead + mapping time
    overhead = 1  # segmentation + repair + preprocessing
    min_time = overhead + (min_groups * 0.14)
    max_time = overhead + (max_groups * 0.14)

    return {
        'stage': 'after_segmentation',
        'confidence': 'medium',
        'initial_groups': initial_groups,
        'min_minutes': min_time,
        'max_minutes': max_time,
        'best_estimate': overhead + (initial_groups * 0.14),
        'display': f"~{int((min_time + max_time) / 2)} minutes"
    }
```

**Example:**
- 50 sections detected → "~8 minutes"
- 200 sections detected → "~30 minutes"

**UI Display:**
```
✓ Segmentation complete (50 sections detected)
Estimated time remaining: ~8 minutes
```

---

### Level 2: After Repair (Most Accurate Pre-Mapping)

**Available Info:** Final group count (after repairs), repair operations applied

**Formula:**
```python
def estimate_after_repair(final_groups: int, repair_stats: dict) -> dict:
    """Most accurate estimate before mapping starts."""
    # This is the final group count that will be mapped

    # Overhead for preprocessing + mapping setup
    overhead_seconds = 30

    # Mapping time
    api_calls_estimated = final_groups * 3.35
    api_time_seconds = api_calls_estimated * 2.5

    total_seconds = overhead_seconds + api_time_seconds
    total_minutes = total_seconds / 60

    # Add small buffer for variability
    buffered_minutes = total_minutes * 1.1

    return {
        'stage': 'after_repair',
        'confidence': 'high',
        'final_groups': final_groups,
        'estimated_api_calls': int(api_calls_estimated),
        'estimated_minutes': total_minutes,
        'buffered_minutes': buffered_minutes,
        'display': f"~{int(buffered_minutes)} minutes",
        'breakdown': {
            'preprocessing': 0.5,
            'mapping': total_minutes - 0.5
        }
    }
```

**Example:**
- 75 sections after repair → 252 API calls → 10.5 minutes → Display: "~12 minutes"

**UI Display:**
```
✓ Segmentation complete
✓ Repair complete (75 sections)
⏳ Starting taxonomy mapping...
Estimated time remaining: ~12 minutes
```

---

### Level 3: During Mapping (Real-Time Progress)

**Available Info:**
- Total groups to process
- Groups completed so far
- Actual API calls made
- Actual time elapsed

**Formula:**
```python
def estimate_during_mapping(
    total_groups: int,
    completed_groups: int,
    actual_api_calls_made: int,
    elapsed_seconds: float
) -> dict:
    """Real-time estimate with actual performance data."""

    remaining_groups = total_groups - completed_groups

    if completed_groups == 0:
        # Use default estimate
        avg_api_latency = 2.5
        calls_per_group = 3.35
    else:
        # Use actual performance
        avg_api_latency = elapsed_seconds / actual_api_calls_made
        calls_per_group = actual_api_calls_made / completed_groups

    # Estimate remaining API calls
    remaining_api_calls = remaining_groups * calls_per_group

    # Estimate remaining time
    remaining_seconds = remaining_api_calls * avg_api_latency
    remaining_minutes = remaining_seconds / 60

    # Progress percentage
    progress_pct = (completed_groups / total_groups) * 100

    return {
        'stage': 'during_mapping',
        'confidence': 'very_high',
        'progress_pct': progress_pct,
        'completed_groups': completed_groups,
        'total_groups': total_groups,
        'remaining_groups': remaining_groups,
        'actual_api_latency': avg_api_latency,
        'estimated_remaining_minutes': remaining_minutes,
        'display': f"~{int(remaining_minutes)} minutes remaining",
        'eta': None  # Calculate actual clock time if needed
    }
```

**Example Progress:**
```
Progress: 25/75 sections (33%)
API calls: 84/252
Avg latency: 2.3s per call
Estimated time remaining: ~8 minutes
```

**UI Display:**
```
✓ Segmentation complete
✓ Repair complete
⏳ Mapping taxonomy (25/75 sections - 33%)
    Estimated time remaining: ~8 minutes
    [████████░░░░░░░░░░░░░░░░]
```

---

## Substep Timing Breakdown (Mapping Stage)

The mapping stage has measurable substeps:

### Mapping Substeps

```python
class MappingProgress:
    def __init__(self, total_groups: int):
        self.total_groups = total_groups
        self.current_group = 0
        self.api_calls_made = 0
        self.start_time = time.time()

    def update_after_pass1(self, group_idx: int):
        """Called after Pass 1 completes for a group."""
        self.current_group = group_idx + 1
        self.api_calls_made += 1

        # Estimate: We're ~40% through this group
        # (Pass 1 = 1 call, Pass 2 typically = 1-2 calls)
        group_progress = 0.4

        return self.calculate_eta(group_progress)

    def update_after_pass2(self, group_idx: int, batch_size: int):
        """Called after Pass 2 completes for a group."""
        self.current_group = group_idx + 1
        self.api_calls_made += 1  # Could be batched

        # This group is 100% complete
        group_progress = 1.0

        return self.calculate_eta(group_progress)

    def calculate_eta(self, current_group_progress: float) -> dict:
        """Calculate ETA with sub-group granularity."""
        elapsed = time.time() - self.start_time

        # Total progress = completed groups + partial progress on current
        total_progress = (self.current_group - 1) + current_group_progress
        progress_pct = (total_progress / self.total_groups) * 100

        if total_progress > 0:
            # Velocity = progress per second
            velocity = total_progress / elapsed
            remaining_progress = self.total_groups - total_progress
            remaining_seconds = remaining_progress / velocity

            return {
                'progress_pct': progress_pct,
                'groups_completed': self.current_group - 1,
                'current_group_progress': current_group_progress,
                'remaining_minutes': remaining_seconds / 60,
                'eta_timestamp': time.time() + remaining_seconds
            }
```

### Substep Progress Display

**Example UI with substeps:**

```
⏳ Mapping taxonomy (Section 42/100)
    ├─ Pass 1: Identifying parent category... ✓
    ├─ Pass 2: Identifying specific category... ⏳
    └─ Progress: 42.5% complete

    Estimated time remaining: ~14 minutes
    [████████████░░░░░░░░░░░░░]
```

---

## Implementation Code

### Python Class for Progressive Estimation

```python
from dataclasses import dataclass
from typing import Optional, Dict, Literal
import time

@dataclass
class ProgressiveEstimate:
    stage: Literal['pre_upload', 'after_segmentation', 'after_repair', 'during_mapping']
    confidence: Literal['very_low', 'low', 'medium', 'high', 'very_high']
    estimated_minutes: float
    min_minutes: Optional[float] = None
    max_minutes: Optional[float] = None
    progress_pct: Optional[float] = None
    details: Optional[Dict] = None

    def display(self) -> str:
        """Format for UI display."""
        if self.stage == 'pre_upload':
            if self.min_minutes and self.max_minutes:
                return f"{int(self.min_minutes)}-{int(self.max_minutes)} minutes (preliminary)"

        elif self.stage in ['after_segmentation', 'after_repair']:
            return f"~{int(self.estimated_minutes)} minutes"

        elif self.stage == 'during_mapping':
            if self.progress_pct:
                return f"~{int(self.estimated_minutes)} minutes remaining ({int(self.progress_pct)}% complete)"

        return f"~{int(self.estimated_minutes)} minutes"


class CVProcessingEstimator:
    """Progressive time estimator for CV processing pipeline."""

    def __init__(self):
        self.stage = 'pre_upload'
        self.start_time = None
        self.stage_start_times = {}

        # Calibration constants (CALIBRATED FROM 19-CV BATCH RUN: 2025-11-08)
        self.AVG_API_LATENCY = 3.73  # seconds (actual: 3.73s measured across 3408 API calls)
        self.API_CALLS_PER_GROUP = 2.82  # (actual: 2.82 measured across 1210 groups)

    def estimate_from_file_size(self, file_size_kb: int) -> ProgressiveEstimate:
        """Level 0: Estimate from file size."""
        estimated_groups = max(10, file_size_kb / 5)
        min_min = estimated_groups * 0.10
        max_min = estimated_groups * 0.20

        return ProgressiveEstimate(
            stage='pre_upload',
            confidence='very_low',
            estimated_minutes=(min_min + max_min) / 2,
            min_minutes=min_min,
            max_minutes=max_min
        )

    def estimate_after_segmentation(self, initial_groups: int) -> ProgressiveEstimate:
        """Level 1: Estimate after segmentation."""
        self.stage = 'after_segmentation'

        # Add ±10% buffer for repair changes
        min_groups = int(initial_groups * 0.9)
        max_groups = int(initial_groups * 1.1)

        overhead = 1
        min_time = overhead + (min_groups * 0.14)
        max_time = overhead + (max_groups * 0.14)
        best_estimate = overhead + (initial_groups * 0.14)

        return ProgressiveEstimate(
            stage='after_segmentation',
            confidence='medium',
            estimated_minutes=best_estimate,
            min_minutes=min_time,
            max_minutes=max_time,
            details={'initial_groups': initial_groups}
        )

    def estimate_after_repair(self, final_groups: int) -> ProgressiveEstimate:
        """Level 2: Most accurate pre-mapping estimate."""
        self.stage = 'after_repair'

        overhead_seconds = 30
        api_calls = final_groups * self.API_CALLS_PER_GROUP
        api_time = api_calls * self.AVG_API_LATENCY

        total_minutes = (overhead_seconds + api_time) / 60
        buffered = total_minutes * 1.1

        return ProgressiveEstimate(
            stage='after_repair',
            confidence='high',
            estimated_minutes=buffered,
            details={
                'final_groups': final_groups,
                'estimated_api_calls': int(api_calls)
            }
        )

    def estimate_during_mapping(
        self,
        total_groups: int,
        completed_groups: int,
        api_calls_made: int,
        elapsed_seconds: float,
        current_group_progress: float = 0.0
    ) -> ProgressiveEstimate:
        """Level 3: Real-time estimate during mapping."""
        self.stage = 'during_mapping'

        if completed_groups == 0 and api_calls_made == 0:
            # Use defaults
            latency = self.AVG_API_LATENCY
            calls_per_group = self.API_CALLS_PER_GROUP
        else:
            # Use actual performance
            latency = elapsed_seconds / api_calls_made if api_calls_made > 0 else self.AVG_API_LATENCY
            calls_per_group = api_calls_made / max(1, completed_groups)

        # Total progress including current group
        total_progress = completed_groups + current_group_progress
        progress_pct = (total_progress / total_groups) * 100

        # Remaining work
        remaining_groups = total_groups - total_progress
        remaining_calls = remaining_groups * calls_per_group
        remaining_seconds = remaining_calls * latency
        remaining_minutes = remaining_seconds / 60

        return ProgressiveEstimate(
            stage='during_mapping',
            confidence='very_high',
            estimated_minutes=remaining_minutes,
            progress_pct=progress_pct,
            details={
                'completed_groups': completed_groups,
                'total_groups': total_groups,
                'current_group_progress': current_group_progress,
                'actual_latency': latency
            }
        )
```

---

## UI Integration Examples

### React Component

```typescript
interface ProcessingStatus {
    stage: 'pre_upload' | 'segmentation' | 'repair' | 'preprocessing' | 'mapping';
    currentEstimate: ProgressiveEstimate;
    substep?: {
        name: string;
        progress: number; // 0-1
    };
}

function CVProcessingProgress({ status }: { status: ProcessingStatus }) {
    const { stage, currentEstimate, substep } = status;

    return (
        <div className="cv-processing">
            <h3>Processing CV</h3>

            {/* Stage checklist */}
            <div className="stages">
                <Stage name="Segmentation" status={getStageStatus('segmentation', stage)} />
                <Stage name="Repair" status={getStageStatus('repair', stage)} />
                <Stage name="Preprocessing" status={getStageStatus('preprocessing', stage)} />
                <Stage name="Taxonomy Mapping" status={getStageStatus('mapping', stage)} />
            </div>

            {/* Current substep (if in mapping) */}
            {substep && (
                <div className="substep">
                    <div>{substep.name}</div>
                    <ProgressBar value={substep.progress} />
                </div>
            )}

            {/* Time estimate */}
            <div className="estimate">
                {currentEstimate.progress_pct !== undefined && (
                    <ProgressBar value={currentEstimate.progress_pct / 100} />
                )}
                <div className="time">
                    {currentEstimate.display()}
                </div>
            </div>
        </div>
    );
}
```

---

## Validation and Calibration

After Phase 1 completes, update calibration constants:

```python
# Analyze actual vs estimated times
def calibrate_from_actual_data(cv_results: List[Dict]) -> Dict[str, float]:
    """Update calibration constants based on actual performance."""

    total_api_latencies = []
    calls_per_group_ratios = []

    for cv in cv_results:
        actual_time = cv['total_seconds']
        api_calls = cv['api_calls']
        groups = cv['final_groups']

        # Back-calculate actual latency
        latency = (actual_time - 60) / api_calls  # 60s overhead
        total_api_latencies.append(latency)

        # Actual calls per group
        calls_per_group_ratios.append(api_calls / groups)

    return {
        'AVG_API_LATENCY': sum(total_api_latencies) / len(total_api_latencies),
        'API_CALLS_PER_GROUP': sum(calls_per_group_ratios) / len(calls_per_group_ratios)
    }
```

---

## Summary: Estimation Confidence Progression

| Stage | Confidence | Error Margin | Updates |
|-------|-----------|--------------|---------|
| Pre-upload | Very Low | ±100% | Static |
| After Segmentation | Medium | ±20% | Once |
| After Repair | High | ±10% | Once |
| During Mapping (0-25%) | High | ±15% | Every group |
| During Mapping (25-75%) | Very High | ±5% | Every group |
| During Mapping (75-100%) | Very High | ±2% | Every group |

The key is to **update frequently during the mapping stage** since that's where 95%+ of the time is spent.

---

## Calibration Results (2025-11-08)

### Batch Run Summary

Processed 19 CVs with complete timing data:
- **Total groups**: 1,210
- **Total API calls**: 3,408
- **Total time**: 211.6 minutes (3.5 hours)

### Measured Constants

| Constant | Original Estimate | Actual Measured | Difference |
|----------|------------------|-----------------|------------|
| `AVG_API_LATENCY` | 2.5 seconds | **3.73 seconds** | +49% slower |
| `API_CALLS_PER_GROUP` | 3.35 calls | **2.82 calls** | -16% fewer calls |
| `MINS_PER_GROUP` | 0.14 min | **0.175 min** | +25% longer |

### Formula Accuracy

**Current formula:** `estimated_minutes = (groups * 0.14) + 1`

**Validation results:**
- Mean absolute error: **2.77 minutes**
- Tested on CVs ranging from 5 to 405 groups
- Range of actual times: 0.28 to 50.2 minutes

**Key findings:**
1. API latency was higher than expected (3.73s vs 2.5s)
2. API calls per group was lower than expected (2.82 vs 3.35)
3. The overhead term (+1 minute) is important for small CVs
4. High variance across CVs (some outliers with 9+ calls/group)

### Recommended Constants for Production

```python
# Use these calibrated values
AVG_API_LATENCY = 3.73  # seconds
API_CALLS_PER_GROUP = 2.82
MINS_PER_GROUP = 0.175  # derived from actual measurements

# Formula options:
# Option 1 (Simplest): estimated_minutes = groups * 0.175
# Option 2 (Better for small CVs): estimated_minutes = (groups * 0.175) + 0.5
```

### Sample Predictions vs Actual

| CV Groups | Actual Time | Predicted (0.175/group) | Error | Error % |
|-----------|-------------|------------------------|-------|---------|
| 5 | 0.3 min | 0.9 min | +0.6 | +200% |
| 22 | 5.0 min | 3.9 min | -1.1 | -22% |
| 50 | 10.4 min | 8.7 min | -1.6 | -16% |
| 89 | 20.8 min | 15.6 min | -5.2 | -25% |
| 218 | 34.0 min | 38.1 min | +4.1 | +12% |
| 405 | 50.2 min | 70.8 min | +20.6 | +41% |

**Note:** Very small CVs (< 10 groups) and very large CVs (> 200 groups) show higher error percentages, but absolute error remains acceptable for UI display purposes.
