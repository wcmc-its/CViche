"""Tests for app/services/pdf_sandbox.py (#806): every PDF parse runs in a
limited child process.

AES fixtures are hand-built (no PDF writer is a dependency) with the
`cryptography` package the backend already pins: the standard security
handler at revision 4 (AESV2, AES-128) and revision 6 (AESV3, AES-256).
`test_aes_fixtures_are_real` proves each one decrypts under pdfminer with
its password, so the encryption tests are not passing on a malformed file.
"""
import hashlib
import os
import struct
import sys
from collections.abc import Callable

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.services import pdf_sandbox
from app.services.pdf_sandbox import (
    PDF_MAX_PAGES,
    EncryptedPdfError,
    PdfBusyError,
    PdfTooComplexError,
    UnreadablePdfError,
    convert_pdf,
    extract_pdf_text,
    read_pdf,
)

_TEXT = "Professor of Medicine, Example University"
_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
_DOC_ID = b"0123456789abcdef"
_PERMS = -4


def _aes_cbc(key: bytes, data: bytes) -> bytes:
    """AES-CBC with a random IV prepended and PKCS#7 padding (PDF strings/streams)."""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return iv + enc.update(padder.update(data) + padder.finalize()) + enc.finalize()


def _aes_raw(key: bytes, data: bytes, iv: bytes = b"\0" * 16) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(data) + enc.finalize()


def _rc4(key: bytes, data: bytes) -> bytes:
    box, j = list(range(256)), 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) % 256
        box[i], box[j] = box[j], box[i]
    out, i, j = bytearray(), 0, 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + box[i]) % 256
        box[i], box[j] = box[j], box[i]
        out.append(byte ^ box[(box[i] + box[j]) % 256])
    return bytes(out)


def _r4(user_pw: str) -> tuple[bytes, Callable[[int, bytes], bytes]]:
    """Revision 4 / AESV2: the /Encrypt body and the per-object stream encryptor."""
    pad = lambda pw: (pw.encode() + _PAD)[:32]  # noqa: E731
    okey = hashlib.md5(pad("owner-pw")).digest()
    for _ in range(50):
        okey = hashlib.md5(okey).digest()
    o = pad(user_pw)
    for i in range(20):
        o = _rc4(bytes(b ^ i for b in okey), o)
    key = hashlib.md5(pad(user_pw) + o + struct.pack("<i", _PERMS) + _DOC_ID).digest()
    for _ in range(50):
        key = hashlib.md5(key).digest()
    u = hashlib.md5(_PAD + _DOC_ID).digest()
    for i in range(20):
        u = _rc4(bytes(b ^ i for b in key), u)
    u += b"\0" * 16
    body = (f"<< /Filter /Standard /V 4 /R 4 /Length 128 /P {_PERMS} "
            f"/CF << /StdCF << /CFM /AESV2 /AuthEvent /DocOpen /Length 16 >> >> "
            f"/StmF /StdCF /StrF /StdCF /O <{o.hex()}> /U <{u.hex()}> >>").encode()

    def encrypt(objnum: int, data: bytes) -> bytes:
        k = hashlib.md5(key + struct.pack("<i", objnum)[:3] + b"\0\0" + b"sAlT").digest()
        return _aes_cbc(k, data)
    return body, encrypt


def _hash_2b(pw: bytes, salt: bytes, udata: bytes = b"") -> bytes:
    """ISO 32000-2 algorithm 2.B (revision 6 password hash)."""
    k = hashlib.sha256(pw + salt + udata).digest()
    i = 0
    while True:
        e = _aes_raw(k[:16], (pw + k + udata) * 64, iv=k[16:32])
        k = (hashlib.sha256, hashlib.sha384, hashlib.sha512)[sum(e[:16]) % 3](e).digest()
        i += 1
        if i >= 64 and e[-1] <= i - 32:
            return k[:32]


