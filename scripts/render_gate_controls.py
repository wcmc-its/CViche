#!/usr/bin/env python3
"""Run the four render/doctor gate controls, then the base-vs-head gate, and print the table.

    python3 scripts/render_gate_controls.py <farm> --base <base_root> --head <head_root> \\
        [--source-dir DIR] [--work DIR] [--summary FILE] [--base-label L] [--head-label L]

<farm> is a stage-output tree (render_gate.py's docstring has the shape).
<base_root> and <head_root> are whole checkouts of the two code versions:
each needs src/ and key_files/, because stage 6 falls back to a template with
no grant tables when key_files/ is missing and nothing raises (a vacuous arm).
--work is wiped and recreated; it defaults to ./render_gate_controls_work.
--summary appends the same Markdown table to FILE ($GITHUB_STEP_SUMMARY in CI).

Issue #783: a gate result is only evidence if the gate could have failed, and
the four controls that show it could were run by hand and written into
docs/guides/render-doctor-gates.md. This runs them, in order:

1. Determinism. Head is rendered twice and doctored twice; both compares must
   report CHANGED 0, or the differ is reporting noise.
2. Crash guard. Every render directory is seeded with a stale docx before its
   arm renders; it must be gone afterwards (render_gate.py wipes its output).
   Then a copy of a clean arm has its _render_index.json rewritten to report
   every uid failed, with the docx left in place, and render_gate_compare
   must refuse to compare it.
3. Real-CV rendering. Every arm renders N of N farm CVs and exits 0.
4. Intentional mutation. A temporary copy of head has the PERSONAL DATA
   work-email write removed (render_gate_controls.MUTATION_*), the shape of
   the 2026-09-07 control, where removing the #550 write-back changed exactly
   the CVs it serves. The set of CVs that must change is predicted from head's
   own render (a non-empty Work email cell), and both checks must report
   exactly that set. The working tree is never edited.

Then base vs head: render_gate_compare over the two renders and
doctor_gate_compare over the two doctor runs. The doctor reads each arm's own
fresh render as stage_6_wcm_documents, so lints gated on the rendered docx
(table_shape and others) run, which the guide lists as a known gap of a farm
built from JSON alone.

PRIVACY (#2176). Corpus uids come from real filenames and carry owners' names
("038WKA_Jane Doe CV ..."). Everything this script prints or writes to
--summary passes through mask_uid(), which keeps only the leading 6-character
document uid (or a short farm number) and hashes anything else. Nothing is
printed from a CV: counts, masked uids and lint names only. The subprocesses'
own output, which does carry full uids and can carry CV text in a traceback,
goes to log files under --work and is never echoed.

Exit status: 0 when every control passed and head equals base; 1 when every
control passed and head differs from base (the gate's result, to be read);
2 when a control failed or the invocation was bad (no result is evidence).
Dates: Date of preparation and tracked-change w:date are masked by
render_gate_compare itself. Two more stage-6 call sites read the current year,
so a run whose renders straddle a new year fails the determinism control.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import doctor_gate_compare as dgc
import render_gate as rg
import render_gate_compare as rgc
from docx import Document
from docx.opc.exceptions import PackageNotFoundError

SCRIPTS = Path(__file__).resolve().parent

#: The template stage 6 renders into (stage_6_word_template.TEMPLATE_PATH).
#: Checked in each arm so a checkout without it fails here, not silently.
TEMPLATE_REL = Path("key_files") / "wcm_cv_template_faculty_october_2022_final.docx"
STAGE6_REL = Path("src") / "unified_pipeline" / "stage_6_word_template.py"
#: run_doctor's stage_6_docx artifact directory (run_doctor._ARTIFACTS,
#: pinned by scripts/test_render_doctor_gates.py).
RENDER_STAGE_DIR = "stage_6_wcm_documents"

#: The mutation control. One line, matched exactly once, in a copy of head.
MUTATION_FILE = Path("src") / "unified_pipeline" / "stage6" / "sections" / "personal_data.py"
MUTATION_ANCHOR = "if work_email and 'work email' in cell_text:"
MUTATION_REPLACEMENT = "if False and work_email and 'work email' in cell_text:"
MUTATION_LABEL = "PERSONAL DATA work-email write removed"
WORK_EMAIL_LABEL = "work email"

STALE_SENTINEL = "_stale_sentinel_wcm.docx"
CRASH_LOG = "controls_traceback.log"
#: What reading a farm, a checkout or a rendered docx can raise: file and
#: copy errors, a subprocess timeout, malformed JSON, a corrupt docx (python-
#: docx's PackageNotFoundError, zipfile's BadZipFile, lxml's XMLSyntaxError,
#: a SyntaxError subclass).
RUN_FAILURES = (OSError, ValueError, KeyError, SyntaxError, subprocess.SubprocessError,
                zipfile.BadZipFile, PackageNotFoundError)
DOCX_SUFFIX = "_wcm.docx"
SUBPROCESS_TIMEOUT_S = 3600
MAX_UID_ROWS = 60
#: Four renders and three doctor runs; four processes keeps a 4-vCPU runner busy.
DEFAULT_JOBS = 4

EXIT_SAME, EXIT_DIFFERS, EXIT_NO_EVIDENCE = 0, 1, 2

# A whole uid that is a stored run's document uid: six upper-case letters,
# digits, "_" or "-" (the S3 run store has uids like "AB1_CD", the local farm "AB1-CD").
_DOCUMENT_UID_RE = re.compile(r"[A-Z0-9_-]{6}")
# A leading token of up to six letters/digits that ends at a separator or at
# the end of the uid: "038WKA" of "038WKA_Jane Doe CV", "web08", "2003".
_LEADING_UID_RE = re.compile(r"[A-Za-z0-9]{1,6}(?![A-Za-z0-9])")


def mask_uid(uid: str) -> str:
    """The part of a uid that is safe to print: its 6-character document uid.

    A uid that IS a document uid is kept whole. Otherwise its leading token is
    kept only when it has the shape of one -- six upper-case letters or
    digits, or a short token with a digit in it (a farm number like "2003" or
    "web08"). Anything else ("Smith_CV", a name with no leading uid) becomes
    "uid-" plus 8 hex characters of its sha256, so it stays distinguishable
    without being readable.
    """
    if _DOCUMENT_UID_RE.fullmatch(uid):
        return uid
    m = _LEADING_UID_RE.match(uid)
    if m:
        token = m.group()
        if any(c.isdigit() for c in token) or (len(token) == 6 and token.isupper()):
            return token
    return "uid-" + hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8]


def _uid_of_docx(name: str) -> str:
    return name[:-len(DOCX_SUFFIX)] if name.endswith(DOCX_SUFFIX) else name


def _masked(uids) -> str:
    return " ".join(sorted({mask_uid(u) for u in uids})) or "none"


@dataclass(frozen=True)
class ArmRun:
    """One render of one arm: what render_gate.py reported, nothing from a CV."""
    label: str
    out: Path
    exit_code: int
    index: dict
    stale_wiped: bool

    def failed_uids(self) -> list[str]:
        return sorted(u for u, r in self.index.items() if "error" in r)

    def rendered(self) -> int:
        return sum(1 for r in self.index.values() if "error" not in r)

    def sources_found(self) -> int:
        return sum(1 for r in self.index.values()
                   if r.get("source_docx") not in (None, "not found"))


@dataclass(frozen=True)
class ControlRow:
    name: str
    measured: str
    passed: bool


@dataclass
class GateReport:
    """Everything the table is built from. Uids here are full; the report
    functions mask every one of them."""
    farm_uids: int
    source_dir: bool
    labels: tuple[str, str]
    controls: list[ControlRow] = field(default_factory=list)
    render: rgc.RenderComparison | None = None
    doctor: dgc.DoctorComparison | None = None
    doctor_delta: dict = field(default_factory=dict)
    note: str = ""

    def controls_passed(self) -> bool:
        return bool(self.controls) and all(c.passed for c in self.controls)

    def head_differs(self) -> bool:
        return not (self.render is not None and self.render.ok
                    and self.doctor is not None and self.doctor.ok)


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("farm", type=Path, help="stage-output tree to gate with")
    parser.add_argument("--base", type=Path, required=True, help="base checkout root")
    parser.add_argument("--head", type=Path, required=True, help="head checkout root")
    parser.add_argument("--source-dir", type=Path, default=None,
                        help="directory holding <uid>*.docx (render_gate.py --source-dir)")
    parser.add_argument("--work", type=Path, default=Path("render_gate_controls_work"),
                        help="scratch directory (wiped and recreated)")
    parser.add_argument("--summary", type=Path, default=None,
                        help="append the Markdown table to this file")
    parser.add_argument("--base-label", default="base")
    parser.add_argument("--head-label", default="head")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                        help=f"renders run side by side (default {DEFAULT_JOBS})")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if not (args.farm / "stage_4_field_extraction").is_dir():
        parser.error(f"no stage_4_field_extraction under {args.farm}")
    if args.source_dir is not None and not args.source_dir.is_dir():
        parser.error(f"--source-dir is not a directory: {args.source_dir}")
    for flag, root in (("--base", args.base), ("--head", args.head)):
        missing = [str(rel) for rel in (STAGE6_REL, TEMPLATE_REL) if not (root / rel).is_file()]
        if missing:
            parser.error(f"{flag} {root} is not a whole checkout: missing {', '.join(missing)}")
    unsafe = rg._validate_output_dir(args.work, args.farm, args.source_dir, args.base, args.head)
    if unsafe:
        parser.error(unsafe.replace("out directory", "--work"))
    return args


def _subprocess_env(arm_root: Path) -> dict:
    """The arm's code on the path, and no AWS credentials: a render or a
    doctor run reads local files only."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env["PYTHONPATH"] = str(Path(arm_root).resolve() / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_script(script: str, arm_root: Path, args: list, log: Path) -> int:
    """Run one of the gate scripts against one arm's code. Its output goes to
    `log` only: it names full uids and can quote CV text in a traceback. Each
    run gets a working directory of its own, since runs go side by side."""
    cwd = log.parent / f"{log.stem}_cwd"
    cwd.mkdir()
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                              env=_subprocess_env(arm_root), cwd=cwd, stdout=fh,
                              stderr=subprocess.STDOUT, timeout=SUBPROCESS_TIMEOUT_S, check=False)
    return proc.returncode


