// Public, auth-free snapshot of the /home Hub used by the demo
// showcase ("Hub · the agent narrator" tab). Mirrors the pattern of
// ChatShowcaseView: AgentHeader + KPI strip + FunnelBoard + the live
// ActivityFeed and DecisionDetail components, all fed by fixture data.
//
// Unlike a static mock, this is INTERACTIVE: clicking a pending row in
// the decision feed selects it into the real <DecisionDetail> panel,
// where Approve / Override / Send-back-&-teach run against mock handlers
// (local state + a toast) instead of the live API — so a visitor feels
// the "agent recommends, you decide" loop end-to-end without a backend.

import React, { useMemo, useState } from 'react';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { useToast } from '../../context/ToastContext';
import { ActivityFeed } from './ActivityFeed';
import { DecisionDetail } from './HomeNow';
import './home.css';

const _NOW = Date.now();

// Rows match the AgentDecisionPayload shape the live feed consumes:
// role_name (RolePill), taali_score (ScoreChip), confidence ("agent N%
// confident"), and evidence.{cells,trace} for the detail panel.
const INITIAL_FEED_ROWS = [
  {
    id: 28,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Maya Chen',
    candidate_email: 'maya.chen@example.com',
    application_id: 1042,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 88,
    confidence: 0.92,
    reasoning:
      "Strong fit — clears every must-have with room to spare. Assessment 88/100; verified the dedupe before editing. Top of this role's pipeline.",
    created_at: new Date(_NOW - 2 * 60 * 1000).toISOString(),
    evidence: {
      cells: [
        { k: 'CV match', v: '94 / 100', good: true },
        { k: 'Assessment', v: '88 / 100', good: true },
        { k: 'AI collaboration', v: 'Top 12%', good: true },
        { k: 'Must-haves', v: '6 / 6', good: true },
      ],
      trace: [
        { who: 'agent', t: 'Pre-screened CV', m: 'Python + AWS + 4y backend — clears every must-have.' },
        { who: 'agent', t: 'Scored assessment', m: 'Revenue-recovery task: 88/100. Verified the dedupe before touching the loader.' },
        { who: 'agent', t: 'Recommendation', m: 'Advance to the technical panel — strongest in this pipeline today.' },
      ],
    },
  },
  {
    id: 27,
    status: 'pending',
    decision_type: 'reject',
    candidate_name: 'Tariq Al-Ahmad',
    candidate_email: 'tariq.a@example.com',
    application_id: 1018,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 41,
    confidence: 0.81,
    reasoning: 'Well below your bar. Missing the must-have distributed-systems and AWS depth; assessment stalled on the schema-drift path.',
    created_at: new Date(_NOW - 44 * 60 * 1000).toISOString(),
    evidence: {
      cells: [
        { k: 'CV match', v: '52 / 100', good: false },
        { k: 'Assessment', v: '41 / 100', good: false },
        { k: 'Must-haves', v: '2 / 6', good: false },
      ],
      trace: [
        { who: 'agent', t: 'Pre-screened CV', m: 'No distributed-systems evidence; AWS named but not demonstrated.' },
        { who: 'agent', t: 'Scored assessment', m: 'Stalled on schema drift; never reached the dedupe fix.' },
        { who: 'agent', t: 'Recommendation', m: 'Reject — below the bar you set for this role.' },
      ],
    },
  },
  {
    id: 26,
    status: 'pending',
    decision_type: 'advance_to_interview',
    candidate_name: 'Jordan Patel',
    candidate_email: 'jordan.patel@example.com',
    application_id: 1029,
    role_id: 110,
    role_name: 'Data Engineer',
    taali_score: 84,
    confidence: 0.88,
    reasoning: 'Strong system design and a clean grounding-vs-ranking split. Flag for the hiring manager — borderline on streaming depth.',
    created_at: new Date(_NOW - 71 * 60 * 1000).toISOString(),
    evidence: {
      cells: [
        { k: 'CV match', v: '86 / 100', good: true },
        { k: 'Assessment', v: '84 / 100', good: true },
        { k: 'Streaming depth', v: 'Borderline', good: null },
      ],
      trace: [
        { who: 'agent', t: 'Scored assessment', m: 'Separated grounding from ranking unprompted — strong design instinct.' },
        { who: 'agent', t: 'Recommendation', m: 'Advance, but flag streaming depth for the hiring manager.' },
      ],
    },
  },
  {
    id: 25,
    status: 'approved',
    decision_type: 'advance_to_interview',
    candidate_name: 'Priya Raman',
    application_id: 1003,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 86,
    human_disposition: 'approved',
    resolved_at: new Date(_NOW - 18 * 60 * 1000).toISOString(),
  },
  {
    id: 24,
    status: 'overridden',
    decision_type: 'reject',
    candidate_name: 'Jonas Weber',
    application_id: 994,
    role_id: 109,
    role_name: 'Senior Backend Engineer',
    taali_score: 58,
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
    role_name: 'Data Engineer',
    taali_score: 39,
    human_disposition: 'approved',
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

// One compact Decision-Hub KPI row — mirrors the real HomePage layout (shared
// <KpiStrip>) so the demo matches the live surface. Awaiting you · Decisions
// today · Org budget · Override; pipeline + active-role volume live in the
// kicker / pipeline strip.
const SHOWCASE_KPIS = [
  { key: 'awaiting', label: 'Awaiting you', value: '103', emph: true, sub: '85 decision pending' },
  { key: 'today', label: 'Decisions today', value: '14', sub: '11 auto-applied' },
  { key: 'budget', label: 'Org budget · MTD', value: '$18', unit: '/ $50', bar: { pct: 36, over: false }, sub: '36% · proj $44 EOM' },
  { key: 'override', label: 'Override rate · 7d', value: '8%', sub: '12% taught' },
];

export const HomeShowcaseView = () => {
  const { showToast } = useToast() || { showToast: () => {} };
  const [rows, setRows] = useState(INITIAL_FEED_ROWS);
  // Pre-select the freshest pending decision so the detail panel is populated
  // on first paint — the visitor immediately sees what "acting on it" looks like.
  const [selectedId, setSelectedId] = useState(INITIAL_FEED_ROWS[0].id);

  const selected = useMemo(
    () => rows.find((row) => row.id === selectedId) || null,
    [rows, selectedId],
  );

  const patchRow = (id, patch) =>
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...patch } : row)));

  const handleApprove = (decision) => {
    patchRow(decision.id, {
      status: 'approved',
      human_disposition: 'approved',
      resolved_at: new Date().toISOString(),
    });
    const verb = decision.decision_type === 'reject' ? 'rejected' : 'advanced';
    showToast(`Approved — ${decision.candidate_name} ${verb}. In the live product this writes back to Workable.`, 'success');
  };

  const handleAlternative = (decision, alt) => {
    patchRow(decision.id, {
      status: 'overridden',
      human_disposition: 'overridden',
      resolution_note: `override → ${String(alt?.label || 'alternative').toLowerCase()}`,
      resolved_at: new Date().toISOString(),
    });
    showToast(`Overridden — ${alt?.label || 'alternative'}. Your call becomes the agent's training signal.`, 'success');
  };

  const handleTeach = (decision) => {
    patchRow(decision.id, { status: 'reverted_for_feedback' });
    showToast(`Sent back with feedback — the agent re-evaluates ${decision.candidate_name} with your correction.`, 'info');
  };

  const handleSnooze = () => {
    showToast('Snoozed 1h — it drops back into your queue later.', 'info');
  };

  return (
    <div>
      <AgentHeader
        kicker="HUB · 103 AWAITING YOU · 5 ACTIVE ROLES"
        title="Good morning"
        subtitle="Every decision the agent makes that needs you. Approve, override, or teach it — your calls become its training signal. The long-term goal is full automation; this is where you keep the loop honest."
        agent={SHOWCASE_AGENT}
      />

      <div className="home-body">
        <KpiStrip columns={4} tiles={SHOWCASE_KPIS} />

        <FunnelBoard
          scopeLabel="all roles"
          stageCounts={{ applied: 312, scored: 184, invited: 9, completed: 4, advanced: 61, rejected: 1905 }}
          decisionsByType={{ send_assessment: 20, reject: 80, advance_to_interview: 3, skip_assessment_reject: 0 }}
        />

        {/* Live split-view: the decision feed on the left, the real
            DecisionDetail action panel on the right. Clicking a pending row
            populates the panel; Approve / Override / Teach run on mock
            handlers. Mirrors the Hub's pending hybrid view. */}
        <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-start">
          <ActivityFeed
            rows={rows}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onNavigate={() => {}}
            subtitle="Click any pending decision to review it on the right — approve, override, or send it back to teach the agent."
          />
          <div className="lg:sticky lg:top-4">
            <DecisionDetail
              decision={selected}
              onApprove={handleApprove}
              onAlternative={handleAlternative}
              onTeach={handleTeach}
              onSnooze={handleSnooze}
              onNavigate={() => {}}
              onReEvaluate={() => {}}
              busy={false}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default HomeShowcaseView;
