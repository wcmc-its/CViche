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
  reading a flat 20,000-entry /Kids array costs ~60 s. Then the page tree
  is walked and counted, stopping one past the cap, so a tree whose /Count
  lies (or sits in a compressed object stream) is still capped;
- refuses a document that cannot be opened without a password (detected
  from the trailer's /Encrypt dictionary, so RC4 and AES alike);
- reports a MemoryError as a limit even when pdfplumber wraps it (it wraps
  every pdfminer failure, MemoryError from a decompression bomb included);
- maps every other failure of the parse -- pdfminer's own errors and the
  unwrapped TypeError/KeyError/ValueError it raises on malformed structure,
  or a reply that cannot be read -- to UnreadablePdfError (a 400), never a
  500;
- passes the child an allowlisted environment, so it holds no credentials;
- exchanges only JSON with the child, never pickle: the child parses
  untrusted input.

A limit hit is PdfTooComplexError, never a fail-open. A child stopped from
outside -- a termination signal, say the pod draining -- is
PdfInterruptedError, and a wall-clock timeout is PdfTimeoutError: neither
says the file is too large (#1566).

Concurrency. At most PDF_CHILD_SLOTS children run at once per process (one
uvicorn process per backend pod, one worker process per worker pod). The
request path takes a slot without waiting, so a burst of uploads gets
PdfBusyError (a 503 with Retry-After) instead of queueing threads or
spawning more children; a rejected upload creates no Run row, so the run
quota never limited it, and /estimate allows 20 calls per 5 minutes. The
run's conversion waits up to PDF_CONVERT_SLOT_WAIT_SECONDS, then fails
with PDF_BUSY_RUN_MESSAGE.

Limits. Pods have a 2Gi memory limit, prod and dev
(k8s/overlays/*/backend-patch.yaml, worker-patch.yaml), and a backend pod
runs up to CVICHE_MAX_CONCURRENT_RUNS=3 runs. Measured in python:3.14-slim
(the image base):
- PDF_CHILD_ADDRESS_SPACE_BYTES = 256 MiB. A child that has imported
  pdfplumber + python-docx maps 69 MB (VmPeak); the corpus's largest CV
  (142 pages) peaks at 50 MB RSS extracting text and 107 MB converting,
  and converts under this cap.
- PDF_CHILD_SLOTS = 2, so PDF children total at most 512 MiB per process,
  a quarter of the pod, leaving 1.5 GiB for the parent and its 3 runs.
  Unslotted, 6 concurrent bombs in a 2 GiB container OOM-killed children,
  and with a 700 MiB parent, 3 concurrent killed the parent.
- PDF_TEXT_TIMEOUT_SECONDS = 30: the upload check is on the request path
  (and /estimate runs it as soon as a file is picked); the 142-page CV
  extracts in 4 s.
- PDF_CONVERT_TIMEOUT_SECONDS = 120: conversion runs once per run, off the
  request path; the 142-page CV converts in 5 s.
- PDF_CONVERT_SLOT_WAIT_SECONDS = 120: no job holds a slot longer than
  120 s, so a run that waited this long saw at least one worst-case job
  finish in each slot and is up against a sustained flood, not a burst.
- PDF_MAX_PAGES = 300: the 276-PDF corpus maxes at 142 pages.
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_CHILD_ADDRESS_SPACE_BYTES = 256 * 1024 * 1024
PDF_CHILD_SLOTS = 2
PDF_TEXT_TIMEOUT_SECONDS = 30
PDF_CONVERT_TIMEOUT_SECONDS = 120
PDF_CONVERT_SLOT_WAIT_SECONDS = 120
PDF_MAX_PAGES = 300
# A /Count with more digits than this is over PDF_MAX_PAGES whatever it says;
# int() of a 5,000-digit string would raise (Python's int-digit limit).
_MAX_COUNT_DIGITS = 9

PDF_TOO_COMPLEX_MESSAGE = (
    "This PDF is too large or complex to process. Please upload a shorter "
    "or simpler PDF, or the CV as a .docx."
)
PDF_UNREADABLE_MESSAGE = (
    "We couldn't read this PDF. It may be damaged or use features we don't "
    "support. Please re-export it, or upload the CV as a .docx."
)
PDF_BUSY_MESSAGE = (
    "The server is busy processing other PDFs — please try again in a moment."
)
PDF_INTERRUPTED_RUN_MESSAGE = (
    "Converting this PDF was interrupted because the server restarted or "
    'was under heavy load; the file itself is fine. Please try "Retry failed '
    'step" in a few minutes.'
)
PDF_TIMEOUT_RUN_MESSAGE = (
    "Converting this PDF took too long and was stopped. This is often "
    'temporary, so please try "Retry failed step" in a few minutes; if it '
    "happens again, upload the CV as a .docx."
)
PDF_BUSY_RUN_MESSAGE = (
    "The server was too busy processing other PDFs to start this run. "
    'Please try "Restart with file" in a few minutes.'
)

# Process-wide: one uvicorn process per backend pod, one worker process per
# worker pod. A threading primitive because the request path reaches
# run_pdf_job through run_in_threadpool and the run through to_thread.
_slots = threading.BoundedSemaphore(PDF_CHILD_SLOTS)

# The child's exit code when it runs out of memory but survives to report it.
_EXIT_OUT_OF_MEMORY = 3
# Signals that stop a child from outside -- a pod shutdown, a Ctrl-C -- and
# say nothing about the PDF. Every other signal stays a limit: SIGKILL comes
# from the kernel's OOM killer (a pod-level SIGKILL kills this parent too, so
# it never sees the child's exit), SIGSEGV/SIGABRT from an allocation failing
# under RLIMIT_AS outside Python's control.
_INTERRUPT_SIGNALS = frozenset({signal.SIGTERM, signal.SIGINT, signal.SIGHUP})

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SRC_DIR = _BACKEND_DIR.parents[1] / "src"
_CHILD_BOOTSTRAP = (
    "import sys; sys.path[:0] = sys.argv[1:3]; "
    "from app.services.pdf_sandbox import _child_main; _child_main()"
)
# The only environment the child gets: no credentials (AWS_*, DB_*, session
# secrets). Partial isolation: the child runs as the same uid as the parent,
# so on Linux it could still read /proc/<parent pid>/environ.
_CHILD_ENV_KEYS = ("PATH", "HOME", "LANG", "PYTHONPATH", "TMPDIR")
_CHILD_ENV_PREFIXES = ("LC_",)

# A page-tree node's /Count, in either key order. `[^>]` keeps a match inside
# one dictionary (a /Kids array holds no '>').
_PAGES_COUNT_RE = re.compile(
    rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)|/Count\s+(\d+)[^>]*?/Type\s*/Pages\b")

_OP_TEXT = "text"
_OP_CONVERT = "convert"
_OPS = (_OP_TEXT, _OP_CONVERT)


class PdfTooComplexError(Exception):
    """The PDF hit a page, memory or time limit."""


class PdfTimeoutError(PdfTooComplexError):
    """The child ran past its wall-clock limit. Still a limit on the request
    path, but ambiguous: a CPU-starved node times out on a file that converts
    in seconds elsewhere, so the run does not blame the file for it."""


class PdfInterruptedError(Exception):
    """The child was stopped by a termination signal (_INTERRUPT_SIGNALS),
    not by anything in the PDF; retrying the same file can succeed."""


class EncryptedPdfError(Exception):
    """The PDF cannot be opened without a password."""


class UnreadablePdfError(Exception):
    """The PDF could not be parsed, or the child's reply could not be."""


class PdfBusyError(Exception):
    """Every PDF child slot is taken."""


@dataclass(frozen=True)
class ConversionResult:
    """What the run needs back from a conversion (the converter's
    ConversionReport, minus everything the orchestrator does not read)."""
    image_only_pages: list[int]


@dataclass(frozen=True)
class PdfText:
    """The upload guard's read of a PDF: every page's text, the page count,
    and the 1-based pages that hold only images (the converter's rule), so
    a mostly scanned PDF is refused before it costs a run (#1282)."""
    text: str
    pages: int
    image_only_pages: list[int]


# --- parent side ---------------------------------------------------------


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if k in _CHILD_ENV_KEYS or k.startswith(_CHILD_ENV_PREFIXES)}


def _parse_reply(op: str, stdout: bytes) -> object:
    try:
        status, value = json.loads(stdout)
    except (ValueError, TypeError) as e:  # empty, garbled or wrong-shaped
        raise UnreadablePdfError(f"PDF {op} child reply unparseable: {stdout[:80]!r}") from e
    if status == "ok":
        return value
    if status == "too_many_pages":
        raise PdfTooComplexError(f"PDF has more than {PDF_MAX_PAGES} pages (found {value})")
    if status == "out_of_memory":
        raise PdfTooComplexError(f"PDF {op} ran out of memory: {value}")
    if status == "encrypted":
        raise EncryptedPdfError()
    if status == "unreadable":
        raise UnreadablePdfError(value)
    raise UnreadablePdfError(f"unknown PDF child status {status!r}")


def run_pdf_job(op: str, args: list[str], timeout_seconds: float,
                slot_wait_seconds: float = 0) -> object:
    """Run `op` on `args` in a limited child; return its JSON result.

    Takes one of PDF_CHILD_SLOTS first: without blocking when
    slot_wait_seconds is 0 (the request path), else waiting up to that long.

    Raises PdfBusyError (no slot), PdfTooComplexError (a page or memory
    limit; its subclass PdfTimeoutError for the time limit),
    PdfInterruptedError (the child was stopped by a termination signal),
    EncryptedPdfError, UnreadablePdfError (any other failure of the parse,
    or a reply that cannot be read), or RuntimeError when the child exits
    non-zero for a reason that is not a limit -- a deployment fault such as
    RLIMIT_AS failing to apply on Linux, surfaced as a 500.
    """
    acquired = (_slots.acquire(blocking=False) if slot_wait_seconds <= 0
                else _slots.acquire(timeout=slot_wait_seconds))
    if not acquired:
        raise PdfBusyError(f"all {PDF_CHILD_SLOTS} PDF child slots busy")
    try:
        cmd = [sys.executable, "-c", _CHILD_BOOTSTRAP, str(_BACKEND_DIR), str(_SRC_DIR), op, *args]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, timeout=timeout_seconds,
                                  check=False, env=_child_env())
        except subprocess.TimeoutExpired as e:  # subprocess.run has already killed it
            # The load average tells a starved node from a heavy file (#1566).
            logger.warning("PDF %s child exceeded %ss; load average %s",
                           op, timeout_seconds, os.getloadavg())
            raise PdfTimeoutError(f"PDF {op} exceeded {timeout_seconds}s") from e
    finally:
        _slots.release()
    if -proc.returncode in _INTERRUPT_SIGNALS:
        raise PdfInterruptedError(f"PDF {op} child stopped by signal {-proc.returncode}")
    if proc.returncode == _EXIT_OUT_OF_MEMORY or proc.returncode < 0:
        # < 0: killed by a signal -- SIGKILL from the kernel or SIGSEGV/SIGABRT
        # when an allocation fails under RLIMIT_AS outside Python's control.
        raise PdfTooComplexError(f"PDF {op} child exited {proc.returncode}")
    if proc.returncode != 0:
        raise RuntimeError(f"PDF {op} child failed with exit code {proc.returncode}")
    return _parse_reply(op, proc.stdout)


def read_pdf(content: bytes) -> PdfText:
    """Every page's text plus which pages are image-only, for the upload's
    readable-text guards. Never waits for a slot: the request path answers
    PdfBusyError with a 503."""
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(content)
        tmp.flush()
        result = run_pdf_job(_OP_TEXT, [tmp.name], PDF_TEXT_TIMEOUT_SECONDS)
    if not isinstance(result, dict):
        raise UnreadablePdfError(f"PDF text child returned {type(result).__name__}")
    text, pages, image_only = result.get("text"), result.get("pages"), result.get("image_only_pages")
    if (not isinstance(text, str) or not isinstance(pages, int) or not isinstance(image_only, list)
            or not all(isinstance(n, int) for n in image_only)):
        raise UnreadablePdfError(f"PDF text child returned {result!r:.80}")
    return PdfText(text=text, pages=pages, image_only_pages=image_only)


def extract_pdf_text(content: bytes) -> str:
    """The text of every page (read_pdf, text only)."""
    return read_pdf(content).text


def convert_pdf(pdf_path: str | Path, docx_path: str | Path) -> ConversionResult:
    """convert_pdf_to_docx, under the same limits; waits up to
    PDF_CONVERT_SLOT_WAIT_SECONDS for a slot."""
    result = run_pdf_job(_OP_CONVERT, [str(pdf_path), str(docx_path)],
                         PDF_CONVERT_TIMEOUT_SECONDS, PDF_CONVERT_SLOT_WAIT_SECONDS)
    pages = result.get("image_only_pages") if isinstance(result, dict) else None
    if not isinstance(pages, list) or not all(isinstance(n, int) for n in pages):
        raise UnreadablePdfError(f"PDF convert child returned {result!r:.80}")
    return ConversionResult(image_only_pages=pages)


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
    counts = (a or b for a, b in _PAGES_COUNT_RE.findall(data))
    return max((int(c) if len(c) <= _MAX_COUNT_DIGITS else 10 ** _MAX_COUNT_DIGITS
                for c in counts), default=0)


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


def _counted_pages(path: str) -> int | None:
    """Pages in the page tree, counted by walking it and stopping one past
    PDF_MAX_PAGES -- not the declared /Count, which a tree can lie about.

    Returns None when the document cannot be opened without a password.
    Encryption is read off the trailer's /Encrypt dictionary (a probe
    document that never decrypts), so it does not depend on which exception
    a given security handler raises.
    """
    from itertools import islice

    from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

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
        return sum(1 for _ in islice(PDFPage.create_pages(doc), PDF_MAX_PAGES + 1))


def _page_texts(path: str) -> dict:
    import pdfplumber

    from unified_pipeline.core.pdf_to_docx import is_image_only_page

    parts = []
    image_only = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            parts.append(page.extract_text() or "")
            if is_image_only_page(page):
                image_only.append(number)
            page.close()  # release the page's parsed objects as we go
    return {"text": "\n".join(parts), "pages": len(parts), "image_only_pages": image_only}


def _convert(pdf_path: str, docx_path: str) -> dict:
    from unified_pipeline.core.pdf_to_docx import convert_pdf_to_docx

    return {"image_only_pages": convert_pdf_to_docx(pdf_path, docx_path).image_only_pages}


def _run_op(op: str, args: list[str]) -> tuple[str, object]:
    raw_pages = _raw_declared_pages(args[0])
    if raw_pages > PDF_MAX_PAGES:
        return "too_many_pages", raw_pages
    pages = _counted_pages(args[0])
    if pages is None:
        return "encrypted", None
    if pages > PDF_MAX_PAGES:
        return "too_many_pages", pages
    return "ok", _page_texts(args[0]) if op == _OP_TEXT else _convert(*args)


def _child_main() -> None:
    """Entry point of the child interpreter: argv = [backend, src, op, *args]."""
    _limit_address_space()
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # library prints must never corrupt the JSON reply
    op, args = sys.argv[3], sys.argv[4:]
    if op not in _OPS:
        raise SystemExit(f"pdf_sandbox: unknown op {op!r}")  # a caller bug: exit 1
    try:
        reply = _run_op(op, args)
    except MemoryError:
        os._exit(_EXIT_OUT_OF_MEMORY)
    except Exception as e:  # noqa: BLE001 -- untrusted input: every parse failure is the PDF's
        # pdfminer/pdfplumber raise unwrapped TypeError/KeyError/ValueError
        # on malformed structure (a page without /MediaBox, a 5,000-digit
        # number), not only their own exception types; any of them means
        # this PDF cannot be read, which the caller answers with a 400.
        reply = ("out_of_memory" if _wraps_memory_error(e) else "unreadable",
                 f"{type(e).__name__}: {e!r:.200}")
        sys.stderr.write(f"pdf_sandbox: {op} failed: {reply[1]}\n")
    out.write(json.dumps(reply).encode())
    out.flush()
