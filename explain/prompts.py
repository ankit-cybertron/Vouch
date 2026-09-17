"""
Vouch — Bedrock prompt templates.

Constraints:
  - The model narrates the numbers. It does NOT produce a risk score.
  - Output must be exactly one sentence.
  - Must reference at least one specific data point.
  - Must end with a specific, actionable recommendation.
"""

from __future__ import annotations

SYSTEM_INSTRUCTION = (
    "You are a code review quality assistant. "
    "You receive structured scoring data and produce exactly one sentence "
    "that narrates the key finding for a human team lead. "
    "Rules: (1) Do not produce a risk score yourself. "
    "(2) Reference at least one specific number from the data. "
    "(3) End with a concrete action. "
    "(4) Maximum 40 words."
)


def build_explanation_prompt(
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
    Build the structured prompt sent to Bedrock.
    Uses JSON-in-prompt style so the model has precise data to narrate.
    """
    top_features_str = ", ".join(
        f"{f['feature']} ({f['contribution']:.3f})" for f in top_risk_features[:3]
    )

    review_minutes = review_duration_seconds // 60

    prompt = f"""{SYSTEM_INSTRUCTION}

Data (JSON):
{{
  "pr": "{pr_key}",
  "change_risk": {change_risk:.2f},
  "review_confidence": {review_confidence:.2f},
  "residual_risk": {residual_risk:.2f},
  "top_risk_features": "{top_features_str}",
  "depth_score": {depth_score:.2f},
  "attention_state": {attention_state:.2f},
  "review_duration_minutes": {review_minutes},
  "diff_lines": {diff_lines},
  "reviewer": "{reviewer}",
  "consecutive_reviews_in_session": {consecutive_reviews},
  "file_context": "{file_context}"
}}

Write one sentence (max 40 words) narrating the most important finding and ending with a specific action:"""

    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# Example outputs (used to validate the prompt in testing)
# ─────────────────────────────────────────────────────────────────────────────

EXAMPLE_OUTPUTS = [
    # High risk, shallow review
    (
        "Approved in 90s on a 600-line diff in `billing/retry.py` — "
        "a file with 11 reverts in 12 months — by a reviewer 7 reviews "
        "into a session; recommend a second look at the retry-backoff logic."
    ),
    # Security path, rubber-stamp
    (
        "A 340-line change touching the auth middleware received only a "
        "bare LGTM from a reviewer mid-session; request explicit sign-off "
        "on the token validation path before merging."
    ),
    # Low risk, fine
    (
        "A 45-line documentation update received a thorough 8-minute review "
        "with two substantive comments; confidence is high and no action needed."
    ),
]
