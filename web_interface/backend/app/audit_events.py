"""Structured audit event names shared across the auth module (#373).

Each of these fires from more than one call site across auth.py,
auth_routes.py and saml_routes.py -- named per CODING_STANDARDS.md's rule
on repeated literals (a magic string used for classification gets a name),
the same shape as the "gpt-5.1" precedent that rule is built on.
"""
LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_FAILED = "LOGIN_FAILED"
SESSION_EXPIRED = "SESSION_EXPIRED"
SESSION_REVOKED = "SESSION_REVOKED"
ROLE_CHANGED = "ROLE_CHANGED"
GROUP_MEMBERSHIP_REMOVED = "GROUP_MEMBERSHIP_REMOVED"
DIRECTORY_UNAVAILABLE = "DIRECTORY_UNAVAILABLE"
SESSION_STORE_UNAVAILABLE = "SESSION_STORE_UNAVAILABLE"
RUN_DELETED = "RUN_DELETED"
