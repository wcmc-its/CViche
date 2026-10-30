"""core.text_norm: the public helpers."""

import re

import pytest

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
        "Quokka College of Pharmacy, 1979-present", "Founding Member, 1979", "Fellow, 1985",
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
    # #1243: a part of 12 characters is a record, one of 11 is not.
    assert text_norm.tab_record_segments("Dean 2001-02\tChairman 2003-04") == ["Dean 2001-02", "Chairman 2003-04"]
    assert text_norm.tab_record_segments("Rep 2001-02\tChair 2003-04") == []


def test_tab_record_segments_reads_a_short_part_as_a_record_only_when_it_names_an_office():
    # #1243 review: a part under 20 characters that names no office is a term
    # of the role beside it (BNYLDF 209), a heading or a detail over the posts,
    # or an item of a list wrapped across parts (EHZFOH 247), not a record.
    assert text_norm.tab_record_segments(
        "2004-2010 Wombat Assembly\tStanding Alternate\tNorvale 2004-2007\tAshby 2007-2010") == []
    assert text_norm.tab_record_segments(
        "2004-2010 Wombat Assembly\tStanding Alternate\tChair 2004-2007\tSecretary 2007-2010"
    ) == ["2004-2010 Wombat Assembly", "Chair 2004-2007", "Secretary 2007-2010"]
    posts = "Secretary, Wombat Guild, 2011-2013\tTreasurer, Wombat Guild, 2014-2016"
    assert text_norm.tab_record_segments(f"Committees 2010-15\t{posts}") == posts.split("\t")
    assert text_norm.tab_record_segments(f"{posts}\tTerm ended 2015-16") == posts.split("\t")
    # A one-year list item no longer pushes the wrapped list over the share.
    wrapped = ("• Consulting\tAcme Labs & Sons (2006-2007, 2010, 2013), Bolt Works\t"
               "(2006), Corla (2007), Danto (2010-2011), Esko (2010-2011),\t"
               "Fenn (2013-2014), Gale Holdings (2014-present), Hux (2014-\t"
               "2015), Ivo (2015), Jax Advisory Board (2014-present),\tKopogranite (2015)")
    assert text_norm.tab_record_segments(wrapped) == []
    assert len(text_norm.tab_record_segments(wrapped.replace("Kopogranite (2015)", "Kopo Fellow (2015)"))) == 3
    # From 20 characters a part needs no office.
    assert text_norm.tab_record_segments("Quokka Society, 2001-2004\tNorvale Ward 2004-07") == [
        "Quokka Society, 2001-2004", "Norvale Ward 2004-07"]
    assert text_norm.tab_record_segments("Quokka Society, 2001-2004\tNorvale Wds 2004-07") == []


@pytest.mark.parametrize("office", ["Member", "Fellow", "Chairperson", "President", "Secretary", "Treasurer",
                                    "Director", "Officer", "Delegate", "Trustee", "Councilor", "Councillor",
                                    "Governor", "Editor", "Founder", "Coordinator", "Liaison", "Representative",
                                    "Dean"])
def test_tab_record_segments_reads_each_office_in_a_short_part(office):
    assert text_norm.tab_record_segments(f"Quokka Society, 2001-2004\t{office} 2004-07") == [
        "Quokka Society, 2001-2004", f"{office} 2004-07"]


def test_tab_record_segments_reads_a_short_officer_post_beside_its_society_as_a_record():
    # #1243 (HZGJFM 65, IZJADE 511, VQFSDI 37): posts of 12 to 19 characters
    # were read as details of the society's line, so the render lost them.
    assert text_norm.tab_record_segments(
        "Quokka Society of Norvale, 1998-present\tMember 1975-1978\tPresident 2011-2013"
    ) == ["Quokka Society of Norvale, 1998-present", "Member 1975-1978", "President 2011-2013"]
    assert text_norm.tab_record_segments(
        "1991-2002 Member, Board of Trustees, Wombat Clinic\t1995-97 President\tPast-President"
    ) == ["1991-2002 Member, Board of Trustees, Wombat Clinic", "1995-97 President"]


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


# --- dated_record_clauses (#1243 S3: several records on one line, no tab) ---


def test_dated_record_clauses_cuts_a_date_trailing_line_into_its_records():
    assert text_norm.dated_record_clauses(
        "Member, Quokka Society, 1995-96. Treasurer, Wombat Council, 1997-99") == [
        "Member, Quokka Society, 1995-96", "Treasurer, Wombat Council, 1997-99"]


