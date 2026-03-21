# Hybrid Repair Plan: 95% Accuracy Target

## Executive Summary

**Goal**: Process 80 CVs with 95%+ accuracy using hybrid approach:
- **Deterministic repairs** for clear patterns (fast, cheap, predictable)
- **LLM-assisted repairs** for ambiguous cases (handles variability)
- **Phased rollout** with metrics-driven decisions

**Target**: 76/80 CVs with avg confidence ≥0.90 (95% success rate)

---

## Architecture: Three-Tier Repair System

```
┌─────────────────────────────────────────────────────────────┐
│              INPUT: Segmented CV JSON                        │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  TIER 1: Simple Deterministic Repairs (LAYER 0.1)           │
│  • Contact coalescing                                        │
│  • ALL-CAPS promotion                                        │
│  • Numbered grant nesting                                    │
│  • Education metadata                                        │
│  • Talk promotion                                            │
│  Cost: $0, Speed: <1s, Coverage: 75-85%                     │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│  TIER 2: Confidence-Based LLM Repair (LAYER 0.2)            │
│  Triggered when:                                             │
│  • Deterministic repairs produce unclear structure           │
│  • Unusual CV format detected                                │
│  • Validation flags low confidence                           │
│  Cost: ~$0.05/CV, Speed: 5-10s, Coverage: +10-15%          │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│  TIER 3: Human Review Queue (LAYER 0.3)                     │
│  For the 3-5% that still fail:                              │
│  • Flag for manual review                                    │
│  • Learn from corrections                                    │
│  • Update patterns if issue repeats 3+ times                 │
│  Cost: Manual, Coverage: Final 3-5%                         │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│         LAYER 1: Structural Signals (existing)               │
│         LAYER 2: Taxonomy Mapping (existing)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## LLM Integration Points

### **Point 1: Segmentation Quality Check**
After initial segmentation, check for ambiguous structure:

```python
def needs_llm_repair(segmented_cv):
    """Detect if CV needs LLM-assisted repair."""
    signals = {
        'too_many_top_level_groups': len(top_level_groups) > 50,
        'too_few_groups': len(top_level_groups) < 5,
        'many_unknown_labels': unknown_pct > 40,
        'fragmented_sections': avg_entries_per_group < 2,
        'unusual_structure': has_tables or mixed_languages
    }
    return any(signals.values())
```

### **Point 2: Post-Repair Validation**
After deterministic repairs, validate quality:

```python
def validate_repair_quality(repaired_cv):
    """Check if repairs produced good structure."""
    issues = []

    if has_orphaned_entries(repaired_cv):
        issues.append('orphaned_entries')

    if has_duplicate_sections(repaired_cv):
        issues.append('duplicate_sections')

    if has_unnested_content(repaired_cv):
        issues.append('unnested_content')

    return len(issues) > 0, issues
