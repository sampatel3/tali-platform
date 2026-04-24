import React, { useMemo, useState } from 'react';
import { Check, PhoneCall } from 'lucide-react';

import { DEMO_ASSESSMENTS, DEFAULT_DEMO_ASSESSMENT_ID, getDemoAssessmentById } from './demoAssessments';
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

const SignalRow = ({ label, value, detail }) => (
  <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4">
    <div className="flex items-center justify-between gap-4">
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

  const handleRequestCallback = async () => {
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
      window.scrollTo(0, 0);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Failed to submit callback request.');
    } finally {
      setLoadingRequest(false);
    }
  };

  if (requestSubmitted) {
    return (
      <div className="min-h-screen bg-[var(--bg)] text-[var(--ink)]">
        <MarketingNav onNavigate={onNavigate} />

        <div className="mx-auto max-w-[1120px] px-6 py-12 md:px-10 md:py-16">
          <div className="rounded-[var(--radius-xl)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-lg)] md:p-10">
            <div className="kicker">CALLBACK REQUEST RECEIVED</div>
            <h1 className="mt-4 font-[var(--font-display)] text-[clamp(42px,5vw,64px)] font-semibold leading-[0.96] tracking-[-0.03em]">
              Thanks, we&apos;ll reach out <em>soon</em>.
            </h1>
            <p className="mt-4 max-w-[760px] text-[16px] leading-[1.6] text-[var(--mute)]">
              We saved your details for the <span className="text-[var(--ink)]">{selectedAssessment.title}</span> walkthrough. A Taali teammate will follow up using{' '}
              <span className="text-[var(--ink)]">{form.workEmail}</span> to schedule a callback and walk you through the candidate flow, runtime signal capture, and recruiter report.
            </p>

            <div className="mt-8 grid gap-4 md:grid-cols-3">
              {[
                ['Track', selectedAssessment.title],
                ['Company', form.company],
                ['Callback', form.workEmail],
              ].map(([label, value]) => (
                <div key={label} className="rounded-[16px] border border-[var(--line)] bg-[var(--bg)] p-5">
                  <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--mute)]">{label}</div>
                  <div className="mt-2 text-[17px] font-semibold tracking-[-0.01em]">{value}</div>
                </div>
              ))}
            </div>

            <div className="mt-8 grid gap-4 lg:grid-cols-[1.1fr_.9fr]">
              <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg)] p-6">
                <div className="flex items-center gap-3">
                  <div className="grid h-11 w-11 place-items-center rounded-[12px] bg-[var(--purple-soft)] text-[var(--purple)]">
                    <PhoneCall size={18} />
                  </div>
                  <div>
                    <div className="font-[var(--font-display)] text-[24px] tracking-[-0.02em]">What happens <em>next</em></div>
                    <div className="mt-1 text-[13px] text-[var(--mute)]">A short, tailored walkthrough instead of an unguided live demo.</div>
                  </div>
                </div>
                <div className="mt-5 space-y-4">
                  {[
                    'We review the selected assessment example and explain what the candidate sees.',
                    'We show the prompt-quality, recovery, and independence signals your team receives back.',
                    'We leave you with a recommended pilot flow for one of your open roles.',
                  ].map((item) => (
                    <div key={item} className="grid grid-cols-[22px_1fr] gap-3 border-b border-[var(--line-2)] pb-4 last:border-b-0 last:pb-0">
                      <div className="grid h-[22px] w-[22px] place-items-center rounded-full bg-[var(--purple-soft)] text-[var(--purple)]">
                        <Check size={12} strokeWidth={2.4} />
                      </div>
                      <p className="text-[13.5px] leading-6 text-[var(--ink-2)]">{item}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-[var(--radius-lg)] bg-[var(--ink)] p-6 text-[var(--bg)]">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple-2)]">SELECTED EXAMPLE</div>
                <h2 className="mt-3 font-[var(--font-display)] text-[28px] tracking-[-0.02em]">{selectedAssessment.title}</h2>
                <p className="mt-3 text-[14px] leading-7 text-white/70">{selectedAssessment.description}</p>
                <div className="mt-5 space-y-3">
                  {[
                    ['Role', selectedAssessment.role],
                    ['Stack', selectedAssessment.stack],
                    ['Deliverable', selectedAssessment.deliverable],
                  ].map(([label, value]) => (
                    <div key={label} className="flex items-center justify-between border-b border-white/10 pb-3 last:border-b-0 last:pb-0">
                      <span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-white/50">{label}</span>
                      <span className="text-[13px] text-white/80">{value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="mt-8 flex flex-wrap gap-3">
              <button type="button" className="btn btn-primary btn-lg" onClick={() => onNavigate('landing')}>
                Back to landing
              </button>
              <button
                type="button"
                className="btn btn-outline btn-lg"
                onClick={() => {
                  setRequestSubmitted(false);
                  window.scrollTo(0, 0);
                }}
              >
                Review another example
              </button>
            </div>
          </div>
        </div>
      </div>
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
            <div className="kicker">◐ TAALI DEMO REQUEST</div>
            <h1 className="mt-4 font-[var(--font-display)] text-[clamp(48px,5.4vw,76px)] font-semibold leading-[0.95] tracking-[-0.03em]">
              Review a candidate
              <br />
              <em>assessment.</em>
            </h1>
            <p className="mt-4 max-w-[760px] text-[16px] leading-[1.6] text-[var(--mute)]">
              Pick the sample assessment you want to review, then leave your details and we&apos;ll call you back with a tailored walkthrough. This page no longer launches a live demo runtime directly.
            </p>
            <p className="mt-2 text-[13.5px] text-[var(--mute)]">You&apos;ll see the candidate flow and the recruiter signal your team gets back before we schedule time.</p>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-[0.92fr_1.08fr]">
          <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
            <h2 className="font-[var(--font-display)] text-[34px] font-semibold tracking-[-0.02em]">
              Your <em>details</em>
            </h2>
            <p className="mt-2 text-[14px] leading-7 text-[var(--mute)]">
              We&apos;ll use these details to tailor the walkthrough to your hiring flow and follow up with the right person.
            </p>
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
              Send me the assessment summary by email and occasional product updates (optional).
            </label>

            {error ? (
              <div className="mt-5 rounded-[var(--radius)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
                {error}
              </div>
            ) : null}

            <div className="mt-6 flex flex-wrap gap-3">
              <button type="button" className="btn btn-purple btn-lg" onClick={handleRequestCallback} disabled={loadingRequest}>
                {loadingRequest ? 'Submitting request…' : 'Request callback'}
              </button>
              <button type="button" className="btn btn-outline btn-lg" onClick={() => onNavigate('landing')}>
                Back to landing
              </button>
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
              <h2 className="font-[var(--font-display)] text-[34px] font-semibold tracking-[-0.02em]">
                Example <em>assessment</em>
              </h2>
              <p className="mt-2 text-[14px] leading-7 text-[var(--mute)]">
                Choose the assessment example you want us to cover on the callback. We&apos;ll use it to walk through the candidate view and recruiter signal.
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

            <div className="grid gap-4 xl:grid-cols-[1.05fr_.95fr]">
              <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">CANDIDATE VIEW</div>
                <h3 className="mt-3 font-[var(--font-display)] text-[30px] tracking-[-0.02em]">
                  What the candidate <em>sees</em>.
                </h3>
                <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">{selectedAssessment.candidateLead}</p>

                <div className="mt-5 rounded-[var(--radius-lg)] bg-[var(--ink)] p-5 text-[var(--bg)]">
                  <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple-2)]">APPLYING FOR</div>
                  <h4 className="mt-3 font-[var(--font-display)] text-[22px] tracking-[-0.02em]">{selectedAssessment.role}</h4>
                  <p className="mt-2 text-[13px] leading-6 text-white/70">{selectedAssessment.team}</p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <span className="chip purple">Assessment 1 of 1</span>
                    <span className="chip">{selectedAssessment.tools}</span>
                  </div>
                </div>

                <div className="mt-5 grid gap-3 sm:grid-cols-3">
                  {[
                    ['Duration', selectedAssessment.durationLabel],
                    ['Stack', selectedAssessment.stack],
                    ['Deliverable', selectedAssessment.deliverable],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] p-4">
                      <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">{label}</div>
                      <div className="mt-2 text-[14px] font-medium leading-6 text-[var(--ink-2)]">{value}</div>
                    </div>
                  ))}
                </div>

                <div className="mt-5 space-y-4">
                  {selectedAssessment.candidateChecklist.map((item) => (
                    <div key={item} className="grid grid-cols-[22px_1fr] gap-3 border-b border-[var(--line-2)] pb-4 last:border-b-0 last:pb-0">
                      <div className="grid h-[22px] w-[22px] place-items-center rounded-full bg-[var(--purple-soft)] text-[var(--purple)]">
                        <Check size={12} strokeWidth={2.4} />
                      </div>
                      <p className="text-[13.5px] leading-6 text-[var(--ink-2)]">{item}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-4">
                <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                  <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">RECRUITER SIGNAL</div>
                  <h3 className="mt-3 font-[var(--font-display)] text-[28px] tracking-[-0.02em]">
                    What the recruiter gets <em>back</em>.
                  </h3>
                  <div className="mt-5 space-y-3">
                    {selectedAssessment.recruiterSignals.map((signal) => (
                      <SignalRow key={signal.label} label={signal.label} value={signal.value} detail={signal.detail} />
                    ))}
                  </div>
                </div>

                <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                  <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--purple)]">CALLBACK WALKTHROUGH</div>
                  <h3 className="mt-3 font-[var(--font-display)] text-[24px] tracking-[-0.02em]">
                    We&apos;ll tailor the <em>demo</em> to your role.
                  </h3>
                  <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">
                    On the callback, we connect this assessment example to your hiring flow and show exactly how Taali scores prompt quality, recovery, and ownership.
                  </p>
                  <div className="mt-5 rounded-[14px] bg-[var(--bg)] p-4 text-[13px] leading-6 text-[var(--ink-2)]">
                    No unguided sandbox. No live access to a demo runtime. Just the assessment example, the recruiter evidence, and a walkthrough tailored to your open role.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DemoExperiencePage;
