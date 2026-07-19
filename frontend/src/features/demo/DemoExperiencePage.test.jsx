import React from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

vi.mock('../../shared/api', () => ({
  assessments: { requestDemo: vi.fn().mockResolvedValue({ data: {} }) },
}));

import { DemoExperiencePage } from './DemoExperiencePage';

let restoreScrollIntoView = null;

afterEach(() => {
  restoreScrollIntoView?.();
  restoreScrollIntoView = null;
  vi.useRealTimers();
});

describe('DemoExperiencePage walkthrough navigation', () => {
  it('opens the walkthrough with the shared peer-view selector', () => {
    render(<DemoExperiencePage />);

    fireEvent.change(screen.getByPlaceholderText('Jane Doe'), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByPlaceholderText('jane@company.com'), { target: { value: 'jane@acme.test' } });
    fireEvent.change(screen.getByPlaceholderText('Acme Inc.'), { target: { value: 'Acme' } });
    fireEvent.click(screen.getByRole('button', { name: 'Company size' }));
    fireEvent.click(screen.getByRole('option', { name: '11–50' }));
    fireEvent.click(screen.getByRole('button', { name: /Open walkthrough/i }));

    const navigation = screen.getByRole('navigation', { name: 'Walkthrough views' });
    const jobs = within(navigation).getByRole('button', { name: /Jobs you’re hiring for/i });
    const workspace = within(navigation).getByRole('button', { name: /Candidate workspace/i });
    expect(jobs).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(workspace);
    expect(workspace).toHaveAttribute('aria-pressed', 'true');
    expect(jobs).toHaveAttribute('aria-pressed', 'false');
  });

  it('cancels the delayed walkthrough scroll when the page unmounts', () => {
    vi.useFakeTimers();
    const scrollIntoView = vi.fn();
    const previousDescriptor = Object.getOwnPropertyDescriptor(
      window.HTMLElement.prototype,
      'scrollIntoView',
    );
    Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
    restoreScrollIntoView = () => {
      if (previousDescriptor) {
        Object.defineProperty(
          window.HTMLElement.prototype,
          'scrollIntoView',
          previousDescriptor,
        );
      } else {
        delete window.HTMLElement.prototype.scrollIntoView;
      }
    };
    const { unmount } = render(<DemoExperiencePage />);

    fireEvent.change(screen.getByPlaceholderText('Jane Doe'), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByPlaceholderText('jane@company.com'), { target: { value: 'jane@acme.test' } });
    fireEvent.change(screen.getByPlaceholderText('Acme Inc.'), { target: { value: 'Acme' } });
    fireEvent.click(screen.getByRole('button', { name: 'Company size' }));
    fireEvent.click(screen.getByRole('option', { name: '11–50' }));
    fireEvent.click(screen.getByRole('button', { name: /Open walkthrough/i }));

    unmount();
    act(() => {
      vi.advanceTimersByTime(60);
    });

    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
