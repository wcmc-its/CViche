# Coding Conventions

**Analysis Date:** 2026-03-22

## Naming Patterns

**Files:**
- Stage modules use numeric prefix + underscore: `stage_2_entry_extraction.py`, `stage_3b_entry_classifier.py`
- Core utilities use descriptive snake_case: `output_manager.py`, `prompt_logger.py`, `async_rate_limiter.py`
- Test scripts prefixed with `test_`: `test_pipeline_stages.py`, `test_phase2_session1_signals.py`
- Validators use noun phrases: `structural_header.py`, `grant_status_corrector.py`, `committee_position_corrector.py`
- Backup files have timestamp suffix: `cv_taxonomy_wcm_backup.py`, `cv_taxonomy_backup_20251016_073739.py`

**Functions:**
- snake_case throughout: `get_hierarchy_path()`, `build_element_index_map()`, `run_stage_2()`
- Stage entry points named `run_stage_N()` or `run_stage_Nb()`: `run_stage_1b()`, `run_stage_3a()`, `run_stage5b()`
- Boolean functions not consistently prefixed (some `is_`, some descriptive): `is_cancelled()` vs `has_guidance()`
- Helper constructors as class methods: `ValidatorGuidance.no_guidance()`

**Variables:**
- snake_case: `total_cost`, `batch_entries`, `document_uid`, `file_handle`
- Descriptive names for domain objects: `taxonomy_context`, `mapping_index`, `hierarchy_path`

**Classes:**
- PascalCase: `OutputManager`, `TaxonomyContext`, `BaseValidator`, `StructuralHeaderValidator`, `PubMedEnricher`
- Dataclasses used for structured return types: `CorrectionResult`, `ValidatorGuidance`, `GuidanceResult`

**Constants:**
- SCREAMING_SNAKE_CASE: `BATCH_CLASSIFICATION_SCHEMA`, `CV_TITLE_PATTERNS`, `MODEL_PRICING`, `DEFAULT_MODEL`
- URL constants: `EFETCH_URL`, `ESEARCH_URL`, `ID_CONVERTER_URL`

## Code Style

**Formatting:**
- No formatter configured (no `.prettierrc`, `pyproject.toml`, or `.flake8` found)
- 4-space indentation throughout
- Line length: not enforced, long lines present in prompt strings and comments
- Blank lines: 2 between top-level definitions, 1 between class methods

**Linting:**
- No linting tool configured
- Type annotations used in core modules but not universally applied

**Shebangs:**
- Main stage files and test scripts use `#!/usr/bin/env python3`
- Utility modules (no shebang): `output_manager.py`, `base_validator.py`, `entry_utils.py`

## Module Documentation

**Module docstrings present on:**
- All stage files (`stage_2_entry_extraction.py` etc.)
- All validator files
- Core utilities

**Standard docstring format:**
```python
"""
Stage 3b: Entry Classification

Classifies individual CV entries to taxonomy codes using:
1. Entry content (primary signal)
2. Hierarchy context from Stage 3a (guidance/constraints)
...

Input:
  - Stage 2 entries (JSON)
Output:
  - Classified entries with taxonomy codes (JSON)
"""
```

**Function docstrings:**
- Present on public functions in core modules
- Use Google-style Args/Returns blocks:
```python
def build_mapping_index(mappings: List[Dict], index: Dict = None, path: List[str] = None) -> Dict:
    """
    Build mapping index.

    Returns dict mapping title -> mapping node
    """
```
- Test functions use single-line docstrings: `"""Test Fix #7: URL/ORCID pattern detection."""`

## Import Organization

**Order (observed):**
1. Standard library: `os`, `sys`, `json`, `re`, `time`, `pathlib`, `typing`, `datetime`, `dataclasses`
2. Third-party: `openai`, `docx`, `tiktoken`, `requests`
3. Local absolute imports (after `sys.path.insert`)

**Path manipulation pattern (ubiquitous):**
```python
sys.path.insert(0, str(Path(__file__).parent))
```
Used at the top of nearly every module to enable cross-module imports without an installed package. Each module adds its own parent directory, making import resolution ad-hoc.

**No path aliases or package-level `__init__.py` imports** used to simplify cross-module access. `__init__.py` files exist but are mostly empty.

