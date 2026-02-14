import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, Plus, UserPlus } from 'lucide-react';
import * as apiClient from '../../shared/api';
import { Button, PageContainer, PageHeader, Panel, Select, TableShell } from '../../shared/ui/TaaliPrimitives';

import {
  CandidateSheet,
  CandidatesTable,
  EmptyRoleDetail,
  RoleSheet,
  RoleSummaryHeader,
  RolesList,
  SearchInput,
  getErrorMessage,
  parseCollection,
  trimOrUndefined,
} from './CandidatesUI';

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
  const [legacyAssessments, setLegacyAssessments] = useState([]);
  const [loadingLegacyAssessments, setLoadingLegacyAssessments] = useState(true);
  const [legacyAssessmentsError, setLegacyAssessmentsError] = useState('');

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

  const loadLegacyAssessments = useCallback(async () => {
    if (!assessmentsApi?.list) {
      setLegacyAssessments([]);
      setLoadingLegacyAssessments(false);
      return;
    }
    setLoadingLegacyAssessments(true);
    setLegacyAssessmentsError('');
    try {
      const res = await assessmentsApi.list({ limit: 200, offset: 0 });
      const items = parseCollection(res.data);
      const deduped = [];
      const seen = new Set();
      for (const item of items) {
        if (item.application_id) continue;
        const key = `${item.role_id || 'none'}:${item.candidate_id || item.candidate_email || item.id}`;
        if (seen.has(key)) continue;
        seen.add(key);
        deduped.push(item);
      }
      setLegacyAssessments(deduped);
    } catch {
      setLegacyAssessments([]);
      setLegacyAssessmentsError('Failed to load dashboard candidates.');
    } finally {
      setLoadingLegacyAssessments(false);
    }
  }, [assessmentsApi]);

  useEffect(() => {
    loadRoles();
    loadTasks();
    loadLegacyAssessments();
  }, [loadRoles, loadTasks, loadLegacyAssessments]);

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
    position: fallbackApp?.candidate_position || assessment.role_name || '',
    completedDate: assessment.completed_at ? new Date(assessment.completed_at).toLocaleDateString() : null,
    breakdown: assessment.breakdown || null,
    prompts: assessment.prompt_count ?? 0,
    promptsList: assessment.prompts_list || [],
    timeline: assessment.timeline || [],
    results: assessment.results || [],
    token: assessment.token,
    _raw: assessment,
  });

  const legacyCandidates = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return legacyAssessments.filter((assessment) => {
      const matchesRole = !selectedRoleId
        || !assessment.role_id
        || String(assessment.role_id) === String(selectedRoleId);
      if (!matchesRole) return false;
      if (!query) return true;
      const haystack = [
        assessment.candidate_name,
        assessment.candidate_email,
        assessment.role_name,
        assessment.status,
        assessment.task_name,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [legacyAssessments, searchQuery, selectedRoleId]);

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

  const legacyCandidatesSection = (
    <Panel className="p-4">
      <div className="mb-3">
        <h3 className="text-sm font-semibold uppercase tracking-[0.08em] text-gray-600">
          Dashboard candidates
        </h3>
        <p className="mt-1 text-sm text-gray-600">
          Candidates that exist from the assessment flow but are not attached to a role application.
        </p>
      </div>
      {loadingLegacyAssessments ? (
        <p className="text-sm text-gray-500">Loading dashboard candidates...</p>
      ) : null}
      {!loadingLegacyAssessments && legacyAssessmentsError ? (
        <p className="text-sm text-red-700">{legacyAssessmentsError}</p>
      ) : null}
      {!loadingLegacyAssessments && !legacyAssessmentsError && legacyCandidates.length === 0 ? (
        <p className="text-sm text-gray-500">No dashboard-only candidates found.</p>
      ) : null}
      {!loadingLegacyAssessments && !legacyAssessmentsError && legacyCandidates.length > 0 ? (
        <TableShell>
          <table className="min-w-[720px]">
            <thead>
              <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Role</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Last activity</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {legacyCandidates.map((assessment) => (
                <tr key={assessment.id}>
                  <td className="px-4 py-3 text-sm font-semibold text-gray-900">
                    {(assessment.candidate_name || assessment.candidate_email || 'Unknown').trim()}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{assessment.candidate_email || '—'}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{assessment.role_name || 'Unassigned role'}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{assessment.status || 'pending'}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    {assessment.updated_at
                      ? new Date(assessment.updated_at).toLocaleString()
                      : (assessment.created_at ? new Date(assessment.created_at).toLocaleString() : '—')}
                  </td>
                  <td className="px-4 py-3">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => onViewCandidate(mapAssessmentForDetail(assessment))}
                    >
                      View
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableShell>
      ) : null}
    </Panel>
  );

  return (
    <div>
      <NavComponent currentPage="candidates" onNavigate={onNavigate} />

      <PageContainer>
        <PageHeader
          className="mb-6"
          title="Candidates"
          subtitle="Manage role pipelines and assessments in one place."
          actions={(
            <>
              <Button type="button" variant="primary" onClick={() => handleOpenRoleSheet('create')}>
                <Plus size={15} />
                New role
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={!selectedRoleId}
                onClick={() => {
                  setCandidateSheetError('');
                  setCandidateSheetOpen(true);
                }}
              >
                <UserPlus size={15} />
                Add candidate
              </Button>
            </>
          )}
        >
          <div className="grid gap-3 md:grid-cols-[280px_minmax(0,1fr)]">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Active role
              </span>
              <Select
                aria-label="Active role"
                value={selectedRoleId}
                onChange={(event) => setSelectedRoleId(event.target.value)}
                disabled={loadingRoles || roles.length === 0}
              >
                {roles.length === 0 ? <option value="">No roles</option> : null}
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>{role.name}</option>
                ))}
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Search candidates
              </span>
              <SearchInput
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search by name, email, position, or status"
              />
            </label>
          </div>
        </PageHeader>

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
              <>
                <EmptyRoleDetail onCreateRole={() => handleOpenRoleSheet('create')} />
                {legacyCandidatesSection}
              </>
            ) : (
              <>
                <RoleSummaryHeader
                  role={selectedRole}
                  roleTasks={roleTasks}
                  onEditRole={() => handleOpenRoleSheet('edit')}
                />
                {loadingTasks ? (
                  <Panel className="px-4 py-3 text-sm text-gray-600 bg-[#faf8ff]">
                    Loading tasks catalog...
                  </Panel>
                ) : null}
                {roleContextError ? (
                  <div className="inline-flex items-center gap-2 border-2 border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
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
                {legacyCandidatesSection}
              </>
            )}
          </div>
        </div>
      </PageContainer>

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
