const demoTasks = [
  {
    id: 'data_eng_b_cdc_fix',
    title: 'Fix the Broken Data Sync',
    description: 'Debug a CDC sync that is creating duplicates, missing updates, and never deleting removed rows.',
    durationLabel: '10 min',
    difficulty: 'Intermediate',
  },
  {
    id: 'data_eng_c_backfill_schema',
    title: 'Historical Backfill + Schema Evolution',
    description: 'Add a safe backfill mode and automatic schema evolution without breaking the working incremental pipeline.',
    durationLabel: '10 min',
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
