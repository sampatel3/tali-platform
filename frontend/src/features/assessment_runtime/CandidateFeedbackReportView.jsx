import React, { useMemo } from 'react';
import { Download, ExternalLink } from 'lucide-react';

import { BrandLabel, Logo } from '../../shared/ui/Branding';
import { Badge, Button, Panel, cx } from '../../shared/ui/TaaliPrimitives';
import { dimensionOrder } from '../../scoring/scoringDimensions';

const clampPercent = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, number));
};

export const CandidateFeedbackReportView = ({
  payload,
  onLinkedInShare = null,
  onDownloadPdf = null,
  downloadingPdf = false,
  embedded = false,
}) => {
  const feedback = payload?.feedback || {};
  const dimensions = useMemo(() => {
    const raw = Array.isArray(feedback?.dimensions) ? feedback.dimensions : [];
    const ordered = [];
    const byId = new Map(raw.map((item) => [item.id, item]));
    dimensionOrder.forEach((id) => {
      if (byId.has(id)) ordered.push(byId.get(id));
    });
    raw.forEach((item) => {
      if (!dimensionOrder.includes(item.id)) ordered.push(item);
    });
    return ordered;
  }, [feedback]);

  const strongest = feedback?.strongest_moment || {};
  const improvements = Array.isArray(feedback?.improvements) ? feedback.improvements : [];
  const strengths = Array.isArray(feedback?.strengths) ? feedback.strengths : [];
  const style = feedback?.style || {};
  const benchmark = feedback?.benchmark || {};
  const benchmarkLabel = feedback?.overall_percentile_label
    || (benchmark?.available ? null : 'Benchmark coming soon');
  const handleLinkedInShare = onLinkedInShare || (() => {});
  const handleDownloadPdf = onDownloadPdf || (() => {});

  const isDownloadDisabled = Boolean(onDownloadPdf) && downloadingPdf;

  return (
    <div className={cx(embedded ? 'bg-[var(--taali-bg)]' : 'min-h-screen bg-[var(--taali-bg)]')}>
      <div className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface-elevated)]">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-4">
          <div className="flex items-center gap-3">
            <Logo onClick={() => {}} />
            <span className="font-mono text-xs text-[var(--taali-muted)]">Candidate feedback</span>
          </div>
          <Badge variant="muted" className="font-mono text-[11px]">
            {payload?.organization_name || feedback?.organization_name || 'Company'}
          </Badge>
        </div>
      </div>

      <div className="mx-auto max-w-5xl space-y-5 px-4 py-8">
        <Panel className="bg-[var(--taali-surface-elevated)] p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <BrandLabel className="mb-2" toneClassName="text-[var(--taali-purple)]">TAALI Feedback</BrandLabel>
              <h1 className="text-3xl font-bold text-[var(--taali-text)]">Your AI Collaboration Profile</h1>
              <p className="mt-1 text-sm text-[var(--taali-muted)]">
                {feedback?.task_name || payload?.task_name || 'Assessment'}
                {feedback?.role_name ? ` · ${feedback.role_name}` : ''}
              </p>
            </div>
            <div className="flex gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={handleLinkedInShare}>
                <ExternalLink size={14} />
                Share on LinkedIn
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleDownloadPdf} disabled={isDownloadDisabled}>
                <Download size={14} />
                {downloadingPdf ? 'Preparing PDF...' : 'Download PDF'}
              </Button>
            </div>
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-[180px_minmax(0,1fr)]">
            <div className="border border-[var(--taali-border)] bg-[var(--taali-purple-soft)] p-4">
              <div className="mb-1 text-xs font-mono text-[var(--taali-muted)]">Overall score</div>
              <div className="text-4xl font-bold text-[var(--taali-purple)]">
                {feedback?.overall_score ?? 0}
                <span className="text-base text-[var(--taali-muted)]">/10</span>
              </div>
              {benchmarkLabel ? (
                <div className="mt-2 text-xs font-mono text-[var(--taali-muted)]">{benchmarkLabel}</div>
              ) : null}
            </div>

            <div className="space-y-3">
              {dimensions.map((item) => (
                <div key={item.id} className="grid grid-cols-[180px_minmax(0,1fr)_90px] items-center gap-2">
                  <div className="text-sm text-[var(--taali-text)]">{item.label}</div>
                  <div className="overflow-hidden bg-[var(--taali-border-muted)]">
                    <div
                      className="h-2 bg-[var(--taali-purple)]"
                      style={{ width: `${clampPercent(Number(item.score || 0) * 10)}%` }}
                    />
                  </div>
                  <div className="text-right font-mono text-xs text-[var(--taali-muted)]">
                    {item.score}/10{item.percentile_label ? ` · ${item.percentile_label}` : ''}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel className="bg-[var(--taali-surface-elevated)] p-6">
          <h2 className="mb-2 text-lg font-bold text-[var(--taali-text)]">Your strongest moment</h2>
          <div className="mb-2 font-mono text-xs text-[var(--taali-muted)]">
            Minute {strongest?.minute ?? 0}
          </div>
          <div className="border border-[var(--taali-border)] bg-[var(--taali-surface-subtle)] p-3 text-sm whitespace-pre-wrap">
            {strongest?.prompt || 'Transcript unavailable for this assessment.'}
          </div>
          {strongest?.reason ? (
            <p className="mt-3 text-sm text-[var(--taali-text)]">{strongest.reason}</p>
          ) : null}
        </Panel>

        <div className="grid gap-5 md:grid-cols-2">
          <Panel className="bg-[var(--taali-surface-elevated)] p-5">
            <h3 className="mb-3 text-base font-bold text-[var(--taali-text)]">Improvement opportunities</h3>
            {improvements.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">No targeted improvements available yet.</p>
            ) : (
              <div className="space-y-3">
                {improvements.slice(0, 3).map((item) => (
                  <div key={item.dimension_id} className="border border-[var(--taali-border)] p-3">
                    <div className="text-sm font-medium text-[var(--taali-text)]">
                      {item.dimension} ({item.score}/10)
                    </div>
                    {item.evidence ? <div className="mt-1 text-xs text-[var(--taali-muted)]">{item.evidence}</div> : null}
                    <div className="mt-2 text-sm text-[var(--taali-text)]">{item.practice_advice}</div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel className="bg-[var(--taali-surface-elevated)] p-5">
            <h3 className="mb-3 text-base font-bold text-[var(--taali-text)]">Your AI collaboration style</h3>
            <div className="border border-[var(--taali-border)] bg-[var(--taali-purple-soft)] p-4">
              <div className="mb-1 font-semibold text-[var(--taali-text)]">{style.label || 'Profile pending'}</div>
              <p className="text-sm text-[var(--taali-text)]">
                {style.description || 'We are still generating your collaboration style.'}
              </p>
            </div>

            <h4 className="mb-2 mt-4 text-sm font-bold text-[var(--taali-text)]">Strengths to keep</h4>
            {strengths.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">No strengths available.</p>
            ) : (
              <ul className="space-y-1 text-sm text-[var(--taali-text)]">
                {strengths.slice(0, 3).map((item) => (
                  <li key={item.dimension_id}>
                    • {item.dimension} ({item.score}/10)
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
};
