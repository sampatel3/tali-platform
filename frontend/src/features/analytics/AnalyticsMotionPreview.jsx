// PUBLIC, auth-free PREVIEW of the real /analytics Outcomes + Fleet views with
// Motion library applied. The live AnalyticsPage self-fetches every feed, so
// there's no auth-free fixture; this preview reproduces the real page shell —
// the AgentHeader, Outcomes-only 6-stat pulse band and underline tabs — and
// REUSES the real, prop-driven OutcomesTab and FleetView fed by an authored
// ANALYTICS_SHOWCASE fixture with realistic Taali numbers. A bespoke Motion +
// SVG chart (decisions per day, no chart lib) draws in beside Outcomes.
//
// Motion: the pulse KPIs tick up, the tab underline slides between tabs
// (layout), the funnel + override bars grow on enter (scoped CSS), and the
// decisions-per-day line + area draw in (pathLength / fade). Reduced motion →
// final state via the shared MotionSystemProvider + the reduced flag.

import React, { useState } from 'react';
import {
  MotionSystemProvider,
  MotionTab,
  MotionTabs,
  Reveal,
  m,
  useReducedMotionSync,
} from '../../shared/motion';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { FleetView } from './FleetTab';
import { OutcomesTab } from './OutcomesTab';
import { ANALYTICS_TABS } from './analyticsTabs';
import {
  EASE_OUT,
  NumberTicker,
  PreviewSwitcher,
} from '../../shared/motion/previewMotion';
import './AnalyticsMotionPreview.css';
import '../../styles/25-analytics.css';

