import React, { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, Loader2, Mail, Minus, Plus, Settings2, Share2, Sparkles, Trash2 } from 'lucide-react';
import { useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';

import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { buildJobSpecPreview, extractJobSpecFacts, splitJobSpecParagraphs } from '../../lib/jobSpecText';
import { roles as rolesApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';
import { WorkableTagSm } from '../../components/integrations/workable/WorkablePrimitives';

const STAGES = [
  { id: 'applied', label: 'Applied', tone: 'var(--mute)' },
  { id: 'invited', label: 'Invited', tone: 'var(--amber)' },
  { id: 'in_assessment', label: 'Assessment', tone: 'var(--purple)' },
  { id: 'review', label: 'Review', tone: 'var(--green)' },
];

const initialsFor = (value) => String(value || '')
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 3)
  .map((part) => part[0])
  .join('')
  .toUpperCase() || 'TA';

const normalizeScore100 = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  if (numeric <= 10) return Math.round(numeric * 10);
  return Math.round(Math.max(0, Math.min(100, numeric)));
};

const formatRelativeTime = (value) => {
  if (!value) return 'Recent';
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return 'Recent';
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60000));
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
};

const formatShortDate = (value) => (
  value ? new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : 'Recent'
);

const roleThresholdFor = (role) => Number(role?.reject_threshold || 60);

const isBelowThreshold = (application, role) => {
  if (application?.below_role_threshold === true) return true;
  const score = normalizeScore100(application?.cv_match_score);
  if (score == null) return false;
  return score < roleThresholdFor(role);
};

const overviewStats = ({ applications, role }) => {
  const scores = applications
    .map((item) => normalizeScore100(item?.taali_score ?? item?.rank_score))
    .filter((value) => Number.isFinite(value));
  const avgScore = scores.length
    ? Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length)
    : null;
  const completed = applications.filter((item) => item?.valid_assessment_id || item?.assessment_preview?.assessment_id).length;
  const below = applications.filter((item) => isBelowThreshold(item, role)).length;

  return {
    pipeline: applications.length,
    completed,
    below,
    avgScore,
  };
};

const highlightCardsForRole = (role, preview) => {
  const criteria = Array.isArray(role?.scoring_criteria) ? role.scoring_criteria : [];
  const items = [
    ...criteria.filter((item) => item?.source === 'job_spec').map((item) => ({
      title: item.text.split(/[.,]/)[0],
      body: item.text,
    })),
    ...preview.supporting.map((paragraph) => ({
      title: paragraph.split(/[.:]/)[0],
      body: paragraph,
    })),
  ];

  const unique = [];
  const seen = new Set();
  items.forEach((item) => {
    const key = String(item?.body || '').toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    unique.push(item);
  });
  return unique.slice(0, 4);
};

const CandidateCard = ({ application, role, onNavigate }) => {
  const score = normalizeScore100(application?.taali_score ?? application?.rank_score);
  const cvMatch = normalizeScore100(application?.cv_match_score);
  const below = isBelowThreshold(application, role);

  return (
    <button
      type="button"
      className="block w-full rounded-[18px] border border-[var(--line)] bg-[var(--bg)] px-3 py-3 text-left transition hover:-translate-y-0.5 hover:border-[var(--purple)] hover:shadow-[var(--shadow-sm)]"
      style={below ? {
        borderColor: 'color-mix(in oklab, var(--red) 20%, var(--line))',
        background: 'color-mix(in oklab, var(--red) 5%, var(--bg))',
      } : undefined}
      onClick={() => onNavigate('candidate-report', { candidateApplicationId: application.id })}
    >
      <div className="grid grid-cols-[34px_1fr_auto] items-center gap-3">
        <div className="grid h-[34px] w-[34px] place-items-center rounded-full bg-[var(--purple-soft)] text-[11.5px] font-semibold text-[var(--purple)]">
          {initialsFor(application?.candidate_name)}
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <div className="truncate text-[13.5px] font-semibold tracking-[-0.01em]">{application?.candidate_name || 'Unknown candidate'}</div>
            {application?.workable_sourced ? <WorkableTagSm /> : null}
          </div>
          <div className="truncate text-[11.5px] text-[var(--mute)]">{application?.candidate_email || 'No email'}</div>
        </div>
        <div className={`font-[var(--font-mono)] text-[13px] font-semibold ${
          score == null ? 'text-[var(--mute)]' : score >= 80 ? 'text-[var(--green)]' : score >= 65 ? 'text-[var(--purple)]' : 'text-[var(--red)]'
        }`.trim()}
        >
          {score == null ? '—' : score}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-[1fr_auto] items-center gap-3">
        <div>
          <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">CV match</div>
          <div className="mt-1 flex items-center gap-2">
            <span className={`font-[var(--font-mono)] text-[12px] font-semibold ${
              cvMatch == null ? 'text-[var(--mute)]' : cvMatch >= 80 ? 'text-[var(--green)]' : cvMatch >= 65 ? 'text-[var(--purple)]' : 'text-[var(--red)]'
            }`.trim()}
            >
              {cvMatch == null ? '—' : `${cvMatch}%`}
            </span>
            {below ? (
              <span
                className="rounded-full px-2 py-0.5 font-[var(--font-mono)] text-[10px] uppercase tracking-[0.08em] text-[var(--red)]"
                style={{ background: 'color-mix(in oklab, var(--red) 10%, transparent)' }}
              >
                Below
              </span>
            ) : null}
          </div>
        </div>
        <div className="font-[var(--font-mono)] text-[10.5px] text-[var(--mute)]">{formatRelativeTime(application?.updated_at || application?.pipeline_stage_updated_at)}</div>
      </div>
    </button>
  );
};

