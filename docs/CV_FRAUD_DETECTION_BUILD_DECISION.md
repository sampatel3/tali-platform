# Taali CV-Fraud Detection — Build Decision & Roadmap

**Status:** Build-decision doc · **Date:** 2026-06-26 · **Owner:** Sam

> The comprehensive lever catalog behind the fraud work; the funnel-specific build spec that was actually implemented is `CV_FRAUD_FUNNEL_DESIGN.md`. Produced by a 9-agent cost/value/placement analysis (83 levers + 6 from an adversarial completeness critic). Reconciles against what shipped in #700.

**Bottom line up front.** Taali has already shipped the expensive-to-get-right core (hidden-text hygiene, copy-paste hard cap, timeline sanity, claims integrity, CV↔Workable diff, unverified-employer grounding). The remaining high-leverage wins are almost all **$0, deterministic, and ride data already in the row** — and the single biggest blind spot is that **the assessment's `git_evidence` (collected for every submission) is analysed by nothing**. Lead with that. Treat the entire "External public data / KYC" category as defer-or-ignore: it is dollars-per-candidate, MENA-coverage-poor, and mostly fairness-loaded for a signal that can only ever flag, not gate.

---

## 1. Master table

Legend — **Build cost:** S (<1d) / M (1-4d) / L (1-2wk) / XL (weeks). **Confidence:** GATE (deterministic, can hard-cap) / FLAG (probabilistic or fairness-risk, advisory only). **Verdict:** BUILD / DEFER / IGNORE / **DONE** (already live).

### A. Document / file forensics (the artifact)

| Lever | Catches | Build | Run | FP / Uniqueness | Conf | Placement | Verdict |
|---|---|---|---|---|---|---|---|
| **DOC-02** Invisible render-mode (Tr 3) via content-stream | Tr-3 hidden keyword/injection text | M | $0 | Very low FP / deterministic subset of DOC-01, but synchronous | GATE | pre-screen | **BUILD** |
| **DOC-03** White-on-white by colour (fill-colour scan) | The named "white text" attack | M-L | $0 | Low-mod FP (light-grey design) / pairs w/ DOC-02 to cover the 2 dominant methods | GATE (flat case) | pre-screen | **BUILD** |
| **DOC-04** Tiny/off-page/zero-area text (bbox+size) | Microfont, off-cropbox stuffing | S-M | $0 | Low-mod FP / rounds out DOC-02/03 same pass | GATE (off-crop) | ingest/pre-screen | **BUILD** (bundle w/ DOC-02/03) |
| **DOC-10** Embedded files / JS / launch-actions / annotation stuffing | Weaponised PDF + non-content-stream stuffing | M | $0 | Very low FP / **unique** (only security lever) | GATE (active content) | ingest | **BUILD** |
| **DOC-09** Image-only / no-text-layer detection | Evasion (text-based stack sees nothing) + OCR routing | S detect | $0 detect | Low FP as router / unique precondition | route, not penalise | ingest | **BUILD** (detection) |
| **DOC-01** Render-vs-text OCR diff (PhantomLint-style) | ANY invisible text incl. OCG-OFF, novel methods | L | ~$0.003-0.006/CV (Anthropic-vision) | Low FP w/ scanned whitelist / **highest net-new** — the one thing text-hygiene can't do | GATE (visibility) | async enrichment | **BUILD** (after DOC-02/03/04 ship cheap subset) |
| DOC-06 Producer/creator + date-gap metadata | Tooling fingerprint, CV-mill clustering feature | S | $0 | HIGH FP if scored (everyone uses Word/Canva) / low CV value | FLAG advisory | async | **DEFER** (only as DOC-12 feature) |
| DOC-11 OCR-vs-text **semantic** diff | Doctored text layer ≠ rendered image | L | same as DOC-01 | Mod-high FP (OCR noise) / rare on CVs | FLAG | on-demand | **DEFER** (until a mismatched-layer attack is observed) |
| DOC-12 CV-mill / template-skeleton clustering | "Fake applicant factory" cross-candidate | XL | embed (metered) | Mod-high FP (popular templates) / unique cross-doc | FLAG cluster | async batch | **DEFER** (P2; needs Neo4j+volume) |
| DOC-05 Zero-width / bidi / Tags smuggling | Invisible-char instruction hiding | — | $0 | Very low FP | GATE | ingest/pre-screen | **DONE** (`document_hygiene.py`) |
| DOC-07 Incremental-save / xref-chain | "Was it edited" | S | $0 | **VERY HIGH FP** — every edited CV trips it | — | n/a | **IGNORE** (benign base-rate ≈ 100%) |
| DOC-08 Font-splice detection | Pasted forged region | L | $0 | HIGH FP / wrong threat model (gamers re-author, not splice) | FLAG | on-demand | **IGNORE** |
| DOC-13 Pixel/copy-move forensics (vendor) | Tampered scanned image | L-XL | **tens of ¢-$1+/doc, quote-only** | Mod FP / CVs are vector text, not pixels | FLAG | on-demand | **IGNORE** (wrong artifact; use DataFlow PSV) |
| DOC-14 AI-generated-PDF structural heuristic | Machine-built vs hand-authored | L | $0 | HIGH FP + fairness / **AI-builder use is not fraud** | — | n/a | **IGNORE** (non-goal, unreliable per source) |

