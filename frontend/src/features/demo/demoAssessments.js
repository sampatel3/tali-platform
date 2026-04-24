const demoTasks = [
  {
    id: 'data_eng_aws_glue_pipeline_recovery',
    title: 'AWS Glue Pipeline Recovery',
    description: 'Recover a finance-critical AWS Glue revenue pipeline by fixing schema drift, dedupe, and bookmark trust issues.',
    durationLabel: '30 min',
    difficulty: 'Medium',
    role: 'Data Platform Engineer',
    team: 'Finance data infrastructure',
    stack: 'PySpark · AWS Glue · Athena',
    tools: 'Claude · IDE · Logs',
    deliverable: 'Patch + validation notes',
    candidateLead: 'A production ETL job is dropping late-arriving records and double-counting revenue after a schema change.',
    candidateChecklist: [
      'Diagnose schema drift, bookmark reuse, and dedupe failures in a real pipeline.',
      'Work with Claude the way you normally would and justify each fix.',
      'Leave a short validation note explaining why the patched flow is safe to rerun.',
    ],
    recruiterSignals: [
      { label: 'Prompt quality', value: 89, detail: 'Candidate scoped root-cause checks before asking Claude for code.' },
      { label: 'Error recovery', value: 86, detail: 'Rejected a premature repartition fix and traced the duplicate records first.' },
      { label: 'Independence', value: 91, detail: 'Owned the replay and bookmark strategy without leaning on canned suggestions.' },
    ],
  },
  {
    id: 'ai_eng_genai_production_readiness',
    title: 'GenAI Production Readiness Review',
    description: 'Stabilize a risky GenAI launch by improving safety guardrails, degraded-mode behavior, and release judgment.',
    durationLabel: '30 min',
    difficulty: 'Medium',
    role: 'AI Full Stack Engineer',
    team: 'GenAI product engineering',
    stack: 'TypeScript · Postgres · Claude Code',
    tools: 'Claude · IDE · Docs',
    deliverable: 'Safe release patch sequence',
    candidateLead: 'A public launch can default to allow during moderation outages. The candidate must tighten the release guardrails without over-blocking traffic.',
    candidateChecklist: [
      'Review a real repo and identify the highest-risk launch blockers first.',
      'Use Claude to plan, challenge, and refine the fix instead of copying blindly.',
      'Submit the patch sequence with clear reasoning about degraded mode and escalation paths.',
    ],
    recruiterSignals: [
      { label: 'Prompt quality', value: 91, detail: 'Candidate framed the riskiest blocker first and requested the smallest safe patch.' },
      { label: 'Error recovery', value: 88, detail: 'Caught Claude’s incorrect caching suggestion before it touched production logic.' },
      { label: 'Independence', value: 94, detail: 'Delegated boilerplate but wrote the release judgment and escalation path personally.' },
    ],
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
