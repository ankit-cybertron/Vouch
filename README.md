# Vouch

**Automated Code Review Intelligence & Risk-Aware PR Governance**

[![Live Deployment](https://img.shields.io/badge/Deployment-AWS%20Elastic%20Beanstalk-232F3E?style=flat-square&logo=amazon-aws&logoColor=white)](http://vouch.ap-south-1.elasticbeanstalk.com)
[![Version](https://img.shields.io/badge/Version-v2.6-0969da?style=flat-square&logo=github&logoColor=white)](https://github.com/ankit-cybertron/Vouch)
[![CI/CD](https://img.shields.io/badge/CI%2FCD-GitHub%20Actions-2088FF?style=flat-square&logo=github-actions&logoColor=white)](https://github.com/ankit-cybertron/Vouch/actions)
[![Tests](https://img.shields.io/badge/Tests-186%20passed-success?style=flat-square&logo=pytest&logoColor=white)](https://github.com/ankit-cybertron/Vouch)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![AWS Region](https://img.shields.io/badge/Region-ap--south--1-FF9900?style=flat-square&logo=amazon-aws&logoColor=white)](http://vouch.ap-south-1.elasticbeanstalk.com)
[![Walkthrough Video](https://img.shields.io/badge/Demo%20Video-YouTube-FF0000?style=flat-square&logo=youtube&logoColor=white)](https://youtu.be/m5iM3ArUbz0)

---

### Live Application & Demonstration

* **Production Environment**: [http://vouch.ap-south-1.elasticbeanstalk.com](http://vouch.ap-south-1.elasticbeanstalk.com)  
  Hosted on AWS Elastic Beanstalk (`ap-south-1` Mumbai) with automated CI/CD gating and production Gunicorn WSGI workers.
* **Product Walkthrough Video**: [https://youtu.be/m5iM3ArUbz0](https://youtu.be/m5iM3ArUbz0)  
  A 2-minute walkthrough showing live PR triage, rubber-stamp interception, sub-500ms AI narration, and reviewer pair analytics.

---

## What is Vouch?

In modern software delivery, peer review is the last human defense before production. However, standard branch protection rules treat every approval identically: a 30-second approval with a bare "LGTM" satisfies branch protection just as easily as an in-depth security review.

**Vouch** is an automated Code Review Intelligence platform that evaluates whether pull request approvals carry genuine scrutiny. By fusing change blast radius with semantic review depth and reviewer fatigue telemetry, Vouch computes the true **residual risk** surviving peer review.

When complex, sensitive code receives rushed or superficial approvals, Vouch intercepts the merge, prevents silent outages, and automatically re-queues the pull request for senior CODEOWNERS review.

---

## Core Product Capabilities

* **Automated Rubber-Stamp Interception**: Detects approvals submitted in under a minute, empty reviews, and non-technical commentary on multi-hundred-line changes.
* **Empirical Residual Risk Governance**: Replaces binary approval checkboxes with continuous residual risk scoring:
  $$\text{Residual Risk} = \text{Change Risk} \times (1.0 - \text{Review Confidence})$$
  Pull requests exceeding the 0.65 threshold are automatically flagged for mandatory re-review.
* **Forensic PR Triage & Audit**: Provides line-by-line inspection timelines, sensitive file mapping (auth, crypto, billing, migrations), and SHAP feature attribution weights.
* **Reviewer Workload & Cognitive Fatigue Monitoring**: Tracks session velocity, off-hours review volume, and queue pressure to protect engineering teams from burnout.
* **Pair Review Intelligence**: Identifies organizational review patterns, flags reciprocal rubber-stamping silos between pairs of engineers, and suggests optimal cross-functional reviewers.
* **Review SLA Breach Forecasting**: Estimates expected turnaround times for open pull requests and proactively alerts team leads before delivery milestones breach.
* **Resilient AI Narration**: Translates complex telemetry into a single, concrete sentence for engineering leads using AWS Bedrock and Groq Cloud with dual-key failover.
* **Enterprise GitHub Onboarding**: 1-click GitHub App integration with short-lived Installation Access Tokens (IATs) and dedicated 5,000 req/hr rate limit quota.

---

## How Vouch Compares

| Capability | Traditional Linters & SAST (SonarQube, CodeQL) | Standard Branch Protection (GitHub Native) | Vouch Review Intelligence |
|:---|:---:|:---:|:---:|
| **Human Inspection Rigor** | ❌ | ❌ | ✅ |
| **Rubber-Stamp Interception** | ❌ | ❌ | ✅ |
| **Cognitive Fatigue Tracking** | ❌ | ❌ | ✅ |
| **Critical Module Awareness** | ❌ | ❌ | ✅ |
| **Pair Collaboration Dynamics** | ❌ | ❌ | ✅ |
| **Actionable AI Triage Narration** | ❌ | ❌ | ✅ |
| **Resilient 3-Tier Failover** | ❌ | ❌ | ✅ |

---

## Quickstart & Local Development

### 1. Clone and Install Dependencies

```bash
git clone https://github.com/ankit-cybertron/Vouch.git
cd Vouch

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment (Optional)

Create a `.env` file in the project root:

```env
GITHUB_TOKEN=ghp_your_token          # Optional: increases GitHub API rate limits
GROQ_API_KEY=gsk_your_key            # Optional: enables sub-500ms AI narration
USE_DYNAMODB=false                   # Set to true for AWS DynamoDB persistence
AWS_REGION=ap-south-1
```

### 3. Start the Server

```bash
python main.py --port 5001
```

Access the local web dashboard:
* **Landing Page**: [http://localhost:5001/](http://localhost:5001/)
* **Repositories Catalog**: [http://localhost:5001/repos](http://localhost:5001/repos)
* **PR Triage Board**: [http://localhost:5001/board](http://localhost:5001/board)
* **Reviewer Intelligence**: [http://localhost:5001/reviewer](http://localhost:5001/reviewer)
* **Pair Review Intelligence**: [http://localhost:5001/pair-intelligence](http://localhost:5001/pair-intelligence)
* **Team Health Scorecard**: [http://localhost:5001/team-health](http://localhost:5001/team-health)

### 4. Run Test Suite

```bash
pytest
```

All 186 unit and integration test suites validate feature extraction, risk calibration, route handlers, storage persistence, and discrepancy detection.

---

## Technical Documentation & Architecture

For in-depth technical specifications, mathematical derivations, machine learning pipeline details, data schemas, and cloud deployment guides, refer to the [`docs/`](docs/) directory:

* [Technical Architecture & Specifications](docs/README.md) — Comprehensive technical reference, ML models, and equations.
* [System Architecture](docs/ARCHITECTURE.md) — End-to-end data pipeline and storage backends.
* [Model Details](docs/MODELS.md) — Machine learning schemas and feature extraction logic.
* [Dashboard & API Reference](docs/DASHBOARD_AND_API.md) — Route controllers and client interaction patterns.
* [AWS Deployment Manual](docs/AWS_DEPLOYMENT.md) — Elastic Beanstalk and DynamoDB operational guide.

---

## Contributors

* [Ankit Kumar Tiwari](https://github.com/ankit-cybertron)
* [Ishaan Chaturvedi](https://github.com/Ishaan-Chaturved1)
* [Vaibhav Jain](https://github.com/vaibhav4561)

*Developed for WeMakeDevs "First Commit" — Bharat Builds Tour · Event 01*