def _read_index(out: Path) -> dict:
    try:
        return rgc._render_index(out)
    except (ValueError, json.JSONDecodeError):
        return {}


def _render_arm(runner, label, arm_root, farm, source_dir, work) -> ArmRun:
    """Render one arm into a directory seeded with a stale docx first, so the
    crash-guard control can see whether render_gate.py wiped it."""
    out = work / f"render_{label}"
    out.mkdir(parents=True)
    (out / STALE_SENTINEL).write_bytes(b"stale output from a previous arm")
    args = [farm.resolve(), out.resolve()]
    if source_dir is not None:
        args += ["--source-dir", source_dir.resolve()]
    code = runner("render_gate.py", arm_root, args, work / "logs" / f"render_{label}.log")
    return ArmRun(label, out, code, _read_index(out), not (out / STALE_SENTINEL).exists())


def _doctor_view(farm: Path, render_out: Path, view: Path) -> Path:
    """The farm's stage_* directories plus this arm's fresh render as
    stage_6_wcm_documents, all as symlinks: the farm is never written."""
    view.mkdir(parents=True)
    for d in sorted(farm.iterdir()):
        if d.is_dir() and d.name.startswith("stage_") and d.name != RENDER_STAGE_DIR:
            (view / d.name).symlink_to(d.resolve(), target_is_directory=True)
    (view / RENDER_STAGE_DIR).symlink_to(render_out.resolve(), target_is_directory=True)
    return view