// ─────────────────────────────────────────────────────────────────────────────
// ANALYTICS_SHOWCASE — authored fixture shaped to feed the pulse band + the real
// OutcomesTab ({summary, breakdown, trend, rolesBreakdown}). Realistic 30-day
// Taali org numbers: ~428 agent decisions, 41% auto-advance, a 5-stage funnel,
// a 9% override rate trending down (more agreement over time), $1,894 MTD spend.
// ─────────────────────────────────────────────────────────────────────────────
export const ANALYTICS_SHOWCASE = {
  summary: {
    kpis: {
      decisions_made: { current: 428 },
      auto_advanced: { current: 176 },
      auto_rejected: { current: 194 },
      human_review: {
        approved: 132,
        override_rate_pct: 9,
        overridden: 38,
        teach_rate_pct: 6,
        taught: 24,
      },
      org_spend: { spent_cents: 189400, budget_cents: 300000 },
    },
    funnel: [
      { key: 'applied', label: 'Applied', count: 1240, percentage: 100 },
      { key: 'scored', label: 'Scored', count: 812, percentage: 65 },
      { key: 'invited', label: 'Invited', count: 214, percentage: 17 },
      { key: 'completed', label: 'Completed', count: 138, percentage: 11 },
      { key: 'advanced', label: 'Advanced', count: 61, percentage: 5 },
      { key: 'rejected', label: 'Rejected', count: 892, percentage: 72 },
    ],
    narrator: {
      paragraph:
        'Across all roles this month the agent made 428 decisions and advanced 61 candidates to your desk; you overrode 9% of its calls and taught it on 24. Advance→hire held at 36% — the agent is handing you a shortlist that converts.',
    },
  },
  breakdown: {
    totals: { advance_conversion: { advanced_total: 61, hired: 22 } },
    roles: [
      { role_id: 7001, role_name: 'AI Engineer', decisions: { total: 186 }, advance_conversion: { advanced_total: 28, hired: 11 } },
      { role_id: 7002, role_name: 'Senior Data Engineer', decisions: { total: 132 }, advance_conversion: { advanced_total: 18, hired: 7 } },
      { role_id: 7003, role_name: 'Frontend Engineer', decisions: { total: 74 }, advance_conversion: { advanced_total: 9, hired: 3 } },
    ],
  },
  trend: {
    months: [
      { month: '2025-11', decisions: 58, override_rate_pct: 18 },
      { month: '2025-12', decisions: 74, override_rate_pct: 15 },
      { month: '2026-01', decisions: 96, override_rate_pct: 12 },
      { month: '2026-02', decisions: 88, override_rate_pct: 11 },
      { month: '2026-03', decisions: 112, override_rate_pct: 9 },
    ],
  },
  rolesBreakdown: [
    { role_id: 7001, name: 'AI Engineer', override_rate_pct: 8, budget_cents: 182000 },
    { role_id: 7002, name: 'Senior Data Engineer', override_rate_pct: 11, budget_cents: 96000 },
    { role_id: 7003, name: 'Frontend Engineer', override_rate_pct: 14, budget_cents: 41000 },
  ],
  fleet: {
    panel: {
      pulse: {
        last_cycle_at: '2026-07-14T08:49:00Z',
        last_activity_at: '2026-07-14T08:52:00Z',
      },
      kpis: {
        agents_running: 3,
        agents_paused: 1,
        pending: 14,
        pending_decisions: 12,
        decisions_today: 47,
        cycles_24h: 38,
        errors_24h: 0,
        budget_spent_cents: 6120,
        budget_cap_cents: 18000,
        oldest_pending_age_seconds: 4380,
      },
      agents: [
        {
          role_id: 7001,
          name: 'AI Engineer',
          running: true,
          paused_reason: null,
          paused_at: null,
          budget_spent_cents: 2460,
          budget_cap_cents: 7000,
          last_run_at: '2026-07-14T08:49:00Z',
          pending: 6,
          cycles_24h: 14,
          activity: { label: 'WORKING', text: 'scoring 3 candidates' },
        },
        {
          role_id: 7002,
          name: 'Senior Data Engineer',
          running: true,
          paused_reason: null,
          paused_at: null,
          budget_spent_cents: 1380,
          budget_cap_cents: 4500,
          last_run_at: '2026-07-14T08:41:00Z',
          pending: 4,
          cycles_24h: 11,
          activity: { label: 'IDLE', text: 'up to date' },
        },
        {
          role_id: 7003,
          name: 'Frontend Engineer',
          running: false,
          // Exercise the real persisted machine value. FleetView must turn it
          // into product copy rather than exposing cents or comparison syntax.
          paused_reason: 'monthly USD cap reached: 1800c >= 1800c',
          paused_at: '2026-07-14T07:58:00Z',
          budget_spent_cents: 1800,
          budget_cap_cents: 1800,
          last_run_at: '2026-07-14T07:57:00Z',
          pending: 2,
          cycles_24h: 7,
          activity: { label: 'PAUSED', text: 'monthly budget reached' },
        },
        {
          role_id: 7004,
          name: 'Product Designer',
          running: true,
          paused_reason: null,
          paused_at: null,
          budget_spent_cents: 480,
          budget_cap_cents: 4700,
          last_run_at: '2026-07-14T08:36:00Z',
          pending: 0,
          cycles_24h: 6,
          activity: { label: 'IDLE', text: 'waiting for new candidates' },
        },
      ],
      timeseries: {
        labels: [],
        cycles: [],
        decisions: [],
        errors: [],
      },
      decisions_by_type: [
        { decision_type: 'advance_to_interview', count: 18 },
        { decision_type: 'reject', count: 16 },
        { decision_type: 'send_assessment', count: 13 },
      ],
      recent_decisions: [
        {
          id: 9101,
          created_at: '2026-07-14T08:52:00Z',
          role_id: 7002,
          role_name: 'Senior Data Engineer',
          decision_type: 'advance_to_interview',
          recommendation: 'advance',
          status: 'approved',
          candidate_name: 'Nadia Rahman',
        },
        {
          id: 9102,
          created_at: '2026-07-14T08:44:00Z',
          role_id: 7001,
          role_name: 'AI Engineer',
          decision_type: 'send_assessment',
          recommendation: 'assessment',
          status: 'pending',
          candidate_name: 'Omar Aziz',
        },
      ],
    },
    activity: [
      {
        kind: 'decision',
        id: 9201,
        role_id: 7002,
        role_name: 'Senior Data Engineer',
        title: 'Recommended an interview',
        detail: 'Nadia Rahman · strong systems design evidence',
        candidate_name: 'Nadia Rahman',
        created_at: '2026-07-14T08:52:00Z',
      },
      {
        kind: 'run',
        id: 9202,
        role_id: 7001,
        role_name: 'AI Engineer',
        title: 'Automatic review completed',
        detail: '18 applications reviewed · 6 decisions need your review',
        created_at: '2026-07-14T08:49:00Z',
      },
      {
        kind: 'needs_input',
        id: 9203,
        role_id: 7003,
        role_name: 'Frontend Engineer',
        title: 'Monthly budget reached',
        detail: 'Paused until the budget is increased or next month begins',
        created_at: '2026-07-14T07:58:00Z',
      },
      {
        kind: 'event',
        id: 9204,
        role_id: 7004,
        role_name: 'Product Designer',
        title: 'Moved candidate to assessment',
        detail: 'Leila Haddad · portfolio review passed',
        created_at: '2026-07-14T07:43:00Z',
      },
    ],
  },
};

