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
        return "(disconnected)"


def _deterministic_reviewer_fallback(reviewer_data: dict) -> str:
    fatigue_state = str(reviewer_data.get("fatigue_state") or "healthy").lower()
    next_pr = reviewer_data.get("next_high_risk_pr")
    trend_pct = str(reviewer_data.get("trend_pct") or "0")
    off_hours_pct = str(reviewer_data.get("off_hours_pct") or "0%").rstrip("%")
    slope = float(reviewer_data.get("session_depth_trend") or 0.0)

    from explain.prompts import (
        REVIEWER_INTERVENTION_HIGH_FATIGUE,
        REVIEWER_INTERVENTION_MODERATE_FATIGUE,
        REVIEWER_INTERVENTION_HEALTHY,
    )

    if next_pr and isinstance(next_pr, dict):
        next_repo = next_pr.get("repo") or "assigned repo"
        next_number = next_pr.get("number") or 0
        next_risk = float(next_pr.get("change_risk") or 0.0)
        path_flags_list = next_pr.get("path_flags") or []
        path_flags = ", ".join(path_flags_list) if path_flags_list else "core application files"
        wait_hours = float(next_pr.get("wait_hours") or 0.0)

        if fatigue_state == "high":
            return REVIEWER_INTERVENTION_HIGH_FATIGUE.format(
                trend_pct=trend_pct,
                off_hours_pct=off_hours_pct,
                next_repo=next_repo,
                next_number=next_number,
                next_risk=next_risk,
                path_flags=path_flags,
            ).strip()
        elif fatigue_state == "moderate":
            return REVIEWER_INTERVENTION_MODERATE_FATIGUE.format(
                slope=slope,
                next_repo=next_repo,
                next_number=next_number,
            ).strip()
        else:
            return REVIEWER_INTERVENTION_HEALTHY.format(
                next_repo=next_repo,
                next_number=next_number,
                next_risk=next_risk,
                wait_hours=wait_hours,
            ).strip()
    else:
        if fatigue_state == "high":
            return (
                f"Your review depth has dropped {trend_pct}% over today's session "
                f"and {off_hours_pct}% of today's reviews were submitted off-hours. "
                "Defer further reviews to tomorrow morning and stop reviewing now."
            )
        elif fatigue_state == "moderate":
            return (
                f"Your depth score trend is declining this session (slope: {slope:.3f}). "
                "Take a break or review remaining PRs first thing tomorrow when your baseline is reset."
            )
        else:
            return (
                "Your review depth is above your 30-day baseline. "
                "You are reviewing in a healthy state with consistent attention depth."
            )


def generate_reviewer_intervention_with_meta(reviewer_data: dict) -> tuple[str, str]:
    """
    Generate reviewer fatigue intervention text using Bedrock Claude 3 Haiku,
    falling back to Groq, and finally to deterministic templates. Never raises.
    Returns (intervention_text, bot_name).
    """
    from explain.prompts import REVIEWER_INTERVENTION_SYSTEM, REVIEWER_INTERVENTION_USER

    user_prompt = REVIEWER_INTERVENTION_USER.format(
        payload_json=json.dumps(reviewer_data, indent=2)
    )

    # 1. Try Bedrock
    try:
        response = _bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 120,
                "temperature": 0.0,
                "system": REVIEWER_INTERVENTION_SYSTEM,
                "messages": [{"role": "user", "content": user_prompt}],
            }).encode(),
        )
        body = json.loads(response["body"].read())
        text = body["content"][0]["text"].strip()
        if text:
            model_label = "Bedrock Claude 3 Haiku" if "haiku" in BEDROCK_MODEL_ID.lower() else f"Bedrock ({BEDROCK_MODEL_ID})"
            return text, model_label
    except Exception as bedrock_err:
        logger.warning("Bedrock reviewer intervention failed: %s; trying Groq fallback", bedrock_err)

    # 2. Try Groq
    try:
        from explain.groq_client import generate_reviewer_intervention_with_meta as groq_intervention_with_meta
        text, bot_name = groq_intervention_with_meta(reviewer_data)
        if text and text.strip():
            return text.strip(), bot_name
    except Exception as groq_err:
        logger.warning("Groq reviewer intervention failed: %s; using deterministic fallback", groq_err)

    # 3. Deterministic template fallback
    try:
        return _deterministic_reviewer_fallback(reviewer_data).strip(), "Vouch Rule Engine"
    except Exception as fallback_err:
        logger.error("Deterministic fallback failed: %s", fallback_err)
        return "Reviewer depth and cadence metrics are within baseline parameters.", "Vouch Rule Engine"


def generate_reviewer_intervention(reviewer_data: dict) -> str:
    text, _ = generate_reviewer_intervention_with_meta(reviewer_data)
    return text


class BedrockClient:
    """Wrapper class for Bedrock explanation & intervention generation."""

    def generate_explanation(self, **kwargs) -> str:
        return generate_explanation(**kwargs)

    def generate_reviewer_intervention(self, reviewer_data: dict) -> str:
        return generate_reviewer_intervention(reviewer_data)

    def generate_reviewer_intervention_with_meta(self, reviewer_data: dict) -> tuple[str, str]:
        return generate_reviewer_intervention_with_meta(reviewer_data)

