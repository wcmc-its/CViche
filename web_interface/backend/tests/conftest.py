import pytest
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import SystemConfig

# In-memory SQLite for tests
TEST_DATABASE_URL = "sqlite:///file::memory:?cache=shared"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
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


@pytest.fixture
def client(db):
    """Provide a FastAPI TestClient with overridden DB dependency."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def seed_simple_mode(db):
    """Seed SystemConfig for simple auth mode."""
    configs = {
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
    }
    for key, value in configs.items():
        db.add(SystemConfig(key=key, value=value))
    db.commit()


@pytest.fixture
def seed_saml_mode(db):
    """Seed SystemConfig for SAML auth mode."""
    configs = {
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
    }
    for key, value in configs.items():
        db.add(SystemConfig(key=key, value=value))
    db.commit()
