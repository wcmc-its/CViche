"""
Assistants + Files API based CV hierarchy extraction.

This is a "sidecar" extraction that complements the signature-based pipeline
by uploading the full DOCX to OpenAI's Assistants API and asking it to
extract the header hierarchy directly from the document.

Cost estimate: ~$0.10 per CV (based on GPT-5.1 pricing and typical CV length)
"""

import os
import time
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from openai import OpenAI
import re


# Initialize OpenAI client
client = OpenAI()  # Expects OPENAI_API_KEY in environment


# Assistant ID - create once and reuse
# Set this as an environment variable or config file entry after first creation
ASSISTANT_ID = os.getenv("CV_HIERARCHY_ASSISTANT_ID")


@dataclass
class HeaderNode:
    """Represents a header in the CV hierarchy."""
    level: int  # 1, 2, or 3
    text: str
    children: List["HeaderNode"] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'level': f'H{self.level}',
            'text': self.text,
            'children': [child.to_dict() for child in self.children]
        }


def create_cv_hierarchy_assistant() -> str:
    """
    Create the CV Hierarchy Extractor assistant.
    Only needs to be called once. Returns the assistant ID.
    """
    assistant = client.beta.assistants.create(
        model="gpt-4-turbo-preview",  # Use gpt-4-turbo for file reading, or "gpt-4o" when available
        name="CV Hierarchy Extractor",
        instructions=(
            "You are an expert in academic CV structure. "
            "You will receive a CV file as an attachment. "
            "Your task is to:\n"
            "1) Read the CV content from the file.\n"
            "2) Identify ONLY section and subsection HEADERS (ignore body content, entries, and bullet items).\n"
            "3) Produce a hierarchical outline using [H1], [H2], [H3] tags.\n\n"
            "Rules:\n"
            "- [H1] = major CV sections (e.g., Education, Training, Appointments, Research, Teaching, Service, Honors, Awards, Licensure, Committees, Professional Activities).\n"
            "- [H2] = subsections under an H1 (e.g., Clinical, Research, Grant Support, Publications, License to Practice, Board Certification).\n"
            "- [H3] = sub-subsections (e.g., Peer-Reviewed Journal Articles, Books, Abstracts, Poster Presentations, Invited Lectures).\n"
            "- Preserve the order of appearance in the CV.\n"
            "- Do NOT reorder headers.\n"
            "- Do NOT include body text, bullet entries, publication titles, dates, or individual job entries.\n"
            "- Do NOT include table column headers (e.g., 'Year', 'Discipline', 'Institution', 'Description/Agency').\n"
            "- You MAY introduce a small number of synthetic grouping headers (like 'PUBLICATIONS' or 'GRANT SUPPORT') only when there are multiple clearly related subheadings and no obvious parent.\n"
            "- Do NOT hallucinate fine-grained categories that are not implied by the CV.\n"
            "- ALWAYS include '[H1] Personal Data:' as the first header to represent contact information and personal details at the top of the CV.\n\n"
            "HIERARCHY CONSISTENCY:\n"
            "- These terms are NEVER [H1] - they are always [H2] or [H3] subsections:\n"
            "  * Geographic: International, National, Regional, Local, State, Institutional\n"
            "  * Time-based: Current, Past, Completed, Active, Pending, Ongoing\n"
            "  * Role-based: Principal Investigator, Co-Investigator, Consultant, Mentor, Student\n"
            "- If you see 'Regional' or 'National' as a header, it belongs under a parent section.\n"
            "- Maintain consistent hierarchy levels when the same pattern repeats.\n\n"
            "- Output ONLY the outline, one header per line, using this format:\n"
            '[H1] SECTION NAME\n'
            '  [H2] Subsection Name\n'
            '    [H3] Sub-subsection Name\n\n'
            'Do not add any explanatory text, just the outline.'
        ),
        tools=[{"type": "file_search"}]
    )

    print(f"✓ Created CV Hierarchy Extractor Assistant")
    print(f"  Assistant ID: {assistant.id}")
    print(f"  Save this ID as CV_HIERARCHY_ASSISTANT_ID environment variable")

    return assistant.id


