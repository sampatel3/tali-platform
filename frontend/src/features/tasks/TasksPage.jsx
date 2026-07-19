import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink, Lock, Search } from 'lucide-react';
import { useParams } from 'react-router-dom';

import '../../styles/09-standing-report.css';
import '../../styles/03-settings-agent.css';

import { useToast } from '../../context/ToastContext';
import { tasks as tasksApi } from '../../shared/api';
import { getErrorMessage } from '../../shared/getErrorMessage';
import { AgentHeader } from '../../shared/layout/AgentHeader';
import { Button, SegmentedControl, Select, Spinner } from '../../shared/ui/TaaliPrimitives';
import { GeneratedDraftsPanel } from './GeneratedDraftsPanel';

const AssessmentPage = lazy(() => import('../assessment_runtime/AssessmentPage'));
const TASK_PAGE_SIZE = 24;

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
      if (lower === 'llm') return 'AI';
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

const TaskLoadError = ({ message, onRetry }) => (
  <div
    className="tasks-empty-panel flex flex-wrap items-center justify-between gap-4"
    role="alert"
  >
    <div className="min-w-0">
      <strong className="block text-[var(--ink)]">We couldn’t load the task library.</strong>
      <p className="mt-1">{message}</p>
    </div>
    <Button type="button" variant="secondary" onClick={onRetry}>
      Retry
    </Button>
  </div>
);

