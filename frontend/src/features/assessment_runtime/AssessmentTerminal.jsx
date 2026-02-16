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

  useEffect(() => {
    if (!hostRef.current || terminalRef.current) return;

    const term = new Terminal({
      fontFamily: 'Menlo, Monaco, Consolas, "Courier New", monospace',
      fontSize: 13,
      convertEol: true,
      cursorBlink: true,
      theme: {
        background: '#000000',
        foreground: '#e5e7eb',
        cursor: '#9D00FF',
        selectionBackground: '#4b556399',
      },
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
    <div className="h-full flex flex-col bg-black text-white">
      <div className="border-b-2 border-black bg-[#0f0f0f] px-3 py-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold uppercase tracking-wide">
            Claude Code CLI
          </span>
          <span className={`font-mono text-[11px] px-2 py-0.5 border ${connected ? 'border-green-700 text-green-400' : 'border-amber-700 text-amber-300'}`}>
            {connectionBadge}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="border border-red-700 px-2 py-1 font-mono text-[11px] text-red-200 hover:bg-red-900/40 disabled:opacity-50"
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
        onMouseDown={() => {
          try {
            terminalRef.current?.focus();
          } catch {
            // noop
          }
        }}
      />
    </div>
  );
};
