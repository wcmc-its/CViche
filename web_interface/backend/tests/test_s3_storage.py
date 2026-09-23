"""S3RunStorage.get_file error mapping.

A genuinely-absent object (NoSuchKey / 404) must raise StorageKeyNotFound
(which subclasses FileNotFoundError, so callers that catch FileNotFoundError
keep working) so callers treat it as an expected miss and fall back.
NoSuchBucket must NOT be treated as a missing object -- a missing or
misconfigured bucket is an infrastructure fault, and converting it to
FileNotFoundError would hide an outage as a user-facing 404 (#790). A 403
AccessDenied must PROPAGATE -- it is a real IAM/KMS fault, not a missing file,
and masking it as "missing" would hide an outage (and is also what a missing
key looks like without s3:ListBucket -- that wants an IAM fix, not a code one).
"""
import io
from urllib.parse import parse_qs, urlparse

import pytest

boto3 = pytest.importorskip("boto3")
from botocore.stub import Stubber
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from app.storage.base import StorageKeyExists, StorageKeyNotFound
from app.storage.s3_storage import S3RunStorage, StorageDeleteError


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


def test_get_file_nosuchbucket_does_not_become_filenotfound():
    """#790: a missing/misconfigured bucket is an infrastructure fault, not
    a missing artifact. Before this fix, NoSuchBucket was in the same
    not-found tuple as NoSuchKey/404 and got reported as FileNotFoundError,
    which every caller (app/api/steps.py, app/api/runs.py) turns into a
    user-facing 404 instead of logging an outage."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("get_object", service_error_code="NoSuchBucket", http_status_code=404)
    with stub, pytest.raises(ClientError) as ei:
        storage.get_file("run1", "input/cv.docx")
    assert not isinstance(ei.value, FileNotFoundError)


def test_get_file_nosuchkey_raises_storagekeynotfound_not_bare_filenotfound():
    """#790 acceptance criterion 3: the concrete exception is
    StorageKeyNotFound (a FileNotFoundError subclass), not a bare
    FileNotFoundError -- callers that want to distinguish a storage-contract
    miss from an unrelated FileNotFoundError elsewhere in the call stack
    can now do so."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with stub, pytest.raises(StorageKeyNotFound):
        storage.get_file("run1", "input/cv.docx")


def test_exists_nosuchbucket_propagates_not_false():
    """#790, the exists() half: a NoSuchBucket-shaped ClientError must not
    be swallowed into a plain False the way a genuine 404 is."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("head_object", service_error_code="NoSuchBucket", http_status_code=404)
    with stub, pytest.raises(ClientError):
        storage.exists("run1", "input/cv.docx")


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


@pytest.mark.parametrize("run_id,key", [
    ("../other", "input/cv.docx"),
    ("run1", "../other/input/cv.docx"),
    ("run1", "/absolute/cv.docx"),
    ("run1", "input/../../other/cv.docx"),
    ("a b", "input/cv.docx"),
    # PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 9:
    # a NUL byte in either component (the run id via RUN_ID_PATTERN, the key
    # via the S3-side check) must be refused before any request is built.
    ("run1", "input/cv\x00.docx"),
    ("run\x00", "input/cv.docx"),
])
def test_s3_key_rejects_unsafe_identifiers(run_id, key):
    """S3 keys are opaque strings, so ".." cannot escape the bucket -- but it
    does silently place the object in a SIBLING run's namespace
    ("cviche/runs/../other/..." is a literal key), which is the same
    isolation break. Rejected inside the storage boundary, so no request
    ever reaches S3."""
    storage = _storage()
    stub = Stubber(storage._s3)
    with stub:  # no stubbed responses queued: any S3 call would raise
        with pytest.raises(ValueError):
            storage.put_file_exclusive(run_id, key, b"x")
        with pytest.raises(ValueError):
            storage.get_file(run_id, key)
    stub.assert_no_pending_responses()


def test_s3_delete_run_rejects_unsafe_run_id():
    """delete_run builds its destructive prefix directly, NOT through
    _s3_key, so it needs the check spelled out: an unvalidated id turns a
    single-run cleanup into a bulk delete of the wrong prefix."""
    storage = _storage()
    stub = Stubber(storage._s3)
    with stub:
        with pytest.raises(ValueError):
            storage.delete_run("../")
        with pytest.raises(ValueError):
            storage.delete_global_prefix("../../")
    stub.assert_no_pending_responses()


def test_s3_key_still_accepts_the_prefixes_live_callers_pass():
    """Regression guard on the validator: list_files passes an empty key and
    trailing-slash prefixes (steps.py:611 "prompt_logs/",
    quality_score_service.py:54 "outputs/"), and legacy run ids from the
    pre-#685 generator can contain "-"/"_". None may be rejected."""
    storage = _storage()
    # "" is only legal as a LIST prefix (item 9 split): list_files passes
    # allow_empty=True; object operations reject it, see
    # test_s3_object_operations_reject_empty_key.
    assert storage._s3_key("run1", "", allow_empty=True) == "cviche/runs/run1/"
    assert storage._s3_key("run1", "outputs/") == "cviche/runs/run1/outputs/"
    assert storage._s3_key("A-B_c1", "prompt_logs/") == "cviche/runs/A-B_c1/prompt_logs/"


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


