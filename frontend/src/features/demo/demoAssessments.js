const demoTasks = [
  {
    id: 'data_eng_a_pipeline_reliability',
    title: 'Orders Pipeline Reliability Sprint',
    description: 'Deliver one production-safe patch for dedupe, hard deletes, schema drift, and idempotent backfills.',
    durationLabel: '35 min',
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
