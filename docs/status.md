# Vouch — Comprehensive System Status & Technical Architecture Dossier

**Status As Of:** September 19, 2026  
**Repository:** `ankit-cybertron/Vouch`  
**Active Branch:** `feat/auth` (main live deployment tracking `main`)  
**Production Live URL:** `http://vouch.ap-south-1.elasticbeanstalk.com`  
**AWS Region:** `ap-south-1` (Mumbai)  
**Test Suite Status:** 125/125 Automated Tests Passing (100% Green)

---

## 1. Executive Summary & Accomplishments

Vouch is an automated Pull Request (PR) review intelligence and risk-aware governance platform. It solves the critical engineering problem of **rubber-stamp approvals**—where high-risk pull requests slip into production because human reviewers are fatigued, rushed, or inattentive.

Instead of predicting code quality with superficial linters, Vouch formulates pull request risk as a joint probability between **intrinsic code change risk** and **human review thoroughness**:

$$\text{Residual Risk } (R_{res}) = R_{change} \times (1.0 - C_{review})$$

### What Has Been Built & Operationalized:
1. **Multi-Model ML Ensemble**:
   - **Model 1 (Change Risk Classifier)**: 28-feature XGBoost model trained on historical commit churn, sensitive paths, author familiarity, and AI generation signals.
   - **Model 2 (Review Depth Scorer)**: Fine-tuned DistilBERT NLP classifier categorizing inline comments into 6 qualitative tiers (Security, Architecture, Correctness, Clarification, Nitpick, Rubber-Stamp).
   - **Model 3 (Reviewer Attention Baseline)**: Non-parametric statistical model utilizing Median and Median Absolute Deviation (MAD) with Isolation Forest anomaly detection for reviewer pacing and fatigue.
2. **Deterministic Heuristic Engine**: Seven production discrepancy rules that catch edge cases (rapid merges, missing reviewers, self-approvals, WIP merges, mass touches, pending changes requested, inattentive rush).
3. **Dual-Mode Persistence Adapter (`dashboard/store.py`)**:
   - Zero-dependency atomic local JSON storage (`data/repos.json`, `data/prs.json`) for local development.
   - Cloud DynamoDB storage (`vouch-repos`, `vouch-prs` with `repo-index` GSI) in `ap-south-1` with `batch_writer()` support.
4. **GitHub-Native Web Dashboard (`dashboard/`)**:
   - Responsive UI designed with GitHub Primer dark/light theme tokens.
   - Live repository catalog, residual risk governance board, in-depth PR forensic view, and team review health analytics.
   - Thread-pool accelerated live GitHub API ingestion with zero hardcoded PR data.
5. **AWS Cloud & Deployment Infrastructure**:
   - **Elastic Beanstalk**: Multi-stage deployment pipeline with isolated web dependencies (`requirements-web.txt`) avoiding heavy ML wheel timeouts.
   - **SageMaker Deployment Ready**: Model artifact packager and endpoint deployment script (`scripts/deploy_sagemaker.py`).
   - **Serverless Event-Driven Pipeline**: CloudFormation / SAM specification (`infra/template.yaml`) and Step Functions orchestration (`infra/state_machine.asl.json`).
   - **Amazon Bedrock**: LLM justification engine generating one-sentence risk rationales using Claude 3 Haiku with offline deterministic fallback.
6. **Authentication Architecture**:
   - GitHub OAuth 2.0 and session token fallback currently implemented.
   - Branch `feat/auth` and GitHub Issue #4 opened for transitioning to GitHub App Installation Access Tokens.

---

## 2. Complete Repository Tree

