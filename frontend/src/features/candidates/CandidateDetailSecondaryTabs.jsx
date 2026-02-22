import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  MessageSquare,
  PlayCircle,
  StickyNote,
  TerminalSquare,
} from 'lucide-react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';

import {
  Badge,
  Button,
  Card,
  Panel,
} from '../../shared/ui/TaaliPrimitives';
import { cvScoreColor, formatCvScore100, toCvScore100 } from './candidatesUiUtils';

const normalizeAssessmentStatus = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'submitted' || normalized === 'graded') return 'completed';
  if (normalized.includes('timeout')) return 'completed_due_to_timeout';
  if (normalized.includes('progress')) return 'in_progress';
  if (normalized.includes('expire')) return 'expired';
  if (normalized.includes('abandon')) return 'abandoned';
  if (normalized.includes('complete')) return 'completed';
  return normalized || 'pending';
};

const promptEmptyMessageForStatus = (status) => {
  if (status === 'in_progress') {
    return 'Assessment in progress — prompt activity will populate here as the candidate works.';
  }
  if (status === 'completed' || status === 'completed_due_to_timeout') {
    return 'Prompt analytics are still processing. Refresh in a moment to load prompt activity.';
  }
  if (status === 'expired' || status === 'abandoned') {
    return 'This assessment was not completed, so no prompt activity is available.';
  }
  return 'No prompt activity is available for this assessment yet.';
};

