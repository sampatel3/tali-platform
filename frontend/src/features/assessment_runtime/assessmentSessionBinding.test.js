import { beforeEach, describe, expect, it, vi } from 'vitest';

describe('candidate assessment browser binding', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    vi.resetModules();
  });

  it('generates a base64url session key and keeps an expiring recovery record without the invite token', async () => {
    const firstModule = await import('./assessmentSessionBinding');
    const firstKey = firstModule.getOrCreateCandidateSessionKey('invite-token-a');

    expect(firstKey).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    expect(window.sessionStorage.length).toBe(1);
    expect(window.localStorage.length).toBe(1);
    const recoveryRecord = JSON.parse(window.localStorage.getItem(window.localStorage.key(0)));
    expect(recoveryRecord).toMatchObject({
      version: 1,
      session_key: firstKey,
    });
    expect(recoveryRecord.expires_at).toBeGreaterThan(Date.now());
    expect(recoveryRecord.expires_at).toBeLessThanOrEqual(
      Date.now() + firstModule.CANDIDATE_SESSION_RECOVERY_TTL_MS,
    );
    expect(`${window.localStorage.key(0)}${JSON.stringify(recoveryRecord)}`).not.toContain('invite-token-a');

    vi.resetModules();
    const reloadedModule = await import('./assessmentSessionBinding');
    expect(reloadedModule.getOrCreateCandidateSessionKey('invite-token-a')).toBe(firstKey);
    expect(window.sessionStorage.length).toBe(1);
  });

  it('recovers the same binding after the tab-scoped storage is cleared', async () => {
    const { getOrCreateCandidateSessionKey } = await import('./assessmentSessionBinding');
    const firstKey = getOrCreateCandidateSessionKey('invite-token-a');

    window.sessionStorage.clear();

    expect(getOrCreateCandidateSessionKey('invite-token-a')).toBe(firstKey);
    expect(window.sessionStorage.length).toBe(1);
  });

  it('discards an expired recovery record instead of reviving it', async () => {
    const { getOrCreateCandidateSessionKey } = await import('./assessmentSessionBinding');
    const firstKey = getOrCreateCandidateSessionKey('invite-token-a');
    const recoveryStorageKey = window.localStorage.key(0);
    const recoveryRecord = JSON.parse(window.localStorage.getItem(recoveryStorageKey));
    window.localStorage.setItem(recoveryStorageKey, JSON.stringify({
      ...recoveryRecord,
      expires_at: Date.now() - 1,
    }));
    window.sessionStorage.clear();

    const replacementKey = getOrCreateCandidateSessionKey('invite-token-a');

    expect(replacementKey).not.toBe(firstKey);
    expect(replacementKey).toMatch(/^[A-Za-z0-9_-]{32,}$/);
  });

  it('clears both copies after a successful assessment submission', async () => {
    const {
      clearCandidateSessionKey,
      getOrCreateCandidateSessionKey,
    } = await import('./assessmentSessionBinding');
    getOrCreateCandidateSessionKey('invite-token-a');

    clearCandidateSessionKey('invite-token-a');

    expect(window.sessionStorage.length).toBe(0);
    expect(window.localStorage.length).toBe(0);
  });

  it('creates a separate binding for a different invite token', async () => {
    const { getOrCreateCandidateSessionKey } = await import('./assessmentSessionBinding');
    const firstKey = getOrCreateCandidateSessionKey('invite-token-a');
    const secondKey = getOrCreateCandidateSessionKey('invite-token-b');

    expect(secondKey).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    expect(secondKey).not.toBe(firstKey);
    expect(window.sessionStorage.length).toBe(2);
    expect(window.localStorage.length).toBe(2);
  });
});
