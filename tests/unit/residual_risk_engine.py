"""
Tests for scoring/residual.py — mathematical composition engine.
"""

import pytest
from unittest.mock import MagicMock, patch

from scoring.residual import (
    ADEQUATE_SECONDS_PER_KLOC,
    CONFIDENCE_WEIGHTS,
    REQUEUE_THRESHOLD,
    compute_residual_risk,
    compute_review_confidence,
    compute_reviewer_familiarity,
    compute_time_adequacy,
    lambda_handler,
)


class TestResidualRiskFormula:
    def test_exact_formula_calculation(self):
        # residual_risk = change_risk * (1 - review_confidence)
        # e.g. change_risk = 0.8, review_confidence = 0.5 -> 0.8 * 0.5 = 0.4
        risk = compute_residual_risk(change_risk=0.8, review_confidence=0.5)
        assert risk == 0.4

    def test_rubber_stamped_review_retains_full_risk(self):
        # When review confidence is 0 (rubber-stamp), residual risk equals change risk
        risk = compute_residual_risk(change_risk=0.9, review_confidence=0.0)
        assert risk == 0.9

    def test_perfect_review_eliminates_risk(self):
        # When review confidence is 1.0, residual risk drops to 0.0
        risk = compute_residual_risk(change_risk=0.95, review_confidence=1.0)
        assert risk == 0.0

    def test_zero_change_risk_always_zero(self):
        # Even with minimal review, a trivial change with zero change risk yields 0 residual risk
        risk = compute_residual_risk(change_risk=0.0, review_confidence=0.1)
        assert risk == 0.0

    def test_high_risk_mitigated_by_thorough_review(self):
        # High change risk (0.85) with high review confidence (0.90)
        # 0.85 * (1 - 0.90) = 0.85 * 0.10 = 0.085
        risk = compute_residual_risk(change_risk=0.85, review_confidence=0.90)
        assert risk == 0.085
        assert risk < REQUEUE_THRESHOLD


class TestTimeAdequacy:
    def test_zero_or_negative_diff_size(self):
        assert compute_time_adequacy(review_seconds=10.0, diff_kloc=0.0) == 1.0
        assert compute_time_adequacy(review_seconds=10.0, diff_kloc=-1.0) == 1.0

    def test_adequate_review_time(self):
        # 1.0 KLOC needs 120 seconds for full adequacy
        adequacy = compute_time_adequacy(review_seconds=120.0, diff_kloc=1.0)
        assert adequacy == 1.0

    def test_insufficient_review_time(self):
        # 1.0 KLOC with only 30 seconds -> 30 / 120 = 0.25
        adequacy = compute_time_adequacy(review_seconds=30.0, diff_kloc=1.0)
        assert adequacy == 0.25

    def test_time_saturation(self):
        # Spending 600 seconds on 1 KLOC caps at 1.0
        adequacy = compute_time_adequacy(review_seconds=600.0, diff_kloc=1.0)
        assert adequacy == 1.0


class TestReviewerFamiliarity:
    def test_zero_commits(self):
        assert compute_reviewer_familiarity(0) == 0.0

    def test_ten_commits(self):
        # 10 / 20.0 = 0.5
        assert compute_reviewer_familiarity(10) == 0.5

    def test_saturation_at_twenty_or_more(self):
        assert compute_reviewer_familiarity(20) == 1.0
        assert compute_reviewer_familiarity(50) == 1.0


class TestReviewConfidenceComposition:
    def test_weights_sum_to_one(self):
        total_weight = sum(CONFIDENCE_WEIGHTS.values())
        assert abs(total_weight - 1.0) < 1e-6

    def test_confidence_balanced_calculation(self):
        conf = compute_review_confidence(
            depth_score=1.0,
            time_adequacy=1.0,
            attention_state=1.0,
            reviewer_familiarity=1.0,
        )
        assert conf == 1.0

    def test_confidence_all_zeros(self):
        conf = compute_review_confidence(
            depth_score=0.0,
            time_adequacy=0.0,
            attention_state=0.0,
            reviewer_familiarity=0.0,
        )
        assert conf == 0.0

    def test_confidence_weighted_values(self):
        # depth: 0.5 * 0.40 = 0.20
        # time: 0.8 * 0.25 = 0.20
        # attention: 0.6 * 0.25 = 0.15
        # familiarity: 0.5 * 0.10 = 0.05
        # Total = 0.60
        conf = compute_review_confidence(
            depth_score=0.5,
            time_adequacy=0.8,
            attention_state=0.6,
            reviewer_familiarity=0.5,
        )
        assert conf == 0.60


class TestLambdaHandlerStages:
    def test_unknown_stage_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown stage: invalid_stage"):
            lambda_handler({"stage": "invalid_stage"}, None)

    @patch("scoring.residual._dynamodb")
    def test_record_stage_success(self, mock_dynamo):
        mock_prs = MagicMock()
        mock_dynamo.Table.return_value = mock_prs

        event = {
            "stage": "record",
            "pr_key": "kubernetes/kubernetes#123",
            "residual_result": {"re_queued": True},
            "explanation": {"text": "High change risk"},
        }
        res = lambda_handler(event, None)
        assert res == {"recorded": True}
        assert mock_prs.update_item.called

    @patch("scoring.residual._dynamodb")
    def test_failure_stage_records_failure(self, mock_dynamo):
        mock_prs = MagicMock()
        mock_dynamo.Table.return_value = mock_prs

        event = {
            "stage": "failure",
            "pr_key": "kubernetes/kubernetes#123",
            "error": "SageMaker timeout",
        }
        res = lambda_handler(event, None)
        assert res == {"failure_recorded": True}
        assert mock_prs.update_item.called
