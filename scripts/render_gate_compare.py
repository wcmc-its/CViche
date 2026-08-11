#!/usr/bin/env python3
"""Compare two render_gate.py output dirs. PASS only at zero unexplained deltas.

    python3 scripts/render_gate_compare.py <arm_a_out> <arm_b_out>

Two independent checks, because each one is blind to what the other catches
(measured by ablation -- 31 of 44 stage-6 renderers are deletable whole with
the paragraph check alone reporting CHANGED 0, see issue #584):

1. PARAGRAPH TEXT, read from raw w:t nodes rather than python-docx
   Paragraph.text -- python-docx drops runs inside <w:ins> tracked changes,
   which stage 6 emits, and under-reports by 10-19% (#461). <w:br/>/<w:tab/>
   collapse to a space so a two-line table cell stays one paragraph.
2. A C14N FINGERPRINT of the whole word/document.xml, which the paragraph
   check cannot see: styles, run properties, numPr (list level), table
   borders and parentage, and every tracked-change deletion (<w:delText>,
   invisible to (1) by construction). Ablation: deleting _add_track_change_
   deletion wipes 1,926 <w:delText> nodes across 68/100 CVs and (1) alone
   reports CHANGED 0.

Both checks mask what stage 6 stamps at render time rather than compares:
the `w:date` timestamp stage 6 writes onto every tracked-change element, and
the "<Month> <day>, <year>" preparation-date text stamp
(stage6/sections/personal_data.py). Masking those two is NOT the same as
being immune to cross-midnight runs -- two more datetime.now() call sites
(stage6/sections/education.py, research_support.py) use the CURRENT YEAR to
decide whether a degree is in-progress and whether a grant is current or
past, which changes which SECTION content renders under, not just a
timestamp value. That is a structural effect this script cannot mask away.
Render both arms on the same day; if a run crosses midnight, re-render the
reference arm and re-run rather than trusting a stale one
(HANDOFF-wave1-completion-2026-08-11.md, issue #584).

FAILS CLOSED on a crashed render: reads both arms' _render_index.json (written
by render_gate.py) and refuses to report PASS if either arm recorded a
failure for any uid in scope, or if the set of successfully-rendered uids
differs between the two arms -- "nothing comparable" is a failure, not
CHANGED 0 (CODING_STANDARDS Section 5.5). If _render_index.json is absent for
an arm (a dir built some other way), that arm is treated as fully unverified:
this script still runs, but says so loudly rather than silently trusting it.

CONTROLS OF RECORD (issue #584): run the same arm against itself -- both
checks must report 0. Run a deliberately crashing arm (stage_6.call_llm
patched to always raise is not enough; patch run_stage6 itself to raise for
every uid) -- render_gate.py must exit non-zero, and this script must refuse
to PASS. See docs/guides/render-doctor-gates.md for the full recipe.
"""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DATE_TEXT_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|"
    r"November|December) \d{1,2}, \d{4}$"
)


def _render_index(out_dir: Path) -> dict:
    p = out_dir / "_render_index.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _check_render_failures(a_dir: Path, b_dir: Path) -> list:
    """Fail-closed guard (defect 1, issue #584): a crashed render must never
    be silently compared as if it were a clean arm. Returns problem strings;
    empty means clear to compare."""
    problems = []
    idx_a, idx_b = _render_index(a_dir), _render_index(b_dir)
    if not idx_a:
        problems.append(f"{a_dir}: no _render_index.json -- unverified arm")
    if not idx_b:
        problems.append(f"{b_dir}: no _render_index.json -- unverified arm")
    for label, idx in (("A", idx_a), ("B", idx_b)):
        failed = sorted(u for u, r in idx.items() if "error" in r)
        if failed:
            problems.append(f"arm {label} recorded {len(failed)} render "
                            f"failure(s): {' '.join(failed[:10])}")
    return problems


def _paras(path: Path) -> list:
    """Paragraph texts via raw w:t nodes -- see module docstring, check 1."""
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        buf = []
        for n in p.iter():
            if n.tag == W + "t":
                buf.append(n.text or "")
            elif n.tag in (W + "br", W + "tab"):
                buf.append(" ")
        t = " ".join("".join(buf).split())
        if t:
            out.append(t)
    return out


def _fingerprint(path: Path) -> str:
    """sha256 of the c14n-canonicalised document.xml, with the two render-time
    stamps masked -- see module docstring, check 2."""
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    date_attr = W + "date"
    for el in root.iter():
        if date_attr in el.attrib:
            el.attrib[date_attr] = "<MASKED-DATE>"
        if el.tag == W + "t" and el.text and _DATE_TEXT_RE.match(el.text.strip()):
            el.text = "<MASKED-DATE>"
    canon = etree.tostring(root, method="c14n")
    return hashlib.sha256(canon).hexdigest()


def main():
    a_dir, b_dir = Path(sys.argv[1]), Path(sys.argv[2])

    problems = _check_render_failures(a_dir, b_dir)
    if problems:
        print("RENDER FAILURE GUARD TRIPPED -- refusing to compare:")
        for p in problems:
            print(f"  - {p}")
        print("\nFAIL - fix the failing arm(s) and re-render before comparing")
        return 1

    a_files = {p.name for p in a_dir.glob("*.docx")}
    b_files = {p.name for p in b_dir.glob("*.docx")}
    only_a, only_b = sorted(a_files - b_files), sorted(b_files - a_files)
    common = sorted(a_files & b_files)

    para_changed, print_changed, fp_a, fp_b = [], 0, 0, 0
    for name in common:
        pa, pb = _paras(a_dir / name), _paras(b_dir / name)
        fa, fb = _fingerprint(a_dir / name), _fingerprint(b_dir / name)
        if fa != fb:
            fp_a += 1
        if pa != pb:
            print_changed += 1
            added = [x for x in pb if x not in pa]
            removed = [x for x in pa if x not in pb]
            para_changed.append((name, len(pa), len(pb), added, removed))

    print(f"compared          {len(common)} CVs")
    print(f"paragraph CHANGED {print_changed}")
    print(f"fingerprint CHANGED {fp_a} (styles/numPr/borders/tracked-changes -- "
          f"invisible to the paragraph check)")
    if only_a:
        print(f"MISSING in B      {len(only_a)}: {' '.join(only_a[:8])}")
    if only_b:
        print(f"EXTRA in B        {len(only_b)}: {' '.join(only_b[:8])}")

    for name, na, nb, added, removed in para_changed[:10]:
        print(f"\n  {name}  paragraphs {na} -> {nb}")
        for x in removed[:3]:
            print(f"    - {x[:150]}")
        for x in added[:3]:
            print(f"    + {x[:150]}")
    if len(para_changed) > 10:
        print(f"\n  ... and {len(para_changed) - 10} more paragraph-changed CVs")

    ok = not print_changed and not fp_a and not only_a and not only_b
    print(f"\n{'PASS - zero unexplained deltas' if ok else 'FAIL - output differs'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
