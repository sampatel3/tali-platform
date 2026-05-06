import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { assessments as assessmentsApi } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';
import { CandidateFeedbackReportView } from './CandidateFeedbackReportView';

const DEFAULT_SHARE_TEXT = 'I completed a TAALI AI collaboration assessment.';

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
      <div className="flex min-h-screen items-center justify-center bg-[var(--taali-bg)]">
        <div className="flex items-center gap-2 text-sm text-[var(--taali-muted)]">
          <Loader2 size={18} className="animate-spin" />
          Loading your feedback report...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-[var(--bg)]">
        <div className="border-b border-[var(--line)] bg-[var(--bg-2)]">
          <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-4">
            <Logo onClick={() => {}} />
            <span className="font-mono text-xs uppercase tracking-[0.12em] text-[var(--mute)]">CANDIDATE · FEEDBACK</span>
          </div>
        </div>
        <div className="mx-auto max-w-3xl px-4 py-14">
          <div className="mc-auth-error-card" role="alert">
            <div className="title">Feedback not ready</div>
            <div className="body">{error}</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <CandidateFeedbackReportView
      payload={payload}
      onLinkedInShare={handleLinkedInShare}
      onDownloadPdf={handleDownloadPdf}
      downloadingPdf={downloadingPdf}
    />
  );
};
