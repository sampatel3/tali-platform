import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { BriefcaseBusiness, ChevronDown, ChevronUp, FileText, Loader2, Sparkles, Download } from 'lucide-react';

import {
  Badge,
  Button,
  Card,
  Panel,
} from '../../shared/ui/TaaliPrimitives';

export const RoleSummaryHeader = ({
  role,
  roleTasks,
  onEditRole,
  batchScoring,
  onBatchScore,
  onFetchCvs,
  fetchCvsProgress,
}) => {
  if (!role) return null;
  const [specExpanded, setSpecExpanded] = useState(false);
  const specPanelId = `role-spec-panel-${role.id || 'active'}`;

  /** Strip HTML to plain text (for preview). */
  const toPlainText = (value) => {
    if (!value) return '';
    const raw = String(value);
    try {
      if (typeof window !== 'undefined' && window.DOMParser) {
        const doc = new window.DOMParser().parseFromString(raw, 'text/html');
        const text = (doc?.body?.textContent || '').trim();
        return text || raw;
      }
    } catch {
      // ignore parsing failures
    }
    return raw.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
  };

  /** Strip HTML from string, preserve basic structure (for full job spec display). */
  const stripHtml = (html) => {
    if (!html || typeof html !== 'string') return html || '';
    return html
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/p>/gi, '\n\n')
      .replace(/<\/div>/gi, '\n')
      .replace(/<li[^>]*>/gi, '\n- ')
      .replace(/<\/li>/gi, '')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&nbsp;/g, ' ')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/\s{2,}/g, '\n')
      .trim();
  };

  /** Remove embedded Python dict/list reprs like {'key': 'val'} from job spec text. */
  const stripEmbeddedReprs = (text) => {
    if (!text || typeof text !== 'string') return text || '';
    let r = text;
    let prev;
    do {
      prev = r;
      r = r.replace(/\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g, '').replace(/\[[^[\]]*(?:\[[^[\]]*\][^[\]]*)*\]/g, '');
    } while (r !== prev);
    return r.replace(/\s{2,}/g, '\n').trim();
  };

  const rawSpec = (role.description || role.job_spec_text || '').trim();
  const noHtml = rawSpec.includes('<') ? stripHtml(rawSpec) : rawSpec;
  const specContent = stripEmbeddedReprs(noHtml) || noHtml || rawSpec;
  const roleDescription = specContent;
  const roleText = toPlainText(specContent);
  const rolePreview = roleText.length > 180 ? `${roleText.slice(0, 180)}…` : roleText;
  const hasSpecContent = specContent.length > 0;
  const additionalRequirements = role.additional_requirements?.trim() || '';
  const hasAdditionalRequirements = additionalRequirements.length > 0;
  const jobSpecReady = Boolean(role.job_spec_present || role.job_spec_filename || hasSpecContent);
  const jobSpecLabel = role.job_spec_filename
    || (jobSpecReady
      ? (role.source === 'workable' ? 'Imported from Workable' : hasSpecContent ? 'Job spec (text)' : 'Ready')
      : 'Not uploaded');

  useEffect(() => {
    setSpecExpanded(Boolean(hasSpecContent || hasAdditionalRequirements));
  }, [role.id, hasSpecContent, hasAdditionalRequirements]);

  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-xl font-bold tracking-tight text-[var(--taali-text)]">{role.name}</h2>
          {rolePreview ? <p className="text-sm text-[var(--taali-muted)]">{rolePreview}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          {onFetchCvs ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={onFetchCvs}
              disabled={Boolean(fetchCvsProgress)}
            >
              {fetchCvsProgress ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  Fetching CVs {fetchCvsProgress.fetched}/{fetchCvsProgress.total}...
                </>
              ) : (
                <>
                  <Download size={14} />
                  Fetch all CVs
                </>
              )}
            </Button>
          ) : null}
          {onBatchScore ? (
            <Button
              type="button"
              variant="primary"
              size="sm"
              onClick={onBatchScore}
              disabled={Boolean(batchScoring)}
            >
              {batchScoring ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  Scoring {batchScoring.scored}/{batchScoring.total}...
                </>
              ) : (
                <>
                  <Sparkles size={14} />
                  Score/Re-score all
                </>
              )}
            </Button>
          ) : null}
          <Button type="button" variant="secondary" size="sm" onClick={onEditRole}>
            Edit role
          </Button>
        </div>
      </div>
      <Card
        className="mt-3 overflow-hidden border-[var(--taali-border-muted)] p-0"
        style={{ background: 'linear-gradient(180deg, var(--taali-surface-subtle) 0%, var(--taali-surface) 60%)' }}
      >
        <div className="flex flex-col">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
            <div className="inline-flex items-center gap-2 text-sm text-[var(--taali-text)]">
              <FileText size={15} className="text-[var(--taali-muted)]" />
              <span className="font-medium">Job spec:</span>
              <span className="text-[var(--taali-muted)]">{jobSpecLabel}</span>
            </div>
            {jobSpecReady || hasAdditionalRequirements ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-expanded={specExpanded}
                aria-controls={specPanelId}
                onClick={() => setSpecExpanded((prev) => !prev)}
              >
                {specExpanded ? 'Hide details' : 'Show details'}
              </Button>
            ) : null}
          </div>

          {specExpanded ? (
            <div id={specPanelId} className="space-y-3 border-y border-[var(--taali-border-muted)] p-3.5">
              <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Job role spec</p>
                {roleDescription ? (
                  <div className="max-h-[360px] overflow-auto pr-1">
                    <div className="job-spec-content text-sm leading-relaxed text-[var(--taali-text)]">
                      <ReactMarkdown
                        components={{
                          h1: ({ node, ...p }) => <h1 className="mt-4 mb-2 border-b border-[var(--taali-border-soft)] pb-1 text-lg font-bold first:mt-0" {...p} />,
                          h2: ({ node, ...p }) => <h2 className="text-base font-bold mt-4 mb-2" {...p} />,
                          h3: ({ node, ...p }) => <h3 className="text-sm font-semibold mt-3 mb-1" {...p} />,
                          p: ({ node, ...p }) => <p className="my-2 leading-relaxed" {...p} />,
                          ul: ({ node, ...p }) => <ul className="list-disc pl-5 my-2 space-y-1" {...p} />,
                          ol: ({ node, ...p }) => <ol className="list-decimal pl-5 my-2 space-y-1" {...p} />,
                          li: ({ node, ...p }) => <li className="leading-relaxed" {...p} />,
                          strong: ({ node, ...p }) => <strong className="font-semibold text-[var(--taali-text)]" {...p} />,
                          br: () => <br />,
                        }}
                      >
                        {roleDescription}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <span className="text-sm text-[var(--taali-muted)]">No job spec text available.</span>
                )}
              </div>

              {hasAdditionalRequirements ? (
                <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-warm)] p-4">
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Additional requirements</p>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--taali-text)]">{additionalRequirements}</p>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="p-3.5">
            <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-3 py-2">
              <div className="inline-flex items-center gap-2 text-sm text-[var(--taali-text)]">
                <BriefcaseBusiness size={15} className="text-[var(--taali-muted)]" />
                <span className="font-medium">Tasks ({roleTasks.length}):</span>
                {roleTasks.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {roleTasks.map((task) => (
                      <Badge key={task.id} variant="muted">{task.name}</Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-[var(--taali-muted)]">No linked tasks</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* Role-level interview focus card removed — interview guidance is per-candidate now,
          surfaced in the candidate score sheet. The role.interview_focus data still feeds the
          per-candidate screening pack template under the hood, but it has no dedicated UI. */}
    </Panel>
  );
};
