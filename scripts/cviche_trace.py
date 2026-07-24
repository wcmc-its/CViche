#!/usr/bin/env python3
"""Trace one CV record across pipeline stages 2 -> 3b -> 4 -> 6.

The autopsy operation that got re-implemented by hand a dozen times during the
C0ZGFW investigation: given a uid and a record (by element index or a text
token), show what each stage did to it in one view --

    stage 2  : extracted text, element idx, parent
    stage 3b : taxonomy code + confidence + reasoning
    stage 4  : extracted fields + coverage % + unextracted words
    stage 6  : whether/where it rendered, or why it didn't

This is the tool that catches "extracted fine, classified fine, but rendered
wrong (or dropped)" -- the failure shape that a per-stage view hides. It reads
the same on-disk artifacts as scripts/doctor_one.py; it does not run the
pipeline and makes no judgements (is-this-R-or-K stays a human call).

    PYTHONPATH=src python3 scripts/cviche_trace.py <uid> <idx|token> [--outputs-root DIR]
    PYTHONPATH=src python3 scripts/cviche_trace.py --selftest

Selector is an element index ("50.4", "114") if it matches one, else a
case-insensitive substring of the entry text.
"""
import argparse
import json
import sys
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

# stage dir -> the entry-list key that stage writes (first match wins; falls
# back to the first list-valued top-level key so a schema tweak doesn't break us)
STAGES = [
    ("stage_2_entry_extraction", "_entries.json", ("entries",)),
    ("stage_3b_classified_entries", "_classified.json", ("entries", "classified_entries")),
    ("stage_4_field_extraction", "_fields.json", ("entries",)),
]
DOCX_STAGE = ("stage_6_wcm_documents", "_wcm.docx")


def norm_key(idx):
    """Canonical string key for an element index (int 114, float 114.1, str '114.10').

    JSON parses "114.10" to the float 114.1, so a stored float is inherently
    lossy -- but stage 2 writes sub-row indices as STRINGS, so the artifacts we
    read keep "114.10" intact. We only normalise ints/floats that arrive bare.
    """
    if isinstance(idx, str):
        return idx.strip()
    if isinstance(idx, float) and idx.is_integer():
        return str(int(idx))
    return str(idx)


def load_entries(outputs_root, uid, subdir, suffix, keys):
    """Return the entry list for one stage, or [] if the file is absent."""
    path = Path(outputs_root) / subdir / f"{uid}{suffix}"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    for k in keys:
        if isinstance(data.get(k), list):
            return data[k]
    for v in data.values():  # fallback: first list-valued key
        if isinstance(v, list):
            return v
    return []


def index_by_key(entries):
    out = {}
    for e in entries:
        out.setdefault(norm_key(e.get("element_idx_start")), []).append(e)
    return out


def docx_blocks(path):
    """(section_header, text) for each body block in document order."""
    doc = Document(str(path))
    out, section = [], "(top)"
    for child in doc.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            t = Paragraph(child, doc).text.strip()
            if not t:
                continue
            if t.isupper() and len(t) < 70 and any(c.isalpha() for c in t):
                section = t
            out.append((section, t))
        elif tag == "tbl":
            for row in Table(child, doc).rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    out.append((section, " | ".join(dict.fromkeys(cells))))
    return out


# Identifier-ish fields only. Role/teaching_role are descriptors ("Director",
# "Lecturer") that collide with dozens of unrelated lines and produce false
# "rendered under X" hits -- excluded deliberately.
TITLE_FIELDS = ("title", "program_name", "activity_title", "event_name", "award_name")


def _clean(s):
    """Lowercased, separator- and punctuation-stripped, whitespace-collapsed."""
    s = str(s).replace("|", " ").replace("\t", " ")
    return " ".join("".join(c if c.isalnum() else " " for c in s).split()).lower()


