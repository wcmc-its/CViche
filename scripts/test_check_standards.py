#!/usr/bin/env python3
"""Self-test for check_standards.py.

    python3 scripts/test_check_standards.py

Builds a temp tree shaped like the real repo (check_standards.py's checks
are anchored to fixed subpaths -- src/unified_pipeline/stage6/..., not an
arbitrary root -- so the fixture has to have that shape, the same reason
check_function_size.py's own self-test copies the script into a temp
scripts/ dir rather than pointing it at synthetic files anywhere). One
planted violation per check, run --report, assert it's counted; then the
--update / diff-gate mechanics against CODING_STANDARDS.md's auto block;
then the four ratcheted rows' baseline mechanics (RATCHETED_ROWS); then
the waiver mechanism (WAIVABLE_ROWS) -- a waived hit is excused from its
row's count but not forgotten, and the waived count itself ratchets; then
the staleness helpers, which run against the real repo rather than the
fixture since there's no git history to fake in a plain tempdir.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_standards.py")
CHECK_FUNCTION_SIZE = os.path.join(HERE, "check_function_size.py")

DOC_TEMPLATE = """# CODING_STANDARDS.md (fixture)

## 9. Where the code stands against this today

<!-- check_standards:auto:begin -->
placeholder -- will be replaced
<!-- check_standards:auto:end -->

