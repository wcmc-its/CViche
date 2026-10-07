"""Turning uploaded bytes into a run (#1298): the upload core shared by POST
/upload, POST /inbox/submit and the worker's emailed-CV intake, plus what it
needs (run ids, the durable archive, the duplicate check, the duration
estimate). Lives in services/ so the worker can use it without importing
app.api or app.auth (see run_service.UPLOAD_DIR). app/api/upload.py re-exports
every name, so existing imports keep working.
"""
import hashlib
import json
import logging
import secrets
import string
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import commit_inserts_retrying_conflict
from app.errors import bad_request, duplicate_file, internal_error
from app.models import Run, RunState, Step, User, can_view_all_runs
from app.pipeline.step_registry import STEP_REGISTRY
from app.schemas import UploadResponse
from app.services.config_service import BASE_OVERHEAD_SECONDS, TIME_PER_1K_TOKENS
from app.services.input_format import detect_input_format_or_none
from app.services.pdf_sandbox import (
    PDF_BUSY_MESSAGE,
    PDF_TOO_COMPLEX_MESSAGE,
    PDF_UNREADABLE_MESSAGE,
    EncryptedPdfError,
    PdfBusyError,
    PdfText,
    PdfTooComplexError,
    UnreadablePdfError,
    read_pdf,
)
from app.services.run_service import UPLOAD_DIR, latest_run_with_hash
from app.services.template_warning import detect_wcm_template
from app.services.upload_validation import (
    ACTIVE_CONTENT_MESSAGE,
    MIN_EXTRACTED_CHARS,
    PDF_EXTENSION,
    SCANNED_PAGE_REJECT_SHARE,
    _extract_text,
    _validate_docx_magic,
    _validate_pdf_magic,
    docx_active_content,
    is_mostly_scanned,
)
from app.storage import get_storage
from app.storage.base import StorageKeyExists

logger = logging.getLogger(__name__)

# How the duplicate notice words the date a file was last processed.
DUPLICATE_DATE_FORMAT = "%B %-d, %Y"


# A PDF child slot frees within PDF_TEXT_TIMEOUT_SECONDS at worst and ~4 s
# for the largest real CV, so a retry this much later usually lands.
_PDF_BUSY_RETRY_AFTER_SECONDS = 10
_ENCRYPTED_PDF_MESSAGE = (
    "This PDF is password-protected, so we can't read it. Please remove the "
    "password, or upload the CV as a .docx."
)


# /estimate's placeholder char count when _extract_text couldn't read the
# document at all (#794) -- a mid-range guess so the quote shown is neither
# suspiciously cheap nor alarmingly expensive while the real content is
# unknown. Distinct from MIN_EXTRACTED_CHARS above, which gates /upload.
_ESTIMATE_FALLBACK_CHAR_COUNT = 5000
# Floor on the char count an estimate is sized from: a readable-but-blank
# document still costs a run's fixed per-stage overhead.
_ESTIMATE_MIN_CHAR_COUNT = 1000


def _estimate_char_count(extracted: str | None) -> int:
    """The char count both /estimate's quote and /upload's stall-watchdog
    duration are sized from, so one file gets one number (#794). ``None``
    (unreadable) takes the fixed fallback; /estimate flags that to the user."""
    if extracted is None:
        return _ESTIMATE_FALLBACK_CHAR_COUNT
    return max(len(extracted), _ESTIMATE_MIN_CHAR_COUNT)


def _page_ranges(pages: list[int]) -> str:
    """1-based page numbers as ranges: [2, 3, 4, 7] -> "2–4, 7"."""
    runs: list[list[int]] = []
    for n in pages:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)


