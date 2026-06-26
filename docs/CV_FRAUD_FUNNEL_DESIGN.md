# Application-Funnel Fraud — Detecting CV Gaming & Lying

*For Sam. Backend Python/FastAPI; all paths under `backend/` unless noted. Frontend React under `frontend/src/`.*

**Status:** Design doc (build spec) · **Date:** 2026-06-26 · **Owner:** Sam

> Scope correction over the earlier docs. `CV_FRAUD_DETECTION_BUILD_DECISION.md` enumerated every lever but conflated two objectives. This doc is **only** the **application-funnel** problem: catching, *at application time*, candidates who **lie on their CV** or **tailor it to the job spec** to win a match-score they don't deserve — without rejecting genuine strong matches. **Assessment-stage fraud** (git-evidence forensics, proctoring) is a separate objective on its own track and is explicitly out of scope here.

---

## 1. The problem, stated precisely

Taali scores candidates on CV↔spec match. **That is exactly the lever a gamer pulls:** mirror the spec, claim the keywords, and the score rewards it — so a high match-score is *ambiguous*. It means **"genuinely qualified" OR "gamed the spec," and the score alone cannot tell them apart.**

The hard constraint, in Sam's words: *bring in genuine high-match candidates, remove the fakes.* So the design must obey:

> **A high match-score is never, by itself, a fraud signal.** The discriminator must be orthogonal to "matches the spec well" — otherwise we punish our best real candidates.

---

## 2. The model — two prongs + triangulation

### Prong 1 — Score integrity (make gaming not pay). *The FP-safe core.*

A genuinely qualified person backs each claimed competency with **specific, concrete, verifiable** work. A gamer lists the spec's words with thin backing. So instead of bolting a fraud-flag onto a high score (FP-risky), we **make the score only credit a requirement when there's grounded, cited evidence** — not when the CV merely asserts or echoes it.

- Gaming stops paying — claims-without-evidence score low **by construction**.
- Genuine candidates are untouched — they have the evidence.

**This already half-exists.** `grounded_evidence.py` uses Anthropic Citations so a "met" verdict only counts when a verbatim CV quote backs it (`grounded = len(evidence) > 0`; ungrounded "met" is flagged and ignored by the qualifying gate). The holistic scorer (`holistic.py`) also emits per-requirement `evidence` quotes and `_ground_quotes()` fuzzy-locates them, **dropping any quote not found in the CV while keeping the status**. The gap: the **score** keeps the status even when grounding fails — so a tailored CV of confident, un-evidenced claims still scores high.

**The build:** derive a **grounding-coverage** metric from the holistic per-requirement evidence (after `_ground_quotes`), and act on the **alignment × grounding conjunction**:

> **high match × LOW grounding coverage on the must-haves = gamed-suspect.** &nbsp; high match × HIGH coverage = genuine.

- Among **must-have** requirements graded MET/PARTIAL, what fraction carry a surviving verbatim quote (`evidence_quotes` non-empty after grounding)?
- `ungrounded_match = overall ≥ HIGH and coverage ≤ LOW and met_must_haves ≥ MIN`.
- **Action:** always **flag** ("match driven by N un-evidenced claims: X, Y"); optionally a **bounded discount** (gated, default shadow) when `ungrounded_match`. Never a hard reject.
- **FP control:** terse/ESL CVs legitimately quote less → the discount is **bounded + conservative threshold + flag-not-gate**. Coverage is computed only over **must-haves** (where evidence should exist), not the whole CV.

Rides existing holistic per-req evidence — **$0, no new LLM call.** Files: `cv_matching/holistic.py`, `cv_matching/schemas.py`, reuse `candidate_search/grounded_evidence.py` semantics.

### Prong 2 — Fraud flags + cross-source corroboration (catch the lies the score can't see).

Orthogonal evidence that a claim is fabricated/inflated, independent of how well it matches the spec.

**(a) Deterministic — gate early at pre-screen (cheap, ~zero FP):**
- Verbatim JD copy-paste *(live)*; hidden-text / prompt-injection / invisible-or-white stuffing — direct manipulation of the scorer *(live; colour/render-mode is the cheap PyPDF2 add)*; impossible-timeline arithmetic *(live, penalty on)*.

