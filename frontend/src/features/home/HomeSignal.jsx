// SIGNAL — what the agent has been told (by humans and by reality).
// Three subsections:
//   1. Pending co-sign — org-scope teach feedback awaiting a second admin.
//   2. Recent feedback — last ~20 teach events; clickable revert within
//      the 1h grace window.
//   3. Realised outcomes — what actually happened to candidates after
//      approved agent decisions (interviewed / hired / rejected_confirmed).
//      Sourced from role.agent_calibration["outcomes"] via
//      GET /agent/realised-outcomes.
//
// Deliberately *not* here: any "rubric revision / retune queued" copy.
// Improving the agent's scoring is a separate workstream — see
// docs/HOME_HUB_DESIGN.md §8.

import React, { useEffect, useState } from 'react';
import { Award, Brain, CheckCircle2, ChevronDown, ChevronUp, ShieldCheck, Undo2, X } from 'lucide-react';

import { agent as agentApi } from '../../shared/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { formatRelativeAge } from './atoms';

const FAILURE_LABEL = {
  rubric_mismatch: 'Rubric mismatch',
  wrong_threshold: 'Wrong threshold',
  missing_signal: 'Missing signal',
  over_confident: 'Over-confident',
  policy_violation: 'Policy violation',
  other: 'Other',
};

const SCOPE_LABEL = { decision: 'this decision', role: 'this role', org: 'org-wide' };

