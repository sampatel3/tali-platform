# Art 50 AI-interaction transparency audit — candidate-facing surfaces

> **Audit date 2026-07-23. Art 50 applies from 2 Aug 2026.**
> **Owner:** Sam Patel · Scope: EU AI Act Art 50(1) — natural persons must be informed they are
> interacting with an AI system, unless obvious. This audit covers **candidate-facing** surfaces only
> (see §2.3 of `docs/COMPLIANCE_RISK_AND_ROADMAP.md`). Recruiter- and client-facing surfaces are out of
> scope and were not modified.

## What counts as candidate-facing here

A surface is candidate-facing only if the actual **job applicant** interacts with, or receives output from,
an AI system. During the sweep two routes that look candidate-facing turned out not to be:

- `/c/:applicationId` sets `applicationId` (not a share token), so `isShareRoute` is false and it loads
  via authenticated recruiter APIs — it is a short **recruiter** URL, not a public candidate surface.
- `/share/:shareToken`, `/submittal/:token`, `/report/:token` render in **"client" (hiring company)** or
  **"recruiter"** mode (`shareViewMode` in `CandidateStandingReportPage.jsx`). Neither recipient is the
  applicant. Taali does not send applicants their own scores/reports (ATS owns candidate comms), so these
  are not candidate-facing for Art 50 purposes.

## Audit table

| Surface | What the candidate sees | AI disclosure present? | Verdict | Fix |
|---|---|---|---|---|
| **Assessment invite email** — `assessment_invite_html` / `assessment_invite_text` (`backend/app/components/notifications/templates.py`) | "What to expect" panel with an **"AI-assisted — Use the built-in chat to debug & reason out loud"** card; plain-text mirror says "AI-assisted: use the built-in chat…" | Yes — "AI-assisted" stated explicitly | **OK** | None |
| **Assessment nudge email** — `assessment_nudge_html` (same file) | "…you **pair with Claude** the whole way" / "you **work with Claude** on a real task" | Yes — names Claude as the thing you pair with; interaction already disclosed at invite | **OK** | None |
| **Assessment expiry reminder** — `assessment_expiry_reminder_html` (same file) | "Your assessment link expires… please complete your assessment before then." | N/A — a reminder to finish; not itself an AI interaction, and AI was disclosed at invite/welcome | **OK** | None |
| **Candidate welcome page** — `CandidateWelcomePage.jsx` (`frontend/src/features/assessment_runtime/`) | Hero: "the same repo, runtime, and **AI tooling**…"; bullets name **Claude**; "What to expect" panel "Repo, editor, and **Claude**"; System check row "**Claude access — Ready**"; "Your rights" panel "We record your prompts, **Claude** responses…" | Partial → now yes. Named Claude and said "AI tooling" once, but never plainly bound "Claude = an AI assistant" | **OK (strengthened)** | Added two-word apposition to the first Claude bullet: "…with **Claude, an AI assistant**, and the live repo." |
| **Assessment runtime — Claude chat** — `AssessmentClaudeChat.jsx` (same dir) | Every assistant message bubble is labelled **"Claude"** (vs "You"); empty state "**Claude is ready** — Ask Claude to inspect the repo…"; input placeholder "Ask Claude…" | Yes — persistent per-message AI attribution the candidate reads throughout the session | **OK** | None |
| **Assessment submitted / status screen** — `AssessmentStatusScreen.jsx` (same dir) | "Task submitted" confirmation. No AI-generated score, summary, or feedback shown to the candidate. | N/A — no AI output surfaced to the candidate | **OK** | None |
| **Public apply / careers** — `PublicJobPage.jsx`, `ApplyForm.jsx`, `CareersPage.jsx` (`frontend/src/features/jobpage/`) | Job description + recruiter-authored screening questions in a plain form. Scoring runs server-side after submit and is recruiter-facing. | N/A — candidate interacts with a form, not an AI; no AI output returned to them here | **OK** | None |
| **Outreach thanks page** — `OutreachThanksPage.jsx` (`frontend/src/features/outreach/`) | Static "thanks / interest recorded" landing after a campaign CTA. | N/A — no AI content or interaction | **OK** | None |

## Gaps found and fixes applied

- **One gap, one fix.** The candidate welcome page disclosed AI involvement (named Claude in five places,
  said "AI tooling" once) but did not, in one plain place, state that Claude *is* an AI assistant. Applied
  the smallest in-voice fix — a two-word apposition on the first Claude mention in
  `CandidateWelcomePage.jsx`:

  > "Work normally inside the workspace with **Claude, an AI assistant**, and the live repo…"

  No layout, component, or style change; light theme and purple tokens untouched.

## Recommendations (not built)

- **No code change needed elsewhere.** All other candidate-facing surfaces already disclose AI or surface
  no AI output to the candidate.
- **Watch item:** if Taali ever starts sending applicants their own AI-generated scores/summaries (e.g. a
  candidate-mode share link), that new surface must carry an "AI-assisted" label — it would be a new Art 50
  interaction/AI-content disclosure point. Not in scope today because no such surface exists.
- **Process:** add "candidate-facing AI surfaces carry an AI-interaction label" to the release-review
  checklist alongside the R12 emotion-inference item.
