import React, { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, Mail, Settings2, Share2 } from 'lucide-react';
import { useParams } from 'react-router-dom';

import { roles as rolesApi } from '../../shared/api';
import { AppShell } from '../../shared/layout/TaaliLayout';

const STAGES = [
  { id: 'applied', label: 'Applied', tone: 'var(--mute)' },
  { id: 'invited', label: 'Invited', tone: 'var(--amber)' },
  { id: 'in_assessment', label: 'Assessment', tone: 'var(--purple)' },
  { id: 'review', label: 'Review', tone: 'var(--green)' },
];

const initialsFor = (value) => String(value || '')
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase() || 'TA';

const asNumber = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

const assessmentIdFor = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const CandidateCard = ({ application, onNavigate }) => {
  const score = application?.taali_score ?? application?.rank_score ?? null;
  const assessmentId = assessmentIdFor(application);
  const nextPage = assessmentId ? 'candidate-detail' : 'candidate-report';
  const nextOptions = assessmentId ? { candidateDetailAssessmentId: assessmentId } : { candidateApplicationId: application.id };
  const scoreClassName = score == null ? 'text-[var(--mute)]' : score >= 80 ? 'text-[var(--green)]' : score >= 65 ? 'text-[var(--amber)]' : 'text-[var(--red)]';

  return (
    <button
      type="button"
      className="block w-full rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg)] px-3 py-3 text-left transition hover:-translate-y-0.5 hover:border-[var(--purple)] hover:shadow-[var(--shadow-sm)]"
      onClick={() => onNavigate(nextPage, nextOptions)}
    >
      <div className="grid grid-cols-[32px_1fr_auto] items-center gap-3">
        <div className="grid h-8 w-8 place-items-center rounded-full bg-[var(--purple-soft)] text-[11.5px] font-semibold text-[var(--purple)]">
          {initialsFor(application?.candidate_name)}
        </div>
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-semibold tracking-[-0.005em]">{application?.candidate_name || 'Unknown candidate'}</div>
          <div className="truncate text-[11.5px] text-[var(--mute)]">{application?.candidate_email || 'No email'}</div>
        </div>
        <div className={`font-[var(--font-mono)] text-[13px] font-semibold ${scoreClassName}`}>{score == null ? '—' : score}</div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 font-[var(--font-mono)] text-[10.5px] tracking-[0.05em]">
        {application?.source ? <span className="rounded-[6px] bg-[var(--bg-3)] px-2 py-1 text-[var(--mute)]">{application.source}</span> : null}
        {application?.candidate_position ? <span className="rounded-[6px] bg-[var(--bg-3)] px-2 py-1 text-[var(--mute)]">{application.candidate_position}</span> : null}
        {application?.application_outcome && application.application_outcome !== 'open'
          ? <span className="rounded-[6px] bg-[var(--bg-3)] px-2 py-1 text-[var(--mute)]">{application.application_outcome}</span>
          : null}
      </div>
      <div className="mt-3 flex items-center justify-between border-t border-[var(--line-2)] pt-2 font-[var(--font-mono)] text-[10.5px] text-[var(--mute)]">
        <span>{assessmentId ? 'Assessment complete' : 'Awaiting report'}</span>
        <span>{application?.updated_at ? new Date(application.updated_at).toLocaleDateString() : 'Recent'}</span>
      </div>
    </button>
  );
};

