import React, { useEffect, useMemo, useState } from 'react';

import { AssessmentContextWindow } from './AssessmentContextWindow';
import { AssessmentTopBar } from './AssessmentTopBar';
import { AssessmentWorkspace } from './AssessmentWorkspace';
import { buildRepoFileTree, formatTime, formatUsd, languageFromPath } from './assessmentRuntimeHelpers';

const PREVIEW_REPO_FILES = [
  {
    path: 'README.md',
    content: '# Revenue Recovery Incident\n\nRestore the batch recovery flow before finance close.',
  },
  {
    path: 'incident_notes.md',
    content: '- Schema drift started after the upstream posted_at migration.\n- Retry batches now inflate totals.\n- Finance needs a credible rollback and verification path.',
  },
  {
    path: 'src/revenue_recovery.py',
    content: [
      'def reconcile_batch(records, schema_version):',
      '    required = {"transaction_id", "amount_cents", "posted_at"}',
      '    missing = required.difference(records.columns)',
      '    if missing:',
      '        raise ValueError(f"Schema drift: {sorted(missing)}")',
      '',
      '    deduped = records.sort_values("posted_at").drop_duplicates(',
      '        subset=["transaction_id"], keep="last"',
      '    )',
      '',
      '    invalid_rows = deduped[deduped["amount_cents"] < 0]',
      '    if not invalid_rows.empty:',
      '        raise ValueError("Negative amount in recovery batch")',
      '',
      '    return deduped',
    ].join('\n'),
  },
  {
    path: 'src/validators.py',
    content: [
      'def validate_retry_ids(df):',
      '    duplicates = df["transaction_id"].duplicated(keep=False)',
      '    return df[duplicates]',
    ].join('\n'),
  },
  {
    path: 'tests/test_revenue_recovery.py',
    content: [
      'def test_dedupes_retries_keep_latest_posted_at():',
      '    ...',
    ].join('\n'),
  },
];

const PREVIEW_TASK_CONTEXT = [
  'Finance close is tomorrow morning and the Glue revenue-recovery job has been unstable for days.',
  'The current failures combine schema drift, duplicate retries inflating totals, and audit uncertainty about which runs are trustworthy.',
  'Use the repo to stabilize the flow, validate the dedupe path, and explain what remains risky.',
].join('\n\n');

const PREVIEW_CONVERSATION = [
  {
    role: 'user',
    content: 'The Glue job is double-counting retries. Help me validate the dedupe logic before I change the loader.',
  },
  {
    role: 'assistant',
    content: 'Start with transaction_id uniqueness after retry merges, then verify the posted_at schema-change path before you patch downstream aggregation.',
  },
];

