"""The SAML ACS login workflow: validate, replay-gate, authorize, provision (#349).

Holds everything POST /api/saml/acs does between reading the form and
answering the browser. That means: parse and signature-check the IdP's
Response, enforce its Destination, accept each assertion ID once, read the
user's attributes, authorize them against ED and resolve their role,
provision their User row, and mint the session token. The route keeps the
HTTP edge: the auth-mode guard, RelayState validation, and mapping the
outcome to a redirect and cookie. Nothing here sees a Request or builds a
Response.
"""
import logging
import os
from dataclasses import dataclass
from enum import StrEnum

from pydantic import SecretStr
from saml2 import SAMLError
from saml2.mdstore import SourceNotFound
from saml2.response import AuthnResponse, IncorrectlySigned
from saml2.sigver import CertificateError, SigverError
from sqlalchemy.orm import Session

from app.audit_events import LOGIN_FAILED, LOGIN_SUCCESS, SESSION_STORE_UNAVAILABLE
from app.auth import SessionEpochUnreadable, create_session_cookie, role_for_membership
from app.config_loader import get_config_value
from app.ed_group_lookup import (
    EdUnavailableError,
    LDAPConfig,
    MembershipResult,
    check_ed_membership,
    fetch_ed_department,
)
from app.models import User, UserRole, UserStatus
from app.saml_client import extract_user_attrs, get_saml_client
from app.saml_replay import (
    assertion_ids,
    get_replay_cache,
    replay_fail_closed,
    replay_ttl,
)
from app.services.ed_access import partner_membership
from app.services.user_service import normalize_email, provision_user
from app.session_idle import SessionStoreUnavailable

# Named for the route, not __name__: these lines (the [SECURITY] rejections
# and the LOGIN_* audit trail) were emitted under app.api.saml_routes before
# the workflow moved here, and both log formats print the logger name, so
# keeping it leaves every line unchanged.
logger = logging.getLogger("app.api.saml_routes")

# SAML binding URI (string literal to avoid import coupling)
BINDING_HTTP_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


class SamlLoginFailure(StrEnum):
    """Which check refused the session. saml_routes maps each to its
    login-page redirect; the four that emit a LOGIN_FAILED audit line
    (NOT_AUTHORIZED, ACCOUNT_DISABLED and the two SESSION_*) use their value as
    its `reason`."""
    NO_RESPONSE = "no_response"
    BAD_SIGNATURE = "bad_signature"
    RESPONSE_REJECTED = "response_rejected"
    PARSER_ERROR = "parser_error"
    WRONG_DESTINATION = "wrong_destination"
    MULTIPLE_ASSERTIONS = "multiple_assertions"
    REPLAYED = "replayed"
    NO_ASSERTION_ID = "no_assertion_id"
    MISSING_ATTRIBUTES = "missing_attributes"
    NOT_AUTHORIZED = "not_authorized"
    DIRECTORY_UNAVAILABLE = "directory_unavailable"
    SESSION_STORE_UNAVAILABLE = "session_store_unavailable"
    SESSION_STATE_UNAVAILABLE = "session_state_unavailable"
    ACCOUNT_DISABLED = "account_disabled"


@dataclass(frozen=True)
class SamlLoginSession:
    """An ACS login that succeeded: the provisioned user and their session token."""
    user: User
    token: str


def authenticate_saml_response(saml_response: str, db: Session) -> SamlLoginSession | SamlLoginFailure:
    """Turn the POSTed SAMLResponse into a session, or the check that refused it.

    A genuinely unexpected exception outside the parsing boundary (see
    _parse_saml_assertion) propagates rather than becoming a failure.
    """
    attrs = _parse_saml_assertion(saml_response, db)
    if isinstance(attrs, SamlLoginFailure):
        return attrs

    user_role = _saml_role_from_ed(attrs, db)
    if isinstance(user_role, SamlLoginFailure):
        return user_role

    # JIT User Provisioning (upsert) -- anchored on cwid, email optional.
    # user_role is None when ED authorization is off: don't override the
    # existing role in that case.
    user = provision_user(
        db=db,
        cwid=attrs["cwid"],
        email=attrs["email"],
        display_name=attrs["display_name"],
        auth_method="saml",
        role=user_role,
        department=_saml_department_from_ed(attrs["cwid"], db),
    )

    # The same test get_current_user applies (app.auth._load_active_user): a
    # session minted here would be refused on its first use anyway.
    if user.status != UserStatus.ACTIVE:
        logger.info(LOGIN_FAILED, extra={"cwid": attrs["cwid"], "reason": SamlLoginFailure.ACCOUNT_DISABLED})
        return SamlLoginFailure.ACCOUNT_DISABLED

    token = _mint_saml_session(user, db, attrs["cwid"])
    if isinstance(token, SamlLoginFailure):
        return token

    logger.info(
        LOGIN_SUCCESS,
        extra={
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "auth_method": "saml",
        },
    )
    return SamlLoginSession(user=user, token=token)