def _doctor_arm(runner, arm: ArmRun, arm_root, farm, source_dir, work) -> dict:
    """doctor_gate.py over the farm plus this arm's render; {} when it wrote
    nothing readable, which compare_reports then refuses."""
    view = _doctor_view(farm, arm.out, work / f"doctor_view_{arm.label}")
    out = work / f"doctor_{arm.label}.json"
    args = [view.resolve(), out.resolve()]
    if source_dir is not None:
        args += ["--source-dir", source_dir.resolve()]
    runner("doctor_gate.py", arm_root, args, work / "logs" / f"doctor_{arm.label}.log")
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {dgc.FAILED_KEY: {"(doctor_gate.py)": "no readable report"}}


def mutate_arm(head_root: Path, dest: Path) -> int:
    """Copy the parts of head stage 6 reads (src/ without outputs, key_files/,
    config.yaml) to `dest`, and apply the mutation there. Returns how many
    times the anchor matched; the caller treats anything but 1 as a failed
    control, because a mutation that did not apply proves nothing."""
    shutil.copytree(head_root / "src", dest / "src",
                    ignore=shutil.ignore_patterns("outputs", "__pycache__", "*.pyc"))
    shutil.copytree(head_root / "key_files", dest / "key_files")
    if (head_root / "config.yaml").is_file():
        shutil.copy2(head_root / "config.yaml", dest / "config.yaml")
    target = dest / MUTATION_FILE
    text = target.read_text(encoding="utf-8")
    hits = text.count(MUTATION_ANCHOR)
    if hits == 1:
        target.write_text(text.replace(MUTATION_ANCHOR, MUTATION_REPLACEMENT), encoding="utf-8")
    return hits


