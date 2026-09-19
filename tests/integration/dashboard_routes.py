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

        # Confirm primary action / setup notice & token fallback are present
        assert ("Install Vouch on GitHub" in html or "GitHub App not configured" in html)
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

    def test_team_health_page_rendering(self, client):
        """GET /team-health renders team review health and fatigue analytics."""
        res = client.get("/team-health")
        assert res.status_code == 200
        html = res.get_data(as_text=True)

        assert "Team Review Health" in html
        assert "Reviewer Fatigue Index" in html
        assert "Rubber-Stamp Index" in html

    def test_validation_route_compatibility(self, client):
        """GET /validation renders team health analytics for backward compatibility."""
        res = client.get("/validation")
        assert res.status_code == 200
        html = res.get_data(as_text=True)

        assert "Team Review Health" in html

    def test_404_error_page(self, client):
        """Requesting non-existent route renders custom 404 page."""
        res = client.get("/non-existent-route-12345")
        assert res.status_code == 404
        html = res.get_data(as_text=True)

        assert "404" in html
        assert "Not Found" in html

    def test_pulls_direct_visit_shows_hero_search(self, client):
        """GET /pulls directly renders clean hero repository search selector."""
        res = client.get("/pulls")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "hero-search-page" in html
        assert "Pull Requests Intelligence" in html
        assert "data-smart-repo" in html

    def test_team_health_direct_visit_shows_hero_search(self, client):
        """GET /team-health directly renders clean hero repository search selector."""
        res = client.get("/team-health")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "hero-search-page" in html
        assert "Team Review Health" in html
        assert "data-smart-repo" in html

    def test_smart_repos_search_api(self, client):
        """GET /api/repos/search returns live typeahead suggestions."""
        res = client.get("/api/repos/search?q=k8s")
        assert res.status_code == 200
        data = res.get_json()
        assert "results" in data
        assert "query" in data
        assert data["query"] == "k8s"


class TestReviewerRoutes:
    def test_reviewer_search_page_200(self, client):
        response = client.get("/reviewer")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Reviewer Profiles" in html
        assert "reviewer-search-input" in html

    def test_reviewer_profile_not_found(self, client, mock_github_404):
        response = client.get("/reviewer/this-user-does-not-exist-xyz")
        assert response.status_code == 404
        html = response.get_data(as_text=True)
        assert "No GitHub user found" in html

    def test_reviewer_api_endpoint(self, client):
        response = client.get("/api/reviewer/torvalds")
        assert response.status_code in (200, 404)
        if response.status_code == 200:
            data = response.get_json()
            assert "grade" in data
            assert "fatigue_state" in data
            assert "priority_queue" in data

    def test_reviewer_cache_refresh(self, client):
        response = client.get("/api/reviewer/testuser/refresh")
        assert response.status_code == 200
        assert response.get_json() == {"status": "cache cleared", "username": "testuser"}

