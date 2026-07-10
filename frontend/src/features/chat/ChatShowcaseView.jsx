import React, { Suspense, lazy, useMemo, useState } from 'react';
import { ArrowUp, Sparkles } from 'lucide-react';

import './chat.css';
// The agent-action cards (impact / needs-a-steer) carry their own `ac-*`
// styling here. The live /chat page rides these in globally via Home; the
// standalone showcase iframe must pull them in itself.
import '../home/agentchat/agentchat.css';
// Lazy so cytoscape (~455 kB) stays out of the showcase path until a
// message actually carries a graph payload.
const GraphView = lazy(() => import('./GraphView'));
import CandidateGrid from './CandidateGrid';
import ToolCallCard from './ToolCallCard';
import CandidateEvidenceCard from './CandidateEvidenceCard';
import Sidebar from './Sidebar';
import { ChatMarkdown, ChatMessage } from '../../shared/chat';
import { NeedsInputCard } from '../home/agentchat/cards.jsx';

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
    frontend_url: '/c/demo?view=interview&k=demo-token&showcase=1',
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
// survivors are ranked, and EVERY qualitative verdict is backed by a verbatim
// CV/notes quote — a criterion only reads as satisfied when `grounded` is true.
const SHOWCASE_TOP_RESULT = {
  spec: { echo: 'owned a production GenAI launch · Postgres in production', ranking_key: 'taali' },
  rank_by: 'taali',
  shown: 3,
  total_matched: 14,
  evidence_model: 'claude-sonnet', // truthy → "grounded vs CV + notes"
  excluded: {
    not_met_total: 11,
    by_criterion: [
      { criterion: 'owned a production GenAI launch', count: 8 },
      { criterion: 'Postgres in production', count: 3 },
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
      role_name: 'AI Engineer',
      taali_score: 81,
      meets_all_criteria: true,
      frontend_url: '/c/demo?view=interview&k=demo-token&showcase=1',
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
          status: 'partially_met',
          grounded: true,
          note: 'Uses Postgres through the ORM; no evidence of owning schema design, tuning, or operations.',
          evidence: [
            { quote: 'Built product features against a Postgres-backed Rails monolith.', source: 'cv' },
          ],
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
    text: 'Anyone with a YC company background who knows Postgres? Show me the graph and the top hits.',
  },
  {
    id: 'm2',
    role: 'assistant',
    leadText:
      'Looking across your candidates for anyone who worked at a YC company and has hands-on Postgres.',
    tool: SHOWCASE_TOOL_PART,
    tailText:
      "**14 candidates** match. Top hits all have hands-on Postgres in production at a YC-backed company. **Priya Raman** is the strongest signal — Helix Pay + Caldera Health, Postgres-heavy infra ownership.\n\nWant me to compare the top three side-by-side, or open Priya's standing report?",
  },
  {
    id: 'm3',
    role: 'user',
    text: 'Now just the top 3 who’ve owned a production GenAI launch AND use Postgres in production — and show me the evidence for each.',
  },
  {
    id: 'm4',
    role: 'assistant',
    leadText:
      'Treating both as hard filters and ranking the survivors by Taali fit. Every verdict below is grounded in a verbatim quote from the CV or the candidate’s notes — anything I can’t cite, I mark unverified rather than assume.',
    tool: SHOWCASE_TOP_PART,
    tailText:
      '**Priya Raman** is the only one who clears both cleanly. Daniel owns Postgres deeply but didn’t own the launch gate; Maya owns the launch but her Postgres is ORM-only. **11 candidates dropped** for failing a must-have — they’re listed in the shareable report so you can audit every cut.',
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
  evidence_model: 'claude-sonnet',
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
      frontend_url: '/c/demo?view=interview&k=demo-token&showcase=1',
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
// surface can't do. Rendered by the live <NeedsInputCard>.
const AGENT_NEEDS_INPUT = {
  kind: 'needs_input',
  needs_input_id: 'ni_invite',
  prompt:
    'Priya and Daniel clear all three must-haves. Want me to invite both to the assessment now, or widen the net to the top 5 so you can compare first?',
  options: [
    { value: 'invite', label: 'Invite both to assessment' },
    { value: 'widen', label: 'Widen to top 5' },
  ],
};

const AGENT_USER_TEXT =
  'Give me the top 3 for this role who’ve (1) owned a production GenAI launch, (2) run Postgres in production, and (3) led a backend team of 3+. Show me the evidence for each.';

const AGENT_LEAD_TEXT =
  'Treating all three as hard filters and ranking the survivors by Taali fit. Every verdict below is grounded in a verbatim quote from the CV or the candidate’s notes — anything I can’t cite, I mark unverified rather than assume.';

const AGENT_TAIL_TEXT =
  '**Priya** and **Daniel** clear all three cleanly. **Maya** owns the launch and led a pod, but her Postgres is ORM-only — flagged above. **11 candidates dropped** for failing a must-have; they’re in the shareable report so you can audit every cut.';

const noop = () => {};

// Inert composer — the preview never sends, so we render the static box rather
// than the live <ChatComposer> (whose autosize needs a mounted, settled layout).
// Same markup the live surface's composer compiles to.
const ShowcaseComposer = ({ placeholder }) => (
  <div className="cp-composer-wrap">
    <form className="cp-composer" onSubmit={(e) => e.preventDefault()}>
      <textarea rows={1} placeholder={placeholder} defaultValue="" />
      <div className="cp-composer-foot">
        <span>
          press <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for newline
        </span>
        <button type="button" className="cp-send-btn" disabled>
          <ArrowUp size={13} /> send
        </button>
      </div>
    </form>
  </div>
);

// ─────────────────────────────────────────────────────────────── Surfaces ──

const AskCenter = () => (
  <div className="cp-center">
    <header className="cp-head">
      <div className="cp-head-ttl">
        YC company background + Postgres
        <span className="sub">Taali</span>
      </div>
      <div className="cp-head-grow" />
      <span className="cp-head-pill">
        <span className="cp-pill-glyph">▮</span>
        14 tools connected
      </span>
    </header>
    <div className="cp-scroll">
      <div className="cp-thread">
        {SHOWCASE_MESSAGES.map((m) => {
          if (m.role === 'user') {
            return <ChatMessage key={m.id} role="user" text={m.text} />;
          }
          return (
            <ChatMessage key={m.id} role="assistant" text={m.leadText}>
              {m.tool ? (
                // Mirrors the live Thread's ToolResultRender: find_top_candidates
                // → the grounded evidence card; the search tools → grid + graph.
                <>
                  <ToolCallCard part={m.tool} />
                  {m.tool.toolName === 'find_top_candidates' ? (
                    <CandidateEvidenceCard data={m.tool.result} />
                  ) : (
                    <>
                      <CandidateGrid rows={m.tool.result.applications} />
                      {m.tool.result.graph ? (
                        <Suspense fallback={null}>
                          <GraphView graph={m.tool.result.graph} />
                        </Suspense>
                      ) : null}
                    </>
                  )}
                </>
              ) : null}
              {m.tailText ? <ChatMarkdown>{m.tailText}</ChatMarkdown> : null}
            </ChatMessage>
          );
        })}
      </div>
    </div>
    <ShowcaseComposer placeholder="Ask anything about your candidates…" />
  </div>
);

const AgentCenter = () => (
  <div className="cp-center">
    <header className="cp-head">
      <div className="cp-head-ttl">
        Senior Backend Engineer
        <span className="sub">Agent</span>
      </div>
      <div className="cp-head-grow" />
      <span className="cp-head-pill cp-head-pill-on">
        <span className="cp-pill-glyph"><Sparkles size={11} /></span>
        Agent on
      </span>
    </header>
    <div className="cp-scroll">
      <div className="cp-thread">
        <ChatMessage role="user" text={AGENT_USER_TEXT} />
        <ChatMessage role="assistant" text={AGENT_LEAD_TEXT}>
          <CandidateEvidenceCard data={AGENT_TOP_RESULT} />
          <ChatMarkdown>{AGENT_TAIL_TEXT}</ChatMarkdown>
        </ChatMessage>
        <NeedsInputCard item={AGENT_NEEDS_INPUT} onAnswer={noop} onDismiss={noop} />
      </div>
    </div>
    <ShowcaseComposer placeholder="Ask about this role’s pool, or tell the agent to change something…" />
  </div>
);

export const ChatShowcaseView = () => {
  // Default to Agents so the preview opens on the per-role agent returning a
  // grounded top-N — the toggle reveals the Ask (search) surface alongside it.
  const [mode, setMode] = useState('agents');

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
      {mode === 'agents' ? <AgentCenter /> : <AskCenter />}
    </div>
  );
};

export default ChatShowcaseView;
