import React from 'react';
import '@testing-library/jest-dom';
import { act, cleanup } from '@testing-library/react';
import { vi } from 'vitest';

vi.mock('@monaco-editor/react', () => ({
  default: ({ value = '', onChange = null }) => React.createElement('textarea', {
    readOnly: true,
    'data-testid': 'code-editor',
    value: value ?? '',
    onChange: onChange ? (event) => onChange(event.target.value) : undefined,
  }),
}));

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

class WebSocketMock {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor() {
    this.readyState = WebSocketMock.CONNECTING;
    setTimeout(() => {
      this.readyState = WebSocketMock.OPEN;
      this.onopen?.();
    }, 0);
  }

  send() {}

  close() {
    this.readyState = WebSocketMock.CLOSED;
    this.onclose?.();
  }
}

if (typeof window !== 'undefined' && !window.ResizeObserver) {
  window.ResizeObserver = ResizeObserverMock;
}

if (typeof window !== 'undefined') {
  window.WebSocket = WebSocketMock;
  globalThis.WebSocket = WebSocketMock;
  window.scrollTo = () => {};
  globalThis.scrollTo = () => {};
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: window.matchMedia || ((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })),
  });
}

beforeEach(() => {
  if (typeof window !== 'undefined') {
    window.history.replaceState(null, '', '/');
    window.location.hash = '';
  }
});

afterEach(async () => {
  await act(async () => {
    await Promise.resolve();
  });
  cleanup();
});
