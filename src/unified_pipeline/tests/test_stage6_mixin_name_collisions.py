"""Guard against a repeat of the `_add_table_row` mixin collision.

`WCMTemplateGenerator` (`stage_6_word_template.py`) is composed from 23
section mixins, one per file under `stage6/sections/`. Python resolves an
attribute reached through `self.<name>` by walking the MRO left to right and
returning the FIRST match -- it does not care whether two mixins independently
defined the same name for unrelated reasons. If two of them do, the later
mixin's own calls to `self.<name>` silently run the earlier mixin's version
instead of their own.

That is exactly what broke: `ClinicalPracticeSection` (added by #625, commit
aeea7a7) and `MembershipsSection` both defined `_add_table_row`, with
different signatures. `ClinicalPracticeSection` precedes `MembershipsSection`
in the `WCMTemplateGenerator` base list, so every `self._add_table_row(...)`
call inside `memberships.py` resolved to the clinical version and raised
`TypeError: ... got an unexpected keyword argument 'entry'` -- on 52 of 66
corpus CVs, with the full 1287-test pipeline suite green throughout, because
no test in that suite ever calls a memberships-rendering method through the
COMPOSED class; the round-2 fixture tests build a generator but exercise
`ClinicalPracticeSection` methods only.

Each mixin owns its methods; nothing here is a documented shared hook (see
`memberships.py`'s own module docstring: "`_add_table_row` ... lives here
because this is the only section that calls it. Everything else either
writes cells directly or uses one of the shared `_add_table_row_with_*`
variants, which stay on `WCMTemplateGenerator`" -- i.e. sharing goes through
`WCMTemplateGenerator` itself or an explicit `_with_*` suffix, never through
two mixins defining the same bare name). So the rule admits no allowlist: any
name owned by more than one mixin is a bug, full stop, until a real shared
convention exists and is documented as one.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_mixin_name_collisions.py -p no:cacheprovider
"""

import ast
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _mixin_callable_owners():
    """Map each callable name defined directly on a mixin to every mixin
    (by class name) that defines it.

    Walks `WCMTemplateGenerator.__bases__` rather than the full MRO -- the
    bases ARE the 23 section mixins, each inheriting only from `object`, so
    walking `vars()` per base (not per MRO entry) looks only at names each
    mixin defines itself, not names it merely inherits. Dunder names
    (`__init__`, `__repr__`, ...) are excluded: Python's own machinery, not a
    section author's naming choice.
    """
    owners = defaultdict(list)
    for base in WCMTemplateGenerator.__bases__:
        # The docstring's premise, pinned: a mixin that grows a parent would
        # carry inherited names this per-base walk cannot see.
        assert base.__bases__ == (object,), f"{base.__name__} inherits from {base.__bases__}; extend the walk to its MRO"
        for name, value in vars(base).items():
            if name.startswith("__"):
                continue
            if callable(value):
                owners[name].append(base.__name__)
    return owners


def test_no_two_section_mixins_define_the_same_callable_name():
    """The regression test for the `_add_table_row` collision itself.

    Fails the moment a second mixin defines a name another mixin already
    owns -- exactly the shape of the #625 regression, generalized to every
    name instead of pinning `_add_table_row` alone, so the next collision
    (any name, any pair of mixins) is caught here instead of by a 52/66
    corpus render failure.
    """
    owners = _mixin_callable_owners()
    collisions = {name: sorted(defs) for name, defs in owners.items() if len(defs) > 1}
    assert collisions == {}, (
        "two or more section mixins composed into WCMTemplateGenerator define "
        f"the same callable name -- the later mixin in the base list silently "
        f"loses its own method to the earlier one via MRO resolution: "
        f"{collisions}"
    )


def test_the_mixin_surface_is_not_silently_empty():
    """Guard the guard: an import failure or an empty base list would make
    the collision check above vacuously pass.
    """
    owners = _mixin_callable_owners()
    assert len(WCMTemplateGenerator.__bases__) == 23, (
        "WCMTemplateGenerator's mixin count changed -- if intentional, "
        "update this pin"
    )
    assert len(owners) > 0, "no callables found on any section mixin"


