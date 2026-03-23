# CV Processing Pipeline Output Reorganization - Summary

**Date**: November 22, 2025
**Updated**: November 26, 2025
**Status**: ✅ COMPLETE

## Overview

Reorganized the CV processing pipeline to output all intermediate files to organized stage folders with consistent naming based on file handles (e.g., `2071_Zuschlag_Cv`), eliminating naming inconsistencies and improving auditability.

## Goals Achieved

✅ **Clean handoffs** between pipeline stages with auditable outputs
✅ **Consistent naming** using file handle throughout (no "publications" in filenames)
✅ **Organized structure** with clear stage separation
✅ **100% coverage** - all document indices are accounted for in Stage 2
✅ **Backward compatibility** maintained for existing code

## Current Directory Structure (November 2025)

```
src/unified_pipeline/outputs/
├── stage_1a_segmentation/          # Hierarchical CV segmentation (LLM)
│   ├── {file_handle}_segmented.json
│   └── {file_handle}_segmented.txt (optional, for human review)
├── stage_1b_hierarchy_mapping/     # Map hierarchy to element indices (NO LLM)
│   └── {file_handle}_hierarchy_mapped.json
├── stage_2_entry_extraction/       # Entry extraction with 100% coverage
│   └── {file_handle}_entries.json
├── stage_3_taxonomy_mapping/       # Taxonomy classification
│   └── {file_handle}_mapped.json
└── archive/                        # Archived outputs before reprocessing
```

### Example File Naming

For CV: `2071_Zuschlag_Cv.docx`

- **Stage 1a**: `2071_Zuschlag_Cv_segmented.json` and `2071_Zuschlag_Cv_segmented.txt`
- **Stage 1b**: `2071_Zuschlag_Cv_hierarchy_mapped.json`
- **Stage 2**: `2071_Zuschlag_Cv_entries.json`
- **Stage 3**: `2071_Zuschlag_Cv_mapped.json`

## Implementation Details

### 1. Output Manager Module (NEW)

**File**: `src/unified_pipeline/core/output_manager.py`

Centralized management of output paths and file naming:

- **File Handle Extraction**: Intelligently extracts clean file handles from various input formats:
  - `2071_Zuschlag_Cv.docx` → `2071_Zuschlag_Cv`
  - `2071_Zuschlag_Cv_signature_segmented.json` → `2071_Zuschlag_Cv`
  - `2071_Zuschlag_Cv_mapped.json` → `2071_Zuschlag_Cv`

- **Path Generation**: Provides methods for all stages:
  - `get_stage1a_json_path()` / `get_stage1a_txt_path()` (Stage 1a - Segmentation)
  - `get_stage1b_path()` (Stage 1b - Hierarchy Mapping)
  - `get_stage2_path()` (Stage 2 - Entry Extraction)
  - `get_stage3_path()` (Stage 3 - Taxonomy Mapping)

- **Active vs Legacy Directories**: Only creates active directories (stage_1a, stage_1b, stage_2, stage_3). Legacy names (stage_1, stage_2a, stage_2b) redirect to active directories for backward compatibility lookups only.

- **Validation**: `validate_stage_output()` checks if stage outputs exist
- **Archiving**: `archive_existing_outputs()` preserves old outputs before reprocessing

### 2. Configuration Updates

**File**: `src/unified_pipeline/config.py`

Stage directory constants are now organized as active vs legacy:

```python
# Active stage directories (created automatically)
OUTPUT_STAGE_1A = OUTPUT_BASE / "stage_1a_segmentation"
OUTPUT_STAGE_1B = OUTPUT_BASE / "stage_1b_hierarchy_mapping"
OUTPUT_STAGE_2 = OUTPUT_BASE / "stage_2_entry_extraction"
OUTPUT_STAGE_3 = OUTPUT_BASE / "stage_3_taxonomy_mapping"

# Legacy redirects (NOT created, for backward compatibility lookups only)
OUTPUT_STAGE_1 → OUTPUT_STAGE_1A
OUTPUT_STAGE_2A → OUTPUT_STAGE_2
OUTPUT_STAGE_2B → OUTPUT_STAGE_2
```

