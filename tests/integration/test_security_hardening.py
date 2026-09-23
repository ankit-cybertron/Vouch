"""
Integration tests for security hardening in the Vouch dashboard.

Validates:
- Security HTTP headers (CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy)
- Session cookie configuration (HttpOnly, SameSite)
- CSRF Origin verification on state-changing methods
- Webhook exemption from CSRF Origin check
- Input sanitization and rejection of dangerous repository input
- Clean error responses without stack trace leaks
"""

import pytest
from dashboard.app import _parse_repo_input


class TestSecurityHeaders:
    """Ensure all required HTTP security headers are present in responses."""

    def test_security_headers_present_on_get(self, client):
        res = client.get("/")
        assert res.status_code == 200

        # Content-Security-Policy
        csp = res.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "object-src 'none'" in csp

        # Standard defense-in-depth headers
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert res.headers.get("X-XSS-Protection") == "0"
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        perm_policy = res.headers.get("Permissions-Policy", "")
        assert "camera=()" in perm_policy
        assert "microphone=()" in perm_policy
        assert "geolocation=()" in perm_policy

    def test_security_headers_present_on_api(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"


class TestSessionCookieConfiguration:
    """Verify session cookie security flags."""

    def test_session_cookie_flags(self, app):
        assert app.config.get("SESSION_COOKIE_HTTPONLY") is True
        assert app.config.get("SESSION_COOKIE_SAMESITE") == "Lax"


class TestCSRFOriginValidation:
    """Verify CSRF Origin verification on state-changing methods."""

    def test_cross_origin_post_blocked(self, client):
        """State-changing POST with an untrusted Origin must return 403 Forbidden."""
        res = client.post(
            "/api/auth/token",
            json={"token": "test_token_123"},
            headers={"Origin": "https://malicious-attacker.com"},
        )
        assert res.status_code == 403
        data = res.get_json()
        assert data.get("error") == "Cross-origin request blocked"

    def test_same_origin_post_allowed(self, client):
        """State-changing POST with matching Origin is allowed through."""
        res = client.post(
            "/api/auth/token",
            json={"token": ""},
            headers={"Origin": "http://localhost"},
        )
        # Should not be 403 CSRF error; it will proceed to handler and return 400 for empty token
        assert res.status_code != 403

    def test_webhook_exempt_from_origin_check(self, client):
        """GitHub webhook endpoint is exempt from Origin check."""
        res = client.post(
            "/webhook",
            data="{}",
            content_type="application/json",
            headers={"Origin": "https://api.github.com"},
        )
        # Webhook handler returns 401/400 (e.g. invalid signature), never 403 CSRF block
        assert res.status_code != 403


class TestInputValidation:
    """Verify strict repository input parsing and sanitization."""

    def test_valid_repo_inputs(self):
        result = _parse_repo_input("kubernetes/kubernetes")
        assert result == ("kubernetes", "kubernetes")

        result = _parse_repo_input("facebook/react")
        assert result == ("facebook", "react")

    def test_invalid_and_malicious_repo_inputs_rejected(self):
        dangerous_inputs = [
            "../../etc/passwd",
            "kubernetes;rm -rf /",
            "<script>alert(1)</script>",
            "owner/repo/extra/path",
            "owner with spaces/repo",
            "owner/repo$(whoami)",
            "",
            None,
        ]
        for bad_input in dangerous_inputs:
            result = _parse_repo_input(bad_input)
            assert result is None


class TestErrorResponses:
    """Verify clean error responses without stack traces."""

    def test_404_error_page(self, client):
        res = client.get("/non-existent-page-xyz-123")
        assert res.status_code == 404
        html = res.get_data(as_text=True)
        assert "404" in html
        assert "Page Not Found" in html
        assert "Traceback" not in html
