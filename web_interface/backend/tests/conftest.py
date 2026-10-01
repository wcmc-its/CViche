import os
import tempfile

# Must run before any module that imports unified_pipeline.core.prompt_logger
# (directly or via app.pipeline.orchestrator / app.main): that module reads
# PROMPT_LOG_DIR from the environment and mkdir's it at IMPORT time, defaulting
# to the checkout's src/unified_pipeline/prompt_logs -- the PII transcript
# store (#776). A session-scoped tempdir here, set before the first import,
# redirects every test's LLM-stub log writes away from that directory.
_PROMPT_LOG_TMPDIR = tempfile.TemporaryDirectory(prefix="cviche-test-prompt-logs-")
os.environ["PROMPT_LOG_DIR"] = _PROMPT_LOG_TMPDIR.name

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

import pytest
import json
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.models import SystemConfig

# The real prompt_logs dir: prompt_logger's _DEFAULT_PROMPT_LOG_DIR, which
# can't be imported here because unified_pipeline isn't on sys.path yet.
_REAL_PROMPT_LOGS_DIR = Path(__file__).parents[3] / "src" / "unified_pipeline" / "prompt_logs"

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


@pytest.fixture(autouse=True)
def _stage_errors_off_the_real_outputs_dir(monkeypatch, tmp_path_factory):
    """execute_step writes a stage-error record (#745) under the orchestrator's
    pipeline_output_dir, which defaults to the checkout's
    src/unified_pipeline/outputs -- the local corpus farm (PII, unversioned).
    A test that fails a stage without pointing pipeline_output_dir at its own
    tmp dir would plant a record there that caps a real run's score. Only that
    default root is redirected; a test that sets its own dir keeps it."""
    from app.pipeline import orchestrator

    real_root = (orchestrator.PARENT_DIR / "src" / "unified_pipeline" / "outputs").resolve()
    sandbox = tmp_path_factory.mktemp("stage_errors_root")
    original = orchestrator.stage_errors_path

    def _redirected(outputs_root, document_uid):
        root = sandbox if Path(outputs_root).resolve() == real_root else outputs_root
        return original(root, document_uid)

    monkeypatch.setattr(orchestrator, "stage_errors_path", _redirected)


@pytest.fixture(autouse=True)
def _no_teams_webhook_leak(monkeypatch):
    """Tests that exercise execute()/submit_feedback for real must not fire
    live Teams posts just because a developer has CVICHE_TEAMS_WEBHOOK_URL
    exported in their shell (config precedence puts the env var first). A test
    that wants to assert on an actual POST sets it back with monkeypatch.setenv.
    """
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)


@pytest.fixture(autouse=True)
def _no_live_ed_department_lookup(monkeypatch):
    """The SAML ACS now reads the user's department from ED after the
    membership check. Tests that enable ED and patch only check_ed_membership
    must not fall through to a real LDAP bind; a test that asserts on the
    department patches this name itself."""
    monkeypatch.setattr("app.api.saml_routes.fetch_ed_department", lambda cwid, cfg: None)


@pytest.fixture(autouse=True)
def _reset_estimate_rate_limiter():
    """/estimate's per-pod, in-memory, per-user counter (#795) is a process
    global, unlike the per-test in-memory DB above -- without this, one
    test's /api/estimate calls count against the next test's budget for the
    same (test-fixture) user id.
    """
    from app.api.upload import _estimate_rate_limiter
    _estimate_rate_limiter.reset()


@pytest.fixture(autouse=True)
def _reset_pod_admission_state():
    """Every client fixture's teardown runs lifespan shutdown, which puts the
    pod into one-way draining and stops any run still holding a slot (#116);
    undo that, and any leaked slot, so the next test can start runs."""
    from app.pipeline import concurrency, orchestrator
    yield
    concurrency._draining = False
    concurrency._active_run_ids.clear()
    orchestrator._cancelled_runs.clear()


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
    # A test that leaves a run slot held would otherwise make the teardown's
    # lifespan shutdown wait out the full production drain budget (#116).
    with patch("app.database.SessionLocal", TestingSessionLocal), \
         patch("app.database.engine", engine), \
         patch.dict(os.environ, {"CVICHE_SHUTDOWN_DRAIN_SECONDS": "0"}):
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
        "saml_sp_base_url": json.dumps(""),
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
        "saml_sp_base_url": json.dumps("https://cviche.med.cornell.edu"),
        "saml_discovery_url": json.dumps("https://login.weill.cornell.edu/discovery"),
        "saml_cert_dir": json.dumps("/etc/cviche/certs"),
    })