const PendingCosignTray = ({ rows, currentUserId, onCosign }) => {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="signal-cosign-tray">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <ShieldCheck size={16} aria-hidden="true" style={{ color: 'var(--purple)' }} />
        <strong style={{ color: 'var(--ink)' }}>Awaiting co-sign</strong>
        <span className="kicker mute">{rows.length} ITEM{rows.length === 1 ? '' : 'S'}</span>
      </div>
      <div className="signal-row-grid">
        {rows.map((row) => {
          const isOwn = currentUserId != null && row.reviewer_id === Number(currentUserId);
          return (
            <div key={row.id} className="signal-cosign-row">
              <div>
                <div style={{ fontSize: 'var(--fs-subtitle)', color: 'var(--ink)', fontWeight: 500 }}>
                  Org-wide correction from <strong>{row.reviewer_name || `User #${row.reviewer_id}`}</strong>
                </div>
                <div style={{ fontSize: 'var(--fs-caption)', color: 'var(--mute)', marginTop: 2 }}>
                  {FAILURE_LABEL[row.failure_mode] || row.failure_mode} · D-{row.decision_id} · {formatRelativeAge(row.created_at)} ago
                </div>
                <div style={{ fontSize: 'var(--fs-body)', color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.5 }}>
                  &ldquo;{row.correction_text}&rdquo;
                </div>
              </div>
              <button
                type="button"
                className="rq-btn rq-teach"
                onClick={() => onCosign(row.id)}
                disabled={isOwn}
                title={isOwn ? "You can't co-sign your own submission" : 'Co-sign to apply this correction'}
              >
                <ShieldCheck size={13} aria-hidden="true" />
                Co-sign
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const FeedbackList = ({ rows, currentUserId, onRevert }) => {
  if (!rows || rows.length === 0) {
    return <div className="signal-empty">No teach events yet — once you click "Send back &amp; teach" on a decision, it lands here.</div>;
  }
  const now = Date.now();
  return (
    <ul className="signal-list">
      {rows.map((row) => {
        const ageMs = now - new Date(row.created_at).getTime();
        const inGrace = !row.applied_at && !row.reverted_at && ageMs < 60 * 60 * 1000;
        const canRevert = inGrace;
        return (
          <li key={row.id}>
            <div className="signal-li-head">
              <Brain size={13} aria-hidden="true" style={{ color: 'var(--purple)' }} />
              <strong style={{ color: 'var(--ink)' }}>{row.reviewer_name || `User #${row.reviewer_id}`}</strong>
              <span>taught the agent · {SCOPE_LABEL[row.scope] || row.scope}</span>
              <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', letterSpacing: '.04em' }}>
                {formatRelativeAge(row.created_at)} ago
              </span>
            </div>
            <div className="signal-li-body">
              <span className="rq-stream-teachpill" style={{ marginRight: 8 }}>{FAILURE_LABEL[row.failure_mode] || row.failure_mode}</span>
              {row.correction_text}
            </div>
            <div style={{ fontSize: 'var(--fs-caption)', color: 'var(--mute)', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <span>D-{row.decision_id}</span>
              {row.role_name ? <span>· {row.role_name}</span> : null}
              {row.cosign_required ? (
                row.cosigned_at
                  ? <span style={{ color: 'var(--purple-2)' }}>· co-signed by {row.cosigned_by_name || 'admin'}</span>
                  : <span style={{ color: 'var(--amber)' }}>· awaiting co-sign</span>
              ) : null}
              {row.applied_at ? <span style={{ color: 'var(--purple-2)' }}>· applied</span> : null}
              {row.reverted_at ? <span style={{ color: 'var(--mute)' }}>· reverted</span> : null}
              {canRevert ? (
                <button
                  type="button"
                  className="rq-btn ghost sm"
                  onClick={() => onRevert(row.id)}
                  style={{ marginLeft: 'auto' }}
                  title="Revert this feedback (1h grace window)"
                >
                  <Undo2 size={12} aria-hidden="true" />
                  Revert
                </button>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
};

// In-scheme purple/grey only (no traffic-light): a confirmed hire reads deep
// purple (the strongest positive), interview lavender-purple, a confirmed
// rejection grey. Matches the analytics-preview palette.
const OUTCOME_LABEL = {
  interviewed: { label: 'Interviewed', color: 'var(--purple-lav)', Icon: CheckCircle2 },
  hired: { label: 'Hired', color: 'var(--purple-2)', Icon: Award },
  rejected_confirmed: { label: 'Rejected · confirmed', color: 'var(--mute)', Icon: X },
};

const VERB_FOR_DECISION = {
  advance_to_interview: 'advance',
  reject: 'reject',
  skip_assessment_reject: 'reject (pre-screen)',
};

const OutcomesList = ({ rows }) => {
  if (!rows || rows.length === 0) {
    return (
      <div className="signal-empty">
        Realised outcomes appear here when an agent decision plays out downstream — the candidate reaches interview, gets hired, or has their rejection confirmed.
      </div>
    );
  }
  return (
    <ul className="signal-list">
      {rows.map((row, i) => {
        const cfg = OUTCOME_LABEL[row.outcome] || { label: row.outcome, color: 'var(--mute)', Icon: CheckCircle2 };
        const Icon = cfg.Icon;
        const verb = VERB_FOR_DECISION[row.decision_type] || row.decision_type;
        return (
          <li key={`${row.decision_id || row.application_id}-${i}`}>
            <div className="signal-li-head">
              <Icon size={13} aria-hidden="true" style={{ color: cfg.color }} />
              <strong style={{ color: cfg.color }}>{cfg.label}</strong>
              <span>· agent's <em>{verb}</em> on {row.role_name || `Role #${row.role_id}`}</span>
              <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', letterSpacing: '.04em' }}>
                {formatRelativeAge(row.observed_at)} ago
              </span>
            </div>
            {row.decision_id ? (
              <div style={{ fontSize: 'var(--fs-caption)', color: 'var(--mute)', fontFamily: 'var(--font-mono)', letterSpacing: '.04em' }}>
                D-{row.decision_id}
                {row.application_id ? ` · A-${row.application_id}` : ''}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
};

export const HomeSignal = ({ feedback, outcomes, loading, reload, embedded = false }) => {
  const { user } = useAuth() || {};
  const { showToast } = useToast() || { showToast: () => {} };

  const cosignPending = (feedback || []).filter(
    (f) => f.cosign_required && !f.cosigned_at && !f.reverted_at,
  );
  const [open, setOpen] = useState(false);

  // feedback arrives async after first render, so the initial useState
  // value can't see co-sign items. Auto-open when any show up; once
  // opened, leave the user in control of closing it.
  useEffect(() => {
    if (cosignPending.length > 0) setOpen(true);
  }, [cosignPending.length]);

  const handleCosign = async (id) => {
    try {
      await agentApi.cosignFeedback(id);
      showToast?.('Co-signed. The agent will apply this correction overnight.', 'success');
      await reload?.();
    } catch (err) {
      showToast?.(err?.response?.data?.detail || 'Co-sign failed', 'error');
    }
  };

  const handleRevert = async (id) => {
    try {
      await agentApi.revertFeedback(id);
      showToast?.('Feedback reverted.', 'success');
      await reload?.();
    } catch (err) {
      showToast?.(err?.response?.data?.detail || 'Revert failed', 'error');
    }
  };

  const summary = [
    feedback?.length ? `${feedback.length} teach` : null,
    outcomes?.length ? `${outcomes.length} outcome${outcomes.length === 1 ? '' : 's'}` : null,
    cosignPending.length ? `${cosignPending.length} awaiting co-sign` : null,
  ].filter(Boolean).join(' · ');

  const content = (
    <>
      <PendingCosignTray
        rows={cosignPending}
        currentUserId={user?.id}
        onCosign={handleCosign}
      />

      <div className="signal-grid">
        <div>
          <div className="kicker" style={{ marginBottom: 8 }}>RECENT FEEDBACK · WHAT HUMANS CORRECTED</div>
          {loading ? <div className="signal-empty">Loading…</div> : <FeedbackList rows={feedback} currentUserId={user?.id} onRevert={handleRevert} />}
        </div>
        <div>
          <div className="kicker" style={{ marginBottom: 8 }}>REALISED OUTCOMES · WHAT REALITY CONFIRMED</div>
          {loading ? <div className="signal-empty">Loading…</div> : <OutcomesList rows={outcomes} />}
        </div>
      </div>
    </>
  );

  // Embedded as the Monitoring section's "Quality" tab — no section chrome.
  if (embedded) {
    return <div className="hm-tabpanel">{content}</div>;
  }

  return (
    <section className="home-section">
      <div className="home-section-head">
        <div>
          <span className="kicker">SIGNAL · LEARNING TRACE</span>
          <h3 className="home-section-title">Is Taali learning from you<em>?</em></h3>
          <p className="home-section-sub">
            Every "send back &amp; teach" click ends up here. When a batch fires, you see the rubric revision it produced. This is the loop made visible.
          </p>
        </div>
        <button
          type="button"
          className="home-section-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span>{open ? 'Hide' : 'Show'} signal{summary ? ` (${summary})` : ''}</span>
          {open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
        </button>
      </div>

      {open ? content : null}
    </section>
  );
};

export default HomeSignal;
