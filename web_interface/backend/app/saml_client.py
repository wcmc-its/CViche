"""pysaml2 SP client factory, certificate generation, and SAML attribute extraction."""
import logging
import platform
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone

from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT
from saml2.config import Config as Saml2Config
from saml2.client import Saml2Client
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.orm import Session

from app.config_loader import get_config_value

logger = logging.getLogger(__name__)

# SAML attribute OID constants
ATTR_MAIL = "urn:oid:0.9.2342.19200300.100.1.3"
ATTR_DISPLAY_NAME = "urn:oid:2.16.840.1.113730.3.1.241"
ATTR_EPPN = "urn:oid:1.3.6.1.4.1.5923.1.1.1.6"


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
        if Path(path).exists():
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
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    logger.info("Generated self-signed SP certificate in %s", cert_dir)


def get_saml_client(db: Session) -> Saml2Client:
    """Build and return a configured Saml2Client from SystemConfig DB values.

    Auto-generates a self-signed SP certificate if one does not already exist.
    """
    entity_id = get_config_value(db, "saml_entity_id") or ""
    idp_metadata_url = get_config_value(db, "saml_idp_metadata_url") or ""
    cert_dir_str = get_config_value(db, "saml_cert_dir")

    # Default cert directory: <backend>/certs
    if not cert_dir_str:
        cert_path = Path(__file__).parent.parent / "certs"
    else:
        cert_path = Path(cert_dir_str)

    # Auto-generate self-signed cert on first use
    if not (cert_path / "sp.crt").exists():
        _generate_self_signed_cert(cert_path)

    saml_config = {
        "entityid": entity_id,
        "service": {
            "sp": {
                "name": "CViche",
                "endpoints": {
                    "assertion_consumer_service": [
                        (f"{entity_id}/api/saml/acs", BINDING_HTTP_POST),
                    ],
                    "single_logout_service": [
                        (f"{entity_id}/api/saml/logout", BINDING_HTTP_REDIRECT),
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


def extract_user_attrs(identity: dict) -> dict:
    """Extract user attributes from a SAML assertion identity dict.

    Handles both OID-keyed and friendly-name-keyed attributes.
    Returns dict with keys: email, display_name, eppn.
    Raises ValueError if required 'mail' attribute is missing.
    """

    def _get(oid: str, friendly: str) -> str | None:
        """Check both OID key and friendly name key; return first value or None."""
        for key in (oid, friendly):
            vals = identity.get(key)
            if vals:
                return vals[0] if isinstance(vals, list) else vals
        return None

    mail = _get(ATTR_MAIL, "mail")
    if mail is None:
        raise ValueError("Required attribute 'mail' missing from SAML assertion")

    display_name = _get(ATTR_DISPLAY_NAME, "displayName")
    eppn = _get(ATTR_EPPN, "eduPersonPrincipalName")

    return {
        "email": mail.strip().lower(),
        "display_name": (display_name or eppn or mail).strip(),
        "eppn": eppn,
    }
