import React, { useState } from 'react';

import { TaaliTile } from '../../shared/ui/Branding';

// DemoShowcasePage — v4 redesign (HANDOFF chat.md §1).
// Sells AI-first: 5-tab walkthrough leading with the agent, then assessment,
// scoring, plain-English chat, workflow/share.

const SHOWCASE_TABS = [
  { k: 'agent', n: '01', label: 'Agentic triage', sub: 'The agent that runs your top of funnel' },
  { k: 'assessment', n: '02', label: 'AI assessment', sub: 'See how candidates pair with AI' },
  { k: 'scoring', n: '03', label: 'Six-axis scoring', sub: 'Evidence-linked, calibrated to your bar' },
  { k: 'chat', n: '04', label: 'Plain-English chat', sub: 'Query your pipeline in english' },
  { k: 'workflow', n: '05', label: 'Workflow & share', sub: 'From inbox to hiring-manager handoff' },
];

const AGENT_STATS = [
  { v: '47', l: 'CVS SCORED', c: 'var(--purple-lav)' },
  { v: '12', l: 'INVITATIONS SENT', c: 'var(--purple-lav)' },
  { v: '8', l: 'ASSESSMENTS GRADED', c: '#7dd0a8' },
  { v: '3', l: 'AUTO-REJECTED', c: '#e8b167' },
  { v: '2', l: 'FLAGGED FOR YOU', c: '#f87171' },
];

const AGENT_FUNNEL = [
  { l: 'APPLIED', v: 47, h: 100, c: 'var(--purple-lav)' },
  { l: 'SCORED', v: 39, h: 83, c: 'var(--purple-lav)' },
  { l: 'INVITED', v: 22, h: 47, c: 'var(--purple-lav)' },
  { l: 'ASSESSED', v: 9, h: 19, c: '#7dd0a8' },
  { l: 'REVIEW', v: 5, h: 11, c: '#7dd0a8' },
];

const AGENT_DECISIONS = [
  { n: 'Maya Chen', i: 'M', sc: 92, k: 'advance', t: '2m', act: 'advanced to Review', why: 'cleared all 6 dimensions · prompt-evidence quality 9.4' },
  { n: 'Jordan Patel', i: 'J', sc: 88, k: 'advance', t: '14m', act: 'advanced to Review', why: 'strong system design · weak ai prompting (flag attached)' },
  { n: 'Tom Liu', i: 'T', sc: 47, k: 'reject', t: '18m', act: 'auto-rejected', why: 'below role threshold (55) · python only, role needs go' },
  { n: 'Dana Ortiz', i: 'D', sc: 61, k: 'flag', t: '34m', act: 'flagged borderline', why: '1 of 6 dimensions sub-threshold · needs your call' },
  { n: 'Priya Raman', i: 'P', sc: 84, k: 'invite', t: '1h', act: 'sent assessment', why: 'cv match 91% · invited to 90-min retry-rails task' },
];

const AUTONOMY_DIAL = [
  ['Score CVs', true],
  ['Send assessments', true],
  ['Auto-reject < 55', true],
  ['Advance to review', true],
  ['Auto-schedule HM', false],
  ['Send rejections', false],
];

const AI_CAPTURE = [
  ['M', 'prompts to AI', '7', 'iteration count, specificity'],
  ['P', 'paste events', '3', 'what came from where'],
  ['T', 'test runs', '11', 'red→green cycles'],
  ['E', 'edits before run', '42', 'typing vs prompting ratio'],
  ['D', 'time-to-decision', '4m', 'from bug-spotted to fix-shipped'],
];

const SCORING_AXES = [
  ['Problem framing', 9.4],
  ['Code quality', 8.8],
  ['AI prompting', 9.6],
  ['Verification', 8.2],
  ['Communication', 9.0],
  ['Time to decision', 8.5],
];

