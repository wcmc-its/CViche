"""
Stage 3a: Header → Taxonomy Mapping

Maps CV section headers to taxonomy codes using LLM analysis.
Produces confidence-weighted taxonomy suggestions for each header node.

Input: Stage 1a hierarchy (JSON)
Output: Header taxonomy mappings with confidence scores (JSON)

This stage runs BEFORE entry extraction (Stage 2) or can run in parallel,
providing taxonomy context that Stage 3b uses for entry-level classification.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from unified_pipeline.llm_client import call_llm


def load_taxonomy() -> dict:
    """Load the taxonomy reference JSON."""
    taxonomy_path = Path(__file__).parent / "core" / "taxonomy_v7.json"
    with open(taxonomy_path, 'r') as f:
        return json.load(f)


def format_hierarchy_as_outline(hierarchy: list[dict], indent: int = 0) -> str:
    """
    Format hierarchy as indented outline for LLM input.

    Args:
        hierarchy: List of hierarchy nodes with 'level', 'text', 'children'
        indent: Current indentation level

    Returns:
        Formatted outline string
    """
    lines = []
    indent_str = "  " * indent

    for node in hierarchy:
        level = node.get('level', 'H1')
        text = node.get('text', '')
        lines.append(f"{indent_str}[{level}] {text}")

        # Recursively format children
        children = node.get('children', [])
        if children:
            lines.append(format_hierarchy_as_outline(children, indent + 1))

    return "\n".join(lines)


def build_taxonomy_reference_condensed(taxonomy: dict) -> str:
    """
    Build a condensed taxonomy reference for the prompt.

    Args:
        taxonomy: Full taxonomy dict

    Returns:
        Condensed reference string
    """
    lines = ["TAXONOMY CODES REFERENCE:", ""]

    # Group by category
    groups = taxonomy.get("meta", {}).get("category_groups", {})
    codes_list = taxonomy.get("codes", [])

    # Build code lookup
    code_lookup = {c["code"]: c for c in codes_list}

    for group_name, group_codes in groups.items():
        lines.append(f"=== {group_name} ===")
        for code in group_codes:
            if code in code_lookup:
                c = code_lookup[code]
                lines.append(f"  {code}: {c['label']}")
                lines.append(f"       {c['purpose']}")
        lines.append("")

    return "\n".join(lines)


def map_headers_to_taxonomy(
    hierarchy: list[dict],
    taxonomy: dict,
) -> tuple[dict, dict]:
    """
    Map CV hierarchy headers to taxonomy codes using LLM.

    Args:
        hierarchy: Stage 1a hierarchy
        taxonomy: Taxonomy reference dict

    Returns:
        Tuple of (mapping_result, stats)
    """
    # Format inputs
    outline = format_hierarchy_as_outline(hierarchy)
    taxonomy_ref = build_taxonomy_reference_condensed(taxonomy)

    # Build the system prompt
    system_prompt = """You are an expert at classifying academic CV sections into a standardized taxonomy.

TASK:
You are given (1) a hierarchical outline of CV sections and (2) a taxonomy reference.
Map each node in the outline to the most specific matching taxonomy code(s) and output a single JSON object.

REQUIREMENTS:

1. Reproduce the hierarchy exactly
   - Preserve all H1, H2, H3 levels.
   - Keep original ordering and nesting.
   - Do not alter or rename headings.

2. For each node, generate a taxonomy_options array
   Each entry must include:
   { "code": "<taxonomy_code>", "confidence": <0–1> }

   Rules:
   - Confidences should sum to 1.0 for each node (±0.01 acceptable).
   - Ignore / omit any option with confidence < 0.05.
   - You may provide multiple codes when classification is ambiguous.

3. Output a valid JSON structure
   Use EXACT structure:
   {
     "mappings": [
       {
         "title": "Node Title",
         "level": "H1 or H2 or H3",
         "taxonomy_options": [
           { "code": "X", "confidence": 0.7 },
           { "code": "Y", "confidence": 0.3 }
         ],
         "note": "Optional—only if ambiguous.",
         "children": [ ... nested nodes ... ]
       }
     ]
   }

