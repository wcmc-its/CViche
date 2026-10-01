"""Tests for Enterprise Directory group membership check module."""
import json
import os
import threading
import time
from dataclasses import FrozenInstanceError
from unittest.mock import patch, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db

from app.ed_group_lookup import (
    DEPARTMENT_MAX_LENGTH,
    fetch_ed_department,
    pick_department,
    check_ed_membership,
    get_cached_membership,
    set_cached_membership,
    get_stale_membership,
    clear_cache,
    EdUnavailableError,
    EdConfigurationError,
    LDAPConfig,
    MembershipCacheKey,
    MembershipResult,
    _bind,
    _ldap_check_membership,
    _memberurl_search_filter,
    _parse_memberurl,
    _user_matches_memberurl,
    _dn_in_scope,
    _validate_ldap_url,
    validate_startup_config,
    _group_cache,
    _stale_cache,
    _MAX_MEMBERURL_SEARCHES,
    _STALE_MAX_AGE,
)
from ldap3 import BASE, LEVEL, SUBTREE
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPCommunicationError,
    LDAPInvalidCredentialsResult,
    LDAPSocketOpenError,
    LDAPSocketReceiveError,
)
from ldap3.utils.conv import escape_filter_chars
from pydantic import SecretStr


# Common LDAP connection config for the membership calls. Spread as **LDAP_PARAMS
# into the (cwid, group_dn(s), ..., cfg) signature -- e.g.
# check_ed_membership(cwid, access_group, admin_group, **LDAP_PARAMS) or
# _ldap_check_membership(cwid=..., group_dn=..., **LDAP_PARAMS, conn=mock_conn).
# The module is keyed on CWID (resolved via `(uid=<cwid>)`), not email.
LDAP_PARAMS = {
    "cfg": LDAPConfig(
        ldap_url="ldaps://ed.weill.cornell.edu:636",
        bind_dn="cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
        bind_password=SecretStr("test-password"),
        search_base="dc=weill,dc=cornell,dc=edu",
    )
}
ACCESS_GROUP = "cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"
ADMIN_GROUP = "cn=ITS:Library:CViche/admin-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"
STAFF_GROUP = "cn=ITS:Library:CViche/ofa-staff-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"


def _groups_side_effect(*member_of):
    """_ldap_check_membership stand-in: True only for the group DNs given."""
    def side_effect(cwid, group_dn, *args, **kwargs):
        return group_dn in member_of
    return side_effect


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear all caches before each test."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# TestCheckEdMembership -- 5 tests for check_ed_membership()
# ---------------------------------------------------------------------------

class TestCheckEdMembership:
    """Tests for the public check_ed_membership() function.

    Both group checks now share one bind (`_bind`), so these patch the bind
    context manager alongside `_ldap_check_membership`.
    """

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_user_in_access_group_returns_true(self, mock_ldap, _mock_bind):
        """User in access group but not admin -> in_access_group=True, in_admin_group=False."""
        def side_effect(email, group_dn, *args, **kwargs):
            if group_dn == ACCESS_GROUP:
                return True
            if group_dn == ADMIN_GROUP:
                return False
            return False

        mock_ldap.side_effect = side_effect
        result = check_ed_membership(
            "user@med.cornell.edu", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS
        )
        assert result == MembershipResult(in_access_group=True, in_admin_group=False)

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_user_not_in_access_group_returns_false(self, mock_ldap, _mock_bind):
        """User not in access group -> both False. Admin check should NOT be called."""
        mock_ldap.return_value = False
        result = check_ed_membership(
            "outsider@example.com", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS
        )
        assert result == MembershipResult(in_access_group=False, in_admin_group=False)
        # Admin group should not be checked when access is denied
        assert mock_ldap.call_count == 1

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_admin_requires_access_group(self, mock_ldap, _mock_bind):
        """User in admin group but NOT in access group -> both False.

        Admin group membership does NOT imply access (locked decision).
        """
        def side_effect(email, group_dn, *args, **kwargs):
            if group_dn == ACCESS_GROUP:
                return False
            if group_dn == ADMIN_GROUP:
                return True
            return False

        mock_ldap.side_effect = side_effect
        result = check_ed_membership(
            "admin-no-access@med.cornell.edu", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS
        )
        assert result == MembershipResult(in_access_group=False, in_admin_group=False)

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_user_in_both_groups(self, mock_ldap, mock_bind):
        """User in both access and admin groups -> both True, over ONE bind."""
        mock_ldap.return_value = True
        result = check_ed_membership(
            "admin@med.cornell.edu", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS
        )
        assert result == MembershipResult(in_access_group=True, in_admin_group=True)
        # Two group checks, one bind (#410).
        assert mock_ldap.call_count == 2
        assert mock_bind.call_count == 1

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_user_in_staff_group_is_staff(self, mock_ldap, _mock_bind):
        mock_ldap.side_effect = _groups_side_effect(ACCESS_GROUP, STAFF_GROUP)
        result = check_ed_membership(
            "staff0001", ACCESS_GROUP, ADMIN_GROUP, staff_group=STAFF_GROUP, **LDAP_PARAMS
        )
        assert result == MembershipResult(
            in_access_group=True, in_admin_group=False, in_staff_group=True)

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_admin_wins_and_staff_is_not_queried(self, mock_ldap, _mock_bind):
        """In all three groups -> admin; the staff search is skipped."""
        mock_ldap.return_value = True
        result = check_ed_membership(
            "both0001", ACCESS_GROUP, ADMIN_GROUP, staff_group=STAFF_GROUP, **LDAP_PARAMS
        )
        assert result == MembershipResult(
            in_access_group=True, in_admin_group=True, in_staff_group=False)
        assert [c.args[1] for c in mock_ldap.call_args_list] == [ACCESS_GROUP, ADMIN_GROUP]

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_staff_requires_access_group(self, mock_ldap, _mock_bind):
        """Staff membership does NOT imply access, the same locked decision as admin."""
        mock_ldap.side_effect = _groups_side_effect(STAFF_GROUP)
        result = check_ed_membership(
            "staffnoaccess", ACCESS_GROUP, ADMIN_GROUP, staff_group=STAFF_GROUP, **LDAP_PARAMS
        )
        assert result == MembershipResult(
            in_access_group=False, in_admin_group=False, in_staff_group=False)
        assert mock_ldap.call_count == 1

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_empty_staff_group_means_nobody_is_staff(self, mock_ldap, _mock_bind):
        """Unset staff_group (every existing deploy) -> never staff, no staff search."""
        mock_ldap.side_effect = _groups_side_effect(ACCESS_GROUP, "")
        result = check_ed_membership(
            "user0001", ACCESS_GROUP, ADMIN_GROUP, staff_group="", **LDAP_PARAMS
        )
        assert result.in_staff_group is False
        assert [c.args[1] for c in mock_ldap.call_args_list] == [ACCESS_GROUP, ADMIN_GROUP]

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_ldap_failure_raises_ed_unavailable(self, mock_ldap, _mock_bind):
        """LDAP connection failure raises EdUnavailableError."""
        mock_ldap.side_effect = EdUnavailableError("Connection refused")
        with pytest.raises(EdUnavailableError, match="Connection refused"):
            check_ed_membership(
                "user@med.cornell.edu", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS
            )


# ---------------------------------------------------------------------------
# TestCache -- 5 tests for cache functions
# ---------------------------------------------------------------------------

class TestCache:
    """Tests for the TTL cache and stale cache functions.

    Cache entries are keyed on (cwid, access_group, admin_group), so the
    accessors take the group DNs alongside the CWID.
    """

    def test_set_and_get_cached_membership(self):
        """set_cached_membership then get_cached_membership returns same record."""
        data = MembershipResult(in_access_group=True, in_admin_group=False)
        set_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP, data)
        result = get_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP)
        assert result == MembershipResult(in_access_group=True, in_admin_group=False)

    def test_cache_miss_returns_none(self):
        """get_cached_membership for unknown email returns None."""
        assert get_cached_membership("unknown@b.com", ACCESS_GROUP, ADMIN_GROUP) is None

    def test_stale_cache_returns_last_known(self):
        """Stale cache returns data after TTL cache is cleared."""
        data = MembershipResult(in_access_group=True, in_admin_group=True)
        set_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP, data)
        # Manually clear the TTL cache (simulating expiry)
        _group_cache.clear()
        # TTL cache empty
        assert get_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP) is None
        stale = get_stale_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP)
        assert stale is not None
        assert stale.in_access_group is True
        assert stale.in_admin_group is True

    def test_stale_cache_rejects_old_entries(self):
        """Stale cache returns None for entries older than 30 minutes."""
        data = MembershipResult(in_access_group=True, in_admin_group=False)
        set_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP, data)
        # Manually backdate the stale entry beyond the 1800s safety bound
        key = MembershipCacheKey("a@b.com", ACCESS_GROUP, ADMIN_GROUP)
        _stale_cache[key].timestamp = time.time() - 2000
        assert get_stale_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP) is None

    def test_set_evicts_expired_stale_entries(self):
        """A later set sweeps out stale entries past the safety bound.

        get_stale_membership only filters expired entries on read, so without
        the sweep the dict grows one entry per CWID for the life of the pod.
        """
        fresh = MembershipResult(in_access_group=True, in_admin_group=False)
        old_key = MembershipCacheKey("old0001", ACCESS_GROUP, ADMIN_GROUP)
        new_key = MembershipCacheKey("new0001", ACCESS_GROUP, ADMIN_GROUP)
        set_cached_membership("old0001", ACCESS_GROUP, ADMIN_GROUP, fresh)
        _stale_cache[old_key].timestamp = time.time() - (_STALE_MAX_AGE + 1)

        set_cached_membership("new0001", ACCESS_GROUP, ADMIN_GROUP, fresh)

        assert old_key not in _stale_cache, "expired entry was not evicted"
        assert new_key in _stale_cache, "fresh entry must survive the sweep"

    def test_clear_cache_empties_both(self):
        """clear_cache() empties both TTL and stale caches."""
        data = MembershipResult(in_access_group=True, in_admin_group=False)
        set_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP, data)
        assert get_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP) is not None
        assert get_stale_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP) is not None
        clear_cache()
        assert get_cached_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP) is None
        assert get_stale_membership("a@b.com", ACCESS_GROUP, ADMIN_GROUP) is None