#: #563 severity contract for `src/unified_pipeline/stage6/sections/*.py`.
#:
#: Keyed on each `logger.<level>(...)` call's MESSAGE-TEXT LITERAL, not its
#: line number (line cites drift across commits -- COMMON.md "Landmines").
#: AST-derived from the tree below by `_sections_logger_severity_map()`; any
#: mismatch means a call site's level moved, its message was reworded, or a
#: new `logger` call was added without a deliberate entry here. A new
#: `logger.<level>(...)` call added anywhere under `stage6/sections/` must be
#: added to this table on purpose, in the same commit.
#:
#: This dict alone is not the whole contract: two message texts each occur
#: at TWO call sites with the same level (see
#: `SECTIONS_LOGGER_DUPLICATE_TEXT_MULTIPLICITY` below). A dict keyed on
#: text alone is last-write-wins, so a level flip at one of a duplicated
#: pair's two sites is invisible if the OTHER site (same text, unflipped
#: level) is visited later and overwrites it in the map -- verifier r1's
#: m06 found this for `memberships.py`; round 2's m11 found the same hole
#: for `honors.py:1009` / `memberships.py:648` (both "  Skipping header
#: entry: '%s...'", warning) specifically, because `memberships.py` sorts
#: after `honors.py` and its unflipped site clobbered the map entry. The
#: test below therefore compares a `collections.Counter` of every
#: `(text, level)` PAIR -- with multiplicity, no per-text collapsing at
#: all -- against this table plus the multiplicity table, so flipping
#: EITHER site of a duplicated pair changes the Counter and fails.
SECTIONS_LOGGER_SEVERITY_CONTRACT = {
    '\nFilling Personal Data...': 'info',
    '    Merged %s fragmented appointment row(s); propagated dates to %s role row(s)': 'info',
    '    Merged dates into %s fragmented appointment rows': 'info',
    "  %s: %s grants -> '%s'": 'info',
    '  D1 Academic: %s entries': 'info',
    '  D2 Hospital: %s entries': 'info',
    '  D3 Other: %s entries': 'info',
    '  Found address from table: %s...': 'debug',
    '  Found email from address block: %s': 'debug',
    '  Found email from paragraph: %s': 'debug',
    '  Found email from table: %s': 'debug',
    '  Found name from table: %s': 'debug',
    '  Found phone from address block: %s': 'debug',
    '  Found phone from table: %s': 'debug',
    '  Moved %d mentees from Past to Current (end_date=present)': 'info',
    '  No Clinical Innovations table found, inserting as bullet points': 'warning',
    '  No Clinical Leadership table found, inserting as bullet points': 'warning',
    '  No Clinical Practice table found, inserting as bullet points': 'warning',
    '  Passthrough: Could not find Employment Status section': 'warning',
    '  Passthrough: Could not find Hospital Affiliation section': 'warning',
    '  Passthrough: Filling Employment Status (%s entries)': 'info',
    "  Passthrough: Skipping non-affiliation table (first cell: '%s')": 'warning',
    '  Presentations: %s Regional, %s National, %s International': 'info',
    '  Rerouted %s Q2 entries/lines to Journal Reviewing': 'info',
    '  Service on Boards: %s Regional, %s National, %s International': 'info',
    "  Skipping header entry: '%s...'": 'warning',
    "  Skipping sparse grant entry: '%s...'": 'warning',
    '  Skipping table (appears to be grant table, not clinical innovations)': 'warning',
    '  Skipping table (appears to be grant table, not clinical leadership)': 'warning',
    '  Skipping table (appears to be grant table, not clinical practice)': 'warning',
    "  Warning: Could not find 'RESEARCH ACTIVITIES' section": 'warning',
    '  Warning: Could not find memberships table': 'warning',
    "  Warning: Could not find section header '%s'": 'warning',
    '%d merged row(s) matched no employer name; one row had inherited its employer from a parent row': 'info',
    "%s: %d entries -> '%s...'": 'info',
    '%s: position record at element %s has no title, employer or dates; its row would be blank, dropping it': 'warning',
    'Adding Appendix (%d unmapped entries)...': 'info',
    'Appendix: %s': 'info',
    'Assigned no percent effort: the grant title substring-matches %d project names in the effort header': 'debug',
    'Could not add citation as insertion: %s': 'warning',
    'Could not find section header %r': 'warning',
    'Could not read original document for personal data fallback (uid=%s, path=%s): %s': 'warning',
    'Discarded a percent effort figure: NaN': 'debug',
    'Discarded a percent effort figure: not a number': 'debug',
    'Discarded a percent effort figure: outside the range (0, %s] percent': 'debug',
    'Dropped %d position record(s) with no title, employer or dates': 'info',
    'Employment Status entry %r matched no template row near the section; entry not written': 'warning',
    'Employment Status entry label %r names no known template row; entry not written': 'warning',
    'Extracted %s fields not consumed by the research-support renderer: %s': 'debug',
    'F1: second %s candidate %r (entry text %r) ignored; keeping %r': 'warning',
    'Filling Administrative Activities (%s entries)...': 'info',
    'Filling Bibliography (%d publications)...': 'info',
    'Filling Board Certification (%s entries)...': 'info',
    'Filling Clinical Practice (%s L1, %s L2, %s L3)...': 'info',
    'Filling Education (%s entries)...': 'info',
    'Filling Honors (%s entries)...': 'info',
    'Filling Institutional Leadership (%s entries)...': 'info',
    'Filling Invited Presentations (%s entries)...': 'info',
    'Filling Licensure (%s entries)...': 'info',
    'Filling Memberships (%s entries)...': 'info',
    'Filling Mentoring (%d mentees, %d summary/outcome lines)...': 'info',
    'Filling Other Educational Experiences (%d entries)...': 'info',
    'Filling Patents & Inventions (%d entries)...': 'info',
    'Filling Positions (%s entries)...': 'info',
    'Filling Postdoctoral Training (%d entries)...': 'info',
    'Filling Research Summary (%s words, %s)...': 'info',
    'Filling Research Support (%s grants)...': 'info',
    'Filling Researcher Profiles (%s entries)...': 'info',
    'Filling Service Activities (%s entries)...': 'info',
    'Filling Teaching (%d entries)...': 'info',
    "Honors table has %d column(s), not the template's %d; folding the granting body into the award cell for this document's honors rows.": 'warning',
    'Journal Reviewing: section not found in template; %d entries not rendered': 'warning',
    'Journal Reviewing: table not found in template; %d entries not rendered': 'warning',
    'L3 bullet already carries every fragment after the role': 'info',
    'Mentoring: %s heading not found in template; %d outcome lines not rendered': 'warning',
    "Mentoring: '%s' heading not found in template; %d entries not rendered": 'warning',
    "Mentoring: '%s' heading not found; %d entries rendered under MENTORING instead": 'warning',
    "Mentoring: none of '%s', '%s' or %s found in template; %d entries not rendered": 'warning',
    'Other Educational Experiences: expected a %d-column table, found %d columns; %d entries not rendered': 'warning',
    'Other Educational Experiences: section heading not found in template; %d entries not rendered': 'warning',
    'Other Educational Experiences: table not found after section heading; %d entries not rendered': 'warning',
    'Patents & Inventions: removed %d previously rendered patent tables before rendering again': 'info',
    'Patents & Inventions: section heading not found in template; %d entries not rendered': 'warning',
    'Patents & Inventions: skipped %d sparse entries (no title or patent number)': 'info',
    'Patents & Inventions: table reposition failed for entry %d of %d; table left at document end instead of under the section heading': 'warning',
    'Percent Effort entry activity %r maps to %r, which has no row in the template table; entry not written': 'warning',
    'Percent Effort entry has an extra cell; entry not written: %r': 'warning',
    'Percent Effort entry spans multiple rows; entry not written: %r': 'warning',
    'Percent Effort: %s already written as %r; conflicting entry %r not written': 'warning',
    'Percent Effort: could not find a valid table after the section header; entries not written': 'warning',
    'Percent Effort: could not find the section header in the template; entries not written': 'warning',
    'Postdoctoral Training: section heading not found in template; %d entries not rendered': 'warning',
    'Postdoctoral Training: table not found after section heading; %d entries not rendered': 'warning',
    'Propagated institution to %d sub-entries': 'info',
    'Service on Boards: no %s or National table found; %d %s entries not rendered': 'warning',
    'Service on Boards: no Regional/National/International table found in template; %d entries not rendered': 'warning',
    'Service on Boards: section not found in template; %d entries not rendered': 'warning',
    'Skipping Research Summary section (no Stage 4.5 output)': 'warning',
    'Skipping Research Summary section (no substantive content)': 'warning',
    'Skipping education entry %d: expected a mapping, got %s': 'warning',
    "Skipping position header entry: '%s...'": 'info',
    'Stopped institution propagation at %d source-structure boundaries': 'info',
    'board certification reconstruction: %d specialty token(s), %d certificate number token(s), %d year token(s) -- counts disagree, pairing positionally with low confidence rather than dropping data': 'warning',
    'board certification table has %d column(s), too narrow to render a row (specialty=%r, cert_number=%r, dates=%r) -- skipping': 'warning',
    "board certification: %d of %d reparsed row(s) are missing a certificate number or date that the entry's structured fields cannot be attributed to a single row -- leaving blank rather than guessing which row it belongs to (certificate_number=%r, year_certified=%r)": 'warning',
    "board certification: %s -- rendering the entry's already-extracted fields instead of losing them (certifying_board=%r, certificate_number=%r)": 'warning',
    'board certification: entry has neither text nor structured fields -- nothing to render': 'debug',
    'board certification: rejected token %r -- matches no known shape (year, certificate number, MOC, or specialty)': 'debug',
    'board certification: skipping blank row -- specialty, certificate number, and dates were all empty': 'warning',
    'memberships: entry at %s has neither an organization nor a date; no row rendered': 'warning',
    "memberships: entry at %s has no 'organization' field; the organization was recovered from its raw text": 'warning',
    'memberships: recovering an organization from raw text would have dropped part of the entry; rendering the raw text instead': 'warning',
    'no template heading for teaching code %s; %d entries not rendered': 'warning',
    'section %r resolved to a table already filled by another code; appending instead of clearing to avoid destroying it': 'warning',
    'teaching entry produced no renderable line (%s): %r': 'warning',
}

