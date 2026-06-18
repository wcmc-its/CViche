"""Local filesystem implementation of RunStorage.

Used in development. Reads/writes to a configurable base directory
(default: web_interface/uploads/).
"""

import os
import shutil
from pathlib import Path

from app.storage.base import RunStorage


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

    def _resolve(self, run_id: str, key: str) -> Path:
        """Resolve a run_id + key to an absolute filesystem path."""
        return self._base / run_id / key

    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        path = self._resolve(run_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def put_global(self, key: str, data: bytes) -> None:
        path = self._base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _delete_tree(self, path: Path) -> int:
        """Recursively delete a directory tree, returning the file count removed.

        Idempotent (returns 0 if absent). Refuses to delete the storage base
        itself, so an empty/degenerate key can't wipe the whole store.
        """
        if path.resolve() == self._base.resolve():
            raise ValueError("refusing to delete the storage base directory")
        if not path.exists():
            return 0
        count = sum(1 for p in path.rglob("*") if p.is_file())
        shutil.rmtree(path)
        return count

    def delete_run(self, run_id: str) -> int:
        return self._delete_tree(self._base / run_id)

    def delete_global_prefix(self, prefix: str) -> int:
        if not prefix or not prefix.strip("/"):
            raise ValueError("delete prefix must be non-empty")
        return self._delete_tree(self._base / prefix)

    def get_file(self, run_id: str, key: str) -> bytes:
        path = self._resolve(run_id, key)
        if not path.exists():
            raise FileNotFoundError(f"No such file: {run_id}/{key}")
        return path.read_bytes()

    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        run_dir = self._base / run_id
        if not run_dir.exists():
            return []

        # Glob for all files under the prefix
        search_dir = run_dir / prefix if prefix else run_dir
        if not search_dir.exists():
            # prefix might be a partial path — glob from parent
            parent = search_dir.parent
            if not parent.exists():
                return []
            pattern = search_dir.name + "*"
            matches = []
            for path in parent.rglob(pattern):
                if path.is_file():
                    # Return key relative to run_dir
                    matches.append(str(path.relative_to(run_dir)))
            return sorted(matches)

        results = []
        for path in search_dir.rglob("*"):
            if path.is_file():
                results.append(str(path.relative_to(run_dir)))
        return sorted(results)

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
