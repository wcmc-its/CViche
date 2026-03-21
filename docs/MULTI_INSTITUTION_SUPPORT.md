# Multi-Institution CV Format Support

## Overview

This document analyzes what would be required to extend CViche to support CV formats for institutions other than Weill Cornell Medicine (WCM).

## Current Architecture

The pipeline has a clear separation between **institution-agnostic** and **institution-specific** components:

### Institution-Agnostic Components (Reusable)

| Stage | Component | Description |
|-------|-----------|-------------|
| 1a | Text extraction | Extracts text from DOCX files |
| 1b | Hierarchy mapping | Identifies document structure (headers, sections) |
| 2 | Entry extraction | Splits sections into individual entries |
| 5 | PubMed enrichment | Adds publication metadata from NCBI |
| 5b | Institution enrichment | Resolves institution names via ROR API |
| 5d | Citation formatting | Formats non-enriched publications to Vancouver style |

### Institution-Specific Components (Require Customization)

| Stage | Component | WCM-Specific Elements |
|-------|-----------|----------------------|
| 3a/3b | Taxonomy mapping | Uses WCM taxonomy codes (A-T, S1-S9, etc.) |
| 4 | Field extraction | Field schemas tied to WCM categories |
| 4.5 | Research summary | Generates M1 section in WCM format but this is common output on CVs / biosketches |
| 5c | Teaching formatter | Formats teaching entries for WCM K-codes |
| 6 | Document generation | Fills WCM Word template |

---

## Components Requiring Institution-Specific Configuration

### 1. Taxonomy Codes (`core/valid_taxonomy_codes.py`)

**Current WCM Structure:**
```
A - Personal Data
B - Education (B1: Undergrad, B2: Graduate)
C - Postdoctoral Training
D - Professional Positions
...
S - Bibliography (S1: Peer-reviewed, S2: Reviews, etc.)
T - Appendix
```

**To Support Another Institution:**
- Define a new taxonomy code set (e.g., `INSTITUTION_X_CODES`)
- Map their CV sections to standardized internal codes
- Create validation functions for the new code set

### 2. Section-to-Template Mapping (`stage_6_word_template.py`)

**Current WCM Mapping:**
```python
TAXONOMY_TO_SECTION = {
    'S1': 'peer_reviewed',
    'S2': 'reviews_editorials',
    'M2A': 'current_grants',
    ...
}
```

**To Support Another Institution:**
- Create a parallel mapping for their template sections
- May need to handle different granularity (e.g., one institution may have 5 publication types, another may have 12)

### 3. Word Template (`key_files/wcm_cv_template_*.docx`)

**Current:**
- Uses `wcm_cv_template_faculty_october_2022_final.docx`
- Has placeholder tables and sections matching WCM taxonomy

**To Support Another Institution:**
- Obtain or create their institutional CV template
- Ensure placeholder markers are consistent
- May need custom table structures

### 4. Date Format Specifications

**Current WCM Formats:**
```python
DATE_FORMATS = {
    'B1': 'mm/yyyy',    # Education dates
    'D1': 'mm/yy',      # Appointment dates
    'F1': 'mm/dd/yyyy', # Licensure dates
    ...
}
```

**To Support Another Institution:**
- Each institution may have different date format preferences
- Create institution-specific date format mappings

### 5. Field Extraction Schemas (`config/field_schemas_v1.1.json`)

**Current:**
- Defines fields to extract per taxonomy code
- E.g., for S1 (publications): authors, title, journal, year, PMID, etc.

**To Support Another Institution:**
- May need additional fields (e.g., impact factor, quartile ranking)
- May have different required vs. optional fields

### 6. LLM Prompts (Throughout Pipeline)

Several stages use LLM prompts that reference WCM-specific terminology:
- `stage_3a_header_taxonomy_mapper.py` - Maps headers to taxonomy
- `stage_3b_entry_classifier.py` - Classifies entries
- `stage_4_field_extractor.py` - Extracts structured fields

**To Support Another Institution:**
- Prompts need institution-specific taxonomy descriptions
- May need different classification criteria

---

## Recommended Refactoring Approach

### Phase 1: Configuration Externalization

1. **Create Institution Configuration Files**
   ```
   config/institutions/
   ├── wcm/
   │   ├── taxonomy.json
   │   ├── field_schemas.json
   │   ├── date_formats.json
   │   └── template.docx
   └── institution_x/
       ├── taxonomy.json
       ├── field_schemas.json
       ├── date_formats.json
       └── template.docx
   ```

2. **Add Institution Parameter to Pipeline**
   ```python
   def run_pipeline(input_file: str, institution: str = "wcm"):
       config = load_institution_config(institution)
       # Use config throughout pipeline
   ```

### Phase 2: Abstract Common Operations

1. **Create Institution-Agnostic Base Classes**
   ```python
   class BaseTaxonomyMapper:
       def __init__(self, taxonomy_config: dict):
           self.codes = taxonomy_config['codes']
           self.mappings = taxonomy_config['mappings']

   class BaseTemplateGenerator:
       def __init__(self, template_path: str, section_mapping: dict):
           ...
   ```

2. **Move WCM-Specific Logic to Subclasses**
   ```python
   class WCMTemplateGenerator(BaseTemplateGenerator):
       # WCM-specific formatting

   class InstitutionXTemplateGenerator(BaseTemplateGenerator):
       # Institution X specific formatting
   ```

### Phase 3: Dynamic Template Handling

1. **Template Discovery System**
   - Scan template for section markers
   - Auto-detect available sections
   - Handle missing sections gracefully

2. **Flexible Section Population**
   - Generic table population logic
   - Institution-specific formatting hooks

---

## Effort Estimate by Component

| Component | Complexity | Notes |
|-----------|------------|-------|
| Taxonomy configuration | Low | JSON config file |
| Field schemas | Low | JSON config file |
| Date formats | Low | JSON config file |
| Template creation | Medium | Requires institution's template |
| LLM prompt adaptation | Medium | May need prompt tuning |
| Template generator | High | Table/section structure varies |
| Testing & validation | High | Need sample CVs from new institution |

---

## Implementation Checklist for New Institution

- [ ] Obtain institutional CV template (Word format)
- [ ] Document taxonomy/section structure
- [ ] Create taxonomy code mappings
- [ ] Define field extraction schemas per category
- [ ] Specify date format requirements
- [ ] Adapt LLM prompts for classification
- [ ] Implement template generator for their format
- [ ] Test with sample CVs
- [ ] Validate output against institutional requirements

---

## Web Interface Changes

The web interface would need minimal changes:

1. **Upload Page**: Add institution selector dropdown
2. **Backend**: Pass institution parameter to pipeline
3. **Output**: Return institution-specific document

```tsx
// UploadPage.tsx
<select value={institution} onChange={(e) => setInstitution(e.target.value)}>
  <option value="wcm">Weill Cornell Medicine</option>
  <option value="institution_x">Institution X</option>
</select>
```

---

## Conclusion

The current architecture is reasonably modular. The primary work for supporting a new institution involves:

1. **Configuration** (40%): Creating taxonomy, field schemas, and date format configs
2. **Template** (30%): Adapting the document generation to their template structure
3. **Validation** (30%): Testing and tuning with real CVs from the new institution

The institution-agnostic components (text extraction, PubMed enrichment, citation formatting) require no changes.
