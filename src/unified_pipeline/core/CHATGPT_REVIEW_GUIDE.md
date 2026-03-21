# ChatGPT Review Guide: CV Taxonomy Classification with Signal Library

## Overview

This guide describes the validation process for the comprehensive signal library (19 signals) for CV taxonomy classification. Each CV generates a pair of files for ChatGPT review.

## File Pairs for Review

For each of the 5 validation CVs, you'll have:

### 1. **Holtz CV** (Already completed)
- **Input (Preprocessed):** `validation_CV_2002_Holtz_Heidi_PhD_preprocessed.json`
- **Output (Mapped):** `holtz_with_all_signals.json`
- **Audit Report:** `holtz_with_all_signals_AUDIT.json`
- **Profile:** Nursing faculty (PhD), Assistant Professor
- **Sections:** 18 sections, 88 entries

### 2. **Cook CV** (Processing)
- **Input (Preprocessed):** `validation_CV_2018_Cook_Dane_PhD_preprocessed.json`
- **Output (Mapped):** `validation_CV_2018_Cook_Dane_PhD_with_signals.json`
- **Audit Report:** `validation_CV_2018_Cook_Dane_PhD_with_signals_AUDIT.json`
- **Profile:** PhD faculty

### 3. **Lau CV** (Processing)
- **Input (Preprocessed):** `validation_CV_2007_Lau_Frank_MD_preprocessed.json`
- **Output (Mapped):** `validation_CV_2007_Lau_Frank_MD_with_signals.json`
- **Audit Report:** `validation_CV_2007_Lau_Frank_MD_with_signals_AUDIT.json`
- **Profile:** MD faculty (clinical + research)

### 4. **Albrecht CV** (Processing)
- **Input (Preprocessed):** `validation_CV_2003_Albrecht_Jennifer_PhD_preprocessed.json`
- **Output (Mapped):** `validation_CV_2003_Albrecht_Jennifer_PhD_with_signals.json`
- **Audit Report:** `validation_CV_2003_Albrecht_Jennifer_PhD_with_signals_AUDIT.json`
- **Profile:** PhD faculty

### 5. **Mucci CV** (Processing)
- **Input (Preprocessed):** `validation_CV_2009_Mucci_Lorelei_ScD_preprocessed.json`
- **Output (Mapped):** `validation_CV_2009_Mucci_Lorelei_ScD_with_signals.json`
- **Audit Report:** `validation_CV_2009_Mucci_Lorelei_ScD_with_signals_AUDIT.json`
- **Profile:** ScD faculty (epidemiology)

## How to Review with ChatGPT

### Step 1: Upload File Pair
For each CV, upload both files to ChatGPT:
1. The preprocessed input file (`*_preprocessed.json`)
2. The mapped output file (`*_with_signals.json`)

### Step 2: Ask ChatGPT to Review

Use this prompt:

```
I have a CV taxonomy classification system that maps CV sections to a standardized taxonomy (sections A-T with subsections). I'm testing a comprehensive signal library (19 structural pattern detectors) to improve classification accuracy.

Please review these two files:
1. Input: [preprocessed CV file]
2. Output: [mapped CV with signals file]

For each CV section, please evaluate:

1. CLASSIFICATION ACCURACY:
   - Is the final_section_id correct for this CV section?
   - Does the final_canonical_name match the content?
   - Are there any obvious misclassifications?

2. STRUCTURAL HINTS EFFECTIVENESS:
   - Which signals fired for this section? (check triggered_hints)
   - Are the fired signals appropriate for the content?
   - Are there false positives (signals that shouldn't have fired)?
   - Are there false negatives (signals that should have fired but didn't)?

3. EDGE CASES & AMBIGUITIES:
   - Are there any ambiguous sections that could belong to multiple categories?
   - How did the classifier handle them?
   - Were the structural hints helpful in disambiguating?

4. MISSING SIGNALS:
   - Are there any patterns in the CV sections that aren't captured by current signals?
   - What additional signals would help improve accuracy?

Please provide:
- Overall accuracy assessment
- List of any misclassifications with suggested fixes
- Signal effectiveness report (which signals are working vs not working)
- Recommendations for new signals or refinements
```

