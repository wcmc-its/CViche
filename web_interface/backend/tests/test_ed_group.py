"""Tests for Enterprise Directory group membership check module."""
import os
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
    _parse_memberurl,
    _user_matches_memberurl,
    _group_cache,
    _stale_cache,
)
from ldap3 import BASE, LEVEL, SUBTREE


# Common test parameters for LDAP calls
LDAP_PARAMS = {
    "ldap_url": "ldaps://ed.weill.cornell.edu:636",
    "bind_dn": "cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
    "bind_password": "test-password",
    "search_base": "dc=weill,dc=cornell,dc=edu",
}
ACCESS_GROUP = "cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"
ADMIN_GROUP = "cn=ITS:Library:CViche/admin-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"


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
                "CN=ITS:Library:CViche/user-role,OU=application security,OU=groups,DC=weill,DC=cornell,DC=edu"
            ],
        }.get(key, MagicMock())

        # Configure the mock connection
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

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
            ldap_url="ldaps://ed.weill.cornell.edu:636",
            bind_dn="cn=svc,ou=SA,dc=weill,dc=cornell,dc=edu",
            bind_password="pass",
            search_base="dc=weill,dc=cornell,dc=edu",
        )
        assert result is True

# ---------------------------------------------------------------------------
# TestGroupOfURLs -- WCM's house-style group schema (memberURL, not member)
# ---------------------------------------------------------------------------

def _make_entry(dn: str, **attrs):
    """Build a mock ldap3.Entry whose entry_dn returns dn and whose
    __getitem__ returns the supplied attribute lists (missing -> [])."""
    entry = MagicMock()
    entry.entry_dn = dn
    entry.__getitem__ = lambda self, key, _a=attrs: _a.get(key, [])
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


class TestGroupOfURLsMembership:
    """End-to-end _ldap_check_membership tests for WCM's groupOfURLs schema.

    Regression coverage for BUG D: until 2026-05-20 the code's fallback used
    (&(objectClass=groupOfNames)(cn=*)) and only read the `member` attribute.
    WCM ED groups are groupOfURLs with memberURL, so every membership check
    returned False -> would have locked every user out post-SAML cutover.
    """

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_user_in_groupofurls_returns_true(self, _mock_server_cls, mock_conn_cls):
        """The WCM hybrid pattern: group has memberURL for the user; filter matches."""
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

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
        )
        assert result is True

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_user_listed_but_filter_excludes_returns_false(self, _mock_server_cls, mock_conn_cls):
        """User's memberURL is in the group but they no longer satisfy the filter
        (e.g., personTypeCode changed from academic-faculty). Must deny access."""
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

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
        )
        assert result is False

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_user_not_in_any_memberurl_returns_false(self, _mock_server_cls, mock_conn_cls):
        """Group has memberURLs but none point at the user -- short-circuits."""
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
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
        )
        assert result is False

    @patch("app.ed_group_lookup.Connection")
    @patch("app.ed_group_lookup.Server")
    def test_groupofnames_member_attribute_still_works(self, _mock_server_cls, mock_conn_cls):
        """Backward compat: a real groupOfNames group with a `member` attribute
        is still recognized (the fallback now handles both schemas)."""
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

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
        )
        assert result is True

# ---------------------------------------------------------------------------
# Integration tests: ACS handler ED wiring
# ---------------------------------------------------------------------------

from app.models import User
from app.auth import create_session_cookie, COOKIE_NAME


def _mock_saml_client(identity_dict):
    """Create a mock Saml2Client with pre-configured ACS response."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.get_identity.return_value = identity_dict
    mock_client.parse_authn_request_response.return_value = mock_response
    return mock_client


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
        mock_check_ed.return_value = {"in_access_group": False, "in_admin_group": False}

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
        mock_check_ed.return_value = {"in_access_group": True, "in_admin_group": False}

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

        set_cached_membership("test0001", {"in_access_group": True, "in_admin_group": False})
        token = create_session_cookie(user)

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

        set_cached_membership("test0001", {"in_access_group": False, "in_admin_group": False})
        token = create_session_cookie(user)

        response = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert response.status_code == 401

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

        token = create_session_cookie(user)

        response = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert response.status_code == 200
