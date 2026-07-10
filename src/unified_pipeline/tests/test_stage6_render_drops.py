"""Stage 6 must not silently drop content that reached it intact.

Both defects below were found by tracing real runs: the content is present in
<uid>_citation_formatted.json and absent from <uid>_wcm.docx. Values are the real
ones from those artifacts.

#261 - PZ69YW: aggregate mentee counts and outcome narrative.
#262 - 9TUVGW: a short but titled clinical-trial row.
"""

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator as G


# --- #262: the orphan-fragment guard must not discard titled entries -----------

def test_titled_short_entry_is_not_an_orphan_fragment():
    # Biotia: 54 chars, no date/audience/location/formatted_text -- but titled.
    fields = {"title": "Biotia-HSS Next Generation Sequencing Orthopedic Assay"}
    text = "Biotia-HSS Next Generation Sequencing Orthopedic Assay"
    assert len(text) < 80
    assert not G._is_orphan_fragment(fields, "", text)


def test_untitled_short_bare_entry_is_still_an_orphan_fragment():
    # A stray sub-header: short, no title, no other signal. Still dropped.
    assert G._is_orphan_fragment({}, "", "Clinical Innovations")
    assert G._is_orphan_fragment({"title": "   "}, "", "Clinical Innovations")


def test_long_or_dated_entries_survive_regardless_of_title():
    assert not G._is_orphan_fragment({}, "", "x" * 80)
    assert not G._is_orphan_fragment({"date": "2024"}, "", "short")
    assert not G._is_orphan_fragment({}, "formatted by stage 5c", "short")


# --- #261: mentee tables need a name; summaries and outcomes do not -----------

def test_named_entry_is_a_mentee_record():
    assert G._is_mentee_record({"extracted_fields": {"mentee_name": "Jane Doe"}})
    assert G._is_mentee_record({"extracted_fields": {"name": "Jane Doe"}})


def test_aggregate_counts_are_not_mentee_records():
    # These render as summary lines, not per-mentee tables. Previously dropped:
    # _create_mentee_table returns None when the name cell is empty.
    for text, level in [("Ph.D. Graduated: 38", "Ph.D. Graduated"),
                        ("Current Ph.D. Students: 12", "Ph.D."),
                        ("Completed: 27", "Completed"),
                        ("Current: 8", None)]:
        entry = {"text": text, "extracted_fields": {"mentee_name": None, "mentee_level": level}}
        assert not G._is_mentee_record(entry), text


def test_blank_name_is_not_a_mentee_record():
    assert not G._is_mentee_record({"extracted_fields": {"mentee_name": "  "}})
    assert not G._is_mentee_record({})


def test_n4_outcome_detected_before_and_after_mismatch_rewrite():
    # _correct_mismatch_if_needed rewrites N4 -> N3A and stashes the original,
    # so an outcome must still be recognised once rerouted.
    assert G._is_mentoring_outcome({"taxonomy_code": "N4"})
    assert G._is_mentoring_outcome({"taxonomy_code": "N3A", "taxonomy_code_original": "N4"})
    assert not G._is_mentoring_outcome({"taxonomy_code": "N3A"})
    assert not G._is_mentoring_outcome({"taxonomy_code": "N3B", "taxonomy_code_original": "N3A"})


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
