"""
Block-coherence corrector (issue #198).

Notices when a contiguous block of sibling entries was scattered across
incompatible taxonomy families -- the fingerprint of lost section context.
A body-styled sub-header the segmenter flattened (e.g. "Postdoctoral Fellows
(direct mentor)") survives only as a stray ``T`` entry; the per-entry
classifier, blind to that governing header, attributes a *third party's* role
(a mentee's "Current position: Senior Resident") to the profile SUBJECT and
codes it C/D instead of N3.

This module only NOTICES and assembles a punt package. The judgment -- is this
one coherent subsection, and which family -- is deferred to an LLM (wired
separately), so we never hard-code "postdoc"/"mentee" keywords. The detector is
deliberately dumb and may over-flag; the LLM is the precision stage.

Two structural smells (no thresholds):
  (a) sibling-incoherence -- a contiguous run of person-records (name + date
      range) under one hierarchy node, split across >=2 taxonomy families.
  (b) orphaned header -- a short header-like row the classifier dumped into
      ``T`` with content entries stranded under it (catches singletons and the
      uniform-wrong case that (a) cannot, since it needs disagreement).

# ponytail: detection only; apply/LLM repair gated off until the punt is wired.
"""

import re
from typing import Dict, List, Optional, Tuple

_YEAR_RANGE = re.compile(r"(19|20)\d{2}\s*[-–—]\s*((present|current|ongoing)\b|(19|20)\d{2})", re.I)
_NAME_OPENER = re.compile(r"^\s*(?:\d{1,2}[.\)]\s*)?[A-Z][a-z]+\s+(?:[A-Z][a-z'’\-]+|[A-Z]\.)")

_MIN_RUN = 3  # a "block" needs at least this many sibling person-records


def _family(code: str) -> str:
    """Top-level taxonomy family: leading letter. N3A->N, D1->D, C->C."""
    m = re.match(r"[A-Z]", (code or "").strip())
    return m.group(0) if m else "?"