# ---------------------------------------------------------------------------
# PR #779 review thread web_interface/backend/tests/test_s3_storage.py
# ---------------------------------------------------------------------------

RUN_PREFIX = "cviche/runs/run1/"


def _stub_listing(stub, keys, prefix=RUN_PREFIX):
    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": k} for k in keys]},
        expected_params={"Bucket": "test-bucket", "Prefix": prefix},
    )


def _delete_params(keys):
    return {"Bucket": "test-bucket", "Delete": {"Objects": [{"Key": k} for k in keys]}}


def test_delete_run_partial_errors_raise_and_are_not_counted():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 1

    S3 answers DeleteObjects with 200 even when some keys stay in place,
    listing them under "Errors". Such a batch must surface as
    StorageDeleteError naming the surviving key, not be counted as deleted."""
    storage = _storage()
    stub = Stubber(storage._s3)
    a, b = RUN_PREFIX + "input/cv.docx", RUN_PREFIX + "steps/3a/output.json"
    _stub_listing(stub, [a, b])
    stub.add_response(
        "delete_objects",
        {
            "Deleted": [{"Key": a}],
            "Errors": [{"Key": b, "Code": "AccessDenied", "Message": "Access Denied"}],
        },
        expected_params=_delete_params([a, b]),
    )
    with stub, pytest.raises(StorageDeleteError, match="cviche/runs/run1/steps/3a/output.json"):
        storage.delete_run("run1")
    stub.assert_no_pending_responses()


def test_delete_run_counts_only_deleted_entries():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 1

    The return value derives from the response's "Deleted" list, not from
    len(batch): a batch of 3 that S3 confirms 2 of (and reports no Errors
    for) returns 2."""
    storage = _storage()
    stub = Stubber(storage._s3)
    keys = [RUN_PREFIX + f"f{i}" for i in range(3)]
    _stub_listing(stub, keys)
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": keys[0]}, {"Key": keys[1]}]},
        expected_params=_delete_params(keys),
    )
    with stub:
        assert storage.delete_run("run1") == 2
    stub.assert_no_pending_responses()


def test_delete_by_prefix_splits_at_1000_keys():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 2

    DeleteObjects takes at most 1000 keys per request. 1001 listed keys must
    go out as exactly [0:1000] then [1000:]; Stubber's expected_params is an
    exact match, so a batch split anywhere else fails."""
    storage = _storage()
    stub = Stubber(storage._s3)
    keys = [RUN_PREFIX + f"f{i}" for i in range(1001)]
    _stub_listing(stub, keys)
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": k} for k in keys[:1000]]},
        expected_params=_delete_params(keys[:1000]),
    )
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": keys[1000]}]},
        expected_params=_delete_params(keys[1000:]),
    )
    with stub:
        assert storage.delete_run("run1") == 1001
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("bad_prefix", ["", "/", "//"])
def test_delete_by_prefix_refuses_empty_prefix(bad_prefix):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 2

    An empty (or slash-only) full prefix would enumerate and delete the whole
    bucket; it must raise before any S3 call."""
    storage = _storage()
    stub = Stubber(storage._s3)
    with stub, pytest.raises(ValueError, match="non-empty"):
        storage._delete_by_prefix(bad_prefix)
    stub.assert_no_pending_responses()


