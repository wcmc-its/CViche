# Testing Patterns

**Analysis Date:** 2026-03-22

## Test Framework

**Runner:**
- pytest is present (`.pytest_cache/` directory exists, `lastfailed` cache references `tests/test_extraction_from_logs.py`)
- No `pytest.ini`, `setup.cfg`, `pyproject.toml`, or `tox.ini` configuration file
- No `conftest.py` found anywhere in the project

**Assertion Library:**
- No `assert` statements found in test files — not using pytest assertions
- Tests rely on `print()` with `✓`/`✗` status indicators and manual `if/else` checks

**Run Commands:**
```bash
# No project-level test runner configured. Run individual test scripts directly:
python src/unified_pipeline/core/test_phase2_session1_signals.py
python src/unified_pipeline/core/test_phase2_session5_llm.py
python src/unified_pipeline/test_pipeline_stages.py
python src/legacy/stage_based_extraction/scripts/production/test_ror_validation.py

# pytest was used for the now-absent tests/ directory (from .pytest_cache lastfailed):
# pytest tests/test_extraction_from_logs.py
```

## Test File Organization

**Location:**
- Test scripts are co-located with the source code they test, NOT in a dedicated `tests/` directory
- The `tests/` directory referenced in `.pytest_cache` no longer exists
- Test files live directly in the same directory as the modules they test

**Naming:**
- All test files prefixed with `test_`: `test_pipeline_stages.py`, `test_phase2_session1_signals.py`
- Some test scripts are named after the thing being tested with `test_` prefix: `test_async_pipeline.py`, `test_hierarchical_mapping.py`

**Structure:**
```
src/unified_pipeline/
├── core/
│   ├── test_batch_classification.py        # Tests taxonomy batch classification
│   ├── test_hierarchical_mapping.py        # Tests hierarchical context classification
│   ├── test_phase2_session1_signals.py     # Tests signal detection functions
│   ├── test_phase2_session2_structural.py  # Tests structural repair functions
│   ├── test_phase2_session5_llm.py         # Tests LLM-based fix functions
│   ├── test_segmentation_import.py         # Smoke test for import resolution
│   └── test_signals_with_tracking.py       # Tests signal tracking
├── test_async_pipeline.py                  # Async vs sequential performance test
└── test_pipeline_stages.py                 # Stage 1/2 manual pipeline test

src/legacy/stage_based_extraction/scripts/
├── testing/                                # Legacy test scripts
│   ├── test_stage1b_enrichment.py
│   ├── test_bibliography_chunk.py
│   └── ...
└── production/                             # Integrated test/demo scripts
    ├── test_ror_validation.py
    ├── test_candidate_demo.py
    └── ...
```

## Test Structure

**Suite Organization:**

Tests do NOT use `unittest.TestCase` or pytest class fixtures. Each test file is a standalone script with named functions:

```python
def test_url_orcid_detection():
    """Test Fix #7: URL/ORCID pattern detection."""
    print("Testing Fix #7: URL/ORCID Detection")
    print("=" * 60)

    test_cases = [
        ("Website: https://www.example.com", True, "URL"),
        ("ORCID: 0000-0002-1825-0097", True, "ORCID"),
    ]

    for text, should_detect, signal_type in test_cases:
        result = compute_structural_hints(text, "")
        detected = result['scores'].get('url_pattern', 0.0) >= 0.8
        status = "✓" if detected == should_detect else "✗"
        print(f"{status} {text[:40]:40} -> {signal_type}: {detected}")
```

