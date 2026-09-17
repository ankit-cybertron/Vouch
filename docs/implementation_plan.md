# Implementation Plan
## Vouch — Four-Day Execution Plan · 17–20 September 2026

| | |
|---|---|
| **Document Type** | Implementation Plan |
| **Product** | Vouch |
| **Version** | 1.0 |
| **Team** | Quantified Minds — Ankit Kumar Tiwari (A), Ishaan Chaturvedi (B) |
| **Status** | In Progress |

---

## Critical Scheduling Note

> ⚠️ **The optional Bangalore day is Saturday 19 Sept, 8 AM–8 PM — Day 3 of 4.**
> Day 3 is when deployment and integration happen. Attending costs a full working day at the worst possible moment.
> **Recommendation:** if attending, one person goes and the other holds the build. Do not both go.

---

## Dependency Chain

```
D1-A1 corpus ──► D1-A2 labels ──► D1-A3 features ──► D2-A1 risk model ──┐
                                                                          ├──► D2-C1 Step Functions ──► D3-A1 validation ──► D4-1 metrics ──► D4-3 video
D1-B1 skeleton ──► D1-B3 comment labels ──► D2-B1 depth model ──────────┘

D1-B2 Amplify placeholder ──► D3-B1 dashboard ──► D3-B2 validation view ──► D4-3 video
```

> **The critical path runs through the corpus.** If D1-A1 slips, every model slips, validation slips, and the video loses its strongest segment. The 5,000-PR cap is not a suggestion.

---

## Day 1 — Thursday 17 Sept · Data and Skeleton

**Goal:** Labelled corpus in S3, webhook events flowing to DynamoDB, live Amplify URL.

| Time | ID | Owner | Task | Done |
|---|---|---|---|---|
| 08:00–09:00 | D1-0 | Both | Kickoff: repo scaffolded, AWS credits redeemed, Builder Center profiles confirmed, GitHub App registered (`vouch-app`), CloudWatch billing alarm set at $80 | `[ ]` |
| 09:00–13:00 | D1-A1 | **Ankit** | Run `corpus/miner.py` against target repo (kubernetes/kubernetes). Pull PR, review and comment history via GitHub GraphQL. Write parquet to S3. **Hard cap: 5,000 PRs. Stop at 17:00 regardless.** | `[ ]` |
| 09:00–13:00 | D1-B1 | **Ishaan** | AWS skeleton via SAM: API Gateway → Lambda (ingest) → EventBridge → DynamoDB. All five tables created. Webhook signature verification passing. | `[ ]` |
| 14:00–17:00 | D1-A2 | **Ankit** | Run `corpus/labeller.py`: revert and hotfix detection, blame traversal to introducing commit, emit `labels.parquet`. Log positive rate. | `[ ]` |
| 14:00–17:00 | D1-B2 | **Ishaan** | End-to-end webhook test against a scratch repo (`curl` simulated events). Amplify placeholder deployed — the public URL exists from Day 1. | `[ ]` |
| 17:00–20:00 | D1-A3 | **Ankit** | Feature extraction v1: run `features/precompute.py` over cloned repo → `vouch-files` DynamoDB. Run `features/extractor.py` against corpus to materialise training feature table. | `[ ]` |
| 17:00–20:00 | D1-B3 | **Ishaan** | Sample ~1,000 review comments. Run LLM bootstrap labelling pipeline to assign depth classes. Write `comments_labelled.parquet`. | `[ ]` |
| 20:00–21:00 | D1-G | Both | **Gate 1 review** (see below) | `[ ]` |

### Gate 1 — Thursday 21:00

> ✅ **Pass:** labelled corpus in S3, webhook events flowing to DynamoDB, live Amplify URL.

**If failed:** Drop to a single repo and 2,000-PR cap. Do not attempt a second repo. Consider substituting a public GH Archive extract for live GraphQL mining.

---

## Day 2 — Friday 18 Sept · Models

**Goal:** First end-to-end residual risk score on a real PR, persisted to DynamoDB.

| Time | ID | Owner | Task | Done |
|---|---|---|---|---|
| 09:00–13:00 | D2-A1 | **Ankit** | Train Change Risk Model (`models/risk/train.py`). Baseline XGBoost, temporal split. Log honest AUC on held-out set. Save `risk_model.json` + `meta.json`. | `[ ]` |
| 09:00–13:00 | D2-B1 | **Ishaan** | Hand-verify the 300-comment stratified sample (classification report by class). Fine-tune DistilBERT (`models/depth/train.py`). Record verified precision per class. | `[ ]` |
| 14:00–17:00 | D2-A2 | **Ankit** | Deploy risk model to SageMaker endpoint. Write inference handler + smoke test: `{"lines_added": 300, ...}` → `{"change_risk": 0.72, ...}`. | `[ ]` |
| 14:00–17:00 | D2-B2 | **Ishaan** | Deploy depth scorer to second endpoint. Wire feature-extraction Lambda to live EventBridge events so real PRs from scratch repo flow through. | `[ ]` |
| 17:00–20:00 | D2-C1 | Both | Step Functions state machine: wire depth → attention → residual risk → Bedrock → routing. Test with mocked SageMaker responses first. | `[ ]` |
| 20:00–22:00 | D2-C2 | Both | Debug the full path on the scratch repo. **First end-to-end residual risk score on a real PR, persisted to DynamoDB.** Log it. Screenshot it. | `[ ]` |
| 22:00 | D2-G | Both | **Gate 2 review — the decisive checkpoint** (see below) | `[ ]` |

