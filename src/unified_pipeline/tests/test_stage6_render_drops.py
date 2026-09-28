"""Stage 6 must not silently drop content that reached it intact.

Both defects below were found by tracing real runs: the content is present in
<uid>_citation_formatted.json and absent from <uid>_wcm.docx. Values are the real
ones from those artifacts.

#261 - PZ69YW: aggregate mentee counts and outcome narrative.
#262 - 9TUVGW: a short but titled clinical-trial row.
#946 - ZA1VOV: submitted chapters rerouted from "In review" to "Books:" by the
       hierarchy-mismatch correction (synthetic fixtures below, no CV values).
"""

# The three predicates below moved to stage6/parsing/records.py in the #398
# split. They were @staticmethod on the generator, and this file already knew
# it -- it reached them through the class, never an instance.
import json
import re
from pathlib import Path

from unified_pipeline.stage6.parsing import (
    _is_mentee_record,
    _is_mentoring_outcome,
    _is_orphan_fragment,
)
from unified_pipeline.stage_6_word_template import (
    _TAXONOMY_WARNED_CONFUSIONS,
    WCMTemplateGenerator,
)

_TAXONOMY_V7 = Path(__file__).resolve().parents[1] / "core" / "taxonomy_v7.json"


# --- #262: the orphan-fragment guard must not discard titled entries -----------

def test_titled_short_entry_is_not_an_orphan_fragment():
    # Biotia: 54 chars, no date/audience/location/formatted_text -- but titled.
    fields = {"title": "Biotia-HSS Next Generation Sequencing Orthopedic Assay"}
    text = "Biotia-HSS Next Generation Sequencing Orthopedic Assay"
    assert len(text) < 80
    assert not _is_orphan_fragment(fields, "", text)


def test_untitled_short_bare_entry_is_still_an_orphan_fragment():
    # A stray sub-header: short, no title, no other signal. Still dropped.
    assert _is_orphan_fragment({}, "", "Clinical Innovations")
    assert _is_orphan_fragment({"title": "   "}, "", "Clinical Innovations")


def test_long_or_dated_entries_survive_regardless_of_title():
    assert not _is_orphan_fragment({}, "", "x" * 80)
    assert not _is_orphan_fragment({"date": "2024"}, "", "short")
    assert not _is_orphan_fragment({}, "formatted by stage 5c", "short")


# --- #261: mentee tables need a name; summaries and outcomes do not -----------

def test_named_entry_is_a_mentee_record():
    assert _is_mentee_record({"extracted_fields": {"mentee_name": "Jane Doe"}})
    assert _is_mentee_record({"extracted_fields": {"name": "Jane Doe"}})


def test_aggregate_counts_are_not_mentee_records():
    # These render as summary lines, not per-mentee tables. Previously dropped:
    # _create_mentee_table returns None when the name cell is empty.
    for text, level in [("Ph.D. Graduated: 38", "Ph.D. Graduated"),
                        ("Current Ph.D. Students: 12", "Ph.D."),
                        ("Completed: 27", "Completed"),
                        ("Current: 8", None)]:
        entry = {"text": text, "extracted_fields": {"mentee_name": None, "mentee_level": level}}
        assert not _is_mentee_record(entry), text


def test_blank_name_is_not_a_mentee_record():
    assert not _is_mentee_record({"extracted_fields": {"mentee_name": "  "}})
    assert not _is_mentee_record({})


def test_n4_outcome_detected_before_and_after_mismatch_rewrite():
    # _correct_mismatch_if_needed rewrites N4 -> N3A and stashes the original,
    # so an outcome must still be recognised once rerouted.
    assert _is_mentoring_outcome({"taxonomy_code": "N4"})
    assert _is_mentoring_outcome({"taxonomy_code": "N3A", "taxonomy_code_original": "N4"})
    assert not _is_mentoring_outcome({"taxonomy_code": "N3A"})
    assert not _is_mentoring_outcome({"taxonomy_code": "N3B", "taxonomy_code_original": "N3A"})


# --- #946 item 3: hierarchy-mismatch reroute (`_correct_mismatch_if_needed`) ---
# Synthetic entries shaped like stage 3b's `hierarchy_mismatch_detail`. Driven
# through `_group_entries_by_code`, the grouping `generate()` renders from, so
# a test fails if the correction stops reaching the groups, not just the helper.

def _mismatched(code: str, expected: list[str], heading: str,
                confidence: float = 0.95) -> dict:
    return {"text": f"Doe J. A synthetic {code} entry.", "taxonomy_code": code,
            "taxonomy_confidence": confidence, "hierarchy_mismatch_flag": True,
            "hierarchy_mismatch_detail": {"hierarchy": [heading], "assigned_code": code,
                                          "expected_codes": expected}}


