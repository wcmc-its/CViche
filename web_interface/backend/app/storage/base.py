"""Abstract base class for run storage backends."""

import re
from abc import ABC, abstractmethod


class StorageKeyExists(Exception):
    """Raised by put_file_exclusive when the target key already has an object.

    Signals a run-id collision to the caller so it can regenerate the id and
    retry, rather than silently overwriting another run's archived file.
    Never raised by put_file, which always overwrites.
    """


# The widest run-id shape any existing Run.id row can hold -- the same pattern
# api/steps.py validates request run ids with. Deliberately NOT "^[A-Z0-9]{6}$":
# the previous generator (secrets.token_urlsafe(4)[:6].upper()) could emit "-"
# and "_", so rows written before it was replaced hold ids a stricter pattern
# would reject, and every read, download and reaper delete of those legacy runs
# would start raising.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Key path components that would walk out of the namespace the key is joined
# onto. An EMPTY component is deliberately allowed: callers legitimately pass
# "" and trailing-slash prefixes (list_files(run_id, "outputs/")).
_UNSAFE_KEY_COMPONENTS = frozenset({".", ".."})
_KEY_SEPARATORS = re.compile(r"[\\/]")


def validate_run_id(run_id: str) -> None:
    """Reject a run id that could reshape or escape the storage namespace.

    Defense in depth at the storage boundary: every run id reaching storage
    today has already been matched against a Run.id row by check_run_access,
    but this interface accepts arbitrary strings and delete_run builds a
    recursive-delete prefix out of one, so a malformed id is a data-loss
    hazard rather than a routing bug.

    Raises:
        ValueError: If run_id is not a plain identifier.
    """
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.match(run_id):
        raise ValueError(f"invalid run id for storage: {run_id!r}")


def validate_key(key: str) -> None:
    """Reject an absolute key, or one carrying a "." or ".." path component.

    Applies equally to run-scoped keys, global keys and delete prefixes: all
    three get joined onto a storage root, and no backend may let a
    caller-supplied string walk out of it. An empty key and a trailing slash
    stay legal -- list and delete prefixes use both.

    Raises:
        ValueError: If the key is absolute or has a relative component.
    """
    if not isinstance(key, str):
        raise ValueError(f"invalid storage key: {key!r}")
    if key.startswith(("/", "\\")):
        raise ValueError(f"storage key must be relative, not absolute: {key!r}")
    for component in _KEY_SEPARATORS.split(key):
        if component in _UNSAFE_KEY_COMPONENTS:
            raise ValueError(
                f"storage key must not contain a {component!r} component: {key!r}"
            )


def validate_run_key(run_id: str, key: str) -> None:
    """The check every run-scoped operation makes: both of the above."""
    validate_run_id(run_id)
    validate_key(key)


