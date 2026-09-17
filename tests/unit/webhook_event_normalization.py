"""
Tests for ingest/events.py — GitHub webhook event normalization.
"""

import pytest

from ingest.events import normalise_event


class TestEventNormalization:
    def test_unsupported_event_type_raises_error(self):
        with pytest.raises(ValueError, match="Unsupported event type"):
            normalise_event("star", {"action": "created"})

    def test_unsupported_action_raises_error(self):
        with pytest.raises(ValueError, match="Ignored action 'labeled'"):
            normalise_event("pull_request", {"action": "labeled"})

    def test_normalise_pull_request_opened(self):
        payload = {
            "action": "opened",
            "repository": {
                "name": "kubernetes",
                "owner": {"login": "kubernetes"},
            },
            "pull_request": {
                "number": 1042,
                "title": "Fix apiserver deadlock",
                "html_url": "https://github.com/kubernetes/kubernetes/pull/1042",
                "user": {"login": "k8s-maintainer"},
                "base": {"sha": "base123"},
                "head": {"sha": "head456"},
                "additions": 45,
                "deletions": 12,
                "changed_files": 3,
                "merged": False,
                "merged_at": None,
            },
        }
        res = normalise_event("pull_request", payload)
        assert res["event_type"] == "pull_request"
        assert res["action"] == "opened"
        assert res["org"] == "kubernetes"
        assert res["repo"] == "kubernetes"
        assert res["pr_number"] == 1042
        assert res["pr_author"] == "k8s-maintainer"
        assert res["additions"] == 45
        assert res["deletions"] == 12

    def test_normalise_pull_request_review_submitted(self):
        payload = {
            "action": "submitted",
            "repository": {
                "name": "react",
                "owner": {"login": "facebook"},
            },
            "pull_request": {"number": 2500},
            "review": {
                "id": 8888,
                "user": {"login": "gaearon"},
                "state": "approved",
                "submitted_at": "2026-09-17T12:00:00Z",
                "body": "Looks good to me!",
            },
        }
        res = normalise_event("pull_request_review", payload)
        assert res["event_type"] == "pull_request_review"
        assert res["action"] == "submitted"
        assert res["reviewer"] == "gaearon"
        assert res["review_state"] == "approved"
        assert res["body"] == "Looks good to me!"

    def test_normalise_review_comment_created(self):
        payload = {
            "action": "created",
            "repository": {
                "name": "flask",
                "owner": {"login": "pallets"},
            },
            "pull_request": {"number": 4900},
            "comment": {
                "id": 777,
                "pull_request_review_id": 999,
                "user": {"login": "davidism"},
                "body": "Should this raise 404 instead of 500?",
                "path": "src/flask/app.py",
                "line": 150,
                "created_at": "2026-09-17T13:00:00Z",
            },
        }
        res = normalise_event("pull_request_review_comment", payload)
        assert res["event_type"] == "pull_request_review_comment"
        assert res["commenter"] == "davidism"
        assert res["line"] == 150
        assert res["path"] == "src/flask/app.py"
