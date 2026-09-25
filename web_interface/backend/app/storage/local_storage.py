"""Local filesystem implementation of RunStorage.

Used in development. Reads/writes to a configurable base directory
(default: web_interface/uploads/).
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from app.storage.base import (
    RunStorage,
    StorageKeyExists,
    StorageKeyNotFound,
    validate_key,
    validate_run_id,
    validate_run_key,
)

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` via a temp file in the same directory + os.replace.

    put_file/put_global used to end in path.write_bytes(data): a process
    interrupted mid-write left a truncated file readable under the final
    key, which a later read consumed as if it were complete (#787). The temp
    file lives in path.parent so os.replace is a same-filesystem rename, not
    a copy, and is cleaned up if the write itself fails. put_file_exclusive
    is untouched: open(path, "xb") is already atomic and its collision
    semantics (StorageKeyExists on a pre-existing key) must not change.

    mkstemp() creates the temp file at 0o600 (owner-only), and os.replace()
    preserves the SOURCE file's mode, not the destination's -- so without an
    explicit chmod, every put_file/put_global regressed from the pre-#787
    path.write_bytes() default (0o666 minus umask, 0o644 under the standard
    022 umask) to owner-only. Nothing reads these files as a different OS
    user: the backend process and every pipeline stage it invokes run as the
    same container user (web_interface/backend/Dockerfile: USER cviche, uid
    1000; local dev has no second user either), so restore the pre-existing
    permissions rather than narrow them.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        # BaseException, not Exception: clean up the temp file even on Ctrl-C
        # or task cancellation, then re-raise.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        except OSError as cleanup_err:
            # Log rather than raise, so a failed cleanup never replaces the
            # original error as the one that propagates.
            logger.warning("failed to clean up temp file %s: %s", tmp_name, cleanup_err)
        raise


class LocalRunStorage(RunStorage):
    """Store run artifacts on the local filesystem.

    Directory layout:
        {base_dir}/{run_id}/{key}

    For example:
        uploads/A1B2C3/input/cv.docx
        uploads/A1B2C3/steps/3a/output.json
    """

    def __init__(self, base_dir: str | None = None):
        if base_dir:
            self._base = Path(base_dir)
        else:
            # Default: CVICHE_LOCAL_STORAGE_DIR env var, or web_interface/uploads/
            env_dir = os.environ.get("CVICHE_LOCAL_STORAGE_DIR")
            if env_dir:
                self._base = Path(env_dir)
            else:
                # Resolve relative to the backend package:
                # app/storage/local_storage.py -> app/storage -> app -> backend -> web_interface
                self._base = Path(__file__).resolve().parent.parent.parent.parent / "uploads"
        self._base.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, *parts: str) -> Path:
        """Join parts under the storage base, proving the result stays inside it.

        The string-level rules (validate_run_id / validate_key) reject ".."
        and absolute components, but a symlink already inside the store can
        redirect a perfectly well-formed path outside it, so containment is
        also checked against fully resolved paths -- Path.resolve() follows
        symlinks, and Path.is_relative_to answers the containment question.

        The candidate itself usually does not exist yet (put_file_exclusive's
        whole point is that it must not), so the check resolves the deepest
        component that DOES exist: the file itself on a read, its parent
        directory on a create. That keeps the exclusive create exclusive --
        nothing here touches or stats the target as a precondition.

        Raises:
            ValueError: If the resolved path is not inside the storage base.
        """
        # ponytail: resolve-then-open, so a symlink planted between the check
        # and the open would still be followed. Closing that gap needs
        # dir-relative I/O (os.open with O_NOFOLLOW walking each component
        # under a dirfd), which is a rewrite of every operation in this class.
        # Every key this store is asked for is server-built, so the
        # string-rules + resolved-containment check is the level bought here;
        # the upgrade path is that dirfd walk, in one place, if the store ever
        # accepts a caller-shaped key.
        candidate = self._base.joinpath(*parts)
        probe = candidate
        while not os.path.lexists(probe) and probe != probe.parent:
            probe = probe.parent
        if not probe.resolve().is_relative_to(self._base.resolve()):
            raise ValueError(f"storage path escapes the storage base: {candidate}")
        return candidate

    def _resolve(self, run_id: str, key: str) -> Path:
        """Resolve a run_id + key to an absolute filesystem path.

        Validates at the boundary so put_file, put_file_exclusive, get_file
        and exists all inherit the same invariant from one place.
        """
        validate_run_key(run_id, key)
        return self._safe_path(run_id, key)

    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        path = self._resolve(run_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, data)

    def put_file_exclusive(self, run_id: str, key: str, data: bytes) -> None:
        path = self._resolve(run_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as f:
                f.write(data)
        except FileExistsError as e:
            raise StorageKeyExists(f"{run_id}/{key} already exists") from e

    def put_global(self, key: str, data: bytes) -> None:
        validate_key(key)
        path = self._safe_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, data)

    def _delete_tree(self, path: Path) -> int:
        """Recursively delete a directory tree, returning the file count removed.

        Idempotent (returns 0 if absent). Refuses to delete the storage base
        itself, so an empty/degenerate key can't wipe the whole store.

        Walks the tree twice -- once to count files, once in rmtree -- which
        is fine for a run directory (tens of files); count while deleting if
        this ever serves large trees (#792).
        """
        if path.resolve() == self._base.resolve():
            raise ValueError("refusing to delete the storage base directory")
        if not path.exists():
            return 0
        count = sum(1 for p in path.rglob("*") if p.is_file())
        shutil.rmtree(path)
        return count

    def delete_run(self, run_id: str) -> int:
        # Validate before building a recursive-delete path: a malformed id
        # here turns a routing bug into a data-loss incident.
        validate_run_id(run_id)
        return self._delete_tree(self._safe_path(run_id))

    def delete_global_prefix(self, prefix: str) -> int:
        if not prefix or not prefix.strip("/"):
            raise ValueError("delete prefix must be non-empty")
        # Non-empty is not the same as safe: "../sibling" is non-empty and
        # would rmtree a directory beside the store.
        validate_key(prefix)
        return self._delete_tree(self._safe_path(prefix))

    def get_file(self, run_id: str, key: str) -> bytes:
        path = self._resolve(run_id, key)
        if not path.exists():
            raise StorageKeyNotFound(f"No such file: {run_id}/{key}")
        return path.read_bytes()

    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        validate_run_key(run_id, prefix)
        run_dir = self._safe_path(run_id)
        if not run_dir.exists():
            return []

        # Literal byte-prefix match against the full relative key, the same
        # as S3's Prefix -- not a directory match. An EXISTING-directory
        # prefix used to take a separate branch that globbed only inside
        # that directory, which misses a sibling key that merely starts with
        # the same string (prefix "input" naming a real input/ directory
        # still must also match a top-level "input_extra.txt"), and a
        # partial-path prefix naming no directory used to glob by basename,
        # which wrongly matched "input/deep/manifest.json" for prefix
        # "input/man" (#792). One literal-prefix pass over every file
        # handles both the same way S3 does.
        matches = []
        for path in run_dir.rglob("*"):
            if path.is_file():
                rel = str(path.relative_to(run_dir))
                if rel.startswith(prefix):
                    matches.append(rel)
        return sorted(matches)

    def get_download_url(
        self,
        run_id: str,
        key: str,
        expires_in: int = 300,
        download_name: str | None = None,
    ) -> str | None:
        # Local storage does not support presigned URLs.
        # The backend proxies the file directly instead.
        return None

    def exists(self, run_id: str, key: str) -> bool:
        return self._resolve(run_id, key).exists()