def render_needles(entry_text, fields=None):
    """Candidate fragments to search for in the rendered doc, most-identifying first.

    5c/5d reformat text and cells hold separators, so equality on a whole line
    misfires (a clean R entry can read NOT-FOUND when it did render). We try the
    stage-4 title-ish field first -- the cleanest identifier -- then the longest
    alnum line. Separators/punctuation are stripped from both sides so
    "shm 2025 POCUS mentor | |" matches a rendered "... POCUS mentor ...".
    """
    cands = []
    for k in TITLE_FIELDS:
        v = (fields or {}).get(k)
        if v and len(_clean(v)) >= 6:
            cands.append(_clean(v))
    best = ""
    for raw in str(entry_text).replace("\t", "\n").split("\n"):
        s = _clean(raw)
        if "institution location" in s or s.startswith("name of"):
            continue
        if len(s) > len(best) and len(s) >= 8:
            best = s
    if best:
        cands.append(best[:60])
    # de-dup, keep order
    return list(dict.fromkeys(c for c in cands if c))


def is_header_shaped(entry_text):
    """True when every content line is a template column-label row.

    Such an entry has no non-header needle, and its full text would match the
    template's own scaffolding header -- so a leak (source header rendered via
    the :76 fallback) is indistinguishable from the template scaffold by
    fragment matching. The honest answer for these is 'can't tell', not 'absent'.
    """
    lines = [_clean(ln) for ln in str(entry_text).replace("\t", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    return bool(lines) and all(
        "institution location" in ln or ln.startswith("name of") or ln == "organization date"
        for ln in lines
    )


def locate_render(blocks, entry_text, fields=None):
    """Where (if anywhere) the entry surfaces in the rendered docx.

    Returns (status, section, count). status is:
      'rendered'      -- a distinctive fragment was found (section, count set)
      'absent'        -- no fragment matched; a hint to VERIFY, not proof of a
                         drop, since 5c/5d reformatting can defeat matching
      'indeterminate' -- header-shaped entry; matcher cannot separate a leak
                         from template scaffolding (see is_header_shaped)
    """
    needles = render_needles(entry_text, fields)
    if not needles:
        return ("indeterminate" if is_header_shaped(entry_text) else "absent"), None, 0
    cleaned = [(sec, _clean(t)) for sec, t in blocks]
    for needle in needles:
        hits = [sec for sec, t in cleaned if needle in t]
        if hits:
            return "rendered", hits[0], len(hits)
    return "absent", None, 0


def find_matches(stage2, selector):
    """Entries the selector picks: exact idx match if any, else substring on text."""
    by_idx = index_by_key(stage2)
    if selector in by_idx:
        return by_idx[selector]
    sel = selector.lower()
    return [e for e in stage2 if sel in str(e.get("text", "")).lower()]


def trace(outputs_root, uid, selector):
    loaded = {sub: load_entries(outputs_root, uid, sub, suf, keys)
              for sub, suf, keys in STAGES}
    s2 = loaded["stage_2_entry_extraction"]
    if not s2:
        return f"no stage-2 entries for uid={uid} under {outputs_root}"
    idx3b = index_by_key(loaded["stage_3b_classified_entries"])
    idx4 = index_by_key(loaded["stage_4_field_extraction"])

    docx_path = Path(outputs_root) / DOCX_STAGE[0] / f"{uid}{DOCX_STAGE[1]}"
    blocks = docx_blocks(docx_path) if docx_path.exists() else []

    matches = find_matches(s2, selector)
    if not matches:
        return f"no entry matched {selector!r} for uid={uid}"

    lines = [f"=== {uid}: {len(matches)} match(es) for {selector!r} ===\n"]
    for e in matches:
        key = norm_key(e.get("element_idx_start"))
        lines.append(f"[{key}]  parent={e.get('parent_idx')}  type={e.get('element_type')}")
        lines.append(f"  s2 text : {' '.join(str(e.get('text','')).split())[:150]}")

        c = next(iter(idx3b.get(key, [])), None)
        if c:
            lines.append(f"  s3b     : code={c.get('taxonomy_code') or c.get('code')}"
                         f"  conf={c.get('taxonomy_confidence') or c.get('confidence')}"
                         f"  hier={c.get('hierarchy')}")
            r = c.get("classification_reasoning") or c.get("reasoning")
            if r:
                lines.append(f"            reason: {str(r)[:110]}")
        else:
            lines.append("  s3b     : (not found)")

        f = next(iter(idx4.get(key, [])), None)
        if f:
            cov = f.get("extraction_coverage") or {}
            pct = cov.get("extraction_coverage_percent") if isinstance(cov, dict) else None
            lines.append(f"  s4 fields: {json.dumps(f.get('extracted_fields') or {})[:150]}")
            if pct is not None:
                un = (cov.get("unextracted_words") or [])[:8]
                lines.append(f"            coverage={pct}%  unextracted={un}")
        else:
            lines.append("  s4 fields: (not found)")

        if blocks:
            status, sec, n = locate_render(blocks, e.get("text", ""),
                                           (f or {}).get("extracted_fields"))
            code = (c or {}).get("taxonomy_code") or (c or {}).get("code")
            if status == "rendered":
                lines.append(f"  s6 render: {n}x under {sec!r}")
            elif status == "indeterminate":
                lines.append("  s6 render: INDETERMINATE (header-shaped; matcher "
                             "can't separate a leak from template scaffold)")
            else:
                why = " (code T = drop)" if code == "T" else " (verify: matcher may miss reformatted text)"
                lines.append(f"  s6 render: NOT FOUND{why}")
        lines.append("")
    return "\n".join(lines)


def _selftest():
    """Synthetic-artifact check of the join + render-locate logic. No files, no LLM."""
    assert norm_key(114) == "114" and norm_key("114.10") == "114.10"
    assert norm_key(50.0) == "50"

    s2 = [{"element_idx_start": "50.4", "text": "Bugando POCUS Program\nDevelop curriculum",
           "parent_idx": 50, "element_type": "table_row"},
          {"element_idx_start": "114.1", "text": "SGIM workshop", "parent_idx": 114}]
    # idx selector wins over substring
    assert [m["element_idx_start"] for m in find_matches(s2, "50.4")] == ["50.4"]
    # substring selector
    assert [m["element_idx_start"] for m in find_matches(s2, "sgim")] == ["114.1"]
    # join by normalized key
    idx = index_by_key([{"element_idx_start": 114.0, "code": "K4"}])
    assert idx["114"][0]["code"] == "K4"
    # render locate: distinctive line found under its section
    blocks = [("EDUCATION", "• Develop curriculum for residents"),
              ("INVITED", "• SGIM workshop 2024")]
    status, sec, n = locate_render(blocks, "Bugando POCUS Program\nDevelop curriculum for residents")
    assert status == "rendered" and n == 1 and sec == "EDUCATION", (status, sec, n)
    # the real C0ZGFW false-negative: separators + reformat must not hide a hit.
    # "shm 2025 POCUS mentor | |" should match a rendered "... POCUS mentor ..."
    b2 = [("INVITED", "1. POCUS mentor — SHM — 2025")]
    status, sec, n = locate_render(b2, "shm 2025 POCUS mentor | |", {"title": "POCUS mentor"})
    assert status == "rendered" and n == 1, (status, sec, n)
    # header-label line yields no needle
    assert render_needles("Title | Institution/Location | Dates") == []
    # a header-shaped entry is INDETERMINATE, not a confident 'absent' (the
    # "harmless header rows" trap: the matcher must say it cannot tell)
    status, _, _ = locate_render([("X", "1. Role(s)/Position — Institution/Location — Dates")],
                                 "Role(s)/Position | Institution/Location | Dates")
    assert status == "indeterminate", status
    # a genuinely-absent normal entry reports 'absent'
    status, _, _ = locate_render([("X", "something else")], "Unique Grant Title 2024")
    assert status == "absent", status
    # a generic role descriptor must NOT be used as a needle (the 50.4 false
    # "22x under wrong section" regression: role='Director' matched everywhere)
    status, sec, n = locate_render(
        [("POSITIONS", "Director of Something"), ("POSITIONS", "Director of Another")],
        "Bugando POCUS Program\nrun the program", {"program_name": "Bugando POCUS Program", "role": "Director"})
    assert (status, n) == ("absent", 0), (status, sec, n)
    print("selftest ok")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("uid", nargs="?", help="document uid, e.g. C0ZGFW")
    ap.add_argument("selector", nargs="?", help="element index (50.4) or a text token")
    ap.add_argument("--outputs-root", default="src/unified_pipeline/outputs",
                    help="dir holding the stage_* subdirs")
    ap.add_argument("--selftest", action="store_true", help="run the self-check and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        _selftest()
        return 0
    if not args.uid or not args.selector:
        ap.error("need <uid> and <selector> (or --selftest)")
    print(trace(args.outputs_root, args.uid, args.selector))
    return 0


if __name__ == "__main__":
    sys.exit(main())
