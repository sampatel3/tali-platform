# Data Protection Impact Assessment — Taali (Vendor / Processor)

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> Taali's own DPIA, implementing Horizon 1, item 4 of `docs/COMPLIANCE_RISK_AND_ROADMAP.md`. WP251rev.01
> and the ICO both expect the provider of a profiling tool that supports significant decisions to complete
> a DPIA **even where the final decision is not solely automated**. This is that document. Legal questions
> for counsel are flagged `⚖ COUNSEL`; open choices are in `[BRACKETS]`.

---

## 1. Why this DPIA exists (and why it is mandatory)

A DPIA is required here, not optional — and on the pre-screen reject path a human is **not** in the loop by default (§4.1), which puts that processing squarely inside Art 35(3)(a) rather than at its edge. WP251rev.01 reads GDPR Art 35(3)(a) as requiring a DPIA for profiling-based decisions that produce legal or similarly significant effects **even when a human is in the loop**. The ICO's AI-in-recruitment audit says the same for AI hiring tools. Denying someone an employment opportunity is a named "similarly significant" effect. Taali's platform profiles candidates and produces advance/reject verdicts that feed such decisions. Therefore a DPIA is mandatory regardless of the human-in-the-loop design. This document also serves as the input the Customer needs for its own deployer DPIA (`DPIA_CUSTOMER_TEMPLATE.md`).

## 2. Description of the processing

Taali is the **processor** (and, under the EU AI Act, the **provider**); the Customer is the controller/deployer. Processing operations, drawn from `docs/COMPLIANCE_RISK_AND_ROADMAP.md` §1:

| Processing operation | What it does | Personal data involved |
|---|---|---|
| ATS sync | Two-way sync with the Customer's ATS (Workable / Bullhorn) | Identity, CV, structured profile, raw ATS payloads |
| AI pre-screen | Screens applications against role criteria | CV text, screening answers, profile |
| CV↔JD scoring | Scores candidate against job with cited evidence (Claude) | CV content, profile, job spec |
| Deterministic verdict | Rule-based advance/reject verdict. **Advances** are queued for the Customer's human sign-off. **Pre-screen rejections apply automatically by default** (`auto_reject_pre_screen: True`), switchable off per role — see §4 and R0 | Scores, rule path, policy revision, auto-reject state/reason/timestamp |
| AI work-sample assessment | Candidate completes a task; full session telemetry captured | Prompts, Claude responses, file changes, validation runs (no screen/mic/camera) |
| Interview transcripts | Ingests Fireflies transcripts where the Customer connects it | Voice-derived transcript text |
| Candidate reports | Generates reports/shortlists shared to the Customer via revocable links | Name, CV text, scores, evidence |
| Outreach | Candidate sourcing / outreach (where enabled) | Contact + profile data |
| Calibration / re-scoring | Tunes scoring within a customer's data | Candidate scores/outcomes `⚖ COUNSEL` |
| Product analytics / metering | Usage events per AI call (CI-enforced) | Operational metadata (Taali-controller data) |

- **Data subjects:** the Customer's candidates and applicants; interview participants where Fireflies is connected.
- **Data categories:** as in `ROPA.md` / `DPA_TEMPLATE.md` Annex I. No special-category data is intentionally processed; incidental special-category signals can appear in free-text CVs, transcripts and answers.
- **Where:** United States today (Railway `us-east4`, Vercel), plus the sub-processors in `DPA_TEMPLATE.md` Annex III. EU region on the 2027 roadmap.
- **Recipients:** the Customer; sub-processors under flow-down contracts.
- **Retention:** per `RETENTION_SCHEDULE.md` and the Customer's configured period.

## 3. Necessity and proportionality

