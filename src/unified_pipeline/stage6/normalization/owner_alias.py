"""The CV owner's surname as this bibliography actually spells it (#1393).

Stage 6 bolds the owner by exact, whole-token match on `target_name` or
`cv_owner.last_name`. When the citations spell the owner some other way -- a
dropped accent (`Muller` for `Müller`), half of a compound surname (`Lopez`
for `Garcia-Lopez`), a married name added (`Jones-Patel` for `Patel`), a typo
(`Wendee` for `Wende`) -- nothing is bolded. This module finds the most
plausible spelling instead.

It is decided once per CV, over every citation the exact match missed, not per
citation: the owner is an author on nearly every paper in their own CV, so the
spelling that recurs across those citations and looks like the owner's name is
the owner. A co-author with a similar name appears in too few of them to win.

Two steps:

- regex: each citation's author segment is split into author tokens, and the
  surname-shaped words (not initials, particles or markers) are the candidates.
- TF-IDF: each candidate is scored by cosine similarity of character-trigram
  TF-IDF vectors against the owner's name keys, accents and punctuation folded
  away. IDF is fit on every candidate surname in the bibliography, so
  trigrams many co-authors share ("son", "er") count for less than the ones
  that distinguish a name.

Pure: citations in, one surname spelling (or '') out.
"""
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence

# ponytail: thresholds set on hand-built name pairs (accent, compound half,
# one-letter typo vs Anderson/Henderson-style near misses), not measured on the
# corpus. Revisit with a batch comparing fuzzy bolds to the citation count.
MIN_SIMILARITY = 0.6
# A candidate must appear in more than this share of the unmatched citations.
MIN_COVERAGE = 0.5

_NGRAM = 3
# The author segment ends at the first sentence break that opens a quote or a
# phrase whose second word is lower case (a title: "A study of"), or at a year.
# Not at any capitalised word, so "Michael E. Wende" stays one author.
_AUTHOR_SEGMENT_END = re.compile(
    r"\.\s+(?=[\"“'‘]|\S+\s+[a-z])|\(?\b(?:19|20)\d{2}\b")
_LEADING_NUMBER = re.compile(r"^\s*\d+[.)]\s*")
_AUTHOR_SEPARATOR = re.compile(r"[,;&]|\band\b|\bet al\b", re.IGNORECASE)
_MARKERS = re.compile(r"[*†‡§¶#^\d]+")
_INITIALS = re.compile(r"^(?:[A-Z]\.?){1,3}$")
_PARTICLES = frozenset((
    "van", "von", "der", "den", "de", "la", "le", "da", "di", "del", "della",
    "du", "dos", "das", "do", "bin", "ibn", "al", "el", "ter", "ten", "zu", "st",
))
# An author token longer than this is a title fragment, not a name.
_MAX_AUTHOR_WORDS = 4


def fold_name(name: str) -> str:
    """Lower-case letters only, accents removed: "O'Connor" -> "oconnor"."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in decomposed.lower() if ch.isalpha() and ch.isascii())


_HYPHENS = r"\-‐‑–"


def _name_parts(name: str, separators: str) -> list[str]:
    """Folded parts of a name, of two or more letters each."""
    return [p for p in (fold_name(w) for w in re.split(f"[{separators}]+", name)) if len(p) > 1]


def _author_segment(citation: str) -> str:
    text = _LEADING_NUMBER.sub("", citation)
    end = _AUTHOR_SEGMENT_END.search(text)
    return text[:end.start()] if end else text


def candidate_surnames(citation: str) -> list[str]:
    """Surname-shaped words in the citation's author list, as written.

    A word is a candidate when it starts with a capital, is not one to three
    initials, and is not a particle. Adjacent candidate words in one author
    are also offered joined ("Mac Donald"). An author token with a lower-case
    word that is not a particle is a title fragment and contributes nothing.
    """
    found: list[str] = []
    for token in _AUTHOR_SEPARATOR.split(_author_segment(citation)):
        words = _MARKERS.sub("", token).split()
        if not words or len(words) > _MAX_AUTHOR_WORDS:
            continue
        if any(w[0].islower() and w.lower() not in _PARTICLES for w in words):
            continue
        run: list[str] = []
        for word in [w.strip(".") for w in words] + [""]:
            if len(fold_name(word)) < 2 or _INITIALS.match(word) or word.lower() in _PARTICLES:
                if len(run) > 1:
                    found.append(" ".join(run))
                run = []
                continue
            found.append(word)
            run.append(word)
    return found


def _trigrams(key: str) -> Counter[str]:
    padded = f" {key} "
    return Counter(padded[i:i + _NGRAM] for i in range(len(padded) - _NGRAM + 1))


class _TfIdf:
    """Character-trigram TF-IDF over one bibliography's surnames."""

    def __init__(self, keys: Iterable[str]) -> None:
        documents = set(keys)
        self._count = len(documents)
        self._df: Counter[str] = Counter()
        for key in documents:
            self._df.update(set(_trigrams(key)))

    def _vector(self, key: str) -> dict[str, float]:
        # Smoothed IDF, so a trigram no surname here has still weighs most.
        return {g: tf * (math.log((1 + self._count) / (1 + self._df[g])) + 1)
                for g, tf in _trigrams(key).items()}

    def similarity(self, a: str, b: str) -> float:
        va, vb = self._vector(a), self._vector(b)
        dot = sum(w * vb.get(g, 0.0) for g, w in va.items())
        norm = math.sqrt(sum(w * w for w in va.values()) * sum(w * w for w in vb.values()))
        return dot / norm if norm else 0.0


