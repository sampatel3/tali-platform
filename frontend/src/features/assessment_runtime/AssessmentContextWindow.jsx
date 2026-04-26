import React, { forwardRef, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

const MARKDOWN_COMPONENTS = {
  p: ({ children }) => <p className="text-[14px] leading-7 text-[var(--ink-2)] [&:not(:first-child)]:mt-3">{children}</p>,
  ul: ({ children }) => <ul className="mt-3 list-disc space-y-2 pl-5 text-[14px] leading-7 text-[var(--ink-2)]">{children}</ul>,
  ol: ({ children }) => <ol className="mt-3 list-decimal space-y-2 pl-5 text-[14px] leading-7 text-[var(--ink-2)]">{children}</ol>,
  li: ({ children }) => <li className="pl-1 marker:text-[var(--purple)]">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-[var(--ink)]">{children}</strong>,
  em: ({ children }) => <em className="italic text-[var(--ink-2)]">{children}</em>,
  code: ({ children }) => (
    <code className="rounded-md bg-[var(--purple-soft)] px-1.5 py-0.5 font-mono text-[0.88em] text-[var(--purple-2)]">
      {children}
    </code>
  ),
};

export const AssessmentContextWindow = forwardRef(({
  taskName,
  taskRole,
  taskContext,
  repoFiles = [],
  cloneCommand,
}, ref) => {
  const [expanded, setExpanded] = useState(true);

  const scenarioSummary = useMemo(() => {
    const compact = String(taskContext || '')
      .replace(/[#*_>`~-]/g, ' ')
      .replace(/\[(.*?)\]\(.*?\)/g, '$1')
      .replace(/\s+/g, ' ')
      .trim();
    if (!compact) {
      return 'Read the repo, inspect the failing path, and sequence your fixes safely before you ship.';
    }
    return compact.length > 260 ? `${compact.slice(0, 257).trim()}...` : compact;
  }, [taskContext]);

  const repoFileCount = Array.isArray(repoFiles) ? repoFiles.length : 0;

  return (
    <section
      ref={ref}
      className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-sm)]"
    >
      <div className="grid gap-4 border-b border-[var(--line)] px-6 py-5 lg:grid-cols-[auto_minmax(0,1fr)_auto] lg:items-center lg:px-8">
        <div className="justify-self-start rounded-full bg-[var(--purple-soft)] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--purple)]">
          Task 01 / 01
        </div>

        <div className="min-w-0">
          <h2 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.01em] text-[var(--ink)]">
            Assessment brief
          </h2>
          <p className="mt-1 max-w-[820px] text-[13.5px] leading-6 text-[var(--ink-2)]">
            {scenarioSummary}
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2">
          {taskName ? (
            <div className="max-w-[260px] truncate rounded-full border border-[var(--line)] bg-[var(--bg)] px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
              {taskName}
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--bg)] px-3 py-1.5 text-[12px] font-medium text-[var(--ink-2)] transition-colors hover:border-[var(--purple)] hover:text-[var(--purple)]"
            aria-label={expanded ? 'Collapse brief' : 'Expand brief'}
          >
            {expanded ? 'Collapse brief' : 'Expand brief'}
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="grid gap-4 px-6 py-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(280px,0.7fr)] lg:px-8">
          <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg)] px-4 py-4">
            <ReactMarkdown components={MARKDOWN_COMPONENTS}>
              {String(taskContext || '').trim() || 'Task context has not been provided yet.'}
            </ReactMarkdown>
          </div>

          <div className="space-y-4">
            <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg)] px-4 py-4">
              <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--mute)]">How to work</div>
              <ul className="mt-3 space-y-2 text-[13px] leading-6 text-[var(--ink-2)]">
                <li>Read the task brief and inspect the repo before changing code.</li>
                <li>Use Claude for scoped help, then validate the patch path yourself.</li>
                <li>Run relevant checks in the live workspace before you submit.</li>
              </ul>
            </div>

            <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg)] px-4 py-4">
              <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--mute)]">Workspace</div>
              <div className="mt-3 text-[13px] leading-6 text-[var(--ink-2)]">
                {taskRole || 'Assessment runtime'}
              </div>
              <div className="mt-2 text-[13px] leading-6 text-[var(--mute)]">
                {repoFileCount > 0 ? `${repoFileCount} repo files loaded into the live workspace.` : 'The live workspace will load when the session starts.'}
              </div>
              {cloneCommand ? (
                <div className="mt-3 rounded-[10px] border border-[var(--line)] bg-[var(--bg-2)] px-3 py-3 font-mono text-[11px] leading-5 text-[var(--mute)]">
                  Clone command:
                  <div className="mt-1 break-all text-[var(--ink-2)]">{cloneCommand}</div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 px-6 py-4 lg:px-8">
          <div className="rounded-full border border-[var(--line)] bg-[var(--bg)] px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
            {repoFileCount > 0 ? `${repoFileCount} repo files ready` : 'Live workspace'}
          </div>
          <div className="rounded-full border border-[var(--line)] bg-[var(--bg)] px-3 py-1.5 text-[12px] text-[var(--ink-2)]">
            Use Claude for scoped help, then validate in the dock before you submit.
          </div>
          {cloneCommand ? (
            <div className="max-w-full truncate rounded-full border border-[var(--line)] bg-[var(--bg)] px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
              Clone command available
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
});

AssessmentContextWindow.displayName = 'AssessmentContextWindow';
