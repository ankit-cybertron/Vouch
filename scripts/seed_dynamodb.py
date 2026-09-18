#!/usr/bin/env python3
"""
Vouch — DynamoDB Batch Seeding Utility.

Uploads repository metadata and scored pull requests from local JSON data
(`data/repos.json`, `data/prs.json`) directly into Amazon DynamoDB tables.

Usage:
    python scripts/seed_dynamodb.py
    python scripts/seed_dynamodb.py --region us-east-1 --prs-table vouch-prs --repos-table vouch-repos
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_dynamodb")


def _to_dynamodb_item(obj: Any) -> Any:
    """Recursively convert float values to Decimal for DynamoDB compliance."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamodb_item(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dynamodb_item(v) for v in obj]
    return obj


def seed_repositories(table, repos_data: dict[str, dict]) -> int:
    """Batch write repository metadata to DynamoDB table."""
    count = 0
    with table.batch_writer() as batch:
        for full_name, repo_meta in repos_data.items():
            item = _to_dynamodb_item(repo_meta)
            if "full_name" not in item:
                item["full_name"] = full_name
            batch.put_item(Item=item)
            count += 1
            logger.info("Queued repo: %s", full_name)
    return count


def seed_pull_requests(table, prs_data: list[dict]) -> int:
    """Batch write scored PRs to DynamoDB table."""
    count = 0
    with table.batch_writer() as batch:
        for pr in prs_data:
            item = _to_dynamodb_item(pr)
            if "pr_key" not in item:
                item["pr_key"] = f"{pr.get('repo')}#{pr.get('pr_number')}"
            batch.put_item(Item=item)
            count += 1
            logger.info("Queued PR: %s (%s)", item.get("pr_key"), item.get("title", "")[:40])
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed DynamoDB tables from local JSON files.")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-south-1"), help="AWS region")
    parser.add_argument("--prs-table", default=os.environ.get("PRS_TABLE", "vouch-prs"), help="DynamoDB PRs table name")
    parser.add_argument("--repos-table", default=os.environ.get("REPOS_TABLE", "vouch-repos"), help="DynamoDB Repos table name")
    parser.add_argument("--data-dir", default=str(Path(__file__).parent.parent / "data"), help="Path to data directory")

    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    repos_file = data_dir / "repos.json"
    prs_file = data_dir / "prs.json"

    if not repos_file.exists() or not prs_file.exists():
        logger.error("Required data files missing in %s", data_dir)
        return 1

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    repos_table = dynamodb.Table(args.repos_table)
    prs_table = dynamodb.Table(args.prs_table)

    logger.info("Seeding repositories into '%s' in %s...", args.repos_table, args.region)
    with open(repos_file, "r", encoding="utf-8") as f:
        repos_data = json.load(f)
    num_repos = seed_repositories(repos_table, repos_data)
    logger.info("Successfully seeded %d repositories.", num_repos)

    logger.info("Seeding pull requests into '%s' in %s...", args.prs_table, args.region)
    with open(prs_file, "r", encoding="utf-8") as f:
        prs_data = json.load(f)
    num_prs = seed_pull_requests(prs_table, prs_data)
    logger.info("Successfully seeded %d pull requests.", num_prs)

    logger.info("DynamoDB seeding complete! Total: %d repos, %d PRs.", num_repos, num_prs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
