"""Which CV headings and lines read as honors, and which lines as grants: one
definition for stage 3b, stage 6 and the doctor (#1577).

Stage 3b's H pin (#1618) and its inverse read a line's grant marker, and its
fellowship pin reads `is_honorific_fellowship`; stage 6's research-support
guard reads `heading_names_honors` and the same grant marker; the doctor's
`section_consistency` lint reports what those leave, by the same tests. Each
test is defined once, here (CODING_STANDARDS.md §1.5), so a lint and a fix
read a line the same way.

- `GRANT_MARKER` and `PI_ROLE_LEAD`: a grant or activity number, a dollar
  amount, a period with two ends, or a PI role at the head of the row. A line
  with either is the author's grant, whatever heading it sits under.
- `heading_names_honors`: a heading that names honors, awards, prizes,
  recognition or distinctions and no funding word. "Grants and Contract
  Awards", "Current Awards" and "Research Awards" head grants lists (YUYVIG).
- `is_honorific_fellowship`: a fellowship that the owner's decision of
  2026-10-30 on #1577 makes an honor. A fellowship the CV says was elected,
  or lists under an honors heading, is an honor and renders under Honors
  with its year. A fellowship grade members apply for (the post-nominal FACS
  or FACC kind), listed under a membership heading, stays a membership. A
  heading that names memberships, associations or credentials therefore
  keeps a fellowship title that no election wording comes with ("Awards,
  Honors, and Membership in Honorary Societies", UVZNIC; "Professional
  Associations", SBPHNA 171), and so does a heading that mixes honors with
  memberships, unless a nearer heading names honors alone. A heading that
  opens with an honor society ("Honor Societies") is an honors heading;
  "Professional and Honorary Societies" mixes the two. A credential heading
  ("Professional Certifications", HBALTD 26; "CERTIFICATION", EUCSNU 45)
  keeps one too: the owner ruled on 2026-11-02 that such a fellowship stays
  a membership.

Pure: data in, bool out; imports nothing but `re` and `collections.abc`.
"""

import re
from collections.abc import Sequence

#: The code a fellowship the decision makes an honor was given: a membership.
MEMBERSHIP_CODE = "I"

#: A grant or activity number, a dollar amount, or a period with two ends
#: (YUYVIG, #1618: an unsure grant answer with none of these, under a heading
#: 3a pins to H, is the author's award).
GRANT_MARKER = re.compile(
    r"\b[A-Z]\d{2}\b"                                                       # R01, K23, T32
    r"|\b(?=[A-Z0-9]*\d{2})(?=[A-Z0-9]*[A-Z]{2})[A-Z0-9]{6,}\b"            # 2T32GM000000
    r"|\$\s?\d|\b\d[\d,.]*\s?(?i:USD|dollars)\b"                            # an amount
    r"|\b(?:19|20)\d{2}\s*(?:[-\u2013\u2014]|\b(?i:to)\b)\s*(?:\d{1,2}/)?"  # two-ended period
    r"(?:[A-Za-z]{3,9}\.?\s*)?(?:(?:19|20)\d{2}\b|\d{2}\b(?!/)|(?i:present|current|date)\b)"
)
#: A PI role at the head of the row, after an optional date or bullet.
PI_ROLE_LEAD = re.compile(
    r"^[\s\d/\-\u2013\u2014,.:;*\u2022]*(?:(?i:(?:principal|co-?)\s*investigator)\b|(?i:co-?)?PI\b)"
)

#: A heading that names honors or awards, and one that names funding.
_HONORS_HEADING_RE = re.compile(r"honou?r|\bawards?\b|\bprizes?\b|recogni[tz]|distinction",
                                re.IGNORECASE)
_FUNDING_HEADING_RE = re.compile(
    r"grant|fund|support|contract|research|investigat|current|\bpast\b|pending|completed"
    r"|ongoing|active", re.IGNORECASE)

#: A heading that names memberships or credentials: a fellowship title under
#: it is where the author put it. "Associations" names a list of bodies, as
#: "Societies" does ("Professional Associations", "Committees and
#: Associations"; SBPHNA 171, #1577 held-out round). A singular "Association"
#: ends one body's own name, which an honors list may use as a sub-heading for
#: that body's honors, and "Associate Editor" names a role.
_MEMBERSHIP_HEADING_RE = re.compile(
    r"member|societ|organi[sz]ation|affiliation|associations|certific|licens", re.IGNORECASE)
#: A heading that opens with an honor society is an honors list, so its
#: "societ" names no membership: "Honor Societies", "Honorary Societies",
#: "Honour Society", "2. Honor Societies". Later in a heading the phrase may
#: share its "Societies" with a membership word before it: "Professional and
#: Honorary Societies" also lists professional societies (#1577 slice A). A
#: lettered heading ("E. Honor Societies") reads as mixed, which keeps I.
_HONOR_SOCIETY_RE = re.compile(r"^[\W\d]*honou?r(?:s|ary)?\s+societ", re.IGNORECASE)
#: A heading that says its rows were elected ("ELECTED MEMBERSHIPS", YME2VA):
#: the CV says a fellowship title under it was elected.
_ELECTED_HEADING_RE = re.compile(r"\belected\b", re.IGNORECASE)

