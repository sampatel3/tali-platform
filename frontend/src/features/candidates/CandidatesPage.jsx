import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  BriefcaseBusiness,
  CheckCircle2,
  ChevronsUpDown,
  FileText,
  Loader2,
  Plus,
  Search,
  UploadCloud,
  UserPlus,
  X,
} from 'lucide-react';
import * as apiClient from '../../lib/api';

const parseCollection = (data) => (Array.isArray(data) ? data : (data?.items || []));
const formatDateTime = (value) => (value ? new Date(value).toLocaleString() : '—');
const trimOrUndefined = (value) => {
  const trimmed = String(value || '').trim();
  return trimmed.length > 0 ? trimmed : undefined;
};

const statusPillClass = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized.includes('interview') || normalized.includes('review')) {
    return 'bg-blue-50 border-blue-200 text-blue-700';
  }
  if (normalized.includes('reject') || normalized.includes('decline')) {
    return 'bg-red-50 border-red-200 text-red-700';
  }
  if (normalized.includes('offer') || normalized.includes('hired')) {
    return 'bg-green-50 border-green-200 text-green-700';
  }
  return 'bg-amber-50 border-amber-200 text-amber-700';
};

const getErrorMessage = (err, fallback) => err?.response?.data?.detail || fallback;

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

const Sheet = ({ open, onClose, title, description, children, footer }) => {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;

    const previousOverflow = document.body.style.overflow;
    previousFocusRef.current = document.activeElement;
    document.body.style.overflow = 'hidden';

    const focusables = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
    if (focusables && focusables.length > 0) {
      focusables[0].focus();
    } else {
      panelRef.current?.focus();
    }

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = panelRef.current?.querySelectorAll(FOCUSABLE_SELECTOR);
      if (!items || items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocusRef.current && typeof previousFocusRef.current.focus === 'function') {
        previousFocusRef.current.focus();
      }
    };
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 backdrop-blur-[1px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="absolute inset-x-0 bottom-0 max-h-[92vh] rounded-t-2xl border border-gray-200 bg-white shadow-2xl focus:outline-none md:inset-y-0 md:right-0 md:left-auto md:h-full md:max-h-none md:w-[640px] md:rounded-none md:border-l"
      >
        <div className="sticky top-0 z-10 border-b border-gray-200 bg-white/95 px-5 py-4 backdrop-blur">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-xl font-bold tracking-tight">{title}</h2>
              {description ? <p className="mt-1 text-sm text-gray-600">{description}</p> : null}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-gray-200 p-1.5 text-gray-600 transition hover:bg-gray-100 hover:text-black"
              aria-label="Close"
            >
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="overflow-y-auto px-5 py-5" style={{ maxHeight: 'calc(92vh - 150px)' }}>
          {children}
        </div>
        <div className="sticky bottom-0 border-t border-gray-200 bg-white px-5 py-4">{footer}</div>
      </div>
    </div>
  );
};

