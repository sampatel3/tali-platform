import React from 'react';

import {
  Badge,
  Button,
  Input,
  Panel,
  Select,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';

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
        className="text-[11px] font-semibold uppercase tracking-[0.08em]"
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
          <Badge variant="muted" className="max-w-full font-mono text-[11px]">
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
              <Badge variant="muted" className="font-mono text-[11px]">
                {pack.source.replace(/_/g, ' ')}
              </Badge>
            ) : null}
            {generatedAt ? (
              <Badge variant="muted" className="font-mono text-[11px]">
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
              <Badge variant="purple" className="font-mono text-[11px]">
                {preScreenRecommendation}
              </Badge>
            ) : null}
            {preScreenScore != null ? (
              <Badge variant="muted" className="font-mono text-[11px]">
                Pre-screen {Math.round(Number(preScreenScore))}/100
              </Badge>
            ) : null}
          </div>
        </div>
        {screeningSummary ? (
          <p className="mt-4 text-sm leading-6 text-[var(--taali-text)]">{screeningSummary}</p>
        ) : null}
      </Panel>

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
                  <Badge key={item} variant="success" className="text-[11px]">
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

const TranscriptPanel = ({
  application = null,
  firefliesConnected = false,
  firefliesLinkSupported = false,
  firefliesLinkModel,
  onFirefliesLinkChange,
  onLinkFireflies,
  linkingFireflies = false,
  manualInterviewSupported = false,
  manualInterviewModel,
  onManualInterviewChange,
  onSaveManualInterview,
  manualInterviewSaving = false,
}) => {
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
  const linkModel = firefliesLinkModel || { meetingId: '', providerUrl: '' };
  const manualModel = manualInterviewModel || {
    stage: 'screening',
    transcriptText: '',
    providerUrl: '',
    meetingDate: '',
    summary: '',
  };

  if (screeningInterview) {
    return (
      <Panel
        className="p-4"
        style={{
          borderColor: 'var(--taali-warning-border)',
          background: 'color-mix(in oklab, var(--taali-warning) 7%, var(--taali-surface))',
        }}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              Screening Transcript
            </div>
            <div className="mt-2 text-lg font-semibold text-[var(--taali-text)]">
              {safeText(screeningInterview?.provider_payload?.title) || 'Stage 1 call transcript'}
            </div>
            <p className="mt-1 text-sm text-[var(--taali-muted)]">
              {screeningInterview?.source === 'fireflies'
                ? 'Linked from Fireflies and attached to this application.'
                : 'Manual transcript attached to the screening workflow.'}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={screeningInterview?.source === 'fireflies' ? 'warning' : 'muted'} className="font-mono text-[11px]">
              {screeningInterview?.source === 'fireflies' ? 'Fireflies linked' : 'Manual transcript'}
            </Badge>
            <Badge variant="muted" className="font-mono text-[11px]">
              {formatDateTime(screeningInterview?.meeting_date || screeningInterview?.linked_at)}
            </Badge>
          </div>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Provider</div>
            <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
              {safeText(screeningInterview?.provider) || 'Transcript'}
            </div>
          </div>
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Speakers</div>
            <div className="mt-2 text-sm font-semibold text-[var(--taali-text)]">
              {speakers.length > 0 ? speakers.join(', ') : 'Not provided'}
            </div>
          </div>
          <div className="rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-3">
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Transcript</div>
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
                This build shows the linked screening question set and transcript evidence. Per-question asked/not-asked transcript coverage is not yet stored in the backend.
              </p>
            </div>
            <Badge variant="muted" className="font-mono text-[11px]">
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
          <details className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
            <summary className="cursor-pointer text-sm font-semibold text-[var(--taali-text)]">
              Transcript excerpt
            </summary>
            <pre className="mt-3 whitespace-pre-wrap font-sans text-sm leading-6 text-[var(--taali-text)]">{transcriptPreview}</pre>
          </details>
        ) : null}
      </Panel>
    );
  }

  return (
    <Panel
      className="p-4"
      style={{
        borderColor: 'var(--taali-warning-border)',
        background: 'color-mix(in oklab, var(--taali-warning) 7%, var(--taali-surface))',
      }}
    >
      <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
        Screening Transcript
      </div>
      <div className="mt-2 text-lg font-semibold text-[var(--taali-text)]">No screening call transcript is attached yet.</div>
      <p className="mt-2 text-sm leading-6 text-[var(--taali-muted)]">
        Attach a Fireflies meeting or paste a manual transcript so Stage 2 follow-up questions can reference the real screening conversation.
      </p>

      {firefliesLinkSupported ? (
        <div className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-[var(--taali-text)]">Link Fireflies meeting</div>
              <p className="mt-1 text-xs text-[var(--taali-muted)]">
                {firefliesConnected
                  ? 'Paste the Fireflies meeting ID if auto-match missed this call.'
                  : 'You can still link a Fireflies meeting ID manually if the workspace credentials are already configured.'}
              </p>
            </div>
            <Badge variant={firefliesConnected ? 'success' : 'muted'} className="font-mono text-[11px]">
              {firefliesConnected ? 'Workspace connected' : 'Manual link only'}
            </Badge>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <Input
              type="text"
              value={linkModel.meetingId || ''}
              onChange={(event) => onFirefliesLinkChange?.({ meetingId: event.target.value })}
              placeholder="Fireflies meeting ID"
            />
            <Input
              type="url"
              value={linkModel.providerUrl || ''}
              onChange={(event) => onFirefliesLinkChange?.({ providerUrl: event.target.value })}
              placeholder="Optional transcript URL"
            />
            <Button
              type="button"
              variant="secondary"
              onClick={onLinkFireflies}
              disabled={linkingFireflies}
            >
              {linkingFireflies ? 'Linking…' : 'Link transcript'}
            </Button>
          </div>
        </div>
      ) : null}

      {manualInterviewSupported ? (
        <div className="mt-4 rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4">
          <div className="text-sm font-semibold text-[var(--taali-text)]">Paste transcript manually</div>
          <p className="mt-1 text-xs text-[var(--taali-muted)]">
            Use this fallback when the screening call transcript lives outside Fireflies or still needs manual review.
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Stage</span>
              <Select
                value={manualModel.stage || 'screening'}
                onChange={(event) => onManualInterviewChange?.({ stage: event.target.value })}
              >
                <option value="screening">Stage 1 · screening</option>
                <option value="tech_stage_2">Stage 2 · technical</option>
              </Select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Meeting date</span>
              <Input
                type="datetime-local"
                value={manualModel.meetingDate || ''}
                onChange={(event) => onManualInterviewChange?.({ meetingDate: event.target.value })}
              />
            </label>
            <label className="block md:col-span-2">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Summary</span>
              <Input
                type="text"
                value={manualModel.summary || ''}
                onChange={(event) => onManualInterviewChange?.({ summary: event.target.value })}
                placeholder="Optional high-level summary"
              />
            </label>
            <label className="block md:col-span-2">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Provider URL</span>
              <Input
                type="url"
                value={manualModel.providerUrl || ''}
                onChange={(event) => onManualInterviewChange?.({ providerUrl: event.target.value })}
                placeholder="Optional link to the recording or transcript"
              />
            </label>
            <label className="block md:col-span-2">
              <span className="mb-1 block font-mono text-xs text-[var(--taali-muted)]">Transcript</span>
              <Textarea
                rows={9}
                value={manualModel.transcriptText || ''}
                onChange={(event) => onManualInterviewChange?.({ transcriptText: event.target.value })}
                placeholder="Paste the interview transcript here"
              />
            </label>
          </div>
          <div className="mt-4 flex justify-end">
            <Button type="button" variant="secondary" onClick={onSaveManualInterview} disabled={manualInterviewSaving}>
              {manualInterviewSaving ? 'Saving…' : 'Save transcript'}
            </Button>
          </div>
        </div>
      ) : null}
    </Panel>
  );
};

