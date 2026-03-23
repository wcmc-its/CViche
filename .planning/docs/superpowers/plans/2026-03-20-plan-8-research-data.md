# Plan 8: Research Data Capture Enhancements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add RunMetrics, error taxonomy, and extended LLM usage tracking to capture research-grade data from every pipeline run. This enables automated quality signals, cost analysis, and prompt iteration tracking for the academic paper.

**Architecture:** New RunMetrics table stores per-run input characterization (word count, publication count, section completeness). Step table gains structured error classification. LLMUsage table gains model version, temperature, finish_reason, retry count, and prompt version hash. All data is populated during pipeline execution by instrumenting the orchestrator and creating an LLM call wrapper. Quality signals are computed queries over existing tables -- no dedicated storage needed.

**Tech Stack:** SQLAlchemy, Alembic, Python hashlib, OpenAI API response introspection

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` -- section 2 (RunMetrics table, modified Step table, modified LLMUsage table, Automated Quality Signals, Cost Tracking Design).

**Dependencies:** Plan 1 (Auth Foundation) must be completed first -- this plan assumes Alembic is initialized and MariaDB is the database backend.

---

### Task 1: RunMetrics Model + Alembic Migration

**Files:**
- Modify: `web_interface/backend/app/models.py`
- Create: `web_interface/backend/alembic/versions/xxxx_add_research_data_tables.py` (via autogenerate)

- [ ] **Step 1: Add RunMetrics model to models.py**

Add to `web_interface/backend/app/models.py` after the existing `LLMUsage` class:

```python
class RunMetrics(Base):
    """Per-run input characterization and quality metrics.

    Computed during pipeline execution. One row per run.
    Essential for the research paper -- correlates input complexity
    with output quality and cost.
    """
    __tablename__ = "run_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(10), ForeignKey("runs.id"), unique=True, nullable=False, index=True)
    word_count = Column(Integer, nullable=True)       # Total words in input document (after stage 1)
    char_count = Column(Integer, nullable=True)       # Total characters in input document (after stage 1)
    publication_count = Column(Integer, nullable=True) # Publications detected (after stage 2)
    sections_populated = Column(Integer, nullable=True)  # WCM sections that received content (after stage 6)
    sections_total = Column(Integer, nullable=False, default=71)  # Total WCM sections
    language = Column(String(50), nullable=True)      # Detected language if non-English
    computed_at = Column(DateTime, server_default=func.now())
```

- [ ] **Step 2: Add error_type column to Step model**

In the existing `Step` class in `web_interface/backend/app/models.py`, add after the `error_message` column:

```python
    error_type = Column(String(50), nullable=True)  # Structured error category:
    # llm_timeout, token_limit, parse_error, invalid_response, api_error, file_error, unknown
```

- [ ] **Step 3: Add extended columns to LLMUsage model**

In the existing `LLMUsage` class in `web_interface/backend/app/models.py`, add after the `timestamp` column:

```python
    model_version = Column(String(100), nullable=True)   # Full model version e.g. "gpt-4o-2024-08-06"
    temperature = Column(Float, nullable=True)            # Temperature setting used
    finish_reason = Column(String(50), nullable=True)     # "stop", "length", "content_filter", etc.
    retry_count = Column(Integer, default=0)              # Retries before this successful call
    prompt_version = Column(String(64), nullable=True)    # SHA-256 hash of prompt template
```

- [ ] **Step 4: Update database.py init_db import**

In `web_interface/backend/app/database.py`, update the `init_db` function to import `RunMetrics`:

```python
def init_db():
    """Initialize database tables."""
    from app.models import Run, Step, Log, LLMUsage, RunMetrics
    Base.metadata.create_all(bind=engine)
```

- [ ] **Step 5: Generate Alembic migration**

Run: `cd web_interface/backend && alembic revision --autogenerate -m "add research data tables and columns"`

- [ ] **Step 6: Review and apply migration**

Review the generated migration file to verify it includes:
- New `run_metrics` table
- New `error_type` column on `steps`
- New `model_version`, `temperature`, `finish_reason`, `retry_count`, `prompt_version` columns on `llm_usage`

Run: `cd web_interface/backend && alembic upgrade head`

- [ ] **Step 7: Verify schema**

Run:
```bash
mysql -u root cviche -e "DESCRIBE run_metrics;"
mysql -u root cviche -e "DESCRIBE steps;" | grep error_type
mysql -u root cviche -e "DESCRIBE llm_usage;" | grep -E "model_version|temperature|finish_reason|retry_count|prompt_version"
```
Expected: all new columns present with correct types.

- [ ] **Step 8: Commit**

```bash
git add web_interface/backend/app/models.py web_interface/backend/app/database.py web_interface/backend/alembic/
git commit -m "feat: add RunMetrics model, Step.error_type, and LLMUsage extended columns"
```

---

### Task 2: Error Taxonomy Classifier

**Files:**
- Create: `web_interface/backend/app/pipeline/error_classifier.py`

- [ ] **Step 1: Create error classifier module**

Create `web_interface/backend/app/pipeline/error_classifier.py`:

```python
"""Classify pipeline exceptions into structured error taxonomy.

Maps Python exceptions to one of the defined error types for
aggregated reporting in the admin dashboard and research export.

Error taxonomy:
- llm_timeout:        OpenAI API timeout or rate limit
- token_limit:        Input or output exceeded token limit
- parse_error:        JSON parse failure on LLM response
- invalid_response:   LLM returned valid JSON but wrong structure
- api_error:          External API error (PubMed, ROR, OpenAI non-timeout)
- file_error:         File I/O error (missing input, write failure)
- unknown:            Unclassified error
"""
import re
from typing import Optional


# Exception class name -> error_type mapping
_CLASS_MAPPINGS = {
    "Timeout": "llm_timeout",
    "APITimeoutError": "llm_timeout",
    "RateLimitError": "llm_timeout",
    "APIConnectionError": "api_error",
    "AuthenticationError": "api_error",
    "APIError": "api_error",
    "APIStatusError": "api_error",
    "BadRequestError": "api_error",
    "InternalServerError": "api_error",
    "JSONDecodeError": "parse_error",
    "FileNotFoundError": "file_error",
    "PermissionError": "file_error",
    "IsADirectoryError": "file_error",
    "OSError": "file_error",
    "IOError": "file_error",
    "KeyError": "invalid_response",
    "IndexError": "invalid_response",
    "ValidationError": "invalid_response",
}

