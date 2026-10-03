# Validators Package

**Purpose**: Provide deterministic guidance to the taxonomy mapper during classification, plus post-classification auto-correction.

## Architecture

The validator system provides:
1. **Pre-classification guidance** - Narrow LLM options using deterministic signals
2. **Post-classification correction** - Auto-fix common misclassification patterns (added v2.0)

### Key Principles

1. **Advisory, not prescriptive**: Validators provide guidance; LLM makes final decision
2. **Runs during mapping**: Integrated into Pass 1 and Pass 2 (not post-processing)
3. **Deterministic detection**: Pattern-based rules, not additional LLM calls
4. **Modular**: Easy to add new validators based on discovered patterns
5. **Logged**: Track validator effectiveness and conflict frequency

### When Validators Run

```
PASS 1 (Parent Section Classification)
├── PRE-VALIDATION
│   ├── Call validators with section label + entries
│   ├── Get guidance (recommended/excluded sections)
│   └── Can OVERRIDE Pass 1 result if confidence ≥ 80%
├── PASS 1 CLASSIFICATION (LLM)
│   └── Maps group to parent section (A-T)
└── VALIDATOR OVERRIDE LOGIC
    └── If validator has high confidence (≥80%), override Pass 1 result

PASS 2 (Subsection Classification)
├── PRE-VALIDATION
│   ├── Call validators with parent + entries
│   ├── Get guidance (recommended/excluded subsections)
│   └── Include guidance in LLM prompt as hints
└── PASS 2 CLASSIFICATION (LLM with validator hints)
    └── Maps to subsection (e.g., S1, S2, S7)
```

## Validator Types

### Section-Level Validators
Analyze ALL entries in a section (e.g., label-content conflict detection).

**Interface**:
```python
def analyze_section(
    self,
    section_label: str,
    entries: List[Dict]  # Full entry objects with entry_type
) -> ValidatorGuidance:
```

### Entry-Level Validators
Analyze individual entries (e.g., URL domain detection, DOI patterns).

**Interface**:
```python
def analyze(
    self,
    entry_text: str  # Single entry text
) -> ValidatorGuidance:
```

## Current Validators

| Validator | Priority | Applies To | Purpose |
|-----------|----------|------------|---------|
| **LabelContentConflictValidator** | 5 (very early) | All sections | Detects generic labels with specific content |
| ContactSectionValidator | 10 | A (Personal Data) | Excludes non-contact subsections |
| URLDomainValidator | 20 | S (Bibliography) | Recommends S7 for preprint URLs |
| S7UnpublishedValidator | 20 | S (Bibliography) | Detects unpublished work patterns |
| EducationPostdocValidator | 15 | B (Education) | Distinguishes postdocs from education |
| AwardsGrantsValidator | 15 | H/M | Separates honors from grants |
| MentoringIndicatorsValidator | 15 | D/E | Detects mentoring vs service |
| ProfessionalServiceValidator | 15 | D/E | Identifies service activities |
| LeadershipCommitteeValidator | 15 | E/I | Identifies leadership roles |
| FellowshipValidator | 12 | B/H | Distinguishes fellowships |

## Creating a New Validator

### 1. Create Validator File

`validators/your_validator.py`:

```python
from typing import List
from .base_validator import BaseValidator, ValidatorGuidance

class YourValidator(BaseValidator):
    """
    Brief description of what this validator detects.

    Case Study: CV XXXX had problem Y, this validator catches it.
    """

    name = "YourValidator"

    def applies_to(self) -> List[str]:
        """Parent sections this validator applies to."""
        return ['S']  # Or ['*'] for universal

    def priority(self) -> int:
        """Execution order (lower = earlier). Range: 1-100."""
        return 20

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """Analyze single entry."""
        # Your detection logic here
        if self._detect_pattern(entry_text):
            return ValidatorGuidance(
                exclude_sections=['S1', 'S2'],
                recommend_sections=['S7'],
                hints=['Detected pattern X, recommend S7'],
                confidence=0.9,
                severity='hard',  # or 'soft'
                allow_override=True,
                deterministic_signals=['pattern_x_detected']
            )
        return ValidatorGuidance.no_guidance()
```

### 2. Register Validator

`validators/__init__.py`:

```python
from .your_validator import YourValidator

_your_validator = YourValidator()
register_validator(_your_validator)
```

