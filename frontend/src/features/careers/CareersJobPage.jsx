import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, MapPin, Briefcase } from 'lucide-react';

import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageContainer,
  Select,
  Spinner,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';
import { careersApi } from './api';

const locationLabel = (job) =>
  [job.location_city, job.location_country].filter(Boolean).join(', ');

const salaryLabel = (job) => {
  if (!job.salary_min && !job.salary_max) return null;
  const cur = job.salary_currency || '';
  const per = job.salary_period ? ` / ${job.salary_period}` : '';
  const range = [job.salary_min, job.salary_max].filter(Boolean).join('–');
  return `${cur} ${range}${per}`.trim();
};

// Inject the JobPosting JSON-LD for Google for Jobs while this page is mounted.
const useJsonLd = (jsonld) => {
  useEffect(() => {
    if (!jsonld) return undefined;
    const el = document.createElement('script');
    el.type = 'application/ld+json';
    el.text = JSON.stringify(jsonld);
    document.head.appendChild(el);
    return () => { document.head.removeChild(el); };
  }, [jsonld]);
};

const EEO_DECLINE = '__decline__';
const EEO_FIELDS = [
  { key: 'gender', label: 'Gender', options: ['Female', 'Male', 'Non-binary', 'Other'] },
  { key: 'race_ethnicity', label: 'Race / ethnicity', options: ['Asian', 'Black', 'Hispanic or Latino', 'White', 'Two or more', 'Other'] },
  { key: 'veteran_status', label: 'Veteran status', options: ['Veteran', 'Not a veteran'] },
  { key: 'disability_status', label: 'Disability status', options: ['Yes', 'No'] },
];

const ScreeningField = ({ question, value, onChange }) => {
  const label = (
    <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">
      {question.prompt}{question.required ? ' *' : ''}
    </span>
  );
  if (question.kind === 'boolean') {
    return (
      <label className="block">
        {label}
        <Select value={value ?? ''} onChange={(e) => onChange(e.target.value === '' ? null : e.target.value === 'true')}>
          <option value="">Select…</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </Select>
      </label>
    );
  }
  if (question.kind === 'single_select') {
    return (
      <label className="block">
        {label}
        <Select value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">Select…</option>
          {(question.options || []).map((opt) => <option key={opt} value={opt}>{opt}</option>)}
        </Select>
      </label>
    );
  }
  if (question.kind === 'multi_select') {
    const selected = Array.isArray(value) ? value : [];
    const toggle = (opt) => onChange(selected.includes(opt) ? selected.filter((o) => o !== opt) : [...selected, opt]);
    return (
      <fieldset className="block">
        {label}
        <div className="space-y-1.5">
          {(question.options || []).map((opt) => (
            <label key={opt} className="flex items-center gap-2 text-sm text-[var(--taali-text)]">
              <input type="checkbox" checked={selected.includes(opt)} onChange={() => toggle(opt)} className="h-4 w-4 accent-[var(--taali-purple)]" />
              {opt}
            </label>
          ))}
        </div>
      </fieldset>
    );
  }
  if (question.kind === 'number') {
    return (
      <label className="block">
        {label}
        <Input type="number" value={value ?? ''} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))} />
      </label>
    );
  }
  return (
    <label className="block">
      {label}
      <Textarea value={value ?? ''} onChange={(e) => onChange(e.target.value)} className="min-h-[4rem]" />
    </label>
  );
};