export const CandidateAiUsageTab = ({ candidate, avgCalibrationScore }) => {
  const assessment = candidate._raw || {};
  const assessmentStatus = normalizeAssessmentStatus(assessment.status || candidate.status);
  const promptEmptyMessage = promptEmptyMessageForStatus(assessmentStatus);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Card className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)]">Avg Prompt clarity</div>
          <div className="text-2xl font-bold text-[var(--taali-text)]">{assessment.prompt_quality_score?.toFixed(1) || '--'}<span className="text-sm text-[var(--taali-muted)]">/10</span></div>
        </Card>
        <Card className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)]">Time to First Prompt</div>
          <div className="text-2xl font-bold text-[var(--taali-text)]">{assessment.time_to_first_prompt_seconds ? `${Math.floor(assessment.time_to_first_prompt_seconds / 60)}m ${Math.round(assessment.time_to_first_prompt_seconds % 60)}s` : '--'}</div>
        </Card>
        <Card className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)]">Browser Focus</div>
          <div
            className={`text-2xl font-bold ${assessment.browser_focus_ratio != null && assessment.browser_focus_ratio < 0.8 ? 'text-[var(--taali-danger)]' : 'text-[var(--taali-text)]'}`}
          >
            {assessment.browser_focus_ratio != null ? `${Math.round(assessment.browser_focus_ratio * 100)}%` : '--'}
          </div>
        </Card>
        <Card className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)]">Tab Switches</div>
          <div className={`text-2xl font-bold ${assessment.tab_switch_count > 5 ? 'text-[var(--taali-danger)]' : 'text-[var(--taali-text)]'}`}>{assessment.tab_switch_count ?? '--'}</div>
        </Card>
        <Card className="p-4">
          <div className="font-mono text-xs text-[var(--taali-muted)]">Calibration</div>
          <div className="text-2xl font-bold text-[var(--taali-text)]">{assessment.calibration_score != null ? `${assessment.calibration_score.toFixed(1)}/10` : '--'}</div>
          <div className="mt-1 font-mono text-xs text-[var(--taali-muted)]">vs avg {avgCalibrationScore != null ? `${avgCalibrationScore.toFixed(1)}/10` : '--'}</div>
        </Card>
      </div>

      {assessment.browser_focus_ratio != null && assessment.browser_focus_ratio < 0.8 ? (
        <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
          <div className="flex items-center gap-2 font-bold text-[var(--taali-warning)]"><AlertTriangle size={18} /> Low Browser Focus ({Math.round(assessment.browser_focus_ratio * 100)}%)</div>
          <div className="mt-1 text-xs text-[var(--taali-muted)]">Candidate spent less than 80% of assessment time with the browser in focus. {assessment.tab_switch_count > 5 ? `${assessment.tab_switch_count} tab switches recorded.` : ''}</div>
        </Panel>
      ) : null}

      {assessment.prompt_analytics?.per_prompt_scores?.length > 0 ? (
        <Panel className="p-4">
          <div className="mb-4 font-bold">Prompt clarity progression</div>
          <div style={{ width: '100%', height: 220 }}>
            <ResponsiveContainer>
              <LineChart
                data={assessment.prompt_analytics.per_prompt_scores.map((p, i) => ({
                  name: `#${i + 1}`,
                  clarity: p.clarity || 0,
                  specificity: p.specificity || 0,
                  efficiency: p.efficiency || 0,
                }))}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#ebe7f8" />
                <XAxis dataKey="name" tick={{ fontSize: 10, fontFamily: 'monospace' }} />
                <YAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
                <Tooltip />
                <Line type="monotone" dataKey="clarity" stroke="var(--taali-purple)" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="specificity" stroke="#2d2d44" strokeWidth={1.3} />
                <Line type="monotone" dataKey="efficiency" stroke="#6b7280" strokeWidth={1.3} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      ) : null}

      <Panel className="p-4">
        <div className="mb-4 font-bold">Prompt Log ({(candidate.promptsList || []).length} prompts)</div>
        <div className="mb-3 font-mono text-xs text-[var(--taali-muted)]">
          Clarity = clear, structured asks · Specificity = concrete context and references · Efficiency = prompt-to-action quality (all /10)
        </div>
        <div className="space-y-3">
          {(candidate.promptsList || []).map((p, i) => {
            const perPrompt = assessment.prompt_analytics?.per_prompt_scores?.[i];
            return (
              <Card key={i} className="p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Badge variant="purple" className="font-mono text-[11px]">#{i + 1}</Badge>
                    {p.timestamp ? <span className="font-mono text-xs text-[var(--taali-muted)]">{new Date(p.timestamp).toLocaleTimeString()}</span> : null}
                    {perPrompt ? <span className="font-mono text-xs text-[var(--taali-muted)]">{perPrompt.word_count} words</span> : null}
                  </div>
                  {perPrompt ? (
                    <div className="flex items-center gap-1">
                      <Badge variant="purple" className="font-mono text-[11px]" title="Clarity: how understandable and structured the prompt is.">
                        Clarity: {perPrompt.clarity}
                      </Badge>
                      <Badge variant="muted" className="font-mono text-[11px]" title="Specificity: how concrete the prompt context is (files, errors, code).">
                        Specificity: {perPrompt.specificity}
                      </Badge>
                      <Badge variant="muted" className="font-mono text-[11px]" title="Efficiency: whether the prompt led to actionable iteration.">
                        Efficiency: {perPrompt.efficiency}
                      </Badge>
                    </div>
                  ) : null}
                </div>

                <div className="bg-[var(--taali-purple-soft)] p-2 font-mono text-sm text-[var(--taali-text)]">
                  {p.message || p.text}
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {perPrompt?.has_context ? <Badge variant="success" className="font-mono text-[11px]">Has Context</Badge> : null}
                  {perPrompt?.is_vague ? <Badge variant="warning" className="font-mono text-[11px]">Vague</Badge> : null}
                  {p.paste_detected ? <Badge variant="warning" className="font-mono text-[11px]">PASTED</Badge> : null}
                  {p.response_latency_ms ? <Badge variant="muted" className="font-mono text-[11px]">{p.response_latency_ms}ms</Badge> : null}
                </div>
              </Card>
            );
          })}

          {(candidate.promptsList || []).length === 0 ? (
            <Card className="py-8 text-center text-[var(--taali-muted)]">{promptEmptyMessage}</Card>
          ) : null}
        </div>
      </Panel>

      {(candidate.promptsList || []).length > 0 && assessment.prompt_analytics ? (
        <Panel className="p-4">
          <div className="mb-3 font-bold">Prompt Statistics</div>
          <div className="grid grid-cols-2 gap-3 font-mono text-sm md:grid-cols-4">
            <div><span className="text-[var(--taali-muted)]">Avg Words:</span> {assessment.prompt_analytics.metric_details?.word_count_avg || '—'}</div>
            <div><span className="text-[var(--taali-muted)]">Questions:</span> {assessment.prompt_analytics.metric_details?.question_presence ? `${(assessment.prompt_analytics.metric_details.question_presence * 100).toFixed(0)}%` : '—'}</div>
            <div><span className="text-[var(--taali-muted)]">Code Context:</span> {assessment.prompt_analytics.metric_details?.code_snippet_rate ? `${(assessment.prompt_analytics.metric_details.code_snippet_rate * 100).toFixed(0)}%` : '—'}</div>
            <div><span className="text-[var(--taali-muted)]">Paste Detected:</span> {assessment.prompt_analytics.metric_details?.paste_ratio ? `${(assessment.prompt_analytics.metric_details.paste_ratio * 100).toFixed(0)}%` : '0%'}</div>
          </div>
        </Panel>
      ) : null}
    </div>
  );
};