const SCORING_EVIDENCE = [
  {
    d: '9.6', l: 'AI prompting', t: '+38m',
    m: 'Asked Claude for the boundary case before coding',
    q: '"What\'s the case where 200 + body=ok:false should retry?"',
    n: 'Specific, hypothesis-driven prompt. Not autopilot.',
    hi: true,
  },
  {
    d: '9.4', l: 'Problem framing', t: '+04m',
    m: 'Wrote down assumptions before opening any file',
    q: '"Assumption: idempotency key is per-attempt, not per-call. Verify."',
    n: 'Started with the unknowns, not the keyboard.',
    hi: true,
  },
  {
    d: '8.2', l: 'Verification', t: '+47m',
    m: 'Ran a property test on both retry branches',
    q: 'fc.assert(fc.property(arbResp, r => …))',
    n: 'Caught the boundary case humans usually miss.',
    hi: false,
  },
  {
    d: '8.5', l: 'Time-to-decision', t: '+23m',
    m: 'Bug spotted → fix shipped in 4 minutes',
    q: '(see git log: 3 commits, no thrash)',
    n: 'No fishing. Hypothesis → test → ship.',
    hi: false,
  },
];

const CHAT_QUERIES = [
  'Top backend candidates who scored above 8 on AI prompting',
  'Anyone in the pipeline who has shipped infra at scale',
  'Show me candidates similar to Maya Chen',
  'Who did Stripe historically interview from this pool?',
  'Compare Jordan and Maya side-by-side on system design',
];

const CHAT_RESULTS = [
  { n: 'Maya Chen', sc: 9.6, role: 'Sr. Backend', c: 'M', why: 'Stripe 4y · went to Claude before code · property tests' },
  { n: 'Jordan Patel', sc: 8.9, role: 'Sr. Backend', c: 'J', why: 'Linear 3y · prompt evidence consistently specific' },
  { n: 'Priya Raman', sc: 8.4, role: 'Staff ML', c: 'P', why: 'Anthropic 2y · uses Claude as a thinking partner' },
];

const FLOW_STEPS = [
  { n: '01', i: 'M', l: 'Workable sync', who: 'AGENT', t: '~1m', d: 'CV lands → match-scored against the role' },
  { n: '02', i: 'A', l: 'AI assessment', who: 'AGENT', t: '90m', d: 'Candidate codes with Claude in the IDE' },
  { n: '03', i: 'S', l: 'Six-axis scoring', who: 'AGENT', t: '~2m', d: 'Evidence-linked rubric, calibrated to your bar' },
  { n: '04', i: 'R', l: 'Recruiter review', who: 'YOU', t: '5m', d: 'You see only what needed your judgement' },
  { n: '05', i: 'H', l: 'HM handoff', who: 'YOU', t: '1m', d: 'Shareable client view, expiring link' },
];

const ATS_CONNECTIONS = [
  ['Workable', 'connected', 'syncing 4 roles · 47 cvs'],
  ['Greenhouse', 'available', 'one-click connect'],
  ['Lever', 'available', 'one-click connect'],
  ['Ashby', 'available', 'one-click connect'],
  ['Slack', 'connected', '#hiring-eng notifs'],
  ['Stripe billing', 'connected', '$347 / $300 cap this mo.'],
];

const SHARE_DURATIONS = ['24h', '7d', '30d', 'single-view'];

