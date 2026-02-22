export const STANDARD_AI_PROMPT_TEMPLATE = `Create a realistic technical assessment task.

Role and seniority:
- Role: [e.g. backend engineer, data engineer, AI engineer]
- Seniority: [junior/mid/senior/staff]

What should be tested:
- Core skills: [list]
- Real-world scenario: [brief context]
- Signals to evaluate: [problem-solving, debugging, testing, communication, AI collaboration]

Task requirements:
- Include starter Python code with realistic issues or missing logic
- Include a pytest suite with 3-6 meaningful tests
- Keep duration practical for hiring workflows
- Return structured task metadata: role fit, rubric, and suitable roles`;

export const STANDARD_MANUAL_TEMPLATE = {
  name: 'Async Data Pipeline Stabilization',
  description: 'Fix reliability issues in an async event processing service used in production. The goal is to make ingestion deterministic, handle malformed events safely, and keep processing idempotent.',
  task_type: 'debugging',
  difficulty: 'mid',
  duration_minutes: 45,
  claude_budget_limit_usd: 5,
  starter_code: `from typing import List, Dict\n\n\ndef process_events(events: List[Dict]) -> int:\n    \"\"\"Process incoming events and return number of successful writes.\"\"\"\n    processed = 0\n    for event in events:\n        # TODO: harden validation and idempotency checks\n        if event.get("id"):\n            processed += 1\n    return processed\n`,
  test_code: `from src.task import process_events\n\n\ndef test_processes_valid_events():\n    events = [{"id": "1"}, {"id": "2"}]\n    assert process_events(events) == 2\n\n\ndef test_skips_invalid_event_payload():\n    events = [{"id": "1"}, {"payload": {}}]\n    assert process_events(events) == 1\n\n\ndef test_handles_empty_input():\n    assert process_events([]) == 0\n`,
  role: 'backend_engineer',
  scenario: 'A production ingestion pipeline is dropping events and producing duplicate records during spikes. Stabilize the processing logic and keep behavior predictable.',
  task_key: '',
  repo_structure: null,
  evaluation_rubric: null,
  extra_data: {
    suitable_roles: ['backend engineer', 'platform engineer', 'full-stack engineer'],
    skills_tested: ['debugging', 'defensive coding', 'test design'],
  },
};

export const API_RELIABILITY_TEMPLATE = {
  name: 'API Reliability Regression Hunt',
  description: 'Stabilize a REST API that intermittently fails under concurrency by fixing retry logic, request validation, and error handling paths.',
  task_type: 'debugging',
  difficulty: 'senior',
  duration_minutes: 60,
  claude_budget_limit_usd: 7,
  starter_code: `from fastapi import FastAPI, HTTPException\n\napp = FastAPI()\n_store = {}\n\n@app.post('/items/{item_id}')\ndef create_item(item_id: str, payload: dict):\n    # TODO: ensure idempotency + validation + clear errors\n    _store[item_id] = payload\n    return {'ok': True}\n`,
  test_code: `from fastapi.testclient import TestClient\nfrom src.task import app\n\nclient = TestClient(app)\n\n\ndef test_create_item():\n    res = client.post('/items/a1', json={'name': 'A'})\n    assert res.status_code == 200\n\n\ndef test_invalid_payload():\n    res = client.post('/items/a2', json={})\n    assert res.status_code in (400, 422)\n`,
  role: 'backend_engineer',
  scenario: 'Production API error rates spiked after a rollout. The team needs a safe fix before peak traffic.',
  task_key: '',
  repo_structure: null,
  evaluation_rubric: null,
  extra_data: {
    suitable_roles: ['backend engineer', 'site reliability engineer'],
    skills_tested: ['api debugging', 'validation', 'error handling'],
  },
};

export const AI_AGENT_TEMPLATE = {
  name: 'Customer Support AI Agent Guardrails',
  description: 'Implement guardrails for an LLM support agent so responses are grounded in provided docs and do not hallucinate unsupported actions.',
  task_type: 'ai_engineering',
  difficulty: 'mid',
  duration_minutes: 45,
  claude_budget_limit_usd: 6,
  starter_code: `DOCS = {'refund': 'Refunds are allowed within 14 days with receipt.'}\n\n\ndef answer(question: str) -> str:\n    # TODO: enforce grounding + refusal policy\n    return 'Sure, done.'\n`,
  test_code: `from src.task import answer\n\n\ndef test_grounded_answer():\n    result = answer('What is the refund policy?')\n    assert '14 days' in result\n\n\ndef test_refuses_unknown_policy():\n    result = answer('Can you waive all late fees forever?')\n    assert 'cannot' in result.lower() or 'not available' in result.lower()\n`,
  role: 'ai_engineer',
  scenario: 'Your team is shipping an AI support agent and legal requires strict policy adherence before launch.',
  task_key: '',
  repo_structure: null,
  evaluation_rubric: null,
  extra_data: {
    suitable_roles: ['ai engineer', 'ml engineer', 'full-stack engineer'],
    skills_tested: ['prompt design', 'guardrails', 'safety'],
  },
};

