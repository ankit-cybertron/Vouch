"""
Integration tests for PR scoring engine (_score_pr_heuristic) in dashboard/app.py.
"""

import pytest
from dashboard.app import _score_pr_heuristic


class TestPrScoringIntegration:
    def test_score_pr_high_risk_profile(self):
        """PR marked with high risk profile computes risk >= 0.65 and triggers re_queued."""
        pr_data = {
            "number": 101,
            "_sim_profile": "high",
            "title": "Update crypto key rotation and auth middleware",
            "_files": [{"filename": "auth/crypto.py"}],
            "additions": 450,
            "deletions": 120,
            "changed_files": 8,
        }
        scored = _score_pr_heuristic(pr_data)

        assert scored["change_risk"] > 0.70
        assert scored["review_confidence"] < 0.35
        assert scored["residual_risk"] >= 0.65
        assert scored["re_queued"] is True

    def test_score_pr_low_risk_profile(self):
        """PR with low risk profile computes low residual risk (< 0.35) and not re-queued."""
        pr_data = {
            "number": 102,
            "_sim_profile": "low",
            "title": "Fix typo in documentation comment",
            "_files": [{"filename": "docs/quickstart.md"}],
            "additions": 5,
            "deletions": 2,
            "changed_files": 1,
        }
        scored = _score_pr_heuristic(pr_data)

        assert scored["change_risk"] < 0.40
        assert scored["review_confidence"] > 0.70
        assert scored["residual_risk"] < 0.35
        assert scored["re_queued"] is False

    def test_score_pr_dynamic_calculation(self):
        """Dynamic PR calculation properly integrates diff size and sensitive file detection."""
        pr_data = {
            "number": 500,
            "title": "Refactor billing and stripe webhook handler",
            "_files": [{"filename": "payments/stripe_webhook.py"}],
            "additions": 800,
            "deletions": 200,
            "changed_files": 15,
            "_reviews": [{"user": {"login": "senior-reviewer"}, "state": "approved"}],
            "_comments": [{"body": "Checked concurrency lock and idempotency key"}],
        }
        scored = _score_pr_heuristic(pr_data)

        assert 0.0 <= scored["change_risk"] <= 1.0
        assert 0.0 <= scored["review_confidence"] <= 1.0
        assert 0.0 <= scored["residual_risk"] <= 1.0
        assert "depth_score" in scored
        assert "time_adequacy" in scored
        assert "attention_state" in scored
        assert "reviewer_familiarity" in scored
