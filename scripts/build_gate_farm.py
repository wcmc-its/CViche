#!/usr/bin/env python3
"""Pick ~60 stored runs that cover stage 6's section codes and copy them into the gate farm.

    PYTHONPATH=src python3 scripts/build_gate_farm.py --dry-run
    PYTHONPATH=src python3 scripts/build_gate_farm.py --write [--replace]
    PYTHONPATH=src python3 scripts/build_gate_farm.py --dry-run --local-out DIR

Run by hand, from a machine whose AWS credentials can read cviche/runs/ and
write cviche/gate-farm/ (#2176). The CI job that reads the farm
(.github/workflows/ci.yml, corpus-gate) has read access to the farm prefix
only, never to cviche/runs/.

Source: s3://<bucket>/cviche/runs/<run_id>/, the per-run layout every web run
stores: outputs/<uid>_<suffix> flat, the uploaded original at
input/<run_id>.docx. Destination, the layout render_gate.py, doctor_gate.py
and render_gate_controls.py read:

    cviche/gate-farm/outputs/stage_4_field_extraction/<uid>_fields.json
    cviche/gate-farm/outputs/stage_5d_citation_formatted/<uid>_citation_formatted.json
    ...                                       (one directory per stage)
    cviche/gate-farm/sources/<uid>.docx       (render_gate.py --source-dir)
    cviche/gate-farm/manifest.json            (uids and section codes, no names;
                                               render_gate_controls.py --manifest)

The suffix-to-directory map is not restated here: it is run_doctor's
artifact_layout() plus render_gate.PRECEDENCE (which adds stage 5c). The
stored _wcm.docx and _render_warnings.json are left out on purpose: the gate
renders each arm fresh.

Selection. A candidate is a run with a stage-4 _fields.json and a .docx
upload (a PDF upload cannot back --source-dir), whose uid is its run id and
has exactly the generated shape (render_gate_controls.is_generated_uid: six of
A-Z0-9). Any other uid -- a filename stem, or an old id with "_" in it, which
is also what "LI_WEI" looks like -- could carry a name into the farm's
filenames and the printed output, so it is never farmed. Runs of the
same CV are collapsed to the latest one, first by the upload's sha256
(input/manifest.json), then by the stage-4 cv_owner name, hashed in memory and
never written or printed. Then a greedy set cover over the taxonomy codes
in each run's stage-4 entries picks runs until every code is covered once,
and a second greedy pass fills to --size, preferring runs whose codes the
farm holds fewest of.

Output names runs by their 6-character uid only. The manifest's
"document_uids" list is the allowlist render_gate_controls.mask_uid reads:
the only uids the gate ever prints unhashed.

--write copies server side (copy_object), so no CV leaves S3. It refuses a
non-empty farm prefix unless --replace, which deletes the farm keys the new
selection does not include, after the copies succeed. --local-out downloads
the selection into DIR in the same layout instead, for a local gate run.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import render_gate as rg
from render_gate_controls import (
    MANIFEST_UIDS_KEY,
    RENDER_STAGE_DIR,
    is_generated_uid,
    mask_uid,
)

DEFAULT_BUCKET = "wcm-cviche-storage"
RUNS_PREFIX = "cviche/runs/"
FARM_PREFIX = "cviche/gate-farm/"
DEFAULT_SIZE = 60
FIELDS_SUFFIX = "_fields.json"
SOURCE_SUFFIX = ".docx"
MANIFEST_NAME = "manifest.json"
WORKERS = 16
NO_CODE = "(none)"

_NAME_WORD_RE = re.compile(r"[a-z]+")


def farm_layout() -> dict[str, str]:
    """Artifact suffix -> stage directory, for every JSON artifact the gate
    reads: run_doctor's layout (minus the stage-6 render, which the gate
    makes itself) plus render_gate's render inputs."""
    from unified_pipeline.run_doctor import artifact_layout

    layout = {path.name: path.parent.name
              for path in artifact_layout(Path(), "").values()
              if path.parent.name != RENDER_STAGE_DIR and path.name.endswith(".json")}
    for stage_dir, pattern in rg.PRECEDENCE:
        layout[pattern.format(uid="")] = stage_dir
    return layout


def owner_key(fields: dict) -> str | None:
    """A hash of the CV owner's name, to collapse re-runs of one CV. None
    when stage 4 found no name (such a run is never collapsed)."""
    owner = fields.get("cv_owner")
    name = owner.get("name") if isinstance(owner, dict) else None
    words = _NAME_WORD_RE.findall(str(name).lower()) if name else []
    return hashlib.sha256(" ".join(sorted(words)).encode()).hexdigest() if words else None


def gateable_uid(run_id: str, uid: str) -> bool:
    """A uid the farm may hold: the run's own id (the orchestrator names
    every web run's outputs after it), in exactly the generated shape."""
    return uid == run_id and is_generated_uid(uid)


