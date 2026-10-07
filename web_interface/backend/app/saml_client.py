"""pysaml2 SP client factory, certificate generation, and SAML attribute extraction."""
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config
from sqlalchemy.orm import Session

from app.config_loader import get_config_value

logger = logging.getLogger(__name__)

# SAML attribute OID constants
ATTR_MAIL = "urn:oid:0.9.2342.19200300.100.1.3"
ATTR_DISPLAY_NAME = "urn:oid:2.16.840.1.113730.3.1.241"
ATTR_EPPN = "urn:oid:1.3.6.1.4.1.5923.1.1.1.6"
ATTR_UID = "urn:oid:0.9.2342.19200300.100.1.1"


def _find_xmlsec1() -> str:
    """Locate the xmlsec1 binary on macOS (Apple Silicon / Intel) or Linux.

    Returns the absolute path to xmlsec1.
    Raises RuntimeError if not found.
    """
    candidates = [
        "/opt/homebrew/bin/xmlsec1",   # Apple Silicon macOS
        "/usr/local/bin/xmlsec1",      # Intel macOS
        "/usr/bin/xmlsec1",            # Linux
    ]
    for path in candidates:
        # A candidate that exists but isn't executable (e.g. a partial
        # package install) must fall through to the PATH lookup below, not
        # be handed to pysaml2/xmlsec as if it were runnable.
        if Path(path).exists() and os.access(path, os.X_OK):
            return path

    # Fall back to PATH lookup
    found = shutil.which("xmlsec1")
    if found:
        return found

    raise RuntimeError(
        "xmlsec1 binary not found. Install with: "
        "brew install libxmlsec1 (macOS) or apt-get install xmlsec1 (Ubuntu/Debian)"
    )


def _generate_self_signed_cert(cert_dir: Path) -> None:
    """Generate a self-signed RSA 2048-bit SP certificate and key.

    Creates sp.crt and sp.key PEM files in cert_dir.
    """
    cert_dir.mkdir(parents=True, exist_ok=True)

    # Generate RSA 2048-bit private key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build X.509 certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "CViche SAML SP"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )

    # Write PEM files
    key_path = cert_dir / "sp.key"
    cert_path = cert_dir / "sp.crt"

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)  # unencrypted SP private key -- owner-only
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    logger.info("Generated self-signed SP certificate in %s", cert_dir)


def get_saml_client(db: Session) -> Saml2Client:
    """Build and return a configured Saml2Client from SystemConfig DB values.

    Auto-generates a self-signed SP certificate if one does not already exist.
    """
    entity_id = get_config_value(db, "saml_entity_id") or ""
    idp_metadata_url = get_config_value(db, "saml_idp_metadata_url") or ""
    sp_base_url = get_config_value(db, "saml_sp_base_url") or ""
    cert_dir_str = get_config_value(db, "saml_cert_dir")

    # The SP endpoints (ACS, SLO) live at the SP's reachable base URL, not at
    # the entity_id. The two often differ -- e.g., entity_id is
    # "https://host/shibboleth" by Shibboleth convention while the SP serves
    # at "https://host". The IdP enforces the Destination/Recipient against
    # the URLs published in our metadata, so they must match the real
    # endpoints exactly.
    if not sp_base_url:
        raise RuntimeError(
            "saml_sp_base_url is not configured. Set the SP's reachable base "
            "URL (e.g. 'https://cviche.weill.cornell.edu') in auth_config.yaml "
            "under saml.sp_base_url. This must match where the SP actually "
            "serves /api/saml/acs, not the entity_id."
        )

    # Default cert directory: <backend>/certs
    if not cert_dir_str:
        cert_path = Path(__file__).parent.parent / "certs"
    else:
        cert_path = Path(cert_dir_str)

    # Auto-generate a self-signed cert only when neither file exists. If exactly
    # one is present, fail loudly -- sp.crt is the cert filed with the IdP, and
    # silently regenerating it would break SSO instead of surfacing the problem.
    crt, key = cert_path / "sp.crt", cert_path / "sp.key"
    if not crt.exists() and not key.exists():
        _generate_self_signed_cert(cert_path)
    elif not (crt.exists() and key.exists()):
        missing = "sp.key" if crt.exists() else "sp.crt"
        raise RuntimeError(
            f"SP cert dir {cert_path} is half-populated: {missing} is missing. "
            "Restore it, or delete both files to regenerate a self-signed pair."
        )

    saml_config = {
        "entityid": entity_id,
        "service": {
            "sp": {
                "name": "CViche",
                "endpoints": {
                    "assertion_consumer_service": [
                        (f"{sp_base_url}/api/saml/acs", BINDING_HTTP_POST),
                    ],
                    "single_logout_service": [
                        (f"{sp_base_url}/api/saml/logout", BINDING_HTTP_REDIRECT),
                    ],
                },
                "allow_unsolicited": True,
                "authn_requests_signed": False,
                "want_assertions_signed": True,  # SEC-02: reject unsigned SAML assertions
            }
        },
        "metadata": {
            "remote": [{"url": idp_metadata_url}]
        },
        "key_file": str(cert_path / "sp.key"),
        "cert_file": str(cert_path / "sp.crt"),
        "xmlsec_binary": _find_xmlsec1(),
    }

    conf = Saml2Config()
    conf.load(saml_config)
    return Saml2Client(config=conf)


