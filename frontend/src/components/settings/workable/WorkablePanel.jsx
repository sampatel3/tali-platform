import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ChevronDown, Loader2 } from 'lucide-react';

import { useToast } from '../../../context/ToastContext';
import {
  canManageWorkable,
  deriveNextPullAt,
  deriveSyncHealth,
  deriveWorkableSummary,
  formatRelativeTime,
  formatUtcClock,
  normalizeWorkableSubdomain,
} from '../../../lib/workableUi';
import { organizations as organizationsApi } from '../../../shared/api';
import {
  SyncPulse,
  WorkableLogo,
} from '../../integrations/workable/WorkablePrimitives';

const DEFAULT_WORKABLE_CONFIG = {
  workflow_mode: 'manual',
  email_mode: 'manual_taali',
  score_precedence: 'workable_first',
  sync_interval_minutes: 30,
  invite_stage_name: 'Taali assessment',
  sync_model: 'scheduled_pull_only',
  sync_scope: 'open_jobs_active_candidates',
};

const resolveConfig = (orgData) => ({
  ...DEFAULT_WORKABLE_CONFIG,
  ...(orgData?.workable_config || {}),
});

const normalizeError = (error, fallback) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  return fallback;
};

const SectionHeading = ({ title, body }) => (
  <div className="mb-3">
    <h3 className="font-[var(--font-display)] text-[18px] font-semibold tracking-[-0.01em]">{title}</h3>
    <p className="mt-1 text-[13px] leading-6 text-[var(--mute)]">{body}</p>
  </div>
);

const SegmentedControl = ({ options, value, onChange }) => (
  <div className="inline-flex rounded-[10px] border border-[var(--line-2)] bg-[var(--bg-3)] p-1">
    {options.map((option) => {
      const active = option.value === value;
      return (
        <button
          key={option.value}
          type="button"
          className={`rounded-[7px] px-3 py-2 text-[12.5px] font-medium transition ${
            active
              ? 'bg-[var(--ink)] text-[var(--bg)]'
              : option.disabled
                ? 'cursor-not-allowed text-[var(--mute-2)]'
                : 'text-[var(--mute)] hover:text-[var(--ink)]'
          }`.trim()}
          onClick={() => {
            if (!option.disabled) onChange(option.value);
          }}
          title={option.title || ''}
          disabled={option.disabled}
        >
          {option.label}
        </button>
      );
    })}
  </div>
);

const ToggleCard = ({
  active = false,
  title,
  body,
  onClick,
  disabled = false,
}) => (
  <button
    type="button"
    disabled={disabled}
    onClick={onClick}
    className={`grid w-full grid-cols-[1fr_auto] gap-4 rounded-[12px] border px-4 py-4 text-left transition ${
      active
        ? 'border-[var(--purple)] bg-[color-mix(in_oklab,var(--purple)_5%,transparent)]'
        : 'border-[var(--line-2)] bg-[var(--bg-2)] hover:border-[var(--purple)]'
    } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`.trim()}
  >
    <div>
      <h4 className="text-[14px] font-semibold">{title}</h4>
      <p className="mt-1 text-[12.5px] leading-6 text-[var(--mute)]">{body}</p>
    </div>
    <span
      aria-hidden="true"
      className={`relative mt-1 inline-block h-[22px] w-[40px] rounded-full transition ${
        active ? 'bg-[var(--purple)]' : 'bg-[var(--line)]'
      }`}
    >
      <span
        className="absolute top-[2px] h-[18px] w-[18px] rounded-full bg-white shadow-[0_1px_2px_rgba(0,0,0,.15)] transition"
        style={{ left: active ? 20 : 2 }}
      />
    </span>
  </button>
);

