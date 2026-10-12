#!/usr/bin/env python3
"""Offline replay of stored stage-2 runs, and the #1226 batch-edge carry gate.

No LLM call is made by any subcommand.

    replay <run_dir>... --out DIR [--src SRC] [--fail-later-batches] [--carry-max N]
        Re-run stage 2 on each run's stored docx and stage-1b JSON, answering
        every call from the run's own prompt logs. A prompt with no stored
        reply gets a sentinel exception, which drives the per-batch ``except``
        path: it is counted, never answered. ``--fail-later-batches`` makes
        every batch after a section's first raise the same way (the
        forced-failure arm). ``--src`` picks the code under test: point it at
        another checkout's ``src`` to replay the base arm. ``--carry-max 0``
        turns the #1226 carry off in code that has one. Writes
        ``DIR/<UID>.json`` per run.

    same --a DIR --b DIR
        Relocation gate: every batch prompt and every entry identical in two
        ``replay`` outputs. Exits 1 on any difference.

    gate --base DIR --new DIR --base-fail DIR --new-fail DIR
        The #1226 offline gate over four ``replay`` outputs, per run:
        - every section's first batch prompt, and every single-batch
          section's prompt, is byte-identical in the base and new arms;
        - no entry changes outside a carried region (from the first new-arm
          batch whose prompt differs to the section end);
        - with every later batch failing in both arms, the new arm keeps
          coverage.unaccounted_indices and loses, gains and double-claims no
          content line against the base arm.
        Sections the base arm cannot fully replay (a stored prompt the base
        code no longer sends) are excluded and counted. Exits 1 when a
        criterion fails, 2 when there is nothing to compare.

    census <run_dir>... [--stage-root ROOT --docx-root DIR ...]
        Batch edges today, and the carry each would take, from stored entries:
        no prompt logs needed.

A <run_dir> holds input/<UID>.docx, outputs/<UID>_hierarchy_mapped.json,
outputs/<UID>_entries.json and prompt_logs/ (the S3 run-artifact layout). A
--stage-root holds stage_1b_hierarchy_mapping/ and stage_2_entry_extraction/
(the local farm layout); its docx are found by file name under --docx-root.
stdout names runs by uid and prints counts only, never CV text. The per-run
JSON that ``replay`` writes holds entry text: keep it out of the repository.
"""

import argparse
import collections
import contextlib
import hashlib
import io
import json
import logging
import re
import sys
import zipfile
from pathlib import Path

_DEFAULT_SRC = Path(__file__).resolve().parents[1] / "src"
STAGE = "stage_2"
_CALL_LOG = re.compile(rf"_{STAGE}_([0-9a-f]{{12}})(_RESPONSE)?\.json$")
_PROMPT_INDEX = re.compile(r"^\[([^\]]+)\] ", re.MULTILINE)
_STRUCTURAL = ("header", "break")


class ReplayMiss(Exception):
    """A prompt the stored run never sent: no reply to give."""


class ForcedFailure(Exception):
    """The forced-failure arm's stand-in for a failed batch call."""


def _import_stage2(src: Path):  # noqa: ANN202 -- the module object
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from unified_pipeline import stage_2_entry_extraction as stage2

    return stage2


# ----------------------------------------------------------------- run layout

def _run_files(run_dir: Path) -> tuple[str, Path, Path, Path]:
    """(uid, docx, stage-1b JSON, stored entries JSON) for an S3-layout run."""
    entries = next((run_dir / "outputs").glob("*_entries.json"))
    uid = entries.name.split("_")[0]
    docx = run_dir / "input" / f"{uid}.docx"
    return uid, docx, run_dir / "outputs" / f"{uid}_hierarchy_mapped.json", entries


