import React, { Suspense, lazy, useCallback, useEffect, useMemo, useState } from 'react';
import { ExternalLink, Lock, Search } from 'lucide-react';
import { useParams } from 'react-router-dom';

import { useToast } from '../../context/ToastContext';
import { tasks as tasksApi } from '../../shared/api';
import { CardSkeleton } from '../../shared/ui/Skeletons';

const AssessmentPage = lazy(() => import('../assessment_runtime/AssessmentPage'));

const getErrorMessage = (error, fallback) => (
  error?.response?.data?.detail
  || error?.message
  || fallback
);

const normalizeTaskRole = (task) => (
  String(task?.role || task?.role_name || task?.category || 'General engineering').trim()
);

const formatDisplayLabel = (value) => {
  const normalized = String(value || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!normalized) return 'General engineering';
  return normalized
    .split(' ')
    .map((part) => {
      const lower = part.toLowerCase();
      if (lower === 'ai') return 'AI';
      if (lower === 'aws') return 'AWS';
      if (lower === 'api') return 'API';
      if (lower === 'llm') return 'LLM';
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(' ');
};

const normalizeDifficulty = (task) => (
  String(task?.difficulty || task?.seniority || 'medium').trim().toLowerCase()
);

const normalizeTaskType = (task) => (
  String(task?.task_type || task?.type || task?.ai_mode || 'repo').trim().toLowerCase()
);

const repoFilesFromTask = (task) => {
  const files = task?.repo_structure?.files;
  if (Array.isArray(files)) {
    return files.map((file) => ({
      path: file.path || file.name || 'README.md',
      content: String(file.content || ''),
    }));
  }
  if (files && typeof files === 'object') {
    return Object.entries(files).map(([path, content]) => ({
      path,
      content: String(content || ''),
    }));
  }
  return [];
};

const buildPreviewStartData = (task) => {
  const durationMinutes = Number(task?.duration_minutes || task?.duration || 30);
  return {
    assessment_id: `task-preview-${task?.id || task?.task_key || 'demo'}`,
    token: null,
    candidate_name: 'Demo candidate',
    organization_name: 'Taali task preview',
    time_remaining: durationMinutes * 60,
    task: {
      ...task,
      name: task?.name || 'Assessment task',
      role: formatDisplayLabel(normalizeTaskRole(task)),
      duration_minutes: durationMinutes,
      description: task?.description || '',
      scenario: task?.scenario || task?.description || '',
      repo_structure: task?.repo_structure || { files: repoFilesFromTask(task) },
      ai_mode: task?.ai_mode || 'claude_cli_terminal',
    },
    claude_budget: task?.claude_budget || {
      enabled: Boolean(task?.claude_budget_limit_usd),
      remaining_usd: Number(task?.claude_budget_limit_usd || 0),
      limit_usd: Number(task?.claude_budget_limit_usd || 0),
    },
  };
};

const taskSearchText = (task) => [
  task?.name,
  task?.task_key,
  task?.description,
  task?.scenario,
  normalizeTaskRole(task),
  normalizeDifficulty(task),
  normalizeTaskType(task),
].join(' ').toLowerCase();

export const TasksPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [difficultyFilter, setDifficultyFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await tasksApi.list();
      setTasks(Array.isArray(res?.data) ? res.data : []);
    } catch (error) {
      setTasks([]);
      showToast(getErrorMessage(error, 'Failed to load assessment tasks.'), 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  const roleOptions = useMemo(() => (
    Array.from(new Set(tasks.map(normalizeTaskRole))).sort()
  ), [tasks]);
  const difficultyOptions = useMemo(() => (
    Array.from(new Set(tasks.map(normalizeDifficulty))).sort()
  ), [tasks]);
  const typeOptions = useMemo(() => (
    Array.from(new Set(tasks.map(normalizeTaskType))).sort()
  ), [tasks]);

  const filteredTasks = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks.filter((task) => {
      if (roleFilter !== 'all' && normalizeTaskRole(task) !== roleFilter) return false;
      if (difficultyFilter !== 'all' && normalizeDifficulty(task) !== difficultyFilter) return false;
      if (typeFilter !== 'all' && normalizeTaskType(task) !== typeFilter) return false;
      if (!q) return true;
      return taskSearchText(task).includes(q);
    });
  }, [difficultyFilter, query, roleFilter, tasks, typeFilter]);

  const groupedTasks = useMemo(() => {
    const groups = new Map();
    filteredTasks.forEach((task) => {
      const role = normalizeTaskRole(task);
      if (!groups.has(role)) groups.set(role, []);
      groups.get(role).push(task);
    });
    return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [filteredTasks]);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="tasks" onNavigate={onNavigate} /> : null}
      <div className="page">
        <div className="tasks-hero">
          <div>
            <div className="kicker">TASK LIBRARY · ENGINEERING BUILT</div>
            <h1>
              Tasks the engineering team built for <em>you</em>.
            </h1>
            <p>Browse read-only assessment tasks, preview the candidate workspace, and use them when assigning candidates from jobs.</p>
          </div>
          <div className="eng-badge">
            <span className="ico"><Lock size={14} /></span>
            <span className="t"><b>Read-only catalog</b><br /><span>Task source stays with engineering</span></span>
          </div>
        </div>

        <div className="tasks-toolbar">
          <div className="seg">
            <button type="button" className={roleFilter === 'all' ? 'active on' : ''} onClick={() => setRoleFilter('all')}>
              All roles
            </button>
            {roleOptions.slice(0, 4).map((role) => (
              <button key={role} type="button" className={roleFilter === role ? 'active on' : ''} onClick={() => setRoleFilter(role)}>
                {formatDisplayLabel(role)}
              </button>
            ))}
          </div>
          <div className="tasks-toolbar-actions">
            <select value={difficultyFilter} onChange={(event) => setDifficultyFilter(event.target.value)}>
              <option value="all">Difficulty · All</option>
              {difficultyOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
            <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
              <option value="all">Type · All</option>
              {typeOptions.map((option) => (
                <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>
              ))}
            </select>
            <label className="relative flex-1">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--mute)]" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search tasks, stacks, or scenarios"
                className="pl-9"
              />
            </label>
          </div>
        </div>

        {loading ? (
          <div className="tasks-loading-grid">
            {Array.from({ length: 4 }).map((_, index) => (
              <CardSkeleton key={`task-library-${index}`} lines={4} />
            ))}
          </div>
        ) : filteredTasks.length === 0 ? (
          <div className="tasks-empty-panel">No tasks available</div>
        ) : (
          <div className="space-y-9">
            {groupedTasks.map(([role, items]) => (
              <section key={role} className="role-group">
                <div className="role-head">
                  <h2>{formatDisplayLabel(role)} <em>tasks</em></h2>
                  <div className="role-meta"><b>{items.length}</b> tasks</div>
                </div>
                <div className="tgrid">
                  {items.map((task) => {
                    const difficulty = normalizeDifficulty(task);
                    const taskType = normalizeTaskType(task);
                    const repoFiles = repoFilesFromTask(task);
                    const rubricCount = Array.isArray(task?.rubric_categories)
                      ? task.rubric_categories.length
                      : Object.keys(task?.evaluation_rubric || {}).length;
                    return (
                      <article key={task.id || task.task_key || task.name} className="tcard">
                        <div className="tcard-head">
                          <div>
                            <div className="tcard-key"><Lock size={12} />Secure assessment task</div>
                            <h3>{task.name || 'Assessment task'}</h3>
                          </div>
                          <span className={`chip ${difficulty === 'easy' ? 'green' : difficulty === 'hard' ? 'red' : 'amber'}`}>
                            {difficulty}
                          </span>
                        </div>
                        <p className="desc">
                          {task.description || String(task.scenario || '').slice(0, 190) || 'Preview the candidate workspace for this assessment task.'}
                        </p>
                        <div className="tcard-stats">
                          <div className="tcard-stat"><div className="k">Duration</div><div className="v">{task.duration_minutes || 60}m</div></div>
                          <div className="tcard-stat"><div className="k">Type</div><div className="v">{taskType.replace(/_/g, ' ')}</div></div>
                          <div className="tcard-stat"><div className="k">Files</div><div className="v">{repoFiles.length || '—'}</div></div>
                          <div className="tcard-stat"><div className="k">Rubric</div><div className="v">{rubricCount || '—'}</div></div>
                        </div>
                        <div className="tcard-foot">
                          <div className="meta"><span><b>{formatDisplayLabel(normalizeTaskRole(task))}</b></span></div>
                          <a
                            className="tcard-cta"
                            href={`/tasks/${encodeURIComponent(task.id || task.task_key || task.name)}/preview`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Preview as candidate <ExternalLink size={13} />
                          </a>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export const TaskPreviewPage = () => {
  const { taskId } = useParams();
  const { showToast } = useToast();
  const [task, setTask] = useState(null);
  const [loading, setLoading] = useState(true);
  const decodedTaskId = decodeURIComponent(String(taskId || ''));

  useEffect(() => {
    let cancelled = false;
    const loadPreviewTask = async () => {
      setLoading(true);
      try {
        const res = await tasksApi.list();
        const items = Array.isArray(res?.data) ? res.data : [];
        const found = items.find((item) => (
          String(item?.id || '') === decodedTaskId
          || String(item?.task_key || '') === decodedTaskId
          || String(item?.name || '') === decodedTaskId
        ));
        if (!cancelled) {
          setTask(found || null);
        }
      } catch (error) {
        if (!cancelled) {
          showToast(getErrorMessage(error, 'Failed to load task preview.'), 'error');
          setTask(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void loadPreviewTask();
    return () => {
      cancelled = true;
    };
  }, [decodedTaskId, showToast]);

  if (loading) {
    return (
      <div className="min-h-screen bg-[var(--bg)] p-10">
        <CardSkeleton lines={5} />
      </div>
    );
  }

  if (!task) {
    return (
      <div className="min-h-screen bg-[var(--bg)] p-10 text-[var(--ink)]">
        <div className="mx-auto max-w-[760px] rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
          <div className="kicker">Task preview</div>
          <h1 className="mt-3 font-[var(--font-display)] text-[34px] font-semibold tracking-[-0.03em]">Task not found.</h1>
          <p className="mt-3 text-[var(--mute)]">Return to the task library and open a current task preview.</p>
        </div>
      </div>
    );
  }

  return (
    <Suspense fallback={<div className="min-h-screen bg-[var(--bg)] p-10"><CardSkeleton lines={5} /></div>}>
      <AssessmentPage
        startData={buildPreviewStartData(task)}
        demoMode
        demoProfile={{ output: 'Preview mode: candidate execution output appears here.' }}
      />
    </Suspense>
  );
};

export default TasksPage;