def _work_email_cell(doc) -> str:
    table = rg._find_personal_data_table(doc)
    if table is None:
        return ""
    for row in table.rows:
        if row.cells and WORK_EMAIL_LABEL in row.cells[0].text.lower() and len(row.cells) > 1:
            return row.cells[1].text.strip()
    return ""


def predicted_mutation_uids(head: ArmRun, template: Path) -> set:
    """The CVs the mutation must change: those whose rendered Work email cell
    differs from the template's own (empty) cell. Read only for the uids the
    index says rendered, which render_gate.py has already opened and checked."""
    blank = _work_email_cell(Document(str(template)))
    rendered = [u for u, r in head.index.items() if "error" not in r]
    return {u for u in rendered
            if _work_email_cell(Document(str(head.out / f"{u}{DOCX_SUFFIX}"))) != blank}


def _determinism_row(head_a, head_b, doc_a, doc_b, years) -> ControlRow:
    render = rgc.compare_dirs(head_a.out, head_b.out)
    doctor = dgc.compare_reports(doc_a, doc_b)
    same_year = years[0] == years[1]
    measured = (f"head rendered twice: {_render_counts(render)}; "
                f"doctored twice: {_doctor_counts(doctor)}")
    if not same_year:
        measured += f"; renders straddled {years[0]}->{years[1]}"
    return ControlRow("determinism", measured, render.ok and doctor.ok and same_year)


def _crash_guard_row(arms: list[ArmRun], work: Path) -> ControlRow:
    """Stale output wiped in every arm, and an injected all-failed index over
    the docx a clean arm left behind refused rather than compared."""
    wiped = sum(1 for a in arms if a.stale_wiped)
    injected = work / "render_crash_injected"
    shutil.copytree(arms[0].out, injected)
    (injected / "_render_index.json").write_text(
        json.dumps({u: {"error": "injected by render_gate_controls"} for u in arms[0].index}),
        encoding="utf-8")
    refused = bool(rgc.compare_dirs(arms[0].out, injected).guard_problems)
    measured = (f"stale docx wiped in {wiped}/{len(arms)} arms; injected all-failed index "
                f"over stale docx: {'refused' if refused else 'COMPARED'}")
    return ControlRow("crash guard", measured, wiped == len(arms) and refused)


def _real_cv_row(arms: list[ArmRun], n: int, source_dir) -> ControlRow:
    parts, ok = [], n > 0
    for a in arms:
        good = a.exit_code == 0 and a.rendered() == n and not a.failed_uids()
        ok = ok and good
        part = f"{a.label} {a.rendered()}/{n} exit {a.exit_code}"
        if a.failed_uids():
            part += f" failed {_masked(a.failed_uids())}"
        parts.append(part)
    if source_dir is not None:
        parts.append(f"source docx for {arms[0].sources_found()}/{n}")
    return ControlRow("real-CV rendering", "; ".join(parts), ok)


