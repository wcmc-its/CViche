# Phase 1 Workflow: Process 20 CVs with Structured Analysis

## Overview

Process 20 CVs one at a time → Collect structured metrics → I aggregate → Send summary to ChatGPT

## Step 1: Run Batch Processing

```bash
cd "/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project/src/unified_pipeline/core"

# Process 20 CVs (will take ~1 hour for all)
python3 -u batch_process_with_repair.py 2>&1 | tee phase1_20cvs.log
```

**What this does:**
- Processes each CV through: Segment → Repair → Preprocess → Taxonomy Map
- Saves structured metrics for each CV: `validation_CV_{id}_{name}_METRICS.json`
- Saves final outputs: `*_with_signals_v3.json` and `*_AUDIT.json`

**Output files per CV:**
- `validation_CV_{id}_{name}_METRICS.json` - **Structured data for aggregation**
- `validation_CV_{id}_{name}_segmented_repaired.json` - Repaired segmentation
- `validation_CV_{id}_{name}_preprocessed.json` - Preprocessed CV
- `validation_CV_{id}_{name}_with_signals_v3.json` - Final mapped output
- `validation_CV_{id}_{name}_with_signals_v3_AUDIT.json` - Signal audit report

## Step 2: Send Each CV to ChatGPT for Analysis

For each of the 20 CVs, send the following 4 files to ChatGPT:
- `validation_CV_{id}_{name}_segmented_repaired.json` - Repaired structure
- `validation_CV_{id}_{name}_preprocessed.json` - Preprocessed version
- `validation_CV_{id}_{name}_with_signals_v3.json` - Final mapped output
- `validation_CV_{id}_{name}_with_signals_v3_AUDIT.json` - Signal audit

**ChatGPT Prompt (use for each CV):**

```
I'm building a CV parsing pipeline with a 3-tier repair system (deterministic fixes → LLM-assisted repair → human review). Target: 95% accuracy across 80 CVs.

This CV went through: Segmentation → Repair → Preprocessing → Taxonomy Mapping with v3 signals.

**Files attached:**
- Repaired segmentation (after deterministic fixes)
- Preprocessed CV (filtered valid groups)
- Final mapped output with taxonomy
- Audit report (signal detection, confidence scores)

**Your task:** Analyze the pipeline's performance on this CV and identify issues.

**Provide analysis in this exact JSON format:**

{
  "cv_id": "2025",
  "cv_name": "Denckla",
  "overall_quality": 7,  // 1-10 integer (1=terrible, 10=perfect)
  "confidence_score": 0.937,
  "hint_coverage": 29.7,

  "issues_found": [
    {
      "issue_type": "segmentation|repair|taxonomy|signal_detection",
      "severity": 8,  // 1-10 integer (1=cosmetic, 10=critical failure)
      "description": "Detailed description of the issue",
      "affected_sections": ["Section name 1", "Section name 2"],
      "fix_type": "DETERMINISTIC|LLM|MANUAL",
      "estimated_impact": "How this affects parsing quality"
    }
  ],

  "priority_fixes": [
    {
      "rank": 1,
      "fix": "Detailed fix description",
      "effort": 3,  // 1-10 integer (1=trivial, 10=major refactor)
      "impact": 9,  // 1-10 integer (1=affects 1 entry, 10=affects all CVs)
      "priority_score": 72,  // Auto-calculate: impact × (11 - effort)
      "implementation_notes": "How to implement this fix"
    }
  ],

  "what_worked_well": [
    "Specific aspect that performed well"
  ],

  "llm_tier2_trigger": true/false  // Would this CV benefit from LLM repair?
}

**Scoring Guide:**
- overall_quality: 1-10 (1=terrible, 10=perfect)
- severity: 1-10 (1=cosmetic issue, 10=critical failure)
- effort: 1-10 (1=trivial 5-min fix, 10=major refactor)
- impact: 1-10 (1=affects 1 entry in 1 CV, 10=affects all CVs)
- priority_score: impact × (11 - effort) [higher = more important]

Focus on:
1. Structural issues (segmentation/repair quality)
2. Taxonomy classification accuracy
3. Signal detection effectiveness
4. Issues that likely affect multiple CVs
```