def _idx(e: Dict) -> int:
    try:
        return int(e.get("element_idx_start", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _hkey(e: Dict) -> str:
    return " > ".join(str(x) for x in (e.get("hierarchy") or []))


def _is_person_record(text: str) -> bool:
    t = (text or "").replace("\t", " ")
    return bool(_NAME_OPENER.match(t) and _YEAR_RANGE.search(t))


def _is_header_like(e: Dict) -> bool:
    """Short, low-structure row that reads like a (mis-bucketed) sub-heading."""
    t = (e.get("text") or "").strip()
    if not t or _is_person_record(t):
        return False
    return len(t) <= 60 and not _YEAR_RANGE.search(t) and (
        t.endswith(":") or t.endswith(")") or _family(e.get("taxonomy_code")) == "T"
    )


def _detect(entries: List[Dict]) -> List[Dict]:
    """Return suspect blocks: {reason, entries, families, hierarchy}."""
    ordered = sorted(entries, key=_idx)
    blocks: List[Dict] = []
    seen = set()  # (start_idx, end_idx) to dedupe overlap between smells

    # (a) sibling-incoherence over contiguous person-record runs
    run: List[Dict] = []
    def flush(run):
        if len(run) >= _MIN_RUN:
            fams = {_family(e.get("taxonomy_code")) for e in run}
            if len(fams) >= 2:
                key = (_idx(run[0]), _idx(run[-1]))
                if key not in seen:
                    seen.add(key)
                    blocks.append({"reason": "sibling-incoherence", "entries": list(run),
                                   "families": sorted(fams), "hierarchy": _hkey(run[0])})
    for e in ordered:
        if _is_person_record(e.get("text", "")) and (not run or _hkey(e) == _hkey(run[-1])):
            run.append(e)
        else:
            flush(run)
            run = [e] if _is_person_record(e.get("text", "")) else []
    flush(run)

    # (b) orphaned T-coded header followed by stranded content (shape-agnostic)
    for i, e in enumerate(ordered):
        if not (_is_header_like(e) and _family(e.get("taxonomy_code")) == "T"):
            continue
        # bound to the contiguous person-record run under the header -- otherwise
        # a flattened hierarchy makes "children" swallow the rest of the document.
        # ponytail: people-list scoped; fully shape-agnostic orphan detection is future work.
        children = []
        for f in ordered[i + 1:]:
            if _hkey(f) != _hkey(e) or not _is_person_record(f.get("text", "")):
                break
            children.append(f)
        if len(children) >= 1:
            key = (_idx(e), _idx(children[-1]))
            if key not in seen:
                seen.add(key)
                blocks.append({"reason": "orphaned-header", "entries": [e] + children,
                               "families": sorted({_family(c.get("taxonomy_code")) for c in children}),
                               "hierarchy": _hkey(e), "header": (e.get("text") or "").strip()})

    # one punt per region: drop a block whose idx span overlaps a kept one
    # (the mentee block is caught by both smells). Prefer earliest start, then largest.
    blocks.sort(key=lambda b: (_idx(b["entries"][0]), -len(b["entries"])))
    kept: List[Dict] = []
    for b in blocks:
        s, e = _idx(b["entries"][0]), _idx(b["entries"][-1])
        if any(not (e < _idx(k["entries"][0]) or s > _idx(k["entries"][-1])) for k in kept):
            continue
        kept.append(b)
    return kept


def build_punt_package(entries: List[Dict], block: Dict) -> str:
    """The context the per-entry classifier never saw -- for the LLM judge."""
    ordered = sorted(entries, key=_idx)
    start = _idx(block["entries"][0])
    preceding = [e for e in ordered if _idx(e) < start][-6:]
    headers = [e for e in preceding if _is_header_like(e)] or preceding[-2:]
    lines = ["PRECEDING CONTEXT (header-like rows the per-entry classifier ignored):"]
    lines += [f"   [{e.get('taxonomy_code')}] {(e.get('text') or '').strip()[:70]}" for e in headers]
    lines.append("\nBLOCK (a contiguous sibling list; current per-entry codes disagree):")
    for e in block["entries"]:
        raw = (e.get("text", "") or "").replace("\t", " ⇥ ")
        lines.append(f"   {str(e.get('taxonomy_code')):4} | {raw[:88]}")
    lines.append("\nWHOSE FACT IS EACH ROW -- the profile SUBJECT's own, or a THIRD PARTY's "
                 "(mentee / co-author / co-PI)? If these form one coherent subsection, give the "
                 "single taxonomy family + evidence; if genuinely mixed, say leave-it.")
    return "\n".join(lines)


def apply_block_coherence_corrections(
    entries: List[Dict], llm: Optional[object] = None, apply: bool = False
) -> Tuple[List[Dict], Dict]:
    """Notice incoherent / orphaned-header blocks. Repair is LLM-gated (apply).

    With ``apply=False`` (default) this is a pure no-op on ``entries`` -- it only
    records what it noticed, so it is safe to wire into the post-3b chain now.
    """
    blocks = _detect(entries)
    flagged = [{
        "reason": b["reason"],
        "hierarchy": b["hierarchy"],
        "families": b["families"],
        "n": len(b["entries"]),
        "punt": build_punt_package(entries, b),
    } for b in blocks]

    corrections_made = 0
    if apply and llm is not None:
        raise NotImplementedError("LLM repair not wired yet (issue #198, step 3)")

    stats = {
        "entries_checked": len(entries),
        "blocks_flagged": len(flagged),
        "corrections_made": corrections_made,
        "flagged": flagged,
    }
    return entries, stats


def _demo() -> None:
    """Self-check: flags an incoherent person-record block + an orphaned T-header;
    leaves a coherent block alone."""
    def E(i, code, text, hier="PROFESSIONAL ACTIVITIES"):
        return {"element_idx_start": i, "taxonomy_code": code, "text": text, "hierarchy": [hier]}

    incoherent = [
        E(10, "N3B", "Brian Kadera, MD\t2011 - 2013\tCurrent position: Assistant Professor, UCLA"),
        E(11, "C",   "Razmik Ghukasayan, MD\t2019 - present\tCurrent position: Senior Resident, UCLA"),
        E(12, "D2",  "Stephanie Kim, MD\t2017 - 2019\tCurrent position: Surgeon, Kaiser Permanente"),
    ]
    _, s = apply_block_coherence_corrections(incoherent)
    assert s["blocks_flagged"] >= 1, "should flag the incoherent person-record block"
    assert any(f["reason"] == "sibling-incoherence" for f in s["flagged"])

    coherent = [
        E(20, "D1", "Assistant Professor of Surgery\t2009 - 2015\tUCLA"),
        E(21, "D1", "Associate Professor of Surgery\t2015 - 2018\tUCLA"),
        E(22, "D1", "Professor of Surgery\t2018 - present\tUCLA"),
    ]
    _, s2 = apply_block_coherence_corrections(coherent)
    assert not any(f["reason"] == "sibling-incoherence" for f in s2["flagged"]), \
        "a homogeneous D block must not be flagged for incoherence"

    orphaned = [
        E(30, "T",  "Postdoctoral Fellows (direct mentor)"),
        E(31, "C",  "Amanda Labora, MD\t2021 - present\tCurrent position: Senior Resident, UCLA"),
    ]
    _, s3 = apply_block_coherence_corrections(orphaned)
    assert any(f["reason"] == "orphaned-header" for f in s3["flagged"]), \
        "a T-coded header with stranded children must be flagged"

    print("block_coherence_corrector self-check OK")


if __name__ == "__main__":
    _demo()
