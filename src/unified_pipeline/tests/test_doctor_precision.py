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
    IN_PLACE_MIN_PRECISION,
    STAGE6_LINT,
    STAGE6_MESSAGE_PREFIX,
    STAGE6_OTHER_SHAPE,
    LintPrecision,
    finding_precision,
    load_ledger,
    load_shape_ledger,
    parse_ledger,
    parse_shape_ledger,
    precision_label,
    precision_payload,
    remediation_allowed,
    shown_in_place,
    stage6_shape,
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


def test_stage6_shape_names_each_emitter_message():
    cases = {
        "hierarchy-mismatch reroute K1->S8 refused fields do not fit: 1 entry": "reroute_refused",
        "hierarchy-mismatch reroute I->Q1 accepted cross family: 2 entries": "reroute_cross_family",
        "hierarchy-mismatch reroute S5->S1 accepted same family: 1 entry": "reroute_same_family",
        "A: 1 entry classified A was not found in the rendered document and was recovered "
        "into the Appendix": "appendix_recovered_A",
        "T: 1 entry classified T was not found in the rendered document and was recovered "
        "into the Appendix": "appendix_recovered",
        "K4: 2 segments of overflow content that the stage 6 reconsider pass coded K4 were "
        "not placed in a section and were recovered into the Appendix": "appendix_recovered",
        "T: 6 entries diverted to the Appendix — no stage 6 section is routed to render this "
        "taxonomy code": "appendix_no_route_T",
        "N3: 2 entries diverted to the Appendix — no stage 6 section is routed to render this "
        "taxonomy code": "appendix_no_route",
        "M2B: 1 entry diverted to the Appendix — declined by the research-support renderer as "
        "too sparse to table": "appendix_grant_too_sparse",
        "M1: 2 entries diverted to the Appendix — stage 3b T-validation recoded them from T to "
        "M1, which only the research summary renders": "appendix_t_validation_recoded",
        "K (Teaching): No visible bulleted content found - may be using track changes only":
            "no_teaching_content",
        "K3 (Synthetic Heading): Content appears combined with semicolons instead of separate "
        "bullets": "semicolon_fused_bullets",
        "Table 'Synthetic': 3 rows have bare dates in column A (should be filtered)":
            "bare_dates_in_table",
        "a reconstructed board certification row had no specialty or certificate number and "
        "was skipped": "board_cert_row_skipped",
        "P: entry at element 12 dropped as a source table header row":
            "memberships_header_row_dropped",
        "2 D1 entries: `appointments` holds several records that were not split into separate "
        "rows (the entry's text holds content its fields do not carry); a record may be missing "
        "from the output": "fanout_list_not_split",
        "X1: 1 entry diverted to the Appendix — stage 4 quarantined it: the stage 3b taxonomy "
        "code was not a valid taxonomy code (see original_taxonomy_code in the stage 4 artifact)":
            "appendix_invalid_code",
        "G: 3 entries diverted to the Appendix — refused by the passthrough writer for G (source "
        "section label did not match)": "appendix_passthrough_refused",
        "K1: 2 entries diverted to the Appendix — not placed by the section routed for K1 (see "
        "any section_render_failed record for that section)": "appendix_section_declined",
        "M1: 1 entry diverted to the Appendix — no research summary rendered":
            "appendix_no_research_summary",
        "M1: 1 dated entry diverted to the Appendix — the generated research summary does not "
        "reproduce it and no other section renders it": "appendix_m1_not_in_summary",
        "2 D1 entries: `appointments` holds records that were not split into separate rows (the "
        "entry's text holds content its fields do not carry); a record may be missing from the "
        "output": "fanout_list_not_split",
        "2 geographic scope classification(s) failed and defaulted to National; the Regional/"
        "National/International split may be wrong": "geo_scope_failed",
        "1 appendix entry reclassification(s) failed or came back incomplete; those entries "
        "stayed in the appendix whole instead of being split and routed to their sections":
            "appendix_reclassification_failed",
        "section K failed: ValueError: synthetic": "section_failed",
        "a message no emitter writes": STAGE6_OTHER_SHAPE,
    }
    for message, shape in cases.items():
        assert stage6_shape(STAGE6_MESSAGE_PREFIX + message) == shape, (message, shape)


