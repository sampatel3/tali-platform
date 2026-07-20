import React, { useState } from 'react';

import {
  Badge,
  Button,
  Input,
  Panel,
} from '../../shared/ui/TaaliPrimitives';
import { MotionDisclosure } from '../../shared/motion';

const safeList = (value) => (Array.isArray(value) ? value.filter(Boolean) : []);

const safeText = (value) => {
  if (typeof value === 'string') return value.trim();
  return '';
};

const toDateValue = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const formatDateTime = (value) => {
  const parsed = toDateValue(value);
  if (!parsed) return '—';
  return parsed.toLocaleString();
};

const latestInterviewForStage = (interviews, stage) => (
  safeList(interviews)
    .filter((item) => String(item?.stage || '').trim().toLowerCase() === stage)
    .sort((left, right) => {
      const leftDate = toDateValue(left?.meeting_date || left?.linked_at || left?.created_at)?.getTime() || 0;
      const rightDate = toDateValue(right?.meeting_date || right?.linked_at || right?.created_at)?.getTime() || 0;
      return rightDate - leftDate;
    })[0] || null
);

const toneStyles = {
  success: {
    borderColor: 'var(--taali-success-border)',
    background: 'color-mix(in oklab, var(--taali-success) 8%, var(--taali-surface))',
    labelColor: 'var(--taali-success)',
  },
  warning: {
    borderColor: 'var(--taali-warning-border)',
    background: 'color-mix(in oklab, var(--taali-warning) 8%, var(--taali-surface))',
    labelColor: 'var(--taali-warning)',
  },
  purple: {
    borderColor: 'color-mix(in oklab, var(--taali-purple) 26%, var(--taali-border))',
    background: 'color-mix(in oklab, var(--taali-purple) 8%, var(--taali-surface))',
    labelColor: 'var(--taali-purple-hover)',
  },
  muted: {
    borderColor: 'var(--taali-border-soft)',
    background: 'var(--taali-surface-subtle)',
    labelColor: 'var(--taali-muted)',
  },
};

const SourceCard = ({
  status,
  title,
  detail,
  tone = 'muted',
}) => {
  const resolved = toneStyles[tone] || toneStyles.muted;
  return (
    <div
      className="rounded-[var(--taali-radius-card)] border p-4"
      style={{
        borderColor: resolved.borderColor,
        background: resolved.background,
      }}
    >
      <div
        className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em]"
        style={{ color: resolved.labelColor }}
      >
        {status}
      </div>
      <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">{title}</div>
      <p className="mt-1 text-xs leading-5 text-[var(--taali-muted)]">{detail}</p>
    </div>
  );
};

