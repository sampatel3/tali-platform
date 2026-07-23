# Data Retention Schedule

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> Default retention policy, implementing Horizon 1 (roadmap R8) of `docs/COMPLIANCE_RISK_AND_ROADMAP.md`.
> **Implementation status (honest):** the automated retention scheduler is a **Horizon-2 roadmap item**;
> current enforcement is **manual via the DSR / erasure workflows** (`backend/app/domains/compliance/`).
> This document sets the policy the scheduler will enforce.

---

## Principles

Storage limitation (GDPR/UK GDPR Art 5(1)(e)) requires retention periods that are **justified, documented, and enforced**. The sector norm for unsuccessful applicants is **6–12 months** after the process closes. The UK anchor is the **6-month Equality Act 2010 claim window** — unsuccessful-applicant data should not outlive the claim period without a documented reason. Periods are **customer-configurable**; the customer is the controller and sets the actual period within these defaults.

## Schedule

| Data | Default retention | Basis / rationale | Enforcement today |
|---|---|---|---|
| **Unsuccessful candidates** (candidate record + CV, profile, screening answers) | **6–12 months after process close**; UK default **6 months** (Equality Act claim window); customer-configurable | Storage limitation; ability to defend a discrimination claim within the window | Manual erasure via DSR workflow |
| **Hired candidates** | Duration of the engagement, then per the customer's employment-records policy | Data becomes part of the employment relationship — handled per customer | Customer-directed; manual |
| **Assessment telemetry** (prompts, responses, file changes, validation runs) | **Same clock as the candidate record** it belongs to | It is candidate data about that application; no independent reason to keep it longer | Manual (part of candidate erasure) `[H2: cascade — currently out of scope, R8]` |
| **Interview transcripts** (if Fireflies connected) | Same clock as the candidate record | Candidate data; customer is controller of the recording | Manual |
| **Candidate reports / submittal snapshots** | Same clock as the candidate record | Frozen snapshots embed candidate PII (R8) | Manual `[H2: snapshot sweep — currently out of scope]` |
| **Decision / audit history** (verdict, rule path, policy revision, override history) | **Retained as compliance evidence** beyond the candidate record | See rationale below | Deliberately retained |
| **Talent-pool candidates** (opt-in) | Per recorded candidate consent (commonly 12 months) | Only lawful on explicit opt-in | Manual; consent-gated |
| **Account data** (users, org) | Life of account + statutory tail | Needed to run the service | Manual |
| **Billing records** | `[statutory — e.g. 6–7 years for tax/accounting; confirm]` | Legal obligation | Manual |

## Why decision/audit history is kept longer than the candidate PII

The decision and audit trail (verdict, rule path, policy revision, override history) is retained after the underlying candidate PII is minimised/erased because it is the **evidence of how each decision was actually reached** — needed to answer a regulator, a candidate contest, or an Equality Act challenge, to evidence "meaningful human review" where a human reviewed (roadmap R1/R2), and to reconstruct the rule, reason and timestamp behind an automatically applied pre-screen rejection where none did (R0). Basis: **compliance with a legal obligation** and/or **legitimate interests** in defending claims and demonstrating accountability. The erasure design already treats the immutable audit trail as deliberately retained compliance evidence (`data_subject_service.py` docstring). `[BRACKET — set an outer bound on audit-history retention, e.g. aligned to the longest applicable limitation period; confirm with counsel.]`

## Enforcement roadmap

- **Today:** retention is enforced **manually** through the DSR access/erasure workflow. Erasure is single-table (primary candidate record); cross-table PII (applications, interviews, snapshots, prospects, assessment artifacts) and ATS re-import are documented as out of scope (roadmap R8).
- **Horizon 2:** automated retention scheduler + cross-table erasure sweep + ATS re-import suppression list. When built, the scheduler enforces this schedule.

## Open decisions

- `[BRACKET]` Default within the 6–12 month band per region/customer — pick the UK default (6 months) as the anchor and let customers extend to 12 with a documented reason.
- `[BRACKET]` Outer bound on decision/audit-history retention.
- `[BRACKET]` Billing-records statutory period — confirm against the entity's jurisdiction.