# ---------------------------------------------------------------------------
# TestDnComparison -- 1 test for case-insensitive DN matching
# ---------------------------------------------------------------------------

class TestDnComparison:
    """Tests for case-insensitive DN comparison in LDAP membership check."""

    def test_case_insensitive_dn_match(self):
        """memberOf with mixed-case DN matches lowercase group_dn."""
        # Build a mock LDAP entry with memberOf containing mixed-case DN
        mock_entry = MagicMock()
        mock_entry.entry_dn = "cn=Test User,ou=People,dc=weill,dc=cornell,dc=edu"
        # memberOf returns mixed-case group DN
        mock_entry.__getitem__ = lambda self, key: {
            "memberOf": [
                "CN=ITS:Library:CViche/user-role,OU=application security,OU=groups,DC=weill,DC=cornell,DC=edu"
            ],
        }.get(key, MagicMock())

        # Configure the mock connection
        mock_conn = MagicMock()

        # First search (user lookup) returns the mock entry
        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "uid=" in search_filter:
                mock_conn.entries = [mock_entry]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect
        mock_conn.entries = [mock_entry]

        result = _ldap_check_membership(
            cwid="testuser",
            group_dn="cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu",
            **LDAP_PARAMS,
            conn=mock_conn,
        )
        assert result is True

# ---------------------------------------------------------------------------
# TestGroupOfURLs -- WCM's house-style group schema (memberURL, not member)
# ---------------------------------------------------------------------------

def _make_entry(dn: str, **attrs):
    """Build a mock ldap3.Entry whose entry_dn returns dn and whose
    __getitem__ returns the supplied attribute lists.

    An attribute NOT passed at all raises KeyError, matching real ldap3
    (LDAPKeyError, a KeyError subclass) for a truly absent attribute. An
    attribute passed explicitly as e.g. `member=[]` returns that empty list
    instead -- a distinct, equally valid LDAP schema state (attribute present
    but empty) that must not raise. Callers that don't care about the
    distinction can simply omit the attribute, same as before.
    """
    entry = MagicMock()
    entry.entry_dn = dn

    def _getitem(self, key, _attrs=attrs):
        if key not in _attrs:
            raise KeyError(key)
        return _attrs[key]

    entry.__getitem__ = _getitem
    return entry


_GOU_GROUP_DN = "cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"
_GOU_USER_DN = "uid=paa2013,ou=People,dc=weill,dc=cornell,dc=edu"
_GOU_MEMBERURL_PAA = "ldap:///uid=paa2013,ou=people,dc=weill,dc=cornell,dc=edu??base?(&(weillCornellEduPersonTypeCode=academic-faculty))"
_GOU_MEMBERURL_DRW = "ldap:///uid=drw2004,ou=people,dc=weill,dc=cornell,dc=edu??base?(&(weillCornellEduPersonTypeCode=academic-faculty))"


class TestParseMemberURL:
    """Direct tests for the RFC 4516 LDAP URL parser."""

    def test_parses_wcm_hybrid_url(self):
        """WCM-style URL: base DN + ?base? scope + stay-active filter."""
        result = _parse_memberurl(_GOU_MEMBERURL_PAA)
        assert result is not None
        base_dn, scope, ldap_filter = result
        assert base_dn == "uid=paa2013,ou=people,dc=weill,dc=cornell,dc=edu"
        assert scope == BASE
        assert ldap_filter == "(&(weillCornellEduPersonTypeCode=academic-faculty))"

    def test_defaults_filter_to_objectclass_star(self):
        """Empty filter component falls back to (objectClass=*) per RFC 4516."""
        result = _parse_memberurl("ldap:///uid=x,ou=people,dc=example,dc=org??sub?")
        assert result is not None
        _, scope, ldap_filter = result
        assert scope == SUBTREE
        assert ldap_filter == "(objectClass=*)"

    def test_returns_none_for_non_ldap_url(self):
        """https:// or arbitrary strings return None, not a crash."""
        assert _parse_memberurl("https://example.com/oops") is None
        assert _parse_memberurl("") is None
        assert _parse_memberurl("garbage") is None

    def test_handles_url_encoded_components(self):
        """LDAP URL bodies can be percent-encoded; decode them."""
        # 'cn=Test User' percent-encoded to demonstrate unquote
        result = _parse_memberurl("ldap:///cn=Test%20User,ou=people,dc=x,dc=y??base?")
        assert result is not None
        base_dn, _, _ = result
        assert base_dn == "cn=Test User,ou=people,dc=x,dc=y"


class TestUserMatchesMemberURL:
    """Direct tests for the memberURL evaluator."""

    def test_short_circuits_when_base_dn_mismatch(self):
        """For ?base? scope, a DN mismatch should not trigger an LDAP roundtrip."""
        mock_conn = MagicMock()
        # Different user than the memberURL points at
        result = _user_matches_memberurl(
            mock_conn, _GOU_MEMBERURL_PAA,
            user_dn="uid=someoneelse,ou=People,dc=weill,dc=cornell,dc=edu",
        )
        assert result is False
        mock_conn.search.assert_not_called()  # short-circuited before LDAP

    def test_returns_true_when_user_matches_base_and_filter(self):
        """Base DN matches and the stay-active filter is satisfied -> True."""
        mock_conn = MagicMock()
        # Filter-eval search returns the user entry
        mock_conn.entries = [_make_entry(_GOU_USER_DN)]
        result = _user_matches_memberurl(mock_conn, _GOU_MEMBERURL_PAA, _GOU_USER_DN)
        assert result is True
        mock_conn.search.assert_called_once()

    def test_returns_false_when_filter_excludes_user(self):
        """Base DN matches BUT the stay-active filter doesn't -- e.g. the user
        is no longer academic-faculty. ldap3 returns zero entries; we return False."""
        mock_conn = MagicMock()
        mock_conn.entries = []  # filter excluded the user
        result = _user_matches_memberurl(mock_conn, _GOU_MEMBERURL_PAA, _GOU_USER_DN)
        assert result is False
        mock_conn.search.assert_called_once()

    def test_malformed_url_returns_false(self):
        """Garbage URL is not a fatal error -- skip and continue evaluating others."""
        mock_conn = MagicMock()
        assert _user_matches_memberurl(mock_conn, "not-a-url", _GOU_USER_DN) is False
        mock_conn.search.assert_not_called()

    # --- dynamic (attribute-rule) memberURLs: the ezproxy-style migration (#318) ---

    _RULE_ONE = (
        "ldap:///ou=people,dc=weill,dc=cornell,dc=edu??one?"
        "(&(objectClass=eduPerson)(weillCornellEduPersonTypeCode=employee))"
    )

    def test_matches_dynamic_one_level_rule_via_user_read(self):
        """A broad ?one? rule is evaluated by reading the USER's own entry with the
        rule filter -- never a broad base/scope search (which ED caps at 500)."""
        mock_conn = MagicMock()
        mock_conn.entries = [_make_entry(_GOU_USER_DN)]  # user satisfies the filter
        result = _user_matches_memberurl(mock_conn, self._RULE_ONE, _GOU_USER_DN)
        assert result is True
        # Search must target the user's own DN at BASE scope, not ou=people/?one?.
        _, kwargs = mock_conn.search.call_args
        assert kwargs["search_base"] == _GOU_USER_DN
        assert kwargs["search_scope"] == BASE

    def test_dynamic_rule_filter_excludes_user(self):
        """User is under the rule's base but fails the filter -> False."""
        mock_conn = MagicMock()
        mock_conn.entries = []
        assert _user_matches_memberurl(mock_conn, self._RULE_ONE, _GOU_USER_DN) is False
        mock_conn.search.assert_called_once()

    def test_dynamic_rule_rejects_user_outside_base(self):
        """User not under the rule's base subtree -> scope gate fails, no LDAP call."""
        mock_conn = MagicMock()
        outsider = "uid=x,ou=people,dc=example,dc=org"
        assert _user_matches_memberurl(mock_conn, self._RULE_ONE, outsider) is False
        mock_conn.search.assert_not_called()

    def test_one_level_rule_rejects_grandchild(self):
        """?one? matches direct children only -- a grandchild DN is not a member."""
        mock_conn = MagicMock()
        grandchild = "uid=x,ou=sub,ou=people,dc=weill,dc=cornell,dc=edu"
        assert _user_matches_memberurl(mock_conn, self._RULE_ONE, grandchild) is False
        mock_conn.search.assert_not_called()


