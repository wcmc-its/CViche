"""The SAML ACS workflow, driven directly -- no TestClient (#349).

pysaml2 is stubbed at get_saml_client and ED at check_ed_membership /
fetch_ed_department, the same seams the route-level suites use. Cases, one
test (or parametrized row) each:
  valid response -> SamlLoginSession, provisioned row, decodable token, LOGIN_SUCCESS
  parser returns None -> NO_RESPONSE
  signature-class exception -> BAD_SIGNATURE ([SECURITY] warning)
  operational / SAMLError exception -> RESPONSE_REJECTED (error log)
  any other parser exception -> PARSER_ERROR (logged with traceback)
  Destination not ours -> WRONG_DESTINATION, ID not recorded; ours or absent -> continues
  two assertion IDs -> MULTIPLE_ASSERTIONS even when the opt-out is set
  same ID twice -> REPLAYED
  no ID -> NO_ASSERTION_ID when failing closed; admitted under the opt-out
  replay-cache bug / provisioning bug -> propagates, not a failure
  no usable identifier -> MISSING_ATTRIBUTES
  ED: not in access group -> NOT_AUTHORIZED (LOGIN_FAILED); unavailable or
      unconfigured -> DIRECTORY_UNAVAILABLE; admin/staff/user role; use_cache=False;
      partner scope admitted / denied without ED; ED off keeps the stored role
  department read only once authorized, and only with ED on
  store unwritable -> SESSION_STORE_UNAVAILABLE; epoch unreadable -> SESSION_STATE_UNAVAILABLE
"""
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import redis.exceptions
from saml2.mdstore import SourceNotFound
from saml2.response import IncorrectlySigned, StatusError
from saml2.sigver import CertificateError, SigverError

import app.session_idle as session_idle
from app.auth import decode_session_cookie
from app.ed_group_lookup import EdUnavailableError, MembershipResult
from app.models import SystemConfig, User
from app.saml_replay import SamlReplayCache, set_replay_cache
from app.services.saml_service import SamlLoginFailure, SamlLoginSession, authenticate_saml_response
from app.session_idle import IdleSessionStore

_ROUTE_LOGGER = "app.api.saml_routes"
_ACS = "https://cviche.med.cornell.edu/api/saml/acs"
_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["SamlUser@Med.Cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["SAML User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["samluser@med.cornell.edu"],
}
_ED_ENV = {
    "ED_LDAP_URL": "ldaps://ed.test:636",
    "ED_LDAP_BIND_DN": "cn=svc",
    "ED_LDAP_BIND_PASSWORD": "pw",
}


def _authn_response(ids=("_a1",), identity=None, destination=None):
    """A parsed-pysaml2-AuthnResponse stand-in: the attributes the workflow reads."""
    assertions = [SimpleNamespace(id=aid, conditions=None, subject=None) for aid in ids]
    return SimpleNamespace(
        assertions=assertions,
        assertion=assertions[-1] if assertions else None,
        not_on_or_after=0,
        response=SimpleNamespace(destination=destination),
        return_addrs=[_ACS],
        get_identity=lambda: dict(_IDENTITY if identity is None else identity),
    )


