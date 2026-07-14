import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ChevronLeft, ChevronRight, Sparkles } from 'lucide-react';
import '../../styles/16-job-pipeline.css';
import '../sourcing/sourcingPanels.css';

import { roles as rolesApi } from '../../shared/api';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { AgentLoop } from '../../shared/motion';
import { candidateReportHref } from './CandidateTriageDrawer';
import { renderJobPipelineScoreCell, formatStatusLabel } from './candidatesUiUtils';
import { ScoreProvenance } from './ScoreProvenance';

// The Candidates tab — a real cross-role list of everyone the agent is
// working: every application (candidate × role) in the org, searchable and
// filterable by role, stage, decision status and source. Backed by the
// existing org-scoped GET /applications endpoint (the same source the global
// search and Home trackers use), so rows carry a real application id and link
// straight to that candidate's standing report.

const PAGE_SIZE = 25;

// pipeline_stage values (see ApplicationResponse.pipeline_stage on the API).
const STAGE_OPTIONS = [
  { value: 'applied', label: 'Applied' },
  { value: 'invited', label: 'Invited' },
  { value: 'in_assessment', label: 'In assessment' },
  { value: 'review', label: 'Review' },
  { value: 'advanced', label: 'Advanced' },
];

// application_outcome values. Default is "open" — everyone the agent is
// actively working — so opening the tab never buries live candidates under a
// wall of rejected/hired history.
const DECISION_OPTIONS = [
  { value: 'open', label: 'Active' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'hired', label: 'Hired' },
  { value: 'withdrawn', label: 'Withdrawn' },
  { value: 'all', label: 'All decisions' },
];

const SOURCE_OPTIONS = [
  { value: '', label: 'All sources' },
  { value: 'workable', label: 'Workable' },
  { value: 'manual', label: 'Native' },
];

function formatDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch (error) {
    return '—';
  }
}

function stageLabel(stage) {
  const key = String(stage || '').toLowerCase();
  const match = STAGE_OPTIONS.find((option) => option.value === key);
  return match ? match.label : formatStatusLabel(stage) || '—';
}

function sourceLabel(application) {
  if (application?.workable_sourced) return 'Sourced';
  const raw = String(application?.source || '').toLowerCase();
  if (raw === 'workable' || application?.workable_candidate_id) return 'Workable';
  return 'Native';
}

function candidateName(application) {
  return (
    application?.candidate_name
    || application?.candidate_email
    || `Candidate #${application?.candidate_id || application?.id || '—'}`
  );
}

// The decision cell reads the same signals the pipeline uses: a real queued
// agent decision beats everything (never a score-band guess), then the
// terminal outcome, then the live stage. No fabricated verdicts.
function DecisionCell({ application }) {
  if (application?.pending_decision) {
    return (
      <span className="ai-action" title="The agent has queued a decision for your review">
        <AgentLoop kind="pulse"><Sparkles size={11} strokeWidth={2} /></AgentLoop>
        <AgentLoop kind="flow" className="ai-action-label">Needs a decision</AgentLoop>
      </span>
    );
  }
  const outcome = String(application?.application_outcome || 'open').toLowerCase();
  if (outcome === 'rejected') return <span className="stage-pill is-disqualified">Rejected</span>;
  if (outcome === 'hired') return <span className="stage-pill">Hired</span>;
  if (outcome === 'withdrawn') return <span className="stage-pill">Withdrawn</span>;
  if (String(application?.pipeline_stage || '').toLowerCase() === 'advanced') {
    return <span className="stage-pill">Advanced</span>;
  }
  return <span className="ctable-em">—</span>;
}

function scoreForRow(application) {
  const raw = application?.score_summary?.taali_score
    ?? application?.taali_score
    ?? application?.pre_screen_score
    ?? application?.cv_match_score;
  if (raw == null || !Number.isFinite(Number(raw))) {
    return { score: null, scoreClass: '' };
  }
  const score = Math.round(Number(raw));
  const scoreClass = score >= 80 ? 'hi' : score >= 60 ? 'mid' : 'lo';
  return { score, scoreClass };
}

