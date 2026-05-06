import React, { useState } from 'react';

import { TaaliTile } from '../../shared/ui/Branding';
import { AuthField } from '../auth/AuthShell';

const STEPS = [
  { n: '01', t: 'Watch the agent triage', d: '47 candidates scored, paced, and shortlisted in 30 seconds — autonomously.' },
  { n: '02', t: 'Open an AI-native assessment', d: 'Step into the IDE the candidate used. Replay every prompt and paste.' },
  { n: '03', t: 'See the standing report', d: 'Score, AI fluency, evidence, interview-ready questions — what your team gets.' },
];

// DemoLeadPage — pre-credentials capture before the demo sandbox spins
// up. Mirrors the canvas layout: editorial pane on the left with the
// kicker / headline / 3-step preview, form on the right.
//
// Submit currently redirects to /demo (the demo experience). When the
// sandbox-seeding API ships, the form payload should drive
// /demo/sandbox?email=... etc.
export const DemoLeadPage = ({ onNavigate }) => {
  const [form, setForm] = useState({ email: '', company: '', role: '', headcount: '' });
  const [submitting, setSubmitting] = useState(false);

  const update = (key) => (e) => setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!form.email.trim()) return;
    setSubmitting(true);
    // No backend endpoint yet for capturing the lead — route into the
    // existing demo experience so users get the walkthrough immediately.
    window.setTimeout(() => {
      setSubmitting(false);
      onNavigate?.('demo');
    }, 200);
  };

  return (
    <div className="mc-demo-lead">
      <aside className="mc-demo-lead-editorial">
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
        <div className="mc-kicker" style={{ marginBottom: 14 }}>SEE THE AGENT WORK A REAL ROLE · 7 MIN</div>
        <h1 className="mc-demo-lead-title">
          Try the live <em>walkthrough</em>.<br />
          <span style={{ color: 'var(--ink-2)' }}>No sales call.</span>
        </h1>
        <p className="mc-demo-lead-sub">
          We'll spin up a sandbox seeded with the Senior Backend Engineer role at Stripe. You'll watch the
          agent triage candidates autonomously, then open a real AI-native assessment — the IDE, the
          prompts, the fluency score — exactly as your hiring manager would see it.
        </p>
        <ol className="mc-demo-lead-steps">
          {STEPS.map(({ n, t, d }) => (
            <li key={n}>
              <span className="mc-demo-lead-step-num">{n}</span>
              <div>
                <div className="mc-demo-lead-step-t">{t}</div>
                <div className="mc-demo-lead-step-d">{d}</div>
              </div>
            </li>
          ))}
        </ol>
      </aside>

      <main className="mc-demo-lead-form-pane">
        <form className="mc-demo-lead-form" onSubmit={handleSubmit}>
          <span className="mc-demo-lead-tag">NO CARD · NO SALES CALL</span>
          <h2 className="mc-demo-lead-form-title">Tell us about you</h2>
          <AuthField
            label="Work email"
            name="email"
            type="email"
            autoComplete="email"
            placeholder="sara@company.com"
            value={form.email}
            onChange={update('email')}
            required
            autoFocus
          />
          <AuthField
            label="Company"
            name="company"
            autoComplete="organization"
            placeholder="Linear"
            value={form.company}
            onChange={update('company')}
          />
          <AuthField
            label="Role you're hiring for"
            name="role"
            placeholder="Senior Backend Engineer"
            helper="We'll seed the sandbox with shortlist candidates relevant to this role."
            value={form.role}
            onChange={update('role')}
          />
          <AuthField
            label="How many people are on your hiring team?"
            name="headcount"
            placeholder="3"
            value={form.headcount}
            onChange={update('headcount')}
          />
          <button type="submit" className="mc-auth-cta" disabled={submitting}>
            {submitting ? 'Opening sandbox…' : 'Open the sandbox →'}
          </button>
          <p className="mc-demo-lead-foot">
            We never ask for a card. The sandbox is yours for 7 days, then archived.
          </p>
        </form>
      </main>
    </div>
  );
};

export default DemoLeadPage;
