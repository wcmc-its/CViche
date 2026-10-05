"""Behaviour tests for Stage 4.5 -- Research Summary Generation (issue #704).

Covers the pure scoring/selection layer (score_entry_seniority,
prioritize_entries, gather_context_entries, is_valid_entry,
format_entry_for_context, build_context_string) and the LLM-driven layer
(score_existing_m1, generate_research_summary, and run_stage_4_5's M1
keep-vs-regenerate decision).

Self-contained: ``call_llm`` is stubbed at the module attribute
(``unified_pipeline.stage_4_5_research_summary.call_llm``) in every test
that reaches it -- no Bedrock/OpenAI, no network. All CV content is
synthetic; nothing under data/, _batch_runs, or any real .docx is read.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage4_5_research_summary_behaviour.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_4_5_research_summary as stage_4_5  # noqa: E402
from unified_pipeline.llm.bedrock import (  # noqa: E402
    BedrockContentFilteredError,
    BedrockEmptyResponseError,
)
from unified_pipeline.llm.retry import LLMOutageError  # noqa: E402
from unified_pipeline.llm_provenance import (  # noqa: E402
    STAGE4_5_CALL_FAILURES_KEY,
    STAGE4_5_FALLBACK_CALLS_KEY,
)
from unified_pipeline.stage_4_5_research_summary import (  # noqa: E402
    CURRENT_CONTEXT_TAG,
    CURRENT_WORK_REQUIREMENT,
    NEUTRAL_REFERENCE_REQUIREMENT,
    ONGOING_PATTERN,
    PI_BONUS,
    RECENCY_WEIGHT,
    SENIOR_AUTHOR_BONUS,
    EntryRecency,
    build_context_string,
    compute_entry_recency,
    format_entry_for_context,
    gather_context_entries,
    is_current_entry,
    is_valid_entry,
    latest_entry_year,
    prioritize_entries,
    run_stage_4_5,
    score_entry_recency,
    score_entry_seniority,
)

# Placeholder EntryRecency for build_context_string tests that exercise the
# char-budget/filtering logic and do not care about the [CURRENT] tag.
_NOT_CURRENT = EntryRecency(is_current=False, latest_year=None, score=0.0)

# The scoring call is the only prompt that says this (stage_4_5 score_existing_m1).
_SCORE_PROMPT_MARKER = "CONTENT TO SCORE"


# --- score_entry_seniority ----------------------------------------------------

def test_score_entry_seniority_pi_role_gets_full_bonus():
    entry = {"extracted_fields": {"pi_role": "Principal Investigator"}, "text": "x"}
    assert score_entry_seniority(entry, "M2A") == PI_BONUS


def test_score_entry_seniority_co_pi_gets_partial_bonus():
    entry = {"extracted_fields": {"pi_role": "Co-PI"}, "text": "x"}
    assert score_entry_seniority(entry, "M2A") == PI_BONUS * 0.7


def test_score_entry_seniority_non_pi_role_gets_no_bonus():
    """"Co-Investigator" contains neither "principal investigator" nor
    "co-pi"/"co-principal", so it must fall through to 0.0."""
    entry = {"extracted_fields": {"role": "Co-Investigator"}, "text": "x"}
    assert score_entry_seniority(entry, "M2A") == 0.0


def test_score_entry_seniority_publication_first_author_match():
    entry = {
        "extracted_fields": {
            "authors": "Jane Public, Jones B, Smith J",
            "target_name": "Jane Public",
        },
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == SENIOR_AUTHOR_BONUS


def test_score_entry_seniority_publication_last_author_match():
    entry = {
        "extracted_fields": {
            "authors": "Smith J, Jones B, Public Jane",
            "target_name": "Jane Public",
        },
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == SENIOR_AUTHOR_BONUS


def test_score_entry_seniority_publication_no_author_match():
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": "Jane Public"},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == 0.0


def test_score_entry_seniority_ignores_non_grant_non_pub_codes():
    """A code that is neither a grant (M2*) nor S1 never earns a bonus, even
    with a PI-shaped role field."""
    entry = {"extracted_fields": {"pi_role": "Principal Investigator"}, "text": "x"}
    assert score_entry_seniority(entry, "H") == 0.0


def test_score_entry_seniority_whitespace_only_target_name_degrades_to_no_bonus():
    """A noisy extracted_fields.target_name must cost the entry its seniority
    bonus, not crash prioritize_entries / run_stage_4_5 (#850)."""
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": "   "},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") < SENIOR_AUTHOR_BONUS


def test_score_entry_seniority_none_target_name_no_bonus():
    """target_name explicitly None must not reach the split() path at all --
    the ``if authors and target_name`` guard short-circuits first (#850)."""
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": None},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == 0.0


def test_score_entry_seniority_empty_string_target_name_no_bonus():
    """target_name == "" is the ordinary falsy case the guard already
    handled before #850; kept as a baseline alongside None/whitespace (#850)."""
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": ""},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == 0.0


def test_score_entry_seniority_single_word_target_name_matches_first_author():
    """A plain single-token target_name (no internal whitespace) must keep
    matching after the #850 fix -- guards against a regression that only
    handles the multi-word split case."""
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": "Smith"},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == SENIOR_AUTHOR_BONUS


def test_score_entry_seniority_padded_multiword_target_name_matches_first_author():
    """Leading/trailing whitespace around an otherwise valid multi-word
    target_name must still resolve to its first token and match (#850)."""
    entry = {
        "extracted_fields": {"authors": "Smith J, Jones B", "target_name": "  Smith Jones "},
        "text": "x",
    }
    assert score_entry_seniority(entry, "S1") == SENIOR_AUTHOR_BONUS


# --- prioritize_entries --------------------------------------------------------

def test_prioritize_entries_orders_by_combined_score_and_applies_limit():
    entries = [
        {"text": "Old co-I grant", "extracted_fields": {"pi_role": "Co-Investigator", "year": "2005"}},
        {"text": "New PI grant with substantive descriptive narrative text " * 5,
         "extracted_fields": {"pi_role": "PI", "year": "2023"}},
        {"text": "Mid grant", "extracted_fields": {"pi_role": "", "year": "2015"}},
    ]

    # M2B, not M2A: M2A is current by definition, which would flatten recency.
    result = prioritize_entries(entries, "M2B", limit=2, current_year=2026)

    assert len(result) == 2
    assert result[0]["text"].startswith("New PI grant")
    assert result[1]["text"] == "Mid grant"
    assert not any(e["text"] == "Old co-I grant" for e in result)


def test_prioritize_entries_empty_list_returns_empty():
    assert prioritize_entries([], "M2A", current_year=2026) == []


@pytest.mark.parametrize("call", [
    lambda: prioritize_entries([{"text": "x", "extracted_fields": {}}], "M2A"),
    lambda: gather_context_entries({"M2A": [{"text": "x", "extracted_fields": {}}]}),
    lambda: is_current_entry({"text": "x", "extracted_fields": {}}, "M2A"),
])
def test_current_year_is_a_required_argument(call):
    """current_year must be required, not left to a per-function
    default -- a caller that omits it is a bug, not a silently-resolved wall
    clock. Guards against a regression that reintroduces `= None` here.
    build_context_string is not in this list: it takes no current_year
    parameter at all (it never resolved a year itself, so
    the earlier "signature parity" parameter was unused)."""
    with pytest.raises(TypeError):
        call()


def test_prioritize_entries_uses_taxonomy_default_limit_when_unspecified():
    """H's ENTRY_LIMITS cap is 10; feeding exactly 10 entries with no
    explicit ``limit`` must return all 10 (proves the taxonomy-keyed default
    is actually consulted, not some other constant)."""
    entries = [{"text": str(i), "extracted_fields": {"year": str(2000 + i)}} for i in range(10)]
    result = prioritize_entries(entries, "H", current_year=2026)
    assert len(result) == 10


def test_prioritize_entries_default_limit_for_unlisted_code():
    """A code absent from ENTRY_LIMITS falls back to ENTRY_LIMITS['default']
    == 5, not to "no limit"."""
    entries = [{"text": str(i), "extracted_fields": {}} for i in range(8)]
    result = prioritize_entries(entries, "Q1", current_year=2026)
    assert len(result) == 5


