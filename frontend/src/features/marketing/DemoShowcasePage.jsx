import React, { useState } from 'react';
import { Check } from 'lucide-react';

import { TaaliTile } from '../../shared/ui/Branding';
import { AuthField } from '../auth/AuthShell';

const PANES = [
  { k: 'jobs',       label: "Jobs you're hiring for",     url: 'taali.ai/jobs',         headline: 'Stop juggling tabs to know where every role stands.' },
  { k: 'candidates', label: 'Candidates flowing in',      url: 'taali.ai/candidates',   headline: 'Walk in with a ranked shortlist, not a CV pile.' },
  { k: 'chat',       label: 'Ask about your candidates',  url: 'taali.ai/chat',         headline: 'Pull a shortlist for a new brief in seconds.' },
  { k: 'workspace',  label: 'Candidate workspace',        url: 'taali.ai/assess/demo',  headline: 'Watch how candidates think, not just what they ship.' },
  { k: 'profile',    label: 'Client-share profile',       url: 'taali.ai/c/demo',       headline: 'Send a clean verdict your client can act on.' },
];

const OUTCOMES = [
  'Every CV scored against the role the moment it lands.',
  'Pre-screen weeds out the obvious nos for you.',
  'Search in plain English, not boolean strings.',
];

const CANDIDATES_MOCK = [
  { name: 'Maya Chen',    role: 'Senior Backend',  score: 92, stage: 'Review',         agent: 'Agent advanced 2m' },
  { name: 'Jordan Patel', role: 'Senior Backend',  score: 88, stage: 'Review',         agent: 'Agent advanced 14m' },
  { name: 'Priya Raman',  role: 'Staff ML',        score: 84, stage: 'Review',         agent: 'You · 1h ago' },
  { name: 'Alex Romero',  role: 'Senior Backend',  score: 79, stage: 'In assessment',  agent: 'Live · 38m left' },
  { name: 'Sam Okafor',   role: 'Customer Eng',    score: 74, stage: 'In assessment',  agent: 'Live · 1h 12m left' },
];

const JOBS_MOCK = [
  { name: 'Senior Backend Engineer', dept: 'Platform', applied: 47, review: 3, agent: '$31 / $50' },
  { name: 'Staff ML Engineer',       dept: 'AI',       applied: 22, review: 1, agent: '$18 / $40' },
  { name: 'Founding Designer',       dept: 'Design',   applied: 14, review: 0, agent: 'OFF' },
  { name: 'Customer Engineer',       dept: 'GTM',      applied: 31, review: 2, agent: '$11 / $25' },
];

const CHAT_MOCK = [
  { kind: 'user', text: 'Find me three candidates for the Senior Backend role who already worked through retry semantics in production.' },
  { kind: 'tool', text: 'graph_search_candidates · scope=role:1442 · 47 → 3 matches' },
  { kind: 'assistant', text: 'Three strong fits: Maya Chen (92, ex-Stripe, idempotency-keys talk), Jordan Patel (88, Linear), Priya Raman (84, applied retries to ML pipelines).' },
];

const WORKSPACE_MOCK = [
  { time: '12:04', action: 'PROMPT', message: '"explain what idempotency keys do in this retry handler"', tone: 'purple' },
  { time: '12:14', action: 'EDIT',   message: 'Wrote test for duplicate-key collision before fix',       tone: 'green' },
  { time: '12:22', action: 'PASTE',  message: 'Pasted SELECT … FOR UPDATE pattern, modified for schema', tone: 'amber' },
  { time: '12:31', action: 'COMMIT', message: 'Fix + 3 tests covering retry, race, and partial-failure', tone: 'green' },
];

const TONE_COLORS = { purple: 'var(--purple)', green: '#16a34a', amber: '#d97706' };

