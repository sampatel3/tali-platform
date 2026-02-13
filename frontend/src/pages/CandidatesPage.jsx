import React, { useState, useEffect, useCallback } from 'react';
import { Search } from 'lucide-react';
import { assessments as assessmentsApi, candidates as candidatesApi } from '../lib/api';

export const CandidatesPage = ({ onNavigate, onViewCandidate, NavComponent, NewAssessmentModalComponent }) => {
  const AssessmentModal = NewAssessmentModalComponent;
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [expandedCandidateId, setExpandedCandidateId] = useState(null);
  const [sendAssessmentCandidate, setSendAssessmentCandidate] = useState(null);
  const [candidateAssessments, setCandidateAssessments] = useState([]);
  const [loadingAssessments, setLoadingAssessments] = useState(false);
  const [copiedInviteId, setCopiedInviteId] = useState(null);
  const [form, setForm] = useState({ email: '', full_name: '', position: '' });
  const [createCvFile, setCreateCvFile] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [uploadingDoc, setUploadingDoc] = useState(null); // { candidateId, type: 'cv'|'job_spec' }
  const [showDocUpload, setShowDocUpload] = useState(null); // candidateId to show upload panel for

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
                          onClick={() => setSendAssessmentCandidate(c)}
                        >
                          Send Assessment
                        </button>
                        <button
                          type="button"
                          className="border border-black px-2 py-1 font-mono text-xs hover:bg-black hover:text-white"
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

      {sendAssessmentCandidate && AssessmentModal ? (
        <AssessmentModal
          candidate={sendAssessmentCandidate}
          onClose={() => setSendAssessmentCandidate(null)}
          onCreated={() => {
            setSendAssessmentCandidate(null);
            loadCandidates();
          }}
        />
      ) : null}
    </div>
  );
};
