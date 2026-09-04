#!/usr/bin/env python3
"""Regenerate the corpus measurements the normalization docstrings assert.

Those modules justify design choices with numbers taken from a run over the
local CV farm -- how many author strings carry a co-first-author mark, how
far two initials definitions disagree, how often the pair parser's
surname-slot branch fires and on what token shapes, how often the taxonomy
shape match would hit real content, whether the PII containment rule changed
any deny decision. Nothing in the repository could reproduce them, so they
were unfalsifiable: a reviewer had to take the number on trust, and a later
change could invalidate one silently.

    python3 scripts/measure_normalization_claims.py <farm-directory>
    python3 scripts/measure_normalization_claims.py <farm-directory> --only pii

The farm directory is REQUIRED and never defaulted. It holds real CVs, so
its path is not a repository constant; every run names it explicitly. This
script only reads -- it opens nothing for writing, creates no file inside
the farm, and prints counts and character-class SHAPES, never a CV-derived
value. The output is meant to be pasted into a pull request, so a token
harvested from a CV must never reach it: every per-token line goes through
`_token_shape`, and `test_measure_normalization_claims.py` fails if a
distinctive token from a synthetic farm shows up in stdout.
"""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.authors import (  # noqa: E402
    _AUTHOR_SUFFIX_RE,
    _INITIALS_TRAILING_MARKS,
    _looks_like_initials,
    _normalize_author_names,
    _parse_surname_initial_pairs,
)
from unified_pipeline.stage6.normalization.pii import (  # noqa: E402
    _PII_FIELD_KEY_RE,
    _from_pii_fragment,
    _pii_fragments,
    _squash,
)
from unified_pipeline.stage6.normalization.taxonomy import (  # noqa: E402
    _TAXONOMY_CODE_PREFIX,
)

# The stages whose stored artifacts carry an `authors` string, i.e. everything
# from field extraction onward. Stage 3b has no extracted fields yet.
_AUTHOR_STAGE_DIRS = (
    "stage_4_field_extraction",
    "stage_5_enrichment",
    "stage_5b_institution_enrichment",
    "stage_5c_teaching_formatted",
    "stage_5d_citation_formatted",
)

# Every stage whose output a leaked taxonomy code could still be sitting in.
_TAXONOMY_STAGE_DIRS = ("stage_3b_classified_entries",) + _AUTHOR_STAGE_DIRS

# The marks that are stripped OFF an initials group rather than read as part
# of it, minus the abbreviating period, which is not an authorship marker.
_AUTHORSHIP_MARKS = _INITIALS_TRAILING_MARKS.replace(".", "")

# The initials test `_parse_author_fallback` carried before the two paths were
# unified: an ASCII-only 1-3 uppercase-letter regex, plus a case-blind
# "2 characters or fewer and isupper()". Kept here, and only here, so the
# disagreement the authors.py docstring cites stays reproducible.
_LEGACY_INITIALS_GROUP_RE = re.compile(r"^[A-Z]{1,3}\.?$")

# A value opening with a bracketed single token -- the only shape
# `_TAXONOMY_CODE_PREFIX` could ever strip.
_LEADING_BRACKET_TOKEN_RE = re.compile(r"^\s*\[([^\]\s]+)\]")


def _legacy_looks_like_initials(token: str) -> bool:
    """The pre-unification fallback's own definition of an initials token."""
    return bool(_LEGACY_INITIALS_GROUP_RE.match(token)) or (
        len(token) <= 2 and token.isupper()
    )


def _token_shape(token: str) -> str:
    """One token's character classes -- "Editor" -> "Aaaaaa", "D1" -> "A9".

    THE redaction boundary of this script. Every per-token line in the
    output is a shape, never the token: uppercase letter -> "A", lowercase
    letter -> "a", digit -> "9", anything else -> ".". A shape carries the
    length and the case pattern a docstring needs to describe a corpus
    finding, and carries no letter, digit or punctuation mark a reader could
    trace back to a person or a document. The script's output is written to
    be pasted into a pull request; a harvested token must not travel with it.
    """
    classes = []
    for char in str(token):
        if char.isdigit():
            classes.append("9")
        elif char.isupper():
            classes.append("A")
        elif char.isalpha():
            classes.append("a")
        else:
            classes.append(".")
    return "".join(classes)


def _shape_histogram(tokens) -> str:
    """`{shape: count}`, sorted, for a printable one-line token summary."""
    return str(dict(sorted(Counter(_token_shape(t) for t in tokens).items())))


