# Technical Requirements Document (TRD)
## Vouch — Technical Specifications

| | |
|---|---|
| **Document Type** | Technical Requirements Document |
| **Product** | Vouch |
| **Version** | 1.0 |
| **Authors** | Ankit Kumar Tiwari, Ishaan Chaturvedi |
| **Status** | Approved |

---

## 1. Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| **Runtime** | Python 3.12 | Uniform across Lambdas and ML pipeline; type hint support |
| **IaC** | AWS SAM | Local dev + reproducible deploy; SAM templates checked in |
| **Ingest / Services** | AWS Lambda | Scale to zero; event-driven; no idle cost |
| **Orchestration** | AWS Step Functions (ASL) | Retries, timeouts, partial failure on multi-stage pipeline |
| **ML — Risk model** | XGBoost ≥ 2.0, scikit-learn ≥ 1.4 | Shallow ensemble; fast to train and serve; interpretable importances |
| **ML — Depth scorer** | PyTorch ≥ 2.2, HuggingFace Transformers ≥ 4.40 | DistilBERT small enough to train in 2–3 hrs on one GPU |
| **ML — Attention** | scikit-learn (Isolation Forest), scipy (MAD) | Lightweight; no GPU required |
| **Corpus mining** | GitHub GraphQL API, PyGithub, GitPython | GraphQL minimises round-trips; GitPython for blame traversal |
| **Data serialisation** | PyArrow ≥ 15, pandas ≥ 2.2 | Typed schemas enforced at write time |
| **Dashboard** | Flask ≥ 3.0, Jinja2, Chart.js CDN | Python-native; no Node build step; Chart.js for client-side visualisation |
| **Hosting** | AWS Amplify (static Flask build or EC2 if needed) | Single deployment URL for judging |
| **Messaging** | AWS SNS | Re-queue notifications |
| **Search (P2)** | AWS OpenSearch | Ownership routing via vector similarity |
| **Auth (P1)** | AWS Cognito + GitHub OAuth | Dashboard access control |

---

## 2. Repository Structure

```
vouch/
  infra/
    template.yaml           # SAM template — all AWS resources
    state_machine.asl.json  # Step Functions state machine definition
  ingest/
    __init__.py
    handler.py              # Webhook Lambda: HMAC verify → DynamoDB → S3 → EventBridge
    events.py               # Event normalisation schemas (dataclasses)
    requirements.txt
  features/
    __init__.py
    extractor.py            # Change-risk feature vector extraction (Lambda handler)
    precompute.py           # Scheduled file-history precompute → vouch-files DynamoDB
    requirements.txt
  corpus/
    __init__.py
    miner.py                # GitHub GraphQL crawler (5,000-PR hard cap)
    labeller.py             # SZZ labeller: revert/hotfix → git blame → labels.parquet
    writers.py              # Typed parquet writers (Arrow schemas enforced)
    requirements.txt
  models/
    risk/
      __init__.py
      train.py              # XGBoost training + temporal split + AUC evaluation
      inference.py          # SageMaker handler: change_risk + top-3 features
      requirements.txt
    depth/
      __init__.py
      train.py              # DistilBERT fine-tune, 6-class, EarlyStopping
      inference.py          # SageMaker handler: depth_score per review
      requirements.txt
    attention/
      __init__.py
      baseline.py           # Robust z-score baseline, DynamoDB persistence, cold-start
      model.py              # Isolation Forest: multivariate anomaly scoring
  scoring/
    __init__.py
    residual.py             # review_confidence + residual_risk + Step Functions Lambda
    routing.py              # CODEOWNERS resolver + SNS dispatch
  explain/
    __init__.py
    bedrock_client.py       # Claude 3 Haiku invocation
    prompts.py              # Structured prompt templates + constraints
  dashboard/
    app.py                  # Flask: board, pr_detail, validation, /api/prs
    requirements.txt
    templates/
      base.html             # Topnav, Inter font, footer
      board.html            # KPI cards, filter bar, PR table
      pr_detail.html        # Score strip, Model 1-3 cards, Bedrock, routing
      validation.html       # P@K cards, reveal mechanic, proof statement
    static/
      css/styles.css        # Design system: tokens, cards, badges, gauges
      js/main.js            # Live search, bar animation, KPI formatting
  eval/
    __init__.py
    retrospective.py        # Batch historical scoring at merge-time features only
    metrics.py              # Precision@K, AUC-ROC, confusion matrix, top catches
  docs/
    plan.md                 # Original execution plan
    prd.md                  # Product Requirements Document
    design.md               # Design Document
    trd.md                  # This file
    implementation_plan.md  # Day-by-day implementation tracking
  .gitignore
  README.md
```

---

## 3. API Specifications

### 3.1 GitHub Webhook Endpoint

```
POST /webhook
```

| Field | Value |
|---|---|
| Auth | HMAC-SHA256 (`X-Hub-Signature-256` header), secret via SSM Parameter Store |
| Response | `202 Accepted` — always, regardless of payload content |
| Timeout | Must return within 10 seconds (GitHub webhook timeout) |
| Supported events | `pull_request` (opened, closed, synchronize), `pull_request_review` (submitted), `pull_request_review_comment` (created) |

