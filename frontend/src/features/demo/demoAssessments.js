const demoTasks = [
  {
    id: 'data_eng_aws_glue_pipeline_recovery',
    title: 'AWS Glue Pipeline Recovery',
    description: 'Recover a finance-critical AWS Glue revenue pipeline by fixing schema drift, dedupe, and bookmark trust issues.',
    durationLabel: '30 min',
    difficulty: 'Medium',
  },
  {
    id: 'ai_eng_genai_production_readiness',
    title: 'GenAI Production Readiness Review',
    description: 'Stabilize a risky GenAI launch by improving safety guardrails, degraded-mode behavior, and release judgment.',
    durationLabel: '30 min',
    difficulty: 'Medium',
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
