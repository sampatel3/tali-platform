import React, { useCallback, useEffect, useId, useState } from 'react';

import { useToast } from '../../context/ToastContext';
import {
  CandidateAiUsageTab,
  CandidateCodeGitTab,
  CandidateTimelineTab,
} from './CandidateDetailSecondaryTabs';
import { CandidateEvaluateTab } from './CandidateEvaluateTab';
import { DecisionRecorder } from './DecisionRecorder';
import { TabBar } from '../../shared/ui/TaaliPrimitives';

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

// Pull the lifecycle snapshot (status / version / who / when / history) out of a
// stored decision (assessment manual_evaluation OR application manual_decision),
// mapping the API's snake_case to the camelCase the DecisionRecorder reads.
const extractPersistedDecision = (stored) => {
  if (!stored || typeof stored !== 'object') return null;
  return {
    status: stored.status || '',
    version: Number(stored.version || 0),
    updatedBy: (stored.updated_by && typeof stored.updated_by === 'object') ? stored.updated_by : null,
    updatedAt: stored.updated_at || null,
    submittedAt: stored.submitted_at || null,
    history: Array.isArray(stored.history) ? stored.history : [],
  };
};

const toList = (value) => (
  Array.isArray(value)
    ? value.map((item) => String(item).trim()).filter(Boolean)
    : String(value || '').split('\n').map((item) => item.trim()).filter(Boolean)
);

// Stable serialization of the decision fields, used to detect unsaved changes
// (dirty) so Save draft / Submit only fire when something actually changed.
const computeDecisionFormKey = ({ decision, rationale, confidence, nextSteps }) => JSON.stringify({
  decision: decision || '',
  rationale: String(rationale || '').trim(),
  confidence: confidence || '',
  nextSteps: [...(Array.isArray(nextSteps) ? nextSteps : [])].map(String).sort(),
});

// The assessment evaluation saves decision + rubric scores + summary notes in
// one PATCH, so its dirty key spans all of them.
const computeAssessmentFormKey = ({
  decision, rationale, confidence, nextSteps, strengths, improvements, scores,
}) => JSON.stringify({
  base: computeDecisionFormKey({ decision, rationale, confidence, nextSteps }),
  strengths: toList(strengths),
  improvements: toList(improvements),
  scores: Object.fromEntries(
    Object.entries(scores || {})
      .map(([key, value]) => [key, {
        score: String(value?.score || '').toLowerCase(),
        evidence: toList(value?.evidence),
      }])
      .sort(([a], [b]) => String(a).localeCompare(String(b)))
  ),
});

// AI-usage analytics, code/git evidence, and the prompt-by-prompt assessment
// timeline. One panel at a time behind a segmented control — stacking all
// three made the Assessment tab several screens of raw dumps.
const EVIDENCE_PANELS = [
  { id: 'prompts', label: 'Prompts', Component: CandidateAiUsageTab },
  { id: 'code', label: 'Code & git', Component: CandidateCodeGitTab },
  { id: 'timeline', label: 'Timeline & replay', Component: CandidateTimelineTab },
];

