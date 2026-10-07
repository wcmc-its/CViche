"""S3 implementation of RunStorage.

Used in production (EKS). Reads bucket name from CVICHE_S3_BUCKET and
an optional key prefix from CVICHE_S3_PREFIX (default "cviche").

Authentication uses the boto3 default credential chain (IRSA on EKS).
boto3 is only imported when this module is loaded, so it is not required
for local-only development.
"""

import os
import logging
from urllib.parse import quote

from app.storage.base import (
    RunStorage,
    StorageError,
    StorageKeyExists,
    StorageKeyNotFound,
    check_artifact_size,
    validate_key,
    validate_run_id,
    validate_run_key,
)

logger = logging.getLogger(__name__)

# S3 error codes that mean "the object is absent" for GetObject/HeadObject.
# NoSuchBucket is deliberately excluded: a missing or misconfigured bucket is
# an infrastructure fault, not a missing artifact, and must propagate so it
# surfaces as an outage rather than a user-facing 404 (#790).
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "404"})

# The object tag GuardDuty Malware Protection for S3 writes its scan result to
# (#1333). Its values are listed beside the download gate in app/api/steps.py.
# https://docs.aws.amazon.com/guardduty/latest/ug/monitor-enable-s3-object-tagging-malware-protection.html
MALWARE_SCAN_STATUS_TAG = "GuardDutyMalwareScanStatus"

# boto3's own defaults (~60s connect, ~60s read, botocore's own retry policy)
# are effectively unbounded relative to the request budget that actually
# matters: create_run_archive (app/api/upload.py) issues up to 2 put_object
# calls per run-id attempt for up to _RUN_ID_ATTEMPTS=5 attempts, all inside
# ONE synchronous upload request (#791). Worst case at these values: 10
# sequential calls x 3 total attempts x (3s + 10s) = ~390s, under the ALB's
# 500s idle timeout, so the client sees an error rather than a dropped
# connection. Values decided on #791.
S3_CONNECT_TIMEOUT_S = 3
S3_READ_TIMEOUT_S = 10
# Total attempts per call, the initial one included (botocore's
# `total_max_attempts`; its `max_attempts` would count retries only).
S3_TOTAL_ATTEMPTS = 3

# Most keys one DeleteObjects request accepts (an S3 API hard limit).
S3_DELETE_BATCH_MAX = 1000


def _translate_client_error(code: str, context: str) -> StorageError | None:
    """Map a known AWS error code to the application exception it means.

    One place for put_file_exclusive, get_file and exists to agree on what
    a code means, instead of each interpreting it independently (PR #779
    review, s3_storage.py item 8; #790 acceptance criterion 2). HeadObject's
    real error responses carry only a bare "404" for a missing key, never
    the modeled "NoSuchKey"/"PreconditionFailed" codes GetObject and
    PutObject-with-precondition use -- but "404" is in _NOT_FOUND_CODES, so
    routing exists() through this helper still maps it correctly. Returns
    None for a code this store attaches no meaning to, so the caller
    re-raises the original ClientError unchanged -- an outage or permissions
    fault, not a storage-contract event.
    """
    if code == "PreconditionFailed":
        return StorageKeyExists(f"{context} already exists")
    if code in _NOT_FOUND_CODES:
        return StorageKeyNotFound(f"No such S3 object: {context}")
    return None


class StorageDeleteError(RuntimeError):
    """Raised when a DeleteObjects call succeeds but reports per-key Errors.

    S3 answers a bulk delete with 200 even when some keys were NOT removed
    (AccessDenied on one key, a KMS fault, ...), listing them under "Errors".
    Counting the batch as deleted would let the lifecycle reaper report a run
    as cleaned up while objects remain (PR #779 review, item 1).
    """


