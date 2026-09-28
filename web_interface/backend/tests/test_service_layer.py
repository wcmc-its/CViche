"""Tests for the service layer (ARCH-01 through ARCH-06 regression)."""
import os
import sys
from pathlib import Path
os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")
# get_estimated_run_cost imports unified_pipeline lazily and silently falls back
# to a flat per-token rate when it can't; put src/ on the path so the cost tests
# exercise the real model whether this file runs alone or in the full suite.
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

import pytest
from fastapi import HTTPException

from app.services.run_service import check_run_access
from app.services.user_service import provision_user
from app.services.config_service import (
    SESSION_TTL, LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW,
    MAX_UPLOAD_SIZE, TIME_PER_1K_TOKENS, BASE_OVERHEAD_SECONDS,
    get_cost_per_1k_tokens, get_estimated_run_cost,
)
from app.errors import not_found, bad_request, forbidden, validation_error
from app.models import Run, User


class TestCheckRunAccess:
    """ARCH-02: check_run_access defined once and working correctly."""

    def test_returns_run_when_owner(self, db):
        user = User(email="owner@example.com", display_name="Owner", role="user", auth_method="simple")
        db.add(user)
        db.commit()
        db.refresh(user)
        run = Run(id="ABC123", filename="cv.docx", file_type="docx", status="created", user_id=user.id)
        db.add(run)
        db.commit()

        result = check_run_access("ABC123", user, db)
        assert result.id == "ABC123"

    def test_returns_run_when_admin(self, db):
        owner = User(email="owner@example.com", display_name="Owner", role="user", auth_method="simple")
        admin = User(email="admin@example.com", display_name="Admin", role="admin", auth_method="simple")
        db.add_all([owner, admin])
        db.commit()
        db.refresh(owner)
        db.refresh(admin)
        run = Run(id="ABC123", filename="cv.docx", file_type="docx", status="created", user_id=owner.id)
        db.add(run)
        db.commit()

        result = check_run_access("ABC123", admin, db)
        assert result.id == "ABC123"

    def test_raises_404_when_not_found(self, db):
        user = User(email="user@example.com", display_name="User", role="user", auth_method="simple")
        db.add(user)
        db.commit()
        db.refresh(user)

        with pytest.raises(HTTPException) as exc_info:
            check_run_access("NONEXISTENT", user, db)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "not_found"

    def test_raises_403_when_not_owner(self, db):
        owner = User(email="owner@example.com", display_name="Owner", role="user", auth_method="simple")
        other = User(email="other@example.com", display_name="Other", role="user", auth_method="simple")
        db.add_all([owner, other])
        db.commit()
        db.refresh(owner)
        db.refresh(other)
        run = Run(id="ABC123", filename="cv.docx", file_type="docx", status="created", user_id=owner.id)
        db.add(run)
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            check_run_access("ABC123", other, db)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "forbidden"

    def test_denies_unowned_run_to_non_admin(self, db):
        """Runs with user_id=None fail closed: a non-admin is denied (issue #112)."""
        user = User(email="user@example.com", display_name="User", role="user", auth_method="simple")
        db.add(user)
        db.commit()
        db.refresh(user)
        run = Run(id="ABC123", filename="cv.docx", file_type="docx", status="created", user_id=None)
        db.add(run)
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            check_run_access("ABC123", user, db)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "forbidden"

    def test_allows_unowned_run_to_admin(self, db):
        """Admins may still access an unowned run."""
        admin = User(email="admin@example.com", display_name="Admin", role="admin", auth_method="simple")
        db.add(admin)
        db.commit()
        db.refresh(admin)
        run = Run(id="ABC123", filename="cv.docx", file_type="docx", status="created", user_id=None)
        db.add(run)
        db.commit()

        result = check_run_access("ABC123", admin, db)
        assert result.id == "ABC123"


