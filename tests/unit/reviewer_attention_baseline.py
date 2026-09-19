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


class TestReviewerProfileAndFatigue:
    @patch("dashboard.app._fetch_gh_user_profile", return_value=({"login": "alice", "name": "Alice"}, False, False))
    @patch("dashboard.app._fetch_gh_user_prs", return_value=([], False))
    @patch("dashboard.app._fetch_gh_user_events", return_value=([], False))
    def test_reviewer_grade_A(self, mock_ev, mock_prs, mock_prof):
        # avg_depth >= 0.65, rubber_stamp_rate < 0.10 -> grade A
        from dashboard.app import _build_reviewer_profile
        mock_vouch_prs = [
            {"repo": "org/repo", "number": 1, "review_depth": 0.85, "change_risk": 0.3},
            {"repo": "org/repo", "number": 2, "review_depth": 0.75, "change_risk": 0.4},
            {"repo": "org/repo", "number": 3, "review_depth": 0.70, "change_risk": 0.2},
        ]
        with patch("dashboard.app.store.get_prs_by_reviewer", return_value=mock_vouch_prs):
            profile = _build_reviewer_profile("alice")
            assert profile["grade"] == "A"
            assert profile["avg_depth_score"] >= 0.65
            assert profile["rubber_stamp_rate"] < 0.10

    @patch("dashboard.app._fetch_gh_user_profile", return_value=({"login": "bob", "name": "Bob"}, False, False))
    @patch("dashboard.app._fetch_gh_user_prs", return_value=([], False))
    @patch("dashboard.app._fetch_gh_user_events", return_value=([], False))
    def test_reviewer_grade_D(self, mock_ev, mock_prs, mock_prof):
        # avg_depth < 0.25, rubber_stamp_rate >= 0.45 -> grade D
        from dashboard.app import _build_reviewer_profile
        mock_vouch_prs = [
            {"repo": "org/repo", "number": 1, "review_depth": 0.05, "change_risk": 0.7},
            {"repo": "org/repo", "number": 2, "review_depth": 0.10, "change_risk": 0.8},
            {"repo": "org/repo", "number": 3, "review_depth": 0.12, "change_risk": 0.5},
        ]
        with patch("dashboard.app.store.get_prs_by_reviewer", return_value=mock_vouch_prs):
            profile = _build_reviewer_profile("bob")
            assert profile["grade"] == "D"
            assert profile["avg_depth_score"] < 0.25
            assert profile["rubber_stamp_rate"] >= 0.45

    @patch("dashboard.app._fetch_gh_user_profile", return_value=({"login": "charlie"}, False, False))
    @patch("dashboard.app._fetch_gh_user_prs", return_value=([], False))
    @patch("dashboard.app._fetch_gh_user_events", return_value=([], False))
    def test_fatigue_state_high(self, mock_ev, mock_prs, mock_prof):
        # consecutive_today >= 8 -> state == "high"
        import time
        from dashboard.app import _build_reviewer_profile
        now = time.time()
        mock_vouch_prs = [
            {"repo": "org/repo", "number": i, "review_depth": 0.5, "scored_at": now - (i * 60)}
            for i in range(10)
        ]
        with patch("dashboard.app.store.get_prs_by_reviewer", return_value=mock_vouch_prs):
            profile = _build_reviewer_profile("charlie")
            assert profile["fatigue_state"]["state"] == "high"
            assert profile["fatigue_state"]["consecutive_reviews_today"] == 10

    @patch("dashboard.app._fetch_gh_user_profile", return_value=({"login": "dan"}, False, False))
    @patch("dashboard.app._fetch_gh_user_prs", return_value=([], False))
    @patch("dashboard.app._fetch_gh_user_events", return_value=([], False))
    def test_fatigue_cold_start(self, mock_ev, mock_prs, mock_prof):
        # < 3 reviews -> slope = 0.0, no crash
        from dashboard.app import _build_reviewer_profile
        mock_vouch_prs = [
            {"repo": "org/repo", "number": 1, "review_depth": 0.6, "scored_at": 1000},
            {"repo": "org/repo", "number": 2, "review_depth": 0.7, "scored_at": 2000},
        ]
        with patch("dashboard.app.store.get_prs_by_reviewer", return_value=mock_vouch_prs):
            profile = _build_reviewer_profile("dan")
            assert profile["fatigue_state"]["session_depth_trend"] == 0.0
            assert profile["fatigue_state"]["state"] in ("healthy", "moderate")

    @patch("dashboard.app._fetch_gh_user_profile", return_value=({"login": "eva"}, False, False))
    @patch("dashboard.app._fetch_gh_user_prs", return_value=([], False))
    @patch("dashboard.app._fetch_gh_user_events", return_value=([], False))
    def test_priority_queue_ranking(self, mock_ev, mock_prs, mock_prof):
        # PRs sorted by change_risk DESC
        from dashboard.app import _build_reviewer_profile
        mock_vouch_prs = [
            {"repo": "org/repo", "number": 1, "state": "open", "reviewer": "eva", "change_risk": 0.25},
            {"repo": "org/repo", "number": 2, "state": "open", "reviewer": "eva", "change_risk": 0.95},
            {"repo": "org/repo", "number": 3, "state": "open", "reviewer": "eva", "change_risk": 0.65},
        ]
        with patch("dashboard.app.store.get_prs_by_reviewer", return_value=mock_vouch_prs):
            profile = _build_reviewer_profile("eva")
            queue = profile["priority_queue"]
            assert len(queue) == 3
            assert queue[0]["number"] == 2
            assert queue[0]["change_risk"] == 0.95
            assert queue[1]["number"] == 3
            assert queue[1]["change_risk"] == 0.65
            assert queue[2]["number"] == 1
            assert queue[2]["change_risk"] == 0.25

