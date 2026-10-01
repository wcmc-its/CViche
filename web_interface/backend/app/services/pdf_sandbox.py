"""Every PDF parse runs here, in a child process with hard limits (#806).

pdfplumber/pdfminer parse with no bound on pages, decompressed size or time:
a 1 MB FlateDecode bomb peaked at 2.2 GB RSS and a 20,000-page file took 60 s
and 2.9 GB, against a 2Gi pod. So the upload text check (/upload, /estimate)
and the run's PDF->docx conversion both go through `run_pdf_job`, which:

- starts a fresh interpreter (`python -c`, not multiprocessing: spawn
  re-imports the parent's main module into the child, and the worker runs
  as `python -m app.worker`, which would load the whole DB/app stack into
  every PDF child before any limit applied);
- caps the child's address space with RLIMIT_AS as its first act, and its
  wall-clock time with a kill;
- refuses, before pdfminer parses anything, a document whose raw bytes
  declare a page-tree node over PDF_MAX_PAGES pages. This must precede
  pdfminer: its parser is quadratic in the length of an array, so merely
  reading a flat 20,000-entry /Kids array costs ~60 s. A page tree hidden
  in a compressed object stream escapes this scan; the count is checked
  again once pdfminer has opened the document, and the time limit bounds
  that open;
- refuses a document that cannot be opened without a password (detected
  from the trailer's /Encrypt dictionary, so RC4 and AES alike);
- reports a MemoryError as a limit even when pdfplumber wraps it (it wraps
  every pdfminer failure, MemoryError from a decompression bomb included);
- exchanges only JSON with the child, never pickle: the child parses
  untrusted input.

A limit hit is PdfTooComplexError, never a fail-open.

Limits. Pods have a 2Gi memory limit, prod and dev
(k8s/overlays/*/backend-patch.yaml, worker-patch.yaml), and a backend pod
runs up to CVICHE_MAX_CONCURRENT_RUNS=3 runs. Measured in python:3.14-slim
(the image base) on #806's review repros:
- PDF_CHILD_ADDRESS_SPACE_BYTES = 512 MiB. A child that has imported
  pdfplumber + python-docx maps 69 MB (VmPeak). The corpus's largest CV
  (142 pages) peaks at 48 MB RSS extracting text and 106 MB converting, so
  the cap is ~5x the worst real CV. It bounds one bad file (a 1 MB
  FlateDecode bomb peaked at 2.2 GB unbounded; capped, its child stops
  near 480 MB). It is not a reservation: four children all at the cap
  would be 2 GiB, the pod's whole limit, so concurrent bombs on one pod
  can still OOM it -- each file just costs a bounded amount now.
- PDF_TEXT_TIMEOUT_SECONDS = 30: the upload check is on the request path
  (and /estimate runs it as soon as a file is picked); the 142-page CV
  extracts in 4 s.
- PDF_CONVERT_TIMEOUT_SECONDS = 120: conversion runs once per run, off the
  request path; the 142-page CV converts in 5 s.
- PDF_MAX_PAGES = 300: the 276-PDF corpus maxes at 142 pages.
"""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_CHILD_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
PDF_TEXT_TIMEOUT_SECONDS = 30
PDF_CONVERT_TIMEOUT_SECONDS = 120
PDF_MAX_PAGES = 300

PDF_TOO_COMPLEX_MESSAGE = (
    "This PDF is too large or complex to process. Please upload a shorter "
    "or simpler PDF, or the CV as a .docx."
)

# The child's exit code when it runs out of memory but survives to report it.
_EXIT_OUT_OF_MEMORY = 3

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SRC_DIR = _BACKEND_DIR.parents[1] / "src"
_CHILD_BOOTSTRAP = (
    "import sys; sys.path[:0] = sys.argv[1:3]; "
    "from app.services.pdf_sandbox import _child_main; _child_main()"
)

# A page-tree node's /Count, in either key order. `[^>]` keeps a match inside
# one dictionary (a /Kids array holds no '>').
_PAGES_COUNT_RE = re.compile(
    rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)|/Count\s+(\d+)[^>]*?/Type\s*/Pages\b")

_OP_TEXT = "text"
_OP_CONVERT = "convert"


class PdfTooComplexError(Exception):
    """The PDF hit a page, memory or time limit."""


class EncryptedPdfError(Exception):
    """The PDF cannot be opened without a password."""


class UnreadablePdfError(Exception):
    """pdfminer could not parse the PDF at all."""


@dataclass(frozen=True)
class ConversionResult:
    """What the run needs back from a conversion (the converter's
    ConversionReport, minus everything the orchestrator does not read)."""
    image_only_pages: list[int]


# --- parent side ---------------------------------------------------------