const NumberStepper = ({ value, onChange, min = 5, max = 1440, step = 5, disabled = false }) => (
  <div className="inline-flex w-fit items-center overflow-hidden rounded-[10px] border border-[var(--line)] bg-[var(--bg-2)]">
    <button
      type="button"
      className="grid h-9 w-8 place-items-center bg-[var(--bg-3)] text-[16px] text-[var(--ink)] transition hover:bg-[var(--purple-soft)] hover:text-[var(--purple)] disabled:cursor-not-allowed disabled:opacity-40"
      onClick={() => onChange(Math.max(min, Number(value || min) - step))}
      disabled={disabled}
      aria-label="Decrease sync interval"
    >
      −
    </button>
    <input
      type="number"
      className="w-16 border-0 bg-transparent px-2 py-2 text-center font-[var(--font-mono)] text-[13px]"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
    />
    <button
      type="button"
      className="grid h-9 w-8 place-items-center bg-[var(--bg-3)] text-[16px] text-[var(--ink)] transition hover:bg-[var(--purple-soft)] hover:text-[var(--purple)] disabled:cursor-not-allowed disabled:opacity-40"
      onClick={() => onChange(Math.min(max, Number(value || min) + step))}
      disabled={disabled}
      aria-label="Increase sync interval"
    >
      +
    </button>
    <span className="pr-3 font-[var(--font-mono)] text-[11px] text-[var(--mute)]">MIN</span>
  </div>
);

const SummaryStat = ({ value, label }) => (
  <div className="stat">
    <div className="n font-[var(--font-display)] text-[22px] font-semibold tracking-[-0.01em]">{value}</div>
    <div className="l mt-0.5 font-[var(--font-mono)] text-[10px] uppercase tracking-[0.1em] text-[var(--mute)]">{label}</div>
  </div>
);

const EmptyState = ({
  tokenExpanded,
  onToggleTokenExpanded,
  tokenForm,
  onTokenFormChange,
  onOAuthConnect,
  onTokenConnect,
  busyAction,
  tokenWarning,
}) => (
  <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-7 shadow-[var(--shadow-sm)]">
    <div className="rounded-[14px] border border-[var(--line-2)] bg-[var(--bg-3)] p-5">
      <div className="grid gap-4 md:grid-cols-[auto_1fr_auto] md:items-center">
        <WorkableLogo size={44} />
        <div>
          <h3 className="text-[16px] font-semibold tracking-[-0.01em]">Connect Workable</h3>
          <p className="mt-1 text-[13px] leading-6 text-[var(--mute)]">
            Pull jobs and candidates from Workable, then push assessment notes back into the ATS when review is done.
          </p>
        </div>
        <span className="chip">DISCONNECTED</span>
      </div>
    </div>

    <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_1fr]">
      <div className="rounded-[12px] border border-[var(--line-2)] p-5">
        <div className="kicker mb-3">OAuth</div>
        <h4 className="text-[18px] font-semibold tracking-[-0.01em]">Use the Workable app flow</h4>
        <p className="mt-2 text-[13px] leading-6 text-[var(--mute)]">
          Redirect through Workable, grant scopes, and land back in Taali with the connection stored for this workspace.
        </p>
        <button
          type="button"
          className="btn btn-purple btn-sm mt-5"
          onClick={onOAuthConnect}
          disabled={busyAction === 'oauth'}
        >
          {busyAction === 'oauth' ? <Loader2 size={14} className="animate-spin" /> : null}
          Connect with Workable
        </button>
      </div>

      <div className="rounded-[12px] border border-[var(--line-2)] p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="kicker mb-2">Access token</div>
            <h4 className="text-[18px] font-semibold tracking-[-0.01em]">Use an access token instead</h4>
          </div>
          <button
            type="button"
            className="icon-btn"
            aria-label="Toggle access token form"
            onClick={onToggleTokenExpanded}
          >
            <ChevronDown size={15} className={`transition ${tokenExpanded ? 'rotate-180' : ''}`} />
          </button>
        </div>
        <p className="mt-2 text-[13px] leading-6 text-[var(--mute)]">
          Best when you already have a partner token and need a fast read-only connection.
        </p>

        {tokenExpanded ? (
          <div className="mt-5 space-y-4">
            <label className="field">
              <span className="k">Workable subdomain</span>
              <input
                value={tokenForm.subdomain}
                onChange={(event) => onTokenFormChange({ subdomain: event.target.value })}
                placeholder="deeplight-ai"
              />
            </label>
            <label className="field">
              <span className="k">Access token</span>
              <textarea
                className="min-h-[140px]"
                value={tokenForm.access_token}
                onChange={(event) => onTokenFormChange({ access_token: event.target.value })}
                placeholder="Paste the Workable API token"
              />
            </label>
            <label className="flex items-start gap-3 rounded-[12px] border border-dashed border-[var(--line)] px-4 py-4 text-[12.5px] leading-6 text-[var(--ink-2)]">
              <input
                type="checkbox"
                className="mt-1"
                checked={tokenForm.read_only}
                onChange={(event) => onTokenFormChange({ read_only: event.target.checked })}
              />
              <span>
                Default to <b>read-only</b>. Uncheck this only if you want Taali to write back notes and stage updates to Workable.
              </span>
            </label>
            {tokenWarning ? (
              <div className="rounded-[12px] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] px-4 py-3 text-[12.5px] text-[var(--taali-warning)]">
                {tokenWarning}
              </div>
            ) : null}
            <button
              type="button"
              className="btn btn-outline btn-sm"
              onClick={onTokenConnect}
              disabled={busyAction === 'token'}
            >
              {busyAction === 'token' ? <Loader2 size={14} className="animate-spin" /> : null}
              Connect token
            </button>
          </div>
        ) : null}
      </div>
    </div>
  </div>
);

