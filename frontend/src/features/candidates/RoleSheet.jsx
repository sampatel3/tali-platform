import React, { useEffect, useState } from 'react';
import { UploadCloud } from 'lucide-react';

import {
  Button,
  Card,
  EmptyState,
  Input,
  Sheet,
  Textarea,
  cx,
} from '../../shared/ui/TaaliPrimitives';

const ROLE_DESCRIPTION_MAX_LENGTH = 20000;

const SectionHeading = ({ children, hint }) => (
  <div className="mb-3">
    <h3 className="text-sm font-semibold text-[var(--taali-text)]">{children}</h3>
    {hint ? <p className="mt-0.5 text-xs text-[var(--taali-muted)]">{hint}</p> : null}
  </div>
);

export const RoleSheet = ({
  open,
  mode,
  role,
  roleTasks,
  allTasks,
  saving,
  error,
  onClose,
  onSubmit,
}) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [jobSpecFile, setJobSpecFile] = useState(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState([]);
  const [nameTouched, setNameTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(role?.name || '');
    setDescription(role?.description || '');
    setJobSpecFile(null);
    setSelectedTaskIds((roleTasks || []).map((task) => Number(task.id)));
    setNameTouched(false);
  }, [mode, open, role, roleTasks]);

  const hasValidName = name.trim().length > 0;
  const canSave = hasValidName && !saving;
  const isEdit = mode === 'edit';
  const descriptionChars = description.length;

  const toggleTask = (taskId) => {
    setSelectedTaskIds((prev) => (
      prev.includes(taskId)
        ? prev.filter((id) => id !== taskId)
        : [...prev, taskId]
    ));
  };

  const handleSave = () => {
    setNameTouched(true);
    if (!hasValidName) return;
    onSubmit({
      name: name.trim(),
      description: description.trim(),
      jobSpecFile,
      taskIds: selectedTaskIds,
    });
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={isEdit ? 'Edit role' : 'New role'}
      description={isEdit
        ? 'Update the role name, job spec, and linked tasks. Workable stays in sync.'
        : 'Name the role, attach a job spec, and link the task(s) candidates take.'}
      footer={(
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="button" variant="primary" disabled={!canSave} onClick={handleSave}>
            {saving ? 'Saving…' : 'Save role'}
          </Button>
        </div>
      )}
    >
      {error ? (
        <Card className="mb-4 border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">
          {error}
        </Card>
      ) : null}

      <div className="space-y-8">
        {/* Role details */}
        <section>
          <SectionHeading>Role details</SectionHeading>
          <div className="space-y-5">
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">Role name *</span>
              <Input
                type="text"
                value={name}
                onBlur={() => setNameTouched(true)}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Senior Backend Engineer"
                className={!hasValidName && nameTouched ? '!border-[var(--taali-danger)] !bg-[var(--taali-danger-soft)]' : ''}
              />
              {!hasValidName && nameTouched ? (
                <span className="mt-1 block text-xs text-[var(--taali-danger)]">Role name is required.</span>
              ) : null}
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-[var(--taali-text)]">Description</span>
              <Textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Optional summary for recruiters."
                className="min-h-[7rem]"
                maxLength={ROLE_DESCRIPTION_MAX_LENGTH}
              />
              <span className="mt-1 block text-xs text-[var(--taali-muted)]">
                {descriptionChars.toLocaleString()} / {ROLE_DESCRIPTION_MAX_LENGTH.toLocaleString()} characters
              </span>
            </label>
          </div>
        </section>

        {/* Job spec */}
        <section>
          <SectionHeading hint="Used to auto-generate scoring criteria, pre-screen questions, and interview-focus pointers. You can also edit the job spec and criteria directly in agent chat. CV-scoring criteria live on the Agent settings tab.">
            Job spec
          </SectionHeading>
          <div className="space-y-3">
            {role?.job_spec_filename ? (
              <Card className="px-3 py-2 text-sm text-[var(--taali-text)]">
                Current file: <span className="font-medium">{role.job_spec_filename}</span>
              </Card>
            ) : null}
            <label className="block cursor-pointer rounded-[var(--taali-radius-card)] border border-dashed border-[var(--taali-border-muted)] bg-[var(--taali-surface)] p-6 text-center transition hover:border-[var(--taali-purple)] hover:bg-[var(--taali-purple-soft)]">
              <UploadCloud size={22} className="mx-auto text-[var(--taali-muted)]" />
              <span className="mt-2 block text-sm font-medium text-[var(--taali-text)]">
                {jobSpecFile ? jobSpecFile.name : 'Choose a job specification file'}
              </span>
              <span className="mt-1 block text-xs text-[var(--taali-muted)]">PDF, DOCX, or TXT</span>
              <input
                type="file"
                accept=".pdf,.docx,.txt"
                onChange={(event) => setJobSpecFile(event.target.files?.[0] || null)}
                className="sr-only"
              />
            </label>
          </div>
        </section>

        {/* Tasks */}
        <section>
          <SectionHeading>{selectedTaskIds.length > 1 ? 'Tasks · A/B' : 'Tasks'}</SectionHeading>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm text-[var(--taali-muted)]">Link the task(s) candidates take.</p>
              <p className="text-xs text-[var(--taali-muted)]">{selectedTaskIds.length} selected</p>
            </div>
            {selectedTaskIds.length > 1 ? (
              <Card className="border-[var(--taali-purple)] bg-[var(--taali-purple-soft)] px-3 py-2 text-xs text-[var(--taali-text)]">
                <span className="font-semibold text-[var(--taali-purple)]">A/B test</span> — with more than one task linked, each candidate is automatically assigned one (split evenly, stable per candidate). You don&apos;t pick per candidate.
              </Card>
            ) : null}
            {allTasks.length === 0 ? (
              <EmptyState
                title="No tasks available"
                description="Create tasks first and come back to link them."
                className="py-8"
              />
            ) : (
              <div className="max-h-[22.5rem] space-y-2 overflow-y-auto pr-1">
                {allTasks.map((task) => {
                  const checked = selectedTaskIds.includes(Number(task.id));
                  return (
                    <label
                      key={task.id}
                      className={cx(
                        'flex cursor-pointer items-start gap-3 rounded-[var(--taali-radius-card)] border px-3 py-2 transition',
                        checked
                          ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)]'
                          : 'border-[var(--taali-border-muted)] bg-[var(--taali-surface)] hover:border-[var(--taali-border)]'
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
                          <span className="mt-0.5 block text-xs text-[var(--taali-muted)]">{task.description}</span>
                        ) : null}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      </div>
    </Sheet>
  );
};
