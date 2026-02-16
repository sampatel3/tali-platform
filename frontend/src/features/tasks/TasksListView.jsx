import React from 'react';
import {
  Code,
  Eye,
  Loader2,
} from 'lucide-react';

const difficultyColors = {
  junior: '#22c55e',
  mid: '#FFAA00',
  senior: '#9D00FF',
  staff: '#FF0033',
};

const TaskCard = ({ task, onViewTask }) => (
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
          onClick={() => onViewTask(task)}
        >
          <Eye size={14} />
        </button>
        {task.is_template && (
          <span className="font-mono text-xs text-gray-400">template</span>
        )}
      </div>
    </div>
  </div>
);

export const TasksListView = ({
  loading,
  tasksList,
  onViewTask,
}) => (
  <div className="hidden md:block max-w-7xl mx-auto px-6 py-8">
    <div className="flex items-center justify-between mb-8">
      <div>
        <h1 className="text-3xl font-bold">Tasks</h1>
        <p className="font-mono text-sm text-gray-600 mt-1">Backend-authored assessment task catalog</p>
      </div>
    </div>

    {loading ? (
      <div className="flex items-center justify-center py-16 gap-3">
        <Loader2 size={24} className="animate-spin" style={{ color: '#9D00FF' }} />
        <span className="font-mono text-sm text-gray-500">Loading tasks...</span>
      </div>
    ) : tasksList.length === 0 ? (
      <div className="border-2 border-black p-16 text-center">
        <Code size={48} className="mx-auto mb-4 text-gray-300" />
        <h3 className="text-xl font-bold mb-2">No tasks available</h3>
        <p className="font-mono text-sm text-gray-500 mb-6">Add task specs in the backend to populate this catalog.</p>
      </div>
    ) : (
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
        {tasksList.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            onViewTask={onViewTask}
          />
        ))}
      </div>
    )}
  </div>
);
