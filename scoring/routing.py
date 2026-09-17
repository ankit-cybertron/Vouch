"""
Vouch — Routing decision and re-queue logic.

After residual risk is computed, determines:
  1. Should this PR be re-queued? (residual_risk > threshold)
  2. Who should re-review it? (CODEOWNERS lookup with ownership history)
  3. SNS notification dispatch.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REQUEUE_THRESHOLD = float(os.environ.get("REQUEUE_THRESHOLD", "0.65"))
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

_sns = boto3.client("sns")
_github = boto3.client("githubapp", region_name="us-east-1") if False else None  # placeholder


# ─────────────────────────────────────────────────────────────────────────────
# CODEOWNERS resolver
# ─────────────────────────────────────────────────────────────────────────────

def _parse_codeowners(codeowners_text: str) -> list[tuple[str, list[str]]]:
    """
    Parse a CODEOWNERS file into a list of (pattern, owners) tuples.
    """
    rules = []
    for line in codeowners_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            pattern = parts[0]
            owners = [o.lstrip("@") for o in parts[1:]]
            rules.append((pattern, owners))
    return rules


def _match_codeowners(
    file_paths: list[str],
    rules: list[tuple[str, list[str]]],
) -> list[str]:
    """Return the union of owners for all file paths, in priority order."""
    owners: set[str] = set()
    for path in file_paths:
        for pattern, rule_owners in reversed(rules):
            if _glob_match(pattern, path):
                owners.update(rule_owners)
                break
    return list(owners)


def _glob_match(pattern: str, path: str) -> bool:
    """Simple glob-style matching for CODEOWNERS patterns."""
    # Convert glob pattern to regex
    regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return bool(re.search(regex + "$", path))


def resolve_reviewer(
    file_paths: list[str],
    codeowners_text: str,
    original_reviewer: str,
    pr_author: str,
) -> str:
    """
    Determine who should re-review the PR.

    Priority:
    1. CODEOWNERS owner for the touched files (excluding original reviewer and PR author)
    2. Any CODEOWNERS owner
    3. Fall back to empty string (team lead to assign manually)
    """
    rules = _parse_codeowners(codeowners_text)
    candidates = _match_codeowners(file_paths, rules)

    # Exclude original reviewer and PR author to get a fresh perspective
    filtered = [c for c in candidates if c not in (original_reviewer, pr_author)]
    if filtered:
        return filtered[0]
    if candidates:
        return candidates[0]
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Routing decision
# ─────────────────────────────────────────────────────────────────────────────

def should_requeue(residual_risk: float) -> bool:
    return residual_risk > REQUEUE_THRESHOLD


def dispatch_requeue_notification(
    pr_key: str,
    pr_url: str,
    residual_risk: float,
    explanation: str,
    re_reviewer: str,
) -> dict:
    """Publish a re-queue notification to SNS."""
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not set — skipping notification for %s", pr_key)
        return {"dispatched": False, "reason": "no_topic_arn"}

    message = {
        "pr_key": pr_key,
        "pr_url": pr_url,
        "residual_risk": residual_risk,
        "explanation": explanation,
        "re_reviewer": re_reviewer,
        "action": "re_review_requested",
    }

    import json
    resp = _sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"Vouch: Re-review requested — {pr_key}",
        Message=json.dumps(message),
        MessageAttributes={
            "action": {"DataType": "String", "StringValue": "re_review_requested"},
        },
    )

    logger.info("SNS notification sent for %s: MessageId=%s", pr_key, resp.get("MessageId"))
    return {"dispatched": True, "message_id": resp.get("MessageId")}
