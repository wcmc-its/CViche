#!/usr/bin/env python3
"""
Stage 3: Taxonomy Mapping

Maps Stage 2 entries to WCM taxonomy using hierarchical context.
Integrates with existing taxonomy_mapper_v2.py system (validators, confusion matrix, multi-pass classification).

Input: Stage 2 entries JSON (hierarchy-aware entries with text)
Output: Taxonomy-mapped entries with codes, labels, confidence, validation flags

Flow:
1. Load Stage 2 entries
2. Group by top-level section (hierarchy[0])
3. Pass 1: Section → Parent Code (A-T)
4. Router: Check if Pass 2 needed (has_subsections + confidence)
5. Pass 2: Entries → Child Codes (batched)
6. Pass 2.5: Apply validators
7. Save mapped output with cost tracking
"""

import sys
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple
from collections import defaultdict

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from core.output_manager import OutputManager
from core.taxonomy_mapper_v2 import (
    classify_pass1_parent,
    classify_pass2_batch,
    classify_with_surfaced_candidates,  # V10: Guided classification
    refine_parent_to_child,              # V10: Auto-refinement
    calculate_cost
)
from core.candidate_surfacer import (     # V10: Candidate surfacing
    surface_candidates_for_subsection,
    extract_parent_code,
    TAXONOMY_CODES_CONDENSED
)
from core.valid_taxonomy_codes import (
    has_subsections,
    validate_taxonomy_code,
    get_valid_children
)
from core.disambiguation_validators import run_all_validators
from core.hierarchy_overrides import apply_hierarchy_overrides