### B. Content / linguistic (the words)

| Lever | Catches | Build | Run | FP / Uniqueness | Conf | Placement | Verdict |
|---|---|---|---|---|---|---|---|
| **C9** Years-claimed vs timeline-sum | Inflated total experience | S | $0 | Mod FP→tolerance+fail-open / near-free on extracted data | GATE arith → soft penalty | full score | **BUILD** |
| **C12** Concurrent full-time overlaps (historical) | Padded parallel jobs | S | $0 | Mod FP at year-grain→multi-year only / extends live fn | GATE → soft penalty | full score | **BUILD** |
| **C14a** Tech anachronism (tool-before-it-existed) | "React 2010", "K8s 2012" | M | $0 | Very low FP when fired, low recall / **unique high-precision tell** | GATE → bounded penalty | full score | **BUILD** |
| **C7** Claim-vs-evidence specificity (Citations) | Fabricated/inflated achievements | M (extend) | ~$0 marginal | Low-mod FP (terse cultures) / **unique** — only achievement lever | FLAG + bounded penalty | full score | **BUILD** (extend live `claims_to_verify`) |
| **C16 / xsc-01** CV↔Workable history diff | Fabricated roles, inflated tenure | S (finish) | $0 | Low FP / **HIGH — only independent-source corroboration** | FLAG → bounded penalty on cv_only+date_shift | full score | **BUILD** (on-by-default + score-aware tier) |
| C3 Semantic JD mirroring (embedding cosine) | Heavy-paraphrase JD mirror | M | ~$0.0002/cand | Mod FP / C1+C2 already cover paste family | FLAG | async | **DEFER** (trigger: paraphrase evasion in data) |
| C10 Seniority/title vs dates | Title inflation | S-M | $0 | Mod FP (startups/locale) / already in holistic dims | FLAG | full score | **DEFER** (surface existing `seniority_alignment`, no new rule) |
| C11 Education-date vs first-job | Fabricated early career | S-M | $0 | Mod-high FP (working students, MENA norms) / low yield | FLAG | full score | **DEFER** (after C9/C12) |
| C18 Summary-vs-body contradiction | Unsupported headline claim | M | ~$0 marginal | Mod FP / overlaps C7 + scorer's missing-skills | FLAG | full score | **DEFER** (fold into C7) |
| C19 Cross-application reuse / template-farm | CV-farm / Sybil reuse | L | embed cheap | Low-mod FP / unique cross-candidate | FLAG cluster | async | **DEFER** (after cheap wins; = id-03/id-04) |
| C1 Verbatim JD copy-paste (8-gram) | Paste JD to game ATS | — | $0 | Very low FP / **only hard cap** | GATE | pre-screen | **DONE** — keep |
| C2 Paraphrased-JD shingle | Reword to dodge C1 | — | $0 | Low-mod FP / closes C1 evasion | FLAG | full score | **DONE** — keep flag-only |
| C6 Prompt-injection detect+strip | Hidden model instructions | — | $0 | Very low FP / **HIGH unique** | GATE (strip on) | ingest/pre-screen | **DONE** — keep; consider per-org cap escalation |
| C13 Timeline sanity (future/impossible dates) | Careless fabrication | — | $0 | Very low FP / high precision | GATE → bounded penalty | full score | **DONE** — flip `HOLISTIC_INTEGRITY_PENALTY_ENABLED` on after shadow |
| C17 Unverified-employer grounding | Parser-hallucinated / ungrounded employer | — | $0 | Very low FP / data-hygiene + weak fraud | FLAG marker | ingest | **DONE** — keep |
| C4 Keyword-density outlier | Skills stuffing (visible) | S | $0 | Mod-high FP (ESL/generalists) / scorer already discounts tool-listing | — | — | **IGNORE** |
| C5 AI-generated-text (perplexity/stylometry) | LLM-written CV | L-XL | ~$0.01-0.02/CV | **61.3% FP on non-native English** / arms race | — | — | **IGNORE** (disparate-impact disqualifier; MENA is ESL) |
| C8 Buzzword ratio | Fluff CV | S | $0 | HIGH FP (ESL) / quality not fraud | — | — | **IGNORE** |
| C15 Gap concealment (CV-internal) | Stretched dates to hide gap | — | $0 | No reliable single-source signal / collapses into C16 | — | — | **IGNORE** (covered by C16 date_shift) |

### C. Cross-source consistency (data we hold)

