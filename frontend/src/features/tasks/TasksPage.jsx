import React, { useState, useEffect } from 'react';
import {
  ArrowLeft,
  X,
  AlertTriangle,
  Bot,
  Loader2,
  Zap,
  Check,
  Code,
  Eye,
  Pencil,
  Trash2,
} from 'lucide-react';
import { tasks as tasksApi } from '../../lib/api';


const STANDARD_AI_PROMPT_TEMPLATE = `Create a realistic technical assessment task.

Role and seniority:
- Role: [e.g. backend engineer, data engineer, AI engineer]
- Seniority: [junior/mid/senior/staff]

What should be tested:
- Core skills: [list]
- Real-world scenario: [brief context]
- Signals to evaluate: [problem-solving, debugging, testing, communication, AI collaboration]

Task requirements:
- Include starter Python code with realistic issues or missing logic
- Include a pytest suite with 3-6 meaningful tests
- Keep duration practical for hiring workflows
- Return structured task metadata: role fit, rubric, and suitable roles`;

const STANDARD_MANUAL_TEMPLATE = {
  name: 'Async Data Pipeline Stabilization',
  description: 'Fix reliability issues in an async event processing service used in production. The goal is to make ingestion deterministic, handle malformed events safely, and keep processing idempotent.',
  task_type: 'debugging',
  difficulty: 'mid',
  duration_minutes: 45,
  claude_budget_limit_usd: 5,
  starter_code: `from typing import List, Dict\n\n\ndef process_events(events: List[Dict]) -> int:\n    \"\"\"Process incoming events and return number of successful writes.\"\"\"\n    processed = 0\n    for event in events:\n        # TODO: harden validation and idempotency checks\n        if event.get("id"):\n            processed += 1\n    return processed\n`,
  test_code: `from src.task import process_events\n\n\ndef test_processes_valid_events():\n    events = [{"id": "1"}, {"id": "2"}]\n    assert process_events(events) == 2\n\n\ndef test_skips_invalid_event_payload():\n    events = [{"id": "1"}, {"payload": {}}]\n    assert process_events(events) == 1\n\n\ndef test_handles_empty_input():\n    assert process_events([]) == 0\n`,
  role: 'backend_engineer',
  scenario: 'A production ingestion pipeline is dropping events and producing duplicate records during spikes. Stabilize the processing logic and keep behavior predictable.',
  task_key: '',
  repo_structure: null,
  evaluation_rubric: null,
  extra_data: {
    suitable_roles: ['backend engineer', 'platform engineer', 'full-stack engineer'],
    skills_tested: ['debugging', 'defensive coding', 'test design'],
  },
};

const DEFAULT_TESTS_BY_TYPE = {
  debugging: ['Root-cause analysis', 'Bug isolation', 'Regression-safe fixes'],
  ai_engineering: ['AI prompt quality', 'Grounded tool usage', 'Output validation'],
  optimization: ['Performance reasoning', 'Tradeoff decisions', 'Instrumentation'],
  build: ['System design', 'Correct implementation', 'Testing discipline'],
  refactor: ['Code readability', 'Architecture choices', 'Behavior preservation'],
};

const prettifyRole = (role) => String(role || 'software_engineer').replace(/[_-]+/g, ' ');

const collectSuitableRoles = (form) => {
  const fromExtra = Array.isArray(form?.extra_data?.suitable_roles)
    ? form.extra_data.suitable_roles.filter(Boolean)
    : [];
  if (fromExtra.length > 0) return fromExtra;
  if (form?.role) return [prettifyRole(form.role)];
  return ['software engineer'];
};

