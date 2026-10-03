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
from unified_pipeline.stage6.fan_out import _RENDERED_FIELDS
from unified_pipeline.stage_6_word_template import (
    _REROUTE_ANCHOR_OVERRIDES,
    _SAME_FAMILY_KIND_FIELDS,
    _TAXONOMY_WARNED_CONFUSIONS,
    REROUTE_ACCEPTED_CROSS_FAMILY,
    REROUTE_ACCEPTED_SAME_FAMILY,
    REROUTE_CHECK,
    REROUTE_REFUSED_FIELDS,
    REROUTE_REFUSED_MENTEE,
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


# --- Class 3 (AUTOPSY-s7ab-batch-2026-10-02): a cross-family reroute must fit --
# A low-confidence cross-family reroute sent mentees, courses and committees to
# S8, whose renderer reads none of their fields: each rendered as a bare
# numbered item. Synthetic records, invented values.

def _fielded(code: str, expected: list[str], fields: dict, idx: int = 7,
             confidence: float = 0.55) -> dict:
    entry = _mismatched(code, expected, "Heading", confidence=confidence)
    entry.update({"element_idx_start": idx, "extracted_fields": fields})
    return entry


_MENTEE = {"mentee_name": "Pat Example", "mentee_level": "Resident",
           "research_focus": "synthetic topic", "start_date": "2001", "end_date": "2002"}


def _reroute_records(gen: WCMTemplateGenerator) -> list[dict]:
    return [w for w in gen._section_failures if w["check"] == REROUTE_CHECK]


def test_mentee_is_not_rerouted_to_a_citation_section_it_cannot_fill() -> None:
    entry = _fielded("N3B", ["R", "S8"], dict(_MENTEE), idx=1234)
    gen = WCMTemplateGenerator(verbose=False)
    groups = gen._group_entries_by_code([entry])
    assert list(groups) == ["N3B"]
    assert entry["taxonomy_code"] == "N3B"
    assert "taxonomy_code_original" not in entry
    [record] = _reroute_records(gen)
    assert (record["code"], record["severity"]) == ("N3B", "INFO")
    assert "N3B->S8" in record["message"] and REROUTE_REFUSED_FIELDS.replace("_", " ") in record["message"]
    assert record["evidence"] == ["element_idx_start 1234"]


def test_generic_date_place_role_fields_do_not_fit_a_target() -> None:
    # A course's `role` and a talk's `location` are rendered by R too, but
    # say nothing about the record being an invited presentation.
    course = _fielded("K1", ["R"], {"course_title": "Course X", "role": "Lecturer",
                                    "institution": "Example U", "start_date": "2010"})
    talk = _fielded("K5", ["R"], {"activity_title": "Talk X", "location": "Springfield",
                                  "date": "2011"})
    assert WCMTemplateGenerator(verbose=False)._correct_mismatch_if_needed(course, "K1") == "K1"
    assert WCMTemplateGenerator(verbose=False)._correct_mismatch_if_needed(talk, "K5") == "K5"


def test_record_with_target_fields_is_still_rerouted_and_warned() -> None:
    # An abstract under a Presentations heading carries a title R renders.
    entry = _fielded("S8", ["R"], {"authors": "Doe J", "title": "A synthetic abstract",
                                   "conference_name": "Meeting X", "year": "2015"}, idx=433)
    gen = WCMTemplateGenerator(verbose=False)
    assert list(gen._group_entries_by_code([entry])) == ["R"]
    assert entry["taxonomy_code_original"] == "S8"
    [record] = _reroute_records(gen)
    assert record["severity"] == "WARN"
    assert REROUTE_ACCEPTED_CROSS_FAMILY.replace("_", " ") in record["message"]


def test_pending_grant_needs_more_than_a_title() -> None:
    # A commentary (S2) under a pending-funding heading: a title alone fills an
    # otherwise empty grant table. An agency is what makes it a grant.
    expected = ["M2C"]
    bare = _fielded("S2", expected, {"title": "A synthetic commentary", "year": "2019"})
    funded = _fielded("S2", expected, {"title": "A synthetic proposal", "agency": "Agency X"})
    gen = WCMTemplateGenerator(verbose=False)
    assert gen._correct_mismatch_if_needed(bare, "S2") == "S2"
    assert gen._correct_mismatch_if_needed(funded, "S2") == "M2C"
    numbered = _fielded("S2", expected, {"title": "A synthetic award", "grant_number": "X01 000"})
    assert gen._correct_mismatch_if_needed(numbered, "S2") == "M2C"


def test_fields_inside_a_record_list_count_toward_the_fit() -> None:
    # The scalar alone does not fit S8; the list's titles do.
    entry = _fielded("N3B", ["R", "S8"], {"mentee_level": "Resident",
                                         "talks": [{"title": "One", "authors": "Doe J"},
                                                   {"title": "Two"}]})
    assert WCMTemplateGenerator(verbose=False)._correct_mismatch_if_needed(entry, "N3B") == "S8"


def test_blank_target_fields_do_not_fit() -> None:
    # Stage 4 writes every schema key; an empty or whitespace title is no title.
    entry = _fielded("N3B", ["R", "S8"], {**_MENTEE, "title": "  ", "authors": {},
                                         "doi": None})
    assert WCMTemplateGenerator(verbose=False)._correct_mismatch_if_needed(entry, "N3B") == "N3B"


def test_text_rendered_target_and_fieldless_entry_keep_the_old_rule() -> None:
    # K2 renders the entry's text, so any record fits; an entry with no
    # stage-4 fields has nothing to judge (see the H -> Q2 test above).
    membership = _fielded("I", ["K2"], {"organization": "Society X", "start_date": "2000"})
    assert WCMTemplateGenerator(verbose=False)._correct_mismatch_if_needed(membership, "I") == "K2"


def test_every_reroute_is_logged_without_entry_text() -> None:
    same = _mismatched("S6", ["S8"], "Abstracts")
    same["element_idx_start"] = 3
    refused = _fielded("N3B", ["R", "S8"], dict(_MENTEE), idx=4)
    also_refused = _fielded("N3B", ["R", "S8"], dict(_MENTEE), idx=5)
    gen = WCMTemplateGenerator(verbose=False)
    gen._group_entries_by_code([same, refused, also_refused])
    records = _reroute_records(gen)
    assert [(r["code"], r["severity"], r["evidence"]) for r in records] == [
        ("S6", "INFO", ["element_idx_start 3"]),
        ("N3B", "INFO", ["element_idx_start 4", "element_idx_start 5"]),
    ]
    assert REROUTE_ACCEPTED_SAME_FAMILY.replace("_", " ") in records[0]["message"]
    dumped = json.dumps(records)
    assert "Doe" not in dumped and "Pat Example" not in dumped


# --- EBYSBC class E18: an accepted reroute still misplaced the record ---------
# A target's anchor was any rendered, non-generic field, so a shared
# `institution` made a trainee the owner's degree row, a talk's `title` made it
# an appointment, and an `organization` made a membership a leadership row.
# Synthetic records, invented values.

def _gen_and_code(entry: dict) -> tuple[WCMTemplateGenerator, str]:
    gen = WCMTemplateGenerator(verbose=False)
    [code] = gen._group_entries_by_code([entry])
    return gen, code


def test_mentee_is_never_rerouted_to_an_owner_record() -> None:
    # Fields that would fit each target: a mentee is still someone else.
    fits = {"B1": {"degree": "PhD", "institution": "Example U"},
            "C": {"institution": "Example Hospital", "training_type": "Fellowship"},
            "D1": {"institution": "Example U", "title": "Research Fellow"}}
    for mentee_code in ("N3", "N3A", "N3B"):
        for target, fields in fits.items():
            entry = _fielded(mentee_code, [target], {**_MENTEE, **fields}, idx=81)
            gen, code = _gen_and_code(entry)
            assert code == mentee_code, (mentee_code, target)
            assert "taxonomy_code_original" not in entry
            [record] = _reroute_records(gen)
            assert REROUTE_REFUSED_MENTEE.replace("_", " ") in record["message"]
            assert (record["severity"], record["evidence"]) == ("INFO", ["element_idx_start 81"])


def test_degree_target_needs_a_degree() -> None:
    # A shared `institution` says nothing about a degree.
    training = _fielded("C", ["B1"], {"institution": "Example Hospital", "role": "Resident",
                                      "start_date": "2001"})
    assert _gen_and_code(training)[1] == "C"
    degree = _fielded("C", ["B1"], {"institution": "Example U", "degree": "MPH"})
    assert _gen_and_code(degree)[1] == "B1"


def test_position_target_needs_an_institution_and_a_title() -> None:
    talk = _fielded("S8", ["D1"], {"title": "A synthetic talk", "conference_name": "Meeting X",
                                   "year": "2020"})
    program = _fielded("B2", ["D1"], {"program_name": "Program X", "institution": "Society X",
                                      "start_date": "1999"})
    unit = _fielded("G", ["D1"], {"organization": "Center X", "department": "Department X"})
    for entry in (talk, program, unit):
        assert _gen_and_code(entry)[1] == entry["hierarchy_mismatch_detail"]["assigned_code"]
    position = _fielded("G", ["D1"], {"title": "Instructor", "institution": "Example U"})
    assert _gen_and_code(position)[1] == "D1"
    # D3 writes `organization`, not `institution`.
    for fields, expected in (({"title": "Analyst", "organization": "Firm X"}, "D3"),
                             ({"title": "Analyst", "institution": "Firm X"}, "G")):
        assert _gen_and_code(_fielded("G", ["D3"], fields))[1] == expected, fields


def test_leadership_target_needs_an_organization_and_a_role() -> None:
    # A membership type is not a role Q1 writes; a role with no organization
    # is a course or committee line.
    affiliate = _fielded("I", ["Q1"], {"organization": "Society X", "membership_type": "Affiliate",
                                       "start_date": "2020"})
    lecturer = _fielded("K3", ["Q1"], {"role": "Lecturer", "start_date": "2005"})
    for entry in (affiliate, lecturer):
        assert _gen_and_code(entry)[1] == entry["hierarchy_mismatch_detail"]["assigned_code"]
    president = _fielded("I", ["Q1"], {"organization": "Society X", "role": "President"})
    assert _gen_and_code(president)[1] == "Q1"


def test_chapter_is_not_rerouted_to_articles_by_its_heading() -> None:
    # Same family, high confidence: the book, its editors or its publisher make
    # it a chapter, and S1 writes none of them.
    for book_field in ("book_title", "editors", "publisher"):
        chapter = _fielded("S4", ["S1"], {"authors": "Doe J", "chapter_title": "Chapter X",
                                          book_field: "Value X", "year": "2008"},
                           idx=159, confidence=0.9)
        gen, code = _gen_and_code(chapter)
        assert code == "S4", book_field
        [record] = _reroute_records(gen)
        assert "S4->S1" in record["message"] and record["severity"] == "INFO"
        assert REROUTE_REFUSED_FIELDS.replace("_", " ") in record["message"]
    article = _fielded("S4", ["S1"], {"authors": "Doe J", "chapter_title": "Paper X",
                                      "year": "2008", "editors": " "}, confidence=0.9)
    assert _gen_and_code(article)[1] == "S1"


def test_anchor_and_kind_fields_are_what_the_renderers_write() -> None:
    # An anchor the target does not write would accept a record that renders
    # without it; a kind field the target DOES write loses nothing on the move.
    for target, groups in _REROUTE_ANCHOR_OVERRIDES.items():
        for group in groups:
            assert group and group <= _RENDERED_FIELDS[target], (target, group)
    for (assigned, target), kind in _SAME_FAMILY_KIND_FIELDS.items():
        assert assigned[0] == target[0], (assigned, target)
        assert kind <= _RENDERED_FIELDS[assigned] and not kind & _RENDERED_FIELDS[target]


# --- #983: multi-record entries fan out before the PII pass and dedup --------
# Invented values. Driven through `_group_entries_by_code` and the two calls
# `generate()` makes next, so a test fails if the fan-out stops preceding either.

def _three_honors(**scalars) -> dict:
    return {"taxonomy_code": "H", "element_idx_start": 3,
            "text": ("1986    Kappa Delta Honor Society, Hollis College\tBeta Sigma Honor Society, "
                     "Hollis College\tPhi Rho History Honor Society, Hollis College"),
            "extracted_fields": {"awards": [
                {"award_name": "Kappa Delta Honor Society", "granting_body": "Hollis College", "date": "1986"},
                {"award_name": "Beta Sigma Honor Society", "granting_body": "Hollis College", "date": "1986"},
                {"award_name": "Phi Rho History Honor Society", "granting_body": "Hollis College",
                 "date": "1986"}], **scalars}}


def test_grouping_replaces_a_multi_record_entry_with_one_entry_per_record() -> None:
    groups = WCMTemplateGenerator(verbose=False)._group_entries_by_code([_three_honors()])
    assert [e["extracted_fields"]["award_name"] for e in groups["H"]] == [
        "Kappa Delta Honor Society", "Beta Sigma Honor Society", "Phi Rho History Honor Society"]
    assert [e["fanned_out_from"]["index"] for e in groups["H"]] == [0, 1, 2]


def test_grouping_splits_the_records_stage4_kept_for_one_entry() -> None:
    """Stage 6 hands fan-out stage 4's records key: without it the list below
    would go to the generic rules, which find the parent an extra record."""
    from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY
    records = [{"committee_name": "Glade Board", "role": "Chair"},
               {"committee_name": "Fern Council", "role": "Member"}]
    entry = {"taxonomy_code": "P", "element_idx_start": 3,
             "text": "Chair, Glade Board\tMember, Fern Council\tAshby University",
             "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}
    groups = WCMTemplateGenerator(verbose=False)._group_entries_by_code([entry])
    assert [e["extracted_fields"]["committee_name"] for e in groups["P"]] == [
        "Glade Board", "Fern Council"]


def test_fanned_out_records_each_go_through_the_pii_pass() -> None:
    """A PII-keyed scalar the children inherit is dropped from EACH child, and
    a labelled fragment in one child's own text is cut from that child only."""
    parent = _three_honors(spouse_name="Pat Example")
    # The fragment sits in the second record's own field as well as the text:
    # an entry is fanned out only when the fields hold every token of the text.
    parent["extracted_fields"]["awards"][1]["granting_body"] = "Hollis College; Passport No.: X1234567"
    parent["text"] = parent["text"].replace(
        "Beta Sigma Honor Society, Hollis College",
        "Beta Sigma Honor Society, Hollis College; Passport No.: X1234567")
    gen = WCMTemplateGenerator(verbose=False)
    groups = gen._group_entries_by_code([parent])
    gen._run_pii_deny_pass(groups)
    honors = groups["H"]
    assert all("spouse_name" not in e["extracted_fields"] for e in honors)
    assert "X1234567" not in honors[1]["text"]
    assert "Beta Sigma Honor Society" in honors[1]["text"]
    assert len(gen._pii_result.withheld) == 4  # three inherited keys + one fragment


def test_a_fanned_out_record_survives_dedup_against_a_longer_entry() -> None:
    later_post = {"leadership_role": "Co-Leader, Genomics Program", "institution": "Ashby Cancer Center",
                  "start_date": "2012"}
    parent = {"taxonomy_code": "O", "text": "2012- Co-Leader, Genomics Program, Ashby Cancer Center\t"
                                             "2022- Deputy Director, Ashby Cancer Center",
              "extracted_fields": {"additional_roles": [
                  later_post, {"leadership_role": "Deputy Director", "institution": "Ashby Cancer Center",
                               "start_date": "2022"}]}}
    longer = {"taxonomy_code": "O", "text": "Co-Leader, Genomics Program Development\tAshby Cancer Center\t2012-2015",
              "extracted_fields": {"leadership_role": "Co-Leader, Genomics Program Development",
                                   "institution": "Ashby Cancer Center", "start_date": "2012", "end_date": "2015"}}
    gen = WCMTemplateGenerator(verbose=False)
    grouped, _, _ = gen._dedup_grouped_entries(gen._group_entries_by_code([longer, parent]))
    assert sorted(e["extracted_fields"]["leadership_role"] for e in grouped["O"]) == [
        "Co-Leader, Genomics Program", "Co-Leader, Genomics Program Development", "Deputy Director"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all checks passed")


# --- #666: a record fused into a kept entry's text reaches the page -------------
# Invented values. A kept M2A row fused two sub-grants and its fields name only
# the umbrella; the sub-grant stage 2 also extracted on its own is kept by dedup
# and rendered by the grant section itself, once.

_UMBRELLA_TEXT = ("Program Lead, Harbor Widget Initiative\t"
                  "Widget Outreach  PI: Dr Quill (1 of 4 Sites)  $111,111\t"
                  "Gizmo Clinic  PI: Dr Quill (1 of 2 Sites)  $222,222")


def _fused_grant_entries() -> list[dict]:
    umbrella = {"taxonomy_code": "M2A", "element_idx_start": 10, "text": _UMBRELLA_TEXT,
                "extracted_fields": {"title": "Harbor Widget Initiative",
                                     "pi_role": "Program Lead", "total_funding": "$333,333"}}
    sub = {"taxonomy_code": "M2A", "element_idx_start": 11,
           "text": "Gizmo Clinic  PI: Dr Quill (1 of 2 Sites)  $222,222",
           "extracted_fields": {"title": "Gizmo Clinic", "pi_name": "Dr Quill",
                                "total_funding": "$222,222"}}
    return [umbrella, sub]


def _m2a_titles(entries: list[dict]) -> list[str]:
    grouped, _, _ = WCMTemplateGenerator(verbose=False)._dedup_grouped_entries(
        WCMTemplateGenerator(verbose=False)._group_entries_by_code(entries))
    return [e["extracted_fields"]["title"] for e in grouped["M2A"]]


def test_dedup_keeps_a_record_only_the_kept_entry_text_carries() -> None:
    assert _m2a_titles(_fused_grant_entries()) == ["Harbor Widget Initiative", "Gizmo Clinic"]


def test_dedup_drops_that_record_when_an_entry_of_another_code_names_it() -> None:
    other = {"taxonomy_code": "T", "element_idx_start": 30,
             "text": "Program funding also lists Gizmo Clinic, 2021", "extracted_fields": {}}
    assert _m2a_titles(_fused_grant_entries() + [other]) == ["Harbor Widget Initiative"]


def test_the_kept_record_renders_once_in_the_grant_table(tmp_path) -> None:
    from docx import Document
    from docx.oxml.ns import qn

    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None  # no LLM, deterministic
    source = tmp_path / "in.json"
    source.write_text(json.dumps({"document_uid": "TESTAA", "entries": [
        {"taxonomy_code": "A", "element_idx_start": 0, "text": "Name: Pat Example, MD",
         "extracted_fields": {}}] + _fused_grant_entries()}))
    out = tmp_path / "out.docx"
    gen.generate(str(source), str(out), research_summary_path=None)
    body = Document(str(out)).element.body
    in_table = [p for p in body.iter(qn("w:p")) if p.xpath("ancestor::w:tc")]
    outside = [p for p in body.iter(qn("w:p")) if not p.xpath("ancestor::w:tc")]

    def text(p) -> str:
        return "".join(t.text or "" for t in p.iter(qn("w:t")))

    assert sum("Gizmo Clinic" in text(p) for p in in_table) == 1, "missing or rendered twice"
    assert not any("Gizmo Clinic" in text(p) for p in outside), "restored as a stray paragraph"


def test_dedup_keeps_one_of_two_identical_copies_of_the_fused_record() -> None:
    umbrella, sub = _fused_grant_entries()
    copy = json.loads(json.dumps(sub))
    assert _m2a_titles([umbrella, sub, copy]) == ["Harbor Widget Initiative", "Gizmo Clinic"]


def test_a_copy_dropped_in_an_earlier_group_does_not_vouch_for_the_record() -> None:
    # Invented. The T group comes first: its copy of the sub-grant is dropped
    # against a longer T entry whose words are reordered, so that entry does
    # not name the record. The dropped copy must not then let the M2A group
    # drop the sub-grant too.
    longer = {"taxonomy_code": "T", "element_idx_start": 1, "extracted_fields": {},
              "text": "Clinic Gizmo PI Dr Quill 1 of 2 Sites $222,222 renewed twice"}
    copy = {"taxonomy_code": "T", "element_idx_start": 2, "extracted_fields": {},
            "text": "Gizmo Clinic  PI: Dr Quill (1 of 2 Sites)  $222,222"}
    assert _m2a_titles([longer, copy] + _fused_grant_entries()) == [
        "Harbor Widget Initiative", "Gizmo Clinic"]
