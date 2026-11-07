"""Deciding whose CV this is (#398).

Same responsibility as `institution.py`, different subject: the owner's name is
needed to auto-fill PI fields, and it may arrive as a structured record, or only
as a document uid like "2015_Testa", or not at all.

`_is_cv_owner` says whether a name a grant gives is the owner's. It lives here,
not in `sections/research_support.py`, because two sections ask: M2's grant
table and N2's training-grant table (#1710), and a section may not import
another (CODING_STANDARDS §1.3).
"""
import re


def _get_cv_owner_name(cv_owner: dict | None = None, document_uid: str = '') -> str:
    """Extract the CV owner's full name for auto-filling PI fields.

    Args:
        cv_owner: Dict with keys like 'last_name', 'first_name', etc.
        document_uid: Document UID like "2015_Testa" to extract name from

    Returns:
        Full name string (e.g., "Jane Testa") or last name if first not available
    """
    if cv_owner:
        first = cv_owner.get('first_name', '')
        last = cv_owner.get('last_name', '')
        if first and last:
            return f"{first} {last}"
        elif last:
            return last

    # Fall back to extracting from document_uid
    if document_uid:
        # Handle patterns like "2015_Testa" or "2003_Examplejs_Cv"
        parts = document_uid.split('_')
        if len(parts) >= 2:
            # Second part is usually the name
            name_part = parts[1]
            # Remove common suffixes
            name_part = re.sub(r'(js|cv|CV|Cv)$', '', name_part, flags=re.IGNORECASE)
            # Capitalize properly
            return name_part.capitalize()

    return ''


# A list of PIs joined by a word, never one person (`_is_cv_owner`), and a
# word of a name: three letters or more, a hyphenated part kept with it.
_NAME_JOINER_RE = re.compile(r" (?:&|and) ")
_NAME_WORD_RE = re.compile(r"[^\W\d_]{3,}(?:-[^\W\d_]+)*")


def _surname_parts(name: str) -> frozenset[str]:
    """The casefolded parts of the surname in "First M. Last".

    A hyphenated surname contributes each part ("Rivera-Ortiz" gives both), so
    "Ana Rivera" still reads as the owner "Ana Rivera-Ortiz". Any comma means a
    list of names ("[PIs Lee, Park]"): no label pattern captures "Last, First",
    and `_get_cv_owner_name` builds "First Last", so a comma never marks a
    surname. Empty for a list or no name.
    """
    if ',' in name:
        return frozenset()
    words = _NAME_WORD_RE.findall(name)
    return frozenset(words[-1].casefold().split('-')) if words else frozenset()


def _is_cv_owner(pi_name: str, owner_name: str) -> bool:
    """True when `pi_name` is one person whose surname is the CV owner's surname.

    "Lee (PI)" names the CV owner "Ann Lee" by surname; the owner's full name is
    the better cell, and it is what the owner auto-fill rendered before the label
    was read. Surname only: a shared first name ("PI: Ann Smith" on Ann Lee's
    CV) or a given name that is another person's surname ("PI: Mark Hale" on
    Tom Mark Lee's CV) is someone else. A joined pair ("Lee & Park") or a list
    ("Lee, Park") is never replaced, since dropping the other name would lose a
    PI.
    """
    if not owner_name or _NAME_JOINER_RE.search(pi_name):
        return False
    return bool(_surname_parts(pi_name) & _surname_parts(owner_name))