class TestDnInScope:
    """Scope gate: is a user DN within a memberURL's base at its scope? (#318)"""

    BASE_DN = "ou=people,dc=weill,dc=cornell,dc=edu"
    CHILD = "uid=paa2013,ou=people,dc=weill,dc=cornell,dc=edu"
    GRANDCHILD = "uid=x,ou=sub,ou=people,dc=weill,dc=cornell,dc=edu"

    def test_base_scope_requires_exact_match(self):
        assert _dn_in_scope(self.CHILD, self.CHILD, BASE) is True
        assert _dn_in_scope(self.CHILD, self.BASE_DN, BASE) is False
        # Shares the leftmost RDN with CHILD but differs deeper -- a
        # comparison that only checks the leftmost component (u[:1] ==
        # b[:1]) would wrongly call this BASE-in-scope.
        sibling_same_leftmost_rdn = "uid=paa2013,ou=other,dc=weill,dc=cornell,dc=edu"
        assert _dn_in_scope(sibling_same_leftmost_rdn, self.CHILD, BASE) is False

    def test_level_scope_direct_child_only(self):
        assert _dn_in_scope(self.CHILD, self.BASE_DN, LEVEL) is True
        assert _dn_in_scope(self.GRANDCHILD, self.BASE_DN, LEVEL) is False
        assert _dn_in_scope(self.BASE_DN, self.BASE_DN, LEVEL) is False
        # Right depth, wrong parent: depth alone must not satisfy LEVEL.
        assert _dn_in_scope("uid=x,ou=other,dc=weill,dc=cornell,dc=edu", self.BASE_DN, LEVEL) is False

    def test_subtree_scope_at_or_below(self):
        assert _dn_in_scope(self.CHILD, self.BASE_DN, SUBTREE) is True
        assert _dn_in_scope(self.GRANDCHILD, self.BASE_DN, SUBTREE) is True
        assert _dn_in_scope(self.BASE_DN, self.BASE_DN, SUBTREE) is True
        assert _dn_in_scope("uid=x,dc=other,dc=org", self.BASE_DN, SUBTREE) is False

    def test_case_insensitive(self):
        assert _dn_in_scope(self.CHILD.upper(), self.BASE_DN, LEVEL) is True

    def test_case_insensitive_on_base_dn_too(self):
        """Case-folding must apply to BOTH sides of the comparison, not just
        the user DN -- a mutation that only lowers user_rdns (or only
        base_rdns) survives test_case_insensitive alone."""
        assert _dn_in_scope(self.CHILD, self.BASE_DN.upper(), LEVEL) is True

    def test_subtree_is_a_positional_suffix_not_a_set_membership(self):
        """SUBTREE must check that base_dn's RDNs are the exact rightmost
        SLICE of user_dn's, in order -- not merely that every RDN of base_dn
        appears somewhere in user_dn (#331). A DN with the same RDN
        components in a different position/order must not match."""
        base = "dc=weill,dc=cornell,dc=edu"
        reordered = "ou=x,dc=cornell,dc=weill,dc=edu"
        assert _dn_in_scope(reordered, base, SUBTREE) is False

    def test_escaped_comma_in_rdn_value_is_one_component(self):
        """A comma inside an RDN's value (escaped per RFC 4514) must not be
        read as a component boundary -- the old partition(',') implementation
        split on it and missed this direct child (#331)."""
        child = r"cn=Smith\, John,ou=people,dc=weill,dc=cornell,dc=edu"
        assert _dn_in_scope(child, self.BASE_DN, LEVEL) is True

    def test_whitespace_after_comma_is_normalized(self):
        spaced = "uid=abc, ou=people, dc=weill, dc=cornell, dc=edu"
        assert _dn_in_scope(spaced, self.BASE_DN, LEVEL) is True

    def test_unparseable_escaped_comma_dn_is_not_in_scope(self):
        """The old string-suffix check read this DN as ending in base_dn and
        accepted it under SUBTREE (#331). ldap3 refuses to parse it (the
        unescaped "=" after the escaped comma), so it now lands on the
        malformed-DN branch and is out of scope."""
        widened = r"uid=x,ou=a\,ou=people,dc=weill,dc=cornell,dc=edu"
        assert _dn_in_scope(widened, self.BASE_DN, SUBTREE) is False

    def test_malformed_user_dn_is_not_in_scope_and_warns_without_the_dn(self, caplog):
        """A user DN ldap3 cannot parse returns False (fail-closed), never
        raises, and logs a warning that leaves the DN itself out."""
        with caplog.at_level("WARNING", logger="app.ed_group_lookup"):
            assert _dn_in_scope("not a dn at all ===", self.BASE_DN, SUBTREE) is False
        assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
            ("WARNING", "user DN does not parse; treating as not in scope")]

    def test_malformed_base_dn_is_not_in_scope_and_logs_an_error(self, caplog):
        """A memberURL base that won't parse is a broken group definition:
        still not in scope, but logged at ERROR and named, unlike a bad user DN."""
        with caplog.at_level("WARNING", logger="app.ed_group_lookup"):
            assert _dn_in_scope(self.CHILD, "=bad base", SUBTREE) is False
        assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
            ("ERROR", "memberURL base DN '=bad base' does not parse; treating as not in scope")]

    def test_escaped_trailing_space_is_not_in_scope(self):
        """parse_dn(strip=True) rejects a value ending in an escaped space,
        which lands on the fail-closed side rather than widening scope."""
        assert _dn_in_scope(r"uid=x\ ,ou=people,dc=weill,dc=cornell,dc=edu",
                            self.BASE_DN, SUBTREE) is False


class TestGroupOfURLsMembership:
    """End-to-end _ldap_check_membership tests for WCM's groupOfURLs schema.

    Regression coverage for BUG D: until 2026-05-20 the code's fallback used
    (&(objectClass=groupOfNames)(cn=*)) and only read the `member` attribute.
    WCM ED groups are groupOfURLs with memberURL, so every membership check
    returned False -> would have locked every user out post-SAML cutover.
    """

    def test_user_in_groupofurls_returns_true(self):
        """The WCM hybrid pattern: group has memberURL for the user; filter matches."""
        mock_conn = MagicMock()

        # search_side_effect drives three calls:
        #  1. mail lookup -> user entry (no memberOf)
        #  2. group lookup -> group entry with memberURL list
        #  3. memberURL filter eval -> the user entry (passes filter)
        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                # User lookup
                mock_conn.entries = [_make_entry(_GOU_USER_DN)]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                # Group lookup
                mock_conn.entries = [_make_entry(
                    _GOU_GROUP_DN,
                    memberURL=[_GOU_MEMBERURL_DRW, _GOU_MEMBERURL_PAA],
                )]
            elif "weillCornellEduPersonTypeCode" in search_filter:
                # memberURL filter eval -- user passes the academic-faculty check
                mock_conn.entries = [_make_entry(_GOU_USER_DN)]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect

        result = _ldap_check_membership(
            cwid="paa2013",
            group_dn=_GOU_GROUP_DN,
            **LDAP_PARAMS,
            conn=mock_conn,
        )
        assert result is True

    def test_user_listed_but_filter_excludes_returns_false(self):
        """User's memberURL is in the group but they no longer satisfy the filter
        (e.g., personTypeCode changed from academic-faculty). Must deny access."""
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN)]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(
                    _GOU_GROUP_DN,
                    memberURL=[_GOU_MEMBERURL_PAA],
                )]
            elif "weillCornellEduPersonTypeCode" in search_filter:
                mock_conn.entries = []   # filter excluded user
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect

        result = _ldap_check_membership(
            cwid="paa2013",
            group_dn=_GOU_GROUP_DN,
            **LDAP_PARAMS,
            conn=mock_conn,
        )
        assert result is False

    def test_user_not_in_any_memberurl_returns_false(self):
        """Group has memberURLs but none point at the user -- short-circuits."""
        mock_conn = MagicMock()
        other_user_dn = "uid=someoneelse,ou=People,dc=weill,dc=cornell,dc=edu"

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(other_user_dn)]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(
                    _GOU_GROUP_DN,
                    memberURL=[_GOU_MEMBERURL_PAA, _GOU_MEMBERURL_DRW],
                )]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect

        result = _ldap_check_membership(
            cwid="outsider",
            group_dn=_GOU_GROUP_DN,
            **LDAP_PARAMS,
            conn=mock_conn,
        )
        assert result is False

    def test_groupofnames_member_attribute_still_works(self):
        """Backward compat: a real groupOfNames group with a `member` attribute
        is still recognized (the fallback now handles both schemas)."""
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN)]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(
                    _GOU_GROUP_DN,
                    member=[_GOU_USER_DN, "uid=otherperson,ou=People,dc=weill,dc=cornell,dc=edu"],
                )]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect

        result = _ldap_check_membership(
            cwid="paa2013",
            group_dn=_GOU_GROUP_DN,
            **LDAP_PARAMS,
            conn=mock_conn,
        )
        assert result is True


class TestNotInDirectoryLogging:
    """#422: no LDAP entry for the cwid must log distinctly from 'not in
    group', at a level visible in prod (default level is INFO)."""

    def test_no_ldap_entry_logs_warning_distinct_from_not_in_group(self, caplog):
        """No entries come back for the uid= lookup -> WARNING log containing
        'not in directory', and the function still returns False."""
        mock_conn = MagicMock()
        mock_conn.entries = []  # user lookup finds nothing

        with caplog.at_level("WARNING", logger="app.ed_group_lookup"):
            result = _ldap_check_membership(
                cwid="ghost0001",
                group_dn=_GOU_GROUP_DN,
                **LDAP_PARAMS,
                conn=mock_conn,
            )

        assert result is False
        assert any(
            record.levelname == "WARNING" and "not in directory" in record.getMessage()
            for record in caplog.records
        )

# ---------------------------------------------------------------------------
# TestCacheLifecycle -- B2: the full cache-aside + stale-fallback lifecycle
# ---------------------------------------------------------------------------

class TestCacheLifecycle:
    """B2: success -> live TTL entry expires -> LDAP unavailable -> stale
    result IS returned -> a stale entry older than the 30-minute bound is
    REJECTED. The single biggest coverage gap the reviewer named."""

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_success_expiry_outage_stale_then_stale_expiry(self, mock_ldap, _mock_bind):
        mock_ldap.return_value = True
        cwid = "life0001"

        # 1. A successful LDAP check populates both the live and stale caches.
        result = check_ed_membership(cwid, ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)
        assert result == MembershipResult(in_access_group=True, in_admin_group=True)
        assert get_cached_membership(cwid, ACCESS_GROUP, ADMIN_GROUP) == result
        assert get_stale_membership(cwid, ACCESS_GROUP, ADMIN_GROUP) == result

        # 2. The live TTL entry expires (simulated the same way TestCache does).
        _group_cache.clear()
        assert get_cached_membership(cwid, ACCESS_GROUP, ADMIN_GROUP) is None

        # 3. LDAP becomes unavailable.
        mock_ldap.side_effect = EdUnavailableError("ED down")

        # 4. The stale result IS returned (still within the 30-minute bound).
        result2 = check_ed_membership(cwid, ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)
        assert result2 == result

        # 5. A stale entry older than _STALE_MAX_AGE is REJECTED -> re-raises.
        key = MembershipCacheKey(cwid, ACCESS_GROUP, ADMIN_GROUP)
        _stale_cache[key].timestamp = time.time() - (_STALE_MAX_AGE + 1)
        with pytest.raises(EdUnavailableError):
            check_ed_membership(cwid, ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)


# ---------------------------------------------------------------------------
# TestExceptionTaxonomy -- B3: real ldap3 failures at the public boundary
# ---------------------------------------------------------------------------