def _reject_mostly_scanned_pdf(pdf: PdfText) -> None:
    """A 400 naming the scanned pages when they are SCANNED_PAGE_REJECT_SHARE
    or more of the PDF (#1282). Covers a fully scanned PDF too, with a
    message that says what to do instead of the generic no-text one."""
    if not is_mostly_scanned(pdf):
        return
    logger.info("Rejected mostly scanned PDF (%d of %d pages image-only)",
                len(pdf.image_only_pages), pdf.pages)
    noun = "Page" if len(pdf.image_only_pages) == 1 else "Pages"
    verb = "is a scanned image" if len(pdf.image_only_pages) == 1 else "are scanned images"
    raise bad_request(
        f"{noun} {_page_ranges(pdf.image_only_pages)} of this PDF {verb}, so "
        "their text can't be read. Export the PDF from the original document "
        "(or run OCR on it) and upload it again, or upload the CV as a .docx."
    )


def _read_upload_text(content: bytes, file_ext: str) -> tuple[str | None, list[int]]:
    """`_extract_text`, plus a PDF's image-only pages (empty for a docx).
    Refuses a mostly scanned PDF with a 400."""
    if file_ext != PDF_EXTENSION:
        return _extract_text(content, file_ext), []
    pdf = read_pdf(content)
    _reject_mostly_scanned_pdf(pdf)
    return pdf.text, pdf.image_only_pages


async def _reject_active_docx_content(content: bytes) -> None:
    """A 400 for a .docx carrying macros, a network-linked part or a DDE field
    (#1334), shared by /upload and /estimate; scanned off the event loop."""
    if await run_in_threadpool(docx_active_content, content) is not None:
        raise bad_request(ACTIVE_CONTENT_MESSAGE)


async def _extract_text_or_400(content: bytes, file_ext: str) -> tuple[str | None, list[int]]:
    """`_read_upload_text` off the event loop (#793), shared by /upload and
    /estimate: every PDF refusal is a 400, a full PDF sandbox a 503."""
    try:
        return await run_in_threadpool(_read_upload_text, content, file_ext)
    except EncryptedPdfError:
        raise bad_request(_ENCRYPTED_PDF_MESSAGE)
    except PdfTooComplexError as e:
        logger.warning("Rejected PDF over a parse limit: %s", e)
        raise bad_request(PDF_TOO_COMPLEX_MESSAGE)
    except UnreadablePdfError as e:
        logger.warning("Rejected unreadable PDF: %s", e)
        raise bad_request(PDF_UNREADABLE_MESSAGE)
    except PdfBusyError:
        raise HTTPException(
            status_code=503,
            detail={"error": "pdf_busy", "message": PDF_BUSY_MESSAGE},
            headers={"Retry-After": str(_PDF_BUSY_RETRY_AFTER_SECONDS)},
        )


# Recalibrated after the #881 parallel LLM batches went live (dev-207): the old
# 20 s/stage overhead and 3-min floor quoted ~2.5x the real wall time on every
# post-parallel prod run (e.g. estimate max 421 s vs actual 89-105 s). With 5 s
# and a 1-min floor, all six runs from 2026-09-28/29 land inside [min, max].
_PER_STAGE_OVERHEAD_SECONDS = 5
_MIN_ESTIMATE_SECONDS = 60
_MIN_ESTIMATE_SPREAD_SECONDS = 60


def estimate_run_seconds(text_char_count: int) -> tuple[int, int]:
    """(min, max) wall-clock seconds for a full run, scaled to document size.

    Token-based (≈4 chars/token), tuned to observed runs (~8 min for 3500
    tokens). Used by /estimate for the pre-run quote AND stored per-run so the
    client stall watchdog can scale its "taking longer than expected" threshold
    to the actual CV instead of a fixed constant. Single source of truth for
    both, so the quote shown and the threshold applied never drift.
    """
    text_char_count = max(text_char_count, 1000)
    estimated_tokens = text_char_count // 4
    base_time = BASE_OVERHEAD_SECONDS + (estimated_tokens / 1000) * TIME_PER_1K_TOKENS
    total_time = base_time + len(STEP_REGISTRY) * _PER_STAGE_OVERHEAD_SECONDS
    time_min = max(int(total_time * 0.6), _MIN_ESTIMATE_SECONDS)
    time_max = max(int(total_time * 1.3), time_min + _MIN_ESTIMATE_SPREAD_SECONDS)
    return time_min, time_max


