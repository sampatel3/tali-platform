import React, { useEffect, useMemo, useState } from 'react';
import {
  BriefcaseBusiness,
  CheckCircle2,
  ChevronsUpDown,
  FileText,
  Loader2,
  Plus,
  Search,
  UploadCloud,
  UserPlus,
} from 'lucide-react';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Panel,
  Select,
  Sheet,
  TableShell,
  Textarea,
  cx,
} from '../../shared/ui/TaaliPrimitives';

export const parseCollection = (data) => (Array.isArray(data) ? data : (data?.items || []));
const formatDateTime = (value) => (value ? new Date(value).toLocaleString() : '—');

export const trimOrUndefined = (value) => {
  const trimmed = String(value || '').trim();
  return trimmed.length > 0 ? trimmed : undefined;
};

const statusVariant = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized.includes('interview') || normalized.includes('review')) return 'purple';
  if (normalized.includes('reject') || normalized.includes('decline')) return 'warning';
  if (normalized.includes('offer') || normalized.includes('hired')) return 'success';
  return 'muted';
};

export const getErrorMessage = (err, fallback) => err?.response?.data?.detail || fallback;

export const SearchInput = ({ value, onChange, placeholder }) => (
  <div className="relative">
    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
    <Input
      type="text"
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      className="pl-9"
    />
  </div>
);

export const RolesList = ({ roles, selectedRoleId, loading, error, onSelectRole, onCreateRole }) => (
  <Panel className="p-4">
    <div className="mb-4 flex items-center justify-between">
      <h2 className="text-sm font-semibold uppercase tracking-[0.08em] text-gray-600">Roles</h2>
      <Button type="button" variant="ghost" size="sm" onClick={onCreateRole}>
        <Plus size={14} />
        New
      </Button>
    </div>

    {loading ? (
      <Card className="px-3 py-4">
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <Loader2 size={14} className="animate-spin" />
          Loading roles...
        </div>
      </Card>
    ) : null}

    {!loading && error ? (
      <Card className="border-red-200 bg-red-50 px-3 py-3 text-sm text-red-700">
        {error}
      </Card>
    ) : null}

    {!loading && !error && roles.length === 0 ? (
      <EmptyState
        title="No roles yet"
        description="Create your first role to start adding candidates."
        action={(
          <Button type="button" variant="primary" size="sm" onClick={onCreateRole}>
            <Plus size={14} />
            Create your first role
          </Button>
        )}
      />
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
                className={cx(
                  'w-full text-left border-2 px-3 py-3 transition',
                  selected
                    ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)]'
                    : 'border-[var(--taali-border-muted)] bg-[var(--taali-surface)] hover:border-[var(--taali-border)]'
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-[var(--taali-text)]">{role.name}</p>
                    <p className="mt-0.5 text-xs text-[var(--taali-muted)]">
                      {role.applications_count || 0} candidate{(role.applications_count || 0) === 1 ? '' : 's'}
                    </p>
                  </div>
                  <ChevronsUpDown size={14} className="mt-0.5 shrink-0 text-gray-400" />
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant={role.job_spec_filename ? 'success' : 'warning'}>
                    {role.job_spec_filename ? 'Spec uploaded' : 'No spec'}
                  </Badge>
                  <Badge variant="purple">Tasks: {role.tasks_count || 0}</Badge>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    ) : null}
  </Panel>
);

export const RoleSummaryHeader = ({ role, roleTasks, onEditRole }) => {
  if (!role) return null;
  return (
    <Panel className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h2 className="text-2xl font-bold tracking-tight text-[var(--taali-text)]">{role.name}</h2>
          {role.description ? <p className="text-sm text-[var(--taali-muted)]">{role.description}</p> : null}
        </div>
        <Button type="button" variant="secondary" size="sm" onClick={onEditRole}>
          Edit role
        </Button>
      </div>
      <Card className="mt-4 p-3 bg-[#faf8ff]">
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
                  <Badge key={task.id} variant="muted">{task.name}</Badge>
                ))}
              </div>
            ) : (
              <span className="text-gray-500">No linked tasks</span>
            )}
          </div>
        </div>
      </Card>
    </Panel>
  );
};

