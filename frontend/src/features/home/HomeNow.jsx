// NOW — the V4 hybrid: pending sidebar (left) + selected detail (right) +
// activity feed (full-width below). The agent-first heart of /home.
//
// Filters live in `filters` (from the parent) and persist in URL search
// params. Approve / Override / Snooze hit the existing endpoints; Teach
// opens TeachModal which POSTs /agent/feedback.

import React, { useEffect, useMemo, useState } from 'react';
import {
  Brain,
  Check,
  ExternalLink,
  Eye,
  FileText,
  Inbox,
  ListChecks,
  Search,
  X,
} from 'lucide-react';

import { agent as agentApi } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import {
  Avatar,
  ConfBar,
  DeepLinkRow,
  formatRelativeAge,
  initialsFrom,
  TypeBadge,
} from './atoms';
import { TeachModal } from './TeachModal';

const STATUS_TABS = [
  { id: 'pending', label: 'Pending' },
  { id: 'reverted_for_feedback', label: 'Returned' },
  { id: 'approved', label: 'Approved' },
  { id: 'overridden', label: 'Overrides' },
  { id: 'all', label: 'All' },
];

const TYPE_OPTIONS = [
  { id: '', label: 'All types' },
  { id: 'advance_to_interview', label: 'Advance' },
  { id: 'reject', label: 'Reject' },
  { id: 'skip_assessment_reject', label: 'Reject (no assess)' },
];

const Toolbar = ({ filters, setFilters, roles }) => (
  <div className="rq-toolbar">
    <div className="rq-toolbar-l">
      <span className="kicker mute" style={{ marginRight: 8 }}>FILTERS</span>
      <select
        className="rq-select"
        value={filters.role_id || ''}
        onChange={(e) => setFilters((f) => ({ ...f, role_id: e.target.value || null }))}
        aria-label="Filter by role"
      >
        <option value="">All roles</option>
        {roles.map((r) => (
          <option key={r.role_id} value={r.role_id}>{r.short_name || r.name}</option>
        ))}
      </select>
      <select
        className="rq-select"
        value={filters.type || ''}
        onChange={(e) => setFilters((f) => ({ ...f, type: e.target.value || null }))}
        aria-label="Filter by decision type"
      >
        {TYPE_OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
      <div className="rq-tabset" style={{ marginLeft: 6 }}>
        {STATUS_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={filters.status === t.id ? 'on' : ''}
            onClick={() => setFilters((f) => ({ ...f, status: t.id }))}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
    <div className="rq-toolbar-r">
      <span className="rq-search">
        <Search size={13} strokeWidth={2} aria-hidden="true" />
        <input
          placeholder="Search candidates, IDs, reasoning…"
          value={filters.q || ''}
          onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value || null }))}
          aria-label="Search decisions"
        />
      </span>
    </div>
  </div>
);

const PendingSidebar = ({ pending, selectedId, onSelect, loading, onNavigate }) => (
  <aside className="rq-split-list">
    <div className="rq-split-list-head">
      <span style={{ fontFamily: 'var(--font-display)', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
        Pending <span style={{ color: 'var(--purple)', marginLeft: 4 }}>{pending.length}</span>
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em' }}>
        {pending[0] ? `OLDEST ${formatRelativeAge(pending[pending.length - 1]?.created_at)}` : ''}
      </span>
    </div>
    <div className="rq-split-list-body">
      {loading && pending.length === 0 ? (
        <div style={{ padding: 16, fontSize: 13, color: 'var(--mute)' }}>Loading…</div>
      ) : pending.length === 0 ? (
        <div className="home-empty" style={{ margin: 6 }}>
          <Inbox size={18} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
          <div>Queue is empty. The agent is running unattended.</div>
        </div>
      ) : (
        pending.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`rq-split-row ${selectedId === p.id ? 'on' : ''}`.trim()}
            onClick={() => onSelect(p.id)}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <TypeBadge type={p.decision_type} size="sm" />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                {formatRelativeAge(p.created_at)}
              </span>
            </div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)', lineHeight: 1.35 }}>
              <span
                role="link"
                tabIndex={0}
                className="rq-inline-link"
                style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer' }}
                onClick={(e) => { e.stopPropagation(); onNavigate?.('candidate-report', { candidateApplicationId: p.application_id }); }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                    onNavigate?.('candidate-report', { candidateApplicationId: p.application_id });
                  }
                }}
                title="Open candidate report"
              >
                {p.candidate_name || `Application #${p.application_id}`}
              </span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--mute)', marginTop: 5, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: 'var(--font-mono)', letterSpacing: '.04em' }}>#{p.id}</span>
              {p.confidence != null ? (
                <>
                  <span style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--bg-3)', overflow: 'hidden', maxWidth: 50 }}>
                    <span style={{ display: 'block', height: '100%', width: `${(p.confidence || 0) * 100}%`, background: p.confidence >= 0.9 ? 'var(--green)' : 'var(--purple)' }} />
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--ink-2)' }}>
                    {Math.round((p.confidence || 0) * 100)}%
                  </span>
                </>
              ) : null}
            </div>
          </button>
        ))
      )}
    </div>
    <div style={{
      padding: '10px 14px', borderTop: '1px solid var(--line)', fontFamily: 'var(--font-mono)',
      fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em',
      display: 'flex', alignItems: 'center', gap: 6,
    }}>
      <ListChecks size={12} aria-hidden="true" />
      <span>If queue empties, agent runs unattended.</span>
    </div>
  </aside>
);

