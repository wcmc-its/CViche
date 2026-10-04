"""`stage6/normalization/owner_alias.py`: the owner's surname as the
bibliography spells it (#1393). Names are invented."""
import pytest

from unified_pipeline.stage6.normalization.owner_alias import (
    MIN_ALIAS_CITATIONS,
    Author,
    OwnerAlias,
    fold_name,
    infer_owner_alias,
    name_parts,
    parse_authors,
)

_TAIL = ". A study of things. J Things. 2023;1:1-9."


@pytest.mark.parametrize("name, folded", [
    ("O'Connor", "oconnor"),
    ("Mac Donald", "macdonald"),
    ("Müller", "muller"),
    ("Müller", "muller"),
    ("García-López", "garcialopez"),
])
def test_fold_name(name, folded):
    assert fold_name(name) == folded


@pytest.mark.parametrize("name, parts", [
    ("Garza Ruiz", {"garza", "ruiz"}),
    ("Garza-Ruiz", {"garza", "ruiz"}),
    ("de la Cruz", {"cruz"}),
    # A surname that is only a particle word is still a name.
    ("Das", {"das"}),
])
def test_name_parts(name, parts):
    assert name_parts(name) == parts


@pytest.mark.parametrize("citation, expected", [
    # Initials, markers and the leading citation number are dropped.
    ("12. Wende ME*, Smith J" + _TAIL, [("Wende", "M"), ("Smith", "J")]),
    # Comma form: the initials token belongs to the surname before it.
    ("Wende, M. E., Smith, J. A. Title of a paper. J Things. 2020.",
     [("Wende", "M"), ("Smith", "J")]),
    # Given-name-first forms.
    ("Michael E. Wendee, John Smith. Cancer genomics in mice. Nature. 2020.",
     [("Wendee", "M"), ("Smith", "J")]),
    ("M. E. Wende, J. Smith" + _TAIL, [("Wende", "M"), ("Smith", "J")]),
    ("Ana Garza Ruiz, John Doe" + _TAIL, [("Garza Ruiz", "A"), ("Doe", "J")]),
    # Spaced compounds, hyphenated compounds and particles stay whole.
    ("Doe A, Garza Ruiz M" + _TAIL, [("Doe", "A"), ("Garza Ruiz", "M")]),
    ("Doe A, Garza-Ruiz M" + _TAIL, [("Doe", "A"), ("Garza-Ruiz", "M")]),
    ("Doe A, de la Cruz M" + _TAIL, [("Doe", "A"), ("de la Cruz", "M")]),
    ("dos Santos AS, Doe J" + _TAIL, [("dos Santos", "A"), ("Doe", "J")]),
    ("Das M, Doe J" + _TAIL, [("Das", "M"), ("Doe", "J")]),
    # The title is not an author list: the segment ends before it.
    ("Smith J, Lee K. Wende disease in mice. 2020.", [("Smith", "J"), ("Lee", "K")]),
    ("Smith J, Lee K (2020) Wende disease.", [("Smith", "J"), ("Lee", "K")]),
])
def test_parse_authors(citation, expected):
    assert parse_authors(citation) == [Author(s, i) for s, i in expected]


def _cites(*authors: str) -> list[str]:
    return [a + _TAIL for a in authors]


@pytest.mark.parametrize("citations, surnames, initials, aliases", [
    # Accent dropped or added.
    (_cites("Muller K, Smith J", "Doe J, Muller K"), ["Müller"], ["K"], ("Muller",)),
    (_cites("Müller K, Smith J", "Doe J, Müller K"), ["Muller"], ["K"], ("Müller",)),
    # Half of a compound surname, or a married name added.
    (_cites("Lopez M, Smith J", "Doe J, Lopez M"), ["Garcia-Lopez"], ["M"], ("Lopez",)),
    (_cites("Smith J, Jones-Patel R", "Jones-Patel R, Doe A"), ["Patel"], ["R"], ("Jones-Patel",)),
    # Punctuation and spacing.
    (_cites("Smith J, Oconnor P", "Oconnor P, Lee K"), ["O'Connor"], ["P"], ("Oconnor",)),
    (_cites("Smith J, Mac Donald P", "Mac Donald P, Lee K"), ["MacDonald"], ["P"], ("Mac Donald",)),
    # A one-letter typo.
    (_cites("Smith J, Wendee ME", "Wendee M, Lee K"), ["Wende"], ["M"], ("Wendee",)),
    # A spaced compound the stage-4 half belongs to (shape b).
    (_cites("Garza Ruiz M, Doe J", "Doe J, Garza Ruiz M"), ["Garza"], ["M"], ("Garza Ruiz",)),
    # Two forms of one compound, each passing on its own (shape g).
    (_cites("Garza M, Doe J", "Doe J, Garza M", "Ruiz M, Lee K", "Lee K, Ruiz M"),
     ["Garza-Ruiz"], ["M"], ("Garza", "Ruiz")),
    # A form joined to an accepted one by a shared part.
    (_cites("Garza Ruiz M, Doe J", "Doe J, Garza Ruiz M", "Ruiz M, Lee K", "Lee K, Ruiz M"),
     ["Garza"], ["M"], ("Garza Ruiz", "Ruiz")),
])
def test_infer_owner_alias_finds_the_owner_spellings(citations, surnames, initials, aliases):
    assert infer_owner_alias(citations, surnames, initials).surnames == aliases


