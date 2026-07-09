import React, { useState } from 'react';

import { TaaliTile } from '../../shared/ui/Branding';

// Recruiter-readable agent feed shown on the dark editorial pane. Same
// vocabulary the real Hub decision feed uses (advance / reject / taught)
// so the lead-capture surface mirrors what the visitor will see once
// they're inside the product.
const AGENT_FEED_ROWS = [
  { type: 'advance', name: 'Maya Chen', detail: 'Strong fit. Top of pipeline.', tone: 'good' },
  { type: 'advance', name: 'Jordan Patel', detail: 'Strong system design — flag for hiring manager.', tone: 'good' },
  { type: 'reject',  name: 'Tom Liu',     detail: 'Well below your bar. Missing must-have skills.', tone: 'mute' },
  { type: 'pending', name: 'Tariq Al-Ahmad', detail: 'Borderline — paused for your call.', tone: 'pend' },
];

const AgentLiveFeed = () => (
  <div className="mc-demo-feed">
    <div className="mc-demo-feed-head">
      <span className="mc-demo-feed-dot" aria-hidden="true" />
      <span className="mc-demo-feed-label">TAALI · DECISION FEED · SR. BACKEND</span>
      <span className="mc-demo-feed-now">LIVE</span>
    </div>
    {AGENT_FEED_ROWS.map((row, i) => (
      <div key={i} className="mc-demo-feed-row" style={{ opacity: 0.55 + i * 0.11 }}>
        <span
          className="mc-demo-feed-event"
          style={{
            color:
              row.tone === 'good' ? '#7dd0a8'
                : row.tone === 'pend' ? '#e8b167'
                  : 'var(--purple-lav)',
          }}
        >
          {row.type}
        </span>
        <span className="mc-demo-feed-msg">
          <strong style={{ color: 'rgba(255, 255, 255, 0.95)' }}>{row.name}</strong> — {row.detail}
        </span>
      </div>
    ))}
    <div className="mc-demo-feed-cursor" aria-hidden="true">
      <span />
    </div>
  </div>
);

const ROLE_OPTIONS = ['Backend', 'Frontend', 'Full-stack', 'ML / AI', 'Staff+', 'Other'];
const VOLUME_OPTIONS = ['1–5', '6–20', '21–50', '50+'];

const API_URL = (import.meta.env.VITE_API_URL || '').replace(/[\r\n\s]+/g, '').trim();