```
.
├── Procfile
├── README.md
├── __pycache__
│   ├── main.cpython-310.pyc
│   └── main.cpython-314.pyc
├── application.py
├── corpus
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   ├── __init__.cpython-314.pyc
│   │   ├── labeller.cpython-314-pytest-9.1.1.pyc
│   │   └── labeller.cpython-314.pyc
│   ├── labeller.py
│   ├── miner.py
│   └── writers.py
├── dashboard
│   ├── __pycache__
│   │   ├── app.cpython-310-pytest-8.3.2.pyc
│   │   ├── app.cpython-310.pyc
│   │   ├── app.cpython-314-pytest-9.1.1.pyc
│   │   ├── app.cpython-314.pyc
│   │   ├── store.cpython-310-pytest-8.3.2.pyc
│   │   ├── store.cpython-310.pyc
│   │   ├── store.cpython-314-pytest-9.1.1.pyc
│   │   └── store.cpython-314.pyc
│   ├── app.py
│   ├── static
│   │   ├── css
│   │   │   └── styles.css
│   │   └── js
│   │       └── main.js
│   ├── store.py
│   └── templates
│       ├── 404.html
│       ├── base.html
│       ├── board.html
│       ├── landing.html
│       ├── pr_detail.html
│       ├── repos.html
│       └── team_health.html
├── data
│   ├── prs.json
│   └── repos.json
├── docs
│   ├── ARCHITECTURE.md
│   ├── AWS_DEPLOYMENT.md
│   ├── DASHBOARD_AND_API.md
│   ├── MODELS.md
│   ├── OVERVIEW.md
│   ├── design.md
│   ├── implementation_plan.md
│   ├── plan.md
│   ├── prd.md
│   ├── status.md
│   └── trd.md
├── eval
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   ├── __init__.cpython-314.pyc
│   │   ├── metrics.cpython-314-pytest-9.1.1.pyc
│   │   └── metrics.cpython-314.pyc
│   ├── metrics.py
│   └── retrospective.py
├── explain
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-314.pyc
│   │   ├── bedrock_client.cpython-314.pyc
│   │   └── prompts.cpython-314.pyc
│   ├── bedrock_client.py
│   └── prompts.py
├── features
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-310-pytest-8.3.2.pyc
│   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   ├── __init__.cpython-314.pyc
│   │   ├── extractor.cpython-310-pytest-8.3.2.pyc
│   │   ├── extractor.cpython-314-pytest-9.1.1.pyc
│   │   └── extractor.cpython-314.pyc
│   ├── extractor.py
│   └── precompute.py
├── infra
│   ├── state_machine.asl.json
│   └── template.yaml
├── ingest
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-310-pytest-8.3.2.pyc
│   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   ├── __init__.cpython-314.pyc
│   │   ├── events.cpython-310-pytest-8.3.2.pyc
│   │   ├── events.cpython-314-pytest-9.1.1.pyc
│   │   └── events.cpython-314.pyc
│   ├── events.py
│   └── handler.py
├── main.py
├── models
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-310-pytest-8.3.2.pyc
│   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   └── __init__.cpython-314.pyc
│   ├── attention
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   │   ├── __init__.cpython-310-pytest-8.3.2.pyc
│   │   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   │   ├── __init__.cpython-314.pyc
│   │   │   ├── baseline.cpython-310-pytest-8.3.2.pyc
│   │   │   ├── baseline.cpython-314-pytest-9.1.1.pyc
│   │   │   └── baseline.cpython-314.pyc
│   │   ├── baseline.py
│   │   └── model.py
│   ├── depth
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   │   ├── __init__.cpython-310-pytest-8.3.2.pyc
│   │   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   │   ├── __init__.cpython-314.pyc
│   │   │   ├── inference.cpython-310-pytest-8.3.2.pyc
│   │   │   ├── inference.cpython-314-pytest-9.1.1.pyc
│   │   │   └── inference.cpython-314.pyc
│   │   ├── inference.py
│   │   └── train.py
│   └── risk
│       ├── __init__.py
│       ├── __pycache__
│       │   ├── __init__.cpython-310-pytest-8.3.2.pyc
│       │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│       │   └── __init__.cpython-314.pyc
│       ├── inference.py
│       └── train.py
├── pytest.ini
├── requirements-ml.txt
├── requirements-web.txt
├── requirements.txt
├── scoring
│   ├── __init__.py
│   ├── __pycache__
│   │   ├── __init__.cpython-310-pytest-8.3.2.pyc
│   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
│   │   ├── __init__.cpython-314.pyc
│   │   ├── residual.cpython-310-pytest-8.3.2.pyc
│   │   ├── residual.cpython-314-pytest-9.1.1.pyc
│   │   ├── residual.cpython-314.pyc
│   │   ├── routing.cpython-310-pytest-8.3.2.pyc
│   │   ├── routing.cpython-314-pytest-9.1.1.pyc
│   │   └── routing.cpython-314.pyc
│   ├── residual.py
│   └── routing.py
├── scripts
│   ├── deploy_sagemaker.py
│   └── seed_dynamodb.py
├── temp
│   └── test_claude.py
└── tests
    ├── __init__.py
    ├── __pycache__
    │   ├── __init__.cpython-310-pytest-8.3.2.pyc
    │   ├── __init__.cpython-314-pytest-9.1.1.pyc
    │   ├── __init__.cpython-314.pyc
    │   ├── conftest.cpython-310-pytest-8.3.2.pyc
    │   ├── conftest.cpython-314-pytest-9.1.1.pyc
    │   └── test_claude.cpython-314-pytest-9.1.1.pyc
    ├── conftest.py
    ├── integration
    │   ├── __init__.py
    │   ├── __pycache__
    │   │   ├── __init__.cpython-310-pytest-8.3.2.pyc
    │   │   ├── __init__.cpython-314-pytest-9.1.1.pyc
    │   │   ├── __init__.cpython-314.pyc
    │   │   ├── dashboard_routes.cpython-310-pytest-8.3.2.pyc
    │   │   ├── dashboard_routes.cpython-314-pytest-9.1.1.pyc
    │   │   ├── github_oauth_service.cpython-310-pytest-8.3.2.pyc
    │   │   ├── github_oauth_service.cpython-314-pytest-9.1.1.pyc
    │   │   ├── pr_risk_calibration.cpython-310-pytest-8.3.2.pyc
    │   │   ├── pr_risk_calibration.cpython-314-pytest-9.1.1.pyc
    │   │   ├── token_authentication.cpython-310-pytest-8.3.2.pyc
    │   │   └── token_authentication.cpython-314-pytest-9.1.1.pyc
    │   ├── dashboard_routes.py
    │   ├── github_oauth_service.py
    │   ├── pr_risk_calibration.py
    │   └── token_authentication.py
    └── unit
        ├── __init__.py
        ├── __pycache__
        │   ├── __init__.cpython-310-pytest-8.3.2.pyc
        │   ├── __init__.cpython-314-pytest-9.1.1.pyc
        │   ├── __init__.cpython-314.pyc
        │   ├── change_risk_model.cpython-310-pytest-8.3.2.pyc
        │   ├── change_risk_model.cpython-314-pytest-9.1.1.pyc
        │   ├── codeowners_routing.cpython-310-pytest-8.3.2.pyc
        │   ├── codeowners_routing.cpython-314-pytest-9.1.1.pyc
        │   ├── diff_feature_extraction.cpython-310-pytest-8.3.2.pyc
        │   ├── diff_feature_extraction.cpython-314-pytest-9.1.1.pyc
        │   ├── heuristic_scoring_discrepancies.cpython-310-pytest-8.3.2.pyc
        │   ├── heuristic_scoring_discrepancies.cpython-314-pytest-9.1.1.pyc
        │   ├── residual_risk_engine.cpython-310-pytest-8.3.2.pyc
        │   ├── residual_risk_engine.cpython-314-pytest-9.1.1.pyc
        │   ├── retrospective_metrics.cpython-310-pytest-8.3.2.pyc
        │   ├── retrospective_metrics.cpython-314-pytest-9.1.1.pyc
        │   ├── review_depth_scorer.cpython-310-pytest-8.3.2.pyc
        │   ├── review_depth_scorer.cpython-314-pytest-9.1.1.pyc
        │   ├── reviewer_attention_baseline.cpython-310-pytest-8.3.2.pyc
        │   ├── reviewer_attention_baseline.cpython-314-pytest-9.1.1.pyc
        │   ├── szz_defect_labelling.cpython-310-pytest-8.3.2.pyc
        │   ├── szz_defect_labelling.cpython-314-pytest-9.1.1.pyc
        │   ├── test_storage.cpython-310-pytest-8.3.2.pyc
        │   ├── test_storage.cpython-314-pytest-9.1.1.pyc
        │   ├── webhook_event_normalization.cpython-310-pytest-8.3.2.pyc
        │   └── webhook_event_normalization.cpython-314-pytest-9.1.1.pyc
        ├── change_risk_model.py
        ├── codeowners_routing.py
        ├── diff_feature_extraction.py
        ├── heuristic_scoring_discrepancies.py
        ├── residual_risk_engine.py
        ├── retrospective_metrics.py
        ├── review_depth_scorer.py
        ├── reviewer_attention_baseline.py
        ├── szz_defect_labelling.py
        ├── test_storage.py
        └── webhook_event_normalization.py
```

