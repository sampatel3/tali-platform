import React, { useMemo, useState } from 'react';
import { Check } from 'lucide-react';

import {
  SettingsPreviewCard,
  ShowcaseCtaBand,
  StandingReportPreviewCard,
  TaskBriefCard,
  WelcomePreviewCard,
  WorkspaceReplayFrame,
} from '../../components/ProductPreviewFrames';
import { assessments as assessmentsApi } from '../../shared/api';
import { MarketingNav } from '../../shared/layout/TaaliLayout';
import {
  DEFAULT_DEMO_ASSESSMENT_ID,
  DEMO_ASSESSMENTS,
  getDemoAssessmentById,
} from './demoAssessments';

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

const SignalRow = ({ label, value, detail }) => (
  <div className="rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-4">
    <div className="flex items-center justify-between gap-3">
      <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">{label}</div>
      <div className="font-[var(--font-mono)] text-[12px] text-[var(--ink)]">{value}</div>
    </div>
    <div className="bar mt-3"><i style={{ width: `${value}%` }} /></div>
    <p className="mt-3 text-[13px] leading-6 text-[var(--ink-2)]">{detail}</p>
  </div>
);

export const DemoExperiencePage = ({ onNavigate }) => {
  const [form, setForm] = useState(initialForm);
  const [selectedAssessmentId, setSelectedAssessmentId] = useState(DEFAULT_DEMO_ASSESSMENT_ID);
  const [error, setError] = useState('');
  const [loadingRequest, setLoadingRequest] = useState(false);
  const [requestSubmitted, setRequestSubmitted] = useState(false);

  const selectedAssessment = useMemo(
    () => getDemoAssessmentById(selectedAssessmentId),
    [selectedAssessmentId],
  );

  const handleRequestShowcase = async () => {
    const missing = missingRequiredFields(form);
    if (missing.length > 0) {
      setError(`Please complete: ${missing.join(', ')}.`);
      return;
    }

    setError('');
    setLoadingRequest(true);
    try {
      await assessmentsApi.startDemo({
        full_name: form.fullName.trim(),
        position: form.position.trim(),
        email: form.email.trim(),
        work_email: form.workEmail.trim(),
        company_name: form.company.trim(),
        company_size: form.companySize,
        assessment_track: selectedAssessmentId,
        marketing_consent: Boolean(form.marketingConsent),
      });
      setRequestSubmitted(true);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Failed to submit demo request.');
    } finally {
      setLoadingRequest(false);
    }
  };

  if (requestSubmitted) {
    return (
      <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
        <MarketingNav onNavigate={onNavigate} />

        <div className="mx-auto max-w-[1280px] px-6 py-10 md:px-10 md:py-14">
          <div className="rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-lg)] md:p-10">
            <div className="kicker">DEMO REQUEST RECEIVED</div>
            <h1 className="mt-4 font-[var(--font-display)] text-[clamp(44px,5.4vw,72px)] font-semibold leading-[0.95] tracking-[-0.04em]">
              Here&apos;s the product <em>flow</em>.
            </h1>
            <p className="mt-4 max-w-[860px] text-[16px] leading-[1.7] text-[var(--mute)]">
              We saved your details for the <span className="text-[var(--ink)]">{selectedAssessment.title}</span> walkthrough.
              A Taali teammate will follow up using <span className="text-[var(--ink)]">{form.workEmail}</span>.
              In the meantime, this is the exact product sequence we&apos;ll walk through on the call.
            </p>

            <div className="mt-7 grid gap-4 md:grid-cols-3">
              {[
                ['Role', selectedAssessment.role],
                ['Company', form.company],
                ['Showcase', selectedAssessment.title],
              ].map(([label, value]) => (
                <div key={label} className="rounded-[18px] border border-[var(--line)] bg-[var(--bg)] p-5">
                  <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--mute)]">{label}</div>
                  <div className="mt-2 text-[18px] font-semibold tracking-[-0.01em]">{value}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-6 space-y-6">
            <TaskBriefCard />
            <WelcomePreviewCard />
            <WorkspaceReplayFrame />
            <StandingReportPreviewCard />
            <SettingsPreviewCard />
            <ShowcaseCtaBand
              primaryLabel="Review another role"
              secondaryLabel="Sign in to Taali"
              onPrimaryAction={() => {
                setRequestSubmitted(false);
                window.scrollTo({ top: 0, behavior: 'smooth' });
              }}
              onSecondaryAction={() => onNavigate('login')}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
      <MarketingNav onNavigate={onNavigate} />

      <div className="mx-auto max-w-[1280px] px-6 py-10 md:px-10 md:py-14">
        <div className="grid gap-6 xl:grid-cols-[1.02fr_.98fr]">
          <div className="space-y-6">
            <div className="relative overflow-hidden rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] px-8 py-10 shadow-[var(--shadow-sm)]">
              <div
                className="pointer-events-none absolute inset-0 opacity-60 tally-bg-soft"
                style={{ maskImage: 'radial-gradient(60% 80% at 85% 45%, black, transparent 70%)' }}
              />
              <div className="relative">
                <div className="kicker">04 · DEMO / ONBOARDING</div>
                <h1 className="mt-4 font-[var(--font-display)] text-[clamp(52px,6vw,84px)] font-semibold leading-[0.94] tracking-[-0.04em]">
                  See a candidate
                  <br />
                  <em>assessment.</em>
                </h1>
                <p className="mt-4 max-w-[700px] text-[16px] leading-[1.7] text-[var(--mute)]">
                  Walk through the exact runtime your candidates use, then see the report your team gets back.
                  This is a product showcase, not a gated thank-you page and not a live runtime dropped on you cold.
                </p>
                <div className="mt-6 space-y-3">
                  {[
                    'Review the real task brief and candidate welcome flow.',
                    'Replay the workspace with repo, tests, and Claude side-by-side.',
                    'Finish on the standing report recruiters actually use to make a call.',
                  ].map((item) => (
                    <div key={item} className="flex items-start gap-3 text-[14px] leading-7 text-[var(--ink-2)]">
                      <span className="mt-1 grid h-6 w-6 place-items-center rounded-full bg-[var(--purple-soft)] text-[var(--purple)]">
                        <Check size={13} strokeWidth={2.5} />
                      </span>
                      <span>{item}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <TaskBriefCard />

            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <div className="kicker m-0">ASSESSMENT EXAMPLE</div>
                  <h2 className="mt-3 font-[var(--font-display)] text-[34px] font-semibold tracking-[-0.03em]">
                    Choose the <em>showcase</em>.
                  </h2>
                </div>
                <p className="max-w-[420px] text-[13.5px] leading-6 text-[var(--mute)]">
                  We&apos;ll tailor the walkthrough to the role you care about, then show the same recruiter report structure at the end.
                </p>
              </div>

              <div className="mt-5 grid gap-3">
                {DEMO_ASSESSMENTS.map((assessment) => {
                  const selected = assessment.id === selectedAssessmentId;
                  return (
                    <button
                      key={assessment.id}
                      type="button"
                      className={`rounded-[18px] border p-5 text-left transition ${
                        selected
                          ? 'border-[var(--purple)] bg-[color-mix(in_oklab,var(--purple)_6%,var(--bg-2))]'
                          : 'border-[var(--line)] hover:border-[var(--purple)]'
                      }`.trim()}
                      onClick={() => setSelectedAssessmentId(assessment.id)}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div>
                          <div className="font-[var(--font-display)] text-[25px] tracking-[-0.03em]">{assessment.title}</div>
                          <p className="mt-2 text-[13.5px] leading-6 text-[var(--ink-2)]">{assessment.description}</p>
                          <div className="mt-3 flex flex-wrap gap-2 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">
                            <span>{assessment.role}</span>
                            <span>•</span>
                            <span>{assessment.durationLabel}</span>
                            <span>•</span>
                            <span>{assessment.stack}</span>
                          </div>
                        </div>
                        <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${selected ? 'bg-[var(--purple)] text-white' : 'bg-[var(--bg-3)] text-transparent'}`.trim()}>
                          <Check size={15} />
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-[1.1fr_.9fr]">
              <div className="rounded-[var(--radius-lg)] bg-[var(--ink)] p-6 text-[var(--bg)] shadow-[var(--shadow-sm)]">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple-2)]">CANDIDATE VIEW</div>
                <h3 className="mt-3 font-[var(--font-display)] text-[32px] tracking-[-0.03em]">{selectedAssessment.role}</h3>
                <p className="mt-3 text-[14px] leading-7 text-white/72">{selectedAssessment.candidateLead}</p>
                <div className="mt-5 space-y-3 text-[13px] leading-6 text-white/80">
                  {selectedAssessment.candidateChecklist.map((item) => (
                    <div key={item} className="flex items-start gap-3 border-b border-white/10 pb-3 last:border-b-0 last:pb-0">
                      <span className="mt-1 h-5 w-5 rounded-full bg-white/12" />
                      <span>{item}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-4">
                {selectedAssessment.recruiterSignals.map((signal) => (
                  <SignalRow key={signal.label} label={signal.label} value={signal.value} detail={signal.detail} />
                ))}
              </div>
            </div>
          </div>

          <div>
            <div className="rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg)] p-8 shadow-[var(--shadow-lg)] xl:sticky xl:top-28">
              <div className="kicker">BOOK A DEMO</div>
              <h2 className="mt-4 font-[var(--font-display)] text-[clamp(36px,4vw,56px)] font-semibold leading-[0.96] tracking-[-0.04em]">
                Start the <em>showcase</em>.
              </h2>
              <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">
                Enter your details and we&apos;ll tailor the walkthrough to your hiring flow before you see the product sequence.
              </p>

              <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-2">
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

              <label className="mt-5 flex items-center gap-3 rounded-[14px] border border-dashed border-[var(--line)] p-4 text-[13px] leading-6 text-[var(--ink-2)]">
                <input
                  type="checkbox"
                  checked={Boolean(form.marketingConsent)}
                  onChange={(event) => setForm((prev) => ({ ...prev, marketingConsent: event.target.checked }))}
                />
                Send me the assessment summary by email and occasional product updates (optional).
              </label>

              {error ? (
                <div className="mt-5 rounded-[14px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
                  {error}
                </div>
              ) : null}

              <div className="mt-6 flex flex-col gap-3">
                <button type="button" className="btn btn-primary btn-lg justify-center" onClick={handleRequestShowcase} disabled={loadingRequest}>
                  {loadingRequest ? 'Starting showcase…' : 'See the showcase →'}
                </button>
                <button type="button" className="btn btn-outline btn-lg justify-center" onClick={() => onNavigate('landing')}>
                  Back to landing
                </button>
                <button type="button" className="text-sm text-[var(--mute)] transition hover:text-[var(--ink)]" onClick={() => onNavigate('login')}>
                  Already have an account? Sign in
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DemoExperiencePage;
