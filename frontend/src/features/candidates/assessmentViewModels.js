import { getDimensionById, normalizeScores } from '../../scoring/scoringDimensions';
import { formatScale100Score, normalizeScore } from '../../lib/scoreDisplay';

export const COMPLETED_ASSESSMENT_STATUSES = new Set(['completed', 'completed_due_to_timeout']);

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

const weightedAverage100 = (...weightedValues) => {
  let numerator = 0;
  let denominator = 0;

  weightedValues.forEach(([value, weight]) => {
    const numericValue = toFiniteNumber(normalizeScore(value, '0-100'));
    const numericWeight = Number(weight);
    if (!Number.isFinite(numericValue) || !Number.isFinite(numericWeight) || numericWeight <= 0) return;
    numerator += numericValue * numericWeight;
    denominator += numericWeight;
  });

  if (denominator <= 0) return null;
  return Math.round((numerator / denominator) * 10) / 10;
};

const computeRoleFitScore = (cvFitScore, requirementsFitScore) => weightedAverage100(
  [cvFitScore, 0.5],
  [requirementsFitScore, 0.5]
);

const computeTaaliScore = (assessmentScore, roleFitScore) => weightedAverage100(
  [assessmentScore, 0.5],
  [roleFitScore, 0.5]
);

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

const buildFallbackRationale = (details = {}, roleFitScore = null, cvFitScore = null, requirementsFitScore = null) => {
  const coverage = details.requirements_coverage && typeof details.requirements_coverage === 'object'
    ? details.requirements_coverage
    : {};

  return uniqueTrimmed([
    roleFitScore != null ? `Role fit ${formatScale100Score(roleFitScore, '0-100')}` : null,
    roleFitScore != null && cvFitScore != null && requirementsFitScore != null
      ? `Role fit blends CV fit ${formatScale100Score(cvFitScore, '0-100')} and recruiter requirements ${formatScale100Score(requirementsFitScore, '0-100')}`
      : null,
    roleFitScore != null && cvFitScore != null && requirementsFitScore == null
      ? `Role fit currently reflects CV fit ${formatScale100Score(cvFitScore, '0-100')}`
      : null,
    roleFitScore != null && cvFitScore == null && requirementsFitScore != null
      ? `Role fit currently reflects recruiter requirements ${formatScale100Score(requirementsFitScore, '0-100')}`
      : null,
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
    const scoreComponents = completedAssessment.score_breakdown?.score_components || {};
    const roleFitComponents = scoreComponents.role_fit_components || {};
    const cvFitScore = normalizeScore(
      scoreComponents.cv_fit_score
      ?? roleFitComponents.cv_fit_score
      ?? matchScores.overall
      ?? completedAssessment.cv_job_match_score,
      details.score_scale || '0-100'
    );
    const requirementsFitScore = normalizeScore(
      scoreComponents.requirements_fit_score
      ?? roleFitComponents.requirements_fit_score
      ?? details.requirements_match_score_100,
      '0-100'
    );
    const roleFitScore = normalizeScore(
      scoreComponents.role_fit_score ?? details.role_fit_score_100 ?? computeRoleFitScore(cvFitScore, requirementsFitScore),
      '0-100'
    );

    return {
      sourceType: 'assessment',
      sourceLabel: 'Completed assessment',
      scoreScale: details.score_scale || '0-100',
      overallScore: cvFitScore,
      cvFitScore,
      roleFitScore,
      skillsScore: normalizeScore(matchScores.skills, details.score_scale || '0-100'),
      experienceScore: normalizeScore(matchScores.experience, details.score_scale || '0-100'),
      requirementsFitScore,
      details,
    };
  }

  const details = application?.cv_match_details && typeof application.cv_match_details === 'object'
    ? application.cv_match_details
    : {};
  const scoreSummary = application?.score_summary || {};
  const roleFitComponents = scoreSummary.role_fit_components || {};
  const cvFitScore = normalizeScore(
    scoreSummary.cv_fit_score ?? roleFitComponents.cv_fit_score ?? application?.cv_match_score,
    details.score_scale || '0-100'
  );
  const requirementsFitScore = normalizeScore(
    scoreSummary.requirements_fit_score ?? roleFitComponents.requirements_fit_score ?? details.requirements_match_score_100,
    '0-100'
  );
  const roleFitScore = normalizeScore(
    scoreSummary.role_fit_score ?? details.role_fit_score_100 ?? computeRoleFitScore(cvFitScore, requirementsFitScore),
    '0-100'
  );

  return {
    sourceType: 'application',
    sourceLabel: 'Application CV fit',
    scoreScale: details.score_scale || '0-100',
    overallScore: cvFitScore,
    cvFitScore,
    roleFitScore,
    skillsScore: normalizeScore(details.skills_match_score_100, '0-100'),
    experienceScore: normalizeScore(details.experience_match_score_100, '0-100'),
    requirementsFitScore,
    details,
  };
};

export const resolveScoreSource = ({ application, completedAssessment }) => {
  const hasCompletedAssessment = Boolean(
    completedAssessment
    && COMPLETED_ASSESSMENT_STATUSES.has(normalizeStatus(completedAssessment.status))
  );

  if (hasCompletedAssessment) {
    return {
      kind: 'assessment',
      label: 'Completed assessment',
      badgeVariant: 'purple',
      updatedAt: completedAssessment.completed_at || completedAssessment.updated_at || completedAssessment.created_at || null,
    };
  }

  return {
    kind: 'application',
    label: 'Application CV fit',
    badgeVariant: 'muted',
    updatedAt: application?.cv_match_scored_at || application?.updated_at || application?.created_at || null,
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
  const firstRequirementGap = requirementsAssessment.find((item) => item.status !== 'met') || null;
  const summaryText = sanitizeScoreText(String(details.summary || '').trim()) || null;

  return {
    ...payload,
    rationaleBullets: rationaleBullets.length
      ? rationaleBullets
      : buildFallbackRationale(details, payload.roleFitScore, payload.cvFitScore, payload.requirementsFitScore),
    requirementsCoverage: details.requirements_coverage && typeof details.requirements_coverage === 'object'
      ? details.requirements_coverage
      : {},
    requirementsAssessment,
    firstRequirementGap,
    matchingSkills: Array.isArray(details.matching_skills) ? details.matching_skills.filter(Boolean) : [],
    missingSkills: Array.isArray(details.missing_skills) ? details.missing_skills.filter(Boolean) : [],
    experienceHighlights: Array.isArray(details.experience_highlights) ? details.experience_highlights.filter(Boolean) : [],
    concerns: Array.isArray(details.concerns) ? details.concerns.filter(Boolean) : [],
    summaryText,
    hasAnyEvidence: Boolean(
      payload.roleFitScore != null
      || payload.cvFitScore != null
      || payload.requirementsFitScore != null
      || requirementsAssessment.length
      || rationaleBullets.length
      || buildFallbackRationale(details, payload.roleFitScore, payload.cvFitScore, payload.requirementsFitScore).length
    ),
  };
};

const buildFallbackAssessmentSummary = ({
  completedAssessment,
  roleFitModel,
  strongestDimension,
  weakestDimension,
  categoryScores,
}) => {
  const summaryBits = [];
  const scoredDimensions = Object.keys(categoryScores || {}).length;

  if (strongestDimension) {
    summaryBits.push(`Strongest dimension: ${getDimensionById(strongestDimension).label}`);
  }

  if (weakestDimension) {
    summaryBits.push(`Weakest dimension to probe: ${getDimensionById(weakestDimension).label}`);
  }

  if (toFiniteNumber(completedAssessment?.tests_total) > 0) {
    summaryBits.push(`Passed ${completedAssessment.tests_passed ?? 0} of ${completedAssessment.tests_total} tests`);
  }

  if (roleFitModel?.firstRequirementGap?.requirement) {
    summaryBits.push(`First recruiter requirement gap: ${roleFitModel.firstRequirementGap.requirement}`);
  }

  if (!roleFitModel?.firstRequirementGap?.requirement && roleFitModel?.summaryText) {
    summaryBits.push(roleFitModel.summaryText);
  }

  if (!summaryBits.length && Array.isArray(roleFitModel?.matchingSkills) && roleFitModel.matchingSkills.length) {
    summaryBits.push(`Strong matching skills: ${roleFitModel.matchingSkills.slice(0, 4).join(', ')}`);
  }

  if (!summaryBits.length && Array.isArray(roleFitModel?.concerns) && roleFitModel.concerns.length) {
    summaryBits.push(`Risk to probe: ${roleFitModel.concerns[0]}`);
  }

  if (!summaryBits.length && scoredDimensions > 0) {
    summaryBits.push(`Completed assessment returned evidence across ${scoredDimensions} scored dimensions`);
  }

  if (!summaryBits.length) {
    summaryBits.push('Completed assessment detail loaded. Review prompts, tests, and git evidence below.');
  }

  return sanitizeScoreText(summaryBits.join('. '));
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
    const roleFitScore = normalizeScore(
      completedAssessment.role_fit_score
      ?? scoreBreakdown.score_components?.role_fit_score
      ?? roleFitModel.roleFitScore
      ?? computeRoleFitScore(roleFitModel.cvFitScore, roleFitModel.requirementsFitScore),
      '0-100'
    );
    const taaliScore = normalizeScore(
      completedAssessment.taali_score
      ?? scoreBreakdown.score_components?.taali_score
      ?? scoreSummary.taali_score
      ?? computeTaaliScore(assessmentScore, roleFitScore)
      ?? completedAssessment.final_score
      ?? completedAssessment.score,
      completedAssessment.taali_score != null || completedAssessment.final_score != null ? '0-100' : '0-10'
    );

    return {
      source,
      taaliScore,
      assessmentScore,
      roleFitScore,
      cvFitScore: roleFitModel.cvFitScore,
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
        categoryScores,
      }),
      categoryScores,
      assessmentStatus: completedAssessment.status || scoreSummary.assessment_status || null,
      completedAt: completedAssessment.completed_at || scoreSummary.assessment_completed_at || null,
      updatedAt: source.updatedAt,
    };
  }

  return {
    source,
    taaliScore: normalizeScore(
      scoreSummary.taali_score
      ?? application?.taali_score
      ?? roleFitModel.roleFitScore
      ?? roleFitModel.cvFitScore,
      '0-100'
    ),
    assessmentScore: normalizeScore(scoreSummary.assessment_score, '0-100'),
    roleFitScore: roleFitModel.roleFitScore,
    cvFitScore: roleFitModel.cvFitScore,
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

const getRecommendation = (score100) => {
  const numeric = toFiniteNumber(score100);
  if (!Number.isFinite(numeric)) return { label: 'Pending', variant: 'muted' };
  if (numeric >= 80) return { label: 'Strong Hire', variant: 'success' };
  if (numeric >= 65) return { label: 'Hire', variant: 'info' };
  if (numeric >= 50) return { label: 'Consider', variant: 'warning' };
  return { label: 'No Hire', variant: 'danger' };
};

const describeTimelineStatus = (status) => {
  const normalized = normalizeStatus(status);
  if (!normalized) return 'No assessment attempts yet';
  if (normalized === 'completed_due_to_timeout') return 'Latest attempt completed due to timeout';
  return `Latest attempt ${normalized.replace(/_/g, ' ')}`;
};

const truncateToken = (value, size = 10) => {
  const text = String(value || '').trim();
  if (!text) return null;
  if (text.length <= size) return text;
  return `${text.slice(0, size)}...`;
};

const buildEvidenceSection = ({
  title,
  badgeLabel,
  badgeVariant,
  description,
  items = [],
  emptyMessage,
}) => ({
  title,
  badgeLabel,
  badgeVariant,
  description,
  items: uniqueTrimmed(items, 4),
  emptyMessage,
});

const buildEvidenceSections = ({ application, completedAssessment, roleFitModel, summaryModel }) => {
  const assessment = completedAssessment && typeof completedAssessment === 'object' ? completedAssessment : null;
  const gitEvidence = assessment?.git_evidence && typeof assessment.git_evidence === 'object'
    ? assessment.git_evidence
    : {};
  const timelineEvents = Array.isArray(assessment?.timeline) ? assessment.timeline : [];
  const assessmentHistory = Array.isArray(application?.assessment_history) ? application.assessment_history : [];
  const cvFilename = assessment?.candidate_cv_filename || assessment?.cv_filename || application?.cv_filename || null;
  const jobSpecFilename = assessment?.candidate_job_spec_filename || application?.role_job_spec_filename || null;
  const aiUsageItems = assessment ? [
    assessment.total_prompts != null ? `${assessment.total_prompts} prompts captured` : null,
    assessment.prompt_quality_score != null ? `Prompt clarity ${assessment.prompt_quality_score.toFixed(1)}/10` : null,
    assessment.browser_focus_ratio != null ? `Browser focus ${Math.round(assessment.browser_focus_ratio * 100)}%` : null,
    assessment.tab_switch_count != null ? `${assessment.tab_switch_count} tab switches recorded` : null,
    assessment.calibration_score != null ? `Calibration ${assessment.calibration_score.toFixed(1)}/10` : null,
    Array.isArray(assessment.prompt_fraud_flags) && assessment.prompt_fraud_flags.length
      ? `${assessment.prompt_fraud_flags.length} integrity flags need review`
      : null,
  ] : [];
  const codeAndGitItems = assessment ? [
    gitEvidence.head_sha ? `Final HEAD ${truncateToken(gitEvidence.head_sha, 12)}` : null,
    gitEvidence.commits ? 'Assessment branch commits were captured' : null,
    gitEvidence.diff_main ? 'Diff against main was captured' : null,
    gitEvidence.diff_staged ? 'Staged diff evidence is available' : null,
    gitEvidence.status_porcelain ? 'Working tree status was captured' : null,
    gitEvidence.error ? `Git evidence warning: ${gitEvidence.error}` : null,
    assessment.final_repo_state ? 'Final repository state snapshot is attached' : null,
  ] : [];
  const timelineItems = assessment ? [
    timelineEvents.length ? `${timelineEvents.length} timeline events recorded` : null,
    describeTimelineStatus(assessment.status),
    assessment.started_at ? `Started ${new Date(assessment.started_at).toLocaleString()}` : null,
    assessment.completed_at ? `Completed ${new Date(assessment.completed_at).toLocaleString()}` : null,
    assessment.superseded_by_assessment_id ? `Superseded by assessment #${assessment.superseded_by_assessment_id}` : null,
    assessment.is_voided ? 'This attempt was voided but remains visible for audit history' : null,
  ] : [
    assessmentHistory.length ? `${assessmentHistory.length} assessment attempts on this role` : null,
    describeTimelineStatus(summaryModel.assessmentStatus || application?.status),
  ];
  const documentItems = [
    cvFilename ? `CV on file: ${cvFilename}` : 'CV not uploaded yet',
    jobSpecFilename ? `Job specification on file: ${jobSpecFilename}` : null,
    roleFitModel.summaryText || null,
    summaryModel.source.kind === 'assessment'
      ? 'Documents are paired with completed assessment evidence in this report'
      : 'Documents currently drive the standing role-fit view until assessment evidence arrives',
  ];

  return {
    aiUsage: buildEvidenceSection({
      title: 'AI usage',
      badgeLabel: assessment ? 'Assessment derived' : 'Pending',
      badgeVariant: assessment ? 'purple' : 'muted',
      description: assessment
        ? 'Prompt activity, browser focus, and calibration stay attached to the standing report.'
        : 'This section activates after a completed assessment adds prompt telemetry.',
      items: aiUsageItems,
      emptyMessage: 'No AI usage evidence is available yet.',
    }),
    codeAndGit: buildEvidenceSection({
      title: 'Code and git',
      badgeLabel: assessment ? 'Workspace evidence' : 'Pending',
      badgeVariant: 'muted',
      description: assessment
        ? 'Repository evidence stays connected to the recruiter-facing report for auditability.'
        : 'Git evidence appears once the candidate works in the assessment workspace.',
      items: codeAndGitItems,
      emptyMessage: assessment
        ? 'No git evidence was captured for this assessment.'
        : 'No code or git evidence is available before assessment completion.',
    }),
    timeline: buildEvidenceSection({
      title: 'History timeline',
      badgeLabel: assessment ? 'Assessment history' : 'Application history',
      badgeVariant: 'muted',
      description: 'Retakes, status changes, and submission history remain visible beside the standing report.',
      items: timelineItems,
      emptyMessage: 'Timeline history will appear here as recruiter activity accumulates.',
    }),
    documents: buildEvidenceSection({
      title: 'Source documents',
      badgeLabel: cvFilename ? 'On file' : 'Missing',
      badgeVariant: cvFilename ? 'success' : 'warning',
      description: 'Source documents stay visible so recruiters can connect the score back to the evidence.',
      items: documentItems,
      emptyMessage: 'No source documents are available for this candidate yet.',
    }),
  };
};

const normalizeFirefliesBlob = (value) => (
  value && typeof value === 'object' ? value : {}
);

const buildFirefliesModel = ({ application }) => {
  const screeningSummary = application?.screening_interview_summary || {};
  const techSummary = application?.tech_interview_summary || {};
  const evidenceSummary = application?.interview_evidence_summary || {};

  const screeningFireflies = normalizeFirefliesBlob(screeningSummary.fireflies);
  const techFireflies = normalizeFirefliesBlob(techSummary.fireflies);
  const evidenceFireflies = normalizeFirefliesBlob(evidenceSummary.fireflies);
  const fireflies = Object.keys(evidenceFireflies).length
    ? evidenceFireflies
    : (Object.keys(screeningFireflies).length ? screeningFireflies : techFireflies);

  const status = normalizeStatus(fireflies.status || 'not_configured');
  const configured = Boolean(
    fireflies.configured
    ?? screeningFireflies.configured
    ?? techFireflies.configured
  );
  const captureExpected = Boolean(
    fireflies.capture_expected
    ?? screeningFireflies.capture_expected
    ?? techFireflies.capture_expected
  );
  const inviteEmail = String(
    fireflies.invite_email
    || screeningFireflies.invite_email
    || techFireflies.invite_email
    || ''
  ).trim();
  const latestSummary = String(
    fireflies.latest_summary
    || screeningFireflies.latest_summary
    || techFireflies.latest_summary
    || ''
  ).trim();
  const latestProviderUrl = String(
    fireflies.latest_provider_url
    || screeningFireflies.latest_provider_url
    || techFireflies.latest_provider_url
    || screeningSummary.latest_provider_url
    || techSummary.latest_provider_url
    || ''
  ).trim();
  const latestMeetingDate = (
    fireflies.latest_meeting_date
    || screeningFireflies.latest_meeting_date
    || techFireflies.latest_meeting_date
    || screeningSummary.latest_meeting_date
    || techSummary.latest_meeting_date
    || null
  );
  const latestSource = String(
    fireflies.latest_source
    || screeningFireflies.latest_source
    || techFireflies.latest_source
    || ''
  ).trim();

  let statusLabel = 'Fireflies not configured';
  let badgeVariant = 'muted';
  let description = '';

  if (status === 'linked') {
    statusLabel = 'Stage 1 Fireflies transcript linked';
    badgeVariant = 'warning';
    description = latestSummary || 'The latest screening transcript is attached and available to recruiters.';
  } else if (status === 'awaiting_transcript') {
    statusLabel = 'Awaiting Fireflies transcript';
    badgeVariant = 'info';
    description = inviteEmail
      ? `Include ${inviteEmail} in the Workable interview invite so TAALI can capture the Stage 1 call.`
      : 'Fireflies is configured and TAALI is waiting for the Stage 1 transcript to be linked.';
  } else if (status === 'not_expected') {
    statusLabel = 'Fireflies capture not expected';
    badgeVariant = 'muted';
    description = 'This application is not currently expected to receive an automatic Fireflies transcript.';
  } else if (captureExpected) {
    statusLabel = 'Fireflies not configured';
    badgeVariant = 'muted';
    description = 'Workable interview capture is expected for this application, but Fireflies is not configured yet.';
  }

  const shouldSurface = Boolean(
    status === 'linked'
    || status === 'awaiting_transcript'
    || captureExpected
    || configured
    || inviteEmail
    || latestSummary
    || latestProviderUrl
  );

  return {
    shouldSurface,
    status,
    statusLabel,
    badgeVariant,
    configured,
    captureExpected,
    inviteEmail: inviteEmail || null,
    latestSummary: latestSummary || null,
    latestProviderUrl: latestProviderUrl || null,
    latestMeetingDate,
    latestSource: latestSource || null,
    linked: status === 'linked',
    description,
  };
};

export const buildStandingCandidateReportModel = ({
  application = null,
  completedAssessment = null,
  identity = {},
}) => {
  const summaryModel = buildAssessmentSummaryModel({ application, completedAssessment });
  const roleFitModel = buildRoleFitEvidenceModel({ application, completedAssessment });
  const categoryScores = normalizeScores(summaryModel.categoryScores || {});
  const dimensionEntries = Object.entries(categoryScores)
    .map(([key, value]) => ({
      key,
      label: getDimensionById(key).label,
      value: Number(value),
    }))
    .filter((item) => Number.isFinite(item.value));
  const recommendation = getRecommendation(summaryModel.taaliScore);
  const recruiterSummaryText = roleFitModel.summaryText
    || roleFitModel.rationaleBullets?.[0]
    || summaryModel.heuristicSummary
    || 'TAALI keeps the evidence attached to the score so recruiters can move faster with less ambiguity.';
  const probeTitle = roleFitModel.firstRequirementGap?.requirement
    || (summaryModel.weakestLabel !== '—' ? summaryModel.weakestLabel : 'Primary probe area');
  const probeDescription = roleFitModel.firstRequirementGap?.impact
    || roleFitModel.firstRequirementGap?.evidence
    || (
      summaryModel.weakestLabel !== '—'
        ? `Interview deeper on ${summaryModel.weakestLabel.toLowerCase()}.`
        : 'Probe where the candidate needs stronger evidence before moving forward.'
    );
  const integritySummaryText = completedAssessment?.superseded_by_assessment_id
    ? 'A newer recruiter retake exists, but the visible completed attempt remains the source of truth until superseded in review.'
    : completedAssessment?.is_voided
      ? 'This attempt was voided. History remains visible so the recruiter can understand how the candidate record changed.'
      : application?.score_summary?.has_voided_attempts
        ? 'Historical attempts stay visible alongside the standing report so the recommendation remains auditable.'
        : 'Retakes and prior attempts stay visible without replacing the standing candidate report.';
  const strongestSignalTitle = summaryModel.strongestLabel !== '—'
    ? summaryModel.strongestLabel
    : (roleFitModel.roleFitScore != null ? 'Role fit' : 'Signal building');
  const strongestSignalDescription = summaryModel.strongestLabel !== '—'
    ? `Highest observed signal currently appears in ${summaryModel.strongestLabel.toLowerCase()}.`
    : (
      roleFitModel.roleFitScore != null
        ? 'Role fit is the strongest available signal until more completed-assessment evidence is present.'
        : 'Signal will strengthen as TAALI collects more completed assessment evidence.'
    );
  const evidenceSections = buildEvidenceSections({
    application,
    completedAssessment,
    roleFitModel,
    summaryModel,
  });
  const firefliesModel = buildFirefliesModel({ application });

  return {
    identity,
    source: summaryModel.source,
    summaryModel,
    roleFitModel,
    recommendation,
    dimensionEntries,
    recruiterSummaryText,
    strongestSignalTitle,
    strongestSignalDescription,
    probeTitle,
    probeDescription,
    integritySummaryText,
    evidenceSections,
    firefliesModel,
    hasCompletedAssessment: summaryModel.source.kind === 'assessment',
    hasDimensionSignal: dimensionEntries.length > 0,
  };
};