---

## 3. Mathematical Foundations & Residual Risk Formulation

The heart of Vouch is the **Residual Risk Equation**, which formalizes the intuitive understanding that even the riskiest code change can be deployed safely if reviewed with high scrutiny, whereas a moderate change reviewed with zero scrutiny carries high probability of failure:

$$R_{res} = R_{change} \times (1.0 - C_{review})$$

Where:
- $R_{change} \in [0.05, 0.95]$: Prior defect probability of the pull request changes.
- $C_{review} \in [0.00, 1.00]$: Composite review confidence factor.
- $R_{res} \in [0.00, 1.00]$: Unmitigated residual defect risk remaining at merge time.

### Review Confidence Formulation
Review confidence aggregates three dimensions:

$$C_{review} = 0.45 \cdot D_{score} + 0.35 \cdot A_{state} + 0.20 \cdot F_{reviewer}$$

1. **$D_{score}$ (Review Depth Score)**: Qualitative value of inline comments, questions, and revision cycles (Model 2).
2. **$A_{state}$ (Reviewer Attention State)**: Reviewer pacing, fatigue penalties, and time adequacy relative to historical baselines (Model 3).
3. **$F_{reviewer}$ (Reviewer Familiarity Score)**: Historical commit and review experience in the specific modules/files touched by this PR.