def test_prioritize_entries_ongoing_entry_outranks_older_senior_entry():
    """#946 item 6: an ongoing Co-I grant must beat a PI grant that ended a
    decade ago. Under the old 0.3 weight on start-year-only recency the PI
    bonus won; recency now leads."""
    entries = [
        {"text": "Old PI grant", "extracted_fields": {"pi_role": "PI", "start_date": "2010", "end_date": "2014"}},
        {"text": "Ongoing co-I grant", "extracted_fields": {"role": "Co-Investigator",
                                                             "start_date": "2012", "end_date": "present"}},
    ]
    result = prioritize_entries(entries, "M2B", limit=1, current_year=2026)
    assert [e["text"] for e in result] == ["Ongoing co-I grant"]


def test_prioritize_entries_ranks_by_latest_year_not_start_year():
    """A 2004-2024 range is recent work; the old code read only the first
    date field it found (start_date 2004) and ranked it below a 2016 entry."""
    entries = [
        {"text": "Single 2016", "extracted_fields": {"start_date": "2016"}},
        {"text": "Long range", "extracted_fields": {"start_date": "2004", "end_date": "2024"}},
    ]
    result = prioritize_entries(entries, "O", limit=1, current_year=2026)
    assert [e["text"] for e in result] == ["Long range"]


def test_prioritize_entries_recency_weight_beats_content_length():
    """Pins RECENCY_WEIGHT's size in the within-section cut. A 2012 entry
    (recency 0.3 at 2026) with no text beats an undated 500-char entry
    (content 0.1) only when the weight is above 1/3: 0.3 x 0.4 = 0.12 > 0.1,
    but the old 0.3 weight gave 0.09 < 0.1."""
    entries = [
        {"text": "u" * 500, "extracted_fields": {}},
        {"text": "", "extracted_fields": {"year": "2012"}},
    ]
    result = prioritize_entries(entries, "O", limit=1, current_year=2026)
    assert result[0]["extracted_fields"] == {"year": "2012"}


# #947: current_year is now required on prioritize_entries / gather_context_entries /
# is_current_entry -- there is no more per-function wall-clock default to test.
# build_context_string takes no current_year at all: it never
# resolved a year itself. Only run_stage_4_5 still resolves the wall clock, and
# only once, at the top of the run; that is covered by
# test_run_stage_4_5_resolves_current_year_once below, near the other
# run_stage_4_5 end-to-end tests.


# --- recency helpers --------------------------------------------------------------

def test_latest_entry_year_takes_max_over_date_fields():
    """Structured DATE_FIELDS are trusted (unlike free text): still the max."""
    entry = {"extracted_fields": {"start_date": "2004", "end_date": "2019"}, "text": "text 2030"}
    assert latest_entry_year(entry, current_year=2026) == 2019


def test_latest_entry_year_leading_range_reads_the_end_year_not_the_start():
    """a 2004-2024 range is recent work -- the leading range's
    END year is the entry's own date, not its start. The old code read only
    the first 4-digit number in the text (2021), which is the range's start."""
    entry = {"extracted_fields": {"narrative": "x"}, "text": "Project A, 2021-2023. Aims 2022."}
    assert latest_entry_year(entry, current_year=2026) == 2023


def test_latest_entry_year_ignores_a_later_aside_past_the_leading_range():
    """the max over every year mentioned in free text let a
    later aside (a renewal year, "replicated in 2023") inflate an old entry.
    Unlike the leading range's own end, a later separate mention -- even one
    naming a bigger year -- is not the entry's own date and must be ignored."""
    entry = {"extracted_fields": {"narrative": "x"}, "text": "Project A, 2010-2012, replicated in 2023."}
    assert latest_entry_year(entry, current_year=2026) == 2012


def test_latest_entry_year_text_fallback_ignores_years_after_current_year():
    """#947: a typo or forward-looking projection in the text must not count
    -- 2030 is dropped and the first remaining year (2019) is used."""
    entry = {"extracted_fields": {}, "text": "2019 draft; projected renewal in 2030."}
    assert latest_entry_year(entry, current_year=2026) == 2019


def test_latest_entry_year_text_fallback_all_years_after_current_year_is_none():
    entry = {"extracted_fields": {}, "text": "Projected extension in 2030."}
    assert latest_entry_year(entry, current_year=2026) is None


def test_latest_entry_year_reads_date_field_and_twentieth_century_years():
    assert latest_entry_year({"extracted_fields": {"date": "2019"}, "text": "2030"}, current_year=2026) == 2019
    assert latest_entry_year({"extracted_fields": {"year": "1998"}, "text": "x"}, current_year=2026) == 1998


@pytest.mark.parametrize("text, expected", [
    ("Award 12019 and 2010", 2010),
    ("Award 20195 and 2010", 2010),
    ("Project 2022-2028", 2026),
    ("Project 2022-2028, pilot 2015", 2026),
    ("Project 2030-2032, pilot 2015", 2015),
])
def test_latest_entry_year_ignores_digits_inside_longer_numbers(text, expected):
    """A grant or ID number that contains a year-like run is not a year.
    a leading range straddling current_year (start <=
    current_year < end) is an entry still in progress -- it is capped at
    current_year, not skipped in favour of a later incidental year
    ("pilot 2015" must not win over the still-open 2022-2028 range)."""
    assert latest_entry_year({"extracted_fields": {}, "text": text}, current_year=2026) == expected


def test_latest_entry_year_none_when_no_year_anywhere():
    assert latest_entry_year({"extracted_fields": {}, "text": "no dates"}, current_year=2026) is None


@pytest.mark.parametrize("code, fields, text, expected", [
    ("M2A", {}, "active grant", True),                       # current by definition
    ("N3A", {}, "current mentee", True),                     # current by definition
    ("M2B", {"end_date": "Present"}, "x", True),             # ongoing end_date
    ("K1", {"end_date": "ongoing"}, "x", True),
    ("M1", {"narrative": "x"}, "Project 2025-present: aims", True),   # open range in text
    ("M1", {"narrative": "x"}, "Project 2025 \u2013 Current", True),
    ("M2B", {"end_date": "2022"}, "x", False),
    ("M2B", {"end_date": "2019, not current"}, "x", False),  # a trailing word is not an ongoing end_date
    ("M1", {}, "Presented findings in 2019", False),         # 'present' inside a word is not a range
    ("M1", {"narrative": "x"}, "Studies airway immunology", True),    # undated M1 = present research statement
    ("M1", {"narrative": "x"}, "Studied airway immunology, 2010-2015", False),  # dated, closed M1
    ("K1", {}, "Undated teaching", False),                   # undated is current only for M1
    ("S1", {"year": "2024"}, "A paper on the present state", False),  # bare 'present' is not a range
])
def test_is_current_entry(code, fields, text, expected):
    assert is_current_entry({"extracted_fields": fields, "text": text}, code, current_year=2026) is expected


@pytest.mark.parametrize("code, fields, text, expected", [
    ("M2A", {"start_date": "2021-07-01", "end_date": "2024-06-30"}, "ended grant", False),  # end_date passed
    ("M2A", {"start_date": "2022", "end_date": "2026"}, "ends this year", True),              # boundary: not yet past
    ("M2A", {"start_date": "2013"}, "start date only", True),     # no end_date: no evidence it ended
    ("N3A", {"end_date": "2020"}, "mentee who finished", False),
    ("K1", {"end_date": "Now"}, "x", True),                       # 'now' is an ongoing end_date
    ("K1", {"end_date": "Presentation 2019"}, "x", False),       # ongoing word must end at a boundary
    # is_current_entry's CURRENT_TAXONOMY_CODES branch returned
    # `not ended_before(...)` before ONGOING_PATTERN was ever checked, so an
    # open "YYYY - Present" end_date on M2A/N3A named a year and read as ended.
    ("M2A", {"end_date": "2024 - Present"}, "x", True),
    ("N3A", {"end_date": "2025-Present"}, "x", True),
])
def test_is_current_entry_current_by_definition_codes_honour_end_date(code, fields, text, expected):
    """Verifier finding on #946 item 6: every M2A counted as current, so a
    grant that ended in 2024 still got the tag and recency 1.0."""
    assert is_current_entry({"extracted_fields": fields, "text": text}, code, current_year=2026) is expected


