import { normalizeScore } from '../../lib/scoreDisplay';
import { dimensionOrder, getDimensionById, normalizeScores } from '../../scoring/scoringDimensions';

const NON_ROLE_FIT_DIMENSIONS = dimensionOrder.filter((key) => key !== 'role_fit');

const toFiniteNumber = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const roundTo = (value, digits = 1) => {
  if (!Number.isFinite(Number(value))) return null;
  const factor = 10 ** digits;
  return Math.round(Number(value) * factor) / factor;
};

const sanitizeSummaryText = (value) => String(value || '').replace(/\s+/g, ' ').trim();

const getPossessiveName = (fullName) => {
  const trimmed = String(fullName || '').trim();
  if (!trimmed) return 'Your';
  return /s$/i.test(trimmed) ? `${trimmed}'` : `${trimmed}'s`;
};

const extractRawCategoryScores = (submissionResult) => (
  submissionResult?.score_breakdown?.category_scores
  || submissionResult?.prompt_analytics?.category_scores
  || submissionResult?.prompt_analytics?.ai_scores
  || submissionResult?.prompt_analytics?.detailed_scores?.category_scores
  || {}
);

const deriveCanonicalCategoryScores = (submissionResult) => {
  const normalized = normalizeScores(extractRawCategoryScores(submissionResult));
  const roleFitScore = normalizeScore(
    submissionResult?.role_fit_score
    ?? submissionResult?.score_breakdown?.score_components?.role_fit_score
    ?? submissionResult?.score_breakdown?.cv_job_match?.role_fit,
    '0-100',
  );
  if (roleFitScore != null && normalized.role_fit == null) {
    return {
      ...normalized,
      role_fit: roundTo(roleFitScore / 10, 2),
    };
  }
  return normalized;
};

const deriveCategoryExtremes = (categoryScores = {}) => {
  const scored = NON_ROLE_FIT_DIMENSIONS
    .map((key) => ({ key, value: Number(categoryScores[key]) }))
    .filter((item) => Number.isFinite(item.value));

  if (!scored.length) {
    return {
      strongestDimension: null,
      weakestDimension: null,
      strongestLabel: '—',
      weakestLabel: '—',
    };
  }

  const strongest = [...scored].sort((a, b) => b.value - a.value)[0];
  const weakest = [...scored].sort((a, b) => a.value - b.value)[0];

  return {
    strongestDimension: strongest?.key || null,
    weakestDimension: weakest?.key || null,
    strongestLabel: strongest?.key ? getDimensionById(strongest.key).label : '—',
    weakestLabel: weakest?.key ? getDimensionById(weakest.key).label : '—',
  };
};

const buildDemoFeedback = ({
  heuristicSummary,
  strongestLabel,
  weakestLabel,
  testsPassed,
  testsTotal,
  promptCount,
  runCount,
  saveCount,
}) => {
  const cleanHeuristicSummary = sanitizeSummaryText(heuristicSummary);
  const fallbackSummaryParts = [];

  if (strongestLabel && strongestLabel !== '—') {
    fallbackSummaryParts.push(`Strongest dimension: ${strongestLabel}`);
  }
  if (weakestLabel && weakestLabel !== '—') {
    fallbackSummaryParts.push(`Primary probe area: ${weakestLabel}`);
  }
  if (Number.isFinite(testsTotal) && testsTotal > 0) {
    fallbackSummaryParts.push(`Passed ${testsPassed ?? 0} of ${testsTotal} tests`);
  }

  const bullets = [];
  if (strongestLabel && strongestLabel !== '—') {
    bullets.push(`Strongest signal observed: ${strongestLabel}.`);
  }
  if (weakestLabel && weakestLabel !== '—') {
    bullets.push(`Recommended area to validate further: ${weakestLabel}.`);
  }
  if (Number.isFinite(testsTotal) && testsTotal > 0) {
    bullets.push(`Validation evidence: ${testsPassed ?? 0}/${testsTotal} tests passed in the task harness.`);
  } else {
    bullets.push(`Runtime evidence captured: ${promptCount} AI prompts, ${runCount} code runs, ${saveCount} saves.`);
  }

  return {
    title: 'Assessment feedback',
    summary: cleanHeuristicSummary || fallbackSummaryParts.join('. ') || 'Assessment evidence has been captured for this demo task.',
    bullets: bullets.slice(0, 3),
    note: 'Demo output mirrors production assessment signal and omits recruiter-only sections such as role fit and interview guidance.',
  };
};