def test_dated_record_clauses_cuts_a_date_led_line_into_its_records():
    assert text_norm.dated_record_clauses(
        "2006 Member, Quokka Society Board. 2008 Chair, Wombat Council.") == [
        "2006 Member, Quokka Society Board", "2008 Chair, Wombat Council"]


def test_dated_record_clauses_needs_two_different_dates_with_words_between():
    # One record written with one date twice, and one with a list of dates.
    assert text_norm.dated_record_clauses(
        "Member, Quokka Society, 2001. Treasurer, Quokka Society, 2001") == []
    assert text_norm.dated_record_clauses("Member, Quokka Society, 2001, 2003") == []
    assert text_norm.dated_record_clauses("Member, Quokka Society, 2001-2005") == []


@pytest.mark.parametrize("unit", [
    # a clause under DATED_CLAUSE_MIN_CHARS: a fragment of the record
    "Chair, Quokka Society, 2001. Chair 2003",
    # no title or body named: places, not records
    "Wombat Hall, Norvale, 2001. Quokka Park, Crab Hollow, 2003",
    # a clause that opens in lower case continues the sentence before it
    "Member, Quokka Society, 2001; and later member, Wombat Council, 2003",
    # fewer than half of the clauses are records
    "Member, Quokka Society, 2001. Wombat Hall, Norvale, 2003. Quokka Park, Crab Hollow, 2005",
    "Member, Quokka Society, 2001. Wombat Hall, Norvale, 2002. Chair, Wombat Council, 2003. "
    "Quokka Park, Crab Hollow, 2004. Ashby Lane, Norvale, 2005",
])
def test_dated_record_clauses_needs_two_record_clauses_that_are_half_of_all(unit):
    assert text_norm.dated_record_clauses(unit) == []


def test_dated_record_clauses_caps_a_clause_at_the_max_length():
    long_name = "Quokka " * 40  # 280 characters
    assert text_norm.dated_record_clauses(
        f"Member, {long_name}Society, 2001. Chair, Wombat Council, 2003. Chair, Norvale Board, 2005") == [
        "Chair, Wombat Council, 2003", "Chair, Norvale Board, 2005"]
    assert (text_norm.DATED_CLAUSE_MIN_CHARS, text_norm.DATED_CLAUSE_MAX_CHARS) == (15, 250)


def test_dated_record_clauses_does_not_cut_narrative_with_years():
    assert text_norm.dated_record_clauses(
        "After my fellowship at the Quokka Clinic in 1985 I joined a small group practice, and in "
        "1990 I founded the Wombat Surgical Society, which I led until 2012.") == []


def test_dated_record_clauses_reads_a_cross_reference_as_no_record():
    pointer = "2019: Quokka Society Master Series, Wombat Lecture Course Director 2013-19 see previous section"
    assert text_norm.dated_record_clauses(pointer) == []
    assert text_norm.dated_record_clauses(pointer.replace("see previous section", "Quokka Society Board")) == [
        "2019: Quokka Society Master Series, Wombat Lecture Course Director", "2013-19 Quokka Society Board"]


def test_dated_record_clauses_reads_a_parenthetical_note_as_no_record():
    assert text_norm.dated_record_clauses(
        "Wombat Academic Appeals Board – 1995 – (Appeal of a denied promotion of a Quokka Faculty "
        "Member, May 1996).") == []
    # A parenthesis inside the note closes before the note does.
    assert text_norm.dated_record_clauses(
        "Chair, Quokka Program, Wombat Graduate School, 2020-present (Norvale University and the Quokka "
        "Education Board (QEB) approved, national accreditation received Dec, 2023)") == []


def test_dated_record_clauses_reads_an_office_held_within_the_record_as_no_record():
    # #1243 review: the office in the aside, written alone, drops its board.
    assert text_norm.dated_record_clauses(
        "Advisory Board, Quokka Research Programs, 2002-2008 (Chairman, 2006-2008)") == []
    assert text_norm.dated_record_clauses(
        "Advisory Board, Quokka Research Programs, 2002-2008 (Chairman of its review panel, 2006-2008)") == []


def test_dated_record_clauses_reads_a_line_that_opens_in_a_dated_parenthesis_as_offices():
    assert text_norm.dated_record_clauses("    (Vice Chair 2006-2008) (Chair 2008-2010)") == []
    # A list number is no aside.
    assert text_norm.dated_record_clauses(
        "(9) Member, Quokka Council, 1993-1995. Chair, Wombat Board, 1995-1997") == [
        "(9) Member, Quokka Council, 1993-1995", "Chair, Wombat Board, 1995-1997"]


