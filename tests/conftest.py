"""
Vouch Test Suite — Shared fixtures and test configuration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on sys.path
import shutil
import tempfile

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Isolate storage for tests to avoid writing to workspace data/prs.json
_TEST_TMP_DIR = tempfile.TemporaryDirectory()
_TEST_DATA_PATH = Path(_TEST_TMP_DIR.name)
_SRC_DATA_DIR = REPO_ROOT / "data"

if (_SRC_DATA_DIR / "repos.json").exists():
    shutil.copy(_SRC_DATA_DIR / "repos.json", _TEST_DATA_PATH / "repos.json")
else:
    (_TEST_DATA_PATH / "repos.json").write_text("{}", encoding="utf-8")
(_TEST_DATA_PATH / "prs.json").write_text("[]", encoding="utf-8")

os.environ["VOUCH_DATA_DIR"] = str(_TEST_DATA_PATH)
os.environ["VOUCH_REPOS_JSON"] = str(_TEST_DATA_PATH / "repos.json")
os.environ["VOUCH_PRS_JSON"] = str(_TEST_DATA_PATH / "prs.json")

# Set test environment flags before importing app
os.environ["PRS_TABLE"] = "test-prs"
os.environ["REVIEWS_TABLE"] = "test-reviews"
os.environ["BASELINES_TABLE"] = "test-baselines"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"


@pytest.fixture
def app():
    """Return configured Flask test application."""
    from dashboard.app import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret-key-12345"
    return flask_app


@pytest.fixture(autouse=True)
def ensure_default_catalog():
    """Ensure catalog and default repository caches are loaded without network requests."""
    from dashboard.app import _init_default_repos, FETCHED_REPOS
    if not FETCHED_REPOS:
        _init_default_repos()



@pytest.fixture
def client(app):
    """Return Flask test client."""
    return app.test_client()


@pytest.fixture
def mock_github_user():
    """Standard mocked GitHub user response."""
    return {
        "login": "test-dev",
        "id": 999999,
        "avatar_url": "https://avatars.githubusercontent.com/u/999999?v=4",
        "name": "Test Developer",
        "email": "test-dev@example.com",
    }


@pytest.fixture
def mock_github_rate_limit():
    """Standard mocked GitHub rate limit response."""
    return {
        "rate": {
            "limit": 5000,
            "remaining": 4950,
            "reset": 1700000000,
            "used": 50,
            "resource": "core",
        }
    }


@pytest.fixture
def sample_diff():
    """Sample realistic git diff text."""
    return """diff --git a/auth/jwt.py b/auth/jwt.py
index 83a21b3..d94e1a0 100644
--- a/auth/jwt.py
+++ b/auth/jwt.py
@@ -10,6 +10,8 @@ def verify_token(token: str) -> dict:
     if not token:
-        raise ValueError("missing token")
+        raise AuthenticationError("missing token")
+    decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
+    return decoded
diff --git a/config/settings.yaml b/config/settings.yaml
index 10a44f2..5e3a89c 100644
--- a/config/settings.yaml
+++ b/config/settings.yaml
@@ -1,4 +1,5 @@
 app:
   name: Vouch
+  session_ttl: 3600
"""


@pytest.fixture
def sample_codeowners():
    """Sample CODEOWNERS file content."""
    return """# Global owners
*       @global-lead @core-team

# Auth and security
auth/** @security-guru @lead-dev

# Database & migrations
db/schema.sql @database-architect
"""
