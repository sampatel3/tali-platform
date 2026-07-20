import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api from './httpClient';
import { assessments } from './assessmentsClient';

const RECRUITER_TOKEN = 'recruiter-token';
const RECRUITER_USER = JSON.stringify({ id: 'recruiter-1' });

const response = (config, data = {}) => ({
  data,
  status: 200,
  statusText: 'OK',
  headers: {},
  config,
  request: {},
});

const unauthorized = (config) => {
  const error = new Error('Request failed with status code 401');
  error.config = config;
  error.response = {
    data: { detail: 'Unauthorized' },
    status: 401,
    statusText: 'Unauthorized',
    headers: {},
    config,
    request: {},
  };
  return error;
};

const header = (config, name) => (
  typeof config?.headers?.get === 'function'
    ? config.headers.get(name)
    : config?.headers?.[name]
);

const candidateRequests = [
  {
    name: 'token preview',
    url: '/assessments/token/candidate-token/preview',
    invoke: () => assessments.preview('candidate-token'),
  },
  {
    name: 'token start',
    url: '/assessments/token/candidate-token/start',
    invoke: () => assessments.start('candidate-token', { consent: true }),
  },
  {
    name: 'code execution',
    url: '/assessments/assessment-1/execute',
    invoke: () => assessments.execute('assessment-1', { code: 'return 1' }, 'candidate-token'),
  },
  {
    name: 'repository file save',
    url: '/assessments/assessment-1/repo-file',
    invoke: () => assessments.saveRepoFile(
      'assessment-1',
      { path: 'src/index.js', content: 'export default 1' },
      'candidate-token',
    ),
  },
  {
    name: 'Claude chat',
    url: '/assessments/assessment-1/claude/chat',
    invoke: () => assessments.claudeChat(
      'assessment-1',
      { message: 'Help me reason about this.' },
      'candidate-token',
    ),
  },
  {
    name: 'runtime event',
    url: '/assessments/assessment-1/runtime-event',
    invoke: () => assessments.runtimeEvent('assessment-1', 'runtime_loaded', 'candidate-token'),
  },
  {
    name: 'submission',
    url: '/assessments/assessment-1/submit',
    invoke: () => assessments.submit('assessment-1', { final_code: 'return 1' }, 'candidate-token'),
  },
  {
    name: 'assessment-id CV upload',
    url: '/assessments/assessment-1/upload-cv',
    invoke: () => assessments.uploadCv(
      'assessment-1',
      'candidate-token',
      new File(['resume'], 'resume.pdf', { type: 'application/pdf' }),
    ),
  },
  {
    name: 'token CV upload',
    url: '/assessments/token/candidate-token/upload-cv',
    invoke: () => assessments.uploadCv(
      null,
      'candidate-token',
      new File(['resume'], 'resume.pdf', { type: 'application/pdf' }),
    ),
  },
];

describe('candidate assessment auth isolation', () => {
  let originalAdapter;
  let logoutSpy;

  beforeEach(() => {
    originalAdapter = api.defaults.adapter;
    localStorage.clear();
    localStorage.setItem('taali_access_token', RECRUITER_TOKEN);
    localStorage.setItem('taali_token_issued_at', '0');
    localStorage.setItem('taali_user', RECRUITER_USER);
    logoutSpy = vi.fn();
    window.addEventListener('auth:logout', logoutSpy);
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    window.removeEventListener('auth:logout', logoutSpy);
    localStorage.clear();
  });

  it.each(candidateRequests)(
    '$name uses only candidate auth and cannot refresh or clear the recruiter session on 401',
    async ({ url, invoke }) => {
      const requests = [];
      api.defaults.adapter = async (config) => {
        requests.push(config);
        if (config.url === '/auth/jwt/refresh') {
          return response(config, { access_token: 'refreshed-recruiter-token' });
        }
        throw unauthorized(config);
      };

      await expect(invoke()).rejects.toMatchObject({ response: { status: 401 } });

      const candidateRequest = requests.find((config) => config.url === url);
      expect(candidateRequest).toBeDefined();
      expect(candidateRequest.authMode).toBe('assessment-token');
      expect(header(candidateRequest, 'Authorization')).toBeUndefined();
      expect(requests.filter((config) => config.url === '/auth/jwt/refresh')).toHaveLength(0);
      expect(localStorage.getItem('taali_access_token')).toBe(RECRUITER_TOKEN);
      expect(localStorage.getItem('taali_token_issued_at')).toBe('0');
      expect(localStorage.getItem('taali_user')).toBe(RECRUITER_USER);
      expect(logoutSpy).not.toHaveBeenCalled();
    },
  );

  it('still refreshes and signs recruiter assessment requests', async () => {
    const requests = [];
    api.defaults.adapter = async (config) => {
      requests.push(config);
      if (config.url === '/auth/jwt/refresh') {
        return response(config, { access_token: 'refreshed-recruiter-token' });
      }
      return response(config, { id: 'assessment-1' });
    };

    await assessments.get('assessment-1');

    expect(requests.map((config) => config.url)).toEqual([
      '/auth/jwt/refresh',
      '/assessments/assessment-1',
    ]);
    const recruiterRequest = requests.at(-1);
    expect(recruiterRequest.authMode).toBeUndefined();
    expect(header(recruiterRequest, 'Authorization')).toBe('Bearer refreshed-recruiter-token');
    expect(localStorage.getItem('taali_access_token')).toBe('refreshed-recruiter-token');
  });

  it('still clears the recruiter session when a recruiter assessment request returns 401', async () => {
    localStorage.setItem('taali_token_issued_at', String(Date.now()));
    const requests = [];
    api.defaults.adapter = async (config) => {
      requests.push(config);
      throw unauthorized(config);
    };

    await expect(assessments.get('assessment-1')).rejects.toMatchObject({ response: { status: 401 } });

    expect(requests).toHaveLength(1);
    expect(header(requests[0], 'Authorization')).toBe(`Bearer ${RECRUITER_TOKEN}`);
    expect(localStorage.getItem('taali_access_token')).toBeNull();
    expect(localStorage.getItem('taali_token_issued_at')).toBeNull();
    expect(localStorage.getItem('taali_user')).toBeNull();
    expect(logoutSpy).toHaveBeenCalledTimes(1);
  });
});
