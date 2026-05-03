import { useState } from 'react';
import type { JSX } from 'react';
import { Backdrop } from '../chrome/Backdrop';
import { useStore } from '../../state/store';
import { CHARACTER_ROLES } from '../../lib/constraints';
import { RefDropZone } from '../widgets/RefDropZone';
import type { UploadResult } from '../../lib/uploads';

interface Props {
  onClose: () => void;
}

export function CharacterAddPopover({ onClose }: Props): JSX.Element {
  const addCharacter = useStore((s) => s.addCharacter);
  const projectId = useStore((s) => s.project?.id ?? null);

  const [name, setName] = useState('');
  const [role, setRole] = useState<string>('supporting');
  const [description, setDescription] = useState('');
  const [refs, setRefs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const canSubmit = name.trim().length > 0 && description.trim().length > 0;

  const submit = async (): Promise<void> => {
    if (!canSubmit) return;
    setBusy(true);
    try {
      // Pack the structured fields into one descriptor for the LLM. The
      // orchestrator's add_character path parses out the name and visual
      // cues from prose, so leading with name and role gives it a stable
      // anchor.
      const descriptor = `${name.trim()} (${role}): ${description.trim()}`;
      await addCharacter(descriptor, refs);
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Backdrop onClose={onClose}>
      <div
        className="popover"
        style={{ width: 'min(460px, 100%)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <button className="popover-close" onClick={onClose} aria-label="close">
          ✕
        </button>

        <div style={{ marginBottom: 14, paddingRight: 32 }}>
          <div className="mono" style={{ fontSize: 9, marginBottom: 4 }}>
            new · character
          </div>
          <div className="serif-it" style={{ fontSize: 32, lineHeight: 1 }}>
            add to cast<span style={{ color: 'var(--accent)' }}>.</span>
          </div>
        </div>

        <SectionLabel>name</SectionLabel>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. mei lin"
          style={{
            width: '100%',
            fontFamily: 'var(--serif)',
            fontStyle: 'italic',
            fontSize: 22,
            padding: 6,
            border: '1px dashed var(--rule)',
            background: 'var(--paper-tint)',
            boxSizing: 'border-box',
          }}
        />

        <SectionLabel>role</SectionLabel>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {CHARACTER_ROLES.map((r) => (
            <span
              key={r}
              onClick={() => setRole(r)}
              className={role === r ? 'pill pill-accent' : 'pill'}
              style={{ cursor: 'pointer' }}
            >
              {r.replace('_', ' ')}
            </span>
          ))}
        </div>

        <SectionLabel hint="who are they?">description</SectionLabel>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={4}
          placeholder="e.g. 40s, greying at the temples. used to be a boxer."
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

        <SectionLabel hint="anchor look · png/jpeg/webp">reference images</SectionLabel>
        <RefDropZone
          projectId={projectId}
          max={3}
          hint="drop a photo to lock the look"
          onUploaded={(r: UploadResult) => setRefs((cur) => [...cur, r.path])}
        />

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            marginTop: 18,
          }}
        >
          <button
            onClick={onClose}
            disabled={busy}
            className="cta cta-ghost"
          >
            cancel
          </button>
          <button
            onClick={() => void submit()}
            disabled={busy || !canSubmit}
            className="cta"
          >
            {busy ? '…' : '✓ add'}
          </button>
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
