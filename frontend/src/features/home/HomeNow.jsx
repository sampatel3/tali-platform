// NOW — the V4 hybrid: pending sidebar (left) + selected detail (right) +
// activity feed (full-width below). The agent-first heart of /home.
//
// Filters live in `filters` (from the parent) and persist in URL search
// params. Approve / Override / Snooze hit the existing endpoints; Teach
// opens TeachModal which POSTs /agent/feedback.

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Button, SegmentedControl, Select } from '../../shared/ui/TaaliPrimitives';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ClipboardList,
  Inbox,
  ListChecks,
  RefreshCw,
  Search,
  UserPlus,
  X,
} from 'lucide-react';

import { agent as agentApi, organizations as orgsApi, roles as rolesApi } from '../../shared/api';
import { AssessmentWorkflowStepper } from '../candidates/AssessmentWorkflow';
import { invitedStageValue, PIPELINE_FUNNEL_STAGES } from '../../shared/metrics';
import { FunnelBoard } from '../../shared/ui/FunnelBoard';
import { useToast } from '../../context/ToastContext';
import { pathForPage } from '../../app/routing';
import {
  Avatar,
  formatRelativeAge,
  initialsFrom,
  RolePill,
  ScoreChip,
  VerdictPill,
} from './atoms';
import { TeachModal } from './TeachModal';
import {
  OverrideModal,
  advanceableWorkableStages,
} from './OverrideModal';
import {
  getOptimisticDecisions,
  subscribeOptimisticDecisions,
  updateOptimisticDecisions,
} from './optimisticDecisionStore';
import { RecentDecisions } from './RecentDecisions';
import AgentNeedsInputCard from '../jobs/AgentNeedsInputCard';
import { AgentDecisionCard } from '../../shared/decisions/AgentDecisionCard';
import { applicationDateContext } from '../../shared/decisions/decisionPresentation';
import {
  isApprovalBlockingStale,
  isEngineOnlyStale,
} from '../../shared/decisions/decisionStaleness';
import { hasApprovalReceiptCausalTransition } from '../../shared/decisions/approvalReceipt';
import {
  DECISION_ACTIONS,
  DEFAULT_ACTIONS,
  formatRoleFamilyReferences,
  isRejectDecisionType,
} from '../../shared/decisions/decisionActions';
import { MotionLoop, MotionStagger, PresenceSwap, Reveal } from '../../shared/motion';


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

// Only unlock an optimistic action when the response proves that no mutation
// was accepted. Network/timeouts and generic 5xx responses are outcome-unknown;
// the approval may already be durable, so those must reconcile through a fresh
// decision snapshot before another click is allowed.
const isDefinitelyUnacceptedMutation = (err) => {
  const status = Number(err?.response?.status);
  const detail = err?.response?.data?.detail;
  const detailText = typeof detail === 'string'
    ? detail
    : (detail?.message || detail?.detail || '');
  if (status === 409) return isDecisionStaleError(err);
  if (status === 503) {
    return /nothing was sent|no provider update was sent|was not queued/i.test(detailText);
  }
  return status >= 400 && status < 500;
};

const isActionableDecision = (decision) => (
  decision?.status === 'pending' || decision?.status === 'reverted_for_feedback'
);

// DECISION_ACTIONS (type-aware action set) + DEFAULT_ACTIONS now live in the
// shared module ../../shared/decisions/decisionActions so the reusable
// <AgentDecisionCard> and this queue share one action vocabulary. Imported
// above; ``handleApprove`` reads DECISION_ACTIONS to open the advance-confirm
// modal for advance_to_interview.

// Everything in the queue is pending (history lives in Monitoring → History),
// so there's no status filter — only the standing "needs re-eval" warning chip
// in the toolbar (toggles filters.status between 'pending' and 'stale').

// 'advance' and 'assessment' are categories — the backend expands them to
// their underlying decision_types (advance → advance_to_interview;
// assessment → send_assessment + resend_assessment_invite). 'reject' and
// 'skip_assessment_reject' map 1:1 to their decision_type so the Hub
// distinguishes the pre-screen reject from a post-assessment reject.
const TYPE_OPTIONS = [
  { id: '', label: 'All', hint: 'All decision types' },
  { id: 'advance', label: 'Advance', hint: 'Advance the candidate to the next stage' },
  { id: 'assessment', label: 'Send', hint: 'Send or resend an assessment invite' },
  { id: 'reject', label: 'Reject', hint: 'Reject after scoring / assessment' },
  { id: 'skip_assessment_reject', label: 'Pre-screen', hint: 'Rejected at pre-screen, before any assessment' },
];

const TYPE_SEGMENT_OPTIONS = TYPE_OPTIONS.map((option) => ({
  value: option.id,
  label: option.label,
  title: option.hint,
}));

// Client-side mirror of the backend's DECISION_TYPE_CATEGORIES (agentic
// routes): which decision_types each category chip expands to. Non-category
// ids ('reject', 'skip_assessment_reject') filter 1:1 on decision_type.
const TYPE_CATEGORY_EXPANSION = {
  advance: ['advance_to_interview'],
  assessment: ['send_assessment', 'resend_assessment_invite'],
};

// Debounced queue search. Typing updates a local input immediately (so the
// field feels responsive) but only commits `q` to the shared filters ~250ms
// after the last keystroke — each committed value recreates loadDecisions and
// fires an uncached listDecisions fetch, so without this every keystroke was a
// separate UAE→us-east4 round-trip. URL persistence still works because we
// commit through the same setFilters path. In the Assessment-stage (invited)
// view the list isn't searchable server-side, so we disable the box with a hint
// instead of firing requests that visibly do nothing.
const SearchInput = ({ filters, setFilters }) => {
  // Search is a decision-queue affordance only. Both trackers (Assessment stage
  // and Sourced) list applications the queue doesn't cover and aren't searchable
  // server-side, so the box is disabled on any tracker view.
  const invited = Boolean(filters.view);
  const [text, setText] = useState(filters.q || '');
  const committed = useRef(filters.q || '');

  // Keep the field in sync when q changes from outside typing (e.g. URL nav,
  // clearing a filter) — but not while the user is mid-type toward a value we
  // haven't committed yet.
  useEffect(() => {
    const q = filters.q || '';
    if (q !== committed.current) {
      committed.current = q;
      setText(q);
    }
  }, [filters.q]);

  useEffect(() => {
    if (text === committed.current) return undefined;
    const t = setTimeout(() => {
      committed.current = text;
      setFilters((f) => ({ ...f, q: text || null }));
    }, 250);
    return () => clearTimeout(t);
  }, [text, setFilters]);

  return (
    <span className={`rq-search${invited ? ' is-disabled' : ''}`}>
      <Search size={13} strokeWidth={2} aria-hidden="true" />
      <input
        placeholder={invited ? 'Search unavailable on this view' : 'Search candidates, IDs, reasoning…'}
        value={invited ? '' : text}
        disabled={invited}
        onChange={(e) => setText(e.target.value)}
        aria-label="Search decisions"
        title={invited ? "Search isn't available on the tracker views yet." : undefined}
      />
    </span>
  );
};

