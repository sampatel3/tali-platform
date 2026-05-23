import React, { useEffect, useState } from 'react';

import { useToast } from '../../context/ToastContext';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateEvaluateTab } from './CandidateEvaluateTab';

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
