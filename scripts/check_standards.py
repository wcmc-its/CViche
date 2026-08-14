#!/usr/bin/env python3
"""Regenerate CODING_STANDARDS.md's mechanically-checkable §9 rows.

A hand-measured table rots the day after it's written -- every stale count
and stale file:line citation §9 has needed fixing so far was found by a
human re-running the Appendix's own grep commands by hand. This script is
that re-run, automated, for the rows honest enough to automate: presence/
count checks with one unambiguous right answer. Most of §9 is architectural
or narrative judgment (how many drivers exist, whether a fallback records
its own degradation, whether a docstring states an import direction) and
stays hand-written -- see CODING_STANDARDS.md §9's second table and the
build note at its top for which is which and why.

    python3 scripts/check_standards.py            # CI gate: diff against the
                                                    # doc's committed auto block,
                                                    # fail if a ratcheted row rose
    python3 scripts/check_standards.py --report   # print every row's fresh
                                                    # value (plus baseline for
                                                    # ratcheted rows), exit 0 always
    python3 scripts/check_standards.py --update   # rewrite the doc's auto block
                                                    # and standards-baseline.json
                                                    # in place (never raises a
                                                    # baseline -- see check_function_size.py)

Five of the nine rows are a snapshot, not a debt budget -- a row going up is
information, not a failure to refuse, and --update always writes the fresh
number either direction. Four rows (2.1, 3.7's dynamic-attribute row, 5.4,
7.1) are ratcheted instead, the same way check_function_size.py ratchets
3.x: scripts/standards-baseline.json holds one number per row, --update
refuses to write one higher than what's on disk, and the bare command fails
if a fresh count exceeds its baseline. See RATCHETED_ROWS below and
CODING_STANDARDS.md's "Closing the loop" section for which rows qualify.

ponytail: each check re-walks its own subtree rather than one shared
whole-repo AST pass -- these run once in CI or by hand, not in a hot loop,
and a shared cache would only save the few hundred ms this already costs.
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
DOC = os.path.join(ROOT, "docs", "CODING_STANDARDS.md")
CHECK_FUNCTION_SIZE = os.path.join(SCRIPTS, "check_function_size.py")
BASELINE = os.path.join(SCRIPTS, "standards-baseline.json")
SKIP_DIRS = {".git", "__pycache__", "node_modules", "outputs", "uploads", ".venv", "venv", "archive"}

BEGIN_MARKER = "<!-- check_standards:auto:begin -->"
END_MARKER = "<!-- check_standards:auto:end -->"

# Rows in ROWS below whose target is "this count never rises," not "this
# count is already zero" -- ratcheted against BASELINE the same way §3.2a
# ratchets oversized-function debt. Which four rows qualify is a judgment
# call made once in CODING_STANDARDS.md's "Closing the loop" section, not
# re-derived here -- adding a row is a real decision, not a mechanical one.
RATCHETED_ROWS = {
    "2.1 no `db.query(` in `api/`",
    "3.7 dynamic attribute access (non-literal)",
    "5.4 bare swallows (`except Exception: pass`)",
    "7.1 stdout-parsing regexes (`PROGRESS_PATTERNS`)",
}

# §9's hand-narrated table cites the `origin/dev` sha it was last measured
# against in its own prose (one definition, per §1.5, not a second field
# that could disagree with it). Past this many commits behind HEAD it's
# worth a human re-read (warn); past this many, treat it as failed rather
# than trust a table that's almost certainly stale (§5.10's recourse
# ladder). 144 commits was enough to drift the last time this was measured
# by hand -- these thresholds are a guess at "notice early" and "stop
# trusting it," not a measured bound; tighten if either proves wrong.
STALE_WARN_COMMITS = 50
STALE_FAIL_COMMITS = 200


def iter_py_files(root, include_tests=False):
    """Yield (relpath-from-ROOT, ast.Module) for every non-test .py file
    under root. Syntax errors are skipped, same policy as
    check_function_size.py: an unparseable file isn't importable anyway,
    so it isn't this gate's problem either."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            if not include_tests and (name.startswith("test_") or "tests" in dirpath.split(os.sep)):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
                tree = ast.parse(source)
            except (SyntaxError, ValueError):
                continue
            yield os.path.relpath(path, ROOT), tree


def _imported_names(tree):
    """Every dotted module name this file imports, e.g. {'docx', 'app.config_loader'}."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def check_pure_no_docx():
    """§1.2 -- stage6's pure packages import no docx."""
    root = os.path.join(ROOT, "src", "unified_pipeline", "stage6")
    pure = {"parsing", "normalization", "resolution", "sorting"}
    hits = []
    for relpath, tree in iter_py_files(root):
        parts = relpath.split(os.sep)
        if not any(p in pure for p in parts):
            continue
        if any(n == "docx" or n.startswith("docx.") for n in _imported_names(tree)):
            hits.append(relpath)
    return len(hits), hits