const DecisionDetail = ({ decision, onApprove, onOverride, onTeach, onSnooze, onNavigate, busy }) => {
  if (!decision) {
    return (
      <section className="rq-hybrid-detail">
        <div className="home-empty">Select a pending decision from the queue to inspect it here.</div>
      </section>
    );
  }
  const evidence = Array.isArray(decision.evidence?.cells) ? decision.evidence.cells : [];
  const trace = Array.isArray(decision.evidence?.trace) ? decision.evidence.trace : [];

  return (
    <section className="rq-hybrid-detail">
      <div className="rq-split-detail-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <TypeBadge type={decision.decision_type} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)', letterSpacing: '.06em' }}>
            D-{decision.id} · {formatRelativeAge(decision.created_at)} ago
          </span>
          {decision.status === 'pending' ? (
            <span className="rq-stream-pendpill">NEEDS YOU</span>
          ) : decision.status === 'reverted_for_feedback' ? (
            <span className="rq-stream-teachpill">+ FEEDBACK</span>
          ) : null}
        </div>
        {decision.confidence != null ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="kicker mute">CONFIDENCE</span>
            <ConfBar value={decision.confidence} />
          </div>
        ) : null}
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 14 }}>
        <Avatar initials={initialsFrom(decision.candidate_name)} size={48} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 600, letterSpacing: '-.02em', lineHeight: 1.2, color: 'var(--ink)' }}>
            <button
              type="button"
              className="rq-inline-link"
              style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', textAlign: 'left' }}
              onClick={() => onNavigate?.('candidate-report', { candidateApplicationId: decision.application_id })}
              title="Open candidate report"
            >
              {decision.candidate_name || `Application #${decision.application_id}`}
            </button>
          </h2>
          <div style={{ fontSize: 13, color: 'var(--mute)', marginTop: 2 }}>
            {decision.candidate_email || ''}
          </div>
        </div>
      </div>

      <p style={{ margin: '0 0 14px', fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.55, maxWidth: 760 }}>
        {decision.reasoning}
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8, marginBottom: 14 }}>
        <DeepLinkRow
          Icon={FileText}
          label="Open candidate report"
          value="Six dimensions + evidence"
          onClick={() => onNavigate?.('candidate-report', { candidateApplicationId: decision.application_id })}
        />
        <DeepLinkRow
          Icon={Eye}
          label="Open role pipeline"
          value={`Role #${decision.role_id}`}
          onClick={() => onNavigate?.('job-pipeline', { roleId: decision.role_id })}
        />
        <DeepLinkRow
          Icon={ExternalLink}
          label="Open assessment results"
          value={`Application ${decision.application_id}`}
          onClick={() => onNavigate?.('candidate-detail', { candidateDetailAssessmentId: decision.application_id })}
        />
      </div>

      {evidence.length > 0 ? (
        <div className="rq-evidence-grid">
          {evidence.map((e, i) => (
            <div key={i} className="rq-ev-cell">
              <div className="rq-ev-k">{e.k || e.label}</div>
              <div className="rq-ev-v" style={{ color: e.good === true ? 'var(--green)' : e.good === false ? 'var(--red)' : 'var(--ink)' }}>
                {e.v ?? e.value}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {trace.length > 0 ? (
        <div className="rq-trace" style={{ marginTop: 14 }}>
          <div className="rq-trace-head">
            <span className="kicker">DECISION TRACE · {trace.length} EVENTS</span>
          </div>
          <ol className="rq-trace-list">
            {trace.map((s, i) => (
              <li key={i}>
                <span className={`rq-trace-dot rq-trace-${s.who || 'agent'}`} />
                <div>
                  <div className="rq-trace-t">
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mute)', letterSpacing: '.08em', marginRight: 8, textTransform: 'uppercase' }}>{s.who || 'agent'}</span>
                    {s.t || s.title}
                  </div>
                  {s.m || s.message ? <div className="rq-trace-m">{s.m || s.message}</div> : null}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {decision.status === 'pending' || decision.status === 'reverted_for_feedback' ? (
        <div className="rq-action-bar">
          <div className="rq-action-l">
            <button type="button" className="rq-btn rq-approve" onClick={() => onApprove(decision)} disabled={busy}>
              <Check size={14} strokeWidth={2.4} aria-hidden="true" />
              Approve
            </button>
            <button type="button" className="rq-btn rq-override" onClick={() => onOverride(decision)} disabled={busy}>
              <X size={14} strokeWidth={2} aria-hidden="true" />
              Override
            </button>
            <button type="button" className="rq-btn rq-teach" onClick={() => onTeach(decision)} disabled={busy}>
              <Brain size={14} strokeWidth={2} aria-hidden="true" />
              Send back &amp; teach
            </button>
          </div>
          <button type="button" className="rq-btn rq-defer" onClick={() => onSnooze(decision)} disabled={busy}>
            Snooze 1h
          </button>
        </div>
      ) : (
        <div className="home-empty" style={{ marginTop: 12 }}>
          {decision.status === 'approved' ? 'Approved — actions are read-only.'
            : decision.status === 'overridden' ? 'Overridden — actions are read-only.'
              : `Decision is ${decision.status}.`}
        </div>
      )}
    </section>
  );
};

const ActivityFeed = ({ rows, selectedId, onSelect, onNavigate }) => (
  <section className="home-section">
    <div className="home-section-head">
      <div>
        <span className="kicker">ACTIVITY · {rows.length} ROWS</span>
        <h3 className="home-section-title">Decision feed<em>.</em></h3>
        <p className="home-section-sub">Reverse-chronological. Filtered by the toolbar above. Pending rows jump into the detail panel.</p>
      </div>
    </div>
    {rows.length === 0 ? (
      <div className="home-empty">Nothing matches these filters yet.</div>
    ) : (
      <ol className="rq-stream-list">
        {rows.map((row) => {
          const isPending = row.status === 'pending' || row.status === 'reverted_for_feedback';
          if (isPending) {
            return (
              <li
                key={row.id}
                className={`rq-stream-item ${selectedId === row.id ? 'rq-stream-active' : ''}`.trim()}
                style={{ cursor: 'pointer' }}
                onClick={() => onSelect(row.id)}
              >
                <div className="rq-stream-rail">
                  <Avatar initials={initialsFrom(row.candidate_name)} size={32} />
                  <span className="rq-stream-rule" />
                </div>
                <div className="rq-stream-body">
                  <div className="rq-stream-meta">
                    <TypeBadge type={row.decision_type} size="sm" />
                    {row.status === 'pending'
                      ? <span className="rq-stream-pendpill">NEEDS YOU</span>
                      : <span className="rq-stream-teachpill">+ FEEDBACK</span>}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                      D-{row.id} · {formatRelativeAge(row.created_at)} ago
                    </span>
                  </div>
                  <div className="rq-stream-title">
                    <button
                      type="button"
                      className="rq-inline-link"
                      style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', fontWeight: 600 }}
                      onClick={(e) => { e.stopPropagation(); onNavigate?.('candidate-report', { candidateApplicationId: row.application_id }); }}
                      title="Open candidate report"
                    >
                      {row.candidate_name || `Application #${row.application_id}`}
                    </button>
                  </div>
                  <div className="rq-stream-sub">
                    <button
                      type="button"
                      className="rq-inline-link"
                      style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
                      onClick={(e) => { e.stopPropagation(); onNavigate?.('job-pipeline', { roleId: row.role_id }); }}
                    >
                      Role #{row.role_id}
                    </button>
                    {row.confidence != null ? <> · agent {Math.round(row.confidence * 100)}% confident</> : null}
                  </div>
                  <div className="rq-stream-reason">{row.reasoning}</div>
                </div>
              </li>
            );
          }
          return (
            <li key={row.id} className="rq-stream-item">
              <div className="rq-stream-rail">
                <span className={`rq-stream-dot ${row.status === 'overridden' ? 'override' : ''}`.trim()}>
                  {row.status === 'approved' ? <Check size={12} aria-hidden="true" /> : <X size={12} aria-hidden="true" />}
                </span>
                <span className="rq-stream-rule" />
              </div>
              <div className="rq-stream-body">
                <div className="rq-stream-meta">
                  <TypeBadge type={row.decision_type} size="sm" />
                  {row.status === 'overridden' ? <span className="rq-stream-overridepill">OVERRIDE</span> : null}
                  {row.human_disposition === 'taught' ? <span className="rq-stream-teachpill">+ FEEDBACK</span> : null}
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                    D-{row.id} · {formatRelativeAge(row.resolved_at || row.created_at)} ago
                  </span>
                </div>
                <div className="rq-stream-resolved-line">
                  <button
                    type="button"
                    className="rq-inline-link"
                    style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'var(--ink)', fontWeight: 600, cursor: 'pointer' }}
                    onClick={() => onNavigate?.('candidate-report', { candidateApplicationId: row.application_id })}
                    title="Open candidate report"
                  >
                    {row.candidate_name || `Application #${row.application_id}`}
                  </button>
                  <span style={{ color: 'var(--mute)' }}> — {row.status} </span>
                  {row.resolution_note ? <span>· {row.resolution_note}</span> : null}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    )}
  </section>
);

export const HomeNow = ({
  decisions,
  pendingOrdered,
  selectedId,
  setSelectedId,
  loading,
  filters,
  setFilters,
  rolesBreakdown,
  reload,
  onNavigate,
}) => {
  const { showToast } = useToast() || { showToast: () => {} };
  const [busyId, setBusyId] = useState(null);
  const [teachFor, setTeachFor] = useState(null);

  const selected = useMemo(
    () => decisions.find((d) => d.id === selectedId) || pendingOrdered[0] || null,
    [decisions, selectedId, pendingOrdered],
  );

  const handleApprove = async (decision) => {
    setBusyId(decision.id);
    try {
      await agentApi.approveDecision(decision.id, {});
      showToast?.('Approved.', 'success');
      await reload?.();
    } catch (err) {
      showToast?.(err?.response?.data?.detail || 'Approve failed', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleOverride = async (decision) => {
    setBusyId(decision.id);
    try {
      await agentApi.overrideDecision(decision.id, {});
      showToast?.('Overridden.', 'success');
      await reload?.();
    } catch (err) {
      showToast?.(err?.response?.data?.detail || 'Override failed', 'error');
    } finally {
      setBusyId(null);
    }
  };

  const handleSnooze = async (decision) => {
    setBusyId(decision.id);
    try {
      await agentApi.snoozeDecision(decision.id);
      showToast?.('Snoozed for 1h.', 'success');
      await reload?.();
    } catch (err) {
      showToast?.(err?.response?.data?.detail || 'Snooze failed', 'error');
    } finally {
      setBusyId(null);
    }
  };

  // Keyboard shortcuts on the action bar — only fire when no modal is
  // open, no input has focus, and the user actually has a selected
  // pending decision they could act on. We intentionally don't intercept
  // single keystrokes when a textarea/select/contenteditable is focused
  // so search-as-you-type stays usable.
  useEffect(() => {
    const onKey = (e) => {
      if (teachFor) return;  // teach modal owns the keyboard while open
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const tag = (e.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (e.target?.isContentEditable) return;
      if (!selected) return;
      if (selected.status !== 'pending' && selected.status !== 'reverted_for_feedback') return;
      const k = e.key.toLowerCase();
      if (k === 'a') { e.preventDefault(); handleApprove(selected); return; }
      if (k === 'o') { e.preventDefault(); handleOverride(selected); return; }
      if (k === 't') { e.preventDefault(); setTeachFor(selected); return; }
      if (k === 's') { e.preventDefault(); handleSnooze(selected); return; }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
    // We deliberately depend on the selected decision and modal state
    // — re-binding on each pending row is cheap and keeps the closure
    // pointing at the right target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, selected?.status, teachFor]);

  return (
    <section className="home-section">
      <div className="home-section-head">
        <div>
          <span className="kicker">NOW · NEEDS YOU</span>
          <h3 className="home-section-title">Review queue<em>.</em></h3>
          <p className="home-section-sub">
            Every decision the agent makes that needs you. Approve, override, or teach it — your calls become its training signal.
          </p>
        </div>
      </div>

      <Toolbar filters={filters} setFilters={setFilters} roles={rolesBreakdown} />

      <div className="rq-hybrid-grid">
        <PendingSidebar
          pending={pendingOrdered}
          selectedId={selected?.id}
          onSelect={setSelectedId}
          loading={loading}
          onNavigate={onNavigate}
        />
        <div className="rq-hybrid-right">
          <DecisionDetail
            decision={selected}
            busy={busyId === selected?.id}
            onApprove={handleApprove}
            onOverride={handleOverride}
            onSnooze={handleSnooze}
            onTeach={(d) => setTeachFor(d)}
            onNavigate={onNavigate}
          />
        </div>
      </div>

      <ActivityFeed
        rows={decisions}
        selectedId={selected?.id}
        onSelect={setSelectedId}
        onNavigate={onNavigate}
      />

      {teachFor ? (
        <TeachModal
          decision={teachFor}
          onClose={() => setTeachFor(null)}
          onSubmitted={async () => {
            showToast?.('Feedback recorded. Decision returned to the queue.', 'success');
            await reload?.();
          }}
        />
      ) : null}
    </section>
  );
};

export default HomeNow;
