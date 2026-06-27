import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import { publicCareersApi } from '../requisitions/api';
import './jobpage.css';
import './careers.css';

// Public, no-auth CAREERS BOARD (/careers/:slug). Lists all of an org's
// published jobs. Reached via the org's `careers_url` (surfaced to recruiters
// on the published requisition). Fetches the board through publicCareersApi.get
// (a bare, JWT-free axios call), then renders the posting org, an "Open roles"
// heading and the jobs as a clean grid of cards — each links to its public
// /job/{token} posting.
//
// No nav, no auth, no recruiter chrome — mirrors PublicJobPage.

const titleCase = (s) => String(s || '')
  .replace(/_/g, ' ')
  .replace(/\b\w/g, (c) => c.toUpperCase());

// The card meta row: location · workplace_type · employment_type · seniority,
// dropping any that are empty. (Salary is rendered separately below the meta.)
const buildMeta = (job) => {
  if (!job) return [];
  return [
    job.location,
    titleCase(job.workplace_type),
    titleCase(job.employment_type),
    titleCase(job.seniority),
  ]
    .map((v) => (v == null ? '' : String(v).trim()))
    .filter(Boolean);
};

const jobHref = (job) => {
  const url = (job?.url == null ? '' : String(job.url)).trim();
  if (url) return url;
  return job?.token ? `/job/${job.token}` : '#';
};

export function CareersPage() {
  const { slug } = useParams();
  const [state, setState] = useState({ loading: true, error: null, board: null });

  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null, board: null });
    publicCareersApi
      .get(slug)
      .then((data) => {
        if (alive) setState({ loading: false, error: null, board: data });
      })
      .catch(() => {
        // Any failure (404, network) reads the same to a public visitor —
        // there's nothing actionable to distinguish.
        if (alive) setState({ loading: false, error: "This careers page isn't available.", board: null });
      });
    return () => { alive = false; };
  }, [slug]);

  if (state.loading) {
    return (
      <div className="pjp-wrap">
        <div className="pjp-muted">Loading…</div>
      </div>
    );
  }

  if (state.error || !state.board) {
    return (
      <div className="pjp-wrap">
        <div className="pjp-brand">taali<span>.</span></div>
        <div className="pjp-muted">{state.error || "This careers page isn't available."}</div>
      </div>
    );
  }

  const board = state.board;
  const jobs = Array.isArray(board.jobs) ? board.jobs : [];

  return (
    <div className="pjp-wrap crs-wrap">
      <div className="pjp-brand">taali<span>.</span></div>
      <header className="pjp-head">
        {board.organization_name ? (
          <div className="pjp-org">{board.organization_name}</div>
        ) : null}
        <h1 className="pjp-title">Open roles</h1>
      </header>

      {jobs.length === 0 ? (
        <div className="pjp-muted">No open roles right now.</div>
      ) : (
        <ul className="crs-list">
          {jobs.map((job, i) => {
            const meta = buildMeta(job);
            return (
              <li key={job.token || i} className="crs-card">
                <a className="crs-card-link" href={jobHref(job)}>
                  <h2 className="crs-card-title">{job.title || 'Open role'}</h2>
                  {meta.length > 0 ? (
                    <div className="crs-card-meta">
                      {meta.map((item, j) => (
                        <span key={j} className="crs-card-meta-item">{item}</span>
                      ))}
                    </div>
                  ) : null}
                  {job.salary ? (
                    <div className="crs-card-salary">{job.salary}</div>
                  ) : null}
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default CareersPage;
