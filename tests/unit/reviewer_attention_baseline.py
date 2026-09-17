"""
Tests for models/attention/baseline.py — Model 3: Reviewer Attention Baseline.
"""

from unittest.mock import MagicMock, patch
import pytest

from models.attention.baseline import (
    MIN_REVIEWS_FOR_BASELINE,
    compute_attention_score,
    mad,
    median,
    robust_z_score,
    update_baseline,
)


class TestRobustStatistics:
    def test_median_odd_number_of_elements(self):
        assert median([3.0, 1.0, 2.0]) == 2.0

    def test_median_even_number_of_elements(self):
        assert median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_median_empty_list(self):
        assert median([]) == 0.0

    def test_mad_identical_elements(self):
        # When all values are identical, MAD should be at floor 1e-6 (no zero division)
        assert mad([5.0, 5.0, 5.0]) == 1e-6

    def test_mad_distributed_elements(self):
        # Values: [1, 2, 3, 4, 5], median is 3.
        # Absolute deviations: [2, 1, 0, 1, 2].
        # Sorted deviations: [0, 1, 1, 2, 2], median is 1.
        assert mad([1.0, 2.0, 3.0, 4.0, 5.0]) == 1.0

    def test_robust_z_score_calculation(self):
        # z = (x - median) / (1.4826 * MAD)
        # x = 5.0, med = 3.0, mad = 1.0 -> 2.0 / 1.4826 ~ 1.34898
        z = robust_z_score(5.0, 3.0, 1.0)
        assert round(z, 2) == 1.35


class TestAttentionScoring:
    @patch("models.attention.baseline._load_baseline")
    def test_cold_start_uses_repo_fallback(self, mock_load):
        # Reviewer with fewer than 10 reviews triggers cold start fallback
        mock_load.return_value = {"review_count": 3}

        repo_fallback = {
            "seconds_per_kloc_window": [120.0, 150.0, 180.0],
            "comment_density_window": [2.0, 3.0, 4.0],
            "mean_depth_score_window": [0.5, 0.6, 0.7],
        }
        current_review = {
            "seconds_per_kloc": 150.0,
            "comment_density": 3.0,
            "mean_depth_score": 0.6,
            "consecutive_reviews": 1,
            "elapsed_session_minutes": 15.0,
            "hour_of_day": 14,
        }

        res = compute_attention_score("new_reviewer", current_review, repo_fallback)
        assert res["low_confidence"] is True
        assert res["reviewer_review_count"] == 3
        assert 0.0 <= res["attention_state"] <= 1.0

    @patch("models.attention.baseline._load_baseline")
    def test_fatigue_penalty_applied(self, mock_load):
        mock_load.return_value = {
            "review_count": 25,
            "seconds_per_kloc_window": [120.0] * 20,
            "comment_density_window": [3.0] * 20,
            "mean_depth_score_window": [0.6] * 20,
        }
        # Reviewer after 8 consecutive reviews and 180 minutes in session
        current_review = {
            "seconds_per_kloc": 120.0,
            "comment_density": 3.0,
            "mean_depth_score": 0.6,
            "consecutive_reviews": 8,
            "elapsed_session_minutes": 180.0,
            "hour_of_day": 15,
        }
        res = compute_attention_score("veteran_reviewer", current_review, {})
        assert res["low_confidence"] is False
        assert res["fatigue_penalty"] > 0.0
        assert res["attention_state"] < 1.0


class TestBaselineUpdate:
    @patch("models.attention.baseline._save_baseline")
    @patch("models.attention.baseline._load_baseline")
    def test_update_baseline_increments_count(self, mock_load, mock_save):
        mock_load.return_value = {"review_count": 5}
        obs = {
            "seconds_per_kloc": 140.0,
            "comment_density": 2.5,
            "mean_depth_score": 0.75,
        }
        updated = update_baseline("alice", obs)
        assert updated["review_count"] == 6
        assert 140.0 in updated["seconds_per_kloc_window"]
        assert mock_save.called
