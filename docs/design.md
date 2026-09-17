# Design Document
## Vouch — System Architecture & UX Design

| | |
|---|---|
| **Document Type** | Design Document |
| **Product** | Vouch |
| **Version** | 1.0 |
| **Authors** | Ankit Kumar Tiwari, Ishaan Chaturvedi |
| **Status** | Approved |

---

## 1. Design Philosophy

### 1.1 Core Principles

1. **Event-driven, not request-driven.** The webhook handler returns 202 in milliseconds; all model inference happens asynchronously. A synchronous GitHub webhook is a bottleneck and a single point of failure.

2. **Models score, LLMs narrate.** Bedrock is the last mile and never the engine. The risk score comes from trained models on real data; the LLM takes the numbers and turns them into a sentence a human can act on.

3. **Privacy is a product feature.** Individual confidence scores are private by design, not redacted by policy. The routing and team dashboard never expose per-reviewer scores.

4. **Pre-computation over live traversal.** File history computed live per PR on the critical path would add 30–40 seconds. A scheduled precompute job makes it a sub-millisecond DynamoDB lookup.

5. **Honest evaluation.** AUC 0.65 on a temporal split is reported honestly. A fabricated 0.95 on a random split would invalidate the entire demo.

---

## 2. End-to-End System Architecture

```
GitHub App Webhook
        │
        ▼
API Gateway (HTTPS)
        │  HMAC-SHA256 verify
        ▼
  Ingest Lambda
  ├── DynamoDB write (vouch-events)
  ├── S3 write (raw diff)
  └── EventBridge emit (PRIngested)
        │
        ▼
  Feature Lambda (triggered by EventBridge)
  ├── DynamoDB read (vouch-files — pre-computed file history)
  ├── S3 read (diff.patch)
  └── Feature vector → DynamoDB write (vouch-prs.features)
        │
        ▼
  Step Functions State Machine
  ├── Step 6:  Depth Scorer (SageMaker endpoint — DistilBERT)
  ├── Step 7:  Attention State (Lambda — robust z-score + Isolation Forest)
  ├── Step 8:  Residual Risk composition (Lambda — deterministic)
  ├── Step 9:  Bedrock explanation (Claude 3 Haiku)
  └── Step 10: Routing decision
              ├── IF residual_risk > 0.65 → SNS notification + GitHub review request
              └── ELSE → record outcome, close silently
        │
        ▼
  DynamoDB (vouch-prs — final scores)
        │
        ▼
  Flask Dashboard (Amplify Hosting)
  ├── /              → Risk Board
  ├── /pr/<key>      → PR Detail
  └── /validation    → Retrospective Validation (the demo)
```

---

## 3. AWS Service Architecture

### 3.1 Services and Roles

| Service | Role | Why It Is Load-Bearing |
|---|---|---|
| **API Gateway + Lambda** | Webhook ingestion | Bursty and event-driven; scales to zero between PRs. Returns 202 fast, never blocks on inference. |
| **EventBridge** | Event routing | Fans one PR event into feature extraction and downstream scoring independently |
| **Step Functions** | Scoring orchestration | Multi-stage pipeline with retries, partial failure and timeouts — precisely its use case |
| **SageMaker** | Train and serve Models 1–2 | Training jobs over corpus; two live inference endpoints |
| **DynamoDB** | Event store, PR state, baselines, file history | Single-digit-ms reads on the hot path |
| **S3** | Diffs, corpus, features, model artifacts | Everything large and immutable |
| **Bedrock** | Explanation layer | Stage 6 only — narrates, never scores |
| **SNS** | Re-queue notifications | Slack and email delivery |
| **Amplify Hosting** | Dashboard | The URL handed to judges |
| **CloudWatch** | Logs, metrics, alarms | Endpoint health during judging |

### 3.2 DynamoDB Table Design

| Table | Partition Key | Sort Key | Contents |
|---|---|---|---|
| `vouch-prs` | `repo#pr` | — | PR metadata, change_risk, review_confidence, residual_risk, state, outcome label |
| `vouch-reviews` | `repo#pr` | `reviewer#ts` | Per-review record: comments, depth classifications, timing, attention_state |
| `vouch-baselines` | `reviewer` | — | Rolling per-reviewer distributions, session counters, last-updated watermark |
| `vouch-files` | `repo#path` | — | Pre-computed file history: churn, revert count, ownership Gini, defect density |
| `vouch-events` | `repo#pr` | `ts` | Raw normalised event log — replay source for re-runs |

