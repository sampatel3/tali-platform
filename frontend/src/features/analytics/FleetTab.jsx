// The Agents view is split into a pure, prop-driven view and a small polling
// wrapper. The view deliberately shares the same KPI and role-first language
// as the rest of the product; the wrapper owns all network lifecycle state.

import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  Circle,
  FilterX,
  Loader2,
  Lock,
  Pause,
  Sparkles,
} from 'lucide-react';

import { agent as agentApi } from '../../shared/api';
import { getAgentPauseCopy } from '../../shared/agentPauseCopy';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  safeNum,
  fmtUsd,
  fmtUsdFine,
  stageLabel,
} from './analyticsFormat';

const PANEL_POLL_MS = 5000;
const ACTIVITY_POLL_MS = 15000;
const COHORT_BEAT_SECS = 1800;

const durationWords = (minutes) => {
  const safeMinutes = Math.max(0, Math.round(minutes));
  if (safeMinutes < 60) {
    const value = Math.max(1, safeMinutes);
    return `${value} minute${value === 1 ? '' : 's'}`;
  }
  const hours = Math.round(safeMinutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'}`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'}`;
};

const relativeTimeInWords = (value) => {
  if (!value) return '—';
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return '—';
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60000));
  if (minutes < 1) return 'just now';
  return `${durationWords(minutes)} ago`;
};

const nextRunInWords = (lastCycleAt) => {
  if (!lastCycleAt) return 'soon';
  const last = new Date(lastCycleAt).getTime();
  if (Number.isNaN(last)) return 'soon';
  const remaining = COHORT_BEAT_SECS - (Date.now() - last) / 1000;
  if (remaining <= 0) return 'now';
  return `in ${durationWords(Math.ceil(remaining / 60))}`;
};

const workingDetailInWords = (value) => {
  const detail = String(value || '').trim();
  const candidateCount = /\b(?:scoring|reviewing)\s+(\d+)\s+candidates?\b/i.exec(detail);
  if (candidateCount) {
    const count = Number(candidateCount[1]);
    return `Reviewing ${count} candidate${count === 1 ? '' : 's'}`;
  }
  if (/\breasoning cycle\b|\bround\s+\d+\b/i.test(detail)) return 'Reviewing candidates';
  return '';
};

const ActivityGlyph = ({ kind }) => {
  const map = {
    run: <Loader2 size={13} aria-hidden="true" />,
    decision: <ArrowUpRight size={13} aria-hidden="true" />,
    event: <ArrowUpRight size={13} aria-hidden="true" />,
    needs_input: <FilterX size={13} aria-hidden="true" />,
  };
  return <span className="gl">{map[kind] || <ArrowUpRight size={13} aria-hidden="true" />}</span>;
};

const ACTIVITY_CODE_LABELS = {
  agent_decision_queued: 'Decision ready for review',
  agent_cycle_aborted: 'Automatic review stopped',
  cv_scored: 'Candidate scored',
  assessment_invite_resent: 'Assessment invite resent',
  workable_note_posted: 'Movement summary added to Workable',
  workable_decision_note_posted: 'Movement summary added to Workable',
  workable_movement_note_failed: 'Could not add movement summary to Workable',
  workable_moved: 'Candidate moved in Workable',
  workable_move_skipped: 'Candidate was not moved in Workable',
  workable_writeback_skipped: 'Workable update skipped',
  workable_writeback_failed: 'Could not update Workable',
  workable_disqualified: 'Candidate disqualified in Workable',
  bullhorn_moved: 'Candidate moved in Bullhorn',
  bullhorn_rejected: 'Candidate rejected in Bullhorn',
  bullhorn_note_posted: 'Movement summary added to Bullhorn',
  bullhorn_decision_note_posted: 'Movement summary added to Bullhorn',
  bullhorn_movement_note_failed: 'Could not add movement summary to Bullhorn',
  bullhorn_writeback_failed: 'Could not update Bullhorn',
  auto_rejected: 'Candidate rejected automatically',
  auto_reject_failed: 'Could not reject candidate automatically',
};

const activityTitleInWords = (value) => {
  const raw = String(value || '').trim();
  if (!raw) return 'Agent activity';

  const started = /^Cycle started(?:\s*\(([^)]+)\))?/i.exec(raw);
  if (started) {
    const trigger = String(started[1] || '').toLowerCase();
    if (trigger === 'scheduled') return 'Automatic review started';
    if (trigger === 'started manually') return 'Review started manually';
    if (trigger === 'new activity') return 'Review started after new activity';
    return 'Review started';
  }
  const finished = /^Cycle finished\s*[—-]\s*(\d+)\s+decisions?/i.exec(raw);
  if (finished) {
    const count = Number(finished[1]);
    return `Review finished · ${count} decision${count === 1 ? '' : 's'} ready`;
  }
  if (/^Cycle paused/i.test(raw)) return 'Review paused · Monthly budget reached';
  if (/^Cycle failed/i.test(raw)) return 'Review failed';
  if (/^Cycle aborted/i.test(raw)) return 'Review stopped';
  if (/^Cycle\b/i.test(raw)) return 'Review updated';

  const decisionCopy = [
    [/^Recommended advance\b/i, 'Recommended an interview'],
    [/^Recommended reject at pre-screen\b/i, 'Recommended rejecting at pre-screen'],
    [/^Recommended reject\b/i, 'Recommended rejection'],
    [/^Recommended send assessment\b/i, 'Recommended an assessment'],
    [/^Recommended resend assessment\b/i, 'Recommended resending the assessment'],
    [/^Escalated\s*[—-]\s*low confidence\b/i, 'Asked for your review because confidence was low'],
  ];
  for (const [pattern, replacement] of decisionCopy) {
    if (pattern.test(raw)) return raw.replace(pattern, replacement);
  }

  const parts = raw.split(/\s+·\s+/);
  const headline = parts.shift() || '';
  const subject = parts.length ? ` · ${parts.join(' · ')}` : '';
  if (ACTIVITY_CODE_LABELS[headline]) return `${ACTIVITY_CODE_LABELS[headline]}${subject}`;

  const stageMove = /^([^→,]+)\s*→\s*([^,]+)(?:,\s*(.+))?$/.exec(headline);
  if (stageMove) {
    const from = stageLabel(stageMove[1].trim());
    const to = stageLabel(stageMove[2].trim());
    const outcome = stageMove[3] ? ` (${stageLabel(stageMove[3].trim())})` : '';
    return `Moved from ${from} to ${to}${outcome}${subject}`;
  }
  const movedTo = /^→\s*([^,]+)(?:,\s*(.+))?$/.exec(headline);
  if (movedTo) {
    const outcome = movedTo[2] ? ` (${stageLabel(movedTo[2].trim())})` : '';
    return `Moved to ${stageLabel(movedTo[1].trim())}${outcome}${subject}`;
  }

  // A code-shaped fallback is operational data, not customer copy. Known
  // values are mapped above; an unknown code receives a safe generic label.
  if (/^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(headline)) return `Agent activity${subject}`;
  if (/\b\d+\s*c\b|(?:micro|monthly)[ _-]*usd|[<>]=/i.test(raw)) return 'Agent activity';
  return raw;
};

const activityDetailInWords = (value) => {
  const detail = String(value || '').trim();
  if (!detail) return null;
  if (
    /\b\d+\s*c\b|(?:micro|monthly)[ _-]*usd|[<>]=|\b(?:role|agent|run|application)_id\s*=/i
      .test(detail)
  ) return null;
  if (/^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(detail)) return null;
  return detail;
};

const statusFor = (agent) => {
  const activity = agent?.activity || {};
  const label = String(activity.label || '').toUpperCase();
  const detail = String(activity.text || '').trim();

  if (agent?.running === false || label === 'PAUSED') {
    return {
      kind: 'paused',
      text: getAgentPauseCopy(agent?.paused_reason || detail).status,
    };
  }
  if (label === 'WORKING') {
    const plainDetail = workingDetailInWords(detail);
    return {
      kind: 'work',
      text: `Working${plainDetail ? ` · ${plainDetail}` : ''}`,
    };
  }
  return {
    kind: 'idle',
    text: `Idle · Next run ${nextRunInWords(agent?.last_run_at)}`,
  };
};

const StatusGlyph = ({ kind }) => (
  <span className={`an-agent-glyph ${kind}`} aria-hidden="true">
    {kind === 'work' ? <Sparkles size={15} /> : kind === 'paused' ? <Pause size={14} /> : <Circle size={13} />}
  </span>
);

const AgentCard = ({ agent }) => {
  const spent = safeNum(agent.budget_spent_cents);
  const cap = safeNum(agent.budget_cap_cents);
  const rawPct = cap > 0 ? Math.round((100 * spent) / cap) : 0;
  const barPct = Math.min(100, Math.max(0, rawPct));
  const barHi = rawPct >= 90;
  const status = statusFor(agent);
  const name = String(agent.name || '').trim() || 'Unnamed role';
  const budgetText = cap > 0
    ? `${fmtUsdFine(spent)} of ${fmtUsdFine(cap)} used`
    : `${fmtUsdFine(spent)} used · No limit set`;

  return (
    <Link
      to={`/jobs/${agent.role_id}?view=role-fit`}
      aria-label={`Open agent settings for ${name}`}
      style={{ color: 'inherit', display: 'block', height: '100%', textDecoration: 'none' }}
    >
      <article className="an-agent-card">
        <header className="an-agent-card-head">
          <StatusGlyph kind={status.kind} />
          <div className="an-agent-identity">
            <h3 className="an-agent-name" title={name}>{name}</h3>
            <p className={`an-agent-status ${status.kind}`}>{status.text}</p>
          </div>
        </header>

        <div className="an-agent-budget">
          <div className="an-agent-budget-head">
            <span>Monthly budget</span>
            <strong>{budgetText}</strong>
          </div>
          <div
            className="an-agent-budget-track"
            role="progressbar"
            aria-label={`${name} monthly budget used`}
            aria-valuemin="0"
            aria-valuemax="100"
            aria-valuenow={barPct}
            aria-valuetext={cap > 0 ? `${rawPct}% used` : 'No monthly cap set'}
          >
            <span
              className={`an-agent-budget-fill${barHi ? ' hi' : ''}`}
              style={{ width: `${barPct}%` }}
            />
          </div>
        </div>

        <div className="an-agent-meta">
          <div className="an-agent-meta-item"><span>Decisions waiting</span><strong>{safeNum(agent.pending)}</strong></div>
          <div className="an-agent-meta-item"><span>Last run</span><strong>{relativeTimeInWords(agent.last_run_at)}</strong></div>
          <div className="an-agent-meta-item"><span>Runs in 24 hours</span><strong>{safeNum(agent.cycles_24h)}</strong></div>
        </div>
      </article>
    </Link>
  );
};

export const FleetView = ({ panel, activity = [], onOpenDecisionLog }) => {
  const kpis = panel?.kpis || {};
  const pulse = panel?.pulse || {};
  const agents = Array.isArray(panel?.agents) ? panel.agents : [];
  const entries = Array.isArray(activity) ? activity : [];

  const activeCount = kpis.agents_running == null
    ? agents.filter((agent) => agent.running !== false).length
    : safeNum(kpis.agents_running);
  const pausedCount = kpis.agents_paused == null
    ? agents.filter((agent) => agent.running === false).length
    : safeNum(kpis.agents_paused);
  const reviewCount = safeNum(kpis.pending_decisions ?? kpis.pending);
  const oldestHint = kpis.oldest_pending_age_seconds != null
    ? `Oldest item has waited ${durationWords(safeNum(kpis.oldest_pending_age_seconds) / 60)}`
    : (reviewCount > 0 ? 'Ready for your review' : 'Nothing waiting');
  const spent = safeNum(kpis.budget_spent_cents);
  const cap = safeNum(kpis.budget_cap_cents);
  const budgetPct = cap > 0 ? Math.round((100 * spent) / cap) : 0;
  const errors = safeNum(kpis.errors_24h);
  const cycles = safeNum(kpis.cycles_24h);

  const summaryTiles = [
    {
      key: 'active-agents',
      label: 'Active agents',
      value: activeCount,
      sub: `${pausedCount} paused`,
    },
    {
      key: 'needs-review',
      label: 'Needs review',
      value: reviewCount,
      emph: reviewCount > 0,
      sub: oldestHint,
    },
    {
      key: 'workspace-spend',
      label: 'Workspace spend',
      value: fmtUsd(spent),
      unit: cap > 0 ? `of ${fmtUsd(cap)}` : null,
      bar: { pct: Math.min(100, Math.max(0, budgetPct)), over: budgetPct > 100 },
      sub: cap > 0 ? `${budgetPct}% of monthly budget used` : 'No monthly limit set',
    },
    {
      key: 'fleet-health',
      label: 'Agent status',
      value: errors > 0 ? `${errors} issue${errors === 1 ? '' : 's'}` : 'All clear',
      sub: `${cycles} run${cycles === 1 ? '' : 's'} in the past 24 hours`,
    },
  ];

  return (
    <div className="an-tabpanel">
      <div className="an-fleet-status">
        <span className="gp" aria-hidden="true" />
        <span>Agent schedule</span>
        <span>Last run <strong>{relativeTimeInWords(pulse.last_cycle_at)}</strong></span>
        <span>Next run <strong>{nextRunInWords(pulse.last_cycle_at)}</strong></span>
        <span>Last activity <strong>{relativeTimeInWords(pulse.last_activity_at)}</strong></span>
      </div>

      <div className="an-fleet-summary">
        <KpiStrip columns={4} tiles={summaryTiles} />
      </div>

      <section aria-labelledby="fleet-agents-heading">
        <h2 className="an-kicker an-fleet-heading" id="fleet-agents-heading">Agents</h2>
        {agents.length === 0 ? (
          <div className="an-card"><div className="an-empty">No agent-enabled roles yet.</div></div>
        ) : (
          <div className="an-fleet-agents">
            {agents.map((agent) => <AgentCard key={agent.role_id} agent={agent} />)}
          </div>
        )}
      </section>

      <article className="an-card an-fleet-activity">
        <header className="ch">
          <h3 className="ct2">Recent activity</h3>
          <button
            type="button"
            className="an-fleet-log-link"
            onClick={onOpenDecisionLog}
            aria-label="View decision log"
          >
            View decision log <ArrowUpRight size={13} aria-hidden="true" />
          </button>
        </header>
        {entries.length === 0 ? (
          <div className="an-empty">No agent activity yet.</div>
        ) : (
          <div className="an-feed" role="list">
            {entries.map((entry, index) => {
              const detail = activityDetailInWords(entry.detail);
              return (
                <div className="fi" role="listitem" key={`${entry.kind || 'activity'}-${entry.id ?? entry.created_at ?? index}`}>
                  <ActivityGlyph kind={entry.kind} />
                  <span>
                    {entry.role_name ? <span className="rc">{entry.role_name}</span> : null}
                    <span className="fbody">{activityTitleInWords(entry.title)}</span>
                    <span className="ft">{relativeTimeInWords(entry.created_at)}</span>
                    {detail ? <span className="fdetail">{detail}</span> : null}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </article>

      <div className="an-privacy">
        <Lock size={14} className="ti" aria-hidden="true" />
        <span>Agent activity and billed spend are visible here. Internal system details stay private.</span>
      </div>
    </div>
  );
};

export const FleetTab = ({ onOpenDecisionLog }) => {
  const [panel, setPanel] = useState(null);
  const [activity, setActivity] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    let panelTimer = null;
    let activityTimer = null;

    const loadPanel = async () => {
      try {
        const response = await agentApi.panel();
        if (!cancelledRef.current) {
          setPanel(response?.data || null);
          setError(null);
        }
      } catch {
        if (!cancelledRef.current) setError('Could not load agents.');
      } finally {
        if (!cancelledRef.current) {
          setLoaded(true);
          panelTimer = setTimeout(loadPanel, PANEL_POLL_MS);
        }
      }
    };

    const loadActivity = async () => {
      try {
        const response = await agentApi.orgActivity({ limit: 12 });
        if (!cancelledRef.current) setActivity(response?.data?.entries || []);
      } catch {
        // Activity is best-effort; the fleet summary remains useful without it.
      } finally {
        if (!cancelledRef.current) activityTimer = setTimeout(loadActivity, ACTIVITY_POLL_MS);
      }
    };

    loadPanel();
    loadActivity();
    return () => {
      cancelledRef.current = true;
      if (panelTimer) clearTimeout(panelTimer);
      if (activityTimer) clearTimeout(activityTimer);
    };
  }, []);

  if (!loaded && !panel) {
    return (
      <div className="an-tabpanel">
        <div className="an-empty"><Spinner size={14} className="!text-current" /> Loading agents…</div>
      </div>
    );
  }
  if (error && !panel) {
    return <div className="an-tabpanel"><div className="an-empty">{error}</div></div>;
  }

  return <FleetView panel={panel} activity={activity} onOpenDecisionLog={onOpenDecisionLog} />;
};

export default FleetTab;
