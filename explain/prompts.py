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


# ─────────────────────────────────────────────────────────────────────────────
# Reviewer Profile & Fatigue Intervention Prompts & Deterministic Fallbacks
# ─────────────────────────────────────────────────────────────────────────────

REVIEWER_INTERVENTION_HIGH_FATIGUE = (
    "Your review depth has dropped {trend_pct}% over today's session "
    "and {off_hours_pct}% of today's reviews were submitted off-hours. "
    "{next_repo}#{next_number} (change risk {next_risk:.2f}) touches "
    "{path_flags} — defer it to tomorrow morning and stop reviewing now."
)

REVIEWER_INTERVENTION_MODERATE_FATIGUE = (
    "Your depth score trend is declining this session (slope: {slope:.3f}). "
    "Consider reviewing {next_repo}#{next_number} first thing tomorrow "
    "when your baseline is reset, rather than pushing through tonight."
)

REVIEWER_INTERVENTION_HEALTHY = (
    "Your review depth is above your 30-day baseline. Good time to "
    "prioritize {next_repo}#{next_number} — it has the highest change "
    "risk in your queue ({next_risk:.2f}) and has been waiting "
    "{wait_hours:.0f} hours."
)

REVIEWER_INTERVENTION_SYSTEM = """You are a code review health advisor \
embedded in Vouch, a PR governance platform. You analyze reviewer behavior \
patterns and generate one specific, actionable intervention. You are direct, \
professional, and data-driven. You never give generic wellness advice. \
Every suggestion must name a specific PR, repo, or time."""

REVIEWER_INTERVENTION_USER = """Reviewer data:
{payload_json}

Generate exactly 2-3 sentences. Rules you must follow:
- Reference at least one specific number from the data (a score, a PR number, \
a repo name, a percentage).
- End with exactly one concrete action the reviewer should take right now.
- Do NOT say: "take care of yourself", "remember to rest", "stay hydrated", \
or any generic wellness phrase.
- Do NOT output a numeric risk score or rating.
- Do NOT add bullet points, headers, or markdown.
- Do NOT use emojis, emoticons, or icons of any kind.
- Plain prose only."""

