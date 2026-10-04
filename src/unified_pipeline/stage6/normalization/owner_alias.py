"""The CV owner's surname as this bibliography actually spells it (#1393).

Stage 6 bolds the owner by exact, whole-token match on `target_name` or
`cv_owner.last_name`. Those come from stage 4, which is not ground truth: for
a compound surname it can keep one half (`Garza` for `Garza Ruiz`), store a
given name as the surname, or hold a married name the papers never use. And
the citations themselves vary: a dropped accent (`Muller` for `Müller`), half
of a compound, a married name added, a typo.

So the owner's spellings are read from the bibliography itself, once per CV:

- parse: each citation's author segment is split into authors, each a surname
  (as written, particles and compound halves kept) and a first initial.
- candidates: the surnames of authors whose initial is the owner's, counted
  over the WHOLE bibliography. The owner is on nearly every paper in their own
  CV; a co-author with the same initial and a related surname is not.
- relation: a candidate is the owner when it shares a name part with a
  stage-4 surname, is a close spelling of one (character-trigram TF-IDF, same
  first letter), or shares a part with a candidate already accepted. When
  stage 4's surname names almost no citation, the one dominant candidate
  replaces it.

Every accepted spelling is returned, with the owner's initials and the name
parts, so the bibliography bolds an alias only on an author whose initials
fit, and can widen a half-matched spaced compound to the whole surname.

Pure: citations in, an `OwnerAlias` out.
"""
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# ponytail: set on hand-built name pairs (accent, compound half, one-letter
# typo vs Anderson/Henderson-style near misses) and checked against the
# corpus render A/B, not tuned on it.
MIN_SIMILARITY = 0.6
# A candidate must be an initial-compatible author in at least this share of
# all citations, and in at least MIN_ALIAS_CITATIONS of them. The initials
# gate is what keeps co-authors out; the count only stops one stray citation
# deciding the owner's name.
MIN_COVERAGE = 0.3
MIN_ALIAS_CITATIONS = 2
# Stage 4's surname is replaced outright only when it names fewer than this
# share of the citations and one candidate covers at least MIN_REPLACEMENT.
MAX_STAGE4_SHARE = 0.5
MIN_REPLACEMENT = 0.5

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
# "M.E." or "M. E." written as separate words still reads as initials.
_PARTICLES = frozenset((
    "van", "von", "der", "den", "de", "la", "le", "da", "di", "del", "della",
    "du", "dos", "das", "do", "bin", "ibn", "al", "el", "ter", "ten", "zu", "st",
))
# An author token longer than this is a title fragment, not a name.
_MAX_AUTHOR_WORDS = 5
_HYPHENS = r"\-‐‑–"


def fold_name(name: str) -> str:
    """Lower-case letters only, accents removed: "O'Connor" -> "oconnor"."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in decomposed.lower() if ch.isalpha() and ch.isascii())


def name_parts(name: str) -> set[str]:
    """Folded hyphen/space parts of a surname, particles left out unless the
    surname is nothing else ("de la Cruz" -> {cruz}, "Das" -> {das})."""
    parts = [p for p in (fold_name(w) for w in re.split(rf"[\s{_HYPHENS}]+", name)) if len(p) > 1]
    named = {p for p in parts if p not in _PARTICLES}
    return named or set(parts)


@dataclass(frozen=True)
class Author:
    """One author of a citation: the surname as written, and the first
    initial (upper case, '' when the citation gives none)."""
    surname: str
    initial: str


@dataclass(frozen=True)
class OwnerAlias:
    """The owner's surname spellings in this bibliography, most frequent
    first, the initials an alias match must carry, and every folded name part
    of those spellings (for widening a half-matched compound)."""
    surnames: tuple[str, ...] = ()
    initials: frozenset[str] = frozenset()
    parts: frozenset[str] = field(default_factory=frozenset)


def author_segment(citation: str) -> str:
    """The citation's author list: from after any leading number to the title."""
    text = _LEADING_NUMBER.sub("", citation)
    end = _AUTHOR_SEGMENT_END.search(text)
    return text[:end.start()] if end else text


def _is_initials(word: str) -> bool:
    return bool(_INITIALS.match(word))


def _author_words(token: str) -> list[str] | None:
    """The name words of one author token, or None for a title fragment."""
    words = _MARKERS.sub("", token).split()
    if not words or len(words) > _MAX_AUTHOR_WORDS:
        return None
    if any(w[0].islower() and w.lower().strip(".") not in _PARTICLES for w in words):
        return None
    return words


def _split_author(words: list[str]) -> Author | None:
    """Surname and initial of an author written "Garza Ruiz M", "M. E.
    Wende", "Ana Garza Ruiz" or "Michael E. Wende"."""
    flags = [_is_initials(w) for w in words]
    if all(flags):
        return None
    if flags[-1]:
        cut = len(flags) - flags[::-1].index(False)
        return Author(" ".join(words[:cut]), words[cut][0])
    if flags[0]:
        return Author(" ".join(w for w in words if not _is_initials(w)), words[0][0])
    if len(words) == 1:
        return Author(words[0].strip("."), '')
    last_initial = max((i for i, f in enumerate(flags) if f), default=0)
    return Author(" ".join(words[last_initial + 1:]), words[0][0].upper())