class TestProvisionUser:
    """ARCH-03: provision_user handles both create and update."""

    def test_creates_new_user(self, db):
        user = provision_user(db, display_name="New User", auth_method="simple",
                              email="new@example.com", role="user")
        assert user.email == "new@example.com"
        assert user.display_name == "New User"
        assert user.auth_method == "simple"
        assert user.role == "user"
        assert user.id is not None

    def test_updates_existing_user(self, db):
        # Create initial user (simple auth -- email anchor)
        provision_user(db, display_name="Old Name", auth_method="simple",
                       email="existing@example.com", role="user")
        # Update by the same email
        user = provision_user(db, display_name="New Name", auth_method="simple",
                              email="existing@example.com", role="admin")
        assert user.display_name == "New Name"
        assert user.role == "admin"

    def test_preserves_role_when_none(self, db):
        """When role=None, existing user's role is preserved (SAML without ED)."""
        provision_user(db, display_name="Keep Role", auth_method="saml",
                       cwid="keep0001", role="admin")
        user = provision_user(db, display_name="Keep Role Updated", auth_method="saml",
                              cwid="keep0001", role=None)
        assert user.role == "admin"  # Preserved, not overwritten

    def test_new_user_defaults_to_user_role(self, db):
        """When role=None for a new user, defaults to 'user'."""
        user = provision_user(db, display_name="Default Role", auth_method="saml",
                              cwid="dfl0001", role=None)
        assert user.role == "user"

    def test_simple_auth_with_admin_role(self, db):
        user = provision_user(db, display_name="Admin", auth_method="simple",
                              email="admin@example.com", role="admin")
        assert user.role == "admin"
        assert user.auth_method == "simple"

    # --- CWID anchoring (#326) ---

    def test_saml_user_anchored_on_cwid(self, db):
        user = provision_user(db, display_name="SAML User", auth_method="saml",
                              cwid="abc1234", email="abc1234@med.cornell.edu", role="admin")
        assert user.cwid == "abc1234"
        assert user.role == "admin"
        assert user.auth_method == "saml"

    def test_saml_user_without_email(self, db):
        """Affiliate with no ED mail -- provisioned by cwid, email stays NULL."""
        user = provision_user(db, display_name="No Mail", auth_method="saml",
                              cwid="nom1001", email=None)
        assert user.cwid == "nom1001"
        assert user.email is None
        assert user.id is not None

    def test_saml_upsert_by_cwid_not_email(self, db):
        """Second login with the same cwid updates the same row even if email changed."""
        u1 = provision_user(db, display_name="V1", auth_method="saml",
                            cwid="dup2002", email="old@example.com")
        u2 = provision_user(db, display_name="V2", auth_method="saml",
                            cwid="dup2002", email="new@example.com")
        assert u1.id == u2.id
        assert u2.email == "new@example.com"

    def test_legacy_email_row_upgraded_in_place(self, db):
        """A pre-CWID row (email only) is adopted and its cwid set -- no duplicate."""
        legacy = provision_user(db, display_name="Legacy", auth_method="saml",
                                email="legacy@med.cornell.edu")
        assert legacy.cwid is None
        upgraded = provision_user(db, display_name="Legacy", auth_method="saml",
                                  cwid="legacy", email="legacy@med.cornell.edu")
        assert upgraded.id == legacy.id
        assert upgraded.cwid == "legacy"

    def test_survives_concurrent_insert(self, db):
        """#359: if a concurrent login inserts the row during our lookup->insert
        window, the losing request returns the existing user instead of raising
        IntegrityError. Rebased onto CWID anchoring: for SSO the constraint that
        fires is unique(cwid), so the recovery lookup must key on cwid. Uses two
        email-less affiliates -- which #326 now permits -- because that is the
        case an email-keyed recovery gets wrong.
        """
        from unittest.mock import patch
        from sqlalchemy.orm import Query

        # A second email-less SSO user, so recovering by email would be ambiguous.
        provision_user(db, display_name="Decoy", auth_method="saml",
                       cwid="decoy9009", email=None, role="user")
        winner = provision_user(db, display_name="Winner", auth_method="saml",
                                cwid="race3003", email=None, role="user")

        # Force our pre-insert lookup to miss the freshly-committed row so the
        # insert collides -- exactly the race in #359.
        with patch.object(Query, "first", return_value=None):
            loser = provision_user(db, display_name="Loser", auth_method="saml",
                                   cwid="race3003", email=None, role="user")

        assert loser.id == winner.id
        assert db.query(User).filter(User.cwid == "race3003").count() == 1

    def test_survives_concurrent_insert_simple_auth(self, db):
        """Same race on the simple-auth path, where there is no cwid and
        unique(email) is the constraint that fires."""
        from unittest.mock import patch
        from sqlalchemy.orm import Query

        winner = provision_user(db, display_name="Winner", auth_method="simple",
                                email="simple-race@example.com", role="user")

        with patch.object(Query, "first", return_value=None):
            loser = provision_user(db, display_name="Loser", auth_method="simple",
                                   email="simple-race@example.com", role="user")

        assert loser.id == winner.id
        assert db.query(User).filter(User.email == "simple-race@example.com").count() == 1


