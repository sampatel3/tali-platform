import React, { useState } from 'react';
import { ArrowRight, Check, ChevronDown, ChevronRight, X } from 'lucide-react';

import { Button, Card } from '../../../shared/ui/TaaliPrimitives';

const DECISION_LABEL = {
  advance_to_interview: 'Advance to technical interview',
  reject: 'Reject candidate',
  skip_assessment_reject: 'Reject without sending assessment',
};

// Streamlined chips for the reject reasons the policy stamps onto
// evidence. Keys must match the engine's ``reject_reason`` values.
const REJECT_REASON_CHIPS = {
  pre_screen_below_threshold: {
    label: 'Pre-screen reject',
    title: 'Pre-screen score is below the role’s reject threshold — quick reject candidate.',
  },
  role_fit_low: {
    label: 'Role-fit reject',
    title: 'CV match is far below the send-assessment floor.',
  },
  must_have_blocked: {
    label: 'Must-have failure',
    title: 'Candidate fails a must-have requirement.',
  },
};

const formatConfidence = (value) => {
  if (value === null || value === undefined) return null;
  const pct = Math.round(Number(value) * 100);
  return `${pct}% confident`;
};

const rejectReasonChip = (evidence) => {
  if (!evidence || typeof evidence !== 'object') return null;
  const reason = typeof evidence.reject_reason === 'string'
    ? evidence.reject_reason.trim()
    : '';
  if (!reason) return null;
  return REJECT_REASON_CHIPS[reason] || { label: reason, title: reason };
};

const renderEvidence = (evidence) => {
  if (!evidence || typeof evidence !== 'object') return null;
  const entries = Object.entries(evidence);
  if (entries.length === 0) return null;
  return (
    <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      {entries.map(([key, value]) => (
        <React.Fragment key={key}>
          <dt className="font-medium text-taali-fg-muted">{key}</dt>
          <dd className="break-words text-taali-fg">
            {typeof value === 'object' ? JSON.stringify(value) : String(value)}
          </dd>
        </React.Fragment>
      ))}
    </dl>
  );
};

export const AgentDecisionCard = ({ decision, onApprove, onOverride, busy = false }) => {
  const [expanded, setExpanded] = useState(false);
  const decisionLabel = DECISION_LABEL[decision.decision_type] || decision.decision_type;
  const confidenceLabel = formatConfidence(decision.confidence);
  const rejectChip = rejectReasonChip(decision.evidence);
  const candidateLabel = decision.candidate_name || decision.candidate_email || `Application #${decision.application_id}`;

  return (
    <Card className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">{candidateLabel}</span>
            <ArrowRight size={14} className="text-taali-fg-muted" aria-hidden />
            <span className="font-medium text-taali-accent">{decisionLabel}</span>
            {rejectChip ? (
              <span
                className="rounded bg-[var(--purple-tint)] px-2 py-0.5 text-[11px] font-medium text-[var(--purple)]"
                title={rejectChip.title}
              >
                {rejectChip.label}
              </span>
            ) : null}
            {confidenceLabel ? (
              <span className="rounded bg-taali-bg-muted px-2 py-0.5 text-[11px] text-taali-fg-muted">
                {confidenceLabel}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-taali-fg">{decision.reasoning}</p>

          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="mt-2 inline-flex items-center gap-1 text-xs text-taali-fg-muted hover:text-taali-fg"
            aria-expanded={expanded}
          >
            {expanded ? (
              <ChevronDown size={14} aria-hidden />
            ) : (
              <ChevronRight size={14} aria-hidden />
            )}
            Evidence + metadata
          </button>

          {expanded ? (
            <div className="mt-1 rounded-md border border-taali-border bg-taali-bg-muted/30 px-3 py-2">
              {renderEvidence(decision.evidence) || (
                <p className="text-xs text-taali-fg-muted">No structured evidence cited.</p>
              )}
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-taali-fg-muted">
                <span>model: {decision.model_version}</span>
                <span>prompt: {decision.prompt_version}</span>
                {decision.agent_run_id ? <span>run #{decision.agent_run_id}</span> : null}
                <span>queued: {new Date(decision.created_at).toLocaleString()}</span>
              </div>
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 gap-2">
          <Button
            variant="primary"
            size="xs"
            onClick={onApprove}
            disabled={busy}
            aria-label={`Approve agent recommendation for ${candidateLabel}`}
          >
            <Check size={14} aria-hidden /> Approve
          </Button>
          <Button
            variant="ghost"
            size="xs"
            onClick={onOverride}
            disabled={busy}
            aria-label={`Override agent recommendation for ${candidateLabel}`}
          >
            <X size={14} aria-hidden /> Override
          </Button>
        </div>
      </div>
    </Card>
  );
};

export default AgentDecisionCard;