### 3. Stage 1a (Segmentation - LLM)

**File**: `src/unified_pipeline/segmentation/signature_based_segmentation.py`

**Changes**:
- ✅ Uses GPT-5.1 for hierarchy normalization (Pass 1 and Pass 2 prompts)
- ✅ Console output shows both prompts sent to LLM AND responses received
- ✅ Output to `outputs/stage_1a_segmentation/`
- ✅ Generates both `.json` and `.txt` files

### 4. Stage 1b (Hierarchy Mapping - NO LLM)

**File**: `src/unified_pipeline/stage_1b_hierarchy_mapper.py`

**Status**: ✅ COMPLETE - Pure Python, no LLM calls

**Key Features**:
- Maps hierarchy headers from Stage 1a to document element indices (paragraph numbers)
- **Synthetic header detection**: Headers created by LLM (e.g., "PUBLICATIONS", "GRANT SUPPORT") are marked as synthetic and skipped during document matching
- **Child search for synthetic parents**: Children of synthetic headers search from the parent's start position, not sequentially
- Computes section boundaries for each hierarchy node
- Creates "Personal Data" synthetic section for preamble content before first section

**Output**: `{file_handle}_hierarchy_mapped.json` with:
- `hierarchy_with_indices`: Mapped hierarchy tree with element_idx for each node
- `section_boundaries`: Flattened list of sections with start/end indices
- `meta.coverage`: Coverage statistics (should be 100%)

### 5. Stage 2 (Entry Extraction - Merged)

**File**: `src/unified_pipeline/stage_2_entry_extraction.py`

**Status**: ✅ COMPLETE - Merged from former Stage 2a + 2b

**Key Features**:
- Extracts individual entries from each section
- **100% coverage**: All document indices are accounted for
- Entry types: `paragraph`, `table`, `table_row`, `header`, `break`
- Parent header entries for non-leaf sections
- Gap break entries for indices between parent headers and first child

**Output**: `{file_handle}_entries.json` with:
- All entries with element indices, types, and hierarchy
- Coverage tracking (should be 100%)

### 6. Stage 3 (Taxonomy Mapping)

**File**: `src/unified_pipeline/core/taxonomy_mapper_v2.py`

**Changes**:
- ✅ Renamed internal references from "Stage 2" to "Stage 3"
- ✅ Use OutputManager for path generation
- ✅ Output to `stage_3_taxonomy_mapping/{file_handle}_mapped.json`
- ✅ Removed version suffixes (`_v2`, `_gpt51`, etc.) from standard output

### 7. Pipeline Orchestrator Updates

**File**: `src/unified_pipeline/run_full_pipeline.py`

**Changes**:
- ✅ Updated stage directories to new structure (1a, 1b, 2, 3)
- ✅ Supports `--stage` argument to run specific stages
- ✅ Updated progress messages with correct stage names

## File Handle Extraction Logic

The `OutputManager._extract_file_handle()` method removes common processing suffixes:

```python
suffixes_to_remove = [
    "_signature_segmented",
    "_segmented",
    "_mapped_v2",
    "_mapped_gpt51_corrected",
    "_mapped_gpt51_hybrid",
    "_mapped_gpt51",
    "_mapped",
    "_delimiters",
    "_entries",
    "_parsed",
    "_classified",
    "_three_pass"
]
```

This ensures consistent file handles across all processing stages.

## Backward Compatibility

### For Code
- ✅ Legacy directory names (`stage_1_segmentation`, `stage_2a_entry_delimitation`, `stage_2b_extract_entries_from_delimiters`) redirect to active directories for lookups
- ✅ Legacy method names (`get_stage1_json_path()`, `get_stage2a_path()`, `get_stage2b_path()`) still work but are deprecated
- ✅ Stage directory mapping includes legacy keys for backward compatibility

