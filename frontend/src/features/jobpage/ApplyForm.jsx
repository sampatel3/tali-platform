import React, { useState } from 'react';

import { publicJobApi } from '../requisitions/api';

// Public, no-login apply form rendered on a job page when the payload says the
// posting is taking applications (`accepts_applications: true`). Candidate-facing
// copy is plain and warm — no internal jargon, no scoring/knockout detail.
//
// On success we show a friendly confirmation, and — if the apply response
// carried an `eeo_token` — an OPTIONAL, clearly-dismissible voluntary self-ID
// step (a separate submit to the EEO endpoint). Everything is voluntary.

const RESUME_EXTS = ['pdf', 'docx']; // mirrors the backend's allowed set
const RESUME_MAX_BYTES = 5 * 1024 * 1024; // 5 MB — mirrors document_service.MAX_FILE_SIZE

const extOf = (name) => {
  const n = String(name || '');
  return n.includes('.') ? n.split('.').pop().toLowerCase() : '';
};

// One screening question, rendered by kind. `value` is this question's current
// answer; `onChange(next)` records it. Answers are keyed by String(question.id)
// in the parent, matching the backend's `answers.get(str(q.id))`.
function QuestionField({ q, value, onChange }) {
  const label = (
    <span className="pjp-field-label">
      {q.prompt}
      {q.required ? <span className="pjp-req" aria-hidden> *</span> : null}
    </span>
  );

  if (q.kind === 'boolean') {
    return (
      <label className="pjp-field">
        {label}
        <select value={value ?? ''} onChange={(e) => onChange(e.target.value || undefined)}>
          <option value="">Select…</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </label>
    );
  }

  if (q.kind === 'single_select') {
    return (
      <label className="pjp-field">
        {label}
        <select value={value ?? ''} onChange={(e) => onChange(e.target.value || undefined)}>
          <option value="">Select…</option>
          {(q.options || []).map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </label>
    );
  }

  if (q.kind === 'multi_select') {
    const selected = Array.isArray(value) ? value : [];
    const toggle = (opt) => {
      const next = selected.includes(opt)
        ? selected.filter((v) => v !== opt)
        : [...selected, opt];
      onChange(next.length ? next : undefined);
    };
    return (
      <div className="pjp-field">
        {label}
        <div className="pjp-checks">
          {(q.options || []).map((opt) => (
            <label key={opt} className="pjp-check">
              <input type="checkbox" checked={selected.includes(opt)} onChange={() => toggle(opt)} />
              {opt}
            </label>
          ))}
        </div>
      </div>
    );
  }

  if (q.kind === 'number') {
    return (
      <label className="pjp-field">
        {label}
        <input
          type="number"
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)}
        />
      </label>
    );
  }

  // text (and any unknown kind) → free-text
  return (
    <label className="pjp-field">
      {label}
      <textarea
        rows={3}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || undefined)}
      />
    </label>
  );
}

const EEO_CATEGORIES = [
  { key: 'gender', label: 'Gender', options: ['Female', 'Male', 'Non-binary', 'Prefer to self-describe'] },
  {
    key: 'race_ethnicity',
    label: 'Race / ethnicity',
    options: ['Asian', 'Black or African American', 'Hispanic or Latino', 'White', 'Two or more races', 'Other'],
  },
  { key: 'veteran_status', label: 'Veteran status', options: ['Yes', 'No'] },
  { key: 'disability_status', label: 'Disability status', options: ['Yes', 'No'] },
];

// The OPTIONAL voluntary self-ID step. Separate submit; fully dismissible.
function EEOStep({ token, onDone }) {
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);

  const submit = async (declined) => {
    setBusy(true);
    try {
      // Declining sends ONLY the decline marker — any values the applicant
      // selected before choosing Skip are deliberately dropped, so no protected
      // characteristic ever leaves the browser on a decline.
      const payload = declined
        ? { declined_to_answer: true }
        : { ...values, declined_to_answer: false };
      await publicJobApi.submitEeo(token, payload);
    } catch {
      // Voluntary + best-effort: never block or alarm the candidate on failure.
    } finally {
      onDone();
    }
  };

  return (
    <div className="pjp-eeo" data-testid="eeo-step">
      <h3 className="pjp-eeo-title">A few optional questions</h3>
      <p className="pjp-muted">
        Sharing this is entirely voluntary and never affects your application. It
        helps the employer understand the reach of their hiring. You can skip it.
      </p>
      {EEO_CATEGORIES.map((cat) => (
        <label key={cat.key} className="pjp-field">
          <span className="pjp-field-label">{cat.label}</span>
          <select
            value={values[cat.key] ?? ''}
            onChange={(e) => setValues((v) => ({ ...v, [cat.key]: e.target.value || undefined }))}
          >
            <option value="">Prefer not to say</option>
            {cat.options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </label>
      ))}
      <div className="pjp-eeo-actions">
        <button type="button" className="pjp-apply-btn" disabled={busy} onClick={() => submit(false)}>
          Submit
        </button>
        <button type="button" className="pjp-link-btn" disabled={busy} onClick={() => submit(true)}>
          Skip
        </button>
      </div>
    </div>
  );
}

