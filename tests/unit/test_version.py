"""
Unit tests for application version control, git build resolution, and version endpoints.
"""

import pytest
from dashboard.version import __version__, get_version_info, get_git_info
from dashboard.app import app


def test_version_info_structure():
    """Verify version info dictionary schema and defaults."""
    info = get_version_info()
    assert "version" in info
    assert "version_tag" in info
    assert "commit" in info
    assert "commit_short" in info
    assert "branch" in info
    assert info["version"] == __version__
    assert info["version_tag"] == f"v{__version__}"
    assert len(info["commit_short"]) > 0


def test_git_info_resolution():
    """Verify git metadata resolution returns valid strings."""
    git_info = get_git_info()
    assert isinstance(git_info["commit"], str)
    assert isinstance(git_info["commit_short"], str)
    assert isinstance(git_info["branch"], str)


def test_api_version_endpoint():
    """Verify GET /api/version returns 200 and accurate payload."""
    with app.test_client() as client:
        resp = client.get("/api/version")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["version"] == __version__
        assert data["commit_short"] is not None


def test_api_health_endpoint():
    """Verify GET /api/health includes status and version."""
    with app.test_client() as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"
        assert data["version"] == __version__
        assert "timestamp" in data


def test_template_context_has_version():
    """Verify app_version is injected into rendered templates."""
    with app.test_client() as client:
        resp = client.get("/repos")
        assert resp.status_code == 200
        content = resp.get_data(as_text=True)
        # Check that version badge and footer version appear in the HTML
        assert f"v{__version__}" in content
