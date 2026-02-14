/** Normalize API start response to assessment shape used by the assessment runtime page. */
export function normalizeStartData(startData) {
  const task = startData.task || {};
  return {
    id: startData.assessment_id,
    token: startData.token,
    starter_code: task.starter_code || '',
    duration_minutes: task.duration_minutes ?? 30,
    time_remaining:
      startData.time_remaining ?? (task.duration_minutes ?? 30) * 60,
    task_name: task.name || 'Assessment',
    description: task.description || startData.description || '',
    scenario: task.scenario || startData.scenario || '',
    repo_structure: task.repo_structure || startData.repo_structure || null,
    task,
    rubric_categories: task.rubric_categories || startData.rubric_categories || [],
    clone_command: startData.clone_command || task.clone_command || null,
    claude_budget: startData.claude_budget || null,
    claude_budget_limit_usd: task.claude_budget_limit_usd ?? null,
    is_timer_paused: Boolean(startData.is_timer_paused),
    pause_reason: startData.pause_reason || null,
  };
}

export function extractRepoFiles(repoStructure) {
  if (!repoStructure) return [];
  if (Array.isArray(repoStructure?.files)) {
    return repoStructure.files
      .map((fileEntry) => ({
        path: fileEntry.path || fileEntry.name || 'file',
        content: fileEntry.content || '',
      }))
      .filter((fileEntry) => fileEntry.path);
  }
  if (repoStructure?.files && typeof repoStructure.files === 'object') {
    return Object.entries(repoStructure.files).map(([path, content]) => ({
      path,
      content:
        typeof content === 'string'
          ? content
          : JSON.stringify(content, null, 2),
    }));
  }
  return [];
}

/** Build a tree { dirPath: [filePaths] } for repo file list. */
export function buildRepoFileTree(repoFiles) {
  const tree = { '': [] };
  for (const { path } of repoFiles) {
    const index = path.lastIndexOf('/');
    const dir = index >= 0 ? path.slice(0, index) : '';
    if (!tree[dir]) tree[dir] = [];
    tree[dir].push(path);
  }
  for (const dir of Object.keys(tree)) {
    tree[dir].sort();
  }
  return tree;
}

/** Infer language from filename for Monaco. */
export function languageFromPath(path) {
  if (!path) return 'python';
  if (/\.(py|pyw)$/i.test(path)) return 'python';
  if (/\.(js|jsx|ts|tsx|mjs|cjs)$/i.test(path)) return 'javascript';
  if (/\.(md|mdx)$/i.test(path)) return 'markdown';
  if (/\.(json)$/i.test(path)) return 'json';
  if (/\.(yaml|yml)$/i.test(path)) return 'yaml';
  if (/\.(sh|bash)$/i.test(path)) return 'shell';
  return 'plaintext';
}

export function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
}

export function formatUsd(value) {
  return typeof value === 'number' && !Number.isNaN(value)
    ? `$${value.toFixed(2)}`
    : 'N/A';
}
