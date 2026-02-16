import React from 'react';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts';

import { Button, Card, Panel } from '../../shared/ui/TaaliPrimitives';
import { AssessmentBrandGlyph } from './AssessmentBrandGlyph';

const formatDuration = (seconds) => {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
};

export const DemoAssessmentSummary = ({
  assessmentName,
  profile,
  summary,
  onRestart,
  onJoinTaali,
}) => {
  const comparisonCategories = summary?.comparison?.categories || [];
  const radarData = (comparisonCategories.length > 0 ? comparisonCategories : (summary?.categories || [])).map((entry) => {
    const candidateLevel = Number(
      entry?.candidateLevel
      ?? entry?.level
      ?? (Number(entry?.candidateScore ?? 0) / 20),
    ) || 0;
    const benchmarkLevel = Number(
      entry?.benchmarkLevel
      ?? (Number(entry?.benchmarkScore ?? 0) / 20)
      ?? candidateLevel,
    ) || 0;
    return {
      dimension: entry?.label || entry?.key || 'Category',
      candidateLevel,
      benchmarkLevel,
      fullMark: 5,
    };
  });

  return (
    <div className="min-h-screen bg-[var(--taali-bg)] text-[var(--taali-text)]">
      <nav className="border-b-2 border-black bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-6 py-4">
          <div className="flex items-center gap-3">
            <AssessmentBrandGlyph />
            <span className="text-lg font-bold tracking-tight">TAALI Demo Results</span>
          </div>
          <Button type="button" variant="secondary" size="sm" onClick={onRestart}>
            Try another demo
          </Button>
        </div>
      </nav>

      <div className="mx-auto max-w-6xl px-6 py-10">
        <Panel className="p-6">
          <div className="mb-2 inline-flex border-2 border-black bg-[var(--taali-purple)] px-3 py-1 font-mono text-xs font-bold text-white">
            TAALI PROFILE
          </div>
          <h1 className="text-3xl font-bold">
            {profile?.fullName ? `${profile.fullName}'s` : 'Your'} TAALI profile
          </h1>
          <p className="mt-2 font-mono text-sm text-[var(--taali-muted)]">
            Assessment: {assessmentName || 'Demo task'}
          </p>
          <p className="mt-3 font-mono text-xs text-[var(--taali-muted)]">
            Comparison against successful-candidate average
          </p>

          <div className="mt-6 grid gap-4 lg:grid-cols-[1.45fr_1fr]">
            <Card className="h-[360px] p-3">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={radarData} outerRadius="72%">
                  <PolarGrid stroke="rgba(157, 0, 255, 0.22)" />
                  <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fontFamily: 'var(--taali-font)' }} />
                  <PolarRadiusAxis domain={[0, 5]} tick={false} axisLine={false} />
                  <Radar
                    name="Your TAALI profile"
                    dataKey="candidateLevel"
                    stroke="#9D00FF"
                    fill="#9D00FF"
                    fillOpacity={0.18}
                  />
                  <Radar
                    name={summary?.comparison?.benchmarkLabel || 'Successful-candidate average'}
                    dataKey="benchmarkLevel"
                    stroke="#1f2937"
                    fill="#1f2937"
                    fillOpacity={0.08}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </Card>

            <Card className="p-4">
              <h3 className="text-lg font-bold">Compared with successful candidates</h3>
              <div className="mt-3 grid gap-2 font-mono text-sm">
                <div>
                  <span className="text-[var(--taali-muted)]">You:</span>{' '}
                  <span className="font-bold">{summary?.comparison?.candidateScore ?? 0}/100</span>
                </div>
                <div>
                  <span className="text-[var(--taali-muted)]">Average:</span>{' '}
                  <span className="font-bold">{summary?.comparison?.benchmarkScore ?? 0}/100</span>
                </div>
                <div>
                  <span className="text-[var(--taali-muted)]">Delta:</span>{' '}
                  <span className={`font-bold ${(summary?.comparison?.deltaScore || 0) >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                    {(summary?.comparison?.deltaScore || 0) >= 0 ? '+' : ''}
                    {summary?.comparison?.deltaScore || 0}
                  </span>
                </div>
              </div>
            </Card>
          </div>

          <Card className="mt-5 grid gap-2 p-4 md:grid-cols-4">
            <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">AI prompts:</span> {summary?.meta?.promptCount ?? 0}</div>
            <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">Code runs:</span> {summary?.meta?.runCount ?? 0}</div>
            <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">Saves:</span> {summary?.meta?.saveCount ?? 0}</div>
            <div className="font-mono text-sm"><span className="text-[var(--taali-muted)]">Session time:</span> {formatDuration(summary?.meta?.timeSpentSeconds)}</div>
          </Card>

          <div className="mt-6 flex flex-wrap gap-3">
            <Button type="button" variant="primary" size="lg" onClick={onJoinTaali}>
              Join TAALI
            </Button>
            <Button type="button" variant="secondary" size="lg" onClick={onRestart}>
              Try another demo
            </Button>
          </div>
        </Panel>
      </div>
    </div>
  );
};
