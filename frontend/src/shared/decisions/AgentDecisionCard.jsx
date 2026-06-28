// AgentDecisionCard — the agent-decision detail panel + approve/alternative/
// teach/snooze action bar. Lifted verbatim out of HomeNow's DecisionDetail so
// it can be reused beyond the /home review queue (e.g. the candidate report)
// with a different parent wiring its own handlers.
//
// Route-agnostic: the one imperative navigation (open job pipeline) goes
// through the ``onNavigate`` prop the parent supplies. The candidate-report
// links are plain new-tab <a href> (presentation, identical for any parent),
// so they stay self-contained here.
//
// Styles are the existing rq-* classes from home.css — imported here so any
// surface rendering this card gets them without remembering to import the CSS
// itself (same pattern ActivityFeed uses). Duplicate CSS imports are deduped
// by the bundler.
import React from 'react';
import {
  Brain,
  Check,
  Eye,
  FileText,
  Inbox,
  RefreshCw,
  Sparkles,
  X,
} from 'lucide-react';

import { pathForPage } from '../../app/routing';
import { ScoreRing } from '../ui/ScoreRing';
import {
  Avatar,
  ConfBar,
  formatRelativeAge,
  initialsFrom,
  TypeBadge,
} from '../../features/home/atoms';
import { ScoreProvenance } from '../../features/candidates/ScoreProvenance';
import { IntegrityFlags } from './IntegrityFlags';
import { DECISION_ACTIONS, DEFAULT_ACTIONS } from './decisionActions';
import '../../features/home/home.css';