const AgentPanel = () => (
  <div className="mc-show-grid mc-show-grid-1-6">
    <div className="mc-show-card mc-show-card-flush">
      <div className="mc-show-card-head">
        <span className="mc-show-kicker mc-show-kicker-mute">// LIVE PIPELINE · SR. BACKEND</span>
        <span className="mc-show-spacer" />
        <span className="mc-show-chip on">● agent on · last action 2m ago</span>
      </div>
      <div className="mc-show-card-body">
        <div className="mc-show-funnel-head">
          <div>
            <div className="mc-show-funnel-num">47 candidates</div>
            <div className="mc-show-funnel-sub">33 scored · 9 in assessment · 5 in review · agent owns 0–60</div>
          </div>
          <span className="mc-show-funnel-delta">+12 today</span>
        </div>
        <div className="mc-show-funnel">
          {AGENT_FUNNEL.map((s) => (
            <div key={s.l}>
              <div className="mc-show-funnel-bar">
                <div style={{ height: `${s.h}%`, background: s.c }}>{s.v}</div>
              </div>
              <div className="mc-show-funnel-label">{s.l}</div>
            </div>
          ))}
        </div>
        <div className="mc-show-kicker mc-show-kicker-mute mc-show-mb-10">// AGENT ACTIVITY · LAST HOUR</div>
        <div className="mc-show-decisions">
          {AGENT_DECISIONS.map((c) => (
            <div key={c.n} className={`mc-show-decision ${c.k === 'flag' ? 'flag' : ''}`.trim()}>
              <span className="mc-show-decision-avatar">{c.i}</span>
              <div>
                <div className="mc-show-decision-name">{c.n}</div>
                <div className="mc-show-decision-meta">{c.t} ago · agent</div>
              </div>
              <div>
                <div className="mc-show-decision-act">
                  <span className={`mc-show-decision-tag mc-show-decision-tag-${c.k}`}>{c.k}</span>
                  {c.act}
                </div>
                <div className="mc-show-decision-why">{c.why}</div>
              </div>
              <span className={`score-pill ${c.sc >= 80 ? 'hi' : c.sc >= 60 ? 'mid' : 'lo'}`}>{c.sc}</span>
            </div>
          ))}
        </div>
      </div>
    </div>

    <div className="mc-show-side">
      <div className="mc-show-card mc-show-card-dark">
        <div className="mc-show-kicker mc-show-kicker-lav mc-show-mb-14">// AUTONOMY DIAL</div>
        <div className="mc-show-side-h">You decide what the agent owns end-to-end.</div>
        {AUTONOMY_DIAL.map(([l, on], i) => (
          <div key={l} className={`mc-show-toggle-row ${i < AUTONOMY_DIAL.length - 1 ? 'border' : ''}`.trim()}>
            <span>{l}</span>
            <span className={`mc-show-switch ${on ? 'on' : ''}`.trim()}>
              <span />
            </span>
          </div>
        ))}
      </div>

      <div className="mc-show-card">
        <div className="mc-show-kicker mc-show-kicker-mute mc-show-mb-10">// WHY IT MATTERS</div>
        <ul className="mc-show-bullets">
          <li><b>4.8× faster</b> top-of-funnel.</li>
          <li><b>$0.41 / candidate</b> all-in (CV → graded).</li>
          <li><b>Zero black-box.</b> Every decision links to evidence.</li>
        </ul>
      </div>
    </div>
  </div>
);