def test_dated_record_clauses_reads_a_year_the_record_s_names_write_as_part_of_the_name():
    talks = ("Visiting Professor: Quokka Protection Act 2010; Wombat Board Chair, Reauthorization Act 2015; "
             "How to Survive")
    assert len(text_norm.dated_record_clauses(talks)) == 2
    names = ["Quokka Protection Act 2010; Wombat Board Chair, Reauthorization Act 2015; How to Survive"]
    assert text_norm.dated_record_clauses(talks, names) == []
    # A name that writes the year beside another word leaves the date alone.
    assert len(text_norm.dated_record_clauses(talks, ["Wombat Act of 2010"])) == 2


def test_dated_record_clauses_leaves_an_undated_semicolon_list_whole():
    # Out of scope (#1243): a ';' list of journals or societies.
    journals = "Journal of Quokka Studies; Wombat Review; Annals of Marsupial Science; Norvale Medical Journal"
    assert text_norm.dated_record_clauses(journals) == []
    dated = "Journal of Quokka Studies, 2001-2005; Marsupial Science Journal, 2006-2009"
    assert text_norm.dated_record_clauses(dated) == dated.split("; ")


@pytest.mark.parametrize("unit, dates", [
    # a lone year after an event word
    ("Member, Quokka Society Annual Meeting 2020 Program Committee, 2019. Chair, Wombat Council, 2021",
     ["2019", "2021"]),
    ("Member, Class of 2010 Quokka Committee. Chair, Wombat Council 2012", ["2012"]),
    # a lone year that starts an event name away from a clause boundary
    ("Member, Quokka Committee, 2009 Western Regional Meeting planning group, 2010", ["2010"]),
    # a lone year between an all-caps acronym and a capitalised word
    ("Member, QOKA 2011 Scientific Program Committee. Chair, Wombat Council, 2013", ["2013"]),
])
def test_record_date_anchors_leave_out_a_year_inside_a_name(unit, dates):
    assert [unit[a.start:a.end] for a in text_norm.record_date_anchors(unit)] == dates


@pytest.mark.parametrize("unit, dates", [
    # each would start an event name; after a month or term, after "MM/" or
    # after a state code, or joined to the date before, it is a date
    ("Member, Quokka Board, May 2012 Western Regional Meeting", ["2012"]),
    ("Member, Quokka Board, Fall 2010 Western Regional Meeting", ["2010"]),
    ("Member, Quokka Board, 5/2012 Western Regional Meeting", ["5/2012"]),
    ("Member, Quokka Board, Crab Hollow, ZQ 1999 Wombat Annual Meeting", ["1999"]),
    ("Chair, Quokka Board; 1980 to 1982 Annual Meeting Committee", ["1980 to 1982"]),
])
def test_record_date_anchors_keep_a_year_a_date_word_or_the_date_before_dates(unit, dates):
    assert [unit[a.start:a.end] for a in text_norm.record_date_anchors(unit)] == dates


def test_noted_clause_head_is_the_record_before_a_note_naming_no_office():
    assert text_norm.noted_clause_head("Quokka Personnel Committee (elected; 2006-08") == "Quokka Personnel Committee"
    assert text_norm.noted_clause_head("Quokka Diversity Committee (founder; Chair 2006-2014") is None
    assert text_norm.noted_clause_head("Quokka Advisory Committee (2009-2012") is None  # dates alone
    assert text_norm.noted_clause_head("Quokka Advisory Committee, 2009-2012") is None


def test_join_date_anchors_joins_a_date_gap_and_leaves_out_what_counts_rejects():
    text = "Member 1990-1992, Chair March 2001 - June 2004, Fellow 2006"
    dates = [text_norm.DateAnchor(m.start(), m.end(), frozenset({m.group()}))
             for m in re.finditer(r"(?:19|20)\d{2}", text)]
    assert [text[a.start:a.end] for a in text_norm.join_date_anchors(text, dates)] == [
        "1990-1992", "2001 - June 2004", "2006"]
    no_2006 = text_norm.join_date_anchors(text, dates, lambda date, joined: date.years != {"2006"})
    assert [a.years for a in no_2006] == [frozenset({"1990", "1992"}), frozenset({"2001", "2004"})]


def test_date_clauses_leave_out_an_aside_of_the_clause_before():
    def clauses(part):
        anchors = text_norm.record_date_anchors(part)
        return [clause.text for clause in text_norm.date_clauses(part, anchors)]
    assert clauses("Member, Quokka Board, 2001 (a note on its work, 2003). Chair, Wombat Council, 2005") == [
        "Member, Quokka Board, 2001", "). Chair, Wombat Council, 2005"]
    # The note's own parenthesis closes before the note does.
    assert clauses("Member, Quokka Board, 2001 (a note (QB) on its work, 2003). Chair, Wombat Council, 2005") == [
        "Member, Quokka Board, 2001", "). Chair, Wombat Council, 2005"]
    assert clauses("Member, Quokka Board, 2001, 2003") == ["Member, Quokka Board, 2001, 2003"]


