"""
Tests for eval/metrics.py — Precision@K and model validation evaluation.
"""

import pandas as pd
import pytest

from eval.metrics import compute_all_metrics, precision_at_k


class TestEvaluationMetrics:
    def test_precision_at_k_calculation(self):
        # 10 PRs ranked by residual risk descending
        # 4 out of the top 5 are truly defective -> Precision@5 = 0.80
        df = pd.DataFrame({
            "residual_risk": [0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.60, 0.50, 0.40, 0.30],
            "is_defective": [1, 1, 0, 1, 1, 0, 0, 1, 0, 0],
        })
        p5 = precision_at_k(df, 5)
        assert p5 == 0.80

    def test_compute_all_metrics_structure(self):
        df = pd.DataFrame({
            "repo": ["org/repo"] * 6,
            "pr_number": [101, 102, 103, 104, 105, 106],
            "residual_risk": [0.92, 0.88, 0.78, 0.55, 0.40, 0.20],
            "is_defective": [1, 1, 0, 1, 0, 0],
            "flagged": [True, True, True, False, False, False],
            "label_source": ["revert", "hotfix", "clean", "hotfix", "clean", "clean"],
            "merged_at": ["2026-09-01"] * 6,
        })
        metrics = compute_all_metrics(df, threshold=0.65)

        assert metrics["total_prs"] == 6
        assert metrics["flagged"] == 3  # PRs with risk > 0.65 (0.92, 0.88, 0.78)
        assert metrics["actually_defective"] == 3
        assert "confusion_matrix" in metrics
        assert "precision_at_5" in metrics
        assert "precision" in metrics
        assert "recall" in metrics
        assert "top_catches" in metrics
        assert len(metrics["top_catches"]) <= 5
