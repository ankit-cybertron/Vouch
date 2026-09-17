# Vouch

> **Review confidence scoring and risk-aware re-queuing for engineering teams**

A GitHub App and review intelligence platform that scores how much each PR approval is actually worth, combines it with the risk of the change, and automatically re-queues merges where high risk met shallow scrutiny.

---

## Core Equation

```
residual_risk = change_risk × (1 − review_confidence)
```

- **High change risk + deep review** → Acceptable (confidence offsets risk)
- **Low change risk + shallow review** → Acceptable (low inherent change risk)
- **High change risk + shallow review** → Flagged and automatically re-queued for senior review

---

## Repository Layout

```
Vouch/
├── main.py           # Unified application entrypoint & CLI
├── requirements.txt  # Consolidated project dependencies
├── dashboard/        # Flask web application (board, repos, PR detail, validation)
│   ├── app.py        # Dashboard backend & scoring engine
│   ├── static/       # GitHub Primer CSS & interactive JS
│   └── templates/    # Octicon-styled Jinja2 templates
├── models/           # Machine learning & NLP models
│   ├── risk/         # Model 1: XGBoost defect probability from diff features
│   ├── depth/        # Model 2: DeBERTa/DistilBERT comment depth classifier
│   └── attention/    # Model 3: Reviewer attention baseline & session fatigue
├── scoring/          # Residual risk composition & PR routing engine
├── features/         # Diff parser, AST code graph, and reviewer precomputes
├── corpus/           # GitHub miner, SZZ defect labeller, parquet writers
├── explain/          # Amazon Bedrock (Claude 3.5 Sonnet) explanation engine
├── ingest/           # GitHub webhook Lambda receiver & event normalizer
├── infra/            # AWS SAM templates, Step Functions ASL, and IAM policies
├── eval/             # Retrospective validation harness & precision@K metrics
└── docs/             # PRD, TRD, system architecture, and design specifications
```

---

## Quickstart

### 1. Clone Repository & Setup Virtual Environment

```bash
git clone https://github.com/ankit-cybertron/Vouch.git
cd Vouch

python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies (Single Requirements File)

```bash
pip install -r requirements.txt
```

### 3. Run Vouch Dashboard

Start the application with the root entrypoint:

```bash
python main.py
```

Options:
```bash
python main.py --port 5001        # Run on custom port (default: 5001)
python main.py --host 127.0.0.1   # Bind to custom host (default: 0.0.0.0)
python main.py --debug            # Enable Flask debug mode
```

Open your browser to:
- **Repositories Catalog**: [http://localhost:5001/repos](http://localhost:5001/repos)
- **PR Intelligence Board**: [http://localhost:5001/board](http://localhost:5001/board)
- **Retrospective Validation**: [http://localhost:5001/validation](http://localhost:5001/validation)

*(Optional: export `GITHUB_TOKEN="your_token"` to increase GitHub API limits from 60 to 5,000 requests/hour).*

---

## Core Capabilities

### 1. Dynamic Popular Repository Discovery (Zero Hardcoding)
- Discovers top active open-source repositories dynamically using GitHub Search API (`stars:>35000+fork:false+archived:false`).
- Click **"Discover Popular"** to catalog trending repositories, fetch active and closed PRs, and evaluate models automatically.

### 2. Multi-Model PR Review Scoring (>= 25 PRs Guaranteed)
- Every fetched repository contains at least 25 to 28+ pull requests across open and merged states.
- **Initial Scoring Policy**: Automatically evaluates $\max(25, \text{PRs in last 30 days})$ with Model 1, Model 2, and Model 3.
- **On-Demand Scoring**: Older PRs display `Pending Analysis` with a **"Run Models"** button for instant evaluation.

### 3. Search by PR Number with On-the-Fly Fetch
- Search any PR by number (`#128540` or `128540`).
- If not present in the cached list, Vouch offers an instant **"Fetch from GitHub & Rate"** action to evaluate and display the PR immediately.

### 4. Refetch Sync for Newly Launched PRs
- Click **"Refetch"** on any repository card or PR board to query GitHub for newly created pull requests, evaluate them, and update repository risk metrics.

### 5. Genuine GitHub Primer Aesthetics
- Clean, professional GitHub design system powered by official GitHub Primer Octicon vector SVGs with zero informal emojis.

---

## ML Pipeline

| Model | Algorithm | Task | Target Metric |
|---|---|---|---|
| **Model 1 — Change Risk** | XGBoost | Predicts defect probability $P(\text{defect})$ from diff size, sensitive paths, and file churn | ROC-AUC ≥ 0.82 |
| **Model 2 — Review Depth** | DeBERTa / DistilBERT | Classifies review comments into 6 depth classes (rubber-stamp, nitpick, clarifying, logic, architecture, security) | Macro F1 ≥ 0.78 |
| **Model 3 — Reviewer Attention** | Robust Z-Score + Isolation Forest | Detects reviewer fatigue and session attention deviations | Precision@K |
| **Composite — Residual Risk** | Deterministic Formula | $R_{\text{residual}} = R_{\text{change}} \times (1 - C_{\text{review}})$ | Flagging Threshold ≥ 0.65 |
| **Stage 6 — Explanation** | Amazon Bedrock (Claude 3.5 Sonnet) | Generates structured, actionable sentence explaining risk contributors | Readability & Accuracy |

---

## AWS Cloud Architecture

```
GitHub Webhook → API Gateway → Lambda (Ingest) → EventBridge
                                                     ↓
                                        Step Functions State Machine
                                                     ↓
                     ┌───────────────────────────────┼──────────────────────────────┐
                     ↓                               ↓                              ↓
             SageMaker (Model 1)            SageMaker (Model 2)            SageMaker (Model 3)
                     └───────────────────────────────┬──────────────────────────────┘
                                                     ↓
                                       Lambda (Scoring & Residual Risk)
                                                     ↓
                                           DynamoDB / EventBridge
                                                     ↓
                                       Amazon Bedrock (Claude 3.5)
                                                     ↓
                                           SNS / GitHub Check Run
```

---

## Team

**Quantified Minds** — Ankit Kumar Tiwari, Ishaan Chaturvedi  
*WeMakeDevs "First Commit" — Bharat Builds Tour · Event 01*