- **Lawful basis:** set by the Customer as controller (typically legitimate interests for screening, with a balancing test; special-category and solely-automated tracks need more — see §4). Taali processes only on documented instructions.
- **Purpose limitation:** candidate data is used to screen, evaluate and support hiring decisions for the Customer, and not for Taali's own purposes. Taali does **not** train foundation models on candidate data. Calibration is committed to being customer-scoped (§4, R3).
- **Data minimisation:** assessments deliberately capture no screen, microphone, camera, or webcam-proctoring data — a defensible minimisation position that also avoids biometric/emotion territory. Verdicts run on a defined, deterministic rule set rather than free inference, which limits stray inputs.
- **Proportionality:** the tool automates screening at volume. On the **advance** path, the human-review queue and evidence-linked decisions are the proportionality controls that keep a machine score from being the outcome. On the **pre-screen reject** path that control is absent by default: the rejection is applied automatically, and the proportionality controls are the deterministic rule, the recorded reason, the per-role opt-out, and the after-the-fact contest route. Whether that is proportionate for EU candidates is the open R0 question (§4).
- **Accuracy:** scores carry provenance (date + engine version) and cited verbatim evidence; decisions record the rule path.

## 4. Automated decision-making analysis (Art 22 / SCHUFA)

This is the load-bearing analysis.

### 4.1 Pre-screen rejection is a solely automated decision today, by default (R0 — read this first)

- **What the code does.** `auto_reject_pre_screen` defaults to `True` (`backend/app/services/agent_policy_settings.py`), while every candidate-facing positive action defaults to `False` (`auto_advance`, `auto_send_assessment`, `auto_skip_assessment`). Two paths reject a candidate with no human in the loop, both gated on that flag:
  1. **Recruiter-authored knockout answers** — `backend/app/domains/job_pages/knockout_automation.py`. Boolean/choice screening-question failures, no model judgment. The live role row is locked so Turn off / Pause wins races; ATS-linked applications are rejected upstream first; the reject falls back to a Decision Hub card when the policy is off, the role is ineligible, or ATS write-back fails.
  2. **Below-threshold pre-screen score** — dispatched from `backend/app/tasks/scoring_tasks.py` and `backend/app/tasks/agent_tasks.py` into `run_application_auto_reject`, which honours `auto_reject_pre_screen`: direct provider disqualify when on, a Decision Hub card when off. **The pre-screen score is LLM-derived** (`backend/app/services/pre_screening_service.py` — `llm_score_100`); the reject rule applied to that score is deterministic.
- **State and control.** Every automatic rejection records its state, reason and timestamp via `mark_auto_reject_state`. The flag is editable per role, so a Customer can turn it off and route rejections to a human instead.
- **This is the GDPR's own canonical Art 22 case.** Recital 71 names "e-recruiting practices without any human intervention"; WP251rev.01 lists denying someone an employment opportunity as a similarly significant effect. Rejecting an applicant with no human involvement is therefore the textbook example, not an edge case.
  - **EU:** Art 22(1) is a prohibition in principle. It needs an Art 22(2) gateway — contractual necessity, EU/Member-State law, or explicit consent. **No gateway is established today.** `⚖ COUNSEL` (§8 Q0).
  - **UK:** DUAA Arts 22A–22D (in force 5 Feb 2026) permit a solely automated significant decision on an ordinary lawful basis **provided the full Art 22C safeguard set applies** — information, representations, human intervention, contest. Art 22B still prohibits it where special-category data is relied on, absent explicit consent covering all data relied on.
  - There is **no jurisdiction-awareness** in the product: a role whose candidates are in the EU gets the same default as a UK one.