def stored_replies(log_dir: Path) -> tuple[dict[str, list[dict]], int]:
    """Stored stage-2 replies keyed by the exact user prompt, in log order, and
    the number of calls that have both a prompt and a reply."""
    prompts, replies = {}, {}
    for path in sorted(log_dir.glob(f"*_{STAGE}_*.json")):
        match = _CALL_LOG.search(path.name)
        if not match:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        (replies if match.group(2) else prompts)[match.group(1)] = data
    by_prompt = collections.defaultdict(list)
    paired = 0
    for log_id, prompt in prompts.items():
        if log_id not in replies:
            continue
        response = replies[log_id]["response"]
        usage = response.get("usage", {})
        by_prompt[prompt["messages"][-1]["content"]].append({
            "content": response["choices"][0]["message"]["content"],
            "cost": response.get("cost", 0.0),
            "total_tokens": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        })
        paired += 1
    return dict(by_prompt), paired


# ----------------------------------------------------------------- replay

class _Replayer:
    """call_llm and detect_entries_for_section stand-ins for one run, plus
    what they saw: each section's range and each batch's prompt."""

    def __init__(self, replies: dict[str, list[dict]], fail_later_batches: bool) -> None:
        self.replies = {prompt: list(answers) for prompt, answers in replies.items()}
        self.fail_later_batches = fail_later_batches
        self.sections: list[dict] = []
        self.served = self.misses = self.forced = 0

    def call_llm(self, stage: str, messages: list, response_format: object = None, **_kw: object) -> dict:
        prompt = messages[-1]["content"]
        listed = _PROMPT_INDEX.findall(prompt)
        batch = {"first": listed[0] if listed else None, "size": len(listed),
                 "sha": hashlib.sha1(prompt.encode()).hexdigest()[:16], "outcome": "served"}
        batches = self.sections[-1]["batches"]
        batches.append(batch)
        if self.fail_later_batches and len(batches) > 1:
            self.forced += 1
            batch["outcome"] = "forced_failure"
            raise ForcedFailure("forced-failure arm")
        answers = self.replies.get(prompt)
        if not answers:
            self.misses += 1
            batch["outcome"] = "miss"
            raise ReplayMiss("no stored reply for this prompt")
        self.served += 1
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def wrap_detect(self, real):  # noqa: ANN001, ANN202 -- wraps a function, returns one
        def detect(section_hierarchy: list, doc_elements: list, start: int, end: int, *args: object,
                   **kwargs: object) -> tuple:
            self.sections.append({"start": start, "end": end, "batches": []})
            return real(section_hierarchy, doc_elements, start, end, *args, **kwargs)
        return detect


