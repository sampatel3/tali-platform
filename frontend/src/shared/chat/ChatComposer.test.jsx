import { useRef, useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { ChatComposer } from './ChatComposer';

it('gives the shared message textarea an accessible name', () => {
  render(<ChatComposer value="" onChange={vi.fn()} onSubmit={vi.fn()} />);
  expect(screen.getByRole('textbox', { name: 'Chat message' })).toBeInTheDocument();
});

// Controlled harness — mirrors how every chat surface wires the composer:
// value lives in the parent, onChange writes it back, onSubmit gets the text.
function Harness({ onSubmit, submitMode }) {
  const [value, setValue] = useState('');
  return (
    <ChatComposer value={value} onChange={setValue} onSubmit={onSubmit} submitMode={submitMode} />
  );
}

function ReplyHarness({ onCancel = () => {} }) {
  const [value, setValue] = useState('A saved draft');
  const [replyTo, setReplyTo] = useState({
    id: 'request:42',
    label: 'Reply to agent',
    prompt: 'What screening threshold should I use?',
  });
  return (
    <ChatComposer
      value={value}
      onChange={setValue}
      onSubmit={() => {}}
      replyTo={replyTo}
      onCancelReply={() => {
        onCancel();
        setReplyTo(null);
      }}
    />
  );
}

const type = (text) =>
  fireEvent.change(screen.getByRole('textbox'), { target: { value: text } });

const pressEnter = (init = {}) =>
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', ...init });

test('plain Enter sends the typed text', () => {
  const onSubmit = vi.fn();
  render(<Harness onSubmit={onSubmit} />);
  type('find the top 10 candidates');
  pressEnter();
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith('find the top 10 candidates');
});

test('Shift+Enter inserts a newline instead of sending', () => {
  const onSubmit = vi.fn();
  render(<Harness onSubmit={onSubmit} />);
  type('line one');
  pressEnter({ shiftKey: true });
  expect(onSubmit).not.toHaveBeenCalled();
});

// The bug: hitting Enter to *commit* an IME / dictation / autocorrect
// composition also fired submit, sending the pre-commit value — "the message
// that gets sent is different from what I typed".
test('Enter while an IME composition is open does NOT send', () => {
  const onSubmit = vi.fn();
  render(<Harness onSubmit={onSubmit} />);
  type('cap salary at');
  pressEnter({ isComposing: true });
  expect(onSubmit).not.toHaveBeenCalled();
});

test('legacy keyCode 229 (composition) does NOT send', () => {
  const onSubmit = vi.fn();
  render(<Harness onSubmit={onSubmit} />);
  type('cap salary at');
  pressEnter({ keyCode: 229 });
  expect(onSubmit).not.toHaveBeenCalled();
});

// After the composition commits (isComposing back to false), the next Enter
// sends the final, complete text intact.
test('Enter after the composition commits sends the final text', () => {
  const onSubmit = vi.fn();
  render(<Harness onSubmit={onSubmit} />);
  type('cap salary at');
  pressEnter({ isComposing: true }); // commit — swallowed
  type('cap salary at AED 25k'); // composition resolved to final text
  pressEnter(); // real send
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith('cap salary at AED 25k');
});

test("cmd mode: plain Enter never sends, even mid-composition", () => {
  const onSubmit = vi.fn();
  render(<Harness onSubmit={onSubmit} submitMode="cmd" />);
  type('note to self');
  pressEnter(); // newline in cmd mode
  pressEnter({ isComposing: true });
  expect(onSubmit).not.toHaveBeenCalled();
  pressEnter({ metaKey: true });
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith('note to self');
});

test('forwards the textarea ref so helper actions can focus the composer', () => {
  function FocusHarness() {
    const inputRef = useRef(null);
    const [value, setValue] = useState('Review affected candidates');
    return (
      <>
        <button type="button" onClick={() => inputRef.current?.focus()}>Focus composer</button>
        <ChatComposer
          ref={inputRef}
          value={value}
          onChange={setValue}
          onSubmit={() => {}}
        />
      </>
    );
  }

  render(<FocusHarness />);
  fireEvent.click(screen.getByRole('button', { name: 'Focus composer' }));
  expect(screen.getByRole('textbox')).toHaveFocus();
});

test('Escape used by an active IME does not cancel reply mode', () => {
  const onCancel = vi.fn();
  render(<ReplyHarness onCancel={onCancel} />);
  const textbox = screen.getByRole('textbox', { name: 'Answer the agent' });

  fireEvent.keyDown(textbox, { key: 'Escape', isComposing: true });
  fireEvent.keyDown(textbox, { key: 'Escape', keyCode: 229 });

  expect(onCancel).not.toHaveBeenCalled();
  expect(textbox).toHaveAttribute('aria-describedby');
});

test('cancel restores composer focus and removes its reply description link', async () => {
  render(<ReplyHarness />);
  const textbox = screen.getByRole('textbox', { name: 'Answer the agent' });
  const descriptionId = textbox.getAttribute('aria-describedby');
  const description = document.getElementById(descriptionId);
  expect(description).toHaveTextContent('What screening threshold should I use?');

  const cancel = screen.getByRole('button', { name: 'Cancel reply and restore draft' });
  cancel.focus();
  fireEvent.click(cancel);

  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Chat message' })).toHaveFocus());
  expect(screen.getByRole('textbox')).not.toHaveAttribute('aria-describedby');
  expect(document.getElementById(descriptionId)).not.toBeInTheDocument();
});