# Message patterns -> error_type (checked if class mapping doesn't match)
_MESSAGE_PATTERNS = [
    (re.compile(r"timeout|timed?\s*out", re.IGNORECASE), "llm_timeout"),
    (re.compile(r"rate.?limit|429|too many requests", re.IGNORECASE), "llm_timeout"),
    (re.compile(r"max.?tokens?|token.?limit|context.?length|maximum.?context", re.IGNORECASE), "token_limit"),
    (re.compile(r"content.?filter|content_filter", re.IGNORECASE), "token_limit"),
    (re.compile(r"finish_reason.*length|truncat", re.IGNORECASE), "token_limit"),
    (re.compile(r"json|parse|decode|deserializ|unexpected.?token", re.IGNORECASE), "parse_error"),
    (re.compile(r"missing.?key|missing.?field|expected.?key|schema|invalid.?format", re.IGNORECASE), "invalid_response"),
    (re.compile(r"api|request.?failed|status.?code|http|connection|network", re.IGNORECASE), "api_error"),
    (re.compile(r"file|directory|path|permission|no such|not found|cannot open", re.IGNORECASE), "file_error"),
]


def classify_error(exception: Exception) -> str:
    """Classify an exception into the error taxonomy.

    Args:
        exception: The caught exception.

    Returns:
        One of: llm_timeout, token_limit, parse_error, invalid_response,
                api_error, file_error, unknown
    """
    # 1. Check exception class name (including parent classes)
    for cls in type(exception).__mro__:
        class_name = cls.__name__
        if class_name in _CLASS_MAPPINGS:
            return _CLASS_MAPPINGS[class_name]

    # 2. Check error message patterns
    error_msg = str(exception)
    for pattern, error_type in _MESSAGE_PATTERNS:
        if pattern.search(error_msg):
            return error_type

    # 3. Check for chained exceptions
    if exception.__cause__:
        chained_type = classify_error(exception.__cause__)
        if chained_type != "unknown":
            return chained_type

    return "unknown"


def classify_finish_reason(finish_reason: Optional[str]) -> Optional[str]:
    """Return an error_type if the finish_reason indicates a problem.

    Args:
        finish_reason: The finish_reason from the OpenAI API response.

    Returns:
        An error_type string if problematic, or None if normal ("stop").
    """
    if not finish_reason or finish_reason == "stop":
        return None
    if finish_reason == "length":
        return "token_limit"
    if finish_reason == "content_filter":
        return "token_limit"
    return None
```

- [ ] **Step 2: Verify module loads**

Run: `cd web_interface/backend && python3 -c "from app.pipeline.error_classifier import classify_error, classify_finish_reason; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/pipeline/error_classifier.py
git commit -m "feat: add error taxonomy classifier for structured step failure tracking"
```

---

### Task 3: Prompt Version Hasher

**Files:**
- Create: `web_interface/backend/app/pipeline/prompt_hasher.py`

- [ ] **Step 1: Create prompt version hasher module**

Create `web_interface/backend/app/pipeline/prompt_hasher.py`:

```python
"""Compute stable hashes for prompt templates.

Generates a SHA-256 hash of the prompt template text (system + user message
templates), stripping variable content so that the same prompt template
always produces the same hash regardless of the specific CV data injected.

This enables longitudinal analysis: "Did accuracy improve when we changed
the Stage 3a prompt on March 15?"
"""
import hashlib
import re
from typing import List, Dict, Optional


def hash_prompt_template(messages: List[Dict[str, str]]) -> str:
    """Compute a stable SHA-256 hash of a prompt template.

    Extracts the system message and the structural parts of the user message,
    normalizing whitespace and stripping obvious variable content (JSON blobs,
    long text blocks) to produce a hash that changes only when the prompt
    template itself changes.

    Args:
        messages: The messages list sent to the OpenAI API.

    Returns:
        First 16 characters of the SHA-256 hex digest (64-bit identifier).
        Sufficient for uniqueness across prompt versions in a single project.
    """
    parts = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            # System prompts are fully template-controlled -- hash as-is
            parts.append(f"SYSTEM:{content}")
        elif role == "user":
            # User prompts contain variable data (CV text, entries, etc.)
            # Extract only the structural/instructional parts
            template_text = _extract_template_structure(content)
            parts.append(f"USER:{template_text}")

    combined = "\n---\n".join(parts)

    # Normalize whitespace for stability
    combined = re.sub(r'\s+', ' ', combined).strip()

    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def _extract_template_structure(user_message: str) -> str:
    """Extract the structural/template parts of a user message.

    Strips large variable blocks (JSON, CV text) and keeps instructional
    text that defines the prompt's behavior.

    Strategy:
    - Remove JSON blocks (content between { } or [ ] that span multiple lines)
    - Remove quoted multi-line text blocks
    - Keep instruction lines, headers, and short inline values
    """
    lines = user_message.split("\n")
    template_lines = []
    in_json_block = False
    brace_depth = 0

    for line in lines:
        stripped = line.strip()

        # Track JSON block depth
        brace_depth += stripped.count("{") + stripped.count("[")
        brace_depth -= stripped.count("}") + stripped.count("]")

        if brace_depth > 1:
            # Deep inside a JSON block -- skip (variable data)
            in_json_block = True
            continue

        if in_json_block and brace_depth <= 0:
            in_json_block = False
            brace_depth = 0
            continue

        # Skip very long lines (likely CV text or data dumps)
        if len(stripped) > 500:
            template_lines.append("[VARIABLE_DATA]")
            continue

        # Keep instruction/template lines
        if stripped:
            template_lines.append(stripped)

    return "\n".join(template_lines)


def hash_system_prompt(system_prompt: str) -> str:
    """Hash just a system prompt string.

    Simpler version for cases where only the system prompt is available.

    Args:
        system_prompt: The system prompt text.

    Returns:
        First 16 characters of the SHA-256 hex digest.
    """
    normalized = re.sub(r'\s+', ' ', system_prompt).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 2: Verify module loads and produces stable hashes**

