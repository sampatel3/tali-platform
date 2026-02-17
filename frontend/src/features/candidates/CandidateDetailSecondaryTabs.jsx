import React from 'react';
import { AlertTriangle } from 'lucide-react';
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

const scoreColor = (score) => {
  if (score >= 7) return 'var(--taali-success)';
  if (score >= 5) return 'var(--taali-warning)';
  return 'var(--taali-danger)';
};

export const CandidateAiUsageTab = ({ candidate, avgCalibrationScore }) => {
  const assessment = candidate._raw || {};

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
                      <Badge variant="purple" className="font-mono text-[11px]">C:{perPrompt.clarity}</Badge>
                      <Badge variant="muted" className="font-mono text-[11px]">S:{perPrompt.specificity}</Badge>
                      <Badge variant="muted" className="font-mono text-[11px]">E:{perPrompt.efficiency}</Badge>
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
            <Card className="py-8 text-center text-[var(--taali-muted)]">No prompt data available yet</Card>
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

export const CandidateCvFitTab = ({ candidate, onDownloadCandidateDoc }) => {
  const assessment = candidate._raw || {};
  const cvMatch = assessment.cv_job_match_details || assessment.prompt_analytics?.cv_job_match?.details || {};
  const matchScores = assessment.prompt_analytics?.cv_job_match || {};
  const overall = matchScores.overall || assessment.cv_job_match_score;
  const skills = matchScores.skills;
  const experience = matchScores.experience;

  return (
    <div className="space-y-6">
      {overall != null ? (
        <>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <Card className="p-6 text-center">
              <div className="mb-1 font-mono text-xs text-gray-500">Overall Match</div>
              <div className="text-4xl font-bold" style={{ color: scoreColor(overall) }}>{overall}/10</div>
            </Card>
            <Card className="p-6 text-center">
              <div className="mb-1 font-mono text-xs text-gray-500">Skills Match</div>
              <div className="text-4xl font-bold" style={{ color: skills != null ? scoreColor(skills) : 'var(--taali-muted)' }}>{skills != null ? `${skills}/10` : '—'}</div>
            </Card>
            <Card className="p-6 text-center">
              <div className="mb-1 font-mono text-xs text-gray-500">Experience</div>
              <div className="text-4xl font-bold" style={{ color: experience != null ? scoreColor(experience) : 'var(--taali-muted)' }}>{experience != null ? `${experience}/10` : '—'}</div>
            </Card>
          </div>

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
          <div className="mb-2 text-[var(--taali-muted)]">No role fit analysis available</div>
          <div className="text-xs text-[var(--taali-muted)]">
            Fit analysis requires both a CV and a job specification to be uploaded for this candidate.
            Upload documents on the Candidates page.
          </div>
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

export const CandidateTimelineTab = ({ candidate }) => (
  <Panel className="p-4">
    <div className="relative pl-7">
      <div className="absolute bottom-0 left-2 top-0 w-0.5 bg-[var(--taali-purple)]" />
      {candidate.timeline.map((t, i) => (
        <div key={i} className="relative mb-5 pl-7 last:mb-0">
          <div className="absolute -left-0 top-1 h-4 w-4 border-2 border-[var(--taali-border)] bg-[var(--taali-purple)]" />
          <div className="mb-1 font-mono text-xs text-[var(--taali-muted)]">{t.time}</div>
          <div className="font-bold text-[var(--taali-text)]">{t.event}</div>
          {t.prompt ? (
            <div className="mt-1 text-sm italic text-[var(--taali-muted)]">"{t.prompt}"</div>
          ) : null}
        </div>
      ))}
    </div>
  </Panel>
);
