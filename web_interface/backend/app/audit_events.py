"""Structured audit event names shared across the auth module (#373).

Each of these fires from more than one call site across auth.py,
auth_routes.py, saml_routes.py and the services/auth_service.py and
services/saml_service.py workflows they call -- named per CODING_STANDARDS.md's
rule on repeated literals (a magic string used for classification gets a
name), the same shape as the "gpt-5.1" precedent that rule is built on.

The USER_* events (#366) fire from services/user_service.provision_user when a
login creates a user or changes its stored identity. A role change made there
reuses ROLE_CHANGED -- the same event the per-request ED re-check emits -- so an
investigation searches one name for every role transition.
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
CONSENT_VERSION_PUBLISHED = "CONSENT_VERSION_PUBLISHED"
USER_CREATED = "USER_CREATED"
USER_IDENTITY_LINKED = "USER_IDENTITY_LINKED"
USER_EMAIL_UPDATED = "USER_EMAIL_UPDATED"