# Letters only: a code read aloud or retyped never confuses O/0 or I/1.
_RUN_ID_ALPHABET = string.ascii_uppercase


def generate_run_id() -> str:
    """Generate a unique 6-character run ID like 'QZKMRT'.

    Draws each of the 6 characters uniformly from A-Z (26 symbols) via
    secrets.choice, giving 26**6 ~= 3.09e8 equally-likely ids. Ids issued
    before letters-only (A-Z0-9) stay valid: every consumer accepts the
    wider set. The A-Z0-9 generator itself narrowed
    the alphabet from the prior generator's: `secrets.token_urlsafe(4)[:6]
    .upper()` emitted base64url output (which can include `-` and `_`)
    case-folded onto 38 symbols, and truncating to 6 chars from a 4-byte
    (32-bit) draw left the 6th character able to take only 4 distinct
    values -- collapsing the last position to ~4 outcomes and the whole id
    to ~2e8 effective values instead of the nominal 36**6 (#685). Every
    existing consumer already accepts the narrower `^[A-Z]{6}$` set --
    steps.py's `_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")` is a
    superset -- so no consumer needed a change.

    Six characters stay the canonical run id (#797 decision,
    2026-09-09): a collision now costs a redraw in create_run_archive, never
    an overwrite, so the id width sets only the redraw rate. With N existing
    runs a fresh draw collides with probability ~N / 26**6: ~3.2e-4 at 1e5
    runs, ~3.2e-3 at 1e6. Migration trigger: when the run table approaches
    ~1e5 rows (redraws near 3e-4 per upload), move to a
    UUID/ULID canonical id with this 6-char form kept as a display id --
    widening touches Run.id (String(10)), artifact basenames and download
    URLs. Exhaustion of every redraw is logged at ERROR by both callers of
    create_run_archive (upload and restart).
    """
    return "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(6))


# Bound on how many times a fresh run id may be regenerated after a storage
# collision before the request is failed outright (#685). Kept small and
# named rather than inline: at ~2.18e9 uniform ids (see generate_run_id), a
# single draw collides with probability ~N / 2.18e9 for N existing runs, so
# more than a couple of
# regenerations would only ever fire under a genuine storage fault, not an
# id collision -- in which case retrying further just delays surfacing it.
_RUN_ID_ATTEMPTS = 5


class RunIdAttemptsExhausted(Exception):
    """Raised when every retry to allocate a collision-free run id failed."""


