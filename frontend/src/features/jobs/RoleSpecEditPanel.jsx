import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';

import { Button, Input, Textarea, cx } from '../../shared/ui/TaaliPrimitives';

// The effective spec text the read-view renders: job_spec_text first, then the
// short description. Editing must target the SAME field, or edits don't show.
const effectiveSpec = (role) => role?.job_spec_text || role?.description || '';

// Direct, document-style editor for the Job Specification tab. One big spec
// field (no cramped form, no file upload) + the role name, with tasks tucked
// into a collapsed section so the spec is the focus. Save calls
// onSubmit({ name, specText, taskIds }).
export const RoleSpecEditPanel = ({ role, roleTasks, allTasks = [], saving, error, onSubmit, onCancel }) => {
  const [name, setName] = useState(role?.name || '');
  const [specText, setSpecText] = useState(effectiveSpec(role));
  const [selectedTaskIds, setSelectedTaskIds] = useState([]);
  const [nameTouched, setNameTouched] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);

  useEffect(() => {
    setName(role?.name || '');
    setSpecText(effectiveSpec(role));
    setSelectedTaskIds((roleTasks || []).map((task) => Number(task.id)));
    setNameTouched(false);
  }, [role?.id, role?.name, role?.description, role?.job_spec_text, roleTasks]);

  const hasValidName = name.trim().length > 0;
  const initialTaskIds = useMemo(() => (roleTasks || []).map((t) => Number(t.id)), [roleTasks]);

  const dirty = (
    name.trim() !== (role?.name || '').trim()
    || specText !== effectiveSpec(role)
    || !sameIds(selectedTaskIds, initialTaskIds)
  );

  const toggleTask = (taskId) => setSelectedTaskIds((prev) => (
    prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]
  ));

  const handleSave = () => {
    setNameTouched(true);
    if (!hasValidName || saving) return;
    onSubmit({ name: name.trim(), specText, taskIds: selectedTaskIds });
  };

  return (
    <div className="role-edit-inline space-y-5">
      {error ? (
        <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">
          {error}
        </div>
      ) : null}

      <label className="block">
        <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">Role name</span>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => setNameTouched(true)}
          placeholder="e.g. Senior Backend Engineer"
          className={!hasValidName && nameTouched ? '!border-[var(--taali-danger)] !bg-[var(--taali-danger-soft)]' : ''}
        />
        {!hasValidName && nameTouched ? (
          <span className="mt-1 block text-xs text-[var(--taali-danger)]">Role name is required.</span>
        ) : null}
      </label>

      <label className="block">
        <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">Job specification</span>
        <span className="mb-2 block text-xs text-[var(--taali-muted)]">
          Edit the full spec directly. This is what candidates are scored against and what appears on the role.
        </span>
        <Textarea
          value={specText}
          onChange={(e) => setSpecText(e.target.value)}
          placeholder="Paste or write the job specification…"
          className="min-h-[60vh] font-mono text-[13px] leading-relaxed"
        />
      </label>

      {/* Tasks — secondary, collapsed by default so the spec stays the focus. */}
      <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-muted)]">
        <button
          type="button"
          className="flex w-full items-center justify-between px-3 py-2.5 text-left"
          onClick={() => setTasksOpen((v) => !v)}
        >
          <span className="text-sm font-medium text-[var(--taali-text)]">
            Assessment tasks <span className="text-[var(--taali-muted)]">· {selectedTaskIds.length} linked</span>
          </span>
          <ChevronDown size={16} className={cx('text-[var(--taali-muted)] transition', tasksOpen && 'rotate-180')} />
        </button>
        {tasksOpen ? (
          <div className="max-h-[18rem] space-y-2 overflow-y-auto border-t border-[var(--taali-border-muted)] p-3">
            {allTasks.length === 0 ? (
              <p className="text-sm text-[var(--taali-muted)]">No tasks available. Create tasks first to link them.</p>
            ) : allTasks.map((task) => {
              const checked = selectedTaskIds.includes(Number(task.id));
              return (
                <label
                  key={task.id}
                  className={cx(
                    'flex cursor-pointer items-start gap-3 rounded-[var(--taali-radius-card)] border px-3 py-2 transition',
                    checked
                      ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)]'
                      : 'border-[var(--taali-border-muted)] bg-[var(--taali-surface)] hover:border-[var(--taali-border)]',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleTask(Number(task.id))}
                    className="mt-0.5 h-4 w-4 accent-[var(--taali-purple)]"
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-[var(--taali-text)]">{task.name}</span>
                    {task.description ? (
                      <span className="mt-0.5 block line-clamp-2 text-xs text-[var(--taali-muted)]">{task.description}</span>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </div>
        ) : null}
      </div>

      <div className="flex gap-2">
        <Button variant="primary" onClick={handleSave} disabled={!hasValidName || saving || !dirty}>
          {saving ? 'Saving…' : 'Save changes'}
        </Button>
        {onCancel ? (
          <Button variant="secondary" onClick={onCancel} disabled={saving}>Cancel</Button>
        ) : null}
      </div>
    </div>
  );
};

// Order-insensitive id-set equality (so re-ordering tasks isn't "dirty").
function sameIds(a, b) {
  if (a.length !== b.length) return false;
  const setB = new Set(b);
  return a.every((id) => setB.has(id));
}

export default RoleSpecEditPanel;