def _owner_keys(owner_names: Iterable[str]) -> set[str]:
    """Each owner surname folded whole, plus each of its hyphen or space
    parts ("García López" -> garcialopez, garcia, lopez)."""
    keys: set[str] = set()
    for name in owner_names:
        if not name or not name.strip():
            continue
        whole = fold_name(name)
        if len(whole) > 1:
            keys.add(whole)
        keys.update(_name_parts(name, rf"\s{_HYPHENS}"))
    return keys


def _score(tfidf: _TfIdf, candidate: str, owner_keys: set[str]) -> float:
    """Best similarity between the candidate (whole, or a hyphen part) and
    an owner key that starts with the same letter. A different first letter
    is never the owner: that gate is what keeps Henderson off an Anderson CV.
    A space-joined candidate scores whole only, so "Michael Wendee" never
    ties with "Wendee"."""
    cand_keys = {fold_name(candidate), *_name_parts(candidate, _HYPHENS)}
    if " " in candidate:
        cand_keys = {fold_name(candidate)}
    return max((tfidf.similarity(c, o) for c in cand_keys for o in owner_keys
                if c and o and c[0] == o[0]), default=0.0)


def infer_owner_alias(unmatched_citations: Sequence[str], owner_names: Iterable[str]) -> str:
    """The owner's surname as the unmatched citations spell it, or ''.

    `unmatched_citations` are the citations in which no exact owner name was
    found; `owner_names` are the names the exact match tried (`target_name`
    surnames, `cv_owner.last_name`) -- surnames, not full names, or a
    first name becomes a key. Returns the candidate, as written in the
    citations, with the highest similarity to an owner key, provided it
    reaches `MIN_SIMILARITY` and appears in more than `MIN_COVERAGE` of the
    unmatched citations. Ties go to the more frequent spelling, then the
    alphabetically first, so the result is deterministic.
    """
    owner_keys = _owner_keys(owner_names)
    if not unmatched_citations or not owner_keys:
        return ''
    per_citation = [candidate_surnames(c) for c in unmatched_citations]
    tfidf = _TfIdf(fold_name(w) for words in per_citation for w in words)

    coverage: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    for words in per_citation:
        for word in words:
            spellings.setdefault(fold_name(word), Counter())[word] += 1
        coverage.update({fold_name(w) for w in words})

    needed = math.floor(MIN_COVERAGE * len(unmatched_citations)) + 1
    best: tuple[float, int, str] | None = None
    for key, seen in coverage.items():
        if seen < needed:
            continue
        spelling = sorted(spellings[key].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        score = _score(tfidf, spelling, owner_keys)
        if score < MIN_SIMILARITY:
            continue
        rank = (score, seen, spelling)
        if best is None or (rank[0], rank[1]) > (best[0], best[1]) or \
                ((rank[0], rank[1]) == (best[0], best[1]) and spelling < best[2]):
            best = rank
    return best[2] if best else ''
