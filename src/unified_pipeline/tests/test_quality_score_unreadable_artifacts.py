"""Regression guard for #497: quality_score._load_first collapsed a corrupt
artifact into the same "not found" outcome as a genuinely absent one.

`_load_first` returned bare `None` on both a missing glob match and a matched
file that failed to parse, so every call site (score_cv_owner, score_t_bucket,
score_field_sparseness, and both of score_duplicate_ratio's) reported "no
X.json found" for a truncated or half-written artifact -- the exact shape a
crashed or OOM-killed stage leaves behind. run_doctor already tells the two
outcomes apart (`_load_json`'s `on_unreadable` callback); this pins the scorer
doing the same, with the score itself held identical either way.

    python3 -m pytest src/unified_pipeline/tests/test_quality_score_unreadable_artifacts.py -p no:cacheprovider

Self-contained: no DB, no network, no PII. All artifacts are synthetic tmp
files -- the farm corpus has 0 unparsable JSON artifacts (scouted over 1830
files), so the truncated-file case here is necessarily a manufactured one.
"""

import json
import logging
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.quality_score import (  # noqa: E402
    score_cv_owner,
    score_duplicate_ratio,
    score_t_bucket,
)

_VALID_FIELDS = {
    "document_uid": "TESTAA",
    "cv_owner": {"first_name": "Jane", "last_name": "Public",
                 "full_name": "Jane Q. Public"},
    "cv_owner_location": {"inference_success": True,
                          "primary_location": "New York, NY"},
    "entries": [{"taxonomy_code": "A",
                "extracted_fields": {"email": "jane@example.com"}}],
}

_VALID_CLASSIFIED = {
    "meta": {"total_entries": 4, "duplicate_entries": 0,
             "code_distribution": {"A": 3, "T": 1}},
}


def _truncate(tmp_path: Path, name: str) -> Path:
    """Write a file that matches the glob but is not valid JSON -- the
    truncated-mid-write shape a crashed/OOM-killed stage leaves behind."""
    p = tmp_path / name
    p.write_text('{"cv_owner": {"full_name": "Jane"')  # deliberately unclosed
    return p


# --------------------------------------------------------------------- #497
# score_cv_owner / *_fields.json
# --------------------------------------------------------------------- #497


def test_cv_owner_unreadable_fields_json_is_named_distinctly(tmp_path, caplog):
    """Positive control: FAILS on dev, where both outcomes say 'not found'."""
    _truncate(tmp_path, "TESTAA_fields.json")
    with caplog.at_level(logging.WARNING, logger="unified_pipeline.quality_score"):
        fraction, detail, cap = score_cv_owner(tmp_path)

    assert "unreadable" in detail, detail
    assert "not found" not in detail, detail
    assert fraction == 1.0
    assert cap == 25
    assert any("TESTAA_fields.json" in r.message for r in caplog.records), \
        "expected a warning naming the unreadable path"


def test_cv_owner_absent_fields_json_still_says_not_found(tmp_path):
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert detail == "no fields.json found", detail
    assert "unreadable" not in detail, detail
    assert fraction == 1.0
    assert cap == 25


def test_cv_owner_unreadable_and_absent_score_identically(tmp_path_factory):
    absent_dir = tmp_path_factory.mktemp("cv_owner_absent")
    unreadable_dir = tmp_path_factory.mktemp("cv_owner_unreadable")
    _truncate(unreadable_dir, "TESTAA_fields.json")

    a_fraction, _, a_cap = score_cv_owner(absent_dir)
    u_fraction, _, u_cap = score_cv_owner(unreadable_dir)

    assert a_fraction == u_fraction == 1.0
    assert a_cap == u_cap == 25


def test_cv_owner_valid_fields_json_still_loads_and_scores(tmp_path):
    (tmp_path / "TESTAA_fields.json").write_text(json.dumps(_VALID_FIELDS))
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert cap is None
    assert "not found" not in detail
    assert "unreadable" not in detail
    assert "full_name='Jane Q. Public'" in detail


# --------------------------------------------------------------------- #497
# score_t_bucket / *_classified.json
# --------------------------------------------------------------------- #497


def test_t_bucket_unreadable_classified_json_is_named_distinctly(tmp_path, caplog):
    _truncate(tmp_path, "TESTAA_classified.json")
    with caplog.at_level(logging.WARNING, logger="unified_pipeline.quality_score"):
        fraction, detail, cap = score_t_bucket(tmp_path)

    assert "unreadable" in detail, detail
    assert "not found" not in detail, detail
    assert fraction == 1.0
    assert cap is None
    assert any("TESTAA_classified.json" in r.message for r in caplog.records)


def test_t_bucket_absent_classified_json_still_says_not_found(tmp_path):
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert detail == "no classified.json found", detail
    assert fraction == 1.0
    assert cap is None


def test_t_bucket_valid_classified_json_still_loads_and_scores(tmp_path):
    (tmp_path / "TESTAA_classified.json").write_text(json.dumps(_VALID_CLASSIFIED))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "not found" not in detail
    assert "unreadable" not in detail
    assert "T_count=1" in detail


# --------------------------------------------------------------------- #497
# score_duplicate_ratio / *_entries.json (the secondary _load_first call)
# --------------------------------------------------------------------- #497


def test_duplicate_ratio_unreadable_entries_json_is_named_distinctly(tmp_path, caplog):
    (tmp_path / "TESTAA_classified.json").write_text(json.dumps(_VALID_CLASSIFIED))
    _truncate(tmp_path, "TESTAA_entries.json")

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.quality_score"):
        fraction, detail, cap = score_duplicate_ratio(tmp_path)

    assert "entries.json unreadable" in detail, detail
    assert cap is None
    assert any("TESTAA_entries.json" in r.message for r in caplog.records)


def test_duplicate_ratio_unreadable_and_absent_entries_json_score_identically(
    tmp_path_factory,
):
    """entries.json only ever nudges the fraction via coverage_pct > 130; an
    unparsable file must leave coverage_pct None exactly like an absent one,
    so the two dirs must score byte-identically apart from the detail note."""
    absent_dir = tmp_path_factory.mktemp("dup_entries_absent")
    unreadable_dir = tmp_path_factory.mktemp("dup_entries_unreadable")
    for d in (absent_dir, unreadable_dir):
        (d / "TESTAA_classified.json").write_text(json.dumps(_VALID_CLASSIFIED))
    _truncate(unreadable_dir, "TESTAA_entries.json")

    a_fraction, a_detail, a_cap = score_duplicate_ratio(absent_dir)
    u_fraction, u_detail, u_cap = score_duplicate_ratio(unreadable_dir)

    assert a_fraction == u_fraction
    assert a_cap == u_cap is None
    assert "entries_coverage_pct=None" in a_detail
    assert "entries_coverage_pct=None" in u_detail
    assert "entries.json unreadable" not in a_detail
    assert "entries.json unreadable" in u_detail
