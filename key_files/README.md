# Key Files Directory - Taxonomy Code Reference

This directory contains symbolic links (aliases) to critical reference files for the Scholar Signals CV parsing system.

## Purpose

Central location for quick access to:
- Architecture documentation
- Taxonomy definitions
- Core implementation files

## Files in This Directory

All files are **MacOS Aliases** (symbolic links) to their actual locations:

- `PIPELINE_ARCHITECTURE_V10.md` - System architecture overview
- `taxonomy_reference.md` - Complete WCM taxonomy documentation
- `taxonomy_definitions.py` - (if applicable) Taxonomy code definitions
- `taxonomy_mapper_v2.py` - Core taxonomy mapping implementation

---

## Taxonomy Code Locations - Update Guide

When updating WCM CV taxonomy codes, you must update **ALL** of the following locations to maintain consistency:

### 1. **PRIMARY SOURCE** - `src/unified_pipeline/core/candidate_surfacer.py`

**Location**: Lines 36-116
**Variable**: `TAXONOMY_CODES_CONDENSED`
**Purpose**: Dictionary used by:
- Candidate surfacing LLM prompts
- Taxonomy mapper enum constraint
- Helper functions throughout the pipeline

**Format**:
```python
TAXONOMY_CODES_CONDENSED = {
    # Parent codes
    "A": "Personal/Contact Information",

    # Child codes
    "B1": "Academic Degrees (MD, PhD, etc.)",
    "B2": "Other Educational Experiences",

    # Sub-child codes
    "Q4A": "Editor/Co-Editor",
    "Q4B": "Journals/Textbooks/Books (Editorial roles)",
    # ... etc
}
```

**Current Count**: 59 codes (58 excluding T)

---

### 2. **ENUM CONSTRAINT** - `src/unified_pipeline/core/taxonomy_mapper_v2.py`

**Location**: Line 3979 (approximately)
**Purpose**: JSON schema enum that prevents LLM from generating invalid taxonomy codes
**Mechanism**: Imports `TAXONOMY_CODES_CONDENSED` from `candidate_surfacer.py`

**Code**:
```python
"taxonomy_code": {
    "type": "string",
    "enum": [code for code in TAXONOMY_CODES_CONDENSED.keys() if code != "T"],
    "description": "Selected taxonomy code (prefer PRIMARY candidates). T is hidden - low confidence routes there automatically."
},
```

**Important**:
- No changes needed here when updating TAXONOMY_CODES_CONDENSED
- Automatically inherits changes from candidate_surfacer.py
- Excludes "T" to implement T-hiding strategy

---

### 3. **DOCUMENTATION** - `taxonomy_reference.md`

**Location**: Root directory
**Purpose**: User-facing reference documentation
**Current Version**: v7.0

**Must Update**:
- Code listings in all categories (A-T)
- Sub-category structures (Q4A-Q4D, M2A-M2C, etc.)
- Routing rules (T2→M3, T3→B2, etc.)
- Version number and update date

**Validation**: Compare against `TAXONOMY_CODES_CONDENSED` to ensure all codes documented

---

### 4. **CANONICAL SOURCE** - WCM CV Template

**Location**: `outputs/legacy/cv_pipeline/docs/`
**Files**:
- `CANONICAL_WCM_TAXONOMY.md`
- `wcm_cv_template_faculty_october_2022_final.docx`

**Purpose**: Official WCM taxonomy definitions (reference only, do not edit)

---

## Update Procedure

When WCM updates their CV taxonomy structure:

### Step 1: Update Source Code

1. **Edit** `src/unified_pipeline/core/candidate_surfacer.py`
   - Locate `TAXONOMY_CODES_CONDENSED` dictionary (lines 36-116)
   - Add new codes in appropriate section (parent/child/sub-child)
   - Remove deprecated codes
   - Update descriptions if changed

2. **Clear Python Cache**
   ```bash
   find src/unified_pipeline -type d -name "__pycache__" -exec rm -rf {} +
   find src/unified_pipeline -name "*.pyc" -delete
   ```

### Step 2: Update Documentation

3. **Edit** `taxonomy_reference.md`
   - Update version number
   - Update all code listings
   - Update routing rules if applicable
   - Update date

### Step 3: Validate

4. **Run Validation Script**
   ```python
   python3 -c "
   import sys
   sys.path.insert(0, 'src/unified_pipeline')
   from core.candidate_surfacer import TAXONOMY_CODES_CONDENSED

   print(f'Total codes: {len(TAXONOMY_CODES_CONDENSED)}')
   print(f'Codes excluding T: {len([c for c in TAXONOMY_CODES_CONDENSED.keys() if c != \"T\"])}')

   # List all codes
   for code in sorted(TAXONOMY_CODES_CONDENSED.keys()):
       print(f'  {code}: {TAXONOMY_CODES_CONDENSED[code][:50]}')
   "
   ```

