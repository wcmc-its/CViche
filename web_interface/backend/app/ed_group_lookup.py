"""Enterprise Directory group membership check via LDAP with TTL cache."""
import time
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator
from urllib.parse import unquote, urlparse
from ldap3 import Server, Connection, BASE, LEVEL, SUBTREE
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn
from ldap3.core.exceptions import (
    LDAPException,
    LDAPBindError,
    LDAPInvalidCredentialsResult,
    LDAPStrongerAuthRequiredResult,
    LDAPInsufficientAccessRightsResult,
    LDAPNoSuchObjectResult,
    LDAPInvalidDnError,
)
from cachetools import TTLCache
from pydantic import SecretStr


class EdUnavailableError(Exception):
    """Raised when the Enterprise Directory is unreachable."""
    pass


class EdConfigurationError(EdUnavailableError):
    """Raised when ED rejects *our* bind -- bad service-account credentials or
    insufficient rights -- as opposed to the directory being down.

    Deliberately a SUBCLASS of EdUnavailableError: every existing
    ``except EdUnavailableError`` handler keeps failing closed to
    ``directory_unavailable`` instead of this becoming an uncaught 500. Only the
    exception type and the log line distinguish the two cases, so an operator
    can tell "fix our config" from "wait for the directory to come back".
    """
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


@dataclass(frozen=True)
class MembershipResult:
    """A user's membership in the ED groups that gate this app.

    ``in_admin_group`` and ``in_staff_group`` are only ever True when
    ``in_access_group`` is True -- neither admin nor staff membership implies
    access (locked decision). ``in_staff_group`` is also only resolved for a
    user who is not an admin: admin wins, so its LDAP search is skipped.
    """
    in_access_group: bool
    in_admin_group: bool
    in_staff_group: bool = False


@dataclass(frozen=True)
class MembershipCacheKey:
    """Cache identity for one membership answer.

    The group DNs are part of the key, not just the CWID: an answer computed
    against one group configuration must never be served after an admin points
    the app at a different group.
    """
    cwid: str
    access_group: str
    admin_group: str
    staff_group: str = ""


@dataclass
class _StaleEntry:
    """A last-known-good membership answer plus when it was recorded."""
    membership: MembershipResult
    timestamp: float


logger = logging.getLogger(__name__)

# LDAP connection budget: ED is on-campus, so a bind that hasn't completed in
# five seconds is a fault, not slowness.
_CONNECT_TIMEOUT_SECONDS = 5
_RECEIVE_TIMEOUT_SECONDS = 5
_LDAPS_PORT_SUFFIX = ":636"

# The only URL schemes an ED bind may use. Anything else (http://, file://, a
# bare hostname) is a configuration mistake, not a directory outage.
_ALLOWED_LDAP_SCHEMES = ("ldap", "ldaps")

# Upper bound on how many memberURL entries in ONE group evaluation may each
# cost an actual LDAP search. Deliberately a cap on SEARCHES, not on URLs
# scanned: _dn_in_scope is a pure string test with no I/O and runs first, so in
# WCM's hybrid pattern (one ?base? memberURL per allowed user) at most one URL
# per evaluation ever reaches the server however long the attribute list is.
# Capping the scan instead would skip a legitimate member who happens to sit
# past the cap in the list -- an approval silently turned into a denial.
_MAX_MEMBERURL_SEARCHES = 25

# 5-minute TTL cache for group membership results
_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ENTRIES = 1024
_group_cache = TTLCache(maxsize=_CACHE_MAX_ENTRIES, ttl=_CACHE_TTL_SECONDS)
_cache_lock = threading.Lock()

# Stale cache: fallback when ED is unavailable (keeps last-known good result).
# Keyed by MembershipCacheKey, like the live cache above.
_stale_cache: dict[MembershipCacheKey, _StaleEntry] = {}
_STALE_MAX_AGE = 1800  # 30 minutes safety bound for stale entries

