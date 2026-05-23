// Decision feed component used by the home Hub. Extracted from
// HomeNow so other surfaces (e.g. the marketing landing page) can
// render the same component with curated mock rows.
//
// Rows expected shape (mirrors AgentDecision rows from /agent API):
//   { id, status, decision_type, candidate_name, application_id,
//     role_id, confidence, reasoning, created_at, resolved_at,
//     resolved_by, human_disposition, resolution_note }

import React from 'react';
import { Link } from 'react-router-dom';
import { Check, X } from 'lucide-react';

import { Avatar, TypeBadge, formatRelativeAge, initialsFrom } from './atoms';
import { pathForPage } from '../../app/routing';

// Import home.css here so any surface that renders <ActivityFeed />
// (the Hub today, marketing landing tomorrow, anywhere else later)
// gets the .home-section + .rq-stream-* styles without having to
// remember to import home.css separately. HomePage continues to
// import home.css for the rest of the Hub layout — duplicate
// side-effect imports are deduped by Vite's module graph.
import './home.css';


// Default subtitle is the Hub framing (mentions the toolbar + detail
// panel that surround the feed at /home). Marketing surfaces pass an
// override since they render the feed standalone.
const DEFAULT_SUBTITLE =
  'Reverse-chronological. Filtered by the toolbar above. Pending rows jump into the detail panel.';

export const ActivityFeed = ({
  rows,
  selectedId,
  onSelect,
  onNavigate,
  subtitle = DEFAULT_SUBTITLE,
}) => (
  <section className="home-section">
    <div className="home-section-head">
      <div>
        <span className="kicker">ACTIVITY · {rows.length} ROWS</span>
        <h3 className="home-section-title">Decision feed<em>.</em></h3>
        <p className="home-section-sub">{subtitle}</p>
      </div>
    </div>
    {rows.length === 0 ? (
      <div className="home-empty">Nothing matches these filters yet.</div>
    ) : (
      <ol className="rq-stream-list">
        {rows.map((row) => {
          const isPending = row.status === 'pending' || row.status === 'reverted_for_feedback';
          if (isPending) {
            return (
              <li
                key={row.id}
                className={`rq-stream-item ${selectedId === row.id ? 'rq-stream-active' : ''}`.trim()}
                style={{ cursor: 'pointer' }}
                onClick={() => onSelect?.(row.id)}
              >
                <div className="rq-stream-rail">
                  <Avatar initials={initialsFrom(row.candidate_name)} size={32} />
                  <span className="rq-stream-rule" />
                </div>
                <div className="rq-stream-body">
                  <div className="rq-stream-meta">
                    <TypeBadge type={row.decision_type} size="sm" />
                    {row.status === 'pending'
                      ? <span className="rq-stream-pendpill">NEEDS YOU</span>
                      : <span className="rq-stream-teachpill">+ FEEDBACK</span>}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                      D-{row.id} · {formatRelativeAge(row.created_at)} ago
                    </span>
                  </div>
                  <div className="rq-stream-title">
                    <Link
                      to={pathForPage('candidate-report', { candidateApplicationId: row.application_id })}
                      className="rq-inline-link"
                      style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', fontWeight: 600, textDecoration: 'none' }}
                      onClick={(e) => e.stopPropagation()}
                      title="Open candidate report"
                    >
                      {row.candidate_name || `Application #${row.application_id}`}
                    </Link>
                  </div>
                  <div className="rq-stream-sub">
                    <button
                      type="button"
                      className="rq-inline-link"
                      style={{ background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
                      onClick={(e) => { e.stopPropagation(); onNavigate?.('job-pipeline', { roleId: row.role_id }); }}
                    >
                      Role #{row.role_id}
                    </button>
                    {row.confidence != null ? <> · agent {Math.round(row.confidence * 100)}% confident</> : null}
                  </div>
                  <div className="rq-stream-reason">{row.reasoning}</div>
                </div>
              </li>
            );
          }
          return (
            <li key={row.id} className="rq-stream-item">
              <div className="rq-stream-rail">
                <span className={`rq-stream-dot ${row.status === 'overridden' ? 'override' : ''}`.trim()}>
                  {row.status === 'approved' ? <Check size={12} aria-hidden="true" /> : <X size={12} aria-hidden="true" />}
                </span>
                <span className="rq-stream-rule" />
              </div>
              <div className="rq-stream-body">
                <div className="rq-stream-meta">
                  <TypeBadge type={row.decision_type} size="sm" />
                  {row.status === 'overridden' ? <span className="rq-stream-overridepill">OVERRIDE</span> : null}
                  {row.human_disposition === 'taught' ? <span className="rq-stream-teachpill">+ FEEDBACK</span> : null}
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                    D-{row.id} · {formatRelativeAge(row.resolved_at || row.created_at)} ago
                  </span>
                </div>
                <div className="rq-stream-resolved-line">
                  <Link
                    to={pathForPage('candidate-report', { candidateApplicationId: row.application_id })}
                    className="rq-inline-link"
                    style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'var(--ink)', fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}
                    title="Open candidate report"
                  >
                    {row.candidate_name || `Application #${row.application_id}`}
                  </Link>
                  <span style={{ color: 'var(--mute)' }}> — {row.status} </span>
                  {row.resolution_note ? <span>· {row.resolution_note}</span> : null}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    )}
  </section>
);

export default ActivityFeed;
