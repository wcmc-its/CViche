"""HTML and plain-text bodies for CViche's own emails (#1298).

Modeled on WCM's ITS broadcast email: logo banner, small eyebrow, large red
headline, bold subheads, a bold underlined call to action, and an About
footer. Table layout with inline CSS only, so it holds up in Outlook desktop:
no web fonts, no external images. The logo is a CID attachment (the app's
host resolves to private addresses, so a hotlinked image would not load for a
recipient off the network); ``LOGO_PATH`` is read once by mailer when building
the message.

Every interpolated value is HTML-escaped. Content is counts and fixed wording
only: callers must never pass a filename.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "email" / "cviche-logo.png"
LOGO_CID = "cviche-logo"
LOGO_WIDTH_PX = 200
LOGO_HEIGHT_PX = 65
HELP_CONTACT = "paa2013@med.cornell.edu"

WCM_RED = "#B31B1B"
TEXT_COLOR = "#222222"
MUTED_COLOR = "#666666"
FONT_STACK = "Arial, Helvetica, sans-serif"
MAX_WIDTH_PX = 600
EYEBROW = "CViche"
ABOUT_TEXT = ("CViche turns an academic CV into the Weill Cornell Medicine format. "
              f"Questions? Contact {HELP_CONTACT}.")


@dataclass(frozen=True, slots=True)
class Para:
    """One paragraph. With ``url``, the plain text reads ``text: url`` and the
    HTML appends a link labelled ``link_label``."""
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


def render_text(content: EmailContent) -> str:
    lines = [f"{p.text}: {p.url}" if p.url else p.text for p in content.paragraphs]
    lines.append(ABOUT_TEXT)
    return "\n\n".join(lines) + "\n"


def _link(url: str, label: str, style: str) -> str:
    return f'<a href="{escape(url, quote=True)}" style="{style}">{escape(label)}</a>'


_LINK_STYLE = f"color:{WCM_RED};text-decoration:underline;"
_CTA_STYLE = f"color:{WCM_RED};font-weight:bold;text-decoration:underline;text-transform:uppercase;letter-spacing:1px;"


def _para_html(para: Para) -> str:
    weight = "bold" if para.bold else "normal"
    link = f": {_link(para.url, para.link_label, _LINK_STYLE)}" if para.url else ""
    return (f'<tr><td style="padding:0 0 14px 0;font-family:{FONT_STACK};font-size:15px;line-height:22px;'
            f'color:{TEXT_COLOR};font-weight:{weight};">{escape(para.text)}{link}</td></tr>')


def _cta_html(content: EmailContent) -> str:
    if not (content.cta_label and content.cta_url):
        return ""
    return (f'<tr><td style="padding:6px 0 22px 0;font-family:{FONT_STACK};font-size:14px;">'
            f'{_link(content.cta_url, content.cta_label, _CTA_STYLE)}</td></tr>')


def render_html(content: EmailContent) -> str:
    """The HTML part; the logo is referenced as ``cid:LOGO_CID``."""
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
        f'<tr><td style="padding:8px 0 20px 0;border-bottom:4px solid {WCM_RED};">'
        f'<img src="cid:{LOGO_CID}" width="{LOGO_WIDTH_PX}" height="{LOGO_HEIGHT_PX}" alt="CViche" '
        'style="display:block;border:0;outline:none;"></td></tr>'
        f'<tr><td style="padding:24px 0 6px 0;font-family:{FONT_STACK};font-size:12px;font-weight:bold;'
        f'color:#000000;text-transform:uppercase;letter-spacing:1px;">{escape(EYEBROW)}</td></tr>'
        f'<tr><td style="padding:0 0 16px 0;font-family:{FONT_STACK};font-size:32px;line-height:38px;'
        f'font-weight:bold;color:{WCM_RED};">{escape(content.headline)}</td></tr>'
        f'{paragraphs}{_cta_html(content)}'
        f'<tr><td style="padding:16px 0 0 0;border-top:1px solid #dddddd;font-family:{FONT_STACK};'
        f'font-size:12px;line-height:18px;color:{MUTED_COLOR};">'
        f'<strong>About CViche.</strong> {escape(ABOUT_TEXT)}</td></tr>'
        '</table></td></tr></table></body></html>'
    )
