import React, { useEffect, useMemo, useState } from 'react';
import { Download, ExternalLink, Loader2 } from 'lucide-react';

import { assessments as assessmentsApi } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';
import { Badge, Button, Panel } from '../../shared/ui/TaaliPrimitives';
import { dimensionOrder } from '../../scoring/scoringDimensions';

const DEFAULT_SHARE_TEXT = 'I completed a TAALI AI collaboration assessment.';

const clampPercent = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, number));
};

export const CandidateFeedbackPage = ({ token }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [payload, setPayload] = useState(null);
  const [downloadingPdf, setDownloadingPdf] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchFeedback = async () => {
      setLoading(true);
      setError('');
      try {
        const res = await assessmentsApi.getCandidateFeedback(token);
        if (!cancelled) setPayload(res.data || null);
      } catch (err) {
        if (cancelled) return;
        setPayload(null);
        setError(err?.response?.data?.detail || 'Feedback report is unavailable right now.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    if (token) fetchFeedback();
    else {
      setLoading(false);
      setError('Assessment token is missing.');
    }
    return () => {
      cancelled = true;
    };
  }, [token]);

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
  }, [feedback?.dimensions]);

  const handleLinkedInShare = () => {
    const url = typeof window !== 'undefined' ? window.location.href : '';
    const shareUrl = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}&summary=${encodeURIComponent(DEFAULT_SHARE_TEXT)}`;
    window.open(shareUrl, '_blank', 'noopener,noreferrer');
  };

  const handleDownloadPdf = async () => {
    if (!token) return;
    setDownloadingPdf(true);
    try {
      const res = await assessmentsApi.downloadCandidateFeedbackPdf(token);
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `candidate-feedback-${payload?.assessment_id || 'report'}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to download PDF.');
    } finally {
      setDownloadingPdf(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#f7f7fb] flex items-center justify-center">
        <div className="flex items-center gap-2 text-sm text-[var(--taali-muted)]">
          <Loader2 size={18} className="animate-spin" />
          Loading your feedback report...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-[#f7f7fb]">
        <div className="border-b-2 border-[var(--taali-border)] bg-white">
          <div className="mx-auto max-w-5xl px-4 py-4 flex items-center gap-3">
            <Logo onClick={() => {}} />
            <span className="font-mono text-xs text-[var(--taali-muted)]">Candidate feedback</span>
          </div>
        </div>
        <div className="mx-auto max-w-3xl px-4 py-14">
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-6">
            <h1 className="text-xl font-bold text-[var(--taali-danger)] mb-2">Feedback not ready</h1>
            <p className="text-sm text-[var(--taali-danger)]">{error}</p>
          </Panel>
        </div>
      </div>
    );
  }

  const strongest = feedback?.strongest_moment || {};
  const improvements = Array.isArray(feedback?.improvements) ? feedback.improvements : [];
  const strengths = Array.isArray(feedback?.strengths) ? feedback.strengths : [];
  const style = feedback?.style || {};
  const benchmark = feedback?.benchmark || {};
  const benchmarkLabel = feedback?.overall_percentile_label
    || (benchmark?.available ? null : 'Benchmark coming soon');

  return (
    <div className="min-h-screen bg-[#f7f7fb]">
      <div className="border-b-2 border-[var(--taali-border)] bg-white">
        <div className="mx-auto max-w-5xl px-4 py-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Logo onClick={() => {}} />
            <span className="font-mono text-xs text-[var(--taali-muted)]">Candidate feedback</span>
          </div>
          <Badge variant="muted" className="font-mono text-[11px]">{payload?.organization_name || feedback?.organization_name || 'Company'}</Badge>
        </div>
      </div>

      <div className="mx-auto max-w-5xl px-4 py-8 space-y-5">
        <Panel className="p-6 bg-white">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-bold text-[var(--taali-text)] mb-1">Your AI Collaboration Profile</h1>
              <p className="text-sm text-[var(--taali-muted)]">
                {feedback?.task_name || payload?.task_name || 'Assessment'}
                {feedback?.role_name ? ` · ${feedback.role_name}` : ''}
              </p>
            </div>
            <div className="flex gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={handleLinkedInShare}>
                <ExternalLink size={14} />
                Share on LinkedIn
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={handleDownloadPdf} disabled={downloadingPdf}>
                <Download size={14} />
                {downloadingPdf ? 'Preparing PDF...' : 'Download PDF'}
              </Button>
            </div>
          </div>

          <div className="mt-5 grid gap-4 md:grid-cols-[180px_minmax(0,1fr)]">
            <div className="border border-[var(--taali-border)] bg-[var(--taali-purple-soft)] p-4">
              <div className="text-xs font-mono text-[var(--taali-muted)] mb-1">Overall score</div>
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
                  <div className="h-2 bg-[var(--taali-border-muted)] overflow-hidden">
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

        <Panel className="p-6 bg-white">
          <h2 className="text-lg font-bold text-[var(--taali-text)] mb-2">Your strongest moment</h2>
          <div className="font-mono text-xs text-[var(--taali-muted)] mb-2">
            Minute {strongest?.minute ?? 0}
          </div>
          <div className="border border-[var(--taali-border)] bg-[#faf8ff] p-3 text-sm whitespace-pre-wrap">
            {strongest?.prompt || 'Transcript unavailable for this assessment.'}
          </div>
          {strongest?.reason ? (
            <p className="mt-3 text-sm text-[var(--taali-text)]">{strongest.reason}</p>
          ) : null}
        </Panel>

        <div className="grid gap-5 md:grid-cols-2">
          <Panel className="p-5 bg-white">
            <h3 className="text-base font-bold mb-3">Improvement opportunities</h3>
            {improvements.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">No targeted improvements available yet.</p>
            ) : (
              <div className="space-y-3">
                {improvements.slice(0, 3).map((item) => (
                  <div key={item.dimension_id} className="border border-[var(--taali-border)] p-3">
                    <div className="font-medium text-sm text-[var(--taali-text)]">{item.dimension} ({item.score}/10)</div>
                    {item.evidence ? <div className="mt-1 text-xs text-[var(--taali-muted)]">{item.evidence}</div> : null}
                    <div className="mt-2 text-sm text-[var(--taali-text)]">{item.practice_advice}</div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel className="p-5 bg-white">
            <h3 className="text-base font-bold mb-3">Your AI collaboration style</h3>
            <div className="border border-[var(--taali-border)] bg-[var(--taali-purple-soft)] p-4">
              <div className="font-semibold text-[var(--taali-text)] mb-1">{style.label || 'Profile pending'}</div>
              <p className="text-sm text-[var(--taali-text)]">{style.description || 'We are still generating your collaboration style.'}</p>
            </div>

            <h4 className="text-sm font-bold mt-4 mb-2 text-[var(--taali-text)]">Strengths to keep</h4>
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

