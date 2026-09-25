"""Real-crypto proof that SAML signature enforcement is not bypassable.

The SP is configured with ``want_assertions_signed=True`` (app/saml_client.py).
Every other SAML test in this suite MOCKS ``parse_authn_request_response``, so
none of them actually exercise pysaml2's signature verification -- which is the
one thing an "auth bypass" would exploit. This test closes that gap by driving
REAL pysaml2 7.5.5 + xmlsec1 end to end:

  1. a genuinely signed assertion is ACCEPTED,
  2. a tampered-after-signing assertion is REJECTED,
  3. an assertion signed by an untrusted key is REJECTED,
  4. an UNSIGNED assertion is REJECTED (because want_assertions_signed=True),
  5. the same unsigned assertion is ACCEPTED once that flag is False -- proving
     the flag is load-bearing, i.e. flipping it reintroduces the bypass.

Skips cleanly where xmlsec1/openssl aren't installed (e.g. minimal CI images).
The prod log line "saml2.sigver error=18 EVP_VerifyFinal FAIL" is pysaml2's
per-certificate trial during IdP cert rollover; cases 2-4 show that if NO cert
verifies, parse raises and login is denied.
"""
import base64
import os
import shutil
import subprocess
import tempfile
import warnings

import pytest

from saml2 import BINDING_HTTP_POST
from saml2.config import Config
from saml2.server import Server
from saml2.client import Saml2Client
from saml2.metadata import entity_descriptor
from saml2.saml import NAMEID_FORMAT_EMAILADDRESS

from app.saml_client import extract_user_attrs
from app.saml_replay import assertion_ids

_XMLSEC = shutil.which("xmlsec1")
_OPENSSL = shutil.which("openssl")
pytestmark = pytest.mark.skipif(
    not (_XMLSEC and _OPENSSL),
    reason="needs xmlsec1 + openssl for real SAML signature verification",
)

IDP_EID = "https://idp.test.local/idp"
SP_EID = "https://sp.test.local/sp"
ACS = "https://sp.test.local/api/saml/acs"
SSO = "https://idp.test.local/sso"
REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
AUTHN = {"class_ref": "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport",
         "authn_auth": IDP_EID}
IDENTITY = {"mail": ["victim@test.local"], "displayName": ["Victim User"],
            "eduPersonPrincipalName": ["victim@test.local"]}


def _gen_cert(tmp, name):
    key, crt = os.path.join(tmp, f"{name}.key"), os.path.join(tmp, f"{name}.crt")
    subprocess.run(
        ["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
         "-keyout", key, "-out", crt, "-days", "1", "-subj", f"/CN={name}"],
        check=True, capture_output=True)
    return key, crt


def _idp_conf(tmp, key, crt, metadata=None):
    c = {"entityid": IDP_EID,
         "service": {"idp": {"endpoints": {"single_sign_on_service": [(SSO, REDIRECT)]},
                             "name_id_format": [NAMEID_FORMAT_EMAILADDRESS]}},
         "key_file": key, "cert_file": crt, "xmlsec_binary": _XMLSEC}
    if metadata:
        c["metadata"] = {"local": metadata}
    return c


def _sp_conf(key, crt, metadata=None, *, want_signed=True):
    c = {"entityid": SP_EID,
         "service": {"sp": {"endpoints": {"assertion_consumer_service": [(ACS, BINDING_HTTP_POST)]},
                            "allow_unsolicited": True, "authn_requests_signed": False,
                            "want_assertions_signed": want_signed, "want_response_signed": False}},
         "key_file": key, "cert_file": crt, "xmlsec_binary": _XMLSEC}
    if metadata:
        c["metadata"] = {"local": metadata}
    return c


def _write_md(conf, path):
    with open(path, "w") as f:
        f.write(str(entity_descriptor(Config().load(conf))))
    return path


def _authn_response(server, name, in_response_to="id-1"):
    return str(server.create_authn_response(
        IDENTITY, in_response_to=in_response_to, destination=ACS, sp_entity_id=SP_EID,
        name_id=server.ident.transient_nameid(SP_EID, name), authn=AUTHN,
        sign_assertion=name != "unsigned", sign_response=False))


