import React, { useState } from 'react';
import { ArrowRight, Check } from 'lucide-react';

import AssessmentPage from '../assessment_runtime/AssessmentPage';
import { DEMO_ASSESSMENTS, DEFAULT_DEMO_ASSESSMENT_ID } from './demoAssessments';
import { Logo } from '../../shared/ui/Branding';
import { assessments as assessmentsApi } from '../../shared/api';
import {
  Button,
  Card,
  Input,
  Panel,
  Select,
} from '../../shared/ui/TaaliPrimitives';
import { GlobalThemeToggle } from '../../shared/ui/GlobalThemeToggle';

const initialForm = {
  fullName: '',
  position: '',
  email: '',
  workEmail: '',
  company: '',
  companySize: '',
  marketingConsent: true,
};

const requiredFieldLabels = {
  fullName: 'Full name',
  position: 'Position',
  email: 'Email',
  workEmail: 'Work email',
  company: 'Company',
  companySize: 'Company size',
};

const companySizeOptions = [
  '1-10',
  '11-50',
  '51-200',
  '201-500',
  '501-2000',
  '2000+',
];

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
    <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
      <nav className="border-b-2 border-black bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
          <div className="flex items-center gap-2">
            <GlobalThemeToggle compact className="!px-2.5 !py-2" />
            <Button type="button" variant="secondary" size="sm" className="font-mono" onClick={() => onNavigate('landing')}>
              Back to landing
            </Button>
            <Button type="button" variant="primary" size="sm" className="font-mono" onClick={() => onNavigate('register')}>
              Join TAALI
            </Button>
          </div>
        </div>
      </nav>

      <div className="mx-auto max-w-6xl px-6 py-10">
        <Panel className="p-6">
          <div className="mb-2 inline-flex border-2 border-black bg-[var(--taali-purple)] px-3 py-1 font-mono text-xs font-bold text-white">
            INTERACTIVE DEMO
          </div>
          <h1 className="text-4xl font-bold">Try a candidate assessment</h1>
          <p className="mt-3 max-w-4xl font-mono text-sm text-[var(--taali-muted)]">
            Complete this short intake, review the demo task, and run through the same assessment runtime candidates use.
            At the end, you&apos;ll see a short TAALI profile summary.
          </p>
          <p className="mt-2 font-mono text-xs text-[var(--taali-muted)]">
            Note: this is a product demo and does not generate a full production report.
          </p>
        </Panel>

        <div className="mt-6 grid gap-6 lg:grid-cols-[1.1fr_1fr]">
          <Panel className="p-5">
            <h2 className="text-2xl font-bold">Your details</h2>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <label className="grid gap-1">
                <span className="font-mono text-xs font-bold">Full name</span>
                <Input
                  value={form.fullName}
                  onChange={(event) => setForm((prev) => ({ ...prev, fullName: event.target.value }))}
                  placeholder="Jane Doe"
                />
              </label>
              <label className="grid gap-1">
                <span className="font-mono text-xs font-bold">Position</span>
                <Input
                  value={form.position}
                  onChange={(event) => setForm((prev) => ({ ...prev, position: event.target.value }))}
                  placeholder="Engineering Manager"
                />
              </label>
              <label className="grid gap-1">
                <span className="font-mono text-xs font-bold">Email</span>
                <Input
                  type="email"
                  value={form.email}
                  onChange={(event) => setForm((prev) => ({ ...prev, email: event.target.value }))}
                  placeholder="jane@email.com"
                />
              </label>
              <label className="grid gap-1">
                <span className="font-mono text-xs font-bold">Work email</span>
                <Input
                  type="email"
                  value={form.workEmail}
                  onChange={(event) => setForm((prev) => ({ ...prev, workEmail: event.target.value }))}
                  placeholder="jane@company.com"
                />
              </label>
              <label className="grid gap-1">
                <span className="font-mono text-xs font-bold">Company</span>
                <Input
                  value={form.company}
                  onChange={(event) => setForm((prev) => ({ ...prev, company: event.target.value }))}
                  placeholder="Acme Inc."
                />
              </label>
              <label className="grid gap-1">
                <span className="font-mono text-xs font-bold">Company size</span>
                <Select
                  value={form.companySize}
                  onChange={(event) => setForm((prev) => ({ ...prev, companySize: event.target.value }))}
                >
                  <option value="">Select size</option>
                  {companySizeOptions.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </Select>
              </label>
              <label className="col-span-2 flex items-start gap-2 border border-[var(--taali-border)] bg-[var(--taali-surface)] px-3 py-2">
                <input
                  type="checkbox"
                  checked={Boolean(form.marketingConsent)}
                  onChange={(event) => setForm((prev) => ({ ...prev, marketingConsent: event.target.checked }))}
                  className="mt-1 h-4 w-4"
                />
                <span className="font-mono text-xs text-[var(--taali-muted)]">
                  I agree to receive TAALI follow-up emails about assessment outcomes and product updates.
                </span>
              </label>
            </div>
          </Panel>

          <Panel className="p-5">
            <h2 className="text-2xl font-bold">Demo assessment task</h2>
            <p className="mt-2 font-mono text-xs text-[var(--taali-muted)]">
              These demo tasks mirror the current live assessment catalog.
            </p>
            <div className="mt-4 grid gap-3">
              {DEMO_ASSESSMENTS.map((assessment) => {
                const isSelected = assessment.id === selectedAssessmentId;
                return (
                  <button
                    key={assessment.id}
                    type="button"
                    onClick={() => setSelectedAssessmentId(assessment.id)}
                    className={`text-left border-2 p-4 transition ${
                      isSelected
                        ? 'border-black bg-[var(--taali-purple)]/10'
                        : 'border-[var(--taali-border)] bg-[var(--taali-surface)] hover:border-black'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-lg font-bold">{assessment.title}</h3>
                        <p className="mt-1 font-mono text-sm text-[var(--taali-muted)]">{assessment.description}</p>
                      </div>
                      {isSelected ? (
                        <span className="inline-flex h-6 w-6 items-center justify-center border-2 border-black bg-[var(--taali-purple)] text-white">
                          <Check size={14} />
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-3 flex gap-2 font-mono text-xs text-[var(--taali-muted)]">
                      <span>Duration: {assessment.durationLabel}</span>
                      <span>•</span>
                      <span>{assessment.difficulty}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </Panel>
        </div>

        {error ? (
          <Card className="mt-5 border-red-300 bg-red-50 p-4">
            <p className="font-mono text-sm text-red-700">{error}</p>
          </Card>
        ) : null}

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Button type="button" variant="primary" size="lg" onClick={handleStart} disabled={loadingStart}>
            {loadingStart ? 'Starting demo...' : (
              <>
                Start demo assessment
                <ArrowRight size={16} />
              </>
            )}
          </Button>
          <Button type="button" variant="secondary" size="lg" onClick={() => onNavigate('register')}>
            Join TAALI
          </Button>
        </div>
      </div>
    </div>
  );
};