export function ApplyForm({ token, questions = [], organizationName, resumeRequired = false }) {
  const [form, setForm] = useState({ full_name: '', email: '', phone: '' });
  const [answers, setAnswers] = useState({});
  const [resume, setResume] = useState(null);
  const [phase, setPhase] = useState('form'); // form | submitting | done
  const [error, setError] = useState(null);
  const [eeoToken, setEeoToken] = useState(null);
  const [eeoDone, setEeoDone] = useState(false);

  const setAnswer = (qid, value) =>
    setAnswers((a) => {
      const next = { ...a };
      if (value === undefined) delete next[qid];
      else next[qid] = value;
      return next;
    });

  const onResume = (e) => {
    const file = e.target.files && e.target.files[0];
    setError(null);
    if (!file) { setResume(null); return; }
    if (!RESUME_EXTS.includes(extOf(file.name))) {
      setError('Please upload your resume as a PDF or Word (.docx) file.');
      setResume(null);
      return;
    }
    if (file.size > RESUME_MAX_BYTES) {
      setError('That file is too large — please keep your resume under 5 MB.');
      setResume(null);
      return;
    }
    setResume(file);
  };

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!form.full_name.trim()) { setError('Please tell us your name.'); return; }
    if (!form.email.trim() && !form.phone.trim()) {
      setError('Please give us an email address or phone number so we can reach you.');
      return;
    }
    if (resumeRequired && !resume) {
      setError('Please upload your resume so your application can be evaluated.');
      return;
    }
    const missing = questions.filter(
      (q) => q.required && (answers[String(q.id)] === undefined || answers[String(q.id)] === ''),
    );
    if (missing.length) { setError('Please answer the required questions.'); return; }

    setPhase('submitting');
    try {
      const res = await publicJobApi.apply(token, {
        full_name: form.full_name.trim(),
        email: form.email.trim() || undefined,
        phone: form.phone.trim() || undefined,
        answers,
        resume,
      });
      setEeoToken(res && res.eeo_token ? res.eeo_token : null);
      setPhase('done');
    } catch (err) {
      const status = err && err.response && err.response.status;
      if (status === 429) setError("You've applied a few times already — please try again later.");
      else setError('Something went wrong submitting your application. Please try again.');
      setPhase('form');
    }
  };

  if (phase === 'done') {
    return (
      <div className="pjp-applied" data-testid="apply-confirmation">
        <h3 className="pjp-eeo-title">Thanks for applying{form.full_name ? `, ${form.full_name.split(' ')[0]}` : ''}.</h3>
        <p className="pjp-muted">
          We&apos;ve received your application{organizationName ? ` to ${organizationName}` : ''}. If it&apos;s a
          match, someone will be in touch.
        </p>
        {eeoToken && !eeoDone ? (
          <EEOStep token={eeoToken} onDone={() => setEeoDone(true)} />
        ) : null}
      </div>
    );
  }

  return (
    <form className="pjp-form" onSubmit={submit} data-testid="apply-form">
      <h3 className="pjp-eeo-title">Apply for this role</h3>
      {error ? <div className="pjp-error" role="alert">{error}</div> : null}

      <label className="pjp-field">
        <span className="pjp-field-label">Full name<span className="pjp-req" aria-hidden> *</span></span>
        <input
          type="text"
          value={form.full_name}
          onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))}
          autoComplete="name"
        />
      </label>
      <label className="pjp-field">
        <span className="pjp-field-label">Email</span>
        <input
          type="email"
          value={form.email}
          onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
          autoComplete="email"
        />
      </label>
      <label className="pjp-field">
        <span className="pjp-field-label">Phone</span>
        <input
          type="tel"
          value={form.phone}
          onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
          autoComplete="tel"
        />
      </label>

      {questions.map((q) => (
        <QuestionField
          key={q.id}
          q={q}
          value={answers[String(q.id)]}
          onChange={(value) => setAnswer(String(q.id), value)}
        />
      ))}

      <label className="pjp-field">
        <span className="pjp-field-label">
          Resume (PDF or Word .docx, up to 5 MB)
          {resumeRequired ? <span className="pjp-req" aria-hidden> *</span> : null}
        </span>
        <input
          type="file"
          accept=".pdf,.docx"
          onChange={onResume}
          data-testid="resume-input"
          aria-required={resumeRequired}
        />
      </label>

      <button type="submit" className="pjp-apply-btn" disabled={phase === 'submitting'}>
        {phase === 'submitting' ? 'Submitting…' : 'Submit application'}
      </button>
    </form>
  );
}

export default ApplyForm;
