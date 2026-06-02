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

// Seeded candidate↔Claude transcript for the static marketing snapshot.
// Shape matches `assessment.ai_prompts` ({ message, response }) so it flows
// through AssessmentClaudeChat's `hydrateMessagesFromAiPrompts` exactly like
// a real session — no special preview code path. Reads as a strong candidate
// steering the agent: verify the dedupe before editing, then scope the
// smallest safe fix. The exchange ties directly to the code open in the
// editor (reconcile_batch in src/revenue_recovery.py).
const PREVIEW_AI_PROMPTS = [
  {
    message:
      'Before I touch the loader — does reconcile_batch actually keep the latest row per transaction_id? Finance thinks retries are inflating the totals.',
    response:
      'The dedupe itself is sound: it sorts by `posted_at`, then `drop_duplicates(subset=["transaction_id"], keep="last")`, so the most recent retry wins. The inflation isn\'t coming from here.\n\nTwo things worth checking before you change anything:\n\n1. The schema-drift guard only checks that columns are *present*, not their dtype. If `posted_at` arrives as a string, the sort goes lexical and "latest" can pick the wrong row.\n2. Nothing dedupes *across* batch boundaries — a retry that lands in the next run still double-counts.\n\nWant me to show where a string `posted_at` would break the sort?',
  },
  {
    message:
      "Good catch on the dtype. What's the smallest safe change so a few negative-amount rows don't fail the whole batch?",
    response:
      'Right now a single negative `amount_cents` raises and aborts the entire run. The minimal, auditable fix is to **quarantine instead of abort**: split into `clean` and `rejected`, return `clean`, and write `rejected` to a side table finance can review.\n\nThat keeps the run idempotent and gives you the rollback path the incident notes ask for. I\'d lock it with one regression test that feeds a mixed batch and asserts the clean count *and* that the rejects are captured, not silently dropped.',
  },
];

// Session budget for the static snapshot. Drives the chat token tracker
// ("21k tokens · $0.42") and the top-bar Claude budget chip; the two stay
// consistent (used 0.42 + remaining 11.58 = 12.00 limit).
const PREVIEW_CLAUDE_BUDGET = {
  enabled: true,
  is_exhausted: false,
  remaining_usd: 11.58,
  limit_usd: 12.0,
  used_usd: 0.42,
  tokens_used: 21300,
};

export const AssessmentRuntimePreviewView = ({
  className = '',
  heightClass = 'h-[44rem]',
  lightMode = false,
  // Static marketing snapshot: render the real workspace fully populated
  // (editor open, chat transcript seeded, brief collapsed) but inert — no
  // pointer events, no focus, aria-hidden — so the landing page shows what
  // a live candidate session looks like without inviting interaction.
  staticPreview = false,
  taskName,
  taskContext,
  taskRole,
  repoFiles = PREVIEW_REPO_FILES,
  initialSelectedRepoPath = 'src/revenue_recovery.py',
  claudeConversation = PREVIEW_CONVERSATION,
  initialAiPrompts = PREVIEW_AI_PROMPTS,
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
      className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex ${heightClass} flex-col overflow-hidden rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] text-[var(--taali-runtime-text)] shadow-[var(--taali-shadow-soft)] ${staticPreview ? 'pointer-events-none select-none' : ''} ${className}`.trim()}
      aria-hidden={staticPreview ? 'true' : undefined}
    >
      <AssessmentTopBar
        taskName={taskName || 'Revenue Recovery Incident'}
        metaLine={taskRole || 'Preview workspace'}
        claudeBudget={PREVIEW_CLAUDE_BUDGET}
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

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {/* Mirror the live runtime's content shell (see AssessmentPageContent):
            same padding and a mt-4 gap between the brief and the workspace so
            the cards sit identically. The real page's mx-auto max-w-[90rem]
            cap is intentionally dropped here — the snapshot renders into a
            fixed, narrower marketing band, so capping would just gutter the
            workspace and squeeze the editor; letting it fill gives the editor
            the same generous width it has on a real full-screen session. */}
        <div className="w-full px-4 py-4 lg:px-8 lg:py-5">
        <AssessmentContextWindow
          defaultExpanded={!staticPreview}
          taskName={taskName || 'Assessment workspace'}
          taskRole={taskRole || 'Preview workspace'}
          taskContext={taskContext || PREVIEW_TASK_CONTEXT}
          repoFiles={previewRepoFiles}
          cloneCommand={null}
        />

        <AssessmentWorkspace
        className="mt-4"
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
        // Agentic-chat path (the live default): reveal the editor on the
        // right by passing selectedFilePath, seed the candidate↔Claude
        // transcript via initialAiPrompts (same shape as assessment.ai_prompts),
        // and feed the session budget so the chat token tracker shows usage.
        // assessmentId/token stay null so the chat can never actually send.
        assessmentId={null}
        assessmentToken={null}
        selectedFilePath={selectedRepoPath}
        codeContext={editorContent}
        claudeBudget={PREVIEW_CLAUDE_BUDGET}
        onClaudeBudgetUpdate={() => {}}
        initialAiPrompts={initialAiPrompts}
        staticAssistantPanelWidth={staticPreview ? 450 : null}
        lightMode={lightMode}
        />
        </div>
      </div>
    </div>
  );
};
