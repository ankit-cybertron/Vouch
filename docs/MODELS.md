# Vouch — Model Architecture & Machine Learning Ensemble

Vouch employs a multi-model ensemble combining gradient-boosted decision trees, fine-tuned transformer NLP, robust non-parametric statistics, and unsupervised anomaly detection to assess risk on every pull request.

---

## 1. Model 1: Change Risk Model (XGBoost)

### Purpose
Predicts the prior defect probability of a pull request based purely on its diff, affected file histories, sensitive paths, and authorship signals:

$$R_{change} \in [0.05, 0.95]$$

### Feature Schema (28 Features)
| Category | Feature Name | Type | Description |
|---|---|---|---|
| **Diff Sizing** | `lines_added` | int | Total added lines |
| | `lines_removed` | int | Total deleted lines |
| | `net_delta` | int | Net line changes (`lines_added - lines_removed`) |
| | `total_changed_lines` | int | Sum of added and deleted lines |
| | `files_touched` | int | Count of modified files |
| | `hunk_count` | int | Total distinct diff hunks |
| | `max_hunk_size` | int | Largest single hunk in lines |
| **History & Churn** | `total_churn_90d` | int | Cumulative line churn across touched files in last 90 days |
| | `total_revert_count` | int | Number of times touched files were reverted in last 90 days |
| | `mean_defect_density` | float | Historical post-merge bugs per line of touched files |
| | `mean_ownership_gini` | float | Gini inequality coefficient of author contributions |
| **Sensitive Paths** | `path_auth` | binary | Touches authentication/session/login code |
| | `path_payment` | binary | Touches billing/stripe/checkout code |
| | `path_migration` | binary | Touches database migrations/DDL scripts |
| | `path_crypto` | binary | Touches TLS/cryptographic primitives/secrets |
| | `path_config` | binary | Touches configuration or `.env` templates |
| | `path_infra` | binary | Touches Terraform/Kubernetes/Dockerfiles |
| | `path_audit` | binary | Touches compliance/audit/policy files |
| | `path_credentials` | binary | Touches private keys/credentials |
| | `path_iac` | binary | Touches Pulumi/CDK/Ansible files |
| | `path_testing` | binary | Touches unit/integration test suites |
| | `path_sensitive_path_count` | int | Total number of sensitive path categories touched |
| **Authorship & AI** | `author_prior_commits_in_files`| int | Author's past commits in touched files |
| | `ai_commit_signal` | binary | AI co-author tag (Copilot, Cursor, Devin, Claude, etc.) |
| | `diff_uniformity` | float | Variance in line lengths (AI-generated code exhibits high uniformity) |
| | `block_add_signal` | binary | Single giant blocks added with zero deletions |
| **PR Context** | `pr_description_quality` | float | Length & presence of reproduction steps in PR body |
| | `rapid_merge_signal` | binary | Merged within 2 minutes of opening |

### Logarithmic Sizing Calibration
To prevent massive refactors from skewing models linearly, diff sizes are scaled logarithmically:

$$S_{norm} = \min\left(1.0, \frac{\ln(1 + \text{lines\_changed})}{\ln(1 + 1000)}\right)$$

---

## 2. Model 2: Review Depth Scorer (DistilBERT)

### Purpose
Classifies each review comment into qualitative categories and computes an aggregate review depth score $D_{score} \in [0, 1]$.

### Comment Taxonomy & Depth Weights
| Class | Description | Weight | Example |
|---|---|---|---|
| `security` | Identifies potential security or auth vulnerability | **0.95** | *"This allows unauthenticated access if the header is empty."* |
| `architectural` | Questions concurrency, state, rollback, or interface design | **0.85** | *"What happens if the DynamoDB transaction aborts halfway?"* |
| `substantive` | Points out algorithmic or functional correctness issues | **0.75** | *"The loop index exceeds array length when buffer is empty."* |
| `clarifying` | Requests context or clarification on implementation | **0.45** | *"Could you explain why we need this extra lock here?"* |
| `nitpick` | Style, formatting, or naming convention suggestions | **0.20** | *"Please rename this variable to camelCase."* |
| `rubber_stamp` | Low-effort approval devoid of technical feedback | **0.00** | *"LGTM", "looks good", "approved", "+1"* |

### Depth Score Calculation
$$D_{score} = \min\left(1.0, \frac{\sum_{i=1}^{N} W(\text{class}_i)}{\sqrt{N + 1}}\right)$$

If an approval is submitted while unresolved `CHANGES_REQUESTED` comments remain, a **35% penalty** is applied:
$$D_{score} \leftarrow D_{score} \times 0.65$$

---

## 3. Model 3: Reviewer Attention & Fatigue Baseline

### Purpose
Detects reviewer fatigue, rushed reviews, and pacing anomalies using historical baseline statistics.

### Robust Statistics (Median & MAD)
Because review duration distributions are heavy-tailed and skewed by interruptions, standard mean and variance fail. Vouch uses non-parametric Median and **Median Absolute Deviation (MAD)**:

$$\text{MAD} = \text{median}(|x_i - \tilde{x}|)$$
$$\text{Robust } Z = \frac{x - \tilde{x}}{1.4826 \times \text{MAD}}$$

### Fatigue Index
Consecutive reviews performed in rapid succession degrade attention. Attention state is penalized based on review count within a rolling 4-hour window:

$$A_{state} = \max\left(0.10, A_{baseline} - (\text{consecutive\_reviews} - 1) \times 0.08\right)$$

### Unsupervised Anomaly Detection
An **Isolation Forest** operates on the tuple `(review_duration_seconds, diff_lines, consecutive_reviews)` to flag anomalous reviews (e.g., 500-line diff approved in 45 seconds by a reviewer on their 6th review).

---

## 4. Production Discrepancy Detectors

Vouch runs seven production discrepancy checks on every PR evaluation:
1. **Rapid Merge Flag**: Merged in under 120 seconds with non-trivial diff lines $\rightarrow$ flags Change Risk elevation.
2. **Missing Reviewer Flag**: Merged or approved PR with no assigned or recording reviewer $\rightarrow$ forces Review Confidence to 0.05.
3. **Self-Review Flag**: Author and approving reviewer are identical $\rightarrow$ forces Review Confidence to 0.00.
4. **WIP Merged Flag**: Pull request marked `[WIP]`, `[DRAFT]`, or `TODO` merged $\rightarrow$ flags elevated Change Risk.
5. **Mass Touch Flag**: Diff touches $>15$ files simultaneously $\rightarrow$ increases defect probability.
6. **Pending Changes Requested**: Approved despite outstanding changes requested $\rightarrow$ penalizes Depth Score.
7. **Inattentive Rush**: Approved at $>10\times$ faster than normal review velocity $\rightarrow$ penalizes Attention State.
