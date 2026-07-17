// DECISION LOG — the immutable decision & audit table (Time / Actor / Role /
// Action / Subject / Outcome) with a filter control. Source:
// /agent-decisions?status=all — every agent decision and its human resolution,
// org-scoped (optionally role-scoped). Actor is derived from who resolved it
// (the current viewer → "You", a teammate → "Recruiter", otherwise "Agent").
// Outcome reads status with the override / approve / auto nuance. No fabricated
// rows.

import React, {
  useEffect, useLayoutEffect, useRef, useState,
} from 'react';
import { Filter } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { agent as agentApi } from '../../shared/api';
import { decisionCursorParams } from '../../shared/api/agentDecisionPagination';
import { Select, Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  safeNum,
  fmtRelShort,
  decisionTypeLabel,
  decisionChipClass,
} from './analyticsFormat';

const resolvedByViewer = (row, currentUserId) => (
  row.resolved_by_user_id != null
  && currentUserId != null
  && Number(row.resolved_by_user_id) === Number(currentUserId)
);

const actorOf = (row, currentUserId) => {
  if (resolvedByViewer(row, currentUserId)) return 'You';
  return row.resolved_by_user_id != null ? 'Recruiter' : 'Agent';
};

const actionLabel = (row) => {
  if (row.override_action) return `Override → ${decisionTypeLabel(row.override_action).toLowerCase()}`;
  return decisionTypeLabel(row.decision_type);
};

export const outcomeOf = (row, currentUserId) => {
  const s = String(row.status || '').toLowerCase();
  if (s === 'overridden') {
    return { text: row.override_action ? `overridden → ${decisionTypeLabel(row.override_action).toLowerCase()}` : 'overridden', tone: 'warn' };
  }
  if (s === 'approved') {
    if (resolvedByViewer(row, currentUserId)) return { text: 'approved by you', tone: 'ok' };
    return { text: row.resolved_by_user_id != null ? 'approved by recruiter' : 'auto', tone: 'ok' };
  }
  if (s === 'reverted_for_feedback') return { text: 'taught', tone: 'warn' };
  if (s === 'pending') return { text: 'pending', tone: 'ok' };
  if (s === 'processing') return { text: 'processing', tone: 'ok' };
  if (s === 'expired') return { text: 'expired', tone: 'ok' };
  if (s === 'discarded') return { text: 'discarded', tone: 'ok' };
  return { text: row.status || '—', tone: 'ok' };
};

const FILTERS = [
  { value: 'all', label: 'All actions' },
  { value: 'advance', label: 'Advances' },
  { value: 'reject', label: 'Rejects' },
  { value: 'send', label: 'Assessments sent' },
  { value: 'overridden', label: 'Overrides' },
];

const PAGE_SIZE = 100;

const filterParams = (filter) => {
  if (filter === 'overridden') return { status: 'overridden' };
  if (filter === 'advance') return { status: 'all', type: 'advance' };
  if (filter === 'reject') return { status: 'all', type: 'all_rejects' };
  if (filter === 'send') return { status: 'all', type: 'assessment' };
  return { status: 'all' };
};