### Risk Tier Boundaries
- **High Residual Risk ($R_{res} \ge 0.65$)**: Triggers automated re-queuing and senior review assignment. Red indicator badge.
- **Medium Residual Risk ($0.35 \le R_{res} < 0.65$)**: Moderate risk; validation warning recommended before merge. Yellow indicator badge.
- **Low Residual Risk ($R_{res} < 0.35$)**: Safe change; review depth satisfactorily mitigates intrinsic change risk. Green indicator badge.

---

## 4. Machine Learning Model Architectures

```
                     Pull Request Unified Diff & Metadata
                                      │
               ┌──────────────────────┼──────────────────────┐
               ▼                      ▼                      ▼
        Model 1 (XGBoost)     Model 2 (DistilBERT)    Model 3 (Attention)
       Change Risk (0.05-0.95) Review Depth (0.0-1.0) Reviewer Fatigue (0.0-1.0)
               │                      │                      │
               │                      └──────────┬───────────┘
               │                                 ▼
               │                    Review Confidence C_review
               │                                 │
               └──────────────────────┬──────────┘
                                      ▼
                      Residual Risk Engine (scoring/residual.py)
                           R_res = R_change * (1 - C_review)
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
                 R_res >= 0.65               R_res < 0.65
               Auto Re-Queue PR             Approved / Logged
          (CODEOWNERS Senior Routing)
```

