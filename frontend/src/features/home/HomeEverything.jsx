// EVERYTHING — full filterable history + analytics drill-ins.
// Replaces the retired /reporting route. The score histogram and funnel
// live behind an accordion so they don't dominate; the daily narrator
// paragraph stays one click away for users who relied on it.

import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { agent as agentApi, analytics as analyticsApi } from '../../shared/api';
import { formatRelativeAge, FeedbackPill, TypeBadge } from './atoms';
import { pathForPage } from '../../app/routing';

const safeNumber = (v, fb = 0) => (Number.isFinite(Number(v)) ? Number(v) : fb);

export const HistoryTable = ({ rows, onSelect, onNavigate }) => (
  <div className="rq-history">
    <div className="rq-history-head">
      <span>Decision</span>
      <span>Status</span>
      <span>Score</span>
      <span>Reviewer</span>
      <span>Outcome</span>
      <span>Age</span>
    </div>
    {rows.length === 0 ? (
      <div className="home-empty" style={{ borderRadius: 0, border: 0 }}>
        No actioned decisions yet — approved, overridden, and taught decisions will land here.
      </div>
    ) : (
      rows.map((row) => (
        <div
          key={row.id}
          className="rq-history-row"
          onClick={() => onSelect?.(row.id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onSelect?.(row.id);
            }
          }}
        >
          <span className="rq-history-decision">
            <TypeBadge type={row.decision_type} size="sm" />
            <span className="rq-history-decision-text">
              <div style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                <a
                  href={pathForPage('candidate-report', { candidateApplicationId: row.application_id, fromHome: true })}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rq-inline-link"
                  style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer', fontWeight: 500, textDecoration: 'none' }}
                  onClick={(e) => e.stopPropagation()}
                  title="Open candidate report in a new tab"
                >
                  {row.candidate_name || `Application #${row.application_id}`}
                </a>
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--mute)', letterSpacing: '.04em' }}>
                D-{row.id}
              </div>
            </span>
          </span>
          <span style={{ fontSize: 12, color: row.status === 'pending' ? 'var(--purple)' : 'var(--ink-2)', fontWeight: row.status === 'pending' ? 600 : 400 }}>
            {row.status}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600, color: 'var(--ink-2)' }}>
            {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : '—'}
          </span>
          <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>
            {row.resolved_by_user_id ? `User #${row.resolved_by_user_id}` : '—'}
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            {row.status === 'overridden' ? <FeedbackPill kind="override" /> : null}
            {row.human_disposition === 'taught' ? <FeedbackPill /> : null}
            {row.status === 'approved' ? <span style={{ fontSize: 12, color: 'var(--mute)' }}>Approved</span> : null}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)', letterSpacing: '.04em' }}>
            {formatRelativeAge(row.resolved_at || row.created_at)}
          </span>
        </div>
      ))
    )}
  </div>
);

const STAGE_LABELS = {
  applied: 'Applied',
  phone_screen: 'Phone screen',
  phone_interview: 'Phone interview',
  interview: 'Interview',
  technical_interview: 'Technical interview',
  final_interview: 'Final interview',
  onsite: 'Onsite',
  assessment: 'Assessment',
  offer: 'Offer',
  offer_extended: 'Offer extended',
  offer_accepted: 'Offer accepted',
  hired: 'Hired',
  unstaged: 'No Workable stage',
};

const DECISION_TYPE_LABELS = {
  advance_to_interview: 'Advance',
  reject: 'Reject',
  skip_assessment_reject: 'Pre-screen reject',
  send_assessment: 'Send assessment',
  resend_assessment_invite: 'Resend invite',
  escalate_low_confidence: 'Escalate',
};

const prettyKey = (key) => {
  const s = String(key || '').replace(/_/g, ' ');
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
};
const stageLabel = (key) => STAGE_LABELS[key] || prettyKey(key);
const typeLabel = (key) => DECISION_TYPE_LABELS[key] || prettyKey(key);
const pct = (part, whole) => (safeNumber(whole) > 0 ? Math.round((safeNumber(part) / safeNumber(whole)) * 100) : 0);

