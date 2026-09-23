"""
Vouch — Flask Dashboard Application.

Routes:
    GET  /                          → Residual-Risk Board (demo + live-fetched PRs)
    GET  /pr/<org>/<repo>/<number>  → PR Detail view
    GET  /validation                → Retrospective Validation (the demo)
    GET  /api/prs                   → JSON endpoint for filtering/live updates
    POST /api/fetch-repo            → Fetch & score live PRs from a GitHub repo
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import html
import math
import os
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
import logging
from urllib.parse import urlparse, urlencode

logger = logging.getLogger(__name__)

from markupsafe import Markup
import requests
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    redirect,
    session,
    url_for,
    has_request_context,
)
from werkzeug.middleware.proxy_fix import ProxyFix

from dashboard.store import get_store
from dashboard.version import get_version_info, __version__
from dashboard.github_app import github_app_auth

try:
    from dotenv import load_dotenv
    _app_file_dir = os.path.dirname(os.path.abspath(__file__))
    for _p in [
        os.path.join(os.path.dirname(_app_file_dir), ".env"),
        os.path.join(_app_file_dir, ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]:
        if os.path.exists(_p):
            load_dotenv(_p)
            break
except ImportError:
    pass

from dashboard.session_store import get_session_store

store = get_store()
session_store = get_session_store()


def _compute_board_stats(prs: list[dict] | None = None) -> dict:
    """Compute live aggregate statistics dynamically across scored pull requests."""
    if prs is None:
        prs = list(LIVE_PRS_CACHE.values()) if "LIVE_PRS_CACHE" in globals() else []
    total = len(prs)
    high_risk = sum(1 for p in prs if p.get("risk_tier") == "high")
    medium_risk = sum(1 for p in prs if p.get("risk_tier") == "medium")
    requeued = sum(1 for p in prs if (p.get("residual_risk") or 0) >= 0.65)
    scored_prs = [p for p in prs if p.get("residual_risk") is not None]
    avg_res = round(sum(p["residual_risk"] for p in scored_prs) / max(len(scored_prs), 1), 2) if scored_prs else 0.0
    return {
        "total_scored": total,
        "total_prs": total,
        "high_risk_flagged": high_risk,
        "medium_risk_count": medium_risk,
        "requeued_today": requeued,
        "avg_residual_risk": avg_res,
        "precision_at_20": f"{min(requeued, 9)}/20" if total else "0/20",
    }

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(DASHBOARD_DIR, "templates"),
    static_folder=os.path.join(DASHBOARD_DIR, "static"),
)

_secret_key = os.environ.get("SECRET_KEY", "").strip()
if not _secret_key:
    raise RuntimeError(
        "SECRET_KEY environment variable is not configured. "
        "A secure SECRET_KEY must be provided via environment or AWS Secrets Manager."
    )
app.config["SECRET_KEY"] = _secret_key
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Production-grade session cookie hardening
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("FLASK_ENV") == "production"
    or os.environ.get("SESSION_COOKIE_SECURE", "false").lower() in ("true", "1")
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

app.jinja_env.globals.update(max=max, min=min)

# Support reverse-proxy headers (Elastic Beanstalk, ngrok, AWS ALB, etc.)
# This ensures request.host_url and url_for() produce the correct scheme/host.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


@app.after_request
def add_security_headers(response):
    """Inject production-ready HTTP security headers."""
    # Prevent MIME sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Prevent clickjacking / framing
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    # Cross-site scripting filter
    response.headers["X-XSS-Protection"] = "0"
    # Referrer policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Restrict permissions
    response.headers["Permissions-Policy"] = (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    )
    # Content Security Policy (allows Google fonts, GitHub avatars, necessary static assets)
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' https: data:; "
        "connect-src 'self' https://api.github.com; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self' https://github.com;"
    )
    response.headers["Content-Security-Policy"] = csp

    # Enable HSTS on HTTPS requests
    is_https = request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    if is_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


@app.before_request
def verify_csrf_origin():
    """Defense-in-depth CSRF guard verifying Origin/Referer on state-changing API requests."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Exempt webhooks if external providers send them
        if request.path.startswith("/api/webhook") or request.path.startswith("/webhook"):
            return None

        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")

        if origin:
            parsed_origin = urlparse(origin).netloc.lower()
            current_host = request.host.lower()
            if parsed_origin and parsed_origin != current_host:
                base_url = os.environ.get("APP_BASE_URL", "")
                if not (base_url and urlparse(base_url).netloc.lower() == parsed_origin):
                    logger.warning("Blocked cross-origin request from origin: %s to %s", origin, request.path)
                    return jsonify({"success": False, "error": "Cross-origin request blocked"}), 403
        elif referer:
            parsed_referer = urlparse(referer).netloc.lower()
            current_host = request.host.lower()
            if parsed_referer and parsed_referer != current_host:
                base_url = os.environ.get("APP_BASE_URL", "")
                if not (base_url and urlparse(base_url).netloc.lower() == parsed_referer):
                    logger.warning("Blocked cross-origin request from referer: %s to %s", referer, request.path)
                    return jsonify({"success": False, "error": "Cross-origin request blocked"}), 403


def _app_base_url() -> str:
    """
    Return the canonical public base URL for this Vouch instance.

    Uses APP_BASE_URL env var when set (required for deployed environments).
    Falls back to Flask's request.host_url for localhost development.
    """
    base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    if has_request_context():
        return request.host_url.rstrip("/")
    return "http://localhost:5001"


def _oauth_callback_uri() -> str:
    """Build the exact GitHub OAuth callback URI, respecting APP_BASE_URL."""
    return _app_base_url() + url_for("auth_github_callback")


def _app_setup_callback_uri() -> str:
    """Build the GitHub App post-install callback URI, respecting APP_BASE_URL."""
    return _app_base_url() + url_for("auth_github_app_callback")


def _resolve_github_token() -> str:
    """Dynamically resolve GitHub token with maximum resilience.
    Priority order:
      0. GitHub App Installation Access Token (if GITHUB_APP_ID configured +
         session holds an installation_id) — short-lived, auto-refreshed.
      1. User's active session token (from OAuth login or legacy token modal)
      2. Server GITHUB_TOKEN environment variable
      3. .env file
      4. git credential helper
    """
    # Priority 0 — GitHub App Installation Access Token
    try:
        if has_request_context() and github_app_auth.is_configured():
            installation_id = session.get("installation_id")
            if installation_id:
                try:
                    return github_app_auth.get_installation_token(installation_id)
                except Exception as exc:
                    print(f"[WARN] GitHub App token fetch failed: {exc}")
    except Exception:
        pass

    # Priority 1 — User's active session token resolved server-side via auth_sid
    try:
        if has_request_context():
            auth_sid = session.get("auth_sid")
            if auth_sid:
                tok = session_store.get_token(auth_sid)
                if tok:
                    return tok
            if session.get("github_token"):
                tok = str(session["github_token"]).strip()
                if tok:
                    return tok
    except Exception:
        pass

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token

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
                        if line.startswith("GITHUB_TOKEN="):
                            t = line.split("=", 1)[1].strip().strip("\"'")
                            if t:
                                os.environ["GITHUB_TOKEN"] = t
                                return t
            except Exception:
                pass

    try:
        import subprocess
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n",
            capture_output=True,
            text=True,
            timeout=2,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                t = line.split("=", 1)[1].strip()
                if t:
                    os.environ["GITHUB_TOKEN"] = t
                    return t
    except Exception:
        pass

    return ""


def _get_github_headers() -> dict[str, str]:
    token = _resolve_github_token()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# Initialize token on module load
GITHUB_TOKEN = _resolve_github_token()
GITHUB_HEADERS = _get_github_headers()

# ─────────────────────────────────────────────────────────────────────────────
# Scoring helpers (heuristic — runs without a trained model)
# ─────────────────────────────────────────────────────────────────────────────

SENSITIVE_PATHS = re.compile(
    r"(auth|authn|authz|payment|billing|pay|migration|migrate|crypto|"
    r"encrypt|tls|secret|config|infra|terraform|helm|kube|k8s|deploy|"
    r"security|permission|token|oauth|jwt)",
    re.IGNORECASE,
)

def _risk_tier(score: float) -> str:
    if score >= 0.65:
        return "high"
    elif score >= 0.35:
        return "medium"
    return "low"


_GITHUB_IDENTIFIER = re.compile(r"^[a-zA-Z0-9_.-]{1,100}$")


