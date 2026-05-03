import { useEffect, useRef, useState } from 'react';
import type { JSX, KeyboardEvent, MouseEvent as ReactMouseEvent } from 'react';
import { useStore } from '../../state/store';

const ROLE_GLYPH = {
  user: '›',
  system: '○',
  assistant: '◆',
} as const;

const ROLE_LABEL = {
  user: 'you',
  system: 'sys',
  assistant: 'ai',
} as const;

const ROLE_COLOR = {
  user: 'var(--ink-soft)',
  system: 'var(--ink-faint)',
  assistant: 'var(--accent)',
} as const;

const STORAGE_KEY = 'sprite.chatDockHeight';
const DEFAULT_HEIGHT = 200;
const MIN_HEIGHT = 120;

export function ChatDock(): JSX.Element {
  const messages = useStore((s) => s.chat.messages);
  const draft = useStore((s) => s.chat.draft);
  const setDraft = useStore((s) => s.setDraft);
  const sendRaw = useStore((s) => s.sendRaw);
  const isStreaming = useStore((s) => s.chat.isStreaming);

  const [height, setHeight] = useState<number>(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    const n = saved ? parseInt(saved, 10) : NaN;
    return Number.isFinite(n) && n >= MIN_HEIGHT ? n : DEFAULT_HEIGHT;
  });

  const dragState = useRef<{ startY: number; startH: number } | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const onHandleMouseDown = (e: ReactMouseEvent) => {
    e.preventDefault();
    dragState.current = { startY: e.clientY, startH: height };
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  };

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const ds = dragState.current;
      if (!ds) return;
      const delta = ds.startY - e.clientY;
      const maxH = Math.floor(window.innerHeight * 0.6);
      const next = Math.max(MIN_HEIGHT, Math.min(maxH, ds.startH + delta));
      setHeight(next);
    };
    const onUp = () => {
      if (!dragState.current) return;
      dragState.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      localStorage.setItem(STORAGE_KEY, String(height));
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [height]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft('');
    void sendRaw(text);
  };

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const recent = messages.slice(-30);

  return (
    <div
      style={{
        flex: '0 0 auto',
        height,
        borderTop: '1.5px solid var(--rule)',
        background: 'var(--paper)',
        padding: '10px 22px 12px',
        fontFamily: 'var(--mono)',
        position: 'relative',
        zIndex: 5,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
      }}
    >
      <div
        onMouseDown={onHandleMouseDown}
        title="drag to resize chat"
        style={{
          position: 'absolute',
          top: -2,
          left: 0,
          right: 0,
          height: 6,
          cursor: 'ns-resize',
          zIndex: 10,
        }}
      />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          fontSize: 9,
          textTransform: 'uppercase',
          letterSpacing: '0.14em',
          color: 'var(--ink-faint)',
          marginBottom: 8,
          borderBottom: '1px dashed var(--rule-soft)',
          paddingBottom: 6,
          flexShrink: 0,
        }}
      >
        <span>chat</span>
        <span>·</span>
        <span>/commands</span>
        <span>·</span>
        <span style={{ color: isStreaming ? 'var(--accent)' : 'var(--good)' }}>
          {isStreaming ? '○ working' : '● ready'}
        </span>
        <span>·</span>
        <span>8643</span>
        <span style={{ flex: 1 }} />
        <span>↵ send</span>
      </div>
      <div
        ref={listRef}
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          fontSize: 12,
          marginBottom: 8,
          flex: '1 1 auto',
          overflowY: 'auto',
          minHeight: 0,
          paddingRight: 4,
        }}
      >
        {recent.length === 0 && (
          <div className="hand" style={{ fontSize: 14, color: 'var(--ink-faint)' }}>
            type a /command or chat with the agent…
          </div>
        )}
        {recent.map((m) => (
          <div
            key={m.id}
            style={{ display: 'flex', gap: 12, alignItems: 'flex-start', lineHeight: 1.25 }}
          >
            <span
              style={{
                fontFamily: 'var(--mono)',
                fontSize: 9,
                textTransform: 'uppercase',
                color: ROLE_COLOR[m.role],
                letterSpacing: '0.12em',
                minWidth: 44,
                flex: '0 0 44px',
                paddingTop: 3,
              }}
            >
              {ROLE_LABEL[m.role]} {ROLE_GLYPH[m.role]}
            </span>
            <span
              className="hand"
              style={{
                fontSize: 15,
                color: 'var(--ink)',
                flex: 1,
                minWidth: 0,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {m.text}
            </span>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexShrink: 0 }}>
        <span style={{ color: 'var(--accent)', fontSize: 14 }}>›</span>
        <input
          type="text"
          value={draft}
          placeholder="type a slash command, or describe an edit…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKey}
          disabled={isStreaming}
          style={{
            flex: 1,
            fontFamily: 'var(--hand)',
            fontSize: 16,
            background: 'transparent',
            border: 'none',
            padding: '4px 0',
            borderBottom: '1px dashed var(--rule-soft)',
            borderRadius: 0,
          }}
        />
        <span
          className="pill pill-faint"
          style={{ borderStyle: 'dashed', opacity: 0.4, cursor: 'not-allowed' }}
          title="reference image attach coming soon"
        >
          + ref
        </span>
      </div>
    </div>
  );
}
