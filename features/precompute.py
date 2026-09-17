"""
Vouch — File history precompute job.

Scheduled daily (or on-demand), this script computes per-file metrics
from git history and writes them into the vouch-files DynamoDB table.
This keeps the hot-path feature extraction Lambda to a simple DynamoDB
lookup rather than live git traversal.

Usage:
    python precompute.py --repo owner/name [--days 90]
"""

from __future__ import annotations

import argparse
import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import git  # GitPython

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_dynamodb = boto3.resource("dynamodb")
FILES_TABLE = "vouch-files-dev"  # override via env or CLI arg

REVERT_PATTERN = re.compile(r"^revert\b", re.I)
HOTFIX_PATTERN = re.compile(r"(hotfix|hot-fix|bugfix|bug-fix|fix:)", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Git analysis helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gini(values: list[int]) -> float:
    """Compute Gini coefficient over author commit counts (ownership concentration)."""
    if not values:
        return 0.0
    n = len(values)
    sorted_v = sorted(values)
    cumulative = sum(sorted_v)
    if cumulative == 0:
        return 0.0
    gini_sum = sum((2 * i - n + 1) * v for i, v in enumerate(sorted_v))
    return gini_sum / (n * cumulative)


def compute_file_metrics(
    repo: git.Repo,
    file_path: str,
    since: datetime,
    org_name: str,
    repo_name: str,
) -> dict:
    """
    Compute historical metrics for a single file path.

    Metrics:
    - churn_90d: number of commits touching this file in the last N days
    - revert_count: number of revert/hotfix commits touching this file
    - defect_density: reverts / total commits (bounded 0–1)
    - ownership_gini: concentration of commits among authors
    - age_days: days since file was last substantively rewritten
    """
    since_ts = since.timestamp()
    commits_in_window = []
    author_counts: dict[str, int] = defaultdict(int)
    revert_count = 0

    try:
        for commit in repo.iter_commits(paths=file_path):
            commit_dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            is_recent = commit_dt.timestamp() >= since_ts

            if is_recent:
                commits_in_window.append(commit)
                author_counts[commit.author.email] += 1

            msg = commit.message.strip()
            if REVERT_PATTERN.match(msg) or HOTFIX_PATTERN.search(msg):
                revert_count += 1

    except git.GitCommandError as exc:
        logger.warning("git error for %s: %s", file_path, exc)
        return {}

    churn = len(commits_in_window)
    total_commits = sum(author_counts.values()) or 1
    defect_density = min(revert_count / total_commits, 1.0)
    ownership_gini = _gini(list(author_counts.values()))

    return {
        "pk": f"{org_name}/{repo_name}#{file_path}",
        "file_path": file_path,
        "repo": f"{org_name}/{repo_name}",
        "churn_90d": churn,
        "revert_count": revert_count,
        "defect_density": round(defect_density, 4),
        "ownership_gini": round(ownership_gini, 4),
        "author_count": len(author_counts),
        "updated_at": int(datetime.now(timezone.utc).timestamp()),
    }


def run_precompute(
    repo_path: str,
    org_name: str,
    repo_name: str,
    days: int = 90,
    table_name: str = FILES_TABLE,
) -> int:
    """
    Run the precompute job over all tracked files in the repo.
    Returns the number of files processed.
    """
    table = _dynamodb.Table(table_name)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    repo = git.Repo(repo_path)
    # Get all tracked file paths
    tracked_files = [
        blob.path
        for item in repo.head.commit.tree.traverse()
        if hasattr(item, "path")
        for blob in [item]
        if item.type == "blob"
    ]

    logger.info("Precomputing history for %d files in %s/%s", len(tracked_files), org_name, repo_name)

    written = 0
    with table.batch_writer() as batch:
        for file_path in tracked_files:
            metrics = compute_file_metrics(repo, file_path, since, org_name, repo_name)
            if metrics:
                batch.put_item(Item=metrics)
                written += 1
                if written % 100 == 0:
                    logger.info("Processed %d / %d files", written, len(tracked_files))

    logger.info("Precompute complete: %d files written to %s", written, table_name)
    return written


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Precompute Vouch file history metrics")
    parser.add_argument("--repo-path", required=True, help="Local path to cloned git repository")
    parser.add_argument("--org", required=True, help="GitHub organisation name")
    parser.add_argument("--repo-name", required=True, help="GitHub repository name")
    parser.add_argument("--days", type=int, default=90, help="Lookback window in days (default 90)")
    parser.add_argument("--table", default=FILES_TABLE, help="DynamoDB table name")
    args = parser.parse_args()

    count = run_precompute(
        repo_path=args.repo_path,
        org_name=args.org,
        repo_name=args.repo_name,
        days=args.days,
        table_name=args.table,
    )
    print(f"Done. {count} files written.")
