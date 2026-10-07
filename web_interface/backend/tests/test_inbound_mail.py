"""app/services/inbound_mail.py (#1298): the outer-message trust headers and
the attachment walk. Messages are built here from stdlib ``email`` with
invented example.org / med.cornell.edu addresses -- no real names."""
from email.message import EmailMessage

import pytest

from app.services import inbound_mail
from app.services.inbound_mail import (
    AttachmentReject,
    UnparseableMessage,
    is_wcm_address,
    parse_message,
)

SES_PASS = "amazonses.com; spf=pass smtp.mailfrom=med.cornell.edu; dkim=pass; dmarc=pass header.from=med.cornell.edu"
SES_FAIL = "amazonses.com; spf=pass; dkim=none; dmarc=fail header.from=med.cornell.edu"
DOCX = b"PK\x03\x04 synthetic docx bytes"
PDF = b"%PDF-1.4 synthetic pdf bytes"
OCTET = ("application", "octet-stream")
VIRUS = "X-SES-Virus-Verdict"
SPAM = "X-SES-Spam-Verdict"
# What SES adds when the receipt rule scans and finds nothing; a header passed
# in ``headers`` overrides it, and ``None`` leaves it out.
DEFAULT_SES_HEADERS = {VIRUS: "PASS"}


def make_eml(*, auth=(SES_PASS,), from_addr="Pat Example <pat@med.cornell.edu>", headers=None,
             attachments=(), inline_images=0, body="Please see attached.") -> bytes:
    msg = EmailMessage()
    for value in auth:  # Authentication-Results first, as SES prepends it
        msg["Authentication-Results"] = value
    if from_addr is not None:
        msg["From"] = from_addr
    msg["To"] = "cv@mail.example.org"
    msg["Subject"] = "FW: synthetic"
    msg["Message-ID"] = "<synthetic-1@example.org>"
    for key, value in {**DEFAULT_SES_HEADERS, **(headers or {})}.items():
        if value is not None:
            msg[key] = value
    msg.set_content(body)
    for _ in range(inline_images):
        msg.add_attachment(b"\x89PNG synthetic", maintype="image", subtype="png", disposition="inline")
    for filename, content in attachments:
        msg.add_attachment(content, maintype=OCTET[0], subtype=OCTET[1], filename=filename)
    return msg.as_bytes()


def test_dmarc_pass_authenticates():
    assert parse_message(make_eml()).authenticated is True


@pytest.mark.parametrize("auth", [(SES_FAIL,), ()], ids=["dmarc_fail", "header_missing"])
def test_dmarc_fail_or_missing_does_not_authenticate(auth):
    assert parse_message(make_eml(auth=auth)).authenticated is False


def test_only_the_first_authentication_results_header_counts():
    """A sender-planted lower header saying dmarc=pass cannot rescue SES's own failure."""
    spoofed = "mx.example.org; dmarc=pass"
    assert parse_message(make_eml(auth=(SES_FAIL, spoofed))).authenticated is False


def test_a_first_header_that_is_not_ses_does_not_count():
    assert parse_message(make_eml(auth=("mx.example.org; dmarc=pass", SES_PASS))).authenticated is False


@pytest.mark.parametrize("header", [SPAM, VIRUS])
def test_ses_fail_verdicts_flag_the_message(header):
    assert parse_message(make_eml(headers={header: "FAIL"})).spam_or_virus is True
    assert parse_message(make_eml(headers={header: "PASS"})).spam_or_virus is False


@pytest.mark.parametrize("verdict", ["PASS", " pass "], ids=["upper", "lower_padded"])
def test_virus_pass_is_accepted(verdict):
    assert parse_message(make_eml(headers={VIRUS: verdict})).spam_or_virus is False


@pytest.mark.parametrize("verdict", [None, "", "GRAY", "PROCESSING_FAILED", "FAIL"],
                         ids=["header_missing", "empty", "gray", "processing_failed", "fail"])
def test_virus_verdict_other_than_pass_fails_closed(verdict):
    """#1332: no header means the receipt rule never scanned (no ScanEnabled)."""
    assert parse_message(make_eml(headers={VIRUS: verdict})).spam_or_virus is True


@pytest.mark.parametrize("spam, expected", [("GRAY", False), ("FAIL", True)])
def test_spam_rejects_on_fail_only_when_the_virus_scan_passed(spam, expected):
    assert parse_message(make_eml(headers={SPAM: spam, VIRUS: "PASS"})).spam_or_virus is expected


def test_the_inner_message_virus_verdict_is_ignored():
    """SES scans the whole raw message, so only the outer verdict counts."""
    inner = make_eml(attachments=[("inner.docx", DOCX)], auth=(), headers={VIRUS: "PASS"})
    parsed = parse_message(make_eml(headers={VIRUS: None}, attachments=[("forwarded.eml", inner)]))
    assert parsed.spam_or_virus is True


