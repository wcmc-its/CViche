# Legacy to Direct CV Populator Migration Plan

## Current State Assessment (2025-11-06)

### What Works
- **Section P (Institutional Admin)** - Working in `direct_cv_populator.py`

### System Architecture Problem
**Running TWO parallel systems:**

1. **Unified Pipeline** → `populate_cv_direct()` (modern, clean)
   - File: `src/unified_pipeline/core/direct_cv_populator.py` (640 lines)
   - Direct python-docx manipulation
   - Only supports A1, P currently

2. **Web Interface** → `populate_cv()` (legacy, complex)
   - File: `src/legacy/stage_based_extraction/scripts/production/populate_cv.py` (751 lines)
   - Depends on: `section_special_handlers.py` (2,209 lines!)
   - Plus: wcm_formatter, template_navigator, section_mapper
   - Total legacy footprint: **153 Python files, 4MB**

### Legacy Section Support
Legacy `section_special_handlers.py` provides handlers for:
- **A/A1**: Personal Data
- **J**: Percent Effort (fixed activity table)
- **K/K1/K2/K3**: Educational Contributions
- **M**: Research (with subsections)
- **N**: Mentoring
- **R**: Invitations to Speak (National/International)
- **S**: Bibliography (with 15 subsections S1-S15)
- **T**: Supplemental
- **Generic handler** for standard sections (B1, D1, etc.)

### Why This Is Causing Problems
- Two codebases doing the same thing differently
- Web interface uses brittle legacy code
- Format adapter needed to bridge systems
- 153 legacy files = massive maintenance burden
- Black box behavior = hard to debug

## Migration Strategy

### Phase 1: Foundation (CURRENT)
**Goal: Update web interface to use direct_cv_populator**

Tasks:
- [x] Audit section support
- [ ] Update web interface imports
- [ ] Test Section P still works
- [ ] Deploy to web interface

### Phase 2: Core Section Handlers
**Goal: Port essential sections from legacy to direct_cv_populator**

Priority order (based on importance):
1. **Section A** - Personal Data (name, contact)
2. **Section B1** - Education (generic table handler)
3. **Section D1** - Positions (generic table handler)
4. **Section S** - Publications/Bibliography
5. **Section M** - Research/Grants
6. **Section N** - Mentoring
7. **Section K** - Educational Contributions
8. **Section R** - Presentations
9. **Section J** - Percent Effort

Strategy per section:
- Extract working logic from `section_special_handlers.py`
- Simplify and clarify (remove black boxes)
- Add as method to `DirectCVPopulator` class
- Test with real CV data
- Update routing in `populate_from_enriched_file()`

### Phase 3: Generic Table Handler
**Goal: Handle all standard WCM table sections**

Many sections (B2, B3, D2, D3, D4, E, F, G, H, I, L, O, Q) use standard table format:
- Find table by heading
- Add rows with formatted cells
- Apply borders and fonts

Create one generic method that handles all of these based on section metadata.

### Phase 4: Testing & Validation
**Goal: Ensure no regression**

- Test all ~70 WCM sections
- Compare output to legacy system
- Verify formatting matches WCM template
- Check all data appears correctly

### Phase 5: Cleanup
**Goal: Remove legacy code**

```bash
# Archive legacy system
mv src/legacy archive/legacy_deprecated_20251106
mv src/unified_pipeline/core/legacy_*.py archive/

# Update imports across codebase
# Remove legacy adapters
# Simplify pipeline configuration
```

## Implementation Notes

### Key Simplifications
1. **No wcm_formatter** - Format directly with python-docx
2. **No template_navigator** - Use `find_table_by_heading()`
3. **No section_mapper** - Section routing in single file
4. **No special_handlers** - Methods on DirectCVPopulator class

### Code Quality Principles
1. ✅ Explicit - every action is logged
2. ✅ Verifiable - can check what actually happened
3. ✅ Debuggable - clear error messages
4. ✅ Testable - simple to unit test
5. ✅ Transparent - no black boxes

### Benefits After Migration
- Single codebase (not two)
- ~600 lines instead of ~3,000
- 1 file instead of 153
- Direct python-docx (well-documented library)
- Easy to debug and extend
- No format adapters needed

## Timeline Estimate
- Phase 1 (Web Interface Update): 1 hour
- Phase 2 (Core Sections): 4-6 hours
- Phase 3 (Generic Handler): 2 hours
- Phase 4 (Testing): 2-3 hours
- Phase 5 (Cleanup): 1 hour

**Total: 10-13 hours**

## Risks & Mitigation
- **Risk**: Breaking existing functionality
  - **Mitigation**: Keep legacy code until fully tested

- **Risk**: Missing edge cases in section handlers
  - **Mitigation**: Incremental migration, test each section

- **Risk**: WCM template format changes
  - **Mitigation**: Document format assumptions clearly

## Success Criteria
✅ Web interface uses `populate_cv_direct()`
✅ All critical sections populate correctly
✅ Output matches WCM template requirements
✅ No dependency on legacy code
✅ Legacy code archived (not deleted)
✅ Performance equal or better than legacy