### Model 1: Change Risk Classifier (`models/risk/`)
- **Type**: Gradient-Boosted Decision Trees (XGBoost binary classifier).
- **Target**: Whether the PR introduced a post-merge defect (labeled via SZZ algorithm in `corpus/labeller.py`).
- **Feature Vector (28 Features)**:
  - **Diff Magnitude**: `lines_added`, `lines_removed`, `net_delta`, `total_changed_lines`, `files_touched`, `hunk_count`, `max_hunk_size`.
  - **Nonlinear Sizing**: Log-scale normalization to prevent massive refactors from saturating risk:
    $$S_{norm} = \min\left(1.0, \frac{\ln(1 + \text{lines\_changed})}{\ln(1 + 1000)}\right)$$
  - **90-Day Churn & Defect Density**: `total_churn_90d`, `total_revert_count`, `mean_defect_density`, `mean_ownership_gini` (Gini coefficient measuring author centralization).
  - **Sensitive Path Categories (Binary Regex Matching)**:
    - `path_auth` (authentication, JWT, session, login)
    - `path_payment` (billing, stripe, checkout, invoices)
    - `path_migration` (SQL schema migrations, DDL scripts)
    - `path_crypto` (encryption, hashing, TLS certificates, secrets)
    - `path_config` (`.env`, config YAML/JSON templates)
    - `path_infra` (Dockerfile, Kubernetes manifests, Terraform, Helm)
    - `path_audit` (compliance, audit logs, security policies)
    - `path_credentials` (private keys, tokens, `.pem`)
    - `path_iac` (Pulumi, CDK, Ansible)
    - `path_testing` (unit/integration test files)
  - **AI Generation & Authorship Signals**:
    - `ai_commit_signal`: Commits authored or co-authored with AI tags (`Co-authored-by: Copilot / Cursor / Devin / Claude`).
    - `diff_uniformity`: Variance in line length distributions (AI-generated code exhibits low variance/high uniformity).
    - `block_add_signal`: Large single-block insertions without corresponding removals.

### Model 2: Review Depth Scorer (`models/depth/`)
- **Type**: Fine-tuned Transformer (`distilbert-base-uncased`) with linear classification head + heuristic keyword parser.
- **Task**: Multi-class sequence classification on individual review comments.
- **Qualitative Taxonomy & Weights**:
  | Category | Assigned Weight | Criteria & Semantic Intent |
  | :--- | :---: | :--- |
  | `security` | **0.95** | Vulnerabilities, memory leaks, SQL injection, auth bypass |
  | `architectural` | **0.85** | Concurrency, race conditions, distributed rollback, state leaks |
  | `substantive` | **0.75** | Algorithmic flaws, off-by-one errors, boundary edge cases |
  | `clarifying` | **0.45** | Questions asking for explanation, intent, or design rationale |
  | `nitpick` | **0.20** | Variable naming, indentation, cosmetic formatting |
  | `rubber_stamp` | **0.00** | Superficial approvals: *"LGTM"*, *"looks good"*, *"approved"*, *"+1"* |
- **Depth Formulation**:
  $$D_{score} = \min\left(1.0, \frac{\sum_{i=1}^{N} W(\text{class}_i)}{\sqrt{N + 1}}\right)$$
- **Unresolved Rejection Penalty**: If a PR was approved while unresolved `CHANGES_REQUESTED` threads remain, depth is penalized by **35%**:
  $$D_{score} \leftarrow D_{score} \times 0.65$$

### Model 3: Reviewer Attention & Baseline (`models/attention/`)
- **Type**: Non-parametric robust statistics + Unsupervised Anomaly Detection (Isolation Forest).
- **Core Problem Solved**: Software engineering review durations have extreme outliers (interruptions, overnight reviews, meetings). Classical Gaussians fail.
- **Formulation**:
  $$\text{MAD} = \text{median}(|x_i - \tilde{x}|)$$
  $$\text{Robust } Z = \frac{x - \tilde{x}}{1.4826 \times \text{MAD}}$$
