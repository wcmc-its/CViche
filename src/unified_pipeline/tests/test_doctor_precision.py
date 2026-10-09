"""Tests for doctor/precision.py: the PRECISION.md reader (#819) and #813's gate.

    python3 -m pytest src/unified_pipeline/tests/test_doctor_precision.py -q -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor import precision  # noqa: E402
from unified_pipeline.doctor.precision import (  # noqa: E402
    LintPrecision,
    load_ledger,
    parse_ledger,
    precision_label,
    precision_payload,
    remediation_allowed,
    user_visible,
)
from unified_pipeline.run_doctor import KNOWN_LINTS  # noqa: E402

_LEDGER = """# Ledger

## How to read a row

| lint | TP / judged |
|---|---|
| `decoy_lint` | 9 / 9 (100%) |

## Per-lint precision

| lint | hits | judged TP / partial / FP | TP / judged | measured |
|---|---|---|---|---|
| `alpha_lint` | 10 | 4 / 2 / 2 | 4 / 8 (50%) | M1 |
| `beta_lint` | 3 | none | none | M1 |
| `stage6_render_warnings`: `shape_a` | 5 | 3 / 0 / 1 | 3 / 4 (75%) | M1 |
| `stage6_render_warnings`: `shape_b` | 5 | 1 / 0 / 5 | 1 / 6 (17%) | M2 |
| `stage6_render_warnings`: `shape_c` | 2 | none | none | M1 |
| `alpha_lint` | 10 | 9 / 0 / 0 | 9 / 9 (100%) | M3 |
| not a lint row | | | 1 / 1 | |

No hits: `gamma_lint`.

| lint | TP / judged |
|---|---|
| `after_table` | 1 / 1 |
"""


def test_parses_the_per_lint_table_only():
    ledger = parse_ledger(_LEDGER)
    # The decoy table under another heading and the table after the first
    # one are not read; a row without a backticked key is skipped.
    assert set(ledger) == {"alpha_lint", "beta_lint", "stage6_render_warnings"}


def test_first_row_of_a_lint_wins():
    assert parse_ledger(_LEDGER)["alpha_lint"] == LintPrecision("alpha_lint", 4, 8, "M1")


def test_none_row_is_listed_with_zero_judged():
    entry = parse_ledger(_LEDGER)["beta_lint"]
    assert entry.judged == 0
    assert entry.precision is None


def test_shape_rows_pool_into_their_lint_key():
    entry = parse_ledger(_LEDGER)["stage6_render_warnings"]
    assert (entry.true_positives, entry.judged, entry.measured) == (4, 10, "M1,M2")
    assert entry.precision == 0.4


def test_columns_are_found_by_header_name():
    text = ("## Per-lint precision (M3)\n\n| TP / judged | lint |\n|---|---|\n"
            "| 2 / 5 | `alpha_lint` |\n")
    assert parse_ledger(text) == {"alpha_lint": LintPrecision("alpha_lint", 2, 5, "")}


def test_a_later_sections_table_is_not_read():
    text = ("## Per-lint precision\n\nNot measured yet.\n\n## History\n\n"
            "| lint | TP / judged |\n|---|---|\n| `alpha_lint` | 1 / 1 |\n")
    assert parse_ledger(text) == {}


def test_no_table_parses_to_empty():
    assert parse_ledger("# Ledger\n\nNo table here.\n") == {}


def test_unreadable_file_is_empty_and_logged(tmp_path, caplog):
    load_ledger.cache_clear()
    try:
        with caplog.at_level("WARNING"):
            assert load_ledger(tmp_path / "missing.md") == {}
    finally:
        load_ledger.cache_clear()
    assert any("unreadable" in r.message for r in caplog.records)


def test_the_committed_ledger_parses_to_known_lints():
    """PRECISION.md is read at run time: a reformat that the parser cannot read,
    or a typo'd lint key, must fail here, not blank the card."""
    ledger = load_ledger()
    assert len(ledger) >= 10
    assert set(ledger) <= set(KNOWN_LINTS)


# --- #813's remediation gate -------------------------------------------------

def _ledger(lint, tp, judged):
    return {lint: LintPrecision(lint, tp, judged, "M9")}


def test_remediation_allowed_at_the_bar():
    assert remediation_allowed("missed_headers", _ledger("missed_headers", 16, 20))


def test_remediation_refused_below_the_precision_bar():
    assert not remediation_allowed("missed_headers", _ledger("missed_headers", 15, 20))


def test_remediation_refused_on_too_few_judged():
    assert not remediation_allowed("missed_headers", _ledger("missed_headers", 19, 19))


def test_remediation_refused_for_a_lint_outside_813_scope():
    assert not remediation_allowed("output_hygiene", _ledger("output_hygiene", 30, 30))


def test_remediation_refused_for_an_unmeasured_lint():
    assert not remediation_allowed("segmentation", {})


def test_no_lint_may_remediate_on_the_committed_ledger():
    """M1: missed_headers is 4 / 8; segmentation and under_extraction have no
    verdicts. #813 stays closed until a re-measure clears the bar."""
    assert not any(remediation_allowed(lint) for lint in precision.REMEDIATION_CANDIDATE_LINTS)


# --- #1589's user-visibility gate --------------------------------------------

def test_user_visible_at_the_bar():
    assert user_visible("dedup_drops", _ledger("dedup_drops", 4, 8))


def test_user_visible_refused_below_the_bar():
    assert not user_visible("dedup_drops", _ledger("dedup_drops", 3, 8))


def test_user_visible_for_an_unmeasured_or_unlisted_lint():
    assert user_visible("no_output", _ledger("no_output", 0, 0))
    assert user_visible("no_output", {})


def test_user_visible_on_the_committed_ledger_pools_shapes():
    """stage6_render_warnings pools its Appendix shapes to well under 50%
    (appendix_no_route_T 2 / 20, appendix_recovered_A 3 / 20); role_consistency
    pools to 99%."""
    assert not user_visible("stage6_render_warnings")
    assert user_visible("role_consistency")


# --- the report block and its label ------------------------------------------

def test_precision_payload_one_entry_per_lint():
    ledger = _ledger("alpha_lint", 4, 8)
    payload = precision_payload(["alpha_lint", "alpha_lint", "unlisted_lint"], ledger)
    assert payload == {
        "alpha_lint": {"precision": 0.5, "judged": 8, "measured": "M9",
                       "label": "p~0.50 n=8", "remediation_allowed": False},
        "unlisted_lint": {"precision": None, "judged": 0, "measured": "",
                          "label": "p unmeasured", "remediation_allowed": False},
    }


def test_precision_payload_carries_the_gate():
    payload = precision_payload(["missed_headers"], _ledger("missed_headers", 18, 20))
    assert payload["missed_headers"]["remediation_allowed"] is True


def test_precision_label():
    assert precision_label(LintPrecision("a", 1, 6, "M1")) == "p~0.17 n=6"
    assert precision_label(LintPrecision("a", 0, 0, "M1")) == "p unmeasured"
    assert precision_label(None) == "p unmeasured"
