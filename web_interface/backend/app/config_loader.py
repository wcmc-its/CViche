"""Load auth config from YAML and seed SystemConfig DB table."""
import json
import logging
import os
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.models import SystemConfig

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "auth_config.yaml"
EXAMPLE_CONFIG_PATH = Path(__file__).parent.parent / "auth_config.yaml.example"

def load_yaml_config() -> dict:
    """Load auth_config.yaml, falling back to the .example template if absent.

    auth_config.yaml is environment-specific and gitignored. In a deployment
    that has not provisioned it, fall back to the tracked .example so the app
    still boots (in a locked-down state) instead of crashing at startup.
    """
    path = CONFIG_PATH
    if not path.exists():
        logger.warning(
            "auth_config.yaml not found at %s; falling back to %s. "
            "Provide a real auth_config.yaml for production.",
            CONFIG_PATH, EXAMPLE_CONFIG_PATH.name,
        )
        path = EXAMPLE_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# Keys whose source of truth is the runtime admin UI (PUT /api/admin/config).
# These are seeded once from YAML and then owned by admins -- a redeploy must
# NOT clobber admin edits, so they are insert-if-absent only.
ADMIN_MANAGED_KEYS = frozenset({
    "allowed_users",
    "admin_users",
    "rate_limit_daily",
    "rate_limit_monthly",
    "consent_version",
    # Global session epoch (revocation counter). Owned at runtime (bumped by the
    # admin "sign out everyone" endpoint), so it must be insert-if-absent only --
    # a per-boot reconcile to the YAML/default would reset it to 0 and silently
    # un-revoke every session on the next deploy.
    "session_epoch",
})

# All other keys (auth_mode, saml_*, ed_*) have no admin-UI path, so the YAML
# (configmap) is their only source of truth. They are reconciled from YAML on
# every boot -- otherwise a configmap edit can never override an existing row
# (the original insert-if-absent bug: flipping auth.mode to "saml" had no
# effect because the row already existed as "simple").


def seed_system_config(db: Session) -> None:
    """Seed/reconcile SystemConfig from YAML.

    Admin-managed keys (see ADMIN_MANAGED_KEYS) are inserted only when absent so
    runtime admin edits survive redeploys. All file-managed keys are reconciled
    to match the YAML on every boot so configmap changes actually take effect.
    """
    config = load_yaml_config()
    defaults = {
        "auth_mode": json.dumps(config.get("auth", {}).get("mode", "simple")),
        "allowed_users": json.dumps(config.get("allowed_users", [])),
        "admin_users": json.dumps(config.get("admin_users", [])),
        "rate_limit_daily": json.dumps(config.get("rate_limits", {}).get("daily", 10)),
        "rate_limit_monthly": json.dumps(config.get("rate_limits", {}).get("monthly", 50)),
        "consent_version": json.dumps(config.get("consent", {}).get("version", "1.0")),
        # Global session epoch; see ADMIN_MANAGED_KEYS. Seeded once at 0; bumped
        # only by the admin revoke-all endpoint.
        "session_epoch": json.dumps(0),
        # SAML config
        "saml_entity_id": json.dumps(config.get("saml", {}).get("entity_id", "")),
        "saml_idp_metadata_url": json.dumps(config.get("saml", {}).get("idp_metadata_url", "")),
        "saml_sp_base_url": json.dumps(config.get("saml", {}).get("sp_base_url", "")),
        "saml_discovery_url": json.dumps(config.get("saml", {}).get("discovery_url", "")),
        "saml_cert_dir": json.dumps(config.get("saml", {}).get("cert_dir", "")),
        # ED group authorization config
        "ed_enabled": json.dumps(config.get("ed", {}).get("enabled", False)),
        "ed_access_group": json.dumps(config.get("ed", {}).get("access_group", "")),
        "ed_admin_group": json.dumps(config.get("ed", {}).get("admin_group", "")),
        # Empty/unset = nobody is staff (read-only elevated role, UserRole.STAFF).
        "ed_staff_group": json.dumps(config.get("ed", {}).get("staff_group", "")),
        # Comma-separated partner ePPN scopes whose users get the user role
        # without a WCM ED entry (#1453). Empty/unset = no partner is admitted.
        "ed_partner_scopes": json.dumps(config.get("ed", {}).get("partner_scopes", "")),
        "ed_contact_name": json.dumps(config.get("ed", {}).get("contact_name", "")),
        "ed_contact_email": json.dumps(config.get("ed", {}).get("contact_email", "")),
    }
    for key, value in defaults.items():
        existing = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if existing is None:
            db.add(SystemConfig(key=key, value=value))
        elif key not in ADMIN_MANAGED_KEYS and existing.value != value:
            # File-managed key drifted from the YAML -- reconcile it.
            logger.info(
                "Reconciling config '%s' from YAML: %s -> %s",
                key, existing.value, value,
            )
            existing.value = value
    db.commit()

def get_config_value(db: Session, key: str):
    """Get a config value from DB, falling back to None."""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        return json.loads(row.value)
    return None

# Baked into the image by the Dockerfile's IMAGE_TAG build arg (#1239).
IMAGE_TAG_ENV = "CVICHE_IMAGE_TAG"


def current_image_tag() -> str | None:
    """The tag of the image this process runs, or None when it was built
    without one (local dev, or an image built before #1239)."""
    return os.environ.get(IMAGE_TAG_ENV, "").strip() or None


def get_config(section, key, default=None):
    # 1. env 
    value = os.environ.get(key)
    if value:
        return value,"env"

    # 2. yaml (ConfigMap)
    try:
        cfg = load_yaml_config() or {}
        value = (cfg.get(section) or {}).get(key)
        if value:
            return value, "yaml"
    except Exception:
        logger.warning("Failed to load auth_config.yaml; returning default value",exc_info=True)

    # 3. default
    return default ,"default"


EMAIL_INTAKE_FLAG = "CVICHE_EMAIL_INTAKE"
_FLAG_ON = frozenset({"1", "true", "yes", "on"})
# The SES receiving address for emailed CVs (#1298).
INTAKE_ADDRESS = "cv@scholars-mail.weill.cornell.edu"


def email_intake_enabled() -> bool:
    """CVICHE_EMAIL_INTAKE is on: the poller runs and the UI shows the intake address."""
    flag, _ = get_config("mail", EMAIL_INTAKE_FLAG, default="")
    return str(flag).strip().lower() in _FLAG_ON
