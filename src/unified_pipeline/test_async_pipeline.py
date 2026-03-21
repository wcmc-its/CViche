#!/usr/bin/env python3
"""
Test script for async pipeline parallelization.

Compares sequential vs parallel execution of Stage 2 and Stage 3b
WITHOUT modifying production code.

Usage:
    python test_async_pipeline.py <docx_path>

Example:
    python test_async_pipeline.py ../../data/sample_cvs/word/2079_Zahida.docx
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple, Any
from datetime import datetime

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from openai import OpenAI, AsyncOpenAI
from core.async_rate_limiter import get_rate_limiter_sync, process_batch_with_rate_limit
from core.output_manager import OutputManager
from core.docx_structure_extractor import extract_unified_elements

# Import stage modules (we'll call their internal functions)
import stage_2_entry_extraction as stage2
import stage_3b_entry_classifier as stage3b


# ============================================================================
# Sequential Baseline (Current Implementation)
# ============================================================================

def run_stage2_sequential(docx_path: str) -> Tuple[Dict, float]:
    """
    Run Stage 2 sequentially (current implementation) and return timing.
    """
    print("\n" + "="*80)
    print("STAGE 2 - SEQUENTIAL (BASELINE)")
    print("="*80)

    start_time = time.time()
    result, output_path = stage2.run_stage_2(docx_path)
    elapsed = time.time() - start_time

    print(f"\n⏱️  Stage 2 Sequential Time: {elapsed:.1f}s")
    return result, elapsed


def run_stage3b_sequential(document_uid: str) -> Tuple[Dict, float]:
    """
    Run Stage 3b sequentially (current implementation) and return timing.
    """
    print("\n" + "="*80)
    print("STAGE 3b - SEQUENTIAL (BASELINE)")
    print("="*80)

    start_time = time.time()
    result = stage3b.run_stage_3b(document_uid)
    elapsed = time.time() - start_time

    print(f"\n⏱️  Stage 3b Sequential Time: {elapsed:.1f}s")
    return result, elapsed


# ============================================================================
# Async Parallel Implementation (Test Version)
# ============================================================================

async def run_stage2_async(docx_path: str, output_suffix: str = "_async") -> Tuple[Dict, float]:
    """
    Run Stage 2 with async parallel LLM calls.

    This is a TEST implementation that doesn't modify the production code.
    """
    print("\n" + "="*80)
    print("STAGE 2 - ASYNC PARALLEL (TEST)")
    print("="*80)

    start_time = time.time()

    # Setup (same as sequential)
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(f"File not found: {docx_path}")

    # Generate document UID
    document_uid = f"ASYNC_{docx_path.stem}"
    om = OutputManager(document_uid)

    # Extract document structure
    print(f"\nExtracting document structure from {docx_path.name}...")
    elements = extract_unified_elements(str(docx_path))
    doc_length = len(elements)
    print(f"  Found {doc_length} elements")

    # Load or generate hierarchy (use existing Stage 1a if available)
    hierarchy_path = om.get_stage1a_path()
    if hierarchy_path.exists():
        print(f"  Loading hierarchy from {hierarchy_path}")
        with open(hierarchy_path) as f:
            hierarchy_data = json.load(f)
        hierarchy = hierarchy_data.get("hierarchy", [])
    else:
        # Run Stage 1a (this is fast, non-LLM)
        print("  Running Stage 1a hierarchy extraction...")
        from segmentation.chat_completions_hierarchy_extractor import extract_hierarchy
        hierarchy = extract_hierarchy(str(docx_path))

    # Run Stage 1b to map hierarchy to element indices
    print("  Running Stage 1b hierarchy mapping...")
    from stage_1b_hierarchy_mapper import run_stage_1b
    mapped_hierarchy = run_stage_1b(str(docx_path), hierarchy)

    # Build sections to process (same logic as sequential)
    sections_to_process = stage2.build_sections_from_hierarchy(
        mapped_hierarchy.get("mapped_hierarchy", []),
        doc_length
    )
    print(f"  Found {len(sections_to_process)} sections to process")

    # =========================================================================
    # ASYNC PARALLEL PROCESSING - This is the key difference!
    # =========================================================================

    client = AsyncOpenAI()
    limiter = get_rate_limiter_sync()

    async def process_section_async(section_info: Tuple) -> Tuple[List[Dict], Dict]:
        """Process a single section asynchronously."""
        hierarchy_path, start_idx, end_idx = section_info

        # Get section text
        section_elements = elements[start_idx:end_idx + 1]

        # Build prompt (reuse stage2 logic)
        section_text = stage2.format_elements_for_prompt(section_elements, start_idx)

        if not section_text.strip():
            return [], {"cost": 0, "tokens": 0}

        # Estimate tokens (~4 chars per token + prompt overhead)
        estimated_tokens = len(section_text) // 4 + 800

        async with limiter.acquire("stage_2", estimated_tokens):
            # Build messages
            system_prompt = stage2.build_entry_detection_prompt(hierarchy_path)

            response = await client.chat.completions.create(
                model="gpt-5.1",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": section_text}
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                max_completion_tokens=4000
            )

            # Record actual usage
            actual_tokens = response.usage.total_tokens
            limiter.record_completion(actual_tokens, estimated_tokens)

            # Parse response
            try:
                result = json.loads(response.choices[0].message.content)
                entries = result.get("entries", [])
            except json.JSONDecodeError:
                entries = []

            # Add hierarchy and text to entries
            for entry in entries:
                entry["hierarchy"] = hierarchy_path
                # Get text from elements
                entry_start = entry.get("element_idx_start", start_idx)
                entry_end = entry.get("element_idx_end", entry_start)
                entry_elements = elements[entry_start:entry_end + 1]
                entry["text"] = "\t".join(e.get("text", "") for e in entry_elements)

            cost = (response.usage.prompt_tokens * 1.0 / 1_000_000) + \
                   (response.usage.completion_tokens * 2.0 / 1_000_000)

            return entries, {"cost": cost, "tokens": actual_tokens}

    # Process all sections in parallel
    print(f"\n  Processing {len(sections_to_process)} sections in parallel...")
    print(f"  Max concurrency: {limiter.get_stage_concurrency('stage_2')}")

    tasks = [process_section_async(section) for section in sections_to_process]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Aggregate results
    all_entries = []
    total_cost = 0
    total_tokens = 0
    errors = 0

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"  ⚠️  Section {i} failed: {result}")
            errors += 1
        else:
            entries, cost_info = result
            all_entries.extend(entries)
            total_cost += cost_info["cost"]
            total_tokens += cost_info["tokens"]

    elapsed = time.time() - start_time

    # Build output (simplified for test)
    output_data = {
        "document_uid": document_uid,
        "document_length": doc_length,
        "total_entries": len(all_entries),
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "async_test": True,
        "sections_processed": len(sections_to_process),
        "errors": errors,
        "entries": all_entries
    }

    # Save to test output
    output_path = om.base_path / f"stage_2_async_test_{document_uid}.json"
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n  ✓ Extracted {len(all_entries)} entries")
    print(f"  ✓ Total cost: ${total_cost:.4f}")
    print(f"  ✓ Total tokens: {total_tokens:,}")
    print(f"  ✓ Errors: {errors}")
    print(f"  ✓ Rate limiter stats: {limiter.stats}")
    print(f"\n⏱️  Stage 2 Async Time: {elapsed:.1f}s")

    return output_data, elapsed


async def run_stage3b_async(document_uid: str) -> Tuple[Dict, float]:
    """
    Run Stage 3b with async parallel LLM calls.

    This is a TEST implementation that doesn't modify the production code.
    """
    print("\n" + "="*80)
    print("STAGE 3b - ASYNC PARALLEL (TEST)")
    print("="*80)

    start_time = time.time()

    # Load Stage 2 output
    om = OutputManager(document_uid)
    stage2_path = om.get_stage2_path()

    if not stage2_path.exists():
        raise FileNotFoundError(f"Stage 2 output not found: {stage2_path}")

    with open(stage2_path) as f:
        stage2_data = json.load(f)

    # Load Stage 3a header mappings
    stage3a_path = om.get_stage3a_path()
    if not stage3a_path.exists():
        raise FileNotFoundError(f"Stage 3a output not found: {stage3a_path}")

    with open(stage3a_path) as f:
        stage3a_data = json.load(f)

    # Get entries and mappings
    entries = stage2_data.get("entries", [])
    content_entries = [e for e in entries if e.get("element_type") not in ("header", "break")]

    print(f"  Loaded {len(content_entries)} content entries from Stage 2")

    # Build mapping index
    mapping_index = stage3b.build_mapping_index(stage3a_data.get("mappings", []))
    taxonomy = stage3b.load_taxonomy()

    # Group entries by hierarchy (same as sequential)
    groups = {}
    for entry in content_entries:
        hierarchy = tuple(entry.get("hierarchy", []))
        if hierarchy not in groups:
            groups[hierarchy] = []
        groups[hierarchy].append(entry)

    print(f"  Grouped into {len(groups)} hierarchy groups")

    # =========================================================================
    # ASYNC PARALLEL PROCESSING
    # =========================================================================

    client = AsyncOpenAI()
    limiter = get_rate_limiter_sync()

    async def classify_group_async(hierarchy: Tuple, group_entries: List[Dict]) -> List[Dict]:
        """Classify a group of entries asynchronously."""

        # Get taxonomy context for this hierarchy
        taxonomy_context = stage3b.get_taxonomy_context(list(hierarchy), mapping_index)

        # Build batches (batch_size=15)
        batch_size = 15
        classified = []

        for batch_start in range(0, len(group_entries), batch_size):
            batch = group_entries[batch_start:batch_start + batch_size]

            # Estimate tokens (~4700 avg for classification)
            estimated_tokens = 4700

            async with limiter.acquire("stage_3b", estimated_tokens):
                # Build prompt (reuse stage3b logic)
                context_str = taxonomy_context.format_context_string()
                all_suggested_codes = taxonomy_context.get_all_suggested_codes()
                relevant_families = set(c[0] for c in all_suggested_codes) if all_suggested_codes else None

                if relevant_families and len(relevant_families) <= 5:
                    relevant_families.update(['H', 'T'])
                    taxonomy_ref = stage3b.build_taxonomy_codes_for_prompt(
                        taxonomy,
                        relevant_families=list(relevant_families),
                        context_codes=all_suggested_codes
                    )
                else:
                    taxonomy_ref = stage3b.build_taxonomy_codes_for_prompt(
                        taxonomy,
                        context_codes=all_suggested_codes
                    )

                # Build system prompt (abbreviated for test)
                system_prompt = f"""You are an expert at classifying academic CV entries into a standardized taxonomy.

