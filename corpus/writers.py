"""
Vouch — Parquet writers for corpus data.

Handles schema validation, type coercion, and writing to both
local filesystem and S3.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Arrow schemas — enforce types at write time
# ─────────────────────────────────────────────────────────────────────────────

PR_SCHEMA = pa.schema([
    pa.field("repo", pa.string()),
    pa.field("pr_number", pa.int64()),
    pa.field("title", pa.string()),
    pa.field("state", pa.string()),
    pa.field("created_at", pa.string()),
    pa.field("merged_at", pa.string()),
    pa.field("closed_at", pa.string()),
    pa.field("additions", pa.int64()),
    pa.field("deletions", pa.int64()),
    pa.field("changed_files", pa.int64()),
    pa.field("author", pa.string()),
    pa.field("base_branch", pa.string()),
    pa.field("head_branch", pa.string()),
    pa.field("commit_message", pa.string()),
    pa.field("commit_sha", pa.string()),
])

REVIEW_SCHEMA = pa.schema([
    pa.field("repo", pa.string()),
    pa.field("pr_number", pa.int64()),
    pa.field("review_id", pa.string()),
    pa.field("state", pa.string()),
    pa.field("submitted_at", pa.string()),
    pa.field("reviewer", pa.string()),
    pa.field("body", pa.string()),
])

COMMENT_SCHEMA = pa.schema([
    pa.field("repo", pa.string()),
    pa.field("pr_number", pa.int64()),
    pa.field("review_id", pa.string()),
    pa.field("comment_id", pa.string()),
    pa.field("body", pa.string()),
    pa.field("path", pa.string()),
    pa.field("line", pa.int64()),
    pa.field("created_at", pa.string()),
    pa.field("commenter", pa.string()),
])

LABEL_SCHEMA = pa.schema([
    pa.field("repo", pa.string()),
    pa.field("pr_number", pa.int64()),
    pa.field("is_defective", pa.int8()),
    pa.field("label_source", pa.string()),
])


# ─────────────────────────────────────────────────────────────────────────────
# Local writers
# ─────────────────────────────────────────────────────────────────────────────

def write_parquet(df: pd.DataFrame, path: str | Path, schema: pa.Schema) -> None:
    """Write a DataFrame to parquet with schema enforcement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, schema=schema, safe=False)
    pq.write_table(table, path, compression="snappy")
    logger.info("Written %d rows to %s", len(df), path)


def write_prs(df: pd.DataFrame, path: str | Path) -> None:
    write_parquet(df, path, PR_SCHEMA)


def write_reviews(df: pd.DataFrame, path: str | Path) -> None:
    write_parquet(df, path, REVIEW_SCHEMA)


def write_comments(df: pd.DataFrame, path: str | Path) -> None:
    write_parquet(df, path, COMMENT_SCHEMA)


def write_labels(df: pd.DataFrame, path: str | Path) -> None:
    write_parquet(df, path, LABEL_SCHEMA)


# ─────────────────────────────────────────────────────────────────────────────
# S3 upload helpers
# ─────────────────────────────────────────────────────────────────────────────

def upload_to_s3(local_path: str | Path, bucket: str, s3_key: str) -> None:
    """Upload a local parquet file to S3."""
    s3 = boto3.client("s3")
    local_path = Path(local_path)
    s3.upload_file(
        str(local_path),
        bucket,
        s3_key,
        ExtraArgs={"ServerSideEncryption": "AES256"},
    )
    logger.info("Uploaded %s → s3://%s/%s", local_path, bucket, s3_key)


def write_and_upload(
    df: pd.DataFrame,
    local_path: str | Path,
    schema: pa.Schema,
    bucket: Optional[str] = None,
    s3_key: Optional[str] = None,
) -> None:
    """Write parquet locally, optionally upload to S3."""
    write_parquet(df, local_path, schema)
    if bucket and s3_key:
        upload_to_s3(local_path, bucket, s3_key)
