// NOW — the V4 hybrid: pending sidebar (left) + selected detail (right) +
// activity feed (full-width below). The agent-first heart of /home.
//
// Filters live in `filters` (from the parent) and persist in URL search
// params. Approve / Override / Snooze hit the existing endpoints; Teach
// opens TeachModal which POSTs /agent/feedback.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  Brain,
  Check,
  Eye,
  FileText,
  Inbox,
  ListChecks,
  RefreshCw,
  Repeat,
  Search,
  Send,
  X,
} from 'lucide-react';

import { agent as agentApi, organizations as orgsApi, roles as rolesApi } from '../../shared/api';
import { AssessmentInviteChip } from '../candidates/CandidateStatusChips';
import { PIPELINE_FUNNEL_STAGES } from '../../shared/metrics';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { useToast } from '../../context/ToastContext';
import { pathForPage } from '../../app/routing';
import { ScoreRing } from '../../shared/ui/ScoreRing';
import {
  Avatar,
  ConfBar,
  DeepLinkRow,
  formatRelativeAge,
  initialsFrom,
  RolePill,
  ScoreChip,
  TypeBadge,
} from './atoms';
import { TeachModal } from './TeachModal';
import { OverrideModal, advanceableWorkableStages } from './OverrideModal';
import { ActivityFeed } from './ActivityFeed';
import { ScoreProvenance } from '../candidates/ScoreProvenance';
import AgentNeedsInputCard from '../jobs/AgentNeedsInputCard';


// The backend returns 409 with a structured detail ({code, message, ...}) when
// a decision's inputs shifted since it was queued. ``detail`` is then an OBJECT
// — passing it straight to a toast crashed the page (React can't render an
// object child → ErrorBoundary "Something went wrong"). These helpers extract a
// safe string and detect the stale case so we can offer Re-evaluate instead.
const isDecisionStaleError = (err) => {
  const detail = err?.response?.data?.detail;
  const code = typeof detail === 'object' && detail !== null ? detail.code : detail;
  return err?.response?.status === 409 && code === 'decision_stale';
};

const apiErrorMessage = (err, fallback = 'Something went wrong') => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') {
    return detail.message || detail.detail || detail.code || fallback;
  }
  return err?.message || fallback;
};


// Type-aware action set. Each decision_type maps to:
//   - primary: the agent's recommendation, fires immediately on click
//     (no confirmation modal).
//   - alternatives: destructive alternative actions the recruiter can
//     pick. Each opens OverrideModal with required "why" textarea, then
//     dispatches via /agent-decisions/{id}/override with the action id.
//
// Plus the two universals — Send back & teach (TeachModal) and Snooze
// 1h (immediate, no modal) — rendered after the type-specific buttons.
const DECISION_ACTIONS = {
  send_assessment: {
    primaryLabel: 'Send assessment',
    primaryIcon: Send,
    alternatives: [
      {
        action: 'reject',
        label: 'Reject',
        icon: X,
        kicker: 'REJECT CANDIDATE',
        headline: 'Reject {name}?',
        body: 'This will disqualify them in Workable and send the rejection email. Cannot be undone from this screen.',
        confirmLabel: 'Reject',
        confirmClass: 'rq-override',
        placeholder: 'e.g. Missing AWS Glue experience confirmed by the recruiter screen',
      },
      {
        action: 'skip_assessment_advance',
        label: 'Skip & advance',
        icon: ArrowRight,
        kicker: 'SKIP ASSESSMENT',
        headline: 'Skip the assessment and move {name} to the advance queue?',
        body: "Skips the assessment email and queues them as an advance. You'll pick the Workable stage when you approve the advance from the queue — nothing posts to Workable yet.",
        confirmLabel: 'Move to advance queue',
        confirmClass: 'rq-approve',
        placeholder: 'e.g. Internal referral — pre-vetted, no need for an assessment',
      },
    ],
  },
  advance_to_interview: {
    primaryLabel: 'Advance to next stage',
    primaryIcon: ArrowRight,
    // The primary "Advance" no longer fires immediately — it opens the
    // shared OverrideModal in ``approve`` mode so the recruiter picks the
    // target Workable stage (and can add an optional note). This matches
    // the candidate-drawer flow on the Jobs page.
    primary: {
      mode: 'approve',
      kicker: 'ADVANCE',
      headline: 'Advance {name} to the next stage?',
      body: 'Pick the Workable stage to move them into. A short summary + 30-day report link is posted to Workable.',
      confirmLabel: 'Advance',
      confirmClass: 'rq-approve',
      placeholder: 'Optional note for the audit trail',
      requireStagePick: true,
    },
    alternatives: [
      {
        action: 'reject',
        label: 'Reject',
        icon: X,
        kicker: 'REJECT CANDIDATE',
        headline: 'Reject {name}?',
        body: 'This will disqualify them in Workable and send the rejection email.',
        confirmLabel: 'Reject',
        confirmClass: 'rq-override',
      },
    ],
  },
  reject: {
    // Primary = approve the agent's reject (fires immediately). Labeled
    // "Approve" to match the bulk action and avoid colliding with the
    // REJECT type badge — the recruiter is approving a decision, not
    // independently rejecting. Outcome is conveyed by the badge + body.
    primaryLabel: 'Approve',
    alternatives: [
      {
        action: 'send_assessment',
        label: 'Send assessment',
        icon: Send,
        kicker: 'OVERRIDE TO SEND',
        headline: 'Send the assessment to {name} instead?',
        body: "Dispatches the assessment invite. The agent will recalibrate based on your reason.",
        confirmLabel: 'Send assessment',
        confirmClass: 'rq-approve',
      },
      {
        action: 'advance',
        label: 'Advance instead',
        icon: ArrowRight,
        kicker: 'OVERRIDE TO ADVANCE',
        headline: 'Advance {name} instead?',
        body: "Pick the Workable stage to move them into. Skips the rejection email.",
        confirmLabel: 'Advance',
        confirmClass: 'rq-approve',
        requireStagePick: true,
      },
    ],
  },
  skip_assessment_reject: {
    // No inline overrides for pre-screen reject. The agent has flagged
    // the CV as not worth assessing (often fraud / hard-constraint
    // failures like salary mismatch caught from Workable answers); a
    // one-click "Send assessment anyway" trains recruiters to ignore
    // the cost-protection signal and drains assessment credits on
    // candidates that shouldn't be tested. If the recruiter disagrees,
    // the right path is ``Send back & teach`` — that produces a
    // learning signal and re-runs the agent with the new context. The
    // universals (teach + snooze) are appended by the renderer.
    // Primary = approve the agent's reject; labeled "Approve" to match the
    // bulk action and the REJECT (PRE-SCREEN) badge carries the outcome.
    primaryLabel: 'Approve',
    alternatives: [],
  },
  resend_assessment_invite: {
    primaryLabel: 'Resend invite',
    primaryIcon: Repeat,
    alternatives: [
      {
        action: 'reject',
        label: 'Reject',
        icon: X,
        kicker: 'REJECT CANDIDATE',
        headline: 'Reject {name}?',
        body: 'This will disqualify them in Workable and send the rejection email.',
        confirmLabel: 'Reject',
        confirmClass: 'rq-override',
      },
      {
        action: 'skip_assessment_advance',
        label: 'Skip & advance',
        icon: ArrowRight,
        kicker: 'SKIP ASSESSMENT',
        headline: 'Skip the assessment and move {name} to the advance queue?',
        body: "Skips resending the invite and queues them as an advance. You'll pick the Workable stage when you approve the advance from the queue — nothing posts to Workable yet.",
        confirmLabel: 'Move to advance queue',
        confirmClass: 'rq-approve',
      },
    ],
  },
};

