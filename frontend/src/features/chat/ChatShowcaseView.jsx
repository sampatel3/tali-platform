import React, { useId, useMemo, useState } from 'react';
import { CircleAlert, MessageSquare } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';

import './chat.css';
import CandidateEvidenceCard from './CandidateEvidenceCard';
import Sidebar from './Sidebar';
import Thread from './Thread';
import {
  AgentFeedTimeline,
  AgentStreamTabs,
  ChatActivity,
  ChatComposer,
  ChatSurface,
  RoleAgentTimeline,
  agentFeedAttentionCount,
  splitAgentTimeline,
} from '../../shared/chat';

// ChatShowcaseView — the step-05 "locked preview" embedded by DemoShowcasePage.
// It mirrors the REAL two-mode Chat surface 1:1: the same <Sidebar> with its
// Ask ↔ Agents toggle, the same message bubbles, the same grounded evidence
// card, and the same per-role agent action cards. Everything is static seed
// data — no backend — but every component is the live one, so the demo can't
// drift from the product.
//
//   • Ask     — plain-English search across the whole pipeline (tool calls
//               visible → candidate grid + knowledge graph, and the grounded
//               find_top_candidates evidence card).
//   • Agents  — one role's autonomous agent: ask it for the top N who meet
//               X, Y and Z, and it returns the same grounded evidence AND
//               offers to act on it (invite the matches, widen the net).

// ─────────────────────────────────────────────────────────── Ask (search) ──

const SHOWCASE_CONVERSATIONS = [
  {
    id: 1,
    title: 'YC company background + Postgres',
    message_count: 8,
    updated_at: new Date(Date.now() - 6 * 60_000).toISOString(),
  },
  {
    id: 2,
    title: 'Compare top three for Senior Backend',
    message_count: 12,
    updated_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
  },
  {
    id: 3,
    title: 'Pipeline review · this week',
    message_count: 6,
    updated_at: new Date(Date.now() - 26 * 60 * 60_000).toISOString(),
  },
  {
    id: 4,
    title: 'Reject reasons audit · Q1 cohort',
    message_count: 18,
    updated_at: new Date(Date.now() - 5 * 86_400_000).toISOString(),
  },
];

const SHOWCASE_GRAPH = {
  nodes: [
    { id: 'p1', label: 'Person', name: 'Priya Raman' },
    { id: 'p2', label: 'Person', name: 'Daniel Okafor' },
    { id: 'p3', label: 'Person', name: 'Maya Chen' },
    { id: 'p4', label: 'Person', name: 'Tomás Alvarez' },
    { id: 'c1', label: 'Company', name: 'Helix Pay' },
    { id: 'c2', label: 'Company', name: 'Wander' },
    { id: 'c3', label: 'Company', name: 'Ledger' },
    { id: 'c4', label: 'Company', name: 'Caldera Health' },
    { id: 's1', label: 'Skill', name: 'Postgres' },
    { id: 's2', label: 'Skill', name: 'Python' },
    { id: 's3', label: 'Skill', name: 'Distributed systems' },
    { id: 'sch1', label: 'School', name: 'IIT Bombay' },
    { id: 'sch2', label: 'School', name: 'UC Berkeley' },
  ],
  edges: [
    { source: 'p1', target: 'c4', label: 'WORKED_AT' },
    { source: 'p1', target: 'c1', label: 'WORKED_AT' },
    { source: 'p1', target: 's1', label: 'HAS_SKILL' },
    { source: 'p1', target: 's2', label: 'HAS_SKILL' },
    { source: 'p1', target: 'sch1', label: 'STUDIED_AT' },
    { source: 'p2', target: 'c2', label: 'WORKED_AT' },
    { source: 'p2', target: 'c3', label: 'WORKED_AT' },
    { source: 'p2', target: 's1', label: 'HAS_SKILL' },
    { source: 'p2', target: 's3', label: 'HAS_SKILL' },
    { source: 'p3', target: 'c1', label: 'WORKED_AT' },
    { source: 'p3', target: 's1', label: 'HAS_SKILL' },
    { source: 'p3', target: 'sch2', label: 'STUDIED_AT' },
    { source: 'p4', target: 'c3', label: 'WORKED_AT' },
    { source: 'p4', target: 's2', label: 'HAS_SKILL' },
    { source: 'p4', target: 's3', label: 'HAS_SKILL' },
  ],
};

