"""HTML and plain-text bodies for CViche's own emails (#1298), built to the
notification-email design in the CViche redesign handoff.

Layout: ITS logo on a manila ground, a white card (4px red top rule) holding
the CViche wordmark, headline, greeting, an optional status list, copy and one
filled button, then the ITS name and help line below the card. Table layout and
inline CSS so it holds up in Outlook desktop; the <style> block holds a
small-screen media query (clients that drop it fall back to the desktop sizes)
and the dark palette. No web fonts, no external images: the logos are CID
attachments (the app's host resolves to private addresses, so a hotlinked image
would not load off the network); mailer attaches exactly the ones the HTML
names. Both logos are transparent PNGs, per the handoff.

Dark mode: the email declares ``color-scheme: light dark`` and sets every colour
again under ``prefers-color-scheme: dark`` (Apple Mail, Outlook for Mac/iOS) and
under ``[data-ogsc]``/``[data-ogsb]`` (Outlook.com, new Outlook for Windows), so
those clients show the designed dark palette instead of inverting the light
one. Each logo is a pair: the dark-ink PNG by default, a light-ink PNG swapped
in by the same rules. Classic Outlook for Windows ignores all of it and inverts
on its own, darkening the ground behind the dark-ink logos (accepted by the
handoff: "Outlook ignores both and shows the light version").

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
DARK_CID_SUFFIX = "-dark"
# CID -> file, for every logo an email may reference.
LOGO_FILES = {
    CVICHE_LOGO_CID: STATIC_EMAIL_DIR / "cviche-logo.png",
    ITS_LOGO_CID: STATIC_EMAIL_DIR / "wcm-its-logo.png",
    CVICHE_LOGO_CID + DARK_CID_SUFFIX: STATIC_EMAIL_DIR / "cviche-logo-dark.png",
    ITS_LOGO_CID + DARK_CID_SUFFIX: STATIC_EMAIL_DIR / "wcm-its-logo-dark.png",
}
ITS_LOGO_WIDTH_PX = 280
ITS_LOGO_HEIGHT_PX = 34  # 703x85 scaled to the width
CVICHE_LOGO_WIDTH_PX = 120
CVICHE_LOGO_HEIGHT_PX = 39  # 520x170 scaled to the width

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
MANILA = "#F3EAD7"
CARD_BORDER = "#E6DAC1"
LIST_BG = "#FCFAF6"
INK = "#222222"
MUTED = "#5F5A50"
FONT = "Arial, Helvetica, sans-serif"
MAX_WIDTH_PX = 600
BUTTON_COLOUR = WCM_RED
BADGE_PX = 44
BADGE_MOBILE_PX = 38
BUTTON_CHAR_PX = 9
BUTTON_PAD_PX = 48
BUTTON_MIN_PX = 160
BUTTON_HEIGHT_PX = 44


class StatusKind(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    WAITING = "waiting"
    SKIPPED = "skipped"


STATUS_COLOURS = {
    StatusKind.PROCESSING: "#1D4ED8",
    StatusKind.READY: "#15803D",
    StatusKind.FAILED: "#B91C1C",
    StatusKind.WAITING: "#B45309",
    StatusKind.SKIPPED: "#8A7A58",
}


class BadgeKind(StrEnum):
    CLOCK = "clock"
    CHECK_AMBER = "check_amber"
    CHECK_GREEN = "check_green"
    CROSS = "cross"
    EXCLAMATION = "exclamation"


# kind -> (glyph, circle background, glyph colour). No images: a text glyph in
# a fixed-size cell. U+FE0E asks for the text (not emoji) form of the check.
BADGES = {
    BadgeKind.CLOCK: ("\u25F7", "#EFF6FF", "#1D4ED8"),
    BadgeKind.CHECK_AMBER: ("\u2713\uFE0E", "#FFFBEB", "#B45309"),
    BadgeKind.CHECK_GREEN: ("\u2713\uFE0E", "#ECFDF3", "#15803D"),
    BadgeKind.CROSS: ("\u2715", "#FEF2F2", "#B91C1C"),
    BadgeKind.EXCLAMATION: ("!", "#FFFBEB", "#B45309"),
}

# Dark palette, from the handoff's dark-mode mock (2a-2f).
DARK_GROUND = "#1C1A16"
DARK_CARD = "#26231E"
DARK_BORDER = "#3A352C"
DARK_LIST_BG = "#2E2A24"
DARK_INK = "#EDE6D8"
DARK_MUTED = "#B5AC9C"
DARK_LINK = "#FF9A8F"
DARK_RED_RULE = "#C8372D"
DARK_BLUE = ("#1E2A44", "#8AB4FF")  # (tint, stroke)
DARK_GREEN = ("#16301F", "#6FD39A")
DARK_RED = ("#3A1D1D", "#FF8A80")
DARK_AMBER = ("#3A2E14", "#F5B85A")
DARK_STATUS_COLOURS = {
    StatusKind.PROCESSING: DARK_BLUE[1],
    StatusKind.READY: DARK_GREEN[1],
    StatusKind.FAILED: DARK_RED[1],
    StatusKind.WAITING: DARK_AMBER[1],
    StatusKind.SKIPPED: DARK_MUTED,
}
DARK_BADGES = {
    BadgeKind.CLOCK: DARK_BLUE,
    BadgeKind.CHECK_AMBER: DARK_AMBER,
    BadgeKind.CHECK_GREEN: DARK_GREEN,
    BadgeKind.CROSS: DARK_RED,
    BadgeKind.EXCLAMATION: DARK_AMBER,
}


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
    lead: bool = False  # shown before the status list


@dataclass(frozen=True, slots=True)
class StatusRow:
    kind: StatusKind
    count: int
    label: str


@dataclass(frozen=True, slots=True)
class EmailContent:
    headline: str
    paragraphs: tuple[Para, ...]
    cta_label: str | None = None
    cta_url: str | None = None
    greeting: str = FALLBACK_GREETING
    status: tuple[StatusRow, ...] = ()
    badge: BadgeKind | None = None


def _visible(rows: tuple[StatusRow, ...]) -> tuple[StatusRow, ...]:
    """A count of zero is never shown."""
    return tuple(row for row in rows if row.count > 0)


def render_text(content: EmailContent) -> str:
    blocks = [content.headline, content.greeting]
    if content.paragraphs[:1] and content.paragraphs[0].lead:
        blocks.append(_para_text(content.paragraphs[0]))
        rest = content.paragraphs[1:]
    else:
        rest = content.paragraphs
    rows = _visible(content.status)
    if rows:
        blocks.append("\n".join(f"- {row.count} {row.label[0].lower()}{row.label[1:]}" for row in rows))
    blocks += [_para_text(p) for p in rest]
    if content.cta_label and content.cta_url:
        blocks.append(f"{content.cta_label}: {content.cta_url}")
    blocks += [
        SIGNATURE,
        f"{ABOUT_TEXT} If you have questions, read the HelpDesk article ({HELPDESK_ARTICLE_URL}) "
        f"or contact {SUPPORT_EMAIL}.",
    ]
    return "\n\n".join(blocks) + "\n"


def _para_text(para: Para) -> str:
    return f"{para.text}: {para.url}" if para.url else para.text


def _link(url: str, label: str) -> str:
    return f'<a class="lk" href="{escape(url, quote=True)}" style="color:{WCM_RED};text-decoration:underline;">{escape(label)}</a>'


def _row(inner: str, style: str = "", cls: str = "") -> str:
    attr = f' class="tx {cls}"' if cls else ' class="tx"'
    return f'<tr><td{attr} style="font-family:{FONT};color:{INK};{style}">{inner}</td></tr>'


def _img(cid: str, width: int, height: int, alt: str, cls: str, display: str = "block") -> str:
    return (f'<img class="{cls}" src="cid:{cid}" width="{width}" height="{height}" alt="{escape(alt)}" '
            f'style="display:{display};border:0;outline:none;width:{width}px;max-width:100%;height:auto;">')


def _logo(cid: str, width: int, height: int, alt: str, cls: str) -> str:
    """The light logo, plus its dark twin hidden until the dark rules show it.
    The twin is kept from Outlook desktop, which would otherwise show both."""
    light = _img(cid, width, height, alt, f"{cls} lt")
    dark = _img(cid + DARK_CID_SUFFIX, width, height, alt, f"{cls} dk", display="none")
    return f"{light}<!--[if !mso]><!-->{dark}<!--<![endif]-->"


def _para_html(para: Para) -> str:
    link = f": {_link(para.url, para.link_label)}" if para.url else ""
    return _row(f"{escape(para.text)}{link}", f"padding:0 0 14px 0;font-size:15px;line-height:22px;")


def _status_row_html(row: StatusRow, first: bool) -> str:
    divider = "" if first else f"border-top:1px solid {CARD_BORDER};"
    cell = f"padding:11px 0;background-color:{LIST_BG};{divider}font-family:{FONT};color:{INK};"
    return (
        f'<tr><td class="li d-{row.kind}" width="32" style="{cell}padding-left:14px;font-size:20px;line-height:18px;'
        f'color:{STATUS_COLOURS[row.kind]};">&#9679;</td>'
        f'<td class="li" width="32" style="{cell}font-size:18px;line-height:18px;font-weight:bold;">{row.count}</td>'
        f'<td class="li" style="{cell}padding-right:14px;font-size:14px;line-height:18px;">{escape(row.label)}</td></tr>'
    )


def _status_html(rows: tuple[StatusRow, ...]) -> str:
    if not rows:
        return ""
    body = "".join(_status_row_html(row, index == 0) for index, row in enumerate(rows))
    table = (f'<table class="ln" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
             f'style="border:1px solid {CARD_BORDER};border-radius:6px;border-collapse:separate;">{body}</table>')
    return _row(table, "padding:0 0 20px 0;")


def _button_html(label: str, url: str) -> str:
    href = escape(url, quote=True)
    width = max(BUTTON_MIN_PX, BUTTON_PAD_PX + BUTTON_CHAR_PX * len(label))
    text_style = f"color:#ffffff;font-family:Arial,sans-serif;font-size:15px;font-weight:bold;"
    vml = (f'<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" '
           f'xmlns:w="urn:schemas-microsoft-com:office:word" href="{href}" '
           f'style="height:{BUTTON_HEIGHT_PX}px;v-text-anchor:middle;width:{width}px;" arcsize="14%" stroke="f" '
           f'fillcolor="{BUTTON_COLOUR}"><w:anchorlock/><center style="{text_style}">{escape(label)}</center>'
           f'</v:roundrect><![endif]-->')
    anchor = (f'<!--[if !mso]><!--><a class="btn" href="{href}" style="display:inline-block;background-color:{BUTTON_COLOUR};'
              f'color:#ffffff;font-family:{FONT};font-size:15px;font-weight:bold;line-height:18px;text-decoration:none;'
              f'padding:13px 24px;border-radius:6px;text-align:center;">{escape(label)}</a><!--<![endif]-->')
    return _row(vml + anchor, "padding:8px 0 4px 0;")


def _cta_html(content: EmailContent) -> str:
    if not (content.cta_label and content.cta_url):
        return ""
    return _button_html(content.cta_label, content.cta_url)


def _footer_html() -> str:
    help_line = (f'If you have questions, read the {_link(HELPDESK_ARTICLE_URL, "HelpDesk article")} '
                 f'or contact {_link("mailto:" + SUPPORT_EMAIL, SUPPORT_EMAIL)}.')
    return (
        _row(escape(SIGNATURE), "padding:16px 4px 8px 4px;font-size:13px;line-height:19px;font-weight:bold;")
        + _row(f'<strong class="tx" style="color:{INK};">About CViche.</strong> {escape(ABOUT_TEXT)} {help_line}',
               f"padding:0 4px;font-size:12px;line-height:18px;color:{MUTED};", "mu")
    )


# selector -> declarations, applied in dark mode. Order matters: a later rule
# wins over an earlier one of the same specificity (.mu over .tx, .d-* over .li).
_DARK_RULES = (
    (".bg", f"background-color:{DARK_GROUND}"),
    (".cardbg", f"background-color:{DARK_CARD};border-color:{DARK_BORDER};border-top-color:{DARK_RED_RULE}"),
    (".tx", f"color:{DARK_INK}"),
    (".mu", f"color:{DARK_MUTED}"),
    (".lk", f"color:{DARK_LINK}"),
    (".ln", f"border-color:{DARK_BORDER}"),
    (".li", f"background-color:{DARK_LIST_BG};border-color:{DARK_BORDER};color:{DARK_INK}"),
    *((f".d-{kind}", f"color:{colour}") for kind, colour in DARK_STATUS_COLOURS.items()),
    *((f".b-{kind}", f"background-color:{tint};color:{stroke}") for kind, (tint, stroke) in DARK_BADGES.items()),
    (".btn", f"background-color:{DARK_INK};color:{DARK_GROUND}"),
    (".lt", "display:none"),
    (".dk", "display:block"),
)


def _dark_css(prefix: str) -> str:
    return "".join(
        f"{prefix}{selector}{{{';'.join(d + ' !important' for d in decls.split(';'))};}}"
        for selector, decls in _DARK_RULES
    )


_STYLE = (
    ":root{color-scheme:light dark;supported-color-schemes:light dark;}"
    f"@media (prefers-color-scheme:dark){{{_dark_css('')}}}"
    # Outlook.com and new Outlook for Windows mark their dark mode with these attributes.
    + _dark_css("[data-ogsc] ") + _dark_css("[data-ogsb] ")
    + "@media only screen and (max-width:480px){"
    ".outer{padding:20px 12px 28px 12px !important;}"
    ".card{padding:16px 22px 22px 22px !important;}"
    ".its{width:220px !important;}"
    ".mark{width:104px !important;}"
    ".badge{width:38px !important;height:38px !important;line-height:38px !important;font-size:19px !important;}"
    ".h{font-size:24px !important;line-height:30px !important;}"
    ".btn{display:block !important;width:100% !important;box-sizing:border-box !important;}}"
)


def _badge_html(kind: BadgeKind | None) -> str:
    if kind is None:
        return ""
    glyph, background, colour = BADGES[kind]
    cell = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td class="badge b-{kind}" width="{BADGE_PX}" height="{BADGE_PX}" align="center" valign="middle" '
            f'style="width:{BADGE_PX}px;height:{BADGE_PX}px;background-color:{background};border-radius:50%;'
            f'font-family:{FONT};font-size:22px;line-height:{BADGE_PX}px;font-weight:bold;color:{colour};'
            f'text-align:center;">{glyph}</td></tr></table>')
    return _row(cell, "padding:0 0 14px 0;")


def _card_html(content: EmailContent) -> str:
    paragraphs = content.paragraphs
    lead = _para_html(paragraphs[0]) if paragraphs and paragraphs[0].lead else ""
    rest = paragraphs[1:] if lead else paragraphs
    inner = (
        _row(_logo(CVICHE_LOGO_CID, CVICHE_LOGO_WIDTH_PX, CVICHE_LOGO_HEIGHT_PX, "CViche", "mark"), "padding:0 0 22px 0;")
        + _badge_html(content.badge)
        + _row(escape(content.headline), "padding:0 0 16px 0;font-size:28px;line-height:34px;font-weight:bold;", "h")
        + _row(escape(content.greeting), "padding:0 0 14px 0;font-size:15px;line-height:22px;")
        + lead + _status_html(_visible(content.status)) + "".join(_para_html(p) for p in rest) + _cta_html(content)
    )
    return (f'<table class="cardbg" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="background-color:#ffffff;border:1px solid {CARD_BORDER};border-top:4px solid {WCM_RED};'
            f'border-radius:8px;border-collapse:separate;"><tr><td class="card" style="padding:30px 36px 36px 36px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{inner}</table>'
            f'</td></tr></table>')


def render_html(content: EmailContent) -> str:
    """The HTML part; logos are referenced as ``cid:`` images."""
    its = _logo(ITS_LOGO_CID, ITS_LOGO_WIDTH_PX, ITS_LOGO_HEIGHT_PX,
               "Weill Cornell Medicine Information Technologies & Services", "its")
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f'<title>{escape(content.headline)}</title><style>{_STYLE}</style></head>'
        f'<body class="bg" style="margin:0;padding:0;background-color:{MANILA};">'
        f'<table class="bg" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background-color:{MANILA};"><tr><td class="outer" align="center" style="padding:32px 24px 40px 24px;">'
        f'<table role="presentation" width="{MAX_WIDTH_PX}" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:{MAX_WIDTH_PX}px;">'
        f'<tr><td style="padding:0 0 16px 0;">{its}</td></tr>'
        f'<tr><td>{_card_html(content)}</td></tr>'
        f'{_footer_html()}'
        '</table></td></tr></table></body></html>'
    )