const RolesList = ({ roles, selectedRoleId, loading, error, onSelectRole, onCreateRole }) => (
  <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
    <div className="mb-4 flex items-center justify-between">
      <h2 className="text-sm font-semibold uppercase tracking-[0.08em] text-gray-600">Roles</h2>
      <button
        type="button"
        onClick={onCreateRole}
        className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50"
      >
        <Plus size={14} />
        New
      </button>
    </div>

    {loading ? (
      <div className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-4 text-sm text-gray-500">
        <Loader2 size={14} className="animate-spin" />
        Loading roles...
      </div>
    ) : null}

    {!loading && error ? (
      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-700">
        {error}
      </div>
    ) : null}

    {!loading && !error && roles.length === 0 ? (
      <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 p-5 text-center">
        <p className="text-sm font-medium text-gray-900">No roles yet</p>
        <p className="mt-1 text-xs text-gray-500">Create your first role to start adding candidates.</p>
        <button
          type="button"
          onClick={onCreateRole}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-black bg-black px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-gray-800"
        >
          <Plus size={14} />
          Create your first role
        </button>
      </div>
    ) : null}

    {!loading && !error && roles.length > 0 ? (
      <ul className="space-y-2">
        {roles.map((role) => {
          const selected = String(role.id) === String(selectedRoleId);
          return (
            <li key={role.id}>
              <button
                type="button"
                onClick={() => onSelectRole(String(role.id))}
                className={`w-full rounded-xl border px-3 py-3 text-left transition ${
                  selected
                    ? 'border-black bg-gray-100 shadow-sm'
                    : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-gray-900">{role.name}</p>
                    <p className="mt-0.5 text-xs text-gray-600">
                      {role.applications_count || 0} candidate{(role.applications_count || 0) === 1 ? '' : 's'}
                    </p>
                  </div>
                  <ChevronsUpDown size={14} className="mt-0.5 shrink-0 text-gray-400" />
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span
                    className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                      role.job_spec_filename
                        ? 'border-green-200 bg-green-50 text-green-700'
                        : 'border-amber-200 bg-amber-50 text-amber-700'
                    }`}
                  >
                    {role.job_spec_filename ? 'Spec uploaded' : 'No spec'}
                  </span>
                  <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700">
                    Tasks: {role.tasks_count || 0}
                  </span>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    ) : null}
  </section>
);

const RoleSummaryHeader = ({ role, roleTasks, onEditRole }) => {
  if (!role) return null;
  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-2xl font-bold tracking-tight text-gray-900">{role.name}</h2>
          {role.description ? <p className="text-sm text-gray-600">{role.description}</p> : null}
        </div>
        <button
          type="button"
          onClick={onEditRole}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
        >
          Edit role
        </button>
      </div>
      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
        <div className="flex flex-wrap items-center gap-5">
          <div className="inline-flex items-center gap-2 text-sm text-gray-700">
            <FileText size={15} className="text-gray-500" />
            <span className="font-medium">Job spec:</span>
            <span>{role.job_spec_filename || 'Not uploaded'}</span>
          </div>
          <div className="inline-flex items-center gap-2 text-sm text-gray-700">
            <BriefcaseBusiness size={15} className="text-gray-500" />
            <span className="font-medium">Tasks ({roleTasks.length}):</span>
            {roleTasks.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {roleTasks.map((task) => (
                  <span
                    key={task.id}
                    className="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-xs text-gray-700"
                  >
                    {task.name}
                  </span>
                ))}
              </div>
            ) : (
              <span className="text-gray-500">No linked tasks</span>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};

const CandidatesTable = ({
  applications,
  loading,
  error,
  searchQuery,
  roleTasks,
  canCreateAssessment,
  creatingAssessmentId,
  viewingApplicationId,
  onAddCandidate,
  onViewCandidate,
  onCreateAssessment,
}) => {
  const [composerApplicationId, setComposerApplicationId] = useState(null);
  const [taskByApplication, setTaskByApplication] = useState({});

  useEffect(() => {
    setComposerApplicationId(null);
  }, [applications, roleTasks]);

  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return applications;
    const query = searchQuery.toLowerCase();
    return applications.filter((app) => (
      [
        app.candidate_name,
        app.candidate_email,
        app.candidate_position,
        app.status,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query)
    ));
  }, [applications, searchQuery]);

  if (loading) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white px-4 py-10 text-center text-sm text-gray-500 shadow-sm">
        <div className="inline-flex items-center gap-2">
          <Loader2 size={15} className="animate-spin" />
          Loading candidates...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-10 text-center text-sm text-red-700 shadow-sm">
        {error}
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-gray-300 bg-gray-50 px-5 py-14 text-center">
        <h3 className="text-lg font-semibold text-gray-900">No candidates yet</h3>
        <p className="mt-1 text-sm text-gray-600">
          Add your first candidate to this role and start assessments.
        </p>
        <button
          type="button"
          onClick={onAddCandidate}
          className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-black bg-black px-3 py-2 text-sm font-semibold text-white transition hover:bg-gray-800"
        >
          <UserPlus size={15} />
          Add candidate
        </button>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-gray-200 bg-white shadow-sm">
      <table className="w-full min-w-[760px]">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
            <th className="px-4 py-3">Name</th>
            <th className="px-4 py-3">Email</th>
            <th className="px-4 py-3">Position</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Last activity</th>
            <th className="px-4 py-3">Actions</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((app) => {
            const selectedTask = taskByApplication[app.id] || (roleTasks.length === 1 ? String(roleTasks[0].id) : '');
            const canOpenComposer = Boolean(canCreateAssessment && app.cv_filename && roleTasks.length > 0);

            return (
              <React.Fragment key={app.id}>
                <tr className="border-b border-gray-100 align-top">
                  <td className="px-4 py-3 text-sm font-semibold text-gray-900">
                    {app.candidate_name || app.candidate_email}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.candidate_email}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.candidate_position || '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${statusPillClass(app.status)}`}>
                      {app.status || 'applied'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{formatDateTime(app.updated_at || app.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onViewCandidate(app)}
                        disabled={viewingApplicationId === app.id}
                        className="rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {viewingApplicationId === app.id ? 'Loading...' : 'View'}
                      </button>
                      {canCreateAssessment ? (
                        <button
                          type="button"
                          onClick={() => {
                            if (composerApplicationId === app.id) {
                              setComposerApplicationId(null);
                              return;
                            }
                            if (roleTasks.length === 1) {
                              setTaskByApplication((prev) => ({ ...prev, [app.id]: String(roleTasks[0].id) }));
                            }
                            setComposerApplicationId(app.id);
                          }}
                          disabled={!canOpenComposer}
                          className="rounded-md border border-black bg-black px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:border-gray-300 disabled:bg-gray-200 disabled:text-gray-500"
                        >
                          Create assessment
                        </button>
                      ) : null}
                    </div>
                    {!app.cv_filename ? (
                      <p className="mt-1 text-xs text-amber-700">CV is required before assessment.</p>
                    ) : null}
                    {app.cv_filename && roleTasks.length === 0 ? (
                      <p className="mt-1 text-xs text-amber-700">Link at least one task to this role first.</p>
                    ) : null}
                  </td>
                </tr>

                {composerApplicationId === app.id ? (
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <td colSpan={6} className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <select
                          value={selectedTask}
                          onChange={(event) => {
                            setTaskByApplication((prev) => ({ ...prev, [app.id]: event.target.value }));
                          }}
                          className="min-w-[240px] rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:border-black focus:outline-none"
                        >
                          <option value="">Select task...</option>
                          {roleTasks.map((task) => (
                            <option key={task.id} value={task.id}>{task.name}</option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={async () => {
                            const success = await onCreateAssessment(app, selectedTask);
                            if (success) setComposerApplicationId(null);
                          }}
                          disabled={!selectedTask || creatingAssessmentId === app.id}
                          className="rounded-md border border-black bg-black px-3 py-2 text-xs font-semibold uppercase tracking-wide text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:border-gray-300 disabled:bg-gray-200 disabled:text-gray-500"
                        >
                          {creatingAssessmentId === app.id ? 'Creating...' : 'Send assessment'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setComposerApplicationId(null)}
                          className="rounded-md border border-gray-300 px-3 py-2 text-xs font-medium text-gray-700 transition hover:bg-gray-100"
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const RoleSheet = ({
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
  const [jobSpecFile, setJobSpecFile] = useState(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState([]);
  const [nameTouched, setNameTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setStep(1);
    setName(role?.name || '');
    setDescription(role?.description || '');
    setJobSpecFile(null);
    setSelectedTaskIds((roleTasks || []).map((task) => Number(task.id)));
    setNameTouched(false);
  }, [mode, open, role, roleTasks]);

  const hasValidName = name.trim().length > 0;
  const canSave = hasValidName && !saving;
  const steps = ['Role details', 'Job spec', 'Tasks'];
  const isEdit = mode === 'edit';

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
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100"
          >
            Cancel
          </button>
          <div className="flex items-center gap-2">
            {step > 1 ? (
              <button
                type="button"
                onClick={() => setStep((value) => Math.max(1, value - 1))}
                className="rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100"
              >
                Back
              </button>
            ) : null}
            {step < 3 ? (
              <button
                type="button"
                onClick={() => {
                  if (step === 1) setNameTouched(true);
                  if (step === 1 && !hasValidName) return;
                  setStep((value) => Math.min(3, value + 1));
                }}
                className="rounded-md border border-black bg-black px-3 py-2 text-sm font-semibold text-white transition hover:bg-gray-800"
              >
                Next
              </button>
            ) : (
              <button
                type="button"
                disabled={!canSave}
                onClick={() => {
                  setNameTouched(true);
                  if (!hasValidName) return;
                  onSubmit({
                    name: name.trim(),
                    description: description.trim(),
                    jobSpecFile,
                    taskIds: selectedTaskIds,
                  });
                }}
                className="rounded-md border border-black bg-black px-3 py-2 text-sm font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:border-gray-300 disabled:bg-gray-200 disabled:text-gray-500"
              >
                {saving ? 'Saving...' : 'Save role'}
              </button>
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
            <div
              key={stepLabel}
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${
                current
                  ? 'border-black bg-black text-white'
                  : complete
                    ? 'border-green-200 bg-green-50 text-green-700'
                    : 'border-gray-200 bg-white text-gray-500'
              }`}
            >
              {complete ? <CheckCircle2 size={13} /> : <span>{stepNumber}</span>}
              {stepLabel}
            </div>
          );
        })}
      </div>

      {error ? (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {step === 1 ? (
        <div className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-gray-800">Role name *</span>
            <input
              type="text"
              value={name}
              onBlur={() => setNameTouched(true)}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Senior Backend Engineer"
              className={`w-full rounded-md border px-3 py-2 text-sm focus:outline-none ${
                !hasValidName && nameTouched
                  ? 'border-red-300 bg-red-50'
                  : 'border-gray-300 bg-white focus:border-black'
              }`}
            />
            {!hasValidName && nameTouched ? (
              <span className="mt-1 block text-xs text-red-700">Role name is required.</span>
            ) : null}
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-gray-800">Description</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Optional summary for recruiters."
              className="min-h-[110px] w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:border-black focus:outline-none"
            />
          </label>
        </div>
      ) : null}

      {step === 2 ? (
        <div className="space-y-4">
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
            <p className="text-sm font-medium text-gray-900">Upload job spec (optional but recommended)</p>
            <p className="mt-1 text-xs text-gray-600">
              Adding a spec now lets recruiters add candidates without friction.
            </p>
          </div>
          {role?.job_spec_filename ? (
            <div className="rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700">
              Current file: <span className="font-medium">{role.job_spec_filename}</span>
            </div>
          ) : null}
          <label className="block rounded-xl border border-dashed border-gray-300 p-5 text-center transition hover:border-gray-400">
            <UploadCloud size={20} className="mx-auto text-gray-500" />
            <span className="mt-2 block text-sm font-medium text-gray-700">
              {jobSpecFile ? jobSpecFile.name : 'Choose a job specification file'}
            </span>
            <span className="mt-1 block text-xs text-gray-500">PDF, DOCX, or TXT</span>
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
            <p className="text-sm font-semibold text-gray-900">Link tasks</p>
            <p className="text-xs text-gray-500">{selectedTaskIds.length} selected</p>
          </div>
          {allTasks.length === 0 ? (
            <div className="rounded-md border border-dashed border-gray-300 bg-gray-50 px-3 py-4 text-sm text-gray-600">
              No tasks available yet. Create tasks first and come back to link them.
            </div>
          ) : (
            <div className="max-h-[300px] space-y-2 overflow-y-auto pr-1">
              {allTasks.map((task) => {
                const checked = selectedTaskIds.includes(Number(task.id));
                return (
                  <label
                    key={task.id}
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 ${
                      checked ? 'border-black bg-gray-100' : 'border-gray-200 bg-white'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleTask(Number(task.id))}
                      className="mt-0.5 h-4 w-4"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-gray-900">{task.name}</span>
                      {task.description ? (
                        <span className="mt-0.5 block text-xs text-gray-600">{task.description}</span>
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

const CandidateSheet = ({
  open,
  role,
  saving,
  error,
  onClose,
  onSubmit,
}) => {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [position, setPosition] = useState('');
  const [cvFile, setCvFile] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [touched, setTouched] = useState({
    email: false,
    name: false,
    cv: false,
  });

  useEffect(() => {
    if (!open) return;
    setEmail('');
    setName('');
    setPosition(role?.name || '');
    setCvFile(null);
    setDragActive(false);
    setTouched({ email: false, name: false, cv: false });
  }, [open, role]);

  const hasRoleSpec = Boolean(role?.job_spec_filename);
  const validEmail = email.trim().length > 0;
  const validName = name.trim().length > 0;
  const hasCv = Boolean(cvFile);
  const canSave = Boolean(role) && hasRoleSpec && validEmail && validName && hasCv && !saving;

  const onDropFile = (event) => {
    event.preventDefault();
    setDragActive(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) setCvFile(file);
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Add candidate"
      description="Create a role application and upload the candidate CV."
      footer={(
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-100"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSave}
            onClick={() => {
              setTouched({ email: true, name: true, cv: true });
              if (!canSave) return;
              onSubmit({
                email: email.trim(),
                name: name.trim(),
                position: position.trim() || undefined,
                cvFile,
              });
            }}
            className="rounded-md border border-black bg-black px-3 py-2 text-sm font-semibold text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:border-gray-300 disabled:bg-gray-200 disabled:text-gray-500"
          >
            {saving ? 'Saving...' : 'Add candidate'}
          </button>
        </div>
      )}
    >
      {error ? (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      <div className="space-y-4">
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
          <span className="font-medium">Role:</span> {role?.name || 'No role selected'}
        </div>

        {!hasRoleSpec ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Upload a role job spec before adding candidates.
          </div>
        ) : null}

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Email *</span>
          <input
            type="email"
            value={email}
            onBlur={() => setTouched((prev) => ({ ...prev, email: true }))}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="candidate@company.com"
            className={`w-full rounded-md border px-3 py-2 text-sm focus:outline-none ${
              touched.email && !validEmail
                ? 'border-red-300 bg-red-50'
                : 'border-gray-300 bg-white focus:border-black'
            }`}
          />
          {touched.email && !validEmail ? (
            <span className="mt-1 block text-xs text-red-700">Candidate email is required.</span>
          ) : null}
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Candidate name *</span>
          <input
            type="text"
            value={name}
            onBlur={() => setTouched((prev) => ({ ...prev, name: true }))}
            onChange={(event) => setName(event.target.value)}
            placeholder="Jane Doe"
            className={`w-full rounded-md border px-3 py-2 text-sm focus:outline-none ${
              touched.name && !validName
                ? 'border-red-300 bg-red-50'
                : 'border-gray-300 bg-white focus:border-black'
            }`}
          />
          {touched.name && !validName ? (
            <span className="mt-1 block text-xs text-red-700">Candidate name is required.</span>
          ) : null}
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Candidate position</span>
          <input
            type="text"
            value={position}
            onChange={(event) => setPosition(event.target.value)}
            placeholder="Defaults to role title"
            className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:border-black focus:outline-none"
          />
        </label>

        <div>
          <span className="mb-1 block text-sm font-semibold text-gray-800">CV upload *</span>
          <label
            onDragEnter={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={(event) => {
              event.preventDefault();
              setDragActive(false);
            }}
            onDrop={onDropFile}
            className={`block rounded-xl border border-dashed p-5 text-center transition ${
              dragActive ? 'border-black bg-gray-100' : 'border-gray-300 bg-white hover:border-gray-400'
            }`}
          >
            <UploadCloud size={20} className="mx-auto text-gray-500" />
            <span className="mt-2 block text-sm font-medium text-gray-700">
              {cvFile ? cvFile.name : 'Drop CV here or choose a file'}
            </span>
            <span className="mt-1 block text-xs text-gray-500">PDF or DOCX</span>
            <input
              type="file"
              accept=".pdf,.docx,.doc"
              onChange={(event) => {
                setTouched((prev) => ({ ...prev, cv: true }));
                setCvFile(event.target.files?.[0] || null);
              }}
              className="sr-only"
            />
          </label>
          {touched.cv && !hasCv ? (
            <span className="mt-1 block text-xs text-red-700">CV is required.</span>
          ) : null}
        </div>
      </div>
    </Sheet>
  );
};

const EmptyRoleDetail = ({ onCreateRole }) => (
  <section className="rounded-2xl border border-dashed border-gray-300 bg-gray-50 px-6 py-16 text-center">
    <h2 className="text-xl font-semibold text-gray-900">No role selected</h2>
    <p className="mt-1 text-sm text-gray-600">Create a role to start managing candidates.</p>
    <button
      type="button"
      onClick={onCreateRole}
      className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-black bg-black px-3 py-2 text-sm font-semibold text-white transition hover:bg-gray-800"
    >
      <Plus size={15} />
      Create your first role
    </button>
  </section>
);

export const CandidatesPage = ({ onNavigate, onViewCandidate, NavComponent }) => {
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const tasksApi = apiClient.tasks;
  const assessmentsApi = apiClient.assessments;

  const [roles, setRoles] = useState([]);
  const [selectedRoleId, setSelectedRoleId] = useState('');
  const [roleTasks, setRoleTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');

  const [loadingRoles, setLoadingRoles] = useState(true);
  const [loadingRoleContext, setLoadingRoleContext] = useState(false);
  const [loadingTasks, setLoadingTasks] = useState(true);
  const [rolesError, setRolesError] = useState('');
  const [roleContextError, setRoleContextError] = useState('');

  const [roleSheetOpen, setRoleSheetOpen] = useState(false);
  const [roleSheetMode, setRoleSheetMode] = useState('create');
  const [candidateSheetOpen, setCandidateSheetOpen] = useState(false);
  const [savingRole, setSavingRole] = useState(false);
  const [addingCandidate, setAddingCandidate] = useState(false);
  const [roleSheetError, setRoleSheetError] = useState('');
  const [candidateSheetError, setCandidateSheetError] = useState('');
  const [creatingAssessmentId, setCreatingAssessmentId] = useState(null);
  const [viewingApplicationId, setViewingApplicationId] = useState(null);

  const selectedRole = useMemo(
    () => roles.find((role) => String(role.id) === String(selectedRoleId)) || null,
    [roles, selectedRoleId]
  );

  const loadRoles = useCallback(async (preferredRoleId = null) => {
    if (!rolesApi?.list) {
      setRoles([]);
      setLoadingRoles(false);
      return;
    }
    setLoadingRoles(true);
    setRolesError('');
    try {
      const res = await rolesApi.list();
      const items = res.data || [];
      setRoles(items);
      setSelectedRoleId((current) => {
        const target = preferredRoleId ? String(preferredRoleId) : String(current || '');
        if (target && items.some((role) => String(role.id) === target)) return target;
        return items.length > 0 ? String(items[0].id) : '';
      });
    } catch {
      setRoles([]);
      setRolesError('Failed to load roles.');
      setSelectedRoleId('');
    } finally {
      setLoadingRoles(false);
    }
  }, [rolesApi]);

  const loadRoleContext = useCallback(async (roleId) => {
    if (!roleId) {
      setRoleTasks([]);
      setRoleApplications([]);
      setRoleContextError('');
      return;
    }

    setLoadingRoleContext(true);
    setRoleContextError('');
    try {
      const [tasksRes, applicationsRes] = await Promise.all([
        rolesApi?.listTasks ? rolesApi.listTasks(roleId) : Promise.resolve({ data: [] }),
        rolesApi?.listApplications ? rolesApi.listApplications(roleId) : Promise.resolve({ data: [] }),
      ]);
      setRoleTasks(tasksRes.data || []);
      setRoleApplications(applicationsRes.data || []);
    } catch {
      setRoleTasks([]);
      setRoleApplications([]);
      setRoleContextError('Failed to load role details.');
    } finally {
      setLoadingRoleContext(false);
    }
  }, [rolesApi]);

  const loadTasks = useCallback(async () => {
    if (!tasksApi?.list) {
      setAllTasks([]);
      setLoadingTasks(false);
      return;
    }
    setLoadingTasks(true);
    try {
      const res = await tasksApi.list();
      setAllTasks(res.data || []);
    } catch {
      setAllTasks([]);
    } finally {
      setLoadingTasks(false);
    }
  }, [tasksApi]);

  useEffect(() => {
    loadRoles();
    loadTasks();
  }, [loadRoles, loadTasks]);

  useEffect(() => {
    loadRoleContext(selectedRoleId);
  }, [selectedRoleId, loadRoleContext]);

  const mapAssessmentForDetail = (assessment, fallbackApp) => ({
    id: assessment.id,
    name: (assessment.candidate_name || fallbackApp?.candidate_name || assessment.candidate_email || '').trim() || 'Unknown',
    email: assessment.candidate_email || fallbackApp?.candidate_email || '',
    task: assessment.task_name || assessment.task?.name || 'Assessment',
    status: assessment.status || 'pending',
    score: assessment.score ?? assessment.overall_score ?? null,
    time: assessment.duration_taken ? `${Math.round(assessment.duration_taken / 60)}m` : '—',
    position: fallbackApp?.candidate_position || '',
    completedDate: assessment.completed_at ? new Date(assessment.completed_at).toLocaleDateString() : null,
    breakdown: assessment.breakdown || null,
    prompts: assessment.prompt_count ?? 0,
    promptsList: assessment.prompts_list || [],
    timeline: assessment.timeline || [],
    results: assessment.results || [],
    token: assessment.token,
    _raw: assessment,
  });

  const handleOpenRoleSheet = (mode) => {
    setRoleSheetMode(mode);
    setRoleSheetError('');
    setRoleSheetOpen(true);
  };

  const handleRoleSubmit = async ({ name, description, jobSpecFile, taskIds }) => {
    if (!rolesApi) return;
    setSavingRole(true);
    setRoleSheetError('');
    try {
      let activeRoleId = selectedRoleId;
      if (roleSheetMode === 'create') {
        const createRes = await rolesApi.create({
          name,
          description: trimOrUndefined(description),
        });
        activeRoleId = String(createRes.data.id);
      } else if (rolesApi.update && selectedRoleId) {
        await rolesApi.update(selectedRoleId, {
          name,
          description: trimOrUndefined(description),
        });
        activeRoleId = String(selectedRoleId);
      }

      if (jobSpecFile && rolesApi.uploadJobSpec) {
        await rolesApi.uploadJobSpec(activeRoleId, jobSpecFile);
      }

      const nextTaskIds = new Set((taskIds || []).map((id) => Number(id)));
      const currentTaskIds = new Set((roleSheetMode === 'edit' ? roleTasks : []).map((task) => Number(task.id)));

      if (rolesApi.addTask) {
        for (const taskId of nextTaskIds) {
          if (!currentTaskIds.has(taskId)) {
            await rolesApi.addTask(activeRoleId, taskId);
          }
        }
      }

      if (roleSheetMode === 'edit' && rolesApi.removeTask) {
        for (const taskId of currentTaskIds) {
          if (!nextTaskIds.has(taskId)) {
            await rolesApi.removeTask(activeRoleId, taskId);
          }
        }
      }

      await loadRoles(activeRoleId);
      await loadRoleContext(activeRoleId);
      setRoleSheetOpen(false);
    } catch (err) {
      setRoleSheetError(getErrorMessage(err, 'Failed to save role.'));
    } finally {
      setSavingRole(false);
    }
  };

  const handleCandidateSubmit = async ({ email, name, position, cvFile }) => {
    if (!rolesApi?.createApplication || !rolesApi?.uploadApplicationCv || !selectedRoleId) return;
    setAddingCandidate(true);
    setCandidateSheetError('');
    try {
      const res = await rolesApi.createApplication(selectedRoleId, {
        candidate_email: email,
        candidate_name: name,
        candidate_position: trimOrUndefined(position),
      });
      await rolesApi.uploadApplicationCv(res.data.id, cvFile);

      await Promise.all([
        loadRoleContext(selectedRoleId),
        loadRoles(selectedRoleId),
      ]);
      setCandidateSheetOpen(false);
    } catch (err) {
      setCandidateSheetError(getErrorMessage(err, 'Failed to add candidate.'));
    } finally {
      setAddingCandidate(false);
    }
  };

  const handleViewFromApplication = async (application) => {
    setViewingApplicationId(application.id);
    try {
      const res = await assessmentsApi.list({
        candidate_id: application.candidate_id,
        role_id: selectedRoleId,
        limit: 1,
        offset: 0,
      });
      const items = parseCollection(res.data);
      if (items.length === 0) {
        alert('No assessments found for this candidate yet.');
        return;
      }
      onViewCandidate(mapAssessmentForDetail(items[0], application));
    } catch (err) {
      alert(getErrorMessage(err, 'Failed to load candidate details.'));
    } finally {
      setViewingApplicationId(null);
    }
  };

  const handleCreateAssessment = async (application, taskId) => {
    if (!rolesApi?.createAssessment) return false;
    const taskNumber = Number(taskId);
    if (!taskNumber) {
      alert('Select a task first.');
      return false;
    }
    setCreatingAssessmentId(application.id);
    try {
      await rolesApi.createAssessment(application.id, { task_id: taskNumber });
      await loadRoleContext(selectedRoleId);
      alert('Assessment created and invite sent.');
      return true;
    } catch (err) {
      alert(getErrorMessage(err, 'Failed to create assessment.'));
      return false;
    } finally {
      setCreatingAssessmentId(null);
    }
  };

  return (
    <div>
      <NavComponent currentPage="candidates" onNavigate={onNavigate} />

      <div className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
        <header className="mb-6 space-y-4 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h1 className="text-3xl font-bold tracking-tight text-gray-900">Candidates</h1>
              <p className="mt-1 text-sm text-gray-600">Manage role pipelines and assessments in one place.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => handleOpenRoleSheet('create')}
                className="inline-flex items-center gap-1.5 rounded-md border border-black bg-black px-3 py-2 text-sm font-semibold text-white transition hover:bg-gray-800"
              >
                <Plus size={15} />
                New role
              </button>
              <button
                type="button"
                disabled={!selectedRoleId}
                onClick={() => {
                  setCandidateSheetError('');
                  setCandidateSheetOpen(true);
                }}
                className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <UserPlus size={15} />
                Add candidate
              </button>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-[280px_minmax(0,1fr)]">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Active role
              </span>
              <select
                value={selectedRoleId}
                onChange={(event) => setSelectedRoleId(event.target.value)}
                disabled={loadingRoles || roles.length === 0}
                className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm focus:border-black focus:outline-none disabled:cursor-not-allowed disabled:bg-gray-100"
              >
                {roles.length === 0 ? <option value="">No roles</option> : null}
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>{role.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Search candidates
              </span>
              <div className="relative">
                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search by name, email, position, or status"
                  className="w-full rounded-md border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm focus:border-black focus:outline-none"
                />
              </div>
            </label>
          </div>
        </header>

        <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <RolesList
            roles={roles}
            selectedRoleId={selectedRoleId}
            loading={loadingRoles}
            error={rolesError}
            onSelectRole={setSelectedRoleId}
            onCreateRole={() => handleOpenRoleSheet('create')}
          />

          <div className="space-y-4">
            {!selectedRole ? (
              <EmptyRoleDetail onCreateRole={() => handleOpenRoleSheet('create')} />
            ) : (
              <>
                <RoleSummaryHeader
                  role={selectedRole}
                  roleTasks={roleTasks}
                  onEditRole={() => handleOpenRoleSheet('edit')}
                />
                {loadingTasks ? (
                  <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
                    Loading tasks catalog...
                  </div>
                ) : null}
                {roleContextError ? (
                  <div className="inline-flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    <AlertCircle size={15} />
                    {roleContextError}
                  </div>
                ) : null}
                <CandidatesTable
                  applications={roleApplications}
                  loading={loadingRoleContext}
                  error={roleContextError}
                  searchQuery={searchQuery}
                  roleTasks={roleTasks}
                  canCreateAssessment={Boolean(rolesApi?.createAssessment)}
                  creatingAssessmentId={creatingAssessmentId}
                  viewingApplicationId={viewingApplicationId}
                  onAddCandidate={() => {
                    setCandidateSheetError('');
                    setCandidateSheetOpen(true);
                  }}
                  onViewCandidate={handleViewFromApplication}
                  onCreateAssessment={handleCreateAssessment}
                />
              </>
            )}
          </div>
        </div>
      </div>

      <RoleSheet
        open={roleSheetOpen}
        mode={roleSheetMode}
        role={roleSheetMode === 'edit' ? selectedRole : null}
        roleTasks={roleSheetMode === 'edit' ? roleTasks : []}
        allTasks={allTasks}
        saving={savingRole}
        error={roleSheetError}
        onClose={() => setRoleSheetOpen(false)}
        onSubmit={handleRoleSubmit}
      />

      <CandidateSheet
        open={candidateSheetOpen}
        role={selectedRole}
        saving={addingCandidate}
        error={candidateSheetError}
        onClose={() => setCandidateSheetOpen(false)}
        onSubmit={handleCandidateSubmit}
      />
    </div>
  );
};