const SHOWCASE_APPLICATIONS = [
  {
    application_id: 'app_priya',
    candidate_name: 'Priya Raman',
    candidate_position: 'Senior AI Engineer',
    candidate_location: 'London, UK',
    role_name: 'AI Engineer',
    taali_score: 81,
    pre_screen_score: 84,
    // Provenance pill under the score in the candidate grid (current engine).
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-20T08:14:00.000Z' } },
    pipeline_stage: 'Onsite',
    frontend_url: '/c/demo?view=interview&showcase=1',
  },
  {
    application_id: 'app_daniel',
    candidate_name: 'Daniel Okafor',
    candidate_position: 'Staff Engineer',
    candidate_location: 'Lagos, NG',
    role_name: 'Senior Backend',
    taali_score: 76,
    pre_screen_score: 79,
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-22T13:05:00.000Z' } },
    pipeline_stage: 'Review',
  },
  {
    application_id: 'app_maya',
    candidate_name: 'Maya Chen',
    candidate_position: 'Senior Engineer',
    candidate_location: 'San Francisco, US',
    role_name: 'AI Engineer',
    taali_score: 73,
    pre_screen_score: 71,
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-23T09:40:00.000Z' } },
    pipeline_stage: 'Review',
  },
  {
    application_id: 'app_tomas',
    candidate_name: 'Tomás Alvarez',
    candidate_position: 'Backend Engineer',
    candidate_location: 'Madrid, ES',
    role_name: 'Senior Backend',
    taali_score: 68,
    pre_screen_score: 70,
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-24T16:12:00.000Z' } },
    pipeline_stage: 'Pre-screen',
  },
];

const SHOWCASE_TOOL_PART = {
  type: 'tool_call',
  toolCallId: 'tool_graph_1',
  toolName: 'graph_search_candidates',
  args: {
    query: 'YC company background AND Postgres expertise',
    limit: 25,
  },
  status: 'complete',
  result: {
    applications: SHOWCASE_APPLICATIONS,
    total_matched: 14,
    graph: SHOWCASE_GRAPH,
  },
};