export const TasksPage = ({ onNavigate, NavComponent = null }) => {
  const { showToast } = useToast();
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [difficultyFilter, setDifficultyFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [facets, setFacets] = useState({ roles: [], difficulties: [], taskTypes: [] });

  const requestRef = useRef(0);

  const loadTasks = useCallback(async ({ append = false, offset = 0 } = {}) => {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    if (append) setLoadingMore(true);
    else setLoading(true);
    setLoadError('');
    try {
      const res = await tasksApi.list({
        limit: TASK_PAGE_SIZE,
        offset,
        search: query.trim() || undefined,
        role: roleFilter === 'all' ? undefined : roleFilter,
        difficulty: difficultyFilter === 'all' ? undefined : difficultyFilter,
        task_type: typeFilter === 'all' ? undefined : typeFilter,
      });
      if (requestRef.current !== requestId) return;
      const page = Array.isArray(res?.data) ? res.data : [];
      setTasks((current) => {
        if (!append) return page;
        const seen = new Set(current.map((task) => Number(task.id)));
        return [...current, ...page.filter((task) => !seen.has(Number(task.id)))];
      });
      setHasMore(page.length >= TASK_PAGE_SIZE);
    } catch (error) {
      if (requestRef.current !== requestId) return;
      const message = getErrorMessage(error, 'Failed to load assessment tasks.');
      setLoadError(message);
      showToast(message, 'error');
    } finally {
      if (requestRef.current === requestId) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [difficultyFilter, query, roleFilter, showToast, typeFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadTasks(); }, query.trim() ? 200 : 0);
    return () => window.clearTimeout(timer);
  }, [loadTasks, query]);

  useEffect(() => {
    let active = true;
    const loadFacets = async () => {
      const collected = { roles: [], difficulties: [], taskTypes: [] };
      let offset = 0;
      do {
        const response = await tasksApi.facets({ limit: 100, offset });
        const page = response?.data || {};
        collected.roles.push(...(page.roles || []));
        collected.difficulties.push(...(page.difficulties || []));
        collected.taskTypes.push(...(page.task_types || []));
        offset = page.next_offset;
      } while (offset != null);
      if (active) setFacets(collected);
    };
    void loadFacets().catch(() => {});
    return () => { active = false; };
  }, []);

  const roleOptions = useMemo(() => (
    Array.from(new Set(facets.roles.length ? facets.roles : tasks.map(normalizeTaskRole))).sort()
  ), [facets.roles, tasks]);
  const difficultyOptions = useMemo(() => (
    Array.from(new Set(facets.difficulties.length ? facets.difficulties : tasks.map(normalizeDifficulty))).sort()
  ), [facets.difficulties, tasks]);
  const typeOptions = useMemo(() => (
    Array.from(new Set(facets.taskTypes.length ? facets.taskTypes : tasks.map(normalizeTaskType))).sort()
  ), [facets.taskTypes, tasks]);
  const roleSegmentOptions = useMemo(() => ([
    { value: 'all', label: 'All roles' },
    ...roleOptions.slice(0, 4).map((role) => ({
      value: role,
      label: formatDisplayLabel(role),
    })),
  ]), [roleOptions]);

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
      <AgentHeader
        breadcrumbs={[{ label: 'Tasks' }]}
        kicker="03 · TASKS"
        title={<>Task <em>catalogue</em></>}
        subtitle="Browse the assessment task library, preview the candidate workspace, and assign tasks to candidates from a role."
      />
      <div className="mc-page">

        <GeneratedDraftsPanel onNavigate={onNavigate} />

        <div className="tasks-toolbar">
          <SegmentedControl
            options={roleSegmentOptions}
            value={roleFilter}
            onChange={setRoleFilter}
            ariaLabel="Filter tasks by role"
            className="tasks-role-filter"
            density="compact"
          />
          <div className="tasks-toolbar-actions">
            <Select
              inline
              value={roleFilter}
              aria-label={`Role · ${roleFilter === 'all' ? 'All' : formatDisplayLabel(roleFilter)}`}
              onChange={(event) => setRoleFilter(event.target.value)}
            >
              <option value="all">Role · All</option>
              {roleOptions.map((option) => (
                <option key={option} value={option}>{formatDisplayLabel(option)}</option>
              ))}
            </Select>
            <Select inline value={difficultyFilter} onChange={(event) => setDifficultyFilter(event.target.value)}>
              <option value="all">Difficulty · All</option>
              {difficultyOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </Select>
            <Select inline value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
              <option value="all">Type · All</option>
              {typeOptions.map((option) => (
                <option key={option} value={option}>{option.replace(/_/g, ' ')}</option>
              ))}
            </Select>
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

        {loadError && tasks.length > 0 ? (
          <TaskLoadError message={loadError} onRetry={() => { void loadTasks(); }} />
        ) : null}

        {loading && tasks.length === 0 ? (
          <div className="flex min-h-[16.25rem] items-center justify-center">
            <Spinner size={32} label="Loading task library" />
          </div>
        ) : loadError && tasks.length === 0 ? (
          <TaskLoadError message={loadError} onRetry={() => { void loadTasks(); }} />
        ) : filteredTasks.length === 0 ? (
          tasks.length === 0 ? (
            <div className="tasks-empty-panel">No tasks in the library yet.</div>
          ) : (
            <div className="tasks-empty-panel">
              No tasks match your filters.{' '}
              <button
                type="button"
                className="taali-text-btn"
                onClick={() => {
                  setQuery('');
                  setRoleFilter('all');
                  setDifficultyFilter('all');
                  setTypeFilter('all');
                }}
              >
                Clear filters
              </button>
            </div>
          )
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
                          {/* Difficulty is a tier, not a status — use the purple
                              chip vocabulary (hard=purple, medium=ink, easy=neutral),
                              never traffic-light green/amber/red. */}
                          <span className={`chip ${difficulty === 'hard' ? 'purple' : difficulty === 'medium' ? 'ink' : ''}`}>
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

        {hasMore ? (
          <div className="mt-8 flex justify-center">
            <Button
              type="button"
              variant="secondary"
              onClick={() => { void loadTasks({ append: true, offset: tasks.length }); }}
              disabled={loadingMore}
            >
              {loadingMore ? 'Loading more…' : `Load more tasks (${tasks.length} shown)`}
            </Button>
          </div>
        ) : null}

        <aside className="tasks-bespoke-cta">
          <div className="tasks-bespoke-cta-body">
            <span className="tasks-bespoke-cta-icon"><Lock size={16} /></span>
            <div>
              <b>Don't see what you need?</b>
              <p>Request a bespoke assessment for your role — the team will build it.</p>
            </div>
          </div>
          <button
            type="button"
            className="tasks-bespoke-cta-btn"
            onClick={() => onNavigate?.('tasks-bespoke')}
          >
            Request a custom task →
          </button>
        </aside>
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
        const res = await tasksApi.get(decodedTaskId);
        if (!cancelled) {
          setTask(res?.data || null);
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
      <div className="flex min-h-screen items-center justify-center bg-[var(--bg)]">
        <Spinner size={32} />
      </div>
    );
  }

  if (!task) {
    return (
      <div className="min-h-screen bg-[var(--bg)] p-10 text-[var(--ink)]">
        <div className="mx-auto max-w-[47.5rem] rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
          <div className="kicker">Task preview</div>
          <h1 className="mt-3 font-[var(--font-display)] text-[2.125rem] font-semibold tracking-[-0.03em]">Task not found.</h1>
          <p className="mt-3 text-[var(--mute)]">This preview link is no longer valid — the task may have been renamed or removed.</p>
          {/* This page opens in a new tab (no history to go back through), so a
              plain anchor to the task library is the only working way out. */}
          <a href="/tasks" className="btn btn-purple btn-sm mt-4 inline-flex">Back to Tasks</a>
        </div>
      </div>
    );
  }

  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center bg-[var(--bg)]"><Spinner size={32} /></div>}>
      <AssessmentPage
        startData={buildPreviewStartData(task)}
        demoMode
        demoProfile={{ output: 'Preview mode: candidate execution output appears here.' }}
      />
    </Suspense>
  );
};

export default TasksPage;