def _frozen_datetime(year):
    """A stand-in for stage_4_5.datetime whose now() is 1 January of `year`,
    so the wall-clock default is testable."""
    class _Frozen:
        @staticmethod
        def now():
            from datetime import datetime as real
            return real(year, 1, 1)
    return _Frozen


@pytest.mark.parametrize("end_date, expected", [
    ("Present", True),
    ("present", True),
    ("Ongoing", True),
    ("ongoing", True),
    ("current", True),
    ("now", True),
    ("2025-Present", True),
    ("2024 - Present", True),      # #947: a spaced dash range must match too
    ("to present", True),
    ("Currently Working", True),   # 8 occurrences in the local stage-4 farm
    ("currently working", True),
    ("2022", False),
    ("2019, not current", False),  # a trailing mention is not an ongoing end_date
    ("Presentation 2019", False),  # the ongoing word must end at a boundary
    # trailing text after the open word needs the end anchor
    # ($) to reject -- without it, "present" alone (preceded by the dash)
    # would already satisfy the pattern regardless of what follows.
    ("2019 - present, renewed 2024", False),
])
def test_ongoing_end_date_shapes(end_date, expected):
    """document exactly which end_date strings count as
    ongoing. Uses K1 (neither current-by-definition nor M1) to isolate
    ONGOING_PATTERN from the other is_current_entry branches."""
    entry = {"extracted_fields": {"end_date": end_date}, "text": "x"}
    assert is_current_entry(entry, "K1", current_year=2026) is expected


def test_ongoing_pattern_matches_spaced_dash_range_directly():
    """Pins the regex fix itself, independent of is_current_entry's branching."""
    assert ONGOING_PATTERN.search("2024 - Present")
    assert not ONGOING_PATTERN.search("2019, not current")
    assert ONGOING_PATTERN.search("Currently Working")
    assert not ONGOING_PATTERN.search("2019 - present, renewed 2024")


def test_score_entry_recency_current_entry_scores_one():
    entry = {"extracted_fields": {"start_date": "1995", "end_date": "present"}, "text": "x"}
    assert score_entry_recency(entry, "O", current_year=2026) == 1.0


def test_score_entry_recency_decays_linearly_and_clamps():
    def rec(year):
        return score_entry_recency({"extracted_fields": {"year": str(year)}, "text": "x"}, "S1", 2026)
    assert rec(2026) == 1.0
    assert rec(2016) == pytest.approx(0.5)
    assert rec(2006) == 0.0
    assert rec(1990) == 0.0          # clamped at 0, never negative
    assert rec(2030) == 1.0          # future-dated (in press) clamps at 1


def test_score_entry_recency_judges_end_date_against_its_own_year(monkeypatch):
    """The ended-grant check must use the caller's current_year, not the
    wall clock (frozen at 2020 here): at 2040 a grant that ended in 2030
    scores by decay, although at the wall-clock year it would be current."""
    monkeypatch.setattr(stage_4_5, "datetime", _frozen_datetime(2020))
    grant = {"extracted_fields": {"end_date": "2030"}, "text": "x"}
    assert score_entry_recency(grant, "M2A", current_year=2040) == pytest.approx(0.5)


def test_score_entry_recency_undated_scores_zero():
    assert score_entry_recency({"extracted_fields": {}, "text": "x"}, "S1", 2026) == 0.0


def test_open_range_in_text_only_makes_m1_current_not_other_codes():
    """OPEN_RANGE_PATTERN ran for every code, so a publication
    abstract mentioning "2005-present" was wrongly tagged current and scored
    1.0. Only M1 (whose project lines carry their dates in the text) checks
    it; the same text on S1 must not."""
    entry = {"extracted_fields": {"year": "2005"}, "text": "Ongoing since 2005-present in this field."}
    assert is_current_entry(entry, "M1", current_year=2026) is True
    assert is_current_entry(entry, "S1", current_year=2026) is False
    assert score_entry_recency(entry, "S1", 2026) != 1.0


def test_compute_entry_recency_agrees_with_is_current_entry_and_score_entry_recency():
    """latest_entry_year was computed twice per entry (once
    inside is_current_entry's undated-M1 branch, again inside
    score_entry_recency's decay calc). compute_entry_recency computes it
    once and its fields must match what the two separate functions return."""
    entry = {"extracted_fields": {"narrative": "x"}, "text": "Studies airway immunology"}  # undated M1
    recency = compute_entry_recency(entry, "M1", 2026)
    assert recency.is_current is True
    assert recency.latest_year is None
    assert recency.score == 1.0
    assert recency.is_current == is_current_entry(entry, "M1", current_year=2026)
    assert recency.score == score_entry_recency(entry, "M1", 2026)

    dated = {"extracted_fields": {"year": "2016"}, "text": "x"}
    dated_recency = compute_entry_recency(dated, "S1", 2026)
    assert dated_recency.latest_year == 2016
    assert dated_recency.is_current is False
    assert dated_recency.score == pytest.approx(0.5)


def test_compute_entry_recency_dated_closed_m1_entry_is_not_current():
    """the undated-M1 test above never exercises compute_entry_recency's
    M1 wire when the entry IS dated. A mutant changing compute_entry_recency's
    `is_current_entry(entry, taxonomy_code, current_year, latest_year=year)` call to
    pass `latest_year=None` instead makes is_current_entry recompute nothing, see
    a None year, and wrongly return True (its "undated M1" branch) for every M1
    entry, dated or not -- with the module suite still green, since no test pinned
    a dated M1 entry through compute_entry_recency/score_entry_recency."""
    entry = {"extracted_fields": {}, "text": "Studied X, 2010-2015"}
    recency = compute_entry_recency(entry, "M1", 2026)
    assert recency.is_current is False
    assert recency.score < 1.0
    assert score_entry_recency(entry, "M1", 2026) < 1.0


# --- gather_context_entries -----------------------------------------------------

def test_gather_context_entries_skips_low_value_sections_and_keeps_boundary():
    """B1/T are explicitly weighted <= -0.85 and an unrecognized code
    defaults to -0.9 -- all three must be dropped. S2 sits at exactly -0.8,
    which is NOT <= -0.85, so it must survive: this pins the boundary, not
    just "low stuff is skipped"."""
    entries_by_code = {
        "M1": [{"text": "research", "extracted_fields": {}}],
        "B1": [{"text": "edu", "extracted_fields": {}}],
        "T": [{"text": "appendix", "extracted_fields": {}}],
        "UNKNOWNCODE": [{"text": "x", "extracted_fields": {}}],
        "S2": [{"text": "review", "extracted_fields": {}}],
    }

    result = gather_context_entries(entries_by_code, current_year=2026)

    codes = [code for code, _entry, _weight, _recency in result]
    assert codes == ["M1", "S2"]


def test_gather_context_entries_adds_seniority_bonus_to_weight():
    """A PI grant's weight in the returned tuple must be base_weight (M2B:
    -0.25) + PI_BONUS, not the bare base weight. The grant is undated, so its
    recency bonus is 0 and the sum isolates the seniority term."""
    entries_by_code = {
        "M2B": [{"text": "R01 study", "extracted_fields": {"pi_role": "Principal Investigator"}}],
    }
    result = gather_context_entries(entries_by_code, current_year=2026)
    assert len(result) == 1
    _code, _entry, weight, _recency = result[0]
    assert weight == stage_4_5.SECTION_WEIGHTS["M2B"] + PI_BONUS


def test_gather_context_entries_adds_recency_bonus_to_weight():
    """An ongoing entry's weight is base + RECENCY_WEIGHT (x1.0)."""
    entries_by_code = {"O": [{"text": "Chair", "extracted_fields": {"start_date": "2020", "end_date": "present"}}]}
    (_code, _entry, weight, _recency), = gather_context_entries(entries_by_code, current_year=2026)
    assert weight == pytest.approx(stage_4_5.SECTION_WEIGHTS["O"] + RECENCY_WEIGHT)


