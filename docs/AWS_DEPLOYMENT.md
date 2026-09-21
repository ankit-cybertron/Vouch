# Vouch — AWS Production Deployment Guide

This guide walks through deploying Vouch into an AWS production environment using AWS Serverless Application Model (SAM) and AWS SageMaker.

---

## 1. Prerequisites

- **AWS CLI** v2 configured with Administrator or appropriate DevOps IAM permissions:
  ```bash
  aws sts get-caller-identity
  ```
- **AWS SAM CLI** installed (`sam --version`).
- **Python 3.11+** installed.
- **Docker** running (required by SAM for building containerized Lambdas).
- **GitHub App** created on GitHub with:
  - Permissions: Pull Requests (Read & Write), Issues (Read & Write), Contents (Read).
  - Webhook URL: (Set after API Gateway deployment).
  - Webhook Secret: A secure random string.

---

## 2. Infrastructure as Code (`infra/template.yaml`)

The SAM template provisions the complete serverless stack:
1. **Amazon DynamoDB Tables**:
   - `vouch-events`
   - `vouch-prs`
   - `vouch-files`
   - `vouch-reviewers`
2. **Amazon S3 Bucket**:
   - `vouch-raw-{account_id}-{region}`
3. **Amazon EventBridge**:
   - Custom event bus `vouch-events-{environment}`
4. **AWS Lambda Functions**:
   - `IngestFunction`: Receives webhooks and verifies HMAC-SHA256 signatures.
   - `FeatureFunction`: Extracts 28 diff and history features.
   - `ScoringFunction`: Executes residual risk logic and discrepancy detection.
   - `RoutingFunction`: Handles CODEOWNERS resolution, GitHub re-reviews, and SNS alerts.
5. **AWS Step Functions State Machine**:
   - Orchestrates feature extraction, parallel model scoring, Bedrock narration, and governance decisions.
6. **Amazon SNS Topic**:
   - `vouch-alerts-{environment}` for Slack/PagerDuty integration.

---

## 3. Step-by-Step Deployment

### Step 1: Clone Repository & Configure Environment
```bash
cd Vouch
cp .env.example .env
```
Update `.env` with:
```ini
AWS_REGION=us-east-1
ENVIRONMENT=production
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
GITHUB_WEBHOOK_SECRET=your_secure_webhook_secret
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
```

### Step 2: Enable Amazon Bedrock Model Access
1. Open the **AWS Management Console** and navigate to **Amazon Bedrock** in `us-east-1`.
2. Go to **Model access** in the left navigation sidebar.
3. Click **Modify model access** and request access for **Anthropic Claude 3 Haiku**.
4. Approval is instantaneous.

### Step 3: Build SAM Artifacts
```bash
sam build --use-container
```

### Step 4: Deploy Stack
```bash
sam deploy --guided \
  --stack-name vouch-production \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND
```

During the guided deployment prompt, supply:
- `Environment`: `production`
- `GitHubWebhookSecret`: `[your secret]`
- `AlertEmail`: `security-alerts@yourcompany.com`

---

## 4. Deploy SageMaker Model Endpoints

SageMaker deployment is a **manual** two-step pipeline triggered from GitHub Actions.
It is not part of the automatic CI/CD flow.

### Step 1 — Prepare inference images (once, or when updating image versions)

Go to **GitHub → ankit-cybertron/Vouch → Actions → Prepare SageMaker Inference Images → Run workflow**.

This mirrors the required AWS Deep Learning Container images into the project's private ECR:

| Source | Destination |
|:---|:---|
| `720646828776.dkr.ecr.ap-south-1.amazonaws.com/sagemaker-xgboost:3.0-5` | `654157459447.dkr.ecr.ap-south-1.amazonaws.com/vouch/sagemaker-xgboost:3.0-5` |
| `763104351884.dkr.ecr.ap-south-1.amazonaws.com/pytorch-inference:2.3.0-cpu-py311` | `654157459447.dkr.ecr.ap-south-1.amazonaws.com/vouch/pytorch-inference:2.3.0-cpu-py311` |

This step is idempotent — re-running it is safe.

### Step 2 — Deploy model endpoints

Go to **GitHub → ankit-cybertron/Vouch → Actions → Deploy Models to Amazon SageMaker → Run workflow**.

The workflow runs `scripts/deploy_sagemaker.py`, which:
1. Packages `models/risk/risk_model.json` + `models/risk/inference.py` into `risk_model.tar.gz`
2. Packages `models/depth/model/` + `models/depth/inference.py` into `depth_model.tar.gz`
3. Uploads artifacts to S3 (`SAGEMAKER_BUCKET`)
4. Creates or updates endpoints `vouch-risk-model-prod` and `vouch-depth-model-prod`

**Required GitHub secrets / vars:**

| Name | Type | Value |
|:---|:---|:---|
| `AWS_ACCESS_KEY_ID` | Secret | IAM key for `vouch-github-actions` |
| `AWS_SECRET_ACCESS_KEY` | Secret | IAM secret |
| `SAGEMAKER_BUCKET` | Secret | S3 bucket name (no `s3://` prefix) |
| `SAGEMAKER_ROLE_ARN` | Secret | SageMaker execution role ARN |
| `AWS_ACCOUNT_ID` | Variable | `654157459447` |
| `AWS_REGION` | Variable | `ap-south-1` |

---

## 5. Webhook Registration

1. Retrieve the deployed API Gateway endpoint URL:
   ```bash
   aws cloudformation describe-stacks \
     --stack-name vouch-production \
     --query "Stacks[0].Outputs[?OutputKey=='WebhookApiUrl'].OutputValue" \
     --output text
   ```
2. Navigate to your **GitHub Organization Settings** $\rightarrow$ **GitHub Apps** $\rightarrow$ **Vouch**.
3. Under **Webhook**, set:
   - **Webhook URL**: `https://[api-id].execute-api.us-east-1.amazonaws.com/Prod/webhook`
   - **Secret**: `[your secret]`
   - **SSL verification**: Enable SSL verification.
4. Save changes. Vouch is now actively scoring pull requests in production!