**Request Headers:**
```
X-GitHub-Event: pull_request
X-Hub-Signature-256: sha256=<hmac>
Content-Type: application/json
```

**Response:**
```json
{"status": "accepted", "pr_key": "kubernetes/kubernetes#12345"}
```

### 3.2 SageMaker Risk Endpoint

**Input (JSON):**
```json
{
  "lines_added": 300,
  "lines_removed": 50,
  "files_touched": 4,
  "path_auth": 1,
  "total_revert_count": 5,
  "mean_defect_density": 0.12,
  "..."
}
```

**Output (JSON):**
```json
{
  "change_risk": 0.81,
  "top_features": [
    {"feature": "total_revert_count", "contribution": 0.312},
    {"feature": "path_auth", "contribution": 0.228},
    {"feature": "lines_added", "contribution": 0.181}
  ]
}
```

### 3.3 SageMaker Depth Endpoint

**Input (JSON):**
```json
{
  "review_id": "RE_kwDOA-5678",
  "comments": [
    {"body": "Why is this retried 3 times?", "path": "pkg/retry.go", "line": 42}
  ]
}
```

**Output (JSON):**
```json
{
  "review_id": "RE_kwDOA-5678",
  "depth_score": 0.45,
  "comment_count": 1,
  "max_depth_class": 2,
  "comments": [
    {
      "body": "Why is this retried 3 times?",
      "class": 2,
      "class_name": "clarifying",
      "weight": 0.45,
      "path": "pkg/retry.go",
      "line": 42
    }
  ]
}
```

### 3.4 Dashboard REST API

```
GET /api/prs?risk=high|medium|low|all
```

**Response:**
```json
{
  "prs": [
    {
      "pr_key": "kubernetes/kubernetes#12345",
      "title": "Fix retry backoff...",
      "risk_tier": "high",
      "residual_risk": 0.73,
      "reviewer": "thockin",
      "merged_at": "2026-09-15T14:32:00Z",
      "status": "re_queued"
    }
  ],
  "count": 3
}
```

---

## 4. Data Schemas

### 4.1 DynamoDB — `vouch-prs`

| Attribute | Type | Description |
|---|---|---|
| `pk` | S | `repo#pr` e.g. `kubernetes/kubernetes#12345` |
| `state` | S | `open`, `closed`, `re_queued`, `scoring_failed` |
| `change_risk` | N (as S) | `[0, 1]` — Model 1 output |
| `review_confidence` | N (as S) | `[0, 1]` — Model 2+3 composite |
| `residual_risk` | N (as S) | `change_risk × (1 − review_confidence)` |
| `features` | M | Full change-risk feature vector (21 fields) |
| `re_queued` | BOOL | Whether a re-review was triggered |
| `explanation` | S | Bedrock-generated narration sentence |
| `ingested_at` | N | Unix timestamp |
| `scored_at` | N | Unix timestamp |

### 4.2 Feature Vector Schema (21 features)

```json
{
  "lines_added": 300,
  "lines_removed": 50,
  "net_delta": 250,
  "total_changed_lines": 350,
  "files_touched": 4,
  "hunk_count": 8,
  "max_hunk_size": 80,
  "total_churn_90d": 42,
  "total_revert_count": 5,
  "mean_defect_density": 0.12,
  "mean_ownership_gini": 0.61,
  "path_auth": 1,
  "path_payment": 0,
  "path_migration": 0,
  "path_crypto": 0,
  "path_config": 0,
  "path_infra": 0,
  "path_sensitive_path_count": 1,
  "author_prior_commits_in_files": 3,
  "ai_commit_signal": 0,
  "diff_uniformity": 0.4210
}
```

### 4.3 Parquet Schemas

**prs.parquet**
```
repo: string, pr_number: int64, title: string, state: string,
created_at: string, merged_at: string, additions: int64,
deletions: int64, changed_files: int64, author: string,
base_branch: string, head_branch: string, commit_message: string,
commit_sha: string
```

**reviews.parquet**
```
repo: string, pr_number: int64, review_id: string,
state: string, submitted_at: string, reviewer: string, body: string
```

**labels.parquet**
```
repo: string, pr_number: int64, is_defective: int8, label_source: string
```

---

## 5. Infrastructure Requirements

### 5.1 DynamoDB Tables

| Table | Billing | Capacity | TTL |
|---|---|---|---|
| `vouch-events` | On-demand | — | 90 days |
| `vouch-prs` | On-demand | — | — |
| `vouch-reviews` | On-demand | — | — |
| `vouch-baselines` | On-demand | — | — |
| `vouch-files` | On-demand | — | — |

### 5.2 Lambda Functions

