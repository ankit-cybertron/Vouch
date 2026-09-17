# VOUCH
**Review confidence scoring and risk-aware re-queuing for engineering teams**

| | |
|---|---|
| **Event** | WeMakeDevs "First Commit" — Bharat Builds Tour · Event 01 |
| **Track** | Ship It (deployed, live URL) · 17–20 September 2026 |
| **Team** | Quantified Minds — Ankit Kumar Tiwari, Ishaan Chaturvedi |

---

> **Executive summary**
>
> A pull request approval is a claim that someone understood the change. In 2026 that claim has quietly become unreliable: AI now writes 42% of committed code, review time is up 441%, and 31% more PRs are merging with no review at all. Vouch is a GitHub App that scores how much each approval is actually worth, combines it with the risk of the change, and automatically re-queues merges where high risk met shallow scrutiny. It is built as an event-driven AWS system with four trained models and an LLM only at the explanation layer. The demo runs on real public-repository history and validates retrospectively: flagged PRs are shown alongside the revert commits that later proved the flag correct.

---

## Contents

1. Problem analysis
2. Competitive analysis and the unoccupied gap
3. Product definition
4. End-to-end system walkthrough
5. Data model
6. ML pipeline specification
7. AWS architecture, cost and security
8. Tech stack and repository structure
9. Feature scope (P0 / P1 / P2)
10. Four-day execution plan, hour by hour
11. Critical path and go/no-go gates
12. Demo video script
13. Mapping to judging criteria
14. Risk register
15. Post-hackathon roadmap
16. Sources

---

## 1. Problem analysis

Code review has always been the quality gate in software. It worked because code was written at human speed and read at human speed. In 2026 that symmetry broke: AI writes the code, humans still have to read it, and the review queue is where the entire delivery pipeline now jams.

| Stat | Figure | Source |
|---|---|---|
| Increase in median PR review time | **+441%** | Faros AI telemetry, 22,000 developers |
| More PRs merging with zero review | **+31%** | Not by policy — reviewers can't keep pace |
| Share of committed code attributed to AI | **42%** | Sonar 2026, 1,100+ developers |

### 1.1 The 2026 evidence base

| Source | Scale | Finding |
|---|---|---|
| **Faros AI** — "The Acceleration Whiplash", 2026 | 22,000 developers, ~4,000 teams | Median code review time up **441.5%**, even as task throughput rose 33.7%. The entire throughput gain queued up at review. |
| **LinearB** — 2026 benchmark | 8.1M pull requests, 4,800+ orgs | Developers *feel* 20% faster but measure 19% slower — a 39-point perception gap. AI-assisted PRs run ~2.5× larger and wait roughly 5× longer for a reviewer. |
| **Opsera** — 2026 enterprise benchmark | 250,000+ developers, 60+ enterprises | AI-generated PRs wait **4.6× longer** in review, even as time-to-PR dropped by up to 58%. |
| **Sonar** — State of Code Survey 2026 | 1,100+ professional developers | AI accounts for 42% of committed code, expected to reach 65% by 2027. **96% don't fully trust** AI-generated code to be functionally correct — yet **only 48%** always verify before committing. |
| **GitHub** — Review telemetry, May 2026 | Platform-wide | Copilot code review processed 60M+ reviews; more than **one in five** code reviews on GitHub now involves an agent. Agent PRs are multiplying faster than human review capacity. |
| **CircleCI** — 2026 throughput data | Platform-wide | Feature-branch throughput up 59% year over year, while *main-branch* throughput for the median team actually **fell**. More code in, less code shipped. |
| **GitLab** — Developer survey | Industry survey | Code review ranks as the **third-largest contributor to developer burnout**, behind only long hours and tight deadlines. |
| **Academic** — arXiv 2407.01407 | Study + prototypes | Directly studies *decision fatigue* in code review. Confirms two failure modes: incomplete comments, and skipping changes that are short, large, or complex. Mechanism documented; no product addresses it. |
| **SmartBear / Cisco** — Case study | 2,500 reviews | Review effectiveness degrades sharply beyond **200–400 lines** per sitting. PR sizes have since grown 51%. |
| **Google** — Engineering productivity | Internal research | Developers spend an average of **6.4 hours per week** on code review activity — a large, fixed cost now spread across far more code. |

### 1.2 The causal chain

| # | Link | Evidence |
|---|---|---|
| 1 | AI dramatically increases code output per engineer | 42% of committed code is AI-authored; feature-branch throughput +59% |
| 2 | Review capacity does not scale with it — it is human hours, and it is fixed | ~6.4 hrs/week/developer, unchanged |
| 3 | Consequently PRs get larger and wait longer | PR size +51%; wait 4.6–5× longer; review time +441% |
| 4 | Reviewers under sustained load degrade in measurable, specific ways | Decision-fatigue study: incomplete comments, skipped changes; effectiveness falls past 200–400 LOC |
| 5 | The release valve is approval without real review | +31% zero-review merges; only 48% verify AI code |
| 6 | Nobody measures the gap between a real review and a nominal one | Existing tools measure count and latency, never depth — see §2 |