const Toolbar = ({ filters, setFilters, roles, bulkAction, staleCount, sourcedCount = 0 }) => (
  <div className="rq-toolbar">
    <div className="rq-toolbar-l">
      <span className="kicker mute" style={{ marginRight: 8 }}>ROLE</span>
      <Select
        inline
        value={filters.role_id || ''}
        onChange={(e) => setFilters((f) => ({ ...f, role_id: e.target.value || null }))}
        aria-label="Select a role to scope the view"
      >
        <option value="">All roles</option>
        {roles.map((r) => (
          <option key={r.role_id} value={r.role_id} title={r.name}>{r.name || r.short_name}</option>
        ))}
      </Select>
      {/* "Filter" label introduces the decision-type segmented set, matching
          the home-preview's second `.tlabel`. */}
      <span className="kicker mute" style={{ margin: '0 2px 0 6px' }}>FILTER</span>
      <SegmentedControl
        options={TYPE_SEGMENT_OPTIONS}
        value={filters.view ? null : (filters.type || '')}
        onChange={(type) => setFilters((f) => ({ ...f, type: type || null, view: null }))}
        ariaLabel="Filter by decision type"
        className="rq-decision-type-filter"
        density="compact"
      />
      {/* Tracker views are not decision types, so they remain a distinct group.
          They use the same shared selection primitive as the decision filters,
          which keeps typography, geometry and interaction states identical. */}
      <SegmentedControl
        options={[
          {
            value: 'invited',
            ariaLabel: 'Assessment stage',
            label: (
              <>
                <ClipboardList size={13} strokeWidth={2} aria-hidden="true" />
                <span>Assessment stage</span>
              </>
            ),
            title: 'Assessments in flight, plus completed ones awaiting your review before a decision',
          },
          {
            value: 'sourced',
            ariaLabel: sourcedCount > 0 ? `Sourced, ${sourcedCount.toLocaleString()}` : 'Sourced',
            label: (
              <>
                <UserPlus size={13} strokeWidth={2} aria-hidden="true" />
                <span>Sourced</span>
              </>
            ),
            meta: sourcedCount > 0 ? sourcedCount.toLocaleString() : null,
            title: 'Sourced prospects — added before they applied, awaiting engagement. Not scored, no decision.',
          },
        ]}
        value={filters.view || null}
        onChange={(view) => setFilters((f) => ({ ...f, view }))}
        ariaLabel="Filter by candidate tracker"
        className="rq-tracker-view-filter"
        density="compact"
        allowDeselect
      />
      {/* Everything in this queue is pending, so there's no "Pending" filter to
          offer — just a standing warning chip for the ones whose score is out
          of date, toggled to review only those. Hidden when there are none and
          nothing is being filtered. */}
      {(staleCount > 0 || filters.status === 'stale') ? (
        <button
          type="button"
          className={`rq-reeval-chip${filters.status === 'stale' ? ' on' : ''}`}
          aria-pressed={filters.status === 'stale'}
          title="Candidates whose score is out of date — older scoring model or changed inputs since they were queued. Toggle to review only these."
          onClick={() => setFilters((f) => ({
            ...f,
            status: f.status === 'stale' ? 'pending' : 'stale',
            view: null,
          }))}
        >
          <RefreshCw size={12} strokeWidth={2.2} aria-hidden="true" />
          {staleCount.toLocaleString()} scores out of date
        </button>
      ) : null}
    </div>
    <div className="rq-toolbar-r">
      {bulkAction}
      <SearchInput filters={filters} setFilters={setFilters} />
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
  const hasVisibleStage = PIPELINE_FUNNEL_STAGES.some((stage) => (
    stage.key === 'invited'
      ? invitedStageValue(counts) > 0
      : (Number(counts[stage.key]) || 0) > 0
  ));
  if (!hasVisibleStage) return null;

  // Flat strip (home-preview): stage value + label + inline decision chips per
  // cell, no cap line and no separate "awaiting your decision" grid.
  return <FunnelBoard variant="flat" stageCounts={counts} decisionsByType={decisionsByType} scopeLabel={scopeLabel} />;
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
      <span style={{ fontFamily: 'var(--font-display)', fontSize: 'var(--fs-body-lg)', fontWeight: 600, color: 'var(--ink)' }}>
        {staleOnly ? 'Scores out of date' : 'Pending'} <span style={{ color: 'var(--purple)', marginLeft: 4 }}>{pending.length}</span>
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-eyebrow)', color: 'var(--mute)', letterSpacing: '.06em' }}>
        {oldestCreatedAt ? `OLDEST ${formatRelativeAge(oldestCreatedAt)}` : ''}
      </span>
    </div>
    <MotionStagger className="rq-split-list-body" data-motion-stagger="pending-decisions">
      {loading && pending.length === 0 ? (
        <div style={{ padding: 16, fontSize: 'var(--fs-body)', color: 'var(--mute)' }}>Loading…</div>
      ) : pending.length === 0 ? (
        <div className="home-empty" style={{ margin: 6 }}>
          <Inbox size={18} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
          <div>{staleOnly ? 'No candidates need re-evaluation right now.' : 'Queue is empty. The agent is running unattended.'}</div>
        </div>
      ) : (
        pending.map((p) => {
          const appliedDateContext = applicationDateContext(p);
          return (
            // role="button" instead of a real <button> so the inline <a>
            // candidate-name link below isn't an interactive child of an
            // interactive parent (invalid HTML, breaks click + keyboard
            // semantics in some browsers / AT). Same pattern HomeEverything
            // uses for its history rows.
            // Row layout mirrors the home-preview `.qitem`: an avatar, then the
            // candidate name + score on one line, the role · age beneath, and the
            // agent's recommendation pill. The stale score-status chip + score-provenance
            // pill are kept (real, load-bearing signal the preview omits).
            <div
              key={p.id}
              role="button"
              aria-pressed={selectedId === p.id}
              tabIndex={0}
              className={`rq-split-row rq-qrow ${selectedId === p.id ? 'on' : ''} ${p.status === 'processing' || p.rescore_in_flight ? 'is-processing' : ''}`.trim()}
              onClick={() => onSelect(p.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelect(p.id);
                }
              }}
            >
              <Avatar initials={initialsFrom(p.candidate_name || `#${p.application_id}`)} size={30} />
              <div className="rq-qmeta">
                <div className="rq-qtop">
                  <a
                    href={pathForPage('candidate-report', {
                      candidateApplicationId: p.application_id,
                      fromHome: true,
                      viewRoleId: p.role_id,
                    })}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rq-qname rq-inline-link"
                    style={{ color: 'inherit', textDecoration: 'none', fontWeight: 600 }}
                    onClick={(e) => e.stopPropagation()}
                    title="Open candidate report in a new tab"
                  >
                    {p.candidate_name || `Application #${p.application_id}`}
                  </a>
                  <ScoreChip score={p.taali_score} size="sm" />
                </div>
                {/* Clean "role · time" text (preview), not a role pill + a noisy
                    score-provenance/version chip. */}
                <div className="rq-qsub">
                  {p.role_name || `Role #${p.role_id}`} · {formatRelativeAge(p.created_at)}
                  {p.applied_at ? (
                    <span title={appliedDateContext.title}>
                      · {appliedDateContext.label} {formatRelativeAge(p.applied_at)} ago
                    </span>
                  ) : null}
                </div>
                <div className="rq-qverdict">
                  <VerdictPill type={p.decision_type} />
                  {p.rescore_in_flight ? (
                    <span
                      className="rq-qstale"
                      title="Re-scoring in progress — refreshes automatically"
                    >
                      <MotionLoop kind="spin" className="inline-flex" aria-hidden="true">
                        <RefreshCw size={9} strokeWidth={2.4} />
                      </MotionLoop>{' '}re-scoring
                    </span>
                  ) : p.is_stale ? (
                    <span
                      className="rq-qstale"
                      title="Score out of date — re-evaluate before acting"
                    >
                      <RefreshCw size={9} strokeWidth={2.4} aria-hidden="true" /> score out of date
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })
      )}
    </MotionStagger>
    <div style={{
      padding: '10px 14px', borderTop: '1px solid var(--line)', fontFamily: 'var(--font-mono)',
      fontSize: 'var(--fs-eyebrow)', color: 'var(--mute)', letterSpacing: '.06em',
      display: 'flex', alignItems: 'center', gap: 6,
    }}>
      <ListChecks size={12} aria-hidden="true" />
      <span>If queue empties, agent runs unattended.</span>
    </div>
  </aside>
  );
};

