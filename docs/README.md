# Vouch — Technical Architecture & Implementation Reference

This document provides a comprehensive technical reference for the engineering architecture, machine learning ensemble, mathematical models, data schemas, and ingestion pipelines powering Vouch.

For a high-level product overview and getting started guide, see the root [README.md](../README.md).

---

## 1. System Architecture

```text
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
    │  • 7 Discrepancy Signal Penalties     │   │  • 33 Exact Rubber-Stamp Token Match  │
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
    │              R_residual = R_change * (1.0 - C_review)                 │
    │                                                                       │
    │    Threshold Evaluation: R_residual >= 0.65 ==> Trigger Re-Queue      │
    └───────────────────────────────────┬───────────────────────────────────┘
                                        │
                                        ▼
    ┌───────────────────────────────────────────────────────────────────────┐
    │               Explainable AI & Resilient 3-Tier Cascade               │
    │  [Tier 1] AWS Bedrock (Claude 3 Haiku / Claude 3.5 Sonnet)            │
    │  [Tier 2] Groq Cloud Cascade (Llama 3.3 70B / Qwen 27B / Mixtral)    │
    │           Dual-Key Failover: GROQ_API_KEY ==> GROQ_API_KEY_backup     │
    │  [Tier 3] Deterministic Vouch Fallback Engine                         │
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

## 2. Mathematical Formulations

### 2.1 The Residual Risk Equation

Vouch evaluates pull requests by joint probability composition:

$$R_{\text{residual}} = R_{\text{change}} \times (1.0 - C_{\text{review}})$$

* $R_{\text{change}} \in [0.04, 0.96]$: Prior probability that the proposed change introduces defects.
* $C_{\text{review}} \in [0.04, 0.95]$: Empirical confidence that peer review detected and resolved latent defects.
* $R_{\text{residual}} \in [0.02, 0.96]$: Unmitigated risk surviving the review process.

### 2.2 Model 1: Change Risk Formulation

Change risk balances code volume, sensitive subsystems, and structural process discrepancies:

1. **Logarithmic Sizing Calibration**:
   $$S_{\text{size}} = \min\left(1.0, \frac{\ln(1 + \text{lines})}{\ln(1 + 800)}\right)$$

2. **Path Sensitivity Score**:
   $$S_{\text{path}} = \begin{cases} \min(0.55 + 0.10 \times \text{count}, 1.0) & \text{if sensitive count} > 0 \\ 0.04 & \text{otherwise} \end{cases}$$

3. **File Breadth Score**:
   $$S_{\text{file}} = \min\left(\frac{\text{files}}{15.0}, 1.0\right)$$

4. **Base Change Risk**:
   $$R_{\text{base}} = 0.38 \times S_{\text{path}} + 0.40 \times S_{\text{size}} + 0.22 \times S_{\text{file}}$$

5. **Discrepancy Signal Penalties**:
   * No Reviewer Assigned: $+0.12$
   * Rapid Merge ($< 10$ min): $+0.10$
   * Stale PR ($> 30$ days): $+0.07$
   * WIP / Draft Title Merged: $+0.15$
   * Mass File Touch ($> 15$ files): $+0.08$
   * Minimal Description ($< 30$ chars for diff $> 200$ lines): $+0.06$
   * Author Self-Review: $+0.14$
   * AI Authorship Signal: $+0.05$

Bounded strictly: $R_{\text{change}} = \text{round}(\max(0.04, \min(R_{\text{change}}, 0.96)), 4)$.

### 2.3 Model 2: Review Depth Formulation

1. **Comment Density**:
   $$\text{density} = \min\left(\frac{\text{comments}}{\max(\text{lines}/1000, 0.1) \times 5.0}, 1.0\right)$$

2. **Semantic Comment Categories & Weights**:
   * `security`: 1.00
   * `architecture`: 0.90
   * `logic_concern`: 0.80
   * `test`: 0.55
   * `clarifying`: 0.45
   * `nit_style`: 0.15
   * `rubber_stamp`: 0.00

3. **Depth Composition**:
   $$D_{\text{score}} = 0.45 \times \text{density} + 0.35 \times D_{\text{reviews}} + 0.20 \times D_{\text{comments}}$$

### 2.4 Model 3: Reviewer Attention & Fatigue

1. **Robust Z-Score Normalization**:
   $$\text{MAD} = \text{median}(|x_i - \tilde{x}|), \quad \text{Robust } Z = \frac{x - \tilde{x}}{1.4826 \times \text{MAD}}$$

2. **Adequacy Calibration**:
   Adequate review duration is calibrated at $1.5$ seconds per line of code (minimum 180s).
   * Duration $< 30\%$ of adequate: $-0.35$ penalty
   * Duration $< 60\%$ of adequate: $-0.18$ penalty
   * Late-night review ($23:00 - 06:00$): $-0.15$ penalty
   * High rubber-stamp ratio: $-0.20 \times \text{ratio}$ penalty

3. **Composite Review Confidence**:
   $$C_{\text{review}} = 0.40 \times D_{\text{score}} + 0.25 \times \text{adequacy} + 0.25 \times \text{attention} + 0.10 \times \text{familiarity}$$
   Bounded strictly: $C_{\text{review}} = \text{round}(\max(0.04, \min(C_{\text{review}}, 0.95)), 4)$.

---

## 3. Data Storage & Persistence

Vouch implements an abstract storage interface `StorageBackend` (`dashboard/store.py`):

* **Local JSON Backend (`JsonStorageBackend`)**:
  * Default development backend (`data/repos.json`, `data/prs.json`).
  * Atomic file replacement via temporary files and `os.replace()`, ensuring thread safety under concurrent requests.
* **AWS DynamoDB Backend (`DynamoDbStorageBackend`)**:
  * Activated when `USE_DYNAMODB=true`.
  * Tables: `vouch-repos`, `vouch-prs`, `vouch-sessions`.
  * Global Secondary Index (`repo-index`) partitioned by repository.
  * Automatic `Decimal` conversion for Python floating-point types.

---

## 4. LLM Explanation Engine & Cascade

1. **AWS Bedrock**: Primary enterprise LLM utilizing Anthropic Claude 3 Haiku / Claude 3.5 Sonnet (`temperature: 0.0`).
2. **Groq Cloud Failover**: Sub-500ms inference with automatic dual-key fallback (`GROQ_API_KEY` $\to$ `GROQ_API_KEY_backup`) across models:
   * `qwen/qwen3.8-27b`
   * `llama-3.3-70b-versatile`
   * `llama-3.1-8b-instant`
3. **Deterministic Rule Engine**: Offline fallback synthesizing explanations from reviewer fatigue state, diff volume, and sensitive path signals.

---

## 5. Testing & Verification

The test suite contains **186 automated tests** across 20 test modules:

```bash
pytest
```

* `tests/integration/dashboard_routes.py`: Flask route integration tests.
* `tests/integration/github_oauth_service.py`: OAuth handshake and token validation.
* `tests/integration/pr_risk_calibration.py`: Score boundary and distribution tests.
* `tests/integration/token_authentication.py`: Session store token isolation and lifecycle.
* `tests/unit/change_risk_model.py`: Model 1 feature extraction and calibration.
* `tests/unit/codeowners_routing.py`: CODEOWNERS parsing and reviewer exclusion.
* `tests/unit/diff_feature_extraction.py`: Diff churn and hunk extraction.
* `tests/unit/heuristic_scoring_discrepancies.py`: Discrepancy penalties and score sync.
* `tests/unit/residual_risk_engine.py`: Residual risk arithmetic and confidence composition.
* `tests/unit/retrospective_metrics.py`: Precision@K and confusion matrix validation.
* `tests/unit/review_depth_scorer.py`: Model 2 comment classification.
* `tests/unit/reviewer_attention_baseline.py`: Median, MAD, and robust z-score.
* `tests/unit/szz_defect_labelling.py`: SZZ commit-tracing defect labeller.
* `tests/unit/test_groq_explanation.py`: Groq client cascading and API error handling.
* `tests/unit/test_load_balancing.py`: Review queue rebalancing logic.
* `tests/unit/test_pair_intelligence.py`: Pair collaboration and echo-chamber detection.
* `tests/unit/test_sla_predictions.py`: SLA turnaround forecast engine.
* `tests/unit/test_storage.py`: Atomic JSON writes and DynamoDB serialization.
* `tests/unit/test_version.py`: Application version metadata and git hash resolution.
* `tests/unit/webhook_event_normalization.py`: GitHub webhook payload normalization.
