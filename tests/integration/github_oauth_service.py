"""
Integration tests for GitHub App authentication routes.

Covers:
  - GET /auth/github/app/install  → redirect to GITHUB_APP_INSTALL_URL
  - GET /auth/github/app/callback → stores installation_id in session
  - GET /auth/logout              → clears installation_id
  - GET /api/auth/status          → reflects GitHub App session state
"""

from unittest.mock import MagicMock, patch
from urllib.parse import urlparse, parse_qs
import pytest


class TestGitHubAppInstallRoute:
    def test_app_install_redirects_to_configured_url(self, client):
        """GET /auth/github/app/install redirects to GITHUB_APP_INSTALL_URL when set."""
        with patch.dict("os.environ", {"GITHUB_APP_INSTALL_URL": "https://github.com/apps/vouch/installations/new"}):
            res = client.get("/auth/github/app/install")
            assert res.status_code == 302
            assert res.headers["Location"] == "https://github.com/apps/vouch/installations/new"

    def test_app_install_no_url_configured_redirects_with_error(self, client):
        """GET /auth/github/app/install without GITHUB_APP_INSTALL_URL returns error redirect."""
        with patch.dict("os.environ", {"GITHUB_APP_INSTALL_URL": ""}):
            res = client.get("/auth/github/app/install")

        assert res.status_code == 302

        location = res.headers["Location"]
        parsed = urlparse(location)
        params = parse_qs(parsed.query)

        assert parsed.path == "/"
        assert "error" in params
        assert params["error"] == [
            "GITHUB_APP_INSTALL_URL is not configured. "
            "Set it to your GitHub App's installation URL."
        ]

    @patch("dashboard.app._resolve_github_token")
    @patch("dashboard.app.requests.get")
    def test_auth_github_host_auto_connect(self, mock_get, mock_resolve, client, mock_github_user, mock_github_rate_limit):
        """When OAuth app is not registered, automatically authenticates using host token."""
        mock_resolve.return_value = "gho_host_token_999"

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

        with patch.dict(
            "os.environ",
            {"GITHUB_CLIENT_ID": "", "GITHUB_CLIENT_SECRET": ""},
        ):
            res = client.get("/auth/github")

        assert res.status_code == 302
        assert res.headers["Location"] == "/repos"

        with client.session_transaction() as sess:
            assert sess["github_token"] == "gho_host_token_999"
            assert sess["auth_type"] == "oauth"
            assert sess["user_login"] == mock_github_user["login"]


class TestGitHubAppCallbackRoute:
    def test_callback_missing_installation_id_redirects_with_error(self, client):
        """Callback without installation_id redirects to landing with error."""
        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = True
            res = client.get("/auth/github/app/callback?setup_action=install")
            assert res.status_code == 302
            assert "error=" in res.headers["Location"]

    def test_callback_app_not_configured_redirects_with_error(self, client):
        """Callback when GitHub App is not configured redirects with error."""
        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = False
            res = client.get("/auth/github/app/callback?installation_id=42&setup_action=install")
            assert res.status_code == 302
            assert "error=" in res.headers["Location"]

    def test_callback_state_mismatch_redirects_with_error(self, client):
        """Callback with wrong state rejects with error redirect."""
        with client.session_transaction() as sess:
            sess["app_install_state"] = "expected_state_abc"

        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = True
            res = client.get(
                "/auth/github/app/callback?installation_id=42&setup_action=install&state=wrong_state"
            )
            assert res.status_code == 302
            assert "error=" in res.headers["Location"]

    def test_callback_success_stores_installation_id(self, client):
        """Valid callback stores installation_id in session and redirects to /repos."""
        mock_token = "ghs_installation_access_token_xyz"
        mock_account = {
            "login": "vouch-org",
            "name": "Vouch Organisation",
            "avatar_url": "https://avatars.githubusercontent.com/u/12345?v=4",
        }

        with patch("dashboard.app.github_app_auth") as mock_auth, \
             patch("dashboard.app.requests.get") as mock_get:
            mock_auth.is_configured.return_value = True
            mock_auth.get_installation_token.return_value = mock_token
            mock_auth._build_jwt.return_value = "mock.jwt.token"

            mock_inst_resp = MagicMock()
            mock_inst_resp.status_code = 200
            mock_inst_resp.json.return_value = {"account": mock_account}
            mock_get.return_value = mock_inst_resp

            res = client.get(
                "/auth/github/app/callback?installation_id=99&setup_action=install"
            )
            assert res.status_code == 302
            assert res.headers["Location"] == "/repos"

            with client.session_transaction() as sess:
                assert sess["installation_id"] == 99
                assert sess["auth_type"] == "github_app"
                assert sess["user_login"] == "vouch-org"

    def test_callback_token_exchange_failure_redirects_with_error(self, client):
        """If token exchange raises, callback redirects to landing with error."""
        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = True
            mock_auth.get_installation_token.side_effect = RuntimeError("GitHub returned HTTP 404")

            res = client.get(
                "/auth/github/app/callback?installation_id=42&setup_action=install"
            )
            assert res.status_code == 302
            assert "error=" in res.headers["Location"]


class TestAuthStatusWithGitHubApp:
    def test_status_unauthenticated(self, client):
        """Unauthenticated client receives authenticated: False."""
        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = False
            res = client.get("/api/auth/status")
            assert res.status_code == 200
            data = res.get_json()
            assert data["authenticated"] is False
            assert data["auth_type"] == "demo"

    def test_status_github_app_session(self, client):
        """Session with installation_id returns authenticated: True with github_app auth_type."""
        with client.session_transaction() as sess:
            sess["installation_id"] = 42
            sess["auth_type"] = "github_app"
            sess["user_login"] = "vouch-org"
            sess["user_name"] = "Vouch Organisation"
            sess["user_avatar"] = "https://avatars.githubusercontent.com/u/12345?v=4"
            sess["rate_limit"] = {"limit": 5000, "remaining": 5000}

        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = True
            res = client.get("/api/auth/status")
            assert res.status_code == 200
            data = res.get_json()
            assert data["authenticated"] is True
            assert data["auth_type"] == "github_app"
            assert data["installation_id"] == 42
            assert data["user_login"] == "vouch-org"

    def test_status_exposes_app_configured_flag(self, client):
        """api/auth/status always exposes app_configured regardless of session."""
        with patch("dashboard.app.github_app_auth") as mock_auth:
            mock_auth.is_configured.return_value = True
            res = client.get("/api/auth/status")
            data = res.get_json()
            assert data["app_configured"] is True


class TestAuthLogoutClearsAppSession:
    def test_logout_clears_installation_id(self, client):
        """GET /auth/logout clears installation_id and other GitHub App session keys."""
        with client.session_transaction() as sess:
            sess["installation_id"] = 42
            sess["auth_type"] = "github_app"
            sess["user_login"] = "vouch-org"

        res = client.get("/auth/logout")
        assert res.status_code == 302
        assert res.headers["Location"] == "/"

        with client.session_transaction() as sess:
            assert "installation_id" not in sess
            assert "auth_type" not in sess
            assert "user_login" not in sess