- **Mitigation status, stated frankly.** In place: the reject rule is deterministic; per-role opt-out exists; auto-reject state, reason and timestamp are recorded; a Decision Hub card is the fallback when the policy is off, the role is ineligible, or ATS write-back fails; the role lock prevents acting on a paused or turned-off role; the contest route is documented (`CANDIDATE_CONTEST_PROCESS.md`) and the ADM position is published on `/privacy`. **Not in place:** no Art 22(2) gateway for the EU; the candidate is **not individually informed at the point of rejection** that a solely automated decision was made about them; human-intervention and contest rights exist as a documented route, not as a surface attached to the rejection itself; no jurisdiction-aware default.
- **The decision is open.** Options A–D and the recommendation (**B — jurisdiction-aware default**) are at `docs/COMPLIANCE_RISK_AND_ROADMAP.md` §5, Horizon 1, item 0. It is deliberately left open rather than changed unilaterally: flipping the default changes funnel throughput for every existing customer. Until it is decided, the honest position is the published one — pre-screen rejection can be automatic, it is on by default, and it is switchable off per role.

### 4.2 The advance path and vendor-level exposure

- **The verdict can itself be the decision.** Under CJEU *SCHUFA* (C-634/21), the automated establishment of a value is Art 22(1) automated decision-making **at the vendor** where a third party "draws strongly" on it — the Court rejected the "preparatory act" reading to avoid a lacuna in protection. So Taali's deterministic verdict can be the Art 22 decision at Taali, not only at the Customer, whenever the Customer's recruiters follow verdicts near-mechanically. Client-side human sign-off does not automatically immunise the platform. (Roadmap §2.1, R1.)
- **The extension from credit scoring to hiring** is the mainstream practitioner reading but has not been decided by a court for recruitment. `⚖ COUNSEL` (§8 Q1).
- **Meaningful human review is the control.** Human oversight only removes a decision from Art 22 if it is meaningful: a reviewer with the authority and competence to change the outcome, who considers all the relevant data. Regulators say involvement "cannot be a token gesture or a rubber stamp," and the ICO's 2025–26 evidence-gathering found most "decision support" tools were, in practice, solely automated. Taali must be able to **evidence** meaningfulness, not assert it. (Roadmap §2.1/§2.2, R2.)
- **Degree and stage of human involvement (WP251 requirement — recorded explicitly):**
  - **Stage — advances and every other positive action:** human review is positioned **after** the verdict is generated and **before** any consequential action (any ATS write-back / stage change). No advance, assessment send, or other positive step is written to the Customer's system of record without a human sign-off step.
  - **Stage — pre-screen rejections:** there is **no human stage** where `auto_reject_pre_screen` is on, which is the default. The verdict is generated and applied. Human involvement exists only after the fact, if the candidate contests or a recruiter reviews the record (§4.1, R0).
  - **Degree:** where a human reviews, they see the full evidence — cited CV evidence, the rule path, the score provenance, and (where relevant) the assessment record — and have authority to override. Overrides are recorded. Decision history is exportable. For an automatically applied pre-screen rejection, the degree of human involvement **at the point of decision is nil**.
  - **Known weakness:** the approve flow is low-friction by design, and override/edit rates are **not yet measured**. Until they are, "meaningful" is a design claim, not an evidenced one. The Horizon-2 "meaningful-HITL evidence pack" (measure override rates, define reviewer authority/competence, add friction on borderline bands, ship review analytics) closes this. `[Target: H2, next 3 months.]`
- **UK track (DUAA Arts 22A–22D, in force 5 Feb 2026).** Solely automated significant decisions may rest on an ordinary lawful basis **provided the Art 22C safeguards apply** (information, representations, human intervention, contest). Art 22B **prohibits** solely automated significant decisions based on special-category data without explicit consent covering all data relied on. The Art 22C safeguards map onto `CANDIDATE_CONTEST_PROCESS.md` and the candidate privacy notice.
- **EU track.** If verdicts are found solely automated in the EU, explicit candidate consent is realistically the only workable Art 22(2) gateway for a vendor. Consent architecture in a recruitment context (imbalance of power) is contested. `⚖ COUNSEL` (§8 Q2).
- **Special-category leakage (Art 22B strict track / Art 9).** CVs, transcripts and answers can carry health/ethnicity/religion signals. No masking/ignore policy for special-category content in scoring inputs exists yet. `⚖ COUNSEL` (§8 Q4, R5).

