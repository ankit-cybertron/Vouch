"""
Vouch — Event normalisation for GitHub webhook payloads.

Converts raw GitHub webhook JSON into a consistent internal schema.
Raises ValueError for unsupported / irrelevant event types.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Supported GitHub event types
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_EVENTS = {
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
}

SUPPORTED_ACTIONS = {
    "pull_request": {"opened", "reopened", "closed", "synchronize"},
    "pull_request_review": {"submitted"},
    "pull_request_review_comment": {"created"},
}


@dataclass
class NormalisedPREvent:
    event_type: str
    action: str
    org: str
    repo: str
    pr_number: int
    pr_title: str
    pr_author: str
    pr_url: str
    base_sha: str
    head_sha: str
    additions: int
    deletions: int
    changed_files: int
    merged: bool
    merged_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NormalisedReviewEvent:
    event_type: str
    action: str
    org: str
    repo: str
    pr_number: int
    review_id: int
    reviewer: str
    review_state: str  # approved, changes_requested, commented
    submitted_at: str
    body: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NormalisedReviewCommentEvent:
    event_type: str
    action: str
    org: str
    repo: str
    pr_number: int
    review_id: int
    comment_id: int
    commenter: str
    body: str
    path: str | None
    line: int | None
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation entry point
# ─────────────────────────────────────────────────────────────────────────────

def normalise_event(event_type: str, payload: dict[str, Any]) -> dict:
    """
    Normalise a raw GitHub webhook payload.

    Returns a plain dict (serialisable) with a consistent schema.
    Raises ValueError for unsupported event types or actions.
    """
    if event_type not in SUPPORTED_EVENTS:
        raise ValueError(f"Unsupported event type: {event_type}")

    action = payload.get("action", "")
    allowed_actions = SUPPORTED_ACTIONS.get(event_type, set())
    if allowed_actions and action not in allowed_actions:
        raise ValueError(f"Ignored action '{action}' for event '{event_type}'")

    if event_type == "pull_request":
        return _normalise_pr(action, payload)
    elif event_type == "pull_request_review":
        return _normalise_review(action, payload)
    elif event_type == "pull_request_review_comment":
        return _normalise_review_comment(action, payload)

    raise ValueError(f"No normaliser for: {event_type}")


def _normalise_pr(action: str, p: dict) -> dict:
    pr = p.get("pull_request", {})
    repo = p.get("repository", {})
    org = repo.get("owner", {}).get("login", "unknown")
    return NormalisedPREvent(
        event_type="pull_request",
        action=action,
        org=org,
        repo=repo.get("name", "unknown"),
        pr_number=pr.get("number", 0),
        pr_title=pr.get("title", ""),
        pr_author=pr.get("user", {}).get("login", "unknown"),
        pr_url=pr.get("html_url", ""),
        base_sha=pr.get("base", {}).get("sha", ""),
        head_sha=pr.get("head", {}).get("sha", ""),
        additions=pr.get("additions", 0),
        deletions=pr.get("deletions", 0),
        changed_files=pr.get("changed_files", 0),
        merged=pr.get("merged", False),
        merged_at=pr.get("merged_at"),
    ).to_dict()


def _normalise_review(action: str, p: dict) -> dict:
    review = p.get("review", {})
    pr = p.get("pull_request", {})
    repo = p.get("repository", {})
    org = repo.get("owner", {}).get("login", "unknown")
    return NormalisedReviewEvent(
        event_type="pull_request_review",
        action=action,
        org=org,
        repo=repo.get("name", "unknown"),
        pr_number=pr.get("number", 0),
        review_id=review.get("id", 0),
        reviewer=review.get("user", {}).get("login", "unknown"),
        review_state=review.get("state", ""),
        submitted_at=review.get("submitted_at", ""),
        body=review.get("body"),
    ).to_dict()


def _normalise_review_comment(action: str, p: dict) -> dict:
    comment = p.get("comment", {})
    pr = p.get("pull_request", {})
    repo = p.get("repository", {})
    org = repo.get("owner", {}).get("login", "unknown")
    return NormalisedReviewCommentEvent(
        event_type="pull_request_review_comment",
        action=action,
        org=org,
        repo=repo.get("name", "unknown"),
        pr_number=pr.get("number", 0),
        review_id=comment.get("pull_request_review_id", 0),
        comment_id=comment.get("id", 0),
        commenter=comment.get("user", {}).get("login", "unknown"),
        body=comment.get("body", ""),
        path=comment.get("path"),
        line=comment.get("line"),
        created_at=comment.get("created_at", ""),
    ).to_dict()
