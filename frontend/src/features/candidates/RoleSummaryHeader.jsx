import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { BriefcaseBusiness, ChevronDown, ChevronUp, FileText } from 'lucide-react';

import {
  Badge,
  Button,
  Card,
  Panel,
} from '../../shared/ui/TaaliPrimitives';

export const RoleSummaryHeader = ({ role, roleTasks, onEditRole }) => {
  if (!role) return null;
  const focus = role.interview_focus || null;
  const focusQuestions = Array.isArray(focus?.questions) ? focus.questions.slice(0, 3) : [];
  const focusTriggers = Array.isArray(focus?.manual_screening_triggers) ? focus.manual_screening_triggers : [];
  const hasInterviewFocus = focusQuestions.length > 0;
  const [focusExpanded, setFocusExpanded] = useState(true);
  const [specExpanded, setSpecExpanded] = useState(false);
  const focusPanelId = `interview-focus-panel-${role.id || 'active'}`;
  const specPanelId = `role-spec-panel-${role.id || 'active'}`;

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

  const roleDescription = (role.description || '').trim();
  const roleText = toPlainText(role.description);
  const rolePreview = roleText.length > 180 ? `${roleText.slice(0, 180)}…` : roleText;
  const hasSpecContent = roleDescription.length > 0;
  const jobSpecReady = Boolean(role.job_spec_present || role.job_spec_filename || hasSpecContent);
  const jobSpecLabel = role.job_spec_filename
    || (jobSpecReady
      ? (role.source === 'workable' ? 'Imported from Workable' : hasSpecContent ? 'Job spec (text)' : 'Ready')
      : 'Not uploaded');

  useEffect(() => {
    setFocusExpanded(true);
    setSpecExpanded(Boolean(roleDescription));
  }, [role.id, roleDescription]);

  return (
    <Panel className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-2xl font-bold tracking-tight text-[var(--taali-text)]">{role.name}</h2>
          {rolePreview ? <p className="text-sm text-[var(--taali-muted)]">{rolePreview}</p> : null}
        </div>
        <Button type="button" variant="secondary" size="sm" onClick={onEditRole}>
          Edit role
        </Button>
      </div>
      <Card className="mt-4 p-3 bg-[#faf8ff]">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="inline-flex items-center gap-2 text-sm text-gray-700">
              <FileText size={15} className="text-gray-500" />
              <span className="font-medium">Job spec:</span>
              <span className="text-gray-600">{jobSpecLabel}</span>
            </div>
            {jobSpecReady ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-expanded={specExpanded}
                aria-controls={specPanelId}
                onClick={() => setSpecExpanded((prev) => !prev)}
              >
                {specExpanded ? 'Hide details' : 'Details'}
              </Button>
            ) : null}
          </div>

          {specExpanded ? (
            <div id={specPanelId} className="border border-[var(--taali-border-muted)] bg-white rounded-lg p-5 max-h-[400px] overflow-auto">
              {roleDescription ? (
                <div className="job-spec-content text-sm text-gray-800 leading-relaxed">
                  <ReactMarkdown
                    components={{
                      h1: ({ node, ...p }) => <h1 className="text-lg font-bold mt-4 mb-2 first:mt-0 border-b border-gray-200 pb-1" {...p} />,
                      h2: ({ node, ...p }) => <h2 className="text-base font-bold mt-4 mb-2" {...p} />,
                      h3: ({ node, ...p }) => <h3 className="text-sm font-semibold mt-3 mb-1" {...p} />,
                      p: ({ node, ...p }) => <p className="my-2 leading-relaxed" {...p} />,
                      ul: ({ node, ...p }) => <ul className="list-disc pl-5 my-2 space-y-1" {...p} />,
                      ol: ({ node, ...p }) => <ol className="list-decimal pl-5 my-2 space-y-1" {...p} />,
                      li: ({ node, ...p }) => <li className="leading-relaxed" {...p} />,
                      strong: ({ node, ...p }) => <strong className="font-semibold text-gray-900" {...p} />,
                      br: () => <br />,
                    }}
                  >
                    {roleDescription}
                  </ReactMarkdown>
                </div>
              ) : (
                <span className="text-gray-500">—</span>
              )}
            </div>
          ) : null}

          {role.additional_requirements?.trim() ? (
            <div className="text-sm text-gray-700">
              <span className="font-medium">Additional requirements (for CV scoring):</span>
              <p className="mt-1 text-xs text-gray-600 whitespace-pre-wrap">{role.additional_requirements.trim()}</p>
            </div>
          ) : null}

          <div className="inline-flex items-center gap-2 text-sm text-gray-700">
            <BriefcaseBusiness size={15} className="text-gray-500" />
            <span className="font-medium">Tasks ({roleTasks.length}):</span>
            {roleTasks.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {roleTasks.map((task) => (
                  <Badge key={task.id} variant="muted">{task.name}</Badge>
                ))}
              </div>
            ) : (
              <span className="text-gray-500">No linked tasks</span>
            )}
          </div>
        </div>
      </Card>

      {hasInterviewFocus ? (
        <Card className="mt-4 p-4">
          <button
            type="button"
            className="flex w-full items-start justify-between gap-3 text-left"
            aria-expanded={focusExpanded}
            aria-controls={focusPanelId}
            onClick={() => setFocusExpanded((prev) => !prev)}
          >
            <div>
              <p className="text-sm font-semibold text-gray-900">Interview focus</p>
              <p className="text-xs text-gray-500">Manual screening pointers from the job spec.</p>
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              {role.interview_focus_generated_at ? (
                <span className="text-[11px] text-gray-400">
                  Updated {new Date(role.interview_focus_generated_at).toLocaleDateString()}
                </span>
              ) : null}
              <span className="inline-flex items-center gap-1.5 font-semibold text-gray-700">
                {focusExpanded ? 'Collapse' : 'Expand'}
                {focusExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </span>
            </div>
          </button>

          {focusExpanded ? (
            <div id={focusPanelId}>
              {focus?.role_summary ? (
                <p className="mt-2 text-sm text-gray-700">{focus.role_summary}</p>
              ) : null}

              {focusTriggers.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {focusTriggers.map((trigger) => (
                    <Badge key={trigger} variant="muted">{trigger}</Badge>
                  ))}
                </div>
              ) : null}

              <div className="mt-3 space-y-2">
                {focusQuestions.map((item, index) => (
                  <Card key={`${item.question}-${index}`} className="border-[var(--taali-border-muted)] bg-[#fffcf5] px-3 py-2">
                    <p className="text-sm font-semibold text-gray-900">
                      {`Q${index + 1}. `}
                      {item.question}
                    </p>
                    {Array.isArray(item.what_to_listen_for) && item.what_to_listen_for.length > 0 ? (
                      <p className="mt-1 text-xs text-gray-700">
                        <span className="font-semibold text-gray-800">Look for:</span>
                        {' '}
                        {item.what_to_listen_for.join(' • ')}
                      </p>
                    ) : null}
                    {Array.isArray(item.concerning_signals) && item.concerning_signals.length > 0 ? (
                      <p className="mt-1 text-xs text-gray-600">
                        <span className="font-semibold text-gray-700">Watch out for:</span>
                        {' '}
                        {item.concerning_signals.join(' • ')}
                      </p>
                    ) : null}
                  </Card>
                ))}
              </div>
            </div>
          ) : null}
        </Card>
      ) : (
        <Card className="mt-4 border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          Upload a job spec to generate interview focus pointers for manual screening.
        </Card>
      )}
    </Panel>
  );
};