const InterviewQuestionCard = ({
  item,
  index,
}) => {
  const positiveSignals = safeList(item?.positive_signals);
  const redFlags = safeList(item?.red_flags);
  const questionLabel = safeText(item?.question) || `Question ${index + 1}`;
  const whyThisMatters = safeText(item?.why_this_matters);
  const evidenceAnchor = safeText(item?.evidence_anchor);
  const followUpProbe = safeText(item?.follow_up_probe);

  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
            Question {String(index + 1).padStart(2, '0')}
          </div>
          <div className="mt-2 text-base font-semibold text-[var(--taali-text)]">{questionLabel}</div>
        </div>
        {evidenceAnchor ? (
          <Badge variant="muted" className="max-w-full font-mono text-[0.6875rem]">
            {evidenceAnchor}
          </Badge>
        ) : null}
      </div>

      {whyThisMatters ? (
        <p className="mt-3 text-sm leading-6 text-[var(--taali-muted)]">{whyThisMatters}</p>
      ) : null}

      {(positiveSignals.length > 0 || redFlags.length > 0) ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Listen For
            </div>
            {positiveSignals.length > 0 ? (
              <ul className="mt-2 space-y-2">
                {positiveSignals.map((entry) => (
                  <li key={entry} className="flex gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-[var(--taali-success)]" />
                    <span>{entry}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-[var(--taali-muted)]">No listening cues attached.</p>
            )}
          </div>
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Red Flags
            </div>
            {redFlags.length > 0 ? (
              <ul className="mt-2 space-y-2">
                {redFlags.map((entry) => (
                  <li key={entry} className="flex gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-[var(--taali-danger)]" />
                    <span>{entry}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-[var(--taali-muted)]">No red flags attached.</p>
            )}
          </div>
        </div>
      ) : null}

      {followUpProbe ? (
        <div className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] p-3 text-sm text-[var(--taali-text)]">
          <span className="font-semibold">Follow-up probe:</span> {followUpProbe}
        </div>
      ) : null}
    </Panel>
  );
};

const InterviewPackPanel = ({
  title,
  subtitle,
  pack,
  emptyMessage,
}) => {
  const questions = safeList(pack?.questions);
  const generatedAt = pack?.generated_at ? formatDateTime(pack.generated_at) : null;

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Interview Pack
            </div>
            <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">{title}</div>
            <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">{subtitle}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {pack?.source ? (
              <Badge variant="muted" className="font-mono text-[0.6875rem]">
                {pack.source.replace(/_/g, ' ')}
              </Badge>
            ) : null}
            {generatedAt ? (
              <Badge variant="muted" className="font-mono text-[0.6875rem]">
                Generated {generatedAt}
              </Badge>
            ) : null}
          </div>
        </div>
      </Panel>

      {questions.length > 0 ? (
        questions.map((item, index) => (
          <InterviewQuestionCard
            key={`${safeText(item?.question) || 'question'}-${index}`}
            item={item}
            index={index}
          />
        ))
      ) : (
        <Panel className="p-4 text-sm text-[var(--taali-muted)]">{emptyMessage}</Panel>
      )}
    </div>
  );
};

