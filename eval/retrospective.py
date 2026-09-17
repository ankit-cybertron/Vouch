"""
Vouch — Retrospective Validation Engine.

Batch-scores historical PRs using ONLY information available at merge
time, then reveals which were subsequently reverted or hotfixed.

This is the core demo segment: "a prediction that already came true."

Usage:
    python retrospective.py --prs ./data/prs.parquet \
                            --labels ./data/labels.parquet \
                            --model ./models/risk/output/risk_model.json \
                            --output ./output/validation_results.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Must match training feature order
FEATURE_COLS = [
    "lines_added", "lines_removed", "net_delta", "total_changed_lines",
    "files_touched", "hunk_count", "max_hunk_size",
    "total_churn_90d", "total_revert_count", "mean_defect_density", "mean_ownership_gini",
    "path_auth", "path_payment", "path_migration", "path_crypto",
    "path_config", "path_infra", "path_sensitive_path_count",
    "author_prior_commits_in_files",
    "ai_commit_signal", "diff_uniformity",
]


def load_data(prs_path: str, labels_path: str) -> pd.DataFrame:
    prs = pd.read_parquet(prs_path)
    labels = pd.read_parquet(labels_path)
    df = prs.merge(labels[["repo", "pr_number", "is_defective", "label_source"]],
                   on=["repo", "pr_number"], how="left")
    df["is_defective"] = df["is_defective"].fillna(0).astype(int)
    return df


def score_all_prs(
    df: pd.DataFrame,
    model: xgb.XGBClassifier,
    residual_threshold: float = 0.65,
) -> pd.DataFrame:
    """
    Score every PR in the dataset using only merge-time features.
    Simulates a simplified residual risk: change_risk × 0.5 (neutral confidence)
    to isolate the change-risk model's predictive power.
    """
    X = df[FEATURE_COLS].fillna(0)
    change_risks = model.predict_proba(X)[:, 1]

    # Simplified: assume neutral review_confidence (0.5) for historical PRs
    # where we don't have reviewer data. This is explicitly documented.
    neutral_confidence = 0.5
    residual_risks = change_risks * (1 - neutral_confidence)

    result = df[["repo", "pr_number", "is_defective", "label_source", "merged_at"]].copy()
    result["change_risk"] = change_risks.round(4)
    result["residual_risk"] = residual_risks.round(4)
    result["flagged"] = residual_risks > residual_threshold

    return result.sort_values("residual_risk", ascending=False).reset_index(drop=True)


def run_retrospective(
    prs_path: str,
    labels_path: str,
    model_path: str,
    output_path: str,
    residual_threshold: float = 0.65,
) -> pd.DataFrame:
    df = load_data(prs_path, labels_path)

    model = xgb.XGBClassifier()
    model.load_model(model_path)
    logger.info("Loaded risk model from %s", model_path)

    scored = score_all_prs(df, model, residual_threshold)
    logger.info(
        "Scored %d PRs: %d flagged (%.1f%%)",
        len(scored), scored["flagged"].sum(), scored["flagged"].mean() * 100,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    scored.to_parquet(out, index=False)
    logger.info("Validation results written to %s", out)
    return scored


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrospective validation")
    parser.add_argument("--prs", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", default="./output/validation_results.parquet")
    parser.add_argument("--threshold", type=float, default=0.65)
    args = parser.parse_args()
    run_retrospective(args.prs, args.labels, args.model, args.output, args.threshold)