// The decision detail + action bar moved to the reusable
// ../../shared/decisions/AgentDecisionCard. Re-exported here under its old name
// so existing importers (HomeShowcaseView) keep working unchanged.
export { AgentDecisionCard as DecisionDetail } from '../../shared/decisions/AgentDecisionCard';


// Invited-candidate tracker — the Home "Assessment pending" view. A split view
// (mirrors the decision queue): a selectable list of candidates with a
// sent-but-not-completed assessment on the left, their card + invite timeline
// on the right. These aren't agent decisions (those leave the queue once
// approved) — they're assessments in flight.
const InvitedPanel = ({ candidates, loading, selectedId, onSelect, roleNameById }) => {
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
        <div>No assessments in this stage. Invites you've sent — in flight or completed and awaiting your review — show up here until you decide.</div>
      </div>
    );
  }
  return (
    <aside className="rq-split-list">
      <div className="rq-split-list-head">
        <span className="kicker">
          Assessment stage
          <span style={{ color: 'var(--purple)', marginLeft: 6 }}>{candidates.length}</span>
        </span>
      </div>
      <div className="rq-split-list-body">
      {candidates.map((c) => {
        const ss = c.score_summary || {};
        const tracking = ss.invite_tracking || {};
        const roleName = c.role?.name || roleNameById?.(c.role_id) || null;
        return (
          <div
            key={c.id}
            role="button"
            aria-pressed={selectedId === c.id}
            tabIndex={0}
            className={`rq-split-row rq-invited-row ${selectedId === c.id ? 'on' : ''}`.trim()}
            onClick={() => onSelect(c.id)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(c.id); } }}
          >
            <Avatar initials={initialsFrom(c.candidate_name || c.candidate_email)} size={34} />
            <div className="rq-invited-main">
              <div className="rq-invited-name">{c.candidate_name || c.candidate_email}</div>
              <div className="rq-invited-meta">
                <RolePill roleName={roleName} roleId={c.role_id} />
                <ScoreChip score={ss.taali_score} size="sm" />
              </div>
              <div className="rq-invited-chips">
                <AssessmentWorkflowStepper status={ss.assessment_status} tracking={tracking} />
              </div>
            </div>
            <span className="rq-invited-age">
              {tracking.invite_sent_at ? formatRelativeAge(tracking.invite_sent_at) : ''}
            </span>
          </div>
        );
      })}
      </div>
    </aside>
  );
};

// Right-pane card for the selected invited candidate. An EXACT copy of the
// redesigned AgentDecisionCard (same header, deep-links, integrity flags and
// requirement bars) — only the agent-recommendation slab is swapped for the
// assessment stage tracker + invite timeline, and the decision-only parts are
// hidden. Reuses the card component so the two surfaces never drift.
const InvitedDetail = ({ candidate, roleNameById, onNavigate }) => {
  if (!candidate) {
    return (
      <section className="rq-hybrid-detail">
        <div className="home-empty" style={{ marginTop: 12 }}>Select a candidate to see their invite status.</div>
      </section>
    );
  }
  const ss = candidate.score_summary || {};
  const t = ss.invite_tracking || {};
  const roleName = candidate.role?.name || roleNameById?.(candidate.role_id) || null;
  const fmt = (ts) => { try { return new Date(ts).toLocaleString(); } catch { return ''; } };
  const timeline = [
    ['Invited', t.invite_sent_at],
    ['Delivered', t.delivered_at],
    ['Email opened', t.opened_at],
    ['Bounced', t.bounced_at],
    ['Assessment started', t.started_at],
    ['Expires', t.expires_at],
  ].filter(([, ts]) => ts);

  // Requirement bars from the candidate's CV match — same source + shape the
  // decision card's backend payload uses (criterion + match score, capped 6).
  const cvDetails = candidate.cv_match_details && typeof candidate.cv_match_details === 'object' ? candidate.cv_match_details : {};
  const reqItems = Array.isArray(cvDetails.requirements_assessment) ? cvDetails.requirements_assessment : [];
  const requirements = reqItems.slice(0, 6).map((it) => ({
    label: it.criterion_text || it.requirement || it.criterion || 'Requirement',
    score: typeof it.match_score === 'number' ? Math.round(it.match_score) : null,
  }));

  // Decision-shaped subject: gives the card its identical identity header,
  // provenance, integrity flags and requirement bars. A non-pending status
  // means no recommendation slab and no action bar render.
  const subject = {
    application_id: candidate.id,
    role_id: candidate.role_id,
    candidate_name: candidate.candidate_name,
    candidate_email: candidate.candidate_email,
    role_name: roleName,
    taali_score: ss.taali_score,
    status: 'invited',
    score_summary: { score_provenance: ss.score_provenance, integrity: ss.integrity },
    requirements,
  };

  // The stage tracker + invite timeline sit exactly where the agent's
  // recommendation slab would be on a decision card.
  const tracker = (
    <>
      <div className="aw-detail-block">
        <AssessmentWorkflowStepper status={ss.assessment_status} tracking={t} labeled />
      </div>
      <div className="rq-invite-timeline">
        <span className="kicker mute">INVITE STATUS</span>
        <ul className="rq-invite-timeline-list">
          {timeline.map(([label, ts]) => (
            <li key={label} className={label === 'Bounced' ? 'is-danger' : ''}>
              <span>{label}</span>
              <span>{fmt(ts)}</span>
            </li>
          ))}
        </ul>
        {(() => {
          const es = (t.email_status || '').toLowerCase();
          if (es === 'failed') {
            return (
              <div className="rq-invite-note is-danger">
                Invite could not be sent — resend it so the candidate receives the assessment.
              </div>
            );
          }
          if (!es) {
            return (
              <div className="rq-invite-note">
                No delivery or open events recorded for this invite yet.
              </div>
            );
          }
          return null;
        })()}
      </div>
    </>
  );

  return (
    <AgentDecisionCard
      decision={subject}
      middleSlot={tracker}
      hideDecisionParts
      onNavigate={onNavigate}
    />
  );
};

