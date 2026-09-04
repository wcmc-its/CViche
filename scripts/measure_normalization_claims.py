#!/usr/bin/env python3
"""Regenerate the corpus measurements the normalization docstrings assert.

Four of those modules justify a design choice with a number taken from a run
over the local CV farm -- how many author strings carry a co-first-author
mark, how far two initials definitions disagree, how often the taxonomy
shape match would hit real content, whether the PII containment rule changed
any deny decision. Nothing in the repository could reproduce them, so they
were unfalsifiable: a reviewer had to take the number on trust, and a later
change could invalidate one silently.

    python3 scripts/measure_normalization_claims.py <farm-directory>
    python3 scripts/measure_normalization_claims.py <farm-directory> --only pii

The farm directory is REQUIRED and never defaulted. It holds real CVs, so
its path is not a repository constant; every run names it explicitly. This
script only reads -- it opens nothing for writing, creates no file inside
the farm, and prints counts, never a CV-derived value.
"""

import argparse
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.authors import (  # noqa: E402
    _INITIALS_TRAILING_MARKS,
    _looks_like_initials,
)
from unified_pipeline.stage6.normalization.pii import (  # noqa: E402
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
    print(f"  those tokens                     : {sorted(bracketed)}")
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
    """
    files = _json_files(farm, ("stage_4_field_extraction",))
    entries = with_fragment = made = denied = differing = 0
    for path in files:
        data = _load(path)
        if data is None:
            continue
        for entry in data.get("entries", []) or []:
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


_MEASURES = {
    "marks": measure_initials_trailing_marks,
    "initials": measure_initials_rule_disagreement,
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
        help="run one measurement instead of all four; repeatable",
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