// DemoLeadPage — pre-credentials capture before the demo sandbox spins
// up. v4 spec: dark editorial pane left, streamlined form right.
//
// Submit forwards the lead to the backend (which emails it to hello@)
// and routes into /demo. The send is fire-and-forget — the visitor's
// path into the walkthrough never blocks on it.
export const DemoLeadPage = ({ onNavigate }) => {
  const [form, setForm] = useState({
    email: '',
    name: '',
    company: '',
    role: 'Backend',
    volume: '6–20',
  });
  const [submitting, setSubmitting] = useState(false);

  const update = (key) => (event) => {
    setForm((prev) => ({ ...prev, [key]: event.target.value }));
  };

  const setField = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!form.email.trim()) return;
    setSubmitting(true);
    if (API_URL) {
      fetch(`${API_URL}/api/v1/public/demo-lead`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      }).catch(() => {});
    }
    window.setTimeout(() => {
      setSubmitting(false);
      onNavigate?.('demo');
    }, 200);
  };

  return (
    <div className="mc-demo-lead">
      {/* ============== LEFT — STORY ============== */}
      <aside className="mc-demo-lead-editorial">
        <div className="mc-demo-lead-grid" aria-hidden="true" />
        <div className="mc-demo-lead-glow" aria-hidden="true" />

        <button
          type="button"
          className="mc-demo-lead-logo"
          onClick={() => onNavigate?.('landing')}
          aria-label="Back to landing"
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

        <div className="mc-demo-lead-story">
          <h1 className="mc-demo-lead-title">
            Let the agent <em>find</em><br />
            your AI-native hires.<br />
            <em>You</em> focus on the ones<br />
            worth your time.
          </h1>
          <p className="mc-demo-lead-sub">
            A recruiter that doesn't sleep. Taali's agent reads every application, runs the
            assessment, scores how candidates code <em>and</em> work with AI, and brings you a ranked shortlist by
            morning. You walk in with a verdict, not an inbox.
          </p>

          <AgentLiveFeed />
        </div>
      </aside>

      {/* ============== RIGHT — THE GATE ============== */}
      <main className="mc-demo-lead-form-pane">
        <div className="mc-demo-lead-topnav">
          <span>HAVE AN ACCOUNT?</span>
          <button type="button" onClick={() => onNavigate?.('login')}>Sign in →</button>
        </div>

        <div className="mc-demo-lead-form-wrap">
          <span className="mc-demo-lead-tag">
            <span className="mc-demo-lead-tag-dot" aria-hidden="true" />
            INTERACTIVE WALKTHROUGH · NO CALL
          </span>

          <h2 className="mc-demo-lead-form-title">
            See it run on <em>your</em> role.
          </h2>
          <p className="mc-demo-lead-form-sub">
            Tell us what you're hiring for. We'll preload realistic candidates and walk you through
            the agent end-to-end — in the next two minutes.
          </p>

          <form className="mc-demo-lead-form" onSubmit={handleSubmit}>
            <label className="mc-demo-lead-field">
              <span className="mc-demo-lead-field-label">Work email</span>
              <input
                type="email"
                name="email"
                autoComplete="email"
                placeholder="sara@company.com"
                value={form.email}
                onChange={update('email')}
                required
                autoFocus
              />
            </label>

            <div className="mc-demo-lead-grid-2">
              <label className="mc-demo-lead-field">
                <span className="mc-demo-lead-field-label">Full name</span>
                <input
                  name="name"
                  autoComplete="name"
                  placeholder="Sara Park"
                  value={form.name}
                  onChange={update('name')}
                />
              </label>
              <label className="mc-demo-lead-field">
                <span className="mc-demo-lead-field-label">Company</span>
                <input
                  name="company"
                  autoComplete="organization"
                  placeholder="Acme"
                  value={form.company}
                  onChange={update('company')}
                />
              </label>
            </div>

            <div className="mc-demo-lead-field">
              <span className="mc-demo-lead-field-label">Role you're hiring for</span>
              <div className="mc-demo-lead-chips" role="radiogroup" aria-label="Role you're hiring for">
                {ROLE_OPTIONS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={form.role === option}
                    onClick={() => setField('role', option)}
                    className={`mc-demo-lead-chip ${form.role === option ? 'on' : ''}`.trim()}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>

            <div className="mc-demo-lead-field">
              <span className="mc-demo-lead-field-label">Hiring volume next quarter</span>
              <div className="mc-demo-lead-segments" role="radiogroup" aria-label="Hiring volume">
                {VOLUME_OPTIONS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={form.volume === option}
                    onClick={() => setField('volume', option)}
                    className={`mc-demo-lead-segment ${form.volume === option ? 'on' : ''}`.trim()}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>

            <button type="submit" className="mc-demo-lead-cta" disabled={submitting}>
              {submitting ? 'Opening walkthrough…' : 'Open the live walkthrough →'}
            </button>

            <div className="mc-demo-lead-trust">
              <span className="mc-demo-lead-trust-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2L2 7l10 5 10-5-10-5z" />
                  <path d="M2 17l10 5 10-5" />
                  <path d="M2 12l10 5 10-5" />
                </svg>
              </span>
              <div>
                <strong>SOC 2 Type II.</strong> We never use your data to train models. Your candidate
                pool stays yours.
              </div>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
};

export default DemoLeadPage;