def _validate_s3_key_text(key: str, *, allow_empty: bool) -> None:
    """S3-side key checks on top of validate_key (PR #779 review, item 9).

    A NUL byte is not a namespace escape, so base.validate_key does not reject
    it, but no legitimate key carries one and S3 would store the literal byte.
    An empty key is legal for LIST prefixes (list_files(run_id, "")) and
    illegal for every object operation, which would otherwise read or write
    the namespace root "cviche/runs/{run_id}/" as if it were a file.
    """
    if "\x00" in key:
        raise ValueError(f"storage key must not contain a NUL byte: {key!r}")
    if not allow_empty and not key:
        raise ValueError("storage key must be non-empty for an object operation")


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
        # Default to "" (falsy), NOT "local": an s3 backend with CVICHE_S3_BUCKET
        # unset must trip the guard below, not silently operate on a bucket
        # literally named "local" (issue #109).
        s3_bucket, _ = get_config("s3", "CVICHE_S3_BUCKET", default="")

        self._bucket = bucket or s3_bucket
        if not self._bucket:
            raise ValueError(
                "S3 bucket not configured. Set CVICHE_S3_BUCKET environment variable."
            )
        s3_bucket_prefix, _ = get_config("s3", "CVICHE_S3_PREFIX", default="cviche")

        # Unlike self._bucket above (which deliberately keeps `or` for the
        # #109 guard), an explicit prefix="" is a real, distinct choice -- it
        # means "no prefix segment" -- and must not be silently replaced by
        # the configured default (PR #779 review, s3_storage.py item 5; #791).
        self._prefix = prefix if prefix is not None else s3_bucket_prefix

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
            config=Config(
                signature_version="s3v4",
                connect_timeout=S3_CONNECT_TIMEOUT_S,
                read_timeout=S3_READ_TIMEOUT_S,
                retries={"mode": "standard", "total_max_attempts": S3_TOTAL_ATTEMPTS},
            ),
        )

    def _key_prefix(self) -> str:
        """The store-root segment before "runs/..." or a global key.

        Empty when self._prefix is "" (an explicit no-prefix store, #791),
        so a caller-supplied empty prefix does not produce a key with a
        leading "/" -- "" plus "runs/..." is "runs/...", not "/runs/...".
        """
        return f"{self._prefix}/" if self._prefix else ""

    def _s3_key(self, run_id: str, key: str, *, allow_empty: bool = False) -> str:
        """Build the full S3 key for a run artifact.

        Validates at the boundary so put_file, put_file_exclusive, get_file,
        list_files, get_download_url and exists all inherit the same invariant
        from one place. S3 keys are opaque strings, so ".." cannot escape the
        bucket, but it does silently MISPLACE an object into a sibling
        namespace (run_id "../other" yields the literal key
        "cviche/runs/../other/..."), which is the same isolation break.

        Only list_files passes allow_empty=True: "" is a valid list prefix
        (the whole run) but never a valid object key (PR #779 review, item 9).
        """
        validate_run_key(run_id, key)
        _validate_s3_key_text(key, allow_empty=allow_empty)
        return f"{self._key_prefix()}runs/{run_id}/{key}"

    def put_file(self, run_id: str, key: str, data: bytes) -> None:
        check_artifact_size(data)
        s3_key = self._s3_key(run_id, key)
        self._s3.put_object(Bucket=self._bucket, Key=s3_key, Body=data)
        logger.debug("Uploaded s3://%s/%s (%d bytes)", self._bucket, s3_key, len(data))

    def put_file_exclusive(self, run_id: str, key: str, data: bytes) -> None:
        check_artifact_size(data)
        s3_key = self._s3_key(run_id, key)
        try:
            self._s3.put_object(
                Bucket=self._bucket, Key=s3_key, Body=data, IfNoneMatch="*",
            )
        except self._s3.exceptions.ClientError as e:
            # S3 answers a failed IfNoneMatch precondition with a 412 whose
            # error code is PreconditionFailed -- that, and only that, means
            # "the key already exists" (#685). Any other ClientError is a real
            # outage/permissions fault and must propagate, not be mistaken for
            # a collision.
            code = e.response.get("Error", {}).get("Code", "")
            translated = _translate_client_error(code, f"{run_id}/{key}")
            if translated is not None:
                raise translated from e
            raise
        logger.debug(
            "Uploaded s3://%s/%s (%d bytes, exclusive)", self._bucket, s3_key, len(data),
        )

    def put_global(self, key: str, data: bytes) -> None:
        check_artifact_size(data)
        validate_key(key)
        _validate_s3_key_text(key, allow_empty=False)
        s3_key = f"{self._key_prefix()}{key}"
        self._s3.put_object(Bucket=self._bucket, Key=s3_key, Body=data)
        logger.debug("Uploaded s3://%s/%s (%d bytes)", self._bucket, s3_key, len(data))

    def get_global(self, key: str) -> bytes:
        validate_key(key)
        _validate_s3_key_text(key, allow_empty=False)
        s3_key = f"{self._key_prefix()}{key}"
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=s3_key)["Body"].read()
        except self._s3.exceptions.NoSuchKey as e:
            raise StorageKeyNotFound(f"No such S3 object: s3://{self._bucket}/{s3_key}") from e

    def list_global(self, prefix: str) -> list[str]:
        validate_key(prefix)
        _validate_s3_key_text(prefix, allow_empty=False)
        root = self._key_prefix()
        keys: list[str] = []
        for page in self._s3.get_paginator("list_objects_v2").paginate(
            Bucket=self._bucket, Prefix=f"{root}{prefix}"
        ):
            keys.extend(obj["Key"][len(root):] for obj in page.get("Contents", []))
        return sorted(keys)

    def _delete_by_prefix(self, s3_prefix: str) -> int:
        """List and bulk-delete every object under a full S3 key prefix.

        Idempotent (no objects -> 0). Batches into delete_objects calls of up to
        S3_DELETE_BATCH_MAX keys, reusing the same paginator idiom as
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
                if len(batch) == S3_DELETE_BATCH_MAX:
                    deleted += self._delete_batch(batch)
                    batch = []
        if batch:
            deleted += self._delete_batch(batch)
        if deleted:
            logger.debug("Deleted %d object(s) under s3://%s/%s", deleted, self._bucket, s3_prefix)
        return deleted

    def _delete_batch(self, batch: list[dict]) -> int:
        """One DeleteObjects call; returns how many keys S3 confirms deleted.

        PR #779 review, item 1: the count comes from the response's "Deleted"
        list, not len(batch), and any "Errors" entry raises so a partial
        failure is never reported as a clean cleanup.
        """
        response = self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": batch})
        errors = response.get("Errors", [])
        if errors:
            for err in errors:
                logger.error(
                    "DeleteObjects left s3://%s/%s in place: %s %s",
                    self._bucket, err.get("Key"), err.get("Code"), err.get("Message", ""),
                )
            failed = ", ".join(f"{err.get('Key')} ({err.get('Code')})" for err in errors)
            raise StorageDeleteError(
                f"{len(errors)} object(s) not deleted from s3://{self._bucket}: {failed}"
            )
        return len(response.get("Deleted", []))

    def delete_run(self, run_id: str) -> int:
        # Everything under {prefix}/runs/{run_id}/. Validated explicitly:
        # this prefix is built here, NOT through _s3_key, so it would not
        # otherwise inherit the boundary check -- and a malformed id turns a
        # routing bug into a bulk delete of the wrong namespace.
        validate_run_id(run_id)
        return self._delete_by_prefix(f"{self._key_prefix()}runs/{run_id}/")

    def delete_global_prefix(self, prefix: str) -> int:
        # Same reasoning as delete_run: a destructive prefix built outside
        # _s3_key needs the boundary check spelled out.
        validate_key(prefix)
        # An empty prefix would resolve to "{self._key_prefix()}" and pass
        # the non-empty check in _delete_by_prefix, wiping the whole store;
        # the base contract says the backend refuses it (PR #779 review,
        # item 9).
        _validate_s3_key_text(prefix, allow_empty=False)
        return self._delete_by_prefix(f"{self._key_prefix()}{prefix}")

    def get_file(self, run_id: str, key: str) -> bytes:
        s3_key = self._s3_key(run_id, key)
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=s3_key)
            return response["Body"].read()
        except self._s3.exceptions.NoSuchKey as e:
            raise StorageKeyNotFound(
                f"No such S3 object: s3://{self._bucket}/{s3_key}"
            ) from e
        except self._s3.exceptions.ClientError as e:
            # A genuinely-absent object can surface as a plain 404 ClientError
            # rather than the modeled NoSuchKey -- mapped to StorageKeyNotFound
            # so callers treat it as an expected miss. NoSuchBucket is
            # deliberately NOT mapped (#790): a missing/misconfigured bucket
            # is an infrastructure fault, not a missing artifact, and must
            # propagate so it surfaces as an outage rather than a
            # user-facing 404. A 403 AccessDenied is likewise NOT mapped: it
            # signals a real IAM/KMS problem the caller should log as an
            # outage. (It is also what a missing key looks like without
            # s3:ListBucket -- grant ListBucket to get a clean 404 here
            # instead of a 403.)
            code = e.response.get("Error", {}).get("Code", "")
            translated = _translate_client_error(code, f"s3://{self._bucket}/{s3_key}")
            if translated is not None:
                raise translated from e
            raise

    def list_files(self, run_id: str, prefix: str = "") -> list[str]:
        s3_prefix = self._s3_key(run_id, prefix, allow_empty=True)
        keys: list[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self._bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                # Strip the run-level prefix to return a relative key
                full_key = obj["Key"]
                run_prefix = f"{self._key_prefix()}runs/{run_id}/"
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
            # RFC 5987. S3 rejects a disposition it cannot encode as ISO-8859-1
            # ("InvalidArgument: Header value cannot be represented using
            # ISO-8859-1"), so a CV named "Smith's CV.docx" -- Word autocorrects
            # the apostrophe to U+2019 -- 400s on download unless percent-encoded.
            params["ResponseContentDisposition"] = (
                f"attachment; filename*=utf-8''{quote(download_name, safe='')}"
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
            # Routed through the same translation helper put_file_exclusive
            # and get_file use (#790 acceptance criterion 2), instead of
            # exists() interpreting the "404" code on its own. A translated
            # StorageKeyNotFound means the object is absent; anything else
            # (AccessDenied, an outage, ...) is not a storage-contract event
            # and must propagate.
            code = e.response.get("Error", {}).get("Code", "")
            translated = _translate_client_error(code, f"s3://{self._bucket}/{s3_key}")
            if isinstance(translated, StorageKeyNotFound):
                return False
            raise

    def get_malware_scan_status(self, run_id: str, key: str) -> str | None:
        # A ClientError (AccessDenied without s3:GetObjectTagging, an outage)
        # propagates: an unreadable tag is not the same as "not scanned yet".
        s3_key = self._s3_key(run_id, key)
        response = self._s3.get_object_tagging(Bucket=self._bucket, Key=s3_key)
        for tag in response["TagSet"]:
            if tag["Key"] == MALWARE_SCAN_STATUS_TAG:
                return tag["Value"]
        return None
