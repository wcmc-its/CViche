"""Storage abstraction layer for CViche run artifacts.

Supports local filesystem (dev) and S3 (production/EKS).
Backend selected by CVICHE_STORAGE_BACKEND environment variable.
"""

from app.storage.factory import get_storage

__all__ = ["get_storage"]
