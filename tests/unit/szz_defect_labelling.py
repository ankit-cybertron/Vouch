"""
Tests for corpus/labeller.py — SZZ defect labeller and git regex matching.
"""

import pandas as pd
import pytest

from corpus.labeller import (
    HOTFIX_RE,
    MERGE_PR_RE,
    REVERT_SHA_RE,
    SQUASH_PR_RE,
    sha_to_pr_number,
)


class TestCorpusLabellerRegexes:
    def test_revert_sha_regex_matching(self):
        msg = "Revert 'Fix memory leak in buffer pool'\n\nThis reverts commit 7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b."
        match = REVERT_SHA_RE.search(msg)
        assert match is not None
        assert match.group(1) == "7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b"

    def test_short_sha_revert(self):
        msg = "This reverts commit 1a2b3c4."
        match = REVERT_SHA_RE.search(msg)
        assert match is not None
        assert match.group(1) == "1a2b3c4"

    def test_hotfix_regex_matching(self):
        assert HOTFIX_RE.search("hotfix: critical security patch") is not None
        assert HOTFIX_RE.search("Bugfix for null pointer exception") is not None
        assert HOTFIX_RE.search("fix: resolve off-by-one error") is not None
        assert HOTFIX_RE.search("fix! breaking change to parser") is not None
        assert HOTFIX_RE.search("Refactor test cases") is None

    def test_pr_merge_commit_extraction(self):
        merge_msg = "Merge pull request #10492 from developer/feature"
        match = MERGE_PR_RE.search(merge_msg)
        assert match is not None
        assert int(match.group(1)) == 10492

    def test_squash_pr_extraction(self):
        squash_msg = "feat: add multi-threading to indexer (#3421)"
        match = SQUASH_PR_RE.search(squash_msg)
        assert match is not None
        assert int(match.group(1)) == 3421


class TestShaToPrMapping:
    def test_sha_to_pr_number_match(self):
        prs_df = pd.DataFrame({
            "commit_sha": ["7a8b9c0d1e2f", "123456789abc", "fedcba987654"],
            "pr_number": [101, 102, 103],
        })
        pr_num = sha_to_pr_number(prs_df, "7a8b9c011111")
        assert pr_num == 101

    def test_sha_to_pr_number_not_found(self):
        prs_df = pd.DataFrame({
            "commit_sha": ["7a8b9c0d1e2f"],
            "pr_number": [101],
        })
        pr_num = sha_to_pr_number(prs_df, "000000000000")
        assert pr_num is None
