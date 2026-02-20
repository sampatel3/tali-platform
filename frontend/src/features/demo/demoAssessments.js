const demoTasks = [
  {
    id: 'data_eng_super_platform_crisis',
    title: 'Data Platform Incident Triage and Recovery',
    description: 'Triage urgent data platform failures, prioritize fixes, and stabilize finance, compliance, and pipeline reliability.',
    durationLabel: '30 min',
    difficulty: 'Medium',
  },
  {
    id: 'ai_eng_super_production_launch',
    title: 'AI Feature Production Readiness Assessment',
    description: 'Evaluate an AI feature prototype and make production-safe improvements across safety, reliability, and cost.',
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
