import React, { useCallback, useEffect, useRef } from 'react';
import { FileText, Loader2, X } from 'lucide-react';

import { Badge, Button } from '../../shared/ui/TaaliPrimitives';
import { statusVariant } from './candidatesUiUtils';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function CandidateCvSidebar({ open, applicationId, onClose, getApplication }) {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [data, setData] = React.useState(null);

  const fetchApplication = useCallback(async () => {
    if (!applicationId || !getApplication) return;
    setLoading(true);
    setError('');
    setData(null);
    try {
      const res = await getApplication(applicationId, { params: { include_cv_text: true } });
      setData(res?.data ?? null);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to load candidate');
    } finally {
      setLoading(false);
    }
  }, [applicationId, getApplication]);

  useEffect(() => {
    if (open && applicationId) fetchApplication();
  }, [open, applicationId, fetchApplication]);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement;
    document.body.style.overflow = 'hidden';
    const focusables = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
    if (focusables?.[0]) focusables[0].focus();
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
      if (previousFocusRef.current?.focus) previousFocusRef.current.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  const formatScore = (v) => (typeof v === 'number' ? `${v.toFixed(1)}/10` : '—');

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]"
        aria-hidden="true"
        onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Candidate details and CV"
        tabIndex={-1}
        className="fixed left-0 top-0 bottom-0 z-50 w-full max-w-[480px] bg-[var(--taali-surface)] border-r-2 border-[var(--taali-border)] shadow-xl flex flex-col focus:outline-none"
      >
        {/* Header */}
        <div className="shrink-0 flex items-start justify-between gap-3 px-5 py-4 border-b border-[var(--taali-border-muted)] bg-[#faf8ff]">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold tracking-tight text-[var(--taali-text)] truncate">
              {data?.candidate_name || data?.candidate_email || 'Candidate'}
            </h2>
            {data?.candidate_email ? (
              <p className="mt-0.5 text-sm text-[var(--taali-muted)] truncate">{data.candidate_email}</p>
            ) : null}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label="Close"
            className="!p-2 shrink-0"
            onClick={onClose}
          >
            <X size={18} />
          </Button>
        </div>

        {/* Meta row */}
        {data && !loading && !error ? (
          <div className="shrink-0 px-5 py-3 flex flex-wrap items-center gap-2 border-b border-[var(--taali-border-muted)] bg-white">
            {data.candidate_position ? (
              <span className="text-xs text-gray-600">{data.candidate_position}</span>
            ) : null}
            <Badge variant={statusVariant(data.status)}>{data.status || 'applied'}</Badge>
            <span className="text-xs text-gray-500">
              Workable: {formatScore(data.workable_score)}
            </span>
            <span className="text-xs text-gray-500">
              Taali: {formatScore(data.cv_match_score)}
            </span>
          </div>
        ) : null}

        {/* Body: loading / error / CV */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-12 text-[var(--taali-muted)]">
              <Loader2 size={24} className="animate-spin" />
              <span className="mt-2 text-sm">Loading CV…</span>
            </div>
          ) : error ? (
            <div className="py-6 text-sm text-red-600">{error}</div>
          ) : data?.cv_text ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-[var(--taali-muted)]">
                <FileText size={14} />
                CV
              </div>
              <div className="rounded-lg border border-[var(--taali-border-muted)] bg-white p-4 text-sm leading-relaxed text-gray-800 whitespace-pre-wrap font-[inherit]">
                {data.cv_text}
              </div>
            </div>
          ) : data ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
              No CV text available for this candidate. Upload a CV or run a full Workable sync to see it here.
            </div>
          ) : null}
        </div>
      </aside>
    </>
  );
}
