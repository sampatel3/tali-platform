import aiGenaiProductionReadinessTask from './productWalkthroughTask';

import { buildStandingCandidateReportModel } from '../candidates/assessmentViewModels';

const pickRepoFiles = (task, preferredPaths = []) => {
  const files = task?.repo_structure?.files && typeof task.repo_structure.files === 'object'
    ? task.repo_structure.files
    : {};
  const orderedPaths = [
    ...preferredPaths.filter((path) => path in files),
    ...Object.keys(files).filter((path) => !preferredPaths.includes(path)),
  ];

  return orderedPaths.map((path) => ({
    path,
    content: String(files[path] || ''),
  }));
};

const summarizeScenario = (scenario, maxParagraphs = 4) => (
  String(scenario || '')
    .split('\n\n')
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .slice(0, maxParagraphs)
    .join('\n\n')
);

const AI_RUNTIME_REPO_FILES = pickRepoFiles(aiGenaiProductionReadinessTask, [
  'README.md',
  'RISKS.md',
  'docs/launch_checklist.md',
  'intelligence/config.py',
  'intelligence/review.py',
  'intelligence/analyzer.py',
  'tests/test_analyzer.py',
]);

const AI_RUNTIME_OUTPUT = [
  '$ pytest -q --tb=short',
  'FAILED tests/test_analyzer.py::test_handles_llm_failure_gracefully',
  'FAILED tests/test_review.py::test_medium_confidence_high_risk_action_requires_review',
  'FAILED tests/test_retrieval.py::test_retriever_returns_grounded_context_documents',
].join('\n');

export const AI_SHOWCASE_APPLICATION = {
  id: 9001,
  candidate_name: 'Candidate walkthrough',
  candidate_email: '',
  candidate_position: 'AI Engineer',
  role_name: 'AI Engineer',
  pipeline_stage: 'technical review',
  application_outcome: 'review',
  workable_sourced: true,
  workable_score_raw: 74,
  created_at: '2026-04-21T09:42:00.000Z',
  updated_at: '2026-04-21T11:08:00.000Z',
  score_summary: {
    assessment_status: 'completed',
  },
  screening_interview_summary: {
    fireflies: {
      status: 'linked',
      configured: true,
      capture_expected: true,
      invite_email: 'taali@fireflies.ai',
      latest_summary:
        'Transcript attached. Recruiters can revisit how the candidate framed risk, where they used AI, and what they escalated instead of auto-shipping.',
      latest_source: 'Fireflies transcript',
      latest_meeting_date: '2026-04-21T10:00:00.000Z',
    },
  },
};

export const AI_SHOWCASE_COMPLETED_ASSESSMENT = {
  id: 1042,
  status: 'completed',
  role_name: 'AI Engineer',
  taali_score: 81,
  assessment_score: 84,
  final_score: 84,
  role_fit_score: 78,
  cv_job_match_score: 76,
  total_duration_seconds: 36 * 60,
  tests_passed: 7,
  tests_total: 8,
  completed_at: '2026-04-21T10:36:00.000Z',
  total_prompts: 9,
  prompt_quality_score: 8.4,
  browser_focus_ratio: 0.93,
  tab_switch_count: 4,
  calibration_score: 8.1,
  prompt_fraud_flags: [],
  score_breakdown: {
    heuristic_summary:
      'The candidate uses AI as a reviewer, not an autopilot. The strongest signal is how they catch unsafe defaults and keep release judgment anchored in risk, evidence, and escalation paths.',
    category_scores: {
      task_completion: 8.4,
      prompt_clarity: 8.6,
      context_provision: 8.4,
      independence_efficiency: 8.0,
      response_utilization: 8.3,
      debugging_design: 8.5,
      written_communication: 7.8,
    },
    score_components: {
      taali_score: 81,
      assessment_score: 84,
      role_fit_score: 78,
      cv_fit_score: 76,
      requirements_fit_score: 80,
    },
  },
  cv_job_match_details: {
    score_scale: '0-100',
    role_fit_score_100: 78,
    summary:
      'Strong fit for an AI engineer role where release safety, prompt judgment, and production readiness matter as much as shipping speed.',
    score_rationale_bullets: [
      'Reads the system risk first and scopes AI use carefully.',
      'Spots unsafe defaults and checks whether AI suggestions should be trusted.',
      'Keeps launch decisions tied to compliance and reliability, not just passing tests.',
    ],
    requirements_match_score_100: 80,
    requirements_coverage: {
      total: 4,
      met: 3,
      partially_met: 1,
      missing: 0,
    },
    requirements_assessment: [
      {
        requirement: 'Communicate residual launch risk clearly',
        priority: 'must_have',
        status: 'partially_met',
        evidence: 'Good judgment overall, but the handoff language still needs to be sharper for legal and compliance stakeholders.',
        impact: 'Probe how they would explain the smallest safe release to non-engineering decision-makers.',
      },
    ],
    matching_skills: [
      'AI-assisted debugging',
      'Prompt design under constraints',
      'Release judgment',
      'Production risk triage',
    ],
    missing_skills: ['Deeper compliance stakeholder framing'],
    experience_highlights: [
      'Used AI to accelerate repo reading without outsourcing the critical decisions.',
    ],
    concerns: [
      'Follow up on how they would communicate what still blocks launch after the patch set.',
    ],
  },
};

