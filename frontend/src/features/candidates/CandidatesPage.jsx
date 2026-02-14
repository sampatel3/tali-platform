import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, Plus, UserPlus } from 'lucide-react';
import * as apiClient from '../../shared/api';
import { Button, PageContainer, PageHeader, Panel, Select } from '../../shared/ui/TaaliPrimitives';

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

const UNASSIGNED_ROLE_ID = '__unassigned_role__';

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

  const unassignedRoleApplications = useMemo(() => {
    const deduped = [];
    const seen = new Set();
    for (const assessment of legacyAssessments) {
      if (assessment.application_id || assessment.role_id) continue;
      const key = String(assessment.candidate_id || assessment.candidate_email || assessment.id);
      if (seen.has(key)) continue;
      seen.add(key);
      deduped.push({
        id: `unassigned-${assessment.id}`,
        candidate_id: assessment.candidate_id,
        candidate_email: assessment.candidate_email || '',
        candidate_name: (assessment.candidate_name || assessment.candidate_email || '').trim() || 'Unknown',
        candidate_position: assessment.role_name || '',
        status: assessment.status || 'pending',
        cv_filename: assessment.candidate_cv_filename || assessment.cv_filename || null,
        cv_match_score: assessment.cv_job_match_score,
        cv_match_details: assessment.cv_job_match_details || null,
        created_at: assessment.created_at,
        updated_at: assessment.updated_at || assessment.completed_at || assessment.created_at,
        _sourceAssessment: assessment,
      });
    }
    return deduped;
  }, [legacyAssessments]);

  const rolesWithUnassigned = useMemo(() => {
    if (unassignedRoleApplications.length === 0) return roles;
    return [
      ...roles,
      {
        id: UNASSIGNED_ROLE_ID,
        name: 'Unassigned role',
        description: 'Candidates without an assigned role.',
        job_spec_filename: null,
        tasks_count: 0,
        applications_count: unassignedRoleApplications.length,
      },
    ];
  }, [roles, unassignedRoleApplications]);

  const selectedRole = useMemo(
    () => rolesWithUnassigned.find((role) => String(role.id) === String(selectedRoleId)) || null,
    [rolesWithUnassigned, selectedRoleId]
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
      return;
    }
    try {
      const res = await assessmentsApi.list({ limit: 200, offset: 0 });
      const items = parseCollection(res.data);
      setLegacyAssessments(items);
    } catch {
      setLegacyAssessments([]);
    }
  }, [assessmentsApi]);

  useEffect(() => {
    loadRoles();
    loadTasks();
    loadLegacyAssessments();
  }, [loadRoles, loadTasks, loadLegacyAssessments]);

  useEffect(() => {
    if (!selectedRoleId) {
      loadRoleContext(selectedRoleId);
      return;
    }
    if (String(selectedRoleId) === UNASSIGNED_ROLE_ID) {
      setRoleTasks([]);
      setRoleContextError('');
      setLoadingRoleContext(false);
      setRoleApplications(unassignedRoleApplications);
      return;
    }
    loadRoleContext(selectedRoleId);
  }, [selectedRoleId, loadRoleContext, unassignedRoleApplications]);

  useEffect(() => {
    setSelectedRoleId((current) => {
      if (current && rolesWithUnassigned.some((role) => String(role.id) === String(current))) {
        return current;
      }
      return rolesWithUnassigned.length > 0 ? String(rolesWithUnassigned[0].id) : '';
    });
  }, [rolesWithUnassigned]);

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
      if (application?._sourceAssessment) {
        onViewCandidate(mapAssessmentForDetail(application._sourceAssessment, application));
        return;
      }
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
    if (String(selectedRoleId) === UNASSIGNED_ROLE_ID) {
      alert('Assign this candidate to a role before creating an assessment.');
      return false;
    }
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
                disabled={!selectedRoleId || selectedRoleId === UNASSIGNED_ROLE_ID}
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
                disabled={loadingRoles || rolesWithUnassigned.length === 0}
              >
                {rolesWithUnassigned.length === 0 ? <option value="">No roles</option> : null}
                {rolesWithUnassigned.map((role) => (
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
            roles={rolesWithUnassigned}
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
                {String(selectedRole.id) === UNASSIGNED_ROLE_ID ? (
                  <Panel className="p-5">
                    <h2 className="text-2xl font-bold tracking-tight text-[var(--taali-text)]">Unassigned role</h2>
                    <p className="mt-1 text-sm text-[var(--taali-muted)]">
                      Candidates that are not attached to a role application yet.
                    </p>
                  </Panel>
                ) : (
                  <RoleSummaryHeader
                    role={selectedRole}
                    roleTasks={roleTasks}
                    onEditRole={() => handleOpenRoleSheet('edit')}
                  />
                )}
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
                  canCreateAssessment={Boolean(rolesApi?.createAssessment) && selectedRoleId !== UNASSIGNED_ROLE_ID}
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
      </PageContainer>

      <RoleSheet
        open={roleSheetOpen}
        mode={roleSheetMode}
        role={roleSheetMode === 'edit' && selectedRole?.id !== UNASSIGNED_ROLE_ID ? selectedRole : null}
        roleTasks={roleSheetMode === 'edit' && selectedRole?.id !== UNASSIGNED_ROLE_ID ? roleTasks : []}
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
