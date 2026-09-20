import sys, time, pytest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

sys.path.insert(0, r"d:\mlops\review-fatigue\Vouch")


def _pr(num, age_h, reviewers, state="open", change_risk=0.5):
    created_at = (datetime.now(timezone.utc) - timedelta(hours=age_h)).isoformat()
    return dict(
        pr_key=f"r#{num}", repo="r", number=num, title=f"PR {num}",
        author="alice", reviewers=reviewers, state=state, change_risk=change_risk,
        scored_at=time.time() - age_h * 3600, path_flags=[], html_url="",
        created_at=created_at,
    )


@pytest.fixture()
def mock_store_sla():
    prs = [
        _pr(1, 50, [], change_risk=0.80),
        _pr(2, 44, ["bob"], change_risk=0.60),
        _pr(3, 10, ["carol"], change_risk=0.30),
    ]
    with patch("dashboard.app.store") as ms:
        ms.get_prs.return_value = prs
        yield ms


def test_breach_probability_bounded(mock_store_sla):
    from dashboard.app import _build_sla_predictions
    result = _build_sla_predictions()
    for p in result["predictions"]:
        assert 0.0 <= p["breach_probability"] <= 1.0


def test_already_breached_flagged(mock_store_sla):
    from dashboard.app import _build_sla_predictions
    result = _build_sla_predictions()
    breached = [p for p in result["predictions"] if p["status"] == "breached"]
    assert len(breached) >= 1


def test_unassigned_pr_max_load(mock_store_sla):
    from dashboard.app import _build_sla_predictions
    result = _build_sla_predictions()
    unassigned = [p for p in result["predictions"] if not p["reviewers"]]
    for p in unassigned:
        assert p["reviewer_avg_load"] == 1.0


def test_not_at_risk_excluded(mock_store_sla):
    from dashboard.app import _build_sla_predictions
    result = _build_sla_predictions()
    numbers = [p["number"] for p in result["predictions"]]
    assert 3 not in numbers
