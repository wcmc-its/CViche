"""Enterprise Directory group membership check via LDAP with TTL cache."""
import os
import time
import logging
import threading
from dataclasses import dataclass
from urllib.parse import unquote
from ldap3 import Server, Connection, BASE, LEVEL, SUBTREE
from ldap3.utils.conv import escape_filter_chars
from ldap3.core.exceptions import LDAPException
from cachetools import TTLCache
from pydantic import SecretStr


class EdUnavailableError(Exception):
    """Raised when the Enterprise Directory is unreachable."""
    pass


@dataclass(frozen=True)
class LDAPConfig:
    """Connection settings for an ED bind, passed as one object instead of
    threading four positional args (incl. the credential) through every call.

    ``bind_password`` is a ``SecretStr``, so the credential never lands in a log
    line or traceback repr -- SecretStr renders as ``'**********'``, and this
    object's dataclass repr shows that masked form. Unwrap with
    ``.get_secret_value()`` only at the ldap3 boundary.
    """
    ldap_url: str
    bind_dn: str
    bind_password: SecretStr
    search_base: str = "dc=weill,dc=cornell,dc=edu"


logger = logging.getLogger(__name__)

# 5-minute TTL cache for group membership results
_group_cache = TTLCache(maxsize=1024, ttl=300)
_cache_lock = threading.Lock()

# Stale cache: fallback when ED is unavailable (keeps last-known good result)
_stale_cache: dict[str, dict] = {}
_STALE_MAX_AGE = 1800  # 30 minutes safety bound for stale entries


# ---------------------------------------------------------------------------
# Cache accessor functions
# ---------------------------------------------------------------------------

def get_cached_membership(email: str) -> dict | None:
    """Return cached membership result for email, or None on cache miss."""
    with _cache_lock:
        return _group_cache.get(email)


def set_cached_membership(email: str, membership: dict) -> None:
    """Store membership result in both live TTL cache and stale fallback."""
    with _cache_lock:
        _group_cache[email] = membership
    _stale_cache[email] = {**membership, "timestamp": time.time()}


def get_stale_membership(email: str) -> dict | None:
    """Return last-known membership from stale cache if within safety bound.

    Returns None if entry is older than _STALE_MAX_AGE seconds (30 min).
    """
    entry = _stale_cache.get(email)
    if entry is None:
        return None
    if time.time() - entry.get("timestamp", 0) > _STALE_MAX_AGE:
        return None
    # Return only the membership keys, not the timestamp
    return {
        "in_access_group": entry["in_access_group"],
        "in_admin_group": entry["in_admin_group"],
    }


def clear_cache() -> None:
    """Clear both live and stale caches (primarily for testing)."""
    with _cache_lock:
        _group_cache.clear()
    _stale_cache.clear()


# ---------------------------------------------------------------------------
# Internal LDAP helper
# ---------------------------------------------------------------------------

# LDAP URL scope tokens (RFC 4516) -> ldap3 scope constants
_LDAP_URL_SCOPE_MAP = {
    "": BASE,
    "base": BASE,
    "one": LEVEL,
    "sub": SUBTREE,
}


def _parse_memberurl(member_url: str) -> tuple[str, str, str] | None:
    """Parse an RFC 4516 LDAP URL into (base_dn, scope, filter).

    Returns None for malformed URLs. WCM's hybrid groupOfURLs pattern uses
    ldap:///uid=X,ou=people,...??base?(filter), so the scope and filter
    components carry real meaning -- the filter is the "stay-active" gate
    (e.g. (weillCornellEduPersonTypeCode=academic-faculty)).
    """
    if not member_url or not member_url.lower().startswith("ldap:///"):
        return None
    body = member_url[len("ldap:///"):]
    parts = body.split("?", 3)
    while len(parts) < 4:
        parts.append("")
    base_dn = unquote(parts[0])
    scope = _LDAP_URL_SCOPE_MAP.get(parts[2].lower(), BASE)
    ldap_filter = unquote(parts[3]) or "(objectClass=*)"
    return base_dn, scope, ldap_filter


def _dn_in_scope(user_dn: str, base_dn: str, scope) -> bool:
    """Whether user_dn falls under base_dn at the given LDAP URL scope.

    BASE   (?base?) -- user_dn must equal base_dn.
    LEVEL  (?one?)  -- user_dn must be a direct child of base_dn.
    SUBTREE(?sub?)  -- user_dn must be base_dn or anywhere beneath it.

    DN comparison is case-insensitive per the LDAP spec.
    """
    u = user_dn.strip().lower()
    b = base_dn.strip().lower()
    if scope == BASE:
        return u == b
    if scope == LEVEL:
        # direct child: strip the user's leftmost RDN, the remainder must be base.
        # ponytail: plain comma split -- WCM user RDNs (uid=cwid) carry no escaped
        # commas; switch to ldap3.utils.dn.parse_dn only if a value ever needs it.
        _, sep, rest = u.partition(",")
        return bool(sep) and rest == b
    # SUBTREE
    return u == b or u.endswith("," + b)


