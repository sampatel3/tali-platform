import React, { useMemo, useState } from 'react';

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
  defaultCollapsedSections = {},
  lightMode = false,
}) => {
  const [collapsedSections, setCollapsedSections] = useState(() => ({
    contextWindow: false,
    taskContext: false,
    instructions: false,
    repoTree: false,
    ...defaultCollapsedSections,
  }));
  const [collapsedRepoDirs, setCollapsedRepoDirs] = useState({});
  const [selectedRepoPath, setSelectedRepoPath] = useState('src/revenue_recovery.py');
  const [editorContent, setEditorContent] = useState(
    PREVIEW_REPO_FILES.find((file) => file.path === 'src/revenue_recovery.py')?.content || ''
  );
  const [claudePrompt, setClaudePrompt] = useState('Ask Claude: compare retry dedupe with the failing regression test');

  const repoFileTree = useMemo(() => buildRepoFileTree(PREVIEW_REPO_FILES), []);

  const toggleSection = (key) => {
    setCollapsedSections((current) => ({
      ...current,
      [key]: !current[key],
    }));
  };

  const toggleRepoDir = (dir) => {
    setCollapsedRepoDirs((current) => ({
      ...current,
      [dir]: !current[dir],
    }));
  };

  const handleSelectRepoFile = (path) => {
    setSelectedRepoPath(path);
    const nextFile = PREVIEW_REPO_FILES.find((file) => file.path === path);
    setEditorContent(nextFile?.content || '');
  };

  return (
    <div
      className={`taali-runtime ${lightMode ? 'taali-runtime-light' : 'taali-runtime-dark'} flex ${heightClass} flex-col overflow-hidden rounded-[var(--taali-radius-panel)] border border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-bg)] text-[var(--taali-runtime-text)] shadow-[var(--taali-shadow-soft)] ${className}`}
    >
      <AssessmentTopBar
        brandName="TAALI runtime"
        taskName="Revenue Recovery Incident"
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
        collapsedSections={collapsedSections}
        toggleSection={toggleSection}
        taskContext={PREVIEW_TASK_CONTEXT}
        aiMode="claude_chat"
        cloneCommand={null}
        lightMode={lightMode}
      />

      <AssessmentWorkspace
        hasRepoStructure
        collapsedSections={collapsedSections}
        toggleSection={toggleSection}
        repoFileTree={repoFileTree}
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
        showTerminal={false}
        terminalPanelOpen={false}
        onToggleTerminal={() => {}}
        terminalConnected={false}
        terminalEvents={[]}
        onTerminalInput={() => {}}
        onTerminalResize={() => {}}
        onTerminalStop={() => {}}
        terminalStopping={false}
        output="42 rows recovered | 2 duplicates removed | schema validated"
        executing={false}
        claudeConversation={PREVIEW_CONVERSATION}
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
