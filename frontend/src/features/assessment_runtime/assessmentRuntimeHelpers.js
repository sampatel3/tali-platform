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
    deliverable: task.deliverable || startData.deliverable || null,
    initial_selected_repo_path:
      startData.initial_selected_repo_path || task.initial_selected_repo_path || null,
    task,
    rubric_categories: task.rubric_categories || startData.rubric_categories || [],
    clone_command: startData.clone_command || task.clone_command || null,
    claude_budget: startData.claude_budget || null,
    claude_budget_limit_usd: task.claude_budget_limit_usd ?? null,
    is_timer_paused: Boolean(startData.is_timer_paused),
    pause_reason: startData.pause_reason || null,
    ai_mode: startData.ai_mode || task.ai_mode || 'claude_cli_terminal',
    repo_url: startData.repo_url || null,
    branch_name: startData.branch_name || null,
    allow_external_clipboard: Boolean(startData.allow_external_clipboard),
  };
}

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);

const normalizeRepoFileEntry = (fileEntry) => {
  const path = normalizeRepoPathInput(fileEntry?.path);
  if (!path) return null;

  const loaded = fileEntry?.loaded !== false;
  const content = loaded ? String(fileEntry?.content ?? '') : '';
  const originalContent = hasOwn(fileEntry, 'originalContent')
    ? fileEntry.originalContent
    : (loaded ? content : undefined);
  const syncedContent = hasOwn(fileEntry, 'syncedContent')
    ? fileEntry.syncedContent
    : (loaded ? content : undefined);
  const revision = typeof fileEntry?.revision === 'string' && fileEntry.revision
    ? fileEntry.revision
    : null;

  return {
    path,
    content,
    loaded,
    originalContent,
    syncedContent,
    revision,
    isNew: Boolean(fileEntry?.isNew),
  };
};

export function extractRepoFiles(repoStructure, { contentsLoaded = true } = {}) {
  if (!repoStructure) return [];
  if (Array.isArray(repoStructure?.files)) {
    return repoStructure.files
      .map((fileEntry) => {
        const content = typeof fileEntry?.content === 'string'
          ? fileEntry.content
          : '';
        return normalizeRepoFileEntry({
          path: fileEntry?.path || fileEntry?.name || 'file',
          content: contentsLoaded ? content : '',
          loaded: contentsLoaded,
          originalContent: contentsLoaded ? content : undefined,
          syncedContent: contentsLoaded ? content : undefined,
        });
      })
      .filter(Boolean);
  }
  if (repoStructure?.files && typeof repoStructure.files === 'object') {
    return Object.entries(repoStructure.files)
      .map(([path, rawContent]) => {
        const content = typeof rawContent === 'string'
          ? rawContent
          : JSON.stringify(rawContent, null, 2);
        return normalizeRepoFileEntry({
          path,
          content: contentsLoaded ? content : '',
          loaded: contentsLoaded,
          originalContent: contentsLoaded ? content : undefined,
          syncedContent: contentsLoaded ? content : undefined,
        });
      })
      .filter(Boolean);
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

export function upsertRepoFile(repoFiles, path, content = '', metadata = {}) {
  const normalizedPath = normalizeRepoPathInput(path);
  if (!normalizedPath) {
    return Array.isArray(repoFiles) ? [...repoFiles] : [];
  }

  const nextFiles = Array.isArray(repoFiles)
    ? repoFiles
        .filter((fileEntry) => normalizeRepoPathInput(fileEntry?.path) !== normalizedPath)
        .map(normalizeRepoFileEntry)
        .filter(Boolean)
    : [];

  const existing = Array.isArray(repoFiles)
    ? repoFiles.find((fileEntry) => normalizeRepoPathInput(fileEntry?.path) === normalizedPath)
    : null;
  const normalizedExisting = normalizeRepoFileEntry(existing);
  const nextContent = String(content ?? '');

  nextFiles.push({
    path: normalizedPath,
    content: nextContent,
    loaded: true,
    originalContent: hasOwn(metadata, 'originalContent')
      ? metadata.originalContent
      : normalizedExisting?.originalContent,
    syncedContent: hasOwn(metadata, 'syncedContent')
      ? metadata.syncedContent
      : normalizedExisting?.syncedContent,
    revision: hasOwn(metadata, 'revision')
      ? metadata.revision
      : normalizedExisting?.revision,
    isNew: hasOwn(metadata, 'isNew')
      ? Boolean(metadata.isNew)
      : (normalizedExisting?.isNew ?? true),
  });

  return nextFiles.sort((a, b) => a.path.localeCompare(b.path));
}

export function mergeEditorContentIntoRepoFiles(repoFiles, selectedRepoPath, editorContent) {
  const normalizedSelectedPath = normalizeRepoPathInput(selectedRepoPath);
  const normalizedFiles = Array.isArray(repoFiles)
    ? repoFiles
        .map(normalizeRepoFileEntry)
        .filter(Boolean)
    : [];

  if (!normalizedSelectedPath) {
    return normalizedFiles.sort((a, b) => a.path.localeCompare(b.path));
  }

  // A manifest entry with blank content is not necessarily an empty file.
  // Never let a transient blank editor buffer overwrite an entry until the
  // selected file has been fetched (or was explicitly created in-browser).
  const selectedFile = normalizedFiles.find((fileEntry) => fileEntry.path === normalizedSelectedPath);
  if (selectedFile && !selectedFile.loaded) {
    return normalizedFiles.sort((a, b) => a.path.localeCompare(b.path));
  }

  return upsertRepoFile(normalizedFiles, normalizedSelectedPath, editorContent ?? '');
}

export function hydrateRepoFile(repoFiles, path, content, revision = null) {
  return upsertRepoFile(repoFiles, path, content, {
    originalContent: String(content ?? ''),
    syncedContent: String(content ?? ''),
    revision,
    isNew: false,
  });
}

export function markRepoFileSynced(repoFiles, path, content, revision = undefined) {
  const normalizedPath = normalizeRepoPathInput(path);
  const expectedContent = String(content ?? '');
  return (Array.isArray(repoFiles) ? repoFiles : []).map((fileEntry) => {
    const normalized = normalizeRepoFileEntry(fileEntry);
    if (!normalized || normalized.path !== normalizedPath || normalized.content !== expectedContent) {
      return normalized;
    }
    return {
      ...normalized,
      syncedContent: expectedContent,
      ...(revision !== undefined ? { revision } : {}),
    };
  }).filter(Boolean);
}

export function isRepoFileModified(fileEntry) {
  const normalized = normalizeRepoFileEntry(fileEntry);
  if (!normalized?.loaded) return false;
  return normalized.isNew || normalized.content !== normalized.originalContent;
}

export function isRepoFileUnsynced(fileEntry) {
  const normalized = normalizeRepoFileEntry(fileEntry);
  if (!normalized?.loaded) return false;
  return normalized.isNew && normalized.syncedContent === undefined
    ? true
    : normalized.content !== normalized.syncedContent;
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