# Single-flight: one in-flight LDAP query per cache key. Concurrent misses on
# the same key wait on the key's lock and then re-read the cache the winner
# populated, instead of each opening its own bind.
_key_locks: dict[MembershipCacheKey, threading.Lock] = {}
_key_lock_waiters: dict[MembershipCacheKey, int] = {}


# ---------------------------------------------------------------------------
# Cache accessor functions
# ---------------------------------------------------------------------------

def get_cached_membership(cwid: str, access_group: str, admin_group: str,
                          staff_group: str = "") -> MembershipResult | None:
    """Return cached membership for (cwid, groups), or None on cache miss."""
    key = MembershipCacheKey(cwid, access_group, admin_group, staff_group)
    with _cache_lock:
        return _group_cache.get(key)


def set_cached_membership(cwid: str, access_group: str, admin_group: str,
                          membership: MembershipResult,
                          staff_group: str = "") -> None:
    """Store membership result in both live TTL cache and stale fallback."""
    key = MembershipCacheKey(cwid, access_group, admin_group, staff_group)
    now = time.time()
    with _cache_lock:
        _group_cache[key] = membership
        _stale_cache[key] = _StaleEntry(membership=membership, timestamp=now)
        # ponytail: full sweep per write, fine at one entry per CWID seen by a
        # pod; if that ever gets large, swap _stale_cache for a bounded TTLCache.
        for k in [k for k, v in _stale_cache.items()
                  if now - v.timestamp > _STALE_MAX_AGE]:
            del _stale_cache[k]


def get_stale_membership(cwid: str, access_group: str, admin_group: str,
                         staff_group: str = "") -> MembershipResult | None:
    """Return last-known membership for (cwid, groups) if within the age bound.

    Returns None if entry is older than _STALE_MAX_AGE seconds (30 min). The
    read and the expiry check are both under _cache_lock, so a concurrent
    set_cached_membership/clear_cache cannot swap the entry out mid-check.
    """
    key = MembershipCacheKey(cwid, access_group, admin_group, staff_group)
    with _cache_lock:
        entry = _stale_cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.timestamp > _STALE_MAX_AGE:
            return None
        return entry.membership


def clear_cache() -> None:
    """Clear both live and stale caches (primarily for testing)."""
    with _cache_lock:
        _group_cache.clear()
        _stale_cache.clear()


@contextmanager
def _single_flight(key: MembershipCacheKey) -> Iterator[None]:
    """Serialize LDAP work for one cache key across threads.

    The per-key lock is held across the LDAP call; the global _cache_lock is
    only held for the bookkeeping below, never across I/O. The lock entry is
    reference-counted and dropped when the last waiter leaves, so _key_locks
    cannot grow one entry per CWID for the life of the pod.
    """
    with _cache_lock:
        lock = _key_locks.get(key)
        if lock is None:
            lock = _key_locks[key] = threading.Lock()
        _key_lock_waiters[key] = _key_lock_waiters.get(key, 0) + 1
    try:
        with lock:
            yield
    finally:
        with _cache_lock:
            _key_lock_waiters[key] -= 1
            if _key_lock_waiters[key] == 0:
                del _key_lock_waiters[key]
                del _key_locks[key]


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

# ED rejected *us*, not the user: our bind DN, our password, or our rights.
# Distinct from the directory being unreachable, so it is raised as
# EdConfigurationError (a subclass) and logged at error with a fix hint.
_ED_CONFIG_ERRORS = (
    LDAPBindError,
    LDAPInvalidCredentialsResult,
    LDAPStrongerAuthRequiredResult,
    LDAPInsufficientAccessRightsResult,
)