| Lever | Catches | Build | Run | FP / Uniqueness | Conf | Placement | Verdict |
|---|---|---|---|---|---|---|---|
| **xsc-05 / id-01** Reused contact across distinct identities | Sockpuppet, re-apply-after-reject, ballot-stuff | S-M | $0 | Mod FP (family/agency)→name-distance + agency-exclusion / **HIGH unique** | GATE (canon. personal email) / FLAG (phone) | ingest | **BUILD** |
| **xsc-07** Reused CV content-hash across candidates | Identical file, fresh contact per persona | S | $0 | Very low FP / plugs the one hole xsc-05 leaves | GATE detect, advisory act | ingest | **BUILD** |
| **NEW: Email canonicalization** (Gmail dot/plus) | Makes xsc-05/id-01 hard-gate actually true | S | $0 | Low FP (provider-specific) / closes cheapest evasion | GATE enabler | ingest | **BUILD** (prereq for id-01 gate) |
| **NEW: Source/referral collusion** (lead_source/source clustering) | Agency-mill / referral-bonus farming / self-referral | S-M | $0 | Mod FP / unique provenance dimension; MENA-relevant (wasta) | FLAG cluster | async | **BUILD** (cheap; MENA value) |
| xsc-02 CV↔Workable questionnaire contradiction | Constraint-lies (auth/location/salary/YoE) | M | $0 core | Mod FP + disparate-impact (diaspora location) / highest-volume game | FLAG + bounded (numeric only) | pre-screen | **DEFER→BUILD numeric core** (keep auth advisory) |
| **NEW: Assessment-vs-CV capability contradiction** | CV claims seniority the assessment disproves | M | $0 | Mod FP (one sample) / **unique, fires pre-interview at volume** | FLAG | full score (at submit) | **BUILD** |
| xsc-03 CV↔interview transcript | Ghostwritten CV, inflated scope | M-L | ~$0.02-0.05/cand | Mod FP / tiny late population, human already does it | FLAG advisory | on-demand | **DEFER** |
| xsc-04 Same-candidate story-drift across applications | Escalating self-edits | M | $0 | Mod FP (legit progression) / niche (repeat applicants) | FLAG | async | **DEFER** (bundle w/ id-03) |
| xsc-06 Cross-candidate CV plagiarism (embeddings) | CV-mill / shared CV | M | embed cheap | Mod-high FP (CV-builder floor) / overlaps xsc-05 | FLAG cluster | async | **DEFER** (= id-04; do minhash slice first) |
| xsc-08 / ext-08 CV↔GitHub/portfolio corroboration | Inflated eng output, dead portfolio | M (GitHub) / L-XL (LinkedIn) | ~$0 GitHub | Mod-high FP (absence≠fraud) / GitHub narrow, LinkedIn MENA-weak | FLAG corroboration | async/on-demand | **DEFER GitHub** (eng-only shortlist) / **IGNORE LinkedIn** |

### D. External public data (scrape / enrichment)

| Lever | Catches | Build | Run | FP / Uniqueness | Verdict |
|---|---|---|---|---|---|
| **ext-06** Company existence + domain-age | Shell/invented employers | S-M | ~$0.001/domain | Mod FP→scope to recent/unverified / **unique legitimacy check, global, no MENA gap** | **BUILD** (narrow, extends `unverified_employers`) |
| ext-03 GitHub activity vs claimed output | Empty account behind "staff eng" | M | ~$0 (free API) | Mod-high FP (private work) / eng-only, gameable | **DEFER** (eng-roles, corroborate-not-penalise) |
| ext-08 Personal portfolio match | Dead/mismatched portfolio | S-M | ~free + small LLM | Low FP / candidate-volunteered | **DEFER** (bundle w/ ext-03 as one links-pass) |
| ext-14 Wayback employer-existence-at-date | Back-dated employer | M | ~free | Low-mod FP / time-axis complement to ext-06 | **DEFER** (after ext-06) |
| ext-04 Scholar/ORCID/Crossref publications | Fake papers | M | ~$0 | Low-mod FP / niche (research roles) | **DEFER** (build w/ research client) |
| ext-11 Web corroboration of awards | Fabricated press/awards | M-L | $0.02-0.10+/cand | HIGH FP (MENA awards under-indexed) / live claims signal covers most | **DEFER** |
| ext-01 LinkedIn existence/title match | #1 lie: fake employer/title | L | **$0.05-0.28/successful match** (low MENA hit-rate → real cost higher) | HIGH FP + disparate-impact / weak vs free Workable diff, gameable | **DEFER** (on-demand shortlist only) |
| ext-09 Professional licence registries | Fake licence (regulated) | L per registry | ~free-pennies | Mod FP / fragmented; DataFlow incumbent | **DEFER** (single-registry pilot on client demand) |
| ext-02 LinkedIn network/endorsements | Sockpuppet profiles | XL | $$ | VERY HIGH FP / depends on deferred ext-01 | **IGNORE** |
| ext-05 Patent records | Fake patents | M | ~$0 | Low FP / near-zero trigger rate | **IGNORE** (fold into ext-04 if ever) |
| ext-07 Email MX/deliverability | Bogus domain | S | $0.0025-0.01 | / subsumed by ext-06 | **IGNORE** (use ext-06 MX for free) |
| ext-10 Kaggle rankings | Fake "Grandmaster" | S-M | ~free | Low FP / tiny population | **IGNORE** (fold into ext-08) |
| ext-12 Conference/talk records | Fake speaking | L | low-mod | Mod-high FP (regional events) / no clean source | **IGNORE** |
| ext-13 Reverse-image search on photo | Stock/stolen headshot | L-XL | $$ + biometric vendor | HIGH FP + bias/consent / low prevalence | **IGNORE** (see id-05 internal pHash instead) |

