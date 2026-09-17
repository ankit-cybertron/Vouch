"""
Integration tests for GitHub OAuth routes (/auth/github, /auth/github/callback, /auth/logout).
"""

from unittest.mock import MagicMock, patch
import pytest


class TestOAuthFlow:
    def test_auth_github_with_client_id_redirects_to_github_authorize(self, client):
        """When GITHUB_CLIENT_ID and SECRET are set, initiates OAuth redirect to GitHub."""
        with patch.dict("os.environ", {
            "GITHUB_CLIENT_ID": "mock_client_id_123",
            "GITHUB_CLIENT_SECRET": "mock_client_secret_456",
        }):
            res = client.get("/auth/github")
            assert res.status_code == 302
            loc = res.headers["Location"]
            assert loc.startswith("https://github.com/login/oauth/authorize")
            assert "client_id=mock_client_id_123" in loc
            assert "scope=read:user,repo" in loc

            # Confirm state was saved in session
            with client.session_transaction() as sess:
                assert "oauth_state" in sess

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

        with patch.dict("os.environ", {"GITHUB_CLIENT_ID": "", "GITHUB_CLIENT_SECRET": ""}):
            res = client.get("/auth/github")
            assert res.status_code == 302
            assert res.headers["Location"] == "/repos"

            # Check that session was populated with user details
            with client.session_transaction() as sess:
                assert sess["github_token"] == "gho_host_token_999"
                assert sess["user_login"] == "test-dev"
                assert sess["auth_type"] == "oauth"

    def test_auth_github_callback_state_mismatch(self, client):
        """Callback with state mismatch or missing state rejects with error redirect."""
        with client.session_transaction() as sess:
            sess["oauth_state"] = "expected_state_abc"

        res = client.get("/auth/github/callback?code=some_code&state=wrong_state")
        assert res.status_code == 302
        assert "error=" in res.headers["Location"]

    @patch("dashboard.app.requests.post")
    @patch("dashboard.app.requests.get")
    def test_auth_github_callback_success(self, mock_get, mock_post, client, mock_github_user, mock_github_rate_limit):
        """Valid OAuth callback exchanges code for token, saves session, and redirects to /repos."""
        with client.session_transaction() as sess:
            sess["oauth_state"] = "valid_state_123"

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "gho_newly_minted_oauth_token",
            "token_type": "bearer",
            "scope": "read:user,repo",
        }
        mock_post.return_value = mock_token_resp

        def mock_get_side_effect(url, **kwargs):
            resp = MagicMock()
            if "rate_limit" in url:
                resp.status_code = 200
                resp.json.return_value = mock_github_rate_limit
            else:
                resp.status_code = 200
                resp.json.return_value = mock_github_user
            return resp

        mock_get.side_effect = mock_get_side_effect

        with patch.dict("os.environ", {
            "GITHUB_CLIENT_ID": "client_id_123",
            "GITHUB_CLIENT_SECRET": "client_secret_456",
        }):
            res = client.get("/auth/github/callback?code=auth_code_xyz&state=valid_state_123")
            assert res.status_code == 302
            assert res.headers["Location"] == "/repos"

            with client.session_transaction() as sess:
                assert sess["github_token"] == "gho_newly_minted_oauth_token"
                assert sess["user_login"] == "test-dev"
                assert sess["auth_type"] == "oauth"

    def test_auth_logout_clears_session(self, client):
        """GET /auth/logout clears user session and redirects to /."""
        with client.session_transaction() as sess:
            sess["github_token"] = "gho_active_session"
            sess["user_login"] = "octocat"

        res = client.get("/auth/logout")
        assert res.status_code == 302
        assert res.headers["Location"] == "/"

        with client.session_transaction() as sess:
            assert "github_token" not in sess
            assert "user_login" not in sess
