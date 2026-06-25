"""S3 implementation of RunStorage.

Used in production (EKS). Reads bucket name from CVICHE_S3_BUCKET and
an optional key prefix from CVICHE_S3_PREFIX (default "cviche").

Authentication uses the boto3 default credential chain (IRSA on EKS).
boto3 is only imported when this module is loaded, so it is not required
for local-only development.
"""

import os
import logging
import yaml

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
        from botocore.config import Config

        from app.config_loader import get_config
        s3_bucket, source = get_config("s3", "CVICHE_S3_BUCKET", default="local")
       
        self._bucket = bucket or s3_bucket
        if not self._bucket:
            raise ValueError(
                "S3 bucket not configured. Set CVICHE_S3_BUCKET environment variable."
            )
        s3_bucket_prefix, source = get_config("s3", "CVICHE_S3_PREFIX", default="cviche")
        
        self._prefix = prefix or s3_bucket_prefix

        # Force Signature Version 4. Without it, botocore falls back to the
        # global s3.amazonaws.com endpoint and signs presigned URLs with SigV2,
        # which S3 rejects for objects encrypted with SSE-KMS:
        #   "Requests specifying Server Side Encryption with AWS KMS managed
        #    keys require AWS Signature Version 4."
        # Pinning the region (from AWS_REGION, set by the IRSA webhook on EKS)
        # also keeps requests on the regional endpoint.
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        self._s3 = boto3.client(
            "s3",
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    def _s3_key(self, run_id: str, key: str) -> str:
        """Build the full S3 key for a run artifact."""
        return f"{self._prefix}/runs/{run_id}/{key}"

    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        s3_key = self._s3_key(run_id, key)
        self._s3.put_object(Bucket=self._bucket, Key=s3_key, Body=data)
        logger.debug("Uploaded s3://%s/%s (%d bytes)", self._bucket, s3_key, len(data))

    def put_global(self, key: str, data: bytes) -> None:
        s3_key = f"{self._prefix}/{key}"
        self._s3.put_object(Bucket=self._bucket, Key=s3_key, Body=data)
        logger.debug("Uploaded s3://%s/%s (%d bytes)", self._bucket, s3_key, len(data))

    def _delete_by_prefix(self, s3_prefix: str) -> int:
        """List and bulk-delete every object under a full S3 key prefix.

        Idempotent (no objects -> 0). Batches into delete_objects calls of up to
        1000 keys (the API hard limit), reusing the same paginator idiom as
        list_files.
        """
        if not s3_prefix or not s3_prefix.strip("/"):
            # Refuse to delete the entire bucket/prefix on an empty argument.
            raise ValueError("delete prefix must be non-empty")
        paginator = self._s3.get_paginator("list_objects_v2")
        deleted = 0
        batch: list[dict] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                batch.append({"Key": obj["Key"]})
                if len(batch) == 1000:
                    self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": batch})
                    deleted += len(batch)
                    batch = []
        if batch:
            self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": batch})
            deleted += len(batch)
        if deleted:
            logger.debug("Deleted %d object(s) under s3://%s/%s", deleted, self._bucket, s3_prefix)
        return deleted

    def delete_run(self, run_id: str) -> int:
        # Everything under {prefix}/runs/{run_id}/
        return self._delete_by_prefix(f"{self._prefix}/runs/{run_id}/")

    def delete_global_prefix(self, prefix: str) -> int:
        return self._delete_by_prefix(f"{self._prefix}/{prefix}")

    def get_file(self, run_id: str, key: str) -> bytes:
        s3_key = self._s3_key(run_id, key)
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=s3_key)
            return response["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            raise FileNotFoundError(f"No such S3 object: s3://{self._bucket}/{s3_key}")
        except self._s3.exceptions.ClientError as e:
            # A genuinely-absent object can surface as a 404 ClientError (or
            # NoSuchBucket) rather than the modeled NoSuchKey -- map those to
            # FileNotFoundError so callers treat it as an expected miss. A 403
            # AccessDenied is deliberately NOT mapped: it signals a real
            # IAM/KMS problem the caller should log as an outage. (It is also
            # what a missing key looks like without s3:ListBucket -- grant
            # ListBucket to get a clean 404 here instead of a 403.)
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                raise FileNotFoundError(f"No such S3 object: s3://{self._bucket}/{s3_key}")
            raise

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