const AssessmentPanel = () => (
  <div className="mc-show-grid mc-show-grid-1-4">
    <div className="mc-show-ide">
      <div className="mc-show-ide-head">
        <div className="mc-show-ide-dots">
          <span style={{ background: '#ff6058' }} />
          <span style={{ background: '#ffbd2e' }} />
          <span style={{ background: '#28c941' }} />
        </div>
        <span className="mc-show-ide-title">maya · payments-retries · 23m left</span>
        <span className="mc-show-spacer" />
        <span className="mc-show-ide-rec">● RECORDING · 7 prompts captured</span>
      </div>
      <div className="mc-show-ide-body">
        <div className="mc-show-ide-code">
          <div className="mc-show-code-comment">{`// retries.ts`}</div>
          <div className="mc-show-code-row"><span>1</span><span><span style={{ color: 'var(--purple-lav)' }}>export</span> async function <span style={{ color: 'var(--purple-lav)' }}>retry</span>{'<T>('}</span></div>
          <div className="mc-show-code-row"><span>2</span><span>{'  fn: () => Promise<T>,'}</span></div>
          <div className="mc-show-code-row"><span>3</span><span>{'  opts: { maxAttempts: '}<span style={{ color: '#7dd0a8' }}>number</span>{' }'}</span></div>
          <div className="mc-show-code-row"><span>4</span><span>{') {'}</span></div>
          <div className="mc-show-code-row"><span>5</span><span>{'  for (let i = 0; i < opts.maxAttempts; i++) {'}</span></div>
          <div className="mc-show-code-row"><span>6</span><span>{'    try { return await fn(); }'}</span></div>
          <div className="mc-show-code-row hi"><span>7</span><span>{'    catch (e) {'}</span></div>
          <div className="mc-show-code-row hi"><span>8</span><span>{'      if (!isRetryable(e)) throw e;'}</span></div>
          <div className="mc-show-code-row"><span>9</span><span>{'      await sleep(backoff(i));'}</span></div>
          <div className="mc-show-code-row"><span>10</span><span>{'    }'}</span></div>
          <div className="mc-show-code-row"><span>11</span><span>{'  }'}</span></div>
          <div className="mc-show-code-row"><span>12</span><span>{'}'}</span></div>
        </div>
        <div className="mc-show-ide-chat">
          <div className="mc-show-kicker mc-show-kicker-lav">// CLAUDE · MAYA'S SESSION</div>
          <div className="mc-show-ide-msg you">What's the boundary case where 200 + body says ok:false? Should that retry?</div>
          <div className="mc-show-ide-msg-claude">
            Two questions: <b>(1)</b> is the failure transient or terminal? <b>(2)</b> does the upstream tell you which?
            Reading <code>Response.body.retryable</code> — if true, throw RetryableError; if false, return.
          </div>
          <div className="mc-show-ide-msg you">Add a property test for both branches.</div>
          <div className="mc-show-ide-typing"><span /> editing tests/retries.test.ts…</div>
        </div>
      </div>
      <div className="mc-show-ide-foot">
        <div style={{ color: '#7dd0a8' }}>✓ retries · honours idempotency key (3ms)</div>
        <div style={{ color: '#7dd0a8' }}>✓ retries · 200+body-says-failure flips to retry (4ms)</div>
        <div style={{ color: '#e8b167' }}>⚠ retries · timing-attack on idemKey not yet covered</div>
      </div>
    </div>

    <div className="mc-show-side">
      <div className="mc-show-card">
        <div className="mc-show-kicker mc-show-mb-14">// WHAT WE CAPTURE</div>
        <h3 className="mc-show-card-h">
          Every <em>prompt, paste, and decision</em> — not just the final code.
        </h3>
        <div>
          {AI_CAPTURE.map(([i, l, v, s], j) => (
            <div key={l} className={`mc-show-capture ${j < AI_CAPTURE.length - 1 ? 'border' : ''}`.trim()}>
              <span className="mc-show-capture-icon">{i}</span>
              <div>
                <div className="mc-show-capture-l">{l}</div>
                <div className="mc-show-capture-s">{s}</div>
              </div>
              <span className="mc-show-capture-v">{v}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="mc-show-card mc-show-card-purple">
        <div className="mc-show-kicker mc-show-mb-8">// THE BIG IDEA</div>
        <p className="mc-show-big-idea">
          You're not hiring for who can solve a leetcode question without ChatGPT. <b>You're hiring for who can ship with it.</b> Taali doesn't block AI — it scores how candidates use it.
        </p>
      </div>
    </div>
  </div>
);

const ScoringPanel = () => (
  <div className="mc-show-grid mc-show-grid-1-12">
    <div className="mc-show-card">
      <div className="mc-show-kicker mc-show-kicker-mute mc-show-mb-14">// CANDIDATE · MAYA CHEN · STRIPE / SR. BACKEND</div>
      <div className="mc-show-score-head">
        <div className="mc-show-score-ring">
          <svg viewBox="0 0 120 120" width="120" height="120">
            <circle cx="60" cy="60" r="52" fill="none" stroke="var(--bg-3)" strokeWidth="10" />
            <circle
              cx="60" cy="60" r="52" fill="none"
              stroke="var(--purple)" strokeWidth="10"
              strokeDasharray={2 * Math.PI * 52}
              strokeDashoffset={2 * Math.PI * 52 * (1 - 0.92)}
              strokeLinecap="round"
              transform="rotate(-90 60 60)"
            />
          </svg>
          <div className="mc-show-score-num">92</div>
        </div>
        <div>
          <div className="mc-show-score-h">Strong hire</div>
          <div className="mc-show-score-d">Top 12% of 47 candidates. Cleared every dimension threshold for this role.</div>
          <span className="mc-show-chip green">● Confidence 0.94</span>
        </div>
      </div>
      <div className="mc-show-axes">
        {SCORING_AXES.map(([l, v]) => (
          <div key={l} className="mc-show-axis">
            <span className="mc-show-axis-l">{l}</span>
            <div className="mc-show-axis-bar">
              <div style={{ width: `${v * 10}%` }} />
              <div className="mc-show-axis-mark" />
            </div>
            <span className="mc-show-axis-v">{v}</span>
          </div>
        ))}
        <div className="mc-show-axis-bar-foot">↑ STRIPE'S BAR FOR THIS ROLE: 7.0</div>
      </div>
    </div>

    <div className="mc-show-card mc-show-card-flush">
      <div className="mc-show-card-head">
        <span className="mc-show-kicker mc-show-kicker-mute">// EVIDENCE · LINKED TO MOMENTS</span>
        <span className="mc-show-spacer" />
        <span className="mc-show-evidence-foot">scrub timeline ↓</span>
      </div>
      <div className="mc-show-card-body">
        {SCORING_EVIDENCE.map((e, i) => (
          <div key={e.l} className={`mc-show-evidence ${i < SCORING_EVIDENCE.length - 1 ? 'border' : ''}`.trim()}>
            <div className="mc-show-evidence-meta">
              <div className={`mc-show-evidence-d ${e.hi ? 'hi' : ''}`.trim()}>{e.d}</div>
              <div className="mc-show-evidence-t">{e.t}</div>
            </div>
            <div>
              <div className="mc-show-evidence-l">{e.l}</div>
              <div className="mc-show-evidence-m">{e.m}</div>
              <div className="mc-show-evidence-q">{e.q}</div>
              <div className="mc-show-evidence-n">// {e.n}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  </div>
);

const ChatPanel = () => (
  <div className="mc-show-grid mc-show-grid-1-16">
    <div className="mc-show-card">
      <div className="mc-show-kicker mc-show-kicker-mute mc-show-mb-14">// SUGGESTED QUERIES</div>
      <h3 className="mc-show-card-h">
        Ask in <em>plain English.</em><br />No boolean strings.
      </h3>
      <div className="mc-show-queries">
        {CHAT_QUERIES.map((q, i) => (
          <button type="button" key={q} className={`mc-show-query ${i === 0 ? 'on' : ''}`.trim()}>
            <span className="mc-show-query-num">0{i + 1}</span>
            {q}
          </button>
        ))}
      </div>
    </div>

    <div className="mc-show-card mc-show-card-flush mc-show-chat">
      <div className="mc-show-card-head">
        <span className="mc-show-chat-icon">●</span>
        <span className="mc-show-chat-label">chat · taali</span>
        <span className="mc-show-spacer" />
        <span className="mc-show-chip on">● indexed: 47 candidates · 12 roles</span>
      </div>
      <div className="mc-show-chat-body">
        <div className="mc-show-chat-user">
          Top backend candidates who scored above 8 on AI prompting
        </div>
        <div className="mc-show-chat-tool">
          <span className="mc-show-chat-tool-icon">⌕</span>
          <span>query.candidates · role:backend · ai_prompting{'>'}8 · order:score desc</span>
          <span className="mc-show-chat-tool-result">3 results · 0.4s</span>
        </div>
        <div className="mc-show-chat-results">
          {CHAT_RESULTS.map((c) => (
            <div key={c.n} className="mc-show-chat-result">
              <div className="mc-show-chat-result-head">
                <span className="mc-show-chat-result-avatar">{c.c}</span>
                <div>
                  <div className="mc-show-chat-result-n">{c.n}</div>
                  <div className="mc-show-chat-result-r">{c.role}</div>
                </div>
                <span className="mc-show-chat-result-sc">{c.sc}</span>
              </div>
              <div className="mc-show-chat-result-w">{c.why}</div>
            </div>
          ))}
        </div>
        <div className="mc-show-chat-assistant">
          Three candidates above the 8.0 bar on AI prompting. <b>Maya</b> stands out — she also leads on problem framing (9.4) and is the only one who shipped property tests. Want me to draft an HM intro for her?
        </div>
        <div className="mc-show-chat-composer">
          <div className="mc-show-chat-composer-input">Ask anything about your pipeline…</div>
          <div className="mc-show-chat-composer-foot">
            <span>⌘↵ to send · @candidate to scope</span>
            <button type="button">Send →</button>
          </div>
        </div>
      </div>
    </div>
  </div>
);

const WorkflowPanel = () => (
  <div className="mc-show-flow">
    <div className="mc-show-card mc-show-card-pad">
      <div className="mc-show-kicker mc-show-kicker-mute mc-show-mb-18">// END-TO-END FLOW · CV → HIRE</div>
      <div className="mc-show-flow-grid">
        <div className="mc-show-flow-line" aria-hidden="true" />
        {FLOW_STEPS.map((s) => (
          <div key={s.n} className="mc-show-flow-step">
            <div className={`mc-show-flow-circle ${s.who === 'AGENT' ? 'agent' : 'you'}`.trim()}>{s.i}</div>
            <div className="mc-show-flow-meta">{s.n} · {s.t}</div>
            <div className="mc-show-flow-l">{s.l}</div>
            <div className="mc-show-flow-d">{s.d}</div>
            <div className={`mc-show-flow-owner ${s.who === 'AGENT' ? 'agent' : 'you'}`.trim()}>{s.who} OWNS</div>
          </div>
        ))}
      </div>
    </div>

    <div className="mc-show-grid mc-show-grid-1-1">
      <div className="mc-show-card">
        <div className="mc-show-kicker mc-show-kicker-mute mc-show-mb-14">// CONNECTED</div>
        <h3 className="mc-show-card-h-sm">Plays nice with your ATS.</h3>
        <div className="mc-show-ats">
          {ATS_CONNECTIONS.map(([n, s, d]) => (
            <div key={n} className="mc-show-ats-card">
              <div className="mc-show-ats-head">
                <span>{n}</span>
                <span className={`mc-show-ats-status ${s}`}>● {s.toUpperCase()}</span>
              </div>
              <div className="mc-show-ats-d">{d}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="mc-show-card">
        <div className="mc-show-kicker mc-show-kicker-mute mc-show-mb-14">// HM HANDOFF</div>
        <h3 className="mc-show-card-h-sm">One link. No login. Expiring.</h3>
        <p className="mc-show-share-sub">Share a clean read-only verdict with the hiring manager. No exports, no screenshots, no leaks.</p>
        <div className="mc-show-share-link">
          <span>🔗</span>
          <span className="mc-show-share-url">taali.ai/c/maya-chen-92-x71qd</span>
          <span className="mc-show-share-ttl">· 7d</span>
        </div>
        <div className="mc-show-share-durations">
          {SHARE_DURATIONS.map((x, i) => (
            <span key={x} className={`mc-show-share-d ${i === 1 ? 'on' : ''}`.trim()}>{x}</span>
          ))}
        </div>
        <div className="mc-show-share-active">
          <b>Active link · viewed 2× by sara@stripe.com.</b> Revoke any time.
        </div>
      </div>
    </div>
  </div>
);

export const DemoShowcasePage = ({ onNavigate }) => {
  const [active, setActive] = useState('agent');
  const idx = SHOWCASE_TABS.findIndex((t) => t.k === active);
  const next = SHOWCASE_TABS[idx + 1];
  const prev = SHOWCASE_TABS[idx - 1];

  return (
    <div className="mc-show">
      {/* TOP BAR */}
      <div className="mc-show-topbar">
        <button
          type="button"
          className="mc-show-logo"
          onClick={() => onNavigate?.('landing')}
          aria-label="Taali home"
        >
          <TaaliTile
            className="h-7 w-7 rounded-[7px]"
            fillClassName="text-[var(--purple)]"
            lineClassName="text-white"
            strokeWidth={2.4}
            cornerRadius={6.5}
          />
          <span>taali<em>.</em></span>
        </button>
        <span className="mc-show-topbar-meta">· LIVE WALKTHROUGH · ACME / SR. BACKEND</span>
        <span className="mc-show-spacer" />
        <span className="mc-show-chip green">Demo data · resets daily</span>
        <button type="button" className="mc-show-btn" onClick={() => onNavigate?.('landing')}>Skip the tour</button>
        <button type="button" className="mc-show-btn primary" onClick={() => onNavigate?.('login')}>Talk to founder →</button>
      </div>

      {/* HERO */}
      <section className="mc-show-section">
        <div className="mc-show-kicker mc-show-mb-14">// THE WALKTHROUGH · 5 SURFACES · ~ 6 MIN</div>
        <div className="mc-show-hero">
          <h1 className="mc-show-hero-title">
            Your hiring funnel,<br />now with an <em>agent</em><br />at the top of it.
          </h1>
          <p className="mc-show-hero-sub">
            Taali's recruiter-agent is autonomous by default and accountable by design. It triages, assesses, scores, and routes — and shows its work at every step. The five tabs below are <b>real product surfaces</b>, pre-loaded with a real role.
          </p>
        </div>

        {/* AGENT MARQUEE */}
        <div className="mc-show-marquee">
          <div className="mc-show-marquee-glow" aria-hidden="true" />
          <div className="mc-show-marquee-head">
            <div className="mc-show-marquee-icon">
              <span className="mc-show-marquee-icon-tile" aria-hidden="true">▲</span>
              <span className="mc-show-marquee-icon-dot" aria-hidden="true" />
            </div>
            <div>
              <div className="mc-show-marquee-kicker">YOUR AGENT · LAST 4 HOURS</div>
              <div className="mc-show-marquee-h">
                Acted <b style={{ color: 'var(--purple-lav)' }}>87 times</b> · paused <b style={{ color: '#e8b167' }}>2</b> for your call · within budget
              </div>
            </div>
            <button type="button" className="mc-show-marquee-cta">See decisions feed →</button>
          </div>
          <div className="mc-show-marquee-stats">
            {AGENT_STATS.map((s) => (
              <div key={s.l} className="mc-show-marquee-stat">
                <div className="mc-show-marquee-v" style={{ color: s.c }}>{s.v}</div>
                <div className="mc-show-marquee-l">{s.l}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* TAB STRIP */}
      <section className="mc-show-section mc-show-tabs-wrap">
        <div className="mc-show-tabs" role="tablist" aria-label="Walkthrough sections">
          {SHOWCASE_TABS.map((t) => {
            const on = t.k === active;
            return (
              <button
                key={t.k}
                type="button"
                role="tab"
                aria-selected={on}
                className={`mc-show-tab ${on ? 'on' : ''}`.trim()}
                onClick={() => setActive(t.k)}
              >
                <div className={`mc-show-tab-num ${on ? 'on' : ''}`.trim()}>{t.n}</div>
                <div className={`mc-show-tab-l ${on ? 'on' : ''}`.trim()}>{t.label}</div>
                <div className="mc-show-tab-s">{t.sub}</div>
              </button>
            );
          })}
        </div>
      </section>

      {/* ACTIVE PANEL */}
      <section className="mc-show-section mc-show-panel">
        {active === 'agent' && <AgentPanel />}
        {active === 'assessment' && <AssessmentPanel />}
        {active === 'scoring' && <ScoringPanel />}
        {active === 'chat' && <ChatPanel />}
        {active === 'workflow' && <WorkflowPanel />}

        <div className="mc-show-pager">
          <button
            type="button"
            className="mc-show-btn"
            disabled={!prev}
            onClick={() => prev && setActive(prev.k)}
          >
            <span>←</span> Previous
          </button>
          <div className="mc-show-pager-count">{idx + 1} / {SHOWCASE_TABS.length}</div>
          {next ? (
            <button type="button" className="mc-show-btn primary" onClick={() => setActive(next.k)}>
              Next: {next.label} <span>→</span>
            </button>
          ) : (
            <button type="button" className="mc-show-btn primary" onClick={() => onNavigate?.('login')}>
              Start the trial <span>→</span>
            </button>
          )}
        </div>

        <div className="mc-show-cta">
          <div>
            <div className="mc-show-kicker mc-show-mb-10">READY TO PUT IT TO WORK?</div>
            <h2 className="mc-show-cta-h">
              Wire your <em>real pipeline</em> into Taali. Free for 14 days.
            </h2>
            <p className="mc-show-cta-sub">
              Connect Workable or Greenhouse, point Taali at one role, and watch your agent triage your next batch of CVs by morning. No card. Cancel any time.
            </p>
          </div>
          <div className="mc-show-cta-side">
            <button type="button" className="mc-show-btn primary tall" onClick={() => onNavigate?.('register')}>
              Start the 14-day trial →
            </button>
            <button type="button" className="mc-show-btn tall" onClick={() => onNavigate?.('demo-lead')}>
              Book a 20-min call instead
            </button>
            <div className="mc-show-cta-foot">SOC 2 · GDPR · NEVER USED FOR TRAINING</div>
          </div>
        </div>
      </section>
    </div>
  );
};

export default DemoShowcasePage;
