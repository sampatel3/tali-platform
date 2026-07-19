import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { JobPipelineHeaderActions } from './JobPipelineHeaderActions';

const renderSisterActions = (status) => render(
  <JobPipelineHeaderActions
    canEditJobSpec={false}
    externalProvider="workable"
    externalProviderLabel="Workable"
    navigate={vi.fn()}
    onEditJobSpec={vi.fn()}
    onOpenProcessDialog={vi.fn()}
    onRescoreSister={vi.fn()}
    onStartRelatedRole={vi.fn()}
    processStatus="idle"
    role={{ id: 9, role_kind: 'sister' }}
    roleAgent={{ pending: 0 }}
    rolePendingReviewTitle="No pending review"
    sisterRescoring={false}
    sisterScoringStatus={status}
    startingRelatedRole={false}
  />,
);

describe('JobPipelineHeaderActions related-role scoring state', () => {
  it('keeps waiting work disabled and exposes its progress', () => {
    renderSisterActions({ status: 'waiting', progress_percent: 42 });
    expect(screen.getByRole('button', { name: 'Waiting 42%' })).toBeDisabled();
  });

  it('keeps retrying work disabled and exposes its progress', () => {
    renderSisterActions({ status: 'retrying', progress_percent: 63 });
    expect(screen.getByRole('button', { name: 'Retrying 63%' })).toBeDisabled();
  });
});