// Grounded "top N with X and Y" result the live <CandidateEvidenceCard> renders
// from find_top_candidates. The flagship Ask surface: criteria are treated as
// hard filters (candidates who fail a must-have are hidden + counted), the
// survivors are ranked, and every completed qualitative verdict is backed by
// a verbatim CV/notes quote. An interrupted check stays visibly unverified.
const SHOWCASE_TOP_RESULT = {
  spec: { echo: 'owned a production GenAI launch · Postgres in production', ranking_key: 'taali' },
  rank_by: 'taali',
  shown: 3,
  total_matched: 14,
  database_matches: 14,
  criteria_requested: [
    'owned a production GenAI launch',
    'Postgres in production',
  ],
  criteria_checked: [
    'owned a production GenAI launch',
    'Postgres in production',
  ],
  criteria_unchecked: [],
  deep_checked: 14,
  evidence_succeeded: 13,
  evidence_failed: 1,
  qualified: 1,
  capped: false,
  evidence_model: 'claude-sonnet', // truthy → "grounded vs CV + notes"
  report_url: '#partial-grounded-report',
  excluded: {
    not_met_total: 11,
    by_criterion: [
      { criterion: 'owned a production GenAI launch', count: 8 },
      { criterion: 'Postgres in production', count: 3 },
    ],
  },
  warnings: [{
    code: 'evidence_partial',
    message: '1 of 14 evidence checks did not complete; Maya remains unverified for Postgres ownership.',
  }],
  candidates: [
    {
      application_id: 'app_priya',
      rank: 1,
      candidate_name: 'Priya Raman',
      candidate_position: 'Senior AI Engineer',
      candidate_location: 'London, UK',
      role_name: 'AI Engineer',
      taali_score: 81,
      meets_all_criteria: true,
      frontend_url: '/c/demo?view=interview&showcase=1',
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Led the patient-summarisation GenAI rollout across two NHS trusts at Helix Health — owned the offline eval harness, retrieval grounding, and the release gate end to end.', source: 'cv' },
          ],
        },
        {
          criterion: 'Postgres in production',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Designed the Postgres schema and partitioning for the clinical audit store (≈14M rows/day) and the row-level-security policies the launch gate required.', source: 'cv' },
          ],
        },
      ],
    },
    {
      application_id: 'app_daniel',
      rank: 2,
      candidate_name: 'Daniel Okafor',
      candidate_position: 'Staff Engineer',
      candidate_location: 'Lagos, NG',
      role_name: 'Senior Backend',
      taali_score: 76,
      meets_all_criteria: false,
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'partially_met',
          grounded: true,
          note: 'Shipped a GenAI feature but did not own the launch gate — release sign-off sat with the platform lead.',
          evidence: [
            { quote: 'Built the retrieval-augmented support assistant at Ledger and shipped it to 40k users behind a feature flag.', source: 'cv' },
            { quote: 'I owned the embedding pipeline and prompt templates; the go/no-go decision was the platform lead’s.', source: 'notes' },
          ],
        },
        {
          criterion: 'Postgres in production',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Ran the primary Postgres fleet at Wander — logical replication, PITR, and a zero-downtime major-version upgrade.', source: 'cv' },
          ],
        },
      ],
    },
    {
      application_id: 'app_maya',
      rank: 3,
      candidate_name: 'Maya Chen',
      candidate_position: 'Senior Engineer',
      candidate_location: 'San Francisco, US',
      role_name: 'AI Engineer',
      taali_score: 73,
      meets_all_criteria: false,
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Took the Helix Pay fraud-explanations LLM feature from prototype to GA; defined the offline eval set and the human-review fallback.', source: 'cv' },
          ],
        },
        {
          criterion: 'Postgres in production',
          status: 'error',
          grounded: false,
          note: 'The evidence check did not complete, so no verdict was inferred.',
          evidence: [],
        },
      ],
    },
  ],
};

const SHOWCASE_TOP_PART = {
  type: 'tool_call',
  toolCallId: 'tool_top_1',
  toolName: 'find_top_candidates',
  args: { criteria: ['owned a production GenAI launch', 'Postgres in production'], limit: 3, rank_by: 'taali' },
  status: 'complete',
  result: SHOWCASE_TOP_RESULT,
};

const SHOWCASE_MESSAGES = [
  {
    id: 'm1',
    role: 'user',
    parts: [{
      type: 'text',
      text: 'Anyone with a YC company background who knows Postgres? Show me the graph and the top hits.',
    }],
  },
  {
    id: 'm2',
    role: 'assistant',
    parts: [
      {
        type: 'text',
        text: 'Looking across your candidates for anyone who worked at a YC company and has hands-on Postgres.',
      },
      SHOWCASE_TOOL_PART,
      {
        type: 'text',
        text: "**14 candidates** match. Top hits all have hands-on Postgres in production at a YC-backed company. **Priya Raman** is the strongest signal — Helix Pay + Caldera Health, Postgres-heavy infra ownership.\n\nWant me to compare the top three side-by-side, or open Priya's standing report?",
      },
    ],
  },
  {
    id: 'm3',
    role: 'user',
    parts: [{
      type: 'text',
      text: 'Now just the top 3 who’ve owned a production GenAI launch AND use Postgres in production — and show me the evidence for each.',
    }],
  },
  {
    id: 'm4',
    role: 'assistant',
    parts: [
      {
        type: 'text',
        text: 'Treating both as hard filters and ranking the survivors by Taali fit. Evidence-backed verdicts quote the CV or candidate notes; anything the verifier could not finish stays unverified rather than becoming an inferred pass or fail.',
      },
      SHOWCASE_TOP_PART,
      {
        type: 'text',
        text: '**Priya Raman** is the only candidate who clears both cleanly. Daniel owns Postgres deeply but did not own the launch gate. Maya’s Postgres check did not complete, so she stays unverified. The partially grounded report keeps that gap visible for audit.',
      },
    ],
  },
];

