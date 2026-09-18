# Vouch — Dashboard & REST API Reference

The Vouch dashboard is a lightweight, responsive Flask web application designed with GitHub-native aesthetics to provide real-time visibility into pull request risk and team review health.

---

## 1. Application Architecture

- **Entrypoint**: `main.py` / `dashboard/app.py`
- **Seed Data & Fallback Catalogs**: `dashboard/seeds.py`
- **Templates**: `dashboard/templates/` (`base.html`, `landing.html`, `repos.html`, `board.html`, `team_health.html`, `pr_detail.html`, `404.html`)
- **Static Assets**: `dashboard/static/css/styles.css`

---

## 2. Web Routes

### `GET /` (Landing Page)
- **Unauthenticated**: Renders a clean landing page with a single primary action: **Connect with GitHub** (OAuth) or an inline GitHub Token input fallback. Top navigation bar is hidden.
- **Authenticated**: Automatically redirects to `/repos`.

### `GET /repos` (Repository Catalog)
- Displays all monitored repositories with live statistics (active PR count, closed PR count, average residual risk).
- Includes the **"+" Add Repository** modal to fetch and score any public or private GitHub repository on demand.

### `GET /pulls?repo={owner}/{repo}` (Residual Risk Board)
- The core governance board.
- Displays PRs categorized into risk tiers:
  - **High Risk** ($R_{res} > 0.65$): Highlighted with red badge; auto-requeued.
  - **Medium Risk** ($0.40 \le R_{res} \le 0.65$): Yellow badge; flagged for attention.
  - **Low Risk** ($R_{res} < 0.40$): Green badge; cleared for deployment.
- Allows filtering by state (`open`, `closed`, `re_queued`) and searching by title, author, or PR number.

### `GET /team-health` (Team Review Health)
- Replaces legacy retrospective validation with operational review health analytics.
- Key metrics:
  - **Reviewer Fatigue Index**: Percentage of reviews conducted beyond safe consecutive review thresholds ($>3$ consecutive reviews).
  - **Rubber-Stamp Index**: Percentage of approvals issued without substantive inline comments or questions.
  - **Review Pacing Distribution**: Categorizes reviews into Rushed ($<2$ min), Brief ($2\text{--}10$ min), Standard ($10\text{--}60$ min), and Thorough ($>60$ min).
  - **Review Load Distribution**: Bar chart showing review workload per engineer.
  - **Recent Review Timeline**: Audit log of reviews flagged for fatigue or low depth.

### `GET /pr/<owner>/<repo>/<number>` (PR Detail View)
- In-depth forensic breakdown of a single pull request:
  - Header with PR title, status, author, reviewer, diff size, and time spent.
  - **Residual Risk Breakdown**: Visual dials for Change Risk, Review Confidence, and Residual Risk.
  - **Contributing Feature Weights**: Top 3 features driving the change risk score.
  - **Inline Comments Classification**: Each comment classified by Model 2 with assigned depth weight.
  - **Amazon Bedrock AI Explanation**: Generated rationale sentence, with a clean `(disconnected)` badge and deterministic fallback when offline.

---

## 3. REST API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/prs` | Returns JSON array of scored PRs (supports `?repo=`, `?state=`, `?q=`). |
| `GET` | `/api/repos` | Returns JSON list of all currently tracked repositories. |
| `POST` | `/api/fetch-repo` | Triggers live fetch and multi-model scoring of a GitHub repository (`{"owner": "...", "repo": "..."}`). |
| `GET` | `/api/auth/status` | Returns `{ "authenticated": true/false, "user": "..." }`. |
| `POST` | `/api/auth/token` | Sets a GitHub token into the current session (`{"token": "ghp_..."}`). |
| `POST` | `/api/auth/token/clear` | Clears the active authentication session. |

---

## 4. Authentication Architecture

Vouch supports two authentication flows:
1. **GitHub OAuth 2.0**:
   - Initiated via `/auth/github`
   - Redirects to `https://github.com/login/oauth/authorize?client_id=...`
   - Callback handled at `/auth/github/callback` with CSRF state verification.
2. **Personal Access Token Fallback**:
   - Developers or evaluators can input a fine-grained or classic GitHub Personal Access Token (`read:user`, `repo`).
   - Token is stored securely in the Flask server-side session and used for GitHub API rate-limit elevation.