def section_codes(fields: dict) -> frozenset[str]:
    entries = fields.get("entries")
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(str(e.get("taxonomy_code") or NO_CODE) for e in entries if isinstance(e, dict))


@dataclass(frozen=True)
class Candidate:
    """One stored run the gate could use. `owner` and `source_sha` are hashes
    used only to collapse re-runs of one CV; neither is written anywhere.
    `artifacts` maps a farm-relative destination to its source key."""
    uid: str
    codes: frozenset[str]
    owner: str | None
    source_sha: str | None
    modified: datetime
    artifacts: dict[str, str]


def latest_per(candidates: list[Candidate], key: str) -> list[Candidate]:
    """One run per value of `key` (an attribute), the most recent; runs
    whose value is None are all kept."""
    best, unkeyed = {}, []
    for c in candidates:
        k = getattr(c, key)
        if k is None:
            unkeyed.append(c)
        elif k not in best or c.modified > best[k].modified:
            best[k] = c
    return sorted([*best.values(), *unkeyed], key=lambda c: c.uid)


def distinct_cvs(candidates: list[Candidate]) -> list[Candidate]:
    """Re-runs collapsed twice: by identical upload, then by owner name --
    either alone misses some (stage 4 can name one owner two ways)."""
    return latest_per(latest_per(candidates, "source_sha"), "owner")


def select_runs(codes_by_uid: dict[str, frozenset[str]], size: int) -> list[str]:
    """Greedy set cover over the codes, then a diminishing-returns fill.

    Phase 1 takes the run covering the most still-uncovered codes until none
    is left (or `size` is reached). Phase 2 scores a run by the sum of
    1 / (1 + runs already holding that code) over its codes, so each further
    pick favours the codes the farm has least of. Ties break on more codes,
    then on uid, so the same store gives the same farm.
    """
    remaining, chosen, held = dict(codes_by_uid), [], Counter()

    def take(uid):
        chosen.append(uid)
        held.update(remaining.pop(uid))

    while remaining and len(chosen) < size:
        uid = max(sorted(remaining), key=lambda u: (len(remaining[u] - held.keys()), len(remaining[u])))
        if not remaining[uid] - held.keys():
            break
        take(uid)
    while remaining and len(chosen) < size:
        take(max(sorted(remaining),
                 key=lambda u: (sum(1 / (1 + held[c]) for c in remaining[u]), len(remaining[u]))))
    return chosen


def _list_keys(s3, bucket: str, prefix: str) -> list[dict]:
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(page.get("Contents", []))
    return keys


def _run_ids(s3, bucket: str) -> list[str]:
    ids = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=RUNS_PREFIX, Delimiter="/"):
        ids.extend(p["Prefix"][len(RUNS_PREFIX):].rstrip("/") for p in page.get("CommonPrefixes", []))
    return ids


def _read_json(s3, bucket: str, key: str):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except json.JSONDecodeError:
        return None


def _candidate(s3, bucket: str, run_id: str, layout: dict) -> Candidate | None:
    """The run's gate-relevant keys and stage-4 summary, or None when it
    cannot be gated (no single stage-4 file, or no single .docx upload)."""
    base = f"{RUNS_PREFIX}{run_id}/"
    outputs = {o["Key"].rsplit("/", 1)[1]: o for o in _list_keys(s3, bucket, base + "outputs/")}
    inputs = {o["Key"].rsplit("/", 1)[1]: o["Key"] for o in _list_keys(s3, bucket, base + "input/")}
    fields_names = [n for n in outputs if n.endswith(FIELDS_SUFFIX)]
    sources = [k for n, k in inputs.items() if n.lower().endswith(SOURCE_SUFFIX)]
    if len(fields_names) != 1 or len(sources) != 1:
        return None
    uid = fields_names[0][:-len(FIELDS_SUFFIX)]
    if not gateable_uid(run_id, uid):
        return None  # anything but a generated run id can carry a name into the farm
    fields = _read_json(s3, bucket, base + "outputs/" + fields_names[0])
    if not isinstance(fields, dict):
        return None
    upload = _read_json(s3, bucket, inputs[MANIFEST_NAME]) if MANIFEST_NAME in inputs else None
    artifacts = {f"outputs/{stage_dir}/{uid}{suffix}": base + "outputs/" + f"{uid}{suffix}"
                 for suffix, stage_dir in layout.items() if f"{uid}{suffix}" in outputs}
    artifacts[f"sources/{uid}{SOURCE_SUFFIX}"] = sources[0]
    return Candidate(uid, section_codes(fields), owner_key(fields),
                     upload.get("sha256") if isinstance(upload, dict) else None,
                     outputs[fields_names[0]]["LastModified"], artifacts)


def gather_candidates(s3, bucket: str, layout: dict) -> list[Candidate]:
    run_ids = _run_ids(s3, bucket)
    with ThreadPoolExecutor(WORKERS) as pool:
        found = list(pool.map(lambda r: _candidate(s3, bucket, r, layout), run_ids))
    candidates = [c for c in found if c is not None and c.codes]
    print(f"runs listed {len(run_ids)}; gateable {len(candidates)} "
          f"(stage-4 fields and a .docx upload)")
    return candidates


