"""
Vouch — GitHub Authentication Service.

Unified module supporting:
  - OAuth 2.0 Web Application Flow (authorize redirect & code exchange)
  - Token profile validation & rate limit extraction
  - Host-token auto-connect
  - Real-time rate limit tracking from GitHub API response headers
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Any

import requests

logger = logging.getLogger(__name__)


class GitHubAuthManager:
    """Manager for GitHub OAuth, token verification, and quota monitoring."""

    def __init__(self, api_base: str = "https://api.github.com"):
        self.api_base = api_base.rstrip("/")

    def initiate_oauth(
        self,
        client_id: str,
        redirect_uri: str,
        scope: str = "read:user repo",
    ) -> tuple[str, str]:
        """Generate authorization URL and state token."""
        if not client_id or not client_id.strip():
            raise ValueError("GITHUB_CLIENT_ID is required to initiate OAuth flow.")
        if not redirect_uri or not redirect_uri.strip():
            raise ValueError("redirect_uri is required to initiate OAuth flow.")

        state = secrets.token_urlsafe(16)
        params = {
            "client_id": client_id.strip(),
            "redirect_uri": redirect_uri.strip(),
            "scope": scope,
            "state": state,
        }
        auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
        return auth_url, state

    def complete_oauth(
        self,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Exchange authorization code for access token and fetch user details."""
        if not client_id or not client_secret:
            raise ValueError("Both GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are required.")
        if not code or not code.strip():
            raise ValueError("Authorization code is required.")

        resp = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id.strip(),
                "client_secret": client_secret.strip(),
                "code": code.strip(),
                "redirect_uri": redirect_uri.strip(),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub token exchange returned HTTP {resp.status_code}.")

        token_data = resp.json()
        if "error" in token_data:
            err_desc = token_data.get("error_description", token_data["error"])
            raise RuntimeError(f"GitHub OAuth error: {err_desc}")

        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("GitHub did not return an access token.")

        profile = self.validate_token(access_token)
        profile["access_token"] = access_token
        profile["auth_type"] = "oauth"
        return profile

    def validate_token(self, token: str) -> dict[str, Any]:
        """Validate token and fetch profile details + quota."""
        clean_token = (token or "").strip()
        if not clean_token:
            raise ValueError("Token cannot be empty.")

        headers = {
            "Authorization": f"Bearer {clean_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        user_resp = requests.get(f"{self.api_base}/user", headers=headers, timeout=10)
        if user_resp.status_code == 401:
            raise PermissionError("Invalid GitHub token (HTTP 401).")
        if user_resp.status_code != 200:
            raise RuntimeError(f"GitHub user lookup failed: HTTP {user_resp.status_code}")

        user_data = user_resp.json()

        rate_resp = requests.get(f"{self.api_base}/rate_limit", headers=headers, timeout=10)
        rate_data = rate_resp.json().get("rate", {}) if rate_resp.status_code == 200 else {}

        return {
            "valid": True,
            "login": user_data.get("login", "unknown"),
            "name": user_data.get("name") or user_data.get("login", "GitHub User"),
            "avatar_url": user_data.get("avatar_url", ""),
            "rate_limit": {
                "limit": rate_data.get("limit", 5000),
                "remaining": rate_data.get("remaining", 5000),
                "reset": rate_data.get("reset", 0),
            },
        }

    def auto_connect_host(self, host_token: str) -> dict[str, Any] | None:
        """Validate host token and return profile if valid."""
        if not host_token or not host_token.strip():
            return None
        try:
            info = self.validate_token(host_token)
            info["access_token"] = host_token.strip()
            info["auth_type"] = "oauth"
            return info
        except Exception:
            return None

    @staticmethod
    def extract_rate_limit(headers: dict[str, Any] | requests.structures.CaseInsensitiveDict) -> dict[str, int] | None:
        """Extract rate limit numbers from GitHub response headers in real-time."""
        headers_lower = {str(k).lower(): v for k, v in headers.items()}
        rem_str = headers_lower.get("x-ratelimit-remaining")
        if rem_str is not None:
            try:
                rem = int(rem_str)
                lim = int(headers_lower.get("x-ratelimit-limit", 5000))
                reset = int(headers_lower.get("x-ratelimit-reset", 0))
                return {
                    "limit": lim,
                    "remaining": rem,
                    "reset": reset,
                }
            except (ValueError, TypeError):
                pass
        return None


auth_manager = GitHubAuthManager()

