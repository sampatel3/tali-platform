import { getDimensionById, normalizeScores } from '../../scoring/scoringDimensions';
import { formatScale100Score, normalizeScore } from '../../lib/scoreDisplay';

const COMPLETED_STATUSES = new Set(['completed', 'completed_due_to_timeout']);

const normalizeStatus = (value) => String(value || '').trim().toLowerCase();

const toFiniteNumber = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const sanitizeScoreText = (value) => String(value || '').replace(/(\d+(?:\.\d+)?)\s*\/\s*100\b/g, (_, score) => {
  const numeric = Number(score);
  if (!Number.isFinite(numeric)) return score;
  return numeric.toFixed(1);
});

const uniqueTrimmed = (items, maxItems = Infinity) => {
  const seen = new Set();
  const output = [];

  (Array.isArray(items) ? items : []).forEach((item) => {
    const text = sanitizeScoreText(String(item || '').replace(/\s+/g, ' ').trim());
    if (!text) return;
    const key = text.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    output.push(text.endsWith('.') ? text : `${text}.`);
  });

  return output.slice(0, maxItems);
};

const deriveCategoryScores = (assessment) => {
  if (!assessment || typeof assessment !== 'object') return {};
  const scoreBreakdown = assessment.score_breakdown && typeof assessment.score_breakdown === 'object'
    ? assessment.score_breakdown
    : {};
  const promptAnalytics = assessment.prompt_analytics && typeof assessment.prompt_analytics === 'object'
    ? assessment.prompt_analytics
    : {};

  const rawScores = scoreBreakdown.category_scores
    || promptAnalytics.category_scores
    || promptAnalytics.ai_scores
    || promptAnalytics.detailed_scores?.category_scores
    || {};

  return normalizeScores(rawScores);
};

const deriveDimensionExtremes = (assessment) => {
  const categoryScores = deriveCategoryScores(assessment);
  const scored = Object.entries(categoryScores)
    .map(([key, value]) => ({ key, value: Number(value) }))
    .filter((item) => Number.isFinite(item.value));

  if (!scored.length) {
    return { strongestDimension: null, weakestDimension: null, categoryScores };
  }

  const strongest = [...scored].sort((a, b) => b.value - a.value)[0];
  const weakest = [...scored].sort((a, b) => a.value - b.value)[0];

  return {
    categoryScores,
    strongestDimension: strongest?.key || null,
    weakestDimension: weakest?.key || null,
  };
};

const buildFallbackRationale = (details = {}, overallScore = null) => {
  const coverage = details.requirements_coverage && typeof details.requirements_coverage === 'object'
    ? details.requirements_coverage
    : {};

  return uniqueTrimmed([
    overallScore != null ? `Role fit score ${formatScale100Score(overallScore, '0-100')}` : null,
    coverage.total
      ? `Recruiter requirements coverage: ${coverage.met ?? 0}/${coverage.total} met, ${coverage.partially_met ?? 0} partial, ${coverage.missing ?? 0} missing`
      : null,
    Array.isArray(details.matching_skills) && details.matching_skills.length
      ? `Strong CV-to-role evidence: ${details.matching_skills.slice(0, 4).join(', ')}`
      : null,
    Array.isArray(details.experience_highlights) && details.experience_highlights.length
      ? `Relevant experience evidence: ${details.experience_highlights.slice(0, 2).join('; ')}`
      : null,
    Array.isArray(details.missing_skills) && details.missing_skills.length
      ? `Gaps vs role requirements: ${details.missing_skills.slice(0, 4).join(', ')}`
      : null,
    Array.isArray(details.concerns) && details.concerns.length
      ? `Risk signals from CV evidence: ${details.concerns.slice(0, 2).join('; ')}`
      : null,
  ]);
};