**(b) Cross-source corroboration — flag at score/async (never auto-reject):**
- **CV↔Workable history diff** *(live, flag)* — independent self-reported source.
- **Unverified / shell employers** — `company_unverified` grounding *(live)* + company **domain-age** (~$0.001/domain).
- **CV-internal coherence** — years-claimed vs timeline-sum, tech anachronism, seniority-vs-dates *(deterministic, bounded)*.
- **LinkedIn URL cross-check** *(new)* — see §3.
- **Knowledge-graph collective corroboration** *(new — the differentiator)* — see §4.

### Triangulation — why this is FP-safe *and* powerful

Stack the corroboration axes — **CV-internal grounding (Prong 1) + LinkedIn + the graph + the Workable diff** — into a per-candidate corroboration picture. A genuine high-match corroborates across them; a tailored/fabricated one fails one or more.

> **Require multiple independent disagreements before it bites.** One source disagreeing is a *question* (flag for review). Several disagreeing, or a deterministic artifact (hidden text, copy-paste, impossible dates), is *action*. This is the rule that keeps genuine high-matches safe while removing fakes.

Positive corroboration also feeds Prong 1: a claim corroborated by LinkedIn/graph earns full evidence-credit; an anomalous one gets the grounding discount.

---

## 3. LinkedIn URL cross-check (new)

**Do the URL-provided version — it's easy and high-confidence; skip name-based discovery.** We usually already hold the exact profile URL: `cv_sections.links[]` (parsed from the CV) + Workable `social_profiles`. No identity-matching guesswork.

