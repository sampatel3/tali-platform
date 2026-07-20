import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  Button,
  Dialog,
  SegmentedControl,
  Sheet,
  TabBar,
} from '../shared/ui/TaaliPrimitives';

describe('Button', () => {
  it.each([
    'primary',
    'secondary',
    'ghost',
    'soft',
    'danger',
    'agent',
    'inverse',
  ])('applies the %s variant class', (variant) => {
    render(<Button variant={variant}>{variant}</Button>);

    expect(screen.getByRole('button', { name: variant })).toHaveClass(`taali-btn-${variant}`);
  });

  it.each(['xs', 'sm', 'md', 'lg'])('applies the %s size class', (size) => {
    render(<Button size={size}>{size}</Button>);

    expect(screen.getByRole('button', { name: size })).toHaveClass(`taali-btn-${size}`);
  });

  it('uses safe defaults for unknown variants and sizes', () => {
    render(<Button variant="unknown" size="unknown">Fallback</Button>);

    expect(screen.getByRole('button', { name: 'Fallback' })).toHaveClass(
      'taali-btn-secondary',
      'taali-btn-md'
    );
  });

  it('defaults native buttons to type button and preserves an explicit type', () => {
    const { rerender } = render(<Button>Safe submit</Button>);

    expect(screen.getByRole('button', { name: 'Safe submit' })).toHaveAttribute('type', 'button');

    rerender(<Button type="submit">Safe submit</Button>);
    expect(screen.getByRole('button', { name: 'Safe submit' })).toHaveAttribute('type', 'submit');
  });

  it('preserves polymorphic rendering without adding a default button type', () => {
    render(<Button as="a" href="/candidates">Candidates</Button>);

    const link = screen.getByRole('link', { name: 'Candidates' });
    expect(link).toHaveAttribute('href', '/candidates');
    expect(link).not.toHaveAttribute('type');
    expect(link).toHaveClass('taali-btn', 'taali-btn-secondary', 'taali-btn-md');
  });

  it('prevents disabled polymorphic links from activating', () => {
    render(<Button as="a" href="/candidates" disabled>Disabled link</Button>);

    const link = screen.getByRole('link', { name: 'Disabled link' });
    expect(link).toHaveAttribute('aria-disabled', 'true');
    expect(link).toHaveAttribute('tabindex', '-1');
    expect(link).not.toHaveAttribute('disabled');
  });

  it('renders an accessible disabled loading state', () => {
    render(<Button loading loadingLabel="Saving changes">Save</Button>);

    const button = screen.getByRole('button', { name: 'Saving changes' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(button).not.toHaveTextContent('Save');
    expect(button.querySelector('.taali-btn-spinner')).toHaveAttribute('data-motion-loop', 'spin');
    expect(button.querySelector('.taali-btn-spinner')).toHaveAttribute('aria-hidden', 'true');
  });

  it('keeps the original label while loading when no loading label is supplied', () => {
    render(<Button loading>Refresh</Button>);

    expect(screen.getByRole('button', { name: 'Refresh' })).toHaveTextContent('Refresh');
  });

  it('supports icon-only buttons and keeps caller classes last', () => {
    render(
      <Button iconOnly aria-label="Close" className="h-12 custom-button-layout">
        <span aria-hidden="true">×</span>
      </Button>
    );

    const button = screen.getByRole('button', { name: 'Close' });
    expect(button).toHaveClass('taali-btn-icon-only', 'h-12', 'custom-button-layout');
    expect(button.className.endsWith('h-12 custom-button-layout')).toBe(true);
  });

  it('supports full-width layout without creating another visual variant', () => {
    render(<Button fullWidth>Continue</Button>);

    expect(screen.getByRole('button', { name: 'Continue' })).toHaveClass('taali-btn-full');
  });
});

function TwoSheetHarness() {
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setLeftOpen(true)}>Open left</Button>
      <Button type="button" onClick={() => setRightOpen(true)}>Open right</Button>

      <Sheet
        open={leftOpen}
        onClose={() => setLeftOpen(false)}
        side="left"
        title="Left sheet"
        footer={<div>Left footer</div>}
      >
        <div>Left content</div>
      </Sheet>

      <Sheet
        open={rightOpen}
        onClose={() => setRightOpen(false)}
        title="Right sheet"
        footer={<div>Right footer</div>}
      >
        <div>Right content</div>
      </Sheet>
    </div>
  );
}