def test_gather_context_entries_passes_current_year_to_section_selection():
    """current_year must reach prioritize_entries' per-section cut, not only
    the final weight. At current_year=2045 all six 2020-2025 entries are past
    the 20-year window (recency 0), so text length decides and the shortest
    (2025) entry is cut; at the wall-clock year it would be the 2020 one."""
    entries = [{"text": "x" * (100 - 10 * i), "extracted_fields": {"year": str(2020 + i)}} for i in range(6)]
    result = gather_context_entries({"O": entries}, current_year=2045)
    years = sorted(e["extracted_fields"]["year"] for _c, e, _w, _r in result)
    assert years == ["2020", "2021", "2022", "2023", "2024"]


def test_gather_context_entries_current_project_leads_old_first_author_paper():
    """#946 item 6 shape: a 2016 first-author paper (S1 + SENIOR_AUTHOR_BONUS)
    used to sort ahead of the owner's current research project (M1, dated only
    in its text). The current project must now come first, and a recent
    paper must beat the old one."""
    old_paper = {"text": "Old paper", "extracted_fields": {
        "title": "T", "authors": "Jane Doe, Roe R", "target_name": "Jane Doe", "year": "2016"}}
    recent_paper = {"text": "Recent paper", "extracted_fields": {
        "title": "T", "authors": "Roe R, Poe P, Moe M", "target_name": "Jane Doe", "year": "2024"}}
    project = {"text": "Current project 2025-present: aims", "extracted_fields": {"narrative": "x"}}
    result = gather_context_entries({"S1": [old_paper, recent_paper], "M1": [project]},
                                    cv_owner_name="Jane Doe", current_year=2026)
    assert [e["text"] for _c, e, _w, _r in result] == ["Current project 2025-present: aims", "Recent paper", "Old paper"]


def test_gather_context_entries_weight_and_tag_use_given_current_year_not_2026():
    """a mutant hardcoding current_year=2026 inside gather's own
    recency computation (rather than threading through the current_year
    parameter) left the suite green, because every other direct
    gather_context_entries test in this file also happens to use 2026. A
    grant ending in 2035 is current-by-definition at 2026 (score 1.0, weight
    0.4) but has passed at 2040 (score 0.75, weight 0.3) -- the two years
    must give different weight and recency.is_current."""
    entries_by_code = {"M2A": [{"text": "Grant", "extracted_fields": {"end_date": "2035"}}]}
    (_code, _entry, weight, recency), = gather_context_entries(entries_by_code, current_year=2040)
    assert recency.is_current is False
    assert weight == pytest.approx(0.3)


def test_gather_and_build_compute_recency_exactly_once_per_entry(monkeypatch):
    """recency was computed up to 3x per entry -- once inside
    prioritize_entries' own scoring loop, again inside gather_context_entries'
    final-weight loop, and a third time inside build_context_string's
    [CURRENT] tag check (which, for an M1 entry, recomputes latest_entry_year
    a second time even within a single is_current_entry call). Wraps both
    compute_entry_recency (catches prioritize/gather re-scoring an entry
    gather already scored) and latest_entry_year (catches build_context_string
    bypassing the passed-in recency and calling is_current_entry itself,
    which for an undated M1 entry recomputes latest_entry_year) to prove the
    full gather -> build pipeline does each exactly once per entry. M1 is
    included because only its undated branch re-enters latest_entry_year
    from inside is_current_entry when not given an already-computed year."""
    compute_calls = []
    real_compute = stage_4_5.compute_entry_recency

    def counting_compute(entry, taxonomy_code, current_year):
        compute_calls.append(id(entry))
        return real_compute(entry, taxonomy_code, current_year)

    year_calls = []
    real_latest_year = stage_4_5.latest_entry_year

    def counting_latest_year(entry, current_year):
        year_calls.append(id(entry))
        return real_latest_year(entry, current_year)

    monkeypatch.setattr(stage_4_5, "compute_entry_recency", counting_compute)
    monkeypatch.setattr(stage_4_5, "latest_entry_year", counting_latest_year)

    entries_by_code = {
        "M2A": [{"text": "Grant", "extracted_fields": {"title": "R01", "end_date": "2020"}}],
        "S1": [{"text": "Paper", "extracted_fields": {"title": "T", "year": "2018"}}],
        "M1": [{"text": "Undated research narrative", "extracted_fields": {}}],
    }
    weighted = gather_context_entries(entries_by_code, current_year=2026)
    build_context_string(weighted)

    assert len(compute_calls) == 3
    assert len(set(compute_calls)) == 3  # one call per distinct entry, not per entry per call site
    assert len(year_calls) == 3
    assert len(set(year_calls)) == 3


# --- is_valid_entry -------------------------------------------------------------

def test_is_valid_entry_rejects_grant_without_title():
    entry = {"extracted_fields": {"title": ""}, "text": "has text"}
    assert is_valid_entry("M2A", entry) is False


def test_is_valid_entry_rejects_grant_with_literal_none_title():
    entry = {"extracted_fields": {"title": "None"}, "text": "has text"}
    assert is_valid_entry("M2A", entry) is False


def test_is_valid_entry_rejects_publication_without_title():
    entry = {"extracted_fields": {"title": ""}, "text": "has text"}
    assert is_valid_entry("S1", entry) is False


def test_is_valid_entry_rejects_entry_with_no_text():
    entry = {"extracted_fields": {}, "text": "   "}
    assert is_valid_entry("H", entry) is False


def test_is_valid_entry_accepts_grant_with_title_and_text():
    entry = {"extracted_fields": {"title": "Study of X"}, "text": "has text"}
    assert is_valid_entry("M2A", entry) is True


def test_is_valid_entry_accepts_generic_entry_with_text():
    entry = {"extracted_fields": {}, "text": "Award"}
    assert is_valid_entry("H", entry) is True


# --- format_entry_for_context ----------------------------------------------------

def test_format_entry_for_context_publication():
    entry = {"extracted_fields": {"authors": "A, B", "title": "T", "journal": "J", "year": "2020"}, "text": ""}
    assert format_entry_for_context("S1", entry) == "[PUB] A, B. T. J. 2020"


def test_format_entry_for_context_grant():
    entry = {"extracted_fields": {"title": "Grant T", "pi_role": "PI", "agency": "NIH"}, "text": ""}
    assert format_entry_for_context("M2A", entry) == "[GRANT-M2A] Grant T | Role: PI | Agency: NIH"


def test_format_entry_for_context_research_activities():
    entry = {"extracted_fields": {}, "text": "research text"}
    assert format_entry_for_context("M1", entry) == "[RESEARCH] research text"


def test_format_entry_for_context_honor_with_year():
    entry = {"extracted_fields": {"award_name": "Best Award", "year": "2019"}, "text": ""}
    assert format_entry_for_context("H", entry) == "[HONOR] Best Award (2019)"


def test_format_entry_for_context_honor_without_year_omits_parens():
    entry = {"extracted_fields": {"award_name": "Best Award"}, "text": ""}
    assert format_entry_for_context("H", entry) == "[HONOR] Best Award"


def test_format_entry_for_context_bibliometric():
    entry = {"extracted_fields": {}, "text": "h-index 20"}
    assert format_entry_for_context("S0", entry) == "[METRICS] h-index 20"


def test_format_entry_for_context_generic_fallback_uses_bare_code():
    entry = {"extracted_fields": {}, "text": "External leadership text"}
    assert format_entry_for_context("Q1", entry) == "[Q1] External leadership text"


# --- build_context_string --------------------------------------------------------

def test_build_context_string_filters_invalid_entries():
    """An entry that fails is_valid_entry (blank text) contributes nothing,
    even though it is well-formed enough to reach build_context_string."""
    entry = ("H", {"extracted_fields": {}, "text": ""}, -0.1, _NOT_CURRENT)
    assert build_context_string([entry], max_tokens=100) == ""


def test_build_context_string_drops_entry_that_exceeds_the_char_budget():
    """max_tokens=3 -> max_chars=12; the formatted entry is 14 chars, so it
    must not be included and the result is empty (not truncated text)."""
    entry = ("X", {"extracted_fields": {}, "text": "A" * 10}, -0.1, _NOT_CURRENT)
    assert build_context_string([entry], max_tokens=3) == ""


