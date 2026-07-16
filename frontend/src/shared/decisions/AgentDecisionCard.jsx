// AgentDecisionCard — the agent-decision detail panel + approve/alternative/
// teach/snooze action bar. Lifted verbatim out of HomeNow's DecisionDetail so
// it can be reused beyond the /home review queue (e.g. the candidate report)
// with a different parent wiring its own handlers.
//
// Route-agnostic: deep links use real anchors so they preserve browser link
// behaviour (open in a new tab, copy link, etc.) on every surface.
//
// Styles are the existing rq-* classes from home.css — imported here so any
// surface rendering this card gets them without remembering to import the CSS
// itself (same pattern ActivityFeed uses). Duplicate CSS imports are deduped
// by the bundler.
import React, { useState } from 'react';
import {
  AlertTriangle,
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
import { AgentFlowButton } from '../motion';
import { PageLink } from '../ui/PageLink';
import { ScoreRing } from '../ui/ScoreRing';
import { Button } from '../ui/TaaliPrimitives';
import {
  Avatar,
  ConfBar,
  formatRelativeAge,
  initialsFrom,
  TypeBadge,
} from '../../features/home/atoms';
import { ScoreProvenance } from '../../features/candidates/ScoreProvenance';
import { IntegrityFlags } from './IntegrityFlags';
import { DecisionNarrative } from './DecisionNarrative';
import { ruleChipText } from './decisionPresentation';
import { normaliseDecisionText } from './decisionText';
import { DECISION_ACTIONS, DEFAULT_ACTIONS, REJECT_CONSEQUENCE_COPY, isRejectDecisionType } from './decisionActions';
import '../../features/home/home.css';

// Absolute applied date ("12 Jun 2026") — same format as the ScoreProvenance
// date pill so the two provenance lines read consistently.
const fmtAppliedDate = (v) => {
  if (!v) return null;
  try {
    return new Date(v).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  } catch {
    return null;
  }
};

// `middleSlot` / `hideDecisionParts` / `statusPill` let a non-decision surface
// reuse this card VERBATIM. The invited-candidate tracker passes the assessment
// stage tracker as `middleSlot` (it sits exactly where the agent-recommendation
// slab would be) and hides the decision-only parts (reasoning, evidence, trace,
// action bar) — keeping the identical header, deep-links, integrity flags and
// requirement bars. One card, two surfaces, guaranteed in lockstep.
export const AgentDecisionCard = ({ decision, onApprove, onAlternative, onTeach, onSnooze, onReEvaluate, busy, middleSlot = null, hideDecisionParts = false, statusPill = null }) => {
  // "why?" disclosure on the recommendation kicker — the causal sentence that
  // no longer prints inline on policy-source cards (it lives here now).
  const [whyOpen, setWhyOpen] = useState(false);
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
  // A re-score is running for this candidate (Re-evaluate on an old-engine
  // score, or a bulk re-score). Grey the card + freeze actions until the
  // fresh score lands — the decisions poll un-greys it automatically.
  const rescoring = isPending && Boolean(decision.rescore_in_flight);
  const frozen = busy || rescoring;
  // Post-handover warning: the candidate already sits in a live Workable
  // interview/offer stage (possibly moved there before the application ever
  // reached Taali). Rejects stay fully approvable — Taali warns, never
  // blocks — but the recruiter must see what approving does before one click.
  const isRejectType = isRejectDecisionType(decision.decision_type);
  const postHandoverWarn = isRejectType && Boolean(decision.candidate_post_handover);
  const spec = DECISION_ACTIONS[decision.decision_type] || DEFAULT_ACTIONS;
  const recExplanation = decision.decision_explanation && typeof decision.decision_explanation === 'object'
    ? decision.decision_explanation
    : null;
  const decisionSource = recExplanation?.source === 'policy' ? 'policy' : 'agent';
  const recChip = ruleChipText(decision);
  const whyText = recExplanation
    ? normaliseDecisionText([recExplanation.summary, recExplanation.context].filter(Boolean).join(' '))
    : '';
  const recRevisionId = recExplanation?.policy_revision_id;
  // "why?" is the single home for the policy causal sentence (the card narrative
  // drops it for policy). Agent cards already print that sentence inline, so the
  // disclosure would only duplicate it — omit it there.
  const showWhy = decisionSource === 'policy' && Boolean(whyText);
  // ScoreProvenance renders nothing without an engine version / scored-at; mirror
  // that so the merged provenance row doesn't print a dangling separator.
  const scoreProvenance = decision?.score_summary?.score_provenance;
  const hasProvenance = Boolean(scoreProvenance
    && (scoreProvenance.engine_version || scoreProvenance.scored_at));
  const PrimaryIcon = spec.primaryIcon || Check;
  const primaryTitle = staleEngineOnly
    ? 'Scored by an older version of Taali’s scoring — this approves the old score as-is. Re-evaluate first to refresh it.'
    : isStale
      ? 'Inputs changed since this was decided — this acts on them anyway. Re-evaluate first to refresh.'
      : undefined;
  // Same reject consequence the candidate-report rail shows, from the shared
  // source — so a recruiter approving a reject from the hub queue sees what it
  // does (the hub card previously showed nothing). Stale/old-engine warning
  // still wins the tooltip.
  const primaryButtonTitle = primaryTitle ?? (isPending && isRejectType ? REJECT_CONSEQUENCE_COPY : undefined);

  return (
    <section className={`rq-hybrid-detail${rescoring ? ' is-rescoring' : ''}`}>
      {/* Compact header (preview): the score ring + name + role, with the scored
          date/version as clean provenance text underneath — one vertical stack,
          so nothing overlaps. The decision type is NOT repeated as a top badge;
          it lives in the recommendation slab below. */}
      <div className="rq-detail-head2">
        {decision.taali_score != null ? (
          <ScoreRing score={decision.taali_score} size={58} label="TAALI" />
        ) : (
          <Avatar initials={initialsFrom(decision.candidate_name)} size={52} />
        )}
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
          <div style={{ fontSize: '0.8125rem', color: 'var(--mute)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {[decision.role_name, decision.candidate_email].filter(Boolean).join(' · ')}
          </div>
          {hasProvenance || decision.applied_at ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--mute)', marginTop: 2 }}>
              <ScoreProvenance provenance={scoreProvenance} density="full" />
              {hasProvenance && decision.applied_at ? <span aria-hidden="true">·</span> : null}
              {decision.applied_at ? (
                <span title="When this application was submitted — how fresh the candidate is">
                  Applied {fmtAppliedDate(decision.applied_at)} · {formatRelativeAge(decision.applied_at)} ago
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
        {statusPill || (decision.status === 'pending' ? (
          <span className="rq-stream-pendpill" style={{ alignSelf: 'flex-start' }}>NEEDS YOU</span>
        ) : decision.status === 'reverted_for_feedback' ? (
          <span className="rq-stream-teachpill" style={{ alignSelf: 'flex-start' }}>+ FEEDBACK</span>
        ) : null)}
      </div>

      {/* Deep-links on their own row — secondary actions rendered as links. */}
      <div className="rq-detail-links" style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 }}>
        <Button
          as="a"
          variant="secondary"
          size="sm"
          href={pathForPage('candidate-report', { candidateApplicationId: decision.application_id, fromHome: true })}
          target="_blank"
          rel="noopener noreferrer"
          style={{ flex: 1, justifyContent: 'center' }}
        >
          <FileText size={14} aria-hidden="true" /> Candidate report
        </Button>
        <Button
          as={PageLink}
          variant="secondary"
          size="sm"
          page="job-pipeline"
          options={{ roleId: decision.role_id }}
          style={{ flex: 1, justifyContent: 'center' }}
        >
          <Eye size={14} aria-hidden="true" /> Job pipeline
        </Button>
      </div>

      {/* Re-score in flight: one unmissable banner at the top; everything
          below it is greyed (via .is-rescoring) and the actions are frozen
          so nothing is approved on a score that's being replaced. */}
      {rescoring ? (
        <div className="rq-rescore-banner" role="status" style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 14px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: '0.8125rem', fontWeight: 500 }}>
          <RefreshCw size={14} strokeWidth={2} aria-hidden="true" className="rq-spin" />
          <span>Re-scoring this candidate — the card updates automatically when the new score lands.</span>
        </div>
      ) : null}

      {/* Invited mode: the assessment stage tracker takes the slab's place. */}
      {middleSlot}

      {/* Reject on a candidate already advanced in Workable: warn BEFORE the
          approve button. Advice, never a block — approving disqualifies them
          in Workable, so the recruiter must knowingly confirm. */}
      {!hideDecisionParts && isPending && postHandoverWarn ? (
        <div className="rq-posthandover-banner" role="alert" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, margin: '0 0 12px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: '0.8125rem', fontWeight: 500, lineHeight: 1.45 }}>
          <AlertTriangle size={14} strokeWidth={2} aria-hidden="true" style={{ marginTop: 1, flexShrink: 0 }} />
          <span>
            <strong>Heads up —</strong> this candidate is in{' '}
            <strong>{decision.candidate_workable_stage || 'a live interview stage'}</strong> in
            Workable. Approving this reject will disqualify them there. You can still
            approve — just make sure that&apos;s intended.
          </span>
        </div>
      ) : null}

      {/* Agent-recommends slab — near the TOP (preview order): the recommendation
          first, then reasoning + flags, with the secondary actions at the bottom. */}
      {isPending && !middleSlot ? (
        <div className="rq-rec">
          <Button
            as={AgentFlowButton}
            variant="agent"
            size="md"
            className="rq-rec-btn"
            onClick={() => onApprove(decision)}
            disabled={frozen}
            title={primaryButtonTitle}
          >
            <PrimaryIcon size={16} strokeWidth={2.4} aria-hidden="true" />
            {spec.primaryLabel}
          </Button>
          <div className="rq-rec-kl">
            <Sparkles size={12} aria-hidden="true" /> {decisionSource === 'policy' ? 'Policy' : 'Agent'}
            {recChip ? <span className="rq-rec-chip">{recChip}</span> : null}
            {showWhy ? (
              <button
                type="button"
                className="rq-rec-why"
                onClick={() => setWhyOpen((value) => !value)}
                aria-expanded={whyOpen}
              >
                why?
              </button>
            ) : null}
          </div>
          {isRejectType ? (
            <div className="rq-rec-conf">{REJECT_CONSEQUENCE_COPY}</div>
          ) : null}
          {showWhy && whyOpen ? (
            <div className="rq-rec-why-panel" role="region" aria-label="Why this recommendation">
              <p className="rq-rec-why-text">{whyText}</p>
              {recRevisionId != null ? (
                <div className="rq-rec-prov">policy revision #{recRevisionId}</div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Resolved/processing cards (timeline, history filters) have no rec slab,
          so the chip + "why?" never render there — the narrative must carry the
          policy cause itself or those cards show no explanation at all. */}
      {!hideDecisionParts ? (
        <DecisionNarrative
          decision={decision}
          density="card"
          showPolicyReason={!(isPending && !middleSlot)}
        />
      ) : null}

      {/* Trust readout right under the summary — the specific things to verify
          and the cross-source corroborations we confirmed. Same component the
          candidate report renders, so the wording is identical everywhere. */}
      <IntegrityFlags integrity={decision?.score_summary?.integrity} style={{ margin: '0 0 14px' }} />

      {/* Requirement bars — the candidate's top requirement grades (same source
          as the candidate report). Preview order: after flags, before actions. */}
      {Array.isArray(decision.requirements) && decision.requirements.length > 0 ? (
        <div className="rq-reqs">
          <div className="kicker mute" style={{ margin: '0 0 7px' }}>REQUIREMENTS</div>
          {decision.requirements.map((r, i) => (
            <div className="rq-req" key={`${r.label}-${i}`}>
              <span className="rq-req-nm" data-tip={r.label}>
                <span className="rq-req-nm-txt">{r.label}</span>
              </span>
              <span className="rq-req-track">
                {r.score != null ? (
                  <span
                    className={`rq-req-fill${r.score < 40 ? ' is-low' : ''}`}
                    style={{ width: `${Math.max(0, Math.min(100, r.score))}%` }}
                  />
                ) : null}
              </span>
              {r.score != null ? <span className="rq-req-val">{Math.round(r.score)}</span> : null}
            </div>
          ))}
        </div>
      ) : null}

      {!hideDecisionParts && isStale && !rescoring && (decision.status === 'pending' || decision.status === 'reverted_for_feedback') ? (
        <div className="rq-stale-banner" style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 14px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: '0.8125rem', fontWeight: 500 }}>
          <RefreshCw size={14} strokeWidth={2} aria-hidden="true" />
          <span>
            {staleEngineOnly
              ? 'This score came from an older version of Taali’s scoring. Re-evaluate to refresh it.'
              : `Inputs changed since this was decided${stalenessSummary ? ` · ${stalenessSummary}` : ''}. Re-evaluate before approving.`}
          </span>
        </div>
      ) : null}

      {/* A pending decision with a resolution_note was returned to the queue
          (the action couldn't complete — e.g. the role has no assessment task).
          Surface the reason so the recruiter doesn't blindly re-approve into the
          same failure; a fresh pending decision has no note. */}
      {!hideDecisionParts && decision.status === 'pending' && decision.resolution_note ? (
        <div className="rq-returned-banner" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, margin: '0 0 14px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: '0.8125rem', fontWeight: 500, lineHeight: 1.45 }}>
          <Inbox size={14} strokeWidth={2} aria-hidden="true" style={{ marginTop: 1, flexShrink: 0 }} />
          <span>{decision.resolution_note}</span>
        </div>
      ) : null}

      {!hideDecisionParts && evidence.length > 0 ? (
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

      {!hideDecisionParts && trace.length > 0 ? (
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

      {/* Secondary actions at the very BOTTOM (preview order). The recommended
          action lives in the slab near the top. */}
      {hideDecisionParts ? null : isPending ? (
        <div className="rq-action-bar">
          <div className="rq-action-l">
            {isStale && onReEvaluate ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => onReEvaluate(decision)}
                disabled={frozen}
              >
                <RefreshCw size={14} strokeWidth={2.4} aria-hidden="true" />
                Re-evaluate
              </Button>
            ) : null}
            {(spec.alternatives || []).map((alt) => {
              const AltIcon = alt.icon || X;
              return (
                <Button
                  key={alt.action}
                  variant="secondary"
                  size="sm"
                  onClick={() => onAlternative(decision, alt)}
                  disabled={frozen}
                  title={alt.body}
                >
                  <AltIcon size={14} strokeWidth={2} aria-hidden="true" />
                  {alt.label}
                </Button>
              );
            })}
            <Button variant="secondary" size="sm" onClick={() => onTeach(decision)} disabled={frozen}>
              <Brain size={14} strokeWidth={2} aria-hidden="true" />
              Send back &amp; teach
            </Button>
          </div>
          <Button variant="ghost" size="sm" onClick={() => onSnooze(decision)} disabled={frozen}>
            Snooze 1h
          </Button>
        </div>
      ) : (
        <div className="home-empty" style={{ marginTop: 12 }}>
          {decision.status === 'approved' ? 'Approved — actions are read-only.'
            : decision.status === 'overridden' ? 'Overridden — actions are read-only.'
              : `Decision is ${String(decision.status || '').replace(/_/g, ' ')} — actions are read-only.`}
        </div>
      )}
    </section>
  );
};

export default AgentDecisionCard;