### Step 3: Review the Audit Report (Optional)

The audit report (`*_AUDIT.json`) contains:
- Signal fire counts and coverage statistics
- Examples of where each signal fired
- Per-section breakdown of signal effectiveness

Use this to understand:
- Which signals are firing frequently (good coverage)
- Which signals are inactive (may fire in other CVs)
- Which sections have high/low hint coverage

## Signal Library (19 Signals)

### Phase 1 Signals (Already tested - 6 signals)
1. **clinical_role** - Detects clinical keywords (RN, Clinical Nurse, etc.)
2. **major_lecture_header** - Detects "MAJOR INVITED LECTURE" in headers
3. **training_received_header** - Detects training/education received patterns
4. **unit_acronym** - Detects medical unit acronyms (ICU, ER, NICU, etc.)
5. **podium** - Detects "Podium Presentation" in entries
6. **employment_pattern** - Detects date range + institution patterns

### Phase 2 Signals (New - 13 signals)
7. **citation_like** - Detects citation patterns (author list + journal)
8. **location_tail** - Detects City, State or City, Country at end of entry
9. **grant_amount** - Detects dollar amounts in grant descriptions
10. **doi_pattern** - Detects DOI identifiers
11. **pmid_pattern** - Detects PubMed IDs
12. **author_first** - Detects first author position
13. **author_last** - Detects last author position
14. **author_corresponding** - Detects corresponding author
15. **keynote_indicators** - Detects explicit keynote/plenary text
16. **panel_workshop_indicators** - Detects panel/workshop participation
17. **mentee_pattern** - Detects mentee/trainee relationships
18. **teaching_role** - Detects teaching/instructor positions
19. **committee_role** - Detects committee membership
20. **award_honor** - Detects award/honor keywords

## Expected Outcomes

### Success Criteria
- **Accuracy:** 95%+ sections classified correctly
- **Coverage:** 70%+ entries receive structural hints
- **Precision:** <5% false positive rate on signals
- **No Regressions:** Classifications that were correct before remain correct

### Known Edge Cases
1. **ADDITIONAL TRAINING vs MENTORING ACTIVITIES (K):**
   - Training received → B6 (Continuing Education)
   - Training provided → K (Mentoring Activities)
   - Signal: `training_received_header` helps distinguish

2. **CLINICAL Roles vs ACADEMIC POSITIONS:**
   - Clinical nursing positions → L (Clinical Practice)
   - Faculty positions → D (Academic Positions)
   - Signal: `clinical_role` helps distinguish

3. **MAJOR INVITED LECTURES - Subsection disambiguation:**
   - Keynotes/plenaries → R1
   - Grand Rounds → R2
   - Panels/workshops → R3
   - Signals: `keynote_indicators`, `panel_workshop_indicators` help distinguish

## Questions for ChatGPT to Address

1. **Are the classifications correct?**
   - List any misclassifications
   - Provide rationale for corrections

2. **Which signals are helping vs causing regressions?**
   - Identify high-value signals
   - Identify noisy/inaccurate signals

3. **What additional signals might be needed?**
   - Patterns not captured by current library
   - Suggested signal implementations

4. **Are there false positives (signals firing incorrectly)?**
   - Specific examples of false positives
   - Suggested refinements to signal logic

## Batch Processing Summary

Once batch processing completes, check `batch_processing_summary.json` for:
- Overall success rate
- Per-CV statistics (sections, entries, hint coverage)
- Any processing errors

## Next Steps After Review

Based on ChatGPT feedback:
1. **Refine signal logic** - Fix false positives/negatives
2. **Add new signals** - Implement missing pattern detectors
3. **Remove noisy signals** - Disable signals causing regressions
4. **Expand validation set** - Test on additional CVs (target: 10-15 CVs for 90% coverage)
5. **Integrate Pass 2 hints** - Add structural hints to subsection classification