def run_pdf_job(op: str, args: list[str], timeout_seconds: float) -> object:
    """Run `op` on `args` in a limited child; return its JSON result.

    Raises PdfTooComplexError on a page, memory or time limit,
    EncryptedPdfError, UnreadablePdfError, or RuntimeError for any other
    child failure (a bug, surfaced, not swallowed).
    """
    cmd = [sys.executable, "-c", _CHILD_BOOTSTRAP, str(_BACKEND_DIR), str(_SRC_DIR), op, *args]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as e:  # subprocess.run has already killed it
        raise PdfTooComplexError(f"PDF {op} exceeded {timeout_seconds}s") from e
    if proc.returncode == _EXIT_OUT_OF_MEMORY or proc.returncode < 0:
        # < 0: killed by a signal -- SIGKILL from the kernel or SIGSEGV/SIGABRT
        # when an allocation fails under RLIMIT_AS outside Python's control.
        raise PdfTooComplexError(f"PDF {op} child exited {proc.returncode}")
    if proc.returncode != 0:
        raise RuntimeError(f"PDF {op} child failed with exit code {proc.returncode}")
    status, value = json.loads(proc.stdout)
    if status == "ok":
        return value
    if status == "too_many_pages":
        raise PdfTooComplexError(f"PDF declares {value} pages (max {PDF_MAX_PAGES})")
    if status == "out_of_memory":
        raise PdfTooComplexError(f"PDF {op} ran out of memory: {value}")
    if status == "encrypted":
        raise EncryptedPdfError()
    if status == "unreadable":
        raise UnreadablePdfError(value)
    raise RuntimeError(f"unknown PDF child status {status!r}")


def extract_pdf_text(content: bytes) -> str:
    """The text of every page, for the upload's readable-text guard."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(content)
        tmp.flush()
        result = run_pdf_job(_OP_TEXT, [tmp.name], PDF_TEXT_TIMEOUT_SECONDS)
    assert isinstance(result, str)
    return result


def convert_pdf(pdf_path: str | Path, docx_path: str | Path) -> ConversionResult:
    """convert_pdf_to_docx, under the same limits."""
    result = run_pdf_job(_OP_CONVERT, [str(pdf_path), str(docx_path)], PDF_CONVERT_TIMEOUT_SECONDS)
    assert isinstance(result, dict)
    return ConversionResult(image_only_pages=list(result["image_only_pages"]))


# --- child side ----------------------------------------------------------


def _limit_address_space() -> None:
    import resource
    try:
        resource.setrlimit(resource.RLIMIT_AS, (PDF_CHILD_ADDRESS_SPACE_BYTES,) * 2)
    except (ValueError, OSError):
        # Linux (every deployed pod) honours RLIMIT_AS; macOS rejects it. A
        # Linux failure is a deployment fault and must not run unbounded.
        if sys.platform == "linux":
            raise
        sys.stderr.write("pdf_sandbox: RLIMIT_AS unsupported here; memory is unbounded\n")


def _raw_declared_pages(path: str) -> int:
    """The largest page-tree /Count visible in the raw bytes (0 if none)."""
    with open(path, "rb") as fh:
        data = fh.read()
    return max((int(a or b) for a, b in _PAGES_COUNT_RE.findall(data)), default=0)


def _wraps_memory_error(exc: BaseException) -> bool:
    seen: list[BaseException] = []
    todo = [exc]
    while todo:
        e = todo.pop()
        if isinstance(e, MemoryError):
            return True
        if any(e is s for s in seen):
            continue
        seen.append(e)
        todo += [a for a in e.args if isinstance(a, BaseException)]
        todo += [c for c in (e.__cause__, e.__context__) if c is not None]
    return False


def _declared_pages(path: str) -> int | None:
    """Page count from the page tree root, before any page is parsed.

    Returns None when the document cannot be opened without a password.
    Encryption is read off the trailer's /Encrypt dictionary (a probe
    document that never decrypts), so it does not depend on which exception
    a given security handler raises.
    """
    from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdftypes import resolve1

    class _Probe(PDFDocument):
        def _initialize_password(self, password: str = "") -> None:
            pass  # record self.encryption, decrypt nothing

    with open(path, "rb") as fh:
        doc = _Probe(PDFParser(fh))
        if doc.encryption is not None:
            fh.seek(0)
            try:
                doc = PDFDocument(PDFParser(fh))  # the empty user password
            except PDFEncryptionError:
                return None
        return int(resolve1(resolve1(doc.catalog["Pages"])["Count"]))


def _page_texts(path: str) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
            page.close()  # release the page's parsed objects as we go
    return "\n".join(parts)


def _convert(pdf_path: str, docx_path: str) -> dict:
    from unified_pipeline.core.pdf_to_docx import convert_pdf_to_docx

    return {"image_only_pages": convert_pdf_to_docx(pdf_path, docx_path).image_only_pages}


def _run_op(op: str, args: list[str]) -> tuple[str, object]:
    from pdfminer.psexceptions import PSException
    from pdfplumber.utils.exceptions import PdfminerException

    raw_pages = _raw_declared_pages(args[0])
    if raw_pages > PDF_MAX_PAGES:
        return "too_many_pages", raw_pages
    try:
        pages = _declared_pages(args[0])
    except (PSException, KeyError, TypeError, ValueError) as e:
        # A parse error, or a catalog with no usable /Pages /Count.
        return "unreadable", repr(e)
    if pages is None:
        return "encrypted", None
    if pages > PDF_MAX_PAGES:
        return "too_many_pages", pages
    try:
        return "ok", _page_texts(args[0]) if op == _OP_TEXT else _convert(*args)
    except PdfminerException as e:
        if _wraps_memory_error(e):
            return "out_of_memory", repr(e.args)
        return "unreadable", repr(e.args)


def _child_main() -> None:
    """Entry point of the child interpreter: argv = [backend, src, op, *args]."""
    _limit_address_space()
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # library prints must never corrupt the JSON reply
    op, args = sys.argv[3], sys.argv[4:]
    try:
        reply = _run_op(op, args)
    except MemoryError:
        os._exit(_EXIT_OUT_OF_MEMORY)
    out.write(json.dumps(reply).encode())
    out.flush()
