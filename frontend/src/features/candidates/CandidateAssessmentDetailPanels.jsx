import React, { useEffect, useState } from 'react';

import { useToast } from '../../context/ToastContext';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateEvaluateTab } from './CandidateEvaluateTab';
import { TranscriptPanel } from './CandidateInterviewStageViews';

// Self-contained panels lifted out of the legacy assessment-detail page so
// the canonical Standing Report can host the full assessment depth without
// dragging the old page's ~30 useState hooks inline. Each wrapper owns its
// own state; the Standing Report just passes the mapped `candidate` view.

const toLineList = (value) => String(value || '')
  .split('\n')
  .map((item) => item.trim())
  .filter(Boolean);

const toEvidenceTextareaValue = (value) => (
  Array.isArray(value)
    ? value.filter(Boolean).join('\n')
    : String(value || '').trim()
);

const buildManualEvaluationDraft = (storedEvaluation = null, evaluationRubric = {}) => {
  const categoryScores = {};
  Object.keys(evaluationRubric || {}).forEach((key) => {
    const entry = storedEvaluation?.category_scores?.[key];
    categoryScores[key] = {
      score: entry?.score || '',
      evidence: toEvidenceTextareaValue(entry?.evidence),
    };
  });
  return {
    categoryScores,
    decision: storedEvaluation?.decision || '',
    rationale: storedEvaluation?.rationale || '',
    confidence: storedEvaluation?.confidence || '',
    nextSteps: Array.isArray(storedEvaluation?.next_steps) ? storedEvaluation.next_steps : [],
    strengths: Array.isArray(storedEvaluation?.strengths) ? storedEvaluation.strengths.join('\n') : '',
    improvements: Array.isArray(storedEvaluation?.improvements) ? storedEvaluation.improvements.join('\n') : '',
  };
};

// AI-usage analytics, code/git evidence, and the prompt-by-prompt assessment
// timeline. These leaf components only need the mapped `candidate` view.
export const AssessmentEvidencePanels = ({ candidate = null, avgCalibrationScore = null }) => {
  if (!candidate) return null;
  return (
    <div className="report-assessment-evidence space-y-4">
      <section>
        <div className="mc-kicker">AI USAGE &amp; PROMPT QUALITY</div>
        <CandidateAiUsageTab candidate={candidate} avgCalibrationScore={avgCalibrationScore} />
      </section>
      <section>
        <div className="mc-kicker">CODE &amp; GIT EVIDENCE</div>
        <CandidateCodeGitTab candidate={candidate} />
      </section>
      <section>
        <div className="mc-kicker">ASSESSMENT TIMELINE</div>
        <CandidateTimelineTab candidate={candidate} />
      </section>
    </div>
  );
};

// Stage-1/Stage-2 interview transcript capture (Fireflies link + manual
// paste), migrated from the legacy /assessments page. Owns its own form
// state + handlers; `onRefresh` reloads the parent report after a save so
// the linked transcript shows immediately.
export const InterviewTranscriptCapture = ({
  application = null,
  firefliesConnected = false,
  rolesApi = null,
  onRefresh = () => {},
}) => {
  const { showToast } = useToast();
  const applicationId = application?.id || null;
  const [firefliesLinkModel, setFirefliesLinkModel] = useState({ meetingId: '', providerUrl: '' });
  const [linkingFireflies, setLinkingFireflies] = useState(false);
  const [manualInterviewModel, setManualInterviewModel] = useState({
    stage: 'screening',
    transcriptText: '',
    providerUrl: '',
    meetingDate: '',
    summary: '',
  });
  const [manualInterviewSaving, setManualInterviewSaving] = useState(false);

  const handleLinkFireflies = async () => {
    if (!applicationId || !rolesApi?.linkFirefliesInterview) return;
    const meetingId = String(firefliesLinkModel.meetingId || '').trim();
    if (!meetingId) {
      showToast('Enter a Fireflies meeting ID to link the transcript.', 'error');
      return;
    }
    setLinkingFireflies(true);
    try {
      await rolesApi.linkFirefliesInterview(applicationId, {
        stage: 'screening',
        fireflies_meeting_id: meetingId,
        provider_url: String(firefliesLinkModel.providerUrl || '').trim() || undefined,
      });
      await onRefresh();
      setFirefliesLinkModel({ meetingId: '', providerUrl: '' });
      showToast('Fireflies transcript linked.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to link Fireflies transcript.', 'error');
    } finally {
      setLinkingFireflies(false);
    }
  };

  const handleSaveManualInterview = async () => {
    if (!applicationId || !rolesApi?.createManualInterview) return;
    const transcriptText = String(manualInterviewModel.transcriptText || '').trim();
    if (!transcriptText) {
      showToast('Paste the transcript text before saving.', 'error');
      return;
    }
    let meetingDate;
    const rawMeetingDate = String(manualInterviewModel.meetingDate || '').trim();
    if (rawMeetingDate) {
      const parsed = new Date(rawMeetingDate);
      if (!Number.isNaN(parsed.getTime())) meetingDate = parsed.toISOString();
    }
    setManualInterviewSaving(true);
    try {
      await rolesApi.createManualInterview(applicationId, {
        stage: manualInterviewModel.stage || 'screening',
        transcript_text: transcriptText,
        provider_url: String(manualInterviewModel.providerUrl || '').trim() || undefined,
        meeting_date: meetingDate,
        summary: String(manualInterviewModel.summary || '').trim() || undefined,
      });
      await onRefresh();
      setManualInterviewModel({
        stage: 'screening',
        transcriptText: '',
        providerUrl: '',
        meetingDate: '',
        summary: '',
      });
      showToast('Interview transcript saved.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to save interview transcript.', 'error');
    } finally {
      setManualInterviewSaving(false);
    }
  };

  return (
    <TranscriptPanel
      application={application}
      firefliesConnected={firefliesConnected}
      firefliesLinkSupported={Boolean(applicationId && rolesApi?.linkFirefliesInterview)}
      firefliesLinkModel={firefliesLinkModel}
      onFirefliesLinkChange={(patch) => setFirefliesLinkModel((prev) => ({ ...prev, ...patch }))}
      onLinkFireflies={handleLinkFireflies}
      linkingFireflies={linkingFireflies}
      manualInterviewSupported={Boolean(applicationId && rolesApi?.createManualInterview)}
      manualInterviewModel={manualInterviewModel}
      onManualInterviewChange={(patch) => setManualInterviewModel((prev) => ({ ...prev, ...patch }))}
      onSaveManualInterview={handleSaveManualInterview}
      manualInterviewSaving={manualInterviewSaving}
    />
  );
};

