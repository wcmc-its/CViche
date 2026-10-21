"""core.text_norm: the public helpers."""

from unified_pipeline.core import text_norm


def test_norm_folds_marks_whitespace_and_case():
    assert text_norm.norm("Zoë  Brändström\t") == "zoe brandstrom"
    assert text_norm.norm(None) == ""


def test_norm_folds_latin_greek_and_cyrillic_accents_only():
    """#1916: a mark on a letter of another script is part of the letter."""
    assert text_norm.norm("Müller") == text_norm.norm("Muller") == "muller"
    assert text_norm.norm("ἄλφα") == "αλφα"  # Greek tonos and breathing
    assert text_norm.norm("Йо") == "ио"  # Cyrillic breve
    assert text_norm.norm("がか") != text_norm.norm("かか")  # Japanese dakuten
    assert text_norm.norm("が") == "が"  # decomposed, then recomposed
    assert text_norm.norm("क्ष") == "क्ष"  # Devanagari virama
    assert text_norm.norm("שָׁלוֹם") == "שָׁלוֹם"  # Hebrew points
    # Compatibility forms fold on purpose; a spacing accent's mark is dropped.
    assert text_norm.norm("ﬁne ™ Ａ") == "fine tm a"
    assert text_norm.norm("Rohe\u00b4") == "rohe "


def test_text_helpers_read_none_as_empty_and_keep_falsy_values():
    """#1916: `str(text or "")` turned 0 and False into ""."""
    for helper in (text_norm.norm, text_norm.fold_quotes, text_norm.squash):
        assert helper(None) == ""
        assert helper("") == ""
        assert helper(0) == "0"
    assert text_norm.norm(False) == text_norm.squash(False) == "false"
    assert text_norm.fold_quotes(False) == "False"
    assert text_norm.norm(2016) == "2016"


def test_squash_drops_all_whitespace():
    assert text_norm.squash(" A b\tC\n") == "abc"


def test_looks_like_record_needs_length_and_a_delimiter():
    assert text_norm.looks_like_record("x" * 57 + " | y")  # 61 chars
    assert not text_norm.looks_like_record("x" * 56 + " | y")  # 60 chars
    assert text_norm.looks_like_record("x" * 60 + "\ty")
    assert not text_norm.looks_like_record("x" * 70)
    assert not text_norm.looks_like_record("a | b")
    assert not text_norm.looks_like_record("  " + "x" * 56 + " | y  ")  # 60 stripped
    assert text_norm.RECORD_ROW_MIN_CHARS == 60
    # A prefilter: a long delimited line of prose passes, a short record does not.
    assert text_norm.looks_like_record("We met every week to read papers | and we wrote notes on all of them")
    assert not text_norm.looks_like_record("Chair\t2010-2012")


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


def test_date_range_matches_each_documented_format_whole():
    """#1916: "5/2017-10/2020" matched as "2017-10", a fragment."""
    for text in ("2009-2011", "1996-8", "5/2017-10/2020", "2012-Jun 2015", "2013-present",
                 "2013 \u2013 Present", "07/01/2012-05/31/2014", "07/2019-02/21", "2010-to date"):
        assert [m.group() for m in text_norm._DATE_RANGE_RE.finditer(f"Chair {text}, Board")] == [text]
    for text in ("2064-71", "pp. 52(10):2064-71", "1998", "2010/2012", "5/2017"):
        assert text_norm._DATE_RANGE_RE.search(text) is None, text


def testedge_date_range_prefers_the_range_at_the_start():
    part = "2001-2003 Chair of the Quokka Board, then member 2004-2006"
    assert text_norm.edge_date_range(part) == "2001-2003"
    assert text_norm.edge_date_range("Chair of the Quokka Board, member 2004-2006") == "2004-2006"
    assert text_norm.edge_date_range("A sentence that holds 2001-2003 in its middle part") is None


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


def test_tab_record_segments_keeps_a_list_item_with_its_own_parenthesis_apart():
    """#1916: a part with a stray ")" ("b) ...") joined a closed part before it."""
    assert text_norm._join_wrapped_parts(
        ["a) Chair of the Wombat Panel 2006-2012", "b) Vice Chair of the Other Panel 2012-2015"]
    ) == ["a) Chair of the Wombat Panel 2006-2012", "b) Vice Chair of the Other Panel 2012-2015"]
    assert text_norm.tab_record_segments(
        "a) Chair of the Wombat Panel 2006-2012\tb) Vice Chair of the Other Panel 2012-2015"
    ) == ["a) Chair of the Wombat Panel 2006-2012", "b) Vice Chair of the Other Panel 2012-2015"]
    # An open parenthesis still takes every part up to the one that closes it.
    assert text_norm._join_wrapped_parts(["Chair (Elected by", "the", "members) 2006-2012", "x"]) == [
        "Chair (Elected by the members) 2006-2012", "x"]


def test_tab_record_segments_counts_month_year_ranges_whole():
    # Same years, different months: two ranges, so two records. The old
    # "2017-10" fragment read both as one.
    assert text_norm.tab_record_segments(
        "President of the Quokka Society 5/2017-10/2020\tPresident of the Wombat Society 6/2017-10/2020"
    ) == ["President of the Quokka Society 5/2017-10/2020", "President of the Wombat Society 6/2017-10/2020"]


