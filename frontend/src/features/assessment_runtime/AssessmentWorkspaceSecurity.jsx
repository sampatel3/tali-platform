import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { MotionLoop } from '../../shared/motion';

const NOOP_SECURITY = Object.freeze({
  enabled: false,
  sessionMarker: '',
  notice: '',
  copy: () => false,
  paste: () => ({ text: '', blocked: false }),
  report: () => {},
  announce: () => {},
});

const AssessmentWorkspaceSecurityContext = createContext(NOOP_SECURITY);

const safeRandomSegment = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID().replace(/-/g, '').slice(0, 10).toUpperCase();
    } catch {
      // Fall through for older/insecure browser contexts.
    }
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
    .slice(-10)
    .toUpperCase();
};

export const createOpaqueWorkspaceMarker = (assessmentId) => {
  const numericId = Number(assessmentId);
  const assessmentSegment = Number.isSafeInteger(numericId) && numericId > 0
    ? `A${numericId.toString(36).toUpperCase()}`
    : 'SESSION';
  return `WS-${assessmentSegment}-${safeRandomSegment().slice(0, 4)}`;
};

export const selectedTextFromTarget = (target) => {
  if (target && typeof target.value === 'string') {
    const start = Number(target.selectionStart);
    const end = Number(target.selectionEnd);
    if (Number.isInteger(start) && Number.isInteger(end) && end > start) {
      return target.value.slice(start, end);
    }
  }
  if (typeof window !== 'undefined' && typeof window.getSelection === 'function') {
    return String(window.getSelection()?.toString() || '');
  }
  return '';
};

const surfaceFromTarget = (target, fallback = 'workspace') => {
  if (target && typeof target.closest === 'function') {
    return target.closest('[data-workspace-surface]')?.dataset?.workspaceSurface || fallback;
  }
  return fallback;
};

export const useAssessmentWorkspaceSecurity = ({
  enabled,
  sessionMarker,
  emitEvent,
  resetKey,
}) => {
  const clipboardRef = useRef('');
  const noticeTimerRef = useRef(null);
  const [notice, setNotice] = useState('');

  const announce = useCallback((message) => {
    setNotice(String(message || ''));
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
    if (message) {
      noticeTimerRef.current = setTimeout(() => setNotice(''), 5000);
    }
  }, []);

  useEffect(() => () => {
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
  }, []);

  useEffect(() => {
    clipboardRef.current = '';
    setNotice('');
  }, [resetKey]);

  const report = useCallback((eventType, metadata = {}) => {
    if (!enabled || typeof emitEvent !== 'function') return;
    emitEvent(eventType, metadata);
  }, [emitEvent, enabled]);

  const copy = useCallback((text, { surface = 'workspace', operation = 'copy', filePath = '' } = {}) => {
    if (!enabled) return false;
    const value = String(text || '');
    if (!value) {
      announce('Select workspace content before copying.');
      return false;
    }
    clipboardRef.current = value;
    report(operation === 'cut' ? 'cut_attempt' : 'copy_attempt', {
      source: surface,
      length: value.length,
      ...(filePath ? { file_path: filePath } : {}),
    });
    announce(`${operation === 'cut' ? 'Cut' : 'Copied'} to the workspace clipboard. It stays inside this assessment.`);
    return true;
  }, [announce, enabled, report]);

  const paste = useCallback(({
    surface = 'workspace',
    externalCharacterCount = 0,
    filePath = '',
  } = {}) => {
    if (!enabled) return { text: '', blocked: false };
    const text = clipboardRef.current;
    if (!text) {
      report('external_paste_blocked', {
        source: surface,
        length: Math.max(0, Number(externalCharacterCount) || 0),
        ...(filePath ? { file_path: filePath } : {}),
      });
      announce('External paste is unavailable. Copy content inside this workspace first, or contact support for an accommodation.');
      return { text: '', blocked: true };
    }
    report('internal_paste', {
      source: surface,
      length: text.length,
      ...(filePath ? { file_path: filePath } : {}),
    });
    announce('Pasted from the workspace clipboard.');
    return { text, blocked: false };
  }, [announce, enabled, report]);

  return useMemo(() => ({
    enabled: Boolean(enabled),
    sessionMarker,
    notice,
    copy,
    paste,
    report,
    announce,
  }), [announce, copy, enabled, notice, paste, report, sessionMarker]);
};

