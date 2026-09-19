"""
Unit tests for on-demand Groq explanation fallback client and API endpoint.
"""

from unittest.mock import MagicMock, patch
import pytest

from dashboard.app import app
from explain.groq_client import (
    generate_groq_explanation,
    resolve_groq_api_key,
    is_groq_configured,
)


class TestGroqClient:
    def test_resolve_key_explicit(self):
        assert resolve_groq_api_key("gsk_test_123") == "gsk_test_123"

    def test_missing_key_returns_clear_error(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            res = generate_groq_explanation(
                pr_key="test/repo#1",
                change_risk=0.7,
                review_confidence=0.2,
                residual_risk=0.56,
                top_risk_features=[{"feature": "sensitive_paths", "contribution": 0.3}],
                depth_score=0.2,
                attention_state=0.5,
                review_duration_seconds=90,
                diff_lines=300,
                reviewer="alice",
                consecutive_reviews=3,
                api_key="",
            )
            assert res["success"] is False
            assert "GROQ_API_KEY is not configured" in res["error"]

    @patch("explain.groq_client.requests.post")
    def test_successful_groq_generation(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Approved in 90s on a 300-line diff in auth/login.py by reviewer 3 reviews into session; recommend second look."
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        res = generate_groq_explanation(
            pr_key="test/repo#1",
            change_risk=0.7,
            review_confidence=0.2,
            residual_risk=0.56,
            top_risk_features=[{"feature": "sensitive_paths", "contribution": 0.3}],
            depth_score=0.2,
            attention_state=0.5,
            review_duration_seconds=90,
            diff_lines=300,
            reviewer="alice",
            consecutive_reviews=3,
            api_key="gsk_valid_key_xyz",
        )

        assert res["success"] is True
        assert "Approved in 90s" in res["explanation"]
        assert "Groq" in res["provider"]
        assert res["error"] is None

    @patch("explain.groq_client.requests.post")
    def test_unauthorized_groq_key(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"error": {"message": "Invalid API Key"}}'
        mock_post.return_value = mock_resp

        res = generate_groq_explanation(
            pr_key="test/repo#1",
            change_risk=0.7,
            review_confidence=0.2,
            residual_risk=0.56,
            top_risk_features=[],
            depth_score=0.2,
            attention_state=0.5,
            review_duration_seconds=90,
            diff_lines=300,
            reviewer="alice",
            consecutive_reviews=3,
            api_key="gsk_invalid_key",
        )

        assert res["success"] is False
        assert "Invalid Groq API key" in res["error"]


class TestGroqEndpoint:
    def test_missing_pr_key_returns_400(self, client):
        res = client.post("/api/explain/groq", json={})
        assert res.status_code == 400
        assert "pr_key is required" in res.get_json()["error"]

    def test_nonexistent_pr_returns_404(self, client):
        res = client.post("/api/explain/groq", json={"pr_key": "nonexistent/repo#99999"})
        assert res.status_code == 404

    @patch("explain.groq_client.generate_groq_explanation")
    def test_successful_api_explanation(self, mock_gen, client):
        from dashboard.app import LIVE_PRS_CACHE

        pr_key = "test-org/test-repo#101"
        LIVE_PRS_CACHE[pr_key] = {
            "pr_key": pr_key,
            "repo": "test-org/test-repo",
            "pr_number": 101,
            "change_risk": 0.8,
            "review_confidence": 0.2,
            "residual_risk": 0.64,
            "top_features": [],
            "depth_score": 0.2,
            "attention_state": 0.5,
            "review_duration_seconds": 60,
            "diff_lines": 400,
            "reviewer": "bob",
            "consecutive_reviews": 4,
            "bedrock_status": "disconnected",
            "bedrock_explanation": None,
        }

        mock_gen.return_value = {
            "success": True,
            "explanation": "High risk change with rubber-stamp review; request second reviewer.",
            "provider": "Groq Llama 3.3",
            "model": "llama-3.3-70b-versatile",
            "error": None,
        }

        res = client.post(
            "/api/explain/groq",
            json={"pr_key": pr_key, "groq_api_key": "gsk_live_test"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "rubber-stamp" in data["explanation"]
        assert LIVE_PRS_CACHE[pr_key]["bedrock_explanation"] == data["explanation"]
        assert LIVE_PRS_CACHE[pr_key]["bedrock_status"] == "connected"
