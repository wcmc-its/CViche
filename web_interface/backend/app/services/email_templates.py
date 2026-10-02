"""HTML and plain-text bodies for CViche's own emails (#1298).

Modeled on WCM's ITS broadcast email: logo banner, small eyebrow, large red
headline, greeting, one bold underlined call to action, a closing and a help
footer. Table layout with inline CSS only, so it holds up in Outlook desktop:
no web fonts, no external images. Logos are CID attachments (the app's host
resolves to private addresses, so a hotlinked image would not load for a
recipient off the network); mailer attaches exactly the ones the HTML names.

``EMAIL_BRANDING`` picks where the two logos go (see ``Branding``).

Every interpolated value is HTML-escaped. Content is counts and fixed wording
only: callers must never pass a filename.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from html import escape
from pathlib import Path

STATIC_EMAIL_DIR = Path(__file__).resolve().parent.parent / "static" / "email"
CVICHE_LOGO_CID = "cviche-logo"
ITS_LOGO_CID = "wcm-its-logo"
# CID -> file, for every logo an email may reference.
LOGO_FILES = {
    CVICHE_LOGO_CID: STATIC_EMAIL_DIR / "cviche-logo.png",
    ITS_LOGO_CID: STATIC_EMAIL_DIR / "wcm-its-logo.png",
}
ITS_LOGO_WIDTH_PX = 600
CVICHE_LOGO_ONLY_WIDTH_PX = 200
CVICHE_LOGO_SECONDARY_WIDTH_PX = 160
CVICHE_LOGO_BENEATH_WIDTH_PX = 140
CVICHE_LOGO_ASPECT = 170 / 520


class Branding(StrEnum):
    ITS_TOP_CVICHE_BOTTOM = "its_top_cviche_bottom"
    BOTH_TOP = "both_top"
    CVICHE_ONLY = "cviche_only"


EMAIL_BRANDING = Branding.ITS_TOP_CVICHE_BOTTOM

SUPPORT_EMAIL = "support@med.cornell.edu"
# Tracking parameters dropped on purpose.
HELPDESK_ARTICLE_URL = (
    "https://wcmcprd.service-now.com/myhelpdesk?id=kb_article_view&sys_kb_id=5a282ff6974d079024b0f757f053af53"
)
# ITS's official name, as on its banner.
SIGNATURE = "Weill Cornell Medicine Information Technologies & Services"
ABOUT_TEXT = "CViche turns an academic CV into the Weill Cornell Medicine format."
FALLBACK_GREETING = "Hello,"
_TITLES = frozenset({"dr", "dr.", "prof", "prof.", "professor", "mr", "mr.", "ms", "ms.", "mrs", "mrs."})

WCM_RED = "#B31B1B"
TEXT_COLOR = "#222222"
MUTED_COLOR = "#666666"
FONT_STACK = "Arial, Helvetica, sans-serif"
MAX_WIDTH_PX = 600
EYEBROW = "CViche"


def greeting_for(display_name: str | None) -> str:
    """``Hi <FirstName>,`` from the first word of the name that is not a
    leading title; ``Hello,`` when there is none."""
    words = (display_name or "").split()
    while words and words[0].lower() in _TITLES:
        words.pop(0)
    return f"Hi {words[0]}," if words else FALLBACK_GREETING


@dataclass(frozen=True, slots=True)
class Para:
    """One paragraph. With ``url`` (the terms link only), the plain text reads
    ``text: url`` and the HTML appends a link labelled ``link_label``."""
    text: str
    url: str | None = None
    link_label: str = "Open"
    bold: bool = False


@dataclass(frozen=True, slots=True)
class EmailContent:
    headline: str
    paragraphs: tuple[Para, ...]
    cta_label: str | None = None
    cta_url: str | None = None
    greeting: str = FALLBACK_GREETING


def render_text(content: EmailContent) -> str:
    lines = [content.greeting]
    lines += [f"{p.text}: {p.url}" if p.url else p.text for p in content.paragraphs]
    if content.cta_label and content.cta_url:
        lines.append(f"{content.cta_label}: {content.cta_url}")
    lines += [
        SIGNATURE,
        f"{ABOUT_TEXT} If you have questions, read the HelpDesk article ({HELPDESK_ARTICLE_URL}) "
        f"or contact {SUPPORT_EMAIL}.",
    ]
    return "\n\n".join(lines) + "\n"


def _link(url: str, label: str, style: str) -> str:
    return f'<a href="{escape(url, quote=True)}" style="{style}">{escape(label)}</a>'


_LINK_STYLE = f"color:{WCM_RED};text-decoration:underline;"
_CTA_STYLE = f"color:{WCM_RED};font-weight:bold;text-decoration:underline;text-transform:uppercase;letter-spacing:1px;"


def _row(inner: str, style: str = "") -> str:
    return f'<tr><td style="{style}font-family:{FONT_STACK};color:{TEXT_COLOR};">{inner}</td></tr>'


def _img(cid: str, width: int, alt: str, height: int | None = None) -> str:
    h = f' height="{height}"' if height else ""
    return (f'<img src="cid:{cid}" width="{width}"{h} alt="{escape(alt)}" '
            f'style="display:block;border:0;outline:none;width:{width}px;max-width:100%;height:auto;">')


def _cviche_img(width: int) -> str:
    return _img(CVICHE_LOGO_CID, width, "CViche", round(width * CVICHE_LOGO_ASPECT))


def _its_img() -> str:
    return _img(ITS_LOGO_CID, ITS_LOGO_WIDTH_PX, "Weill Cornell Medicine Information Technologies & Services")


def _header_html(branding: Branding) -> str:
    rule = f"border-bottom:4px solid {WCM_RED};"
    if branding == Branding.CVICHE_ONLY:
        return _row(_cviche_img(CVICHE_LOGO_ONLY_WIDTH_PX), f"padding:8px 0 20px 0;{rule}")
    if branding == Branding.BOTH_TOP:
        return (_row(_its_img(), "padding:0;")
                + _row(_cviche_img(CVICHE_LOGO_BENEATH_WIDTH_PX), f"padding:14px 0 16px 0;{rule}"))
    return _row(_its_img(), f"padding:0 0 10px 0;{rule}")


def _para_html(para: Para) -> str:
    weight = "bold" if para.bold else "normal"
    link = f": {_link(para.url, para.link_label, _LINK_STYLE)}" if para.url else ""
    return _row(f"{escape(para.text)}{link}", f"padding:0 0 14px 0;font-size:15px;line-height:22px;font-weight:{weight};")


def _cta_html(content: EmailContent) -> str:
    if not (content.cta_label and content.cta_url):
        return ""
    return _row(_link(content.cta_url, content.cta_label, _CTA_STYLE), "padding:6px 0 22px 0;font-size:14px;")


def _footer_html(branding: Branding) -> str:
    logo = ""
    if branding == Branding.ITS_TOP_CVICHE_BOTTOM:
        logo = _row(_cviche_img(CVICHE_LOGO_SECONDARY_WIDTH_PX), "padding:0 0 16px 0;")
    help_line = (
        f'If you have questions, read the {_link(HELPDESK_ARTICLE_URL, "HelpDesk article", _LINK_STYLE)} '
        f'or contact {_link("mailto:" + SUPPORT_EMAIL, SUPPORT_EMAIL, _LINK_STYLE)}.'
    )
    return (
        _row(escape(SIGNATURE), "padding:0 0 20px 0;font-size:14px;line-height:20px;font-weight:bold;")
        + logo
        + f'<tr><td style="padding:16px 0 0 0;border-top:1px solid #dddddd;font-family:{FONT_STACK};'
        f'font-size:12px;line-height:18px;color:{MUTED_COLOR};">'
        f'<strong>About CViche.</strong> {escape(ABOUT_TEXT)} {help_line}</td></tr>'
    )


def render_html(content: EmailContent, branding: Branding | None = None) -> str:
    """The HTML part; logos are referenced as ``cid:`` images."""
    branding = branding or EMAIL_BRANDING
    paragraphs = "".join(_para_html(p) for p in content.paragraphs)
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(content.headline)}</title></head>'
        '<body style="margin:0;padding:0;background-color:#ffffff;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background-color:#ffffff;"><tr><td align="center" style="padding:16px;">'
        f'<table role="presentation" width="{MAX_WIDTH_PX}" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:{MAX_WIDTH_PX}px;background-color:#ffffff;">'
        f'{_header_html(branding)}'
        f'<tr><td style="padding:24px 0 6px 0;font-family:{FONT_STACK};font-size:12px;font-weight:bold;'
        f'color:#000000;text-transform:uppercase;letter-spacing:1px;">{escape(EYEBROW)}</td></tr>'
        f'<tr><td style="padding:0 0 16px 0;font-family:{FONT_STACK};font-size:32px;line-height:38px;'
        f'font-weight:bold;color:{WCM_RED};">{escape(content.headline)}</td></tr>'
        f'{_row(escape(content.greeting), "padding:0 0 14px 0;font-size:15px;line-height:22px;")}'
        f'{paragraphs}{_cta_html(content)}{_footer_html(branding)}'
        '</table></td></tr></table></body></html>'
    )