const getRoleFitPayload = ({ application, completedAssessment }) => {
  if (completedAssessment && typeof completedAssessment === 'object') {
    const details = completedAssessment.cv_job_match_details && typeof completedAssessment.cv_job_match_details === 'object'
      ? completedAssessment.cv_job_match_details
      : (completedAssessment.prompt_analytics?.cv_job_match?.details || {});
    const matchScores = completedAssessment.prompt_analytics?.cv_job_match || {};

    return {
      sourceType: 'assessment',
      sourceLabel: 'Completed assessment',
      scoreScale: details.score_scale || '0-100',
      overallScore: normalizeScore(matchScores.overall ?? completedAssessment.cv_job_match_score, details.score_scale || '0-100'),
      skillsScore: normalizeScore(matchScores.skills, details.score_scale || '0-100'),
      experienceScore: normalizeScore(matchScores.experience, details.score_scale || '0-100'),
      requirementsFitScore: normalizeScore(details.requirements_match_score_100, '0-100'),
      details,
    };
  }

  const details = application?.cv_match_details && typeof application.cv_match_details === 'object'
    ? application.cv_match_details
    : {};
  const scoreSummary = application?.score_summary || {};

  return {
    sourceType: 'application',
    sourceLabel: 'Application CV fit',
    scoreScale: details.score_scale || '0-100',
    overallScore: normalizeScore(scoreSummary.cv_fit_score ?? application?.cv_match_score, details.score_scale || '0-100'),
    skillsScore: normalizeScore(details.skills_match_score_100, '0-100'),
    experienceScore: normalizeScore(details.experience_match_score_100, '0-100'),
    requirementsFitScore: normalizeScore(
      scoreSummary.requirements_fit_score ?? details.requirements_match_score_100,
      '0-100'
    ),
    details,
  };
};

export const resolveScoreSource = ({ application, completedAssessment }) => {
  const hasCompletedAssessment = Boolean(
    completedAssessment
    && COMPLETED_STATUSES.has(normalizeStatus(completedAssessment.status))
  );

  if (hasCompletedAssessment) {
    return {
      kind: 'assessment',
      label: 'Completed assessment',
      badgeVariant: 'purple',
      updatedAt: completedAssessment.completed_at || completedAssessment.updated_at || completedAssessment.created_at || null,
      formulaLabel: application?.score_summary?.formula_label || 'TAALI score blends assessment and role-fit evidence when both are available.',
    };
  }

  return {
    kind: 'application',
    label: 'Application CV fit',
    badgeVariant: 'muted',
    updatedAt: application?.cv_match_scored_at || application?.updated_at || application?.created_at || null,
    formulaLabel: application?.score_summary?.formula_label || 'TAALI score currently reflects CV fit until a completed assessment is available.',
  };
};

export const buildRoleFitEvidenceModel = ({ application, completedAssessment }) => {
  const payload = getRoleFitPayload({ application, completedAssessment });
  const details = payload.details && typeof payload.details === 'object' ? payload.details : {};
  const rationaleBullets = uniqueTrimmed(details.score_rationale_bullets, 6);
  const requirementsAssessment = Array.isArray(details.requirements_assessment)
    ? details.requirements_assessment
      .map((item) => ({
        requirement: String(item?.requirement || '').trim(),
        priority: String(item?.priority || 'nice_to_have').toLowerCase(),
        status: String(item?.status || 'unknown').toLowerCase(),
        evidence: String(item?.evidence || '').trim(),
        impact: String(item?.impact || '').trim(),
      }))
      .filter((item) => item.requirement)
    : [];

  return {
    ...payload,
    rationaleBullets: rationaleBullets.length
      ? rationaleBullets
      : buildFallbackRationale(details, payload.overallScore),
    requirementsCoverage: details.requirements_coverage && typeof details.requirements_coverage === 'object'
      ? details.requirements_coverage
      : {},
    requirementsAssessment,
    matchingSkills: Array.isArray(details.matching_skills) ? details.matching_skills.filter(Boolean) : [],
    missingSkills: Array.isArray(details.missing_skills) ? details.missing_skills.filter(Boolean) : [],
    experienceHighlights: Array.isArray(details.experience_highlights) ? details.experience_highlights.filter(Boolean) : [],
    concerns: Array.isArray(details.concerns) ? details.concerns.filter(Boolean) : [],
    hasAnyEvidence: Boolean(
      payload.overallScore != null
      || payload.requirementsFitScore != null
      || requirementsAssessment.length
      || rationaleBullets.length
      || buildFallbackRationale(details, payload.overallScore).length
    ),
  };
};