HIERARCHY CONTEXT:
{context_str}

AVAILABLE TAXONOMY CODES:
{taxonomy_ref}

For each entry, respond with JSON:
{{"classifications": [{{"entry_index": 0, "code": "X", "confidence": 0.95, "reasoning": "..."}}]}}
"""

                # Build user message with entries
                entries_text = "\n\n".join([
                    f"Entry {i}:\n{e.get('text', '')[:500]}"
                    for i, e in enumerate(batch)
                ])

                response = await client.chat.completions.create(
                    model="gpt-5.1",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": entries_text}
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    max_completion_tokens=2000
                )

                # Record actual usage
                actual_tokens = response.usage.total_tokens
                limiter.record_completion(actual_tokens, estimated_tokens)

                # Parse response
                try:
                    result = json.loads(response.choices[0].message.content)
                    classifications = result.get("classifications", [])
                except json.JSONDecodeError:
                    classifications = []

                # Apply classifications to entries
                for cls in classifications:
                    idx = cls.get("entry_index", 0)
                    if 0 <= idx < len(batch):
                        batch[idx]["taxonomy_code"] = cls.get("code", "T")
                        batch[idx]["taxonomy_confidence"] = cls.get("confidence", 0.5)
                        batch[idx]["classification_reasoning"] = cls.get("reasoning", "")
                        batch[idx]["classification_source"] = "llm_async"

                # Ensure all entries have a code
                for entry in batch:
                    if "taxonomy_code" not in entry:
                        entry["taxonomy_code"] = "T"
                        entry["taxonomy_confidence"] = 0.3
                        entry["classification_source"] = "default"

                classified.extend(batch)

        return classified

    # Process all groups in parallel
    print(f"\n  Processing {len(groups)} groups in parallel...")
    print(f"  Max concurrency: {limiter.get_stage_concurrency('stage_3b')}")

    tasks = [classify_group_async(h, g) for h, g in groups.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Aggregate results
    all_classified = []
    errors = 0

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"  ⚠️  Group {i} failed: {result}")
            errors += 1
        else:
            all_classified.extend(result)

    elapsed = time.time() - start_time

    # Compute code distribution
    code_counts = {}
    for entry in all_classified:
        code = entry.get("taxonomy_code", "?")
        code_counts[code] = code_counts.get(code, 0) + 1

    # Build output
    output_data = {
        "document_uid": document_uid,
        "async_test": True,
        "total_entries": len(all_classified),
        "groups_processed": len(groups),
        "errors": errors,
        "code_distribution": dict(sorted(code_counts.items())),
        "entries": all_classified
    }

    # Save to test output
    output_path = om.base_path / f"stage_3b_async_test_{document_uid}.json"
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n  ✓ Classified {len(all_classified)} entries")
    print(f"  ✓ Errors: {errors}")
    print(f"  ✓ Rate limiter stats: {limiter.stats}")
    print(f"\n⏱️  Stage 3b Async Time: {elapsed:.1f}s")

    return output_data, elapsed


# ============================================================================
# Main Test Runner
# ============================================================================

async def run_comparison_test(docx_path: str, skip_sequential: bool = False):
    """
    Run comparison test between sequential and async implementations.
    """
    print("\n" + "="*80)
    print("ASYNC PIPELINE COMPARISON TEST")
    print("="*80)
    print(f"Document: {docx_path}")
    print(f"Time: {datetime.now().isoformat()}")
    print("="*80)

    results = {
        "docx_path": docx_path,
        "timestamp": datetime.now().isoformat(),
        "stage2": {},
        "stage3b": {}
    }

    # Get document UID from path
    docx_path_obj = Path(docx_path)
    base_uid = docx_path_obj.stem

    # =========================================================================
    # Stage 2 Tests
    # =========================================================================

    if not skip_sequential:
        # Run sequential baseline
        try:
            stage2_seq_result, stage2_seq_time = run_stage2_sequential(docx_path)
            results["stage2"]["sequential"] = {
                "time": stage2_seq_time,
                "entries": stage2_seq_result.get("total_entries", 0),
                "tokens": stage2_seq_result.get("total_tokens", 0),
                "cost": stage2_seq_result.get("total_cost", 0)
            }
        except Exception as e:
            print(f"Stage 2 Sequential FAILED: {e}")
            results["stage2"]["sequential"] = {"error": str(e)}

    # Run async version
    try:
        stage2_async_result, stage2_async_time = await run_stage2_async(docx_path)
        results["stage2"]["async"] = {
            "time": stage2_async_time,
            "entries": stage2_async_result.get("total_entries", 0),
            "tokens": stage2_async_result.get("total_tokens", 0),
            "cost": stage2_async_result.get("total_cost", 0)
        }
    except Exception as e:
        print(f"Stage 2 Async FAILED: {e}")
        import traceback
        traceback.print_exc()
        results["stage2"]["async"] = {"error": str(e)}

    # =========================================================================
    # Stage 3b Tests (use existing Stage 2 output)
    # =========================================================================

    # Find an existing Stage 2 output to use for Stage 3b testing
    om = OutputManager(base_uid)
    existing_stage2 = list(Path("outputs/stage_2_entry_extraction").glob(f"*{base_uid}*_entries.json"))

    if existing_stage2:
        test_uid = existing_stage2[0].stem.replace("_entries", "")
        print(f"\n  Using existing Stage 2 output for Stage 3b test: {test_uid}")

        if not skip_sequential:
            # Run sequential baseline
            try:
                stage3b_seq_result, stage3b_seq_time = run_stage3b_sequential(test_uid)
                results["stage3b"]["sequential"] = {
                    "time": stage3b_seq_time,
                    "entries": stage3b_seq_result.get("total_entries", 0)
                }
            except Exception as e:
                print(f"Stage 3b Sequential FAILED: {e}")
                results["stage3b"]["sequential"] = {"error": str(e)}

        # Run async version
        try:
            stage3b_async_result, stage3b_async_time = await run_stage3b_async(test_uid)
            results["stage3b"]["async"] = {
                "time": stage3b_async_time,
                "entries": stage3b_async_result.get("total_entries", 0)
            }
        except Exception as e:
            print(f"Stage 3b Async FAILED: {e}")
            import traceback
            traceback.print_exc()
            results["stage3b"]["async"] = {"error": str(e)}
    else:
        print(f"\n  ⚠️  No existing Stage 2 output found for {base_uid}, skipping Stage 3b test")

    # =========================================================================
    # Summary
    # =========================================================================

    print("\n" + "="*80)
    print("COMPARISON SUMMARY")
    print("="*80)

    if "sequential" in results["stage2"] and "async" in results["stage2"]:
        seq_time = results["stage2"]["sequential"].get("time", 0)
        async_time = results["stage2"]["async"].get("time", 0)
        if seq_time > 0 and async_time > 0:
            speedup = seq_time / async_time
            print(f"\nStage 2:")
            print(f"  Sequential: {seq_time:.1f}s")
            print(f"  Async:      {async_time:.1f}s")
            print(f"  Speedup:    {speedup:.2f}x")

    if "sequential" in results["stage3b"] and "async" in results["stage3b"]:
        seq_time = results["stage3b"]["sequential"].get("time", 0)
        async_time = results["stage3b"]["async"].get("time", 0)
        if seq_time > 0 and async_time > 0:
            speedup = seq_time / async_time
            print(f"\nStage 3b:")
            print(f"  Sequential: {seq_time:.1f}s")
            print(f"  Async:      {async_time:.1f}s")
            print(f"  Speedup:    {speedup:.2f}x")

    print("\n" + "="*80)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_async_pipeline.py <docx_path> [--skip-sequential]")
        print()
        print("Options:")
        print("  --skip-sequential    Only run async version (faster for iteration)")
        print()
        print("Example:")
        print("  python test_async_pipeline.py ../../data/sample_cvs/word/2079_Zahida.docx")
        sys.exit(1)

    docx_path = sys.argv[1]
    skip_sequential = "--skip-sequential" in sys.argv

    asyncio.run(run_comparison_test(docx_path, skip_sequential))
