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


def resolve_groq_api_keys(explicit_key: str | None = None) -> list[str]:
    """
    Resolve Groq API keys in priority order:
      1. Explicit key (passed by caller/user)
      2. GROQ_API_KEY (primary)
      3. GROQ_API_KEY_backup (backup)
    Checks local .env files first, then os.environ.
    """
    if explicit_key and str(explicit_key).strip():
        return [str(explicit_key).strip()]

    keys = []

    env_primary = ""
    env_backup = ""

    # Priority 1: Check .env file directly for keys
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
                            if val and not env_primary:
                                env_primary = val
                                os.environ["GROQ_API_KEY"] = val
                        elif line.startswith("GROQ_API_KEY_backup="):
                            val = line.split("=", 1)[1].strip().strip("\"'")
                            if val and not env_backup:
                                env_backup = val
                                os.environ["GROQ_API_KEY_backup"] = val
            except Exception:
                pass

    # Priority 2: Check os.environ
    if not env_primary:
        env_primary = os.environ.get("GROQ_API_KEY", "").strip()
    if not env_backup:
        env_backup = os.environ.get("GROQ_API_KEY_backup", "").strip()

    if env_primary and env_primary not in keys:
        keys.append(env_primary)
    if env_backup and env_backup not in keys:
        keys.append(env_backup)

    return keys


def resolve_groq_api_key(explicit_key: str | None = None) -> str:
    """
    Resolve primary Groq API key from explicit argument, local .env files, or environment.
    Always checks local .env files first to ensure newly added keys are picked up
    without requiring a shell restart.
    """
    keys = resolve_groq_api_keys(explicit_key)
    return keys[0] if keys else ""


