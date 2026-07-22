"""Pass 3b hierarchy building for chunked Word segmentation.

Split out of word_chunked.py (#315 follow-up).
"""

from typing import Dict, List


def build_hierarchy(flat_groups: List[Dict]) -> List[Dict]:
    """
    Pass 3b: Build hierarchical structure from flat list of groups.

    Groups are organized by level markers:
    - Level 1 (L1) = top-level groups
    - Level 2 (L2) = subgroups of preceding L1
    - Level 3 (L3) = sub-subgroups of preceding L2

    Example:
        G1 [L1] Major Committee Assignments
        G2 [L2] Local         → becomes G1.1
        G3 [L2] National      → becomes G1.2
        G4 [L2] International → becomes G1.3
        G5 [L1] Awards        → new top-level

    Returns hierarchical list with proper nesting and renumbered IDs.
    """

    hierarchical = []
    current_l1 = None
    current_l2 = None
    subgroup_counter_l1 = {}  # Track subgroup numbering per L1 parent
    subgroup_counter_l2 = {}  # Track sub-subgroup numbering per L2 parent

    for group in flat_groups:
        level = group['level']

        if level == 1:
            # New top-level group
            current_l1 = group.copy()
            current_l1['subgroups'] = []
            hierarchical.append(current_l1)
            current_l2 = None
            subgroup_counter_l1[current_l1['id']] = 0

        elif level == 2:
            # Subgroup of current L1
            if current_l1 is None:
                # No parent L1, treat as top-level
                current_l1 = group.copy()
                current_l1['subgroups'] = []
                hierarchical.append(current_l1)
                current_l2 = None
                subgroup_counter_l1[current_l1['id']] = 0
            else:
                # Create subgroup
                # Initialize counter if not exists
                if current_l1['id'] not in subgroup_counter_l1:
                    subgroup_counter_l1[current_l1['id']] = 0
                subgroup_counter_l1[current_l1['id']] += 1
                subgroup_num = subgroup_counter_l1[current_l1['id']]

                subgroup = group.copy()
                # Renumber ID to show hierarchy: G1 → G1.1, G1.2, etc.
                parent_id = current_l1['id']
                subgroup['id'] = f"{parent_id}.{subgroup_num}"
                subgroup['parent_id'] = parent_id
                subgroup['subgroups'] = []  # For potential L3 children

                current_l1['subgroups'].append(subgroup)
                current_l2 = subgroup
                subgroup_counter_l2[subgroup['id']] = 0

        elif level == 3:
            # Sub-subgroup of current L2
            if current_l2 is None:
                # No parent L2, attach to L1 as L2 instead
                if current_l1 is None:
                    # No parents at all, treat as top-level
                    group_copy = group.copy()
                    group_copy['subgroups'] = []
                    hierarchical.append(group_copy)
                else:
                    # Initialize counter if not exists
                    if current_l1['id'] not in subgroup_counter_l1:
                        subgroup_counter_l1[current_l1['id']] = 0
                    subgroup_counter_l1[current_l1['id']] += 1
                    subgroup_num = subgroup_counter_l1[current_l1['id']]

                    subgroup = group.copy()
                    parent_id = current_l1['id']
                    subgroup['id'] = f"{parent_id}.{subgroup_num}"
                    subgroup['parent_id'] = parent_id
                    subgroup['subgroups'] = []

                    current_l1['subgroups'].append(subgroup)
                    current_l2 = subgroup
                    subgroup_counter_l2[subgroup['id']] = 0
            else:
                # Create sub-subgroup
                # Initialize counter if not exists
                if current_l2['id'] not in subgroup_counter_l2:
                    subgroup_counter_l2[current_l2['id']] = 0
                subgroup_counter_l2[current_l2['id']] += 1
                subsubgroup_num = subgroup_counter_l2[current_l2['id']]

                subsubgroup = group.copy()
                parent_id = current_l2['id']
                subsubgroup['id'] = f"{parent_id}.{subsubgroup_num}"
                subsubgroup['parent_id'] = parent_id
                subsubgroup['subgroups'] = []

                current_l2['subgroups'].append(subsubgroup)

        else:
            # Unexpected level, treat as top-level
            group_copy = group.copy()
            group_copy['subgroups'] = []
            hierarchical.append(group_copy)
            current_l1 = group_copy
            current_l2 = None

    return hierarchical