**Save each ChatGPT response as:**
`validation_CV_{id}_{name}_CHATGPT_ANALYSIS.json`

## Step 3: Aggregate ChatGPT Analyses

After getting all 20 ChatGPT responses, run:

```bash
python3 aggregate_chatgpt_analyses.py
```

**What this does:**
- Reads all 20 `*_CHATGPT_ANALYSIS.json` files
- Identifies recurring issues (appearing in 3+ CVs)
- Ranks fixes by total_priority (sum of priority_scores across all CVs)
- Calculates LLM trigger rate (% needing Tier 2)
- Groups similar issues by type and description
- Generates implementation plan sorted by impact

**Output files:**
- `phase1_chatgpt_aggregated.json` - Full aggregated data
- `phase1_implementation_plan.md` - **Prioritized action plan**

## Step 4: Implement Fixes (based on aggregated ChatGPT analyses)

Based on ChatGPT's analysis, I will:
1. **Add deterministic fixes** for issues in 3+ CVs
2. **Implement LLM trigger heuristics** for Tier 2
3. **Document edge cases** for Tier 3
4. **Update repair pipeline** with new fixes

## Step 5: Process Next Batch

After implementing fixes:
- Test on 3-5 CVs to validate improvements
- If successful, continue to CVs 21-40
- Repeat analysis at 40 CVs milestone

## File Structure After Phase 1

```
core/
├── phase1_20cvs.log                                  # Full processing log
├── batch_processing_with_repair_summary.json         # Basic batch summary
│
├── phase1_chatgpt_aggregated.json                    # Aggregated ChatGPT analyses
├── phase1_implementation_plan.md                     # Prioritized action plan
│
├── validation_CV_2025_Denckla_METRICS.json           # Per-CV structured metrics
├── validation_CV_2025_Denckla_segmented_repaired.json
├── validation_CV_2025_Denckla_preprocessed.json
├── validation_CV_2025_Denckla_with_signals_v3.json
├── validation_CV_2025_Denckla_with_signals_v3_AUDIT.json
├── validation_CV_2025_Denckla_CHATGPT_ANALYSIS.json  # ChatGPT's per-CV analysis
│
└── [... repeated for all 20 CVs]
```

## METRICS.json Format

Each `validation_CV_{id}_{name}_METRICS.json` contains:

```json
{
  "cv_id": "2025",
  "cv_name": "Denckla",
  "status": "SUCCESS",
  "repair": {
    "groups_before": 26,
    "groups_after": 22,
    "repairs_applied": ["all_caps_promotion", "contact_coalescing"]
  },
  "final_metrics": {
    "total_entries": 64,
    "hint_coverage_pct": 29.7,
    "avg_confidence": 0.85,
    "total_api_calls": 94,
    "total_tokens": 135134
  },
  "taxonomy_issues": [
    {
      "source_label": "Unknown section",
      "canonical_name": "Professional Positions",
      "confidence": 0.75
    }
  ],
  "signals_fired": {
    "award_honor": 9,
    "employment_pattern": 9,
    "grant_amount": 3
  },
  "structural_issues": [
    "low_hint_coverage",
    "low_confidence"
  ]
}
```

## Success Criteria

Phase 1 complete when:
- ✅ 20 CVs processed through pipeline
- ✅ All 20 CVs analyzed by ChatGPT individually
- ✅ ChatGPT analyses aggregated
- ✅ Implementation plan generated with prioritized fixes
- ✅ Ready to implement fixes for Phase 2

## Timeline

- **Step 1 - Processing**: ~1-2 hours (20 CVs × 3-6 min each)
- **Step 2 - ChatGPT Analysis**: ~1.5-2 hours (20 CVs × 5 min each, manual)
- **Step 3 - Aggregation**: <1 minute
- **Step 4 - Implementation**: Variable (depends on fixes identified)
