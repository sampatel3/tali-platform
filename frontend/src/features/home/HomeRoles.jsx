// ROLES — per-role breakdown table.
// One row per role with pending / today / 7d / budget / override + teach
// rates. Rows deep-link to /jobs/:id. Paused or over-budget roles are
// flagged inline so a recruiter can spot a stuck role at a glance.

import React, { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

import { formatUsd } from './atoms';

// Map raw backend pause reasons to short, recruiter-friendly labels.
// The backend writes implementation-detail strings like "monthly USD cap
// reached: 5012c >= 5000c"; we don't want that uppercased on the home page.
const humanizePausedReason = (reason) => {
  if (!reason) return null;
  const r = String(reason).toLowerCase();
  if (r.startsWith('monthly usd cap')) return 'Monthly budget reached';
  if (r.startsWith('role paused')) return null;
  if (r.includes('decision budget')) return 'Cycle limit reached';
  return String(reason).slice(0, 32);
};

export const HomeRoles = ({ rows, loading, onNavigate, embedded = false }) => {
  const [open, setOpen] = useState(false);
  // Embedded (Monitoring) view only lists roles the agent has actually acted
  // on — roles with zero lifetime decisions are noise in the comparison.
  const visibleRows = embedded
    ? rows.filter((r) => Number(r.decisions_total) > 0)
    : rows;
  const totalPending = visibleRows.reduce((sum, r) => sum + (Number(r.pending) || 0), 0);

  const body = loading ? (
    <div className="home-empty">Loading…</div>
  ) : visibleRows.length === 0 ? (
    <div className="home-empty">
      {embedded ? 'No roles have made a decision yet.' : 'No roles configured yet — create one from the Jobs tab.'}
    </div>
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
        {visibleRows.map((r) => {
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
                    {(() => {
                      const label = humanizePausedReason(r.paused_reason);
                      return label ? `PAUSED · ${label}` : 'PAUSED';
                    })()}
                  </span>
                ) : null}
                {r.paused ? null : r.agentic_mode_enabled ? (
                  <span className="rq-r-flag on">AGENT ON</span>
                ) : (
                  <span className="rq-r-flag mute">AGENT OFF</span>
                )}
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
  );

  // Embedded as the Monitoring section's by-role comparison — table only.
  if (embedded) {
    return <div className="hm-tabpanel">{body}</div>;
  }

  return (
  <section className="home-section">
    <div className="home-section-head">
      <div>
        <span className="kicker">ROLES · WHERE THE AGENT IS WORKING</span>
        <h3 className="home-section-title">By role<em>.</em></h3>
        <p className="home-section-sub">
          Live counts per role: what's pending, what landed today, where humans are correcting the agent.
        </p>
      </div>
      <button
        type="button"
        className="home-section-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>{open ? 'Hide' : 'Show'} roles ({rows.length}{totalPending > 0 ? ` · ${totalPending} pending` : ''})</span>
        {open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      </button>
    </div>

    {open ? body : null}
  </section>
  );
};

export default HomeRoles;