class TestConfigService:
    """ARCH-04: Config values centralized with correct defaults."""

    def test_session_ttl_default(self):
        # Shortened from 7d to 12h in 5ee9538 (session revocation + shorter TTL,
        # #110/#127); the assertion was not updated at the time.
        assert SESSION_TTL == 12 * 3600  # 43200

    def test_login_rate_limit_defaults(self):
        assert LOGIN_RATE_LIMIT_MAX == 10
        assert LOGIN_RATE_LIMIT_WINDOW == 60

    def test_upload_size_default(self):
        # Default lowered from 50 MB to 10 MB in PR #54.
        assert MAX_UPLOAD_SIZE == 10 * 1024 * 1024  # 10485760

    def test_cost_estimation_defaults(self):
        assert TIME_PER_1K_TOKENS == 30
        assert BASE_OVERHEAD_SECONDS == 60

    def test_cost_per_1k_tokens_derived(self):
        # Rate is derived from the model configured in llm_config.yaml.
        assert get_cost_per_1k_tokens() > 0

    # (char count as /estimate computes it, actual USD) for every run measured
    # for #538: 20 corpus CVs run 2026-09-17 at c7daa91 (sum of per-stage
    # costs recorded in the stage JSONs), web runs 976WPY and A5IZ6Q
    # (September), and AVFPHW / YME2VA from the issue's own table.
    _MEASURED_RUN_COSTS = (
        (5278, 0.55), (5653, 0.54), (6748, 0.56), (9135, 0.68),
        (15595, 0.97), (16033, 1.01), (22092, 0.94), (23027, 0.77),
        (27644, 1.11), (28728, 1.26), (34462, 1.68), (35032, 1.42),
        (36721, 1.89), (37263, 1.70), (41844, 1.82), (42858, 1.97),
        (58757, 2.57), (106058, 3.07), (123211, 4.74), (131574, 4.15),
        (140151, 4.93), (168421, 6.41), (198639, 6.80), (338634, 13.17),
    )

    @pytest.mark.parametrize("chars,actual", _MEASURED_RUN_COSTS)
    def test_run_cost_range_brackets_measured_actual(self, chars, actual):
        # #538: the quoted maximum was below the actual on all 24 of these.
        # Pins the cost-model constants so they can't drift back under them.
        cost_min, cost_max = get_estimated_run_cost(chars)
        assert cost_min <= actual <= cost_max

    def test_run_cost_scales_with_entry_density(self):
        # Cost must rise with document size (more entries -> more stage 3b
        # calls), and a tiny CV is floored, not zero.
        small_min, small_max = get_estimated_run_cost(3000)
        big_min, big_max = get_estimated_run_cost(60000)
        assert small_min >= 0.10
        assert big_max > small_max


class TestErrorHelpers:
    """ARCH-06: All error helpers produce consistent structured format."""

    def test_all_errors_have_structured_detail(self):
        errors = [
            not_found("test"),
            bad_request("test"),
            forbidden("test"),
            validation_error("test"),
        ]
        for err in errors:
            assert isinstance(err.detail, dict), f"Expected dict, got {type(err.detail)}"
            assert "error" in err.detail, f"Missing 'error' key in {err.detail}"
            assert "message" in err.detail, f"Missing 'message' key in {err.detail}"

    def test_not_found_status(self):
        assert not_found("x").status_code == 404

    def test_bad_request_status(self):
        assert bad_request("x").status_code == 400

    def test_forbidden_status(self):
        assert forbidden("x").status_code == 403

    def test_validation_error_status(self):
        assert validation_error("x").status_code == 422

    def test_bad_request_custom_error_code(self):
        err = bad_request("test", error_code="custom_code")
        assert err.detail["error"] == "custom_code"


# ============================================================
# Admin Service Tests (ARCH-05)
# ============================================================

from app.services.admin_service import get_users_with_stats, get_single_user_stats
from app.models import Run, Feedback
from datetime import datetime