_SHAPE_LEDGER = """## Per-lint precision

| lint | TP / judged | measured |
|---|---|---|
| `role_consistency`: `owner_pi_role_empty` | 9 / 9 (100%) | X6-role |
| `role_consistency`: `pi_cell_empty` | 1 / 4 (25%) | RC-ROLE2 |
| `stage6_render_warnings`: `reroute_refused` | 9 / 10 (90%) | M1 |
| `stage6_render_warnings`: `appendix_recovered_A` | 1 / 10 (10%) | M1 |
| `owner_attribution`: `citation_without_owner` | 1 / 9 (11%) | YUY-OA |
| `owner_attribution`: `mentee_under_non_mentee_heading` | 1 / 1 (100%) | YUY-OA |
| `half_lint` | 1 / 2 (50%) | M1 |
| `low_lint` | 2 / 5 (40%) | M1 |
| `none_lint` | none | M1 |
"""


def test_shape_rows_are_kept_apart_unpooled():
    rows = parse_shape_ledger(_SHAPE_LEDGER)
    assert rows[("role_consistency", "pi_cell_empty")] == LintPrecision(
        "role_consistency", 1, 4, "RC-ROLE2", "pi_cell_empty")
    assert rows[("low_lint", None)] == LintPrecision("low_lint", 2, 5, "M1")
    assert parse_ledger(_SHAPE_LEDGER)["role_consistency"] == LintPrecision(
        "role_consistency", 10, 13, "X6-role,RC-ROLE2")


def test_a_finding_reads_the_row_of_the_shape_its_message_names():
    rows = parse_shape_ledger(_SHAPE_LEDGER)
    empty = "entry 7: 'Your role:' is MPI, but the PI cell is empty (pi_cell_empty, #1403)"
    named = "entry 8: the PI cell names the CV owner, and 'Your role:' is empty (owner_pi_role_empty, #1403)"
    assert finding_precision("role_consistency", empty, rows).shape == "pi_cell_empty"
    assert finding_precision("role_consistency", named, rows).shape == "owner_pi_role_empty"
    assert not shown_in_place("role_consistency", empty, rows)
    assert shown_in_place("role_consistency", named, rows)


def test_a_shape_name_inside_a_longer_token_is_not_that_shape():
    # Listed first, so a bare substring test would pick it.
    rows = {("role_consistency", "role_empty"): LintPrecision("role_consistency", 0, 9, "", "role_empty"),
            **parse_shape_ledger(_SHAPE_LEDGER)}
    named = "entry 8: ... (owner_pi_role_empty, #1403)"
    assert finding_precision("role_consistency", named, rows).shape == "owner_pi_role_empty"


def test_a_message_naming_no_shape_reads_its_lints_rows_pooled():
    rows = parse_shape_ledger(_SHAPE_LEDGER)
    entry = finding_precision("owner_attribution", "entries 3-3 (S1), whose author list never names the owner", rows)
    assert (entry.true_positives, entry.judged) == (2, 10)
    assert not shown_in_place("owner_attribution", "entries 3-3 (S1) ...", rows)


def test_a_stage6_message_reads_its_own_shapes_row_and_no_pool():
    rows = parse_shape_ledger(_SHAPE_LEDGER)
    recovered = STAGE6_MESSAGE_PREFIX + "A: 1 entry classified A was not found and was recovered into the Appendix"
    refused = STAGE6_MESSAGE_PREFIX + "hierarchy-mismatch reroute K1->S8 refused fields do not fit: 1 entry"
    assert not shown_in_place(STAGE6_LINT, recovered, rows)
    assert shown_in_place(STAGE6_LINT, refused, rows)
    # A shape with no row is unmeasured, not the other shapes' pool (10 / 20).
    assert finding_precision(STAGE6_LINT, STAGE6_MESSAGE_PREFIX + "an unknown check", rows) is None
    assert shown_in_place(STAGE6_LINT, STAGE6_MESSAGE_PREFIX + "an unknown check", rows)


def test_the_gate_is_below_half_and_spares_the_unmeasured():
    rows = parse_shape_ledger(_SHAPE_LEDGER)
    assert IN_PLACE_MIN_PRECISION == 0.50
    assert shown_in_place("half_lint", "x", rows)  # exactly half is shown (Paul, 2026-10-08)
    assert not shown_in_place("low_lint", "x", rows)
    assert shown_in_place("none_lint", "x", rows)  # listed, no verdicts
    assert shown_in_place("absent_lint", "x", rows)  # not in the ledger


def test_the_committed_ledger_gates_by_shape():
    """On the real ledger: owner_pi_role_empty is shown, a recovered A entry is not."""
    load_shape_ledger.cache_clear()
    assert shown_in_place("role_consistency", "entry 1: ... (owner_pi_role_empty, #1403)")
    assert not shown_in_place(STAGE6_LINT, STAGE6_MESSAGE_PREFIX + "A: 1 entry ... recovered into the Appendix")
