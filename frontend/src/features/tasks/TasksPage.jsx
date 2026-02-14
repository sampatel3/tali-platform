import React, { useEffect, useState } from 'react';

import { tasks as tasksApi } from '../../shared/api';
import { CreateTaskModal } from './CreateTaskModal';
import { TasksListView } from './TasksListView';

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
        if (!cancelled) {
          setTasksList(res.data || []);
        }
      } catch (err) {
        console.warn('Failed to fetch tasks:', err.message);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchTasks();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleDeleteTask = async (task) => {
    if (!window.confirm(`Delete "${task.name}"? This cannot be undone.`)) {
      return;
    }

    setDeletingId(task.id);
    try {
      await tasksApi.delete(task.id);
      setTasksList((prev) => prev.filter((candidateTask) => candidateTask.id !== task.id));
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to delete task');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="tasks" onNavigate={onNavigate} /> : null}

      <TasksListView
        loading={loading}
        tasksList={tasksList}
        deletingId={deletingId}
        onCreateTask={() => setShowCreateModal(true)}
        onViewTask={setViewingTask}
        onEditTask={setEditingTask}
        onDeleteTask={handleDeleteTask}
      />

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
            setTasksList((prev) => prev.map((task) => (task.id === taskId ? updatedTask : task)));
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
