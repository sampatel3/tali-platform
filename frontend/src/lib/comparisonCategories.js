import { SCORING_CATEGORY_GLOSSARY } from './scoringGlossary';

/** Category keys used for radar/overlay comparison (must match backend score_breakdown.category_scores). */
export const COMPARISON_CATEGORY_KEYS = [
  'task_completion',
  'prompt_clarity',
  'context_provision',
  'independence',
  'utilization',
  'communication',
  'approach',
  'cv_match',
];

/** Config for comparison charts: key, label from glossary. */
export const COMPARISON_CATEGORY_CONFIG = COMPARISON_CATEGORY_KEYS.map((key) => ({
  key,
  label: SCORING_CATEGORY_GLOSSARY[key]?.label || key.replace(/_/g, ' '),
}));

/**
 * Get category scores (0–10) from an assessment or display candidate.
 * Used for overlay radar in dashboard comparison.
 */
export function getCategoryScoresFromAssessment(assessmentOrCandidate) {
  const raw = assessmentOrCandidate?._raw ?? assessmentOrCandidate;
  const breakdown = assessmentOrCandidate?.breakdown ?? raw?.breakdown;
  const categoryScores = breakdown?.categoryScores ?? breakdown?.detailedScores?.category_scores ?? raw?.prompt_analytics?.detailed_scores?.category_scores ?? {};
  return categoryScores;
}
