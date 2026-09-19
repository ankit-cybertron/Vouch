"""
Vouch — GitHub App Authentication Module.

Implements short-lived Installation Access Token (IAT) lifecycle:
  1. Sign a 10-minute RS256 JWT using GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY.
  2. Exchange it for an Installation Access Token via
     POST /app/installations/{installation_id}/access_tokens.
  3. Cache the token in memory (thread-safe), refreshing automatically
     when it approaches expiry (5-minute safety buffer).

Environment variables
---------------------
GITHUB_APP_ID           : Numeric GitHub App ID (required).
GITHUB_APP_PRIVATE_KEY  : Full RSA private key PEM string, newlines as \\n
                          OR the literal text of a PEM file. (required)
GITHUB_APP_PRIVATE_KEY_PATH : Path to a PEM file (alternative to inline key).
GITHUB_APP_INSTALL_URL  : Full installation URL, e.g.
                          https://github.com/apps/vouch/installations/new
GITHUB_WEBHOOK_SECRET   : Secret used to verify incoming webhook payloads.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency guard — give a clear error if PyJWT / cryptography are
# missing rather than a confusing ImportError deep inside a request.
# ---------------------------------------------------------------------------
try:
    import jwt  # PyJWT
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    _JWT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JWT_AVAILABLE = False
    jwt = None  # type: ignore[assignment]
    load_pem_private_key = None  # type: ignore[assignment]

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_GITHUB_API_BASE = "https://api.github.com"
_JWT_TTL_SECONDS = 600  # 10 minutes (GitHub max)
_TOKEN_REFRESH_BUFFER_SECONDS = 300  # refresh 5 minutes before expiry


# ---------------------------------------------------------------------------
# In-memory token cache entry
# ---------------------------------------------------------------------------
class _CachedToken:
    __slots__ = ("token", "expires_at")

    def __init__(self, token: str, expires_at: datetime) -> None:
        self.token = token
        self.expires_at = expires_at

    def is_valid(self) -> bool:
        """Return True if the token has at least _TOKEN_REFRESH_BUFFER_SECONDS left."""
        remaining = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return remaining > _TOKEN_REFRESH_BUFFER_SECONDS


# ---------------------------------------------------------------------------
# Main auth class
# ---------------------------------------------------------------------------
class GitHubAppAuth:
    """
    Thread-safe manager for GitHub App Installation Access Tokens.

    Usage (after module-level singleton is initialised)::

        token = github_app_auth.get_installation_token(installation_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        # Cache: installation_id (int) → _CachedToken
        self._cache: dict[int, _CachedToken] = {}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True when both GITHUB_APP_ID and a private key are available."""
        return bool(self._app_id() and self._private_key_pem())

    def get_installation_token(self, installation_id: int | str) -> str:
        """
        Return a valid Installation Access Token for *installation_id*.

        Raises
        ------
        RuntimeError
            If the app is not configured or the GitHub API call fails.
        """
        if not _JWT_AVAILABLE:
            raise RuntimeError(
                "PyJWT and cryptography packages are required for GitHub App auth. "
                "Install them with: pip install PyJWT cryptography"
            )

        installation_id = int(installation_id)

        with self._lock:
            cached = self._cache.get(installation_id)
            if cached and cached.is_valid():
                logger.debug(
                    "Reusing cached installation token for installation %s", installation_id
                )
                return cached.token

            # Fetch a fresh token
            token, expires_at = self._exchange_token(installation_id)
            self._cache[installation_id] = _CachedToken(token, expires_at)
            logger.info(
                "Fetched new installation token for installation %s, expires %s",
                installation_id,
                expires_at.isoformat(),
            )
            return token

    def evict(self, installation_id: int | str) -> None:
        """Remove a cached token (e.g. after receiving an installation.deleted event)."""
        with self._lock:
            self._cache.pop(int(installation_id), None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _app_id(self) -> Optional[str]:
        return os.environ.get("GITHUB_APP_ID", "").strip() or None

    def _private_key_pem(self) -> Optional[bytes]:
        """
        Resolve the RSA private key PEM from environment.

        Checks (in order):
          1. GITHUB_APP_PRIVATE_KEY env var (inline PEM, \\n escaped as literal \\n)
          2. GITHUB_APP_PRIVATE_KEY_PATH env var (path to PEM file)
        """
        inline = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
        if inline:
            # Allow \\n literals (common in env-var injection) to be real newlines
            pem = inline.replace("\\n", "\n")
            return pem.encode() if isinstance(pem, str) else pem

        path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
        if path and os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()

        return None

    def _build_jwt(self) -> str:
        """
        Sign a short-lived JWT (RS256) for authenticating as the GitHub App itself.

        The JWT is used only to request Installation Access Tokens; it is never
        stored in the user session or sent to the browser.
        """
        if not _JWT_AVAILABLE:
            raise RuntimeError("PyJWT / cryptography not installed.")

        app_id = self._app_id()
        if not app_id:
            raise RuntimeError("GITHUB_APP_ID environment variable is not set.")

        pem_bytes = self._private_key_pem()
        if not pem_bytes:
            raise RuntimeError(
                "GitHub App private key not found. Set GITHUB_APP_PRIVATE_KEY or "
                "GITHUB_APP_PRIVATE_KEY_PATH."
            )

        now = int(time.time())
        payload = {
            "iat": now - 60,  # issued 60 s in the past to tolerate clock skew
            "exp": now + _JWT_TTL_SECONDS,
            "iss": app_id,
        }

        # Load key with cryptography to validate it before passing to PyJWT
        private_key = load_pem_private_key(pem_bytes, password=None)

        encoded: str = jwt.encode(payload, private_key, algorithm="RS256")  # type: ignore[arg-type]
        return encoded

    def _exchange_token(self, installation_id: int) -> tuple[str, datetime]:
        """
        POST /app/installations/{id}/access_tokens and return (token, expires_at).
        """
        app_jwt = self._build_jwt()
        url = f"{_GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )

        if resp.status_code != 201:
            logger.error(
                "Failed to exchange installation token: HTTP %s — %s",
                resp.status_code,
                resp.text[:200],
            )
            raise RuntimeError(
                f"GitHub returned HTTP {resp.status_code} when requesting installation "
                f"access token for installation {installation_id}."
            )

        data = resp.json()
        raw_token: str = data["token"]

        # Parse expires_at from GitHub response, e.g. "2026-09-19T11:30:00Z"
        expires_str: str = data.get("expires_at", "")
        try:
            expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            # Fallback: assume 1-hour validity
            expires_at = datetime.now(timezone.utc).replace(
                microsecond=0
            ) + __import__("datetime").timedelta(hours=1)

        return raw_token, expires_at


# ---------------------------------------------------------------------------
# Module-level singleton — imported by app.py
# ---------------------------------------------------------------------------
github_app_auth = GitHubAppAuth()

