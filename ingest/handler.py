"""
Vouch — Ingest Lambda handler.

Receives GitHub webhook events, verifies HMAC, normalises the payload,
writes to DynamoDB and S3, and emits a PRIngested event to EventBridge.
Returns 202 immediately; never blocks on model inference.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import boto3

from events import normalise_event

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS clients (initialised outside handler for Lambda container reuse)
_dynamodb = boto3.resource("dynamodb")
_s3 = boto3.client("s3")
_eventbridge = boto3.client("events")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]
PRS_TABLE = os.environ["PRS_TABLE"]
RAW_BUCKET = os.environ["RAW_BUCKET"]
EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")


# ─────────────────────────────────────────────────────────────────────────────
# HMAC verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ─────────────────────────────────────────────────────────────────────────────
# Storage helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_event(event: dict) -> None:
    table = _dynamodb.Table(EVENTS_TABLE)
    table.put_item(Item=event)


def _write_pr_stub(pr_key: str, org: str, repo: str, pr_number: int) -> None:
    table = _dynamodb.Table(PRS_TABLE)
    table.put_item(
        Item={
            "pk": pr_key,
            "org": org,
            "repo": repo,
            "pr_number": pr_number,
            "state": "open",
            "ingested_at": int(time.time()),
        },
        ConditionExpression="attribute_not_exists(pk)",
    )


def _write_diff_to_s3(org: str, repo: str, pr_number: int, diff: str) -> None:
    key = f"raw/{org}/{repo}/{pr_number}/diff.patch"
    _s3.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=diff.encode() if diff else b"",
        ContentType="text/plain",
        ServerSideEncryption="AES256",
    )


def _emit_event(detail: dict, detail_type: str) -> None:
    _eventbridge.put_events(
        Entries=[
            {
                "Source": "vouch.ingest",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
                "EventBusName": EVENT_BUS_NAME,
            }
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ─────────────────────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context: Any) -> dict:
    """
    Entry point.  Returns 202 as quickly as possible.
    Heavy processing (feature extraction, scoring) is triggered by the
    PRIngested EventBridge event — never inline here.
    """
    headers = event.get("headers") or {}
    body_str = event.get("body") or ""
    body_bytes = body_str.encode() if isinstance(body_str, str) else body_str

    # 1. Verify GitHub webhook signature
    signature = headers.get("X-Hub-Signature-256") or headers.get("x-hub-signature-256", "")
    if GITHUB_WEBHOOK_SECRET and not _verify_signature(body_bytes, signature):
        logger.warning("Webhook signature verification failed")
        return {"statusCode": 401, "body": json.dumps({"error": "Invalid signature"})}

    # 2. Parse payload
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON payload: %s", exc)
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON"})}

    gh_event_type = headers.get("X-GitHub-Event") or headers.get("x-github-event", "unknown")
    logger.info("Received GitHub event: %s", gh_event_type)

    # 3. Normalise event
    try:
        normalised = normalise_event(gh_event_type, payload)
    except ValueError as exc:
        # Unsupported event type — acknowledge and drop
        logger.info("Unsupported event type %s: %s", gh_event_type, exc)
        return {"statusCode": 202, "body": json.dumps({"status": "ignored"})}

    org = normalised["org"]
    repo = normalised["repo"]
    pr_number = normalised["pr_number"]
    pr_key = f"{org}/{repo}#{pr_number}"
    ts = str(int(time.time() * 1000))

    # 4. Persist raw event
    _write_event(
        {
            "pk": pr_key,
            "sk": ts,
            "event_type": gh_event_type,
            "payload": normalised,
            "ttl": int(time.time()) + 90 * 24 * 3600,  # 90-day TTL
        }
    )

    # 5. Write PR stub (idempotent — condition expression prevents overwrite)
    if gh_event_type in ("pull_request",):
        try:
            _write_pr_stub(pr_key, org, repo, pr_number)
        except _dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # PR already exists — fine

        # Write diff to S3 if present in payload
        diff = payload.get("pull_request", {}).get("patch") or ""
        _write_diff_to_s3(org, repo, pr_number, diff)

    # 6. Emit to EventBridge
    _emit_event(
        detail={
            "pr_key": pr_key,
            "org": org,
            "repo": repo,
            "pr_number": pr_number,
            "event_type": gh_event_type,
            "action": normalised.get("action"),
        },
        detail_type="PRIngested",
    )

    return {"statusCode": 202, "body": json.dumps({"status": "accepted", "pr_key": pr_key})}