class TestAdminService:
    """ARCH-05: Admin stats use aggregation, not N+1."""

    def test_get_users_with_stats_empty(self, db):
        """No users returns empty list."""
        result = get_users_with_stats(db)
        assert result == []

    def test_get_users_with_stats_no_runs(self, db):
        """User with no runs has zero stats."""
        user = User(email="norun@example.com", display_name="No Runs", role="user", auth_method="simple")
        db.add(user)
        db.commit()

        result = get_users_with_stats(db)
        assert len(result) == 1
        assert result[0].total_runs == 0
        assert result[0].total_cost == 0.0
        assert result[0].feedback_count == 0
        assert result[0].completed_run_count == 0
        assert result[0].runs_today == 0

    def test_get_users_with_stats_with_data(self, db):
        """User with runs and feedback has correct aggregated stats."""
        user = User(email="active@example.com", display_name="Active", role="user", auth_method="simple")
        db.add(user)
        db.commit()
        db.refresh(user)

        # Add runs
        now = datetime.now()
        run1 = Run(id="RUN001", filename="cv1.docx", file_type="docx", status="complete",
                    user_id=user.id, total_cost=0.15, started_at=now)
        run2 = Run(id="RUN002", filename="cv2.docx", file_type="docx", status="running",
                    user_id=user.id, total_cost=0.10, started_at=now)
        db.add_all([run1, run2])
        db.commit()

        # Add feedback
        fb = Feedback(run_id="RUN001", user_id=user.id, reviewer_role="faculty",
                      overall_usefulness=4, manual_conversion_effort="moderate",
                      correction_effort="moderate", likelihood_to_recommend=4)
        db.add(fb)
        db.commit()

        result = get_users_with_stats(db)
        assert len(result) == 1
        assert result[0].total_runs == 2
        assert result[0].total_cost == 0.25
        assert result[0].completed_run_count == 1
        assert result[0].feedback_count == 1
        assert result[0].runs_today == 2  # Both started today

    def test_get_users_with_stats_multiple_users(self, db):
        """Multiple users each get their own stats (no cross-contamination)."""
        u1 = User(email="u1@example.com", display_name="U1", role="user", auth_method="simple")
        u2 = User(email="u2@example.com", display_name="U2", role="user", auth_method="simple")
        db.add_all([u1, u2])
        db.commit()
        db.refresh(u1)
        db.refresh(u2)

        now = datetime.now()
        run1 = Run(id="RUN001", filename="cv.docx", file_type="docx", status="complete",
                    user_id=u1.id, total_cost=0.20, started_at=now)
        db.add(run1)
        db.commit()

        result = get_users_with_stats(db)
        # Results ordered by created_at desc, so u2 first (created second)
        stats_map = {r.email: r for r in result}
        assert stats_map["u1@example.com"].total_runs == 1
        assert stats_map["u1@example.com"].total_cost == 0.20
        assert stats_map["u2@example.com"].total_runs == 0
        assert stats_map["u2@example.com"].total_cost == 0.0

    def test_get_single_user_stats(self, db):
        """get_single_user_stats returns correct stats for one user."""
        user = User(email="single@example.com", display_name="Single", role="user", auth_method="simple")
        db.add(user)
        db.commit()
        db.refresh(user)

        now = datetime.now()
        run = Run(id="RUN001", filename="cv.docx", file_type="docx", status="complete",
                  user_id=user.id, total_cost=0.30, started_at=now)
        db.add(run)
        db.commit()

        stats = get_single_user_stats(user, db)
        assert stats["total_runs"] == 1
        assert stats["total_cost"] == 0.30
        assert stats["completed_run_count"] == 1
        assert stats["feedback_count"] == 0
        assert stats["runs_today"] == 1


class TestNormalizeEmail:
    """#348: shared email normalization keeps the identity key consistent."""

    def test_lowercases_and_trims(self):
        from app.services.user_service import normalize_email
        assert normalize_email("  John.Doe@MED.Cornell.EDU ") == "john.doe@med.cornell.edu"

    def test_idempotent(self):
        from app.services.user_service import normalize_email
        assert normalize_email("a@b.com") == "a@b.com"

    def test_none_passes_through(self):
        """#326 made email optional; a SAML user with no ED `mail` reaches this
        with None and must not raise -- it did, and every such login 500'd into
        /login?error=auth_failed."""
        from app.services.user_service import normalize_email
        assert normalize_email(None) is None

    def test_blank_collapses_to_none(self):
        """#413: "" is a real value under unique(email), so two email-less users
        would collide on it. NULL does not collide."""
        from app.services.user_service import normalize_email
        assert normalize_email("") is None
        assert normalize_email("   ") is None


