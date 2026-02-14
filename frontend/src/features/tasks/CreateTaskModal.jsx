import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Check,
  Loader2,
  X,
  Zap,
} from 'lucide-react';

import { tasks as tasksApi } from '../../shared/api';
import { TaskFormFields } from './TaskFormFields';
import {
  STANDARD_AI_PROMPT_TEMPLATE,
  STANDARD_MANUAL_TEMPLATE,
  buildTaskFormState,
  buildTaskJsonPreview,
  collectSuitableRoles,
  collectWhatTaskTests,
  listRepoFiles,
} from './taskTemplates';

const createFormFromTask = (initialTask) => {
  if (!initialTask) {
    return buildTaskFormState(null);
  }

  return {
    ...buildTaskFormState(initialTask),
    claude_budget_limit_usd: initialTask.claude_budget_limit_usd ?? null,
  };
};

export const CreateTaskModal = ({ onClose, onCreated, initialTask, onUpdated, viewOnly = false }) => {
  const isEdit = Boolean(initialTask) && !viewOnly;
  const [step, setStep] = useState(initialTask ? 'manual' : 'ai-prompt');
  const [form, setForm] = useState(() => createFormFromTask(initialTask));
  const [aiPrompt, setAiPrompt] = useState(STANDARD_AI_PROMPT_TEMPLATE);
  const [aiDifficulty, setAiDifficulty] = useState('');
  const [aiDuration, setAiDuration] = useState('');
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (initialTask) {
      setForm(createFormFromTask(initialTask));
      setStep('manual');
      return;
    }

    setForm(buildTaskFormState(null));
    setStep('ai-prompt');
    setAiPrompt(STANDARD_AI_PROMPT_TEMPLATE);
  }, [initialTask]);

  const handleGenerate = async () => {
    setError('');
    if (!aiPrompt.trim()) {
      setError('Describe what you want to assess');
      return;
    }

    setGenerating(true);
    try {
      const res = await tasksApi.generate({
        prompt: aiPrompt,
        difficulty: aiDifficulty || undefined,
        duration_minutes: aiDuration ? parseInt(aiDuration) : undefined,
      });

      setForm((prev) => ({
        ...prev,
        ...STANDARD_MANUAL_TEMPLATE,
        ...res.data,
        extra_data: res.data?.extra_data || prev.extra_data || STANDARD_MANUAL_TEMPLATE.extra_data,
        main_repo_path: '',
        template_repo_url: '',
        repo_file_count: 0,
      }));
      setStep('ai-review');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to generate task — try again');
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async () => {
    setError('');
    if (!form.name || !form.description) {
      setError('Name and description are required');
      return;
    }
    if (!form.starter_code) {
      setError('Starter code is required');
      return;
    }
    if (!form.test_code) {
      setError('Test suite is required');
      return;
    }

    setLoading(true);
    try {
      if (isEdit && initialTask?.id) {
        const res = await tasksApi.update(initialTask.id, form);
        onUpdated?.(initialTask.id, res.data);
      } else {
        const res = await tasksApi.create({ ...form, is_active: true });
        onCreated(res.data);
      }
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || (isEdit ? 'Failed to update task' : 'Failed to create task'));
    } finally {
      setLoading(false);
    }
  };

  const modalTitle = viewOnly ? 'Task Overview' : isEdit ? 'Edit Task' : {
    'ai-prompt': 'Generate Task with AI',
    'ai-review': 'Review Generated Task',
    manual: 'Create Task Manually',
  }[step];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-white border-2 border-black w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-8 py-5 border-b-2 border-black">
          <div className="flex items-center gap-3">
            {!viewOnly && !isEdit && step !== 'ai-prompt' && (
              <button
                className="border-2 border-black p-1 hover:bg-black hover:text-white transition-colors"
                onClick={() => setStep('ai-prompt')}
              >
                <ArrowLeft size={16} />
              </button>
            )}
            <h2 className="text-xl font-bold">{modalTitle}</h2>
          </div>
          <button className="border-2 border-black p-1 hover:bg-black hover:text-white transition-colors" onClick={onClose}>
            <X size={18} />
          </button>
        </div>

        <div className="px-8 py-6">
          {error && (
            <div className="border-2 border-red-500 bg-red-50 p-3 mb-5 font-mono text-sm text-red-700 flex items-center gap-2">
              <AlertTriangle size={16} /> {error}
            </div>
          )}

          {!isEdit && step === 'ai-prompt' && (
            <div className="space-y-5">
              <div className="border-2 border-black p-3 bg-purple-50">
                <div className="font-mono text-xs font-bold" style={{ color: '#6b21a8' }}>
                  GenAI-first creation is enabled
                </div>
                <div className="font-mono text-xs text-gray-600 mt-1">
                  Use the standard prompt template below, then generate and review before saving.
                </div>
              </div>
              <div>
                <label className="block font-mono text-sm mb-1 font-bold">What do you want to assess?</label>
                <p className="font-mono text-xs text-gray-500 mb-2">Be specific about the role, skills, and what kind of challenge you want. The more detail, the better the task.</p>
                <textarea
                  className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[140px]"
                  placeholder={"Example: Create a debugging task for a senior Python backend engineer.\nThe code should be a REST API handler with 3 bugs:\n- An off-by-one error in pagination\n- A race condition in the cache layer\n- Incorrect error handling that swallows exceptions\nShould test async/await knowledge and production debugging skills."}
                  value={aiPrompt}
                  onChange={(e) => setAiPrompt(e.target.value)}
                  autoFocus
                />
                <div className="flex flex-wrap items-center gap-2 mt-2">
                  <button
                    type="button"
                    className="border border-black px-3 py-1.5 font-mono text-xs hover:bg-black hover:text-white transition-colors"
                    onClick={() => setAiPrompt(STANDARD_AI_PROMPT_TEMPLATE)}
                  >
                    Use Standard Prompt Template
                  </button>
                  <button
                    type="button"
                    className="border border-black px-3 py-1.5 font-mono text-xs hover:bg-black hover:text-white transition-colors"
                    onClick={() => {
                      setForm((prev) => ({
                        ...prev,
                        ...STANDARD_MANUAL_TEMPLATE,
                        main_repo_path: '',
                        template_repo_url: '',
                        repo_file_count: 0,
                      }));
                      setStep('manual');
                    }}
                  >
                    Switch to Manual Template
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block font-mono text-sm mb-1">Difficulty (optional)</label>
                  <select
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
                    value={aiDifficulty}
                    onChange={(e) => setAiDifficulty(e.target.value)}
                  >
                    <option value="">Auto-detect</option>
                    <option value="junior">Junior</option>
                    <option value="mid">Mid-Level</option>
                    <option value="senior">Senior</option>
                    <option value="staff">Staff+</option>
                  </select>
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Duration (optional)</label>
                  <select
                    className="w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white"
                    value={aiDuration}
                    onChange={(e) => setAiDuration(e.target.value)}
                  >
                    <option value="">Auto-detect</option>
                    <option value="15">15 min</option>
                    <option value="30">30 min</option>
                    <option value="45">45 min</option>
                    <option value="60">60 min</option>
                    <option value="90">90 min</option>
                  </select>
                </div>
              </div>
              <button
                className="w-full border-2 border-black py-3 font-bold text-white transition-colors flex items-center justify-center gap-2"
                style={{ backgroundColor: generating ? '#6b21a8' : '#9D00FF' }}
                onClick={handleGenerate}
                disabled={generating}
              >
                {generating ? (
                  <><Loader2 size={18} className="animate-spin" /> Generating task with Claude...</>
                ) : (
                  <><Zap size={18} /> Generate Task</>
                )}
              </button>
              {generating && (
                <p className="font-mono text-xs text-center text-gray-500">This usually takes 5-10 seconds...</p>
              )}
            </div>
          )}

          {!isEdit && step === 'ai-review' && (
            <div className="space-y-4">
              <div className="border-2 border-black p-3 mb-2 flex items-center gap-2" style={{ backgroundColor: '#f3e8ff' }}>
                <Bot size={16} style={{ color: '#9D00FF' }} />
                <span className="font-mono text-xs" style={{ color: '#6b21a8' }}>
                  AI-generated — review and edit anything below before saving
                </span>
              </div>
              <TaskFormFields form={form} setForm={setForm} />
              <div className="flex gap-3">
                <button
                  className="flex-1 border-2 border-black py-3 font-bold hover:bg-gray-100 transition-colors flex items-center justify-center gap-2"
                  onClick={() => setStep('ai-prompt')}
                >
                  <Zap size={16} /> Regenerate
                </button>
                <button
                  className="flex-1 border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors flex items-center justify-center gap-2"
                  style={{ backgroundColor: '#9D00FF' }}
                  onClick={handleSave}
                  disabled={loading}
                >
                  {loading ? <><Loader2 size={18} className="animate-spin" /> Saving...</> : <><Check size={18} /> Save Task</>}
                </button>
              </div>
            </div>
          )}

          {step === 'manual' && (
            <div className="space-y-4">
              <TaskFormFields form={form} setForm={setForm} readOnly={viewOnly} />
              {!viewOnly && (
                <button
                  className="w-full border-2 border-black py-3 font-bold text-white hover:bg-black transition-colors flex items-center justify-center gap-2"
                  style={{ backgroundColor: '#9D00FF' }}
                  onClick={handleSave}
                  disabled={loading}
                >
                  {loading ? <><Loader2 size={18} className="animate-spin" /> {isEdit ? 'Saving...' : 'Creating...'}</> : (isEdit ? 'Save changes' : 'Create Task')}
                </button>
              )}
              {viewOnly && (
                <>
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
                    <div className="font-mono text-sm mb-2 font-bold">Task Context</div>
                    <p className="font-mono text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                      {form.scenario || form.description || 'No scenario has been added.'}
                    </p>
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
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