Run:
```bash
cd web_interface/backend && python3 -c "
from app.pipeline.prompt_hasher import hash_prompt_template

msgs = [
    {'role': 'system', 'content': 'You are a CV parser.'},
    {'role': 'user', 'content': 'Parse this CV:\n{some variable data here}'}
]
h1 = hash_prompt_template(msgs)
h2 = hash_prompt_template(msgs)
assert h1 == h2, 'Hash should be stable'
assert len(h1) == 16, 'Hash should be 16 chars'
print(f'Hash: {h1} - OK')
"
```
Expected: prints hash and `OK`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/pipeline/prompt_hasher.py
git commit -m "feat: add prompt template version hasher for longitudinal analysis"
```

---

### Task 4: LLM Call Wrapper for Extended Tracking

**Files:**
- Create: `web_interface/backend/app/pipeline/llm_tracker.py`

- [ ] **Step 1: Create LLM tracking wrapper**

Create `web_interface/backend/app/pipeline/llm_tracker.py`:

```python
"""Wrapper for recording extended LLM usage data.

Extracts model_version, temperature, finish_reason, and prompt_version
from OpenAI API calls and stores them in the LLMUsage table.

This module does NOT make LLM calls itself -- it records metadata from
calls made by the existing pipeline stage functions. The orchestrator
calls `record_llm_usage()` after each stage completes, using data
extracted from the stage's return values and the OpenAI response objects.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session

from app.models import LLMUsage
from app.pipeline.prompt_hasher import hash_prompt_template


def record_llm_usage(
    db: Session,
    run_id: str,
    step_number: int,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost: float,
    latency_ms: Optional[int] = None,
    model_version: Optional[str] = None,
    temperature: Optional[float] = None,
    finish_reason: Optional[str] = None,
    retry_count: int = 0,
    prompt_version: Optional[str] = None,
    messages: Optional[List[Dict[str, str]]] = None,
) -> LLMUsage:
    """Record an LLM API call with extended metadata.

    Args:
        db: Database session.
        run_id: Pipeline run ID.
        step_number: Step number within the run.
        model: Model name requested (e.g., "gpt-5.1").
        prompt_tokens: Number of prompt tokens.
        completion_tokens: Number of completion tokens.
        total_tokens: Total tokens.
        cost: Computed cost in USD.
        latency_ms: API call latency in milliseconds.
        model_version: Full model version from response (e.g., "gpt-4o-2024-08-06").
        temperature: Temperature setting used for this call.
        finish_reason: Finish reason from the API response.
        retry_count: Number of retries before this call succeeded.
        prompt_version: Pre-computed prompt hash, or None to compute from messages.
        messages: The messages list sent to the API (used to compute prompt_version
                  if prompt_version is not provided).

    Returns:
        The created LLMUsage record.
    """
    # Compute prompt_version from messages if not provided
    if prompt_version is None and messages is not None:
        prompt_version = hash_prompt_template(messages)

    usage = LLMUsage(
        run_id=run_id,
        step_number=step_number,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost=cost,
        latency_ms=latency_ms,
        model_version=model_version,
        temperature=temperature,
        finish_reason=finish_reason,
        retry_count=retry_count,
        prompt_version=prompt_version,
    )

    db.add(usage)
    db.commit()

    return usage


def extract_openai_metadata(response) -> Dict[str, Any]:
    """Extract extended metadata from an OpenAI API response object.

    Works with the openai Python library response objects. Returns a dict
    with keys matching the LLMUsage extended columns.

    Args:
        response: An OpenAI ChatCompletion response object.

    Returns:
        Dict with keys: model_version, finish_reason, prompt_tokens,
        completion_tokens, total_tokens. Values may be None if not available.
    """
    metadata = {
        "model_version": None,
        "finish_reason": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    # Model version (the actual model used, not the alias requested)
    if hasattr(response, "model"):
        metadata["model_version"] = response.model

    # Finish reason from first choice
    if hasattr(response, "choices") and response.choices:
        choice = response.choices[0]
        if hasattr(choice, "finish_reason"):
            metadata["finish_reason"] = choice.finish_reason

    # Token usage
    if hasattr(response, "usage") and response.usage:
        usage = response.usage
        metadata["prompt_tokens"] = getattr(usage, "prompt_tokens", 0) or 0
        metadata["completion_tokens"] = getattr(usage, "completion_tokens", 0) or 0
        metadata["total_tokens"] = getattr(usage, "total_tokens", 0) or 0

    return metadata
```

- [ ] **Step 2: Verify module loads**

Run: `cd web_interface/backend && python3 -c "from app.pipeline.llm_tracker import record_llm_usage, extract_openai_metadata; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/pipeline/llm_tracker.py
git commit -m "feat: add LLM call tracking wrapper with extended metadata extraction"
```

---

### Task 5: RunMetrics Population During Pipeline Execution

**Files:**
- Create: `web_interface/backend/app/pipeline/metrics_collector.py`
- Modify: `web_interface/backend/app/pipeline/orchestrator.py`

- [ ] **Step 1: Create metrics collector module**

Create `web_interface/backend/app/pipeline/metrics_collector.py`:

```python
"""Collect and store RunMetrics during pipeline execution.

Called by the orchestrator at specific pipeline stages:
- After stage 1a/1b: word_count, char_count, language
- After stage 2: publication_count
- After stage 6: sections_populated
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import RunMetrics


def _count_words_and_chars(text: str) -> tuple:
    """Count words and characters in text.

    Args:
        text: The input text to analyze.

    Returns:
        Tuple of (word_count, char_count).
    """
    char_count = len(text)
    word_count = len(text.split())
    return word_count, char_count


def _detect_language(text: str) -> Optional[str]:
    """Detect if text is non-English.

    Simple heuristic: check for high proportion of non-ASCII characters.
    Returns language hint or None if appears to be English.

    For a more robust solution, could use langdetect library, but
    keeping dependencies minimal for now.

    Args:
        text: Sample text to check.

    Returns:
        Language string if non-English detected, None otherwise.
    """
    if not text:
        return None

    # Sample first 2000 chars for performance
    sample = text[:2000]

    # Count non-ASCII alphabetic characters
    non_ascii_alpha = sum(1 for c in sample if c.isalpha() and ord(c) > 127)
    total_alpha = sum(1 for c in sample if c.isalpha())

    if total_alpha == 0:
        return None

    non_ascii_ratio = non_ascii_alpha / total_alpha

    if non_ascii_ratio > 0.3:
        return "non-english"  # Generic flag; specific detection would need langdetect

    return None


def get_or_create_metrics(db: Session, run_id: str) -> RunMetrics:
    """Get existing RunMetrics or create a new one.

    Args:
        db: Database session.
        run_id: Pipeline run ID.

    Returns:
        The RunMetrics record.
    """
    metrics = db.query(RunMetrics).filter(RunMetrics.run_id == run_id).first()
    if not metrics:
        metrics = RunMetrics(run_id=run_id)
        db.add(metrics)
        db.commit()
        db.refresh(metrics)
    return metrics


