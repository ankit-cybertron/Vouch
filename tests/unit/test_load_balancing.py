import sys, time, pytest
from unittest.mock import patch

sys.path.insert(0, r"d:\mlops\review-fatigue\Vouch")

import dashboard.app as app_mod

BASE_PR = dict(
    pr_key="org/repo#1", repo="org/repo", number=1, title="Test PR",
    state="open", change_risk=0.80, review_depth=0.50,
    scored_at=time.time() - 3600, reviewers=["alice"],
    path_flags=["security"], author="bob",
    html_url="https://github.com/org/repo/pull/1",
)

@pytest.fixture()
def mock_store():
    prs = [
        dict(BASE_PR, pr_key="org/repo#1", number=1, reviewers=["alice"], scored_at=time.time() - 3600),
        dict(BASE_PR, pr_key="org/repo#2", number=2, reviewers=["alice"], scored_at=time.time() - 7200, review_depth=0.10),
        dict(BASE_PR, pr_key="org/repo#3", number=3, reviewers=["bob"], scored_at=time.time() - 1800, change_risk=0.30),
        dict(BASE_PR, pr_key="org/repo#4", number=4, reviewers=[], state="open", author="carol", change_risk=0.75, scored_at=time.time() - 500),
    ]
    with patch("dashboard.app.store") as ms:
        ms.get_prs.return_value = prs
        ms.get_open_unreviewed_prs.return_value = [prs[3]]
        yield ms


def test_capacity_tiers_assigned(mock_store):
    app_mod._lb_cache.clear()
    from dashboard.app import _build_load_balancing_data
    result = _build_load_balancing_data()
    tiers = {r["login"]: r["capacity_tier"] for r in result["reviewers"]}
    assert set(tiers.keys()) == {"alice", "bob"}
    for t in tiers.values():
        assert t in ("available", "busy", "overloaded")


def test_load_score_bounded(mock_store):
    app_mod._lb_cache.clear()
    from dashboard.app import _build_load_balancing_data
    result = _build_load_balancing_data()
    for r in result["reviewers"]:
        assert 0.0 <= r["current_load_score"] <= 1.0


def test_suggestion_no_self_assignment(mock_store):
    app_mod._lb_cache.clear()
    from dashboard.app import _build_load_balancing_data
    result = _build_load_balancing_data()
    for sugg in result["suggestions"]:
        assert sugg.get("suggested_reviewer") != sugg.get("author", "")


def test_empty_data_returns_safe_dict(mock_store):
    mock_store.get_prs.return_value = []
    mock_store.get_open_unreviewed_prs.return_value = []
    app_mod._lb_cache.clear()
    from dashboard.app import _build_load_balancing_data
    result = _build_load_balancing_data()
    assert "reviewers" in result
    assert "suggestions" in result
    assert result["overloaded_count"] == 0