def check_sections_no_peers():
    """§1.3 -- stage6/sections/* modules import shared helpers, never each other."""
    root = os.path.join(ROOT, "src", "unified_pipeline", "stage6", "sections")
    if not os.path.isdir(root):
        return 0, []
    siblings = {
        f[:-3] for f in os.listdir(root)
        if f.endswith(".py") and f != "__init__.py"
    }
    hits = []
    for relpath, tree in iter_py_files(root):
        if os.path.basename(relpath) == "__init__.py":
            continue  # the package aggregating its own children isn't a peer import
        stem = os.path.basename(relpath)[:-3]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                tail = node.module.rsplit(".", 1)[-1]
                if tail in siblings and tail != stem:
                    hits.append(f"{relpath}: imports sibling '{tail}'")
    return len(hits), hits


def check_core_no_web_backend():
    """§1.4 -- src/unified_pipeline/ does not import the web backend."""
    root = os.path.join(ROOT, "src", "unified_pipeline")
    hits = []
    for relpath, tree in iter_py_files(root):
        for name in _imported_names(tree):
            if name == "app" or name.startswith("app.") or name == "web_interface" or name.startswith("web_interface."):
                hits.append(f"{relpath}: imports '{name}'")
    return len(hits), hits


def check_no_db_query_in_api():
    """§2.1 -- api/ modules call services/, not db.query( directly.

    Textual, same as the doc's own Appendix command: db.query( is a call on
    whatever the session variable happens to be named, not a fixed dotted
    import path, so there is no more precise AST shape to key on without
    guessing the session's variable name.
    """
    root = os.path.join(ROOT, "web_interface", "backend", "app", "api")
    if not os.path.isdir(root):
        return 0, []
    count = 0
    for name in os.listdir(root):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
            count += len(re.findall(r"db\.query\(", fh.read()))
    return count, []


def check_function_size_excess():
    """§3.x -- reuse check_function_size.py's own --report output (the CLI
    contract, not its internals -- see the module docstring)."""
    r = subprocess.run(
        [sys.executable, CHECK_FUNCTION_SIZE, "--report"],
        capture_output=True, text=True, cwd=ROOT,
    )
    m = re.search(r"excess (\d+)", r.stdout)
    return (int(m.group(1)) if m else -1), []


def check_no_metaprogramming():
    """§3.7 -- no metaclasses, eval/exec, dynamic class creation, or
    __getattr__/__setattr__ overrides."""
    hits = []
    for relpath, tree in iter_py_files(os.path.join(ROOT, "src")):
        hits.extend(_metaprogramming_hits(relpath, tree))
    for relpath, tree in iter_py_files(os.path.join(ROOT, "web_interface")):
        hits.extend(_metaprogramming_hits(relpath, tree))
    return len(hits), hits


def _metaprogramming_hits(relpath, tree):
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if any(kw.arg == "metaclass" for kw in node.keywords):
                hits.append(f"{relpath}:{node.lineno}: metaclass= on {node.name}")
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in ("__getattr__", "__setattr__"):
                    hits.append(f"{relpath}:{item.lineno}: {item.name} on {node.name}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            hits.append(f"{relpath}:{node.lineno}: {node.func.id}(")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "type" and len(node.args) == 3:
            hits.append(f"{relpath}:{node.lineno}: type(name, bases, dict) call")
    return hits


_ATTR_FUNCS = {"getattr", "setattr", "hasattr", "delattr"}


def check_dynamic_attribute_access():
    """§3.7 -- two sub-patterns the rule names together: getattr/setattr/
    hasattr/delattr whose attribute name is a variable, not a literal (the
    doc's own flagship candidate for an AST scan -- its Appendix footnote
    said the grep overcounts), and `__dict__` passed around as a payload."""
    hits = []
    for base in (os.path.join(ROOT, "src"), os.path.join(ROOT, "web_interface")):
        for relpath, tree in iter_py_files(base):
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and node.attr == "__dict__"):
                    hits.append(f"{relpath}:{node.lineno}: __dict__ used as a payload")
                    continue
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in _ATTR_FUNCS and len(node.args) >= 2):
                    continue
                attr_arg = node.args[1]
                if not (isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str)):
                    hits.append(f"{relpath}:{node.lineno}: {node.func.id}(...) with a non-literal attribute name")
    return len(hits), hits


