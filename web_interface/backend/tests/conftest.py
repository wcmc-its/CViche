import pytest
import json
from unittest.mock import patch
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.models import SystemConfig

# In-memory SQLite for tests -- StaticPool ensures all connections share one DB
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_database():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """Provide a test database session."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _upsert_config(db, configs: dict):
    """Insert or update SystemConfig rows (handles lifespan pre-seeding)."""
    for key, value in configs.items():
        existing = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if existing:
            existing.value = value
        else:
            db.add(SystemConfig(key=key, value=value))
    db.commit()


@pytest.fixture
def client(db):
    """Provide a FastAPI TestClient with overridden DB dependency.

    Patches the database module so that the app lifespan (which creates
    its own SessionLocal) also talks to the same in-memory test DB.
    """
    from app.main import app

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # Patch the SessionLocal used by the lifespan so seed_system_config
    # and check_consent_integrity talk to the test DB, not the real one.
    with patch("app.database.SessionLocal", TestingSessionLocal), \
         patch("app.database.engine", engine):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.clear()


@pytest.fixture
def seed_simple_mode(db):
    """Seed SystemConfig for simple auth mode."""
    _upsert_config(db, {
        "auth_mode": json.dumps("simple"),
        "allowed_users": json.dumps(["test@example.com"]),
        "admin_users": json.dumps(["admin@example.com"]),
        "rate_limit_daily": json.dumps(10),
        "rate_limit_monthly": json.dumps(50),
        "consent_version": json.dumps("1.0"),
        "saml_entity_id": json.dumps(""),
        "saml_idp_metadata_url": json.dumps(""),
        "saml_discovery_url": json.dumps(""),
        "saml_cert_dir": json.dumps(""),
    })


@pytest.fixture
def seed_saml_mode(db):
    """Seed SystemConfig for SAML auth mode."""
    _upsert_config(db, {
        "auth_mode": json.dumps("saml"),
        "allowed_users": json.dumps(["test@example.com"]),
        "admin_users": json.dumps([]),
        "rate_limit_daily": json.dumps(10),
        "rate_limit_monthly": json.dumps(50),
        "consent_version": json.dumps("1.0"),
        "saml_entity_id": json.dumps("https://cviche.med.cornell.edu/shibboleth"),
        "saml_idp_metadata_url": json.dumps("https://shibboleth.weill.cornell.edu/idp/metadata"),
        "saml_discovery_url": json.dumps("https://login.weill.cornell.edu/discovery"),
        "saml_cert_dir": json.dumps("/etc/cviche/certs"),
    })


# --- SAML test fixtures ---

@pytest.fixture
def mock_saml_identity():
    """Mock SAML assertion identity dict with OID-keyed attributes."""
    return {
        "urn:oid:0.9.2342.19200300.100.1.3": ["testuser@med.cornell.edu"],
        "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
        "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["testuser@cornell.edu"],
    }


@pytest.fixture
def mock_saml_identity_friendly():
    """Mock SAML assertion identity dict with friendly-name keys."""
    return {
        "mail": ["testuser@med.cornell.edu"],
        "displayName": ["Test User"],
        "eduPersonPrincipalName": ["testuser@cornell.edu"],
    }


@pytest.fixture
def mock_saml_identity_minimal():
    """Mock SAML assertion with only mail (minimum required)."""
    return {
        "urn:oid:0.9.2342.19200300.100.1.3": ["testuser@med.cornell.edu"],
    }


@pytest.fixture
def mock_saml_identity_no_mail():
    """Mock SAML assertion missing required mail attribute."""
    return {
        "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
        "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["testuser@cornell.edu"],
    }
