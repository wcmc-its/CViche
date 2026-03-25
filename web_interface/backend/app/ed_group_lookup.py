"""Enterprise Directory group membership check via LDAP with TTL cache."""
import os
import time
import logging
import threading
from ldap3 import Server, Connection, SUBTREE
from ldap3.utils.conv import escape_filter_chars
from ldap3.core.exceptions import LDAPException
from cachetools import TTLCache


class EdUnavailableError(Exception):
    """Raised when the Enterprise Directory is unreachable."""
    pass


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

def _ldap_check_membership(email: str, group_dn: str, ldap_url: str,
                            bind_dn: str, bind_password: str,
                            search_base: str) -> bool:
    """Check if a user (by email) is a member of the given LDAP group.

    Uses escape_filter_chars for injection protection.
    Tries memberOf attribute first, falls back to querying the group directly.
    """
    safe_email = escape_filter_chars(email)
    search_filter = f"(mail={safe_email})"

    use_ssl = ldap_url.startswith("ldaps://") or ":636" in ldap_url
    conn = None
    try:
        server = Server(ldap_url, use_ssl=use_ssl, connect_timeout=5,
                        get_info="NONE")
        conn = Connection(server, user=bind_dn, password=bind_password,
                          auto_bind=True, read_only=True, receive_timeout=5)

        # Search for the user entry to get memberOf attribute
        conn.search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["memberOf", "dn"],
        )

        if not conn.entries:
            logger.debug("No LDAP entry found for email=%s", email)
            return False

        user_entry = conn.entries[0]
        user_dn = str(user_entry.entry_dn)

        # Try memberOf attribute first
        member_of_list = []
        try:
            member_of_raw = user_entry["memberOf"]
            if member_of_raw:
                member_of_list = [str(dn) for dn in member_of_raw]
        except (KeyError, LDAPException):
            pass

        if member_of_list:
            result = group_dn.lower() in [dn.lower() for dn in member_of_list]
            logger.debug("memberOf check for %s in %s: %s", email, group_dn, result)
            return result

        # Fallback: query the group DN itself for member attribute
        logger.debug("memberOf empty/missing for %s, checking group directly", email)
        conn.search(
            search_base=group_dn,
            search_filter=f"(&(objectClass=groupOfNames)(cn=*))",
            search_scope=SUBTREE,
            attributes=["member"],
        )

        if conn.entries:
            group_entry = conn.entries[0]
            try:
                members_raw = group_entry["member"]
                if members_raw:
                    members = [str(m).lower() for m in members_raw]
                    result = user_dn.lower() in members
                    logger.debug("Direct group member check for %s: %s", email, result)
                    return result
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
                        ldap_url: str, bind_dn: str, bind_password: str,
                        search_base: str = "dc=weill,dc=cornell,dc=edu") -> dict:
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
        in_access = _ldap_check_membership(
            email, access_group, ldap_url, bind_dn, bind_password, search_base
        )
    except (LDAPException, OSError, ConnectionError) as exc:
        raise EdUnavailableError(str(exc)) from exc

    in_admin = False
    if in_access and admin_group:
        try:
            in_admin = _ldap_check_membership(
                email, admin_group, ldap_url, bind_dn, bind_password, search_base
            )
        except (LDAPException, OSError, ConnectionError) as exc:
            raise EdUnavailableError(str(exc)) from exc

    return {"in_access_group": in_access, "in_admin_group": in_admin}
