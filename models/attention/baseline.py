"""
Vouch — Model 3: Reviewer Attention Baseline.

Maintains a rolling per-reviewer baseline of review behaviour and
computes a robust z-score (median + MAD, not mean + SD) to detect
deviation from the reviewer's own established patterns.

Metrics tracked per reviewer:
    - seconds_per_kloc: review duration / (diff size in KLOC)
    - comment_density: comments per 100 lines of diff
    - mean_depth_score: average depth weight of comments in the review

Session context features:
    - consecutive_reviews: number of reviews in the current session
    - elapsed_session_minutes: total time in the session so far
    - hour_of_day: local hour when review was submitted (0–23)

Cold start: fewer than MIN_REVIEWS_FOR_BASELINE prior reviews falls
back to repo-level distribution, marked as low_confidence=True.
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BASELINES_TABLE = os.environ.get("BASELINES_TABLE", "vouch-baselines-dev")
MIN_REVIEWS_FOR_BASELINE = 10
ROLLING_WINDOW = 30  # reviews

_dynamodb = boto3.resource("dynamodb")


# ─────────────────────────────────────────────────────────────────────────────
# Robust statistics helpers
# ─────────────────────────────────────────────────────────────────────────────

def median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def mad(values: list[float], med: Optional[float] = None) -> float:
    """Median Absolute Deviation — robust spread estimate."""
    if not values:
        return 1.0
    m = med if med is not None else median(values)
    deviations = [abs(v - m) for v in values]
    return max(median(deviations), 1e-6)  # floor to avoid division by zero


def robust_z_score(value: float, med: float, mad_val: float) -> float:
    """Compute robust z-score: (x - median) / (1.4826 * MAD)."""
    return (value - med) / (1.4826 * max(mad_val, 1e-6))


# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB baseline persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_baseline(reviewer: str) -> dict:
    table = _dynamodb.Table(BASELINES_TABLE)
    resp = table.get_item(Key={"pk": reviewer})
    return resp.get("Item", {})


def _save_baseline(reviewer: str, baseline: dict) -> None:
    table = _dynamodb.Table(BASELINES_TABLE)
    baseline["pk"] = reviewer
    baseline["updated_at"] = int(time.time())
    table.put_item(Item=baseline)


# ─────────────────────────────────────────────────────────────────────────────
# Baseline update
# ─────────────────────────────────────────────────────────────────────────────

def update_baseline(reviewer: str, new_observation: dict) -> dict:
    """
    Append a new review observation to the reviewer's rolling window
    and persist the updated baseline.

    observation keys:
        seconds_per_kloc, comment_density, mean_depth_score
    """
    baseline = _load_baseline(reviewer)

    for metric in ("seconds_per_kloc", "comment_density", "mean_depth_score"):
        key = f"{metric}_window"
        window: list[float] = baseline.get(key, [])
        window.append(float(new_observation.get(metric, 0)))
        # Keep only the last ROLLING_WINDOW reviews
        baseline[key] = window[-ROLLING_WINDOW:]

    baseline["review_count"] = baseline.get("review_count", 0) + 1
    _save_baseline(reviewer, baseline)
    return baseline


# ─────────────────────────────────────────────────────────────────────────────
# Attention score computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_attention_score(
    reviewer: str,
    current_review: dict,
    repo_fallback: dict,
) -> dict:
    """
    Compute attention_state ∈ [0, 1] for the current review.

    1 = reviewer is performing at or above their normal baseline.
    0 = severe deviation (rushing, distracted, late-session fatigue).

    Returns a dict with:
        attention_state: float
        low_confidence: bool (True if using repo fallback)
        z_scores: dict[metric, float]
    """
    baseline = _load_baseline(reviewer)
    review_count = baseline.get("review_count", 0)
    low_confidence = review_count < MIN_REVIEWS_FOR_BASELINE

    if low_confidence:
        # Fall back to repo-level distribution
        reference = repo_fallback
        logger.info(
            "Cold start for reviewer %s (%d reviews). Using repo fallback.",
            reviewer, review_count,
        )
    else:
        reference = baseline

    z_scores = {}
    for metric in ("seconds_per_kloc", "comment_density", "mean_depth_score"):
        window: list[float] = reference.get(f"{metric}_window", [])
        if not window:
            z_scores[metric] = 0.0
            continue
        m = median(window)
        m_val = mad(window, m)
        z = robust_z_score(float(current_review.get(metric, 0)), m, m_val)
        z_scores[metric] = round(z, 4)

    # Session context modifiers
    consecutive = int(current_review.get("consecutive_reviews", 0))
    elapsed_minutes = float(current_review.get("elapsed_session_minutes", 0))
    hour = int(current_review.get("hour_of_day", 12))

    # Penalise late-session reviews (fatigue signal)
    fatigue_penalty = min(consecutive * 0.03 + elapsed_minutes * 0.001, 0.3)
    # Small penalty for late-night or early-morning reviews
    off_hours_penalty = 0.05 if (hour < 7 or hour > 22) else 0.0

    # Composite: penalise negative z-scores on key metrics
    # seconds_per_kloc negative z = reviewed too fast
    # comment_density negative z = left fewer comments than usual
    # mean_depth_score negative z = shallower comments than usual
    raw_score = 1.0
    for metric, z in z_scores.items():
        if z < -1.5:
            raw_score -= 0.15 * abs(z + 1.5)  # escalating penalty

    attention_state = max(0.0, min(1.0, raw_score - fatigue_penalty - off_hours_penalty))

    return {
        "attention_state": round(attention_state, 4),
        "low_confidence": low_confidence,
        "z_scores": z_scores,
        "fatigue_penalty": round(fatigue_penalty, 4),
        "off_hours_penalty": round(off_hours_penalty, 4),
        "reviewer_review_count": review_count,
    }
