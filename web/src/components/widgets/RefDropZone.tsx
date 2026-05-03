import { useEffect, useRef, useState } from 'react';
import type { JSX, ChangeEvent, DragEvent } from 'react';
import {
  uploadReference,
  ACCEPT_MIMES,
  MAX_UPLOAD_BYTES,
  type UploadHandle,
  type UploadResult,
  type UploadError,
} from '../../lib/uploads';

interface Props {
  // null => pre-project mode: files are buffered locally and the parent is
  // expected to upload them once a project exists (BriefScreen does this
  // post-/sprite_new). Non-null => post-project mode: files start uploading
  // immediately on pick.
  projectId: string | null;
  onUploaded?: (result: UploadResult) => void;
  onPendingChange?: (files: File[]) => void;
  initialPaths?: string[];
  max?: number;
  hint?: string;
  disabled?: boolean;
}

interface PendingFile {
  id: string;
  file: File;
  progress: number;
  status: 'buffered' | 'uploading' | 'done' | 'failed' | 'aborted';
  result?: UploadResult;
  error?: UploadError;
  handle?: UploadHandle;
}

const ACCEPT_ATTR = Array.from(ACCEPT_MIMES).join(',');

function fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`;
}

function newId(): string {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function RefDropZone({
  projectId,
  onUploaded,
  onPendingChange,
  initialPaths = [],
  max = 8,
  hint = 'drop ref images · png/jpeg/webp · 5mb max',
  disabled = false,
}: Props): JSX.Element {
  const [items, setItems] = useState<PendingFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Guards the onUploaded callback against firing after parent unmount —
  // an in-flight XHR completion otherwise sets stale state and can call
  // a no-longer-mounted parent callback.
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  function startUpload(it: PendingFile, pid: string): PendingFile {
    const handle = uploadReference(pid, it.file, (loaded, total) => {
      if (!aliveRef.current) return;
      setItems((cur) =>
        cur.map((x) =>
          x.id === it.id ? { ...x, progress: total > 0 ? loaded / total : 0 } : x,
        ),
      );
    });
    handle.promise
      .then((result) => {
        if (!aliveRef.current) return;
        setItems((cur) =>
          cur.map((x) =>
            x.id === it.id
              ? { ...x, status: 'done', progress: 1, result, handle: undefined }
              : x,
          ),
        );
        onUploaded?.(result);
      })
      .catch((error: UploadError) => {
        if (!aliveRef.current) return;
        setItems((cur) =>
          cur.map((x) =>
            x.id === it.id
              ? {
                  ...x,
                  status: error.code === 'aborted' ? 'aborted' : 'failed',
                  error,
                  handle: undefined,
                }
              : x,
          ),
        );
      });
    return { ...it, status: 'uploading', progress: 0, handle };
  }

  function intake(files: File[]) {
    if (disabled) return;
    setItems((current) => {
      const seen = new Set(current.map((it) => fileKey(it.file)));
      const next = [...current];
      for (const file of files) {
        if (next.length >= max) break;
        const key = fileKey(file);
        if (seen.has(key)) continue;
        seen.add(key);
        // Client-side gates so obviously-bad files are rejected without
        // hitting the network or the server.
        if (!ACCEPT_MIMES.has(file.type)) {
          next.push({
            id: newId(),
            file,
            progress: 0,
            status: 'failed',
            error: {
              code: 'invalid_mime',
              message: `unsupported type ${file.type || '(none)'}`,
            },
          });
          continue;
        }
        if (file.size > MAX_UPLOAD_BYTES) {
          next.push({
            id: newId(),
            file,
            progress: 0,
            status: 'failed',
            error: {
              code: 'too_large',
              message: `${(file.size / 1024 / 1024).toFixed(1)}MB > 5MB`,
            },
          });
          continue;
        }
        const fresh: PendingFile = {
          id: newId(),
          file,
          progress: 0,
          status: 'buffered',
        };
        next.push(projectId ? startUpload(fresh, projectId) : fresh);
      }
      const bufferedFiles = next
        .filter((it) => it.status === 'buffered' || it.status === 'uploading')
        .map((it) => it.file);
      onPendingChange?.(bufferedFiles);
      return next;
    });
  }

  function onPick(e: ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list) return;
    intake(Array.from(list));
    if (inputRef.current) inputRef.current.value = '';
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const list = e.dataTransfer?.files;
    if (!list || list.length === 0) return;
    intake(Array.from(list));
  }

  function onDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (!disabled) setDragOver(true);
  }

  function onDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
  }

  function remove(id: string) {
    setItems((cur) => {
      const it = cur.find((x) => x.id === id);
      if (it?.handle) it.handle.abort();
      const next = cur.filter((x) => x.id !== id);
      const bufferedFiles = next
        .filter((x) => x.status === 'buffered' || x.status === 'uploading')
        .map((x) => x.file);
      onPendingChange?.(bufferedFiles);
      return next;
    });
  }

  const totalShown = items.length + initialPaths.length;
  const atCap = totalShown >= max;

  return (
    <div
      className="dashed-accent"
      onDrop={onDrop}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      style={{
        padding: 12,
        background: dragOver ? 'var(--accent-tint)' : 'var(--paper)',
        opacity: disabled ? 0.4 : 1,
        cursor: disabled || atCap ? 'not-allowed' : 'pointer',
        transition: 'background 80ms ease-in-out',
      }}
      onClick={(e) => {
        if (disabled || atCap) return;
        if (e.target === e.currentTarget) inputRef.current?.click();
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        multiple
        onChange={onPick}
        style={{ display: 'none' }}
      />

      {totalShown === 0 ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            color: 'var(--accent)',
            pointerEvents: 'none',
          }}
        >
          <span className="serif-it" style={{ fontSize: 18, lineHeight: 1 }}>
            +
          </span>
          <div style={{ textAlign: 'center' }}>
            <div className="hand" style={{ fontSize: 13 }}>{hint}</div>
            <div className="mono" style={{ fontSize: 8, marginTop: 2 }}>
              0/{max} · click or drop
            </div>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {initialPaths.map((p) => (
            <RefRow key={p} label={shortPath(p)} status="done" />
          ))}
          {items.map((it) => (
            <RefRow
              key={it.id}
              label={it.file.name}
              status={it.status}
              progress={it.progress}
              error={it.error?.message}
              onRemove={() => remove(it.id)}
            />
          ))}
          {!atCap && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                inputRef.current?.click();
              }}
              className="hand"
              style={{
                marginTop: 4,
                background: 'transparent',
                border: '1px dashed var(--rule-soft)',
                color: 'var(--accent)',
                cursor: 'pointer',
                padding: '4px 8px',
                fontSize: 11,
                alignSelf: 'flex-start',
              }}
            >
              + add another ({totalShown}/{max})
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface RefRowProps {
  label: string;
  status: PendingFile['status'];
  progress?: number;
  error?: string;
  onRemove?: () => void;
}

function RefRow({ label, status, progress = 0, error, onRemove }: RefRowProps): JSX.Element {
  const tag = (() => {
    if (status === 'done') return { sym: '✓', color: 'var(--accent)' };
    if (status === 'failed') return { sym: '✕', color: '#b00' };
    if (status === 'aborted') return { sym: '⌫', color: 'var(--ink-faint)' };
    if (status === 'uploading') return { sym: `${Math.round(progress * 100)}%`, color: 'var(--ink-soft)' };
    return { sym: '…', color: 'var(--ink-soft)' };
  })();
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 6px',
        background: 'var(--paper-tint)',
        border: '1px dashed var(--rule-soft)',
      }}
    >
      <span className="mono" style={{ fontSize: 9, color: tag.color, minWidth: 32 }}>
        {tag.sym}
      </span>
      <span
        className="hand"
        style={{
          flex: 1,
          fontSize: 12,
          color: 'var(--ink)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
        title={label}
      >
        {label}
        {error && (
          <span style={{ color: '#b00', marginLeft: 8, fontSize: 11 }}>· {error}</span>
        )}
      </span>
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--ink-faint)',
            cursor: 'pointer',
            fontSize: 11,
            padding: 2,
          }}
          aria-label="remove"
        >
          ✕
        </button>
      )}
    </div>
  );
}

function shortPath(p: string): string {
  const parts = p.split('/');
  const last = parts[parts.length - 1] ?? p;
  return `ref · ${last.slice(0, 12)}…`;
}