4. Clarifying notes
   Include a note ONLY IF the node has more than one taxonomy option.
   Rules:
   - 1–2 sentences max.
   - No chain-of-thought or detailed reasoning.
   - Summaries should be high-level, e.g.:
     "Ambiguous between S8 and R; classified mainly as S8 because not explicitly invited."
     "Split between D1 and D3 due to unclear academic vs non-academic appointment."

   For nodes with a single, clear mapping:
   - DO NOT include a note.

5. No explanation outside the JSON
   - Output only the JSON.
   - No markdown, no commentary, no backticks.

6. Special considerations for ambiguous headers
   - Clinical-sounding sections (e.g., "Hospital Infection Control Activities") may contain:
     * Clinical projects (L2) OR committee membership (P) - include BOTH as options.
     * Leadership roles (L3/O) are rare; usually it's committee service (P).
   - "Advisory Committees" or similar may include R (invited presentations) if entries are invited scientific sessions.
   - Headers with "Invited" in them → likely R.
   - Headers with "Committee" in them → likely P (internal) or Q2 (external).
   - "Administrative Activity" headers often contain both O (leadership) and P (committee service).

7. AVOID OVERUSING T (Appendix/Other)
   T should only be suggested when a section truly contains miscellaneous content.
   NEVER suggest T as primary for sections that might contain:
   - "Other Publications" → likely contains grants (M2), reports (S5), or other outputs - NOT T
   - "Research Interests" → M1, not T
   - "Grant Support" or "Funding" → M2, not T
   - "Editorial Activities" → Q4, not T
   - Sections with "Other" in the title often contain valid content that maps to specific codes

8. SERVICE HEADERS OVERRIDE PARENT HIERARCHY
   Service-related headers should be classified based on their content, NOT their parent section:
   - "Committee", "Search Committee", "Advisory Committee" → P or Q2 (even if under "Publications")
   - "Chair", "Member", "Service" → P, Q2, or O depending on role
   - "Accreditation", "Review Panel" → Q2 or Q3
   - "Community Service", "Volunteer" → P or Q2

   Example: If "Search Committee" appears under a "Publications" parent header,
   classify it as P (service), not S1-S9 (publications). The child header's
   semantic meaning takes precedence over incorrect parent placement.

9. RESEARCHER PROFILE SECTIONS → S0
   Sections containing researcher identifiers or bibliometric summaries should map to S0:
   - "Research Profile", "Researcher Profile", "Author Profile" → S0
   - "Bibliometrics", "Publication Metrics", "Citation Metrics" → S0
   - "Google Scholar", "ORCID", "ResearchGate" (as section headers) → S0

   S0 (Researcher Profile & Bibliometric Summary) precedes S1-S9 publications.
   It captures ORCID, h-index, citation counts, and publication volume summaries.

10. CONFERENCE PROCEEDINGS SECTIONS → S8 (NOT S1)
    Sections for conference papers, proceedings, or presentations should default to S8:
    - "Conference Papers", "Conference Proceedings", "Proceedings" → S8
    - "Presentations", "Conference Presentations" → S8
    - "Abstracts", "Published Abstracts" → S8
    - "Posters", "Poster Presentations" → S8
    - "Symposia", "Workshop Papers" → S8

    Conference content is typically NOT peer-reviewed like journal articles.
    Only suggest S1 if the header explicitly indicates peer review.

11. Q1 VS Q2 FOR EXTERNAL SERVICE SECTIONS
    For sections describing external organization involvement:
    - Q1 (External Leadership): Sections with "Chair", "President", "Director", "Officer"
    - Q2 (External Service): Sections with "Member", "Service", "Volunteer", "Participation"

    Headers like "Professional Memberships", "Society Memberships" → Q2
    Headers like "Leadership Positions", "Board Positions" → Q1