def test_delete_run_prefix_is_slash_terminated_run_namespace():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 3

    delete_run must list exactly "cviche/runs/run1/" -- WITH the trailing
    slash. "cviche/runs/run1" would also match run10/, run1a/, ... and the
    reaper would delete a sibling run's artifacts."""
    storage = _storage()
    stub = Stubber(storage._s3)
    key = "cviche/runs/run1/input/cv.docx"
    _stub_listing(stub, [key], prefix="cviche/runs/run1/")
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": key}]},
        expected_params=_delete_params([key]),
    )
    with stub:
        assert storage.delete_run("run1") == 1
    stub.assert_no_pending_responses()


def test_delete_global_prefix_lists_exactly_the_given_prefix():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 3

    delete_global_prefix joins the caller's prefix onto the store root
    verbatim ("cviche/by-submitter/e@x.edu/R1/"), never under runs/."""
    storage = _storage()
    stub = Stubber(storage._s3)
    key = "cviche/by-submitter/e@x.edu/R1/manifest.json"
    _stub_listing(stub, [key], prefix="cviche/by-submitter/e@x.edu/R1/")
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": key}]},
        expected_params=_delete_params([key]),
    )
    with stub:
        assert storage.delete_global_prefix("by-submitter/e@x.edu/R1/") == 1
    stub.assert_no_pending_responses()


def test_put_file_sends_bucket_key_body_without_precondition():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 4

    put_file is the overwriting write: exact Bucket/Key/Body, and (because
    expected_params is an exact dict match) NO IfNoneMatch."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_response(
        "put_object",
        {},
        expected_params={
            "Bucket": "test-bucket",
            "Key": "cviche/runs/run1/input/cv.docx",
            "Body": b"dummy-bytes",
        },
    )
    with stub:
        storage.put_file("run1", "input/cv.docx", b"dummy-bytes")
    stub.assert_no_pending_responses()


def test_list_files_returns_run_relative_keys():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 5

    Normal listing: the run namespace "cviche/runs/run1/" is stripped from
    every returned key, in S3 order."""
    storage = _storage()
    stub = Stubber(storage._s3)
    _stub_listing(stub, [RUN_PREFIX + "input/cv.docx", RUN_PREFIX + "steps/3a/output.json"])
    with stub:
        assert storage.list_files("run1") == ["input/cv.docx", "steps/3a/output.json"]
    stub.assert_no_pending_responses()


def test_list_files_prefix_filter_is_sent_to_s3():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 5

    Prefix-filtered listing: the caller's prefix is appended to the run
    namespace and sent as the S3 Prefix, so filtering happens server-side."""
    storage = _storage()
    stub = Stubber(storage._s3)
    _stub_listing(stub, [RUN_PREFIX + "steps/3a/output.json"], prefix=RUN_PREFIX + "steps/3a/")
    with stub:
        assert storage.list_files("run1", "steps/3a/") == ["steps/3a/output.json"]
    stub.assert_no_pending_responses()


def test_list_files_empty_result():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 5

    Empty listing: S3 omits "Contents" entirely for an empty prefix; the
    result is [] rather than a KeyError."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_response(
        "list_objects_v2",
        {"KeyCount": 0},
        expected_params={"Bucket": "test-bucket", "Prefix": RUN_PREFIX},
    )
    with stub:
        assert storage.list_files("run1") == []
    stub.assert_no_pending_responses()


def test_list_files_follows_pagination():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 5

    Multi-page listing: a truncated first page must be followed by a second
    request carrying its NextContinuationToken, and both pages' keys are
    returned in order."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_response(
        "list_objects_v2",
        {
            "IsTruncated": True,
            "NextContinuationToken": "tok",
            "Contents": [{"Key": RUN_PREFIX + "a.json"}],
        },
        expected_params={"Bucket": "test-bucket", "Prefix": RUN_PREFIX},
    )
    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": RUN_PREFIX + "b.json"}]},
        expected_params={"Bucket": "test-bucket", "Prefix": RUN_PREFIX, "ContinuationToken": "tok"},
    )
    with stub:
        assert storage.list_files("run1") == ["a.json", "b.json"]
    stub.assert_no_pending_responses()


def test_list_files_returns_foreign_key_unchanged():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 5

    Full-key to relative-key conversion only strips the run namespace: a key
    S3 returns that does not start with it is passed through verbatim (the
    else branch), never mangled by a blind slice."""
    storage = _storage()
    stub = Stubber(storage._s3)
    _stub_listing(stub, [RUN_PREFIX + "input/cv.docx", "cviche/runs/run10/input/cv.docx"])
    with stub:
        assert storage.list_files("run1") == ["input/cv.docx", "cviche/runs/run10/input/cv.docx"]
    stub.assert_no_pending_responses()


