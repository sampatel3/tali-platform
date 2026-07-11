import React, { useEffect, useState } from 'react';

import { analytics as analyticsApi } from '../../shared/api';
import {
  Card,
  EmptyState,
  PageContainer,
  PageHeader,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';

const fmtDays = (n) => (n == null ? '—' : `${n} day${n === 1 ? '' : 's'}`);

const FunnelBar = ({ stage, max }) => {
  const pct = max > 0 ? Math.round((stage.count / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="w-40 shrink-0 truncate text-sm text-[var(--taali-text)]">{stage.name}</div>
      <div className="relative h-7 flex-1 overflow-hidden rounded-[var(--taali-radius-control)] bg-[var(--taali-surface-subtle)]">
        <div
          className="h-full rounded-[var(--taali-radius-control)] bg-[var(--taali-purple)] transition-all"
          style={{ width: `${Math.max(pct, stage.count > 0 ? 6 : 0)}%` }}
        />
      </div>
      <div className="w-12 shrink-0 text-right text-sm font-semibold text-[var(--taali-text)]">{stage.count}</div>
    </div>
  );
};

const Stat = ({ label, value }) => (
  <Card className="px-4 py-3">
    <div className="text-xs uppercase tracking-wide text-[var(--taali-muted)]">{label}</div>
    <div className="mt-1 text-xl font-semibold text-[var(--taali-text)]">{value}</div>
  </Card>
);

export const PipelineAnalyticsPage = ({ onNavigate, NavComponent }) => {
  const [funnel, setFunnel] = useState(null);
  const [ttf, setTtf] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([analyticsApi.pipelineFunnel(), analyticsApi.timeToFill()])
      .then(([f, t]) => { if (!cancelled) { setFunnel(f); setTtf(t); setLoading(false); } })
      .catch(() => { if (!cancelled) { setError('Failed to load analytics.'); setLoading(false); } });
    return () => { cancelled = true; };
  }, []);

  const maxCount = funnel?.stages?.reduce((m, s) => Math.max(m, s.count), 0) || 0;
  const overall = ttf?.overall;

  return (
    <>
      {NavComponent ? <NavComponent currentPage="analytics" onNavigate={onNavigate} /> : null}
      <PageContainer>
        <PageHeader title="Pipeline analytics" subtitle="Live funnel and time-to-fill across your roles." />

        {loading ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : error ? (
          <EmptyState title="Analytics" description={error} />
        ) : (
          <div className="space-y-6">
            <section>
              <h2 className="mb-3 text-sm font-semibold text-[var(--taali-text)]">
                Pipeline funnel <span className="text-[var(--taali-muted)]">({funnel?.total || 0} in pipeline)</span>
              </h2>
              {(funnel?.stages || []).length === 0 ? (
                <EmptyState title="No applications yet" description="The funnel fills as candidates enter the pipeline." className="py-8" />
              ) : (
                <Card className="space-y-2.5 px-4 py-4">
                  {funnel.stages.map((s) => <FunnelBar key={s.slug} stage={s} max={maxCount} />)}
                </Card>
              )}
              {funnel?.outcomes && Object.keys(funnel.outcomes).length ? (
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--taali-muted)]">
                  {Object.entries(funnel.outcomes).map(([k, v]) => (
                    <span key={k} className="rounded-full bg-[var(--taali-surface-subtle)] px-2.5 py-1">
                      {k}: <strong className="text-[var(--taali-text)]">{v}</strong>
                    </span>
                  ))}
                </div>
              ) : null}
            </section>

            <section>
              <h2 className="mb-3 text-sm font-semibold text-[var(--taali-text)]">Time to fill</h2>
              {!overall || overall.count === 0 ? (
                <EmptyState title="No accepted offers yet" description="Time-to-fill appears once offers are accepted." className="py-8" />
              ) : (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Stat label="Hires" value={overall.count} />
                  <Stat label="Median" value={fmtDays(overall.median)} />
                  <Stat label="Average" value={fmtDays(overall.avg)} />
                  <Stat label="Range" value={`${fmtDays(overall.min)}–${fmtDays(overall.max)}`} />
                </div>
              )}
            </section>
          </div>
        )}
      </PageContainer>
    </>
  );
};

export default PipelineAnalyticsPage;
