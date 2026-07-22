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
        body: 'This rejects the candidate for this role. Cannot be undone from this screen.',
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
      body: 'Pick the Workable stage to move them into. A concise movement summary is posted to the connected ATS.',
      confirmLabel: 'Advance',
      confirmClass: 'rq-approve',
      placeholder: 'Optional note for the audit trail',
      requireStagePick: true,
    },
    alternatives: [
      {
        action: 'send_assessment',
        label: 'Send assessment',
        icon: Send,
        kicker: 'OVERRIDE TO SEND',
        headline: 'Send an assessment to {name} instead of advancing?',
        body: "Dispatches the assessment invite instead of moving them forward — works even when this role skips assessments. The agent will recalibrate based on your reason.",
        confirmLabel: 'Send assessment',
        confirmClass: 'rq-approve',
        placeholder: 'e.g. Want to verify hands-on skill before the interview',
      },
      {
        action: 'reject',
        label: 'Reject',
        icon: X,
        kicker: 'REJECT CANDIDATE',
        headline: 'Reject {name}?',
        body: 'This rejects the candidate for this role.',
        confirmLabel: 'Reject',
        confirmClass: 'rq-override',
      },
    ],
  },
  reject: {
    // Primary = confirm the agent's reject (fires immediately). Labeled with
    // the action verb so the "Agent recommends" slab reads as the actual
    // recommendation ("Agent recommends: Reject"), not a generic "Approve"
    // that looked like advancing the candidate.
    primaryLabel: 'Reject',
    primaryIcon: X,
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
    // Primary = confirm the agent's pre-screen reject — the action verb under
    // the "Agent recommends" slab.
    primaryLabel: 'Reject',
    primaryIcon: X,
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
        body: 'This rejects the candidate for this role.',
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

// The two reject decision types (agent-recommended reject + pre-screen reject).
export const isRejectDecisionType = (decisionType) =>
  decisionType === 'reject' || decisionType === 'skip_assessment_reject';

// Consequence surfaced beside every one-click reject — the candidate report
// rail AND the Home hub card — so a recruiter always sees what confirming does.
// Single source so the surfaces never drift. Deliberately says nothing about a
// candidate email: Taali never emails candidates about the job (see backend
// actions/reject_application.py); any candidate-facing message is the ATS's own
// disqualify workflow, so we don't claim one on Taali's behalf.
export const REJECT_CONSEQUENCE_COPY =
  'Rejects this candidate for this role. Other roles keep their own candidate status.';

const normaliseRoleReference = (reference, relationship) => {
  const name = String(reference?.name || '').trim();
  const id = String(reference?.id ?? '').trim().replace(/^#/, '');
  if (!name || !id) return null;
  return {
    key: id,
    label: `${name} #${id} (${relationship})`,
  };
};

const joinRoleReferences = (labels) => {
  if (labels.length === 1) return labels[0];
  if (labels.length === 2) return `${labels[0]} and ${labels[1]}`;
  return `${labels.slice(0, -1).join(', ')}, and ${labels.at(-1)}`;
};

/**
 * Format a complete linked-role family without ever dropping a role name or
 * reference. Incomplete metadata deliberately returns null so callers keep a
 * conditional, ATS-safe warning instead of presenting a partial family as
 * though it were exhaustive (or claiming every standalone role is linked).
 */
export const formatRoleFamilyReferences = (roleFamily) => {
  const owner = normaliseRoleReference(roleFamily?.owner, 'original');
  const relatedInput = Array.isArray(roleFamily?.related) ? roleFamily.related : [];
  const related = relatedInput.map((role) => normaliseRoleReference(role, 'related'));
  if (!owner || related.length === 0 || related.some((role) => role == null)) return null;

  const seen = new Set([owner.key]);
  const roles = [owner];
  related.forEach((role) => {
    if (seen.has(role.key)) return;
    seen.add(role.key);
    roles.push(role);
  });
  if (roles.length < 2) return null;
  return joinRoleReferences(roles.map((role) => role.label));
};

const roleReferenceForId = (roleFamily, roleId) => {
  const owner = roleFamily?.owner;
  const related = Array.isArray(roleFamily?.related) ? roleFamily.related : [];
  const candidates = [owner, ...related].filter(Boolean);
  return candidates.find((reference) => (
    reference?.id != null
    && roleId != null
    && String(reference.id) === String(roleId)
  )) || null;
};

/**
 * Explain the effect on the logical role that owns the decision. Related-role
 * membership, pipeline state, and outcomes are independent; the owner ATS
 * application is only a transport/write-back boundary.
 */
export const buildRejectConsequenceCopy = (roleFamily, roleId) => {
  const owner = normaliseRoleReference(roleFamily?.owner, 'original');
  const currentReference = roleReferenceForId(roleFamily, roleId);
  const current = normaliseRoleReference(
    currentReference,
    owner && currentReference && String(currentReference.id) === String(roleFamily?.owner?.id)
      ? 'original'
      : 'related',
  );

  if (current && owner && current.key !== owner.key) {
    return `Rejects this candidate only for ${current.label}. The linked ATS application and other roles are unchanged.`;
  }
  if (current && owner && current.key === owner.key) {
    return `Rejects this candidate for ${current.label} and writes the rejection to its ATS application. Related roles keep their own candidate status.`;
  }
  return REJECT_CONSEQUENCE_COPY;
};

/** Resolve the reject modal copy at click-time while preserving its undo note. */
export const withRoleAwareRejectCopy = (alternative, roleFamily, roleId) => {
  if (alternative?.action !== 'reject') return alternative;
  const undoCopy = /cannot be undone from this screen/i.test(alternative?.body || '')
    ? ' Cannot be undone from this screen.'
    : '';
  return {
    ...alternative,
    body: `${buildRejectConsequenceCopy(roleFamily, roleId)}${undoCopy}`,
  };
};