12. GRANT/FUNDING SECTION HEADERS → M2 (OVERRIDE PARENT)
    Headers related to research funding should ALWAYS map to M2, regardless of parent section.
    This takes precedence over any parent hierarchy placement.

    Headers that indicate grants/funding → M2:
    - "Research Support", "Grant Support", "Grants", "Funding"
    - "Sponsored Research", "Extramural Funding", "Intramural Funding"
    - "Active Grants", "Current Grants", "Completed Grants", "Past Grants"
    - "Pending Grants", "Grants Under Review", "Submitted Grants"
    - "Research Funding", "External Funding", "Grant Awards"
    - "Principal Investigator", "Co-Investigator" (as section headers)

    Split M2A vs M2B based on status indicators:
    - "Active", "Current", "Ongoing" → M2A (active grants)
    - "Completed", "Past", "Previous", "Former" → M2B (completed grants)
    - If no status indicated, suggest both M2A and M2B with equal confidence

    IMPORTANT: Even if a grant header appears under "TEACHING" or "SERVICE" due to
    CV formatting issues, classify it as M2. The header content takes precedence.

13. MENTORING/ADVISING SECTION HEADERS → N3 (OVERRIDE PARENT)
    Headers related to student mentoring and advising should ALWAYS map to N3,
    regardless of parent section. This takes precedence over any parent hierarchy.

    Headers that indicate mentoring → N3:
    - "Graduate Students", "Doctoral Students", "PhD Students", "Masters Students"
    - "Thesis Committees", "Dissertation Committees", "Doctoral Committees"
    - "Student Advising", "Graduate Advising", "Academic Advising"
    - "Mentees", "Trainees", "Advisees", "Research Mentees"
    - "Student Research", "Student Projects", "Student Supervision"
    - "Postdoctoral Trainees", "Postdoctoral Fellows", "Postdocs"
    - "Research Supervision", "Thesis Supervision"

    Split N3A vs N3B based on status:
    - "Current", "Active", "Ongoing" → N3A (current mentees)
    - "Past", "Former", "Graduated", "Completed", "Alumni" → N3B (past mentees)
    - If no status indicated, suggest N3B as primary (most entries are completed)

    IMPORTANT: Even if mentoring headers appear under "SERVICE" or other sections
    due to CV formatting, classify them as N3. The header content takes precedence.

14. TEACHING (K) VS MENTORING (N3) DISTINCTION
    Teaching and mentoring are distinct categories. Do not conflate them:

    TEACHING (K-series) - Classroom instruction:
    - "Courses Taught", "Teaching Experience", "Classroom Teaching" → K1
    - "Course Development", "Curriculum Development" → K3
    - "Teaching Assistant", "Guest Lectures" → K1 or K2

    MENTORING (N3-series) - Individual student supervision:
    - "Graduate Advising", "Thesis Advising" → N3
    - "Dissertation Committees", "Student Committees" → N3
    - "Research Mentoring", "Student Research Supervision" → N3

    Key distinction:
    - K = Teaching COURSES to groups of students
    - N3 = Advising/mentoring INDIVIDUAL students on research/theses

    When a parent header is "TEACHING" but a child header is about advising:
    - "TEACHING" → K1 (for the parent)
    - "Graduate Student Advising" (child) → N3 (override, not K)

    The child header's semantic meaning takes precedence.

YOUR GOAL:
Classify each outline node into the most appropriate taxonomy code(s), provide reasonable confidence weights, and include concise clarifying notes only when ambiguity exists."""

    # Build user message
    user_message = f"""Here is the CV section hierarchy to classify:

{outline}

---

{taxonomy_ref}

---