### For Existing Outputs
- ✅ Old outputs preserved in `outputs/archive/` directory
- ✅ OutputManager can extract file handles from old naming schemes
- ✅ No automatic migration - new runs will use new structure
- ✅ Legacy directories are NOT created automatically (only active directories are created)

## Testing

### OutputManager Tests

```python
from core.output_manager import OutputManager

# Test with original CV
om = OutputManager('data/sample_cvs/word/2071_Zuschlag_Cv.docx')
print(om.file_handle)  # Output: "2071_Zuschlag_Cv"
print(om.get_stage1a_json_path())  # .../stage_1a_segmentation/2071_Zuschlag_Cv_segmented.json

# Test with processed file
om2 = OutputManager('data/sample_cvs/word/2071_Zuschlag_Cv_signature_segmented.json')
print(om2.file_handle)  # Output: "2071_Zuschlag_Cv" (suffix removed)
print(om2.get_stage3_path())  # .../stage_3_taxonomy_mapping/2071_Zuschlag_Cv_mapped.json
```

✅ **Result**: File handle extraction works correctly across all input formats

## Usage Examples

### For Pipeline Development

```python
from core.output_manager import OutputManager

# Initialize with any CV file or processed output
om = OutputManager("path/to/cv_file.docx")

# Get paths for each stage (new naming convention)
stage1a_json = om.get_stage1a_json_path()
stage1a_txt = om.get_stage1a_txt_path()
stage1b = om.get_stage1b_path()
stage2 = om.get_stage2_path()
stage3 = om.get_stage3_path()

# Validate outputs exist
if om.validate_stage_output("stage_1a_segmentation"):
    print("Stage 1a complete")

# Archive existing outputs before reprocessing
archived = om.archive_existing_outputs()
```

### For Direct Stage Execution

```python
# Stage 1a: Segmentation (automatically uses OutputManager)
from segmentation.signature_based_segmentation import run_stage_1a

result = run_stage_1a("cv.docx")
# Outputs to: outputs/stage_1a_segmentation/cv_segmented.json
#             outputs/stage_1a_segmentation/cv_segmented.txt

# Stage 1b: Hierarchy Mapping (no LLM)
from stage_1b_hierarchy_mapper import run_stage_1b

result = run_stage_1b("cv.docx", "outputs/stage_1a_segmentation/cv_segmented.json")
# Outputs to: outputs/stage_1b_hierarchy_mapping/cv_hierarchy_mapped.json

# Stage 2: Entry Extraction
from stage_2_entry_extraction import run_stage_2

result = run_stage_2("cv.docx", "outputs/stage_1b_hierarchy_mapping/cv_hierarchy_mapped.json")
# Outputs to: outputs/stage_2_entry_extraction/cv_entries.json

# Stage 3: Taxonomy Mapping (automatically uses OutputManager)
from core.taxonomy_mapper_v2 import map_cv_sections

result = map_cv_sections("outputs/stage_2_entry_extraction/cv_entries.json")
# Outputs to: outputs/stage_3_taxonomy_mapping/cv_mapped.json
```

## Key Benefits

### 1. Improved Troubleshooting
- **Clear stage separation**: Each stage has its own directory
- **Auditable outputs**: Every intermediate file is preserved with clear naming
- **TXT companions**: Human-readable versions of JSON outputs for quick review

### 2. Consistent Naming
- **No more variations**: Eliminated `_signature_`, `_v2`, `_gpt51_corrected`, etc. from standard outputs
- **File handle based**: All outputs tied to original CV filename (e.g., `2071_Zuschlag_Cv`)
- **No "publications"**: Avoided using entity-specific terms in filenames (borderline cases exist)

### 3. Clean Handoffs
- **Stage 1a** → JSON segmentation with hierarchy preserved (LLM)
- **Stage 1b** → Hierarchy mapped to element indices (NO LLM)
- **Stage 2** → Entry extraction with 100% coverage
- **Stage 3** → Taxonomy classification
- Each stage takes explicit input from previous stage

