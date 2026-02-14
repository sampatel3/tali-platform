import { dimensionOrder, getDimensionById, normalizeScores } from '../scoring/scoringDimensions';

type CategoryConfig = {
  key: string;
  label: string;
};

type AssessmentLike = {
  _raw?: Record<string, any>;
  breakdown?: Record<string, any>;
  [key: string]: any;
};

/** Canonical category keys used for radar/overlay comparison. */
export const COMPARISON_CATEGORY_KEYS = [...dimensionOrder];

/** Config for comparison charts: key, label from glossary. */
export const COMPARISON_CATEGORY_CONFIG: CategoryConfig[] = COMPARISON_CATEGORY_KEYS.map((key) => ({
  key,
  label: getDimensionById(key).label || key.replace(/_/g, ' '),
}));

/**
 * Get category scores (0–10) from an assessment or display candidate.
 * Used for overlay radar in dashboard comparison.
 */
export function getCategoryScoresFromAssessment(assessmentOrCandidate: AssessmentLike): Record<string, number> {
  const raw = assessmentOrCandidate?._raw ?? assessmentOrCandidate;
  const breakdown = assessmentOrCandidate?.breakdown ?? raw?.breakdown;
  const legacyFlatBreakdownScores = breakdown
    ? {
        task_completion: breakdown.taskCompletion,
        prompt_clarity: breakdown.promptClarity,
        context_provision: breakdown.contextProvision,
        independence: breakdown.independence,
        utilization: breakdown.utilization,
        communication: breakdown.communication,
        approach: breakdown.approach,
        cv_match: breakdown.cvMatch,
      }
    : {};
  const categoryScores =
    breakdown?.categoryScores ||
    breakdown?.detailedScores?.category_scores ||
    raw?.score_breakdown?.category_scores ||
    raw?.prompt_analytics?.category_scores ||
    raw?.prompt_analytics?.detailed_scores?.category_scores ||
    raw?.prompt_analytics?.ai_scores ||
    legacyFlatBreakdownScores ||
    {};
  return normalizeScores(categoryScores);
}
