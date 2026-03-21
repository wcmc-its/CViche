"""
Direct file-based CV hierarchy extraction using Assistants API.

This approach uploads the DOCX file and asks the Assistant to read the ENTIRE document
directly, WITHOUT using the file_search tool (which only retrieves semantically
relevant chunks via RAG).

Key differences from assistants_hierarchy_extractor.py:
- NO file_search tool (the model reads the full file directly)
- Uses latest stable Assistants API endpoints
- More reliable for large CVs (1,000+ paragraphs)

Cost estimate: ~$0.25-$0.60 per CV (based on 50-120k tokens)
"""

import os
import time
from typing import List, Dict
from dataclasses import dataclass, field
from openai import OpenAI
import re


# Initialize OpenAI client
client = OpenAI()


# Assistant ID - create once and reuse
ASSISTANT_ID = os.getenv("CV_DIRECT_FILE_ASSISTANT_ID")


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


def create_direct_file_assistant() -> str:
    """
    Create the Direct File CV Hierarchy Extractor assistant.
    This assistant reads files directly WITHOUT the file_search tool.
    Only needs to be called once. Returns the assistant ID.
    """
    assistant = client.beta.assistants.create(
        model="gpt-4o",  # Latest stable model with excellent document understanding
        name="CV Direct File Hierarchy Extractor",
        instructions=(
            "You are an expert in academic CV structure. "
            "You will receive a CV file as an attachment. "
            "Your task is to:\n"
            "1) Read the ENTIRE CV content from the attached file.\n"
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
            "- ALWAYS include '[H1] Personal Data:' as the first header to represent contact information and personal details at the top of the CV.\n"
            "- Read the ENTIRE document from beginning to end - do not stop early.\n"
            "- Extract ALL major sections, not just those that seem most relevant.\n\n"
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
        # CRITICAL: NO tools specified - the model reads files directly
    )

    print(f"✓ Created CV Direct File Hierarchy Extractor Assistant")
    print(f"  Assistant ID: {assistant.id}")
    print(f"  Save this ID as CV_DIRECT_FILE_ASSISTANT_ID environment variable")

    return assistant.id


def get_direct_file_outline_for_cv(cv_path: str, assistant_id: str) -> str:
    """
    Upload a CV and extract its header hierarchy by having the Assistant read
    the file directly (without file_search tool).

    Args:
        cv_path: Path to the CV DOCX file
        assistant_id: ID of the Direct File CV Hierarchy Extractor assistant

    Returns:
        Text outline in [H1]/[H2]/[H3] format
    """
    print(f"\nDirect File API: Extracting hierarchy from full document...")

    # 1. Upload file
    with open(cv_path, "rb") as f:
        file_obj = client.files.create(
            file=f,
            purpose="assistants"
        )

    print(f"  ✓ Uploaded file (ID: {file_obj.id})")

    # 2. Create a thread with the file attached
    # CRITICAL: We attach the file but do NOT specify any tools
    # This forces the model to read the file content directly
    thread = client.beta.threads.create(
        messages=[
            {
                "role": "user",
                "content": (
                    "Please read the ENTIRE attached CV file from beginning to end and "
                    "extract ALL section and subsection headers. Output the complete "
                    "[H1]/[H2]/[H3] hierarchy as specified in your instructions. "
                    "Remember to include '[H1] Personal Data:' as the first header. "
                    "Do NOT use semantic search or retrieval - read the full document sequentially."
                ),
                "attachments": [
                    {
                        "file_id": file_obj.id,
                        "tools": [{"type": "code_interpreter"}]  # Use code_interpreter to read file content
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

    print(f"  → Running assistant (reading full file)...")

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
        if elapsed > 180:  # 3 minute timeout for large files
            raise TimeoutError("Assistant run exceeded 3 minute timeout")

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


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python direct_file_hierarchy_extractor.py <cv_file.docx> [--create-assistant]")
        sys.exit(1)

    if "--create-assistant" in sys.argv:
        assistant_id = create_direct_file_assistant()
        print(f"\nSet environment variable:")
        print(f"export CV_DIRECT_FILE_ASSISTANT_ID={assistant_id}")
        sys.exit(0)

    cv_path = sys.argv[1]

    if not ASSISTANT_ID:
        print("Error: CV_DIRECT_FILE_ASSISTANT_ID environment variable not set")
        print("Run with --create-assistant first to create the assistant")
        sys.exit(1)

    # Extract hierarchy
    outline_text = get_direct_file_outline_for_cv(cv_path, ASSISTANT_ID)

    print("\n" + "="*80)
    print("DIRECT FILE ASSISTANT OUTLINE")
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
