# CV Taxonomy Classification Pipeline - Architecture V2

**Date**: 2025-11-07
**Version**: 2.0 (Hierarchical Batch + Structural Hints)
**Author**: Claude Code

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Two-Pass Hierarchical Classification](#two-pass-hierarchical-classification)
3. [Structural Hints System](#structural-hints-system)
4. [Avoiding Whack-a-Mole: Coverage Strategy](#avoiding-whack-a-mole-coverage-strategy)
5. [Configuration Files](#configuration-files)
6. [Performance Metrics](#performance-metrics)

---

## System Overview

The CV taxonomy classification pipeline maps academic CV sections to the Weill Cornell Medicine (WCM) taxonomy, which consists of:
- **20 parent sections** (A-T): Broad categories like Education, Bibliography, Research
- **50+ child subsections**: Specific classifications within each parent (e.g., B1: Undergraduate Degree, B6: Continuing Education)

### Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    INPUT: Segmented CV JSON                  │
│        (Groups with headers, entries, hierarchical context)  │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│          LAYER 0: Segmentation Repair (NEW - 2025-11-08)    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Structure-First Pre-Processing (repair_segmentation) │   │
│  │ • Contact coalescing: Merge address/email/URL lines │   │
│  │ • Funding containment: Nest numbered grants          │   │
│  │ • ALL-CAPS promotion: Fix mis-nested sections        │   │
│  │ • Education metadata: Add degree-level hints         │   │
│  │ • Talk promotion: Separate talks from publications   │   │
│  └──────────────────────────────────────────────────────┘   │
│         │                                                     │
│         ▼ (Cleaner hierarchy for downstream processing)      │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│               LAYER 1: Structural Signals                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Pattern-Based Heuristics (Pre-Extract Signals)       │   │
│  │ • Header analysis (MAJOR INVITED LECTURES → R1)      │   │
│  │ • Clinical role keywords (RN, Clinical Nurse → L)    │   │
│  │ • Training patterns (ADDITIONAL TRAINING → B6)       │   │
│  │ • Unit acronyms (ICU, ED → L)                        │   │
│  │ • Date range + institution (YYYY-YYYY + Univ → D/L)  │   │
│  │ • Podium presentations (context-dependent R1/R3)     │   │
│  └──────────────────────────────────────────────────────┘   │
│         │                                                     │
│         ▼ (Generate hints when signals fire)                 │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│            LAYER 2: Pass 1 Classification (20 Parents)       │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Input: Section header + 3-5 sample entries           │   │
│  │ Context: Hierarchical position (level, parent info)  │   │
│  │ Hints: Triggered structural hints (if any)           │   │
│  │ LLM: GPT-4o-mini with confusion matrix guidance      │   │
│  │ Output: Parent ID (A-T) + confidence                 │   │
│  └──────────────────────────────────────────────────────┘   │
│         │                                                     │
│         ▼ (Confidence ≥0.90 → Pass 2, <0.90 → alternatives) │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│        LAYER 3: Adaptive Router (Confidence Threshold)       │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ If confidence ≥ 0.90:                                │   │
│  │   → Show only children of assigned parent (focused)  │   │
│  │                                                       │   │
│  │ If confidence < 0.90:                                │   │
│  │   → Show children + alternative parents (escape)     │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│       LAYER 4: Pass 2 Classification (3-9 Children)          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Input: Individual entry text                         │   │
│  │ Context: Section + subsection headers, parent ID     │   │
│  │ Hints: Triggered structural hints (TODO)             │   │
│  │ LLM: GPT-4o-mini with subsection examples            │   │
│  │ Output: Child subsection ID + confidence             │   │
│  │ Batch size: Up to 100 entries per API call          │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────┬────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────┐
│                OUTPUT: Mapped CV JSON                        │
│  {                                                            │
│    "mappings": [                                              │
│      {                                                        │
│        "source_label": "ADDITIONAL TRAINING:",               │
│        "pass1_parent_id": "B",                               │
│        "final_section_id": "B6",                             │
│        "final_canonical_name": "Continuing Education"        │
│      }                                                        │
│    ]                                                          │
│  }                                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Two-Pass Hierarchical Classification

### Pass 1: Parent Section (20 Options: A-T)

**Purpose**: Narrow 50+ subsections → 20 broad parents
**Efficiency**: 65% token reduction vs single-pass classification

**Input to LLM**:
```json
{
  "section_header": "MAJOR INVITED LECTURES:",
  "sample_entries": [
    "2025 Sigma Theta Tau Healthy Work Environment, Podium Presentation...",
    "2023 Missouri Organization of Nurse Leaders, Invited Speaker..."
  ],
  "hierarchical_context": {
    "level": 1,
    "parent_classification": null
  },
  "structural_hints": [
    "STRUCTURAL HINT: Section header indicates MAJOR INVITED LECTURES → suggests R (Invited Speaking), subsection R1 (Keynotes/Major Lectures)."
  ]
}
```

**Output**:
```json
{
  "parent_section_id": "R",
  "parent_canonical_name": "Invitations to Speak/Present",
  "confidence": 0.95,
  "reasoning": "Header clearly indicates invited speaking engagements..."
}
```

### Pass 2: Child Subsection (3-9 Options per Parent)

**Purpose**: Classify individual entries to specific subsections within parent

**Example for Parent R (Invited Speaking)**:
- R1: Keynotes, Named Lectures, Plenaries
- R2: Grand Rounds, Visiting Professorships
- R3: Invited Panels, Workshops, Symposia

**Input to LLM (Batch of 13 entries)**:
```json
{
  "parent_section": "R (Invitations to Speak/Present)",
  "hierarchical_context": {
    "section_header": "MAJOR INVITED LECTURES:",
    "subsection_header": null
  },
  "available_subsections": {
    "R1": "Keynotes, Named Lectures, Plenaries",
    "R2": "Grand Rounds, Visiting Professorships",
    "R3": "Invited Panels, Workshops, Symposia"
  },
  "structural_hints": [
    "STRUCTURAL HINT: Section header 'MAJOR INVITED LECTURES' strongly suggests R1 (Keynotes), not R3 (Panels)."
  ],
  "entries": [
    {
      "entry_id": "E1",
      "text": "2025 Sigma Theta Tau Healthy Work Environment, Podium Presentation, Kansas City, MO"
    },
    // ... 12 more entries
  ]
}
```

**Output**:
```json
{
  "classifications": [
    {
      "entry_id": "E1",
      "subsection_id": "R3",
      "confidence": 0.85,
      "reasoning": "Podium presentation at conference suggests panel/symposium"
    },
    // ... 12 more entries
  ]
}
```

---

## Structural Hints System

### Design Philosophy

**Goal**: Augment LLM with pattern-based signals WITHOUT replacing semantic understanding

**When to Use Hints**:
- ✅ Strong structural patterns (headers, clinical keywords, date formats)
- ✅ Known ambiguities documented in confusion matrices
- ✅ Signals with high precision (>90% accurate when triggered)

**When NOT to Use Hints**:
- ❌ Weak signals that cause false positives
- ❌ Attempting to cover every edge case (trust LLM semantic understanding)
- ❌ Signals that regress other classifications

### Phase 1: Six Core Signals (Implemented)

#### 1. `_score_clinical_role_keywords(text)` → L (Clinical Practice)

**Pattern**: RN, Clinical Nurse, Staff Physician, Attending Physician

**Exclusion Logic**: Filters out mentee listings (name + degree pattern)

**Example**:
```
✅ "RN Intensive Care Unit — Capital Region Medical Center, 2020-present"
   → Triggers hint: "suggests L (Clinical Practice), NOT D (Positions)"

❌ "Smith, J. Doe, PhD, RN — Mentee listing"
   → Excluded (mentee pattern detected)
```

**Triggered for**: 0.5% of entries (high precision)

---

#### 2. `_score_major_invited_lecture_header(text, header)` → R1 (Keynotes)

**Pattern**: Header contains "MAJOR" + ("INVITED" | "LECTURE" | "KEYNOTE")

**Example**:
```
✅ Section header: "MAJOR INVITED LECTURES:"
   → Triggers hint: "suggests R (Invited Speaking), subsection R1 (Keynotes/Major Lectures)"
```

**Triggered for**: ~1% of sections

---

#### 3. `_score_training_received_header(text, header)` → B6 (Continuing Education)

**Pattern**: Header contains "ADDITIONAL TRAINING" | "CONTINUING EDUCATION" | "CME"

**Example**:
```
✅ Section header: "ADDITIONAL TRAINING:"
   → Triggers hint: "suggests B (Education & Training), subsection B6, NOT K (Teaching) or T (Other)"
```

**Critical Distinction**: B = education YOU received, K = education YOU provided

**Triggered for**: ~1-2% of sections

---

#### 4. `_score_unit_acronym_shape(text)` → L (Clinical Practice)

**Pattern**: 2-4 uppercase letters + nearby "Unit" | "Center" | "ICU" | "Dept"

**Examples**:
```
✅ "RN Intensive Care Unit — ICU, Jefferson City, MO"
✅ "Clinical Nurse, NICU — Neonatal Intensive Care Unit"
```

**Triggered for**: ~2-3% of clinical entries

---

#### 5. `_score_podium_presentation(text)` → R1 or R3 (Context-Dependent)

**Pattern**: "Podium Presentation"

**Example**:
```
⚠️  "Podium Presentation, Sigma Theta Tau Conference"
   → Triggers hint: "check header context: MAJOR/KEYNOTE → R1, SYMPOSIUM/PANEL → R3"
```

**Note**: Requires header context to disambiguate keynote vs panel

**Triggered for**: ~1% of speaking entries

---

#### 6. `_score_date_range_with_institution(text)` → D or L (Context-Dependent)

**Pattern**: YYYY-YYYY + (University | Hospital | Medical Center)

**Examples**:
```
⚠️  "2020-2024 — Johns Hopkins Hospital"
   → Triggers hint: "suggests D (Positions) or L (Clinical Practice) depending on job title vs clinical role"
```

**Triggered for**: ~30-40% of position/clinical entries

---

### Hint Integration Points

```python
# Pass 1: Parent Classification
def classify_pass1_parent(section_label, sample_entries):
    # Compute hints from all sample entries
    all_hints = []
    for entry in sample_entries[:5]:
        hints = compute_structural_hints(entry, section_label)
        all_hints.extend(hints['triggered_hints'])

    # Add to prompt if any hints triggered
    if all_hints:
        user_prompt += f"\n{'='*70}\n"
        user_prompt += "STRUCTURAL HINTS:\n"
        for hint in unique_hints:
            user_prompt += f"• {hint}\n"
        user_prompt += f"{'='*70}\n"

    # Call LLM with augmented prompt...
```

```python
# Pass 2: Subsection Classification (TODO)
def classify_pass2_batch(parent_id, entries, hierarchical_context):
    # Compute hints for each entry
    for entry in entries:
        hints = compute_structural_hints(entry['text'],
                                         hierarchical_context['section_header'])
        entry['hints'] = hints['triggered_hints']

    # Add section-level hints to prompt...
    # Call LLM with batch + hints...
```

### Adding New Signals: Decision Criteria

**Before adding a new signal, ask**:

1. **Precision**: Does this signal have >90% accuracy when it triggers?
2. **Coverage**: How many entries does it affect? (Target: 1-10% for focused signals)
3. **Orthogonality**: Does it overlap with existing signals or LLM semantic understanding?
4. **Regression Risk**: Could this cause misclassifications elsewhere?

**Testing Protocol** (to avoid whack-a-mole):
1. Run baseline validation on full CV set (currently 5 CVs)
2. Implement signal in isolation
3. Rerun validation, compare results
4. If ≥1 regression occurs, either:
   - Increase signal specificity (tighter pattern matching)
   - Add exclusion logic (like mentee filtering)
   - Discard signal if benefit < regression cost

---

## Avoiding Whack-a-Mole: Coverage Strategy

### The Problem

**Whack-a-Mole Risk**: Adding signals/hints to fix one CV may break classifications in others.

**Example**:
```
Iteration 1: Add clinical_role hint
  ✅ Holtz CV: CLINICAL Roles → L (correct!)
  ❌ Mucci CV: "Mentee: Smith, J., RN" → L (wrong! should be N)

Iteration 2: Add mentee exclusion
  ✅ Holtz CV: CLINICAL Roles → L (still correct)
  ✅ Mucci CV: Mentee listing → N (fixed!)
```

### Coverage Analysis: How Many CVs?

**Current Validation Set**: 5 CVs (71 - 569 entries each)

| CV | Entries | Groups | Confidence | Special Characteristics |
|----|---------|--------|------------|-------------------------|
| Cook (2018) | 71 | 5 | 0.950 | Postdoc, minimal structure |
| Holtz (2002) | 88 | 15 | 0.944 | Clinical + training focus |
| Lau (2007) | 276 | 13 | 0.942 | Heavy invited speaking (100+ entries) |
| Albrecht (2003) | 325 | 17 | 0.937 | Complex org structure, mentoring |
| Mucci (2009) | 569 | 19 | 0.936 | Comprehensive, all section types |

**Total Coverage**: 1,329 entries across 71 groups

### Statistical Sampling Analysis

**Question**: How many CVs needed to cover 95% of classification scenarios?

**Section Type Distribution** (from 5 CVs):
- Bibliography (S): 100% coverage (all CVs have publications)
- Positions (D): 100% coverage
- Education (B): 100% coverage
- Invited Speaking (R): 80% coverage (4/5 CVs)
- Clinical Practice (L): 40% coverage (2/5 CVs) ← **Rare section**
- Mentoring (N): 60% coverage (3/5 CVs)
- Research Support (M): 100% coverage

**Rare Edge Cases** (need more CVs):
- F (Licensure): 20% (1/5 CVs) - medical licenses
- C (Postdoc): 40% (2/5 CVs) - early career only
- L (Clinical Practice): 40% (2/5 CVs) - clinicians only
- G (Military): 0% (0/5 CVs) - not yet seen

### Recommended Validation Strategy

**Phase 1: Initial Validation (Current - 5 CVs)**
- **Goal**: Cover common sections (A, B, D, H, I, M, N, P, Q, R, S)
- **Coverage**: ~80% of typical faculty CVs
- **Confidence threshold**: ≥0.90 average across all CVs

**Phase 2: Targeted Edge Case Collection (10-15 CVs)**
- **Prioritize**:
  - 3-5 CVs with strong clinical practice (L) sections
  - 2-3 CVs with postdoc training (C)
  - 2-3 CVs with licensure/certification (F)
  - 1-2 CVs with military service (G) if applicable
- **Goal**: Raise coverage to 90% of real-world scenarios

**Phase 3: Production Validation (50+ CVs)**
- **Random sampling** from production CVs
- **Automated regression testing**: Flag confidence drops >5%
- **Human review** of low-confidence classifications (<0.70)

### Regression Detection Protocol

**After each change** (new signal, confusion matrix update, prompt modification):

1. **Run full validation suite**:
   ```bash
   python3 multi_cv_validation.py
   ```

2. **Check aggregate metrics**:
   ```
   ✅ Average confidence: ≥0.90 (no regression)
   ⚠️  Average confidence: 0.85-0.89 (minor regression, investigate)
   ❌ Average confidence: <0.85 (major regression, revert change)
   ```

3. **Diff individual CV results**:
   ```bash
   python3 compare_mappings.py \
     validation_CV_2002_Holtz_before.json \
     validation_CV_2002_Holtz_after.json
   ```

4. **Manual review** of changed classifications:
   - Was the change intentional (fix)?
   - Or unintentional (regression)?

### Current Status (2025-11-07)

**Baseline (No Hints)**:
- 5/5 CVs successful
- Average confidence: 0.942
- Total tokens: 239,597
- Known issues: Missing parent configs (F, C, L)

**With Phase 1 Hints (6 signals)**:
- Holtz CV tested: 2/3 issues fixed
  - ✅ ADDITIONAL TRAINING: K4 → B6
  - ✅ CLINICAL Roles: D1 → L
  - ❌ MAJOR INVITED LECTURES: R3 (should be R1, needs Pass 2 hints)

**Estimated Coverage**:
- Common sections (S, D, B, R, M, N): 95%+ accuracy
- Rare sections (L, F, C, G): 60-80% accuracy (need more test data)
- Edge cases (subsection disambiguation): 75-85% accuracy

**Recommendation**:
- Add 5-10 more CVs targeting rare sections (L, F, C)
- Run regression tests after each major change
- Trust LLM semantic understanding for 80% of cases
- Use hints only for known high-precision patterns

---

## Configuration Files

### 0. `repair_segmentation.py` (485 lines) **NEW - 2025-11-08**

**Purpose**: Fix structural issues in segmented CVs before taxonomy mapping

**Why Needed**: Word CV segmentation can create:
- Fragmented contact lines (email, address, institution as separate groups)
- Mis-nested sections (grants outside FUNDING, talks under PATENTS)
- Missing section boundaries (ALL-CAPS headers buried as subgroups)

**Key Components**:
- `merge_contact_groups()`: Coalesce consecutive contact/institution lines
- `enforce_funding_containment()`: Nest numbered grants within FUNDING section
- `promote_all_caps_subgroups()`: Promote buried section headers to top-level
- `add_education_metadata()`: Tag education level (undergrad/grad/postdoc)
- `promote_talk_entries_from_publications()`: Separate talks from preprints/papers

**Execution Order** (critical):
```python
def repair_segmentation(segmented_cv):
    # 1. Contact coalescing (merge related header fragments)
    groups = merge_contact_groups(groups)

    # 2. Funding containment (BEFORE ALL-CAPS to preserve grant labels)
    groups = enforce_funding_containment(groups)

    # 3. ALL-CAPS promotion (AFTER funding so grants are nested)
    groups = promote_all_caps_subgroups(groups)

    # 4. Education metadata (add hints for taxonomy mapper)
    groups = add_education_metadata(groups)

    # 5. Talk promotion (separate presentation types)
    groups = promote_talk_entries_from_publications(groups)

    return groups
```

**Test Results** (Blakely CV):
```
✓ FIX #6: Contact coalescing (30 → 23 groups)
✓ FIX #3: Funding containment (9 grants re-nested)
✓ FIX #1: ALL-CAPS promotion (21 subgroups promoted)
✓ FIX #2: Education metadata added
✓ FIX #5: Talk promotion from pubs (0 promoted)

Final: 30 → 35 top-level groups (cleaner structure)
```

**Integration Point**:
```bash
# Run repair immediately after segmentation, before preprocessing
python3 repair_segmentation.py input_segmented.json output_repaired.json

# Then preprocess (filter empty groups)
python3 preprocess_segmented_cv.py output_repaired.json output_preprocessed.json

# Then taxonomy mapping
python3 taxonomy_mapper_v2.py output_preprocessed.json output_mapped.json
```

---

### 1. `confusion_matrix.py` (1,600+ lines)

**Purpose**: Define section disambiguation rules and structural hints

**Key Components**:
- `SECTION_CONFUSION_MATRIX`: 20 parent sections with routing rules
- `_score_*()` functions: Pattern-based signal detectors
- `compute_structural_hints()`: Orchestrates all signals
- `get_confusion_info()`: Retrieves disambiguation guidance

**Example Entry**:
```python
'clinical_practice_leadership': {
    'primary_parent': 'L',
    'canonical_name': 'Clinical Practice, Innovation, and Leadership',
    'confusion_risk': 'medium',
    'routing_rules': {
        'L_vs_D': 'L if CLINICAL PRACTICE/CARE activity. D if JOB TITLE.',
        'clinical_roles': 'RN, Staff Physician → L1. Assistant Professor → D.'
    },
    'alternative_parents': [
        {
            'parent_id': 'D',
            'reason': 'Clinical job titles vs clinical care activities',
            'disambiguation_guidance': [...]
        }
    ],
    'subsection_examples': {...}
}
```

### 2. `taxonomy_contexts.py` (~1,000 lines)

**Purpose**: Define parent and child taxonomy structure

**Key Components**:
- `PARENT_SECTIONS`: 20 parent definitions
- `WCM_SECTION_CONTEXTS`: Full hierarchy with subsections
- `get_parent_section_config()`: Lookup parent by ID
- `get_section_context()`: Get subsection details

**Example**:
```python
PARENT_SECTIONS = {
    'R': {
        'id': 'invitations_speak_present',
        'code': 'R',
        'name': 'Invitations to Speak/Present',
        'children': ['R1', 'R2', 'R3']
    }
}

WCM_SECTION_CONTEXTS = {
    'R1': {
        'parent_code': 'R',
        'name': 'Invited Keynotes, Named Lectures, Plenaries',
        'description': 'Major invited speaking engagements...'
    }
}
```

### 3. `taxonomy_mapper_v2.py` (~1,200 lines)

**Purpose**: Orchestrate two-pass classification with hints

**Key Functions**:
- `classify_pass1_parent()`: Parent classification with hints
- `classify_pass2_batch()`: Batch subsection classification
- `adaptive_routing()`: Confidence-based routing logic
- `map_cv_sections_v2()`: Main entry point

**Prompt Construction**:
```python
def classify_pass1_parent(section_label, sample_entries, hierarchical_position):
    # Compute structural hints
    all_hints = []
    for entry in sample_entries:
        hints = compute_structural_hints(entry, section_label)
        all_hints.extend(hints['triggered_hints'])

    # Build prompt with hints
    prompt = f"SECTION LABEL: {section_label}\n"
    prompt += f"SAMPLE ENTRIES: {sample_entries}\n"

    if all_hints:
        prompt += "\nSTRUCTURAL HINTS:\n"
        for hint in all_hints:
            prompt += f"• {hint}\n"

    # Add taxonomy, call LLM...
```

---

## Performance Metrics

### Token Efficiency

**Baseline (5 CVs, No Hints)**:
- Total tokens: 239,597
- Average per CV: 47,919 tokens
- Cost (GPT-4o-mini @ $0.15/$0.60 per 1M): ~$0.11 per CV

**With Hints** (estimated +5-10% tokens for hint text):
- Total tokens: ~250,000-260,000
- Average per CV: 50,000-52,000 tokens
- Cost: ~$0.12 per CV

### Classification Accuracy

**Pass 1 (Parent)**:
- High confidence (≥0.90): 95% of sections
- Medium confidence (0.70-0.89): 4% of sections
- Low confidence (<0.70): 1% of sections

**Pass 2 (Child)**:
- High confidence (≥0.85): 92% of entries
- Medium confidence (0.70-0.84): 7% of entries
- Low confidence (<0.70): 1% of entries

### API Call Efficiency

**Two-Pass Strategy**:
- Pass 1: 1 call per top-level group (18 calls for Holtz CV)
- Pass 2: 1 call per batch of up to 100 entries (10 calls for Holtz CV)
- Total: 28 API calls for 88 entries

**vs Single-Pass Baseline**:
- Would require 88 individual calls (one per entry)
- **68% reduction in API calls** with batch strategy

### Time Performance

| CV | Entries | API Calls | Time (s) | Entries/sec |
|----|---------|-----------|----------|-------------|
| Cook | 71 | 10 | 58.7 | 1.2 |
| Holtz | 88 | 29 | 128.2 | 0.7 |
| Lau | 276 | 22 | 121.0 | 2.3 |
| Albrecht | 325 | 47 | 218.2 | 1.5 |
| Mucci | 569 | 56 | 315.2 | 1.8 |

**Average**: ~1.5 entries/second (network latency dominates)

---

## Future Enhancements

### 1. Pass 2 Hint Integration (Priority: High)

**Status**: Not yet implemented

**Plan**:
- Add `compute_structural_hints()` to Pass 2 classification
- Adjust hint messages for subsection context
- Test on MAJOR INVITED LECTURES → R1 vs R3 disambiguation

### 2. Additional Signals (Priority: Medium)

**Candidate signals from user's library**:
- Citation-like patterns (author lists + volume(issue):pages) → S
- Location tail patterns (City, ST) → D, L, R
- Grant amount patterns ($XXX,XXX) → M
- Author position indicators (first, last, corresponding) → S subsections

**Testing protocol**: Add one signal at a time, validate on full CV set

### 3. Parent Configuration Completion (Priority: High)

**Missing parents** (cause Pass 2 failures):
- F (Licensure and Certification) - needs subsection definitions
- C (Postdoctoral Training) - needs subsection definitions
- L (Clinical Practice) - confusion matrix exists, needs taxonomy_contexts entry

### 4. Confidence Calibration (Priority: Low)

**Goal**: Improve confidence score reliability

**Approach**:
- Collect human-labeled ground truth for 50+ CVs
- Compare LLM confidence vs human agreement
- Adjust confidence thresholds based on empirical data

### 5. Active Learning Loop (Priority: Low)

**Goal**: Automatically improve from production data

**Approach**:
- Flag low-confidence classifications (<0.70)
- Route to human review
- Use corrections to fine-tune confusion matrices
- Periodically retrain structural hint thresholds

---

## Appendix: Glossary

**Parent Section**: One of 20 broad categories (A-T) in WCM taxonomy
**Child Subsection**: Specific classification within a parent (e.g., B6 within B)
**Structural Hint**: Pattern-based signal that augments LLM classification
**Confusion Matrix**: Disambiguation rules for commonly confused sections
**Adaptive Routing**: Confidence-based decision to show focused vs broad options
**Whack-a-Mole**: Phenomenon where fixing one issue breaks another
**Precision**: Accuracy of signal when it triggers (TP / (TP + FP))
**Coverage**: Percentage of entries/sections affected by signal
**Regression**: Unintentional classification change caused by system update