### E. External paid verification / KYC

| Lever | Catches | Run cost | Verdict |
|---|---|---|---|
| ext-01-dataflow-psv Education/credential PSV | Fake degrees/licences at source | **AED 300-500 (~$80-135)/doc, ~weeks, candidate-paid** | **DEFER** (table-stakes only for regulated Gulf hiring; thin "request" handoff) |
| ext-03-identity-kyc-liveness (Uqudo/Onfido) | Synthetic/stolen identity, proxy | **$0.50-3.00/verification** | **DEFER** (anti-proxy at assessment boundary; only on observed abuse) |
| ext-04-sanctions-pep | Sanctions/PEP | ~$99/mo entity-based | **DEFER** (compliance feature, not fraud; regulated clients only) |
| ext-06-reference-checks (Xref) | Role/date confirmation | ~$40-60/check | **DEFER** (candidate-chosen referees → weak anti-fraud; sell as feature) |
| ext-07-email/phone-reputation (Twilio/ZeroBounce) | Disposable/VoIP contacts | $0.0025-0.05/lookup | **DEFER** (anti-spam hygiene; = bp-09 free-list version) |
| ext-08-social-profile (enrichment) | Invented persona | $0 GitHub / $$ LinkedIn | **DEFER GitHub / IGNORE LinkedIn** (dup of xsc-08) |
| ext-02-employment-voe-us (Work Number/Truework) | Fake US jobs | **$55-105/pull** | **IGNORE** (US payroll DB → ~0% MENA hit; free Workable diff covers it) |
| ext-05-criminal-background | Criminal history | $3.50-100/check | **IGNORE** (off-target for fraud; employer/PEO job) |

### F. Behavioural / process signals

| Lever | Catches | Build | Run | FP / Uniqueness | Conf | Placement | Verdict |
|---|---|---|---|---|---|---|---|
| **NEW: Git-commit forensics on submission** | One-dump paste, foreign committer, end-of-window timestamp cluster, work outside observed channel | S-M | $0 | Mod FP (infrequent committers) / **HIGHEST uncovered — ground-truth, hard to evade** | FLAG → bounded penalty | full score (at submit) | **BUILD — #1 priority** |
| **bp-01** Un-gate paste/keystroke/tab proctoring | Paste solution, alt-tab to ChatGPT | S (flip flags) | $0 | Mod FP / built end-to-end, just disabled | FLAG / soft penalty | full score | **BUILD** (reframe as weaker corroborator to git) |
| **bp-02** Assessment time anomalies (impossibly fast) | Pre-leaked task, ghost-completion | S | $0 | Low-mod FP at tail / unique (re-typed prep shows no paste) | FLAG | full score | **BUILD** (tail-only) |
| **NEW: Retry / multi-attempt re-roll abuse** | Re-sit a now-seen task for higher score | S | $0 | Low FP (exclude `retry_after_failure`) / unique re-roll game | GATE re-issue / FLAG | pre-screen at issue | **BUILD** |
| **bp-07** Application velocity / spray | Bot/spray across roles | S-M | $0 | Low-mod FP / unique cross-role; de-risks ATS roadmap | GATE (extreme) / FLAG | ingest | **BUILD** (lean hook) |
| **bp-09** Disposable-email at ingest (free blocklist) | Throwaway sockpuppet accounts | S | $0 | Low FP (curate out privacy-forwarders) / front-door filter | GATE (known-disposable) | ingest | **BUILD** (free list only) |
| **bp-05** IP datacenter/VPN/Tor flag | Proxy-taker masking origin | M | free tier / ~$0.02 | Low-mod FP / unique network signal; sidesteps MENA geo | FLAG corroborator | async at start | **BUILD** (lean, free tier) |
| bp-03 Cross-candidate code similarity (MOSS/Dolos) | Collusion / leaked answer key | M-L | $0 self-host | **HIGH FP on 5×30-min tasks (93% convergence)** / unique | FLAG | async batch | **DEFER** (until catalog breadth grows; self-host Dolos) |
| bp-06 IP-geo vs claimed location | Proxy-taker location | S | rides bp-05 | HIGH FP (MENA mobile geo 30-50%, cross-border apply is normal) | — | — | **DEFER→IGNORE** (bp-05 covers proxy case) |
| bp-08 Device fingerprint reuse | Ring on one machine | M-L | $0 OSS / $99/mo Pro | Mod FP (shared machines) / premature while Workable-fed | FLAG | ingest | **DEFER** (revisit at self-serve scale) |
| bp-10 Agent-chat misuse (injection/probe) | Jailbreak the in-task agent | — | $0 | Low FP / **the one behavioural hard-gate** | GATE (void on repeat) | live runtime | **DONE** — keep patterns current |
| bp-04 AI-text detector on free-text | LLM-written answers | S wire | vendor | **50-61% FP on ESL** / AI-use often not fraud here | — | — | **IGNORE** (watch HOW not WHETHER) |
| bp-11 Right-to-work doc forensics | Forged visa/ID | XL | high | / wrong layer (employer/PEO job) | — | — | **IGNORE** |
| **NEW: Deepfake/voice-clone at video boundary** | Live AI-avatar interview fraud | — | — | / named gap, no capture surface today | FLAG human-owned | on-demand | **DEFER** (acknowledge; load-bearing only if proctored video ships) |