Some hand-written prose after the block, which --update must leave alone.
"""


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)


def build_tree(tree):
    """A minimal repo skeleton with exactly one planted violation per row."""
    os.makedirs(os.path.join(tree, "scripts"))
    for src, name in ((SCRIPT, "check_standards.py"), (CHECK_FUNCTION_SIZE, "check_function_size.py")):
        with open(src) as fh, open(os.path.join(tree, "scripts", name), "w") as out:
            out.write(fh.read())
    _write(os.path.join(tree, "docs", "CODING_STANDARDS.md"), DOC_TEMPLATE)

    # 1.2 -- a pure-layer file that imports docx (the violation)
    _write(
        os.path.join(tree, "src", "unified_pipeline", "stage6", "parsing", "bad.py"),
        "import docx\n\ndef f():\n    return docx\n",
    )
    _write(os.path.join(tree, "src", "unified_pipeline", "stage6", "parsing", "__init__.py"), "")

    # 1.3 -- a section file importing a sibling section (not __init__.py, which is exempt)
    sections = os.path.join(tree, "src", "unified_pipeline", "stage6", "sections")
    _write(os.path.join(sections, "alpha.py"), "def a():\n    pass\n")
    _write(os.path.join(sections, "beta.py"), "from .alpha import a\n\ndef b():\n    return a()\n")
    _write(os.path.join(sections, "__init__.py"), "from .alpha import a\nfrom .beta import b\n")

    # 1.4 -- pipeline core importing the web backend
    _write(
        os.path.join(tree, "src", "unified_pipeline", "reaches_back.py"),
        "from app.config_loader import get_config\n\ndef f():\n    return get_config\n",
    )

    # 2.1 -- db.query( in api/
    api = os.path.join(tree, "web_interface", "backend", "app", "api")
    _write(os.path.join(api, "widgets.py"), "def handler(db):\n    return db.query(Widget).all()\n")

    # 3.7 metaprogramming -- one unwaived __getattr__ override (still counts)
    # and one waived one (excused, but tracked in its own ratcheted budget)
    _write(
        os.path.join(tree, "src", "meta.py"),
        "class Router:\n    def __getattr__(self, name):\n        return None\n",
    )
    _write(
        os.path.join(tree, "src", "waived_meta.py"),
        "class Router2:\n"
        "    # standards-waiver: 3.7 -- deliberate, see #999\n"
        "    def __getattr__(self, name):\n"
        "        return None\n",
    )

    # 3.7 dynamic attribute access -- non-literal attribute name
    _write(
        os.path.join(tree, "src", "dynattr.py"),
        "def f(obj, field):\n    return getattr(obj, field)\n\n"
        "def g(obj):\n    return getattr(obj, 'literal_is_fine')\n",
    )

    # 5.4 -- one uncommented bare swallow, one commented (must NOT count)
    _write(
        os.path.join(tree, "src", "swallows.py"),
        "def f():\n    try:\n        risky()\n    except Exception:\n        pass\n\n"
        "def g():\n    try:\n        risky()\n    except Exception:\n        pass  # best-effort, logged elsewhere\n",
    )

    # 7.1 -- PROGRESS_PATTERNS with a known regex count
    pipeline = os.path.join(tree, "web_interface", "backend", "app", "pipeline")
    _write(
        os.path.join(pipeline, "orchestrator.py"),
        "import re\n\nPROGRESS_PATTERNS = [\n    re.compile(r'a'),\n    re.compile(r'b'),\n    re.compile(r'c'),\n]\n",
    )

    # 7.9 -- a 3.14 image, one doc that disagrees (the violation) and one
    # workflow pin that agrees (must NOT count)
    _write(
        os.path.join(tree, "web_interface", "backend", "Dockerfile"),
        "FROM python:3.14-slim AS deps\nRUN pip install nothing\n",
    )
    _write(os.path.join(tree, "docs", "STALE_README.md"), "### Prerequisites\n\n- Python 3.11+\n")
    _write(
        os.path.join(tree, ".github", "workflows", "ci.yml"),
        "jobs:\n  t:\n    steps:\n      - uses: actions/setup-python@v5\n        with:\n          python-version: \"3.14\"\n",
    )



def run(tree, *args):
    return subprocess.run(
        [sys.executable, os.path.join(tree, "scripts", "check_standards.py"), *args],
        capture_output=True, text=True, cwd=tree,
    )


def main():
    with tempfile.TemporaryDirectory() as tree:
        build_tree(tree)

        r = run(tree, "--report")
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "1.2 pure layers import no `docx`" in out and "today=1" in out.split("1.2")[1].split("\n")[0]
        print("1.2 docx import in a pure layer          counted   ok")

        assert "today=1" in out.split("1.3")[1].split("\n")[0]
        print("1.3 __init__.py excluded, sibling counted            ok")

        assert "today=1" in out.split("1.4")[1].split("\n")[0]
        print("1.4 core -> web-backend import            counted   ok")

        assert "today=1" in out.split("2.1")[1].split("\n")[0]
        print("2.1 db.query( in api/                     counted   ok")

        section_37meta = out.split("3.7 no metaprogramming")[1].split("3.7 dynamic attribute access")[0]
        assert "today=1" in section_37meta.split("\n")[0]  # net: waived_meta.py's hit is excused
        assert "waived (1" in section_37meta  # but not forgotten -- tracked separately
        print("3.7 __getattr__: unwaived counts, waived is excused but tracked  ok")

        dynattr_section = out.split("3.7 dynamic attribute access")[1].split("\n")[0]
        assert "today=1" in dynattr_section, dynattr_section  # only the non-literal call counts
        print("3.7 dynamic attribute access              counted   ok (literal call excluded)")

        swallow_section = out.split("5.4")[1].split("\n")[0]
        assert "today=1" in swallow_section, swallow_section  # commented one is excluded
        print("5.4 bare swallow, commented one excluded  counted   ok")

        assert "today=3" in out.split("7.1")[1].split("\n")[0]
        print("7.1 PROGRESS_PATTERNS regex count          counted   ok")

        assert "today=1" in out.split("7.9")[1].split("\n")[0], out.split("7.9")[1][:200]
        print("7.9 doc disagreeing with the image tag    counted   ok (matching CI pin excluded)")

        assert "7.4" not in out  # [judgement], not [gate] -- the auto table is [gate]-only
        print("7.4 absent from --report's auto-checkable set             ok")

        # --report never touches the doc
        with open(os.path.join(tree, "docs", "CODING_STANDARDS.md")) as fh:
            assert "placeholder" in fh.read()

        # default mode, before any baseline exists: the four ratcheted rows
        # have nothing to compare against -> gate refuses to guess
        r = run(tree)
        assert r.returncode == 2, r.stdout
        assert "no baseline" in r.stderr
        print("default mode  no baseline yet   exit=2    ok")

        # --update rewrites the doc block AND writes a fresh baseline
        r = run(tree, "--update")
        assert r.returncode == 0, r.stderr
        with open(os.path.join(tree, "docs", "CODING_STANDARDS.md")) as fh:
            updated = fh.read()
        assert "placeholder" not in updated
        assert "1.2 pure layers import no `docx`" in updated
        assert "Some hand-written prose after the block" in updated
        assert updated.count("<!-- check_standards:auto:begin -->") == 1
        assert "ratchet" in updated  # the four ratcheted rows' status symbol
        print("--update rewrites the doc block and preserves prose        ok")

        baseline_path = os.path.join(tree, "scripts", "standards-baseline.json")
        with open(baseline_path) as fh:
            baseline = json.load(fh)
        assert baseline["2.1 no `db.query(` in `api/`"] == 1
        assert baseline["7.1 stdout-parsing regexes (`PROGRESS_PATTERNS`)"] == 3
        assert baseline["3.7 no metaprogramming [waived]"] == 1  # a waiver ratchets too
        print("--update writes standards-baseline.json with fresh counts  ok")

        # now in sync -> default mode passes
        r = run(tree)
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout
        print("default mode  in sync            exit=0    ok")

        # add a second db.query( site -> 2.1's count rises past its baseline
        with open(os.path.join(tree, "web_interface", "backend", "app", "api", "widgets.py"), "a") as fh:
            fh.write("\n\ndef handler2(db):\n    return db.query(Gadget).all()\n")
        r = run(tree)
        assert r.returncode == 1, r.stdout
        assert "2.1" in r.stderr and "rose 1 -> 2" in r.stderr
        print("ratcheted row rising blocks the default gate  exit=1      ok")

        # --update also refuses to lock in the regression, same as check_function_size.py
        r = run(tree, "--update")
        assert r.returncode == 1, r.stdout
        assert "refusing to raise the baseline" in r.stderr
        with open(baseline_path) as fh:
            assert json.load(fh)["2.1 no `db.query(` in `api/`"] == 1  # unchanged
        print("--update refuses to raise a ratcheted baseline             ok")

        # while 2.1 is still regressed, ALSO genuinely fix the dynamic-
        # attribute violation (1 -> 0) -- a --update that stops at the
        # first blocked row would wrongly leave this real improvement
        # unlocked and the doc's auto block untouched too
        _write(
            os.path.join(tree, "src", "dynattr.py"),
            "def g(obj):\n    return getattr(obj, 'literal_is_fine')\n",
        )
        r = run(tree, "--update")
        assert r.returncode == 1, r.stdout  # still blocked overall: 2.1 is still up
        assert "2.1" in r.stderr and "refusing to raise" in r.stderr
        with open(baseline_path) as fh:
            b = json.load(fh)
        assert b["2.1 no `db.query(` in `api/`"] == 1  # blocked row: untouched
        assert b["3.7 dynamic attribute access (non-literal)"] == 0  # other row: locked in
        with open(os.path.join(tree, "docs", "CODING_STANDARDS.md")) as fh:
            assert "| 3.7 dynamic attribute access (non-literal) | falling | 0 | ratchet |" in fh.read()
        print("--update locks in an unrelated improvement despite a blocked row  ok")

        # restore both fixtures to their original state. dynattr's baseline
        # needs a hand-edit back up to 1 too -- exactly the "a maintainer
        # edits the baseline file by hand, a separate and visibly
        # deliberate act" escape hatch §3.2a already documents for
        # check_function_size.py; --update itself won't do this (it never
        # raises a baseline, by design).
        _write(
            os.path.join(tree, "src", "dynattr.py"),
            "def f(obj, field):\n    return getattr(obj, field)\n\n"
            "def g(obj):\n    return getattr(obj, 'literal_is_fine')\n",
        )
        with open(baseline_path) as fh:
            b = json.load(fh)
        b["3.7 dynamic attribute access (non-literal)"] = 1
        with open(baseline_path, "w") as fh:
            json.dump(b, fh)
        _write(
            os.path.join(tree, "web_interface", "backend", "app", "api", "widgets.py"),
            "def handler(db):\n    return db.query(Widget).all()\n",
        )

        # everything is back at its original baseline -> --update has
        # nothing to refuse, and resyncs the doc's auto block (still
        # showing dynattr=0 from the partial update above)
        r = run(tree, "--update")
        assert r.returncode == 0, r.stderr
        print("--update resyncs cleanly once nothing is regressed        ok")

        r = run(tree)
        assert r.returncode == 0, r.stdout
        print("reverting the regression clears the ratchet gate          ok")

        # a SECOND waived violation -> the waived budget itself rises past
        # its baseline (1 -> 2). A waiver is not a free pass: this must
        # block the gate exactly like any other ratcheted row rising.
        _write(
            os.path.join(tree, "src", "waived_meta2.py"),
            "class Router3:\n"
            "    # standards-waiver: 3.7 -- also deliberate, see #1000\n"
            "    def __getattr__(self, name):\n"
            "        return None\n",
        )
        r = run(tree)
        assert r.returncode == 1, r.stdout
        assert "[waived]" in r.stderr and "rose 1 -> 2" in r.stderr
        print("a second waiver rising blocks the gate too  exit=1        ok")

        r = run(tree, "--update")
        assert r.returncode == 1, r.stdout
        assert "refusing to raise the baseline" in r.stderr
        print("--update refuses to raise a waived-count baseline too      ok")

        # remove it -> the waived-count ratchet is satisfied again, but the
        # blocked --update above still rewrote the doc to THAT moment's
        # snapshot (2 waived), so one more --update is needed to resync it
        # to the reverted state -- same "blocked rows don't freeze the
        # doc" behavior already exercised for 2.1/dynattr above.
        os.remove(os.path.join(tree, "src", "waived_meta2.py"))
        r = run(tree, "--update")
        assert r.returncode == 0, r.stderr
        r = run(tree)
        assert r.returncode == 0, r.stdout
        print("removing the extra waiver clears the gate                 ok")

        # now waive the ORIGINAL unwaived __getattr__ too (net -> 0, waived
        # -> 2) and lock in the new waived-count baseline by hand -- the
        # same deliberate maintainer edit §3.2a's own escape hatch uses,
        # since --update itself never raises a baseline. This is the
        # concrete case the doc promises: a row with only waived hits
        # reads ~, never a clean ✓.
        _write(
            os.path.join(tree, "src", "meta.py"),
            "class Router:\n"
            "    # standards-waiver: 3.7 -- also deliberate, see #1001\n"
            "    def __getattr__(self, name):\n"
            "        return None\n",
        )
        with open(baseline_path) as fh:
            b = json.load(fh)
        b["3.7 no metaprogramming [waived]"] = 2
        with open(baseline_path, "w") as fh:
            json.dump(b, fh)
        r = run(tree, "--update")
        assert r.returncode == 0, r.stderr
        with open(os.path.join(tree, "docs", "CODING_STANDARDS.md")) as fh:
            doc = fh.read()
        assert "| 3.7 no metaprogramming | 0 | 0 (2 waived) | ~ |" in doc
        print("net-zero-but-waived renders ~, not a clean check mark      ok")

        # restore meta.py's real violation (unwaived) and its baseline, so
        # later assertions about this row aren't left in a mutated state
        _write(
            os.path.join(tree, "src", "meta.py"),
            "class Router:\n    def __getattr__(self, name):\n        return None\n",
        )
        with open(baseline_path) as fh:
            b = json.load(fh)
        b["3.7 no metaprogramming [waived]"] = 1
        with open(baseline_path, "w") as fh:
            json.dump(b, fh)
        run(tree, "--update")

        # fix the docx violation (not a ratcheted row) -> the AUTO TABLE goes
        # stale rather than the ratchet gate tripping, a different failure mode
        os.remove(os.path.join(tree, "src", "unified_pipeline", "stage6", "parsing", "bad.py"))
        r = run(tree, "--report")
        assert "today=0" in r.stdout.split("1.2")[1].split("\n")[0]
        r = run(tree)
        assert r.returncode == 1 and "stale" in r.stderr, "fixing a non-ratcheted violation without --update must show as doc drift"
        print("fixed non-ratcheted violation shows as doc drift  exit=1  ok")

    # Staleness helpers run against the real repo, not the fixture -- a
    # plain tempdir has no git history to fake a commit-distance from.
    sys.path.insert(0, HERE)
    import check_standards
    sha = check_standards._table_verified_sha()
    assert sha, "expected a `measured against `origin/dev` @ `<sha>`` sentence in CODING_STANDARDS.md"
    # None is the correct answer, not a failure, on a shallow clone -- CI's
    # own checkout is fetch-depth: 1, where `git rev-list SHA..HEAD` can't
    # see far enough back to answer. Assert the shape of whichever one it
    # gives, not that it must be the full-history value.
    behind = check_standards._commits_behind(sha)
    assert behind is None or behind >= 0
    behind_self = check_standards._commits_behind("HEAD")
    assert behind_self is None or behind_self == 0
    print("staleness helpers parse the doc's sha and count real commits   ok")

    # WAIVER_RE must anchor to an actual comment, not just the substring
    # appearing anywhere on the line -- a nearby line that merely mentions
    # this syntax (discussing it, not applying it) must not excuse a real
    # hit. Both are lines with no `#` immediately before the token.
    assert check_standards.WAIVER_RE.search('NOTE = "standards-waiver: 3.7 "') is None
    assert check_standards.WAIVER_RE.search(
        'return getattr(obj, field)  # TODO: consider a "standards-waiver: 3.7" comment here'
    ) is None
    assert check_standards.WAIVER_RE.search("    # standards-waiver: 3.7 -- reason").group(1) == "3.7"
    print("waiver token only matches inside an actual comment              ok")

    print("\nall check_standards self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