def _config(db, **values):
    for key, value in values.items():
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row is None:
            db.add(SystemConfig(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)
    db.commit()


def _events(caplog, name):
    return [r for r in caplog.records if r.getMessage() == name]


@pytest.fixture
def saml(db, seed_saml_mode):
    """SAML mode with the epoch seeded (the lifespan does this in a real app)."""
    _config(db, session_epoch=0)


@pytest.fixture
def ed(db, seed_ed_enabled, monkeypatch):
    _config(db, session_epoch=0)
    for key, value in _ED_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def replay_cache():
    cache = SamlReplayCache("", fail_closed=True)
    set_replay_cache(cache)
    yield cache
    set_replay_cache(None)


@pytest.fixture
def parser():
    """Patch the pysaml2 client; the test sets parser.return_value / side_effect."""
    with patch("app.services.saml_service.get_saml_client") as get_client:
        yield get_client.return_value.parse_authn_request_response


def _login(db, parser, response=None):
    parser.return_value = _authn_response() if response is None else response
    return authenticate_saml_response("base64data", db)


# --- success ------------------------------------------------------------------

def test_valid_response_signs_in_and_logs_login_success(db, saml, replay_cache, parser, caplog):
    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        outcome = _login(db, parser)

    assert isinstance(outcome, SamlLoginSession)
    user = db.query(User).one()
    assert (user.cwid, user.email, user.display_name, user.auth_method, user.role) == (
        "samluser", "samluser@med.cornell.edu", "SAML User", "saml", "user",
    )
    assert decode_session_cookie(outcome.token)["user_id"] == user.id
    parser.assert_called_once_with("base64data", "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST")
    [event] = _events(caplog, "LOGIN_SUCCESS")
    assert (event.name, event.user_id, event.email, event.role, event.auth_method) == (
        _ROUTE_LOGGER, user.id, "samluser@med.cornell.edu", "user", "saml",
    )


# --- the parsing boundary -----------------------------------------------------

def test_parser_returning_none_is_no_response(db, saml, parser, caplog):
    parser.return_value = None

    with caplog.at_level(logging.WARNING, logger=_ROUTE_LOGGER):
        assert authenticate_saml_response("x", db) is SamlLoginFailure.NO_RESPONSE
    assert "SAML ACS: authn_response is None (invalid assertion)" in caplog.messages


@pytest.mark.parametrize("exc, failure, level, message", [
    (SigverError("sig"), SamlLoginFailure.BAD_SIGNATURE, logging.WARNING,
     "[SECURITY] SAML signature validation failed: sig"),
    (CertificateError("cert"), SamlLoginFailure.BAD_SIGNATURE, logging.WARNING,
     "[SECURITY] SAML signature validation failed: cert"),
    (IncorrectlySigned("signed"), SamlLoginFailure.BAD_SIGNATURE, logging.WARNING,
     "[SECURITY] SAML signature validation failed: signed"),
    (RuntimeError("config"), SamlLoginFailure.RESPONSE_REJECTED, logging.ERROR,
     "SAML ACS processing failed: config"),
    (OSError("network"), SamlLoginFailure.RESPONSE_REJECTED, logging.ERROR,
     "SAML ACS processing failed: network"),
    (SourceNotFound("md"), SamlLoginFailure.RESPONSE_REJECTED, logging.ERROR,
     "SAML ACS processing failed: md"),
    (StatusError("idp status"), SamlLoginFailure.RESPONSE_REJECTED, logging.ERROR,
     "SAML ACS processing failed: idp status"),
    (Exception("AudienceRestrictions conditions not satisfied!"), SamlLoginFailure.PARSER_ERROR,
     logging.ERROR, "[SECURITY] SAML response rejected: unexpected parser error"),
    (TypeError("bug"), SamlLoginFailure.PARSER_ERROR, logging.ERROR,
     "[SECURITY] SAML response rejected: unexpected parser error"),
])
def test_parser_exception_maps_to_its_clause(db, saml, parser, caplog, exc, failure, level, message):
    parser.side_effect = exc

    with caplog.at_level(logging.WARNING, logger=_ROUTE_LOGGER):
        assert authenticate_saml_response("x", db) is failure

    [record] = [r for r in caplog.records if r.getMessage() == message]
    assert (record.name, record.levelno) == (_ROUTE_LOGGER, level)
    assert db.query(User).count() == 0


def test_get_saml_client_failure_is_inside_the_parsing_boundary(db, saml):
    with patch("app.services.saml_service.get_saml_client", side_effect=RuntimeError("no cert dir")):
        assert authenticate_saml_response("x", db) is SamlLoginFailure.RESPONSE_REJECTED


# --- Destination and the replay gate ------------------------------------------

def test_foreign_destination_is_rejected_before_the_replay_gate(db, saml, replay_cache, parser, caplog):
    with caplog.at_level(logging.WARNING, logger=_ROUTE_LOGGER):
        outcome = _login(db, parser, _authn_response(destination="https://evil.test/acs"))

    assert outcome is SamlLoginFailure.WRONG_DESTINATION
    assert replay_cache._local == {}
    assert caplog.messages == [
        f"[SECURITY] SAML response rejected: Destination 'https://evil.test/acs' is not this SP's ACS ['{_ACS}']"
    ]


def test_our_own_destination_is_accepted(db, saml, replay_cache, parser):
    assert isinstance(_login(db, parser, _authn_response(destination=_ACS)), SamlLoginSession)


@pytest.mark.parametrize("fail_closed", [True, False])
def test_two_assertion_ids_are_rejected_whatever_the_opt_out(db, saml, replay_cache, parser, fail_closed):
    with patch("app.services.saml_service.replay_fail_closed", return_value=fail_closed):
        outcome = _login(db, parser, _authn_response(ids=("_a", "_b")))

    assert outcome is SamlLoginFailure.MULTIPLE_ASSERTIONS
    assert replay_cache._local == {}


def test_second_presentation_of_an_id_is_replayed(db, saml, replay_cache, parser):
    assert isinstance(_login(db, parser, _authn_response(ids=("_once",))), SamlLoginSession)

    assert _login(db, parser, _authn_response(ids=("_once",))) is SamlLoginFailure.REPLAYED


def test_missing_id_fails_closed(db, saml, replay_cache, parser):
    with patch("app.services.saml_service.replay_fail_closed", return_value=True):
        assert _login(db, parser, _authn_response(ids=())) is SamlLoginFailure.NO_ASSERTION_ID
    assert db.query(User).count() == 0


def test_missing_id_is_admitted_under_the_opt_out_with_a_warning(db, saml, replay_cache, parser, caplog):
    with patch("app.services.saml_service.replay_fail_closed", return_value=False), \
            caplog.at_level(logging.WARNING, logger=_ROUTE_LOGGER):
        outcome = _login(db, parser, _authn_response(ids=()))

    assert isinstance(outcome, SamlLoginSession)
    assert ("SAML ACS: no assertion ID extractable; replay gate skipped "
            "(CVICHE_SAML_REPLAY_FAIL_CLOSED opt-out)") in caplog.messages


def test_a_replay_cache_bug_propagates(db, saml, parser):
    broken = SamlReplayCache("", fail_closed=True)
    broken.check_and_record = MagicMock(side_effect=TypeError("bug"))
    set_replay_cache(broken)
    try:
        with pytest.raises(TypeError):
            _login(db, parser)
    finally:
        set_replay_cache(None)
    assert db.query(User).count() == 0


@patch("app.services.saml_service.extract_user_attrs")
def test_email_is_normalized_again_before_provisioning(extract, db, saml, replay_cache, parser):
    """Belt and braces over extract_user_attrs's own normalization (#348)."""
    extract.return_value = {"cwid": "u0001", "email": " Mixed@Case.EDU ", "display_name": "U"}

    assert _login(db, parser).user.email == "mixed@case.edu"


def test_no_usable_identifier_is_missing_attributes(db, saml, replay_cache, parser):
    outcome = _login(db, parser, _authn_response(identity={"displayName": ["Nameless"]}))

    assert outcome is SamlLoginFailure.MISSING_ATTRIBUTES


# --- ED authorization and role ------------------------------------------------

@patch("app.services.saml_service.check_ed_membership")
def test_user_outside_the_access_group_is_not_authorized(check_ed, db, ed, replay_cache, parser, caplog):
    check_ed.return_value = MembershipResult(in_access_group=False, in_admin_group=False)

    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        assert _login(db, parser) is SamlLoginFailure.NOT_AUTHORIZED

    [failed] = _events(caplog, "LOGIN_FAILED")
    assert (failed.name, failed.cwid, failed.reason) == (_ROUTE_LOGGER, "samluser", "not_authorized")
    assert "SAML ACS: user samluser not in ED access group" in caplog.messages
    assert db.query(User).count() == 0


@patch("app.services.saml_service.fetch_ed_department")
@patch("app.services.saml_service.check_ed_membership", side_effect=EdUnavailableError("down"))
def test_ed_unavailable_is_directory_unavailable(check_ed, department, db, ed, replay_cache, parser):
    assert _login(db, parser) is SamlLoginFailure.DIRECTORY_UNAVAILABLE
    department.assert_not_called()
    assert db.query(User).count() == 0


@pytest.mark.parametrize("unset", ["ED_LDAP_URL", "ED_LDAP_BIND_DN"])
@patch("app.services.saml_service.check_ed_membership")
def test_unconfigured_ed_is_directory_unavailable_without_ldap(
    check_ed, db, ed, replay_cache, parser, monkeypatch, unset,
):
    monkeypatch.delenv(unset)

    assert _login(db, parser) is SamlLoginFailure.DIRECTORY_UNAVAILABLE
    check_ed.assert_not_called()


@pytest.mark.parametrize("membership, role", [
    (MembershipResult(in_access_group=True, in_admin_group=True), "admin"),
    (MembershipResult(in_access_group=True, in_admin_group=False, in_staff_group=True), "staff"),
    (MembershipResult(in_access_group=True, in_admin_group=False), "user"),
])
@patch("app.services.saml_service.fetch_ed_department", return_value="Pediatrics")
@patch("app.services.saml_service.check_ed_membership")
def test_ed_membership_sets_role_and_department(
    check_ed, _department, db, ed, replay_cache, parser, membership, role,
):
    check_ed.return_value = membership

    outcome = _login(db, parser)

    assert (outcome.user.role, outcome.user.department) == (role, "Pediatrics")
    assert check_ed.call_args.kwargs["use_cache"] is False
    assert check_ed.call_args.kwargs["cwid"] == "samluser"


@pytest.mark.parametrize("scopes, expected", [("hss.edu", SamlLoginSession), ("nyp.org", None)])
@patch("app.services.saml_service.check_ed_membership")
def test_partner_scope_is_decided_without_ed(check_ed, db, ed, replay_cache, parser, scopes, expected):
    _config(db, ed_partner_scopes=scopes)
    partner = {"urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["js1234@hss.edu"]}

    outcome = _login(db, parser, _authn_response(identity=partner))

    if expected is None:
        assert outcome is SamlLoginFailure.NOT_AUTHORIZED
    else:
        assert (outcome.user.cwid, outcome.user.role) == ("js1234@hss.edu", "user")
    check_ed.assert_not_called()


@patch("app.services.saml_service.fetch_ed_department")
def test_ed_off_keeps_the_stored_role_and_reads_no_department(department, db, saml, replay_cache, parser):
    db.add(User(cwid="samluser", email="samluser@med.cornell.edu", display_name="Old",
                role="admin", auth_method="saml"))
    db.commit()

    outcome = _login(db, parser)

    assert outcome.user.role == "admin"
    department.assert_not_called()


@patch("app.services.saml_service.provision_user", side_effect=TypeError("bug"))
def test_a_provisioning_bug_propagates(_provision, db, saml, replay_cache, parser):
    with pytest.raises(TypeError):
        _login(db, parser)


# --- session minting ----------------------------------------------------------

def test_unwritable_store_is_session_store_unavailable(db, saml, replay_cache, parser, monkeypatch, caplog):
    store = IdleSessionStore("redis://fake", 1200)
    store._client = MagicMock()
    store._client.set.side_effect = redis.exceptions.ConnectionError("valkey down")
    monkeypatch.setattr(session_idle, "_store", store)

    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        assert _login(db, parser) is SamlLoginFailure.SESSION_STORE_UNAVAILABLE

    [failed] = _events(caplog, "LOGIN_FAILED")
    assert (failed.cwid, failed.reason) == ("samluser", "session_store_unavailable")
    assert [r.reason for r in _events(caplog, "SESSION_STORE_UNAVAILABLE")] == ["start"]
    assert not _events(caplog, "LOGIN_SUCCESS")
    assert db.query(User).one().cwid == "samluser"


def test_unreadable_epoch_is_session_state_unavailable(db, saml, replay_cache, parser, caplog):
    _config(db, session_epoch="abc")

    with caplog.at_level(logging.INFO, logger=_ROUTE_LOGGER):
        assert _login(db, parser) is SamlLoginFailure.SESSION_STATE_UNAVAILABLE

    [failed] = _events(caplog, "LOGIN_FAILED")
    assert (failed.cwid, failed.reason) == ("samluser", "session_state_unavailable")
    assert not _events(caplog, "SESSION_STORE_UNAVAILABLE")