def is_groq_configured() -> bool:
    """Return True if at least one valid Groq API key is discovered."""
    return len(resolve_groq_api_keys()) > 0


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
    Cascades automatically through GROQ_API_KEY -> GROQ_API_KEY_backup on failure.
    Cascades automatically through candidate models on 404 (model_not_found).
    If all keys fail, returns requires_key: True so UI can prompt user.

    Returns dict with keys:
      - success: bool
      - requires_key: bool
      - explanation: str
      - provider: str
      - model: str
      - error: str | None
    """
    # Check if primary key resolves (supports mocked resolve_groq_api_key in tests)
    primary_token = resolve_groq_api_key(api_key)

    # Build unique model candidate chain
    model_chain = []
    if model and model.strip():
        model_chain.append(model.strip())
    for m in CANDIDATE_GROQ_MODELS:
        if m and m.strip() and m.strip() not in model_chain:
            model_chain.append(m.strip())

    initial_model = model_chain[0] if model_chain else "qwen/qwen3.8-27b"

    if not primary_token:
        return {
            "success": False,
            "requires_key": True,
            "explanation": "",
            "provider": "Groq",
            "model": initial_model,
            "error": "GROQ_API_KEY is not configured. Please enter your Groq API key or add it to .env.",
        }

    candidate_keys = resolve_groq_api_keys(api_key)
    if not candidate_keys:
        candidate_keys = [primary_token]

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

    last_error = ""
    had_auth_error = False
    attempted_keys = 0

    for key_idx, token in enumerate(candidate_keys):
        attempted_keys += 1
        key_label = "primary" if key_idx == 0 else "backup"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        key_failed = False
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
                    provider_label = f"Groq ({current_model})" if key_idx == 0 else f"Groq ({current_model}) [backup key]"
                    return {
                        "success": True,
                        "requires_key": False,
                        "explanation": text,
                        "provider": provider_label,
                        "model": current_model,
                        "error": None,
                    }
                elif resp.status_code == 401:
                    had_auth_error = True
                    logger.warning("Groq %s key (%s...) failed 401 Unauthorized", key_label, token[:8])
                    last_error = "Invalid Groq API key. Please check your GROQ_API_KEY in .env or session."
                    key_failed = True
                    break
                elif resp.status_code == 404:
                    # Model not available in this account/tier; try next candidate model
                    logger.info("Groq model %s returned 404 on %s key, cascading to next model", current_model, key_label)
                    last_error = _format_groq_error(resp.status_code, resp.text)
                    continue
                elif resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning("Groq %s key (%s...) hit HTTP %s, trying backup key", key_label, token[:8], resp.status_code)
                    last_error = _format_groq_error(resp.status_code, resp.text)
                    key_failed = True
                    break
                else:
                    last_error = _format_groq_error(resp.status_code, resp.text)
                    logger.error("Groq API error HTTP %s: %s", resp.status_code, resp.text[:200])
                    key_failed = True
                    break

            except requests.exceptions.Timeout:
                logger.warning("Groq request timed out on %s key", key_label)
                last_error = f"Groq API request timed out after 12 seconds on {key_label} key."
                key_failed = True
                break
            except Exception as exc:
                logger.error("Groq request exception for model %s on %s key: %s", current_model, key_label, exc)
                last_error = f"Failed to connect to Groq API: {str(exc)}"
                key_failed = True
                break

    # If all configured keys failed, prompt user for key
    if had_auth_error and attempted_keys == 1:
        final_error = "Invalid Groq API key. Please check your GROQ_API_KEY in .env or session."
    elif attempted_keys > 1:
        final_error = f"All {attempted_keys} configured Groq API keys (including backup) failed: {last_error}. Please enter a valid Groq API key."
    else:
        final_error = last_error or "Groq explanation failed across all candidate keys and models."

    return {
        "success": False,
        "requires_key": True,
        "explanation": "",
        "provider": "Groq",
        "model": initial_model,
        "error": final_error,
    }


def generate_reviewer_intervention_with_meta(reviewer_data: dict) -> tuple[str, str]:
    """
    Generate reviewer fatigue intervention using Groq.
    Tries primary GROQ_API_KEY then GROQ_API_KEY_backup across candidate models.
    Returns (text, model_display_name).
    """
    # Check if primary key resolves (supports mocked resolve_groq_api_key in tests)
    primary_token = resolve_groq_api_key()
    if not primary_token:
        raise ValueError("GROQ_API_KEY is not configured.")

    candidate_keys = resolve_groq_api_keys()
    if not candidate_keys:
        candidate_keys = [primary_token]

    from explain.prompts import REVIEWER_INTERVENTION_SYSTEM, REVIEWER_INTERVENTION_USER

    user_content = REVIEWER_INTERVENTION_USER.format(
        payload_json=json.dumps(reviewer_data, indent=2)
    )

    models_to_try = [
        os.environ.get("GROQ_MODEL_ID"),
        "qwen/qwen3.8-27b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ]
    models_to_try = [m for m in models_to_try if m]

    last_exc = None
    for key_idx, token in enumerate(candidate_keys):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        for model in models_to_try:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": REVIEWER_INTERVENTION_SYSTEM},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 120,
                }
                resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"].strip()
                    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
                        text = text[1:-1].strip()
                    backup_tag = " (backup)" if key_idx > 0 else ""
                    model_name = f"Groq {model}{backup_tag}"
                    return text, model_name
                elif resp.status_code == 404:
                    continue
                else:
                    last_exc = RuntimeError(f"Groq API error HTTP {resp.status_code}: {resp.text}")
                    break  # Break out of model loop for this key, try backup key
            except Exception as exc:
                last_exc = exc
                break  # Try backup key

    if last_exc:
        raise last_exc
    raise RuntimeError("Groq intervention generation failed across all keys and models.")


def generate_reviewer_intervention(reviewer_data: dict) -> str:
    """
    Generate reviewer fatigue intervention using Groq.
    Raises exception on failure so caller can fall through to fallback templates.
    """
    text, _ = generate_reviewer_intervention_with_meta(reviewer_data)
    return text


class GroqClient:
    """Wrapper class for Groq LLM operations."""

    def generate_reviewer_intervention(self, reviewer_data: dict) -> str:
        return generate_reviewer_intervention(reviewer_data)

    def generate_reviewer_intervention_with_meta(self, reviewer_data: dict) -> tuple[str, str]:
        return generate_reviewer_intervention_with_meta(reviewer_data)

    def generate_explanation(self, **kwargs) -> dict:
        return generate_groq_explanation(**kwargs)