def _json_files(farm: Path, stage_dirs) -> list[Path]:
    """Every stored stage artifact under `farm`, in a stable order."""
    files: list[Path] = []
    for name in stage_dirs:
        stage = farm / name
        if stage.is_dir():
            files.extend(sorted(stage.glob("*.json")))
    return files


def _load(path: Path):
    """One artifact, or None when it is unreadable -- reported, never fatal."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"  ! unreadable, skipped: {path.name} ({exc})")
        return None


def _walk_strings(node, out: list[str]) -> None:
    """Every string value anywhere in one artifact."""
    if isinstance(node, dict):
        for value in node.values():
            _walk_strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_strings(value, out)
    elif isinstance(node, str):
        out.append(node)


def _walk_authors(node, out: set) -> None:
    """Every non-blank `authors` string anywhere in one artifact."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "authors" and isinstance(value, str) and value.strip():
                out.add(value)
            _walk_authors(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_authors(value, out)


def _distinct_author_strings(farm: Path) -> set:
    """The farm's distinct `authors` values, across every stage that has them."""
    found: set = set()
    for path in _json_files(farm, _AUTHOR_STAGE_DIRS):
        data = _load(path)
        if data is not None:
            _walk_authors(data, found)
    return found


def measure_initials_trailing_marks(farm: Path) -> None:
    """authors.py `_INITIALS_TRAILING_MARKS`: how many strings carry a mark.

    A string counts when one of its comma-separated tokens ends in an
    authorship marker AND is an initials group once that marker is stripped
    -- i.e. exactly the tokens the constant exists to keep classified as
    initials.
    """
    strings = _distinct_author_strings(farm)
    marked = [
        s for s in strings
        if any(t.strip().endswith(tuple(_AUTHORSHIP_MARKS))
               and _looks_like_initials(t.strip())
               for t in s.split(","))
    ]
    print("initials_trailing_marks")
    print(f"  distinct author strings          : {len(strings)}")
    print(f"  carrying an authorship marker    : {len(marked)}")


def measure_initials_rule_disagreement(farm: Path) -> None:
    """authors.py `_looks_like_initials`: how far the two definitions differ.

    Counted over comma-split tokens, which is where both definitions are
    applied: a string is a disagreement when any one of its tokens is
    initials to one definition and a name to the other.
    """
    strings = _distinct_author_strings(farm)
    differing = [
        s for s in strings
        if any(_looks_like_initials(t.strip())
               != _legacy_looks_like_initials(t.strip())
               for t in s.split(",") if t.strip())
    ]
    print("initials_rule_disagreement")
    print(f"  distinct author strings          : {len(strings)}")
    print(f"  classified differently by the two: {len(differing)}")


class _BranchRecorder(logging.Handler):
    """Reads the parser branch off `_normalize_author_names`' own debug line.

    The dispatcher is not exposed as a function of its own, and re-deciding
    "pairs or fallback?" here would be exactly the unverifiable second
    implementation this script exists to retire. The round-2 debug line is
    structural -- branch name and token counts, never the author string --
    so reading it leaks nothing.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.branch = None
        self.tokens_in = None

    def emit(self, record):
        match = re.search(r"branch=(\w+) tokens_in=(\d+)", record.getMessage())
        if match:
            self.branch = match.group(1)
            self.tokens_in = int(match.group(2))


def _dispatched_branch(authors: str) -> tuple[str | None, int | None]:
    """Which parser the real dispatcher sends `authors` to, and its token count."""
    logger = logging.getLogger("unified_pipeline.stage6.normalization.authors")
    recorder = _BranchRecorder()
    previous_level = logger.level
    logger.addHandler(recorder)
    logger.setLevel(logging.DEBUG)
    try:
        _normalize_author_names(authors)
    finally:
        logger.removeHandler(recorder)
        logger.setLevel(previous_level)
    return recorder.branch, recorder.tokens_in


def _pair_parser_parts(authors: str) -> list[str] | None:
    """The token list `_normalize_author_names` would hand the PAIR parser.

    None when the string takes the fallback instead. Mirrors the caller's
    input cleanup and its pair detector; every string is cross-checked
    against `_dispatched_branch`, so a drift is reported as a disagreement
    count rather than quietly changing the number.
    """
    cleaned = re.sub(r",{2,}", ",", authors).rstrip(".,;")
    cleaned = re.sub(r"\s*&\s*", ", ", cleaned)
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    for i in range(1, len(parts), 2):
        part = parts[i]
        if _AUTHOR_SUFFIX_RE.match(part.rstrip(".")):
            continue
        if not _looks_like_initials(part):
            return None
    return parts


def _surname_slot_orphans(parts: list[str]) -> tuple[list[str], list[str], list[int]]:
    """`(elements, orphan tokens, consecutive-run lengths)` for one token list.

    A line-for-line mirror of `_parse_surname_initial_pairs`, adding only a
    record of which tokens the surname-slot branch places and how many of
    them arrive back to back. The caller asserts `elements` against the real
    parser's output on every string, so the mirror cannot drift unnoticed.
    """
    elements: list[str] = []
    orphans: list[str] = []
    runs: list[int] = []
    merge_target_open = False
    run = 0
    i = 0
    while i < len(parts):
        surname = parts[i].strip().rstrip(".,")

        if i + 1 >= len(parts):
            if surname:
                elements.append(surname)
            i += 1
            continue

        initials = parts[i + 1].strip().rstrip(".,")

        if _AUTHOR_SUFFIX_RE.match(initials.rstrip(".")):
            elements.append(f"{surname} {initials.rstrip('.')}")
            merge_target_open = False
            if run:
                runs.append(run)
                run = 0
            i += 2
            continue

        if _looks_like_initials(surname):
            orphans.append(surname)
            run += 1
            if merge_target_open:
                elements[-1] = f"{elements[-1]} {surname}"
                merge_target_open = False
            else:
                elements.append(surname)
                merge_target_open = True
            i += 1
            continue

        initials_normalized = initials.replace(" ", "")
        if _looks_like_initials(initials_normalized):
            initials_normalized = initials_normalized.upper()
        elements.append(f"{surname} {initials_normalized}")
        merge_target_open = False
        if run:
            runs.append(run)
            run = 0
        i += 2

    if run:
        runs.append(run)
    return elements, orphans, runs


def measure_pair_parser_orphans(farm: Path) -> None:
    """authors.py `_parse_surname_initial_pairs`: the surname-slot orphan.

    An initials group standing where a surname should be has no pair to make.
    It used to be skipped, which deleted it outright; it is placed now. This
    counts how often that branch actually fires on the farm, the SHAPE of the
    tokens it places, and how many arrive consecutively -- a run of two or
    more is the only input on which the pair parser's coalescing differs from
    `_parse_author_fallback`, which never coalesces.
    """
    strings = _distinct_author_strings(farm)
    reaching = orphan_strings = disagreements = 0
    orphan_tokens: list[str] = []
    runs: Counter = Counter()
    for text in sorted(strings):
        parts = _pair_parser_parts(text)
        branch, tokens_in = _dispatched_branch(text)
        if branch != ("pairs" if parts is not None else "fallback"):
            disagreements += 1
            continue
        if parts is None:
            continue
        elements, orphans, string_runs = _surname_slot_orphans(parts)
        if tokens_in != len(parts) or elements != _parse_surname_initial_pairs(parts):
            disagreements += 1
            continue
        reaching += 1
        if orphans:
            orphan_strings += 1
            orphan_tokens.extend(orphans)
            runs.update(string_runs)
    print("pair_parser_orphans")
    print(f"  distinct author strings          : {len(strings)}")
    print(f"  reaching the pair parser         : {reaching}")
    print(f"  strings hitting the surname slot : {orphan_strings}")
    print(f"  tokens placed by that branch     : {len(orphan_tokens)}")
    print(f"  those tokens, by shape           : {_shape_histogram(orphan_tokens)}")
    print(f"  consecutive-orphan runs, by len  : {dict(sorted(runs.items()))}")
    print(f"  mirror/dispatcher disagreements  : {disagreements}")


def measure_taxonomy_shape(farm: Path) -> None:
    """taxonomy.py `_TAXONOMY_CODE_PREFIX`: what the shape match would hit.

    The cost of matching a shape rather than an allowlist of real codes is
    that a bracketed non-code ("[R01]") is stripped too. Counted over every
    string value the stored artifacts hold. A leading bracket containing
    WHITESPACE is not a candidate at all -- the prefix cannot match one, and
    stage 3b's reasoning prose opens with bracketed sentences in bulk -- so
    the population is values opening with a bracketed single token.
    """
    files = _json_files(farm, _TAXONOMY_STAGE_DIRS)
    values = 0
    bracketed: dict[str, int] = {}
    matched = 0
    for path in files:
        data = _load(path)
        if data is None:
            continue
        strings: list[str] = []
        _walk_strings(data, strings)
        values += len(strings)
        for text in strings:
            token = _LEADING_BRACKET_TOKEN_RE.match(text)
            if not token:
                continue
            bracketed[token.group(1)] = bracketed.get(token.group(1), 0) + 1
            if _TAXONOMY_CODE_PREFIX.match(text):
                matched += 1
    print("taxonomy_shape")
    print(f"  artifacts read                   : {len(files)}")
    print(f"  string values                    : {values}")
    print(f"  values opening with a [token]    : {sum(bracketed.values())}")
    print(f"  distinct [token]s                : {len(bracketed)}")
    print(f"  those tokens, by shape           : {_shape_histogram(bracketed)}")
    print(f"  matching _TAXONOMY_CODE_PREFIX   : {matched}")


def _entry_deny_decisions(entry: dict) -> tuple[int, int, int]:
    """Deny decisions for one A entry: (made, denied, differing from base).

    Mirrors `sections/personal_data.py`: the three extracted values it
    passes through the predicate, checked only when the entry carries a PII
    fragment at all. "Base" is the bare-substring containment test this
    predicate replaced.
    """
    fragments = _pii_fragments(entry.get("text", ""))
    if not fragments:
        return 0, 0, 0

    fields = entry.get("extracted_fields", {}) or {}
    values = [
        fields.get("phone"),
        fields.get("address"),
        (fields.get("email") or fields.get("primary_email")
         or fields.get("institutional_email") or fields.get("work_email")
         or fields.get("personal_email")),
    ]
    made = denied = differing = 0
    for value in values:
        made += 1
        now = _from_pii_fragment(value, fragments)
        squashed = _squash(value)
        before = bool(squashed) and any(squashed in _squash(f) for f in fragments)
        denied += int(now)
        differing += int(now != before)
    return made, denied, differing


def measure_pii_deny_decisions(farm: Path) -> None:
    """pii.py `_from_pii_fragment`: deny decisions, and which ones moved.

    Read the "entries carrying a PII fragment" line first. When it is 0 the
    three lines under it are 0 by construction and say nothing about the
    predicate -- the farm's stage-4 artifacts simply hold no protected-data
    label for it to act on, and the motivating cases live in the tests.

    The last two lines widen that check past the A code and past the text,
    to every stored stage-4 entry at any taxonomy code and to its extracted
    field KEYS as well. They are what says whether "no protected-data label"
    is a property of the A entries or of the whole farm, which is the
    difference between "this predicate is untested by the corpus" and "the
    corpus cannot see protected data at all".
    """
    files = _json_files(farm, ("stage_4_field_extraction",))
    entries = with_fragment = made = denied = differing = 0
    all_entries = all_labelled = 0
    for path in files:
        data = _load(path)
        if data is None:
            continue
        for entry in data.get("entries", []) or []:
            all_entries += 1
            fields = entry.get("extracted_fields") or {}
            if (_pii_fragments(entry.get("text", ""))
                    or any(_PII_FIELD_KEY_RE.match(k) for k in fields)):
                all_labelled += 1
            if entry.get("taxonomy_code") != "A":
                continue
            entries += 1
            entry_made, entry_denied, entry_differing = _entry_deny_decisions(entry)
            with_fragment += int(bool(entry_made))
            made += entry_made
            denied += entry_denied
            differing += entry_differing
    print("pii_deny_decisions")
    print(f"  stage-4 artifacts read           : {len(files)}")
    print(f"  A-coded entries                  : {entries}")
    print(f"  entries carrying a PII fragment  : {with_fragment}")
    print(f"  deny decisions made              : {made}")
    print(f"  values denied                    : {denied}")
    print(f"  differing from bare containment  : {differing}")
    print(f"  stage-4 entries at any code      : {all_entries}")
    print(f"  ... with a PII label or field key: {all_labelled}")


_MEASURES = {
    "marks": measure_initials_trailing_marks,
    "initials": measure_initials_rule_disagreement,
    "orphans": measure_pair_parser_orphans,
    "taxonomy": measure_taxonomy_shape,
    "pii": measure_pii_deny_decisions,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "farm",
        help="directory holding the stage_* artifact folders (never defaulted)",
    )
    parser.add_argument(
        "--only", choices=sorted(_MEASURES), action="append",
        help="run one measurement instead of all five; repeatable",
    )
    args = parser.parse_args(argv)

    farm = Path(args.farm).expanduser()
    if not farm.is_dir():
        parser.error(f"not a directory: {farm}")

    print(f"farm: {farm}")
    for name in (args.only or sorted(_MEASURES)):
        _MEASURES[name](farm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
