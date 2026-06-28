// TEACHING HISTORY — what you've taught the agent (left) + how it has
// calibrated (right). Real sources:
//   teach feed          → /agent/feedback (feedback prop, from AnalyticsPage)
//   threshold history   → /analytics/threshold-history?role_id= (role-scoped)
//   agreement trend     → /analytics/decision-trend (agreement_rate_pct)
// The threshold timeline needs a specific role; with "All roles" selected it
// prompts to pick one (proper empty state) rather than inventing a series.

import React, { useEffect, useState } from 'react';
import { Brain, Loader2 } from 'lucide-react';

import { analytics as analyticsApi } from '../../shared/api';
import { safeNum, monthShort, fmtRelAgo, fmtDay } from './analyticsFormat';

const FAILURE_LABEL = {
  rubric_mismatch: 'Rubric mismatch',
  wrong_threshold: 'Score threshold',
  missing_signal: 'Missing signal',
  over_confident: 'Over-confident',
  policy_violation: 'Policy violation',
  other: 'Other',
};
const SCOPE_LABEL = { decision: 'this decision', role: 'this role', org: 'org-wide' };

const TeachCard = ({ row }) => {
  const kind = FAILURE_LABEL[row.failure_mode] || 'Teaching';
  const scope = SCOPE_LABEL[row.scope] || row.scope;
  return (
    <div className="an-teach">
      <div className="th">
        <span className="tk">{kind}</span>
        <span className="tw">{fmtRelAgo(row.created_at)}</span>
      </div>
      <div className="qa">
        {row.role_name ? <span className="ql">{row.role_name} · {scope}</span> : <span className="ql">{scope}</span>}
        <br />
        <b>&ldquo;{row.correction_text}&rdquo;</b>
      </div>
    </div>
  );
};

// Agreement-trend bars (share of agent decisions approved without change).
const AgreementBars = ({ months }) => {
  const rows = (Array.isArray(months) ? months : []).filter(() => true);
  const withData = rows.some((m) => safeNum(m.decisions) > 0);
  if (!withData) {
    return <div className="an-empty">Agreement history appears once the agent has resolved decisions over a month or more.</div>;
  }
  const max = 100; // agreement is a 0–100 percentage
  return (
    <div className="an-bars" style={{ height: 120 }}>
      {rows.map((m, i) => {
        const has = safeNum(m.decisions) > 0;
        const v = safeNum(m.agreement_rate_pct);
        const isLast = i === rows.length - 1;
        const h = has ? Math.max(6, Math.round((v / max) * 100)) : 2;
        return (
          <div className="an-bar" key={m.month}>
            <div className={`bv${has ? '' : ' muted'}`}>{has ? `${v}%` : '—'}</div>
            <div
              className={`bk${!has ? ' empty' : isLast ? ' hl' : ''}`}
              style={{ height: `${h}%` }}
              title={`${monthShort(m.month)} · ${safeNum(m.decisions)} resolved`}
            />
            <div className="bl">{monthShort(m.month)}</div>
          </div>
        );
      })}
    </div>
  );
};

// Threshold-history timeline. Real ThresholdCalibration rows when present;
// otherwise a single current-threshold entry (has_history=false) — never
// fabricated past changes.
const ThresholdTimeline = ({ roleId, roleName }) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!roleId) { setData(null); return undefined; }
    let cancelled = false;
    setLoading(true);
    analyticsApi.thresholdHistory(roleId)
      .then((res) => { if (!cancelled) setData(res?.data || null); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [roleId]);

  if (!roleId) {
    return (
      <div className="an-card">
        <div className="ct2" style={{ marginBottom: 6 }}>Threshold history</div>
        <div className="an-empty">Pick a role above to see how its score threshold has changed.</div>
      </div>
    );
  }
  if (loading && !data) {
    return (
      <div className="an-card">
        <div className="ct2" style={{ marginBottom: 6 }}>Threshold history</div>
        <div className="an-empty"><Loader2 size={13} className="animate-spin" aria-hidden="true" /> Loading…</div>
      </div>
    );
  }
  const entries = Array.isArray(data?.entries) ? data.entries : [];
  const hasHistory = Boolean(data?.has_history);
  return (
    <div className="an-card">
      <div className="ct2" style={{ marginBottom: 11 }}>
        {data?.role_name || roleName || 'Role'} · threshold history
      </div>
      {entries.length === 0 ? (
        <div className="an-empty">No threshold recorded for this role yet.</div>
      ) : (
        entries.map((e, i) => (
          <div className="an-evoline" key={`${e.at || 'current'}-${i}`}>
            <span className="when">{e.at ? fmtDay(e.at) : 'now'}</span>
            <span className="badge2">{e.threshold != null ? Math.round(e.threshold) : '—'}</span>
            <span className="enote">{e.note}</span>
          </div>
        ))
      )}
      {!hasHistory ? (
        <div className="an-empty" style={{ paddingBottom: 0 }}>
          No calibration changes recorded yet — the agent will log each threshold change here as it learns from your decisions.
        </div>
      ) : null}
    </div>
  );
};

export const TeachingTab = ({ feedback, trend, roleId, roleName }) => {
  const rows = Array.isArray(feedback) ? feedback : [];
  return (
    <div className="an-tabpanel">
      <div className="an-grid2">
        <div>
          <div className="an-kicker">What you&rsquo;ve taught the agent{rows.length ? ` · ${rows.length} event${rows.length === 1 ? '' : 's'}` : ''}</div>
          {rows.length === 0 ? (
            <div className="an-card">
              <div className="an-empty">
                No teach events yet — once you click &ldquo;Send back &amp; teach&rdquo; on a decision, or override one with a reason, it lands here as standing feedback the agent applies to the next cohort.
              </div>
            </div>
          ) : (
            rows.map((row) => <TeachCard key={row.id} row={row} />)
          )}
        </div>
        <div>
          <div className="an-kicker">How the agent has calibrated</div>
          <ThresholdTimeline roleId={roleId} roleName={roleName} />
          <div className="an-card">
            <div className="ct2" style={{ marginBottom: 6 }}>Agreement trend</div>
            <div className="cd" style={{ marginBottom: 12 }}>Share of agent decisions you approve without change</div>
            <AgreementBars months={trend?.months} />
          </div>
          <div className="an-note">
            <Brain size={15} className="ti" aria-hidden="true" />
            <span>
              Every override and answer becomes standing feedback the agent applies to the next cohort.
              This is the loop that makes the agent yours.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TeachingTab;
