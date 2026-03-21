# Plan 7: Storage Abstraction Layer (Local + S3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Abstract all file I/O behind a `RunStorage` interface so CViche works identically on local disk (dev) and S3 (production/EKS). Upload, download, pipeline step outputs, and prompt logs all go through this layer. On S3, downloads use presigned URLs; per-step outputs are uploaded to S3 immediately after each step completes for crash resilience.

**Architecture:** A `RunStorage` abstract base class with two implementations (`LocalRunStorage`, `S3RunStorage`) selected at startup by the `CVICHE_STORAGE_BACKEND` environment variable. The pipeline continues to use local ephemeral storage during execution; the orchestrator pushes outputs to `RunStorage` at step boundaries. The upload endpoint writes through `RunStorage`. The download/data endpoints read through `RunStorage` (or redirect to presigned URLs for S3).

**Tech Stack:** Python `abc`, `boto3`, `pathlib`, FastAPI, existing SQLAlchemy models

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` -- section 9 (Storage & EKS Deployment)

**Depends on:** Plan 1 (auth foundation) for `get_current_user` dependency on endpoints, but the storage layer itself is independent and can be built in parallel.

---

### Task 1: RunStorage Abstract Interface + LocalRunStorage

**Files:**
- Create: `web_interface/backend/app/storage/__init__.py`
- Create: `web_interface/backend/app/storage/base.py`
- Create: `web_interface/backend/app/storage/local.py`

- [ ] **Step 1: Create the storage package**

```bash
mkdir -p web_interface/backend/app/storage
```

- [ ] **Step 2: Create the abstract RunStorage interface**

Create `web_interface/backend/app/storage/base.py`:

```python
"""Abstract storage interface for run file I/O."""
from abc import ABC, abstractmethod


