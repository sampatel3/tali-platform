// Type-aware action set for an agent decision. Each decision_type maps to:
//   - primary: the agent's recommendation, fires immediately on click
//     (no confirmation modal).
//   - alternatives: destructive alternative actions the recruiter can
//     pick. Each opens OverrideModal with required "why" textarea, then
//     dispatches via /agent-decisions/{id}/override with the action id.
//
// Plus the two universals — Send back & teach (TeachModal) and Snooze
// 1h (immediate, no modal) — rendered after the type-specific buttons.
//
// Shared so both the Home review queue (HomeNow) and the reusable
// <AgentDecisionCard> render the exact same action vocabulary. Moved here
// out of HomeNow.jsx unchanged — HomeNow imports it for handleApprove, the
// card imports it for the action bar.
import { ArrowRight, Check, Repeat, Send, X } from 'lucide-react';

export const DECISION_ACTIONS = {
  send_assessment: {
    primaryLabel: 'Send assessment',
    primaryIcon: Send,
    alternatives: [
      {
        action: 'reject',
        label: 'Reject',
        icon: X,
        kicker: 'REJECT CANDIDATE',
        headline: 'Reject {name}?',
        body: 'This will disqualify them in Workable and send the rejection email. Cannot be undone from this screen.',
        confirmLabel: 'Reject',
        confirmClass: 'rq-override',
        placeholder: 'e.g. Missing AWS Glue experience confirmed by the recruiter screen',
      },
      {
        action: 'skip_assessment_advance',
        label: 'Skip & advance',
        icon: ArrowRight,
        kicker: 'SKIP ASSESSMENT',
        headline: 'Skip the assessment and move {name} to the advance queue?',
        body: "Skips the assessment email and queues them as an advance. You'll pick the Workable stage when you approve the advance from the queue — nothing posts to Workable yet.",
        confirmLabel: 'Move to advance queue',
        confirmClass: 'rq-approve',
        placeholder: 'e.g. Internal referral — pre-vetted, no need for an assessment',
      },
    ],
  },
  advance_to_interview: {
    primaryLabel: 'Advance to next stage',
    primaryIcon: ArrowRight,
    // The primary "Advance" no longer fires immediately — it opens the
    // shared OverrideModal in ``approve`` mode so the recruiter picks the
    // target Workable stage (and can add an optional note). This matches
    // the candidate-drawer flow on the Jobs page.
    primary: {
      mode: 'approve',
      kicker: 'ADVANCE',
      headline: 'Advance {name} to the next stage?',
      body: 'Pick the Workable stage to move them into. A short summary + 30-day report link is posted to Workable.',
      confirmLabel: 'Advance',
      confirmClass: 'rq-approve',
      placeholder: 'Optional note for the audit trail',
      requireStagePick: true,
    },
    alternatives: [
      {
        action: 'reject',
        label: 'Reject',
        icon: X,
        kicker: 'REJECT CANDIDATE',
        headline: 'Reject {name}?',
        body: 'This will disqualify them in Workable and send the rejection email.',
        confirmLabel: 'Reject',
        confirmClass: 'rq-override',
      },
    ],
  },
  reject: {
    // Primary = approve the agent's reject (fires immediately). Labeled
    // "Approve" to match the bulk action and avoid colliding with the
    // REJECT type badge — the recruiter is approving a decision, not
    // independently rejecting. Outcome is conveyed by the badge + body.
    primaryLabel: 'Approve',
    alternatives: [
      {
        action: 'send_assessment',
        label: 'Send assessment',
        icon: Send,
        kicker: 'OVERRIDE TO SEND',
        headline: 'Send the assessment to {name} instead?',
        body: "Dispatches the assessment invite. The agent will recalibrate based on your reason.",
        confirmLabel: 'Send assessment',
        confirmClass: 'rq-approve',
      },
      {
        action: 'advance',
        label: 'Advance instead',
        icon: ArrowRight,
        kicker: 'OVERRIDE TO ADVANCE',
        headline: 'Advance {name} instead?',
        body: "Pick the Workable stage to move them into. Skips the rejection email.",
        confirmLabel: 'Advance',
        confirmClass: 'rq-approve',
        requireStagePick: true,
      },
    ],
  },
  skip_assessment_reject: {
    // No inline overrides for pre-screen reject. The agent has flagged
    // the CV as not worth assessing (often fraud / hard-constraint
    // failures like salary mismatch caught from Workable answers); a
    // one-click "Send assessment anyway" trains recruiters to ignore
    // the cost-protection signal and drains assessment credits on
    // candidates that shouldn't be tested. If the recruiter disagrees,
    // the right path is ``Send back & teach`` — that produces a
    // learning signal and re-runs the agent with the new context. The
    // universals (teach + snooze) are appended by the renderer.
    // Primary = approve the agent's reject; labeled "Approve" to match the
    // bulk action and the REJECT (PRE-SCREEN) badge carries the outcome.
    primaryLabel: 'Approve',
    alternatives: [],
  },
  resend_assessment_invite: {
    primaryLabel: 'Resend invite',
    primaryIcon: Repeat,
    alternatives: [
      {
        action: 'reject',
        label: 'Reject',
        icon: X,
        kicker: 'REJECT CANDIDATE',
        headline: 'Reject {name}?',
        body: 'This will disqualify them in Workable and send the rejection email.',
        confirmLabel: 'Reject',
        confirmClass: 'rq-override',
      },
      {
        action: 'skip_assessment_advance',
        label: 'Skip & advance',
        icon: ArrowRight,
        kicker: 'SKIP ASSESSMENT',
        headline: 'Skip the assessment and move {name} to the advance queue?',
        body: "Skips resending the invite and queues them as an advance. You'll pick the Workable stage when you approve the advance from the queue — nothing posts to Workable yet.",
        confirmLabel: 'Move to advance queue',
        confirmClass: 'rq-approve',
      },
    ],
  },
};

// Fallback for any decision_type not mapped above (e.g. legacy or
// escalate_low_confidence). Single generic Approve + the universals.
export const DEFAULT_ACTIONS = {
  primaryLabel: 'Approve',
  primaryIcon: Check,
  alternatives: [],
};
