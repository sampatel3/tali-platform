# Compliance Artifact Pack — Index

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> One-page index of Taali's compliance artifact pack. Every document implements Horizon 1 of
> `docs/COMPLIANCE_RISK_AND_ROADMAP.md` (the verified risk assessment) and must be read against it.
> Nothing here is legal advice; nothing should be signed, sent, or published without counsel sign-off.

---

## Files

| File | Purpose | Roadmap item |
|---|---|---|
| `DPA_TEMPLATE.md` | Controller→processor Data Processing Agreement (Art 28) with SCC/UK-Addendum transfer modules, subprocessor authorization, DSR/breach terms, UAE annex stub, Fireflies-consent allocation | H1.1 |
| `DPIA_VENDOR.md` | Taali's own DPIA — filled in; the Art 22/SCHUFA analysis; degree/stage of human involvement; risk register R1–R14 with mitigation status; residual-risk statement | H1.4 |
| `DPIA_CUSTOMER_TEMPLATE.md` | Fill-in-the-blanks DPIA a customer (deployer/controller) adapts before procuring — sales collateral | H1.4 |
| `ROPA.md` | Records of processing (Art 30): Taali-as-processor and Taali-as-controller tables | H1.5 |
| `CANDIDATE_PRIVACY_NOTICE_TEMPLATE.md` | Template notice customers give candidates; plain-English ADM description + UK Art 22C checklist + transfers + rights | H1.3 |
| `CONTROLLER_PROCESSOR_MATRIX.md` | Role allocation per activity; the calibration/re-scoring `⚖ COUNSEL` question and the per-customer-scope design rule | H1.7 |
| `CANDIDATE_CONTEST_PROCESS.md` | Operational contest/representations process: intake, SLAs, reviewer duties, logging, escalation | H1.6 |
| `RETENTION_SCHEDULE.md` | Default retention policy; honest "scheduler is H2, enforcement is manual today" status | H1.3 / R8 |
| `INDEX.md` | This index | — |

## How the pack hangs together

- The **DPA** is what a customer's DPO asks for first; it points to the **subprocessor page** (to be published), **ROPA**, and the **DPIAs**.
- The **vendor DPIA** carries the load-bearing Art 22/SCHUFA analysis and the risk register; the **customer DPIA template** is the deployer-facing version.
- The **privacy notice** and **contest process** deliver the candidate-facing transparency and Art 22C rights.
- The **role matrix** answers "who is controller for what" and isolates the one live classification question (calibration).
- The **retention schedule** sets the policy the H2 scheduler will enforce.

## Counsel-review status checklist

Nothing in this pack is cleared until counsel signs. Track here:

- [ ] `DPA_TEMPLATE.md` — SCC/Addendum options, liability cross-ref, UAE Annex V, Fireflies Annex VI
- [ ] `DPIA_VENDOR.md` — SCHUFA→hiring extension, EU consent gateway, residual-risk / Art 36 conclusion
- [ ] `DPIA_CUSTOMER_TEMPLATE.md` — accuracy of pre-filled facts; fit for deployer use
- [ ] `ROPA.md` — controller/processor split, retention/statutory cells
- [ ] `CANDIDATE_PRIVACY_NOTICE_TEMPLATE.md` — ADM wording, recording-consent line
- [ ] `CONTROLLER_PROCESSOR_MATRIX.md` — calibration classification (R3 / §8 Q3)
- [ ] `CANDIDATE_CONTEST_PROCESS.md` — how much decision logic must be disclosed
- [ ] `RETENTION_SCHEDULE.md` — default period, audit-history outer bound, billing statutory period

## Open `⚖ COUNSEL` questions carried by this pack (map to roadmap §8)

1. SCHUFA extension to hiring — is Taali's design safe? (§8 Q1) — DPIA §4
2. EU explicit-consent gateway architecture (§8 Q2) — DPIA §4
3. Controller/processor line on calibration/re-scoring (§8 Q3) — role matrix + ROPA
4. Special-category leakage filtering in CV/transcript inputs (§8 Q4) — DPIA §4, privacy notice
5. UAE PDPL Art 23 wording + ADGM sequencing (§8 Q7) — DPA Annex V
6. Fireflies recording-consent allocation (§8 Q8) — DPA Annex VI
7. Transfers: TRAs per subprocessor + DPF-fallback (§8 Q6) — DPA clause 10

## Dependencies outside this pack (to publish/build)

- **taali.ai/subprocessors** public page (H1.2) — the DPA, ROPA, and privacy notice all reference it.
- Real **Terms** and **Privacy** pages to replace the dangling `RegisterPage.jsx` links (H1.3).
- **Delete/fence `sentiment_trajectory`** + release-checklist rule (H1.8, R12).