def test_build_context_string_stops_before_the_entry_that_would_overflow():
    """max_tokens=7 -> max_chars=28. Each formatted entry is 14 chars. After
    the first entry, the correct running total is 14 + 1 (the "+1 for
    newline" accounting on line 297) = 15; checking the second entry then
    computes 15 + 14 = 29 > 28, so it is dropped and only the first entry
    appears. If the "+1" were ever dropped, the running total would stay at
    14, so 14 + 14 = 28 is NOT > 28 and the second entry would wrongly be
    admitted -- this is the exact boundary that distinguishes the two."""
    e1 = ("X", {"extracted_fields": {}, "text": "A" * 10}, -0.1, _NOT_CURRENT)
    e2 = ("X", {"extracted_fields": {}, "text": "B" * 10}, -0.2, _NOT_CURRENT)
    result = build_context_string([e1, e2], max_tokens=7)
    assert result == "[X] " + "A" * 10


def test_build_context_string_admits_entry_that_exactly_fills_the_budget():
    """max_tokens=4 -> max_chars=16, and the single formatted entry is
    exactly 16 chars ("[X] " + 12 A's). The budget check on line 293 is
    ``total_chars + len(formatted) > max_chars: break`` -- with an EXACT
    fill, 0 + 16 > 16 is False, so the entry must be admitted. A ``>=``
    off-by-one there would wrongly break on this exact-fill boundary and
    return an empty string instead."""
    entry = ("X", {"extracted_fields": {}, "text": "A" * 12}, -0.1, _NOT_CURRENT)
    result = build_context_string([entry], max_tokens=4)
    assert result == "[X] " + "A" * 12


def test_build_context_string_includes_every_entry_that_fits():
    e1 = ("X", {"extracted_fields": {}, "text": "A" * 10}, -0.1, _NOT_CURRENT)
    e2 = ("X", {"extracted_fields": {}, "text": "B" * 10}, -0.2, _NOT_CURRENT)
    result = build_context_string([e1, e2], max_tokens=8)
    assert result == "[X] " + "A" * 10 + "\n[X] " + "B" * 10


def test_build_context_string_tags_current_entries():
    current_entry = {"extracted_fields": {"end_date": "present"}, "text": "Teaching"}
    past_entry = {"extracted_fields": {"end_date": "2019"}, "text": "Old teaching"}
    current = ("K1", current_entry, -0.1, compute_entry_recency(current_entry, "K1", 2026))
    past = ("K1", past_entry, -0.2, compute_entry_recency(past_entry, "K1", 2026))
    result = build_context_string([current, past], max_tokens=100)
    assert result == f"{CURRENT_CONTEXT_TAG} [K1] Teaching\n[K1] Old teaching"


def test_build_context_string_tag_follows_recency_computed_at_a_year_other_than_2026():
    """the [CURRENT] tag must come from each entry's own
    already-computed EntryRecency (gather's 4th tuple element), exercised at
    current_year=2040 so a regression that only happens to tag correctly at
    2026 (every other direct build_context_string test's year) cannot hide."""
    current_grant = {"extracted_fields": {"title": "R01", "end_date": "2045"}, "text": "x"}
    past_grant = {"extracted_fields": {"title": "R21", "end_date": "2035"}, "text": "y"}
    current_recency = compute_entry_recency(current_grant, "M2A", 2040)
    past_recency = compute_entry_recency(past_grant, "M2A", 2040)
    assert current_recency.is_current is True
    assert past_recency.is_current is False

    result = build_context_string(
        [("M2A", current_grant, 0.0, current_recency), ("M2A", past_grant, 0.0, past_recency)],
        max_tokens=100)

    assert result == (f"{CURRENT_CONTEXT_TAG} [GRANT-M2A] R01 | Role:  | Agency: "
                       "\n[GRANT-M2A] R21 | Role:  | Agency: ")


# --- score_existing_m1 (call_llm stubbed) -----------------------------------------

