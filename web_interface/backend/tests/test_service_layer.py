"""Tests for the service layer (ARCH-01 through ARCH-06 regression)."""
import os
os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

import pytest
from fastapi import HTTPException

from app.services.run_service import check_run_access
from app.services.user_service import provision_user
from app.services.config_service import (
    SESSION_TTL, LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW,
    MAX_UPLOAD_SIZE, TIME_PER_1K_TOKENS, BASE_OVERHEAD_SECONDS,
    get_cost_per_1k_tokens,
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

    def test_allows_unowned_run(self, db):
        """Runs with user_id=None are accessible to any user."""
        user = User(email="user@example.com", display_name="User", role="user", auth_method="simple")
        db.add(user)
        db.commit()
        db.refresh(user)
        run = Run(id="ABC123", filename="cv.docx", file_type="docx", status="created", user_id=None)
        db.add(run)
        db.commit()

        result = check_run_access("ABC123", user, db)
        assert result.id == "ABC123"


class TestProvisionUser:
    """ARCH-03: provision_user handles both create and update."""

    def test_creates_new_user(self, db):
        user = provision_user(db, "new@example.com", "New User", "simple", role="user")
        assert user.email == "new@example.com"
        assert user.display_name == "New User"
        assert user.auth_method == "simple"
        assert user.role == "user"
        assert user.id is not None

    def test_updates_existing_user(self, db):
        # Create initial user
        provision_user(db, "existing@example.com", "Old Name", "simple", role="user")
        # Update
        user = provision_user(db, "existing@example.com", "New Name", "saml", role="admin")
        assert user.display_name == "New Name"
        assert user.auth_method == "saml"
        assert user.role == "admin"

    def test_preserves_role_when_none(self, db):
        """When role=None, existing user's role is preserved (SAML without ED)."""
        provision_user(db, "keep@example.com", "Keep Role", "simple", role="admin")
        user = provision_user(db, "keep@example.com", "Keep Role Updated", "saml", role=None)
        assert user.role == "admin"  # Preserved, not overwritten

    def test_new_user_defaults_to_user_role(self, db):
        """When role=None for a new user, defaults to 'user'."""
        user = provision_user(db, "default@example.com", "Default Role", "saml", role=None)
        assert user.role == "user"

    def test_simple_auth_with_admin_role(self, db):
        user = provision_user(db, "admin@example.com", "Admin", "simple", role="admin")
        assert user.role == "admin"
        assert user.auth_method == "simple"

    def test_saml_auth_with_ed_role(self, db):
        user = provision_user(db, "saml@example.com", "SAML User", "saml", role="admin")
        assert user.role == "admin"
        assert user.auth_method == "saml"


class TestConfigService:
    """ARCH-04: Config values centralized with correct defaults."""

    def test_session_ttl_default(self):
        assert SESSION_TTL == 7 * 24 * 3600  # 604800

    def test_login_rate_limit_defaults(self):
        assert LOGIN_RATE_LIMIT_MAX == 10
        assert LOGIN_RATE_LIMIT_WINDOW == 60

    def test_upload_size_default(self):
        assert MAX_UPLOAD_SIZE == 10 * 1024 * 1024  # 10 MiB safety-net default

    def test_cost_estimation_defaults(self):
        assert TIME_PER_1K_TOKENS == 30
        assert BASE_OVERHEAD_SECONDS == 60

    def test_cost_per_1k_tokens_derived(self):
        # Rate is derived from the model configured in llm_config.yaml.
        assert get_cost_per_1k_tokens() > 0


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
