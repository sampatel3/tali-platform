// Public, auth-free snapshot of the /home Hub used by the demo
// showcase ("Workflow & decisions" tab). Mirrors the pattern of
// ChatShowcaseView: AgentHeader + KPI strip + ActivityFeed, all fed by
// the same MARKETING_DECISION_FEED_ROWS shape the landing already
// uses, so the visitor sees the real components on real fixtures
// without an auth gate or any backend round-trip.

import React from 'react';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { ActivityFeed } from './ActivityFeed';
import './home.css';

const _NOW = Date.now();

const SHOWCASE_FEED_ROWS = [
  {
    id: 28,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Maya Chen',
    application_id: 1042,
    role_id: 109,
    confidence: 0.92,
    reasoning: "Strong fit. Top of this role's pipeline.",
    created_at: new Date(_NOW - 2 * 60 * 1000).toISOString(),
  },
  {
    id: 27,
    status: 'pending',
    decision_type: 'reject',
    candidate_name: 'Tariq Al-Ahmad',
    application_id: 1018,
    role_id: 109,
    confidence: 0.81,
    reasoning: 'Well below your bar. Missing the must-have skills.',
    created_at: new Date(_NOW - 44 * 60 * 1000).toISOString(),
  },
  {
    id: 26,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Jordan Patel',
    application_id: 1029,
    role_id: 110,
    confidence: 0.88,
    reasoning: 'Strong system design — flag for hiring manager.',
    created_at: new Date(_NOW - 71 * 60 * 1000).toISOString(),
  },
  {
    id: 25,
    status: 'approved',
    decision_type: 'advance_to_interview',
    candidate_name: 'Priya Raman',
    application_id: 1003,
    role_id: 109,
    resolved_at: new Date(_NOW - 18 * 60 * 1000).toISOString(),
  },
  {
    id: 24,
    status: 'overridden',
    decision_type: 'reject',
    candidate_name: 'Jonas Weber',
    application_id: 994,
    role_id: 109,
    human_disposition: 'taught',
    resolution_note: 'override → advance',
    resolved_at: new Date(_NOW - 52 * 60 * 1000).toISOString(),
  },
  {
    id: 23,
    status: 'approved',
    decision_type: 'reject',
    candidate_name: 'Tom Liu',
    application_id: 988,
    role_id: 110,
    resolved_at: new Date(_NOW - 95 * 60 * 1000).toISOString(),
  },
];

const SHOWCASE_AGENT = {
  on: true,
  paused: false,
  pending: 3,
  spentCents: 1820,
  budgetCents: 5000,
  tick: 'Advanced Maya Chen to Review · 2m ago',
  inFlight: false,
};

// Shared org KPI strip (primary) + Decision-Hub health row (secondary) —
// mirrors the real HomePage layout so the demo matches the live surface.
const SHOWCASE_KPIS = [
  { l: 'In pipeline', v: '312', d: 'across 5 active roles' },
  { l: 'Active roles', v: '5', d: '2 with candidates' },
  { l: 'Awaiting you', v: '3', d: 'oldest 1h', emph: true },
  { l: 'Org budget · MTD', v: '$18', unit: '/ $50', d: '36% · proj $44 EOM', bar: 36 },
];

const SHOWCASE_KPIS_HEALTH = [
  { l: 'Decisions today', v: '14', d: '11 auto · 3 pending' },
  { l: 'Override rate · 7d', v: '8%', d: '12% taught · last 2m ago' },
];

const ShowcaseKpiTile = ({ k }) => (
  <div className={`rq-kpi ${k.emph ? 'rq-kpi-emph' : ''}`.trim()}>
    <div className="l">{k.l}</div>
    <div className="v">
      {k.emph ? <em>{k.v}</em> : k.v}
      {k.unit ? <span style={{ color: 'var(--mute)', fontSize: 15, fontWeight: 400 }}> {k.unit}</span> : null}
    </div>
    {k.bar != null ? (
      <div className="rq-bar">
        <i style={{ width: `${k.bar}%`, background: 'var(--purple)' }} />
      </div>
    ) : null}
    <div className="d">{k.d}</div>
  </div>
);

export const HomeShowcaseView = () => (
  <div>
    <AgentHeader
      kicker="HUB · 3 PENDING · 5 ACTIVE ROLES"
      title="Good morning"
      subtitle="Every decision the agent makes that needs you. Approve, override, or teach it — your calls become its training signal. The long-term goal is full automation; this is where you keep the loop honest."
      agent={SHOWCASE_AGENT}
    />

    <div className="home-body">
      <div className="rq-kpis">
        {SHOWCASE_KPIS.map((k) => <ShowcaseKpiTile key={k.l} k={k} />)}
      </div>
      <div className="rq-kpis rq-kpis-secondary">
        {SHOWCASE_KPIS_HEALTH.map((k) => <ShowcaseKpiTile key={k.l} k={k} />)}
      </div>

      <ActivityFeed
        rows={SHOWCASE_FEED_ROWS}
        selectedId={null}
        onSelect={() => {}}
        onNavigate={() => {}}
      />
    </div>
  </div>
);

export default HomeShowcaseView;