export default function CandidatesListPage({ onNavigate, NavComponent = null }) {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [stageFilter, setStageFilter] = useState('');
  const [decisionFilter, setDecisionFilter] = useState('open');
  const [sourceFilter, setSourceFilter] = useState('');
  const [page, setPage] = useState(0);

  const [roleOptions, setRoleOptions] = useState([]);
  const loadedOnce = useRef(false);
  const requestSequence = useRef(0);

  // Role list for the filter — small cardinality, fetched once.
  useEffect(() => {
    let cancelled = false;
    rolesApi.list()
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res.data) ? res.data : (res.data?.items || []);
        setRoleOptions(list);
      })
      .catch(() => { if (!cancelled) setRoleOptions([]); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const nextQ = q.trim();
    if (nextQ === debouncedQ) return undefined;
    const timer = window.setTimeout(() => {
      setDebouncedQ(nextQ);
      setPage(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [debouncedQ, q]);

  const loadCandidates = useCallback(async () => {
    const requestId = ++requestSequence.current;
    if (loadedOnce.current) setRefreshing(true);
    else setLoading(true);

    const params = {
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      application_outcome: decisionFilter || 'open',
      include_stage_counts: false,
    };
    if (debouncedQ) params.search = debouncedQ;
    if (roleFilter) params.role_id = roleFilter;
    if (stageFilter) params.pipeline_stage = stageFilter;
    if (sourceFilter) params.source = sourceFilter;

    try {
      const res = await rolesApi.listApplicationsGlobal(params);
      if (requestId !== requestSequence.current) return;
      setRows(Array.isArray(res.data?.items) ? res.data.items : []);
      setTotal(Number(res.data?.total || 0));
      setError('');
      loadedOnce.current = true;
    } catch (loadError) {
      if (requestId !== requestSequence.current) return;
      setError('Could not load candidates. Check your connection and try again.');
    } finally {
      if (requestId === requestSequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [debouncedQ, decisionFilter, page, roleFilter, sourceFilter, stageFilter]);

  useEffect(() => { loadCandidates(); }, [loadCandidates]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (page >= pageCount) setPage(pageCount - 1);
  }, [page, pageCount]);

  const resultLabel = useMemo(() => {
    if (total === 0) return 'No candidates';
    return `${total.toLocaleString()} candidate${total === 1 ? '' : 's'}`;
  }, [total]);

  const hasFilters = Boolean(debouncedQ || roleFilter || stageFilter || sourceFilter || decisionFilter !== 'open');

  const openReport = useCallback((application) => {
    if (!application?.id) return;
    if (onNavigate) {
      onNavigate('candidate-report', { candidateApplicationId: application.id });
      return;
    }
    window.location.assign(candidateReportHref(application));
  }, [onNavigate]);

  return (
    <div className="src-shell">
      {NavComponent ? <NavComponent currentPage="candidates" onNavigate={onNavigate} /> : null}
      <AgentHeader
        breadcrumbs={[{ label: 'Candidates' }]}
        kicker="CANDIDATES · EVERY ROLE"
        title={<>Your <em>candidates</em></>}
        period={false}
        subtitle="Everyone the agent is working, across all your roles. Search and filter, then open any candidate's report to see the score, evidence and decision."
      />

      <main className="src-root">
        <section className="src-tab-panel">
          <div className="src-toolbar">
            <div className="src-filters">
              <label className="src-field">
                <span className="src-field-label">Search candidates</span>
                <input
                  className="src-input"
                  placeholder="Name, email, or role"
                  value={q}
                  onChange={(event) => setQ(event.target.value)}
                />
              </label>
              <label className="src-field">
                <span className="src-field-label">Role</span>
                <select
                  className="src-input"
                  value={roleFilter}
                  onChange={(event) => { setRoleFilter(event.target.value); setPage(0); }}
                >
                  <option value="">All roles</option>
                  {roleOptions.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.short_name || role.name || `Role #${role.id}`}
                    </option>
                  ))}
                </select>
              </label>
              <label className="src-field">
                <span className="src-field-label">Stage</span>
                <select
                  className="src-input"
                  value={stageFilter}
                  onChange={(event) => { setStageFilter(event.target.value); setPage(0); }}
                >
                  <option value="">All stages</option>
                  {STAGE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="src-field">
                <span className="src-field-label">Decision</span>
                <select
                  className="src-input"
                  value={decisionFilter}
                  onChange={(event) => { setDecisionFilter(event.target.value); setPage(0); }}
                >
                  {DECISION_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label className="src-field">
                <span className="src-field-label">Source</span>
                <select
                  className="src-input"
                  value={sourceFilter}
                  onChange={(event) => { setSourceFilter(event.target.value); setPage(0); }}
                >
                  {SOURCE_OPTIONS.map((option) => (
                    <option key={option.value || 'all'} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="src-result-count" aria-live="polite">
              {resultLabel}
              {refreshing ? <span className="src-refreshing">Updating…</span> : null}
            </div>
          </div>

          {error ? (
            <div className="src-form-error src-error-row" role="alert">
              <span>{error}</span>
              <button type="button" className="src-link" onClick={loadCandidates}>Retry</button>
            </div>
          ) : null}

          {loading ? (
            <div className="src-muted" role="status">Loading candidates…</div>
          ) : rows.length === 0 ? (
            <div className="src-empty">
              <p className="src-empty-title">
                {hasFilters ? 'No candidates match these filters' : 'No candidates yet'}
              </p>
              <p className="src-empty-body">
                {hasFilters
                  ? 'Try a broader search, or clear a filter.'
                  : 'As candidates apply or are added to your roles, the agent screens them and they appear here.'}
              </p>
            </div>
          ) : (
            <div className="ctable-wrap">
              <table className="ctable">
                <thead>
                  <tr>
                    <th>Candidate</th>
                    <th>Role</th>
                    <th>Stage</th>
                    <th>Decision</th>
                    <th>Score</th>
                    <th>Source</th>
                    <th>Applied</th>
                    <th aria-label="Open" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((application) => {
                    const { score, scoreClass } = scoreForRow(application);
                    return (
                      <tr
                        key={application.id}
                        onClick={() => openReport(application)}
                        style={{ cursor: 'pointer' }}
                      >
                        <td>
                          <div className="name">{candidateName(application)}</div>
                          <div className="sub">
                            {application?.candidate_email
                              || application?.candidate_position
                              || 'No contact captured'}
                          </div>
                        </td>
                        <td>{application?.role_name || <span className="ctable-em">—</span>}</td>
                        <td><span className="stage-pill">{stageLabel(application?.pipeline_stage)}</span></td>
                        <td><DecisionCell application={application} /></td>
                        <td>
                          {renderJobPipelineScoreCell(score, scoreClass, application?.score_status)}
                          <ScoreProvenance
                            provenance={application?.score_summary?.score_provenance}
                            density="compact"
                            className="mt-0.5"
                          />
                        </td>
                        <td>{sourceLabel(application)}</td>
                        <td className="ctable-status">
                          {formatDate(application?.applied_at || application?.created_at)}
                        </td>
                        <td>
                          <a
                            href={candidateReportHref(application)}
                            className="btn btn-ghost btn-sm"
                            onClick={(event) => {
                              event.stopPropagation();
                              event.preventDefault();
                              openReport(application);
                            }}
                          >
                            View →
                          </a>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {total > PAGE_SIZE ? (
            <nav className="src-pagination" aria-label="Candidate pages">
              <button
                type="button"
                className="src-page-btn"
                onClick={() => setPage((value) => Math.max(0, value - 1))}
                disabled={page === 0}
              >
                <ChevronLeft size={14} aria-hidden="true" />
                Previous
              </button>
              <span className="src-page-info">Page {page + 1} of {pageCount}</span>
              <button
                type="button"
                className="src-page-btn"
                onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}
                disabled={page + 1 >= pageCount}
              >
                Next
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            </nav>
          ) : null}
        </section>
      </main>
    </div>
  );
}