#: A body or office named for its fellows, not a fellowship: "Elected to the
#: Fellowship Committee", "Named Fellowship Program Director", "Elected to the
#: Fellows Council" (#1577 review, B3).
_NOT_A_FELLOWSHIP = (r"(?!\s+(?:sub)?committees?\b|\s+councils?\b|\s+programm?e?s?\b|\s+directors?\b"
                     r"|\s+boards?\b|\s+selection\b|\s+review\b|\s+panels?\b|\s+chair\w*"
                     r"|\s+coordinators?\b)")
#: A line that opens, after an optional date or bullet, with an election to a
#: fellowship: "2017: Elected Fellow of ...", "Elected to Fellowship, ...",
#: "Named Fellow of ...", "Inducted as a Fellow in ...", "Elected to the
#: College of Fellows of ..." (RXYBVF 111).
_ELECTED_FELLOW_LEAD_RE = re.compile(
    r"^[\W\d]*(?:[A-Za-z]{3,9}\.?\s+)?(?:(?:19|20)\d{2}\W*)?(?:elected|named|inducted)\s+"
    r"(?:(?:as|to|into|an?|the)\s+)*(?:college\s+of\s+)?(?:associate\s+|distinguished\s+|honorary\s+)?"
    r"fellow(?:ship|s)?\b" + _NOT_A_FELLOWSHIP,
    re.IGNORECASE)
#: An election stated later in the line, tied to the fellowship: "(elected
#: Fellow)" (BMAMWE 392), "<body>, Elected as Fellow, 2006" (web204 3168), or
#: an election with nothing but a year after the fellowship: "Fellow of the
#: <body> (elected)" (ULHCAP 42), "Fellow, <body> (elected 2014)" (MIFYLG 92),
#: "[elected 2031]", ", elected 2031". "Fellow, <body> (elected Governor,
#: 2015)" elects the owner to an office, not to the fellowship.
_ELECTED_FELLOW_CLAUSE_RE = re.compile(
    r"\belected\s+(?:as\s+|to\s+)?(?:an?\s+)?(?:associate\s+|distinguished\s+|honorary\s+)?"
    r"fellow(?:ship)?\b" + _NOT_A_FELLOWSHIP +
    r"|\bfellow\b[^.;\n]{0,80}[(\[,]\s*elected\s*(?:[)\]]|(?:in\s+)?(?:19|20)\d{2}\b)",
    re.IGNORECASE)
#: A membership grade or another role on the same line: a membership row that
#: mentions an election ("<Academy> (Active Member; Science Committee,
#: 1996-2003; elected fellow 2010)", CYOFWJ 65) is still the membership, as is
#: a fellow elected to an office ("(elected Governor, Northern Chapter)").
#: "Council on <field>" is the body the fellowship is in ("Fellow of the
#: <Association>, Council on <field> (elected 2015)"), not a role.
_OTHER_ROLE_RE = re.compile(
    r"\b(?:members?|membership|committees?|chair\w*|boards?|councils?(?!\s+(?:on|for)\b)"
    r"|councill?ors?|president|officer|secretary|treasurer|governors?|regents?|trustees?"
    r"|directors?|delegates?)\b", re.IGNORECASE)
