# Data Protection Impact Assessment — Customer / Deployer Template

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> Sales collateral: a fill-in-the-blanks DPIA a recruitment customer (the **controller / deployer**) can
> adapt before procuring Taali. The ICO says deployers must complete a DPIA **before** they buy and use an
> AI hiring tool, so this is built to be genuinely usable. Platform facts are pre-filled and accurate as at
> 2026-07-23. Fields marked **`[CUSTOMER]`** are for you to complete — they are your decisions as controller.

---

## How to use this template

Sections A–D are **pre-filled by Taali** with accurate facts about the platform. Sections E–I are **`[CUSTOMER]`** — your lawful basis, your role criteria, your reviewer process, your retention choice, your risk sign-off. A DPIA is your obligation as controller; Taali provides the vendor-side inputs (its own DPIA is `DPIA_VENDOR.md`). Complete every `[CUSTOMER]` field before go-live. Where you need a lawyer, we flag `⚖ COUNSEL`.

## A. The processing — what Taali does (pre-filled)

Taali is your **processor** (and, under the EU AI Act, the **provider**); you are the **controller / deployer**.

| Operation | What it does |
|---|---|
| ATS sync | Two-way sync with your ATS (Workable / Bullhorn) |
| AI pre-screen | Screens applications against role criteria |
| CV↔JD scoring | Scores candidate vs. job with cited verbatim evidence (Claude) |
| Deterministic verdict | Rule-based advance/reject verdict. **Advances are queued for your human sign-off. Pre-screen rejections are applied automatically by default** — you can turn that off per role (see §C) |
| AI work-sample assessment | Candidate completes a task; session telemetry plus integrity metrics from the assessment tab (clipboard, blocked export attempts, tab-focus changes) captured for cheating deterrence (**no screen, microphone, or camera**) |
| Interview transcripts | Only if **you** connect Fireflies |
| Candidate reports | Reports/shortlists shared via time-limited, revocable links |

## B. What data Taali processes, and where (pre-filled)

- **Data subjects:** your candidates and applicants; interview participants if you connect Fireflies.
- **Categories:** identity/contact; CV content; structured profile; screening answers and notes; assessment session telemetry; interview transcripts (if connected); scores/verdicts/evidence; reports; raw ATS payloads synced from your ATS. **No special-category data is intentionally processed.** Free-text fields (CV, transcript, answers) can incidentally contain special-category signals — see §F and `⚖ COUNSEL`.
- **Location / transfers:** processed in the **United States** today (Railway `us-east4`, Vercel, AWS S3 object storage), plus Taali's sub-processors. Transfers from the UK/EU rely on the **EU–US Data Privacy Framework / UK Extension** where the sub-processor is certified, and **EU SCCs + UK Addendum** otherwise. Per-sub-processor mechanisms: **taali.ai/subprocessors**. An EU hosting region is on Taali's 2027 roadmap.
- **Sub-processors:** Anthropic (AI), Railway (hosting), Vercel (frontend), AWS S3 (object storage — CV/job-spec uploads and cached report documents), E2B (assessment sandboxes), Fireflies (transcripts, if connected), Resend (email), Stripe (billing), GitHub/Microsoft (assessment repos), Neo4j/Voyage (evidence graph, if enabled). Live list and transfer mechanisms at **taali.ai/subprocessors**.

## C. Automated decision-making design (pre-filled)

- Taali produces a **deterministic, rule-based** advance/reject verdict from defined role criteria, with cited evidence and a recorded rule path — not a free-form AI judgement. The pre-screen score the rule is applied to is produced with AI assistance.
- **Advances always wait for a person.** Progressing a candidate, sending an assessment, or any other positive step is queued for a human on your team to confirm before anything is written back to your ATS. The reviewer sees the cited evidence, the rule path, the score provenance, and the assessment record, and can override; overrides are recorded; decision history is exportable.
- **Pre-screen rejections can be applied automatically, and this is ON by default for your roles.** Where a candidate fails a screening rule you wrote, or scores below the pre-screen threshold you set, Taali applies the rejection without a person confirming it individually. The setting (`auto_reject_pre_screen`) is **on by default** and you can **turn it off per role**, in which case the rejection becomes a card for one of your humans to action instead. Every automatic rejection records its state, reason and timestamp, and remains reviewable and reversible.
- **What that means for your position as controller.** An automatic pre-screen rejection is a **solely automated decision with a similarly significant effect** — it is the GDPR's own named example (Recital 71, "e-recruiting practices without any human intervention"). In the **EU**, Art 22(1) prohibits this in principle unless you have an Art 22(2) gateway (contractual necessity, Member-State law, or explicit consent); **Taali does not provide one**. In the **UK**, DUAA Arts 22A–22D permit it on an ordinary lawful basis **provided you deliver the Art 22C safeguards** — information, representations, human intervention, contest. If special-category data is relied on, Art 22B applies and needs explicit consent. **Record your decision in §E.** `⚖ COUNSEL`
- **This design supports your Art 22 / UK Art 22C position but does not, by itself, satisfy it.** Regulators treat "human in the loop" as meaningful only if your reviewer genuinely has authority, competence and time to change the outcome — not a rubber stamp. On the advance path, **your reviewer process (§G) is what makes the safeguard real**. On the reject path there is no reviewer by default, so your §E decision and the Art 22C safeguards are what make it lawful. See `⚖ COUNSEL` in §H.

