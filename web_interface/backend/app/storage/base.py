"""Abstract base class for run storage backends."""

from abc import ABC, abstractmethod


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