> **The problem, in one sentence**
>
> Teams have lost the ability to distinguish a pull request that was genuinely reviewed from one that was merely approved — and they are making merge decisions as though the two were the same thing.

---

## 2. Competitive analysis and the unoccupied gap

Two mature categories sit adjacent to this problem. Neither occupies it.

| Category | Representative products | What they do | Why the gap remains |
|---|---|---|---|
| **AI code reviewers** | GitHub Copilot Code Review, CodeRabbit, Cursor Bugbot, Qodo | Read the diff and post automated review comments | They review *the code*, adding another opinion to an already-saturated queue. They never assess whether the *human* review was adequate. An AI approving AI-written code is two machines agreeing, with no accountability signal at all. |
| **Engineering analytics** | LinearB, Haystack, Opsera, minware, CodeScene, Count.co | Dashboards of cycle time, PR counts, comment counts, reviewer load | Retrospective, team-level, built for managers reviewing a quarter. Not per-reviewer, not real-time, and critically they **take no action**. A number on a dashboard does not stop a bad merge. |
| **Static analysis / CI** | SonarQube, Semgrep, CodeQL | Rule-based defect and vulnerability detection | Orthogonal. Catches known patterns, blind to review process entirely. A PR can pass every gate and still have been merged by someone who never opened the diff. |

### 2.1 The unoccupied space

No product currently does all three of the following:

1. Scores an **individual review's reliability** at the moment it happens, using the reviewer's own historical baseline rather than a global standard.
2. Multiplies that against the **risk of the change itself** to produce a single residual-risk figure.
3. **Acts on it** — routing high-residual merges back for a second look, automatically, to the right person.

The academic literature has established the failure mode. The tooling market has built measurement without a control loop. Closing that loop is the entire opportunity.

> **Positioning sentence for judges**
>
> "Everyone is building AI that reviews your code. We built the thing that checks whether the review itself can be trusted — and re-queues it when it can't."

---

## 3. Product definition

Vouch is a GitHub App installed at organisation level. It observes every pull request and every review, and produces one number per merged PR: **residual risk**.

> **The core equation**
>
> `residual_risk = change_risk × (1 − review_confidence)`
>
> A risky change with a deep review is fine. A trivial change with a shallow review is fine. A risky change with a shallow review is the case nobody currently detects — and it is the only case that matters.

### 3.1 Personas and what each one gets

| Persona | Their current pain | What Vouch gives them |
|---|---|---|
| **The reviewer** — senior engineer, 15 PRs in queue | No way to know which of 15 PRs deserves the two hours they have. Everything looks equally urgent. | Queue reordered by risk. A private nudge when their current session shows degraded attention. A one-click "flag this for a second pair of eyes" when they know they skimmed. |
| **The author** — engineer waiting on review | PR sits for days, then gets an LGTM that catches nothing, then breaks in production and they own it. | Faster routing on low-risk changes. Genuine scrutiny where it matters. Fewer post-merge fire drills. |
| **The team / EM** — platform or eng manager | Knows review quality is slipping under AI volume. Has no instrument that measures it, so cannot manage it. | Residual-risk board across all merges. Automatic re-queue of the dangerous ones. Trend visibility that is about process, not individuals. |

### 3.2 Core user stories

| ID | Persona | Story |
|---|---|---|
| US-1 | Reviewer | As a reviewer with a full queue, I want my PRs ordered by risk so I spend my limited attention where it changes outcomes. |
| US-2 | Reviewer | As a reviewer, I want a private signal when my current session shows degraded depth, so I can stop before I approve something I shouldn't. |
| US-3 | Reviewer | As a reviewer, I want to flag a review as low-confidence without admitting it publicly, so honesty carries no social cost. |
| US-4 | Team | As a team, I want merged PRs where high risk met shallow review surfaced automatically, so they get a second look before an incident. |
| US-5 | Team | As a team, I want the re-review routed to whoever has the most ownership history in those files, so it lands with someone who can actually judge it. |
| US-6 | EM | As an EM, I want process-level trends, not individual scorecards, so the tool improves the system rather than policing people. |

### 3.3 Privacy stance — a design decision, not a disclaimer

Individual review-confidence scores are **private to the reviewer**. Only PR-level residual risk is visible to the team. No per-person leaderboards, ever. Two reasons, and both should be stated aloud in the demo: first, without it no engineer installs this and the product is dead on arrival; second, judges will think "isn't this surveillance?" within thirty seconds, and answering before they ask converts the strongest objection into a considered design choice.

---

## 4. End-to-end system walkthrough