**Patterns:**
- Setup: direct import of module under test, inline test data as list of tuples
- No teardown — tests are stateless (don't write files or mutate shared state)
- Assertions: manual `if detected == should_detect` comparisons with print-based pass/fail
- Tests accumulate failures rather than stopping at first failure

**Main guard:**
```python
if __name__ == '__main__':
    test_url_orcid_detection()
    test_grant_number_detection()
    # ...
```
Or a `main()` function that calls all test functions and returns exit code:
```python
def main():
    try:
        test_fix22_research_narrative()
        test_fix23_disambiguation_guidance()
        # ...
    except Exception as e:
        traceback.print_exc()
        return 1
    return 0

if __name__ == '__main__':
    exit(main())
```

## Mocking

**Framework:** None. No `unittest.mock`, `pytest-mock`, or similar mocking library used.

**Patterns:**
- LLM calls are NOT mocked. Tests that require OpenAI API call the live API.
- Some tests explicitly avoid LLM calls by using empty or trivially-handled inputs:
```python
groups = [
    {'id': 'G2', 'label_inferred': 'Unknown', 'entries': []},  # Empty → skips LLM call
]
result = llm_context_repair_for_unknowns(groups, verbose=False)
```
- Import-only tests verify that a function is importable and has the expected signature:
```python
import inspect
sig = inspect.signature(repair_segmentation)
has_enable_param = 'enable_llm_repair' in sig.parameters
```
- The async pipeline test (`test_async_pipeline.py`) uses real `AsyncOpenAI()` calls with a `--skip-sequential` flag to speed up iteration

**What to Mock:**
- OpenAI API calls (currently unmocked — tests make live API calls)
- File I/O when testing logic independent of output paths

**What NOT to Mock:**
- The function under test's internal logic
- `Path` operations (tests check real filesystem state)

## Fixtures and Factories

**Test Data:**
- Inline tuples in test functions (no fixture files or factory functions):
```python
TEST_ENTRIES = [
    "Innovations in Global Medical & Health Education journal. 2014-2017",
    "Journal of Medical Education and Curricular Development. 2016-present",
]

test_cases = [
    ("NIH R01-HL123456, Principal Investigator", True),
    ("Regular research without grant number", False),
]
```
- Some tests load real CV files from `data/sample_cvs/word/` (hardcoded paths):
```python
word_file = Path('/Users/paulalbert/Library/CloudStorage/.../2025_Denckla_Cv.docx')
```
Note: These hardcoded absolute paths will fail on any machine other than the original developer's.

**Location:**
- No dedicated fixtures directory
- Test data embedded inline or referenced via absolute paths

## Coverage

**Requirements:** None enforced. No coverage configuration found.

**View Coverage:**
```bash
# Not configured. Would require:
pytest --cov=src/unified_pipeline tests/
```

## Test Types

**Functional/Manual Tests (primary pattern):**
- Scope: individual functions or small groups of functions
- Approach: call function with known inputs, print pass/fail with `✓`/`✗`
- Execution: manually run from command line, not part of CI
- Examples: `test_phase2_session1_signals.py`, `test_phase2_session2_structural.py`

**Integration/Performance Tests:**
- `test_async_pipeline.py`: Compares sequential vs async pipeline execution on a real CV file
- `test_pipeline_stages.py`: Runs Stage 1 → Stage 2a → Stage 2b on a real CV file
- These require the full environment (OpenAI API key, real CV files)

**Smoke/Import Tests:**
- `test_segmentation_import.py`: Verifies module can be imported and a real file can be processed
- Validates fix correctness after code changes

**Pytest Tests (historical):**
- `.pytest_cache/v/cache/lastfailed` references `tests/test_extraction_from_logs.py::test_extraction_coverage_not_too_low`
- That `tests/` directory no longer exists — the formal pytest test suite was deleted or never committed

## Common Patterns

**Signal/Detection Testing:**
```python
def test_grant_number_detection():
    test_cases = [
        ("NIH R01-HL123456, Principal Investigator", True),
        ("Regular research without grant number", False),
    ]
    for text, should_detect in test_cases:
        result = compute_structural_hints(text, "")
        detected = result['scores'].get('grant_number_patterns', 0.0) >= 0.8
        status = "✓" if detected == should_detect else "✗"
        print(f"{status} {text[:50]:50} -> {detected}")
```

**Import + Signature Validation:**
```python
def test_fix21_function_import():
    try:
        from repair_segmentation import llm_context_repair_for_unknowns
        print(f"✓ Function imported successfully")
        result = llm_context_repair_for_unknowns(groups, verbose=False)
        print(f"✓ Function executes without errors")
    except Exception as e:
        print(f"✗ Error: {e}")
        traceback.print_exc()
```

**API/LLM Classification Test:**
```python
def test_editorial_board_classification():
    result = classify_pass2_batch(
        parent_section_id="extramural_professional_activities",
        section_header="PROFESSIONAL PRACTICE: REVIEWER / BOARD MEMBER",
        subsection_header="Editorial Board Member",
        entries=TEST_ENTRIES,
        model="gpt-4o-mini"
    )
    if result['success']:
        all_q4c = all(c['child_section_id'] == 'Q4C' for c in result['classifications'])
        print("✓ SUCCESS" if all_q4c else "✗ FAILURE")
```

## Key Observation: Testing Gap

The project lacks a runnable test suite. There is no:
- `tests/` directory with pytest-compatible files
- CI/CD pipeline running tests
- Mocking layer enabling offline testing
- Coverage reporting

All "test" files are manual diagnostic scripts that require a live OpenAI API key and often hardcoded absolute paths to CV files. They are development tools for verifying a specific fix, not regression guards.

New code should add pytest-style tests with `assert` statements to `src/unified_pipeline/core/` or a new `tests/` directory to fill this gap.

---

*Testing analysis: 2026-03-22*
