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

import { analytics as analyticsApi } from '../../shared/api';
import { formatRelativeAge, FeedbackPill, TypeBadge } from './atoms';
import { pathForPage } from '../../app/routing';

const safeNumber = (v, fb = 0) => (Number.isFinite(Number(v)) ? Number(v) : fb);

const HistoryTable = ({ rows, onSelect, onNavigate }) => (
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
        Nothing matches the current filters.
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
            {row.status === 'approved' ? <span style={{ fontSize: 12, color: 'var(--green)' }}>Approved</span> : null}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)', letterSpacing: '.04em' }}>
            {formatRelativeAge(row.resolved_at || row.created_at)}
          </span>
        </div>
      ))
    )}
  </div>
);

const AnalyticsDrillIns = ({ summary }) => {
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

  return (
    <div className="home-analytics-body">
      <div>
        <div className="kicker" style={{ marginBottom: 8 }}>SCORE DISTRIBUTION · 30 DAYS</div>
        <div style={{ height: 240 }}>
          <ResponsiveContainer>
            <BarChart data={histogramData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="range" tick={{ fill: 'var(--mute)', fontSize: 12 }} />
              <YAxis tick={{ fill: 'var(--mute)', fontSize: 12 }} />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-2)',
                  border: '1px solid var(--line)',
                  borderRadius: '12px',
                  color: 'var(--ink)',
                }}
              />
              <Bar dataKey="count" fill="var(--purple)" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div>
        <div className="kicker" style={{ marginBottom: 8 }}>FUNNEL · 30 DAYS</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {funnel.map((stage) => (
            <div key={stage.label} style={{ display: 'grid', gridTemplateColumns: '110px 1fr auto', alignItems: 'center', gap: 12 }}>
              <span className="kicker">{stage.label}</span>
              <div style={{ position: 'relative', height: 10, borderRadius: 999, background: 'var(--bg-3)', overflow: 'hidden' }}>
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    left: 0,
                    width: `${Math.max(0, Math.min(100, safeNumber(stage.percentage)))}%`,
                    background: 'linear-gradient(90deg, var(--purple), color-mix(in srgb, var(--purple) 60%, var(--lime)))',
                    borderRadius: 999,
                  }}
                />
              </div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--ink-2)' }}>
                {safeNumber(stage.count).toLocaleString()}
              </span>
            </div>
          ))}
        </div>
        {summary?.narrator?.paragraph ? (
          <div style={{ marginTop: 14, fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.55, padding: 12, background: 'var(--bg)', border: '1px solid var(--line)', borderRadius: 12 }}>
            {summary.narrator.paragraph}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export const HomeEverything = ({ rows, onSelect, onNavigate }) => {
  const [sectionOpen, setSectionOpen] = useState(false);
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const [analytics, setAnalytics] = useState(null);
  const [loadingAnalytics, setLoadingAnalytics] = useState(false);

  useEffect(() => {
    if (!analyticsOpen) return undefined;
    let cancelled = false;
    setLoadingAnalytics(true);
    analyticsApi.reportingSummary({})
      .then((res) => {
        if (cancelled) return;
        setAnalytics(res?.data || null);
      })
      .catch(() => {
        if (!cancelled) setAnalytics(null);
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
            Every decision the agent has made, who reviewed it, what they did. The score histogram and funnel live in the panel below — they used to be on /reporting.
          </p>
        </div>
        <button
          type="button"
          className="home-section-toggle"
          onClick={() => setSectionOpen((v) => !v)}
          aria-expanded={sectionOpen}
        >
          <span>{sectionOpen ? 'Hide' : 'Show'} history ({rows.length})</span>
          {sectionOpen ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
        </button>
      </div>

      {sectionOpen ? (
        <>
          <HistoryTable rows={rows} onSelect={onSelect} onNavigate={onNavigate} />

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
                ? <div className="signal-empty" style={{ padding: 24, textAlign: 'center' }}>Loading analytics…</div>
                : <AnalyticsDrillIns summary={analytics} />
            ) : null}
          </div>
        </>
      ) : null}
    </section>
  );
};

export default HomeEverything;
