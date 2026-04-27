import React, { useCallback, useMemo, useState } from 'react';

import { assessments as assessmentsApi } from '../../shared/api';
import { CandidateMiniNav, MarketingNav } from '../../shared/layout/TaaliLayout';
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

const companySizeOptions = ['1-10', '11-50', '51–200', '201–1,000', '1,000+'];
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
    document.getElementById('demo-walkthrough')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 60);
};

const REPORT_SHOWCASE_TOKEN = 'demo-token';
const REPORT_SHOWCASE_TABS = new Set(['overview', 'assessment', 'prep']);

const PANE_NARRATIVE = {
  jobs: 'Open roles you’re hiring for, with Workable sync.',
  candidates: 'Every candidate scored, filterable, and ranked.',
  report: 'Standing report with assessment + interview transcript.',
  workspace: 'The exact surface candidates use to solve the task.',
};

export const DemoExperiencePage = ({ onNavigate }) => {
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState('');
  const [submittedLead, setSubmittedLead] = useState(null);
  const [activePane, setActivePane] = useState('jobs');

  const updateField = (field) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const panes = useMemo(() => ({
    jobs: {
      key: 'jobs',
      label: 'Jobs you’re hiring for',
      urlLabel: 'taali.ai/jobs · recruiter workspace',
      src: '/jobs?demo=1&showcase=1',
    },
    candidates: {
      key: 'candidates',
      label: 'Candidates flowing in',
      urlLabel: 'taali.ai/candidates · scored & filterable',
      src: '/candidates?demo=1&showcase=1',
    },
    report: {
      key: 'report',
      label: 'Standing report',
      urlLabel: 'taali.ai/c/demo · hiring team report',
      src: `/c/demo?view=interview&k=${REPORT_SHOWCASE_TOKEN}&showcase=1`,
    },
    workspace: {
      key: 'workspace',
      label: 'Candidate workspace',
      urlLabel: 'taali.ai/assess/demo · candidate workspace',
      src: '/assessment/live?demo=1&showcase=1',
    },
  }), []);

  const handleShowcaseFrameLoad = useCallback((pane) => (event) => {
    const frame = event.currentTarget;
    if (typeof window === 'undefined') return;

    try {
      const frameUrl = new URL(frame.contentWindow?.location?.href || '', window.location.origin);
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

      if (pane.key === 'report') {
        const tab = frameUrl.searchParams.get('tab') || 'overview';
        allowed = sameRoute
          && frameUrl.searchParams.get('view') === 'interview'
          && frameUrl.searchParams.get('k') === REPORT_SHOWCASE_TOKEN
          && frameUrl.searchParams.get('showcase') === '1'
          && REPORT_SHOWCASE_TABS.has(tab);
      }

      if (!allowed) {
        frame.src = pane.src;
      }
    } catch {
      frame.src = pane.src;
    }
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
                Open the candidate workspace and the hiring-team report on this page. No fake provisioning, no live candidate launch, just the product flow.
              </p>
              <p className="lede-sub">
                Built from the same surfaces candidates and recruiters use after sign in.
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
                    <select value={form.companySize} onChange={updateField('companySize')}>
                      <option value="">Select...</option>
                      {companySizeOptions.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span className="k">Track</span>
                    <select value={form.assessmentTrack} onChange={updateField('assessmentTrack')}>
                      {assessmentTracks.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
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
                <div className="mt-5 text-center text-[13px] text-[var(--mute)]">
                  Already know what you need?{' '}
                  <button type="button" className="text-[var(--purple)]" onClick={() => onNavigate?.('login')}>
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
                  What candidates and hiring teams <em>actually see.</em>
                </h2>
                <p>
                  Thanks, {submittedLead.fullName.split(/\s+/)[0] || submittedLead.fullName}. The frames below are real app routes using deterministic demo data.
                </p>
              </div>
              <div className="r">
                <b>This walkthrough covers</b>
                The full hiring loop: jobs board, candidates flowing in with Workable sync and CV scoring, the standing report with interview transcript evidence, and the candidate workspace.
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

            {Object.entries(panes).map(([key, pane]) => (
              <div key={key} className={`wt-pane ${activePane === key ? 'active' : ''}`}>
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
            ))}

            <div className="wt-foot">
              <button type="button" className="exit" onClick={() => setSubmittedLead(null)}>← Back to form</button>
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
