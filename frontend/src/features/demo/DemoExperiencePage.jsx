import React, { useCallback, useMemo, useRef, useState } from 'react';

import { assessments as assessmentsApi } from '../../shared/api';
import { motionSafeScrollBehavior } from '../../shared/motion';
import { CandidateMiniNav, MarketingNav } from '../../shared/layout/TaaliLayout';
import { Select } from '../../shared/ui/TaaliPrimitives';
import { PRODUCT_WALKTHROUGH_TASK } from './productWalkthroughModels';

const initialForm = {
  fullName: '',
  position: '',
  workEmail: '',
  company: '',
  companySize: '',
  assessmentTrack: 'AI Engineer',
  marketingConsent: true,
};

const companySizeOptions = ['1–10', '11–50', '51–200', '201–1,000', '1,000+'];
const assessmentTracks = ['AI Engineer', 'Frontend Engineer', 'Backend Engineer', 'Data Engineer'];

const requiredFieldLabels = {
  fullName: 'Full name',
  workEmail: 'Work email',
  company: 'Company',
  companySize: 'Company size',
};

const missingRequiredFields = (form) => (
  Object.entries(requiredFieldLabels)
    .filter(([field]) => !String(form[field] || '').trim())
    .map(([, label]) => label)
);

const scrollToWalkthrough = () => {
  if (typeof document === 'undefined') return;
  window.setTimeout(() => {
    document.getElementById('demo-walkthrough')?.scrollIntoView({ behavior: motionSafeScrollBehavior('smooth'), block: 'start' });
  }, 60);
};

const REPORT_SHOWCASE_TOKEN = 'demo-token';
const REPORT_SHOWCASE_TABS = new Set(['overview', 'cv']);

const PANE_NARRATIVE = {
  jobs: 'See every role you’re hiring for in one place.',
  candidates: 'Triage your shortlist — every candidate scored and ranked.',
  chat: 'Ask plain-English questions across your whole candidate pool.',
  profile: 'Send your client a clean, shareable verdict in one click.',
  workspace: 'The candidate side — every prompt, edit, and test run captured for scoring.',
};

// Slim "why this matters to a recruiter" caption above each pane. Headline
// frames what the recruiter gets out of it; outcomes are the placement /
// time-saving / client-trust wins, not feature lists. Three outcomes max so
// the strip stays one row and never pushes the iframe below the fold.
const PANE_VALUE = {
  jobs: {
    headline: 'Stop juggling tabs to know where every role stands.',
    outcomes: [
      'See pipeline volume per role at a glance',
      'Spot stalled roles before your client asks',
      'No double-entry — Workable stays the source of truth',
    ],
  },
  candidates: {
    headline: 'Walk in with a ranked shortlist instead of a CV pile.',
    outcomes: [
      'Every CV scored against the role the moment it lands',
      'Pre-screen weeds out the obvious nos for you',
      'Search in plain English, not boolean strings',
    ],
  },
  chat: {
    headline: 'Pull a shortlist for a new brief in seconds, not hours.',
    outcomes: [
      'Reuse every past placement and search you’ve done',
      'No saved searches to maintain or remember',
      'Answers come with the candidates attached',
    ],
  },
  workspace: {
    headline: 'Watch how your candidates think, not just what they ship.',
    outcomes: [
      'Every prompt, edit, file open, and test run is captured automatically',
      'Real IDE, real repo, real AI tools — no toy puzzles, no installs for the candidate',
      'Becomes the AI-collaboration evidence that backs every score you send your client',
    ],
  },
  profile: {
    headline: 'Send a candidate to your client without writing a 30-line email.',
    outcomes: [
      'One link, one clear verdict — your client knows what to do',
      'Internal scoring and recruiter notes stay internal',
      'Looks more polished than the PDF reports your competitors send',
    ],
  },
};

