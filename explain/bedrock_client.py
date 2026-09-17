"""
Vouch — Amazon Bedrock explanation client.

Calls Claude 3 Haiku to narrate numeric scoring results as a single,
actionable human sentence. The LLM narrates — it never scores.

Tight JSON-structured prompt prevents the model from going off-script.
"""

from __future__ import annotations

import json
import logging
import os

import boto3

from explain.prompts import build_explanation_prompt

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-3-haiku-20240307-v1:0",
)

_bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")


def generate_explanation(
    pr_key: str,
    change_risk: float,
    review_confidence: float,
    residual_risk: float,
    top_risk_features: list[dict],
    depth_score: float,
    attention_state: float,
    review_duration_seconds: int,
    diff_lines: int,
    reviewer: str,
    consecutive_reviews: int,
    file_context: str = "",
) -> str:
    """
    Generate a one-sentence actionable explanation for the scoring result.

    Returns the explanation string. On error, returns a safe fallback.
    """
    prompt = build_explanation_prompt(
        pr_key=pr_key,
        change_risk=change_risk,
        review_confidence=review_confidence,
        residual_risk=residual_risk,
        top_risk_features=top_risk_features,
        depth_score=depth_score,
        attention_state=attention_state,
        review_duration_seconds=review_duration_seconds,
        diff_lines=diff_lines,
        reviewer=reviewer,
        consecutive_reviews=consecutive_reviews,
        file_context=file_context,
    )

    try:
        response = _bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 120,
                "temperature": 0.0,  # deterministic narration
                "messages": [{"role": "user", "content": prompt}],
            }).encode(),
        )
        body = json.loads(response["body"].read())
        text = body["content"][0]["text"].strip()
        logger.info("Explanation for %s: %s", pr_key, text)
        return text

    except Exception as exc:
        logger.error("Bedrock call failed for %s: %s", pr_key, exc)
        # Safe fallback that still communicates the key fact
        return (
            f"PR scored residual risk {residual_risk:.2f} "
            f"(change risk {change_risk:.2f}, review confidence {review_confidence:.2f}) "
            f"— explanation generation failed."
        )
