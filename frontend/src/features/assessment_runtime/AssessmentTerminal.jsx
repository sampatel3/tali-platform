import React, { useEffect, useMemo, useRef } from 'react';
import { Square } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

export const AssessmentTerminal = ({
  events,
  connected,
  disabled = false,
  onInput,
  onResize,
  onStop,
  stopping = false,
  lightMode = false,
}) => {
  const hostRef = useRef(null);
  const terminalRef = useRef(null);
  const fitAddonRef = useRef(null);
  const dataDisposableRef = useRef(null);
  const eventCursorRef = useRef(0);
  const resizeObserverRef = useRef(null);
  const disabledRef = useRef(disabled);

  useEffect(() => {
    disabledRef.current = disabled;
  }, [disabled]);

  const connectionBadge = useMemo(
    () => (connected ? 'CONNECTED' : 'RECONNECTING'),
    [connected],
  );

  const resolveTerminalTheme = () => {
    if (typeof window === 'undefined' || !hostRef.current) {
      return {
        background: '#060b14',
        foreground: '#edf2ff',
        cursor: '#9D00FF',
        selectionBackground: 'rgba(157, 0, 255, 0.22)',
      };
    }
    const styles = window.getComputedStyle(hostRef.current);
    return {
      background: styles.getPropertyValue('--taali-runtime-terminal-bg').trim() || '#060b14',
      foreground: styles.getPropertyValue('--taali-runtime-terminal-text').trim() || '#edf2ff',
      cursor: styles.getPropertyValue('--taali-purple').trim() || '#9D00FF',
      selectionBackground: styles.getPropertyValue('--taali-runtime-selection').trim() || 'rgba(157, 0, 255, 0.22)',
    };
  };

  useEffect(() => {
    if (!hostRef.current || terminalRef.current) return;

    const term = new Terminal({
      fontFamily: 'Menlo, Monaco, Consolas, "Courier New", monospace',
      fontSize: 13,
      convertEol: true,
      cursorBlink: true,
      theme: resolveTerminalTheme(),
      scrollback: 4000,
      disableStdin: false,
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(hostRef.current);
    fitAddon.fit();
    term.focus();
    term.writeln('Starting Claude Code CLI terminal...');

    const sendResize = () => {
      try {
        fitAddon.fit();
      } catch {
        // noop
      }
      onResize?.(term.rows, term.cols);
    };

    const dataDisposable = term.onData((data) => {
      if (disabledRef.current) return;
      onInput?.(data);
    });

    const resizeObserver = new ResizeObserver(() => {
      sendResize();
    });
    resizeObserver.observe(hostRef.current);
    setTimeout(sendResize, 10);

    terminalRef.current = term;
    fitAddonRef.current = fitAddon;
    dataDisposableRef.current = dataDisposable;
    resizeObserverRef.current = resizeObserver;

    return () => {
      try {
        resizeObserver.disconnect();
      } catch {
        // noop
      }
      try {
        dataDisposable.dispose();
      } catch {
        // noop
      }
      try {
        term.dispose();
      } catch {
        // noop
      }
      resizeObserverRef.current = null;
      dataDisposableRef.current = null;
      terminalRef.current = null;
      fitAddonRef.current = null;
      eventCursorRef.current = 0;
    };
  }, [onInput, onResize]);

  useEffect(() => {
    const term = terminalRef.current;
    if (!term) return;
    term.options.theme = resolveTerminalTheme();
  }, [lightMode]);

  useEffect(() => {
    if (!connected) return;
    const term = terminalRef.current;
    if (!term) return;
    const timer = setTimeout(() => {
      try {
        term.focus();
      } catch {
        // noop
      }
    }, 0);
    return () => clearTimeout(timer);
  }, [connected]);

  useEffect(() => {
    const term = terminalRef.current;
    if (!term || !Array.isArray(events)) return;

    while (eventCursorRef.current < events.length) {
      const event = events[eventCursorRef.current];
      eventCursorRef.current += 1;
      if (!event) continue;

      if (event.type === 'output') {
        term.write(String(event.data || ''));
        continue;
      }

      if (event.type === 'error') {
        term.writeln(`\r\n[error] ${String(event.message || 'Terminal error')}`);
        continue;
      }

      if (event.type === 'exit') {
        term.writeln('\r\n[terminal exited]');
      }
    }
  }, [events]);

  return (
    <div className="flex h-full flex-col bg-[var(--taali-runtime-terminal-bg)] text-[var(--taali-runtime-terminal-text)]">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--taali-runtime-border)] bg-[var(--taali-runtime-terminal-header)] px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold uppercase tracking-wide">
            Claude Code CLI
          </span>
          <span className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${
            connected
              ? 'border-[var(--taali-success-border)] bg-[var(--taali-success-soft)] text-[var(--taali-success)]'
              : 'border-[var(--taali-warning-border)] bg-[var(--taali-warning-soft)] text-[var(--taali-warning)]'
          }`}>
            {connectionBadge}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="rounded-[var(--taali-radius-control)] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] px-2 py-1 font-mono text-[11px] text-[var(--taali-danger)] transition-colors hover:border-[var(--taali-danger)] disabled:opacity-50"
            onClick={onStop}
            disabled={stopping}
          >
            <span className="inline-flex items-center gap-1">
              <Square size={10} />
              {stopping ? 'Stopping...' : 'Stop'}
            </span>
          </button>
        </div>
      </div>
      <div
        ref={hostRef}
        className="flex-1 overflow-hidden"
        tabIndex={0}
        onMouseDown={() => {
          try {
            terminalRef.current?.focus();
          } catch {
            // noop
          }
        }}
        onPaste={(event) => {
          if (disabledRef.current) return;
          const text = event.clipboardData?.getData('text');
          if (!text) return;
          event.preventDefault();
          const term = terminalRef.current;
          if (term && typeof term.paste === 'function') {
            term.paste(text);
            return;
          }
          onInput?.(text);
        }}
      />
    </div>
  );
};
