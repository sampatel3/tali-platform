import React, { useEffect, useState } from 'react';

import { Spinner, Panel } from '../../shared/ui/TaaliPrimitives';
import { analytics as analyticsApi } from '../../shared/api';

export const AnalyticsPage = ({ onNavigate, NavComponent }) => {
  const [data, setData] = useState({
    weekly_completion: [],
    total_assessments: 0,
    completed_count: 0,
    completion_rate: 0,
    top_score: null,
    avg_score: null,
    avg_time_minutes: null,
  });
  const [loading, setLoading] = useState(true);
  const maxRate = 100;

  useEffect(() => {
    let cancelled = false;
    analyticsApi.get()
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch(() => {
        if (!cancelled) setData((d) => d);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const weekly = data.weekly_completion?.length ? data.weekly_completion : [
    { week: 'Week 1', rate: 0, count: 0 },
    { week: 'Week 2', rate: 0, count: 0 },
    { week: 'Week 3', rate: 0, count: 0 },
    { week: 'Week 4', rate: 0, count: 0 },
    { week: 'Week 5', rate: 0, count: 0 },
  ];

  return (
    <div>
      <NavComponent currentPage="analytics" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <h1 className="text-3xl font-bold mb-2">Analytics</h1>
        <p className="text-sm text-[var(--taali-muted)] mb-8">Assessment performance over time</p>

        {loading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <Spinner size={24} />
            <span className="text-sm text-[var(--taali-muted)]">Loading analytics...</span>
          </div>
        ) : (
          <>
            <Panel className="p-8 mb-8">
              <h2 className="font-bold text-xl mb-6 text-[var(--taali-text)]">Completion Rate</h2>
              <div className="flex items-end gap-4 h-64">
                {weekly.map((w, i) => (
                  <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
                    <div className="font-mono text-xs mb-2 font-bold text-[var(--taali-text)]">{w.rate}%</div>
                    <div
                      className={`w-full border-2 border-[var(--taali-border)] transition-all ${i === weekly.length - 1 ? 'bg-[var(--taali-purple)]' : 'bg-[var(--taali-border-muted)]'}`}
                      style={{ height: `${(w.rate / maxRate) * 100}%` }}
                    />
                    <div className="font-mono text-xs mt-2 text-[var(--taali-muted)]">{w.week}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-6 mt-6 font-mono text-xs text-[var(--taali-text)]">
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-[var(--taali-border)] bg-[var(--taali-purple)]" />
                  <span>Your rate: {data.completion_rate ?? 0}%</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-[var(--taali-border)] bg-[var(--taali-border-muted)]" />
                  <span>Industry avg: 65%</span>
                </div>
              </div>
            </Panel>

            <div className="grid md:grid-cols-3 gap-6">
              <Panel className="p-6">
                <div className="font-mono text-sm text-[var(--taali-muted)] mb-2">Total Assessments</div>
                <div className="text-4xl font-bold text-[var(--taali-text)]">{data.total_assessments ?? 0}</div>
                <div className="font-mono text-xs text-[var(--taali-muted)] mt-1">All time</div>
              </Panel>
              <Panel className="p-6">
                <div className="font-mono text-sm text-[var(--taali-muted)] mb-2">Top Score</div>
                <div className="text-4xl font-bold text-[var(--taali-purple)]">
                  {data.top_score != null ? `${data.top_score}/10` : '—'}
                </div>
                <div className="font-mono text-xs text-[var(--taali-muted)] mt-1">Best candidate score</div>
              </Panel>
              <Panel className="p-6">
                <div className="font-mono text-sm text-[var(--taali-muted)] mb-2">Avg Time to Complete</div>
                <div className="text-4xl font-bold text-[var(--taali-text)]">
                  {data.avg_time_minutes != null ? `${data.avg_time_minutes}m` : '—'}
                </div>
                <div className="font-mono text-xs text-[var(--taali-muted)] mt-1">Completed assessments</div>
              </Panel>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