const collectWhatTaskTests = (form) => {
  const fromExtra = Array.isArray(form?.extra_data?.skills_tested)
    ? form.extra_data.skills_tested.filter(Boolean)
    : [];
  if (fromExtra.length > 0) return fromExtra;

  const rubricKeys = form?.evaluation_rubric && typeof form.evaluation_rubric === 'object'
    ? Object.keys(form.evaluation_rubric)
    : [];
  if (rubricKeys.length > 0) return rubricKeys.map((k) => String(k).replace(/[_-]+/g, ' '));

  return DEFAULT_TESTS_BY_TYPE[form?.task_type] || DEFAULT_TESTS_BY_TYPE.debugging;
};

const listRepoFiles = (form) => {
  const files = form?.repo_structure?.files;
  if (!files) return [];
  if (Array.isArray(files)) {
    return files
      .map((entry) => entry?.path || entry?.name || '')
      .filter(Boolean);
  }
  if (typeof files === 'object') return Object.keys(files);
  return [];
};

const buildTaskJsonPreview = (form) => ({
  task_id: form.task_key || null,
  name: form.name || '',
  role: form.role || null,
  duration_minutes: form.duration_minutes,
  claude_budget_limit_usd: form.claude_budget_limit_usd ?? null,
  scenario: form.scenario || null,
  repo_structure: form.repo_structure || null,
  evaluation_rubric: form.evaluation_rubric || null,
  expected_approaches: form.extra_data?.expected_approaches || null,
  suitable_roles: form.extra_data?.suitable_roles || null,
  skills_tested: form.extra_data?.skills_tested || null,
  extra_data: form.extra_data || null,
});

