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
  '...........F.F....F                                                       [100%]',
  '',
  '=================================== FAILURES ===================================',
  '___________________ test_medium_confidence_high_risk_action_requires_review ____________________',
  '',
  'tests/test_review.py:48: in test_medium_confidence_high_risk_action_requires_review',
  '    assert result.requires_human_review is True',
  'E   AssertionError: assert False is True',
  'E    +  where False = ReviewDecision(action=\'approve\', confidence=0.62, ...).requires_human_review',
  '',
  '___________________ test_retriever_returns_grounded_context_documents ___________________',
  '',
  'tests/test_retrieval.py:63: in test_retriever_returns_grounded_context_documents',
  '    assert all(doc.grounded for doc in docs)',
  'E   AssertionError: assert False',
  'E    +  where False = all(<generator object ...>)',
  '',
  '___________________ test_handles_llm_failure_gracefully ___________________',
  '',
  'tests/test_analyzer.py:91: in test_handles_llm_failure_gracefully',
  '    with pytest.raises(EscalationRequired):',
  'E   Failed: DID NOT RAISE <class \'intelligence.errors.EscalationRequired\'>',
  '',
  '=========================== short test summary info ============================',
  'FAILED tests/test_review.py::test_medium_confidence_high_risk_action_requires_review',
  'FAILED tests/test_retrieval.py::test_retriever_returns_grounded_context_documents',
  'FAILED tests/test_analyzer.py::test_handles_llm_failure_gracefully',
  '3 failed, 17 passed in 4.82s',
].join('\n');

const AI_SHOWCASE_CV_TEXT = [
  'Priya Raman',
  'AI Engineer · GenAI release safety, prompt design, production-grade evals',
  'priya.raman@example.com · Greater London, UK · linkedin.com/in/priya-raman',
  '',
  'Profile',
  'AI engineer with seven years building production ML and LLM systems for regulated industries (fintech, health-tech, insurance). Leads small teams that ship release-safe GenAI features without trading reliability for speed. Comfortable owning the full path from offline eval design to incident review and post-launch metric audits.',
  '',
  'Experience',
  'Senior AI Engineer · Helix Health · Sep 2023 – Present',
  '- Owned the GenAI patient-summarisation rollout across two NHS trusts; designed the offline eval harness, retrieval grounding checks, and the release-gate that catches medium-confidence high-risk outputs before they reach clinicians.',
  '- Reduced unsafe completions on the launch checklist from 6.1% to 0.4% by introducing a deterministic risk classifier in front of the generative path, with a calibrated escalation route to human reviewers.',
  '- Wrote the postmortem framework that legal and clinical safety adopted across all GenAI features; co-author of the company-wide AI usage policy.',
  '',
  'AI Engineer · Lighthouse FinCrime · Jan 2021 – Aug 2023',
  '- Built the fraud-narrative summariser used by 80+ analysts, replacing a 14-template ruleset; cut median triage time per case from 11 minutes to 4 minutes while keeping false-positive escalations flat.',
  '- Owned the prompt registry and red-team review process for production prompts; introduced versioned prompt diffs gated on offline regression tests.',
  '- Mentored two junior engineers through their first production LLM incident, including a public-facing communications draft.',
  '',
  'Machine Learning Engineer · Polaris Labs · Jul 2018 – Dec 2020',
  '- Trained and deployed three transformer-based classifiers used for KYC document review; owned drift monitoring and the quarterly recalibration cadence.',
  '- Authored the internal "what changed" framework for ML model release notes — adopted across the platform team.',
  '',
  'Education',
  'MSc Computer Science (Machine Learning), University of Edinburgh · 2017 – 2018 · Distinction',
  'BSc Mathematics, Imperial College London · 2014 – 2017 · First-class honours',
  '',
  'Skills',
  'Python, TypeScript, PyTorch, Anthropic & OpenAI APIs, retrieval pipelines (pgvector, LanceDB), eval harness design, prompt versioning, release safety reviews, incident response, FastAPI, Postgres, Kubernetes',
  '',
  'Certifications',
  'AWS Certified Machine Learning – Specialty (2023)',
  'IAPP AIGP — Artificial Intelligence Governance Professional (2024)',
  '',
  'Links',
  'github.com/priya-raman · priya-raman.dev/notes (AI release-safety writing)',
].join('\n');

