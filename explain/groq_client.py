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
DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL_ID", "qwen/qwen3.8-27b")

# Candidate models in order of priority. If a model returns 404 (model_not_found),
# the client automatically cascades to the next candidate model.
CANDIDATE_GROQ_MODELS = [
    os.environ.get("GROQ_MODEL_ID"),
    "qwen/qwen3.8-27b",
    "groq/compound-mini",
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def resolve_groq_api_key(explicit_key: str | None = None) -> str:
    """
    Resolve Groq API key from explicit argument, local .env files, or environment.
    Always checks local .env files first to ensure newly added keys are picked up
    without requiring a shell restart.
    """
    if explicit_key and str(explicit_key).strip():
        return str(explicit_key).strip()

    # Priority 1: Check .env file directly for active key
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(curr_dir), ".env"),
        os.path.join(curr_dir, ".env"),
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

    # Priority 2: Check os.environ
    token = os.environ.get("GROQ_API_KEY", "").strip()
    if token:
        return token

    return ""


def is_groq_configured() -> bool:
    """Return True if a valid Groq API key is discovered."""
    return bool(resolve_groq_api_key())


def _format_groq_error(resp_status: int, resp_text: str) -> str:
    """Format raw HTTP response into a concise, human-readable error."""
    try:
        data = json.loads(resp_text)
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            if isinstance(err, dict) and "message" in err:
                return f"{err['message']}"
            return str(err)
    except Exception:
        pass
    cleaned = resp_text.strip().replace("\n", " ")
    if len(cleaned) > 120:
        cleaned = cleaned[:120] + "..."
    return f"HTTP {resp_status}: {cleaned}"


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
    Cascades automatically through candidate models on 404 (model_not_found).

    Returns dict with keys:
      - success: bool
      - explanation: str
      - provider: str
      - model: str
      - error: str | None
    """
    token = resolve_groq_api_key(api_key)

    # Build unique model candidate chain
    model_chain = []
    if model and model.strip():
        model_chain.append(model.strip())
    for m in CANDIDATE_GROQ_MODELS:
        if m and m.strip() and m.strip() not in model_chain:
            model_chain.append(m.strip())

    initial_model = model_chain[0] if model_chain else "qwen/qwen3.8-27b"

    if not token:
        return {
            "success": False,
            "explanation": "",
            "provider": "Groq",
            "model": initial_model,
            "error": "GROQ_API_KEY is not configured. Please enter your Groq API key or add it to .env.",
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

    last_error = ""
    for current_model in model_chain:
        payload = {
            "model": current_model,
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
                if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
                    text = text[1:-1].strip()
                return {
                    "success": True,
                    "explanation": text,
                    "provider": f"Groq ({current_model})",
                    "model": current_model,
                    "error": None,
                }
            elif resp.status_code == 401:
                return {
                    "success": False,
                    "explanation": "",
                    "provider": "Groq",
                    "model": current_model,
                    "error": "Invalid Groq API key. Please check your GROQ_API_KEY in .env or session.",
                }
            elif resp.status_code == 404:
                # Model not available in this account/tier; try next candidate in chain
                logger.info("Groq model %s returned 404, cascading to next model", current_model)
                last_error = _format_groq_error(resp.status_code, resp.text)
                continue
            else:
                last_error = _format_groq_error(resp.status_code, resp.text)
                logger.error("Groq API error HTTP %s: %s", resp.status_code, resp.text[:200])
                break

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "explanation": "",
                "provider": "Groq",
                "model": current_model,
                "error": "Groq API request timed out after 12 seconds. Please retry.",
            }
        except Exception as exc:
            logger.error("Groq request exception for model %s: %s", current_model, exc)
            return {
                "success": False,
                "explanation": "",
                "provider": "Groq",
                "model": current_model,
                "error": f"Failed to connect to Groq API: {str(exc)}",
            }

    return {
        "success": False,
        "explanation": "",
        "provider": "Groq",
        "model": initial_model,
        "error": last_error or "None of the configured Groq models were reachable for your account.",
    }

