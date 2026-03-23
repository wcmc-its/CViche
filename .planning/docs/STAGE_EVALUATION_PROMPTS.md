# CV Pipeline Stage Evaluation Prompts

Use these prompts to have ChatGPT evaluate the output of each pipeline stage. For each evaluation, provide:
1. This prompt
2. The stage output JSON file
3. `taxonomy_reference.md` (for Stages 2b and 3)

---

## Stage 1: Hierarchy Extraction

### Prompt

```
You are evaluating the output of Stage 1 (Hierarchy Extraction) of a CV parsing pipeline. This stage extracts section headers and their hierarchical structure from an academic CV.

**Your Task**: Analyze the JSON output for errors and quality issues, then provide a structured evaluation.

**Input Files Provided**:
1. `_segmented.json` - The Stage 1 output containing extracted hierarchy
2. (Optional) The original CV text or document for reference

**Evaluation Criteria**:

### Error Categories to Check

1. **Missing Sections** (Impact: 8-10)
   - Standard CV sections not detected (Education, Publications, etc.)
   - Example: CV clearly has "Research Experience" but it's missing from hierarchy

2. **False Positive Headers** (Impact: 6-8)
   - Non-headers incorrectly identified as section headers
   - Example: "2019-2023" detected as a header, publication titles as headers

3. **Incorrect Hierarchy Levels** (Impact: 5-7)
   - H1/H2/H3 assignments don't match logical structure
   - Example: "Publications" as H2 under "Education" when it should be H1

4. **Duplicate Headers** (Impact: 4-6)
   - Same section appearing multiple times incorrectly
   - Example: "Education" appears twice at H1 level

5. **Truncated/Malformed Headers** (Impact: 3-5)
   - Partial header text or encoding issues
   - Example: "Publicat..." instead of "Publications"

6. **Parent-Child Mismatches** (Impact: 5-7)
   - Subsections assigned to wrong parent
   - Example: "Peer-Reviewed Articles" under "Teaching" instead of "Publications"

### Output Format

Provide your evaluation in this exact format:

---

## Error Analysis

### Errors Found

| # | Error Type | Severity (1-10) | Description | Example from Output |
|---|------------|-----------------|-------------|---------------------|
| 1 | [type] | [1-10] | [description] | [specific example] |
| ... | ... | ... | ... | ... |

### Error Summary
- **Critical Errors (8-10)**: [count]
- **Major Errors (5-7)**: [count]
- **Minor Errors (1-4)**: [count]
- **Total Errors**: [count]

---

## Quality Assessment

### Strengths
- [List what the extraction did well]

### Weaknesses
- [List systematic issues or patterns of failure]

---

## Scoring Rubric

| Category | Weight | Score (0-100) | Weighted |
|----------|--------|---------------|----------|
| Completeness (all sections found) | 30% | | |
| Accuracy (no false positives) | 25% | | |
| Hierarchy correctness (H1/H2/H3) | 20% | | |
| Header text quality | 15% | | |
| Structure coherence | 10% | | |

**OVERALL SCORE: [X]/100**

---

## Recommendations

1. [Specific actionable recommendation]
2. [...]

---

Now analyze the provided Stage 1 output JSON.
```

---

## Stage 1b: Hierarchy Mapping

### Prompt