class TestExceptionTaxonomy:
    """B3: Server, Connection, auto_bind, and search() each raise a
    representative ldap3 exception; check_ed_membership must classify each
    into the right side of the EdUnavailableError / EdConfigurationError
    taxonomy."""

    def test_configuration_error_is_a_subclass_of_unavailable(self):
        """Both call sites' `except EdUnavailableError` must still catch this."""
        assert issubclass(EdConfigurationError, EdUnavailableError)

    @patch("app.ed_group_lookup.Server")
    def test_server_construction_socket_failure_is_unavailable(self, mock_server):
        """A communication/socket failure building Server() -> EdUnavailableError."""
        mock_server.side_effect = LDAPSocketOpenError("cannot open socket")
        with pytest.raises(EdUnavailableError) as excinfo:
            check_ed_membership("tax0001", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)
        assert not isinstance(excinfo.value, EdConfigurationError)

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_connection_construction_communication_failure_is_unavailable(
        self, _mock_server, mock_conn_cls
    ):
        """A communication failure building Connection() -> EdUnavailableError."""
        mock_conn_cls.side_effect = LDAPSocketReceiveError("recv failed")
        with pytest.raises(EdUnavailableError) as excinfo:
            check_ed_membership("tax0002", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)
        assert not isinstance(excinfo.value, EdConfigurationError)

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_auto_bind_credentials_failure_is_configuration_error(
        self, _mock_server, mock_conn_cls
    ):
        """auto_bind rejecting our service-account credentials -> EdConfigurationError."""
        mock_conn_cls.side_effect = LDAPBindError("invalid credentials")
        with pytest.raises(EdConfigurationError):
            check_ed_membership("tax0003", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_search_communication_failure_is_unavailable(self, _mock_server, mock_conn_cls):
        """A transport fault mid-search() -> EdUnavailableError, not swallowed."""
        mock_conn = MagicMock()
        mock_conn.search.side_effect = LDAPCommunicationError("connection reset mid-search")
        mock_conn_cls.return_value = mock_conn
        with pytest.raises(EdUnavailableError) as excinfo:
            check_ed_membership("tax0004", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)
        assert not isinstance(excinfo.value, EdConfigurationError)

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_search_credentials_failure_is_configuration_error(self, _mock_server, mock_conn_cls):
        """A rights/credentials fault mid-search() -> EdConfigurationError."""
        mock_conn = MagicMock()
        mock_conn.search.side_effect = LDAPInvalidCredentialsResult("rights revoked mid-search")
        mock_conn_cls.return_value = mock_conn
        with pytest.raises(EdConfigurationError):
            check_ed_membership("tax0005", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)


# ---------------------------------------------------------------------------
# TestUnbindCalledOnBothPaths -- B4: connection-leak regression
# ---------------------------------------------------------------------------

class TestUnbindCalledOnBothPaths:
    """B4: unbind() must fire whether the bind body succeeds or raises."""

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_unbind_called_on_success(self, _mock_server, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        with _bind(LDAP_PARAMS["cfg"]) as conn:
            assert conn is mock_conn

        mock_conn.unbind.assert_called_once()

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_unbind_called_when_body_raises(self, _mock_server, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        with pytest.raises(EdUnavailableError):
            with _bind(LDAP_PARAMS["cfg"]) as conn:
                raise LDAPCommunicationError("boom mid-search")

        mock_conn.unbind.assert_called_once()


# ---------------------------------------------------------------------------
# TestFilterInjectionEscaping -- B5
# ---------------------------------------------------------------------------

class TestFilterInjectionEscaping:
    """B5: a cwid carrying LDAP filter metacharacters must reach the server
    escaped -- assert the actual search_filter passed, not just that the
    call didn't crash."""

    def test_cwid_with_metacharacters_is_escaped_in_search_filter(self):
        malicious_cwid = "a*)(uid=*))(|(uid=*"
        mock_conn = MagicMock()
        mock_conn.entries = []

        _ldap_check_membership(
            cwid=malicious_cwid, group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )

        first_call_kwargs = mock_conn.search.call_args_list[0].kwargs
        expected_filter = f"(uid={escape_filter_chars(malicious_cwid)})"
        assert first_call_kwargs["search_filter"] == expected_filter
        # The raw, unescaped metacharacter sequence must not appear.
        assert "*)(uid=*))(|(uid=*" not in first_call_kwargs["search_filter"]


# ---------------------------------------------------------------------------
# TestMemberOfFallthroughToMemberURL -- B6
# ---------------------------------------------------------------------------

class TestMemberOfFallthroughToMemberURL:
    """B6: memberOf is populated (for some OTHER group) but doesn't name the
    target group, and the target group IS a groupOfURLs -- membership must
    still succeed via the memberURL fallthrough. No test covered this path
    before; a future "memberOf present -> skip Path 2" optimization would
    silently break it."""

    def test_memberof_present_without_target_falls_through_to_memberurl_hit(self):
        mock_conn = MagicMock()
        other_group_dn = "cn=SomeOtherGroup,ou=groups,dc=weill,dc=cornell,dc=edu"

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN, memberOf=[other_group_dn])]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [
                    _make_entry(_GOU_GROUP_DN, memberURL=[_GOU_MEMBERURL_PAA])
                ]
            elif "weillCornellEduPersonTypeCode" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN)]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect

        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is True


# ---------------------------------------------------------------------------
# TestAttributeVariations -- B7
# ---------------------------------------------------------------------------

class TestAttributeVariations:
    """B7: absent and empty LDAP attributes are both normal schema variation
    and must return False WITHOUT raising. Each case gets its own named test
    so a regression in any one path is identified by name, not inference."""

    def test_missing_memberof_returns_false_without_raising(self):
        """User entry has no memberOf attribute at all (KeyError path)."""
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN)]  # no memberOf key
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_GROUP_DN, member=[], memberURL=[])]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect
        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is False

    def test_missing_member_returns_false_without_raising(self):
        """Group entry has no `member` attribute at all (KeyError path)."""
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN, memberOf=[])]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_GROUP_DN, memberURL=[])]  # no member key
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect
        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is False

    def test_missing_memberurl_returns_false_without_raising(self):
        """Group entry has no `memberURL` attribute at all (KeyError path)."""
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN, memberOf=[])]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_GROUP_DN, member=[])]  # no memberURL key
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect
        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is False

    def test_empty_member_list_returns_false_without_raising(self):
        """`member` IS present (not absent) but is an empty list."""
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN, memberOf=[])]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_GROUP_DN, member=[])]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect
        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is False

    def test_empty_memberurl_list_returns_false_without_raising(self):
        """`memberURL` IS present (not absent) but is an empty list."""
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN, memberOf=[])]
            elif "groupOfNames" in search_filter or "groupOfURLs" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_GROUP_DN, memberURL=[])]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect
        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is False


# ---------------------------------------------------------------------------
# TestSearchParameters -- B8
# ---------------------------------------------------------------------------

class TestSearchParameters:
    """B8: assert the actual keyword arguments passed to conn.search() for
    both authorization searches, instead of only string-matching inside a
    mock side_effect."""

    def test_user_and_group_search_parameters(self):
        mock_conn = MagicMock()

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [_make_entry(_GOU_USER_DN, memberOf=[])]
            else:
                mock_conn.entries = [_make_entry(_GOU_GROUP_DN, member=[], memberURL=[])]

        mock_conn.search.side_effect = search_side_effect

        _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )

        assert mock_conn.search.call_count == 2
        user_call = mock_conn.search.call_args_list[0].kwargs
        group_call = mock_conn.search.call_args_list[1].kwargs
        cfg = LDAP_PARAMS["cfg"]

        assert user_call["search_base"] == cfg.search_base
        assert user_call["search_filter"] == "(uid=paa2013)"
        assert user_call["search_scope"] == SUBTREE
        assert user_call["attributes"] == ["memberOf", "dn"]

        assert group_call["search_base"] == _GOU_GROUP_DN
        assert group_call["search_filter"] == (
            "(|(objectClass=groupOfNames)(objectClass=groupOfURLs))"
        )
        assert group_call["search_scope"] == BASE
        assert group_call["attributes"] == ["member", "memberURL"]


# ---------------------------------------------------------------------------
# TestAmbiguousUidLookup -- B9
# ---------------------------------------------------------------------------

