"""
Tests for scoring/routing.py — CODEOWNERS routing and re-queue decisions.
"""

from unittest.mock import MagicMock, patch
import pytest

from scoring.routing import (
    REQUEUE_THRESHOLD,
    _glob_match,
    _parse_codeowners,
    dispatch_requeue_notification,
    resolve_reviewer,
    should_requeue,
)


class TestCodeownersParsing:
    def test_parse_rules_with_comments_and_empty_lines(self, sample_codeowners):
        rules = _parse_codeowners(sample_codeowners)
        assert len(rules) == 3

        patterns = [r[0] for r in rules]
        assert "*" in patterns
        assert "auth/**" in patterns
        assert "db/schema.sql" in patterns

        # Check stripped '@' prefixes
        auth_rule = next(r for r in rules if r[0] == "auth/**")
        assert auth_rule[1] == ["security-guru", "lead-dev"]

    def test_empty_codeowners_text(self):
        assert _parse_codeowners("") == []
        assert _parse_codeowners("# Just a comment\n\n") == []


class TestGlobMatching:
    def test_wildcard_extension_match(self):
        assert _glob_match("*.py", "main.py")
        assert _glob_match("*.py", "app.py")
        assert not _glob_match("*.py", "main.go")

    def test_recursive_directory_glob(self):
        assert _glob_match("auth/**", "auth/jwt.py")
        assert _glob_match("auth/**", "auth/providers/oauth.py")
        assert not _glob_match("auth/**", "billing/invoice.py")

    def test_exact_path_match(self):
        assert _glob_match("db/schema.sql", "db/schema.sql")
        assert not _glob_match("db/schema.sql", "db/schema.py")


class TestReviewerResolution:
    def test_resolve_excludes_author_and_original_reviewer(self, sample_codeowners):
        # auth/jwt.py matches auth/** owned by security-guru, lead-dev
        # If original_reviewer is security-guru, lead-dev should be selected
        reviewer = resolve_reviewer(
            file_paths=["auth/jwt.py"],
            codeowners_text=sample_codeowners,
            original_reviewer="security-guru",
            pr_author="junior-dev",
        )
        assert reviewer == "lead-dev"

    def test_resolve_fallback_when_all_specific_owners_excluded(self, sample_codeowners):
        # When all specific owners are in filtered out list, falls back to candidates[0]
        reviewer = resolve_reviewer(
            file_paths=["auth/jwt.py"],
            codeowners_text=sample_codeowners,
            original_reviewer="security-guru",
            pr_author="lead-dev",
        )
        assert reviewer in ["security-guru", "lead-dev"]

    def test_resolve_global_fallback(self, sample_codeowners):
        # File matching global rule * picks un-excluded global owner
        reviewer = resolve_reviewer(
            file_paths=["docs/overview.md"],
            codeowners_text=sample_codeowners,
            original_reviewer="global-lead",
            pr_author="dev",
        )
        assert reviewer == "core-team"

    def test_empty_candidates_returns_empty_string(self):
        reviewer = resolve_reviewer(
            file_paths=["unknown/path.txt"],
            codeowners_text="",
            original_reviewer="alice",
            pr_author="bob",
        )
        assert reviewer == ""


class TestRequeueDecision:
    def test_should_requeue_above_threshold(self):
        assert should_requeue(0.66) is True
        assert should_requeue(0.95) is True

    def test_should_not_requeue_at_or_below_threshold(self):
        assert should_requeue(REQUEUE_THRESHOLD) is False
        assert should_requeue(0.64) is False
        assert should_requeue(0.10) is False


class TestRequeueNotification:
    def test_dispatch_skipped_when_no_topic_arn(self):
        with patch("scoring.routing.SNS_TOPIC_ARN", ""):
            res = dispatch_requeue_notification(
                pr_key="org/repo#1",
                pr_url="https://github.com/org/repo/pull/1",
                residual_risk=0.85,
                explanation="Rubber stamped",
                re_reviewer="security-guru",
            )
            assert res == {"dispatched": False, "reason": "no_topic_arn"}

    @patch("scoring.routing._sns")
    def test_dispatch_publishes_sns_message(self, mock_sns):
        mock_sns.publish.return_value = {"MessageId": "msg-9999"}
        with patch("scoring.routing.SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:12345:vouch"):
            res = dispatch_requeue_notification(
                pr_key="org/repo#42",
                pr_url="https://github.com/org/repo/pull/42",
                residual_risk=0.78,
                explanation="Critical defect risk",
                re_reviewer="alice",
            )
            assert res == {"dispatched": True, "message_id": "msg-9999"}
            assert mock_sns.publish.called
