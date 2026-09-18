"""
Vouch — Dual-Mode Storage Adapter (Local JSON + AWS DynamoDB).

Provides a unified repository/data-access layer for repositories and pull requests.
Supports two operational modes:
1. Local JSON Mode (default): Persists to `data/repos.json` and `data/prs.json`.
   Thread-safe and atomic file updates.
2. DynamoDB Mode (production): Activated via `USE_DYNAMODB=true`.
   Persists to AWS DynamoDB tables `vouch-repos` and `vouch-prs`.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DATA_DIR = Path(__file__).parent.parent / "data"
REPOS_JSON_PATH = DATA_DIR / "repos.json"
PRS_JSON_PATH = DATA_DIR / "prs.json"


class StorageBackend(ABC):
    """Abstract interface for Vouch persistence layer."""

    @abstractmethod
    def get_repos(self) -> dict[str, dict]:
        """Return mapping of full_name -> repo_metadata."""
        ...

    @abstractmethod
    def save_repo(self, repo_meta: dict) -> None:
        """Insert or update a repository metadata entry."""
        ...

    @abstractmethod
    def get_prs(self, repo: str | None = None) -> list[dict]:
        """Return all PRs, optionally filtered by repository full_name."""
        ...

    @abstractmethod
    def get_pr(self, repo: str, pr_number: int) -> dict | None:
        """Return a single PR by repo and number."""
        ...

    @abstractmethod
    def save_pr(self, pr: dict) -> None:
        """Insert or update a scored PR record."""
        ...

    @abstractmethod
    def save_prs(self, prs: list[dict]) -> None:
        """Batch insert or update scored PR records."""
        ...


class JsonStorageBackend(StorageBackend):
    """Thread-safe local JSON persistence backend with atomic writes."""

    def __init__(self, repos_path: Path = REPOS_JSON_PATH, prs_path: Path = PRS_JSON_PATH):
        self.repos_path = repos_path
        self.prs_path = prs_path
        self.lock = threading.Lock()
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """Initialize data directory and default JSON seeds if missing."""
        self.repos_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.repos_path.exists():
            try:
                from dashboard.seeds import DEFAULT_REPOS
                self._atomic_write(self.repos_path, DEFAULT_REPOS)
            except Exception as e:
                logger.warning("Could not load default repos seeds: %s", e)
                self._atomic_write(self.repos_path, {})

        if not self.prs_path.exists():
            try:
                from dashboard.seeds import DEMO_PRS
                self._atomic_write(self.prs_path, DEMO_PRS)
            except Exception as e:
                logger.warning("Could not load default prs seeds: %s", e)
                self._atomic_write(self.prs_path, [])

    def _atomic_write(self, target_path: Path, data: Any) -> None:
        """Write JSON data to a temporary file then atomically replace target."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_name = tempfile.mkstemp(
            dir=str(target_path.parent),
            prefix=f"{target_path.name}.tmp-",
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, target_path)
        except Exception:
            if os.path.exists(temp_name):
                os.remove(temp_name)
            raise

    def get_repos(self) -> dict[str, dict]:
        with self.lock:
            if not self.repos_path.exists():
                return {}
            try:
                with open(self.repos_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Failed to read %s: %s", self.repos_path, e)
                return {}

    def save_repo(self, repo_meta: dict) -> None:
        full_name = repo_meta.get("full_name")
        if not full_name:
            return
        with self.lock:
            repos = {}
            if self.repos_path.exists():
                try:
                    with open(self.repos_path, "r", encoding="utf-8") as f:
                        repos = json.load(f)
                except Exception:
                    repos = {}
            repos[full_name] = repo_meta
            self._atomic_write(self.repos_path, repos)

    def get_prs(self, repo: str | None = None) -> list[dict]:
        with self.lock:
            if not self.prs_path.exists():
                return []
            try:
                with open(self.prs_path, "r", encoding="utf-8") as f:
                    prs = json.load(f)
            except Exception as e:
                logger.error("Failed to read %s: %s", self.prs_path, e)
                return []

        if repo:
            target_repo = repo.lower().strip()
            return [p for p in prs if str(p.get("repo", "")).lower() == target_repo]
        return prs

    def get_pr(self, repo: str, pr_number: int) -> dict | None:
        prs = self.get_prs(repo=repo)
        for p in prs:
            if int(p.get("pr_number", 0)) == int(pr_number):
                return p
        return None

    def save_pr(self, pr: dict) -> None:
        self.save_prs([pr])

    def save_prs(self, new_prs: list[dict]) -> None:
        if not new_prs:
            return
        with self.lock:
            existing = []
            if self.prs_path.exists():
                try:
                    with open(self.prs_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = []

            new_keys = set()
            clean_new_prs = []
            for p in new_prs:
                key = str(p.get("pr_key") or f"{p.get('repo')}#{p.get('pr_number')}").lower()
                if key not in new_keys:
                    new_keys.add(key)
                    clean_new_prs.append(p)

            # Prepend newly fetched PRs, keep remaining existing PRs
            remaining_existing = [
                p for p in existing
                if str(p.get("pr_key") or f"{p.get('repo')}#{p.get('pr_number')}").lower() not in new_keys
            ]
            updated_list = clean_new_prs + remaining_existing
            self._atomic_write(self.prs_path, updated_list)


class DynamoDbStorageBackend(StorageBackend):
    """AWS DynamoDB production persistence backend."""

    def __init__(
        self,
        prs_table_name: str = "vouch-prs",
        repos_table_name: str = "vouch-repos",
        region_name: str = "us-east-1",
    ):
        import boto3
        self.region_name = os.environ.get("AWS_REGION", region_name)
        self.dynamodb = boto3.resource("dynamodb", region_name=self.region_name)
        self.prs_table = self.dynamodb.Table(os.environ.get("PRS_TABLE", prs_table_name))
        self.repos_table = self.dynamodb.Table(os.environ.get("REPOS_TABLE", repos_table_name))

    def _serialize_floats(self, obj: Any) -> Any:
        """Convert float values to Decimal for DynamoDB serialization."""
        if isinstance(obj, float):
            return Decimal(str(obj))
        if isinstance(obj, dict):
            return {k: self._serialize_floats(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._serialize_floats(v) for v in obj]
        return obj

    def _deserialize_decimals(self, obj: Any) -> Any:
        """Convert Decimal values back to float/int for application runtime."""
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        if isinstance(obj, dict):
            return {k: self._deserialize_decimals(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._deserialize_decimals(v) for v in obj]
        return obj

    def get_repos(self) -> dict[str, dict]:
        try:
            res = self.repos_table.scan()
            items = res.get("Items", [])
            repos = {}
            for it in items:
                clean = self._deserialize_decimals(it)
                repos[clean["full_name"]] = clean
            return repos
        except Exception as e:
            logger.error("DynamoDB get_repos failed: %s", e)
            return {}

    def save_repo(self, repo_meta: dict) -> None:
        try:
            item = self._serialize_floats(repo_meta)
            self.repos_table.put_item(Item=item)
        except Exception as e:
            logger.error("DynamoDB save_repo failed: %s", e)

    def get_prs(self, repo: str | None = None) -> list[dict]:
        try:
            if repo:
                from boto3.dynamodb.conditions import Key
                res = self.prs_table.query(
                    IndexName="repo-index",
                    KeyConditionExpression=Key("repo").eq(repo.lower()),
                )
            else:
                res = self.prs_table.scan()
            return [self._deserialize_decimals(it) for it in res.get("Items", [])]
        except Exception as e:
            logger.error("DynamoDB get_prs failed: %s", e)
            return []

    def get_pr(self, repo: str, pr_number: int) -> dict | None:
        try:
            pr_key = f"{repo.lower()}#{pr_number}"
            res = self.prs_table.get_item(Key={"pr_key": pr_key})
            item = res.get("Item")
            return self._deserialize_decimals(item) if item else None
        except Exception as e:
            logger.error("DynamoDB get_pr failed: %s", e)
            return None

    def save_pr(self, pr: dict) -> None:
        try:
            item = self._serialize_floats(pr)
            self.prs_table.put_item(Item=item)
        except Exception as e:
            logger.error("DynamoDB save_pr failed: %s", e)

    def save_prs(self, prs: list[dict]) -> None:
        for pr in prs:
            self.save_pr(pr)


_GLOBAL_STORE: StorageBackend | None = None


def get_store() -> StorageBackend:
    """Return configured storage backend (DynamoDB if USE_DYNAMODB=true, else JSON)."""
    global _GLOBAL_STORE
    if _GLOBAL_STORE is not None:
        return _GLOBAL_STORE

    use_ddb = os.environ.get("USE_DYNAMODB", "").strip().lower() in ("true", "1", "yes")
    if use_ddb:
        logger.info("Initializing DynamoDB storage backend.")
        _GLOBAL_STORE = DynamoDbStorageBackend()
    else:
        logger.info("Initializing Local JSON storage backend.")
        _GLOBAL_STORE = JsonStorageBackend()

    return _GLOBAL_STORE
