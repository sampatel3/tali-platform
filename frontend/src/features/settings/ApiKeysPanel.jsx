import React, { useEffect, useState } from 'react';

import { apiKeys as apiKeysApi } from '../../shared/api';
import { useToast } from '../../context/ToastContext';
import { getErrorMessage } from '../../shared/getErrorMessage';

// Read-only defaults when minting a key without touching the scope list.
const READ_ONLY_DEFAULTS = ['roles:read', 'applications:read', 'assessments:read'];

const fmtDate = (value) => {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleDateString();
  } catch {
    return '—';
  }
};

const S = {
  scopes: { display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 6 },
  scope: { display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 },
  secret: {
    marginTop: 14,
    padding: 16,
    borderRadius: 14,
    border: '1px solid var(--purple)',
    background: 'var(--purple-soft)',
  },
  secretValue: {
    display: 'block',
    margin: '10px 0',
    padding: 12,
    borderRadius: 10,
    background: 'var(--bg)',
    color: 'var(--ink)',
    fontFamily: 'var(--font-mono)',
    fontSize: 13,
    wordBreak: 'break-all',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '1.4fr 1fr 1.6fr 0.8fr 0.7fr auto',
    gap: 12,
    alignItems: 'center',
    padding: '12px 0',
    borderTop: '1px solid var(--line)',
    fontSize: 13,
  },
  head: { fontSize: 11, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--mute)' },
  chip: {
    display: 'inline-block',
    padding: '2px 8px',
    margin: '2px 4px 2px 0',
    borderRadius: 999,
    fontSize: 11,
    fontFamily: 'var(--font-mono)',
    background: 'var(--purple-soft)',
    color: 'var(--purple)',
  },
  mono: { fontFamily: 'var(--font-mono)', color: 'var(--mute)' },
};

export const ApiKeysPanel = () => {
  const { showToast } = useToast();
  const [keys, setKeys] = useState([]);
  const [availableScopes, setAvailableScopes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [selectedScopes, setSelectedScopes] = useState(READ_ONLY_DEFAULTS);
  const [isTest, setIsTest] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newSecret, setNewSecret] = useState(null);

  const loadKeys = async () => {
    try {
      const res = await apiKeysApi.list();
      const data = res?.data || {};
      setKeys(Array.isArray(data.keys) ? data.keys : []);
      const scopes = Array.isArray(data.available_scopes) ? data.available_scopes : [];
      setAvailableScopes(scopes);
      setSelectedScopes((prev) => {
        const valid = prev.filter((s) => scopes.includes(s));
        return valid.length ? valid : READ_ONLY_DEFAULTS.filter((s) => scopes.includes(s));
      });
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to load API keys.'), 'error');
      setKeys([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadKeys();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleScope = (scope) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      showToast('Give the key a name.', 'error');
      return;
    }
    if (!selectedScopes.length) {
      showToast('Select at least one scope.', 'error');
      return;
    }
    setCreating(true);
    try {
      const res = await apiKeysApi.create({ name: trimmed, scopes: selectedScopes, is_test: isTest });
      setNewSecret(res?.data?.secret || '');
      setName('');
      showToast('API key created — copy the secret now.', 'success');
      await loadKeys();
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to create API key.'), 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (key) => {
    if (typeof window !== 'undefined' && !window.confirm(`Revoke "${key.name}"? It stops working immediately.`)) {
      return;
    }
    try {
      await apiKeysApi.revoke(key.id);
      showToast('API key revoked.', 'success');
      await loadKeys();
    } catch (error) {
      showToast(getErrorMessage(error, 'Failed to revoke key.'), 'error');
    }
  };

  const handleCopySecret = async () => {
    if (!newSecret) return;
    try {
      await navigator.clipboard.writeText(newSecret);
      showToast('Secret copied to clipboard.', 'success');
    } catch {
      showToast('Copy failed — select and copy manually.', 'error');
    }
  };

  return (
    <div className="api-keys-panel">
      <div className="settings-subcard">
        <div className="settings-subcard-head">
          <div>
            <h3>Create an API key</h3>
            <p>Authenticate machine-to-machine requests to the Taali public API. The secret is shown once, on creation.</p>
          </div>
        </div>
        <form onSubmit={handleCreate}>
          <label className="field">
            <span className="k">Key name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Data warehouse sync"
              maxLength={120}
              disabled={creating}
            />
          </label>
          <div className="field">
            <span className="k">Scopes</span>
            <div style={S.scopes}>
              {availableScopes.length === 0 ? (
                <span style={S.mono}>Loading scopes…</span>
              ) : (
                availableScopes.map((scope) => (
                  <label key={scope} style={S.scope}>
                    <input
                      type="checkbox"
                      checked={selectedScopes.includes(scope)}
                      onChange={() => toggleScope(scope)}
                      disabled={creating}
                    />
                    <code>{scope}</code>
                  </label>
                ))
              )}
            </div>
          </div>
          <label style={{ ...S.scope, marginTop: 10 }}>
            <input type="checkbox" checked={isTest} onChange={(e) => setIsTest(e.target.checked)} disabled={creating} />
            <span>Test key (<code>tali_test_</code> — non-billing sandbox)</span>
          </label>
          <div className="settings-save-row">
            <div className="settings-inline-note">Scope keys to the minimum your integration needs.</div>
            <button type="submit" className="btn btn-purple btn-sm" disabled={creating}>
              {creating ? 'Creating…' : 'Create key'}
            </button>
          </div>
        </form>
      </div>

      {newSecret ? (
        <div style={S.secret} role="alert">
          <strong>Copy your secret now — you won&apos;t be able to see it again.</strong>
          <code style={S.secretValue}>{newSecret}</code>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="btn btn-purple btn-sm" onClick={handleCopySecret}>Copy secret</button>
            <button type="button" className="btn btn-sm" onClick={() => setNewSecret(null)}>Done</button>
          </div>
        </div>
      ) : null}

      <div className="settings-subcard">
        <div className="settings-subcard-head">
          <div>
            <h3>Active keys</h3>
            <p>
              Revoke a key to stop it working immediately.{' '}
              <a href="/developers" target="_blank" rel="noreferrer">Read the API docs →</a>
            </p>
          </div>
        </div>
        {loading ? (
          <p className="settings-inline-note">Loading…</p>
        ) : keys.length === 0 ? (
          <p className="settings-inline-note">No API keys yet.</p>
        ) : (
          <div role="table">
            <div role="row" style={{ ...S.row, borderTop: 'none' }}>
              <span style={S.head}>Name</span>
              <span style={S.head}>Key</span>
              <span style={S.head}>Scopes</span>
              <span style={S.head}>Last used</span>
              <span style={S.head}>Status</span>
              <span />
            </div>
            {keys.map((key) => {
              const revoked = Boolean(key.revoked_at);
              return (
                <div role="row" key={key.id} style={{ ...S.row, opacity: revoked ? 0.55 : 1 }}>
                  <span>{key.name}</span>
                  <span style={S.mono}>{key.prefix}…</span>
                  <span>
                    {(key.scopes || []).map((s) => (
                      <span key={s} style={S.chip}>{s}</span>
                    ))}
                  </span>
                  <span>{fmtDate(key.last_used_at)}</span>
                  <span>{revoked ? 'Revoked' : (key.is_test ? 'Test' : 'Active')}</span>
                  <span>
                    {!revoked ? (
                      <button type="button" className="btn btn-sm" onClick={() => handleRevoke(key)}>Revoke</button>
                    ) : null}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default ApiKeysPanel;