const CriteriaItem = ({
  item,
  editingId,
  editingText,
  savingId,
  onStartEdit,
  onChangeEdit,
  onCancelEdit,
  onSaveEdit,
  onDelete,
}) => {
  const isEditing = editingId === item.id;
  const sourceTone = item?.source === 'recruiter'
    ? {
      background: 'color-mix(in oklab, var(--green) 9%, transparent)',
      color: 'var(--green)',
      borderColor: 'color-mix(in oklab, var(--green) 20%, var(--line))',
    }
    : {
      background: 'color-mix(in oklab, var(--purple) 9%, transparent)',
      color: 'var(--purple)',
      borderColor: 'color-mix(in oklab, var(--purple) 20%, var(--line))',
    };

  return (
    <div className="grid grid-cols-[32px_1fr_auto_auto] items-start gap-3 rounded-[16px] border border-[var(--line)] bg-[var(--bg)] px-4 py-3">
      <span className="mt-0.5 font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--mute)]">
        {String(item?.order || '').padStart(2, '0')}
      </span>

      <div className="min-w-0">
        {isEditing ? (
          <textarea
            className="w-full rounded-[12px] border border-[var(--line)] bg-[var(--bg-2)] px-3 py-2 text-[13.5px] leading-6 outline-none focus:border-[var(--purple)]"
            rows={3}
            value={editingText}
            onChange={(event) => onChangeEdit(event.target.value)}
          />
        ) : (
          <div className="text-[13.5px] leading-6 text-[var(--ink)]">{item?.text}</div>
        )}
      </div>

      <span
        className="rounded-full border px-2.5 py-1 font-[var(--font-mono)] text-[10px] uppercase tracking-[0.08em]"
        style={sourceTone}
      >
        {item?.source === 'recruiter' ? 'Recruiter' : 'Job spec'}
      </span>

      <div className="flex items-center gap-2">
        {isEditing ? (
          <>
            <button type="button" className="icon-btn" onClick={() => onSaveEdit(item)} disabled={savingId === item.id} title="Save criterion">
              {savingId === item.id ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            </button>
            <button type="button" className="icon-btn" onClick={onCancelEdit} title="Cancel">
              <Minus size={14} />
            </button>
          </>
        ) : (
          <>
            <button type="button" className="icon-btn" onClick={() => onStartEdit(item)} title="Edit criterion">
              <Settings2 size={14} />
            </button>
            <button type="button" className="icon-btn" onClick={() => onDelete(item)} title="Delete criterion">
              <Trash2 size={14} />
            </button>
          </>
        )}
      </div>
    </div>
  );
};

export const JobPipelinePage = ({ onNavigate }) => {
  const { roleId } = useParams();
  const { user } = useAuth();
  const { showToast } = useToast();

  const [role, setRole] = useState(null);
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const [newCriterion, setNewCriterion] = useState('');
  const [criteriaSavingId, setCriteriaSavingId] = useState('');
  const [editingCriterionId, setEditingCriterionId] = useState('');
  const [editingCriterionText, setEditingCriterionText] = useState('');
  const [thresholdDraft, setThresholdDraft] = useState(60);
  const [thresholdSaving, setThresholdSaving] = useState(false);

  useEffect(() => {
    setThresholdDraft(roleThresholdFor(role));
  }, [role]);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      if (!roleId) {
        setError('Role not found.');
        setLoading(false);
        return;
      }

      setLoading(true);
      setError('');
      try {
        const [roleRes, pipelineRes] = await Promise.all([
          rolesApi.get(roleId),
          rolesApi.listPipeline(roleId, { stage: 'all', application_outcome: 'open', limit: 200, offset: 0 }),
        ]);
        if (cancelled) return;
        setRole(roleRes?.data || null);
        const payload = pipelineRes?.data || {};
        setApplications(Array.isArray(payload?.items) ? payload.items : []);
      } catch (requestError) {
        if (!cancelled) {
          setRole(null);
          setApplications([]);
          setError(requestError?.response?.data?.detail || 'Failed to load role pipeline.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [roleId]);

  const grouped = useMemo(() => {
    const next = {
      applied: [],
      invited: [],
      in_assessment: [],
      review: [],
    };
    applications.forEach((application) => {
      const key = String(application?.pipeline_stage || 'applied');
      if (key in next) next[key].push(application);
    });
    return next;
  }, [applications]);

  const preview = useMemo(() => buildJobSpecPreview(role), [role]);
  const facts = useMemo(() => extractJobSpecFacts(role?.job_spec_text || role?.description || ''), [role?.description, role?.job_spec_text]);
  const paragraphs = useMemo(() => splitJobSpecParagraphs(role?.job_spec_text || role?.description || ''), [role?.description, role?.job_spec_text]);
  const highlights = useMemo(() => highlightCardsForRole(role || {}, preview), [preview, role]);
  const stats = useMemo(() => overviewStats({ applications, role }), [applications, role]);
  const belowThresholdCount = stats.below;
  const thresholdCaption = `${belowThresholdCount} of ${applications.length} candidates currently score below ${thresholdDraft}%.`;
  const recruiterName = String(user?.full_name || user?.name || 'Recruiter').trim() || 'Recruiter';
  const interviewQuestions = useMemo(() => (
    (Array.isArray(role?.interview_focus?.questions) ? role.interview_focus.questions : [])
      .map((item) => {
        if (typeof item === 'string') {
          return {
            question: item,
            what_to_listen_for: [],
            concerning_signals: [],
          };
        }
        return item;
      })
      .filter((item) => Boolean(item?.question))
  ), [role?.interview_focus?.questions]);

  const handleReloadPipeline = async () => {
    if (!roleId) return;
    const pipelineRes = await rolesApi.listPipeline(roleId, { stage: 'all', application_outcome: 'open', limit: 200, offset: 0 });
    setApplications(Array.isArray(pipelineRes?.data?.items) ? pipelineRes.data.items : []);
  };

  const handleInvite = async () => {
    const candidateName = window.prompt('Candidate name');
    const candidateEmail = window.prompt('Candidate email');
    if (!candidateEmail || !roleId) return;
    setInviteLoading(true);
    try {
      await rolesApi.createApplication(roleId, {
        candidate_name: candidateName || undefined,
        candidate_email: candidateEmail,
      });
      await handleReloadPipeline();
    } catch {
      setError('Failed to invite candidate.');
    } finally {
      setInviteLoading(false);
    }
  };

  const handleAddCriterion = async () => {
    const text = String(newCriterion || '').trim();
    if (!text || !role) return;
    setCriteriaSavingId('new');
    try {
      const res = await rolesApi.createCriterion(role.id, { text, source: 'recruiter' });
      setRole((current) => ({ ...current, scoring_criteria: res?.data?.items || current?.scoring_criteria || [] }));
      setNewCriterion('');
      await handleReloadPipeline();
      showToast('Scoring criteria updated. CV matches were re-scored for this role.', 'success');
    } catch (requestError) {
      showToast(requestError?.response?.data?.detail || 'Failed to add recruiter requirement.', 'error');
    } finally {
      setCriteriaSavingId('');
    }
  };

  const handleSaveCriterion = async (item) => {
    const text = String(editingCriterionText || '').trim();
    if (!text || !role) return;
    setCriteriaSavingId(item.id);
    try {
      const res = await rolesApi.updateCriterion(role.id, item.id, { text });
      setRole((current) => ({ ...current, scoring_criteria: res?.data?.items || current?.scoring_criteria || [] }));
      setEditingCriterionId('');
      setEditingCriterionText('');
      await handleReloadPipeline();
      showToast('Criterion updated and candidate CV matches re-scored.', 'success');
    } catch (requestError) {
      showToast(requestError?.response?.data?.detail || 'Failed to update scoring criterion.', 'error');
    } finally {
      setCriteriaSavingId('');
    }
  };

  const handleDeleteCriterion = async (item) => {
    if (!role) return;
    setCriteriaSavingId(item.id);
    try {
      const res = await rolesApi.deleteCriterion(role.id, item.id);
      setRole((current) => ({ ...current, scoring_criteria: res?.data?.items || current?.scoring_criteria || [] }));
      await handleReloadPipeline();
      showToast('Criterion removed and candidate CV matches re-scored.', 'success');
    } catch (requestError) {
      showToast(requestError?.response?.data?.detail || 'Failed to delete criterion.', 'error');
    } finally {
      setCriteriaSavingId('');
    }
  };

  const handleSaveThreshold = async () => {
    if (!role) return;
    setThresholdSaving(true);
    try {
      const res = await rolesApi.update(role.id, { reject_threshold: thresholdDraft });
      setRole(res?.data || role);
      setApplications((current) => current.map((application) => ({
        ...application,
        role_reject_threshold: thresholdDraft,
        below_role_threshold: normalizeScore100(application?.cv_match_score) != null
          ? normalizeScore100(application?.cv_match_score) < thresholdDraft
          : application?.below_role_threshold,
      })));
      showToast('Reject threshold updated.', 'success');
    } catch (requestError) {
      showToast(requestError?.response?.data?.detail || 'Failed to update reject threshold.', 'error');
    } finally {
      setThresholdSaving(false);
    }
  };

  return (
    <AppShell currentPage="jobs" onNavigate={onNavigate}>
      <div className="page">
        <button type="button" className="mb-4 inline-flex items-center gap-2 font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.08em] text-[var(--mute)] hover:text-[var(--purple)]" onClick={() => onNavigate('jobs')}>
          <ChevronLeft size={12} />
          All roles
        </button>

        {loading ? (
          <div className="space-y-5">
            <div className="animate-pulse rounded-[24px] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
              <div className="h-4 w-1/5 rounded-full bg-[var(--line)]" />
              <div className="mt-4 h-10 w-1/2 rounded-full bg-[var(--line)]" />
              <div className="mt-4 h-4 w-3/4 rounded-full bg-[var(--line)]" />
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-44 animate-pulse rounded-[22px] bg-[var(--bg-2)]" />)}
            </div>
          </div>
        ) : error ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </div>
        ) : (
          <>
            <div className="relative mb-5 overflow-hidden rounded-[26px] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
              <div className="pointer-events-none absolute right-0 top-4 font-[var(--font-display)] text-[120px] font-semibold leading-none tracking-[-0.08em] text-[var(--bg-3)] opacity-70">
                {initialsFor(role?.name)}
              </div>

              <div className="relative flex flex-wrap items-start justify-between gap-6">
                <div className="max-w-[760px]">
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span className="kicker m-0">ROLE · #{role?.id || roleId}</span>
                    <span className="chip green">Active</span>
                    <span className="chip">Posted {formatShortDate(role?.created_at)}</span>
                  </div>
                  <h1 className="font-[var(--font-display)] text-[44px] font-semibold leading-[0.95] tracking-[-0.04em]">
                    {role?.name || 'Role pipeline'}
                  </h1>

                  <div className="mt-5 grid gap-3 md:grid-cols-3 xl:grid-cols-6">
                    {[...facts, ['Recruiter', recruiterName], ['Assessment', 'Taali workflow']].slice(0, 6).map(([label, value]) => (
                      <div key={label} className="rounded-[14px] border border-[var(--line)] bg-[var(--bg)] px-3 py-3">
                        <div className="font-[var(--font-mono)] text-[10px] uppercase tracking-[0.1em] text-[var(--mute)]">{label}</div>
                        <div className={`mt-1 text-[13px] leading-5 ${label === 'Assessment' ? 'text-[var(--purple)]' : 'text-[var(--ink)]'}`.trim()}>{value}</div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="flex flex-col items-end gap-3">
                  <div className="row">
                    <button type="button" className="icon-btn" title="Share"><Share2 size={15} strokeWidth={1.7} /></button>
                    <button type="button" className="icon-btn" title="Settings"><Settings2 size={15} strokeWidth={1.7} /></button>
                  </div>
                  <div className="row">
                    <button type="button" className="btn btn-outline btn-sm">Edit role</button>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleInvite} disabled={inviteLoading}>
                      {inviteLoading ? 'Inviting…' : <>Invite candidate <span className="arrow">→</span></>}
                    </button>
                  </div>
                </div>
              </div>

              <div className="mt-6 grid gap-5 xl:grid-cols-[1.5fr_.9fr]">
                <div className="rounded-[20px] border border-[var(--line)] bg-[var(--bg)] px-5 py-5">
                  <p className="text-[15px] leading-7 text-[var(--ink-2)]">
                    <b>{preview.lead || 'This role is hiring for strong execution judgment.'}</b>
                  </p>
                  <button
                    type="button"
                    className="mt-4 inline-flex items-center gap-2 font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.08em] text-[var(--purple)]"
                    onClick={() => setDescriptionExpanded((value) => !value)}
                  >
                    {descriptionExpanded ? 'Hide full description' : 'Read full description'}
                    <Sparkles size={12} />
                  </button>

                  {descriptionExpanded ? (
                    <div className="mt-5 grid gap-4">
                      {paragraphs.map((paragraph, index) => (
                        <div key={`${index}-${paragraph.slice(0, 12)}`} className="rounded-[16px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-4">
                          <div className="mb-2 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--purple)]">
                            {String(index + 1).padStart(2, '0')}
                          </div>
                          <ReactMarkdown
                            components={{
                              p: ({ children }) => <p className="text-[14px] leading-7 text-[var(--ink-2)]">{children}</p>,
                              li: ({ children }) => <li className="ml-5 list-disc text-[14px] leading-7 text-[var(--ink-2)]">{children}</li>,
                              strong: ({ children }) => <strong className="font-semibold text-[var(--ink)]">{children}</strong>,
                            }}
                          >
                            {paragraph}
                          </ReactMarkdown>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>

                <div className="rounded-[20px] border border-[var(--line)] bg-[var(--bg)] px-5 py-5">
                  <h3 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">At a glance</h3>
                  <div className="mt-4 space-y-3">
                    {highlights.length ? highlights.map((item) => (
                      <div key={item.body} className="rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3">
                        <div className="text-[14px] font-semibold">{item.title}</div>
                        <div className="mt-1 text-[12.5px] leading-6 text-[var(--mute)]">{item.body}</div>
                      </div>
                    )) : (
                      <div className="rounded-[14px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-4 text-[13px] leading-6 text-[var(--mute)]">
                        Upload a richer job description or add criteria to surface sharper role highlights here.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            <div className="mb-5 grid gap-5 xl:grid-cols-[1.2fr_.8fr]">
              <div className="rounded-[24px] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                <h3 className="font-[var(--font-display)] text-[28px] font-semibold tracking-[-0.03em]">Scoring <em>criteria</em></h3>
                <p className="mt-1 text-[13px] leading-6 text-[var(--mute)]">Every CV on this role is scored against these. Adding or editing any item triggers a re-score of all candidates on the role.</p>

                <div className="mt-5 space-y-3">
                  {(Array.isArray(role?.scoring_criteria) ? role.scoring_criteria : []).map((item) => (
                    <CriteriaItem
                      key={item.id}
                      item={item}
                      editingId={editingCriterionId}
                      editingText={editingCriterionText}
                      savingId={criteriaSavingId}
                      onStartEdit={(criterion) => {
                        setEditingCriterionId(criterion.id);
                        setEditingCriterionText(criterion.text);
                      }}
                      onChangeEdit={setEditingCriterionText}
                      onCancelEdit={() => {
                        setEditingCriterionId('');
                        setEditingCriterionText('');
                      }}
                      onSaveEdit={handleSaveCriterion}
                      onDelete={handleDeleteCriterion}
                    />
                  ))}
                </div>

                <div className="mt-4 rounded-[16px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-4">
                  <label className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">
                    Add recruiter requirement
                  </label>
                  <textarea
                    className="mt-2 w-full rounded-[12px] border border-[var(--line)] bg-[var(--bg-2)] px-3 py-2 text-[13.5px] leading-6 outline-none focus:border-[var(--purple)]"
                    rows={3}
                    value={newCriterion}
                    onChange={(event) => setNewCriterion(event.target.value)}
                    placeholder="Add a new recruiter-specific requirement that Claude should score on the next CV pass…"
                  />
                  <div className="mt-3 flex items-center justify-between gap-3">
                    <span className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
                      Scored by Claude Sonnet · re-scores on CV change or criteria update
                    </span>
                    <button type="button" className="btn btn-outline btn-sm" onClick={handleAddCriterion} disabled={criteriaSavingId === 'new' || !newCriterion.trim()}>
                      {criteriaSavingId === 'new' ? 'Adding…' : '+ Add recruiter requirement'}
                    </button>
                  </div>
                </div>
              </div>

              <div className="rounded-[24px] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                <h3 className="font-[var(--font-display)] text-[28px] font-semibold tracking-[-0.03em]">Reject <em>threshold</em></h3>
                <p className="mt-1 text-[13px] leading-6 text-[var(--mute)]">CVs below this score are flagged for bulk rejection. Nothing happens automatically — you stay in control.</p>

                <div className="mt-6">
                  <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Below this → flag for rejection</div>
                  <div className="mt-2 flex items-end gap-2">
                    <span className="font-[var(--font-display)] text-[56px] font-semibold leading-none tracking-[-0.06em]">{thresholdDraft}</span>
                    <span className="mb-2 text-[20px] text-[var(--mute)]">%</span>
                  </div>

                  <div className="mt-5 flex items-center gap-3">
                    <button type="button" className="icon-btn" onClick={() => setThresholdDraft((current) => Math.max(0, current - 5))}>
                      <Minus size={14} />
                    </button>
                    <input
                      className="w-full accent-[var(--purple)]"
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      value={thresholdDraft}
                      onChange={(event) => setThresholdDraft(Number(event.target.value))}
                    />
                    <button type="button" className="icon-btn" onClick={() => setThresholdDraft((current) => Math.min(100, current + 5))}>
                      <Plus size={14} />
                    </button>
                  </div>

                  <div className="mt-2 flex justify-between font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--mute)]">
                    <span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span>
                  </div>

                  <div className="mt-6">
                    <div className="mb-2 font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Pipeline distribution</div>
                    <div className="flex flex-wrap gap-2">
                      {applications.map((application) => (
                        <span
                          key={application.id}
                          className="h-3 w-3 rounded-full"
                          style={{
                            background: isBelowThreshold({ ...application, role_reject_threshold: thresholdDraft }, { reject_threshold: thresholdDraft })
                              ? 'var(--red)'
                              : 'var(--green)',
                            boxShadow: '0 0 0 3px color-mix(in oklab, currentColor 12%, transparent)',
                          }}
                        />
                      ))}
                    </div>
                  </div>

                  <p className="mt-4 text-[13px] leading-6 text-[var(--ink-2)]">
                    <b>{thresholdCaption}</b> Review them on the Candidates table, then bulk-reject if needed.
                  </p>

                  <div className="mt-4 flex items-center justify-between gap-3">
                    <button type="button" className="btn btn-outline btn-sm" onClick={() => onNavigate('candidates')}>
                      View below threshold →
                    </button>
                    <button type="button" className="btn btn-outline btn-sm" onClick={handleSaveThreshold} disabled={thresholdSaving}>
                      {thresholdSaving ? 'Saving…' : 'Save threshold'}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-[20px] bg-[var(--ink)] p-5 text-[var(--bg)]">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-white/60">In pipeline</div>
                <div className="mt-2 text-[28px] font-semibold tracking-[-0.03em]">{stats.pipeline}</div>
                <div className="mt-1 text-[12px] text-white/60">Open candidates</div>
              </div>
              <div className="rounded-[20px] border border-[var(--line)] bg-[var(--bg-2)] p-5">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Below threshold</div>
                <div className="mt-2 text-[28px] font-semibold tracking-[-0.03em] text-[var(--red)]">{stats.below}</div>
                <div className="mt-1 text-[12px] text-[var(--mute)]">Ready for recruiter triage</div>
              </div>
              <div className="rounded-[20px] border border-[var(--line)] bg-[var(--bg-2)] p-5">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Assessments done</div>
                <div className="mt-2 text-[28px] font-semibold tracking-[-0.03em]">{stats.completed}</div>
                <div className="mt-1 text-[12px] text-[var(--mute)]">Completed reports</div>
              </div>
              <div className="rounded-[20px] border border-[var(--line)] bg-[var(--bg-2)] p-5">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Avg Taali</div>
                <div className="mt-2 text-[28px] font-semibold tracking-[-0.03em] text-[var(--purple)]">{stats.avgScore == null ? '—' : stats.avgScore}</div>
                <div className="mt-1 text-[12px] text-[var(--mute)]">Across scored candidates</div>
              </div>
            </div>

            <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
              <div>
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <div className="app-tabs">
                    <button type="button" className="app-tab active">Pipeline</button>
                    <button type="button" className="app-tab" onClick={() => onNavigate('candidates')}>Candidates table</button>
                    <button type="button" className="app-tab" onClick={() => onNavigate('reporting')}>Activity</button>
                  </div>
                  <div className="row">
                    <span className="chip">FILTER · Open</span>
                    <span className="chip">SORT · Recent</span>
                  </div>
                </div>

                <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
                  {STAGES.map((stage) => (
                    <div key={stage.id} className="flex min-h-[460px] flex-col gap-3 rounded-[22px] border border-[var(--line)] bg-[var(--bg-2)] p-4">
                      <div className="mb-1 flex items-center justify-between border-b border-[var(--line-2)] px-1 pb-3">
                        <div className="flex items-center gap-2 text-sm font-semibold">
                          <span className="h-2 w-2 rounded-full" style={{ background: stage.tone }} />
                          {stage.label}
                        </div>
                        <div className="font-[var(--font-mono)] text-[11.5px] tracking-[0.06em] text-[var(--mute)]">
                          {grouped[stage.id].length}
                        </div>
                      </div>
                      {grouped[stage.id].length === 0 ? (
                        <div className="mt-8 rounded-[18px] border border-dashed border-[var(--line)] px-4 py-5 text-center font-[var(--font-mono)] text-[12px] uppercase tracking-[0.06em] text-[var(--mute)]">
                          Empty
                        </div>
                      ) : (
                        grouped[stage.id].map((application) => (
                          <CandidateCard key={application.id} application={application} role={role} onNavigate={onNavigate} />
                        ))
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-5">
                <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                  <h3 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.02em]">Role <em>summary</em></h3>
                  <div className="mt-4 space-y-3 text-sm">
                    <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Role</span><span>{role?.name || '—'}</span></div>
                    <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Applications</span><span>{applications.length}</span></div>
                    <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Below threshold</span><span>{belowThresholdCount}</span></div>
                    <div className="flex justify-between"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Threshold</span><span>{roleThresholdFor(role)}%</span></div>
                  </div>
                </div>

                <div className="rounded-[22px] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                  <h3 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.02em]">Interview <em>focus</em></h3>
                  <p className="mt-1 text-[12.5px] leading-6 text-[var(--mute)]">Use these prompts when candidates reach panel review.</p>
                  <div className="mt-4 space-y-4">
                    {interviewQuestions.length ? (
                      interviewQuestions.map((item, index) => (
                        <div key={item?.question || index} className="rounded-[16px] border border-[var(--line)] bg-[var(--bg)] px-4 py-4">
                          <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--purple)]">{`Q${index + 1}`}</div>
                          <div className="mt-2 text-[14px] font-semibold">{item?.question}</div>
                          {item?.what_to_listen_for ? (
                            <div className="mt-2 text-[12.5px] leading-6 text-[var(--ink-2)]"><b>Listen for:</b> {Array.isArray(item.what_to_listen_for) ? item.what_to_listen_for.join(' • ') : item.what_to_listen_for}</div>
                          ) : null}
                          {item?.concerning_signals ? (
                            <div className="mt-2 text-[12.5px] leading-6 text-[var(--mute)]"><b className="text-[var(--ink)]">Watch for:</b> {Array.isArray(item.concerning_signals) ? item.concerning_signals.join(' • ') : item.concerning_signals}</div>
                          ) : null}
                        </div>
                      ))
                    ) : (
                      <div className="rounded-[16px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-5 text-[13px] leading-6 text-[var(--mute)]">
                        Interview guidance will appear once a richer job spec is uploaded for this role.
                      </div>
                    )}
                  </div>
                </div>

                <button type="button" className="btn btn-outline w-full justify-center" onClick={handleInvite}>
                  <Mail size={14} />
                  Invite another candidate
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
};

export default JobPipelinePage;