function DialogHarness() {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <Button type="button" onClick={() => setOpen(true)}>Open dialog</Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Confirm action"
        footer={<Button type="button">Confirm</Button>}
      >
        <p>Dialog content</p>
      </Dialog>
    </div>
  );
}

function TabHarness() {
  const [activeTab, setActiveTab] = useState('overview');
  const tabs = [
    {
      id: 'overview',
      label: 'Overview',
      tabId: 'local-tab-overview',
      panelId: 'local-panel-overview',
    },
    {
      id: 'disabled',
      label: 'Unavailable',
      tabId: 'local-tab-disabled',
      panelId: 'local-panel-disabled',
      disabled: true,
    },
    {
      id: 'history',
      label: 'History',
      tabId: 'local-tab-history',
      panelId: 'local-panel-history',
    },
    {
      id: 'notes',
      label: 'Notes',
      tabId: 'local-tab-notes',
      panelId: 'local-panel-notes',
    },
  ];
  const active = tabs.find((tab) => tab.id === activeTab);

  return (
    <>
      <TabBar
        tabs={tabs}
        activeTab={activeTab}
        onChange={setActiveTab}
        ariaLabel="Candidate evidence views"
        variant="segmented"
      />
      <div
        role="tabpanel"
        id={active.panelId}
        aria-labelledby={active.tabId}
      >
        {active.label} panel
      </div>
    </>
  );
}

function SegmentedHarness() {
  const [stage, setStage] = useState('all');
  return (
    <SegmentedControl
      ariaLabel="Filter candidates by stage"
      value={stage}
      onChange={setStage}
      options={[
        { value: 'all', label: 'All', meta: 12 },
        { value: 'review', label: 'Review', meta: 3 },
      ]}
    />
  );
}

describe('TabBar', () => {
  it('uses a labelled tab contract and roving tabindex', () => {
    render(<TabHarness />);

    const tablist = screen.getByRole('tablist', { name: 'Candidate evidence views' });
    const overview = within(tablist).getByRole('tab', { name: 'Overview' });
    const history = within(tablist).getByRole('tab', { name: 'History' });
    const unavailable = within(tablist).getByRole('tab', { name: 'Unavailable' });

    expect(overview).toHaveAttribute('aria-selected', 'true');
    expect(overview).toHaveAttribute('aria-controls', 'local-panel-overview');
    expect(overview).toHaveAttribute('tabindex', '0');
    expect(history).toHaveAttribute('tabindex', '-1');
    expect(unavailable).toBeDisabled();
    expect(unavailable).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Overview');
  });

  it('activates with Left/Right/Home/End, wraps, and skips disabled tabs', () => {
    render(<TabHarness />);

    const overview = screen.getByRole('tab', { name: 'Overview' });
    fireEvent.keyDown(overview, { key: 'ArrowRight' });

    const history = screen.getByRole('tab', { name: 'History' });
    expect(history).toHaveFocus();
    expect(history).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('History panel');

    fireEvent.keyDown(history, { key: 'End' });
    const notes = screen.getByRole('tab', { name: 'Notes' });
    expect(notes).toHaveFocus();
    expect(notes).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(notes, { key: 'Home' });
    expect(overview).toHaveFocus();
    expect(overview).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(overview, { key: 'ArrowLeft' });
    expect(notes).toHaveFocus();
    expect(notes).toHaveAttribute('aria-selected', 'true');
  });
});

