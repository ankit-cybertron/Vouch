# Vouch — System Architecture & AWS Infrastructure

Vouch is architected as an asynchronous, event-driven serverless system on AWS. It handles GitHub webhooks with low latency and offloads all heavy computation (diff parsing, feature extraction, ML inference, LLM narration) to background services.

---

## 1. End-to-End Pipeline

```
                                GitHub Webhook
                                      │
                                      ▼
                             API Gateway (HTTPS)
                                      │ HMAC-SHA256
                                      ▼
                                Ingest Lambda
                                ├── S3 (raw diff.patch)
                                ├── DynamoDB (vouch-events)
                                └── EventBridge (PRIngested)
                                      │
                                      ▼
                               Feature Lambda
                                ├── S3 read diff
                                ├── DynamoDB read file history (vouch-files)
                                └── DynamoDB write features (vouch-prs)
                                      │
                                      ▼
                          Step Functions State Machine
                                      │
       ┌──────────────────────────────┼──────────────────────────────┐
       ▼                              ▼                              ▼
Model 1: Change Risk         Model 2: Depth Scorer         Model 3: Attention State
(SageMaker XGBoost)          (SageMaker DistilBERT)        (Lambda: Median/MAD/IForest)
       │                              │                              │
       └──────────────────────────────┼──────────────────────────────┘
                                      │
                                      ▼
                        Residual Risk Engine (Lambda)
                        - Deterministic R_res formula
                        - 7 Discrepancy checks
                                      │
                                      ▼
                        Bedrock Explanation (Claude 3 Haiku)
                        - Translates scores to 1-sentence action
                        - Graceful fallback if disconnected
                                      │
                                      ▼
                        Governance & Routing (Lambda)
                        ├── IF R_res > 0.65:
                        │   ├── CODEOWNERS candidate lookup
                        │   ├── GitHub review request via REST API
                        │   └── SNS notification publish
                        └── ELSE:
                            └── Record outcome silently
```

---

## 2. Dual-Mode Storage Architecture

Vouch incorporates a **Dual-Mode Storage Adapter (`dashboard/store.py`)** that unifies local offline development and production cloud deployments under a single interface:

```
                  Unified Storage Interface (dashboard/store.py)
                                      │
                     ┌────────────────┴────────────────┐
                     ▼                                 ▼
         Local Mode (Default)               DynamoDB Mode (AWS)
         - File: data/repos.json            - Table: vouch-repos
         - File: data/prs.json              - Table: vouch-prs
         - Atomic tempfile swaps            - Activated via USE_DYNAMODB=true
         - Thread-safe memory locks         - Batch seeding: scripts/seed_dynamodb.py
```

### 1. Local JSON Persistence Mode (Default)
- **Zero Cloud Dependencies**: Runs out-of-the-box on developer machines without AWS credentials or Docker.
- **Disk Persistence**: Monitored repositories and scored PRs are persisted to `data/repos.json` and `data/prs.json`. When you add a new repo or score PRs via the web dashboard, changes survive server restarts.
- **Concurrency & Safety**: Thread-safe with atomic file writes (`write to .tmp -> flush -> fsync -> atomic rename`) preventing partial file writes.

### 2. DynamoDB Production Mode (`USE_DYNAMODB=true`)
- When deploying to AWS Elastic Beanstalk or Lambda, setting `USE_DYNAMODB=true` seamlessly switches the storage adapter to Amazon DynamoDB using `boto3.resource("dynamodb")`.
- Authentication uses boto3's standard credential provider chain (e.g. Elastic Beanstalk EC2 instance profile `aws-elasticbeanstalk-ec2-role`).
- Configuration environment variables:
  ```bash
  USE_DYNAMODB=true
  AWS_REGION=ap-south-1
  REPOS_TABLE=vouch-repos
  PRS_TABLE=vouch-prs
  ```
- Batch write support: `save_prs()` leverages `batch_writer()` for bulk PR writes (requires IAM `dynamodb:BatchWriteItem`).

---

## 3. DynamoDB Table Specifications

### `vouch-repos` (Repository Catalog Store)
- **Partition Key**: `full_name` (String — e.g. `kubernetes/kubernetes`)
- **Attributes**: `owner`, `repo`, `description`, `stars`, `forks`, `language`, `language_color`, `is_public`, `active_prs_count`, `closed_prs_count`, `avg_residual_risk`, `last_fetched_at`

### `vouch-prs` (Pull Request & Scoring Store)
- **Partition Key**: `pr_key` (String — e.g. `kubernetes/kubernetes#140058`)
- **Global Secondary Index (GSI)**:
  - **Index Name**: `repo-index`
  - **Partition Key**: `repo` (String — e.g. `kubernetes/kubernetes`)
  - **Projection**: `ALL`
- **Attributes**:
  - `repo`, `pr_number`, `title`, `author`, `state`
  - `change_risk`, `review_confidence`, `residual_risk`
  - `depth_score`, `attention_state`, `time_adequacy`, `reviewer_familiarity`
  - `top_features` (List of feature names & contribution weights)
  - `comments` (Classified inline comments & weights)
  - `explanation` (Generated narrative or fallback)
  - `re_queued` (Boolean)

### `vouch-files` (Precomputed File Churn & Defect History)
- **Partition Key**: `repo` (String)
- **Sort Key**: `file_path` (String)
- **Attributes**:
  - `churn_90d` (Integer)
  - `revert_count_90d` (Integer)
  - `defect_density` (Float)
  - `ownership_gini` (Float)
  - `top_authors` (Map of author to commit count)

### `vouch-reviewers` (Reviewer Attention Baselines)
- **Partition Key**: `repo` (String)
- **Sort Key**: `reviewer_login` (String)
- **Attributes**:
  - `historical_durations` (List of review durations in seconds)
  - `median_duration` (Float)
  - `mad_duration` (Float)
  - `review_count` (Integer)
  - `last_review_timestamp` (ISO-8601 String)

---

## 3. Storage Layer (Amazon S3)

- **Bucket**: `vouch-raw-{account_id}-{region}`
  - `diffs/{owner}/{repo}/{pr_number}.patch`: Raw git diff payload.
  - `events/{event_id}.json`: Raw webhook JSON payload for replayability.
  - `models/`: Exported model artifacts (`risk_model.json`, DistilBERT weights).

---

## 4. LLM Narration with Amazon Bedrock

- **Model**: `anthropic.claude-3-haiku-20240307-v1:0`
- **Principle**: The LLM never computes numeric scores. It receives the calculated features, discrepancy flags, and scores, and generates a single human-readable rationale.
- **Fail-Safe Mechanism**:
  If Bedrock credentials, quotas, or network connections are unavailable:
  1. The UI displays `(disconnected)` indicator.
  2. The system generates a deterministic rule-based template explanation (e.g., *"Approved in 73s on a 520-line diff touching sensitive paths; re-review recommended."*).
  3. Processing continues without throwing unhandled exceptions.