def _r6(user_pw: str) -> tuple[bytes, Callable[[int, bytes], bytes]]:
    """Revision 6 / AESV3: the /Encrypt body and the stream encryptor."""
    key = os.urandom(32)
    upw, opw = user_pw.encode(), b"owner-pw"
    uvs, uks, ovs, oks = (os.urandom(8) for _ in range(4))
    u = _hash_2b(upw, uvs) + uvs + uks
    ue = _aes_raw(_hash_2b(upw, uks), key)
    o = _hash_2b(opw, ovs, u) + ovs + oks
    oe = _aes_raw(_hash_2b(opw, oks, u), key)
    perms = Cipher(algorithms.AES(key), modes.ECB()).encryptor().update(
        struct.pack("<i", _PERMS) + b"\xff\xff\xff\xffTadb" + os.urandom(4))
    body = (f"<< /Filter /Standard /V 5 /R 6 /Length 256 /P {_PERMS} "
            f"/CF << /StdCF << /CFM /AESV3 /AuthEvent /DocOpen /Length 32 >> >> "
            f"/StmF /StdCF /StrF /StdCF /O <{o.hex()}> /U <{u.hex()}> "
            f"/OE <{oe.hex()}> /UE <{ue.hex()}> /Perms <{perms.hex()}> >>").encode()
    return body, lambda objnum, data: _aes_cbc(key, data)