```
You are evaluating the output of Stage 1b (Hierarchy Mapping) of a CV parsing pipeline. This stage maps the extracted headers to specific paragraph indices in the Word document.

**Your Task**: Analyze the JSON output for errors and quality issues, then provide a structured evaluation.

**Input Files Provided**:
1. `_hierarchy_mapped.json` - The Stage 1b output containing section boundaries

**Evaluation Criteria**:

### Error Categories to Check

1. **Invalid Index Ranges** (Impact: 9-10)
   - element_idx_start > element_idx_end
   - Negative indices or indices exceeding document length
   - Example: {"element_idx_start": 50, "element_idx_end": 30}

2. **Overlapping Sections** (Impact: 7-9)
   - Multiple sections claiming the same paragraph indices
   - Example: Section A covers 10-50, Section B covers 40-60

3. **Gaps in Coverage** (Impact: 6-8)
   - Paragraph ranges not assigned to any section
   - Example: Paragraphs 25-30 not included in any section boundary

4. **Misaligned Boundaries** (Impact: 5-7)
   - Section boundaries don't match actual content breaks
   - Example: "Publications" section starts at paragraph 45 but first publication is at 47

5. **Missing Section Mappings** (Impact: 7-9)
   - Sections from Stage 1 hierarchy not mapped to indices
   - Example: "Awards" in hierarchy but no corresponding boundary entry

6. **Leaf Section Errors** (Impact: 4-6)
   - has_children flag incorrect
   - Leaf sections with no content (zero-length ranges)

### Output Format

Provide your evaluation in this exact format:

---

## Error Analysis

### Errors Found

| # | Error Type | Severity (1-10) | Description | Example from Output |
|---|------------|-----------------|-------------|---------------------|
| 1 | [type] | [1-10] | [description] | [specific example] |
| ... | ... | ... | ... | ... |

### Error Summary
- **Critical Errors (8-10)**: [count]
- **Major Errors (5-7)**: [count]
- **Minor Errors (1-4)**: [count]
- **Total Errors**: [count]

---

## Quality Assessment

### Strengths
- [List what the mapping did well]

### Weaknesses
- [List systematic issues or patterns of failure]

---

## Scoring Rubric

| Category | Weight | Score (0-100) | Weighted |
|----------|--------|---------------|----------|
| Index validity (no invalid ranges) | 30% | | |
| Coverage (all paragraphs mapped) | 25% | | |
| Boundary accuracy | 20% | | |
| Hierarchy consistency (matches Stage 1) | 15% | | |
| Metadata correctness (has_children, etc.) | 10% | | |

**OVERALL SCORE: [X]/100**

---

## Recommendations

1. [Specific actionable recommendation]
2. [...]

---

Now analyze the provided Stage 1b output JSON.
```

---

## Stage 2a: Entry Delimiter Detection

### Prompt

```
You are evaluating the output of Stage 2a (Entry Delimiter Detection) of a CV parsing pipeline. This stage identifies the start and end paragraph indices for individual CV entries (publications, positions, awards, etc.) within each section.

**Your Task**: Analyze the JSON output for errors and quality issues, then provide a structured evaluation.

**Input Files Provided**:
1. `_delimiters.json` - The Stage 2a output containing entry boundaries

**Evaluation Criteria**:

### Error Categories to Check

1. **Merged Entries** (Impact: 8-10)
   - Multiple distinct entries combined into one delimiter range
   - Example: Two publications merged into one entry (indices 45-52 should be 45-48 and 49-52)

2. **Split Entries** (Impact: 7-9)
   - Single entry incorrectly split into multiple delimiters
   - Example: One grant split into separate entries for title, amount, and dates

3. **Overlapping Delimiters** (Impact: 8-10)
   - Entry boundaries overlap with each other
   - Example: Entry 1 covers 10-15, Entry 2 covers 13-18

4. **Nested/Duplicate Delimiters** (Impact: 6-8)
   - Same content appears in multiple delimiter entries
   - Example: Both 19-25 AND 19-19 exist for the same grant

5. **Missing Entries** (Impact: 8-10)
   - Visible entries in the section not detected
   - Example: Section has 10 publications but only 7 delimiters found

6. **Invalid Index Ranges** (Impact: 9-10)
   - start_idx > end_idx, or indices outside section bounds
   - Example: {"element_idx_start": 60, "element_idx_end": 55}

7. **Incorrect Element Types** (Impact: 3-5)
   - Wrong element_type (paragraph vs table vs table_row)
   - Example: Table content marked as "paragraph"

8. **Low Confidence Without Justification** (Impact: 2-4)
   - Entries with confidence < 0.8 but no reasoning provided
   - Example: {"confidence": 0.6, "reasoning": ""}

### Output Format

Provide your evaluation in this exact format:

---

## Error Analysis

### Errors Found

| # | Error Type | Severity (1-10) | Description | Example from Output |
|---|------------|-----------------|-------------|---------------------|
| 1 | [type] | [1-10] | [description] | [specific example] |
| ... | ... | ... | ... | ... |

### Error Summary
- **Critical Errors (8-10)**: [count]
- **Major Errors (5-7)**: [count]
- **Minor Errors (1-4)**: [count]
- **Total Errors**: [count]

---

## Quality Assessment

### Strengths
- [List what the delimiter detection did well]

### Weaknesses
- [List systematic issues or patterns of failure]

### Section-by-Section Analysis

| Section | Entries Found | Issues |
|---------|---------------|--------|
| [section name] | [count] | [brief issue description or "None"] |
| ... | ... | ... |

---

## Scoring Rubric

| Category | Weight | Score (0-100) | Weighted |
|----------|--------|---------------|----------|
| Entry detection completeness | 30% | | |
| Boundary accuracy (no merges/splits) | 30% | | |
| No overlaps or duplicates | 20% | | |
| Index validity | 10% | | |
| Confidence calibration | 10% | | |

**OVERALL SCORE: [X]/100**

---

## Recommendations

1. [Specific actionable recommendation]
2. [...]

---

Now analyze the provided Stage 2a output JSON.
```