### Gate 2 — Friday 22:00 (The Decisive One)

> ✅ **Pass:** one real PR scored end-to-end — change risk, depth, residual risk, persisted to DynamoDB.

**If failed:** Cut Model 3 entirely. Cut OpenSearch. Run residual risk from Models 1 and 2 only. A two-model system that works beats a four-model system that doesn't. **Decide Friday night, not Sunday morning.**

---

## Day 3 — Saturday 19 Sept · Integration and the Validation View

**Goal:** Deployed dashboard with a working retrospective validation view.

| Time | ID | Owner | Task | Done |
|---|---|---|---|---|
| 09:00–13:00 | D3-A1 | **Ankit** | Retrospective validation engine (`eval/retrospective.py`). Batch-score historical PRs at merge-time information only. Join against revert outcomes. Compute Precision@K. | `[ ]` |
| 09:00–13:00 | D3-B1 | **Ishaan** | Dashboard: wire Flask `app.py` to live DynamoDB reads (replace demo data with real queries). Risk board live. Cognito placeholder auth. | `[ ]` |
| 14:00–17:00 | D3-A2 | **Ankit** | Attention Model (Model 3). Robust z-score baselines (`models/attention/baseline.py`) + Isolation Forest (`models/attention/model.py`). Cold-start fallback. **Cut to plain z-score if behind schedule.** | `[ ]` |
| 14:00–17:00 | D3-B2 | **Ishaan** | Validation view in the dashboard (`templates/validation.html`). Flagged PRs with reveal mechanic showing revert commits. Precision@K summary cards. **This screen is the demo.** | `[ ]` |
| 17:00–20:00 | D3-C1 | Both | Bedrock explanation layer (`explain/bedrock_client.py`). Wire to Step Functions. Auto re-queue via SNS. Test full flow including notification delivery. | `[ ]` |
| 20:00–23:00 | D3-C2 | Both | Polish, end-to-end rehearsal on the deployed URL. Fix what breaks. Do not add features. | `[ ]` |
| 23:00 | D3-G | Both | **Gate 3 — Feature freeze** (see below) | `[ ]` |

### Gate 3 — Saturday 23:00 (Feature Freeze)

> ✅ **Pass:** deployed dashboard with a working validation view.

**If failed:** The validation view is non-negotiable — it is the demo. Cut Bedrock, cut auto re-queue, cut Model 3, and ship the validation view. Everything else becomes roadmap in the video.

**Nothing new after this point.** Only bug fixes and recording.

---

## Day 4 — Sunday 20 Sept · Validation, Video, Submission

**Goal:** Recorded 3-minute video submitted before 18:00.

| Time | ID | Owner | Task | Done |
|---|---|---|---|---|
| 09:00–11:00 | D4-1 | Both | Full validation run. Compute final Precision@K (`eval/metrics.py`). Select the 2–3 most striking individual catches — a flagged PR with a visible downstream revert commit. | `[ ]` |
| 11:00–12:00 | D4-2 | Both | Architecture diagram finalised (draw.io or Excalidraw). Video script rehearsed once end-to-end (see beat sheet below). | `[ ]` |
| 12:00–15:00 | D4-3 | Both | Record the 3-minute video. Budget 4–5 takes. Follow the beat sheet exactly. | `[ ]` |
| 15:00–17:00 | D4-4 | **Ankit** | AWS Builder Center blog post (separate prize — top 5 win Logitech keyboard). Most content already exists in `docs/`. | `[ ]` |
| 15:00–17:00 | D4-5 | **Ishaan** | README review, architecture diagram in repo, deployment verification, SageMaker endpoints confirmed warm. | `[ ]` |
| 17:00–18:00 | D4-6 | Both | **Submit.** Not at the deadline. Keep endpoints warm and URL alive through judging. | `[ ]` |

---

## Video Beat Sheet (3 minutes)

| Time | Beat | Content |
|---|---|---|
| 0:00–0:25 | The problem, in numbers | Open cold on the statistics. Review time up 441%. 31% more zero-review merges. 96% don't trust AI code; 48% verify it. No product framing yet — establish the gap first. |
| 0:25–0:50 | The insight | Everyone builds AI that reviews code. Nobody checks whether the human review was real. Introduce `residual_risk = change_risk × (1 − review_confidence)`. |
| 0:50–1:40 | The product, live | Screen recording of deployed URL. Risk board. Click into one PR: change risk, shallow review, Bedrock explanation, auto re-queue firing. |
| 1:40–2:20 | **The proof** | The strongest 40 seconds. Real repo, real history, flagged on merge-time information only — then reveal the revert commits. Say the honest Precision@K figure out loud. |
| 2:20–2:45 | Architecture | The diagram. Name Step Functions, SageMaker, EventBridge, Bedrock and say what each one does. Note scale-to-zero cost profile and read-only permission scope. |
| 2:45–3:00 | Learning and market | Name one thing neither had touched before Thursday. One line on market: GitHub Marketplace, per-seat, teams above ~20 engineers. |

