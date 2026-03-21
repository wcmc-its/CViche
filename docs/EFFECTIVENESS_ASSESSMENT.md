# CViche Effectiveness Assessment

## Purpose

This assessment framework measures how effectively CViche transforms arbitrarily formatted CVs into standardized institutional documents. Use this to evaluate accuracy, completeness, and quality of the automated conversion.

---

## Assessment Methodology

### Sample Selection
- Select CVs representing diverse formats, lengths, and career stages
- Include edge cases (international formats, non-standard sections, sparse CVs)
- Recommended minimum: 20 CVs for statistical validity

### Evaluation Process
1. Process each CV through CViche
2. Compare output against source CV
3. Score each dimension using rubrics below
4. Calculate aggregate metrics

---

## Dimension 1: Entry Extraction Accuracy

**Question:** Did the tool correctly identify and extract all discrete entries from the source CV?

**Quantitative Counts:**
| Metric | Count |
|--------|-------|
| Total entries in source CV | ___ |
| Entries correctly extracted | ___ |
| Entries missed entirely | ___ |
| Entries incorrectly merged | ___ |
| Entries incorrectly split | ___ |
| **Extraction Rate** | **___%** |

**Subjective Score (1-10):** ___

| Score | Criteria |
|-------|----------|
| 10 | 100% extracted perfectly |
| 9 | 99%+ extracted; trivial omissions only |
| 8 | 97-98% extracted; minor issues |
| 7 | 95-96% extracted; few errors |
| 6 | 90-94% extracted; noticeable gaps |
| 5 | 85-89% extracted; significant gaps |
| 4 | 75-84% extracted; many issues |
| 3 | 60-74% extracted; unreliable |
| 1-2 | <60% extracted; failed |

---

## Dimension 2: Taxonomy Classification Accuracy

**Question:** Were entries assigned to the correct institutional CV categories?

**Quantitative Counts:**
| Metric | Count |
|--------|-------|
| Total entries classified | ___ |
| Correctly classified | ___ |
| Incorrectly classified | ___ |
| **Classification Accuracy** | **___%** |

**Subjective Score (1-10):** ___

| Score | Criteria |
|-------|----------|
| 10 | 100% correctly classified |
| 9 | 99%+ correct; trivial edge cases only |
| 8 | 97-98% correct; rare misclassifications |
| 7 | 95-96% correct; occasional errors |
| 6 | 90-94% correct; pattern of errors |
| 5 | 85-89% correct; frequent errors |
| 4 | 75-84% correct; systematic issues |
| 3 | 60-74% correct; unreliable |
| 1-2 | <60% correct; failed |

**Error Analysis:**
| Source Category | Misclassified As | Count |
|-----------------|------------------|-------|
| | | |
| | | |
| | | |

---

## Dimension 3: Field Extraction Completeness

**Question:** For each entry, were all relevant fields correctly extracted?

### Publications (S-codes)
| Field | Present in Source | Correctly Extracted | Accuracy % |
|-------|:-----------------:|:-------------------:|:----------:|
| Authors | ___ | ___ | ___% |
| Title | ___ | ___ | ___% |
| Journal | ___ | ___ | ___% |
| Year | ___ | ___ | ___% |
| Volume/Issue | ___ | ___ | ___% |
| Pages | ___ | ___ | ___% |
| DOI/PMID | ___ | ___ | ___% |
| **Publications Average** | | | **___%** |

### Positions (D-codes)
| Field | Present in Source | Correctly Extracted | Accuracy % |
|-------|:-----------------:|:-------------------:|:----------:|
| Title/Role | ___ | ___ | ___% |
| Institution | ___ | ___ | ___% |
| Department | ___ | ___ | ___% |
| Start Date | ___ | ___ | ___% |
| End Date | ___ | ___ | ___% |
| **Positions Average** | | | **___%** |

### Education (B-codes)
| Field | Present in Source | Correctly Extracted | Accuracy % |
|-------|:-----------------:|:-------------------:|:----------:|
| Degree | ___ | ___ | ___% |
| Institution | ___ | ___ | ___% |
| Field/Major | ___ | ___ | ___% |
| Year | ___ | ___ | ___% |
| **Education Average** | | | **___%** |