@pytest.mark.parametrize("citations, surnames, initials, aliases", [
    # Stage 4 kept the other half of the compound (shape c).
    (_cites("Ruiz M, Doe J", "Doe J, Ruiz M", "Lee K, Ruiz M"), ["Garza"], ["M"], ("Ruiz",)),
    # A married name the papers never use; they carry the maiden name (shape k).
    (_cites("Nolan V, Doe J", "Doe J, Nolan V", "Lee K, Nolan V"), ["Kerr"], ["V"], ("Nolan",)),
    # A given name stored as the surname, and no first name (shape l): the
    # stored name's own letter is the initial.
    (_cites("Ruiz M, Doe J", "Doe J, Ruiz M", "Lee K, Ruiz M"), ["Mateo"], [], ("Ruiz",)),
    # The papers use the second surname, with its particle (shape p).
    (_cites("dos Santos AS, Doe J", "Doe J, dos Santos AS", "Lee K, dos Santos AS"),
     ["Silveira"], ["A"], ("dos Santos",)),
])
def test_infer_owner_alias_replaces_a_stage4_surname_the_papers_never_use(
        citations, surnames, initials, aliases):
    assert infer_owner_alias(citations, surnames, initials).surnames == aliases


@pytest.mark.parametrize("citations, surnames, initials, rejected", [
    # Similar trigrams, different first letter.
    (_cites("Smith J, Henderson P", "Henderson P, Lee K", "Doe J, Henderson P",
            "Anderson P, Lee K", "Anderson P, Doe J", "Anderson P"), ["Anderson"], ["P"], "Henderson"),
    # Too little in common.
    (_cites("Smith J, Wuertz P", "Wuertz P, Lee K", "Wu P, Lee K", "Wu P"), ["Wu"], ["P"], "Wuertz"),
    # A co-author who shares half the owner's compound, with another initial
    # (shapes h, r): the owner's own half is found, the co-author's never.
    (_cites("Garza J, Ruiz M", "Garza J, Doe A, Ruiz M", "Ruiz M, Garza J", "Garza J, Ruiz M"),
     ["Garza-Ruiz"], ["M"], "Garza"),
    (_cites("Garza M, Doe J", "Garza M, Lee K", "Doe J, Garza-Ruiz J", "Lee K, Garza-Ruiz J"),
     ["Garza"], ["M"], "Garza-Ruiz"),
    # A name in the title is not an author.
    (["Smith J, Lee K. Wende disease in mice. 2020."] * 3, ["Wende"], ["W"], "Wende"),
    # Nothing to search.
    ([], ["Wende"], ["M"], "Wende"),
])
def test_infer_owner_alias_declines(citations, surnames, initials, rejected):
    assert rejected not in infer_owner_alias(citations, surnames, initials).surnames


def test_infer_owner_alias_shape_h_finds_only_the_owner_half():
    citations = _cites("Garza J, Ruiz M", "Garza J, Doe A, Ruiz M", "Ruiz M, Garza J", "Garza J, Ruiz M")
    assert infer_owner_alias(citations, ["Garza-Ruiz"], ["M"]).surnames == ("Ruiz",)


def test_infer_owner_alias_needs_an_owner_initial():
    # No stage-4 initial, no exact owner author, and the stage-4 surname is
    # carried by an author: nothing is known to check initials against.
    citations = _cites("Wende, Doe J", "Wende, Lee K")
    assert infer_owner_alias(citations, ["Wende"]) == OwnerAlias()


