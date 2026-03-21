"""
Preprocess Segmented CV - Filter and Mark Empty Groups

Handles empty structural fragments before taxonomy mapping and extraction.

Empty groups are artifacts from segmentation (title headers, institution headers,
indentation markers). By default, this module PRESERVES them as marked structural
headers for use as fallback context during extraction recovery.

Modes:
  1. PRESERVE (default): Mark empty groups as 'is_structural_header=True'
     - Skips classification (saves API calls)
     - Available as context for recovery when extraction fails
     - Example: "Faculty of Health Sciences" provides institutional context

  2. REMOVE: Delete empty groups entirely
     - Cleaner data structure
     - No fallback context available

Usage:
    # Default: Preserve headers for recovery context
    python preprocess_segmented_cv.py input_segmented.json output_cleaned.json

    # Or as module:
    from preprocess_segmented_cv import filter_valid_groups
    cleaned_cv = filter_valid_groups(segmented_cv, preserve_headers=True)
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any


def is_valid_group(group: Dict[str, Any]) -> bool:
    """
    Check if a group has content worth processing downstream.

    Args:
        group: Group dictionary from segmented CV

    Returns:
        True if group has entries or subgroups, False if empty
    """
    entries = group.get('entries', [])
    subgroups = group.get('subgroups', [])

    # Group is valid if it has any entries or any subgroups
    return len(entries) > 0 or len(subgroups) > 0


def filter_valid_groups(segmented_cv: Dict[str, Any], verbose: bool = True,
                       preserve_headers: bool = True) -> Dict[str, Any]:
    """
    Filter out empty groups from segmented CV, optionally preserving as structural headers.

    Args:
        segmented_cv: Segmented CV dictionary with 'groups' list
        verbose: Print filtering statistics
        preserve_headers: If True, mark empty groups as structural headers instead of removing

    Returns:
        Cleaned CV dictionary with valid groups (and optionally marked headers)
    """
    original_groups = segmented_cv.get('groups', [])

    # Recursively filter groups and subgroups
    def filter_recursive(groups: List[Dict]) -> List[Dict]:
        """Recursively filter groups, preserving hierarchy."""
        valid_groups = []

        for group in groups:
            # First, recursively filter subgroups
            if 'subgroups' in group and group['subgroups']:
                group['subgroups'] = filter_recursive(group['subgroups'])

            # Check if this group is valid (has entries or valid subgroups)
            if is_valid_group(group):
                valid_groups.append(group)
            elif preserve_headers:
                # Mark as structural header for fallback context
                group['is_structural_header'] = True
                group['skip_classification'] = True
                group['meta'] = group.get('meta', {})
                group['meta']['preserved_as_header'] = True
                group['meta']['reason'] = 'Empty group preserved for recovery context'
                valid_groups.append(group)

        return valid_groups

    # Filter groups
    valid_groups = filter_recursive(original_groups)

    # Build filtered CV
    filtered_cv = {
        **segmented_cv,
        'groups': valid_groups
    }

    # Update metadata
    original_count = len(original_groups)
    filtered_count = len(valid_groups)

    # Count preserved headers
    preserved_headers_count = sum(1 for g in valid_groups if g.get('is_structural_header', False))
    content_groups_count = filtered_count - preserved_headers_count
    removed_count = original_count - filtered_count
    removal_pct = (removed_count / original_count * 100) if original_count > 0 else 0

    if 'meta' not in filtered_cv:
        filtered_cv['meta'] = {}

    filtered_cv['meta']['preprocessing'] = {
        'original_groups': original_count,
        'total_groups_after': filtered_count,
        'content_groups': content_groups_count,
        'preserved_headers': preserved_headers_count,
        'removed_groups': removed_count,
        'removal_percentage': round(removal_pct, 1),
        'preserve_headers_enabled': preserve_headers,
        'filter_criteria': 'groups with 0 entries and 0 subgroups'
    }

    if verbose:
        print("="*80)
        print("PREPROCESSING: FILTER EMPTY GROUPS")
        print("="*80)
        print(f"Original groups: {original_count}")
        print(f"Content groups: {content_groups_count}")
        if preserve_headers:
            print(f"Preserved headers: {preserved_headers_count} (for recovery context)")
            print(f"Total after processing: {filtered_count}")
        else:
            print(f"Removed empty: {removed_count} ({removal_pct:.1f}%)")
        print()

    return filtered_cv


def get_removed_groups(segmented_cv: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get list of groups that would be removed by filtering.

    Useful for debugging and auditing.

    Args:
        segmented_cv: Segmented CV dictionary

    Returns:
        List of groups that would be filtered out
    """
    removed = []

    def collect_removed(groups: List[Dict], parent_path: str = ""):
        """Recursively collect removed groups."""
        for group in groups:
            group_label = group.get('label_inferred', group.get('label', 'Unknown'))
            group_id = group.get('id', 'Unknown')
            path = f"{parent_path}/{group_label}" if parent_path else group_label

            # Check subgroups first
            if 'subgroups' in group and group['subgroups']:
                collect_removed(group['subgroups'], path)

            # Check if this group would be removed
            if not is_valid_group(group):
                removed.append({
                    'id': group_id,
                    'label': group_label,
                    'path': path,
                    'entries_count': len(group.get('entries', [])),
                    'subgroups_count': len(group.get('subgroups', []))
                })

    collect_removed(segmented_cv.get('groups', []))
    return removed