class TestAmbiguousUidLookup:
    """B9: (uid=<cwid>) returning MULTIPLE entries logs a warning and
    proceeds with the first entry (documented judgement call: fail loud, not
    closed, on a directory-hygiene defect)."""

    def test_multiple_entries_logs_warning_and_uses_first(self, caplog):
        mock_conn = MagicMock()
        first_entry = _make_entry(_GOU_USER_DN, memberOf=[_GOU_GROUP_DN.lower()])
        second_entry = _make_entry(
            "uid=paa2013,ou=OtherPeople,dc=weill,dc=cornell,dc=edu", memberOf=[]
        )

        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "(uid=" in search_filter:
                mock_conn.entries = [first_entry, second_entry]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect

        with caplog.at_level("WARNING", logger="app.ed_group_lookup"):
            result = _ldap_check_membership(
                cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
            )

        # entries[0]'s memberOf matches group_dn -> proves the FIRST entry was used.
        assert result is True
        assert any(
            record.levelname == "WARNING" and "Ambiguous ED lookup" in record.getMessage()
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# TestCacheKeyIncludesGroups -- B10
# ---------------------------------------------------------------------------

class TestCacheKeyIncludesGroups:
    """B10: the cache key includes the group DNs -- the same cwid evaluated
    against a different access/admin pair must NOT get a cross-contaminated
    cache hit."""

    def test_same_cwid_different_groups_do_not_share_cache_entry(self):
        data_a = MembershipResult(in_access_group=True, in_admin_group=False)
        set_cached_membership("shared0001", ACCESS_GROUP, ADMIN_GROUP, data_a)

        other_access = "cn=OtherAccess,ou=groups,dc=weill,dc=cornell,dc=edu"
        other_admin = "cn=OtherAdmin,ou=groups,dc=weill,dc=cornell,dc=edu"
        assert get_cached_membership("shared0001", other_access, other_admin) is None
        assert get_stale_membership("shared0001", other_access, other_admin) is None
        # The original entry is still intact under its own key.
        assert get_cached_membership("shared0001", ACCESS_GROUP, ADMIN_GROUP) == data_a

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_different_group_pair_triggers_a_fresh_ldap_query(self, mock_ldap, _mock_bind):
        mock_ldap.return_value = True
        check_ed_membership("shared0002", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)
        assert mock_ldap.call_count == 2  # access + admin, one bind

        other_access = "cn=OtherAccess,ou=groups,dc=weill,dc=cornell,dc=edu"
        other_admin = "cn=OtherAdmin,ou=groups,dc=weill,dc=cornell,dc=edu"
        check_ed_membership("shared0002", other_access, other_admin, **LDAP_PARAMS)
        # A fresh LDAP round happened for the different group pair -- it was
        # NOT served from the first pair's cache entry.
        assert mock_ldap.call_count == 4

    def test_same_groups_different_staff_group_do_not_share_cache_entry(self):
        staff = MembershipResult(in_access_group=True, in_admin_group=False, in_staff_group=True)
        set_cached_membership("shared0003", ACCESS_GROUP, ADMIN_GROUP, staff, STAFF_GROUP)

        # Staff group unset (or repointed): the staff answer must not be served.
        assert get_cached_membership("shared0003", ACCESS_GROUP, ADMIN_GROUP) is None
        assert get_stale_membership("shared0003", ACCESS_GROUP, ADMIN_GROUP) is None
        assert get_cached_membership(
            "shared0003", ACCESS_GROUP, ADMIN_GROUP, STAFF_GROUP) == staff

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_different_staff_group_triggers_a_fresh_ldap_query(self, mock_ldap, _mock_bind):
        mock_ldap.side_effect = _groups_side_effect(ACCESS_GROUP, STAFF_GROUP)
        first = check_ed_membership(
            "shared0004", ACCESS_GROUP, ADMIN_GROUP, staff_group=STAFF_GROUP, **LDAP_PARAMS)
        assert first.in_staff_group is True

        second = check_ed_membership(
            "shared0004", ACCESS_GROUP, ADMIN_GROUP, staff_group="", **LDAP_PARAMS)
        assert second.in_staff_group is False


# ---------------------------------------------------------------------------
# TestSingleFlight -- B11
# ---------------------------------------------------------------------------

class TestSingleFlight:
    """B11: concurrent misses on the same key must produce exactly ONE LDAP
    query -- exercised with real threads, not a simulated sequence."""

    @patch("app.ed_group_lookup._bind")
    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_concurrent_misses_produce_one_ldap_query(self, mock_ldap, _mock_bind):
        state = {"calls": 0}
        state_lock = threading.Lock()

        def slow_ldap_check(cwid, group_dn, cfg, conn):
            with state_lock:
                state["calls"] += 1
            # Hold the single-flight lock long enough for the other threads
            # to queue up behind it before this one finishes and populates
            # the cache.
            time.sleep(0.05)
            return True

        mock_ldap.side_effect = slow_ldap_check

        cwid = "flight01"
        results = []
        results_lock = threading.Lock()
        thread_count = 10
        barrier = threading.Barrier(thread_count)

        def worker():
            barrier.wait()
            r = check_ed_membership(cwid, ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == thread_count
        assert all(
            r == MembershipResult(in_access_group=True, in_admin_group=True) for r in results
        )
        # thread_count concurrent misses on the same key -> exactly ONE LDAP
        # round (2 calls: access + admin), not thread_count rounds.
        assert state["calls"] == 2


# ---------------------------------------------------------------------------
# TestParseMemberURLUnknownScope -- B12
# ---------------------------------------------------------------------------

class TestParseMemberURLUnknownScope:
    """B12: an unrecognized LDAP URL scope token must parse to None (rejected
    as malformed), never silently degrade to BASE scope."""

    def test_unrecognized_scope_token_rejected_not_defaulted_to_base(self):
        result = _parse_memberurl(
            "ldap:///uid=x,ou=people,dc=example,dc=org??weird?(objectClass=*)"
        )
        assert result is None

    def test_unrecognized_scope_token_logs_warning(self, caplog):
        with caplog.at_level("WARNING", logger="app.ed_group_lookup"):
            result = _parse_memberurl(
                "ldap:///uid=x,ou=people,dc=example,dc=org??weird?(objectClass=*)"
            )
        assert result is None
        assert any(
            "Unrecognized LDAP URL scope token" in record.getMessage()
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# TestMembershipResultContract -- B13
# ---------------------------------------------------------------------------

class TestMembershipResultContract:
    """B13: the typed MembershipResult contract -- attribute access works,
    and the record is frozen/immutable."""

    def test_attribute_access(self):
        m = MembershipResult(in_access_group=True, in_admin_group=False)
        assert m.in_access_group is True
        assert m.in_admin_group is False

    def test_frozen_immutable(self):
        m = MembershipResult(in_access_group=True, in_admin_group=False)
        with pytest.raises(FrozenInstanceError):
            m.in_access_group = False


# ---------------------------------------------------------------------------
# Integration tests: ACS handler ED wiring
# ---------------------------------------------------------------------------

from app.models import SystemConfig, User
from app.auth import create_session_cookie, COOKIE_NAME


def _mock_saml_client(identity_dict):
    """Create a mock Saml2Client with pre-configured ACS response."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.get_identity.return_value = identity_dict
    mock_response.response.destination = None  # absent Destination is allowed (#672)
    # A real pysaml2 response always carries an assertion ID; give this stub
    # one too so the replay gate's fail-closed default (a missing ID) doesn't
    # fire on tests that aren't exercising that path.
    assertion = MagicMock()
    assertion.id = f"_{uuid4().hex}"
    mock_response.assertions = [assertion]
    mock_response.assertion = assertion
    mock_client.parse_authn_request_response.return_value = mock_response
    return mock_client


def _set_staff_group_config(db, staff_group: str) -> None:
    """Upsert ed_staff_group: app startup may already have seeded it empty."""
    row = db.query(SystemConfig).filter_by(key="ed_staff_group").first()
    if row is None:
        db.add(SystemConfig(key="ed_staff_group", value=json.dumps(staff_group)))
    else:
        row.value = json.dumps(staff_group)
    db.commit()


_ED_ENV = {
    "ED_LDAP_URL": "ldaps://ed.weill.cornell.edu:636",
    "ED_LDAP_BIND_DN": "cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
    "ED_LDAP_BIND_PASSWORD": "test-password",
}

_SAML_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["test@med.cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["test@cornell.edu"],
}


class TestACSGroupCheck:
    """Integration tests for ACS handler ED group authorization wiring."""

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_user_not_in_access_group_denied(
        self, mock_extract, mock_get_client, mock_check_ed, client, seed_ed_enabled
    ):
        """User not in ED access group gets redirected to /login?error=not_authorized."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = {"cwid": "test0001", "email": "test@med.cornell.edu", "display_name": "Test User"}
        mock_check_ed.return_value = MembershipResult(in_access_group=False, in_admin_group=False)

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=not_authorized" in response.headers["location"]

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_user_in_access_group_allowed(
        self, mock_extract, mock_get_client, mock_check_ed, client, seed_ed_enabled
    ):
        """User in ED access group proceeds normally -- 302 to / with session cookie."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = {"cwid": "test0001", "email": "test@med.cornell.edu", "display_name": "Test User"}
        mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        cookies = {c.name: c for c in response.cookies.jar}
        assert COOKIE_NAME in cookies

    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_ed_disabled_skips_check(
        self, mock_extract, mock_get_client, client, seed_saml_mode
    ):
        """When ed_enabled is false, ACS skips ED check and proceeds normally."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = {"cwid": "test0001", "email": "test@med.cornell.edu", "display_name": "Test User"}

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/"

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_staff_group_member_provisioned_as_staff(
        self, mock_extract, mock_get_client, mock_check_ed, client, db, seed_ed_enabled
    ):
        clear_cache()
        _set_staff_group_config(db, STAFF_GROUP)
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = {"cwid": "staff0002", "email": "ofa@med.cornell.edu", "display_name": "OFA Staff"}
        mock_check_ed.return_value = MembershipResult(
            in_access_group=True, in_admin_group=False, in_staff_group=True)

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        assert mock_check_ed.call_args.kwargs["staff_group"] == STAFF_GROUP
        assert db.query(User).filter_by(cwid="staff0002").one().role == "staff"

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_ldap_unavailable_at_login(
        self, mock_extract, mock_get_client, mock_check_ed, client, seed_ed_enabled
    ):
        """ED unavailable at login time redirects to /login?error=directory_unavailable."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = {"cwid": "test0001", "email": "test@med.cornell.edu", "display_name": "Test User"}
        mock_check_ed.side_effect = EdUnavailableError("Connection refused")

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=directory_unavailable" in response.headers["location"]


# ---------------------------------------------------------------------------
# Integration tests: Per-request ED re-check in get_current_user
# ---------------------------------------------------------------------------


class TestPerRequestCheck:
    """Integration tests for get_current_user ED group re-check."""

    def test_cached_member_allowed(self, client, db, seed_ed_enabled):
        """SAML user with cached in_access_group=True gets 200 on /api/auth/me."""
        clear_cache()
        user = User(
            cwid="test0001",
            email="test@med.cornell.edu",
            display_name="Test User",
            role="user",
            auth_method="saml",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        set_cached_membership("test0001", ACCESS_GROUP, ADMIN_GROUP,
                              MembershipResult(in_access_group=True, in_admin_group=False))
        token = create_session_cookie(user, db)

        response = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert response.status_code == 200

    def test_removed_user_denied(self, client, db, seed_ed_enabled):
        """SAML user with cached in_access_group=False gets 401 on /api/auth/me."""
        clear_cache()
        user = User(
            cwid="test0001",
            email="test@med.cornell.edu",
            display_name="Test User",
            role="user",
            auth_method="saml",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        set_cached_membership("test0001", ACCESS_GROUP, ADMIN_GROUP,
                              MembershipResult(in_access_group=False, in_admin_group=False))
        token = create_session_cookie(user, db)

        response = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert response.status_code == 401

    @staticmethod
    def _saml_user(db, role="user"):
        user = User(cwid="staff0001", email="staff@med.cornell.edu",
                    display_name="Staff User", role=role, auth_method="saml")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def _configure_staff_group(db):
        _set_staff_group_config(db, STAFF_GROUP)

    def _me_with_cached(self, client, db, membership, staff_group, role="user"):
        """GET /api/auth/me for a SAML user whose ED answer is `membership`,
        cached under the key the per-request check builds for `staff_group`."""
        clear_cache()
        user = self._saml_user(db, role=role)
        set_cached_membership("staff0001", ACCESS_GROUP, ADMIN_GROUP, membership, staff_group)
        token = create_session_cookie(user, db)
        return client.get("/api/auth/me", cookies={COOKIE_NAME: token})

    def test_staff_group_member_synced_to_staff(self, client, db, seed_ed_enabled):
        self._configure_staff_group(db)
        response = self._me_with_cached(
            client, db,
            MembershipResult(in_access_group=True, in_admin_group=False, in_staff_group=True),
            STAFF_GROUP,
        )
        assert response.status_code == 200
        assert response.json()["role"] == "staff"

    def test_admin_wins_over_staff_on_role_sync(self, client, db, seed_ed_enabled):
        self._configure_staff_group(db)
        response = self._me_with_cached(
            client, db,
            MembershipResult(in_access_group=True, in_admin_group=True, in_staff_group=True),
            STAFF_GROUP,
        )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    def test_staff_without_access_is_denied(self, client, db, seed_ed_enabled):
        self._configure_staff_group(db)
        response = self._me_with_cached(
            client, db,
            MembershipResult(in_access_group=False, in_admin_group=False, in_staff_group=True),
            STAFF_GROUP,
        )
        assert response.status_code == 401

    def test_unset_staff_group_demotes_staff_to_user(self, client, db, seed_ed_enabled):
        """No ed_staff_group configured: a user whose stored role is staff is
        re-synced to user (the per-request check keys on an empty staff group)."""
        response = self._me_with_cached(
            client, db,
            MembershipResult(in_access_group=True, in_admin_group=False),
            "",
            role="staff",
        )
        assert response.status_code == 200
        assert response.json()["role"] == "user"

    def test_simple_mode_skips_ed(self, client, db, seed_simple_mode):
        """Simple mode user bypasses ED check entirely."""
        clear_cache()
        # Add user email to allowed_users (already done in seed_simple_mode fixture)
        user = User(
            email="test@example.com",
            display_name="Test User",
            role="user",
            auth_method="simple",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_session_cookie(user, db)

        response = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# D14: the ED LDAP URL is validated before anything is constructed
# ---------------------------------------------------------------------------

class TestLdapUrlValidation:
    """A malformed ED_LDAP_URL is a configuration fault, and it is caught
    before a Server object exists -- never as a socket attempt against
    nothing, and never as an uncaught 500 at either call site."""

    _BAD_URLS = [
        "http://ed.weill.cornell.edu",   # wrong scheme
        "ldaps://",                      # no host
        "ldap://",                       # no host
        "ed.weill.cornell.edu:636",      # no scheme at all
        "",                              # unset config
    ]

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_unsupported_scheme_raises_before_server(self, mock_server, mock_conn_cls):
        cfg = LDAPConfig(
            ldap_url="http://ed.weill.cornell.edu",
            bind_dn="cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
            bind_password=SecretStr("test-password"),
        )
        with pytest.raises(EdConfigurationError):
            with _bind(cfg):
                pass
        mock_server.assert_not_called()
        mock_conn_cls.assert_not_called()

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_empty_host_raises_before_server(self, mock_server, mock_conn_cls):
        cfg = LDAPConfig(
            ldap_url="ldaps://",
            bind_dn="cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
            bind_password=SecretStr("test-password"),
        )
        with pytest.raises(EdConfigurationError):
            with _bind(cfg):
                pass
        mock_server.assert_not_called()
        mock_conn_cls.assert_not_called()

    @pytest.mark.parametrize("bad_url", _BAD_URLS)
    def test_validator_rejects(self, bad_url):
        with pytest.raises(EdConfigurationError):
            _validate_ldap_url(bad_url)

    @pytest.mark.parametrize("good_url", [
        "ldap://ed.weill.cornell.edu",
        "ldap://ed.weill.cornell.edu:389",
        "ldaps://ed.weill.cornell.edu:636",
        "LDAPS://ED.WEILL.CORNELL.EDU:636",
    ])
    def test_validator_accepts_real_urls(self, good_url):
        _validate_ldap_url(good_url)  # must not raise

    @patch("app.ed_group_lookup.Server")
    def test_fails_closed_through_the_public_api(self, mock_server):
        """EdConfigurationError subclasses EdUnavailableError, so both call
        sites' existing `except EdUnavailableError` handlers still fail closed
        rather than letting a config fault become a 500."""
        cfg = LDAPConfig(
            ldap_url="ldap://",
            bind_dn="cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
            bind_password=SecretStr("test-password"),
        )
        with pytest.raises(EdUnavailableError):
            check_ed_membership("badurl01", ACCESS_GROUP, ADMIN_GROUP, cfg=cfg)
        mock_server.assert_not_called()


class TestValidateStartupConfig:
    """validate_startup_config fails a deployment at boot on a misconfigured
    ED, instead of on the first SAML login (#330)."""

    _GOOD_URL = "ldaps://ed.weill.cornell.edu:636"
    _GOOD_DN = "cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu"
    _GOOD_PASSWORD = "test-password"  # synthetic test literal, not a real credential
    _GOOD_GROUP = "cn=cviche-access,ou=Groups,dc=weill,dc=cornell,dc=edu"

    def test_accepts_a_complete_config(self):
        validate_startup_config(
            self._GOOD_URL, self._GOOD_DN, self._GOOD_PASSWORD, self._GOOD_GROUP,
        )  # must not raise

    def test_rejects_empty_bind_password(self):
        """The one field no call site ever checked (#330's residual gap)."""
        with pytest.raises(EdConfigurationError, match="ED_LDAP_BIND_PASSWORD"):
            validate_startup_config(
                self._GOOD_URL, self._GOOD_DN, "", self._GOOD_GROUP,
            )

    def test_rejects_whitespace_only_bind_password(self):
        with pytest.raises(EdConfigurationError, match="ED_LDAP_BIND_PASSWORD"):
            validate_startup_config(
                self._GOOD_URL, self._GOOD_DN, "   ", self._GOOD_GROUP,
            )

    def test_rejects_whitespace_only_bind_dn(self):
        with pytest.raises(EdConfigurationError, match="ED_LDAP_BIND_DN"):
            validate_startup_config(
                self._GOOD_URL, "   ", self._GOOD_PASSWORD, self._GOOD_GROUP,
            )

    def test_rejects_whitespace_only_access_group(self):
        with pytest.raises(EdConfigurationError, match="ed_access_group"):
            validate_startup_config(
                self._GOOD_URL, self._GOOD_DN, self._GOOD_PASSWORD, "   ",
            )

    def test_rejects_missing_bind_dn(self):
        with pytest.raises(EdConfigurationError, match="ED_LDAP_BIND_DN"):
            validate_startup_config(
                self._GOOD_URL, "", self._GOOD_PASSWORD, self._GOOD_GROUP,
            )

    def test_rejects_missing_access_group(self):
        with pytest.raises(EdConfigurationError, match="ed_access_group"):
            validate_startup_config(
                self._GOOD_URL, self._GOOD_DN, self._GOOD_PASSWORD, "",
            )

    def test_rejects_bad_ldap_url(self):
        with pytest.raises(EdConfigurationError, match="Invalid ED LDAP URL"):
            validate_startup_config(
                "http://ed.weill.cornell.edu", self._GOOD_DN, self._GOOD_PASSWORD,
                self._GOOD_GROUP,
            )

    def test_reports_every_missing_field_at_once(self):
        """One restart should surface every problem, not one field per boot."""
        with pytest.raises(EdConfigurationError) as excinfo:
            validate_startup_config("", "", "", "")
        message = str(excinfo.value)
        assert "ED_LDAP_BIND_DN" in message
        assert "ED_LDAP_BIND_PASSWORD" in message
        assert "ed_access_group" in message


class TestValidateStartupConfigWiring:
    """The lifespan WIRE from `if get_config_value(db, "ed_enabled")` (main.py)
    through to validate_startup_config() actually refusing app startup (#330).

    TestValidateStartupConfig above exhaustively covers the helper as a pure
    function, but never calls through main.py's lifespan -- so a mutation that
    drops the `ed_enabled` gate, no-ops the validate_startup_config() call, or
    hardcodes the bind password survives every one of those tests. This class
    boots a real TestClient(app) (running the real lifespan) against an
    isolated in-memory DB, so those call-site mutations are caught.

    Seeding SystemConfig directly is not enough: lifespan's own
    seed_system_config() reconciles file-managed keys (including ed_enabled)
    from YAML on every boot, so a pre-seeded DB row is overwritten before the
    gate is even checked. `load_yaml_config` is patched instead, the same way
    the app itself reads `ed.enabled` from a rendered auth_config.yaml.
    """

    @staticmethod
    def _fresh_engine_and_sessionmaker():
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)
        return engine, Session

    def _boot(self, yaml_cfg):
        """Boot the app against yaml_cfg; returns (app, cleanup)."""
        from app.main import app
        engine, Session = self._fresh_engine_and_sessionmaker()
        session = Session()

        def override_get_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        patches = [
            patch("app.database.SessionLocal", Session),
            patch("app.database.engine", engine),
            patch("app.config_loader.load_yaml_config", return_value=yaml_cfg),
        ]
        for p in patches:
            p.__enter__()

        def cleanup():
            for p in reversed(patches):
                p.__exit__(None, None, None)
            app.dependency_overrides.clear()
            session.close()

        return app, cleanup

    def test_startup_refuses_when_ed_enabled_and_ldap_unset(self, monkeypatch):
        monkeypatch.delenv("ED_LDAP_URL", raising=False)
        monkeypatch.delenv("ED_LDAP_BIND_DN", raising=False)
        monkeypatch.delenv("ED_LDAP_BIND_PASSWORD", raising=False)
        app, cleanup = self._boot({"ed": {
            "enabled": True,
            "access_group": "cn=g,ou=groups,dc=example,dc=org",
        }})
        try:
            with pytest.raises(EdConfigurationError):
                with TestClient(app):
                    pass
        finally:
            cleanup()

    def test_startup_succeeds_when_ed_enabled_and_ldap_configured(self, monkeypatch):
        monkeypatch.setenv("ED_LDAP_URL", "ldaps://ed.example.org:636")
        monkeypatch.setenv("ED_LDAP_BIND_DN", "cn=svc,dc=example,dc=org")
        monkeypatch.setenv("ED_LDAP_BIND_PASSWORD", "test-password")
        app, cleanup = self._boot({"ed": {
            "enabled": True,
            "access_group": "cn=g,ou=groups,dc=example,dc=org",
        }})
        try:
            with TestClient(app) as c:
                assert c.get("/health").status_code == 200
        finally:
            cleanup()

    def test_startup_refuses_when_only_bind_password_env_is_unset(self, monkeypatch):
        """Isolates ED_LDAP_BIND_PASSWORD: URL/bind_dn/access_group are all
        valid, only the password is missing -- catches a mutation that reads
        the wire's bind_password from anywhere other than the real env var
        (e.g. hardcoding a non-empty placeholder), which the
        all-fields-unset case above can't isolate."""
        monkeypatch.setenv("ED_LDAP_URL", "ldaps://ed.example.org:636")
        monkeypatch.setenv("ED_LDAP_BIND_DN", "cn=svc,dc=example,dc=org")
        monkeypatch.delenv("ED_LDAP_BIND_PASSWORD", raising=False)
        app, cleanup = self._boot({"ed": {
            "enabled": True,
            "access_group": "cn=g,ou=groups,dc=example,dc=org",
        }})
        try:
            with pytest.raises(EdConfigurationError, match="ED_LDAP_BIND_PASSWORD"):
                with TestClient(app):
                    pass
        finally:
            cleanup()

    def test_startup_refuses_when_only_ldap_url_env_is_unset(self, monkeypatch):
        """Isolates ED_LDAP_URL: bind_dn/bind_password/access_group are all
        valid, only the URL is missing -- catches a mutation that reads the
        wire's ldap_url from anywhere other than the real env/yaml config
        (e.g. hardcoding a valid placeholder), which a case that leaves every
        field unset can't isolate."""
        monkeypatch.delenv("ED_LDAP_URL", raising=False)
        monkeypatch.setenv("ED_LDAP_BIND_DN", "cn=svc,dc=example,dc=org")
        monkeypatch.setenv("ED_LDAP_BIND_PASSWORD", "test-password")
        app, cleanup = self._boot({"ed": {
            "enabled": True,
            "access_group": "cn=g,ou=groups,dc=example,dc=org",
        }})
        try:
            with pytest.raises(EdConfigurationError, match="Invalid ED LDAP URL"):
                with TestClient(app):
                    pass
        finally:
            cleanup()

    def test_startup_refuses_when_only_bind_dn_env_is_unset(self, monkeypatch):
        """Isolates ED_LDAP_BIND_DN: url/bind_password/access_group are all
        valid, only bind_dn is missing -- catches a mutation that reads the
        wire's bind_dn from anywhere other than the real env config (e.g.
        hardcoding a valid placeholder)."""
        monkeypatch.setenv("ED_LDAP_URL", "ldaps://ed.example.org:636")
        monkeypatch.delenv("ED_LDAP_BIND_DN", raising=False)
        monkeypatch.setenv("ED_LDAP_BIND_PASSWORD", "test-password")
        app, cleanup = self._boot({"ed": {
            "enabled": True,
            "access_group": "cn=g,ou=groups,dc=example,dc=org",
        }})
        try:
            with pytest.raises(EdConfigurationError, match="ED_LDAP_BIND_DN"):
                with TestClient(app):
                    pass
        finally:
            cleanup()

    def test_startup_refuses_when_only_access_group_is_unset(self, monkeypatch):
        """Isolates ed_access_group: url/bind_dn/bind_password are all valid,
        only access_group is missing from the yaml config -- catches a
        mutation that reads the wire's ed_access_group from anywhere other
        than the real DB config (e.g. hardcoding a valid placeholder)."""
        monkeypatch.setenv("ED_LDAP_URL", "ldaps://ed.example.org:636")
        monkeypatch.setenv("ED_LDAP_BIND_DN", "cn=svc,dc=example,dc=org")
        monkeypatch.setenv("ED_LDAP_BIND_PASSWORD", "test-password")
        app, cleanup = self._boot({"ed": {"enabled": True}})
        try:
            with pytest.raises(EdConfigurationError, match="ed_access_group"):
                with TestClient(app):
                    pass
        finally:
            cleanup()

    def test_startup_skips_validation_when_ed_disabled(self, monkeypatch):
        """Even with LDAP config fully unset, ed_enabled=False must not refuse
        to start -- a deployment that doesn't use ED authorization shouldn't be
        punished for an ED_LDAP_BIND_PASSWORD it will never read."""
        monkeypatch.delenv("ED_LDAP_URL", raising=False)
        monkeypatch.delenv("ED_LDAP_BIND_DN", raising=False)
        monkeypatch.delenv("ED_LDAP_BIND_PASSWORD", raising=False)
        app, cleanup = self._boot({"ed": {"enabled": False}})
        try:
            with TestClient(app) as c:
                assert c.get("/health").status_code == 200
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# D15: the memberURL budget caps SEARCHES, not URLs scanned
# ---------------------------------------------------------------------------

def _memberurl_group_conn(member_urls, filter_hit: bool):
    """A conn whose (uid=) lookup finds the user, whose group read returns
    `member_urls`, and whose per-URL filter evaluation either matches the user
    (`filter_hit=True`) or returns nothing."""
    mock_conn = MagicMock()

    def search_side_effect(search_base, search_filter, search_scope, attributes):
        if "(uid=" in search_filter:
            mock_conn.entries = [_make_entry(_GOU_USER_DN)]
        elif "groupOfNames" in search_filter:
            mock_conn.entries = [_make_entry(_GOU_GROUP_DN, memberURL=member_urls)]
        else:
            mock_conn.entries = [_make_entry(_GOU_USER_DN)] if filter_hit else []

    mock_conn.search.side_effect = search_side_effect
    return mock_conn


def _filter_eval_search_count(mock_conn) -> int:
    """How many of the conn's searches were per-memberURL filter evaluations
    (i.e. BASE reads of the user's own DN), as opposed to the uid lookup and
    the group read."""
    return sum(1 for call in mock_conn.search.call_args_list
               if call.kwargs.get("search_base") == _GOU_USER_DN
               and "(uid=" not in call.kwargs.get("search_filter", ""))


class TestMemberURLSearchBudget:
    """D15. `_dn_in_scope` is pure string work with no I/O and runs first, so
    the thing worth bounding is the number of memberURLs that actually reach
    the server. Capping the SCAN instead would silently deny a legitimate
    member who happens to sit past the cap in the attribute list."""

    # Each URL scopes over the user, so each one costs a real search.
    _SEARCHING_URLS = [
        f"ldap:///ou=people,dc=weill,dc=cornell,dc=edu??sub?(rule{i}=1)"
        for i in range(_MAX_MEMBERURL_SEARCHES * 3)
    ]
    # None of these scope over the user, so none of them costs a search.
    _NON_SEARCHING_URLS = [
        f"ldap:///uid=other{i:04d},ou=people,dc=weill,dc=cornell,dc=edu??base?(x=1)"
        for i in range(_MAX_MEMBERURL_SEARCHES * 2)
    ]

    def test_search_filter_helper_distinguishes_cost(self):
        """The budget is only meaningful if the helper it counts with is right."""
        assert _memberurl_search_filter(self._SEARCHING_URLS[0], _GOU_USER_DN) is not None
        assert _memberurl_search_filter(self._NON_SEARCHING_URLS[0], _GOU_USER_DN) is None
        assert _memberurl_search_filter("not-a-url", _GOU_USER_DN) is None

    def test_stops_at_the_cap_and_warns(self, caplog):
        """A group whose URLs would all require a search stops at the cap."""
        mock_conn = _memberurl_group_conn(self._SEARCHING_URLS, filter_hit=False)
        with caplog.at_level("WARNING", logger="app.ed_group_lookup"):
            result = _ldap_check_membership(
                cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
            )
        assert result is False
        assert _filter_eval_search_count(mock_conn) == _MAX_MEMBERURL_SEARCHES
        warnings = [r.getMessage() for r in caplog.records
                    if "memberURL search cap" in r.getMessage()]
        assert len(warnings) == 1
        assert _GOU_GROUP_DN in warnings[0]
        assert str(_MAX_MEMBERURL_SEARCHES) in warnings[0]

    def test_member_past_the_cap_in_scan_order_is_still_found(self):
        """The member sits far past the cap in the ATTRIBUTE LIST but is the
        only URL that needs a search -- scanning is unbounded, so they are
        still found. This is the approval the cap must never turn into a
        denial."""
        urls = self._NON_SEARCHING_URLS + [_GOU_MEMBERURL_PAA]
        assert len(urls) > _MAX_MEMBERURL_SEARCHES
        mock_conn = _memberurl_group_conn(urls, filter_hit=True)
        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is True
        # Exactly one URL ever reached the server, however long the list was.
        assert _filter_eval_search_count(mock_conn) == 1

    def test_group_under_the_cap_is_unaffected(self):
        """A group with fewer searching URLs than the cap evaluates them all."""
        urls = self._SEARCHING_URLS[:_MAX_MEMBERURL_SEARCHES - 1]
        mock_conn = _memberurl_group_conn(urls, filter_hit=False)
        result = _ldap_check_membership(
            cwid="paa2013", group_dn=_GOU_GROUP_DN, **LDAP_PARAMS, conn=mock_conn,
        )
        assert result is False
        assert _filter_eval_search_count(mock_conn) == len(urls)


# ---------------------------------------------------------------------------
# D17/D18: the login path bypasses BOTH caches
#
# Baseline (before the cache-aside move) always ran a fresh ED query at login.
# Nothing but these tests stops that from silently regressing: a live-cache hit
# up to _CACHE_TTL_SECONDS old would let a user whose access was revoked three
# minutes ago mint a NEW session.
# ---------------------------------------------------------------------------

_ACS_ATTRS = {
    "cwid": "test0001",
    "email": "test@med.cornell.edu",
    "display_name": "Test User",
}


def _blank_access_group(db):
    """Point ed_access_group at an empty string, as an unset config would."""
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "ed_access_group").first()
    row.value = json.dumps("")
    db.commit()


class TestLoginPathBypassesCache:
    """D17: `use_cache=False` on the login path governs the LIVE read as well
    as the stale one."""

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_acs_passes_use_cache_false(
        self, mock_extract, mock_get_client, mock_check_ed, client, seed_ed_enabled
    ):
        """Pin the security-critical keyword itself, not merely that the call
        happened -- deleting it would restore the regression silently."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = dict(_ACS_ATTRS)
        mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)

        client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        mock_check_ed.assert_called_once()
        assert mock_check_ed.call_args.kwargs["use_cache"] is False

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.ed_group_lookup._query_ed")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_acs_requeries_ed_despite_warm_live_cache(
        self, mock_extract, mock_get_client, mock_query, client, seed_ed_enabled
    ):
        """A warm live-cache entry saying True must not mint a session for a
        user ED now says is out."""
        clear_cache()
        set_cached_membership(
            _ACS_ATTRS["cwid"], ACCESS_GROUP, ADMIN_GROUP,
            MembershipResult(in_access_group=True, in_admin_group=False),
        )
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = dict(_ACS_ATTRS)
        mock_query.return_value = MembershipResult(in_access_group=False, in_admin_group=False)

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=not_authorized" in response.headers["location"]
        mock_query.assert_called_once()
        # ...and the fresh answer replaced the warm one: the login still WRITES
        # the cache, so the per-request re-checks that follow see the denial.
        assert get_cached_membership(
            _ACS_ATTRS["cwid"], ACCESS_GROUP, ADMIN_GROUP
        ) == MembershipResult(in_access_group=False, in_admin_group=False)

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.ed_group_lookup._query_ed")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_acs_does_not_serve_stale_during_outage(
        self, mock_extract, mock_get_client, mock_query, client, seed_ed_enabled
    ):
        """Both caches hold a last-known-good True and ED is down: the login
        path still refuses. An outage blocks NEW sessions -- the stronger gate."""
        clear_cache()
        set_cached_membership(
            _ACS_ATTRS["cwid"], ACCESS_GROUP, ADMIN_GROUP,
            MembershipResult(in_access_group=True, in_admin_group=False),
        )
        # Expire only the live entry; the stale entry stays inside _STALE_MAX_AGE.
        _group_cache.clear()
        assert get_stale_membership(_ACS_ATTRS["cwid"], ACCESS_GROUP, ADMIN_GROUP) is not None

        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = dict(_ACS_ATTRS)
        mock_query.side_effect = EdUnavailableError("Connection refused")

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=directory_unavailable" in response.headers["location"]

    def test_per_request_check_still_serves_stale_during_outage(self):
        """The counterpart: use_cache=True (auth.py) keeps the stale fallback,
        so an outage does not evict an already-authorized session."""
        clear_cache()
        set_cached_membership(
            "stale001", ACCESS_GROUP, ADMIN_GROUP,
            MembershipResult(in_access_group=True, in_admin_group=False),
        )
        _group_cache.clear()
        with patch("app.ed_group_lookup._query_ed",
                   side_effect=EdUnavailableError("Connection refused")):
            result = check_ed_membership(
                "stale001", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS, use_cache=True
            )
        assert result == MembershipResult(in_access_group=True, in_admin_group=False)

    def test_use_cache_false_still_writes_both_caches(self):
        """Module-level counterpart to the ACS assertion above."""
        clear_cache()
        fresh = MembershipResult(in_access_group=True, in_admin_group=True)
        with patch("app.ed_group_lookup._query_ed", return_value=fresh) as mock_query:
            assert check_ed_membership(
                "warm0001", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS, use_cache=False
            ) == fresh
        mock_query.assert_called_once()
        assert get_cached_membership("warm0001", ACCESS_GROUP, ADMIN_GROUP) == fresh
        assert get_stale_membership("warm0001", ACCESS_GROUP, ADMIN_GROUP) == fresh

    def test_use_cache_false_ignores_a_live_hit_every_time(self):
        """Two consecutive use_cache=False calls both reach ED."""
        clear_cache()
        allowed = MembershipResult(in_access_group=True, in_admin_group=False)
        with patch("app.ed_group_lookup._query_ed", return_value=allowed) as mock_query:
            check_ed_membership("warm0002", ACCESS_GROUP, ADMIN_GROUP,
                                **LDAP_PARAMS, use_cache=False)
            check_ed_membership("warm0002", ACCESS_GROUP, ADMIN_GROUP,
                                **LDAP_PARAMS, use_cache=False)
        assert mock_query.call_count == 2


# ---------------------------------------------------------------------------
# D12/D18: an unconfigured access group fails closed at every layer
# ---------------------------------------------------------------------------

class TestEmptyAccessGroupFailsClosed:
    """An empty group DN used to reach LDAP as an empty search_base and come
    back False -- fail-closed only by accident. It is now explicit, and each
    layer must convert it into a denial rather than a 500."""

    @pytest.mark.parametrize("access_group", ["", "   ", "\t\n"])
    def test_module_rejects_empty_access_group(self, access_group):
        with patch("app.ed_group_lookup._query_ed") as mock_query:
            with pytest.raises(ValueError):
                check_ed_membership("nogrp001", access_group, ADMIN_GROUP, **LDAP_PARAMS)
        mock_query.assert_not_called()

    def test_module_allows_empty_admin_group(self):
        """admin_group may legitimately be empty (documented short-circuit)."""
        clear_cache()
        with patch("app.ed_group_lookup._query_ed",
                   return_value=MembershipResult(in_access_group=True, in_admin_group=False)):
            result = check_ed_membership("noadm001", ACCESS_GROUP, "", **LDAP_PARAMS)
        assert result == MembershipResult(in_access_group=True, in_admin_group=False)

    def test_per_request_check_converts_valueerror_to_401(
        self, client, db, seed_ed_enabled
    ):
        """auth.py must not let the ValueError become a 500."""
        clear_cache()
        _blank_access_group(db)
        user = User(
            cwid="nogrp002",
            email="nogrp002@med.cornell.edu",
            display_name="No Group",
            role="user",
            auth_method="saml",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_session_cookie(user, db)

        response = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "directory_unavailable"

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_acs_redirects_without_calling_ldap(
        self, mock_extract, mock_get_client, mock_check_ed, client, db, seed_ed_enabled
    ):
        """saml_routes.py guards ahead of the call, so no LDAP work is even
        attempted, and the browser sees directory_unavailable."""
        clear_cache()
        _blank_access_group(db)
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = dict(_ACS_ATTRS)

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "dummy", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=directory_unavailable" in response.headers["location"]
        mock_check_ed.assert_not_called()


# ---------------------------------------------------------------------------
# Department from ED (Runs admin view)
# ---------------------------------------------------------------------------


class TestPickDepartment:
    def test_primary_department_wins(self):
        attrs = {
            "weillCornellEduPrimaryDepartment": ["Library"],
            "weillCornellEduDepartment": ["Security", "Library"],
        }
        assert pick_department(attrs) == "Library"

    def test_falls_back_to_first_listed_department(self):
        assert pick_department({"weillCornellEduDepartment": ["  Medicine ", "Other"]}) == "Medicine"

    def test_blank_or_missing_is_none(self):
        assert pick_department({}) is None
        assert pick_department({"weillCornellEduPrimaryDepartment": ["  "]}) is None

    def test_truncated_to_column_width(self):
        value = "D" * (DEPARTMENT_MAX_LENGTH + 40)
        assert len(pick_department({"weillCornellEduPrimaryDepartment": [value]})) == DEPARTMENT_MAX_LENGTH


class TestFetchEdDepartment:
    @patch("app.ed_group_lookup._bind")
    def test_reads_primary_department(self, mock_bind):
        conn = mock_bind.return_value.__enter__.return_value
        conn.entries = [MagicMock(entry_attributes_as_dict={
            "weillCornellEduPrimaryDepartment": ["Pediatrics"]})]
        assert fetch_ed_department("abc1234", LDAP_PARAMS["cfg"]) == "Pediatrics"
        kwargs = conn.search.call_args.kwargs
        assert kwargs["search_filter"] == "(uid=abc1234)"

    @patch("app.ed_group_lookup._bind")
    def test_no_entry_is_none(self, mock_bind):
        conn = mock_bind.return_value.__enter__.return_value
        conn.entries = []
        assert fetch_ed_department("abc1234", LDAP_PARAMS["cfg"]) is None

    @patch("app.ed_group_lookup._bind", side_effect=EdUnavailableError("down"))
    def test_ed_error_is_swallowed_and_logged(self, _mock_bind, caplog):
        assert fetch_ed_department("abc1234", LDAP_PARAMS["cfg"]) is None
        assert any(r.exc_info for r in caplog.records)


class TestAcsStoresDepartment:
    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.fetch_ed_department", return_value="Pediatrics")
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_department_stored_on_login(
        self, mock_extract, mock_get_client, mock_check_ed, _mock_dept,
        client, db, seed_ed_enabled
    ):
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = {"cwid": "test0001", "email": "test@med.cornell.edu", "display_name": "Test User"}
        mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)

        response = client.post("/api/saml/acs", data={"SAMLResponse": "dummy", "RelayState": "/"},
                               follow_redirects=False)
        assert response.status_code == 302
        assert db.query(User).filter(User.cwid == "test0001").one().department == "Pediatrics"

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.fetch_ed_department", return_value=None)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    @patch("app.api.saml_routes.extract_user_attrs")
    def test_missing_department_leaves_null_and_keeps_existing(
        self, mock_extract, mock_get_client, mock_check_ed, _mock_dept,
        client, db, seed_ed_enabled
    ):
        clear_cache()
        db.add(User(cwid="test0001", email="test@med.cornell.edu", display_name="Test User",
                    role="user", auth_method="saml", department="Kept Dept"))
        db.commit()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_extract.return_value = {"cwid": "test0001", "email": "test@med.cornell.edu", "display_name": "Test User"}
        mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)

        response = client.post("/api/saml/acs", data={"SAMLResponse": "dummy", "RelayState": "/"},
                               follow_redirects=False)
        assert response.status_code == 302
        db.expire_all()
        assert db.query(User).filter(User.cwid == "test0001").one().department == "Kept Dept"