def test_exists_true_on_head_object_success():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 6"""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_response(
        "head_object",
        {"ContentLength": 3},
        expected_params={"Bucket": "test-bucket", "Key": "cviche/runs/run1/input/cv.docx"},
    )
    with stub:
        assert storage.exists("run1", "input/cv.docx") is True
    stub.assert_no_pending_responses()


def test_exists_false_on_404():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 6"""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error(
        "head_object", service_error_code="404", http_status_code=404,
        expected_params={"Bucket": "test-bucket", "Key": "cviche/runs/run1/input/cv.docx"},
    )
    with stub:
        assert storage.exists("run1", "input/cv.docx") is False
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("code,status", [("AccessDenied", 403), ("InternalError", 500)])
def test_exists_non_404_clienterror_propagates(code, status):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 6

    A 403 (IAM/KMS) or 5xx from head_object is an infrastructure fault; it
    must propagate as ClientError, not be reported as "file doesn't exist"."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_client_error("head_object", service_error_code=code, http_status_code=status)
    with stub, pytest.raises(ClientError) as ei:
        storage.exists("run1", "input/cv.docx")
    assert ei.value.response["Error"]["Code"] == code
    stub.assert_no_pending_responses()


def test_get_file_returns_body_bytes():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 7

    The success path reads the streaming Body fully and returns the bytes
    unchanged (including a leading NUL, so no text decoding sneaks in)."""
    storage = _storage()
    stub = Stubber(storage._s3)
    payload = b"\x00PK-docx-bytes"
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(payload), len(payload))},
        expected_params={"Bucket": "test-bucket", "Key": "cviche/runs/run1/input/cv.docx"},
    )
    with stub:
        assert storage.get_file("run1", "input/cv.docx") == payload
    stub.assert_no_pending_responses()


def test_put_global_writes_under_prefix_not_runs_namespace():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 8

    put_global writes {prefix}/{key}, never {prefix}/runs/{run_id}/{key}: the
    by-submitter index must sit beside runs/, not inside a run."""
    storage = _storage()
    stub = Stubber(storage._s3)
    stub.add_response(
        "put_object",
        {},
        expected_params={
            "Bucket": "test-bucket",
            "Key": "cviche/by-submitter/jdoe@example.edu/AAAAAA/manifest.json",
            "Body": b"{}",
        },
    )
    with stub:
        storage.put_global("by-submitter/jdoe@example.edu/AAAAAA/manifest.json", b"{}")
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("key", ["../escape", "/abs/manifest.json", "by-submitter/a\x00b", ""])
def test_put_global_rejects_unsafe_keys_before_any_call(key):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 8

    put_global validates its key at the boundary: a ".." component, an
    absolute key, a NUL byte or an empty key raises with no request made."""
    storage = _storage()
    stub = Stubber(storage._s3)
    with stub, pytest.raises(ValueError):
        storage.put_global(key, b"{}")
    stub.assert_no_pending_responses()


def test_s3_object_operations_reject_empty_key():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 9

    "" is a legal LIST prefix (base.py's validate_key allows it because
    list_files callers pass "" and "outputs/") but never a legal object key:
    every object operation would otherwise address the namespace root
    "cviche/runs/run1/" as a file. Rejected with no S3 call."""
    storage = _storage()
    stub = Stubber(storage._s3)
    with stub:
        with pytest.raises(ValueError, match="non-empty"):
            storage.put_file("run1", "", b"x")
        with pytest.raises(ValueError, match="non-empty"):
            storage.put_file_exclusive("run1", "", b"x")
        with pytest.raises(ValueError, match="non-empty"):
            storage.get_file("run1", "")
        with pytest.raises(ValueError, match="non-empty"):
            storage.exists("run1", "")
        with pytest.raises(ValueError, match="non-empty"):
            storage.get_download_url("run1", "")
        with pytest.raises(ValueError, match="non-empty"):
            storage.delete_global_prefix("")
    stub.assert_no_pending_responses()