// Fallback for any decision_type not mapped above (e.g. legacy or
// escalate_low_confidence). Single generic Approve + the universals.
const DEFAULT_ACTIONS = {
  primaryLabel: 'Approve',
  primaryIcon: Check,
  alternatives: [],
};

// Two real states for the action queue. Returned/Approved/Overrides/All were
// removed: they don't change the (always-pending) queue sidebar here — they're
// decision history, which lives in Monitoring → History. 'stale' ("Needs
// re-eval") is a client-side lens over pending (see filters.status handling).
const STATUS_TABS = [
  { id: 'pending', label: 'Pending', hint: 'Every decision waiting for your approval' },
  { id: 'stale', label: 'Needs re-eval', hint: 'Pending decisions whose score is out of date — older scoring model or changed inputs. Re-evaluate before acting.' },
];

// 'advance' and 'assessment' are categories — the backend expands them to
// their underlying decision_types (advance → advance_to_interview;
// assessment → send_assessment + resend_assessment_invite). 'reject' and
// 'skip_assessment_reject' map 1:1 to their decision_type so the Hub
// distinguishes the pre-screen reject from a post-assessment reject.
const TYPE_OPTIONS = [
  { id: '', label: 'All types', hint: 'All decision types' },
  { id: 'advance', label: 'Advance', hint: 'Advance the candidate to the next stage' },
  { id: 'assessment', label: 'Send assessment', hint: 'Send or resend an assessment invite' },
  { id: 'reject', label: 'Reject', hint: 'Reject after scoring / assessment' },
  { id: 'skip_assessment_reject', label: 'Reject (pre-screen)', hint: 'Rejected at pre-screen, before any assessment' },
];

const Toolbar = ({ filters, setFilters, roles, bulkAction, staleCount }) => (
  <div className="rq-toolbar">
    <div className="rq-toolbar-l">
      <span className="kicker mute" style={{ marginRight: 8 }}>ROLE</span>
      <select
        className="rq-select"
        value={filters.role_id || ''}
        onChange={(e) => setFilters((f) => ({ ...f, role_id: e.target.value || null }))}
        aria-label="Select a role to scope the view"
      >
        <option value="">All roles</option>
        {roles.map((r) => (
          <option key={r.role_id} value={r.role_id} title={r.name}>{r.name || r.short_name}</option>
        ))}
      </select>
      <div className="rq-tabset" role="group" aria-label="Filter by decision type">
        {TYPE_OPTIONS.map((o) => (
          <button
            key={o.id || 'all'}
            type="button"
            className={(!filters.view && (filters.type || '') === o.id) ? 'on' : ''}
            title={o.hint}
            onClick={() => setFilters((f) => ({ ...f, type: o.id || null, view: null }))}
          >
            {o.label}
          </button>
        ))}
        {/* Not a decision-type — switches the queue to the invited-candidate
            tracker (sent-but-not-completed assessments). Sits next to the
            decision-type pills so it reads as part of the same control. */}
        <button
          type="button"
          className={filters.view === 'invited' ? 'on' : ''}
          onClick={() => setFilters((f) => ({ ...f, view: f.view === 'invited' ? null : 'invited' }))}
          title="Candidates sent an assessment that hasn't been completed yet"
        >
          Assessment pending
        </button>
      </div>
      <div className="rq-tabset" role="group" aria-label="Filter the queue">
        {STATUS_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={(!filters.view && filters.status === t.id) ? 'on' : ''}
            title={t.hint}
            onClick={() => setFilters((f) => ({ ...f, status: t.id, view: null }))}
          >
            {t.label}{t.id === 'stale' && staleCount > 0 ? ` ${staleCount}` : ''}
          </button>
        ))}
      </div>
    </div>
    <div className="rq-toolbar-r">
      {bulkAction}
      <span className="rq-search">
        <Search size={13} strokeWidth={2} aria-hidden="true" />
        <input
          placeholder="Search candidates, IDs, reasoning…"
          value={filters.q || ''}
          onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value || null }))}
          aria-label="Search decisions"
        />
      </span>
    </div>
  </div>
);

// The candidate-pipeline funnel for the scoped role, surfaced next to the
// pending queue so a recruiter knows the denominator before advancing more
// — "I've got 17 pending, but I've already advanced 10" is the question this
// answers. Renders the shared B2 <FunnelBoard> (stages only — the org decision
// breakdown lives in the hero) so it matches the role-detail funnel exactly.
// Counts come from role.stage_counts on /agent/roles/breakdown.
const PipelineStandingStrip = ({ rolesBreakdown, filters }) => {
  // Both the stage counts AND the pending-decision breakdown come from
  // rolesBreakdown (each role carries stage_counts + pending_decisions_by_type),
  // so the role-scoped funnel shows that role's real decisions instead of
  // lumping everyone into "decision pending".
  const { counts, decisionsByType, scopeLabel } = useMemo(() => {
    const roles = Array.isArray(rolesBreakdown) ? rolesBreakdown : [];
    if (filters.role_id) {
      const role = roles.find((r) => String(r.role_id) === String(filters.role_id));
      return {
        counts: role?.stage_counts || null,
        decisionsByType: role?.pending_decisions_by_type || {},
        scopeLabel: role?.short_name || role?.name || `Role #${filters.role_id}`,
      };
    }
    // No role filter → sum each stage + decision type across every role.
    const sum = (key) => roles.reduce((acc, r) => {
      const obj = r?.[key] || {};
      for (const k of Object.keys(obj)) acc[k] = (acc[k] || 0) + (Number(obj[k]) || 0);
      return acc;
    }, {});
    const summed = sum('stage_counts');
    return {
      counts: Object.keys(summed).length ? summed : null,
      decisionsByType: sum('pending_decisions_by_type'),
      scopeLabel: 'all roles',
    };
  }, [rolesBreakdown, filters.role_id]);

  if (!counts) return null;
  // Nothing in the pipeline at all → no point showing an all-zero board.
  if (PIPELINE_FUNNEL_STAGES.every((s) => (Number(counts[s.key]) || 0) === 0)) return null;

  return <FunnelBoard stageCounts={counts} decisionsByType={decisionsByType} scopeLabel={scopeLabel} />;
};

