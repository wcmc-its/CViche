#!/usr/bin/env python3
"""Update M4 taxonomy codes to M2 codes in 2100_Mocco stage files."""

import json
import os

# Mapping M4 -> M2 based on status:
# M4A (active/ongoing trials) -> M2A
# M4B (completed trials) -> M2B
# M4C (pending trials) -> M2C
CODE_MAPPING = {
    'M4A': 'M2A',
    'M4B': 'M2B',
    'M4C': 'M2C',
    'M4': 'M2'  # Fallback
}

def update_codes_in_file(filepath):
    """Update M4 codes to M2 codes in a JSON file."""
    with open(filepath, 'r') as f:
        data = json.load(f)

    changes = 0

    def update_entry(entry):
        nonlocal changes
        if isinstance(entry, dict):
            for key, val in list(entry.items()):
                if key == 'taxonomy_code' and isinstance(val, str) and val.startswith('M4'):
                    old_code = val
                    new_code = CODE_MAPPING.get(val, val)
                    if old_code != new_code:
                        entry[key] = new_code
                        changes += 1
                        print(f"  Changed {old_code} -> {new_code}")
                elif isinstance(val, (dict, list)):
                    update_entry(val)
        elif isinstance(entry, list):
            for item in entry:
                update_entry(item)

    update_entry(data)

    if changes > 0:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Updated {changes} codes in {os.path.basename(filepath)}")
    else:
        print(f"No M4 codes found in {os.path.basename(filepath)}")

    return changes

if __name__ == '__main__':
    import sys
    # Get base path
    base_path = os.path.dirname(os.path.abspath(__file__))

    # Files to update
    files_to_update = [
        os.path.join(base_path, 'outputs/stage_3b_classified_entries/2100_Mocco_classified.json'),
        os.path.join(base_path, 'outputs/stage_4_field_extraction/2100_Mocco_fields.json'),
        os.path.join(base_path, 'outputs/stage_5_enrichment/2100_Mocco_enriched.json'),
        os.path.join(base_path, 'outputs/stage_5b_institution_enrichment/2100_Mocco_institution_enriched.json'),
        os.path.join(base_path, 'outputs/stage_5c_teaching_formatted/2100_Mocco_teaching_formatted.json'),
        os.path.join(base_path, 'outputs/stage_5d_citation_formatted/2100_Mocco_citation_formatted.json'),
    ]

    total_changes = 0
    for f in files_to_update:
        if os.path.exists(f):
            print(f"\nProcessing {f}...")
            total_changes += update_codes_in_file(f)
        else:
            print(f"\nSkipping {f} (not found)")

    print(f"\n=== Total: {total_changes} code changes made ===")