| Function | Handler | Memory | Timeout |
|---|---|---|---|
| `vouch-ingest` | `ingest/handler.lambda_handler` | 256 MB | 10s |
| `vouch-features` | `features/extractor.lambda_handler` | 512 MB | 60s |
| `vouch-scoring` | `scoring/residual.lambda_handler` | 512 MB | 60s |

### 5.3 SageMaker Endpoints

| Endpoint | Model | Instance | Start/Stop |
|---|---|---|---|
| Risk scorer | XGBoost | `ml.m5.large` | Manual — tear down overnight |
| Depth scorer | DistilBERT | `ml.g4dn.xlarge` | Manual — tear down overnight |

### 5.4 IAM Permissions (Least Privilege)

**Ingest Lambda:**
- `dynamodb:PutItem` on `vouch-events`, `vouch-prs`
- `s3:PutObject` on `vouch-raw-bucket/raw/*`
- `events:PutEvents` on `vouch-events` bus

**Feature Lambda:**
- `dynamodb:GetItem` on `vouch-files`
- `dynamodb:UpdateItem` on `vouch-prs`
- `s3:GetObject` on `vouch-raw-bucket/raw/*`
- `events:PutEvents` on `vouch-events` bus

**Scoring Lambda:**
- `dynamodb:GetItem`, `UpdateItem` on `vouch-prs`, `vouch-reviews`, `vouch-baselines`
- `sagemaker:InvokeEndpoint` on risk and depth endpoints
- `bedrock:InvokeModel` on Claude 3 Haiku model ARN
- `sns:Publish` on `vouch-notifications` topic

---

## 6. Performance Requirements

| Requirement | Target | How Achieved |
|---|---|---|
| Webhook response time | < 500ms | 202 returned before any model inference |
| Change risk available | < 5s after PR opened | EventBridge fan-out, pre-computed file history |
| Residual risk available | < 10s after review submitted | Step Functions parallel stages |
| Dashboard page load | < 2s | DynamoDB reads from pre-computed state; no live inference |
| SageMaker inference (risk) | < 300ms | ml.m5.large; XGBoost shallow model |
| SageMaker inference (depth) | < 1s | ml.g4dn.xlarge; DistilBERT batch |

---

## 7. ML Training Requirements

### 7.1 Corpus Requirements

| Parameter | Value |
|---|---|
| Minimum PRs for training | 2,000 |
| Hard cap per run | 5,000 |
| Required columns | `pr_number`, `merged_at`, `commit_sha` + all feature columns |
| Label requirement | `is_defective ∈ {0, 1}` derived from SZZ |
| Split method | **Temporal only** — first 80% by merge date → train; last 20% → test |

### 7.2 Depth Scorer Training Requirements

| Parameter | Value |
|---|---|
| Base model | `distilbert-base-uncased` |
| Training data | ≥ 500 labelled comments per class (LLM-bootstrapped) |
| Hand-verified sample | 300 comments stratified across 6 classes |
| Epochs | 3 with EarlyStopping (patience=2) |
| Evaluation metric | Weighted F1, reported per class |
| Note | Report hand-verified precision explicitly; never a machine-only number |

---

## 8. Observability Requirements

| Signal | Tool | Alert Condition |
|---|---|---|
| Lambda errors | CloudWatch Logs | Error rate > 1% |
| SageMaker endpoint latency | CloudWatch Metrics | p99 > 2000ms |
| DynamoDB throttle | CloudWatch Metrics | Any throttle event |
| Billing | CloudWatch Billing Alarm | > $80 cumulative |
| Step Functions failures | CloudWatch Metrics | Any execution failure |

> ⚠️ **Set the billing alarm on Day 1.** SageMaker endpoints at $20–40/day are the standard way hackathon teams burn credits.

---

## 9. Dependency Versions (Pinned)

```
# Core
boto3>=1.34.0
python-dotenv>=1.0.0

# ML — Risk
xgboost>=2.0.0
scikit-learn>=1.4.0
pandas>=2.2.0
pyarrow>=15.0.0
numpy>=1.26.0

# ML — Depth
torch>=2.2.0
transformers>=4.40.0
accelerate>=0.28.0

# Corpus
requests>=2.31.0
GitPython>=3.1.40

# Dashboard
flask>=3.0.0
```

---

## 10. Testing Requirements

### 10.1 Unit Tests (P1)
- `ingest/events.py` — normalisation coverage for all supported event types and unsupported graceful rejection
- `features/extractor.py` — feature vector correctness for edge cases (empty diff, large diff, no file history)
- `scoring/residual.py` — boundary conditions (`change_risk=1.0, review_confidence=0.0` → `residual_risk=1.0`)
- `scoring/routing.py` — CODEOWNERS parsing, reviewer exclusion logic

### 10.2 Integration Tests (P1)
- End-to-end webhook → DynamoDB persistence (SAM local)
- Feature extraction with mock S3 diff and DynamoDB file history

### 10.3 Model Validation (P0)
- Temporal split AUC reported in `models/risk/output/*.meta.json`
- Depth scorer classification report per class in `models/depth/output/meta.json`
- Retrospective validation Precision@K in `eval/output/*.metrics.json`