### 4. Better Organization
```
Before (mixed):
outputs/
├── 2071_Zuschlag_Cv_signature_segmented.json
├── 2071_Zuschlag_Cv_mapped_v2.json
├── 2071_Zuschlag_Cv_mapped_gpt51_corrected.json
└── ...scattered files...

After (organized):
outputs/
├── stage_1a_segmentation/
│   ├── 2071_Zuschlag_Cv_segmented.json
│   └── 2071_Zuschlag_Cv_segmented.txt
├── stage_1b_hierarchy_mapping/
│   └── 2071_Zuschlag_Cv_hierarchy_mapped.json
├── stage_2_entry_extraction/
│   └── 2071_Zuschlag_Cv_entries.json
├── stage_3_taxonomy_mapping/
│   └── 2071_Zuschlag_Cv_mapped.json
└── archive/
    └── 2071_Zuschlag_Cv_20251122_143000/
```

## Next Steps

### Completed Work (November 2025)

1. ✅ **Merged Stage 2a/2b into Stage 2**
   - Entry delimitation and extraction combined into single Stage 2
   - 100% coverage achieved for all document indices
   - Deprecated stage_2a and stage_2b directories

2. ✅ **Added Stage 1b (Hierarchy Mapping)**
   - Pure Python, no LLM calls
   - Maps hierarchy to element indices
   - Handles synthetic headers correctly

3. ✅ **Coverage Tracking**
   - Stage 2 now tracks and reports coverage percentage
   - Parent header entries for non-leaf sections
   - Gap break entries for unassigned indices

### Recommended Future Work

1. **Add Stage Output Validation**
   - Validate JSON structure at each stage
   - Check for required fields
   - Flag suspicious patterns (e.g., missing entries, low confidence scores)

2. **Create Migration Script**
   - Optional script to migrate existing outputs to new structure
   - Useful for reprocessing old CVs with consistent naming

3. **Add Output Logging**
   - Log all output file paths to a central registry
   - Track processing history per CV
   - Enable easy lookup of "where did this file go?"

## Files Modified

### Key Pipeline Files

- ✅ `src/unified_pipeline/core/output_manager.py` - Centralized output path management
- ✅ `src/unified_pipeline/segmentation/signature_based_segmentation.py` - Stage 1a (LLM segmentation)
- ✅ `src/unified_pipeline/stage_1b_hierarchy_mapper.py` - Stage 1b (hierarchy mapping, NO LLM)
- ✅ `src/unified_pipeline/stage_2_entry_extraction.py` - Stage 2 (entry extraction)
- ✅ `src/unified_pipeline/core/taxonomy_mapper_v2.py` - Stage 3 (taxonomy mapping)
- ✅ `src/unified_pipeline/run_full_pipeline.py` - Pipeline orchestrator

### Documentation
- ✅ `src/unified_pipeline/OUTPUT_REORGANIZATION_SUMMARY.md` (this file)

## Validation

✅ **Output Manager**: File handle extraction tested and working
✅ **Directory Structure**: All active stage directories created automatically (stage_1a, stage_1b, stage_2, stage_3)
✅ **Legacy Directories**: NOT created (stage_1, stage_2a, stage_2b redirect to active directories)
✅ **Naming Consistency**: File handles correctly extracted from all input formats
✅ **Backward Compatibility**: Legacy stage references maintained for lookups
✅ **100% Coverage**: Stage 2 accounts for all document indices

## Conclusion

The output reorganization is complete and ready for use. The new structure provides:

- Clear separation of pipeline stages (1a, 1b, 2, 3)
- Consistent file naming based on file handles
- Improved troubleshooting with auditable outputs
- Clean handoffs between stages
- 100% coverage in Stage 2 (all document indices accounted for)
- Backward compatibility with existing code

All code changes are backward compatible, and the new `OutputManager` module provides a clean, centralized way to manage all pipeline outputs.

---

**Implementation Status**: ✅ COMPLETE
**Testing Status**: ✅ VERIFIED
**Documentation Status**: ✅ COMPLETE
**Last Updated**: November 26, 2025