export const CandidateStageOneScreeningTab = ({
  application = null,
}) => {
  const screeningPack = application?.screening_pack || null;
  const preScreenScore = application?.pre_screen_score;
  const preScreenRecommendation = safeText(application?.pre_screen_recommendation);
  const preScreenEvidence = application?.pre_screen_evidence || {};
  const matchingSkills = safeList(preScreenEvidence?.matching_skills);
  const missingSkills = safeList(preScreenEvidence?.missing_skills);
  const concerns = safeList(preScreenEvidence?.concerns);
  const screeningSummary = safeText(preScreenEvidence?.summary);
  const fraudCopyPaste = preScreenEvidence?.fraud_signals?.cv_copy_paste || null;
  const fraudTriggered = !!fraudCopyPaste?.triggered;

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Stage 1
            </div>
            <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">
              Recruiter screening questions
            </div>
            <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
              Use this pack before or during the screening call so the first conversation closes role-fit gaps instead of repeating CV review.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {preScreenRecommendation ? (
              <Badge variant="purple" className="font-mono text-[0.6875rem]">
                {preScreenRecommendation}
              </Badge>
            ) : null}
            {preScreenScore != null ? (
              <Badge variant="muted" className="font-mono text-[0.6875rem]">
                Pre-screen {Math.round(Number(preScreenScore))}/100
              </Badge>
            ) : null}
          </div>
        </div>
        {screeningSummary ? (
          <p className="mt-4 text-sm leading-6 text-[var(--taali-text)]">{screeningSummary}</p>
        ) : null}
      </Panel>

      {fraudTriggered ? (
        <Panel className="p-4 border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)]">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-danger)]">
                CV plagiarism flag
              </div>
              <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
                {Math.round(Number(fraudCopyPaste.score || 0) * 100)}% of the CV is copied verbatim from the job description.
              </div>
              <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
                The pre-screen agent capped this candidate&rsquo;s score so they were filtered before the full CV match ran. Review the matched chunks below — common boilerplate like &ldquo;strong communication skills&rdquo; would not normally trigger this.
              </p>
            </div>
            <Badge variant="danger" className="font-mono text-[0.6875rem]">
              Threshold {Math.round(Number(fraudCopyPaste.threshold || 0) * 100)}%
            </Badge>
          </div>
          {Array.isArray(fraudCopyPaste.evidence) && fraudCopyPaste.evidence.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {fraudCopyPaste.evidence.slice(0, 5).map((snippet, idx) => (
                <li
                  key={`${snippet.cv_word_offset}-${idx}`}
                  className="rounded border border-[var(--taali-danger-border)] bg-white/60 p-2 text-xs leading-5 text-[var(--taali-text)]"
                >
                  &ldquo;{snippet.text}&rdquo;
                  <span className="ml-2 text-[var(--taali-muted)]">
                    ({snippet.word_count} words)
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <InterviewPackPanel
          title="Screening question bank"
          subtitle="Generated from role-fit evidence, recruiter requirements, and candidate context already on file."
          pack={screeningPack}
          emptyMessage="No screening questions are attached to this candidate yet."
        />

        <div className="space-y-4">
          <Panel className="p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Matching Skills
            </div>
            {matchingSkills.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {matchingSkills.map((item) => (
                  <Badge key={item} variant="success" className="text-[0.6875rem]">
                    {item}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm text-[var(--taali-muted)]">No matching skills are attached yet.</p>
            )}
          </Panel>

          <Panel className="p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Gaps To Validate
            </div>
            {missingSkills.length > 0 ? (
              <ul className="mt-3 space-y-2">
                {missingSkills.map((item) => (
                  <li key={item} className="flex gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-[var(--taali-warning)]" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-[var(--taali-muted)]">No major gaps were flagged in pre-screening.</p>
            )}
          </Panel>

          <Panel className="p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Recruiter Concerns
            </div>
            {concerns.length > 0 ? (
              <ul className="mt-3 space-y-2">
                {concerns.map((item) => (
                  <li key={item} className="flex gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-[var(--taali-danger)]" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-[var(--taali-muted)]">No concerns were flagged for this screening pass.</p>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
};

export const TranscriptPanel = ({
  application = null,
}) => {
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const screeningInterview = latestInterviewForStage(application?.interviews, 'screening');
  const screeningPackQuestions = safeList(application?.screening_pack?.questions);
  const screeningSummary = safeText(
    screeningInterview?.summary
      || application?.screening_interview_summary?.summary
  );
  const transcriptText = safeText(screeningInterview?.transcript_text);
  const transcriptPreview = transcriptText ? transcriptText.slice(0, 900) : '';
  const transcriptUrl = safeText(
    screeningInterview?.provider_url
      || screeningInterview?.provider_payload?.transcript_url
  );
  const speakers = safeList(screeningInterview?.speakers).map((speaker) => safeText(speaker?.name)).filter(Boolean);
  const source = safeText(screeningInterview?.source).toLowerCase();
  const isLegacyManual = source === 'manual';

  if (screeningInterview) {
    return (
      <Panel className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Interview transcript
            </div>
            <div className="mt-2 text-lg font-semibold text-[var(--taali-text)]">
              {safeText(screeningInterview?.provider_payload?.title) || 'Stage 1 call transcript'}
            </div>
            <p className="mt-1 text-sm text-[var(--taali-muted)]">
              {isLegacyManual
                ? 'Attached to this candidate as a historical transcript record.'
                : 'Synced to this candidate by the workspace transcription service.'}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={isLegacyManual ? 'muted' : 'purple'} className="font-mono text-[0.6875rem]">
              {isLegacyManual ? 'Attached' : 'Synced'}
            </Badge>
            <Badge variant="muted" className="font-mono text-[0.6875rem]">
              {formatDateTime(screeningInterview?.meeting_date || screeningInterview?.linked_at)}
            </Badge>
          </div>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
            <div className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Provider</div>
            <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
              {safeText(screeningInterview?.provider) || (isLegacyManual ? 'Manual record' : 'Transcription service')}
            </div>
          </div>
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
            <div className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Speakers</div>
            <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
              {speakers.length > 0 ? speakers.join(', ') : 'Not provided'}
            </div>
          </div>
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
            <div className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Transcript</div>
            <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
              {transcriptUrl ? (
                <a href={transcriptUrl} target="_blank" rel="noreferrer" className="text-[var(--taali-purple-hover)] underline-offset-2 hover:underline">
                  Open transcript
                </a>
              ) : (
                'Attached inline'
              )}
            </div>
          </div>
        </div>

        {screeningSummary ? (
          <div className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Screening Summary
            </div>
            <p className="mt-2 text-sm leading-6 text-[var(--taali-text)]">{screeningSummary}</p>
          </div>
        ) : null}

        <div className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                Screening Coverage Review
              </div>
              <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
                The linked screening questions and transcript evidence are shown here. Per-question coverage (asked vs not asked) is coming soon.
              </p>
            </div>
            <Badge variant="muted" className="font-mono text-[0.6875rem]">
              {screeningPackQuestions.length} questions attached
            </Badge>
          </div>

          {screeningPackQuestions.length > 0 ? (
            <ul className="mt-4 space-y-2">
              {screeningPackQuestions.map((item, index) => (
                <li key={`${safeText(item?.question) || 'screening'}-${index}`} className="flex gap-2 text-sm text-[var(--taali-text)]">
                  <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-[var(--taali-purple)]" />
                  <span>{safeText(item?.question) || `Screening question ${index + 1}`}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {transcriptPreview ? (
          <div className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-expanded={transcriptOpen}
              aria-controls="candidate-transcript-excerpt"
              onClick={() => setTranscriptOpen((open) => !open)}
            >
              {transcriptOpen ? 'Hide transcript excerpt' : 'Show transcript excerpt'}
            </Button>
            <MotionDisclosure open={transcriptOpen} id="candidate-transcript-excerpt">
              <pre className="mt-3 whitespace-pre-wrap font-sans text-sm leading-6 text-[var(--taali-text)]">{transcriptPreview}</pre>
            </MotionDisclosure>
          </div>
        ) : null}
      </Panel>
    );
  }

  return (
    <Panel className="p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
        Interview transcript
      </div>
      <div className="mt-2 text-lg font-semibold text-[var(--taali-text)]">Waiting for the interview transcript.</div>
      <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
        Transcripts sync automatically from the workspace transcription service once a meeting is matched to this candidate. Provider connections are managed in Settings.
      </p>
    </Panel>
  );
};

export const CandidateStageTwoTechnicalTab = ({
  application = null,
  hasCompletedAssessment = false,
  guidanceSlot = null,
}) => {
  const screeningInterview = latestInterviewForStage(application?.interviews, 'screening');
  const latestTechInterview = latestInterviewForStage(application?.interviews, 'tech_stage_2');
  const interviewEvidence = application?.interview_evidence_summary || {};
  const assessmentSignal = interviewEvidence?.assessment_signal || {};
  const missingSkills = safeList(interviewEvidence?.missing_skills);
  const technicalPack = application?.tech_interview_pack || null;

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
          Stage 2
        </div>
        <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">
          Technical / panel interview
        </div>
        <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
          These follow-up questions pull from the role fit review, the coding assessment when available, and any linked screening transcript so the panel can validate evidence instead of starting from scratch.
        </p>
      </Panel>

      <div className="grid gap-3 md:grid-cols-3">
        <SourceCard
          status="Active"
          title="CV + job spec"
          detail="Role requirements, candidate history, and recruiter scoring guidance are already attached."
          tone="success"
        />
        <SourceCard
          status={hasCompletedAssessment ? 'Active' : 'Pending'}
          title="Taali assessment"
          detail={hasCompletedAssessment
            ? `${safeText(assessmentSignal?.task_name) || 'Assessment evidence'} is available for follow-up.`
            : 'Complete the assessment to feed runtime evidence into the technical panel.'}
          tone={hasCompletedAssessment ? 'success' : 'muted'}
        />
        <SourceCard
          status={screeningInterview ? (screeningInterview.source === 'manual' ? 'Attached' : 'Synced') : 'Waiting'}
          title="Screening transcript"
          detail={screeningInterview
            ? 'The latest screening conversation is linked and available for technical follow-up.'
            : 'The workspace transcription service will add Stage 1 evidence here once a meeting is matched.'}
          tone={screeningInterview ? 'warning' : 'muted'}
        />
      </div>

      <TranscriptPanel application={application} />

      <InterviewPackPanel
        title="Technical probing questions"
        subtitle="Deeper follow-up prompts drawn from the actual role evidence already stored on this application."
        pack={technicalPack}
        emptyMessage="No Stage 2 technical questions are attached yet."
      />

      {(missingSkills.length > 0 || latestTechInterview) ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          {latestTechInterview ? (
            <Panel className="p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
                Latest Technical Interview
              </div>
              <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
                {formatDateTime(latestTechInterview?.meeting_date || latestTechInterview?.linked_at)}
              </div>
              {safeText(latestTechInterview?.summary) ? (
                <p className="mt-3 text-sm leading-6 text-[var(--taali-text)]">{safeText(latestTechInterview.summary)}</p>
              ) : (
                <p className="mt-3 text-sm text-[var(--taali-muted)]">No technical interview summary has been attached yet.</p>
              )}
            </Panel>
          ) : (
            <Panel className="p-4 text-sm text-[var(--taali-muted)]">
              No technical interview transcript is attached yet.
            </Panel>
          )}

          <Panel className="p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Technical Gaps To Probe
            </div>
            {missingSkills.length > 0 ? (
              <ul className="mt-3 space-y-2">
                {missingSkills.map((item) => (
                  <li key={item} className="flex gap-2 text-sm text-[var(--taali-text)]">
                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-[var(--taali-warning)]" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-sm text-[var(--taali-muted)]">No explicit technical gaps were surfaced for this stage.</p>
            )}
          </Panel>
        </div>
      ) : null}

      {guidanceSlot}
    </div>
  );
};

const WorkableCommentCard = ({ comment }) => {
  const body = safeText(comment?.body);
  if (!body) return null;
  const author = safeText(comment?.author);
  const when = comment?.created_at ? formatDateTime(comment.created_at) : '';
  const header = [author, when && when !== '—' ? when : ''].filter(Boolean).join(' · ');
  return (
    <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
      {header ? (
        <div className="font-mono text-xs text-[var(--taali-muted)]">{header}</div>
      ) : null}
      <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-[var(--taali-text)]">{body}</p>
    </div>
  );
};

const WorkableAnswerCard = ({ entry }) => {
  const question = safeText(entry?.question);
  const answer = safeText(entry?.answer);
  if (!question && !answer) return null;
  return (
    <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
      {question ? (
        <div className="text-sm font-semibold text-[var(--taali-text)]">{question}</div>
      ) : null}
      {answer ? (
        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-[var(--taali-muted)]">{answer}</p>
      ) : null}
    </div>
  );
};

const WorkableActivityRow = ({ entry }) => {
  const action = safeText(entry?.action);
  const stage = safeText(entry?.stage);
  const body = safeText(entry?.body);
  const when = entry?.created_at ? formatDateTime(entry.created_at) : '';
  const header = [action ? action.replace(/_/g, ' ') : '', stage, when && when !== '—' ? when : '']
    .filter(Boolean)
    .join(' · ');
  if (!header && !body) return null;
  return (
    <li className="flex gap-2 text-sm text-[var(--taali-text)]">
      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--taali-purple)]" />
      <div className="min-w-0">
        {header ? <div className="font-mono text-xs text-[var(--taali-muted)]">{header}</div> : null}
        {body ? <div className="whitespace-pre-wrap">{body}</div> : null}
      </div>
    </li>
  );
};

export const CandidateTeamNotesTab = ({
  application = null,
  noteText = '',
  onNoteTextChange = () => {},
  onSaveNote = () => {},
  busyAction = '',
  canSaveNote = false,
}) => {
  const applicationNotes = safeText(application?.notes);
  const screeningSummary = safeText(application?.screening_interview_summary?.summary);
  const techSummary = safeText(application?.tech_interview_summary?.summary);
  const workableComments = safeList(application?.workable_comments);
  const workableAnswers = safeList(application?.workable_questionnaire_answers);
  const workableActivity = safeList(application?.workable_activity_log);

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
          Team Notes
        </div>
        <div className="mt-2 text-xl font-semibold text-[var(--taali-text)]">
          Shared recruiter context
        </div>
        <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
          Comments, questionnaire answers, and activity synced from Workable sit alongside Taali notes here. Feedback you add below stays in Taali for the hiring team and Taali agents — it is not posted back to Workable or Bullhorn.
        </p>
      </Panel>

      {workableComments.length > 0 ? (
        <Panel className="p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Workable Comments
            </div>
            <Badge variant="purple" className="font-mono text-[0.6875rem]">Synced from Workable</Badge>
          </div>
          <div className="mt-3 space-y-2">
            {workableComments.map((comment, index) => (
              <WorkableCommentCard key={`workable-comment-${index}`} comment={comment} />
            ))}
          </div>
        </Panel>
      ) : null}

      {workableAnswers.length > 0 ? (
        <Panel className="p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Questionnaire Responses
            </div>
            <Badge variant="purple" className="font-mono text-[0.6875rem]">LinkedIn / Workable apply</Badge>
          </div>
          <div className="mt-3 space-y-2">
            {workableAnswers.map((entry, index) => (
              <WorkableAnswerCard key={`workable-answer-${index}`} entry={entry} />
            ))}
          </div>
        </Panel>
      ) : null}

      {workableActivity.length > 0 ? (
        <Panel className="p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Workable Activity
            </div>
            <Badge variant="muted" className="font-mono text-[0.6875rem]">Synced from Workable</Badge>
          </div>
          <ul className="mt-3 space-y-2">
            {workableActivity.map((entry, index) => (
              <WorkableActivityRow key={`workable-activity-${index}`} entry={entry} />
            ))}
          </ul>
        </Panel>
      ) : null}

      {applicationNotes ? (
        <Panel className="p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
            Application Notes
          </div>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-[var(--taali-text)]">{applicationNotes}</p>
        </Panel>
      ) : null}

      {(screeningSummary || techSummary) ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel className="p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Screening Summary
            </div>
            <p className="mt-3 text-sm leading-6 text-[var(--taali-text)]">
              {screeningSummary || 'No screening summary is attached yet.'}
            </p>
          </Panel>
          <Panel className="p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Technical Summary
            </div>
            <p className="mt-3 text-sm leading-6 text-[var(--taali-text)]">
              {techSummary || 'No technical interview summary is attached yet.'}
            </p>
          </Panel>
        </div>
      ) : null}

      {canSaveNote ? (
        <Panel className="p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
            Add Recruiter Feedback
          </div>
          <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
            Saved notes are appended to the assessment timeline for the wider hiring team.
          </p>
          <div className="mt-4 flex flex-col gap-3 md:flex-row">
            <Input
              type="text"
              className="flex-1"
              placeholder="Add recruiter feedback from the interview"
              value={noteText}
              onChange={(event) => onNoteTextChange(event.target.value)}
            />
            <Button type="button" size="sm" variant="secondary" onClick={onSaveNote} disabled={busyAction !== ''}>
              {busyAction === 'note' ? 'Saving...' : 'Save feedback'}
            </Button>
          </div>
        </Panel>
      ) : (
        <Panel className="p-4 text-sm text-[var(--taali-muted)]">
          Recruiter feedback saves to the assessment timeline once an assessment exists for this candidate.
        </Panel>
      )}
    </div>
  );
};
