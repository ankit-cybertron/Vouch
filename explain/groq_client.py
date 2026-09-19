"""
Vouch — Groq on-demand LLM explanation client.

Provides high-throughput, ultra-low-latency on-demand risk narration when
Amazon Bedrock access is pending approval. Narrates numeric triage metrics
into a single actionable sentence using Groq Llama 3.3 70B (or Llama 3.1 8B).
The LLM narrates — it never scores.
"""

from __future__ import annotations

import json
import logging
import os
import requests

from explain.prompts import build_explanation_prompt, SYSTEM_INSTRUCTION

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL_ID", "llama-3.3-70b-versatile")
FALLBACK_GROQ_MODEL = "llama-3.1-8b-instant"


def resolve_groq_api_key(explicit_key: str | None = None) -> str:
    """Resolve Groq API key from explicit argument, environment, or .env files."""
    if explicit_key and str(explicit_key).strip():
        return str(explicit_key).strip()

    token = os.environ.get("GROQ_API_KEY", "").strip()
    if token:
        return token

    # Check candidates
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(curr_dir, ".env"),
        os.path.join(os.path.dirname(curr_dir), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    for env_path in candidates:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GROQ_API_KEY="):
                            val = line.split("=", 1)[1].strip().strip("\"'")
                            if val:
                                os.environ["GROQ_API_KEY"] = val
                                return val
            except Exception:
                pass

    return ""


def is_groq_configured() -> bool:
    """Return True if a valid Groq API key is discovered."""
    return bool(resolve_groq_api_key())


def generate_groq_explanation(
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
    api_key: str | None = None,
    model: str | None = None,
) -> dict:
    """
    Generate a one-sentence actionable explanation using Groq Cloud API.

    Returns dict with keys:
      - success: bool
      - explanation: str
      - provider: str
      - model: str
      - error: str | None
    """
    token = resolve_groq_api_key(api_key)

    chosen_model = model or DEFAULT_GROQ_MODEL

    if not token:
        return {
            "success": False,
            "explanation": "",
            "provider": "Groq",
            "model": chosen_model,
            "error": "GROQ_API_KEY is not configured. Please enter your Groq API key to generate narration.",
        }

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

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 120,
    }

    try:
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            # Clean up potential outer quotes
            if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
                text = text[1:-1].strip()
            return {
                "success": True,
                "explanation": text,
                "provider": f"Groq ({chosen_model})",
                "model": chosen_model,
                "error": None,
            }
        elif resp.status_code == 401:
            return {
                "success": False,
                "explanation": "",
                "provider": "Groq",
                "model": chosen_model,
                "error": "Invalid Groq API key. Please verify your GROQ_API_KEY credentials.",
            }
        elif resp.status_code == 404 and chosen_model != FALLBACK_GROQ_MODEL:
            # Retry once with instant fallback model
            logger.warning("Groq model %s unavailable, retrying with %s", chosen_model, FALLBACK_GROQ_MODEL)
            payload["model"] = FALLBACK_GROQ_MODEL
            fallback_resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=12)
            if fallback_resp.status_code == 200:
                fb_data = fallback_resp.json()
                text = fb_data["choices"][0]["message"]["content"].strip()
                if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
                    text = text[1:-1].strip()
                return {
                    "success": True,
                    "explanation": text,
                    "provider": f"Groq ({FALLBACK_GROQ_MODEL})",
                    "model": FALLBACK_GROQ_MODEL,
                    "error": None,
                }

        err_body = resp.text[:200]
        logger.error("Groq API error HTTP %s: %s", resp.status_code, err_body)
        return {
            "success": False,
            "explanation": "",
            "provider": "Groq",
            "model": chosen_model,
            "error": f"Groq API returned HTTP {resp.status_code}: {err_body}",
        }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "explanation": "",
            "provider": "Groq",
            "model": chosen_model,
            "error": "Groq API request timed out after 12 seconds. Please retry.",
        }
    except Exception as exc:
        logger.error("Groq request exception: %s", exc)
        return {
            "success": False,
            "explanation": "",
            "provider": "Groq",
            "model": chosen_model,
            "error": f"Failed to connect to Groq API: {str(exc)}",
        }