The complete life of a single pull request through Vouch, from the moment it opens to the moment a re-review is routed. Latency figures are targets, not measurements.

**Step 1 — PR opened** `t = 0s`

GitHub fires `pull_request.opened` to the App webhook. API Gateway receives it, verifies the HMAC signature, and hands to the ingest Lambda. The Lambda writes a normalised event to DynamoDB (`vouch-events`) and drops the raw diff to S3 under `raw/{org}/{repo}/{pr}/diff.patch`. It emits `PRIngested` onto EventBridge and returns 202 immediately — webhook handlers must never block on model inference.

**Step 2 — Feature extraction** `t + ~2s`

EventBridge triggers the feature Lambda. It computes the change-risk feature vector: diff size, files touched, complexity delta, 90-day churn of each touched file, ownership concentration, test-coverage delta, path sensitivity flags, author tenure in those paths, AI-authorship signals. File-history features come from a pre-computed table in DynamoDB rather than live git traversal — this is the difference between a 2-second and a 40-second path.

**Step 3 — Change risk scored** `t + ~3s` `MODEL 1`

The vector goes to the SageMaker risk endpoint. Returns `change_risk ∈ [0,1]`, plus the top three contributing features for explainability. Written back to `vouch-prs`. The PR now has a risk score before a human has looked at it — which is what makes queue reordering possible.

**Step 4 — Queue reordering** `t + ~4s`

Each candidate reviewer's queue view is recomputed and cached. The dashboard reads from this cache; it never recomputes on page load.

**Step 5 — Human reviews it** `minutes to days`

The reviewer works in GitHub as normal. Vouch changes nothing about their workflow. Every `pull_request_review` and `pull_request_review_comment` event streams in, capturing comment text, file and line anchors, and precise timestamps. Time-on-review is derived from the interval between first interaction and submission, clipped to exclude implausible idle gaps.

**Step 6 — Review depth scored** `on review submit + ~2s` `MODEL 2`

Each comment goes to the depth-scorer endpoint and is classified: *rubber-stamp, nit/style, clarifying question, logic concern, architecture concern, security concern*. The review's depth score is a weighted aggregate — a single security concern outweighs six style nits. A review with zero comments scores at floor, regardless of how long it took.

**Step 7 — Attention state** `+ ~1s` `MODEL 3`

The reviewer's rolling baseline is fetched from DynamoDB. Current review is compared against *their own* distribution on seconds-per-KLOC, comment density and depth distribution, with session context layered on (consecutive reviews, elapsed session, hour of day). Output `attention_state ∈ [0,1]`. The baseline is then updated with this review.

**Step 8 — Review confidence and residual risk** `+ ~1s`

`review_confidence = f(depth_score, time_adequacy, attention_state, reviewer_file_familiarity)`, then `residual_risk = change_risk × (1 − review_confidence)`. Persisted to `vouch-prs`. Step Functions orchestrates steps 6–8 so a single endpoint failure retries rather than silently dropping the PR.

**Step 9 — Explanation generated** `+ ~3s` `BEDROCK`

Scores and top contributing features are passed to Bedrock, which returns one human sentence: *"Approved in 90s on a 600-line diff in `billing/retry.py` — a file with 11 reverts in 12 months — by a reviewer 7 reviews into a session. Recommend a second look at the retry-backoff logic."* Bedrock narrates; it never scores.

**Step 10 — Routing decision** `+ ~2s`

If `residual_risk > threshold`, Step Functions opens a follow-up review request. The re-reviewer is selected by ownership history over the touched files, resolved through an OpenSearch similarity query across past PRs. Notification goes out via SNS. Below threshold, the PR closes out silently and contributes only to the trend data.

**Step 11 — Outcome feedback** `days to weeks`

When a revert or hotfix later touches the same lines, Vouch links it back to the originating PR. This is the label that retrains the risk model — **the system gets better at exactly the thing it was wrong about**. It is also the mechanism behind the long-term data moat in §15.

---

## 5. Data model

### 5.1 DynamoDB tables

| Table | Key | Contents |
|---|---|---|
| `vouch-prs` | PK `repo#pr` | PR metadata, change_risk, review_confidence, residual_risk, state, outcome label once known |
| `vouch-reviews` | PK `repo#pr`, SK `reviewer#ts` | Per-review record: comments, depth classifications, timing, attention_state at submission |
| `vouch-baselines` | PK `reviewer` | Rolling per-reviewer distributions, session counters, last-updated watermark |
| `vouch-files` | PK `repo#path` | Pre-computed file history: churn, revert count, ownership distribution, defect density |
| `vouch-events` | PK `repo#pr`, SK `ts` | Raw normalised event log — replay source if the pipeline needs re-running |

> `vouch-files` is the one that makes the latency budget work. Computing file history live per PR would dominate the critical path; pre-computing it on a schedule reduces step 2 to a lookup.