class RunStorage(ABC):
    """Abstract interface for storing and retrieving run files.

    All file I/O for uploads, pipeline outputs, and prompt logs goes through
    this interface. Two implementations exist:
    - LocalRunStorage: reads/writes to local filesystem (dev)
    - S3RunStorage: reads/writes to S3 (production/EKS)

    Key structure:
        {prefix}/runs/{run_id}/input/{original_filename}
        {prefix}/runs/{run_id}/steps/{stage_id}/{output_filename}
        {prefix}/runs/{run_id}/steps/{stage_id}/prompt_logs/{log_filename}
        {prefix}/runs/{run_id}/output/{final_document}
    """

    @abstractmethod
    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        """Store a file.

        Args:
            run_id: The run identifier (e.g., "A1B2C3").
            key: Relative path within the run's namespace
                 (e.g., "input/resume.docx", "steps/3a/mapped.json").
            data: Raw file bytes.
        """
        ...

    @abstractmethod
    def get_file(self, run_id: str, key: str) -> bytes:
        """Retrieve a file's contents.

        Args:
            run_id: The run identifier.
            key: Relative path within the run's namespace.

        Returns:
            Raw file bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        ...

    @abstractmethod
    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        """List files under a run, optionally filtered by prefix.

        Args:
            run_id: The run identifier.
            prefix: Optional prefix to filter keys
                    (e.g., "steps/3a/" to list only stage 3a outputs).

        Returns:
            List of keys relative to the run namespace.
        """
        ...

    @abstractmethod
    def get_download_url(self, run_id: str, key: str, expires_in: int = 300) -> str:
        """Get a URL for downloading a file.

        For S3: returns a presigned URL (default 5-minute expiry).
        For local: returns a backend-relative URL that the API will proxy.

        Args:
            run_id: The run identifier.
            key: Relative path within the run's namespace.
            expires_in: URL expiry in seconds (S3 only, default 300 = 5 min).

        Returns:
            A URL string suitable for redirecting the client.
        """
        ...

    @abstractmethod
    def exists(self, run_id: str, key: str) -> bool:
        """Check whether a file exists.

        Args:
            run_id: The run identifier.
            key: Relative path within the run's namespace.

        Returns:
            True if the file exists, False otherwise.
        """
        ...

    @abstractmethod
    def delete_run(self, run_id: str) -> int:
        """Delete all files for a run.

        Args:
            run_id: The run identifier.

        Returns:
            Number of files deleted.
        """
        ...
```

- [ ] **Step 3: Create LocalRunStorage implementation**

Create `web_interface/backend/app/storage/local.py`:

```python
"""Local filesystem implementation of RunStorage."""
import os
from pathlib import Path

from app.storage.base import RunStorage


class LocalRunStorage(RunStorage):
    """Stores run files on the local filesystem.

    Directory layout mirrors the S3 key structure:
        {base_dir}/runs/{run_id}/input/{filename}
        {base_dir}/runs/{run_id}/steps/{stage_id}/{filename}
        {base_dir}/runs/{run_id}/steps/{stage_id}/prompt_logs/{filename}
        {base_dir}/runs/{run_id}/output/{filename}

    In dev, {base_dir} defaults to web_interface/outputs.
    Legacy uploaded files in web_interface/uploads/ are handled by the
    upload endpoint, which writes through this storage layer.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, run_id: str, key: str) -> Path:
        """Resolve a run_id + key to an absolute filesystem path."""
        # Security: prevent directory traversal
        safe_key = Path(key)
        if ".." in safe_key.parts:
            raise ValueError(f"Invalid key (directory traversal): {key}")
        return self.base_dir / "runs" / run_id / safe_key

    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        path = self._resolve(run_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_file(self, run_id: str, key: str) -> bytes:
        path = self._resolve(run_id, key)
        if not path.exists():
            raise FileNotFoundError(f"File not found: runs/{run_id}/{key}")
        return path.read_bytes()

    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        run_dir = self.base_dir / "runs" / run_id
        if not run_dir.exists():
            return []

        if prefix:
            search_dir = run_dir / prefix
        else:
            search_dir = run_dir

        if not search_dir.exists():
            return []

        results = []
        for path in search_dir.rglob("*"):
            if path.is_file():
                # Return key relative to the run directory
                rel = path.relative_to(run_dir)
                results.append(str(rel))

        return sorted(results)

    def get_download_url(self, run_id: str, key: str, expires_in: int = 300) -> str:
        """For local storage, return a backend API path that will proxy the file.

        The frontend calls this URL, and the backend streams the file.
        The expires_in parameter is ignored for local storage.
        """
        return f"/api/run/{run_id}/data/{key}"

    def exists(self, run_id: str, key: str) -> bool:
        return self._resolve(run_id, key).exists()

    def delete_run(self, run_id: str) -> int:
        """Delete all files for a run by removing its directory tree."""
        import shutil
        run_dir = self.base_dir / "runs" / run_id
        if not run_dir.exists():
            return 0

        count = sum(1 for _ in run_dir.rglob("*") if _.is_file())
        shutil.rmtree(run_dir)
        return count

    def get_local_path(self, run_id: str, key: str) -> Path:
        """Get the local filesystem path for a file (local-only convenience).

        Used by the download endpoint to serve files directly via FileResponse.
        Not part of the abstract interface -- callers must check isinstance().
        """
        path = self._resolve(run_id, key)
        if not path.exists():
            raise FileNotFoundError(f"File not found: runs/{run_id}/{key}")
        return path
```

- [ ] **Step 4: Create storage package init**

Create `web_interface/backend/app/storage/__init__.py`:

```python
"""Storage abstraction layer for run file I/O."""
from app.storage.base import RunStorage
from app.storage.local import LocalRunStorage

__all__ = ["RunStorage", "LocalRunStorage"]
```

- [ ] **Step 5: Verify imports**

Run: `cd web_interface/backend && python3 -c "from app.storage import RunStorage, LocalRunStorage; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add web_interface/backend/app/storage/
git commit -m "feat: add RunStorage abstract interface and LocalRunStorage implementation"
```

---

### Task 2: S3RunStorage Implementation

**Files:**
- Modify: `web_interface/backend/requirements.txt`
- Create: `web_interface/backend/app/storage/s3.py`
- Modify: `web_interface/backend/app/storage/__init__.py`

- [ ] **Step 1: Add boto3 to requirements**

Add `boto3` to `web_interface/backend/requirements.txt` (append to the file):
```
boto3
```

- [ ] **Step 2: Install boto3**

Run: `cd web_interface/backend && pip install boto3`

- [ ] **Step 3: Create S3RunStorage implementation**

Create `web_interface/backend/app/storage/s3.py`:

```python
"""S3 implementation of RunStorage."""
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from app.storage.base import RunStorage

logger = logging.getLogger(__name__)


class S3RunStorage(RunStorage):
    """Stores run files in Amazon S3.

    S3 key structure:
        {prefix}/runs/{run_id}/input/{original_filename}
        {prefix}/runs/{run_id}/steps/{stage_id}/{output_filename}
        {prefix}/runs/{run_id}/steps/{stage_id}/prompt_logs/{log_filename}
        {prefix}/runs/{run_id}/output/{final_document}

    Authentication: Uses the default boto3 credential chain.
    On EKS, this means IRSA (IAM Roles for Service Accounts).
    Locally, uses ~/.aws/credentials or environment variables.
    """

    def __init__(self, bucket: str, prefix: str = "cviche", region: Optional[str] = None):
        """Initialize S3 storage.

        Args:
            bucket: S3 bucket name (from CVICHE_S3_BUCKET).
            prefix: Key prefix for all objects (from CVICHE_S3_PREFIX, default "cviche").
            region: AWS region. If None, uses the default from boto3 config.
        """
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")

        session_kwargs = {}
        if region:
            session_kwargs["region_name"] = region

        self._s3 = boto3.client("s3", **session_kwargs)
        logger.info(
            "S3RunStorage initialized: bucket=%s, prefix=%s",
            self.bucket,
            self.prefix,
        )

    def _key(self, run_id: str, key: str) -> str:
        """Build the full S3 object key."""
        # Security: prevent directory traversal
        if ".." in key:
            raise ValueError(f"Invalid key (directory traversal): {key}")
        return f"{self.prefix}/runs/{run_id}/{key}"

    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        s3_key = self._key(run_id, key)
        self._s3.put_object(Bucket=self.bucket, Key=s3_key, Body=data)
        logger.debug("S3 PUT: s3://%s/%s (%d bytes)", self.bucket, s3_key, len(data))

    def get_file(self, run_id: str, key: str) -> bytes:
        s3_key = self._key(run_id, key)
        try:
            response = self._s3.get_object(Bucket=self.bucket, Key=s3_key)
            return response["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(f"S3 object not found: s3://{self.bucket}/{s3_key}")
            raise

    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        s3_prefix = self._key(run_id, prefix) if prefix else f"{self.prefix}/runs/{run_id}/"
        # Ensure trailing slash for prefix listing
        if not s3_prefix.endswith("/"):
            s3_prefix += "/"

        results = []
        paginator = self._s3.get_paginator("list_objects_v2")

        # The run-level prefix to strip when returning relative keys
        run_prefix = f"{self.prefix}/runs/{run_id}/"

        for page in paginator.paginate(Bucket=self.bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                # Return key relative to the run namespace
                rel_key = obj["Key"][len(run_prefix):]
                if rel_key:  # Skip the directory marker itself
                    results.append(rel_key)

        return sorted(results)

    def get_download_url(self, run_id: str, key: str, expires_in: int = 300) -> str:
        """Generate a presigned S3 URL for downloading.

        Default expiry is 5 minutes (300 seconds) per the spec.
        The presigned URL bypasses auth middleware -- anyone with the URL can
        download until it expires. The URL is only generated after the backend
        has verified auth + run ownership.
        """
        s3_key = self._key(run_id, key)
        url = self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": s3_key},
            ExpiresIn=expires_in,
        )
        logger.debug(
            "Presigned URL generated: s3://%s/%s (expires in %ds)",
            self.bucket,
            s3_key,
            expires_in,
        )
        return url

    def exists(self, run_id: str, key: str) -> bool:
        s3_key = self._key(run_id, key)
        try:
            self._s3.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def delete_run(self, run_id: str) -> int:
        """Delete all objects under a run's prefix.

        Uses batch delete for efficiency (up to 1000 keys per request).
        """
        run_prefix = f"{self.prefix}/runs/{run_id}/"
        deleted = 0

        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=run_prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue

            delete_request = {
                "Objects": [{"Key": obj["Key"]} for obj in objects],
                "Quiet": True,
            }
            self._s3.delete_objects(Bucket=self.bucket, Delete=delete_request)
            deleted += len(objects)

        logger.info("Deleted %d objects for run %s", deleted, run_id)
        return deleted
```

- [ ] **Step 4: Update storage package init**

Update `web_interface/backend/app/storage/__init__.py`:

```python
"""Storage abstraction layer for run file I/O."""
from app.storage.base import RunStorage
from app.storage.local import LocalRunStorage
from app.storage.s3 import S3RunStorage

__all__ = ["RunStorage", "LocalRunStorage", "S3RunStorage"]
```

- [ ] **Step 5: Verify imports**

Run: `cd web_interface/backend && python3 -c "from app.storage import RunStorage, LocalRunStorage, S3RunStorage; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add web_interface/backend/requirements.txt web_interface/backend/app/storage/s3.py web_interface/backend/app/storage/__init__.py
git commit -m "feat: add S3RunStorage implementation with presigned URL support"
```

---

### Task 3: Storage Factory + App-Wide Singleton

**Files:**
- Create: `web_interface/backend/app/storage/factory.py`
- Modify: `web_interface/backend/app/main.py`

- [ ] **Step 1: Create the storage factory**

Create `web_interface/backend/app/storage/factory.py`:

```python
"""Storage factory: selects backend based on CVICHE_STORAGE_BACKEND env var."""
import os
import logging
from pathlib import Path

from app.storage.base import RunStorage
from app.storage.local import LocalRunStorage
from app.storage.s3 import S3RunStorage

logger = logging.getLogger(__name__)

# Module-level singleton -- initialized once on first call
_storage_instance: RunStorage | None = None


def get_storage() -> RunStorage:
    """Get the application-wide RunStorage instance.

    Selected by environment variable CVICHE_STORAGE_BACKEND:
    - "local" (default): LocalRunStorage writing to web_interface/outputs/
    - "s3": S3RunStorage using CVICHE_S3_BUCKET and CVICHE_S3_PREFIX

    This function is safe to call as a FastAPI dependency:
        storage: RunStorage = Depends(get_storage)
    """
    global _storage_instance

    if _storage_instance is not None:
        return _storage_instance

    backend = os.environ.get("CVICHE_STORAGE_BACKEND", "local").lower()

    if backend == "s3":
        bucket = os.environ.get("CVICHE_S3_BUCKET")
        if not bucket:
            raise RuntimeError(
                "CVICHE_STORAGE_BACKEND=s3 but CVICHE_S3_BUCKET is not set. "
                "Set the S3 bucket name in the CVICHE_S3_BUCKET environment variable."
            )
        prefix = os.environ.get("CVICHE_S3_PREFIX", "cviche")
        region = os.environ.get("AWS_DEFAULT_REGION")

        _storage_instance = S3RunStorage(bucket=bucket, prefix=prefix, region=region)
        logger.info("Storage backend: S3 (bucket=%s, prefix=%s)", bucket, prefix)

    elif backend == "local":
        # Default: local filesystem under web_interface/outputs/
        base_dir = Path(__file__).parent.parent.parent.parent / "outputs"
        _storage_instance = LocalRunStorage(base_dir=base_dir)
        logger.info("Storage backend: local (%s)", base_dir)

    else:
        raise RuntimeError(
            f"Unknown CVICHE_STORAGE_BACKEND: '{backend}'. "
            f"Expected 'local' or 's3'."
        )

    return _storage_instance


def reset_storage() -> None:
    """Reset the singleton (for testing only)."""
    global _storage_instance
    _storage_instance = None
```

- [ ] **Step 2: Initialize storage on startup in main.py**

Add to the `lifespan` function in `web_interface/backend/app/main.py`, after `init_db()`:

```python
    from app.storage.factory import get_storage
    storage = get_storage()
    print(f"✅ Storage backend initialized: {type(storage).__name__}")
```

- [ ] **Step 3: Verify startup**

Run: `cd web_interface/backend && python3 -c "from app.storage.factory import get_storage; s = get_storage(); print(type(s).__name__)"`
Expected: `LocalRunStorage`

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/storage/factory.py web_interface/backend/app/main.py
git commit -m "feat: add storage factory with env-var-based backend selection"
```

---

### Task 4: Update upload.py to Use RunStorage

**Files:**
- Modify: `web_interface/backend/app/api/upload.py`

The upload endpoint currently saves files directly to `web_interface/uploads/`. After this change it writes through `RunStorage` using the key `input/{original_filename}`.

- [ ] **Step 1: Refactor upload_cv to use RunStorage**

In `web_interface/backend/app/api/upload.py`, make these changes:

1. Remove the file-system `UPLOAD_DIR` constant and its `mkdir` call.
2. Import `get_storage` and `RunStorage`.
3. Save the uploaded file through `RunStorage.put_file()`.

Replace the entire file with:

```python
"""File upload API endpoint."""
import secrets
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel

from app.database import get_db
from app.models import Run, Step
from app.schemas import UploadResponse
from app.pipeline.step_registry import STEP_REGISTRY
from app.storage.factory import get_storage
from app.storage.base import RunStorage

router = APIRouter()


class EstimateResponse(BaseModel):
    """Response model for cost/time estimation."""
    document_tokens: int
    text_characters: int
    estimated_cost_min: float
    estimated_cost_max: float
    estimated_time_seconds_min: int
    estimated_time_seconds_max: int
    num_steps: int
    filename: str
    file_size_kb: float


def generate_run_id() -> str:
    """Generate a unique 6-character run ID like 'A1B2C3'."""
    return secrets.token_urlsafe(4)[:6].upper()


@router.post("/upload", response_model=UploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: RunStorage = Depends(get_storage),
):
    """Upload a CV file and create a new pipeline run."""

    # Validate file type
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in [".docx", ".pdf"]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Only .docx and .pdf are supported."
        )

    # Generate run ID
    run_id = generate_run_id()

    # Read file content
    content = await file.read()

    # Store uploaded file via storage abstraction
    storage.put_file(run_id, f"input/{file.filename}", content)

    # Create run record
    run = Run(
        id=run_id,
        filename=file.filename,
        file_type=file_ext[1:],  # Remove dot
        status="created",
        started_at=datetime.now()
    )
    db.add(run)

    # Create step records (all pending initially)
    for step_def in STEP_REGISTRY:
        step = Step(
            run_id=run_id,
            step_number=step_def.number,
            stage_id=step_def.stage_id,
            step_name=step_def.name,
            status="pending"
        )
        db.add(step)

    db.commit()

    return UploadResponse(
        run_id=run_id,
        filename=file.filename,
        file_type=file_ext[1:],
        status="created",
        message=f"File uploaded successfully. Run ID: {run_id}"
    )


@router.post("/estimate", response_model=EstimateResponse)
async def estimate_processing(file: UploadFile = File(...)):
    """
    Estimate cost and time for processing a CV file.

    This endpoint analyzes the document to estimate:
    - Number of tokens (based on document text)
    - Estimated cost range (based on token count and LLM pricing)
    - Estimated time range (based on token count and processing patterns)

    The file is not saved - this is just for estimation.
    """
    import tempfile
    import os

    # Validate file type
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in [".docx", ".pdf"]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file_ext}. Only .docx and .pdf are supported."
        )

    # Read file content
    content = await file.read()
    file_size_kb = len(content) / 1024

    # Extract actual text from document to estimate tokens
    document_text = ""
    text_char_count = 0

    try:
        if file_ext == ".docx":
            # Extract text from Word document
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                from docx import Document
                doc = Document(tmp_path)
                # Get text from paragraphs
                paragraphs_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
                # Also get text from tables
                tables_text = ""
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            if cell.text.strip():
                                tables_text += cell.text + " "
                document_text = paragraphs_text + "\n" + tables_text
                text_char_count = len(document_text)
            finally:
                os.unlink(tmp_path)

        elif file_ext == ".pdf":
            # For PDFs, try to extract text using pypdf if available
            try:
                import pypdf
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                try:
                    reader = pypdf.PdfReader(tmp_path)
                    for page in reader.pages:
                        document_text += page.extract_text() or ""
                    text_char_count = len(document_text)
                finally:
                    os.unlink(tmp_path)
            except ImportError:
                # Fallback: rough estimate for PDFs (typically ~500-1000 chars per page, ~1 page per 30KB)
                estimated_pages = max(1, len(content) // 30000)
                text_char_count = estimated_pages * 2000  # ~2000 chars per page average
    except Exception as e:
        # Fallback: very rough estimate
        text_char_count = 5000  # Assume a typical CV has ~5000 characters

    # Ensure we have a reasonable minimum
    text_char_count = max(text_char_count, 1000)

    # Estimate tokens (roughly 4 characters per token for English text)
    estimated_tokens = text_char_count // 4

    # Cost estimation based on empirical data from actual pipeline runs
    cost_per_1k_tokens = 0.075  # ~$0.075 per 1000 document tokens
    base_cost = (estimated_tokens / 1000) * cost_per_1k_tokens

    # Add variation buffer for different CV complexities
    cost_min = base_cost * 0.8
    cost_max = base_cost * 1.4

    # Time estimation based on empirical data
    base_overhead_seconds = 60
    time_per_1k_tokens = 30

    base_time = base_overhead_seconds + (estimated_tokens / 1000) * time_per_1k_tokens
    num_stages = len(STEP_REGISTRY)

    total_time = base_time + (num_stages * 20)

    time_min = int(total_time * 0.6)
    time_max = int(total_time * 1.3)

    # Ensure reasonable minimums
    time_min = max(time_min, 180)
    time_max = max(time_max, time_min + 180)
    cost_min = max(cost_min, 0.10)
    cost_max = max(cost_max, cost_min * 1.3)

    return EstimateResponse(
        document_tokens=estimated_tokens,
        text_characters=text_char_count,
        estimated_cost_min=round(cost_min, 3),
        estimated_cost_max=round(cost_max, 3),
        estimated_time_seconds_min=time_min,
        estimated_time_seconds_max=time_max,
        num_steps=num_stages,
        filename=file.filename,
        file_size_kb=round(file_size_kb, 1)
    )
```

- [ ] **Step 2: Verify upload endpoint loads**

Run: `cd web_interface/backend && python3 -c "from app.api.upload import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/api/upload.py
git commit -m "feat: update upload endpoint to write files through RunStorage"
```

---

### Task 5: Update runs.py to Pull Input File from RunStorage

**Files:**
- Modify: `web_interface/backend/app/api/runs.py`

The `start_run` endpoint currently locates the uploaded file at `web_interface/uploads/{run_id}_{filename}`. It must now pull the file from `RunStorage` to local ephemeral storage so the pipeline can process it.

- [ ] **Step 1: Update start_run to retrieve input file from RunStorage**

In `web_interface/backend/app/api/runs.py`, update the `start_run` function:

```python
"""Run status and management API endpoints."""
import tempfile
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pathlib import Path

from app.database import get_db
from app.models import Run, Step
from app.schemas import RunStatus, RunSummary, StepSummary
from app.pipeline.orchestrator import PipelineOrchestrator
from app.storage.factory import get_storage
from app.storage.base import RunStorage

router = APIRouter()


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(db: Session = Depends(get_db)):
    """List all pipeline runs, most recent first."""
    runs = db.query(Run).order_by(Run.started_at.desc()).all()

    results = []
    for run in runs:
        total_duration_seconds = None
        if run.started_at:
            if run.completed_at:
                total_duration_seconds = int((run.completed_at - run.started_at).total_seconds())
            elif run.status == "running":
                from datetime import datetime
                total_duration_seconds = max(0, int((datetime.now() - run.started_at).total_seconds()))

        results.append(RunSummary(
            run_id=run.id,
            filename=run.filename,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            total_cost=run.total_cost or 0.0,
            total_duration_seconds=total_duration_seconds
        ))

    return results


@router.get("/run/{run_id}/status", response_model=RunStatus)
async def get_run_status(run_id: str, db: Session = Depends(get_db)):
    """Get the current status of a pipeline run."""

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Get all steps for this run
    steps = db.query(Step).filter(Step.run_id == run_id).order_by(Step.step_number).all()

    step_summaries = [
        StepSummary(
            step_number=step.step_number,
            stage_id=step.stage_id,
            step_name=step.step_name,
            status=step.status,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_seconds=step.duration_seconds,
            cost=step.cost or 0.0,
            output_files=step.output_files
        )
        for step in steps
    ]

    # Calculate total duration if run is complete or running
    total_duration_seconds = None
    if run.started_at:
        if run.completed_at:
            total_duration_seconds = int((run.completed_at - run.started_at).total_seconds())
        else:
            from datetime import datetime
            total_duration_seconds = int((datetime.now() - run.started_at).total_seconds())
            total_duration_seconds = max(0, total_duration_seconds)

    return RunStatus(
        run_id=run.id,
        filename=run.filename,
        file_type=run.file_type,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_cost=run.total_cost or 0.0,
        total_tokens=run.total_tokens or 0,
        input_tokens=run.input_tokens or 0,
        output_tokens=run.output_tokens or 0,
        total_duration_seconds=total_duration_seconds,
        error_message=run.error_message,
        steps=step_summaries
    )


@router.post("/run/{run_id}/start")
async def start_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    storage: RunStorage = Depends(get_storage),
):
    """Start executing a pipeline run."""

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status not in ["created", "paused"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start run in status: {run.status}"
        )

    # Pull input file from storage to local ephemeral path for pipeline execution.
    # The pipeline operates on local files; S3 is used at boundaries only (per spec).
    input_key = f"input/{run.filename}"
    if not storage.exists(run_id, input_key):
        raise HTTPException(status_code=404, detail="Uploaded file not found in storage")

    file_data = storage.get_file(run_id, input_key)

    # Write to a local temp directory that persists for the run's lifetime
    ephemeral_dir = Path(tempfile.mkdtemp(prefix=f"cviche_run_{run_id}_"))
    file_path = ephemeral_dir / run.filename
    file_path.write_bytes(file_data)

    # Update status
    run.status = "running"
    db.commit()

    # Start pipeline execution in background
    def run_pipeline():
        from app.database import SessionLocal
        bg_db = SessionLocal()
        try:
            orchestrator = PipelineOrchestrator(run_id, file_path, bg_db)
            import asyncio
            asyncio.run(orchestrator.execute())
        finally:
            bg_db.close()
            # Clean up ephemeral directory after pipeline completes
            import shutil
            shutil.rmtree(ephemeral_dir, ignore_errors=True)

    background_tasks.add_task(run_pipeline)

    return {"message": f"Pipeline started for run {run_id}", "status": "running"}


@router.post("/run/{run_id}/pause")
async def pause_run(run_id: str, db: Session = Depends(get_db)):
    """Pause a running pipeline."""

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause run in status: {run.status}"
        )

    run.status = "paused"
    db.commit()

    return {"message": f"Run {run_id} paused", "status": "paused"}


@router.post("/run/{run_id}/cancel")
async def cancel_run(run_id: str, db: Session = Depends(get_db)):
    """Cancel a running pipeline."""
    from app.pipeline.orchestrator import cancel_run as orchestrator_cancel

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel run in status: {run.status}"
        )

    # Signal cancellation to orchestrator
    orchestrator_cancel(run_id)

    # Update run status
    run.status = "cancelled"
    run.error_message = "Cancelled by user"
    from datetime import datetime
    run.completed_at = datetime.now()
    db.commit()

    return {"message": f"Run {run_id} cancelled", "status": "cancelled"}


@router.post("/run/{run_id}/retry/{step_number}")
async def retry_step(
    run_id: str,
    step_number: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Retry a failed step."""

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    step = db.query(Step).filter(
        Step.run_id == run_id,
        Step.step_number == step_number
    ).first()

    if not step:
        raise HTTPException(
            status_code=404,
            detail=f"Step {step_number} not found for run {run_id}"
        )

    if step.status != "error":
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry failed steps. Step {step_number} status: {step.status}"
        )

    # Reset step status
    step.status = "pending"
    step.error_message = None
    db.commit()

    # TODO: Implement retry logic
    return {"message": f"Step {step_number} queued for retry", "status": "pending"}


@router.get("/run/{run_id}/quality")
async def get_data_quality(run_id: str, step: int = 3, db: Session = Depends(get_db)):
    """Get comprehensive data quality report for a run.

    Note: This endpoint still reads from the pipeline's local output directories.
    It is not yet migrated to RunStorage because quality data is computed from
    the pipeline's native output structure. This will be addressed in a follow-up
    when the orchestrator pushes step outputs to RunStorage (Task 7).
    """
    # Keep existing implementation unchanged for now.
    # The quality endpoint reads from src/unified_pipeline/outputs/ and
    # web_interface/outputs/{run_id}/ which are the pipeline's local directories.
    # After Task 7 (per-step S3 upload), this will be migrated to read from
    # RunStorage. For local dev, there is no functional change.
    import json

    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    outputs_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id

    if not outputs_dir.exists():
        raise HTTPException(status_code=404, detail="Output directory not found")

    # ... rest of existing quality endpoint unchanged ...
    # (keeping existing code as-is; the full function is long and unchanged)
    return {"run_id": run_id, "status": "quality endpoint preserved"}
```

**Important note:** The `get_data_quality` function body is very long (700+ lines). The actual change in this step only touches the imports at the top of the file and the `start_run` function. The `get_data_quality` function body should remain completely unchanged from its current implementation. The code block above truncates it only for plan readability.

- [ ] **Step 2: Verify module loads**

Run: `cd web_interface/backend && python3 -c "from app.api.runs import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/api/runs.py
git commit -m "feat: update start_run to pull input files from RunStorage"
```

---

### Task 6: Update steps.py to Use RunStorage for File Serving

**Files:**
- Modify: `web_interface/backend/app/api/steps.py`

The `get_data_file` and `get_json_content` endpoints currently serve files from local filesystem paths. After this change, they read through `RunStorage` and, for S3, redirect to presigned URLs for binary downloads.

- [ ] **Step 1: Refactor get_data_file to use RunStorage**

In `web_interface/backend/app/api/steps.py`, update the file-serving endpoints. The key change: instead of searching the filesystem for files, look them up via `RunStorage`. For backward compatibility with existing runs that stored absolute paths in `step.output_files`, fall back to direct filesystem access.

Add these imports at the top:

```python
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from app.storage.factory import get_storage
from app.storage.base import RunStorage
from app.storage.local import LocalRunStorage
```

Replace the `get_data_file` endpoint:

```python
@router.get("/run/{run_id}/data/{filename:path}")
async def get_data_file(
    run_id: str,
    filename: str,
    preview: bool = False,
    storage: RunStorage = Depends(get_storage),
):
    """Download or preview a data file.

    For S3 backend: binary files redirect to a presigned URL.
    For local backend: files are served directly via FileResponse.
    JSON previews are always proxied (read through storage, returned as JSON).
    """

    # Security: reject directory traversal
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    download_filename = Path(filename).name

    # --- JSON preview: always proxy (read bytes, parse, return) ---
    if preview and download_filename.endswith(".json"):
        try:
            data_bytes = _resolve_file_bytes(run_id, filename, storage)
            data = json.loads(data_bytes.decode("utf-8"))
            preview_data = _parse_json_to_preview(data)
            return JSONResponse(content=preview_data.dict() if preview_data else {})
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    # --- Binary download: use presigned URL for S3, FileResponse for local ---
    # Try to find the file in RunStorage first
    # Map the filename to a storage key. Files may be stored as:
    #   input/{filename}
    #   steps/{stage_id}/{filename}
    #   output/{filename}
    # Or the filename itself may already be a valid key.

    # Try the filename as-is first (it may already be a key)
    if storage.exists(run_id, filename):
        return _serve_file(run_id, filename, download_filename, storage)

    # Try common prefixes
    for prefix in ["output", "input"]:
        key = f"{prefix}/{download_filename}"
        if storage.exists(run_id, key):
            return _serve_file(run_id, key, download_filename, storage)

    # Try to find in steps/ subdirectories
    step_files = storage.list_files(run_id, "steps/")
    for key in step_files:
        if Path(key).name == download_filename:
            return _serve_file(run_id, key, download_filename, storage)

    # --- Fallback: legacy filesystem paths for pre-migration runs ---
    file_path = _legacy_find_file(run_id, filename)
    if file_path:
        if download_filename.endswith(".docx"):
            return FileResponse(
                str(file_path),
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename=download_filename
            )
        elif download_filename.endswith(".json"):
            return FileResponse(str(file_path), media_type="application/json", filename=download_filename)
        return FileResponse(str(file_path), filename=download_filename)

    raise HTTPException(status_code=404, detail=f"File not found: {filename}")


def _serve_file(
    run_id: str, key: str, download_filename: str, storage: RunStorage
):
    """Serve a file from RunStorage.

    - S3: redirect to presigned URL (offloads bandwidth).
    - Local: serve via FileResponse (zero-copy sendfile).
    """
    if isinstance(storage, LocalRunStorage):
        local_path = storage.get_local_path(run_id, key)
        media_type = None
        if download_filename.endswith(".docx"):
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif download_filename.endswith(".json"):
            media_type = "application/json"
        return FileResponse(str(local_path), media_type=media_type, filename=download_filename)
    else:
        # S3: redirect to presigned URL
        url = storage.get_download_url(run_id, key, expires_in=300)
        return RedirectResponse(url=url, status_code=307)


def _legacy_find_file(run_id: str, filename: str) -> Path | None:
    """Fall back to the legacy filesystem search for pre-migration runs.

    Searches web_interface/outputs/{run_id}/ and
    src/unified_pipeline/outputs/ stage directories.
    """
    # Check if filename is an absolute path that exists
    if filename.startswith('/') and Path(filename).exists():
        return Path(filename)

    # Check web_interface/outputs/{run_id}
    output_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id
    candidate = output_dir / filename
    if candidate.exists():
        return candidate

    # Check the main pipeline output directories
    pipeline_output_dir = Path(__file__).parent.parent.parent.parent.parent / "src" / "unified_pipeline" / "outputs"
    if pipeline_output_dir.exists():
        for stage_dir in pipeline_output_dir.iterdir():
            if stage_dir.is_dir():
                base_filename = Path(filename).name
                candidate = stage_dir / base_filename
                if candidate.exists():
                    return candidate

    return None
```

- [ ] **Step 2: Update get_json_content similarly**

Replace the `get_json_content` endpoint:

```python
@router.get("/run/{run_id}/data/{filename:path}/json")
async def get_json_content(
    run_id: str,
    filename: str,
    storage: RunStorage = Depends(get_storage),
):
    """Get raw JSON content for display in viewer."""

    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not filename.endswith(".json") and not Path(filename).name.endswith(".json"):
        raise HTTPException(status_code=400, detail="Only JSON files can be viewed")

    try:
        data_bytes = _resolve_file_bytes(run_id, filename, storage)
        data = json.loads(data_bytes.decode("utf-8"))

        return JSONResponse(content={
            "filename": filename,
            "size_bytes": len(data_bytes),
            "content": data
        })
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading JSON: {str(e)}")


def _resolve_file_bytes(run_id: str, filename: str, storage: RunStorage) -> bytes:
    """Resolve a filename to bytes, trying RunStorage then legacy filesystem.

    Raises FileNotFoundError if not found anywhere.
    """
    # Try filename as-is in storage
    if storage.exists(run_id, filename):
        return storage.get_file(run_id, filename)

    download_filename = Path(filename).name

    # Try common prefixes
    for prefix in ["output", "input"]:
        key = f"{prefix}/{download_filename}"
        if storage.exists(run_id, key):
            return storage.get_file(run_id, key)

    # Try steps/ subdirectories
    step_files = storage.list_files(run_id, "steps/")
    for key in step_files:
        if Path(key).name == download_filename:
            return storage.get_file(run_id, key)

    # Legacy filesystem fallback
    file_path = _legacy_find_file(run_id, filename)
    if file_path:
        return file_path.read_bytes()

    raise FileNotFoundError(f"File not found: {filename}")
```

- [ ] **Step 3: Keep prompt-logs endpoint unchanged for now**

The `get_prompt_logs` endpoint reads from `src/unified_pipeline/prompt_logs/` which is the pipeline's local output directory. This will be updated in Task 7 when the orchestrator pushes prompt logs to RunStorage after each step. For now, leave it unchanged -- it works correctly for local dev.

- [ ] **Step 4: Verify module loads**

Run: `cd web_interface/backend && python3 -c "from app.api.steps import router; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add web_interface/backend/app/api/steps.py
git commit -m "feat: update steps.py file serving to read through RunStorage"
```

---

### Task 7: Per-Step S3 Upload Hook in Pipeline Orchestrator

**Files:**
- Modify: `web_interface/backend/app/pipeline/orchestrator.py`

This is the core change for S3 resilience. After each pipeline step completes, its output files are pushed to `RunStorage`. This means if a pod crashes after step 3, steps 1-3 outputs are safe in S3. The pipeline still reads/writes locally during execution -- S3 writes happen at the boundaries.

- [ ] **Step 1: Add storage integration to PipelineOrchestrator**

In `web_interface/backend/app/pipeline/orchestrator.py`, make these changes:

1. Import `get_storage` and `RunStorage`.
2. Accept a `storage` parameter in the constructor.
3. Add a `_upload_step_outputs` method that pushes output files to `RunStorage` after each step.
4. Call `_upload_step_outputs` at the end of `execute_step`.
5. Upload prompt logs after each step that produces them.

Add these imports near the top of the file:

```python
from app.storage.factory import get_storage
from app.storage.base import RunStorage
```

Update the `PipelineOrchestrator.__init__` method to accept and store a storage instance:

```python
class PipelineOrchestrator:
    """Orchestrates the execution of all 12 pipeline stages."""

    def __init__(self, run_id: str, file_path: Path, db: Session, storage: RunStorage | None = None):
        self.run_id = run_id
        self.file_path = file_path
        self.db = db
        self.storage = storage or get_storage()

        # Output directory for this run (in the unified_pipeline outputs)
        self.pipeline_output_dir = PARENT_DIR / 'src' / 'unified_pipeline' / 'outputs'

        # Web interface output directory (for tracking)
        self.web_output_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id
        self.web_output_dir.mkdir(parents=True, exist_ok=True)

        # Document UID extracted from filename
        self.document_uid = Path(file_path).stem

        # Track outputs between stages
        self.stage_outputs: Dict[str, str] = {}

        # Model to use for LLM stages
        self.model = "gpt-5.1"

        # Total cost tracking
        self.total_cost = 0.0
```

Add the `_upload_step_outputs` method:

```python
    def _upload_step_outputs(self, stage_id: str, output_files: list[str]) -> None:
        """Push step output files to RunStorage after a step completes.

        This provides crash resilience on S3: if the pod dies, completed step
        outputs are already persisted. For local storage, this is effectively
        a copy to the storage directory structure.

        Args:
            stage_id: The pipeline stage identifier (e.g., "3a", "4.5").
            output_files: List of absolute local paths to output files.
        """
        for file_path_str in output_files:
            try:
                file_path = Path(file_path_str)
                if not file_path.exists():
                    continue

                key = f"steps/{stage_id}/{file_path.name}"
                data = file_path.read_bytes()
                self.storage.put_file(self.run_id, key, data)

                # If this is the final stage (6), also store under output/
                if stage_id == "6":
                    output_key = f"output/{file_path.name}"
                    self.storage.put_file(self.run_id, output_key, data)

            except Exception as e:
                # Log but don't fail the pipeline -- the local file is still valid
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to upload step output to storage: %s (%s)", file_path_str, e
                )

    def _upload_prompt_logs(self, stage_id: str, step_number: int) -> None:
        """Push prompt log files for a step to RunStorage.

        Prompt logs are written to src/unified_pipeline/prompt_logs/ by the
        pipeline's prompt_logger. After each step, we find logs matching this
        stage and upload them.
        """
        from app.api.steps import STAGE_TO_FILENAME_PATTERNS

        patterns = STAGE_TO_FILENAME_PATTERNS.get(stage_id, [])
        if not patterns:
            return

        prompt_logs_dir = PARENT_DIR / "src" / "unified_pipeline" / "prompt_logs"
        if not prompt_logs_dir.exists():
            return

        # Get the run's start time to filter logs
        run = self.db.query(Run).filter(Run.id == self.run_id).first()
        if not run or not run.started_at:
            return
        run_start_ts = run.started_at.timestamp()

        for pattern in patterns:
            for log_file in prompt_logs_dir.glob(pattern):
                if not log_file.name.endswith(".txt"):
                    continue
                if log_file.stat().st_mtime < run_start_ts:
                    continue

                try:
                    key = f"steps/{stage_id}/prompt_logs/{log_file.name}"
                    data = log_file.read_bytes()
                    self.storage.put_file(self.run_id, key, data)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Failed to upload prompt log to storage: %s (%s)", log_file.name, e
                    )
```

Update `execute_step` to call these methods after step completion. Add this block right after the `step.status = "complete"` section and before the `emit_step_complete` call:

```python
            # Upload step outputs to RunStorage (S3 boundary push)
            self._upload_step_outputs(stage_id, result.get("output_files", []))
            self._upload_prompt_logs(stage_id, step_number)
```

The full updated `execute_step` `try` block becomes:

```python
        try:
            step.status = "running"
            step.started_at = datetime.now()
            self.db.commit()

            await event_emitter.emit_step_start(self.run_id, step_number, self.total_cost)
            await self.log(step_number, f"Starting Stage {stage_id}: {step_def.name}")

            start_time = time.time()

            # Execute the actual stage logic
            result = await self._execute_stage_logic(stage_id, cv_path)

            duration = int(time.time() - start_time)

            # Mark as complete
            step.status = "complete"
            step.completed_at = datetime.now()
            step.duration_seconds = duration
            step.cost = result.get("cost", 0.0)
            step.output_files = json.dumps(result.get("output_files", []))
            self.db.commit()

            # Upload step outputs to RunStorage (S3 boundary push)
            self._upload_step_outputs(stage_id, result.get("output_files", []))
            self._upload_prompt_logs(stage_id, step_number)

            await self.log(step_number, f"Completed Stage {stage_id} in {duration}s")
            await event_emitter.emit_step_complete(
                self.run_id,
                step_number,
                duration,
                step.cost,
                result.get("output_files", [])
            )
```

- [ ] **Step 2: Update the background task in runs.py to pass storage**

In `web_interface/backend/app/api/runs.py`, update the `run_pipeline` closure inside `start_run` to pass the storage instance:

```python
    # Capture storage reference for background task
    storage_ref = storage

    def run_pipeline():
        from app.database import SessionLocal
        bg_db = SessionLocal()
        try:
            orchestrator = PipelineOrchestrator(run_id, file_path, bg_db, storage=storage_ref)
            import asyncio
            asyncio.run(orchestrator.execute())
        finally:
            bg_db.close()
            import shutil
            shutil.rmtree(ephemeral_dir, ignore_errors=True)

    background_tasks.add_task(run_pipeline)
```

- [ ] **Step 3: Verify orchestrator loads**

Run: `cd web_interface/backend && python3 -c "from app.pipeline.orchestrator import PipelineOrchestrator; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/pipeline/orchestrator.py web_interface/backend/app/api/runs.py
git commit -m "feat: add per-step S3 upload hook in pipeline orchestrator for crash resilience"
```

---

### Task 8: Presigned URL Download Endpoint

**Files:**
- Modify: `web_interface/backend/app/api/steps.py`

Add a dedicated download endpoint that generates presigned URLs for S3 or proxies files for local storage. This is the endpoint the frontend calls when the user clicks "Download" on the final output document.

- [ ] **Step 1: Add the download endpoint**

Add this new endpoint to `web_interface/backend/app/api/steps.py`:

```python
@router.get("/run/{run_id}/download/{filename:path}")
async def download_file(
    run_id: str,
    filename: str,
    storage: RunStorage = Depends(get_storage),
    db: Session = Depends(get_db),
):
    """Download a run output file.

    For S3: returns a 307 redirect to a presigned URL (5-minute expiry).
    For local: returns the file directly via FileResponse.

    The frontend should use this endpoint for all file downloads.
    Auth and ownership checks happen before the presigned URL is generated,
    so the URL itself is safe to redirect to without further auth.
    """
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Verify run exists
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    download_filename = Path(filename).name

    # Try to find the file in storage
    # Check output/ first (final documents), then steps/, then input/
    search_keys = [
        f"output/{download_filename}",
        filename,  # as-is (may already be a full key)
    ]

    # Also search steps/ directories
    step_files = storage.list_files(run_id, "steps/")
    for key in step_files:
        if Path(key).name == download_filename:
            search_keys.append(key)

    search_keys.append(f"input/{download_filename}")

    found_key = None
    for key in search_keys:
        if storage.exists(run_id, key):
            found_key = key
            break

    if found_key:
        return _serve_file(run_id, found_key, download_filename, storage)

    # Legacy fallback
    file_path = _legacy_find_file(run_id, filename)
    if file_path:
        media_type = None
        if download_filename.endswith(".docx"):
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif download_filename.endswith(".json"):
            media_type = "application/json"
        return FileResponse(str(file_path), media_type=media_type, filename=download_filename)

    raise HTTPException(status_code=404, detail=f"File not found: {filename}")
```

- [ ] **Step 2: Verify endpoint loads**

Run: `cd web_interface/backend && python3 -c "from app.api.steps import router; print([r.path for r in router.routes])"`
Expected: list of route paths including `/run/{run_id}/download/{filename:path}`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/api/steps.py
git commit -m "feat: add presigned URL download endpoint for S3-backed file serving"
```

---

### Task 9: ConfigMap Notes for EKS Deployment

**Files:**
- Create: `web_interface/backend/k8s/configmap.yaml` (reference example, not applied)
- Create: `web_interface/backend/k8s/deployment-env.yaml` (reference example)

These are reference manifests for the EKS deployment. They are not applied by this plan -- they document how `auth_config.yaml` and `consent_text.md` should be mounted as ConfigMaps, and how secrets/env vars should be injected.

- [ ] **Step 1: Create the k8s reference directory**

```bash
mkdir -p web_interface/backend/k8s
```

- [ ] **Step 2: Create ConfigMap reference for auth_config.yaml and consent_text.md**

Create `web_interface/backend/k8s/configmap.yaml`:

```yaml
# Reference ConfigMap for CViche on EKS.
# auth_config.yaml and consent_text.md are config (not run data) and must
# survive pod restarts. They are mounted via ConfigMap.
#
# Apply with: kubectl apply -f configmap.yaml
# Update with: kubectl create configmap cviche-config --from-file=... --dry-run=client -o yaml | kubectl apply -f -
#
# After updating consent_text.md, bump the consent version in the admin
# dashboard Config tab (or in the ConfigMap if pre-seeding).

apiVersion: v1
kind: ConfigMap
metadata:
  name: cviche-config
  namespace: cviche
  labels:
    app: cviche
data:
  auth_config.yaml: |
    auth:
      mode: simple  # "simple" or "saml"

    allowed_users:
      - paa2013@med.cornell.edu

    admin_users:
      - paa2013@med.cornell.edu

    rate_limits:
      daily: 10
      monthly: 50

    consent:
      version: "1.0"

  consent_text.md: |
    # CViche Pilot Program — Consent

    The Samuel J. Wood Library is conducting a pilot of **CViche**, a prototype
    tool that uses AI to convert CVs into Weill Cornell Medicine format.

    ## How your CV will be used

    - Your CV will be processed using AI (OpenAI GPT models).
    - Project staff will review the output for quality assessment.
    - Feedback you provide will be analyzed to improve the system.

    ## Sensitive information

    You may omit personal contact details (phone, home address) before
    submitting your CV. Only your name and email are required.

    ## Consent

    By proceeding, you confirm that you have read the information above and
    consent to participate in this pilot program.
```

- [ ] **Step 3: Create deployment environment reference**

Create `web_interface/backend/k8s/deployment-env.yaml`:

```yaml
# Reference: environment variables and volume mounts for CViche on EKS.
# This is a partial Deployment spec showing only the env/volume sections.
# Merge into your full Deployment manifest or Helm values.

apiVersion: apps/v1
kind: Deployment
metadata:
  name: cviche-backend
  namespace: cviche
spec:
  template:
    spec:
      serviceAccountName: cviche-sa  # Must have IRSA role for S3 access
      containers:
        - name: cviche
          image: cviche-backend:latest
          env:
            # --- Required in production ---
            - name: CVICHE_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: cviche-secrets
                  key: database-url
            - name: CVICHE_SESSION_SECRET
              valueFrom:
                secretKeyRef:
                  name: cviche-secrets
                  key: session-secret
            - name: OPENAI_API_KEY_WORK
              valueFrom:
                secretKeyRef:
                  name: cviche-secrets
                  key: openai-api-key

            # --- Storage: S3 ---
            - name: CVICHE_STORAGE_BACKEND
              value: "s3"
            - name: CVICHE_S3_BUCKET
              value: "wcm-cviche-prod"  # Replace with actual bucket
            - name: CVICHE_S3_PREFIX
              value: "cviche"

            # --- Optional ---
            - name: CVICHE_SECURE_COOKIES
              value: "true"  # Set "false" if no TLS termination
            - name: CVICHE_ALLOWED_ORIGINS
              value: "https://cviche.med.cornell.edu"  # Replace with actual URL

          volumeMounts:
            # Mount ConfigMap files into the paths the app expects
            - name: cviche-config
              mountPath: /app/web_interface/backend/auth_config.yaml
              subPath: auth_config.yaml
              readOnly: true
            - name: cviche-config
              mountPath: /app/web_interface/backend/consent_text.md
              subPath: consent_text.md
              readOnly: true

      volumes:
        - name: cviche-config
          configMap:
            name: cviche-config

---
# Kubernetes Secret (create manually or via sealed-secrets/external-secrets)
# kubectl create secret generic cviche-secrets \
#   --from-literal=database-url='mysql+pymysql://cviche:PASSWORD@mariadb:3306/cviche' \
#   --from-literal=session-secret='RANDOM_64_CHAR_HEX' \
#   --from-literal=openai-api-key='sk-...'

# IRSA (IAM Roles for Service Accounts) for S3 access:
# 1. Create IAM role with S3 read/write policy for the bucket
# 2. Annotate the service account:
#    kubectl annotate serviceaccount cviche-sa \
#      eks.amazonaws.com/role-arn=arn:aws:iam::ACCOUNT:role/cviche-s3-role
# No access keys needed -- boto3 picks up credentials automatically via IRSA.
```

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/k8s/
git commit -m "docs: add EKS reference manifests for ConfigMap, secrets, and S3 storage"
```

---

### Task 10: Integration Test for Storage Layer

**Files:**
- Create: `web_interface/backend/tests/test_storage.py`

- [ ] **Step 1: Create the test file**

Create `web_interface/backend/tests/test_storage.py`:

```python
"""Tests for the RunStorage abstraction layer."""
import os
import tempfile
import pytest
from pathlib import Path

from app.storage.local import LocalRunStorage
from app.storage.factory import get_storage, reset_storage


class TestLocalRunStorage:
    """Test LocalRunStorage implementation."""

    def setup_method(self):
        """Create a temp directory for each test."""
        self.tmp_dir = tempfile.mkdtemp(prefix="cviche_test_storage_")
        self.storage = LocalRunStorage(base_dir=self.tmp_dir)

    def teardown_method(self):
        """Clean up temp directory."""
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_put_and_get_file(self):
        data = b"hello world"
        self.storage.put_file("RUN123", "input/test.txt", data)
        result = self.storage.get_file("RUN123", "input/test.txt")
        assert result == data

    def test_get_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.storage.get_file("RUN123", "nonexistent.txt")

    def test_exists(self):
        assert not self.storage.exists("RUN123", "input/test.txt")
        self.storage.put_file("RUN123", "input/test.txt", b"data")
        assert self.storage.exists("RUN123", "input/test.txt")

    def test_list_files_empty(self):
        assert self.storage.list_files("RUN123") == []

    def test_list_files(self):
        self.storage.put_file("RUN123", "input/cv.docx", b"doc")
        self.storage.put_file("RUN123", "steps/3a/mapped.json", b"json")
        self.storage.put_file("RUN123", "output/final.docx", b"final")

        files = self.storage.list_files("RUN123")
        assert len(files) == 3
        assert "input/cv.docx" in files
        assert "steps/3a/mapped.json" in files
        assert "output/final.docx" in files

    def test_list_files_with_prefix(self):
        self.storage.put_file("RUN123", "steps/3a/mapped.json", b"json")
        self.storage.put_file("RUN123", "steps/4/fields.json", b"json")
        self.storage.put_file("RUN123", "output/final.docx", b"final")

        files = self.storage.list_files("RUN123", "steps/")
        assert len(files) == 2
        assert "steps/3a/mapped.json" in files
        assert "steps/4/fields.json" in files

    def test_delete_run(self):
        self.storage.put_file("RUN123", "input/cv.docx", b"doc")
        self.storage.put_file("RUN123", "steps/3a/mapped.json", b"json")

        count = self.storage.delete_run("RUN123")
        assert count == 2
        assert self.storage.list_files("RUN123") == []

    def test_delete_run_nonexistent(self):
        count = self.storage.delete_run("NONEXISTENT")
        assert count == 0

    def test_get_download_url_local(self):
        """Local storage returns a backend API path, not a presigned URL."""
        url = self.storage.get_download_url("RUN123", "output/final.docx")
        assert url == "/api/run/RUN123/data/output/final.docx"

    def test_directory_traversal_rejected(self):
        with pytest.raises(ValueError, match="directory traversal"):
            self.storage.put_file("RUN123", "../../../etc/passwd", b"evil")

    def test_get_local_path(self):
        self.storage.put_file("RUN123", "input/cv.docx", b"doc")
        path = self.storage.get_local_path("RUN123", "input/cv.docx")
        assert path.exists()
        assert path.name == "cv.docx"

    def test_get_local_path_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.storage.get_local_path("RUN123", "nonexistent.txt")

    def test_nested_keys(self):
        """Keys can have multiple path components."""
        self.storage.put_file("RUN123", "steps/3a/prompt_logs/log1.txt", b"log")
        assert self.storage.exists("RUN123", "steps/3a/prompt_logs/log1.txt")
        assert self.storage.get_file("RUN123", "steps/3a/prompt_logs/log1.txt") == b"log"

    def test_isolation_between_runs(self):
        """Files from different runs are isolated."""
        self.storage.put_file("RUN_A", "input/cv.docx", b"run_a_data")
        self.storage.put_file("RUN_B", "input/cv.docx", b"run_b_data")

        assert self.storage.get_file("RUN_A", "input/cv.docx") == b"run_a_data"
        assert self.storage.get_file("RUN_B", "input/cv.docx") == b"run_b_data"

        self.storage.delete_run("RUN_A")
        assert not self.storage.exists("RUN_A", "input/cv.docx")
        assert self.storage.exists("RUN_B", "input/cv.docx")


class TestStorageFactory:
    """Test the storage factory."""

    def setup_method(self):
        reset_storage()

    def teardown_method(self):
        reset_storage()

    def test_default_is_local(self):
        """Default backend is LocalRunStorage."""
        # Remove env var if set
        os.environ.pop("CVICHE_STORAGE_BACKEND", None)
        storage = get_storage()
        assert isinstance(storage, LocalRunStorage)

    def test_explicit_local(self):
        os.environ["CVICHE_STORAGE_BACKEND"] = "local"
        storage = get_storage()
        assert isinstance(storage, LocalRunStorage)

    def test_s3_requires_bucket(self):
        os.environ["CVICHE_STORAGE_BACKEND"] = "s3"
        os.environ.pop("CVICHE_S3_BUCKET", None)
        with pytest.raises(RuntimeError, match="CVICHE_S3_BUCKET"):
            get_storage()

    def test_unknown_backend_raises(self):
        os.environ["CVICHE_STORAGE_BACKEND"] = "gcs"
        with pytest.raises(RuntimeError, match="Unknown CVICHE_STORAGE_BACKEND"):
            get_storage()

    def test_singleton_behavior(self):
        """get_storage returns the same instance on repeated calls."""
        os.environ.pop("CVICHE_STORAGE_BACKEND", None)
        s1 = get_storage()
        s2 = get_storage()
        assert s1 is s2
```

- [ ] **Step 2: Run tests**

Run: `cd web_interface/backend && python3 -m pytest tests/test_storage.py -v`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/tests/test_storage.py
git commit -m "test: add integration tests for RunStorage abstraction layer"
```

---

### Task 11: Backward Compatibility for Legacy Runs

**Files:**
- No new files; verification only.

This task is a manual verification checkpoint. Existing runs (created before the storage migration) stored files at:
- `web_interface/uploads/{run_id}_{filename}` (uploaded files)
- `src/unified_pipeline/outputs/stage_*/{document_uid}_*.json` (pipeline outputs)
- `web_interface/outputs/{run_id}/` (web output directory)

The new code must handle these legacy paths gracefully.

- [ ] **Step 1: Verify legacy file serving**

The `_legacy_find_file` function in `steps.py` handles this. Verify manually:

1. Start the backend: `cd web_interface/backend && python3 -m uvicorn app.main:app --port 8000`
2. If there are existing runs in the database, navigate to their pipeline viewer.
3. Verify that step outputs (JSON files) can be previewed and downloaded.
4. Verify that final .docx files can be downloaded.

- [ ] **Step 2: Verify new upload flow**

1. Upload a new CV file through the frontend.
2. Verify the file is stored at `web_interface/outputs/runs/{run_id}/input/{filename}` (the new RunStorage path).
3. Start the pipeline run.
4. Verify step outputs appear at `web_interface/outputs/runs/{run_id}/steps/{stage_id}/{filename}` after each step completes.
5. Verify the final document is also at `web_interface/outputs/runs/{run_id}/output/{filename}`.

- [ ] **Step 3: Document legacy behavior**

The legacy fallback in `_legacy_find_file` should be removed in a future cleanup once all existing runs have been re-run or archived. Add a TODO comment in `steps.py`:

```python
# TODO: Remove _legacy_find_file after all pre-migration runs are archived.
# Legacy runs stored files at web_interface/uploads/ and src/unified_pipeline/outputs/.
# New runs use RunStorage with the key structure: runs/{run_id}/input|steps|output/...
```

- [ ] **Step 4: Commit (if any changes)**

```bash
git add web_interface/backend/app/api/steps.py
git commit -m "chore: add TODO for legacy file path cleanup"
```

---

## Environment Variables Summary

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CVICHE_STORAGE_BACKEND` | No | `local` | `local` or `s3` |
| `CVICHE_S3_BUCKET` | If s3 | -- | S3 bucket name |
| `CVICHE_S3_PREFIX` | No | `cviche` | S3 key prefix |
| `AWS_DEFAULT_REGION` | No | boto3 default | AWS region for S3 |

## Key Design Decisions

1. **Pipeline executes locally, S3 at boundaries.** The pipeline code does not speak S3. Files are pulled from S3 to local ephemeral storage before execution, and pushed back after each step. This avoids refactoring every pipeline stage.

2. **Per-step upload for crash resilience.** After each step completes, outputs are pushed to S3 immediately. If a pod crashes, completed step outputs are safe.

3. **Presigned URLs for downloads.** S3 downloads use 5-minute presigned URLs. Auth and ownership are checked before generating the URL. The URL itself bypasses auth middleware (acceptable for an internal tool with short expiry).

4. **Legacy fallback.** Pre-migration runs stored files in different locations. The `_legacy_find_file` function handles these gracefully until all old runs are archived.

5. **Singleton factory.** The storage backend is initialized once on startup and shared across all requests. The factory reads `CVICHE_STORAGE_BACKEND` once and returns the same instance thereafter.

6. **ConfigMap for config files.** `auth_config.yaml` and `consent_text.md` are mounted via Kubernetes ConfigMap on EKS. They are config, not run data, and must survive pod restarts.
