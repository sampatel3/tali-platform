// Decision feed component used by the home Hub. Extracted from
// HomeNow so other surfaces (e.g. the marketing landing page) can
// render the same component with curated mock rows.
//
// Rows expected shape (mirrors AgentDecision rows from /agent API):
//   { id, status, decision_type, candidate_name, application_id,
//     role_id, confidence, reasoning, created_at, resolved_at,
//     resolved_by, human_disposition, resolution_note }

import React, { useState } from 'react';
import { Check, X } from 'lucide-react';

import { Avatar, RolePill, ScoreChip, TypeBadge, formatRelativeAge, initialsFrom, humanizeStatusInline } from './atoms';
import { pathForPage } from '../../app/routing';
import { ScoreProvenance } from '../candidates/ScoreProvenance';

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
  'Newest first, filtered by the toolbar above. Click a pending row to review it in the detail panel.';

export const ActivityFeed = ({
  rows,
  selectedId,
  onSelect,
  onNavigate,
  subtitle = DEFAULT_SUBTITLE,
  title = 'Decision feed',
  kicker,
  collapsedCount,
}) => {
  const [expanded, setExpanded] = useState(false);
  const capped = collapsedCount != null && !expanded;
  const shown = capped ? rows.slice(0, collapsedCount) : rows;
  return (
  <section className="home-section">
    <div className="home-section-head">
      <div>
        <span className="kicker">{kicker || `ACTIVITY · ${rows.length} ROWS`}</span>
        <h3 className="home-section-title">{title}<em>.</em></h3>
        <p className="home-section-sub">{subtitle}</p>
      </div>
    </div>
    {rows.length === 0 ? (
      <div className="home-empty">Nothing matches these filters yet.</div>
    ) : (
      <ol className="rq-stream-list">
        {shown.map((row) => {
          // ``processing`` = approved/overridden and mid-Workable-writeback. It
          // renders in the same rich layout as pending (candidate + reasoning)
          // but greyed + non-actionable so the recruiter can see it's in flight.
          const isProcessing = row.status === 'processing';
          const isPending = row.status === 'pending' || row.status === 'reverted_for_feedback';
          if (isPending || isProcessing) {
            return (
              <li
                key={row.id}
                className={`rq-stream-item ${selectedId === row.id ? 'rq-stream-active' : ''} ${isProcessing ? 'is-processing' : ''}`.trim()}
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
                    <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
                      <ScoreChip score={row.taali_score} size="sm" />
                      <ScoreProvenance provenance={row?.score_summary?.score_provenance} density="pill" />
                    </span>
                    {/* Processing has no pill — the card is already dimmed
                        (.is-processing opacity) which reads as in-flight. */}
                    {isProcessing
                      ? null
                      : row.status === 'pending'
                        ? <span className="rq-stream-pendpill">NEEDS YOU</span>
                        : <span className="rq-stream-teachpill">+ FEEDBACK</span>}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-caption)', color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                      D-{row.id} · {formatRelativeAge(row.created_at)} ago
                    </span>
                  </div>
                  <div className="rq-stream-title">
                    <a
                      href={pathForPage('candidate-report', { candidateApplicationId: row.application_id, fromHome: true })}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rq-inline-link"
                      style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', fontWeight: 600, textDecoration: 'none' }}
                      onClick={(e) => e.stopPropagation()}
                      title="Open candidate report in a new tab"
                    >
                      {row.candidate_name || `Application #${row.application_id}`}
                    </a>
                  </div>
                  <div className="rq-stream-sub" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <RolePill
                      roleName={row.role_name}
                      roleId={row.role_id}
                      onClick={(e) => { e.stopPropagation(); onNavigate?.('job-pipeline', { roleId: row.role_id }); }}
                    />
                    {row.confidence != null ? <span>agent {Math.round(row.confidence * 100)}% confident</span> : null}
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
                  <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
                    <ScoreChip score={row.taali_score} size="sm" />
                    <ScoreProvenance provenance={row?.score_summary?.score_provenance} density="pill" />
                  </span>
                  {row.status === 'overridden' ? <span className="rq-stream-overridepill">OVERRIDE</span> : null}
                  {row.human_disposition === 'taught' ? <span className="rq-stream-teachpill">+ FEEDBACK</span> : null}
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-caption)', color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                    D-{row.id} · {formatRelativeAge(row.resolved_at || row.created_at)} ago
                  </span>
                </div>
                <div className="rq-stream-resolved-line">
                  <a
                    href={pathForPage('candidate-report', { candidateApplicationId: row.application_id, fromHome: true })}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rq-inline-link"
                    style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'var(--ink)', fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}
                    title="Open candidate report in a new tab"
                  >
                    {row.candidate_name || `Application #${row.application_id}`}
                  </a>
                  <span style={{ color: 'var(--mute)' }}> — {humanizeStatusInline(row.status)} </span>
                  {row.resolution_note ? <span>· {row.resolution_note}</span> : null}
                </div>
                {(row.role_name || row.role_id != null) ? (
                  <div style={{ marginTop: 4 }}>
                    <RolePill
                      roleName={row.role_name}
                      roleId={row.role_id}
                      onClick={() => onNavigate?.('job-pipeline', { roleId: row.role_id })}
                    />
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    )}
    {collapsedCount != null && rows.length > collapsedCount ? (
      <button type="button" className="rq-feed-toggle" onClick={() => setExpanded((v) => !v)}>
        {expanded ? 'Show fewer' : `Show all ${rows.length} decisions`}
      </button>
    ) : null}
  </section>
  );
};

export default ActivityFeed;