def _group(*entries: dict) -> dict[str, list[str]]:
    gen = WCMTemplateGenerator(verbose=False)
    return {code: [e["text"] for e in group]
            for code, group in gen._group_entries_by_code(list(entries)).items()}


def test_submitted_chapter_under_book_chapters_stays_in_review() -> None:
    # ZA1VOV's shape: S7 "(Submitted, In editing)" under "Book Chapters",
    # expected ['S3', 'S4']. Status beats heading -- never Books.
    entry = _mismatched("S7", ["S3", "S4"], "Book Chapters")
    assert _group(entry) == {"S7": [entry["text"]]}
    assert entry["taxonomy_code"] == "S7"
    assert "taxonomy_code_original" not in entry


def test_s7_is_not_rerouted_even_to_a_single_unambiguous_section() -> None:
    # One longest expected code, same family: rerouted before #946.
    entry = _mismatched("S7", ["S", "S8"], "Abstracts")
    assert _group(entry) == {"S7": [entry["text"]]}


def test_expected_codes_tied_across_sections_skip_the_reroute_in_any_order() -> None:
    # S3 (books) and S4 (book_chapters) tie at length 2: the heading does
    # not say which, so the classifier's S1 stands -- whatever order stage 3b
    # listed them in (it is a set, so either order reaches stage 6).
    for expected in (["S3", "S4"], ["S4", "S3"]):
        entry = _mismatched("S1", expected, "Books and Book Chapters")
        assert _group(entry) == {"S1": [entry["text"]]}, expected
        assert "taxonomy_code_original" not in entry


def test_tie_within_one_section_still_reroutes_deterministically() -> None:
    # K1 and K2 both render under 'teaching', so the tie is harmless: the
    # reroute still happens, and to the same code in either order.
    for expected in (["K1", "K2"], ["K2", "K1"]):
        entry = _mismatched("K3", expected, "Teaching")
        assert _group(entry) == {"K1": [entry["text"]]}, expected
        assert entry["taxonomy_code_original"] == "K3"


def test_a_single_section_heading_still_reroutes_same_family() -> None:
    entry = _mismatched("S6", ["S8"], "Abstracts")
    assert _group(entry) == {"S8": [entry["text"]]}
    assert entry["taxonomy_code_original"] == "S6"


def test_same_family_skipped_when_expected_codes_span_sections() -> None:
    # "Committees" -> P, Q2, O: three sections. The longest-code pick made
    # every Q3 there Q2 (#946 item 3: 69 corpus rows); the classifier stands.
    entry = _mismatched("Q3", ["P", "Q2", "O"], "Committees")
    assert _group(entry) == {"Q3": [entry["text"]]}
    assert "taxonomy_code_original" not in entry
    # Two sections are already too many.
    two = _mismatched("Q3", ["P", "Q2"], "Committees")
    assert _group(two) == {"Q3": [two["text"]]}


def test_cross_family_low_confidence_rule_is_unchanged() -> None:
    # The span-of-sections rule is same-family only: cross-family still
    # reroutes on low confidence, whatever the heading names.
    low = _mismatched("H", ["P", "Q2", "O"], "Committees", confidence=0.5)
    high = _mismatched("H", ["P", "Q2", "O"], "Committees", confidence=0.9)
    assert WCMTemplateGenerator(verbose=False)._correct_mismatch_if_needed(low, "H") == "Q2"
    assert WCMTemplateGenerator(verbose=False)._correct_mismatch_if_needed(high, "H") == "H"


_WARNED_PAIRS = [("D3", "D1"), ("K1", "K4"), ("K4", "K1"), ("K5", "K4"),
                 ("Q1", "Q2"), ("Q2", "Q3"), ("S1", "S8"), ("S2", "S1")]


def test_taxonomy_warned_pairs_are_never_rerouted() -> None:
    # One-section heading, so only the warned-pair rule can stop each one.
    assert sorted(_TAXONOMY_WARNED_CONFUSIONS) == sorted(_WARNED_PAIRS)
    for assigned, target in _WARNED_PAIRS:
        entry = _mismatched(assigned, [target], "Heading")
        assert _group(entry) == {assigned: [entry["text"]]}, (assigned, target)
        assert "taxonomy_code_original" not in entry


def test_each_warned_pair_is_named_by_the_taxonomy() -> None:
    # Pins the frozenset to its source: the target code's common_confusions
    # says "... misclassified as <target> instead of <assigned>".
    codes = {c["code"]: c for c in json.loads(_TAXONOMY_V7.read_text())["codes"]}
    for assigned, target in sorted(_TAXONOMY_WARNED_CONFUSIONS):
        warnings = " ".join(codes[target].get("common_confusions", []))
        assert re.search(
            rf"misclassified as {target}\b[^.;]*instead of[^.;]*\b{assigned}\b", warnings,
        ), (assigned, target)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