#: One call site in `research_support.py`'s `_print_verbose` logs a
#: caller-built string (not a literal in the call), so it cannot be keyed by
#: message text; it is keyed by (file, enclosing function) instead, which is
#: exactly as stable against line drift.
SECTIONS_LOGGER_NON_LITERAL_SEVERITY_CONTRACT = {
    ("research_support.py", "_print_verbose"): "debug",
}

#: Explicit multiplicity for the two message texts above that occur at TWO
#: call sites apiece (both sites already share a level, or they would show
#: up in `text_to_levels` below as an inconsistency): `"  Skipping header
#: entry: '%s...'"` (honors.py:1009, memberships.py:648) and `"Mentoring:
#: '%s' heading not found; %d entries rendered under MENTORING instead"`
#: (mentoring.py, two guard clauses, pre-existing). Any text not listed
#: here is assumed to occur exactly once. Still keyed on message text, not
#: on line numbers or a running index.
SECTIONS_LOGGER_DUPLICATE_TEXT_MULTIPLICITY = {
    "  Skipping header entry: '%s...'": 2,
    "Mentoring: '%s' heading not found; %d entries rendered under MENTORING instead": 2,
}

# Every level-ish method `logging.Logger` exposes, not just the five this
# package uses today: a site added as `logger.critical(...)` or the
# deprecated `logger.warn(...)` must be SEEN by the census so the count
# guard below rejects it, rather than being invisible and silently
# leaving the contract short by one.
_LOGGER_LEVELS = {"debug", "info", "warning", "warn", "error",
                  "exception", "critical", "fatal", "log"}


