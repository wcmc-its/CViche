"""Load auth config from YAML and seed SystemConfig DB table."""
import json
import yaml
from pathlib import Path
from sqlalchemy.orm import Session
from app.models import SystemConfig

CONFIG_PATH = Path(__file__).parent.parent / "auth_config.yaml"

def load_yaml_config() -> dict:
    """Load auth_config.yaml from disk."""
    with open(CONFIG_PATH, "r") as f:
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