## D. Candidate transparency and rights features (pre-filled)

- Assessment sessions disclose exactly what is and isn't recorded, at session start.
- Scores carry provenance (date + engine version) and cited evidence.
- Taali provides a **candidate privacy-notice template** (`CANDIDATE_PRIVACY_NOTICE_TEMPLATE.md`) for you to issue, including a plain-English ADM description and a contest route.
- Taali provides a **candidate contest process** (`CANDIDATE_CONTEST_PROCESS.md`) with SLAs (acknowledge within 3 working days, resolve within 30 days), routed to you as controller.
- Retention defaults are documented in `RETENTION_SCHEDULE.md`; **you set the actual period** (§ I).

---

## E. `[CUSTOMER]` — Your lawful basis

- **Purpose of processing:** `[CUSTOMER — e.g. screening applicants for role X]`
- **Lawful basis (Art 6):** `[CUSTOMER — e.g. legitimate interests; attach your legitimate-interests assessment / balancing test]`
- **If any solely automated significant decision is possible:** identify your Art 22 / UK Art 22C gateway and safeguards. `⚖ COUNSEL`
- **Automatic pre-screen rejection — record your decision (this applies to you by default, see §C):** `[CUSTOMER — state whether you LEAVE IT ON or TURN IT OFF per role, and on what basis. If ON: name the jurisdictions your candidates are in; for EU candidates identify your Art 22(2) gateway; for UK candidates set out your Art 22C safeguard set (how you inform the candidate, take representations, provide human intervention, and allow contest) and who delivers each. If OFF: confirm every rejection becomes a human-actioned decision, and for which roles.]` `⚖ COUNSEL`
- **Special-category basis (Art 9), if engaged:** `[CUSTOMER — normally none; if your process could rely on special-category data, or you run a demographic survey, state the Art 9 condition and any explicit consent]`
- **Transparency:** `[CUSTOMER — confirm you will issue the candidate privacy notice and name yourself as controller]`

## F. `[CUSTOMER]` — Your role criteria and inputs

- **Role criteria you configure:** `[CUSTOMER — list the criteria; confirm they are job-relevant and non-discriminatory]`
- **Special-category leakage check:** `[CUSTOMER — confirm your criteria and inputs do not rely on, or proxy for, special-category characteristics]` `⚖ COUNSEL`
- **Data minimisation:** `[CUSTOMER — confirm you only sync candidate data you need for this process]`

## G. `[CUSTOMER]` — Your reviewer process (the meaningful-review safeguard)

- **Who reviews verdicts:** `[CUSTOMER — role/seniority; confirm authority and competence to change the outcome]`
- **What they must do before acting:** `[CUSTOMER — e.g. open the cited evidence and rule path; do not act on the score alone]`
- **Override expectation:** `[CUSTOMER — confirm reviewers may and do override; consider tracking override rates]`
- **Contest handling:** `[CUSTOMER — who handles candidate representations/contests under the SLA]`

## H. `[CUSTOMER]` — Risk assessment and mitigations

Assess the risks to candidates for **your** deployment. Common ones (adapt severity to your context):

| Risk | `[CUSTOMER]` mitigation |
|---|---|
| **Candidate rejected at pre-screen with no human involved (ON by default)** | `[CUSTOMER — record your §E decision. If left on: Art 22C safeguards delivered and, for EU candidates, an Art 22(2) gateway identified. If not: turn it off for the affected roles]` `⚖ COUNSEL` |
| Over-reliance on AI verdict (rubber-stamp review) | `[CUSTOMER — reviewer authority + evidence-first review per §G]` `⚖ COUNSEL` |
| Indirect discrimination via role criteria | `[CUSTOMER — criteria review; bias monitoring; you remain liable under Equality Act]` |
| Special-category leakage in free-text inputs | `[CUSTOMER — input hygiene; awaiting Taali H2 masking analysis]` `⚖ COUNSEL` |
| International transfer of candidate data to the US | `[CUSTOMER — reliance on DPF/SCCs per taali.ai/subprocessors; your own TRA if required]` |
| Interview-recording consent (if you connect Fireflies) | `[CUSTOMER — you are responsible for lawful recording basis; DPA Annex VI]` `⚖ COUNSEL` |
| Retention beyond need | `[CUSTOMER — set and enforce your retention period, §I]` |

## I. `[CUSTOMER]` — Retention decision

- **Unsuccessful candidates:** `[CUSTOMER — default 6–12 months after process close; UK anchor is the 6-month Equality Act claim window]`
- **Hired candidates:** `[CUSTOMER — per your employment records policy]`
- **Talent pool (opt-in only):** `[CUSTOMER — period, on recorded candidate consent]`

## J. `[CUSTOMER]` — Sign-off and consultation

- **DPO / privacy advice:** `[CUSTOMER]` · **Consulted candidates' perspective how:** `[CUSTOMER]`
- **Residual risk acceptable?** `[CUSTOMER — yes/no + reasoning]` · **Art 36 prior consultation needed?** `[CUSTOMER — assess]`
- **Sign-off:** `[CUSTOMER name / role / date]`
- **Review cadence:** `[CUSTOMER — recommend quarterly and on any change to role criteria, reviewer process, or Taali's ADM design]`