### 5.2 S3 layout

```
s3://vouch-{env}/
  raw/{org}/{repo}/{pr}/diff.patch        # raw diffs
  corpus/{repo}/prs.parquet               # mined training corpus
  corpus/{repo}/reviews.parquet
  corpus/{repo}/labels.parquet            # SZZ-derived defect labels
  features/{repo}/risk_features.parquet   # materialised training features
  models/risk/{version}/model.tar.gz      # SageMaker artifacts
  models/depth/{version}/model.tar.gz
```

---

## 6. ML pipeline specification

Six stages. Four involve real models. The LLM appears only at stage 6 — this is deliberate, and worth stating explicitly to judges, because the majority of hackathon submissions are the inverse.

### 6.1 Model 1 — Change Risk `GRADIENT BOOSTING`

**Task:** predict `P(this change causes a defect)` using only information available at merge time.
**Algorithm:** XGBoost or LightGBM, binary classification, ~200 trees, depth 4–6. Deliberately shallow — heavy tuning is a poor use of hackathon hours.

| Feature group | Features |
|---|---|
| Change size | lines added, lines removed, files touched, hunks, max hunk size |
| Complexity | cyclomatic complexity delta, nesting depth delta, new function count |
| File history | 90-day churn, lifetime revert count, defect density, age since last rewrite |
| Ownership | ownership concentration (Gini over authors), author's prior commits in these files |
| Path sensitivity | touches auth / payment / migration / config / crypto paths (regex flags) |
| Testing | test files touched, test-to-source ratio, coverage delta if available |
| Provenance | AI-authorship signals: commit message patterns, diff-shape heuristics, bot co-author trailers |

**Labels — SZZ-style:** identify commits that were later reverted, or whose lines were modified by a commit whose message matches hotfix/bugfix patterns; trace back via `git blame` to the introducing commit; label its originating PR positive. Fully automatable over public repo history.

**Expected AUC 0.62–0.70.** Report it honestly — this is a genuinely hard prediction problem and published work lands in a similar band. A real 0.65 is worth more to this audience than a fabricated 0.95.

### 6.2 Model 2 — Review Depth Scorer `TRANSFORMER FINE-TUNE`

**Task:** classify each review comment into six depth classes.
**Architecture:** DistilBERT or MiniLM fine-tuned — small enough to train in two to three hours on a single GPU instance, and to serve cheaply from a SageMaker endpoint.

| Class | Weight | Example |
|---|---|---|
| Rubber-stamp | 0.0 | "LGTM", "ship it", bare approval with no body |
| Nit / style | 0.15 | naming, formatting, import order |
| Clarifying question | 0.45 | "why is this retried three times?" |
| Logic concern | 0.80 | "this is off-by-one when the list is empty" |
| Architecture concern | 0.90 | "this couples the scheduler to the storage layer" |
| Security concern | 1.00 | "this path is reachable with an unvalidated token" |

**Labelling:** bootstrap several thousand real review comments with an LLM, then hand-verify a stratified ~300-comment sample and report precision on that sample. Stating the verified number and the sample size reads as rigour; stating an unverified 95% reads as noise.

### 6.3 Model 3 — Reviewer Attention `TIME SERIES / ANOMALY`

**Task:** detect deviation from a reviewer's own established behaviour.
**Method:** robust z-score (median and MAD, not mean and SD — review times are heavily right-skewed) over a rolling 30-review window per reviewer, across seconds-per-KLOC, comment density, and mean depth score. An isolation forest layered on top catches multivariate deviation that no single axis shows. Session context — consecutive review count, elapsed session minutes, hour of day — enters as additional features.

**Cold start:** fewer than 10 prior reviews means no personal baseline exists. Fall back to a repo-level distribution and mark the score as low-confidence rather than fabricating a personal one.

### 6.4 Model 4 — Ownership routing `EMBEDDINGS`

Past PRs embedded and indexed in OpenSearch. Given a PR needing re-review, nearest-neighbour search over file-path and diff-content embeddings surfaces the engineers with the deepest relevant history. P2 scope — cut without regret if time runs short, and fall back to a simple CODEOWNERS lookup.

### 6.5 Stage 5 — Residual risk composition `DETERMINISTIC`

Not a model, but the product's core logic and the thing worth explaining most carefully in the video. Review confidence is a weighted composition of depth score, time adequacy relative to diff size, attention state, and reviewer familiarity with the touched files. Weights are hand-set for the hackathon and explicitly flagged as such — pretending they were learned would be the one dishonest claim in an otherwise honest system.

### 6.6 Stage 6 — Explanation `BEDROCK`

Takes the numeric outputs plus top contributing features and returns a single sentence a human can act on. Constrained by a tight prompt template with structured JSON input so it narrates the numbers rather than inventing its own assessment. Bedrock is the last mile, never the engine.