def test_has_payload_and_is_prose_read_past_date_words():
    assert not text_norm.has_payload(" - June 2004, and the present")
    assert text_norm.has_payload("2004 Quokka")
    assert text_norm.is_prose("given for steady dedication to the clinic and its patients over many years")
    assert not text_norm.is_prose("Clinical skills tutor, Quokka Medical School, ongoing")
    # As many capitalised words as lowercase ones is no sentence.
    assert not text_norm.is_prose("Quokka Wombat Norvale Ashby sits under every tree")
    assert text_norm.is_prose("Quokka Wombat Norvale sits under every tree")
    assert text_norm.PROSE_MIN_LOWERCASE_WORDS == 4


_TITLE_WORDS = ("professor", "instructor", "lecturer", "fellow", "director", "chief", "chair", "chairman",
                "chairperson", "attending", "surgeon", "physician", "resident", "member", "president", "dean",
                "head", "scientist", "consultant", "officer", "editor", "coordinator", "manager", "advisor",
                "associate", "assistant")
_BODY_WORDS = ("society", "association", "academy", "college", "federation", "council", "committee", "board",
               "foundation", "forum", "network", "consortium", "alliance", "league", "union", "club", "task force",
               "working group", "work group", "workgroup", "panel", "commission", "subcommittee", "section",
               "chapter", "caucus", "institute", "organization", "center", "centre", "program", "journal",
               "initiative", "conference", "meeting", "symposium", "congress", "course",
               "school", "university", "hospital", "department", "division")


@pytest.mark.parametrize("word", _TITLE_WORDS + _BODY_WORDS)
def test_dated_record_clauses_reads_a_title_or_a_body_as_naming_a_record(word):
    unit = f"Quokka {word.title()}, Norvale, 2001. Wombat {word.title()}, Crab Hollow, 2003"
    assert text_norm.dated_record_clauses(unit) == unit.split(". ")
    assert text_norm.dated_record_clauses(unit.replace(word.title(), "Hall")) == []


@pytest.mark.parametrize("word", ["Meeting", "Conference", "Symposium", "Congress", "Convention", "Assembly",
                                  "Summit", "Workshop", "Course"])
def test_record_date_anchors_read_a_year_after_or_before_an_event_word_as_its_name(word):
    after = f"Member, Quokka {word} 2010 Committee, 2012"
    assert [after[a.start:a.end] for a in text_norm.record_date_anchors(after)] == ["2012"]
    before = f"Member, Quokka Board, 2009 Western {word} planning group, 2011"
    assert [before[a.start:a.end] for a in text_norm.record_date_anchors(before)] == ["2011"]


@pytest.mark.parametrize("month", ["January", "Feb", "Mar.", "April", "May", "June", "Jul", "August", "Sept",
                                   "October", "Nov", "December", "Spring", "Summer", "Fall", "Autumn", "Winter"])
def test_record_date_anchors_keep_a_year_after_a_month_or_term(month):
    unit = f"Member, Quokka Board, {month} 2012 Western Regional Meeting"
    assert [unit[a.start:a.end] for a in text_norm.record_date_anchors(unit)] == ["2012"]


@pytest.mark.parametrize("pointer", ["see previous section", "see also the above", "cf. page 3", "as above",
                                     "listed below", "noted elsewhere", "see attached", "see table"])
def test_dated_record_clauses_reads_each_cross_reference_as_no_record(pointer):
    unit = f"2001 Member, Quokka Society Board. 2003 Treasurer, Wombat Council, {pointer}"
    assert text_norm.dated_record_clauses(unit) == []
    assert len(text_norm.dated_record_clauses(unit.replace(pointer, "Norvale"))) == 2


def test_a_name_names_a_year_by_the_word_before_it_or_after_it():
    talks = "Visiting Professor: Quokka Protection Act 2010; Wombat Board Chair, Reauthorization Act 2015; How to Survive"
    assert text_norm.dated_record_clauses(talks, ["Quokka Protection Act 2010", "Reauthorization Act 2015"]) == []
    posts = "Member, Wombat Board, 2008. Chair, 2010 Quokka Initiative Council, 2012"
    assert text_norm.dated_record_clauses(posts, ["2010 Quokka Initiative"]) == [
        "Member, Wombat Board, 2008", "Chair, 2010 Quokka Initiative Council, 2012"]
    assert text_norm.dated_record_clauses(posts) == ["Member, Wombat Board, 2008", "Quokka Initiative Council, 2012"]


