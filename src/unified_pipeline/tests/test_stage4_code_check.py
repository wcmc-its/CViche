"""#651: `stage4.code_check.quarantine_invalid_taxonomy_codes` -- the 3b -> 4
membership check (CODING_STANDARDS.md 5.10). An unrecognized code is re-coded T
with the failed predicate named, never silently defaulted to DEFAULT_SCHEMA.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_code_check.py -p no:cacheprovider

Synthetic entries only; no network, no LLM.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.stage3b.io import canonical_taxonomy_codes  # noqa: E402
from unified_pipeline.stage4.code_check import (  # noqa: E402
    INVALID_CODE_REASON,
    quarantine_invalid_taxonomy_codes,
)
from unified_pipeline.stage4.schemas import get_field_schema  # noqa: E402


def _e(code):
    return {"text": "synthetic entry text", "taxonomy_code": code}


def test_every_canonical_code_passes_through_as_the_same_object():
    entries = [_e(c) for c in sorted(canonical_taxonomy_codes())]
    out, rejected = quarantine_invalid_taxonomy_codes(entries)
    assert rejected == {}
    assert all(a is b for a, b in zip(out, entries))


def test_valid_codes_keep_their_stage4_schema():
    # Blast-radius pin: the check must not change schema selection for a valid code.
    for c in canonical_taxonomy_codes():
        out, _ = quarantine_invalid_taxonomy_codes([_e(c)])
        assert get_field_schema(out[0]["taxonomy_code"]) == get_field_schema(c)


def test_unknown_code_is_recoded_t_with_named_reason_and_original_kept():
    entries = [_e("ZZ9")]
    out, rejected = quarantine_invalid_taxonomy_codes(entries)
    assert out[0]["taxonomy_code"] == "T"
    assert out[0]["original_taxonomy_code"] == "ZZ9"
    assert out[0]["taxonomy_code_quarantine_reason"] == INVALID_CODE_REASON
    assert rejected == {"'ZZ9'": 1}
    assert entries[0]["taxonomy_code"] == "ZZ9"  # caller's entry not mutated


def test_retired_invalid_codes_from_the_taxonomy_are_rejected():
    # taxonomy_v7.json's own `invalid_codes` list ("must not be used in final
    # classification output") is not in `codes`, so it fails membership.
    out, rejected = quarantine_invalid_taxonomy_codes([_e("S10"), _e("Q5")])
    assert [e["taxonomy_code"] for e in out] == ["T", "T"]
    assert sum(rejected.values()) == 2


def test_missing_null_and_non_string_codes_are_quarantined_not_raised():
    entries = [{"text": "x"}, _e(None), _e(["A"]), _e(7), _e("")]
    out, rejected = quarantine_invalid_taxonomy_codes(entries)
    assert all(e["taxonomy_code"] == "T" for e in out)
    assert all(e["taxonomy_code_quarantine_reason"] == INVALID_CODE_REASON for e in out)
    assert sum(rejected.values()) == 5


def test_a_genuine_t_is_not_marked_quarantined():
    out, rejected = quarantine_invalid_taxonomy_codes([_e("T")])
    assert rejected == {}
    assert "taxonomy_code_quarantine_reason" not in out[0]


def test_one_warning_per_distinct_bad_code(caplog):
    with caplog.at_level(logging.WARNING, logger="unified_pipeline.stage4.code_check"):
        quarantine_invalid_taxonomy_codes([_e("ZZ9"), _e("ZZ9"), _e("YY1")])
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2
    assert any("2 entries" in m and "ZZ9" in m for m in msgs)


def test_reasoning_corrector_output_outside_v7_is_quarantined_at_stage_4():
    # #651 second producer: stage 3b's apply_reasoning_corrections rewrites
    # taxonomy_code to anything in its own VALID_CODES, which includes C1 (not
    # in taxonomy_v7.json). Pins the consequence: such an entry reaches stage 4
    # as C1 and is re-coded T with the original kept (was: rendered by the
    # postdoc_training renderer via RENDER_ROUTED_CODES). Judgement call, #383.
    from unified_pipeline.core.validators.reasoning_consistency_checker import (
        apply_reasoning_corrections,
    )
    corrected, _ = apply_reasoning_corrections([{
        "text": "Postdoctoral trainee, Example Institute",
        "taxonomy_code": "D1",
        "classification_reasoning": "Training position; should be C1.",
    }])
    assert corrected[0]["taxonomy_code"] == "C1"
    assert "C1" not in canonical_taxonomy_codes()
    out, rejected = quarantine_invalid_taxonomy_codes(corrected)
    assert out[0]["taxonomy_code"] == "T"
    assert out[0]["original_taxonomy_code"] == "C1"
    assert rejected == {"'C1'": 1}
