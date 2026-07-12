import React, { useState } from 'react';

import { Badge, Button, Panel, Textarea } from '../../shared/ui/TaaliPrimitives';

// The recruiter "Record your decision" card, extracted so it can drive both the
// assessment-backed Evaluate tab and the application-level decision surface for
// candidates with no assessment linked. Presentational: the owner holds the
// form state + persistence and passes the lifecycle snapshot in.

export const DECISION_OPTIONS = [
  { value: 'advance', label: 'Advance', description: 'Send to panel' },
  { value: 'hold', label: 'Hold', description: 'Keep in pool' },
  { value: 'reject', label: 'Reject', description: 'Send rejection' },
];

export const CONFIDENCE_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

export const NEXT_STEP_OPTIONS = [
  'Schedule panel',
  'Request references',
  'Add to talent pool',
  'Notify hiring manager',
];

const STATUS_BADGE = {
  submitted: { label: 'Recorded', variant: 'purple' },
  draft: { label: 'Draft', variant: 'warning' },
};

const HISTORY_ACTION_LABEL = {
  saved_draft: 'Saved draft',
  submitted: 'Recorded decision',
  updated: 'Updated decision',
};

const DECISION_LABEL = { advance: 'Advance', hold: 'Hold', reject: 'Reject' };

const decisionLabel = (value) => DECISION_LABEL[value] || (value ? String(value) : '—');

const formatWhen = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
};