### 3. Test Validator

```bash
# Test on specific CV
python3 -m core.taxonomy_mapper_v2 \
    outputs/XXXX_segmented.json \
    outputs/XXXX_mapped_test.json

# Look for validator logs
[VALIDATOR-P1] YourValidator → 3 hints, confidence=0.90
[OVERRIDE] Validator: A → S (pattern X detected)
```

## ValidatorGuidance Fields

| Field | Type | Purpose |
|-------|------|---------|
| `exclude_sections` | `List[str]` | Sections to exclude (hard rule) |
| `recommend_sections` | `List[str]` | Sections to recommend (soft suggestion) |
| `hints` | `List[str]` | Human-readable explanations for LLM |
| `confidence` | `float` | 0.0-1.0, how confident in guidance |
| `severity` | `str` | 'hard' (must follow) or 'soft' (advisory) |
| `allow_override` | `bool` | Can LLM override with semantic reasoning? |
| `deterministic_signals` | `List[str]` | Log identifiers for tracking |
| `exclusion_reasons` | `Dict` | Why each section was excluded |

## Pass 1 Override Threshold

Validators can **override Pass 1 results** if:
- Validator provides `recommended_subsections`
- `confidence_in_guidance >= 0.80`
- Logs: `[OVERRIDE] Validator: {original} → {recommended} ({reason})`

## Best Practices

### YAGNI Principle
Start with 3-6 patterns, add more only when proven necessary. Don't over-engineer.

### Pattern-Based Detection
Use `startswith()` to catch subtypes:
```python
if entry_type.startswith('publication'):  # Catches publication_peer_reviewed, etc.
```

### Confidence Levels
- **0.95+**: Nearly certain (e.g., DOI pattern)
- **0.85**: High confidence (e.g., label-content conflict with 70%+ consistency)
- **0.70**: Moderate confidence (e.g., URL domain heuristics)
- **0.50**: Weak signal (e.g., keyword hints)

### Advisory vs Hard Rules
- **Hard (`severity='hard'`)**: Deterministic, no semantic exceptions (e.g., DOI means published)
- **Soft (`severity='soft'`)**: Strong suggestion, LLM can override (e.g., label conflicts)

### Logging for Monitoring
Always include `deterministic_signals` for tracking:
```python
deterministic_signals=[
    f'label_content_conflict:publication:{consistency_pct}pct'
]
```

## Example: Label-Content Conflict Validator

**Problem**: CV 2039 had "Contact Information" sections containing 28 publications and 13 presentations.

**Solution**: Detect when generic labels (contact, personal, CV) contain specific content types (publications, presentations, grants).

**Key Features**:
- Section-level analysis (needs all entries to determine consistency)
- 70% consistency threshold (avoid false positives)
- 85% confidence (high but not absolute)
- Soft severity (allow LLM semantic override)
- Logs conflicts for monitoring

**Results**:
- CV 2039: Groups G10, G33, G34 correctly mapped from A → S/R
- Pass 1 override: Validator confidence 85% > 80% threshold
- Modular: No changes to core mapper logic needed

## Files

### Pre-Classification Validators
- `base_validator.py`: Base classes and interfaces
- `guidance_engine.py`: Orchestrates all validators
- `label_content_conflict.py`: Generic label + specific content detector
- `s7_unpublished.py`: Unpublished work patterns
- `education_postdoc.py`: Postdoc vs education distinguisher
- `awards_grants.py`: Honors vs grants separator
- `contact_section.py`: Contact info validator
- `url_domain.py`: URL-based classification
- `mentoring_indicators.py`: Mentoring detection
- `professional_service.py`: Service activity detection
- `leadership_committee.py`: Leadership role detection
- `fellowship_classifier.py`: Fellowship distinguisher

### Post-Classification Correctors (v2.0 → v3.0)
- `structural_header.py`: Detects document titles, page numbers → forces T
- `committee_position_corrector.py`: Distinguishes committee service (P/Q2) from positions (D)
- `reasoning_consistency_checker.py`: Auto-corrects when reasoning contradicts assigned code
- `grant_status_corrector.py`: Date-based M2A/M2B/M2C override (v2.1)
- `teaching_leadership_corrector.py`: Course Director → K3 not K1 (v3.0)
- `leadership_level_corrector.py`: Committee chairs → P not O (v3.0)
- `adjunct_position_corrector.py`: Community college instructors → D3 not D1 (v3.0)
- `training_compliance_corrector.py`: DEI/Title IX training → B2 not P (v3.0)
- `invited_talk_corrector.py`: Keynotes/invited conference talks → R not S8 (v3.0)
- `hierarchy_mismatch_flagger.py`: QA flags for hierarchy-taxonomy mismatches (v2.1)

