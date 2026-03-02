import React from 'react';
import { Code, Eye, Pencil, Plus, Trash2 } from 'lucide-react';

import { Badge, Button, PageContainer, PageHeader, Panel } from '../../shared/ui/TaaliPrimitives';
import { CardSkeleton } from '../../shared/ui/Skeletons';
import { ScoringGlossaryPanel, SCORING_GLOSSARY_METRIC_COUNT } from '../../shared/ui/ScoringGlossaryPanel';

const DIFFICULTY_LEVEL_CLASS = {
  junior: 'bg-[var(--taali-level-junior)] text-white border-[var(--taali-border)]',
  mid: 'bg-[var(--taali-level-mid)] text-white border-[var(--taali-border)]',
  senior: 'bg-[var(--taali-level-senior)] text-white border-[var(--taali-border)]',
  staff: 'bg-[var(--taali-level-staff)] text-white border-[var(--taali-border)]',
};

const TaskCard = ({ task, onViewTask, onEditTask, onDeleteTask, taskAuthoringEnabled }) => (
  <Panel key={task.id} as="div" className="p-4 transition-shadow hover:shadow-md">
    <div className="mb-2 flex items-center justify-between gap-3">
      <span
        className={[
          'border-2 px-2.5 py-1 font-mono text-[11px] font-bold tracking-wide',
          DIFFICULTY_LEVEL_CLASS[task.difficulty] || 'bg-[var(--taali-purple)] text-white border-[var(--taali-border)]',
        ].join(' ')}
      >
        {task.difficulty?.toUpperCase() || 'MID'}
      </span>
      <span className="font-mono text-xs text-[var(--taali-muted)]">{task.duration_minutes}min</span>
    </div>
    <h3 className="mb-1.5 text-base font-bold leading-snug text-[var(--taali-text)]">{task.name}</h3>
    <p className="mb-3 line-clamp-3 text-[13px] leading-5 text-[var(--taali-muted)]">{task.description}</p>
    <div className="flex flex-wrap items-end justify-between gap-2">
      <div className="flex flex-wrap gap-1.5">
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
      <div className="flex items-center gap-1.5">
        <Button
          variant="secondary"
          size="xs"
          title="View task"
          onClick={() => onViewTask(task)}
        >
          <Eye size={14} />
        </Button>
        {!task.is_template && taskAuthoringEnabled ? (
          <>
            <Button
              variant="secondary"
              size="xs"
              title="Edit task"
              onClick={() => onEditTask(task)}
            >
              <Pencil size={14} />
            </Button>
            <Button
              variant="danger"
              size="xs"
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
      title="Tasks"
      subtitle="Assessment task catalog"
      density="compact"
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
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <CardSkeleton key={`tasks-skeleton-${index}`} lines={3} />
        ))}
      </div>
    ) : tasksList.length === 0 ? (
      <div className="taali-empty-state border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] px-5 py-10 text-center">
        <Code size={42} className="mx-auto mb-3 text-[var(--taali-border-muted)]" />
        <h3 className="mb-2 text-lg font-bold text-[var(--taali-text)]">No tasks available</h3>
        <p className="text-sm text-[var(--taali-muted)]">
          {taskAuthoringEnabled
            ? 'Create your first task to start evaluating candidates.'
            : 'Task authoring is disabled in this environment.'}
        </p>
      </div>
    ) : (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
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
      <Panel as="div" className="mt-6 p-3">
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