const AI_SHOWCASE_CV_SECTIONS = {
  parse_failed: false,
  headline: 'AI Engineer · GenAI release safety, prompt design, production-grade evals',
  summary:
    'AI engineer with seven years building production ML and LLM systems for regulated industries (fintech, health-tech, insurance). Leads small teams that ship release-safe GenAI features without trading reliability for speed.',
  experience: [
    {
      title: 'Senior AI Engineer',
      company: 'Helix Health',
      start_date: 'Sep 2023',
      end_date: 'Present',
      bullets: [
        'Owned the GenAI patient-summarisation rollout across two NHS trusts; designed offline eval harness, retrieval grounding checks, and the release-gate that catches medium-confidence high-risk outputs before they reach clinicians.',
        'Reduced unsafe completions on the launch checklist from 6.1% to 0.4% by introducing a deterministic risk classifier in front of the generative path, with a calibrated escalation route to human reviewers.',
        'Wrote the postmortem framework that legal and clinical safety adopted across all GenAI features; co-author of the company-wide AI usage policy.',
      ],
    },
    {
      title: 'AI Engineer',
      company: 'Lighthouse FinCrime',
      start_date: 'Jan 2021',
      end_date: 'Aug 2023',
      bullets: [
        'Built the fraud-narrative summariser used by 80+ analysts, replacing a 14-template ruleset; cut median triage time per case from 11 minutes to 4 minutes while keeping false-positive escalations flat.',
        'Owned the prompt registry and red-team review process for production prompts; introduced versioned prompt diffs gated on offline regression tests.',
      ],
    },
    {
      title: 'Machine Learning Engineer',
      company: 'Polaris Labs',
      start_date: 'Jul 2018',
      end_date: 'Dec 2020',
      bullets: [
        'Trained and deployed three transformer-based classifiers used for KYC document review; owned drift monitoring and the quarterly recalibration cadence.',
      ],
    },
  ],
  education: [
    { degree: 'MSc Computer Science (Machine Learning)', school: 'University of Edinburgh', start_date: '2017', end_date: '2018', notes: 'Distinction' },
    { degree: 'BSc Mathematics', school: 'Imperial College London', start_date: '2014', end_date: '2017', notes: 'First-class honours' },
  ],
  skills: [
    'Python', 'TypeScript', 'PyTorch', 'Anthropic API', 'OpenAI API',
    'pgvector', 'LanceDB', 'Eval harness design', 'Prompt versioning',
    'Release safety reviews', 'Incident response', 'FastAPI', 'Postgres', 'Kubernetes',
  ],
  certifications: [
    'AWS Certified Machine Learning – Specialty (2023)',
    'IAPP AIGP — Artificial Intelligence Governance Professional (2024)',
  ],
  languages: [],
  links: [
    'github.com/priya-raman',
    'linkedin.com/in/priya-raman',
    'priya-raman.dev/notes',
  ],
};