export const CandidateCvFitTab = ({
  candidate,
  onDownloadCandidateDoc,
  onRequestCvUpload = null,
  requestingCvUpload = false,
}) => {
  const assessment = candidate._raw || {};
  const cvMatch = assessment.cv_job_match_details || assessment.prompt_analytics?.cv_job_match?.details || {};
  const matchScores = assessment.prompt_analytics?.cv_job_match || {};
  const overall = toCvScore100(matchScores.overall ?? assessment.cv_job_match_score, cvMatch);
  const skills = toCvScore100(matchScores.skills, cvMatch);
  const experience = toCvScore100(matchScores.experience, cvMatch);
  const requirementsMatch = toCvScore100(cvMatch.requirements_match_score_100, cvMatch);
  const requirementsCoverage = cvMatch.requirements_coverage || {};
  const requirementsAssessment = Array.isArray(cvMatch.requirements_assessment) ? cvMatch.requirements_assessment : [];
  const rationaleBullets = Array.isArray(cvMatch.score_rationale_bullets)
    ? cvMatch.score_rationale_bullets
      .map((item) => String(item || '').trim())
      .filter(Boolean)
      .slice(0, 6)
    : [];
  const fallbackWhyBullets = [
    requirementsCoverage.total
      ? `Recruiter requirements coverage: ${requirementsCoverage.met ?? 0}/${requirementsCoverage.total} met, ${requirementsCoverage.partially_met ?? 0} partial, ${requirementsCoverage.missing ?? 0} missing.`
      : null,
    cvMatch.matching_skills?.length
      ? `Strong CV-to-role evidence: ${cvMatch.matching_skills.slice(0, 4).join(', ')}.`
      : null,
    cvMatch.missing_skills?.length
      ? `Gaps vs role requirements: ${cvMatch.missing_skills.slice(0, 4).join(', ')}.`
      : null,
    cvMatch.concerns?.length
      ? `Risk signals from CV evidence: ${cvMatch.concerns.slice(0, 2).join('; ')}.`
      : null,
  ].filter(Boolean);
  const whyBullets = rationaleBullets.length > 0 ? rationaleBullets : fallbackWhyBullets;
  const hasCv = Boolean(assessment.candidate_cv_filename || assessment.cv_filename);

  return (
    <div className="space-y-6">
      {overall != null ? (
        <>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <Card className="p-6 text-center">
              <div className="mb-1 font-mono text-xs text-gray-500">Overall Match</div>
              <div className="text-4xl font-bold" style={{ color: cvScoreColor(overall, cvMatch) }}>{formatCvScore100(overall, cvMatch)}</div>
            </Card>
            <Card className="p-6 text-center">
              <div className="mb-1 font-mono text-xs text-gray-500">Skills Match</div>
              <div className="text-4xl font-bold" style={{ color: skills != null ? cvScoreColor(skills, cvMatch) : 'var(--taali-muted)' }}>{skills != null ? formatCvScore100(skills, cvMatch) : '—'}</div>
            </Card>
            <Card className="p-6 text-center">
              <div className="mb-1 font-mono text-xs text-gray-500">Experience</div>
              <div className="text-4xl font-bold" style={{ color: experience != null ? cvScoreColor(experience, cvMatch) : 'var(--taali-muted)' }}>{experience != null ? formatCvScore100(experience, cvMatch) : '—'}</div>
            </Card>
          </div>

          {whyBullets.length > 0 ? (
            <Panel className="p-4">
              <div className="mb-2 font-bold">Why this score</div>
              <ul className="space-y-1.5">
                {whyBullets.map((item, index) => (
                  <li key={`why-score-${index}`} className="flex items-start gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-0.5 text-[var(--taali-success)]">•</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {(requirementsMatch != null || requirementsAssessment.length > 0) ? (
            <Panel className="p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="font-bold">Recruiter Requirements Fit</div>
                {requirementsMatch != null ? (
                  <Badge variant="purple" className="font-mono text-[11px]">{formatCvScore100(requirementsMatch, cvMatch)}</Badge>
                ) : null}
              </div>
              {requirementsCoverage.total ? (
                <div className="mb-3 grid grid-cols-2 gap-2 font-mono text-xs text-[var(--taali-muted)] md:grid-cols-4">
                  <div>Total: {requirementsCoverage.total}</div>
                  <div>Met: {requirementsCoverage.met ?? 0}</div>
                  <div>Partial: {requirementsCoverage.partially_met ?? 0}</div>
                  <div>Missing: {requirementsCoverage.missing ?? 0}</div>
                </div>
              ) : null}
              {requirementsAssessment.length > 0 ? (
                <div className="space-y-2">
                  {requirementsAssessment.map((item, index) => {
                    const priority = String(item?.priority || 'nice_to_have');
                    const status = String(item?.status || 'unknown');
                    const priorityBadge = priority === 'must_have' || priority === 'constraint' ? 'warning' : 'muted';
                    const statusBadge = status === 'met' ? 'success' : (status === 'partially_met' ? 'warning' : (status === 'missing' ? 'warning' : 'muted'));
                    return (
                      <Card key={`${item?.requirement || 'requirement'}-${index}`} className="p-3">
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                          <Badge variant={priorityBadge} className="font-mono text-[11px]">{priority.replace(/_/g, ' ')}</Badge>
                          <Badge variant={statusBadge} className="font-mono text-[11px]">{status.replace(/_/g, ' ')}</Badge>
                        </div>
                        <div className="text-sm font-semibold text-[var(--taali-text)]">{item?.requirement || 'Unnamed requirement'}</div>
                        {item?.evidence ? <div className="mt-1 text-xs text-[var(--taali-muted)]">Evidence: {item.evidence}</div> : null}
                        {item?.impact ? <div className="mt-1 text-xs text-[var(--taali-muted)]">Impact: {item.impact}</div> : null}
                      </Card>
                    );
                  })}
                </div>
              ) : (
                <div className="font-mono text-xs text-[var(--taali-muted)]">No requirement-level breakdown was returned for this score.</div>
              )}
            </Panel>
          ) : null}

          {cvMatch.matching_skills?.length > 0 ? (
            <Panel className="p-4">
              <div className="mb-3 font-bold text-[var(--taali-success)]">Matching Skills</div>
              <div className="flex flex-wrap gap-1.5">
                {cvMatch.matching_skills.map((skill, i) => (
                  <Badge key={i} variant="success" className="font-mono text-[11px]">{skill}</Badge>
                ))}
              </div>
            </Panel>
          ) : null}

          {cvMatch.missing_skills?.length > 0 ? (
            <Panel className="p-4">
              <div className="mb-3 font-bold text-[var(--taali-danger)]">Missing Skills</div>
              <div className="flex flex-wrap gap-1.5">
                {cvMatch.missing_skills.map((skill, i) => (
                  <Badge key={i} variant="warning" className="font-mono text-[11px]">{skill}</Badge>
                ))}
              </div>
            </Panel>
          ) : null}

          {cvMatch.experience_highlights?.length > 0 ? (
            <Panel className="p-4">
              <div className="mb-3 font-bold">Relevant Experience</div>
              <ul className="space-y-1">
                {cvMatch.experience_highlights.map((exp, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-0.5 text-[var(--taali-success)]">•</span>{exp}
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {cvMatch.concerns?.length > 0 ? (
            <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-4">
              <div className="mb-3 font-bold text-[var(--taali-warning)]">Concerns</div>
              <ul className="space-y-1">
                {cvMatch.concerns.map((concern, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-[var(--taali-text)]">
                    <AlertTriangle size={14} className="mt-0.5 shrink-0 text-[var(--taali-warning)]" />{concern}
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {cvMatch.summary ? (
            <Panel className="p-4">
              <div className="mb-2 font-bold">Summary</div>
              <p className="text-sm italic text-[var(--taali-text)]">"{cvMatch.summary}"</p>
            </Panel>
          ) : null}
        </>
      ) : (
        <Card className="p-8 text-center">
          <div className="mb-2 text-[var(--taali-muted)]">{hasCv ? 'No role fit analysis available' : 'Role fit: N/A — No CV'}</div>
          <div className="text-xs text-[var(--taali-muted)]">
            {hasCv
              ? 'Fit analysis requires both a CV and a job specification to be uploaded for this candidate. Upload documents on the Candidates page.'
              : 'Upload a CV from the Candidates page to enable CV ↔ Job role fit scoring.'}
          </div>
          {!hasCv && typeof onRequestCvUpload === 'function' ? (
            <div className="mt-4">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={onRequestCvUpload}
                disabled={requestingCvUpload}
              >
                {requestingCvUpload ? 'Sending CV request...' : 'Request CV upload from candidate'}
              </Button>
            </div>
          ) : null}
        </Card>
      )}

      <Panel className="p-4">
        <div className="mb-3 font-bold">Documents</div>
        <div className="space-y-3 font-mono text-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span>{assessment.cv_uploaded ? '✅' : '❌'}</span>
              <span>CV: {assessment.candidate_cv_filename || assessment.cv_filename || 'Not uploaded'}</span>
            </div>
            {(assessment.candidate_cv_filename || assessment.cv_filename) ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onDownloadCandidateDoc('cv')}
              >
                Download
              </Button>
            ) : null}
          </div>
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span>{assessment.candidate_job_spec_filename ? '✅' : '❌'}</span>
              <span>Job Specification: {assessment.candidate_job_spec_filename || 'Not uploaded'}</span>
            </div>
            {assessment.candidate_job_spec_filename ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onDownloadCandidateDoc('job-spec')}
              >
                Download
              </Button>
            ) : null}
          </div>
        </div>
      </Panel>
    </div>
  );
};

export const CandidateCodeGitTab = ({ candidate }) => {
  const assessment = candidate._raw || {};
  const gitEvidence = assessment.git_evidence || {};
  const headSha = gitEvidence.head_sha;
  const commits = gitEvidence.commits;
  const diffMain = gitEvidence.diff_main;
  const diffStaged = gitEvidence.diff_staged;
  const statusPorcelain = gitEvidence.status_porcelain;
  const error = gitEvidence.error;
  const hasAny = headSha || commits || diffMain || diffStaged || statusPorcelain || error;

  if (!hasAny) {
    return (
      <Card className="bg-[var(--taali-bg)] p-6">
        <div className="text-sm text-[var(--taali-muted)]">No git evidence captured for this assessment. This can happen if the task did not use a repository or evidence capture failed.</div>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {assessment.completed_due_to_timeout ? (
        <Panel className="border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] p-3 text-sm text-[var(--taali-text)]">Assessment was auto-submitted when time expired.</Panel>
      ) : null}

      {headSha ? (
        <Panel className="p-4">
          <div className="mb-1 font-mono text-xs font-bold text-[var(--taali-muted)]">Final HEAD (SHA)</div>
          <pre className="overflow-x-auto bg-[#151122] p-2 font-mono text-xs text-gray-200">{headSha}</pre>
        </Panel>
      ) : null}

      {commits ? (
        <Panel className="p-4">
          <div className="mb-1 font-mono text-xs font-bold text-[var(--taali-muted)]">Commits (assessment branch)</div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap bg-[#151122] p-2 font-mono text-xs text-gray-200">{commits}</pre>
        </Panel>
      ) : null}

      {diffMain ? (
        <Panel className="p-4">
          <div className="mb-1 font-mono text-xs font-bold text-[var(--taali-muted)]">Diff (main...HEAD)</div>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap bg-[#151122] p-2 font-mono text-xs text-green-300">{diffMain}</pre>
        </Panel>
      ) : null}

      {diffStaged ? (
        <Panel className="p-4">
          <div className="mb-1 font-mono text-xs font-bold text-[var(--taali-muted)]">Staged diff</div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap bg-[#151122] p-2 font-mono text-xs text-gray-200">{diffStaged}</pre>
        </Panel>
      ) : null}

      {statusPorcelain ? (
        <Panel className="p-4">
          <div className="mb-1 font-mono text-xs font-bold text-[var(--taali-muted)]">Status (porcelain)</div>
          <pre className="overflow-x-auto bg-[#151122] p-2 font-mono text-xs text-gray-200">{statusPorcelain}</pre>
        </Panel>
      ) : null}

      {error ? (
        <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-3 text-sm text-[var(--taali-danger)]">{error}</Panel>
      ) : null}
    </div>
  );
};

const prettifyEventName = (value) => String(value || '')
  .replace(/_/g, ' ')
  .replace(/\b\w/g, (chunk) => chunk.toUpperCase())
  .trim();

const timelineIconForType = (type) => {
  if (type === 'started') return PlayCircle;
  if (type === 'first_prompt' || type === 'ai_prompt') return MessageSquare;
  if (type === 'code_run' || type === 'code_change') return TerminalSquare;
  if (type === 'test_run') return CheckCircle2;
  if (type === 'submitted') return CheckCircle2;
  if (type === 'note') return StickyNote;
  return PlayCircle;
};

const normalizeTimelineEvents = (timeline) => {
  const items = Array.isArray(timeline) ? timeline : [];
  let sawPrompt = false;
  const normalized = items.map((raw, index) => {
    const eventTypeRaw = String(raw?.event_type || raw?.type || raw?.event || '').toLowerCase();
    const timestamp = raw?.timestamp || raw?.ts || raw?.time || null;
    const promptPreview = String(raw?.preview || raw?.prompt || raw?.message || raw?.text || '').slice(0, 100);
    const testsPassed = raw?.tests_passed;
    const testsTotal = raw?.tests_total;
    const linesAdded = Number(raw?.lines_added ?? raw?.code_diff_lines_added ?? 0);
    const linesRemoved = Number(raw?.lines_removed ?? raw?.code_diff_lines_removed ?? 0);
    const changedFile = raw?.file_path || raw?.file || null;

    let type = 'event';
    let label = raw?.event || prettifyEventName(eventTypeRaw) || 'Event';
    if (eventTypeRaw.includes('start')) {
      type = 'started';
      label = 'Assessment started';
    } else if (eventTypeRaw === 'ai_prompt') {
      if (!sawPrompt) {
        type = 'first_prompt';
        label = 'First prompt';
      } else {
        type = 'ai_prompt';
        label = 'AI prompt';
      }
      sawPrompt = true;
    } else if (eventTypeRaw.includes('code_execute')) {
      type = 'code_run';
      label = 'Code run';
    } else if (eventTypeRaw.includes('submit') || eventTypeRaw.includes('timeout')) {
      type = 'submitted';
      label = eventTypeRaw.includes('timeout') ? 'Submitted (timeout)' : 'Submitted';
    } else if (eventTypeRaw.includes('terminal_usage')) {
      type = 'usage';
      label = 'AI usage';
    } else if (eventTypeRaw.includes('terminal_input')) {
      type = 'ai_prompt';
      label = 'Terminal input';
    } else if (eventTypeRaw.includes('terminal_output')) {
      type = 'event';
      label = 'Terminal output';
    } else if (eventTypeRaw.includes('terminal_exit')) {
      type = 'event';
      label = 'Terminal exited';
    } else if (eventTypeRaw.includes('error')) {
      type = 'event';
      label = 'Error event';
    } else if (eventTypeRaw.includes('code') || linesAdded !== 0 || linesRemoved !== 0) {
      type = 'code_change';
      label = 'Code change';
    } else if (eventTypeRaw.includes('test')) {
      type = 'test_run';
      label = 'Test run';
    } else if (eventTypeRaw.includes('note') || String(raw?.event || '').toLowerCase().includes('note')) {
      type = 'note';
      label = 'Recruiter note';
    }

    const detailBits = [];
    if (promptPreview && (type === 'first_prompt' || type === 'ai_prompt' || type === 'note')) {
      detailBits.push(promptPreview);
    }
    if (type === 'code_run' && Number.isFinite(Number(testsPassed)) && Number.isFinite(Number(testsTotal))) {
      detailBits.push(`Tests: ${testsPassed}/${testsTotal}`);
    }
    if ((type === 'code_change' || type === 'event') && (linesAdded !== 0 || linesRemoved !== 0)) {
      detailBits.push(`Code delta: +${linesAdded} / -${Math.abs(linesRemoved)}`);
    }
    if (changedFile) {
      detailBits.push(`File: ${changedFile}`);
    }
    if (Number.isFinite(Number(raw?.latency_ms))) {
      detailBits.push(`Latency: ${raw.latency_ms}ms`);
    }
    if (raw?.author) {
      detailBits.push(`Author: ${raw.author}`);
    }

    return {
      id: `${index}-${eventTypeRaw || label}`,
      label,
      type,
      timestamp,
      details: detailBits,
      raw,
    };
  });

  const withQuietPeriods = [];
  normalized.forEach((event, index) => {
    const prev = normalized[index - 1];
    if (prev?.timestamp && event?.timestamp) {
      const prevTs = new Date(prev.timestamp).getTime();
      const nextTs = new Date(event.timestamp).getTime();
      if (Number.isFinite(prevTs) && Number.isFinite(nextTs)) {
        const gapSeconds = Math.round((nextTs - prevTs) / 1000);
        if (gapSeconds > 120) {
          const minutes = Math.round(gapSeconds / 60);
          withQuietPeriods.push({
            id: `${event.id}-quiet`,
            label: `${minutes} min quiet period`,
            type: 'quiet',
            timestamp: event.timestamp,
            details: [],
            raw: {},
          });
        }
      }
    }
    withQuietPeriods.push(event);
  });

  return withQuietPeriods;
};

const snapshotLabel = (promptIndex, stage) => {
  if (stage === 'final') return 'Final submission';
  const humanIndex = Number.isFinite(Number(promptIndex)) ? Number(promptIndex) + 1 : null;
  if (humanIndex == null) return stage === 'before' ? 'Code state (before)' : 'Code state (after)';
  return stage === 'before' ? `Prompt #${humanIndex} · before` : `Prompt #${humanIndex} · after`;
};

const buildReplayFrames = (rawSnapshots) => {
  const snapshots = Array.isArray(rawSnapshots) ? rawSnapshots : [];
  const frames = [];
  let frameCounter = 0;
  const pushFrame = ({ stage, promptIndex = null, code }) => {
    if (typeof code !== 'string') return;
    const normalizedCode = code;
    if (frames.length > 0 && frames[frames.length - 1].code === normalizedCode) return;
    frames.push({
      id: `frame-${frameCounter}`,
      order: frameCounter,
      stage,
      promptIndex,
      label: snapshotLabel(promptIndex, stage),
      code: normalizedCode,
    });
    frameCounter += 1;
  };

  snapshots.forEach((entry) => {
    if (!entry || typeof entry !== 'object') return;
    if (typeof entry.final === 'string') return;
    const promptIndex = Number.isFinite(Number(entry.prompt_index)) ? Number(entry.prompt_index) : null;
    if (typeof entry.code_before === 'string') {
      pushFrame({ stage: 'before', promptIndex, code: entry.code_before });
    }
    if (typeof entry.code_after === 'string') {
      pushFrame({ stage: 'after', promptIndex, code: entry.code_after });
    }
  });

  const finalSnapshot = snapshots.find((entry) => entry && typeof entry === 'object' && typeof entry.final === 'string');
  if (finalSnapshot && typeof finalSnapshot.final === 'string') {
    pushFrame({ stage: 'final', promptIndex: null, code: finalSnapshot.final });
  }

  return frames;
};

const buildReplayEventIndexMap = (events, frameCount) => {
  if (!Array.isArray(events) || frameCount <= 0) return {};
  const map = {};
  let promptProgress = 0;
  const promptLikeTypes = new Set(['first_prompt', 'ai_prompt', 'code_change', 'code_run']);

  events.forEach((event, index) => {
    if (promptLikeTypes.has(event.type)) {
      promptProgress += 1;
    }
    if (event.type === 'started') {
      map[event.id] = 0;
      return;
    }
    if (event.type === 'submitted') {
      map[event.id] = frameCount - 1;
      return;
    }
    if (promptProgress > 0) {
      map[event.id] = Math.min(frameCount - 1, Math.max(0, promptProgress * 2 - 1));
      return;
    }
    const ratio = index / Math.max(1, events.length - 1);
    map[event.id] = Math.max(0, Math.min(frameCount - 1, Math.round(ratio * (frameCount - 1))));
  });
  return map;
};

export const CandidateTimelineTab = ({ candidate }) => {
  const events = useMemo(() => normalizeTimelineEvents(candidate?.timeline || []), [candidate?.timeline]);
  const assessment = candidate?._raw || {};
  const replayFrames = useMemo(
    () => buildReplayFrames(assessment.code_snapshots),
    [assessment.code_snapshots]
  );
  const replayEventIndexMap = useMemo(
    () => buildReplayEventIndexMap(events, replayFrames.length),
    [events, replayFrames.length]
  );
  const [activeReplayIndex, setActiveReplayIndex] = useState(0);
  useEffect(() => {
    setActiveReplayIndex(0);
  }, [candidate?.id, replayFrames.length]);
  const selectedReplayFrame = replayFrames[Math.max(0, Math.min(activeReplayIndex, replayFrames.length - 1))] || null;
  const totalPrompts = Number(assessment.total_prompts ?? (candidate?.promptsList || []).length ?? 0);
  const totalTokens = Number((assessment.total_input_tokens || 0) + (assessment.total_output_tokens || 0));
  const avgPromptWords = (() => {
    const prompts = Array.isArray(candidate?.promptsList) ? candidate.promptsList : [];
    if (!prompts.length) return null;
    const total = prompts.reduce((sum, prompt) => {
      const text = String(prompt?.message || prompt?.text || '');
      return sum + text.trim().split(/\s+/).filter(Boolean).length;
    }, 0);
    return Math.round(total / prompts.length);
  })();

  if (events.length === 0) {
    return (
      <Panel className="p-6 text-sm text-[var(--taali-muted)]">
        Assessment activity will appear here once the candidate starts.
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <div className="mb-2 font-bold text-[var(--taali-text)]">AI Usage Summary</div>
        <div className="grid grid-cols-2 gap-3 font-mono text-xs text-[var(--taali-text)] md:grid-cols-3">
          <div>Claude prompts: <span className="font-bold">{totalPrompts || 0}</span></div>
          <div>Total tokens: <span className="font-bold">{totalTokens.toLocaleString()}</span></div>
          <div>Avg prompt size: <span className="font-bold">{avgPromptWords != null ? `${avgPromptWords} words` : '—'}</span></div>
        </div>
      </Panel>

      {replayFrames.length > 0 ? (
        <Panel className="p-4">
          <div className="mb-1 font-bold text-[var(--taali-text)]">Replay mode</div>
          <div className="mb-3 text-xs text-[var(--taali-muted)]">
            Click any timeline replay button to jump to the closest code state.
          </div>
          <div className="mb-3 flex flex-wrap gap-2">
            {replayFrames.map((frame, index) => (
              <Button
                key={frame.id}
                type="button"
                variant={index === activeReplayIndex ? 'primary' : 'secondary'}
                size="sm"
                onClick={() => setActiveReplayIndex(index)}
              >
                {frame.label}
              </Button>
            ))}
          </div>
          <div className="mb-1 font-mono text-xs text-[var(--taali-muted)]">
            {selectedReplayFrame?.label || 'Code state'}
          </div>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap bg-[#151122] p-3 font-mono text-xs text-gray-200">
            {(selectedReplayFrame?.code || '').trim() || '# No code snapshot captured for this step'}
          </pre>
        </Panel>
      ) : null}

      <Panel className="p-4">
        <div className="mb-3 font-bold text-[var(--taali-text)]">Assessment Timeline</div>
        <div className="relative pl-8">
          <div className="absolute bottom-0 left-3 top-0 w-0.5 bg-[var(--taali-purple)]/40" />
          {events.map((event) => {
            const Icon = timelineIconForType(event.type);
            const when = event.timestamp ? new Date(event.timestamp).toLocaleString() : '—';
            if (event.type === 'quiet') {
              return (
                <div key={event.id} className="relative mb-4 pl-8 last:mb-0">
                  <div className="absolute left-0 top-1.5 h-6 w-6 border border-[var(--taali-border)] bg-[var(--taali-warning-soft)] text-[var(--taali-warning)] flex items-center justify-center">
                    <AlertTriangle size={12} />
                  </div>
                  <div className="font-mono text-xs text-[var(--taali-muted)]">{event.label}</div>
                </div>
              );
            }
            return (
              <div key={event.id} className="relative mb-5 pl-8 last:mb-0">
                <div className="absolute left-0 top-1.5 h-6 w-6 border border-[var(--taali-border)] bg-[var(--taali-surface)] text-[var(--taali-purple)] flex items-center justify-center">
                  <Icon size={14} />
                </div>
                <div className="mb-1 font-mono text-xs text-[var(--taali-muted)]">{when}</div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="font-bold text-[var(--taali-text)]">{event.label}</div>
                  {replayFrames.length > 0 ? (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        const replayIndex = replayEventIndexMap[event.id] ?? 0;
                        setActiveReplayIndex(replayIndex);
                      }}
                    >
                      Replay
                    </Button>
                  ) : null}
                </div>
                {event.details.length > 0 ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer font-mono text-xs text-[var(--taali-purple)]">Details</summary>
                    <div className="mt-1 space-y-1 text-sm text-[var(--taali-muted)]">
                      {event.details.map((detail) => (
                        <div key={`${event.id}-${detail}`}>{detail}</div>
                      ))}
                    </div>
                  </details>
                ) : null}
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
};