#: So is a membership period that runs to the present beside an election
#: dated in another year: "<Association>, 1989 - Present. Elected as Fellow,
#: 2009." (web204 3166), "1996-Present Fellow in the <Association>, Elected as
#: fellow, 2002" (ULHCAP 154, whose fellowship the CV lists again, with its
#: year, under Certification). Recoding such a line to H would drop the
#: membership, or the election's own year. An election in the period's first
#: year ("2009-present Fellow, <Society> (elected 2009)") dates the fellowship.
_OPEN_SPAN_RE = re.compile(r"\b((?:19|20)\d{2})\s*[-\u2013\u2014]\s*(?:present|current)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

#: The words that name a society-like body.
_BODY = r"(?:association|society|college|academy|institute)"
#: A fellowship as the line's title. The line opens with "Fellow, <body>",
#: "Fellow of/in <body>", "Fellow (2014)", "Fellow 2018-present",
#: "Fellow | <Body>" or "Fellow<tab><Body>" (GDGSWJ 98), with "Distinguished",
#: "Founding", "Honorary" or "Associate" allowed in front; or "Fellow" and,
#: after an optional dash, up to four capitalised words ending in a body:
#: "Fellow Royal College of Pathologists" (YME2VA 23), "Fellow - <Heart>
#: Association", "Honorary Fellow <Chinese> Society of ..." (web067 200); or
#: it opens with "Fellowship, <body>" or "Fellowship in the <body>" (SHERVB
#: 76, GNONHA 213, CTXOTY 67); or it names a body and then "Fellow":
#: "<Society> Fellow", "<Society> of <Place> Fellow" (NTCULM 34), "<College>
#: of <Field>, Fellow" (PBSGQZ 67); or "fellow of <body>" or "fellowship of
#: <body>" later in it (web175 80, "FACNM, fellow of ..."; web051 133,
#: "Awarded Fellowship of <College>"). "Fellow Travel Award" and "Fellow -
#: <programme>" are no title. A bare "Fellowship" at the end ("<Society>
#: Fellowship") is a programme or an award, not this shape. "<ACRONYM>
#: Fellow" is not matched: "NIH Fellow 1998" and "AHA Fellow" name a training
#: or funding fellowship as often as an honorific one (SQMWHM 1536 "NATA
#: Fellow" is the one corpus line it costs).
_FELLOW_TITLE_RE = re.compile(
    r"^[\W\d]*(?:(?i:present)\W*)?"
    r"(?:(?i:distinguished|founding|honorary|associate)\s+)?(?i:fellow)\b"
    r"(?:\s*,|\s+(?i:of|in)\b|\s*\(|\s+\d|\s*[|\t]\s*(?=[A-Z])"
    r"|\s*(?:[-\u2013\u2014]\s*)?(?:[A-Z][\w&'\u2019.-]*\s+){0,4}(?i:" + _BODY + r")\b)"
    r"|^[\W\d]*(?i:fellowship)(?:\s*[,|\t]\s*|\s+(?i:in|of)\s+(?:(?i:the)\s+)?)"
    r"(?=[^,|\t]{0,40}\b(?i:" + _BODY + r")\b)"
    r"|\b(?i:" + _BODY + r")(?:\s+(?i:of)\s+[\w&'\u2019-]+){0,2}\s+(?i:fellow)\b"
    r"|\b(?i:" + _BODY + r")\b[^,;|\t\n]{0,60},\s*(?i:fellow)\b(?!\s+(?i:travel|award|program))"
    r"|\b(?i:fellow(?:ship)?)\s+(?i:of|in)\s+(?:(?i:the)\s+)?(?:[\w&'\u2019-]+\s+){0,4}(?i:" + _BODY + r")\b")


def heading_names_honors(heading: str) -> bool:
    """A heading that names honors or awards and no funding word."""
    return bool(_HONORS_HEADING_RE.search(heading)) and not _FUNDING_HEADING_RE.search(heading)


def _is_membership_row(text: str) -> bool:
    """A line that names another membership role, or a membership period to
    the present and an election dated in another year."""
    if _OTHER_ROLE_RE.search(text):
        return True
    span = _OPEN_SPAN_RE.search(text)
    if not span:
        return False
    other_years = set(_YEAR_RE.findall(text[:span.start()] + " " + text[span.end():]))
    return bool(other_years - {span.group(1)})


def _names_memberships(heading: str) -> bool:
    """A heading that names memberships or credentials, an honor society
    aside (`_HONOR_SOCIETY_RE`)."""
    return bool(_MEMBERSHIP_HEADING_RE.search(_HONOR_SOCIETY_RE.sub(" ", heading)))


def _lists_under_honors(heading_path: Sequence[str]) -> bool:
    """Whether the nearest heading that names honors or memberships names
    honors and not memberships: "Professional Honors & Recognition >
    National" (MVUREJ 44) and "Honors and Awards > Honor Societies" do;
    "Honors and Professional Memberships > National", "Awards, Honors, and
    Membership in Honorary Societies" (UVZNIC), "Honors and Awards >
    Memberships", "Professional and Honorary Societies" and "Honors, Awards,
    and Professional Associations > Professional Associations" (SBPHNA 171)
    do not. So a heading that mixes honors with memberships is no honors
    path: under it only a nearer honors-only heading, or election wording,
    makes a fellowship an honor."""
    for level in reversed(heading_path):
        names_honors = bool(_HONORS_HEADING_RE.search(level))
        names_membership = _names_memberships(level)
        if names_honors or names_membership:
            return names_honors and not names_membership
    return False


def is_honorific_fellowship(text: str, heading_path: Sequence[str]) -> bool:
    """A fellowship the 2026-10-30 decision on #1577 makes an honor.

    True when the line opens with an election to a fellowship; or states the
    election later and is no membership row (`_is_membership_row`); or names a
    fellowship as its title under a heading that says its rows were elected,
    or that lists it under honors (`_lists_under_honors`). The code the line
    was given is the caller's to check.
    """
    if _ELECTED_FELLOW_LEAD_RE.search(text):
        return True
    if _ELECTED_FELLOW_CLAUSE_RE.search(text) and not _is_membership_row(text):
        return True
    if not _FELLOW_TITLE_RE.search(text):
        return False
    return (any(_ELECTED_HEADING_RE.search(level) for level in heading_path)
            or _lists_under_honors(heading_path))