// DemoShowcasePage — structured product showcase per HANDOFF §4 row 12.
// Static, prospect-facing. Lead form on the left, browser-frame product
// mocks on the right with 5 switchable panes.
export const DemoShowcasePage = ({ onNavigate }) => {
  const [active, setActive] = useState('candidates');
  const pane = PANES.find((p) => p.k === active) || PANES[1];

  return (
    <div className="mc-showcase">
      <header className="mc-showcase-head">
        <button
          type="button"
          className="mc-showcase-logo"
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
        <div className="mc-kicker">PRODUCT SHOWCASE · 5 TABS · PRE-LOADED WITH A REAL ROLE</div>
        <button type="button" className="btn btn-outline btn-sm" onClick={() => onNavigate?.('login')}>
          Sign in
        </button>
      </header>

      <div className="mc-showcase-grid">
        <aside className="mc-showcase-form">
          <h1 className="mc-showcase-title">
            See Taali for <em>your</em> hiring<span className="mc-period">.</span>
          </h1>
          <p className="mc-showcase-sub">
            Click through the live product on the right. We'll send a tailored playback to the email you give us — no calendar tax, no sales call.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              onNavigate?.('demo-lead');
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
          >
            <AuthField label="Full name" name="name" placeholder="Sara Park" />
            <AuthField label="Work email" name="email" type="email" autoComplete="email" placeholder="sara@acme.com" />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <AuthField label="Company" name="company" placeholder="Acme" />
              <div className="mc-auth-field">
                <label className="mc-auth-field-label">Headcount</label>
                <select className="mc-auth-input" defaultValue="11-50">
                  <option>1–10</option>
                  <option>11–50</option>
                  <option>51–200</option>
                  <option>200+</option>
                </select>
              </div>
            </div>
            <div className="mc-auth-field">
              <label className="mc-auth-field-label">Track of interest</label>
              <select className="mc-auth-input" defaultValue="backend">
                <option value="backend">Backend Engineer</option>
                <option value="frontend">Frontend Engineer</option>
                <option value="fullstack">Full-stack Engineer</option>
                <option value="ml">ML / AI Engineer</option>
                <option value="design">Designer</option>
                <option value="other">Other</option>
              </select>
            </div>
            <button type="submit" className="mc-auth-cta">
              Open the live product →
            </button>
            <p className="mc-showcase-trust">No payment · No installs · No sales call</p>
          </form>
        </aside>

        <main className="mc-showcase-main">
          <div className="mc-showcase-tabs">
            {PANES.map((p) => (
              <button
                key={p.k}
                type="button"
                className={`mc-showcase-tab ${p.k === active ? 'on' : ''}`.trim()}
                onClick={() => setActive(p.k)}
              >
                {p.label}
              </button>
            ))}
          </div>

          <p className="mc-showcase-headline">{pane.headline}</p>

          <div className="mc-showcase-frame">
            <div className="mc-showcase-chrome">
              <div className="mc-showcase-traffic">
                <span style={{ background: '#ff6058' }} />
                <span style={{ background: '#ffbd2e' }} />
                <span style={{ background: '#28c941' }} />
              </div>
              <div className="mc-showcase-url">
                <span className="dot" />
                https://{pane.url}
                <span className="live">· live</span>
              </div>
              <span className="mc-showcase-data-tag">Showcase · Acme demo data</span>
            </div>
            <div className="mc-showcase-pane">
              {active === 'jobs' ? <JobsPane /> : null}
              {active === 'candidates' ? <CandidatesPane /> : null}
              {active === 'chat' ? <ChatPane /> : null}
              {active === 'workspace' ? <WorkspacePane /> : null}
              {active === 'profile' ? <ProfilePane /> : null}
            </div>
          </div>

          <div className="mc-showcase-outcomes">
            {OUTCOMES.map((line) => (
              <div key={line} className="mc-showcase-outcome">
                <Check size={13} strokeWidth={2.4} style={{ color: 'var(--green)', flexShrink: 0, marginTop: 2 }} />
                <span>{line}</span>
              </div>
            ))}
          </div>
        </main>
      </div>
    </div>
  );
};

const CandidatesPane = () => (
  <>
    <div className="mc-showcase-pane-head">
      <div>
        <div className="mc-kicker">CANDIDATES · ACME · LIVE</div>
        <h2 className="mc-showcase-pane-title">3 in review across 4 active roles</h2>
      </div>
      <span className="mc-showcase-sync">Synced 2m ago</span>
    </div>
    <div className="mc-showcase-table">
      {CANDIDATES_MOCK.map((c, i) => (
        <div key={c.name} className={`mc-showcase-row ${i ? 'is-divided' : ''}`}>
          <div>
            <div className="mc-showcase-row-name">{c.name}</div>
            <div className="mc-showcase-row-meta">{c.name.toLowerCase().split(' ')[0]}@example.com</div>
          </div>
          <div className="mc-showcase-row-role">{c.role}</div>
          <span className={`mc-showcase-score ${c.score >= 80 ? 'hi' : c.score >= 60 ? 'mid' : 'lo'}`.trim()}>
            {c.score} <span style={{ color: 'var(--mute)', fontWeight: 400 }}>/ 100</span>
          </span>
          <span className="mc-showcase-stage">{c.stage}</span>
          <span
            className="mc-showcase-row-agent"
            style={{ color: c.agent.startsWith('Agent') ? 'var(--purple)' : c.agent.startsWith('Live') ? 'var(--green)' : 'var(--mute)' }}
          >
            {c.agent}
          </span>
        </div>
      ))}
    </div>
  </>
);