def test_record_date_anchors_never_read_a_range_as_a_name():
    unit = "Member, Quokka Symposium 2001-2003 Planning Committee, Wombat Annual Meeting 2005"
    assert [unit[a.start:a.end] for a in text_norm.record_date_anchors(unit)] == ["2001-2003"]


def test_record_date_anchors_hold_their_years_and_keep_a_year_after_an_acronym_before_punctuation():
    unit = "Member, Board of QOKA 2011, Chair, Wombat Council, 1990-1992"
    anchors = text_norm.record_date_anchors(unit)
    assert [unit[a.start:a.end] for a in anchors] == ["2011", "1990-1992"]
    assert [a.years for a in anchors] == [frozenset({"2011"}), frozenset({"1990", "1992"})]


def test_dated_record_clauses_reads_a_prose_clause_as_no_record():
    unit = ("Member, Quokka Society Board, 2001. Chair, Wombat Council, 2003. Awarded for steady service "
            "to the clinic and its patients over many years by the Wombat Society, 2005")
    assert text_norm.dated_record_clauses(unit) == [
        "Member, Quokka Society Board, 2001", "Chair, Wombat Council, 2003"]


def test_dated_record_clauses_tells_dates_apart_as_written():
    # The same year, once alone and once opening a range, is two dates.
    assert text_norm.dated_record_clauses("Member, Quokka Society, 2013. Chair, Wombat Council, 2013-present") == [
        "Member, Quokka Society, 2013", "Chair, Wombat Council, 2013-present"]


def test_noted_clause_head_reads_date_words_as_no_note():
    assert text_norm.noted_clause_head("Quokka Advisory Committee (June 2009-present") is None


def test_dated_record_clauses_reads_an_aside_with_no_office_as_a_note():
    assert text_norm.dated_record_clauses("Advisory Board, Quokka Research Programs, 1992-1998 (Quokka Panel, 1996-1998)") == []


def test_dated_record_clauses_reads_a_line_that_is_a_sentence_as_one_record():
    # Its clauses are records alone; the words after the last date make the
    # line a sentence about them.
    unit = ("Quokka Society Board, 2001. Wombat Council Chair, 2003. after which we left the region and "
            "stopped serving on these bodies entirely")
    assert text_norm.dated_record_clauses(unit) == []
    assert len(text_norm.dated_record_clauses(unit.split(". after")[0])) == 2


# --- dated_record_clauses: #1243 review round ---


@pytest.mark.parametrize("office", ["Vice-president,1989-1990", "Chairman, 2006-2008", "Vice Chair 2012-2014",
                                    "Chair and Treasurer, 2003-2005",
                                    "Consulting Editor 2011-2013", "Vice President from 2013-2016",
                                    "President Elect (2014-2015)", "Chair-elect, 2016"])
def test_dated_record_clauses_reads_an_office_alone_as_no_record(office):
    # The office of the record before it, without its body.
    assert text_norm.dated_record_clauses(f"Member, Quokka Society Board, 2001. {office}") == []
    assert len(text_norm.dated_record_clauses("Member, Quokka Society Board, 2001. Treasurer, Wombat Council, 2003")) == 2


def test_dated_record_clauses_reads_a_clause_inside_an_earlier_parenthesis_as_an_aside():
    assert text_norm.dated_record_clauses(
        "Quokka Society (elected fellow 2004; Burrow Committee Chairman, 2011-2016; Pouch Committee "
        "2017-Present)") == []
    # A clause after the parenthesis closes is a record again.
    assert text_norm.dated_record_clauses(
        "Quokka Burrow Committee (2001-2004); Wombat Pouch Committee (2003-2006; Chair 2004-2006); "
        "Norvale Program Committee (1998-2001)") == [
        "Quokka Burrow Committee (2001-2004)", "Wombat Pouch Committee (2003-2006)",
        "Norvale Program Committee (1998-2001)"]


def test_dated_record_clauses_keeps_a_note_after_a_date_with_its_clause():
    assert text_norm.dated_record_clauses(
        "Quokka Hiring Committee, Department of Wombats; 2010- 2011 (Chair). Wombat Selection Committee, "
        "Department of Wombats; 2009.") == [
        "Quokka Hiring Committee, Department of Wombats; 2010- 2011 (Chair)",
        "Wombat Selection Committee, Department of Wombats; 2009"]
    # Dates that only such notes separate are one record's.
    assert text_norm.dated_record_clauses(
        "Organizer, Quokka Burrow Symposia, 1992 (Wombat University), 1994 (Norvale University), and "
        "2001 (Norvale University, with a co-chair)") == []


