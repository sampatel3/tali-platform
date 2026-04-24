import React, { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Copy,
  Download,
  ExternalLink,
  Loader2,
  Mail,
} from 'lucide-react';

import { assessments as assessmentsApi, roles as rolesApi } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { AppShell } from '../../shared/layout/TaaliLayout';
import { Badge, Button, Panel, Spinner } from '../../shared/ui/TaaliPrimitives';
import { buildStandingCandidateReportModel } from './assessmentViewModels';
import {
  aiCollabBand,
  buildSixAxisMetrics,
  copyText,
  formatDateTime,
  formatScale100,
  formatStatusLabel,
  recommendationFromScore,
  uniq,
} from './redesignUtils';

const resolveCompletedAssessmentId = (application) => (
  application?.score_summary?.assessment_id
  || application?.valid_assessment_id
  || null
);

const resolveCompletedAssessmentStatus = (application) => (
  String(application?.score_summary?.assessment_status || application?.valid_assessment_status || '').toLowerCase()
);

const hasCompletedAssessment = (application) => {
  const id = resolveCompletedAssessmentId(application);
  const status = resolveCompletedAssessmentStatus(application);
  return Boolean(id) && (status === 'completed' || status === 'completed_due_to_timeout');
};

const HeroStat = ({ label, value, description, highlight = false }) => (
  <div>
    <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.1em] text-white/55">{label}</div>
    <div className="mt-1 text-[30px] font-semibold tracking-[-0.03em]" style={highlight ? { color: 'var(--lime)' } : undefined}>{value}</div>
    <div className="mt-1 text-[12px] text-white/55">{description}</div>
  </div>
);

const RankedItem = ({ index, title, description, scoreLabel = '—', evidence = null, tone = 'default' }) => (
  <div className="grid grid-cols-[34px_minmax(0,1fr)_auto] gap-4 border-b border-[var(--line-2)] py-4 last:border-b-0">
    <div className="font-[var(--font-display)] text-[28px] font-semibold leading-none tracking-[-0.03em]" style={{ color: tone === 'warning' ? 'var(--amber)' : 'var(--purple)' }}>
      {String(index + 1).padStart(2, '0')}
    </div>
    <div>
      <div className="text-[15px] font-semibold">{title}</div>
      <div className="mt-1 text-[13px] leading-6 text-[var(--ink-2)]">{description}</div>
      {evidence ? (
        <div className="mt-3 rounded-[14px] border-l-[3px] border-[var(--purple)] bg-[var(--bg)] px-4 py-3 font-[var(--font-mono)] text-[12px] leading-6 text-[var(--ink-2)]">
          {evidence}
        </div>
      ) : null}
    </div>
    <div className="font-[var(--font-mono)] text-[13px] font-semibold" style={{ color: tone === 'warning' ? 'var(--amber)' : 'var(--green)' }}>
      {scoreLabel}
    </div>
  </div>
);

const DimensionList = ({ items }) => (
  <div className="space-y-3">
    {items.map((item) => {
      const color = item.tone === 'success'
        ? 'var(--green)'
        : item.tone === 'warning'
          ? 'var(--amber)'
          : item.tone === 'danger'
            ? 'var(--red)'
            : 'var(--purple)';
      return (
        <div key={item.key} className="border-b border-[var(--line-2)] pb-3 last:border-b-0 last:pb-0">
          <div className="mb-1.5 flex items-end justify-between gap-3">
            <span className="text-[13.5px] font-medium">{item.label}</span>
            <span className="font-[var(--font-mono)] text-[12.5px]" style={{ color }}>{item.displayValue}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-[var(--bg)]">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max(0, Math.min(100, Number(item.percent || 0)))}%`,
                background: color,
              }}
            />
          </div>
          <p className="mt-2 text-[12.5px] leading-6 text-[var(--mute)]">{item.note}</p>
        </div>
      );
    })}
  </div>
);

