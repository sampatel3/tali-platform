import React, { useState, useEffect, useCallback } from 'react';
import { Search } from 'lucide-react';
import * as apiClient from '../lib/api';

export const CandidatesPage = ({ onNavigate, onViewCandidate, NavComponent }) => {
  const assessmentsApi = apiClient.assessments;
  const candidatesApi = apiClient.candidates;
  const rolesApi = 'roles' in apiClient ? apiClient.roles : null;
  const tasksApi = apiClient.tasks;
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [expandedCandidateId, setExpandedCandidateId] = useState(null);
  const [candidateAssessments, setCandidateAssessments] = useState([]);
  const [loadingAssessments, setLoadingAssessments] = useState(false);
  const [copiedInviteId, setCopiedInviteId] = useState(null);
  const [form, setForm] = useState({ email: '', full_name: '', position: '' });
  const [createCvFile, setCreateCvFile] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [uploadingDoc, setUploadingDoc] = useState(null); // { candidateId, type: 'cv'|'job_spec' }
  const [showDocUpload, setShowDocUpload] = useState(null); // candidateId to show upload panel for
  const [roles, setRoles] = useState([]);
  const [selectedRoleId, setSelectedRoleId] = useState('');
  const [roleTasks, setRoleTasks] = useState([]);
  const [roleApplications, setRoleApplications] = useState([]);
  const [allTasks, setAllTasks] = useState([]);
  const [taskToLink, setTaskToLink] = useState('');
  const [jobSpecFile, setJobSpecFile] = useState(null);
  const [roleForm, setRoleForm] = useState({ name: '', description: '' });
  const [applicationForm, setApplicationForm] = useState({ candidate_email: '', candidate_name: '', candidate_position: '' });
  const [applicationCvFile, setApplicationCvFile] = useState(null);
  const [assessmentTaskByApplication, setAssessmentTaskByApplication] = useState({});

  const loadCandidates = useCallback(async () => {
    setLoading(true);
    try {
      const res = await candidatesApi.list({ q, limit: 100, offset: 0 });
      setItems(res.data?.items || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => {
    loadCandidates();
  }, [loadCandidates]);

  const loadRoles = useCallback(async () => {
    if (!rolesApi?.list) return;
    try {
      const res = await rolesApi.list();
      const roleItems = res.data || [];
      setRoles(roleItems);
      if (!selectedRoleId && roleItems.length > 0) {
        setSelectedRoleId(String(roleItems[0].id));
      }
    } catch {
      setRoles([]);
    }
  }, [rolesApi, selectedRoleId]);

  const loadRoleContext = useCallback(async (roleId) => {
    if (!roleId) {
      setRoleTasks([]);
      setRoleApplications([]);
      return;
    }
    try {
      const [tasksRes, appsRes] = await Promise.all([
        rolesApi?.listTasks ? rolesApi.listTasks(roleId) : Promise.resolve({ data: [] }),
        rolesApi?.listApplications ? rolesApi.listApplications(roleId) : Promise.resolve({ data: [] }),
      ]);
      setRoleTasks(tasksRes.data || []);
      setRoleApplications(appsRes.data || []);
    } catch {
      setRoleTasks([]);
      setRoleApplications([]);
    }
  }, [rolesApi]);

  useEffect(() => {
    loadRoles();
    if (tasksApi?.list) {
      tasksApi.list().then((res) => setAllTasks(res.data || [])).catch(() => setAllTasks([]));
    }
  }, [loadRoles, tasksApi]);

  useEffect(() => {
    loadRoleContext(selectedRoleId);
  }, [selectedRoleId, loadRoleContext]);

  const loadCandidateAssessments = async (candidateId) => {
    setLoadingAssessments(true);
    try {
      const res = await assessmentsApi.list({ candidate_id: candidateId, limit: 100, offset: 0 });
      setCandidateAssessments(res.data?.items || []);
    } catch {
      setCandidateAssessments([]);
    } finally {
      setLoadingAssessments(false);
    }
  };

  const handleCreateOrUpdate = async () => {
    if (!form.email.trim() && !editingId) {
      alert('Email is required');
      return;
    }
    if (!editingId && !createCvFile) {
      alert('CV is required when creating a candidate');
      return;
    }
    try {
      if (editingId) {
        await candidatesApi.update(editingId, {
          full_name: form.full_name || null,
          position: form.position || null,
        });
        setEditingId(null);
      } else {
        const res = await candidatesApi.createWithCv({
          email: form.email.trim(),
          full_name: form.full_name || null,
          position: form.position || null,
          file: createCvFile,
        });
        // After creation, show document upload panel for the new candidate
        if (res.data?.id) {
          setShowDocUpload(res.data.id);
        }
      }
      setForm({ email: '', full_name: '', position: '' });
      setCreateCvFile(null);
      await loadCandidates();
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to save candidate');
    }
  };

  const handleDocUpload = async (candidateId, docType, file) => {
    setUploadingDoc({ candidateId, type: docType });
    try {
      if (docType === 'cv') {
        await candidatesApi.uploadCv(candidateId, file);
      } else {
        await candidatesApi.uploadJobSpec(candidateId, file);
      }
      await loadCandidates();
    } catch (err) {
      alert(err?.response?.data?.detail || `Failed to upload ${docType === 'cv' ? 'CV' : 'job spec'}`);
    } finally {
      setUploadingDoc(null);
    }
  };

  const handleEdit = (candidate) => {
    setEditingId(candidate.id);
    setForm({
      email: candidate.email || '',
      full_name: candidate.full_name || '',
      position: candidate.position || '',
    });
  };

  const handleDelete = async (candidateId) => {
    if (!window.confirm('Delete this candidate?')) return;
    try {
      await candidatesApi.remove(candidateId);
      if (expandedCandidateId === candidateId) {
        setExpandedCandidateId(null);
        setCandidateAssessments([]);
      }
      await loadCandidates();
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to delete candidate');
    }
  };

  const handleCreateRole = async () => {
    if (!rolesApi?.create) return;
    if (!roleForm.name.trim()) {
      alert('Role name is required');
      return;
    }
    try {
      const res = await rolesApi.create({
        name: roleForm.name.trim(),
        description: roleForm.description || undefined,
      });
      const createdRole = res.data;
      setRoleForm({ name: '', description: '' });
      await loadRoles();
      setSelectedRoleId(String(createdRole.id));
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to create role');
    }
  };

  const handleUploadRoleJobSpec = async () => {
    if (!rolesApi?.uploadJobSpec) return;
    if (!selectedRoleId) {
      alert('Select a role first');
      return;
    }
    if (!jobSpecFile) {
      alert('Select a job specification file');
      return;
    }
    try {
      await rolesApi.uploadJobSpec(selectedRoleId, jobSpecFile);
      setJobSpecFile(null);
      await loadRoles();
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to upload job specification');
    }
  };

  const handleLinkTaskToRole = async () => {
    if (!rolesApi?.addTask) return;
    if (!selectedRoleId || !taskToLink) {
      alert('Select a role and a task');
      return;
    }
    try {
      await rolesApi.addTask(selectedRoleId, Number(taskToLink));
      setTaskToLink('');
      await loadRoleContext(selectedRoleId);
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to link task');
    }
  };

  const handleCreateApplication = async () => {
    if (!rolesApi?.createApplication) return;
    if (!selectedRoleId) {
      alert('Select a role first');
      return;
    }
    if (!applicationForm.candidate_email.trim()) {
      alert('Candidate email is required');
      return;
    }
    if (!applicationCvFile) {
      alert('CV is required for role application');
      return;
    }
    try {
      const res = await rolesApi.createApplication(selectedRoleId, {
        candidate_email: applicationForm.candidate_email.trim(),
        candidate_name: applicationForm.candidate_name || undefined,
        candidate_position: applicationForm.candidate_position || undefined,
      });
      await rolesApi.uploadApplicationCv(res.data.id, applicationCvFile);
      setApplicationForm({ candidate_email: '', candidate_name: '', candidate_position: '' });
      setApplicationCvFile(null);
      await loadRoleContext(selectedRoleId);
      await loadCandidates();
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to create role application');
    }
  };

  const handleSendAssessmentForApplication = async (applicationId) => {
    if (!rolesApi?.createAssessment) return;
    const taskId = Number(assessmentTaskByApplication[applicationId] || 0);
    if (!taskId) {
      alert('Select a role task first');
      return;
    }
    try {
      await rolesApi.createAssessment(applicationId, { task_id: taskId });
      await loadRoleContext(selectedRoleId);
      alert('Assessment created and invite sent.');
    } catch (err) {
      alert(err?.response?.data?.detail || 'Failed to create assessment');
    }
  };

  const selectedRole = roles.find((role) => String(role.id) === String(selectedRoleId));
  const canCreateApplicationsForRole = Boolean(selectedRoleId && selectedRole?.job_spec_filename);
  const unlinkedTasks = allTasks.filter((task) => !roleTasks.some((linked) => linked.id === task.id));

  return (
    <div>
      <NavComponent currentPage="candidates" onNavigate={onNavigate} />
      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-3xl font-bold">Candidates</h1>
            <p className="font-mono text-sm text-gray-600 mt-1">Search and manage candidate profiles</p>
          </div>
          <div className="font-mono text-sm text-gray-600">{items.length} total</div>
        </div>

        <div className="border-2 border-black p-4 mb-6 bg-gray-50">
          <div className="mb-3">
            <div className="font-mono text-xs font-bold">Role workflow</div>
            <div className="font-mono text-xs text-gray-600 mt-1">
              Create a role first, then add candidates to that role.
            </div>
          </div>
          <div className="grid lg:grid-cols-2 gap-4">
            <div className="border border-black p-3 bg-white">
              <div className="font-mono text-xs text-gray-500 mb-2">1. Create role and upload job spec</div>
              <div className="flex flex-wrap gap-2 mb-2">
                <input
                  type="text"
                  className="flex-1 min-w-[180px] border-2 border-black px-2 py-1 font-mono text-sm"
                  placeholder="Role name (e.g. Backend Engineer)"
                  value={roleForm.name}
                  onChange={(e) => setRoleForm((prev) => ({ ...prev, name: e.target.value }))}
                />
                <button
                  type="button"
                  className="border-2 border-black px-3 py-1 font-mono text-sm font-bold text-white"
                  style={{ backgroundColor: '#9D00FF' }}
                  onClick={handleCreateRole}
                >
                  Add Role
                </button>
              </div>
              <textarea
                className="w-full border-2 border-black px-2 py-1 font-mono text-xs mb-2"
                placeholder="Role description (optional)"
                value={roleForm.description}
                onChange={(e) => setRoleForm((prev) => ({ ...prev, description: e.target.value }))}
              />
              <select
                className="w-full border-2 border-black px-2 py-1 font-mono text-sm bg-white mb-2"
                value={selectedRoleId}
                onChange={(e) => setSelectedRoleId(e.target.value)}
              >
                <option value="">Select role...</option>
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>{role.name}</option>
                ))}
              </select>
              <div className="flex items-center gap-2">
                <input
                  type="file"
                  accept=".pdf,.docx,.txt"
                  className="font-mono text-xs"
                  onChange={(e) => setJobSpecFile(e.target.files?.[0] || null)}
                />
                <button
                  type="button"
                  className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                  onClick={handleUploadRoleJobSpec}
                >
                  Upload job spec
                </button>
              </div>
              {selectedRole && (
                <div className="font-mono text-xs text-gray-600 mt-2">
                  Selected role: <span className="font-bold">{selectedRole.name}</span>{' '}
                  {selectedRole.job_spec_filename ? `(Job spec: ${selectedRole.job_spec_filename})` : '(No job spec uploaded)'}
                </div>
              )}
            </div>

            <div className="border border-black p-3 bg-white">
              <div className="font-mono text-xs text-gray-500 mb-2">2. Add candidate to selected role (CV required)</div>
              <div className="grid md:grid-cols-3 gap-2 mb-2">
                <input
                  type="email"
                  className="border-2 border-black px-2 py-1 font-mono text-xs"
                  placeholder="candidate@company.com"
                  value={applicationForm.candidate_email}
                  onChange={(e) => setApplicationForm((prev) => ({ ...prev, candidate_email: e.target.value }))}
                />
                <input
                  type="text"
                  className="border-2 border-black px-2 py-1 font-mono text-xs"
                  placeholder="Candidate name"
                  value={applicationForm.candidate_name}
                  onChange={(e) => setApplicationForm((prev) => ({ ...prev, candidate_name: e.target.value }))}
                />
                <input
                  type="text"
                  className="border-2 border-black px-2 py-1 font-mono text-xs"
                  placeholder="Candidate position"
                  value={applicationForm.candidate_position}
                  onChange={(e) => setApplicationForm((prev) => ({ ...prev, candidate_position: e.target.value }))}
                />
              </div>
              <div className="flex items-center gap-2 mb-2">
                <input
                  type="file"
                  accept=".pdf,.docx,.doc"
                  className="font-mono text-xs"
                  onChange={(e) => setApplicationCvFile(e.target.files?.[0] || null)}
                />
                <button
                  type="button"
                  className="border-2 border-black px-3 py-1 font-mono text-xs font-bold text-white disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{ backgroundColor: '#9D00FF' }}
                  onClick={handleCreateApplication}
                  disabled={!canCreateApplicationsForRole}
                >
                  Add Candidate
                </button>
              </div>
              {!selectedRoleId && (
                <div className="font-mono text-xs text-gray-500 mb-2">
                  Select a role before adding a candidate.
                </div>
              )}
              {selectedRoleId && !canCreateApplicationsForRole && (
                <div className="font-mono text-xs text-red-600 mb-2">
                  Upload a job spec for this role before adding candidates.
                </div>
              )}
            </div>
          </div>

          <div className="border border-black p-3 bg-white mt-4">
            <div className="font-mono text-xs text-gray-500 mb-2">3. Link tasks and send assessments</div>
            <div className="flex items-center gap-2 mb-2">
              <select
                className="flex-1 border-2 border-black px-2 py-1 font-mono text-xs bg-white"
                value={taskToLink}
                onChange={(e) => setTaskToLink(e.target.value)}
              >
                <option value="">Link task to role...</option>
                {unlinkedTasks.map((task) => (
                  <option key={task.id} value={task.id}>{task.name}</option>
                ))}
              </select>
              <button
                type="button"
                className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                onClick={handleLinkTaskToRole}
                disabled={!selectedRoleId}
              >
                Link task
              </button>
            </div>
            <div className="font-mono text-xs text-gray-600 mb-3">
              Linked tasks: {roleTasks.length > 0 ? roleTasks.map((task) => task.name).join(', ') : 'None'}
            </div>
            {selectedRoleId ? (
              roleApplications.length > 0 ? (
                <div className="space-y-2">
                  {roleApplications.map((app) => (
                    <div key={app.id} className="border border-gray-300 p-2 flex flex-wrap items-center gap-2 justify-between">
                      <div>
                        <div className="font-mono text-sm font-bold">{app.candidate_name || app.candidate_email}</div>
                        <div className="font-mono text-xs text-gray-600">{app.candidate_email} • CV: {app.cv_filename || 'missing'}</div>
                      </div>
                      <div className="flex items-center gap-2">
                        <select
                          className="border-2 border-black px-2 py-1 font-mono text-xs bg-white"
                          value={assessmentTaskByApplication[app.id] || ''}
                          onChange={(e) => setAssessmentTaskByApplication((prev) => ({ ...prev, [app.id]: e.target.value }))}
                        >
                          <option value="">Select role task...</option>
                          {roleTasks.map((task) => (
                            <option key={task.id} value={task.id}>{task.name}</option>
                          ))}
                        </select>
                        <button
                          type="button"
                          className="border-2 border-black px-3 py-1 font-mono text-xs font-bold text-white disabled:opacity-50 disabled:cursor-not-allowed"
                          style={{ backgroundColor: '#9D00FF' }}
                          onClick={() => handleSendAssessmentForApplication(app.id)}
                          disabled={!app.cv_filename || roleTasks.length === 0}
                        >
                          Create Assessment
                        </button>
                        {!app.cv_filename && (
                          <span className="font-mono text-[11px] text-red-600">Upload CV first</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="font-mono text-xs text-gray-500">No candidates added to this role yet.</div>
              )
            ) : (
              <div className="font-mono text-xs text-gray-500">Select a role to link tasks and send assessments.</div>
            )}
          </div>
        </div>

        <div className="border-2 border-black p-4 mb-6">
          <div className="font-mono text-xs text-gray-500 mb-2">Search</div>
          <input
            type="text"
            className="w-full border-2 border-black px-3 py-2 font-mono text-sm"
            placeholder="Search by name or email"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>

        <div className="border-2 border-black p-4 mb-6">
          <div className="font-mono text-xs text-gray-500 mb-2">{editingId ? 'Edit Candidate' : 'Create Candidate'}</div>
          <div className="grid md:grid-cols-3 gap-2">
            <input
              type="email"
              className="border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="email@company.com"
              value={form.email}
              onChange={(e) => setForm((p) => ({ ...p, email: e.target.value }))}
              disabled={Boolean(editingId)}
            />
            <input
              type="text"
              className="border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="Full name"
              value={form.full_name}
              onChange={(e) => setForm((p) => ({ ...p, full_name: e.target.value }))}
            />
            <input
              type="text"
              className="border-2 border-black px-3 py-2 font-mono text-sm"
              placeholder="Position"
              value={form.position}
              onChange={(e) => setForm((p) => ({ ...p, position: e.target.value }))}
            />
          </div>
          {!editingId && (
            <div className="mt-3">
              <div className="font-mono text-xs text-gray-500 mb-1">CV Upload (required for new candidates)</div>
              <input
                type="file"
                accept=".pdf,.docx"
                className="font-mono text-xs"
                onChange={(e) => setCreateCvFile(e.target.files?.[0] || null)}
              />
              {createCvFile && (
                <div className="font-mono text-xs text-gray-600 mt-1">{createCvFile.name}</div>
              )}
            </div>
          )}
          <div className="flex gap-2 mt-3">
            <button
              type="button"
              className="border-2 border-black px-4 py-2 font-mono text-sm font-bold text-white"
              style={{ backgroundColor: '#9D00FF' }}
              onClick={handleCreateOrUpdate}
            >
              {editingId ? 'Update Candidate' : 'Create Candidate'}
            </button>
            {editingId && (
              <button
                type="button"
                className="border-2 border-black px-4 py-2 font-mono text-sm font-bold hover:bg-black hover:text-white"
                onClick={() => {
                  setEditingId(null);
                  setForm({ email: '', full_name: '', position: '' });
                  setCreateCvFile(null);
                }}
              >
                Cancel
              </button>
            )}
          </div>
        </div>

        {/* Document upload panel (shown after creating a new candidate) */}
        {showDocUpload && (() => {
          const candidate = items.find(c => c.id === showDocUpload);
          if (!candidate) return null;
          return (
            <div className="border-2 border-black p-4 mb-6" style={{ borderColor: '#9D00FF' }}>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <div className="font-mono text-xs text-gray-500">Upload Documents for {candidate.full_name || candidate.email}</div>
                  <div className="font-mono text-xs text-gray-400 mt-1">CV is required at creation. You can optionally upload/update CV and job spec here.</div>
                </div>
                <button type="button" className="font-mono text-xs text-gray-500 hover:text-black" onClick={() => setShowDocUpload(null)}>Close</button>
              </div>
              <div className="grid md:grid-cols-2 gap-4">
                <div className="border border-gray-300 p-3">
                  <div className="font-mono text-xs font-bold mb-2">CV Upload {candidate.cv_filename && <span className="text-green-600 font-normal ml-1">Uploaded</span>}</div>
                  {candidate.cv_filename ? (
                    <div className="font-mono text-xs text-gray-600">{candidate.cv_filename}</div>
                  ) : null}
                  <input
                    type="file"
                    accept=".pdf,.docx"
                    className="font-mono text-xs mt-2"
                    disabled={uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'cv'}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleDocUpload(candidate.id, 'cv', file);
                    }}
                  />
                  {uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'cv' && (
                    <div className="font-mono text-xs text-gray-500 mt-1">Uploading...</div>
                  )}
                </div>
                <div className="border border-gray-300 p-3">
                  <div className="font-mono text-xs font-bold mb-2">Job Spec Upload {candidate.job_spec_filename && <span className="text-green-600 font-normal ml-1">Uploaded</span>}</div>
                  {candidate.job_spec_filename ? (
                    <div className="font-mono text-xs text-gray-600">{candidate.job_spec_filename}</div>
                  ) : null}
                  <input
                    type="file"
                    accept=".pdf,.docx,.txt"
                    className="font-mono text-xs mt-2"
                    disabled={uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'job_spec'}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleDocUpload(candidate.id, 'job_spec', file);
                    }}
                  />
                  {uploadingDoc?.candidateId === candidate.id && uploadingDoc?.type === 'job_spec' && (
                    <div className="font-mono text-xs text-gray-500 mt-1">Uploading...</div>
                  )}
                </div>
              </div>
            </div>
          );
        })()}

        <div className="border-2 border-black overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead>
              <tr className="border-b-2 border-black bg-gray-50">
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Name</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Email</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Position</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Documents</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Created</th>
                <th className="px-4 py-3 text-left font-mono text-xs font-bold uppercase">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center font-mono text-sm text-gray-500">Loading candidates...</td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center font-mono text-sm text-gray-500">No candidates found.</td>
                </tr>
              ) : (
                items.map((c) => (
                  <React.Fragment key={c.id}>
                  <tr className="border-b border-gray-200 align-top">
                    <td className="px-4 py-3 font-bold">{c.full_name || '--'}</td>
                    <td className="px-4 py-3 font-mono text-sm">{c.email}</td>
                    <td className="px-4 py-3 font-mono text-sm">{c.position || '--'}</td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1">
                        <span className={`px-1.5 py-0.5 font-mono text-xs border ${c.cv_filename ? 'bg-green-50 border-green-600 text-green-700' : 'bg-gray-50 border-gray-300 text-gray-400'}`}>
                          CV {c.cv_filename ? '✓' : '—'}
                        </span>
                        <span className={`px-1.5 py-0.5 font-mono text-xs border ${c.job_spec_filename ? 'bg-green-50 border-green-600 text-green-700' : 'bg-gray-50 border-gray-300 text-gray-400'}`}>
                          JD {c.job_spec_filename ? '✓' : '—'}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-sm">{c.created_at ? new Date(c.created_at).toLocaleDateString() : '--'}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                          onClick={() => handleEdit(c)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                          onClick={() => setShowDocUpload(showDocUpload === c.id ? null : c.id)}
                        >
                          Upload Docs
                        </button>
                        <button
                          type="button"
                          className="border-2 border-black px-2 py-1 font-mono text-xs font-bold text-white"
                          style={{ backgroundColor: '#9D00FF' }}
                          onClick={async () => {
                            if (expandedCandidateId === c.id) {
                              setExpandedCandidateId(null);
                              setCandidateAssessments([]);
                              return;
                            }
                            setExpandedCandidateId(c.id);
                            await loadCandidateAssessments(c.id);
                          }}
                        >
                          {expandedCandidateId === c.id ? 'Hide' : 'Assessments'}
                        </button>
                        <button
                          type="button"
                          className="border border-red-600 text-red-700 px-2 py-1 font-mono text-xs hover:bg-red-600 hover:text-white"
                          onClick={() => handleDelete(c.id)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedCandidateId === c.id && (
                    <tr className="border-b border-gray-200">
                      <td colSpan={6} className="px-4 py-3">
                        <div className="border border-gray-300 p-2">
                          <div className="font-mono text-xs text-gray-500 mb-2">Assessments</div>
                          {loadingAssessments ? (
                            <div className="font-mono text-xs text-gray-500">Loading...</div>
                          ) : candidateAssessments.length === 0 ? (
                            <div className="font-mono text-xs text-gray-500">No assessments for this candidate.</div>
                          ) : (
                            <div className="space-y-2">
                              {candidateAssessments.map((a) => (
                                <div key={a.id} className="flex items-center justify-between border border-gray-200 p-2">
                                  <div>
                                    <div className="font-mono text-xs">{a.task_name || 'Assessment'}</div>
                                    <div className="font-mono text-xs text-gray-500">Status: {a.status} | Score: {a.score ?? '--'}</div>
                                  </div>
                                  <div className="flex gap-2">
                                    <button
                                      type="button"
                                      className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                                      onClick={() => {
                                        const base = window.location.origin;
                                        const link = `${base}/assess/${a.token}`;
                                        const name = a.candidate_name || c.full_name || c.email || 'there';
                                        const firstName = name.split(' ')[0];
                                        const duration = a.duration_minutes || 30;
                                        const task = a.task_name || 'Technical Assessment';
                                        const template = `Subject: Your Technical Assessment — ${task}\n\nHi ${firstName},\n\nThank you for your interest. As part of our process, we'd like you to complete a short technical assessment.\n\nAssessment: ${task}\nTime allowed: ${duration} minutes\nYour unique link: ${link}\n\nA few things to note:\n- The assessment is self-contained — no setup required, just a browser\n- The timer starts when you click Begin\n- You can use the built-in AI assistant during the task\n- Make sure you're in a quiet place with a stable internet connection before starting\n\nPlease complete the assessment at your earliest convenience.\n\nIf you have any questions, just reply to this email.\n\nGood luck!`;
                                        navigator.clipboard.writeText(template);
                                        setCopiedInviteId(a.id);
                                        setTimeout(() => setCopiedInviteId(null), 2000);
                                      }}
                                    >
                                      {copiedInviteId === a.id ? 'Copied!' : 'Copy Invite Email'}
                                    </button>
                                    <button
                                      type="button"
                                      className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
                                      onClick={() =>
                                        onViewCandidate({
                                          id: a.id,
                                          name: a.candidate_name || c.full_name || c.email,
                                          email: a.candidate_email || c.email,
                                          task: a.task_name || 'Assessment',
                                          status: a.status || 'pending',
                                          score: a.score ?? null,
                                          time: a.duration_taken ? `${Math.round(a.duration_taken / 60)}m` : '—',
                                          position: c.position || '',
                                          completedDate: a.completed_at ? new Date(a.completed_at).toLocaleDateString() : null,
                                          breakdown: a.breakdown || null,
                                          prompts: a.prompt_count ?? 0,
                                          promptsList: a.prompts_list || [],
                                          timeline: a.timeline || [],
                                          results: a.results || [],
                                          token: a.token,
                                          _raw: a,
                                        })
                                      }
                                    >
                                      Open Detail
                                    </button>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