// Decisions per day for the bespoke SVG chart (last 14 days).
const DECISIONS_PER_DAY = [8, 11, 9, 14, 12, 18, 15, 21, 17, 24, 19, 26, 23, 31];

const usd = (cents) => `$${(cents / 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

// Bespoke Motion + SVG line/area chart — no chart lib. The line draws in via
// pathLength, the area fades up, and the end-point dot pops. Reduced motion →
// fully drawn immediately.
const DecisionsChart = ({ data, reduced }) => {
  const W = 640;
  const H = 190;
  const PAD = 14;
  const max = Math.max(...data);
  const stepX = (W - PAD * 2) / (data.length - 1);
  const x = (i) => PAD + i * stepX;
  const y = (v) => H - PAD - (v / max) * (H - PAD * 2);
  const line = data.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const area = `${line} L ${x(data.length - 1).toFixed(1)} ${H - PAD} L ${x(0).toFixed(1)} ${H - PAD} Z`;
  const lastX = x(data.length - 1);
  const lastY = y(data[data.length - 1]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="amp-chart-svg" role="img" aria-label="Agent decisions per day, trending up over the last 14 days" preserveAspectRatio="none">
      <defs>
        <linearGradient id="ampChartFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--purple)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--purple)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <m.path
        d={area}
        fill="url(#ampChartFill)"
        initial={reduced ? { opacity: 1 } : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8, delay: 0.3, ease: 'easeOut' }}
      />
      <m.path
        d={line}
        fill="none"
        stroke="var(--purple)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={reduced ? { pathLength: 1 } : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 1.1, ease: EASE_OUT }}
      />
      <m.circle
        cx={lastX}
        cy={lastY}
        r="4.5"
        fill="var(--purple)"
        initial={reduced ? { scale: 1, opacity: 1 } : { scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.3, delay: reduced ? 0 : 1.05, ease: EASE_OUT }}
        style={{ transformOrigin: `${lastX}px ${lastY}px` }}
      />
    </svg>
  );
};

export const AnalyticsMotionPreview = () => {
  const reduced = useReducedMotionSync();
  const [tab, setTab] = useState('outcomes');

  const { summary, breakdown, trend, rolesBreakdown, fleet } = ANALYTICS_SHOWCASE;
  const fleetSelected = tab === 'fleet';
  const k = summary.kpis;
  const hr = k.human_review;
  const spend = k.org_spend;
  const conv = breakdown.totals.advance_conversion;
  const advanceHirePct = conv.advanced_total > 0 ? Math.round((conv.hired / conv.advanced_total) * 100) : null;
  const budgetPct = spend.budget_cents > 0 ? Math.round((spend.spent_cents / spend.budget_cents) * 100) : null;

  return (
    <MotionSystemProvider>
        <div data-brand="taali" className="amp-root">
          <Reveal reduced={reduced}>
            <AgentHeader
              breadcrumbs={[{ label: fleetSelected ? 'Analytics · agents' : 'Analytics · last 30 days' }]}
              kicker={fleetSelected ? 'ANALYTICS · LIVE WORKSPACE' : 'ANALYTICS · LAST 30 DAYS · ALL ROLES'}
              title="Analytics"
              subtitle="Outcomes, your agents, and the teaching history that keeps them calibrated."
            />
          </Reveal>

          <div className="an-page">
            {/* 6-stat pulse band — reproduced markup, values tick up. Gated on
                the shared reveal trigger so it fills on mount (above the fold),
                never only on scroll. */}
            {tab === 'outcomes' ? (
              <div className={reduced ? 'an-pulse' : 'an-pulse pv-reveal'}>
                {[
                  { k: 'Decisions', v: <NumberTicker to={k.decisions_made.current} reduced={reduced} />, s: `${hr.approved.toLocaleString()} approved` },
                  { k: 'Auto-advanced', v: <NumberTicker to={k.auto_advanced.current} reduced={reduced} />, s: `${k.auto_rejected.current.toLocaleString()} auto-rejected` },
                  { k: 'Advance → hire', v: <NumberTicker to={advanceHirePct} reduced={reduced} format={(n) => `${Math.round(n)}%`} />, s: `${conv.hired} of ${conv.advanced_total} advanced`, attn: true },
                  { k: 'Override rate', v: <NumberTicker to={hr.override_rate_pct} reduced={reduced} format={(n) => `${Math.round(n)}%`} />, s: `${hr.overridden} overrides` },
                  { k: 'Taught', v: <NumberTicker to={hr.teach_rate_pct} reduced={reduced} format={(n) => `${Math.round(n)}%`} />, s: `${hr.taught} teaching events` },
                  { k: 'Spend · MTD', v: <NumberTicker to={spend.spent_cents} reduced={reduced} format={(n) => usd(n)} />, s: budgetPct != null ? `${budgetPct}% of ${usd(spend.budget_cents)}` : 'no cap set' },
                ].map((cell) => (
                  <div key={cell.k} className="an-pcell">
                    <div className="k">{cell.k}</div>
                    <div className={`v${cell.attn ? ' attn' : ''}`}>{cell.v}</div>
                    <div className="s">{cell.s}</div>
                  </div>
                ))}
              </div>
            ) : null}

            {/* Reuse the live page's Job-style tabs and keyboard behavior. */}
            <MotionTabs value={tab} onValueChange={setTab} className="vtabs" aria-label="Analytics views">
              {ANALYTICS_TABS.map((t) => (
                <MotionTab
                  key={t.key}
                  value={t.key}
                  className={`vtab${tab === t.key ? ' on' : ''}`}
                  indicatorClassName="vtab-motion-indicator"
                >
                  {t.label}
                </MotionTab>
              ))}
            </MotionTabs>

            {tab === 'outcomes' ? (
              <>
                {/* Bespoke Motion SVG chart card — draws in. */}
                <Reveal delay={0.1} className="an-card amp-chart-card" reduced={reduced}>
                  <div className="ch">
                    <div>
                      <div className="ct2">Agent decisions per day</div>
                      <div className="cd">Last 14 days · all roles</div>
                    </div>
                  </div>
                  <DecisionsChart data={DECISIONS_PER_DAY} reduced={reduced} />
                </Reveal>

                {/* The REAL OutcomesTab on the authored fixture. Scoped CSS grows
                    its funnel + override bars on enter. */}
                <Reveal delay={0.16} className="amp-outcomes" reduced={reduced}>
                  <OutcomesTab
                    summary={summary}
                    breakdown={breakdown}
                    trend={trend}
                    rolesBreakdown={rolesBreakdown}
                  />
                </Reveal>
              </>
            ) : tab === 'fleet' ? (
              <FleetView
                panel={fleet.panel}
                activity={fleet.activity}
                onOpenDecisionLog={() => setTab('log')}
              />
            ) : (
              <div className="an-empty amp-tab-placeholder">
                This tab self-fetches live data in the app. Outcomes and Agent
                fleet are fixture-driven in this preview.
              </div>
            )}
          </div>

          <PreviewSwitcher current="analytics" badge="PREVIEW · Analytics on Motion" />
        </div>
    </MotionSystemProvider>
  );
};

export default AnalyticsMotionPreview;
