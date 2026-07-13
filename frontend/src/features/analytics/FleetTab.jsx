// AGENT FLEET — the live operational lens. Self-fetches + polls /agent/panel
// (pulse, KPIs, per-agent cards, recent decisions) and /agent/activity (the
// org-wide merged feed). This is a now-state view (ignores the page's
// role/window scope, same as the preview). Recoloured to the in-scheme
// purple / amber / grey: WORKING = purple, PAUSED/budget = amber, IDLE = grey.
//
// Privacy: shows agent state, activity, decision outcomes, cycles/errors, and
// billed spend vs cap — never raw model cost, model names, or reasoning labels.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUpRight,
  FilterX,
  Loader2,
  Lock,
} from 'lucide-react';

import { agent as agentApi } from '../../shared/api';
import { AgentLoop } from '../../shared/motion';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  safeNum,
  fmtUsd,
  fmtUsdFine,
  fmtRelShort,
  fmtRelAgo,
  fmtClock,
  decisionTypeLabel,
  decisionChipClass,
} from './analyticsFormat';
import { outcomeOf } from './DecisionLogTab';

const PANEL_POLL_MS = 5000;
const ACTIVITY_POLL_MS = 15000;
const COHORT_BEAT_SECS = 1800;

const nextCycleLabel = (lastCycleAt) => {
  if (!lastCycleAt) return 'soon';
  const last = new Date(lastCycleAt).getTime();
  if (Number.isNaN(last)) return 'soon';
  const remaining = COHORT_BEAT_SECS - (Date.now() - last) / 1000;
  if (remaining <= 0) return 'due';
  return `${Math.ceil(remaining / 60)}m`;
};

// lucide glyph per activity kind (the preview used tabler webfont icons).
const ActivityGlyph = ({ kind }) => {
  const map = {
    run: <Loader2 size={13} aria-hidden="true" />,
    decision: <ArrowUpRight size={13} aria-hidden="true" />,
    event: <ArrowUpRight size={13} aria-hidden="true" />,
    needs_input: <FilterX size={13} aria-hidden="true" />,
  };
  return <span className="gl">{map[kind] || <ArrowUpRight size={13} aria-hidden="true" />}</span>;
};

const AgentCard = ({ a }) => {
  const spent = safeNum(a.budget_spent_cents);
  const cap = safeNum(a.budget_cap_cents);
  const p = cap > 0 ? Math.min(100, Math.round((100 * spent) / cap)) : 0;
  const act = a.activity || { label: 'IDLE', text: 'idle' };
  const actCls = act.label === 'WORKING' ? 'work' : act.label === 'PAUSED' ? 'paused' : 'idle';
  const badgeText = act.label === 'WORKING'
    ? 'WORKING'
    : act.label === 'PAUSED'
      ? `PAUSED · ${act.text || 'budget'}`
      : `IDLE · next ${a.running ? nextCycleLabel(a.last_run_at) : '—'}`;
  // Budget bar turns amber only at the caution threshold (>=90%), matching the
  // paused-card visual; otherwise lavender.
  const barHi = p >= 90;
  return (
    <div className={`an-acard ${a.running ? 'run' : 'paused'}`}>
      <div className="at">
        <span className="an-name" title={a.name}>{a.name}</span>
        {a.running ? (
          <AgentLoop kind="flow" className="an-apill on">ON</AgentLoop>
        ) : (
          <span className="an-apill off">PAUSED</span>
        )}
      </div>
      <span className={`an-actbadge ${actCls}`}>
        {actCls === 'work' ? <AgentLoop kind="pulse" className="pd" /> : null}
        {badgeText}
      </span>
      <div className="an-abar"><i className={barHi ? 'hi' : ''} style={{ width: `${p}%` }} /></div>
      <div className="an-astats">
        <div className="sr"><span>Budget</span><b>{fmtUsdFine(spent)}/{fmtUsdFine(cap)}</b></div>
        <div className="sr"><span>Cycles 24h</span><b>{safeNum(a.cycles_24h)}</b></div>
        <div className="sr"><span>Pending</span><b>{safeNum(a.pending)}</b></div>
        <div className="sr"><span>Last run</span><b>{a.last_run_at ? fmtRelAgo(a.last_run_at) : '—'}</b></div>
      </div>
    </div>
  );
};