def test_tab_record_segments_keeps_duplicate_parts_in_order():
    line = ("Member of the Quokka Board 2001-2003\tChair of the Wombat Panel 2004-2006\t"
            "Member of the Quokka Board 2001-2003")
    assert text_norm.tab_record_segments(line) == [
        "Member of the Quokka Board 2001-2003", "Chair of the Wombat Panel 2004-2006",
        "Member of the Quokka Board 2001-2003"]


def test_tab_record_segments_reads_words_of_any_script():
    """#1916: the word test was ASCII-only."""
    assert text_norm.tab_record_segments(
        "Председатель комитета 2009-2011\tЧлен совета общества 2012-2015"
    ) == ["Председатель комитета 2009-2011", "Член совета общества 2012-2015"]
    # Words anywhere in the part count, not only next to the range: a record
    # that reads "title, place | dates" is still a record.
    assert text_norm.tab_record_segments(
        "Attending Physician | 07/2002 – 06/2003\tAttending Physician & Director | 06/2003 – 12/2006"
    ) == ["Attending Physician | 07/2002 – 06/2003", "Attending Physician & Director | 06/2003 – 12/2006"]


def test_tab_record_segments_takes_a_range_at_the_start_or_end_of_a_part_only():
    # A range in the middle of a part is a sentence or a wrapped line, not a record's date.
    assert text_norm.tab_record_segments(
        "Marsupial Defences and (medical 2010-2012: 4\tInfection undergraduate) hours 2013-2015: 1 hour Tutorial"
    ) == []
    assert text_norm.tab_record_segments(
        "Interim Director of the Wombat Laboratory 2016 -2018(June)\t2012-2015 Director of the Quokka Laboratory"
    ) == ["Interim Director of the Wombat Laboratory 2016 -2018(June)", "2012-2015 Director of the Quokka Laboratory"]
    # A second part with its range in the middle is a sentence, not a record.
    assert text_norm.tab_record_segments(
        "Chair of the Wombat Panel 2006-2012\tServed on the quokka committee 2010-2012 as chair of its work group"
    ) == []


# #1243: a society line whose posts are mostly dated by one year (BFSUMA 98's
# shape): two of its six parts hold a range, so the half-dated share failed.
_ONE_YEAR_POSTS = ("Quokka College of Pharmacy, 1979-present\tFounding Member, 1979\tFellow, 1985\t"
                   "Research Affairs Committee, 1985\tChairperson, Scientific Session, 1985 Annual Meeting\t"
                   "Chairperson, Awards Committee, 1987-1988")


def test_tab_record_segments_reads_parts_dated_by_one_year_beside_two_ranges():
    assert text_norm.tab_record_segments(_ONE_YEAR_POSTS) == [
        "Quokka College of Pharmacy, 1979-present", "Founding Member, 1979",
        "Research Affairs Committee, 1985", "Chairperson, Awards Committee, 1987-1988"]
    # A year at the start dates a part too; a year in the middle does not.
    assert text_norm.tab_record_segments(
        "2005-present Founding President of the Wombat Forum\t2009 Conference Director, Wombat Congress\t"
        "Held the yearly 2011 meeting of the Wombat Congress in Norvale\t2016-present Wombat Fellowship director"
    ) == ["2005-present Founding President of the Wombat Forum", "2009 Conference Director, Wombat Congress",
          "2016-present Wombat Fellowship director"]


def test_tab_record_segments_still_needs_two_ranges_besides_one_year_parts():
    # One-year parts help a line reach its share; they are not its two ranges.
    assert text_norm.tab_record_segments(
        "Founding Member of the Quokka Society, 1979\tResearch Affairs Committee, 1985\t"
        "Chair of the Wombat Panel 2006-2012") == []


def test_tab_record_segments_does_not_date_details_after_a_label_by_their_years():
    # A part ending in a colon labels the parts after it: details of one record.
    assert text_norm.tab_record_segments(_ONE_YEAR_POSTS + "\tCommittee service:") == []
    assert text_norm.tab_record_segments(_ONE_YEAR_POSTS.replace("\tFellow, 1985", "\tFellow, 1985:")) == []


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


def test_is_placeholder_title_reads_the_source_by_whole_phrase():
    """#1916: the source vouches for a bracketed title as written, or for its
    words without the brackets as a whole phrase."""
    assert text_norm.is_placeholder_title("[AUC]", "An auction of quokka art.")
    assert text_norm.is_placeholder_title("[AUC]", "Bauc quokka art.")
    assert not text_norm.is_placeholder_title("[AUC]", "28.[AUC]")
    assert not text_norm.is_placeholder_title("[AUC]", "Quill A. AUC. J Imag Stud.")
    assert not text_norm.is_placeholder_title("[Erratum]", "Quill A. Erratum: wombats. J Imag Stud.")
    assert not text_norm.is_placeholder_title("[Ürn]", "Quill A. Urn. J Imag Stud.")
    assert text_norm.is_placeholder_title("[]", "Quill A. J Imag Stud.")
