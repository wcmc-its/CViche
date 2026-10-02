"""The shape of a web run id, so a name fallback can refuse to read one as a name (#457).

In the web driver the document uid IS the run id, and `generate_run_id`
(web_interface/backend/app/api/upload.py) issues six letters A-Z (#1192). Ids
from the generations before it stay valid: A-Z0-9, and before that upper-cased
urlsafe base64, which can carry "-" and "_" (app/storage/base.py). A guard that
said "an alphabetic token can be a surname" (#464) stopped telling an
identifier from a name the day ids became letters-only.

The pipeline cannot import the backend (CODING_STANDARDS 1.4), so the shape is
defined here and the backend's `generate_run_id` test pins the generator to it.
"""

import re

# Uppercase only: a filename-style stem ("Doe", "2015_Doe") is mixed case
# or longer than six characters, so it does not match.
RUN_ID_RE = re.compile(r"[A-Z0-9_-]{6}")


def is_run_id(uid: str) -> bool:
    """True when `uid` is exactly a web run id, i.e. an identifier, not a name."""
    return RUN_ID_RE.fullmatch(uid) is not None
