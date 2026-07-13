// DECISION LOG — the immutable decision & audit table (Time / Actor / Role /
// Action / Subject / Outcome) with a filter control. Source:
// /agent-decisions?status=all — every agent decision and its human resolution,
// org-scoped (optionally role-scoped). Actor is derived from who resolved it
// (recruiter → "You", otherwise "Agent"). Outcome reads status with the
// override / approve / auto nuance. No fabricated rows.

import React, { useEffect, useMemo, useState } from 'react';
import { Filter } from 'lucide-react';

import { agent as agentApi } from '../../shared/api';
import { Select, Spinner } from '../../shared/ui/TaaliPrimitives';
import {
  safeNum,
  fmtRelShort,
  decisionTypeLabel,
  decisionChipClass,
} from './analyticsFormat';

const actorOf = (row) => (row.resolved_by_user_id != null ? 'You' : 'Agent');

const actionLabel = (row) => {
  if (row.override_action) return `Override → ${decisionTypeLabel(row.override_action).toLowerCase()}`;
  return decisionTypeLabel(row.decision_type);
};

export const outcomeOf = (row) => {
  const s = String(row.status || '').toLowerCase();
  if (s === 'overridden') {
    return { text: row.override_action ? `overridden → ${decisionTypeLabel(row.override_action).toLowerCase()}` : 'overridden', tone: 'warn' };
  }
  if (s === 'approved') {
    return { text: row.resolved_by_user_id != null ? 'approved by you' : 'auto', tone: 'ok' };
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

const matchesFilter = (row, filter) => {
  if (filter === 'all') return true;
  if (filter === 'overridden') return String(row.status || '').toLowerCase() === 'overridden';
  const t = String(row.decision_type || '').toLowerCase();
  if (filter === 'advance') return t.includes('advance');
  if (filter === 'reject') return t.includes('reject');
  if (filter === 'send') return t.includes('send') || t.includes('invite');
  return true;
};

export const DecisionLogTab = ({ roleId }) => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    agentApi.listDecisions({ status: 'all', limit: 100, ...(roleId ? { role_id: roleId } : {}) })
      .then((res) => { if (!cancelled) setRows(Array.isArray(res?.data) ? res.data : []); })
      .catch(() => { if (!cancelled) setRows([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [roleId]);

  const filtered = useMemo(() => rows.filter((r) => matchesFilter(r, filter)), [rows, filter]);

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
        ) : filtered.length === 0 ? (
          <div className="an-empty">
            {rows.length === 0
              ? 'No decisions recorded yet — every agent and recruiter action will land here.'
              : 'No decisions match this filter.'}
          </div>
        ) : (
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
                {filtered.map((row) => {
                  const outcome = outcomeOf(row);
                  const isReject = String(row.decision_type || '').toLowerCase().includes('reject');
                  return (
                    <tr key={row.id}>
                      <td className="an-stt ok">{fmtRelShort(row.resolved_at || row.created_at)}</td>
                      <td>{actorOf(row)}</td>
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
        )}
      </div>
    </div>
  );
};

export default DecisionLogTab;
