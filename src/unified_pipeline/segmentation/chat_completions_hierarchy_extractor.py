"""
Direct Chat Completions API based CV hierarchy extraction.

This approach:
1. Converts DOCX to plain text using python-docx
2. Sends the full text directly to GPT-4o via Chat Completions API
3. No Assistants API, no file uploads, no RAG - just direct text processing

Advantages:
- Reads 100% of the document (no chunk retrieval)
- Uses latest GPT-4o model
- Simpler and faster (single API call)
- No deprecated APIs
- More deterministic

Cost estimate: ~$0.25-$0.60 per CV (50-120k tokens at GPT-4o rates)
"""

import os
from typing import List, Dict
from dataclasses import dataclass, field
from openai import OpenAI
import re
from docx import Document


# Initialize OpenAI client
client = OpenAI()


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


def extract_text_from_docx(docx_path: str) -> str:
    """
    Extract all text from a DOCX file preserving paragraph structure.

    Args:
        docx_path: Path to the DOCX file

    Returns:
        Full text content with paragraphs separated by newlines
    """
    doc = Document(docx_path)
    paragraphs = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:  # Only include non-empty paragraphs
            paragraphs.append(text)

    return "\n".join(paragraphs)


def get_hierarchy_from_text(cv_text: str, model: str = "gpt-4o") -> str:
    """
    Extract CV header hierarchy from full document text using Chat Completions API.

    Args:
        cv_text: Full CV text content
        model: OpenAI model to use (default: gpt-4o)

    Returns:
        Text outline in [H1]/[H2]/[H3] format
    """
    print(f"\nChat Completions API: Extracting hierarchy from full document text...")
    print(f"  Document length: {len(cv_text)} characters, {len(cv_text.split())} words")

    system_prompt = """You are an expert in academic CV structure.

You will receive the full text content of an academic CV.

Your task is to:
1) Read the ENTIRE CV content.
2) Identify ONLY section and subsection HEADERS (ignore body content, entries, and bullet items).
3) Produce a hierarchical outline using [H1], [H2], [H3] tags.

Rules:
- [H1] = major CV sections (e.g., Education, Training, Appointments, Research, Teaching, Service, Honors, Awards, Licensure, Committees, Professional Activities, Publications, Grant Support).
- [H2] = subsections under an H1 (e.g., Clinical, Research, Grant Support, Publications, License to Practice, Board Certification, Editorial Boards, Reviewer).
- [H3] = sub-subsections (e.g., Peer-Reviewed Journal Articles, Books, Abstracts, Poster Presentations, Invited Lectures).
- Preserve the order of appearance in the CV.
- Do NOT reorder headers.
- Do NOT include body text, bullet entries, publication titles, dates, or individual job entries.
- Do NOT include table column headers (e.g., 'Year', 'Discipline', 'Institution', 'Description/Agency').
- You MAY introduce a small number of synthetic grouping headers (like 'PUBLICATIONS' or 'GRANT SUPPORT') only when there are multiple clearly related subheadings and no obvious parent.
- Do NOT hallucinate fine-grained categories that are not implied by the CV.
- ALWAYS include '[H1] Personal Data:' as the first header to represent contact information and personal details at the top of the CV.
- Read the ENTIRE document from beginning to end - do not stop early.
- Extract ALL major sections, not just those that seem most relevant.

HIERARCHY CONSISTENCY RULES:
- The following terms are NEVER top-level [H1] sections - they are always subsections:
  * Geographic scope: International, National, Regional, Local, State, Institutional
  * Time-based: Current, Past, Completed, Active, Pending, Ongoing
  * Role-based: Principal Investigator, Co-Investigator, Consultant, Mentor, Student
- If you see "International", "National", "Regional" etc. appearing as apparent section headers,
  they MUST be [H2] or [H3] under a parent like "Presentations" or "Service", never [H1].
- When the same subsection pattern repeats (e.g., International/National/Regional under multiple
  parent sections), maintain consistent hierarchy levels throughout.
- If formatting makes hierarchy ambiguous, use semantic meaning: a geographic term like "Regional"
  is inherently a subdivision, not a major CV category.

Output ONLY the outline, one header per line, using this format:
[H1] SECTION NAME
  [H2] Subsection Name
    [H3] Sub-subsection Name

Do not add any explanatory text, just the outline."""

    user_prompt = f"""Please extract all section and subsection headers from this CV and output the complete [H1]/[H2]/[H3] hierarchy.

Remember to include '[H1] Personal Data:' as the first header.

CV CONTENT:
{cv_text}"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.0,  # Deterministic output
    )

    outline_text = response.choices[0].message.content.strip()

    # Clean up any markdown code fences
    outline_text = re.sub(r'^```[^\n]*\n', '', outline_text, flags=re.MULTILINE)
    outline_text = re.sub(r'\n```$', '', outline_text)

    # Log token usage
    usage = response.usage
    print(f"  ✓ Extraction completed")
    print(f"  Tokens: {usage.prompt_tokens} input + {usage.completion_tokens} output = {usage.total_tokens} total")
    estimated_cost = (usage.prompt_tokens * 2.50 / 1_000_000) + (usage.completion_tokens * 10.00 / 1_000_000)
    print(f"  Estimated cost: ${estimated_cost:.4f}")

    return outline_text


def get_cv_hierarchy(cv_path: str, model: str = "gpt-4o") -> str:
    """
    Extract CV header hierarchy from a DOCX file.

    Args:
        cv_path: Path to the CV DOCX file
        model: OpenAI model to use (default: gpt-4o)

    Returns:
        Text outline in [H1]/[H2]/[H3] format
    """
    # Step 1: Convert DOCX to text
    print(f"Converting DOCX to text: {cv_path}")
    cv_text = extract_text_from_docx(cv_path)

    # Step 2: Extract hierarchy using Chat Completions API
    outline_text = get_hierarchy_from_text(cv_text, model=model)

    return outline_text


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
        print("Usage: python chat_completions_hierarchy_extractor.py <cv_file.docx> [model]")
        print("  model: Optional, defaults to gpt-4o. Can also use gpt-4o-mini for faster/cheaper results.")
        sys.exit(1)

    cv_path = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "gpt-4o"

    # Extract hierarchy
    outline_text = get_cv_hierarchy(cv_path, model=model)

    print("\n" + "="*80)
    print("CV HIERARCHY OUTLINE (Chat Completions API)")
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
