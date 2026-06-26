# Tali Scoring Engine — Integrity-Aware Redesign

*For Sam. Backend Python/FastAPI under `backend/app`; FE React under `frontend/src`. Locks the decisions from the 2026-06-26 design chat. Builds on `CV_FRAUD_FUNNEL_DESIGN.md` (which is now a *component* of this) and the live holistic engine v2.1.0.*

**Status:** Design doc · decisions locked · **Date:** 2026-06-26 · **Owner:** Sam

---

## 1. The frame — one engine, 4 inputs → 3 outputs

Stop thinking of "the score" and "the fraud flags" as separate systems. One engine:

- **Inputs:** **Match** (spec fit) · **Evidence** (grounding) · **Integrity** (fraud/coherence) · **Corroboration** (graph / GitHub / Workable).
- **Outputs:** **(1) Match score + Integrity/Trust band** · **(2) narrative summary** · **(3) agent warnings**.

Today only *Match* drives the number; Evidence/Integrity/Corroboration are advisory side-channels. This redesign makes them first-class — but in the **right output** for each, so we never punish a genuine high-match on a false positive.

---

## 2. Locked decisions (from Sam)

1. **Two readouts, not one blended number** — a **Match score** (suitability) + an **Integrity/Trust band** (how much we trust the match is real).
2. **Probabilistic corroboration = WARN-ONLY** — it never silently lowers the Match number — **conditional on the warning being clear and the information clearly reported in the candidate report.** Only **deterministic, high-confidence** fraud (copy-paste, hidden-text, impossible dates) moves the Match score.
3. **Outcome-learned graph signals → into the score, bias-gated.** Deepen **GitHub** too (richer signals, absence-neutral).

---

## 3. The score model

### Match score (`role_fit_score`, 0–100) — "how well do they fit"
Holistic spec-fit, as today. Moves **only** on:
- **Deterministic high-confidence integrity** — copy-paste cap, hidden-text cap, impossible-timeline penalty. *(Already wired + live.)*
- **Bias-gated graph outcome priors** (NEW, decision 3) — does this *(profile × company × role-family)* resemble profiles that have **succeeded** here? A bounded, bias-gated nudge. This is the graph's **outcome** use, distinct from its anomaly/corroboration use.

### Integrity / Trust band (NEW) — "how much do we trust it"
A recruiter-facing band — **High / Medium / Low + "N to verify"** — summarising every integrity + corroboration signal via the **triangulation verdict**. It is the **second number**, shown beside the match. A genuine candidate = high match **+ high trust**; a gamer = high match **+ low trust** — surfacing the discriminator that was invisible before. **It does not reduce the Match score** beyond the deterministic penalties already inside it.

### Signal routing (the heart of the design)

| Signal | Match score | Trust band | Warning + report + summary |
|---|---|---|---|
| Copy-paste (pre-screen) | **cap** (deterministic) | ↓↓ | ✓ |
| Hidden-text / injection | **cap** (deterministic) | ↓↓ | ✓ |
| Impossible timeline | **penalty** (deterministic) | ↓ | ✓ |
| Grounding coverage (spec-gaming tell) | — *(warn)* | ↓ | ✓ |
| Workable diff · unverified employer · coherence | — *(warn)* | ↓ | ✓ |
| **Graph anomaly** (stack ≠ peers at employer) | — *(warn)* | ↓ | ✓ |
| **Graph outcome priors** (skill→outcome, top-performer overlap) | **bias-gated nudge** | — | (positive, in summary) |
| **GitHub** (languages · activity · account-age) | — *(warn; absence-neutral)* | ↑ / ↓ | ✓ |

The rule the table encodes: **the Match number only moves where we're deterministically sure (fraud artifacts) or where we have a bias-gated positive prior. Everything probabilistic lives in the Trust band + warnings.** That is decision 2 made concrete.

---

## 4. Graph & GitHub — using them fully (decision 3)

Today corroboration uses **1 of ~6** graph queries (`company_tech_stack`) and only GitHub repo languages. The depth:

**Graph — split the two uses:**
- **Corroboration / anomaly** (`company_tech_stack`): claimed stack vs peers → **warn-only** (today's behaviour). Trust band + warning.
- **Outcome priors** (`skill_to_outcome_paths`, `similar_past_candidates`, `company_overlap_with_top_performers` — already powering the *agent* via `synthesise_prior`): fold into the Match score as a **bounded, bias-gated suitability prior.** Reuse the autoresearch bias gate so it can't encode protected-characteristic bias. *Shadow first.*

**GitHub — deepen beyond languages, stay absence-neutral:**
- Add contribution recency, **account-age vs claimed experience**, repo substance.
- **Absence is never a minus** (private work is invisible + biased). Strong positive corroboration → **trust-band boost**; a contradiction (e.g. account created after claimed GitHub-based work) → warning.

---

## 5. Agent warnings + summary text

Corroboration routes to **two human-facing channels** (decision 2's "clearly reported" condition):
- **Agent-decision warnings** in the Decision Hub — *"peers at {employer} rarely list {skill} — verify"*, *"GitHub shows none of the claimed {stack}"*.
- The candidate **summary text** — woven into the holistic narrative: *"Strong match on paper; note GitHub shows no {stack} and peers from {employer} typically don't list it — confirm in screening."*

This is the warns-not-blocks channel: loud and actionable, with **no silent false-positive penalty** to the number.

---

## 6. Visibility (build first — already decided)

Candidate report overview (`CandidateStandingReportPage`):
- Add a consolidated **"Integrity & corroboration"** block beside the requirements, showing the 3 outcomes (copy-paste / integrity layer / graph+GitHub) + the **Trust band** as the headline.
- **Enrich the requirement grades** — they already render as met/missing; add the **evidence quote + 0–100 sub-score** per requirement (call-2 data we already persist, just don't show).

---

## 7. Timing / re-evaluation

Corroboration is async (post-score). The **Trust band + warnings + summary refresh** when enrichment lands, via the existing `deterministic_decision_refresh` mechanism. The **Match score** only re-computes when a deterministic penalty or a bias-gated prior changes — so the async corroboration never silently churns the number.

---

## 8. FP / fairness guardrails (non-negotiable)

- The Match number moves **only** on deterministic high-confidence fraud + bias-gated priors — never a probabilistic hunch.
- Probabilistic signals are **warn-only + clearly reported** (decision 2's condition).
- Outcome priors run through the **bias gate**; GitHub stays **absence-neutral**.
- Everything stays **warn, never auto-reject** — a human owns the decision. (The one place a signal may gate the *auto-advance* is a deterministic artifact → Hold-for-review, never auto-Reject.)

---

## 9. Phased build

| Phase | What | Risk |
|---|---|---|
| **P1 — Visibility** (now) | Integrity & corroboration block on the candidate report + enriched requirement grades + the **Trust band** readout (band-from-triangulation helper). | Low — decided, FE + 1 backend helper |
| **P2 — Trust band as first-class** | Compute + persist the band; surface beside the match everywhere (lists, report, decision hub, kanban). | Low–med |
| **P3 — Warnings + summary** | Corroboration → agent-decision warnings + woven into summary text. | Med (touches narrative) |
| **P4 — Graph outcome priors → match (bias-gated)** | Fold skill→outcome / top-performer overlap as a bounded bias-gated prior on the Match score. **Shadow first.** | High — changes the number; needs bias review |
| **P5 — GitHub depth** | Richer GitHub signals, absence-neutral → trust band + warnings. | Low–med |

---

## 10. Open

- **Trust band shape:** categorical (High/Med/Low + count) vs 0–100? *(Lean categorical + "N to verify" — clearer for recruiters than another number to interpret.)*
- **P4 bias gate:** reuse the autoresearch gate as-is, or a scoped variant for the prior?
- **P4 magnitude:** how big a nudge may an outcome prior give the match (e.g. ±5 bounded)?