def record_input_metrics(db: Session, run_id: str, stage1_output_path: str) -> RunMetrics:
    """Record word count, char count, and language after stage 1.

    Reads the stage 1a segmentation output to extract the original CV text
    and compute input characterization metrics.

    Args:
        db: Database session.
        run_id: Pipeline run ID.
        stage1_output_path: Path to stage 1a segmentation JSON output.

    Returns:
        Updated RunMetrics record.
    """
    metrics = get_or_create_metrics(db, run_id)

    try:
        with open(stage1_output_path, 'r', encoding='utf-8') as f:
            stage1_data = json.load(f)

        # Extract text from hierarchy nodes
        all_text = _extract_text_from_hierarchy(stage1_data.get('hierarchy', []))

        word_count, char_count = _count_words_and_chars(all_text)
        language = _detect_language(all_text)

        metrics.word_count = word_count
        metrics.char_count = char_count
        metrics.language = language
        metrics.computed_at = datetime.now()
        db.commit()

    except Exception as e:
        # Metrics are best-effort; don't fail the pipeline
        print(f"Warning: Failed to compute input metrics: {e}")

    return metrics


def _extract_text_from_hierarchy(nodes: list) -> str:
    """Recursively extract all text content from hierarchy nodes.

    Args:
        nodes: List of hierarchy node dicts from stage 1a output.

    Returns:
        Concatenated text from all nodes.
    """
    parts = []
    for node in nodes:
        text = node.get('text', '')
        if text:
            parts.append(text)
        # Include content/body text if present
        content = node.get('content', '')
        if content:
            parts.append(content)
        # Recurse into children
        children = node.get('children', [])
        if children:
            parts.append(_extract_text_from_hierarchy(children))
    return ' '.join(parts)


def record_publication_count(db: Session, run_id: str, stage2_output_path: str) -> RunMetrics:
    """Record publication count after stage 2.

    Counts entries that appear to be publications based on their
    section context or content patterns.

    Args:
        db: Database session.
        run_id: Pipeline run ID.
        stage2_output_path: Path to stage 2 entry extraction JSON output.

    Returns:
        Updated RunMetrics record.
    """
    metrics = get_or_create_metrics(db, run_id)

    try:
        with open(stage2_output_path, 'r', encoding='utf-8') as f:
            stage2_data = json.load(f)

        # Count total entries -- publication_count is refined later
        # after classification (stage 3b), but we record initial entry count here.
        # The orchestrator can update this after stage 3b with classified counts.
        total_entries = stage2_data.get('total_entries', 0)
        if total_entries == 0:
            # Try counting from sections
            sections = stage2_data.get('sections', [])
            for section in sections:
                total_entries += len(section.get('entries', []))

        metrics.publication_count = total_entries
        metrics.computed_at = datetime.now()
        db.commit()

    except Exception as e:
        print(f"Warning: Failed to compute publication count: {e}")

    return metrics