// ──────────────────────────────────────────────────────── Agents (per role) ──

// The agent list the real <Sidebar> renders in Agents mode — same shape the
// Home dock + /chat sidebar poll (role_id, group, agent_enabled, attention).
const SHOWCASE_AGENTS = [
  {
    role_id: 1,
    role_name: 'Senior Backend Engineer',
    group: 'on_paused',
    agent_enabled: true,
    agent_paused: false,
    last_message_preview: 'Ranked your top 3 against all 3 must-haves',
    unread_messages: 0,
    open_questions: 1,
  },
  {
    role_id: 2,
    role_name: 'AI Engineer',
    group: 'on_paused',
    agent_enabled: true,
    agent_paused: false,
    last_message_preview: '2 candidates cleared every criterion',
    unread_messages: 1,
    open_questions: 0,
  },
  {
    role_id: 3,
    role_name: 'Staff Platform Engineer',
    group: 'on_paused',
    agent_enabled: true,
    agent_paused: true,
    agent_paused_reason: 'monthly budget reached',
    last_message_preview: '',
  },
  {
    role_id: 4,
    role_name: 'Data Engineer',
    group: 'previously_on',
    agent_enabled: false,
    last_message_preview: 'Agent off — tap to set up',
  },
  {
    role_id: 5,
    role_name: 'Frontend Engineer',
    group: 'active',
    agent_enabled: false,
  },
];

const AGENT_ACTIVE_ROLE_ID = 1;

// The grounded top-N the agent returns — the SAME <CandidateEvidenceCard> shape
// the Ask surface uses, but here it's the agent's `candidate_evidence` action,
// scoped to this one role and driven by THREE must-haves (X, Y, Z).
const AGENT_TOP_RESULT = {
  type: 'candidate_evidence',
  spec: {
    echo: 'owned a production GenAI launch · Postgres in production · led a backend team of 3+',
    ranking_key: 'taali',
  },
  rank_by: 'taali',
  shown: 3,
  total_matched: 14,
  database_matches: 14,
  criteria_requested: [
    'owned a production GenAI launch',
    'Postgres in production',
    'led a backend team of 3+',
  ],
  criteria_checked: [
    'owned a production GenAI launch',
    'Postgres in production',
    'led a backend team of 3+',
  ],
  criteria_unchecked: [],
  deep_checked: 14,
  evidence_succeeded: 14,
  qualified: 2,
  capped: false,
  evidence_model: 'claude-sonnet',
  // Fixture-only target: keep the live report affordance visible in the
  // auth-free showcase without pretending a persisted report token exists.
  report_url: '#grounded-report',
  excluded: {
    not_met_total: 11,
    by_criterion: [
      { criterion: 'owned a production GenAI launch', count: 7 },
      { criterion: 'Postgres in production', count: 2 },
      { criterion: 'led a backend team of 3+', count: 2 },
    ],
  },
  warnings: [],
  candidates: [
    {
      application_id: 'app_priya',
      rank: 1,
      candidate_name: 'Priya Raman',
      candidate_position: 'Senior AI Engineer',
      candidate_location: 'London, UK',
      role_name: 'Senior Backend Engineer',
      taali_score: 81,
      meets_all_criteria: true,
      frontend_url: '/c/demo?view=interview&showcase=1',
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Led the patient-summarisation GenAI rollout across two NHS trusts at Helix Health — owned the offline eval harness, retrieval grounding, and the release gate end to end.', source: 'cv' },
          ],
        },
        {
          criterion: 'Postgres in production',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Designed the Postgres schema and partitioning for the clinical audit store (≈14M rows/day) and the row-level-security policies the launch gate required.', source: 'cv' },
          ],
        },
        {
          criterion: 'led a backend team of 3+',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Managed a 5-engineer backend team at Helix Health — owned hiring, the on-call rotation, and the quarterly roadmap.', source: 'cv' },
          ],
        },
      ],
    },
    {
      application_id: 'app_daniel',
      rank: 2,
      candidate_name: 'Daniel Okafor',
      candidate_position: 'Staff Engineer',
      candidate_location: 'Lagos, NG',
      role_name: 'Senior Backend Engineer',
      taali_score: 76,
      meets_all_criteria: true,
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Took the Ledger support assistant from prototype to GA for 40k users — owned the eval set, the rollout flags, and the final go/no-go gate.', source: 'cv' },
          ],
        },
        {
          criterion: 'Postgres in production',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Ran the primary Postgres fleet at Wander — logical replication, PITR, and a zero-downtime major-version upgrade.', source: 'cv' },
          ],
        },
        {
          criterion: 'led a backend team of 3+',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Led a 4-person platform squad at Wander for two years — set the architecture and ran sprint planning.', source: 'notes' },
          ],
        },
      ],
    },
    {
      application_id: 'app_maya',
      rank: 3,
      candidate_name: 'Maya Chen',
      candidate_position: 'Senior Engineer',
      candidate_location: 'San Francisco, US',
      role_name: 'Senior Backend Engineer',
      taali_score: 73,
      meets_all_criteria: false,
      criteria: [
        {
          criterion: 'owned a production GenAI launch',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Took the Helix Pay fraud-explanations LLM feature from prototype to GA; defined the offline eval set and the human-review fallback.', source: 'cv' },
          ],
        },
        {
          criterion: 'Postgres in production',
          status: 'partially_met',
          grounded: true,
          note: 'Uses Postgres through the ORM; no evidence of owning schema design, tuning, or operations.',
          evidence: [
            { quote: 'Built product features against a Postgres-backed Rails monolith.', source: 'cv' },
          ],
        },
        {
          criterion: 'led a backend team of 3+',
          status: 'met',
          grounded: true,
          evidence: [
            { quote: 'Tech lead for a 3-engineer growth pod at Helix Pay — owned delivery and mentoring.', source: 'cv' },
          ],
        },
      ],
    },
  ],
};

