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

import json
import re
from typing import Callable, Dict, List, Optional, Tuple

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
            # entries = children only; the header is context (surfaced by the punt's
            # preceding-row fetch), never recoded -- it is not a content row.
            key = (_idx(children[0]), _idx(children[-1]))
            if key not in seen:
                seen.add(key)
                blocks.append({"reason": "orphaned-header", "entries": children,
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


def build_punt_package(entries: List[Dict], block: Dict, subject_name: Optional[str] = None) -> str:
    """Restore the context the per-entry classifier never saw, for the LLM judge:
    the profile subject's identity, the subject's own education (a self-vs-other
    anchor), the recovered sub-header ancestry, and the block at full width."""
    ordered = sorted(entries, key=_idx)
    start = _idx(block["entries"][0])

    # recovered sub-header ancestry: header-like rows in the ~20 rows above the
    # block (wide enough to reach grouping headers like "Trainees (laboratory)",
    # not just the direct parent), nearest last.
    window = [e for e in ordered if _idx(e) < start][-20:]
    # genuine orphaned sub-headers land in the T bucket; prefer those (drops a real
    # content row like "X (editorial board member)" that the ")"-rule would catch).
    headers = [e for e in window if _is_header_like(e) and _family(e.get("taxonomy_code")) == "T"][-4:]
    if not headers:
        headers = [e for e in window if _is_header_like(e)][-2:]
    # subject's own degrees: the B family is reliably the subject, so it anchors
    # "these ARE the subject" against the third parties in the block.
    education = [e for e in ordered if _family(e.get("taxonomy_code")) == "B"][:3]

    lines: List[str] = []
    if subject_name:
        lines.append(f"PROFILE SUBJECT: {subject_name}")
    section = " > ".join(str(x) for x in (block["entries"][0].get("hierarchy") or []))
    if section:
        lines.append(f"SECTION (as classified): {section}")
    if education:
        lines.append("SUBJECT'S OWN EDUCATION (these ARE the subject -- contrast against the block):")
        for e in education:
            lines.append(f"   [{e.get('taxonomy_code')}] {(e.get('text') or '').replace(chr(9), ' ').strip()[:140]}")
    lines.append("\nRECOVERED SUB-HEADERS above the block (nearest last; the per-entry "
                 "classifier never saw these):")
    lines += [f"   [{e.get('taxonomy_code')}] {(e.get('text') or '').replace(chr(9), ' ').strip()[:90]}" for e in headers]
    lines.append("\nBLOCK (a contiguous sibling list; current per-entry codes shown -- they disagree):")
    for e in block["entries"]:
        raw = (e.get("text", "") or "").replace("\t", " ⇥ ")
        lines.append(f"   {str(e.get('taxonomy_code')):4} | {raw[:300]}")
    lines.append("\nWHOSE FACT IS EACH ROW -- the profile SUBJECT's own, or a THIRD PARTY's "
                 "(mentee / co-author / co-PI)? If these form one coherent subsection, give the "
                 "single taxonomy family + evidence; if genuinely mixed, say leave-it.")
    return "\n".join(lines)


# --- LLM repair (the "punt") ------------------------------------------------
# The detector never decides; it hands a flagged block + recovered context to a
# judge. The prompt is anchored on the GENERAL principle -- whose fact is this,
# the profile subject's own or a third party's -- not on "postdoc"/"mentee", so
# it generalizes to co-authors / co-PIs. Framed neutrally so a subject's genuine
# training/position is left alone.
_VALID_CODE = re.compile(r"^[A-Z]\d{0,2}[A-Z]?$")

_REPAIR_LEGEND = (
    "  N3A = a person the SUBJECT currently mentors      N3B = a person the SUBJECT mentored (ended)\n"
    "  C   = the SUBJECT's OWN postdoctoral training (a residency/fellowship the subject completed)\n"
    "  D1/D2/D3 = the SUBJECT's OWN academic / hospital / other position\n"
    "  B1  = the SUBJECT's OWN degree"
)


def _build_repair_prompt(punt: str) -> str:
    return (
        "You audit one person's CV (\"the SUBJECT\"). The rows below sit under a single\n"
        "section but were auto-classified one row at a time WITHOUT their section header,\n"
        "so a row describing a THIRD PARTY the subject relates to (a mentee, co-author, or\n"
        "co-PI) may have been coded as if it were the SUBJECT's own record.\n\n"
        "For each row decide WHOSE fact it is, then give the correct code. Do NOT assume the\n"
        "rows are mentees -- if a row is genuinely the subject's own training/position, keep\n"
        "its current code. Use the recovered header above the block as your main evidence.\n\n"
        "Codes:\n" + _REPAIR_LEGEND + "\n\n"
        "------------------------------------------------------------\n"
        + punt +
        "\n------------------------------------------------------------\n\n"
        "Return STRICT JSON only, no prose:\n"
        '{"coherent": <true if these form one subsection>,\n'
        ' "verdict": "subject" | "third_party" | "mixed",\n'
        ' "codes": [<one taxonomy code per row, in the order shown>],\n'
        ' "confidence": <0.0-1.0>,\n'
        ' "evidence": "<one short sentence>"}'
    )


def _parse_repair_response(text: str) -> Dict:
    """Tolerant parse: strip code fences, grab the outermost JSON object."""
    t = (text or "").strip()
    if "```" in t:
        t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(t[start:end + 1])
    except (ValueError, TypeError):
        return {}


def repair_block(block: Dict, punt: str, llm: Callable[[str], str], min_confidence: float) -> Dict:
    """Ask the judge; return a verdict with an ``applied`` flag. Conservative:
    only apply when coherent, confident, one valid code per row."""
    parsed = _parse_repair_response(llm(_build_repair_prompt(punt)))
    codes = parsed.get("codes") or []
    n = len(block["entries"])
    parsed["applied"] = bool(
        parsed.get("coherent") is True
        and float(parsed.get("confidence", 0) or 0) >= min_confidence
        and len(codes) == n
        and all(isinstance(c, str) and _VALID_CODE.match(c) for c in codes)
    )
    parsed["codes"] = codes
    return parsed


def apply_block_coherence_corrections(
    entries: List[Dict],
    llm: Optional[Callable[[str], str]] = None,
    apply: bool = False,
    min_confidence: float = 0.8,
    subject_name: Optional[str] = None,
) -> Tuple[List[Dict], Dict]:
    """Notice incoherent / orphaned-header blocks; repair via LLM when ``apply``.

    With ``apply=False`` (default) this is a pure no-op on ``entries`` -- it only
    records what it noticed, so it is safe to wire into the post-3b chain now.
    When ``apply`` and ``llm`` are given, each flagged block is punted to the
    judge and its codes overridden in place only on a confident, coherent verdict.
    """
    blocks = _detect(entries)
    corrections_made = 0
    flagged = []
    for b in blocks:
        punt = build_punt_package(entries, b, subject_name)
        rec = {"reason": b["reason"], "hierarchy": b["hierarchy"],
               "families": b["families"], "n": len(b["entries"]), "punt": punt}
        if apply and llm is not None:
            verdict = repair_block(b, punt, llm, min_confidence)
            rec["verdict"] = {k: verdict.get(k) for k in ("coherent", "verdict", "confidence", "applied", "evidence")}
            if verdict["applied"]:
                for e, new in zip(b["entries"], verdict["codes"]):
                    old = e.get("taxonomy_code")
                    if new and new != old:
                        e["taxonomy_code"] = new
                        e["taxonomy_confidence"] = verdict.get("confidence", min_confidence)
                        e["block_coherence_correction"] = {"original_code": old, "evidence": verdict.get("evidence", "")}
                        e["classification_reasoning"] = f"[block-coherence {old}->{new}] " + (e.get("classification_reasoning") or "")
                        corrections_made += 1
        flagged.append(rec)

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
    judge1 = lambda _p: '{"coherent": true, "verdict": "third_party", "codes": ["N3A"], "confidence": 0.9, "evidence": "x"}'
    _, s4 = apply_block_coherence_corrections(orphaned, llm=judge1, apply=True)
    assert orphaned[0]["taxonomy_code"] == "T", "the header row must NOT be recoded"
    assert orphaned[1]["taxonomy_code"] == "N3A"

    # repair apply path with a stub judge (offline; live Bedrock is step B).
    incoherent2 = [
        E(10, "N3B", "Brian Kadera, MD\t2011 - 2013\tCurrent position: Assistant Professor, UCLA"),
        E(11, "C",   "Razmik Ghukasayan, MD\t2019 - present\tCurrent position: Senior Resident, UCLA"),
        E(12, "D2",  "Stephanie Kim, MD\t2017 - 2019\tCurrent position: Surgeon, Kaiser Permanente"),
    ]
    judge_mentees = lambda _p: '{"coherent": true, "verdict": "third_party", "codes": ["N3B","N3A","N3B"], "confidence": 0.95, "evidence": "named trainees under a mentoring header"}'
    _, sa = apply_block_coherence_corrections(incoherent2, llm=judge_mentees, apply=True)
    assert sa["corrections_made"] == 2, sa["corrections_made"]  # C->N3A, D2->N3B; N3B unchanged
    assert [e["taxonomy_code"] for e in incoherent2] == ["N3B", "N3A", "N3B"]

    # low confidence / leave-it must NOT mutate
    incoherent3 = [dict(e, taxonomy_code=c) for e, c in zip(incoherent2, ["N3B", "C", "D2"])]
    judge_unsure = lambda _p: '{"coherent": false, "verdict": "mixed", "codes": [], "confidence": 0.2, "evidence": "unclear"}'
    _, sb = apply_block_coherence_corrections(incoherent3, llm=judge_unsure, apply=True)
    assert sb["corrections_made"] == 0
    assert [e["taxonomy_code"] for e in incoherent3] == ["N3B", "C", "D2"]

    print("block_coherence_corrector self-check OK")


if __name__ == "__main__":
    _demo()