### G. Identity / duplicate detection

| Lever | Catches | Build | Run | FP / Uniqueness | Conf | Placement | Verdict |
|---|---|---|---|---|---|---|---|
| **id-01** Exact dup phone/email/work_email | Same human, second email | S | $0 | Low-mod FP / **HIGH — data collected, currently discarded by silent merge** | GATE (canon. email) / FLAG (phone) | ingest | **BUILD** |
| **id-03** Multi-persona entity resolution (multi-key clustering) | Same person, no shared contact | M | $0 | Low-mod FP on strong keys / **HIGH unique — activates SIMILAR_TO** | GATE (strong key) / FLAG (minhash) | async batch | **BUILD** |
| **id-05** Profile-photo internal pHash dedup | Same/stock photo across personas | S-M | ~$0 | Low FP (blocklist placeholders) / **unique; uses ignored `image_url`** | GATE (exact) / FLAG | async batch | **BUILD** |
| id-04 Embedding near-dup persona (semantic) | Reworded-CV same person | M | embed cheap | Mod-high FP (same-role cluster) / incremental over id-03 | FLAG | async | **DEFER** (turn on if reword-evasion appears) |
| id-06 Reverse-image search (TinEye) | Web-sourced photo | S | $0.01-0.04/search | Low-mod FP / misses LinkedIn (the case that matters) | FLAG | on-demand | **DEFER** (button on advanced/flagged only) |
| id-09 Cross-org duplicate intelligence | Serial fraudster across tenants | L | $0 | Mod FP + HIGH governance / value scales with tenants | FLAG | async | **DEFER** (seed hashed keys now, build sharing later) |
| id-02 Name↔email mismatch | Borrowed identity | S | $0 | **HIGH FP + structural MENA bias** (romanization) / weak, gameable | — | — | **IGNORE** name-match (keep only disposable-domain → = bp-09) |
| id-07 Face-match across own photos | Proxy/impersonation | L-XL | ~$0 self-host | Mod FP + bias/consent / human interview already does it | FLAG | on-demand | **IGNORE** (revisit if proctored product) |
| id-08 Gov-ID + liveness KYC (Sumsub/Veriff) | Real-unique-human proof | XL | **$0.80-1.35+/verification** | Low FP / disproportionate as screening control | GATE late | on-demand | **DEFER** (consented pre-offer gate the org owns) |

---

## 2. The cost-vs-value cut

### 2×2

**HIGH value / LOW cost — BUILD NOW (all $0 run, S-M build, ride existing data):**
- **Git-commit forensics** — the headline. Ground-truth process record on the product's differentiating artifact, captured already, analysed by nothing, hard to evade.
- **id-01 + email canonicalization + xsc-07 content-hash** — the duplicate-identity triad: same data, indexed, currently thrown away by silent merge. Closes the cheapest multi-persona evasion.
- **id-03 multi-persona clustering + id-05 photo pHash** — activate the already-defined-but-uncomputed `SIMILAR_TO` edge and the ignored `image_url`; cover the same-person-without-shared-contact and synthetic-identity gaps.
- **C9 / C12 / C14a** — deterministic timeline/anachronism extensions of live `fraud_detection.py`.
- **C7 specificity + C16 score-aware tier** — extend the two highest-value live signals (achievement grounding, independent-source diff).
- **DOC-02/03/04/10 hygiene module + DOC-09 detection** — deterministic render-state hygiene, no renderer, no new dep (PyPDF2 ContentStream — the "needs PyMuPDF" assumption is wrong).
- **bp-01 (flip flags) / bp-02 / retry-abuse / bp-07 / bp-09** — assessment + intake behavioural signals, mostly already-captured.
- **ext-06 domain-age** — the one cheap external lever (~$0.001/domain, global, no MENA gap), extends `unverified_employers`.
- **bp-05 IP/VPN flag** — cheap given existing IP plumbing; free vendor tier; sidesteps MENA geo-inaccuracy.

**HIGH value / HIGH cost — DEFER with explicit trigger:**
- **DOC-01 render-OCR diff** (~$0.003-0.006/CV) — *trigger:* ship after the deterministic DOC-02/03 subset; it mops up OCG-OFF/novel methods the cheap pass can't.
- **id-08 gov-ID KYC** ($0.80-1.35+/verification) / **ext-03-identity** ($0.50-3/verification) — *trigger:* a regulated client, or observed proxy-abuse at the assessment boundary. Consented pre-offer gate, not screening.
- **ext-01-dataflow-psv** (~$80-135/doc, weeks) — *trigger:* selling into regulated Gulf hiring (healthcare/finance). Candidate-paid; build only a thin "request" handoff.
- **bp-03 code similarity** — *trigger:* task catalog grows beyond 5×30-min (convergence FP is maximal today) or answer-key leakage observed. Self-host Dolos, never pay Codequiry.
- **DOC-12 / id-04 / C19 cross-candidate clustering** — *trigger:* Neo4j+Voyage provisioned in prod and org volume is high enough for clusters to be meaningful.

