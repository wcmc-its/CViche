"""The per-user ED access-group check, shared by auth.py's per-request
re-check and the emailed-CV intake (#1298).

Lives in services/ so the worker can use it without importing app.auth, which
reads CVICHE_SESSION_SECRET at import. No HTTP types here: the caller turns the
exceptions below into whatever its context needs (auth.py a 401, intake a
rejected message). Fail closed: anything that is not a confirmed "in the group"
raises.
"""
import logging
import os
from collections.abc import Callable

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config_loader import get_config, get_config_value
from app.ed_group_lookup import EdUnavailableError, LDAPConfig, MembershipResult, check_ed_membership
from app.models import User

logger = logging.getLogger(__name__)

SAML_AUTH_METHOD = "saml"


class EdCwidMissing(Exception):
    """A SAML user with no CWID (provisioned before CWID anchoring)."""


class EdUnverifiable(Exception):
    """Membership could not be verified: directory down or no group configured."""


class EdNotInAccessGroup(Exception):
    """The directory answered and the user is not in the access group."""


def partner_membership(cwid: str, db: Session) -> MembershipResult | None:
    """The membership a partner-institution user gets, or None for a WCM user.

    A partner user's anchor is their scoped ePPN (#1452), which has no WCM ED
    entry. They are admitted with the user role when their scope is in
    ``ed_partner_scopes``, and never get admin or staff, which stay ED-gated
    (#1453). Read on every check, so removing a scope evicts its users on
    their next request.
    """
    _, at, scope = cwid.rpartition("@")
    if not at:
        return None
    allowed = {s.strip().lower() for s in (get_config_value(db, "ed_partner_scopes") or "").split(",")}
    return MembershipResult(in_access_group=scope in allowed, in_admin_group=False)


def verify_ed_access(
    user: User, db: Session,
    check_membership: Callable[..., MembershipResult] | None = None,
) -> MembershipResult | None:
    """Re-verify ``user``'s ED access-group membership.

    Returns None when no check applies (not a SAML user, or ED disabled), else
    the membership answer. Raises EdCwidMissing, EdUnverifiable or
    EdNotInAccessGroup. ``check_membership`` lets auth.py pass its own module's
    name so its existing patch points keep working.
    """
    if user.auth_method != SAML_AUTH_METHOD or not get_config_value(db, "ed_enabled"):
        return None
    if not user.cwid:
        raise EdCwidMissing(user.id)
    membership = partner_membership(user.cwid, db)
    if membership is not None:
        if not membership.in_access_group:
            raise EdNotInAccessGroup(user.cwid)
        return membership
    ed_access_group = get_config_value(db, "ed_access_group") or ""
    ed_admin_group = get_config_value(db, "ed_admin_group") or ""
    ed_staff_group = get_config_value(db, "ed_staff_group") or ""
    ldap_url, _ = get_config("ldap", "ED_LDAP_URL", default="")
    ldap_bind_dn, _ = get_config("ldap", "ED_LDAP_BIND_DN", default="")
    cfg = LDAPConfig(
        ldap_url=ldap_url, bind_dn=ldap_bind_dn,
        bind_password=SecretStr(os.environ.get("ED_LDAP_BIND_PASSWORD", "")),
    )
    try:
        # use_cache=True: a re-check of an already-authorized user may ride the
        # 5-minute live cache, and an ED outage must not evict them.
        membership = (check_membership or check_ed_membership)(
            cwid=user.cwid, access_group=ed_access_group, admin_group=ed_admin_group,
            staff_group=ed_staff_group, cfg=cfg, use_cache=True,
        )
    except EdUnavailableError as e:  # EdConfigurationError is a subclass
        raise EdUnverifiable("directory unavailable") from e
    except ValueError as e:  # no ED access group configured
        logger.error("ED access group is not configured; denying %s", user.cwid)
        raise EdUnverifiable("access group not configured") from e
    if not membership.in_access_group:
        raise EdNotInAccessGroup(user.cwid)
    return membership