def test_score_existing_m1_parses_valid_json_response(monkeypatch):
    def fake_call_llm(**_kwargs):
        return {
            "content": json.dumps({"score": 0.85, "reasoning": "Great narrative"}),
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            "cache_read_tokens": 1, "cache_write_tokens": 2, "cost": 0.01,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, usage = stage_4_5.score_existing_m1("some content")

    assert score == 0.85
    assert reasoning == "Great narrative"
    assert usage["cost"] == 0.01
    assert usage["cache_read_tokens"] == 1


def test_score_existing_m1_strips_markdown_code_fence(monkeypatch):
    fenced = "```json\n" + json.dumps({"score": 0.55, "reasoning": "ok"}) + "\n```"

    def fake_call_llm(**_kwargs):
        return {
            "content": fenced,
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, _usage = stage_4_5.score_existing_m1("content")

    assert score == 0.55
    assert reasoning == "ok"


def test_score_existing_m1_regex_fallback_when_not_valid_json(monkeypatch):
    """Content that is not parseable JSON at all (no fences either) but
    still contains a "score": <n> substring must be recovered via the
    regex fallback, with a reasoning string distinct from the JSON path's."""
    def fake_call_llm(**_kwargs):
        return {
            "content": 'Sure, here you go "score": 0.42 based on the content.',
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, _usage = stage_4_5.score_existing_m1("content")

    assert score == 0.42
    assert reasoning == "Score extracted from response"


def test_score_existing_m1_returns_zero_when_totally_unparseable(monkeypatch):
    def fake_call_llm(**_kwargs):
        return {
            "content": "no json and no score field anywhere in this text",
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    score, reasoning, _usage = stage_4_5.score_existing_m1("content")

    assert score == 0.0
    assert reasoning == "Failed to parse response"


# --- generate_research_summary (call_llm stubbed) ---------------------------------

def test_generate_research_summary_returns_stripped_text_and_usage(monkeypatch):
    def fake_call_llm(**_kwargs):
        return {
            "content": "  Generated summary text about research.  ",
            "prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50, "cost": 0.02,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    text, usage = stage_4_5.generate_research_summary("some context", "Jane Doe")

    assert text == "Generated summary text about research."
    assert usage["prompt_tokens"] == 20
    assert usage["cost"] == 0.02


def test_generate_research_summary_prompt_requires_current_work_first_and_neutral_reference(monkeypatch):
    """#946 item 6: the generation prompt must (a) tell the model to open with
    current work and keep older work brief, and (b) forbid gendered pronouns
    inferred from the owner's name."""
    captured = {}

    def fake_call_llm(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return {"content": "x", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    stage_4_5.generate_research_summary("[CURRENT] [RESEARCH] ctx", "Jane Doe")

    prompt = captured["prompt"]
    assert f"- {CURRENT_WORK_REQUIREMENT}" in prompt
    assert f"- {NEUTRAL_REFERENCE_REQUIREMENT}" in prompt
    assert "Open with the researcher's CURRENT research" in CURRENT_WORK_REQUIREMENT
    assert "older work only briefly" in CURRENT_WORK_REQUIREMENT
    assert CURRENT_CONTEXT_TAG in CURRENT_WORK_REQUIREMENT
    assert "never use gendered pronouns" in NEUTRAL_REFERENCE_REQUIREMENT
    assert "never infer gender from the name" in NEUTRAL_REFERENCE_REQUIREMENT
    assert f"ongoing entries tagged {CURRENT_CONTEXT_TAG}" in prompt


def test_current_context_tag_appears_in_current_work_requirement():
    """CURRENT_WORK_REQUIREMENT and CURRENT_CONTEXT_TAG live in
    two different places in the module now (prompt text next to the
    generation prompt, the tag itself next to the recency constants); this
    pins that they can't drift apart -- no call_llm stub needed."""
    assert CURRENT_CONTEXT_TAG in CURRENT_WORK_REQUIREMENT


# --- generate_summary_unless_withheld (#1224; call_llm stubbed) ---------------------

_REPLY_USAGE = {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16, "cost": 0.003}


def _stub_reply(monkeypatch, content):
    """call_llm returns `content`; the list records each call so tests can count them."""
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return {"content": content, **_REPLY_USAGE}
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)
    return calls


@pytest.mark.parametrize("blank_context", ["", "   ", "\n\t \n"])
def test_blank_context_makes_no_llm_call_and_returns_empty_summary(monkeypatch, blank_context):
    """The defect: a blank context still went to the model, which answered with a
    refusal that stage 6 then rendered as the Research Activities paragraph."""
    calls = _stub_reply(monkeypatch, "I don't have access to specific CV details for Jane Doe.")

    text, method, usage = stage_4_5.generate_summary_unless_withheld(blank_context, "Jane Doe")

    assert calls == []
    assert (text, method, usage) == ("", stage_4_5.GENERATION_METHOD_SKIPPED_EMPTY_CONTEXT, {})


def test_non_blank_context_returns_the_generated_summary(monkeypatch):
    calls = _stub_reply(monkeypatch, "  Doe's lab studies widget dynamics.  ")

    text, method, usage = stage_4_5.generate_summary_unless_withheld("[GRANT-M2A] R01 Widgets", "Jane Doe")

    assert len(calls) == 1
    assert text == "Doe's lab studies widget dynamics."
    assert method == stage_4_5.GENERATION_METHOD_LLM
    assert usage["cost"] == 0.003


@pytest.mark.parametrize("reply", [
    "I don't have access to specific CV details for Jane Doe. Please provide the CV.",
    "I do not have enough information to write this summary.",
    "I cannot write a summary without the CV context.",
    "I can't produce a research summary from an empty context.",
    "I'm unable to summarize research that was not provided.",
    "I am unable to generate this paragraph.",
    "I am not able to see any CV content.",
    "I apologize, but the CV context appears to be empty.",
    "I apologise, but the context section is blank.",
    "I'm sorry, but no CV content was provided.",
    "I’m sorry, but no CV content was provided.",       # curly apostrophe
    "I don’t have access to specific CV details.",
    "  i DON'T have the details needed.",                   # leading space, any case
])
def test_refusal_opening_reply_is_withheld_but_its_cost_is_kept(monkeypatch, reply):
    """A refusal can also come back for a non-blank context (a policy refusal, a
    context the model reads as empty). It is withheld the same way, and the spend
    is still returned so the stage's cost total stays honest."""
    _stub_reply(monkeypatch, reply)

    text, method, usage = stage_4_5.generate_summary_unless_withheld("[GRANT-M2A] R01 Widgets", "Jane Doe")

    assert text == ""
    assert method == stage_4_5.GENERATION_METHOD_REFUSED
    assert usage["cost"] == 0.003


@pytest.mark.parametrize("reply", [
    "Doe's research program studies widget dynamics, and the work cannot be done without imaging.",
    "Jane Doe does not use animal models; her lab develops computational methods.",
    "Immunology research led by Dr. Doe focuses on widget signalling.",   # opens with "I" but is no refusal
    "Investigations by Doe's group, including ones I'm told were pioneering, ...",
    "This work could not have proceeded without R01 support.",
    'Doe studies why patients with asthma say "I cannot breathe" during exacerbations.',  # refusal words mid-text
])
def test_real_summary_is_never_mistaken_for_a_refusal(monkeypatch, reply):
    """The pattern is anchored to the reply's opening first-person phrase: a summary
    that merely contains 'cannot', 'does not' or an I-initial word is kept."""
    _stub_reply(monkeypatch, reply)

    text, method, _usage = stage_4_5.generate_summary_unless_withheld("[GRANT-M2A] R01 Widgets", "Jane Doe")

    assert text == reply
    assert method == stage_4_5.GENERATION_METHOD_LLM


# --- run_stage_4_5 end-to-end (call_llm stubbed) ----------------------------------

def _write_fields_json(tmp_path, document_uid, entries, cv_owner=None):
    data = {"document_uid": document_uid, "entries": entries}
    if cv_owner is not None:
        data["cv_owner"] = cv_owner
    path = tmp_path / "fields.json"
    path.write_text(json.dumps(data))
    return path


def test_run_stage_4_5_keeps_high_scoring_existing_m1(monkeypatch, tmp_path):
    """M1 score >= 0.8 -> the existing text is used verbatim, no generation
    call happens, and original_m1_content stays None (only the regenerate
    branch populates it)."""
    def fake_call_llm(*, messages, **_kwargs):
        if _SCORE_PROMPT_MARKER in messages[0]["content"]:
            return {
                "content": json.dumps({"score": 0.9, "reasoning": "cohesive narrative"}),
                "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cost": 0.001,
            }
        raise AssertionError("generation must not be attempted when M1 is kept")
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    existing_text = "Existing high quality narrative about cancer research funded by NIH."
    inp = _write_fields_json(
        tmp_path, "TEST01",
        [{"taxonomy_code": "M1", "text": existing_text, "extracted_fields": {}}],
        cv_owner={"first_name": "Jane", "last_name": "Doe"},
    )
    outp = tmp_path / "out.json"

    result_path = run_stage_4_5(str(inp), str(outp), verbose=True)

    out = json.loads(Path(result_path).read_text())
    assert out["research_summary"]["generation_method"] == "existing_content"
    assert out["research_summary"]["text"] == existing_text
    assert out["research_summary"]["m1_score"] == 0.9
    assert out["original_m1_content"] is None
    assert out["context_used"] == {"entry_count": 0, "top_codes": []}


def test_run_stage_4_5_regenerates_low_scoring_m1(monkeypatch, tmp_path):
    """M1 score below 0.8 -> falls through to context gathering + LLM
    generation; the output text must be the GENERATED text (not the
    existing M1 text -- that would mean the fallback/keep route leaked
    through), and original_m1_content must record the low score as the
    reason it was replaced."""
    def fake_call_llm(*, messages, **_kwargs):
        if _SCORE_PROMPT_MARKER in messages[0]["content"]:
            return {
                "content": json.dumps({"score": 0.3, "reasoning": "too keyword-y"}),
                "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cost": 0.001,
            }
        return {
            "content": "Newly generated narrative summary.",
            "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": 0.005,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "TEST02",
        [
            {"taxonomy_code": "M1", "text": "Cancer. Genomics.", "extracted_fields": {}},
            {"taxonomy_code": "S1", "text": "A paper",
             "extracted_fields": {"authors": "Doe J, Smith A", "title": "A Paper",
                                   "journal": "Nature", "year": "2020", "target_name": "Jane Doe"}},
            {"taxonomy_code": "M2A", "text": "Grant text",
             "extracted_fields": {"title": "R01 Study", "pi_role": "PI", "agency": "NIH", "year": "2022"}},
        ],
        cv_owner={"first_name": "Jane", "last_name": "Doe"},
    )
    outp = tmp_path / "out.json"

    result_path = run_stage_4_5(str(inp), str(outp), verbose=True)

    out = json.loads(Path(result_path).read_text())
    assert out["research_summary"]["generation_method"] == "llm_generated"
    assert out["research_summary"]["text"] == "Newly generated narrative summary."
    assert out["research_summary"]["m1_score"] == 0.3
    assert out["original_m1_content"]["combined_text"] == "Cancer. Genomics."
    assert out["original_m1_content"]["reason_not_used"] == (
        "Score 0.30 below threshold 0.8 - too keyword-y"
    )
    # Context gathering actually ran across all three taxonomy codes.
    assert set(out["context_used"]["top_codes"]) == {"M1", "S1", "M2A"}


def test_run_stage_4_5_skips_scoring_when_no_existing_m1(monkeypatch, tmp_path):
    """No M1 entries at all -> existing_m1_content is empty, so
    score_existing_m1 must never be invoked (a call carrying the scoring prompt
    would fail the assertion below); generation proceeds directly."""
    def fake_call_llm(*, messages, **_kwargs):
        if _SCORE_PROMPT_MARKER in messages[0]["content"]:
            raise AssertionError("scoring must not run when there is no existing M1 content")
        return {
            "content": "Generated from context only.",
            "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": 0.005,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "TEST03",
        [{"taxonomy_code": "S1", "text": "A paper",
          "extracted_fields": {"authors": "Doe J", "title": "A Paper", "journal": "Nature", "year": "2020"}}],
    )
    outp = tmp_path / "out.json"

    result_path = run_stage_4_5(str(inp), str(outp), verbose=False)

    out = json.loads(Path(result_path).read_text())
    assert out["research_summary"]["generation_method"] == "llm_generated"
    assert out["research_summary"]["text"] == "Generated from context only."
    assert out["research_summary"]["m1_score"] == 0.0
    assert out["original_m1_content"] is None


def test_run_stage_4_5_derives_owner_name_from_document_uid(monkeypatch, tmp_path):
    """With no cv_owner block, the owner name is derived from the second
    underscore-delimited segment of document_uid: the literal substrings
    "js" and "Cv" are stripped (line 456's ``.replace('js', '').replace('Cv',
    '')``), then the result is capitalized, and threaded into the
    generation prompt.

    "JanejsCv" contains "Jane" as a substring even WITHOUT stripping (it
    capitalizes to "Janejscv"), so merely checking ``"Jane" in prompt``
    passes on both the correct and the buggy (unstripped) behaviour and
    proves nothing. Instead assert the exact rendered clause "for Jane."
    (name immediately followed by the sentence's period) is present, and
    that the unstripped form "Janejscv" is absent -- only the intended
    strip-then-capitalize route produces exactly "Jane"."""
    captured = {}

    def fake_call_llm(*, messages, **_kwargs):
        if _SCORE_PROMPT_MARKER in messages[0]["content"]:
            raise AssertionError("no M1 content -- scoring must not run")
        captured["prompt"] = messages[0]["content"]
        return {
            "content": "Summary text.",
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
        }
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "2015_JanejsCv_extra",
        [{"taxonomy_code": "S1", "text": "A paper",
          "extracted_fields": {"authors": "Doe J", "title": "A Paper", "journal": "Nature", "year": "2020"}}],
    )
    outp = tmp_path / "out.json"

    run_stage_4_5(str(inp), str(outp), verbose=False)

    assert "for Jane." in captured["prompt"]
    assert "Janejscv" not in captured["prompt"]


def test_run_stage_4_5_raises_file_not_found_for_missing_input(monkeypatch, tmp_path):
    def fake_call_llm(**_kwargs):
        raise AssertionError("call_llm must not be reached for a missing input file")
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    missing_output = tmp_path / "should_not_be_created.json"

    try:
        run_stage_4_5("__no_such_input_704_w3__", str(missing_output), verbose=False)
        raised = False
    except FileNotFoundError as exc:
        raised = True
        assert "__no_such_input_704_w3__" in str(exc)

    assert raised is True
    assert not missing_output.exists()


def test_run_stage_4_5_resolves_current_year_once_from_wall_clock(monkeypatch, tmp_path):
    """current_year must be required and resolved once at the
    top of the run, not left to each function's own default. Frozen at 2030,
    a grant that ended in 2028 is not current -- proving resolve_current_year
    actually ran and its result (not the real wall-clock year) reached
    is_current_entry via gather/build. end_date 2028 is chosen so the two
    candidate years disagree: at the real wall clock the grant would still
    be current (not ended), at the frozen 2030 it is not -- an end_date
    before both years (e.g. 2025) cannot tell them apart."""
    monkeypatch.setattr(stage_4_5, "datetime", _frozen_datetime(2030))
    captured = {}

    def fake_call_llm(*, messages, **_kwargs):
        if _SCORE_PROMPT_MARKER in messages[0]["content"]:
            raise AssertionError("no M1 content -- scoring must not run")
        captured["prompt"] = messages[0]["content"]
        return {"content": "Summary.", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "TEST05",
        [{"taxonomy_code": "M2A", "text": "Grant", "extracted_fields": {"title": "R01", "end_date": "2028"}}],
        cv_owner={"first_name": "Jane", "last_name": "Doe"},
    )
    outp = tmp_path / "out.json"

    run_stage_4_5(str(inp), str(outp), verbose=False)

    # CURRENT_WORK_REQUIREMENT's own boilerplate always mentions the tag;
    # check the CV CONTEXT entry itself is not tagged.
    assert f"{CURRENT_CONTEXT_TAG} [GRANT-M2A]" not in captured["prompt"]
    assert "[GRANT-M2A]" in captured["prompt"]


def test_run_stage_4_5_calls_send_no_call_site_max_tokens(monkeypatch, tmp_path):
    """Neither the scoring nor the generation call caps its output: a tight cap
    cut the summary off mid-sentence (Sonnet 4.6 once, Sonnet 5 on 9 of 13
    calls, 2026-09-29), and the Bedrock client's 16K floor already bounds a
    runaway."""
    seen = []

    def fake_call_llm(*, messages, **kwargs):
        seen.append(kwargs)
        if _SCORE_PROMPT_MARKER in messages[0]["content"]:
            return {"content": json.dumps({"score": 0.3, "reasoning": "r"}),
                    "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0}
        return {"content": "Generated.", "prompt_tokens": 1, "completion_tokens": 1,
                "total_tokens": 2, "cost": 0.0}
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)

    inp = _write_fields_json(
        tmp_path, "TEST07",
        [{"taxonomy_code": "M1", "text": "Cancer. Genomics.", "extracted_fields": {}}],
        cv_owner={"first_name": "Jane", "last_name": "Doe"},
    )
    run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=False)

    assert len(seen) == 2
    assert all("max_tokens" not in kw for kw in seen)


# Entries whose section weight is at or below the -0.85 cut, so none reaches the context.
_LOW_VALUE_ENTRIES = [
    {"taxonomy_code": "T", "text": "A whole synthetic CV that collapsed into one catch-all entry " * 20,
     "extracted_fields": {}},
    {"taxonomy_code": "A", "text": "Name: Jane Doe", "extracted_fields": {}},
    {"taxonomy_code": "D1", "text": "Assistant Professor, Example University, 2015-present", "extracted_fields": {}},
]


def _no_llm_call_allowed(monkeypatch):
    def fake_call_llm(**_kwargs):
        raise AssertionError("stage 4.5 must not call the LLM when no context reaches it (#1224)")
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)


def test_run_stage_4_5_skips_the_llm_when_no_entry_reaches_the_context(monkeypatch, tmp_path):
    """#1224 (MYAXRH): a CV whose entries are all low-weight codes gives an empty
    context. The run records that, spends nothing, and writes an empty summary
    rather than the model's refusal."""
    _no_llm_call_allowed(monkeypatch)
    inp = _write_fields_json(tmp_path, "TEST08", _LOW_VALUE_ENTRIES,
                             cv_owner={"first_name": "Jane", "last_name": "Doe"})

    out = json.loads(Path(run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=True)).read_text())

    summary = out["research_summary"]
    assert summary["generation_method"] == stage_4_5.GENERATION_METHOD_SKIPPED_EMPTY_CONTEXT
    assert summary["text"] == ""
    assert (summary["word_count"], summary["char_count"]) == (0, 0)
    assert out["context_used"] == {"entry_count": 0, "top_codes": []}
    assert out["total_cost"] == 0.0 and out["total_tokens"] == 0


def test_run_stage_4_5_blank_context_from_blank_valid_entries_also_skips(monkeypatch, tmp_path):
    """Weighted entries can exist yet build_context_string filters every one (no
    text, or a grant with no title). The context is just as empty, so the same skip."""
    _no_llm_call_allowed(monkeypatch)
    entries = [{"taxonomy_code": "M2A", "text": "Grant", "extracted_fields": {"title": "None"}},
               {"taxonomy_code": "H", "text": "", "extracted_fields": {}}]
    inp = _write_fields_json(tmp_path, "TEST09", entries)

    out = json.loads(Path(run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=False)).read_text())

    assert out["context_used"]["entry_count"] == 2   # weighted entries exist ...
    assert out["research_summary"]["generation_method"] == stage_4_5.GENERATION_METHOD_SKIPPED_EMPTY_CONTEXT
    assert out["research_summary"]["text"] == ""     # ... but nothing usable reached the prompt


def test_run_stage_4_5_withholds_a_refusal_reply_and_still_counts_its_cost(monkeypatch, tmp_path):
    def fake_call_llm(*, messages, **_kwargs):
        assert _SCORE_PROMPT_MARKER not in messages[0]["content"]   # no M1, so no scoring call
        return {"content": "I'm unable to write this summary from the context given.",
                "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": 0.005}
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)
    inp = _write_fields_json(
        tmp_path, "TEST10",
        [{"taxonomy_code": "M2A", "text": "Grant", "extracted_fields": {"title": "R01 Study", "agency": "NIH"}}])

    out = json.loads(Path(run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=False)).read_text())

    assert out["research_summary"]["generation_method"] == stage_4_5.GENERATION_METHOD_REFUSED
    assert out["research_summary"]["text"] == ""
    assert out["total_cost"] == 0.005 and out["total_tokens"] == 30


