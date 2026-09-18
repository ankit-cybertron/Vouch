# Vouch

> **Review confidence scoring and risk-aware merge re-queuing for engineering teams**

[![Live App](https://img.shields.io/badge/Live%20Deployment-AWS%20Elastic%20Beanstalk-232F3E?style=flat-square&logo=amazon-aws&logoColor=white)](http://vouch.ap-south-1.elasticbeanstalk.com)
[![CI/CD](https://img.shields.io/badge/CI%2FCD-GitHub%20Actions-2088FF?style=flat-square&logo=github-actions&logoColor=white)](https://github.com/ankit-cybertron/Vouch/actions)
[![Tests](https://img.shields.io/badge/Tests-113%20passed-success?style=flat-square&logo=pytest&logoColor=white)](https://github.com/ankit-cybertron/Vouch)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![AWS Region](https://img.shields.io/badge/Region-ap--south--1-FF9900?style=flat-square&logo=amazon-aws&logoColor=white)](http://vouch.ap-south-1.elasticbeanstalk.com)

---

### 🌐 Live Production Deployment

**Access the live application**: [http://vouch.ap-south-1.elasticbeanstalk.com](http://vouch.ap-south-1.elasticbeanstalk.com)

Hosted on **AWS Elastic Beanstalk** (`ap-south-1` Mumbai) powered by production Gunicorn WSGI workers, dual-mode persistence, and automated CI/CD gating.

---

## Overview

**Vouch** is a review intelligence platform and automated gating system that evaluates whether pull request approvals carry genuine scrutiny. By fusing change risk prediction with deep review depth classification and reviewer fatigue baselines, Vouch computes the true **residual risk** of every pull request before it lands in production.

When critical code paths meet shallow scrutiny or rushed rubber-stamps, Vouch intercepts the merge, prevents silent outages, and automatically re-queues the PR for targeted senior review.

---

## Core Equation

$$\text{Residual Risk} = \text{Change Risk} \times (1 - \text{Review Confidence})$$

$$\mathbf{R_{\text{residual}} = R_{\text{change}} \times (1 - C_{\text{review}})}$$

- **High change risk + deep review** $\to$ **Acceptable** (substantive scrutiny offsets underlying risk).
- **Low change risk + shallow review** $\to$ **Acceptable** (low inherent blast radius).
- **High change risk + shallow review** $\to$ **Flagged & Re-queued** ($R_{\text{residual}} \ge 0.65$ triggers merge block and re-routing).

---

## Core Capabilities

- **Automated Risk & Review Scoring**: Evaluates pull requests against 3 ML/statistical models in real time to calculate defect probabilities and review thoroughness.
- **Dynamic Repository Ingestion**: Ingests, analyzes, and scores pull requests from public repositories on-the-fly with zero hardcoded limits.
- **On-Demand PR Analysis**: Search any pull request number to trigger instant GitHub API fetching, feature extraction, and ML scoring.
- **Team Health & Reviewer Coverage**: Live analytics tracking review fatigue distributions, unreviewed risk ratios, high-risk churn volume, and reviewer attention baselines.
- **Explainable AI via Amazon Bedrock**: Generates human-readable, context-aware risk explanations using Anthropic Claude 3.5 Sonnet on AWS Bedrock with graceful offline fallback.
- **Dual-Mode Persistence**: Seamlessly operates in zero-dependency thread-safe atomic local JSON mode or switches directly to cloud-scale Amazon DynamoDB.
- **Zero Gimmick Aesthetics**: High-performance, responsive UI designed around GitHub's native design language and Octicon design system.

---

## Machine Learning & Intelligence Pipeline

| Component | Architecture | Objective | Key Metrics |
|:---|:---|:---|:---|
| **Model 1: Change Risk** | XGBoost Classifier | Predicts defect introduction probability $P(\text{defect})$ from diff churn, file entropy, and sensitive subsystem paths | ROC-AUC $\ge 0.82$, PR-AUC $\ge 0.75$ |
| **Model 2: Review Depth** | DeBERTa-v3 / DistilBERT | Classifies review comments into 6 semantic depth categories (rubber-stamp, nitpick, clarifying, logic, architecture, security) | Macro F1 $\ge 0.78$ |
| **Model 3: Reviewer Attention** | Robust Z-Score + Isolation Forest | Analyzes reviewer dwell time, historical fatigue trends, and concurrent review load | Precision@K |
| **Composite Engine** | Deterministic Calibration | Composes change risk and review confidence into normalized residual risk score ($0.0 - 1.0$) | Flagging Threshold $\ge 0.65$ |
| **LLM Explainer** | Amazon Bedrock (Claude 3.5 Sonnet) | Synthesizes feature attributions and review comments into structured engineer-facing rationale | Structured, deterministic JSON |

---

## Cloud Architecture & Tech Stack

Vouch is engineered for enterprise-grade scalability and reliability on Amazon Web Services:

- **Web Application & API**: Flask 3.x with Gunicorn 26.x running on AWS Elastic Beanstalk (`ap-south-1`).
- **Data Persistence Layer**: Dual-mode storage adapter supporting thread-safe local JSON storage and Amazon DynamoDB (`vouch-repos` and `vouch-prs`).
- **Explainability Engine**: Amazon Bedrock via Boto3, invoking Claude 3.5 Sonnet with automated fallback when credentials or models are unavailable.
- **Automated CI/CD Pipeline**: GitHub Actions with a two-stage gated release workflow (`Tests` $\to$ `Deploy to AWS Elastic Beanstalk`).

---

## CI/CD Deployment Pipeline

Deployments are governed by a strict two-stage automated release pipeline:

1. **Test Suite Stage (`test.yml`)**: Executes on every push and pull request to `main`. Runs 113 comprehensive unit, integration, and route verification tests using Pytest.
2. **Elastic Beanstalk Deployment Stage (`deploy.yml`)**: Triggered via `workflow_run` only after the `Tests` workflow completes successfully. Packages source artifacts, validates production dependencies, and deploys directly to Elastic Beanstalk environment `vouch-prod`.


---

## Quickstart & Local Development

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
GITHUB_TOKEN=ghp_your_github_token       # Optional: increases API rate limits
USE_DYNAMODB=false                       # Set to true to enable AWS DynamoDB storage
AWS_REGION=ap-south-1
```

### 3. Run Locally

**Development Server:**
```bash
python main.py --port 5001
```

**Production Server (Gunicorn):**
```bash
gunicorn --bind 127.0.0.1:8000 application:application
```

Access local endpoints:
- **Repositories Catalog**: [http://localhost:5001/repos](http://localhost:5001/repos)
- **PR Intelligence Board**: [http://localhost:5001/board](http://localhost:5001/board)
- **Team Health Analytics**: [http://localhost:5001/health](http://localhost:5001/health)

### 4. Running the Test Suite

```bash
pytest
```

All 113 test suites validate feature extraction, risk calibration, route handlers, storage persistence, and discrepancy detection.

---

## Documentation

Comprehensive architecture, model, and deployment documentation is available in the [`docs/`](docs/) directory:

- [System Overview](docs/OVERVIEW.md) — High-level introduction, problem statement, and core concepts.
- [System Architecture](docs/ARCHITECTURE.md) — End-to-end data pipeline, storage backends, and component interactions.
- [Model Details](docs/MODELS.md) — Mathematical formulations, feature extraction schemas, and calibration logic.
- [Dashboard & API](docs/DASHBOARD_AND_API.md) — Complete endpoint reference and client interaction patterns.
- [AWS Deployment Guide](docs/AWS_DEPLOYMENT.md) — Step-by-step Elastic Beanstalk and DynamoDB deployment instructions.

---

## Team

**Quantified Minds**
- **Ankit Kumar Tiwari**
- **Ishaan Chaturvedi**

*Developed for WeMakeDevs "First Commit" — Bharat Builds Tour · Event 01*
