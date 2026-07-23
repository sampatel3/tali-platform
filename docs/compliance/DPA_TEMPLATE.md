# Data Processing Agreement (Template)

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> This template implements Horizon 1, item 1 of `docs/COMPLIANCE_RISK_AND_ROADMAP.md`. It is a
> controller→processor DPA for candidate data. It papers Taali as **processor** to its customers
> (controllers). Clauses that need a qualified lawyer are flagged `⚖ COUNSEL`. Fill-in choices for
> Sam or counsel are in `[BRACKETS]`.

---

## 1. Parties and roles

This Data Processing Agreement ("DPA") is entered into between:

- **[CUSTOMER LEGAL NAME]**, `[registered address]` — the **Controller**; and
- **[TAALI LEGAL ENTITY — e.g. "Taali Ltd" / the ADGM entity once incorporated]**, `[registered address]` — the **Processor** ("Taali").

The DPA forms part of, and is governed by, the `[Main Services Agreement / Terms of Service]` between the parties (the "Principal Agreement"). Where this DPA conflicts with the Principal Agreement on the processing of personal data, this DPA prevails.

**Role allocation.** For candidate personal data, the Customer is the Controller and Taali is the Processor. The detailed activity-by-activity allocation is set out in `docs/compliance/CONTROLLER_PROCESSOR_MATRIX.md` and is incorporated here by reference. Taali is a Controller only for the data described in the "Taali as controller" section of `docs/compliance/ROPA.md` (account, billing, site, support data), which is out of scope for this DPA.

`⚖ COUNSEL` — one classification question is open: whether Taali's calibration / re-scoring pipelines keep Taali inside processor scope, or make Taali a joint/independent controller for that activity (ICO "central-model" test, roadmap R3 and §8 Q3). The design rule Taali commits to is that **calibration stays per-customer-scoped**; see the matrix. Until counsel confirms, this DPA governs only processor-scope activity.

## 2. Definitions

Terms not defined here take the meaning given in the applicable data-protection law. "UK GDPR", "EU GDPR", "Data Protection Laws", "personal data", "processing", "controller", "processor", "sub-processor", "data subject", and "personal data breach" are used as defined in the UK GDPR / EU GDPR (Regulation (EU) 2016/679) and, where the Customer or its candidates are in the UAE, the relevant UAE law (see Annex V).

## 3. Subject matter, duration, nature and purpose (Annex I summary)

Full details are in **Annex I**. In summary:

- **Subject matter:** provision of Taali's agentic hiring platform — AI pre-screening, CV↔job scoring with cited evidence, deterministic advance/reject verdicts (advances are queued for the Customer's human sign-off; pre-screen rejections are applied automatically where the Customer leaves that setting enabled, which is the default, and can be turned off per role), AI work-sample assessments, interview-transcript ingestion (where the Customer connects Fireflies), candidate reports, and ATS two-way sync.
- **Duration:** for the term of the Principal Agreement, plus the deletion/return window in clause 12.
- **Nature and purpose:** processing candidate data on the Customer's documented instructions to help the Customer screen, evaluate and make hiring decisions about candidates. Taali does not decide who is hired; the Customer's authorised humans do. Where the Customer leaves automatic pre-screen rejection enabled, Taali applies that rejection against the Customer's own screening rules and threshold without individual human confirmation; the Customer remains the controller for that decision and is responsible for its Art 22 / UK Art 22C position (see `DPIA_VENDOR.md` §4.1 and `DPIA_CUSTOMER_TEMPLATE.md` §C).

## 4. Categories of data and data subjects (Annex I summary)