## Post-Classification Correctors (v3.0)

These validators run AFTER LLM classification to catch and auto-correct common errors.
Based on expert evaluation feedback achieving 86-91/100 accuracy scores.

### When They Run

```
LLM CLASSIFICATION (stage_3b_entry_classifier.py)
├── Batch classification with rules v2.6
├── T-validation gate
├── Fragment reconnection
├── Duplicate detection
└── POST-CLASSIFICATION PASS (10 steps)
    ├── 1. apply_structural_corrections()      → T for doc titles/page numbers
    ├── 2. apply_committee_corrections()       → P/Q2 for committee service
    ├── 3. apply_reasoning_corrections()       → Fix reasoning-code mismatches
    ├── 4. apply_grant_status_corrections()    → M2A/M2B/M2C based on dates
    ├── 5. apply_teaching_leadership_corrections() → K1 → K3 for Course Directors
    ├── 6. apply_leadership_level_corrections()    → O → P for non-executive roles
    ├── 7. apply_adjunct_position_corrections()    → D1 → D3 for non-faculty
    ├── 8. apply_training_compliance_corrections() → P → B2 for trainings
    ├── 9. apply_invited_talk_corrections()        → S8 → R for invited talks
    └── 10. flag_hierarchy_mismatches()            → QA flags (no auto-correct)
```

### 1. StructuralHeaderValidator

Detects document-level structural elements that should be T (not A):

| Pattern | Example | Action |
|---------|---------|--------|
| CV titles | "CURRICULUM VITAE", "CV" | → T |
| Page markers | "Page 1", "1 of 15" | → T |
| Timestamps | "Updated: January 2024" | → T |
| Dividers | "---", "***" | → T |
| Name-only | "John Smith" (just name) | → T |

### 2. CommitteePositionCorrector

Distinguishes committee service from positions (D codes → P/Q2):

| Input Pattern | Classification |
|---------------|----------------|
| "Appointed to R&D Committee" | D2 → P (internal) |
| "Member, NIH Study Section" | D1 → Q2 (external) |
| "Serves on AHA Advisory Board" | D2 → Q2 (external) |

### 3. ReasoningConsistencyChecker

Detects when LLM reasoning suggests code X but assigns code Y:

```
Text: "CURRICULUM VITAE"
Assigned: A
Reasoning: "T is appropriate as an appendix/other structural element"
→ Auto-correct to: T (confidence 0.90)
```

### 4. GrantStatusCorrector (v2.1)

Corrects grant status codes based on date analysis:

| Scenario | From | To | Reason |
|----------|------|-----|--------|
| End year < current year | M2A | M2B | Grant completed |
| End year < current year, heading files it as awarded | M2C | M2B | Grant completed |
| End year ≥ current year | M2B/M2C | M2A | Grant still active |