const PendingSidebar = ({ pending, selectedId, onSelect, loading, onNavigate, staleOnly = false }) => {
  // The list is sorted by score, so the oldest item is no longer at a fixed
  // position — derive its age explicitly for the header label.
  const oldestCreatedAt = pending.reduce((oldest, p) => {
    if (!p?.created_at) return oldest;
    if (!oldest || new Date(p.created_at) < new Date(oldest)) return p.created_at;
    return oldest;
  }, null);
  return (
  <aside className="rq-split-list">
    <div className="rq-split-list-head">
      <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.875rem', fontWeight: 600, color: 'var(--ink)' }}>
        {staleOnly ? 'Needs re-eval' : 'Pending'} <span style={{ color: 'var(--purple)', marginLeft: 4 }}>{pending.length}</span>
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.65625rem', color: 'var(--mute)', letterSpacing: '.06em' }}>
        {oldestCreatedAt ? `OLDEST ${formatRelativeAge(oldestCreatedAt)}` : ''}
      </span>
    </div>
    <div className="rq-split-list-body">
      {loading && pending.length === 0 ? (
        <div style={{ padding: 16, fontSize: '0.8125rem', color: 'var(--mute)' }}>Loading…</div>
      ) : pending.length === 0 ? (
        <div className="home-empty" style={{ margin: 6 }}>
          <Inbox size={18} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
          <div>{staleOnly ? 'No candidates need re-evaluation right now.' : 'Queue is empty. The agent is running unattended.'}</div>
        </div>
      ) : (
        pending.map((p) => (
          // role="button" instead of a real <button> so the inline <a>
          // candidate-name link below isn't an interactive child of an
          // interactive parent (invalid HTML, breaks click + keyboard
          // semantics in some browsers / AT). Same pattern HomeEverything
          // uses for its history rows.
          <div
            key={p.id}
            role="button"
            tabIndex={0}
            className={`rq-split-row ${selectedId === p.id ? 'on' : ''} ${p.status === 'processing' ? 'is-processing' : ''}`.trim()}
            onClick={() => onSelect(p.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onSelect(p.id);
              }
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
              <TypeBadge type={p.decision_type} size="sm" />
              <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
                <ScoreChip score={p.taali_score} size="sm" />
                <ScoreProvenance provenance={p?.score_summary?.score_provenance} density="pill" />
              </span>
              {p.is_stale ? (
                <span
                  title="Score out of date — re-evaluate"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 3, fontFamily: 'var(--font-mono)',
                    fontSize: '0.5625rem', letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--purple)',
                    background: 'var(--purple-soft)', borderRadius: 4, padding: '1px 5px', whiteSpace: 'nowrap',
                  }}
                >
                  <RefreshCw size={9} strokeWidth={2.4} aria-hidden="true" /> re-eval
                </span>
              ) : null}
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.625rem', color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                {formatRelativeAge(p.created_at)}
              </span>
            </div>
            <div style={{ fontSize: '0.8125rem', fontWeight: 500, color: 'var(--ink)', lineHeight: 1.35 }}>
              <a
                href={pathForPage('candidate-report', { candidateApplicationId: p.application_id, fromHome: true })}
                target="_blank"
                rel="noopener noreferrer"
                className="rq-inline-link"
                style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', textDecoration: 'none' }}
                onClick={(e) => e.stopPropagation()}
                title="Open candidate report in a new tab"
              >
                {p.candidate_name || `Application #${p.application_id}`}
              </a>
            </div>
            {(p.role_name || p.role_id != null) ? (
              <div style={{ marginTop: 5, minWidth: 0 }}>
                <RolePill roleName={p.role_name} roleId={p.role_id} />
              </div>
            ) : null}
            <div style={{ fontSize: '0.6875rem', color: 'var(--mute)', marginTop: 5, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: 'var(--font-mono)', letterSpacing: '.04em' }}>#{p.id}</span>
              {p.confidence != null ? (
                <>
                  <span style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--bg-3)', overflow: 'hidden', maxWidth: 50 }}>
                    <span style={{ display: 'block', height: '100%', width: `${(p.confidence || 0) * 100}%`, background: p.confidence >= 0.9 ? 'var(--green)' : 'var(--purple)' }} />
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--ink-2)' }}>
                    {Math.round((p.confidence || 0) * 100)}%
                  </span>
                </>
              ) : null}
            </div>
          </div>
        ))
      )}
    </div>
    <div style={{
      padding: '10px 14px', borderTop: '1px solid var(--line)', fontFamily: 'var(--font-mono)',
      fontSize: '0.65625rem', color: 'var(--mute)', letterSpacing: '.06em',
      display: 'flex', alignItems: 'center', gap: 6,
    }}>
      <ListChecks size={12} aria-hidden="true" />
      <span>If queue empties, agent runs unattended.</span>
    </div>
  </aside>
  );
};