def _mutation_row(hits, head: ArmRun, mutated: ArmRun, template: Path) -> ControlRow:
    name = f"mutation ({MUTATION_LABEL})"
    if hits != 1:
        return ControlRow(name, f"anchor matched {hits} times in {MUTATION_FILE}, expected 1", False)
    predicted = predicted_mutation_uids(head, template)
    result = rgc.compare_dirs(head.out, mutated.out)
    para = {_uid_of_docx(d.name) for d in result.paragraph_changed}
    fp = {_uid_of_docx(n) for n in result.fingerprint_changed}
    ok = bool(predicted) and para == predicted and fp == predicted and not result.guard_problems
    measured = (f"paragraph CHANGED {len(para)}, fingerprint CHANGED {len(fp)}; "
                f"predicted {len(predicted)} (rendered work email); "
                f"{'exact match' if para == fp == predicted else 'MISMATCH'}")
    if result.guard_problems:
        measured = "refused: mutated arm did not render cleanly"
    return ControlRow(name, measured, ok)


def _render_counts(result: rgc.RenderComparison) -> str:
    if result.guard_problems:
        return "refused (an arm recorded a failure)"
    return (f"paragraph CHANGED {len(result.paragraph_changed)}, "
            f"fingerprint CHANGED {len(result.fingerprint_changed)} of {len(result.common)}")


def _doctor_counts(result: dgc.DoctorComparison) -> str:
    if result.guard_problems:
        return "refused (an arm recorded a failure)"
    return f"CHANGED {len(result.changed)} of {len(result.common)}"


def _doctor_deltas(doc_base: dict, doc_head: dict, changed) -> dict:
    """uid -> (gained, lost) findings, for the uids the doctor compare
    reported changed."""
    return {u: dgc.findings_delta(doc_base[u], doc_head[u]) for u in changed}


def _per_uid_rows(report: GateReport) -> list[str]:
    """One table row per changed run: counts only, uid masked."""
    render = report.render
    para = {_uid_of_docx(d.name): d for d in render.paragraph_changed} if render else {}
    fp = {_uid_of_docx(n) for n in render.fingerprint_changed} if render else set()
    uids = sorted(set(para) | fp | set(report.doctor_delta), key=mask_uid)
    rows = []
    for uid in uids[:MAX_UID_ROWS]:
        d = para.get(uid)
        gained, lost = report.doctor_delta.get(uid, ([], []))
        rows.append(f"| {mask_uid(uid)} | {len(d.added) if d else 0} | {len(d.removed) if d else 0} "
                    f"| {'yes' if uid in fp else 'no'} | {len(gained)} | {len(lost)} |")
    if len(uids) > MAX_UID_ROWS:
        rows.append(f"| ... {len(uids) - MAX_UID_ROWS} more | | | | | |")
    return rows


def _lint_rows(report: GateReport) -> list[str]:
    gained, lost = Counter(), Counter()
    for g, lo in report.doctor_delta.values():
        gained.update(str(f.get("lint", "?")) for f in g)
        lost.update(str(f.get("lint", "?")) for f in lo)
    return [f"| {lint} | {gained[lint]} | {lost[lint]} |" for lint in sorted(set(gained) | set(lost))]


def format_report(report: GateReport) -> str:
    """The Markdown table, every uid masked and nothing taken from a CV."""
    base_label, head_label = report.labels
    lines = ["## Render/doctor gate (#783, #2176)", "",
             f"Farm: {report.farm_uids} CVs"
             + (", rendered with their source docx." if report.source_dir else ", no source docx.")
             + f" Base `{base_label}`, head `{head_label}`.", "",
             "| control | measured | result |", "|---|---|---|"]
    lines += [f"| {c.name} | {c.measured} | {'PASS' if c.passed else 'FAIL'} |"
              for c in report.controls]
    lines += ["", "### Base vs head", ""]
    if report.render is None or report.doctor is None:
        lines.append(f"Not compared: {report.note or 'a control failed before the comparison'}.")
        return "\n".join(lines) + "\n"
    lines += ["| check | result |", "|---|---|",
              f"| render | {_render_counts(report.render)} |",
              f"| doctor | {_doctor_counts(report.doctor)} |", ""]
    per_uid = _per_uid_rows(report)
    if per_uid:
        lines += ["| run | paragraphs added | paragraphs removed | fingerprint changed "
                  "| doctor findings gained | doctor findings lost |",
                  "|---|---|---|---|---|---|", *per_uid, ""]
    lints = _lint_rows(report)
    if lints:
        lines += ["| lint | findings gained | findings lost |", "|---|---|---|", *lints, ""]
    lines.append(_verdict(report))
    return "\n".join(lines) + "\n"


