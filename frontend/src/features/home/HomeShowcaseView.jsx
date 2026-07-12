// Public, auth-free snapshot of the /home Hub used by the demo showcase
// ("The Hub" tab — tab 01). Mirrors the LIVE home layout: the AgentHeader slab
// over the 3-column app-shell — agent rail (AgentSidebar) · decision feed +
// detail (ActivityFeed/DecisionDetail) · the agent chat dock. The feed and the
// dock run on fixture data + mock handlers (local state + a toast) instead of
// the live API, so a visitor feels the whole "agent works, you steer + decide"
// loop end-to-end without a backend.

import React, { useMemo, useState } from 'react';
import { MessageSquare } from 'lucide-react';

import { AgentHeader } from '../../shared/layout/AgentHeader';
import { KpiStrip } from '../../shared/ui/KpiStrip';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { useToast } from '../../context/ToastContext';
import { ChatMessage, ChatComposer } from '../../shared/chat';
import { ActivityFeed } from './ActivityFeed';
import { DecisionDetail } from './HomeNow';
import { AgentSidebar } from './agentchat/AgentSidebar';
import { ImpactCard, DraftTaskCard } from './agentchat/cards.jsx';
import './home.css';
import './agentchat/agentchat.css';

const _NOW = Date.now();

// Score-provenance line the live feed + detail panel render under each score
// ("Scored {date} · v2.1.0 · Sonnet" in the detail panel; a version pill in the
// list). Today's decisions are all on the current holistic engine.
const prov = (hoursAgo, version = '2.1.0') => ({
  engine_version: version,
  scored_at: new Date(_NOW - hoursAgo * 60 * 60 * 1000).toISOString(),
});

