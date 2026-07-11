// PUBLIC, auth-free PREVIEW of the real /analytics Outcomes view with the
// Motion library applied. The live AnalyticsPage self-fetches every feed, so
// there's no auth-free fixture; this preview reproduces the real page shell —
// the AgentHeader, the 6-stat pulse band and the underline tabs — and REUSES
// the real, prop-driven OutcomesTab (funnel conversion + advance→hire +
// override-rate bars + by-role table) fed by an authored ANALYTICS_SHOWCASE
// fixture with realistic Taali numbers. A bespoke Motion + SVG chart (decisions
// per day, no chart lib) draws in beside it.
//
// Motion: the pulse KPIs tick up, the tab underline slides between tabs
// (layout), the funnel + override bars grow on enter (scoped CSS), and the
// decisions-per-day line + area draw in (pathLength / fade). Reduced motion →
// final state via <MotionConfig reducedMotion="user"> + the reduced flag.

import React, { useState } from 'react';
import { LazyMotion, domMax, MotionConfig, m } from 'motion/react';
import { Bot, Brain, FlaskConical, History, TrendingUp } from 'lucide-react';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { OutcomesTab } from './OutcomesTab';
import {
  EASE_OUT,
  NumberTicker,
  Reveal,
  PreviewSwitcher,
  useReducedMotionSync,
} from '../../shared/motion/previewMotion';
import './AnalyticsMotionPreview.css';

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
};

// Decisions per day for the bespoke SVG chart (last 14 days).
const DECISIONS_PER_DAY = [8, 11, 9, 14, 12, 18, 15, 21, 17, 24, 19, 26, 23, 31];

const TABS = [
  { key: 'outcomes', label: 'Outcomes', Icon: TrendingUp },
  { key: 'fleet', label: 'Agent fleet', Icon: Bot },
  { key: 'teaching', label: 'Teaching history', Icon: Brain },
  { key: 'ab', label: 'A·B tasks', Icon: FlaskConical },
  { key: 'log', label: 'Decision log', Icon: History },
];

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

  const { summary, breakdown, trend, rolesBreakdown } = ANALYTICS_SHOWCASE;
  const k = summary.kpis;
  const hr = k.human_review;
  const spend = k.org_spend;
  const conv = breakdown.totals.advance_conversion;
  const advanceHirePct = conv.advanced_total > 0 ? Math.round((conv.hired / conv.advanced_total) * 100) : null;
  const budgetPct = spend.budget_cents > 0 ? Math.round((spend.spent_cents / spend.budget_cents) * 100) : null;

  return (
    <LazyMotion features={domMax} strict>
      <MotionConfig reducedMotion="user">
        <div data-brand="taali" className="amp-root">
          <Reveal>
            <AgentHeader
              breadcrumbs={[{ label: 'Analytics · last 30 days' }]}
              kicker="ANALYTICS · LAST 30 DAYS · ALL ROLES"
              title="Analytics"
              subtitle="Outcomes, your agent fleet, and the teaching history that keeps the agent calibrated."
            />
          </Reveal>

          <div className="an-page">
            {/* 6-stat pulse band — reproduced markup, values tick up. */}
            <m.div
              className="an-pulse"
              initial="hidden"
              animate="show"
              variants={{ hidden: {}, show: { transition: { delayChildren: 0.1, staggerChildren: 0.06 } } }}
            >
              {[
                { k: 'Decisions', v: <NumberTicker to={k.decisions_made.current} reduced={reduced} />, s: `${hr.approved.toLocaleString()} approved` },
                { k: 'Auto-advanced', v: <NumberTicker to={k.auto_advanced.current} reduced={reduced} />, s: `${k.auto_rejected.current.toLocaleString()} auto-rejected` },
                { k: 'Advance → hire', v: <NumberTicker to={advanceHirePct} reduced={reduced} format={(n) => `${Math.round(n)}%`} />, s: `${conv.hired} of ${conv.advanced_total} advanced`, attn: true },
                { k: 'Override rate', v: <NumberTicker to={hr.override_rate_pct} reduced={reduced} format={(n) => `${Math.round(n)}%`} />, s: `${hr.overridden} overrides` },
                { k: 'Taught', v: <NumberTicker to={hr.teach_rate_pct} reduced={reduced} format={(n) => `${Math.round(n)}%`} />, s: `${hr.taught} teaching events` },
                { k: 'Spend · MTD', v: <NumberTicker to={spend.spent_cents} reduced={reduced} format={(n) => usd(n)} />, s: budgetPct != null ? `${budgetPct}% of ${usd(spend.budget_cents)}` : 'no cap set' },
              ].map((cell) => (
                <m.div
                  key={cell.k}
                  className="an-pcell"
                  variants={{ hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: EASE_OUT } } }}
                >
                  <div className="k">{cell.k}</div>
                  <div className={`v${cell.attn ? ' attn' : ''}`}>{cell.v}</div>
                  <div className="s">{cell.s}</div>
                </m.div>
              ))}
            </m.div>

            {/* Underline tabs — the active underline slides between tabs (layout). */}
            <div className="vtabs" role="tablist" aria-label="Analytics sections">
              {TABS.map((t) => {
                const { Icon } = t;
                const on = tab === t.key;
                return (
                  <button
                    key={t.key}
                    type="button"
                    role="tab"
                    aria-selected={on}
                    className={`vtab${on ? ' on' : ''} amp-vtab`}
                    onClick={() => setTab(t.key)}
                  >
                    <Icon size={16} aria-hidden="true" />
                    {t.label}
                    {on ? <m.span layoutId="ampTabUnderline" className="amp-underline" transition={{ duration: 0.32, ease: EASE_OUT }} /> : null}
                  </button>
                );
              })}
            </div>

            {tab === 'outcomes' ? (
              <>
                {/* Bespoke Motion SVG chart card — draws in. */}
                <Reveal delay={0.1} className="an-card amp-chart-card">
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
                <Reveal delay={0.16} className="amp-outcomes">
                  <OutcomesTab
                    summary={summary}
                    breakdown={breakdown}
                    trend={trend}
                    rolesBreakdown={rolesBreakdown}
                  />
                </Reveal>
              </>
            ) : (
              <div className="an-empty amp-tab-placeholder">
                This tab self-fetches live agent data in the app. The Outcomes tab
                above is the fixture-driven preview.
              </div>
            )}
          </div>

          <PreviewSwitcher current="analytics" badge="PREVIEW · Analytics on Motion" />
        </div>
      </MotionConfig>
    </LazyMotion>
  );
};

export default AnalyticsMotionPreview;
