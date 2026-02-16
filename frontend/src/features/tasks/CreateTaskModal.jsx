import React from 'react';
import { X } from 'lucide-react';

import {
  buildTaskFormState,
  buildTaskJsonPreview,
  collectSuitableRoles,
  collectWhatTaskTests,
  listRepoFiles,
} from './taskTemplates';

export const CreateTaskModal = ({ onClose, initialTask, viewOnly = false }) => {
  if (!initialTask) return null;
  const form = buildTaskFormState(initialTask);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-white border-2 border-black w-full max-w-3xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-8 py-5 border-b-2 border-black">
          <div>
            <h2 className="text-xl font-bold">Task Overview</h2>
            {!viewOnly && (
              <p className="font-mono text-xs text-red-600 mt-1">Task authoring is disabled. Tasks are backend-managed only.</p>
            )}
          </div>
          <button className="border-2 border-black p-1 hover:bg-black hover:text-white transition-colors" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        <div className="px-8 py-6 space-y-4">
          <div className="grid md:grid-cols-4 gap-3">
            <div className="border-2 border-black p-3">
              <div className="font-mono text-xs text-gray-500 mb-1">Task Type</div>
              <div className="font-bold capitalize">{String(form.task_type || 'debugging').replace('_', ' ')}</div>
            </div>
            <div className="border-2 border-black p-3">
              <div className="font-mono text-xs text-gray-500 mb-1">Difficulty</div>
              <div className="font-bold capitalize">{form.difficulty || 'mid'}</div>
            </div>
            <div className="border-2 border-black p-3">
              <div className="font-mono text-xs text-gray-500 mb-1">Duration</div>
              <div className="font-bold">{form.duration_minutes || 30} minutes</div>
            </div>
            <div className="border-2 border-black p-3">
              <div className="font-mono text-xs text-gray-500 mb-1">Claude Budget</div>
              <div className="font-bold">
                {typeof form.claude_budget_limit_usd === 'number' ? `$${form.claude_budget_limit_usd.toFixed(2)}` : 'Unlimited'}
              </div>
            </div>
          </div>

          <div className="border-2 border-black p-4">
            <div className="font-mono text-sm mb-2 font-bold">Description</div>
            <p className="font-mono text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
              {form.description || 'No description has been added.'}
            </p>
          </div>

          <div className="border-2 border-black p-4">
            <div className="font-mono text-sm mb-2 font-bold">Task Context</div>
            <p className="font-mono text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
              {form.scenario || form.description || 'No scenario has been added.'}
            </p>
          </div>

          <div className="border-2 border-black p-4">
            <div className="font-mono text-sm mb-2 font-bold">Suitable Roles</div>
            <div className="flex flex-wrap gap-2">
              {collectSuitableRoles(form).map((role) => (
                <span key={role} className="border border-gray-300 px-2 py-1 font-mono text-xs bg-gray-50 capitalize">
                  {role}
                </span>
              ))}
            </div>
          </div>

          <div className="border-2 border-black p-4">
            <div className="font-mono text-sm mb-2 font-bold">What This Task Tests</div>
            <ul className="space-y-1">
              {collectWhatTaskTests(form).map((item) => (
                <li key={item} className="font-mono text-sm text-gray-700">- {item}</li>
              ))}
            </ul>
          </div>

          <div className="border-2 border-black p-4">
            <div className="font-mono text-sm mb-2 font-bold">Repository</div>
            <div className="space-y-2">
              <div className="font-mono text-xs text-gray-600">
                Template repo URL: <span className="text-black">{form.template_repo_url || 'Not available'}</span>
              </div>
              <div className="font-mono text-xs text-gray-600">
                Local repo path: <span className="text-black">{form.main_repo_path || 'Not available'}</span>
              </div>
              <div className="font-mono text-xs text-gray-600">
                Files in template: <span className="text-black">{form.repo_file_count || listRepoFiles(form).length || 0}</span>
              </div>
              {listRepoFiles(form).length > 0 && (
                <div>
                  <div className="font-mono text-xs text-gray-500 mb-1">Repo files</div>
                  <div className="flex flex-wrap gap-1">
                    {listRepoFiles(form).slice(0, 12).map((path) => (
                      <span key={path} className="px-2 py-1 border border-gray-300 bg-gray-50 font-mono text-xs">{path}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="grid md:grid-cols-2 gap-4">
            <div className="border-2 border-black p-4">
              <div className="font-mono text-sm mb-2 font-bold">Starter Code</div>
              <pre className="w-full border border-gray-300 px-3 py-2 font-mono text-xs bg-gray-50 overflow-auto max-h-72 leading-relaxed whitespace-pre-wrap">
                {form.starter_code || '# No starter code'}
              </pre>
            </div>
            <div className="border-2 border-black p-4">
              <div className="font-mono text-sm mb-2 font-bold">Test Suite</div>
              <pre className="w-full border border-gray-300 px-3 py-2 font-mono text-xs bg-gray-50 overflow-auto max-h-72 leading-relaxed whitespace-pre-wrap">
                {form.test_code || '# No tests'}
              </pre>
            </div>
          </div>

          <div>
            <div className="font-mono text-sm mb-2 font-bold">Task JSON Preview</div>
            <p className="font-mono text-xs text-gray-500 mb-2">Aligned to the runtime task context schema (`task_id` maps to stored `task_key`).</p>
            <pre className="w-full border-2 border-black px-4 py-3 font-mono text-xs bg-gray-50 overflow-auto max-h-80 leading-relaxed">{JSON.stringify(buildTaskJsonPreview(form), null, 2)}</pre>
          </div>

          <button
            type="button"
            className="w-full border-2 border-black py-3 font-bold hover:bg-black hover:text-white transition-colors"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
