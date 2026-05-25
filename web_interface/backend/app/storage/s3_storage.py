"""S3 implementation of RunStorage.

Used in production (EKS). Reads bucket name from CVICHE_S3_BUCKET and
an optional key prefix from CVICHE_S3_PREFIX (default "cviche").

Authentication uses the boto3 default credential chain (IRSA on EKS).
boto3 is only imported when this module is loaded, so it is not required
for local-only development.
"""

import os
import logging

from app.storage.base import RunStorage

logger = logging.getLogger(__name__)


class S3RunStorage(RunStorage):
    """Store run artifacts in Amazon S3.

    S3 key layout:
        {prefix}/runs/{run_id}/{key}

    For example:
        cviche/runs/A1B2C3/input/cv.docx
        cviche/runs/A1B2C3/steps/3a/output.json
    """

    def __init__(self, bucket: str | None = None, prefix: str | None = None):
        import boto3

        self._bucket = bucket or os.environ.get("CVICHE_S3_BUCKET")
        if not self._bucket:
            raise ValueError(
                "S3 bucket not configured. Set CVICHE_S3_BUCKET environment variable."
            )

        self._prefix = prefix or os.environ.get("CVICHE_S3_PREFIX", "cviche")
        self._s3 = boto3.client("s3")

    def _s3_key(self, run_id: str, key: str) -> str:
        """Build the full S3 key for a run artifact."""
        return f"{self._prefix}/runs/{run_id}/{key}"

    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        s3_key = self._s3_key(run_id, key)
        self._s3.put_object(Bucket=self._bucket, Key=s3_key, Body=data)
        logger.debug("Uploaded s3://%s/%s (%d bytes)", self._bucket, s3_key, len(data))

    def get_file(self, run_id: str, key: str) -> bytes:
        s3_key = self._s3_key(run_id, key)
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=s3_key)
            return response["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            raise FileNotFoundError(f"No such S3 object: s3://{self._bucket}/{s3_key}")

    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        s3_prefix = self._s3_key(run_id, prefix)
        keys: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self._bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                # Strip the run-level prefix to return a relative key
                full_key = obj["Key"]
                run_prefix = f"{self._prefix}/runs/{run_id}/"
                if full_key.startswith(run_prefix):
                    keys.append(full_key[len(run_prefix):])
                else:
                    keys.append(full_key)

        return keys

    def get_download_url(
        self,
        run_id: str,
        key: str,
        expires_in: int = 300,
        download_name: str | None = None,
    ) -> str | None:
        s3_key = self._s3_key(run_id, key)
        params = {"Bucket": self._bucket, "Key": s3_key}
        if download_name:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{download_name}"'
            )
        url = self._s3.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return url

    def exists(self, run_id: str, key: str) -> bool:
        s3_key = self._s3_key(run_id, key)
        try:
            self._s3.head_object(Bucket=self._bucket, Key=s3_key)
            return True
        except self._s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise
