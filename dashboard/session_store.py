"""
Vouch — Server-Side Authentication Session Store.

Provides server-side credential persistence for Personal Access Tokens (PAT)
and OAuth tokens, isolating credentials from client-side browser cookies.
The browser session stores only a cryptographically random `auth_sid`.

Operational Modes:
1. LocalSessionStore (default / local dev):
   Thread-safe in-memory store with lock protection and automatic TTL cleanup.
2. DynamoDbSessionStore (production / USE_DYNAMODB=true):
   Persists credentials to AWS DynamoDB table `vouch-sessions` (or SESSIONS_TABLE).
   Includes native DynamoDB TTL attribute for automated expiration.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default token lifespan: 7 days in seconds
_DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600


class AuthSessionStore(ABC):
    """Abstract interface for server-side authentication credential store."""

    @abstractmethod
    def create_session(
        self,
        token: str,
        user_login: str = "",
        auth_type: str = "token",
        ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    ) -> str:
        """
        Store credential server-side and return a new opaque session_id.
        The raw token is never returned to the caller.
        """
        ...

    @abstractmethod
    def get_token(self, session_id: str) -> Optional[str]:
        """Retrieve the raw token for a valid, non-expired session_id."""
        ...

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """Evict the session credential on logout or token clear."""
        ...


class LocalSessionStore(AuthSessionStore):
    """
    Thread-safe in-memory credential store for development and testing.
    Automatically purges expired sessions during access.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # session_id -> {"token": str, "user_login": str, "auth_type": str, "expires_at": float}
        self._store: dict[str, dict[str, Any]] = {}

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [sid for sid, data in self._store.items() if data.get("expires_at", 0) <= now]
        for sid in expired:
            self._store.pop(sid, None)

    def create_session(
        self,
        token: str,
        user_login: str = "",
        auth_type: str = "token",
        ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    ) -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._purge_expired()
            self._store[session_id] = {
                "token": token.strip(),
                "user_login": user_login,
                "auth_type": auth_type,
                "created_at": now,
                "expires_at": now + ttl_seconds,
            }
        return session_id

    def get_token(self, session_id: str) -> Optional[str]:
        if not session_id or not isinstance(session_id, str):
            return None
        now = time.time()
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return None
            if entry.get("expires_at", 0) <= now:
                self._store.pop(session_id, None)
                return None
            return entry.get("token")

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._lock:
            self._store.pop(session_id, None)


class DynamoDbSessionStore(AuthSessionStore):
    """
    Production credential store using AWS DynamoDB.
    Uses native DynamoDB Time-To-Live (TTL) attribute for automated eviction.
    """

    def __init__(
        self,
        table_name: str = "vouch-sessions",
        region_name: str = "ap-south-1",
    ) -> None:
        import boto3

        self.region_name = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or region_name
        ).strip()
        self.table_name = (os.environ.get("SESSIONS_TABLE") or table_name).strip()

        if not self.region_name:
            raise ValueError("DynamoDbSessionStore: AWS_REGION must be configured.")
        if not self.table_name:
            raise ValueError("DynamoDbSessionStore: SESSIONS_TABLE must be configured.")

        self.dynamodb = boto3.resource("dynamodb", region_name=self.region_name)
        self.table = self.dynamodb.Table(self.table_name)

    def create_session(
        self,
        token: str,
        user_login: str = "",
        auth_type: str = "token",
        ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    ) -> str:
        session_id = secrets.token_urlsafe(32)
        now = int(time.time())
        item = {
            "session_id": session_id,
            "github_token": token.strip(),
            "user_login": user_login,
            "auth_type": auth_type,
            "created_at": now,
            "ttl": now + ttl_seconds,
        }
        try:
            self.table.put_item(Item=item)
            return session_id
        except Exception as e:
            logger.error("DynamoDbSessionStore create_session failed: %s", e)
            raise RuntimeError("Failed to securely store authentication session.") from e

    def get_token(self, session_id: str) -> Optional[str]:
        if not session_id or not isinstance(session_id, str):
            return None
        now = int(time.time())
        try:
            res = self.table.get_item(Key={"session_id": session_id})
            item = res.get("Item")
            if not item:
                return None
            if item.get("ttl", 0) <= now:
                # Expired item
                self.delete_session(session_id)
                return None
            return item.get("github_token")
        except Exception as e:
            logger.error("DynamoDbSessionStore get_token failed: %s", e)
            return None

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        try:
            self.table.delete_item(Key={"session_id": session_id})
        except Exception as e:
            logger.error("DynamoDbSessionStore delete_session failed: %s", e)


_GLOBAL_SESSION_STORE: Optional[AuthSessionStore] = None
_GLOBAL_STORE_LOCK = threading.Lock()


def get_session_store() -> AuthSessionStore:
    """Return singleton AuthSessionStore instance respecting USE_DYNAMODB."""
    global _GLOBAL_SESSION_STORE
    if _GLOBAL_SESSION_STORE is not None:
        return _GLOBAL_SESSION_STORE

    with _GLOBAL_STORE_LOCK:
        if _GLOBAL_SESSION_STORE is not None:
            return _GLOBAL_SESSION_STORE

        use_dynamodb = os.environ.get("USE_DYNAMODB", "").strip().lower() in ("true", "1", "yes")
        if use_dynamodb:
            try:
                _GLOBAL_SESSION_STORE = DynamoDbSessionStore()
                logger.info("Initialized DynamoDB server-side session store.")
                return _GLOBAL_SESSION_STORE
            except Exception as e:
                logger.warning(
                    "Failed to initialize DynamoDbSessionStore (%s). Falling back to LocalSessionStore.", e
                )
        _GLOBAL_SESSION_STORE = LocalSessionStore()
        logger.info("Initialized in-memory LocalSessionStore.")
        return _GLOBAL_SESSION_STORE