// After the grounded answer, the agent offers to ACT on it — the thing the Ask
// surface can't do. Rendered by the live <AgentPromptCard>.
const AGENT_NEEDS_INPUT = {
  id: 'ni_invite',
  kind: 'needs_input',
  needs_input_id: 'ni_invite',
  status: 'open',
  prompt:
    'Priya and Daniel clear all three must-haves. Want me to invite both to the assessment now, or widen the net to the top 5 so you can compare first?',
  options: [
    { value: 'invite', label: 'Invite both to assessment' },
    { value: 'widen', label: 'Widen to top 5' },
  ],
};

// Autonomous activity belongs in Agent Feed, never in the conversational
// transcript. These use the same durable timeline shapes as the live page so
// the showcase demonstrates the real routing and disclosure behaviour.
const AGENT_RUN_ERROR = {
  id: 'agent-run-error',
  kind: 'message',
  author: 'agent',
  message_kind: 'event',
  created_at: '2026-07-15T18:43:00Z',
  actions: [{
    type: 'agent_event',
    severity: 'error',
    event_type: 'agent_run_terminal',
    title: 'Agent run stopped before completion',
    summary: 'The cycle ended early. Six decisions were retained and unfinished work can retry safely.',
    occurred_at: '2026-07-15T18:43:00Z',
    details: [
      { label: 'Agent run', value: '#7042' },
      { label: 'Work retained', value: '6 decisions' },
    ],
    suggestions: [
      {
        label: 'Explain stop',
        prompt: 'Explain why agent run #7042 stopped and what is safe to retry.',
      },
      {
        label: 'Preview retry',
        prompt: 'Preview the unfinished work from agent run #7042 before retrying it.',
      },
    ],
  }],
};

const AGENT_CANDIDATE_DECISION = {
  id: 'agent-decision-maya',
  kind: 'decision',
  decision_id: 7421,
  role_id: AGENT_ACTIVE_ROLE_ID,
  candidate_name: 'Maya Chen',
  recommendation: 'advance',
  score: 73,
  status: 'pending',
  reasoning: 'Maya clears two must-haves and has one explicitly grounded Postgres risk to review.',
  created_at: '2026-07-15T18:41:00Z',
};

