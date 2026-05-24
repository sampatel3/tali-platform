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

import { agent as agentApi, organizations as orgsApi } from '../../shared/api';
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
import { OverrideModal, normalizeWorkableStages } from './OverrideModal';
import { ActivityFeed } from './ActivityFeed';
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
        headline: 'Skip the assessment and advance {name}?',
        body: 'Pick the Workable stage to move them into. Skips the assessment email.',
        confirmLabel: 'Advance',
        confirmClass: 'rq-approve',
        placeholder: 'e.g. Internal referral — pre-vetted, no need for an assessment',
        requireStagePick: true,
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
        headline: 'Skip the assessment and advance {name}?',
        body: 'Pick the Workable stage to move them into. Skips resending the invite.',
        confirmLabel: 'Advance',
        confirmClass: 'rq-approve',
        requireStagePick: true,
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

const STATUS_TABS = [
  { id: 'pending', label: 'Pending' },
  { id: 'reverted_for_feedback', label: 'Returned' },
  { id: 'approved', label: 'Approved' },
  { id: 'overridden', label: 'Overrides' },
  { id: 'all', label: 'All' },
];

// 'advance' is a category — the backend expands it to advance_to_interview
// + send_assessment + resend_assessment_invite. 'reject' and
// 'skip_assessment_reject' map 1:1 to their decision_type so the Hub
// distinguishes the pre-screen reject from a post-assessment reject.
const TYPE_OPTIONS = [
  { id: '', label: 'All types' },
  { id: 'advance', label: 'Advance' },
  { id: 'reject', label: 'Reject' },
  { id: 'skip_assessment_reject', label: 'Reject (pre-screen)' },
];

const Toolbar = ({ filters, setFilters, roles, bulkAction }) => (
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
            className={(filters.type || '') === o.id ? 'on' : ''}
            onClick={() => setFilters((f) => ({ ...f, type: o.id || null }))}
          >
            {o.label}
          </button>
        ))}
      </div>
      <div className="rq-tabset" role="group" aria-label="Filter by decision status">
        {STATUS_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={filters.status === t.id ? 'on' : ''}
            onClick={() => setFilters((f) => ({ ...f, status: t.id }))}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
    <div className="rq-toolbar-r" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
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

const PendingSidebar = ({ pending, selectedId, onSelect, loading, onNavigate }) => {
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
      <span style={{ fontFamily: 'var(--font-display)', fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
        Pending <span style={{ color: 'var(--purple)', marginLeft: 4 }}>{pending.length}</span>
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em' }}>
        {oldestCreatedAt ? `OLDEST ${formatRelativeAge(oldestCreatedAt)}` : ''}
      </span>
    </div>
    <div className="rq-split-list-body">
      {loading && pending.length === 0 ? (
        <div style={{ padding: 16, fontSize: 13, color: 'var(--mute)' }}>Loading…</div>
      ) : pending.length === 0 ? (
        <div className="home-empty" style={{ margin: 6 }}>
          <Inbox size={18} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
          <div>Queue is empty. The agent is running unattended.</div>
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <TypeBadge type={p.decision_type} size="sm" />
              <ScoreChip score={p.taali_score} size="sm" />
              {p.status === 'processing' ? (
                <span className="rq-proc-tag">Processing…</span>
              ) : null}
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mute)', letterSpacing: '.06em', marginLeft: 'auto' }}>
                {formatRelativeAge(p.created_at)}
              </span>
            </div>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)', lineHeight: 1.35 }}>
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
            <div style={{ fontSize: 11, color: 'var(--mute)', marginTop: 5, display: 'flex', alignItems: 'center', gap: 8 }}>
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
      fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.06em',
      display: 'flex', alignItems: 'center', gap: 6,
    }}>
      <ListChecks size={12} aria-hidden="true" />
      <span>If queue empties, agent runs unattended.</span>
    </div>
  </aside>
  );
};

