#!/usr/bin/env python3
"""Self-test for the oversized-function ratchet.

    python3 scripts/test_check_function_size.py

Asserts the three behaviours the gate exists for: debt growing fails, debt
shrinking passes, and --update refuses to turn the ratchet backwards. Uses a
temp tree, so it never touches the real baseline.
"""

import json
import os
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_function_size.py")


def write_fn(path, name, body_lines):
    """A function of exactly body_lines+1 lines."""
    with open(path, "w") as fh:
        fh.write(f"def {name}():\n")
        fh.writelines(f"    x{i} = {i}\n" for i in range(body_lines))


def run(tree, *args):
    # Run from elsewhere on purpose: the scan and the baseline are anchored to
    # the script's own repo, so cwd must not be able to change what is measured.
    return subprocess.run(
        [sys.executable, os.path.join(tree, "scripts", "check.py"), *args],
        capture_output=True, text=True, cwd=os.path.dirname(tree),
    )


def main():
    with tempfile.TemporaryDirectory() as tree:
        os.mkdir(os.path.join(tree, "scripts"))
        with open(SCRIPT) as src, open(os.path.join(tree, "scripts", "check.py"), "w") as dst:
            dst.write(src.read())

        # one 301-line function -> excess 101
        write_fn(os.path.join(tree, "a.py"), "big", 300)
        r = run(tree, "--update")
        assert r.returncode == 0, r.stderr
        base = json.load(open(os.path.join(tree, "scripts", "function-size-baseline.json")))
        assert base["excess_lines"] == 101, base
        assert base["count"] == 1, base
        print(f"baseline           excess={base['excess_lines']:4}  ok")

        # unchanged -> pass
        assert run(tree).returncode == 0
        print("unchanged          exit=0                ok")

        # a second oversized function -> debt grows -> FAIL
        write_fn(os.path.join(tree, "b.py"), "big2", 250)   # 251 lines -> +51
        r = run(tree)
        assert r.returncode == 1, f"expected failure, got {r.returncode}: {r.stdout}"
        assert "grew by 51 lines" in r.stderr, r.stderr
        print("debt grows         exit=1  +51 lines     ok")

        # split b back below the threshold -> back to exactly the baseline -> pass, unchanged
        with open(os.path.join(tree, "b.py"), "w") as fh:
            fh.write("def h1():\n")
            fh.writelines(f"    y{i} = {i}\n" for i in range(120))
            fh.write("\ndef h2():\n")
            fh.writelines(f"    z{i} = {i}\n" for i in range(120))
        r = run(tree)
        assert r.returncode == 0, r.stderr
        assert "unchanged" in r.stdout, r.stdout
        print("back to baseline   exit=0  unchanged     ok")

        # now split the original offender too -> below baseline -> reported as an improvement
        with open(os.path.join(tree, "a.py"), "w") as fh:
            fh.write("def a1():\n")
            fh.writelines(f"    x{i} = {i}\n" for i in range(150))
            fh.write("\ndef a2():\n")
            fh.writelines(f"    w{i} = {i}\n" for i in range(150))
        r = run(tree)
        assert r.returncode == 0, r.stderr
        assert "101 lines better" in r.stdout, r.stdout
        print("split below limit  exit=0  -101 lines    ok")

        # restore the offender so the --update regression case below is meaningful
        write_fn(os.path.join(tree, "a.py"), "big", 300)

        # --update must refuse to raise the baseline
        write_fn(os.path.join(tree, "b.py"), "big2", 400)
        r = run(tree, "--update")
        assert r.returncode == 1, f"--update should refuse a regression: {r.stdout}"
        assert "only turns one way" in r.stderr, r.stderr
        after = json.load(open(os.path.join(tree, "scripts", "function-size-baseline.json")))
        assert after["excess_lines"] == 101, "baseline must be untouched after a refused update"
        print("update refuses up  exit=1  baseline kept ok")

        # a test file is ignored, so test helpers cannot blow the budget
        write_fn(os.path.join(tree, "test_helper.py"), "huge_fixture", 900)
        r = run(tree, "--report")
        assert "huge_fixture" not in r.stdout, r.stdout
        print("test files skipped                       ok")

    print("\nall ratchet self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
