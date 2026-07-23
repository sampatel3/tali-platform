# Records of Processing Activities (ROPA)

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> Art 30 records, implementing Horizon 1, item 5 of `docs/COMPLIANCE_RISK_AND_ROADMAP.md`. Two sections:
> Taali as **processor** (Art 30(2)) for customer candidate data, and Taali as **controller** (Art 30(1))
> for its own account/billing/site/support data. Honest tables. `[BRACKETS]` = to confirm before relying.

Controller/processor contact: `[Taali legal entity, address, contact — and DPO/representative if appointed]`.

---

## Section 1 — Taali as processor (Art 30(2))

Processing carried out on behalf of each customer (the controller). One row per activity; applies per customer.

| Activity | Categories of data | Categories of data subjects | Purpose | Transfers | Retention | Sub-processors |
|---|---|---|---|---|---|---|
| ATS sync | Identity/contact, CV content, structured profile, raw ATS payloads (`workable_data` / `bullhorn_data`), ATS identifiers | Customer's candidates/applicants | Sync candidate records to/from customer ATS | US (see §3) | Per customer period + `RETENTION_SCHEDULE.md` | Railway, AWS S3 |
| Document storage | CV / job-spec file uploads, cached report documents | Candidates | Durable storage of candidate documents (Railway filesystem is ephemeral) | US | Same clock as candidate record | AWS S3 (S3-compatible; endpoint set by deployment config) |
| AI pre-screen | CV text, screening answers, profile | Candidates | Screen applications vs role criteria | US | Same clock as candidate record | Railway, Anthropic |
| CV↔JD scoring | CV content, profile, job spec | Candidates | Score candidate vs job with cited evidence | US | Same clock | Railway, Anthropic, `[Neo4j/Voyage if enabled]` |
| Deterministic verdict + decision application | Scores, rule path, policy revision, override history, auto-reject state/reason/timestamp | Candidates | Advance/reject verdict. Advances are queued for the customer's human sign-off; **pre-screen rejections are applied automatically** where the customer leaves that setting on (the default), switchable off per role | US | Decision/audit history retained as compliance evidence (see `RETENTION_SCHEDULE.md`) | Railway |
| AI work-sample assessment | Prompts, Claude responses, file changes, validation runs (no screen/mic/camera) | Candidates | Evaluate work-sample performance | US | Same clock as candidate record | Railway, Anthropic, E2B, GitHub/Microsoft |
| Interview transcripts | Voice-derived transcript text | Candidates + interview participants | Transcribe interviews (only if customer connects Fireflies) | US | Same clock | Fireflies, Railway |
| Candidate reports / shortlists | Name, CV text, scores, evidence | Candidates | Share reports to customer via revocable links | US | Same clock (frozen snapshots — see R8) | Railway, AWS S3 |
| Outreach / sourcing | Contact + profile data | Prospective candidates | Candidate sourcing/outreach where enabled | US | `[per customer]` | Railway, Resend |
| Calibration / re-scoring | Candidate scores/outcomes | Candidates | Tune scoring **within a customer's data only** | US | `[per customer]` | Railway, Anthropic |
| Transactional email | Name, email | Candidates (assessment invites etc.) | Send platform emails | US | `[transient]` | Resend |
| Product analytics / metering | Operational usage metadata per AI call | n/a (metadata) | Cost/usage audit trail (CI-enforced) | US | `[retention TBD]` | Railway |

**Calibration/re-scoring note.** Recorded here as processor-scope **because Taali commits to keeping calibration per-customer-scoped**. If calibration ever used one customer's candidate data to improve a central model deployed to others, Taali would become a **controller** for that activity (ICO central-model test). That line is a `⚖ COUNSEL` question (roadmap R3 / §8 Q3; see `CONTROLLER_PROCESSOR_MATRIX.md`).

**Art 32 security measures (applies to all rows above):** tenant-scoped RBAC; revocable, time-limited share links; durable decision/audit history; encryption in transit; per-call usage logging; sub-processor flow-down contracts. Full list in `DPA_TEMPLATE.md` Annex II.

---

## Section 2 — Taali as controller (Art 30(1))

Processing Taali carries out for its own purposes.

| Activity | Categories of data | Data subjects | Purpose | Lawful basis | Transfers | Retention | Sub-processors |
|---|---|---|---|---|---|---|---|
| Accounts / auth | Name, work email, hashed credentials, role, org | Customer users (recruiters, admins) | Provide and secure the platform | Contract | US | Life of account + `[statutory]` | Railway |
| Billing | Billing contact, plan, invoices, payment metadata | Customer billing contacts | Take payment, meet tax/accounting duties | Contract; legal obligation | US | `[statutory — e.g. 6–7 years for tax records; confirm]` | Stripe, Railway |
| Site / marketing | IP, device/usage analytics, form submissions | Site visitors, leads | Run and improve the website | Legitimate interests / consent for non-essential cookies | US | `[analytics retention — confirm]` | Vercel, `[analytics vendor if any]` |
| Support email | Name, email, message content | Anyone emailing `hello@`/`support@` | Respond to enquiries and support | Legitimate interests | `[email host region]` | `[email host]`; routes to `sampatel@taali.ai` |
| Product usage metering (own view) | Aggregate usage/cost metrics | n/a | Operate and price the service | Legitimate interests | US | `[retention TBD]` | Railway |

Notes: `hello@` / `support@` are aliases on `sampatel@taali.ai`; `noreply@` has no mailbox. Confirm the support-email host and its region before finalising the transfer/retention cells.

---

## Section 3 — Where the data is (shared by both sections)

United States today: Railway `us-east4` (API, Postgres, Redis), Vercel (frontend/CDN), and S3-compatible object storage for CV / job-spec uploads and cached report documents (AWS S3 `us-east-1` by default; provider set by `AWS_S3_ENDPOINT_URL` — confirm the production endpoint), plus the sub-processors listed above. Transfer mechanisms per sub-processor are published at **taali.ai/subprocessors** and reproduced in `DPA_TEMPLATE.md` Annex III (DPF + UK Extension where certified — all verified 2026-07-23 except E2B, which relies on SCCs + UK Addendum). EU hosting region on the 2027 roadmap.
