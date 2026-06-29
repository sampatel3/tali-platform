import React, { useEffect, useState } from 'react';

import { Button, Card, Sheet } from '../../shared/ui/TaaliPrimitives';
import { RoleEditFields } from './RoleEditFields';

// Slide-over editor for a role (used from the Jobs list). The role DETAIL page
// edits these same fields inline on its Job Specification tab — both render the
// shared <RoleEditFields/> so they never drift.
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

      <RoleEditFields
        name={name}
        onName={setName}
        nameTouched={nameTouched}
        onNameBlur={() => setNameTouched(true)}
        description={description}
        onDescription={setDescription}
        jobSpecFile={jobSpecFile}
        onJobSpecFile={setJobSpecFile}
        selectedTaskIds={selectedTaskIds}
        onToggleTask={toggleTask}
        role={role}
        allTasks={allTasks}
      />
    </Sheet>
  );
};

export default RoleSheet;