Now output the JSON mapping. Remember: output ONLY valid JSON, no markdown or commentary."""

    # Build messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    # Call LLM
    llm_result = call_llm(
        stage="stage_3a",
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=8000
    )

    # Parse response
    content = llm_result["content"]

    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        result = {"mappings": [], "error": str(e)}

    stats = {
        "model": llm_result["model"],
        "input_tokens": llm_result["prompt_tokens"],
        "output_tokens": llm_result["completion_tokens"],
        "total_tokens": llm_result["total_tokens"],
        "cost": llm_result["cost"],
        "elapsed_seconds": llm_result["latency_ms"] / 1000.0
    }

    return result, stats


def count_nodes(mappings: list[dict]) -> int:
    """Count total nodes in mapping tree."""
    count = 0
    for node in mappings:
        count += 1
        count += count_nodes(node.get("children", []))
    return count


def run_stage_3a(
    document_uid: str,
    stage_1a_path: str | None = None,
    output_dir: str | None = None,
) -> dict:
    """
    Run Stage 3a header taxonomy mapping.

    Args:
        document_uid: Document identifier
        stage_1a_path: Path to Stage 1a output (optional, will auto-detect)
        output_dir: Output directory (optional, will auto-detect)

    Returns:
        Result dict with mappings, stats, and output path
    """
    print("=" * 80)
    print("STAGE 3a: HEADER → TAXONOMY MAPPING")
    print("=" * 80)
    print()

    # Find Stage 1a input
    if stage_1a_path is None:
        base_dir = Path(__file__).parent / "outputs" / "stage_1a_segmentation"
        stage_1a_path = base_dir / f"{document_uid}_segmented.json"
    else:
        stage_1a_path = Path(stage_1a_path)

    if not stage_1a_path.exists():
        raise FileNotFoundError(f"Stage 1a output not found: {stage_1a_path}")

    print(f"Input: {stage_1a_path}")

    # Load hierarchy
    with open(stage_1a_path, 'r') as f:
        stage_1a_data = json.load(f)

    hierarchy = stage_1a_data.get("hierarchy", [])
    print(f"Loaded hierarchy: {len(hierarchy)} top-level sections")

    # Load taxonomy
    taxonomy = load_taxonomy()
    print(f"Loaded taxonomy v{taxonomy['meta']['version']}: {taxonomy['meta']['total_valid_codes']} codes")
    print()

    # Map headers to taxonomy
    print(f"Mapping headers to taxonomy...")
    mappings, stats = map_headers_to_taxonomy(hierarchy, taxonomy)

    node_count = count_nodes(mappings.get("mappings", []))
    print(f"  ✓ Mapped {node_count} nodes")
    print(f"  ✓ Tokens: {stats['input_tokens']} in + {stats['output_tokens']} out = {stats['total_tokens']} total")
    print(f"  ✓ Cost: ${stats['cost']:.4f}")
    print(f"  ✓ Time: {stats['elapsed_seconds']:.1f}s")
    print()

    # Prepare output
    if output_dir is None:
        output_dir = Path(__file__).parent / "outputs" / "stage_3a_header_mappings"
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document_uid}_header_taxonomy.json"

    # Build output document
    output_doc = {
        "document_uid": document_uid,
        "stage": "3a",
        "stage_name": "Header Taxonomy Mapping",
        "source_file": str(stage_1a_path),
        "mappings": mappings.get("mappings", []),
        "meta": {
            "model": stats.get("model", "unknown"),
            "taxonomy_version": taxonomy["meta"]["version"],
            "node_count": node_count,
            "stats": stats,
            "generated_at": datetime.now().isoformat()
        }
    }

    # Write output
    with open(output_path, 'w') as f:
        json.dump(output_doc, f, indent=2)

    print(f"Output: {output_path}")
    print("=" * 80)

    return {
        "document_uid": document_uid,
        "output_path": str(output_path),
        "node_count": node_count,
        "stats": stats,
        "mappings": mappings.get("mappings", [])
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stage_3a_header_taxonomy_mapper.py <document_uid>")
        print("  document_uid: Document identifier (e.g., 2086_Jones_Webb)")
        sys.exit(1)

    document_uid = sys.argv[1]

    result = run_stage_3a(document_uid)

    print()
    print("MAPPING SUMMARY:")
    print("-" * 40)

    def print_mapping(node, indent=0):
        prefix = "  " * indent
        title = node.get("title", "?")
        level = node.get("level", "?")
        options = node.get("taxonomy_options", [])

        if options:
            codes = ", ".join([f"{o['code']}({o['confidence']:.0%})" for o in options[:3]])
        else:
            codes = "?"

        print(f"{prefix}[{level}] {title[:50]}: {codes}")

        for child in node.get("children", []):
            print_mapping(child, indent + 1)

    for mapping in result.get("mappings", []):
        print_mapping(mapping)
