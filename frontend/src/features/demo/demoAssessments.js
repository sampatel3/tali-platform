const demoTasks = [
  {
    // Keep legacy track id for backwards compatibility with older backend deploys.
    id: 'data_eng_b_cdc_fix',
    title: 'Orders Pipeline Reliability Sprint',
    description: 'Deliver one production-safe patch for dedupe, hard deletes, schema drift, and idempotent backfills (target ~10 min, hard cap 15 min).',
    durationLabel: '15 min',
    difficulty: 'Advanced',
  },
];

export const DEMO_ASSESSMENTS = demoTasks;
export const DEFAULT_DEMO_ASSESSMENT_ID = DEMO_ASSESSMENTS[0]?.id || null;

const demoAssessmentMap = DEMO_ASSESSMENTS.reduce((acc, assessment) => {
  acc[assessment.id] = assessment;
  return acc;
}, {});

export const getDemoAssessmentById = (assessmentId) => (
  demoAssessmentMap[assessmentId] || demoAssessmentMap[DEFAULT_DEMO_ASSESSMENT_ID]
);