export const AssessmentRuntimePreviewView = ({
  className = '',
  heightClass = 'h-[44rem]',
  lightMode = false,
  taskName,
  taskContext,
  taskRole,
  repoFiles = PREVIEW_REPO_FILES,
  initialSelectedRepoPath = 'src/revenue_recovery.py',
  claudeConversation = PREVIEW_CONVERSATION,
  initialClaudePrompt = 'Ask Claude: compare retry dedupe with the failing regression test',
  output = '42 rows recovered | 2 duplicates removed | schema validated',
  showTerminal = true,
}) => {
  const previewRepoFiles = useMemo(
    () => (Array.isArray(repoFiles) && repoFiles.length > 0 ? repoFiles : PREVIEW_REPO_FILES),
    [repoFiles],
  );
  const [collapsedRepoDirs, setCollapsedRepoDirs] = useState({});
  const [selectedRepoPath, setSelectedRepoPath] = useState(
    previewRepoFiles.find((file) => file.path === initialSelectedRepoPath)?.path
      || previewRepoFiles[0]?.path
      || initialSelectedRepoPath
  );
  const [editorContent, setEditorContent] = useState(
    previewRepoFiles.find((file) => file.path === initialSelectedRepoPath)?.content
      || previewRepoFiles[0]?.content
      || ''
  );
  const [claudePrompt, setClaudePrompt] = useState(initialClaudePrompt);
  const [repoPanelCollapsed, setRepoPanelCollapsed] = useState(false);
  const [assistantPanelCollapsed, setAssistantPanelCollapsed] = useState(false);
  const [terminalPanelOpen, setTerminalPanelOpen] = useState(true);
  const [outputPanelOpen, setOutputPanelOpen] = useState(true);

  useEffect(() => {
    const fallbackPath = previewRepoFiles.find((file) => file.path === initialSelectedRepoPath)?.path
      || previewRepoFiles[0]?.path
      || null;
    if (!fallbackPath) {
      setSelectedRepoPath(initialSelectedRepoPath);
      setEditorContent('');
      return;
    }

    if (!previewRepoFiles.some((file) => file.path === selectedRepoPath)) {
      setSelectedRepoPath(fallbackPath);
      setEditorContent(
        previewRepoFiles.find((file) => file.path === fallbackPath)?.content || ''
      );
    }
  }, [initialSelectedRepoPath, previewRepoFiles, selectedRepoPath]);

  const repoFileTree = useMemo(() => buildRepoFileTree(previewRepoFiles), [previewRepoFiles]);

  const toggleRepoDir = (dir) => {
    setCollapsedRepoDirs((current) => ({
      ...current,
      [dir]: !current[dir],
    }));
  };

  const handleSelectRepoFile = (path) => {
    setSelectedRepoPath(path);
    const nextFile = previewRepoFiles.find((file) => file.path === path);
    setEditorContent(nextFile?.content || '');
  };

  return (
    <div
      className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex ${heightClass} flex-col overflow-hidden rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] text-[var(--taali-runtime-text)] shadow-[var(--taali-shadow-soft)] ${className}`}
    >
      <AssessmentTopBar
        taskName={taskName || 'Revenue Recovery Incident'}
        metaLine={taskRole || 'Preview workspace'}
        claudeBudget={{
          enabled: true,
          remaining_usd: 6.2,
          limit_usd: 12.0,
        }}
        aiMode="claude_chat"
        terminalCapabilities={{}}
        formatUsd={formatUsd}
        isTimeLow={false}
        timeUrgencyLevel="normal"
        timeLeft={27 * 60 + 14}
        formatTime={formatTime}
        isTimerPaused={false}
        lightMode={lightMode}
        onToggleTheme={() => {}}
        onSubmit={() => {}}
      />

      <AssessmentContextWindow
        taskName={taskName || 'Assessment workspace'}
        taskRole={taskRole || 'Preview workspace'}
        taskContext={taskContext || PREVIEW_TASK_CONTEXT}
        repoFiles={previewRepoFiles}
        cloneCommand={null}
      />

      <AssessmentWorkspace
        hasRepoStructure
        repoFileTree={repoFileTree}
        repoPanelCollapsed={repoPanelCollapsed}
        onToggleRepoPanel={() => setRepoPanelCollapsed((current) => !current)}
        collapsedRepoDirs={collapsedRepoDirs}
        toggleRepoDir={toggleRepoDir}
        selectedRepoPath={selectedRepoPath}
        onSelectRepoFile={handleSelectRepoFile}
        assessmentStarterCode={editorContent}
        editorContent={editorContent}
        onEditorChange={setEditorContent}
        onExecute={() => {}}
        onSave={() => {}}
        editorLanguage={languageFromPath(selectedRepoPath)}
        editorFilename={selectedRepoPath}
        isTimerPaused={false}
        showTerminal={showTerminal}
        assistantPanelCollapsed={assistantPanelCollapsed}
        onToggleAssistantPanel={() => setAssistantPanelCollapsed((current) => !current)}
        terminalPanelOpen={terminalPanelOpen}
        onToggleTerminal={() => setTerminalPanelOpen((current) => !current)}
        outputPanelOpen={outputPanelOpen}
        onToggleOutput={() => setOutputPanelOpen((current) => !current)}
        terminalConnected={false}
        terminalEvents={[]}
        onTerminalInput={() => {}}
        onTerminalResize={() => {}}
        onRestartTerminal={() => {}}
        terminalRestarting={false}
        output={output}
        executing={false}
        claudeConversation={claudeConversation}
        claudePrompt={claudePrompt}
        onClaudePromptChange={setClaudePrompt}
        onClaudePromptSubmit={() => {}}
        claudePromptSending={false}
        claudePromptDisabled={false}
        lightMode={lightMode}
      />
    </div>
  );
};