### Grants (M2-codes)
| Field | Present in Source | Correctly Extracted | Accuracy % |
|-------|:-----------------:|:-------------------:|:----------:|
| Title | ___ | ___ | ___% |
| Funder/Agency | ___ | ___ | ___% |
| Role (PI/Co-I) | ___ | ___ | ___% |
| Amount | ___ | ___ | ___% |
| Dates | ___ | ___ | ___% |
| Grant Number | ___ | ___ | ___% |
| **Grants Average** | | | **___%** |

**Overall Field Extraction Score (1-10):** ___

---

## Dimension 4: PubMed Enrichment Success

**Question:** How effectively did the tool match and enrich publications with PubMed data?

**Quantitative Metrics:**
| Metric | Count |
|--------|:-----:|
| Total publications in source | ___ |
| Publications eligible for PubMed matching | ___ |
| Publications successfully matched | ___ |
| False matches (wrong publication) | ___ |
| Missed matches (should have matched) | ___ |

| Calculated Rate | Value |
|-----------------|:-----:|
| **Enrichment Rate** (matched / eligible) | ___% |
| **Precision** (correct / total matches) | ___% |
| **False Match Rate** (false / total matches) | ___% |

**Subjective Score (1-10):** ___

*Consider: Did enrichment add value? Were matches reliable? Did it handle edge cases (non-English, old publications, preprints)?*

---

## Dimension 5: Output Document Quality

**Question:** Does the output document meet institutional format standards?

*Rate each criterion from 1-10 (1=completely wrong, 5=acceptable with issues, 10=perfect)*

| Criterion | Score (1-10) | Notes |
|-----------|:------------:|-------|
| All required sections present | ___ | |
| Entries placed in correct sections | ___ | |
| Chronological ordering accuracy | ___ | |
| Date format consistency | ___ | |
| Citation format consistency | ___ | |
| CV owner name bolding in publications | ___ | |
| Absence of duplicate entries | ___ | |
| No orphaned/misplaced content | ___ | |
| Document opens and renders correctly | ___ | |
| Table formatting and alignment | ___ | |
| **Section Average** | **___** | |

**Scoring Guide:**
- 10: Perfect, no issues
- 8-9: Minor issues, easily overlooked
- 6-7: Noticeable issues but acceptable
- 4-5: Significant issues requiring correction
- 1-3: Major problems, unusable without rework

---

## Dimension 6: Research Summary Quality (M1 Section)

**Question:** How well does the AI-generated research summary capture the researcher's work?

*Rate each aspect from 1-10*

| Aspect | Score (1-10) | Notes |
|--------|:------------:|-------|
| **Accuracy** - Facts and claims are correct | ___ | |
| **Completeness** - Covers major research themes | ___ | |
| **Coherence** - Logical flow and organization | ___ | |
| **Writing Quality** - Grammar, style, readability | ___ | |
| **Appropriate Length** - Neither too brief nor verbose | ___ | |
| **Specificity** - Includes concrete details, not vague | ___ | |
| **Relevance** - Focuses on significant contributions | ___ | |
| **Section Average** | **___** | |

**Scoring Guide:**
- 10: Publication-ready, no edits needed
- 8-9: Minor polish needed
- 6-7: Solid draft, some revision required
- 4-5: Usable starting point, significant editing needed
- 1-3: Requires complete rewrite

---

## Dimension 7: Processing Efficiency

| Metric | Value |
|--------|-------|
| Document size (pages) | ___ |
| Document size (KB) | ___ |
| Total processing time | ___ seconds |
| Total API cost | $___.___ |
| Cost per entry | $___.___ |

---

## Dimension 8: Error Analysis

### Critical Errors (require manual correction)
| Error Type | Count | Examples |
|------------|-------|----------|
| Missing entries | ___ | |
| Wrong classification | ___ | |
| Incorrect field values | ___ | |
| Duplicate entries | ___ | |
| Formatting errors | ___ | |