- **Fatigue Decay Index**: Review attention degrades under high consecutive review volumes within a rolling 4-hour window:
  $$A_{state} = \max\left(0.10, A_{baseline} - (\text{consecutive\_reviews} - 1) \times 0.08\right)$$
- **Isolation Forest Vector**: `[review_duration_seconds, diff_lines, consecutive_reviews]` identifying anomalous rushed reviews (e.g. 1,000 lines approved in 45 seconds).

---

## 5. Catalog of Deterministic Heuristic Rules

In addition to ML inferences, Vouch runs 7 production discrepancy checks that safeguard against deceptive reviews:

1. **Rapid Merge Discrepancy**:
   - *Condition*: Merged in under 120 seconds with $>10$ diff lines.
   - *Action*: Flags `rapid_merge`; raises change risk baseline by $+0.25$.
2. **Missing Reviewer Discrepancy**:
   - *Condition*: PR merged or approved with zero recorded reviewers.
   - *Action*: Forces $C_{review} = 0.05$ (guaranteeing High Residual Risk if $R_{change} > 0.35$).
3. **Self-Review Discrepancy**:
   - *Condition*: PR author and approving reviewer logins match.
   - *Action*: Forces $C_{review} = 0.00$; flags self-approval.
4. **WIP/Draft Merged Discrepancy**:
   - *Condition*: Merged PR containing `[WIP]`, `[DRAFT]`, or `TODO` in title or body without marked task completion.
   - *Action*: Increases Change Risk by $+0.20$.
5. **Mass File Touch Discrepancy**:
   - *Condition*: Single diff touches $>15$ files simultaneously.
   - *Action*: Elevates risk multiplier due to high blast radius across multiple architectural domains.
6. **Pending Changes Requested Discrepancy**:
   - *Condition*: Approval granted while an active `CHANGES_REQUESTED` state is recorded.
   - *Action*: Imposes $35\%$ penalty multiplier on $D_{score}$.
7. **Inattentive Rush Discrepancy**:
   - *Condition*: Review velocity $>10\times$ faster than reviewer's personal median pace for equivalent diff sizes.
   - *Action*: Reduces $A_{state}$ to $0.20$.

---

## 6. End-to-End Pipeline & AWS Infrastructure

```
                                  GitHub
                         (Webhook / REST API)
                                    │
                       ┌────────────┴────────────┐
                       ▼                         ▼
               AWS Lambda Ingest          Flask Web App
              (infra/template.yaml)     (dashboard/app.py)
                       │                         │
                       ▼                         ▼
              EventBridge Event Bus         Dual-Mode Store
             (vouch-events-prod)         (dashboard/store.py)
                       │                         │
                       ▼                         ▼
            Step Functions Pipeline         Amazon DynamoDB
          (infra/state_machine.asl.json)  vouch-repos & vouch-prs
                       │                    (ap-south-1)
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   Feature Lambda  Model Inferences Bedrock LLM
   (features/)     (SageMaker / ML) (Claude 3 Haiku)
```

### 1. Dual-Mode Storage Architecture (`dashboard/store.py`)
- **Local Mode (`USE_DYNAMODB=false`)**:
  - Persists to `data/repos.json` and `data/prs.json`.
  - Atomic temporary file swaps (`.tmp -> flush -> fsync -> rename`) with in-memory threading locks.
- **DynamoDB Production Mode (`USE_DYNAMODB=true`)**:
  - Region: `ap-south-1` (Mumbai).
  - Table 1: `vouch-repos` — Partition Key: `full_name` (`String`).
  - Table 2: `vouch-prs` — Partition Key: `pr_key` (`String`), Global Secondary Index: `repo-index` with Partition Key `repo` (`String`).
  - Batching: `save_prs()` executes bulk operations via `batch_writer()` (`BatchWriteItem`).
  - IAM: Utilizes standard AWS credential provider chain (`aws-elasticbeanstalk-ec2-role`).

