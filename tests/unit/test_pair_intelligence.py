import sys, time, pytest
from unittest.mock import patch

sys.path.insert(0, r"d:\mlops\review-fatigue\Vouch")

import dashboard.app as app_mod

_t = time.time() - 3600

PAIR_PRS = [
    dict(pr_key="r#1", repo="r", number=1, title="t", author="alice", reviewers=["bob"],
         review_depth=0.80, residual_risk=0.10, re_queued=False, path_flags=["auth"],
         change_risk=0.50, scored_at=_t, state="merged"),
    dict(pr_key="r#2", repo="r", number=2, title="t", author="alice", reviewers=["bob"],
         review_depth=0.75, residual_risk=0.15, re_queued=False, path_flags=["auth"],
         change_risk=0.45, scored_at=_t, state="merged"),
    dict(pr_key="r#3", repo="r", number=3, title="t", author="alice", reviewers=["bob"],
         review_depth=0.78, residual_risk=0.12, re_queued=False, path_flags=["auth"],
         change_risk=0.55, scored_at=_t, state="merged"),
    dict(pr_key="r#4", repo="r", number=4, title="t", author="carol", reviewers=["dave"],
         review_depth=0.08, residual_risk=0.80, re_queued=True, path_flags=[],
         change_risk=0.70, scored_at=_t, state="open"),
    dict(pr_key="r#5", repo="r", number=5, title="t", author="carol", reviewers=["dave"],
         review_depth=0.05, residual_risk=0.90, re_queued=True, path_flags=[],
         change_risk=0.85, scored_at=_t, state="open"),
]


@pytest.fixture()
def mock_store_pair():
    with patch("dashboard.app.store") as ms:
        ms.get_prs.return_value = PAIR_PRS
        yield ms


def test_self_review_excluded(mock_store_pair):
    extra = dict(PAIR_PRS[0], pr_key="r#6", number=6, author="selfrev", reviewers=["selfrev"])
    mock_store_pair.get_prs.return_value = PAIR_PRS + [extra]
    app_mod._pair_cache.clear()
    from dashboard.app import _build_pair_intelligence_data
    result = _build_pair_intelligence_data()
    for p in result["pairs"]:
        assert p["author"] != p["reviewer"]


def test_golden_pair_classification(mock_store_pair):
    app_mod._pair_cache.clear()
    from dashboard.app import _build_pair_intelligence_data
    result = _build_pair_intelligence_data()
    golden_keys = [(p["author"], p["reviewer"]) for p in result["golden"]]
    assert ("alice", "bob") in golden_keys


def test_risky_pair_classification(mock_store_pair):
    app_mod._pair_cache.clear()
    from dashboard.app import _build_pair_intelligence_data
    result = _build_pair_intelligence_data()
    risky_keys = [(p["author"], p["reviewer"]) for p in result["risky"]]
    assert ("carol", "dave") in risky_keys


def test_minimum_review_filter(mock_store_pair):
    app_mod._pair_cache.clear()
    from dashboard.app import _build_pair_intelligence_data
    result = _build_pair_intelligence_data()
    for p in result["pairs"]:
        assert p["reviews"] >= 2