- **Fetch** the public profile (provided URL only), extract employer / title / dates / tenure.
- **Diff** vs the CV's structured history (reuse `diff_cv_vs_workable_history` shape).
- Match → corroborates. Mismatch → **flag for review** (candidate controls both docs → a question, not a verdict). No URL / no profile (common in MENA) → **fail open, no penalty for absence.**
- **Cost is operational, not legal** (Sam's ruling: no legal blocker on public data): LinkedIn blocks unauthenticated fetches, so this needs a fetch route that survives blocking (a scraping provider, or accept partial coverage) — budget that, and run it **async on the provided-URL subset**, never a top-of-funnel gate.

Placement: **async enrichment**. Files: new `services/external_corroboration.py`, reuse `cv_sections.links` + `candidate.social_profiles`; persist into `integrity_signals`.

---

## 4. Knowledge-graph collective corroboration (new — the moat)

Use the Graphiti graph as a **collective truth model**: across candidates we've seen, what tech stack co-occurs with **(Company X, role-family R)**? Test a new candidate's claim against it.

- Claimed stack inside the seen distribution for that company+role → **corroborates** (genuine).
- Claimed stack an outlier nobody else from there shows (bleeding-edge ML stack at a bank where every other candidate ran SAS/SQL) → **anomaly → route to scrutiny** (classic CV-inflation-to-match-spec tell).
- Claimed employer with **no graph presence** → fabricated-employer signal (ties into domain-age / `company_unverified`).

**Feasibility (verified against the code):**
- First-class nodes exist — `NODE_COMPANY`, `NODE_SKILL`, `NODE_ROLE` (`candidate_graph/schema.py`); edges `WORKED_AT` (Candidate→Company), `HAS_SKILL` (Candidate→Skill).
- **No materialized `Company→Skill` edge**, but `candidate_graph/search.py::colleague_neighbourhood` already aggregates company×skill post-walk — the pattern to copy. New query `company_tech_stack(company, role_family)` = multi-hop walk + aggregate, **~a small wiring job, no graph remodel.**
- Compare the candidate's claimed stack to that aggregate (overlap + Voyage-embedding distance to the company-role centroid).
- `SIMILAR_TO` edge is **defined but dormant** — out of scope for v1 (materialise later for speed).

**Designing for the FP constraint:**
- **Role-scoped** (company × role-family), not company-wide — big employers run many stacks.
- **Distributional + tolerant** — "is this *plausible*," not "does it match the mode." Genuine outliers exist.
- **Cold-start fail-open** — fire only when the graph holds **≥ N independent observations** for that (company, role) cell; else silence. *Mandatory:* the graph is populated **only from in-assessment/advanced candidates** (cost control in `candidate_graph/sync.py`), so per-company coverage is thin early. Most powerful where you have density; harmless where you don't.
- **Corroboration-first, never a gate** — positive alignment boosts confidence; a negative only flags.
- **Prod-gated** on `NEO4J_URI` + `VOYAGE_API_KEY` (both set in prod); degrades to no-signal cleanly when unset.

**Why it differentiates:** a **data-network-effect verification layer** — stronger the more candidates Taali sees, strongest for the popular target employers where gaming concentrates, and uncopyable without the same accumulated data.

Placement: **async enrichment**. Files: `candidate_graph/graphrag_queries.py` (new query), new analyser in `services/`, persist into `integrity_signals`.

---

## 5. Placement design (the pre-screen vs full-score split)

| Tier | When | What lands here | Can it gate? |
|---|---|---|---|
| **Ingest** | parse-time, $0, deterministic | invisible-char/hidden-text detection, employer grounding, content-hash, link extraction | gate only deterministic + ~0-FP |
| **Pre-screen** | Haiku, *before* paid Sonnet | verbatim copy-paste, hidden-text (Tr/colour), impossible-timeline | **yes** — deterministic, low-FP; a gate here *saves* the Sonnet call |
| **Full holistic score** | needs LLM-extracted timeline/claims/evidence | **Prong 1 grounding coverage + conjunction**, CV↔Workable diff, anachronism, years-vs-sum | bounded penalty / flag only |
| **Async enrichment** | off the hot path | **LinkedIn URL diff, graph corroboration**, domain-age | flag only |
| **On-demand late-stage** | $-per-candidate, consented | (out of scope — KYC/DataFlow) | human-owned |

**The rule:** *deterministic + low-FP → may gate, and gate early to save spend. Probabilistic or fairness-risk → flag or bounded discount, surfaced to the human, never a cap.*

---

## 6. Reconcile with what's already live (#700)

| Live signal | Decision |
|---|---|
| Verbatim copy-paste (pre-screen hard cap) | **KEEP** |
| Hidden-text / injection strip+detect (+cap action) | **KEEP**; add cheap PyPDF2 colour/render-mode (Tr 3) detection |
| Timeline sanity penalty (now on) | **KEEP** |
| Unverified extraordinary claims penalty (now on) | **KEEP**; fold into Prong-1 grounding view |
| CV↔Workable diff (flag) | **KEEP + RE-WEIGHT** — becomes a corroboration axis in triangulation |
| Shingle / dilution copy-paste, unverified-employer | **KEEP** as flags / corroboration inputs |

Nothing is removed. The rolled-out penalty flags stay on; the new work layers Prong 1 + cross-source corroboration on top. **Open:** whether to keep the live penalties applying during the build or return to shadow until triangulation is in (Sam's call).

---

## 7. Build plan & sequencing

**Wave 1 — Prong 1: evidence-grounded score integrity ($0, rides holistic).** *Build first — it's the FP-safe core that actually stops spec-gaming.*
1. Grounding-coverage metric over must-have requirements (after `_ground_quotes`) in `holistic.py`; `ungrounded_match` conjunction signal.
2. Persist under `cv_match_details.integrity_signals.grounding`; bounded discount behind a flag (default shadow).
3. Surface in the "Verify before interview" panel (the un-evidenced must-haves).
4. Tests: gamed CV (high match, low grounding) flags; genuine CV (high match, high grounding) clean; terse genuine CV not over-penalised.

**Wave 2 — Graph collective corroboration (the differentiator).**
5. `company_tech_stack(company, role_family)` in `graphrag_queries.py` (multi-hop aggregate, cold-start fail-open).
6. Claimed-stack-vs-distribution analyser; corroborate/anomaly signal into `integrity_signals.graph_corroboration`. Async.
7. Tests with a synthetic graph fixture; cold-start returns no-signal.

**Wave 3 — LinkedIn URL cross-check.**
8. Fetch route (provider/abstraction) + public-profile parse; diff vs CV history; async; flag-only. `integrity_signals.linkedin`.

**Wave 4 — Triangulation + cheap deterministic adds.**
9. Corroboration aggregator (combine grounding + Workable + graph + LinkedIn → require-multiple-disagreements); the "verify before interview" panel becomes the triangulated view.
10. PyPDF2 colour/render-mode hidden-text; domain-age; years-vs-sum + anachronism.

**Open decisions:** (1) Prong-1 discount on/shadow at launch? (2) Graph cold-start `N` threshold? (3) LinkedIn fetch route — provider vs partial-coverage best-effort? (4) Triangulation: how many disagreements before a bounded discount vs flag-only? (5) live penalties — keep on or shadow during the build?