def main():
    """Command-line interface for preprocessing."""
    if len(sys.argv) < 2:
        print("Preprocess Segmented CV - Filter and Mark Empty Groups")
        print()
        print("Usage: python preprocess_segmented_cv.py <input_segmented.json> [output_cleaned.json]")
        print()
        print("By default, PRESERVES empty groups as structural headers for recovery context.")
        print("Structural headers are marked with 'is_structural_header=True' and skipped")
        print("during classification (saves API calls) but available for extraction recovery.")
        print()
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])
    else:
        # Default: add "_cleaned" before extension
        output_path = input_path.parent / (input_path.stem + '_cleaned.json')

    # Load segmented CV
    with open(input_path, 'r') as f:
        segmented_cv = json.load(f)

    print(f"Input: {input_path}")
    print()

    # Show what will be removed (for audit)
    removed_groups = get_removed_groups(segmented_cv)
    if removed_groups:
        print("GROUPS TO BE REMOVED:")
        for group in removed_groups[:10]:  # Show first 10
            print(f"  • {group['id']}: {group['label']} ({group['entries_count']} entries, {group['subgroups_count']} subgroups)")
        if len(removed_groups) > 10:
            print(f"  ... and {len(removed_groups) - 10} more")
        print()

    # Filter
    filtered_cv = filter_valid_groups(segmented_cv, verbose=True)

    # Save
    with open(output_path, 'w') as f:
        json.dump(filtered_cv, f, indent=2)

    print(f"✓ Cleaned CV saved to: {output_path}")
    print()

    # Show statistics
    stats = filtered_cv['meta']['preprocessing']
    print("STATISTICS:")

    if stats['preserve_headers_enabled']:
        api_calls_saved = stats['preserved_headers']
        print(f"  Content groups for classification: {stats['content_groups']}")
        print(f"  Structural headers preserved: {stats['preserved_headers']}")
        print(f"  API calls saved (headers skipped): {api_calls_saved}")
        print(f"  Token cost reduction: ~{api_calls_saved * 1200} tokens")
        print(f"  Classification efficiency: +{(api_calls_saved / stats['original_groups'] * 100):.1f}%")
        print(f"  ✓ Headers available for recovery context")
    else:
        print(f"  API calls saved: {stats['removed_groups']}")
        print(f"  Token cost reduction: ~{stats['removed_groups'] * 1200} tokens")
        print(f"  Classification efficiency: +{stats['removal_percentage']:.1f}%")


if __name__ == '__main__':
    main()