const buildDemoReportModel = ({
  canonicalCategoryScores,
  profile,
  assessmentName,
  submissionResult,
  promptCount,
  runCount,
  saveCount,
}) => {
  const strongestAndWeakest = deriveCategoryExtremes(canonicalCategoryScores);
  const assessmentScore = normalizeScore(
    submissionResult?.assessment_score
    ?? submissionResult?.final_score
    ?? submissionResult?.score_breakdown?.score_components?.assessment_score,
    '0-100',
  );

  const categoryScores = NON_ROLE_FIT_DIMENSIONS.reduce((acc, key) => {
    const numericValue = toFiniteNumber(canonicalCategoryScores[key]);
    if (Number.isFinite(numericValue)) {
      acc[key] = roundTo(numericValue, 2);
    }
    return acc;
  }, {});

  const dimensionEntries = NON_ROLE_FIT_DIMENSIONS
    .map((key) => {
      const numericValue = toFiniteNumber(categoryScores[key]);
      if (!Number.isFinite(numericValue)) return null;
      return {
        key,
        label: getDimensionById(key).label,
        value: roundTo(numericValue, 2),
      };
    })
    .filter(Boolean);

  const feedback = buildDemoFeedback({
    heuristicSummary:
      submissionResult?.score_breakdown?.heuristic_summary
      || submissionResult?.prompt_analytics?.heuristic_summary
      || '',
    strongestLabel: strongestAndWeakest.strongestLabel,
    weakestLabel: strongestAndWeakest.weakestLabel,
    testsPassed: toFiniteNumber(submissionResult?.tests_passed ?? submissionResult?.tests_pass_count),
    testsTotal: toFiniteNumber(submissionResult?.tests_total ?? submissionResult?.tests_run_count),
    promptCount,
    runCount,
    saveCount,
  });

  const displayName = `${getPossessiveName(profile?.fullName)} TAALI profile`;

  return {
    identity: {
      sectionLabel: 'TAALI profile',
      name: displayName,
      email: profile?.email || profile?.workEmail || null,
      taskName: assessmentName || 'Demo task',
      assessmentId: Math.max(1, Math.round(toFiniteNumber(submissionResult?.id) || 1)),
    },
    source: {
      kind: 'assessment',
      label: 'Completed assessment',
      badgeVariant: 'purple',
      updatedAt: submissionResult?.completed_at || null,
    },
    summaryModel: {
      source: {
        kind: 'assessment',
        label: 'Completed assessment',
        badgeVariant: 'purple',
        updatedAt: submissionResult?.completed_at || null,
      },
      taaliScore: normalizeScore(
        submissionResult?.taali_score
        ?? submissionResult?.score_breakdown?.score_components?.taali_score,
        '0-100',
      ),
      assessmentScore,
      roleFitScore: null,
      strongestDimension: strongestAndWeakest.strongestDimension,
      weakestDimension: strongestAndWeakest.weakestDimension,
      strongestLabel: strongestAndWeakest.strongestLabel,
      weakestLabel: strongestAndWeakest.weakestLabel,
      heuristicSummary: feedback.summary,
      categoryScores,
      assessmentStatus: submissionResult?.status || 'completed',
      completedAt: submissionResult?.completed_at || null,
      updatedAt: submissionResult?.completed_at || null,
    },
    dimensionEntries,
    feedback,
    hasCompletedAssessment: true,
    hasDimensionSignal: dimensionEntries.length > 0,
    radarCategoryKeys: dimensionEntries.map((entry) => entry.key),
  };
};

export const buildDemoSummary = ({
  runCount = 0,
  promptMessages = [],
  saveCount = 0,
  timeSpentSeconds = 0,
  tabSwitchCount = 0,
  submissionResult = null,
  profile = null,
  assessmentName = null,
}) => {
  const promptCount = promptMessages.length;
  const canonicalCategoryScores = deriveCanonicalCategoryScores(submissionResult);

  return {
    meta: {
      promptCount,
      runCount,
      saveCount,
      timeSpentSeconds,
      tabSwitchCount,
    },
    reportModel: buildDemoReportModel({
      canonicalCategoryScores,
      profile,
      assessmentName,
      submissionResult,
      promptCount,
      runCount,
      saveCount,
    }),
    submission: {
      id: toFiniteNumber(submissionResult?.id),
      status: submissionResult?.status || null,
      completedAt: submissionResult?.completed_at || null,
    },
  };
};