- **Data subjects:** the Customer's job candidates and applicants; where the Customer connects an interview tool, interview participants.
- **Categories of personal data** (as processed on the platform today — full column-level list in Annex I):
  - Identity and contact: name, email, work email, phone, location city/country, profile and image URLs, headline, summary, current position/company.
  - CV / résumé content: CV file, filename, extracted CV text, parsed CV sections.
  - Structured profile: social profiles, education history, work-experience history, skills, tags.
  - Application content: screening-question answers (free text), recruiter notes.
  - Assessment session telemetry: candidate prompts, Claude responses, file changes, validation runs (Taali does **not** record screen, microphone, or camera).
  - Interview transcripts and audio-derived text (only where the Customer connects Fireflies).
  - Scoring and decision records: scores, cited evidence, rule path, policy revision, verdict, human-override history.
  - Reports and shortlists: candidate reports and submittal snapshots.
  - Raw ATS payloads and identifiers synced from the Customer's ATS (e.g. Workable / Bullhorn records).
- **Special categories (Art 9):** not intentionally processed. Taali does not ask for special-category data. Free-text surfaces (CV text, transcripts, screening answers) can incidentally contain special-category signals; see clause 6(g) and the DPIA. `⚖ COUNSEL` on the sufficiency of filtering (roadmap R5 / §8 Q4).

## 5. Documented instructions

Taali processes candidate data only on the Customer's documented instructions, including on international transfers, unless required to do otherwise by law (in which case Taali informs the Customer first, unless the law prohibits it). The Principal Agreement, this DPA, the product configuration the Customer sets, and any later written instruction the parties agree together are the documented instructions. Taali tells the Customer if, in Taali's opinion, an instruction infringes Data Protection Laws.

## 6. Processor obligations

Taali will:

- **(a) Confidentiality** — ensure people authorised to process candidate data are under a duty of confidentiality.
- **(b) Security (Art 32)** — implement the technical and organisational measures in **Annex II**. These reference Taali's actual controls: tenant-scoped role-based access control; time-limited, revocable candidate-report share links; a durable decision/audit history; encryption of candidate data in transit; and per-call usage logging. Measures are kept appropriate to the risk and may be updated, provided the level of protection is not reduced.
- **(c) Sub-processors** — engage sub-processors only under clause 7.
- **(d) Data-subject requests** — assist the Customer under clause 8.
- **(e) Assistance with obligations** — assist the Customer, taking into account the nature of processing and information available to Taali, with security (Art 32), breach notification (Arts 33–34), data-protection impact assessments (Art 35) and prior consultation (Art 36). Taali's vendor DPIA and customer DPIA template (`docs/compliance/DPIA_VENDOR.md`, `DPIA_CUSTOMER_TEMPLATE.md`) are provided to support this.
- **(f) Breach notification** — notify the Customer under clause 9.
- **(g) Special-category minimisation** — not use candidate data to infer or derive special-category characteristics, and not perform emotion or sentiment inference on candidates (this is a standing product rule; EU AI Act Art 5(1)(f)). `⚖ COUNSEL` on filtering of incidental special-category content in free-text inputs.
- **(h) Records** — maintain records of processing carried out for the Customer (Art 30(2)); see `docs/compliance/ROPA.md`.
- **(i) Audit** — make available the information needed to show compliance, under clause 11.
- **(j) Deletion / return** — under clause 12.

## 7. Sub-processor authorisation