---

## Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| Corpus mining overruns Day 1 and everything slips | **High** | Hard cap at 5,000 PRs, one repo. Stop at 17:00 Thursday regardless. Gate 1 forces the decision. |
| SageMaker endpoints burn credits by Saturday | **High** | Tear endpoints down overnight Thu/Fri. Smallest viable instances. Billing alarm on Day 1. |
| Change Risk AUC is mediocre (0.60–0.65) | Medium | Expected and fine. Report honestly; lean the demo on individual catches rather than aggregate metrics. |
| Depth scorer labels are noisy | Medium | Hand-verify stratified sample and report precision on it. A stated limitation earns more credit than a suspicious number. |
| Temporal leakage inflates results | Medium | Temporal split only, never random. Audit explicitly on Saturday — it is the one bug that would invalidate the whole demo. |
| Saturday in Bangalore eats the integration day | **High** | Split the team or skip. It adds nothing to the score. |
| "Isn't this surveillance?" | Medium | Anticipated by design: private-to-reviewer scoring, team-visible only at PR level. Address it unprompted in the video. |
| Scope creep into P1/P2 | Medium | Gate 3 feature freeze at Saturday 23:00. Validation view outranks every additional feature. |
| Endpoint dies during judging | Low | CloudWatch alarm; keep endpoints warm from Sunday afternoon; recorded fallback walkthrough in the video regardless. |

---

## Go/No-Go Decision Matrix

| Scenario at Gate 2 (Friday 22:00) | Decision |
|---|---|
| Both models deployed, end-to-end score working | **Full four-model path. Continue.** |
| Risk model deployed, depth scorer fails | Use a weighted comment-count proxy for depth. Continue with two SageMaker calls → one. |
| Neither model deployed | **Hard cut to Model 1 only, remove Step Functions, compute residual risk in Lambda directly.** A working system beats an aspirational one. |
| No corpus (Gate 1 failed) | **GH Archive extract.** Download pre-mined BigQuery export of GitHub events. 6 hours of data collection becomes 30 minutes. |

---

## File Checklist (P0 — must exist before submission)

### Infrastructure
- `[ ]` `infra/template.yaml` — deployed and stack stable
- `[ ]` `infra/state_machine.asl.json` — state machine running in AWS

### Corpus & Data
- `[ ]` `corpus/miner.py` — run, ≥2,000 PRs in S3
- `[ ]` `corpus/labeller.py` — run, `labels.parquet` exists
- `[ ]` `corpus/writers.py` — typed parquet files confirmed

### Features
- `[ ]` `features/precompute.py` — run, `vouch-files` DynamoDB populated
- `[ ]` `features/extractor.py` — Lambda wired to EventBridge

### Models
- `[ ]` `models/risk/train.py` — AUC logged, model artifact in S3
- `[ ]` `models/risk/inference.py` — SageMaker endpoint responding
- `[ ]` `models/depth/train.py` — F1 logged, model artifact in S3
- `[ ]` `models/depth/inference.py` — SageMaker endpoint responding

### Scoring
- `[ ]` `scoring/residual.py` — Lambda deployed, all stages tested
- `[ ]` `scoring/routing.py` — SNS dispatch confirmed

### Explain
- `[ ]` `explain/bedrock_client.py` — Bedrock call returning narration

### Evaluation
- `[ ]` `eval/retrospective.py` — batch run complete
- `[ ]` `eval/metrics.py` — Precision@K figures ready for the video

### Dashboard
- `[ ]` `dashboard/app.py` — accessible at public URL
- `[ ]` `dashboard/templates/board.html` — risk board showing real data
- `[ ]` `dashboard/templates/pr_detail.html` — PR detail loading
- `[ ]` `dashboard/templates/validation.html` — validation view with reveal mechanic working

### Documentation
- `[ ]` `README.md` — complete with quickstart and architecture
- `[ ]` `docs/prd.md`
- `[ ]` `docs/design.md`
- `[ ]` `docs/trd.md`
- `[ ]` `docs/implementation_plan.md`

---

## Judging Criteria Mapping

| Criterion | How Vouch Scores |
|---|---|
| **Idea and impact** | Backed by nine independent 2026 datasets. Not a hypothetical problem — a measured, worsening one every engineer in the room is personally living through. |
| **Built on AWS** | Twelve services, each doing real work. Step Functions orchestrates a genuine multi-stage pipeline; SageMaker serves two trained models. Cost and permission decisions are deliberate and explainable. |
| **Learning** | SZZ defect labelling, transformer fine-tuning, SageMaker endpoints, Step Functions orchestration — all genuinely new. Name the specific thing that was new in the video. |
| **Execution** | P0 set is a working deployed system. Retrospective validation proves it works on real data. Gate structure means something ships even if two of four models get cut. |
| **Demo video** | The proof segment is unusually strong: a prediction that already came true, independently verifiable by anyone who opens the repository. |