A grant under a pending / submitted / in-review / not-funded / declined /
withdrawn / applied / application heading, or whose text has a
`Status: Pending`-style line, is left alone whatever its code: its dates are a
proposed period (#981). An M2C under any other heading moves to M2B on ended
dates only when the heading names current, past, completed, funded or awarded
grants: an old application is not a completed award (EBYSBC E7).

**Key patterns:** `2019-2024`, `2020-present`, `01/2019-12/2024`, `03/01/2024-12/31/2028`

### 5. TeachingLeadershipCorrector (v3.0)

Corrects K1 → K3 when teaching leadership roles are present:

| Pattern | From | To |
|---------|------|-----|
| "Course Director and Lecturer, PREV 659" | K1 | K3 |
| "Co-Director, Introduction to Clinical Research" | K1 | K3 |
| "Program Director, Graduate Education" | K1 | K3 |

**Key signals:** Course Director, Program Director, Co-Director, Clerkship Director

### 6. LeadershipLevelCorrector (v3.0)

Corrects O → P for non-executive administrative roles:

| Role Type | Code | Example |
|-----------|------|---------|
| Executive (keep O) | O | Department Chair, Center Director, Associate Dean |
| Administrative (→ P) | P | Committee Chair, Track Director, Secretary/Treasurer |

**Rule:** O = budget/personnel authority. Everything else = P.

### 7. AdjunctPositionCorrector (v3.0)

Corrects D1 → D3 for non-faculty positions:

| Position | From | To |
|----------|------|-----|
| "Adjunct Instructor, Community College" | D1 | D3 |
| "Lab Manager and Instructor" | D1 | D3 |
| "Part-time Instructor, Biology Lab" | D1 | D3 |

**Protected patterns:** "Professor", "Adjunct Professor", "Faculty"

### 8. TrainingComplianceCorrector (v3.0)

Corrects P → B2 for training/compliance courses received:

| Training Type | From | To |
|---------------|------|-----|
| "Annual Title IX Training" | P | B2 |
| "Everyday Bias for Healthcare Professionals" | P | B2 |
| "CITI Training", "HIPAA Training" | P | B2 |

**Rule:** Trainings RECEIVED = B2. Trainings FACILITATED = P or K.

### 9. InvitedTalkCorrector (v3.0)

Corrects S8 → R for invited conference presentations:

| Pattern | From | To |
|---------|------|-----|
| "Invited Talk at Federal Interagency Conference" | S8 | R |
| "Keynote Lecture, Annual Meeting" | S8 | R |
| "Grand Rounds, Department of Medicine" | S8 | R |

**S8 protected:** "Poster Presentation", "Contributed Talk", "Abstract #123"

### 10. HierarchyMismatchFlagger (v2.1)

**QA flags only - no auto-correction.** Flags entries where taxonomy code doesn't match hierarchy expectations.

| Hierarchy | Code | Flag? | Notes |
|-----------|------|-------|-------|
| "Publications" | M2 | ⚠ Yes | Grant under Publications |
| "Honors" | R | No | Invited talks often under Honors |
| "Patent Applications" | M3 | No | M3 correct for all patents |
| "Current Grants" | M2B | ⚠ Yes | Active grants marked completed |

**Accepted mismatches (not flagged):**
- R under "Honors" (CV authors often misplace invited talks)
- M3 under "Patent Applications" (pending patents are still M3)

### Usage

```python
from core.validators import (
    apply_structural_corrections,
    apply_committee_corrections,
    apply_reasoning_corrections,
    apply_grant_status_corrections,
    apply_teaching_leadership_corrections,
    apply_leadership_level_corrections,
    apply_adjunct_position_corrections,
    apply_training_compliance_corrections,
    apply_invited_talk_corrections,
    flag_hierarchy_mismatches
)

# Apply all post-classification corrections (in order)
entries, struct_stats = apply_structural_corrections(entries, document_name)
entries, committee_stats = apply_committee_corrections(entries)
entries, reasoning_stats = apply_reasoning_corrections(entries)
entries, grant_stats = apply_grant_status_corrections(entries)
entries, teaching_stats = apply_teaching_leadership_corrections(entries)
entries, leadership_stats = apply_leadership_level_corrections(entries)
entries, adjunct_stats = apply_adjunct_position_corrections(entries)
entries, training_stats = apply_training_compliance_corrections(entries)
entries, invited_stats = apply_invited_talk_corrections(entries)
entries, mismatch_stats = flag_hierarchy_mismatches(entries)

# Total corrections
total = sum([
    struct_stats['corrections_made'],
    committee_stats['corrections_made'],
    reasoning_stats['corrections_made'],
    grant_stats['corrections_applied'],
    teaching_stats['corrections_applied'],
    leadership_stats['corrections_applied'],
    adjunct_stats['corrections_applied'],
    training_stats['corrections_applied'],
    invited_stats['corrections_applied']
])
print(f"Total corrections: {total}")
print(f"QA flags: {mismatch_stats['entries_flagged']}")
```

## Debugging

Enable debug output:
```bash
python3 -m core.taxonomy_mapper_v2 input.json output.json 2>&1 | \
    grep -E "(VALIDATOR|OVERRIDE)"
```

Expected output:
```
[VALIDATOR-P1] LabelContentConflictValidator → 4 hints, confidence=0.85
[OVERRIDE] Validator: A → S (label-content conflict detected)
[VALIDATOR-P2] S7UnpublishedValidator → 2 hints, confidence=0.90
```