def _parse_saml_assertion(saml_response: str, db: Session) -> dict | SamlLoginFailure:
    """Validate the POSTed assertion and pull the user attributes out of it.

    Returns the attrs, or the failure to reject with; every failure arm is a
    rejection rather than a raise.

    Two try blocks on purpose: the first (get_saml_client + parse) is the
    untrusted-input parsing boundary, where a bare `except Exception` IS
    correct (see its comment). The replay gate and attribute extraction
    below it are deliberately OUTSIDE that boundary: a bug inside
    check_and_record must still 500, not get redirected as if it were an
    untrusted-input parsing failure (PR #781 fix-round item 1, threads
    r3966551686 / r3966560287).
    """
    try:
        client = get_saml_client(db)
        authn_response = client.parse_authn_request_response(
            saml_response, BINDING_HTTP_POST
        )

        if authn_response is None:
            logger.warning("SAML ACS: authn_response is None (invalid assertion)")
            return SamlLoginFailure.NO_RESPONSE

    except (SigverError, CertificateError, IncorrectlySigned) as e:
        # SEC-02: signature/validation failures get the [SECURITY] prefix.
        # Narrow exception types, not string-matching str(e) -- pysaml2's
        # exception message text isn't a stable API across dependency
        # versions. Verified against the installed pysaml2: SigverError is
        # the base of SignatureError/BadSignature/MissingKey/XmlsecError;
        # CertificateError is its own separate root (not a SigverError
        # subclass); IncorrectlySigned is response.py's distinct
        # signature-failure type. Together these are pysaml2's full
        # signature/cert-validation exception surface.
        logger.warning("[SECURITY] SAML signature validation failed: %s", str(e))
        return SamlLoginFailure.BAD_SIGNATURE
    except (RuntimeError, OSError, SAMLError, SourceNotFound) as e:
        # Same taxonomy as saml_login (get_saml_client() is the shared call
        # that can raise RuntimeError/OSError/SourceNotFound), plus
        # SAMLError for pysaml2's other real response-processing errors not
        # already narrowed above -- e.g. StatusError (IdP reported an error
        # status) and VerificationError (audience/recipient/conditions
        # check failed). CertificateError is intentionally omitted from
        # this tuple: it's already caught above alongside the
        # signature-specific exceptions.
        #
        # A genuinely unexpected exception still propagates rather than
        # redirecting -- see saml_login's comment for why (mrj4001 review,
        # PR #656 item 6 / #672). This SAMLError/RuntimeError/OSError/
        # SourceNotFound tuple stays narrow; the catch-all below it is a
        # separate, deliberate boundary (see its own comment).
        logger.error("SAML ACS processing failed: %s", e, exc_info=True)
        return SamlLoginFailure.RESPONSE_REJECTED
    except Exception:
        # pysaml2's audience-restriction check (saml2/response.py's
        # AuthnResponse.for_me()) raises a BARE `Exception`, not a SAMLError
        # subclass -- the one pysaml2 failure mode none of the typed clauses
        # above can name (ditto ResponseLifetimeExceed, the expired-assertion
        # case -- also a plain Exception, not a SAMLError). This IS the
        # untrusted-input boundary where a fail-closed catch-all is correct
        # (CODING_STANDARDS.md §5.5), unlike the replay cache's operational
        # except (SamlReplayCache.check_and_record catches only
        # redis.RedisError -- mrj4001 review, PR #781 threads r3966551686 /
        # r3966560287). Scoped to ONLY get_saml_client()/parse above -- NOT
        # the replay gate or attribute extraction below (see the docstring).
        logger.exception("[SECURITY] SAML response rejected: unexpected parser error")
        return SamlLoginFailure.PARSER_ERROR

    # Outside the parsing try on purpose -- see the docstring.
    destination_error = _reject_wrong_destination(authn_response)
    if destination_error is not None:
        return destination_error

    replay_error = _reject_replayed_assertion(authn_response)
    if replay_error is not None:
        return replay_error

    try:
        identity = authn_response.get_identity()
        attrs = extract_user_attrs(identity)
        # email is the unique identity key; normalize once so the ED membership
        # check, its cache, and provisioning all agree on casing (#348).
        attrs["email"] = normalize_email(attrs["email"])
    except ValueError as e:
        # Missing required attribute (e.g., mail)
        logger.warning("SAML ACS: missing attributes -- %s", str(e))
        return SamlLoginFailure.MISSING_ATTRIBUTES

    return attrs


