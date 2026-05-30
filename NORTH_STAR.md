# Taali — North Star

> **Product north-star for Taali.** This repo (`tali-platform`) is the **live
> Taali product** — the hiring brand. Objective · non-goals · decision principles
> · killable claim below. The platform-level canonical reference (*one product /
> one substrate / many brands*, plus the CI-gated invariants) lives in the
> `north-star/` repo; Taali is a **brand on the `mainspring` substrate** and
> inherits those invariants. This file is Taali's own bet and the measuring stick
> the platform audit grades it against.
>
> *Reconciliation note:* the central `north-star/` model currently labels
> `tali-platform` "legacy" and treats `taali-brand` as the brand. Adopting this
> file makes `tali-platform` the canonical live Taali; that flip should be ratified
> by an ADR in the `north-star/` repo rather than left as drift.

## Objective

Taali makes hiring decisions defensible by capturing how a candidate actually
works with AI — the prompts, the iterations, the abstention, the recoveries — and
turning that into a structured, auditable, role-fit-grounded recommendation that a
recruiter and a regulator can both stand behind.

## Non-goals

- **We do not score "talent" in the abstract.** Taali measures observable working
  behaviour against a specific role spec — not personality, not potential, not
  pedigree.
- **We do not generate hire/no-hire verdicts that bypass a human recruiter.** The
  recommendation is the input; the human is the decision.
  *(Scope qualifier — `TAA-11`.)* This is a **default**, not an absolute invariant.
  Recruiter-configured auto-execution is a deliberate, **opt-in** capability: a role
  may enable `role.auto_reject` (default **off**) to let the agent auto-execute the
  *reject* prong in Workable. The *advance* prong is an internal hand-back with no
  Workable write. So the bypass is recruiter-authorised and reject-only, not an
  unsupervised hire/no-hire verdict; the opt-in surface and its guardrails are
  tracked in `TAA-11`.
- **We do not optimise for screening throughput at the cost of fair-treatment
  evidence.** A faster but less defensible score is a worse product.
- **We do not build features that would make us indistinguishable from generic ATS
  tooling** (job-board scraping, mass outreach, calendar bots). Taali's job ends
  where adjacent ATS jobs begin.

## Decision principles

1. **The session is the artefact.** Everything we score must trace to something
   the candidate actually did, observable in the session log, replayable later.
2. **Role spec first, model second.** Role-fit comes from CV-vs-job evidence, not
   from a generic "good candidate" prior.
3. **EEOC/fair-hiring posture is inherited from Mainspring, not bolted on.** When
   the substrate adds compliance rule packs, Taali consumes them.
   *(Current vs target — `TAA-9`/`TAA-29`.)* Today this is the **target**, not the
   state: the EEOC engine is **brand-owned** in this repo
   (`backend/app/decision_policy/bias_audit.py`, **0 mainspring imports** — it
   consumes no mainspring compliance rule pack). Inheritance from the substrate is
   the convergence target (`TAA-29`); until that lands, fair-hiring is bolted on
   brand-side, the inverse of what this principle asserts.
4. **Determinism over personalised cleverness.** Two candidates with the same
   evidence get the same score, full stop. No model-driven adjustment, no
   recruiter-context drift.
   *(Which layer — `TAA-20`/`TAA-8`.)* This principle governs the **final verdict**:
   the decision engine (`decision_policy/engine.evaluate`) is pure-Python and
   deterministic. It does **not** yet hold for every score feeding it — the
   per-dimension **rubric grader** is LLM-driven and was **non-deterministic**
   (no `temperature`/`seed` pinned, so two identical submissions could score
   differently) until `TAA-8` pinned `temperature=0`. Read "the same score" as
   the target for the scored inputs, achieved at the verdict layer and restored
   for the rubric layer by `TAA-8`.
5. **Recruiter trust is earned with surfaced reasoning, not hidden by a confident
   number.** Every score links to the evidence; every evidence link is verifiable.

## Killable claim

"AI-native engineering and knowledge-work skill is real, measurable, and
predictive of role performance in a way generic technical screening misses." If
that turns out to be wrong — if how someone works with AI doesn't actually predict
how they perform on the job — Taali is selling a more elegant version of a useless
metric, and we should know it from outcome data within 12–18 months of real
customer use.