def parse_authors(citation: str) -> list[Author]:
    """The authors of a citation. A comma form ("Wende, M. E.") puts the
    initials in the next token, which is folded back into the surname."""
    tokens = [_author_words(t) for t in _AUTHOR_SEPARATOR.split(author_segment(citation))]
    authors: list[Author] = []
    for i, words in enumerate(tokens):
        if words is None or all(_is_initials(w) for w in words):
            continue
        following = tokens[i + 1] if i + 1 < len(tokens) else None
        if following and all(_is_initials(w) for w in following) \
                and not any(_is_initials(w) for w in words):
            author = Author(" ".join(words), following[0][0])
        else:
            author = _split_author(words)
        if author and len(fold_name(author.surname)) > 1:
            authors.append(author)
    return authors


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


def _keys(name: str) -> set[str]:
    """A surname folded whole, plus its parts."""
    whole = fold_name(name)
    return ({whole} if len(whole) > 1 else set()) | name_parts(name)


def _owner_initials(given_initials: Iterable[str], surnames: Sequence[str],
                    per_citation: list[list[Author]]) -> frozenset[str]:
    """The owner's first initials: the given ones (stage-4 first name,
    target_name initials); else those of authors spelled exactly like a
    stage-4 surname; else, for a stage-4 surname no author carries (a given
    name stored as the surname), its own first letter."""
    initials = frozenset(i.upper() for i in given_initials if i)
    if initials:
        return initials
    wanted = {fold_name(s) for s in surnames} - {''}
    seen = {a.initial for authors in per_citation for a in authors
            if a.initial and fold_name(a.surname) in wanted}
    if seen:
        return frozenset(seen)
    carried = {fold_name(a.surname) for authors in per_citation for a in authors}
    return frozenset(fold_name(s)[0].upper() for s in surnames if fold_name(s)
                     and fold_name(s) not in carried)


def _related(tfidf: _TfIdf, candidate: str, owner_keys: set[str]) -> bool:
    """A shared folded part, or a close spelling with the same first letter.
    A different first letter is never a fuzzy match: that gate keeps
    Henderson off an Anderson CV."""
    cand_keys = _keys(candidate)
    if cand_keys & owner_keys:
        return True
    return any(c[0] == o[0] and tfidf.similarity(c, o) >= MIN_SIMILARITY
               for c in cand_keys for o in owner_keys)


@dataclass
class _Candidates:
    """Initial-compatible author surnames: citations covered per folded key,
    and the most frequent spelling of each."""
    coverage: Counter[str]
    spelling: dict[str, str]

    @classmethod
    def count(cls, per_citation: list[list[Author]], initials: frozenset[str]) -> _Candidates:
        coverage: Counter[str] = Counter()
        spellings: dict[str, Counter[str]] = {}
        for authors in per_citation:
            fitting = [a.surname for a in authors if a.initial in initials]
            for surname in fitting:
                spellings.setdefault(fold_name(surname), Counter())[surname] += 1
            coverage.update({fold_name(s) for s in fitting})
        spelling = {k: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                    for k, c in spellings.items()}
        return cls(coverage, spelling)

    def ranked(self, needed: int) -> list[str]:
        """Folded keys reaching `needed` citations, most covered first."""
        return sorted((k for k, n in self.coverage.items() if n >= needed),
                      key=lambda k: (-self.coverage[k], k))


def _accept(tfidf: _TfIdf, ranked: list[str], spelling: dict[str, str],
            owner_keys: set[str]) -> list[str]:
    """Keys related to a stage-4 surname, then, until nothing changes, to an
    accepted candidate by a shared part."""
    accepted = [k for k in ranked if _related(tfidf, spelling[k], owner_keys)]
    grown = True
    while grown:
        parts = set().union(*(_keys(spelling[k]) for k in accepted))
        extra = [k for k in ranked if k not in accepted and _keys(spelling[k]) & parts]
        accepted += extra
        grown = bool(extra)
    return sorted(accepted, key=ranked.index)


def _stage4_share(per_citation: list[list[Author]], surnames: Sequence[str]) -> float:
    """Share of citations with an author whose surname, or a part of it, is
    a stage-4 surname."""
    wanted = {fold_name(s) for s in surnames} - {''}
    hits = sum(1 for authors in per_citation
               if any(_keys(a.surname) & wanted for a in authors))
    return hits / len(per_citation)


def infer_owner_alias(citations: Sequence[str], surnames: Iterable[str],
                      given_initials: Iterable[str] = ()) -> OwnerAlias:
    """The owner's spellings in this bibliography (see the module docstring).

    `surnames` are stage 4's (`cv_owner.last_name`, each `target_name`
    surname); `given_initials` the owner's first initials stage 4 knows
    (first name, full name, `target_name` initials). Returns an empty
    `OwnerAlias` when nothing qualifies.
    """
    stage4 = [s for s in surnames if s and s.strip()]
    per_citation = [parse_authors(c) for c in citations]
    initials = _owner_initials(given_initials, stage4, per_citation)
    if not per_citation or not initials:
        return OwnerAlias()
    candidates = _Candidates.count(per_citation, initials)
    needed = max(MIN_ALIAS_CITATIONS, math.ceil(MIN_COVERAGE * len(per_citation)))
    ranked = candidates.ranked(needed)
    tfidf = _TfIdf(fold_name(a.surname) for authors in per_citation for a in authors)
    owner_keys = set().union(*(_keys(s) for s in stage4))
    accepted = _accept(tfidf, ranked, candidates.spelling, owner_keys)
    if not accepted and ranked and _stage4_share(per_citation, stage4) < MAX_STAGE4_SHARE \
            and candidates.coverage[ranked[0]] >= MIN_REPLACEMENT * len(per_citation):
        accepted = _accept(tfidf, ranked, candidates.spelling, _keys(candidates.spelling[ranked[0]]))
    spellings = tuple(candidates.spelling[k] for k in accepted)
    return OwnerAlias(spellings, initials, frozenset().union(*(name_parts(s) for s in spellings)))