def _reject_wrong_destination(authn_response: AuthnResponse) -> SamlLoginFailure | None:
    """Reject a Response whose Destination is not one of this SP's ACS URLs.

    pysaml2 7.5.x only LOGS this mismatch: StatusResponse._verify returns None
    and entity._parse_response discards that, handing back a usable response
    (#672). An absent Destination is allowed, matching pysaml2's own check.
    Returns the failure to reject with, or None to continue.
    """
    destination = authn_response.response.destination
    if destination and destination not in authn_response.return_addrs:
        logger.warning(
            "[SECURITY] SAML response rejected: Destination %r is not this SP's ACS %r",
            destination, authn_response.return_addrs,
        )
        return SamlLoginFailure.WRONG_DESTINATION
    return None


def _reject_replayed_assertion(authn_response: AuthnResponse) -> SamlLoginFailure | None:
    """Replay gate: allow_unsolicited=True (required for IdP-initiated SSO)
    means pysaml2 never matches InResponseTo, so a captured signed response
    would otherwise replay until its NotOnOrAfter lapses. Each assertion ID is
    accepted exactly once (see app/saml_replay.py). Returns the failure to
    reject with, or None to continue.
    """
    ids = assertion_ids(authn_response)
    if len(ids) > 1:
        # ponytail: single-assertion invariant -- pysaml2 7.5.5's
        # parse_assertion (saml2/response.py, "saml2int limitation") raises
        # InvalidAssertion unless a response carries exactly one plain OR
        # exactly one encrypted assertion, so this branch is reached only by a
        # mixed response (e.g. one plain plus one encrypted), which it rejects; a Lua
        # EXISTS-all-then-SET-all script (fakeredis would need the `lupa`
        # extra, not installed/pinned) is the upgrade path if
        # multi-assertion responses are ever accepted.
        #
        # Never fail open here, and ignore CVICHE_SAML_REPLAY_FAIL_CLOSED --
        # a response naming more than one assertion ID is untrustworthy on
        # its face (see the ponytail comment above), not merely unverifiable.
        logger.warning(
            "[SECURITY] SAML response carried %d assertion IDs (pysaml2 "
            "permits at most 1); rejecting rather than guessing which is authoritative",
            len(ids),
        )
        return SamlLoginFailure.MULTIPLE_ASSERTIONS
    replay_cache = get_replay_cache()
    if ids:
        if not replay_cache.check_and_record(ids[0], replay_ttl(authn_response)):
            # Reject like every other ACS failure: the benign replay case
            # is a human re-POSTing the ACS form (back button), not an attacker.
            logger.warning("[SECURITY] SAML assertion replay rejected (ID already presented)")
            return SamlLoginFailure.REPLAYED
        return None
    if replay_fail_closed():
        # Real pysaml2 responses always carry assertion IDs; a missing ID
        # means replay can't be verified, so reject when configured to fail
        # closed (prod).
        logger.warning("[SECURITY] SAML assertion carried no ID; failing closed -- rejecting")
        return SamlLoginFailure.NO_ASSERTION_ID
    # No ID: the gate is skipped BECAUSE CVICHE_SAML_REPLAY_FAIL_CLOSED is
    # explicitly opted out (local dev, when Valkey is not running) -- not
    # because "fail open" is this gate's default. Only stubbed parsers land
    # here in practice; a deployed instance never reaches this branch (see
    # app/saml_replay.py:check_deployed_posture, which refuses the opt-out
    # outright on a deployed instance).
    logger.warning(
        "SAML ACS: no assertion ID extractable; replay gate skipped "
        "(CVICHE_SAML_REPLAY_FAIL_CLOSED opt-out)"
    )
    return None


def _saml_role_from_ed(attrs: dict, db: Session) -> UserRole | SamlLoginFailure | None:
    """The role ED says this user should have, or None when ED authorization
    is disabled -- in which case the caller preserves whatever role the user
    already has. A SamlLoginFailure when ED denies the user or cannot answer."""
    if not get_config_value(db, "ed_enabled"):
        return None

    membership = partner_membership(attrs["cwid"], db)
    if membership is None:
        membership = _wcm_membership_from_ed(attrs, db)
        if isinstance(membership, SamlLoginFailure):
            return membership

    if not membership.in_access_group:
        logger.warning("SAML ACS: user %s not in ED access group", attrs["cwid"])
        logger.info(
            LOGIN_FAILED,
            extra={"cwid": attrs["cwid"], "reason": SamlLoginFailure.NOT_AUTHORIZED},
        )
        return SamlLoginFailure.NOT_AUTHORIZED

    return role_for_membership(membership)


