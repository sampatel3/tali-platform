import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock('./httpClient', () => ({ default: http }));

import { agent } from './agentClient';
import { agentChat } from './agentChatClient';
import { roles } from './rolesClient';

describe('versioned role mutation clients', () => {
  beforeEach(() => vi.clearAllMocks());

  it('forwards the rendered version on shared role and job-spec writes', () => {
    roles.update(26, { agentic_mode_enabled: false, expected_version: 4 });
    roles.updateJobSpec(26, { job_spec_text: 'Updated spec', expected_version: 4 });

    expect(http.patch).toHaveBeenCalledWith('/roles/26', {
      agentic_mode_enabled: false,
      expected_version: 4,
    });
    expect(http.put).toHaveBeenCalledWith('/roles/26/job-spec', {
      job_spec_text: 'Updated spec',
      expected_version: 4,
    });
  });

  it('sends expected_version in pause and resume command bodies', () => {
    agent.pause(26, 4);
    agent.resume(26, 5);

    expect(http.post).toHaveBeenCalledWith('/roles/26/agent/pause', { expected_version: 4 });
    expect(http.post).toHaveBeenCalledWith('/roles/26/agent/resume', { expected_version: 5 });
  });

  it('sends the workspace control version in global pause and resume commands', () => {
    agent.pauseAll(8);
    agent.resumeAll(9);

    expect(http.post).toHaveBeenCalledWith('/agent/pause-all', {
      expected_control_version: 8,
    });
    expect(http.post).toHaveBeenCalledWith('/agent/resume-all', {
      expected_control_version: 9,
    });
  });

  it('caps the status refresh that gates workspace controls', () => {
    agent.orgStatus();

    expect(http.get).toHaveBeenCalledWith('/agent/org-status', { timeout: 10000 });
  });

  it('versions draft approval and structured revision commands', () => {
    agentChat.approveDraftTask(26, 81, 9);
    agentChat.reviseDraftTask(26, 81, {
      answers: { issues: ['scope'] },
      note: 'Keep the scenario.',
      expectedVersion: 10,
    });

    expect(http.post).toHaveBeenCalledWith(
      '/agent-chat/conversations/26/draft-tasks/81/approve',
      { expected_version: 9 },
    );
    expect(http.post).toHaveBeenCalledWith(
      '/agent-chat/conversations/26/draft-tasks/81/revise',
      {
        expected_version: 10,
        answers: { issues: ['scope'] },
        note: 'Keep the scenario.',
      },
    );
  });

  it('versions lifecycle, client assignment, and permanent deletion commands', () => {
    roles.setJobStatus(26, 'filled', 'role closed', 6);
    roles.setClient(26, 19, 7);
    roles.remove(26, 8);
    roles.regenerateInterviewFocus(26, 9);
    roles.createFeedbackNote(26, 'Prefer product judgment.', 10);

    expect(http.post).toHaveBeenCalledWith('/roles/26/job-status', {
      status: 'filled',
      reason: 'role closed',
      expected_version: 6,
    });
    expect(http.post).toHaveBeenCalledWith('/roles/26/client', {
      client_id: 19,
      expected_version: 7,
    });
    expect(http.delete).toHaveBeenCalledWith('/roles/26', {
      params: { expected_version: 8 },
    });
    expect(http.post).toHaveBeenCalledWith('/roles/26/regenerate-interview-focus', {
      expected_version: 9,
    });
    expect(http.post).toHaveBeenCalledWith('/roles/26/feedback-notes', {
      note: 'Prefer product judgment.',
      expected_version: 10,
    });
  });
});