export const DemoExperiencePage = ({ onNavigate }) => {
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState('');
  const [submittedLead, setSubmittedLead] = useState(null);
  const [activePane, setActivePane] = useState('jobs');

  // One-shot guard: the validator can only reset a given iframe once. The
  // iframe is sandboxed (allow-scripts allow-same-origin) so even if it
  // navigates somewhere unexpected, the blast radius is the iframe itself.
  // Without this guard, a redirect inside the iframe (e.g. a route that
  // doesn't recognise showcase=1 yet) ping-pongs against frame.src = pane.src
  // and the user sees the showcase flash.
  const resetCountsRef = useRef(new Map());

  const updateField = (field) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const panes = useMemo(() => ({
    jobs: {
      key: 'jobs',
      label: 'Jobs you’re hiring for',
      urlLabel: 'taali.ai/jobs · your open roles',
      src: '/jobs?demo=1&showcase=1',
    },
    candidates: {
      key: 'candidates',
      label: 'Candidates flowing in',
      urlLabel: 'taali.ai/candidates · scored & ranked',
      src: '/candidates?demo=1&showcase=1',
    },
    chat: {
      key: 'chat',
      label: 'Ask about your candidates',
      urlLabel: 'taali.ai/chat · plain-English candidate search',
      src: '/showcase/chat',
    },
    workspace: {
      key: 'workspace',
      label: 'Candidate workspace',
      urlLabel: 'taali.ai/assess/demo · the candidate experience',
      src: '/assessment/live?demo=1&showcase=1',
    },
    profile: {
      key: 'profile',
      label: 'Client-share profile',
      urlLabel: 'taali.ai/c/demo · what your client sees',
      src: `/c/demo?view=client&k=${REPORT_SHOWCASE_TOKEN}&showcase=1`,
    },
  }), []);

  const handleShowcaseFrameLoad = useCallback((pane) => (event) => {
    const frame = event.currentTarget;
    if (typeof window === 'undefined') return;

    let frameHref;
    try {
      frameHref = frame.contentWindow?.location?.href;
    } catch {
      // Cross-origin: leave it alone, the sandbox attribute contains it.
      return;
    }
    // Empty href means the iframe is mid-init (about:blank) — don't fight it.
    if (!frameHref) return;

    let frameUrl;
    try {
      frameUrl = new URL(frameHref, window.location.origin);
    } catch {
      return;
    }

    const intendedUrl = new URL(pane.src, window.location.origin);
    const sameRoute = frameUrl.pathname === intendedUrl.pathname;
    let allowed = sameRoute;

    if (pane.key === 'workspace') {
      allowed = sameRoute
        && frameUrl.searchParams.get('demo') === '1'
        && frameUrl.searchParams.get('showcase') === '1';
    }

    if (pane.key === 'jobs' || pane.key === 'candidates') {
      allowed = sameRoute
        && frameUrl.searchParams.get('demo') === '1'
        && frameUrl.searchParams.get('showcase') === '1';
    }

    if (pane.key === 'chat') {
      allowed = sameRoute;
    }

    if (pane.key === 'profile') {
      const tab = frameUrl.searchParams.get('tab') || 'overview';
      allowed = sameRoute
        && frameUrl.searchParams.get('view') === 'client'
        && frameUrl.searchParams.get('k') === REPORT_SHOWCASE_TOKEN
        && frameUrl.searchParams.get('showcase') === '1'
        && REPORT_SHOWCASE_TABS.has(tab);
    }

    if (allowed) return;

    // Only reset src once per pane lifetime. Subsequent reloads land
    // wherever they land — sandbox keeps the blast radius contained and
    // the user no longer sees the page flash.
    const counts = resetCountsRef.current;
    const used = counts.get(pane.key) || 0;
    if (used >= 1) return;
    counts.set(pane.key, used + 1);
    frame.src = pane.src;
  }, []);

  const handleSubmit = (event) => {
    event.preventDefault();
    const missingFields = missingRequiredFields(form);
    if (missingFields.length > 0) {
      setError(`Please complete: ${missingFields.join(', ')}.`);
      return;
    }

    setError('');
    const nextLead = {
      fullName: form.fullName.trim(),
      workEmail: form.workEmail.trim(),
    };
    setSubmittedLead(nextLead);
    scrollToWalkthrough();

    void assessmentsApi.requestDemo({
      full_name: form.fullName.trim(),
      position: form.position.trim() || null,
      email: form.workEmail.trim(),
      work_email: form.workEmail.trim(),
      company_name: form.company.trim(),
      company_size: form.companySize.trim(),
      assessment_track: form.assessmentTrack,
      marketing_consent: Boolean(form.marketingConsent),
    }).catch(() => {
      // The walkthrough is intentionally not blocked by lead-capture failures.
    });
  };

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      {!submittedLead ? (
        <MarketingNav onNavigate={onNavigate} />
      ) : (
        <CandidateMiniNav label="Demo showcase · navigation locked" />
      )}

      <main className="demo-wrap">
        {!submittedLead ? (
          <section className="demo-split step1">
            <div className="demo-left">
              <div className="pointer-events-none absolute inset-0 tally-bg-soft" />
              <span className="eyebrow">
                <span className="eyebrow-tag">taali.</span>
                Product walkthrough
              </span>
              <h1>
                See Taali <em>end to end.</em>
              </h1>
              <p className="lede">
                Walk through what you can do with Taali — see your jobs board, triage your candidates, ask questions in plain English, watch the candidate experience, and send a clean verdict to your client.
              </p>
              <p className="lede-sub">
                Click through it like a customer would. No setup, no fake data screens — just the product.
              </p>

              <div className="demo-preview">
                <div className="label">Walkthrough task</div>
                <h4>{PRODUCT_WALKTHROUGH_TASK.title}</h4>
                <p>{PRODUCT_WALKTHROUGH_TASK.description}</p>
                <div className="meta">
                  <span>{PRODUCT_WALKTHROUGH_TASK.durationLabel}</span>
                  <span>{PRODUCT_WALKTHROUGH_TASK.stack}</span>
                  <span>{PRODUCT_WALKTHROUGH_TASK.tools}</span>
                </div>
              </div>
            </div>

            <div className="demo-right">
              <form className="demo-card" onSubmit={handleSubmit}>
                <div className="kicker">01 · YOUR DETAILS</div>
                <h2>
                  Open the <em>walkthrough.</em>
                </h2>
                <p className="mb-7 text-sm text-[var(--mute)]">
                  We&apos;ll use this to tailor the follow-up. The walkthrough opens immediately.
                </p>

                <div className="form-grid">
                  <label className="field">
                    <span className="k">Full name</span>
                    <input value={form.fullName} placeholder="Jane Doe" onChange={updateField('fullName')} />
                  </label>
                  <label className="field">
                    <span className="k">Position</span>
                    <input value={form.position} placeholder="Engineering Manager" onChange={updateField('position')} />
                  </label>
                  <label className="field">
                    <span className="k">Work email</span>
                    <input type="email" value={form.workEmail} placeholder="jane@company.com" onChange={updateField('workEmail')} />
                  </label>
                  <label className="field">
                    <span className="k">Company</span>
                    <input value={form.company} placeholder="Acme Inc." onChange={updateField('company')} />
                  </label>
                  <label className="field">
                    <span className="k">Company size</span>
                    <Select value={form.companySize} onChange={updateField('companySize')} placeholder="Select…">
                      <option value="">Select…</option>
                      {companySizeOptions.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </Select>
                  </label>
                  <label className="field">
                    <span className="k">Track</span>
                    <Select value={form.assessmentTrack} onChange={updateField('assessmentTrack')}>
                      {assessmentTracks.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </Select>
                  </label>
                </div>

                <label className="optin">
                  <input
                    type="checkbox"
                    checked={Boolean(form.marketingConsent)}
                    onChange={updateField('marketingConsent')}
                  />
                  <span>Email me a session report sample and occasional product updates.</span>
                </label>

                {error ? (
                  <div className="mt-5 rounded-[14px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
                    {error}
                  </div>
                ) : null}

                <div className="mt-6">
                  <button type="submit" className="btn btn-purple btn-lg w-full justify-center">
                    Open walkthrough <span className="arrow">→</span>
                  </button>
                </div>
                <div className="mt-5 text-center text-[0.8125rem] text-[var(--mute)]">
                  Already know what you need?{' '}
                  <button type="button" className="taali-text-btn" onClick={() => onNavigate?.('login')}>
                    Sign in
                  </button>
                </div>
              </form>
            </div>
          </section>
        ) : (
          <section id="demo-walkthrough" className="wt-wrap">
            <div className="wt-head">
              <div className="l">
                <div className="kicker">01 · PRODUCT WALKTHROUGH</div>
                <h2>
                  Try the five things <em>you’ll do most.</em>
                </h2>
                <p>
                  Thanks, {submittedLead.fullName.split(/\s+/)[0] || submittedLead.fullName}. Click through each pane below — these are live screens, not videos.
                </p>
              </div>
              <div className="r">
                <b>What you can do here</b>
                Open your jobs board, sort and filter candidates, ask Taali questions in plain English, see the candidate experience, and send your client a clean shareable verdict.
              </div>
            </div>

            <div className="wt-tabs" role="tablist" aria-label="Walkthrough views">
              {Object.entries(panes).map(([key, pane], index) => (
                <button
                  key={key}
                  type="button"
                  className={activePane === key ? 'active' : ''}
                  onClick={() => setActivePane(key)}
                >
                  <span className="num">{index + 1}</span>
                  {pane.label}
                </button>
              ))}
            </div>

            {Object.entries(panes).map(([key, pane]) => {
              const value = PANE_VALUE[key];
              return (
                <div key={key} className={`wt-pane ${activePane === key ? 'active' : ''}`}>
                  {value ? (
                    <div className="wt-value">
                      <div className="wt-value-head">
                        <span className="wt-value-eyebrow">Why this matters to you</span>
                        <span className="wt-value-headline">{value.headline}</span>
                      </div>
                      <ul className="wt-value-outcomes">
                        {value.outcomes.map((outcome) => (
                          <li key={outcome}>{outcome}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <div className="wt-frame" data-pane={key}>
                    <div className="wt-chrome">
                      <span className="dots" aria-hidden="true"><i /><i /><i /></span>
                      <span className="url"><span className="lock">●</span>{pane.urlLabel}</span>
                      <span className="wt-locked-badge">Locked preview</span>
                    </div>
                    <div className="wt-stage">
                      <iframe
                        title={pane.label}
                        src={pane.src}
                        sandbox="allow-scripts allow-same-origin"
                        referrerPolicy="no-referrer"
                        onLoad={handleShowcaseFrameLoad(pane)}
                      />
                      <div className="wt-tip"><span className="dot" /> {PANE_NARRATIVE[key] || 'Interactive demo surface'}</div>
                    </div>
                  </div>
                </div>
              );
            })}

            <div className="wt-foot">
              <button type="button" className="taali-text-btn exit" onClick={() => setSubmittedLead(null)}>← Back to form</button>
              <div className="nav">
                <button type="button" className="btn btn-outline btn-sm" onClick={() => onNavigate?.('landing')}>Read more about Taali</button>
                <button type="button" className="btn btn-purple btn-sm" onClick={() => window.location.assign('mailto:hello@taali.ai?subject=Taali%20demo')}>Book a 20-min call</button>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
};

export default DemoExperiencePage;