def test_list_files_still_accepts_empty_prefix_after_object_key_check():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 9

    The other half of the empty-key split: list_files("run1", "") must keep
    working (steps.py and quality_score_service.py rely on it) and send the
    bare run namespace as the Prefix."""
    storage = _storage()
    stub = Stubber(storage._s3)
    _stub_listing(stub, [RUN_PREFIX + "input/cv.docx"], prefix=RUN_PREFIX)
    with stub:
        assert storage.list_files("run1", "") == ["input/cv.docx"]
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("op,code,status", [
    ("list_objects_v2", "AccessDenied", 403),
    ("list_objects_v2", "InternalError", 500),
    ("delete_objects", "AccessDenied", 403),
    ("delete_objects", "SlowDown", 503),
])
def test_delete_run_propagates_s3_errors(op, code, status):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 10

    AccessDenied, throttling (SlowDown) and server errors from either the
    listing or the delete call propagate as ClientError so the reaper logs a
    failure instead of counting the run as cleaned up."""
    storage = _storage()
    stub = Stubber(storage._s3)
    if op == "delete_objects":
        _stub_listing(stub, [RUN_PREFIX + "input/cv.docx"])
    stub.add_client_error(op, service_error_code=code, http_status_code=status)
    with stub, pytest.raises(ClientError) as ei:
        storage.delete_run("run1")
    assert ei.value.response["Error"]["Code"] == code
    stub.assert_no_pending_responses()


def test_delete_global_prefix_propagates_s3_errors():
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 10"""
    storage = _storage()
    stub = Stubber(storage._s3)
    _stub_listing(stub, ["cviche/by-submitter/e@x.edu/R1/manifest.json"],
                  prefix="cviche/by-submitter/e@x.edu/R1/")
    stub.add_client_error("delete_objects", service_error_code="SlowDown", http_status_code=503)
    with stub, pytest.raises(ClientError) as ei:
        storage.delete_global_prefix("by-submitter/e@x.edu/R1/")
    assert ei.value.response["Error"]["Code"] == "SlowDown"
    stub.assert_no_pending_responses()


def test_client_pins_sigv4_for_sse_kms(monkeypatch):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 11

    SSE-KMS objects reject SigV2-signed presigned URLs ("Requests specifying
    Server Side Encryption with AWS KMS managed keys require AWS Signature
    Version 4"), so the client must be built with signature_version s3v4 and
    every presigned URL it hands out must carry the SigV4 algorithm marker.
    (botocore >= 1.29 already defaults S3 to s3v4, so this pins the effective
    value: a refactor to signature_version="s3" fails here; simply dropping
    the explicit pin is behaviour-preserving on current botocore.)"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    storage = _storage()
    assert storage._s3.meta.config.signature_version == "s3v4"
    url = storage.get_download_url("run1", "input/cv.docx")
    query = parse_qs(urlparse(url).query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert "Signature" not in query  # the SigV2 marker


def test_region_from_aws_region_env(monkeypatch):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 11

    AWS_REGION (set by the IRSA webhook on EKS) selects the client region
    and keeps requests on the regional endpoint."""
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    client = _storage()._s3
    assert client.meta.region_name == "us-west-2"
    assert client.meta.endpoint_url.startswith("https://s3.us-west-2.")


def test_region_falls_back_to_aws_default_region(monkeypatch):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 11"""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    client = _storage()._s3
    assert client.meta.region_name == "eu-west-1"
    assert client.meta.endpoint_url.startswith("https://s3.eu-west-1.")


def test_aws_region_takes_precedence_over_default_region(monkeypatch):
    """PR #779 review thread web_interface/backend/tests/test_s3_storage.py item 11"""
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    assert _storage()._s3.meta.region_name == "us-west-2"


# ---------------------------------------------------------------------------
# #791: explicit prefix handling and bounded timeouts/retries
# ---------------------------------------------------------------------------

def test_prefix_empty_string_is_preserved_not_replaced_by_default(monkeypatch):
    """prefix="" is a real, distinct choice (an explicit no-prefix store) and
    must not be silently replaced by CVICHE_S3_PREFIX's default -- unlike
    self._bucket, which deliberately keeps `or` for the #109 guard."""
    monkeypatch.setenv("CVICHE_S3_PREFIX", "should-not-be-used")
    storage = S3RunStorage(bucket="test-bucket", prefix="")
    assert storage._prefix == ""


