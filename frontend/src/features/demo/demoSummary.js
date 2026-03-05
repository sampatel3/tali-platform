import { normalizeScore } from '../../lib/scoreDisplay';
import { dimensionOrder, getDimensionById, normalizeScores } from '../../scoring/scoringDimensions';

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const containsAny = (text, patterns) => patterns.some((pattern) => pattern.test(text));

const toFiniteNumber = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const roundTo = (value, digits = 1) => {
  if (!Number.isFinite(Number(value))) return null;
  const factor = 10 ** digits;
  return Math.round(Number(value) * factor) / factor;
};

const NON_ROLE_FIT_DIMENSIONS = dimensionOrder.filter((key) => key !== 'role_fit');

const recommendationForScore = (score100) => {
  const numeric = toFiniteNumber(score100);
  if (!Number.isFinite(numeric)) return { label: 'Pending', variant: 'muted' };
  if (numeric >= 80) return { label: 'Strong Hire', variant: 'success' };
  if (numeric >= 65) return { label: 'Hire', variant: 'info' };
  if (numeric >= 50) return { label: 'Consider', variant: 'warning' };
  return { label: 'No Hire', variant: 'danger' };
};

const categoryPlaybook = {
  problem_framing: {
    label: 'Problem Framing',
  },
  execution_rigor: {
    label: 'Execution Rigor',
  },
  testing_validation: {
    label: 'Testing & Validation',
  },
  ai_collaboration: {
    label: 'AI Collaboration',
  },
  technical_communication: {
    label: 'Technical Communication',
  },
  delivery_momentum: {
    label: 'Delivery Momentum',
  },
};

const successfulCandidateBenchmarks = {
  data_eng_aws_glue_pipeline_recovery: {
    problem_framing: 3.8,
    execution_rigor: 3.6,
    testing_validation: 3.7,
    ai_collaboration: 3.5,
    technical_communication: 3.6,
    delivery_momentum: 3.5,
  },
  ai_eng_genai_production_readiness: {
    problem_framing: 3.8,
    execution_rigor: 3.7,
    testing_validation: 3.6,
    ai_collaboration: 3.8,
    technical_communication: 3.7,
    delivery_momentum: 3.6,
  },
  default: {
    problem_framing: 3.6,
    execution_rigor: 3.6,
    testing_validation: 3.5,
    ai_collaboration: 3.5,
    technical_communication: 3.4,
    delivery_momentum: 3.6,
  },
};

const levelToScore = (level) => Math.round((clamp(Number(level) || 0, 0, 5) / 5) * 100);

