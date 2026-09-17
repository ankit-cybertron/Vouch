# Vouch

> **Review confidence scoring and risk-aware re-queuing for engineering teams**

A GitHub App that scores how much each PR approval is actually worth, combines it with the risk of the change, and automatically re-queues merges where high risk met shallow scrutiny.

---

## Core Equation

```
residual_risk = change_risk × (1 − review_confidence)
```

- **High change risk + deep review** → acceptable
- **Low change risk + shallow review** → acceptable
- **High change risk + shallow review** → flagged and re-queued

---

## Repository Layout

```
vouch/
  infra/            # SAM templates, Step Functions ASL, IAM policies
  ingest/           # Webhook Lambda, event normalisation
  features/         # Feature extraction, file-history precompute
  corpus/           # GraphQL miner, SZZ labeller, parquet writers
  models/
    risk/           # XGBoost training + SageMaker inference handler
    depth/          # DistilBERT fine-tune + SageMaker inference handler
    attention/      # Rolling baseline, robust z-score, isolation forest
  scoring/          # Residual risk composition, routing decision
  explain/          # Bedrock prompt templates + client
  dashboard/        # Flask web app (board, PR detail, validation view)
  eval/             # Retrospective validation, precision@K reporting
```

---

## Quickstart

### Prerequisites
- Python 3.12+
- AWS CLI configured with appropriate credentials
- AWS SAM CLI (`brew install aws-sam-cli`)
- GitHub App registered with your organisation

### 1. Clone and set up environment

```bash
git clone https://github.com/<your-org>/vouch.git
cd vouch
python -m venv .venv
source .venv/bin/activate
```

### 2. Deploy infrastructure

```bash
cd infra
sam build
sam deploy --guided
```

### 3. Mine training corpus

```bash
pip install -r corpus/requirements.txt
python corpus/miner.py --repo kubernetes/kubernetes --limit 5000
python corpus/labeller.py --repo kubernetes/kubernetes
```

### 4. Train models

```bash
# Risk model
pip install -r models/risk/requirements.txt
python models/risk/train.py

# Depth scorer
pip install -r models/depth/requirements.txt
python models/depth/train.py
```

### 5. Run the dashboard

```bash
pip install -r dashboard/requirements.txt
cd dashboard
flask run
# Open http://127.0.0.1:5000
```

---

## ML Pipeline

| Model | Algorithm | Task |
|---|---|---|
| **Model 1** — Change Risk | XGBoost | `P(defect)` from diff features at merge time |
| **Model 2** — Review Depth | DistilBERT fine-tune | Classify each comment into 6 depth classes |
| **Model 3** — Reviewer Attention | Robust z-score + Isolation Forest | Detect deviation from reviewer's own baseline |
| **Stage 5** — Residual Risk | Deterministic composition | `change_risk × (1 − review_confidence)` |
| **Stage 6** — Explanation | Amazon Bedrock (Claude 3) | Narrate findings as one actionable sentence |

---

## AWS Services

API Gateway → Lambda → EventBridge → Step Functions → SageMaker → DynamoDB → Bedrock → SNS

---

## Team

Quantified Minds — Ankit Kumar Tiwari, Ishaan Chaturvedi
WeMakeDevs "First Commit" — Bharat Builds Tour · Event 01