// Exported so the public demo showcase (HomeShowcaseView) can render the
// exact same decision-detail + action bar the recruiter sees on /home,
// wired to mock handlers instead of the live API.
export const DecisionDetail = ({ decision, onApprove, onAlternative, onTeach, onSnooze, onNavigate, onReEvaluate, busy }) => {
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
          <div className="rq-detail-links" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 10 }}>
            <DeepLinkRow
              Icon={FileText}
              label="Open candidate report"
              href={pathForPage('candidate-report', { candidateApplicationId: decision.application_id, fromHome: true })}
            />
            <DeepLinkRow
              Icon={Eye}
              label="Open job pipeline"
              onClick={() => onNavigate?.('job-pipeline', { roleId: decision.role_id })}
            />
          </div>
        </div>
        {decision.taali_score != null ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            <ScoreRing score={decision.taali_score} size={72} label="TALI" />
            <ScoreProvenance provenance={decision?.score_summary?.score_provenance} density="full" />
          </div>
        ) : null}
      </div>

      <p style={{ margin: '0 0 14px', fontSize: '0.875rem', color: 'var(--ink-2)', lineHeight: 1.55, maxWidth: 760 }}>
        {decision.reasoning}
      </p>

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
              <div className="rq-ev-v" style={{ color: e.good === true ? 'var(--green)' : e.good === false ? 'var(--red)' : 'var(--ink)' }}>
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

      {decision.status === 'pending' || decision.status === 'reverted_for_feedback' ? (
        (() => {
          const spec = DECISION_ACTIONS[decision.decision_type] || DEFAULT_ACTIONS;
          const PrimaryIcon = spec.primaryIcon || Check;
          return (
            <div className="rq-action-bar">
              <div className="rq-action-l">
                {isStale && onReEvaluate ? (
                  <button
                    type="button"
                    className="rq-btn rq-approve"
                    onClick={() => onReEvaluate(decision)}
                    disabled={busy}
                  >
                    <RefreshCw size={14} strokeWidth={2.4} aria-hidden="true" />
                    Re-evaluate
                  </button>
                ) : null}
                <button
                  type="button"
                  className={`rq-btn ${spec.primaryClass || 'rq-approve'}`}
                  onClick={() => onApprove(decision)}
                  disabled={busy}
                  title={
                    staleEngineOnly
                      ? 'Scored by an older model — this approves the old score as-is. Re-evaluate to re-score first.'
                      : isStale
                        ? 'Inputs changed since this was decided — this acts on them anyway. Re-evaluate first to refresh.'
                        : undefined
                  }
                >
                  <PrimaryIcon size={14} strokeWidth={2.4} aria-hidden="true" />
                  {spec.primaryLabel}
                </button>
                {(spec.alternatives || []).map((alt) => {
                  const AltIcon = alt.icon || X;
                  return (
                    <button
                      key={alt.action}
                      type="button"
                      className="rq-btn rq-override"
                      onClick={() => onAlternative(decision, alt)}
                      disabled={busy}
                      title={alt.body}
                    >
                      <AltIcon size={14} strokeWidth={2} aria-hidden="true" />
                      {alt.label}
                    </button>
                  );
                })}
                <button type="button" className="rq-btn rq-teach" onClick={() => onTeach(decision)} disabled={busy}>
                  <Brain size={14} strokeWidth={2} aria-hidden="true" />
                  Send back &amp; teach
                </button>
              </div>
              <button type="button" className="rq-btn rq-defer" onClick={() => onSnooze(decision)} disabled={busy}>
                Snooze 1h
              </button>
            </div>
          );
        })()
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


// Invited-candidate tracker — the Home "Assessment pending" view. Lists
// candidates with a sent-but-not-completed assessment + delivery tracking
// (Invited / Delivered / Opened / Started). These aren't agent decisions
// (those leave the queue once approved) — they're assessments in flight, so
// this is a flat list rather than the decision split-view.
const InvitedPanel = ({ candidates, loading, roleNameById }) => {
  if (loading) {
    return (
      <div className="rq-empty">
        <RefreshCw size={16} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
        <div>Loading invited candidates…</div>
      </div>
    );
  }
  if (!candidates.length) {
    return (
      <div className="rq-empty">
        <Inbox size={18} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
        <div>No assessments awaiting completion. Invites you've sent that haven't been started or completed show up here.</div>
      </div>
    );
  }
  return (
    <div className="rq-invited-list">
      {candidates.map((c) => {
        const ss = c.score_summary || {};
        const tracking = ss.invite_tracking || {};
        const roleName = c.role?.name || roleNameById?.(c.role_id) || null;
        return (
          <a
            key={c.id}
            className="rq-split-row rq-invited-row"
            href={pathForPage('candidate-report', { candidateApplicationId: c.id, fromHome: true })}
          >
            <Avatar initials={initialsFrom(c.candidate_name || c.candidate_email)} size={34} />
            <div className="rq-invited-main">
              <div className="rq-invited-name">{c.candidate_name || c.candidate_email}</div>
              <div className="rq-invited-meta">
                <RolePill roleName={roleName} roleId={c.role_id} />
                <ScoreChip score={ss.taali_score} size="sm" />
              </div>
              <div className="rq-invited-chips">
                <AssessmentInviteChip status={ss.assessment_status} tracking={tracking} />
              </div>
            </div>
            <span className="rq-invited-age">
              {tracking.invite_sent_at ? formatRelativeAge(tracking.invite_sent_at) : ''}
            </span>
          </a>
        );
      })}
    </div>
  );
};

export const HomeNow = ({
  decisions,
  pendingOrdered,
  selectedId,
  setSelectedId,
  loading,
  filters,
  setFilters,
  rolesBreakdown,
  reload,
  onNavigate,
  // When the agent chat dock is present it owns the agent's questions, so the
  // feed hides its own needs-input block to avoid duplicating them.
  questionsInDock = false,
}) => {
  const { showToast } = useToast() || { showToast: () => {} };
  const [busyId, setBusyId] = useState(null);
  // Invited-candidate tracker ("Assessment pending" view). Fetched on demand
  // from the applications list — these are sent assessments, not decisions, so
  // they don't ride the decision queue's data flow.
  const invitedView = filters.view === 'invited';
  const [invited, setInvited] = useState([]);
  const [invitedLoading, setInvitedLoading] = useState(false);
  const roleNameById = useCallback((id) => {
    const match = (rolesBreakdown || []).find((r) => String(r.role_id) === String(id));
    return match ? (match.name || match.short_name) : null;
  }, [rolesBreakdown]);
  useEffect(() => {
    if (!invitedView) return undefined;
    let cancelled = false;
    setInvitedLoading(true);
    rolesApi
      .listApplicationsGlobal({
        assessment_status: 'pending,in_progress',
        role_id: filters.role_id || undefined,
        limit: 100,
        include_stage_counts: false,
        sort_by: 'pipeline_stage_updated_at',
        sort_order: 'desc',
      })
      .then((res) => {
        if (cancelled) return;
        const items = Array.isArray(res?.data?.items) ? res.data.items : [];
        setInvited(items);
      })
      .catch((err) => {
        if (cancelled) return;
        setInvited([]);
        showToast?.(apiErrorMessage(err, "Couldn't load invited candidates."), 'error');
      })
      .finally(() => {
        if (!cancelled) setInvitedLoading(false);
      });
    return () => { cancelled = true; };
  }, [invitedView, filters.role_id, showToast]);
  const [teachFor, setTeachFor] = useState(null);
  // Alternative-action confirmation modal target. When set, OverrideModal
  // is rendered with the decision + the chosen alternative spec. Used for
  // both override flows (Reject / Skip & advance / Advance instead) AND
  // the primary Advance-to-interview confirmation (mode: 'approve').
  const [alternativeFor, setAlternativeFor] = useState(null);
  // Workable stages keyed by role shortcode. Each entry is one of:
  //   undefined  → never requested
  //   'loading'  → fetch in flight
  //   'error'    → fetch failed (retryable — re-requesting refetches)
  //   array      → loaded (an empty array means the role genuinely has none)
  // We keep these states distinct so a transient fetch failure can't be
  // mistaken for "no stages" and, crucially, isn't cached forever: a single
  // Workable hiccup used to poison the cache so the picker showed "no stages"
  // until a full page reload.
  const [stagesByShortcode, setStagesByShortcode] = useState({});
  // Mirror of each shortcode's load status for synchronous dedupe decisions,
  // so ``ensureStages`` can skip in-flight/loaded fetches without running a
  // side-effect inside a setState updater (updaters must stay pure).
  const stagesStatusRef = useRef({});

  // Optimistic approvals. Approving a decision is async server-side: the
  // backend just flips it to ``processing`` and hands the heavy work (GitHub
  // branch + invite dispatch, serialized per org) to a worker. So there's no
  // reason to block the recruiter's click on the round-trip — we reflect the
  // action instantly here: the row drops out of the queue, selection advances
  // to the next, and we reconcile when fresh data lands. ``acted`` holds the
  // ids approved-but-not-yet-confirmed; each handler removes its own ids in a
  // finally so a *failed* send returns the card to the queue.
  const [acted, setActed] = useState(() => new Set());
  // Ref mirror so synchronous helpers (advanceFrom) can read the latest set
  // without waiting for the state update to flush.
  const actedRef = useRef(acted);
  useEffect(() => { actedRef.current = acted; }, [acted]);

  // Client-side role-scope guard. The parent fetches role-scoped data, but
  // while a role switch is mid-flight it keeps the *previous* scope's rows on
  // screen (stale-while-revalidate — see HomePage.loadDecisions) to avoid a
  // blank flash. Without this guard that means the queue briefly shows another
  // role's candidates under the newly-selected role's funnel — the
  // "I selected a role but the list still shows everyone" confusion. Scoping
  // the displayed rows to filters.role_id makes the queue + feed match the
  // funnel the instant you select, regardless of fetch latency; the server
  // fetch is still the source of truth and fills in the complete set.
  const inRoleScope = useCallback(
    (d) => !filters.role_id || String(d?.role_id) === String(filters.role_id),
    [filters.role_id],
  );

  // Overlays applied to the server data: approved-in-flight rows leave the
  // pending sidebar entirely (the queue visibly shrinks) and show as
  // ``processing`` in the activity feed (greyed, not gone).
  // "Needs re-eval" is a lens over the pending queue, driven by the status pill
  // (filters.status === 'stale', fetched as pending): same rows, filtered to
  // those whose score is stale (older model or changed inputs). The count is
  // the stale total in scope so the pill advertises how many need attention.
  const staleOnly = filters.status === 'stale';
  const stalePendingCount = useMemo(
    () => pendingOrdered.filter((d) => inRoleScope(d) && !acted.has(d.id) && d.is_stale).length,
    [pendingOrdered, acted, inRoleScope],
  );
  const effPending = useMemo(
    () => pendingOrdered.filter(
      (d) => inRoleScope(d) && !acted.has(d.id) && (!staleOnly || d.is_stale),
    ),
    [pendingOrdered, acted, inRoleScope, staleOnly],
  );
  const effDecisions = useMemo(
    () => decisions
      .filter(inRoleScope)
      .map((d) => (acted.has(d.id) ? { ...d, status: 'processing' } : d)),
    [decisions, acted, inRoleScope],
  );

  const selected = useMemo(
    () => effDecisions.find((d) => d.id === selectedId)
      || effPending.find((d) => d.id === selectedId)
      || effPending[0]
      || null,
    [effDecisions, effPending, selectedId],
  );

  // After approving ``id``, focus the next still-pending decision so the
  // recruiter can keep moving (send, send, send) without re-clicking the list.
  const advanceFrom = useCallback((id) => {
    const skip = (d) => d.id === id || actedRef.current.has(d.id);
    const idx = pendingOrdered.findIndex((d) => d.id === id);
    const after = idx >= 0 ? pendingOrdered.slice(idx + 1).find((d) => !skip(d)) : null;
    const next = after || pendingOrdered.find((d) => !skip(d)) || null;
    setSelectedId(next ? next.id : null);
  }, [pendingOrdered, setSelectedId]);

  // Lazy-load a role's Workable stages, keyed by shortcode. Drives both the
  // single-decision modal and the per-role pickers in the bulk-approve modal.
  // Skips fetches that are already in flight or successfully loaded, but a
  // prior 'error' (or never-fetched) shortcode is (re)fetched — so simply
  // re-opening the modal recovers from a transient Workable failure.
  const ensureStages = useCallback((shortcode) => {
    if (!shortcode) return;
    const status = stagesStatusRef.current[shortcode];
    if (status === 'loading' || status === 'ready') return;
    stagesStatusRef.current[shortcode] = 'loading';
    setStagesByShortcode((p) => ({ ...p, [shortcode]: 'loading' }));
    orgsApi
      .getWorkableStages({ shortcode })
      .then((res) => {
        const list = Array.isArray(res?.data?.stages) ? res.data.stages : [];
        stagesStatusRef.current[shortcode] = 'ready';
        setStagesByShortcode((p) => ({ ...p, [shortcode]: list }));
      })
      .catch(() => {
        stagesStatusRef.current[shortcode] = 'error';
        setStagesByShortcode((p) => ({ ...p, [shortcode]: 'error' }));
      });
  }, []);

  // Lazy-fetch the selected decision's role stages so the single-decision
  // modal's picker is ready when it opens.
  useEffect(() => {
    ensureStages(selected?.workable_job_id);
  }, [selected?.workable_job_id, ensureStages]);

  const handleApprove = async (decision) => {
    // ``advance_to_interview`` opens the same confirmation modal as the
    // overrides — the recruiter picks the Workable target stage there.
    const spec = DECISION_ACTIONS[decision.decision_type];
    if (spec?.primary) {
      setAlternativeFor({ decision, alternative: spec.primary });
      return;
    }
    // Optimistic + async. The backend only flips the decision to ``processing``
    // and runs the heavy send (GitHub branch + invite) in a background worker,
    // so reflect the action instantly: drop the card from the queue and advance
    // to the next. The click feels instant regardless of GitHub/Workable latency.
    setActed((prev) => new Set(prev).add(decision.id));
    advanceFrom(decision.id);
    showToast?.(
      decision.decision_type === 'send_assessment' ? 'Sending assessment…'
        : decision.decision_type === 'resend_assessment_invite' ? 'Resending invite…'
          : (decision.decision_type === 'reject' || decision.decision_type === 'skip_assessment_reject') ? 'Rejecting…'
            : 'Approved.',
      'success',
    );
    try {
      await agentApi.approveDecision(decision.id, {}, { force: Boolean(decision.is_stale) });
      await reload?.();
    } catch (err) {
      // The send didn't take — return the card to the queue and refocus it so
      // the recruiter sees why. We never silently drop a failed send.
      setSelectedId(decision.id);
      if (isDecisionStaleError(err)) {
        showToast?.("This decision's inputs changed — re-evaluate to refresh it.", 'warning');
      } else {
        showToast?.(apiErrorMessage(err, "Couldn't send — returned to your queue."), 'error');
      }
      await reload?.();
    } finally {
      // Drop the optimistic mark: on success the server now reports the row as
      // processing (already gone from the pending list); on failure it's still
      // pending, so clearing the mark makes the card reappear in the queue.
      setActed((prev) => { const next = new Set(prev); next.delete(decision.id); return next; });
    }
  };

  // A4: discard a stale decision and re-run the agent on fresh inputs.
  const handleReEvaluate = async (decision) => {
    setBusyId(decision.id);
    try {
      await agentApi.reEvaluateDecision(decision.id);
      showToast?.('Re-evaluating with fresh inputs…', 'success');
      await reload?.();
    } catch (err) {
      showToast?.(apiErrorMessage(err, 'Re-evaluate failed'), 'error');
    } finally {
      setBusyId(null);
    }
  };

  // Open OverrideModal for the chosen alternative. The actual POST
  // happens inside the modal so the recruiter has to fill in the
  // required "why" textarea before submitting.
  const handleAlternative = (decision, alternative) => {
    setAlternativeFor({ decision, alternative });
  };

  const handleSnooze = async (decision) => {
    setBusyId(decision.id);
    try {
      await agentApi.snoozeDecision(decision.id);
      showToast?.('Snoozed for 1h.', 'success');
      await reload?.();
    } catch (err) {
      showToast?.(apiErrorMessage(err, 'Snooze failed'), 'error');
    } finally {
      setBusyId(null);
    }
  };

  // Pending decisions matching the current filter scope. Used by the
  // bulk-approve action: we only ever approve what's visible, so the
  // recruiter's confirmation matches the rows they see on screen.
  const visiblePending = useMemo(() => effDecisions.filter((d) => d.status === 'pending'), [effDecisions]);
  // "Skip & advance" only makes sense for the assessment decisions — it skips
  // the assessment and re-queues the candidate as an advance. It's meaningless
  // (and a no-op the server would reject) for an advance or reject decision, so
  // in the advance / reject queues there are no targets and the bulk button is
  // hidden. In a mixed "all types" view it acts on just the assessment subset.
  const skipAdvanceTargets = useMemo(
    () => visiblePending.filter(
      (d) => d.decision_type === 'send_assessment'
        || d.decision_type === 'resend_assessment_invite',
    ),
    [visiblePending],
  );

  const [bulkBusy, setBulkBusy] = useState(false);
  // Bulk-approve confirmation target. null = modal closed. We snapshot the
  // ids/summary at open time so the confirmation reflects the rows the
  // recruiter saw, even if the queue reloads underneath the modal. Replaces
  // the native window.confirm so the dialog uses the app's design tokens.
  const [bulkConfirm, setBulkConfirm] = useState(null);
  // Recruiter's per-role Workable stage picks for the bulk-approve modal,
  // keyed by role_id. Only the advancing roles need one; reset each open.
  const [bulkStages, setBulkStages] = useState({});

  const handleBulkApprove = () => {
    if (bulkBusy || visiblePending.length === 0) return;
    const typeLabel = filters.type
      ? (TYPE_OPTIONS.find((o) => o.id === filters.type)?.label || 'decision').toLowerCase()
      : 'pending decision';
    const roleScope = filters.role_id
      ? (rolesBreakdown.find((r) => String(r.role_id) === String(filters.role_id))?.short_name
        || rolesBreakdown.find((r) => String(r.role_id) === String(filters.role_id))?.name
        || `role #${filters.role_id}`)
      : 'all roles';
    const count = visiblePending.length;
    const sample = visiblePending
      .slice(0, 3)
      .map((d) => d.candidate_name || `#${d.id}`)
      .join(', ');
    const more = count > 3 ? ` and ${count - 3} more` : '';
    const ids = visiblePending.map((d) => Number(d.id));
    // Only ``advance_to_interview`` approvals move the candidate in Workable,
    // and only when the role is linked to a Workable job (has a shortcode).
    // Group those by role so we can ask for one target stage per role —
    // a bulk set can span roles, each with its own Workable stage list.
    const advanceRolesMap = new Map();
    for (const d of visiblePending) {
      if (d.decision_type !== 'advance_to_interview' || !d.workable_job_id) continue;
      const key = Number(d.role_id);
      if (!advanceRolesMap.has(key)) {
        advanceRolesMap.set(key, {
          role_id: key,
          role_name: d.role_name || `Role #${key}`,
          shortcode: d.workable_job_id,
          count: 0,
        });
      }
      advanceRolesMap.get(key).count += 1;
    }
    const advanceRoles = [...advanceRolesMap.values()];
    setBulkStages({});
    setBulkConfirm({ count, typeLabel, roleScope, sample, more, ids, advanceRoles });
  };

  const runBulkApprove = async () => {
    if (!bulkConfirm) return;
    const { ids, count } = bulkConfirm;
    const stages = { ...bulkStages };
    setBulkConfirm(null);
    setBulkBusy(true);
    // Optimistic: clear the whole batch from the queue immediately so the click
    // feels instant. Rows that fail (partial failure / network error) reappear
    // when fresh data lands in the finally below.
    setActed((prev) => { const next = new Set(prev); ids.forEach((id) => next.add(id)); return next; });
    try {
      const res = await agentApi.bulkApproveDecisions(
        ids,
        null,
        Object.keys(stages).length ? stages : null,
      );
      const payload = res?.data || {};
      const approved = Number(payload.approved || 0);
      const failed = Array.isArray(payload.failures) ? payload.failures.length : 0;
      if (failed === 0) {
        showToast?.(`Approved ${approved} / ${count}.`, 'success');
      } else {
        showToast?.(`Approved ${approved} / ${count} — ${failed} failed.`, 'warning');
      }
      await reload?.();
    } catch (err) {
      showToast?.(apiErrorMessage(err, 'Bulk approve failed'), 'error');
      await reload?.();
    } finally {
      // Reconcile against the server: approved rows are now processing (gone
      // from the pending list), any that failed are still pending and reappear.
      setActed((prev) => { const next = new Set(prev); ids.forEach((id) => next.delete(id)); return next; });
      setBulkBusy(false);
      setBulkStages({});
    }
  };

  // Bulk counterpart of the per-card "Skip & advance": reclassify every visible
  // candidate into the advance queue WITHOUT sending the assessment. No stage
  // picker and no Workable write here — each card becomes a pending
  // advance_to_interview decision, and the recruiter picks the Workable stage
  // when approving the advance from the queue. Serialized per org server-side.
  const handleBulkSkipAdvance = async () => {
    const ids = skipAdvanceTargets.map((d) => d.id);
    if (!ids.length || bulkBusy) return;
    setBulkBusy(true);
    // Optimistic: clear the batch immediately; failures reappear on reload.
    setActed((prev) => { const next = new Set(prev); ids.forEach((id) => next.add(id)); return next; });
    try {
      const res = await agentApi.bulkOverrideDecisions(ids, 'skip_assessment_advance');
      const payload = res?.data || {};
      const accepted = Number(payload.accepted || 0);
      const failed = Array.isArray(payload.failures) ? payload.failures.length : 0;
      showToast?.(
        failed === 0
          ? `Moved ${accepted} / ${ids.length} to the advance queue.`
          : `Moved ${accepted} / ${ids.length} to the advance queue — ${failed} failed.`,
        failed === 0 ? 'success' : 'warning',
      );
      await reload?.();
    } catch (err) {
      showToast?.(apiErrorMessage(err, 'Bulk skip & advance failed'), 'error');
      await reload?.();
    } finally {
      setActed((prev) => { const next = new Set(prev); ids.forEach((id) => next.delete(id)); return next; });
      setBulkBusy(false);
    }
  };

  // When the bulk-confirm modal opens, prefetch the Workable stages for every
  // advancing role so each role's picker is ready.
  useEffect(() => {
    (bulkConfirm?.advanceRoles || []).forEach((r) => ensureStages(r.shortcode));
  }, [bulkConfirm, ensureStages]);

  // Gate the bulk Confirm: every advancing role must have a stage picked, or
  // genuinely have no stages to pick (the candidate then advances on Tali's
  // internal stage only). Hold Confirm while a role is still loading or
  // errored — an errored role shows a Retry control, so we don't let the
  // recruiter advance assuming a Workable move that never resolved.
  const bulkStagesReady = useMemo(() => {
    const roles = bulkConfirm?.advanceRoles || [];
    return roles.every((r) => {
      const raw = stagesByShortcode[r.shortcode];
      if (raw === undefined || raw === 'loading' || raw === 'error') return false;
      if (advanceableWorkableStages(raw).length === 0) return true; // nothing to pick
      return Boolean(bulkStages[r.role_id]);
    });
  }, [bulkConfirm, stagesByShortcode, bulkStages]);

  // Only on the plain Pending view: not the invited tracker, and not "Needs
  // re-eval" (status 'stale', which status==='pending' already excludes) —
  // bulk-approving stale scores is what we want the recruiter to stop and
  // re-evaluate instead.
  const bulkActionEl = !invitedView && filters.status === 'pending' && visiblePending.length > 0 ? (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <button
        type="button"
        className="btn btn-purple btn-sm"
        onClick={handleBulkApprove}
        disabled={bulkBusy}
      >
        <Check size={13} strokeWidth={2} aria-hidden="true" style={{ marginRight: 6, verticalAlign: '-2px' }} />
        {bulkBusy ? 'Approving…' : `Approve ${visiblePending.length} visible`}
      </button>
      {skipAdvanceTargets.length > 0 ? (
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={handleBulkSkipAdvance}
          disabled={bulkBusy}
          title="Skip the assessment and move these candidates to the advance queue (you pick the Workable stage when approving each advance)"
        >
          <ArrowRight size={13} strokeWidth={2} aria-hidden="true" style={{ marginRight: 6, verticalAlign: '-2px' }} />
          {bulkBusy ? 'Working…' : `Skip & advance ${skipAdvanceTargets.length} visible`}
        </button>
      ) : null}
    </div>
  ) : null;

  // Keyboard shortcuts on the action bar — only fire when no modal is
  // open, no input has focus, and the user actually has a selected
  // pending decision they could act on. We intentionally don't intercept
  // single keystrokes when a textarea/select/contenteditable is focused
  // so search-as-you-type stays usable.
  useEffect(() => {
    const onKey = (e) => {
      if (invitedView) return;  // invited tracker has no decision under focus
      if (teachFor || bulkConfirm) return;  // an open modal owns the keyboard
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const tag = (e.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (e.target?.isContentEditable) return;
      if (!selected) return;
      if (selected.status !== 'pending' && selected.status !== 'reverted_for_feedback') return;
      const k = e.key.toLowerCase();
      if (k === 'a') { e.preventDefault(); handleApprove(selected); return; }
      if (k === 't') { e.preventDefault(); setTeachFor(selected); return; }
      if (k === 's') { e.preventDefault(); handleSnooze(selected); return; }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
    // We deliberately depend on the selected decision and modal state
    // — re-binding on each pending row is cheap and keeps the closure
    // pointing at the right target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, selected?.status, teachFor, bulkConfirm, invitedView]);

  // Esc cancels / Enter confirms the bulk-approve modal.
  useEffect(() => {
    if (!bulkConfirm) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); setBulkConfirm(null); }
      if (e.key === 'Enter') { e.preventDefault(); runBulkApprove(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkConfirm]);

  return (
    <section className="home-section">
      <div className="home-section-head">
        <div>
          <span className="kicker">NOW · NEEDS YOU</span>
          <h3 className="home-section-title">Review queue<em>.</em></h3>
          <p className="home-section-sub">
            Every decision the agent makes that needs you. Approve, override, or teach it — your calls become its training signal.
          </p>
        </div>
      </div>

      <Toolbar
        filters={filters}
        setFilters={setFilters}
        roles={rolesBreakdown}
        bulkAction={bulkActionEl}
        staleCount={stalePendingCount}
      />

      {/* Funnel standing for the scoped role — how many are already advanced /
          in review / rejected — so the pending count has a denominator. */}
      <PipelineStandingStrip rolesBreakdown={rolesBreakdown} filters={filters} />

      {/* Open orchestrator questions across the org (or scoped to the
          toolbar's role filter when set). Hides itself when the queue
          is empty so a clean state renders nothing. Centralised here so
          recruiters answer everything from one place rather than having
          to bounce into each role page. */}
      {!questionsInDock && <AgentNeedsInputCard roleId={filters.role_id || undefined} />}

      {invitedView ? (
        <InvitedPanel
          candidates={invited}
          loading={invitedLoading}
          roleNameById={roleNameById}
        />
      ) : (
        <>
          <div className="rq-hybrid-grid">
            <PendingSidebar
              pending={effPending}
              selectedId={selected?.id}
              onSelect={setSelectedId}
              loading={loading}
              onNavigate={onNavigate}
              staleOnly={staleOnly}
            />
            <div className="rq-hybrid-right">
              <DecisionDetail
                decision={selected}
                busy={busyId === selected?.id}
                onApprove={handleApprove}
                onAlternative={handleAlternative}
                onReEvaluate={handleReEvaluate}
                onSnooze={handleSnooze}
                onTeach={(d) => setTeachFor(d)}
                onNavigate={onNavigate}
              />
            </div>
          </div>

          <ActivityFeed
            rows={effDecisions}
            selectedId={selected?.id}
            onSelect={setSelectedId}
            onNavigate={onNavigate}
          />
        </>
      )}

      {teachFor ? (
        <TeachModal
          decision={teachFor}
          onClose={() => setTeachFor(null)}
          onSubmitted={async () => {
            showToast?.('Feedback recorded. Decision returned to the queue.', 'success');
            await reload?.();
          }}
        />
      ) : null}

      {alternativeFor ? (
        <OverrideModal
          decision={alternativeFor.decision}
          alternative={alternativeFor.alternative}
          workableStages={(() => {
            const raw = stagesByShortcode[alternativeFor.decision?.workable_job_id];
            return Array.isArray(raw) ? raw : [];
          })()}
          onClose={() => setAlternativeFor(null)}
          onSubmitted={async () => {
            showToast?.(
              `${alternativeFor.alternative.confirmLabel || 'Override'} dispatched.`,
              'success',
            );
            await reload?.();
          }}
        />
      ) : null}

      {bulkConfirm ? (
        <div className="rq-modal-backdrop" onClick={() => setBulkConfirm(null)}>
          <div
            className="rq-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rq-bulk-title"
            style={{ width: 'min(480px, 100%)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="rq-modal-head">
              <div>
                <span className="kicker" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <Check size={11} aria-hidden="true" />
                  BULK APPROVE
                </span>
                <h3
                  id="rq-bulk-title"
                  className="home-title-md"
                  style={{ margin: '6px 0 2px' }}
                >
                  {`Approve ${bulkConfirm.count} ${bulkConfirm.typeLabel}${bulkConfirm.count === 1 ? '' : 's'} on ${bulkConfirm.roleScope}?`}
                </h3>
                {bulkConfirm.sample ? (
                  <p style={{ margin: 0, fontSize: '0.8125rem', color: 'var(--ink-2)', maxWidth: 420, lineHeight: 1.5 }}>
                    {`${bulkConfirm.sample}${bulkConfirm.more}`}
                  </p>
                ) : null}
              </div>
              <button type="button" className="rq-tinybtn" onClick={() => setBulkConfirm(null)} aria-label="Close">
                <X size={12} strokeWidth={2.2} />
              </button>
            </div>

            <div className="rq-modal-body">
              {bulkConfirm.advanceRoles.length > 0 ? (
                <div className="rq-modal-section">
                  <span className="rq-modal-label">
                    Move advancing candidates to which Workable stage? (required)
                  </span>
                  {bulkConfirm.advanceRoles.map((r) => {
                    const raw = stagesByShortcode[r.shortcode];
                    const stages = advanceableWorkableStages(raw);
                    const picked = bulkStages[r.role_id];
                    return (
                      <div key={r.role_id} style={{ marginTop: 10 }}>
                        <div style={{ fontSize: '0.78125rem', color: 'var(--ink-2)', marginBottom: 6 }}>
                          {r.role_name} · {r.count} advancing
                        </div>
                        {raw === undefined || raw === 'loading' ? (
                          <span style={{ fontSize: '0.75rem', color: 'var(--mute)' }}>Loading stages…</span>
                        ) : raw === 'error' ? (
                          <span style={{ fontSize: '0.75rem', color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                            Couldn&apos;t load Workable stages.
                            <button
                              type="button"
                              className="rq-tinybtn"
                              onClick={() => ensureStages(r.shortcode)}
                              style={{ display: 'inline-flex', alignItems: 'center', gap: 4, width: 'auto', padding: '2px 8px' }}
                            >
                              <RefreshCw size={11} strokeWidth={2} aria-hidden="true" />
                              Retry
                            </button>
                          </span>
                        ) : stages.length === 0 ? (
                          <span style={{ fontSize: '0.75rem', color: 'var(--mute)' }}>
                            No advance stages in this Workable job — only Sourced / Applied. These candidates advance on Tali's internal stage; nothing posts to Workable. Add interview/offer stages to the job in Workable to move them there.
                          </span>
                        ) : (
                          <div className="rq-modal-pills" role="radiogroup" aria-label={`Workable stage for ${r.role_name}`}>
                            {stages.map((stage) => {
                              const isOn = picked === stage.value;
                              return (
                                <button
                                  key={stage.value}
                                  type="button"
                                  role="radio"
                                  aria-checked={isOn}
                                  className={`rq-modal-pill ${isOn ? 'on' : ''}`}
                                  onClick={() => setBulkStages((prev) => ({ ...prev, [r.role_id]: stage.value }))}
                                >
                                  <span>{stage.label}</span>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : null}
              <p style={{ margin: bulkConfirm.advanceRoles.length > 0 ? '12px 0 0' : 0, fontSize: '0.8125rem', color: 'var(--mute)', lineHeight: 1.5 }}>
                This runs each approval in turn and reports any failures.
              </p>
            </div>

            <div className="rq-modal-foot">
              <button type="button" className="rq-btn ghost" onClick={() => setBulkConfirm(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="rq-btn rq-teach"
                onClick={runBulkApprove}
                disabled={!bulkStagesReady}
                title={bulkStagesReady ? undefined : 'Pick a Workable stage for each advancing role first'}
              >
                <Check size={13} strokeWidth={2} aria-hidden="true" />
                {`Approve ${bulkConfirm.count}`}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
};

export default HomeNow;
