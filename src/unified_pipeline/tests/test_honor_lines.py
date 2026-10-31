"""core/honor_lines.py (#1577): the honors-heading and honorific-fellowship tests
that stage 3b's fellowship pin, stage 6's research-support guard and the
doctor's section_consistency lint share.

Self-contained: no LLM, no network, invented text only.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.honor_lines import (  # noqa: E402
    heading_names_honors,
    is_honorific_fellowship,
)

# --- heading_names_honors -----------------------------------------------------


@pytest.mark.parametrize("heading", [
    "Honors and Awards", "AWARDS", "Prizes", "Professional Recognition", "Distinctions",
    "Honours", "Post-Degree Honors and Awards", "Academic Honors and Awards",
])
def test_a_heading_naming_honors_names_honors(heading):
    assert heading_names_honors(heading)


@pytest.mark.parametrize("heading", [
    # a funding word too: these head grants lists
    "Grants and Contract Awards", "Current Awards", "Research Awards", "Past Awards",
    "Pending Awards", "Awards and Support", "Investigator Awards",
    # no honors word
    "Professional Memberships", "Education", "",
])
def test_a_heading_naming_funding_or_no_honors_does_not(heading):
    assert not heading_names_honors(heading)


# --- is_honorific_fellowship: an election ---------------------------------------

@pytest.mark.parametrize("text", [
    "2017: Elected Fellow of the Invented Society for Cell Examples",
    "Elected to Fellowship, Invented Diseases Society - 1994",
    "Named Fellow of the Invented Heart Association (FIHA)",
    "2007 Inducted as a Fellow in the Invented Psychological Association.",
    "Oct. 2015 Elected Honorary Fellow, Invented Royal College of Examples",
    "2031 Elected to the College of Fellows of the Invented Institute for Examples (IIE).",
    # stated later in the line, with no other role
    "2031-present Invented Epidemiological Society (elected Fellow)",
    "2031 Fellow of the Invented Heart Association (elected)",
    "Fellow, Invented Liver Association (elected 2031)",
    "Invented College of Nutrition, Elected as Fellow, 2031.",
])
def test_an_elected_fellowship_is_an_honor_under_any_heading(text):
    assert is_honorific_fellowship(text, ("Professional Memberships",))


@pytest.mark.parametrize("text", [
    # a membership row that mentions the election beside another role (CYOFWJ 65)
    "Invented Academy of Examples (Active Member; Science Committee, 2031-2033; elected fellow 2034)",
    "Invented Society (elected fellow 2031; Research Committee Chairman, 2032-2034)",
    "Society of Invented Fellows, elected 2031 - present member",
    # "elected" with no fellowship
    "Elected Member, Invented Society of Examples, 2031",
    "Elected President, Invented Society of Examples, 2031",
])
def test_an_election_that_is_not_the_lines_fellowship_is_not(text):
    assert not is_honorific_fellowship(text, ("Professional Societies",))


# --- is_honorific_fellowship: a fellowship title under an honors heading ---------

@pytest.mark.parametrize("text", [
    "2031 Fellow, Invented College of Medical Examples (ICME)",
    "Fellow of the Invented College of Radiology, 2031",
    "Fellow 2031-present\tInvented College of Surgeons",
    "Fellow (2031), Invented Association",
    "1988     Founding Fellow, Invented Informatics Association",
    "2031 Distinguished Fellow of the Invented Psychiatric Association",
    "2031 Invented Gastroenterological Association Fellow",
    "Invented Society Fellow 2031",
    "Fellow\tInvented Society for Bone Examples\t2031-present",            # GDGSWJ 98
    "Fellow | Invented Academy of Examples | 2031",
    "2031             Fellowship, Invented College of Physicians",      # SHERVB 76
    "Fellowship | Invented College of Physicians | January 2031",        # CTXOTY 67.2
    "Fellowship in the Invented College of Critical Examples (FICE), Invented Society 2031",  # GNONHA 213
    "Invented College of Physicians, Fellow | 2031",                      # PBSGQZ 67.0
    "1988-1991 Invented Society of Examples Fellow",                     # NTCULM 34
    "2031- FICNM, fellow of Invented College of Nuclear Examples",       # web175 80
])
def test_a_fellowship_title_under_an_honors_heading_is_an_honor(text):
    assert is_honorific_fellowship(text, ("Professional Honors & Recognition", "National"))


@pytest.mark.parametrize("text", [
    # a fellowship programme, a training title, a member
    "1988-1991 Invented Society Fellowship",
    "Research Fellow, Invented Institute",
    "Member, Invented Society of Examples",
    # an award or programme named for fellows, not a title
    "2031 Fellow Travel Award, Invented Society of Examples",
    "Fellow - Invented Leadership Programme, 2031",
    "Invented Society, Fellow Travel Award 2031",
    "Fellowship - Invented Leadership Programme, 2031",
    # an acronym before "Fellow": a training or funding fellowship as often as an honorific one
    "NIH Fellow 1998",
    "INVA Fellow, June 2031",
])
def test_lines_that_are_no_fellowship_title_are_not_an_honor(text):
    assert not is_honorific_fellowship(text, ("Honors and Awards",))


@pytest.mark.parametrize("heading_path", [
    # the leaf names memberships or credentials: the author put it there (UVZNIC 131, HBALTD 26, EUCSNU 45)
    ("Awards, Honors, and Membership in Honorary Societies",),
    ("Honors and Awards", "Memberships"),
    ("Honors and Awards", "Organizations"),
    ("Honors and Awards", "Affiliations"),
    ("Honors and Awards", "Certifications"),
    ("Honors and Awards", "Licensure"),
    ("Professional Certifications",),
    # no honors word on the path
    ("Professional Societies",),
    ("Service",),
    (),
])
def test_a_fellowship_title_off_an_honors_path_is_not_an_honor(heading_path):
    assert not is_honorific_fellowship("2031 Fellow, Invented College of Examples", heading_path)


def test_the_honors_word_may_sit_on_a_parent_heading():
    # MVUREJ 44: "Professional Honors & Recognition > International/National/Regional".
    assert is_honorific_fellowship("2031 Fellow, Invented College of Examples",
                                   ("Professional Honors & Recognition", "International/National/Regional"))