def test_infer_owner_alias_counts_the_whole_bibliography():
    owner = _cites("Garza-Ruiz ME, Doe J") * 8
    # One stray citation is never enough, however few the misses (shape q).
    assert "Ruiz" not in infer_owner_alias(owner + _cites("Ruiz M, Lee K"), ["Garza-Ruiz"], ["M"]).surnames
    # Enough citations, but under the share of the whole bibliography.
    few = _cites("Ruiz M, Lee K") * MIN_ALIAS_CITATIONS
    assert "Ruiz" not in infer_owner_alias(owner + few, ["Garza-Ruiz"], ["M"]).surnames
    assert "Ruiz" in infer_owner_alias(owner + few * 2, ["Garza-Ruiz"], ["M"]).surnames


def test_infer_owner_alias_keeps_stage4_when_it_names_the_papers():
    # Stage 4 names most citations, so an unrelated frequent co-author with
    # the owner's initial does not replace it.
    citations = _cites("Garza M, Ruiz M", "Ruiz M, Garza M", "Ruiz M, Doe J", "Garza M, Lee K")
    assert infer_owner_alias(citations, ["Garza"], ["M"]).surnames == ("Garza",)


def test_infer_owner_alias_initials_fall_back_to_the_exact_owner_authors():
    citations = _cites("Wende ME, Doe J", "Doe J, Wende-Lopez M", "Lee K, Wende-Lopez M", "Wende ME, Lee K")
    alias = infer_owner_alias(citations, ["Wende"])
    assert alias.initials == frozenset("M")
    assert "Wende-Lopez" in alias.surnames


def test_infer_owner_alias_result_carries_initials_and_parts():
    citations = _cites("Garza Ruiz M, Doe J", "Doe J, Garza Ruiz M")
    parts = frozenset({"garza", "ruiz"})
    assert infer_owner_alias(citations, ["Ruiz"], ["m"]) == OwnerAlias(
        ("Garza Ruiz",), frozenset("M"), parts, parts)


def test_infer_owner_alias_prefers_the_more_frequent_spelling_as_written():
    citations = _cites("Muller K", "Muller K", "MULLER K")
    assert infer_owner_alias(citations, ["Müller"], ["K"]).surnames == ("Muller",)


# --- #1394 review: the author segment and the small-bibliography gate --------


@pytest.mark.parametrize("citation, expected", [
    # A Title Case title: the segment ends at the period after the last
    # author's initials, so the last (often senior) author is kept.
    ("Doe J, Lee K, Garza Ruiz MA. Title Case Study of Things. J Things. 2020;1:1.",
     [("Doe", "J"), ("Lee", "K"), ("Garza Ruiz", "M")]),
    ("Doe J, Wende ME. Chemoprevention. J Things. 2020;1:1.", [("Doe", "J"), ("Wende", "M")]),
    # Not a Vancouver end: a given name, a middle initial, then the surname.
    ("Michael E. Wende, John Doe. A study of things. 2020.", [("Wende", "M"), ("Doe", "J")]),
    # Hyphenated and four-letter initials are never a surname.
    ("Lee H.-T, Doe HJWL, Smith A. Title Case Study. J. 2020.",
     [("Lee", "H"), ("Doe", "H"), ("Smith", "A")]),
])
def test_parse_authors_vancouver_title_case_and_initials(citation, expected):
    assert parse_authors(citation) == [Author(s, i) for s, i in expected]


@pytest.mark.parametrize("owner, coauthor", [
    ("Chen", "Cheng"),       # one letter added
    ("Martin", "Martinez"),  # a longer surname that starts with the owner's
    ("Moser", "Mosser"),     # a doubled letter
])
@pytest.mark.parametrize("total", [3, 4, 5, 6, 10])
@pytest.mark.parametrize("absent", [1, 2])
def test_infer_owner_alias_never_aliases_a_similar_coauthor(owner, coauthor, total, absent):
    # The owner is absent from 1-2 citations, where a co-author with the same
    # initial and a similar surname appears: never the owner's alias, on a
    # bibliography of any size.
    citations = _cites(*[f"{owner} M, Doe J"] * (total - absent), *[f"Doe J, {coauthor} M"] * absent)
    assert coauthor not in infer_owner_alias(citations, [owner], ["M"]).surnames


def test_infer_owner_alias_close_spelling_only_when_stage4_spelling_is_absent():
    # Stage 4's spelling on no paper: the close one is the owner's (a stage-4
    # typo). On even one paper: the close one is someone else.
    typos = _cites("Wendee M, Doe J", "Doe J, Wendee M", "Lee K, Wendee M")
    assert infer_owner_alias(typos, ["Wende"], ["M"]).surnames == ("Wendee",)
    assert "Wendee" not in infer_owner_alias(typos + _cites("Wende M, Lee K"), ["Wende"], ["M"]).surnames
