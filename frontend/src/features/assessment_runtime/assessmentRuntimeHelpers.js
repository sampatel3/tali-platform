/** Normalize API start response to assessment shape used by the assessment runtime page. */
export function normalizeStartData(startData) {
  const task = startData.task || {};
  return {
    id: startData.assessment_id,
    token: startData.token,
    candidate_name: startData.candidate_name || '',
    organization_name: startData.organization_name || '',
    expires_at: startData.expires_at || null,
    invite_sent_at: startData.invite_sent_at || null,
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
    ai_mode: startData.ai_mode || task.ai_mode || 'claude_cli_terminal',
    terminal_mode: Boolean(startData.terminal_mode),
    terminal_capabilities: startData.terminal_capabilities || {},
    repo_url: startData.repo_url || null,
    branch_name: startData.branch_name || null,
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

export function normalizeRepoPathInput(path) {
  const rawPath = String(path || '').trim().replace(/\\/g, '/');
  if (!rawPath) return '';

  const normalizedParts = rawPath
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean);

  if (normalizedParts.length === 0) return '';
  if (normalizedParts.some((part) => part === '.' || part === '..')) return '';

  return normalizedParts.join('/');
}

export function upsertRepoFile(repoFiles, path, content = '') {
  const normalizedPath = normalizeRepoPathInput(path);
  if (!normalizedPath) {
    return Array.isArray(repoFiles) ? [...repoFiles] : [];
  }

  const nextFiles = Array.isArray(repoFiles)
    ? repoFiles
        .filter((fileEntry) => normalizeRepoPathInput(fileEntry?.path) !== normalizedPath)
        .map((fileEntry) => ({
          path: normalizeRepoPathInput(fileEntry?.path),
          content: String(fileEntry?.content || ''),
        }))
        .filter((fileEntry) => fileEntry.path)
    : [];

  nextFiles.push({
    path: normalizedPath,
    content: String(content || ''),
  });

  return nextFiles.sort((a, b) => a.path.localeCompare(b.path));
}

export function mergeEditorContentIntoRepoFiles(repoFiles, selectedRepoPath, editorContent) {
  const normalizedSelectedPath = normalizeRepoPathInput(selectedRepoPath);
  const normalizedFiles = Array.isArray(repoFiles)
    ? repoFiles
        .map((fileEntry) => ({
          path: normalizeRepoPathInput(fileEntry?.path),
          content: String(fileEntry?.content || ''),
        }))
        .filter((fileEntry) => fileEntry.path)
    : [];

  if (!normalizedSelectedPath) {
    return normalizedFiles.sort((a, b) => a.path.localeCompare(b.path));
  }

  return upsertRepoFile(normalizedFiles, normalizedSelectedPath, editorContent ?? '');
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

export function formatBudgetUsd(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'N/A';
  }

  const absoluteValue = Math.abs(value);
  const roundedToTwo = Number(value.toFixed(2));
  if (Math.abs(value - roundedToTwo) < 0.000005) {
    return `$${value.toFixed(2)}`;
  }

  if (absoluteValue >= 1) {
    return `$${value.toFixed(3)}`;
  }

  return `$${value.toFixed(4)}`;
}
