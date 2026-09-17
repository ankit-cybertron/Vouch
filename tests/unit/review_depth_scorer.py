"""
Tests for models/depth/inference.py — Model 2: Review Depth Scorer.
"""

from unittest.mock import MagicMock
import numpy as np
import pytest
import torch

from models.depth.inference import (
    DEPTH_WEIGHTS,
    LABEL_NAMES,
    input_fn,
    predict_fn,
)


class TestDepthInferenceHandler:
    def test_depth_weights_ordering(self):
        # Weights should be strictly non-decreasing from rubber-stamp to security
        assert len(DEPTH_WEIGHTS) == 6
        assert len(LABEL_NAMES) == 6
        assert DEPTH_WEIGHTS[0] == 0.0  # rubber_stamp
        assert DEPTH_WEIGHTS[-1] == 1.0  # security
        for i in range(len(DEPTH_WEIGHTS) - 1):
            assert DEPTH_WEIGHTS[i] <= DEPTH_WEIGHTS[i + 1]

    def test_input_fn_parsing(self):
        body = '{"review_id": "r1", "comments": [{"body": "LGTM"}]}'
        parsed = input_fn(body, "application/json")
        assert parsed["review_id"] == "r1"
        assert len(parsed["comments"]) == 1

    def test_input_fn_rejects_non_json(self):
        with pytest.raises(ValueError, match="Unsupported content type"):
            input_fn("some text", "text/plain")

    def test_empty_comments_returns_zero_depth(self):
        # Rubber stamped review with no comments receives floor score 0.0
        data = {"review_id": "r_empty", "comments": []}
        res = predict_fn(data, None)
        assert res["depth_score"] == 0.0
        assert res["comments"] == []
        assert res["review_id"] == "r_empty"

    def test_predict_fn_annotates_comments(self):
        mock_model = MagicMock()
        # Mock sequence classification logits for 2 comments:
        # Comment 0 -> Class 0 ("rubber_stamp")
        # Comment 1 -> Class 3 ("logic_concern", weight 0.80)
        logits_tensor = torch.tensor([
            [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 10.0, 0.0, 0.0],
        ])
        mock_output = MagicMock()
        mock_output.logits = logits_tensor
        mock_model.return_value = mock_output

        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {"input_ids": torch.zeros((2, 10), dtype=torch.long)}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("models.depth.inference._tokenizer", mock_tokenizer)
            data = {
                "review_id": "r_detailed",
                "comments": [
                    {"body": "LGTM!"},
                    {"body": "Potential race condition in mutex lock on line 42"},
                ],
            }
            res = predict_fn(data, mock_model)

        assert res["review_id"] == "r_detailed"
        assert res["comment_count"] == 2
        assert len(res["comments"]) == 2
        assert res["comments"][0]["class_name"] == "rubber_stamp"
        assert res["comments"][1]["class_name"] == "logic_concern"
        assert res["depth_score"] > 0.0