def _wcm_membership_from_ed(attrs: dict, db: Session) -> MembershipResult | SamlLoginFailure:
    """A WCM user's ED membership, read fresh from LDAP; or
    DIRECTORY_UNAVAILABLE when ED is unconfigured or unavailable."""
    from app.config_loader import get_config

    ed_access_group = get_config_value(db, "ed_access_group") or ""
    ed_admin_group = get_config_value(db, "ed_admin_group") or ""
    ed_staff_group = get_config_value(db, "ed_staff_group") or ""
    ldap_url, source = get_config("ldap", "ED_LDAP_URL", default="")
    bind_dn, source = get_config("ldap", "ED_LDAP_BIND_DN", default="")
    bind_password = os.environ.get("ED_LDAP_BIND_PASSWORD", "")
    if not ldap_url or not bind_dn or not ed_access_group.strip():
        # An unset access group would otherwise reach LDAP as an empty
        # search_base; check_ed_membership now rejects it outright, so guard
        # here and fail closed with the same operator-visible error.
        logger.error(
            "ED not configured (need ED_LDAP_URL, ED_LDAP_BIND_DN, ed_access_group)"
        )
        return SamlLoginFailure.DIRECTORY_UNAVAILABLE

    ldap_cfg = LDAPConfig(
        ldap_url=ldap_url, bind_dn=bind_dn, bind_password=SecretStr(bind_password)
    )
    try:
        # use_cache=False: the login path is the stronger gate, so it reads
        # NEITHER cache -- not the 5-minute live one, not the 30-minute
        # stale one -- and always queries ED. A user removed from ED three
        # minutes ago must not be able to START a new session off a warm
        # live entry, nor off a last-known-good answer during an outage.
        # (The per-request re-check in auth.py keeps use_cache=True.)
        # check_ed_membership still WRITES both caches on success, so there
        # is no set_cached_membership here and the login keeps warming the
        # cache the per-request checks read.
        membership = check_ed_membership(
            cwid=attrs["cwid"],
            access_group=ed_access_group,
            admin_group=ed_admin_group,
            staff_group=ed_staff_group,
            cfg=ldap_cfg,
            use_cache=False,
        )
    except EdUnavailableError:
        logger.error("ED unavailable during SAML login for %s", attrs["cwid"], exc_info=True)
        return SamlLoginFailure.DIRECTORY_UNAVAILABLE
    return membership


def _saml_department_from_ed(cwid: str, db: Session) -> str | None:
    """The user's ED department, or None when ED is off, unconfigured, or the
    read fails. Best effort: login has already been authorized by
    _saml_role_from_ed, and a department is display metadata only."""
    if not get_config_value(db, "ed_enabled"):
        return None

    from app.config_loader import get_config

    ldap_url, _ = get_config("ldap", "ED_LDAP_URL", default="")
    bind_dn, _ = get_config("ldap", "ED_LDAP_BIND_DN", default="")
    if not ldap_url or not bind_dn:
        return None
    ldap_cfg = LDAPConfig(
        ldap_url=ldap_url, bind_dn=bind_dn,
        bind_password=SecretStr(os.environ.get("ED_LDAP_BIND_PASSWORD", "")),
    )
    return fetch_ed_department(cwid, ldap_cfg)


def _mint_saml_session(user: User, db: Session, cwid: str) -> str | SamlLoginFailure:
    """The session cookie value for a freshly-authenticated SAML user.

    A session store that cannot be written (or an unreadable epoch) is a login
    that cannot succeed: issuing the cookie anyway hands the user a credential
    no later request can resolve (#657 review, thread 16).
    """
    try:
        return create_session_cookie(user, db)
    except SessionStoreUnavailable:
        logger.error("Session store unavailable during SAML login for %s", cwid,
                     exc_info=True)
        logger.info(
            LOGIN_FAILED,
            extra={"cwid": cwid, "reason": SamlLoginFailure.SESSION_STORE_UNAVAILABLE},
        )
        logger.info(SESSION_STORE_UNAVAILABLE, extra={"reason": "start"})
        return SamlLoginFailure.SESSION_STORE_UNAVAILABLE
    except SessionEpochUnreadable:
        logger.error("Session epoch unreadable during SAML login for %s", cwid,
                     exc_info=True)
        logger.info(
            LOGIN_FAILED,
            extra={"cwid": cwid, "reason": SamlLoginFailure.SESSION_STATE_UNAVAILABLE},
        )
        return SamlLoginFailure.SESSION_STATE_UNAVAILABLE