### 6.7 Training data — the unlock

> **You do not need your own team's review history.**
>
> Public repositories already contain years of it, with ground truth attached. LLVM, Kubernetes, Rust and React have millions of reviews, and every revert and hotfix commit sits in the history as a free label. This is what turns "you can't build a baseline in four days" into "you have hundreds of thousands of labelled rows by Thursday evening."

### 6.8 Retrospective validation — the demo that wins

Point Vouch at a real repository's history. Have it flag PRs as high-residual-risk using *only* information available at merge time. Then reveal which of those were in fact later reverted or hotfixed.

**This is not a demo of a prediction. It is a demo of a prediction that already came true** — on real data, on a repository the judges recognise, with the revert commit visible on screen as proof. Almost nothing else on the leaderboard will have external validation of any kind.

**Metric:** precision@K on flagged merges. Of the top 20 PRs Vouch flagged, how many were genuinely reverted or hotfixed within N days? Report the real figure even if it is 6/20.

---

## 7. AWS architecture, cost and security

### 7.1 Services and their roles

| Service | Role | Why it is load-bearing |
|---|---|---|
| **API Gateway + Lambda** | Webhook ingestion | Bursty and event-driven; scales to zero between PRs. Returns 202 fast, never blocks on inference. |
| **EventBridge** | Event routing | Fans one PR event into risk scoring, depth scoring and baseline update independently |
| **Step Functions** | Scoring orchestration | Multi-stage pipeline with retries, partial failure and timeouts — precisely its use case |
| **SageMaker** | Train and serve models 1–3 | Training jobs over the corpus; two live inference endpoints |
| **DynamoDB** | Event store, PR state, baselines, file history | Single-digit-ms reads on the hot path; the pre-computed file table keeps step 2 fast |
| **S3** | Diffs, corpus, features, model artifacts | Everything large and immutable |
| **OpenSearch** | Ownership and similarity routing | Vector search over PR history to answer "who should re-review this" |
| **Bedrock** | Explanation layer | Stage 6 only |
| **Cognito** | Dashboard auth | GitHub OAuth federation |
| **Amplify Hosting** | Dashboard | The URL handed to judges |
| **SNS** | Re-queue notifications | Slack and email delivery |
| **CloudWatch** | Logs, metrics, alarms | Endpoint health during judging — a dead endpoint on Sunday night is an avoidable loss |

### 7.2 Cost profile

| Component | Hackathon-scale | Note |
|---|---|---|
| Lambda + API Gateway | Effectively free | Thousands of invocations sit inside free tier |
| DynamoDB | Low single-digit $ | On-demand; small tables |
| S3 | < $1 | A few GB of corpus and artifacts |
| SageMaker training | $5–15 | A few hours of GPU for the transformer fine-tune; XGBoost trains on CPU |
| SageMaker endpoints | $20–40 | **Dominant cost.** Two endpoints running continuously. Use the smallest viable instance; consider serverless inference. |
| OpenSearch | $15–25 | Smallest cluster. First thing to cut if credits run tight. |
| Bedrock | < $5 | One short completion per scored PR |

> ⚠️ **Cost discipline:** Always-on SageMaker endpoints are the standard way hackathon teams burn credits by Saturday. Tear endpoints down overnight Thursday and Friday; bring them up Saturday and leave them warm through judging. Set a CloudWatch billing alarm on day one, not day three.

### 7.3 Security and permissions

- GitHub App requests **read-only** scopes on code and pull requests, plus write on review requests. Nothing else.
- Webhook HMAC signature verified in the ingest Lambda before any processing.
- Raw diffs encrypted at rest in S3; lifecycle policy expires them after the scoring window.
- Per-reviewer confidence scores stored under the reviewer's own partition and never exposed through the team-level API.
- Least-privilege IAM per Lambda. No wildcard resource policies, even for a hackathon — judges with AWS backgrounds do look.

---

## 8. Tech stack and repository structure

| Layer | Choice |
|---|---|
| Ingest / services | Python 3.12 Lambdas, AWS SAM for local dev and deploy |
| ML — risk model | XGBoost, scikit-learn, pandas, PyArrow |
| ML — depth scorer | PyTorch + HuggingFace Transformers (DistilBERT) |
| Corpus mining | GitHub GraphQL API, PyGithub, GitPython for blame traversal |
| Orchestration | AWS Step Functions (ASL definitions in repo) |
| Dashboard | React + Vite, Tailwind, Recharts; deployed via Amplify Hosting |
| IaC | SAM templates checked in — reproducibility is part of the score |