def get_assistant_outline_for_cv(cv_path: str, assistant_id: str) -> str:
    """
    Upload a CV and extract its header hierarchy using the Assistants API.

    Args:
        cv_path: Path to the CV DOCX file
        assistant_id: ID of the CV Hierarchy Extractor assistant

    Returns:
        Text outline in [H1]/[H2]/[H3] format
    """
    print(f"\nAssistants API: Extracting hierarchy from full document...")

    # 1. Upload file
    with open(cv_path, "rb") as f:
        file_obj = client.files.create(
            file=f,
            purpose="assistants"
        )

    print(f"  ✓ Uploaded file (ID: {file_obj.id})")

    # 2. Create a thread with the file attached
    thread = client.beta.threads.create(
        messages=[
            {
                "role": "user",
                "content": (
                    "Please extract all section and subsection headers from the attached CV "
                    "and output the [H1]/[H2]/[H3] hierarchy as specified in your instructions. "
                    "Remember to include '[H1] Personal Data:' as the first header."
                ),
                "attachments": [
                    {
                        "file_id": file_obj.id,
                        "tools": [{"type": "file_search"}]
                    }
                ]
            }
        ]
    )

    print(f"  ✓ Created thread (ID: {thread.id})")

    # 3. Create a run
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant_id
    )

    print(f"  → Running assistant...")

    # 4. Poll until completion
    start_time = time.time()
    while True:
        run_status = client.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id
        )

        if run_status.status in ("completed", "failed", "cancelled", "expired"):
            break

        elapsed = time.time() - start_time
        if elapsed > 120:  # 2 minute timeout
            raise TimeoutError("Assistant run exceeded 2 minute timeout")

        time.sleep(1)

    if run_status.status != "completed":
        raise RuntimeError(f"Assistants run failed with status: {run_status.status}")

    print(f"  ✓ Assistant completed in {time.time() - start_time:.1f}s")

    # 5. Fetch assistant messages
    messages = client.beta.threads.messages.list(thread_id=thread.id)

    # Find the last assistant message with text
    for m in messages.data:
        if m.role == "assistant":
            parts = []
            for content_block in m.content:
                if content_block.type == "text":
                    parts.append(content_block.text.value)
            if parts:
                outline_text = "\n".join(parts)

                # Clean up any markdown code fences
                outline_text = re.sub(r'^```[^\n]*\n', '', outline_text, flags=re.MULTILINE)
                outline_text = re.sub(r'\n```$', '', outline_text)

                return outline_text.strip()

    raise RuntimeError("No assistant response with text found")


