"""
Vouch — SZZ-style defect labeller.

Identifies commits that introduced defects by:
  1. Finding revert or hotfix commits in git history
  2. Extracting the reverted commit SHA from the commit message
  3. Tracing that SHA back to its originating PR via the corpus parquet

Produces a labels.parquet with (repo, pr_number, is_defective, label_source).

Usage:
    python labeller.py --repo-path ./repos/kubernetes \
                       --prs-parquet ./data/raw/kubernetes_kubernetes/prs.parquet \
                       --output ./data/raw/kubernetes_kubernetes/labels.parquet
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

import git
import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REVERT_SHA_RE = re.compile(r'This reverts commit ([0-9a-f]{7,40})', re.I)
HOTFIX_RE = re.compile(r'(hotfix|hot-fix|bugfix|bug-fix|fix\s*(!|:))', re.I)
MERGE_PR_RE = re.compile(r'Merge pull request #(\d+)', re.I)
SQUASH_PR_RE = re.compile(r'\(#(\d+)\)', re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Commit analysis
# ─────────────────────────────────────────────────────────────────────────────

def find_defect_introducing_shas(repo: git.Repo) -> dict[str, str]:
    """
    Walk all commits and find SHA → label_source mappings for
    commits that were later identified as introducing defects.

    Returns: dict of {introducing_sha: label_source}
    """
    defective: dict[str, str] = {}

    for commit in repo.iter_commits():
        msg = commit.message.strip()

        # Check for explicit revert
        match = REVERT_SHA_RE.search(msg)
        if match:
            reverted_sha = match.group(1)
            defective[reverted_sha] = "revert"
            continue

        # Check for hotfix/bugfix — blame the parent commit's changed lines
        if HOTFIX_RE.search(msg):
            try:
                for parent in commit.parents[:1]:
                    for diff in commit.diff(parent):
                        if diff.a_blob:
                            # Simplified: mark parent commit as introducing
                            defective[parent.hexsha] = "hotfix"
            except Exception:
                pass

    return defective


def sha_to_pr_number(prs_df: pd.DataFrame, sha: str) -> int | None:
    """Map a commit SHA (full or abbreviated) to a PR number."""
    matches = prs_df[prs_df["commit_sha"].str.startswith(sha[:7])]
    if not matches.empty:
        return int(matches.iloc[0]["pr_number"])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main labelling pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_labeller(
    repo_path: str,
    prs_parquet: str,
    output_path: str,
) -> pd.DataFrame:
    """
    Run the SZZ-style labeller and write labels.parquet.

    Output columns:
        repo, pr_number, is_defective, label_source
    """
    prs_df = pd.read_parquet(prs_parquet)
    repo = git.Repo(repo_path)

    logger.info("Scanning commits in %s for defect signals...", repo_path)
    defective_shas = find_defect_introducing_shas(repo)
    logger.info("Found %d potential defect-introducing SHAs", len(defective_shas))

    # Resolve SHAs → PR numbers
    rows: list[dict] = []
    matched = 0
    for sha, source in defective_shas.items():
        pr_num = sha_to_pr_number(prs_df, sha)
        if pr_num is not None:
            rows.append({
                "repo": prs_df.iloc[0]["repo"] if not prs_df.empty else "unknown",
                "pr_number": pr_num,
                "is_defective": 1,
                "label_source": source,
            })
            matched += 1

    # All PRs not in the defective set get label 0
    defective_prs = {r["pr_number"] for r in rows}
    for _, row in prs_df.iterrows():
        if row["pr_number"] not in defective_prs:
            rows.append({
                "repo": row["repo"],
                "pr_number": int(row["pr_number"]),
                "is_defective": 0,
                "label_source": "clean",
            })

    labels_df = pd.DataFrame(rows).drop_duplicates(subset=["repo", "pr_number"])
    positive_rate = labels_df["is_defective"].mean()

    logger.info(
        "Labelling complete: %d PRs, %d positive (%.1f%%), matched %d SHAs",
        len(labels_df), labels_df["is_defective"].sum(), positive_rate * 100, matched,
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    labels_df.to_parquet(output, index=False)
    logger.info("Labels written to %s", output)
    return labels_df


# ─────────────────────────────────────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SZZ-style PR defect labeller")
    parser.add_argument("--repo-path", required=True, help="Local path to cloned git repo")
    parser.add_argument("--prs-parquet", required=True, help="Path to prs.parquet from miner")
    parser.add_argument("--output", required=True, help="Output path for labels.parquet")
    args = parser.parse_args()

    run_labeller(args.repo_path, args.prs_parquet, args.output)
