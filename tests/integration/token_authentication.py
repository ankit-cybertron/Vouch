"""
Integration tests for dashboard auth APIs (/api/auth/*).
"""

from unittest.mock import MagicMock, patch
import pytest


class TestAuthApiEndpoints:
    def test_auth_status_unauthenticated(self, client):
        """Unauthenticated client receives authenticated: False."""
        res = client.get("/api/auth/status")
        assert res.status_code == 200
        data = res.get_json()

        assert data["authenticated"] is False
        assert data["auth_type"] == "demo"
        assert data["user_login"] == ""

    def test_auth_status_authenticated(self, client):
        """Authenticated session returns user details and quota."""
        with client.session_transaction() as sess:
            sess["github_token"] = "gho_test_123"
            sess["user_login"] = "octocat"
            sess["user_name"] = "The Octocat"
            sess["user_avatar"] = "https://example.com/avatar.png"
            sess["auth_type"] = "oauth"
            sess["rate_limit"] = {"limit": 5000, "remaining": 4900}

        res = client.get("/api/auth/status")
        assert res.status_code == 200
        data = res.get_json()

        assert data["authenticated"] is True
        assert data["user_login"] == "octocat"
        assert data["auth_type"] == "oauth"
        assert data["rate_limit"]["remaining"] == 4900

    def test_post_token_empty_payload(self, client):
        """POST /api/auth/token without token returns 400 error."""
        res = client.post("/api/auth/token", json={})
        assert res.status_code == 400
        data = res.get_json()
        assert data["success"] is False
        assert "empty" in data["error"].lower()

    @patch("dashboard.app.requests.get")
    def test_post_token_invalid_github_response(self, mock_get, client):
        """POST /api/auth/token with rejected token returns 400 error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        res = client.post("/api/auth/token", json={"token": "ghp_invalid_token"})
        assert res.status_code == 400
        data = res.get_json()
        assert data["success"] is False
        assert "Invalid GitHub token" in data["error"]

    @patch("dashboard.app.requests.get")
    def test_post_token_valid_sets_session(self, mock_get, client, mock_github_user, mock_github_rate_limit):
        """POST /api/auth/token with valid token sets encrypted session and returns user info."""
        def mock_side_effect(url, **kwargs):
            resp = MagicMock()
            if "rate_limit" in url:
                resp.status_code = 200
                resp.json.return_value = mock_github_rate_limit
            else:
                resp.status_code = 200
                resp.json.return_value = mock_github_user
            return resp

        mock_get.side_effect = mock_side_effect

        res = client.post("/api/auth/token", json={"token": "ghp_valid_test_token_12345"})
        assert res.status_code == 200
        data = res.get_json()

        assert data["success"] is True
        assert data["user"]["login"] == "test-dev"
        assert data["rate_limit"]["remaining"] == 4950

        # Verify session state was persisted
        with client.session_transaction() as sess:
            assert sess["github_token"] == "ghp_valid_test_token_12345"
            assert sess["user_login"] == "test-dev"
            assert sess["auth_type"] == "token"

    def test_post_clear_token_resets_session(self, client):
        """POST /api/auth/clear-token clears session and resets to demo mode."""
        with client.session_transaction() as sess:
            sess["github_token"] = "gho_test_123"
            sess["user_login"] = "octocat"

        res = client.post("/api/auth/clear-token")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

        with client.session_transaction() as sess:
            assert "github_token" not in sess
            assert "user_login" not in sess
