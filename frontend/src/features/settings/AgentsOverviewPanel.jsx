import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { agent as agentApi } from '../../shared/api';

// Polls: the panel summary is cheap (counts + sums) so 5s matches the
// Background jobs table cadence; the activity feed is heavier so it lags.
const PANEL_POLL_MS = 5000;
const ACTIVITY_POLL_MS = 15000;
// The agent cohort runs on a ~30-min beat (resets on deploy). Used only to
// estimate the next cycle client-side — same heuristic as the local monitor.
const COHORT_BEAT_SECS = 1800;

const ACTIVITY_ICON = { run: '◐', decision: '◆', event: '→', needs_input: '?' };

// Friendly labels so the decision log + type chart never surface raw enum
// names. Anything unmapped falls back to the enum (defensive, not expected).
const TYPE_LABEL = {
  advance_to_interview: 'Advance',
  send_assessment: 'Send assessment',
  resend_assessment_invite: 'Resend invite',
  reject: 'Reject',
  skip_assessment_reject: 'Pre-screen reject',
  escalate_low_confidence: 'Escalate',
};

// Chart palette — literal tokens (SVG fill can't read CSS custom properties).
const C = {
  purple: '#5e3aa8',
  purpleLav: '#7c5cff',
  green: '#15a36a',
  red: '#e64a4a',
  amber: '#d88a1c',
  mute: '#8b8595',
  grid: '#f1edf5',
};

const prettyType = (t) => TYPE_LABEL[t] || String(t || '').replace(/_/g, ' ');