def test_prefix_defaults_when_omitted(monkeypatch):
    """prefix=None (the default, distinct from "") still resolves from
    CVICHE_S3_PREFIX -- only an explicit "" is preserved as empty."""
    monkeypatch.setenv("CVICHE_S3_PREFIX", "cviche-env-value")
    storage = S3RunStorage(bucket="test-bucket")
    assert storage._prefix == "cviche-env-value"


def test_prefix_empty_string_produces_keys_without_leading_slash():
    """#791 skeptic finding: naively joining f"{prefix}/runs/..." with an
    empty prefix produces a key with a leading "/". Pinned choice: an empty
    prefix means "no prefix segment", so the key is plain "runs/...", not
    "/runs/...", matching what a caller who deliberately chose no-prefix
    would expect."""
    storage = S3RunStorage(bucket="test-bucket", prefix="")
    assert storage._s3_key("run1", "input/cv.docx") == "runs/run1/input/cv.docx"
    assert storage._s3_key("run1", "", allow_empty=True) == "runs/run1/"


def test_delete_run_uses_key_prefix_when_store_prefix_is_empty():
    """#791: delete_run's listing/delete prefix must route through
    _key_prefix(), not raw self._prefix directly -- a no-prefix store must
    query "runs/run1/", not "/runs/run1/" (which would list nothing and
    silently delete 0 objects instead of the run's own artifacts)."""
    storage = S3RunStorage(bucket="test-bucket", prefix="")
    stub = Stubber(storage._s3)
    key = "runs/run1/input/cv.docx"
    _stub_listing(stub, [key], prefix="runs/run1/")
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": key}]},
        expected_params=_delete_params([key]),
    )
    with stub:
        assert storage.delete_run("run1") == 1
    stub.assert_no_pending_responses()


def test_delete_global_prefix_uses_key_prefix_when_store_prefix_is_empty():
    """#791: delete_global_prefix must route through _key_prefix(), not raw
    self._prefix directly -- a no-prefix store must query the caller's
    prefix verbatim ("by-submitter/..."), not "/by-submitter/..."."""
    storage = S3RunStorage(bucket="test-bucket", prefix="")
    stub = Stubber(storage._s3)
    key = "by-submitter/e@x.edu/R1/manifest.json"
    _stub_listing(stub, [key], prefix="by-submitter/e@x.edu/R1/")
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": key}]},
        expected_params=_delete_params([key]),
    )
    with stub:
        assert storage.delete_global_prefix("by-submitter/e@x.edu/R1/") == 1
    stub.assert_no_pending_responses()


def test_put_global_writes_without_leading_slash_when_store_prefix_is_empty():
    """#791: put_global must route through _key_prefix(), not raw
    self._prefix directly -- a no-prefix store must write "manifest.json",
    not "/manifest.json"."""
    storage = S3RunStorage(bucket="test-bucket", prefix="")
    stub = Stubber(storage._s3)
    stub.add_response(
        "put_object",
        {},
        expected_params={"Bucket": "test-bucket", "Key": "manifest.json", "Body": b"data"},
    )
    with stub:
        storage.put_global("manifest.json", b"data")
    stub.assert_no_pending_responses()


def test_list_files_strips_run_prefix_when_store_prefix_is_empty():
    """#791: list_files must strip the SAME run-level prefix it queried
    with, via _key_prefix() -- not raw self._prefix directly, which for a
    no-prefix store would build "/runs/run1/" (leading slash), fail to
    match the returned "runs/run1/..." keys, and silently fall through to
    the else branch, returning the full key instead of the relative one."""
    storage = S3RunStorage(bucket="test-bucket", prefix="")
    stub = Stubber(storage._s3)
    _stub_listing(stub, ["runs/run1/input/cv.docx"], prefix="runs/run1/")
    with stub:
        assert storage.list_files("run1") == ["input/cv.docx"]
    stub.assert_no_pending_responses()


