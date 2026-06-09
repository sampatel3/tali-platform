# Workable Partnership Program — Application Draft (Taali)

**Status:** Draft for submission · **Date:** 2026-06-04 · **Owner:** Sam

Workable's partner onboarding is an application + fit review (not self-serve): submit the form → Partnerships team reviews → on acceptance you get a sandbox account, API docs, and dedicated technical support; integration build is expected to take < 90 days with their QA. Apply for the **Assessment** integration category.

- Apply: https://www.workable.com/partnership-program/apply
- Assessment partner program: https://www.workable.com/developers/partner-program/assessment
- Provider API docs: https://workable.readme.io/page/assessment-providers

> Placeholders marked `[TODO]` need Sam's real values before submission. Do not assert compliance certifications we don't hold.

---

## Application fields

**Company name:** [TODO: legal entity] — product: **Taali**
**Website:** https://taali.ai
**Primary contact:** Sam Patel — [TODO: email] (note: Workable account used for testing is sampatel@deeplight.ae)
**HQ / region:** [TODO]
**Integration category:** Assessment provider
**Existing Workable integration?** Yes — Taali already integrates with Workable via OAuth (scopes `r_jobs`, `r_candidates`, `w_candidates`) for candidate sync and decision write-back. This application is to additionally list Taali as a native **Assessments Provider** in the Workable marketplace.

---

## Product description (short)

Taali is an **agent-native hiring platform**. For technical and AI roles, Taali runs candidates through real, hands-on assessments in live cloud sandboxes (not multiple-choice quizzes), where the candidate works in an actual repo with an AI coding assistant. Taali captures the full working session, then scores it with AI against a role-specific rubric — producing a single score, a clear recommendation, and transparent, evidence-cited reasoning that a recruiter can defend.

## Product description (long, for the listing)

Taali assesses how candidates actually work. Each assessment provisions a real coding sandbox seeded with a role-relevant task; the candidate solves it using an AI assistant, and Taali records their prompts, code, test outcomes, and process. Taali then evaluates the submission across role-specific dimensions (skills coverage and depth, problem-solving, efficiency, independence), blends it with CV-to-role fit, and returns:

- a **score** (0–100),
- a **recommendation/grade** (mapped to Workable's no / yes / definitely-yes), and
- a **plain-language summary plus per-criterion evidence**, with a shareable report and a downloadable PDF.

Inside Workable, hiring managers attach a Taali assessment to a pipeline stage and send it in one click; results land on the candidate's Workable timeline with the score, grade, summary, and report — no context-switching.

## Why it's valuable to Workable customers

- **Signal, not trivia:** real-work assessment for engineering / AI / data roles, where résumé screening is weakest.
- **Defensible decisions:** every score comes with cited evidence and a written rationale — useful for fairness and audit.
- **Zero workflow change:** send from inside Workable, results on the timeline.
- **AI-native:** built around an AI assistant as a first-class part of the candidate's workflow — assessing the skills that matter as the way people actually work changes.

## Technical readiness

- **Auth:** existing Workable OAuth partner app (auth-code flow against `workable.com/oauth/token`), per-org tokens, refresh + revoke handled. For the Assessments Provider model we will expose the bearer-authenticated provider endpoints.
- **Provider endpoints:** `GET /tests` (our task catalog) and `POST /assessments` (create from candidate name/email/job) — both thin adapters over our existing assessment engine.
- **Results callback:** we `PUT` status `pending → completed/expired` to the supplied `callback_url`, delivered through a durable, retried outbox (no lost results), with `results_url`, `score`, `grade`, `summary`, `details`, and a report PDF attachment.
- **Scale/robustness:** existing integration already respects Workable's rate limits (10 req/10s), serializes per-org writes via a mutex, and retries transient failures.
- Detailed mapping in our internal `WORKABLE_ASSESSMENTS_PROVIDER_SPEC.md`; every functional piece (assessment runtime, scoring, evidence, share links, PDF report, metering) is already in production.

## Security & data handling

- Per-org credential isolation; OAuth tokens and provider keys stored per organization; secrets hashed/encrypted at rest.
- Candidate PII received from Workable (name, email, phone) is used solely to create and deliver the assessment; data is org-scoped with hard tenant isolation.
- Signed/verified webhooks; least-privilege scopes.
- [TODO: state real posture — GDPR stance, data residency, retention policy, SOC 2 status (in progress / none yet), DPA availability. Do not claim certifications we don't have.]

## Support model

- We provide technical support during build and own bug-fixes for the integration; Workable provides first-level customer support per the program.
- [TODO: support email / SLA / docs URL for the public listing.]

## Commercial

- Taali bills customers directly (usage-based, via Stripe); the marketplace listing is distribution. [TODO: confirm whether Workable expects any rev-share or listing fee for the Assessment category.]

## Target customers

- Companies hiring software / AI / data engineers who already use Workable and want real-work signal earlier in the funnel; well-suited to teams adopting AI-assisted engineering. [TODO: add current customer count / logos if shareable.]

---

## Pre-submission checklist

- [ ] Fill every `[TODO]` (legal entity, contact email, region, compliance posture, support SLA, customers).
- [ ] Confirm commercial terms (rev-share / listing fee) for the Assessment category.
- [ ] Decide the public listing name, one-liner, logo, and screenshots (results-on-timeline shot is the money screenshot).
- [ ] Confirm we can request a Workable **sandbox** account for QA.
- [ ] Have `WORKABLE_ASSESSMENTS_PROVIDER_SPEC.md` ready to share with their engineering team during QA.
- [ ] Verify our existing OAuth app's partner status in the Workable partner dashboard (we appear to already hold partner OAuth credentials — confirm whether marketplace listing is a separate step).
