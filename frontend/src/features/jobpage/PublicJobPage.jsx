import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import { publicJobApi } from '../requisitions/api';
import { ChatMarkdown } from '../../shared/chat';
import { useDocumentMeta } from '../../shared/seo/useDocumentMeta';
import { ApplyForm } from './ApplyForm';
import './jobpage.css';

// Public, no-auth careers-style job posting. Reached via /job/:token — the
// shareable link a published requisition produces. Fetches the snapshot through
// publicJobApi.get (a bare, JWT-free axios call), then renders a clean centered
// document: the posting org, the title, a meta row (location · workplace ·
// employment · seniority · salary, empties omitted) and the JD body via the
// shared ChatMarkdown so it reads like every other rendered-markdown surface.
//
// No nav, no auth, no recruiter chrome.

const titleCase = (s) => String(s || '')
  .replace(/_/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase());

const fmtNum = (n) => {
  const num = Number(n);
  return Number.isFinite(num) ? num.toLocaleString('en-US') : String(n);
};

// "AED 20,000–28,000" / "Up to AED 28,000" / "From AED 20,000" — currency
// defaults to AED; returns '' when there's no band at all.
const formatSalary = (job) => {
  const min = job?.salary_min;
  const max = job?.salary_max;
  const hasMin = min != null && min !== '';
  const hasMax = max != null && max !== '';
  if (!hasMin && !hasMax) return '';
  const currency = job?.salary_currency || 'AED';
  if (hasMin && hasMax) return `${currency} ${fmtNum(min)}–${fmtNum(max)}`;
  if (hasMax) return `Up to ${currency} ${fmtNum(max)}`;
  return `From ${currency} ${fmtNum(min)}`;
};

// The meta row: location · workplace_type · employment_type · seniority ·
// salary band, dropping any that are empty.
const buildMeta = (job) => {
  if (!job) return [];
  return [
    job.location,
    titleCase(job.workplace_type),
    titleCase(job.employment_type),
    titleCase(job.seniority),
    formatSalary(job),
  ]
    .map((v) => (v == null ? '' : String(v).trim()))
    .filter(Boolean);
};

export function PublicJobPage() {
  const { token } = useParams();
  const [state, setState] = useState({ loading: true, error: null, job: null });

  const jobTitle = state.job?.title;
  const jobOrg = state.job?.organization_name;
  useDocumentMeta(jobTitle ? {
    title: jobOrg ? `${jobTitle} at ${jobOrg} — Taali` : `${jobTitle} — Taali`,
    description: `${jobTitle}${jobOrg ? ` at ${jobOrg}` : ''}. View the role and how to apply.`,
  } : undefined);

  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null, job: null });
    publicJobApi
      .get(token)
      .then((data) => {
        if (alive) setState({ loading: false, error: null, job: data });
      })
      .catch(() => {
        // Any failure (404, revoked, network) reads the same to a public
        // visitor — there's nothing actionable to distinguish.
        if (alive) setState({ loading: false, error: "This job posting isn't available.", job: null });
      });
    return () => { alive = false; };
  }, [token]);

  if (state.loading) {
    return (
      <div className="pjp-wrap">
        <div className="pjp-muted">Loading…</div>
      </div>
    );
  }

  if (state.error || !state.job) {
    return (
      <div className="pjp-wrap">
        <div className="pjp-brand">taali<span>.</span></div>
        <div className="pjp-muted">{state.error || "This job posting isn't available."}</div>
      </div>
    );
  }

  const job = state.job;
  const meta = buildMeta(job);
  const applyHref = job.apply_url || (job.apply_email ? `mailto:${job.apply_email}` : null);

  return (
    <div className="pjp-wrap">
      <div className="pjp-brand">taali<span>.</span></div>
      <header className="pjp-head">
        {job.organization_name ? (
          <div className="pjp-org">{job.organization_name}</div>
        ) : null}
        <h1 className="pjp-title">{job.title || 'Open role'}</h1>
        {meta.length > 0 ? (
          <div className="pjp-meta">
            {meta.map((item, i) => (
              <span key={i} className="pjp-meta-item">{item}</span>
            ))}
          </div>
        ) : null}
      </header>
      <div className="pjp-body">
        <ChatMarkdown>{job.jd_markdown || ''}</ChatMarkdown>
      </div>
      <footer className="pjp-apply">
        {job.accepts_applications ? (
          <ApplyForm
            token={token}
            questions={job.screening_questions || []}
            organizationName={job.organization_name}
            resumeRequired={Boolean(job.resume_required)}
          />
        ) : applyHref ? (
          <a className="pjp-apply-btn" href={applyHref} target="_blank" rel="noreferrer noopener">
            Apply for this role →
          </a>
        ) : (
          <p className="pjp-muted">
            Applications are not open for this role right now.
          </p>
        )}
      </footer>
    </div>
  );
}

export default PublicJobPage;