# Attribute keys we consume; anything else the IdP releases is not stored.
_MAPPED_ATTR_KEYS = frozenset({
    ATTR_MAIL, "mail",
    ATTR_DISPLAY_NAME, "displayName",
    ATTR_EPPN, "eduPersonPrincipalName",
    ATTR_UID, "uid",
})


# ePPN scopes under which WCM issues CWIDs: a `uid` or ePPN local part in
# these is the CWID. Any other scope belongs to a partner institution whose
# local parts share the CWID namespace's shape (a Cornell Ithaca NetID looks
# like a CWID), so a partner user is keyed on the full scoped ePPN instead
# (#1452). The SP's one IdP is the WCM login proxy, which brokers the partner
# IdPs and declares only med.cornell.edu in its metadata, so the scope can't
# be checked against metadata here -- the proxy is the trust boundary.
CWID_SCOPES = frozenset({"med.cornell.edu"})


def _identity_key(uid: str | None, eppn: str | None) -> str | None:
    """Derive the user's identity anchor from released attributes.

    - ePPN outside CWID_SCOPES: the full lowercased ePPN. It contains "@" and
      a CWID never does, so a partner user can't land on a WCM account.
      `uid` is ignored -- a partner's uid (e.g. an AD sAMAccountName) is not
      a CWID.
    - Otherwise: the CWID -- `uid` if released, else the ePPN local part.
      Unchanged from before #1452.

    Returns None if nothing is usable -- including an ePPN with an empty local
    part, which must yield None, not "" (mrj4001 review, PR #781 thread
    r3967362882 #21).
    """
    local, at, scope = (eppn or "").strip().lower().rpartition("@")
    if at and scope and scope not in CWID_SCOPES:
        return f"{local}@{scope}" if local else None
    if uid and uid.strip():
        return uid.strip().lower()
    return local or None


def extract_user_attrs(identity: dict) -> dict:
    """Extract user attributes from a SAML assertion identity dict.

    Handles both OID-keyed and friendly-name-keyed attributes.
    Returns dict with keys: cwid, email, display_name, eppn.

    The anchor is a CWID, or a partner user's scoped ePPN (see _identity_key,
    #1452); it is returned under "cwid" either way. Email is
    preferred but optional -- nothing in the app sends mail, so a user with no
    ED `mail` (e.g. external affiliates) still authenticates. Raises ValueError
    only when no anchor is derivable.

    Only uid/mail/displayName/eppn are consumed; any other attribute the IdP
    releases is not stored. Log the dropped attribute *names* (not values --
    they may be PII) so the loss is visible instead of silent.
    """

    def _get(oid: str, friendly: str) -> str | None:
        """Check both OID key and friendly name key; return first value or None."""
        for key in (oid, friendly):
            vals = identity.get(key)
            if vals:
                return vals[0] if isinstance(vals, list) else vals
        return None

    unmapped = sorted(k for k in identity if k not in _MAPPED_ATTR_KEYS)
    if unmapped:
        logger.info(
            "SAML: %d unmapped attribute(s) released by IdP, not stored: %s",
            len(unmapped), unmapped,
        )

    uid = _get(ATTR_UID, "uid")
    eppn = _get(ATTR_EPPN, "eduPersonPrincipalName")
    mail = _get(ATTR_MAIL, "mail")
    display_name = _get(ATTR_DISPLAY_NAME, "displayName")

    cwid = _identity_key(uid, eppn)
    if cwid is None:
        raise ValueError(
            "No CWID derivable from SAML assertion (need uid or eduPersonPrincipalName)"
        )

    return {
        "cwid": cwid,
        "email": (mail.strip().lower() or None) if mail else None,
        "display_name": (display_name or eppn or mail or cwid).strip(),
        "eppn": eppn,
    }
