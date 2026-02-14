const baseRubricCategories = [
  { category: 'task_completion', weight: 0.3 },
  { category: 'prompt_clarity', weight: 0.15 },
  { category: 'context_provision', weight: 0.15 },
  { category: 'independence_efficiency', weight: 0.15 },
  { category: 'response_utilization', weight: 0.1 },
  { category: 'debugging_design', weight: 0.1 },
  { category: 'written_communication', weight: 0.05 },
];

const demoTasks = [
  {
    id: 'backend-reliability',
    title: 'Backend API Reliability',
    description: 'Stabilize a flaky endpoint and ship a safe patch with tests.',
    durationLabel: '25 min',
    difficulty: 'Intermediate',
    availability: 'full',
    taskData: {
      task_name: 'Demo: Backend API Reliability',
      duration_minutes: 25,
      scenario: [
        'An order sync endpoint occasionally duplicates records in production.',
        'Your goal is to patch the issue, explain the root cause, and add confidence checks.',
        'Use the repository context, run code iteratively, and keep your final changes focused.',
      ].join('\n\n'),
      starter_code: [
        'def merge_order(existing, incoming):',
        '    """Merge incoming payload into an existing order record."""',
        '    if incoming.get("status"):',
        '        existing["status"] = incoming["status"]',
        '    if incoming.get("items"):',
        '        existing["items"] += incoming["items"]',
        '    return existing',
      ].join('\n'),
      language: 'python',
      repo_structure: {
        files: {
          'src/order_merge.py': [
            'def merge_order(existing, incoming):',
            '    """Merge incoming payload into an existing order record."""',
            '    if incoming.get("status"):',
            '        existing["status"] = incoming["status"]',
            '    if incoming.get("items"):',
            '        existing["items"] += incoming["items"]',
            '    return existing',
          ].join('\n'),
          'tests/test_order_merge.py': [
            'from src.order_merge import merge_order',
            '',
            'def test_merge_status():',
            '    assert merge_order({"status": "open", "items": []}, {"status": "closed"})["status"] == "closed"',
          ].join('\n'),
          'README.md': [
            '# Backend Reliability Demo',
            '',
            '- Fix duplicate item handling.',
            '- Preserve immutable fields.',
            '- Add one regression test.',
          ].join('\n'),
        },
      },
      rubric_categories: baseRubricCategories,
    },
  },
  {
    id: 'frontend-debugging',
    title: 'Frontend Bug Triage',
    description: 'Investigate an intermittent UI bug and ship a robust fix.',
    durationLabel: '20 min',
    difficulty: 'Intermediate',
    availability: 'preview',
    taskData: {
      task_name: 'Demo: Frontend Bug Triage',
      duration_minutes: 20,
      scenario: [
        'A user settings form resets values after a slow network response.',
        'Investigate state flow and ensure edits are not overwritten by stale responses.',
        'Capture tradeoffs clearly and keep the fix minimal.',
      ].join('\n\n'),
      starter_code: [
        'export function mergeRemoteSettings(localDraft, remoteData) {',
        '  return { ...localDraft, ...remoteData };',
        '}',
      ].join('\n'),
      language: 'javascript',
      repo_structure: {
        files: {
          'src/settingsMerge.js': [
            'export function mergeRemoteSettings(localDraft, remoteData) {',
            '  return { ...localDraft, ...remoteData };',
            '}',
          ].join('\n'),
          'src/hooks/useSettingsSync.js': [
            'export function shouldApplyServerPayload(lastEditedAt, payloadFetchedAt) {',
            '  return payloadFetchedAt >= lastEditedAt;',
            '}',
          ].join('\n'),
          'README.md': [
            '# Frontend Triage Demo',
            '',
            '- Keep unsaved edits intact.',
            '- Avoid stale response overwrites.',
            '- Write one guard condition test.',
          ].join('\n'),
        },
      },
      rubric_categories: baseRubricCategories,
    },
  },
  {
    id: 'data-pipeline',
    title: 'Data Pipeline Incident',
    description: 'Trace a broken transform and restore clean downstream output.',
    durationLabel: '30 min',
    difficulty: 'Advanced',
    availability: 'preview',
    taskData: {
      task_name: 'Demo: Data Pipeline Incident',
      duration_minutes: 30,
      scenario: [
        'A daily ETL run suddenly drops high-value records.',
        'Pinpoint the transformation bug, patch logic, and protect against recurrence.',
        'Explain how you validated the fix and what monitoring you would add.',
      ].join('\n\n'),
      starter_code: [
        'def normalize_record(record):',
        '    score = int(record.get("score", 0))',
        '    if score < 50:',
        '        return None',
        '    record["score"] = score',
        '    return record',
      ].join('\n'),
      language: 'python',
      repo_structure: {
        files: {
          'pipeline/transform.py': [
            'def normalize_record(record):',
            '    score = int(record.get("score", 0))',
            '    if score < 50:',
            '        return None',
            '    record["score"] = score',
            '    return record',
          ].join('\n'),
          'pipeline/tests/test_transform.py': [
            'from pipeline.transform import normalize_record',
            '',
            'def test_preserves_qualifying_rows():',
            '    assert normalize_record({"score": "65"})["score"] == 65',
          ].join('\n'),
          'README.md': [
            '# Data Incident Demo',
            '',
            '- Prevent false drops from parsing issues.',
            '- Document validation steps.',
            '- Add one edge-case test.',
          ].join('\n'),
        },
      },
      rubric_categories: baseRubricCategories,
    },
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