### Minor Errors (acceptable with review)
| Error Type | Count | Examples |
|------------|-------|----------|
| Date format inconsistencies | ___ | |
| Abbreviation variations | ___ | |
| Capitalization issues | ___ | |
| Spacing/punctuation | ___ | |

---

## Summary Scorecard

| Dimension | Weight | Score (1-10) | Weighted Score |
|-----------|--------|:------------:|----------------|
| Entry Extraction | 20% | ___ | ___ |
| Taxonomy Classification | 20% | ___ | ___ |
| Field Extraction | 20% | ___ | ___ |
| PubMed Enrichment | 10% | ___ | ___ |
| Output Quality | 15% | ___ | ___ |
| Research Summary | 10% | ___ | ___ |
| Processing Efficiency | 5% | ___ | ___ |
| **Overall Score** | **100%** | | **___/10.0** |

**Interpretation:**
- 9-10: Excellent - minimal human review needed
- 7-8: Good - minor corrections required
- 5-6: Acceptable - moderate editing needed
- 3-4: Poor - significant rework required
- 1-2: Failed - manual conversion preferable

---

## Usability Assessment

### Time Savings
| Metric | Value |
|--------|-------|
| Estimated manual conversion time | ___ minutes |
| CViche processing time | ___ minutes |
| Manual review/correction time | ___ minutes |
| **Total automated workflow time** | ___ minutes |
| **Net time savings** | **___% reduction** |

### User Acceptance

*Rate each statement from 1-10 (1=strongly disagree, 10=strongly agree)*

| Statement | Score (1-10) |
|-----------|:------------:|
| The output required minimal corrections | ___ |
| I would use this tool again | ___ |
| The tool saved me significant time | ___ |
| I would recommend this to colleagues | ___ |
| The output quality met my expectations | ___ |
| The tool handled my CV's format well | ___ |
| I trust the accuracy of the extracted data | ___ |
| The cost was reasonable for the value provided | ___ |
| **User Acceptance Average** | **___** |

---

## Comparative Analysis (Multi-CV Evaluation)

If evaluating multiple CVs, aggregate results across your sample:

### Score Distribution (1-10 scale)
| Dimension | Min | Max | Mean | Median | Std Dev |
|-----------|:---:|:---:|:----:|:------:|:-------:|
| Entry Extraction | ___ | ___ | ___ | ___ | ___ |
| Taxonomy Classification | ___ | ___ | ___ | ___ | ___ |
| Field Extraction | ___ | ___ | ___ | ___ | ___ |
| PubMed Enrichment | ___ | ___ | ___ | ___ | ___ |
| Output Quality | ___ | ___ | ___ | ___ | ___ |
| Research Summary | ___ | ___ | ___ | ___ | ___ |
| User Acceptance | ___ | ___ | ___ | ___ | ___ |
| **Overall Score** | ___ | ___ | ___ | ___ | ___ |

### Quantitative Metrics
| Metric | Min | Max | Mean | Median |
|--------|:---:|:---:|:----:|:------:|
| Extraction Rate % | ___ | ___ | ___ | ___ |
| Classification Accuracy % | ___ | ___ | ___ | ___ |
| Field Accuracy % | ___ | ___ | ___ | ___ |
| PubMed Enrichment Rate % | ___ | ___ | ___ | ___ |
| Processing Time (seconds) | ___ | ___ | ___ | ___ |
| Cost ($) | ___ | ___ | ___ | ___ |
| Time Savings % | ___ | ___ | ___ | ___ |

### Performance by CV Characteristics
| CV Type | n | Mean Score | Notes |
|---------|:-:|:----------:|-------|
| Short (<5 pages) | ___ | ___ | |
| Medium (5-15 pages) | ___ | ___ | |
| Long (>15 pages) | ___ | ___ | |
| Early career | ___ | ___ | |
| Mid career | ___ | ___ | |
| Senior faculty | ___ | ___ | |
| Clinical focus | ___ | ___ | |
| Research focus | ___ | ___ | |
| International format | ___ | ___ | |

---

## Evaluator Information

| Field | Value |
|-------|-------|
| Evaluator Name | |
| Date | |
| CV Identifier | |
| CViche Version | |
| Notes | |
