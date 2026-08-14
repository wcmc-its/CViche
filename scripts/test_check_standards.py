#!/usr/bin/env python3
"""Self-test for check_standards.py.

    python3 scripts/test_check_standards.py

Builds a temp tree shaped like the real repo (check_standards.py's checks
are anchored to fixed subpaths -- src/unified_pipeline/stage6/..., not an
arbitrary root -- so the fixture has to have that shape, the same reason
check_function_size.py's own self-test copies the script into a temp
scripts/ dir rather than pointing it at synthetic files anywhere). One
planted violation per check, run --report, assert it's counted; then the
--update / diff-gate mechanics against CODING_STANDARDS.md's auto block.
"""

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

    # 3.7 metaprogramming -- a __getattr__ override
    _write(
        os.path.join(tree, "src", "meta.py"),
        "class Router:\n    def __getattr__(self, name):\n        return None\n",
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

    # 7.4 -- datetime.now() in stage6/
    _write(
        os.path.join(tree, "src", "unified_pipeline", "stage6", "sections", "gamma.py"),
        "from datetime import datetime\n\ndef f():\n    return datetime.now()\n",
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

        assert "today=1" in out.split("3.7 no metaprogramming")[1].split("\n")[0]
        print("3.7 __getattr__ override                  counted   ok")

        dynattr_section = out.split("3.7 dynamic attribute access")[1].split("\n")[0]
        assert "today=1" in dynattr_section, dynattr_section  # only the non-literal call counts
        print("3.7 dynamic attribute access              counted   ok (literal call excluded)")

        swallow_section = out.split("5.4")[1].split("\n")[0]
        assert "today=1" in swallow_section, swallow_section  # commented one is excluded
        print("5.4 bare swallow, commented one excluded  counted   ok")

        assert "today=3" in out.split("7.1")[1].split("\n")[0]
        print("7.1 PROGRESS_PATTERNS regex count          counted   ok")

        assert "today=1" in out.split("7.4")[1].split("\n")[0]
        print("7.4 datetime.now() in stage6/              counted   ok")

        # --report never touches the doc
        with open(os.path.join(tree, "docs", "CODING_STANDARDS.md")) as fh:
            assert "placeholder" in fh.read()

        # default mode: the doc's placeholder auto block doesn't match fresh
        # numbers -> gate fails
        r = run(tree)
        assert r.returncode == 1, r.stdout
        assert "stale" in r.stderr
        print("default mode  stale auto block  exit=1    ok")

        # --update rewrites the block in place and preserves everything else
        r = run(tree, "--update")
        assert r.returncode == 0, r.stderr
        with open(os.path.join(tree, "docs", "CODING_STANDARDS.md")) as fh:
            updated = fh.read()
        assert "placeholder" not in updated
        assert "1.2 pure layers import no `docx`" in updated
        assert "Some hand-written prose after the block" in updated
        assert updated.count("<!-- check_standards:auto:begin -->") == 1
        print("--update rewrites only the marked block               ok")

        # now in sync -> default mode passes
        r = run(tree)
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout
        print("default mode  in sync            exit=0    ok")

        # fix the docx violation -> --report count drops, and the doc goes stale again
        os.remove(os.path.join(tree, "src", "unified_pipeline", "stage6", "parsing", "bad.py"))
        r = run(tree, "--report")
        assert "today=0" in r.stdout.split("1.2")[1].split("\n")[0]
        r = run(tree)
        assert r.returncode == 1, "fixing a violation without --update must show as drift, not silently pass"
        print("fixed violation shows as doc drift        exit=1    ok")

    print("\nall check_standards self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