**LOW value — IGNORE (and why, so we never revisit):**
- **C5 / bp-04 AI-text detection** — 50-61% false-positive on non-native English. Taali's pool is overwhelmingly ESL. This is a textbook disparate-impact violation and directly contradicts the "never act on protected characteristics" ruling. On agentic tasks, AI-use often isn't even fraud. **Permanent no.**
- **DOC-07 incremental-save / DOC-08 font-splice / DOC-14 AI-PDF** — high-value-elsewhere forensics that are noise on CVs (CVs are expected to be edited, re-authored, and tool-built). Benign base-rate ≈ 100%.
- **DOC-13 pixel forensics / id-07 face-match / id-02 name-email** — wrong artifact (CVs are vector text), or bias/consent landmines for a MENA/global cohort.
- **ext-02 US-payroll VoE** — ~0% hit-rate on MENA employment; free Workable diff already catches fake jobs.
- **ext-05 criminal / ext-07 email-MX / ext-10 Kaggle / ext-12 confs / ext-13 reverse-image** — off-target, subsumed by a cheaper lever, or near-zero trigger rate.
- **C4 density / C8 buzzword / C15 gap-internal** — the scorer already discounts tool-listing; C15 has no single-source signal.
- **bp-06 IP-geo / bp-11 right-to-work** — MENA cross-border applying is *normal*, not fraud; right-to-work is the employer/PEO's late-stage job.

**The expensive-external reality, in one line:** every KYC/scrape lever costs cents-to-dollars *per candidate*, can only ever **flag** (not gate), and is weakest exactly where Taali operates (MENA mid-market, ESL, sparse LinkedIn). The free internal cross-source levers dominate them on value-per-cost. External spend belongs at **on-demand, late-stage, on a shortlisted few**, never top-of-funnel.

---

## 3. Placement design

**The principle — three-tier gate logic:**

1. **Ingest (parse-time):** deterministic, $0, identity/structural facts available before any LLM. Compute once, persist, let downstream reads be free. Can **hard-gate or hold** when the signal is deterministic *and* near-zero-FP (known-disposable email, weaponised PDF, exact content-hash dup).
2. **Pre-screen gate (Haiku, before paid Sonnet):** **cost-before-LLM is the whole point** — a deterministic fraud-positive here *saves* the expensive holistic call. Only **deterministic, low-FP** signals hard-cap here (copy-paste C1, hidden-text DOC-02/03, canonicalized-email dup, numeric questionnaire contradiction). Probabilistic signals must not gate.
3. **Full holistic score:** signals that need the LLM-extracted timeline/claims/dimensions. **Probabilistic → bounded soft penalty or flag only**, folded into `compute_integrity_penalty` (capped at 15, never auto-rejects).
4. **Async post-score enrichment:** anything slow, cross-candidate, or vendor-dependent — cohort clustering (id-03/id-05), render-OCR diff (DOC-01), domain-age (ext-06), IP/VPN (bp-05). Off the candidate hot path; flag is ready before a human looks.
5. **On-demand late-stage:** dollars-per-candidate or consent-gated — DataFlow PSV, gov-ID KYC, LinkedIn, reverse-image. Recruiter-triggered on a shortlisted few.

**The split as a rule:** *deterministic + low-FP → can gate (and gate early to save spend); probabilistic or fairness-risk → flag only, surfaced to the human, never a cap.* The live `bp-10` agent-misuse void is the template for how a behavioural signal earns the right to hard-gate: deterministic + repeated + narrowly scoped.

**BUILD-set placement map:**

| Lever | Lands at | Why |
|---|---|---|
| DOC-02/03/04 hygiene | **pre-screen** (flat/off-crop cases) + ingest persist | deterministic, synchronous, gates before paid call |
| DOC-10 active-content | **ingest** | security gate before anything touches the file |
| DOC-09 image-only | **ingest** | route-to-OCR decision at parse |
| DOC-01 render-OCR diff | **async** | render+OCR too slow/costly for sync |
| Email canonicalization | **ingest** | beside `_normalize_phone_for_match` |
| id-01 contact dup | **ingest** | where phone dedup already runs; cache cluster-id onto application |
| xsc-07 content-hash | **ingest** | hash on parse, check before scoring spend |
| id-03 / id-05 / source-collusion | **async batch** | inherently cross-candidate; write back cluster-id + SIMILAR_TO |
| C9 / C12 / C14a / C7 / C16 | **full score** | need LLM-extracted timeline/claims; fold into `compute_integrity_penalty` |
| xsc-02 numeric | **pre-screen** | questionnaire already in that prompt; gates on hard constraints |
| Git-forensics / bp-01 / bp-02 / assessment-vs-CV | **full score (at submit)** | data only exists post-assessment |
| Retry-abuse | **pre-screen at assessment issue** | block re-issue of a seen task |
| bp-07 velocity / bp-09 disposable | **ingest** | front-door, throttle bots before scoring spend |
| bp-05 IP/VPN | **async at assessment start** | vendor call off the candidate path |
| ext-06 domain-age | **async** | extends `unverified_employers` bundle |

