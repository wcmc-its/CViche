# Classification Rules v1.0.0 - Description

**Status:** Superseded by v2.0.0 on 2025-01-28

## Overview

Version 1.0 consisted of 31 ad-hoc rules developed iteratively during pipeline development. The rules were effective (~93% accuracy) but had several issues:

## Structure (v1.0)

The rules were organized as a sequential numbered list:
1. Rule 1 - T (Appendix/Other) basics
2. Rule 2 - Structural separators
3. Rule 3 - Personal data (A)
...through...
31. Rule 31 - Grant status (M2 subcodes)

## Known Issues (Fixed in v2.0)

### 1. Document Title Misclassification
- **Problem:** "CURRICULUM VITAE" was being classified as A (Personal Data) instead of T
- **Root cause:** No explicit rule for document-level structural elements
- **v2.0 fix:** Added StructuralHeaderValidator + explicit rules in Section A

### 2. Committee vs Position Confusion
- **Problem:** "Appointed to R&D Committee" was coded as D2 (hospital position)
- **Root cause:** Ambiguous position keywords overlapped with committee service
- **v2.0 fix:** Added CommitteePositionCorrector validator + clearer D vs P/Q2 rules

### 3. Reasoning-Code Mismatch
- **Problem:** LLM reasoning said "T is appropriate" but assigned code A
- **Root cause:** Bug in final code assignment step of LLM response
- **v2.0 fix:** Added ReasoningConsistencyChecker to auto-correct

### 4. Missing Code Definitions
- **Problem:** C (training), L1-L3 (clinical), Q4A-D (editorial) not fully defined
- **Root cause:** Gradual taxonomy expansion without rule updates
- **v2.0 fix:** Added complete definitions in dedicated sections

### 5. Cross-Reference Gaps
- **Problem:** K2 (clinical teaching) vs L1-L3 (clinical activity) confusion
- **Root cause:** Similar concepts in different categories without clear guidance
- **v2.0 fix:** Added explicit cross-references

## Token Usage

- v1.0: ~8,542 tokens
- v2.0: ~6,772 tokens (20.7% reduction)

## Note

The full text of v1.0 rules was not archived before the refactor. This description documents what was changed and why. Future versions will include full rule text exports.
