# Candidate Contest / Representations Process

> **Status: DRAFT v0.1 — prepared for counsel review; not legal advice; do not sign/publish without counsel sign-off.**
> **Date:** 2026-07-23 · **Owner:** Sam Patel
>
> The operational process behind the candidate's Art 22 / UK Art 22C rights, implementing Horizon 1, item 6
> of `docs/COMPLIANCE_RISK_AND_ROADMAP.md`. Minimum-viable, email-based; turned into product in Horizon 2.
> This gives candidates a real way to get information, make representations, obtain human intervention, and
> contest a recommendation. It ties into the existing `data_subject_requests` machinery.

---

## Scope

Covers a candidate who wants to understand, respond to, or challenge a rule-based recommendation Taali produced about them. Delivers all four UK Art 22C(2) safeguards — **(a) information, (b) representations, (c) human intervention, (d) contest** — and the equivalent EU Art 22(3) / WP251 expectations.

## Intake channels

1. **Via the customer** — the candidate contacts the customer (the controller) using the contact in the customer's privacy notice. This is the primary route; the customer owns the decision.
2. **Via Taali** — the candidate emails **`hello@taali.ai`**. Taali does **not** decide the merits. Taali acknowledges, then routes the request to the relevant customer as controller (per `DPA_TEMPLATE.md` clause 8(a)).

Either way the decision-maker is the customer's authorised human. Taali's role is to support that human with the full evidence.

## SLAs

- **Acknowledge within 3 working days** of receipt.
- **Resolve within 30 calendar days** of a validated request (or sooner where the customer's statutory deadline is shorter).

These mirror the DSR SLAs in `DPA_TEMPLATE.md` clause 8(c) and the candidate privacy notice.

## What the reviewing human must do

The reviewer must be a person with **authority and competence to change the outcome** — not a rubber stamp. Before responding they must:

1. Open and consider the **full evidence**: the cited verbatim CV/answer evidence, the **rule path** (which criteria drove the recommendation), the score provenance (date + engine version), and the **assessment record** where relevant.
2. Take into account any **representations** the candidate has made.
3. Reach an independent judgement, and **exercise the authority to change the recommendation** where the evidence or representations warrant it.
4. **Not** rely on the score or verdict alone.

This is the point at which "meaningful human review" is real. The Horizon-2 evidence pack (measured override rates, reviewer authority definition) will make this demonstrable to a regulator (roadmap R1/R2).

## Logging

- Log every contest/representation as a request record, reusing the existing pattern in `backend/app/domains/compliance/` (`data_subject_service.py` / the `data_subject_requests` table) so the request and its outcome are **durable evidence that survives even an erasure**.
- `[BRACKET — decide whether to add a dedicated request_type (e.g. "contest") to the DSR model, or record contests as a typed note on the existing request. Product decision for H2.]`
- Record: who reviewed, what evidence they opened, the representations received, the outcome (upheld / changed), and the reasoning.

## Escalation

- If the reviewer lacks authority or the case is borderline, escalate to `[CUSTOMER — named senior reviewer / hiring manager]`.
- If the candidate is dissatisfied, tell them they may complain to a supervisory authority (ICO in the UK; local DPA in the EU; `[UAE authority if relevant]`).
- Taali-side escalation for routing/tooling problems: `[Sam Patel / owner]`.

## What the candidate gets back

A response in **plain language** that gives the **outcome** (recommendation upheld or changed) and the **reasoning** — what the recommendation was based on and how their representations were considered — at a level that is meaningful without exposing other candidates' data or trade-secret internals beyond what transparency law requires. `⚖ COUNSEL` on how much logic detail must be disclosed.
