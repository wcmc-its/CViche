"""Tests for Enterprise Directory group membership check module."""
import time
from unittest.mock import patch, MagicMock

import pytest

from app.ed_group_lookup import (
    check_ed_membership,
    get_cached_membership,
    set_cached_membership,
    get_stale_membership,
    clear_cache,
    EdUnavailableError,
    _ldap_check_membership,
    _group_cache,
    _stale_cache,
)


# Common test parameters for LDAP calls
LDAP_PARAMS = {
    "ldap_url": "ldaps://ed.weill.cornell.edu:636",
    "bind_dn": "cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
    "bind_password": "test-password",
    "search_base": "dc=weill,dc=cornell,dc=edu",
}
ACCESS_GROUP = "cn=App-CViche-Users,ou=Groups,dc=weill,dc=cornell,dc=edu"
ADMIN_GROUP = "cn=App-CViche-Admins,ou=Groups,dc=weill,dc=cornell,dc=edu"


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
    """Tests for the public check_ed_membership() function."""

    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_user_in_access_group_returns_true(self, mock_ldap):
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
        assert result == {"in_access_group": True, "in_admin_group": False}

    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_user_not_in_access_group_returns_false(self, mock_ldap):
        """User not in access group -> both False. Admin check should NOT be called."""
        mock_ldap.return_value = False
        result = check_ed_membership(
            "outsider@example.com", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS
        )
        assert result == {"in_access_group": False, "in_admin_group": False}
        # Admin group should not be checked when access is denied
        assert mock_ldap.call_count == 1

    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_admin_requires_access_group(self, mock_ldap):
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
        assert result == {"in_access_group": False, "in_admin_group": False}

    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_user_in_both_groups(self, mock_ldap):
        """User in both access and admin groups -> both True."""
        mock_ldap.return_value = True
        result = check_ed_membership(
            "admin@med.cornell.edu", ACCESS_GROUP, ADMIN_GROUP, **LDAP_PARAMS
        )
        assert result == {"in_access_group": True, "in_admin_group": True}

    @patch("app.ed_group_lookup._ldap_check_membership")
    def test_ldap_failure_raises_ed_unavailable(self, mock_ldap):
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
    """Tests for the TTL cache and stale cache functions."""

    def test_set_and_get_cached_membership(self):
        """set_cached_membership then get_cached_membership returns same dict."""
        data = {"in_access_group": True, "in_admin_group": False}
        set_cached_membership("a@b.com", data)
        result = get_cached_membership("a@b.com")
        assert result == {"in_access_group": True, "in_admin_group": False}

    def test_cache_miss_returns_none(self):
        """get_cached_membership for unknown email returns None."""
        assert get_cached_membership("unknown@b.com") is None

    def test_stale_cache_returns_last_known(self):
        """Stale cache returns data after TTL cache is cleared."""
        data = {"in_access_group": True, "in_admin_group": True}
        set_cached_membership("a@b.com", data)
        # Manually clear the TTL cache (simulating expiry)
        _group_cache.clear()
        assert get_cached_membership("a@b.com") is None  # TTL cache empty
        stale = get_stale_membership("a@b.com")
        assert stale is not None
        assert stale["in_access_group"] is True
        assert stale["in_admin_group"] is True

    def test_stale_cache_rejects_old_entries(self):
        """Stale cache returns None for entries older than 30 minutes."""
        data = {"in_access_group": True, "in_admin_group": False}
        set_cached_membership("a@b.com", data)
        # Manually backdate the stale entry beyond the 1800s safety bound
        _stale_cache["a@b.com"]["timestamp"] = time.time() - 2000
        assert get_stale_membership("a@b.com") is None

    def test_clear_cache_empties_both(self):
        """clear_cache() empties both TTL and stale caches."""
        data = {"in_access_group": True, "in_admin_group": False}
        set_cached_membership("a@b.com", data)
        assert get_cached_membership("a@b.com") is not None
        assert get_stale_membership("a@b.com") is not None
        clear_cache()
        assert get_cached_membership("a@b.com") is None
        assert get_stale_membership("a@b.com") is None


# ---------------------------------------------------------------------------
# TestDnComparison -- 1 test for case-insensitive DN matching
# ---------------------------------------------------------------------------

class TestDnComparison:
    """Tests for case-insensitive DN comparison in LDAP membership check."""

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_case_insensitive_dn_match(self, mock_server_cls, mock_conn_cls):
        """memberOf with mixed-case DN matches lowercase group_dn."""
        # Build a mock LDAP entry with memberOf containing mixed-case DN
        mock_entry = MagicMock()
        mock_entry.entry_dn = "cn=Test User,ou=People,dc=weill,dc=cornell,dc=edu"
        # memberOf returns mixed-case group DN
        mock_entry.__getitem__ = lambda self, key: {
            "memberOf": [
                "CN=App-CViche-Users,OU=Groups,DC=weill,DC=cornell,DC=edu"
            ],
        }.get(key, MagicMock())

        # Configure the mock connection
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        # First search (user lookup) returns the mock entry
        def search_side_effect(search_base, search_filter, search_scope, attributes):
            if "mail=" in search_filter:
                mock_conn.entries = [mock_entry]
            else:
                mock_conn.entries = []

        mock_conn.search.side_effect = search_side_effect
        mock_conn.entries = [mock_entry]

        result = _ldap_check_membership(
            email="testuser@med.cornell.edu",
            group_dn="cn=App-CViche-Users,ou=Groups,dc=weill,dc=cornell,dc=edu",
            ldap_url="ldaps://ed.weill.cornell.edu:636",
            bind_dn="cn=svc,ou=SA,dc=weill,dc=cornell,dc=edu",
            bind_password="pass",
            search_base="dc=weill,dc=cornell,dc=edu",
        )
        assert result is True