def test_client_has_bounded_connect_read_timeout_and_retries():
    """#791: the client must carry explicit, named timeout/retry values
    instead of inheriting boto3's own defaults, so a degraded S3 fails
    within a stated bound instead of compounding silently across the
    upload endpoint's up-to-10 sequential put_object calls.

    Asserts the LITERAL numbers, not the module constants they are read
    from: importing S3_CONNECT_TIMEOUT_S et al. and comparing the built
    client's config back to those same constants would still pass if the
    constants were reset to botocore's own defaults (60s/60s, no bounded
    retries) -- exactly the regression this test exists to catch. Worst
    case at these numbers: 5 run-id attempts x up to 2 put_object calls
    each = 10 sequential calls, x 4 total attempts per call (the initial
    attempt plus 3 retries), x 35s per attempt (5s connect + 30s read) =
    up to 1,400s before the upload endpoint can no longer succeed."""
    config = _storage()._s3.meta.config
    assert config.connect_timeout == 5
    assert config.read_timeout == 30
    assert config.retries["mode"] == "standard"
    # botocore normalizes the "standard" mode's max_attempts (a retry count)
    # into total_max_attempts (max_attempts + 1, the initial attempt
    # included) on the built client's own config -- confirmed empirically
    # against the installed botocore, not assumed.
    assert config.retries["total_max_attempts"] == 4


# ---------------------------------------------------------------------------
# #792: LocalRunStorage.list_files literal-prefix parity with S3
# ---------------------------------------------------------------------------

def test_local_list_files_matches_s3_literal_prefix_semantics(tmp_path):
    """S3's Prefix is a literal byte prefix, not a directory match: a
    request for "input/man" returns only keys whose string starts with
    exactly that, never a sibling directory's "input/deep/manifest.json".
    LocalRunStorage's partial-path fallback used to glob recursively by
    basename and returned that sibling too (#792). Covers the three cases
    #792's acceptance criterion names: an existing-directory prefix, an
    absent prefix, and a partial-path prefix naming no directory."""
    from app.storage.local_storage import LocalRunStorage

    local = LocalRunStorage(base_dir=str(tmp_path))
    local.put_file("run1", "input/deep/manifest.json", b"{}")
    local.put_file("run1", "input/manual.txt", b"x")
    local.put_file("run1", "input/cv.docx", b"y")

    # existing-directory prefix
    assert local.list_files("run1", "input/deep/") == ["input/deep/manifest.json"]
    # absent prefix -- no directory and no file starts with it
    assert local.list_files("run1", "does/not/exist") == []
    # partial-path prefix naming no directory: literal byte-prefix match,
    # the same as S3's Prefix would return (verified against the real
    # S3RunStorage's Prefix-filtered listing behaviour in
    # test_list_files_prefix_filter_is_sent_to_s3 above, which sends the
    # caller's prefix to S3 unmodified for server-side literal matching).
    assert local.list_files("run1", "input/man") == ["input/manual.txt"]


def test_local_and_s3_list_files_agree_on_the_same_layout(tmp_path):
    """#792 acceptance criterion 3: the same logical layout, run through
    both backends, must return the same key set for an existing-directory
    prefix, an absent prefix, and a partial-path prefix -- proven directly
    against LocalRunStorage (real filesystem) and a stubbed S3RunStorage
    (server-side Prefix filtering simulated by the stub), not by asserting
    against each backend's own idea of the answer separately.

    "input" both names a real directory here AND is a literal prefix of a
    top-level sibling key ("input_extra.txt") -- the case the existing-
    directory branch used to miss (#792)."""
    from app.storage.local_storage import LocalRunStorage

    layout = {
        "input/cv.docx": b"cv-bytes",
        "input/deep/manifest.json": b"{}",
        "input_extra.txt": b"sibling-bytes",
        "steps/3a/output.json": b"[]",
    }

    local = LocalRunStorage(base_dir=str(tmp_path))
    for key, data in layout.items():
        local.put_file("run1", key, data)

    cases = (
        ("input", ["input/cv.docx", "input/deep/manifest.json", "input_extra.txt"]),
        ("does/not/exist", []),
        ("steps/3a/", ["steps/3a/output.json"]),
    )
    for prefix, expected in cases:
        assert sorted(local.list_files("run1", prefix)) == sorted(expected)

    s3 = _storage()
    stub = Stubber(s3._s3)
    for prefix, expected in cases:
        _stub_listing(stub, [RUN_PREFIX + k for k in expected], prefix=RUN_PREFIX + prefix)
    with stub:
        for prefix, expected in cases:
            assert sorted(s3.list_files("run1", prefix)) == sorted(expected)
    stub.assert_no_pending_responses()