def _user_matches_memberurl(conn: Connection, member_url: str, user_dn: str) -> bool:
    """Return True if user_dn satisfies the membership rule encoded in member_url.

    Evaluates the rule against the USER's own entry -- a single base-scoped read of
    user_dn with the URL's filter -- rather than searching the URL's base/scope and
    scanning the results for the user. The latter breaks on broad dynamic rules
    (e.g. ?one? over ou=people matching ~10-15k people): WCM ED caps searches at
    ~500 entries (sizeLimitExceeded), so anyone past the first page is a silent
    false negative. Testing the user directly is O(1), size-limit-immune, and still
    handles the WCM hybrid ?base?-per-user pattern (scope gate == the old
    short-circuit, filter gate == the old stay-active check).
    """
    parsed = _parse_memberurl(member_url)
    if parsed is None:
        return False
    base_dn, scope, ldap_filter = parsed
    # Scope gate: is user_dn within the URL's base at the URL's scope? Cheap, no I/O.
    if not _dn_in_scope(user_dn, base_dn, scope):
        return False
    # Filter gate: does the user's own entry satisfy the URL's stay-active filter?
    try:
        conn.search(
            search_base=user_dn,
            search_filter=ldap_filter,
            search_scope=BASE,
            attributes=["dn"],
        )
    except LDAPException:
        return False
    return len(conn.entries) == 1

def _ldap_check_membership(email: str, group_dn: str, cfg: LDAPConfig) -> bool:
    """Check if a user (by email) is a member of the given LDAP group.
    Handles both schemas WCM ED uses:
      groupOfNames  -- static `member` attribute on the group, listing user DNs
      groupOfURLs   -- dynamic `memberURL` attribute, each URL resolving (via
                       ldap3 search) to zero or more users. WCM's house style is
                       a "hybrid" groupOfURLs with one memberURL per allowed user
                       and an attribute-based stay-active filter.
    Uses escape_filter_chars for injection protection.
    
    """
    safe_email = escape_filter_chars(email)
    search_filter = f"(mail={safe_email})"

    use_ssl = cfg.ldap_url.startswith("ldaps://") or ":636" in cfg.ldap_url
    conn = None
    try:
        server = Server(cfg.ldap_url, use_ssl=use_ssl, connect_timeout=5,
                        get_info="NONE")
        conn = Connection(server, user=cfg.bind_dn,
                          password=cfg.bind_password.get_secret_value(),
                          auto_bind=True, read_only=True, receive_timeout=5)

        # Find the user entry; collect user_dn and any memberOf attribute
        conn.search(
            search_base=cfg.search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["memberOf", "dn"],
        )

        if not conn.entries:
            logger.debug("No LDAP entry found for email=%s", email)
            return False

        user_entry = conn.entries[0]
        user_dn = str(user_entry.entry_dn)

        # Path 1: memberOf on the user (groupOfNames only; WCM ED does not
        # populate memberOf for groupOfURLs groups)
        try:
            member_of_raw = user_entry["memberOf"]
            if member_of_raw:
                member_of_list = [str(dn).lower() for dn in member_of_raw]
                if group_dn.lower() in member_of_list:
                    logger.debug("memberOf hit for %s in %s", email, group_dn)
                    return True
                # Fall through -- group could still be groupOfURLs even though
                # memberOf is populated for other (groupOfNames) groups.
        except (KeyError, LDAPException):
            pass

         # Path 2: read the group entry, handle both schemas
        logger.debug("Checking group entry directly for %s in %s", email, group_dn)
        conn.search(
            search_base=group_dn,
            search_filter="(|(objectClass=groupOfNames)(objectClass=groupOfURLs))",
            search_scope=BASE,
            attributes=["member", "memberURL"],
        )

        if not conn.entries:
            logger.debug("Group %s not found or wrong objectClass", group_dn)
            return False

        group_entry = conn.entries[0]

        # Path 2a: static `member` list (groupOfNames)
        try:
            members_raw = group_entry["member"]
            if members_raw:
                members = [str(m).lower() for m in members_raw]
                if user_dn.lower() in members:
                    logger.debug("Group `member` hit for %s in %s", email, group_dn)
                    return True
        except (KeyError, LDAPException):
            pass

        # Path 2b: dynamic `memberURL` list (groupOfURLs) -- evaluate each URL
        try:
            member_urls_raw = group_entry["memberURL"]
            if member_urls_raw:
                for url in member_urls_raw:
                    if _user_matches_memberurl(conn, str(url), user_dn):
                        logger.debug("memberURL hit for %s in %s via %s",
                                     email, group_dn, url)
                        return True
        except (KeyError, LDAPException):
            pass

        logger.debug("User %s not found in group %s via any method", email, group_dn)
        return False

    except (LDAPException, OSError, ConnectionError) as exc:
        raise EdUnavailableError(str(exc)) from exc
    finally:
        if conn:
            try:
                conn.unbind()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_ed_membership(email: str, access_group: str, admin_group: str,
                        cfg: LDAPConfig) -> dict:
    """Check user's membership in access and admin ED groups.

    Returns dict with keys:
        in_access_group: bool -- whether user is in the access group
        in_admin_group: bool  -- whether user is in the admin group

    Admin group is only checked if user is in the access group AND
    admin_group is non-empty.  Admin group membership does NOT imply
    access (per locked decision).

    Raises EdUnavailableError if LDAP is unreachable.
    """
    try:
        in_access = _ldap_check_membership(email, access_group, cfg)
    except (LDAPException, OSError, ConnectionError) as exc:
        raise EdUnavailableError(str(exc)) from exc

    in_admin = False
    if in_access and admin_group:
        try:
            in_admin = _ldap_check_membership(email, admin_group, cfg)
        except (LDAPException, OSError, ConnectionError) as exc:
            raise EdUnavailableError(str(exc)) from exc

    return {"in_access_group": in_access, "in_admin_group": in_admin}