def test_sender_is_the_single_lowercased_address():
    assert parse_message(make_eml(from_addr="Pat Example <PAT@Med.Cornell.edu>")).from_addr == "pat@med.cornell.edu"
    assert parse_message(make_eml(from_addr="a@med.cornell.edu, b@med.cornell.edu")).from_addr is None


@pytest.mark.parametrize("addr, expected", [
    ("pat@med.cornell.edu", True), ("pat@dept.med.cornell.edu", True), ("pat@nyp.org", True),
    ("pat@example.org", False), ("pat@evilmed.cornell.edu", False), ("pat@med.cornell.edu.example.org", False),
])
def test_is_wcm_address(addr, expected):
    assert is_wcm_address(addr) is expected


def test_docx_and_pdf_attachments_are_collected():
    parsed = parse_message(make_eml(attachments=[("a.docx", DOCX), ("b.pdf", PDF), ("c.DOCX", DOCX)]))
    assert [a.filename for a in parsed.attachments] == ["a.docx", "b.pdf", "c.DOCX"]
    assert not parsed.rejected


def test_attached_eml_is_opened_one_level():
    inner = make_eml(attachments=[("inner.docx", DOCX)], auth=())
    parsed = parse_message(make_eml(attachments=[("forwarded.eml", inner)]))
    assert [a.filename for a in parsed.attachments] == ["inner.docx"]


def test_message_rfc822_part_is_opened_one_level():
    outer = EmailMessage()
    outer["Authentication-Results"] = SES_PASS
    outer["From"] = "pat@med.cornell.edu"
    outer.set_content("fwd")
    inner = EmailMessage()
    inner["From"] = "someone@example.org"
    inner.set_content("inner body")
    inner.add_attachment(PDF, maintype="application", subtype="pdf", filename="inner.pdf")
    outer.add_attachment(inner)
    parsed = parse_message(outer.as_bytes())
    assert [a.filename for a in parsed.attachments] == ["inner.pdf"]


def test_the_inner_message_authentication_header_and_sender_are_ignored():
    inner = make_eml(attachments=[("inner.docx", DOCX)], auth=(SES_PASS,), from_addr="boss@med.cornell.edu")
    parsed = parse_message(make_eml(auth=(SES_FAIL,), attachments=[("forwarded.eml", inner)]))
    assert parsed.authenticated is False
    assert parsed.from_addr == "pat@med.cornell.edu"


def test_an_eml_inside_an_eml_inside_the_message_is_rejected_as_too_deep():
    deepest = make_eml(attachments=[("deep.docx", DOCX)], auth=())
    middle = make_eml(attachments=[("deepest.eml", deepest)], auth=())
    parsed = parse_message(make_eml(attachments=[("middle.eml", middle)]))
    assert parsed.attachments == []
    assert parsed.rejected[AttachmentReject.NESTED_TOO_DEEP] == 1


def test_inline_images_and_body_text_are_ignored_not_rejected():
    parsed = parse_message(make_eml(inline_images=2, attachments=[("cv.docx", DOCX)]))
    assert len(parsed.attachments) == 1
    assert not parsed.rejected


def test_doc_and_other_types_are_rejected_with_a_reason():
    parsed = parse_message(make_eml(attachments=[("old.doc", b"\xd0\xcf\x11\xe0"), ("x.zip", b"PK"), ("ok.pdf", PDF)]))
    assert [a.filename for a in parsed.attachments] == ["ok.pdf"]
    assert parsed.rejected[AttachmentReject.UNSUPPORTED_TYPE] == 2


def test_magic_bytes_must_match_the_extension():
    parsed = parse_message(make_eml(attachments=[("fake.pdf", b"not a pdf"), ("fake.docx", b"%PDF-1.4")]))
    assert parsed.attachments == []
    assert parsed.rejected[AttachmentReject.NOT_A_REAL_FILE] == 2


def test_oversize_attachment_is_rejected(monkeypatch):
    monkeypatch.setattr(inbound_mail, "MAX_UPLOAD_SIZE", 10)
    parsed = parse_message(make_eml(attachments=[("big.pdf", PDF)]))
    assert parsed.attachments == []
    assert parsed.rejected[AttachmentReject.TOO_LARGE] == 1


def test_path_and_control_characters_are_stripped_from_the_filename():
    parsed = parse_message(make_eml(attachments=[("..\\dir/cv.docx", DOCX)]))
    assert parsed.attachments[0].filename == "cv.docx"


def test_bytes_with_no_headers_are_unparseable():
    with pytest.raises(UnparseableMessage):
        parse_message(b"")
