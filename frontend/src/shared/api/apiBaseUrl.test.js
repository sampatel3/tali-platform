import { afterEach, describe, expect, it, vi } from 'vitest';

const loadModule = async () => {
  vi.resetModules();
  return import('./apiBaseUrl');
};

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('resolveApiUrl', () => {
  it('prefers an explicit VITE_API_URL when provided', async () => {
    vi.stubEnv('VITE_API_URL', ' https://api.example.com/ ');
    const { resolveApiUrl } = await loadModule();

    expect(resolveApiUrl({ hostname: 'frontend.example.com', origin: 'https://frontend.example.com' })).toBe('https://api.example.com');
  });

  it('falls back to localhost for local development', async () => {
    const { resolveApiUrl } = await loadModule();

    expect(resolveApiUrl({ hostname: 'localhost', origin: 'http://localhost:5173' })).toBe('http://localhost:8000');
  });

  it('falls back to the Railway API for remote preview builds without config', async () => {
    const { DEFAULT_REMOTE_API_URL, resolveApiUrl } = await loadModule();

    expect(resolveApiUrl({ hostname: 'frontend-preview.vercel.app', origin: 'https://frontend-preview.vercel.app' })).toBe(DEFAULT_REMOTE_API_URL);
  });
});
