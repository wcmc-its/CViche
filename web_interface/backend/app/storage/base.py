"""Abstract base class for run storage backends."""

from abc import ABC, abstractmethod


class StorageKeyExists(Exception):
    """Raised by put_file_exclusive when the target key already has an object.

    Signals a run-id collision to the caller so it can regenerate the id and
    retry, rather than silently overwriting another run's archived file
    (#685). Never raised by put_file, which always overwrites.
    """


class RunStorage(ABC):
    """Interface for storing and retrieving run artifacts.

    All file I/O for uploads, pipeline outputs, and prompt logs goes through
    this interface. Two implementations exist:
    - LocalRunStorage: reads/writes to local filesystem (dev)
    - S3RunStorage: reads/writes to S3 (production/EKS)
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
        collision that let one user's upload overwrite another's (#685).
        Callers that want "create fresh or fail" (allocating a new run id's
        archive) use this instead of exists()-then-put_file, which is a
        race, not a fix.

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
