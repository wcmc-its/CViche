# Classification Rules Archive

This directory contains versioned archives of the CV taxonomy classification rules used in `stage_3b_entry_classifier.py`.

## Current Version

**v2.0.0** (2025-01-28) - Located in `stage_3b_entry_classifier.py`

## Versioning Policy

1. **When to bump versions:**
   - **Patch (2.0.x)**: Typo fixes, clarifications that don't change classification behavior
   - **Minor (2.x.0)**: New rules added, edge cases refined
   - **Major (x.0.0)**: Structural reorganization, taxonomy code changes

2. **Before making major changes:**
   - Export current rules to a timestamped file in this directory
   - Document the change rationale

## Version History

### v2.0.0 (2025-01-28)
- **Structure:** Refactored from 31 ad-hoc rules into 9 logical sections (A-I)
- **Token count:** ~6,772 (20.7% reduction from v1.0)
- **New additions:**
  - C (training) codes: C1 (pre-doctoral), C2 (post-doctoral)
  - L1-L3 (clinical activity) codes with K2 cross-references
  - Q4A-D (editorial roles) detailed definitions
  - Conference acronym list for S8 detection
  - Cross-references between related taxonomy sections
- **Post-classification validators added:**
  - `StructuralHeaderValidator`: Detects document titles, page numbers → T
  - `CommitteePositionCorrector`: Distinguishes committee service (P/Q2) from positions (D)
  - `ReasoningConsistencyChecker`: Auto-corrects when reasoning contradicts assigned code

### v1.0.0 (pre-2025-01-28)
- **Structure:** 31 ad-hoc rules in sequential format
- **Token count:** ~8,542
- **Issues addressed in v2.0:**
  - Document titles (CURRICULUM VITAE) → coded as A instead of T
  - Committee service → coded as D instead of P/Q2
  - Reasoning-code mismatches (reasoning says "T" but code is "A")
  - Missing C, L, Q4 subcategory definitions

## Files

- `README.md` - This file
- `rules_v1_0_0_DESCRIPTION.md` - Description of v1.0 rules (not full export)
- Future versions will be archived as `rules_vX_X_X.txt`

## Extracting Current Rules

To export current rules for archiving:

```python
# In stage_3b_entry_classifier.py, the rules are in the BATCH_CLASSIFICATION_PROMPT template
# Look for the section between:
#   "CV TAXONOMY CLASSIFICATION RULES v2.0"
# and
#   "════════════════════════════════════════════════════════════════════════════════"
#   (the "I. OUTPUT FORMAT" section marks the end of rules)
```

## Related Files

- `stage_3b_entry_classifier.py` - Contains current rules in `BATCH_CLASSIFICATION_PROMPT`
- `core/validators/` - Post-classification auto-correction validators
- `core/taxonomy_v7.json` - Full taxonomy code definitions