### 2. Elastic Beanstalk Deployment Architecture
- **Environment**: `vouch-prod` in `ap-south-1`.
- **WSGI Entrypoint**: `main:app` running Gunicorn on port `5000`.
- **Dependency Isolation**:
  - `requirements-web.txt`: Lightweight web dependencies (~40 MB total) for fast Elastic Beanstalk deploys.
  - `requirements-ml.txt`: Heavy ML packages (`torch`, `transformers`, `xgboost`) reserved for SageMaker and CI testing.
- **Deployment Workflow (`.github/workflows/deploy-eb.yml`)**:
  - Gated automatically on the GitHub Actions `Tests` workflow (`workflow_run: completed: success`).

### 3. Amazon Bedrock LLM Narration (`explain/bedrock_client.py`)
- **Model**: `anthropic.claude-3-haiku-20240307-v1:0`.
- **Core Principle**: The LLM **never** calculates numeric scores. It receives the calculated features, discrepancy flags, and model outputs, producing a single professional explanatory sentence (e.g. *"Active PR #142197: 116-line diff touching sensitive paths; review depth within safety bounds."*).
- **Fail-Safe Mechanism**: If Bedrock credentials, quotas, or network connections are unavailable, Vouch displays a `(disconnected)` indicator and produces a deterministic template rationale without throwing unhandled exceptions.

---

## 7. Current Authentication State & Next Immediate Goal

### Current State
- Users can authenticate via **GitHub OAuth 2.0** or provide a session **Personal Access Token (PAT)**.
- Public repositories can be fetched and scored dynamically without user credentials.

### Target Architecture (`feat/auth`, Issue #4)
- **GitHub App with Installation Access Tokens**:
  - Eliminates manual PAT generation and rotation.
  - Grants Vouch a dedicated rate limit pool of **5,000 to 12,500 requests/hour** (independent of user PAT quotas).
  - Enables authenticated HTTP Git operations (`git clone` / `git fetch`) via `GitPython` for fast offline diff and commit history analysis without making thousands of individual REST calls.

---

## 8. Brainstorming Vectors for Claude & Future Enhancements

Use these prompts and areas for further architectural and modeling improvements:

1. **Graph Neural Networks (GNN) on File Dependency Topology**:
   - *Current*: Sensitive paths are matched via binary regexes (`path_auth`, `path_crypto`).
   - *Enhancement Idea*: Construct a repository import graph (e.g. AST import trees in Python/TypeScript/Go). Compute PageRank or GNN node embeddings to measure how close the touched files are to core architectural bottlenecks.
2. **Reviewer Attention Temporal Dynamics**:
   - *Current*: Reviewer fatigue is modeled with consecutive review counts in a rolling 4-hour window.
   - *Enhancement Idea*: Incorporate circadian rhythm signals (time-of-day, late-night commit approvals, weekend reviews) and time-to-first-review velocity.
3. **AST-Aware Semantic Diff Sizing**:
   - *Current*: Log-normalized line counts (`lines_added + lines_removed`).
   - *Enhancement Idea*: Count AST node mutations rather than text lines. White-space, comment changes, and re-formatting should contribute near-zero risk, whereas logic branch changes contribute high weight.
4. **LoRA Fine-Tuning for Model 2 (Review Depth)**:
   - *Current*: DistilBERT sequence classification with synthetic keyword taxonomy.
   - *Enhancement Idea*: Fine-tune a lightweight LLM or modern encoder (e.g., ModernBERT or DeBERTa-v3) using LoRA on real GitHub code review conversations labeled with PR outcome data.
5. **Automated Review Assignment Optimization**:
   - *Current*: CODEOWNERS-based round-robin fallback.
   - *Enhancement Idea*: Multi-objective optimization matching PR domain tags with the least-fatigued, most-familiar qualified engineer.
