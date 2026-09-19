"""
Integration tests for dashboard auth APIs (/api/auth/*) with GitHub App session model.

The old PAT-paste endpoint (POST /api/auth/token) has been removed.
These tests validate the remaining auth API surface:
  - GET  /api/auth/status       → reflects GitHub App OR OAuth session
  - POST /api/auth/clear-token  → clears all session state including installation_id
"""

from unittest.mock import patch
import pytest


class TestAuthApiEndpoints:
    def test_auth_status_unauthenticated(self, client):
        """Unauthenticated client receives authenticated: False."""
        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = False
            res = client.get("/api/auth/status")
            assert res.status_code == 200
            data = res.get_json()

            assert data["authenticated"] is False
            assert data["auth_type"] == "demo"
            assert data["user_login"] == ""

    def test_auth_status_authenticated_with_oauth(self, client):
        """OAuth-authenticated session returns user details and quota."""
        with client.session_transaction() as sess:
            sess["github_token"] = "gho_test_123"
            sess["user_login"] = "octocat"
            sess["user_name"] = "The Octocat"
            sess["user_avatar"] = "https://example.com/avatar.png"
            sess["auth_type"] = "oauth"
            sess["rate_limit"] = {"limit": 5000, "remaining": 4900}

        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = False
            res = client.get("/api/auth/status")
            assert res.status_code == 200
            data = res.get_json()

            assert data["authenticated"] is True
            assert data["user_login"] == "octocat"
            assert data["auth_type"] == "oauth"
            assert data["rate_limit"]["remaining"] == 4900

    def test_auth_status_authenticated_with_github_app(self, client):
        """GitHub App installation session returns authenticated: True with app auth_type."""
        with client.session_transaction() as sess:
            sess["installation_id"] = 77
            sess["auth_type"] = "github_app"
            sess["user_login"] = "my-org"
            sess["user_name"] = "My Organisation"
            sess["user_avatar"] = "https://avatars.githubusercontent.com/u/77?v=4"
            sess["rate_limit"] = {"limit": 5000, "remaining": 5000}

        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = True
            res = client.get("/api/auth/status")
            assert res.status_code == 200
            data = res.get_json()

            assert data["authenticated"] is True
            assert data["auth_type"] == "github_app"
            assert data["installation_id"] == 77
            assert data["app_configured"] is True
            assert data["user_login"] == "my-org"

    def test_post_clear_token_resets_session(self, client):
        """POST /api/auth/clear-token clears session including installation_id and resets to demo mode."""
        with client.session_transaction() as sess:
            sess["installation_id"] = 42
            sess["github_token"] = "gho_test_123"
            sess["user_login"] = "octocat"
            sess["auth_type"] = "github_app"

        res = client.post("/api/auth/clear-token")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

        with client.session_transaction() as sess:
            assert "github_token" not in sess
            assert "installation_id" not in sess
            assert "user_login" not in sess

    def test_pat_paste_endpoint_removed(self, client):
        """POST /api/auth/token (legacy PAT input) no longer exists — returns 404 or 405."""
        res = client.post("/api/auth/token", json={"token": "ghp_some_pat"})
        assert res.status_code in (404, 405), (
            f"PAT endpoint should be removed but returned {res.status_code}"
        )