const DecisionDetail = ({ decision, onApprove, onAlternative, onTeach, onSnooze, onNavigate, onReEvaluate, busy }) => {
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

  return (
    <section className="rq-hybrid-detail">
      <div className="rq-split-detail-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <TypeBadge type={decision.decision_type} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)', letterSpacing: '.06em' }}>
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

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 14 }}>
        <Avatar initials={initialsFrom(decision.candidate_name)} size={48} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 600, letterSpacing: '-.02em', lineHeight: 1.2, color: 'var(--ink)' }}>
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
          <div style={{ fontSize: 13, color: 'var(--mute)', marginTop: 2 }}>
            {decision.candidate_email || ''}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0, alignItems: 'center', marginRight: 16 }}>
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
        {decision.taali_score != null ? (
          <ScoreRing score={decision.taali_score} size={72} label="TALI" />
        ) : null}
      </div>

      <p style={{ margin: '0 0 14px', fontSize: 14, color: 'var(--ink-2)', lineHeight: 1.55, maxWidth: 760 }}>
        {decision.reasoning}
      </p>

      {isStale && (decision.status === 'pending' || decision.status === 'reverted_for_feedback') ? (
        <div className="rq-stale-banner" style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '0 0 14px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: 13, fontWeight: 500 }}>
          <RefreshCw size={14} strokeWidth={2} aria-hidden="true" />
          <span>
            Inputs changed since this was decided{stalenessSummary ? ` · ${stalenessSummary}` : ''}. Re-evaluate before approving.
          </span>
        </div>
      ) : null}

      {/* A pending decision with a resolution_note was returned to the queue
          (the action couldn't complete — e.g. the role has no assessment task).
          Surface the reason so the recruiter doesn't blindly re-approve into the
          same failure; a fresh pending decision has no note. */}
      {decision.status === 'pending' && decision.resolution_note ? (
        <div className="rq-returned-banner" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, margin: '0 0 14px', padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: 13, fontWeight: 500, lineHeight: 1.45 }}>
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
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--mute)', letterSpacing: '.08em', marginRight: 8, textTransform: 'uppercase' }}>{s.who || 'agent'}</span>
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
                  disabled={busy || isStale}
                  title={isStale ? 'Inputs changed — re-evaluate before approving' : undefined}
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
}) => {
  const { showToast } = useToast() || { showToast: () => {} };
  const [busyId, setBusyId] = useState(null);
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

  const selected = useMemo(
    () => decisions.find((d) => d.id === selectedId) || pendingOrdered[0] || null,
    [decisions, selectedId, pendingOrdered],
  );

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
    // Every other decision_type still fires immediately on click.
    const spec = DECISION_ACTIONS[decision.decision_type];
    if (spec?.primary) {
      setAlternativeFor({ decision, alternative: spec.primary });
      return;
    }
    setBusyId(decision.id);
    try {
      await agentApi.approveDecision(decision.id, {});
      showToast?.('Approved.', 'success');
      await reload?.();
    } catch (err) {
      if (isDecisionStaleError(err)) {
        // Inputs changed since this decision was queued — don't crash; nudge
        // the recruiter to re-evaluate (the CTA is rendered inline) and pull
        // fresh data so the stale badge appears.
        showToast?.("This decision's inputs changed — re-evaluate to refresh it.", 'warning');
        await reload?.();
      } else {
        showToast?.(apiErrorMessage(err, 'Approve failed'), 'error');
      }
    } finally {
      setBusyId(null);
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
  const visiblePending = useMemo(() => decisions.filter((d) => d.status === 'pending'), [decisions]);

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
    } finally {
      setBulkBusy(false);
      setBulkStages({});
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
      if (normalizeWorkableStages(raw).length === 0) return true; // nothing to pick
      return Boolean(bulkStages[r.role_id]);
    });
  }, [bulkConfirm, stagesByShortcode, bulkStages]);

  // The action only makes sense when looking at pending rows. Hide
  // otherwise so we don't promise to approve overridden / approved
  // history the user is just browsing.
  const bulkActionEl = filters.status === 'pending' && visiblePending.length > 0 ? (
    <button
      type="button"
      className="btn btn-purple btn-sm"
      onClick={handleBulkApprove}
      disabled={bulkBusy}
    >
      <Check size={13} strokeWidth={2} aria-hidden="true" style={{ marginRight: 6, verticalAlign: '-2px' }} />
      {bulkBusy ? 'Approving…' : `Approve ${visiblePending.length} visible`}
    </button>
  ) : null;

  // Keyboard shortcuts on the action bar — only fire when no modal is
  // open, no input has focus, and the user actually has a selected
  // pending decision they could act on. We intentionally don't intercept
  // single keystrokes when a textarea/select/contenteditable is focused
  // so search-as-you-type stays usable.
  useEffect(() => {
    const onKey = (e) => {
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
  }, [selected?.id, selected?.status, teachFor, bulkConfirm]);

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

      <Toolbar filters={filters} setFilters={setFilters} roles={rolesBreakdown} bulkAction={bulkActionEl} />

      {/* Open orchestrator questions across the org (or scoped to the
          toolbar's role filter when set). Hides itself when the queue
          is empty so a clean state renders nothing. Centralised here so
          recruiters answer everything from one place rather than having
          to bounce into each role page. */}
      <AgentNeedsInputCard roleId={filters.role_id || undefined} />

      <div className="rq-hybrid-grid">
        <PendingSidebar
          pending={pendingOrdered}
          selectedId={selected?.id}
          onSelect={setSelectedId}
          loading={loading}
          onNavigate={onNavigate}
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
        rows={decisions}
        selectedId={selected?.id}
        onSelect={setSelectedId}
        onNavigate={onNavigate}
      />

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
                  style={{ margin: '6px 0 2px', fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 600, letterSpacing: '-.02em', color: 'var(--ink)' }}
                >
                  {`Approve ${bulkConfirm.count} ${bulkConfirm.typeLabel}${bulkConfirm.count === 1 ? '' : 's'} on ${bulkConfirm.roleScope}?`}
                </h3>
                {bulkConfirm.sample ? (
                  <p style={{ margin: 0, fontSize: 13, color: 'var(--ink-2)', maxWidth: 420, lineHeight: 1.5 }}>
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
                    const stages = normalizeWorkableStages(raw);
                    const picked = bulkStages[r.role_id];
                    return (
                      <div key={r.role_id} style={{ marginTop: 10 }}>
                        <div style={{ fontSize: 12.5, color: 'var(--ink-2)', marginBottom: 6 }}>
                          {r.role_name} · {r.count} advancing
                        </div>
                        {raw === undefined || raw === 'loading' ? (
                          <span style={{ fontSize: 12, color: 'var(--mute)' }}>Loading stages…</span>
                        ) : raw === 'error' ? (
                          <span style={{ fontSize: 12, color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
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
                          <span style={{ fontSize: 12, color: 'var(--mute)' }}>
                            No Workable stages found for this role. These candidates' internal stage will still update; nothing posts to Workable.
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
              <p style={{ margin: bulkConfirm.advanceRoles.length > 0 ? '12px 0 0' : 0, fontSize: 13, color: 'var(--mute)', lineHeight: 1.5 }}>
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
