"""S3RunStorage.get_file error mapping.

A genuinely-absent object (NoSuchKey / 404 / NoSuchBucket) must raise
FileNotFoundError so callers treat it as an expected miss and fall back. A 403
AccessDenied must PROPAGATE -- it is a real IAM/KMS fault, not a missing file,
and masking it as "missing" would hide an outage (and is also what a missing
key looks like without s3:ListBucket -- that wants an IAM fix, not a code one).
"""
from urllib.parse import parse_qs, urlparse

import pytest

boto3 = pytest.importorskip("boto3")
from botocore.stub import Stubber
from botocore.exceptions import ClientError

from app.storage.base import StorageKeyExists
from app.storage.s3_storage import S3RunStorage


def _storage():
    return S3RunStorage(bucket="test-bucket", prefix="cviche")


def test_unset_bucket_raises_instead_of_using_local(monkeypatch):
    """An s3 backend with CVICHE_S3_BUCKET unset must raise, not silently
    operate on a bucket literally named 'local' (#109)."""
    monkeypatch.delenv("CVICHE_S3_BUCKET", raising=False)
    with pytest.raises(ValueError, match="bucket not configured"):
        S3RunStorage()  # no explicit bucket -> resolves from config -> "" -> raise


def test_get_file_nosuchkey_is_filenotfound():
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with stub, pytest.raises(FileNotFoundError):
        storage.get_file("run1", "input/cv.docx")


def test_get_file_404_clienterror_is_filenotfound():
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("get_object", service_error_code="404", http_status_code=404)
    with stub, pytest.raises(FileNotFoundError):
        storage.get_file("run1", "input/cv.docx")


def test_get_file_accessdenied_propagates():
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)
    with stub, pytest.raises(ClientError):
        storage.get_file("run1", "input/cv.docx")


def test_put_file_exclusive_sends_if_none_match_and_maps_412(monkeypatch):
    """#685: an existing key must be reported as a collision, not silently
    overwritten -- put_file_exclusive asks S3 to enforce that with
    IfNoneMatch="*" and maps the resulting 412 PreconditionFailed to
    StorageKeyExists so callers can regenerate the run id and retry."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error(
        "put_object",
        service_error_code="PreconditionFailed",
        http_status_code=412,
        expected_params={
            "Bucket": "test-bucket",
            "Key": "cviche/runs/run1/input/cv.docx",
            "Body": b"dummy-bytes",
            "IfNoneMatch": "*",
        },
    )
    with stub, pytest.raises(StorageKeyExists):
        storage.put_file_exclusive("run1", "input/cv.docx", b"dummy-bytes")
    stub.assert_no_pending_responses()


def test_put_file_exclusive_other_clienterror_propagates():
    """A non-412 ClientError (outage, permissions) is a real fault, not a
    collision, and must not be swallowed as StorageKeyExists."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
    with stub, pytest.raises(ClientError):
        storage.put_file_exclusive("run1", "input/cv.docx", b"dummy-bytes")


def test_download_name_is_percent_encoded_not_raw(monkeypatch):
    """S3 rejects a disposition it cannot encode as ISO-8859-1 ("InvalidArgument:
    Header value cannot be represented using ISO-8859-1"), so a CV named
    "Smith's CV.docx" (Word autocorrects the apostrophe to U+2019) 400s on
    download unless the name is percent-encoded. Verified against the real
    bucket: raw -> InvalidArgument, encoded -> 200."""
    # Presigning signs the URL, so it needs credentials -- CI has none.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    url = _storage().get_download_url("run1", "input/cv.docx",
                                      download_name="Dvořák’s CV – 2026.docx")
    # parse_qs undoes the URL-encoding boto3 applies; the RFC 5987 layer remains.
    disposition = parse_qs(urlparse(url).query)["response-content-disposition"][0]
    assert disposition == (
        "attachment; filename*=utf-8''Dvo%C5%99%C3%A1k%E2%80%99s%20CV%20%E2%80%93%202026.docx")
    # The signed URL must carry only latin-1-encodable bytes for S3 to accept it.
    disposition.encode("latin-1")