def test_stage_6_renders_nothing_for_the_skipped_summary_run_stage_4_5_writes(monkeypatch, tmp_path):
    """The wire: the real stage-4.5 output for an empty context is the document
    stage 6 reads, and stage 6 reports that it rendered no summary paragraph."""
    from unified_pipeline.stage_6_word_template import WCMTemplateGenerator

    _no_llm_call_allowed(monkeypatch)
    inp = _write_fields_json(tmp_path, "TEST11", _LOW_VALUE_ENTRIES)
    out_path = run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=False)

    gen = WCMTemplateGenerator(verbose=False)
    assert gen._fill_research_summary(json.loads(Path(out_path).read_text())) is False
    assert gen.stats["entries_inserted"] == 0


def test_run_stage_4_5_records_each_call_the_fallback_served(monkeypatch, tmp_path):
    """#1174: both calls (the M1 relevance score and the generation) are
    served by the fallback here; the artifact lists them, by call name."""
    from unified_pipeline.llm_provenance import FALLBACK_SERVED_KEY, STAGE4_5_FALLBACK_CALLS_KEY

    def fake_call_llm(*, messages, **_kwargs):
        reply = {"content": '{"score": 0.1, "reasoning": "weak"}'
                 if _SCORE_PROMPT_MARKER in messages[0]["content"]
                 else "A synthetic research summary paragraph.",
                 "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0,
                 FALLBACK_SERVED_KEY: "example.fallback-model-1"}
        return reply
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)
    inp = _write_fields_json(tmp_path, "TEST12", [
        {"taxonomy_code": "M1", "text": "Keywords only"},
        {"taxonomy_code": "M2A", "text": "Grant",
         "extracted_fields": {"title": "R01 Study", "agency": "NIH"}}])

    out = json.loads(Path(run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=False)).read_text())

    assert out[STAGE4_5_FALLBACK_CALLS_KEY] == [
        {"call": "m1_relevance_score", "model": "example.fallback-model-1"},
        {"call": "summary_generation", "model": "example.fallback-model-1"}]


