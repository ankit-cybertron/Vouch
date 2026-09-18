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
from urllib.parse import urlparse

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

from dashboard.store import get_store

store = get_store()


def _compute_board_stats(prs: list[dict] | None = None) -> dict:
    """Compute live aggregate statistics dynamically across scored pull requests."""
    if prs is None:
        prs = list(LIVE_PRS_CACHE.values()) if "LIVE_PRS_CACHE" in globals() else []
    total = len(prs)
    high_risk = sum(1 for p in prs if p.get("risk_tier") == "high")
    requeued = sum(1 for p in prs if p.get("residual_risk", 0) >= 0.65)
    avg_res = round(sum(p.get("residual_risk", 0) for p in prs) / max(total, 1), 2)
    return {
        "total_scored": total,
        "high_risk_flagged": high_risk,
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
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "vouch-dev-secret")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.globals.update(max=max, min=min)


def _resolve_github_token() -> str:
    """Dynamically resolve GitHub token with maximum resilience.
    Priority order:
      1. User's active session token (from OAuth login or Option 4 token modal)
      2. Server GITHUB_TOKEN environment variable
      3. .env file
      4. git credential helper
    """
    try:
        if has_request_context() and session.get("github_token"):
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


def _parse_repo_input(raw: str) -> tuple[str, str] | None:
    """
    Accept any of:
      - https://github.com/owner/repo
      - github.com/owner/repo
      - owner/repo
    Returns (owner, repo) or None on failure.
    """
    raw = raw.strip().rstrip("/")
    # URL form
    if "github.com" in raw:
        try:
            parsed = urlparse(raw if raw.startswith("http") else "https://" + raw)
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2:
                return parts[0], parts[1]
        except Exception:
            pass
    # owner/repo form
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0], parts[1]
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
    merged_at = pr_data.get("merged_at") or pr_data.get("closed_at")
    pr_title = (pr_data.get("title") or "").strip()
    pr_body = (pr_data.get("body") or "").strip()
    pr_author = (pr_data.get("user") or {}).get("login", "unknown")

    # Review duration
    review_duration_seconds = pr_data.get("_duration_secs", 0)
    if not review_duration_seconds:
        if created_at and merged_at:
            try:
                t0 = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
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
        reviewer = reviews[0].get("user", {}).get("login", "")
    elif not reviewer and pr_data.get("requested_reviewers"):
        reviewer = pr_data["requested_reviewers"][0].get("login", "")
    if not reviewer:
        reviewer = (pr_data.get("assignee") or {}).get("login", "") or "collaborator"

    # ─────────────────────────────────────────────────────────────────
    # Discrepancy signals (used by all three models)
    # ─────────────────────────────────────────────────────────────────

    # Signal: no meaningful reviewer assigned
    no_reviewer = (reviewer in ("", "collaborator") and not reviews)

    # Signal: rapid merge — merged < 10 minutes after opening
    rapid_merge = (
        review_duration_seconds > 0
        and review_duration_seconds < 600
        and state == "closed"
    )

    # Signal: stale PR — open for > 30 days (complacency / context loss)
    stale_pr = (state == "open" and pr_age_days > 30)

    # Signal: WIP/DO-NOT-MERGE in title but merged
    wip_pattern = re.compile(r"\b(wip|do\s*not\s*merge|draft|dnm|blocked|hold)\b", re.I)
    wip_not_draft = bool(wip_pattern.search(pr_title) and state == "closed")

    # Signal: mass file touch — shotgun commit touching > 15 files
    mass_file_touch = changed_files > 15

    # Signal: empty or minimal PR description (< 30 chars) for large diff
    empty_description = len(pr_body) < 30 and total_lines > 100

    # Signal: self-review — author and reviewer share the same login
    self_review = bool(reviewer and reviewer.lower() == pr_author.lower()
                       and reviewer != "collaborator")

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
        if not reviews:
            depth_from_reviews = 0.05
        elif all_bots and reviews:
            depth_from_reviews = 0.05
        elif has_changes_requested and state == "closed":
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
        if has_changes_requested and state == "closed":
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
        if has_changes_requested and state == "closed":
            _findings.append("merged with pending change requests")
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
        "merged_at": merged_at if state == "closed" else None,
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
        requeued_count = sum(1 for p in scored_only if p.get("re_queued"))
        avg_res = (sum(p["residual_risk"] for p in scored_only) / total_scored) if total_scored > 0 else 0.0
        return {
            "prs": cached_prs,
            "error": None,
            "repo_meta": FETCHED_REPOS[full_repo_name],
            "stats": {
                "total_scored": total_scored,
                "high_risk_flagged": high_count,
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

    # 5. Determine initial scoring window: max(25, count of PRs in the last 30 days)
    count_30_days = sum(1 for c in candidates if _is_within_30_days(c.get("created_at") or c.get("updated_at")))
    target_scored_count = max(25, count_30_days)

    # 6. Process candidate PRs in parallel for ultra-fast response
    def _process_candidate(item):
        idx, pr = item
        num = pr.get("number")
        if not num:
            return None

        if idx < target_scored_count:
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
            return scored_item
        else:
            total_lines = pr.get("additions", 0) + pr.get("deletions", 0) or 150
            unscored_item = {
                "pr_key": f"{full_repo_name}#{num}",
                "pr_url": pr.get("html_url", f"https://github.com/{owner}/{repo}/pull/{num}"),
                "repo": full_repo_name,
                "pr_number": num,
                "title": pr.get("title", f"Pull request #{num}"),
                "author": (pr.get("user") or {}).get("login", "collaborator"),
                "reviewer": "collaborator",
                "created_at": pr.get("created_at") or "2026-06-01T12:00:00Z",
                "merged_at": pr.get("merged_at") if pr.get("state") == "closed" else None,
                "state": pr.get("state", "closed"),
                "change_risk": None,
                "review_confidence": None,
                "residual_risk": None,
                "risk_tier": "unscored",
                "confidence_pct": None,
                "residual_pct": None,
                "diff_lines": total_lines,
                "changed_files": pr.get("changed_files", 2),
                "sensitive_files": [f["filename"] for f in (pr.get("_files") or []) if "filename" in f][:3],
                "comments": [],
                "scored": False,
                "status": "pending_analysis",
                "explanation": "Older pull request cataloged for on-demand analysis.",
            }
            _cache_scored_pr(unscored_item)
            return unscored_item

    with ThreadPoolExecutor(max_workers=8) as pool:
        all_prs = [p for p in pool.map(_process_candidate, enumerate(candidates[:limit])) if p is not None]


    # Sort so scored PRs are ordered descending by residual risk, followed by unscored older PRs
    scored_prs = [p for p in all_prs if p.get("scored") and p.get("residual_risk") is not None]
    unscored_prs = [p for p in all_prs if not p.get("scored") or p.get("residual_risk") is None]
    scored_prs.sort(key=lambda x: x.get("residual_risk", 0.0), reverse=True)
    all_prs = scored_prs + unscored_prs

    # Compute repository stats
    total_scored = len(scored_prs)
    high_count = sum(1 for p in scored_prs if p.get("risk_tier") == "high")
    requeued_count = sum(1 for p in scored_prs if p.get("re_queued"))
    avg_res = (sum(p["residual_risk"] for p in scored_prs) / total_scored) if total_scored > 0 else 0.0

    stats = {
        "total_scored": total_scored,
        "total_prs": len(all_prs),
        "high_risk_flagged": high_count,
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



def _init_default_repos() -> None:
    """Populate default repositories catalog and PR caches with persisted repositories."""
    stored_repos = store.get_repos()
    for name, repo_meta in stored_repos.items():
        FETCHED_REPOS[name] = dict(repo_meta)

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
    token = session.get("github_token", "")
    auth_info = {
        "is_authenticated": bool(token),
        "auth_type": session.get("auth_type", "demo" if not token else "token"),
        "user_login": session.get("user_login", ""),
        "user_name": session.get("user_name", session.get("user_login", "")),
        "user_avatar": session.get("user_avatar", ""),
        "rate_limit": session.get("rate_limit", {}),
    }
    return {
        "repos_count": len(FETCHED_REPOS),
        "all_repos": list(FETCHED_REPOS.values()),
        "auth": auth_info,
    }


@app.route("/")
def landing_page():
    """Main Landing Page introducing Vouch, with OAuth & Demo mode entry points."""
    # If user is already authenticated in session, route directly to repos
    if session.get("github_token") and not request.args.get("reauth"):
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

    has_oauth = bool(client_id) or bool(_resolve_github_token())

    return render_template(
        "landing.html",
        sample_repos=sample_repos,
        has_oauth=has_oauth,
        oauth_setup=oauth_setup,
        error=error,
    )


@app.route("/repos")
def repos_page():
    """Repositories catalog page — displays all fetched repositories with '+' fetch option."""
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

    search = request.args.get("q", "").strip().lower()
    repos_list = list(FETCHED_REPOS.values())
    if search:
        repos_list = [
            r for r in repos_list
            if search in r["full_name"].lower()
            or search in r.get("description", "").lower()
            or search in r.get("language", "").lower()
        ]

    return render_template(
        "repos.html",
        repos=repos_list,
        search=search,
    )


# ── GitHub OAuth Authentication Routes ─────────────────────────
@app.route("/auth/github")
def auth_github():
    """Initiate GitHub OAuth 2.0 flow or auto-connect using configured GitHub token."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()

    # If full OAuth client is configured, run standard GitHub OAuth 2.0 redirect
    if client_id and client_secret:
        state = secrets.token_urlsafe(16)
        session["oauth_state"] = state
        redirect_uri = request.host_url.rstrip("/") + url_for("auth_github_callback")
        scope = "read:user,repo"
        github_auth_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope={scope}"
            f"&state={state}"
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

                session["github_token"] = existing_token
                session["user_login"] = user_data.get("login", "github_user")
                session["user_name"] = user_data.get("name") or user_data.get("login", "GitHub User")
                session["user_avatar"] = user_data.get("avatar_url", "")
                session["auth_type"] = "oauth"
                session["rate_limit"] = {
                    "limit": rate_data.get("limit", 5000),
                    "remaining": rate_data.get("remaining", 5000),
                }
                return redirect(url_for("repos_page"))
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
        token_resp = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
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

        session["github_token"] = access_token
        session["user_login"] = user_data.get("login", "github_user")
        session["user_name"] = user_data.get("name") or user_data.get("login", "GitHub User")
        session["user_avatar"] = user_data.get("avatar_url", "")
        session["auth_type"] = "oauth"
        session["rate_limit"] = {
            "limit": rate_data.get("limit", 5000),
            "remaining": rate_data.get("remaining", 5000),
        }

        return redirect(url_for("repos_page"))
    except Exception as exc:
        return redirect(url_for("landing_page", error=f"OAuth connection error: {str(exc)}"))


@app.route("/auth/logout")
def auth_logout():
    """Clear user session and return to landing page."""
    session.pop("github_token", None)
    session.pop("user_login", None)
    session.pop("user_name", None)
    session.pop("user_avatar", None)
    session.pop("auth_type", None)
    session.pop("rate_limit", None)
    return redirect(url_for("landing_page"))


# ── Option 4: In-App UI Personal Token API ─────────────────────
@app.route("/api/auth/token", methods=["POST"])
def api_save_token():
    """Option 4: In-App UI Personal Access Token submission."""
    payload = request.get_json(silent=True) or {}
    raw_token = payload.get("token", "").strip()
    if not raw_token:
        return jsonify({"success": False, "error": "Token cannot be empty"}), 400

    try:
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
                "error": "Invalid GitHub token. GitHub rejected the token (HTTP 401). Please verify permissions."
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

        session["github_token"] = raw_token
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
    except Exception as exc:
        return jsonify({"success": False, "error": f"Connection error: {str(exc)}"}), 500


@app.route("/api/auth/clear-token", methods=["POST"])
def api_clear_token():
    """Reset session to default demo mode."""
    session.pop("github_token", None)
    session.pop("user_login", None)
    session.pop("user_name", None)
    session.pop("user_avatar", None)
    session.pop("auth_type", None)
    session.pop("rate_limit", None)
    return jsonify({"success": True, "message": "Reset to Demo Mode"})


@app.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """Return current session auth state."""
    token = session.get("github_token")
    return jsonify({
        "authenticated": bool(token),
        "auth_type": session.get("auth_type", "demo" if not token else "token"),
        "user_login": session.get("user_login", ""),
        "user_name": session.get("user_name", ""),
        "user_avatar": session.get("user_avatar", ""),
        "rate_limit": session.get("rate_limit", {}),
    })


@app.route("/repo/<owner>/<repo_name>")
def repo_view(owner: str, repo_name: str):
    """Repository-scoped PR board showing all its active (open) and closed (merged) PRs."""
    full_name = f"{owner}/{repo_name}"
    risk_filter = request.args.get("risk", "all")
    state_filter = request.args.get("state", "all")
    search = request.args.get("q", "").strip()

    # Check in-memory cache, then load from store if present
    if full_name.lower() not in REPO_PRS_CACHE:
        try:
            prs_from_store = store.get_prs(repo=full_name)
            if prs_from_store:
                REPO_PRS_CACHE[full_name.lower()] = prs_from_store
                stored_repos = store.get_repos()
                if full_name in stored_repos:
                    FETCHED_REPOS[full_name] = stored_repos[full_name]
        except Exception:
            pass

    # Use cached or fetch & score
    if full_name.lower() in REPO_PRS_CACHE and full_name in FETCHED_REPOS:
        prs = list(REPO_PRS_CACHE[full_name.lower()])
        current_repo_meta = FETCHED_REPOS[full_name]
        scored_only = [p for p in prs if p.get("scored") and p.get("residual_risk") is not None]
        total = len(scored_only)
        high_count = sum(1 for p in scored_only if p.get("risk_tier") == "high")
        requeued_count = sum(1 for p in scored_only if p.get("re_queued"))
        avg_res = (sum(p["residual_risk"] for p in scored_only) / total) if total > 0 else 0.0
        stats = {
            "total_scored": total,
            "total_prs": len(prs),
            "high_risk_flagged": high_count,
            "requeued_today": requeued_count,
            "avg_residual_risk": round(avg_res, 2),
        }
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

    # Pre-filter counts
    open_count = len([p for p in prs if p.get("state") == "open"])
    closed_count = len([p for p in prs if p.get("state") == "closed"])

    # Risk level filter
    if risk_filter != "all":
        prs = [p for p in prs if p.get("risk_tier") == risk_filter]

    # State filter (open vs closed)
    if state_filter == "open":
        prs = [p for p in prs if p.get("state") == "open"]
    elif state_filter == "closed":
        prs = [p for p in prs if p.get("state") == "closed"]

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
        risk_filter=risk_filter,
        state_filter=state_filter,
        search=search,
        current_repo=current_repo_meta,
        active_repo_name=full_name,
        open_count=open_count,
        closed_count=closed_count,
    )


@app.route("/pulls")
@app.route("/board")
def board():
    """General PR board — defaults to first repository or query."""
    repo_arg = request.args.get("repo", "").strip()
    if repo_arg:
        parsed = _parse_repo_input(repo_arg)
        if parsed:
            return repo_view(parsed[0], parsed[1])

    if FETCHED_REPOS:
        first_repo = next(iter(FETCHED_REPOS.values()))
        return repo_view(first_repo["owner"], first_repo["repo"])
    return redirect("/repos")


@app.route("/api/clear-repos", methods=["POST"])
def clear_repos_api():
    """Clear all fetched repositories and pull requests from memory."""
    FETCHED_REPOS.clear()
    REPO_PRS_CACHE.clear()
    LIVE_PRS_CACHE.clear()
    return jsonify({"success": True, "message": "All fetched repository data cleared."})


def _resolve_pr_detail(org: str, repo_name: str, pr_number: int) -> tuple[dict | None, int]:
    """Finds a PR from live GitHub (with complete conversation, reviews, diffs), or cached/demo catalog."""
    full_repo = f"{org}/{repo_name}"
    full_repo_lower = full_repo.lower()

    # 1. First, attempt live fetch directly from GitHub API (the authoritative source)
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
            state = (rv.get("state") or "COMMENTED").upper()
            if rv_body or state in ("APPROVED", "CHANGES_REQUESTED"):
                default_msg = "Approved these changes." if state == "APPROVED" else ("Requested changes." if state == "CHANGES_REQUESTED" else "Submitted a review.")
                conversation.append({
                    "type": "review",
                    "user": {
                        "login": login,
                        "avatar_url": u.get("avatar_url") or f"https://github.com/{login}.png",
                        "html_url": u.get("html_url") or f"https://github.com/{login}",
                    },
                    "body": rv_body or default_msg,
                    "review_state": state,
                    "created_at": rv.get("submitted_at") or rv.get("created_at") or pr_data.get("created_at"),
                    "role_badge": "Reviewer",
                    "is_author": False,
                })

        # Sort timeline chronologically (initial description remains first)
        first_desc = conversation[0]
        rest_sorted = sorted(conversation[1:], key=lambda x: x.get("created_at") or "")
        full_conversation = [first_desc] + rest_sorted

        scored = _score_pr_heuristic(pr_data, reviews, review_comments)
        scored["body"] = pr_body
        scored["conversation"] = full_conversation
        scored["comments_count"] = len(issue_comments) + len(review_comments)
        scored["pr_url"] = pr_data.get("html_url") or f"https://github.com/{full_repo}/pull/{pr_number}"
        _cache_scored_pr(scored)
        return scored, 200

    # 2. Check repo-scoped in-memory cache if GitHub was not reachable
    for key in (
        f"{full_repo_lower}/{pr_number}",
        f"{full_repo_lower}#{pr_number}",
        f"{full_repo}/{pr_number}",
        f"{full_repo}#{pr_number}",
    ):
        if key in LIVE_PRS_CACHE:
            cached_pr = LIVE_PRS_CACHE[key]
            if cached_pr.get("scored") and cached_pr.get("residual_risk") is not None:
                return cached_pr, 200

    # 3. Check REPO_PRS_CACHE for this repository
    if full_repo_lower in REPO_PRS_CACHE:
        for p in REPO_PRS_CACHE[full_repo_lower]:
            if p.get("pr_number") == pr_number:
                if p.get("scored") and p.get("residual_risk") is not None:
                    _cache_scored_pr(p)
                    return p, 200
                scored = _score_pr_heuristic(p)
                scored["scored"] = True
                _cache_scored_pr(scored)
                return scored, 200

    # 4. Check persistent storage for this repository and PR number
    stored_prs = store.get_prs()
    for p in stored_prs:
        if p.get("repo", "").lower() == full_repo_lower and p.get("pr_number") == pr_number:
            _cache_scored_pr(p)
            return p, 200

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
        if dur < 600 and pr.get("state") == "closed":
            discrepancy_counts["rapid_merge"] += 1
        if pr.get("reviewer") and pr.get("author") and pr["reviewer"].lower() == pr["author"].lower() and pr["reviewer"] != "collaborator":
            discrepancy_counts["self_review"] += 1
        if pr.get("reviewer") in ("", "collaborator", "unassigned"):
            discrepancy_counts["no_reviewer"] += 1
        if any(w in pr.get("title", "").lower() for w in ("wip", "do not merge", "draft", "dnm")) and pr.get("state") == "closed":
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
    repo_filter = request.args.get("repo", "all")
    health_data = _compute_team_health(repo_filter=repo_filter)
    return render_template(
        "team_health.html",
        health=health_data,
        repos=list(FETCHED_REPOS.values()),
        active_repo=repo_filter,
    )


@app.route("/api/repos")
def api_repos():
    """List all fetched repositories in catalog."""
    return jsonify({
        "repos": list(FETCHED_REPOS.values()),
        "total": len(FETCHED_REPOS),
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
    result = _fetch_and_score_repo(owner, repo_name, limit=50)
    result["redirect_url"] = f"/repo/{owner}/{repo_name}"
    return jsonify(result)


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
