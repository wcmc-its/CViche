# Iterative Testing Framework for 80 CV Processing

## Goal
Process 80 CVs systematically while avoiding overfitting and maintaining code quality.

## Core Principles

### 1. **Stabilization Over Iteration**
- Don't add code after every batch
- Let patterns emerge across multiple CVs
- Only fix issues that appear in 3+ CVs

### 2. **Regression Testing**
- Test new fixes on ALL previously processed CVs
- Track whether fixes help or hurt existing CVs
- Maintain a "golden set" of well-working CVs

### 3. **Metrics-Driven Decisions**
- Use data, not intuition
- Track objective metrics over time
- Only add complexity if metrics justify it

### 4. **Code Freeze Points**
- Stabilize code at specific milestones
- Separate collection from implementation
- Batch fixes rather than incremental tweaks

---

## Four-Phase Approach

### **Phase 1: Stabilization (CVs 1-18)**
*Status: 3/18 complete (Holtz, Simpson, Blakely)*

**Objective**: Validate current pipeline on diverse CVs WITHOUT adding new code.

**Activities**:
```bash
# Process each CV with current repair pipeline
for cv in CVs_4_to_18:
    python3 batch_process_with_repair.py --cv {cv}

# Collect feedback (manual ChatGPT review or automated)
# Log issues in issues_log.json
```

**Success Metrics**:
- [ ] 15 CVs processed successfully
- [ ] Avg confidence ≥ 0.90 across all CVs
- [ ] Hint coverage ≥ 40% across all CVs
- [ ] No catastrophic failures (confidence < 0.70)

**Issue Collection Template**:
```json
{
  "cv_id": "2025",
  "cv_name": "Denckla",
  "issue_type": "segmentation|signal|taxonomy",
  "description": "ALL-CAPS header not promoted",
  "frequency": "appears_in_cvs: [2025, 2024, 2100]",
  "severity": "critical|moderate|minor",
  "proposed_fix": "Extend ALL-CAPS pattern to include..."
}
```

**Deliverables**:
- `phase1_results_summary.json` - Stats for all 18 CVs
- `phase1_issues_log.json` - Categorized issues
- `phase1_analysis.md` - Patterns and recommendations

---

### **Phase 2: Targeted Fixes (CVs 19-30)**

**Objective**: Add ONLY high-priority fixes that apply to multiple CVs.

**Fix Criteria** (ALL must be met):
1. ✅ Appears in ≥3 CVs from Phase 1
2. ✅ Severity: critical or moderate
3. ✅ Fix is general (not CV-specific hack)
4. ✅ Regression test passes on CVs 1-18

**Process**:
```bash
# 1. Implement fix in repair_segmentation.py
# 2. Add test case to test_repairs.py
# 3. Run regression test
python3 test_repairs.py --test-on-all-cvs

# 4. If regression passes, process next batch
python3 batch_process_with_repair.py --cvs 19-30
```

**Max Fixes Allowed**: 3 new patterns
**Rationale**: Limit complexity growth

**Deliverables**:
- `phase2_fixes_implemented.md` - What we added and why
- `phase2_regression_test_results.json` - Impact on CVs 1-18
- `phase2_results_summary.json` - Stats for CVs 19-30

---

### **Phase 3: Validation (CVs 31-50)**

**Objective**: CODE FREEZE. Validate that pipeline works broadly.

**Activities**:
```bash
# NO CODE CHANGES - just process CVs
python3 batch_process_with_repair.py --cvs 31-50

# Collect comprehensive stats
python3 analyze_batch_results.py --cvs 1-50
```

**Success Metrics**:
- [ ] Avg confidence ≥ 0.90 across all 50 CVs
- [ ] No more than 5% catastrophic failures
- [ ] Repairs fire appropriately (not over-/under-applied)

**If Metrics Fail**:
- Analyze root causes
- Consider ONE critical fix if blocking >10 CVs
- Re-run regression tests

**Deliverables**:
- `phase3_comprehensive_stats.json` - All 50 CVs analyzed
- `phase3_repair_effectiveness.md` - Which repairs helped most
- `phase3_failure_analysis.md` - CVs that didn't work and why

---

### **Phase 4: Final Push (CVs 51-80)**

**Objective**: Complete processing with minimal changes.

**Activities**:
```bash
# Process remaining CVs
python3 batch_process_with_repair.py --cvs 51-80

# Generate final analysis
python3 final_analysis.py --all-cvs
```