def create_run_archive(
    content: bytes,
    file_ext: str,
    build_manifest: Callable[[str, str], bytes],
    write_local: Callable[[Path], None],
) -> tuple[str, str, Path, bytes]:
    """Allocate a fresh run id and durably archive `content` under it.

    Exclusive-creates the durable archive via put_file_exclusive so a run-id
    collision can never silently overwrite another run's archived input
    (#685) -- this is what closes the overwrite, not the pod-local write
    below. input/manifest.json is written BEFORE input/{stored_name}: the
    manifest is the collision sentinel, so a colliding id is caught before
    any CV content byte lands under another run's prefix (verifier finding,
    round 2) -- writing content first could, on an extension mismatch (this
    id already archived under a *different* extension), let the new file
    land in the other run's namespace ahead of the manifest check catching
    it. On a collision (StorageKeyExists, or FileExistsError from
    `write_local`) the id is regenerated and the whole attempt retried, up
    to _RUN_ID_ATTEMPTS times; a colliding attempt's own partial pod-local
    file is removed before the retry so failed attempts never accumulate on
    disk.

    If the manifest write succeeds but the *content* write then fails, the
    manifest is left orphaned under the abandoned id with no run row ever
    created for it. This is harmless and not cleaned up: (1) if the content
    write failed with StorageKeyExists, that would require this id's content
    key to already exist while its manifest.json did not -- a state nothing
    in this codebase can produce, since create_run_archive is the only
    writer of any `input/` key (verified by grep) and always writes the
    manifest first; (2) if it failed with a real storage fault, the request
    is failing anyway (fatal per #170) and the orphaned manifest carries only
    this same requesting user's own filename/email, so it is not a
    cross-user disclosure -- just an unclaimed object nothing ever lists or
    references. RunStorage has no single-key delete (only whole-run
    delete_run, which in a genuine collision would risk deleting the OTHER
    run's real files sharing that id -- unsafe to call here), so proactively
    deleting it would need a new storage primitive for a branch that cannot
    currently occur.

    Shared by /upload and restart_run so both close the same race the same
    way (runs.py imports this rather than duplicating the retry loop).

    Args:
        content: the file bytes to archive (the raw upload, or the original
            run's input being copied forward on restart).
        file_ext: dotted extension, e.g. ".docx".
        build_manifest: (run_id, stored_name) -> the manifest JSON to archive
            alongside the file. Caller-supplied because /upload's and
            restart_run's manifests carry different provenance fields.
        write_local: writes the pod-local copy at the given path. Both
            callers use an exclusive `"xb"` open, so a pod-local id
            collision raises FileExistsError and this loop retries with a
            fresh id rather than overwriting another run's local input.

    Returns:
        (run_id, stored_name, file_path, manifest) for the archived file.

    Raises:
        RunIdAttemptsExhausted: every attempt collided.
        Exception: a non-collision storage failure, propagated as-is (fatal,
            not retried -- mirrors #170: no run is created on an archive
            failure that isn't a collision).
    """
    storage = get_storage()
    for _ in range(_RUN_ID_ATTEMPTS):
        run_id = generate_run_id()
        stored_name = f"{run_id}.{file_ext.lstrip('.')}"
        file_path = UPLOAD_DIR / stored_name
        try:
            write_local(file_path)
        except FileExistsError:
            # Pod-local id collision (vanishingly rare -- see generate_run_id).
            # Regenerate rather than overwrite another upload's local copy.
            continue
        manifest = build_manifest(run_id, stored_name)
        try:
            # Manifest first: it is the collision sentinel, so a colliding id
            # is caught before any CV content byte is written (see docstring).
            storage.put_file_exclusive(run_id, "input/manifest.json", manifest)
            storage.put_file_exclusive(run_id, f"input/{stored_name}", content)
        except StorageKeyExists:
            # Same run id already has an archive: another request won the
            # race. Discard this attempt's local file and try a fresh id.
            _unlink_best_effort(file_path)
            continue
        except Exception:
            # A real storage fault, not a collision -- fatal per #170: clean
            # up and propagate, do not retry.
            _unlink_best_effort(file_path)
            raise
        return run_id, stored_name, file_path, manifest
    raise RunIdAttemptsExhausted(
        f"could not allocate a collision-free run id after {_RUN_ID_ATTEMPTS} attempts"
    )


