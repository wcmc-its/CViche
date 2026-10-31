"""Which CV headings and lines read as honors: one definition for stage 3b, stage 6 and the doctor (#1577).

Stage 3b's fellowship pin recodes a model answer by `is_honorific_fellowship`,
stage 6's research-support guard routes a grant record by
`heading_names_honors`, and the doctor's `section_consistency` lint reports
what those two leave, by the same tests. Each test is defined once, here
(CODING_STANDARDS.md §1.5), so a lint cannot flag a shape the fix reads
differently.

- `heading_names_honors`: a heading that names honors, awards, prizes,
  recognition or distinctions and no funding word. "Grants and Contract
  Awards", "Current Awards" and "Research Awards" head grants lists (YUYVIG).
- `is_honorific_fellowship`: a fellowship that the owner's decision of
  2026-10-30 on #1577 makes an honor. A fellowship the CV says was elected,
  or lists under an honors heading, is an honor and renders under Honors
  with its year. A fellowship grade members apply for (the post-nominal FACS
  or FACC kind), listed under a membership heading, stays a membership. A
  leaf heading that names memberships or credentials therefore keeps a
  fellowship title that no election wording comes with ("Awards, Honors, and
  Membership in Honorary Societies", UVZNIC; "Professional Certifications",
  HBALTD).

Pure: data in, bool out; imports nothing but `re`.
"""

import re
from collections.abc import Sequence

#: A heading that names honors or awards, and one that names funding.
_HONORS_HEADING_RE = re.compile(r"honou?r|\bawards?\b|\bprizes?\b|recogni[tz]|distinction",
                                re.IGNORECASE)
_FUNDING_HEADING_RE = re.compile(
    r"grant|fund|support|contract|research|investigat|current|\bpast\b|pending|completed"
    r"|ongoing|active", re.IGNORECASE)

#: A leaf heading that names memberships or credentials: a fellowship title
#: under it is where the author put it.
_MEMBERSHIP_HEADING_RE = re.compile(r"member|societ|organi[sz]ation|affiliation|certific|licens",
                                    re.IGNORECASE)

#: A line that opens, after an optional date or bullet, with an election to a
#: fellowship: "2017: Elected Fellow of ...", "Elected to Fellowship, ...",
#: "Named Fellow of ...", "Inducted as a Fellow in ...", "Elected to the
#: College of Fellows of ..." (RXYBVF 111).
_ELECTED_FELLOW_LEAD_RE = re.compile(
    r"^[\W\d]*(?:[A-Za-z]{3,9}\.?\s+)?(?:(?:19|20)\d{2}\W*)?(?:elected|named|inducted)\s+"
    r"(?:(?:as|to|into|an?|the)\s+)*(?:college\s+of\s+)?(?:associate\s+|distinguished\s+|honorary\s+)?"
    r"fellow(?:ship|s)?\b",
    re.IGNORECASE)
#: An election stated later in the line, tied to the fellowship: "(elected
#: Fellow)" (BMAMWE 392), "Fellow of the <body> (elected)" (ULHCAP 42),
#: "<body>, Elected as Fellow, 2006" (web204 3168).
_ELECTED_FELLOW_CLAUSE_RE = re.compile(
    r"\belected\s+(?:as\s+|to\s+)?(?:an?\s+)?(?:associate\s+|distinguished\s+|honorary\s+)?"
    r"fellow(?:ship)?\b"
    r"|\bfellow\b[^.;\n]{0,60}\(\s*elected\b",
    re.IGNORECASE)
#: A membership grade or another role on the same line: a membership row that
#: mentions an election ("<Academy> (Active Member; Science Committee,
#: 1996-2003; elected fellow 2010)", CYOFWJ 65) is still the membership.
_OTHER_ROLE_RE = re.compile(
    r"\b(?:members?|membership|committees?|chair\w*|boards?|council|president|officer"
    r"|secretary|treasurer)\b", re.IGNORECASE)
#: So is a membership period that runs to the present beside a separately
#: dated election: "<Association>, 1989 - Present. Elected as Fellow, 2009."
#: (web204 3166), "1996-Present Fellow in the <Association>, Elected as
#: fellow, 2002" (ULHCAP 154, whose fellowship the CV lists again, with its
#: year, under Certification). Recoding such a line to H would drop the
#: membership, or the election's own year.
_OPEN_SPAN_RE = re.compile(r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:present|current)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

