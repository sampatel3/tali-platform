import React, { useState } from 'react';
import { ArrowRight, Check, ChevronDown, ChevronRight, RefreshCw, X } from 'lucide-react';

import { Button, Card } from '../../../shared/ui/TaaliPrimitives';
import { AgentFlowButton } from '../../../shared/motion';
import {
  buildRejectConsequenceCopy,
  isRejectDecisionType,
} from '../../../shared/decisions/decisionActions';

const DECISION_LABEL = {
  advance_to_interview: 'Advance to technical interview',
  reject: 'Reject candidate',
  skip_assessment_reject: 'Reject without sending assessment',
  send_assessment: 'Send assessment',
  resend_assessment_invite: 'Resend assessment invite',
  escalate_low_confidence: 'Escalate — low confidence',
};

const formatConfidence = (value) => {
  if (value === null || value === undefined) return null;
  const pct = Math.round(Number(value) * 100);
  return `${pct}% confident`;
};

// Purple-tone confidence bands — no red/amber per house style. Low
// confidence is muted, not alarming; the recruiter can still spot it.
const CONFIDENCE_BAND_CLASS = {
  high: 'bg-taali-accent/20 text-taali-accent',
  medium: 'bg-taali-accent/10 text-taali-accent',
  low: 'bg-taali-bg-muted text-taali-fg-muted',
};

// Human labels for the backend staleness reason codes (mirror of
// decision_staleness._REASON_LABELS). ``engine_outdated`` is the "old model"
// dimension: advisory, not blocking — see the Approve gate below.
const STALENESS_LABELS = {
  criteria_changed: 'role criteria edited',
  cv_replaced: 'new CV uploaded',
  pre_screen_score_shifted: 'pre-screen score changed',
  assessment_score_shifted: 'assessment score changed',
  cutoff_changed: 'cutoff changed',
  recruiter_note_added: 'recruiter note added',
  engine_outdated: 'scored by an older model',
};

// Relative-age label from the backend-computed age_seconds. Keeps the
// "how old is this decision" signal visible without a date library.
const formatAge = (seconds) => {
  const s = Number(seconds) || 0;
  if (s < 60) return 'Queued just now';
  const mins = Math.floor(s / 60);
  if (mins < 60) return `Queued ${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `Queued ${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `Queued ${days}d ago`;
};

const formatCost = (cents) => {
  const c = Number(cents) || 0;
  if (c <= 0) return null;
  return `$${(c / 100).toFixed(2)}`;
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

export const AgentDecisionCard = ({ decision, onApprove, onOverride, onReEvaluate, busy = false }) => {
  const [expanded, setExpanded] = useState(false);
  const decisionLabel = DECISION_LABEL[decision.decision_type] || decision.decision_type;
  const confidenceLabel = formatConfidence(decision.confidence);
  const candidateLabel = decision.candidate_name || decision.candidate_email || `Application #${decision.application_id}`;
  const rejectConsequence = isRejectDecisionType(decision.decision_type)
    ? buildRejectConsequenceCopy(decision.role_family)
    : null;

  const isStale = Boolean(decision.is_stale);
  const stalenessSummary = decision.staleness_summary;
  const stalenessReasons = Array.isArray(decision.staleness_reasons) ? decision.staleness_reasons : [];
  // "Old model" is advisory — flag it and offer Re-evaluate, but never block
  // the recruiter's Approve on it (the score is superseded, not wrong). A
  // genuine INPUT change (criteria/CV/cutoff/note/score drift) still blocks.
  const staleEngineOnly = stalenessReasons.length > 0 && stalenessReasons.every((r) => r === 'engine_outdated');
  const stalenessBlocking = stalenessReasons.some((r) => r !== 'engine_outdated');
  const bandClass = CONFIDENCE_BAND_CLASS[decision.confidence_band] || 'bg-taali-bg-muted text-taali-fg-muted';
  const ageLabel = formatAge(decision.age_seconds);
  const costLabel = formatCost(decision.cost_usd_cents);

  return (
    <Card className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">{candidateLabel}</span>
            <ArrowRight size={14} className="text-taali-fg-muted" aria-hidden />
            <span className="font-medium text-taali-accent">{decisionLabel}</span>
            {confidenceLabel ? (
              <span className={`rounded px-2 py-0.5 text-[0.6875rem] ${bandClass}`}>
                {confidenceLabel}
              </span>
            ) : null}
            <span className="text-[0.6875rem] text-taali-fg-muted">{ageLabel}</span>
          </div>

          {isStale ? (
            <div className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-taali-accent/10 px-2 py-1 text-[0.6875rem] font-medium text-taali-accent">
              <RefreshCw size={12} aria-hidden />
              <span>
                {staleEngineOnly
                  ? stalenessSummary || 'Scored by an older model'
                  : `Inputs changed${stalenessSummary ? ` · ${stalenessSummary}` : ''}`}
              </span>
            </div>
          ) : null}

          <p className="mt-1 text-sm text-taali-fg">{decision.reasoning}</p>

          {rejectConsequence ? (
            <div
              className="mt-2 rounded-md border border-taali-border bg-taali-bg-muted/30 px-3 py-2 text-xs text-taali-fg"
              role="alert"
            >
              <strong>Shared candidate pool —</strong> {rejectConsequence}
            </div>
          ) : null}

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
              {isStale && stalenessReasons.length ? (
                <div className="mt-2 text-[0.6875rem] text-taali-accent">
                  Stale because: {stalenessReasons.map((r) => STALENESS_LABELS[r] || r).join(', ')}
                </div>
              ) : null}
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[0.6875rem] text-taali-fg-muted">
                <span>model: {decision.model_version}</span>
                <span>prompt: {decision.prompt_version}</span>
                {decision.agent_run_id ? <span>run #{decision.agent_run_id}</span> : null}
                {costLabel ? <span>cost: {costLabel}</span> : null}
                <span>queued: {new Date(decision.created_at).toLocaleString()}</span>
              </div>
            </div>
          ) : null}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="flex gap-2">
            <Button
              as={AgentFlowButton}
              variant="primary"
              size="xs"
              onClick={onApprove}
              disabled={busy || stalenessBlocking}
              title={
                stalenessBlocking
                  ? 'Inputs changed since this decision — re-evaluate before approving'
                  : staleEngineOnly
                    ? 'Scored by an older model — re-evaluate to re-score, or approve as-is'
                    : rejectConsequence || undefined
              }
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
          {isStale && onReEvaluate ? (
            <Button
              variant="ghost"
              size="xs"
              onClick={onReEvaluate}
              disabled={busy}
              aria-label={`Re-evaluate agent recommendation for ${candidateLabel}`}
            >
              <RefreshCw size={14} aria-hidden /> Re-evaluate
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
};

export default AgentDecisionCard;
