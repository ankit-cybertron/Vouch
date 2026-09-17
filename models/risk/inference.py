"""
Vouch — Model 1: Change Risk SageMaker Inference Handler.

Serves the trained XGBoost risk model as a SageMaker endpoint.
Returns change_risk ∈ [0,1] plus the top-3 contributing features.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Must match training feature order exactly
FEATURE_COLS = [
    "lines_added", "lines_removed", "net_delta", "total_changed_lines",
    "files_touched", "hunk_count", "max_hunk_size",
    "total_churn_90d", "total_revert_count", "mean_defect_density", "mean_ownership_gini",
    "path_auth", "path_payment", "path_migration", "path_crypto",
    "path_config", "path_infra", "path_sensitive_path_count",
    "author_prior_commits_in_files",
    "ai_commit_signal", "diff_uniformity",
]

_model: xgb.XGBClassifier | None = None
_feature_importances: dict[str, float] = {}


def model_fn(model_dir: str) -> xgb.XGBClassifier:
    """Load model from SageMaker model directory."""
    global _model, _feature_importances
    model_path = Path(model_dir) / "risk_model.json"
    _model = xgb.XGBClassifier()
    _model.load_model(str(model_path))
    _feature_importances = dict(zip(FEATURE_COLS, _model.feature_importances_.tolist()))
    logger.info("Risk model loaded from %s", model_path)
    return _model


def input_fn(request_body: str, content_type: str = "application/json") -> dict:
    """Deserialise the request payload."""
    if content_type != "application/json":
        raise ValueError(f"Unsupported content type: {content_type}")
    return json.loads(request_body)


def predict_fn(features: dict, model: xgb.XGBClassifier) -> dict:
    """Run inference. Returns risk score and top-3 contributing features."""
    # Build feature vector in the correct order
    vec = [[features.get(col, 0) for col in FEATURE_COLS]]
    prob = float(model.predict_proba(vec)[0][1])

    # Approximate per-feature contribution using importance × feature_value
    contributions = {
        col: _feature_importances.get(col, 0) * abs(float(features.get(col, 0)))
        for col in FEATURE_COLS
    }
    top3 = sorted(contributions.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "change_risk": round(prob, 4),
        "top_features": [{"feature": k, "contribution": round(v, 4)} for k, v in top3],
    }


def output_fn(prediction: dict, accept: str = "application/json") -> tuple[str, str]:
    """Serialise the prediction to JSON."""
    return json.dumps(prediction), "application/json"


# ─────────────────────────────────────────────────────────────────────────────
# Local testing entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    model_dir = sys.argv[1] if len(sys.argv) > 1 else "./output"
    model = model_fn(model_dir)
    sample = {col: 0 for col in FEATURE_COLS}
    sample.update({"lines_added": 300, "total_revert_count": 5, "path_auth": 1})
    result = predict_fn(sample, model)
    print(json.dumps(result, indent=2))