class RunStorage(ABC):
    """Interface for storing and retrieving run artifacts.

    All file I/O for uploads, pipeline outputs, and prompt logs goes through
    this interface. Two implementations exist:
    - LocalRunStorage: reads/writes to local filesystem (dev)
    - S3RunStorage: reads/writes to S3 (production/EKS)

    Key validation is a storage invariant, not a caller courtesy. run_id, key
    and prefix arrive here as arbitrary strings, so every backend must enforce
    the same logical rules on every operation that builds a path or key out of
    them -- put_file, put_file_exclusive, get_file, exists, list_files,
    put_global, delete_run and delete_global_prefix: run ids match
    RUN_ID_PATTERN, and keys are relative with no "." or ".." component
    (validate_run_id / validate_key / validate_run_key above, which both
    backends call). A backend on a hierarchical namespace must additionally
    guarantee containment after symlink resolution, not just by inspecting the
    string.
    """

    @abstractmethod
    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        """Store a file for a run.

        Args:
            run_id: The run identifier.
            key: Relative path within the run's storage (e.g., "input/cv.docx",
                 "steps/3a/output.json").
            data: File contents as bytes.
        """
        ...

    @abstractmethod
    def put_file_exclusive(self, run_id: str, key: str, data: bytes) -> None:
        """Store a file for a run, but only if the key does not already exist.

        Same contract as put_file, except a pre-existing key is a hard error
        instead of a silent overwrite -- this is what closes the run-id
        collision that let one user's upload overwrite another's. Callers
        that want "create fresh or fail" (allocating a new run id's archive)
        use this instead of exists()-then-put_file, which is a race, not a
        fix.

        Each call is atomic for its own key: the existence check and the
        write are one indivisible operation in every backend (an O_EXCL
        create locally, a conditional PUT on S3), so two concurrent callers
        for the same key can never both succeed. A SEQUENCE of keys is NOT
        transactional, though -- writing input/manifest.json and then
        input/{stored_name} is two independent atomic writes, and a failure
        of the second leaves the namespace partially populated with the
        first. Reconciling (or deliberately tolerating) that residue is the
        caller's contract, not this interface's: see create_run_archive in
        api/upload.py, which documents why the orphaned manifest it can leave
        behind is harmless and is not cleaned up.

        Args:
            run_id: The run identifier.
            key: Relative path within the run's storage (e.g., "input/cv.docx",
                 "steps/3a/output.json").
            data: File contents as bytes.

        Raises:
            StorageKeyExists: If an object already exists at run_id/key.
        """
        ...

    @abstractmethod
    def put_global(self, key: str, data: bytes) -> None:
        """Store a file at a top-level key, outside any run's namespace.

        Unlike put_file (which namespaces under runs/{run_id}/), this writes to
        {prefix}/{key} directly. Used for cross-run index objects that make the
        store browsable along a different axis -- e.g. a by-submitter index at
        "by-submitter/{email}/{run_id}/manifest.json".

        Args:
            key: Full relative key from the storage root (no run_id prefix).
            data: File contents as bytes.
        """
        ...

    @abstractmethod
    def delete_run(self, run_id: str) -> int:
        """Delete every stored artifact for a run (its whole namespace).

        Idempotent: a no-op returning 0 when the run has nothing stored, so a
        reaper can re-run safely. Used to clean up runs that never ran (see the
        orphaned-run reaper) so abandoned uploads don't linger in the store.

        Args:
            run_id: The run identifier.

        Returns:
            Number of objects/files removed.
        """
        ...

    @abstractmethod
    def delete_global_prefix(self, prefix: str) -> int:
        """Delete every object under a top-level key prefix written via
        put_global (e.g. "by-submitter/{email}/{run_id}/").

        Idempotent like delete_run. The prefix must be non-empty and name a
        sub-path of the store -- implementations refuse an empty prefix so a
        bug can't wipe the whole store.

        Args:
            prefix: Full relative key prefix from the storage root (no run_id
                namespace), e.g. "by-submitter/jdoe@example.edu/A1B2C3/".

        Returns:
            Number of objects/files removed.
        """
        ...

    @abstractmethod
    def get_file(self, run_id: str, key: str) -> bytes:
        """Retrieve a file for a run.

        Args:
            run_id: The run identifier.
            key: Relative path within the run's storage.

        Returns:
            File contents as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        ...

    @abstractmethod
    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        """List files for a run, optionally filtered by prefix.

        Args:
            run_id: The run identifier.
            prefix: Optional prefix to filter keys (e.g., "steps/3a/").

        Returns:
            List of keys (relative paths) matching the prefix.
        """
        ...

    @abstractmethod
    def get_download_url(
        self,
        run_id: str,
        key: str,
        expires_in: int = 300,
        download_name: str | None = None,
    ) -> str | None:
        """Generate a download URL for a file.

        For S3, this returns a presigned URL. For local storage, returns None
        (the backend proxies the file directly instead).

        Args:
            run_id: The run identifier.
            key: Relative path within the run's storage.
            expires_in: URL expiry in seconds (default 5 minutes).
            download_name: If set, the URL forces a download with this filename
                (Content-Disposition: attachment). Ignored by backends that
                don't return a URL.

        Returns:
            Presigned URL string, or None if not supported.
        """
        ...

    @abstractmethod
    def exists(self, run_id: str, key: str) -> bool:
        """Check whether a file exists.

        Args:
            run_id: The run identifier.
            key: Relative path within the run's storage.

        Returns:
            True if the file exists, False otherwise.
        """
        ...