```
vouch/
  infra/            # SAM templates, Step Functions ASL, IAM policies
  ingest/           # webhook Lambda, event normalisation
  features/         # feature extraction, file-history precompute
  corpus/           # GraphQL miner, SZZ labeller, parquet writers
  models/
    risk/           # XGBoost training + inference handler
    depth/          # DistilBERT fine-tune + inference handler
    attention/      # rolling baseline, robust z-score, isolation forest
  scoring/          # residual risk composition, routing decision
  explain/          # Bedrock prompt templates + client
  dashboard/        # React app
  eval/             # retrospective validation, precision@K reporting
  README.md
```

---

## 9. Feature scope

| Priority | Feature | Note |
|---|---|---|
| **P0** — must ship | GitHub App ingestion + event store | Nothing works without it |
| **P0** | Corpus mining + SZZ labelling | Critical path for every model |
| **P0** | Change Risk Model (Model 1) | Trained on public repo history |
| **P0** | Review Depth Scorer (Model 2) | Fine-tuned, SageMaker endpoint |
| **P0** | Residual risk computation + dashboard | The deployed URL |
| **P0** | Retrospective validation view | This *is* the demo — treat as P0, not polish |
| **P1** — if time | Reviewer Attention Model (Model 3) | Strongest differentiator; degrade to a plain rolling z-score if hours run short |
| **P1** | Bedrock explanation layer | Makes the dashboard legible; genuinely fast to add |
| **P1** | Auto re-queue via Step Functions | Turns a dashboard into a product |
| **P2** — stretch | Ownership routing via OpenSearch | Fall back to CODEOWNERS; cut without regret |
| **P2** | Slack integration, reviewer queue UI | Describe as roadmap in the video instead |

---

## 10. Four-day execution plan

> ⚠️ **Scheduling conflict — decide before Thursday**
>
> The optional Bangalore day is Saturday 19 Sept, 8 AM–8 PM. That is Day 3 of 4, and Day 3 is when deployment and integration happen. Attending costs roughly a full working day at the worst possible moment, and the rules state plainly that being there adds nothing to your score. **Recommendation:** if you attend, one person goes and the other holds the build. Do not both go.

### Day 1 — Thursday 17 Sept · Data and skeleton

| Time | ID | Owner | Task |
|---|---|---|---|
| 08:00–09:00 | D1-0 | Both | Kickoff. Repo scaffolded, AWS credits redeemed, Builder Center profiles confirmed, GitHub App registered, CloudWatch billing alarm set. |
| 09:00–13:00 | D1-A1 | **Ankit** | Corpus miner: pull PR, review and comment history via GitHub GraphQL for the target repo. Write parquet to S3. **Hard cap: 5,000 PRs.** |
| 09:00–13:00 | D1-B1 | **Ishaan** | AWS skeleton: API Gateway → Lambda → EventBridge → DynamoDB via SAM. Tables created. Webhook signature verification working. |
| 14:00–17:00 | D1-A2 | **Ankit** | SZZ labeller: revert and hotfix detection, blame traversal to introducing commit, emit labels parquet. |
| 14:00–17:00 | D1-B2 | **Ishaan** | End-to-end webhook test against a scratch repo. Amplify placeholder deployed — the public URL exists from day one. |
| 17:00–20:00 | D1-A3 | **Ankit** | Feature extraction v1: change size, complexity, file history, path sensitivity. Materialise the training feature table. |
| 17:00–20:00 | D1-B3 | **Ishaan** | Comment sampling for depth-scorer labelling; LLM bootstrap labelling pipeline running. |
| 20:00–21:00 | D1-G | Both | **Gate 1.** See §11. |

### Day 2 — Friday 18 Sept · Models

| Time | ID | Owner | Task |
|---|---|---|---|
| 09:00–13:00 | D2-A1 | **Ankit** | Train Change Risk Model. Baseline XGBoost, honest AUC on a held-out temporal split — never a random split, it leaks future information. |
| 09:00–13:00 | D2-B1 | **Ishaan** | Hand-verify the 300-comment stratified sample. Fine-tune DistilBERT. Record verified precision per class. |
| 14:00–17:00 | D2-A2 | **Ankit** | Deploy risk model to SageMaker endpoint. Inference handler + smoke tests. |
| 14:00–17:00 | D2-B2 | **Ishaan** | Deploy depth scorer to second endpoint. Feature-extraction Lambda wired to live events. |
| 17:00–20:00 | D2-C1 | Both | Step Functions state machine: call both endpoints, compose residual risk, write to DynamoDB. |
| 20:00–22:00 | D2-C2 | Both | Debug the full path. **First end-to-end residual risk score on a real PR.** |
| 22:00 | D2-G | Both | **Gate 2 — the decisive checkpoint.** See §11. |

### Day 3 — Saturday 19 Sept · Integration and the validation view