def _parse_repo_input(raw: str) -> tuple[str, str] | None:
    """
    Accept any of:
      - https://github.com/owner/repo
      - github.com/owner/repo
      - owner/repo
    Returns (owner, repo) or None on failure.
    Validates strictly against GitHub identifier rules.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip().rstrip("/")
    owner, repo = None, None
    # URL form
    if "github.com" in raw:
        try:
            parsed = urlparse(raw if raw.startswith("http") else "https://" + raw)
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2:
                owner, repo = parts[0], parts[1]
        except Exception:
            pass
    # owner/repo form
    elif "/" in raw:
        parts = raw.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            owner, repo = parts[0], parts[1]

    if owner and repo and _GITHUB_IDENTIFIER.match(owner) and _GITHUB_IDENTIFIER.match(repo):
        return owner, repo
    return None


# In-memory cache for all scored PRs (demo + live fetched)
LIVE_PRS_CACHE: dict[str, dict] = {}

# Repository catalog and PR cache by repository
LANGUAGE_COLORS = {
    "Python": "#3572A5",
    "Go": "#00ADD8",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "C++": "#f34b7d",
    "C": "#555555",
    "Rust": "#dea584",
    "Java": "#b07219",
    "Ruby": "#701516",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "LLVM": "#1f883d",
    "Unknown": "#8b949e",
}

FETCHED_REPOS: dict[str, dict] = {}
REPO_PRS_CACHE: dict[str, list[dict]] = {}
_reviewer_cache: dict = {}   # {username: {"data": dict, "ts": float}}
REVIEWER_CACHE_TTL: int = 300  # 5 minutes

# Analytics caches (2-minute TTL) for Features 1, 5, 6, 8
_health_cache: dict = {}    # {"org_health": {"data": dict, "ts": float}}
_lb_cache: dict = {}        # {"load_balancing": {"data": dict, "ts": float}}
_pair_cache: dict = {}      # {"pair_intel": {"data": dict, "ts": float}}
HEALTH_CACHE_TTL: int = 120  # 2 minutes



def _github_get(url: str, params: dict | None = None) -> dict | list | None:
    headers = _get_github_headers()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=12)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 403:
            print(f"[WARN] GitHub API 403 Forbidden: {resp.text[:120]}")
            return {"_rate_limit_exceeded": True, "message": resp.json().get("message", "API rate limit exceeded")}
        elif resp.status_code == 404:
            return {"_not_found": True}
        return None
    except Exception as exc:
        print(f"[ERROR] GitHub request error {url}: {exc}")
        return None


def _score_pr_heuristic(pr_data: dict, reviews: list[dict] | None = None, comments: list[dict] | None = None) -> dict:
    """
    Score a PR using Vouch's core residual risk formulation:
        residual_risk = change_risk × (1.0 − review_confidence)

    Three scoring models run in the heuristic engine:
      Model 1 — Change Risk (XGBoost proxy): log-scale size, path sensitivity,
                 + 7 discrepancy signals (no reviewer, rapid merge, stale PR,
                   WIP-not-draft, mass file touch, empty description, self-review)
      Model 2 — Review Depth (DistilBERT proxy): expanded rubber stamp detection,
                 keyword-based comment classification, request-changes penalty
      Model 3 — Attention Baseline (Robust z-score proxy): multi-factor continuous
                 scoring across time-of-day, weekends, reviewer velocity, review pace

    Risk tiers:
      High   (≥ 0.65): triggers re-queue — red badge
      Medium (0.35–0.64): moderate risk — yellow badge
      Low    (< 0.35): safe change — green badge
    """
    reviews = reviews if reviews is not None else pr_data.get("_reviews", [])
    comments = comments if comments is not None else pr_data.get("_comments", [])

    additions = pr_data.get("additions", 0) or 0
    deletions = pr_data.get("deletions", 0) or 0
    changed_files = pr_data.get("changed_files", 0) or 0
    total_lines = additions + deletions

    if total_lines == 0 and changed_files == 0:
        total_lines = min(max(len(pr_data.get("body") or "") // 8, 40), 650)
        changed_files = max(total_lines // 80, 1)
        additions = int(total_lines * 0.75)
        deletions = total_lines - additions

    pr_number = pr_data.get("number") or pr_data.get("pr_number", 0)
    repo_full = pr_data.get("_repo") or pr_data.get("repo", "")
    state = pr_data.get("state", "open")
    created_at = pr_data.get("created_at", "")
    raw_merged_at = pr_data.get("merged_at")
    raw_closed_at = pr_data.get("closed_at")
    if raw_merged_at or pr_data.get("merged") is True or pr_data.get("is_merged") is True:
        is_merged = True
    elif pr_data.get("merged") is False or pr_data.get("is_merged") is False:
        is_merged = False
    elif raw_closed_at and not raw_merged_at:
        # Real GitHub PR closed without merging has closed_at set and merged_at is null/None
        is_merged = False
    elif state == "closed":
        # Backward compatibility for mock objects without closed_at or merged_at
        is_merged = True
    else:
        is_merged = False

    merged_at = raw_merged_at if is_merged else None
    closed_at = raw_closed_at
    is_closed_unmerged = (state == "closed" and not is_merged)
    pr_title = (pr_data.get("title") or "").strip()
    pr_body = (pr_data.get("body") or "").strip()
    pr_author = (pr_data.get("user") or {}).get("login", "unknown")

    # Review duration
    review_duration_seconds = pr_data.get("_duration_secs", 0)
    if not review_duration_seconds:
        end_time_str = merged_at or closed_at
        if created_at and end_time_str:
            try:
                t0 = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                review_duration_seconds = max(int((t1 - t0).total_seconds()), 1)
            except Exception:
                review_duration_seconds = 3600
        elif state == "open" and created_at:
            try:
                t0 = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                review_duration_seconds = max(int((now - t0).total_seconds()), 1)
            except Exception:
                review_duration_seconds = 3600
        else:
            review_duration_seconds = 3600

    # Compute PR age in days
    pr_age_days = 0
    if created_at:
        try:
            t0 = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            pr_age_days = (datetime.now(timezone.utc) - t0).days
        except Exception:
            pr_age_days = 0

    # Merge timestamp for hour-of-day and day-of-week (attention signal)
    merge_hour = 12  # default: midday
    is_weekend_merge = False
    if merged_at:
        try:
            t_merge = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
            merge_hour = t_merge.hour
            is_weekend_merge = t_merge.weekday() >= 5  # Sat=5, Sun=6
        except Exception:
            pass
    elif created_at:
        try:
            t_open = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            merge_hour = t_open.hour
            is_weekend_merge = t_open.weekday() >= 5
        except Exception:
            pass

    # Sensitive paths
    sensitive_count = 0
    sensitive_files = []
    for f in (pr_data.get("_files") or []):
        fn = f.get("filename", "") if isinstance(f, dict) else str(f)
        if SENSITIVE_PATHS.search(fn):
            sensitive_count += 1
            sensitive_files.append(fn)

    if not sensitive_files:
        title_lower = pr_title.lower()
        if SENSITIVE_PATHS.search(title_lower):
            sensitive_count = 1
            sensitive_files.append("sensitive_module")

    # Reviewer resolution
    reviewer = pr_data.get("_sim_reviewer") or pr_data.get("reviewer") or ""
    if not reviewer and reviews:
        # Prioritize an approved review first
        for rv in reviews:
            if rv.get("state") == "APPROVED":
                rev_user = rv.get("user", {}).get("login", "")
                if rev_user and not (rev_user.endswith("[bot]") or rev_user.endswith("-bot")):
                    reviewer = rev_user
                    break
        if not reviewer:
            for rv in reviews:
                rev_user = rv.get("user", {}).get("login", "")
                if rev_user and not (rev_user.endswith("[bot]") or rev_user.endswith("-bot")):
                    reviewer = rev_user
                    break
        if not reviewer and reviews:
            reviewer = reviews[0].get("user", {}).get("login", "")
    if not reviewer and pr_data.get("requested_reviewers"):
        reviewer = pr_data["requested_reviewers"][0].get("login", "")
    if not reviewer and pr_data.get("assignees"):
        for a in pr_data["assignees"]:
            if a.get("login") and a.get("login") != pr_author:
                reviewer = a.get("login")
                break
    if not reviewer:
        reviewer = (pr_data.get("assignee") or {}).get("login", "")
    if not reviewer and pr_data.get("merged_by"):
        mb = (pr_data.get("merged_by") or {}).get("login", "")
        if mb and mb != pr_author:
            reviewer = mb
    if reviewer == "collaborator":
        reviewer = ""

    # ─────────────────────────────────────────────────────────────────
    # Discrepancy signals (used by all three models)
    # ─────────────────────────────────────────────────────────────────

    # Signal: no meaningful reviewer assigned
    no_reviewer = (not reviewer and not reviews)

    # Signal: rapid merge — merged < 10 minutes after opening
    rapid_merge = (
        review_duration_seconds > 0
        and review_duration_seconds < 600
        and is_merged
    )

    # Signal: stale PR — open for > 30 days (complacency / context loss)
    stale_pr = (state == "open" and pr_age_days > 30)

    # Signal: WIP/DO-NOT-MERGE in title but merged
    wip_pattern = re.compile(r"\b(wip|do\s*not\s*merge|draft|dnm|blocked|hold)\b", re.I)
    wip_not_draft = bool(wip_pattern.search(pr_title) and is_merged)

    # Signal: mass file touch — shotgun commit touching > 15 files
    mass_file_touch = changed_files > 15

    # Signal: empty or minimal PR description (< 30 chars) for large diff
    empty_description = len(pr_body) < 30 and total_lines > 100

    # Signal: self-review — author and reviewer share the same login
    self_review = bool(reviewer and pr_author and reviewer.lower() == pr_author.lower())

    # AI authorship signal (from commit message or body)
    ai_pattern = re.compile(
        r"(copilot|cursor|chatgpt|gpt-4|gpt4|claude|gemini|devin|aider|sweep|cody"
        r"|autocomplete|ai-generated|auto-generated|llm|openai|codewhisperer)",
        re.I
    )
    ai_authored = bool(
        ai_pattern.search(pr_title)
        or ai_pattern.search(pr_body[:1000])
        or ai_pattern.search(
            pr_data.get("head", {}).get("ref", "")
            if isinstance(pr_data.get("head"), dict) else ""
        )
    )

    # ─────────────────────────────────────────────────────────────────
    # Simulated profiles (demo catalog — preserved as-is)
    # ─────────────────────────────────────────────────────────────────
    sim_profile = pr_data.get("_sim_profile")
    if sim_profile == "high":
        change_risk = round(0.84 + ((pr_number * 17) % 7) * 0.01, 4)
        review_confidence = round(0.16 + ((pr_number * 13) % 6) * 0.01, 4)
        residual_risk = round(change_risk * (1.0 - review_confidence), 4)
        re_queued = True
        depth_score = round(review_confidence * 0.65, 3)
        time_adequacy = round(review_confidence * 0.70, 3)
        attention_state = 0.22
        reviewer_familiarity = 0.30
        rubber_stamp_count = 0
        has_changes_requested = False
    elif sim_profile == "medium":
        change_risk = round(0.63 + ((pr_number * 19) % 8) * 0.012, 4)
        review_confidence = round(0.39 + ((pr_number * 11) % 6) * 0.012, 4)
        residual_risk = round(change_risk * (1.0 - review_confidence), 4)
        re_queued = False
        depth_score = round(review_confidence * 0.85, 3)
        time_adequacy = round(review_confidence * 0.80, 3)
        attention_state = 0.50
        reviewer_familiarity = 0.60
        rubber_stamp_count = 0
        has_changes_requested = False
    elif sim_profile == "low":
        change_risk = round(0.14 + ((pr_number * 23) % 10) * 0.015, 4)
        review_confidence = round(0.74 + ((pr_number * 7) % 8) * 0.018, 4)
        residual_risk = round(change_risk * (1.0 - review_confidence), 4)
        re_queued = False
        depth_score = round(review_confidence * 0.95, 3)
        time_adequacy = round(review_confidence * 0.90, 3)
        attention_state = 0.75
        reviewer_familiarity = 0.85
        rubber_stamp_count = 0
        has_changes_requested = False
    else:
        # ─────────────────────────────────────────────────────────────
        # Model 1 — Change Risk (XGBoost heuristic proxy)
        # ─────────────────────────────────────────────────────────────
        # Log-scale size: prevents 600-line diff == 3000-line diff
        size_score = min(math.log1p(total_lines) / math.log1p(800), 1.0)

        # Path sensitivity: base 0.55 + 0.10 per sensitive file (capped)
        path_score = (
            min(0.55 + sensitive_count * 0.10, 1.0)
            if sensitive_count > 0 else 0.04
        )

        # File breadth: saturates at 15 files
        file_score = min(changed_files / 15.0, 1.0)

        # Base change risk from structural signals
        change_risk = round(
            0.38 * path_score + 0.40 * size_score + 0.22 * file_score, 4
        )

        # Apply discrepancy signal adjustments to change risk
        if no_reviewer:
            change_risk = min(change_risk + 0.12, 0.96)
        if rapid_merge:
            change_risk = min(change_risk + 0.10, 0.96)
        if stale_pr:
            change_risk = min(change_risk + 0.07, 0.96)
        if wip_not_draft:
            change_risk = min(change_risk + 0.15, 0.96)
        if mass_file_touch:
            change_risk = min(change_risk + 0.08, 0.96)
        if empty_description and total_lines > 200:
            change_risk = min(change_risk + 0.06, 0.96)
        if self_review:
            change_risk = min(change_risk + 0.14, 0.96)
        if ai_authored:
            change_risk = min(change_risk + 0.05, 0.96)

        change_risk = round(max(min(change_risk, 0.96), 0.04), 4)

        # ─────────────────────────────────────────────────────────────
        # Model 2 — Review Depth (DistilBERT heuristic proxy)
        # ─────────────────────────────────────────────────────────────
        total_comments = len(comments) + (pr_data.get("review_comments", 0) or 0)
        kloc = max(total_lines / 1000.0, 0.1)
        comment_density = min(total_comments / (kloc * 5.0), 1.0)

        # Expanded rubber stamp detection (covers emoji, whitespace, markdown, phrases)
        _RUBBER_STAMP_EXACT = frozenset({
            "", "lgtm", "lgtm!", "lgtm :+1:", "looks good", "looks good to me",
            "looks good!", "approved", "approve", "+1", "ack", "ship it", "shipit",
            ":+1:", ":thumbsup:", "👍", "✅", "ok", "ok.", "ok!", "okay", "yes",
            "done", "merged", "merge", "sure", "fine", "wfm", "great",
            "no issues", "no comments", "no notes", "seems fine", "seems good",
        })

        def _is_rubber_stamp(body: str) -> bool:
            cleaned = re.sub(r"[^\w\s]", " ", (body or "").strip().lower())
            cleaned = " ".join(cleaned.split())
            return bool(
                cleaned in _RUBBER_STAMP_EXACT
                or len(cleaned) < 4
                or re.fullmatch(r"[\s\W]+", body or "")
            )

        # Keyword-based comment classification (Model 2 depth classes)
        _DEPTH_KEYWORDS = {
            "security": re.compile(
                r"\b(xss|sql.inject|injection|auth|authn|csrf|cors|vuln|secur|exploit"
                r"|sanitiz|escape|privilege|escalat|overfl|cve|owasp)\b", re.I),
            "architecture": re.compile(
                r"\b(architect|design|pattern|solid|coupling|cohesion|abstract|interfac"
                r"|depend|layer|module|concern|refactor|extract|separate|decouple)\b", re.I),
            "logic_concern": re.compile(
                r"\b(bug|wrong|incorrect|broken|fail|error|issue|problem|crash"
                r"|undefined|race|deadlock|leak|null|none|exception|throw|off.by)\b", re.I),
            "test": re.compile(
                r"\b(test|assert|coverage|mock|stub|fixture|spec|unit|integr|e2e|pytest)\b",
                re.I),
            "nit_style": re.compile(
                r"\b(nit|style|format|indent|whitespace|typo|spell|lint|naming|rename"
                r"|casing|camelcase|snakecase|const|var|unused|import)\b", re.I),
        }
        _DEPTH_WEIGHTS_MAP = {
            "security": 1.00, "architecture": 0.90, "logic_concern": 0.80,
            "test": 0.55, "clarifying": 0.45, "nit_style": 0.15, "rubber_stamp": 0.00,
        }

        def _classify_comment(body: str) -> tuple[str, float]:
            if _is_rubber_stamp(body):
                return "rubber_stamp", 0.00
            for cls, pattern in _DEPTH_KEYWORDS.items():
                if pattern.search(body):
                    return cls, _DEPTH_WEIGHTS_MAP[cls]
            return "clarifying", 0.45

        # Classify all review comments
        classified_comments = [
            {
                "body": c.get("body", ""),
                **dict(zip(("class_name", "weight"), _classify_comment(c.get("body", "")))),
                "path": c.get("path"),
                "line": c.get("line"),
            }
            for c in comments[:5]
        ]

        # Review event analysis
        has_approvals = False
        has_changes_requested = False
        has_dismissed = False
        all_bots = True
        reviewer_logins: set[str] = set()
        rubber_stamp_count = 0

        for r in reviews:
            review_state = r.get("state", "")
            review_body = r.get("body") or ""
            review_login = (r.get("user") or {}).get("login", "")
            is_bot = review_login.endswith("[bot]") or review_login.endswith("-bot")

            if not is_bot:
                all_bots = False
            if review_login:
                reviewer_logins.add(review_login)

            if review_state == "APPROVED":
                has_approvals = True
                if _is_rubber_stamp(review_body):
                    rubber_stamp_count += 1
            elif review_state == "CHANGES_REQUESTED":
                has_changes_requested = True
            elif review_state == "DISMISSED":
                has_dismissed = True

        total_reviews = max(len(reviews), 1)
        substantive_ratio = max(len(reviews) - rubber_stamp_count, 0) / total_reviews if reviews else 0.0

        # Depth from review events
        if is_closed_unmerged:
            # PR closed without merging: lack of deep review is expected since code was abandoned/rejected
            depth_from_reviews = 0.50
        elif not reviews:
            depth_from_reviews = 0.05
        elif all_bots and reviews:
            depth_from_reviews = 0.05
        elif has_changes_requested and is_merged:
            depth_from_reviews = max(substantive_ratio * 0.50, 0.05)
        elif has_dismissed and has_approvals:
            depth_from_reviews = max(substantive_ratio * 0.60, 0.10)
        else:
            depth_from_reviews = substantive_ratio

        # Comment depth from classified comments
        if classified_comments:
            comment_weights = [c["weight"] for c in classified_comments]
            comment_depth_avg = sum(comment_weights) / len(comment_weights)
            max_comment_weight = max(comment_weights)
            comment_depth_score = max(comment_depth_avg, max_comment_weight * 0.5)
        else:
            comment_depth_score = 0.0

        depth_score = round(
            0.45 * comment_density
            + 0.35 * depth_from_reviews
            + 0.20 * comment_depth_score,
            4
        )
        if has_changes_requested and is_merged:
            depth_score = round(max(depth_score - 0.20, 0.02), 4)
        depth_score = round(max(min(depth_score, 1.0), 0.0), 4)

        # ─────────────────────────────────────────────────────────────
        # Model 3 — Attention Baseline (Robust z-score heuristic proxy)
        # ─────────────────────────────────────────────────────────────
        attention_state = 1.0

        adequate_secs = max(total_lines * 1.5, 180)
        time_adequacy = round(min(review_duration_seconds / adequate_secs, 1.0), 4)

        if time_adequacy < 0.30:
            attention_state -= 0.35
        elif time_adequacy < 0.60:
            attention_state -= 0.18

        if rubber_stamp_count > 0 and reviews:
            attention_state -= 0.20 * (rubber_stamp_count / total_reviews)

        if merge_hour >= 23 or merge_hour < 6:
            attention_state -= 0.15
        if is_weekend_merge and sensitive_count > 0:
            attention_state -= 0.10
        if not is_closed_unmerged:
            if not reviews:
                attention_state -= 0.30
            if rapid_merge:
                attention_state -= 0.25
            if self_review:
                attention_state -= 0.30
            if stale_pr:
                attention_state -= 0.08

        attention_state = round(max(0.04, min(1.0, attention_state)), 4)

        # Reviewer familiarity
        _SENIOR_ROLES = frozenset({
            "core", "maintainer", "lead", "dev-lead", "tech-lead", "owner",
            "admin", "principal", "architect", "senior", "staff",
        })
        reviewer_familiarity = 0.70 if any(r in reviewer.lower() for r in _SENIOR_ROLES) else 0.35
        if len(reviewer_logins) >= 2:
            reviewer_familiarity = min(reviewer_familiarity + 0.15, 1.0)
        if no_reviewer:
            reviewer_familiarity = 0.10

        # ─────────────────────────────────────────────────────────────
        # Residual risk composition
        # ─────────────────────────────────────────────────────────────
        review_confidence = round(
            0.40 * depth_score
            + 0.25 * time_adequacy
            + 0.25 * attention_state
            + 0.10 * reviewer_familiarity,
            4,
        )
        review_confidence = round(max(min(review_confidence, 0.95), 0.04), 4)
        residual_risk = round(change_risk * (1.0 - review_confidence), 4)
        residual_risk = round(max(min(residual_risk, 0.96), 0.02), 4)
        re_queued = residual_risk >= 0.65

    risk_tier = _risk_tier(residual_risk)

    # ─────────────────────────────────────────────────────────────────
    # Rule-based narrative explanation
    # ─────────────────────────────────────────────────────────────────
    mins = review_duration_seconds // 60
    dur_str = f"{mins}m" if mins > 0 else f"{review_duration_seconds}s"

    _findings: list[str] = []
    if sensitive_count > 0:
        _findings.append(f"{sensitive_count} sensitive path(s)")
    if wip_not_draft:
        _findings.append("WIP merged")
    if rapid_merge:
        _findings.append(f"merged in {dur_str}")
    if no_reviewer:
        _findings.append("no reviewer assigned")
    if self_review:
        _findings.append("self-approved")
    if not sim_profile:
        if rubber_stamp_count > 0:
            _findings.append(f"{rubber_stamp_count} rubber-stamp approval(s)")
        if has_changes_requested:
            if is_merged:
                _findings.append("merged with pending change requests")
            else:
                _findings.append("changes requested, unmerged")
        elif is_closed_unmerged:
            _findings.append("closed without merging")
    _finding_str = "; ".join(_findings) if _findings else "standard change"

    if state == "open":
        if re_queued:
            explanation = (
                f"Active PR #{pr_number}: {total_lines}-line diff across {changed_files} file(s) "
                f"({_finding_str}). "
                f"Residual risk {residual_risk:.2f} exceeds 0.65 — senior re-review required before merge."
            )
        elif risk_tier == "medium":
            explanation = (
                f"Active PR #{pr_number}: {total_lines}-line diff in review ({_finding_str}). "
                f"Residual risk {residual_risk:.2f} is moderate — validation recommended before merge."
            )
        else:
            explanation = (
                f"Active PR #{pr_number}: {total_lines}-line diff open for {dur_str} ({_finding_str}). "
                f"Residual risk {residual_risk:.2f} is low — review depth is within safety bounds."
            )
    elif is_closed_unmerged:
        explanation = (
            f"Closed PR #{pr_number}: {total_lines}-line diff ({_finding_str}) "
            f"closed without merging on {dur_str}. No production deployment risk."
        )
    else:
        if re_queued:
            explanation = (
                f"Merged PR #{pr_number}: {total_lines}-line diff touching {changed_files} file(s) "
                f"({_finding_str}) merged with {review_confidence*100:.0f}% confidence. "
                f"Residual risk {residual_risk:.2f} triggers re-queue for retrospective safety audit."
            )
        elif risk_tier == "medium":
            explanation = (
                f"Merged PR #{pr_number}: {total_lines}-line change reviewed in {dur_str} "
                f"({_finding_str}). Residual risk {residual_risk:.2f} is medium — standard sign-off coverage."
            )
        else:
            explanation = (
                f"Merged PR #{pr_number}: {total_lines}-line change reviewed in {dur_str} "
                f"({review_confidence*100:.0f}% confidence, {_finding_str}). "
                f"Residual risk {residual_risk:.2f} is safely resolved."
            )

    # ─────────────────────────────────────────────────────────────────
    # Top features — actual signal contributors
    # ─────────────────────────────────────────────────────────────────
    top_features: list[dict] = []
    top_features.append({
        "feature": "diff_size",
        "contribution": round(min(math.log1p(total_lines) / math.log1p(800), 1.0) * 0.40, 3),
    })
    if depth_score < 0.35:
        top_features.append({
            "feature": "shallow_review_depth",
            "contribution": round((1.0 - depth_score) * 0.35, 3),
        })
    if sensitive_count > 0:
        top_features.append({
            "feature": "sensitive_paths",
            "contribution": round(sensitive_count * 0.10 * change_risk, 3),
        })
    if no_reviewer:
        top_features.append({"feature": "no_reviewer_assigned", "contribution": 0.12})
    if rapid_merge:
        top_features.append({"feature": "rapid_merge", "contribution": 0.10})
    if self_review:
        top_features.append({"feature": "self_review", "contribution": 0.14})
    if wip_not_draft:
        top_features.append({"feature": "wip_merged", "contribution": 0.15})
    if empty_description:
        top_features.append({"feature": "empty_description", "contribution": 0.06})
    if mass_file_touch:
        top_features.append({"feature": "mass_file_touch", "contribution": 0.08})
    if ai_authored:
        top_features.append({"feature": "ai_authored", "contribution": 0.05})
    top_features.sort(key=lambda x: x["contribution"], reverse=True)
    top_features = top_features[:5]

    status = "re_queued" if re_queued else ("active" if state == "open" else "closed")
    html_url = pr_data.get("html_url") or f"https://github.com/{repo_full}/pull/{pr_number}"

    # Comment display list
    if not sim_profile and classified_comments:
        display_comments = classified_comments
    elif not sim_profile and comments:
        display_comments = [
            {
                "body": c.get("body", ""),
                **dict(zip(("class_name", "weight"), _classify_comment(c.get("body", "")))),
                "path": c.get("path"),
                "line": c.get("line"),
            }
            for c in comments[:5]
        ]
    elif re_queued:
        display_comments = [{"body": "LGTM", "class_name": "rubber_stamp", "weight": 0.0, "path": None}]
    else:
        display_comments = [{"body": f"Review active on #{pr_number}", "class_name": "general", "weight": 0.35, "path": None}]

    scored_dict = {
        "pr_key": f"{repo_full}#{pr_number}",
        "pr_url": html_url,
        "repo": repo_full,
        "pr_number": pr_number,
        "title": pr_title or pr_data.get("title", ""),
        "author": pr_author,
        "reviewer": reviewer,
        "created_at": created_at,
        "merged_at": merged_at,
        "closed_at": closed_at,
        "is_merged": is_merged,
        "state": state,
        "scored": True,
        "change_risk": change_risk,
        "review_confidence": review_confidence,
        "residual_risk": residual_risk,
        "depth_score": depth_score,
        "attention_state": attention_state,
        "time_adequacy": time_adequacy,
        "reviewer_familiarity": reviewer_familiarity,
        "status": status,
        "re_queued": re_queued,
        "explanation": explanation,
        "bedrock_status": "disconnected",   # Bedrock is currently unavailable
        "bedrock_explanation": None,        # Set by explain/ when Bedrock is reachable
        "top_features": top_features,
        "comments": display_comments,
        "diff_lines": total_lines,
        "review_duration_seconds": review_duration_seconds,
        "consecutive_reviews": 1 if re_queued else 0,
        "risk_tier": risk_tier,
        "confidence_pct": int(review_confidence * 100),
        "residual_pct": int(residual_risk * 100),
        "change_risk_pct": int(change_risk * 100),
        "sensitive_files": sensitive_files[:5],
        "additions": additions,
        "deletions": deletions,
        "changed_files": changed_files,
        "body": pr_body,
        "conversation": pr_data.get("conversation", []),
        "comments_count": pr_data.get("comments_count", pr_data.get("_comments_count", len(comments))),
        "live": True,
    }

    _cache_scored_pr(scored_dict)
    return scored_dict


def _cache_scored_pr(pr_dict: dict) -> None:
    """Store scored PR strictly under repository-scoped keys to avoid cross-repo collision and persist to store."""
    repo = pr_dict.get("repo", "")
    num = pr_dict.get("pr_number", 0)
    if repo and num:
        LIVE_PRS_CACHE[f"{repo}/{num}"] = pr_dict
        LIVE_PRS_CACHE[f"{repo}#{num}"] = pr_dict
        LIVE_PRS_CACHE[f"{repo.lower()}/{num}"] = pr_dict
        LIVE_PRS_CACHE[f"{repo.lower()}#{num}"] = pr_dict
        try:
            store.save_pr(pr_dict)
        except Exception:
            pass


def _is_within_30_days(date_str: str | None) -> bool:
    """Check if date string is within the last 30 days."""
    if not date_str:
        return False
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() <= 30 * 86400
    except Exception:
        return False



def _fetch_and_score_repo(owner: str, repo: str, limit: int = 50, force_refresh: bool = False) -> dict:
    """
    Fetch both closed (merged) and active (open) PRs from GitHub (at least 25 PRs).
    Applies initial scoring to recent window: max(25, count_in_last_30_days).
    Older PRs are cataloged as unscored for on-demand analysis.
    """
    full_repo_name = f"{owner}/{repo}"

    # Return cached if available and not a forced refresh
    if not force_refresh and full_repo_name.lower() in REPO_PRS_CACHE and full_repo_name in FETCHED_REPOS:
        cached_prs = REPO_PRS_CACHE[full_repo_name.lower()]
        scored_only = [p for p in cached_prs if p.get("scored") and p.get("residual_risk") is not None]
        total_scored = len(scored_only)
        high_count = sum(1 for p in scored_only if p.get("risk_tier") == "high")
        medium_count = sum(1 for p in scored_only if p.get("risk_tier") == "medium")
        requeued_count = sum(1 for p in scored_only if p.get("re_queued"))
        avg_res = (sum(p["residual_risk"] for p in scored_only) / total_scored) if total_scored > 0 else 0.0
        return {
            "prs": cached_prs,
            "error": None,
            "repo_meta": FETCHED_REPOS[full_repo_name],
            "stats": {
                "total_scored": total_scored,
                "total_prs": len(cached_prs),
                "high_risk_flagged": high_count,
                "medium_risk_count": medium_count,
                "requeued_today": requeued_count,
                "avg_residual_risk": round(avg_res, 2),
            }
        }

    base = f"https://api.github.com/repos/{owner}/{repo}"

    rate_limited = False
    repo_meta = _github_get(base)
    if isinstance(repo_meta, dict) and repo_meta.get("_rate_limit_exceeded"):
        rate_limited = True
        repo_meta = {
            "full_name": full_repo_name,
            "description": f"Repository {owner}/{repo} on GitHub",
            "stargazers_count": "1.4k",
            "language": "Python" if "py" in repo.lower() else ("TypeScript" if "react" in repo.lower() else "Go"),
            "html_url": f"https://github.com/{owner}/{repo}",
        }
    elif not repo_meta or not isinstance(repo_meta, dict) or "full_name" not in repo_meta:
        return {
            "prs": [],
            "error": f"Repository '{owner}/{repo}' was not found on GitHub. Check spelling or access permissions.",
            "repo_meta": {},
            "stats": _compute_board_stats([]),
        }

    candidates = []
    seen_numbers = set()

    # 1. Fetch closed PRs from GitHub (up to 35)
    if not rate_limited:
        closed_resp = _github_get(
            f"{base}/pulls",
            params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": 35},
        )
        if isinstance(closed_resp, list):
            for p in closed_resp:
                if isinstance(p, dict) and p.get("number") and p["number"] not in seen_numbers:
                    seen_numbers.add(p["number"])
                    candidates.append(p)
        elif isinstance(closed_resp, dict) and closed_resp.get("_rate_limit_exceeded"):
            rate_limited = True

    # 2. Fetch active (open) PRs from GitHub (up to 25)
    if not rate_limited:
        open_resp = _github_get(
            f"{base}/pulls",
            params={"state": "open", "sort": "updated", "direction": "desc", "per_page": 25},
        )
        if isinstance(open_resp, list):
            for p in open_resp:
                if isinstance(p, dict) and p.get("number") and p["number"] not in seen_numbers:
                    seen_numbers.add(p["number"])
                    candidates.append(p)
        elif isinstance(open_resp, dict) and open_resp.get("_rate_limit_exceeded"):
            rate_limited = True


    # 4. Sort candidates chronologically (most recent first)
    candidates.sort(
        key=lambda x: x.get("created_at") or x.get("updated_at") or "",
        reverse=True
    )

    # 5. Process all candidate PRs in parallel for ultra-fast response and 100% synchronized model scores
    def _process_candidate(item):
        idx, pr = item
        num = pr.get("number")
        if not num:
            return None

        full_pr = dict(pr)
        if not rate_limited and "additions" not in pr:
            single_detail = _github_get(f"{base}/pulls/{num}")
            if isinstance(single_detail, dict) and not single_detail.get("_rate_limit_exceeded"):
                full_pr.update(single_detail)

        full_pr["_repo"] = full_repo_name

        reviews = []
        comments = []
        if not rate_limited:
            r_resp = _github_get(f"{base}/pulls/{num}/reviews") or []
            if isinstance(r_resp, list):
                reviews = r_resp
            c_resp = _github_get(f"{base}/pulls/{num}/comments") or []
            if isinstance(c_resp, list):
                comments = c_resp

        scored_item = _score_pr_heuristic(full_pr, reviews, comments)
        scored_item["scored"] = True
        _cache_scored_pr(scored_item)
        return scored_item

    with ThreadPoolExecutor(max_workers=8) as pool:
        all_prs = [p for p in pool.map(_process_candidate, enumerate(candidates[:limit])) if p is not None]

    # Order all PRs chronologically descending so newest PRs appear at the top ("above")
    def _pr_sort_key(p):
        return (p.get("created_at") or "", p.get("pr_number") or 0)

    all_prs.sort(key=_pr_sort_key, reverse=True)
    scored_prs = [p for p in all_prs if p.get("scored") and p.get("residual_risk") is not None]

    # Compute repository stats
    total_scored = len(scored_prs)
    high_count = sum(1 for p in scored_prs if p.get("risk_tier") == "high")
    medium_count = sum(1 for p in scored_prs if p.get("risk_tier") == "medium")
    requeued_count = sum(1 for p in scored_prs if p.get("re_queued"))
    avg_res = (sum(p["residual_risk"] for p in scored_prs) / total_scored) if total_scored > 0 else 0.0

    stats = {
        "total_scored": total_scored,
        "total_prs": len(all_prs),
        "high_risk_flagged": high_count,
        "medium_risk_count": medium_count,
        "requeued_today": requeued_count,
        "avg_residual_risk": round(avg_res, 2),
    }

    lang = repo_meta.get("language") or "Code"
    stars_val = str(repo_meta.get("stargazers_count", repo_meta.get("stars", "—")))
    forks_val = str(repo_meta.get("forks_count", repo_meta.get("forks", "—")))
    open_cnt = len([p for p in all_prs if p.get("state") == "open"])
    closed_cnt = len([p for p in all_prs if p.get("state") == "closed"])

    # Register in catalog
    FETCHED_REPOS[full_repo_name] = {
        "full_name": full_repo_name,
        "owner": owner,
        "repo": repo,
        "description": repo_meta.get("description") or "Public repository on GitHub.",
        "stars": stars_val,
        "forks": forks_val,
        "language": lang,
        "language_color": LANGUAGE_COLORS.get(lang, "#586069"),
        "is_public": True,
        "active_prs_count": open_cnt,
        "closed_prs_count": closed_cnt,
        "avg_residual_risk": round(avg_res, 2),
        "last_fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    REPO_PRS_CACHE[full_repo_name.lower()] = all_prs
    try:
        store.save_repo(FETCHED_REPOS[full_repo_name])
        store.save_prs(all_prs)
    except Exception:
        pass

    return {
        "prs": all_prs,
        "error": None,
        "repo_meta": {
            "full_name": full_repo_name,
            "description": repo_meta.get("description") or "Public repository on GitHub.",
            "stars": stars_val,
            "forks": forks_val,
            "language": lang,
            "url": repo_meta.get("html_url", f"https://github.com/{owner}/{repo}"),
            "open_prs_count": open_cnt,
            "closed_prs_count": closed_cnt,
            "rate_limited": rate_limited,
        },
        "stats": stats,
    }


def _discover_popular_repos(limit: int = 6) -> list[dict]:
    """
    Dynamically discover popular active repositories using GitHub Search API (not hardcoded).
    Falls back gracefully if GitHub API rate limit is exceeded.
    """
    search_url = "https://api.github.com/search/repositories"
    params = {
        "q": "stars:>35000 fork:false archived:false",
        "sort": "stars",
        "order": "desc",
        "per_page": limit,
    }
    data = _github_get(search_url, params=params)
    found_repos = []
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        for item in data["items"][:limit]:
            owner = (item.get("owner") or {}).get("login")
            name = item.get("name")
            if owner and name:
                found_repos.append((owner, name))

    if not found_repos:
        fallback_pairs = [
            ("pallets", "flask"),
            ("fastapi", "fastapi"),
            ("facebook", "react"),
            ("psf", "requests"),
            ("kubernetes", "kubernetes"),
            ("django", "django"),
        ]
        found_repos = fallback_pairs[:limit]

    results = []
    for owner, repo_name in found_repos:
        res = _fetch_and_score_repo(owner, repo_name, limit=50)
        if res.get("repo_meta"):
            results.append(res["repo_meta"])
    return results



DEFAULT_INITIAL_REPO = "ankit-cybertron/Vouch"


def _ensure_vouch_repo_exists() -> dict:
    """Ensure ankit-cybertron/Vouch is present in catalog and persistent store."""
    for key in list(FETCHED_REPOS.keys()):
        if key.lower() == DEFAULT_INITIAL_REPO.lower():
            return FETCHED_REPOS[key]

    try:
        stored_repos = store.get_repos()
        for key, meta in stored_repos.items():
            if key.lower() == DEFAULT_INITIAL_REPO.lower():
                FETCHED_REPOS[DEFAULT_INITIAL_REPO] = dict(meta)
                return FETCHED_REPOS[DEFAULT_INITIAL_REPO]
    except Exception:
        pass

    vouch_meta = {
        "full_name": DEFAULT_INITIAL_REPO,
        "owner": "ankit-cybertron",
        "repo": "Vouch",
        "description": "Multi-model pull request review intelligence & residual risk calibration engine.",
        "stars": "1",
        "forks": "0",
        "language": "Python",
        "language_color": "#3572A5",
        "is_public": True,
        "active_prs_count": 0,
        "closed_prs_count": 3,
        "avg_residual_risk": 0.58,
        "last_fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    FETCHED_REPOS[DEFAULT_INITIAL_REPO] = vouch_meta
    try:
        store.save_repo(vouch_meta)
    except Exception as ex:
        logger.warning(f"Could not persist default Vouch repo: {ex}")
    return vouch_meta


def _init_default_repos() -> None:
    """Populate default repositories catalog and PR caches with persisted repositories."""
    stored_repos = store.get_repos()
    for name, repo_meta in stored_repos.items():
        FETCHED_REPOS[name] = dict(repo_meta)

    _ensure_vouch_repo_exists()

    stored_prs = store.get_prs()
    for pr in stored_prs:
        if "risk_tier" not in pr and "residual_risk" in pr:
            pr["risk_tier"] = _risk_tier(pr["residual_risk"])
        repo_name = pr.get("repo", "")
        if repo_name:
            if repo_name not in REPO_PRS_CACHE:
                REPO_PRS_CACHE[repo_name] = []
            REPO_PRS_CACHE[repo_name].append(pr)
        _cache_scored_pr(pr)


# Preload persisted data into active cache on startup
_init_default_repos()

# ─────────────────────────────────────────────────────────────────────────────
# Routes & Auth State
# ─────────────────────────────────────────────────────────────────────────────

@app.context_processor
def inject_global_vars():
    installation_id = session.get("installation_id")
    has_token = bool(session.get("auth_sid")) or bool(session.get("github_token"))
    is_authenticated = bool(installation_id) or has_token
    auth_info = {
        "is_authenticated": is_authenticated,
        "auth_type": session.get("auth_type", "demo" if not is_authenticated else ("github_app" if installation_id else "token")),
        "user_login": session.get("user_login", ""),
        "user_name": session.get("user_name", session.get("user_login", "")),
        "user_avatar": session.get("user_avatar", ""),
        "rate_limit": session.get("rate_limit", {}),
    }
    from explain.groq_client import is_groq_configured
    groq_configured = is_groq_configured() or bool(session.get("groq_api_key"))

    app_install_url = os.environ.get("GITHUB_APP_INSTALL_URL", "").strip()
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()

    return {
        "repos_count": len(FETCHED_REPOS),
        "all_repos": list(FETCHED_REPOS.values()),
        "auth": auth_info,
        "app_version": get_version_info(),
        "groq_configured": groq_configured,
        "app_install_url": app_install_url,
        "app_configured": github_app_auth.is_configured(),
        "has_oauth": bool(client_id),
    }


@app.route("/")
def landing_page():
    """Main Landing Page introducing Vouch, with GitHub App install & Demo mode entry points."""
    # If user is already authenticated (GitHub App or OAuth/PAT), route directly to repos
    already_authed = (
        session.get("installation_id") and github_app_auth.is_configured()
    ) or ((session.get("auth_sid") or session.get("github_token")) and not request.args.get("reauth"))
    if already_authed and not request.args.get("reauth"):
        return redirect(url_for("repos_page"))

    # If a repo query param was provided directly to root, route to that repo
    repo_arg = request.args.get("repo", "").strip()
    if repo_arg:
        parsed = _parse_repo_input(repo_arg)
        if parsed:
            return repo_view(parsed[0], parsed[1])

    try:
        for name, meta in store.get_repos().items():
            if name not in FETCHED_REPOS:
                FETCHED_REPOS[name] = meta
    except Exception:
        pass

    sample_repos = list(FETCHED_REPOS.values())[:6]
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    oauth_setup = request.args.get("oauth_setup") == "1"
    error = request.args.get("error")

    app_configured = github_app_auth.is_configured()
    app_install_url = os.environ.get("GITHUB_APP_INSTALL_URL", "").strip()
    # Legacy OAuth availability (fallback when GitHub App is not configured)
    has_oauth = bool(client_id) or bool(_resolve_github_token())

    # Pre-compute the exact callback URLs for display in setup instructions
    computed_oauth_callback_uri = _oauth_callback_uri()
    computed_app_callback_uri = _app_setup_callback_uri()

    raw_yt = os.environ.get("YOUTUBE_DEMO_URL", "").strip() or "https://youtu.be/m5iM3ArUbz0"
    vid_match = re.search(r"(?:v=|\/embed\/|youtu\.be\/|\/v\/|watch\?v=)([a-zA-Z0-9_-]{11})", raw_yt)
    youtube_embed_url = f"https://www.youtube.com/embed/{vid_match.group(1)}" if vid_match else raw_yt
    youtube_url = raw_yt

    return render_template(
        "landing.html",
        sample_repos=sample_repos,
        has_oauth=has_oauth,
        oauth_setup=oauth_setup,
        error=error,
        app_configured=app_configured,
        app_install_url=app_install_url,
        computed_oauth_callback_uri=computed_oauth_callback_uri,
        computed_app_callback_uri=computed_app_callback_uri,
        youtube_url=youtube_url,
        youtube_embed_url=youtube_embed_url,
    )


@app.route("/repos")
def repos_page():
    """Repositories catalog page — displays all fetched repositories with '+' fetch option."""
    repo_arg = request.args.get("repo", "").strip()
    if repo_arg:
        parsed = _parse_repo_input(repo_arg)
        if parsed:
            return repo_view(parsed[0], parsed[1])

    _ensure_vouch_repo_exists()

    try:
        for name, meta in store.get_repos().items():
            if name not in FETCHED_REPOS:
                FETCHED_REPOS[name] = meta
    except Exception:
        pass

    search = request.args.get("q", "").strip().lower()
    repos_list = list(FETCHED_REPOS.values())
    if search:
        repos_list = [
            r for r in repos_list
            if search in r.get("full_name", "").lower()
            or search in r.get("description", "").lower()
            or search in r.get("language", "").lower()
        ]

    for r in repos_list:
        r.setdefault("avg_residual_risk", 0.0)
        r.setdefault("active_prs_count", 0)
        r.setdefault("closed_prs_count", 0)

    # Ensure ankit-cybertron/Vouch is always prioritized and sorted first
    vouch_key = DEFAULT_INITIAL_REPO.lower()
    repos_list.sort(key=lambda r: 0 if r.get("full_name", "").lower() == vouch_key else 1)


    session_repos = session.get("session_repos") or []
    active_repo = session.get("active_repo")
    if active_repo and active_repo not in session_repos:
        session_repos.append(active_repo)
        session["session_repos"] = session_repos

    return render_template(
        "repos.html",
        repos=repos_list,
        search=search,
        initial_repo_name=DEFAULT_INITIAL_REPO,
        session_repos=session_repos,
        has_session_repos=len(session_repos) > 0,
    )


# ── GitHub App Authentication Routes ───────────────────────────
@app.route("/auth/github/app/install")
def auth_github_app_install():
    """Redirect user to GitHub App installation page."""
    install_url = os.environ.get("GITHUB_APP_INSTALL_URL", "").strip()
    if not install_url:
        return redirect(url_for("landing_page", error=(
            "GITHUB_APP_INSTALL_URL is not configured. "
            "Set it to your GitHub App's installation URL."
        )))
    return redirect(install_url)


@app.route("/auth/github/app/callback")
def auth_github_app_callback():
    """
    GitHub App post-installation callback.

    GitHub redirects here after a user installs (or updates) the Vouch GitHub App,
    passing:
      - installation_id : numeric ID of the new installation
      - setup_action    : "install" | "update" | "request"
      - state           : opaque CSRF token we stored in the session
    """
    installation_id = request.args.get("installation_id", "").strip()
    setup_action = request.args.get("setup_action", "install")
    state = request.args.get("state", "")
    saved_state = session.pop("app_install_state", None)

    # Validate CSRF state when we generated one
    if saved_state and state != saved_state:
        return redirect(url_for("landing_page", error="GitHub App installation state mismatch. Please try again."))

    if not installation_id:
        return redirect(url_for("landing_page", error="No installation_id received from GitHub."))

    if not github_app_auth.is_configured():
        return redirect(url_for("landing_page", error="GitHub App is not configured on this server."))

    # Exchange installation_id for a token immediately to validate it works
    try:
        token = github_app_auth.get_installation_token(int(installation_id))
        # Fetch authenticated user via /user — not available on App tokens directly,
        # so we use /app/installations/{id} to get account metadata.
        app_jwt = github_app_auth._build_jwt()
        inst_resp = requests.get(
            f"https://api.github.com/app/installations/{installation_id}",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
        account = {}
        if inst_resp.status_code == 200:
            account = inst_resp.json().get("account", {})

        session["installation_id"] = int(installation_id)
        session["auth_type"] = "github_app"
        session["setup_action"] = setup_action
        session["user_login"] = account.get("login", "github_app_user")
        session["user_name"] = account.get("name") or account.get("login", "GitHub App")
        session["user_avatar"] = account.get("avatar_url", "")
        session["rate_limit"] = {"limit": 5000, "remaining": 5000}

        return redirect(url_for("repos_page", from_auth="1"))
    except Exception as exc:
        return redirect(url_for("landing_page", error=f"GitHub App token exchange failed: {exc}"))


# ── GitHub OAuth Authentication Routes (legacy / fallback) ─────────────────
@app.route("/auth/github")
def auth_github():
    """Initiate GitHub OAuth 2.0 flow or auto-connect using configured GitHub token."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()

    # If full OAuth client is configured, run standard GitHub OAuth 2.0 redirect
    if client_id and client_secret:
        state = secrets.token_urlsafe(16)
        session["oauth_state"] = state
        # Use APP_BASE_URL when deployed (Elastic Beanstalk / reverse proxy)
        redirect_uri = _oauth_callback_uri()

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user repo",
            "state": state,
        }

        github_auth_url = (
            "https://github.com/login/oauth/authorize?"
            + urlencode(params)
        )
        return redirect(github_auth_url)

    # Seamless automatic connect: If OAuth app is not registered on GitHub yet,
    # connect the user's GitHub ID directly using their available GitHub token
    existing_token = _resolve_github_token()
    if existing_token:
        try:
            user_resp = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {existing_token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if user_resp.status_code == 200:
                user_data = user_resp.json()
                rate_resp = requests.get(
                    "https://api.github.com/rate_limit",
                    headers={
                        "Authorization": f"Bearer {existing_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=10,
                )
                rate_data = rate_resp.json().get("rate", {}) if rate_resp.status_code == 200 else {}
                auth_sid = session_store.create_session(
                    token=existing_token,
                    user_login=user_data.get("login", "github_user"),
                    auth_type="oauth",
                )
                session["auth_sid"] = auth_sid
                session.pop("github_token", None)
                session["user_login"] = user_data.get("login", "github_user")
                session["user_name"] = user_data.get("name") or user_data.get("login", "GitHub User")
                session["user_avatar"] = user_data.get("avatar_url", "")
                session["auth_type"] = "oauth"
                session["rate_limit"] = {
                    "limit": rate_data.get("limit", 5000),
                    "remaining": rate_data.get("remaining", 5000),
                }
                return redirect(url_for("repos_page", from_auth="1"))
            else:
                return redirect(url_for("landing_page", error="Could not authenticate with GitHub token. Please verify your token."))
        except Exception as exc:
            return redirect(url_for("landing_page", error=f"GitHub connection error: {str(exc)}"))

    return redirect(url_for("landing_page", oauth_setup="1"))


@app.route("/auth/github/callback")
def auth_github_callback():
    """GitHub OAuth 2.0 callback endpoint."""
    code = request.args.get("code")
    state = request.args.get("state")
    saved_state = session.pop("oauth_state", None)

    if not code or state != saved_state:
        return redirect(url_for("landing_page", error="OAuth state validation failed. Please try again."))

    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()

    try:
        # Use APP_BASE_URL when deployed so redirect_uri matches what GitHub expects
        redirect_uri = _oauth_callback_uri()
        token_resp = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        if token_resp.status_code != 200:
            return redirect(url_for("landing_page", error="Failed to exchange authorization code with GitHub."))

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            err_desc = token_data.get("error_description", "GitHub did not return an access token.")
            return redirect(url_for("landing_page", error=err_desc))

        user_resp = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        user_data = user_resp.json() if user_resp.status_code == 200 else {}

        rate_resp = requests.get(
            "https://api.github.com/rate_limit",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        rate_data = rate_resp.json().get("rate", {}) if rate_resp.status_code == 200 else {}

        # Store OAuth token securely in server-side session store
        auth_sid = session_store.create_session(
            token=access_token,
            user_login=user_data.get("login", "github_user"),
            auth_type="oauth",
        )
        session["auth_sid"] = auth_sid
        session.pop("github_token", None)
        session["user_login"] = user_data.get("login", "github_user")
        session["user_name"] = user_data.get("name") or user_data.get("login", "GitHub User")
        session["user_avatar"] = user_data.get("avatar_url", "")
        session["auth_type"] = "oauth"
        session["rate_limit"] = {
            "limit": rate_data.get("limit", 5000),
            "remaining": rate_data.get("remaining", 5000),
        }

        return redirect(url_for("repos_page", from_auth="1"))
    except Exception as exc:
        return redirect(url_for("landing_page", error=f"OAuth connection error: {str(exc)}"))


@app.route("/auth/logout")
def auth_logout():
    """Clear user session and return to landing page."""
    auth_sid = session.pop("auth_sid", None)
    if auth_sid:
        session_store.delete_session(auth_sid)
    session.pop("github_token", None)
    session.pop("installation_id", None)
    session.pop("setup_action", None)
    session.pop("user_login", None)
    session.pop("user_name", None)
    session.pop("user_avatar", None)
    session.pop("auth_type", None)
    session.pop("rate_limit", None)
    session.pop("session_repos", None)
    session.pop("active_repo", None)
    session.pop("active_reviewer", None)
    session.clear()
    return redirect(url_for("landing_page"))


# ── Option 4: In-App UI Personal Access Token API (Fallback) ─────
@app.route("/api/auth/token", methods=["POST"])
def api_save_token():
    """Validate and store Personal Access Token (classic ghp_ or fine-grained github_pat_)."""
    payload = request.get_json(silent=True) or {}
    raw_token = payload.get("token", "").strip()
    if not raw_token:
        return jsonify({"success": False, "error": "Token cannot be empty"}), 400

    try:
        # Validate token against GitHub API
        user_resp = requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=8,
        )
        if user_resp.status_code != 200:
            return jsonify({
                "success": False,
                "error": "Invalid GitHub token. GitHub rejected the token (HTTP 401). Please verify permissions.",
            }), 400

        user_data = user_resp.json()
        rate_resp = requests.get(
            "https://api.github.com/rate_limit",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=8,
        )
        rate_data = rate_resp.json().get("rate", {}) if rate_resp.status_code == 200 else {}

        # Store PAT securely in server-side session store
        auth_sid = session_store.create_session(
            token=raw_token,
            user_login=user_data.get("login", "github_user"),
            auth_type="token",
        )

        # Reset any prior GitHub App installation from session so PAT is used
        session.pop("installation_id", None)
        session.pop("setup_action", None)
        session.pop("github_token", None)  # Ensure raw token is never stored in client cookie
        session["auth_sid"] = auth_sid
        session["user_login"] = user_data.get("login", "github_user")
        session["user_name"] = user_data.get("name") or user_data.get("login", "GitHub User")
        session["user_avatar"] = user_data.get("avatar_url", "")
        session["auth_type"] = "token"
        session["rate_limit"] = {
            "limit": rate_data.get("limit", 5000),
            "remaining": rate_data.get("remaining", 5000),
        }

        return jsonify({
            "success": True,
            "message": f"Connected as @{session['user_login']}",
            "user": {
                "login": session["user_login"],
                "name": session["user_name"],
                "avatar_url": session["user_avatar"],
            },
            "rate_limit": session["rate_limit"],
        })
    except requests.exceptions.RequestException:
        return jsonify({"success": False, "error": "Connection error: Unable to reach GitHub API."}), 502
    except Exception:
        return jsonify({"success": False, "error": "Unexpected error validating token."}), 500


@app.route("/api/auth/clear-token", methods=["POST"])
def api_clear_token():
    """Reset session to default demo mode."""
    auth_sid = session.pop("auth_sid", None)
    if auth_sid:
        session_store.delete_session(auth_sid)
    session.pop("github_token", None)
    session.pop("installation_id", None)
    session.pop("setup_action", None)
    session.pop("user_login", None)
    session.pop("user_name", None)
    session.pop("user_avatar", None)
    session.pop("auth_type", None)
    session.pop("rate_limit", None)
    return jsonify({"success": True, "message": "Reset to Demo Mode"})


@app.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """Return current session auth state."""
    installation_id = session.get("installation_id")
    has_token = bool(session.get("auth_sid")) or bool(session.get("github_token"))
    authenticated = bool(installation_id) or has_token
    auth_type = session.get("auth_type", "demo" if not authenticated else ("github_app" if installation_id else "token"))
    return jsonify({
        "authenticated": authenticated,
        "auth_type": auth_type,
        "app_configured": github_app_auth.is_configured(),
        "installation_id": installation_id,
        "user_login": session.get("user_login", ""),
        "user_name": session.get("user_name", ""),
        "user_avatar": session.get("user_avatar", ""),
        "rate_limit": session.get("rate_limit", {}),
    })


@app.route("/repo/<owner>/<repo_name>")
def repo_view(owner: str, repo_name: str):
    """Repository-scoped PR board showing all its active (open) and closed (merged) PRs."""
    full_name = f"{owner}/{repo_name}"
    session["active_repo"] = full_name
    s_repos = session.get("session_repos") or []
    if full_name not in s_repos:
        s_repos.append(full_name)
        session["session_repos"] = s_repos

    risk_filter = request.args.get("risk", "all")
    state_filter = request.args.get("state", "all")
    search = request.args.get("q", "").strip()

    # Check in-memory cache, then load from store if present
    if full_name.lower() not in REPO_PRS_CACHE:
        try:
            prs_from_store = store.get_prs(repo=full_name)
            if prs_from_store:
                for idx, p in enumerate(prs_from_store):
                    if not p.get("scored") or p.get("residual_risk") is None:
                        scored_p = _score_pr_heuristic(p, p.get("_reviews") or p.get("reviews") or [], p.get("_comments") or p.get("comments") or [])
                        scored_p["scored"] = True
                        _cache_scored_pr(scored_p)
                        prs_from_store[idx] = scored_p
                        try:
                            store.save_pr(scored_p)
                        except Exception:
                            pass
                REPO_PRS_CACHE[full_name.lower()] = prs_from_store
                stored_repos = store.get_repos()
                if full_name in stored_repos:
                    FETCHED_REPOS[full_name] = stored_repos[full_name]
        except Exception:
            pass

    # Use cached or fetch & score
    if full_name.lower() in REPO_PRS_CACHE and full_name in FETCHED_REPOS:
        prs = list(REPO_PRS_CACHE[full_name.lower()])
        cache_updated = False
        for idx, p in enumerate(prs):
            if not p.get("scored") or p.get("residual_risk") is None:
                scored_p = _score_pr_heuristic(p, p.get("_reviews") or p.get("reviews") or [], p.get("_comments") or p.get("comments") or [])
                scored_p["scored"] = True
                _cache_scored_pr(scored_p)
                prs[idx] = scored_p
                cache_updated = True
                try:
                    store.save_pr(scored_p)
                except Exception:
                    pass
        if cache_updated:
            REPO_PRS_CACHE[full_name.lower()] = prs

        current_repo_meta = FETCHED_REPOS[full_name]
        scored_only = [p for p in prs if p.get("scored") and p.get("residual_risk") is not None]
        total = len(scored_only)
        high_count = sum(1 for p in scored_only if p.get("risk_tier") == "high")
        medium_count = sum(1 for p in scored_only if p.get("risk_tier") == "medium")
        requeued_count = sum(1 for p in scored_only if p.get("re_queued"))
        avg_res = (sum(p["residual_risk"] for p in scored_only) / total) if total > 0 else 0.0
        stats = {
            "total_scored": total,
            "total_prs": len(prs),
            "high_risk_flagged": high_count,
            "medium_risk_count": medium_count,
            "requeued_today": requeued_count,
            "avg_residual_risk": round(avg_res, 2),
        }
        FETCHED_REPOS[full_name]["avg_residual_risk"] = stats["avg_residual_risk"]
    else:
        result = _fetch_and_score_repo(owner, repo_name, limit=50)
        prs = result.get("prs", [])
        current_repo_meta = result.get("repo_meta", {})
        stats = result.get("stats") or _compute_board_stats(prs)

    # Keyword or PR number search with on-the-fly fetch
    if search:
        search_stripped = search.lstrip("#").strip()
        if search_stripped.isdigit():
            target_num = int(search_stripped)
            # Check if this PR exists in prs
            found = any(p.get("pr_number") == target_num for p in prs)
            if not found:
                # Auto-fetch directly from GitHub and score it
                auto_fetched, _ = _resolve_pr_detail(owner, repo_name, target_num)
                if auto_fetched:
                    auto_fetched["scored"] = True
                    prs.insert(0, auto_fetched)
                    if full_name.lower() in REPO_PRS_CACHE:
                        REPO_PRS_CACHE[full_name.lower()].insert(0, auto_fetched)
                    if full_name in FETCHED_REPOS:
                        if auto_fetched.get("state") == "open":
                            FETCHED_REPOS[full_name]["active_prs_count"] = FETCHED_REPOS[full_name].get("active_prs_count", 0) + 1
                        else:
                            FETCHED_REPOS[full_name]["closed_prs_count"] = FETCHED_REPOS[full_name].get("closed_prs_count", 0) + 1
                        try:
                            store.save_repo(FETCHED_REPOS[full_name])
                            store.save_pr(auto_fetched)
                        except Exception:
                            pass

    # Pre-filter repository KPI counts
    total_pr_count = len(prs)
    scored_prs_all = [p for p in prs if p.get("scored") and p.get("residual_risk") is not None]
    if not scored_prs_all:
        scored_prs_all = [p for p in prs if p.get("residual_risk") is not None]
    high_risk_count = sum(1 for p in scored_prs_all if p.get("risk_tier") == "high")
    medium_risk_count = sum(1 for p in scored_prs_all if p.get("risk_tier") == "medium")
    avg_residual = round(sum(p["residual_risk"] for p in scored_prs_all) / len(scored_prs_all), 2) if scored_prs_all else 0.0

    # Ensure stats is 100% synchronized with the pre-filter repository totals
    stats = {
        "total_scored": len(scored_prs_all),
        "total_prs": total_pr_count,
        "high_risk_flagged": high_risk_count,
        "medium_risk_count": medium_risk_count,
        "avg_residual_risk": avg_residual,
    }

    # Pre-filter counts for tri-state tabs
    open_count = len([p for p in prs if p.get("state") == "open"])
    merged_count = len([p for p in prs if p.get("state") == "closed" and (p.get("merged_at") or p.get("is_merged"))])
    closed_count = len([p for p in prs if p.get("state") == "closed" and not (p.get("merged_at") or p.get("is_merged"))])

    # Risk level filter
    if risk_filter != "all":
        prs = [p for p in prs if p.get("risk_tier") == risk_filter]

    # State filter (open vs merged vs closed)
    if state_filter == "open":
        prs = [p for p in prs if p.get("state") == "open"]
    elif state_filter == "merged":
        prs = [p for p in prs if p.get("state") == "closed" and (p.get("merged_at") or p.get("is_merged"))]
    elif state_filter == "closed":
        prs = [p for p in prs if p.get("state") == "closed" and not (p.get("merged_at") or p.get("is_merged"))]

    # Keyword or PR number filter
    if search:
        q_lower = search.lower()
        search_num = search.lstrip("#").strip()
        prs = [
            p for p in prs
            if q_lower in p.get("title", "").lower()
            or q_lower in p.get("author", "").lower()
            or (search_num and str(p.get("pr_number", "")) == search_num)
            or q_lower in str(p.get("pr_number", ""))
        ]

    return render_template(
        "board.html",
        prs=prs,
        stats=stats,
        total_pr_count=total_pr_count,
        high_risk_count=high_risk_count,
        medium_risk_count=medium_risk_count,
        avg_residual=avg_residual,
        risk_filter=risk_filter,
        state_filter=state_filter,
        search=search,
        current_repo=current_repo_meta,
        active_repo_name=full_name,
        open_count=open_count,
        merged_count=merged_count,
        closed_count=closed_count,
    )


@app.route("/pulls")
@app.route("/board")
def board():
    """General PR board — clean hero search page when visited directly, or repository PR board."""
    repo_arg = request.args.get("repo", "").strip()
    force_search = request.args.get("view") == "search"
    if repo_arg:
        parsed = _parse_repo_input(repo_arg)
        if parsed:
            return repo_view(parsed[0], parsed[1])

    # If the user already loaded an active repo in this session, keep PR board visible on tab click
    active_repo = session.get("active_repo")
    if not force_search and active_repo:
        parsed = _parse_repo_input(active_repo)
        if parsed:
            return repo_view(parsed[0], parsed[1])

    recent_repos = []
    seen = set()
    for repo_dict in list(FETCHED_REPOS.values()):
        fn = repo_dict.get("full_name") or f"{repo_dict.get('owner')}/{repo_dict.get('repo')}"
        if fn and fn not in seen:
            seen.add(fn)
            recent_repos.append(repo_dict)

    total_prs = sum(len(prs) for prs in REPO_PRS_CACHE.values())
    total_repos = len(FETCHED_REPOS)

    return render_template(
        "board.html",
        mode="search",
        recent_repos=recent_repos[:12],
        total_prs=total_prs,
        total_repos=total_repos,
        current_repo=None,
        active_repo_name=None,
        prs=[],
        stats={"total_scored": 0, "total_prs": total_prs, "high_risk_flagged": 0, "medium_risk_count": 0, "avg_residual_risk": 0},
    )


@app.route("/api/clear-repos", methods=["POST"])
def clear_repos_api():
    """Clear all fetched repositories and pull requests from memory and session."""
    FETCHED_REPOS.clear()
    REPO_PRS_CACHE.clear()
    LIVE_PRS_CACHE.clear()
    session.pop("session_repos", None)
    session.pop("active_repo", None)
    session.pop("active_reviewer", None)
    return jsonify({"success": True, "message": "All fetched repository data cleared."})


def _resolve_pr_detail(org: str, repo_name: str, pr_number: int) -> tuple[dict | None, int]:
    """Finds a PR from live GitHub (with complete conversation, reviews, diffs), or cached/demo catalog."""
    full_repo = f"{org}/{repo_name}"
    full_repo_lower = full_repo.lower()

    # 1. Check if this PR was already scored and stored in memory or persistence
    existing_scored = None
    for key in (
        f"{full_repo_lower}/{pr_number}",
        f"{full_repo_lower}#{pr_number}",
        f"{full_repo}/{pr_number}",
        f"{full_repo}#{pr_number}",
    ):
        if key in LIVE_PRS_CACHE and LIVE_PRS_CACHE[key].get("scored") and LIVE_PRS_CACHE[key].get("residual_risk") is not None:
            existing_scored = dict(LIVE_PRS_CACHE[key])
            break

    if not existing_scored and full_repo_lower in REPO_PRS_CACHE:
        for p in REPO_PRS_CACHE[full_repo_lower]:
            if p.get("pr_number") == pr_number and p.get("scored") and p.get("residual_risk") is not None:
                existing_scored = dict(p)
                break

    if not existing_scored:
        for p in store.get_prs():
            if p.get("repo", "").lower() == full_repo_lower and p.get("pr_number") == pr_number and p.get("scored") and p.get("residual_risk") is not None:
                existing_scored = dict(p)
                break

    # 2. Attempt live fetch directly from GitHub API for complete timeline conversation
    base = f"https://api.github.com/repos/{full_repo}"
    pr_data = _github_get(f"{base}/pulls/{pr_number}")
    if isinstance(pr_data, dict) and "number" in pr_data and not pr_data.get("_rate_limit_exceeded"):
        pr_data["_repo"] = full_repo

        # Fetch conversation components in parallel
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_ic = ex.submit(_github_get, f"{base}/issues/{pr_number}/comments")
            f_rc = ex.submit(_github_get, f"{base}/pulls/{pr_number}/comments")
            f_rv = ex.submit(_github_get, f"{base}/pulls/{pr_number}/reviews")

            issue_comments = f_ic.result() or []
            review_comments = f_rc.result() or []
            reviews = f_rv.result() or []

        if isinstance(issue_comments, dict):
            issue_comments = []
        if isinstance(review_comments, dict):
            review_comments = []
        if isinstance(reviews, dict):
            reviews = []

        # Build chronological conversation timeline
        author_login = (pr_data.get("user") or {}).get("login", "author")
        author_avatar = (pr_data.get("user") or {}).get("avatar_url") or f"https://github.com/{author_login}.png"
        pr_body = (pr_data.get("body") or "").strip()

        conversation = []
        # Initial PR description by author
        conversation.append({
            "type": "description",
            "user": {
                "login": author_login,
                "avatar_url": author_avatar,
                "html_url": f"https://github.com/{author_login}",
            },
            "body": pr_body or "No description provided by author.",
            "created_at": pr_data.get("created_at"),
            "role_badge": "Author",
            "is_author": True,
        })

        # Discussion comments on the PR issue
        for ic in issue_comments:
            if not isinstance(ic, dict) or not ic.get("body"):
                continue
            u = ic.get("user") or {}
            login = u.get("login", "unknown")
            is_auth = (login == author_login)
            is_bot = (u.get("type") == "Bot" or "[bot]" in login.lower() or login.endswith("-bot"))
            badge = "Author" if is_auth else ("Bot" if is_bot else "Contributor")
            conversation.append({
                "type": "comment",
                "user": {
                    "login": login,
                    "avatar_url": u.get("avatar_url") or f"https://github.com/{login}.png",
                    "html_url": u.get("html_url") or f"https://github.com/{login}",
                },
                "body": ic.get("body", "").strip(),
                "created_at": ic.get("created_at"),
                "role_badge": badge,
                "is_author": is_auth,
            })

        # In-line code review comments
        for rc in review_comments:
            if not isinstance(rc, dict) or not rc.get("body"):
                continue
            u = rc.get("user") or {}
            login = u.get("login", "unknown")
            conversation.append({
                "type": "review_comment",
                "user": {
                    "login": login,
                    "avatar_url": u.get("avatar_url") or f"https://github.com/{login}.png",
                    "html_url": u.get("html_url") or f"https://github.com/{login}",
                },
                "body": rc.get("body", "").strip(),
                "path": rc.get("path"),
                "line": rc.get("line") or rc.get("original_line"),
                "diff_hunk": rc.get("diff_hunk"),
                "created_at": rc.get("created_at"),
                "role_badge": "Code Review",
                "is_author": (login == author_login),
            })

        # Formal review submissions (Approvals / Changes Requested)
        for rv in reviews:
            if not isinstance(rv, dict):
                continue
            u = rv.get("user") or {}
            login = u.get("login", "unknown")
            rv_body = (rv.get("body") or "").strip()
            rv_state = (rv.get("state") or "COMMENTED").upper()
            if rv_body or rv_state in ("APPROVED", "CHANGES_REQUESTED"):
                default_msg = "Approved these changes." if rv_state == "APPROVED" else ("Requested changes." if rv_state == "CHANGES_REQUESTED" else "Submitted a review.")
                conversation.append({
                    "type": "review",
                    "user": {
                        "login": login,
                        "avatar_url": u.get("avatar_url") or f"https://github.com/{login}.png",
                        "html_url": u.get("html_url") or f"https://github.com/{login}",
                    },
                    "body": rv_body or default_msg,
                    "review_state": rv_state,
                    "created_at": rv.get("submitted_at") or rv.get("created_at") or pr_data.get("created_at"),
                    "role_badge": "Reviewer",
                    "is_author": False,
                })

        # Sort timeline chronologically (initial description remains first)
        first_desc = conversation[0]
        rest_sorted = sorted(conversation[1:], key=lambda x: x.get("created_at") or "")
        full_conversation = [first_desc] + rest_sorted

        # Compute the LATEST score using live GitHub data (prioritizing latest scoring)
        all_comments = review_comments + issue_comments
        scored = _score_pr_heuristic(pr_data, reviews, all_comments)
        scored["body"] = pr_body
        scored["conversation"] = full_conversation
        scored["comments_count"] = len(issue_comments) + len(review_comments)
        scored["pr_url"] = pr_data.get("html_url") or f"https://github.com/{full_repo}/pull/{pr_number}"
        scored["scored"] = True

        # Synchronize this LATEST score everywhere so board, cache, and persistence are 100% in sync
        _cache_scored_pr(scored)

        if full_repo_lower in REPO_PRS_CACHE:
            updated = False
            for idx, p in enumerate(REPO_PRS_CACHE[full_repo_lower]):
                if p.get("pr_number") == pr_number:
                    REPO_PRS_CACHE[full_repo_lower][idx] = scored
                    updated = True
                    break
            if not updated:
                REPO_PRS_CACHE[full_repo_lower].insert(0, scored)
        else:
            REPO_PRS_CACHE[full_repo_lower] = [scored]

        try:
            store.save_pr(scored)
        except Exception:
            pass

        if full_repo in FETCHED_REPOS:
            prs = REPO_PRS_CACHE.get(full_repo_lower, [])
            scored_only = [p for p in prs if p.get("scored") and p.get("residual_risk") is not None]
            if scored_only:
                avg_res = sum(p["residual_risk"] for p in scored_only) / len(scored_only)
                FETCHED_REPOS[full_repo]["avg_residual_risk"] = round(avg_res, 2)
            try:
                store.save_repo(FETCHED_REPOS[full_repo])
            except Exception:
                pass

        return scored, 200

    # 3. If GitHub was unreachable, return existing scored from cache/storage
    if existing_scored:
        _cache_scored_pr(existing_scored)
        return existing_scored, 200

    return None, 404


@app.route("/pr/<org>/<repo_name>/<int:pr_number>")
def pr_detail_explicit(org: str, repo_name: str, pr_number: int):
    """PR detail view with explicit org, repo, and PR number."""
    pr, code = _resolve_pr_detail(org, repo_name, pr_number)
    if not pr:
        return render_template(
            "404.html",
            message=f"Pull request #{pr_number} from {org}/{repo_name} could not be found."
        ), 404
    return render_template("pr_detail.html", pr=pr)


@app.route("/pr/<path:pr_key>")
def pr_detail_catchall(pr_key: str):
    """
    Handles any PR route format:
      - /pr/kubernetes/kubernetes/128540
      - /pr/kubernetes/kubernetes#128540
      - /pr/kubernetes/kubernetes%23128540
      - /pr/128540
    """
    pr_key = pr_key.strip("/")

    # Check direct cache first
    if pr_key in LIVE_PRS_CACHE:
        return render_template("pr_detail.html", pr=LIVE_PRS_CACHE[pr_key])

    # If it has a # or %23
    cleaned = pr_key.replace("%23", "#")
    if "#" in cleaned:
        repo_part, num_part = cleaned.split("#", 1)
        if "/" in repo_part and num_part.isdigit():
            org, repo_name = repo_part.split("/", 1)
            pr, code = _resolve_pr_detail(org, repo_name, int(num_part))
            if pr:
                return render_template("pr_detail.html", pr=pr)

    # If format is org/repo/number
    parts = cleaned.split("/")
    if len(parts) >= 3 and parts[-1].isdigit():
        org = parts[0]
        repo_name = parts[1]
        pr_number = int(parts[-1])
        pr, code = _resolve_pr_detail(org, repo_name, pr_number)
        if pr:
            return render_template("pr_detail.html", pr=pr)

    # If format is just a number
    if cleaned.isdigit():
        num = int(cleaned)
        for p in list(LIVE_PRS_CACHE.values()):
            if isinstance(p, dict) and p.get("pr_number") == num:
                return render_template("pr_detail.html", pr=p)
        if num in LIVE_PRS_CACHE:
            return render_template("pr_detail.html", pr=LIVE_PRS_CACHE[num])
        stored_prs = store.get_prs()
        for p in stored_prs:
            if p.get("pr_number") == num:
                _cache_scored_pr(p)
                return render_template("pr_detail.html", pr=p)

    # Fallback 404
    return render_template(
        "404.html",
        message=f"Could not locate pull request '{pr_key}'."
    ), 404


def _compute_team_health(repo_filter: str | None = None) -> dict:
    """
    Compute real-time team review health, fatigue index, rubber-stamp rate,
    module-level safety posture, and pacing distributions.
    """
    all_prs: list[dict] = []
    if repo_filter and repo_filter != "all" and repo_filter in REPO_PRS_CACHE:
        all_prs = list(REPO_PRS_CACHE[repo_filter])
    else:
        for r_prs in REPO_PRS_CACHE.values():
            all_prs.extend(r_prs)
        for pr_dict in LIVE_PRS_CACHE.values():
            if pr_dict not in all_prs:
                all_prs.append(pr_dict)

    if not all_prs:
        all_prs = store.get_prs()

    # De-duplicate by pr_key
    seen_keys = set()
    deduped_prs = []
    for p in all_prs:
        pk = p.get("pr_key") or f"{p.get('repo')}#{p.get('pr_number')}"
        if pk not in seen_keys:
            seen_keys.add(pk)
            deduped_prs.append(p)
    all_prs = deduped_prs

    total_prs = len(all_prs)
    rubber_stamp_count = 0
    total_duration_secs = 0
    fatigued_reviewer_count = 0

    # 1. Reviewer analytics
    reviewers_map: dict[str, dict] = {}
    for pr in all_prs:
        rev = pr.get("reviewer") or "unassigned"
        if rev == "collaborator":
            rev = "unassigned"
        dur = int(pr.get("review_duration_seconds") or 3600)
        depth = float(pr.get("depth_score") if pr.get("depth_score") is not None else 0.45)
        att = float(pr.get("attention_state") if pr.get("attention_state") is not None else 0.65)
        residual = float(pr.get("residual_risk") if pr.get("residual_risk") is not None else 0.35)

        total_duration_secs += dur

        # Detect if rubber stamp
        is_rubber = False
        comments = pr.get("comments") or []
        for c in comments:
            if c.get("class_name") == "rubber_stamp" or (isinstance(c.get("body"), str) and c["body"].strip().lower() in ("lgtm", "approved", "👍")):
                is_rubber = True
                break
        if not is_rubber and (depth < 0.20 or dur < 120):
            is_rubber = True

        if is_rubber:
            rubber_stamp_count += 1

        if rev not in reviewers_map:
            reviewers_map[rev] = {
                "login": rev,
                "prs_reviewed": 0,
                "rubber_stamps": 0,
                "durations": [],
                "depths": [],
                "attentions": [],
                "residuals": [],
            }
        r_entry = reviewers_map[rev]
        r_entry["prs_reviewed"] += 1
        if is_rubber:
            r_entry["rubber_stamps"] += 1
        r_entry["durations"].append(dur)
        r_entry["depths"].append(depth)
        r_entry["attentions"].append(att)
        r_entry["residuals"].append(residual)

    reviewer_rows = []
    for rev, data in reviewers_map.items():
        if rev == "unassigned":
            continue
        count = data["prs_reviewed"]
        avg_dur_min = round(sum(data["durations"]) / max(count, 1) / 60.0, 1)
        avg_depth = round(sum(data["depths"]) / max(count, 1), 2)
        avg_att = round(sum(data["attentions"]) / max(count, 1), 2)
        rubber_pct = int((data["rubber_stamps"] / max(count, 1)) * 100)

        # Fatigue status classification
        if avg_att < 0.40 or count >= 8 or rubber_pct >= 50:
            status = "High Fatigue"
            status_class = "high"
            fatigued_reviewer_count += 1
        elif avg_att < 0.60 or count >= 4:
            status = "Moderate Load"
            status_class = "medium"
            fatigued_reviewer_count += 1
        else:
            status = "Healthy"
            status_class = "low"

        reviewer_rows.append({
            "login": rev,
            "avatar_url": f"https://github.com/{rev}.png",
            "prs_reviewed": count,
            "avg_duration_min": avg_dur_min,
            "avg_depth": avg_depth,
            "avg_attention": avg_att,
            "rubber_stamps": data["rubber_stamps"],
            "rubber_stamp_pct": rubber_pct,
            "status": status,
            "status_class": status_class,
        })

    reviewer_rows.sort(key=lambda x: x["prs_reviewed"], reverse=True)

    # 2. Module & Path Safety Heatmap
    MODULE_PATTERNS = {
        "Authentication & Identity": re.compile(r"(auth|oauth|jwt|token|session|login|sso)", re.I),
        "Billing & Payments": re.compile(r"(payment|billing|stripe|invoice|charge|wallet)", re.I),
        "Database & Schema Migrations": re.compile(r"(migration|migrate|schema|alembic|flyway)", re.I),
        "Cryptography & Security": re.compile(r"(crypto|encrypt|decrypt|cipher|vault|kms|secret)", re.I),
        "Cloud Infrastructure & K8s": re.compile(r"(terraform|k8s|kubernetes|helm|docker|infra)", re.I),
        "Core Application Logic": None,
    }

    module_stats = {name: {"name": name, "count": 0, "change_risks": [], "depths": [], "residuals": []} for name in MODULE_PATTERNS}

    for pr in all_prs:
        files = pr.get("sensitive_files") or []
        title = pr.get("title", "")
        body = pr.get("body", "")
        combined_text = " ".join([str(f) for f in files] + [title, body])

        matched_module = "Core Application Logic"
        for mod_name, pattern in MODULE_PATTERNS.items():
            if pattern and pattern.search(combined_text):
                matched_module = mod_name
                break

        ms = module_stats[matched_module]
        ms["count"] += 1
        cr_val = pr.get("change_risk")
        dp_val = pr.get("depth_score")
        rr_val = pr.get("residual_risk")
        ms["change_risks"].append(float(cr_val) if cr_val is not None else 0.40)
        ms["depths"].append(float(dp_val) if dp_val is not None else 0.50)
        ms["residuals"].append(float(rr_val) if rr_val is not None else 0.35)

    module_rows = []
    for name, data in module_stats.items():
        if data["count"] == 0:
            continue
        c = data["count"]
        avg_cr = round(sum(data["change_risks"]) / c, 2)
        avg_dp = round(sum(data["depths"]) / c, 2)
        avg_rr = round(sum(data["residuals"]) / c, 2)

        if avg_rr >= 0.65:
            status = "Under-Reviewed"
            status_class = "high"
        elif avg_rr >= 0.35:
            status = "Needs Attention"
            status_class = "medium"
        else:
            status = "Protected"
            status_class = "low"

        module_rows.append({
            "name": name,
            "count": c,
            "avg_change_risk": avg_cr,
            "avg_depth": avg_dp,
            "avg_residual_risk": avg_rr,
            "status": status,
            "status_class": status_class,
        })

    module_rows.sort(key=lambda x: x["avg_residual_risk"], reverse=True)

    # 3. Discrepancy Aggregations
    discrepancy_counts = {
        "rapid_merge": 0,
        "self_review": 0,
        "no_reviewer": 0,
        "wip_merged": 0,
        "rubber_stamps": 0,
        "high_residual_requeued": 0,
    }
    for pr in all_prs:
        dur = int(pr.get("review_duration_seconds") or 3600)
        is_pr_merged = bool(pr.get("merged_at")) or bool(pr.get("is_merged"))
        if dur < 600 and is_pr_merged:
            discrepancy_counts["rapid_merge"] += 1
        if pr.get("reviewer") and pr.get("author") and pr["reviewer"].lower() == pr["author"].lower() and pr["reviewer"] != "collaborator":
            discrepancy_counts["self_review"] += 1
        if pr.get("reviewer") in ("", "collaborator", "unassigned", None):
            discrepancy_counts["no_reviewer"] += 1
        if any(w in pr.get("title", "").lower() for w in ("wip", "do not merge", "draft", "dnm")) and is_pr_merged:
            discrepancy_counts["wip_merged"] += 1
        if pr.get("re_queued"):
            discrepancy_counts["high_residual_requeued"] += 1

    discrepancy_counts["rubber_stamps"] = rubber_stamp_count

    # 4. Review Pacing Distribution
    total_non_zero = max(total_prs, 1)
    pacing_dist = {
        "rushed": 0, "brief": 0, "standard": 0, "thorough": 0,
        "rushed_pct": 0, "brief_pct": 0, "standard_pct": 0, "thorough_pct": 0,
    }
    for pr in all_prs:
        mins = (pr.get("review_duration_seconds") or 3600) / 60.0
        if mins < 5:
            pacing_dist["rushed"] += 1
        elif mins < 20:
            pacing_dist["brief"] += 1
        elif mins < 60:
            pacing_dist["standard"] += 1
        else:
            pacing_dist["thorough"] += 1

    pacing_dist["rushed_pct"] = int((pacing_dist["rushed"] / total_non_zero) * 100)
    pacing_dist["brief_pct"] = int((pacing_dist["brief"] / total_non_zero) * 100)
    pacing_dist["standard_pct"] = int((pacing_dist["standard"] / total_non_zero) * 100)
    pacing_dist["thorough_pct"] = int((pacing_dist["thorough"] / total_non_zero) * 100)

    # 5. Global KPIs
    rubber_stamp_rate = round((rubber_stamp_count / max(total_prs, 1)) * 100, 1)
    fatigue_index = round((fatigued_reviewer_count / max(len(reviewer_rows), 1)) * 100, 1) if reviewer_rows else 0.0
    avg_review_mins = round((total_duration_secs / max(total_prs, 1)) / 60.0, 1)
    re_queued_count = sum(1 for pr in all_prs if pr.get("re_queued"))

    return {
        "total_prs": total_prs,
        "rubber_stamp_rate": rubber_stamp_rate,
        "fatigue_index": fatigue_index,
        "avg_review_mins": avg_review_mins,
        "re_queued_count": re_queued_count,
        "reviewers": reviewer_rows,
        "modules": module_rows,
        "discrepancies": discrepancy_counts,
        "pacing": pacing_dist,
        "repo_filter": repo_filter or "all",
        "repos": list(FETCHED_REPOS.values()),
    }


@app.route("/team-health")
@app.route("/validation")
def team_health():
    try:
        data = _build_org_health_data()
    except Exception as e:
        logger.error("team_health route error: %s", e)
        data = _empty_org_health()
    return render_template("team_health.html", **data)


# ── Feature 8: _build_org_health_data ───────────────────────────────────────

def _empty_org_health() -> dict:
    return {
        "reviewer_cards": [],
        "module_safety": [],
        "org_kpis": {
            "total_prs_scored": 0, "open_prs": 0, "high_risk_open": 0,
            "requeued_total": 0, "org_rubber_stamp_rate": 0.0,
            "avg_residual_risk": 0.0, "total_repos": 0, "total_reviewers": 0,
            "high_fatigue_count": 0, "healthy_count": 0,
        },
        "repos": [],
        "pacing": {"rushed": 0, "brief": 0, "standard": 0, "thorough": 0,
                   "rushed_pct": 0, "brief_pct": 0, "standard_pct": 0, "thorough_pct": 0},
        "discrepancies": {"rapid_merge": 0, "self_review": 0, "no_reviewer": 0,
                          "wip_merged": 0, "rubber_stamps": 0, "high_residual_requeued": 0},
    }


def _build_org_health_data() -> dict:
    """Org-wide reviewer health data for Feature 8 (/team-health redesign)."""
    global _health_cache
    cached = _health_cache.get("org_health")
    if cached and (time.time() - cached["ts"]) < HEALTH_CACHE_TTL:
        return cached["data"]

    try:
        all_prs = store.get_prs()
        repos = store.get_repos()
        now = time.time()

        # Org-level KPIs
        total_prs_scored = len(all_prs)
        open_prs = [p for p in all_prs if p.get("state") == "open"]
        high_risk_open = [p for p in open_prs if p.get("change_risk", 0) >= 0.65]
        requeued_prs = [p for p in all_prs if p.get("re_queued")]
        all_depths = [p.get("review_depth", 0) for p in all_prs if p.get("review_depth") is not None]
        org_rubber_stamp_rate = sum(1 for d in all_depths if d < 0.15) / len(all_depths) \
                                if all_depths else 0.0
        avg_residual_risk = sum(p.get("residual_risk", 0) for p in all_prs) / total_prs_scored \
                            if total_prs_scored else 0.0

        # Per-reviewer health cards
        reviewer_map: dict = {}
        for pr in all_prs:
            for reviewer in pr.get("reviewers", []):
                if not reviewer or not isinstance(reviewer, str):
                    continue
                if reviewer not in reviewer_map:
                    reviewer_map[reviewer] = {
                        "login": reviewer,
                        "reviews_total": 0, "reviews_today": 0, "reviews_week": 0,
                        "depth_scores": [], "residual_risks": [], "requeued_count": 0,
                        "high_risk_count": 0, "rubber_stamps": 0, "path_flags_all": [],
                        "scored_ats": [], "off_hours_count": 0, "weekend_count": 0,
                    }
                rm = reviewer_map[reviewer]
                rm["reviews_total"] += 1
                scored_at = pr.get("scored_at", 0)
                rm["scored_ats"].append(scored_at)
                if scored_at >= now - 86400:  rm["reviews_today"] += 1
                if scored_at >= now - 604800: rm["reviews_week"] += 1
                depth = pr.get("review_depth")
                if depth is not None:
                    rm["depth_scores"].append(depth)
                    if depth < 0.15: rm["rubber_stamps"] += 1
                rr = pr.get("residual_risk")
                if rr is not None: rm["residual_risks"].append(rr)
                if pr.get("re_queued"): rm["requeued_count"] += 1
                if pr.get("change_risk", 0) >= 0.65: rm["high_risk_count"] += 1
                rm["path_flags_all"].extend(pr.get("path_flags", []))
                if scored_at:
                    dt = datetime.utcfromtimestamp(scored_at)
                    if dt.hour >= 23 or dt.hour < 6: rm["off_hours_count"] += 1
                    if dt.weekday() >= 5: rm["weekend_count"] += 1

        reviewer_cards = []
        from collections import Counter
        for login, rm in reviewer_map.items():
            depths = rm["depth_scores"]
            avg_depth = sum(depths) / len(depths) if depths else 0.0
            rrs = rm["residual_risks"]
            avg_rr = sum(rrs) / len(rrs) if rrs else 0.0
            rs_rate = rm["rubber_stamps"] / rm["reviews_total"] if rm["reviews_total"] else 0.0
            off_pct = rm["off_hours_count"] / rm["reviews_total"] * 100 if rm["reviews_total"] else 0
            wknd_pct = rm["weekend_count"] / rm["reviews_total"] * 100 if rm["reviews_total"] else 0

            # Depth trend: compare last 5 vs prior 5
            sorted_depths = [d for _, d in sorted(zip(rm["scored_ats"], depths), key=lambda x: x[0])]
            if len(sorted_depths) >= 6:
                prior_avg = sum(sorted_depths[:-5]) / (len(sorted_depths) - 5)
                recent_avg = sum(sorted_depths[-5:]) / 5
                if recent_avg > prior_avg + 0.05:   trend = "improving"
                elif recent_avg < prior_avg - 0.05: trend = "degrading"
                else:                               trend = "stable"
            else:
                trend = "stable"

            if rm["reviews_today"] >= 8 or off_pct > 35:   fatigue = "high"
            elif rm["reviews_today"] >= 4 or off_pct > 15: fatigue = "moderate"
            else:                                           fatigue = "healthy"

            health_score = int(
                avg_depth * 35 +
                (1 - rs_rate) * 25 +
                (1 - avg_rr) * 25 +
                (1 - min(1.0, off_pct / 50)) * 15
            )
            top_domains = [d for d, _ in Counter(rm["path_flags_all"]).most_common(2)]
            sparkline = sorted_depths[-10:]

            reviewer_cards.append({
                "login": login,
                "reviews_total": rm["reviews_total"],
                "reviews_today": rm["reviews_today"],
                "reviews_week": rm["reviews_week"],
                "avg_depth_score": round(avg_depth, 3),
                "avg_residual_risk": round(avg_rr, 3),
                "rubber_stamp_rate": round(rs_rate, 3),
                "requeued_count": rm["requeued_count"],
                "high_risk_count": rm["high_risk_count"],
                "off_hours_pct": round(off_pct, 1),
                "weekend_pct": round(wknd_pct, 1),
                "trend": trend,
                "fatigue_state": fatigue,
                "health_score": health_score,
                "top_domains": top_domains,
                "sparkline": sparkline,
            })

        reviewer_cards.sort(key=lambda r: r["health_score"])

        # Module safety matrix from path_flags
        domain_matrix: dict = {}
        for pr in all_prs:
            for flag in pr.get("path_flags", []):
                if flag not in domain_matrix:
                    domain_matrix[flag] = {"touches": 0, "change_risk": [], "depth": [], "residual": []}
                dm = domain_matrix[flag]
                dm["touches"] += 1
                dm["change_risk"].append(pr.get("change_risk", 0))
                dm["depth"].append(pr.get("review_depth", 0))
                dm["residual"].append(pr.get("residual_risk", 0))

        module_safety = []
        for domain, dm in domain_matrix.items():
            avg_cr  = sum(dm["change_risk"]) / len(dm["change_risk"])
            avg_dep = sum(dm["depth"]) / len(dm["depth"])
            avg_res = sum(dm["residual"]) / len(dm["residual"])
            module_safety.append({
                "domain": domain, "touches": dm["touches"],
                "avg_change_risk": round(avg_cr, 3),
                "avg_depth": round(avg_dep, 3),
                "avg_residual": round(avg_res, 3),
                "status": "critical" if avg_res >= 0.65 else "warning" if avg_res >= 0.35 else "safe",
            })
        module_safety.sort(key=lambda m: m["avg_residual"], reverse=True)

        # Review pacing distribution
        total_non_zero = max(total_prs_scored, 1)
        pacing = {"rushed": 0, "brief": 0, "standard": 0, "thorough": 0}
        for pr in all_prs:
            mins = (pr.get("review_duration_seconds") or 3600) / 60.0
            if mins < 5:       pacing["rushed"] += 1
            elif mins < 20:    pacing["brief"] += 1
            elif mins < 60:    pacing["standard"] += 1
            else:              pacing["thorough"] += 1
        pacing["rushed_pct"]   = int(pacing["rushed"]   / total_non_zero * 100)
        pacing["brief_pct"]    = int(pacing["brief"]    / total_non_zero * 100)
        pacing["standard_pct"] = int(pacing["standard"] / total_non_zero * 100)
        pacing["thorough_pct"] = int(pacing["thorough"] / total_non_zero * 100)

        # Discrepancy counts
        rubber_stamp_count = sum(
            1 for pr in all_prs
            if (pr.get("review_depth") or 0.5) < 0.15 or (pr.get("review_duration_seconds") or 3600) < 120
        )
        discrepancies = {
            "rapid_merge": sum(1 for pr in all_prs if (pr.get("review_duration_seconds") or 3600) < 600 and pr.get("state") == "merged"),
            "self_review": sum(1 for pr in all_prs if pr.get("author") and any(pr.get("author") == r for r in pr.get("reviewers", []))),
            "no_reviewer": sum(1 for pr in all_prs if not pr.get("reviewers")),
            "wip_merged": sum(1 for pr in all_prs if any(w in pr.get("title", "").lower() for w in ("wip", "draft", "dnm", "do not merge")) and pr.get("state") == "merged"),
            "rubber_stamps": rubber_stamp_count,
            "high_residual_requeued": len(requeued_prs),
        }

        result = {
            "reviewer_cards": reviewer_cards,
            "module_safety": module_safety,
            "org_kpis": {
                "total_prs_scored": total_prs_scored,
                "open_prs": len(open_prs),
                "high_risk_open": len(high_risk_open),
                "requeued_total": len(requeued_prs),
                "org_rubber_stamp_rate": round(org_rubber_stamp_rate * 100, 1),
                "avg_residual_risk": round(avg_residual_risk, 3),
                "total_repos": len(repos),
                "total_reviewers": len(reviewer_cards),
                "high_fatigue_count": sum(1 for r in reviewer_cards if r["fatigue_state"] == "high"),
                "healthy_count": sum(1 for r in reviewer_cards if r["fatigue_state"] == "healthy"),
            },
            "repos": list(repos.values()),
            "pacing": pacing,
            "discrepancies": discrepancies,
        }
        _health_cache["org_health"] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        logger.error("_build_org_health_data error: %s", e)
        return _empty_org_health()


@app.route("/api/team-health/refresh")
def team_health_cache_refresh():
    """Bust the org health cache and return fresh data."""
    global _health_cache
    _health_cache.clear()
    return jsonify({"status": "ok", "message": "Cache cleared"})


@app.route("/api/team-health/export")
def export_team_health_csv():
    """Export reviewer health data as CSV."""
    import csv, io
    from flask import Response
    try:
        data = _build_org_health_data()
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=[
            "login", "reviews_total", "reviews_today", "avg_depth_score",
            "rubber_stamp_rate", "avg_residual_risk", "fatigue_state",
            "health_score", "trend", "off_hours_pct", "top_domains"
        ])
        writer.writeheader()
        for r in data["reviewer_cards"]:
            row = dict(r)
            row["top_domains"] = "|".join(r.get("top_domains", []))
            writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=vouch-team-health.csv"}
        )
    except Exception as e:
        logger.error("export_team_health_csv error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Feature 1: Load Balancing ────────────────────────────────────────────────

def _build_load_balancing_data() -> dict:
    """Build reviewer workload and assignment suggestion data for /load-balancing."""
    global _lb_cache
    cached = _lb_cache.get("load_balancing")
    if cached and (time.time() - cached["ts"]) < HEALTH_CACHE_TTL:
        return cached["data"]

    try:
        all_prs = store.get_prs()
        now = time.time()

        reviewer_map: dict = {}
        for pr in all_prs:
            for reviewer in pr.get("reviewers", []):
                if not reviewer or not isinstance(reviewer, str):
                    continue
                if reviewer not in reviewer_map:
                    reviewer_map[reviewer] = {
                        "login": reviewer, "reviews_total": 0, "reviews_today": 0,
                        "reviews_this_week": 0, "open_assigned": 0,
                        "avg_depth_score": [], "high_risk_reviews": 0,
                        "rubber_stamps": 0, "last_review_at": None,
                        "fatigue_state": "healthy", "current_load_score": 0.0,
                    }
                rm = reviewer_map[reviewer]
                rm["reviews_total"] += 1
                scored_at = pr.get("scored_at", 0)
                if scored_at >= now - 86400:   rm["reviews_today"] += 1
                if scored_at >= now - 604800:  rm["reviews_this_week"] += 1
                if pr.get("state") == "open":  rm["open_assigned"] += 1
                if pr.get("change_risk", 0) >= 0.65: rm["high_risk_reviews"] += 1
                if pr.get("review_depth", 1.0) < 0.15: rm["rubber_stamps"] += 1
                depth = pr.get("review_depth")
                if depth is not None: rm["avg_depth_score"].append(depth)
                if rm["last_review_at"] is None or scored_at > rm["last_review_at"]:
                    rm["last_review_at"] = scored_at

        for login, rm in reviewer_map.items():
            depths = rm["avg_depth_score"]
            rm["avg_depth_score"] = round(sum(depths) / len(depths), 3) if depths else 0.0
            if rm["reviews_today"] >= 8:   rm["fatigue_state"] = "high"
            elif rm["reviews_today"] >= 4: rm["fatigue_state"] = "moderate"
            else:                          rm["fatigue_state"] = "healthy"
            load = (
                min(1.0, rm["reviews_today"] / 10) * 0.40 +
                min(1.0, rm["open_assigned"] / 8)  * 0.35 +
                min(1.0, rm["high_risk_reviews"] / 5) * 0.25
            )
            rm["current_load_score"] = round(load, 3)

        reviewers_sorted = sorted(reviewer_map.values(), key=lambda r: r["current_load_score"], reverse=True)
        for r in reviewers_sorted:
            s = r["current_load_score"]
            if s >= 0.70:   r["capacity_tier"] = "overloaded"
            elif s >= 0.40: r["capacity_tier"] = "busy"
            else:           r["capacity_tier"] = "available"

        unassigned_high_risk = [
            p for p in store.get_open_unreviewed_prs()
            if p.get("change_risk", 0) >= 0.35
        ]

        def domain_match_score(reviewer_login: str, path_flags: list) -> float:
            reviewer_prs = [p for p in all_prs if reviewer_login in p.get("reviewers", [])]
            if not reviewer_prs or not path_flags: return 0.0
            matches = sum(1 for p in reviewer_prs if any(f in p.get("path_flags", []) for f in path_flags))
            return round(matches / len(reviewer_prs), 3)

        available_reviewers = [r for r in reviewers_sorted if r["capacity_tier"] == "available"]
        suggestions = []
        for pr in unassigned_high_risk[:10]:
            scored = []
            for r in available_reviewers[:20]:
                dm = domain_match_score(r["login"], pr.get("path_flags", []))
                combined = dm * 0.60 + (1 - r["current_load_score"]) * 0.40
                scored.append((r["login"], combined, dm, r["current_load_score"]))
            scored.sort(key=lambda x: x[1], reverse=True)
            best = scored[0] if scored else None
            suggestions.append({
                "pr_key": pr["pr_key"], "repo": pr["repo"], "number": pr["number"],
                "title": pr["title"][:60], "change_risk": pr.get("change_risk", 0),
                "path_flags": pr.get("path_flags", []),
                "wait_hours": round((now - pr.get("scored_at", now)) / 3600, 1),
                "suggested_reviewer": best[0] if best else None,
                "suggestion_confidence": round(best[1], 3) if best else 0.0,
                "domain_match": round(best[2], 3) if best else 0.0,
                "html_url": pr.get("html_url", ""),
            })

        result = {
            "reviewers": reviewers_sorted,
            "overloaded_count": sum(1 for r in reviewers_sorted if r["capacity_tier"] == "overloaded"),
            "available_count": sum(1 for r in reviewers_sorted if r["capacity_tier"] == "available"),
            "unassigned_high_risk": unassigned_high_risk[:10],
            "suggestions": suggestions,
            "total_open_prs": sum(1 for p in all_prs if p.get("state") == "open"),
        }
        _lb_cache["load_balancing"] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        logger.error("_build_load_balancing_data error: %s", e)
        return {"reviewers": [], "overloaded_count": 0, "available_count": 0,
                "unassigned_high_risk": [], "suggestions": [], "total_open_prs": 0}


@app.route("/load-balancing")
def load_balancing():
    try:
        data = _build_load_balancing_data()
    except Exception as e:
        logger.error("load_balancing route error: %s", e)
        data = {"reviewers": [], "overloaded_count": 0, "available_count": 0,
                "unassigned_high_risk": [], "suggestions": [], "total_open_prs": 0}
    return render_template("load_balancing.html", **data)


@app.route("/api/load-balancing")
def api_load_balancing():
    return jsonify(_build_load_balancing_data())


# ── Feature 5: Pair Intelligence ─────────────────────────────────────────────

def _build_pair_intelligence_data() -> dict:
    """Build author-reviewer pair quality data for /pair-intelligence."""
    global _pair_cache
    cached = _pair_cache.get("pair_intel")
    if cached and (time.time() - cached["ts"]) < HEALTH_CACHE_TTL:
        return cached["data"]

    try:
        from collections import defaultdict, Counter
        all_prs = store.get_prs()

        pair_map: dict = defaultdict(lambda: {
            "reviews": 0, "requeued": 0, "depth_scores": [],
            "residual_risks": [], "path_flags_all": [], "high_risk_prs": 0,
        })

        for pr in all_prs:
            author = pr.get("author")
            if not author: continue
            for reviewer in pr.get("reviewers", []):
                if not reviewer or reviewer == author: continue
                key = (author, reviewer)
                pm = pair_map[key]
                pm["reviews"] += 1
                if pr.get("re_queued"): pm["requeued"] += 1
                depth = pr.get("review_depth")
                if depth is not None: pm["depth_scores"].append(depth)
                rr = pr.get("residual_risk")
                if rr is not None: pm["residual_risks"].append(rr)
                pm["path_flags_all"].extend(pr.get("path_flags", []))
                if pr.get("change_risk", 0) >= 0.65: pm["high_risk_prs"] += 1

        pairs = []
        for (author, reviewer), pm in pair_map.items():
            if pm["reviews"] < 2: continue
            avg_depth = sum(pm["depth_scores"]) / len(pm["depth_scores"]) if pm["depth_scores"] else 0.0
            avg_residual = sum(pm["residual_risks"]) / len(pm["residual_risks"]) if pm["residual_risks"] else 0.0
            requeue_rate = pm["requeued"] / pm["reviews"]
            quality_score = (
                (1 - avg_residual) * 0.40 +
                avg_depth          * 0.40 +
                (1 - requeue_rate) * 0.20
            )
            domain_counter = Counter(pm["path_flags_all"])
            top_domains = [d for d, _ in domain_counter.most_common(3)]
            if quality_score >= 0.70 and pm["reviews"] >= 3: pair_class = "golden"
            elif requeue_rate >= 0.40 or avg_depth < 0.20:  pair_class = "risky"
            elif avg_residual >= 0.55:                       pair_class = "watchlist"
            else:                                            pair_class = "neutral"
            pairs.append({
                "author": author, "reviewer": reviewer,
                "reviews": pm["reviews"], "requeued": pm["requeued"],
                "requeue_rate": round(requeue_rate, 3),
                "avg_depth_score": round(avg_depth, 3),
                "avg_residual_risk": round(avg_residual, 3),
                "high_risk_prs": pm["high_risk_prs"],
                "quality_score": round(quality_score, 3),
                "top_domains": top_domains, "pair_class": pair_class,
            })

        pairs.sort(key=lambda p: p["reviews"], reverse=True)
        golden    = [p for p in pairs if p["pair_class"] == "golden"]
        risky     = [p for p in pairs if p["pair_class"] == "risky"]
        watchlist = [p for p in pairs if p["pair_class"] == "watchlist"]
        neutral   = [p for p in pairs if p["pair_class"] == "neutral"]

        result = {
            "pairs": pairs, "golden": golden[:10], "risky": risky[:10],
            "watchlist": watchlist[:10], "neutral": neutral[:20],
            "total_pairs": len(pairs), "golden_count": len(golden),
            "risky_count": len(risky), "watchlist_count": len(watchlist),
        }
        _pair_cache["pair_intel"] = {"data": result, "ts": time.time()}
        return result
    except Exception as e:
        logger.error("_build_pair_intelligence_data error: %s", e)
        return {"pairs": [], "golden": [], "risky": [], "watchlist": [], "neutral": [],
                "total_pairs": 0, "golden_count": 0, "risky_count": 0, "watchlist_count": 0}


@app.route("/pair-intelligence")
def pair_intelligence():
    try:
        data = _build_pair_intelligence_data()
    except Exception as e:
        logger.error("pair_intelligence route error: %s", e)
        data = {"pairs": [], "golden": [], "risky": [], "watchlist": [], "neutral": [],
                "total_pairs": 0, "golden_count": 0, "risky_count": 0, "watchlist_count": 0}
    return render_template("pair_intelligence.html", **data)


@app.route("/api/pair-intelligence")
def api_pair_intelligence():
    return jsonify(_build_pair_intelligence_data())


# ── Feature 6: SLA Predictions ────────────────────────────────────────────────

DEFAULT_SLA_HOURS = 48
PREDICTION_HORIZON_HOURS = 8


def _build_sla_predictions() -> dict:
    """Build SLA breach prediction data for /sla-predictions."""
    try:
        all_prs = store.get_prs()
        open_prs = [p for p in all_prs if p.get("state") == "open"]
        now = time.time()
        predictions = []

        for pr in open_prs:
            created_ts = None
            created_at = pr.get("created_at")
            if created_at:
                try:
                    created_ts = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    pass
            if not created_ts:
                continue

            age_hours = (now - created_ts) / 3600
            sla_remaining_hours = DEFAULT_SLA_HOURS - age_hours
            already_breached = sla_remaining_hours < 0

            reviewers = pr.get("reviewers", [])
            if reviewers:
                reviewer_pr_counts: dict = {}
                for p2 in all_prs:
                    for r in p2.get("reviewers", []):
                        if p2.get("scored_at", 0) >= now - 86400:
                            reviewer_pr_counts[r] = reviewer_pr_counts.get(r, 0) + 1
                avg_load = sum(
                    min(1.0, reviewer_pr_counts.get(r, 0) / 10) for r in reviewers
                ) / len(reviewers)
            else:
                avg_load = 1.0

            age_fraction = min(1.0, age_hours / DEFAULT_SLA_HOURS)
            breach_probability = min(0.99, age_fraction * 0.60 + avg_load * 0.40)
            expected_review_delay = avg_load * 12
            predicted_breach_in = max(0.0, sla_remaining_hours - expected_review_delay)

            at_risk = (
                already_breached or
                predicted_breach_in <= PREDICTION_HORIZON_HOURS or
                breach_probability >= 0.60
            )
            if not at_risk:
                continue

            change_risk = pr.get("change_risk", 0.0)
            urgency = min(1.0, breach_probability * 0.60 + change_risk * 0.40)

            if already_breached:           status = "breached"
            elif predicted_breach_in <= 2: status = "imminent"
            elif predicted_breach_in <= PREDICTION_HORIZON_HOURS: status = "at_risk"
            else:                          status = "warning"

            predictions.append({
                "pr_key": pr["pr_key"], "repo": pr["repo"], "number": pr["number"],
                "title": pr["title"][:60], "author": pr.get("author"),
                "reviewers": reviewers, "change_risk": round(change_risk, 3),
                "age_hours": round(age_hours, 1),
                "sla_remaining_hours": round(sla_remaining_hours, 1),
                "predicted_breach_in": round(predicted_breach_in, 1),
                "breach_probability": round(breach_probability, 3),
                "reviewer_avg_load": round(avg_load, 3),
                "urgency": round(urgency, 3), "status": status,
                "path_flags": pr.get("path_flags", []),
                "html_url": pr.get("html_url", ""),
            })

        predictions.sort(key=lambda p: p["urgency"], reverse=True)
        return {
            "predictions": predictions,
            "breached_count": sum(1 for p in predictions if p["status"] == "breached"),
            "imminent_count": sum(1 for p in predictions if p["status"] == "imminent"),
            "at_risk_count":  sum(1 for p in predictions if p["status"] == "at_risk"),
            "warning_count":  sum(1 for p in predictions if p["status"] == "warning"),
            "sla_hours": DEFAULT_SLA_HOURS,
            "horizon_hours": PREDICTION_HORIZON_HOURS,
        }
    except Exception as e:
        logger.error("_build_sla_predictions error: %s", e)
        return {"predictions": [], "breached_count": 0, "imminent_count": 0,
                "at_risk_count": 0, "warning_count": 0,
                "sla_hours": DEFAULT_SLA_HOURS, "horizon_hours": PREDICTION_HORIZON_HOURS}


@app.route("/sla-predictions")
def sla_predictions():
    try:
        data = _build_sla_predictions()
    except Exception as e:
        logger.error("sla_predictions route error: %s", e)
        data = {"predictions": [], "breached_count": 0, "imminent_count": 0,
                "at_risk_count": 0, "warning_count": 0,
                "sla_hours": DEFAULT_SLA_HOURS, "horizon_hours": PREDICTION_HORIZON_HOURS}
    return render_template("sla_predictions.html", **data)


@app.route("/api/sla-predictions")
def api_sla_predictions():
    return jsonify(_build_sla_predictions())




@app.route("/api/version")
def api_version():
    """Application version, git build metadata, and environment status."""
    return jsonify(get_version_info())


@app.route("/api/health")
def api_health():
    """Health check endpoint for ALB and monitoring systems."""
    v_info = get_version_info()
    return jsonify({
        "status": "healthy",
        "version": v_info["version"],
        "commit": v_info["commit_short"],
        "branch": v_info["branch"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/repos")
def api_repos():
    """List all fetched repositories in catalog."""
    return jsonify({
        "repos": list(FETCHED_REPOS.values()),
        "total": len(FETCHED_REPOS),
    })


@app.route("/api/repos/search")
def api_repos_search():
    """Smart repository search endpoint matching query against fetched and stored repositories."""
    q = request.args.get("q", "").strip().lower()
    all_repos = dict(FETCHED_REPOS)
    try:
        for name, meta in store.get_repos().items():
            if name not in all_repos:
                all_repos[name] = meta
    except Exception:
        pass

    results = []
    for full_name, r in all_repos.items():
        fname = (r.get("full_name") or full_name).lower()
        desc = (r.get("description") or "").lower()
        lang = (r.get("language") or "").lower()
        owner = r.get("owner", "")
        repo = r.get("repo", "")
        if not q or (q in fname or q in desc or q in lang or q in owner.lower() or q in repo.lower()):
            results.append({
                "full_name": r.get("full_name") or full_name,
                "owner": owner or (full_name.split("/")[0] if "/" in full_name else ""),
                "repo": repo or (full_name.split("/")[1] if "/" in full_name else full_name),
                "description": r.get("description", ""),
                "stars": r.get("stars", "—"),
                "language": r.get("language", "Code"),
                "language_color": r.get("language_color", "#586069"),
                "active_prs_count": r.get("active_prs_count", 0),
            })

    vouch_name = DEFAULT_INITIAL_REPO.lower()
    def _rank(item):
        fn = item["full_name"].lower()
        if fn == q:
            return 0
        if q and fn.startswith(q):
            return 1
        if q and q in fn.split("/")[-1]:
            return 2
        if fn == vouch_name:
            return 3
        return 4

    results.sort(key=lambda x: (_rank(x), x["full_name"].lower()))
    return jsonify({
        "query": q,
        "results": results[:15],
        "total": len(results),
    })


@app.route("/api/prs")
def api_prs():
    repo_filter = request.args.get("repo", "").strip()
    risk_filter = request.args.get("risk", "all")
    prs = store.get_prs(repo=repo_filter if repo_filter else None)
    if risk_filter != "all":
        prs = [p for p in prs if p.get("risk_tier") == risk_filter]
    prs = sorted(prs, key=lambda x: x.get("residual_risk") or 0.0, reverse=True)
    return jsonify({
        "prs": [
            {
                "pr_key": p.get("pr_key") or f"{p.get('repo', '')}#{p.get('pr_number', '')}",
                "pr_url": p.get("pr_url", ""),
                "title": p.get("title", ""),
                "risk_tier": p.get("risk_tier", "unscored"),
                "residual_risk": p.get("residual_risk"),
                "reviewer": p.get("reviewer", ""),
                "merged_at": p.get("merged_at"),
                "status": p.get("status", "pending_analysis"),
            }
            for p in prs
        ],
        "count": len(prs),
    })


@app.route("/api/discover-popular", methods=["POST"])
def api_discover_popular():
    """Discover and fetch popular repositories dynamically from GitHub."""
    data = request.get_json(force=True, silent=True) or {}
    limit = int(data.get("limit", 6))
    discovered = _discover_popular_repos(limit=limit)
    return jsonify({
        "success": True,
        "count": len(FETCHED_REPOS),
        "discovered": len(discovered),
        "repos": list(FETCHED_REPOS.values()),
    })


@app.route("/api/refetch-repo", methods=["POST"])
def api_refetch_repo():
    """
    Refetch pull requests for a repository from GitHub to discover newly launched PRs,
    score recent ones, and update the repository in the catalog.
    """
    data = request.get_json(force=True, silent=True) or {}
    raw_repo = (data.get("repo") or "").strip()
    owner = (data.get("owner") or "").strip()
    repo_name = (data.get("repo_name") or data.get("repo") or "").strip()

    if raw_repo and ("/" in raw_repo or "http" in raw_repo):
        parsed = _parse_repo_input(raw_repo)
        if parsed:
            owner, repo_name = parsed

    if not owner or not repo_name:
        return jsonify({"success": False, "error": "Missing repository owner and name."}), 400

    result = _fetch_and_score_repo(owner, repo_name, limit=50, force_refresh=True)
    return jsonify({
        "success": True,
        "repo": f"{owner}/{repo_name}",
        "stats": result.get("stats", {}),
        "repo_meta": result.get("repo_meta", {}),
        "prs_count": len(result.get("prs", [])),
    })


@app.route("/api/fetch-single-pr", methods=["POST"])
def api_fetch_single_pr():
    """
    Fetch a single PR by number directly from GitHub if not already present in the cached repo list,
    evaluate Model 1, 2, 3 and Residual Risk, and add it to the repo cache.
    """
    data = request.get_json(force=True, silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    pr_number_raw = data.get("pr_number")

    if not owner or not repo or pr_number_raw is None:
        return jsonify({"success": False, "error": "Owner, repo, and pr_number are required."}), 400

    try:
        pr_number = int(str(pr_number_raw).lstrip("#"))
    except ValueError:
        return jsonify({"success": False, "error": "Invalid PR number."}), 400

    full_repo = f"{owner}/{repo}"
    cached_list = REPO_PRS_CACHE.get(full_repo.lower(), [])

    # Check if already in cached list
    for p in cached_list:
        if p.get("pr_number") == pr_number:
            if not p.get("scored"):
                full_scored, _ = _resolve_pr_detail(owner, repo, pr_number)
                if full_scored:
                    p.update(full_scored)
                    p["scored"] = True
            return jsonify({"success": True, "pr": p, "already_cached": True})

    # Not in cache: fetch directly via _resolve_pr_detail
    pr_obj, code = _resolve_pr_detail(owner, repo, pr_number)
    if not pr_obj:
        return jsonify({"success": False, "error": f"Pull request #{pr_number} could not be resolved from {full_repo}."}), 404

    pr_obj["scored"] = True

    if full_repo.lower() not in REPO_PRS_CACHE:
        REPO_PRS_CACHE[full_repo.lower()] = []
    REPO_PRS_CACHE[full_repo.lower()].insert(0, pr_obj)

    if full_repo in FETCHED_REPOS:
        meta = FETCHED_REPOS[full_repo]
        if pr_obj.get("state") == "open":
            meta["active_prs_count"] = meta.get("active_prs_count", 0) + 1
        else:
            meta["closed_prs_count"] = meta.get("closed_prs_count", 0) + 1
        try:
            store.save_repo(meta)
        except Exception:
            pass

    try:
        store.save_pr(pr_obj)
    except Exception:
        pass

    return jsonify({"success": True, "pr": pr_obj, "newly_fetched": True})


@app.route("/api/score-pr", methods=["POST"])
def api_score_pr():
    """
    Score an older PR on demand that was initially left unscored.
    """
    data = request.get_json(force=True, silent=True) or {}
    owner = (data.get("owner") or "").strip()
    repo = (data.get("repo") or "").strip()
    pr_number_raw = data.get("pr_number")

    if not owner or not repo or pr_number_raw is None:
        return jsonify({"success": False, "error": "Owner, repo, and pr_number are required."}), 400

    try:
        pr_number = int(str(pr_number_raw).lstrip("#"))
    except ValueError:
        return jsonify({"success": False, "error": "Invalid PR number."}), 400

    full_repo = f"{owner}/{repo}"
    cached_list = REPO_PRS_CACHE.get(full_repo.lower(), [])
    target = None
    for p in cached_list:
        if p.get("pr_number") == pr_number:
            target = p
            break

    if target and target.get("scored") and target.get("residual_risk") is not None:
        return jsonify({"success": True, "pr": target})

    scored_item, code = _resolve_pr_detail(owner, repo, pr_number)
    if not scored_item:
        return jsonify({"success": False, "error": f"Failed to score PR #{pr_number}."}), 404

    scored_item["scored"] = True
    if target:
        target.update(scored_item)
    else:
        if full_repo.lower() not in REPO_PRS_CACHE:
            REPO_PRS_CACHE[full_repo.lower()] = []
        REPO_PRS_CACHE[full_repo.lower()].append(scored_item)

    if full_repo in FETCHED_REPOS:
        prs = REPO_PRS_CACHE[full_repo.lower()]
        scored_only = [p for p in prs if p.get("scored") and p.get("residual_risk") is not None]
        total_s = len(scored_only)
        avg_res = (sum(p["residual_risk"] for p in scored_only) / total_s) if total_s > 0 else 0.0
        FETCHED_REPOS[full_repo]["avg_residual_risk"] = round(avg_res, 2)

    return jsonify({"success": True, "pr": scored_item})


@app.route("/api/fetch-repo", methods=["POST"])
def api_fetch_repo():
    """
    Fetch and score live PRs from a GitHub repository.
    Body: {"repo": "owner/repo" | "https://github.com/owner/repo"}
    """
    data = request.get_json(force=True, silent=True) or {}
    raw = (data.get("repo") or "").strip()

    if not raw:
        return jsonify({"error": "No repository provided.", "prs": []}), 400

    parsed = _parse_repo_input(raw)
    if not parsed:
        return jsonify({"error": f"Could not parse '{raw}'. Use owner/repo or a GitHub URL.", "prs": []}), 400

    owner, repo_name = parsed
    full_name = f"{owner}/{repo_name}"
    session["active_repo"] = full_name
    s_repos = session.get("session_repos") or []
    if full_name not in s_repos:
        s_repos.append(full_name)
        session["session_repos"] = s_repos
    result = _fetch_and_score_repo(owner, repo_name, limit=50)
    result["redirect_url"] = f"/repo/{owner}/{repo_name}"
    return jsonify(result)


@app.route("/api/explain/groq", methods=["POST"])
def api_explain_groq():
    """
    Generate an on-demand LLM risk explanation using Groq API as an immediate
    fallback while Amazon Bedrock quota access is pending approval.
    """
    from explain.groq_client import generate_groq_explanation

    data = request.get_json(force=True, silent=True) or {}
    pr_key = (data.get("pr_key") or "").strip()
    user_api_key = (data.get("groq_api_key") or session.get("groq_api_key") or "").strip()
    model = (data.get("model") or "").strip() or None

    if not pr_key:
        return jsonify({"success": False, "error": "pr_key is required."}), 400

    # Store api key in session if provided
    if user_api_key:
        session["groq_api_key"] = user_api_key

    # Resolve PR object
    pr_obj = None
    if pr_key in LIVE_PRS_CACHE:
        pr_obj = LIVE_PRS_CACHE[pr_key]
    else:
        for k, v in LIVE_PRS_CACHE.items():
            if k.lower() == pr_key.lower():
                pr_obj = v
                break

    if not pr_obj and ("#" in pr_key or "/" in pr_key):
        parts = pr_key.replace("#", "/").split("/")
        if len(parts) >= 3:
            owner, repo, num_str = parts[0], parts[1], parts[2]
            try:
                num = int(num_str)
                pr_obj, _ = _resolve_pr_detail(owner, repo, num)
            except Exception:
                pass

    if not pr_obj:
        stored = store.get_prs()
        for p in stored:
            curr_key = p.get("pr_key") or f"{p.get('repo')}#{p.get('pr_number')}"
            if curr_key.lower() == pr_key.lower():
                pr_obj = p
                break

    if not pr_obj:
        return jsonify({"success": False, "error": f"Pull request '{pr_key}' not found."}), 404

    # Extract metrics for explanation prompt
    change_risk = float(pr_obj.get("change_risk") if pr_obj.get("change_risk") is not None else 0.5)
    review_confidence = float(pr_obj.get("review_confidence") if pr_obj.get("review_confidence") is not None else 0.5)
    residual_risk = float(pr_obj.get("residual_risk") if pr_obj.get("residual_risk") is not None else 0.25)
    top_features = pr_obj.get("top_features") or []
    depth_score = float(pr_obj.get("depth_score") if pr_obj.get("depth_score") is not None else 0.5)
    attention_state = float(pr_obj.get("attention_state") if pr_obj.get("attention_state") is not None else 1.0)
    review_duration_seconds = int(pr_obj.get("review_duration_seconds") or 3600)
    diff_lines = int(pr_obj.get("diff_lines") or (pr_obj.get("additions", 0) + pr_obj.get("deletions", 0)) or 100)
    reviewer = str(pr_obj.get("reviewer") or "None assigned")
    consecutive_reviews = int(pr_obj.get("consecutive_reviews") or 0)
    sensitive_files = pr_obj.get("sensitive_files") or []
    file_context = ", ".join(sensitive_files[:2]) if sensitive_files else ""

    result = generate_groq_explanation(
        pr_key=pr_key,
        change_risk=change_risk,
        review_confidence=review_confidence,
        residual_risk=residual_risk,
        top_risk_features=top_features,
        depth_score=depth_score,
        attention_state=attention_state,
        review_duration_seconds=review_duration_seconds,
        diff_lines=diff_lines,
        reviewer=reviewer,
        consecutive_reviews=consecutive_reviews,
        file_context=file_context,
        api_key=user_api_key,
        model=model,
    )

    if result.get("success"):
        explanation_text = result["explanation"]
        pr_obj["bedrock_status"] = "connected"
        pr_obj["bedrock_explanation"] = explanation_text
        pr_obj["groq_explanation"] = explanation_text
        pr_obj["llm_provider"] = result.get("provider", "Groq Llama 3.3")
        _cache_scored_pr(pr_obj)
        try:
            store.save_pr(pr_obj)
        except Exception:
            pass

        return jsonify({
            "success": True,
            "explanation": explanation_text,
            "provider": result.get("provider", "Groq"),
            "model": result.get("model", "qwen/qwen3.8-27b"),
        })
    else:
        err_str = result.get("error", "Failed to generate explanation from Groq.")
        requires_key = bool(
            result.get("requires_key")
            or "not configured" in err_str.lower()
            or "invalid" in err_str.lower()
            or "enter a" in err_str.lower()
            or "failed" in err_str.lower()
        )
        return jsonify({
            "success": False,
            "error": err_str,
            "requires_key": requires_key,
        }), 400


# ─────────────────────────────────────────────────────────────────────────────
# Template filters
# ─────────────────────────────────────────────────────────────────────────────

@app.template_filter("pct")
def pct_filter(value):
    return f"{float(value)*100:.0f}%"


@app.template_filter("fmt_date")
def fmt_date_filter(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %H:%M UTC")
    except Exception:
        return value


@app.template_filter("fmt_markdown")
def fmt_markdown_filter(text):
    if not text:
        return Markup('<span style="color:var(--color-fg-muted); font-style:italic;">No description provided.</span>')

    escaped = html.escape(str(text))
    code_blocks = []

    def _save_code_block(match):
        code = match.group(1)
        idx = len(code_blocks)
        code_blocks.append(f'<pre class="gh-code-block"><code>{code}</code></pre>')
        return f'___CODE_BLOCK_{idx}___'

    escaped = re.sub(r'```(?:\w*)\r?\n([\s\S]*?)```', _save_code_block, escaped)
    escaped = re.sub(r'`([^`]+)`', r'<code class="gh-inline-code">\1</code>', escaped)
    escaped = re.sub(r'(?m)^####\s+(.+)$', r'<h4 class="gh-md-h4">\1</h4>', escaped)
    escaped = re.sub(r'(?m)^###\s+(.+)$', r'<h3 class="gh-md-h3">\1</h3>', escaped)
    escaped = re.sub(r'(?m)^##\s+(.+)$', r'<h2 class="gh-md-h2">\1</h2>', escaped)
    escaped = re.sub(r'(?m)^#\s+(.+)$', r'<h1 class="gh-md-h1">\1</h1>', escaped)
    escaped = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', escaped)
    escaped = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', escaped)
    escaped = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2" target="_blank" rel="noopener noreferrer" class="gh-md-link">\1</a>', escaped)
    escaped = re.sub(r'(?<!href=")(?<!">)(https?://[^\s<]+)', r'<a href="\1" target="_blank" rel="noopener noreferrer" class="gh-md-link">\1</a>', escaped)
    escaped = re.sub(r'(?m)^&gt;\s+(.+)$', r'<blockquote class="gh-md-quote">\1</blockquote>', escaped)
    escaped = re.sub(r'(?m)^[-*]\s+\[ \]\s+(.+)$', r'<li class="gh-task-item"><input type="checkbox" disabled /> \1</li>', escaped)
    escaped = re.sub(r'(?m)^[-*]\s+\[x\]\s+(.+)$', r'<li class="gh-task-item"><input type="checkbox" checked disabled /> \1</li>', escaped)
    escaped = re.sub(r'(?m)^[-*]\s+(.+)$', r'<li class="gh-md-li">\1</li>', escaped)
    escaped = escaped.replace("\r\n", "\n")
    escaped = re.sub(r'\n{2,}', '</p><p>', escaped)
    escaped = escaped.replace("\n", "<br>")

    for idx, block in enumerate(code_blocks):
        escaped = escaped.replace(f'___CODE_BLOCK_{idx}___', block)

    return Markup(f'<div class="gh-md-content"><p>{escaped}</p></div>')


# ─────────────────────────────────────────────────────────────────────────────
# Reviewer Profile & Fatigue Governance
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_gh_user_profile(username: str, gh_token: str | None = None) -> tuple[dict, bool, bool]:
    """Fetch GitHub user profile. Returns (profile_dict, not_found, rate_limited)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (gh_token or "").strip() or _resolve_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/users/{username}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json(), False, False
        elif resp.status_code == 404:
            return {}, True, False
        elif resp.status_code in (403, 429):
            return {}, False, True
        return {}, False, False
    except Exception:
        return {}, False, False


def _fetch_gh_user_prs(username: str, gh_token: str | None = None) -> tuple[list, bool]:
    """Fetch GitHub PRs reviewed by user. Returns (prs_list, rate_limited)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (gh_token or "").strip() or _resolve_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/search/issues?q=reviewed-by:{username}+type:pr&per_page=100"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            return items, False
        elif resp.status_code in (403, 429):
            return [], True
        return [], False
    except Exception:
        return [], False


def _fetch_gh_user_events(username: str, gh_token: str | None = None) -> tuple[list, bool]:
    """Fetch GitHub user public events. Returns (events_list, rate_limited)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (gh_token or "").strip() or _resolve_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/users/{username}/events?per_page=100"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return (data if isinstance(data, list) else []), False
        elif resp.status_code in (403, 429):
            return [], True
        return [], False
    except Exception:
        return [], False


def _build_reviewer_profile(username: str, gh_token: str | None = None) -> dict:
    """
    Build aggregated reviewer profile combining live GitHub identity and Vouch triage data.
    Runs concurrently across GitHub endpoints with graceful rate-limit handling.
    """
    clean_username = (username or "").strip()
    if not clean_username:
        raise ValueError("GitHub username must not be empty.")

    # 1. Parallel GitHub API calls
    with ThreadPoolExecutor(max_workers=3) as executor:
        f_profile = executor.submit(_fetch_gh_user_profile, clean_username, gh_token)
        f_prs = executor.submit(_fetch_gh_user_prs, clean_username, gh_token)
        f_events = executor.submit(_fetch_gh_user_events, clean_username, gh_token)

        gh_profile, not_found, rl_profile = f_profile.result()
        gh_prs, rl_prs = f_prs.result()
        gh_events, rl_events = f_events.result()

    if not_found:
        raise ValueError(f"GitHub user '{clean_username}' not found")

    github_rate_limited = rl_profile or rl_prs or rl_events
    if not gh_profile and not github_rate_limited:
        # Fallback profile identity for graceful display
        gh_profile = {
            "login": clean_username,
            "name": clean_username,
            "avatar_url": f"https://github.com/{clean_username}.png",
        }
    elif not gh_profile:
        gh_profile = {
            "login": clean_username,
            "avatar_url": f"https://github.com/{clean_username}.png",
        }

    # 2. Vouch store aggregation & dynamic scoring for newly searched reviewer
    vouch_prs = store.get_prs_by_reviewer(clean_username)

    existing_keys = {
        str(p.get("pr_key") or f"{p.get('repo')}#{p.get('number') or p.get('pr_number')}").lower()
        for p in vouch_prs
    }
    newly_scored_prs = []
    if gh_prs and isinstance(gh_prs, list):
        for item in gh_prs[:30]:
            repo_url = item.get("repository_url", "")
            if "/repos/" not in repo_url:
                continue
            repo_name = repo_url.split("/repos/")[-1].strip()
            pr_num = item.get("number")
            if not repo_name or not pr_num:
                continue
            key = f"{repo_name}#{pr_num}".lower()
            if key in existing_keys:
                continue

            pr_candidate = {
                "pr_key": f"{repo_name}#{pr_num}",
                "repo": repo_name,
                "number": int(pr_num),
                "pr_number": int(pr_num),
                "title": item.get("title") or "",
                "body": item.get("body") or "",
                "state": item.get("state", "open"),
                "created_at": item.get("created_at") or "",
                "updated_at": item.get("updated_at") or "",
                "closed_at": item.get("closed_at"),
                "html_url": item.get("html_url") or f"https://github.com/{repo_name}/pull/{pr_num}",
                "reviewer": clean_username,
                "reviewers": [clean_username],
                "author": (item.get("user") or {}).get("login", "unknown"),
            }
            try:
                scored = _score_pr_heuristic(pr_candidate)
                scored["reviewer"] = clean_username
                if "reviewers" not in scored or not scored["reviewers"]:
                    scored["reviewers"] = [clean_username]
                elif clean_username not in scored["reviewers"]:
                    scored["reviewers"].append(clean_username)
                newly_scored_prs.append(scored)
                existing_keys.add(key)
            except Exception as e:
                logger.debug("Heuristic scoring skipped for %s: %s", key, e)

        if newly_scored_prs:
            try:
                store.save_prs(newly_scored_prs)
                known_repos = store.get_repos()
                for sp in newly_scored_prs:
                    r_full = sp.get("repo")
                    if r_full and r_full not in known_repos:
                        owner_p, _, repo_p = r_full.partition("/")
                        store.save_repo({
                            "full_name": r_full,
                            "owner": owner_p,
                            "repo": repo_p,
                            "description": f"Repository {r_full} on GitHub",
                            "stars": "—",
                            "forks": "—",
                            "language": "Code",
                            "language_color": "#586069",
                            "is_public": True,
                            "active_prs_count": 0,
                            "closed_prs_count": 0,
                            "avg_residual_risk": 0.0,
                        })

            except Exception as e:
                logger.warning("Failed saving discovered PRs for %s: %s", clean_username, e)

            vouch_prs = store.get_prs_by_reviewer(clean_username)

    total_reviews_in_vouch = len(vouch_prs)


    def _extract_depth(p: dict) -> float:
        d = p.get("review_depth")
        if d is None:
            d = p.get("depth_score")
        if d is None:
            d = p.get("review_confidence")
        try:
            return float(d or 0.0)
        except (ValueError, TypeError):
            return 0.0

    def _extract_ts(p: dict) -> float:
        for f in ("scored_at", "reviewed_at", "updated_at", "created_at"):
            val = p.get(f)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str) and val.strip():
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
                except Exception:
                    pass
        return 0.0

    depth_scores = [_extract_depth(p) for p in vouch_prs]
    avg_depth_score = (sum(depth_scores) / len(depth_scores)) if depth_scores else 0.0

    rubber_stamps = sum(1 for d in depth_scores if d < 0.15)
    rubber_stamp_rate = (rubber_stamps / total_reviews_in_vouch) if total_reviews_in_vouch > 0 else 0.0

    high_risk_rubber_stamped = sum(
        1 for p in vouch_prs
        if float(p.get("change_risk", 0.0) or 0.0) >= 0.65 and _extract_depth(p) < 0.15
    )

    from collections import Counter, defaultdict
    domain_counts = Counter()
    for p in vouch_prs:
        flags = p.get("path_flags") or []
        if isinstance(flags, list):
            for f in flags:
                if f:
                    domain_counts[str(f)] += 1
        elif isinstance(flags, str) and flags:
            domain_counts[flags] += 1
    top_domains = domain_counts.most_common(3)

    # Calibrated reviewer grade calculation
    if (avg_depth_score >= 0.22 and rubber_stamp_rate <= 0.20) or (avg_depth_score >= 0.15 and rubber_stamp_rate <= 0.12 and high_risk_rubber_stamped == 0):
        grade = "A"
    elif (avg_depth_score >= 0.14 and rubber_stamp_rate <= 0.35) or (avg_depth_score >= 0.25 and rubber_stamp_rate <= 0.40):
        grade = "B"
    elif rubber_stamp_rate <= 0.50 and avg_depth_score >= 0.08:
        grade = "C"
    else:
        grade = "D"

    grade_descriptions = {
        "A": f"Exemplary review rigor. Avg depth {avg_depth_score:.2f} with low rubber-stamp rate ({rubber_stamp_rate*100:.1f}%).",
        "B": f"Solid review discipline. Avg depth {avg_depth_score:.2f} across active repositories.",
        "C": f"Moderate review depth ({avg_depth_score:.2f}). {rubber_stamp_rate*100:.1f}% of reviews were quick approvals.",
        "D": f"High rubber-stamp rate ({rubber_stamp_rate*100:.1f}%). Review depth needs attention.",
    }

    # Per-repository statistics
    repo_groups = defaultdict(list)
    for p in vouch_prs:
        repo_groups[p.get("repo", "unknown")].append(p)

    per_repo_stats = []
    for repo_name, pr_list in sorted(repo_groups.items(), key=lambda x: len(x[1]), reverse=True)[:6]:
        r_depths = [_extract_depth(p) for p in pr_list]
        r_stamps = sum(1 for d in r_depths if d < 0.15)

        sorted_prs = sorted(pr_list, key=_extract_ts)
        prior = sorted_prs[:-5] if len(sorted_prs) > 5 else []
        recent = sorted_prs[-5:]
        prior_depths = [_extract_depth(p) for p in prior]
        recent_depths = [_extract_depth(p) for p in recent]
        prior_avg = (sum(prior_depths) / len(prior_depths)) if prior_depths else None
        recent_avg = (sum(recent_depths) / len(recent_depths)) if recent_depths else 0.0

        if prior_avg is None:
            trend = "stable"
        elif recent_avg > prior_avg + 0.05:
            trend = "improving"
        elif recent_avg < prior_avg - 0.05:
            trend = "degrading"
        else:
            trend = "stable"

        sparkline = [_extract_depth(p) for p in sorted_prs[-10:]]

        per_repo_stats.append({
            "repo": repo_name,
            "reviews_count": len(pr_list),
            "avg_depth_score": round(sum(r_depths) / len(r_depths), 3) if r_depths else 0.0,
            "rubber_stamp_rate": round(r_stamps / len(pr_list), 3) if pr_list else 0.0,
            "trend": trend,
            "sparkline": sparkline,
        })

    # Fatigue state calculation
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    today_prs = [p for p in vouch_prs if _extract_ts(p) >= today_start.timestamp()]
    consecutive_today = len(today_prs)

    def is_off_hours(ts: float) -> bool:
        if not ts:
            return False
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.hour >= 23 or dt.hour < 6

    def is_weekend(ts: float) -> bool:
        if not ts:
            return False
        return datetime.fromtimestamp(ts, tz=timezone.utc).weekday() >= 5

    off_hours_count = sum(1 for p in vouch_prs if is_off_hours(_extract_ts(p)))
    off_hours_pct = (off_hours_count / total_reviews_in_vouch) if total_reviews_in_vouch else 0.0

    weekend_count = sum(1 for p in vouch_prs if is_weekend(_extract_ts(p)))
    weekend_pct = (weekend_count / total_reviews_in_vouch) if total_reviews_in_vouch else 0.0

    # Session depth trend: slope of last 10 review depth scores
    last_10 = sorted(vouch_prs, key=_extract_ts)[-10:]
    if len(last_10) >= 3:
        depths_seq = [_extract_depth(p) for p in last_10]
        n = len(depths_seq)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(depths_seq) / n
        denom = sum((x - x_mean) ** 2 for x in xs)
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, depths_seq)) / denom if denom != 0 else 0.0
    else:
        slope = 0.0

    if consecutive_today >= 8 or (off_hours_pct > 0.35 and avg_depth_score < 0.35):
        fatigue_state_label = "high"
    elif consecutive_today >= 4 or slope < -0.05:
        fatigue_state_label = "moderate"
    else:
        fatigue_state_label = "healthy"

    all_ts = [_extract_ts(p) for p in vouch_prs if _extract_ts(p) > 0]
    last_hour = datetime.fromtimestamp(max(all_ts), tz=timezone.utc).hour if all_ts else 0

    fatigue_state = {
        "state": fatigue_state_label,
        "z_score": round(slope * -10, 2),
        "consecutive_reviews_today": consecutive_today,
        "last_review_hour": last_hour,
        "off_hours_reviews_pct": round(off_hours_pct * 100, 1),
        "weekend_reviews_pct": round(weekend_pct * 100, 1),
        "session_depth_trend": round(slope, 4),
    }

    # Priority queue: open PRs where reviewer is assigned
    open_assigned = []
    u_lower = clean_username.lower()
    for p in vouch_prs:
        if p.get("state") == "open":
            r_list = p.get("reviewers") or []
            r_single = p.get("reviewer") or ""
            is_assigned = False
            if isinstance(r_list, list) and any(str(r).strip().lower() == u_lower for r in r_list if r):
                is_assigned = True
            elif isinstance(r_list, str) and r_list.strip().lower() == u_lower:
                is_assigned = True
            elif isinstance(r_single, str) and r_single.strip().lower() == u_lower:
                is_assigned = True

            if is_assigned:
                open_assigned.append(p)

    open_assigned.sort(key=lambda p: float(p.get("change_risk", 0.0) or 0.0), reverse=True)

    priority_queue = []
    for p in open_assigned[:15]:
        opened_ts = p.get("created_at")
        wait_hours = 0.0
        if opened_ts:
            try:
                if isinstance(opened_ts, (int, float)):
                    wait_hours = (datetime.now(timezone.utc).timestamp() - float(opened_ts)) / 3600
                else:
                    opened_dt = datetime.fromisoformat(str(opened_ts).replace("Z", "+00:00"))
                    wait_hours = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
            except Exception:
                pass

        cr = float(p.get("change_risk", 0.0) or 0.0)
        risk_tier = "high" if cr >= 0.65 else ("medium" if cr >= 0.35 else "low")
        pr_num = p.get("number") or p.get("pr_number", 0)

        priority_queue.append({
            "pr_key": p.get("pr_key", f"{p.get('repo', '')}#{pr_num}"),
            "repo": p.get("repo", ""),
            "title": str(p.get("title", ""))[:60],
            "number": int(pr_num),
            "change_risk": round(cr, 3),
            "risk_tier": risk_tier,
            "wait_hours": round(wait_hours, 1),
            "path_flags": p.get("path_flags", []),
            "url": p.get("html_url", f"https://github.com/{p.get('repo', '')}/pull/{pr_num}"),
        })

    # LLM Intervention
    next_pr = priority_queue[0] if priority_queue else None
    trend_pct = abs(int(slope * 100)) if slope < 0 else 0

    intervention_input = {
        "reviewer": clean_username,
        "fatigue_state": fatigue_state_label,
        "z_score": fatigue_state["z_score"],
        "consecutive_reviews_today": consecutive_today,
        "session_depth_trend": slope,
        "rubber_stamp_rate": f"{rubber_stamp_rate*100:.1f}%",
        "off_hours_pct": f"{off_hours_pct*100:.1f}%",
        "trend_pct": str(trend_pct),
        "next_high_risk_pr": {
            "repo": next_pr["repo"],
            "number": next_pr["number"],
            "change_risk": next_pr["change_risk"],
            "path_flags": next_pr["path_flags"],
            "wait_hours": next_pr["wait_hours"],
        } if next_pr else None,
    }

    try:
        from explain.bedrock_client import BedrockClient
        intervention_text, intervention_bot = BedrockClient().generate_reviewer_intervention_with_meta(intervention_input)
    except Exception as exc:
        logger.error("Failed to generate intervention for %s: %s", clean_username, exc)
        intervention_text = "Reviewer cadence and depth scores are monitored."
        intervention_bot = "Vouch Rule Engine"

    # Strip any emojis/pictographs from the intervention text
    emoji_re = re.compile(
        r"[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50\u2b55\u200d\ufe0f\ufe0e]+",
        flags=re.UNICODE,
    )
    intervention_text = emoji_re.sub("", intervention_text)
    intervention_text = re.sub(r" +", " ", intervention_text).strip()

    # ── Workload & Capacity Calculation ──────────────────────────────────────
    now_ts = time.time()
    reviews_today = consecutive_today
    one_week_ago = now_ts - 7 * 86400
    reviews_this_week = sum(1 for p in vouch_prs if _extract_ts(p) >= one_week_ago)
    open_assigned_count = len(open_assigned)
    high_risk_open_count = sum(
        1 for p in open_assigned
        if float(p.get("change_risk", 0.0) or 0.0) >= 0.65
    )

    load_raw = (
        min(1.0, reviews_today / 10) * 0.40 +
        min(1.0, open_assigned_count / 8) * 0.35 +
        min(1.0, high_risk_open_count / 5) * 0.25
    )
    load_score = round(load_raw, 3)
    load_pct = min(100, int(load_score * 100))

    if load_score >= 0.70:
        capacity_tier = "overloaded"
        capacity_label = "Overloaded"
        capacity_badge_class = "gh-label-high"
    elif load_score >= 0.40:
        capacity_tier = "busy"
        capacity_label = "Busy"
        capacity_badge_class = "gh-label-medium"
    else:
        capacity_tier = "available"
        capacity_label = "Available"
        capacity_badge_class = "gh-label-low"

    workload = {
        "capacity_tier": capacity_tier,
        "capacity_label": capacity_label,
        "capacity_badge_class": capacity_badge_class,
        "load_score": load_score,
        "load_pct": load_pct,
        "reviews_today": reviews_today,
        "reviews_this_week": reviews_this_week,
        "open_assigned_count": open_assigned_count,
        "high_risk_open_count": high_risk_open_count,
    }

    # ── Reviewer Pairing Intelligence ────────────────────────────────────────
    co_reviewer_counts = defaultdict(lambda: {"reviews": 0, "depths": [], "requeued": 0})
    author_pair_counts = defaultdict(lambda: {"reviews": 0, "depths": [], "requeued": 0})

    u_clean_lower = clean_username.lower()

    for p in vouch_prs:
        p_depth = _extract_depth(p)
        p_requeued = 1 if p.get("re_queued") else 0

        # Check author
        auth = str(p.get("author") or "").strip()
        if auth and auth.lower() != u_clean_lower and auth.lower() not in ("unknown", "collaborator", "none"):
            author_pair_counts[auth]["reviews"] += 1
            author_pair_counts[auth]["depths"].append(p_depth)
            author_pair_counts[auth]["requeued"] += p_requeued

        # Check co-reviewers
        revs = p.get("reviewers") or []
        if isinstance(revs, str):
            revs = [revs]
        single_r = p.get("reviewer")
        if single_r and single_r not in revs:
            revs = list(revs) + [single_r]

        for r in revs:
            r_str = str(r or "").strip()
            if r_str and r_str.lower() != u_clean_lower and r_str.lower() not in ("unknown", "collaborator", "none", "none requested"):
                co_reviewer_counts[r_str]["reviews"] += 1
                co_reviewer_counts[r_str]["depths"].append(p_depth)
                co_reviewer_counts[r_str]["requeued"] += p_requeued

    pairing_list = []
    for co_rev, stats in co_reviewer_counts.items():
        cnt = stats["reviews"]
        avg_d = sum(stats["depths"]) / len(stats["depths"]) if stats["depths"] else 0.0
        rq_rate = stats["requeued"] / cnt if cnt else 0.0
        pairing_list.append({
            "login": co_rev,
            "type": "Co-Reviewer",
            "reviews_count": cnt,
            "avg_depth": round(avg_d, 2),
            "requeue_rate_pct": int(rq_rate * 100),
            "is_co_reviewer": True,
        })

    for auth, stats in author_pair_counts.items():
        cnt = stats["reviews"]
        if cnt >= 2 or not pairing_list:
            avg_d = sum(stats["depths"]) / len(stats["depths"]) if stats["depths"] else 0.0
            rq_rate = stats["requeued"] / cnt if cnt else 0.0
            pairing_list.append({
                "login": auth,
                "type": "Author Pair",
                "reviews_count": cnt,
                "avg_depth": round(avg_d, 2),
                "requeue_rate_pct": int(rq_rate * 100),
                "is_co_reviewer": False,
            })

    pairing_list.sort(key=lambda x: (x["reviews_count"], 1 if x["is_co_reviewer"] else 0), reverse=True)
    frequent_pairs = pairing_list[:3]

    recent_reviewers = store.get_all_reviewer_usernames(limit=10)

    return {
        "username": clean_username,
        "github_profile": gh_profile,
        "github_rate_limited": github_rate_limited,
        "total_reviews_in_vouch": total_reviews_in_vouch,
        "avg_depth_score": round(avg_depth_score, 3),
        "rubber_stamp_rate": round(rubber_stamp_rate, 3),
        "high_risk_rubber_stamped": high_risk_rubber_stamped,
        "grade": grade,
        "grade_description": grade_descriptions[grade],
        "domain_expertise": top_domains,
        "per_repo_stats": per_repo_stats,
        "fatigue_state": fatigue_state,
        "priority_queue": priority_queue,
        "intervention": intervention_text,
        "intervention_bot": intervention_bot,
        "recent_reviewers": recent_reviewers,
        "workload": workload,
        "frequent_pairs": frequent_pairs,
    }


@app.route("/reviewer")
def reviewer_search():
    force_search = request.args.get("view") == "search"
    active_reviewer = session.get("active_reviewer")
    if not force_search and active_reviewer:
        return reviewer_profile(active_reviewer)

    recent = store.get_all_reviewer_usernames(limit=10)
    total_reviewers = len(store.get_all_reviewer_usernames(limit=None))
    total_repos = len(store.get_repos())
    return render_template(
        "reviewer.html",
        mode="search",
        recent_reviewers=recent,
        total_reviewers=total_reviewers,
        total_repos=total_repos,
    )



@app.route("/reviewer/<username>")
def reviewer_profile(username):
    session["active_reviewer"] = username
    if request.args.get("refresh"):
        _reviewer_cache.pop(username, None)

    cached = _reviewer_cache.get(username)
    if cached and (time.time() - cached["ts"]) < REVIEWER_CACHE_TTL:
        data = dict(cached["data"])
        data["is_self"] = (session.get("github_login") == username)
        return render_template("reviewer.html", mode="profile", **data)

    gh_token = session.get("github_token") or request.args.get("token")
    try:
        data = _build_reviewer_profile(username, gh_token)
    except ValueError as e:
        return render_template(
            "reviewer.html",
            mode="not_found",
            username=username,
            error=str(e),
        ), 404
    except Exception as e:
        app.logger.error("Reviewer profile error for %s: %s", username, e)
        return render_template(
            "reviewer.html",
            mode="error",
            username=username,
            error="Failed to load reviewer profile.",
        ), 500

    _reviewer_cache[username] = {"data": dict(data), "ts": time.time()}
    data["is_self"] = (session.get("github_login") == username)
    return render_template("reviewer.html", mode="profile", **data)


@app.route("/api/reviewer/<username>")
def api_reviewer(username):
    cached = _reviewer_cache.get(username)
    if cached and (time.time() - cached["ts"]) < REVIEWER_CACHE_TTL:
        return jsonify(cached["data"])
    gh_token = session.get("github_token") or request.args.get("token")
    try:
        data = _build_reviewer_profile(username, gh_token)
        _reviewer_cache[username] = {"data": dict(data), "ts": time.time()}
        return jsonify(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reviewer/<username>/refresh")
def api_reviewer_refresh(username):
    _reviewer_cache.pop(username, None)
    return jsonify({"status": "cache cleared", "username": username})


# ─────────────────────────────────────────────────────────────────────────────
# Production Error Handlers
# ─────────────────────────────────────────────────────────────────────────────

@app.errorhandler(400)
def bad_request_error(err):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": getattr(err, "description", "Bad request")}), 400
    return render_template(
        "404.html",
        error_title="Bad Request",
        message="The request was invalid or malformed.",
    ), 400


@app.errorhandler(404)
def not_found_error(err):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Endpoint not found"}), 404
    return render_template(
        "404.html",
        error_title="Page Not Found",
        message="The page, repository, or pull request you are looking for could not be found.",
    ), 404


@app.errorhandler(500)
def internal_server_error(err):
    logger.error("Internal Server Error on %s: %s", request.path, err, exc_info=True)
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "An internal server error occurred"}), 500
    return render_template(
        "404.html",
        error_title="Internal Server Error",
        message="An unexpected server error occurred while processing your request. Please try again later.",
    ), 500


if __name__ == "__main__":

    app.run(debug=True, port=5000)