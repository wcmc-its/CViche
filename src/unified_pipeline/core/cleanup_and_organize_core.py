#!/usr/bin/env python3
"""
Clean up core directory and organize CV files into standard output structure.

This script:
1. Finds all CV-related files in src/unified_pipeline/core/
2. Groups them by CV name
3. Moves them to outputs/cv_processing/ with timestamp-based folders
4. Archives non-CV files to outputs/archive/

STANDARD OUTPUT LOCATION:
    outputs/cv_processing/YYYY-MM-DD_HH-MM_CV_NAME/

Files that will be organized:
    - validation_CV_*.json (various processing stages)
    - CV_*.json (various processing stages)
    - Any JSON file with recognizable CV name patterns

Non-CV files will be moved to:
    outputs/archive/core_cleanup_YYYY-MM-DD/
"""

import os
import re
import shutil
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CORE_DIR = PROJECT_ROOT / "src/unified_pipeline/core"
OUTPUT_DIR = PROJECT_ROOT / "outputs/cv_processing"
ARCHIVE_DIR = PROJECT_ROOT / "outputs/archive" / f"core_cleanup_{datetime.now().strftime('%Y-%m-%d_%H-%M')}"
DOCX_SOURCE_DIR = PROJECT_ROOT / "data/sample_cvs/word"

# CV name patterns to recognize
CV_PATTERNS = [
    r'validation_CV_(\d{4})_([A-Za-z_]+)',  # validation_CV_2002_Holtz
    r'CV_(\d{4})_([A-Za-z_]+)',  # CV_2022_Afifi
    r'(\d{4})_([A-Za-z_]+)_Cv',  # 2074_Blakely_Cv
]


def extract_cv_info(filename: str):
    """
    Extract CV year and name from filename.

    Returns: (year, name) or None if not a CV file
    """
    for pattern in CV_PATTERNS:
        match = re.match(pattern, filename)
        if match:
            year, name = match.groups()
            # Normalize name (remove underscores, keep original case for matching)
            return year, name
    return None


def get_file_timestamp(file_path: Path):
    """Get file modification time as formatted string."""
    if file_path.exists():
        mtime = os.path.getmtime(file_path)
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime("%Y-%m-%d_%H-%M")
    return datetime.now().strftime("%Y-%m-%d_%H-%M")


def categorize_files():
    """Categorize files in core directory."""
    cv_files = defaultdict(list)  # cv_key -> list of files
    non_cv_files = []

    # Get all JSON files in core
    for file_path in CORE_DIR.glob("*.json"):
        filename = file_path.stem

        cv_info = extract_cv_info(filename)
        if cv_info:
            year, name = cv_info
            cv_key = f"{year}_{name}"
            cv_files[cv_key].append(file_path)
        else:
            # Not a CV file
            non_cv_files.append(file_path)

    return cv_files, non_cv_files


def find_original_docx(cv_key: str):
    """Try to find the original .docx file for a CV."""
    # Try various naming patterns
    patterns = [
        f"{cv_key}.docx",
        f"{cv_key}_Cv.docx",
        f"{cv_key.replace('_', '')}.docx",
    ]

    for pattern in patterns:
        docx_path = DOCX_SOURCE_DIR / pattern
        if docx_path.exists():
            return docx_path

    # Try case-insensitive search
    for docx_file in DOCX_SOURCE_DIR.glob("*.docx"):
        if cv_key.lower() in docx_file.stem.lower():
            return docx_file

    return None


def organize_cv_files(cv_key: str, files: list):
    """Organize all files for a single CV."""
    # Get timestamp from oldest file
    oldest_file = min(files, key=lambda f: os.path.getmtime(f))
    timestamp = get_file_timestamp(oldest_file)

    # Create CV folder
    cv_folder = OUTPUT_DIR / f"{timestamp}_{cv_key}"
    cv_folder.mkdir(parents=True, exist_ok=True)

    print(f"Organizing {cv_key}...")
    print(f"  → {cv_folder.name}/")

    files_moved = 0

    # Move all JSON files
    for file_path in sorted(files):
        dest = cv_folder / file_path.name
        shutil.move(str(file_path), str(dest))
        print(f"    ✓ Moved {file_path.name}")
        files_moved += 1

    # Try to copy original .docx
    docx_source = find_original_docx(cv_key)
    if docx_source:
        docx_dest = cv_folder / docx_source.name
        if not docx_dest.exists():  # Don't overwrite if already there
            shutil.copy2(str(docx_source), str(docx_dest))
            print(f"    ✓ Copied {docx_source.name} (original)")
            files_moved += 1

    return files_moved


def archive_non_cv_files(files: list):
    """Archive non-CV files."""
    if not files:
        return 0

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nArchiving {len(files)} non-CV files to:")
    print(f"  {ARCHIVE_DIR}/")

    files_moved = 0
    for file_path in sorted(files):
        dest = ARCHIVE_DIR / file_path.name
        shutil.move(str(file_path), str(dest))
        print(f"    ✓ Archived {file_path.name}")
        files_moved += 1

    return files_moved


def main():
    print(f"{'='*70}")
    print("CLEANING UP AND ORGANIZING CORE DIRECTORY")
    print(f"{'='*70}")
    print(f"Source: {CORE_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Archive: {ARCHIVE_DIR}")
    print(f"{'='*70}\n")

    # Categorize files
    cv_files, non_cv_files = categorize_files()

    if not cv_files and not non_cv_files:
        print("No files to organize!")
        return 0

    print(f"Found {len(cv_files)} unique CVs with files")
    print(f"Found {len(non_cv_files)} non-CV files\n")

    # Organize CV files
    total_cv_files = 0
    for cv_key in sorted(cv_files.keys()):
        files_moved = organize_cv_files(cv_key, cv_files[cv_key])
        total_cv_files += files_moved
        print()

    # Archive non-CV files
    total_archived = archive_non_cv_files(non_cv_files)

    print(f"\n{'='*70}")
    print(f"CLEANUP COMPLETE!")
    print(f"{'='*70}")
    print(f"Organized {len(cv_files)} CVs ({total_cv_files} files)")
    print(f"Archived {total_archived} non-CV files")
    print(f"CV outputs: {OUTPUT_DIR}")
    print(f"Archive: {ARCHIVE_DIR}")
    print(f"{'='*70}\n")

    return 0


if __name__ == '__main__':
    exit(main())