export const DecisionRecorder = ({
  kicker = 'Your evaluation',
  title = 'Record your decision.',
  intro = 'This recruiter evaluation stays attached to the candidate report and becomes the internal source of truth.',
  entityNoun = 'evaluation',
  decision,
  onDecisionChange,
  rationale,
  onRationaleChange,
  confidence,
  onConfidenceChange,
  nextSteps = [],
  onToggleNextStep,
  // Persisted lifecycle snapshot: { status, version, updatedBy, updatedAt, submittedAt, history }
  persisted = null,
  dirty = false,
  saving = false,
  savingMode = null, // 'draft' | 'submit' | null
  conflict = false,
  onReload,
  onSaveDraft,
  onSubmit,
  disabled = false,
  className = '',
}) => {
  const [historyOpen, setHistoryOpen] = useState(false);
  const persistedStatus = persisted?.status || '';
  const isRecorded = persistedStatus === 'submitted';
  const statusBadge = STATUS_BADGE[persistedStatus] || { label: 'Not recorded', variant: 'muted' };
  const updatedByName = persisted?.updatedBy?.name || '';
  const updatedAtLabel = formatWhen(persisted?.updatedAt);
  const history = Array.isArray(persisted?.history) ? persisted.history : [];

  const primaryLabel = isRecorded ? `Update ${entityNoun}` : `Submit ${entityNoun}`;
  // Saving a draft only makes sense when there's something unsaved. Submitting
  // is always available until an unchanged decision is already recorded (so a
  // clean draft can still be promoted to recorded).
  const draftDisabled = disabled || saving || !dirty;
  const submitDisabled = disabled || saving || (isRecorded && !dirty);

  return (
    <Panel className={`bg-[var(--taali-surface-muted)] p-5 ${className}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{kicker}</div>
          <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">{title}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge variant={statusBadge.variant} className="font-mono text-[0.625rem] uppercase tracking-[0.1em]">
            {statusBadge.label}
          </Badge>
          {persisted?.version ? (
            <span className="font-mono text-[0.625rem] text-[var(--taali-muted)]">v{persisted.version}</span>
          ) : null}
        </div>
      </div>

      <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">{intro}</p>

      {(updatedByName || updatedAtLabel) ? (
        <div className="mt-3 text-xs text-[var(--taali-muted)]">
          {isRecorded ? 'Recorded' : 'Last saved'}
          {updatedByName ? (
            <>
              {' '}by <span className="font-semibold text-[var(--taali-text)]">{updatedByName}</span>
            </>
          ) : null}
          {updatedAtLabel ? <> · {updatedAtLabel}</> : null}
          {dirty ? <span className="ml-2 font-semibold text-[var(--taali-warning)]">Unsaved changes</span> : null}
        </div>
      ) : null}

      {conflict ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-[var(--taali-radius-card)] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] px-3 py-2 text-xs text-[var(--taali-text)]">
          <span>This {entityNoun} was updated elsewhere. Reload to see the latest before saving again.</span>
          {onReload ? (
            <Button type="button" variant="secondary" size="sm" onClick={onReload}>
              Reload
            </Button>
          ) : null}
        </div>
      ) : null}

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        {DECISION_OPTIONS.map((option) => {
          const active = decision === option.value;
          return (
            <button
              key={option.value}
              type="button"
              className={`rounded-[var(--taali-radius-card)] border px-4 py-4 text-left transition ${
                active
                  ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)] text-[var(--taali-purple)]'
                  : 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]'
              }`}
              onClick={() => onDecisionChange(option.value)}
            >
              <div className="font-mono text-[0.625rem] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
                {active ? 'Selected' : 'Decision'}
              </div>
              <div className="mt-2 text-lg font-semibold">{option.label}</div>
              <div className="mt-1 text-xs text-[var(--taali-muted)]">{option.description}</div>
            </button>
          );
        })}
      </div>

      <div className="mt-5">
        <label className="mb-2 block font-mono text-[0.65625rem] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
          Your rationale
        </label>
        <Textarea
          className="min-h-[7.5rem] text-sm"
          placeholder="Why are you advancing, holding, or rejecting this candidate?"
          value={rationale}
          onChange={(event) => onRationaleChange(event.target.value)}
        />
      </div>

      <div className="mt-5">
        <div className="mb-2 font-mono text-[0.65625rem] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
          Confidence
        </div>
        <div className="flex flex-wrap gap-2">
          {CONFIDENCE_OPTIONS.map((option) => {
            const active = confidence === option.value;
            return (
              <button
                key={option.value}
                type="button"
                className={`rounded-full border px-3 py-2 text-sm transition ${
                  active
                    ? 'border-[var(--taali-purple)] bg-[var(--taali-purple)] text-[var(--taali-inverse-text)]'
                    : 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]'
                }`}
                onClick={() => onConfidenceChange(option.value)}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mt-5">
        <div className="mb-2 font-mono text-[0.65625rem] uppercase tracking-[0.1em] text-[var(--taali-muted)]">
          Next steps
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          {NEXT_STEP_OPTIONS.map((option) => {
            const active = Array.isArray(nextSteps) && nextSteps.includes(option);
            return (
              <label
                key={option}
                className={`flex cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-sm transition ${
                  active
                    ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)] text-[var(--taali-purple)]'
                    : 'border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-text)]'
                }`}
              >
                <input
                  type="checkbox"
                  checked={active}
                  onChange={() => onToggleNextStep(option)}
                  className="h-4 w-4"
                />
                <span>{option}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
        {history.length ? (
          <button
            type="button"
            className="taali-text-btn"
            onClick={() => setHistoryOpen((open) => !open)}
          >
            {historyOpen ? 'Hide history' : `History (${history.length})`}
          </button>
        ) : (
          <span />
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onSaveDraft} disabled={draftDisabled}>
            {savingMode === 'draft' ? 'Saving...' : 'Save draft'}
          </Button>
          <Button type="button" variant="primary" onClick={onSubmit} disabled={submitDisabled}>
            {savingMode === 'submit' ? 'Saving...' : primaryLabel}
          </Button>
        </div>
      </div>

      {historyOpen && history.length ? (
        <div className="mt-3 space-y-2 border-t border-[var(--taali-border)] pt-3" data-testid="decision-history">
          {[...history].reverse().map((entry, index) => (
            <div key={`${entry.version}-${index}`} className="text-xs text-[var(--taali-muted)]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-[var(--taali-text)]">
                  {HISTORY_ACTION_LABEL[entry.action] || 'Saved'}
                </span>
                <span className="font-mono text-[0.625rem]">v{entry.version}</span>
                <span>·</span>
                <span>
                  {decisionLabel(entry.decision)}
                  {entry.confidence ? ` · ${entry.confidence} confidence` : ''}
                </span>
              </div>
              <div className="mt-0.5">
                {entry.by?.name ? <span className="text-[var(--taali-text)]">{entry.by.name}</span> : 'Unknown'}
                {formatWhen(entry.at) ? ` · ${formatWhen(entry.at)}` : ''}
                {entry.rationale_excerpt ? ` — ${entry.rationale_excerpt}` : ''}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </Panel>
  );
};