```

### **Point 3: LLM-Assisted Repair**
When validation fails, use LLM to fix:

```python
def llm_assisted_repair(cv, issues):
    """
    Use LLM to fix structural issues that deterministic patterns missed.

    Cost: ~$0.05 per CV (only called for 15-20% of CVs)
    """
    prompt = f"""
You are a CV structure expert. Fix these structural issues in the CV:

ISSUES DETECTED:
{json.dumps(issues, indent=2)}

CURRENT STRUCTURE (first 5 groups):
{json.dumps(cv['groups'][:5], indent=2)}

FIXES NEEDED:
1. Merge fragmented sections (e.g., multiple "Education" groups → one group)
2. Promote buried headers (e.g., ALL-CAPS subgroups → top-level)
3. Nest related content (e.g., numbered grants under "Funding" header)
4. Remove duplicate sections
5. Ensure clear hierarchy (max 2 levels deep)

Return ONLY the corrected 'groups' array in valid JSON.
Follow this schema exactly:
{{
  "groups": [
    {{
      "id": "G1",
      "label_inferred": "Education",
      "level": 1,
      "entries": [...],
      "subgroups": [...]
    }}
  ]
}}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )

    repaired = json.loads(response.choices[0].message.content)

    # Merge LLM repairs with original metadata
    cv['groups'] = repaired['groups']
    cv['meta']['llm_repair_applied'] = True
    cv['meta']['llm_repair_issues'] = issues

    return cv
```

---

## Four-Phase Execution Plan

### **Phase 1: Stabilization (CVs 1-18)** ← WE ARE HERE

**Objective**: Validate current pipeline, collect baseline metrics.

**Execution**:
```bash
# Process CVs 4-18 (already have 3)
python3 batch_process_with_repair.py --cvs 4-18 --no-new-fixes

# Generate metrics
python3 analyze_phase1.py
```

**Deliverables**:
- [ ] 18 CVs processed
- [ ] Baseline metrics collected
- [ ] Issues categorized by frequency
- [ ] LLM trigger rate measured (how many need Tier 2?)

**Success Criteria**:
- 15/18 CVs successful (83%+)
- Avg confidence ≥0.85
- Clear understanding of gap to 95%

**Timeline**: 3-5 days

---

### **Phase 2: Add LLM Layer (CVs 19-35)**

**Objective**: Implement Tier 2 (LLM-assisted repair), measure improvement.

**Execution**:
```bash
# Implement llm_assisted_repair() in repair_segmentation.py
# Process with LLM layer enabled
python3 batch_process_with_repair.py --cvs 19-35 --enable-llm-repair

# Compare to Phase 1 baseline
python3 compare_phases.py --phase1 --phase2
```

**Deliverables**:
- [ ] LLM repair layer implemented
- [ ] Tier 2 triggered for 15-25% of CVs
- [ ] Accuracy improvement measured
- [ ] Cost per CV calculated

**Success Criteria**:
- 32/35 CVs successful (91%+)
- Avg confidence ≥0.88
- LLM cost <$0.10/CV

**Timeline**: 5-7 days

---

### **Phase 3: Optimization (CVs 36-60)**

**Objective**: Fine-tune triggers, optimize for 95% accuracy.

**Execution**:
```bash
# Adjust LLM trigger thresholds based on Phase 2 data
# Process larger batch
python3 batch_process_with_repair.py --cvs 36-60 --optimized-triggers

# Track which CVs go to Tier 3 (human review)
python3 flag_tier3_cvs.py
```

**Deliverables**:
- [ ] Optimized LLM triggers
- [ ] 95% accuracy achieved on 60 CVs
- [ ] Clear criteria for Tier 3 (human review)

**Success Criteria**:
- 57/60 CVs successful (95%)
- Avg confidence ≥0.90
- <5% require Tier 3

**Timeline**: 7-10 days

---

### **Phase 4: Final Validation (CVs 61-80)**

**Objective**: Validate 95% accuracy on final 20 CVs, document learnings.

**Execution**:
```bash
# Process final batch with frozen code
python3 batch_process_with_repair.py --cvs 61-80 --code-freeze

# Generate final report
python3 final_analysis.py --all-80-cvs
```

**Deliverables**:
- [ ] All 80 CVs processed
- [ ] 95%+ accuracy validated
- [ ] Production deployment guide
- [ ] Lessons learned document

**Success Criteria**:
- 76/80 CVs successful (95%)
- Avg confidence ≥0.90 across all 80
- Clear documentation for production

**Timeline**: 5-7 days

---

## Metrics Tracking

### **Per-CV Metrics**
```json
{
  "cv_id": "2025",
  "phase": 1,
  "tier1_repairs_applied": ["contact_coalescing", "all_caps_promotion"],
  "tier2_triggered": false,
  "tier3_flagged": false,
  "final_confidence": 0.918,
  "hint_coverage": 46.8,
  "api_calls": 94,
  "tokens": 135134,
  "cost": 0.08,
  "processing_time_sec": 45
}
```

### **Phase Aggregate Metrics**
```json
{
  "phase": 1,
  "cvs_processed": 18,
  "success_rate": 0.833,
  "avg_confidence": 0.87,
  "avg_hint_coverage": 42.3,
  "tier1_only_pct": 0.75,
  "tier2_triggered_pct": 0.20,
  "tier3_flagged_pct": 0.05,
  "avg_cost_per_cv": 0.06,
  "total_tokens": 876543
}
```

---

## Decision Rules

### **When to Trigger Tier 2 (LLM Repair)**
```python
def should_trigger_llm_repair(cv, tier1_result):
    """Decide if CV needs LLM assistance."""

    # Trigger if deterministic repairs look questionable
    if tier1_result['repaired_groups'] > tier1_result['original_groups'] * 2:
        return True  # Too much fragmentation

    if tier1_result['repaired_groups'] < 5:
        return True  # Too few groups

    # Trigger if validation detects issues
    has_issues, issues = validate_repair_quality(cv)
    if has_issues and len(issues) > 2:
        return True  # Multiple structural problems

    return False
```

### **When to Flag for Tier 3 (Human Review)**
```python
def should_flag_for_human_review(cv, tier1_result, tier2_result):
    """Flag CVs that need human review."""

    # After both Tier 1 and Tier 2, still low confidence
    if tier2_result and tier2_result['confidence'] < 0.80:
        return True

    # Unusual format that LLM couldn't handle
    if cv.get('meta', {}).get('unusual_format'):
        return True

    # Critical content (tenure/promotion CV)
    if cv.get('meta', {}).get('high_stakes', False):
        return True

    return False
```

---

## Cost Projections

### **Tier 1 Only** (75% of CVs)
- Cost: $0/CV (deterministic)
- Time: ~30s/CV (segmentation + repair + taxonomy)
- Total for 60 CVs: $0

### **Tier 1 + Tier 2** (20% of CVs)
- Tier 1: $0
- Tier 2 LLM repair: ~$0.05/CV
- Total for 16 CVs: $0.80

### **Tier 3 Human Review** (5% of CVs)
- Tier 1+2: $0.05
- Human time: 10 min/CV @ $30/hr = $5.00
- Total for 4 CVs: $20.20

**Total for 80 CVs**: ~$21 + segmentation/taxonomy costs

---

## Phase 1 Kickoff Checklist

### **Immediate Actions**:
- [x] Create hybrid plan document
- [ ] Create metrics tracking script
- [ ] Prepare batch processing script for CVs 4-18
- [ ] Set up results directory structure
- [ ] Run first batch (CVs 4-8)

### **First Week Goals**:
- [ ] Process 15 additional CVs (total 18)
- [ ] Collect baseline metrics
- [ ] Identify patterns needing LLM assist
- [ ] Calculate Tier 2 trigger rate
- [ ] Document issues systematically

---

## Version Control

- **Version**: 1.0
- **Created**: 2025-11-08
- **Status**: ACTIVE - Phase 1 starting now
- **Target**: 95% accuracy across 80 CVs
- **Approach**: Hybrid (deterministic + LLM)
