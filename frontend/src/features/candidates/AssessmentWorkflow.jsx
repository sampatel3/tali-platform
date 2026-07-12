import React from 'react';
import { AgentLoop } from '../../shared/motion';

// The five-step assessment lifecycle, in order. The stepper and the funnel
// strip both read from this so they stay in lockstep.
const WF_STEPS = [
  { key: 'sent', label: 'Sent' },
  { key: 'delivered', label: 'Delivered' },
  { key: 'opened', label: 'Opened' },
  { key: 'started', label: 'Started' },
  { key: 'completed', label: 'Completed' },
];

const _fmtTs = (ts) => {
  if (!ts) return '';
  try { return new Date(ts).toLocaleString(); } catch { return ''; }
};

const _dotMod = { D: 'done', C: 'current', T: 'todo', W: 'warn', E: 'err' };

/**
 * Derive where a candidate sits in the assessment lifecycle from the assessment
 * status + the Resend delivery tracking, returning everything the UI needs:
 * per-step states for the stepper, a one-line human summary, a tone, and the
 * recommended next action. Single source of truth for both the row stepper and
 * the funnel summary.
 *
 * Step state codes: D done · C current · T to-do · W expired · E failed.
 */
export function deriveAssessmentWorkflow(status, tracking) {
  const s = (status || '').toLowerCase();
  const t = tracking || {};
  const es = (t.email_status || '').toLowerCase();

  const build = (codes, label, tone, action = null, live = false) => ({
    steps: WF_STEPS.map((step, i) => ({ ...step, state: _dotMod[codes[i]] })),
    codes,
    label,
    tone,
    action,
    live,
    title: [
      t.invite_sent_at && `Sent: ${_fmtTs(t.invite_sent_at)}`,
      t.delivered_at && `Delivered: ${_fmtTs(t.delivered_at)}`,
      t.opened_at && `Opened: ${_fmtTs(t.opened_at)}`,
      t.bounced_at && `Bounced: ${_fmtTs(t.bounced_at)}`,
      t.started_at && `Started: ${_fmtTs(t.started_at)}`,
      t.expires_at && `Expires: ${_fmtTs(t.expires_at)}`,
    ].filter(Boolean).join('\n') || 'Assessment invite',
  });

  // Failure / terminal states first — they override the happy-path progression.
  if (es === 'failed') return build('ETTTT', 'Never sent — provider error', 'err', 'resend');
  if (es === 'bounced' || es === 'complained') {
    return build('DETTT', es === 'complained' ? 'Marked as spam — invite blocked' : 'Bounced — never reached inbox', 'err', 'resend');
  }
  if (s === 'completed' || s === 'completed_due_to_timeout') {
    return build('DDDDD', 'Completed', 'ok', 'view');
  }

  const started = Boolean(t.started_at) || s === 'in_progress';
  const opened = Boolean(t.opened_at) || es === 'opened' || es === 'clicked';
  const delivered = Boolean(t.delivered_at) || es === 'delivered';

  if (s === 'expired') {
    if (started) return build('DDDDW', 'Expired — started, never finished', 'warn', 'resend');
    if (opened) return build('DDDWT', 'Expired — opened, never started', 'warn', 'resend');
    if (delivered) return build('DDWTT', 'Expired — delivered, never opened', 'warn', 'resend');
    return build('DWTTT', 'Expired — invite lapsed', 'warn', 'resend');
  }
  if (started) return build('DDDCT', 'In progress', 'live', null, true);
  if (opened) return build('DDDCT', 'Opened — not started yet', 'muted', 'nudge');
  if (delivered) return build('DDCTT', 'Delivered to inbox', 'muted');
  return build('DCTTT', 'Sent — delivery pending', 'muted');
}

/**
 * The per-candidate lifecycle stepper. Compact (dots only) for list rows;
 * pass `labeled` to show step names underneath (detail pane).
 */
export function AssessmentWorkflowStepper({ status, tracking, labeled = false }) {
  const wf = deriveAssessmentWorkflow(status, tracking);
  if (!wf.steps[0]) return null;
  // `labeled` is the card variant (detail pane): the state reads as a pill above
  // a full-width rail of named nodes. Unlabeled is the compact dots-only row.
  return (
    <div className={`aw${labeled ? ' aw--card' : ''}`} title={wf.title}>
      {labeled ? (
        <div className={`aw-state aw-state--${wf.tone}`}>
          {wf.live ? <AgentLoop kind="pulse" className="aw-live-dot" /> : null}
          {wf.label}
        </div>
      ) : null}
      <div className={`aw-step${labeled ? ' aw-step--labeled' : ''}`}>
        {wf.steps.map((step, i) => (
          <React.Fragment key={step.key}>
            {i > 0 ? (
              <span
                aria-hidden="true"
                className={`aw-conn${['done', 'warn', 'err'].includes(wf.steps[i - 1].state) ? ' aw-conn--on' : ''}`}
              />
            ) : null}
            {labeled ? (
              <span className="aw-node">
                <span className={`aw-dot aw-dot--${step.state}`} />
                <span className={`aw-node-lbl${step.state !== 'todo' ? ' aw-node-lbl--on' : ''}`}>{step.label}</span>
              </span>
            ) : (
              <span className={`aw-dot aw-dot--${step.state}`} />
            )}
          </React.Fragment>
        ))}
      </div>
      {labeled ? null : (
        <div className={`aw-state aw-state--${wf.tone}`}>
          {wf.live ? <AgentLoop kind="pulse" className="aw-live-dot" /> : null}
          {wf.label}
        </div>
      )}
    </div>
  );
}

// The aggregate "Assessment stage" strip (summarizeAssessmentWorkflow +
// AssessmentFunnelStrip) was removed: the home funnel's Invited stage now
// carries these sub-counts (delivered / opened / in-progress) from a real
// backend aggregate, so the client-side strip over the loaded page was
// redundant. The per-candidate stepper above is the remaining surface.