export const WorkablePanel = ({
  orgData,
  onOrgDataChange,
  active = true,
  currentUser = null,
}) => {
  const { showToast } = useToast();
  const [config, setConfig] = useState(() => resolveConfig(orgData));
  const [syncStatus, setSyncStatus] = useState(null);
  const [busyAction, setBusyAction] = useState('');
  const [statusError, setStatusError] = useState('');
  const [tokenExpanded, setTokenExpanded] = useState(false);
  const [tokenForm, setTokenForm] = useState({
    access_token: '',
    subdomain: '',
    read_only: true,
  });
  const inviteStageTimer = useRef(null);

  const manageable = canManageWorkable(currentUser);

  useEffect(() => {
    setConfig(resolveConfig(orgData));
  }, [orgData]);

  const refreshOrg = async () => {
    const response = await organizationsApi.get();
    onOrgDataChange(response?.data || null);
    return response?.data || null;
  };

  const refreshStatus = async (runId = null) => {
    try {
      const response = await organizationsApi.getWorkableStatus(runId);
      setSyncStatus(response?.data || null);
      setStatusError('');
      return response?.data || null;
    } catch (error) {
      setStatusError(normalizeError(error, 'Failed to load Workable sync status.'));
      return null;
    }
  };

  useEffect(() => {
    if (!active || !orgData?.id || !manageable) return undefined;
    void refreshStatus();
    return undefined;
  }, [active, manageable, orgData?.id]);

  useEffect(() => {
    const inProgress = Boolean(syncStatus?.sync_in_progress);
    if (!active || !manageable || !inProgress) return undefined;

    const interval = window.setInterval(async () => {
      const nextStatus = await refreshStatus(syncStatus?.run_id || null);
      if (nextStatus && !nextStatus.sync_in_progress) {
        await refreshOrg();
      }
    }, 3000);

    return () => window.clearInterval(interval);
  }, [active, manageable, syncStatus?.run_id, syncStatus?.sync_in_progress]);

  useEffect(() => () => {
    if (inviteStageTimer.current) window.clearTimeout(inviteStageTimer.current);
  }, []);

  const persistConfig = async (updates, options = {}) => {
    const nextConfig = {
      ...resolveConfig(orgData),
      ...config,
      ...updates,
    };
    setConfig(nextConfig);

    try {
      const response = await organizationsApi.update({ workable_config: nextConfig });
      const nextOrg = response?.data || null;
      onOrgDataChange(nextOrg);
      setConfig(resolveConfig(nextOrg));
      if (!options.silent) showToast(options.successMessage || 'Workable settings updated.', 'success');
      return nextOrg;
    } catch (error) {
      setConfig(resolveConfig(orgData));
      showToast(normalizeError(error, options.errorMessage || 'Failed to update Workable settings.'), 'error');
      return null;
    }
  };

  const handleInviteStageChange = (value) => {
    setConfig((current) => ({ ...current, invite_stage_name: value }));
    if (inviteStageTimer.current) window.clearTimeout(inviteStageTimer.current);
    inviteStageTimer.current = window.setTimeout(() => {
      void persistConfig({ invite_stage_name: value }, { silent: true });
    }, 500);
  };

  const handleSyncIntervalChange = async (value) => {
    const clamped = Math.max(5, Math.min(1440, Number(value || DEFAULT_WORKABLE_CONFIG.sync_interval_minutes)));
    await persistConfig({ sync_interval_minutes: clamped });
  };

  const handleWorkflowModeChange = async (workflowMode) => {
    await persistConfig({ workflow_mode: workflowMode });
  };

  const handleEmailModeChange = async (emailMode) => {
    await persistConfig({ email_mode: emailMode });
  };

  const handleOauthConnect = async () => {
    setBusyAction('oauth');
    try {
      const response = await organizationsApi.getWorkableAuthorizeUrl();
      const url = response?.data?.url || response?.data?.authorize_url;
      if (!url) {
        throw new Error('Workable authorize URL is unavailable.');
      }
      window.location.href = url;
    } catch (error) {
      showToast(normalizeError(error, 'Failed to start Workable OAuth.'), 'error');
      setBusyAction('');
    }
  };

  const handleTokenConnect = async () => {
    if (!tokenForm.subdomain.trim() || !tokenForm.access_token.trim()) {
      showToast('Add the Workable subdomain and access token first.', 'error');
      return;
    }
    setBusyAction('token');
    try {
      await organizationsApi.connectWorkableToken({
        access_token: tokenForm.access_token.trim(),
        subdomain: tokenForm.subdomain.trim(),
        read_only: tokenForm.read_only,
      });
      await refreshOrg();
      await refreshStatus();
      setTokenForm({ access_token: '', subdomain: '', read_only: true });
      setTokenExpanded(false);
      showToast('Workable connected.', 'success');
    } catch (error) {
      showToast(normalizeError(error, 'Failed to connect Workable token.'), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const handleStartSync = async () => {
    setBusyAction('sync');
    try {
      const response = await organizationsApi.triggerWorkableSync();
      await refreshStatus(response?.data?.run_id || null);
      showToast('Workable sync started.', 'success');
    } catch (error) {
      showToast(normalizeError(error, 'Failed to start Workable sync.'), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const handleCancelSync = async () => {
    setBusyAction('cancel');
    try {
      await organizationsApi.cancelWorkableSync(syncStatus?.run_id || null);
      await refreshStatus(syncStatus?.run_id || null);
      await refreshOrg();
      showToast('Workable sync cancellation requested.', 'success');
    } catch (error) {
      showToast(normalizeError(error, 'Failed to cancel Workable sync.'), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm('Disconnect Workable for this workspace? Synced records will remain until you clear them.')) return;
    setBusyAction('disconnect');
    try {
      await organizationsApi.disconnectWorkable();
      await refreshOrg();
      setSyncStatus(null);
      showToast('Workable disconnected.', 'success');
    } catch (error) {
      showToast(normalizeError(error, 'Failed to disconnect Workable.'), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const handleClearData = async () => {
    if (!window.confirm('Clear all synced Workable records from Taali? This only affects imported data in this workspace.')) return;
    setBusyAction('clear');
    try {
      await organizationsApi.clearWorkableData();
      await refreshStatus(syncStatus?.run_id || null);
      await refreshOrg();
      showToast('Cleared synced Workable data.', 'success');
    } catch (error) {
      showToast(normalizeError(error, 'Failed to clear Workable data.'), 'error');
    } finally {
      setBusyAction('');
    }
  };

  const summary = useMemo(
    () => deriveWorkableSummary({ org: orgData, syncStatus }),
    [orgData, syncStatus],
  );
  const lastSyncAt = syncStatus?.workable_last_sync_at || orgData?.workable_last_sync_at;
  const nextPullAt = deriveNextPullAt(lastSyncAt, config.sync_interval_minutes);
  const syncHealth = deriveSyncHealth({
    lastSyncStatus: syncStatus?.workable_last_sync_status || orgData?.workable_last_sync_status,
    syncInProgress: syncStatus?.sync_in_progress,
    lastSyncAt,
    errors: syncStatus?.errors || syncStatus?.workable_last_sync_summary?.errors || [],
  });
  const tokenMode = config.email_mode === 'workable_preferred_fallback_manual' ? 'read/write' : 'read-only';
  const lastSyncClock = formatUtcClock(lastSyncAt);
  const lastSyncDuration = summary.duration;
  const tokenWarning = tokenForm.read_only
    ? ''
    : 'Write access lets Taali post notes and stage updates back to Workable. Double-check token scope before connecting.';

  if (!manageable) {
    return (
      <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--bg-2)] p-6 shadow-[var(--shadow-sm)]">
        <div className="flex items-start gap-3 text-[13px] text-[var(--mute)]">
          <AlertTriangle size={16} className="mt-0.5 text-[var(--amber)]" />
          Only workspace admins and owners can manage the Workable integration.
        </div>
      </div>
    );
  }

  if (!orgData?.workable_connected) {
    return (
      <EmptyState
        tokenExpanded={tokenExpanded}
        onToggleTokenExpanded={() => setTokenExpanded((value) => !value)}
        tokenForm={tokenForm}
        onTokenFormChange={(updates) => setTokenForm((current) => ({ ...current, ...updates }))}
        onOAuthConnect={handleOauthConnect}
        onTokenConnect={handleTokenConnect}
        busyAction={busyAction}
        tokenWarning={tokenWarning}
      />
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-[var(--font-display)] text-[28px] font-semibold tracking-[-0.02em]">Workable</h2>
          <p className="mt-1 text-[13.5px] text-[var(--mute)]">
            Pull jobs and candidates from Workable. Push assessment scores and invite candidates from inside Taali.
          </p>
        </div>
        <span className="chip green"><span className="dot" />CONNECTED</span>
      </div>

      <div className="rounded-[14px] border border-[var(--line-2)] bg-[var(--bg-3)] p-4">
        <div className="grid gap-4 md:grid-cols-[auto_1fr_auto] md:items-center">
          <WorkableLogo size={44} />
          <div>
            <h4 className="text-[14.5px] font-semibold">{normalizeWorkableSubdomain(orgData.workable_subdomain)}</h4>
            <div className="mt-1 flex flex-wrap gap-3 font-[var(--font-mono)] text-[11.5px] text-[var(--mute)]">
              <span className="inline-flex items-center gap-2"><SyncPulse status={syncHealth} />{syncHealth === 'error' ? 'Error' : syncHealth === 'stale' ? 'Stale' : 'Healthy'}</span>
              <span>Last sync: <b className="font-medium text-[var(--ink-2)]">{lastSyncAt ? formatRelativeTime(lastSyncAt) : 'Never'}</b></span>
              <span>Token: <b className="font-medium text-[var(--ink-2)]">{tokenMode}</b></span>
              <span>Next pull {nextPullAt ? formatRelativeTime(nextPullAt) : 'scheduled after first sync'}</span>
            </div>
          </div>
          <div className="row justify-end">
            <button type="button" className="btn btn-outline btn-sm" onClick={handleStartSync} disabled={busyAction === 'sync'}>
              {busyAction === 'sync' ? <Loader2 size={14} className="animate-spin" /> : null}
              Sync now
            </button>
            <button
              type="button"
              className="btn btn-outline btn-sm text-[var(--red)]"
              style={{ borderColor: 'color-mix(in oklab, var(--red) 30%, var(--line))' }}
              onClick={handleDisconnect}
              disabled={busyAction === 'disconnect'}
            >
              {busyAction === 'disconnect' ? <Loader2 size={14} className="animate-spin" /> : null}
              Disconnect
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-[12px] border border-[var(--line-2)] bg-[var(--bg-3)] p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="font-[var(--font-mono)] text-[11px] uppercase tracking-[0.1em] text-[var(--mute)]">
            Last sync · {lastSyncClock}{lastSyncDuration ? ` · ${lastSyncDuration}` : ''}
          </div>
          <button type="button" className="text-[12.5px] text-[var(--purple)]" onClick={() => void refreshStatus(syncStatus?.run_id || null)}>
            View sync log →
          </button>
        </div>
        <div className="grid gap-4 md:grid-cols-4">
          <SummaryStat value={summary.openJobs} label="Open jobs" />
          <SummaryStat value={summary.activeCandidates} label="Active candidates" />
          <SummaryStat value={`+${summary.newCandidates}`} label="New since last sync" />
          <SummaryStat value={summary.errors} label="Errors" />
        </div>
      </div>

      <div>
        <SectionHeading
          title="Workflow mode"
          body="How Taali behaves once a candidate is synced from Workable."
        />
        <div className="grid gap-3 lg:grid-cols-2">
          <ToggleCard
            active={config.workflow_mode === 'workable_hybrid'}
            title="Workable hybrid"
            body="Jobs and candidates flow in from Workable. Taali invites, scores, and writes results back as private notes on the Workable profile."
            onClick={() => void handleWorkflowModeChange('workable_hybrid')}
          />
          <ToggleCard
            active={config.workflow_mode === 'manual'}
            title="Manual"
            body="Roles and candidates are managed only in Taali. Workable is a read-only mirror - nothing flows back."
            onClick={() => void handleWorkflowModeChange('manual')}
          />
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <SectionHeading
            title="Email delivery"
            body="When in doubt, send invites through Workable's templated emails so your ATS owns the candidate-facing thread."
          />
          <SegmentedControl
            value={config.email_mode}
            onChange={(value) => void handleEmailModeChange(value)}
            options={[
              { value: 'manual_taali', label: 'Taali only' },
              { value: 'workable_preferred_fallback_manual', label: 'Workable preferred · Taali fallback' },
            ]}
          />
        </div>

        <div>
          <SectionHeading
            title="Score precedence"
            body="Which score appears on the candidate row when both Workable and Taali have scored the candidate."
          />
          <SegmentedControl
            value={config.score_precedence}
            onChange={() => {}}
            options={[
              { value: 'workable_first', label: 'Workable score wins' },
              { value: 'taali_first', label: 'Taali score wins', disabled: true, title: 'Coming soon' },
            ]}
          />
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <label className="field">
          <span className="k">Sync interval</span>
          <NumberStepper
            value={config.sync_interval_minutes}
            onChange={(value) => void handleSyncIntervalChange(value)}
          />
          <span className="mt-1 block text-[11.5px] text-[var(--mute)]">5 min - 24 h. Scheduled pull only.</span>
        </label>

        <label className="field">
          <span className="k">Invite-stage in Workable</span>
          <input
            value={config.invite_stage_name}
            onChange={(event) => handleInviteStageChange(event.target.value)}
          />
          <span className="mt-1 block text-[11.5px] text-[var(--mute)]">
            When a candidate enters this stage in Workable, Taali sends them the assessment.
          </span>
        </label>
      </div>

      <div className="rounded-[12px] border border-[var(--line-2)] bg-[var(--bg-3)] p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h4 className="text-[14px] font-semibold">Sync scope</h4>
            <p className="mt-1 text-[12.5px] leading-6 text-[var(--mute)]">
              Open jobs · active candidates only. Archived jobs and rejected candidates are skipped to keep your workspace lean.
            </p>
          </div>
          <span className="chip">FIXED</span>
        </div>
      </div>

      <div className="row">
        <button type="button" className="btn btn-outline btn-sm opacity-60" disabled title="Coming soon">
          Pause sync
        </button>
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={handleCancelSync}
          disabled={!syncStatus?.sync_in_progress || busyAction === 'cancel'}
        >
          {busyAction === 'cancel' ? <Loader2 size={14} className="animate-spin" /> : null}
          Cancel running sync
        </button>
        <button
          type="button"
          className="btn btn-outline btn-sm text-[var(--red)]"
          style={{ borderColor: 'color-mix(in oklab, var(--red) 30%, var(--line))' }}
          onClick={handleClearData}
          disabled={busyAction === 'clear'}
        >
          {busyAction === 'clear' ? <Loader2 size={14} className="animate-spin" /> : null}
          Clear all synced data
        </button>
      </div>

      {statusError ? (
        <div className="rounded-[12px] border border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] px-4 py-3 text-[12.5px] text-[var(--taali-warning)]">
          {statusError}
        </div>
      ) : null}
    </div>
  );
};

export default WorkablePanel;
