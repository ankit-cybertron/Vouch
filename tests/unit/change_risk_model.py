"""
Tests for models/risk/inference.py — Model 1: Change Risk inference handler.
"""

import json
from unittest.mock import MagicMock
import numpy as np
import pytest

from models.risk.inference import (
    FEATURE_COLS,
    input_fn,
    output_fn,
    predict_fn,
)


class TestRiskInferenceHandler:
    def test_feature_columns_contract(self):
        # Must have exactly 28 features in predetermined order for production change risk
        assert len(FEATURE_COLS) == 28
        assert "lines_added" in FEATURE_COLS
        assert "total_revert_count" in FEATURE_COLS
        assert "path_auth" in FEATURE_COLS
        assert "path_credentials" in FEATURE_COLS
        assert "ai_commit_signal" in FEATURE_COLS
        assert "rapid_merge_signal" in FEATURE_COLS

    def test_input_fn_valid_json(self):
        payload = json.dumps({"lines_added": 150, "files_touched": 4})
        parsed = input_fn(payload, "application/json")
        assert parsed["lines_added"] == 150
        assert parsed["files_touched"] == 4

    def test_input_fn_unsupported_content_type(self):
        with pytest.raises(ValueError, match="Unsupported content type"):
            input_fn("data", "text/csv")

    def test_output_fn_serialization(self):
        pred = {"change_risk": 0.75, "top_features": []}
        body, content_type = output_fn(pred)
        assert content_type == "application/json"
        assert json.loads(body)["change_risk"] == 0.75

    def test_predict_fn_computes_risk_and_top_features(self):
        mock_model = MagicMock()
        # Mock predict_proba to return [P(class 0), P(class 1)]
        mock_model.predict_proba.return_value = np.array([[0.28, 0.72]])

        features = {
            "lines_added": 500,
            "total_revert_count": 3,
            "path_auth": 1,
            "files_touched": 12,
        }

        # Mock feature importances in inference module
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "models.risk.inference._feature_importances",
                {"lines_added": 0.4, "total_revert_count": 0.35, "path_auth": 0.25},
            )
            result = predict_fn(features, mock_model)

        assert result["change_risk"] == 0.72
        assert "top_features" in result
        assert len(result["top_features"]) <= 3
        # lines_added contribution = 0.4 * 500 = 200.0
        top_feature_names = [f["feature"] for f in result["top_features"]]
        assert "lines_added" in top_feature_names