const getPossessiveName = (fullName) => {
  const trimmed = String(fullName || '').trim();
  if (!trimmed) return 'Your';
  return /s$/i.test(trimmed) ? `${trimmed}'` : `${trimmed}'s`;
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

const buildFallbackCanonicalScores = ({ categories, submissionResult }) => {
  const levelByKey = categories.reduce((acc, entry) => {
    acc[entry.key] = clamp(Number(entry.level) || 0, 0, 5);
    return acc;
  }, {});
  const meanLevel = (...keys) => {
    const values = keys
      .map((key) => levelByKey[key])
      .filter((value) => Number.isFinite(value) && value > 0);
    if (!values.length) return 0;
    return values.reduce((acc, value) => acc + value, 0) / values.length;
  };

  const fallback = {
    task_completion: meanLevel('execution_rigor', 'testing_validation', 'delivery_momentum') * 2,
    prompt_clarity: meanLevel('technical_communication', 'ai_collaboration') * 2,
    context_provision: meanLevel('problem_framing', 'technical_communication') * 2,
    independence_efficiency: meanLevel('delivery_momentum', 'execution_rigor') * 2,
    response_utilization: meanLevel('ai_collaboration', 'execution_rigor') * 2,
    debugging_design: meanLevel('problem_framing', 'testing_validation') * 2,
    written_communication: meanLevel('technical_communication') * 2,
  };

  const roleFitScore = normalizeScore(
    submissionResult?.role_fit_score
    ?? submissionResult?.score_breakdown?.score_components?.role_fit_score
    ?? submissionResult?.score_breakdown?.cv_job_match?.role_fit,
    '0-100',
  );
  if (roleFitScore != null) {
    fallback.role_fit = roleFitScore / 10;
  }

  return normalizeScores(fallback);
};

const deriveCanonicalCategoryScores = ({ categories, submissionResult }) => {
  const rawSubmissionScores =
    submissionResult?.score_breakdown?.category_scores
    || submissionResult?.prompt_analytics?.category_scores
    || submissionResult?.prompt_analytics?.ai_scores
    || submissionResult?.prompt_analytics?.detailed_scores?.category_scores
    || {};

  const normalizedSubmissionScores = normalizeScores(rawSubmissionScores);
  if (Object.keys(normalizedSubmissionScores).length > 0) {
    const roleFitScore = normalizeScore(
      submissionResult?.role_fit_score
      ?? submissionResult?.score_breakdown?.score_components?.role_fit_score
      ?? submissionResult?.score_breakdown?.cv_job_match?.role_fit,
      '0-100',
    );
    if (roleFitScore != null && normalizedSubmissionScores.role_fit == null) {
      return {
        ...normalizedSubmissionScores,
        role_fit: roundTo(roleFitScore / 10, 2),
      };
    }
    return normalizedSubmissionScores;
  }

  return buildFallbackCanonicalScores({ categories, submissionResult });
};

const buildDemoReportModel = ({
  canonicalCategoryScores,
  profile,
  assessmentName,
  submissionResult,
  candidateScore,
  heuristicSummary,
}) => {
  const strongestAndWeakest = deriveCategoryExtremes(canonicalCategoryScores);
  const assessmentScore = normalizeScore(
    submissionResult?.assessment_score
    ?? submissionResult?.final_score
    ?? submissionResult?.score_breakdown?.score_components?.assessment_score
    ?? candidateScore,
    '0-100',
  );
  const taaliScore = normalizeScore(
    submissionResult?.taali_score
    ?? submissionResult?.score_breakdown?.score_components?.taali_score
    ?? assessmentScore
    ?? candidateScore,
    '0-100',
  );
  const roleFitScore = normalizeScore(
    submissionResult?.role_fit_score
    ?? submissionResult?.score_breakdown?.score_components?.role_fit_score,
    '0-100',
  );
  const assessmentId = Math.max(1, Math.round(toFiniteNumber(submissionResult?.id) || 1));
  const dimensionEntries = NON_ROLE_FIT_DIMENSIONS
    .map((key) => {
      const numericValue = toFiniteNumber(canonicalCategoryScores[key]);
      if (!Number.isFinite(numericValue)) return null;
      return {
        key,
        label: getDimensionById(key).label,
        value: roundTo(numericValue, 2),
      };
    })
    .filter(Boolean);
  const recommendation = recommendationForScore(taaliScore);
  const displayName = `${getPossessiveName(profile?.fullName)} TAALI profile`;

  return {
    identity: {
      sectionLabel: 'TAALI profile',
      name: displayName,
      email: profile?.email || profile?.workEmail || null,
      taskName: assessmentName || 'Demo task',
      assessmentId,
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
      taaliScore,
      assessmentScore,
      roleFitScore,
      strongestDimension: strongestAndWeakest.strongestDimension,
      weakestDimension: strongestAndWeakest.weakestDimension,
      strongestLabel: strongestAndWeakest.strongestLabel,
      weakestLabel: strongestAndWeakest.weakestLabel,
      heuristicSummary,
      categoryScores: canonicalCategoryScores,
      assessmentStatus: submissionResult?.status || 'completed',
      completedAt: submissionResult?.completed_at || null,
      updatedAt: submissionResult?.completed_at || null,
    },
    roleFitModel: {},
    recommendation,
    dimensionEntries,
    recruiterSummaryText: heuristicSummary,
    strongestSignalTitle: strongestAndWeakest.strongestLabel,
    strongestSignalDescription: strongestAndWeakest.strongestDimension
      ? `Highest observed signal currently appears in ${strongestAndWeakest.strongestLabel.toLowerCase()}.`
      : 'Dimension signal is still being collected.',
    probeTitle: strongestAndWeakest.weakestLabel,
    probeDescription: strongestAndWeakest.weakestDimension
      ? `Validate evidence around ${strongestAndWeakest.weakestLabel.toLowerCase()}.`
      : 'No priority probe area has been detected.',
    integritySummaryText: 'Demo assessment integrity signal is captured in-session and reflected in score telemetry.',
    evidenceSections: {},
    hasCompletedAssessment: true,
    hasDimensionSignal: dimensionEntries.length > 0,
    radarCategoryKeys: NON_ROLE_FIT_DIMENSIONS,
  };
};

export const profileBandForLevel = (level) => {
  if (level >= 5) return 'Very strong';
  if (level >= 4) return 'Strong';
  if (level >= 3) return 'Developing';
  if (level >= 2) return 'Early';
  return 'Limited';
};

export const buildDemoSummary = ({
  runCount = 0,
  promptMessages = [],
  saveCount = 0,
  finalCode = '',
  timeSpentSeconds = 0,
  tabSwitchCount = 0,
  taskKey = null,
  submissionResult = null,
  profile = null,
  assessmentName = null,
}) => {
  const promptCount = promptMessages.length;
  const promptCorpus = promptMessages.join(' ').toLowerCase();
  const averagePromptLength = promptCount > 0
    ? promptMessages.reduce((acc, msg) => acc + msg.length, 0) / promptCount
    : 0;

  const hasTestingSignal = containsAny(`${promptCorpus} ${finalCode.toLowerCase()}`, [
    /test/i,
    /assert/i,
    /edge case/i,
    /coverage/i,
    /regression/i,
  ]);
  const hasDebugSignal = containsAny(promptCorpus, [/debug/i, /trace/i, /root cause/i, /investigate/i]);
  const hasTradeoffSignal = containsAny(promptCorpus, [/tradeoff/i, /risk/i, /impact/i, /constraint/i]);
  const hasStepSignal = containsAny(promptCorpus, [/step/i, /plan/i, /approach/i, /strategy/i]);
  const hasVerificationSignal = runCount >= 2 || hasTestingSignal;

  const categories = [
    {
      key: 'problem_framing',
      level: clamp(1 + (hasStepSignal ? 1 : 0) + (hasDebugSignal ? 1 : 0) + (averagePromptLength > 55 ? 1 : 0), 1, 5),
    },
    {
      key: 'execution_rigor',
      level: clamp(1 + Math.min(runCount, 3) + (saveCount > 0 ? 1 : 0), 1, 5),
    },
    {
      key: 'testing_validation',
      level: clamp(1 + (hasTestingSignal ? 2 : 0) + (hasVerificationSignal ? 1 : 0), 1, 5),
    },
    {
      key: 'ai_collaboration',
      level: clamp(1 + Math.min(promptCount, 3) + (averagePromptLength > 30 ? 1 : 0), 1, 5),
    },
    {
      key: 'technical_communication',
      level: clamp(1 + (averagePromptLength > 45 ? 1 : 0) + (hasTradeoffSignal ? 2 : 0), 1, 5),
    },
    {
      key: 'delivery_momentum',
      level: clamp(1 + (timeSpentSeconds > 180 ? 1 : 0) + (runCount > 1 ? 1 : 0) + (promptCount > 0 ? 1 : 0), 1, 5),
    },
  ].map((entry) => ({
    ...entry,
    label: categoryPlaybook[entry.key].label,
    band: profileBandForLevel(entry.level),
  }));

  const benchmarkByCategory = successfulCandidateBenchmarks[taskKey] || successfulCandidateBenchmarks.default;
  const comparisonCategories = categories.map((entry) => {
    const benchmarkLevel = clamp(
      Number(benchmarkByCategory[entry.key] ?? successfulCandidateBenchmarks.default[entry.key] ?? 3.5),
      1,
      5,
    );
    const deltaLevel = Number((entry.level - benchmarkLevel).toFixed(1));
    return {
      key: entry.key,
      label: entry.label,
      candidateLevel: entry.level,
      benchmarkLevel,
      deltaLevel,
      candidateScore: levelToScore(entry.level),
      benchmarkScore: levelToScore(benchmarkLevel),
    };
  });

  const candidateAvgLevel = categories.reduce((acc, entry) => acc + entry.level, 0) / Math.max(categories.length, 1);
  const benchmarkAvgLevel = comparisonCategories.reduce((acc, entry) => acc + entry.benchmarkLevel, 0) / Math.max(comparisonCategories.length, 1);
  const derivedCandidateScore = levelToScore(candidateAvgLevel);
  const benchmarkScore = levelToScore(benchmarkAvgLevel);
  const submissionTaaliScore = normalizeScore(
    submissionResult?.taali_score
    ?? submissionResult?.score_breakdown?.score_components?.taali_score,
    '0-100',
  );
  const candidateScore = roundTo(submissionTaaliScore, 1) ?? derivedCandidateScore;
  const canonicalCategoryScores = deriveCanonicalCategoryScores({ categories, submissionResult });
  const heuristicSummary = String(
    submissionResult?.score_breakdown?.heuristic_summary
    || submissionResult?.prompt_analytics?.heuristic_summary
    || 'Comparison against successful-candidate average.',
  ).trim();

  return {
    categories,
    comparison: {
      candidateScore,
      benchmarkScore,
      deltaScore: roundTo(candidateScore - benchmarkScore, 1),
      categories: comparisonCategories,
      benchmarkLabel: 'Successful-candidate average',
    },
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
      candidateScore,
      heuristicSummary,
    }),
    submission: {
      id: toFiniteNumber(submissionResult?.id),
      status: submissionResult?.status || null,
      completedAt: submissionResult?.completed_at || null,
    },
  };
};