**Graceful import fallbacks in some modules:**
```python
try:
    from .base_validator import BaseValidator, ValidatorGuidance
except ImportError:
    from base_validator import BaseValidator, ValidatorGuidance
```

## Type Annotations

**Usage:**
- Core modules use `from typing import Dict, List, Optional, Tuple, Any`
- Function signatures annotated in core modules: `def classify_entries(entries: List[Dict], ...) -> Tuple[List[Dict], Dict]:`
- Stage files (`stage_2_entry_extraction.py`, `stage_3b_entry_classifier.py`) consistently annotated
- Test scripts and ad-hoc scripts: minimal or no annotations
- Dataclasses (`@dataclass`) used for structured return objects in validator system

## Error Handling

**Patterns:**
- Try/except around all OpenAI API calls; fallback to empty list or default values on failure:
```python
try:
    result = json.loads(response.choices[0].message.content)
    entries = result.get("entries", [])
except json.JSONDecodeError:
    entries = []
```
- Broad `except Exception as e` with `print(f"... failed: {e}")` in test scripts
- `traceback.print_exc()` called in test/comparison scripts for detailed error output
- Graceful degradation: missing fields use `.get("key", default)` throughout
- `FileNotFoundError` raised explicitly when required stage input files are missing

**Not used:**
- Custom exception classes
- Logging of exceptions in production stage code (print-based only)

## Logging

**Two approaches coexist:**

1. **`print()` statements** (dominant in stage files): Stage progress, token counts, cost summaries all go to stdout via print. Stage files like `stage_3b_entry_classifier.py` have ~123 print calls.

2. **`logging` module** (used in `cv_parser/` and `core/entry_utils.py`):
```python
import logging
logger = logging.getLogger(__name__)
logger.debug("Converting legacy string entry...")
logger.warning("Dict entry missing 'text_snippet' field")
```
Files using logging module: `entry_utils.py`, `async_rate_limiter.py`, `cv_parser/cv_taxonomy_wcm.py`, `cv_parser/adaptive_extractor.py`, `cv_parser/data_structurer.py`

3. **Structured prompt logging** via `core/prompt_logger.py`: All LLM calls log request and response to `src/unified_pipeline/prompt_logs/` as JSON files.

**Rule of thumb:** Production stage pipeline files use `print()`. Library-style modules in `core/` and `cv_parser/` use `logging`.

## Function Design

**Size:** Stage functions are large (50-200+ lines). The extraction/classification stages have complex logic that is not decomposed into smaller helpers.

**Parameters:** Most functions accept typed parameters. Optional parameters default to `None` or a sentinel value. Config overrides (model name, batch size) passed as function arguments rather than globals.

**Return Values:** Stage entry points return `(result_dict, output_path)` tuples. Validator methods return dataclass instances.

**OpenAI client initialization:**
```python
client = OpenAI()  # reads OPENAI_API_KEY from environment automatically
```
Client instantiated at module level in some files, inside functions in others. `AsyncOpenAI()` used for async test scripts.

## Module Design

**Exports:**
- No `__all__` declarations
- Modules export by making functions/classes importable directly

**Barrel Files:** Not used. Each consumer imports directly from the module file.

**Dataclasses:** Used in the validator system for structured data:
```python
@dataclass
class ValidatorGuidance:
    exclude_sections: List[str] = field(default_factory=list)
    recommend_sections: List[str] = field(default_factory=list)
    confidence: float = 0.0
```

**Abstract base class pattern** in validators:
```python
class BaseValidator(ABC):
    @abstractmethod
    def applies_to(self) -> List[str]: ...
    @abstractmethod
    def analyze(self, entry_text: str) -> ValidatorGuidance: ...
    @abstractmethod
    def priority(self) -> int: ...
```

## Comments

**When to Comment:**
- Section dividers using `# ===` banners (widely used to separate logical blocks):
```python
# ============================================================================
# Async Parallel Implementation (Test Version)
# ============================================================================
```
- Inline comments explain domain-specific logic: taxonomy codes, CV parsing heuristics, LLM prompt choices
- `# NOTE:` and `# DEPRECATED:` inline markers used

**No JSDoc/TSDoc equivalents enforced.** Google-style docstrings used informally in key modules.

---

*Convention analysis: 2026-03-22*