def check_bare_swallows():
    """§5.4 -- the rule's own literal wording: `except Exception: pass`,
    nothing else in the handler, AND no comment justifying it (the rule's
    own text: "requires a comment stating what is expected and why
    continuing is correct" -- a handler that already has one is compliant,
    not a violation). Comments aren't in the AST, so this re-reads the
    source line the `pass` sits on, plus the line before it, for a `#`.
    Not any exception type swallowed any way (a broader reading of "bare
    swallow") -- that's a different, larger number (measured separately at
    ~41 during this row's last refresh); this stays scoped to what the
    rule text actually names, so a reviewer citing this row is citing the
    same pattern the rule states."""
    hits = []
    for base in (os.path.join(ROOT, "src"), os.path.join(ROOT, "web_interface")):
        for relpath, tree in iter_py_files(base):
            path = os.path.join(ROOT, relpath)
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            for node in ast.walk(tree):
                if not (isinstance(node, ast.ExceptHandler) and len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass)):
                    continue
                if not (isinstance(node.type, ast.Name) and node.type.id == "Exception"):
                    continue
                pass_line = node.body[0].lineno
                own_line = lines[pass_line - 1] if pass_line <= len(lines) else ""
                prev_line = lines[pass_line - 2] if pass_line >= 2 else ""
                if "#" in own_line or prev_line.strip().startswith("#"):
                    continue  # already carries the comment the rule asks for
                hits.append(f"{relpath}:{node.lineno}: except Exception: pass (no comment)")
    return len(hits), hits


def check_progress_patterns():
    """§7.1 -- re.compile( calls inside orchestrator.py's PROGRESS_PATTERNS
    list, parsed (not imported) so this stays stdlib-only and never pulls
    in the web backend's DB/config env requirements."""
    path = os.path.join(ROOT, "web_interface", "backend", "app", "pipeline", "orchestrator.py")
    if not os.path.exists(path):
        return -1, []
    with open(path, encoding="utf-8", errors="replace") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PROGRESS_PATTERNS" for t in node.targets
        ):
            count = sum(
                1 for n in ast.walk(node.value)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "compile"
            )
            return count, []
    return -1, []  # PROGRESS_PATTERNS not found -- doc row needs a human look


# 7.4 ("datetime.now() in stage6") used to have a check here, wired into
# ROWS below. It's a [judgement] rule, not a [gate] -- the auto table above
# is documented as covering only [gate] rules, so counting it was a mistake
# (this script's, not the rule's own). Its file:line detail already lives
# in the rule's own *Why:*, so it isn't replaced with a hand-narrated §9 row
# either -- that would just be the same "judgement row in a [gate] table"
# shape §9 already has two pre-existing instances of (2.3, 6.5), not fixed
# by this round.


