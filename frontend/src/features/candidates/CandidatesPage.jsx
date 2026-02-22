import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Plus, UserPlus } from 'lucide-react';
import * as apiClient from '../../shared/api';
import { Button, Input, PageContainer, PageHeader, Panel, Select } from '../../shared/ui/TaaliPrimitives';

import { useToast } from '../../context/ToastContext';
import {
  CandidateCvSidebar,
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

const DEFAULT_INVITE_TEMPLATE = (
  'Hi {{candidate_name}},\n\n'
  + "You've been invited to complete a technical assessment ({{task_name}}).\n\n"
  + 'Start here:\n{{assessment_link}}\n\n'
  + 'Thanks,\n{{organization_name}}\n'
);

const clampDurationMinutes = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 30;
  return Math.max(15, Math.min(180, Math.round(numeric)));
};

const applyInviteTemplate = (template, vars) => {
  const source = String(template || '').trim();
  if (!source) return '';
  return source.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, key) => {
    const value = vars?.[key];
    return value == null ? '' : String(value);
  });
};

export const CandidatesPage = ({ onNavigate, onViewCandidate, NavComponent }) => {
  const { showToast } = useToast();
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const tasksApi = apiClient.tasks;
  const assessmentsApi = apiClient.assessments;
  const orgsApi = 'organizations' in apiClient ? apiClient.organizations : null;

  const [roles, setRoles] = useState([]);
  const [selectedRoleId, setSelectedRoleId] = useState('');
  const [roleTasks, setRoleTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState('cv_match_score');
  const [sortOrder, setSortOrder] = useState('desc');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
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
  const [uploadingCvId, setUploadingCvId] = useState(null);
  const [viewingApplicationId, setViewingApplicationId] = useState(null);
  const [generatingTaaliId, setGeneratingTaaliId] = useState(null);
  const [inviteSheetOpen, setInviteSheetOpen] = useState(false);
  const [inviteDraft, setInviteDraft] = useState(null);
  const [cvSidebarApplicationId, setCvSidebarApplicationId] = useState(null);
  const [batchScoring, setBatchScoring] = useState(null);
  const [fetchCvsProgress, setFetchCvsProgress] = useState(null);
  const [interviewFocusGeneratingRoleId, setInterviewFocusGeneratingRoleId] = useState(null);
  const [orgPreferences, setOrgPreferences] = useState({
    defaultAssessmentDurationMinutes: 30,
    inviteEmailTemplate: DEFAULT_INVITE_TEMPLATE,
    organizationName: 'TAALI',
  });
  const interviewFocusAutoAttemptedRef = useRef(new Set());

  const selectedRole = useMemo(
    () => roles.find((role) => String(role.id) === String(selectedRoleId)) || null,
    [roles, selectedRoleId]
  );

  const statusOptions = useMemo(() => {
    const statuses = new Set();
    roleApplications.forEach((application) => {
      statuses.add((application.status || 'applied').toLowerCase());
    });
    return ['all', ...Array.from(statuses).sort()];
  }, [roleApplications]);

  const activeFilterCount = useMemo(() => (
    [
      searchQuery.trim() !== '',
      sortBy !== 'cv_match_score',
      sortOrder !== 'desc',
      sourceFilter !== 'all',
      statusFilter !== 'all',
      minCvMatchScore !== '',
    ].filter(Boolean).length
  ), [
    searchQuery,
    sortBy,
    sortOrder,
    sourceFilter,
    statusFilter,
    minCvMatchScore,
  ]);

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
        include_cv_text: true,
      };
      if (sourceFilter !== 'all') appParams.source = sourceFilter;
      if (statusFilter !== 'all') appParams.status = statusFilter;
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
  }, [rolesApi, sortBy, sortOrder, sourceFilter, statusFilter, minCvMatchScore]);

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
    let cancelled = false;
    const loadOrgPreferences = async () => {
      if (!orgsApi?.get) return;
      try {
        const res = await orgsApi.get();
        if (cancelled) return;
        const data = res?.data || {};
        setOrgPreferences({
          defaultAssessmentDurationMinutes: clampDurationMinutes(data.default_assessment_duration_minutes),
          inviteEmailTemplate: String(data.invite_email_template || '').trim() || DEFAULT_INVITE_TEMPLATE,
          organizationName: String(data.name || 'TAALI').trim() || 'TAALI',
        });
      } catch {
        if (cancelled) return;
        setOrgPreferences((prev) => ({ ...prev }));
      }
    };
    loadOrgPreferences();
    return () => {
      cancelled = true;
    };
  }, [orgsApi]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') loadRoles();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loadRoles]);

  useEffect(() => {
    loadRoleContext(selectedRoleId);
  }, [selectedRoleId, loadRoleContext]);

  useEffect(() => {
    if (statusFilter === 'all') return;
    const hasSelectedStatus = roleApplications.some(
      (application) => (application.status || 'applied').toLowerCase() === statusFilter
    );
    if (!hasSelectedStatus) setStatusFilter('all');
  }, [roleApplications, statusFilter]);

  const resetFilters = useCallback(() => {
    setSearchQuery('');
    setSortBy('cv_match_score');
    setSortOrder('desc');
    setSourceFilter('all');
    setStatusFilter('all');
    setMinCvMatchScore('');
  }, []);

  const handleSortChange = useCallback((nextSortBy, nextSortOrder) => {
    setSortBy(nextSortBy);
    setSortOrder(nextSortOrder);
  }, []);

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

  const generateInterviewFocusForRole = useCallback(async (roleId, { silent = false } = {}) => {
    if (!rolesApi?.regenerateInterviewFocus || !roleId) return false;
    setInterviewFocusGeneratingRoleId(String(roleId));
    try {
      const res = await rolesApi.regenerateInterviewFocus(roleId);
      const data = res?.data || {};
      if (data.interview_focus_generated) {
        await Promise.all([
          loadRoles(roleId),
          loadRoleContext(roleId),
        ]);
        if (!silent) {
          showToast('Interview focus pointers generated.', 'success');
        }
        return true;
      }
      if (data.interview_focus_error && !silent) {
        showToast(data.interview_focus_error, 'error');
      }
      return false;
    } catch (err) {
      if (!silent) {
        showToast(getErrorMessage(err, 'Failed to generate interview focus.'), 'error');
      }
      return false;
    } finally {
      setInterviewFocusGeneratingRoleId((current) => (
        String(current) === String(roleId) ? null : current
      ));
    }
  }, [loadRoleContext, loadRoles, rolesApi, showToast]);

  const handleRoleSubmit = async ({ name, description, additionalRequirements, jobSpecFile, taskIds }) => {
    if (!rolesApi) return;
    setSavingRole(true);
    setRoleSheetError('');
    try {
      let activeRoleId = selectedRoleId;
      let shouldAutoGenerateInterviewFocus = false;
      if (roleSheetMode === 'create') {
        const createRes = await rolesApi.create({
          name,
          description: trimOrUndefined(description),
          additional_requirements: trimOrUndefined(additionalRequirements),
        });
        activeRoleId = String(createRes.data.id);
      } else if (rolesApi.update && selectedRoleId) {
        await rolesApi.update(selectedRoleId, {
          name,
          description: trimOrUndefined(description),
          additional_requirements: trimOrUndefined(additionalRequirements),
        });
        activeRoleId = String(selectedRoleId);
      }

      if (jobSpecFile && rolesApi.uploadJobSpec) {
        await rolesApi.uploadJobSpec(activeRoleId, jobSpecFile);
        shouldAutoGenerateInterviewFocus = true;
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

      if (shouldAutoGenerateInterviewFocus) {
        interviewFocusAutoAttemptedRef.current.add(String(activeRoleId));
        void generateInterviewFocusForRole(activeRoleId, { silent: true });
      }
    } catch (err) {
      setRoleSheetError(getErrorMessage(err, 'Failed to save role.'));
    } finally {
      setSavingRole(false);
    }
  };

  const handleCandidateSubmit = async ({ email, name, position, cvFile }) => {
    if (!rolesApi?.createApplication || !selectedRoleId) return;
    setAddingCandidate(true);
    setCandidateSheetError('');
    try {
      const res = await rolesApi.createApplication(selectedRoleId, {
        candidate_email: email,
        candidate_name: name,
        candidate_position: trimOrUndefined(position),
      });
      if (cvFile && rolesApi?.uploadApplicationCv) {
        await rolesApi.uploadApplicationCv(res.data.id, cvFile);
      }

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

  const handleUploadApplicationCv = useCallback(async (application, file) => {
    if (!rolesApi?.uploadApplicationCv || !application?.id || !file) return;
    setUploadingCvId(application.id);
    try {
      await rolesApi.uploadApplicationCv(application.id, file);
      await loadRoleContext(selectedRoleId);
      showToast('CV uploaded.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to upload CV.'), 'error');
    } finally {
      setUploadingCvId(null);
    }
  }, [loadRoleContext, rolesApi, selectedRoleId, showToast]);

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
        showToast('No assessments found for this candidate yet.', 'info');
        return;
      }
      onViewCandidate(mapAssessmentForDetail(items[0], application));
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to load candidate details.'), 'error');
    } finally {
      setViewingApplicationId(null);
    }
  };

  const handleCreateAssessment = async (application, taskId) => {
    if (!rolesApi?.createAssessment) return false;
    const taskNumber = Number(taskId);
    if (!taskNumber) {
      showToast('Select a task first.', 'info');
      return false;
    }
    setCreatingAssessmentId(application.id);
    try {
      if (!application?.cv_filename) {
        showToast('No CV uploaded — Role fit scoring will show N/A.', 'info');
      }
      const durationMinutes = clampDurationMinutes(orgPreferences.defaultAssessmentDurationMinutes);
      const res = await rolesApi.createAssessment(application.id, {
        task_id: taskNumber,
        duration_minutes: durationMinutes,
      });
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
      const fallbackBody = (
        `Hi ${candidateName || 'there'},\n\n`
        + `You've been invited to complete a technical assessment (${taskName}).\n\n`
        + `Start here:\n${link}\n\n`
        + `Thanks,\n${orgPreferences.organizationName || selectedRole?.name || 'TAALI'}\n`
      );
      const body = applyInviteTemplate(orgPreferences.inviteEmailTemplate, {
        candidate_name: candidateName || 'there',
        candidate_email: candidateEmail || '',
        assessment_link: link,
        task_name: taskName,
        role_name: selectedRole?.name || '',
        organization_name: orgPreferences.organizationName || 'TAALI',
      }) || fallbackBody;
      setInviteDraft({
        to: candidateEmail,
        subject,
        body,
        link,
        noCv: !application?.cv_filename,
        inviteChannel: created.invite_channel || null,
        inviteSentAt: created.invite_sent_at || null,
      });
      setInviteSheetOpen(true);
      return true;
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to create assessment.'), 'error');
      return false;
    } finally {
      setCreatingAssessmentId(null);
    }
  };

  const handleGenerateTaaliCvAi = useCallback(async (application) => {
    if (!rolesApi?.generateTaaliCvAi) return;
    setGeneratingTaaliId(application.id);
    try {
      const res = await rolesApi.generateTaaliCvAi(application.id);
      const updated = res?.data;
      if (updated && updated.id) {
        setRoleApplications((prev) =>
          prev.map((app) => (Number(app.id) === Number(updated.id) ? { ...app, ...updated } : app))
        );
      }
    } catch (err) {
      const msg = getErrorMessage(err, 'Failed to generate TAALI score.');
      showToast(msg, err?.response?.status === 404 ? 'info' : 'error');
    } finally {
      setGeneratingTaaliId(null);
    }
  }, [rolesApi, showToast]);

  const handleFetchCvs = useCallback(async () => {
    if (!rolesApi?.fetchCvs || !selectedRoleId) return;
    try {
      await rolesApi.fetchCvs(selectedRoleId);
      setFetchCvsProgress({ total: 0, fetched: 0, status: 'running' });

      const poll = setInterval(async () => {
        try {
          const statusRes = await rolesApi.fetchCvsStatus(selectedRoleId);
          const s = statusRes?.data || {};
          setFetchCvsProgress({ total: s.total || 0, fetched: s.fetched || 0, status: s.status || 'running' });
          if (s.status === 'completed' || s.status === 'failed' || s.status === 'idle') {
            clearInterval(poll);
            setFetchCvsProgress(null);
            loadRoleContext(selectedRoleId);
            if (s.status === 'completed') {
              showToast(`Fetched ${s.fetched || 0} CVs from Workable.`, 'success');
            }
          }
        } catch {
          clearInterval(poll);
          setFetchCvsProgress(null);
        }
      }, 3000);
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to start CV fetch.'), 'error');
      setFetchCvsProgress(null);
    }
  }, [rolesApi, selectedRoleId, loadRoleContext, showToast]);

  const handleBatchScore = useCallback(async () => {
    if (!rolesApi?.batchScore || !selectedRoleId) return;
    try {
      const res = await rolesApi.batchScore(selectedRoleId);
      const data = res?.data || {};
      setBatchScoring({ total: data.total_unscored || data.total || 0, scored: 0, status: 'running' });

      // Poll for progress
      const poll = setInterval(async () => {
        try {
          const statusRes = await rolesApi.batchScoreStatus(selectedRoleId);
          const s = statusRes?.data || {};
          setBatchScoring({ total: s.total || 0, scored: s.scored || 0, status: s.status || 'running' });
          if (s.status === 'completed' || s.status === 'failed' || s.status === 'idle') {
            clearInterval(poll);
            setBatchScoring(null);
            loadRoleContext(selectedRoleId);
            if (s.status === 'completed') {
              showToast(`Scored ${s.scored || 0} candidates.`, 'success');
            }
          }
        } catch {
          clearInterval(poll);
          setBatchScoring(null);
        }
      }, 3000);
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to start batch scoring.'), 'error');
      setBatchScoring(null);
    }
  }, [rolesApi, selectedRoleId, loadRoleContext, showToast]);

  const handleRegenerateInterviewFocus = useCallback(async () => {
    if (!selectedRoleId) return;
    interviewFocusAutoAttemptedRef.current.delete(String(selectedRoleId));
    await generateInterviewFocusForRole(selectedRoleId);
  }, [generateInterviewFocusForRole, selectedRoleId]);

  useEffect(() => {
    if (!selectedRole || !rolesApi?.regenerateInterviewFocus) return;
    const roleId = String(selectedRole.id || '');
    if (!roleId) return;

    const focus = selectedRole.interview_focus || null;
    const hasInterviewFocus = Array.isArray(focus?.questions) && focus.questions.length > 0;
    const hasSpecText = Boolean(
      String(selectedRole.description || selectedRole.job_spec_text || '').trim()
    );
    const jobSpecReady = Boolean(selectedRole.job_spec_present || selectedRole.job_spec_filename || hasSpecText);

    if (!jobSpecReady || hasInterviewFocus) return;
    if (String(interviewFocusGeneratingRoleId) === roleId) return;
    if (interviewFocusAutoAttemptedRef.current.has(roleId)) return;

    interviewFocusAutoAttemptedRef.current.add(roleId);
    void generateInterviewFocusForRole(roleId, { silent: true });
  }, [generateInterviewFocusForRole, interviewFocusGeneratingRoleId, rolesApi, selectedRole]);

  const handleEnrichCandidate = useCallback(async (application) => {
    if (!rolesApi?.enrichApplication) return;
    try {
      const res = await rolesApi.enrichApplication(application.id);
      const updated = res?.data;
      if (updated && updated.id) {
        setRoleApplications((prev) =>
          prev.map((app) => (Number(app.id) === Number(updated.id) ? { ...app, ...updated } : app))
        );
      }
      showToast('Profile enriched from Workable.', 'success');
    } catch (err) {
      showToast(getErrorMessage(err, 'Failed to enrich candidate.'), 'error');
    }
  }, [rolesApi, showToast]);

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
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
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
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                Search candidates
              </span>
              <SearchInput
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search by name, email, position, or status"
              />
            </label>
          </div>
          <div className="mt-3 border border-[var(--taali-border-muted)] bg-[var(--taali-surface)] p-3">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                Sorting and filters
              </p>
              <div className="flex items-center gap-2 text-xs text-[var(--taali-muted)]">
                <span>{activeFilterCount > 0 ? `${activeFilterCount} active` : 'Default view'}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={resetFilters}
                  disabled={activeFilterCount === 0}
                >
                  Reset
                </Button>
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                  Sort by
                </span>
                <Select aria-label="Sort by" value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
                  <option value="cv_match_score">Taali AI (CV match /100)</option>
                  <option value="created_at">Added</option>
                </Select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                  Order
                </span>
                <Select aria-label="Sort order" value={sortOrder} onChange={(event) => setSortOrder(event.target.value)}>
                  <option value="desc">Descending</option>
                  <option value="asc">Ascending</option>
                </Select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                  Source
                </span>
                <Select aria-label="Source filter" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
                  <option value="all">All</option>
                  <option value="manual">Manual</option>
                  <option value="workable">Workable</option>
                </Select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                  Status
                </span>
                <Select aria-label="Status filter" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  {statusOptions.map((status) => (
                    <option key={status} value={status}>
                      {status === 'all'
                        ? 'All'
                        : status
                          .split(/[_\s-]+/)
                          .map((chunk) => chunk.charAt(0).toUpperCase() + chunk.slice(1))
                          .join(' ')}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                  Min taali
                </span>
                <Input
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  aria-label="Minimum CV match score"
                  placeholder="0"
                  value={minCvMatchScore}
                  onChange={(event) => setMinCvMatchScore(event.target.value)}
                />
              </label>
            </div>
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
            onRefresh={() => loadRoles()}
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
                  batchScoring={batchScoring}
                  onBatchScore={handleBatchScore}
                  onFetchCvs={rolesApi?.fetchCvs ? handleFetchCvs : null}
                  fetchCvsProgress={fetchCvsProgress}
                  interviewFocusGenerating={String(interviewFocusGeneratingRoleId) === String(selectedRoleId)}
                  onRegenerateInterviewFocus={rolesApi?.regenerateInterviewFocus ? handleRegenerateInterviewFocus : null}
                />
                {loadingTasks ? (
                  <Panel className="px-4 py-3">
                    <div className="space-y-2 animate-pulse">
                      <div className="h-3 w-32 rounded bg-[var(--taali-border)]" />
                      <div className="h-3 w-48 rounded bg-[var(--taali-border)]" />
                    </div>
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
                  statusFilter={statusFilter}
                  sortBy={sortBy}
                  sortOrder={sortOrder}
                  roleTasks={roleTasks}
                  canCreateAssessment={Boolean(rolesApi?.createAssessment)}
                  creatingAssessmentId={creatingAssessmentId}
                  viewingApplicationId={viewingApplicationId}
                  generatingTaaliId={generatingTaaliId}
                  onChangeSort={handleSortChange}
                  onAddCandidate={() => {
                    setCandidateSheetError('');
                    setCandidateSheetOpen(true);
                  }}
                  onViewCandidate={handleViewFromApplication}
                  onOpenCvSidebar={(app) => setCvSidebarApplicationId(app?.id ?? null)}
                  onCreateAssessment={handleCreateAssessment}
                  onUploadCv={handleUploadApplicationCv}
                  uploadingCvId={uploadingCvId}
                  onGenerateTaaliCvAi={handleGenerateTaaliCvAi}
                  onEnrichCandidate={handleEnrichCandidate}
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

      <CandidateCvSidebar
        open={cvSidebarApplicationId != null}
        application={roleApplications.find((a) => Number(a.id) === Number(cvSidebarApplicationId)) ?? null}
        onClose={() => setCvSidebarApplicationId(null)}
        onFetchCvFromWorkable={handleGenerateTaaliCvAi}
        fetchingCvApplicationId={generatingTaaliId}
      />
    </div>
  );
};