def _sections_logger_severity_map():
    """AST-walk every `logger.<level>(...)` call under `stage6/sections/`.

    Returns (text_level_records, by_file_and_function): the first is a
    LIST (not a dict) of every literal call site's (message text, level)
    pair, in AST-visit order, with NO deduplication -- a text used at two
    call sites appears twice, so `Counter(text_level_records)` preserves
    real multiplicity instead of collapsing a duplicated text to its
    last-visited site's level (see `SECTIONS_LOGGER_SEVERITY_CONTRACT`'s
    docstring for why a plain dict keyed on text alone missed exactly this
    -- verifier r1 m06 and round-2 m11). The second return value maps
    (filename, enclosing function name) to level for any call whose first
    argument is not a string literal (see the non-literal table above).
    """
    sections_dir = _SRC / "unified_pipeline" / "stage6" / "sections"
    text_level_records: list[tuple[str, str]] = []
    by_file_function: dict[tuple[str, str], str] = {}
    for path in sorted(sections_dir.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        func_stack: list[str] = []

        class _Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):  # noqa: N802
                func_stack.append(node.name)
                self.generic_visit(node)
                func_stack.pop()

            def visit_Call(self, node):  # noqa: N802
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "logger"
                    and node.func.attr in _LOGGER_LEVELS
                ):
                    first = node.args[0] if node.args else None
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        text_level_records.append((first.value, node.func.attr))
                    else:
                        func_name = func_stack[-1] if func_stack else "<module>"
                        by_file_function[(path.name, func_name)] = node.func.attr
                self.generic_visit(node)

        _Visitor().visit(tree)
    return text_level_records, by_file_function


