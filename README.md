# Vouch

> **Automated Code Review Intelligence & Empirical Risk-Aware PR Governance for GitHub**

[![Live App](https://img.shields.io/badge/Live%20Deployment-AWS%20Elastic%20Beanstalk-232F3E?style=flat-square&logo=amazon-aws&logoColor=white)](http://vouch.ap-south-1.elasticbeanstalk.com)
[![Version](https://img.shields.io/badge/Version-v2.6%20Intelligence%20Edition-0969da?style=flat-square&logo=github&logoColor=white)](https://github.com/ankit-cybertron/Vouch)
[![CI/CD](https://img.shields.io/badge/CI%2FCD-GitHub%20Actions-2088FF?style=flat-square&logo=github-actions&logoColor=white)](https://github.com/ankit-cybertron/Vouch/actions)
[![Tests](https://img.shields.io/badge/Tests-186%20passed-success?style=flat-square&logo=pytest&logoColor=white)](https://github.com/ankit-cybertron/Vouch)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![AWS Region](https://img.shields.io/badge/Region-ap--south--1-FF9900?style=flat-square&logo=amazon-aws&logoColor=white)](http://vouch.ap-south-1.elasticbeanstalk.com)
[![Demo Video](https://img.shields.io/badge/Demo%20Video-YouTube%20Walkthrough-FF0000?style=flat-square&logo=youtube&logoColor=white)](https://youtu.be/m5iM3ArUbz0)

---

### 🌐 Live Production Deployment & Demo Video

* **Live Cloud Application**: [http://vouch.ap-south-1.elasticbeanstalk.com](http://vouch.ap-south-1.elasticbeanstalk.com)  
  *Hosted on AWS Elastic Beanstalk (`ap-south-1` Mumbai) powered by production Gunicorn WSGI workers, automated CI/CD gating, dual-mode persistence, and Amazon DynamoDB.*
* **2-Minute Walkthrough Video**: [https://youtu.be/m5iM3ArUbz0](https://youtu.be/m5iM3ArUbz0)  
  *Fast-paced technical demonstration covering live PR triage, rubber-stamp interception, multi-model scoring, sub-500ms Groq narration, and pair review analytics.*

---

## 1. Executive Summary & Problem Statement

In modern continuous delivery environments, human code review is the single most critical quality and security safeguard. However, standard review workflows suffer from four systemic failure modes:

1. **Rubber-Stamp Approvals**: Under delivery pressure, reviewers frequently issue rapid approvals (e.g. bare "LGTM", "+1", emoji reactions, or approvals submitted within seconds) without scrutinizing complex diffs.
2. **Reviewer Fatigue & Inattention**: As engineers conduct multiple reviews in a single working session or review outside normal operating hours, cognitive fatigue causes sharp drops in comment depth and defect detection.
3. **Review Scrutiny Mismatch**: High-risk pull requests touching sensitive modules (authentication middleware, cryptographic primitives, SQL migrations, payment gateways, infrastructure-as-code) frequently receive the same superficial scrutiny as routine documentation or asset changes.
4. **Binary Branch Protection Flaws**: Standard GitHub branch protection rules only enforce *that* a PR has an approval (boolean status), completely blind to *how thoroughly* the code was inspected or *how critical* the touched files are.

**Vouch** solves this by replacing binary approval checks with an empirical, multi-model evaluation of **Residual Risk**. When a high-risk change receives a shallow or rushed review, Vouch flags the pull request for mandatory senior re-review, identifies suitable domain codeowners, and prevents unexamined defects from reaching production.

---

## 2. Core Mathematical Formulation

The foundational principle of Vouch is that code safety is a function of both intrinsic change risk and empirical review thoroughness:

$$\mathbf{R_{\text{residual}} = R_{\text{change}} \times (1.0 - C_{\text{review}})}$$

Where:
* **$R_{\text{change}} \in [0.04, 0.96]$**: Intrinsic defect introduction probability evaluated by Model 1 from diff churn, modified sensitive files, file breadth, and author risk history.
* **$C_{\text{review}} \in [0.04, 0.95]$**: Empirical review confidence composed from Model 2 (semantic comment depth), Model 3 (reviewer attention baseline & session pacing), review duration adequacy, and reviewer domain familiarity.
* **$R_{\text{residual}} \in [0.02, 0.96]$**: Unmitigated risk surviving the peer review process.

### Risk Tiers & Automated Governance

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                   Change Risk (R_change)                │
                    │   Low Churn / Safe Paths        High Churn / Auth / Crypto│
┌───────────────────┼────────────────────────────┬────────────────────────────┤
│ Deep Scrutiny     │ Low Residual Risk          │ Acceptable Residual Risk   │
│ Substantive Review│ (R_res < 0.35)             │ (0.35 ≤ R_res < 0.65)      │
│ High C_review     │ Status: APPROVED           │ Status: MONITORED          │
├───────────────────┼────────────────────────────┼────────────────────────────┤
│ Shallow Review    │ Acceptable Residual Risk   │ Critical Residual Risk     │
│ Rushed Rubberstamp│ (R_res < 0.35)             │ (R_res ≥ 0.65)             │
│ Low C_review      │ Status: MONITORED          │ Status: RE-QUEUE MANDATORY │
└───────────────────┴────────────────────────────┴────────────────────────────┘
```

* 🚨 **High Risk ($R_{\text{residual}} \ge 0.65$)**: Automatically sets `re_queued = True`. Rendered with red badges (`gh-label-high`, `needs-re-review`). Triggers CODEOWNERS re-routing and blocks merge eligibility.
* ⚠️ **Medium Risk ($0.35 \le R_{\text{residual}} < 0.65$)**: Rendered with amber badges (`gh-label-medium`). Monitored change with moderate residual risk.
* ✅ **Low Risk ($R_{\text{residual}} < 0.35$)**: Rendered with green badges (`gh-label-low`). Thorough review on moderate change or safe change with routine approval.

---

## 3. End-to-End System Architecture

```
                                 GitHub Repository
                          (Webhook / REST API Ingestion)
                                        │
                                        ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │                        Vouch Ingestion Layer                          │
    │  • 1-Click GitHub App Installation Access Tokens (IATs, 5000 req/hr)  │
    │  • GitHub OAuth 2.0 Web Flow & Personal Access Token (PAT) modal      │
    │  • Server-Side Opaque Session Isolation (Local JSON / DynamoDB)       │
    │  • HMAC-SHA256 Webhook Verification (ingest/handler.py)               │
    └───────────────────────────────────┬───────────────────────────────────┘
                                        │
                                        ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │                   Feature Extraction & Calibration                    │
    │  • Diff Churn: Lines added/deleted, hunks, entropy                    │
    │  • 10 Sensitive Path Categories: auth, crypto, db, infra, billing...  │
    │  • Review Telemetry: Duration, comments, timestamps, line references │
    │  • Authorship Indicators: Author tenure, bot/AI generation markers    │
    └───────────────────┬───────────────────────────────┬───────────────────┘
                        │                               │
                        ▼                               ▼
    ┌───────────────────────────────────────┐   ┌───────────────────────────────────────┐
    │     Model 1: Change Risk (XGBoost)    │   │  Models 2 & 3: Review Depth & Attention│
    │  • 28 Churn & Sensitivity Features    │   │  • Model 2: DistilBERT NLP Classifier │
    │  • Logarithmic Sizing Calibration     │   │    (6 tiers: security, arch, logic...) │
    │  • 7 Discrepancy Signal Penalties     │   │  • 33 Exact Rubber-Stamp Token Matched│
    │  • SHAP Relative Risk Attribution     │   │  • Model 3: Robust Z-Score (MAD)      │
    │                                       │   │  • Multivariate Isolation Forest      │
    │                                       │   │  • Pacing (1.5s/line) & Fatigue State │
    └───────────────────┬───────────────────┘   └───────────────────┬───────────────────┘
                        │                                           │
                        │ R_change                         C_review │
                        └─────────────────────┬─────────────────────┘
                                              │
                                              ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │                    Residual Risk Composition Engine                   │
    │              R_residual = R_change × (1.0 - C_review)                 │
    │                                                                       │
    │    Threshold Evaluation: R_res ≥ 0.65  ==>  Trigger Re-Queue          │
    └───────────────────────────────────┬───────────────────────────────────┘
                                        │
                                        ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │               Explainable AI & Resilient 3-Tier Cascade               │
    │     "Models score the numbers; Language Models narrate the action"     │
    │                                                                       │
    │  [Tier 1] AWS Bedrock (Claude 3 Haiku / Claude 3.5 Sonnet)            │
    │  [Tier 2] Groq Cloud Cascade (Llama 3.3 70B / Qwen 27B / Mixtral)    │
    │           Dual-Key Failover: GROQ_API_KEY  ==>  GROQ_API_KEY_backup   │
    │  [Tier 3] Deterministic Vouch Fallback Engine (Zero-Crash Guarantee)  │
    └───────────────────────────────────┬───────────────────────────────────┘
                                        │
                                        ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │                     Review Intelligence Delivery                      │
    │  • Live Pull Request Triage Board with Interactive KPI Filters        │
    │  • Forensic PR Audit View with Hover Popovers & SHAP Weight Visualizer │
    │  • Reviewer Workload Capacity & Cognitive Fatigue Leaderboards        │
    │  • Pair Collaboration Matrix & Echo-Chamber Silo Detection           │
    │  • Review SLA Breach Predictor & Proactive Turnaround Forecasts       │
    │  • 1-Click SOC2 / Compliance CSV & JSON Report Export Center          │
    └───────────────────────────────────────────────────────────────────────┘
```

---

## 4. Machine Learning & Statistical Ensemble

### Model 1: Change Risk Classifier (XGBoost + Heuristic Proxy)
* **Standalone Cloud Model (`models/risk/`)**: 28-feature gradient-boosted decision tree (`xgboost.XGBClassifier`) trained on historical PR corpora to predict defect-introduction probability.
* **Production Proxy (`dashboard/app.py: _score_pr_heuristic`)**:
  - **Logarithmic Sizing**: $S_{\text{size}} = \min\left(1.0, \frac{\ln(1 + \text{lines})}{\ln(1 + 800)}\right)$
  - **Path Sensitivity Score**: Matches 10 sensitive categories (`auth`, `security`, `crypto`, `database`, `migration`, `infra`, `billing`, `api`, `config`, `core`).
    $$S_{\text{path}} = \begin{cases} \min(0.55 + 0.10 \times \text{count}, 1.0) & \text{if sensitive count} > 0 \\ 0.04 & \text{otherwise} \end{cases}$$
  - **File Breadth**: $S_{\text{file}} = \min(\text{changed\_files} / 15.0, 1.0)$
  - **Base Risk**: $R_{\text{base}} = 0.38 \times S_{\text{path}} + 0.40 \times S_{\text{size}} + 0.22 \times S_{\text{file}}$
  - **7 Discrepancy Signal Penalties**:
    1. *No Reviewer Assigned*: $+0.12$
    2. *Rapid Merge (< 10 min)*: $+0.10$
    3. *Stale PR (> 30 days)*: $+0.07$
    4. *WIP / Draft Title Merged*: $+0.15$
    5. *Mass File Touch (> 15 files)*: $+0.08$
    6. *Minimal Description (< 30 chars for diff > 200 lines)*: $+0.06$
    7. *Author Self-Review*: $+0.14$
    8. *AI Authorship Signal*: $+0.05$

### Model 2: Review Depth Scorer (DistilBERT + NLP Rules)
* **Standalone Transformer (`models/depth/`)**: Fine-tuned `DistilBERT` sequence classifier categorizing review commentary across 6 qualitative tiers.
* **Production NLP Engine**:
  - **Exact Rubber-Stamp Matcher**: Scans against 33 exact rubber-stamp tokens (`lgtm`, `looks good to me`, `approved`, `ship it`, `+1`, `👍`, `✅`, `wfm`, `fine`, etc.) and comments $< 4$ characters.
  - **Semantic Regex Classification & Weights**:
    - `security` (xss, injection, auth, csrf, cors, vuln, crypto, cve) $\to$ **1.00**
    - `architecture` (solid, coupling, cohesion, interface, layer, refactor) $\to$ **0.90**
    - `logic_concern` (bug, broken, error, crash, deadlock, race, null, exception) $\to$ **0.80**
    - `test` (assert, coverage, mock, fixture, unit, e2e, pytest) $\to$ **0.55**
    - `clarifying` (standard substantive discussion) $\to$ **0.45**
    - `nit_style` (nit, style, format, indent, typo, naming, lint) $\to$ **0.15**
    - `rubber_stamp` (unsubstantive quick approvals) $\to$ **0.00**

### Model 3: Reviewer Attention & Fatigue Baseline (Robust Z-Score)
* **Statistical Anomaly Detection (`models/attention/`)**: Non-parametric evaluation using Median and Median Absolute Deviation (MAD):
  $$\text{MAD} = \text{median}(|x_i - \tilde{x}|), \quad \text{Robust } Z = \frac{x - \tilde{x}}{1.4826 \times \text{MAD}}$$
* **Multivariate Isolation Forest (`models/attention/model.py`)**: Evaluates joint vector `[seconds_per_kloc, comment_density, mean_depth_score, consecutive_reviews, elapsed_session_minutes, hour_of_day]`.
* **Session Pacing Evaluation**: Adequate review duration calibrated at $1.5$ seconds per line of code (minimum 180s). Rushed reviews receive up to $-0.35$ attention penalties; late-night reviews ($23:00 - 06:00$) receive $-0.15$; high rubber-stamp ratios receive $-0.20$.

### Review Confidence Composition
$$C_{\text{review}} = 0.40 \times D_{\text{score}} + 0.25 \times \text{time\_adequacy} + 0.25 \times \text{attention\_state} + 0.10 \times \text{reviewer\_familiarity}$$

---

## 5. Resilient AI / LLM Explanation Layer

> **Core Philosophy**: *"Machine learning models score the numbers; Language Models narrate the action."*

The LLM layer is strictly decoupled from metric computation. It ingests verified numeric scores and generates **exactly one concise, actionable English sentence** (under 40 words) providing direct triage advice to engineering leads.

### 3-Tier Resilient Fallback Cascade
1. **Tier 1 — Amazon Bedrock**: Primary enterprise LLM integration invoking Anthropic Claude 3 Haiku (`anthropic.claude-3-haiku-20240307-v1:0`) or Claude 3.5 Sonnet at `temperature: 0.0`.
2. **Tier 2 — Groq Cloud Dual-Key Cascade**: On-demand ultra-fast ($< 500\text{ms}$) inference. If `GROQ_API_KEY` hits rate limits, automatically falls over to `GROQ_API_KEY_backup`. Supports seamless model cascade (`qwen/qwen3.8-27b` $\to$ `llama-3.3-70b-versatile` $\to$ `llama-3.1-8b-instant`).
3. **Tier 3 — Deterministic Rule Engine**: If cloud APIs are unreachable, Vouch synthesizes an immediate deterministic explanation based on reviewer fatigue, sensitive files, and diff churn without failing.

---

## 6. Review Intelligence & Organizational Governance

Vouch is more than a pull request checker — it is an organizational intelligence suite:

| Feature Area | Route | Core Capability |
|:---|:---|:---|
| **Live PR Triage Board** | `/pulls`, `/board` | Real-time PR list with 4 interactive KPI filters (Total, High Risk, Medium Risk, Avg Risk), live text search, and missing PR auto-fetch. |
| **Forensic PR Detail** | `/pr/<owner>/<repo>/<num>` | Multi-model breakdown with interactive hover popovers, SHAP attribution weights, full conversation timeline, and Groq LLM explainer. |
| **Reviewer Profiles** | `/reviewer/<username>` | Cognitive fatigue scorecards, real-time workload capacity gauges, rubber-stamp rate tracking, and priority queues ranked by change risk. |
| **Pair Intelligence** | `/pair-intelligence` | Interactive co-review collaboration matrix, turnaround metrics, and detection of reciprocal rubber-stamping silos and knowledge echo-chambers. |
| **Load Balancing** | `/load-balancing` | Dynamic review queue rebalancing, preventing senior engineer burnout and redistributing backlog across qualified CODEOWNERS. |
| **Review SLA Predictor** | `/sla-predictions` | Forecasts expected hours to first review and merge, identifying impending SLA breaches before they stall releases. |
| **Team Health Scorecard** | `/team-health` | Organization-wide Rubber-Stamp Index, Fatigue Index, Module Safety Matrix, and 1-click compliance export (CSV / JSON). |

---

## 7. Dual-Mode Storage Architecture

Vouch features an abstract storage interface `StorageBackend` (`dashboard/store.py`) enabling zero-config local execution and enterprise AWS cloud deployment:

* **Local JSON Mode (`JsonStorageBackend`)**:
  - Default for local development and testing (`data/repos.json`, `data/prs.json`).
  - Thread-safe atomic writes using temporary files and atomic `os.replace()`, preventing corrupt reads under concurrent requests.
* **Amazon DynamoDB Mode (`DynamoDbStorageBackend`)**:
  - Activated via `USE_DYNAMODB=true`.
  - Tables: `vouch-repos`, `vouch-prs`, and `vouch-sessions`.
  - Sub-millisecond queries via Global Secondary Index (`repo-index`) partitioned by repository.
  - Automatic Python float $\leftrightarrow$ `boto3.dynamodb.types.Decimal` serialization.
  - Batch insertion using DynamoDB `batch_writer()`.

---

## 8. GitHub Primer Design System & UI/UX

Modeled directly on the **GitHub Primer Design System** (`dashboard/static/css/styles.css`):
* **Dual Theming**: Complete Light and Dark themes toggled via `#theme-toggle-btn` and persisted in `localStorage['vouch-theme']`.
* **Typography Hierarchy**: Google Fonts `Inter` for interface structure and `JetBrains Mono` for diffs, metrics, and SHAP attribution.
* **Information Density**: Compact 13px/14px layouts, Octicons, state pills, and responsive 2-column forensic inspection views.
* **Interactive Hover Popovers**: Rich mathematical formulas and model attribution popovers appearing on metric hover across desktop and tablet viewports.

---

## 9. Quickstart & Local Development

### Prerequisites
* Python 3.12+
* Git
* (Optional) GitHub Personal Access Token or GitHub App credentials

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/ankit-cybertron/Vouch.git
cd Vouch

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables (Optional)
Create a `.env` file in the project root:
```env
# GitHub Authentication (Optional: increases API rate limits)
GITHUB_TOKEN=ghp_your_personal_access_token

# Groq Cloud Narration (Optional: enables instant sub-500ms AI explanations)
GROQ_API_KEY=gsk_your_primary_key
GROQ_API_KEY_backup=gsk_your_backup_key

# AWS Cloud Integration (Optional: defaults to local JSON storage)
USE_DYNAMODB=false
AWS_REGION=ap-south-1

# Demo Video Configuration
YOUTUBE_DEMO_URL=https://youtu.be/m5iM3ArUbz0
```

### 3. Run Application Server

**Development Server:**
```bash
python main.py --port 5001
```

**Production WSGI Server (Gunicorn):**
```bash
gunicorn --bind 127.0.0.1:8000 application:application
```

Access local endpoints:
* **Landing Page**: [http://localhost:5001/](http://localhost:5001/)
* **Repositories Catalog**: [http://localhost:5001/repos](http://localhost:5001/repos)
* **PR Triage Board**: [http://localhost:5001/board](http://localhost:5001/board)
* **Reviewer Intelligence**: [http://localhost:5001/reviewer](http://localhost:5001/reviewer)
* **Pair Review Intelligence**: [http://localhost:5001/pair-intelligence](http://localhost:5001/pair-intelligence)
* **Team Health Scorecard**: [http://localhost:5001/team-health](http://localhost:5001/team-health)

---

## 10. Verification & Test Suite

Vouch maintains a comprehensive suite of **186 automated tests** spanning unit, integration, and route verification:

```bash
pytest
```

```text
============================= test session starts ==============================
collected 186 items

tests/integration/dashboard_routes.py ....................               [ 10%]
tests/integration/github_oauth_service.py ............                   [ 17%]
tests/integration/pr_risk_calibration.py ...                             [ 18%]
tests/integration/token_authentication.py ..........                     [ 24%]
tests/unit/change_risk_model.py .....                                    [ 26%]
tests/unit/codeowners_routing.py .............                           [ 33%]
tests/unit/diff_feature_extraction.py ........                           [ 38%]
tests/unit/heuristic_scoring_discrepancies.py ...............            [ 46%]
tests/unit/residual_risk_engine.py ...................                   [ 56%]
tests/unit/retrospective_metrics.py ..                                   [ 57%]
tests/unit/review_depth_scorer.py .....                                  [ 60%]
tests/unit/reviewer_attention_baseline.py ..............                 [ 67%]
tests/unit/szz_defect_labelling.py .......                               [ 71%]
tests/unit/test_groq_explanation.py ...........                          [ 77%]
tests/unit/test_load_balancing.py ....                                   [ 79%]
tests/unit/test_pair_intelligence.py ....                                [ 81%]
tests/unit/test_sla_predictions.py ....                                  [ 83%]
tests/unit/test_storage.py ....................                          [ 94%]
tests/unit/test_version.py .....                                         [ 97%]
tests/unit/webhook_event_normalization.py .....                          [100%]

============================= 186 passed in 40.59s =============================
```

---

## 11. Key Differentiators vs. Traditional Tooling

| Capability | Traditional Linters & SAST (SonarQube, CodeQL) | Standard Branch Protection (GitHub Native) | Vouch Review Intelligence |
|:---|:---|:---|:---|
| **Focus** | Static syntax, vulnerabilities, code style | Binary approval count (boolean `approved`) | **Human inspection rigor & empirical residual risk** |
| **Rubber-Stamp Detection** | ❌ None | ❌ None (treats 10-second approval as valid) | ✅ **Intercepts empty reviews, +1s, and rushed approvals** |
| **Cognitive Fatigue Tracking** | ❌ None | ❌ None | ✅ **Tracks review pacing, session volume, and off-hours load** |
| **Critical Module Awareness** | ⚠️ Generic file rules | ⚠️ Static CODEOWNERS without risk context | ✅ **Evaluates joint probability of diff risk $\times$ scrutiny** |
| **Collaboration Dynamics** | ❌ None | ❌ None | ✅ **Pair review collaboration matrix & echo-chamber alerts** |
| **Explainability** | ⚠️ Cryptic linter rules | ❌ None | ✅ **Sub-500ms LLM narration with concrete lead recommendations** |
| **Resilience** | N/A | High | ✅ **3-Tier cascade: Bedrock $\to$ Groq Dual-Key $\to$ Deterministic** |

---

## 12. Project Structure

```text
Vouch/
├── .github/workflows/          # Automated CI/CD (Tests + Elastic Beanstalk Deploy)
├── corpus/                     # GitHub GraphQL corpus mining & SZZ defect labelling
├── dashboard/                  # Production Flask application & Primer UI
│   ├── static/css/styles.css   # Unified GitHub Primer design tokens & styles
│   ├── static/js/              # Smart autocomplete, theme toggle, and API pollers
│   ├── templates/              # Jinja2 templates (landing, board, pr_detail, reviewer...)
│   ├── app.py                  # Core route controllers & heuristic 3-model scoring proxy
│   ├── auth.py                 # OAuth 2.0 & PAT authentication manager
│   ├── github_app.py           # GitHub App RS256 JWT & Installation Access Token engine
│   ├── session_store.py        # Opaque server-side credential isolation
│   ├── store.py                # Dual-mode storage (Local Atomic JSON & DynamoDB)
│   └── version.py              # Semantic versioning (v2.6) & git hash resolution
├── data/                       # Local JSON persistence store (repos.json, prs.json)
├── docs/                       # Architectural dossiers & deployment manuals
├── eval/                       # Retrospective validation engine & Precision@K metrics
├── explain/                    # AI explanation layer (Bedrock Claude 3 & Groq cascade)
├── features/                   # Diff churn, AST heuristics, and sensitive path matchers
├── infra/                      # AWS SAM templates & Step Functions ASL specifications
├── ingest/                     # Webhook signature verification & event normalization
├── models/                     # Standalone ML training & SageMaker inference handlers
│   ├── risk/                   # Model 1: XGBoost defect introduction classifier
│   ├── depth/                  # Model 2: DistilBERT semantic review depth classifier
│   └── attention/              # Model 3: Robust Z-Score & Isolation Forest fatigue baseline
├── scoring/                    # Residual risk composition & CODEOWNERS SNS routing
└── tests/                      # 186 unit and integration test suites
```

---

## 13. Team & Acknowledgments

**Quantified Minds**
* **Ankit Kumar Tiwari**
* **Ishaan Chaturvedi**

*Developed for WeMakeDevs "First Commit" — Bharat Builds Tour · Event 01*  
*Special thanks to the open-source engineering community for foundational research in empirical defect prediction and review ergonomics.*
