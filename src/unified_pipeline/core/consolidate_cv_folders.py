#!/usr/bin/env python3
"""
Consolidate CV processing folders - merge multiple folders for the same CV.

Problem:
When CVs are processed in multiple stages at different times, each stage
creates a separate timestamped folder. This results in many folders per CV
with only 1-2 files each.

Solution:
1. Identify all folders for the same CV (ignoring timestamps and suffixes)
2. Consolidate all files into the most complete folder
3. Remove empty/partial folders

Example:
  2025-11-08_10-51_2006_Bush_preprocessed/     (1 file)
  2025-11-08_10-51_2006_Bush_segmented_repaired/ (1 file)
  2025-11-08_11-41_2006_Bush_METRICS/          (1 file)
  2025-11-09_10-16_2006_Bush/                  (5 files) ← KEEP & consolidate into this

  → Consolidate all into: 2025-11-09_10-16_2006_Bush/
"""

import os
import re
import shutil
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs/cv_processing"

def extract_cv_name(folder_name: str):
    """
    Extract the base CV name from a folder name.

    Examples:
        2025-11-09_10-16_2006_Bush              → 2006_Bush
        2025-11-08_10-51_2006_Bush_preprocessed → 2006_Bush
        2025-11-08_11-41_2006_Bush_METRICS      → 2006_Bush
        2025-11-09_08-39_2001_Ut_Format         → 2001_Ut_Format
    """
    # Remove timestamp prefix (YYYY-MM-DD_HH-MM_)
    pattern = r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}_(.+)$'
    match = re.match(pattern, folder_name)

    if not match:
        return None

    name_part = match.group(1)

    # Remove common suffixes
    suffixes_to_remove = [
        '_preprocessed',
        '_segmented_repaired',
        '_METRICS',
        '_with_signals_v',
        '_repaired',
        '_segmented',
        '_mapped'
    ]

    for suffix in suffixes_to_remove:
        if name_part.endswith(suffix):
            name_part = name_part[:-len(suffix)]
            break

    return name_part


def get_folder_quality_score(folder_path: Path):
    """
    Calculate a quality score for a folder based on completeness.

    Higher score = more complete and recent

    Score components:
    - +10 for each expected file type (.docx, _segmented, _repaired, _preprocessed, _mapped)
    - +1 for being more recent (timestamp)
    """
    score = 0

    # Count expected file types
    has_docx = any(f.suffix == '.docx' for f in folder_path.iterdir() if f.is_file())
    has_segmented = any('_segmented.json' in f.name for f in folder_path.iterdir() if f.is_file())
    has_repaired = any('_repaired.json' in f.name for f in folder_path.iterdir() if f.is_file())
    has_preprocessed = any('_preprocessed.json' in f.name for f in folder_path.iterdir() if f.is_file())
    has_mapped = any('_mapped.json' in f.name for f in folder_path.iterdir() if f.is_file())

    score += 10 if has_docx else 0
    score += 10 if has_segmented else 0
    score += 10 if has_repaired else 0
    score += 10 if has_preprocessed else 0
    score += 10 if has_mapped else 0

    # Add timestamp component (more recent = higher score, but lower weight than completeness)
    # Extract timestamp from folder name
    folder_name = folder_path.name
    timestamp_match = re.match(r'^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})_', folder_name)
    if timestamp_match:
        timestamp_str = timestamp_match.group(1)
        try:
            dt = datetime.strptime(timestamp_str, '%Y-%m-%d_%H-%M')
            # Add fractional score based on days since epoch (normalized)
            days_since_epoch = (dt - datetime(1970, 1, 1)).days
            score += days_since_epoch * 0.001  # Very small weight
        except:
            pass

    return score


def consolidate_folders():
    """Main consolidation function."""

    print(f"{'='*70}")
    print("CONSOLIDATING CV PROCESSING FOLDERS")
    print(f"{'='*70}")
    print(f"Directory: {OUTPUT_DIR}")
    print(f"{'='*70}\n")

    # Group folders by CV name
    cv_folders = defaultdict(list)

    for folder_path in OUTPUT_DIR.iterdir():
        if not folder_path.is_dir():
            continue

        cv_name = extract_cv_name(folder_path.name)
        if cv_name:
            cv_folders[cv_name].append(folder_path)

    print(f"Found {len(cv_folders)} unique CVs\n")

    # Process each CV
    total_consolidated = 0
    total_removed = 0

    for cv_name in sorted(cv_folders.keys()):
        folders = cv_folders[cv_name]

        if len(folders) <= 1:
            # Only one folder, skip
            continue

        print(f"\n{cv_name}:")
        print(f"  Found {len(folders)} folders")

        # Score each folder
        folder_scores = []
        for folder in folders:
            score = get_folder_quality_score(folder)
            file_count = len([f for f in folder.iterdir() if f.is_file()])
            folder_scores.append((folder, score, file_count))

        # Sort by score (highest first)
        folder_scores.sort(key=lambda x: x[1], reverse=True)

        # Best folder is the target
        target_folder, target_score, target_files = folder_scores[0]
        print(f"  Target folder: {target_folder.name} (score: {target_score:.1f}, {target_files} files)")

        # Consolidate files from other folders
        files_moved = 0
        for source_folder, score, file_count in folder_scores[1:]:
            print(f"  Consolidating: {source_folder.name} (score: {score:.1f}, {file_count} files)")

            for file_path in source_folder.iterdir():
                if not file_path.is_file():
                    continue

                dest_path = target_folder / file_path.name

                # Only move if file doesn't exist in target
                if not dest_path.exists():
                    shutil.move(str(file_path), str(dest_path))
                    print(f"    ✓ Moved {file_path.name}")
                    files_moved += 1
                else:
                    # File exists, compare sizes to decide if we should replace
                    if file_path.stat().st_size > dest_path.stat().st_size:
                        # Source is larger, replace
                        dest_path.unlink()
                        shutil.move(str(file_path), str(dest_path))
                        print(f"    ✓ Replaced {file_path.name} (larger version)")
                        files_moved += 1
                    else:
                        print(f"    ⊘ Skipped {file_path.name} (already exists)")

            # Remove empty source folder
            if not any(source_folder.iterdir()):
                source_folder.rmdir()
                print(f"    ✓ Removed empty folder: {source_folder.name}")
                total_removed += 1

        if files_moved > 0:
            total_consolidated += 1

    print(f"\n{'='*70}")
    print(f"CONSOLIDATION COMPLETE")
    print(f"{'='*70}")
    print(f"CVs consolidated: {total_consolidated}")
    print(f"Folders removed: {total_removed}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"{'='*70}\n")

    return 0


if __name__ == '__main__':
    exit(consolidate_folders())
