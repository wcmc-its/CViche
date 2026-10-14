"""core.text_norm: the public helpers."""

from unified_pipeline.core import text_norm


def test_norm_folds_marks_whitespace_and_case():
    assert text_norm.norm("Zoë  Brändström\t") == "zoe brandstrom"
    assert text_norm.norm(None) == ""


def test_squash_drops_all_whitespace():
    assert text_norm.squash(" A b\tC\n") == "abc"


def test_looks_like_record_needs_length_and_a_delimiter():
    assert text_norm.looks_like_record("x" * 57 + " | y")  # 61 chars
    assert not text_norm.looks_like_record("x" * 56 + " | y")  # 60 chars
    assert text_norm.looks_like_record("x" * 60 + "\ty")
    assert not text_norm.looks_like_record("x" * 70)
    assert not text_norm.looks_like_record("a | b")
    assert not text_norm.looks_like_record("  " + "x" * 56 + " | y  ")  # 60 stripped


def test_tab_record_segments_returns_each_dated_part_of_a_tab_fused_line():
    # #1243: stage 2 fused three posts of one society with tabs.
    line = ("Executive Committee of the Quokka Society 2013-present\t"
            "President 5/2017-10/2020\tPresident Elect 5/2015-5/2017")
    assert text_norm.tab_record_segments(line) == [
        "Executive Committee of the Quokka Society 2013-present",
        "President 5/2017-10/2020",
        "President Elect 5/2015-5/2017",
    ]
    # A month before the end year, and a range closed by a worded end.
    assert text_norm.tab_record_segments(
        "Chair of the Wombat Panel 2006-2012\tVice Chair of the Other Panel 2012-Jun 2015"
    ) == ["Chair of the Wombat Panel 2006-2012", "Vice Chair of the Other Panel 2012-Jun 2015"]


def test_tab_record_segments_needs_two_dated_parts_with_words():
    # One dated part is one record, whatever else the line holds.
    assert text_norm.tab_record_segments("Member of the Quokka Society 2009-2011\t2009-2011") == []
    # A bare date column is the date of a neighbouring part, not a record.
    assert text_norm.tab_record_segments(
        "2010-2013\tAdvisory Board Member of the Quokka Society\t2010-2013") == []
    # Page ranges of two citations are not date ranges.
    assert text_norm.tab_record_segments(
        "Quimby F. Synthetic Oncology 2009;52(10):2064-71\tQuimby F. Synthetic Oncology 2009;56:2088-93"
    ) == []
    assert text_norm.tab_record_segments("a line without a tab, 2001-2003, with words enough") == []


def test_tab_record_segments_reads_one_record_with_dated_details_as_one_record():
    # A description that repeats the title's years: one range, so one record.
    assert text_norm.tab_record_segments(
        "2016-2019 Health Interest Partnership, Preceptor\t2016-2019 Mentored undergraduate students") == []
    # Two dated details among mostly undated parts of one mentee's entry.
    assert text_norm.tab_record_segments(
        "Quimby Farrow (2007-- )\tDepartment of Marsupial Studies\tThesis title on quokka habits\t"
        "Awards and distinctions:\t2009 Quokka Fellowship\t2006-2007 Wombat Master's Award (value 500)\t"
        "2005-2006 Entrance Scholarship, Marsupial University") == []
    # A prose part is not a record.
    assert text_norm.tab_record_segments(
        "Chair of the Wombat Panel 2006-2012\t" + "Led a team through the response, " * 8 + "2010-2012") == []


def test_tab_record_segments_joins_a_part_wrapped_inside_a_parenthesis():
    assert text_norm.tab_record_segments(
        "1996-present Organize a seminar on child welfare. (Supported by\tAcme Laboratories, 1998 - 2001)") == []
    assert text_norm.tab_record_segments(
        "Chair of the Wombat Panel (Elected by\tthe members) 2006-2012\tVice Chair of the Other Panel 2012-2015"
    ) == ["Chair of the Wombat Panel (Elected by the members) 2006-2012", "Vice Chair of the Other Panel 2012-2015"]


def test_tab_record_segments_takes_a_range_at_the_start_or_end_of_a_part_only():
    # A range in the middle of a part is a sentence or a wrapped line, not a record's date.
    assert text_norm.tab_record_segments(
        "Host Defences and (medical 2010-2012: 4\tInfection undergraduate) hours 2013-2015: 1 hour Tutorial"
    ) == []
    assert text_norm.tab_record_segments(
        "Interim Director of the Wombat Laboratory 2016 -2018(June)\t2012-2015 Director of the Quokka Laboratory"
    ) == ["Interim Director of the Wombat Laboratory 2016 -2018(June)", "2012-2015 Director of the Quokka Laboratory"]
    # A second part with its range in the middle is a sentence, not a record.
    assert text_norm.tab_record_segments(
        "Chair of the Wombat Panel 2006-2012\tServed on the quokka committee 2010-2012 as chair of its work group"
    ) == []


def test_tab_record_segments_needs_parts_long_enough_to_carry_words():
    assert text_norm.tab_record_segments("Chair 2010-2012\tMember 2013-2015") == []


def test_fold_quotes_maps_typographic_apostrophes_and_quotes_to_ascii():
    """#1232: a source header 'LOCAL (CONT<U+2019>D)' and the hierarchy node
    'LOCAL (CONT'D)' must compare equal."""
    curly = "LOCAL (CONT\u2019D) \u2018a\u2019 \u201cb\u201d \u02bc \u2032 \u201e \u2033"
    assert text_norm.fold_quotes(curly) == "LOCAL (CONT'D) 'a' \"b\" ' ' \" \""
    assert text_norm.fold_quotes("plain 'ascii' text") == "plain 'ascii' text"
    assert text_norm.fold_quotes(None) == ""


def test_norm_leaves_typographic_apostrophes_alone():
    """The fold is a separate helper on purpose: widening `norm` would move
    every coverage and render metric built on it (#1232)."""
    assert text_norm.norm("O\u2019Neil") == "o\u2019neil"



def test_is_placeholder_title_flags_only_brackets_the_source_lacks():
    """#446: the 5d LLM wrote "[Title not provided]" where the source had no
    title; a bracketed value the CV itself carries is its own text."""
    assert text_norm.is_placeholder_title("[Title not provided]", "Quill A, Brandt B,")
    assert text_norm.is_placeholder_title(" [Title not available]. ", "Ruritania.")
    assert not text_norm.is_placeholder_title("[Erratum]", "Quill A. [Erratum]. J Imag Stud.")
    assert not text_norm.is_placeholder_title("A study [of things]", "")
    assert not text_norm.is_placeholder_title("A study of things", "")
    assert not text_norm.is_placeholder_title(None, "")