@pytest.mark.parametrize("unit", [
    "March 2011-May 2014 Associate Professor of Wombat Genetics, Quokka University. Associate of Quokka "
    "Institute (2008-2013). Associate of the Wombat Center (2009-2014).",
    "March 2011-May 2014 Associate Professor of Wombat Genetics, Quokka University. Associate of Quokka "
    "Institute, 2008-2013. Associate of the Wombat Center, 2009-2014",
    "March 2011-May 2014 Associate Professor of Wombat Genetics, Quokka University; Associate of Quokka "
    "Institute, 2008-2013; Associate of the Wombat Center, 2009-2014 (honorary)",
    "2001-present Professor of Pouch Care (2000-present) and Burrow Science (2001-2015), Quokka University",
    "1984-2012 Director of Wombat Burrows, Departments of Pouch Care (l984-2012 and Marsupials - l988-1997) "
    "Quokka Center for Burrow Science",
])
def test_dated_record_clauses_does_not_cut_a_date_led_line_whose_later_dates_end_clauses(unit):
    assert text_norm.dated_record_clauses(unit) == []


def test_dated_record_clauses_keeps_a_date_led_line_with_a_bracketed_later_date():
    # Opened after a full stop, not after a word, the parenthesis is the date's own.
    assert text_norm.dated_record_clauses("2001 Quokka Society Lecture. (2003) Wombat Council Lecture") == [
        "2001 Quokka Society Lecture", "(2003) Wombat Council Lecture"]


def test_dated_record_clauses_keeps_each_clause_s_own_month():
    unit = ("10/2011 Quokka Hospital Pouch Care Forum #1 (regional) 2/2012 Quokka Hospital Pouch Care Forum #2 "
            "(regional) 4/2013 Wombat Burrow Advisory Council Retreat, Norvale (national)")
    assert text_norm.dated_record_clauses(unit) == [
        "10/2011 Quokka Hospital Pouch Care Forum #1 (regional)", "2/2012 Quokka Hospital Pouch Care Forum #2 (regional)",
        "4/2013 Wombat Burrow Advisory Council Retreat, Norvale (national)"]


@pytest.mark.parametrize("unit, dates", [
    ("Quokka Club Chapter October 2012-April2013 Wombat Student Organization October 2012-June 2013",
     ["2012-April2013", "2012-June 2013"]),
    ("Member, Quokka Study Section, 1990 to 1992 Wombat Collegium, 1994 to present", ["1990 to 1992",
                                                                                    "1994 to present"]),
    ("Member, Quokka Plan Committee, 2011-2 Member, Wombat Advisory Council, 2011-", ["2011-2", "2011-"]),
    ("Member, Quokka Board, April2012. Chair, Wombat Council, 6/14/2014", ["April2012", "6/14/2014"]),
])
def test_record_date_anchors_read_a_date_whole(unit, dates):
    anchors = text_norm.record_date_anchors(unit)
    assert [unit[a.start:a.end] for a in anchors] == dates


def test_dated_record_clauses_keep_a_date_open_at_their_end():
    assert text_norm.dated_record_clauses("Member, Quokka Plan Committee, 2011-2 Member, Wombat Advisory Council, 2011-") == [
        "Member, Quokka Plan Committee, 2011-2", "Member, Wombat Advisory Council, 2011-"]
    assert text_norm.dated_record_clauses(
        "Member, Quokka Study Section, 1990 to 1992 Wombat Collegium - member, 1994 to present") == [
        "Member, Quokka Study Section, 1990 to 1992", "Wombat Collegium - member, 1994 to present"]


def test_dated_record_clauses_reads_a_year_after_its_month_as_the_same_date():
    # One talk, its date written twice.
    assert text_norm.dated_record_clauses(
        "03/2014 Quokka: The Hidden Wombat, Department of Burrows, Fall 2014 Lecture Series, Norvale "
        "University") == []


