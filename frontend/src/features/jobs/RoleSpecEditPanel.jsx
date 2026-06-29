import React, { useEffect, useState } from 'react';

import { RoleEditFields } from '../candidates/RoleEditFields';

// Inline role editor for the Job Specification tab — the same fields as the
// <RoleSheet> slide-over, but edited directly in the tab (no modal). Seeds from
// the current role + linked tasks; Save calls onSubmit({ name, description,
// jobSpecFile, taskIds }) — the same contract the slide-over used.
export const RoleSpecEditPanel = ({ role, roleTasks, allTasks, saving, error, onSubmit }) => {
  const [name, setName] = useState(role?.name || '');
  const [description, setDescription] = useState(role?.description || '');
  const [jobSpecFile, setJobSpecFile] = useState(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState([]);
  const [nameTouched, setNameTouched] = useState(false);

  // Re-seed when the role (or its linked tasks) changes — e.g. navigating
  // between roles, or after a save refreshes the workspace.
  useEffect(() => {
    setName(role?.name || '');
    setDescription(role?.description || '');
    setJobSpecFile(null);
    setSelectedTaskIds((roleTasks || []).map((task) => Number(task.id)));
    setNameTouched(false);
  }, [role?.id, role?.name, role?.description, roleTasks]);

  const hasValidName = name.trim().length > 0;

  const dirty = (
    name.trim() !== (role?.name || '').trim()
    || description.trim() !== (role?.description || '').trim()
    || jobSpecFile != null
    || !sameIds(selectedTaskIds, (roleTasks || []).map((t) => Number(t.id)))
  );

  const toggleTask = (taskId) => {
    setSelectedTaskIds((prev) => (
      prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]
    ));
  };

  const handleSave = () => {
    setNameTouched(true);
    if (!hasValidName || saving) return;
    onSubmit({
      name: name.trim(),
      description: description.trim(),
      jobSpecFile,
      taskIds: selectedTaskIds,
    });
  };

  return (
    <div className="role-edit-inline">
      {error ? <div className="role-edit-inline-error">{error}</div> : null}

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

      <div className="role-edit-inline-actions">
        <button
          type="button"
          className="btn btn-purple btn-sm"
          onClick={handleSave}
          disabled={!hasValidName || saving || !dirty}
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
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
