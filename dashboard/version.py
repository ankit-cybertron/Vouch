"""
Vouch Application Version and Build Information.

Provides a unified single source of truth for semantic application versioning,
active git commit hash resolution, branch metadata, and environment detection.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache

__version__ = "2.2"
VERSION_NAME = "Bharat Builds Edition"


@lru_cache(maxsize=1)
def get_git_info() -> dict[str, str]:
    """Retrieve git commit and branch info with safe fallback chains."""
    # 1. Check environment variables (useful in Docker / CI / EB deployments)
    commit_sha = (
        os.environ.get("GIT_COMMIT_SHA")
        or os.environ.get("COMMIT_SHA")
        or os.environ.get("HEROKU_SLUG_COMMIT")
        or ""
    ).strip()
    branch = os.environ.get("GIT_BRANCH", "").strip()

    # 2. Check for local .version or commit file
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version_file = os.path.join(repo_root, ".version")
    if not commit_sha and os.path.exists(version_file):
        try:
            with open(version_file, "r", encoding="utf-8") as vf:
                lines = [l.strip() for l in vf.readlines() if l.strip()]
                for l in lines:
                    if l.startswith("COMMIT="):
                        commit_sha = l.split("=", 1)[1].strip()
                    elif l.startswith("BRANCH="):
                        branch = l.split("=", 1)[1].strip()
        except Exception:
            pass

    # 3. Dynamic git invocation if repository has .git folder
    commit_short = commit_sha[:7] if commit_sha else ""
    if not commit_short:
        try:
            short_rev = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if short_rev.returncode == 0 and short_rev.stdout.strip():
                commit_short = short_rev.stdout.strip()

            full_rev = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if full_rev.returncode == 0 and full_rev.stdout.strip():
                commit_sha = full_rev.stdout.strip()

            if not branch:
                br_proc = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if br_proc.returncode == 0 and br_proc.stdout.strip():
                    branch = br_proc.stdout.strip()
        except Exception:
            pass

    return {
        "commit": commit_sha or "HEAD",
        "commit_short": commit_short or (commit_sha[:7] if commit_sha else "release"),
        "branch": branch or "main",
    }


def get_version_info() -> dict[str, str]:
    """Return comprehensive application version metadata."""
    git_info = get_git_info()
    return {
        "version": __version__,
        "version_tag": f"v{__version__}",
        "version_name": VERSION_NAME,
        "commit": git_info["commit"],
        "commit_short": git_info["commit_short"],
        "branch": git_info["branch"],
    }