---

## 4. Reconcile with what's already live

| Live signal | File | Decision | Notes |
|---|---|---|---|
| **C1** verbatim copy-paste (hard cap) | `fraud_detection.py` / `pre_screening_service.py` | **KEEP** | Cheapest hard signal; keep cap. Only residual: threshold tuning. |
| **C2** shingle near-dup (flag) | `fraud_detection.py` | **KEEP** | Right call flag-only. |
| **C6** injection detect+strip | `document_hygiene.py` | **KEEP** | Strip stays default-on. **RE-WEIGHT option:** per-org escalation flag→hard-cap on injection hits. |
| **DOC-05** invisible-char strip | `document_hygiene.py` | **KEEP** | Done; no rebuild. |
| **C13** timeline sanity penalty | `fraud_detection.py` → `holistic.py` | **KEEP + turn on** | Flip `HOLISTIC_INTEGRITY_PENALTY_ENABLED` once shadow confirms FP floor. |
| **C17** unverified-employer grounding | `cv_parsing/grounding.py` | **KEEP** | Data-hygiene + weak fraud; load-bearing for trustworthy display. |
| **C16 / xsc-01** CV↔Workable diff | `fraud_detection.py` | **RE-WEIGHT** | Enable `FRAUD_WORKABLE_DIFF_ENABLED` by default; add bounded score-aware tier for `cv_only_role + date_shift` on same employer. Flag stays on. |
| **C7** claims_to_verify / integrity penalty | `holistic.py` | **RE-WEIGHT (extend)** | Tighten specificity rubric (who/when/how cited); surface it. Keep cap at 15. |
| Shingle/JD-mirror, hidden-text, dilution-resistant, unverified-extraordinary-claim (flag-gated) | `fraud_detection.py` | **KEEP** | Live penalties stay on where already enabled; flip remaining flag-gated ones on after shadow review. |
| **bp-10** agent-misuse void | `components/assessments/integrity.py` | **KEEP** | The one behavioural hard-gate; keep patterns current. |
| **bp-01** proctoring (paste/focus/tab) | `components/scoring/analytics.py` | **RE-PLACE** | Currently disabled by `MVP_DISABLE_PROCTORING=True`. **Re-place as the weaker corroborator** beneath git-forensics — flip flags, keep flag/soft-penalty (never join the hard-void path). |
| **MVP_DISABLE_CLAUDE_SCORING / PROCTORING** | `config.py` | **REMOVE (flip)** | Gating the un-built assessment-integrity wins. |

No live signal should be **removed**. The live penalty flags should **stay on**; the work is turning on the shadow-validated ones and adding the bounded score-aware tier to C16.

---

## 5. Recommended build set + sequencing

Ordered by value-per-cost. Each is mostly wiring over confirmed-existing infra.

**Wave 1 — Assessment integrity (the differentiator; all $0 run):**
1. **Git-commit forensics** — parse `assessment.git_evidence` (`status_porcelain`, `diff_staged`, `diff_main`, `git log` commits) in a new analyser beside `components/assessments/service.py` / `analytics.py`; classify history-shape (single-dump, foreign committer email, end-of-window timestamp cluster, diff-without-dialogue). **S-M.** Depends on: nothing (data captured). Bounded penalty via existing integrity path. *Build first.*
2. **bp-01 un-gate proctoring** — flip `MVP_DISABLE_PROCTORING`/`MVP_DISABLE_CLAUDE_SCORING` in `config.py`; decide flag vs soft-penalty thresholds. **S.** Reframe as corroborator to #1.
3. **bp-02 time anomalies** — tail-only ratio over `total_duration_seconds`/`tests_passed` in `analytics.py`. **S.**
4. **Retry/re-roll abuse** — query over assessment rows by `(candidate_identity × task_key)`; exclude `retry_after_failure`; gate re-issue. **S.** Depends on id-01/id-03 for cross-identity re-roll.

**Wave 2 — Duplicate-identity triad (data we discard today; all $0):**
5. **Email canonicalization** — Gmail dot/plus stripper in `sync_service.py` beside `_normalize_phone_for_match`. **S.** *Prereq for #6 hard-gate.*
6. **id-01 exact contact dup** — indexed group-by; emit `duplicate_identity` into `pre_screen_evidence` + reuse FE fraud-chip. **S.** Gate on canonicalized personal email + name-distance; flag on phone; exclude agency contacts.
7. **xsc-07 content-hash** — add indexed `cv_content_hash` column (sha256 of normalized cv_text), populate at parse (reuse `cv_parsing/cache.py` pattern), backfill, cross-identity lookup at ingest. **S.**
8. **id-03 multi-persona clustering** — multi-key union-find (links[], file/content hash, MinHash) nightly per org; write cluster-id back + materialize `SIMILAR_TO`. **M.** Reuses shingle code + `links[]`.
9. **id-05 photo pHash** — add Pillow+imagehash; hash `image_url` at ingest; cross-candidate Hamming compare in #8's cohort pass. **S-M.** Blocklist placeholder hashes.