# --- SAML test fixtures ---

@pytest.fixture
def seed_ed_enabled(db):
    """Seed SystemConfig with ED group authorization enabled."""
    _upsert_config(db, {
        "auth_mode": json.dumps("saml"),
        "allowed_users": json.dumps([]),
        "admin_users": json.dumps([]),
        "rate_limit_daily": json.dumps(10),
        "rate_limit_monthly": json.dumps(50),
        "consent_version": json.dumps("1.0"),
        "saml_entity_id": json.dumps("https://cviche.med.cornell.edu/shibboleth"),
        "saml_idp_metadata_url": json.dumps("https://shibboleth.weill.cornell.edu/idp/metadata"),
        "saml_sp_base_url": json.dumps("https://cviche.med.cornell.edu"),
        "saml_discovery_url": json.dumps("https://login.weill.cornell.edu/discovery"),
        "saml_cert_dir": json.dumps("/etc/cviche/certs"),
        "ed_enabled": json.dumps(True),
        "ed_access_group": json.dumps("cn=ITS:Library:CViche/user-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"),
        "ed_admin_group": json.dumps("cn=ITS:Library:CViche/admin-role,ou=application security,ou=groups,dc=weill,dc=cornell,dc=edu"),
        "ed_contact_name": json.dumps("Paul Albert"),
        "ed_contact_email": json.dumps("paa2013@med.cornell.edu"),
    })


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


def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring live AWS Bedrock credentials (deselected by default)")


def _real_prompt_logs_listing() -> set[str]:
    """Relative paths under the real prompt_logs dir, or an empty set if it
    doesn't exist. Never lists file contents -- those are PII transcripts."""
    if not _REAL_PROMPT_LOGS_DIR.is_dir():
        return set()
    return {
        str(p.relative_to(_REAL_PROMPT_LOGS_DIR))
        for p in _REAL_PROMPT_LOGS_DIR.rglob("*")
    }


@pytest.fixture(scope="session", autouse=True)
def _guard_real_prompt_logs_untouched():
    """Fail the session if the real (PII) prompt_logs directory changed.

    A regression guard for #776: some tests drive LLM-stub calls through
    prompt_logger, which writes wherever PROMPT_LOG_DIR points. This session
    already redirected PROMPT_LOG_DIR to a tempdir before any pipeline module
    was imported, so under a passing suite this snapshot never changes; if a
    future test (or a change to prompt_logger's default) writes into the
    real directory anyway, this fails loudly instead of silently growing the
    checkout's transcript store. It also fails on any file disappearing --
    a test teardown once rmtree'd a real prompt_logs directory outright, and
    that is the worse PII failure of the two.
    """
    before = _real_prompt_logs_listing()
    yield
    after = _real_prompt_logs_listing()
    added = after - before
    removed = before - after
    assert not added, (
        f"backend test suite wrote {len(added)} file(s) into the real "
        f"{_REAL_PROMPT_LOGS_DIR} (the PII transcript store) instead of the "
        f"session's PROMPT_LOG_DIR tempdir: {sorted(added)[:10]}"
    )
    assert not removed, (
        f"backend test suite deleted {len(removed)} file(s) from the real "
        f"{_REAL_PROMPT_LOGS_DIR} (the PII transcript store) -- a broader "
        f"cleanup than the one seeded file a test intended to remove: "
        f"{sorted(removed)[:10]}"
    )


@pytest.fixture
def cv_pdf():
    """Synthetic PDF bytes from the pipeline suite's builder (#806): no PDF
    writer is a dependency, so fixtures are hand-written PDF bytes.
    ``cv_pdf(image_pages=(1,), user_password="pw")`` adds an image-only
    second page / encrypts it; the first page always carries well over
    upload.py's MIN_EXTRACTED_CHARS of text."""
    from unified_pipeline.tests.test_pdf_to_docx import _make_pdf

    text_page = [
        (i == 0, 12 if i == 0 else 10, 72, 720 - 16 * i,
         "EDUCATION" if i == 0 else f"Entry {i}: Doctor of Medicine, Example University, New York, 2019")
        for i in range(12)
    ]

    def build(image_pages=(), user_password=None) -> bytes:
        pages = [text_page] + ([[]] if image_pages else [])
        return _make_pdf(pages, image_pages=image_pages, user_password=user_password)

    return build