const JobsPane = () => (
  <>
    <div className="mc-showcase-pane-head">
      <div>
        <div className="mc-kicker">01 · ROLE PIPELINE</div>
        <h2 className="mc-showcase-pane-title">4 active roles · 114 candidates in flight</h2>
      </div>
      <span className="mc-showcase-sync">Synced from Workable</span>
    </div>
    <div className="mc-showcase-jobs">
      {JOBS_MOCK.map((job) => (
        <div key={job.name} className="mc-showcase-job">
          <div>
            <div className="mc-showcase-row-name">{job.name}</div>
            <div className="mc-showcase-row-meta">{job.dept}</div>
          </div>
          <div className="mc-showcase-job-stats">
            <div><span className="num">{job.applied}</span><span className="lab">Applied</span></div>
            <div><span className="num">{job.review}</span><span className="lab">Review</span></div>
            <div><span className="num is-mono">{job.agent}</span><span className="lab">Agent</span></div>
          </div>
        </div>
      ))}
    </div>
  </>
);

const ChatPane = () => (
  <>
    <div className="mc-showcase-pane-head">
      <div>
        <div className="mc-kicker">02 · CHAT</div>
        <h2 className="mc-showcase-pane-title">Ask in plain English. Get a ranked shortlist.</h2>
      </div>
      <span className="mc-showcase-sync">Live · streaming</span>
    </div>
    <div className="mc-showcase-chat">
      {CHAT_MOCK.map((m, i) => (
        <div key={i} className={`mc-showcase-msg is-${m.kind}`}>
          {m.kind === 'tool' ? <span className="mc-showcase-tool">{m.text}</span> : <span>{m.text}</span>}
        </div>
      ))}
    </div>
  </>
);

const WorkspacePane = () => (
  <>
    <div className="mc-showcase-pane-head">
      <div>
        <div className="mc-kicker">CANDIDATE · MAYA CHEN · AI USAGE TRACE</div>
        <h2 className="mc-showcase-pane-title">Every prompt, paste, and decision — replayed.</h2>
      </div>
      <span className="mc-showcase-sync" style={{ color: 'var(--purple)' }}>Fluency 87 / 100</span>
    </div>
    <div className="mc-showcase-trace">
      {WORKSPACE_MOCK.map((event, i) => (
        <div key={event.time} className={`mc-showcase-trace-row ${i ? 'is-divided' : ''}`}>
          <div className="mc-showcase-trace-time">{event.time}</div>
          <div className="mc-showcase-trace-action" style={{ color: TONE_COLORS[event.tone] }}>{event.action}</div>
          <div className="mc-showcase-trace-message">{event.message}</div>
        </div>
      ))}
    </div>
  </>
);

const ProfilePane = () => (
  <>
    <div className="mc-showcase-pane-head">
      <div>
        <div className="mc-kicker">CLIENT-SHAREABLE · MAYA CHEN</div>
        <h2 className="mc-showcase-pane-title">Strong hire — recommend on-site.</h2>
      </div>
      <span className="mc-showcase-sync" style={{ color: 'var(--green)' }}>92 / 100</span>
    </div>
    <div className="mc-showcase-profile">
      <div className="mc-showcase-profile-bullets">
        <h3>Why we're sharing this candidate</h3>
        <ul>
          <li>Strong systems-design and release-safety signals — both above the role threshold.</li>
          <li>Methodical idempotency reasoning before writing code.</li>
          <li>Pushed back on under-specified retry semantics — context-aware.</li>
        </ul>
      </div>
      <div className="mc-showcase-profile-meta">
        <div className="mc-kicker">SHARED VIEW · NO INTERNAL NOTES · LINK EXPIRES IN 7 DAYS</div>
        <p>Recipients see the score, recommendation, and evidence — no recruiter prompts, no AI usage trace.</p>
      </div>
    </div>
  </>
);

export default DemoShowcasePage;
