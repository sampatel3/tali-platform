import React, { useEffect, useState } from 'react';

import { Button, Dialog, Select, Textarea } from '../../shared/ui/TaaliPrimitives';

export function RetakeAssessmentDialog({
  open,
  application,
  roleTasks,
  loading = false,
  defaultTaskId = '',
  onClose,
  onConfirm,
}) {
  const [selectedTask, setSelectedTask] = useState('');
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (!open) return;
    if (defaultTaskId) {
      setSelectedTask(String(defaultTaskId));
    } else if (roleTasks.length === 1) {
      setSelectedTask(String(roleTasks[0].id));
    } else {
      setSelectedTask('');
    }
    setReason('');
  }, [defaultTaskId, open, roleTasks]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Retake assessment"
      description={`Create a new assessment for ${application?.candidate_name || application?.candidate_email || 'this candidate'} and void the current attempt.`}
      footer={(
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="primary"
            disabled={!selectedTask || loading}
            onClick={() => onConfirm?.({
              taskId: selectedTask,
              reason,
            })}
          >
            {loading ? 'Creating retake...' : 'Confirm retake'}
          </Button>
        </div>
      )}
    >
      <div className="space-y-4">
        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-[var(--taali-text)]">Task</span>
          <Select value={selectedTask} onChange={(event) => setSelectedTask(event.target.value)}>
            <option value="">Select task...</option>
            {roleTasks.map((task) => (
              <option key={task.id} value={task.id}>{task.name}</option>
            ))}
          </Select>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-[var(--taali-text)]">Reason (optional)</span>
          <Textarea
            rows={4}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Why is this attempt being replaced?"
          />
        </label>
      </div>
    </Dialog>
  );
}

export default RetakeAssessmentDialog;