**Wave 3 — Content/timeline extensions (full-score, $0):**
10. **C9 years-vs-sum** + **C12 historical overlaps** — extend `detect_timeline_inconsistencies`; multi-year threshold; fold into `compute_integrity_penalty`. **S each.**
11. **C14a tech anachronism** — curated tool→release-year table + date-matcher in `fraud_detection.py`. **M.**
12. **C16 score-aware tier** + enable by default — promote `cv_only_role + date_shift` to bounded penalty. **S.**
13. **C7 specificity rubric** — tighten the live `claims_to_verify` prompt field; surface in report. **M.**

**Wave 4 — Document hygiene module (deterministic, no new dep):**
14. **DOC-02/03/04 + DOC-10 + DOC-09** — one PyPDF2 ContentStream pass computing Tr-mode, fill-colour, bbox/size, active-content, image-only; gate the unambiguous cases at pre-screen, persist all at ingest. **M-L combined.** Corrects the "needs PyMuPDF" assumption.

**Wave 5 — Cheap intake + external (de-risks the standalone-ATS roadmap):**
15. **bp-09 disposable-email** (free blocklist, curate out privacy-forwarders) — **S.**
16. **bp-07 velocity** (indexed windowed aggregate) — **S-M.**
17. **Source/referral-collusion clustering** (group-by on `lead_source`/`source`) — **S-M.**
18. **ext-06 domain-age** (RDAP, scope to recent/unverified employers) — **S-M.** First paid external (~$0.001/domain).
19. **bp-05 IP/VPN flag** (reuse `middleware.py` `_get_client_ip`; free vendor tier) — **M.**

**Wave 6 — DOC-01 render-OCR diff (async, ~$0.003-0.006/CV):** the one high-cost build worth doing — start with the Anthropic-vision variant (zero new vendor, reuses metering). **L.** Depends on Wave 4 shipping the cheap subset first.

**Deferred-with-trigger (do not build now):** id-04, id-06, id-09 (seed hashed keys now), bp-03, bp-08, xsc-02 LLM-semantic half, xsc-03, DOC-11, DOC-12, C3, C10, C11, C19, ext-03/08 GitHub-portfolio, ext-09 licence, all KYC.

---

## 6. Open decisions for Sam

1. **C13 / flag-gated penalties — flip on?** Several integrity penalties (`HOLISTIC_INTEGRITY_PENALTY_ENABLED`, `FRAUD_WORKABLE_DIFF_ENABLED`, hidden-text/shingle flags) are built but flag-gated. Approve turning them on after a shadow-data FP review, or do you want a defined shadow window first?

2. **C6 injection — escalate flag→hard-cap per-org?** Hidden-instruction attacks run ~80% success and the strip already neutralises them. Do you want an opt-in per-org *cap* (not just strip) for injection hits, given it can flip a verdict?

3. **Hard-gate vs flag-only on the duplicate-identity triad.** id-01/xsc-07 can deterministically hard-cap canonicalized-email/exact-file dups. Comfortable letting them *gate* (with name-distance + agency-contact exclusion), or flag-only until you've watched the cluster output? (My rec: gate email-canon dups, flag everything phone/photo.)

4. **Assessment integrity stance — penalty or flag?** Git-forensics + bp-01/bp-02 are strong but evadable-or-noisy. Do they (a) only surface on the standing report for the human, or (b) feed a bounded score penalty? (My rec: bounded soft penalty for git-forensics single-dump/foreign-committer; flag-only for client telemetry.)

5. **External spend authorization.** ext-06 domain-age (~$0.001/domain) and bp-05 IP/VPN (free tier) are the only externals in the build set. Approve a small monthly cap, or keep everything $0-internal for now and revisit externals when standalone-ATS intake goes live?

6. **The deepfake/video gap.** Named but un-buildable today (no video-capture surface). Park it as an explicit "if we ship proctored remote interview/assessment" trigger — agreed, or do you want a virtual-camera-detection spike scoped now given how fast this threat is rising?

7. **KYC/DataFlow as a client-gated module.** Confirm these stay **out of the screening funnel** entirely and only appear as recruiter-triggered, candidate-consented, employer-owned late-stage actions — i.e. a product/partnership decision, not a fraud-engine lever.

**Files touched by the build set:** `backend/app/services/fraud_detection.py`, `document_hygiene.py`, `pre_screening_service.py`, `cv_matching/holistic.py`, `cv_parsing/grounding.py` + `cache.py` + `pdf_text.py`, `components/assessments/service.py` + `integrity.py` + `submission_runtime.py`, `components/scoring/analytics.py`, `components/integrations/workable/sync_service.py`, `candidate_graph/` (SIMILAR_TO), `platform/middleware.py`, `config.py`.