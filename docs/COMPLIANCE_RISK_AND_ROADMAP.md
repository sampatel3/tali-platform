# Compliance Risk Assessment & Roadmap — UK / EU / UAE Hiring

**Date:** 2026-07-23 · **Status:** Research deliverable, pre-counsel
**Scope:** Selling Taali (agentic hiring platform) to recruitment agencies and in-house teams in the UK, EU, and UAE.

> **This document is research to prepare for legal counsel, not legal advice.**
> It maps the regulatory landscape against Taali's actual product controls so a lawyer
> can be briefed efficiently. Questions that need a qualified lawyer are flagged
> `⚖ COUNSEL` throughout and collected in §8. Regulatory facts below were verified
> against primary sources (EUR-Lex, legislation.gov.uk, ICO publications, official
> DIFC/ADGM materials) in July 2026; the law is moving — re-verify before relying.

---

## 0. TL;DR

0. **Correction to the original premise — Taali does make solely automated decisions today, by default.** The brief described verdicts as "queued for human sign-off," and that is true for *advances*. It is not true for *rejections at pre-screen*: `auto_reject_pre_screen` defaults to `True`, and a below-threshold LLM-derived pre-screen score results in an automatic rejection with no human in the loop. That is the GDPR's own textbook Art 22 case. **This is risk R0 and the single most important item in this document** — it needs a decision from Sam (see §5, Horizon 1, item 0) before EU candidates run through a default-configured role. It does not require abandoning the feature: the UK permits it with safeguards, and the EU route needs a gateway.
1. **After R0, the load-bearing control is meaningful human review.** In the EU, the CJEU's SCHUFA judgment (C-634/21) means Taali's deterministic advance/reject verdicts can *themselves* be the GDPR Art 22 "decision" — at the vendor — whenever recruiters draw strongly on them. Client-side sign-off does not automatically immunize the platform, and regulators say human involvement "cannot be a token gesture or a rubber stamp." Taali must be able to *evidence* meaningfulness (reviewer authority, evidence access, non-trivial override rates), not assert it.
2. **The UK just got more permissive — with hard edges.** Since 5 Feb 2026, DUAA Arts 22A–22D permit solely automated significant decisions on ordinary lawful bases, subject to a four-part safeguard checklist (information, representations, human intervention, contest) that maps directly onto buildable product features. But decisions based on special-category data remain prohibited without explicit consent, and CV/transcript/telemetry data can leak special-category signals.
3. **The EU AI Act clock moved in Taali's favour.** The Digital Omnibus (Parliament 16 Jun 2026, Council 29 Jun 2026) defers Annex III high-risk obligations — which cover recruitment AI — from 2 Aug 2026 to **2 Dec 2027** as a fixed date. The deck's Dec 2027 citation is correct. But Art 50 transparency (tell people they're interacting with AI) still applies from **2 Aug 2026**, and the Art 5 prohibitions (including workplace emotion inference) have applied since Feb 2025.
4. **US-only hosting is lawful today but fragile.** The EU–US Data Privacy Framework survived its first challenge (Sept 2025) but the appeal (C-703/25 P) is pending at the CJEU. Taali needs the paperwork stack now (DPA + SCCs/UK Addendum fallback, subprocessor transfer matrix) and a hosting-region decision within 12 months — which also fixes the documented UAE latency problem.
5. **The biggest self-inflicted risks are documentation gaps, not architecture.** No privacy notice or terms pages exist (RegisterPage references them), no DPA template, no subprocessor list, no DPIA, no retention schedule, and erasure is single-table by design. All fixable before the next pilot; none require re-architecture.
6. **Biggest classification risk:** ICO's position is that a vendor that uses candidate data to improve a central model deployed to all customers is a **controller**, not a processor. Taali's calibration and re-scoring pipelines sit close to this line. `⚖ COUNSEL`

---

## 0b. Implementation status — Horizon 1 shipped (2026-07-23, same branch)

The H1 "before next pilot" items in §5 were implemented alongside this assessment; §4/§6 describe the state *before* that work:

- **Public pages live**: `/privacy`, `/terms` (pre-counsel drafts, bracketed decisions visible), `/subprocessors` (10 subprocessors, per-subprocessor transfer mechanism, DPF statuses verified 2026-07-23 — all certified except E2B → SCCs). RegisterPage links fixed; landing footer links added; sitemap updated.
- **Compliance pack**: `docs/compliance/` — DPA template, vendor DPIA, customer DPIA template, ROPA, candidate privacy-notice template, controller/processor matrix, candidate contest process (ack 3 working days / resolve 30 days), retention schedule policy (scheduler itself remains H2), pack INDEX with counsel checklist. All DRAFT-marked for counsel.
- **Code**: dormant `sentiment_trajectory` field removed from scoring schemas with an Art 5(1)(f) fencing comment (verified dormant repo-wide); Art 50 transparency audit written (`docs/compliance/ART50_AI_TRANSPARENCY_AUDIT.md`) — one gap found and fixed (assessment welcome page now says "Claude, an AI assistant"); all other candidate-facing surfaces already disclose AI involvement.
- **Deck**: slide 08 now carries the §6-recommended wording ("Operated in line with UK and EU data-protection requirements, under each customer's instructions") with the compliance-pack list and subprocessor-page link — accurate because it ships together with the artifacts above.
- **Correction found during implementation (R0)**: the "every verdict is queued for human sign-off" description in the original brief is wrong for rejections. `auto_reject_pre_screen` defaults to `True`, so below-threshold and knockout rejections apply automatically. The published `/privacy` and `/terms` pages, the compliance pack, and the deck's case-study claim were all corrected to state this accurately. The product default itself is **deliberately unchanged** pending Sam's decision — see §5 Horizon 1 item 0.
- **Still open**: everything in H2/H3 (meaningful-HITL evidence pack, erasure sweep v2 + retention scheduler, bias pipeline, AI Act provider file, hosting region), plus every `⚖ COUNSEL` item — the pack is drafted, not lawyer-approved.

## 1. What Taali is, in regulator's terms

| Product fact | Regulatory characterization |
|---|---|
| AI pre-screening + CV↔JD scoring (Claude), verbatim-citation evidence | AI system for "recruitment or selection of natural persons… to analyse and filter job applications, and to evaluate candidates" — **EU AI Act Annex III 4(a) high-risk** (obligations from 2 Dec 2027); profiling under GDPR/UK GDPR |
| Deterministic rule-based advance/reject verdicts. **Advances** are queued for human sign-off (HITL). **Pre-screen rejects auto-apply by default** (`auto_reject_pre_screen: True`) | Automated decision-making. The advance path's exposure turns on whether human review is *meaningful*. The pre-screen reject path has **no human in the loop at all** by default — a solely automated decision in the GDPR's own canonical example (Recital 71, e-recruiting). See **R0**, the highest-severity row in the register |
| AI-native work-sample assessments with full session telemetry | Candidate evaluation + monitoring; disclosed at session start (good); also a data-minimisation and special-category-leakage surface |
| Interview transcripts (Fireflies) | Voice + transcript data at a US subprocessor; recording-consent rules vary by jurisdiction `⚖ COUNSEL` |
| Candidate reports shared to clients | Disclosure to third parties; share-link governance; frozen snapshots embed PII |
| Workable/Bullhorn two-way sync, decision write-back | Taali writes hiring outcomes into customer systems of record; erased candidates can re-sync back in |
| US-only hosting (Railway us-east4, Vercel) | Restricted international transfer for every UK/EU/UAE candidate record |

**Roles:** Taali is a **processor** to its customers (controllers) for candidate data — *provided* it stays on the right side of the ICO's controller test (§3, R3). Under the EU AI Act Taali is the **provider**; customers are **deployers**. Taali is a controller for its own account/billing/usage data.

---

## 2. Verified regulatory baseline

Facts below were adversarially verified against primary sources (multiple independent checks per claim). Confidence is noted where a claim rests on analogy or secondary corroboration.

### 2.1 EU — GDPR Article 22 and SCHUFA

- **Art 22(1) is a prohibition in principle, not a right the candidate must invoke.** Solely automated decisions with legal or similarly significant effects are lawful only through an Art 22(2) gateway: contractual necessity, EU/Member State law authorisation, or explicit consent. (SCHUFA paras 52–53, verified verbatim on EUR-Lex, CELEX 62021CJ0634.)
- **Recruitment is the GDPR's own named example.** Recital 71 cites "e-recruiting practices without any human intervention"; the EDPB-endorsed WP251rev.01 guidelines list "decisions that deny someone an employment opportunity" as similarly significant. (Verified against the official WP251rev.01 PDF.)
- **SCHUFA (C-634/21, 7 Dec 2023): the score itself can be the decision.** The automated establishment of a probability value is Art 22(1) automated decision-making *at the scoring vendor* where a third party "draws strongly" on it. The Court expressly rejected the "preparatory act" reading because it would create "a risk of circumventing Article 22… and, consequently, a lacuna in legal protection" (para 61). In SCHUFA the bank's humans made the formal final decision — the vendor's score was still the Art 22 decision. **Direct analogy to Taali's vendor-verdict + client-HITL structure.** The extension from credit scoring to hiring is the mainstream practitioner reading but has not been decided by a court for recruitment. `⚖ COUNSEL`
- **Human oversight only takes a decision out of Art 22 if it is meaningful**: someone with "the authority and competence to change the decision" who "consider[s] all the relevant data." Controllers "cannot avoid the Article 22 provisions by fabricating human involvement." (WP251rev.01, verbatim; applied by DPAs, e.g. Spain's AEPD.)
- **DPIAs are mandatory regardless of HITL.** WP251rev.01 reads Art 35(3)(a) as requiring a DPIA for profiling-based significant decisions *even when not wholly automated*, and says the DPIA should record the degree and stage of human involvement.
- **Practical consequence:** if Taali's verdicts are found solely automated in the EU, explicit candidate consent is realistically the only workable gateway for a vendor (contract-necessity runs between employer and candidate; no authorising law exists). `⚖ COUNSEL` on consent architecture — freely-given consent in recruitment contexts is contested.

### 2.2 UK — DUAA 2025 (Arts 22A–22D) and the ICO

- **The old Art 22 is gone.** DUAA 2025 s.80 replaced UK GDPR Art 22 with Arts 22A–22D, in force **5 Feb 2026** (S.I. 2026/82); as of 19 Jun 2026 all DUAA data-protection provisions are in force. UK compliance documents citing old Art 22 or the "three gateways" as current UK law are wrong — several claims failed verification on exactly this point.
- **The reformed regime is permissive with hard edges.** Solely automated significant decisions may rest on any ordinary lawful basis (potentially legitimate interests) **provided Art 22C safeguards apply**: (a) information about the decision, (b) enable representations, (c) enable human intervention, (d) enable contesting. Art 22B prohibits solely automated significant decisions based on **special-category data** unless explicit consent (covering *all* data relied on) or contract/law necessity plus a substantial-public-interest condition; and bars them entirely on the new "recognised legitimate interests" basis (Art 6(1)(ea)).
- **"Solely automated" = no meaningful human involvement** (Art 22A(1)(a)), with the extent of profiling a mandatory factor. The ICO's March 2026 position: human involvement must be "meaningful and active… not a token gesture or a rubber stamp"; the reviewer needs real influence, authority, discretion, competence. Its 2025–26 recruitment evidence-gathering (30+ employers) found **most tools believed to be "decision support" were in practice solely automated** — the regulator's default assumption is that HITL claims fail.
- **ICO AI-in-recruitment audit (Nov 2024, 296 recommendations)** — still-operative substance: providers should design tools to *prevent* recruiters progressing/rejecting candidates based solely on AI grades or scores; candidates need transparent privacy information including a clear retention period, and a simple way to object to or challenge automated decisions.
- **Controller status:** the ICO holds an AI recruitment vendor is a **controller** where it exercises overall control in practice — expressly including using candidate data collected on recruiters' behalf "to develop a central AI model that they deploy to all recruiters." Audited providers had wrongly self-classified as processors behind deliberately broad contracts.
- **Bias monitoring cannot use inferred demographics.** Inferred gender/ethnicity (e.g. from a name) is still special-category data and "will not be adequate and accurate enough" — providers must collect demographic data directly (optional post-assessment survey, explicit consent, Art 9 condition).
- **In flux:** ICO ADM guidance is under review (consultation closed 29 May 2026, final guidance pending); a statutory AI/ADM code of practice is pending (SI 2026/425). UK positions on "meaningful human involvement" may tighten. `⚖ COUNSEL` to track.

### 2.3 EU AI Act — current timeline (verified July 2026)

- **The Digital Omnibus is adopted.** European Parliament 16 Jun 2026; Council final approval 29 Jun 2026; publication in the Official Journal expected July 2026, in force before the original 2 Aug 2026 deadline. **The final OJ text must be reviewed by counsel once published.** `⚖ COUNSEL`
- **New dates (fixed calendar dates, not standards-dependent):** stand-alone Annex III high-risk obligations — including **Annex III 4(a) recruitment/selection systems** — apply from **2 December 2027** (Annex I embedded systems: 2 Aug 2028).
- **What still applies on the original schedule:**
  - **Art 5 prohibitions — in force since 2 Feb 2025** — include emotion inference in the workplace (Art 5(1)(f)). Directly relevant: Taali must never ship sentiment/emotion analysis of candidates (see R12).
  - **Art 4 AI literacy** (providers and deployers) — since 2 Feb 2025.
  - **GPAI obligations** — since 2 Aug 2025 (Anthropic's problem as model provider, but Taali should confirm flow-down).
  - **Art 50 transparency — from 2 Aug 2026, unamended by the omnibus:** people must be told they are interacting with an AI system; synthetic content must be machine-readable-marked (Art 50(2) grace to 2 Dec 2026 for systems placed on market before Aug 2026). Taali's candidate-facing AI interactions (assessment Claude chat, any candidate-facing agent surfaces) need explicit AI-interaction labelling by 2 Aug 2026 — mostly already true in the product, but should be audited.
- **Provider obligations to be ready for by Dec 2027** (Arts 8–21 via Art 16): risk-management system (Art 9), data governance (Art 10 — including bias examination; Art 10(5) permits special-category processing strictly necessary for bias detection/correction, with safeguards), technical documentation (Art 11), automatic logging (Art 12 — Taali's telemetry is a genuine head start), transparency to deployers (Art 13), human-oversight design (Art 14 — HITL queue is a head start), accuracy/robustness/cybersecurity (Art 15), QMS (Art 17), conformity assessment (Art 43 — for Annex III 4 systems this is the **internal-control route**, Annex VI, no notified body needed `⚖ COUNSEL` to confirm on final text), EU declaration of conformity (Art 47), CE marking (Art 48), **registration in the EU database before placing on market** (Art 49).
- **Deployer obligations** (customers): human oversight, input-data quality, log retention, worker information — Taali's sales collateral should map how the product discharges these for deployers; deployer high-risk duties follow the Dec 2027 date, Art 50 deployer transparency from Aug 2026.

### 2.4 International transfers — US-only hosting

- **EU–US Data Privacy Framework: valid today, under appeal.** General Court dismissed Latombe (T-553/23) on 3 Sept 2025; appeal pending at the CJEU (C-703/25 P, filed 31 Oct 2025, no hearing date as of mid-2026). The CJEU has twice struck down predecessor frameworks. Plan on SCC fallback.
- **UK: the UK Extension ("Data Bridge") has been live since 12 Oct 2023** — UK transfers to US companies certified under DPF + UK Extension need no IDTA/Addendum. Otherwise: IDTA or UK Addendum to EU SCCs + transfer risk assessment.
- **Subprocessor status:** Anthropic is DPF-certified (active as of 2026). The certification status of Railway, E2B, Resend, Fireflies, Neo4j (Aura), and Voyage must be checked one by one on dataprivacyframework.gov and the result recorded in a subprocessor matrix; non-certified ones need SCCs in the DPA chain. (GitHub/Microsoft and Stripe are certified; verify current status when building the matrix.)
- **Where is the data?** answer for a DPO today: "US (Railway us-east4 + Vercel CDN), under [DPF certification / SCCs] with each subprocessor listed at [subprocessor page]" — every bracket must exist before the sentence works.

### 2.5 UAE — PDPL, DIFC, ADGM

- **Federal PDPL (Decree-Law 45/2021):** in force since Jan 2022, but the **executive regulations are still not issued** as of mid-2026 (sources conflict; some claim 2024 finalization — treat as unresolved `⚖ COUNSEL`). The UAE Data Office's enforcement activity has grown since 2025 (breach notification, security measures). Cross-border transfers: Art 22 (adequacy — criteria await the executive regulations) and Art 23 (absent adequacy: contract binding recipient to PDPL standards, explicit consent, contractual necessity, public interest). Practical posture: PDPL-compliant DPA language + explicit-consent fallback until the regulations land.
- **ADGM DPR 2021 (relevant — ADGM entity planned via Hub71):** GDPR-style regime with its own adequacy list and transfer safeguards; ADGM's Office of Data Protection has published an **Addendum to the EU SCCs** usable as an appropriate safeguard for transfers from ADGM to non-adequate jurisdictions (which includes the US). An ADGM entity processing candidate data will need: registration/notification with the ODP, a data-protection policy, appointment assessment for a DPO, and ADGM-Addendum SCCs covering the flow ADGM → US (Railway). `⚖ COUNSEL` for ADGM incorporation sequencing.
- **DIFC DP Law 2020 (if DIFC-based customers):** adequacy-first (Art 26) with DIFC SCCs and other Art 27 safeguards for the rest; the US is not on the DIFC adequacy list, so DIFC-customer contracts should incorporate DIFC SCCs.

### 2.6 Adjacent obligations

- **UK Equality Act 2010:** employers are liable for discriminatory outcomes of AI tools they adopt — "the software decided" is no defence; liability attaches to effect, not intent. Indirect discrimination via facially neutral scoring criteria is the main exposure. Customers will (and should) demand bias-testing evidence and contractual warranties from Taali. Retention interacts here: the Equality Act claim window (6 months) anchors the ICO's norm that unsuccessful-applicant data shouldn't outlive the claim period absent a documented reason.
- **NYC Local Law 144 (template if US sales happen):** annual independent bias audit of automated employment decision tools (selection rates + impact ratios per race/ethnicity and sex, four-fifths-rule benchmark), a published summary, and 10-business-day advance notice to candidates. Useful as the *shape* of a bias-audit artifact even where not legally required.
- **Retention norms:** GDPR/UK GDPR storage limitation requires justified, documented, enforced periods. Sector norm for unsuccessful applicants: **6–12 months** post-process (UK: 6-month Equality Act claim window as anchor), longer only with candidate consent (e.g. talent-pool opt-in, commonly 12 months). Taali currently retains indefinitely — see R8.

---

## 3. Risk register

Severity: **H** = could block a sale, trigger enforcement, or require re-architecture if ignored; **M** = manageable now, expensive later; **L** = monitor.

| # | Risk | Sev | Regulation | What triggers it | Current mitigation | Gap |
|---|---|---|---|---|---|---|
| **R0** | **Default-on automatic rejection at pre-screen — a genuinely solely-automated hiring decision.** `auto_reject_pre_screen` defaults to `True` (`backend/app/services/agent_policy_settings.py`). Two paths auto-reject with no human: recruiter-authored knockout answers (`domains/job_pages/knockout_automation.py`) and **below-threshold pre-screen scores**, where the score is LLM-derived (`services/pre_screening_service.py` — `llm_score_100`) and the reject rule applied to it is deterministic. Rejecting an applicant is the GDPR's own named example of a similarly significant effect | **H — highest** | GDPR Art 22 (prohibition in principle, needs an Art 22(2) gateway); UK Arts 22A–22D (permitted, but only with the full Art 22C safeguard set); SCHUFA at vendor level | Every EU/UK candidate auto-rejected at pre-screen on a default-configured role, today | Deterministic rule applied to the score; per-role opt-out; auto-reject state, reason and timestamp recorded; ATS write-back failure falls back to a Decision Hub card; role lock prevents acting on paused/off roles | **No Art 22(2) gateway for the EU.** Candidates are not individually informed a solely automated decision was made about them. Human-intervention and contest rights now exist as a documented route but are not surfaced at the point of rejection. No jurisdiction-awareness: an EU-candidate role gets the same default as a UK one |
| R1 | **Vendor-level Art 22 exposure (EU):** Taali's verdict is itself the automated decision if recruiters draw strongly on it; client HITL doesn't immunize the platform | H | GDPR Art 22, SCHUFA C-634/21 | Selling into EU; any customer whose recruiters follow verdicts near-mechanically | Deterministic rule path + policy revision recorded; evidence attached to every verdict; human sign-off queue | No Art 22(2) gateway established for the EU (explicit-consent flow doesn't exist); no candidate representations/contest route; no evidence that client review is meaningful in practice |
| R2 | **Rubber-stamp HITL:** regulators' default assumption is that "decision support" is solely automated in practice | H | UK Art 22A + ICO Mar 2026; EU WP251rev.01 | ICO audit/complaint; DPO diligence; one-click approve behaviour in real usage | Reviewer sees evidence + rule path; overrides recorded; decision history exportable | No measured override rates; no reviewer-competence/authority definition; approve flow is low-friction by design; no "meaningfulness evidence pack" to show a regulator |
| R3 | **Controller misclassification:** using customer candidate data to calibrate/improve central scoring makes Taali a controller with full direct obligations | H | UK GDPR (ICO audit position); EDPB 07/2020 | Calibration pipeline, cross-customer recalibration, prompt/rubric tuning on live candidate data | Tenant scoping; "we never train models on your candidate data" claim on RegisterPage | That claim is only true for *model training* — calibration/re-scoring pipelines on candidate data sit close to the ICO's "central model improvement" line; role allocation never papered `⚖ COUNSEL` |
| R4 | **No candidate transparency artifacts:** Art 13/14 notices, retention period, ADM information duties | H | GDPR/UK GDPR Arts 12–14; UK Art 22C(a); ICO audit recs | First DPO review; any candidate complaint; register page references non-existent Terms/Privacy | Assessment telemetry disclosure at session start (genuinely good); score provenance shown | No privacy notice exists anywhere; no candidate-facing ADM explanation; no retention period stated; Terms/Privacy links dangle |
| R5 | **Special-category leakage into verdicts:** CVs, transcripts, telemetry carry health/ethnicity/religion signals; UK Art 22B then prohibits solely automated decisions absent explicit consent to *all* data relied on | M–H | UK Art 22B; GDPR Art 9 | Any verdict influenced by special-category signals in source docs | Rule-based verdict layer is deterministic on defined criteria (limits stray inputs) | No filtering/masking policy for special-category content in CV/transcript inputs to scoring; no documented analysis `⚖ COUNSEL` |
| R6 | **Transfer stack incomplete:** US-only hosting serving UK/EU/UAE candidates | M (H if DPF falls) | GDPR Ch V; UK IDTA/Addendum/Data Bridge; PDPL Arts 22–23; ADGM DPR | Every candidate record, today; DPF appeal C-703/25 P | Anthropic DPF-certified; DPF + UK Extension valid today | No DPA template, no SCC/IDTA fallback module, no per-subprocessor transfer matrix (Railway/E2B/Fireflies/Neo4j/Voyage unverified), no TRAs, no public subprocessor list |
| R7 | **EU AI Act provider obligations (due 2 Dec 2027):** QMS, risk mgmt, data governance, technical docs, conformity assessment, CE, EU database registration | M now → H mid-2027 | Reg 2024/1689 as amended by omnibus; Annex III 4(a) | Selling into EU after Dec 2027 without the stack | Art 12-grade logging/telemetry already strong; Art 14-grade oversight design exists | Everything document-shaped: QMS, risk mgmt file, Art 11 tech docs, bias-testing framework, conformity assessment, registration |
| R8 | **Retention + erasure incompleteness:** indefinite retention; single-table erasure leaves PII in applications (cv_text), interviews, report snapshots, prospects, assessment artifacts; **the stored CV file itself survives in object storage** — erasure clears `cv_file_url` on the candidate row but never calls `delete_from_s3`, so the richest single PII artifact persists; erased candidates can re-sync from ATS | H | GDPR Art 5(1)(e), Art 17; ICO retention norms | Any erasure request today only partially erases; DPO asks "what's your retention schedule" | DSR machinery with durable evidence log; honest documented scope; `delete_from_s3` already exists and is callable | Cross-table sweep unimplemented (documented as out of scope); **object-storage deletion not wired into erasure**; no retention scheduler; no re-import suppression |
| R9 | **Bias testing without lawful demographic data:** inferred demographics are unlawful for bias monitoring (UK); EU AI Act Art 10 requires bias examination by Dec 2027 | M | ICO audit report; EU AI Act Art 10(2)(f)/(5); Equality Act (customer pressure) | Building bias metrics from names/inferences; or having no bias evidence when customers ask | Voluntary self-ID on the native apply flow (optional, token-scoped, stored segregated from scoring); owner-only aggregate EEO report (k-anonymity suppressed); 4/5ths adverse-impact script (`backend/scripts/adverse_impact_report.py`); continuous bias-monitor capability; no protected attributes in the schema by policy | Self-ID covers only native applies (not ATS-synced or assessment candidates); framing is US EEO/OFCCP, needs UK/EU explicit-consent + Art 9-condition wording; reporting is operator-run, not scheduled monitoring fed back into design |
| R10 | **UAE regime uncertainty + ADGM entity:** PDPL exec regs pending; ADGM DPR applies fully to the planned entity | M | PDPL 45/2021; ADGM DPR 2021 | ADGM incorporation (Hub71); UAE customer DPO diligence | None UAE-specific | No PDPL-aligned DPA language, no ADGM registration/policy/DPO assessment, no ADGM-Addendum SCCs for ADGM→US flow |
| R11 | **Marketing overclaim:** compliance claims ahead of documentation | M | UK/EU consumer + misrepresentation exposure; DPO trust | A DPO reads the deck, asks for the DPA/DPIA/subprocessor list, and nothing exists | Current deck wording (slide 04) is soft and defensible | The stronger "operates in line with…" claim (described for A7) is not survivable today — see §6 |
| R12 | **Emotion-inference prohibition (in force now):** any sentiment/emotion analysis of candidates in a work context is a prohibited practice | L (H if triggered) | EU AI Act Art 5(1)(f), in force Feb 2025 | Populating `sentiment_trajectory` (dormant field in scoring schemas); surfacing Fireflies sentiment features | Field is dormant; Fireflies sentiment not surfaced | Remove the dormant field or hard-document that it must never ship; add to release-review checklist |
| R13 | **Interview recording consent (Fireflies):** call-recording consent rules vary by jurisdiction; transcripts hit a US subprocessor | M | ePrivacy/member-state recording law; PDPL | Fireflies-transcribed interviews with EU/UAE candidates | Fireflies is customer-connected (their meeting, their consent flow) | Consent responsibility never papered in the DPA; Fireflies transfer status unverified `⚖ COUNSEL` |
| R14 | **DPIA absence blocks procurement:** deployers cannot lawfully buy without doing a DPIA; ICO says providers should complete one too | H (sales blocker) | GDPR/UK Art 35; ICO audit recs | Next enterprise security/privacy review | Decision provenance + telemetry make a strong DPIA *input* | No vendor DPIA, no customer-facing DPIA template as sales collateral |

---

## 4. Gap analysis vs existing controls

Grounded in the code as of 2026-07-23 (branch `main`).

### The one control that is weaker than described
**Human review does not cover rejections (R0).** The brief and the earlier draft of this document both described verdicts as queued for human sign-off. That holds for advances — `auto_advance`, `auto_send_assessment` and `auto_skip_assessment` all default to `False`, so positive actions genuinely wait for a person. It does not hold for pre-screen rejections, where `auto_reject_pre_screen` defaults to `True`. What *is* real on that path: the reject rule is deterministic, the state/reason/timestamp are recorded, the role row is locked so a paused or turned-off role cannot be acted on, an ATS write-back failure falls back to a Decision Hub card, and recruiters can disable it per role. What is missing: any Art 22(2) gateway for the EU, notification to the candidate that a solely automated decision was made, and jurisdiction-awareness in the default.

### What exists and is genuinely strong
- **DSR machinery with durable evidence** — `backend/app/domains/compliance/data_subject_service.py`: access-export + erasure with a logged `data_subject_requests` row that survives erasure. Erasure scrubs an enumerated field list on `candidates` including raw ATS payloads (`workable_data`, `bullhorn_data`).
- **Candidate-facing telemetry transparency** — assessment welcome page states exactly what is and isn't recorded ("We record your prompts, Claude responses, file changes, validation runs… We do not record your screen, microphone, or camera."), mirrored in-session. No webcam/lockdown proctoring — a deliberate, defensible data-minimisation position that also keeps Taali away from biometric/emotion territory.
- **Decision accountability** — every verdict records policy revision + rule path; overrides recorded; exportable decision history. This is the raw material for UK Art 22C safeguards, EU Art 22(3) human-intervention rights, and EU AI Act Art 12 logging. Few competitors will have this.
- **Access control & sharing** — tenant-scoped RBAC; time-limited, revocable share links.
- **Usage metering** — every Anthropic call writes a UsageEvent (CI-enforced): doubles as a processing-activity audit trail.

### Gaps (each maps to register rows)
- **Erasure is single-table** (R8) — module docstring honestly lists out-of-scope PII: `candidate_applications` (cv_text, cv_file_url, cv_sections, screening_answers, notes), interview records, frozen `rpt_` report snapshots and submittal packs, `prospects`/outreach rows, assessment artifacts, ATS re-import resurrection. **Not in that list, and worth adding: the CV file in object storage.** Erasure nulls the `cv_file_url` pointer but never deletes the object, so the original CV survives an erasure request. `delete_from_s3` already exists (`backend/app/services/s3_service.py`), so this is a wiring job, not a build.
- **No privacy notice or terms pages** (R4) — `RegisterPage.jsx` references "Terms and Privacy"; no such routes exist.
- **No retention schedule** (R8) — no automated retention/deletion job; indefinite retention is the current default.
- **No compliance paperwork as artifacts** (R6, R10, R14) — no DPA, subprocessor list, DPIA, ROPA, transfer matrix, in repo or on the site.
- **No candidate ADM information/contest route** (R1, R4) — nothing tells a candidate a rule-based verdict was reached about them, on what logic, or how to contest it. (UK Art 22C makes this a checklist; EU WP251 expects it.)
- **No meaningfulness evidence** (R2) — override rates are computable from existing decision records but not measured or reported anywhere.
- **Bias monitoring is real but partial** (R9) — a voluntary self-ID step (segregated from scoring), an owner-only aggregate EEO report, a 4/5ths adverse-impact script, and a continuous bias-monitor capability already exist. The gaps: coverage is native-apply only, the wording is US EEO/OFCCP-framed rather than UK/EU explicit-consent + Art 9, and impact-ratio reporting is operator-run rather than scheduled and fed back into design.
- **Dormant `sentiment_trajectory` field** (R12) — `backend/app/components/scoring/schemas.py` — never populated; should be removed or fenced.

---

## 5. Prioritized roadmap

### Horizon 1 — before the next pilot (documentation sprint; ~1–2 weeks of focused work, no re-architecture)

0. **DECIDE: what happens to default-on pre-screen auto-reject for UK/EU candidates (R0).** This is a product/business call, not a documentation task, and it is deliberately left open rather than changed unilaterally — flipping it alters funnel throughput for every existing customer. The realistic options, in ascending order of cost:

   | Option | What it means | Legal position | Cost |
   |---|---|---|---|
   | **A. Keep default on, add UK safeguards** | Auto-reject stays default. Add: candidate notice at rejection stating a solely automated decision was made, the logic in plain terms, and the contest route | Defensible in the **UK** (DUAA Arts 22A–22D permit it with the Art 22C safeguard set). **Not sufficient for the EU** — no Art 22(2) gateway | Low — one notification surface + copy |
   | **B. Jurisdiction-aware default** (recommended) | Default stays on for UK/rest-of-world; defaults **off** for roles whose candidates are in the EU, where the reject becomes a Decision Hub card instead. Plus option A's safeguards everywhere | Clean in both. EU rejects become human decisions; UK keeps the throughput | Medium — needs a jurisdiction signal per role/org and a default-resolution rule |
   | **C. Default off everywhere** | Every reject becomes a Decision Hub card | Safest; removes R0 | Highest — hits the core "handles repetitive triage" value proposition and every customer's throughput |
   | **D. Explicit-consent gateway** | Collect candidate explicit consent to automated rejection at apply time | Theoretically opens the EU Art 22(2)(a) gateway, but consent in a recruitment context is contested (imbalance of power) and a refusal path is needed | Medium build, **weak legal footing** — `⚖ COUNSEL` before building |

   **Recommendation: B**, with A's candidate-notice work done first because it is cheap and required under every option. Do not ship D without counsel. Until this is decided, the honest public position is the one now published on `/privacy` — that pre-screen rejection can be automatic, is on by default, and is per-role switchable.

1. **DPA template** (controller→processor, Art 28) with: subprocessor authorization + list, SCC module (EU 2021 SCCs) + UK Addendum/Data Bridge language, PDPL Art 23 contract language for UAE customers, security annex, DSR-assistance terms, Fireflies recording-consent allocation. `⚖ COUNSEL` review before first signature.
2. **Public subprocessor page** (taali.ai/subprocessors): name, function, location, transfer mechanism, DPF status — verify each on dataprivacyframework.gov while building it (closes the unknowns in R6).
3. **Candidate privacy-notice template** for customers + a Taali-hosted notice for platform-collected data; includes retention period, ADM description, contest route. Fix the dangling Terms/Privacy links with real pages.
4. **Vendor DPIA + customer-facing DPIA template** as sales collateral (R14 is a sales blocker; deployers cannot lawfully buy without one). Record degree/stage of human involvement per WP251.
5. **Records of processing (ROPA)** — one honest table; the subprocessor page and §1 of this doc are most of it.
6. **Candidate contest/representations route, minimum viable** — a documented email-based process with SLA, linked from the privacy notice and (H2) from candidate-facing surfaces. UK Art 22C(b)/(d) and ICO "simple way to challenge" expect it.
7. **Controller/processor role matrix** — write down exactly which processing is processor-scope vs Taali-controller-scope; decide whether calibration pipelines stay inside processor scope (customer-scoped calibration only) or get papered as controller processing. `⚖ COUNSEL` (R3 — highest-leverage legal question on the list).
8. **Delete or fence `sentiment_trajectory`**; add "no emotion/sentiment inference on candidates" to the release checklist (R12). Audit candidate-facing AI surfaces for Art 50 interaction-labelling before 2 Aug 2026 (mostly done already via assessment disclosures).

### Horizon 2 — next 3 months

1. **Meaningful-HITL evidence pack** (R1/R2, the load-bearing risk): measure and report override/edit rates from existing decision records; define reviewer authority/competence in the product docs; add friction where evidence review hasn't happened (e.g. verdict evidence must be opened before approve on contested/borderline bands); ship an org-level "human review analytics" view. Goal: a one-pager that shows a regulator the review is real.
2. **Erasure sweep v2 + retention scheduler** (R8): cross-table erasure (applications, interviews, snapshots, prospects, assessment artifacts), **object-storage deletion wired into `fulfill_erasure` via the existing `delete_from_s3`** — do this one first, it is the cheapest and removes the richest surviving artifact, ATS re-import suppression list, and default retention schedule (unsuccessful candidates: configurable, default 6–12 months post-process; UK default anchored to the 6-month Equality Act window; talent-pool retention only on recorded candidate opt-in).
3. **Bias-testing approach** (R9): extend the existing voluntary self-ID step (today native-apply only, US EEO/OFCCP-framed) with UK/EU explicit-consent + Art 9-condition wording and post-assessment coverage for ATS-synced candidates; turn the existing 4/5ths adverse-impact script and owner-only EEO report into scheduled quarterly impact-ratio reporting (LL144 shape) on pre-screen and verdict outcomes, fed back into design. This simultaneously serves ICO expectations, Equality Act customer demands, and EU AI Act Art 10 readiness — and it is an upgrade of existing machinery, not a green-field build.
4. **EU AI Act provider-readiness workplan** (R7): map existing telemetry/logging/oversight to Arts 9–15; start the Art 11 technical-documentation file and Art 17 QMS skeleton now while the material is fresh; assign the conformity-assessment route (internal control, Annex VI) and registration task with a mid-2027 internal deadline. Review the final omnibus OJ text with counsel when published.
5. **UK Art 22C product mapping**: candidate decision notices, representations intake, human-intervention escalation, contest flow — turn the H1 email-based route into product. Track the ICO's final ADM guidance + statutory code (SI 2026/425) and adapt.
6. **UAE/ADGM paperwork** (R10): PDPL-aligned DPA annex; on ADGM incorporation — ODP notification, DP policy, DPO-appointment assessment, ADGM-Addendum SCCs for ADGM→US flows. `⚖ COUNSEL`
7. **Special-category leakage analysis** (R5): document what CV/transcript content can reach scoring inputs; add masking/ignore rules for obvious special-category content; record the analysis in the DPIA.

### Horizon 3 — 6–12 months

1. **Hosting-region decision** (R6): stand up an EU region (Railway supports EU regions; Postgres+Redis migration is days of work, not months — the main costs are a migration window, dual-running during cutover, and re-doing the subprocessor matrix). Strong side-benefit: fixes the documented UAE/EU latency problem (API currently us-east4). Trigger to accelerate: DPF appeal (C-703/25 P) moving toward a hearing, or a lighthouse customer making EU hosting a procurement condition. Recommendation: plan it as a 2027-H1 project now; treat DPF-invalidity as the contingency that makes it urgent.
2. **Conformity-assessment prep + EU database registration** (R7): complete the Art 11 file, run the Annex VI internal-control assessment, draw up the declaration of conformity, register before continuing EU sales past 2 Dec 2027.
3. **Certifications for procurement**: SOC 2 Type II first (fastest procurement unlock for UK/US-style buyers; observation window means starting ~Q4 2026 to have it in 2027), ISO 27001 where EU/UAE enterprise buyers require it; consider ISO/IEC 42001 (AI management) as differentiation aligned with AI Act QMS work — one management-system effort can feed both. `⚖ COUNSEL`/auditor scoping.
4. **Monitor and adapt**: Latombe appeal; final ICO ADM guidance + statutory code; UAE PDPL executive regulations; EU AI Act harmonised standards for employment systems as they appear.

---

## 6. The deck claim, assessed frankly

Two versions of the claim are in play:

**(a) What the repo actually ships today** (deck slide 04, `frontend/public/_deck/index.html`):
> "**UK/EU deployment controls** are agreed with each customer and support their compliance work."

**(b) The stronger claim described for appendix slide A7:**
> "Taali operates in line with UK and EU data-protection requirements" + a documented per-customer compliance process (controller/processor roles, DPIA, candidate contest route, DPA, subprocessors, transfer mechanisms, EU AI Act readiness).

**Finding:** (b) does not exist in the deployed deck — the deck on `main` has appendix slides A1–A5 (product demo only), and the phrase "data-protection requirements" appears nowhere in the repo (verified against full git history). The strongest live claim is (a), which is defensible: it promises process, not status.

**What (b) would need to be true and documented to survive a DPO's scrutiny:**
1. A signed-ready **DPA** with SCC/Addendum modules (H1.1) — the first thing a DPO requests.
2. A **public subprocessor list** with transfer mechanisms per subprocessor (H1.2).
3. **Candidate privacy notices** + retention schedule actually enforced in product (H1.3, H2.2).
4. A **vendor DPIA** they can read and a template they can adapt (H1.4).
5. A **documented controller/processor allocation** that survives the ICO's central-model test (H1.7).
6. A demonstrable **candidate contest route** (H1.6/H2.5).
7. An answer to "**where is the data and under what mechanism**" that doesn't improvise (§2.4).
8. **Meaningful-review evidence** — override analytics, reviewer role definition (H2.1) — because the DPO's first substantive question about an AI hiring tool in 2026 is Art 22/22A exposure.
9. **A defensible answer on automated rejection (R0)** — the DPO *will* ask whether any decision is made without a human. The answer must be the accurate one now published on `/privacy`, plus whichever option from §5 Horizon 1 item 0 has been implemented. This is the item most likely to decide whether the claim survives scrutiny, because it is the only place where the product does something the claim's plain reading would deny.

Until those exist, "operates in line with UK and EU data-protection requirements" is an aspiration phrased as a status, and a competent DPO will treat the gap between claim and artifacts as a credibility signal about everything else in the deck.

**Recommended wording (until H1 is done):** keep slide 04 as is, and if a compliance appendix slide is wanted, phrase it as *design + process*: "Built to support UK and EU data-protection compliance: human review with recorded overrides, evidence-linked decisions, candidate transparency, tenant isolation, and erasure workflows — with a documented per-customer compliance pack (DPA, DPIA support, subprocessor list) as part of onboarding." Ship the H1 artifacts, then the stronger sentence becomes safe with one change: "operates in line with" → "**is operated in line with** UK and EU data-protection requirements **under each customer's instructions**" (accurate for a processor, and honest about the shared-responsibility split).

---

## 7. What to tell a DPO who asks "where is the data?"

Today's honest answer, in one paragraph (usable once H1.2 exists):

> Candidate data is processed in the United States: application data and decision records on Railway (us-east4: API, Postgres, Redis), CV and job-spec uploads plus cached report documents in S3-compatible object storage (AWS S3, us-east-1, by default), frontend served via Vercel. AI processing is performed by Anthropic (DPF-certified). Assessment sandboxes run on E2B; transcripts, where the customer connects Fireflies, are processed by Fireflies; email via Resend; billing via Stripe; assessment repos on GitHub; candidate evidence graph (where enabled) on Neo4j/Voyage. Transfers from the UK/EU rely on the EU–US Data Privacy Framework and UK Extension where the subprocessor is certified, and on Standard Contractual Clauses (with the UK Addendum) otherwise — see the subprocessor list at taali.ai/subprocessors for the per-subprocessor mechanism. An EU hosting region is on the 2027 roadmap.

Every clause in that paragraph must be verified while building the subprocessor page (H1.2).

---

## 8. Questions for counsel (collected)

0. **Default-on pre-screen auto-reject (R0) — ask this one first.** Given that a below-threshold LLM-derived pre-screen score automatically rejects EU and UK applicants today with no human review: (a) is option B (jurisdiction-aware default) sufficient, or does the EU exposure require default-off pending an Art 22(2) gateway? (b) does the recruiter-authored *knockout* path (boolean answers, no model involvement) sit outside Art 22 as a non-profiling deterministic filter, or is it caught too? (c) is candidate explicit consent to automated rejection viable in recruitment, or is the imbalance-of-power objection fatal? (d) does UK Art 22C compliance require notifying the candidate at the point of rejection, or is an on-request route enough?
1. **SCHUFA extension to hiring (R1):** does Taali's specific design — deterministic rule verdict, mandatory human sign-off before any ATS write-back, evidence attached — keep customers from "drawing strongly" on the verdict in the SCHUFA sense? What HITL design/evidence would counsel treat as safe harbour in practice?
2. **EU gateway (R1):** if vendor-level Art 22 exposure is assumed, is an explicit-consent gateway workable in recruitment (imbalance-of-power objections), and how should it be architected (who collects, when, what happens on refusal)?
3. **Controller/processor line (R3):** do the calibration and re-scoring pipelines, as scoped, cross the ICO's "central model development" controller test? Can customer-scoped calibration keep Taali inside processor scope? Does the "we never train models on your candidate data" claim need qualifying?
4. **Special-category leakage (R5):** what level of filtering/analysis is needed for CV/transcript inputs so UK Art 22B's strict track and EU Art 9 are not inadvertently engaged?
5. **Final omnibus text (R7):** confirm against the OJ text — Annex III dates, conformity-assessment route for Annex III 4, registration timing, any changes to Art 6(3) filter usable by Taali, and the grandfathering rules for systems already on the market before Dec 2027.
6. **Transfers (R6):** confirm the DPA's SCC/Addendum architecture and whether TRAs are needed per subprocessor; contingency plan if the CJEU strikes the DPF (C-703/25 P).
7. **UAE (R10):** current status of PDPL executive regulations (sources conflict); ADGM incorporation sequencing — ODP notification, DPO requirement, ADGM-Addendum SCCs for ADGM→US; whether serving mainland-UAE customers from a future ADGM entity changes which regime applies.
8. **Fireflies/recording (R13):** allocation of recording-consent responsibility between Taali, the customer, and Fireflies across EU member states and the UAE.
9. **Assessment telemetry retention:** is full session telemetry justified indefinitely for hired candidates vs unsuccessful ones under storage limitation, and what schedule is defensible?
10. **Equality Act warranties (R9):** what bias-testing representations can Taali safely give customers before the H2 bias pipeline produces data?

---

## Appendix — primary sources relied on

- CJEU C-634/21 *SCHUFA Holding (Scoring)*, 7 Dec 2023 — EUR-Lex CELEX 62021CJ0634 (operative part, paras 48–63 verified verbatim).
- A29WP/EDPB *Guidelines on Automated individual decision-making and Profiling* (WP251rev.01) — official EC PDF (ec.europa.eu newsroom item 612053).
- Data (Use and Access) Act 2025, s.80 + Part 5 — legislation.gov.uk (ukpga/2025/18); S.I. 2026/82 commencement.
- ICO: *AI tools in recruitment — audit outcomes report* (Nov 2024, ico.org.uk PDF); DUAA organisational guidance (updated 19 Jun 2026); March 2026 ADM-in-hiring statement; ADM guidance under-review banner.
- Regulation (EU) 2024/1689 (AI Act) — EUR-Lex; Digital Omnibus amendments: EP endorsement 16 Jun 2026, Council final approval 29 Jun 2026 (Annex III → 2 Dec 2027; Art 50 unamended, applies 2 Aug 2026). *Final OJ text pending review.*
- Latombe v Commission, T-553/23 (GC, 3 Sept 2025); appeal C-703/25 P (pending).
- UK Extension to the EU–US DPF ("Data Bridge"), in effect 12 Oct 2023 — ICO guidance.
- UAE Federal Decree-Law 45/2021 (PDPL) Arts 22–23; DIFC DP Law 2020 Arts 26–27 + DIFC SCCs; ADGM DPR 2021 + ADGM Addendum to EU SCCs (adgm.com).
- NYC Local Law 144 (DCWP rules) — bias-audit shape reference.