---

## Stage 2b: Entry Extraction

### Prompt

```
You are evaluating the output of Stage 2b (Entry Extraction) of a CV parsing pipeline. This stage extracts the actual text content for each entry using the delimiter indices from Stage 2a.

**Your Task**: Analyze the JSON output for errors and quality issues, then provide a structured evaluation.

**Input Files Provided**:
1. `_entries.json` - The Stage 2b output containing extracted entry text
2. `taxonomy_reference.md` - For understanding expected entry types by section

**Evaluation Criteria**:

### Error Categories to Check

1. **Empty/Missing Text** (Impact: 9-10)
   - Entry has empty string or only whitespace for text field
   - Example: {"text": "", "hierarchy": ["Publications"]}

2. **Truncated Text** (Impact: 7-9)
   - Entry text appears cut off mid-sentence or mid-word
   - Example: Text ends with "...published in the Journal of"

3. **Merged Text** (Impact: 7-9)
   - Multiple entries' text concatenated into one entry
   - Example: Two publication citations in one text field

4. **Garbage/Noise Text** (Impact: 6-8)
   - Non-entry content (page numbers, headers, footers)
   - Example: {"text": "Page 5 of 12"}

5. **Encoding Issues** (Impact: 5-7)
   - Special characters corrupted or displaying incorrectly
   - Example: "Müller" appearing as "MÃ¼ller"

6. **Inconsistent Formatting** (Impact: 3-5)
   - Tab separators, extra whitespace, inconsistent delimiters
   - Example: Some entries use tabs, others use pipes

7. **Missing Hierarchy** (Impact: 6-8)
   - Entry has empty or incorrect hierarchy array
   - Example: {"hierarchy": [], "text": "PhD, Harvard University"}

8. **Duplicate Entries** (Impact: 6-8)
   - Same text appearing in multiple entry objects
   - Example: Identical publication text with different entry indices

### Output Format

Provide your evaluation in this exact format:

---

## Error Analysis

### Errors Found

| # | Error Type | Severity (1-10) | Description | Example from Output |
|---|------------|-----------------|-------------|---------------------|
| 1 | [type] | [1-10] | [description] | [specific example] |
| ... | ... | ... | ... | ... |

### Error Summary
- **Critical Errors (8-10)**: [count]
- **Major Errors (5-7)**: [count]
- **Minor Errors (1-4)**: [count]
- **Total Errors**: [count]

---

## Quality Assessment

### Strengths
- [List what the extraction did well]

### Weaknesses
- [List systematic issues or patterns of failure]

### Content Quality by Section

| Section | Entry Count | Avg Text Length | Issues |
|---------|-------------|-----------------|--------|
| [section name] | [count] | [chars] | [brief description or "None"] |
| ... | ... | ... | ... |

---

## Scoring Rubric

| Category | Weight | Score (0-100) | Weighted |
|----------|--------|---------------|----------|
| Text completeness (no empty/truncated) | 35% | | |
| Text accuracy (no merges/garbage) | 30% | | |
| Hierarchy preservation | 15% | | |
| Formatting consistency | 10% | | |
| No duplicates | 10% | | |

**OVERALL SCORE: [X]/100**

---

## Recommendations

1. [Specific actionable recommendation]
2. [...]

---

Now analyze the provided Stage 2b output JSON.
```

---

## Stage 3: Taxonomy Mapping

### Prompt

