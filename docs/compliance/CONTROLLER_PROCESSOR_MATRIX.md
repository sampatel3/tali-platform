# Controller / Processor Role Matrix

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> Role allocation per processing activity, implementing Horizon 1, item 7 of `docs/COMPLIANCE_RISK_AND_ROADMAP.md`.
> This is the highest-leverage legal question in the pack (roadmap R3 / §8 Q3). The honest answer on
> calibration/re-scoring is flagged `⚖ COUNSEL`.

For each activity: who is **controller**, who is **processor**, and notes. "Customer" = the recruitment customer.

| Activity | Controller | Processor | Notes |
|---|---|---|---|
| ATS sync | Customer | Taali | Taali syncs candidate data on the customer's instruction into/out of the customer's ATS |
| AI pre-screen | Customer | Taali | Screening against the customer's role criteria |
| CV↔JD scoring | Customer | Taali | Scoring + cited evidence on the customer's candidates |
| Deterministic verdicts | Customer | Taali | Taali generates the verdict against the customer's criteria; the customer owns the decision either way. For **advances** the customer's human confirms it. For **pre-screen rejections** Taali applies it automatically where the customer leaves that setting on (the default, switchable off per role) — the customer is still controller, but no human of theirs confirms the individual decision (`DPIA_VENDOR.md` §4.1, R0). **Note:** under SCHUFA the verdict can itself be an Art 22 decision *at Taali* even so — this matrix records data-protection **role**, not whether Art 22 is engaged (see `DPIA_VENDOR.md` §4) |
| AI work-sample assessments | Customer | Taali | Assessment run and telemetry captured on the customer's behalf |
| Interview transcripts | Customer | Taali | Only if the customer connects Fireflies; **customer is responsible for the lawful recording basis** (DPA Annex VI) `⚖ COUNSEL` |
| Candidate reports / shortlists | Customer | Taali | Reports shared to the customer via revocable links |
| Outreach / sourcing | Customer | Taali | Where enabled; sourcing on the customer's behalf `[confirm no Taali-controller sourcing exists]` |
| **Calibration / re-scoring** | **Customer-scoped only today → Taali is processor. `⚖ COUNSEL`** | Taali (today) | **See design rule below.** ICO's central-model controller test is the live question (§8 Q3). If calibration ever spans customers to improve a shared model, Taali becomes a **controller** for that activity |
| Product analytics / metering | **Taali** | — | Taali's own operational usage/cost data (Art 30(1)) — Taali is controller |
| Billing | **Taali** | Stripe (processor to Taali) | Taali is controller for its account/billing data |
| Accounts / site / support | **Taali** | Vercel / email host (processors to Taali) | Taali-controller data (see `ROPA.md` §2) |

## Design rule for calibration / re-scoring (the load-bearing rule)

**Calibration and re-scoring must stay per-customer-scoped to keep Taali inside processor role.** Concretely:

- Calibration may use a customer's own candidate data to tune scoring **for that customer only**.
- Candidate data from one customer must **not** be used to improve a scoring model, prompt, rubric, or weighting that is deployed to other customers.
- Taali does **not** train foundation models on candidate data (existing claim), and the calibration pipeline must not become a back-door "central model improvement" that the ICO would read as controller processing.

If a future feature needs cross-customer learning, it must be **re-papered as controller processing** (with its own lawful basis, transparency, and DPIA) before it ships — not run under the processor DPA. `⚖ COUNSEL` to confirm the line and whether the "we never train models on your candidate data" claim needs qualifying (roadmap R3 / §8 Q3).