@pytest.mark.parametrize("unit, clauses", [
    ("Member, Vision 2020 Steering Committee, 2015-2017. Chair, Strategic Plan 2025 Task Force, 2018-2021",
     ["Member, Vision 2020 Steering Committee, 2015-2017", "Chair, Strategic Plan 2025 Task Force, 2018-2021"]),
    ("2015 Grand Rounds, Wombat Hospital: Quokka Science. 2016 Grand Rounds, Quokka Hospital: The Wombat "
     "Care Act of 2010", ["2015 Grand Rounds, Wombat Hospital: Quokka Science",
                          "2016 Grand Rounds, Quokka Hospital: The Wombat Care Act of 2010"]),
    # a year after a body, or between two offices, is a date
    ("Quokka Burrow Gala Committee 2012 Wombat Pouch Drive Advisory Board 2012-2018",
     ["Quokka Burrow Gala Committee 2012", "Wombat Pouch Drive Advisory Board 2012-2018"]),
    ("Quokka Society Treasurer 2005 President, Wombat Council 2008",
     ["Quokka Society Treasurer 2005", "President, Wombat Council 2008"]),
    ("Quokka Society Treasurer 2005 Wombat Council Chair 2008",
     ["Quokka Society Treasurer 2005", "Wombat Council Chair 2008"]),
    ("Member, Quokka Hall 2005 President, Wombat Council 2008",
     ["Member, Quokka Hall 2005", "President, Wombat Council 2008"]),
    ("Member, Quokka Hall 2005 Professor, Wombat College 2008",
     ["Member, Quokka Hall 2005", "Professor, Wombat College 2008"]),
    ("Member, Quokka Hall 2005 Treasurer, Wombat Council 2008",
     ["Member, Quokka Hall 2005", "Treasurer, Wombat Council 2008"]),
    ("Quokka Professor 2005 Wombat Council Chair 2008", ["Quokka Professor 2005", "Wombat Council Chair 2008"]),
])
def test_record_date_anchors_read_a_year_inside_any_name_as_part_of_it(unit, clauses):
    assert text_norm.dated_record_clauses(unit) == clauses


@pytest.mark.parametrize("pointer", ["(see Leadership)", "(see pg. 4)", "see also: Teaching", "refer to Appendix",
                                     "described in Section III", "see Quokka Section"])
def test_dated_record_clauses_reads_any_pointer_as_no_record(pointer):
    unit = f"2001 Member, Quokka Society Board. 2003 Treasurer, Wombat Council {pointer}"
    assert text_norm.dated_record_clauses(unit) == []
    # "See" as a name is no pointer.
    assert len(text_norm.dated_record_clauses(
        "2001 Member, Quokka Society Board. 2003 Treasurer, Holy See Wombat Council")) == 2


def test_dated_record_clauses_reads_first_person_lines_as_narrative():
    assert text_norm.dated_record_clauses(
        "In 2005 I became Director of the Quokka Center. In 2010 I was named Chair of the Wombat "
        "Department.") == []
    assert text_norm.dated_record_clauses(
        "Member, Quokka Board, 2001. Chair, Wombat Council, 2003, my first office") == []
    # A roman numeral and a capitalised "Our" name no narrator.
    assert len(text_norm.dated_record_clauses(
        "Member, Phase I trials Committee, 2001. Chair, Our Lady of Wombat Hospital Board, 2003")) == 2


def test_dated_record_clauses_closes_the_parenthesis_a_clause_opens():
    assert text_norm.dated_record_clauses(
        "Mentor, Quokka Program, 2014 Mentor, Wombat Pouch Support Program (since 2009)") == [
        "Mentor, Quokka Program, 2014", "Mentor, Wombat Pouch Support Program (since 2009)"]


def test_dated_record_clauses_leaves_a_pipe_row_whole():
    assert text_norm.dated_record_clauses(
        "Quokka Guest Professor | 2016-19 | Wombat University, Visiting Professorship (2016-2019)") == []


def test_dated_record_clauses_needs_the_word_its_section_s_records_name():
    positions = ("Quokka School of Medicine, Norvale, Wombatia (1981-1984) Assistant Professor of Pouch Care, "
                 "Wombat, Wombatia (1984-1990)")
    assert len(text_norm.dated_record_clauses(positions)) == 2
    assert text_norm.dated_record_clauses(positions, record_word=text_norm.POSITION_TITLE_RE) == []
    memberships = "Charter member, Quokka Society, Norvale Chapter, 1987, Chairperson, Pouch Committee, 1989"
    assert len(text_norm.dated_record_clauses(memberships)) == 2
    assert text_norm.dated_record_clauses(memberships, record_word=text_norm.ORGANIZATION_WORD_RE) == []
    assert len(text_norm.dated_record_clauses("Member, Quokka Society, 1987. Member, Wombat Academy, 1989",
                                              record_word=text_norm.ORGANIZATION_WORD_RE)) == 2


@pytest.mark.parametrize("qualifier", ["Vice", "Deputy", "Senior", "Junior", "Interim", "Acting", "Consulting",
                                       "Contributing", "Founding", "Elect", "Elected", "Past", "Honorary",
                                       "Executive", "General"])
