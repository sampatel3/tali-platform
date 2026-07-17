import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AssessmentEvidencePanels } from './CandidateAssessmentDetailPanels';

describe('AssessmentEvidencePanels', () => {
  it('uses the shared keyboard tab contract for raw-evidence panels', () => {
    render(
      <AssessmentEvidencePanels
        candidate={{ id: 17, status: 'completed', _raw: {}, promptsList: [], timeline: [] }}
      />
    );

    const tablist = screen.getByRole('tablist', { name: 'Assessment evidence' });
    const prompts = within(tablist).getByRole('tab', { name: 'Prompts' });
    const code = within(tablist).getByRole('tab', { name: 'Code & git' });

    expect(prompts).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Prompts');

    fireEvent.keyDown(prompts, { key: 'ArrowRight' });
    expect(code).toHaveFocus();
    expect(code).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Code & git');
  });
});