// Rows match the AgentDecisionPayload shape the live feed consumes:
// role_name (RolePill), taali_score (ScoreChip), confidence ("agent N%
// confident"), score_summary.score_provenance (ScoreProvenance), and
// evidence.{cells,trace} for the detail panel.
export const INITIAL_FEED_ROWS = [
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
    score_summary: { score_provenance: prov(0.1) },
    confidence: 0.92,
    reasoning:
      "Strong fit — clears every must-have with room to spare. Assessment 88/100; verified the dedupe before editing. Top of this role's pipeline.",
    created_at: new Date(_NOW - 2 * 60 * 1000).toISOString(),
    evidence: {
      cells: [
        { k: 'CV match', v: '94 / 100', good: true },
        { k: 'Assessment', v: '88 / 100', good: true },
        { k: 'Discernment', v: 'Top 12%', good: true },
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
    score_summary: { score_provenance: prov(0.8) },
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
    score_summary: { score_provenance: prov(1.2) },
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
    score_summary: { score_provenance: prov(0.4) },
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
    score_summary: { score_provenance: prov(0.9) },
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
    score_summary: { score_provenance: prov(1.6) },
    human_disposition: 'approved',
    resolved_at: new Date(_NOW - 95 * 60 * 1000).toISOString(),
  },
];

export const SHOWCASE_AGENT = {
  on: true,
  paused: false,
  pending: 3,
  spentCents: 1820,
  budgetCents: 5000,
  tick: 'Advanced Maya Chen to Review · 2m ago',
  inFlight: false,
};

export const SHOWCASE_KPIS = [
  { key: 'awaiting', label: 'Awaiting you', value: '103', emph: true, sub: '85 not yet decided by the agent' },
  { key: 'today', label: 'Decisions today', value: '14', sub: '11 auto-applied' },
  { key: 'budget', label: 'Org budget · MTD', value: '$18', unit: '/ $50', bar: { pct: 36, over: false }, sub: '36% · proj $44 EOM' },
  { key: 'override', label: 'Override rate · 7d', value: '8%', sub: '12% taught' },
];

// The agent rail — every live role, so you can click one and steer (or activate)
// its agent. Mix of on / paused / off so the dock's states are visible.
export const SHOWCASE_AGENTS = [
  { role_id: 109, role_name: 'Senior Backend Engineer', group: 'on_paused', agent_enabled: true, unread_messages: 0, open_questions: 1, pending_decisions: 85, last_message_preview: 'Capped salary at AED 25k · re-screened 4', budget_cap_cents: 5000, budget_spent_cents: 1820 },
  { role_id: 110, role_name: 'Data Engineer', group: 'on_paused', agent_enabled: true, unread_messages: 0, open_questions: 0, pending_decisions: 12, last_message_preview: 'Idle · waiting for new candidates', budget_cap_cents: 5000, budget_spent_cents: 640 },
  { role_id: 111, role_name: 'AI Delivery Lead', group: 'on_paused', agent_enabled: false, agent_paused: true, agent_paused_reason: 'paused by recruiter', pending_decisions: 3, last_message_preview: '' },
  { role_id: 112, role_name: 'Platform Engineer', group: 'previously_on', agent_enabled: false, pending_decisions: 0, last_message_preview: 'Agent off · ran last week' },
  { role_id: 113, role_name: 'Senior Cloud Architect', group: 'starred', agent_enabled: false, pending_decisions: 0, last_message_preview: 'Agent off — tap to set up' },
  { role_id: 114, role_name: 'DataOps Engineer', group: 'active', agent_enabled: false, pending_decisions: 0, last_message_preview: 'Agent off — tap to set up' },
];

// Mock impact cards in the live shapes ImpactCard / DraftTaskCard render.
const CONSTRAINT_CARD = {
  type: 'constraint_change',
  action: 'updated',
  criterion: { text: 'Salary ≤ AED 25,000' },
  would_rescreen: { count: 4, est_cost_usd: 0.2 },
};
const SIM_CARD = {
  type: 'threshold_simulation',
  current_threshold: 70,
  simulated_threshold: 65,
  delta_above: 6,
  added_sample: ['Ada Okafor', 'Bo Zhang', 'Chen Wei'],
};
const REJECT_QUESTIONS = [
  {
    key: 'issues',
    prompt: "What's off about this draft?",
    multi: true,
    options: [
      { value: 'scenario', label: 'Scenario unrealistic / off-role' },
      { value: 'difficulty', label: 'Wrong difficulty' },
      { value: 'rubric', label: 'Rubric weights off' },
      { value: 'decisions', label: 'Decisions weak or unclear' },
    ],
  },
  {
    key: 'direction',
    prompt: 'What should the revision do?',
    multi: false,
    options: [
      { value: 'targeted', label: 'Targeted fix — keep the structure' },
      { value: 'harder', label: 'Make it harder' },
      { value: 'reweight', label: 'Reweight toward decisions' },
    ],
  },
];
const DRAFT_CARD = {
  type: 'draft_task_review',
  role_id: 109,
  drafts: [
    {
      task_id: 1,
      name: 'Revenue-Recovery Incident Under Pressure',
      deliverable_kind: 'code',
      decisions: [
        { headline: 'Stop the double-charge before the dedupe fix' },
        { headline: 'Decide whether to replay or drop the stuck queue' },
      ],
      rubric: [
        { name: 'design_decisions_articulated', weight: 0.35 },
        { name: 'reasoning_under_pressure', weight: 0.25 },
        { name: 'release_safety', weight: 0.2 },
      ],
      repo_file_count: 9,
    },
  ],
  reject_questions: REJECT_QUESTIONS,
};

// The agent chat dock — the central new surface. Static-but-real: it renders the
// shared <ChatMessage>/<ChatComposer> + the live impact / draft-task cards, fed
// with fixture data, so it looks exactly like the product.
export const ShowcaseDock = ({ onAct }) => {
  const [input, setInput] = useState('');
  const submit = (text) => {
    setInput('');
    onAct(`“${text}” — in the live product this runs the agent and posts the impact here.`);
  };
  return (
    <aside className="ac-dock">
      <div className="ac-dock-head">
        <MessageSquare size={15} />
        <span>Ask the agent</span>
        <span className="ac-dock-role">Senior Backend Engineer</span>
      </div>
      <div className="ac-stream">
        <ChatMessage role="user" text="Cap salary at AED 25k on this role" />
        <ChatMessage
          role="assistant"
          text={"Done — set the cap to **AED 25,000**. 22 of 278 candidates stated a figure; the cap drops 4 of them, the rest are unverified so I can't filter on them. Want me to re-screen just the 4 affected?"}
        >
          <ImpactCard card={CONSTRAINT_CARD} onApply={() => {}} busy={false} />
        </ChatMessage>
        <ChatMessage role="user" text="what if I drop the cut-off to 65?" />
        <ChatMessage
          role="assistant"
          text={"Dropping the cut-off **70 → 65** brings 6 more into review — Ada, Bo and Chen lead them. Already-advanced and rejected candidates stay put."}
        >
          <ImpactCard card={SIM_CARD} onApply={() => {}} busy={false} />
        </ChatMessage>
        <ChatMessage
          role="assistant"
          text={"You've also got an assessment task I drafted for this role — approve it, or reject with a steer and I'll re-author it:"}
        >
          <DraftTaskCard card={DRAFT_CARD} onApprove={() => onAct('Approved — the task is now live and assignable.')} onRevise={() => onAct('On it — re-authoring the task from your feedback.')} busy={false} />
        </ChatMessage>
      </div>
      <div className="ac-dock-composer">
        <ChatComposer
          value={input}
          onChange={setInput}
          onSubmit={submit}
          placeholder="Ask about this role's pool, or tell the agent to change something"
        />
      </div>
    </aside>
  );
};

export const HomeShowcaseView = () => {
  const { showToast } = useToast() || { showToast: () => {} };
  const [rows, setRows] = useState(INITIAL_FEED_ROWS);
  const [selectedId, setSelectedId] = useState(INITIAL_FEED_ROWS[0].id);

  const selected = useMemo(
    () => rows.find((row) => row.id === selectedId) || null,
    [rows, selectedId],
  );

  const patchRow = (id, patch) =>
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...patch } : row)));

  const handleApprove = (decision) => {
    patchRow(decision.id, { status: 'approved', human_disposition: 'approved', resolved_at: new Date().toISOString() });
    const verb = decision.decision_type === 'reject' ? 'rejected' : 'advanced';
    showToast(`Approved — ${decision.candidate_name} ${verb}. In the live product this writes back to Workable.`, 'success');
  };

  const handleAlternative = (decision, alt) => {
    patchRow(decision.id, { status: 'overridden', human_disposition: 'overridden', resolution_note: `override → ${String(alt?.label || 'alternative').toLowerCase()}`, resolved_at: new Date().toISOString() });
    showToast(`Overridden — ${alt?.label || 'alternative'}. Your call becomes the agent's training signal.`, 'success');
  };

  const handleTeach = (decision) => {
    patchRow(decision.id, { status: 'reverted_for_feedback' });
    showToast(`Sent back with feedback — the agent re-evaluates ${decision.candidate_name} with your correction.`, 'info');
  };

  const handleSnooze = () => showToast('Snoozed 1h — it drops back into your queue later.', 'info');

  return (
    <div className="home-app" style={{ height: '100vh' }}>
      <AgentHeader
        kicker="HUB · 103 AWAITING YOU · 4 ACTIVE ROLES"
        title="Good morning"
        subtitle="Steer each role's agent in plain English, then approve, override, or teach its calls — this is where you keep the loop honest."
        agent={SHOWCASE_AGENT}
      />

      <div className="ac-shell">
        <AgentSidebar agents={SHOWCASE_AGENTS} activeRoleId={109} onSelect={() => {}} />

        <div className="ac-main">
          <div className="home-body">
            <KpiStrip columns={4} tiles={SHOWCASE_KPIS} />

            <FunnelBoard
              variant="flat"
              scopeLabel="all roles"
              stageCounts={{ applied: 312, scored: 184, invited: 9, completed: 4, advanced: 61, rejected: 1905, in_assessment: 6, invited_opened: 7, invited_delivered: 8 }}
              decisionsByType={{ send_assessment: 20, reject: 80, advance_to_interview: 3, skip_assessment_reject: 0 }}
            />

            <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-start">
              <ActivityFeed
                rows={rows}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onNavigate={() => {}}
                subtitle="Click any pending decision to review it on the right — approve, override, or send it back to teach the agent."
              />
              <div className="min-w-0 lg:sticky lg:top-4">
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

        <ShowcaseDock onAct={(msg) => showToast(msg, 'info')} />
      </div>
    </div>
  );
};

export default HomeShowcaseView;