- **(a) General authorisation.** The Customer gives Taali general written authorisation to engage sub-processors to process candidate data, provided Taali complies with this clause.
- **(b) Public list.** The current sub-processors — with name, function, processing location, and transfer mechanism — are published at **taali.ai/subprocessors** and incorporated here by reference. **Annex III** reproduces the list as at the DPA date. `[NOTE: the public subprocessor page is Horizon-1 item 2 and must be live before this clause is relied on.]`
- **(c) Flow-down.** Taali imposes on each sub-processor data-protection obligations that are, in substance, the same as those in this DPA, by written contract. Taali remains liable to the Customer for a sub-processor's failure to meet those obligations.
- **(d) Change notice.** Taali gives the Customer at least **`[30]` days'** notice before adding or replacing a sub-processor (via the public page and `[email to the Customer's notified contact]`). Within that period the Customer may object on reasonable data-protection grounds. If the parties cannot resolve the objection, the Customer may terminate the affected Services under the Principal Agreement.

## 8. Data-subject requests (DSRs)

- **(a) Routing.** If a candidate contacts Taali directly to exercise a right, Taali will not respond on the merits (except to confirm receipt and redirect) and will forward the request to the Customer without undue delay. Taali's intake channel is `hello@taali.ai`, routed to the Customer as Controller.
- **(b) Assistance.** Taali assists the Customer to respond to access, rectification, erasure, restriction, portability, objection, and automated-decision requests, using the platform's data-subject-request tooling (access export and erasure; see `backend/app/domains/compliance/`).
- **(c) SLA.** Taali **acknowledges** a Customer-relayed DSR within **3 working days** and **completes** its assistance within **30 calendar days** of a validated request, or sooner where the Customer's own statutory deadline is shorter and the Customer tells Taali of it.
- **(d) Automated decisions / contest.** For requests about a Taali verdict, Taali supports the Customer's response under `docs/compliance/CANDIDATE_CONTEST_PROCESS.md` (information, representations, human intervention, contest).
- **(e) Erasure scope.** Taali's erasure scope is documented honestly: today it scrubs the primary candidate record; a cross-table sweep and a re-import suppression list are on the Horizon-2 roadmap (roadmap R8). `[Customer to be told the current scope during onboarding.]`

## 9. Personal-data breach notification

Taali notifies the Customer of a personal-data breach affecting candidate data **without undue delay** after becoming aware of it, **targeting notification within 72 hours**. The notification will describe, to the extent known, the nature of the breach, the categories and approximate number of data subjects and records affected, likely consequences, and the measures taken or proposed. Taali provides reasonable cooperation to help the Customer meet its own Art 33/34 duties. Taali does not notify a supervisory authority or data subjects on the Customer's behalf unless the Customer instructs it in writing.

## 10. International transfers

- **(a) Where processing happens.** Candidate data is processed in the United States today (Railway `us-east4` for API/Postgres/Redis; Vercel for the frontend), plus the sub-processor locations in Annex III. An EU hosting region is on Taali's 2027 roadmap.
- **(b) EU transfers — SCCs.** For transfers of EU candidate data to a country without an adequacy decision, the parties incorporate the **EU Standard Contractual Clauses (Commission Implementing Decision (EU) 2021/914), Module Two (controller-to-processor)** by reference, with the docking clause enabled, the Annexes populated from Annexes I–III of this DPA, `[Option 2 / general authorisation]` at Clause 9, and the governing law and forum at `[Member State — e.g. Ireland]`. Where a sub-processor is certified under the EU–US Data Privacy Framework, that transfer may rely on the DPF instead; the mechanism per sub-processor is recorded in Annex III.
- **(c) UK transfers — Addendum / Data Bridge.** For transfers of UK candidate data, the parties incorporate the **UK Addendum to the EU SCCs (IDTA Addendum, ICO version B1.0)**, completing Table 1 (parties), Table 2 (the Module-Two SCCs above), Table 3 (Annexes), and Table 4 `[importer may end the Addendum]`. Where a US sub-processor is certified under the DPF **and the UK Extension ("Data Bridge")**, that transfer may instead rely on the Data Bridge; the mechanism per sub-processor is in Annex III.
- **(d) DPF reliance note.** Some sub-processors (e.g. Anthropic; `[verify Stripe, GitHub/Microsoft]`) are DPF-certified and rely on the DPF / UK Extension. The DPF is valid today but under appeal at the CJEU (C-703/25 P). If the DPF is invalidated, the SCC / UK Addendum modules in (b)–(c) apply as the fallback without further amendment.
- **(e) Transfer risk assessments.** `[Whether a TRA is needed per sub-processor is a ⚖ COUNSEL question — roadmap §8 Q6.]`

## 11. Audit rights

Taali makes available to the Customer the information reasonably necessary to demonstrate compliance with this DPA and Art 28, and allows for and contributes to audits, including inspections, conducted by the Customer or an auditor it mandates. To limit disruption, the parties agree audits are satisfied first by Taali providing its compliance pack (this pack, security documentation, and — when available — third-party certifications such as SOC 2), and that on-site or bespoke audits are `[limited to once per 12 months, on 30 days' notice, at the Customer's cost, under confidentiality, unless a breach or regulator requires otherwise]`.

## 12. Deletion and return on termination

On termination or expiry of the Principal Agreement, Taali, at the Customer's choice, deletes or returns all candidate data and deletes existing copies, unless law requires storage. The Customer makes the deletion/return election within `[30]` days of termination; absent an election, Taali `[deletes]` after `[90]` days. Data retained by law is protected under this DPA until deletion. Retention defaults while the engagement is live are in `docs/compliance/RETENTION_SCHEDULE.md`.

## 13. Liability

Each party's liability under this DPA is subject to the limitations and exclusions of liability in the Principal Agreement. `[BRACKET — cross-reference the exact liability clause of the Main Services Agreement; counsel to confirm the SCCs' own liability terms (Clause 12) are not undercut, since SCC liability cannot be limited as against data subjects.]`

## 14. General

Governing law and jurisdiction follow the Principal Agreement, except that the SCCs and UK Addendum carry their own governing-law and forum terms for transfer matters. Changes to this DPA are by written agreement, except that Taali may update Annexes II and III to reflect improved security or authorised sub-processor changes under clauses 6(b) and 7.

---

## Annex I — Details of processing

| Item | Detail |
|---|---|
| Controller | `[CUSTOMER]` |
| Processor | Taali (`[legal entity]`) |
| Subject matter | AI hiring platform (screening, scoring, verdicts, assessments, transcripts, reports, ATS sync) |
| Duration | Term of the Principal Agreement + deletion/return window (clause 12) |
| Nature and purpose | Screen, evaluate, and support human hiring decisions about the Controller's candidates, on the Controller's instructions |
| Data subjects | The Controller's candidates and applicants; interview participants (where Fireflies connected) |
| Data categories | Identity/contact; CV content; structured profile; application content (screening answers, notes); assessment session telemetry; interview transcripts (if connected); scoring/decision records; reports/shortlists; raw ATS payloads. Column-level list mirrors `_ERASE_FIELDS` in `backend/app/domains/compliance/data_subject_service.py`. |
| Special categories | None intentionally processed; incidental free-text signals possible (clause 4) |
| Frequency | Continuous, for the term |
| Retention | Per `docs/compliance/RETENTION_SCHEDULE.md` and the Customer's configured period |

## Annex II — Technical and organisational security measures (Art 32)

These describe Taali's **actual** controls; each is kept appropriate to the risk.

| Measure | Control |
|---|---|
| Access control | Tenant-scoped role-based access control; candidate data isolated per organization |
| Sharing control | Candidate-report share links are time-limited and revocable |
| Accountability / logging | Durable decision and audit history (policy revision, rule path, overrides); every AI call writes a usage event (CI-enforced) |
| Encryption | Candidate data encrypted in transit (TLS) `[at-rest encryption: confirm provider default and state it]` |
| Data minimisation | No screen / microphone / camera / webcam-proctoring capture in assessments; disclosed to candidates at session start |
| Deletion | Data-subject erasure tooling scrubs the primary candidate record (scope in clause 8(e)) |
| Supplier management | Sub-processors under written flow-down contracts (clause 7) |
| `[Add as verified]` | `[e.g. backups, secrets management, vulnerability management, MFA on admin access — populate from real infra before signature]` |

## Annex III — Sub-processors (as at DPA date)

Canonical, live list: **taali.ai/subprocessors**. DPF statuses below were verified against vendor privacy notices/changelogs on **2026-07-23**; re-confirm each on dataprivacyframework.gov at DPA signature (roadmap R6).

`[CONFIRM]` — object storage is S3-compatible and the provider is set by deployment config (`AWS_S3_ENDPOINT_URL`: unset = AWS S3, or Tigris / Cloudflare R2 / MinIO). The row below assumes the AWS S3 default. Confirm the production endpoint and correct this row plus the public page if production points elsewhere.

| Sub-processor | Function | Location | Transfer mechanism |
|---|---|---|---|
| Anthropic | AI model processing (scoring, assessment chat) | US | EU–US DPF + UK Extension (certified, verified 2026-07-23) |
| Railway | Hosting — API, Postgres, Redis (`us-east4`) | US | EU–US DPF + UK Extension + Swiss DPF (certified, verified 2026-07-23) |
| Vercel | Frontend hosting / CDN | US / global edge | EU–US DPF + UK Extension + Swiss DPF (certified, verified 2026-07-23) |
| E2B | Assessment sandbox execution | US | SCCs / UK Addendum (DPF status unverified — treat as not certified) |
| Fireflies | Interview transcription (only if Customer connects it) | US | EU–US DPF (certified, verified 2026-07-23); SCC fallback per its DPA |
| Resend | Transactional email | US | EU–US DPF + UK Extension (certified, verified 2026-07-23) |
| Stripe | Billing (Taali-controller data; listed for completeness) | US | EU–US DPF + UK Extension (certified, verified 2026-07-23); SCC fallback |
| Amazon Web Services (S3) | Object storage — CV / job-spec uploads, cached report documents (`AWS_S3_BUCKET`, default region `us-east-1`) | US | EU–US DPF + UK Extension + Swiss DPF (certified, verified 2026-07-23) |
| GitHub / Microsoft | Assessment repositories | US | EU–US DPF + UK Extension + Swiss DPF (certified, verified 2026-07-23) |
| Neo4j (Aura) | Candidate evidence graph (where enabled) | US | EU–US DPF + UK Extension + Swiss DPF (certified, verified 2026-07-23) |
| Voyage (MongoDB) | Embeddings for the evidence graph | US | EU–US DPF + UK Extension + Swiss DPF (MongoDB group certification, verified 2026-07-23) |

## Annex IV — Standard Contractual Clauses

The EU SCCs (2021/914) Module Two and the UK Addendum are incorporated per clause 10. Their Annexes are populated from Annexes I–III above. `[Attach the completed SCC + Addendum forms as executed schedules; counsel to finalise the elected options.]`

## Annex V — UAE annex (stub) `⚖ COUNSEL`

For Customers or candidates in the UAE, and pending the Taali ADGM entity:

- **Federal PDPL (Decree-Law 45/2021), Art 23 transfer basis.** Where no adequacy decision applies, transfers of UAE candidate data outside the UAE rely on `[a contract binding the recipient to PDPL-equivalent standards / the candidate's explicit consent / contractual necessity]`. `[PLACEHOLDER — Art 23-compliant contract wording to be drafted; the PDPL executive regulations are not yet issued as of mid-2026, so the exact required form is unresolved.]` `⚖ COUNSEL` (roadmap R10 / §8 Q7).
- **ADGM (once the entity is incorporated).** For flows from the ADGM entity to the US, incorporate the **ADGM Office of Data Protection Addendum to the EU SCCs**; register/notify with the ODP; maintain a DP policy and DPO-appointment assessment. `⚖ COUNSEL` on incorporation sequencing.
- **DIFC (if DIFC-based Customers).** Incorporate DIFC SCCs (DIFC DP Law 2020, Arts 26–27) for DIFC→US flows.

## Annex VI — Interview-recording consent allocation `⚖ COUNSEL`

Where the Customer connects Fireflies to transcribe interviews:

- The **Customer, as Controller, is solely responsible** for establishing and evidencing the lawful basis for recording and transcribing interviews, including obtaining any consent required from interview participants under the applicable jurisdiction's call-recording and ePrivacy rules.
- Taali processes the resulting transcripts only as processor, on the Customer's instructions.
- Fireflies is a sub-processor (Annex III); DPF-certified (verified 2026-07-23), SCC fallback per its DPA.

`⚖ COUNSEL` — allocation of recording-consent responsibility across EU member states and the UAE, and confirmation this clause is sufficient (roadmap R13 / §8 Q8).
