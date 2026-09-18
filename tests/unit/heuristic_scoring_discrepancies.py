"""
Tests for Vouch's enhanced heuristic scoring engine:
- Model 1 (Change Risk) discrepancy detection (rapid merge, no reviewer, self-review, WIP-not-draft, mass files)
- Model 2 (Review Depth) comment classification, rubber-stamp filters, pending change request penalty
- Model 3 (Attention Baseline) velocity, timing, off-hours, self-review penalties
- Bedrock disconnected state display contract
"""

import pytest
from dashboard.app import _score_pr_heuristic, app


class TestModel1ChangeRiskDiscrepancies:
    """Verify Model 1 detects structural, author, and PR discrepancies."""

    def test_rapid_merge_increases_risk(self):
        base_pr = {
            "number": 101,
            "additions": 400,
            "deletions": 50,
            "changed_files": 4,
            "state": "closed",
            "created_at": "2026-09-18T10:00:00Z",
            "merged_at": "2026-09-18T10:04:00Z",  # 4 minutes = rapid merge
            "_duration_secs": 240,
            "reviewer": "alice",
            "user": {"login": "bob"},
        }
        scored = _score_pr_heuristic(base_pr)
        # Verify rapid_merge is captured in top_features
        features = [f["feature"] for f in scored["top_features"]]
        assert "rapid_merge" in features
        assert scored["change_risk"] > 0.40

    def test_no_reviewer_assigned_detected(self):
        pr = {
            "number": 102,
            "additions": 200,
            "deletions": 20,
            "changed_files": 2,
            "state": "open",
            "reviewer": "",
            "user": {"login": "charlie"},
        }
        scored = _score_pr_heuristic(pr, reviews=[], comments=[])
        features = [f["feature"] for f in scored["top_features"]]
        assert "no_reviewer_assigned" in features
        assert scored["reviewer_familiarity"] == 0.10

    def test_self_review_discrepancy(self):
        pr = {
            "number": 103,
            "additions": 300,
            "deletions": 30,
            "changed_files": 3,
            "state": "closed",
            "reviewer": "dev_dan",
            "user": {"login": "dev_dan"},  # Author is reviewer
            "_duration_secs": 1200,
        }
        scored = _score_pr_heuristic(pr)
        features = [f["feature"] for f in scored["top_features"]]
        assert "self_review" in features

    def test_wip_merged_discrepancy(self):
        pr = {
            "number": 104,
            "title": "[WIP] Core refactoring before testing",
            "additions": 500,
            "deletions": 100,
            "changed_files": 8,
            "state": "closed",
            "reviewer": "eve",
            "user": {"login": "frank"},
        }
        scored = _score_pr_heuristic(pr)
        features = [f["feature"] for f in scored["top_features"]]
        assert "wip_merged" in features
        assert "WIP merged" in scored["explanation"]

    def test_mass_file_touch_discrepancy(self):
        pr = {
            "number": 105,
            "additions": 350,
            "deletions": 50,
            "changed_files": 25,  # > 15 files = mass file touch
            "state": "open",
            "reviewer": "grace",
            "user": {"login": "heidi"},
        }
        scored = _score_pr_heuristic(pr)
        features = [f["feature"] for f in scored["top_features"]]
        assert "mass_file_touch" in features


class TestModel2ReviewDepthScorer:
    """Verify Model 2 classifies comments and filters rubber stamps."""

    def test_rubber_stamp_approvals_scored_low(self):
        pr = {
            "number": 201,
            "additions": 600,
            "deletions": 50,
            "changed_files": 5,
            "state": "closed",
            "reviewer": "ivan",
            "user": {"login": "judy"},
        }
        rubber_reviews = [
            {"state": "APPROVED", "body": "LGTM 👍", "user": {"login": "ivan"}},
        ]
        scored = _score_pr_heuristic(pr, reviews=rubber_reviews, comments=[])
        assert scored["depth_score"] < 0.20
        # Comment weight should be 0.0 for rubber stamp
        assert any("rubber-stamp" in scored["explanation"] for _ in [1])

    def test_substantive_security_comment_classified(self):
        pr = {
            "number": 202,
            "additions": 200,
            "deletions": 20,
            "changed_files": 2,
            "state": "open",
            "reviewer": "mallory",
            "user": {"login": "oscar"},
        }
        comments = [
            {"body": "Potential XSS vulnerability here: user input is not escaped before render", "path": "views.py", "line": 42}
        ]
        scored = _score_pr_heuristic(pr, reviews=[], comments=comments)
        assert len(scored["comments"]) > 0
        assert scored["comments"][0]["class_name"] == "security"
        assert scored["comments"][0]["weight"] == 1.00

    def test_pending_changes_requested_penalty(self):
        pr = {
            "number": 203,
            "additions": 300,
            "deletions": 10,
            "changed_files": 3,
            "state": "closed",
            "reviewer": "peggy",
            "user": {"login": "sybil"},
        }
        reviews = [
            {"state": "CHANGES_REQUESTED", "body": "Please add unit tests", "user": {"login": "trent"}},
            {"state": "APPROVED", "body": "Looks ok to me", "user": {"login": "peggy"}},
        ]
        scored = _score_pr_heuristic(pr, reviews=reviews, comments=[])
        assert "merged with pending change requests" in scored["explanation"]


class TestModel3AttentionBaseline:
    """Verify Model 3 attention baseline penalties."""

    def test_inattentive_rapid_review_penalty(self):
        pr = {
            "number": 301,
            "additions": 1000,
            "deletions": 200,
            "changed_files": 10,
            "state": "closed",
            "_duration_secs": 30,  # 30s for 1200 lines = extreme rush
            "reviewer": "victor",
            "user": {"login": "walter"},
        }
        scored = _score_pr_heuristic(pr)
        assert scored["time_adequacy"] < 0.10
        assert scored["attention_state"] < 0.60


class TestBedrockDisconnectedState:
    """Verify Bedrock disconnected contract and UI rendering."""

    def test_bedrock_status_disconnected_by_default(self):
        pr = {
            "number": 401,
            "additions": 100,
            "deletions": 10,
            "changed_files": 2,
            "state": "open",
        }
        scored = _score_pr_heuristic(pr)
        assert scored["bedrock_status"] == "disconnected"
        assert scored["bedrock_explanation"] is None

    def test_pr_detail_template_renders_disconnected(self):
        pr_data = {
            "number": 999,
            "additions": 50,
            "deletions": 5,
            "changed_files": 1,
            "state": "open",
            "repo": "test-org/test-repo",
            "_repo": "test-org/test-repo",
        }
        scored = _score_pr_heuristic(pr_data)
        assert scored["bedrock_status"] == "disconnected"

        # Request the PR detail route
        with app.test_request_context():
            from flask import render_template
            html = render_template(
                "pr_detail.html",
                pr=scored,
                org="test-org",
                repo="test-repo",
                number=999,
            )
            # Must show (disconnected)
            assert "(disconnected)" in html
