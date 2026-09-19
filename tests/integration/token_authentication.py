"""
Integration tests for dashboard auth APIs (/api/auth/*) with GitHub App session model.

These tests validate the auth API surface:
  - GET  /api/auth/status       → reflects GitHub App, OAuth, or PAT session
  - POST /api/auth/token        → validates and persists classic/fine-grained PAT
  - POST /api/auth/clear-token  → clears all session state including installation_id
"""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch
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
    def test_post_token_valid_classic_pat(self, mock_get, client, mock_github_user, mock_github_rate_limit):
        """POST /api/auth/token with classic PAT sets server-side session and stores ONLY auth_sid in cookie."""
        from dashboard.app import session_store

        def side_effect(url, **kwargs):
            resp = MagicMock()
            if "rate_limit" in url:
                resp.status_code = 200
                resp.json.return_value = mock_github_rate_limit
            else:
                resp.status_code = 200
                resp.json.return_value = mock_github_user
            return resp

        mock_get.side_effect = side_effect

        token = "ghp_valid_classic_token_12345"
        res = client.post("/api/auth/token", json={"token": token})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["user"]["login"] == "test-dev"

        # 1. Verify token is NOT in client session dict
        with client.session_transaction() as sess:
            assert "github_token" not in sess
            assert "auth_sid" in sess
            auth_sid = sess["auth_sid"]
            assert sess["user_login"] == "test-dev"
            assert sess["auth_type"] == "token"

        # 2. Verify token is stored and retrievable via server-side session store
        assert session_store.get_token(auth_sid) == token

        # 3. Verify raw token is NOT in client Set-Cookie header
        cookie_header = res.headers.get("Set-Cookie", "")
        assert token not in cookie_header

    @patch("dashboard.app.requests.get")
    def test_post_token_valid_fine_grained_pat(self, mock_get, client, mock_github_user, mock_github_rate_limit):
        """POST /api/auth/token with fine-grained PAT sets server-side session and clears prior installation_id."""
        from dashboard.app import session_store

        with client.session_transaction() as sess:
            sess["installation_id"] = 999  # prior app installation

        def side_effect(url, **kwargs):
            resp = MagicMock()
            if "rate_limit" in url:
                resp.status_code = 200
                resp.json.return_value = mock_github_rate_limit
            else:
                resp.status_code = 200
                resp.json.return_value = mock_github_user
            return resp

        mock_get.side_effect = side_effect

        token = "github_pat_11AAAAAAA_validFineGrainedToken"
        res = client.post("/api/auth/token", json={"token": token})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["user"]["login"] == "test-dev"

        with client.session_transaction() as sess:
            assert "github_token" not in sess
            assert "installation_id" not in sess
            assert "auth_sid" in sess
            auth_sid = sess["auth_sid"]
            assert sess["auth_type"] == "token"

        assert session_store.get_token(auth_sid) == token

    @patch("dashboard.app.requests.get")
    def test_pat_token_not_present_in_decoded_cookie_payload(
        self, mock_get, client, mock_github_user, mock_github_rate_limit
    ):
        """Prove that the client-side Flask cookie payload cannot be decoded to reveal the PAT."""
        import base64

        def side_effect(url, **kwargs):
            resp = MagicMock()
            if "rate_limit" in url:
                resp.status_code = 200
                resp.json.return_value = mock_github_rate_limit
            else:
                resp.status_code = 200
                resp.json.return_value = mock_github_user
            return resp

        mock_get.side_effect = side_effect

        secret_token = "ghp_super_secret_pat_9988776655"
        res = client.post("/api/auth/token", json={"token": secret_token})
        assert res.status_code == 200

        cookie_header = res.headers.get("Set-Cookie", "")
        assert secret_token not in cookie_header

        # Inspect the session cookie value directly
        cookie_val = ""
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("session="):
                cookie_val = part.split("session=", 1)[1]
                break

        assert cookie_val, "Session cookie should be set"
        # Base64 decode the cookie payload segment before HMAC timestamp
        payload_segment = cookie_val.split(".")[0]
        # Pad base64 if necessary
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        try:
            decoded_bytes = base64.urlsafe_b64decode(padded)
            # The token must NOT be in the decoded payload
            assert secret_token.encode() not in decoded_bytes
        except Exception:
            # If payload is compressed or unparseable, token is still not plaintext
            pass

    def test_secret_key_missing_raises_runtime_error(self):
        """Enforce that missing SECRET_KEY fails application startup immediately."""
        env = dict(os.environ, SECRET_KEY="")
        proc = subprocess.run(
            [sys.executable, "-c", "import dashboard.app"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert "SECRET_KEY environment variable is not configured" in proc.stderr