def test_dated_record_clauses_reads_a_qualified_office_alone_as_no_record(qualifier):
    assert text_norm.dated_record_clauses(f"Member, Quokka Society Board, 2001. {qualifier} Chair, 2003") == []
    assert text_norm.dated_record_clauses(f"Member, Quokka Society Board, 2001. {qualifier} Wombat Chair, 2003") == [
        "Member, Quokka Society Board, 2001", f"{qualifier} Wombat Chair, 2003"]


@pytest.mark.parametrize("word", ["from", "since", "until", "then", "also", "and", "the"])
def test_dated_record_clauses_reads_an_office_with_a_connective_alone_as_no_record(word):
    assert text_norm.dated_record_clauses(f"Member, Quokka Society Board, 2001. Chair {word} 2003") == []


def test_dated_record_clauses_leaves_a_note_that_holds_the_next_date_where_it_is():
    assert text_norm.dated_record_clauses(
        "Member, Quokka Committee, 2014-2017 (Chair 2015-2016). Member, Wombat Board, 2019") == [
        "Member, Quokka Committee, 2014-2017", "Member, Wombat Board, 2019"]


def test_dated_record_clauses_reads_a_date_with_no_words_before_it_as_an_aside():
    assert text_norm.dated_record_clauses(
        "Member, Quokka Board, 1988 (Wombat University), 1990 (Norvale University). Chair, Wombat Council, 1995") == [
        "Member, Quokka Board, 1988 (Wombat University)", "Chair, Wombat Council, 1995"]


def test_dated_record_clauses_gives_a_date_led_clause_the_note_after_its_date():
    assert text_norm.dated_record_clauses("2001 (Quokka University Lecture), 2003 (Wombat University Lecture)") == [
        "2001 (Quokka University Lecture)", "2003 (Wombat University Lecture)"]


@pytest.mark.parametrize("unit", [
    "I chaired the Quokka Society Board, 2001. Treasurer, Wombat Council, 2003",
    "Member, Quokka Society Board, 2001. I led the Wombat Council, 2003",
    "Member, Quokka Society Board, 2001; Treasurer, Wombat Council, 2003, and I founded its fund",
    "Member, Quokka Society Board, 2001. Treasurer, Wombat Council, 2003, with our team",
])
def test_dated_record_clauses_reads_each_first_person_form_as_narrative(unit):
    assert text_norm.dated_record_clauses(unit) == []


def test_record_date_anchors_hold_the_years_of_a_glued_date():
    unit = "Member, Quokka Board, May2016. Chair, Wombat Council, 2015-May2018"
    assert [a.years for a in text_norm.record_date_anchors(unit)] == [frozenset({"2016"}),
                                                                       frozenset({"2015", "2018"})]


def test_dated_record_clauses_opens_only_a_parenthesis_closed_before_any_opens():
    assert text_norm.dated_record_clauses("9) Member (elected), Quokka Council, 1993. Chair, Wombat Board, 1995") == [
        "(9) Member (elected), Quokka Council, 1993", "Chair, Wombat Board, 1995"]
    assert text_norm.dated_record_clauses("Member, Quokka Board (QB)), 1999. Chair, Wombat Council, 2001") == [
        "Member, Quokka Board (QB)), 1999", "Chair, Wombat Council, 2001"]


def test_a_position_names_any_title():
    unit = "Teacher, Quokka High School, 2003-2004. Instructor, Wombat College, 2004-2006"
    assert len(text_norm.dated_record_clauses(unit, record_word=text_norm.POSITION_TITLE_RE)) == 2


@pytest.mark.parametrize("unit", ["Member, Quokka Hall:2005 Wombat Council, 2008",
                                  "Member, Quokka Vision 2005/Wombat Council, 2008"])
def test_record_date_anchors_read_a_year_beside_punctuation_as_a_date(unit):
    # Only a space on both sides puts a year inside a name.
    assert [unit[a.start:a.end] for a in text_norm.record_date_anchors(unit)] == ["2005", "2008"]


def test_date_clauses_attach_no_note_for_the_doctor():
    # The doctor's cut is the one it had before the recovery's (#1243 review):
    # a note after a date opens the next clause, which it then reads as an aside.
    part = "Member, Quokka Board, 2001 (Chair). Treasurer, Wombat Council, 2005"
    assert [clause.text for clause in text_norm.date_clauses(part, text_norm.record_date_anchors(part))] == [
        "Member, Quokka Board, 2001", " (Chair). Treasurer, Wombat Council, 2005"]
