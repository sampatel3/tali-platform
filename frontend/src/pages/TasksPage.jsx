import React, { useState, useEffect } from 'react';
import {
  ArrowLeft,
  X,
  AlertTriangle,
  Bot,
  Loader2,
  Zap,
  Check,
  FileText,
  Code,
  Plus,
  Eye,
  Pencil,
  Trash2,
} from 'lucide-react';
import { tasks as tasksApi } from '../lib/api';


const buildTaskJsonPreview = (form) => ({
  task_id: form.task_key || null,
  name: form.name || '',
  role: form.role || null,
  duration_minutes: form.duration_minutes,
  scenario: form.scenario || null,
  repo_structure: form.repo_structure || null,
  evaluation_rubric: form.evaluation_rubric || null,
  expected_approaches: form.extra_data?.expected_approaches || null,
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
  const [step, setStep] = useState(initialTask ? 'manual' : 'choose');
  const [form, setForm] = useState({
    name: initialTask?.name ?? '',
    description: initialTask?.description ?? '',
    task_type: initialTask?.task_type ?? 'debugging',
    difficulty: initialTask?.difficulty ?? 'mid',
    duration_minutes: initialTask?.duration_minutes ?? 30,
    starter_code: initialTask?.starter_code ?? '',
    test_code: initialTask?.test_code ?? '',
    task_key: initialTask?.task_key ?? '',
    role: initialTask?.role ?? '',
    scenario: initialTask?.scenario ?? '',
    repo_structure: initialTask?.repo_structure ?? null,
    evaluation_rubric: initialTask?.evaluation_rubric ?? null,
    extra_data: initialTask?.extra_data ?? null,
  });
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiDifficulty, setAiDifficulty] = useState('');
  const [aiDuration, setAiDuration] = useState('');
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (initialTask) {
      setForm({
        name: initialTask.name ?? '',
        description: initialTask.description ?? '',
        task_type: initialTask.task_type ?? 'debugging',
        difficulty: initialTask.difficulty ?? 'mid',
        duration_minutes: initialTask.duration_minutes ?? 30,
        starter_code: initialTask.starter_code ?? '',
        test_code: initialTask.test_code ?? '',
        task_key: initialTask.task_key ?? '',
        role: initialTask.role ?? '',
        scenario: initialTask.scenario ?? '',
        repo_structure: initialTask.repo_structure ?? null,
        evaluation_rubric: initialTask.evaluation_rubric ?? null,
        extra_data: initialTask.extra_data ?? null,
      });
      setStep('manual');
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
      setForm(res.data);
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

  const modalTitle = viewOnly ? 'View Task' : isEdit ? 'Edit Task' : {
    'choose': 'Create New Task',
    'ai-prompt': 'Generate with AI',
    'ai-review': 'Review Generated Task',
    'manual': 'Create Task Manually',
  }[step];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-white border-2 border-black w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-8 py-5 border-b-2 border-black">
          <div className="flex items-center gap-3">
            {!viewOnly && !isEdit && step !== 'choose' && (
              <button
                className="border-2 border-black p-1 hover:bg-black hover:text-white transition-colors"
                onClick={() => setStep(step === 'ai-review' ? 'ai-prompt' : 'choose')}
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

          {/* Step: Choose Path (skip when editing) */}
          {!isEdit && step === 'choose' && (
            <div className="space-y-4">
              <p className="font-mono text-sm text-gray-600 mb-6">How would you like to create your assessment task?</p>
              <button
                className="w-full border-2 border-black p-6 text-left hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow group"
                onClick={() => setStep('ai-prompt')}
              >
                <div className="flex items-start gap-4">
                  <div
                    className="w-12 h-12 border-2 border-black flex items-center justify-center shrink-0"
                    style={{ backgroundColor: '#9D00FF' }}
                  >
                    <Bot size={24} className="text-white" />
                  </div>
                  <div>
                    <h3 className="font-bold text-lg mb-1">Generate with AI</h3>
                    <p className="font-mono text-sm text-gray-600">
                      Describe what you want to assess in plain English. Claude will generate the full task including starter code, bugs, and test suite.
                    </p>
                    <p className="font-mono text-xs mt-2" style={{ color: '#9D00FF' }}>
                      Recommended for quick setup
                    </p>
                  </div>
                </div>
              </button>
              <button
                className="w-full border-2 border-black p-6 text-left hover:shadow-[4px_4px_0px_0px_rgba(0,0,0,1)] transition-shadow group"
                onClick={() => setStep('manual')}
              >
                <div className="flex items-start gap-4">
                  <div className="w-12 h-12 border-2 border-black flex items-center justify-center bg-black shrink-0">
                    <FileText size={24} className="text-white" />
                  </div>
                  <div>
                    <h3 className="font-bold text-lg mb-1">Create Manually</h3>
                    <p className="font-mono text-sm text-gray-600">
                      Write your own task from scratch. Full control over the description, starter code, test suite, and all parameters.
                    </p>
                    <p className="font-mono text-xs text-gray-400 mt-2">
                      Best for specific, custom assessments
                    </p>
                  </div>
                </div>
              </button>
            </div>
          )}

          {/* Step: AI Prompt (create only) */}
          {!isEdit && step === 'ai-prompt' && (
            <div className="space-y-5">
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
                  <span className="font-mono text-xs px-2 py-1 border border-gray-300">{task.task_type?.replace('_', ' ')}</span>
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

