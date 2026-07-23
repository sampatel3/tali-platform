import React from 'react';
import { ShieldAlert } from 'lucide-react';

import { Panel } from '../../shared/ui/TaaliPrimitives';

const formatTime = (iso) => {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

/**
 * Assessment integrity metrics, as a designed panel.
 *
 * These deter and detect cheating: the workspace is closed, so an external
 * model cannot be handed the repo — but a screenshot and a question can be, and
 * tab focus is the only signal that catches it. They are candidate-controlled
 * browser signals, so the panel states that rather than presenting counts as
 * findings.
 */
export const CandidateIntegritySignalsPanel = ({ summary }) => {
  if (!summary?.hasData) {
    return (
      <Panel className="p-3.5" data-testid="assessment-integrity-panel">
        <div className="mb-1 font-bold">Assessment integrity</div>
        <div className="text-xs text-[var(--taali-muted)]">
          No workspace activity metrics were logged for this assessment.
        </div>
      </Panel>
    );
  }

  return (
    <Panel className="p-3.5" data-testid="assessment-integrity-panel">
      <div className="mb-1 flex items-center gap-2 font-bold">
        <ShieldAlert size={16} className="text-[var(--taali-purple)]" />
        Assessment integrity
      </div>
      <div className="mb-3 text-xs text-[var(--taali-muted)]">
        Activity metrics from the candidate&rsquo;s assessment tab. Candidate-controlled
        browser signals &mdash; context for reviewing the work, not proof of anything on
        their own, and not an input to the score.
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {summary.groups.map((group) => (
          <div
            key={group.label}
            className="rounded-[var(--taali-radius-sm,10px)] bg-[var(--taali-surface-2,rgba(0,0,0,0.03))] px-3 py-2"
            data-testid={`integrity-group-${group.label.replace(/\s+/g, '-')}`}
          >
            <div className="text-lg font-bold text-[var(--taali-text)]">{group.count}</div>
            <div className="text-xs capitalize text-[var(--taali-muted)]">{group.label}</div>
          </div>
        ))}
      </div>

      {summary.tabFocusTimestamps.length > 0 ? (
        <div className="mt-3">
          <div className="text-xs font-medium text-[var(--taali-text)]">
            Tab lost focus at
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5" data-testid="integrity-tab-focus-times">
            {summary.tabFocusTimestamps.slice(0, 12).map((iso, index) => {
              const label = formatTime(iso);
              return label ? (
                <span
                  key={`${iso}-${index}`}
                  className="rounded-full bg-[var(--taali-surface-2,rgba(0,0,0,0.05))] px-2 py-0.5 font-mono text-[0.6875rem] text-[var(--taali-muted)]"
                >
                  {label}
                </span>
              ) : null;
            })}
            {summary.tabFocusTimestamps.length > 12 ? (
              <span className="px-1 py-0.5 text-[0.6875rem] text-[var(--taali-muted)]">
                +{summary.tabFocusTimestamps.length - 12} more
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </Panel>
  );
};

export default CandidateIntegritySignalsPanel;
