import { useState } from 'react';
import type { JSX } from 'react';
import { Backdrop } from '../chrome/Backdrop';
import { useStore } from '../../state/store';
import {
  ALLOWED_CAMERAS,
  DURATION_MIN,
  DURATION_MAX,
} from '../../lib/constraints';
import type { Shot, DialogEntry } from '../../types/sprite';

type Mode =
  | { kind: 'edit'; shot: Shot }
  | { kind: 'add'; insertAfterOrdinal: number };

interface Props {
  mode: Mode;
  onClose: () => void;
}

export function ShotEditPopover({ mode, onClose }: Props): JSX.Element {
  const characters = useStore((s) => s.characters);
  const editShotField = useStore((s) => s.editShotField);
  const deleteShot = useStore((s) => s.deleteShot);
  const addShot = useStore((s) => s.addShot);

  const seed: Shot | null = mode.kind === 'edit' ? mode.shot : null;
  const [action, setAction] = useState(seed?.action ?? '');
  const [duration, setDuration] = useState(seed?.duration_seconds ?? 8);
  const [camera, setCamera] = useState(
    seed?.camera ?? ALLOWED_CAMERAS[0] ?? 'static wide',
  );
  const [presentSet, setPresentSet] = useState<Set<string>>(
    new Set(seed?.characters_present ?? []),
  );
  const [dialog, setDialog] = useState<DialogEntry[]>(
    seed?.character_dialog ? [...seed.character_dialog] : [],
  );
  const [busy, setBusy] = useState(false);

  const ordinalLabel =
    mode.kind === 'edit'
      ? String(mode.shot.ordinal).padStart(2, '0')
      : String(mode.insertAfterOrdinal + 1).padStart(2, '0');

  const save = async (): Promise<void> => {
    setBusy(true);
    try {
      if (mode.kind === 'add') {
        await addShot(mode.insertAfterOrdinal + 1, action.trim(), {
          duration: String(duration),
          camera,
          characters: Array.from(presentSet).join('+'),
        });
      } else {
        const s = mode.shot;
        // Per-field surgical edits. Order matters for visual fields
        // (action/camera) which trigger reference-still regen on the
        // server; we await each to keep the regen pipeline serial.
        if (action !== s.action) {
          await editShotField(s.id, 'action', action);
        }
        if (duration !== s.duration_seconds) {
          await editShotField(s.id, 'duration_seconds', String(duration));
        }
        if (camera !== s.camera) {
          await editShotField(s.id, 'camera', camera);
        }
        const oldPresent = new Set(s.characters_present);
        const presentChanged =
          oldPresent.size !== presentSet.size ||
          [...oldPresent].some((id) => !presentSet.has(id));
        if (presentChanged) {
          await editShotField(
            s.id,
            'characters_present',
            JSON.stringify([...presentSet]),
          );
        }
        const oldDialog = JSON.stringify(s.character_dialog ?? []);
        const newDialog = JSON.stringify(dialog);
        if (oldDialog !== newDialog) {
          await editShotField(s.id, 'character_dialog', newDialog);
        }
      }
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const del = async (): Promise<void> => {
    if (mode.kind !== 'edit') return;
    if (!confirm(`Delete shot ${mode.shot.ordinal}? This cannot be undone.`)) {
      return;
    }
    setBusy(true);
    try {
      await deleteShot(mode.shot.id);
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = action.trim().length > 0;

  return (
    <Backdrop onClose={onClose}>
      <div
        className="popover"
        style={{ width: 'min(640px, 100%)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <button className="popover-close" onClick={onClose} aria-label="close">
          ✕
        </button>

        <div style={{ marginBottom: 14, paddingRight: 32 }}>
          <div className="mono" style={{ fontSize: 9, marginBottom: 4 }}>
            {mode.kind === 'add' ? 'new · shot' : 'edit · shot'}
          </div>
          <div className="serif-it" style={{ fontSize: 32, lineHeight: 1 }}>
            shot {ordinalLabel}
            <span style={{ color: 'var(--accent)' }}>.</span>
          </div>
          <div className="mono" style={{ fontSize: 9, marginTop: 6 }}>
            {duration}s · {camera}
          </div>
        </div>

        <SectionLabel hint="action / blocking">what happens</SectionLabel>
        <textarea
          value={action}
          onChange={(e) => setAction(e.target.value)}
          rows={3}
          placeholder="e.g. she slides the receipt across. neither of them looks at it."
          style={{
            width: '100%',
            fontFamily: 'var(--hand)',
            fontSize: 14,
            padding: 8,
            border: '1px dashed var(--rule)',
            background: 'var(--paper-tint)',
            resize: 'vertical',
            boxSizing: 'border-box',
          }}
        />

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 14,
            marginTop: 14,
          }}
        >
          <div>
            <SectionLabel>{`duration · ${duration}s`}</SectionLabel>
            <input
              type="range"
              min={DURATION_MIN}
              max={DURATION_MAX}
              step={1}
              value={duration}
              onChange={(e) => setDuration(parseInt(e.target.value, 10))}
              style={{ width: '100%' }}
            />
            <div
              className="mono"
              style={{ fontSize: 8, color: 'var(--ink-faint)' }}
            >{`${DURATION_MIN}s to ${DURATION_MAX}s`}</div>
          </div>
          <div>
            <SectionLabel>camera</SectionLabel>
            <select
              value={camera}
              onChange={(e) => setCamera(e.target.value)}
              style={{
                width: '100%',
                fontFamily: 'var(--mono)',
                fontSize: 11,
                padding: 6,
                background: 'var(--paper-tint)',
                border: '1px dashed var(--rule)',
                boxSizing: 'border-box',
              }}
            >
              {ALLOWED_CAMERAS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        </div>

        <SectionLabel>characters present</SectionLabel>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {characters.length === 0 && (
            <span
              className="mono"
              style={{ fontSize: 10, color: 'var(--ink-faint)' }}
            >
              no cast yet
            </span>
          )}
          {characters.map((c) => {
            const on = presentSet.has(c.id);
            return (
              <span
                key={c.id}
                onClick={() => {
                  const next = new Set(presentSet);
                  if (next.has(c.id)) next.delete(c.id);
                  else next.add(c.id);
                  setPresentSet(next);
                }}
                className={on ? 'pill pill-accent' : 'pill'}
                style={{ cursor: 'pointer' }}
              >
                {on ? '✓' : '+'} {c.name}
              </span>
            );
          })}
        </div>

        <div
          className="mono"
          style={{
            fontSize: 9,
            marginTop: 14,
            marginBottom: 4,
            color: 'var(--ink-soft)',
            letterSpacing: 1,
            textTransform: 'uppercase',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span>dialog</span>
          {characters.length > 0 && (
            <span
              onClick={() =>
                setDialog([
                  ...dialog,
                  { char_id: characters[0]?.id ?? '', line: '' },
                ])
              }
              className="pill"
              style={{ cursor: 'pointer', fontSize: 9 }}
            >
              + add line
            </span>
          )}
        </div>
        {dialog.length === 0 && (
          <div
            className="hand"
            style={{ fontSize: 13, color: 'var(--ink-faint)' }}
          >
            no dialog
          </div>
        )}
        {dialog.map((d, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              gap: 6,
              marginTop: 6,
              alignItems: 'center',
            }}
          >
            <select
              value={d.char_id}
              onChange={(e) => {
                const next = [...dialog];
                next[i] = { ...d, char_id: e.target.value };
                setDialog(next);
              }}
              style={{
                fontFamily: 'var(--mono)',
                fontSize: 10,
                padding: 4,
                border: '1px solid var(--rule)',
                background: 'var(--paper)',
              }}
            >
              {characters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={d.line}
              onChange={(e) => {
                const next = [...dialog];
                next[i] = { ...d, line: e.target.value };
                setDialog(next);
              }}
              placeholder="“you want pie?”"
              style={{
                flex: 1,
                fontFamily: 'var(--hand)',
                fontSize: 13,
                padding: 6,
                border: '1px dashed var(--rule)',
                background: 'var(--paper-tint)',
                boxSizing: 'border-box',
              }}
            />
            <span
              onClick={() => setDialog(dialog.filter((_, j) => j !== i))}
              className="mono"
              style={{
                cursor: 'pointer',
                color: 'var(--accent)',
                fontSize: 11,
                padding: '0 4px',
              }}
            >
              ✕
            </span>
          </div>
        ))}

        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginTop: 18,
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          {mode.kind === 'edit' ? (
            <button
              onClick={() => void del()}
              disabled={busy}
              className="cta cta-ghost"
              style={{ color: 'var(--accent)', borderColor: 'var(--accent)' }}
            >
              ⌫ delete shot
            </button>
          ) : (
            <span />
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={onClose}
              disabled={busy}
              className="cta cta-ghost"
            >
              cancel
            </button>
            <button
              onClick={() => void save()}
              disabled={busy || !canSubmit}
              className="cta"
            >
              {busy ? '…' : mode.kind === 'add' ? '✓ add' : '✓ save'}
            </button>
          </div>
        </div>
      </div>
    </Backdrop>
  );
}

function SectionLabel({
  children,
  hint,
}: {
  children: string;
  hint?: string;
}): JSX.Element {
  return (
    <div
      className="mono"
      style={{
        fontSize: 9,
        marginTop: 14,
        marginBottom: 4,
        color: 'var(--ink-soft)',
        letterSpacing: 1,
        textTransform: 'uppercase',
      }}
    >
      <span>{children}</span>
      {hint && (
        <>
          <span style={{ opacity: 0.5, margin: '0 6px' }}>·</span>
          <span style={{ textTransform: 'none', letterSpacing: 0 }}>
            {hint}
          </span>
        </>
      )}
    </div>
  );
}
