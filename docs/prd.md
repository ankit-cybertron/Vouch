# Product Requirements Document (PRD)
## Vouch — Review Confidence Scoring & Risk-Aware Re-queuing

| | |
|---|---|
| **Product** | Vouch |
| **Version** | 1.0 (Hackathon MVP) |
| **Event** | WeMakeDevs "First Commit" — Bharat Builds Tour · Event 01 |
| **Track** | Ship It (deployed, live URL) · 17–20 September 2026 |
| **Team** | Quantified Minds — Ankit Kumar Tiwari, Ishaan Chaturvedi |
| **Status** | In Development |

---

## 1. Executive Summary

A pull request approval is a claim that someone understood the change. In 2026 that claim has quietly become unreliable: AI now writes 42% of committed code, review time is up 441%, and 31% more PRs are merging with no review at all.

**Vouch** is a GitHub App that scores how much each PR approval is actually worth, combines it with the risk of the change, and automatically re-queues merges where high risk met shallow scrutiny.

> **Positioning sentence:**
> "Everyone is building AI that reviews your code. We built the thing that checks whether the review itself can be trusted — and re-queues it when it can't."

---

## 2. Problem Statement

### 2.1 The Causal Chain

| # | Link | Evidence |
|---|---|---|
| 1 | AI dramatically increases code output per engineer | 42% of committed code is AI-authored; feature-branch throughput +59% |
| 2 | Review capacity does not scale with it | ~6.4 hrs/week/developer, unchanged |
| 3 | Consequently PRs get larger and wait longer | PR size +51%; wait 4.6–5× longer; review time +441% |
| 4 | Reviewers under load degrade in measurable ways | Decision-fatigue: incomplete comments, skipped changes past 200–400 LOC |
| 5 | The release valve is approval without real review | +31% zero-review merges; only 48% verify AI code |
| 6 | Nobody measures the gap | Existing tools measure count and latency, never depth |

### 2.2 Evidence Base

| Source | Scale | Key Finding |
|---|---|---|
| Faros AI, 2026 | 22,000 developers | Median review time **+441.5%** |
| LinearB, 2026 | 8.1M pull requests | AI PRs wait **5× longer** |
| Opsera, 2026 | 250,000+ developers | AI PRs wait **4.6× longer** in review |
| Sonar, 2026 | 1,100+ developers | **96% don't fully trust** AI code; only **48%** always verify |
| GitHub, May 2026 | Platform-wide | **1 in 5** code reviews involves an agent |
| SmartBear / Cisco | 2,500 reviews | Effectiveness degrades past **200–400 lines** per sitting |

### 2.3 The Problem in One Sentence

> Teams have lost the ability to distinguish a pull request that was genuinely reviewed from one that was merely approved — and they are making merge decisions as though the two were the same thing.

---

## 3. Product Vision & Goals

### 3.1 Vision

A GitHub App installed at organisation level that observes every pull request and every review, and produces one number per merged PR: **residual risk**.

```
residual_risk = change_risk × (1 − review_confidence)
```

- **High change risk + deep review** → acceptable
- **Low change risk + shallow review** → acceptable
- **High change risk + shallow review** → flagged and re-queued ← *the only case that matters*

### 3.2 Goals

| Goal | Metric |
|---|---|
| Score review reliability per PR | `review_confidence ∈ [0, 1]` at review submission |
| Score change risk per PR | `change_risk ∈ [0, 1]` at PR open |
| Detect high-residual-risk PRs | `residual_risk > 0.65` triggers re-queue |
| Validate retrospectively | Precision@20 on flagged historical PRs with real revert outcomes |
| Protect reviewer privacy | Individual confidence scores never visible to team |

---

## 4. Target Users & Personas

### 4.1 Personas

| Persona | Current Pain | What Vouch Gives Them |
|---|---|---|
| **The Reviewer** — senior engineer, 15 PRs in queue | No way to know which of 15 PRs deserves their two hours. Everything looks equally urgent. | Queue reordered by risk. Private nudge when their session shows degraded attention. One-click flag to request a second pair of eyes. |
| **The Author** — engineer waiting on review | PR sits for days, gets a rubber-stamp LGTM, then breaks in production. | Faster routing on low-risk changes. Genuine scrutiny where it matters. Fewer post-merge fire drills. |
| **The EM / Team** — platform or engineering manager | Knows review quality is slipping under AI volume. Has no instrument to measure it. | Residual-risk board across all merges. Automatic re-queue of dangerous ones. Trend visibility about process, not individuals. |

