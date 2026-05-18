"""Load auth config from YAML and seed SystemConfig DB table."""
import json
import logging
import yaml
from pathlib import Path
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
    with open(path, "r") as f:
        return yaml.safe_load(f)

def seed_system_config(db: Session) -> None:
    """Seed SystemConfig table from YAML. Only inserts keys absent from DB (forward-compatible)."""
    config = load_yaml_config()
    defaults = {
        "auth_mode": json.dumps(config.get("auth", {}).get("mode", "simple")),
        "allowed_users": json.dumps(config.get("allowed_users", [])),
        "admin_users": json.dumps(config.get("admin_users", [])),
        "rate_limit_daily": json.dumps(config.get("rate_limits", {}).get("daily", 10)),
        "rate_limit_monthly": json.dumps(config.get("rate_limits", {}).get("monthly", 50)),
        "consent_version": json.dumps(config.get("consent", {}).get("version", "1.0")),
        # SAML config (Phase 8 will consume these; stored now for forward-compatibility)
        "saml_entity_id": json.dumps(config.get("saml", {}).get("entity_id", "")),
        "saml_idp_metadata_url": json.dumps(config.get("saml", {}).get("idp_metadata_url", "")),
        "saml_discovery_url": json.dumps(config.get("saml", {}).get("discovery_url", "")),
        "saml_cert_dir": json.dumps(config.get("saml", {}).get("cert_dir", "")),
        # ED group authorization config (Phase 9)
        "ed_enabled": json.dumps(config.get("ed", {}).get("enabled", False)),
        "ed_access_group": json.dumps(config.get("ed", {}).get("access_group", "")),
        "ed_admin_group": json.dumps(config.get("ed", {}).get("admin_group", "")),
        "ed_contact_name": json.dumps(config.get("ed", {}).get("contact_name", "")),
        "ed_contact_email": json.dumps(config.get("ed", {}).get("contact_email", "")),
    }
    for key, value in defaults.items():
        existing = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if not existing:
            db.add(SystemConfig(key=key, value=value))
    db.commit()

def get_config_value(db: Session, key: str):
    """Get a config value from DB, falling back to None."""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        return json.loads(row.value)
    return None