export const PRODUCT_WALKTHROUGH = {
  runtime: {
    taskName: aiGenaiProductionReadinessTask.name,
    taskRole: 'AI Engineer',
    taskContext: summarizeScenario(aiGenaiProductionReadinessTask.scenario),
    repoFiles: AI_RUNTIME_REPO_FILES,
    initialSelectedRepoPath: 'intelligence/analyzer.py',
    initialClaudePrompt:
      'Prioritize the highest-risk launch blockers first, then outline the smallest safe patch sequence for this regulated GenAI release.',
    claudeConversation: [
      {
        role: 'user',
        content:
          'Prioritize the highest-risk launch blockers first, then outline the smallest safe patch sequence for this regulated GenAI release.',
      },
    ],
    output: AI_RUNTIME_OUTPUT,
  },
  report: {
    reportModel: buildStandingCandidateReportModel({
      application: AI_SHOWCASE_APPLICATION,
      completedAssessment: AI_SHOWCASE_COMPLETED_ASSESSMENT,
      identity: {
        assessmentId: AI_SHOWCASE_COMPLETED_ASSESSMENT.id,
        sectionLabel: 'Standing report',
        name: AI_SHOWCASE_APPLICATION.candidate_name,
        email: AI_SHOWCASE_APPLICATION.candidate_email,
        position: AI_SHOWCASE_APPLICATION.candidate_position,
        taskName: aiGenaiProductionReadinessTask.name,
        roleName: AI_SHOWCASE_APPLICATION.role_name,
        applicationStatus: AI_SHOWCASE_APPLICATION.application_outcome,
        durationLabel: '36 min',
        completedLabel: 'Apr 21, 2026',
      },
    }),
    hero: {
      eyebrow: 'Candidate standing report',
      title: 'Candidate walkthrough',
      subtitle:
        'The recruiter side keeps the AI-collaboration read, Workable context, and transcript-backed interview evidence in one place.',
      stageLabel: `Stage · ${AI_SHOWCASE_APPLICATION.pipeline_stage}`,
      outcomeLabel: `Outcome · ${AI_SHOWCASE_APPLICATION.application_outcome}`,
      workableLabel: 'Synced from Workable',
      stats: [
        {
          key: 'taali',
          label: 'Taali score',
          value: '81',
          description: 'AI-collaboration and delivery signal',
          highlight: true,
        },
        {
          key: 'role-fit',
          label: 'Role fit',
          value: '78',
          description: 'Role and background alignment',
        },
        {
          key: 'assessment',
          label: 'Assessment',
          value: '84',
          description: 'Completed technical assessment',
        },
        {
          key: 'workable',
          label: 'Workable raw',
          value: '74',
          description: 'Existing ATS signal',
        },
      ],
    },
    workable: {
      workableRawScore: AI_SHOWCASE_APPLICATION.workable_score_raw,
      taaliScore: AI_SHOWCASE_COMPLETED_ASSESSMENT.taali_score,
      posted: false,
      workableProfileUrl: '',
      scorePrecedence: 'workable_first',
    },
  },
};

export const PRODUCT_WALKTHROUGH_TASK = {
  id: aiGenaiProductionReadinessTask.task_id,
  title: aiGenaiProductionReadinessTask.name,
  role: 'AI Engineer',
  durationLabel: `${aiGenaiProductionReadinessTask.duration_minutes} min`,
  stack: 'Python · Claude · Release safety',
  tools: 'Repo · Editor · Terminal · Claude',
  description:
    'Review a regulated GenAI launch, tighten the safety guardrails, and show how the candidate works with AI when the repo and launch pressure are both real.',
};

export const PRODUCT_WALKTHROUGH_START_DATA = {
  assessment_id: 9001,
  token: 'demo-product-walkthrough',
  candidate_name: 'Candidate walkthrough',
  organization_name: 'Taali demo',
  time_remaining: aiGenaiProductionReadinessTask.duration_minutes * 60,
  terminal_mode: false,
  task: {
    ...aiGenaiProductionReadinessTask,
    name: aiGenaiProductionReadinessTask.name,
    duration_minutes: aiGenaiProductionReadinessTask.duration_minutes,
    proctoring_enabled: false,
    claude_budget_limit_usd: 8,
  },
};