def test_run_stage_4_5_writes_no_fallback_record_when_none_was_served(monkeypatch, tmp_path):
    from unified_pipeline.llm_provenance import STAGE4_5_FALLBACK_CALLS_KEY

    monkeypatch.setattr(stage_4_5, "call_llm", lambda **_kw: {
        "content": "A synthetic research summary paragraph.",
        "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0})
    inp = _write_fields_json(tmp_path, "TEST13", [
        {"taxonomy_code": "M2A", "text": "Grant",
         "extracted_fields": {"title": "R01 Study", "agency": "NIH"}}])

    out = json.loads(Path(run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=False)).read_text())

    assert STAGE4_5_FALLBACK_CALLS_KEY not in out


# --- #1174: a failed call never fails the stage ---------------------------------

_SUMMARY_TEXT = "A synthetic research summary paragraph about widget dynamics and their regulation."
_M1_AND_GRANT = [
    {"taxonomy_code": "M1", "text": "Keywords only. Widgets.", "extracted_fields": {}},
    {"taxonomy_code": "M2A", "text": "Grant", "extracted_fields": {"title": "R01 Study", "agency": "NIH"}}]


def _access_denied() -> ClientError:
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Converse")


def _stub_calls(monkeypatch, *, score=None, generation=None):
    """call_llm by prompt: `score`/`generation` is the reply string, or the
    exception call_llm raises once the client's retries and fallback are spent.
    A call with no plan fails the test."""
    def fake_call_llm(*, messages, **_kwargs):
        outcome = score if _SCORE_PROMPT_MARKER in messages[0]["content"] else generation
        assert outcome is not None, "unexpected stage-4.5 call"
        if isinstance(outcome, BaseException):
            raise outcome
        return {"content": outcome, **_REPLY_USAGE}
    monkeypatch.setattr(stage_4_5, "call_llm", fake_call_llm)


def _run(tmp_path, entries=_M1_AND_GRANT):
    inp = _write_fields_json(tmp_path, "TEST20", entries, cv_owner={"first_name": "Jane", "last_name": "Doe"})
    return json.loads(Path(run_stage_4_5(str(inp), str(tmp_path / "out.json"), verbose=True)).read_text())


def test_score_call_failing_on_every_model_still_generates_the_summary(monkeypatch, tmp_path):
    """The QFQLNF failure: the M1 relevance call is content-filtered on every
    model the client tries. The M1 text is treated as unscored and the summary
    is generated, exactly as for a low score; the failure is recorded, not
    swallowed."""
    _stub_calls(monkeypatch, score=BedrockContentFilteredError("filtered"), generation=_SUMMARY_TEXT)

    out = _run(tmp_path)

    summary = out["research_summary"]
    assert (summary["text"], summary["generation_method"]) == (_SUMMARY_TEXT, stage_4_5.GENERATION_METHOD_LLM)
    assert (summary["m1_score"], summary["score_reasoning"]) == (0.0, stage_4_5.M1_UNSCORED_REASONING)
    assert out["original_m1_content"]["total_entries"] == 1
    assert out[STAGE4_5_CALL_FAILURES_KEY] == [
        {"call": "m1_relevance_score", "exception_type": "BedrockContentFilteredError",
         "stop_reason": "content_filtered", "message": "filtered"}]
    assert STAGE4_5_FALLBACK_CALLS_KEY not in out


def test_generation_call_failing_on_every_model_writes_an_empty_summary(monkeypatch, tmp_path):
    """The failed call's billed attempts stay in the stage's cost."""
    _stub_calls(monkeypatch, score='{"score": 0.1, "reasoning": "keywords"}',
                generation=BedrockEmptyResponseError("no text", stop_reason="content_filtered", cost=0.002))

    out = _run(tmp_path)

    summary = out["research_summary"]
    assert (summary["text"], summary["generation_method"]) == ("", stage_4_5.GENERATION_METHOD_LLM_CALL_FAILED)
    assert summary["m1_score"] == 0.1
    assert out["original_m1_content"] is None   # nothing replaced the M1 text
    assert out[STAGE4_5_CALL_FAILURES_KEY] == [
        {"call": "summary_generation", "exception_type": "BedrockEmptyResponseError",
         "stop_reason": "content_filtered", "message": "no text"}]
    assert out["total_cost"] == pytest.approx(0.003 + 0.002)   # the score call + the failed call


def test_both_calls_failing_records_both_and_keeps_the_m1_text_out(monkeypatch, tmp_path):
    _stub_calls(monkeypatch, score=_access_denied(), generation=_access_denied())

    out = _run(tmp_path)

    assert out["research_summary"]["generation_method"] == stage_4_5.GENERATION_METHOD_LLM_CALL_FAILED
    assert [(f["call"], f["exception_type"], f["stop_reason"]) for f in out[STAGE4_5_CALL_FAILURES_KEY]] == [
        ("m1_relevance_score", "ClientError", None), ("summary_generation", "ClientError", None)]
    assert out["total_cost"] == 0.0


def test_a_successful_run_writes_no_failure_record(monkeypatch, tmp_path):
    _stub_calls(monkeypatch, score='{"score": 0.1, "reasoning": "keywords"}', generation=_SUMMARY_TEXT)

    assert STAGE4_5_CALL_FAILURES_KEY not in _run(tmp_path)


def test_stage_6_renders_nothing_for_a_failed_generation_call(monkeypatch, tmp_path):
    from unified_pipeline.stage_6_word_template import WCMTemplateGenerator

    _stub_calls(monkeypatch, generation=_access_denied())
    out = _run(tmp_path, entries=_M1_AND_GRANT[1:])

    gen = WCMTemplateGenerator(verbose=False)
    assert gen._fill_research_summary(out) is False


def test_a_provider_outage_still_propagates(monkeypatch, tmp_path):
    """#810: an outage is the driver's call, as in every other stage."""
    _stub_calls(monkeypatch, generation=LLMOutageError("down", seconds_waited=900.0))

    with pytest.raises(LLMOutageError):
        _run(tmp_path, entries=_M1_AND_GRANT[1:])