export const AgentDecisionCard = ({ decision, onApprove, onAlternative, onTeach, onSnooze, onNavigate, onReEvaluate, busy }) => {
  if (!decision) {
    return (
      <section className="rq-hybrid-detail">
        <div className="home-empty">Select a pending decision from the queue to inspect it here.</div>
      </section>
    );
  }
  const evidence = Array.isArray(decision.evidence?.cells) ? decision.evidence.cells : [];
  const trace = Array.isArray(decision.evidence?.trace) ? decision.evidence.trace : [];
  const isStale = Boolean(decision.is_stale);
  const stalenessSummary = decision.staleness_summary;
  // Old-model staleness reads differently from an input change — the inputs
  // didn't move, the scoring engine did. Re-evaluate here re-scores on the
  // current engine rather than just re-running the agent.
  const stalenessReasons = Array.isArray(decision.staleness_reasons) ? decision.staleness_reasons : [];
  const staleEngineOnly = stalenessReasons.length > 0 && stalenessReasons.every((r) => r === 'engine_outdated');

  const isPending = decision.status === 'pending' || decision.status === 'reverted_for_feedback';
  const spec = DECISION_ACTIONS[decision.decision_type] || DEFAULT_ACTIONS;
  const PrimaryIcon = spec.primaryIcon || Check;
  const primaryTitle = staleEngineOnly
    ? 'Scored by an older model — this approves the old score as-is. Re-evaluate to re-score first.'
    : isStale
      ? 'Inputs changed since this was decided — this acts on them anyway. Re-evaluate first to refresh.'
      : undefined;

  return (
    <section className="rq-hybrid-detail">
      <div className="rq-split-detail-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <TypeBadge type={decision.decision_type} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.6875rem', color: 'var(--mute)', letterSpacing: '.06em' }}>
            D-{decision.id} · {formatRelativeAge(decision.created_at)} ago
          </span>
          {decision.status === 'pending' ? (
            <span className="rq-stream-pendpill">NEEDS YOU</span>
          ) : decision.status === 'reverted_for_feedback' ? (
            <span className="rq-stream-teachpill">+ FEEDBACK</span>
          ) : null}
        </div>
        {decision.confidence != null ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="kicker mute">CONFIDENCE</span>
            <ConfBar value={decision.confidence} />
          </div>
        ) : null}
      </div>

      <div className="rq-detail-identity" style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 14 }}>
        <Avatar initials={initialsFrom(decision.candidate_name)} size={48} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="home-title-md" style={{ margin: 0, lineHeight: 1.2, overflowWrap: 'anywhere' }}>
            <a
              href={pathForPage('candidate-report', { candidateApplicationId: decision.application_id, fromHome: true })}
              target="_blank"
              rel="noopener noreferrer"
              className="rq-inline-link"
              style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', textAlign: 'left', textDecoration: 'none' }}
              title="Open candidate report in a new tab"
            >
              {decision.candidate_name || `Application #${decision.application_id}`}
            </a>
          </h2>
          <div style={{ fontSize: '0.8125rem', color: 'var(--mute)', marginTop: 2 }}>
            {decision.candidate_email || ''}
          </div>
          {/* Deep-links sit on their own line below the email so a long name
              can never collide with them. */}
          {/* Standard outline buttons (preview), not the bespoke DeepLinkRow. */}
          <div className="rq-detail-links" style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12 }}>
            <a
              className="btn btn-outline btn-sm"
              href={pathForPage('candidate-report', { candidateApplicationId: decision.application_id, fromHome: true })}
              target="_blank"
              rel="noopener noreferrer"
              style={{ flex: 1, justifyContent: 'center' }}
            >
              <FileText size={14} aria-hidden="true" /> Candidate report
            </a>
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={() => onNavigate?.('job-pipeline', { roleId: decision.role_id })}
              style={{ flex: 1, justifyContent: 'center' }}
            >
              <Eye size={14} aria-hidden="true" /> Job pipeline
            </button>
          </div>
        </div>
        {decision.taali_score != null ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            <ScoreRing score={decision.taali_score} size={72} label="TALI" />
            <ScoreProvenance provenance={decision?.score_summary?.score_provenance} density="full" />
          </div>
        ) : null}
      </div>

      {/* Agent-recommends slab — near the TOP (preview order): the recommendation
          first, then reasoning + flags, with the secondary actions at the bottom. */}
      {isPending ? (
        <div className="rq-rec">
          <div className="rq-rec-kl"><Sparkles size={12} aria-hidden="true" /> Agent recommends</div>
          <button
            type="button"
            className="rq-rec-btn"
            onClick={() => onApprove(decision)}
            disabled={busy}
            title={primaryTitle}
          >
            <PrimaryIcon size={16} strokeWidth={2.4} aria-hidden="true" />
            {spec.primaryLabel}
          </button>
          {decision.confidence != null ? (
            <div className="rq-rec-conf">Confidence {Math.round(decision.confidence * 100)}%</div>
          ) : null}
        </div>
      ) : null}

      <p style={{ margin: '0 0 14px', fontSize: '0.875rem', color: 'var(--ink-2)', lineHeight: 1.55, maxWidth: 760 }}>
        {decision.reasoning}
      </p>

      {/* Trust readout right under the summary — the specific things to verify
          and the cross-source corroborations we confirmed. Same component the
          candidate report renders, so the wording is identical everywhere. */}
      <IntegrityFlags integrity={decision?.score_summary?.integrity} style={{ margin: '0 0 14px' }} />

      {isStale && (decision.status === 'pending' || decision.status === 'reverted_for_feedback') ? (
        <div className="rq-stale-banner" style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 14px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: '0.8125rem', fontWeight: 500 }}>
          <RefreshCw size={14} strokeWidth={2} aria-hidden="true" />
          <span>
            {staleEngineOnly
              ? 'This score is from an older model. Re-evaluate to re-score on the current engine.'
              : `Inputs changed since this was decided${stalenessSummary ? ` · ${stalenessSummary}` : ''}. Re-evaluate before approving.`}
          </span>
        </div>
      ) : null}

      {/* A pending decision with a resolution_note was returned to the queue
          (the action couldn't complete — e.g. the role has no assessment task).
          Surface the reason so the recruiter doesn't blindly re-approve into the
          same failure; a fresh pending decision has no note. */}
      {decision.status === 'pending' && decision.resolution_note ? (
        <div className="rq-returned-banner" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, margin: '0 0 14px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: '0.8125rem', fontWeight: 500, lineHeight: 1.45 }}>
          <Inbox size={14} strokeWidth={2} aria-hidden="true" style={{ marginTop: 1, flexShrink: 0 }} />
          <span>{decision.resolution_note}</span>
        </div>
      ) : null}

      {evidence.length > 0 ? (
        <div className="rq-evidence-grid">
          {evidence.map((e, i) => (
            <div key={i} className="rq-ev-cell">
              <div className="rq-ev-k">{e.k || e.label}</div>
              <div className="rq-ev-v" style={{ color: e.good === true ? 'var(--purple)' : 'var(--ink)' }}>
                {e.v ?? e.value}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {trace.length > 0 ? (
        <div className="rq-trace" style={{ marginTop: 14 }}>
          <div className="rq-trace-head">
            <span className="kicker">DECISION TRACE · {trace.length} EVENTS</span>
          </div>
          <ol className="rq-trace-list">
            {trace.map((s, i) => (
              <li key={i}>
                <span className={`rq-trace-dot rq-trace-${s.who || 'agent'}`} />
                <div>
                  <div className="rq-trace-t">
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.625rem', color: 'var(--mute)', letterSpacing: '.08em', marginRight: 8, textTransform: 'uppercase' }}>{s.who || 'agent'}</span>
                    {s.t || s.title}
                  </div>
                  {s.m || s.message ? <div className="rq-trace-m">{s.m || s.message}</div> : null}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {/* Secondary actions at the very BOTTOM (preview order), standard .btn
          family. The recommended action lives in the slab near the top. */}
      {isPending ? (
        <div className="rq-action-bar">
          <div className="rq-action-l">
            {isStale && onReEvaluate ? (
              <button
                type="button"
                className="btn btn-outline btn-sm"
                onClick={() => onReEvaluate(decision)}
                disabled={busy}
              >
                <RefreshCw size={14} strokeWidth={2.4} aria-hidden="true" />
                Re-evaluate
              </button>
            ) : null}
            {(spec.alternatives || []).map((alt) => {
              const AltIcon = alt.icon || X;
              return (
                <button
                  key={alt.action}
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => onAlternative(decision, alt)}
                  disabled={busy}
                  title={alt.body}
                >
                  <AltIcon size={14} strokeWidth={2} aria-hidden="true" />
                  {alt.label}
                </button>
              );
            })}
            <button type="button" className="btn btn-purple btn-sm" onClick={() => onTeach(decision)} disabled={busy}>
              <Brain size={14} strokeWidth={2} aria-hidden="true" />
              Send back &amp; teach
            </button>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onSnooze(decision)} disabled={busy}>
            Snooze 1h
          </button>
        </div>
      ) : (
        <div className="home-empty" style={{ marginTop: 12 }}>
          {decision.status === 'approved' ? 'Approved — actions are read-only.'
            : decision.status === 'overridden' ? 'Overridden — actions are read-only.'
              : `Decision is ${decision.status}.`}
        </div>
      )}
    </section>
  );
};

export default AgentDecisionCard;
