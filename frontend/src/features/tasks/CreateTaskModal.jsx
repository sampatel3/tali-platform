import React from 'react';
import { X } from 'lucide-react';

import {
  buildTaskFormState,
  buildTaskJsonPreview,
  collectSuitableRoles,
  collectWhatTaskTests,
  listRepoFiles,
} from './taskTemplates';
import { Button, Panel } from '../../shared/ui/TaaliPrimitives';

export const CreateTaskModal = ({ onClose, initialTask, viewOnly = false }) => {
  if (!initialTask) return null;
  const form = buildTaskFormState(initialTask);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-[var(--taali-surface)] border-2 border-[var(--taali-border)] w-full max-w-3xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-8 py-5 border-b-2 border-[var(--taali-border)]">
          <div>
            <h2 className="text-xl font-bold text-[var(--taali-text)]">Task Overview</h2>
            {!viewOnly && (
              <p className="font-mono text-xs text-[var(--taali-danger)] mt-1">Task authoring is disabled. Tasks are backend-managed only.</p>
            )}
          </div>
          <Button variant="ghost" size="sm" className="!p-2" onClick={onClose} aria-label="Close">
            <X size={18} />
          </Button>
        </div>

        <div className="px-8 py-6 space-y-4">
          <div className="grid md:grid-cols-4 gap-3">
            <Panel as="div" className="p-3">
              <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Task Type</div>
              <div className="font-bold capitalize text-[var(--taali-text)]">{String(form.task_type || 'debugging').replace('_', ' ')}</div>
            </Panel>
            <Panel as="div" className="p-3">
              <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Difficulty</div>
              <div className="font-bold capitalize text-[var(--taali-text)]">{form.difficulty || 'mid'}</div>
            </Panel>
            <Panel as="div" className="p-3">
              <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Duration</div>
              <div className="font-bold text-[var(--taali-text)]">{form.duration_minutes || 30} minutes</div>
            </Panel>
            <Panel as="div" className="p-3">
              <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Claude Budget</div>
              <div className="font-bold text-[var(--taali-text)]">
                {typeof form.claude_budget_limit_usd === 'number' ? `$${form.claude_budget_limit_usd.toFixed(2)}` : 'Unlimited'}
              </div>
            </Panel>
          </div>

          <Panel as="div" className="p-4">
            <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">Description</div>
            <p className="font-mono text-sm text-[var(--taali-text)] whitespace-pre-wrap leading-relaxed">
              {form.description || 'No description has been added.'}
            </p>
          </Panel>

          <Panel as="div" className="p-4">
            <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">Task Context</div>
            <p className="font-mono text-sm text-[var(--taali-text)] whitespace-pre-wrap leading-relaxed">
              {form.scenario || form.description || 'No scenario has been added.'}
            </p>
          </Panel>

          <Panel as="div" className="p-4">
            <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">Suitable Roles</div>
            <div className="flex flex-wrap gap-2">
              {collectSuitableRoles(form).map((role) => (
                <span key={role} className="px-2 py-1 font-mono text-xs border border-[var(--taali-border-muted)] bg-[var(--taali-bg)] capitalize text-[var(--taali-text)]">
                  {role}
                </span>
              ))}
            </div>
          </Panel>

          <Panel as="div" className="p-4">
            <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">What This Task Tests</div>
            <ul className="space-y-1">
              {collectWhatTaskTests(form).map((item) => (
                <li key={item} className="font-mono text-sm text-[var(--taali-text)]">- {item}</li>
              ))}
            </ul>
          </Panel>

          <Panel as="div" className="p-4">
            <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">Repository</div>
            <div className="space-y-2">
              <div className="font-mono text-xs text-[var(--taali-muted)]">
                Template repo URL: <span className="text-[var(--taali-text)]">{form.template_repo_url || 'Not available'}</span>
              </div>
              <div className="font-mono text-xs text-[var(--taali-muted)]">
                Local repo path: <span className="text-[var(--taali-text)]">{form.main_repo_path || 'Not available'}</span>
              </div>
              <div className="font-mono text-xs text-[var(--taali-muted)]">
                Files in template: <span className="text-[var(--taali-text)]">{form.repo_file_count || listRepoFiles(form).length || 0}</span>
              </div>
              {listRepoFiles(form).length > 0 && (
                <div>
                  <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">Repo files</div>
                  <div className="flex flex-wrap gap-1">
                    {listRepoFiles(form).slice(0, 12).map((path) => (
                      <span key={path} className="px-2 py-1 border border-[var(--taali-border-muted)] bg-[var(--taali-bg)] font-mono text-xs text-[var(--taali-text)]">{path}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </Panel>

          <div className="grid md:grid-cols-2 gap-4">
            <Panel as="div" className="p-4">
              <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">Starter Code</div>
              <pre className="w-full border border-[var(--taali-border-muted)] px-3 py-2 font-mono text-xs bg-[var(--taali-bg)] overflow-auto max-h-72 leading-relaxed whitespace-pre-wrap text-[var(--taali-text)]">
                {form.starter_code || '# No starter code'}
              </pre>
            </Panel>
            <Panel as="div" className="p-4">
              <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">Test Suite</div>
              <pre className="w-full border border-[var(--taali-border-muted)] px-3 py-2 font-mono text-xs bg-[var(--taali-bg)] overflow-auto max-h-72 leading-relaxed whitespace-pre-wrap text-[var(--taali-text)]">
                {form.test_code || '# No tests'}
              </pre>
            </Panel>
          </div>

          <div>
            <div className="font-mono text-sm mb-2 font-bold text-[var(--taali-text)]">Task JSON Preview</div>
            <p className="font-mono text-xs text-[var(--taali-muted)] mb-2">Aligned to the runtime task context schema (`task_id` maps to stored `task_key`).</p>
            <pre className="w-full border-2 border-[var(--taali-border)] px-4 py-3 font-mono text-xs bg-[var(--taali-bg)] overflow-auto max-h-80 leading-relaxed text-[var(--taali-text)]">{JSON.stringify(buildTaskJsonPreview(form), null, 2)}</pre>
          </div>

          <Button variant="secondary" className="w-full" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
};
