"""
Vouch — Model 3: Reviewer Attention Isolation Forest.

Layers a multivariate anomaly detector on top of the robust z-score
baseline to catch joint deviations that no single metric reveals.

For example: median speed AND median comment density are each within
normal range, but the combination hasn't been seen before — Isolation
Forest flags it whereas individual z-scores would not.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest

logger = logging.getLogger(__name__)

# Features fed to Isolation Forest
IF_FEATURES = [
    "seconds_per_kloc",
    "comment_density",
    "mean_depth_score",
    "consecutive_reviews",
    "elapsed_session_minutes",
    "hour_of_day",
]

# Contamination: fraction of reviews we expect to be anomalous
DEFAULT_CONTAMINATION = 0.05


def build_isolation_forest(
    training_observations: list[dict],
    contamination: float = DEFAULT_CONTAMINATION,
    random_state: int = 42,
) -> IsolationForest:
    """
    Train an Isolation Forest on a reviewer's historical observations.

    Each observation is a dict with the IF_FEATURES keys.
    Returns the fitted model.
    """
    X = _observations_to_matrix(training_observations)
    model = IsolationForest(
        contamination=contamination,
        random_state=random_state,
        n_estimators=100,
        n_jobs=-1,
    )
    model.fit(X)
    return model


def _observations_to_matrix(observations: list[dict]) -> np.ndarray:
    rows = []
    for obs in observations:
        rows.append([float(obs.get(f, 0)) for f in IF_FEATURES])
    return np.array(rows, dtype=float)


def score_observation(
    model: IsolationForest,
    observation: dict,
) -> float:
    """
    Score a single observation against the trained Isolation Forest.

    Returns an anomaly score in [0, 1] where:
        0 = highly anomalous (likely inattentive review)
        1 = normal behaviour
    """
    X = _observations_to_matrix([observation])
    # score_samples returns negative values — more negative = more anomalous
    raw = model.score_samples(X)[0]
    # Normalise to [0, 1]: typical range is approximately [-0.5, 0.0]
    normalised = float(np.clip((raw + 0.5) / 0.5, 0.0, 1.0))
    return round(normalised, 4)


def combined_attention_score(
    z_score_attention: float,
    if_score: float,
    z_weight: float = 0.6,
    if_weight: float = 0.4,
) -> float:
    """
    Combine the robust z-score attention and Isolation Forest scores
    into a single attention_state value.
    """
    combined = z_weight * z_score_attention + if_weight * if_score
    return round(max(0.0, min(1.0, combined)), 4)
