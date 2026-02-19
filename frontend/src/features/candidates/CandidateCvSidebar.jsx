import React, { useEffect, useRef } from 'react';
import { FileText, Linkedin, Github, Twitter, ExternalLink, Loader2, MapPin, X } from 'lucide-react';

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

const SOCIAL_ICONS = {
  linkedin: Linkedin,
  github: Github,
  twitter: Twitter,
};

const CV_SECTION_HEADERS = /^(Professional\s+)?Experience|Work\s+(?:History|Experience)|Education|Skills|Summary|Objective|Qualifications|Certifications|Projects|Achievements|Languages$/i;

function formatCvWithSections(text) {
  if (!text || typeof text !== 'string') return null;
  const trimmed = text.trim();
  if (!trimmed) return null;
  const lines = trimmed.split(/\n/);
  const elements = [];
  let currentBlock = [];
  const flushBlock = (isHeader = false) => {
    const block = currentBlock.join('\n').trim();
    if (block) {
      if (isHeader) {
        elements.push(
          <h4 key={elements.length} className="text-sm font-semibold text-[var(--taali-text)] mt-4 mb-2 first:mt-0 border-b border-[var(--taali-border-muted)] pb-1">
            {block}
          </h4>
        );
      } else {
        elements.push(
          <div key={elements.length} className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800">
            {block}
          </div>
        );
      }
    }
    currentBlock = [];
  };
  for (const line of lines) {
    const trimmedLine = line.trim();
    if (CV_SECTION_HEADERS.test(trimmedLine)) {
      flushBlock(false);
      currentBlock.push(line);
      flushBlock(true);
    } else {
      currentBlock.push(line);
    }
  }
  flushBlock(false);
  if (elements.length === 0) return <span className="whitespace-pre-wrap block">{trimmed}</span>;
  return <div className="space-y-2">{elements}</div>;
}

export function CandidateCvSidebar({ open, application, onClose, onFetchCvFromWorkable, fetchingCvApplicationId }) {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);

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
  const data = application ?? null;

  const socials = Array.isArray(data?.candidate_social_profiles) ? data.candidate_social_profiles : [];
  const skills = Array.isArray(data?.candidate_skills) ? data.candidate_skills : [];
  const hasProfileSummary = data && (data.candidate_headline || data.candidate_location || socials.length > 0 || skills.length > 0);

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
          <div className="flex items-start gap-3 min-w-0 flex-1">
            {data?.candidate_image_url ? (
              <img
                src={data.candidate_image_url}
                alt=""
                className="w-10 h-10 rounded-full object-cover shrink-0"
              />
            ) : (
              <div className="w-10 h-10 rounded-full bg-[var(--taali-primary)] text-white flex items-center justify-center text-sm font-bold shrink-0">
                {(data?.candidate_name || '?').split(/\s+/).map((w) => w[0]).join('').toUpperCase().slice(0, 2)}
              </div>
            )}
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-bold tracking-tight text-[var(--taali-text)] truncate">
                {data?.candidate_name || data?.candidate_email || 'Candidate'}
              </h2>
              {data?.candidate_headline ? (
                <p className="text-sm text-gray-600 truncate">{data.candidate_headline}</p>
              ) : null}
              {data?.candidate_email ? (
                <p className="mt-0.5 text-sm text-[var(--taali-muted)] truncate">{data.candidate_email}</p>
              ) : null}
            </div>
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

        {/* Profile summary */}
        {hasProfileSummary ? (
          <div className="shrink-0 px-5 py-3 border-b border-[var(--taali-border-muted)] bg-white space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
              {data.candidate_location ? (
                <span className="inline-flex items-center gap-0.5">
                  <MapPin size={11} />
                  {data.candidate_location}
                </span>
              ) : null}
              {data.candidate_phone ? (
                <span>{data.candidate_phone}</span>
              ) : null}
              {socials.map((s, i) => {
                const type = (s.type || '').toLowerCase();
                const Icon = SOCIAL_ICONS[type] || ExternalLink;
                return (
                  <a
                    key={`${type}-${i}`}
                    href={s.url || '#'}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-gray-400 hover:text-gray-700"
                    title={s.name || type}
                  >
                    <Icon size={13} />
                  </a>
                );
              })}
            </div>
            {skills.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {skills.slice(0, 8).map((s) => (
                  <Badge key={s} variant="muted">{s}</Badge>
                ))}
                {skills.length > 8 ? (
                  <span className="text-xs text-gray-400">+{skills.length - 8}</span>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Meta row */}
        {data ? (
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

        {/* Body: CV from already-loaded application */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {data?.cv_text ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-[var(--taali-muted)]">
                <FileText size={14} />
                CV
              </div>
              <div className="rounded-lg border border-[var(--taali-border-muted)] bg-white p-4 font-[inherit]">
                {formatCvWithSections(data.cv_text)}
              </div>
            </div>
          ) : data ? (
            <div className="space-y-3">
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                No CV text available for this candidate.
              </div>
              {data.source === 'workable' && onFetchCvFromWorkable ? (
                <div>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={fetchingCvApplicationId === data.id}
                    onClick={() => onFetchCvFromWorkable(data)}
                  >
                    {fetchingCvApplicationId === data.id ? (
                      <>
                        <Loader2 size={14} className="animate-spin" />
                        Fetching from Workable...
                      </>
                    ) : (
                      'Fetch CV from Workable'
                    )}
                  </Button>
                  <p className="mt-1.5 text-xs text-gray-500">
                    Downloads the resume from Workable, extracts text, and updates the TAALI score.
                  </p>
                </div>
              ) : (
                <p className="text-xs text-gray-600">
                  Upload a CV for this application, or run a full Workable sync (not candidates-only) to import CVs.
                </p>
              )}
            </div>
          ) : null}
        </div>
      </aside>
    </>
  );
}