| Time | ID | Owner | Task |
|---|---|---|---|
| 09:00–13:00 | D3-A1 | **Ankit** | Retrospective validation engine. Batch-score historical PRs at merge-time information only; join against actual revert outcomes; compute precision@K. |
| 09:00–13:00 | D3-B1 | **Ishaan** | Dashboard: residual-risk board, per-PR detail view. Cognito auth via GitHub OAuth. |
| 14:00–17:00 | D3-A2 | **Ankit** | Attention Model (Model 3). Robust z-score baselines, isolation forest, cold-start fallback. **Cut to plain z-score if behind schedule.** |
| 14:00–17:00 | D3-B2 | **Ishaan** | Validation view in the dashboard — flagged PRs with revert outcomes revealed. This screen is the demo. |
| 17:00–20:00 | D3-C1 | Both | Bedrock explanation layer. Auto re-queue via Step Functions + SNS. |
| 20:00–23:00 | D3-C2 | Both | Polish, end-to-end rehearsal on the deployed URL, fix what breaks. |
| 23:00 | D3-G | Both | **Gate 3 — feature freeze.** Nothing new after this point. |

### Day 4 — Sunday 20 Sept · Validation, video, submission

| Time | ID | Owner | Task |
|---|---|---|---|
| 09:00–11:00 | D4-1 | Both | Full validation run. Compute final precision@K. Select the two or three most striking individual catches — a flagged PR with a visible downstream revert is the money shot. |
| 11:00–12:00 | D4-2 | Both | Architecture diagram finalised. Video script rehearsed once end to end. |
| 12:00–15:00 | D4-3 | Both | Record the 3-minute video. Budget 4–5 takes. §12 has the beat sheet. |
| 15:00–17:00 | D4-4 | **Ankit** | AWS Builder Center blog post — separate prize, top 5 win a Logitech keyboard. Most of the content already exists in this document. |
| 15:00–17:00 | D4-5 | **Ishaan** | README, architecture diagram in repo, deployment verification, endpoint warm-up. |
| 17:00–18:00 | D4-6 | Both | **Submit.** Not at the deadline. Keep endpoints warm and the URL alive through judging. |

---

## 11. Critical path and go/no-go gates

### 11.1 Dependency chain

```
D1-A1 corpus ──► D1-A2 labels ──► D1-A3 features ──► D2-A1 risk model ──┐
                                                                         ├──► D2-C1 Step Functions ──► D3-A1 validation ──► D4-1 metrics ──► D4-3 video
D1-B1 skeleton ──► D1-B3 comment labels ──► D2-B1 depth model ──────────┘

D1-B2 Amplify placeholder ──► D3-B1 dashboard ──► D3-B2 validation view ──► D4-3 video
```

**The critical path runs through the corpus.** If D1-A1 slips, every model slips, validation slips, and the video loses its strongest segment. This is why the 5,000-PR cap is not a suggestion.

### 11.2 Gates

> ✅ **Gate 1 — Thursday 21:00**
>
> **Pass:** labelled corpus in S3, webhook events flowing to DynamoDB, live Amplify URL.
> **If failed:** drop to a single repo and a 2,000-PR cap. Do not attempt a second repo. Consider substituting a public GH Archive extract for live GraphQL mining.

> ✅ **Gate 2 — Friday 22:00 · the decisive one**
>
> **Pass:** one real PR scored end to end — change risk, depth, residual risk, persisted.
> **If failed:** cut Model 3 entirely, cut OpenSearch, and run residual risk from Models 1 and 2 only. A two-model system that works beats a four-model system that doesn't. Decide this Friday night, not Sunday morning.

> ✅ **Gate 3 — Saturday 23:00 · feature freeze**
>
> **Pass:** deployed dashboard with a working validation view.
> **If failed:** the validation view is non-negotiable — it is the demo. Cut Bedrock, cut auto re-queue, cut the attention model, and ship the validation view. Everything else becomes roadmap in the video.

---

## 12. Demo video script — 3 minutes

| Time | Beat | Content |
|---|---|---|
| **0:00–0:25** | The problem, in numbers | Open cold on the statistics. Review time up 441%. 31% more PRs merging with zero review. 96% of developers don't trust AI code; 48% verify it. No product framing yet — establish the gap first. |
| **0:25–0:50** | The insight | Everyone is building AI that reviews code. Nobody checks whether the human review was real. Introduce residual risk: `change_risk × (1 − review_confidence)`. |
| **0:50–1:40** | The product, live | Screen recording of the deployed URL. Residual-risk board. Click into one PR: change risk, the shallow review, the Bedrock explanation, the auto re-queue firing. |
| **1:40–2:20** | **The proof** | The strongest forty seconds available. Real repo, real history, flagged on merge-time information only — then reveal the revert commits. Say the honest precision@K figure out loud. |
| **2:20–2:45** | Architecture | The diagram. Name Step Functions, SageMaker, EventBridge, Bedrock and say what each one does. Note the scale-to-zero cost profile and the read-only permission scope. |
| **2:45–3:00** | Learning and market | Name one specific thing neither of you had touched on Thursday. Then one line on the market: GitHub Marketplace app, per-seat, teams above ~20 engineers. |

