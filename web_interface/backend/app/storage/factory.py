"""Factory for creating the configured RunStorage backend.

The backend is selected by the CVICHE_STORAGE_BACKEND environment variable:
- "local" (default): LocalRunStorage — filesystem-based, for development.
- "s3": S3RunStorage — for production on EKS.

The storage instance is created once (singleton) and reused for the lifetime
of the process.
"""

import os
import logging

from app.storage.base import RunStorage

logger = logging.getLogger(__name__)

_storage: RunStorage | None = None


def get_storage() -> RunStorage:
    """Return the singleton RunStorage instance, creating it on first call."""
    global _storage
    if _storage is None:
        backend = os.environ.get("CVICHE_STORAGE_BACKEND", "local")
        if backend == "s3":
            from app.storage.s3_storage import S3RunStorage

            _storage = S3RunStorage()
            logger.info("Storage backend: S3 (bucket=%s)", os.environ.get("CVICHE_S3_BUCKET"))
        else:
            from app.storage.local_storage import LocalRunStorage

            _storage = LocalRunStorage()
            logger.info("Storage backend: local filesystem")
    return _storage