#: The words that name a society-like body.
_BODY = r"(?:association|society|college|academy|institute)"
#: A fellowship as the line's title. The line opens with "Fellow, <body>",
#: "Fellow of/in <body>", "Fellow (2014)", "Fellow 2018-present",
#: "Fellow | <Body>" or "Fellow<tab><Body>" (GDGSWJ 98), with "Distinguished",
#: "Founding", "Honorary" or "Associate" allowed in front; or it opens with
#: "Fellowship, <body>" or "Fellowship in the <body>" (SHERVB 76, GNONHA 213,
#: CTXOTY 67); or it names a body and then "Fellow": "<Society> Fellow",
#: "<Society> of <Place> Fellow" (NTCULM 34), "<College> of <Field>, Fellow"
#: (PBSGQZ 67); or "fellow of <body>" later in it (web175 80, "FACNM, fellow
#: of ..."). "Fellow Travel Award" and "Fellow - <programme>" are no title. A
#: bare "Fellowship" at the end ("<Society> Fellowship") is a programme or an
#: award, not this shape. "<ACRONYM> Fellow" is not matched: "NIH Fellow 1998"
#: and "AHA Fellow" name a training or funding fellowship as often as an
#: honorific one (SQMWHM 1536 "NATA Fellow" is the one corpus line it costs).
_FELLOW_TITLE_RE = re.compile(
    r"^[\W\d]*(?:(?i:present)\W*)?"
    r"(?:(?i:distinguished|founding|honorary|associate)\s+)?(?i:fellow)\b"
    r"(?:\s*,|\s+(?i:of|in)\b|\s*\(|\s+\d|\s*[|\t]\s*(?=[A-Z]))"
    r"|^[\W\d]*(?i:fellowship)(?:\s*[,|\t]\s*|\s+(?i:in|of)\s+(?:(?i:the)\s+)?)"
    r"(?=[^,|\t]{0,40}\b(?i:" + _BODY + r")\b)"
    r"|\b(?i:" + _BODY + r")(?:\s+(?i:of)\s+[\w&'’-]+){0,2}\s+(?i:fellow)\b"
    r"|\b(?i:" + _BODY + r")\b[^,;|\t\n]{0,60},\s*(?i:fellow)\b(?!\s+(?i:travel|award|program))"
    r"|\b(?i:fellow)\s+(?i:of|in)\s+(?:(?i:the)\s+)?(?:[\w&'’-]+\s+){0,4}(?i:" + _BODY + r")\b")


def heading_names_honors(heading: str) -> bool:
    """A heading that names honors or awards and no funding word."""
    return bool(_HONORS_HEADING_RE.search(heading)) and not _FUNDING_HEADING_RE.search(heading)


def _is_membership_row(text: str) -> bool:
    """A line that names another membership role, or a membership period to
    the present and a year outside it."""
    if _OTHER_ROLE_RE.search(text):
        return True
    span = _OPEN_SPAN_RE.search(text)
    return bool(span) and bool(_YEAR_RE.search(text[:span.start()] + " " + text[span.end():]))


def is_honorific_fellowship(text: str, heading_path: Sequence[str]) -> bool:
    """A fellowship the 2026-10-30 decision on #1577 makes an honor.

    True when the line opens with an election to a fellowship; or states the
    election later and is no membership row (`_is_membership_row`); or names a
    fellowship as its title on a heading path that names honors, whose leaf
    names no membership or credential. The code the line was given is the
    caller's to check.
    """
    if _ELECTED_FELLOW_LEAD_RE.search(text):
        return True
    if _ELECTED_FELLOW_CLAUSE_RE.search(text) and not _is_membership_row(text):
        return True
    leaf = heading_path[-1] if heading_path else ""
    return (any(_HONORS_HEADING_RE.search(level) for level in heading_path)
            and not _MEMBERSHIP_HEADING_RE.search(leaf)
            and bool(_FELLOW_TITLE_RE.search(text)))
