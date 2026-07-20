import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  createProofHeaders: vi.fn(),
  getProofBinding: vi.fn(),
}));

vi.mock('./httpClient', () => ({
  ASSESSMENT_TOKEN_AUTH_MODE: 'assessment-token',
  default: {
    get: mocks.get,
    post: mocks.post,
  },
}));

vi.mock('../assessment/candidateProofBinding', () => ({
  createCandidateProofHeaders: (...args) => mocks.createProofHeaders(...args),
  getOrCreateCandidateProofBinding: (...args) => mocks.getProofBinding(...args),
}));

import { assessments } from './assessmentsClient';

const expectedHeaders = {
  'X-Assessment-Token': 'assessment-token',
  'X-Assessment-Session': 'candidate-session-key',
  'X-Assessment-Key-Id': 'proof-key-id',
  'X-Assessment-Proof-Timestamp': '1234',
  'X-Assessment-Proof-Nonce': 'proof-nonce',
  'X-Assessment-Proof': 'proof-signature',
};

const proofHeaders = {
  'X-Assessment-Key-Id': 'proof-key-id',
  'X-Assessment-Proof-Timestamp': '1234',
  'X-Assessment-Proof-Nonce': 'proof-nonce',
  'X-Assessment-Proof': 'proof-signature',
};

describe('assessments candidate runtime client', () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.post.mockReset();
    mocks.createProofHeaders.mockReset();
    mocks.getProofBinding.mockReset();
    mocks.createProofHeaders.mockResolvedValue(proofHeaders);
    mocks.getProofBinding.mockResolvedValue({
      keyId: 'proof-key-id',
      publicJwk: { kty: 'EC', crv: 'P-256', x: 'public-x', y: 'public-y' },
    });
  });

  it('registers the public proof key on start and signs the lazy file request', async () => {
    await assessments.start('invite-token', { candidate_session_key: 'candidate-session-key' });
    await assessments.getRepoFile(42, 'src/main.py', 'assessment-token', 'candidate-session-key');

    expect(mocks.post).toHaveBeenCalledWith(
      '/assessments/token/invite-token/start',
      {
        candidate_session_key: 'candidate-session-key',
        candidate_proof_key_id: 'proof-key-id',
        candidate_proof_public_jwk: { kty: 'EC', crv: 'P-256', x: 'public-x', y: 'public-y' },
      },
      { authMode: 'assessment-token', headers: proofHeaders },
    );
    expect(mocks.get).toHaveBeenCalledWith('/assessments/42/repo-file?path=src%2Fmain.py', {
      authMode: 'assessment-token',
      headers: expectedHeaders,
    });
    expect(mocks.createProofHeaders).toHaveBeenNthCalledWith(1, 'invite-token', expect.objectContaining({
      method: 'POST',
      pathAndQuery: '/api/v1/assessments/token/invite-token/start',
    }));
    expect(mocks.createProofHeaders).toHaveBeenNthCalledWith(2, 'assessment-token', {
      method: 'GET',
      pathAndQuery: '/api/v1/assessments/42/repo-file?path=src%2Fmain.py',
      body: null,
    });
  });

  it('propagates a request-bound browser proof to every authenticated runtime write', async () => {
    await assessments.execute(42, { code: 'x = 1' }, 'assessment-token', 'candidate-session-key');
    await assessments.saveRepoFile(42, { path: 'x.py', content: 'x = 1' }, 'assessment-token', 'candidate-session-key');
    await assessments.claudeChat(42, { message: 'inspect' }, 'assessment-token', 'candidate-session-key');
    await assessments.runtimeEvent(42, 'file_opened', 'assessment-token', {}, 'candidate-session-key');
    await assessments.keepalive(42, 'assessment-token', 'candidate-session-key');
    await assessments.submit(42, { final_code: 'x = 1' }, 'assessment-token', {}, 'candidate-session-key');

    expect(mocks.post).toHaveBeenNthCalledWith(
      1,
      '/assessments/42/execute',
      { code: 'x = 1' },
      { authMode: 'assessment-token', headers: expectedHeaders },
    );
    expect(mocks.post.mock.calls[1][2]).toMatchObject({ authMode: 'assessment-token', headers: expectedHeaders });
    expect(mocks.post.mock.calls[2][2]).toMatchObject({ authMode: 'assessment-token', headers: expectedHeaders, timeout: 120000 });
    expect(mocks.post.mock.calls[3][2]).toMatchObject({ authMode: 'assessment-token', headers: expectedHeaders });
    expect(mocks.post.mock.calls[4][2]).toMatchObject({ authMode: 'assessment-token', headers: expectedHeaders });
    expect(mocks.post.mock.calls[5][2]).toMatchObject({ authMode: 'assessment-token', headers: expectedHeaders });
    expect(mocks.createProofHeaders).toHaveBeenCalledTimes(6);
    expect(mocks.createProofHeaders.mock.calls.map((call) => call[1].pathAndQuery)).toEqual([
      '/api/v1/assessments/42/execute',
      '/api/v1/assessments/42/repo-file',
      '/api/v1/assessments/42/claude/chat',
      '/api/v1/assessments/42/runtime-event',
      '/api/v1/assessments/42/keepalive',
      '/api/v1/assessments/42/submit',
    ]);
  });
});
