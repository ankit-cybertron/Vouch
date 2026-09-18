"""
Unit tests for Vouch Dual-Mode Storage Adapter (dashboard/store.py).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from dashboard.store import (
    DynamoDbStorageBackend,
    JsonStorageBackend,
    get_store,
)


class TestJsonStorageBackend:
    @pytest.fixture
    def temp_store(self, tmp_path):
        repos_file = tmp_path / "repos.json"
        prs_file = tmp_path / "prs.json"
        return JsonStorageBackend(repos_path=repos_file, prs_path=prs_file)

    def test_initialization_creates_files(self, temp_store):
        """Storage backend automatically initializes seed files if missing."""
        assert temp_store.repos_path.exists()
        assert temp_store.prs_path.exists()

        repos = temp_store.get_repos()
        assert isinstance(repos, dict)
        assert len(repos) > 0

    def test_save_and_get_repo(self, temp_store):
        """Saving a new repository persists it to JSON file."""
        new_repo = {
            "full_name": "acme/corp",
            "owner": "acme",
            "repo": "corp",
            "stars": "42",
            "language": "Python",
        }
        temp_store.save_repo(new_repo)

        repos = temp_store.get_repos()
        assert "acme/corp" in repos
        assert repos["acme/corp"]["stars"] == "42"

        # Verify disk persistence
        with open(temp_store.repos_path, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert "acme/corp" in disk_data

    def test_save_and_get_prs(self, temp_store):
        """Saving PRs persists them and supports filtering by repo."""
        pr1 = {
            "pr_key": "acme/corp#1",
            "repo": "acme/corp",
            "pr_number": 1,
            "title": "Fix memory leak in payment gateway",
            "residual_risk": 0.72,
        }
        pr2 = {
            "pr_key": "other/tool#2",
            "repo": "other/tool",
            "pr_number": 2,
            "title": "Refactor logger",
            "residual_risk": 0.25,
        }
        temp_store.save_prs([pr1, pr2])

        all_prs = temp_store.get_prs()
        assert len(all_prs) >= 2

        filtered = temp_store.get_prs(repo="acme/corp")
        assert any(p["pr_key"] == "acme/corp#1" for p in filtered)
        assert not any(p["pr_key"] == "other/tool#2" for p in filtered)

        single = temp_store.get_pr(repo="acme/corp", pr_number=1)
        assert single is not None
        assert single["title"] == "Fix memory leak in payment gateway"

    def test_atomic_overwrite_preserves_updates(self, temp_store):
        """Updating an existing PR updates the record in-place."""
        pr = {
            "pr_key": "test/repo#100",
            "repo": "test/repo",
            "pr_number": 100,
            "title": "Initial Title",
            "residual_risk": 0.50,
        }
        temp_store.save_pr(pr)

        pr_updated = dict(pr, title="Updated Title", residual_risk=0.65)
        temp_store.save_pr(pr_updated)

        fetched = temp_store.get_pr(repo="test/repo", pr_number=100)
        assert fetched is not None
        assert fetched["title"] == "Updated Title"
        assert fetched["residual_risk"] == 0.65


class TestDynamoDbStorageSerialization:
    def test_float_to_decimal_conversion(self):
        """DynamoDbStorageBackend converts floats to Decimals recursively."""
        backend = DynamoDbStorageBackend.__new__(DynamoDbStorageBackend)
        input_data = {
            "name": "pr",
            "risk": 0.81,
            "counts": [1, 2.5],
            "nested": {"score": 0.45},
        }
        serialized = backend._serialize_floats(input_data)
        assert isinstance(serialized["risk"], Decimal)
        assert isinstance(serialized["counts"][1], Decimal)
        assert isinstance(serialized["nested"]["score"], Decimal)

    def test_decimal_to_float_conversion(self):
        """DynamoDbStorageBackend converts Decimals back to float/int."""
        backend = DynamoDbStorageBackend.__new__(DynamoDbStorageBackend)
        input_data = {
            "risk": Decimal("0.81"),
            "count": Decimal("5"),
            "nested": {"score": Decimal("0.45")},
        }
        deserialized = backend._deserialize_decimals(input_data)
        assert deserialized["risk"] == 0.81
        assert deserialized["count"] == 5
        assert isinstance(deserialized["count"], int)
        assert deserialized["nested"]["score"] == 0.45


class TestGetStoreFactory:
    def test_get_store_returns_json_by_default(self, monkeypatch):
        """Without USE_DYNAMODB, get_store returns JsonStorageBackend."""
        monkeypatch.delenv("USE_DYNAMODB", raising=False)
        # Reset singleton
        import dashboard.store as store_mod
        store_mod._GLOBAL_STORE = None

        st = get_store()
        assert isinstance(st, JsonStorageBackend)


class TestAppStoreIntegration:
    def test_new_repo_and_prs_added_to_store_on_fetch(self, tmp_path, monkeypatch):
        """Whenever new data is fetched, it is automatically added to the JSON store."""
        test_repos_file = tmp_path / "repos.json"
        test_prs_file = tmp_path / "prs.json"

        backend = JsonStorageBackend(repos_path=test_repos_file, prs_path=test_prs_file)
        initial_repo_count = len(backend.get_repos())
        initial_pr_count = len(backend.get_prs())

        # Simulate newly fetched repo
        new_repo_name = "org/new-microservice"
        new_repo_meta = {
            "full_name": new_repo_name,
            "owner": "org",
            "repo": "new-microservice",
            "description": "Newly fetched service",
            "active_prs_count": 2,
            "closed_prs_count": 10,
        }
        backend.save_repo(new_repo_meta)

        # Simulate newly fetched PRs
        new_prs = [
            {
                "pr_key": f"{new_repo_name}#101",
                "repo": new_repo_name,
                "pr_number": 101,
                "title": "Add OAuth2 authentication endpoint",
                "residual_risk": 0.58,
                "state": "open",
            },
            {
                "pr_key": f"{new_repo_name}#102",
                "repo": new_repo_name,
                "pr_number": 102,
                "title": "Fix SQL injection vulnerability",
                "residual_risk": 0.88,
                "state": "closed",
            },
        ]
        backend.save_prs(new_prs)

        # Verify added to repos
        repos_now = backend.get_repos()
        assert len(repos_now) == initial_repo_count + 1
        assert new_repo_name in repos_now
        assert repos_now[new_repo_name]["description"] == "Newly fetched service"

        # Verify added to PRs
        prs_now = backend.get_prs()
        assert len(prs_now) == initial_pr_count + 2
        assert any(p["pr_key"] == f"{new_repo_name}#101" for p in prs_now)
        assert any(p["pr_key"] == f"{new_repo_name}#102" for p in prs_now)

        # Verify newly fetched PRs are placed at the beginning
        assert prs_now[0]["pr_key"] in (f"{new_repo_name}#101", f"{new_repo_name}#102")

        # Verify reading filtered by repo returns exactly the 2 new PRs
        repo_prs = backend.get_prs(repo=new_repo_name)
        assert len(repo_prs) == 2