// Workable stages that count as "final interview or beyond" — used to
// compare how many candidates reached the finish line vs how many of those
// Tali actually advanced.
const FINAL_PLUS_STAGES = ['final_interview', 'offer', 'offer_extended', 'offer_accepted', 'hired'];
const sumStages = (stages, keys) => keys.reduce((acc, k) => acc + safeNumber(stages?.[k]), 0);
const sortedEntriesDesc = (obj, valueOf) =>
  Object.entries(obj || {}).sort((a, b) => valueOf(b[1]) - valueOf(a[1]));

const RoleRow = ({ role, expanded, onToggle }) => {
  const d = role.decisions || {};
  const c = role.advance_conversion || {};
  const s = role.score_stats || {};
  const advanced = safeNumber(c.advanced_total);
  const hired = safeNumber(c.hired);
  const byType = sortedEntriesDesc(d.by_type, (v) => safeNumber(v?.total));
  const stages = sortedEntriesDesc(role.workable_stages, (v) => safeNumber(v));
  const advancedStages = sortedEntriesDesc(c.by_stage, (v) => safeNumber(v));
  return (
    <>
      <div
        className="hbr-row"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }}>
          {role.role_name}
        </span>
        <span className="hbr-num">
          {safeNumber(d.approved)}<span style={{ color: 'var(--mute)' }}> / {safeNumber(d.total)}</span>
        </span>
        <span className="hbr-num">{advanced}</span>
        <span className="hbr-num">{safeNumber(c.reached_final_interview)}</span>
        <span className="hbr-num">{safeNumber(c.reached_offer)}</span>
        <span
          className="hbr-num"
          style={{ color: hired > 0 ? 'var(--purple)' : 'var(--ink-2)', fontWeight: hired > 0 ? 600 : 400 }}
        >
          {hired}
        </span>
        <span className="hbr-num">{advanced > 0 ? `${pct(hired, advanced)}%` : '—'}</span>
        <span className="hbr-num">
          {s.avg != null ? s.avg : '—'}
          {s.median != null ? <span style={{ color: 'var(--mute)' }}> · med {s.median}</span> : null}
        </span>
        <span style={{ display: 'flex', justifyContent: 'center', color: 'var(--mute)' }}>
          {expanded ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
        </span>
      </div>
      {expanded ? (
        <div className="hbr-detail">
          <div style={{ gridColumn: '1 / -1' }}>
            <div className="kicker" style={{ marginBottom: 8 }}>
              Advanced cohort ({advanced}) now sits at
            </div>
            <div className="hbr-chips">
              {advancedStages.length === 0 ? (
                <span style={{ fontSize: 12, color: 'var(--mute)' }}>No advances yet</span>
              ) : (
                advancedStages.map(([key, n]) => (
                  <span key={key} className="hbr-chip">
                    {stageLabel(key)} <b>{safeNumber(n).toLocaleString()}</b>
                  </span>
                ))
              )}
            </div>
          </div>
          <div>
            <div className="kicker" style={{ marginBottom: 8 }}>Decisions by type · approved / total</div>
            <div className="hbr-chips">
              {byType.length === 0 ? (
                <span style={{ fontSize: 12, color: 'var(--mute)' }}>None</span>
              ) : (
                byType.map(([key, v]) => (
                  <span key={key} className="hbr-chip">
                    {typeLabel(key)} <b>{safeNumber(v.approved)}/{safeNumber(v.total)}</b>
                  </span>
                ))
              )}
            </div>
          </div>
          <div>
            <div className="kicker" style={{ marginBottom: 8 }}>Current Workable stage mix</div>
            <div className="hbr-chips">
              {stages.length === 0 ? (
                <span style={{ fontSize: 12, color: 'var(--mute)' }}>No Workable data</span>
              ) : (
                stages.map(([key, n]) => (
                  <span key={key} className="hbr-chip">
                    {stageLabel(key)} <b>{safeNumber(n).toLocaleString()}</b>
                  </span>
                ))
              )}
            </div>
            {safeNumber(s.count) > 0 ? (
              <div style={{ fontSize: 11, color: 'var(--mute)', marginTop: 8 }}>
                Headline score · n={safeNumber(s.count).toLocaleString()} · min {s.min} · p25 {s.p25} · median {s.median} · p75 {s.p75} · max {s.max}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
};

export const DecisionsByRole = ({ data }) => {
  const [expandedId, setExpandedId] = useState(null);
  if (!data) return null;
  const roles = Array.isArray(data.roles) ? data.roles : [];
  const totals = data.totals || {};
  const td = totals.decisions || {};
  const tc = totals.advance_conversion || {};
  const ts = totals.score_stats || {};
  const advanced = safeNumber(tc.advanced_total);
  const totalFinalPlus = sumStages(totals.workable_stages, FINAL_PLUS_STAGES);
  const totalReachedFinal = safeNumber(tc.reached_final_interview);
  return (
    <div className="home-by-role">
      <div className="hm-acard-head" style={{ marginBottom: 12 }}>
        <div>
          <div className="hm-acard-title">By role</div>
          <div className="hm-acard-desc">Decision volume, advance→hire, and where the advanced cohort sits — per active role</div>
        </div>
      </div>
      <div className="hbr-totals">
        <div className="hbr-card">
          <div className="kicker" style={{ marginBottom: 6 }}>Decisions approved</div>
          <div className="hbr-card-value">{safeNumber(td.approved).toLocaleString()}</div>
          <div className="hbr-card-sub">of {safeNumber(td.total).toLocaleString()} made</div>
        </div>
        <div className="hbr-card">
          <div className="kicker" style={{ marginBottom: 6 }}>Advanced</div>
          <div className="hbr-card-value">{advanced.toLocaleString()}</div>
          <div className="hbr-card-sub">handed to Workable</div>
        </div>
        <div className="hbr-card">
          <div className="kicker" style={{ marginBottom: 6 }}>Reached final / offer</div>
          <div className="hbr-card-value">{safeNumber(tc.reached_final_interview)} / {safeNumber(tc.reached_offer)}</div>
          <div className="hbr-card-sub">
            {advanced > 0 ? `${pct(tc.reached_final_interview, advanced)}% / ${pct(tc.reached_offer, advanced)}% of advanced` : '—'}
          </div>
        </div>
        <div className="hbr-card">
          <div className="kicker" style={{ marginBottom: 6 }}>Hired</div>
          <div className="hbr-card-value">{safeNumber(tc.hired).toLocaleString()}</div>
          <div className="hbr-card-sub">{advanced > 0 ? `${pct(tc.hired, advanced)}% of advanced` : '—'}</div>
        </div>
        <div className="hbr-card">
          <div className="kicker" style={{ marginBottom: 6 }}>Headline score</div>
          <div className="hbr-card-value">{ts.avg != null ? ts.avg : '—'}</div>
          <div className="hbr-card-sub">
            {ts.median != null ? `median ${ts.median} · n=${safeNumber(ts.count).toLocaleString()} · all-time` : 'no scores yet'}
          </div>
        </div>
      </div>

      <div className="hbr-caption">
        Conversion columns (→&nbsp;Final / Offer / Hired) count <b>only candidates Tali advanced</b>; expand a role to
        see where that cohort sits now plus the full Workable stage mix for everyone in the role.
        {totalFinalPlus > 0 ? (
          <> Of {totalFinalPlus.toLocaleString()} candidate{totalFinalPlus === 1 ? '' : 's'} now at final interview or beyond,{' '}
            <b>{totalReachedFinal.toLocaleString()}</b> {totalReachedFinal === 1 ? 'was' : 'were'} advanced by Tali.</>
        ) : null}
      </div>

      {roles.length === 0 ? (
        <div className="home-empty">No decisions recorded yet.</div>
      ) : (
        <div className="hbr-table">
          <div className="hbr-scroll">
            <div className="hbr-head">
              <span>Role</span>
              <span>Appr / total</span>
              <span>Advanced</span>
              <span>→ Final</span>
              <span>→ Offer</span>
              <span>Hired</span>
              <span>Hire %</span>
              <span>Score avg</span>
              <span />
            </div>
            {roles.map((role) => (
              <RoleRow
                key={role.role_id}
                role={role}
                expanded={expandedId === role.role_id}
                onToggle={() => setExpandedId((cur) => (cur === role.role_id ? null : role.role_id))}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// Single funnel conversion row — filled .conv-style bar. The bar fills to the
// stage's share of applied; the right label reads the step-to-step conversion
// ("18% of scored") so the agent's gating is legible.
const FunnelRow = ({ stage, prev, isLast }) => {
  const ofApplied = Math.max(0, Math.min(100, safeNumber(stage.percentage)));
  const prevCount = prev ? safeNumber(prev.count) : 0;
  const stepPct = !prev
    ? '100%'
    : isLast
      // Terminal stage is a hand-off, not a funnel step — advancement isn't
      // strictly downstream of completion, so a step-% here can read >100%.
      // Label it as the hand-off instead (matches preview).
      ? 'to recruiter'
      : prevCount > 0
        ? `${Math.round((safeNumber(stage.count) / prevCount) * 100)}% of ${String(prev.label).toLowerCase()}`
        : '—';
  return (
    <div className="hm-convrow">
      <span className="hm-conv-l">{stage.label}</span>
      <span className="hm-conv-track">
        <span
          className="hm-conv-fill"
          style={{ width: `${Math.max(ofApplied, stage.count > 0 ? 7 : 0)}%` }}
        >
          {safeNumber(stage.count).toLocaleString()}
        </span>
      </span>
      <span className="hm-conv-pct">{stepPct}</span>
    </div>
  );
};

export const AnalyticsDrillIns = ({ summary, breakdown }) => {
  const histogramData = useMemo(() => {
    const buckets = summary?.score_buckets;
    return Array.isArray(buckets) && buckets.length
      ? buckets
      : [
        { range: '0-20', count: 0 },
        { range: '20-40', count: 0 },
        { range: '40-60', count: 0 },
        { range: '60-80', count: 0 },
        { range: '80-100', count: 0 },
      ];
  }, [summary]);

  const funnel = useMemo(() => (
    Array.isArray(summary?.funnel) && summary.funnel.length
      ? summary.funnel
      : [
        { label: 'APPLIED', count: 0, percentage: 0 },
        { label: 'INVITED', count: 0, percentage: 0 },
        { label: 'DONE', count: 0, percentage: 0 },
        { label: 'REVIEW', count: 0, percentage: 0 },
        { label: 'HIRED', count: 0, percentage: 0 },
      ]
  ), [summary]);

  // Advance → hire quality, from the by-role breakdown totals (real fields):
  // of the candidates the agent advanced, how many were hired. The preview's
  // "Up from 31% last month" delta has NO backing series, so it's omitted.
  const conv = breakdown?.totals?.advance_conversion || {};
  const advancedTotal = safeNumber(conv.advanced_total);
  const hiredTotal = safeNumber(conv.hired);
  const advanceHirePct = advancedTotal > 0 ? Math.round((hiredTotal / advancedTotal) * 100) : null;

  return (
    <>
    {/* Funnel conversion — full-width card (preview .card2 over .conv). */}
    <div className="hm-acard">
      <div className="hm-acard-head">
        <div>
          <div className="hm-acard-title">Funnel conversion · all roles</div>
          <div className="hm-acard-desc">Where candidates move, and where the agent gates them</div>
        </div>
      </div>
      <div className="hm-conv">
        {funnel.map((stage, i) => (
          <FunnelRow
            key={stage.label}
            stage={stage}
            prev={i > 0 ? funnel[i - 1] : null}
            isLast={i === funnel.length - 1}
          />
        ))}
      </div>
      {summary?.narrator?.paragraph ? (
        <div className="hm-narrator">{summary.narrator.paragraph}</div>
      ) : null}
    </div>

    {/* Advance → hire bigstat + score distribution (preview .grid2). */}
    <div className="hm-agrid2">
      <div className="hm-acard">
        <div className="hm-acard-head"><div className="hm-acard-title">Advance → hire quality</div></div>
        <div className="hm-bigstat">
          <div className="hm-bigstat-n">{advanceHirePct != null ? `${advanceHirePct}%` : '—'}</div>
          <div className="hm-bigstat-sub">
            {advancedTotal > 0
              ? <><b style={{ color: 'var(--ink-2)', fontWeight: 600 }}>{hiredTotal.toLocaleString()}</b> of the {advancedTotal.toLocaleString()} candidate{advancedTotal === 1 ? '' : 's'} the agent advanced {hiredTotal === 1 ? 'was' : 'were'} hired.</>
              : 'No advanced candidates in this window yet — advance→hire appears once the agent hands candidates to the recruiter.'}
          </div>
        </div>
      </div>
      <div className="hm-acard">
        <div className="hm-acard-head"><div className="hm-acard-title">Score distribution</div></div>
        <div style={{ height: 180 }}>
          <ResponsiveContainer>
            <BarChart data={histogramData} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
              <XAxis dataKey="range" tick={{ fill: 'var(--mute)', fontSize: 11 }} tickLine={false} />
              <YAxis tick={{ fill: 'var(--mute)', fontSize: 11 }} tickLine={false} axisLine={false} allowDecimals={false} />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-2)',
                  border: '1px solid var(--line)',
                  borderRadius: '12px',
                  color: 'var(--ink)',
                }}
              />
              <Bar dataKey="count" fill="var(--purple)" radius={[6, 6, 0, 0]} maxBarSize={48} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>

    {/* By role — the full real decisions/outcomes table. */}
    <div className="hm-acard">
      <DecisionsByRole data={breakdown} />
    </div>
    </>
  );
};

export const HomeEverything = ({ onSelect, onNavigate }) => {
  const [sectionOpen, setSectionOpen] = useState(false);
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const [analytics, setAnalytics] = useState(null);
  const [breakdown, setBreakdown] = useState(null);
  const [loadingAnalytics, setLoadingAnalytics] = useState(false);
  // History is the inverse of the live queue: only decisions that have
  // already been actioned (approved / overridden / taught / discarded /
  // expired). Pending/processing rows live in the review queue above, so
  // they're deliberately excluded here — history is "things you can't see
  // anymore". Self-fetched (status=resolved) rather than reusing the
  // queue's pending list.
  const [historyRows, setHistoryRows] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoadingHistory(true);
    agentApi.listDecisions({ status: 'resolved', limit: 100 })
      .then((res) => {
        if (!cancelled) setHistoryRows(Array.isArray(res?.data) ? res.data : []);
      })
      .catch(() => {
        if (!cancelled) setHistoryRows([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!analyticsOpen) return undefined;
    let cancelled = false;
    setLoadingAnalytics(true);
    Promise.all([
      analyticsApi.reportingSummary({}),
      analyticsApi.decisionsBreakdown({}),
    ])
      .then(([summaryRes, breakdownRes]) => {
        if (cancelled) return;
        setAnalytics(summaryRes?.data || null);
        setBreakdown(breakdownRes?.data || null);
      })
      .catch(() => {
        if (!cancelled) {
          setAnalytics(null);
          setBreakdown(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingAnalytics(false);
      });
    return () => { cancelled = true; };
  }, [analyticsOpen]);

  return (
    <section className="home-section">
      <div className="home-section-head">
        <div>
          <span className="kicker">EVERYTHING · FULL TRACKING</span>
          <h3 className="home-section-title">History &amp; analytics<em>.</em></h3>
          <p className="home-section-sub">
            Decisions the agent made that have already been actioned — approved, overridden, or taught by you (and auto-applied ones). Anything still awaiting you lives in the review queue above, not here. The score histogram and funnel live in the panel below.
          </p>
        </div>
        <button
          type="button"
          className="home-section-toggle"
          onClick={() => setSectionOpen((v) => !v)}
          aria-expanded={sectionOpen}
        >
          <span>{sectionOpen ? 'Hide' : 'Show'} history ({historyRows.length})</span>
          {sectionOpen ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
        </button>
      </div>

      {sectionOpen ? (
        <>
          {loadingHistory
            ? <div className="home-empty">Loading history…</div>
            : <HistoryTable rows={historyRows} onSelect={onSelect} onNavigate={onNavigate} />}

          <div className="home-analytics-accordion">
            <button
              type="button"
              className="home-analytics-toggle"
              onClick={() => setAnalyticsOpen((v) => !v)}
              aria-expanded={analyticsOpen}
            >
              <span>Score distribution &amp; funnel</span>
              {analyticsOpen ? <ChevronUp size={16} aria-hidden="true" /> : <ChevronDown size={16} aria-hidden="true" />}
            </button>
            {analyticsOpen ? (
              loadingAnalytics
                ? <div className="home-empty">Loading analytics…</div>
                : <AnalyticsDrillIns summary={analytics} breakdown={breakdown} />
            ) : null}
          </div>
        </>
      ) : null}
    </section>
  );
};

export default HomeEverything;
