#!/usr/bin/env python3
"""Compare two render_gate.py output dirs. PASS only at zero unexplained deltas.

    python3 scripts/render_gate_compare.py <arm_a_out> <arm_b_out>

Two independent checks, because each one is blind to what the other catches
(measured by ablation -- 31 of 44 stage-6 renderers are deletable whole with
the paragraph check alone reporting CHANGED 0, see issue #584):

1. PARAGRAPH TEXT, read from raw w:t nodes rather than python-docx
   Paragraph.text -- python-docx drops runs inside <w:ins> tracked changes,
   which stage 6 emits, and under-reports by 10-19% (#461). <w:br/>/<w:tab/>
   collapse to a space so a two-line table cell stays one paragraph. Text
   extraction is deliberately scoped to w:t/w:br/w:tab -- the WordprocessingML
   elements stage 6 actually emits into paragraph runs (verified: no w:cr,
   w:noBreakHyphen, w:softHyphen, or w:sym in this codebase's render output).
   A document from elsewhere in the OOXML vocabulary is out of scope, not
   silently mishandled.
2. A C14N FINGERPRINT of the whole word/document.xml, which the paragraph
   check cannot see: styles, run properties, numPr (list level), table
   borders and parentage, and every tracked-change deletion (<w:delText>,
   invisible to (1) by construction). Ablation: deleting _add_track_change_
   deletion wipes 1,926 <w:delText> nodes across 68/100 CVs and (1) alone
   reports CHANGED 0.

Both checks mask what stage 6 stamps at render time rather than compares:
the `w:date` timestamp stage 6 writes onto every tracked-change element, and
the "Date of preparation: <Month> <day>, <year>" paragraph
(stage6/sections/personal_data.py) -- masked by its known label text, not by
matching any bare "<Month> <day>, <year>" string anywhere in the document,
which would also swallow a real CV date (a grant/degree date rendered as its
own text run) as a false-negative (review on #589). Masking those two is NOT
the same as being immune to cross-midnight runs -- two more datetime.now()
call sites (stage6/sections/education.py, research_support.py) use the
CURRENT YEAR to decide whether a degree is in-progress and whether a grant is
current or past, which changes which SECTION content renders under, not just
a timestamp value. That is a structural effect this script cannot mask away.
Render both arms on the same day; if a run crosses midnight, re-render the
reference arm and re-run rather than trusting a stale one
(HANDOFF-wave1-completion-2026-08-11.md, issue #584).

FAILS CLOSED on a crashed render: reads both arms' _render_index.json (written
by render_gate.py) and refuses to report PASS if either arm recorded a
failure for any uid in scope, if the set of successfully-rendered uids
differs between the two arms, or if an index's "success" entries and the
DOCX files actually on disk disagree in either direction -- an index that
claims success for a uid with no corresponding DOCX (or a DOCX with no
matching success entry) means the two are out of sync, and "nothing
comparable" is a failure, not CHANGED 0 (CODING_STANDARDS Section 5.5). If
_render_index.json is absent for an arm (a dir built some other way), that
arm is treated as fully unverified: this script still runs, but says so
loudly rather than silently trusting it. A render comparison over zero
common artifacts is also refused outright, so a run that produced nothing to
compare can never look like PASS.

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
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Anchored on the known "Date of preparation:" label (stage6/sections/
# personal_data.py renders it as one run, "Date of preparation: <date>"),
# not on any bare "<Month> <day>, <year>" text -- see module docstring.
# Captures the label separately so only the date value gets masked; text
# that happens to follow in the same run (a real content difference) is
# preserved and still compared.
_PREP_DATE_RE = re.compile(
    r"^(Date of preparation:\s*)"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r" \d{1,2}, \d{4}"
)


def _render_index(out_dir: Path) -> dict:
    p = out_dir / "_render_index.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        idx = json.load(f)
    if not isinstance(idx, dict):
        raise ValueError(f"{p}: _render_index.json is not an object (got {type(idx).__name__})")
    for uid, entry in idx.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{p}: entry for uid {uid!r} is not an object (got {type(entry).__name__})")
    return idx


def _check_render_failures(a_dir: Path, b_dir: Path) -> list:
    """Fail-closed guard (defect 1, issue #584): a crashed render must never
    be silently compared as if it were a clean arm. Returns problem strings;
    empty means clear to compare.

    Also cross-checks each index against the DOCX files actually on disk
    (review on #589): _check_render_failures only used to look at "error"
    entries, so an index whose "success" entries have no corresponding DOCX
    -- and the docx-set comparison below sees 0 == 0 -- reported PASS on a
    comparison that verified nothing.
    """
    problems = []
    try:
        idx_a, idx_b = _render_index(a_dir), _render_index(b_dir)
    except (ValueError, json.JSONDecodeError) as e:
        problems.append(f"unreadable/malformed _render_index.json: {e}")
        return problems
    if not idx_a:
        problems.append(f"{a_dir}: no _render_index.json -- unverified arm")
    if not idx_b:
        problems.append(f"{b_dir}: no _render_index.json -- unverified arm")
    for label, idx, out_dir in (("A", idx_a, a_dir), ("B", idx_b, b_dir)):
        failed = sorted(u for u, r in idx.items() if "error" in r)
        if failed:
            problems.append(f"arm {label} recorded {len(failed)} render "
                            f"failure(s): {' '.join(failed[:10])}")
        successful = {u for u, r in idx.items() if "error" not in r}
        docx_present = {p.name[:-len("_wcm.docx")] for p in out_dir.glob("*_wcm.docx")}
        index_only = sorted(successful - docx_present)
        docx_only = sorted(docx_present - successful)
        if index_only:
            problems.append(f"arm {label}: index reports success but no DOCX on disk for "
                            f"{len(index_only)} uid(s): {' '.join(index_only[:10])}")
        if docx_only:
            problems.append(f"arm {label}: DOCX on disk has no matching success entry in the "
                            f"index for {len(docx_only)} uid(s): {' '.join(docx_only[:10])}")
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
        if el.tag == W + "t" and el.text:
            m = _PREP_DATE_RE.match(el.text)
            if m:
                # Replace only the date value; anything else in the same
                # run (a real content difference) survives untouched.
                el.text = m.group(1) + "<MASKED-DATE>" + el.text[m.end():]
    canon = etree.tostring(root, method="c14n")
    return hashlib.sha256(canon).hexdigest()


def _read_artifact(func, path: Path):
    """Wraps _paras/_fingerprint so a corrupt DOCX is an explicit gate
    failure, not an uncaught BadZipFile/XML-parse traceback (review on
    #589) -- CI diagnostics stay actionable and the process still exits
    non-zero either way."""
    try:
        return func(path)
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as e:
        raise ValueError(f"{path}: unreadable/corrupt DOCX ({type(e).__name__}: {e})") from e


@dataclass(frozen=True)
class ParagraphDelta:
    """One CV whose paragraph text differs between the arms. `added` and
    `removed` hold CV text: main() prints them for a human at a terminal,
    render_gate_controls.py reads only their lengths (#2176)."""
    name: str
    paragraphs_a: int
    paragraphs_b: int
    added: list[str]
    removed: list[str]


@dataclass(frozen=True)
class RenderComparison:
    """Everything one A/B compare found. `guard_problems` non-empty means
    nothing else was computed: the arms were refused, not compared."""
    guard_problems: list[str] = field(default_factory=list)
    common: list[str] = field(default_factory=list)
    paragraph_changed: list[ParagraphDelta] = field(default_factory=list)
    fingerprint_changed: list[str] = field(default_factory=list)
    only_a: list[str] = field(default_factory=list)
    only_b: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """PASS: compared something, and nothing differs."""
        return (not self.guard_problems and bool(self.common) and not self.paragraph_changed
                and not self.fingerprint_changed and not self.only_a and not self.only_b)


def compare_dirs(a_dir: Path, b_dir: Path) -> RenderComparison:
    """Both checks over every docx the two arms share, behind the
    crashed-render guard. Raises ValueError on a corrupt docx (see
    _read_artifact). main() prints this; render_gate_controls.py reads it."""
    problems = _check_render_failures(a_dir, b_dir)
    if problems:
        return RenderComparison(guard_problems=problems)

    a_files = {p.name for p in a_dir.glob("*.docx")}
    b_files = {p.name for p in b_dir.glob("*.docx")}
    paragraph_changed, fingerprint_changed = [], []
    common = sorted(a_files & b_files)
    for name in common:
        pa, pb = _read_artifact(_paras, a_dir / name), _read_artifact(_paras, b_dir / name)
        fa, fb = _read_artifact(_fingerprint, a_dir / name), _read_artifact(_fingerprint, b_dir / name)
        if fa != fb:
            fingerprint_changed.append(name)
        if pa != pb:
            paragraph_changed.append(ParagraphDelta(
                name, len(pa), len(pb),
                added=[x for x in pb if x not in pa],
                removed=[x for x in pa if x not in pb]))
    return RenderComparison(common=common, paragraph_changed=paragraph_changed,
                            fingerprint_changed=fingerprint_changed,
                            only_a=sorted(a_files - b_files), only_b=sorted(b_files - a_files))


def main(argv=None):
    a_dir, b_dir = Path(sys.argv[1]), Path(sys.argv[2])

    try:
        result = compare_dirs(a_dir, b_dir)
    except ValueError as e:
        print(f"FAIL - {e}")
        return 1
    if result.guard_problems:
        print("RENDER FAILURE GUARD TRIPPED -- refusing to compare:")
        for p in result.guard_problems:
            print(f"  - {p}")
        print("\nFAIL - fix the failing arm(s) and re-render before comparing")
        return 1

    if not result.common:
        print("FAIL - no comparable rendered CVs")
        return 1

    print(f"compared          {len(result.common)} CVs")
    print(f"paragraph CHANGED {len(result.paragraph_changed)}")
    print(f"fingerprint CHANGED {len(result.fingerprint_changed)} (styles/numPr/borders/tracked-changes -- "
          f"invisible to the paragraph check)")
    if result.only_a:
        print(f"MISSING in B      {len(result.only_a)}: {' '.join(result.only_a[:8])}")
    if result.only_b:
        print(f"EXTRA in B        {len(result.only_b)}: {' '.join(result.only_b[:8])}")

    for delta in result.paragraph_changed[:10]:
        print(f"\n  {delta.name}  paragraphs {delta.paragraphs_a} -> {delta.paragraphs_b}")
        for x in delta.removed[:3]:
            print(f"    - {x[:150]}")
        for x in delta.added[:3]:
            print(f"    + {x[:150]}")
    if len(result.paragraph_changed) > 10:
        print(f"\n  ... and {len(result.paragraph_changed) - 10} more paragraph-changed CVs")

    print(f"\n{'PASS - zero unexplained deltas' if result.ok else 'FAIL - output differs'}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
