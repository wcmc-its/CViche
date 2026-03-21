# Spacing Row Fix - Complete

## Issue
Section P table had an unwanted empty spacing row after the header:
```
Row 0: Name of Committee | Role | Dates     [HEADER]
Row 1: [EMPTY SPACING ROW]                  ❌ UNWANTED
Row 2: Curriculum Committee | Chair | ...
Row 3: Faculty Senate | Member | ...
```

## Root Cause
WCM template includes example/placeholder rows in tables. The populator was adding data rows without first clearing these template rows.

## Solution
Added template cleanup logic to **both** table population methods:

### 1. `populate_section_P_institutional_admin()` (lines 435-439)
```python
# Remove any existing data rows (template may have empty example rows)
# Keep only the header row (row 0)
rows_to_delete = len(table.rows) - 1
for _ in range(rows_to_delete):
    table._element.remove(table.rows[-1]._element)
```

### 2. `populate_generic_table()` (lines 356-360)
```python
# Remove any existing data rows (template may have empty example rows)
# Keep only the header row (row 0)
rows_to_delete = len(table.rows) - 1
for _ in range(rows_to_delete):
    table._element.remove(table.rows[-1]._element)
```

## Result After Fix
```
Total rows: 3

Row 0: Name of Committee | Role | Dates     [HEADER]
Row 1: Curriculum Committee | Chair | 2021–Present  ✅
Row 2: Faculty Senate | Member | 2019–Present       ✅
```

Clean table with no spacing rows!

## Bonus Fix
Also resolved FutureWarning:
```python
# Before:
if not target_para_element:

# After:
if target_para_element is None:
```

## Files Modified
- `src/unified_pipeline/core/direct_cv_populator.py`

## Testing
✅ Tested with `data/test_cvs/focused/O_service.docx`
✅ Output verified: `/tmp/test_o_fixed/stage_4_wcm_templates/O_service_WCM.docx`
✅ Table 20 (Section P) has 3 rows: 1 header + 2 data (no spacing)

## Impact
- **Section P**: Fixed
- **All generic sections** (B1, D1, E, I, O): Also fixed via generic handler
- **Future sections**: Any new sections using these handlers will not have spacing rows

## Date
2025-11-06
