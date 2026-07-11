import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { organizations as orgsApi } from '../../shared/api';
import { useJobStatus } from '../../contexts/JobStatusContext';
import { Spinner } from '../../shared/ui/TaaliPrimitives';
import { formatRelativeDateTime, SyncPulse } from '../../shared/ui/RecruiterDesignPrimitives';

// Bullhorn connect card + status + stage-map editor for the Settings page.
// Mirrors the Workable integration surface (purple design tokens, shared
// components) but with Bullhorn's CREDENTIAL connect (username / client id /
// client secret / API-user password) instead of an OAuth redirect — the backend
// runs the automated OAuth exchange server-side and uses the password once,
// in-memory. The password is a plain field the recruiter enters at connect time
// and is never stored client-side beyond the in-flight request.

const errorText = (err, fallback) =>
  err?.response?.data?.detail || err?.message || fallback;

const EMPTY_CONNECT = { username: '', client_id: '', client_secret: '', password: '' };

// Bullhorn "response type" -> the outcome semantics the reject checkbox encodes.
// Kept in-component (small, single-use) rather than a shared constant.
const REJECT_HINT = 'Treat this remote status as a rejection when it maps in.';

export const BullhornConnection = ({ orgData }) => {
  const connected = Boolean(orgData?.bullhorn_connected);
  // Surface the sync in the global BackgroundJobsPanel too (mirrors the Workable
  // sync): once we kick a run off, tell the shared job-status context to track it
  // so it stays visible even after the recruiter leaves this tab.
  const jobStatus = useJobStatus();

  const [connectForm, setConnectForm] = useState(EMPTY_CONNECT);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState('');

  const [status, setStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);

  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState('');

  const [stageMap, setStageMap] = useState(null); // { pipeline_stages, mappings, unmapped_statuses }
  const [stageRows, setStageRows] = useState([]);
  const [stageSaving, setStageSaving] = useState(false);
  const [stageError, setStageError] = useState('');
  const [stageSaved, setStageSaved] = useState(false);

  const syncInProgress = Boolean(status?.sync_in_progress);
  const unmappedCount = status?.unmapped_status_count ?? 0;

  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const res = await orgsApi.getBullhornStatus();
      setStatus(res.data || null);
    } catch (err) {
      // A 503 (flag off) or transient error just leaves the card in its
      // not-connected shell — no scary banner for an expected-off integration.
      setStatus(null);
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const refreshStageMap = useCallback(async () => {
    setStageError('');
    try {
      const res = await orgsApi.getBullhornStageMap();
      const data = res.data || {};
      setStageMap(data);
      setStageRows(Array.isArray(data.mappings) ? data.mappings.map((m) => ({ ...m })) : []);
    } catch (err) {
      setStageMap(null);
      setStageRows([]);
    }
  }, []);

  useEffect(() => {
    if (connected) {
      void refreshStatus();
      void refreshStageMap();
    }
  }, [connected, refreshStatus, refreshStageMap]);

  // Poll while a sync is running so the strip stays live (mirrors the Workable
  // sync poll). Stops as soon as the run finalizes.
  useEffect(() => {
    if (!syncInProgress) return undefined;
    const id = setInterval(() => { void refreshStatus(); }, 4000);
    return () => clearInterval(id);
  }, [syncInProgress, refreshStatus]);

  const canConnect = useMemo(
    () => Object.values(connectForm).every((v) => v.trim().length > 0),
    [connectForm],
  );

  const handleConnect = async () => {
    setConnectError('');
    if (!canConnect) {
      setConnectError('Enter the API username, client id, client secret, and API-user password.');
      return;
    }
    setConnecting(true);
    try {
      await orgsApi.connectBullhorn({
        username: connectForm.username.trim(),
        client_id: connectForm.client_id.trim(),
        client_secret: connectForm.client_secret,
        password: connectForm.password,
      });
      // Clear the credential fields immediately after a successful connect so
      // the password never lingers in component state.
      setConnectForm(EMPTY_CONNECT);
      window.location.reload();
    } catch (err) {
      setConnectError(errorText(err, 'Bullhorn connection failed.'));
    } finally {
      setConnecting(false);
    }
  };

  const handleSync = async () => {
    setSyncError('');
    setSyncing(true);
    try {
      await orgsApi.syncBullhorn();
      jobStatus?.trackBullhornSync?.();
      await refreshStatus();
    } catch (err) {
      setSyncError(errorText(err, 'Could not start the Bullhorn sync.'));
    } finally {
      setSyncing(false);
    }
  };

  const handleCancelSync = async () => {
    try {
      await orgsApi.cancelBullhornSync();
      await refreshStatus();
    } catch (err) {
      setSyncError(errorText(err, 'Could not request cancellation.'));
    }
  };

  const updateRow = (idx, patch) => {
    setStageSaved(false);
    setStageRows((rows) => rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const removeRow = (idx) => {
    setStageSaved(false);
    setStageRows((rows) => rows.filter((_, i) => i !== idx));
  };

  const addRowForStatus = (remoteStatus) => {
    setStageSaved(false);
    const firstStage = stageMap?.pipeline_stages?.[0] || 'applied';
    setStageRows((rows) => [...rows, { remote_status: remoteStatus, taali_stage: firstStage, is_reject: false }]);
  };

  const handleSaveStageMap = async () => {
    setStageError('');
    setStageSaved(false);
    setStageSaving(true);
    try {
      const mappings = stageRows
        .filter((r) => (r.remote_status || '').trim() && (r.taali_stage || '').trim())
        .map((r) => ({
          remote_status: r.remote_status.trim(),
          taali_stage: r.taali_stage.trim(),
          is_reject: Boolean(r.is_reject),
        }));
      const res = await orgsApi.replaceBullhornStageMap(mappings);
      setStageSaved(true);
      // Re-pull so the unmapped list reflects the new state.
      await refreshStageMap();
      await refreshStatus();
      if (res?.data?.unmapped_statuses) {
        setStageMap((prev) => (prev ? { ...prev, unmapped_statuses: res.data.unmapped_statuses } : prev));
      }
    } catch (err) {
      setStageError(errorText(err, 'Could not save the stage mapping.'));
    } finally {
      setStageSaving(false);
    }
  };

  const pipelineStages = stageMap?.pipeline_stages || ['applied', 'invited', 'in_assessment', 'review', 'advanced'];
  const unmappedStatuses = stageMap?.unmapped_statuses || status?.unmapped_statuses || [];

  return (
    <>
      <div className="wk-status">
        <div>
          <h4>{connected ? 'Bullhorn connected' : 'Bullhorn not connected'}</h4>
          <div className="meta">
            <span>
              <SyncPulse status={status?.last_sync_status === 'failed' ? 'error' : (orgData?.bullhorn_last_sync_at ? 'healthy' : 'stale')} />
              <span>{connected ? ' Connected' : ' Waiting for connection'}</span>
            </span>
            <span>Last sync: <b>{orgData?.bullhorn_last_sync_at ? formatRelativeDateTime(orgData.bullhorn_last_sync_at) : 'Never'}</b></span>
            <span>Events: <b>{status?.event_subscription_active ? 'subscribed' : 'not subscribed'}</b></span>
            {connected ? <span>Needs mapping: <b>{unmappedCount}</b></span> : null}
          </div>
        </div>
        <div className="settings-inline-actions">
          {connected ? (
            <button
              type="button"
              className="btn btn-purple btn-sm"
              onClick={handleSync}
              disabled={syncing || syncInProgress}
            >
              {syncing || syncInProgress ? 'Syncing...' : 'Sync now'}
            </button>
          ) : null}
        </div>
      </div>

      {!connected ? (
        <div className="settings-top-gap">
          <div className="row-form">
            <label className="field">
              <span className="k">API username</span>
              <input
                value={connectForm.username}
                onChange={(e) => setConnectForm((p) => ({ ...p, username: e.target.value }))}
                placeholder="taali.api"
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span className="k">Client ID</span>
              <input
                value={connectForm.client_id}
                onChange={(e) => setConnectForm((p) => ({ ...p, client_id: e.target.value }))}
                placeholder="OAuth client id"
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span className="k">Client secret</span>
              <input
                type="password"
                value={connectForm.client_secret}
                onChange={(e) => setConnectForm((p) => ({ ...p, client_secret: e.target.value }))}
                placeholder="OAuth client secret"
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span className="k">API-user password</span>
              <input
                type="password"
                value={connectForm.password}
                onChange={(e) => setConnectForm((p) => ({ ...p, password: e.target.value }))}
                placeholder="Used once — never stored"
                autoComplete="off"
              />
            </label>
          </div>
          <div className="settings-save-row">
            <div className="settings-inline-note">
              The API-user password is used once for the secure sign-in and is never saved.
            </div>
            <button type="button" className="btn btn-purple btn-sm" onClick={handleConnect} disabled={connecting}>
              {connecting ? 'Connecting...' : 'Connect Bullhorn'}
            </button>
          </div>
          {connectError ? (
            <div className="settings-banner warning settings-top-gap">
              <div className="settings-banner-icon">!</div>
              <div>
                <div className="settings-banner-title">Connection failed</div>
                <div className="settings-banner-copy">{connectError}</div>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {connected && syncInProgress ? (
        <div className="settings-banner warning settings-top-gap">
          <div className="settings-banner-icon"><Spinner size={16} /></div>
          <div>
            <div className="settings-banner-title">Sync running in the background</div>
            <div className="settings-banner-copy">
              {status?.sync_progress?.phase
                ? `Phase: ${String(status.sync_progress.phase).replace(/_/g, ' ')} — ${status.sync_progress.jobs_processed || 0}/${status.sync_progress.jobs_total || 0} jobs.`
                : 'We will keep this strip updated while the sync runs.'}
            </div>
          </div>
          <button type="button" className="btn btn-outline btn-sm" onClick={handleCancelSync}>
            Stop sync
          </button>
        </div>
      ) : null}

      {syncError ? (
        <div className="settings-banner warning settings-top-gap">
          <div className="settings-banner-icon">!</div>
          <div>
            <div className="settings-banner-title">Sync error</div>
            <div className="settings-banner-copy">{syncError}</div>
          </div>
        </div>
      ) : null}

      {connected ? (
        <div className="settings-top-gap">
          <div className="settings-inline-actions space-between">
            <div className="mono-label">Stage mapping</div>
            <button type="button" className="settings-link-button" onClick={() => void refreshStageMap()}>
              Refresh
            </button>
          </div>
          <p className="sub">
            Map each Bullhorn status to a Taali pipeline stage. Unmapped statuses stay at the top of the funnel until you map them.
          </p>

          {unmappedStatuses.length ? (
            <div className="settings-chip-row settings-top-gap">
              {unmappedStatuses.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="btn btn-outline btn-sm"
                  onClick={() => addRowForStatus(s)}
                  title="Add a mapping row for this unmapped status"
                >
                  + {s}
                </button>
              ))}
            </div>
          ) : null}

          <div className="bh-stage-table settings-top-gap">
            {stageRows.length === 0 ? (
              <div className="settings-inline-note">No stage mappings yet. Add one from an unmapped status above.</div>
            ) : (
              stageRows.map((row, idx) => (
                <div className="bh-stage-row" key={idx}>
                  <input
                    className="bh-stage-remote"
                    value={row.remote_status}
                    onChange={(e) => updateRow(idx, { remote_status: e.target.value })}
                    placeholder="Bullhorn status"
                  />
                  <select
                    value={row.taali_stage}
                    onChange={(e) => updateRow(idx, { taali_stage: e.target.value })}
                  >
                    {pipelineStages.map((stage) => (
                      <option key={stage} value={stage}>{stage}</option>
                    ))}
                  </select>
                  <label className="bh-stage-reject" title={REJECT_HINT}>
                    <input
                      type="checkbox"
                      checked={Boolean(row.is_reject)}
                      onChange={(e) => updateRow(idx, { is_reject: e.target.checked })}
                    />
                    <span>Reject</span>
                  </label>
                  <button type="button" className="settings-link-button" onClick={() => removeRow(idx)}>
                    Remove
                  </button>
                </div>
              ))
            )}
          </div>

          <div className="settings-save-row">
            <div className="settings-inline-note">
              {stageSaved ? 'Stage mapping saved.' : 'Changes apply to the next sync and any live status change.'}
            </div>
            <button type="button" className="btn btn-purple btn-sm" onClick={handleSaveStageMap} disabled={stageSaving}>
              {stageSaving ? 'Saving...' : 'Save stage mapping'}
            </button>
          </div>
          {stageError ? (
            <div className="settings-banner warning settings-top-gap">
              <div className="settings-banner-icon">!</div>
              <div>
                <div className="settings-banner-title">Could not save</div>
                <div className="settings-banner-copy">{stageError}</div>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
};

export default BullhornConnection;