const AGENT_USER_TEXT =
  'Give me the top 3 for this role who’ve (1) owned a production GenAI launch, (2) run Postgres in production, and (3) led a backend team of 3+. Show me the evidence for each.';

const AGENT_LEAD_TEXT =
  'Treating all three as hard filters and ranking the survivors by Taali fit. Every verdict below is grounded in a verbatim quote from the CV or the candidate’s notes — anything I can’t cite, I mark unverified rather than assume.';

const AGENT_TAIL_TEXT =
  '**Priya** and **Daniel** clear all three cleanly. **Maya** owns the launch and led a pod, but her Postgres is ORM-only — flagged above. **11 candidates dropped** for failing a must-have; they’re in the shareable report so you can audit every cut.';

const AGENT_TIMELINE = [
  {
    id: 'agent-user-top-three',
    kind: 'message',
    author: 'recruiter',
    text: AGENT_USER_TEXT,
    created_at: '2026-07-15T18:40:00Z',
  },
  {
    id: 'agent-grounded-answer',
    kind: 'message',
    author: 'agent',
    message_kind: 'chat',
    text: AGENT_LEAD_TEXT,
    actions: [AGENT_TOP_RESULT],
    created_at: '2026-07-15T18:40:08Z',
  },
  {
    id: 'agent-grounded-conclusion',
    kind: 'message',
    author: 'agent',
    message_kind: 'chat',
    text: AGENT_TAIL_TEXT,
    created_at: '2026-07-15T18:40:09Z',
  },
  {
    ...AGENT_NEEDS_INPUT,
    created_at: '2026-07-15T18:40:10Z',
  },
  AGENT_CANDIDATE_DECISION,
  AGENT_RUN_ERROR,
];

const AGENT_QUESTION_POSITIONS = new Map([['ni_invite', 1]]);

const noop = () => {};

const ShowcaseComposer = ({
  placeholder,
  value: controlledValue,
  onChange: controlledOnChange,
  onSubmit = noop,
}) => {
  const [localValue, setLocalValue] = useState('');
  const controlled = controlledValue !== undefined;
  const value = controlled ? controlledValue : localValue;
  const setValue = controlled ? controlledOnChange : setLocalValue;
  return (
    <div className="cp-composer-wrap">
      <ChatComposer value={value} onChange={setValue} onSubmit={onSubmit} placeholder={placeholder} />
    </div>
  );
};

// ─────────────────────────────────────────────────────────────── Surfaces ──

const AskCenter = () => (
  <ChatSurface className="cp-center" density="comfortable">
    <header className="cp-head">
      <div className="cp-head-titles">
        <div className="cp-head-ttl">YC company background + Postgres</div>
        <div className="cp-head-sub">Taali</div>
      </div>
      <div className="cp-head-grow" />
      <span className="cp-head-pill">
        <span className="cp-pill-glyph">▮</span>
        14 tools connected
      </span>
    </header>
    <div className="cp-scroll">
      <Thread
        messages={SHOWCASE_MESSAGES}
        isStreaming={false}
        error={null}
      />
    </div>
    <ShowcaseComposer placeholder="Ask anything about your candidates…" />
  </ChatSurface>
);

