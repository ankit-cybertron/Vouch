"""
Vouch — GitHub GraphQL corpus miner.

Pulls PR metadata, reviews, and review comments from a public GitHub
repository via the GraphQL API, with pagination, rate-limit handling,
and a hard cap at 5,000 PRs per run.

Usage:
    python miner.py --repo kubernetes/kubernetes --output-dir ./data/raw --limit 5000
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

GITHUB_API = "https://api.github.com/graphql"
PR_HARD_CAP = 5_000

# ─────────────────────────────────────────────────────────────────────────────
# GraphQL queries
# ─────────────────────────────────────────────────────────────────────────────

PR_LIST_QUERY = """
query PRList($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: 100,
      after: $after,
      states: [MERGED],
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        state
        createdAt
        mergedAt
        closedAt
        additions
        deletions
        changedFiles
        author { login }
        baseRefName
        headRefName
        commits(last: 1) {
          nodes { commit { message oid } }
        }
        files(first: 50) {
          nodes { path additions deletions }
        }
        reviews(first: 50) {
          nodes {
            id
            state
            submittedAt
            author { login }
            body
            comments(first: 50) {
              nodes {
                id
                body
                path
                line
                createdAt
                author { login }
              }
            }
          }
        }
      }
    }
  }
  rateLimit { remaining resetAt }
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# GitHub GraphQL client
# ─────────────────────────────────────────────────────────────────────────────

class GitHubClient:
    def __init__(self, token: str):
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v4+json",
        })

    def query(self, query: str, variables: dict) -> dict:
        resp = self._session.post(
            GITHUB_API,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    def wait_for_rate_limit(self, remaining: int, reset_at: str) -> None:
        if remaining < 10:
            reset_dt = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
            wait = (reset_dt - datetime.now(timezone.utc)).total_seconds() + 5
            logger.warning("Rate limit low (%d remaining). Sleeping %.0fs.", remaining, wait)
            time.sleep(max(wait, 0))


# ─────────────────────────────────────────────────────────────────────────────
# Mining logic
# ─────────────────────────────────────────────────────────────────────────────

def mine_prs(
    client: GitHubClient,
    owner: str,
    name: str,
    limit: int = PR_HARD_CAP,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Mine PRs, reviews, and review comments.
    Returns three lists: prs, reviews, comments.
    Enforces the hard cap.
    """
    prs: list[dict] = []
    reviews: list[dict] = []
    comments: list[dict] = []

    cursor = None
    total = 0

    while total < limit:
        logger.info("Fetching PRs cursor=%s (total so far: %d)", cursor, total)
        data = client.query(PR_LIST_QUERY, {"owner": owner, "name": name, "after": cursor})

        repo_data = data["repository"]["pullRequests"]
        rate = data["rateLimit"]
        client.wait_for_rate_limit(rate["remaining"], rate["resetAt"])

        for node in repo_data["nodes"]:
            if total >= limit:
                break

            pr_number = node["number"]
            repo_full = f"{owner}/{name}"

            # Last commit info
            last_commit = {}
            commits_nodes = node.get("commits", {}).get("nodes", [])
            if commits_nodes:
                last_commit = commits_nodes[-1].get("commit", {})

            pr_record = {
                "repo": repo_full,
                "pr_number": pr_number,
                "title": node["title"],
                "state": node["state"],
                "created_at": node["createdAt"],
                "merged_at": node["mergedAt"],
                "closed_at": node["closedAt"],
                "additions": node["additions"],
                "deletions": node["deletions"],
                "changed_files": node["changedFiles"],
                "author": (node.get("author") or {}).get("login", "ghost"),
                "base_branch": node["baseRefName"],
                "head_branch": node["headRefName"],
                "commit_message": last_commit.get("message", ""),
                "commit_sha": last_commit.get("oid", ""),
                "file_paths": [f["path"] for f in node.get("files", {}).get("nodes", [])],
            }
            prs.append(pr_record)
            total += 1

            # Reviews and comments
            for review_node in node.get("reviews", {}).get("nodes", []):
                review_record = {
                    "repo": repo_full,
                    "pr_number": pr_number,
                    "review_id": review_node["id"],
                    "state": review_node["state"],
                    "submitted_at": review_node["submittedAt"],
                    "reviewer": (review_node.get("author") or {}).get("login", "ghost"),
                    "body": review_node.get("body") or "",
                }
                reviews.append(review_record)

                for comment_node in review_node.get("comments", {}).get("nodes", []):
                    comments.append({
                        "repo": repo_full,
                        "pr_number": pr_number,
                        "review_id": review_node["id"],
                        "comment_id": comment_node["id"],
                        "body": comment_node.get("body") or "",
                        "path": comment_node.get("path"),
                        "line": comment_node.get("line"),
                        "created_at": comment_node.get("createdAt"),
                        "commenter": (comment_node.get("author") or {}).get("login", "ghost"),
                    })

        if not repo_data["pageInfo"]["hasNextPage"]:
            break
        cursor = repo_data["pageInfo"]["endCursor"]
        time.sleep(0.5)  # courtesy delay

    logger.info(
        "Mining complete: %d PRs, %d reviews, %d comments",
        len(prs), len(reviews), len(comments),
    )
    return prs, reviews, comments


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mine GitHub PR corpus")
    parser.add_argument("--repo", required=True, help="owner/name format, e.g. kubernetes/kubernetes")
    parser.add_argument("--output-dir", default="./data/raw", help="Output directory for parquet files")
    parser.add_argument("--limit", type=int, default=PR_HARD_CAP, help=f"PR hard cap (default {PR_HARD_CAP})")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub PAT (or set GITHUB_TOKEN env)")
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("ERROR: GitHub token required (--token or GITHUB_TOKEN env var)")

    owner, name = args.repo.split("/", 1)
    out_dir = Path(args.output_dir) / args.repo.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    client = GitHubClient(args.token)
    prs, reviews, comments = mine_prs(client, owner, name, args.limit)

    pd.DataFrame(prs).to_parquet(out_dir / "prs.parquet", index=False)
    pd.DataFrame(reviews).to_parquet(out_dir / "reviews.parquet", index=False)
    pd.DataFrame(comments).to_parquet(out_dir / "comments.parquet", index=False)

    print(f"Saved to {out_dir}: {len(prs)} PRs, {len(reviews)} reviews, {len(comments)} comments")