## 5. Risks to data subjects and mitigations

Reuses the roadmap risk register (R0–R14). "Implemented (H1)" = delivered by this documentation sprint. "Planned (H2/H3)" = roadmap, with target.

| # | Risk to individuals | Sev | Mitigation status |
|---|---|---|---|
| **R0** | **A candidate is rejected at pre-screen with no human involved.** `auto_reject_pre_screen` defaults on; a failed recruiter-authored knockout, or an LLM-derived pre-screen score below the recruiter's threshold, applies the rejection automatically (§4.1) | **H — highest** | **Partially mitigated (H1):** deterministic reject rule; per-role opt-out; auto-reject state, reason and timestamp recorded; Decision Hub fallback when the policy is off, the role is ineligible, or ATS write-back fails; role lock; contest route (`CANDIDATE_CONTEST_PROCESS.md`); ADM position published on `/privacy` and in `CANDIDATE_PRIVACY_NOTICE_TEMPLATE.md`. **Not mitigated:** no Art 22(2) gateway for the EU; the candidate is not individually informed at the point of rejection; contest rights are a documented route, not a surface attached to the rejection; no jurisdiction-aware default. **Decision open:** roadmap §5 Horizon 1 item 0 (options A–D, recommendation B). `⚖ COUNSEL` (§8 Q0). |
| R1 | Vendor-level Art 22 exposure — verdict is itself the decision | H | **Implemented (H1):** contest route (`CANDIDATE_CONTEST_PROCESS.md`), candidate ADM notice (privacy template), this DPIA. **Planned (H2):** meaningful-HITL evidence pack; EU consent-gateway design pending `⚖ COUNSEL`. |
| R2 | Rubber-stamp HITL — review not meaningful in practice | H | **Implemented (H1):** reviewer sees evidence + rule path; overrides recorded; degree/stage documented above. **Planned (H2):** measured override rates, reviewer competence/authority definition, friction on borderline bands, review analytics. |
| R3 | Controller misclassification via calibration on candidate data | H | **Implemented (H1):** role matrix (`CONTROLLER_PROCESSOR_MATRIX.md`) with the design rule that calibration stays per-customer-scoped. `⚖ COUNSEL` (§8 Q3). |
| R4 | No candidate transparency (notice, retention, ADM info) | H | **Implemented (H1):** `CANDIDATE_PRIVACY_NOTICE_TEMPLATE.md` with ADM description + retention + contest; dangling Terms/Privacy links to be replaced with real pages `[Sam to publish pages]`. |
| R5 | Special-category leakage into verdicts | M–H | **Planned (H2):** leakage analysis + masking/ignore rules; recorded in this DPIA on completion. `⚖ COUNSEL` (§8 Q4). |
| R6 | Incomplete transfer stack (US-only hosting) | M (H if DPF falls) | **Implemented (H1):** DPA with SCC/UK-Addendum modules; public subprocessor page `[to publish]`; transfer matrix in DPA Annex III. **Planned (H3):** EU region. |
| R7 | EU AI Act provider obligations (due 2 Dec 2027) | M→H | **Planned (H2/H3):** provider-readiness workplan (Arts 9–15 mapping), Art 11 tech-doc file, Art 17 QMS, Annex VI conformity assessment, EU-database registration. Telemetry/logging (Art 12) and oversight design (Art 14) are head starts. |
| R8 | Retention + erasure incompleteness | H | **Implemented (H1):** retention policy (`RETENTION_SCHEDULE.md`); honest erasure-scope disclosure. **Planned (H2):** cross-table erasure sweep, retention scheduler, ATS re-import suppression. |
| R9 | Bias testing without lawful demographic data | M | **Partially implemented:** voluntary self-ID step on the native apply flow (optional, dismissible, stored segregated from scoring); owner-only aggregate EEO report with k-anonymity small-cell suppression; 4/5ths adverse-impact script (`backend/scripts/adverse_impact_report.py`); continuous bias-monitor capability; no protected attributes anywhere in the schema by policy. **Planned (H2):** extend self-ID to ATS-synced/assessment candidates, UK/EU explicit-consent + Art 9-condition wording (current framing is US EEO/OFCCP), scheduled impact-ratio reporting fed back into design. |
| R10 | UAE regime uncertainty + ADGM entity | M | **Implemented (H1):** PDPL Art 23 annex stub in DPA. **Planned (H2):** ADGM notification/policy/DPO assessment, ADGM-Addendum SCCs. `⚖ COUNSEL`. |
| R11 | Marketing overclaim ahead of documentation | M | **Implemented (H1):** this pack makes the stronger claim survivable; keep deck wording to design+process until pack is signed off (roadmap §6). |
| R12 | Emotion-inference prohibition (in force now) | L (H if triggered) | **Implemented (H1):** standing product rule "no emotion/sentiment inference on candidates"; dormant `sentiment_trajectory` field to be removed or fenced + added to release checklist `[H1 action]`. |
| R13 | Interview-recording consent (Fireflies) | M | **Implemented (H1):** consent responsibility allocated to the Customer in DPA Annex VI. `⚖ COUNSEL` (§8 Q8). |
| R14 | DPIA absence blocks procurement | H (sales blocker) | **Implemented (H1):** this DPIA + `DPIA_CUSTOMER_TEMPLATE.md`. |