def _parse_memberurl(member_url: str) -> tuple[str, str, str] | None:
    """Parse an RFC 4516 LDAP URL into (base_dn, scope, filter).

    Returns None for malformed URLs -- including an unrecognized scope token,
    which must not silently degrade to BASE. WCM's hybrid groupOfURLs pattern
    uses ldap:///uid=X,ou=people,...??base?(filter), so the scope and filter
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
    scope_token = parts[2].lower()
    if scope_token not in _LDAP_URL_SCOPE_MAP:
        # An unknown token means we cannot know what the URL scopes over.
        # Defaulting it to BASE would be a guess; treat the URL as malformed.
        logger.warning("Unrecognized LDAP URL scope token %r in memberURL",
                       parts[2])
        return None
    scope = _LDAP_URL_SCOPE_MAP[scope_token]
    ldap_filter = unquote(parts[3]) or "(objectClass=*)"
    return base_dn, scope, ldap_filter


def _dn_in_scope(user_dn: str, base_dn: str, scope) -> bool:
    """Whether user_dn falls under base_dn at the given LDAP URL scope.

    BASE   (?base?) -- user_dn must equal base_dn.
    LEVEL  (?one?)  -- user_dn must be a direct child of base_dn.
    SUBTREE(?sub?)  -- user_dn must be base_dn or anywhere beneath it.

    Parses both DNs into RDN-component tuples with ldap3.utils.dn.parse_dn
    (#331) rather than comparing the raw strings: a plain comma split reads an
    escaped comma inside an RDN value as a component boundary, which both
    misses a real child (an unescaped-looking split that doesn't land on a
    real RDN edge) and can widen SUBTREE to match a DN that only *looks* like
    it ends in base_dn once you split on every comma. Components are compared
    case-insensitively per the LDAP spec; parse_dn(strip=True) also settles
    the whitespace-after-comma variance a DN string may carry. strip=True
    does change what parses: it also rejects a value ending in an escaped
    space (`ou=p\\ `), which strip=False accepts. That can only turn such a
    DN into "not in scope", the fail-closed side.

    Fail-closed: a DN that won't parse is "not in scope" (False), and the
    caller chain treats False as deny, not as "skip this check":
    _memberurl_search_filter returns None, _user_matches_memberurl returns
    False, and that memberURL grants no membership.
    """
    try:
        base_rdns = parse_dn(base_dn, strip=True)
    except LDAPInvalidDnError:
        # base_dn comes from the group's memberURL in the directory: a
        # malformed group definition, not user input.
        logger.error("memberURL base DN %r does not parse; treating as not in scope", base_dn)
        return False
    try:
        user_rdns = parse_dn(user_dn, strip=True)
    except LDAPInvalidDnError:
        # Untrusted input. The DN itself (it carries the CWID) stays out of the log.
        logger.warning("user DN does not parse; treating as not in scope")
        return False
    u = tuple((rdn_type.lower(), value.lower()) for rdn_type, value, _ in user_rdns)
    b = tuple((rdn_type.lower(), value.lower()) for rdn_type, value, _ in base_rdns)
    if scope == BASE:
        return u == b
    if scope == LEVEL:
        # direct child: strip the user's leftmost RDN, the remainder must be base.
        return len(u) == len(b) + 1 and u[1:] == b
    # SUBTREE: base_dn's RDN sequence must be exactly the rightmost slice of
    # user_dn's -- i.e. user_dn is base_dn or a descendant of it.
    return len(u) >= len(b) and u[len(u) - len(b):] == b


def _memberurl_search_filter(member_url: str, user_dn: str) -> str | None:
    """The filter this memberURL would apply to user_dn's own entry, or None.

    None means evaluating the URL against this user costs NO LDAP search: the
    URL is malformed, or its base/scope does not cover user_dn. Both gates are
    pure string work. Split out of _user_matches_memberurl so the caller can
    budget the searches a group evaluation triggers (_MAX_MEMBERURL_SEARCHES)
    before spending one. The budgeting caller parses each URL twice -- once
    here, once inside _user_matches_memberurl -- which is deliberate: both
    passes are I/O-free and deterministic, and threading the parsed filter
    through would widen _user_matches_memberurl's signature for no gain.
    """
    parsed = _parse_memberurl(member_url)
    if parsed is None:
        return None
    base_dn, scope, ldap_filter = parsed
    # Scope gate: is user_dn within the URL's base at the URL's scope? Cheap, no I/O.
    if not _dn_in_scope(user_dn, base_dn, scope):
        return None
    return ldap_filter


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
    ldap_filter = _memberurl_search_filter(member_url, user_dn)
    if ldap_filter is None:
        return False
    # Filter gate: does the user's own entry satisfy the URL's stay-active filter?
    try:
        conn.search(
            search_base=user_dn,
            search_filter=ldap_filter,
            search_scope=BASE,
            attributes=["dn"],
        )
    except LDAPNoSuchObjectResult:
        # The DN genuinely is not there. Verified against ldap3 2.9.1: with the
        # default Connection(raise_exceptions=False) this never fires -- a
        # missing DN comes back as search()->False with zero entries -- but if
        # that default is ever flipped, an absent entry is a "not a member"
        # answer, not an outage.
        return False
    except LDAPException as exc:
        # Anything else here is a transport/protocol fault (timeout, reset,
        # session terminated). Returning False would read as "not a member" and
        # silently deny a legitimate user; raise so the stale fallback can
        # engage instead.
        raise EdUnavailableError(str(exc)) from exc
    return len(conn.entries) == 1


def _validate_ldap_url(ldap_url: str) -> None:
    """Reject a malformed ED URL before a Server is built or a socket opened.

    Raises EdConfigurationError -- a subclass of EdUnavailableError -- so both
    call sites keep failing closed on their existing handler (see D8) while an
    operator still gets a log line that says "fix the config", not "the
    directory is down". urlparse, not a regex: the stdlib already knows how to
    split a scheme from a host.
    """
    parsed = urlparse(ldap_url or "")
    if parsed.scheme not in _ALLOWED_LDAP_SCHEMES or not parsed.hostname:
        logger.error(
            "ED_LDAP_URL is not a usable LDAP URL (%r) -- expected "
            "ldap://host[:port] or ldaps://host[:port]", ldap_url
        )
        raise EdConfigurationError(
            f"Invalid ED LDAP URL: {ldap_url!r}; expected an ldap:// or "
            f"ldaps:// URL with a host"
        )


@contextmanager
def _bind(cfg: LDAPConfig) -> Iterator[Connection]:
    """Yield one bound, read-only ED connection and unbind it on the way out.

    One authorization decision = one bind, however many groups it consults.
    The URL is validated first, so a misconfigured ED_LDAP_URL fails as an
    EdConfigurationError instead of a socket attempt against nothing. Also the
    single place LDAP faults are classified (see EdConfigurationError).
    """
    _validate_ldap_url(cfg.ldap_url)
    use_ssl = (cfg.ldap_url.startswith("ldaps://")
               or _LDAPS_PORT_SUFFIX in cfg.ldap_url)
    conn = None
    try:
        server = Server(cfg.ldap_url, use_ssl=use_ssl,
                        connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                        get_info="NONE")
        conn = Connection(server, user=cfg.bind_dn,
                          password=cfg.bind_password.get_secret_value(),
                          auto_bind=True, read_only=True,
                          receive_timeout=_RECEIVE_TIMEOUT_SECONDS)
        yield conn
    except _ED_CONFIG_ERRORS as exc:
        logger.error(
            "ED rejected our bind as %s -- check ED_LDAP_BIND_DN / "
            "ED_LDAP_BIND_PASSWORD and the service account's rights: %s",
            cfg.bind_dn, exc
        )
        raise EdConfigurationError(str(exc)) from exc
    except (LDAPException, OSError, ConnectionError) as exc:
        logger.warning("ED directory unreachable at %s: %s", cfg.ldap_url, exc)
        raise EdUnavailableError(str(exc)) from exc
    finally:
        if conn:
            try:
                conn.unbind()
            except LDAPException:
                # Best effort -- the connection is being discarded either way.
                logger.debug("ED unbind failed", exc_info=True)


def _ldap_check_membership(cwid: str, group_dn: str, cfg: LDAPConfig,
                           conn: Connection) -> bool:
    """Check if a user (by CWID) is a member of the given LDAP group.

    Takes an already-bound `conn` (see `_bind`) so that both group checks for one
    authorization decision share a single bind. `cfg` supplies the user search base.

    Handles both schemas WCM ED uses:
      groupOfNames  -- static `member` attribute on the group, listing user DNs
      groupOfURLs   -- dynamic `memberURL` attribute, each URL resolving (via
                       ldap3 search) to zero or more users. WCM's house style is
                       a "hybrid" groupOfURLs with one memberURL per allowed user
                       and an attribute-based stay-active filter.
    Uses escape_filter_chars for injection protection.

    Resolves the user by `(uid=<cwid>)` -- every WCM identity has a uid, so
    this works for affiliates with no `mail` attribute (unlike the old
    mail-based lookup).
    """
    safe_cwid = escape_filter_chars(cwid)
    search_filter = f"(uid={safe_cwid})"

    # Find the user entry; collect user_dn and any memberOf attribute
    conn.search(
        search_base=cfg.search_base,
        search_filter=search_filter,
        search_scope=SUBTREE,
        attributes=["memberOf", "dn"],
    )

    if not conn.entries:
        logger.warning(
            "No ED directory entry for cwid=%s (not in directory -- "
            "distinct from not-in-group)", cwid
        )
        return False

    if len(conn.entries) > 1:
        # JUDGEMENT CALL: uid is supposed to be unique in ED, so more than one
        # hit is a directory-hygiene defect. We warn loudly and proceed with the
        # first entry rather than failing closed -- denying here would lock a
        # real user out of the app over someone else's duplicate record, and the
        # membership answer for entries[0] is still evaluated on its own merits.
        logger.warning(
            "Ambiguous ED lookup for cwid=%s: %d entries matched (uid=) -- "
            "using the first; uid should be unique",
            cwid, len(conn.entries)
        )

    user_entry = conn.entries[0]
    user_dn = str(user_entry.entry_dn)

    # Path 1: memberOf on the user (groupOfNames only; WCM ED does not
    # populate memberOf for groupOfURLs groups)
    try:
        member_of_raw = user_entry["memberOf"]
        if member_of_raw:
            member_of_list = [str(dn).lower() for dn in member_of_raw]
            if group_dn.lower() in member_of_list:
                logger.debug("memberOf hit for %s in %s", cwid, group_dn)
                return True
            # Fall through -- group could still be groupOfURLs even though
            # memberOf is populated for other (groupOfNames) groups.
    except KeyError:
        # Expected: the entry simply carries no memberOf attribute. ED schemas
        # vary by person type, and Path 2 below answers the question anyway, so
        # continuing is correct. (ldap3 raises LDAPKeyError, a KeyError.)
        pass

    # Path 2: read the group entry, handle both schemas
    logger.debug("Checking group entry directly for %s in %s", cwid, group_dn)
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
                logger.debug("Group `member` hit for %s in %s", cwid, group_dn)
                return True
    except KeyError:
        # Expected: a groupOfURLs group has no `member` attribute. Path 2b is
        # the branch that answers for that schema, so continuing is correct.
        pass

    # Path 2b: dynamic `memberURL` list (groupOfURLs) -- evaluate each URL
    try:
        member_urls_raw = group_entry["memberURL"]
    except KeyError:
        # Expected: a groupOfNames group has no `memberURL` attribute. Path 2a
        # already answered for that schema, so this is a genuine non-member.
        member_urls_raw = None
    if member_urls_raw:
        searches = 0
        for url in member_urls_raw:
            url = str(url)
            if _memberurl_search_filter(url, user_dn) is not None:
                # This URL would hit the server. Scanning stays unbounded and
                # cheap; only the I/O is budgeted (_MAX_MEMBERURL_SEARCHES).
                if searches >= _MAX_MEMBERURL_SEARCHES:
                    logger.warning(
                        "memberURL search cap (%d) reached evaluating group %s "
                        "for cwid=%s -- stopping; the group is malformed or "
                        "far larger than this pattern expects",
                        _MAX_MEMBERURL_SEARCHES, group_dn, cwid
                    )
                    break
                searches += 1
            if _user_matches_memberurl(conn, url, user_dn):
                logger.debug("memberURL hit for %s in %s via %s",
                             cwid, group_dn, url)
                return True

    logger.debug("User %s not found in group %s via any method", cwid, group_dn)
    return False


def _query_ed(cwid: str, access_group: str, admin_group: str,
              cfg: LDAPConfig, staff_group: str = "") -> MembershipResult:
    """Resolve the group memberships over a single bind.

    Admin is only checked when access is True AND admin_group is non-empty:
    admin group membership does NOT imply access (per locked decision). Staff
    follows the same rule, and is checked only for a non-admin -- admin wins,
    so an admin's staff membership is never needed. An empty staff_group means
    nobody is staff.
    """
    with _bind(cfg) as conn:
        in_access = _ldap_check_membership(cwid, access_group, cfg, conn)
        in_admin = False
        if in_access and admin_group:
            in_admin = _ldap_check_membership(cwid, admin_group, cfg, conn)
        in_staff = False
        if in_access and not in_admin and staff_group:
            in_staff = _ldap_check_membership(cwid, staff_group, cfg, conn)
    return MembershipResult(
        in_access_group=in_access, in_admin_group=in_admin, in_staff_group=in_staff,
    )


# ED attributes that carry a person's department name, in preference order.
# Confirmed against a live ED record (2026-09-30): the PRIMARY department is a
# single-valued display name ("Library"); weillCornellEduDepartment is the
# multi-valued list of every department the person belongs to.
_ED_PRIMARY_DEPARTMENT_ATTR = "weillCornellEduPrimaryDepartment"
_ED_ALL_DEPARTMENTS_ATTR = "weillCornellEduDepartment"
_ED_DEPARTMENT_ATTRS = (_ED_PRIMARY_DEPARTMENT_ATTR, _ED_ALL_DEPARTMENTS_ATTR)

# users.department column width (models.User.department).
DEPARTMENT_MAX_LENGTH = 255


def pick_department(attributes: dict[str, list]) -> str | None:
    """The department name to store from an ED entry's attribute dict.

    Prefers the primary department, falls back to the first listed department,
    and returns None when neither carries a non-blank value. Pure function so
    the choice is testable without a directory.
    """
    for attr in _ED_DEPARTMENT_ATTRS:
        for value in attributes.get(attr) or []:
            cleaned = str(value).strip()
            if cleaned:
                return cleaned[:DEPARTMENT_MAX_LENGTH]
    return None


def fetch_ed_department(cwid: str, cfg: LDAPConfig) -> str | None:
    """The user's department name from ED, or None when it cannot be read.

    Never raises for a directory fault: the department is display metadata, so
    an ED error here must not change the login outcome (the membership check,
    which does gate login, has already answered). The fault is logged with its
    traceback and the caller keeps whatever department it already has.
    """
    try:
        with _bind(cfg) as conn:
            conn.search(
                search_base=cfg.search_base,
                search_filter=f"(uid={escape_filter_chars(cwid)})",
                search_scope=SUBTREE,
                attributes=list(_ED_DEPARTMENT_ATTRS),
            )
            if not conn.entries:
                return None
            return pick_department(conn.entries[0].entry_attributes_as_dict)
    except (EdUnavailableError, LDAPException):
        logger.warning("Could not read ED department for cwid=%s", cwid,
                       exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_startup_config(ldap_url: str, bind_dn: str, bind_password: str,
                            access_group: str) -> None:
    """Validate the ED config an authorization decision needs, at startup.

    Every field required to reach `_bind` is checked today only per-request
    (saml_service.py, auth.py both build an LDAPConfig from the same env/DB
    reads and let a bad one surface as the first login's bind failure), and
    ED_LDAP_BIND_PASSWORD is not checked anywhere -- an empty password reaches
    ldap3 and only then maps to EdConfigurationError (#330). Call this from
    main.py's lifespan, gated on ed_enabled, so a misconfigured deployment
    fails at boot instead of on the first SAML login.

    Raises EdConfigurationError naming every missing/invalid field at once
    (not just the first found) so an operator fixes the whole deployment in
    one pass, instead of one field per restart.
    """
    problems = []
    try:
        _validate_ldap_url(ldap_url)
    except EdConfigurationError as exc:
        problems.append(str(exc))
    if not bind_dn or not bind_dn.strip():
        problems.append("ED_LDAP_BIND_DN is not set")
    if not bind_password or not bind_password.strip():
        problems.append("ED_LDAP_BIND_PASSWORD is not set")
    if not access_group or not access_group.strip():
        problems.append("ed_access_group is not set")
    if problems:
        raise EdConfigurationError("; ".join(problems))


def check_ed_membership(cwid: str, access_group: str, admin_group: str,
                        cfg: LDAPConfig, *,
                        use_cache: bool = True,
                        staff_group: str = "") -> MembershipResult:
    """Return the user's membership in the access, admin and staff ED groups.

    Keyed on CWID (resolved via `(uid=<cwid>)`), not email. This is the whole
    cache-aside flow, so both call sites share one implementation:

      1. live cache hit -> returned as-is, no LDAP.
      2. miss -> one bind, both groups, populate live + stale caches.
      3. EdUnavailableError during the LDAP work -> if a stale entry within
         _STALE_MAX_AGE exists, return it; otherwise re-raise.

    `use_cache` governs BOTH reads, not just the stale one:

      use_cache=True  -- steps 1-3 above. The per-request re-check (`auth.py`)
                         passes this: an answer up to _CACHE_TTL_SECONDS old is
                         fine for a session that is already authorized, and an
                         ED outage must not evict it.
      use_cache=False -- skip the live read AND the stale read; always query ED.
                         The login path (`saml_service.py`) passes this, because
                         minting a NEW session is the stronger gate: a user
                         whose access was revoked two minutes ago must not be
                         let in off a warm cache entry, and must not be let in
                         off a last-known-good answer during an outage either.
                         Both caches are still WRITTEN on success, so the login
                         keeps warming them for the per-request checks that
                         follow.

    Single-flight around the LDAP call applies in both modes.

    Raises ValueError if `access_group` is empty/whitespace -- an empty group DN
    would go to LDAP as an empty search_base and come back False, which is
    fail-closed only by accident. `admin_group` and `staff_group` may
    legitimately be empty (documented short-circuit: no such group configured,
    so nobody holds that role).

    Raises EdUnavailableError if LDAP is unreachable and no stale answer is
    usable, or EdConfigurationError (a subclass) if ED rejected our bind.
    """
    if not access_group or not access_group.strip():
        raise ValueError(
            "access_group must be a non-empty ED group DN; refusing to run an "
            "authorization check with no group configured"
        )

    if use_cache:
        cached = get_cached_membership(cwid, access_group, admin_group, staff_group)
        if cached is not None:
            return cached

    key = MembershipCacheKey(cwid, access_group, admin_group, staff_group)
    with _single_flight(key):
        if use_cache:
            # A concurrent miss on the same key may have populated the cache
            # while we waited on the lock -- reuse its answer instead of
            # re-querying. Skipped under use_cache=False for the same reason
            # the first read is: this path must always see ED itself.
            cached = get_cached_membership(cwid, access_group, admin_group, staff_group)
            if cached is not None:
                return cached

        try:
            membership = _query_ed(cwid, access_group, admin_group, cfg, staff_group)
        except EdUnavailableError:
            if use_cache:
                stale = get_stale_membership(cwid, access_group, admin_group, staff_group)
                if stale is not None:
                    logger.warning(
                        "ED unavailable for cwid=%s -- serving last-known "
                        "membership from the stale cache", cwid
                    )
                    return stale
            raise

        # Written in BOTH modes: a fresh login warms the cache the subsequent
        # per-request checks read.
        set_cached_membership(cwid, access_group, admin_group, membership, staff_group)
        return membership