def _expected_severity_counter():
    """Build the expected `Counter` of (text, level) pairs from the two
    checked-in tables: one occurrence per text in
    `SECTIONS_LOGGER_SEVERITY_CONTRACT`, except the texts listed in
    `SECTIONS_LOGGER_DUPLICATE_TEXT_MULTIPLICITY`, which occur that many
    times.
    """
    counter: Counter[tuple[str, str]] = Counter()
    for text, level in SECTIONS_LOGGER_SEVERITY_CONTRACT.items():
        multiplicity = SECTIONS_LOGGER_DUPLICATE_TEXT_MULTIPLICITY.get(text, 1)
        counter[(text, level)] = multiplicity
    return counter


def test_sections_logger_severity_matches_the_563_contract_table():
    """The #563 severity-pinning gate.

    A blanket severity sweep whose levels no test can distinguish is
    vacuous (verifier r1, m06: flipping `memberships.py`'s
    `logger.warning(...)` to `logger.info(...)` passed the entire suite).
    A dict keyed on text alone is also not enough (round-2 m11: two call
    sites share a text -- honors.py:1009 and memberships.py:648 -- and a
    dict is last-write-wins, so flipping the earlier-sorted one is masked
    by the later, unflipped one). This AST-walks every call site under
    `stage6/sections/` as a full list (no dedup) and compares a `Counter`
    of `(text, level)` pairs, WITH multiplicity, against the checked-in
    tables, so flipping the level at ANY of the 117 literal call sites --
    including either half of a duplicated-text pair -- fails here.
    """
    text_level_records, by_file_function = _sections_logger_severity_map()
    actual_counter = Counter(text_level_records)
    expected_counter = _expected_severity_counter()
    assert actual_counter == expected_counter, (
        "a logger call's message text, level, or occurrence count under "
        "stage6/sections/ no longer matches the #563 contract table -- a "
        "level flipped (possibly at one site of a duplicated-text pair), "
        "a message was reworded, or a new call was added without a "
        "deliberate table entry"
    )
    assert by_file_function == SECTIONS_LOGGER_NON_LITERAL_SEVERITY_CONTRACT, (
        "the non-literal logger call site(s) under stage6/sections/ changed "
        "-- update SECTIONS_LOGGER_NON_LITERAL_SEVERITY_CONTRACT deliberately"
    )
    text_to_levels: dict[str, set[str]] = {}
    for text, level in text_level_records:
        text_to_levels.setdefault(text, set()).add(level)
    inconsistent = {text: sorted(levels) for text, levels in text_to_levels.items() if len(levels) > 1}
    assert inconsistent == {}, (
        "the same message text is logged at two different levels at "
        f"different call sites under stage6/sections/: {inconsistent} -- "
        "each text must log at one consistent severity"
    )


def test_sections_logger_severity_contract_is_not_silently_empty():
    """Guard the guard: an empty table would make the contract test above
    vacuously pass. The count is the TRUE number of literal call sites
    (117, with duplicated texts counted once per site, not once per
    distinct text) so a newly added `logger` call in `stage6/sections/`
    fails this guard until it is added to the contract table on purpose.
    """
    text_level_records, by_file_function = _sections_logger_severity_map()
    assert len(text_level_records) == 117, (
        "literal call-site count under stage6/sections/ changed -- update "
        "the table (and SECTIONS_LOGGER_DUPLICATE_TEXT_MULTIPLICITY if a "
        "text now repeats)"
    )
    assert len(by_file_function) == 1



if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
