"""app/services/mailer.py (#1298): off by default, counts-only bodies, no
filename ever, header values on one line. SES is never called: boto3.client
is patched. Addresses are invented."""
import logging
from email import message_from_bytes, policy
from unittest.mock import MagicMock

import pytest

from app.models import InboundRejectReason
from app.services import email_templates as templates
from app.services import mailer


@pytest.fixture
def ses(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(mailer, "_client", lambda: client)
    return client


def test_send_is_off_by_default_and_logs_only_the_kind(ses, monkeypatch, caplog):
    monkeypatch.delenv("CVICHE_MAIL_SEND", raising=False)
    with caplog.at_level(logging.INFO, logger="app.services.mailer"):
        sent = mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=2, held=0, batch_id="BATCHA"))
    assert sent is False
    ses.send_email.assert_not_called()
    assert "would have sent" in caplog.text
    assert "pat@med.cornell.edu" not in caplog.text and "We received" not in caplog.text


def test_send_calls_ses_when_the_flag_is_on(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "true")
    monkeypatch.setenv("CVICHE_MAIL_FROM", "no-reply@mail.example.org")
    assert mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=2, held=0, batch_id="BATCHA")) is True
    kwargs = ses.send_email.call_args.kwargs
    assert kwargs["FromEmailAddress"] == "no-reply@mail.example.org"
    assert kwargs["Destination"] == {"ToAddresses": ["pat@med.cornell.edu"]}


def test_default_sender_is_the_cviche_no_reply_address(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    monkeypatch.delenv("CVICHE_MAIL_FROM", raising=False)
    mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=1, held=0, batch_id="BATCHA"))
    assert ses.send_email.call_args.kwargs["FromEmailAddress"] == "no-reply@cviche.weill.cornell.edu"


def _notice(**kwargs):
    kwargs.setdefault("batch_id", "BATCHA")
    return mailer.processing_notice("pat@med.cornell.edu", **kwargs).body