const EeoStep = ({ orgSlug, applicationId, onDone }) => {
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(false);

  const submit = async (declined) => {
    setSaving(true);
    try {
      const payload = declined
        ? { declined_to_answer: true }
        : {
          gender: values.gender === EEO_DECLINE ? null : values.gender || null,
          race_ethnicity: values.race_ethnicity === EEO_DECLINE ? null : values.race_ethnicity || null,
          veteran_status: values.veteran_status === EEO_DECLINE ? null : values.veteran_status || null,
          disability_status: values.disability_status === EEO_DECLINE ? null : values.disability_status || null,
        };
      await careersApi.submitEeo(orgSlug, applicationId, payload);
    } catch {
      // Voluntary + non-blocking — never trap the candidate on an EEO error.
    } finally {
      setSaving(false);
      onDone();
    }
  };

  return (
    <Card className="px-5 py-5">
      <h2 className="text-base font-semibold text-[var(--taali-text)]">Voluntary self-identification</h2>
      <p className="mt-1 text-sm text-[var(--taali-muted)]">
        Completing this is entirely optional. Your answers are kept separate from your application and are
        <strong> never used in hiring decisions</strong> — they help us measure the fairness of our process.
      </p>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {EEO_FIELDS.map((field) => (
          <label key={field.key} className="block">
            <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">{field.label}</span>
            <Select
              value={values[field.key] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
            >
              <option value="">Select…</option>
              {field.options.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
              <option value={EEO_DECLINE}>Prefer not to say</option>
            </Select>
          </label>
        ))}
      </div>
      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="primary" disabled={saving} onClick={() => submit(false)}>Submit</Button>
        <Button variant="ghost" disabled={saving} onClick={() => submit(true)}>Prefer not to answer</Button>
      </div>
    </Card>
  );
};

// Public, no-auth job detail + apply flow: /careers/:orgSlug/:roleSlug.
export const CareersJobPage = () => {
  const { orgSlug, roleSlug } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [form, setForm] = useState({ full_name: '', email: '', phone: '' });
  const [answers, setAnswers] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [result, setResult] = useState(null); // { application_id }
  const [eeoDone, setEeoDone] = useState(false);

  useJsonLd(job?.job_posting_jsonld);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    careersApi
      .getJob(orgSlug, roleSlug)
      .then((res) => { if (!cancelled) { setJob(res); setLoading(false); } })
      .catch((err) => {
        if (!cancelled) {
          setError(err?.response?.status === 404 ? 'This role is no longer open.' : 'Failed to load the role.');
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [orgSlug, roleSlug]);

  const canSubmit = useMemo(
    () => form.full_name.trim() && (form.email.trim() || form.phone.trim()) && !submitting,
    [form, submitting],
  );

  const submitApplication = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await careersApi.apply(orgSlug, roleSlug, {
        full_name: form.full_name.trim(),
        email: form.email.trim() || null,
        phone: form.phone.trim() || null,
        answers,
      });
      setResult(res);
    } catch (err) {
      const status = err?.response?.status;
      setSubmitError(
        status === 429 ? 'Too many attempts — please try again later.'
          : status === 503 ? 'Applications are not open for this role right now.'
            : status === 422 ? 'Please provide your name and an email or phone number.'
              : 'Something went wrong submitting your application.',
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <div className="flex min-h-screen items-center justify-center"><Spinner /></div>;
  if (error) return <PageContainer><EmptyState title="Role unavailable" description={error} /></PageContainer>;

  const salary = salaryLabel(job);

  return (
    <PageContainer width="default">
      <button
        type="button"
        onClick={() => navigate(`/careers/${encodeURIComponent(orgSlug)}`)}
        className="mb-4 inline-flex items-center gap-1 text-sm text-[var(--taali-muted)] hover:text-[var(--taali-text)]"
      >
        <ArrowLeft size={15} /> All roles
      </button>

      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-[var(--taali-text)]">{job.title}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-[var(--taali-muted)]">
          {job.department ? <span className="inline-flex items-center gap-1"><Briefcase size={14} />{job.department}</span> : null}
          {locationLabel(job) ? <span className="inline-flex items-center gap-1"><MapPin size={14} />{locationLabel(job)}</span> : null}
          {job.employment_type ? <Badge variant="muted">{job.employment_type}</Badge> : null}
          {job.workplace_type ? <Badge variant="info">{job.workplace_type}</Badge> : null}
        </div>
        {salary ? <p className="mt-2 text-sm font-medium text-[var(--taali-text)]">{salary}</p> : null}
      </header>

      {job.description ? (
        <Card className="mb-6 whitespace-pre-wrap px-5 py-5 text-sm leading-relaxed text-[var(--taali-text)]">
          {job.description}
        </Card>
      ) : null}

      {result ? (
        eeoDone ? (
          <Card className="px-5 py-6 text-center">
            <h2 className="text-lg font-semibold text-[var(--taali-text)]">Application received</h2>
            <p className="mt-1 text-sm text-[var(--taali-muted)]">Thank you for applying. We&apos;ll be in touch.</p>
          </Card>
        ) : (
          <>
            <Card className="mb-4 px-5 py-4 text-sm text-[var(--taali-text)]">
              <span className="font-medium">Thanks — your application is in.</span> One optional step below.
            </Card>
            <EeoStep orgSlug={orgSlug} applicationId={result.application_id} onDone={() => setEeoDone(true)} />
          </>
        )
      ) : (
        <Card className="px-5 py-5">
          <h2 className="text-base font-semibold text-[var(--taali-text)]">Apply for this role</h2>
          <div className="mt-4 space-y-4">
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">Full name *</span>
              <Input value={form.full_name} onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))} placeholder="Your name" />
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">Email</span>
                <Input type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} placeholder="you@example.com" />
              </label>
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">Phone</span>
                <Input value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} placeholder="+…" />
              </label>
            </div>
            <p className="text-xs text-[var(--taali-muted)]">Provide at least an email or a phone number.</p>

            {(job.screening_questions || []).map((q) => (
              <ScreeningField
                key={q.id}
                question={q}
                value={answers[String(q.id)]}
                onChange={(val) => setAnswers((a) => ({ ...a, [String(q.id)]: val }))}
              />
            ))}

            {submitError ? (
              <Card className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">
                {submitError}
              </Card>
            ) : null}

            <Button variant="primary" size="lg" disabled={!canSubmit} onClick={submitApplication}>
              {submitting ? 'Submitting…' : 'Submit application'}
            </Button>
          </div>
        </Card>
      )}
    </PageContainer>
  );
};

export default CareersJobPage;