def replay_run(stage2, run_dir: Path, out_dir: Path, fail_later_batches: bool) -> dict:  # noqa: ANN001 -- the module
    """Replay one run and write its record to ``out_dir/<UID>.json``."""
    uid, docx, hierarchy_json, stored_entries_path = _run_files(run_dir)
    replies, paired = stored_replies(run_dir / "prompt_logs")
    replayer = _Replayer(replies, fail_later_batches)
    real_call, real_detect, real_om = stage2.call_llm, stage2.detect_entries_for_section, stage2.OutputManager
    scratch = out_dir / "_stage2_outputs"

    def output_manager(input_path: str, base_output_dir: object = None) -> object:
        return real_om(input_path, base_output_dir=base_output_dir or scratch)

    stage2.call_llm = replayer.call_llm
    stage2.detect_entries_for_section = replayer.wrap_detect(real_detect)
    stage2.OutputManager = output_manager
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            output, _ = stage2.run_stage_2(str(docx), str(hierarchy_json), workers=1)
    finally:
        stage2.call_llm, stage2.detect_entries_for_section, stage2.OutputManager = real_call, real_detect, real_om
    stored = json.loads(stored_entries_path.read_text(encoding="utf-8"))["entries"]
    record = {
        "uid": uid,
        "fail_later_batches": fail_later_batches,
        "stored_calls": paired,
        "served": replayer.served,
        "misses": replayer.misses,
        "forced_failures": replayer.forced,
        "matches_stored_entries": _canonical(output["entries"]) == _canonical(stored),
        "sections": replayer.sections,
        "coverage": output["coverage"],
        "batch_carry": output.get("batch_carry"),
        "entries": output["entries"],
    }
    (out_dir / f"{uid}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def _canonical(entries: list[dict]) -> collections.Counter:
    return collections.Counter(json.dumps(e, sort_keys=True) for e in entries)


# ----------------------------------------------------------------- gate

def _main_index(idx: object) -> int | None:
    text = str(idx).split(".")[0]
    return int(text) if text.lstrip("-").isdigit() else None


def _entry_lines(entry: dict) -> list[str]:
    """The element keys a content entry covers: a paragraph range, or a row
    span inside one table."""
    start, end = entry["element_idx_start"], entry["element_idx_end"]
    if isinstance(start, int) and isinstance(end, int):
        return [str(i) for i in range(start, end + 1)]
    start_parent, _, start_row = str(start).partition(".")
    end_parent, _, end_row = str(end).partition(".")
    if start_parent == end_parent and start_row.isdigit() and end_row.isdigit() and int(end_row) >= int(start_row):
        return [f"{start_parent}.{r}" for r in range(int(start_row), int(end_row) + 1)]
    return [str(start)]


def line_census(entries: list[dict]) -> tuple[set[str], set[str]]:
    """(lines in at least one content entry, lines in two or more)."""
    counts = collections.Counter(line for e in entries if e["element_type"] not in _STRUCTURAL
                                 for line in _entry_lines(e))
    return set(counts), {line for line, n in counts.items() if n > 1}


def carried_regions(base: dict, new: dict) -> list[tuple[int, int]]:
    """Per section, the element range a carry can change: from the first new-arm
    batch whose prompt differs from the base arm's to the section end. A
    section the base arm could not fully replay is excluded whole."""
    regions = []
    for base_section, new_section in zip(base["sections"], new["sections"], strict=True):
        span_end = base_section["end"] - 1  # detect_entries_for_section is passed end_idx + 1
        if any(b["outcome"] == "miss" for b in base_section["batches"]):
            regions.append((base_section["start"], span_end))
            continue
        base_shas = [b["sha"] for b in base_section["batches"]]
        for ordinal, batch in enumerate(new_section["batches"]):
            if ordinal >= len(base_shas) or batch["sha"] != base_shas[ordinal]:
                regions.append((_main_index(batch["first"]), span_end))
                break
    return regions


def _outside(entries: list[dict], regions: list[tuple[int, int]]) -> collections.Counter:
    def inside(entry: dict) -> bool:
        main = _main_index(entry["element_idx_start"])
        return main is not None and any(lo <= main <= hi for lo, hi in regions)
    return _canonical([e for e in entries if not inside(e)])


def gate_run(base: dict, new: dict, base_fail: dict, new_fail: dict) -> dict:
    """The gate's numbers for one run (the criteria are in the module docstring)."""
    first_batch_diffs = single_batch_diffs = 0
    for base_section, new_section in zip(base["sections"], new["sections"], strict=True):
        base_batches, new_batches = base_section["batches"], new_section["batches"]
        if base_batches and (not new_batches or new_batches[0]["sha"] != base_batches[0]["sha"]):
            first_batch_diffs += 1
        if len(base_batches) == 1 and [b["sha"] for b in new_batches] != [base_batches[0]["sha"]]:
            single_batch_diffs += 1
    regions = carried_regions(base, new)
    outside_base, outside_new = _outside(base["entries"], regions), _outside(new["entries"], regions)
    covered_base, multi_base = line_census(base_fail["entries"])
    covered_new, multi_new = line_census(new_fail["entries"])
    return {
        "uid": base["uid"],
        "base_matches_stored": base["matches_stored_entries"],
        "base_served": base["served"], "base_misses": base["misses"], "stored_calls": base["stored_calls"],
        "base_miss_sections": sum(any(b["outcome"] == "miss" for b in s["batches"]) for s in base["sections"]),
        "multi_batch_sections": sum(len(s["batches"]) > 1 for s in base["sections"]),
        "first_batch_prompt_diffs": first_batch_diffs,
        "single_batch_prompt_diffs": single_batch_diffs,
        "changed_entries_outside_carry": sum(((outside_base - outside_new) + (outside_new - outside_base)).values()),
        "unaccounted_same_fail_arms": base_fail["coverage"]["unaccounted_indices"]
        == new_fail["coverage"]["unaccounted_indices"],
        "unaccounted_same_as_base": base["coverage"]["unaccounted_indices"]
        == new_fail["coverage"]["unaccounted_indices"],
        "fail_lines_lost": len(covered_base - covered_new),
        "fail_lines_gained": len(covered_new - covered_base),
        "fail_multi_claimed_base": len(multi_base), "fail_multi_claimed_new": len(multi_new),
        "fail_entries_identical": _canonical(base_fail["entries"]) == _canonical(new_fail["entries"]),
        "new_batch_carry": new["batch_carry"],
    }


def gate_passes(row: dict) -> bool:
    return (row["first_batch_prompt_diffs"] == 0 and row["single_batch_prompt_diffs"] == 0
            and row["changed_entries_outside_carry"] == 0
            and row["unaccounted_same_fail_arms"] and row["unaccounted_same_as_base"]
            and row["fail_lines_lost"] == 0 and row["fail_lines_gained"] == 0
            and row["fail_multi_claimed_base"] == row["fail_multi_claimed_new"])


GATE_COLUMNS = ("uid", "base_matches_stored", "stored_calls", "base_served", "base_misses",
                "base_miss_sections", "multi_batch_sections", "first_batch_prompt_diffs",
                "single_batch_prompt_diffs", "changed_entries_outside_carry", "unaccounted_same_fail_arms",
                "unaccounted_same_as_base", "fail_lines_lost", "fail_lines_gained",
                "fail_multi_claimed_base", "fail_multi_claimed_new", "fail_entries_identical", "pass")


def run_gate(dirs: dict[str, Path]) -> int:
    uids = sorted(p.stem for p in dirs["base"].glob("*.json"))
    if not uids:
        print("gate: no replay records under --base", file=sys.stderr)
        return 2
    print("\t".join(GATE_COLUMNS))
    failed = 0
    for uid in uids:
        arms = {name: json.loads((d / f"{uid}.json").read_text(encoding="utf-8")) for name, d in dirs.items()}
        row = gate_run(arms["base"], arms["new"], arms["base_fail"], arms["new_fail"])
        row["pass"] = gate_passes(row)
        failed += not row["pass"]
        print("\t".join(str(row[c]) for c in GATE_COLUMNS))
    print(f"runs {len(uids)}  pass {len(uids) - failed}  fail {failed}")
    return 1 if failed else 0


def run_same(a_dir: Path, b_dir: Path) -> int:
    uids = sorted(p.stem for p in a_dir.glob("*.json"))
    if not uids:
        print("same: no replay records under --a", file=sys.stderr)
        return 2
    differ = []
    for uid in uids:
        a, b = (json.loads((d / f"{uid}.json").read_text(encoding="utf-8")) for d in (a_dir, b_dir))
        shas = [[[batch["sha"] for batch in s["batches"]] for s in arm["sections"]] for arm in (a, b)]
        if shas[0] != shas[1] or _canonical(a["entries"]) != _canonical(b["entries"]) \
                or a["coverage"] != b["coverage"]:
            differ.append(uid)
    print(f"runs {len(uids)}  identical {len(uids) - len(differ)}  differ {len(differ)} {' '.join(differ)}")
    return 1 if differ else 0


# ----------------------------------------------------------------- census

def _sections(stage2, hierarchy: dict) -> list[tuple[int, int]]:  # noqa: ANN001
    """run_stage_2's section list: the leaf sections, plus a Personal Data
    section for anything before the first one."""
    leaves = stage2.extract_leaf_sections_with_boundaries(
        hierarchy.get("section_boundaries", []), hierarchy.get("hierarchy_with_indices", []))
    first = min((start for _, start, _ in leaves), default=0)
    spans = [(0, first - 1)] if first > 0 else []
    return spans + [(start, end) for _, start, end in leaves]


def census_cv(stage2, docx: Path, hierarchy_json: Path, entries_json: Path) -> dict:  # noqa: ANN001
    """Today's batch edges for one CV and the carry each would take, using the
    stored entries as the model's reply."""
    hierarchy = json.loads(hierarchy_json.read_text(encoding="utf-8"))
    stored = json.loads(entries_json.read_text(encoding="utf-8"))
    content = [e for e in stored["entries"] if e.get("element_type") not in _STRUCTURAL]
    doc_elements = stage2.extract_unified_elements(str(docx)).get("elements", [])
    header_indices = stage2.collect_header_indices(hierarchy.get("hierarchy_with_indices", []))
    row = {"edges": 0, "multi_batch_sections": 0, "carries": [], "skipped": collections.Counter(),
           "elements_today": 0, "elements_carry": 0, "calls_today": 0, "calls_carry": 0,
           "stage2_cost": stored.get("total_cost", 0.0)}
    for start, end in _sections(stage2, hierarchy):
        elements = stage2._section_elements(doc_elements, start, end + 1, header_indices)
        if not elements:
            continue
        today = stage2.make_record_aligned_batches(elements)
        row["calls_today"] += len(today)
        row["elements_today"] += len(elements)
        row["edges"] += len(today) - 1
        row["multi_batch_sections"] += len(today) > 1
        _simulate_carry(stage2, elements, content, row)
    row["skipped"] = dict(row["skipped"])
    return row


def _simulate_carry(stage2, elements: list[dict], content: list[dict], row: dict) -> None:  # noqa: ANN001
    """Walk one section's batches with the carry on, the stored entries that
    start in each batch standing in for its reply."""
    pos = 0
    while pos < len(elements):
        end = stage2._record_aligned_end(elements, pos, stage2.STAGE2_BATCH_SIZE)
        batch = elements[pos:end]
        row["calls_carry"] += 1
        row["elements_carry"] += len(batch)
        if end >= len(elements):
            return
        keys = {str(e["idx"]) for e in batch}
        reply = [e for e in content if stage2.delimiter_index_text(e["element_idx_start"]) in keys]
        split = stage2._carry_split(reply, batch, stage2.STAGE2_CARRY_MAX)
        if split.skip_reason is None:
            row["carries"].append(len(batch) - split.carry)
        else:
            row["skipped"][split.skip_reason] += 1
        pos += split.carry


def _farm_triples(stage_root: Path, docx_roots: list[Path]) -> list[tuple[str, Path, Path, Path]]:
    """(uid, docx, stage-1b JSON, entries JSON) for every farm CV whose docx is found."""
    docx_by_stem = {}
    for root in docx_roots:
        for path in root.rglob("*.docx"):
            docx_by_stem.setdefault(path.stem, path)
    triples = []
    for entries in sorted((stage_root / "stage_2_entry_extraction").glob("*_entries.json")):
        prefix = entries.name[: -len("_entries.json")]
        hierarchy = stage_root / "stage_1b_hierarchy_mapping" / f"{prefix}_hierarchy_mapped.json"
        uid, _, stem = prefix.partition("_")
        docx = docx_by_stem.get(stem) or docx_by_stem.get(prefix)
        if hierarchy.exists() and docx is not None:
            triples.append((uid or prefix, docx, hierarchy, entries))
    return triples


def _percentile(values: list[int], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0


def run_census(stage2, triples: list[tuple[str, Path, Path, Path]]) -> int:  # noqa: ANN001
    if not triples:
        print("census: no CVs to measure", file=sys.stderr)
        return 2
    totals = collections.Counter()
    carries, skipped, failed = [], collections.Counter(), []
    print("uid\tedges\tmulti_batch_sections\tcarried\tskipped\telements_today\telements_carry\tcalls_today\tcalls_carry")
    for uid, docx, hierarchy, entries in triples:
        try:
            row = census_cv(stage2, docx, hierarchy, entries)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:  # an unreadable CV is counted, not fatal
            failed.append(uid)
            print(f"census: {uid} skipped: {type(exc).__name__}", file=sys.stderr)
            continue
        carries += row["carries"]
        skipped.update(row["skipped"])
        for key in ("edges", "multi_batch_sections", "elements_today", "elements_carry", "calls_today", "calls_carry"):
            totals[key] += row[key]
        extra = row["elements_carry"] - row["elements_today"]
        totals["est_extra_cost"] += row["stage2_cost"] * extra / row["elements_today"] if row["elements_today"] else 0
        totals["stage2_cost"] += row["stage2_cost"]
        print(f"{uid}\t{row['edges']}\t{row['multi_batch_sections']}\t{len(row['carries'])}\t"
              f"{sum(row['skipped'].values())}\t{row['elements_today']}\t{row['elements_carry']}\t"
              f"{row['calls_today']}\t{row['calls_carry']}")
    extra = totals["elements_carry"] - totals["elements_today"]
    print(f"CVs {len(triples) - len(failed)} (unreadable {len(failed)})  edges {totals['edges']}  "
          f"multi-batch sections {totals['multi_batch_sections']}")
    print(f"carried {len(carries)}  skipped {dict(skipped)}  carry median {_percentile(carries, 0.5)}  "
          f"p90 {_percentile(carries, 0.9)}  max {max(carries, default=0)}")
    print(f"elements sent {totals['elements_today']} -> {totals['elements_carry']} (+{extra}, "
          f"{100 * extra / max(totals['elements_today'], 1):.1f}%)  calls {totals['calls_today']} -> "
          f"{totals['calls_carry']} (+{totals['calls_carry'] - totals['calls_today']})")
    print(f"stage-2 cost ${totals['stage2_cost']:.2f}  estimated extra ${totals['est_extra_cost']:.2f}")
    return 0


# ----------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("run_dirs", nargs="+", type=Path)
    replay.add_argument("--out", type=Path, required=True)
    replay.add_argument("--src", type=Path, default=_DEFAULT_SRC)
    replay.add_argument("--fail-later-batches", action="store_true")
    replay.add_argument("--carry-max", type=int)
    same = sub.add_parser("same")
    same.add_argument("--a", type=Path, required=True)
    same.add_argument("--b", type=Path, required=True)
    gate = sub.add_parser("gate")
    for arm in ("base", "new", "base-fail", "new-fail"):
        gate.add_argument(f"--{arm}", type=Path, required=True)
    census = sub.add_parser("census")
    census.add_argument("run_dirs", nargs="*", type=Path)
    census.add_argument("--stage-root", type=Path, action="append", default=[])
    census.add_argument("--docx-root", type=Path, action="append", default=[])
    census.add_argument("--src", type=Path, default=_DEFAULT_SRC)
    args = parser.parse_args(argv)

    if args.command == "same":
        return run_same(args.a, args.b)
    if args.command == "gate":
        return run_gate({"base": args.base, "new": args.new, "base_fail": args.base_fail,
                         "new_fail": args.new_fail})
    stage2 = _import_stage2(args.src)
    logging.getLogger(stage2.logger.name).setLevel(logging.ERROR)  # per-batch warnings are the point here
    if args.command == "replay":
        if args.carry_max is not None:
            stage2.STAGE2_CARRY_MAX = args.carry_max
        args.out.mkdir(parents=True, exist_ok=True)
        print("uid\tstored_calls\tserved\tmisses\tforced_failures\tmatches_stored_entries")
        for run_dir in args.run_dirs:
            record = replay_run(stage2, run_dir, args.out, args.fail_later_batches)
            print(f"{record['uid']}\t{record['stored_calls']}\t{record['served']}\t{record['misses']}\t"
                  f"{record['forced_failures']}\t{record['matches_stored_entries']}")
        return 0
    triples = [_run_files(d) for d in args.run_dirs]
    for root in args.stage_root:
        triples += _farm_triples(root, args.docx_root)
    return run_census(stage2, triples)


if __name__ == "__main__":
    sys.exit(main())
