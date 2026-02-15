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
import { AssessmentInviteSheet } from './AssessmentInviteSheet';

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
  const [sortBy, setSortBy] = useState('rank_score');
  const [sortOrder, setSortOrder] = useState('desc');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [minRankScore, setMinRankScore] = useState('');
  const [minWorkableScore, setMinWorkableScore] = useState('');
  const [minCvMatchScore, setMinCvMatchScore] = useState('');

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
  const [inviteSheetOpen, setInviteSheetOpen] = useState(false);
  const [inviteDraft, setInviteDraft] = useState(null);

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
      const appParams = {
        sort_by: sortBy,
        sort_order: sortOrder,
      };
      if (sourceFilter !== 'all') appParams.source = sourceFilter;
      if (minRankScore !== '') appParams.min_rank_score = Number(minRankScore);
      if (minWorkableScore !== '') appParams.min_workable_score = Number(minWorkableScore);
      if (minCvMatchScore !== '') appParams.min_cv_match_score = Number(minCvMatchScore);
      const [tasksRes, applicationsRes] = await Promise.all([
        rolesApi?.listTasks ? rolesApi.listTasks(roleId) : Promise.resolve({ data: [] }),
        rolesApi?.listApplications ? rolesApi.listApplications(roleId, appParams) : Promise.resolve({ data: [] }),
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
  }, [rolesApi, sortBy, sortOrder, sourceFilter, minRankScore, minWorkableScore, minCvMatchScore]);

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
      const res = await rolesApi.createAssessment(application.id, { task_id: taskNumber });
      await loadRoleContext(selectedRoleId);
      const created = res?.data || {};
      const candidateEmail = created.candidate_email || application?.candidate_email || '';
      const candidateName = created.candidate_name || application?.candidate_name || '';
      const taskName = (
        roleTasks.find((task) => Number(task.id) === taskNumber)?.name
        || created.task_name
        || selectedRole?.name
        || 'Technical assessment'
      );
      let link = '';
      if (created.id && created.token) {
        link = `${window.location.origin}/assessment/${created.id}?token=${created.token}`;
      } else if (created.token) {
        link = `${window.location.origin}/assess/${created.token}`;
      }
      const subject = `Technical Assessment Invitation — ${taskName}`;
      const body = (
        `Hi ${candidateName || 'there'},\n\n`
        + `You've been invited to complete a technical assessment (${taskName}).\n\n`
        + `Start here:\n${link}\n\n`
        + `Thanks,\n${selectedRole?.name || 'TAALI'}\n`
      );
      setInviteDraft({
        to: candidateEmail,
        subject,
        body,
        link,
        inviteChannel: created.invite_channel || null,
        inviteSentAt: created.invite_sent_at || null,
      });
      setInviteSheetOpen(true);
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
          <div className="mt-3 grid gap-3 md:grid-cols-6">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Sort by
              </span>
              <Select aria-label="Sort by" value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
                <option value="rank_score">Rank score</option>
                <option value="workable_score">Workable score</option>
                <option value="cv_match_score">CV match score</option>
                <option value="created_at">Created at</option>
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Order
              </span>
              <Select aria-label="Sort order" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)}>
                <option value="desc">Descending</option>
                <option value="asc">Ascending</option>
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Source
              </span>
              <Select aria-label="Source filter" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
                <option value="all">All</option>
                <option value="manual">Manual</option>
                <option value="workable">Workable</option>
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Min rank
              </span>
              <input
                type="number"
                min="0"
                max="10"
                step="0.1"
                value={minRankScore}
                onChange={(event) => setMinRankScore(event.target.value)}
                className="w-full border border-[var(--taali-border-muted)] rounded-lg px-3 py-2 text-sm"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Min Workable
              </span>
              <input
                type="number"
                min="0"
                max="10"
                step="0.1"
                value={minWorkableScore}
                onChange={(event) => setMinWorkableScore(event.target.value)}
                className="w-full border border-[var(--taali-border-muted)] rounded-lg px-3 py-2 text-sm"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Min CV
              </span>
              <input
                type="number"
                min="0"
                max="10"
                step="0.1"
                value={minCvMatchScore}
                onChange={(event) => setMinCvMatchScore(event.target.value)}
                className="w-full border border-[var(--taali-border-muted)] rounded-lg px-3 py-2 text-sm"
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
              <EmptyRoleDetail onCreateRole={() => handleOpenRoleSheet('create')} />
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

      <AssessmentInviteSheet
        open={inviteSheetOpen}
        onClose={() => setInviteSheetOpen(false)}
        draft={inviteDraft}
      />
    </div>
  );
};