@pytest.fixture(scope="module")
def harness():
    """Build a trusted IdP, an attacker IdP, and strict/lax SPs once; mint the
    valid / tampered / forged / unsigned response payloads."""
    tmp = tempfile.mkdtemp(prefix="samlsig_")
    idp_key, idp_crt = _gen_cert(tmp, "idp")
    sp_key, sp_crt = _gen_cert(tmp, "sp")
    atk_key, atk_crt = _gen_cert(tmp, "attacker")

    idp_md = _write_md(_idp_conf(tmp, idp_key, idp_crt), os.path.join(tmp, "idp_md.xml"))
    sp_md = _write_md(_sp_conf(sp_key, sp_crt), os.path.join(tmp, "sp_md.xml"))

    idp = Server(config=Config().load(_idp_conf(tmp, idp_key, idp_crt, [sp_md])))
    atk = Server(config=Config().load(_idp_conf(tmp, atk_key, atk_crt, [sp_md])))
    strict_sp = Saml2Client(config=Config().load(_sp_conf(sp_key, sp_crt, [idp_md], want_signed=True)))
    # lax_sp is INTENTIONALLY insecure (want_assertions_signed=False) — it is the
    # negative control test_flag_is_load_bearing needs to prove the prod flag
    # (app/saml_client.py: want_assertions_signed=True, SEC-02) is what forces
    # rejection. pysaml2 rightly UserWarns "accepts unsigned ..." on this config;
    # it reflects only this deliberate fixture, not prod, so silence it here.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="The SAML service provider accepts unsigned")
        lax_sp = Saml2Client(config=Config().load(_sp_conf(sp_key, sp_crt, [idp_md], want_signed=False)))

    valid = _authn_response(idp, "victim")
    tampered = valid.replace("victim@test.local", "admin@test.local")
    assert tampered != valid
    forged = _authn_response(atk, "victim")
    unsigned = _authn_response(idp, "unsigned")
    # No InResponseTo at all -- a genuinely unsolicited (IdP-initiated)
    # response, same shape as a real IdP-initiated login. D9 #12 (mrj4001
    # review, PR #781 thread r3967362882): this harness DOES drive real
    # pysaml2 parsing (unlike every other SAML test, which mocks
    # parse_authn_request_response), so it's the right place for a
    # real-parser proof that allow_unsolicited=True actually works.
    unsolicited = _authn_response(idp, "victim", in_response_to=None)

    return {"strict": strict_sp, "lax": lax_sp,
            "valid": valid, "tampered": tampered, "forged": forged,
            "unsigned": unsigned, "unsolicited": unsolicited}


def _parse(sp, xml, outstanding=None):
    b64 = base64.b64encode(xml.encode()).decode()
    if outstanding is None:
        outstanding = {"id-1": "/"}
    return sp.parse_authn_request_response(b64, BINDING_HTTP_POST, outstanding=outstanding)


def test_valid_signature_is_accepted(harness):
    resp = _parse(harness["strict"], harness["valid"])
    assert resp is not None
    attrs = extract_user_attrs(resp.get_identity())
    assert attrs["email"] == "victim@test.local"


def test_tampered_assertion_is_rejected(harness):
    """Editing the assertion after signing must fail signature verification."""
    with pytest.raises(Exception) as exc:
        _parse(harness["strict"], harness["tampered"])
    assert "signature" in str(exc.value).lower()


def test_forged_signature_is_rejected(harness):
    """An assertion signed by a key not in the IdP metadata must be rejected."""
    with pytest.raises(Exception) as exc:
        _parse(harness["strict"], harness["forged"])
    assert "signature" in str(exc.value).lower()


def test_unsigned_assertion_is_rejected_when_signing_required(harness):
    with pytest.raises(Exception) as exc:
        _parse(harness["strict"], harness["unsigned"])
    assert "signature" in str(exc.value).lower()


def test_flag_is_load_bearing(harness):
    """Same unsigned assertion is accepted once want_assertions_signed=False --
    documents that the flag (app/saml_client.py) is what forces rejection."""
    resp = _parse(harness["lax"], harness["unsigned"])
    assert resp is not None  # bypass reappears if the flag is ever flipped off


def test_unsolicited_response_is_accepted(harness):
    """D9 #12 (mrj4001 review, PR #781 thread r3967362882): proves pysaml2
    itself accepts a genuinely unsolicited response (no InResponseTo, parsed
    against an empty outstanding-request map) when the SP is configured with
    allow_unsolicited=True -- via this file's OWN `_sp_conf` harness config,
    not the running app's. That IdP-initiated SSO keeps working end to end
    additionally depends on the app's own SP config actually setting
    allow_unsolicited=True, which is pinned separately in
    test_saml_sp.py::TestGetSamlClient::test_config_allows_unsolicited_responses.
    A validly signed, otherwise-unremarkable response with
    in_response_to=None must be accepted here, and assertion_ids() (the
    replay gate's own ID extraction) must find exactly the one real
    assertion ID pysaml2 parsed out of it."""
    resp = _parse(harness["strict"], harness["unsolicited"], outstanding={})
    assert resp is not None
    ids = assertion_ids(resp)
    assert len(ids) == 1
    assert ids[0]