const buildFallbackAssessmentSummary = ({ completedAssessment, roleFitModel, strongestDimension, weakestDimension }) => {
  const summaryBits = [];

  if (strongestDimension) {
    summaryBits.push(`Strongest dimension: ${getDimensionById(strongestDimension).label}`);
  }

  if (weakestDimension) {
    summaryBits.push(`Interview deeper on ${getDimensionById(weakestDimension).label.toLowerCase()}`);
  }

  if (roleFitModel?.requirementsAssessment?.length) {
    const weakestRequirement = roleFitModel.requirementsAssessment.find((item) => item.status !== 'met');
    if (weakestRequirement) {
      summaryBits.push(`Probe requirement gap: ${weakestRequirement.requirement}`);
    }
  }

  if (!summaryBits.length && toFiniteNumber(completedAssessment?.tests_total) > 0) {
    summaryBits.push(
      `Passed ${completedAssessment.tests_passed ?? 0} of ${completedAssessment.tests_total} tests`
    );
  }

  return sanitizeScoreText(summaryBits.join('. ') || 'Assessment evidence is available for recruiter review.');
};

export const buildAssessmentSummaryModel = ({ application, completedAssessment }) => {
  const source = resolveScoreSource({ application, completedAssessment });
  const scoreSummary = application?.score_summary || {};
  const roleFitModel = buildRoleFitEvidenceModel({ application, completedAssessment });

  if (source.kind === 'assessment') {
    const { strongestDimension, weakestDimension, categoryScores } = deriveDimensionExtremes(completedAssessment);
    const scoreBreakdown = completedAssessment.score_breakdown && typeof completedAssessment.score_breakdown === 'object'
      ? completedAssessment.score_breakdown
      : {};
    const heuristicSummary = sanitizeScoreText(String(
      scoreBreakdown.heuristic_summary
      || completedAssessment.prompt_analytics?.heuristic_summary
      || ''
    ).trim());

    const assessmentScore = normalizeScore(
      completedAssessment.assessment_score ?? completedAssessment.final_score ?? completedAssessment.score,
      completedAssessment.score != null && Number(completedAssessment.score) <= 10 ? '0-10' : '0-100'
    );
    const taaliScore = normalizeScore(
      completedAssessment.taali_score ?? scoreSummary.taali_score ?? completedAssessment.final_score ?? completedAssessment.score,
      completedAssessment.taali_score != null || completedAssessment.final_score != null ? '0-100' : '0-10'
    );

    return {
      source,
      taaliScore,
      assessmentScore,
      cvFitScore: roleFitModel.overallScore,
      requirementsFitScore: roleFitModel.requirementsFitScore,
      strongestDimension,
      weakestDimension,
      strongestLabel: strongestDimension ? getDimensionById(strongestDimension).label : '—',
      weakestLabel: weakestDimension ? getDimensionById(weakestDimension).label : '—',
      heuristicSummary: heuristicSummary || buildFallbackAssessmentSummary({
        completedAssessment,
        roleFitModel,
        strongestDimension,
        weakestDimension,
      }),
      categoryScores,
      assessmentStatus: completedAssessment.status || scoreSummary.assessment_status || null,
      completedAt: completedAssessment.completed_at || scoreSummary.assessment_completed_at || null,
      updatedAt: source.updatedAt,
    };
  }

  return {
    source,
    taaliScore: normalizeScore(scoreSummary.taali_score ?? application?.taali_score ?? application?.cv_match_score, '0-100'),
    assessmentScore: normalizeScore(scoreSummary.assessment_score, '0-100'),
    cvFitScore: roleFitModel.overallScore,
    requirementsFitScore: roleFitModel.requirementsFitScore,
    strongestDimension: null,
    weakestDimension: null,
    strongestLabel: '—',
    weakestLabel: '—',
    heuristicSummary: roleFitModel.rationaleBullets[0]
      || 'TAALI score is currently driven by CV-to-role evidence until a completed assessment is available.',
    categoryScores: {},
    assessmentStatus: scoreSummary.assessment_status || null,
    completedAt: scoreSummary.assessment_completed_at || null,
    updatedAt: source.updatedAt,
  };
};
