// ROLES — per-role breakdown table.
// One row per role with pending / today / 7d / budget / override + teach
// rates. Rows deep-link to /jobs/:id. Paused or over-budget roles are
// flagged inline so a recruiter can spot a stuck role at a glance.

import React from 'react';

import { formatUsd } from './atoms';

export const HomeRoles = ({ rows, loading, onNavigate }) => (
  <section className="home-section">
    <div className="home-section-head">
      <div>
        <span className="kicker">ROLES · WHERE THE AGENT IS WORKING</span>
        <h3 className="home-section-title">By role<em>.</em></h3>
        <p className="home-section-sub">
          Live counts per role: what's pending, what landed today, where humans are correcting the agent.
        </p>
      </div>
    </div>

    {loading ? (
      <div className="home-empty">Loading…</div>
    ) : rows.length === 0 ? (
      <div className="home-empty">No roles configured yet — create one from the Jobs tab.</div>
    ) : (
      <div className="rq-roletable">
        <div className="rq-roletable-head">
          <span>Role</span>
          <span>Pending</span>
          <span>Today</span>
          <span>7 days</span>
          <span>Budget · MTD</span>
          <span>Override / Teach 7d</span>
          <span />
        </div>
        {rows.map((r) => {
          const cap = Number(r.cap_cents || 0);
          const spent = Number(r.budget_cents || 0);
          const overBudget = cap > 0 && spent > cap;
          const budgetPct = cap > 0 ? Math.min(100, (spent / cap) * 100) : 0;
          return (
            <div
              key={r.role_id}
              className="rq-roletable-row"
              role="button"
              tabIndex={0}
              onClick={() => onNavigate?.('job-pipeline', { roleId: r.role_id })}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onNavigate?.('job-pipeline', { roleId: r.role_id });
                }
              }}
              style={{ cursor: 'pointer' }}
            >
              <span className="rq-r-name">
                <span className="rq-r-name-main">{r.name}</span>
                {r.paused ? (
                  <span className="rq-r-flag amber">
                    PAUSED{r.paused_reason ? ` · ${String(r.paused_reason).toUpperCase()}` : ''}
                  </span>
                ) : null}
                {!r.agentic_mode_enabled && !r.paused ? (
                  <span className="rq-r-flag mute">AGENT OFF</span>
                ) : null}
              </span>
              <span className={r.pending > 0 ? 'rq-r-pending on' : 'rq-r-pending'}>
                {r.pending > 0 ? <em>{r.pending}</em> : <span style={{ color: 'var(--mute)' }}>—</span>}
              </span>
              <span className="rq-r-num">{r.today}</span>
              <span className="rq-r-num">{r.week}</span>
              <span className="rq-r-budget">
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, fontWeight: 500, color: overBudget ? 'var(--red)' : 'var(--ink-2)' }}>
                  {formatUsd(spent)}
                  {cap > 0 ? <span style={{ color: 'var(--mute)' }}>/{formatUsd(cap)}</span> : null}
                </span>
                {cap > 0 ? (
                  <span className="rq-bar">
                    <i style={{ width: `${budgetPct}%`, background: overBudget ? 'var(--red)' : budgetPct > 80 ? 'var(--amber)' : 'var(--purple)' }} />
                  </span>
                ) : null}
              </span>
              <span style={{ display: 'flex', flexDirection: 'column', gap: 2, fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
                <span style={{ color: r.override_rate_pct > 15 ? 'var(--amber)' : 'var(--ink-2)' }}>
                  OVR {r.override_rate_pct}%
                </span>
                <span style={{ color: r.teach_rate_pct > 0 ? 'var(--purple)' : 'var(--mute)' }}>
                  TEACH {r.teach_rate_pct}%
                </span>
              </span>
              <button
                type="button"
                className="rq-r-link"
                onClick={(e) => { e.stopPropagation(); onNavigate?.('job-pipeline', { roleId: r.role_id }); }}
              >
                Open →
              </button>
            </div>
          );
        })}
      </div>
    )}
  </section>
);

export default HomeRoles;
