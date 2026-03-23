# Entry Format Audit Report

**Date:** 2025-11-14
**Purpose:** Document where CV entries are created and their formats for standardization

---

## Summary

The codebase currently uses **TWO entry formats**:
1. **Legacy**: Plain strings `"MD, Yale School of Medicine, 2013"`
2. **Modern**: Dictionaries with `text_snippet`, `entry_type`, `confidence`

---

## Entry Creation Points

### ✅ Modern Format (Already Using Dicts)

#### 1. **Segmentation Modules** - PRIMARY CREATORS
- `segmentation/word_chunked.py` - ✅ Creates modern format
- `segmentation/pdf_vision.py` - ✅ Creates modern format
- `segmentation/word_delimited.py` - Status unknown
- `segmentation/word_original.py` - Status unknown

**Impact:** HIGH - These are the main entry points for all CVs

#### 2. **Entry Processing**
- `core/reinsert_paragraph_delimiters.py:135,151,162,174` - ✅ Reads `text_snippet`
- `core/enrich_segments_with_formatting.py:145,171,182,204` - ✅ Reads/writes `text_snippet`

**Impact:** MEDIUM - Post-processing correctly handles modern format

---

### ⚠️ Mixed Format (Handles Both)

#### 3. **CV Pipeline**
- `core/cv_pipeline.py:494-502` - Checks for both `text` and `text_snippet`
  ```python
  text = entry.get('text', entry.get('text_snippet', ''))
  ```
- `core/cv_pipeline.py:524-526` - Filters based on presence of `text_snippet`

**Impact:** HIGH - Core orchestration handles both formats defensively

#### 4. **Taxonomy Mapper**
- `core/taxonomy_mapper_v2.py:1029,1105,1199` - ✅ JUST FIXED - Now handles both
- `core/taxonomy_mapper.py:359,368` - ⚠️ OLD VERSION - May need similar fix

**Impact:** CRITICAL - Fixed in v2, but v1 may have same bug

---

### ❌ Legacy Format (May Create Strings)

#### 5. **Direct Populator**
- `core/direct_cv_populator.py:1117` - Creates `{"entries": entries}`
  - **Status:** Needs investigation - what format are `entries`?

#### 6. **Test Files**
- `core/test_batch_classification.py:34` - Test data structure
- `core/phase2_test_generator.py:116` - Uses `entry['text_snippet']`

---

## Files Already Using Helper Functions

The new `entry_utils.py` module is now available but not yet imported anywhere.

---

## Migration Priority

### Phase 1: Critical Fixes (DONE)
- [x] Fix `taxonomy_mapper_v2.py` to handle both formats
- [x] Create `entry_utils.py` helper module

### Phase 2: High Priority (Recommended Next)
1. **Check `core/taxonomy_mapper.py`** (old version)
   - May have same `.lower()` bug
   - Consider deprecating if v2 is preferred

2. **Audit `core/direct_cv_populator.py:1117`**
   - Verify what format `entries` uses
   - Migrate to modern format if using strings

3. **Standardize segmentation modules**
   - Verify `word_delimited.py` and `word_original.py` use modern format
   - Add validation to ensure consistency

### Phase 3: Medium Priority
4. **Refactor `cv_pipeline.py`**
   - Use `normalize_entry()` instead of manual checks
   - Replace `entry.get('text', entry.get('text_snippet', ''))` pattern

5. **Add validation layer**
   - Call `validate_entry_structure()` after segmentation
   - Log warnings for legacy format usage

### Phase 4: Low Priority (Future)
6. **Update tests** to use modern format exclusively
7. **Add typing hints** using `Union[str, Dict]` → `Dict` migration
8. **Deprecate legacy format** with clear migration path

---

## Recommended Usage Pattern

```python
# At entry creation (segmentation)
entries = [{
    'text_snippet': "MD, Yale School of Medicine, 2013",
    'entry_type': 'education',
    'confidence': 1.0
}]

# At entry consumption (anywhere)
from core.entry_utils import normalize_entry, extract_text_snippets

# Option 1: Full normalization
normalized = normalize_entry(entry)  # Always returns dict
text = normalized['text_snippet']

# Option 2: Quick text extraction
texts = extract_text_snippets(entries)  # Returns list of strings
```

---

## Risk Assessment

### Low Risk Changes
- ✅ Add `normalize_entry()` calls in existing code
- ✅ Use `extract_text_snippets()` where only text is needed
- ✅ Add validation in non-critical paths

### Medium Risk Changes
- ⚠️ Modify segmentation output format
- ⚠️ Change cv_pipeline.py entry handling
- ⚠️ Update database insertion logic

### High Risk Changes
- ❌ Remove string format support entirely
- ❌ Change segmented JSON file structure
- ❌ Modify existing mapped outputs

---

## Testing Strategy

### Unit Tests Needed
```python
def test_normalize_entry_string():
    result = normalize_entry("MD, Yale")
    assert result['text_snippet'] == "MD, Yale"
    assert result['entry_type'] == 'unknown'

def test_normalize_entry_dict():
    entry = {'text_snippet': 'MD', 'entry_type': 'education'}
    result = normalize_entry(entry)
    assert result == entry

def test_extract_text_snippets_mixed():
    entries = ["MD, Yale", {'text_snippet': 'PhD, MIT'}]
    result = extract_text_snippets(entries)
    assert result == ["MD, Yale", "PhD, MIT"]
```

### Integration Tests
- Run CV 2045 through full pipeline (segmentation → mapping)
- Verify no errors with modern format
- Check backward compatibility with legacy CVs

---

## Next Steps

1. ✅ **COMPLETED:** Create `entry_utils.py` helper module
2. ✅ **COMPLETED:** Fix `taxonomy_mapper_v2.py` dict handling
3. **TODO:** Check if `taxonomy_mapper.py` needs same fix
4. **TODO:** Audit `direct_cv_populator.py` entry format
5. **TODO:** Add `normalize_entry()` to high-traffic code paths
6. **TODO:** Add integration tests for both formats

---

## Decision: Adopt Modern Format

**Recommendation:** YES - Standardize on dictionary format incrementally

**Rationale:**
- Better debuggability (entry_type visible)
- Extensible for future features
- Already partially adopted in segmentation
- Helper functions enable safe migration
- Defensive code prevents breakage

**Approach:** Keep both formats supported for 2-3 releases, gradually migrate, then deprecate strings.
