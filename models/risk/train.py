"""
Vouch — Model 1: Change Risk XGBoost Training.

Trains an XGBoost classifier to predict P(this PR causes a defect).
Uses a strict TEMPORAL split — never a random split, which would leak
future information and produce misleadingly high metrics.

Usage:
    python train.py --prs ./data/prs.parquet \
                    --labels ./data/labels.parquet \
                    --output ./output/risk_model.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    average_precision_score,
    roc_auc_score,
    classification_report,
)
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Features used by Model 1
FEATURE_COLS = [
    # Diff size
    "lines_added", "lines_removed", "net_delta", "total_changed_lines",
    "files_touched", "hunk_count", "max_hunk_size",
    # File history (from vouch-files precompute)
    "total_churn_90d", "total_revert_count", "mean_defect_density", "mean_ownership_gini",
    # Path sensitivity flags
    "path_auth", "path_payment", "path_migration", "path_crypto",
    "path_config", "path_infra", "path_sensitive_path_count",
    # Author tenure
    "author_prior_commits_in_files",
    # AI authorship
    "ai_commit_signal", "diff_uniformity",
]

LABEL_COL = "is_defective"
DATE_COL = "merged_at"

# Train on the first 80% of PRs by merge date; test on the most recent 20%.
TRAIN_SPLIT_RATIO = 0.8


def load_data(prs_path: str, labels_path: str) -> pd.DataFrame:
    prs = pd.read_parquet(prs_path)
    labels = pd.read_parquet(labels_path)
    merged = prs.merge(labels[["repo", "pr_number", LABEL_COL]], on=["repo", "pr_number"], how="left")
    merged[LABEL_COL] = merged[LABEL_COL].fillna(0).astype(int)
    merged[DATE_COL] = pd.to_datetime(merged[DATE_COL], utc=True, errors="coerce")
    merged = merged.dropna(subset=[DATE_COL])
    return merged.sort_values(DATE_COL).reset_index(drop=True)


def temporal_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by time. All future knowledge is excluded from training."""
    split_idx = int(len(df) * TRAIN_SPLIT_RATIO)
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Select and fill feature columns."""
    features = df[FEATURE_COLS].copy()
    features = features.fillna(0)
    return features


def train(
    prs_path: str,
    labels_path: str,
    output_path: str,
    n_estimators: int = 200,
    max_depth: int = 5,
    learning_rate: float = 0.05,
) -> dict:
    df = load_data(prs_path, labels_path)
    train_df, test_df = temporal_split(df)

    X_train = build_feature_matrix(train_df)
    y_train = train_df[LABEL_COL].values
    X_test = build_feature_matrix(test_df)
    y_test = test_df[LABEL_COL].values

    positive_rate = y_train.mean()
    scale_pos_weight = (1 - positive_rate) / max(positive_rate, 0.001)

    logger.info(
        "Training on %d samples (%.1f%% positive), testing on %d",
        len(X_train), positive_rate * 100, len(X_test),
    )

    model = xgb.XGBClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        scale_pos_weight=scale_pos_weight,
        eval_metric="auc",
        use_label_encoder=False,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # Evaluate
    y_prob = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, y_prob)
    ap = average_precision_score(y_test, y_prob)
    logger.info("Test AUC: %.4f | Average Precision: %.4f", auc, ap)

    # Feature importances
    importances = dict(zip(FEATURE_COLS, model.feature_importances_.tolist()))
    top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info("Top features: %s", top_features)

    # Save model
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(out))

    # Save metadata
    meta = {
        "auc": round(auc, 4),
        "average_precision": round(ap, 4),
        "train_size": len(X_train),
        "test_size": len(X_test),
        "positive_rate_train": round(float(positive_rate), 4),
        "features": FEATURE_COLS,
        "top_features": top_features,
        "split": "temporal",
        "split_ratio": TRAIN_SPLIT_RATIO,
        "warning": "Temporal split only. Never use random split — it leaks future information.",
    }
    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2))
    logger.info("Model saved to %s | Meta: %s", out, meta_path)
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Vouch Change Risk model")
    parser.add_argument("--prs", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--output", default="./output/risk_model.json")
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--lr", type=float, default=0.05)
    args = parser.parse_args()
    train(args.prs, args.labels, args.output, args.n_estimators, args.max_depth, args.lr)
