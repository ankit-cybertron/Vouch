"""
Vouch — Precision@K and evaluation metrics.

Reports Precision@K for K = 5, 10, 20 on the flagged PR ranking.
Also produces a confusion matrix and summary stats for the demo video.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    roc_auc_score,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def precision_at_k(ranked_df: pd.DataFrame, k: int) -> float:
    """
    Of the top-K ranked PRs (by residual_risk descending),
    what fraction were actually defective?
    """
    top_k = ranked_df.head(k)
    return round(top_k["is_defective"].mean(), 4)


def compute_all_metrics(
    ranked_df: pd.DataFrame,
    threshold: float = 0.65,
) -> dict:
    """
    Compute a full suite of evaluation metrics on the validation results.

    ranked_df must have columns: residual_risk, is_defective (sorted by risk desc).
    """
    y_true = ranked_df["is_defective"].values
    y_score = ranked_df["residual_risk"].values
    y_pred = (y_score > threshold).astype(int)

    metrics = {
        "total_prs": len(ranked_df),
        "flagged": int(y_pred.sum()),
        "actually_defective": int(y_true.sum()),
        "positive_rate": round(float(y_true.mean()), 4),
        "precision_at_5": precision_at_k(ranked_df, 5),
        "precision_at_10": precision_at_k(ranked_df, 10),
        "precision_at_20": precision_at_k(ranked_df, 20),
    }

    if y_true.sum() > 0:
        try:
            metrics["auc_roc"] = round(roc_auc_score(y_true, y_score), 4)
            metrics["average_precision"] = round(average_precision_score(y_true, y_score), 4)
        except Exception:
            metrics["auc_roc"] = None
            metrics["average_precision"] = None

        tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
        metrics["confusion_matrix"] = {
            "true_positive": int(tp),
            "false_positive": int(fp),
            "true_negative": int(tn),
            "false_negative": int(fn),
        }
        if (tp + fp) > 0:
            metrics["precision"] = round(tp / (tp + fp), 4)
        if (tp + fn) > 0:
            metrics["recall"] = round(tp / (tp + fn), 4)

    # Highlight the most striking individual catches for the demo
    catches = ranked_df[
        (ranked_df["is_defective"] == 1) & (ranked_df["flagged"])
    ].head(5)[["repo", "pr_number", "residual_risk", "label_source", "merged_at"]]
    metrics["top_catches"] = catches.to_dict(orient="records")

    return metrics


def print_summary(metrics: dict) -> None:
    """Pretty-print metrics for the terminal and demo video."""
    print("\n" + "=" * 60)
    print("  VOUCH — RETROSPECTIVE VALIDATION RESULTS")
    print("=" * 60)
    print(f"  Total PRs scored:    {metrics['total_prs']}")
    print(f"  Flagged:             {metrics['flagged']}")
    print(f"  Actually defective:  {metrics['actually_defective']}")
    print(f"  Positive rate:       {metrics['positive_rate']*100:.1f}%")
    print()
    print(f"  Precision@5:   {metrics['precision_at_5']:.0%}")
    print(f"  Precision@10:  {metrics['precision_at_10']:.0%}")
    print(f"  Precision@20:  {metrics['precision_at_20']:.0%}")
    if "auc_roc" in metrics and metrics["auc_roc"]:
        print(f"  AUC-ROC:       {metrics['auc_roc']:.4f}")
    print()
    print("  Top catches (flagged & truly defective):")
    for catch in metrics.get("top_catches", []):
        print(f"    PR #{catch['pr_number']} — risk {catch['residual_risk']:.2f} — {catch['label_source']}")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    parquet_path = sys.argv[1] if len(sys.argv) > 1 else "./output/validation_results.parquet"
    df = pd.read_parquet(parquet_path).sort_values("residual_risk", ascending=False)
    metrics = compute_all_metrics(df)
    print_summary(metrics)
    out = Path(parquet_path).with_suffix(".metrics.json")
    out.write_text(json.dumps(metrics, indent=2))
    print(f"\nMetrics written to {out}")
