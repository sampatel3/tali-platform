import React from 'react';
import { Code, Eye, Pencil, Plus, Trash2 } from 'lucide-react';

import {
  Badge,
  Button,
  PageContainer,
  PageHeader,
  Panel,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';
import { ScoringGlossaryPanel, SCORING_GLOSSARY_METRIC_COUNT } from '../../shared/ui/ScoringGlossaryPanel';

const DIFFICULTY_LEVEL_CLASS = {
  junior: 'bg-[var(--taali-level-junior)] text-white border-transparent',
  mid: 'bg-[var(--taali-level-mid)] text-white border-transparent',
  senior: 'bg-[var(--taali-level-senior)] text-white border-transparent',
  staff: 'bg-[var(--taali-level-staff)] text-white border-transparent',
};

const TaskCard = ({ task, onViewTask, onEditTask, onDeleteTask, taskAuthoringEnabled }) => (
  <Panel key={task.id} as="div" className="p-6 hover:shadow-lg transition-shadow">
    <div className="flex items-center justify-between mb-3">
      <span
        className={[
          'rounded-full border px-3 py-1 text-xs font-mono font-bold',
          DIFFICULTY_LEVEL_CLASS[task.difficulty] || 'bg-[var(--taali-purple)] text-white border-transparent',
        ].join(' ')}
      >
        {task.difficulty?.toUpperCase() || 'MID'}
      </span>
      <span className="font-mono text-xs text-[var(--taali-muted)]">{task.duration_minutes}min</span>
    </div>
    <h3 className="font-bold text-lg mb-2 text-[var(--taali-text)]">{task.name}</h3>
    <p className="text-sm text-[var(--taali-muted)] mb-4 line-clamp-3">{task.description}</p>
    <div className="flex items-center justify-between flex-wrap gap-2">
      <div className="flex flex-wrap gap-1">
        <Badge variant="muted" className="font-mono">{task.task_type?.replace('_', ' ')}</Badge>
        {task.role ? (
          <Badge variant="muted" className="font-mono">{String(task.role).replace(/_/g, ' ')}</Badge>
        ) : null}
        {typeof task.repo_file_count === 'number' ? (
          <Badge variant="muted" className="font-mono">{task.repo_file_count} files</Badge>
        ) : null}
        {typeof task.claude_budget_limit_usd === 'number' ? (
          <Badge variant="warning" className="font-mono">${task.claude_budget_limit_usd.toFixed(2)} Claude cap</Badge>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          title="View task"
          onClick={() => onViewTask(task)}
        >
          <Eye size={14} />
        </Button>
        {!task.is_template && taskAuthoringEnabled ? (
          <>
            <Button
              variant="secondary"
              size="sm"
              title="Edit task"
              onClick={() => onEditTask(task)}
            >
              <Pencil size={14} />
            </Button>
            <Button
              variant="danger"
              size="sm"
              title="Delete task"
              onClick={() => onDeleteTask(task)}
            >
              <Trash2 size={14} />
            </Button>
          </>
        ) : null}
        {task.is_template ? (
          <span className="font-mono text-xs text-[var(--taali-muted)]">template</span>
        ) : null}
      </div>
    </div>
  </Panel>
);

export const TasksListView = ({
  loading,
  tasksList,
  onViewTask,
  onEditTask,
  onDeleteTask,
  onCreateTask,
  taskAuthoringEnabled,
}) => (
  <PageContainer density="compact" width="wide">
    <PageHeader
      density="compact"
      className="mb-6"
      title="Tasks"
      subtitle="Assessment task catalog"
      actions={taskAuthoringEnabled ? (
        <Button type="button" variant="primary" size="sm" onClick={onCreateTask}>
          <Plus size={14} />
          Create Task
        </Button>
      ) : (
        <span className="font-mono text-xs text-[var(--taali-muted)]">Task authoring is disabled.</span>
      )}
    />

    {loading ? (
      <div className="flex min-h-[260px] items-center justify-center">
        <Spinner size={32} />
      </div>
    ) : tasksList.length === 0 ? (
      <Panel className="px-6 py-16 text-center">
        <Code size={48} className="mx-auto mb-4 text-[var(--taali-border-muted)]" />
        <h3 className="mb-2 text-xl font-bold text-[var(--taali-text)]">No tasks available</h3>
        <p className="text-sm text-[var(--taali-muted)]">
          {taskAuthoringEnabled
            ? 'Create your first task to start evaluating candidates.'
            : 'Task authoring is disabled in this environment.'}
        </p>
      </Panel>
    ) : (
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
        {tasksList.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            onViewTask={onViewTask}
            onEditTask={onEditTask}
            onDeleteTask={onDeleteTask}
            taskAuthoringEnabled={taskAuthoringEnabled}
          />
        ))}
      </div>
    )}

    {!loading ? (
      <Panel as="div" className="mt-8 p-4">
        <details>
          <summary className="cursor-pointer font-mono text-xs text-[var(--taali-purple)] hover:underline">
            View TAALI scoring glossary ({SCORING_GLOSSARY_METRIC_COUNT} metrics) →
          </summary>
          <ScoringGlossaryPanel className="mt-3" />
        </details>
      </Panel>
    ) : null}
  </PageContainer>
);
