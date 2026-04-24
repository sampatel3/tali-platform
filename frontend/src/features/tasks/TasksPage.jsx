import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { useToast } from '../../context/ToastContext';
import { tasks as tasksApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';
import { CreateTaskModal } from './CreateTaskModal';

const isTaskAuthoringEnabled = () => import.meta.env.VITE_TASK_AUTHORING_ENABLED !== 'false';

const getErrorMessage = (error, fallback) => (
  error?.response?.data?.detail
  || error?.message
  || fallback
);

const difficultyTone = (difficulty) => {
  if (difficulty === 'senior' || difficulty === 'staff') return 'p1';
  if (difficulty === 'mid') return 'p2';
  return 'p3';
};

const difficultyLabel = (difficulty) => String(difficulty || 'junior').toUpperCase();

export const TasksPage = ({ onNavigate }) => {
  const { showToast } = useToast();
  const [tasksList, setTasksList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState('view');
  const [activeTask, setActiveTask] = useState(null);
  const [saving, setSaving] = useState(false);
  const [modalError, setModalError] = useState('');
  const taskAuthoringEnabled = isTaskAuthoringEnabled();

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await tasksApi.list();
      setTasksList(Array.isArray(res?.data) ? res.data : []);
    } catch (err) {
      setTasksList([]);
      showToast(getErrorMessage(err, 'Failed to fetch tasks.'), 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    void fetchTasks();
  }, [fetchTasks]);

  const openModal = (mode, task = null) => {
    setModalMode(mode);
    setActiveTask(task);
    setModalError('');
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setActiveTask(null);
    setModalError('');
    setSaving(false);
  };

  const handleSubmitTask = async (payload) => {
    setSaving(true);
    setModalError('');
    try {
      if (modalMode === 'create') {
        await tasksApi.create(payload);
        showToast('Task created.', 'success');
      } else if (modalMode === 'edit' && activeTask?.id) {
        await tasksApi.update(activeTask.id, payload);
        showToast('Task updated.', 'success');
      }
      await fetchTasks();
      closeModal();
    } catch (err) {
      setModalError(getErrorMessage(err, 'Failed to save task.'));
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteTask = async (task) => {
    if (!task?.id) return;
    if (!window.confirm(`Delete task "${task.name}"? This cannot be undone.`)) return;
    try {
      await tasksApi.delete(task.id);
      showToast('Task deleted.', 'success');
      await fetchTasks();
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to delete task.'), 'error');
    }
  };

  const groupedTasks = useMemo(() => ({
    custom: tasksList.filter((task) => !task?.is_template),
    templates: tasksList.filter((task) => task?.is_template),
  }), [tasksList]);

  const summary = useMemo(() => ({
    total: tasksList.length,
    templates: groupedTasks.templates.length,
    custom: groupedTasks.custom.length,
    senior: tasksList.filter((task) => ['senior', 'staff'].includes(String(task?.difficulty))).length,
  }), [groupedTasks.custom.length, groupedTasks.templates.length, tasksList]);

  const renderTaskGroup = (title, label, items) => (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-sm)]">
      <div className="flex items-center justify-between border-b border-[var(--line)] bg-[var(--bg-3)] px-6 py-4">
        <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.12em] text-[var(--purple)]">{label}</div>
        <div className="font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">{items.length} TASKS</div>
      </div>
      {items.length === 0 ? (
        <div className="px-6 py-6 text-sm text-[var(--mute)]">No tasks in this group yet.</div>
      ) : (
        items.map((task) => (
          <div key={task.id} className="grid gap-4 border-b border-[var(--line-2)] px-6 py-4 last:border-b-0 md:grid-cols-[1fr_auto_auto] md:items-center">
            <div>
              <div className="text-[14px] font-medium tracking-[-0.01em]">{task.name}</div>
              <div className="mt-1 text-[12.5px] leading-6 text-[var(--mute)]">
                {task.description || 'No description yet.'}
              </div>
              <div className="mt-2 font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
                {task.task_type || 'general'} · {task.duration_minutes || 30}min
              </div>
            </div>
            <span className={`t-pill ${difficultyTone(String(task?.difficulty || 'junior'))}`}>
              {difficultyLabel(task?.difficulty)}
            </span>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="btn btn-outline btn-sm" onClick={() => openModal('view', task)}>View</button>
              {taskAuthoringEnabled ? (
                <>
                  <button type="button" className="btn btn-outline btn-sm" onClick={() => openModal('edit', task)}>Edit</button>
                  <button type="button" className="btn btn-outline btn-sm" onClick={() => handleDeleteTask(task)}>Delete</button>
                </>
              ) : null}
            </div>
          </div>
        ))
      )}
    </div>
  );

  return (
    <AppShell currentPage="tasks" onNavigate={onNavigate}>
      <div className="page">
        <div className="relative mb-5 overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-8 py-8 shadow-[var(--shadow-sm)]">
          <div className="kicker">YOUR TASKS · TASK LIBRARY</div>
          <div className="mt-3 grid gap-6 lg:grid-cols-[1fr_auto] lg:items-end">
            <div>
              <h1 className="font-[var(--font-display)] text-[44px] font-semibold leading-none tracking-[-0.04em]">
                What needs <em>you</em>, today.
              </h1>
              <p className="mt-3 max-w-[540px] text-[14.5px] leading-7 text-[var(--mute)]">
                Review, create, and maintain the technical tasks your team uses in assessments.
              </p>
            </div>
            <div className="flex gap-7">
              <div>
                <div className="text-[28px] font-semibold tracking-[-0.02em] text-[var(--red)]">{summary.total}</div>
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">Total</div>
              </div>
              <div>
                <div className="text-[28px] font-semibold tracking-[-0.02em] text-[var(--purple)]">{summary.custom}</div>
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">Custom</div>
              </div>
              <div>
                <div className="text-[28px] font-semibold tracking-[-0.02em]">{summary.templates}</div>
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">Templates</div>
              </div>
              <div>
                <div className="text-[28px] font-semibold tracking-[-0.02em]">{summary.senior}</div>
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">Senior+</div>
              </div>
            </div>
          </div>
        </div>

        <div className="grid gap-5 xl:grid-cols-[1fr_320px]">
          <div className="space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="app-tabs">
                <button type="button" className="app-tab active">All</button>
                <button type="button" className="app-tab">Custom</button>
                <button type="button" className="app-tab">Templates</button>
              </div>
              <button type="button" className="btn btn-purple btn-sm" onClick={() => openModal('create')} disabled={!taskAuthoringEnabled}>
                + New task
              </button>
            </div>

            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 6 }).map((_, index) => (
                  <div key={index} className="h-20 animate-pulse rounded-[var(--radius)] bg-[var(--bg-2)]" />
                ))}
              </div>
            ) : tasksList.length === 0 ? (
              <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-10 text-center shadow-[var(--shadow-sm)]">
                <h2 className="font-[var(--font-display)] text-[34px] tracking-[-0.03em]">No tasks available</h2>
                <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">Create your first task to start evaluating candidates.</p>
              </div>
            ) : (
              <>
                {renderTaskGroup('Custom tasks', '● Custom tasks', groupedTasks.custom)}
                {renderTaskGroup('Template tasks', '● Template tasks', groupedTasks.templates)}
              </>
            )}
          </div>

          <div className="space-y-5">
            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
              <h3 className="font-[var(--font-display)] text-[18px] font-semibold tracking-[-0.02em]">Task <em>templates</em></h3>
              <p className="mt-1 text-[12.5px] text-[var(--mute)]">Start from common assessment formats, then tune them to the role.</p>
              <div className="mt-4 space-y-2">
                {tasksList.slice(0, 4).map((task) => (
                  <button key={task.id} type="button" className="flex w-full items-center justify-between rounded-[10px] border border-[var(--line-2)] px-4 py-3 text-left transition hover:border-[var(--purple)]" onClick={() => openModal('view', task)}>
                    <div>
                      <div className="text-[13.5px] font-medium">{task.name}</div>
                      <div className="mt-1 text-[11.5px] text-[var(--mute)]">{task.task_type || 'general'} · {task.duration_minutes || 30}min</div>
                    </div>
                    <span className="font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">→</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
              <h3 className="font-[var(--font-display)] text-[18px] font-semibold tracking-[-0.02em]">Coverage <em>snapshot</em></h3>
              <p className="mt-1 text-[12.5px] text-[var(--mute)]">A quick view of the current task mix.</p>
              <div className="mt-4 space-y-4">
                {[
                  ['AI engineering', tasksList.filter((task) => task.task_type === 'ai_engineering').length],
                  ['Debugging', tasksList.filter((task) => task.task_type === 'debugging').length],
                  ['Optimization', tasksList.filter((task) => task.task_type === 'optimization').length],
                ].map(([label, count]) => (
                  <div key={label}>
                    <div className="mb-1 flex items-center justify-between text-[13px]">
                      <span>{label}</span>
                      <span className="font-[var(--font-mono)] text-[var(--mute)]">{count}</span>
                    </div>
                    <div className="bar"><i style={{ width: `${tasksList.length ? (Number(count) / tasksList.length) * 100 : 0}%` }} /></div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {modalOpen ? (
          <CreateTaskModal
            initialTask={activeTask}
            mode={modalMode}
            taskAuthoringEnabled={taskAuthoringEnabled}
            saving={saving}
            error={modalError}
            onSubmit={handleSubmitTask}
            onClose={closeModal}
          />
        ) : null}
      </div>
    </AppShell>
  );
};

export default TasksPage;