> **`vouch-files` is the performance-critical table.** Computing file history live per PR would dominate the critical path; pre-computing it reduces Step 2 to a lookup.

### 3.3 S3 Layout

```
s3://vouch-{env}-{account}/
  raw/{org}/{repo}/{pr}/diff.patch        # raw diffs (encrypted, 30-day lifecycle)
  corpus/{repo}/prs.parquet               # mined training corpus
  corpus/{repo}/reviews.parquet
  corpus/{repo}/labels.parquet            # SZZ-derived defect labels
  features/{repo}/risk_features.parquet   # materialised training features
  models/risk/{version}/model.tar.gz      # SageMaker artifacts
  models/depth/{version}/model.tar.gz
```

---

## 4. Event Flow Detail

### 4.1 PR Opened → Risk Scored (Steps 1–4)

```
t=0s    GitHub fires pull_request.opened
t+0s    API Gateway receives, verifies HMAC, Lambda writes event + emits PRIngested
t+2s    Feature Lambda: computes change-risk vector from diff + DynamoDB file history
t+3s    SageMaker risk endpoint: returns change_risk ∈ [0,1] + top-3 features
t+4s    Queue cache updated — reviewer dashboard now shows reordered PRs
```

### 4.2 Review Submitted → Residual Risk (Steps 5–10)

```
Review submitted (minutes to days later)
  → pull_request_review event → Ingest Lambda
  → Step Functions triggered

t+2s    Model 2 (depth scorer): each comment classified into 6 depth classes
t+1s    Model 3 (attention): reviewer's rolling baseline fetched, z-score computed
t+1s    Residual risk: change_risk × (1 − review_confidence) persisted
t+3s    Bedrock: one-sentence narration generated
t+2s    Routing: if residual_risk > 0.65 → SNS + GitHub review request
```

---

## 5. ML Pipeline Design

### 5.1 Model 1 — Change Risk

```
Algorithm: XGBoost (LightGBM fallback)
Task:      Binary classification — P(PR causes a defect)
Features:  21 features across 7 groups (size, complexity, file history,
           ownership, path sensitivity, testing, AI provenance)
Labels:    SZZ-derived — reverted/hotfixed commits traced back to introducing PR
Split:     Temporal only — never random (prevents future leakage)
Expected:  AUC 0.62–0.70 (honest; published work in similar band)
Serving:   SageMaker real-time endpoint; returns score + top-3 feature contributions
```

### 5.2 Model 2 — Review Depth Scorer

```
Algorithm: DistilBERT fine-tune (6-class sequence classification)
Task:      Classify review comment into depth class
Classes:   rubber_stamp (0.0), nit_style (0.15), clarifying (0.45),
           logic_concern (0.80), architecture (0.90), security (1.00)
Labels:    LLM-bootstrapped, 300-comment stratified sample hand-verified
Aggregate: Weighted mean of comment weights, boosted by max severity
Serving:   SageMaker real-time endpoint
```

### 5.3 Model 3 — Reviewer Attention

```
Algorithm: Robust z-score (median + MAD, not mean + SD)
           + Isolation Forest (multivariate anomaly layer)
Task:      Detect deviation from reviewer's own 30-review rolling baseline
Metrics:   seconds_per_KLOC, comment_density, mean_depth_score
Context:   consecutive_reviews, elapsed_session_minutes, hour_of_day
Cold start: <10 reviews → repo-level fallback, low_confidence=True
Privacy:   Baseline stored per-reviewer in DynamoDB; never exposed to team
```

### 5.4 Stage 5 — Residual Risk Composition (Deterministic)

```python
review_confidence = (
    0.40 × depth_score
  + 0.25 × time_adequacy
  + 0.25 × attention_state
  + 0.10 × reviewer_familiarity
)

residual_risk = change_risk × (1 − review_confidence)
```

> Weights are **hand-set** and explicitly flagged as such. Pretending they were learned would be the one dishonest claim in an otherwise honest system.

### 5.5 Stage 6 — Bedrock Explanation

```
Model:    Claude 3 Haiku (anthropic.claude-3-haiku-20240307-v1:0)
Input:    Structured JSON: all scores + top features + context
Output:   Exactly one sentence (max 40 words)
          Must reference at least one specific number
          Must end with a concrete action
          Must NOT produce a risk score itself
Temperature: 0.0 (deterministic narration)
```

---

## 6. Dashboard UX Design

### 6.1 Design Language

