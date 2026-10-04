"""`stage6/normalization/owner_alias.py`: the owner's surname as the
bibliography spells it, when no exact name matches (#1393). Names are invented."""
import pytest

from unified_pipeline.stage6.normalization.owner_alias import (
    candidate_surnames,
    fold_name,
    infer_owner_alias,
)

_TAIL = ". A study of things. J Things. 2023;1:1-9."


@pytest.mark.parametrize("name, folded", [
    ("O'Connor", "oconnor"),
    ("Mac Donald", "macdonald"),
    ("Müller", "muller"),
    ("Müller", "muller"),
    ("García-López", "garcialopez"),
])
def test_fold_name(name, folded):
    assert fold_name(name) == folded


@pytest.mark.parametrize("citation, expected", [
    # Initials, markers and the leading citation number are not candidates.
    ("12. Wende ME*, Smith J" + _TAIL, ["Wende", "Smith"]),
    # Comma form: "Wende, M. E." splits into a surname and an initials token.
    ("Wende, M. E., Smith, J. A. Title of a paper. J Things. 2020.", ["Wende", "Smith"]),
    # Full-name form keeps the surname after the middle initial.
    ("Michael E. Wendee, John Smith. Cancer genomics in mice. Nature. 2020.",
     ["Michael", "Wendee", "John", "Smith", "John Smith"]),
    # Adjacent name words are also offered joined.
    ("Doe A, Mac Donald P" + _TAIL, ["Doe", "Mac", "Donald", "Mac Donald"]),
    # Particles are not candidates; the bibliography matcher widens over them.
    ("Doe A, de la Cruz M" + _TAIL, ["Doe", "Cruz"]),
    # The title is not an author list: the segment ends before it.
    ("Smith J, Lee K. Wende disease in mice. 2020.", ["Smith", "Lee"]),
    ("Smith J, Lee K (2020) Wende disease.", ["Smith", "Lee"]),
])
def test_candidate_surnames(citation, expected):
    assert candidate_surnames(citation) == expected


@pytest.mark.parametrize("citations, owner_names, alias", [
    # Accent dropped or added.
    (["Muller K, Smith J" + _TAIL], ["Müller"], "Muller"),
    (["Müller K, Smith J" + _TAIL], ["Muller"], "Müller"),
    # Half of a compound surname, or a married name added.
    (["Lopez M, Smith J" + _TAIL], ["Garcia-Lopez"], "Lopez"),
    (["Smith J, Jones-Patel R" + _TAIL], ["Patel"], "Jones-Patel"),
    # Punctuation and spacing.
    (["Smith J, Oconnor P" + _TAIL], ["O'Connor"], "Oconnor"),
    (["Smith J, Mac Donald P" + _TAIL], ["MacDonald"], "Mac Donald"),
    # A one-letter typo.
    (["Smith J, Wendee ME" + _TAIL], ["Wende"], "Wendee"),
    (["Michael E. Wendee, John Smith. Cancer genomics in mice. Nature. 2020."],
     ["Wende"], "Wendee"),
])
def test_infer_owner_alias_finds_the_plausible_spelling(citations, owner_names, alias):
    assert infer_owner_alias(citations, owner_names) == alias


@pytest.mark.parametrize("citations, owner_names", [
    # Similar trigrams, different first letter.
    (["Smith J, Henderson P" + _TAIL], ["Anderson"]),
    # Too little in common.
    (["Smith J, Wuertz P" + _TAIL], ["Wu"]),
    (["Smith J, Lee K" + _TAIL], ["Wende"]),
    # A co-author in only half the unmatched citations is not the owner.
    (["Smith J, Alvarez-Diaz P" + _TAIL, "Doe J, Lee K" + _TAIL], ["Diaz"]),
    # A name in the title is not an author.
    (["Smith J, Lee K. Wende disease in mice. 2020."], ["Wende"]),
    # Nothing to search, or nothing to search for.
    ([], ["Wende"]),
    (["Smith J, Wende M" + _TAIL], []),
    (["Smith J, Wende M" + _TAIL], ["", "  "]),
])
def test_infer_owner_alias_declines_an_implausible_spelling(citations, owner_names):
    assert infer_owner_alias(citations, owner_names) == ''


def test_infer_owner_alias_needs_a_majority_of_unmatched_citations():
    owner_cites = ["Smith J, Wendee ME" + _TAIL, "Wendee M, Doe A" + _TAIL]
    other = ["Doe J, Lee K" + _TAIL]
    assert infer_owner_alias(owner_cites + other, ["Wende"]) == "Wendee"
    assert infer_owner_alias(owner_cites + other * 2, ["Wende"]) == ''


def test_infer_owner_alias_prefers_the_closer_spelling_then_the_more_frequent():
    citations = ["Wendee M, Wendt K, Smith J, Lee K" + _TAIL,
                 "Wendt K, Doe A, Wendee M, Park S" + _TAIL]
    # Both recur; "Wendee" is closer to "Wende" than "Wendt" is.
    assert infer_owner_alias(citations, ["Wende"]) == "Wendee"
    # Two spellings that fold the same: the more frequent one, as written.
    citations = ["Muller K" + _TAIL, "Muller K" + _TAIL, "MULLER K" + _TAIL]
    assert infer_owner_alias(citations, ["Müller"]) == "Muller"