export const TASK_TEMPLATES = [
  { id: 'manual', label: 'Pipeline Stabilization', task: STANDARD_MANUAL_TEMPLATE },
  { id: 'api-reliability', label: 'API Reliability', task: API_RELIABILITY_TEMPLATE },
  { id: 'ai-agent', label: 'AI Agent Guardrails', task: AI_AGENT_TEMPLATE },
];

const DEFAULT_TESTS_BY_TYPE = {
  debugging: ['Root-cause analysis', 'Bug isolation', 'Regression-safe fixes'],
  ai_engineering: ['AI prompt quality', 'Grounded tool usage', 'Output validation'],
  optimization: ['Performance reasoning', 'Tradeoff decisions', 'Instrumentation'],
  build: ['System design', 'Correct implementation', 'Testing discipline'],
  refactor: ['Code readability', 'Architecture choices', 'Behavior preservation'],
};

const prettifyRole = (role) => String(role || 'software_engineer').replace(/[_-]+/g, ' ');

export const collectSuitableRoles = (form) => {
  const fromExtra = Array.isArray(form?.extra_data?.suitable_roles)
    ? form.extra_data.suitable_roles.filter(Boolean)
    : [];
  if (fromExtra.length > 0) return fromExtra;
  if (form?.role) return [prettifyRole(form.role)];
  return ['software engineer'];
};

export const collectWhatTaskTests = (form) => {
  const fromExtra = Array.isArray(form?.extra_data?.skills_tested)
    ? form.extra_data.skills_tested.filter(Boolean)
    : [];
  if (fromExtra.length > 0) return fromExtra;

  const rubricKeys = form?.evaluation_rubric && typeof form.evaluation_rubric === 'object'
    ? Object.keys(form.evaluation_rubric)
    : [];
  if (rubricKeys.length > 0) return rubricKeys.map((k) => String(k).replace(/[_-]+/g, ' '));

  return DEFAULT_TESTS_BY_TYPE[form?.task_type] || DEFAULT_TESTS_BY_TYPE.debugging;
};

export const listRepoFiles = (form) => {
  const files = form?.repo_structure?.files;
  if (!files) return [];
  if (Array.isArray(files)) {
    return files
      .map((entry) => entry?.path || entry?.name || '')
      .filter(Boolean);
  }
  if (typeof files === 'object') return Object.keys(files);
  return [];
};

export const buildTaskJsonPreview = (form) => ({
  task_id: form.task_key || null,
  name: form.name || '',
  role: form.role || null,
  duration_minutes: form.duration_minutes,
  claude_budget_limit_usd: form.claude_budget_limit_usd ?? null,
  scenario: form.scenario || null,
  repo_structure: form.repo_structure || null,
  evaluation_rubric: form.evaluation_rubric || null,
  expected_approaches: form.extra_data?.expected_approaches || null,
  suitable_roles: form.extra_data?.suitable_roles || null,
  skills_tested: form.extra_data?.skills_tested || null,
  extra_data: form.extra_data || null,
});

export const buildTaskFormState = (initialTask) => ({
  name: initialTask?.name ?? STANDARD_MANUAL_TEMPLATE.name,
  description: initialTask?.description ?? STANDARD_MANUAL_TEMPLATE.description,
  task_type: initialTask?.task_type ?? STANDARD_MANUAL_TEMPLATE.task_type,
  difficulty: initialTask?.difficulty ?? STANDARD_MANUAL_TEMPLATE.difficulty,
  duration_minutes: initialTask?.duration_minutes ?? STANDARD_MANUAL_TEMPLATE.duration_minutes,
  claude_budget_limit_usd: initialTask?.claude_budget_limit_usd ?? STANDARD_MANUAL_TEMPLATE.claude_budget_limit_usd,
  starter_code: initialTask?.starter_code ?? STANDARD_MANUAL_TEMPLATE.starter_code,
  test_code: initialTask?.test_code ?? STANDARD_MANUAL_TEMPLATE.test_code,
  task_key: initialTask?.task_key ?? STANDARD_MANUAL_TEMPLATE.task_key,
  role: initialTask?.role ?? STANDARD_MANUAL_TEMPLATE.role,
  scenario: initialTask?.scenario ?? STANDARD_MANUAL_TEMPLATE.scenario,
  repo_structure: initialTask?.repo_structure ?? STANDARD_MANUAL_TEMPLATE.repo_structure,
  evaluation_rubric: initialTask?.evaluation_rubric ?? STANDARD_MANUAL_TEMPLATE.evaluation_rubric,
  extra_data: initialTask?.extra_data ?? STANDARD_MANUAL_TEMPLATE.extra_data,
  main_repo_path: initialTask?.main_repo_path ?? '',
  template_repo_url: initialTask?.template_repo_url ?? '',
  repo_file_count: initialTask?.repo_file_count ?? 0,
});