export const AssessmentEvidencePanels = ({ candidate = null }) => {
  const [activePanel, setActivePanel] = useState('prompts');
  const idPrefix = useId().replace(/:/g, '');
  if (!candidate) return null;
  const active = EVIDENCE_PANELS.find((panel) => panel.id === activePanel) || EVIDENCE_PANELS[0];
  const ActiveComponent = active.Component;
  const evidenceTabs = EVIDENCE_PANELS.map((panel) => ({
    id: panel.id,
    label: panel.label,
    tabId: `${idPrefix}-evidence-tab-${panel.id}`,
    panelId: `${idPrefix}-evidence-panel-${panel.id}`,
  }));

  return (
    <div className="report-assessment-evidence">
      <div className="mc-kicker">RAW EVIDENCE</div>
      <TabBar
        tabs={evidenceTabs}
        activeTab={activePanel}
        onChange={setActivePanel}
        ariaLabel="Assessment evidence"
        className="report-assessment-evidence-tabs"
        density="compact"
      />
      <div
        role="tabpanel"
        id={`${idPrefix}-evidence-panel-${active.id}`}
        aria-labelledby={`${idPrefix}-evidence-tab-${active.id}`}
      >
        <ActiveComponent candidate={candidate} />
      </div>
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
  // PR3: when hosted in the Assessment pane the decision lives on the header
  // strip, so omit the in-rubric DecisionRecorder (assessment evidence stays).
  hideDecision = false,
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
  const [savingMode, setSavingMode] = useState(null);
  const [persisted, setPersisted] = useState(null);
  const [baselineKey, setBaselineKey] = useState(null);
  const [conflict, setConflict] = useState(false);

  // Hydrate the form + lifecycle snapshot + dirty baseline from a stored
  // evaluation (on mount, and again after each save/reload). Recording the
  // baseline from the same normalized values we set means "dirty" starts false.
  const hydrateFromStored = useCallback((stored) => {
    const draft = buildManualEvaluationDraft(stored, evaluationRubric);
    setManualEvalScores(draft.categoryScores);
    setManualEvalDecision(draft.decision);
    setManualEvalRationale(draft.rationale);
    setManualEvalConfidence(draft.confidence);
    setManualEvalNextSteps(draft.nextSteps);
    setManualEvalStrengths(draft.strengths);
    setManualEvalImprovements(draft.improvements);
    setManualEvalSummary(stored || null);
    setPersisted(extractPersistedDecision(stored));
    setBaselineKey(computeAssessmentFormKey({
      decision: draft.decision,
      rationale: draft.rationale,
      confidence: draft.confidence,
      nextSteps: draft.nextSteps,
      strengths: draft.strengths,
      improvements: draft.improvements,
      scores: draft.categoryScores,
    }));
  }, [evaluationRubric]);

  useEffect(() => {
    hydrateFromStored(storedManualEvaluation);
  }, [hydrateFromStored, storedManualEvaluation]);

  const liveKey = computeAssessmentFormKey({
    decision: manualEvalDecision,
    rationale: manualEvalRationale,
    confidence: manualEvalConfidence,
    nextSteps: manualEvalNextSteps,
    strengths: manualEvalStrengths,
    improvements: manualEvalImprovements,
    scores: manualEvalScores,
  });
  const dirty = baselineKey !== null && liveKey !== baselineKey;

  const handleSave = async (mode) => {
    if (!assessmentId || !assessmentsApi?.updateManualEvaluation) return;
    const status = mode === 'submit' ? 'submitted' : 'draft';
    const payloadScores = {};
    for (const [key, value] of Object.entries(manualEvalScores || {})) {
      const score = String(value?.score || '').trim().toLowerCase();
      const evidenceList = toLineList(value?.evidence);
      if (score && evidenceList.length === 0) {
        showToast(`Evidence is required for "${String(key).replace(/_/g, ' ')}".`, 'info');
        return;
      }
      payloadScores[key] = { score: score || null, evidence: evidenceList };
    }

    setManualEvalSaving(true);
    setSavingMode(mode);
    try {
      const res = await assessmentsApi.updateManualEvaluation(assessmentId, {
        status,
        expected_version: persisted?.version ?? 0,
        decision: manualEvalDecision || null,
        rationale: String(manualEvalRationale || '').trim() || null,
        confidence: manualEvalConfidence || null,
        next_steps: Array.isArray(manualEvalNextSteps) ? manualEvalNextSteps : [],
        category_scores: payloadScores,
        strengths: toLineList(manualEvalStrengths),
        improvements: toLineList(manualEvalImprovements),
      });
      const saved = res.data?.evaluation_result || res.data?.manual_evaluation;
      if (saved && typeof saved === 'object') {
        hydrateFromStored(saved);
      }
      setConflict(false);
      showToast(status === 'submitted' ? 'Evaluation recorded.' : 'Draft saved.', 'success');
    } catch (err) {
      if (err?.response?.status === 409) {
        setConflict(true);
        showToast('This evaluation was updated elsewhere. Reload to see the latest.', 'error');
      } else {
        showToast(err?.response?.data?.detail || 'Failed to save', 'error');
      }
    } finally {
      setManualEvalSaving(false);
      setSavingMode(null);
    }
  };

  const reloadStored = useCallback(async () => {
    if (!assessmentId || !assessmentsApi?.get) return;
    try {
      const res = await assessmentsApi.get(assessmentId);
      const stored = res?.data?.evaluation_result || res?.data?.manual_evaluation || null;
      hydrateFromStored(stored);
      setConflict(false);
      showToast('Reloaded the latest evaluation.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to reload.', 'error');
    }
  }, [assessmentId, assessmentsApi, hydrateFromStored, showToast]);

  if (!candidate) return null;

  return (
    <CandidateEvaluateTab
      candidate={candidate}
      evaluationRubric={evaluationRubric}
      manualEvalScores={manualEvalScores}
      setManualEvalScores={setManualEvalScores}
      manualEvalStrengths={manualEvalStrengths}
      setManualEvalStrengths={setManualEvalStrengths}
      manualEvalImprovements={manualEvalImprovements}
      setManualEvalImprovements={setManualEvalImprovements}
      manualEvalSummary={manualEvalSummary}
      manualEvalDecision={manualEvalDecision}
      setManualEvalDecision={setManualEvalDecision}
      manualEvalRationale={manualEvalRationale}
      setManualEvalRationale={setManualEvalRationale}
      manualEvalConfidence={manualEvalConfidence}
      setManualEvalConfidence={setManualEvalConfidence}
      manualEvalNextSteps={manualEvalNextSteps}
      setManualEvalNextSteps={setManualEvalNextSteps}
      decisionState={{
        persisted,
        dirty,
        saving: manualEvalSaving,
        savingMode,
        conflict,
        onReload: reloadStored,
        onSaveDraft: () => handleSave('draft'),
        onSubmit: () => handleSave('submit'),
      }}
      roleFitCriteria={roleFitCriteria}
      recommendation={recommendation}
      recruiterSummary={recruiterSummary}
      hideDecision={hideDecision}
    />
  );
};

// Standalone "Record your decision" surface for a candidate with NO assessment
// linked (e.g. rejected at CV stage). Persists against the application via
// rolesApi.updateApplicationDecision, sharing the DecisionRecorder card +
// draft/submitted lifecycle with the assessment-backed Evaluate tab.
export const ApplicationDecisionPanel = ({
  application = null,
  rolesApi = null,
  onSaved = null,
}) => {
  const { showToast } = useToast();
  const applicationId = application?.id || null;
  const storedDecision = application?.manual_decision || null;

  const [decision, setDecision] = useState('');
  const [rationale, setRationale] = useState('');
  const [confidence, setConfidence] = useState('');
  const [nextSteps, setNextSteps] = useState([]);
  const [persisted, setPersisted] = useState(null);
  const [baselineKey, setBaselineKey] = useState(null);
  const [saving, setSaving] = useState(false);
  const [savingMode, setSavingMode] = useState(null);
  const [conflict, setConflict] = useState(false);

  const hydrate = useCallback((stored) => {
    setDecision(stored?.decision || '');
    setRationale(stored?.rationale || '');
    setConfidence(stored?.confidence || '');
    setNextSteps(Array.isArray(stored?.next_steps) ? stored.next_steps : []);
    setPersisted(extractPersistedDecision(stored));
    setBaselineKey(computeDecisionFormKey({
      decision: stored?.decision || '',
      rationale: stored?.rationale || '',
      confidence: stored?.confidence || '',
      nextSteps: Array.isArray(stored?.next_steps) ? stored.next_steps : [],
    }));
  }, []);

  useEffect(() => {
    hydrate(storedDecision);
  }, [hydrate, storedDecision]);

  const toggleNextStep = (step) => {
    setNextSteps((previous) => {
      const next = Array.isArray(previous) ? previous : [];
      return next.includes(step) ? next.filter((item) => item !== step) : [...next, step];
    });
  };

  const liveKey = computeDecisionFormKey({ decision, rationale, confidence, nextSteps });
  const dirty = baselineKey !== null && liveKey !== baselineKey;

  const handleSave = async (mode) => {
    if (!applicationId || !rolesApi?.updateApplicationDecision) return;
    const status = mode === 'submit' ? 'submitted' : 'draft';
    setSaving(true);
    setSavingMode(mode);
    try {
      const res = await rolesApi.updateApplicationDecision(applicationId, {
        status,
        expected_version: persisted?.version ?? 0,
        decision: decision || null,
        rationale: String(rationale || '').trim() || null,
        confidence: confidence || null,
        next_steps: Array.isArray(nextSteps) ? nextSteps : [],
      });
      const saved = res?.data?.manual_decision;
      if (saved && typeof saved === 'object') {
        hydrate(saved);
      }
      setConflict(false);
      showToast(status === 'submitted' ? 'Decision recorded.' : 'Draft saved.', 'success');
      if (onSaved) onSaved(saved);
    } catch (err) {
      if (err?.response?.status === 409) {
        setConflict(true);
        showToast('This decision was updated elsewhere. Reload to see the latest.', 'error');
      } else {
        showToast(err?.response?.data?.detail || 'Failed to save decision.', 'error');
      }
    } finally {
      setSaving(false);
      setSavingMode(null);
    }
  };

  const reload = useCallback(async () => {
    if (!applicationId || !rolesApi?.getApplication) return;
    try {
      const res = await rolesApi.getApplication(applicationId);
      hydrate(res?.data?.manual_decision || null);
      setConflict(false);
      showToast('Reloaded the latest decision.', 'success');
    } catch (err) {
      showToast(err?.response?.data?.detail || 'Failed to reload.', 'error');
    }
  }, [applicationId, rolesApi, hydrate, showToast]);

  if (!application) return null;

  return (
    <div className="space-y-4">
      <DecisionRecorder
        kicker="Your decision"
        entityNoun="decision"
        intro="No assessment is linked yet — record your decision against this candidate's application. It stays on the candidate report as the internal source of truth."
        decision={decision}
        onDecisionChange={setDecision}
        rationale={rationale}
        onRationaleChange={setRationale}
        confidence={confidence}
        onConfidenceChange={setConfidence}
        nextSteps={nextSteps}
        onToggleNextStep={toggleNextStep}
        persisted={persisted}
        dirty={dirty}
        saving={saving}
        savingMode={savingMode}
        conflict={conflict}
        onReload={reload}
        onSaveDraft={() => handleSave('draft')}
        onSubmit={() => handleSave('submit')}
        disabled={!applicationId}
      />
    </div>
  );
};