// Manual evaluation rubric. Owns the full manual-eval state surface the
// CandidateEvaluateTab expects (scores, decision, rationale, strengths,
// improvements, next steps) plus the AI-suggestion fetch.
export const EvaluatePanel = ({
  candidate = null,
  evaluationRubric = {},
  assessmentId = null,
  assessmentsApi = null,
  roleFitCriteria = [],
  recommendation = null,
  recruiterSummary = '',
}) => {
  const { showToast } = useToast();
  const completedAssessment = candidate?._raw || null;
  const storedManualEvaluation = completedAssessment?.evaluation_result
    || completedAssessment?.manual_evaluation
    || null;

  const [manualEvalScores, setManualEvalScores] = useState({});
  const [manualEvalStrengths, setManualEvalStrengths] = useState('');
  const [manualEvalImprovements, setManualEvalImprovements] = useState('');
  const [manualEvalSummary, setManualEvalSummary] = useState(null);
  const [manualEvalDecision, setManualEvalDecision] = useState('');
  const [manualEvalRationale, setManualEvalRationale] = useState('');
  const [manualEvalConfidence, setManualEvalConfidence] = useState('');
  const [manualEvalNextSteps, setManualEvalNextSteps] = useState([]);
  const [manualEvalSaving, setManualEvalSaving] = useState(false);
  const [aiEvalSuggestion, setAiEvalSuggestion] = useState(null);
  const [aiEvalLoading, setAiEvalLoading] = useState(false);

  useEffect(() => {
    const nextDraft = buildManualEvaluationDraft(storedManualEvaluation, evaluationRubric);
    setManualEvalScores(nextDraft.categoryScores);
    setManualEvalDecision(nextDraft.decision);
    setManualEvalRationale(nextDraft.rationale);
    setManualEvalConfidence(nextDraft.confidence);
    setManualEvalNextSteps(nextDraft.nextSteps);
    setManualEvalStrengths(nextDraft.strengths);
    setManualEvalImprovements(nextDraft.improvements);
    setManualEvalSummary(storedManualEvaluation);
  }, [evaluationRubric, storedManualEvaluation]);

  const handleGenerateAiSuggestions = async () => {
    if (!assessmentId || !assessmentsApi?.aiEvalSuggestions) return;
    setAiEvalLoading(true);
    try {
      const res = await assessmentsApi.aiEvalSuggestions(assessmentId);
      setAiEvalSuggestion(res?.data || null);
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to generate AI evaluation suggestion.', 'error');
    } finally {
      setAiEvalLoading(false);
    }
  };

  if (!candidate) return null;

  return (
    <CandidateEvaluateTab
      candidate={candidate}
      evaluationRubric={evaluationRubric}
      assessmentId={assessmentId}
      aiEvalSuggestion={aiEvalSuggestion}
      onGenerateAiSuggestions={handleGenerateAiSuggestions}
      aiEvalLoading={aiEvalLoading}
      manualEvalScores={manualEvalScores}
      setManualEvalScores={setManualEvalScores}
      manualEvalStrengths={manualEvalStrengths}
      setManualEvalStrengths={setManualEvalStrengths}
      manualEvalImprovements={manualEvalImprovements}
      setManualEvalImprovements={setManualEvalImprovements}
      manualEvalSummary={manualEvalSummary}
      setManualEvalSummary={setManualEvalSummary}
      manualEvalDecision={manualEvalDecision}
      setManualEvalDecision={setManualEvalDecision}
      manualEvalRationale={manualEvalRationale}
      setManualEvalRationale={setManualEvalRationale}
      manualEvalConfidence={manualEvalConfidence}
      setManualEvalConfidence={setManualEvalConfidence}
      manualEvalNextSteps={manualEvalNextSteps}
      setManualEvalNextSteps={setManualEvalNextSteps}
      manualEvalSaving={manualEvalSaving}
      setManualEvalSaving={setManualEvalSaving}
      toLineList={toLineList}
      toEvidenceTextareaValue={toEvidenceTextareaValue}
      assessmentsApi={assessmentsApi}
      roleFitCriteria={roleFitCriteria}
      recommendation={recommendation}
      recruiterSummary={recruiterSummary}
    />
  );
};