def group_entries_by_section(entries: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group Stage 2 entries by top-level section (hierarchy[0]).

    Args:
        entries: List of Stage 2 entry dicts

    Returns:
        Dict mapping section_header → list of entries
    """
    sections = defaultdict(list)

    for entry in entries:
        hierarchy = entry.get("hierarchy", [])
        if not hierarchy:
            # No hierarchy - put in "Unknown" section
            section_key = "Unknown"
        else:
            # Use first level as section key
            section_key = hierarchy[0]

        sections[section_key].append(entry)

    return dict(sections)


def is_empty_text(text: str) -> bool:
    """
    Check if text is empty or contains only whitespace.

    Args:
        text: Text to check

    Returns:
        True if empty or whitespace-only
    """
    return not text or text.strip() == ""


def extract_subsection_header(hierarchy: List[str]) -> str:
    """
    Extract subsection header from hierarchy.

    For hierarchy like ["Education", "Graduate", "Ph.D."],
    returns "Ph.D." (most specific level).

    For single-level hierarchy ["Education"], returns "".
    """
    if len(hierarchy) > 1:
        return hierarchy[-1]
    return ""


def group_entries_by_subsection(section_entries: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group section entries by subsection (second level of hierarchy).

    Args:
        section_entries: List of entries from the same top-level section

    Returns:
        Dict mapping subsection_header → list of entries
    """
    subsections = defaultdict(list)

    for entry in section_entries:
        hierarchy = entry.get("hierarchy", [])
        if len(hierarchy) > 1:
            # Use second level as subsection key
            subsection_key = hierarchy[1]
        else:
            # No subsection - use "main"
            subsection_key = "(main)"

        subsections[subsection_key].append(entry)

    return dict(subsections)


def correct_blog_media_classification(entry: Dict) -> Dict:
    """
    Post-classification correction for blogs and other media (S8 → S9).

    Issue #3 Fix: Blogs/podcasts/videos were being misclassified as S8 (Abstracts)
    but should be S9 (Other Media).

    Args:
        entry: Mapped entry with taxonomy_code

    Returns:
        Entry with corrected taxonomy_code if needed
    """
    import re

    code = entry.get('taxonomy_code', '')
    text = entry.get('text', '').lower()

    # Only correct S8 entries that clearly contain blog/media keywords
    if code == 'S8':
        # Media keywords that indicate S9 not S8
        media_patterns = [
            r'\bblog\b', r'\bblog\s+post\b', r'\bblog\s+for\b',
            r'\bpodcast\b', r'\bvideo\b', r'\bwebinar\b',
            r'\byoutube\b', r'\bvimeo\b',
            r'\bmedia\s+interview\b', r'\bnews\s+article\b'
        ]

        if any(re.search(pattern, text) for pattern in media_patterns):
            # Correct to S9
            entry['taxonomy_code'] = 'S9'
            entry['taxonomy_label'] = 'Other Media (Podcasts, Blogs, Videos)'
            entry['classification_method'] = 'pass2_corrected_s8_to_s9'
            print(f"      🔧 AUTO-CORRECTED: S8 → S9 (detected media keyword in: '{text[:80]}...')")

    return entry


def map_personal_data_section(entries: List[Dict]) -> List[Dict]:
    """
    Special handler for "Personal Data" section.
    Maps directly to A (Personal Data) with subsections A1/A2/A3.

    Uses pattern matching:
    - Name detection → A1
    - Email detection → A2
    - Phone/address detection → A3

    Args:
        entries: List of Personal Data entries

    Returns:
        List of mapped entries
    """
    import re

    mapped = []

    for entry in entries:
        text = entry.get("text", "").lower()

        # Detect subsection by pattern matching
        if re.search(r'\b(email|e-mail)\b', text) or '@' in text:
            child_code = "A2"
            child_label = "Email"
            confidence = 0.98
        elif re.search(r'\b(phone|tel|mobile|cell)\b', text) or re.search(r'\d{3}[-.)]\d{3}', text):
            child_code = "A3"
            child_label = "Phone/Address"
            confidence = 0.98
        elif re.search(r'\bcurriculum vitae\b', text) or re.search(r'\b(dr\.|m\.d\.|ph\.d\.)', text):
            child_code = "A1"
            child_label = "Name/Title"
            confidence = 0.95
        else:
            # Default to A1 for other personal info
            child_code = "A1"
            child_label = "Name/Title"
            confidence = 0.85

        mapped_entry = {
            **entry,  # Preserve all original fields
            "taxonomy_code": child_code,
            "taxonomy_label": child_label,
            "parent_code": "A",
            "parent_label": "Personal Data",
            "confidence": confidence,
            "validation_flags": [],
            "classification_method": "pattern_matching"
        }
        mapped.append(mapped_entry)

    return mapped


def run_stage_3(docx_path: str, entries_json_path: str = None, mode: str = "two-pass"):
    """
    Main Stage 3: Map entries to WCM taxonomy

    Args:
        docx_path: Path to Word document (for context)
        entries_json_path: Optional path to Stage 2b entries JSON
        mode: Classification mode - "two-pass" or "guided" (V10)
            - "two-pass": Constrained classification (Pass 1 → Pass 2 with ENUM)
            - "guided": LLM-surfaced guided classification (semantic analysis)
    """

    print("="*80)
    print(f"STAGE 3: TAXONOMY MAPPING ({mode.upper()} MODE)")
    print("="*80)
    print(f"Input: {docx_path}")
    print()

    # Setup
    om = OutputManager(docx_path)

    # Load Stage 2b entries
    if entries_json_path is None:
        entries_json_path = om.get_stage2b_path()

    print(f"Loading entries: {entries_json_path}")
    with open(entries_json_path) as f:
        entries_data = json.load(f)

    entries = entries_data.get("entries", [])
    document_uid = entries_data.get("document_uid")

    print(f"Loaded {len(entries)} entries from Stage 2b")
    print()

    # Group entries by top-level section
    sections = group_entries_by_section(entries)

    print(f"Grouped into {len(sections)} sections:")
    for section_name, section_entries in sections.items():
        print(f"  - {section_name}: {len(section_entries)} entries")
    print()

    # Process each section
    all_mapped_entries = []
    total_cost = 0.0
    total_tokens = 0
    pass1_calls = 0
    pass2_calls = 0

    # ========================================================================
    # MODE ROUTING: Two-Pass vs Guided Classification
    # ========================================================================

    if mode == "guided":
        # ====================================================================
        # GUIDED MODE (V10): LLM-surfaced candidate classification
        # ====================================================================
        print("Using GUIDED classification mode (V10 architecture)")
        print("  → Surfacing candidates based on CV structure (headers + samples)")
        print("  → Guided classification with no ENUM constraints")
        print()

        # Group all entries by subsection across all sections
        all_subsections = []
        for section_header, section_entries in sections.items():
            subsections = group_entries_by_subsection(section_entries)
            for subsection_header, subsection_entries in subsections.items():
                all_subsections.append({
                    "section_header": section_header,
                    "subsection_header": subsection_header,
                    "entries": subsection_entries
                })

        print(f"Processing {len(all_subsections)} subsections across all sections")
        print()

        guided_calls = 0
        surfacing_calls = 0
        refinement_calls = 0
        escape_hatch_usage = 0

        for subsection_idx, subsection_data in enumerate(all_subsections, 1):
            section_header = subsection_data["section_header"]
            subsection_header = subsection_data["subsection_header"]
            subsection_entries = subsection_data["entries"]

            print(f"[{subsection_idx}/{len(all_subsections)}] {section_header} → {subsection_header}")
            print(f"  Entries: {len(subsection_entries)}")

            # Special handling for Personal Data section
            if section_header == "Personal Data":
                print("  → Using pattern matching for Personal Data")
                mapped = map_personal_data_section(subsection_entries)
                all_mapped_entries.extend(mapped)
                print(f"  ✓ Mapped {len(mapped)} entries (pattern matching, $0.00)")
                print()
                continue

            # Separate entries with text from empty entries
            entries_with_text = []
            empty_entries = []
            entry_index_map = {}  # Maps valid entry index → original entry index

            for i, entry in enumerate(subsection_entries):
                text = entry.get("text", "")
                if is_empty_text(text):
                    empty_entries.append((i, entry))
                else:
                    entry_index_map[len(entries_with_text)] = i
                    entries_with_text.append(entry)

            if empty_entries:
                print(f"  → Skipping {len(empty_entries)} empty entries")

            # Only classify entries that have text
            if not entries_with_text:
                print(f"  → All entries empty, skipping classification")
                for i, entry in enumerate(subsection_entries):
                    mapped_entry = {
                        **entry,
                        "taxonomy_code": "T",
                        "taxonomy_label": "Other",
                        "parent_code": "T",
                        "parent_label": "Other",
                        "confidence": 0.0,
                        "validation_flags": [],
                        "classification_method": "empty_text_skipped"
                    }
                    all_mapped_entries.append(mapped_entry)
                print()
                continue

            # Step 1: Surface candidates for this subsection
            print(f"  Step 1: Surfacing candidates...")

            # Adaptive sampling: Scale sample size with subsection size
            # This ensures LLM sees diverse content, especially when entries are chronologically ordered
            num_entries = len(entries_with_text)

            if num_entries < 10:
                # Small subsection: send all entries
                sample_indices = list(range(num_entries))
                sampling_strategy = "all"
            elif num_entries <= 30:
                # Medium subsection: first 5 + last 5
                sample_indices = list(range(5)) + list(range(num_entries - 5, num_entries))
                sampling_strategy = "first5+last5"
            else:
                # Large subsection: first 5 + last 5 + 5 evenly-spaced middle entries
                middle_start = 5
                middle_end = num_entries - 5
                middle_step = (middle_end - middle_start) // 5
                middle_indices = [middle_start + i * middle_step for i in range(5)]
                sample_indices = list(range(5)) + middle_indices + list(range(num_entries - 5, num_entries))
                sampling_strategy = "first5+middle5+last5"

            # Get sample entries with deduplication to ensure unique samples
            raw_samples = [entries_with_text[i].get("text", "") for i in sample_indices if i < num_entries]
            # Deduplicate while preserving order
            seen = set()
            sample_entries = []
            for entry in raw_samples:
                if entry and entry not in seen:
                    seen.add(entry)
                    sample_entries.append(entry)

            print(f"    → Sampling: {len(sample_entries)}/{num_entries} entries ({sampling_strategy})")

            surfacing_result = surface_candidates_for_subsection(
                section_header=section_header,
                subsection_header=subsection_header,
                sample_entries=sample_entries,
                model="gpt-5.1",
                max_candidates=12
            )
            surfacing_calls += 1

            # Track surfacing cost
            surfacing_tokens = surfacing_result.get("token_usage", {})
            surfacing_cost = calculate_cost(
                surfacing_tokens.get("prompt_tokens", 0),
                surfacing_tokens.get("completion_tokens", 0),
                "gpt-5.1"
            )
            total_cost += surfacing_cost
            total_tokens += surfacing_tokens.get("total_tokens", 0)

            primary_candidates = surfacing_result.get("primary_candidates", [])
            secondary_candidates = surfacing_result.get("secondary_candidates", [])

            print(f"    → Surfaced {len(primary_candidates)} primary + {len(secondary_candidates)} secondary")
            print(f"    → Cost: ${surfacing_cost:.4f}")

            # Check for cross-category override detection
            if surfacing_result.get("override_detected"):
                override_info = surfacing_result["override_detected"]
                print(f"    🔀 CROSS-CATEGORY OVERRIDE: {override_info.get('from_parent')} → {override_info.get('to_parent')}")
                print(f"       Reason: {override_info.get('reason', 'N/A')}")

            # Step 2: Classify entries using surfaced candidates (BATCHED)
            print(f"  Step 2: Classifying {len(entries_with_text)} entries...")

            entry_texts = [e.get("text", "") for e in entries_with_text]

            # CRITICAL FIX: Batch entries to avoid gpt-5.1 incomplete response issue
            # gpt-5.1 can only reliably return ~10 classifications per call
            BATCH_SIZE = 10
            classifications = []
            total_classification_cost = 0.0
            total_classification_tokens = 0
            total_escape_usage = 0

            num_batches = (len(entry_texts) + BATCH_SIZE - 1) // BATCH_SIZE

            if num_batches > 1:
                print(f"    → Processing in {num_batches} batches of {BATCH_SIZE}")

            for batch_idx in range(num_batches):
                start_idx = batch_idx * BATCH_SIZE
                end_idx = min((batch_idx + 1) * BATCH_SIZE, len(entry_texts))
                batch_texts = entry_texts[start_idx:end_idx]

                classification_result = classify_with_surfaced_candidates(
                    entries=batch_texts,
                    primary_candidates=primary_candidates,
                    secondary_candidates=secondary_candidates,
                    hierarchy=list(TAXONOMY_CODES_CONDENSED.keys()),
                    model="gpt-5.1"
                )
                guided_calls += 1

                # Track classification cost
                classification_tokens = classification_result.get("token_usage", {})
                batch_cost = calculate_cost(
                    classification_tokens.get("prompt_tokens", 0),
                    classification_tokens.get("completion_tokens", 0),
                    "gpt-5.1"
                )
                total_classification_cost += batch_cost
                total_classification_tokens += classification_tokens.get("total_tokens", 0)

                batch_classifications = classification_result.get("classifications", [])
                batch_escape_usage = classification_result.get("escape_hatch_usage", 0)
                total_escape_usage += batch_escape_usage

                # Safety check: LLM may return wrong number of classifications
                if len(batch_classifications) != len(batch_texts):
                    print(f"    ⚠️  WARNING: Batch {batch_idx+1} - LLM returned {len(batch_classifications)} classifications for {len(batch_texts)} entries!")
                    print(f"    → Padding with T/0.0 for missing entries")
                    # Pad with T/0.0 instead of truncating
                    while len(batch_classifications) < len(batch_texts):
                        batch_classifications.append({
                            "taxonomy_code": "T",
                            "taxonomy_label": "Other",
                            "confidence": 0.0,
                            "reasoning": "LLM incomplete response - padded"
                        })

                classifications.extend(batch_classifications)

            # Update totals
            total_cost += total_classification_cost
            total_tokens += total_classification_tokens
            escape_hatch_usage += total_escape_usage

            # Calculate overall statistics
            avg_confidence = sum(c.get("confidence", 0.0) for c in classifications) / len(classifications) if classifications else 0.0

            print(f"    → Avg confidence: {avg_confidence:.3f}")
            print(f"    → Escape hatch usage: {total_escape_usage}/{len(classifications)}")
            print(f"    → Cost: ${total_classification_cost:.4f}")

            # Step 3: Check for parent-only codes and auto-refine
            refined_classifications = []
            refinement_cost = 0.0

            for i, classification in enumerate(classifications):
                code = classification.get("taxonomy_code", "T")

                # Check if this is a parent-only code (single letter, not in child list)
                if len(code) == 1 and code != "T":
                    # This is a parent code - needs refinement
                    print(f"    → Auto-refining parent code '{code}' for entry {i+1}...")

                    entry_text = entry_texts[i]
                    refinement_result = refine_parent_to_child(
                        entry_text=entry_text,
                        parent_code=code,
                        hierarchy=list(TAXONOMY_CODES_CONDENSED.keys()),
                        model="gpt-5.1"
                    )
                    refinement_calls += 1

                    # Track refinement cost
                    refinement_tokens = refinement_result.get("token_usage", {})
                    refine_cost = calculate_cost(
                        refinement_tokens.get("prompt_tokens", 0),
                        refinement_tokens.get("completion_tokens", 0),
                        "gpt-5.1"
                    )
                    refinement_cost += refine_cost
                    total_cost += refine_cost
                    total_tokens += refinement_tokens.get("total_tokens", 0)

                    if refinement_result.get("success"):
                        refined_code = refinement_result.get("taxonomy_code", code)
                        refined_label = refinement_result.get("taxonomy_label", classification.get("taxonomy_label", ""))
                        print(f"       → Refined: {code} → {refined_code}")

                        refined_classifications.append({
                            "taxonomy_code": refined_code,
                            "taxonomy_label": refined_label,
                            "confidence": refinement_result.get("confidence", 0.5),
                            "reasoning": f"Auto-refined from {code}: {refinement_result.get('reasoning', '')}"
                        })
                    else:
                        print(f"       → Refinement failed, keeping {code}")
                        refined_classifications.append(classification)
                else:
                    refined_classifications.append(classification)

            if refinement_calls > 0:
                print(f"    → Refinement cost: ${refinement_cost:.4f}")

            # Merge classifications back with empty entries in original order
            all_classifications = [None] * len(subsection_entries)

            # Place classifications for valid entries
            for valid_idx, orig_idx in entry_index_map.items():
                if valid_idx < len(refined_classifications):
                    all_classifications[orig_idx] = refined_classifications[valid_idx]

            # Fill in empty entries with T (Other)
            for orig_idx, entry in empty_entries:
                all_classifications[orig_idx] = {
                    "taxonomy_code": "T",
                    "taxonomy_label": "Other",
                    "confidence": 0.0,
                    "reasoning": "Empty text - no classification attempted"
                }

            # Ensure no None values remain (fallback for any unmapped entries)
            for i in range(len(all_classifications)):
                if all_classifications[i] is None:
                    all_classifications[i] = {
                        "taxonomy_code": "T",
                        "taxonomy_label": "Other",
                        "confidence": 0.0,
                        "reasoning": "Fallback - classification not mapped"
                    }

            # Step 4: Apply validators
            print(f"  Step 3: Applying validators...")

            for i, (entry, classification) in enumerate(zip(subsection_entries, all_classifications)):
                code = classification.get("taxonomy_code", "T")
                label = classification.get("taxonomy_label", "Other")
                confidence = classification.get("confidence", 0.0)

                # Determine if this was an empty entry
                entry_text = entry.get("text", "")
                is_empty = is_empty_text(entry_text)

                # Run validators only for non-empty entries
                validation_flags = []
                if not is_empty:
                    # Extract parent code for validation
                    parent_code = extract_parent_code(code)
                    parent_label = label.split(" - ")[0] if " - " in label else label

                    group_for_validation = {
                        "label_inferred": section_header,
                        "parent_section_id": parent_code,
                        "child_section_id": code,
                        "entries": [entry_text],
                        "hierarchy": entry.get("hierarchy", [])
                    }
                    validation_flags = run_all_validators(group_for_validation)

                # Set classification method
                if is_empty:
                    classification_method = "empty_text_skipped"
                else:
                    classification_method = "guided"

                mapped_entry = {
                    **entry,
                    "taxonomy_code": code,
                    "taxonomy_label": label,
                    "parent_code": extract_parent_code(code),
                    "parent_label": label.split(" - ")[0] if " - " in label else label,
                    "confidence": confidence,
                    "validation_flags": [
                        {
                            "severity": flag.severity,
                            "confusion_type": flag.confusion_type,
                            "message": flag.message,
                            "suggestion": flag.suggestion
                        }
                        for flag in validation_flags
                    ],
                    "classification_method": classification_method
                }

                # Apply blog/media auto-correction (Issue #3 fix)
                mapped_entry = correct_blog_media_classification(mapped_entry)

                all_mapped_entries.append(mapped_entry)

            # Report validation results
            subsection_start = len(all_mapped_entries) - len(subsection_entries)
            total_flags = sum(len(e.get("validation_flags", [])) for e in all_mapped_entries[subsection_start:])
            print(f"    → {total_flags} validation flags raised")
            print(f"  ✓ Subsection complete: {len(subsection_entries)} entries")
            print()

        # End of guided mode - report statistics
        print("="*80)
        print("GUIDED MODE STATISTICS")
        print("="*80)
        print(f"Surfacing calls: {surfacing_calls}")
        print(f"Guided classification calls: {guided_calls}")
        print(f"Auto-refinement calls: {refinement_calls}")
        print(f"Escape hatch usage: {escape_hatch_usage} entries")
        print(f"Total cost: ${total_cost:.4f}")
        print(f"Total tokens: {total_tokens:,}")
        print()

    else:
        # ====================================================================
        # TWO-PASS MODE (V9): Constrained classification with ENUM
        # ====================================================================
        print("Using TWO-PASS classification mode (V9 architecture)")
        print("  → Pass 1: Section → Parent Code")
        print("  → Pass 2: Entries → Child Codes (constrained by parent)")
        print()

        for section_idx, (section_header, section_entries) in enumerate(sections.items(), 1):
            print(f"[{section_idx}/{len(sections)}] Processing section: {section_header}")
            print(f"  Entries: {len(section_entries)}")

            # Special handling for Personal Data section
            if section_header == "Personal Data":
                print("  → Using pattern matching for Personal Data")
                mapped = map_personal_data_section(section_entries)
                all_mapped_entries.extend(mapped)
                print(f"  ✓ Mapped {len(mapped)} entries (pattern matching, $0.00)")
                print()
                continue

            # Pass 1: Classify section to parent code
            sample_texts = [e.get("text", "")[:300] for e in section_entries[:5]]

            print(f"  Pass 1: Section → Parent Code...")
            pass1_result = classify_pass1_parent(
                section_label=section_header,
                sample_entries=sample_texts,
                hierarchical_position={"level": 1}
            )
            pass1_calls += 1

            parent_code = pass1_result.get("parent_section_id", "T")
            parent_label = pass1_result.get("parent_canonical_name", "Unknown")
            parent_confidence = pass1_result.get("confidence", 0.0)

            # Track Pass 1 cost
            pass1_tokens = pass1_result.get("token_usage", {})
            pass1_cost = calculate_cost(
                pass1_tokens.get("prompt_tokens", 0),
                pass1_tokens.get("completion_tokens", 0),
                "gpt-5.1"
            )
            total_cost += pass1_cost
            total_tokens += pass1_tokens.get("total_tokens", 0)

            print(f"    → Parent: {parent_code} ({parent_label})")
            print(f"    → Confidence: {parent_confidence:.3f}")
            print(f"    → Cost: ${pass1_cost:.4f}")

            # Split section into subsections for subsection-level override checking
            subsections = group_entries_by_subsection(section_entries)
            print(f"  → Split into {len(subsections)} subsection(s)")

            # Process each subsection
            for subsection_idx, (subsection_header, subsection_entries) in enumerate(subsections.items(), 1):
                print(f"  [{subsection_idx}/{len(subsections)}] Subsection: {subsection_header} ({len(subsection_entries)} entries)")

                # Check for hierarchy-based overrides (cross-category subsections)
                override_parent_code, override_parent_label, override_reason = apply_hierarchy_overrides(
                    section_header=section_header,
                    subsection_header=subsection_header,
                    section_entries=subsection_entries,
                    parent_code=parent_code,
                    parent_label=parent_label
                )

                # Apply override if detected
                subsection_parent_code = parent_code
                subsection_parent_label = parent_label
                subsection_parent_confidence = parent_confidence

                if override_reason:
                    print(f"    🔀 OVERRIDE: {parent_code} → {override_parent_code}")
                    print(f"       Reason: {override_reason}")
                    subsection_parent_code = override_parent_code
                    subsection_parent_label = override_parent_label
                    subsection_parent_confidence = 0.95  # High confidence for rule-based overrides

                # Router: Check if Pass 2 needed for this subsection's parent code
                needs_pass2 = has_subsections(subsection_parent_code)

                if not needs_pass2:
                    print(f"    → No subsections for {subsection_parent_code}, using parent code")
                    # Map all entries to parent code
                    for entry in subsection_entries:
                        mapped_entry = {
                            **entry,
                            "taxonomy_code": subsection_parent_code,
                            "taxonomy_label": subsection_parent_label,
                            "parent_code": subsection_parent_code,
                            "parent_label": subsection_parent_label,
                            "confidence": subsection_parent_confidence,
                            "validation_flags": [],
                            "classification_method": "pass1_only"
                        }
                        all_mapped_entries.append(mapped_entry)

                    print(f"    ✓ Mapped {len(subsection_entries)} entries to {subsection_parent_code}")
                    continue  # Move to next subsection

                # Pass 2: Classify entries to child codes (batched)
                print(f"    Pass 2: Entries → Child Codes...")

                # Separate entries with text from empty entries
                entries_with_text = []
                empty_entries = []
                entry_index_map = {}  # Maps valid entry index → original entry index

                for i, entry in enumerate(subsection_entries):
                    text = entry.get("text", "")
                    if is_empty_text(text):
                        empty_entries.append((i, entry))
                    else:
                        entry_index_map[len(entries_with_text)] = i
                        entries_with_text.append(entry)

                if empty_entries:
                    print(f"      → Skipping {len(empty_entries)} empty entries")

                # Only classify entries that have text
                if not entries_with_text:
                    print(f"      → All entries empty, using parent code")
                    classifications = [
                        {
                            "child_section_id": subsection_parent_code,
                            "child_canonical_name": subsection_parent_label,
                            "confidence": 0.5,
                            "reasoning": "No text to classify"
                        }
                        for _ in subsection_entries
                    ]
                else:
                    entry_texts = [e.get("text", "") for e in entries_with_text]
                    subsection_header_for_llm = extract_subsection_header(entries_with_text[0].get("hierarchy", []))

                    # Process entries in batches of 10
                    batch_size = 10
                    all_classifications = []
                    pass2_batch_cost = 0.0
                    num_batches = (len(entry_texts) + batch_size - 1) // batch_size  # Ceiling division

                    for batch_idx in range(num_batches):
                        start_idx = batch_idx * batch_size
                        end_idx = min(start_idx + batch_size, len(entry_texts))

                        batch_texts = entry_texts[start_idx:end_idx]
                        batch_objects = entries_with_text[start_idx:end_idx]

                        pass2_result = classify_pass2_batch(
                            parent_section_id=subsection_parent_code,
                            parent_confidence=subsection_parent_confidence,
                            section_header=section_header,
                            subsection_header=subsection_header_for_llm,
                            entries=batch_texts,
                            model="gpt-5.1",
                            max_batch_size=batch_size,
                            entry_objects=batch_objects
                        )
                        pass2_calls += 1

                        # Track Pass 2 cost for this batch
                        pass2_tokens = pass2_result.get("token_usage", {})
                        batch_cost = calculate_cost(
                            pass2_tokens.get("prompt_tokens", 0),
                            pass2_tokens.get("completion_tokens", 0),
                            "gpt-5.1"
                        )
                        pass2_batch_cost += batch_cost
                        total_tokens += pass2_tokens.get("total_tokens", 0)

                        # Collect classifications from this batch
                        if pass2_result.get("success", False):
                            batch_classifications = pass2_result.get("classifications", [])
                            all_classifications.extend(batch_classifications)
                        else:
                            # Fallback for failed batch
                            print(f"        ⚠ Batch {batch_idx + 1}/{num_batches} failed, using parent code")
                            for _ in range(len(batch_texts)):
                                all_classifications.append({
                                    "child_section_id": subsection_parent_code,
                                    "child_canonical_name": subsection_parent_label,
                                    "confidence": 0.5,
                                    "reasoning": "Fallback - batch classification failed"
                                })

                    total_cost += pass2_batch_cost
                    print(f"      → {num_batches} batches, Cost: ${pass2_batch_cost:.4f}")

                    # Now merge classifications back with empty entries in original order
                    classifications = [None] * len(subsection_entries)

                    # Place classifications for valid entries
                    for valid_idx, orig_idx in entry_index_map.items():
                        if valid_idx < len(all_classifications):
                            classifications[orig_idx] = all_classifications[valid_idx]

                    # Fill in empty entries with parent code
                    for orig_idx, entry in empty_entries:
                        classifications[orig_idx] = {
                            "child_section_id": subsection_parent_code,
                            "child_canonical_name": subsection_parent_label,
                            "confidence": 0.0,
                            "reasoning": "Empty text - no classification attempted"
                        }

                    # Ensure no None values remain (fallback for any unmapped entries)
                    for i in range(len(classifications)):
                        if classifications[i] is None:
                            classifications[i] = {
                                "child_section_id": subsection_parent_code,
                                "child_canonical_name": subsection_parent_label,
                                "confidence": 0.0,
                                "reasoning": "Fallback - classification not mapped"
                            }

                # Handle case where classifications don't match entries count
                if len(classifications) != len(subsection_entries):
                    print(f"      ⚠ Warning: {len(classifications)} classifications for {len(subsection_entries)} entries")
                    # Pad classifications if needed
                    while len(classifications) < len(subsection_entries):
                        classifications.append({
                            "child_section_id": subsection_parent_code,
                            "child_canonical_name": subsection_parent_label,
                            "confidence": 0.5,
                            "reasoning": "Fallback - no classification returned"
                        })

                # Apply Pass 2.5: Validators
                print(f"    Pass 2.5: Applying validators...")

                for i, (entry, classification) in enumerate(zip(subsection_entries, classifications)):
                    child_code = classification.get("child_section_id", subsection_parent_code)
                    child_label = classification.get("child_canonical_name", subsection_parent_label)
                    child_confidence = classification.get("confidence", 0.0)

                    # Determine if this was an empty entry
                    entry_text = entry.get("text", "")
                    is_empty = is_empty_text(entry_text)

                    # Run validators only for non-empty entries
                    validation_flags = []
                    if not is_empty:
                        group_for_validation = {
                            "label_inferred": section_header,
                            "parent_section_id": subsection_parent_code,
                            "child_section_id": child_code,
                            "entries": [entry_text],
                            "hierarchy": entry.get("hierarchy", [])
                        }
                        validation_flags = run_all_validators(group_for_validation)

                    # Set classification method based on whether entry was empty
                    if is_empty:
                        classification_method = "empty_text_skipped"
                    else:
                        classification_method = "pass2"

                    mapped_entry = {
                        **entry,
                        "taxonomy_code": child_code,
                        "taxonomy_label": child_label,
                        "parent_code": subsection_parent_code,
                        "parent_label": subsection_parent_label,
                        "confidence": child_confidence,
                        "validation_flags": [
                            {
                                "severity": flag.severity,
                                "confusion_type": flag.confusion_type,
                                "message": flag.message,
                                "suggestion": flag.suggestion
                            }
                            for flag in validation_flags
                        ],
                        "classification_method": classification_method
                    }

                    # Apply blog/media auto-correction (Issue #3 fix)
                    mapped_entry = correct_blog_media_classification(mapped_entry)

                    all_mapped_entries.append(mapped_entry)

                # Report validation results for this subsection
                subsection_start = len(all_mapped_entries) - len(subsection_entries)
                total_flags = sum(len(e.get("validation_flags", [])) for e in all_mapped_entries[subsection_start:])
                print(f"      → {total_flags} validation flags raised")
                print(f"    ✓ Mapped {len(subsection_entries)} entries to {subsection_parent_code} subsections")

            # End of subsection loop - report section completion
            print(f"  ✓ Section complete: {len(section_entries)} total entries processed")
            print()

    # Validate that all indices are preserved
    original_indices = set(
        (e["element_idx_start"], e["element_idx_end"])
        for e in entries
    )
    mapped_indices = set(
        (e["element_idx_start"], e["element_idx_end"])
        for e in all_mapped_entries
    )

    missing_indices = original_indices - mapped_indices
    if missing_indices:
        print(f"\n⚠ WARNING: {len(missing_indices)} element indices were lost during mapping!")
        print(f"  Missing: {list(missing_indices)[:5]}...")

    # Count statistics
    empty_count = sum(1 for e in all_mapped_entries if e.get("classification_method") == "empty_text_skipped")

    # Save output
    output_path = om.get_stage3_path()

    output_data = {
        "document_uid": document_uid,
        "total_entries": len(all_mapped_entries),
        "entries_mapped": len(all_mapped_entries),  # Alias for pipeline compatibility
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "pass1_calls": pass1_calls,
        "pass2_calls": pass2_calls,
        "stats": {
            "entries_with_text": len(all_mapped_entries) - empty_count,
            "empty_text_entries": empty_count,
            "indices_preserved": len(missing_indices) == 0,
            "missing_indices_count": len(missing_indices)
        },
        "mapped_entries": all_mapped_entries
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print()
    print("="*80)
    print("STAGE 3 COMPLETE")
    print("="*80)
    print(f"Output: {output_path}")
    print(f"Total entries mapped: {len(all_mapped_entries)}")
    print(f"  - With text: {len(all_mapped_entries) - empty_count}")
    print(f"  - Empty (skipped): {empty_count}")
    print(f"Indices preserved: {'✓ YES' if len(missing_indices) == 0 else f'✗ NO ({len(missing_indices)} missing)'}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Total tokens: {total_tokens:,}")
    print(f"Pass 1 calls: {pass1_calls}")
    print(f"Pass 2 calls: {pass2_calls}")
    print("="*80)

    return output_data, output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python stage_3_taxonomy_mapper.py <docx_path> [entries_json] [--mode MODE]")
        print()
        print("Arguments:")
        print("  docx_path      : Path to Word document")
        print("  entries_json   : Optional path to Stage 2b entries JSON")
        print("  --mode MODE    : Classification mode (two-pass | guided)")
        print()
        print("Modes:")
        print("  two-pass (default) : Constrained classification (Pass 1 → Pass 2 with ENUM)")
        print("  guided             : LLM-surfaced guided classification (V10 architecture)")
        print()
        print("Examples:")
        print("  python stage_3_taxonomy_mapper.py data/sample_cvs/word/2071_Zuschlag_Cv.docx")
        print("  python stage_3_taxonomy_mapper.py data/sample_cvs/word/2082_Dr_Scot.docx --mode guided")
        sys.exit(1)

    # Parse arguments
    docx_path = sys.argv[1]
    entries_json = None
    mode = "two-pass"  # Default

    # Parse remaining arguments
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--mode" and i + 1 < len(sys.argv):
            mode = sys.argv[i + 1]
            i += 2
        elif not arg.startswith("--") and entries_json is None:
            entries_json = arg
            i += 1
        else:
            i += 1

    # Validate mode
    if mode not in ["two-pass", "guided"]:
        print(f"Error: Invalid mode '{mode}'. Must be 'two-pass' or 'guided'")
        sys.exit(1)

    run_stage_3(docx_path, entries_json, mode=mode)