export const CandidateStandingReportPage = ({ onNavigate }) => {
  const { applicationId } = useParams();
  const { showToast } = useToast();
  const [application, setApplication] = useState(null);
  const [assessment, setAssessment] = useState(null);
  const [role, setRole] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    const numericId = Number(applicationId);
    if (!Number.isFinite(numericId)) {
      setLoading(false);
      setError('Candidate report unavailable.');
      return;
    }

    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const appRes = await rolesApi.getApplication(numericId, {
          params: { include_cv_text: true },
        });
        if (cancelled) return;

        const nextApplication = appRes?.data || null;
        setApplication(nextApplication);

        const requests = [];
        if (hasCompletedAssessment(nextApplication)) {
          requests.push(
            assessmentsApi.get(resolveCompletedAssessmentId(nextApplication))
              .then((res) => ({ kind: 'assessment', data: res?.data || null }))
              .catch(() => ({ kind: 'assessment', data: null }))
          );
        }
        if (nextApplication?.role_id) {
          requests.push(
            rolesApi.get(nextApplication.role_id)
              .then((res) => ({ kind: 'role', data: res?.data || null }))
              .catch(() => ({ kind: 'role', data: null }))
          );
        }

        if (requests.length > 0) {
          const results = await Promise.all(requests);
          if (cancelled) return;
          results.forEach((result) => {
            if (result.kind === 'assessment') setAssessment(result.data);
            if (result.kind === 'role') setRole(result.data);
          });
        } else {
          setAssessment(null);
          setRole(null);
        }
      } catch (err) {
        if (!cancelled) {
          setApplication(null);
          setAssessment(null);
          setRole(null);
          setError(err?.response?.data?.detail || 'Failed to load candidate report.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [applicationId]);

  const identity = useMemo(() => ({
    sectionLabel: `Standing report · application #${application?.id || applicationId}`,
    name: application?.candidate_name || application?.candidate_email || 'Candidate',
    email: application?.candidate_email || '',
    position: application?.candidate_position || '',
    roleName: application?.role_name || role?.name || '',
    applicationStatus: application?.application_outcome || '',
    durationLabel: assessment?.duration_taken != null ? `${Math.round(Number(assessment.duration_taken) / 60)} min` : '—',
    completedLabel: assessment?.completed_at ? new Date(assessment.completed_at).toLocaleDateString() : '',
    assessmentId: assessment?.id || null,
  }), [application, applicationId, assessment, role?.name]);

  const reportModel = useMemo(() => buildStandingCandidateReportModel({
    application,
    completedAssessment: assessment,
    identity,
  }), [application, assessment, identity]);
  const recommendation = useMemo(() => recommendationFromScore(reportModel?.summaryModel?.taaliScore), [reportModel]);
  const metrics = useMemo(() => buildSixAxisMetrics(assessment), [assessment]);
  const aiCollab = useMemo(() => aiCollabBand(assessment), [assessment]);

  const strengths = useMemo(() => uniq([
    reportModel?.strongestSignalDescription,
    ...(reportModel?.roleFitModel?.rationaleBullets || []),
    ...(reportModel?.roleFitModel?.matchingSkills || []).map((item) => `Matching skill: ${item}`),
    reportModel?.summaryModel?.heuristicSummary,
  ], 4), [reportModel]);

  const risks = useMemo(() => uniq([
    reportModel?.probeDescription,
    reportModel?.roleFitModel?.firstRequirementGap?.requirement
      ? `Requirement gap: ${reportModel.roleFitModel.firstRequirementGap.requirement}`
      : null,
    ...(reportModel?.roleFitModel?.concerns || []),
    ...(reportModel?.roleFitModel?.missingSkills || []).map((item) => `Skill gap: ${item}`),
  ], 4), [reportModel]);

  const focusQuestions = Array.isArray(role?.interview_focus?.questions) ? role.interview_focus.questions : [];

  const handleCopyLink = async () => {
    try {
      await copyText(window.location.href);
      showToast('Link copied to clipboard.', 'success');
    } catch {
      showToast('Failed to copy report link.', 'error');
    }
  };

  const handleEmailPanel = () => {
    const subject = encodeURIComponent(`Candidate standing report: ${identity.name}`);
    const body = encodeURIComponent(`Review this Taali standing report:\n\n${window.location.href}`);
    window.location.href = `mailto:?subject=${subject}&body=${body}`;
  };

  const handleDownloadReport = async () => {
    if (!application?.id) return;
    setBusyAction('download');
    try {
      const res = await rolesApi.downloadApplicationReport(application.id);
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `candidate-report-${application.id}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to download candidate report.', 'error');
    } finally {
      setBusyAction('');
    }
  };

  if (loading) {
    return (
      <AppShell currentPage="candidates" onNavigate={onNavigate}>
        <div className="page">
          <div className="grid min-h-[320px] place-items-center rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] shadow-[var(--shadow-sm)]">
            <div className="flex items-center gap-3 text-sm text-[var(--mute)]">
              <Spinner size={20} />
              Loading standing report...
            </div>
          </div>
        </div>
      </AppShell>
    );
  }

  if (error || !application) {
    return (
      <AppShell currentPage="candidates" onNavigate={onNavigate}>
        <div className="page">
          <div className="rounded-[var(--radius-lg)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error || 'Candidate report unavailable.'}
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell currentPage="candidates" onNavigate={onNavigate}>
      <div className="page">
        <button
          type="button"
          className="mb-4 inline-flex items-center gap-2 font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.08em] text-[var(--mute)] transition-colors hover:text-[var(--purple)]"
          onClick={() => onNavigate('candidates')}
        >
          <ArrowLeft size={12} />
          Back to candidates
        </button>

        <section className="relative overflow-hidden rounded-[var(--radius-xl)] bg-[var(--ink)] px-7 py-9 text-[var(--bg)] md:px-10">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0"
            style={{
              backgroundImage:
                'linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px)',
              backgroundSize: '42px 42px',
              maskImage: 'radial-gradient(70% 100% at 20% 30%, black, transparent 75%)',
            }}
          />
          <div className="relative">
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <div className="kicker !text-[var(--purple-2)]">{identity.sectionLabel}</div>
              <Badge variant={recommendation.variant}>{recommendation.label}</Badge>
            </div>
            <h1 className="max-w-[920px] font-[var(--font-display)] text-[44px] font-semibold leading-[0.98] tracking-[-0.04em] md:text-[58px]">
              {identity.name} - where they <span className="text-[var(--purple-2)]">stand</span> in the pipeline.
            </h1>
            <p className="mt-4 max-w-[760px] text-[18px] leading-8 text-white/78">
              A role-anchored, shareable summary. Evidence-first: the standing report keeps the recommendation attached to the assessment signal and the source documents already on file.
            </p>

            <div className="mt-7 grid gap-4 border-t border-white/12 pt-5 md:grid-cols-2 xl:grid-cols-4">
              <HeroStat
                label="Composite"
                value={reportModel?.summaryModel?.taaliScore != null ? formatScale100(reportModel.summaryModel.taaliScore) : '—'}
                description={reportModel?.summaryModel?.source?.label || 'TAALI decision score'}
                highlight
              />
              <HeroStat
                label="Role fit"
                value={reportModel?.summaryModel?.roleFitScore != null ? formatScale100(reportModel.summaryModel.roleFitScore) : '—'}
                description={identity.roleName || 'Role-fit evidence'}
                highlight
              />
              <HeroStat
                label="AI-collaboration"
                value={aiCollab.score != null ? `${aiCollab.label} · ${Math.round(aiCollab.score * 10)}` : 'Pending'}
                description={assessment ? 'Assessment-derived runtime signal' : 'Assessment still pending'}
              />
              <HeroStat
                label="Signal source"
                value={assessment ? formatStatusLabel(assessment.status) : 'Application'}
                description={assessment?.completed_at ? `Updated ${formatDateTime(assessment.completed_at)}` : formatStatusLabel(application.application_outcome)}
              />
            </div>
          </div>
        </section>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-4 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] px-5 py-4 shadow-[var(--shadow-sm)]">
          <div className="flex items-center gap-3">
            <div className="grid h-8 w-8 place-items-center rounded-[10px] bg-[var(--purple-soft)] text-[var(--purple)]">
              <Copy size={15} />
            </div>
            <div>
              <div className="text-[13.5px] font-semibold">Shareable link</div>
              <div className="text-[12px] text-[var(--mute)]">Internal review link · recruiter access required</div>
            </div>
          </div>
          <div className="min-w-[240px] grow rounded-[10px] bg-[var(--bg)] px-4 py-2 font-[var(--font-mono)] text-[12px] text-[var(--ink-2)]">
            {window.location.hostname}{window.location.pathname}
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn btn-outline btn-sm" onClick={handleCopyLink}>Copy</button>
            <button type="button" className="btn btn-outline btn-sm" onClick={handleEmailPanel}>
              <Mail size={14} />
              Email to panel
            </button>
            <button type="button" className="btn btn-purple btn-sm" onClick={handleDownloadReport} disabled={busyAction !== ''}>
              {busyAction === 'download' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
              Download PDF
            </button>
          </div>
        </div>

        <div className="mt-5 grid gap-5 xl:grid-cols-[1.4fr_1fr]">
          <div className="space-y-4">
            <Panel className="p-6">
              <div className="kicker">Verdict</div>
              <h2 className="mt-3 font-[var(--font-display)] text-[32px] font-semibold tracking-[-0.03em]">
                {recommendation.ctaLabel}. <span className="text-[var(--purple)]">With evidence.</span>
              </h2>
              <p className="mt-4 text-[15px] leading-7 text-[var(--ink-2)]">
                {reportModel?.recruiterSummaryText || reportModel?.summaryModel?.heuristicSummary || 'The standing report is ready for hiring-team review.'}
              </p>
              <p className="mt-3 text-[14px] leading-7 text-[var(--mute)]">
                <b className="text-[var(--ink-2)]">Watch-out.</b>
                {' '}
                {reportModel?.probeDescription || 'Use the interview to validate the weakest evidence behind the TAALI score.'}
              </p>
              <div className="mt-5 flex flex-wrap gap-2">
                {assessment?.id ? (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => onNavigate('candidate-detail', { candidateDetailAssessmentId: assessment.id })}
                  >
                    Full assessment <span className="arrow">→</span>
                  </button>
                ) : null}
                {application?.candidate_email ? (
                  <button type="button" className="btn btn-outline btn-sm" onClick={() => window.location.href = `mailto:${application.candidate_email}`}>
                    Email candidate
                  </button>
                ) : null}
              </div>
            </Panel>

            <Panel className="p-6">
              <h2 className="font-[var(--font-display)] text-[26px] font-semibold tracking-[-0.025em]">Top <span className="text-[var(--purple)]">strengths</span></h2>
              <p className="mt-1 text-[13px] text-[var(--mute)]">Ranked by the evidence currently attached to this role and assessment.</p>
              <div className="mt-4">
                {strengths.length ? strengths.map((item, index) => (
                  <RankedItem
                    key={item}
                    index={index}
                    title={item}
                    description={index === 0
                      ? (reportModel?.strongestSignalDescription || 'This is the most durable signal in the standing report.')
                      : 'This signal stays attached to the candidate record so the panel can review the underlying evidence quickly.'}
                    scoreLabel={index === 0 && reportModel?.summaryModel?.taaliScore != null ? formatScale100(reportModel.summaryModel.taaliScore) : 'Signal'}
                    evidence={index === 0 ? reportModel?.summaryModel?.heuristicSummary : null}
                  />
                )) : (
                  <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                    No strengths have been generated for this standing report yet.
                  </div>
                )}
              </div>
            </Panel>

            <Panel className="p-6">
              <h2 className="font-[var(--font-display)] text-[26px] font-semibold tracking-[-0.025em]">Risks to <span className="text-[var(--purple)]">probe</span></h2>
              <p className="mt-1 text-[13px] text-[var(--mute)]">Use these in the hiring panel instead of re-running the assessment.</p>
              <div className="mt-4">
                {risks.length ? risks.map((item, index) => (
                  <RankedItem
                    key={item}
                    index={index}
                    title={item}
                    description="This is the evidence gap or risk signal that deserves direct follow-up in the interview."
                    scoreLabel={index === 0 ? recommendation.label : 'Probe'}
                    tone="warning"
                  />
                )) : (
                  <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                    No major risks were surfaced for this candidate yet.
                  </div>
                )}
              </div>
            </Panel>
          </div>

          <div className="space-y-4">
            <Panel className="p-6">
              <h2 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.025em]">Scored <span className="text-[var(--purple)]">dimensions</span></h2>
              <p className="mt-1 text-[13px] text-[var(--mute)]">
                {assessment ? 'The six recruiter review axes from the completed assessment.' : 'Dimension scoring appears once a completed assessment is attached.'}
              </p>
              <div className="mt-4">
                {assessment ? (
                  <DimensionList items={metrics} />
                ) : (
                  <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                    This candidate currently has application-level evidence only. Dimension scoring will populate after a completed assessment.
                  </div>
                )}
              </div>
            </Panel>

            <Panel className="p-6">
              <h2 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.025em]">References <span className="text-[var(--purple)]">& docs</span></h2>
              <div className="mt-4 space-y-3">
                <div className="rounded-[12px] border border-[var(--line-2)] bg-[var(--bg)] px-4 py-3">
                  <div className="text-[13.5px] font-semibold">{application?.candidate_name || application?.candidate_email}</div>
                  <div className="mt-1 text-[12px] text-[var(--mute)]">{application?.candidate_email || 'Email unavailable'}</div>
                </div>
                <div className="rounded-[12px] border border-[var(--line-2)] bg-[var(--bg)] px-4 py-3">
                  <div className="text-[13.5px] font-semibold">CV on file</div>
                  <div className="mt-1 text-[12px] text-[var(--mute)]">{application?.cv_filename || assessment?.candidate_cv_filename || assessment?.cv_filename || 'Not uploaded'}</div>
                </div>
                <div className="rounded-[12px] border border-[var(--line-2)] bg-[var(--bg)] px-4 py-3">
                  <div className="text-[13.5px] font-semibold">Matching skills</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {(reportModel?.roleFitModel?.matchingSkills || []).slice(0, 6).map((item) => (
                      <Badge key={item} variant="success">{item}</Badge>
                    ))}
                    {(reportModel?.roleFitModel?.matchingSkills || []).length === 0 ? (
                      <span className="text-[12px] text-[var(--mute)]">No extracted matching skills yet.</span>
                    ) : null}
                  </div>
                </div>
              </div>
            </Panel>

            <Panel className="p-6">
              <h2 className="font-[var(--font-display)] text-[24px] font-semibold tracking-[-0.025em]">How to <span className="text-[var(--purple)]">interview</span></h2>
              <div className="mt-4 space-y-3">
                {focusQuestions.length ? focusQuestions.slice(0, 3).map((item, index) => (
                  <div key={`${item.question}-${index}`} className="rounded-[12px] border border-[var(--line-2)] bg-[var(--bg)] px-4 py-4">
                    <div className="font-[var(--font-mono)] text-[10.5px] uppercase tracking-[0.08em] text-[var(--purple)]">{`Q${index + 1}`}</div>
                    <div className="mt-2 text-[14px] font-semibold">{item.question}</div>
                    {Array.isArray(item?.what_to_listen_for) && item.what_to_listen_for.length > 0 ? (
                      <div className="mt-2 text-[12.5px] leading-6 text-[var(--ink-2)]">
                        <span className="font-medium">Look for:</span>
                        {' '}
                        {item.what_to_listen_for.join(' • ')}
                      </div>
                    ) : null}
                  </div>
                )) : (
                  <div className="rounded-[14px] border border-dashed border-[var(--line)] bg-[var(--bg)] px-4 py-10 text-center text-sm text-[var(--mute)]">
                    No interview-focus questions are available for this role yet.
                  </div>
                )}
              </div>
              {assessment?.id ? (
                <div className="mt-4">
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={() => onNavigate('candidate-detail', { candidateDetailAssessmentId: assessment.id })}
                  >
                    Open full assessment <ExternalLink size={14} />
                  </button>
                </div>
              ) : null}
            </Panel>
          </div>
        </div>
      </div>
    </AppShell>
  );
};

export default CandidateStandingReportPage;