export const CandidatesTable = ({
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
      <Panel className="px-4 py-10 text-center text-sm text-gray-500">
        <div className="inline-flex items-center gap-2">
          <Loader2 size={15} className="animate-spin" />
          Loading candidates...
        </div>
      </Panel>
    );
  }

  if (error) {
    return (
      <Panel className="px-4 py-10 text-center text-sm border-red-200 bg-red-50 text-red-700">
        {error}
      </Panel>
    );
  }

  if (filtered.length === 0) {
    return (
      <EmptyState
        title="No candidates yet"
        description="Add your first candidate to this role and start assessments."
        action={(
          <Button type="button" variant="primary" size="sm" onClick={onAddCandidate}>
            <UserPlus size={15} />
            Add candidate
          </Button>
        )}
      />
    );
  }

  return (
    <TableShell>
      <table className="min-w-[760px]">
        <thead>
          <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
            <th className="px-4 py-3">Name</th>
            <th className="px-4 py-3">Email</th>
            <th className="px-4 py-3">Position</th>
            <th className="px-4 py-3">CV match</th>
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
                <tr className="align-top">
                  <td className="px-4 py-3 text-sm font-semibold text-gray-900">
                    {app.candidate_name || app.candidate_email}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.candidate_email}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.candidate_position || '—'}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    {typeof app.cv_match_score === 'number'
                      ? `${app.cv_match_score.toFixed(1)}/10`
                      : (
                        app.cv_filename
                          ? (app.cv_match_details?.error ? 'Unavailable' : 'Pending')
                          : '—'
                      )}
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={statusVariant(app.status)}>{app.status || 'applied'}</Badge>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{formatDateTime(app.updated_at || app.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => onViewCandidate(app)}
                        disabled={viewingApplicationId === app.id}
                      >
                        {viewingApplicationId === app.id ? 'Loading...' : 'View'}
                      </Button>
                      {canCreateAssessment ? (
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
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
                        >
                          Create assessment
                        </Button>
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
                  <tr className="bg-[#faf8ff]">
                    <td colSpan={7} className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Select
                          value={selectedTask}
                          onChange={(event) => {
                            setTaskByApplication((prev) => ({ ...prev, [app.id]: event.target.value }));
                          }}
                          className="min-w-[240px]"
                        >
                          <option value="">Select task...</option>
                          {roleTasks.map((task) => (
                            <option key={task.id} value={task.id}>{task.name}</option>
                          ))}
                        </Select>
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          onClick={async () => {
                            const success = await onCreateAssessment(app, selectedTask);
                            if (success) setComposerApplicationId(null);
                          }}
                          disabled={!selectedTask || creatingAssessmentId === app.id}
                        >
                          {creatingAssessmentId === app.id ? 'Creating...' : 'Send assessment'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setComposerApplicationId(null)}
                        >
                          Cancel
                        </Button>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </TableShell>
  );
};

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
        <Card className="mb-4 border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </Card>
      ) : null}

      {step === 1 ? (
        <div className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-gray-800">Role name *</span>
            <Input
              type="text"
              value={name}
              onBlur={() => setNameTouched(true)}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Senior Backend Engineer"
              className={!hasValidName && nameTouched ? '!border-red-400 !bg-red-50' : ''}
            />
            {!hasValidName && nameTouched ? (
              <span className="mt-1 block text-xs text-red-700">Role name is required.</span>
            ) : null}
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-gray-800">Description</span>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Optional summary for recruiters."
              className="min-h-[110px]"
            />
          </label>
        </div>
      ) : null}

      {step === 2 ? (
        <div className="space-y-4">
          <Card className="bg-[#faf8ff] p-4">
            <p className="text-sm font-medium text-gray-900">Upload job spec (optional but recommended)</p>
            <p className="mt-1 text-xs text-gray-600">
              Adding a spec now lets recruiters add candidates without friction.
            </p>
          </Card>
          {role?.job_spec_filename ? (
            <Card className="px-3 py-2 text-sm text-gray-700">
              Current file: <span className="font-medium">{role.job_spec_filename}</span>
            </Card>
          ) : null}
          <label className="block border-2 border-dashed border-[var(--taali-border-muted)] p-5 text-center transition hover:border-[var(--taali-border)]">
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
                      'flex cursor-pointer items-start gap-3 border-2 px-3 py-2',
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

export const CandidateSheet = ({
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
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            type="button"
            variant="primary"
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
          >
            {saving ? 'Saving...' : 'Add candidate'}
          </Button>
        </div>
      )}
    >
      {error ? (
        <Card className="mb-4 border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </Card>
      ) : null}

      <div className="space-y-4">
        <Card className="bg-[#faf8ff] px-3 py-2 text-sm text-gray-700">
          <span className="font-medium">Role:</span> {role?.name || 'No role selected'}
        </Card>

        {!hasRoleSpec ? (
          <Card className="border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Upload a role job spec before adding candidates.
          </Card>
        ) : null}

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Email *</span>
          <Input
            type="email"
            value={email}
            onBlur={() => setTouched((prev) => ({ ...prev, email: true }))}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="candidate@company.com"
            className={touched.email && !validEmail ? '!border-red-400 !bg-red-50' : ''}
          />
          {touched.email && !validEmail ? (
            <span className="mt-1 block text-xs text-red-700">Candidate email is required.</span>
          ) : null}
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Candidate name *</span>
          <Input
            type="text"
            value={name}
            onBlur={() => setTouched((prev) => ({ ...prev, name: true }))}
            onChange={(event) => setName(event.target.value)}
            placeholder="Jane Doe"
            className={touched.name && !validName ? '!border-red-400 !bg-red-50' : ''}
          />
          {touched.name && !validName ? (
            <span className="mt-1 block text-xs text-red-700">Candidate name is required.</span>
          ) : null}
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-gray-800">Candidate position</span>
          <Input
            type="text"
            value={position}
            onChange={(event) => setPosition(event.target.value)}
            placeholder="Defaults to role title"
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
            className={cx(
              'block border-2 border-dashed p-5 text-center transition',
              dragActive
                ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)]'
                : 'border-[var(--taali-border-muted)] bg-[var(--taali-surface)] hover:border-[var(--taali-border)]'
            )}
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

export const EmptyRoleDetail = ({ onCreateRole }) => (
  <EmptyState
    title="No role selected"
    description="Create a role to start managing candidates."
    action={(
      <Button type="button" variant="primary" size="sm" onClick={onCreateRole}>
        <Plus size={15} />
        Create your first role
      </Button>
    )}
  />
);