def _verdict(report: GateReport) -> str:
    if not report.controls_passed():
        return "**No evidence**: a control failed, so the comparison above proves nothing."
    if report.head_differs():
        return "**Controls passed. Head differs from base** in the runs listed above."
    return "**Controls passed. Head renders and doctors identically to base.**"


def _render_all(runner, args, work) -> tuple[dict, int]:
    """Every render the controls need, plus the mutation's anchor count. The
    arms are separate processes writing separate directories, so they run
    side by side (--jobs); the dict keeps a fixed order regardless."""
    mutated_root = work / "arm_mutated"
    hits = mutate_arm(args.head, mutated_root)
    plan = [("head_a", args.head), ("head_b", args.head), ("base", args.base)]
    if hits == 1:
        plan.append(("mutated", mutated_root))
    with ThreadPoolExecutor(args.jobs) as pool:
        futures = {label: pool.submit(_render_arm, runner, label, root, args.farm,
                                      args.source_dir, work) for label, root in plan}
        return {label: f.result() for label, f in futures.items()}, hits


def _doctor_all(runner, args, arms, work) -> dict:
    plan = (("head_a", args.head), ("head_b", args.head), ("base", args.base))
    with ThreadPoolExecutor(args.jobs) as pool:
        futures = {label: pool.submit(_doctor_arm, runner, arms[label], root, args.farm,
                                      args.source_dir, work) for label, root in plan}
        return {label: f.result() for label, f in futures.items()}


def run_controls(args, runner=run_script) -> GateReport:
    """Render, doctor, run the four controls in order, then compare base and head."""
    work = args.work
    shutil.rmtree(work, ignore_errors=True)
    (work / "logs").mkdir(parents=True)
    n = len(rg._discover_uids(args.farm, set()))
    report = GateReport(n, args.source_dir is not None, (args.base_label, args.head_label))
    years = [datetime.date.today().year]
    arms, hits = _render_all(runner, args, work)
    docs = _doctor_all(runner, args, arms, work)
    years.append(datetime.date.today().year)

    report.controls.append(_determinism_row(arms["head_a"], arms["head_b"],
                                            docs["head_a"], docs["head_b"], years))
    report.controls.append(_crash_guard_row(list(arms.values()), work))
    report.controls.append(_real_cv_row(list(arms.values()), n, args.source_dir))
    report.controls.append(_mutation_row(hits, arms["head_a"], arms.get("mutated", arms["head_a"]),
                                         args.head / TEMPLATE_REL))

    report.render = rgc.compare_dirs(arms["base"].out, arms["head_a"].out)
    report.doctor = dgc.compare_reports(docs["base"], docs["head_a"])
    if not report.doctor.guard_problems:
        report.doctor_delta = _doctor_deltas(docs["base"], docs["head_a"], report.doctor.changed)
    return report


def main(argv=None, runner=run_script) -> int:
    args = _parse_args(argv)
    try:
        report = run_controls(args, runner)
    except RUN_FAILURES as e:
        # A traceback printed here would carry paths with full uids in them.
        # It goes to a log under --work instead, and the run reports no
        # evidence. Anything outside RUN_FAILURES is a bug in this script and
        # propagates.
        args.work.mkdir(parents=True, exist_ok=True)
        (args.work / CRASH_LOG).write_text(traceback.format_exc(), encoding="utf-8")
        report = GateReport(0, args.source_dir is not None, (args.base_label, args.head_label),
                            note=f"the run stopped on {type(e).__name__} (traceback in "
                                 f"--work's {CRASH_LOG})")
    text = format_report(report)
    print(text, end="")
    if args.summary is not None:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    if not report.controls_passed():
        return EXIT_NO_EVIDENCE
    return EXIT_DIFFERS if report.head_differs() else EXIT_SAME


if __name__ == "__main__":
    sys.exit(main())
