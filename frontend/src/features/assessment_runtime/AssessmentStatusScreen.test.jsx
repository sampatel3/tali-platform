import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AssessmentStatusScreen } from './AssessmentStatusScreen';

describe('AssessmentStatusScreen', () => {
  it('keeps hook order stable when loading finishes', () => {
    const { rerender } = render(<AssessmentStatusScreen mode="loading" />);
    expect(screen.getByText('Loading assessment...')).toBeInTheDocument();

    rerender(<AssessmentStatusScreen mode="submitted" />);

    expect(screen.getByRole('heading', { name: 'Task submitted' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close window' })).toBeInTheDocument();
  });
});
