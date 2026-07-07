"""S3RunStorage.get_file error mapping.

A genuinely-absent object (NoSuchKey / 404 / NoSuchBucket) must raise
FileNotFoundError so callers treat it as an expected miss and fall back. A 403
AccessDenied must PROPAGATE -- it is a real IAM/KMS fault, not a missing file,
and masking it as "missing" would hide an outage (and is also what a missing
key looks like without s3:ListBucket -- that wants an IAM fix, not a code one).
"""
import pytest

boto3 = pytest.importorskip("boto3")
from botocore.stub import Stubber
from botocore.exceptions import ClientError

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