export const JobPipelinePage = ({ onNavigate }) => {
  const { roleId } = useParams();
  const [role, setRole] = useState(null);
  const [applications, setApplications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [inviteLoading, setInviteLoading] = useState(false);

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
      } catch {
        if (!cancelled) {
          setRole(null);
          setApplications([]);
          setError('Failed to load role pipeline.');
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
      if (key in next) {
        next[key].push(application);
      }
    });
    return next;
  }, [applications]);

  const stats = useMemo(() => {
    const completed = applications.filter((item) => assessmentIdFor(item)).length;
    const scores = applications
      .map((item) => Number(item?.taali_score ?? item?.rank_score))
      .filter((value) => Number.isFinite(value));
    const avgScore = scores.length ? Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length) : null;
    return {
      pipeline: applications.length,
      awaitingInvite: grouped.applied.length,
      completed,
      avgScore,
      review: grouped.review.length,
    };
  }, [applications, grouped]);

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
      const pipelineRes = await rolesApi.listPipeline(roleId, { stage: 'all', application_outcome: 'open', limit: 200, offset: 0 });
      setApplications(Array.isArray(pipelineRes?.data?.items) ? pipelineRes.data.items : []);
    } catch {
      setError('Failed to invite candidate.');
    } finally {
      setInviteLoading(false);
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
            <div className="animate-pulse rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
              <div className="h-4 w-1/5 rounded-full bg-[var(--line)]" />
              <div className="mt-4 h-10 w-1/2 rounded-full bg-[var(--line)]" />
              <div className="mt-4 h-4 w-3/4 rounded-full bg-[var(--line)]" />
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              {Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-28 animate-pulse rounded-[var(--radius)] bg-[var(--bg-2)]" />)}
            </div>
          </div>
        ) : error ? (
          <div className="rounded-[var(--radius-lg)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </div>
        ) : (
          <>
            <div className="relative mb-5 overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-8 shadow-[var(--shadow-sm)]">
              <div className="pointer-events-none absolute -bottom-8 right-0 font-[var(--font-display)] text-[160px] font-bold leading-none tracking-[-0.08em] text-[var(--bg-3)] opacity-60">
                {String(role?.name || 'ROLE').split(/\s+/).slice(0, 2).map((part) => part[0]).join('.')}
              </div>
              <div className="relative grid gap-6 lg:grid-cols-[1fr_auto]">
                <div>
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span className="kicker m-0">ROLE · #{role?.id || roleId}</span>
                    <span className="chip green">Active</span>
                    {role?.location ? <span className="chip">{role.location}</span> : null}
                  </div>
                  <h1 className="font-[var(--font-display)] text-[42px] font-semibold leading-none tracking-[-0.035em]">
                    {role?.name || 'Role pipeline'}
                  </h1>
                  <p className="mt-3 max-w-[680px] text-[14.5px] leading-7 text-[var(--mute)]">
                    {role?.description || 'Review candidates by stage, open the assessment signal, and move the pipeline forward without leaving the role workspace.'}
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {role?.hiring_manager ? <span className="chip purple">Hiring manager: <b>{role.hiring_manager}</b></span> : null}
                    {role?.salary_range ? <span className="chip">{role.salary_range}</span> : null}
                    {role?.workable_job_id ? <span className="chip">Source: Workable</span> : null}
                  </div>
                </div>
                <div className="relative flex flex-col items-end gap-3">
                  <div className="flex gap-2">
                    <button type="button" className="icon-btn" title="Share"><Share2 size={15} strokeWidth={1.7} /></button>
                    <button type="button" className="icon-btn" title="Settings"><Settings2 size={15} strokeWidth={1.7} /></button>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    <button type="button" className="btn btn-outline btn-sm">Edit role</button>
                    <button type="button" className="btn btn-purple btn-sm" onClick={handleInvite} disabled={inviteLoading}>
                      {inviteLoading ? 'Inviting…' : <>Invite candidate <span className="arrow">→</span></>}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              <div className="rounded-[var(--radius)] bg-[var(--ink)] p-4 text-[var(--bg)]">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-white/60">In pipeline</div>
                <div className="mt-1 text-[22px] font-semibold tracking-[-0.02em]">{stats.pipeline}</div>
                <div className="mt-1 text-xs text-white/60">Active candidates</div>
              </div>
              <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg-2)] p-4">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Awaiting invite</div>
                <div className="mt-1 text-[22px] font-semibold tracking-[-0.02em]">{stats.awaitingInvite}</div>
                <div className="mt-1 text-xs text-[var(--mute)]">Applied stage</div>
              </div>
              <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg-2)] p-4">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Assessments done</div>
                <div className="mt-1 text-[22px] font-semibold tracking-[-0.02em]">{stats.completed}</div>
                <div className="mt-1 text-xs text-[var(--mute)]">Completed reports</div>
              </div>
              <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg-2)] p-4">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Avg TAALI</div>
                <div className="mt-1 text-[22px] font-semibold tracking-[-0.02em] text-[var(--purple)]">{stats.avgScore == null ? '—' : stats.avgScore}</div>
                <div className="mt-1 text-xs text-[var(--mute)]">Across scored candidates</div>
              </div>
              <div className="rounded-[var(--radius)] border border-[var(--line)] bg-[var(--bg-2)] p-4">
                <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-[var(--mute)]">Ready for review</div>
                <div className="mt-1 text-[22px] font-semibold tracking-[-0.02em]">{stats.review}</div>
                <div className="mt-1 text-xs text-[var(--mute)]">Panel-ready candidates</div>
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
                    <span className="chip">SORT · Composite</span>
                    <button type="button" className="btn btn-outline btn-sm" onClick={handleInvite}>Bulk invite</button>
                  </div>
                </div>

                <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
                  {STAGES.map((stage) => (
                    <div key={stage.id} className="flex min-h-[460px] flex-col gap-3 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-4">
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
                        <div className="mt-8 rounded-[var(--radius)] border border-dashed border-[var(--line)] px-4 py-5 text-center font-[var(--font-mono)] text-[12px] uppercase tracking-[0.06em] text-[var(--mute)]">
                          Empty
                        </div>
                      ) : (
                        grouped[stage.id].map((application) => (
                          <CandidateCard key={application.id} application={application} onNavigate={onNavigate} />
                        ))
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-5">
                <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                  <h3 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">Role <em>summary</em></h3>
                  <p className="mt-1 text-[12.5px] text-[var(--mute)]">A quick read on the role and the pipeline.</p>
                  <div className="mt-4 space-y-3 text-sm">
                    <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Role</span><span>{role?.name || '—'}</span></div>
                    <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Applications</span><span>{applications.length}</span></div>
                    <div className="flex justify-between border-b border-[var(--line-2)] pb-3"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Review ready</span><span>{grouped.review.length}</span></div>
                    <div className="flex justify-between"><span className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.08em] text-[var(--mute)]">Live assessments</span><span>{grouped.in_assessment.length}</span></div>
                  </div>
                </div>

                <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
                  <h3 className="font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.02em]">Interview <em>focus</em></h3>
                  <p className="mt-1 text-[12.5px] text-[var(--mute)]">Use these prompts when the candidate reaches panel.</p>
                  <div className="mt-4 space-y-4">
                    {(Array.isArray(role?.interview_focus?.questions) ? role.interview_focus.questions : [
                      'Ask how they decide when Claude output is wrong but plausible.',
                      'Probe the release-safety tradeoff they made under time pressure.',
                      'Have them walk through the smallest safe patch they would ship first.',
                    ]).map((question) => (
                      <div key={question} className="border-b border-[var(--line-2)] pb-4 last:border-b-0 last:pb-0">
                        <div className="text-[13.5px] font-medium">{question}</div>
                        <div className="mt-1 text-[12.5px] leading-6 text-[var(--mute)]">Interview signal aligned to the role and assessment evidence.</div>
                      </div>
                    ))}
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