export const CandidateStageTwoTechnicalTab = ({
  application = null,
  hasCompletedAssessment = false,
  firefliesConnected = false,
  firefliesLinkSupported = false,
  firefliesLinkModel = null,
  onFirefliesLinkChange = null,
  onLinkFireflies = null,
  linkingFireflies = false,
  manualInterviewSupported = false,
  manualInterviewModel = null,
  onManualInterviewChange = null,
  onSaveManualInterview = null,
  manualInterviewSaving = false,
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
          status={screeningInterview ? (screeningInterview.source === 'fireflies' ? 'Synced' : 'Attached') : 'Waiting'}
          title="Screening transcript"
          detail={screeningInterview
            ? 'The latest screening conversation is linked and available for technical follow-up.'
            : (firefliesConnected
              ? 'Fireflies is connected, but no Stage 1 transcript has been linked yet.'
              : 'Attach a transcript manually or connect Fireflies to feed Stage 1 evidence in here.')}
          tone={screeningInterview ? 'warning' : 'muted'}
        />
      </div>

      <TranscriptPanel
        application={application}
        firefliesConnected={firefliesConnected}
        firefliesLinkSupported={firefliesLinkSupported}
        firefliesLinkModel={firefliesLinkModel}
        onFirefliesLinkChange={onFirefliesLinkChange}
        onLinkFireflies={onLinkFireflies}
        linkingFireflies={linkingFireflies}
        manualInterviewSupported={manualInterviewSupported}
        manualInterviewModel={manualInterviewModel}
        onManualInterviewChange={onManualInterviewChange}
        onSaveManualInterview={onSaveManualInterview}
        manualInterviewSaving={manualInterviewSaving}
      />

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
          Keep the panel aligned on what to validate next, what the screening call already covered, and any open concerns before the next stage.
        </p>
      </Panel>

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