def record_publication_count_from_classified(
    db: Session, run_id: str, stage3b_output_path: str
) -> RunMetrics:
    """Refine publication count after stage 3b classification.

    Uses taxonomy codes to count only actual publications (S-codes).

    Args:
        db: Database session.
        run_id: Pipeline run ID.
        stage3b_output_path: Path to stage 3b classified entries JSON.

    Returns:
        Updated RunMetrics record.
    """
    metrics = get_or_create_metrics(db, run_id)

    try:
        with open(stage3b_output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Count entries with S-codes (publications)
        pub_count = 0
        entries = data.get('entries', [])
        for entry in entries:
            code = entry.get('taxonomy_code', '') or entry.get('code', '')
            if code.startswith('S'):
                pub_count += 1

        metrics.publication_count = pub_count
        metrics.computed_at = datetime.now()
        db.commit()

    except Exception as e:
        print(f"Warning: Failed to compute classified publication count: {e}")

    return metrics


def record_sections_populated(db: Session, run_id: str, stage6_output_path: str) -> RunMetrics:
    """Record sections_populated after stage 6.

    Reads the stage 6 output (WCM document or its metadata) to determine
    how many WCM template sections received content.

    Args:
        db: Database session.
        run_id: Pipeline run ID.
        stage6_output_path: Path to stage 6 output.

    Returns:
        Updated RunMetrics record.
    """
    metrics = get_or_create_metrics(db, run_id)

    try:
        # Stage 6 produces a .docx file. Check for accompanying metadata JSON
        # that may include section population stats.
        metadata_path = Path(stage6_output_path).with_suffix('.json')

        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            sections_populated = meta.get('sections_populated', None)
            if sections_populated is not None:
                metrics.sections_populated = sections_populated
                metrics.computed_at = datetime.now()
                db.commit()
                return metrics

        # If no metadata JSON, try to count from the last available JSON output
        # (stage 5d, 5c, 5b, or 5) which has classified entries with codes
        # that map to WCM sections
        parent_dir = Path(stage6_output_path).parent.parent
        for stage_name in ['stage_5d_citation_formatted', 'stage_5c_teaching_formatted',
                           'stage_5b_institution_enriched', 'stage_5_enrichment',
                           'stage_4_field_extraction']:
            stage_dir = parent_dir / stage_name
            if stage_dir.exists():
                json_files = list(stage_dir.glob('*.json'))
                if json_files:
                    with open(json_files[0], 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # Count unique taxonomy codes as proxy for sections populated
                    codes = set()
                    entries = data.get('entries', [])
                    for entry in entries:
                        code = entry.get('taxonomy_code', '') or entry.get('code', '')
                        if code:
                            codes.add(code)
                    metrics.sections_populated = len(codes)
                    metrics.computed_at = datetime.now()
                    db.commit()
                    return metrics

    except Exception as e:
        print(f"Warning: Failed to compute sections populated: {e}")

    return metrics
```

- [ ] **Step 2: Instrument orchestrator to populate RunMetrics after stage 1a**

In `web_interface/backend/app/pipeline/orchestrator.py`, add the import at the top:

```python
from app.pipeline.metrics_collector import (
    record_input_metrics,
    record_publication_count,
    record_publication_count_from_classified,
    record_sections_populated,
)
```

Then, inside `_execute_stage_logic`, after the stage 1a block completes (after `await self.update_cost(step_number, cost, ...)` at the end of the `if stage_id == '1a':` block), add:

```python
                # Record input metrics (word count, char count, language)
                try:
                    record_input_metrics(self.db, self.run_id, str(output_file))
                except Exception as e:
                    await self.log(step_number, f"Warning: input metrics failed: {e}", "WARNING")
```

- [ ] **Step 3: Instrument orchestrator to populate RunMetrics after stage 2**

Inside `_execute_stage_logic`, after the stage 2 block completes (after `await self.update_cost(step_number, cost, ...)` at the end of the `elif stage_id == '2':` block), add:

```python
                # Record initial publication/entry count
                try:
                    record_publication_count(self.db, self.run_id, str(stage2_path))
                except Exception as e:
                    await self.log(step_number, f"Warning: publication count failed: {e}", "WARNING")
```

- [ ] **Step 4: Instrument orchestrator to refine publication count after stage 3b**

Inside `_execute_stage_logic`, after the stage 3b block completes (after `await self.update_cost(step_number, cost, ...)` at the end of the `elif stage_id == '3b':` block), add:

```python
                # Refine publication count with classified data
                try:
                    record_publication_count_from_classified(
                        self.db, self.run_id, stage3b_result['output_path']
                    )
                except Exception as e:
                    await self.log(step_number, f"Warning: classified pub count failed: {e}", "WARNING")
```

- [ ] **Step 5: Instrument orchestrator to populate sections_populated after stage 6**

Inside `_execute_stage_logic`, after the stage 6 block completes (after `await self.log(step_number, f"WCM template generated: ...")` at the end of the `elif stage_id == '6':` block), add:

```python
                # Record sections populated in output
                try:
                    record_sections_populated(self.db, self.run_id, stage6_output_path)
                except Exception as e:
                    await self.log(step_number, f"Warning: sections populated count failed: {e}", "WARNING")
```

- [ ] **Step 6: Verify metrics collector loads**

Run: `cd web_interface/backend && python3 -c "from app.pipeline.metrics_collector import record_input_metrics; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add web_interface/backend/app/pipeline/metrics_collector.py web_interface/backend/app/pipeline/orchestrator.py
git commit -m "feat: populate RunMetrics during pipeline execution at stages 1a, 2, 3b, and 6"
```

---

### Task 6: Populate error_type When Steps Fail

**Files:**
- Modify: `web_interface/backend/app/pipeline/orchestrator.py`

- [ ] **Step 1: Add error classifier import to orchestrator**

In `web_interface/backend/app/pipeline/orchestrator.py`, add to the imports:

```python
from app.pipeline.error_classifier import classify_error
```

- [ ] **Step 2: Set error_type in the step error handler**

In the `execute_step` method's `except` block (around line 379-388 in the current file), modify the error handling to classify the error:

Find this block in `execute_step`:

```python
        except Exception as e:
            step.status = "error"
            step.completed_at = datetime.now()
            tb_str = traceback.format_exc()
            step.error_message = f"{str(e)}\n\nTraceback:\n{tb_str}"
            self.db.commit()
```

Replace with:

```python
        except Exception as e:
            step.status = "error"
            step.completed_at = datetime.now()
            tb_str = traceback.format_exc()
            step.error_message = f"{str(e)}\n\nTraceback:\n{tb_str}"
            step.error_type = classify_error(e)
            self.db.commit()
```

The only change is adding the `step.error_type = classify_error(e)` line.

- [ ] **Step 3: Verify error classification works**

Run:
```bash
cd web_interface/backend && python3 -c "
from app.pipeline.error_classifier import classify_error

# Test various exception types
assert classify_error(FileNotFoundError('no such file')) == 'file_error'
assert classify_error(ValueError('JSON parse error')) == 'parse_error'
assert classify_error(TimeoutError('connection timed out')) == 'llm_timeout'
assert classify_error(RuntimeError('something unknown')) == 'unknown'
assert classify_error(KeyError('missing_field')) == 'invalid_response'
print('All error classifications correct')
"
```
Expected: `All error classifications correct`

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/pipeline/orchestrator.py
git commit -m "feat: classify step failures into error taxonomy for aggregated reporting"
```

---

### Task 7: Populate LLMUsage Extended Fields from Pipeline Stages

**Files:**
- Modify: `web_interface/backend/app/pipeline/orchestrator.py`

This is the most involved task. The existing orchestrator tracks cost via `self.update_cost()` but does not write individual `LLMUsage` records during pipeline execution. We need to add `record_llm_usage()` calls after each LLM-using stage.

- [ ] **Step 1: Add LLM tracker import to orchestrator**

In `web_interface/backend/app/pipeline/orchestrator.py`, add to the imports:

```python
from app.pipeline.llm_tracker import record_llm_usage
```

- [ ] **Step 2: Record LLM usage after stage 1a**

Inside `_execute_stage_logic`, in the `if stage_id == '1a':` block, after the `await self.update_cost(...)` call, add:

```python
                # Record detailed LLM usage
                try:
                    record_llm_usage(
                        db=self.db,
                        run_id=self.run_id,
                        step_number=step_number,
                        model=self.model,
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                        cost=cost,
                        latency_ms=int(time.time() - start_time) * 1000 if 'start_time' in dir() else None,
                        model_version=stats.get('model_version'),
                        temperature=None,  # Stage 1a uses default
                        finish_reason=stats.get('finish_reason'),
                        retry_count=0,
                    )
                except Exception as e:
                    await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 3: Record LLM usage after stage 2**

Inside the `elif stage_id == '2':` block, after the `await self.update_cost(...)` call, add:

```python
                # Record detailed LLM usage
                try:
                    record_llm_usage(
                        db=self.db,
                        run_id=self.run_id,
                        step_number=step_number,
                        model=self.model,
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                        cost=cost,
                        model_version=stage2_data.get('model_version'),
                        finish_reason=stage2_data.get('finish_reason'),
                    )
                except Exception as e:
                    await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 4: Record LLM usage after stage 3a**

Inside the `elif stage_id == '3a':` block, after the `await self.update_cost(...)` call, add:

```python
                # Record detailed LLM usage
                try:
                    record_llm_usage(
                        db=self.db,
                        run_id=self.run_id,
                        step_number=step_number,
                        model=self.model,
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                        cost=cost,
                        model_version=stats.get('model_version'),
                        temperature=0.2,  # Stage 3a uses temperature=0.2
                        finish_reason=stats.get('finish_reason'),
                    )
                except Exception as e:
                    await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 5: Record LLM usage after stage 3b**

Inside the `elif stage_id == '3b':` block, after the `await self.update_cost(...)` call, add:

```python
                # Record detailed LLM usage
                try:
                    record_llm_usage(
                        db=self.db,
                        run_id=self.run_id,
                        step_number=step_number,
                        model=self.model,
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                        cost=cost,
                        model_version=stats3b.get('model_version'),
                        temperature=0.2,
                        finish_reason=stats3b.get('finish_reason'),
                    )
                except Exception as e:
                    await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 6: Record LLM usage after stage 4**

Inside the `elif stage_id == '4':` block, after the `await self.update_cost(...)` call, add:

```python
                # Record detailed LLM usage
                try:
                    record_llm_usage(
                        db=self.db,
                        run_id=self.run_id,
                        step_number=step_number,
                        model=self.model,
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                        cost=cost,
                        model_version=stats4.get('model_version'),
                        finish_reason=stats4.get('finish_reason'),
                    )
                except Exception as e:
                    await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 7: Record LLM usage after stage 4.5**

Inside the `elif stage_id == '4.5':` block, after the `if cost > 0: await self.update_cost(...)` block, add:

```python
                # Record detailed LLM usage
                if cost > 0:
                    try:
                        record_llm_usage(
                            db=self.db,
                            run_id=self.run_id,
                            step_number=step_number,
                            model=self.model,
                            prompt_tokens=input_tokens,
                            completion_tokens=output_tokens,
                            total_tokens=input_tokens + output_tokens,
                            cost=cost,
                            model_version=stage45_data.get('model_version'),
                            finish_reason=stage45_data.get('finish_reason'),
                        )
                    except Exception as e:
                        await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 8: Record LLM usage after stage 5c**

Inside the `elif stage_id == '5c':` block, after the `if cost > 0: await self.update_cost(...)` block, add:

```python
                # Record detailed LLM usage
                if cost > 0:
                    try:
                        record_llm_usage(
                            db=self.db,
                            run_id=self.run_id,
                            step_number=step_number,
                            model=self.model,
                            prompt_tokens=input_tokens,
                            completion_tokens=output_tokens,
                            total_tokens=input_tokens + output_tokens,
                            cost=cost,
                            model_version=stage5c_meta.get('model_version'),
                            finish_reason=stage5c_meta.get('finish_reason'),
                        )
                    except Exception as e:
                        await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 9: Record LLM usage after stage 5d**

Inside the `elif stage_id == '5d':` block, after the `if cost > 0: await self.update_cost(...)` block, add:

```python
                # Record detailed LLM usage
                if cost > 0:
                    try:
                        record_llm_usage(
                            db=self.db,
                            run_id=self.run_id,
                            step_number=step_number,
                            model=self.model,
                            prompt_tokens=input_tokens,
                            completion_tokens=output_tokens,
                            total_tokens=input_tokens + output_tokens,
                            cost=cost,
                            model_version=stage5d_meta.get('model_version'),
                            finish_reason=stage5d_meta.get('finish_reason'),
                        )
                    except Exception as e:
                        await self.log(step_number, f"Warning: LLM usage recording failed: {e}", "WARNING")
```

- [ ] **Step 10: Commit**

```bash
git add web_interface/backend/app/pipeline/orchestrator.py
git commit -m "feat: record extended LLM usage metadata from all pipeline stages"
```

---

### Task 8: Propagate Extended Metadata from Pipeline Stage Functions

**Files:**
- Modify: `src/unified_pipeline/stage_3a_header_taxonomy_mapper.py`
- Modify: `src/unified_pipeline/stage_3b_entry_classifier.py`
- Modify: `src/unified_pipeline/stage_4_field_extractor.py`
- Modify: `src/unified_pipeline/stage_4_5_research_summary.py`
- Modify: `src/unified_pipeline/stage_5c_teaching_formatter.py`
- Modify: `src/unified_pipeline/stage_5d_citation_formatter.py`
- Modify: `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py`

The existing stage functions return stats dicts with `cost`, `input_tokens`, `output_tokens`, but do NOT return `model_version`, `finish_reason`, or `temperature`. We need to add these to the return values so the orchestrator can pass them to `record_llm_usage()`.

- [ ] **Step 1: Update stage 3a to return extended metadata**

In `src/unified_pipeline/stage_3a_header_taxonomy_mapper.py`, after the `response = client.chat.completions.create(...)` call (around line 328), the function already has `response.choices[0]`. Add to the stats dict that gets returned:

Find the stats dictionary construction (where `input_tokens`, `output_tokens`, and `cost` are set) and add:

```python
    stats['model_version'] = getattr(response, 'model', None)
    stats['finish_reason'] = response.choices[0].finish_reason if response.choices else None
```

- [ ] **Step 2: Update stage 3b to return extended metadata**

Apply the same pattern to `src/unified_pipeline/stage_3b_entry_classifier.py`. After each `response = client.chat.completions.create(...)` call, add `model_version` and `finish_reason` to the returned stats dict.

- [ ] **Step 3: Update stage 4 to return extended metadata**

Apply the same pattern to `src/unified_pipeline/stage_4_field_extractor.py`. The stats dict should include `model_version` and `finish_reason` from the OpenAI response.

- [ ] **Step 4: Update stage 4.5 to return extended metadata**

Apply the same pattern to `src/unified_pipeline/stage_4_5_research_summary.py`.

- [ ] **Step 5: Update stage 5c to return extended metadata**

Apply the same pattern to `src/unified_pipeline/stage_5c_teaching_formatter.py`. The stage 5c metadata dict (accessed as `stage5c_meta` in the orchestrator) should include `model_version` and `finish_reason`.

- [ ] **Step 6: Update stage 5d to return extended metadata**

Apply the same pattern to `src/unified_pipeline/stage_5d_citation_formatter.py`. The stage 5d metadata dict should include `model_version` and `finish_reason`.

- [ ] **Step 7: Update stage 1a (chunked_chat_hierarchy_extractor) to return extended metadata**

In `src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py`, add `model_version` and `finish_reason` to the stats dict returned alongside the hierarchy.

- [ ] **Step 8: Test that stage functions still work**

Run a quick import test to verify no syntax errors were introduced:

```bash
cd /Users/paulalbert/Dropbox/GitHub/CViche && python3 -c "
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, 'src/unified_pipeline')
from unified_pipeline.stage_3a_header_taxonomy_mapper import run_stage_3a
print('Stage 3a OK')
"
```

- [ ] **Step 9: Commit**

```bash
git add src/unified_pipeline/stage_3a_header_taxonomy_mapper.py \
        src/unified_pipeline/stage_3b_entry_classifier.py \
        src/unified_pipeline/stage_4_field_extractor.py \
        src/unified_pipeline/stage_4_5_research_summary.py \
        src/unified_pipeline/stage_5c_teaching_formatter.py \
        src/unified_pipeline/stage_5d_citation_formatter.py \
        src/unified_pipeline/segmentation/chunked_chat_hierarchy_extractor.py
git commit -m "feat: propagate model_version and finish_reason from all LLM stage functions"
```

---

### Task 9: Automated Quality Signals API

**Files:**
- Create: `web_interface/backend/app/api/quality_signals.py`
- Modify: `web_interface/backend/app/main.py`

These are computed queries over existing data -- no new tables needed.

- [ ] **Step 1: Create quality signals API module**

Create `web_interface/backend/app/api/quality_signals.py`:

```python
"""Automated quality signals computed from run data.

These metrics are computed at query time, not stored. They provide
automated quality indicators alongside human feedback ratings.

Signals:
- Publication match rate: PubMed-enriched pubs / total pubs
- Section completeness: sections populated / total sections
- Truncation rate: LLM calls with finish_reason='length' / total calls
- Retry rate: LLM calls with retry_count > 0 / total calls
- Cost per section: total cost / sections populated
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Run, LLMUsage, RunMetrics
from app.auth import get_current_user, require_admin


class RunQualitySignals(BaseModel):
    """Quality signals for a single run."""
    run_id: str
    publication_match_rate: Optional[float] = None  # 0.0-1.0
    section_completeness: Optional[float] = None    # 0.0-1.0
    truncation_rate: Optional[float] = None         # 0.0-1.0
    retry_rate: Optional[float] = None              # 0.0-1.0
    cost_per_section: Optional[float] = None        # USD


class AggregateQualitySignals(BaseModel):
    """Aggregate quality signals across all runs."""
    total_runs: int
    avg_section_completeness: Optional[float] = None
    avg_truncation_rate: Optional[float] = None
    avg_retry_rate: Optional[float] = None
    avg_cost_per_section: Optional[float] = None
    runs_with_truncation: int = 0
    runs_with_retries: int = 0


router = APIRouter()


@router.get("/run/{run_id}/quality-signals", response_model=RunQualitySignals)
async def get_run_quality_signals(
    run_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get automated quality signals for a specific run."""
    # Verify run exists and user has access
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Run not found"})

    if hasattr(run, 'user_id') and run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Access denied"})

    signals = RunQualitySignals(run_id=run_id)

    # Section completeness from RunMetrics
    metrics = db.query(RunMetrics).filter(RunMetrics.run_id == run_id).first()
    if metrics:
        if metrics.sections_populated is not None and metrics.sections_total:
            signals.section_completeness = metrics.sections_populated / metrics.sections_total
        if metrics.sections_populated and run.total_cost and metrics.sections_populated > 0:
            signals.cost_per_section = run.total_cost / metrics.sections_populated

    # Truncation rate and retry rate from LLMUsage
    llm_calls = db.query(LLMUsage).filter(LLMUsage.run_id == run_id).all()
    if llm_calls:
        total_calls = len(llm_calls)
        truncated = sum(1 for c in llm_calls if c.finish_reason == 'length')
        retried = sum(1 for c in llm_calls if (c.retry_count or 0) > 0)
        signals.truncation_rate = truncated / total_calls
        signals.retry_rate = retried / total_calls

    return signals


@router.get("/admin/quality-signals", response_model=AggregateQualitySignals)
async def get_aggregate_quality_signals(
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """Get aggregate quality signals across all completed runs. Admin only."""
    completed_runs = db.query(Run).filter(Run.status == "complete").count()

    if completed_runs == 0:
        return AggregateQualitySignals(total_runs=0)

    # Average section completeness
    avg_completeness = db.query(
        func.avg(
            RunMetrics.sections_populated * 1.0 / RunMetrics.sections_total
        )
    ).filter(
        RunMetrics.sections_populated.isnot(None),
        RunMetrics.sections_total > 0,
    ).scalar()

    # Average cost per section
    avg_cost = db.query(
        func.avg(Run.total_cost / RunMetrics.sections_populated)
    ).join(
        RunMetrics, Run.id == RunMetrics.run_id
    ).filter(
        RunMetrics.sections_populated > 0,
        Run.total_cost > 0,
    ).scalar()

    # Truncation and retry rates from LLMUsage
    total_llm_calls = db.query(func.count(LLMUsage.id)).scalar() or 0
    truncated_calls = db.query(func.count(LLMUsage.id)).filter(
        LLMUsage.finish_reason == 'length'
    ).scalar() or 0
    retried_calls = db.query(func.count(LLMUsage.id)).filter(
        LLMUsage.retry_count > 0
    ).scalar() or 0

    # Count runs that had at least one truncation/retry
    runs_with_truncation = db.query(func.count(func.distinct(LLMUsage.run_id))).filter(
        LLMUsage.finish_reason == 'length'
    ).scalar() or 0
    runs_with_retries = db.query(func.count(func.distinct(LLMUsage.run_id))).filter(
        LLMUsage.retry_count > 0
    ).scalar() or 0

    return AggregateQualitySignals(
        total_runs=completed_runs,
        avg_section_completeness=float(avg_completeness) if avg_completeness else None,
        avg_truncation_rate=truncated_calls / total_llm_calls if total_llm_calls > 0 else None,
        avg_retry_rate=retried_calls / total_llm_calls if total_llm_calls > 0 else None,
        avg_cost_per_section=float(avg_cost) if avg_cost else None,
        runs_with_truncation=runs_with_truncation,
        runs_with_retries=runs_with_retries,
    )
```

- [ ] **Step 2: Register quality signals routes in main.py**

In `web_interface/backend/app/main.py`, add to the router imports and registration:

```python
from app.api import quality_signals

# In the router registration section:
app.include_router(quality_signals.router, prefix="/api", tags=["quality"])
```

- [ ] **Step 3: Verify endpoints load**

Run:
```bash
cd web_interface/backend && python3 -c "
from app.api.quality_signals import router
print(f'Routes: {[r.path for r in router.routes]}')
print('OK')
"
```
Expected: prints route paths and `OK`

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/api/quality_signals.py web_interface/backend/app/main.py
git commit -m "feat: add automated quality signals API endpoints (computed queries)"
```

---

### Task 10: Research Data CSV Export Extension

**Files:**
- Modify: `web_interface/backend/app/api/quality_signals.py` (add export endpoint)

- [ ] **Step 1: Add research data export endpoint**

Add to `web_interface/backend/app/api/quality_signals.py`:

```python
import csv
import io
from fastapi.responses import StreamingResponse


@router.get("/admin/export/research-data")
async def export_research_data(
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """Export comprehensive research dataset as CSV.

    Joins runs + run_metrics + aggregated LLM usage into a single flat table
    suitable for statistical analysis. Each row is one completed run.

    Admin only. Logged for audit trail.
    """
    # Query all completed runs with metrics
    runs = (
        db.query(Run, RunMetrics)
        .outerjoin(RunMetrics, Run.id == RunMetrics.run_id)
        .filter(Run.status == "complete")
        .order_by(Run.started_at)
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        "run_id", "filename", "started_at", "completed_at",
        "total_cost", "total_tokens", "input_tokens", "output_tokens",
        # RunMetrics
        "word_count", "char_count", "publication_count",
        "sections_populated", "sections_total", "language",
        # Computed quality signals
        "section_completeness", "cost_per_section",
        "llm_call_count", "truncation_count", "retry_count",
        "truncation_rate", "retry_rate",
    ])

    for run, metrics in runs:
        # Compute per-run LLM aggregates
        llm_calls = db.query(LLMUsage).filter(LLMUsage.run_id == run.id).all()
        llm_call_count = len(llm_calls)
        truncation_count = sum(1 for c in llm_calls if c.finish_reason == 'length')
        retry_total = sum(1 for c in llm_calls if (c.retry_count or 0) > 0)

        # Compute quality signals
        section_completeness = None
        cost_per_section = None
        if metrics and metrics.sections_populated is not None and metrics.sections_total:
            section_completeness = round(metrics.sections_populated / metrics.sections_total, 4)
            if run.total_cost and metrics.sections_populated > 0:
                cost_per_section = round(run.total_cost / metrics.sections_populated, 6)

        writer.writerow([
            run.id,
            run.filename,
            run.started_at.isoformat() if run.started_at else "",
            run.completed_at.isoformat() if run.completed_at else "",
            run.total_cost,
            run.total_tokens,
            run.input_tokens,
            run.output_tokens,
            # RunMetrics
            metrics.word_count if metrics else "",
            metrics.char_count if metrics else "",
            metrics.publication_count if metrics else "",
            metrics.sections_populated if metrics else "",
            metrics.sections_total if metrics else 71,
            metrics.language if metrics else "",
            # Computed
            section_completeness if section_completeness is not None else "",
            cost_per_section if cost_per_section is not None else "",
            llm_call_count,
            truncation_count,
            retry_total,
            round(truncation_count / llm_call_count, 4) if llm_call_count > 0 else "",
            round(retry_total / llm_call_count, 4) if llm_call_count > 0 else "",
        ])

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=cviche_research_data.csv"
        },
    )
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/backend/app/api/quality_signals.py
git commit -m "feat: add research data CSV export with run metrics and quality signals"
```

---

### Task 11: Integration Verification

**Files:** No new files

- [ ] **Step 1: Run all imports**

Verify all new modules load without errors:

```bash
cd web_interface/backend && python3 -c "
from app.models import RunMetrics, Step, LLMUsage
from app.pipeline.error_classifier import classify_error, classify_finish_reason
from app.pipeline.prompt_hasher import hash_prompt_template, hash_system_prompt
from app.pipeline.llm_tracker import record_llm_usage, extract_openai_metadata
from app.pipeline.metrics_collector import (
    record_input_metrics,
    record_publication_count,
    record_publication_count_from_classified,
    record_sections_populated,
)
from app.api.quality_signals import router as quality_router
print('All modules loaded successfully')
"
```

- [ ] **Step 2: Verify database schema**

```bash
cd web_interface/backend && alembic upgrade head
mysql -u root cviche -e "
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'cviche'
  AND (
    TABLE_NAME = 'run_metrics'
    OR (TABLE_NAME = 'steps' AND COLUMN_NAME = 'error_type')
    OR (TABLE_NAME = 'llm_usage' AND COLUMN_NAME IN ('model_version', 'temperature', 'finish_reason', 'retry_count', 'prompt_version'))
  )
ORDER BY TABLE_NAME, ORDINAL_POSITION;
"
```

Expected output should show:
- `run_metrics` table with all columns
- `steps.error_type`
- `llm_usage.model_version`, `temperature`, `finish_reason`, `retry_count`, `prompt_version`

- [ ] **Step 3: Start server and verify endpoints**

```bash
cd web_interface/backend && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
sleep 2

# Login (requires Plan 1 auth to be implemented)
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt

# Test quality signals endpoint (should return empty data)
curl -s http://localhost:8000/api/admin/quality-signals -b cookies.txt | python3 -m json.tool

# Test research data export (should return CSV headers)
curl -s http://localhost:8000/api/admin/export/research-data -b cookies.txt | head -1
```

- [ ] **Step 4: Run a test pipeline (manual)**

Upload a test CV through the web interface and verify:
1. `run_metrics` table gets a row with `word_count` and `char_count` after stage 1a
2. `run_metrics.publication_count` is populated after stage 3b
3. `run_metrics.sections_populated` is populated after stage 6
4. `llm_usage` table has rows with `model_version` and `finish_reason` populated
5. If any step fails, `steps.error_type` is set to a taxonomy value

```bash
# Check RunMetrics after a run
mysql -u root cviche -e "SELECT * FROM run_metrics ORDER BY computed_at DESC LIMIT 1;"

# Check LLMUsage extended fields
mysql -u root cviche -e "SELECT run_id, step_number, model, model_version, finish_reason, retry_count, prompt_version FROM llm_usage ORDER BY timestamp DESC LIMIT 5;"

# Check error_type (if any steps failed)
mysql -u root cviche -e "SELECT run_id, step_number, step_name, error_type, error_message FROM steps WHERE status='error' ORDER BY id DESC LIMIT 5;"
```

- [ ] **Step 5: Commit (tag release)**

```bash
git add -A
git commit -m "feat: complete research data capture - RunMetrics, error taxonomy, LLM tracking, quality signals"
```