**Only Allow**:
- Bug fixes for critical blockers
- Documentation updates
- Statistics collection

**Deliverables**:
- `final_results_all_80_cvs.json` - Complete stats
- `final_repair_pipeline_effectiveness.md` - What worked, what didn't
- `recommendations_for_production.md` - Next steps

---

## Metrics to Track

### Per-CV Metrics
```json
{
  "cv_id": "2025",
  "cv_name": "Denckla",
  "phase": 1,
  "segmentation": {
    "original_groups": 30,
    "repaired_groups": 35,
    "repairs_applied": ["contact_coalescing", "all_caps_promotion"]
  },
  "signals": {
    "total_entries": 47,
    "hint_coverage_pct": 46.8,
    "top_signals": ["employment_pattern", "award_honor"]
  },
  "taxonomy": {
    "avg_confidence": 0.918,
    "api_calls": 94,
    "tokens": 135134,
    "low_confidence_sections": []
  },
  "issues": [
    {
      "type": "segmentation",
      "description": "Funding section split across pseudo-headers"
    }
  ]
}
```

### Aggregate Metrics (Track Over Time)
- **Success Rate**: % CVs with avg confidence ≥ 0.90
- **Avg Confidence**: Mean across all CVs (should be stable or increasing)
- **Hint Coverage**: Mean % entries with structural hints
- **API Efficiency**: Avg API calls per CV (should be stable)
- **Repair Effectiveness**: % CVs where repairs changed structure
- **Regression Count**: # previously working CVs broken by new fixes

---

## Decision Rules

### When to Add a New Fix
```python
if issue.frequency >= 3 CVs:
    if issue.severity in ['critical', 'moderate']:
        if fix_is_general(proposed_fix):
            if regression_test_passes(fix, all_processed_cvs):
                ADD_FIX
            else:
                REJECT("Breaks existing CVs")
        else:
            DEFER("Too specific, revisit in Phase 4")
    else:
        DEFER("Low severity")
else:
    DEFER("Too rare")
```

### When to Stop Adding Fixes
```python
if phase >= 3:
    CODE_FREEZE = True

if repair_segmentation.lines > 1000:
    REFACTOR_REQUIRED = True

if new_fix_breaks > 2 existing_cvs:
    REJECT_FIX = True
```

---

## Anti-Patterns to Avoid

### ❌ **Don't Do This**:
1. **Add a fix after every CV** - This leads to overfitting
2. **Fix unique issues** - Only fix patterns that repeat
3. **Skip regression testing** - Always test on previous CVs
4. **Ignore metrics** - Data should drive decisions
5. **Grow code indefinitely** - Refactor when complexity increases

### ✅ **Do This Instead**:
1. **Batch issues across CVs** - Look for patterns
2. **Fix systematic problems** - Ignore one-offs
3. **Test every change** - Regression test suite
4. **Track metrics religiously** - Automated analysis
5. **Refactor when needed** - Keep code clean

---

## Success Criteria for 80 CV Project

### Minimum Viable Success
- [ ] 70/80 CVs processed (87.5% success rate)
- [ ] Avg confidence ≥ 0.85 across all CVs
- [ ] No more than 10% catastrophic failures (conf < 0.70)
- [ ] Code maintainable (<1500 lines in repair module)
- [ ] Clear documentation of what works and what doesn't

### Stretch Goals
- [ ] 75/80 CVs processed (93.8% success rate)
- [ ] Avg confidence ≥ 0.90 across all CVs
- [ ] <5% catastrophic failures
- [ ] Automated repair effectiveness analysis
- [ ] Production-ready pipeline with monitoring

---

## Current Status

**Phase 1 Progress**: 3/18 CVs complete
- ✅ Holtz (2002): 0.930 avg confidence
- ✅ Simpson: 0.939 avg confidence
- ✅ Blakely (2074): 0.918 avg confidence

**Repairs Implemented**: 6 fixes in repair_segmentation.py
**Code Size**: 485 lines (healthy)
**Documentation**: Good (3 docs created)

**Next Steps**:
1. Complete Denckla segmentation test (in progress)
2. Process CVs 4-18 with current code (no changes)
3. Collect systematic issue log
4. Analyze patterns at CV 18 milestone

---

## Document Version

- **Version**: 1.0
- **Created**: 2025-11-08
- **Last Updated**: 2025-11-08
- **Status**: Active framework for 80 CV processing