## 6. Consultation

`[Record who was consulted: Sam Patel (owner); counsel review — PENDING; a candidate/data-subject perspective — how obtained; the DPO or equivalent if appointed. Add dates as they occur.]`

## 7. Residual-risk statement

The **highest residual risk is R0**. On a default-configured role, an EU or UK candidate can be rejected at pre-screen with no human involvement. The Horizon-1 controls reduce it — the rule is deterministic, the reason is recorded, the opt-out exists per role, and the contest route and ADM wording are now documented and published — but they do not remove it. There is no Art 22(2) gateway for EU candidates, and the candidate is not told at the point of rejection that the decision was solely automated. **R0 cannot be closed by documentation.** It needs the product/business decision at roadmap §5, Horizon 1, item 0, and counsel's answer to §8 Q0.

The other **residual high risks** are R1/R2 (meaningfulness of human review on the advance path is documented by design but not yet evidenced by measured override rates) and R8 (erasure is single-table pending the H2 sweep). These reduce to acceptable once the Horizon-2 items land.

On **Art 36 prior consultation**: no residual risk on Taali's processor-side processing is assessed as requiring it, **provided** counsel confirms the SCHUFA-to-hiring analysis (§8 Q1), the controller/processor line on calibration (§8 Q3), **and the R0 position (§8 Q0)**. R0 is the one row where a high residual risk to individuals could survive mitigation, which is the Art 36 trigger — so this conclusion is weaker than the others until Q0 is answered. `[Sam / counsel to confirm this conclusion before relying on it.]` The Customer, as controller, makes its own residual-risk and prior-consultation call in its deployer DPIA — including on automatic pre-screen rejection for its own roles.

## 8. Sign-off and review cadence

- **Sign-off:** `[Owner: Sam Patel — date] · [Counsel: name — date] · [DPO/equivalent — date]`. Do not treat as complete until counsel signs.
- **Review cadence:** **quarterly**, and additionally **on publication of the ICO's final ADM guidance** (consultation closed 29 May 2026; final pending) and the statutory AI/ADM code (SI 2026/425), and when the EU AI Act omnibus OJ text is published for review.
- **Counsel questions carried from this DPIA:** **Q0 default-on pre-screen auto-reject (R0 — ask first)**; Q1 SCHUFA→hiring extension; Q2 EU consent gateway; Q3 controller/processor line on calibration; Q4 special-category leakage filtering; Q8 Fireflies recording consent (numbering follows roadmap §8).
