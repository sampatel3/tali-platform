import React from 'react';
import ReactMarkdown from 'react-markdown';
import { Plus, Trash2, ArrowUp } from 'lucide-react';

import './chat.css';
import GraphView from './GraphView';
import CandidateGrid from './CandidateGrid';
import ToolCallCard from './ToolCallCard';
import CandidateEvidenceCard from './CandidateEvidenceCard';

const SHOWCASE_CONVERSATIONS = [
  {
    id: 1,
    title: 'YC company background + Postgres',
    message_count: 8,
    updated_at: new Date(Date.now() - 6 * 60_000).toISOString(),
    bucket: 'today',
    active: true,
  },
  {
    id: 2,
    title: 'Compare top three for Senior Backend',
    message_count: 12,
    updated_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
    bucket: 'today',
  },
  {
    id: 3,
    title: 'Pipeline review · this week',
    message_count: 6,
    updated_at: new Date(Date.now() - 26 * 60 * 60_000).toISOString(),
    bucket: 'yesterday',
  },
  {
    id: 4,
    title: 'Reject reasons audit · Q1 cohort',
    message_count: 18,
    updated_at: new Date(Date.now() - 5 * 86_400_000).toISOString(),
    bucket: 'week',
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
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-20T08:14:00.000Z', model: 'Sonnet' } },
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
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-22T13:05:00.000Z', model: 'Sonnet' } },
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
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-23T09:40:00.000Z', model: 'Sonnet' } },
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
    score_summary: { score_provenance: { engine_version: '2.1.0', scored_at: '2026-04-24T16:12:00.000Z', model: 'Sonnet' } },
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
// from find_top_candidates. The flagship chat surface: criteria are treated as
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

const SidebarConversationGroup = ({ label, rows }) => {
  if (!rows.length) return null;
  return (
    <div className="cp-group">
      <div className="cp-group-h">{label}</div>
      {rows.map((r) => (
        <div key={r.id} style={{ position: 'relative' }}>
          <button
            type="button"
            className={`cp-conv ${r.active ? 'cp-active' : ''}`}
          >
            <div className="cp-conv-q">{r.title}</div>
            <div className="cp-conv-meta">
              {r.message_count} msg ·{' '}
              {new Date(r.updated_at).toLocaleString(undefined, {
                hour: 'numeric',
                minute: '2-digit',
              })}
            </div>
          </button>
          <button
            type="button"
            title="Delete conversation"
            style={{
              position: 'absolute',
              right: 8,
              top: 8,
              background: 'transparent',
              color: 'var(--c-mute-2)',
              padding: 4,
              borderRadius: 6,
            }}
          >
            <Trash2 size={13} />
          </button>
        </div>
      ))}
    </div>
  );
};

export const ChatShowcaseView = () => {
  const buckets = SHOWCASE_CONVERSATIONS.reduce(
    (acc, conv) => {
      acc[conv.bucket] = acc[conv.bucket] || [];
      acc[conv.bucket].push(conv);
      return acc;
    },
    {},
  );

  return (
    <div className="cp-root">
      <aside className="cp-side">
        <div className="cp-side-head">
          <span className="cp-side-title">Conversations</span>
          <button type="button" className="cp-new-chat">
            <span className="cp-plus">
              <Plus size={11} strokeWidth={3} />
            </span>
            New conversation
          </button>
        </div>
        <div className="cp-side-list">
          <SidebarConversationGroup label="Today" rows={buckets.today || []} />
          <SidebarConversationGroup label="Yesterday" rows={buckets.yesterday || []} />
          <SidebarConversationGroup label="This week" rows={buckets.week || []} />
        </div>
      </aside>

      <div className="cp-center">
        <header className="cp-head">
          <div className="cp-head-ttl">
            YC company background + Postgres
            <span className="sub">Taali</span>
          </div>
          <div className="cp-head-grow" />
          <span className="cp-head-pill">
            <span className="cp-pill-glyph">▮</span>
            Live across your candidates
          </span>
        </header>
        <div className="cp-scroll">
          <div className="cp-thread">
            {SHOWCASE_MESSAGES.map((m) => {
              if (m.role === 'user') {
                return <div key={m.id} className="cp-msg-user">{m.text}</div>;
              }
              return (
                <div key={m.id} className="cp-msg-assistant">
                  {m.leadText ? (
                    <div>
                      <ReactMarkdown>{m.leadText}</ReactMarkdown>
                    </div>
                  ) : null}
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
                          {m.tool.result.graph ? <GraphView graph={m.tool.result.graph} /> : null}
                        </>
                      )}
                    </>
                  ) : null}
                  {m.tailText ? (
                    <div>
                      <ReactMarkdown>{m.tailText}</ReactMarkdown>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
        <div className="cp-composer-wrap">
          <form className="cp-composer" onSubmit={(e) => e.preventDefault()}>
            <textarea
              rows={1}
              placeholder="Ask anything about your candidates…"
              defaultValue=""
            />
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
      </div>
    </div>
  );
};

export default ChatShowcaseView;