// A sourced lead's ingestion channel, phrased for the recruiter. Prospects
// added inside Taali carry source 'sourced'; Workable-originated leads carry
// 'workable'. It's the honest "how did this lead enter" signal we have.
const sourcedChannelLabel = (app) => {
  const s = String(app?.source || '').trim().toLowerCase();
  if (s === 'workable') return 'via Workable';
  return 'added manually';
};

// Sourced tracker — the Home "Sourced" view. Prospects added to a role BEFORE
// they applied (Phase 3a): no CV, never scored, never in the decision queue.
// A read-only tracker (mirrors InvitedPanel), grouped by role, so the recruiter
// can see who's been sourced and is awaiting engagement. No decision actions and
// no score — sourced leads have no verdict. Each row deep-links to the candidate
// report by application id, exactly like the pending queue rows.
const SourcedPanel = ({ candidates, loading, roleNameById }) => {
  if (loading) {
    return (
      <div className="home-empty">
        <RefreshCw size={16} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
        <div>Loading sourced candidates…</div>
      </div>
    );
  }
  if (!candidates.length) {
    return (
      <div className="home-empty">
        <Inbox size={18} aria-hidden="true" style={{ marginBottom: 6, color: 'var(--mute)' }} />
        <div>No sourced candidates. Prospects added before they apply — by you or the agent — show up here, awaiting engagement. They aren&apos;t scored and never enter the decision queue.</div>
      </div>
    );
  }
  // Group by role so the tracker reads as "who's sourced, per role". Preserve
  // the server's newest-first order within each group (the fetch sorts by
  // created_at desc); groups appear in first-seen order.
  const order = [];
  const byRole = new Map();
  for (const c of candidates) {
    const key = String(c.role_id ?? 'none');
    if (!byRole.has(key)) { byRole.set(key, []); order.push(key); }
    byRole.get(key).push(c);
  }
  return (
    <aside className="rq-split-list rq-sourced-list">
      <div className="rq-split-list-head">
        <span className="kicker">
          Sourced
          <span style={{ color: 'var(--purple)', marginLeft: 6 }}>{candidates.length}</span>
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-eyebrow)', color: 'var(--mute)', letterSpacing: '.06em' }}>
          AWAITING ENGAGEMENT
        </span>
      </div>
      <div className="rq-split-list-body">
        {order.map((key) => {
          const rows = byRole.get(key);
          const roleName = rows[0].role_name || roleNameById?.(rows[0].role_id)
            || (key === 'none' ? 'No role' : `Role #${key}`);
          return (
            <div key={key} className="rq-sourced-group">
              <div className="rq-sourced-grouphdr">
                <RolePill roleName={roleName} roleId={rows[0].role_id} />
                <span className="rq-sourced-groupcount">{rows.length}</span>
              </div>
              {rows.map((c) => (
                <div key={c.id} className="rq-split-row rq-sourced-row">
                  <Avatar initials={initialsFrom(c.candidate_name || c.candidate_email || `#${c.id}`)} size={30} />
                  <div className="rq-sourced-main">
                    <a
                      href={pathForPage('candidate-report', {
                        candidateApplicationId: c.id,
                        fromHome: true,
                        viewRoleId: c.role_id,
                      })}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rq-sourced-name rq-inline-link"
                      title="Open candidate report in a new tab"
                    >
                      {c.candidate_name || c.candidate_email || `Application #${c.id}`}
                    </a>
                    <div className="rq-sourced-sub">
                      Sourced {formatRelativeAge(c.created_at)} ago · {sourcedChannelLabel(c)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </aside>
  );
};

export const HomeNow = ({
  decisions,
  pendingOrdered,
  staleCount = 0,
  selectedId,
  setSelectedId,
  loading,
  filters,
  setFilters,
  rolesBreakdown,
  reload: reloadProp,
  decisionScopeKey = 'home:decisions:default',
  decisionRevision = 0,
  decisionRevisionScopeKey = null,
  onNavigate,
  // When the agent chat dock is present it owns the agent's questions, so the
  // feed hides its own needs-input block to avoid duplicating them.
  questionsInDock = false,
}) => {
  const { showToast } = useToast() || { showToast: () => {} };
  const [busyId, setBusyId] = useState(null);
  // Bumped every time we reload after an action, so the RecentDecisions list
  // re-fetches and the call the recruiter just made shows up there immediately
  // (it fetches RESOLVED decisions, which the hub's pending feed doesn't cover).
  const [recentRefresh, setRecentRefresh] = useState(0);
  const reload = useCallback(async (...args) => {
    const r = await reloadProp?.(...args);
    setRecentRefresh((v) => v + 1);
    return r;
  }, [reloadProp]);
  // Invited-candidate tracker ("Assessment pending" view). Fetched on demand
  // from the applications list — these are sent assessments, not decisions, so
  // they don't ride the decision queue's data flow.
  const invitedView = filters.view === 'invited';
  const [invited, setInvited] = useState([]);
  const [invitedLoading, setInvitedLoading] = useState(false);
  const [selectedInvitedId, setSelectedInvitedId] = useState(null);
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
        assessment_status: 'pending,in_progress,completed',
        role_id: filters.role_id || undefined,
        limit: 100,
        include_stage_counts: false,
        sort_by: 'pipeline_stage_updated_at',
        sort_order: 'desc',
      })
      .then((res) => {
        if (cancelled) return;
        const raw = Array.isArray(res?.data?.items) ? res.data.items : [];
        // "Assessment stage" = in-flight assessments + completed-but-not-yet-
        // decided (those sit in the 'review' stage for the recruiter to check
        // before a decision). Drop already-decided ones (advanced / rejected /
        // hired) so a completed assessment leaves the stage once it's actioned.
        const items = raw.filter((it) => {
          const stage = String(it.pipeline_stage || '').toLowerCase();
          const outcome = String(it.application_outcome || '').toLowerCase();
          return stage !== 'advanced' && outcome !== 'rejected' && outcome !== 'hired';
        });
        setInvited(items);
        // Keep the current selection if it's still in the list, else focus the
        // first row so the detail card is never empty on load.
        setSelectedInvitedId((prev) => (
          prev && items.some((i) => i.id === prev) ? prev : (items[0]?.id ?? null)
        ));
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
  const selectedInvited = invited.find((c) => c.id === selectedInvitedId) || invited[0] || null;

  // Sourced tracker ("Sourced" view). Same on-demand fetch as the invited
  // tracker, filtered to pipeline_stage=sourced — un-scored prospects that never
  // ride the decision queue. Kept entirely separate from the decision data flow.
  const sourcedView = filters.view === 'sourced';
  const [sourced, setSourced] = useState([]);
  const [sourcedLoading, setSourcedLoading] = useState(false);
  useEffect(() => {
    if (!sourcedView) return undefined;
    let cancelled = false;
    setSourcedLoading(true);
    rolesApi
      .listApplicationsGlobal({
        pipeline_stage: 'sourced',
        role_id: filters.role_id || undefined,
        limit: 100,
        include_stage_counts: false,
        sort_by: 'created_at',
        sort_order: 'desc',
      })
      .then((res) => {
        if (cancelled) return;
        const raw = Array.isArray(res?.data?.items) ? res.data.items : [];
        setSourced(raw);
      })
      .catch((err) => {
        if (cancelled) return;
        setSourced([]);
        showToast?.(apiErrorMessage(err, "Couldn't load sourced candidates."), 'error');
      })
      .finally(() => {
        if (!cancelled) setSourcedLoading(false);
      });
    return () => { cancelled = true; };
  }, [sourcedView, filters.role_id, showToast]);

  // Sourced count for the toolbar chip — read from the polled role breakdown
  // (stage_counts.sourced), scoped to the selected role. Cheap: reuses data the
  // page already fetches, no extra request just to badge the toggle.
  const sourcedCount = useMemo(() => {
    const list = Array.isArray(rolesBreakdown) ? rolesBreakdown : [];
    if (filters.role_id) {
      const role = list.find((r) => String(r.role_id) === String(filters.role_id));
      return Number(role?.stage_counts?.sourced || 0);
    }
    return list.reduce((acc, r) => acc + (Number(r?.stage_counts?.sourced) || 0), 0);
  }, [rolesBreakdown, filters.role_id]);

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
  // backend flips it to ``processing`` and hands the heavy work to a worker.
  // Reflect that state immediately, but keep the row visible and greyed so the
  // recruiter can distinguish "accepted and running" from "the click vanished".
  // Selection still advances to the next actionable row. ``acted`` bridges the
  // request-to-reload gap; fresh server data then owns the processing state.
  // Map value: the decision-list scope that initiated the action, plus the
  // first post-accept load ticket allowed to settle the optimistic overlay.
  const acted = useSyncExternalStore(
    subscribeOptimisticDecisions,
    getOptimisticDecisions,
    getOptimisticDecisions,
  );
  // Ref mirror so synchronous helpers (advanceFrom) can read the latest map
  // without waiting for the state update to flush.
  const actedRef = useRef(acted);
  useEffect(() => { actedRef.current = acted; }, [acted]);

  const markActed = useCallback((decisionsToLock) => {
    const normalized = new Map();
    (decisionsToLock || []).forEach((decisionOrId) => {
      const source = decisionOrId && typeof decisionOrId === 'object'
        ? decisionOrId
        : { id: decisionOrId };
      if (source.id == null) return;
      normalized.set(source.id, source);
    });
    // Queue-mutating API clients invalidate every cached decision-list scope;
    // this helper owns only the cross-remount optimistic acknowledgement.
    updateOptimisticDecisions((prev) => {
      const next = new Map(prev);
      normalized.forEach((source, id) => {
        if (!next.has(id)) {
          next.set(id, {
            scopeKey: decisionScopeKey,
            settleAfter: null,
            source: {
              decision_type: source.decision_type ?? null,
              resolution_note: source.resolution_note ?? null,
            },
          });
        }
      });
      return next;
    });
  }, [decisionScopeKey]);

  const clearActed = useCallback((ids) => {
    const clearing = new Set(ids);
    if (clearing.size === 0) return;
    updateOptimisticDecisions((prev) => {
      if (![...clearing].some((id) => prev.has(id))) return prev;
      const next = new Map(prev);
      clearing.forEach((id) => next.delete(id));
      return next;
    });
  }, []);

  // Stamp accepted rows with the post-accept refresh ticket. The refresh may
  // apply, lose to a newer poll, or fail; in every case a same-scope successful
  // revision at/after this ticket is the first authoritative snapshot allowed
  // to remove the overlay.
  const reconcileActedAfterReload = useCallback(async (ids, { outcomeUnknown = false } = {}) => {
    let refresh = null;
    try {
      refresh = await reload?.();
    } catch {
      // Keep the safe read-only state. The normal poll will reconcile it.
    }
    const settleAfter = Number(refresh?.ticket);
    const scopeKey = refresh?.scopeKey;
    if (Number.isFinite(settleAfter) && scopeKey) {
      const stamping = new Set(ids);
      updateOptimisticDecisions((prev) => {
        let changed = false;
        const next = new Map(prev);
        stamping.forEach((id) => {
          const entry = next.get(id);
          if (!entry || entry.scopeKey !== scopeKey) return;
          if (
            entry.settleAfter !== settleAfter
            || Boolean(entry.outcomeUnknown) !== Boolean(outcomeUnknown)
          ) {
            next.set(id, { ...entry, settleAfter, outcomeUnknown: Boolean(outcomeUnknown) });
            changed = true;
          }
        });
        return changed ? next : prev;
      });
    }
    return refresh;
  }, [reload]);

  const rawDecisionsById = useMemo(() => new Map([
    ...(pendingOrdered || []).map((decision) => [String(decision.id), decision]),
    ...(decisions || []).map((decision) => [String(decision.id), decision]),
  ]), [decisions, pendingOrdered]);

  // Drop only overlays whose own post-accept ticket has been satisfied by an
  // authoritative publication. Exact scope permits absence to settle; another
  // scope may settle only when it actually contains that decision (so a role or
  // search filter cannot infer success merely by hiding the row). Raw props
  // then own the result: processing stays grey, absence disappears, and a
  // genuine worker return to pending becomes actionable again.
  useEffect(() => {
    updateOptimisticDecisions((prev) => {
      let changed = false;
      const next = new Map(prev);
      next.forEach((entry, id) => {
        const rawDecision = rawDecisionsById.get(String(id));
        const revisionReached = entry.settleAfter != null
          && Number(decisionRevision) >= Number(entry.settleAfter);
        const authoritativeForRow = entry.scopeKey === decisionRevisionScopeKey
          || Boolean(rawDecision);
        // Keep the cross-remount/cache tombstone while the server itself still
        // says processing. It is removed only when an authoritative snapshot
        // shows completion/absence or an explicit worker return to pending.
        const serverStillProcessing = rawDecision?.status === 'processing';
        if (serverStillProcessing && !entry.processingObserved) {
          next.set(id, { ...entry, processingObserved: true });
          changed = true;
          return;
        }
        // A timeout/connection loss is not proof that the mutation failed. A
        // later GET that still says pending can race the original POST, so it
        // must not unlock the row on a timer. Pending becomes safe only after
        // this tab has first observed processing (then it proves a worker
        // returned the decision). For an outcome-unknown action, absence is
        // not proof either: the pending API is capped, so the row may simply
        // have fallen outside its first page. An explicit terminal row is
        // causal; otherwise require the observed processing transition before
        // either pending or absence can settle.
        const terminalRowObserved = Boolean(
          rawDecision
          && rawDecision.status !== 'pending'
          && rawDecision.status !== 'reverted_for_feedback'
          && rawDecision.status !== 'processing',
        );
        // Approval workers stamp a returned row with a new retry note (or an
        // intentional reclassification changes its type). Unlike an unchanged
        // pending/reverted snapshot, that durable server marker proves the
        // ambiguous mutation finished and the row is safe to act on again —
        // even when the worker was too fast for this tab to observe processing.
        const actionableRetryObserved = Boolean(
          rawDecision
          && entry.source
          && isActionableDecision(rawDecision)
          && hasApprovalReceiptCausalTransition(rawDecision, entry.source),
        );
        const outcomeIsCausal = !entry.outcomeUnknown
          || entry.processingObserved
          || terminalRowObserved
          || actionableRetryObserved;
        if (
          revisionReached
          && authoritativeForRow
          && !serverStillProcessing
          && outcomeIsCausal
        ) {
          next.delete(id);
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [acted, decisionRevision, decisionRevisionScopeKey, rawDecisionsById]);

  // Optimistic re-scores. Clicking Re-evaluate on an old-engine score enqueues
  // an async re-score and the decision STAYS in the queue until the fresh
  // score lands — so grey it immediately. This set covers the gap between the
  // click and the next fetch; after that the server's ``rescore_in_flight``
  // flag (from the live score job) takes over and the poll un-greys the card.
  const [rescoring, setRescoring] = useState(() => new Set());

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
  // Same guard for the decision-type chips: the server fetch honors
  // filters.type, but stale-while-revalidate keeps the previous filter's rows
  // rendered while the switch is in flight — so clamp the displayed rows to
  // the requested type too. Mirrors the backend's DECISION_TYPE_CATEGORIES
  // expansion ('advance' / 'assessment' are categories; the rest are 1:1).
  const inTypeScope = useCallback(
    (d) => {
      if (!filters.type) return true;
      const types = TYPE_CATEGORY_EXPANSION[filters.type] || [filters.type];
      return types.includes(d?.decision_type);
    },
    [filters.type],
  );

  // Overlays applied to the server data: approved-in-flight rows stay in the
  // sidebar as grey, read-only ``processing`` rows. This preserves visible
  // acknowledgement of the recruiter's click while preventing a second action.
  // "Needs re-eval" is a lens over the pending queue, driven by the status pill
  // (filters.status === 'stale', fetched as pending): same rows, filtered to
  // those whose score is stale (older model or changed inputs). The pill COUNT
  // comes from the server (staleCount prop) so it reflects the whole queue, not
  // the capped page — counting client-side here silently under-reports a deep
  // backlog.
  const staleOnly = filters.status === 'stale';
  // Overlay the optimistic re-score mark onto server rows (see ``rescoring``).
  const withRescoring = useCallback(
    (d) => (rescoring.has(d.id) && !d.rescore_in_flight ? { ...d, rescore_in_flight: true } : d),
    [rescoring],
  );
  const effPending = useMemo(
    () => pendingOrdered
      .filter((d) => inRoleScope(d)
        && inTypeScope(d)
        && (!staleOnly || d.is_stale))
      // Preserve the queue order so the row greys in place instead of jumping
      // out of view in a long list. Selection advances independently below.
      .map((d) => {
        const effective = withRescoring(d);
        return acted.has(d.id) ? { ...effective, status: 'processing' } : effective;
      }),
    [pendingOrdered, acted, inRoleScope, inTypeScope, staleOnly, withRescoring],
  );
  const effDecisions = useMemo(
    () => decisions
      .filter((d) => inRoleScope(d) && inTypeScope(d))
      .map((d) => (acted.has(d.id) ? { ...d, status: 'processing' } : withRescoring(d))),
    [decisions, acted, inRoleScope, inTypeScope, withRescoring],
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
    const skip = (d) => d.id === id
      || !isActionableDecision(d)
      || actedRef.current.has(d.id);
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

  const handleApprove = useCallback(async (decision) => {
    if (isApprovalBlockingStale(decision)) {
      showToast?.("This decision's inputs changed — re-evaluate before approving.", 'warning');
      return;
    }
    // ``advance_to_interview`` opens the same confirmation modal as the
    // overrides — the recruiter picks the Workable target stage there.
    const spec = DECISION_ACTIONS[decision.decision_type];
    if (spec?.primary) {
      setAlternativeFor({ decision, alternative: spec.primary });
      return;
    }
    // Optimistic + async. Grey the row as ``processing`` immediately and
    // advance selection to the next actionable card. The row stays visible as
    // acknowledgement while the worker completes the provider write.
    markActed([decision]);
    advanceFrom(decision.id);
    showToast?.(
      decision.decision_type === 'send_assessment' ? 'Sending assessment…'
        : decision.decision_type === 'resend_assessment_invite' ? 'Resending invite…'
          : (decision.decision_type === 'reject' || decision.decision_type === 'skip_assessment_reject') ? 'Rejecting…'
            : 'Approved.',
      'success',
    );
    try {
      await agentApi.approveDecision(decision.id, {}, { force: isEngineOnlyStale(decision) });
    } catch (err) {
      if (isDefinitelyUnacceptedMutation(err)) {
        // The server proved nothing was accepted, so restore/refocus the row.
        clearActed([decision.id]);
        setSelectedId(decision.id);
        if (isDecisionStaleError(err)) {
          showToast?.("This decision's inputs changed — re-evaluate to refresh it.", 'warning');
        } else {
          showToast?.(apiErrorMessage(err, "Couldn't send — returned to your queue."), 'error');
        }
        try { await reload?.(); } catch { /* best-effort failure refresh */ }
      } else {
        // Timeout/5xx can arrive after the durable commit. Keep the row locked
        // and let a causal refresh reveal processing, completion, or a return.
        showToast?.(
          apiErrorMessage(err, "Couldn't confirm the action — checking its current status."),
          'warning',
        );
        await reconcileActedAfterReload([decision.id], { outcomeUnknown: true });
      }
      return;
    }
    await reconcileActedAfterReload([decision.id]);
  }, [
    advanceFrom,
    clearActed,
    markActed,
    reconcileActedAfterReload,
    reload,
    setSelectedId,
    showToast,
  ]);

  // A4: discard a stale decision and re-run the agent on fresh inputs.
  // Engine-stale decisions instead get an async re-score and STAY in the
  // queue — mark them rescoring immediately so the row + card grey out;
  // the server's rescore_in_flight flag takes over on the next fetch.
  const handleReEvaluate = async (decision) => {
    setBusyId(decision.id);
    setRescoring((prev) => new Set(prev).add(decision.id));
    try {
      await agentApi.reEvaluateDecision(decision.id);
      showToast?.('Re-evaluating with fresh inputs…', 'success');
      await reload?.();
      // Fresh data is in: the live score job now reports rescore_in_flight
      // itself (or the decision left the queue), so drop the optimistic mark —
      // keeping it would grey the refreshed card forever.
      setRescoring((prev) => { const next = new Set(prev); next.delete(decision.id); return next; });
    } catch (err) {
      setRescoring((prev) => { const next = new Set(prev); next.delete(decision.id); return next; });
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

  const handleSnooze = useCallback(async (decision) => {
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
  }, [reload, showToast]);

  // Actionable pending decisions matching the current filter scope. Used by
  // the bulk-approve action: visible processing acknowledgements are excluded,
  // so a recruiter can never submit the same decision twice.
  // Rows mid-re-score or marked stale are excluded. Old-engine scores have a
  // deliberately bounded single-row override, but the bulk endpoint has no
  // explicit force contract and must never infer that authorization.
  const visiblePending = useMemo(
    () => effDecisions.filter(
      (d) => isActionableDecision(d)
        && !d.rescore_in_flight
        && !d.is_stale,
    ),
    [effDecisions],
  );
  // "Skip & advance" only makes sense for the assessment decisions — it skips
  // the assessment and re-queues the candidate as an advance. It's meaningless
  // (and a no-op the server would reject) for an advance or reject decision.
  // The bulk button only shows when the queue is filtered to the Send chip
  // (filters.type === 'assessment'): in a mixed "all types" view the count
  // would cover an invisible subset of the list, and the recruiter couldn't
  // tell which cards it was about to act on.
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
    // Rejects on candidates already advanced in Workable (live interview /
    // offer stage): still approvable in bulk — Taali warns, never blocks —
    // but the recruiter must see exactly who they'd be disqualifying there
    // before confirming (no silent irreversible write-backs in a batch).
    const postHandoverRejects = visiblePending
      .filter(
        (d) => (d.decision_type === 'reject' || d.decision_type === 'skip_assessment_reject')
          && d.candidate_post_handover,
      )
      .map((d) => ({
        name: d.candidate_name || `#${d.id}`,
        stage: d.candidate_workable_stage || 'a live interview stage',
      }));
    const linkedRejectFamilies = [...new Set(
      visiblePending
        .filter((decision) => isRejectDecisionType(decision.decision_type))
        .map((decision) => formatRoleFamilyReferences(decision.role_family))
        .filter(Boolean),
    )];
    setBulkStages({});
    setBulkConfirm({
      count,
      typeLabel,
      roleScope,
      sample,
      more,
      ids,
      decisions: visiblePending,
      advanceRoles,
      postHandoverRejects,
      linkedRejectFamilies,
    });
  };

  const runBulkApprove = async () => {
    if (!bulkConfirm) return;
    // Never advance with an incomplete stage map — the same gate the Confirm
    // button enforces (bulkStagesReady). Without this a caller (e.g. the Enter
    // key) could submit with stages={}, silently advancing candidates on Tali's
    // internal stage with nothing posted to Workable. Bulk actions must collect
    // their required inputs.
    if (!bulkStagesReady) return;
    const { ids, count } = bulkConfirm;
    const stages = { ...bulkStages };
    setBulkConfirm(null);
    setBulkBusy(true);
    // Optimistic: grey the whole batch immediately. Rows that fail return to
    // actionable pending styling as soon as the API identifies them.
    markActed(bulkConfirm.decisions);
    try {
      const res = await agentApi.bulkApproveDecisions(
        ids,
        null,
        Object.keys(stages).length ? stages : null,
      );
      const payload = res?.data || {};
      const failures = Array.isArray(payload.failures) ? payload.failures : [];
      const failedIds = failures.map((failure) => Number(failure.decision_id));
      const failedIdSet = new Set(failedIds);
      const acceptedIds = ids.filter((id) => !failedIdSet.has(Number(id)));
      clearActed(failedIds);
      const accepted = Number(payload.accepted || acceptedIds.length || 0);
      const failed = failures.length;
      if (failed === 0) {
        showToast?.(`Approved ${accepted} / ${count}.`, 'success');
      } else {
        showToast?.(`Approved ${accepted} / ${count} — ${failed} failed.`, 'warning');
      }
      await reconcileActedAfterReload(acceptedIds);
    } catch (err) {
      if (isDefinitelyUnacceptedMutation(err)) {
        clearActed(ids);
        showToast?.(apiErrorMessage(err, 'Bulk approve failed'), 'error');
        try { await reload?.(); } catch { /* best-effort failure refresh */ }
      } else {
        showToast?.('Could not confirm every approval — checking current status.', 'warning');
        await reconcileActedAfterReload(ids, { outcomeUnknown: true });
      }
    } finally {
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
    // Optimistic: grey the batch immediately; explicit failures are restored.
    markActed(skipAdvanceTargets);
    try {
      const res = await agentApi.bulkOverrideDecisions(ids, 'skip_assessment_advance');
      const payload = res?.data || {};
      const failures = Array.isArray(payload.failures) ? payload.failures : [];
      const failedIds = failures.map((failure) => Number(failure.decision_id));
      const failedIdSet = new Set(failedIds);
      const acceptedIds = ids.filter((id) => !failedIdSet.has(Number(id)));
      clearActed(failedIds);
      const accepted = Number(payload.accepted || acceptedIds.length || 0);
      const failed = failures.length;
      showToast?.(
        failed === 0
          ? `Moved ${accepted} / ${ids.length} to the advance queue.`
          : `Moved ${accepted} / ${ids.length} to the advance queue — ${failed} failed.`,
        failed === 0 ? 'success' : 'warning',
      );
      await reconcileActedAfterReload(acceptedIds);
    } catch (err) {
      if (isDefinitelyUnacceptedMutation(err)) {
        clearActed(ids);
        showToast?.(apiErrorMessage(err, 'Bulk skip & advance failed'), 'error');
        try { await reload?.(); } catch { /* best-effort failure refresh */ }
      } else {
        showToast?.('Could not confirm every move — checking current status.', 'warning');
        await reconcileActedAfterReload(ids, { outcomeUnknown: true });
      }
    } finally {
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
  const bulkActionEl = !invitedView && !sourcedView && filters.status === 'pending' && visiblePending.length > 0 ? (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <Button
        variant="primary"
        size="sm"
        onClick={handleBulkApprove}
        disabled={bulkBusy}
      >
        <Check size={13} strokeWidth={2} aria-hidden="true" />
        {bulkBusy ? 'Approving…' : `Approve ${visiblePending.length} visible`}
      </Button>
      {filters.type === 'assessment' && skipAdvanceTargets.length > 0 ? (
        <Button
          variant="secondary"
          size="sm"
          onClick={handleBulkSkipAdvance}
          disabled={bulkBusy}
          title="Skip the assessment and move these candidates to the advance queue (you pick the Workable stage when approving each advance)"
        >
          <ArrowRight size={13} strokeWidth={2} aria-hidden="true" />
          {bulkBusy ? 'Working…' : `Skip & advance ${skipAdvanceTargets.length} visible`}
        </Button>
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
      if (invitedView || sourcedView) return;  // trackers have no decision under focus
      if (teachFor || bulkConfirm || alternativeFor) return;  // an open modal owns the keyboard
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const tag = (e.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (e.target?.isContentEditable) return;
      if (!selected) return;
      if (!isActionableDecision(selected)) return;
      const k = e.key.toLowerCase();
      if (k === 'a') {
        e.preventDefault();
        if (!isApprovalBlockingStale(selected)) handleApprove(selected);
        return;
      }
      if (k === 't') { e.preventDefault(); setTeachFor(selected); return; }
      if (k === 's') { e.preventDefault(); handleSnooze(selected); return; }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
    // Rebind when the selected row, modal state, or scope-sensitive action
    // handlers change so a shortcut after a filter switch cannot submit using
    // the previous scope's reload/optimistic lock.
  }, [
    alternativeFor,
    bulkConfirm,
    handleApprove,
    handleSnooze,
    invitedView,
    selected,
    sourcedView,
    teachFor,
  ]);

  // Esc cancels / Enter confirms the bulk-approve modal. Enter only fires once
  // every advancing role has its stage picked (bulkStagesReady) — matching the
  // Confirm button's disabled state so the natural confirm key can't bypass the
  // required Workable stage pick. We depend on bulkStagesReady (and re-bind
  // runBulkApprove) so the handler never closes over a stale empty stage map.
  useEffect(() => {
    if (!bulkConfirm) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); setBulkConfirm(null); }
      if (e.key === 'Enter' && bulkStagesReady) { e.preventDefault(); runBulkApprove(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkConfirm, bulkStagesReady]);

  return (
    <Reveal as="section" className="home-section" delay={0.08}>
      {/* Funnel leads the column (above the queue) so the pending count always
          has its denominator — how many are already advanced / in review /
          rejected — in view, matching the hub layout. */}
      <PipelineStandingStrip rolesBreakdown={rolesBreakdown} filters={filters} />

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
        staleCount={staleCount}
        sourcedCount={sourcedCount}
      />

      {/* Open orchestrator questions across the org (or scoped to the
          toolbar's role filter when set). Hides itself when the queue
          is empty so a clean state renders nothing. Centralised here so
          recruiters answer everything from one place rather than having
          to bounce into each role page. */}
      {!questionsInDock && <AgentNeedsInputCard roleId={filters.role_id || undefined} />}

      {invitedView ? (
        (invitedLoading || invited.length === 0) ? (
          <InvitedPanel candidates={invited} loading={invitedLoading} roleNameById={roleNameById} />
        ) : (
          <div className="rq-hybrid-grid">
            <InvitedPanel
              candidates={invited}
              loading={invitedLoading}
              selectedId={selectedInvited?.id}
              onSelect={setSelectedInvitedId}
              roleNameById={roleNameById}
            />
            <div className="rq-hybrid-right">
              <PresenceSwap presenceKey={selectedInvited?.id || 'invited-empty'}>
                <InvitedDetail
                  candidate={selectedInvited}
                  roleNameById={roleNameById}
                  onNavigate={onNavigate}
                />
              </PresenceSwap>
            </div>
          </div>
        )
      ) : sourcedView ? (
        <SourcedPanel
          candidates={sourced}
          loading={sourcedLoading}
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
              <PresenceSwap presenceKey={selected?.id || 'decision-empty'}>
                <AgentDecisionCard
                  decision={selected}
                  busy={busyId === selected?.id}
                  onApprove={handleApprove}
                  onAlternative={handleAlternative}
                  onReEvaluate={handleReEvaluate}
                  onSnooze={handleSnooze}
                  onTeach={(d) => setTeachFor(d)}
                  onNavigate={onNavigate}
                />
              </PresenceSwap>
            </div>
          </div>

          {/* Minimal recent-decisions list — who, what was decided, when, and a
              link to the report. Find a candidate again after they've moved on;
              the full audit trail lives on Analytics → Decision log. */}
          <RecentDecisions roleId={filters.role_id} collapsedCount={5} refreshKey={recentRefresh} />
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
          onSubmitting={() => {
            markActed([alternativeFor.decision]);
          }}
          onRejected={() => {
            const submittedId = alternativeFor.decision.id;
            clearActed([submittedId]);
            setSelectedId(submittedId);
          }}
          onSubmitted={async () => {
            const submittedId = alternativeFor.decision.id;
            // OverrideModal calls this only after the mutation is durably
            // accepted (or a timeout status check confirms that acceptance).
            markActed([alternativeFor.decision]);
            advanceFrom(submittedId);
            showToast?.(
              `${alternativeFor.alternative.confirmLabel || 'Override'} dispatched.`,
              'success',
            );
            await reconcileActedAfterReload([submittedId]);
          }}
          onOutcomeUnknown={() => {
            const submittedId = alternativeFor.decision.id;
            markActed([alternativeFor.decision]);
            advanceFrom(submittedId);
            showToast?.(
              "Couldn't confirm the action — checking its current status.",
              'warning',
            );
            void reconcileActedAfterReload([submittedId], { outcomeUnknown: true });
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
            tabIndex={-1}
            // Move focus into the dialog on open so keyboard/screen-reader users
            // land inside it (focus otherwise stays on the "Approve N" trigger
            // behind the backdrop) — this is also what keeps stray a/t/s
            // shortcuts from reaching the decision underneath.
            ref={(el) => { if (el && !el.contains(document.activeElement)) el.focus(); }}
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
                  <p style={{ margin: 0, fontSize: 'var(--fs-body)', color: 'var(--ink-2)', maxWidth: 420, lineHeight: 1.5 }}>
                    {`${bulkConfirm.sample}${bulkConfirm.more}`}
                  </p>
                ) : null}
              </div>
              <button type="button" className="rq-tinybtn" onClick={() => setBulkConfirm(null)} aria-label="Close">
                <X size={12} strokeWidth={2.2} />
              </button>
            </div>

            <div className="rq-modal-body">
              {(bulkConfirm.linkedRejectFamilies || []).length > 0 ? (
                <div className="rq-modal-section" role="alert" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '8px 12px', borderRadius: 8, background: 'var(--amber-soft)', color: 'var(--ink-2)', fontSize: 'var(--fs-body)', fontWeight: 500, lineHeight: 1.5 }}>
                  <AlertTriangle size={14} strokeWidth={2} aria-hidden="true" style={{ marginTop: 2, flexShrink: 0 }} />
                  <span>
                    <strong>Shared-pool rejection —</strong> approving this batch rejects the ATS application across all linked roles:{' '}
                    {bulkConfirm.linkedRejectFamilies.join(' · ')}.
                  </span>
                </div>
              ) : null}
              {(bulkConfirm.postHandoverRejects || []).length > 0 ? (
                <div className="rq-modal-section" role="alert" style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '8px 12px', borderRadius: 8, background: 'var(--purple-soft)', color: 'var(--purple)', fontSize: 'var(--fs-body)', fontWeight: 500, lineHeight: 1.5 }}>
                  <AlertTriangle size={14} strokeWidth={2} aria-hidden="true" style={{ marginTop: 2, flexShrink: 0 }} />
                  <span>
                    <strong>Heads up —</strong> this batch rejects{' '}
                    {bulkConfirm.postHandoverRejects.length === 1 ? 'a candidate' : `${bulkConfirm.postHandoverRejects.length} candidates`}{' '}
                    already advanced in Workable (
                    {bulkConfirm.postHandoverRejects.slice(0, 3).map((p) => `${p.name} · ${p.stage}`).join(', ')}
                    {bulkConfirm.postHandoverRejects.length > 3 ? ` and ${bulkConfirm.postHandoverRejects.length - 3} more` : ''}
                    ). Approving disqualifies them there.
                  </span>
                </div>
              ) : null}
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
                        <div style={{ fontSize: 'var(--fs-body)', color: 'var(--ink-2)', marginBottom: 6 }}>
                          {r.role_name} · {r.count} advancing
                        </div>
                        {raw === undefined || raw === 'loading' ? (
                          <span style={{ fontSize: 'var(--fs-caption)', color: 'var(--mute)' }}>Loading stages…</span>
                        ) : raw === 'error' ? (
                          <span style={{ fontSize: 'var(--fs-caption)', color: 'var(--ink-2)', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
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
                          <span style={{ fontSize: 'var(--fs-caption)', color: 'var(--mute)' }}>
                            No advance stages in this Workable job — only Sourced / Applied. These candidates advance on Taali's internal stage; nothing posts to Workable. Add interview/offer stages to the job in Workable to move them there.
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
              <p style={{ margin: bulkConfirm.advanceRoles.length > 0 ? '12px 0 0' : 0, fontSize: 'var(--fs-body)', color: 'var(--mute)', lineHeight: 1.5 }}>
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
    </Reveal>
  );
};

export default HomeNow;
