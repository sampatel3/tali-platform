import React, { useEffect, useState } from 'react';
import { CheckCircle2, UploadCloud } from 'lucide-react';

import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Sheet,
  Textarea,
  cx,
} from '../../shared/ui/TaaliPrimitives';

const ROLE_DESCRIPTION_MAX_LENGTH = 20000;
const ROLE_ADDITIONAL_REQUIREMENTS_MAX_LENGTH = 12000;

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
  const [step, setStep] = useState(1);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [additionalRequirements, setAdditionalRequirements] = useState('');
  const [autoRejectEnabled, setAutoRejectEnabled] = useState(false);
  const [autoRejectThreshold100, setAutoRejectThreshold100] = useState('');
  const [autoRejectNoteTemplate, setAutoRejectNoteTemplate] = useState('');
  const [jobSpecFile, setJobSpecFile] = useState(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState([]);
  const [nameTouched, setNameTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setStep(1);
    setName(role?.name || '');
    setDescription(role?.description || '');
    setAdditionalRequirements(role?.additional_requirements || '');
    setAutoRejectEnabled(Boolean(role?.auto_reject_enabled));
    setAutoRejectThreshold100(role?.auto_reject_threshold_100 ?? '');
    setAutoRejectNoteTemplate(role?.auto_reject_note_template || '');
    setJobSpecFile(null);
    setSelectedTaskIds((roleTasks || []).map((task) => Number(task.id)));
    setNameTouched(false);
  }, [mode, open, role, roleTasks]);

  const hasValidName = name.trim().length > 0;
  const canSave = hasValidName && !saving;
  const steps = ['Role details', 'Job spec', 'Tasks'];
  const isEdit = mode === 'edit';
  const descriptionChars = description.length;
  const additionalRequirementsChars = additionalRequirements.length;

  const toggleTask = (taskId) => {
    setSelectedTaskIds((prev) => (
      prev.includes(taskId)
        ? prev.filter((id) => id !== taskId)
        : [...prev, taskId]
    ));
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={isEdit ? 'Edit role' : 'New role'}
      description={isEdit ? 'Update role details, job spec, and linked tasks.' : 'Set up a role in three quick steps.'}
      footer={(
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <div className="flex items-center gap-2">
            {step > 1 ? (
              <Button type="button" variant="ghost" onClick={() => setStep((value) => Math.max(1, value - 1))}>Back</Button>
            ) : null}
            {step < 3 ? (
              <Button
                type="button"
                variant="primary"
                onClick={() => {
                  if (step === 1) setNameTouched(true);
                  if (step === 1 && !hasValidName) return;
                  setStep((value) => Math.min(3, value + 1));
                }}
              >
                Next
              </Button>
            ) : (
              <Button
                type="button"
                variant="primary"
                disabled={!canSave}
                onClick={() => {
                  setNameTouched(true);
                  if (!hasValidName) return;
                  onSubmit({
                    name: name.trim(),
                    description: description.trim(),
                    additionalRequirements: additionalRequirements.trim() || undefined,
                    autoRejectEnabled,
                    autoRejectThreshold100: autoRejectEnabled && autoRejectThreshold100 !== ''
                      ? Number(autoRejectThreshold100)
                      : undefined,
                    autoRejectNoteTemplate: autoRejectNoteTemplate.trim() || undefined,
                    jobSpecFile,
                    taskIds: selectedTaskIds,
                  });
                }}
              >
                {saving ? 'Saving...' : 'Save role'}
              </Button>
            )}
          </div>
        </div>
      )}
    >
      <div className="mb-5 flex flex-wrap items-center gap-2">
        {steps.map((stepLabel, index) => {
          const stepNumber = index + 1;
          const current = stepNumber === step;
          const complete = stepNumber < step;
          return (
            <Badge
              key={stepLabel}
              variant={current ? 'purple' : (complete ? 'success' : 'muted')}
              className="gap-1.5 px-2.5 py-1 text-xs"
            >
              {complete ? <CheckCircle2 size={12} /> : <span>{stepNumber}</span>}
              {stepLabel}
            </Badge>
          );
        })}
      </div>

      {error ? (
        <Card className="mb-4 border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-3 py-2 text-sm text-[var(--taali-danger)]">
          {error}
        </Card>
      ) : null}

      {step === 1 ? (
        <div className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-[var(--taali-text)]">Role name *</span>
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
            <span className="mb-1 block text-sm font-semibold text-[var(--taali-text)]">Description</span>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Optional summary for recruiters."
              className="min-h-[110px]"
              maxLength={ROLE_DESCRIPTION_MAX_LENGTH}
            />
            <span className="mt-1 block text-xs text-[var(--taali-muted)]">
              {descriptionChars.toLocaleString()}/{ROLE_DESCRIPTION_MAX_LENGTH.toLocaleString()} characters
            </span>
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-[var(--taali-text)]">Additional requirements (for CV scoring)</span>
            <Textarea
              value={additionalRequirements}
              onChange={(event) => setAdditionalRequirements(event.target.value)}
              placeholder={`One requirement per line. Prefix with the priority so the AI weighs it correctly.

Examples:
Must have: 5+ years building data pipelines on AWS
Preferred: Banking or fintech background
Nice to have: AWS Solutions Architect certification
Constraint: Based in UAE (no remote)
Disqualifying: No experience with regulated financial data`}
              className="min-h-[160px]"
              maxLength={ROLE_ADDITIONAL_REQUIREMENTS_MAX_LENGTH}
            />
            <span className="mt-1 block text-xs text-[var(--taali-muted)]">
              These are used alongside the job spec when we score how well a candidate&apos;s CV matches. Leave blank to score only on the job spec.
            </span>
            <span className="mt-1 block text-xs text-[var(--taali-muted)]">
              {additionalRequirementsChars.toLocaleString()}/{ROLE_ADDITIONAL_REQUIREMENTS_MAX_LENGTH.toLocaleString()} characters
            </span>
          </label>
          <Card className="p-4">
            <label className="flex items-center gap-2 text-sm font-semibold text-[var(--taali-text)]">
              <input
                type="checkbox"
                checked={autoRejectEnabled}
                onChange={(event) => setAutoRejectEnabled(event.target.checked)}
              />
              Enable role-level auto-reject override
            </label>
            <p className="mt-2 text-xs text-[var(--taali-muted)]">
              Uses pre-screen score only. Leave disabled to inherit the organization-level Workable automation defaults.
            </p>
            {autoRejectEnabled ? (
              <div className="mt-4 space-y-4">
                <label className="block">
                  <span className="mb-1 block text-sm font-semibold text-[var(--taali-text)]">Threshold (0-100)</span>
                  <Input
                    type="number"
                    min={0}
                    max={100}
                    value={autoRejectThreshold100}
                    onChange={(event) => setAutoRejectThreshold100(event.target.value)}
                    placeholder="e.g. 55"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-sm font-semibold text-[var(--taali-text)]">Auto-reject note template</span>
                  <Textarea
                    value={autoRejectNoteTemplate}
                    onChange={(event) => setAutoRejectNoteTemplate(event.target.value)}
                    placeholder="Optional note appended when the candidate is auto-rejected in Workable."
                    className="min-h-[90px]"
                  />
                </label>
              </div>
            ) : null}
          </Card>
        </div>
      ) : null}

      {step === 2 ? (
        <div className="space-y-4">
          <Card className="p-4">
            <p className="text-sm font-medium text-[var(--taali-text)]">Upload job spec (optional but recommended)</p>
            <p className="mt-1 text-xs text-[var(--taali-muted)]">
              Adding a spec now lets recruiters add candidates without friction and auto-generates interview focus pointers.
            </p>
          </Card>
          {role?.job_spec_filename ? (
            <Card className="px-3 py-2 text-sm text-[var(--taali-text)]">
              Current file: <span className="font-medium">{role.job_spec_filename}</span>
            </Card>
          ) : null}
          <label className="block rounded-[var(--taali-radius-card)] border border-dashed border-[var(--taali-border-muted)] bg-[var(--taali-surface)] p-5 text-center transition hover:border-[var(--taali-border)]">
            <UploadCloud size={20} className="mx-auto text-[var(--taali-muted)]" />
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
      ) : null}

      {step === 3 ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-[var(--taali-text)]">Link tasks</p>
            <p className="text-xs text-[var(--taali-muted)]">{selectedTaskIds.length} selected</p>
          </div>
          {allTasks.length === 0 ? (
            <EmptyState
              title="No tasks available"
              description="Create tasks first and come back to link them."
              className="py-8"
            />
          ) : (
            <div className="max-h-[300px] space-y-2 overflow-y-auto pr-1">
              {allTasks.map((task) => {
                const checked = selectedTaskIds.includes(Number(task.id));
                return (
                  <label
                    key={task.id}
                    className={cx(
                      'flex cursor-pointer items-start gap-3 rounded-[var(--taali-radius-card)] border px-3 py-2',
                      checked
                        ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)]'
                        : 'border-[var(--taali-border-muted)] bg-[var(--taali-surface)]'
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleTask(Number(task.id))}
                      className="mt-0.5 h-4 w-4"
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
      ) : null}
    </Sheet>
  );
};