### 4.2 Core User Stories

| ID | Persona | Story |
|---|---|---|
| US-1 | Reviewer | As a reviewer with a full queue, I want PRs ordered by risk so I spend limited attention where it changes outcomes. |
| US-2 | Reviewer | As a reviewer, I want a private signal when my current session shows degraded depth, so I can stop before approving something I shouldn't. |
| US-3 | Reviewer | As a reviewer, I want to flag a review as low-confidence without admitting it publicly, so honesty carries no social cost. |
| US-4 | Team | As a team, I want merged PRs where high risk met shallow review surfaced automatically for a second look before an incident. |
| US-5 | Team | As a team, I want re-reviews routed to whoever has the most ownership history in those files. |
| US-6 | EM | As an EM, I want process-level trends, not individual scorecards, so the tool improves the system rather than policing people. |

---

## 5. Privacy Design

Individual review-confidence scores are **private to the reviewer**. Only PR-level residual risk is visible to the team. No per-person leaderboards, ever.

**Why this is a design decision, not a disclaimer:**
1. Without it, no engineer installs this — the product is dead on arrival.
2. It pre-empts the strongest objection: "isn't this surveillance?" The answer is addressed before it's asked.

---

## 6. Feature Scope

| Priority | Feature | Rationale |
|---|---|---|
| **P0** | GitHub App ingestion + event store | Nothing works without it |
| **P0** | Corpus mining + SZZ labelling | Critical path for every model |
| **P0** | Change Risk Model (Model 1) | Trained on public repo history |
| **P0** | Review Depth Scorer (Model 2) | Fine-tuned, SageMaker endpoint |
| **P0** | Residual risk computation + dashboard | The deployed URL |
| **P0** | Retrospective validation view | This *is* the demo — treat as P0 |
| **P1** | Reviewer Attention Model (Model 3) | Strongest differentiator; degrade to rolling z-score if time short |
| **P1** | Bedrock explanation layer | Makes the dashboard legible |
| **P1** | Auto re-queue via Step Functions | Turns a dashboard into a product |
| **P2** | Ownership routing via OpenSearch | Fall back to CODEOWNERS; cut without regret |
| **P2** | Slack integration, reviewer queue UI | Roadmap in the video instead |

---

## 7. Success Metrics

| Metric | Definition | Target |
|---|---|---|
| Precision@20 | Of the top 20 flagged PRs, how many were genuinely reverted or hotfixed? | Report honestly — even 6/20 is valid |
| AUC-ROC | Discriminative power of the change-risk model on held-out temporal split | 0.62–0.70 (expected band; published work similar) |
| End-to-end latency | Time from review submitted to residual risk persisted | < 10 seconds |
| Dashboard availability | During judging window (Sunday afternoon) | 99%+ uptime |

---

## 8. Constraints & Assumptions

- **Time:** 4 days (17–20 September 2026)
- **Data:** Public repository history only (no team's private review history required)
- **Budget:** AWS credits; SageMaker endpoints torn down overnight to control cost
- **No live demo:** Video is the sole judged artifact — treat recording as load-bearing engineering time
- **Temporal integrity:** Model trained only on merge-time information; never future labels

---

## 9. Out of Scope (v1.0)

- GitLab / Bitbucket ingestion
- Per-org model fine-tuning
- Slack bot integration
- Real-time reviewer queue UI
- GitHub Marketplace listing

---

## 10. Post-Hackathon Roadmap

| Horizon | Work |
|---|---|
| **Weeks 1–4** | Harden the GitHub App; onboard 3–5 friendly open-source repos as design partners. Replace hand-set weights with learned weights from observed outcomes. |
| **Months 2–3** | GitHub Marketplace listing. Free tier (single repo). Slack integration. |
| **Months 4–6** | First paid teams. GitLab and Bitbucket ingestion. Per-org model fine-tuning. |

### Commercial Thesis

- **Buyer:** EMs and platform teams at 20–500 engineer organisations
- **Distribution:** GitHub Marketplace — one-click install, per-seat pricing
- **Why now:** The problem didn't exist at this magnitude 18 months ago. AI-code shift is creating the category in real time.
- **Moat:** Residual-risk model improves with every revert observed across every installed organisation. Analytics dashboards never close that loop.