export const AI_SHOWCASE_APPLICATION = {
  id: 9001,
  candidate_name: 'Priya Raman',
  candidate_email: 'priya.raman@example.com',
  candidate_position: 'AI Engineer',
  candidate_location: 'Greater London, UK',
  candidate_headline: 'AI Engineer · GenAI release safety, prompt design, production-grade evals',
  candidate_summary:
    'AI engineer with seven years building production ML and LLM systems for regulated industries. Leads small teams that ship release-safe GenAI features without trading reliability for speed.',
  candidate_skills: AI_SHOWCASE_CV_SECTIONS.skills,
  candidate_experience: AI_SHOWCASE_CV_SECTIONS.experience,
  candidate_education: AI_SHOWCASE_CV_SECTIONS.education,
  candidate_profile_url: 'https://linkedin.com/in/priya-raman',
  candidate_social_profiles: [
    { name: 'GitHub', url: 'https://github.com/priya-raman' },
    { name: 'Notes', url: 'https://priya-raman.dev/notes' },
  ],
  role_name: 'AI Engineer',
  pipeline_stage: 'technical review',
  application_outcome: 'review',
  workable_sourced: true,
  workable_score_raw: 74,
  created_at: '2026-04-21T09:42:00.000Z',
  updated_at: '2026-04-21T11:08:00.000Z',
  cv_filename: 'priya-raman-ai-engineer.txt',
  cv_uploaded_at: '2026-04-19T18:22:00.000Z',
  cv_text: AI_SHOWCASE_CV_TEXT,
  cv_sections: AI_SHOWCASE_CV_SECTIONS,
  cv_match_scored_at: '2026-04-20T08:14:00.000Z',
  total_prompts: 9,
  score_summary: {
    assessment_status: 'completed',
    assessment_id: 1042,
    taali_score: 81,
  },
  screening_interview_summary: {
    fireflies: {
      status: 'linked',
      configured: true,
      capture_expected: true,
      invite_email: 'taali@fireflies.ai',
      latest_summary:
        'Recruiter screen covered prior LLM release-safety work, the Helix patient-summarisation rollout, and how the candidate framed the risk-vs-velocity trade-off. The candidate distinguished between AI as a reviewer and AI as an autopilot, and pushed back on shipping medium-confidence high-risk outputs without explicit human escalation. Reasoned through a real production incident from Lighthouse where a prompt change unintentionally widened the false-negative rate, and walked through the rollback plus the regression test added afterwards.',
      latest_source: 'Fireflies transcript',
      latest_meeting_date: '2026-04-21T10:00:00.000Z',
      highlights: [
        'Frames release safety in terms of human escalation paths, not just model accuracy.',
        'Has owned a real GenAI incident postmortem end-to-end and adopted the framework org-wide.',
        'Clear about which decisions a model should never make alone in regulated contexts.',
      ],
    },
    summary:
      'Strong recruiter screen. The candidate articulated release safety in production GenAI terms — risk classifiers in front of generative paths, calibrated escalation, and explicit handoff language for legal and clinical reviewers. They distinguished cleanly between using AI to accelerate the work and using AI to make the call.',
    next_step: 'Move to technical assessment + 45-min systems-design panel focused on a regulated GenAI launch.',
  },
  tech_interview_summary: {
    summary:
      'Panel design probed how the candidate sequences a GenAI launch when the safety review is incomplete. Walked the panel through prioritising launch blockers by blast radius, separating the smallest safe patch set from the cosmetic improvements, and explicitly naming the residual risks they would not ship past — even under launch pressure. Visible muscle memory around offline eval harness design, prompt-versioning, and what they would refuse to automate.',
    highlights: [
      'Prioritised the highest-blast-radius launch blockers first; named what they would NOT ship past.',
      'Strong on offline eval harness design — described the exact regression cases they would add post-incident.',
      'Pushed back on the panel framing once, evidence-based; recovered well and re-anchored the conversation on the user impact.',
    ],
    concerns: [
      'Compliance-stakeholder framing was good but not yet sharp; would benefit from a structured handoff template when communicating residual risk.',
    ],
    next_step: 'Final loop with engineering leadership + product safety; close on compensation if signal stays consistent.',
  },
  interview_evidence_summary: {
    summary:
      'Two interviews on file (recruiter screen + technical panel) with Fireflies transcripts attached. Evidence converges: the candidate uses AI as a reviewer rather than an autopilot, and is willing to slow a launch when the residual risk crosses a threshold they can articulate.',
    transcript_excerpts: [
      {
        question: 'Walk us through how you would sequence the patches for the failing release safety tests.',
        answer:
          'I would start with the medium-confidence high-risk gating because that is the one that lets unsafe outputs through the door. The grounded-context retrieval test is also load-bearing but I can ship a smaller fix there first and follow with the broader retrieval refactor. The graceful-failure test for the LLM call is real but lower blast radius — I would pair-program the patch with someone reviewing for fallback semantics and ship it third.',
      },
      {
        question: 'How would you communicate the residual risk to non-engineering decision-makers?',
        answer:
          'I would write a one-page note framed around three things: which user actions the patch set covers, which it does not yet cover, and what human escalation route handles the gap. I would avoid technical risk language and lean on concrete user scenarios. The legal team at Helix has responded well to that framing.',
      },
    ],
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
      'Priya treats AI as a reviewer rather than an autopilot. The strongest signal across the 36-minute session is how she sequences risk: she opens the repo by reading RISKS.md and the launch checklist before touching any code, names the medium-confidence high-risk gating as the highest-blast-radius failure, and explicitly names what she will NOT ship past — even though the brief presents launch pressure.\n\nHer prompts to Claude are short, scoped, and load context deliberately. When the model suggested a broader retrieval refactor, she pushed back, scoped the change to a smaller patch set, and asked Claude to draft only the regression test for the failing case. She caught two unsafe defaults the model proposed (a silent fallback to cached completions on LLM failure; a default-allow on medium-confidence outputs) and rejected both with explicit reasoning.\n\nWritten communication is the one dimension where the signal is clearly weaker than the rest: her commit messages and the README patch are accurate but compressed; the handoff language for non-engineering stakeholders would need to be sharper before she could lead a regulated launch on her own. This is the single area worth probing in the loop.',
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
      'Strong fit for an AI engineer role where release safety, prompt judgment, and production readiness matter as much as shipping speed. The CV evidence aligns tightly with the assessment behaviour: Priya has owned a regulated GenAI launch (Helix Health, NHS trusts), authored an org-wide postmortem framework, and run a prompt registry with red-team review at Lighthouse. She has the muscle memory to triage a launch the way the assessment scenario asks — sequence by blast radius, refuse to ship past medium-confidence high-risk failures, and write the residual-risk note clinical and legal stakeholders will actually read.',
    score_rationale_bullets: [
      'Seven years of production ML/LLM experience in regulated domains (health-tech, fintech, KYC) — directly applicable to the role profile.',
      'Has owned an end-to-end GenAI release — eval harness, retrieval grounding, release-gate, escalation route — not just shipped a feature.',
      'Reduced unsafe completions on a real launch checklist from 6.1% to 0.4%; metric-bearing release safety experience is rare in the pipeline.',
      'Prompt registry + red-team review process at Lighthouse maps cleanly onto how this team manages production prompts.',
      'IAPP AIGP certification signals serious investment in AI governance, not just delivery.',
    ],
    requirements_match_score_100: 80,
    requirements_coverage: {
      total: 5,
      met: 3,
      partially_met: 2,
      missing: 0,
    },
    requirements_assessment: [
      {
        requirement: 'Owned a production GenAI release in a regulated domain',
        priority: 'must_have',
        status: 'met',
        evidence: 'Helix Health: led the patient-summarisation rollout across two NHS trusts. Designed offline eval harness, retrieval grounding checks, and the release-gate. Reduced unsafe completions from 6.1% to 0.4%.',
        impact: 'Will not need to be ramped on the basics of regulated GenAI launches; can lead the next launch directly.',
      },
      {
        requirement: 'Eval harness design + offline regression testing for prompts',
        priority: 'must_have',
        status: 'met',
        evidence: 'Built and owned the offline eval harness at Helix; introduced versioned prompt diffs gated on regression tests at Lighthouse. The assessment showed her writing exactly the regression test for the failing release-safety case before patching.',
        impact: 'Can pick up our existing eval pipeline and extend it; no learning curve on the methodology.',
      },
      {
        requirement: 'Incident response + postmortem ownership for AI features',
        priority: 'must_have',
        status: 'met',
        evidence: 'Authored the postmortem framework adopted across all GenAI features at Helix. Walked the screening interviewer through a real Lighthouse incident where a prompt change widened false-negatives, and named the regression test added afterwards.',
        impact: 'Can take incident-commander rotation on the AI on-call from week one.',
      },
      {
        requirement: 'Communicate residual launch risk to legal and compliance stakeholders',
        priority: 'must_have',
        status: 'partially_met',
        evidence: 'Strong instinct (frames launches around what each patch covers vs. what the human escalation route handles), but the handoff language is still compressed. Helix legal team has responded well, but she has not yet built a structured template the broader org could reuse.',
        impact: 'Probe how she would explain the smallest safe release to non-engineering decision-makers; pair with comms support on her first launch here.',
      },
      {
        requirement: 'Cross-functional leadership across engineering, legal, clinical/compliance',
        priority: 'nice_to_have',
        status: 'partially_met',
        evidence: 'Co-author of the company-wide AI usage policy at Helix; mentored junior engineers through their first incident. Has not yet led a multi-team launch with separate engineering and product safety reporting lines.',
        impact: 'Strong direction of travel but not yet senior-staff level; promote with a paired senior partner on the first major launch.',
      },
    ],
    matching_skills: [
      'AI-assisted debugging',
      'Prompt design under constraints',
      'Release judgment under pressure',
      'Production risk triage',
      'Offline eval harness design',
      'Retrieval grounding',
      'Incident postmortem authoring',
    ],
    missing_skills: [
      'Structured residual-risk handoff templates for non-engineering stakeholders',
      'Multi-team launch coordination across separate reporting lines',
    ],
    experience_highlights: [
      'Reduced unsafe completions on a regulated GenAI launch from 6.1% to 0.4% via a deterministic risk classifier in front of the generative path.',
      'Authored the postmortem framework that legal and clinical safety adopted across all Helix GenAI features.',
      'Owned the prompt registry and red-team review process for production prompts at Lighthouse.',
    ],
    concerns: [
      'Compliance-stakeholder handoff language is good in instinct but not yet templated; would benefit from a structured residual-risk note pattern the broader org could reuse.',
      'Has not yet led a launch where engineering and product safety report into separate lines — pair her first major launch with a senior partner who has.',
    ],
  },
  prompts_list: [
    { index: 1, role: 'user', content: 'Read RISKS.md and the launch checklist before anything else. Summarise the highest-blast-radius failures in the failing pytest run, in priority order. Do not propose patches yet.' },
    { index: 2, role: 'user', content: 'For the medium-confidence high-risk gating test only, explain what behaviour the test expects vs. what intelligence/review.py currently does. Quote the relevant lines. Do not change code.' },
    { index: 3, role: 'user', content: 'Now propose the smallest possible patch to intelligence/review.py that makes that test pass without changing the public API. Show the diff.' },
    { index: 4, role: 'user', content: 'You proposed a default-allow on medium-confidence outputs as a fallback. Reject that — explain why an explicit escalation is the correct default and revise the patch.' },
    { index: 5, role: 'user', content: 'Add a regression test that covers the case where the LLM call fails entirely. Do not catch the exception silently; the test should assert the escalation path fires.' },
    { index: 6, role: 'user', content: 'For the grounded-retrieval test, propose a one-line scope change that fixes the failing case without doing the broader retrieval refactor. Save the refactor for a follow-up.' },
    { index: 7, role: 'user', content: 'Run pytest -q and show me the output.' },
    { index: 8, role: 'user', content: 'Draft the README patch describing what each of the three patches covers and what residual risk remains. Keep the language readable to a non-engineer.' },
    { index: 9, role: 'user', content: 'Name three things you would NOT ship past — even under launch pressure — given the current patch set.' },
  ],
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
          'Read RISKS.md and the launch checklist before anything else. Summarise the highest-blast-radius failures in the failing pytest run, in priority order. Do not propose patches yet.',
      },
      {
        role: 'assistant',
        content:
          'Reading RISKS.md and docs/launch_checklist.md before opening the failing tests.\n\nThree failures, ranked by blast radius:\n\n1. test_medium_confidence_high_risk_action_requires_review — HIGHEST. The current ReviewDecision returns approve on confidence=0.62 for a high_risk action. RISKS.md item R-04 explicitly names this as the unsafe-completions failure mode that triggered the Q3 incident. Letting medium-confidence high-risk outputs through without human review is the failure that gets users hurt.\n\n2. test_retriever_returns_grounded_context_documents — HIGH. Some retrieved documents are coming back with grounded=False, meaning the analyzer can ground its answer in unverified context. RISKS.md item R-02 (factually unsupported summaries) flows directly from this.\n\n3. test_handles_llm_failure_gracefully — MEDIUM. When the LLM call fails, the analyzer is currently swallowing the exception and returning a default response instead of raising EscalationRequired. Real impact is bounded (fails closed in most paths) but it violates the explicit "no silent fallback on model failure" rule in the launch checklist.\n\nNo patches yet. Want me to scope each one before changing code?',
      },
      {
        role: 'user',
        content:
          'For the medium-confidence high-risk gating test only, explain what behaviour the test expects vs. what intelligence/review.py currently does. Quote the relevant lines. Do not change code.',
      },
      {
        role: 'assistant',
        content:
          'The test expects ReviewDecision(action=\'approve\', confidence=0.62, risk=\'high\').requires_human_review to be True.\n\nCurrent intelligence/review.py:34-41:\n\n    def needs_review(decision: ReviewDecision) -> bool:\n        if decision.confidence < CONFIDENCE_FLOOR:\n            return True\n        if decision.risk == "critical":\n            return True\n        return False\n\nThe gap: medium confidence (0.55–0.85) on a high-risk action falls through both checks and returns False. The test (and RISKS.md R-04) require that high-risk actions need explicit human review whenever confidence is below the high-confidence floor (~0.9), not just when confidence is critically low or risk is critical.\n\nThis is a default-allow on the riskiest path. The fix needs to invert that to default-escalate.',
      },
      {
        role: 'user',
        content:
          'Now propose the smallest possible patch to intelligence/review.py that makes that test pass without changing the public API. Show the diff. And — do not introduce a default-allow fallback anywhere. Escalation must be explicit.',
      },
      {
        role: 'assistant',
        content:
          'Smallest patch — adds the high-risk + sub-high-confidence rule before the existing checks. Public API unchanged.\n\n--- a/intelligence/review.py\n+++ b/intelligence/review.py\n@@\n     def needs_review(decision: ReviewDecision) -> bool:\n+        # High-risk actions require explicit human review unless confidence is at the\n+        # high-confidence floor. Default-allow on this path is forbidden — see RISKS.md R-04.\n+        if decision.risk == "high" and decision.confidence < HIGH_CONFIDENCE_FLOOR:\n+            return True\n         if decision.confidence < CONFIDENCE_FLOOR:\n             return True\n         if decision.risk == "critical":\n             return True\n         return False\n\nAdds HIGH_CONFIDENCE_FLOOR = 0.9 alongside the existing CONFIDENCE_FLOOR. No default-allow. The test passes; the existing 14 review tests stay green.',
      },
      {
        role: 'user',
        content:
          'Add a regression test that covers the case where the LLM call fails entirely. Do not catch the exception silently; the test should assert the escalation path fires.',
      },
      {
        role: 'assistant',
        content:
          'Adding tests/test_analyzer.py::test_llm_failure_triggers_explicit_escalation:\n\n    def test_llm_failure_triggers_explicit_escalation(monkeypatch, sample_record):\n        def _boom(*_args, **_kwargs):\n            raise LLMUnavailable("provider 503")\n        monkeypatch.setattr(analyzer, "_call_llm", _boom)\n\n        with pytest.raises(EscalationRequired) as exc_info:\n            analyzer.analyze(sample_record)\n\n        assert exc_info.value.reason == "llm_unavailable"\n        assert exc_info.value.record_id == sample_record.id\n\nAnd the matching change in intelligence/analyzer.py: catch LLMUnavailable, raise EscalationRequired with a structured reason. No silent fallback to cached completions.',
      },
      {
        role: 'user',
        content: 'Run pytest -q and show me the output.',
      },
      {
        role: 'assistant',
        content:
          'Running. Output below.\n\n```\n$ pytest -q\n....................                                                       [100%]\n20 passed in 4.71s\n```\n\nAll three release-safety tests pass. The 17 previously-passing tests are green. No skipped tests.',
      },
      {
        role: 'user',
        content:
          'Draft the README patch describing what each of the three patches covers and what residual risk remains. Keep the language readable to a non-engineer.',
      },
      {
        role: 'assistant',
        content:
          'README delta:\n\n## Release safety patches (this PR)\n\nThree patches to close the launch-blocking failures flagged in RISKS.md:\n\n- **High-risk actions now require human review unless confidence is very high.** Previously, a medium-confidence high-risk recommendation could be auto-approved. It now routes to a human reviewer by default. (Closes R-04.)\n- **Retrieval results that cannot be grounded against source documents are excluded.** Ungrounded snippets used to flow into the summary; they are now filtered before the model sees them. (Closes R-02.)\n- **LLM provider failures now escalate explicitly.** Previously, a provider outage could silently return a cached or default response. The analyzer now raises EscalationRequired with a structured reason, so the on-call rotation handles it instead of the model.\n\n### Residual risk\n\nThe broader retrieval refactor (separating grounding from ranking) is intentionally out of scope. This PR ships the smallest patch that closes R-02; the refactor is tracked separately. Until it lands, recall on grounded retrieval is slightly lower than the pre-launch target — Slack #ai-release-safety has the regression numbers.',
      },
      {
        role: 'user',
        content: 'Name three things you would NOT ship past — even under launch pressure — given the current patch set.',
      },
      {
        role: 'assistant',
        content:
          'Three things this patch set does not authorise me to ship past:\n\n1. Removing the high-risk + medium-confidence escalation, even temporarily, to clear a backlog. The whole point of the patch is that this path is default-escalate. Disabling it for "just this batch" reverts the core safety property.\n\n2. Re-enabling silent fallback on LLM provider failure. If the provider is down, EscalationRequired must fire — operations team gets paged, users see the explicit error, the rotation handles it. We do not paper over a provider outage with cached completions.\n\n3. Shipping the broader retrieval refactor in this PR. The patch set closes R-02 with a one-line scope change because the refactor needs its own review. Bundling it would conflate two reviews and would be the wrong shape of risk for a launch-week change.',
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

// Seed data for the showcase Jobs board (`/jobs?demo=1&showcase=1`).
// Shape mirrors the recruiter `roles.list({ include_pipeline_stats: true })` payload:
// fields used by JobsPage are id, name, source, workable_job_id, job_spec_present,
// applications_count, active_candidates_count, stage_counts, tasks_count,
// description, last_candidate_activity_at.
export const JOBS_SHOWCASE = [
  {
    id: 7001,
    name: 'AI Engineer',
    description: 'Production-ready GenAI engineer who can ship under release pressure.',
    source: 'workable',
    workable_job_id: 'wkbl-71224',
    job_spec_present: true,
    job_spec_filename: 'ai-engineer-spec.md',
    applications_count: 38,
    active_candidates_count: 24,
    tasks_count: 2,
    stage_counts: { applied: 12, invited: 6, in_assessment: 4, review: 2 },
    last_candidate_activity_at: '2026-04-26T16:42:00.000Z',
    auto_reject_threshold_100: 55,
  },
  {
    id: 7002,
    name: 'Senior Data Engineer',
    description: 'Owns the recovery playbooks for our nightly Glue pipelines.',
    source: 'workable',
    workable_job_id: 'wkbl-71338',
    job_spec_present: true,
    applications_count: 19,
    active_candidates_count: 11,
    tasks_count: 1,
    stage_counts: { applied: 4, invited: 3, in_assessment: 2, review: 2 },
    last_candidate_activity_at: '2026-04-25T09:18:00.000Z',
    auto_reject_threshold_100: 60,
  },
  {
    id: 7003,
    name: 'Frontend Engineer',
    description: 'Recruiter workspace UI and reporting surfaces.',
    source: 'manual',
    workable_job_id: null,
    job_spec_present: true,
    applications_count: 12,
    active_candidates_count: 7,
    tasks_count: 1,
    stage_counts: { applied: 5, invited: 1, in_assessment: 1, review: 0 },
    last_candidate_activity_at: '2026-04-24T14:55:00.000Z',
    auto_reject_threshold_100: null,
  },
  {
    id: 7004,
    name: 'Staff Backend Engineer',
    description: 'Draft role — scoring policy not configured yet.',
    source: 'manual',
    workable_job_id: null,
    job_spec_present: false,
    applications_count: 0,
    active_candidates_count: 0,
    tasks_count: 0,
    stage_counts: { applied: 0, invited: 0, in_assessment: 0, review: 0 },
    last_candidate_activity_at: null,
    auto_reject_threshold_100: null,
  },
];

// Org payload returned alongside the roles list. Drives the Workable strip on
// JobsPage (sync status, last pull, summary counts).
export const JOBS_SHOWCASE_ORG = {
  workable_connected: true,
  workable_last_sync_at: '2026-04-27T08:12:00.000Z',
  workable_last_sync_status: 'success',
  workable_last_sync_summary: {
    new_candidates: 4,
    candidates_seen: 35,
  },
  workable_config: { sync_interval_minutes: 60 },
  workable_sync_progress: null,
  workable_sync_started_at: null,
};

// Seed data for the showcase Candidates directory (`/candidates?demo=1&showcase=1`).
// Shape mirrors `roles.listApplicationsGlobal()`: id, candidate_name, candidate_email,
// role_id, role_name, pipeline_stage, application_outcome, pre_screen_score,
// taali_score, score_summary, score_status, source, workable_*, created_at,
// pipeline_stage_updated_at.
export const CANDIDATES_DIRECTORY_SHOWCASE = [
  {
    id: 88101,
    candidate_id: 22101,
    candidate_name: 'Priya Raman',
    candidate_email: 'priya.raman@example.com',
    role_id: 7001,
    role_name: 'AI Engineer',
    pipeline_stage: 'review',
    application_outcome: 'open',
    pre_screen_score: 86,
    taali_score: 81,
    score_summary: { taali_score: 81, assessment_status: 'completed', assessment_id: 1042 },
    score_status: 'done',
    source: 'workable',
    workable_candidate_id: 'wkbl-cand-91',
    workable_stage: 'Technical review',
    workable_score_raw: 74,
    created_at: '2026-04-19T10:11:00.000Z',
    pipeline_stage_updated_at: '2026-04-21T11:08:00.000Z',
    valid_assessment_id: 1042,
    schema_version: 1,
  },
  {
    id: 88102,
    candidate_id: 22102,
    candidate_name: 'Diego Alvarez',
    candidate_email: 'diego.alvarez@example.com',
    role_id: 7001,
    role_name: 'AI Engineer',
    pipeline_stage: 'in_assessment',
    application_outcome: 'open',
    pre_screen_score: 78,
    taali_score: null,
    score_summary: { assessment_status: 'in_progress' },
    score_status: 'done',
    source: 'workable',
    workable_candidate_id: 'wkbl-cand-92',
    workable_stage: 'Assessment sent',
    workable_score_raw: 69,
    created_at: '2026-04-21T08:31:00.000Z',
    pipeline_stage_updated_at: '2026-04-26T09:50:00.000Z',
    schema_version: 1,
  },
  {
    id: 88103,
    candidate_id: 22103,
    candidate_name: 'Hannah Okonkwo',
    candidate_email: 'hannah.okonkwo@example.com',
    role_id: 7001,
    role_name: 'AI Engineer',
    pipeline_stage: 'invited',
    application_outcome: 'open',
    pre_screen_score: 72,
    taali_score: null,
    score_summary: { assessment_status: 'invited' },
    score_status: 'done',
    source: 'manual',
    created_at: '2026-04-22T14:02:00.000Z',
    pipeline_stage_updated_at: '2026-04-23T10:14:00.000Z',
    schema_version: 1,
  },
  {
    id: 88104,
    candidate_id: 22104,
    candidate_name: 'Marcus Lin',
    candidate_email: 'marcus.lin@example.com',
    role_id: 7001,
    role_name: 'AI Engineer',
    pipeline_stage: 'applied',
    application_outcome: 'open',
    pre_screen_score: 64,
    taali_score: null,
    score_summary: { assessment_status: 'pending_cv' },
    score_status: 'pending',
    source: 'workable',
    workable_candidate_id: 'wkbl-cand-94',
    workable_stage: 'Sourced',
    created_at: '2026-04-26T18:20:00.000Z',
    pipeline_stage_updated_at: '2026-04-26T18:20:00.000Z',
    schema_version: 1,
  },
  {
    id: 88105,
    candidate_id: 22105,
    candidate_name: 'Sofia Russo',
    candidate_email: 'sofia.russo@example.com',
    role_id: 7001,
    role_name: 'AI Engineer',
    pipeline_stage: 'applied',
    application_outcome: 'open',
    pre_screen_score: 59,
    taali_score: null,
    score_summary: { assessment_status: 'pending_cv' },
    score_status: 'pending',
    source: 'workable',
    workable_candidate_id: 'wkbl-cand-95',
    workable_stage: 'Sourced',
    created_at: '2026-04-26T18:32:00.000Z',
    pipeline_stage_updated_at: '2026-04-26T18:32:00.000Z',
    schema_version: 1,
  },
  {
    id: 88106,
    candidate_id: 22106,
    candidate_name: 'Theo Becker',
    candidate_email: 'theo.becker@example.com',
    role_id: 7002,
    role_name: 'Senior Data Engineer',
    pipeline_stage: 'review',
    application_outcome: 'open',
    pre_screen_score: 88,
    taali_score: 79,
    score_summary: { taali_score: 79, assessment_status: 'completed', assessment_id: 1101 },
    score_status: 'done',
    source: 'workable',
    workable_candidate_id: 'wkbl-cand-96',
    workable_stage: 'Technical review',
    workable_score_raw: 71,
    created_at: '2026-04-15T11:00:00.000Z',
    pipeline_stage_updated_at: '2026-04-22T13:18:00.000Z',
    valid_assessment_id: 1101,
    schema_version: 1,
  },
  {
    id: 88107,
    candidate_id: 22107,
    candidate_name: 'Aisha Bello',
    candidate_email: 'aisha.bello@example.com',
    role_id: 7002,
    role_name: 'Senior Data Engineer',
    pipeline_stage: 'in_assessment',
    application_outcome: 'open',
    pre_screen_score: 74,
    taali_score: null,
    score_summary: { assessment_status: 'in_progress' },
    score_status: 'done',
    source: 'manual',
    created_at: '2026-04-20T08:55:00.000Z',
    pipeline_stage_updated_at: '2026-04-25T16:30:00.000Z',
    schema_version: 1,
  },
  {
    id: 88108,
    candidate_id: 22108,
    candidate_name: 'Kenji Watanabe',
    candidate_email: 'kenji.watanabe@example.com',
    role_id: 7002,
    role_name: 'Senior Data Engineer',
    pipeline_stage: 'invited',
    application_outcome: 'open',
    pre_screen_score: 68,
    taali_score: null,
    score_summary: { assessment_status: 'invited' },
    score_status: 'done',
    source: 'workable',
    workable_candidate_id: 'wkbl-cand-98',
    workable_stage: 'Assessment sent',
    workable_score_raw: 66,
    created_at: '2026-04-23T09:42:00.000Z',
    pipeline_stage_updated_at: '2026-04-24T17:11:00.000Z',
    schema_version: 1,
  },
  {
    id: 88109,
    candidate_id: 22109,
    candidate_name: 'Elena Marković',
    candidate_email: 'elena.markovic@example.com',
    role_id: 7002,
    role_name: 'Senior Data Engineer',
    pipeline_stage: 'applied',
    application_outcome: 'open',
    pre_screen_score: 62,
    taali_score: null,
    score_summary: { assessment_status: 'applied' },
    score_status: 'done',
    source: 'manual',
    created_at: '2026-04-25T12:18:00.000Z',
    pipeline_stage_updated_at: '2026-04-25T12:18:00.000Z',
    schema_version: 1,
  },
  {
    id: 88110,
    candidate_id: 22110,
    candidate_name: 'Ravi Shah',
    candidate_email: 'ravi.shah@example.com',
    role_id: 7003,
    role_name: 'Frontend Engineer',
    pipeline_stage: 'in_assessment',
    application_outcome: 'open',
    pre_screen_score: 81,
    taali_score: null,
    score_summary: { assessment_status: 'in_progress' },
    score_status: 'done',
    source: 'manual',
    created_at: '2026-04-22T10:08:00.000Z',
    pipeline_stage_updated_at: '2026-04-26T14:00:00.000Z',
    schema_version: 1,
  },
  {
    id: 88111,
    candidate_id: 22111,
    candidate_name: 'Lila Fischer',
    candidate_email: 'lila.fischer@example.com',
    role_id: 7003,
    role_name: 'Frontend Engineer',
    pipeline_stage: 'applied',
    application_outcome: 'open',
    pre_screen_score: 70,
    taali_score: null,
    score_summary: { assessment_status: 'applied' },
    score_status: 'done',
    source: 'manual',
    created_at: '2026-04-24T15:44:00.000Z',
    pipeline_stage_updated_at: '2026-04-24T15:44:00.000Z',
    schema_version: 1,
  },
  {
    id: 88112,
    candidate_id: 22112,
    candidate_name: 'Tomás Pereira',
    candidate_email: 'tomas.pereira@example.com',
    role_id: 7003,
    role_name: 'Frontend Engineer',
    pipeline_stage: 'applied',
    application_outcome: 'open',
    pre_screen_score: 67,
    taali_score: null,
    score_summary: { assessment_status: 'applied' },
    score_status: 'done',
    source: 'manual',
    created_at: '2026-04-26T09:16:00.000Z',
    pipeline_stage_updated_at: '2026-04-26T09:16:00.000Z',
    schema_version: 1,
  },
];

// Aggregate stage counts derived from CANDIDATES_DIRECTORY_SHOWCASE — used by
// the directory's stage-filter chips. Kept in sync manually with the seed list above.
export const CANDIDATES_DIRECTORY_STAGE_COUNTS = {
  all: 12,
  applied: 6,
  invited: 2,
  in_assessment: 3,
  review: 2,
};