| Token | Value | Use |
|---|---|---|
| Background | `#F7F8FA` | Page body |
| Surface | `#FFFFFF` | Cards, nav |
| Border | `#E5E7EB` | 1px card/table borders |
| Text primary | `#111827` | Headings, primary content |
| Text secondary | `#6B7280` | Labels, meta |
| Indigo | `#4F46E5` | Primary accent, active nav, CTAs |
| Red | `#EF4444` | High risk |
| Amber | `#F59E0B` | Medium risk |
| Green | `#22C55E` | Low risk / passed |
| Font | Inter (Google Fonts) | All UI text |
| Mono font | JetBrains Mono | Scores, code, commit hashes |
| Card radius | `8px` | All card surfaces |
| Box shadow | `0 1px 3px rgba(0,0,0,0.05)` | Subtle card elevation |

### 6.2 Page Inventory

#### `/` — Residual Risk Board
- **KPI row:** Total PRs scored · High risk flagged · Re-queued today · Avg residual risk
- **Filter bar:** Live search + risk-level dropdown
- **PR table:** Sorted by residual_risk DESC · Risk-tier pill · Score bar · Reviewer · Status badge · View CTA
- **Live search:** Client-side JS, no page reload

#### `/pr/<key>` — PR Detail
- **Header:** PR title, repo, author, merged date, status badge (Re-queued / Passed)
- **Score strip:** Change Risk gauge × Review Confidence gauge = Residual Risk gauge (large)
- **Model 1 card:** Top-3 contributing features as horizontal Chart.js bar chart
- **Model 2 card:** Per-comment depth classification with colour-coded badges
- **Model 3 card:** Reviewer attention state, z-score delta vs baseline, session context
- **Bedrock card:** Italicised one-sentence narration with indigo left-border
- **Routing card:** Green "Passed" or Red "Re-queued" banner with detail

#### `/validation` — Retrospective Validation
- **Precision@K row:** P@5 · P@10 · P@20 as large stat cards
- **Methodology note:** Temporal split, neutral confidence assumption — documented prominently
- **Flagged PR table:** "Click to reveal" progressive disclosure of actual revert commits and hotfix messages
- **Proof statement:** Closing copy positioning this as a prediction that already came true

### 6.3 Component Hierarchy

```
base.html
├── TopNav (brand, links, live status dot)
├── board.html
│   ├── KPIRow (×4)
│   ├── FilterBar
│   └── PRTable → PRRow (×N)
├── pr_detail.html
│   ├── PRDetailHeader
│   ├── ScoreStrip (×3 gauges)
│   └── DetailGrid
│       ├── ChangeRiskCard (Chart.js bar chart)
│       ├── ReviewDepthCard (comment list with depth badges)
│       ├── AttentionCard (attention metrics)
│       ├── ExplanationCard (Bedrock quote)
│       └── RoutingCard (banner)
└── validation.html
    ├── PrecisionKRow (×4)
    ├── MethodNote
    ├── ValidationTable (reveal mechanic)
    └── ProofStatement
```

---

## 7. Security Design

| Concern | Mitigation |
|---|---|
| Webhook authenticity | HMAC-SHA256 verified in ingest Lambda before any processing |
| Raw diff sensitivity | Encrypted at rest in S3 (AES-256); 30-day lifecycle policy |
| Reviewer privacy | Per-reviewer baseline stored under reviewer's own DK partition; never exposed through team-level API |
| GitHub permissions | Read-only on code + PRs; write only on review requests. Nothing else. |
| IAM | Least-privilege per Lambda. No wildcard resource policies. |
| Dashboard auth | Cognito with GitHub OAuth federation (P1 — Amplify placeholder in v1) |

---

## 8. Cost Profile

| Component | Hackathon Scale | Note |
|---|---|---|
| Lambda + API Gateway | ~$0 | Thousands of invocations inside free tier |
| DynamoDB | $2–5 | On-demand; small tables |
| S3 | < $1 | A few GB of corpus and artifacts |
| SageMaker training | $5–15 | GPU hours for DistilBERT; XGBoost on CPU |
| SageMaker endpoints | **$20–40** | **Dominant cost.** Two endpoints continuously warm. |
| Bedrock | < $5 | One short completion per scored PR |

> ⚠️ **Cost discipline:** Tear SageMaker endpoints down overnight Thursday and Friday. Bring them up Saturday and leave warm through judging. Set a CloudWatch billing alarm on Day 1.
