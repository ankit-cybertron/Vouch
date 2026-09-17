"""
Vouch — Residual Risk Composition Engine.

Implements the core product equation:
    residual_risk = change_risk × (1 − review_confidence)

review_confidence is a weighted composition of:
    - depth_score:          quality of review comments (Model 2)
    - time_adequacy:        was enough time spent relative to diff size?
    - attention_state:      deviation from reviewer's own baseline (Model 3)
    - reviewer_familiarity: author's prior commits in the touched files

Weights are hand-set and explicitly flagged as such.

Also serves as the Step Functions Lambda handler for all scoring stages.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

from models.attention.baseline import compute_attention_score, update_baseline

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_dynamodb = boto3.resource("dynamodb")
_sagemaker = boto3.client("runtime.sagemaker")
_bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

PRS_TABLE = os.environ.get("PRS_TABLE", "vouch-prs-dev")
REVIEWS_TABLE = os.environ.get("REVIEWS_TABLE", "vouch-reviews-dev")
BASELINES_TABLE = os.environ.get("BASELINES_TABLE", "vouch-baselines-dev")
RISK_ENDPOINT = os.environ.get("RISK_ENDPOINT", "")
DEPTH_ENDPOINT = os.environ.get("DEPTH_ENDPOINT", "")

# ── Residual risk threshold for re-queue ─────────────────────────────────────
REQUEUE_THRESHOLD = 0.65

# ── Review confidence component weights (hand-set for hackathon) ─────────────
# Explicitly flagged as not learned from data.
CONFIDENCE_WEIGHTS = {
    "depth_score": 0.40,
    "time_adequacy": 0.25,
    "attention_state": 0.25,
    "reviewer_familiarity": 0.10,
}

# Adequate seconds per KLOC for a thoughtful review
ADEQUATE_SECONDS_PER_KLOC = 120  # 2 minutes per 1000 lines


# ─────────────────────────────────────────────────────────────────────────────
# Component computations
# ─────────────────────────────────────────────────────────────────────────────

def compute_time_adequacy(review_seconds: float, diff_kloc: float) -> float:
    """
    How well did the time spent match the diff size?
    Returns 0.0 (none) → 1.0 (adequate or better).
    """
    if diff_kloc <= 0:
        return 1.0
    actual_secs_per_kloc = review_seconds / diff_kloc
    ratio = actual_secs_per_kloc / ADEQUATE_SECONDS_PER_KLOC
    return round(min(ratio, 1.0), 4)


def compute_reviewer_familiarity(prior_commits_in_files: int) -> float:
    """
    Familiarity score based on the reviewer's prior commit history
    in the files touched by this PR.
    Returns 0.0 → 1.0, saturating at 20+ prior commits.
    """
    return round(min(prior_commits_in_files / 20.0, 1.0), 4)


def compute_review_confidence(
    depth_score: float,
    time_adequacy: float,
    attention_state: float,
    reviewer_familiarity: float,
) -> float:
    """
    Compose review_confidence ∈ [0, 1] from four components.
    Weights are hand-set (documented as such in the plan).
    """
    confidence = (
        CONFIDENCE_WEIGHTS["depth_score"] * depth_score
        + CONFIDENCE_WEIGHTS["time_adequacy"] * time_adequacy
        + CONFIDENCE_WEIGHTS["attention_state"] * attention_state
        + CONFIDENCE_WEIGHTS["reviewer_familiarity"] * reviewer_familiarity
    )
    return round(max(0.0, min(1.0, confidence)), 4)


def compute_residual_risk(change_risk: float, review_confidence: float) -> float:
    """residual_risk = change_risk × (1 − review_confidence)"""
    return round(change_risk * (1.0 - review_confidence), 4)


# ─────────────────────────────────────────────────────────────────────────────
# SageMaker calls
# ─────────────────────────────────────────────────────────────────────────────

def _call_risk_endpoint(features: dict) -> dict:
    if not RISK_ENDPOINT:
        # Return a stub if endpoint not deployed yet
        return {"change_risk": 0.5, "top_features": []}
    resp = _sagemaker.invoke_endpoint(
        EndpointName=RISK_ENDPOINT,
        ContentType="application/json",
        Body=json.dumps(features).encode(),
    )
    return json.loads(resp["Body"].read())


def _call_depth_endpoint(review_data: dict) -> dict:
    if not DEPTH_ENDPOINT:
        return {"depth_score": 0.3, "comments": []}
    resp = _sagemaker.invoke_endpoint(
        EndpointName=DEPTH_ENDPOINT,
        ContentType="application/json",
        Body=json.dumps(review_data).encode(),
    )
    return json.loads(resp["Body"].read())


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler (dispatches by stage name from Step Functions)
# ─────────────────────────────────────────────────────────────────────────────

def lambda_handler(event: dict, context: Any) -> dict:
    stage = event.get("stage")
    pr_key = event.get("pr_key")

    prs_table = _dynamodb.Table(PRS_TABLE)
    reviews_table = _dynamodb.Table(REVIEWS_TABLE)

    if stage == "depth":
        review_id = event["review_id"]
        review_item = reviews_table.get_item(
            Key={"pk": pr_key, "sk": review_id}
        ).get("Item", {})
        comments = review_item.get("comments", [])
        depth_result = _call_depth_endpoint({"review_id": review_id, "comments": comments})
        reviews_table.update_item(
            Key={"pk": pr_key, "sk": review_id},
            UpdateExpression="SET depth_score = :d, depth_detail = :dd",
            ExpressionAttributeValues={":d": str(depth_result["depth_score"]), ":dd": depth_result},
        )
        return depth_result

    elif stage == "attention":
        review_id = event["review_id"]
        depth_result = event["depth_result"]
        review_item = reviews_table.get_item(
            Key={"pk": pr_key, "sk": review_id}
        ).get("Item", {})
        reviewer = review_item.get("reviewer", "unknown")
        observation = {
            "seconds_per_kloc": float(review_item.get("seconds_per_kloc", 60)),
            "comment_density": float(review_item.get("comment_density", 0)),
            "mean_depth_score": depth_result.get("depth_score", 0),
            "consecutive_reviews": int(review_item.get("consecutive_reviews", 0)),
            "elapsed_session_minutes": float(review_item.get("elapsed_session_minutes", 0)),
            "hour_of_day": int(review_item.get("hour_of_day", 12)),
        }
        attention_result = compute_attention_score(reviewer, observation, repo_fallback={})
        update_baseline(reviewer, observation)
        return attention_result

    elif stage == "residual":
        pr_item = prs_table.get_item(Key={"pk": pr_key}).get("Item", {})
        review_id = event["review_id"]
        depth_result = event["depth_result"]
        attention_result = event["attention_result"]
        review_item = reviews_table.get_item(
            Key={"pk": pr_key, "sk": review_id}
        ).get("Item", {})

        # Risk score — either pre-computed at PR open, or call endpoint now
        change_risk = float(pr_item.get("change_risk", 0.5))
        if not pr_item.get("change_risk") and RISK_ENDPOINT:
            features = pr_item.get("features", {})
            risk_result = _call_risk_endpoint(features)
            change_risk = risk_result["change_risk"]

        depth_score = float(depth_result.get("depth_score", 0))
        review_seconds = float(review_item.get("review_duration_seconds", 60))
        diff_kloc = float(pr_item.get("features", {}).get("total_changed_lines", 100)) / 1000.0
        prior_commits = int(pr_item.get("features", {}).get("author_prior_commits_in_files", 0))

        time_adequacy = compute_time_adequacy(review_seconds, diff_kloc)
        reviewer_familiarity = compute_reviewer_familiarity(prior_commits)
        attention_state = float(attention_result.get("attention_state", 0.5))

        review_confidence = compute_review_confidence(
            depth_score, time_adequacy, attention_state, reviewer_familiarity
        )
        residual_risk = compute_residual_risk(change_risk, review_confidence)
        re_queued = residual_risk > REQUEUE_THRESHOLD

        result = {
            "change_risk": change_risk,
            "depth_score": depth_score,
            "time_adequacy": time_adequacy,
            "attention_state": attention_state,
            "reviewer_familiarity": reviewer_familiarity,
            "review_confidence": review_confidence,
            "residual_risk": residual_risk,
            "re_queued": re_queued,
            "suggested_reviewer": pr_item.get("codeowners_reviewer", ""),
            "confidence_weights": CONFIDENCE_WEIGHTS,
            "weights_note": "Hand-set for hackathon; not learned from data.",
        }

        prs_table.update_item(
            Key={"pk": pr_key},
            UpdateExpression=(
                "SET change_risk = :cr, review_confidence = :rc, "
                "residual_risk = :rr, re_queued = :rq, scored_at = :t"
            ),
            ExpressionAttributeValues={
                ":cr": str(change_risk), ":rc": str(review_confidence),
                ":rr": str(residual_risk), ":rq": re_queued,
                ":t": int(time.time()),
            },
        )
        return result

    elif stage == "record":
        residual_result = event.get("residual_result", {})
        prs_table.update_item(
            Key={"pk": pr_key},
            UpdateExpression="SET #s = :s, explanation = :e",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "re_queued" if residual_result.get("re_queued") else "closed",
                ":e": event.get("explanation", {}).get("text", ""),
            },
        )
        return {"recorded": True}

    elif stage == "failure":
        logger.error("Scoring pipeline failure for %s: %s", pr_key, event.get("error"))
        prs_table.update_item(
            Key={"pk": pr_key},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "scoring_failed"},
        )
        return {"failure_recorded": True}

    raise ValueError(f"Unknown stage: {stage}")