def manifest(chosen: list[Candidate], all_codes: set) -> dict:
    covered = set().union(*(c.codes for c in chosen)) if chosen else set()
    return {"built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "runs": len(chosen),
            "codes_available": sorted(all_codes),
            "codes_covered": sorted(covered),
            "codes_missing": sorted(all_codes - covered),
            MANIFEST_UIDS_KEY: sorted(c.uid for c in chosen if is_generated_uid(c.uid)),
            "codes_by_uid": {c.uid: sorted(c.codes) for c in chosen if is_generated_uid(c.uid)}}


def print_plan(chosen: list[Candidate], man: dict) -> None:
    held = Counter(code for c in chosen for code in c.codes)
    print(f"selected {man['runs']} runs; codes covered {len(man['codes_covered'])}"
          f"/{len(man['codes_available'])}; missing: {' '.join(man['codes_missing']) or 'none'}")
    allowed = frozenset(man[MANIFEST_UIDS_KEY])
    print("runs: " + " ".join(sorted(mask_uid(c.uid, allowed) for c in chosen)))
    print("runs per code: " + " ".join(f"{code}={n}" for code, n in sorted(held.items())))


def _farm_is_empty(s3, bucket: str) -> bool:
    return not s3.list_objects_v2(Bucket=bucket, Prefix=FARM_PREFIX, MaxKeys=1).get("Contents")


def write_farm(s3, bucket: str, chosen: list[dict], man: dict, replace: bool) -> int:
    """Copy every chosen artifact server side, write the manifest, and with
    `replace` delete the farm keys the selection no longer includes."""
    if not replace and not _farm_is_empty(s3, bucket):
        print(f"s3://{bucket}/{FARM_PREFIX} is not empty; pass --replace", file=sys.stderr)
        return 2
    wanted = {FARM_PREFIX + dest: src for c in chosen for dest, src in c.artifacts.items()}
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda kv: s3.copy_object(Bucket=bucket, Key=kv[0],
                                                CopySource={"Bucket": bucket, "Key": kv[1]}),
                      wanted.items()))
    s3.put_object(Bucket=bucket, Key=FARM_PREFIX + MANIFEST_NAME,
                  Body=json.dumps(man, indent=1).encode(), ContentType="application/json")
    wanted.setdefault(FARM_PREFIX + MANIFEST_NAME, None)
    stale = [o["Key"] for o in _list_keys(s3, bucket, FARM_PREFIX) if o["Key"] not in wanted]
    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale[i:i + 1000]]})
    print(f"wrote {len(wanted) - 1} objects and the manifest; removed {len(stale)} stale")
    return 0


def download_farm(s3, bucket: str, chosen: list[dict], man: dict, out: Path) -> int:
    """The same layout, on local disk, for a local render_gate_controls.py run."""
    if out.exists() and any(out.iterdir()):
        print(f"{out} is not empty", file=sys.stderr)
        return 2
    pairs = [(out / dest, src) for c in chosen for dest, src in c.artifacts.items()]
    for dest, _ in pairs:
        dest.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda p: s3.download_file(bucket, p[1], str(p[0])), pairs))
    (out / MANIFEST_NAME).write_text(json.dumps(man, indent=1), encoding="utf-8")
    print(f"downloaded {len(pairs)} objects into the local farm")
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="read only: print the selection")
    mode.add_argument("--write", action="store_true", help="copy the selection into the farm")
    parser.add_argument("--replace", action="store_true",
                        help="with --write: replace a non-empty farm")
    parser.add_argument("--local-out", type=Path, default=None,
                        help="with --dry-run: download the selection into this directory")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    args = parser.parse_args(argv)
    if args.replace and not args.write:
        parser.error("--replace needs --write")
    if args.local_out is not None and not args.dry_run:
        parser.error("--local-out needs --dry-run (it never writes to S3)")
    if args.size < 1:
        parser.error("--size must be at least 1")
    return args


def main(argv=None) -> int:
    args = _parse_args(argv)
    import boto3

    s3 = boto3.client("s3")
    candidates = distinct_cvs(gather_candidates(s3, args.bucket, farm_layout()))
    print(f"distinct CVs {len(candidates)} (re-runs collapsed to the latest)")
    by_uid = {c.uid: c for c in candidates}
    chosen = [by_uid[u] for u in select_runs({u: c.codes for u, c in by_uid.items()}, args.size)]
    man = manifest(chosen, set().union(*(c.codes for c in candidates)) if candidates else set())
    print_plan(chosen, man)
    if args.write:
        return write_farm(s3, args.bucket, chosen, man, args.replace)
    if args.local_out is not None:
        return download_farm(s3, args.bucket, chosen, man, args.local_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