```
You are evaluating the output of Stage 3 (Taxonomy Mapping) of a CV parsing pipeline. This stage classifies each CV entry to the WCM taxonomy codes (A-T with subcategories).

**Your Task**: Analyze the JSON output for errors and quality issues, then provide a structured evaluation.

**Input Files Provided**:
1. `_mapped.json` - The Stage 3 output containing taxonomy classifications
2. `taxonomy_reference.md` - The complete taxonomy code definitions

**IMPORTANT**: Use taxonomy_reference.md as the authoritative source for:
- Valid taxonomy codes (A, B1, B2, C, D1-D3, E, F1-F2, G, H, I, J, K1-K5, L1-L3, M1-M4, N1-N4, O, P, Q1-Q4, R, S1-S9, T)
- Code definitions and what content belongs in each
- Common confusions to watch for (H vs I for fellowships, S1 vs S8 for abstracts, etc.)

**Evaluation Criteria**:

### Error Categories to Check

1. **Incorrect Parent Code** (Impact: 9-10)
   - Entry assigned to completely wrong top-level category
   - Example: Publication classified as "D" (Employment) instead of "S" (Bibliography)

2. **Incorrect Child Code** (Impact: 7-9)
   - Right parent but wrong subcategory
   - Example: Book chapter (S4) classified as journal article (S1)

3. **Invalid Taxonomy Code** (Impact: 10)
   - Code doesn't exist in the taxonomy
   - Example: "S10", "B3", "X1" (not valid codes)

4. **Common Confusion Errors** (Impact: 6-8)
   - Errors matching known confusion patterns from taxonomy_reference.md
   - Example: Fellowship honor (H) vs society membership (I), Abstract (S8) vs article (S1)

5. **Low Confidence Without Flag** (Impact: 4-6)
   - Entry has confidence < 0.7 but no validation_flags
   - Example: {"confidence": 0.55, "validation_flags": []}

6. **Inconsistent Classification** (Impact: 6-8)
   - Similar entries classified differently
   - Example: Two peer-reviewed articles, one S1 and one S2

7. **Missing Classification** (Impact: 9-10)
   - Entry has no taxonomy_code or null/empty code
   - Example: {"taxonomy_code": null, "text": "PhD, MIT, 2019"}

8. **Hierarchy-Code Mismatch** (Impact: 5-7)
   - Taxonomy code contradicts section hierarchy
   - Example: Entry under "Publications" section classified as "H" (Honors)

9. **Reasoning Quality Issues** (Impact: 2-4)
   - Empty, generic, or incorrect reasoning
   - Example: {"reasoning": "Classified based on content"} (not specific)

### Output Format

Provide your evaluation in this exact format:

---

## Error Analysis

### Errors Found

| # | Error Type | Severity (1-10) | Entry Text (truncated) | Assigned Code | Suggested Code | Reasoning |
|---|------------|-----------------|------------------------|---------------|----------------|-----------|
| 1 | [type] | [1-10] | [first 50 chars...] | [code] | [correct code] | [why it's wrong] |
| ... | ... | ... | ... | ... | ... | ... |

### Error Summary
- **Critical Errors (8-10)**: [count]
- **Major Errors (5-7)**: [count]
- **Minor Errors (1-4)**: [count]
- **Total Errors**: [count]
- **Error Rate**: [errors / total_entries]%

---

## Classification Distribution

| Taxonomy Code | Count | Expected Range | Status |
|---------------|-------|----------------|--------|
| A | [n] | [typical for CVs] | [OK/High/Low] |
| B1 | [n] | | |
| ... | ... | ... | ... |

### Distribution Anomalies
- [List any codes with unexpectedly high or low counts]

---

## Confusion Pattern Analysis

Check for these known confusion patterns from taxonomy_reference.md:

| Confusion Pair | Instances Found | Correctly Resolved? |
|----------------|-----------------|---------------------|
| H ↔ I (honors vs memberships) | [count] | [Yes/No/Partial] |
| S1 ↔ S8 (articles vs abstracts) | [count] | [Yes/No/Partial] |
| C ↔ D (postdoc vs employment) | [count] | [Yes/No/Partial] |
| M2 ↔ H (grants vs awards) | [count] | [Yes/No/Partial] |
| K ↔ R (teaching vs presentations) | [count] | [Yes/No/Partial] |

---

## Validation Flags Analysis

| Flag Type | Count | Appropriately Raised? |
|-----------|-------|----------------------|
| [flag_type] | [n] | [Yes/No/Excessive/Missing] |
| ... | ... | ... |

---

## Scoring Rubric

| Category | Weight | Score (0-100) | Weighted |
|----------|--------|---------------|----------|
| Parent code accuracy | 30% | | |
| Child code accuracy | 25% | | |
| Consistency (similar entries same code) | 15% | | |
| Confidence calibration | 10% | | |
| Validation flag appropriateness | 10% | | |
| Reasoning quality | 10% | | |

**OVERALL SCORE: [X]/100**

---

## Recommendations

1. [Specific actionable recommendation]
2. [...]

---

## Sample Corrections

Provide 3-5 specific corrections for the most impactful errors:

1. **Entry**: "[text excerpt]"
   - **Current**: [code] ([label])
   - **Should be**: [code] ([label])
   - **Reason**: [explanation]

---

Now analyze the provided Stage 3 output JSON using taxonomy_reference.md as the authoritative code reference.
```