class TestCsvInjectionGuard:
    """#333: admin CSV exports must neutralize formula-injection triggers."""

    def test_neutralizes_formula_triggers(self):
        from app.api.admin_routes import _sanitize_csv_cell
        for trigger in ("=", "+", "-", "@", "\t", "\r"):
            assert _sanitize_csv_cell(f"{trigger}cmd()") == f"'{trigger}cmd()"

    def test_leaves_safe_values_untouched(self):
        from app.api.admin_routes import _sanitize_csv_cell
        assert _sanitize_csv_cell("alice@example.com".lstrip("@")) == "alice@example.com".lstrip("@")
        assert _sanitize_csv_cell("Jane Doe") == "Jane Doe"
        assert _sanitize_csv_cell("cv.docx") == "cv.docx"
        assert _sanitize_csv_cell(42) == 42  # non-strings pass through unchanged
        assert _sanitize_csv_cell("") == ""


# ============================================================
# Consent Service Tests (#655 review: ConsentService.record_consent +
# get_current_consent_document)
# ============================================================

from unittest.mock import patch

from app.consent import get_current_consent_document, get_consent_text, get_consent_hash
from app.services.consent_service import record_consent
from app.models import Consent


def _seed_consent_user(db, email="consent-svc@example.com"):
    user = User(email=email, display_name="Consent Svc User", role="user", auth_method="simple")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestGetCurrentConsentDocument:
    """#655 review: version, text, and hash resolved together as one unit,
    instead of independent get_config_value()/get_consent_text()/get_consent_hash()
    lookups at each call site."""

    def test_returns_consistent_version_text_hash(self, db, seed_simple_mode):
        document = get_current_consent_document(db)
        assert document.version == "1.0"
        assert document.text == get_consent_text()
        assert document.hash == get_consent_hash()

    def test_falls_back_to_default_version_when_unconfigured(self, db):
        # No SystemConfig row for consent_version -- falls back to the same
        # "1.0" default the router used before this moved.
        document = get_current_consent_document(db)
        assert document.version == "1.0"


class TestRecordConsent:
    """#655 review: ConsentService.record_consent owns the domain workflow
    (resolve version, create audit row, mutate user, manage the transaction)
    that used to live inline in the POST /api/consent route handler."""

    def test_happy_path_creates_audit_row_and_updates_user(self, db, seed_simple_mode):
        user = _seed_consent_user(db)

        document = record_consent(
            db=db,
            user=user,
            default_submission_type="own_cv",
            ip_address="203.0.113.7",
            user_agent="pytest-agent",
        )

        assert document.version == "1.0"

        record = db.query(Consent).filter(Consent.user_id == user.id).one()
        assert record.consent_version == "1.0"
        assert record.consent_text_hash == document.hash
        assert record.ip_address == "203.0.113.7"
        assert record.user_agent == "pytest-agent"

        assert user.consent_version == "1.0"
        assert user.consent_date is not None
        assert user.default_submission_type == "own_cv"

    def test_commit_failure_rolls_back_and_reraises(self, db, seed_simple_mode):
        user = _seed_consent_user(db)

        with patch.object(db, "commit", side_effect=RuntimeError("boom")), \
             patch.object(db, "rollback") as mock_rollback:
            with pytest.raises(RuntimeError):
                record_consent(
                    db=db,
                    user=user,
                    default_submission_type="own_cv",
                    ip_address="203.0.113.7",
                    user_agent="pytest-agent",
                )
            mock_rollback.assert_called_once()

    def test_repeat_submission_creates_a_second_audit_row(self, db, seed_simple_mode):
        """Consent is an append-only audit table: two submissions from the same
        user each get their own row -- not deduped or updated in place."""
        user = _seed_consent_user(db)

        record_consent(db, user, "own_cv", "203.0.113.7", "agent-1")
        record_consent(db, user, "authorized_admin", "203.0.113.8", "agent-2")

        rows = db.query(Consent).filter(Consent.user_id == user.id).all()
        assert len(rows) == 2
        assert user.default_submission_type == "authorized_admin"
