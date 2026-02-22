import React, { useCallback, useEffect, useState } from 'react';

import { useToast } from '../../context/ToastContext';
import { tasks as tasksApi } from '../../shared/api';
import { CreateTaskModal } from './CreateTaskModal';
import { TasksListView } from './TasksListView';

const isTaskAuthoringEnabled = () => import.meta.env.VITE_TASK_AUTHORING_ENABLED !== 'false';

const getErrorMessage = (error, fallback) => (
  error?.response?.data?.detail
  || error?.message
  || fallback
);

export const TasksPage = ({ onNavigate, NavComponent }) => {
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
    fetchTasks();
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

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="tasks" onNavigate={onNavigate} /> : null}

      <TasksListView
        loading={loading}
        tasksList={tasksList}
        taskAuthoringEnabled={taskAuthoringEnabled}
        onCreateTask={() => openModal('create')}
        onViewTask={(task) => openModal('view', task)}
        onEditTask={(task) => openModal('edit', task)}
        onDeleteTask={handleDeleteTask}
      />

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
  );
};