describe('SegmentedControl', () => {
  it('uses button-group semantics for a mode or filter', () => {
    render(<SegmentedHarness />);

    const group = screen.getByRole('group', { name: 'Filter candidates by stage' });
    const all = within(group).getByRole('button', { name: /All/ });
    const review = within(group).getByRole('button', { name: /Review/ });

    expect(all).toHaveAttribute('aria-pressed', 'true');
    expect(review).toHaveAttribute('aria-pressed', 'false');
    expect(within(group).queryByRole('tab')).not.toBeInTheDocument();

    fireEvent.click(review);
    expect(all).toHaveAttribute('aria-pressed', 'false');
    expect(review).toHaveAttribute('aria-pressed', 'true');
  });

  it('can deselect the active option without changing the default contract', () => {
    const onChange = vi.fn();
    const options = [
      { value: 'assessment', label: 'Assessment stage' },
      { value: 'sourced', label: 'Sourced' },
    ];
    const { rerender } = render(
      <SegmentedControl
        ariaLabel="Candidate tracker"
        value="assessment"
        onChange={onChange}
        options={options}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Assessment stage' }));
    expect(onChange).toHaveBeenLastCalledWith('assessment');

    onChange.mockClear();
    rerender(
      <SegmentedControl
        ariaLabel="Candidate tracker"
        value="assessment"
        onChange={onChange}
        options={options}
        allowDeselect
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Assessment stage' }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('keeps rich compact options accessible and ignores disabled choices', () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        ariaLabel="Candidate tracker"
        density="compact"
        value="assessment"
        onChange={onChange}
        allowDeselect
        options={[
          {
            value: 'assessment',
            ariaLabel: 'Assessment stage',
            label: <><span aria-hidden="true">A</span>Assessment stage</>,
          },
          {
            value: 'sourced',
            ariaLabel: 'Sourced, 3',
            label: <><span aria-hidden="true">S</span>Sourced</>,
            meta: 3,
            disabled: true,
          },
        ]}
      />,
    );

    const group = screen.getByRole('group', { name: 'Candidate tracker' });
    expect(group).toHaveClass('taali-segmented-control--compact');
    expect(within(group).getByRole('button', { name: 'Assessment stage' }))
      .toHaveAttribute('aria-pressed', 'true');
    const sourced = within(group).getByRole('button', { name: 'Sourced, 3' });
    expect(within(sourced).getByText('3')).toHaveClass('taali-segmented-control__meta');
    expect(sourced).toBeDisabled();

    fireEvent.click(sourced);
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('Sheet', () => {
  it('restores page scrolling after multiple sheets close in any order', () => {
    render(<TwoSheetHarness />);

    fireEvent.click(screen.getByRole('button', { name: 'Open left' }));
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.click(screen.getByRole('button', { name: 'Open right' }));
    expect(document.body.style.overflow).toBe('hidden');

    const leftDialog = screen.getByRole('dialog', { name: 'Left sheet' });
    fireEvent.click(within(leftDialog).getByRole('button', { name: 'Close' }));
    expect(document.body.style.overflow).toBe('hidden');

    const rightDialog = screen.getByRole('dialog', { name: 'Right sheet' });
    fireEvent.click(within(rightDialog).getByRole('button', { name: 'Close' }));
    expect(document.body.style.overflow).toBe('');
  });

  it('restores focus and unmounts after its exit when Escape closes it', async () => {
    render(<TwoSheetHarness />);

    const trigger = screen.getByRole('button', { name: 'Open right' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole('dialog', { name: 'Right sheet' });
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(document.body.style.overflow).toBe('');
    expect(trigger).toHaveFocus();
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Right sheet' })).not.toBeInTheDocument();
    });
  });
});

describe('Dialog', () => {
  it('closes from its backdrop and restores the trigger after the exit', async () => {
    render(<DialogHarness />);

    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole('dialog', { name: 'Confirm action' });
    expect(document.body.style.overflow).toBe('hidden');
    expect(within(dialog).getByRole('button', { name: 'Close' })).toHaveFocus();

    const backdrop = dialog.parentElement?.parentElement;
    fireEvent.mouseDown(backdrop);

    expect(document.body.style.overflow).toBe('');
    expect(trigger).toHaveFocus();
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Confirm action' })).not.toBeInTheDocument();
    });
  });
});
