import React, { useState } from 'react';
import { Check } from 'lucide-react';

import AssessmentPage from '../assessment_runtime/AssessmentPage';
import { DEMO_ASSESSMENTS, DEFAULT_DEMO_ASSESSMENT_ID } from './demoAssessments';
import { assessments as assessmentsApi } from '../../shared/api';
import { MarketingNav } from '../../shared/layout/TaaliLayout';

const initialForm = {
  fullName: '',
  position: '',
  email: '',
  workEmail: '',
  company: '',
  companySize: '',
  marketingConsent: false,
};

const requiredFieldLabels = {
  fullName: 'Full name',
  position: 'Position',
  email: 'Email',
  workEmail: 'Work email',
  company: 'Company',
  companySize: 'Company size',
};

const companySizeOptions = ['1-10', '11-50', '51-200', '201-500', '501-2000', '2000+'];

const missingRequiredFields = (form) => (
  Object.entries(requiredFieldLabels)
    .filter(([field]) => !String(form[field] || '').trim())
    .map(([, label]) => label)
);

export const DemoExperiencePage = ({ onNavigate }) => {
  const [form, setForm] = useState(initialForm);
  const [selectedAssessmentId, setSelectedAssessmentId] = useState(DEFAULT_DEMO_ASSESSMENT_ID);
  const [error, setError] = useState('');
  const [loadingStart, setLoadingStart] = useState(false);
  const [started, setStarted] = useState(false);
  const [demoSession, setDemoSession] = useState(null);

  const handleStart = async () => {
    const missing = missingRequiredFields(form);
    if (missing.length > 0) {
      setError(`Please complete: ${missing.join(', ')}.`);
      return;
    }
    setError('');
    setLoadingStart(true);
    try {
      const res = await assessmentsApi.startDemo({
        full_name: form.fullName.trim(),
        position: form.position.trim(),
        email: form.email.trim(),
        work_email: form.workEmail.trim(),
        company_name: form.company.trim(),
        company_size: form.companySize,
        assessment_track: selectedAssessmentId,
        marketing_consent: Boolean(form.marketingConsent),
      });
      setDemoSession(res.data);
      setStarted(true);
      window.scrollTo(0, 0);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Failed to start demo assessment.');
    } finally {
      setLoadingStart(false);
    }
  };

  if (started && demoSession) {
    return (
      <AssessmentPage
        token={demoSession.token}
        startData={demoSession}
        demoMode
        demoProfile={form}
        onDemoRestart={() => {
          setStarted(false);
          setDemoSession(null);
          window.scrollTo(0, 0);
        }}
        onJoinTaali={() => onNavigate('register')}
      />
    );
  }

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <MarketingNav onNavigate={onNavigate} />

      <div className="mx-auto max-w-[1240px] px-6 py-10 md:px-10 md:py-12">
        <div className="relative mb-6 overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-8 py-10 shadow-[var(--shadow-sm)]">
          <div
            className="pointer-events-none absolute inset-0 opacity-60 tally-bg-soft"
            style={{ maskImage: 'radial-gradient(50% 80% at 85% 50%, black, transparent 70%)' }}
          />
          <div className="relative">
            <div className="kicker">◐ TAALI INTERACTIVE DEMO</div>
            <h1 className="mt-4 font-[var(--font-display)] text-[clamp(48px,5.4vw,76px)] font-semibold leading-[0.95] tracking-[-0.03em]">
              Try a candidate
              <br />
              <em>assessment.</em>
            </h1>
            <p className="mt-4 max-w-[720px] text-[16px] leading-[1.55] text-[var(--mute)]">
              Complete this short intake, review the demo task, and run through the same assessment runtime candidates use. At the end, you&apos;ll see a short TAALI profile summary.
            </p>
            <p className="mt-2 text-[13.5px] text-[var(--mute)]">Note: this is a product demo and does not generate a full production report.</p>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[34px] font-semibold tracking-[-0.02em]">
              Your <em>details</em>
            </h2>
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="field">
                <span className="k">Full name</span>
                <input value={form.fullName} placeholder="Jane Doe" onChange={(event) => setForm((prev) => ({ ...prev, fullName: event.target.value }))} />
              </label>
              <label className="field">
                <span className="k">Position</span>
                <input value={form.position} placeholder="Engineering Manager" onChange={(event) => setForm((prev) => ({ ...prev, position: event.target.value }))} />
              </label>
              <label className="field">
                <span className="k">Email</span>
                <input type="email" value={form.email} placeholder="jane@email.com" onChange={(event) => setForm((prev) => ({ ...prev, email: event.target.value }))} />
              </label>
              <label className="field">
                <span className="k">Work email</span>
                <input type="email" value={form.workEmail} placeholder="jane@company.com" onChange={(event) => setForm((prev) => ({ ...prev, workEmail: event.target.value }))} />
              </label>
              <label className="field">
                <span className="k">Company</span>
                <input value={form.company} placeholder="Acme Inc." onChange={(event) => setForm((prev) => ({ ...prev, company: event.target.value }))} />
              </label>
              <label className="field">
                <span className="k">Company size</span>
                <select value={form.companySize} onChange={(event) => setForm((prev) => ({ ...prev, companySize: event.target.value }))}>
                  <option value="">Select size</option>
                  {companySizeOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
              </label>
            </div>
            <label className="mt-5 flex items-center gap-3 rounded-[12px] border border-dashed border-[var(--line)] p-4 text-[13px] leading-6 text-[var(--ink-2)]">
              <input
                type="checkbox"
                checked={Boolean(form.marketingConsent)}
                onChange={(event) => setForm((prev) => ({ ...prev, marketingConsent: event.target.checked }))}
              />
              Send me my demo results by email and occasional product updates (optional).
            </label>
          </div>

          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[34px] font-semibold tracking-[-0.02em]">
              Demo <em>assessment task</em>
            </h2>
            <p className="mt-2 text-[14px] leading-7 text-[var(--mute)]">
              Pick the role-specific scenario you want to run. Each option opens the same candidate runtime with a different task brief.
            </p>
            <div className="mt-5 grid gap-3">
              {DEMO_ASSESSMENTS.map((assessment) => {
                const selected = assessment.id === selectedAssessmentId;
                return (
                  <button
                    key={assessment.id}
                    type="button"
                    className={`flex items-start gap-4 rounded-[14px] border p-5 text-left transition ${selected ? 'border-[var(--purple)] bg-[color-mix(in_oklab,var(--purple)_6%,var(--bg-2))]' : 'border-[var(--line)] hover:border-[var(--purple)]'}`.trim()}
                    onClick={() => setSelectedAssessmentId(assessment.id)}
                  >
                    <div className="grow">
                      <h3 className="text-[18px] font-semibold tracking-[-0.01em]">{assessment.title}</h3>
                      <p className="mt-2 text-[13.5px] leading-6 text-[var(--ink-2)]">{assessment.description}</p>
                      <div className="mt-3 font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
                        Duration: {assessment.durationLabel} · {assessment.difficulty}
                      </div>
                    </div>
                    <div className={`grid h-7 w-7 shrink-0 place-items-center rounded-full ${selected ? 'bg-[var(--purple)] text-white' : 'bg-[var(--bg-3)] text-transparent'}`.trim()}>
                      <Check size={14} />
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {error ? (
          <div className="mt-5 rounded-[var(--radius)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </div>
        ) : null}

        <div className="mt-6 flex flex-wrap gap-3">
          <button type="button" className="btn btn-purple btn-lg" onClick={handleStart} disabled={loadingStart}>
            {loadingStart ? 'Starting demo…' : <>Start demo assessment <span className="arrow">→</span></>}
          </button>
          <button type="button" className="btn btn-outline btn-lg" onClick={() => onNavigate('register')}>
            Join TAALI
          </button>
        </div>
      </div>
    </div>
  );
};

export default DemoExperiencePage;
