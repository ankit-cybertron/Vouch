# Vouch — System Overview

Vouch is an automated, event-driven pull request risk intelligence and reviewer attention governance platform. It identifies deceptive pull request approvals, mitigates reviewer fatigue, and surfaces under-reviewed, high-risk code changes before they cause production incidents.

---

## 1. Problem Statement

Modern software development heavily relies on code reviews as the primary safety gate before deploying code into production. However, engineering organizations consistently face two acute failure modes:
1. **Rubber-Stamping**: Approvals issued without thorough inspection (e.g., approving hundreds of lines of critical infrastructure or authentication changes within seconds with simple comments like "LGTM").
2. **Reviewer Fatigue**: Reviewers reviewing multiple complex PRs consecutively experience sharp drops in attention and defect detection accuracy.

Traditional CI/CD tools measure static analysis and test coverage, but **fail to measure human review quality**. Vouch solves this by computing mathematical **Residual Risk**.

---

## 2. Core Mathematical Foundation

Vouch defines **Residual Risk** ($R_{res}$) as the probability that a pull request contains uncaught defects after review:

$$R_{res} = R_{change} \times (1 - C_{review})$$

Where:
- $R_{change} \in [0, 1]$ is the **Change Risk** predicted by Model 1 (trained on diff features, churn history, sensitive paths, and authorship signals).
- $C_{review} \in [0, 1]$ is the **Review Confidence**, representing the thoroughness and attention applied during the review:

$$C_{review} = 0.35 \times D_{score} + 0.30 \times A_{state} + 0.20 \times T_{adeq} + 0.15 \times F_{fam}$$

### Review Confidence Factors
| Factor | Name | Description | Weight |
|---|---|---|---|
| $D_{score}$ | **Depth Score** | Substantiveness of inline review comments (Model 2). | 35% |
| $A_{state}$ | **Attention State** | Reviewer session fatigue & pacing anomaly detection (Model 3). | 30% |
| $T_{adeq}$ | **Time Adequacy** | Time spent reviewing relative to diff size ($\min(1.0, \frac{t_{review}}{t_{expected}})$). | 20% |
| $F_{fam}$ | **Familiarity** | Reviewer's historical commit count in the touched files ($\min(1.0, \frac{commits}{20})$). | 15% |

### Operational Action: Re-Queuing Threshold
If $R_{res} > 0.65$, the PR is automatically flagged as **High Risk** and re-queued to a secondary reviewer via CODEOWNERS routing and Amazon SNS.

---

## 3. Core Principles

1. **Event-Driven, Asynchronous Pipeline**: Webhook ingestion acknowledges GitHub in milliseconds; all feature extraction and model inference run asynchronously via AWS EventBridge and Step Functions.
2. **Models Score, LLMs Narrate**: Machine learning models and deterministic equations compute all scores. Amazon Bedrock (Claude 3 Haiku) is strictly used to translate scores into a single actionable explanation sentence for developers.
3. **No Gimmicks & Production Rigor**: Minimalist, GitHub-native UI, no decorative icons, real data seeds, and graceful degradation when upstream services (e.g., Bedrock) are offline.
4. **Privacy by Design**: Reviewer attention metrics are aggregated into team-level health trends; individual engineer scores are not exposed.

---

## 4. High-Level Subsystems

```
┌──────────────────────────────────────────────────────────┐
│                    GitHub App Webhooks                   │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│             Ingestion & Feature Pipeline                 │
│  - API Gateway + Ingest Lambda + S3 (Raw Diffs)          │
│  - DynamoDB (vouch-events, vouch-prs, vouch-files)       │
│  - EventBridge Event Bus                                 │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│                 Three-Model ML Ensemble                  │
│  - Model 1: XGBoost Change Risk (28 Features)            │
│  - Model 2: DistilBERT Comment Depth Scorer              │
│  - Model 3: Attention Baseline & Robust Z-Scores         │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│                Scoring & Governance Engine               │
│  - Residual Risk Composition                             │
│  - Discrepancy Detectors (7 Rule-Based Signals)          │
│  - Amazon Bedrock Narration (Claude 3 Haiku)             │
│  - CODEOWNERS Re-queuing & SNS Notifications             │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│                 Vouch Dashboard (Flask)                  │
│  - Repositories Catalog & Live Fetcher                   │
│  - Residual-Risk PR Board                                │
│  - Team Health & Reviewer Fatigue Analytics              │
│  - PR Detail & Disconnected-State Fallback               │
└──────────────────────────────────────────────────────────┘
```