const fmtMoney = (cents) => {
  const n = Number(cents || 0) / 100;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: n >= 100 ? 0 : 2 })}`;
};

const fmtRelShort = (value) => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  const mins = Math.round((Date.now() - parsed.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
};

const fmtClock = (value) => {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
};

const nextCycleLabel = (lastCycleAt) => {
  if (!lastCycleAt) return '—';
  const last = new Date(lastCycleAt).getTime();
  if (Number.isNaN(last)) return '—';
  const elapsed = (Date.now() - last) / 1000;
  const remaining = COHORT_BEAT_SECS - elapsed;
  if (remaining <= 0) return 'due';
  return `~${Math.ceil(remaining / 60)}m`;
};

const decisionStatusTone = (status) => {
  const s = String(status || '').toLowerCase();
  if (s === 'approved' || s === 'pending') return 'ok';
  if (s === 'overridden' || s === 'reverted_for_feedback') return 'warn';
  return 'dim';
};

function Kpi({ label, value, sub, hint, tone }) {
  return (
    <div className="agz-kpi">
      <div className="agz-kpi-label">{label}</div>
      <div className={`agz-kpi-val${tone ? ` tone-${tone}` : ''}`}>
        {value}
        {sub ? <small> {sub}</small> : null}
      </div>
      {hint ? <div className="agz-kpi-hint">{hint}</div> : null}
    </div>
  );
}

function AgentCard({ a }) {
  const pct = a.budget_cap_cents > 0
    ? Math.min(100, Math.round((100 * a.budget_spent_cents) / a.budget_cap_cents))
    : 0;
  const barTone = pct >= 90 ? 'red' : pct >= 70 ? 'amber' : 'purple';
  const act = a.activity || { label: 'IDLE', text: 'idle' };
  const actCls = act.label === 'WORKING' ? 'work' : act.label === 'PAUSED' ? 'paused' : 'idle';
  const idleText = act.label === 'IDLE'
    ? `last run ${fmtRelShort(a.last_run_at)} · next ${a.next_run_at ? fmtRelShort(a.next_run_at) : 'on beat'}`
    : act.text;
  return (
    <div className={`agz-agent ${a.running ? 'run' : 'paused'}`}>
      <div className="agz-agent-top">
        <span className="agz-agent-name" title={a.name}>{a.name}</span>
        <span className={`agz-pill ${a.running ? 'on' : 'off'}`}>{a.running ? 'ON' : 'PAUSED'}</span>
      </div>
      <div className={`agz-now agz-${actCls}`}>
        <span className="agz-actbadge">{act.label}</span>
        <span className="agz-acttxt" title={idleText}>{idleText}</span>
      </div>
      <div className="agz-bar"><i className={`tone-${barTone}`} style={{ width: `${pct}%` }} /></div>
      <div className="agz-stats">
        <span>budget <b>{fmtMoney(a.budget_spent_cents)} / {fmtMoney(a.budget_cap_cents)}</b> ({pct}%)</span>
        <span>cycles 24h <b>{a.cycles_24h}</b></span>
        <span>last run <b>{fmtClock(a.last_run_at)}</b></span>
        <span>next run <b>{a.running ? (a.next_run_at ? fmtClock(a.next_run_at) : 'on beat') : '—'}</b></span>
        <span>pending <b>{a.pending}</b></span>
        <span />
      </div>
    </div>
  );
}

const tooltipStyle = {
  background: '#fff',
  border: '1px solid #e8e2ee',
  borderRadius: 10,
  fontSize: 12,
  color: '#15121a',
};

export default function AgentsOverviewPanel() {
  const [panel, setPanel] = useState(null);
  const [activity, setActivity] = useState([]);
  const [error, setError] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    let panelTimer = null;
    let actTimer = null;
    const loadPanel = async () => {
      try {
        const res = await agentApi.panel();
        if (!cancelledRef.current) { setPanel(res?.data || null); setError(null); }
      } catch (err) {
        if (!cancelledRef.current) setError('Could not load agent overview.');
      } finally {
        if (!cancelledRef.current) {
          setLoaded(true);
          panelTimer = setTimeout(loadPanel, PANEL_POLL_MS);
        }
      }
    };
    const loadActivity = async () => {
      try {
        const res = await agentApi.orgActivity({ limit: 40 });
        if (!cancelledRef.current) setActivity(res?.data?.entries || []);
      } catch { /* feed is best-effort */ }
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

  const tsData = useMemo(() => {
    const ts = panel?.timeseries;
    if (!ts?.labels) return [];
    return ts.labels.map((hour, i) => ({
      hour,
      cycles: ts.cycles?.[i] ?? 0,
      decisions: ts.decisions?.[i] ?? 0,
      errors: ts.errors?.[i] ?? 0,
    }));
  }, [panel]);

  const typeData = useMemo(
    () => (panel?.decisions_by_type || []).map((d) => ({ name: prettyType(d.decision_type), count: d.count })),
    [panel],
  );

  if (!loaded && !panel) {
    return (
      <div className="agz-loading"><Loader2 size={14} className="animate-spin" /> Loading agent overview…</div>
    );
  }
  if (error && !panel) {
    return <div className="agz-error">{error}</div>;
  }

  const k = panel?.kpis || {};
  const pulse = panel?.pulse || {};
  const agents = panel?.agents || [];
  const recent = panel?.recent_decisions || [];
  const oldestHint = k.oldest_pending_age_seconds != null
    ? `oldest ${fmtRelShort(new Date(Date.now() - k.oldest_pending_age_seconds * 1000).toISOString())}`.replace(' ago', '')
    : null;

  return (
    <div className="agz">
      <div className="agz-pulse">
        Agent cohort cycle: last <b>{fmtClock(pulse.last_cycle_at)}{pulse.last_cycle_at ? ` (${fmtRelShort(pulse.last_cycle_at)})` : ''}</b>
        {' · '}next <b>{nextCycleLabel(pulse.last_cycle_at)}</b>
        {' · '}last activity <b>{fmtRelShort(pulse.last_activity_at)}</b>
      </div>

      <div className="agz-kpis">
        <Kpi label="Agents" value={k.agents_running ?? 0} sub={`running · ${k.agents_paused ?? 0} paused`} />
        <Kpi label="Pending decisions" value={k.pending_decisions ?? 0} hint={oldestHint} tone="amber" />
        <Kpi label="Decisions today" value={k.decisions_today ?? 0} tone="purple" />
        <Kpi label="Cycles (24h)" value={k.cycles_24h ?? 0} />
        <Kpi label="Errors (24h)" value={k.errors_24h ?? 0} tone={k.errors_24h ? 'amber' : undefined} />
        <Kpi label="Workspace budget" value={fmtMoney(k.budget_spent_cents)} sub={`/ ${fmtMoney(k.budget_cap_cents)}`} tone="green" />
      </div>

      <div className="agz-section-h">Agents on this workspace</div>
      {agents.length === 0 ? (
        <div className="agz-empty">No agent-enabled roles yet. Turn on the agent for a role to see it here.</div>
      ) : (
        <div className="agz-agents">{agents.map((a) => <AgentCard key={a.role_id} a={a} />)}</div>
      )}

      <div className="agz-section-h">Last 24 hours</div>
      <div className="agz-charts">
        <div className="agz-chartcard">
          <h3>Cycles &amp; decisions / hour</h3>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={tsData} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="hour" tick={{ fontSize: 10, fill: C.mute }} interval={5} tickLine={false} axisLine={{ stroke: C.grid }} />
              <YAxis tick={{ fontSize: 10, fill: C.mute }} tickLine={false} axisLine={false} allowDecimals={false} width={28} />
              <Tooltip contentStyle={tooltipStyle} />
              <Line type="monotone" dataKey="cycles" stroke={C.purple} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="decisions" stroke={C.green} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="agz-chartcard">
          <h3>Errors / hour</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={tsData} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="hour" tick={{ fontSize: 10, fill: C.mute }} interval={5} tickLine={false} axisLine={{ stroke: C.grid }} />
              <YAxis tick={{ fontSize: 10, fill: C.mute }} tickLine={false} axisLine={false} allowDecimals={false} width={28} />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="errors" fill={C.red} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="agz-chartcard">
          <h3>Decisions by type (today)</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={typeData} layout="vertical" margin={{ top: 6, right: 12, left: 8, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 10, fill: C.mute }} tickLine={false} axisLine={{ stroke: C.grid }} allowDecimals={false} />
              <YAxis type="category" dataKey="name" tick={{ fontSize: 10, fill: C.mute }} tickLine={false} axisLine={false} width={96} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(94,58,168,0.06)' }} />
              <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                {typeData.map((entry, i) => (
                  <Cell key={entry.name} fill={[C.purple, C.purpleLav, C.amber, C.red, C.green][i % 5]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="agz-grid2">
        <div>
          <div className="agz-section-h">Activity log</div>
          {activity.length === 0 ? (
            <div className="agz-empty">No agent activity yet.</div>
          ) : (
            <ul className="agz-feed">
              {activity.map((e) => (
                <li key={`${e.kind}-${e.id}`}>
                  <span className="agz-glyph">{ACTIVITY_ICON[e.kind] || '·'}</span>
                  <div className="agz-feed-body">
                    <div className="agz-feed-title">
                      {e.role_name ? <span className="agz-role">{e.role_name}</span> : null}
                      {e.title}
                    </div>
                    {e.detail ? <div className="agz-feed-detail">{e.detail}</div> : null}
                  </div>
                  <span className="agz-when" title={e.created_at}>{fmtRelShort(e.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <div className="agz-section-h">Decision log</div>
          {recent.length === 0 ? (
            <div className="agz-empty">No decisions yet.</div>
          ) : (
            <table className="agz-table">
              <thead>
                <tr><th>Time</th><th>Role</th><th>Decision</th><th>Status</th></tr>
              </thead>
              <tbody>
                {recent.map((d) => (
                  <tr key={d.id}>
                    <td className="agz-dim">{fmtClock(d.created_at)}</td>
                    <td>{d.role_name || `Role #${d.role_id}`}</td>
                    <td><span className="agz-chip">{prettyType(d.decision_type)}</span></td>
                    <td className={`agz-st-${decisionStatusTone(d.status)}`}>{d.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="agz-privacy">
        <b>Shown:</b> agent state, live activity, pending/decision counts, cycles &amp; errors over time,
        and your billed spend vs. cap. <b>Hidden by design:</b> raw model cost, model names, and internal
        reasoning labels — this view is for operating the agents, not their internals.
      </div>
    </div>
  );
}
