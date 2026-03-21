#!/usr/bin/env python3
"""
Reorganize CV processing outputs into individual CV folders.

STANDARD OUTPUT LOCATION:
All CV processing outputs should be organized in:
    outputs/cv_processing/

Structure per CV:
    outputs/cv_processing/YYYY-MM-DD_HH-MM_CV_NAME/
        - CV_NAME.docx (original from data/sample_cvs/word/)
        - CV_NAME_segmented.json (Pass 1: Word structure extraction)
        - CV_NAME_repaired.json (Pass 2: Structural repairs)
        - CV_NAME_preprocessed.json (Pass 3: Signal detection)
        - CV_NAME_mapped.json (Pass 4: Taxonomy mapping)

Benefits of this structure:
    - Visible folder (not hidden like .outputs/)
    - Chronological organization with timestamps
    - All 5 files per CV in one place for easy review
    - Consistent location across multiple test iterations
    - Original .docx alongside processed outputs

Usage:
    Run this script after batch processing to move files from temporary
    locations (like .outputs/phase2_20cvs/) to the standard location.
"""

import os
import shutil
from pathlib import Path
from datetime import datetime

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
OLD_OUTPUT_DIR = PROJECT_ROOT / "src/unified_pipeline/core/.outputs/phase2_20cvs"
NEW_OUTPUT_DIR = PROJECT_ROOT / "outputs/cv_processing"
DOCX_SOURCE_DIR = PROJECT_ROOT / "data/sample_cvs/word"

def get_cv_names():
    """Get unique CV names from existing files."""
    cv_names = set()
    if OLD_OUTPUT_DIR.exists():
        for file in OLD_OUTPUT_DIR.glob("*_segmented.json"):
            cv_name = file.stem.replace("_segmented", "")
            cv_names.add(cv_name)
    return sorted(cv_names)

def get_file_timestamp(file_path):
    """Get file modification time as formatted string."""
    if file_path.exists():
        mtime = os.path.getmtime(file_path)
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime("%Y-%m-%d_%H-%M")
    return datetime.now().strftime("%Y-%m-%d_%H-%M")

def reorganize_cv_files(cv_name):
    """Reorganize all files for a single CV."""
    # Get timestamp from segmented file (earliest file created)
    segmented_file = OLD_OUTPUT_DIR / f"{cv_name}_segmented.json"
    timestamp = get_file_timestamp(segmented_file)

    # Create CV folder
    cv_folder = NEW_OUTPUT_DIR / f"{timestamp}_{cv_name}"
    cv_folder.mkdir(parents=True, exist_ok=True)

    print(f"Organizing {cv_name}...")
    print(f"  → {cv_folder.name}/")

    files_moved = 0

    # Move JSON files
    for suffix in ["segmented", "repaired", "preprocessed", "mapped"]:
        old_file = OLD_OUTPUT_DIR / f"{cv_name}_{suffix}.json"
        if old_file.exists():
            new_file = cv_folder / f"{cv_name}_{suffix}.json"
            shutil.move(str(old_file), str(new_file))
            print(f"    ✓ Moved {cv_name}_{suffix}.json")
            files_moved += 1
        else:
            print(f"    ⚠ Missing {cv_name}_{suffix}.json")

    # Copy original .docx file
    docx_source = DOCX_SOURCE_DIR / f"{cv_name}.docx"
    if docx_source.exists():
        docx_dest = cv_folder / f"{cv_name}.docx"
        shutil.copy2(str(docx_source), str(docx_dest))
        print(f"    ✓ Copied {cv_name}.docx (original)")
        files_moved += 1
    else:
        print(f"    ⚠ Original .docx not found: {docx_source}")

    return files_moved

def main():
    print(f"{'='*70}")
    print("REORGANIZING CV PROCESSING OUTPUTS")
    print(f"{'='*70}")
    print(f"From: {OLD_OUTPUT_DIR}")
    print(f"To:   {NEW_OUTPUT_DIR}")
    print(f"{'='*70}\n")

    # Get all CVs
    cv_names = get_cv_names()

    if not cv_names:
        print("No CVs found to reorganize!")
        return 1

    print(f"Found {len(cv_names)} CVs to reorganize\n")

    total_files = 0
    for cv_name in cv_names:
        files_moved = reorganize_cv_files(cv_name)
        total_files += files_moved
        print()

    print(f"{'='*70}")
    print(f"COMPLETE!")
    print(f"{'='*70}")
    print(f"Reorganized {len(cv_names)} CVs")
    print(f"Moved/copied {total_files} files")
    print(f"New location: {NEW_OUTPUT_DIR}")
    print(f"{'='*70}\n")

    # Also move the processing report
    report_file = OLD_OUTPUT_DIR / "PHASE2_20CVS_PROCESSING_REPORT.md"
    if report_file.exists():
        shutil.copy2(str(report_file), str(NEW_OUTPUT_DIR / "PHASE2_20CVS_PROCESSING_REPORT.md"))
        print(f"✓ Copied processing report to {NEW_OUTPUT_DIR}")

    return 0

if __name__ == '__main__':
    exit(main())