---

## Full Pipeline Summary Evaluation

### Prompt

```
You are evaluating the complete output of a CV parsing pipeline that has run all 5 stages. You have already evaluated each stage individually. Now provide an overall pipeline assessment.

**Your Task**: Synthesize the individual stage evaluations into an overall pipeline quality score and analysis.

**Input**: Your previous evaluations for:
- Stage 1: Hierarchy Extraction
- Stage 1b: Hierarchy Mapping
- Stage 2a: Entry Delimiter Detection
- Stage 2b: Entry Extraction
- Stage 3: Taxonomy Mapping

**Evaluation Framework**:

### Error Propagation Analysis

Identify how errors in earlier stages affected later stages:

| Source Stage | Error | Downstream Impact | Stages Affected |
|--------------|-------|-------------------|-----------------|
| [stage] | [error description] | [how it propagated] | [which stages] |

### Stage Scores Summary

| Stage | Score | Weight | Weighted Score |
|-------|-------|--------|----------------|
| Stage 1 (Hierarchy) | /100 | 15% | |
| Stage 1b (Mapping) | /100 | 10% | |
| Stage 2a (Delimiters) | /100 | 20% | |
| Stage 2b (Extraction) | /100 | 20% | |
| Stage 3 (Taxonomy) | /100 | 35% | |
| **PIPELINE TOTAL** | | 100% | **/100** |

### Quality Gates

| Gate | Threshold | Actual | Pass/Fail |
|------|-----------|--------|-----------|
| No critical errors in Stage 1 | 0 | [count] | |
| Entry detection rate | >90% | [%] | |
| Taxonomy accuracy | >85% | [%] | |
| No invalid taxonomy codes | 0 | [count] | |

### Overall Assessment

**Pipeline Health**: [Excellent / Good / Acceptable / Needs Improvement / Critical Issues]

**Confidence in Output**: [High / Medium / Low]
- Can the `_mapped.json` be used as-is? [Yes / With Review / No]
- Estimated manual corrections needed: [count or %]

### Top 3 Issues to Address

1. **[Issue]**: [Description and recommended fix]
2. **[Issue]**: [Description and recommended fix]
3. **[Issue]**: [Description and recommended fix]

### Strengths

1. [What the pipeline did well]
2. [...]

---

**FINAL PIPELINE SCORE: [X]/100**

**Recommendation**: [Ship as-is / Review flagged entries / Re-run specific stages / Manual review required]
```

---

## Usage Instructions

1. **Run the pipeline** on a CV:
   ```bash
   python3 run_full_pipeline.py 'path/to/cv.docx' 'cv_uid'
   ```

2. **After each stage**, provide ChatGPT with:
   - The appropriate prompt from above
   - The stage output JSON file
   - `taxonomy_reference.md` (for Stages 2b and 3)

3. **Collect scores** from each evaluation

4. **After all stages**, use the Full Pipeline Summary prompt to get an overall assessment

---

## Score Interpretation Guide

| Score Range | Interpretation | Action |
|-------------|----------------|--------|
| 90-100 | Excellent | Output ready for production use |
| 80-89 | Good | Minor review recommended |
| 70-79 | Acceptable | Review flagged entries before use |
| 60-69 | Needs Improvement | Significant review required |
| <60 | Critical Issues | Re-run pipeline or manual processing needed |
