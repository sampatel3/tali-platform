import { fireEvent, render } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import CriteriaEditor from './CriteriaEditor';

const CHIPS = [
  { id: 1, text: '5+ years backend', bucket: 'must', ordering: 0 },
  { id: 2, text: 'GraphQL', bucket: 'preferred', ordering: 0 },
];

const makeDataTransfer = () => ({
  setData: vi.fn(),
  getData: vi.fn(),
  effectAllowed: '',
  dropEffect: '',
});

describe('CriteriaEditor drag-and-drop', () => {
  it('calls onUpdate with the target bucket when a chip is dropped on a different column', () => {
    const onUpdate = vi.fn();
    const { container } = render(
      <CriteriaEditor mode="role" criteria={CHIPS} onUpdate={onUpdate} />,
    );

    const chip = container.querySelector('.ce-chip--must');
    const targetColumn = container.querySelector('.ce-col--preferred');
    const dataTransfer = makeDataTransfer();

    fireEvent.dragStart(chip, { dataTransfer });
    fireEvent.dragOver(targetColumn, { dataTransfer });
    fireEvent.drop(targetColumn, { dataTransfer });

    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onUpdate).toHaveBeenCalledWith(1, { bucket: 'preferred' });
  });

  it('does not call onUpdate when a chip is dropped on its own column', () => {
    const onUpdate = vi.fn();
    const { container } = render(
      <CriteriaEditor mode="role" criteria={CHIPS} onUpdate={onUpdate} />,
    );

    const chip = container.querySelector('.ce-chip--must');
    const sameColumn = container.querySelector('.ce-col--must');
    const dataTransfer = makeDataTransfer();

    fireEvent.dragStart(chip, { dataTransfer });
    fireEvent.drop(sameColumn, { dataTransfer });

    expect(onUpdate).not.toHaveBeenCalled();
  });
});