def _aes_pdf(revision: int, user_pw: str) -> bytes:
    """One page of `_TEXT`, its content stream AES-encrypted."""
    body, encrypt = (_r4 if revision == 4 else _r6)(user_pw)
    stream = encrypt(4, f"BT /F1 12 Tf 72 720 Td ({_TEXT}) Tj ET".encode())
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"),
        4: b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        6: body,
    }
    out = bytearray(b"%PDF-1.7\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 7\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % offsets[n] for n in sorted(objs))
    out += (b"trailer\n<< /Size 7 /Root 1 0 R /Encrypt 6 0 R /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n"
            % (_DOC_ID.hex().encode(), _DOC_ID.hex().encode(), xref))
    return bytes(out)


def _pdfminer_text(content: bytes, password: str) -> str:
    import io

    import pdfplumber
    with pdfplumber.open(io.BytesIO(content), password=password) as pdf:
        return pdf.pages[0].extract_text()


@pytest.mark.parametrize("revision", [4, 6])
def test_aes_fixtures_are_real(revision):
    assert _pdfminer_text(_aes_pdf(revision, "secret"), "secret") == _TEXT


@pytest.mark.parametrize("revision", [4, 6])
def test_aes_pdf_with_a_user_password_is_encrypted(revision):
    with pytest.raises(EncryptedPdfError):
        extract_pdf_text(_aes_pdf(revision, "secret"))


def test_rc4_pdf_with_a_user_password_is_encrypted(cv_pdf):
    with pytest.raises(EncryptedPdfError):
        extract_pdf_text(cv_pdf(user_password="secret"))


@pytest.mark.parametrize("revision", [4, 6])
def test_aes_pdf_openable_without_a_password_is_read(revision):
    """Owner-password-only encryption (edit restrictions) opens with the
    empty user password, so it is a readable PDF, not a password 400."""
    assert extract_pdf_text(_aes_pdf(revision, "")) == _TEXT


def test_text_of_a_plain_pdf(cv_pdf):
    text = extract_pdf_text(cv_pdf())
    assert text.startswith("EDUCATION") and "Example University" in text


def test_page_cap_is_inclusive():
    from unified_pipeline.tests.test_pdf_to_docx import _make_pdf
    page = [(False, 10, 72, 700, "A page.")]
    assert extract_pdf_text(_make_pdf([page] * PDF_MAX_PAGES)).count("A page.") == PDF_MAX_PAGES
    with pytest.raises(PdfTooComplexError, match="more than 300 pages"):
        extract_pdf_text(_make_pdf([page] * (PDF_MAX_PAGES + 1)))


def test_page_cap_is_read_from_raw_bytes_before_pdfminer():
    """pdfminer parses an array in quadratic time: reading a flat
    20,000-entry /Kids array alone takes ~60 s, past the 30 s limit. The
    raw-byte scan must reject it first, by its declared count -- reaching
    pdfminer would surface as "exceeded" instead."""
    from unified_pipeline.tests.test_pdf_to_docx import _make_pdf
    with pytest.raises(PdfTooComplexError, match=r"more than 300 pages \(found 20000\)"):
        extract_pdf_text(_make_pdf([[]] * 20000))


@pytest.mark.parametrize("node, pages", [
    (b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 512 >>", 512),
    (b"<< /Count 77 /Kids [3 0 R] /Type /Pages >>", 77),
    (b"<< /Type /Outlines /Count 900 >>", 0),  # bookmarks, not pages
])
def test_raw_page_count_scan(tmp_path, node, pages):
    f = tmp_path / "n.pdf"
    f.write_bytes(b"%PDF-1.4\n1 0 obj\n" + node + b"\nendobj\n")
    assert pdf_sandbox._raw_declared_pages(str(f)) == pages


def test_a_wrapped_memory_error_is_a_limit_not_unreadable():
    """pdfplumber wraps a decompression bomb's MemoryError in its
    PdfminerException; reading that as "unreadable" would fail open."""
    from pdfplumber.utils.exceptions import PdfminerException
    assert pdf_sandbox._wraps_memory_error(PdfminerException(MemoryError("x")))
    assert not pdf_sandbox._wraps_memory_error(PdfminerException(ValueError("x")))


def test_page_cap_is_rechecked_after_pdfminer_opens_the_document():
    """A page tree the raw scan cannot see (here: a nested dictionary inside
    the /Pages node; in the wild, a compressed object stream) is still
    capped, by the count pdfminer resolves."""
    from unified_pipeline.tests.test_pdf_to_docx import _make_pdf
    content = _make_pdf([[(False, 10, 72, 700, "A page.")]] * (PDF_MAX_PAGES + 1)).replace(
        b"<< /Type /Pages /Kids", b"<< /Type /Pages /X << >> /Kids", 1)
    with pytest.raises(PdfTooComplexError, match=r"more than 300 pages \(found 301\)"):
        extract_pdf_text(content)


@pytest.mark.parametrize("child", [
    "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",  # the kernel's OOM kill
    f"import os; os._exit({pdf_sandbox._EXIT_OUT_OF_MEMORY})",  # a MemoryError the child caught
])
def test_child_death_by_memory_is_a_limit(monkeypatch, child):
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP", child)
    with pytest.raises(PdfTooComplexError, match="child exited"):
        pdf_sandbox.run_pdf_job("text", ["/nonexistent.pdf"], 30)


def test_wrapped_memory_error_in_the_child_is_a_limit(monkeypatch, cv_pdf):
    """End to end through the real child and reply protocol: pdfplumber
    surfaces a bomb's MemoryError wrapped in PdfminerException (measured on
    Linux: "Unable to allocate output buffer"); it must come back as a
    limit, not as "unreadable" (which the upload treats as fail-open)."""
    child = (
        "import sys; sys.path[:0] = sys.argv[1:3]\n"
        "from app.services import pdf_sandbox as s\n"
        "from pdfplumber.utils.exceptions import PdfminerException\n"
        "def boom(path): raise PdfminerException(MemoryError('Unable to allocate output buffer.'))\n"
        "s._page_texts = boom\n"
        "s._child_main()"
    )
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP", child)
    with pytest.raises(PdfTooComplexError, match="ran out of memory"):
        extract_pdf_text(cv_pdf())


def test_page_cap_applies_to_conversion(tmp_path):
    from unified_pipeline.tests.test_pdf_to_docx import _make_pdf
    src = tmp_path / "long.pdf"
    src.write_bytes(_make_pdf([[(False, 10, 72, 700, "A page.")]] * (PDF_MAX_PAGES + 1)))
    with pytest.raises(PdfTooComplexError):
        convert_pdf(src, tmp_path / "long.docx")
    assert not (tmp_path / "long.docx").exists()


def test_timeout_kills_the_child(cv_pdf, monkeypatch):
    monkeypatch.setattr(pdf_sandbox, "PDF_TEXT_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(PdfTooComplexError, match="exceeded"):
        extract_pdf_text(cv_pdf())


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux only (every pod)")
def test_flate_bomb_hits_the_memory_cap():
    """1 GiB of zeros compresses to ~1 MB: decompressing it in the child
    exceeds PDF_CHILD_ADDRESS_SPACE_BYTES, which must surface as a limit,
    not a crash or a fail-open."""
    import zlib
    # Compressed a megabyte at a time: building the 1 GiB in the test process
    # itself OOM-killed it in a 2 GiB container.
    z = zlib.compressobj(9)
    chunk = b" " * (1 << 20)
    data = (z.compress(b"BT /F1 12 Tf 72 720 Td (x) Tj ET")
            + b"".join(z.compress(chunk) for _ in range(1024)) + z.flush())
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 5\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref
    with pytest.raises(PdfTooComplexError):
        extract_pdf_text(bytes(out))


def test_unparseable_pdf_is_unreadable():
    with pytest.raises(UnreadablePdfError):
        extract_pdf_text(b"%PDF-1.4 not really a pdf")


def test_read_pdf_reports_image_only_pages(cv_pdf):
    """#1282: the upload's read counts pages and names the image-only ones
    by the converter's rule, so the gate and the run agree."""
    pdf = read_pdf(cv_pdf(image_pages=(2,), text_pages=2))
    assert (pdf.pages, pdf.image_only_pages) == (3, [3])
    assert "EDUCATION" in pdf.text


@pytest.mark.parametrize("value", ['"text"', "{}", '{"text": "t", "pages": 1, "image_only_pages": ["1"]}'])
def test_a_wrong_shaped_text_reply_is_unreadable(monkeypatch, cv_pdf, value):
    reply = f'["ok", {value}]'
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP", f"import sys; sys.stdout.write({reply!r})")
    with pytest.raises(UnreadablePdfError):
        read_pdf(cv_pdf())


def test_conversion_reports_image_only_pages(cv_pdf, tmp_path):
    src = tmp_path / "scan.pdf"
    src.write_bytes(cv_pdf(image_pages=(1,)))
    assert convert_pdf(src, tmp_path / "scan.docx").image_only_pages == [2]
    assert (tmp_path / "scan.docx").is_file()


def test_child_failure_is_not_swallowed(monkeypatch):
    """A child that crashes for any reason other than a limit (here: an
    unknown op) is a RuntimeError, never a pass or a fail-open."""
    with pytest.raises(RuntimeError, match="exit code"):
        pdf_sandbox.run_pdf_job("no-such-op", ["/nonexistent.pdf"], 30)


# --- concurrency slots (#806 review B1) -----------------------------------


@pytest.fixture
def both_slots_held():
    """Take every PDF child slot for the duration of a test."""
    for _ in range(pdf_sandbox.PDF_CHILD_SLOTS):
        assert pdf_sandbox._slots.acquire(blocking=False)
    yield
    for _ in range(pdf_sandbox.PDF_CHILD_SLOTS):
        pdf_sandbox._slots.release()


def _no_spawn(*args, **kwargs):
    raise AssertionError("a PDF child was spawned with every slot taken")


def test_request_path_does_not_wait_for_a_slot(both_slots_held, monkeypatch, cv_pdf):
    import time
    monkeypatch.setattr(pdf_sandbox.subprocess, "run", _no_spawn)
    start = time.monotonic()
    with pytest.raises(PdfBusyError):
        extract_pdf_text(cv_pdf())
    assert time.monotonic() - start < 0.5  # refused at once, not after a wait


def test_conversion_waits_for_a_slot_then_gives_up(both_slots_held, monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_sandbox.subprocess, "run", _no_spawn)
    monkeypatch.setattr(pdf_sandbox, "PDF_CONVERT_SLOT_WAIT_SECONDS", 0.2)
    with pytest.raises(PdfBusyError):
        convert_pdf(tmp_path / "a.pdf", tmp_path / "a.docx")


def test_conversion_takes_a_slot_freed_while_it_waits(monkeypatch, cv_pdf, tmp_path):
    import threading
    src = tmp_path / "cv.pdf"
    src.write_bytes(cv_pdf())
    for _ in range(pdf_sandbox.PDF_CHILD_SLOTS):
        pdf_sandbox._slots.acquire()
    threading.Timer(0.3, pdf_sandbox._slots.release).start()
    try:
        assert convert_pdf(src, tmp_path / "cv.docx").image_only_pages == []
    finally:
        for _ in range(pdf_sandbox.PDF_CHILD_SLOTS - 1):
            pdf_sandbox._slots.release()


@pytest.mark.parametrize("child", [
    "import time; time.sleep(5)",  # timed out and killed
    "import sys; sys.exit(1)",     # failed
    "print('garbage')",            # unreadable reply
])
def test_every_exit_path_releases_its_slot(monkeypatch, child):
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP", child)
    with pytest.raises(Exception):
        pdf_sandbox.run_pdf_job("text", ["/x.pdf"], 0.5)
    for _ in range(pdf_sandbox.PDF_CHILD_SLOTS):  # all free again
        assert pdf_sandbox._slots.acquire(blocking=False)
    for _ in range(pdf_sandbox.PDF_CHILD_SLOTS):
        pdf_sandbox._slots.release()


# --- every parse failure is a known error, never a 500 (#806 review N1) ----


def test_a_5000_digit_page_count_is_refused():
    """The raw scan used to int() it outside any handler and hit Python's
    int-digit limit; a raw-scan-invisible copy reaches pdfminer's own int()."""
    from unified_pipeline.tests.test_pdf_to_docx import _make_pdf
    huge = b"/Count " + b"9" * 5000
    visible = _make_pdf([[]]).replace(b"/Count 1", huge, 1)
    with pytest.raises(PdfTooComplexError):
        extract_pdf_text(visible)
    hidden = visible.replace(b"<< /Type /Pages /Kids", b"<< /Type /Pages /X << >> /Kids", 1)
    with pytest.raises((PdfTooComplexError, UnreadablePdfError)):
        extract_pdf_text(hidden)


def test_a_page_without_a_mediabox_is_unreadable(cv_pdf):
    """pdfplumber raises an unwrapped TypeError here, not its own type."""
    content = cv_pdf().replace(b"/MediaBox [0 0 612 792] ", b"", 1)
    with pytest.raises(UnreadablePdfError, match="TypeError"):
        extract_pdf_text(content)


@pytest.mark.parametrize("stdout", ["", "garbage", "[1, 2, 3]", '{"a": 1}', '"ok"', '["ok", 5]', '["weird", 1]'])
def test_an_unreadable_child_reply_is_unreadable(monkeypatch, cv_pdf, stdout):
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP", f"import sys; sys.stdout.write({stdout!r})")
    with pytest.raises(UnreadablePdfError):
        extract_pdf_text(cv_pdf())


@pytest.mark.parametrize("value", ["5", "{}", '{"image_only_pages": "x"}', '{"image_only_pages": ["1"]}'])
def test_a_wrong_shaped_conversion_reply_is_unreadable(monkeypatch, tmp_path, value):
    reply = f'["ok", {value}]'
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP", f"import sys; sys.stdout.write({reply!r})")
    with pytest.raises(UnreadablePdfError):
        convert_pdf(tmp_path / "a.pdf", tmp_path / "a.docx")


# --- a page tree that lies about its size (#806 review N2) -----------------


def _lying_tree_pdf(nodes: int, per_node: int) -> bytes:
    """Root /Count 1 over `nodes` intermediate nodes, each also /Count 1,
    each holding `per_node` real pages: no /Count the raw scan sees is big."""
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
            3: b"<< /Length 0 >>\nstream\n\nendstream"}
    num, inter = 4, []
    for _ in range(nodes):
        node, num = num, num + 1
        kids = []
        for _ in range(per_node):
            objs[num] = (b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                         b"/Contents 3 0 R >>" % node)
            kids.append(b"%d 0 R" % num)
            num += 1
        objs[node] = b"<< /Type /Pages /Parent 2 0 R /Count 1 /Kids [" + b" ".join(kids) + b"] >>"
        inter.append(b"%d 0 R" % node)
    objs[2] = b"<< /Type /Pages /Count 1 /Kids [" + b" ".join(inter) + b"] >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), {}
    for n in sorted(objs):
        offsets[n] = len(out)
        out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % offsets[n] for n in sorted(objs))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_a_page_tree_that_understates_its_count_is_still_capped():
    content = _lying_tree_pdf(nodes=100, per_node=200)  # 20,000 pages, every /Count 1
    assert pdf_sandbox._PAGES_COUNT_RE.findall(content)  # the scan sees only /Count 1s
    with pytest.raises(PdfTooComplexError, match=r"more than 300 pages \(found 301\)"):
        extract_pdf_text(content)


# --- environment and rlimit (#806 review N3, N5) ---------------------------


def test_child_gets_no_credentials(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak")
    monkeypatch.setenv("DB_PASSWORD", "leak")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP",
                        "import json, os, sys; sys.stdout.write(json.dumps(['ok', sorted(os.environ)]))")
    env = pdf_sandbox.run_pdf_job("text", ["/x.pdf"], 30)
    assert "AWS_SECRET_ACCESS_KEY" not in env and "DB_PASSWORD" not in env
    assert "PATH" in env and "LC_ALL" in env


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux only (every pod)")
def test_rlimit_that_cannot_be_applied_on_linux_is_fatal(monkeypatch, cv_pdf):
    """Lowering the child's hard RLIMIT_AS below the cap first makes the
    child's own setrlimit raise; on Linux that must stop the child (a
    deployment fault, a 500), never parse unbounded."""
    child = ("import resource, sys; resource.setrlimit(resource.RLIMIT_AS, (200 << 20, 200 << 20)); "
             "sys.path[:0] = sys.argv[1:3]; "
             "from app.services.pdf_sandbox import _child_main; _child_main()")
    monkeypatch.setattr(pdf_sandbox, "_CHILD_BOOTSTRAP", child)
    with pytest.raises(RuntimeError, match="exit code 1"):
        extract_pdf_text(cv_pdf())