def _unlink_best_effort(path: Path) -> None:
    """Remove a leftover pod-local file, never raising.

    Cleanup after an aborted attempt -- an OSError here (permissions, a
    concurrent delete) must not mask the original failure being handled.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# The 500 a run creation that failed after its archive returns (#802). Which
# one depends on whether the compensation actually removed what was archived:
# the submitter is told "nothing was saved" only when that is true.
RUN_NOT_CREATED_NOTHING_SAVED = (
    "We couldn't finish creating your run, so nothing was saved. "
    "Please try again in a moment."
)
RUN_NOT_CREATED_FILE_KEPT = (
    "We couldn't finish creating your run. Your file was stored, but no run "
    "was created for it. Please try again in a moment."
)


def _compensate_failed_run(run_id: str, email: str, file_path: Path) -> bool:
    """Undo a durable archive after creating the run row that should have
    followed it failed (#802). run_id was generated fresh for this request and
    no row ever referenced it, so deleting its whole storage namespace is safe
    here -- nothing else can live under it (unlike a general run_id, this one
    is never reused for another archive).

    Both deletes are attempted even if the first raises; each failure is
    logged. Returns True only when both succeeded, i.e. nothing archived for
    this request is left in storage.

    # ponytail: compensation runs in-process; a crash between archive and
    # this block still orphans -- a storage-keyed sweep is the upgrade path
    # (#802 option 2).
    """
    storage = get_storage()
    removed = True
    try:
        storage.delete_run(run_id)
    except Exception:
        removed = False
        logger.exception("Compensating delete_run failed for orphaned run %s", run_id)
    try:
        # Mirrors the exact key put_global wrote the index under.
        storage.delete_global_prefix(f"by-submitter/{email.lower()}/{run_id}/")
    except Exception:
        removed = False
        logger.exception("Compensating delete_global_prefix failed for orphaned run %s", run_id)
    _unlink_best_effort(file_path)
    if not removed:
        logger.error(
            "Compensation incomplete: run %s's input archive is still in storage "
            "with no run row (#802)", run_id,
        )
    return removed


def _rollback_logged(db: Session, run_id: str) -> None:
    """Roll the session back, logging rather than raising if that fails too.

    A dead connection can make rollback() raise; that must not skip the
    compensation that follows it (#802), and the original failure is the
    one the caller reports.
    """
    try:
        db.rollback()
    except Exception:
        logger.exception("Session rollback failed while compensating run %s", run_id)


@contextmanager
def compensated_run_creation(
    db: Session, run_id: str, email: str, file_path: Path,
) -> Iterator[None]:
    """Wrap everything between a durable archive and its row commit (#802).

    The body stages the Run/Step rows; on a clean exit they are committed.
    If the body or the commit raises, the session is rolled back, the archive
    is compensated, and a 500 is raised whose message says whether the file
    is still stored. Shared by /upload and restart_run so both write paths
    handle a failure after the archive identically. A one-off write conflict
    (MariaDB 1020) gets one retry in a fresh transaction before it counts as
    a failure (#1285).
    """
    try:
        yield
        commit_inserts_retrying_conflict(db)
    except Exception as exc:
        logger.exception(
            "Run row creation or commit failed after a durable archive; compensating (run=%s)",
            run_id,
        )
        _rollback_logged(db, run_id)
        removed = _compensate_failed_run(run_id, email, file_path)
        raise internal_error(
            RUN_NOT_CREATED_NOTHING_SAVED if removed else RUN_NOT_CREATED_FILE_KEPT
        ) from exc


class DuplicateInfo(NamedTuple):
    """An earlier run of the same file: when, and the run id if the viewer may see it."""
    processed_on: str
    run_id: str | None


def duplicate_info(db: Session, sha256: str, user: User) -> DuplicateInfo | None:
    """The #1286 privacy rule in one place: any run (any submitter) holding this
    file's hash counts, but other users get only the date; the run id goes to
    admins and staff (who may read every run) and to the run's own submitter,
    never anyone else's."""
    latest = latest_run_with_hash(db, sha256)
    if latest is None:
        return None
    can_see_run = can_view_all_runs(user) or latest.user_id == user.id
    return DuplicateInfo(latest.started_at.strftime(DUPLICATE_DATE_FORMAT), latest.id if can_see_run else None)


def _reject_unconfirmed_duplicate(db: Session, sha256: str, user: User) -> None:
    """Stop with a 409 when any run (any submitter) already holds this file's hash.

    Raised before anything is archived or charged."""
    info = duplicate_info(db, sha256, user)
    if info is not None:
        raise duplicate_file(
            f"This file was already processed on {info.processed_on}. Run it again?",
            info.processed_on,
            info.run_id,
        )


def _archive_or_502(
    content: bytes,
    file_ext: str,
    build_manifest: Callable[[str, str], bytes],
    write_local: Callable[[Path], None],
) -> tuple[str, str, Path, bytes]:
    """create_run_archive, with a storage failure turned into the 502 that creates no run (#170)."""
    try:
        return create_run_archive(content, file_ext, build_manifest, write_local)
    except Exception as e:
        logger.error("Durable archive of upload failed; aborting upload: %s", e)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "storage_unavailable",
                "message": (
                    "We couldn't store your file securely, so no run was created. "
                    "Please try again in a moment."
                ),
            },
        )


def _add_pending_steps(db: Session, run_id: str) -> None:
    """Stage one pending Step row per STEP_REGISTRY entry for a new run."""
    for step_def in STEP_REGISTRY:
        step = Step(
            run_id=run_id,
            step_number=step_def.number,
            stage_id=step_def.stage_id,
            step_name=step_def.name,
            status="pending"
        )
        db.add(step)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """How one CV becomes a run: the attestation, the render options and the
    batch it joins. Shared by /upload and POST /inbox/submit (#1298)."""
    submission_type: Literal["own_cv", "authorized_admin"]
    include_track_changes: bool = True
    include_classification_comments: bool = False
    strip_wcm_instructions: bool = True
    batch_id: str | None = None
    confirm_duplicate: bool = False


async def create_run_from_bytes(
    db: Session, current_user: User, *, filename: str, content: bytes,
    content_type: str | None, request: RunRequest,
) -> UploadResponse:
    """The upload core (#1298): validate the bytes -> duplicate check -> extract
    text -> archive -> Run row. The caller has already checked consent, the
    batch, the filename and the rate limit, and read ``content`` within the
    size cap. Raises the same HTTPErrors /upload always did."""
    file_ext = Path(filename).suffix.lower()
    submission_type = request.submission_type
    include_track_changes = request.include_track_changes
    include_classification_comments = request.include_classification_comments
    strip_wcm_instructions = request.strip_wcm_instructions
    batch_id = request.batch_id
    confirm_duplicate = request.confirm_duplicate
    # Validate magic bytes match claimed extension
    if file_ext == PDF_EXTENSION and not _validate_pdf_magic(content):
        logger.warning("[SECURITY] Rejected upload: file claims .pdf but magic bytes do not match (user=%s)", current_user.email)
        raise bad_request("File content does not match .pdf format. The file may be corrupted or mislabeled.")
    if file_ext == ".docx" and not _validate_docx_magic(content):
        logger.warning("[SECURITY] Rejected upload: file claims .docx but magic bytes do not match (user=%s)", current_user.email)
        raise bad_request("File content does not match .docx format. The file may be corrupted or mislabeled.")
    if file_ext == ".docx":
        await _reject_active_docx_content(content)

    # Same bytes already run by anyone? Ask first (nothing archived or charged yet).
    source_sha256 = hashlib.sha256(content).hexdigest()
    if not confirm_duplicate:
        _reject_unconfirmed_duplicate(db, source_sha256, current_user)

    # Reject documents we can't read (scanned images, password-protected, blank).
    # These pass the magic-byte check but yield no text, so they would burn LLM
    # calls and return empty output with no explanation to the user. Fail open
    # (extracted is None) if extraction couldn't run, to avoid blocking valid files.
    extracted, scanned_pages = await _extract_text_or_400(content, file_ext)
    if extracted is not None and len(extracted.strip()) < MIN_EXTRACTED_CHARS:
        logger.info("Rejected upload with no readable text (user=%s, chars=%d)", current_user.email, len(extracted.strip()))
        raise bad_request(
            "We couldn't read any text from this file. It may be a scanned image, "
            "password-protected, or empty. Please upload a text-based Word document or PDF."
        )

    # Cheap, no-LLM check: does this look like the *blank* WCM CV template?
    # Reuses the already-extracted text -- a filled CV matches the blank-template
    # string set on almost no lines, an unfilled template on nearly all of them.
    # Best-effort and non-fatal: detect_wcm_template swallows its own errors and
    # returns (False, None), so this never blocks an upload. We only warn (the UI
    # requires an acknowledgement) -- we never reject, since reformatting an
    # existing publication list is a legitimate, template-shaped use.
    wcm_template_warning, wcm_template_match_ratio = await run_in_threadpool(
        detect_wcm_template, extracted
    )
    if wcm_template_warning:
        logger.info(
            "Upload looks like a blank WCM template (user=%s, match_ratio=%s)",
            current_user.email, wcm_template_match_ratio,
        )

    # Was this CV written in the WCM template (filled in) or another format?
    # Recorded on the run for score comparisons; best-effort, NULL on failure.
    input_format, input_format_score = await run_in_threadpool(
        detect_input_format_or_none, extracted
    )

    # Allocate a fresh run id and durably archive the ORIGINAL upload to the
    # run's storage namespace BEFORE creating the run record. The pod-local
    # copy is ephemeral (lost on a pod recycle), so the archive is the run's
    # only recoverable input. Both the pod-local write and the archive are
    # exclusive creates: a run-id collision on either regenerates the id and
    # retries (#685) rather than silently overwriting another run's file. A
    # failure here (a real storage fault, or every retry colliding) is
    # therefore FATAL: we abort with an error and create NO run record,
    # rather than commit a "created" run whose input can't be recovered and
    # which would linger as an orphan in the DB and on the Runs dashboard
    # (and leave half-written objects in the store). Stop on error -- do not
    # proceed. (issue #170)
    def _build_manifest(run_id: str, stored_name: str) -> bytes:
        return json.dumps({
            "run_id": run_id,
            "original_filename": filename,  # only record of the real name
            "stored_as": stored_name,
            "file_type": file_ext[1:],
            "size_bytes": len(content),
            "sha256": source_sha256,
            "content_type": content_type,
            "uploaded_at": datetime.now().isoformat(),
            "user_email": current_user.email,
        }, indent=2).encode("utf-8")

    def _write_local(path: Path) -> None:
        with open(path, "xb") as f:
            f.write(content)

    run_id, stored_name, file_path, manifest = _archive_or_502(
        content, file_ext, _build_manifest, _write_local,
    )
    # Everything from here to the commit compensates the archive on failure (#802).
    with compensated_run_creation(db, run_id, current_user.email, file_path):
        storage = get_storage()

        # Cross-run, browsable-by-submitter index: the same manifest keyed under the
        # submitter so runs can be found by who uploaded them in S3 without opening
        # each run folder. This is a navigation pointer only -- the run and its input
        # are already durably stored above and the pipeline does not read it. So,
        # unlike the archive, this stays BEST-EFFORT: a failure here must never fail
        # the upload or orphan a run.
        try:
            storage.put_global(
                f"by-submitter/{current_user.email.lower()}/{run_id}/manifest.json",
                manifest,
            )
        except Exception as e:
            logger.warning("Failed to write by-submitter index (run=%s): %s", run_id, e)

        # Input-scaled wall-clock estimate, stored so the client stall watchdog can
        # scale its "taking longer than expected" threshold to this CV instead of a
        # fixed constant (large CVs were false-positiving as "may be stuck"). Same
        # helper and char count as /estimate.
        _, estimated_duration_seconds = estimate_run_seconds(_estimate_char_count(extracted))

        # Create run record. Persist the user's output-rendering choices (issue
        # #153) as the truthy ints the Stage 6 generator reads at render time.
        run = Run(
            id=run_id,
            filename=filename,
            file_type=file_ext[1:],  # Remove dot
            status=RunState.CREATED,
            started_at=datetime.now(),
            user_id=current_user.id,
            submission_type=submission_type,
            estimated_duration_seconds=estimated_duration_seconds,
            show_track_changes=1 if include_track_changes else 0,
            show_pipeline_comments=1 if include_classification_comments else 0,
            strip_template_instructions=1 if strip_wcm_instructions else 0,
            batch_id=batch_id,
            input_format=input_format,
            input_format_score=input_format_score,
            source_sha256=source_sha256,
            scanned_pages=",".join(map(str, scanned_pages)) or None,
        )
        db.add(run)
        _add_pending_steps(db, run_id)

    return UploadResponse(
        run_id=run_id,
        filename=filename,
        file_type=file_ext[1:],
        status=RunState.CREATED,
        message=f"File uploaded successfully. Run ID: {run_id}",
        wcm_template_warning=wcm_template_warning,
        wcm_template_match_ratio=wcm_template_match_ratio,
    )
