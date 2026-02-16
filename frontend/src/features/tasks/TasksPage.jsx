import React, { useEffect, useState } from 'react';

import { tasks as tasksApi } from '../../shared/api';
import { CreateTaskModal } from './CreateTaskModal';
import { TasksListView } from './TasksListView';

export const TasksPage = ({ onNavigate, NavComponent }) => {
  const [tasksList, setTasksList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [viewingTask, setViewingTask] = useState(null);

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

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="tasks" onNavigate={onNavigate} /> : null}

      <TasksListView
        loading={loading}
        tasksList={tasksList}
        onViewTask={setViewingTask}
      />

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
