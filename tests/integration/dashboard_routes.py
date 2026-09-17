"""
Integration tests for dashboard routes and page rendering.
"""

import pytest


class TestDashboardCoreRoutes:
    def test_landing_page_unauthenticated(self, client):
        """Unauthenticated GET / renders clean minimal landing page without top bar."""
        res = client.get("/")
        assert res.status_code == 200
        html = res.get_data(as_text=True)

        # Confirm single-action button & token fallback are present
        assert "Connect with GitHub" in html
        assert "inline-token-form" in html
        assert "landing-token-input" in html

        # Confirm top navigation bar is absent on landing page
        assert 'class="gh-nav"' not in html
        assert 'id="nav-repos"' not in html

    def test_landing_page_authenticated_redirects_to_repos(self, client):
        """Authenticated user visiting / is redirected straight to /repos."""
        with client.session_transaction() as sess:
            sess["github_token"] = "gho_test_mock_token_xyz"
            sess["user_login"] = "alice"

        res = client.get("/")
        assert res.status_code == 302
        assert res.headers["Location"] == "/repos"

    def test_repos_page_rendering(self, client):
        """GET /repos renders catalog of monitored repositories."""
        res = client.get("/repos")
        assert res.status_code == 200
        html = res.get_data(as_text=True)

        assert "Repositories" in html
        assert "kubernetes/kubernetes" in html
        assert "facebook/react" in html

    def test_pulls_board_rendering(self, client):
        """GET /pulls renders PR intelligence board."""
        res = client.get("/pulls?repo=kubernetes/kubernetes")
        assert res.status_code == 200
        html = res.get_data(as_text=True)

        assert "Pull Requests" in html or "kubernetes" in html

    def test_validation_page_rendering(self, client):
        """GET /validation renders retrospective validation matrix."""
        res = client.get("/validation")
        assert res.status_code == 200
        html = res.get_data(as_text=True)

        assert "Retrospective Validation" in html

    def test_404_error_page(self, client):
        """Requesting non-existent route renders custom 404 page."""
        res = client.get("/non-existent-route-12345")
        assert res.status_code == 404
        html = res.get_data(as_text=True)

        assert "404" in html
        assert "Not Found" in html