> There is no live demo at this event, so the video is the entire judged artifact. Treat Sunday's three hours of recording as load-bearing engineering time, not an afterthought.

---

## 13. Mapping to judging criteria

| Criterion | How Vouch scores |
|---|---|
| **Idea and impact** | Backed by nine independent 2026 datasets covering hundreds of thousands of developers. Not a hypothetical problem but a measured, worsening one — and one every engineer in the room is personally living through, which makes it land without explanation. |
| **Built on AWS** | Twelve services, each doing real work. Step Functions orchestrates a genuine multi-stage pipeline; SageMaker serves two trained models. Event-driven by nature, not retrofitted onto a request-response app. Cost and permission decisions are deliberate and explainable. |
| **Learning** | SZZ defect labelling, transformer fine-tuning, SageMaker endpoints, Step Functions orchestration — all genuinely new relative to compiler and signal-processing work. Name the specific thing that was new. |
| **Execution** | The P0 set is a working deployed system. Retrospective validation proves it works on real data rather than a staged happy path. Gate structure means something ships even if two of four models get cut. |
| **Demo video** | The proof segment is unusually strong: a prediction that already came true, independently verifiable by anyone who opens the repository. |

---

## 14. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Corpus mining overruns Day 1 and everything slips | **High** | Hard cap at 5,000 PRs, one repo. Stop pulling at 17:00 Thursday regardless of completeness. Gate 1 forces the decision. |
| SageMaker endpoint costs burn the credits by Saturday | **High** | Tear endpoints down overnight Thu/Fri. Smallest viable instances. Billing alarm on day one. |
| Change Risk AUC is mediocre (0.60–0.65) | Medium | Expected and fine. Report honestly; lean the demo on individual catches rather than aggregate metrics. A weak-but-real model beats a strong-but-fake one with this audience. |
| Depth scorer labels are noisy | Medium | Hand-verify the stratified sample and report precision on it. A stated limitation earns more credit than a suspicious number. |
| Temporal leakage inflates results | Medium | Temporal split only, never random. Feature extraction must use merge-time information exclusively. Audit this explicitly on Saturday — it is the one bug that would invalidate the whole demo. |
| Saturday in Bangalore eats the integration day | **High** | Split the team or skip. It adds nothing to the score. |
| "Isn't this surveillance?" | Medium | Anticipated by design: private-to-reviewer scoring, team-visible only at PR level. Address it unprompted in the video. |
| Scope creep into P1/P2 | Medium | Gate 3 feature freeze at Saturday 23:00. The validation view outranks every additional feature. |
| Endpoint dies during judging | Low | CloudWatch alarm; keep endpoints warm from Sunday afternoon onward; a recorded fallback walkthrough exists in the video regardless. |

---

## 15. Post-hackathon roadmap

| Horizon | Work |
|---|---|
| **Weeks 1–4** | Harden the GitHub App; onboard 3–5 friendly open-source repos as design partners. Replace hand-set residual-risk weights with weights learned from observed outcomes. |
| **Months 2–3** | GitHub Marketplace listing. Free tier scoring a single repo as the acquisition wedge. Slack integration for re-queue notifications. |
| **Months 4–6** | First paid teams. GitLab and Bitbucket ingestion. Per-org model fine-tuning on that org's own revert history. |

### 15.1 Commercial thesis

- **Buyer:** engineering managers and platform teams at 20–500 engineer organisations — large enough to have a review bottleneck, too small to have built internal tooling for it.
- **Distribution:** GitHub Marketplace. One-click install, per-seat pricing, free single-repo tier as the wedge.
- **Why now:** the problem did not exist at this magnitude eighteen months ago. The category is being created in real time by the AI-code shift, which is precisely the window in which a small team can take a position.
- **Moat:** the residual-risk model improves with every revert it observes across every installed organisation. Incumbent analytics dashboards never close that loop, so they accumulate no comparable signal.

---

## 16. Sources

**Sources for §1.** Faros AI, "The Acceleration Whiplash" (2026), telemetry across 22,000 developers / ~4,000 teams. LinearB 2026 benchmark, 8.1M pull requests across 4,800+ organisations. Opsera 2026 enterprise benchmark, 250,000+ developers across 60+ organisations. Sonar, State of Code Developer Survey 2026, 1,100+ developers. GitHub Copilot code review telemetry, May 2026. CircleCI 2026 throughput data. GitLab developer survey on burnout contributors. arXiv 2407.01407, "Towards debiasing code review support." SmartBear / Cisco case study of 2,500 reviews. Google engineering productivity research on review hours per developer per week.

**Caveat.** Several figures were reported through secondary industry coverage rather than obtained from the primary reports. Verify every headline number against the original publication before it goes on a slide or into the video — a judge who checks one and finds it misstated will discount all of them.