5. **Test with Sample CV**
   ```bash
   python3 src/unified_pipeline/stage_3_taxonomy_mapper.py "data/sample_cvs/word/[CV_FILE].docx" --mode guided
   ```

6. **Check for Invalid Codes**
   - Review output for any enum validation errors
   - Confirm all valid codes are accepted
   - Confirm invalid codes are rejected

### Step 4: Commit Changes

7. **Git Commit**
   ```bash
   git add src/unified_pipeline/core/candidate_surfacer.py
   git add taxonomy_reference.md
   git commit -m "Update WCM taxonomy: [description of changes]"
   ```

---

## Key Taxonomy Concepts

### Code Structure

- **Parent codes** (11): Single letter (A, C, E, G, H, I, J, O, P, R, T)
- **Child codes** (36): Letter + number (B1, D1, K1, M1, etc.)
- **Sub-child codes** (12): Letter + number + letter (Q4A, M2A, N3A, etc.)

**Total**: 59 valid codes

### Special Cases

#### T-Hiding Strategy

- "T" (Appendix/Other) is excluded from the enum
- LLM cannot directly select "T"
- Low-confidence classifications (< 0.4) automatically route to "T"
- Forces the LLM to attempt real classifications

#### Invalid Codes

These codes do NOT exist and should never be in the dictionary:
- Q5 (doesn't exist per WCM template)
- S10-S15 (processing intermediates only, not final codes)
- Any invented codes (A4, A6, A7, A9, AT1-AT5, etc.)

#### Routing Rules

Some T-subcodes map to other categories:
- T2 (Technology Transfer) → M3 (Patents & Inventions)
- T3 (Professional Development) → B2 (Other Educational Experiences)
- T1 (Community & Public Engagement) → Stays under T
- T4 (References) → Stays under T

---

## Critical Subcodes by Category

### D - Professional Positions
- D1: Academic Appointments
- D2: Hospital Appointments
- D3: Other Professional Positions & Employment

### F - Licensure & Certification
- F1: Licensure
- F2: Board Certification

### K - Educational Contributions
- K1-K4: Teaching activities
- K5: Community education

### L - Clinical Practice
- L1: Clinical Practice
- L2: Clinical Innovations
- L3: Clinical Leadership

### M - Research
- M2 (parent): Research Support/Funding
  - M2A: Current Funding
  - M2B: Past (Completed) Funding
  - M2C: Pending Funding
- M4 (parent): Clinical Trials
  - M4A: Interventional Clinical Trials
  - M4B: Observational Clinical Trials
  - M4C: Device Clinical Trials

### N - Mentoring
- N3 (parent): Mentees
  - N3A: Current Mentees
  - N3B: Past Mentees

### Q - Professional Development
- Q4 (parent): Editorial Activities
  - Q4A: Editor/Co-Editor
  - Q4B: Journals/Textbooks/Books (Editorial roles)
  - Q4C: Editorial Board Membership
  - Q4D: Journal Reviewing/Ad hoc Reviewing

---

## Common Issues and Troubleshooting

### Issue: "Invalid taxonomy code" errors

**Cause**: Code not in `TAXONOMY_CODES_CONDENSED`
**Solution**: Add code to dictionary in `candidate_surfacer.py`

### Issue: Valid code being rejected by enum

**Cause**: Code missing from dictionary or Python cache stale
**Solution**:
1. Verify code exists in `TAXONOMY_CODES_CONDENSED`
2. Clear Python cache (see Update Procedure, Step 2)
3. Re-run test

### Issue: Documentation doesn't match code

**Cause**: `taxonomy_reference.md` not updated after code changes
**Solution**: Manually sync documentation with dictionary

### Issue: LLM inventing codes

**Cause**: Enum constraint not working
**Solution**: Verify line 3979 in `taxonomy_mapper_v2.py` has enum constraint active

---

## Version History

### v7.0 (2025-11-25)
- Complete 59-code taxonomy structure
- Added all subcodes: Q4A-Q4D, M2A-M2C, M4A-M4C, N3A-N3B, L1-L3, K5, F1-F2, D3
- Removed invalid codes: Q5, S10-S15
- Implemented T-hiding strategy
- Added enum constraint to prevent invalid code generation

---

## Contact and References

**Canonical WCM Template**: October 2022 Final
**System Architecture**: PIPELINE_ARCHITECTURE_V10.md
**Guided Classification**: V10 architecture (2-pass: candidate surfacing + classification)

For questions about WCM taxonomy structure, refer to the canonical template in `outputs/legacy/cv_pipeline/docs/`.