export const AssessmentWorkspaceSecurityProvider = ({ value, children }) => (
  <AssessmentWorkspaceSecurityContext.Provider value={value || NOOP_SECURITY}>
    {children}
  </AssessmentWorkspaceSecurityContext.Provider>
);

export const useWorkspaceSecurity = () => useContext(AssessmentWorkspaceSecurityContext);

export const WorkspaceSecurityBanner = ({ supportHref }) => {
  const security = useWorkspaceSecurity();
  if (!security.enabled) return null;
  return (
    <div
      className="assessment-workspace-security-banner"
      data-testid="assessment-workspace-security-banner"
      role="note"
    >
      <div>
        <span className="assessment-workspace-security-banner__label">Workspace controls</span>
        <span role="status" aria-live="polite" aria-atomic="true" data-testid="assessment-workspace-security-status">
          {security.notice || 'Copy and paste use an in-workspace clipboard. External paste, page save, print, and drag/drop have best-effort browser restrictions; activity signals are advisory.'}
        </span>
      </div>
      <div className="assessment-workspace-security-banner__actions">
        <span className="font-mono" data-testid="assessment-workspace-marker">{security.sessionMarker}</span>
        {supportHref ? <a href={supportHref}>Accessibility or accommodation</a> : null}
      </div>
    </div>
  );
};

export const WorkspaceSecurityWatermark = () => {
  const security = useWorkspaceSecurity();
  if (!security.enabled) return null;
  return (
    <div className="assessment-workspace-watermark" aria-hidden="true" data-testid="assessment-workspace-watermark">
      <MotionLoop as="span" kind="bob" duration={7}>{security.sessionMarker}</MotionLoop>
      <MotionLoop as="span" kind="bob" duration={9} delay={-3}>{security.sessionMarker}</MotionLoop>
      <MotionLoop as="span" kind="bob" duration={11} delay={-6}>{security.sessionMarker}</MotionLoop>
    </div>
  );
};

export const WorkspacePrintBlocker = () => {
  const security = useWorkspaceSecurity();
  if (!security.enabled) return null;
  return (
    <div className="assessment-workspace-print-blocker" aria-hidden="true">
      <strong>Printing is unavailable for this assessment workspace.</strong>
      <span>{security.sessionMarker}</span>
    </div>
  );
};

export const createProtectedRootHandlers = (security) => ({
  onCopy: (event) => {
    if (!security.enabled || event.defaultPrevented) return;
    const text = selectedTextFromTarget(event.target);
    if (!text) return;
    event.preventDefault();
    security.copy(text, { surface: surfaceFromTarget(event.target), operation: 'copy' });
  },
  onCut: (event) => {
    if (!security.enabled || event.defaultPrevented) return;
    const text = selectedTextFromTarget(event.target);
    if (!text) return;
    // Editable surfaces that support cutting manage their own value update.
    // At the root we only prevent export and retain a workspace copy.
    event.preventDefault();
    security.copy(text, { surface: surfaceFromTarget(event.target), operation: 'cut' });
  },
  onPaste: (event) => {
    if (!security.enabled || event.defaultPrevented) return;
    const externalCharacterCount = String(event.clipboardData?.getData?.('text/plain') || '').length;
    event.preventDefault();
    security.paste({
      surface: surfaceFromTarget(event.target),
      externalCharacterCount,
    });
  },
  onDragOver: (event) => {
    if (!security.enabled) return;
    event.preventDefault();
  },
  onDragStart: (event) => {
    if (!security.enabled) return;
    event.preventDefault();
    try {
      event.dataTransfer?.clearData?.();
    } catch {
      // Some browsers expose a read-only DataTransfer during dragstart.
    }
    const textLength = selectedTextFromTarget(event.target).length;
    security.report('drag_drop_blocked', {
      source: surfaceFromTarget(event.target),
      length: textLength,
    });
    security.announce('Dragging content out of the assessment workspace is unavailable.');
  },
  onDrop: (event) => {
    if (!security.enabled) return;
    event.preventDefault();
    const files = Number(event.dataTransfer?.files?.length || 0);
    security.report('drag_drop_blocked', {
      source: surfaceFromTarget(event.target),
      length: files,
    });
    security.announce('Drag and drop is unavailable in this assessment workspace.');
  },
  onContextMenu: (event) => {
    if (!security.enabled) return;
    event.preventDefault();
    security.report('context_menu_blocked', {
      source: surfaceFromTarget(event.target),
      length: selectedTextFromTarget(event.target).length,
    });
    security.announce('The browser context menu is unavailable in this assessment workspace.');
  },
});