const AgentCenter = ({ streamView, onStreamChange }) => {
  const chatPanelId = useId();
  const feedPanelId = useId();
  const [composerValue, setComposerValue] = useState('');
  const { conversation: conversationItems, feed: feedItems } = useMemo(
    () => splitAgentTimeline(AGENT_TIMELINE),
    [],
  );
  const feedAttention = useMemo(() => agentFeedAttentionCount(feedItems), [feedItems]);
  const prefillFromFeed = (prompt) => {
    setComposerValue(String(prompt || '').trim());
    onStreamChange('chat');
  };
  const submitComposer = () => {
    setComposerValue('');
    onStreamChange('chat');
  };
  const renderAction = (card, _actionIndex, _item, options = {}) => {
    if (card.type === 'candidate_evidence') return <CandidateEvidenceCard data={card} />;
    if (card.type !== 'agent_event') return null;
    return (
      <ChatActivity
        severity={card.severity || 'info'}
        severityLabel={card.severity === 'error' ? 'Error' : 'Update'}
        typeLabel="Agent run"
        title={card.title}
        summary={card.summary}
        icon={CircleAlert}
        details={card.details}
        detailOnly={Boolean(options.detailOnly)}
        actions={(card.suggestions || []).map((suggestion) => ({
          label: suggestion.label,
          onClick: () => prefillFromFeed(suggestion.prompt),
        }))}
      />
    );
  };

  return (
    <ChatSurface className="cp-center" density="comfortable" tone="agent">
      <header className="cp-head">
        <span className="cp-head-lead"><MessageSquare size={15} /> Ask the agent</span>
        <span className="cp-head-role">Senior Backend Engineer</span>
      </header>

      <AgentStreamTabs
        value={streamView}
        onChange={onStreamChange}
        attentionCount={feedAttention}
        chatPanelId={chatPanelId}
        feedPanelId={feedPanelId}
      />

      <div className="cp-scroll-stack">
        <div
          className="cp-scroll"
          id={chatPanelId}
          role="tabpanel"
          aria-label="Chat"
          hidden={streamView !== 'chat'}
        >
          <RoleAgentTimeline
            items={conversationItems}
            className="cp-thread"
            roleId={AGENT_ACTIVE_ROLE_ID}
            roleName="Senior Backend Engineer"
            renderAction={renderAction}
          />
        </div>

        <div
          className="cp-scroll cp-feed-scroll"
          id={feedPanelId}
          role="tabpanel"
          aria-label="Agent feed"
          hidden={streamView !== 'feed'}
        >
          <AgentFeedTimeline
            items={feedItems}
            roleId={AGENT_ACTIVE_ROLE_ID}
            roleName="Senior Backend Engineer"
            openQuestionPositions={AGENT_QUESTION_POSITIONS}
            openQuestionCount={1}
            onAnswer={noop}
            onDismiss={noop}
            onPrompt={prefillFromFeed}
            renderAction={renderAction}
          />
        </div>
      </div>

      <ShowcaseComposer
        value={composerValue}
        onChange={setComposerValue}
        onSubmit={submitComposer}
        placeholder="Ask about this role’s pool, or tell the agent to change something…"
      />
    </ChatSurface>
  );
};

export const ChatShowcaseView = () => {
  // Default to Agents so the preview opens on the per-role agent returning a
  // grounded top-N. A query-owned mode keeps both surfaces directly linkable
  // for product review and deterministic browser screenshots.
  const [searchParams, setSearchParams] = useSearchParams();
  const mode = searchParams.get('mode') === 'ask' ? 'ask' : 'agents';
  const streamView = searchParams.get('stream') === 'feed' ? 'feed' : 'chat';
  const setMode = (nextMode) => {
    const next = new URLSearchParams(searchParams);
    next.set('mode', nextMode === 'ask' ? 'ask' : 'agents');
    setSearchParams(next, { replace: true });
  };
  const setStreamView = (nextStream) => {
    const next = new URLSearchParams(searchParams);
    next.set('stream', nextStream === 'feed' ? 'feed' : 'chat');
    setSearchParams(next, { replace: true });
  };

  const agentAttention = useMemo(
    () =>
      SHOWCASE_AGENTS.reduce(
        (sum, a) => sum + (a.unread_messages || 0) + (a.open_questions || 0),
        0,
      ),
    [],
  );

  return (
    <div className="cp-root">
      <Sidebar
        mode={mode}
        onModeChange={setMode}
        conversations={SHOWCASE_CONVERSATIONS}
        activeId={1}
        onNew={noop}
        onSelect={noop}
        onDelete={noop}
        agents={SHOWCASE_AGENTS}
        activeRoleId={AGENT_ACTIVE_ROLE_ID}
        onSelectAgent={noop}
        agentAttention={agentAttention}
      />
      {mode === 'agents' ? (
        <AgentCenter streamView={streamView} onStreamChange={setStreamView} />
      ) : <AskCenter />}
    </div>
  );
};

export default ChatShowcaseView;