export const FleetTab = () => {
  const [panel, setPanel] = useState(null);
  const [activity, setActivity] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    let panelTimer = null;
    let actTimer = null;
    const loadPanel = async () => {
      try {
        const res = await agentApi.panel();
        if (!cancelledRef.current) { setPanel(res?.data || null); setError(null); }
      } catch {
        if (!cancelledRef.current) setError('Could not load the agent fleet.');
      } finally {
        if (!cancelledRef.current) {
          setLoaded(true);
          panelTimer = setTimeout(loadPanel, PANEL_POLL_MS);
        }
      }
    };
    const loadActivity = async () => {
      try {
        const res = await agentApi.orgActivity({ limit: 12 });
        if (!cancelledRef.current) setActivity(res?.data?.entries || []);
      } catch { /* best-effort feed */ }
      finally {
        if (!cancelledRef.current) actTimer = setTimeout(loadActivity, ACTIVITY_POLL_MS);
      }
    };
    loadPanel();
    loadActivity();
    return () => {
      cancelledRef.current = true;
      if (panelTimer) clearTimeout(panelTimer);
      if (actTimer) clearTimeout(actTimer);
    };
  }, []);

  const k = panel?.kpis || {};
  const pulse = panel?.pulse || {};
  const agents = useMemo(() => (Array.isArray(panel?.agents) ? panel.agents : []), [panel]);
  const recent = useMemo(() => (Array.isArray(panel?.recent_decisions) ? panel.recent_decisions : []), [panel]);

  if (!loaded && !panel) {
    return (
      <div className="an-tabpanel">
        <div className="an-empty"><Spinner size={14} className="!text-current" /> Loading agent fleet…</div>
      </div>
    );
  }
  if (error && !panel) {
    return <div className="an-tabpanel"><div className="an-empty">{error}</div></div>;
  }

  const oldestHint = k.oldest_pending_age_seconds != null
    ? `oldest ${fmtRelShort(new Date(Date.now() - k.oldest_pending_age_seconds * 1000).toISOString())}`
    : null;
  const lastActivity = pulse.last_activity_at ? fmtRelAgo(pulse.last_activity_at) : '—';

  return (
    <div className="an-tabpanel">
      {/* Cohort cycle banner. */}
      <div className="an-cohort">
        <span className="gp" aria-hidden="true" />
        Agent review cycle · last run <b>{pulse.last_cycle_at ? fmtRelAgo(pulse.last_cycle_at) : '—'}</b>
        {' · '}next <b>{nextCycleLabel(pulse.last_cycle_at)}</b>
        {' · '}last activity <b>{lastActivity}</b>
      </div>

      {/* 6 fleet KPIs. */}
      <div className="an-fkpis">
        <div className="an-fkpi">
          <div className="k">Agents</div>
          <div className="v">{safeNum(k.agents_running)} <small>running</small></div>
          <div className="s">{safeNum(k.agents_paused)} paused</div>
        </div>
        <div className={`an-fkpi${safeNum(k.pending_decisions) > 0 ? ' attn' : ''}`}>
          <div className="k">Pending decisions</div>
          <div className="v">{safeNum(k.pending_decisions)}</div>
          <div className="s">{oldestHint || 'none waiting'}</div>
        </div>
        <div className="an-fkpi">
          <div className="k">Decisions today</div>
          <div className="v">{safeNum(k.decisions_today)}</div>
          <div className="s">across the fleet</div>
        </div>
        <div className="an-fkpi">
          <div className="k">Cycles · 24h</div>
          <div className="v">{safeNum(k.cycles_24h)}</div>
          <div className="s">~every 30m</div>
        </div>
        <div className={`an-fkpi${safeNum(k.errors_24h) > 0 ? ' attn' : ''}`}>
          <div className="k">Errors · 24h</div>
          <div className="v">{safeNum(k.errors_24h)}</div>
          <div className="s">{safeNum(k.errors_24h) > 0 ? 'see activity log' : 'all clear'}</div>
        </div>
        <div className="an-fkpi">
          <div className="k">Workspace budget</div>
          <div className="v">{fmtUsd(k.budget_spent_cents)}<small> / {fmtUsd(k.budget_cap_cents)}</small></div>
          <div className="s">{safeNum(k.budget_cap_cents) > 0 ? `${Math.round((100 * safeNum(k.budget_spent_cents)) / safeNum(k.budget_cap_cents))}%` : 'no cap set'}</div>
        </div>
      </div>

      {/* Per-agent cards. */}
      <div className="an-kicker">Agents on this workspace</div>
      {agents.length === 0 ? (
        <div className="an-card"><div className="an-empty">No agent-enabled roles yet. Turn on the agent for a role to see it here.</div></div>
      ) : (
        <div className="an-agrid">
          {agents.map((a) => <AgentCard key={a.role_id} a={a} />)}
        </div>
      )}

      {/* Activity log + decision log. */}
      <div className="an-grid2">
        <div className="an-card">
          <div className="ch"><div className="ct2">Activity log</div></div>
          {activity.length === 0 ? (
            <div className="an-empty">No agent activity yet.</div>
          ) : (
            <div className="an-feed">
              {activity.map((e) => (
                <div className="fi" key={`${e.kind}-${e.id}`}>
                  <ActivityGlyph kind={e.kind} />
                  <span>
                    {e.role_name ? <span className="rc">{e.role_name}</span> : null}
                    <span className="fbody">{e.title}</span>
                    <span className="ft">{fmtRelShort(e.created_at)}</span>
                    {e.detail ? <div className="fdetail">{e.detail}</div> : null}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="an-card">
          <div className="ch"><div className="ct2">Decision log</div></div>
          {recent.length === 0 ? (
            <div className="an-empty">No decisions yet.</div>
          ) : (
            <div className="an-table-scroll">
              <table className="an-table">
                <thead>
                  <tr><th>Time</th><th>Role</th><th>Decision</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {recent.map((d) => {
                    const s = String(d.status || '').toLowerCase();
                    const tone = s === 'overridden' || s === 'reverted_for_feedback' ? 'warn' : 'ok';
                    return (
                      <tr key={d.id}>
                        <td className="an-stt ok">{fmtRelShort(d.created_at)}</td>
                        <td>{d.role_name || `Role #${d.role_id}`}</td>
                        <td><span className={`an-dchip ${decisionChipClass(d.decision_type)}`}>{decisionTypeLabel(d.decision_type)}</span></td>
                        <td><span className={`an-stt ${tone}`}>{outcomeOf(d).text}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Privacy note. */}
      <div className="an-privacy">
        <Lock size={14} className="ti" aria-hidden="true" />
        <span>
          <b>Shown:</b> agent state, live activity, decision outcomes, cycles &amp; errors, and your billed
          spend vs. cap. <b>Hidden by design:</b> internal system details.
        </span>
      </div>
    </div>
  );
};

export default FleetTab;