def _load_baseline():
    """Committed ratchet baselines for RATCHETED_ROWS, keyed by row label.
    Missing file reads as {} -- every row then reports "no baseline," the
    same recourse check_function_size.py gives a missing baseline."""
    try:
        with open(BASELINE, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def _table_verified_sha():
    """The `origin/dev @ <sha>` §9's hand-narrated table was last measured
    against, parsed from the doc's own sentence rather than a second field
    that could disagree with it (§1.5). None if the sentence isn't there."""
    with open(DOC, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"measured against `origin/dev` @ `([0-9a-f]{7,40})`", text)
    return m.group(1) if m else None


def _commits_behind(sha):
    """How many commits HEAD is ahead of `sha`. None if git can't answer
    (shallow checkout, sha not reachable, no .git at all) -- the caller
    skips the staleness check rather than guess at a number that isn't
    grounded in anything."""
    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", f"{sha}..HEAD"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        )
        return int(r.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None


# (row label, target text, check fn) -- target is prose ("0", "falling"),
# not itself the enforcement; RATCHETED_ROWS above decides how a row's
# count is actually gated.
ROWS = [
    ("1.2 pure layers import no `docx`", "0", check_pure_no_docx),
    ("1.3 peers do not import peers", "0", check_sections_no_peers),
    ("1.4 core does not import the web backend", "0", check_core_no_web_backend),
    ("2.1 no `db.query(` in `api/`", "falling", check_no_db_query_in_api),
    ("3.x oversized-function debt (excess lines)", "falling", check_function_size_excess),
    ("3.7 no metaprogramming", "0", check_no_metaprogramming),
    ("3.7 dynamic attribute access (non-literal)", "falling", check_dynamic_attribute_access),
    ("5.4 bare swallows (`except Exception: pass`)", "falling", check_bare_swallows),
    ("7.1 stdout-parsing regexes (`PROGRESS_PATTERNS`)", "falling", check_progress_patterns),
]


def compute_rows():
    """[(label, target, count, detail), ...] -- each check fn() runs once,
    reused by --report, the doc table, and the ratchet gate below rather
    than each recomputing it."""
    return [(label, target, *fn()) for label, target, fn in ROWS]


def render_table(results):
    lines = [BEGIN_MARKER, "", "| Rule | Target | Today | |", "|---|---|---|---|"]
    for label, target, count, _ in results:
        if label.startswith("3.x") or label in RATCHETED_ROWS:
            symbol = "ratchet"
        else:
            symbol = "✓" if count == 0 else "✗"
        lines.append(f"| {label} | {target} | {count} | {symbol} |")
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def _split_doc(doc_text):
    """(before, current_auto_block, after), or None if the markers aren't
    both present exactly once."""
    if doc_text.count(BEGIN_MARKER) != 1 or doc_text.count(END_MARKER) != 1:
        return None
    before, rest = doc_text.split(BEGIN_MARKER, 1)
    middle, after = rest.split(END_MARKER, 1)
    return before, BEGIN_MARKER + middle + END_MARKER, after


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true", help="print every row's fresh value and exit 0")
    ap.add_argument("--update", action="store_true", help="rewrite CODING_STANDARDS.md's auto block and standards-baseline.json")
    args = ap.parse_args()

    results = compute_rows()
    fresh = render_table(results)
    baseline = _load_baseline()

    if args.report:
        for label, target, count, detail in results:
            extra = f"  baseline={baseline.get(label, 'unset')}" if label in RATCHETED_ROWS else ""
            print(f"  {label:52} target={target:8} today={count}{extra}")
            for d in detail[:5]:
                print(f"      {d}")
            if len(detail) > 5:
                print(f"      ... and {len(detail) - 5} more")
        return 0

    with open(DOC, encoding="utf-8") as fh:
        doc_text = fh.read()
    parts = _split_doc(doc_text)
    if parts is None:
        print(f"{DOC}: expected exactly one {BEGIN_MARKER} ... {END_MARKER} block", file=sys.stderr)
        return 2
    before, current_block, after = parts

    ratchet_counts = {label: count for label, _, count, _ in results if label in RATCHETED_ROWS}

    if args.update:
        # Collect every blocked row before writing anything, the same shape
        # as `risen` below -- a --update that stops at the first regression
        # it meets would silently skip both the doc rewrite the module
        # docstring promises ("always writes the fresh number either
        # direction") and any *other* row's legitimate improvement, just
        # because ROWS happened to iterate a worse row first.
        new_baseline = dict(baseline)
        blocked = []
        for label, count in ratchet_counts.items():
            prior = baseline.get(label)
            if prior is not None and count > prior:
                blocked.append((label, prior, count))
                continue  # leave this one row's baseline untouched
            new_baseline[label] = count
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(new_baseline, fh, indent=2, sort_keys=True)
            fh.write("\n")
        with open(DOC, "w", encoding="utf-8") as fh:
            fh.write(before + fresh.rstrip("\n") + "\n" + after)
        if blocked:
            for label, prior, count in blocked:
                print(f"refusing to raise the baseline for {label}: {prior} -> {count}. "
                      f"The ratchet only turns one way.", file=sys.stderr)
            print("Every other row and the doc's auto block were still updated.", file=sys.stderr)
            return 1
        print("CODING_STANDARDS.md's auto block and standards-baseline.json updated")
        return 0

    missing = [label for label in ratchet_counts if label not in baseline]
    if missing:
        for label in missing:
            print(f"no baseline for {label!r} in {BASELINE}; run --update to set one", file=sys.stderr)
        return 2

    risen = [(label, baseline[label], count) for label, count in ratchet_counts.items() if count > baseline[label]]
    if risen:
        for label, base, count in risen:
            print(f"FAIL: {label} rose {base} -> {count}. The ratchet only turns one way.", file=sys.stderr)
        return 1

    if current_block.strip() != fresh.strip():
        print("FAIL: CODING_STANDARDS.md's auto-generated §9 rows are stale.", file=sys.stderr)
        print("Run: python3 scripts/check_standards.py --update", file=sys.stderr)
        return 1

    sha = _table_verified_sha()
    if sha:
        behind = _commits_behind(sha)
        if behind is not None and behind > STALE_WARN_COMMITS:
            print(f"WARN: §9's hand-narrated table was last measured at {sha}, now {behind} "
                  f"commits behind HEAD -- unlike the auto table above, it can't re-verify "
                  f"itself by regenerating. Worth a re-read.", file=sys.stderr)
            if behind > STALE_FAIL_COMMITS:
                print(f"FAIL: {behind} commits behind is past the hard limit ({STALE_FAIL_COMMITS}).", file=sys.stderr)
                return 1

    print("OK: CODING_STANDARDS.md's auto-generated §9 rows match current dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