def parse_outline(text: str) -> List[HeaderNode]:
    """
    Parse [H1]/[H2]/[H3] style outline text into a forest of HeaderNode trees.

    Args:
        text: Outline text with lines like "[H1] Education:" or "  [H2] Clinical"

    Returns:
        List of root HeaderNode objects (forest)
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    stack: List[HeaderNode] = []
    forest: List[HeaderNode] = []

    for line in lines:
        # Count leading spaces for indentation (optional, as we also use level)
        original_line = line
        line = line.lstrip()

        if not line.startswith("[H"):
            continue

        # Extract level (1, 2, or 3)
        level = int(line[2])

        # Extract text after "] "
        if "]" not in line:
            continue
        after_bracket = line.split("]", 1)[1].strip()

        node = HeaderNode(level=level, text=after_bracket)

        # Build tree based on level
        while stack and stack[-1].level >= level:
            stack.pop()

        if stack:
            stack[-1].children.append(node)
        else:
            forest.append(node)

        stack.append(node)

    return forest


def normalize_header_text(s: str) -> str:
    """
    Normalize header text for matching between outlines.
    Removes punctuation, lowercases, normalizes whitespace.
    """
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def index_headers(forest: List[HeaderNode]) -> Dict[str, List[HeaderNode]]:
    """
    Create an index of normalized header text to HeaderNode objects.

    Returns:
        Dictionary mapping normalized text to list of matching nodes
    """
    index = {}

    def walk(node: HeaderNode):
        norm = normalize_header_text(node.text)
        index.setdefault(norm, []).append(node)
        for child in node.children:
            walk(child)

    for root in forest:
        walk(root)

    return index


def format_outline_for_display(forest: List[HeaderNode]) -> str:
    """
    Convert a HeaderNode forest back to [H1]/[H2]/[H3] outline text.
    """
    lines = []

    def walk(node: HeaderNode, indent_level: int = 0):
        indent = "  " * indent_level
        lines.append(f"{indent}[H{node.level}] {node.text}")
        for child in node.children:
            walk(child, indent_level + 1)

    for root in forest:
        walk(root)

    return "\n".join(lines)


def compare_outlines(pipeline_forest: List[HeaderNode], assistant_forest: List[HeaderNode]) -> List[str]:
    """
    Compare pipeline outline vs assistant outline and return list of differences.

    Returns:
        List of human-readable difference descriptions
    """
    differences = []

    p_index = index_headers(pipeline_forest)
    a_index = index_headers(assistant_forest)

    # Headers in Assistant but not in Pipeline
    assistant_only = set(a_index.keys()) - set(p_index.keys())
    if assistant_only:
        differences.append(f"Headers in Assistant outline but NOT in Pipeline ({len(assistant_only)}):")
        for norm_text in sorted(assistant_only):
            original_text = a_index[norm_text][0].text
            differences.append(f"  - {original_text}")

    # Headers in Pipeline but not in Assistant
    pipeline_only = set(p_index.keys()) - set(a_index.keys())
    if pipeline_only:
        differences.append(f"\nHeaders in Pipeline outline but NOT in Assistant ({len(pipeline_only)}):")
        for norm_text in sorted(pipeline_only):
            original_text = p_index[norm_text][0].text
            differences.append(f"  - {original_text}")

    # Check for level disagreements (same header, different level)
    common = set(p_index.keys()) & set(a_index.keys())
    level_disagreements = []
    for norm_text in common:
        p_nodes = p_index[norm_text]
        a_nodes = a_index[norm_text]

        # Compare first occurrence of each
        if p_nodes[0].level != a_nodes[0].level:
            level_disagreements.append(
                f"  - '{p_nodes[0].text}': Pipeline=[H{p_nodes[0].level}], Assistant=[H{a_nodes[0].level}]"
            )

    if level_disagreements:
        differences.append(f"\nHeaders with different levels ({len(level_disagreements)}):")
        differences.extend(level_disagreements)

    return differences


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python assistants_hierarchy_extractor.py <cv_file.docx> [--create-assistant]")
        sys.exit(1)

    if "--create-assistant" in sys.argv:
        assistant_id = create_cv_hierarchy_assistant()
        print(f"\nSet environment variable:")
        print(f"export CV_HIERARCHY_ASSISTANT_ID={assistant_id}")
        sys.exit(0)

    cv_path = sys.argv[1]

    if not ASSISTANT_ID:
        print("Error: CV_HIERARCHY_ASSISTANT_ID environment variable not set")
        print("Run with --create-assistant first to create the assistant")
        sys.exit(1)

    # Extract hierarchy
    outline_text = get_assistant_outline_for_cv(cv_path, ASSISTANT_ID)

    print("\n" + "="*80)
    print("ASSISTANT OUTLINE")
    print("="*80)
    print(outline_text)
    print("="*80)

    # Parse and display structure
    forest = parse_outline(outline_text)
    print(f"\nParsed: {len(forest)} top-level sections")

    # Count total headers
    def count_nodes(forest):
        count = 0
        for node in forest:
            count += 1
            count += count_nodes(node.children)
        return count

    print(f"Total headers: {count_nodes(forest)}")