export const DecisionLogTab = ({ roleId }) => {
  const { user } = useAuth();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [filter, setFilter] = useState('all');
  const [reloadKey, setReloadKey] = useState(0);
  const scopeGenerationRef = useRef(0);
  const loadEarlierInFlightRef = useRef(false);
  const scopeKey = JSON.stringify([roleId ?? null, filter, reloadKey]);

  useLayoutEffect(() => {
    scopeGenerationRef.current += 1;
    loadEarlierInFlightRef.current = false;
    setLoadingMore(false);
    return () => {
      scopeGenerationRef.current += 1;
      loadEarlierInFlightRef.current = false;
    };
  }, [scopeKey]);

  useEffect(() => {
    let cancelled = false;
    const requestGeneration = scopeGenerationRef.current;
    setLoading(true);
    setLoadError(false);
    agentApi.listDecisions({
      ...filterParams(filter),
      limit: PAGE_SIZE + 1,
      ...(roleId ? { role_id: roleId } : {}),
    })
      .then((res) => {
        if (cancelled || scopeGenerationRef.current !== requestGeneration) return;
        const page = Array.isArray(res?.data) ? res.data : [];
        setRows(page.slice(0, PAGE_SIZE));
        setHasMore(page.length > PAGE_SIZE);
      })
      .catch(() => {
        if (!cancelled && scopeGenerationRef.current === requestGeneration) {
          setRows([]);
          setHasMore(false);
          setLoadError(true);
        }
      })
      .finally(() => {
        if (!cancelled && scopeGenerationRef.current === requestGeneration) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [filter, reloadKey, roleId, scopeKey]);

  const loadEarlier = async () => {
    const cursor = decisionCursorParams(rows[rows.length - 1]);
    if (!cursor || loadEarlierInFlightRef.current) return;
    const requestGeneration = scopeGenerationRef.current;
    loadEarlierInFlightRef.current = true;
    setLoadingMore(true);
    setLoadError(false);
    try {
      const res = await agentApi.listDecisions({
        ...filterParams(filter),
        limit: PAGE_SIZE + 1,
        ...(roleId ? { role_id: roleId } : {}),
        ...cursor,
      });
      if (scopeGenerationRef.current !== requestGeneration) return;
      const page = Array.isArray(res?.data) ? res.data : [];
      const visiblePage = page.slice(0, PAGE_SIZE);
      setRows((current) => {
        const seen = new Set(current.map((row) => String(row.id)));
        return [...current, ...visiblePage.filter((row) => !seen.has(String(row.id)))];
      });
      setHasMore(page.length > PAGE_SIZE);
    } catch {
      if (scopeGenerationRef.current === requestGeneration) setLoadError(true);
    } finally {
      if (scopeGenerationRef.current === requestGeneration) {
        loadEarlierInFlightRef.current = false;
        setLoadingMore(false);
      }
    }
  };

  return (
    <div className="an-tabpanel">
      <div className="an-card">
        <div className="ch">
          <div>
            <div className="ct2">Decision &amp; audit log</div>
            <div className="cd">Every agent and recruiter action, immutable</div>
          </div>
          <span className="an-sel" style={{ gap: 6 }}>
            <Filter size={14} aria-hidden="true" style={{ color: 'var(--mute)' }} />
            <Select inline value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter decisions">
              {FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </Select>
          </span>
        </div>
        {loading ? (
          <div className="an-empty"><Spinner size={14} className="!text-current" /> Loading decisions…</div>
        ) : loadError && rows.length === 0 ? (
          <div className="an-empty" role="alert">
            Decisions could not be loaded.{' '}
            <button type="button" className="btn btn-sm" onClick={() => setReloadKey((value) => value + 1)}>Retry</button>
          </div>
        ) : rows.length === 0 ? (
          <div className="an-empty">
            {filter === 'all'
              ? 'No decisions recorded yet — every agent and recruiter action will land here.'
              : 'No decisions match this filter.'}
          </div>
        ) : (
          <>
            <div className="an-table-scroll">
              <table className="an-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Actor</th>
                  <th>Role</th>
                  <th>Action</th>
                  <th>Subject</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const outcome = outcomeOf(row, user?.id);
                  const isReject = String(row.decision_type || '').toLowerCase().includes('reject');
                  return (
                    <tr key={row.id}>
                      <td className="an-stt ok">{fmtRelShort(row.resolved_at || row.created_at)}</td>
                      <td>{actorOf(row, user?.id)}</td>
                      <td>{row.role_name || `Role #${row.role_id}`}</td>
                      <td>
                        <span className={`an-dchip ${row.override_action ? 'rej' : decisionChipClass(row.decision_type)}`}>
                          {actionLabel(row)}
                        </span>
                      </td>
                      <td>{row.candidate_name || (isReject ? '—' : `Application #${safeNum(row.application_id)}`)}</td>
                      <td><span className={`an-stt ${outcome.tone}`}>{outcome.text}</span></td>
                    </tr>
                  );
                })}
              </tbody>
              </table>
            </div>
            {loadError ? <div className="an-empty" role="alert">Earlier decisions could not be loaded. Try again.</div> : null}
            {hasMore ? (
              <div className="an-empty">
                <button type="button" className="btn btn-sm" onClick={loadEarlier} disabled={loadingMore}>
                  {loadingMore ? 'Loading…' : 'Load earlier decisions'}
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
};

export default DecisionLogTab;
