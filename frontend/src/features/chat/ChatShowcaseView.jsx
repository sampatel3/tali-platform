import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Plus, Trash2, ChevronRight, Search, ArrowUp } from 'lucide-react';

import './chat.css';
import GraphView from './GraphView';
import CandidateGrid from './CandidateGrid';

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
    { id: 'c1', label: 'Company', name: 'Stripe' },
    { id: 'c2', label: 'Company', name: 'Airbnb' },
    { id: 'c3', label: 'Company', name: 'Brex' },
    { id: 'c4', label: 'Company', name: 'Helix Health' },
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
    pipeline_stage: 'Pre-screen',
  },
];

const SHOWCASE_TOOL_PART = {
  type: 'tool_call',
  toolCallId: 'tool_graph_1',
  toolName: 'Candidate search',
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
      "**14 candidates** match. Top hits all have hands-on Postgres in production at a YC-backed company. **Priya Raman** is the strongest signal — Stripe + Helix Health, Postgres-heavy infra ownership.\n\nWant me to compare the top three side-by-side, or open Priya's standing report?",
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

const StaticToolCard = ({ part }) => {
  const [open, setOpen] = useState(false);
  const argsLabel = Object.entries(part.args || {}).map(([k, v]) => (
    <span key={k}>
      <b>{k}=</b>
      {Array.isArray(v) ? `[${v.join(',')}]` : String(v)}{' '}
    </span>
  ));
  const total = part.result?.total_matched ?? part.result?.applications?.length;
  const showing = part.result?.applications?.length;

  return (
    <div className={`cp-tool ${open ? 'cp-tool-open' : ''}`}>
      <button type="button" className="cp-tool-head" onClick={() => setOpen((v) => !v)}>
        <span className="cp-tool-glyph">
          <Search size={13} strokeWidth={2.2} />
        </span>
        <span className="cp-tool-tname">{part.toolName}</span>
        <span className="cp-tool-args">{argsLabel}</span>
        <span className="cp-tool-count">{showing} of {total}</span>
        <ChevronRight size={14} className="cp-tool-chev" />
      </button>
      {open ? (
        <div className="cp-tool-body">
          <div className="cp-tool-kv">
            <div className="k">tool</div>
            <div className="v">{part.toolName}</div>
            <div className="k">args</div>
            <div className="v">
              <pre className="cp-tool-raw">{JSON.stringify(part.args || {}, null, 2)}</pre>
            </div>
            <div className="k">graph</div>
            <div className="v">
              {part.result.graph.nodes.length} nodes · {part.result.graph.edges.length} edges
            </div>
          </div>
        </div>
      ) : null}
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
                    <>
                      <StaticToolCard part={m.tool} />
                      <CandidateGrid rows={m.tool.result.applications} />
                      <GraphView graph={m.tool.result.graph} />
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
