import React from 'react';

/**
 * Pre-screen recommendation chip.
 * Maps the four backend recommendation strings to colour-coded badges.
 *
 * recommendation: "Strong match" | "Proceed to screening" |
 *                 "Manual review recommended" | "Below threshold" | null
 */
export function PreScreenChip({ recommendation, runAt = null, compact = false }) {
  const r = (recommendation || '').toLowerCase();
  let cls = 'ps-chip ps-chip--unrun';
  let label = 'Not pre-screened';
  if (r.startsWith('strong')) {
    cls = 'ps-chip ps-chip--strong';
    label = compact ? 'Strong' : 'Strong match';
  } else if (r.startsWith('proceed')) {
    cls = 'ps-chip ps-chip--proceed';
    label = compact ? 'Proceed' : 'Proceed';
  } else if (r.startsWith('manual')) {
    cls = 'ps-chip ps-chip--review';
    label = compact ? 'Review' : 'Manual review';
  } else if (r.startsWith('below')) {
    cls = 'ps-chip ps-chip--rejected';
    label = compact ? 'Rejected' : 'Below threshold';
  }
  const title = runAt ? `Pre-screen run: ${new Date(runAt).toLocaleString()}` : 'No pre-screen yet';
  return <span className={cls} title={title}>{label}</span>;
}

/**
 * Fraud / CV-plagiarism chip.
 * Reads `pre_screen_evidence.fraud_signals.cv_copy_paste` produced by the
 * pre-screen agent. Renders nothing when no signal is present or when the
 * detector did not trigger — we don't want to clutter clean rows.
 */
export function FraudChip({ application, compact = false }) {
  const signal = application?.pre_screen_evidence?.fraud_signals?.cv_copy_paste;
  if (!signal || !signal.triggered) return null;
  const pct = Math.round(Number(signal.score || 0) * 100);
  const title = (
    `CV plagiarism detected: ${pct}% of the CV text is copied verbatim from the `
    + `job description (threshold ${Math.round(Number(signal.threshold || 0) * 100)}%).`
  );
  return (
    <span className="ps-chip ps-chip--rejected" title={title}>
      {compact ? `Plagiarism · ${pct}%` : `Possible CV plagiarism · ${pct}%`}
    </span>
  );
}

function _fmtTs(ts) {
  if (!ts) return null;
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return null;
  }
}

/**
 * Assessment-invite tracker chip(s) for an invited candidate.
 *
 * Renders the assessment lifecycle (Invited / In progress / Completed /
 * Expired) and, while an invite is in flight, the email-delivery state
 * (Delivered / Opened / Bounced) derived from the Resend webhook. The full
 * timeline (sent / delivered / opened / started / expires) is in the tooltip.
 * Renders nothing when the candidate has no assessment yet, so not-yet-invited
 * rows stay uncluttered.
 *
 * Props: `status` = score_summary.assessment_status,
 *        `tracking` = score_summary.invite_tracking.
 */
export function AssessmentInviteChip({ status, tracking, compact = false }) {
  const s = (status || '').toLowerCase();
  const t = tracking || {};
  if (!s && !t.invite_sent_at) return null;

  let lifeCls = 'asmt-chip asmt-chip--invited';
  let lifeLabel = compact ? 'Invited' : 'Assessment sent';
  if (s === 'in_progress') {
    lifeCls = 'asmt-chip asmt-chip--progress';
    lifeLabel = compact ? 'In progress' : 'Assessment in progress';
  } else if (s === 'completed' || s === 'completed_due_to_timeout') {
    lifeCls = 'asmt-chip asmt-chip--done';
    lifeLabel = 'Completed';
  } else if (s === 'expired') {
    lifeCls = 'asmt-chip asmt-chip--expired';
    lifeLabel = 'Expired';
  }

  const lines = [];
  if (t.invite_sent_at) lines.push(`Invited: ${_fmtTs(t.invite_sent_at)}`);
  if (t.delivered_at) lines.push(`Delivered: ${_fmtTs(t.delivered_at)}`);
  if (t.opened_at) lines.push(`Email opened: ${_fmtTs(t.opened_at)}`);
  if (t.bounced_at) lines.push(`Bounced: ${_fmtTs(t.bounced_at)}`);
  if (t.started_at) lines.push(`Started: ${_fmtTs(t.started_at)}`);
  if (t.expires_at) lines.push(`Expires: ${_fmtTs(t.expires_at)}`);
  const lifeTitle = lines.join('\n') || 'Assessment invite';

  // Delivery chip — only while the invite is the active concern, or on failure.
  const es = (t.email_status || '').toLowerCase();
  // 'failed' = the send itself never succeeded (e.g. Resend rate-limited a
  // bulk-invite burst); 'bounced'/'complained' = accepted then rejected. All
  // three mean the candidate never got the invite — surface them so a recruiter
  // can resend instead of waiting on someone who never heard from us.
  const isSendFailure = es === 'failed' || es === 'bounced' || es === 'complained';
  const showDelivery = !s || s === 'pending' || s === 'in_progress' || isSendFailure;
  let deliv = null;
  if (showDelivery) {
    if (isSendFailure) {
      const failLabel = es === 'complained' ? 'Spam complaint' : es === 'failed' ? 'Not sent' : 'Bounced';
      const failTitle = es === 'failed'
        ? 'Invite could not be sent (email provider error) — resend it so the candidate gets the assessment'
        : t.bounced_at
          ? `Email ${es}: ${_fmtTs(t.bounced_at)}`
          : `Email ${es} — invite did not reach the candidate`;
      deliv = { cls: 'asmt-chip asmt-chip--bounced', label: failLabel, title: failTitle };
    } else if (t.opened_at || es === 'opened' || es === 'clicked') {
      deliv = { cls: 'asmt-chip asmt-chip--opened', label: 'Opened', title: t.opened_at ? `Email opened: ${_fmtTs(t.opened_at)}` : 'Email opened' };
    } else if (t.delivered_at || es === 'delivered') {
      deliv = { cls: 'asmt-chip asmt-chip--delivered', label: 'Delivered', title: t.delivered_at ? `Delivered: ${_fmtTs(t.delivered_at)}` : 'Delivered to inbox' };
    } else if (es === 'sent') {
      deliv = { cls: 'asmt-chip asmt-chip--sent', label: 'Sent', title: 'Accepted by email provider — delivery pending' };
    }
  }

  return (
    <>
      <span className={lifeCls} title={lifeTitle}>{lifeLabel}</span>
      {deliv ? <span className={deliv.cls} title={deliv.title}>{deliv.label}</span> : null}
    </>
  );
}

/**
 * Graph sync status chip.
 * Reads `graph_synced_at` and `graph_stale` from the application/candidate row.
 */
export function GraphStatusChip({ syncedAt, stale = false, compact = false }) {
  if (!syncedAt) {
    return (
      <span className="graph-chip graph-chip--none" title="Not synced to knowledge graph">
        {compact ? '—' : 'Not in graph'}
      </span>
    );
  }
  if (stale) {
    const title = `CV updated since last graph sync (${new Date(syncedAt).toLocaleString()})`;
    return (
      <span className="graph-chip graph-chip--stale" title={title}>
        {compact ? 'Stale' : 'Graph stale'}
      </span>
    );
  }
  const title = `Synced to graph at ${new Date(syncedAt).toLocaleString()}`;
  return (
    <span className="graph-chip graph-chip--in" title={title}>
      {compact ? 'In graph' : 'In graph'}
    </span>
  );
}