const TaskFormFields = ({ form, setForm, readOnly = false }) => {
  const noop = () => {};
  const upd = readOnly ? noop : setForm;
  const inputClass = (base) => `${base} ${readOnly ? 'bg-gray-100 cursor-default' : ''}`;
  return (
    <div className="space-y-4">
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Task Name *</label>
        <input
          type="text"
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
          placeholder="e.g. Async Pipeline Debugging"
          value={form.name}
          onChange={(e) => upd((p) => ({ ...p, name: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Description *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">What the candidate sees as the brief. Be specific about what they need to accomplish.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[80px]')}
          placeholder="Fix 3 bugs in an async data pipeline that processes streaming JSON events..."
          value={form.description}
          onChange={(e) => upd((p) => ({ ...p, description: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Type</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.task_type}
            onChange={(e) => upd((p) => ({ ...p, task_type: e.target.value }))}
            disabled={readOnly}
          >
            <option value="debugging">Debugging</option>
            <option value="ai_engineering">AI Engineering</option>
            <option value="optimization">Optimization</option>
            <option value="build">Build from Scratch</option>
            <option value="refactor">Refactoring</option>
          </select>
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Difficulty</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.difficulty}
            onChange={(e) => upd((p) => ({ ...p, difficulty: e.target.value }))}
            disabled={readOnly}
          >
            <option value="junior">Junior</option>
            <option value="mid">Mid-Level</option>
            <option value="senior">Senior</option>
            <option value="staff">Staff+</option>
          </select>
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Duration</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.duration_minutes}
            onChange={(e) => upd((p) => ({ ...p, duration_minutes: parseInt(e.target.value) }))}
            disabled={readOnly}
          >
            <option value={15}>15 min</option>
            <option value={30}>30 min</option>
            <option value={45}>45 min</option>
            <option value={60}>60 min</option>
            <option value={90}>90 min</option>
          </select>
        </div>
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Claude Budget Limit (USD)</label>
        <p className="font-mono text-xs text-gray-500 mb-1">
          Per-candidate Claude spend cap for this task. Leave blank for unlimited.
        </p>
        <input
          type="number"
          step="0.01"
          min="0.01"
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
          placeholder="e.g. 5.00"
          value={form.claude_budget_limit_usd ?? ''}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') {
              upd((p) => ({ ...p, claude_budget_limit_usd: null }));
              return;
            }
            const parsed = Number(raw);
            if (Number.isNaN(parsed)) return;
            upd((p) => ({ ...p, claude_budget_limit_usd: parsed }));
          }}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Primary Role</label>
          <input
            type="text"
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
            placeholder="e.g. backend_engineer"
            value={form.role || ''}
            onChange={(e) => upd((p) => ({ ...p, role: e.target.value }))}
            readOnly={readOnly}
            disabled={readOnly}
          />
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Task Key (optional)</label>
          <input
            type="text"
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
            placeholder="e.g. backend_async_pipeline_debug"
            value={form.task_key || ''}
            onChange={(e) => upd((p) => ({ ...p, task_key: e.target.value }))}
            readOnly={readOnly}
            disabled={readOnly}
          />
        </div>
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Scenario / Context</label>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[80px]')}
          placeholder="Describe why this task exists and what production context it simulates."
          value={form.scenario || ''}
          onChange={(e) => upd((p) => ({ ...p, scenario: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Starter Code *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">The code the candidate starts with. Include bugs, scaffolding, or an incomplete implementation.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[180px] bg-gray-50 leading-relaxed')}
          placeholder={"# Python starter code\n# Include realistic bugs or incomplete sections\n\ndef process_data(items):\n    ..."}
          value={form.starter_code}
          onChange={(e) => upd((p) => ({ ...p, starter_code: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Test Suite *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">pytest tests that validate the correct solution. These run automatically when the candidate submits.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[120px] bg-gray-50 leading-relaxed')}
          placeholder={"import pytest\n\ndef test_basic_case():\n    assert process_data([1, 2, 3]) == [2, 4, 6]\n\ndef test_edge_case():\n    assert process_data([]) == []"}
          value={form.test_code}
          onChange={(e) => upd((p) => ({ ...p, test_code: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
    </div>
  );
};

const CreateTaskModal = ({ onClose, onCreated, initialTask, onUpdated, viewOnly = false }) => {
  const isEdit = Boolean(initialTask) && !viewOnly;
  const resolvedInitialBudget = initialTask ? (initialTask.claude_budget_limit_usd ?? null) : STANDARD_MANUAL_TEMPLATE.claude_budget_limit_usd;
  const [step, setStep] = useState(initialTask ? 'manual' : 'ai-prompt');
  const [form, setForm] = useState({
    name: initialTask?.name ?? STANDARD_MANUAL_TEMPLATE.name,
    description: initialTask?.description ?? STANDARD_MANUAL_TEMPLATE.description,
    task_type: initialTask?.task_type ?? STANDARD_MANUAL_TEMPLATE.task_type,
    difficulty: initialTask?.difficulty ?? STANDARD_MANUAL_TEMPLATE.difficulty,
    duration_minutes: initialTask?.duration_minutes ?? STANDARD_MANUAL_TEMPLATE.duration_minutes,
    claude_budget_limit_usd: resolvedInitialBudget,
    starter_code: initialTask?.starter_code ?? STANDARD_MANUAL_TEMPLATE.starter_code,
    test_code: initialTask?.test_code ?? STANDARD_MANUAL_TEMPLATE.test_code,
    task_key: initialTask?.task_key ?? STANDARD_MANUAL_TEMPLATE.task_key,
    role: initialTask?.role ?? STANDARD_MANUAL_TEMPLATE.role,
    scenario: initialTask?.scenario ?? STANDARD_MANUAL_TEMPLATE.scenario,
    repo_structure: initialTask?.repo_structure ?? STANDARD_MANUAL_TEMPLATE.repo_structure,
    evaluation_rubric: initialTask?.evaluation_rubric ?? STANDARD_MANUAL_TEMPLATE.evaluation_rubric,
    extra_data: initialTask?.extra_data ?? STANDARD_MANUAL_TEMPLATE.extra_data,
    main_repo_path: initialTask?.main_repo_path ?? '',
    template_repo_url: initialTask?.template_repo_url ?? '',
    repo_file_count: initialTask?.repo_file_count ?? 0,
  });
  const [aiPrompt, setAiPrompt] = useState(STANDARD_AI_PROMPT_TEMPLATE);
  const [aiDifficulty, setAiDifficulty] = useState('');
  const [aiDuration, setAiDuration] = useState('');
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (initialTask) {
      setForm({
        name: initialTask.name ?? STANDARD_MANUAL_TEMPLATE.name,
        description: initialTask.description ?? STANDARD_MANUAL_TEMPLATE.description,
        task_type: initialTask.task_type ?? STANDARD_MANUAL_TEMPLATE.task_type,
        difficulty: initialTask.difficulty ?? STANDARD_MANUAL_TEMPLATE.difficulty,
        duration_minutes: initialTask.duration_minutes ?? STANDARD_MANUAL_TEMPLATE.duration_minutes,
        claude_budget_limit_usd: initialTask.claude_budget_limit_usd ?? null,
        starter_code: initialTask.starter_code ?? STANDARD_MANUAL_TEMPLATE.starter_code,
        test_code: initialTask.test_code ?? STANDARD_MANUAL_TEMPLATE.test_code,
        task_key: initialTask.task_key ?? STANDARD_MANUAL_TEMPLATE.task_key,
        role: initialTask.role ?? STANDARD_MANUAL_TEMPLATE.role,
        scenario: initialTask.scenario ?? STANDARD_MANUAL_TEMPLATE.scenario,
        repo_structure: initialTask.repo_structure ?? STANDARD_MANUAL_TEMPLATE.repo_structure,
        evaluation_rubric: initialTask.evaluation_rubric ?? STANDARD_MANUAL_TEMPLATE.evaluation_rubric,
        extra_data: initialTask.extra_data ?? STANDARD_MANUAL_TEMPLATE.extra_data,
        main_repo_path: initialTask.main_repo_path ?? '',
        template_repo_url: initialTask.template_repo_url ?? '',
        repo_file_count: initialTask.repo_file_count ?? 0,
      });
      setStep('manual');
    } else {
      setStep('ai-prompt');
      setAiPrompt(STANDARD_AI_PROMPT_TEMPLATE);
    }
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
    'manual': 'Create Task Manually',
  }[step];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-white border-2 border-black w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
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

          {/* Step: AI Prompt (create only) */}
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

          {/* Step: AI Review (create only) */}
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

          {/* Step: Manual (create or edit or view) */}
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

export const TasksPage = ({ onNavigate, NavComponent }) => {
  const [tasksList, setTasksList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingTask, setEditingTask] = useState(null);
  const [viewingTask, setViewingTask] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const fetchTasks = async () => {
      try {
        const res = await tasksApi.list();
        if (!cancelled) setTasksList(res.data || []);
      } catch (err) {
        console.warn('Failed to fetch tasks:', err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchTasks();
    return () => { cancelled = true; };
  }, []);

  const difficultyColors = {
    junior: '#22c55e',
    mid: '#FFAA00',
    senior: '#9D00FF',
    staff: '#FF0033',
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="tasks" onNavigate={onNavigate} /> : null}
      <div className="hidden md:block max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold">Tasks</h1>
            <p className="font-mono text-sm text-gray-600 mt-1">Manage assessment task templates</p>
          </div>
          <button
            className="border-2 border-black px-6 py-3 font-bold text-white hover:bg-black transition-colors flex items-center gap-2"
            style={{ backgroundColor: '#9D00FF' }}
            onClick={() => setShowCreateModal(true)}
          >
            <Code size={18} /> New Task
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-16 gap-3">
            <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
            <span className="font-mono text-sm text-gray-500">Loading tasks...</span>
          </div>
        ) : tasksList.length === 0 ? (
          <div className="border-2 border-black p-16 text-center">
            <Code size={48} className="mx-auto mb-4 text-gray-300" />
            <h3 className="text-xl font-bold mb-2">No tasks yet</h3>
            <p className="font-mono text-sm text-gray-500 mb-6">Create your first task template to start assessing candidates</p>
            <button
              className="border-2 border-black px-6 py-3 font-bold text-white hover:bg-black transition-colors"
              style={{ backgroundColor: '#9D00FF' }}
              onClick={() => setShowCreateModal(true)}
            >
              Create Task
            </button>
          </div>
        ) : (
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {tasksList.map((task) => (
              <div key={task.id} className="border-2 border-black p-6 hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow">
                <div className="flex items-center justify-between mb-3">
                  <span
                    className="px-3 py-1 text-xs font-mono font-bold text-white border-2 border-black"
                    style={{ backgroundColor: difficultyColors[task.difficulty] || '#9D00FF' }}
                  >
                    {task.difficulty?.toUpperCase()}
                  </span>
                  <span className="font-mono text-xs text-gray-500">{task.duration_minutes}min</span>
                </div>
                <h3 className="font-bold text-lg mb-2">{task.name}</h3>
                <p className="font-mono text-sm text-gray-600 mb-4 line-clamp-3">{task.description}</p>
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div className="flex flex-wrap gap-1">
                    <span className="font-mono text-xs px-2 py-1 border border-gray-300">{task.task_type?.replace('_', ' ')}</span>
                    {task.role && (
                      <span className="font-mono text-xs px-2 py-1 border border-gray-300 bg-gray-50">
                        {String(task.role).replace(/_/g, ' ')}
                      </span>
                    )}
                    {typeof task.repo_file_count === 'number' && (
                      <span className="font-mono text-xs px-2 py-1 border border-gray-300 bg-gray-50">{task.repo_file_count} files</span>
                    )}
                    {typeof task.claude_budget_limit_usd === 'number' && (
                      <span className="font-mono text-xs px-2 py-1 border border-amber-500 bg-amber-50">
                        ${task.claude_budget_limit_usd.toFixed(2)} Claude cap
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      className="border-2 border-black p-2 hover:bg-black hover:text-white transition-colors"
                      title="View task"
                      onClick={() => setViewingTask(task)}
                    >
                      <Eye size={14} />
                    </button>
                    {task.is_template && (
                      <span className="font-mono text-xs text-gray-400">template</span>
                    )}
                    {!task.is_template && (
                      <>
                        <button
                          type="button"
                          className="border-2 border-black p-2 hover:bg-black hover:text-white transition-colors"
                          title="Edit task"
                          onClick={() => setEditingTask(task)}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          type="button"
                          className="border-2 border-red-600 text-red-600 p-2 hover:bg-red-600 hover:text-white transition-colors disabled:opacity-50"
                          title="Delete task"
                          disabled={deletingId === task.id}
                          onClick={async () => {
                            if (!window.confirm(`Delete "${task.name}"? This cannot be undone.`)) return;
                            setDeletingId(task.id);
                            try {
                              await tasksApi.delete(task.id);
                              setTasksList((prev) => prev.filter((t) => t.id !== task.id));
                            } catch (err) {
                              alert(err.response?.data?.detail || 'Failed to delete task');
                            } finally {
                              setDeletingId(null);
                            }
                          }}
                        >
                          {deletingId === task.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showCreateModal && (
        <CreateTaskModal
          onClose={() => setShowCreateModal(false)}
          onCreated={(newTask) => {
            setTasksList((prev) => [newTask, ...prev]);
          }}
        />
      )}
      {editingTask && (
        <CreateTaskModal
          initialTask={editingTask}
          onClose={() => setEditingTask(null)}
          onUpdated={(taskId, updatedTask) => {
            setTasksList((prev) => prev.map((t) => (t.id === taskId ? updatedTask : t)));
          }}
        />
      )}
      {viewingTask && (
        <CreateTaskModal
          initialTask={viewingTask}
          viewOnly
          onClose={() => setViewingTask(null)}
        />
      )}
    </div>
  );
};
