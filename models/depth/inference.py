"""
Vouch — Model 2: Review Depth Scorer SageMaker Inference Handler.

Classifies each review comment into 6 depth classes and computes
an aggregate weighted depth score for the entire review.

Input:  {"review_id": "...", "comments": [{"body": "..."}, ...]}
Output: {"depth_score": 0.72, "comments": [{"body": "...", "class": 3, "class_name": "logic_concern", "weight": 0.8}]}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import numpy as np

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEPTH_WEIGHTS = [0.0, 0.15, 0.45, 0.80, 0.90, 1.00]
LABEL_NAMES = [
    "rubber_stamp", "nit_style", "clarifying",
    "logic_concern", "architecture", "security",
]

_tokenizer = None
_model = None


def model_fn(model_dir: str):
    global _tokenizer, _model
    model_path = Path(model_dir) / "model"
    _tokenizer = AutoTokenizer.from_pretrained(str(model_path))
    _model = AutoModelForSequenceClassification.from_pretrained(str(model_path))
    _model.eval()
    logger.info("Depth model loaded from %s", model_path)
    return _model


def input_fn(request_body: str, content_type: str = "application/json") -> dict:
    if content_type != "application/json":
        raise ValueError(f"Unsupported content type: {content_type}")
    return json.loads(request_body)


def predict_fn(data: dict, model) -> dict:
    comments = data.get("comments", [])
    if not comments:
        # No comments — floor score, rubber-stamp
        return {
            "depth_score": 0.0,
            "comments": [],
            "review_id": data.get("review_id"),
        }

    bodies = [c.get("body", "") for c in comments]

    encodings = _tokenizer(
        bodies,
        truncation=True,
        padding=True,
        max_length=256,
        return_tensors="pt",
    )

    with torch.no_grad():
        logits = model(**encodings).logits
    preds = torch.argmax(logits, dim=-1).numpy()

    annotated = []
    for i, (comment, pred_class) in enumerate(zip(comments, preds)):
        annotated.append({
            "body": comment.get("body", ""),
            "class": int(pred_class),
            "class_name": LABEL_NAMES[pred_class],
            "weight": DEPTH_WEIGHTS[pred_class],
            "path": comment.get("path"),
            "line": comment.get("line"),
        })

    # Aggregate: weighted mean (bounded by highest class seen)
    weights = [a["weight"] for a in annotated]
    aggregate_depth = sum(weights) / max(len(weights), 1)
    # Boost: if any security/architecture concern present, floor at that weight
    max_weight = max(weights)
    depth_score = round(max(aggregate_depth, max_weight * 0.5), 4)

    return {
        "review_id": data.get("review_id"),
        "depth_score": depth_score,
        "comment_count": len(annotated),
        "max_depth_class": int(np.max(preds)),
        "comments": annotated,
    }


def output_fn(prediction: dict, accept: str = "application/json") -> tuple[str, str]:
    return json.dumps(prediction), "application/json"
