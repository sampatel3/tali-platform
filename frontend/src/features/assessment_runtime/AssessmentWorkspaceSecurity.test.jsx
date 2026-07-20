import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  createOpaqueWorkspaceMarker,
  createProtectedRootHandlers,
  useAssessmentWorkspaceSecurity,
} from './AssessmentWorkspaceSecurity';

describe('assessment workspace security controller', () => {
  it('creates an opaque marker that carries no candidate PII', () => {
    const marker = createOpaqueWorkspaceMarker(42);
    expect(marker).toMatch(/^WS-A16-[A-Z0-9]{4}$/);
    expect(marker).not.toContain('@');
  });

  it('keeps copied content in memory and emits bounded advisory metadata only', () => {
    const emitEvent = vi.fn();
    const { result } = renderHook(() => useAssessmentWorkspaceSecurity({
      enabled: true,
      sessionMarker: 'WS-A16-TEST',
      emitEvent,
      resetKey: 42,
    }));

    act(() => {
      result.current.copy('private workspace code', {
        surface: 'editor',
        operation: 'copy',
      });
    });
    expect(emitEvent).toHaveBeenCalledWith('copy_attempt', {
      source: 'editor',
      length: 22,
    });
    expect(JSON.stringify(emitEvent.mock.calls)).not.toContain('private workspace code');

    let pasteResult;
    act(() => {
      pasteResult = result.current.paste({
        surface: 'claude',
        externalCharacterCount: 9,
      });
    });
    expect(pasteResult).toEqual({ text: 'private workspace code', blocked: false });
    expect(emitEvent).toHaveBeenLastCalledWith('internal_paste', {
      source: 'claude',
      length: 22,
    });
  });

  it('blocks external paste when no workspace content has been copied', () => {
    const emitEvent = vi.fn();
    const { result } = renderHook(() => useAssessmentWorkspaceSecurity({
      enabled: true,
      sessionMarker: 'WS-A16-TEST',
      emitEvent,
      resetKey: 42,
    }));

    let pasteResult;
    act(() => {
      pasteResult = result.current.paste({
        surface: 'editor',
        externalCharacterCount: 128,
      });
    });

    expect(pasteResult).toEqual({ text: '', blocked: true });
    expect(emitEvent).toHaveBeenCalledWith('external_paste_blocked', {
      source: 'editor',
      length: 128,
    });
  });

  it('blocks outbound drag and browser context-menu export paths', () => {
    const security = {
      enabled: true,
      report: vi.fn(),
      announce: vi.fn(),
    };
    const handlers = createProtectedRootHandlers(security);
    const target = {
      value: 'private workspace code',
      selectionStart: 0,
      selectionEnd: 7,
      closest: () => ({ dataset: { workspaceSurface: 'editor' } }),
    };
    const drag = {
      target,
      preventDefault: vi.fn(),
      dataTransfer: { clearData: vi.fn() },
    };
    handlers.onDragStart(drag);

    expect(drag.preventDefault).toHaveBeenCalledOnce();
    expect(drag.dataTransfer.clearData).toHaveBeenCalledOnce();
    expect(security.report).toHaveBeenCalledWith('drag_drop_blocked', {
      source: 'editor',
      length: 7,
    });

    const contextMenu = { target, preventDefault: vi.fn() };
    handlers.onContextMenu(contextMenu);
    expect(contextMenu.preventDefault).toHaveBeenCalledOnce();
    expect(security.report).toHaveBeenCalledWith('context_menu_blocked', {
      source: 'editor',
      length: 7,
    });
  });
});