def test_notice_for_runs_only_links_to_the_batch(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org/")
    body = _notice(runs=3, held=0)
    assert "Processing 3 CVs" in body and "https://cviche.example.org/runs?batch=BATCHA" in body
    assert "waiting" not in body


def test_notice_with_held_files_names_new_run_and_keeps_one_button(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    body = _notice(runs=2, held=1)
    assert "Processing 2 CVs" in body and "1 CV is waiting for your confirmation" in body
    assert "View your CVs: https://cviche.example.org/runs?batch=BATCHA" in body
    assert body.count("https://cviche.example.org/runs") == 1  # one button; no inline duplicate link


def test_notice_when_everything_is_held():
    body = _notice(runs=0, held=4, batch_id=None)
    assert "Processing" not in body and "4 CVs are waiting for your confirmation" in body


def test_notice_for_outdated_consent_says_to_sign_in(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    body = _notice(runs=0, held=3, batch_id=None, outdated_consent=True)
    assert "terms have been updated" in body and "sign in to review and accept them" in body
    assert "https://cviche.example.org/consent" in body and "3 CVs are waiting" in body
    assert "agreed to" not in body
    assert "Processing" not in body


@pytest.mark.parametrize("kwargs", [
    {"runs": 2, "held": 0}, {"runs": 2, "held": 1}, {"runs": 0, "held": 4}, {"runs": 1, "held": 0, "skipped": 2},
], ids=["runs", "runs_and_held", "all_held", "skipped"])
def test_every_accepted_notice_cites_the_agreed_terms_with_the_consent_date(monkeypatch, kwargs):
    from datetime import datetime
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    body = _notice(consent_date=datetime(2026, 9, 3, 14, 5), **kwargs)
    assert ("These CVs are processed under the CViche terms you agreed to on September 3, 2026: "
            "https://cviche.example.org/help#data-retention") in body


def test_the_terms_sentence_omits_the_date_when_none_is_stored(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    body = _notice(runs=1, held=0, consent_date=None)
    assert "terms you agreed to: https://cviche.example.org/help#data-retention" in body
    assert " on " not in body.split("agreed to")[1].split(":")[0]


def test_notices_never_carry_a_filename():
    for kwargs in ({"runs": 2, "held": 1}, {"runs": 0, "held": 2, "outdated_consent": True}, {"runs": 1, "held": 0, "skipped": 2}):
        body = _notice(**kwargs)
        assert ".docx" not in body and ".pdf" not in body


def test_header_values_lose_cr_and_lf(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    mailer.send(mailer.OutboundMail(
        mailer.MailKind.REJECTION, "pat@med.cornell.edu\r\nBcc: x@example.org", "Hi\nthere\r\n", "body"))
    kwargs = ses.send_email.call_args.kwargs
    assert "\n" not in kwargs["Destination"]["ToAddresses"][0] and "\r" not in kwargs["Destination"]["ToAddresses"][0]
    sent = _parse(kwargs)
    assert sent["Subject"] == "Hi there" and sent["Bcc"] is None


def test_a_ses_error_is_swallowed(ses, monkeypatch):
    from botocore.exceptions import ClientError
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    ses.send_email.side_effect = ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")
    assert mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=1, held=0, batch_id="BATCHA")) is False


@pytest.mark.parametrize("reason", sorted(mailer.REPLYABLE_REASONS))
def test_every_rejection_reply_has_fixed_wording(reason):
    assert mailer.rejection("pat@med.cornell.edu", reason).body.split("\n\n")[1].startswith("We couldn't accept your email")


def test_unknown_or_unauthenticated_reasons_are_never_replyable():
    for reason in (InboundRejectReason.NOT_AUTHENTICATED, InboundRejectReason.UNKNOWN_USER,
                   InboundRejectReason.SENDER_DOMAIN, InboundRejectReason.SPAM_OR_VIRUS):
        assert reason not in mailer.REPLYABLE_REASONS


def _parse(send_kwargs):
    return message_from_bytes(send_kwargs["Content"]["Raw"]["Data"], policy=policy.default)


def _html(message):
    return message.get_body(preferencelist=("html",)).get_content()


def _text(message):
    return message.get_body(preferencelist=("plain",)).get_content()


def _all_mails(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    return [
        mailer.processing_notice("pat@med.cornell.edu", runs=2, held=1, batch_id="BATCHA", skipped=1),
        mailer.processing_notice("pat@med.cornell.edu", runs=0, held=2, outdated_consent=True),
        mailer.rejection("pat@med.cornell.edu", InboundRejectReason.NO_VALID_ATTACHMENTS),
        mailer.completion_notice("pat@med.cornell.edu", complete=2, failed=1, batch_id="BATCHA"),
        mailer.completion_notice("pat@med.cornell.edu", complete=1, failed=0, batch_id="BATCHA", single_run_id="RUNAAA"),
    ]


def _images(message):
    return {p["Content-ID"]: p for p in message.walk() if p.get_content_type() == "image/png"}


@pytest.mark.parametrize("branding,expected", [
    (templates.Branding.ITS_TOP_CVICHE_BOTTOM, {"<wcm-its-logo>", "<cviche-logo>"}),
    (templates.Branding.BOTH_TOP, {"<wcm-its-logo>", "<cviche-logo>"}),
    (templates.Branding.CVICHE_ONLY, {"<cviche-logo>"}),
])
def test_each_branding_attaches_exactly_the_images_it_references(ses, monkeypatch, branding, expected):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    monkeypatch.setattr(templates, "EMAIL_BRANDING", branding)
    for mail in _all_mails(monkeypatch):
        assert mailer.send(mail)
        message = _parse(ses.send_email.call_args.kwargs)
        images = _images(message)
        assert set(images) == expected
        assert all(p.get_content()[:4] == b"\x89PNG" for p in images.values())
        html = _html(message)
        assert {f"<{cid}>" for cid in ("cviche-logo", "wcm-its-logo") if f"cid:{cid}" in html} == expected


def test_the_default_branding_is_its_top_and_cviche_at_the_bottom():
    assert templates.EMAIL_BRANDING == templates.Branding.ITS_TOP_CVICHE_BOTTOM
    html = mailer.completion_notice("pat@med.cornell.edu", complete=2, failed=0, batch_id="BATCHA").html
    html = html[html.index("<body"):]
    assert html.index("cid:wcm-its-logo") < html.index("Your CVs are done") < html.index("cid:cviche-logo")
    assert html.index("cid:cviche-logo") < html.index("About CViche")


def test_both_top_puts_cviche_right_under_the_its_banner():
    html = templates.render_html(templates.EmailContent("Headline", ()), templates.Branding.BOTH_TOP)
    html = html[html.index("<body"):]
    assert html.index("cid:wcm-its-logo") < html.index("cid:cviche-logo") < html.index(">Headline<")


def test_every_mail_is_text_plus_html_with_cid_images(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    for mail in _all_mails(monkeypatch):
        assert mailer.send(mail)
        message = _parse(ses.send_email.call_args.kwargs)
        assert message.get_content_type() == "multipart/alternative"
        assert _text(message).strip() and "<table" in _html(message)
        assert "http://" not in _html(message) and 'src="https' not in _html(message)  # no remote images


def test_the_html_is_the_branded_layout(monkeypatch):
    html = mailer.completion_notice("pat@med.cornell.edu", complete=2, failed=0, batch_id="BATCHA").html
    assert "#B31B1B" in html and "Your CVs are done" in html and "View your CVs" in html
    assert "text-transform:uppercase" in html and "max-width:600px" in html and "About CViche" in html
    assert "paa2013" not in html and "mailto:support@med.cornell.edu" in html and templates.HELPDESK_ARTICLE_URL.replace("&", "&amp;") in html
    assert "If you have questions, read the" in html and "Weill Cornell Medicine Information Technologies &amp; Services" in html


def test_html_escapes_every_interpolated_value(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", 'https://cviche.example.org/"><script>x</script>')
    mail = mailer.completion_notice("pat@med.cornell.edu", complete=1, failed=0, batch_id='A<b>"B', single_run_id=None)
    assert "<script" not in mail.html and "A<b>" not in mail.html
    assert "&lt;script&gt;" in mail.html or "&lt;b&gt;" in mail.html


def test_no_part_of_any_mail_carries_a_filename_or_score(monkeypatch):
    for mail in _all_mails(monkeypatch):
        for part in (mail.body, mail.html):
            assert "score" not in part.lower()
            # The rejection's fixed wording names the accepted file types; no other mail may.
            assert mail.kind == mailer.MailKind.REJECTION or (".docx" not in part and ".pdf" not in part)


def test_the_plain_part_keeps_the_terms_sentence(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    mail = mailer.processing_notice("pat@med.cornell.edu", runs=1, held=0, batch_id="BATCHA")
    assert "terms you agreed to" in mail.body and "terms you agreed to" in mail.html
    assert "Your CV is being processed" in mail.html


def test_completion_wording_multi_and_single(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    multi = mailer.completion_notice("pat@med.cornell.edu", complete=3, failed=0, batch_id="BATCHA")
    assert "Your 3 CVs are done: 3 ready to download." in multi.body and "failed" not in multi.body
    assert "https://cviche.example.org/runs?batch=BATCHA" in multi.body
    mixed = mailer.completion_notice("pat@med.cornell.edu", complete=2, failed=1, batch_id="BATCHA")
    assert "Your 3 CVs are done: 2 ready to download, 1 failed." in mixed.body and "retry it" in mixed.body
    ok = mailer.completion_notice("pat@med.cornell.edu", complete=1, failed=0, batch_id="BATCHA", single_run_id="RUNAAA")
    assert "Your CV is ready" in ok.body and "https://cviche.example.org/run/RUNAAA" in ok.body
    bad = mailer.completion_notice("pat@med.cornell.edu", complete=0, failed=1, batch_id="BATCHA", single_run_id="RUNAAA")
    assert "Your CV failed to process. Open the run to retry it" in bad.body and "/run/RUNAAA" in bad.body


def test_html_escapes_paragraph_text_headline_and_link_label():
    from app.services.email_templates import EmailContent, Para, render_html
    html = render_html(EmailContent("<i>head</i>", (Para("<script>a</script>", "https://x.example.org/?a=1&b=<2>", "<u>go</u>"),), "<b>cta</b>", "https://x.example.org/"))
    for raw in ("<script>", "<i>head", "<u>go", "<b>cta", "b=<2>"):
        assert raw not in html
    assert "&lt;script&gt;" in html and "&amp;b=" in html


@pytest.mark.parametrize("name,expected", [
    ("Dr. Pat Example", "Hi Pat,"), ("Dr Pat Example", "Hi Pat,"), ("Prof. Pat Example", "Hi Pat,"),
    ("Pat Example", "Hi Pat,"), ("  Pat  ", "Hi Pat,"), ("", "Hello,"), (None, "Hello,"), ("Dr.", "Hello,"),
])
def test_greeting_derivation(name, expected):
    assert templates.greeting_for(name) == expected


def test_every_mail_opens_with_the_greeting_in_both_parts(monkeypatch):
    for mail in _all_mails(monkeypatch):
        assert mail.body.startswith("Hello,\n")
    mail = mailer.completion_notice("pat@med.cornell.edu", complete=2, failed=0, batch_id="BATCHA", display_name="Dr. Pat Example")
    assert mail.body.startswith("Hi Pat,\n") and ">Hi Pat,<" in mail.html


def test_the_greeting_name_is_html_escaped():
    mail = mailer.rejection("pat@med.cornell.edu", InboundRejectReason.TOO_MANY_FILES, display_name="<script>x</script> Example")
    assert "<script" not in mail.html and "Hi &lt;script&gt;x&lt;/script&gt;," in mail.html


def test_the_help_line_and_signature_are_in_both_parts(monkeypatch):
    for mail in _all_mails(monkeypatch):
        for part in (mail.body, mail.html):
            assert "support@med.cornell.edu" in part and "paa2013" not in part
            assert "Information Technologies" in part
        assert templates.HELPDESK_ARTICLE_URL in mail.body


@pytest.mark.parametrize("skipped,text", [(1, "1 attachment could not be used and was skipped."), (2, "2 attachments could not be used and were skipped.")])
def test_skipped_attachment_wording_is_singular_or_plural(skipped, text):
    assert text in mailer.processing_notice("pat@med.cornell.edu", runs=1, held=0, batch_id="BATCHA", skipped=skipped).body


@pytest.mark.parametrize("kwargs,label", [
    ({"runs": 3, "held": 0}, "View your CVs"), ({"runs": 1, "held": 0}, "View your CV"),
    ({"runs": 0, "held": 2}, "Open New run"), ({"runs": 0, "held": 2, "outdated_consent": True}, "Review the terms"),
])
def test_each_email_has_one_button_labelled_for_it(monkeypatch, kwargs, label):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    mail = mailer.processing_notice("pat@med.cornell.edu", batch_id="BATCHA", **kwargs)
    assert mail.html.count("text-decoration:underline;text-transform:uppercase") == 1 and f">{label}</a>" in mail.html
    assert f"{label}: https://" in mail.body
    assert "Open Runs" not in mail.html and "Open the batch" not in mail.html
