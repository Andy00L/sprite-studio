import { useState } from 'react';
import type { JSX } from 'react';
import { Backdrop } from '../chrome/Backdrop';
import { SpriteSheet } from '../sprites/SpriteSheet';
import { useStore } from '../../state/store';
import { characterSheetUrl } from '../../lib/assets';
import { toneForOrdinal } from '../../lib/design';
import { RefDropZone } from '../widgets/RefDropZone';
import type { UploadResult } from '../../lib/uploads';
import type { Character } from '../../types/sprite';

interface Props {
  character: Character;
  onClose: () => void;
}

export function CharacterEditPopover({ character, onClose }: Props): JSX.Element {
  const project = useStore((s) => s.project);
  const shots = useStore((s) => s.shots);
  const setCharacterField = useStore((s) => s.setCharacterField);
  const removeCharacter = useStore((s) => s.removeCharacter);
  const editShotField = useStore((s) => s.editShotField);
  const editCharacterRefs = useStore((s) => s.editCharacterRefs);

  const projectId = project?.id ?? '';
  const tone = toneForOrdinal(character.ordinal);
  const hasReal = Boolean(character.master_sheet_path);
  const existingRefs = character.reference_image_path
    ? [character.reference_image_path]
    : [];

  const [persona, setPersona] = useState(character.persona);
  const [visualTweak, setVisualTweak] = useState('');
  const [newRefs, setNewRefs] = useState<string[]>([]);
  const [appearsIn, setAppearsIn] = useState<Set<string>>(
    new Set(
      shots
        .filter((s) => s.characters_present.includes(character.id))
        .map((s) => s.id),
    ),
  );
  const [busy, setBusy] = useState(false);

  const personaChanged = persona !== character.persona;
  const tweakChanged = visualTweak.trim().length > 0;

  const save = async (): Promise<void> => {
    setBusy(true);
    try {
      if (personaChanged) {
        await setCharacterField(character.id, 'persona', persona);
      }
      if (newRefs.length > 0) {
        // New refs imply re-anchoring; route through the dedicated path so
        // the orchestrator forces regenerate-with-refs instead of trying a
        // surgical edit on the previous sheet.
        await editCharacterRefs(
          character.id,
          tweakChanged ? visualTweak.trim() : 'regenerate with new ref',
          newRefs,
        );
      } else if (tweakChanged) {
        await setCharacterField(
          character.id,
          'visual_description',
          visualTweak.trim(),
        );
      }
      // Sync appears-in deltas. Each delta is a separate edit_shot_field
      // call; partial failure leaves the rest applied. Document on the
      // failure modes list.
      for (const shot of shots) {
        const wasIn = shot.characters_present.includes(character.id);
        const nowIn = appearsIn.has(shot.id);
        if (wasIn === nowIn) continue;
        const next = nowIn
          ? [...shot.characters_present, character.id]
          : shot.characters_present.filter((id) => id !== character.id);
        await editShotField(
          shot.id,
          'characters_present',
          JSON.stringify(next),
        );
      }
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async (): Promise<void> => {
    setBusy(true);
    try {
      await setCharacterField(
        character.id,
        'appearance',
        visualTweak.trim() || 'regenerate',
      );
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const del = async (): Promise<void> => {
    if (!confirm(`Delete character "${character.name}"? This cannot be undone.`)) {
      return;
    }
    setBusy(true);
    try {
      await removeCharacter(character.id);
      onClose();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Backdrop onClose={onClose}>
      <div
        className="popover"
        style={{ width: 'min(540px, 100%)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <button className="popover-close" onClick={onClose} aria-label="close">
          ✕
        </button>

        <div
          style={{
            display: 'flex',
            gap: 16,
            alignItems: 'flex-start',
            marginBottom: 18,
            paddingRight: 32,
          }}
        >
          {hasReal ? (
            <img
              src={characterSheetUrl(
                projectId,
                character.id,
                character.updated_at ?? undefined,
              )}
              alt={character.name}
              width={84}
              height={84}
              style={{
                display: 'block',
                borderRadius: 4,
                border: '1px solid var(--rule)',
              }}
            />
          ) : (
            <SpriteSheet tone={tone} size={84} />
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="mono" style={{ fontSize: 9, marginBottom: 4 }}>
              edit · character
            </div>
            <div
              className="serif-it"
              style={{ fontSize: 32, lineHeight: 1, marginBottom: 6 }}
            >
              {character.name}
            </div>
            <div className="mono" style={{ fontSize: 9, marginBottom: 8 }}>
              {character.role ?? 'unset'}
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <span className="pill pill-faint">
                ♪ {character.voice_personality ?? 'auto'}
              </span>
              {character.source === 'reference_image' && (
                <span
                  className="pill pill-accent"
                  title="character was anchored to a reference image"
                >
                  ref
                </span>
              )}
            </div>
          </div>
        </div>

        <SectionLabel hint="~25 words">persona</SectionLabel>
        <textarea
          value={persona}
          onChange={(e) => setPersona(e.target.value)}
          rows={3}
          style={textareaStyle}
        />

        <SectionLabel hint="natural language">visual tweak</SectionLabel>
        <input
          type="text"
          value={visualTweak}
          onChange={(e) => setVisualTweak(e.target.value)}
          placeholder="e.g. add a scar, change jacket color"
          style={inputStyle}
        />

        <SectionLabel hint="re-anchors look · png/jpeg/webp">reference image</SectionLabel>
        <RefDropZone
          projectId={projectId || null}
          initialPaths={existingRefs}
          max={1}
          hint="drop a photo to re-anchor"
          onUploaded={(r: UploadResult) => setNewRefs((cur) => [...cur, r.path])}
        />

        {shots.length > 0 && (
          <>
            <SectionLabel hint="click to toggle">appears in shots</SectionLabel>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {shots.map((s, i) => {
                const on = appearsIn.has(s.id);
                return (
                  <span
                    key={s.id}
                    onClick={() => {
                      const next = new Set(appearsIn);
                      if (next.has(s.id)) next.delete(s.id);
                      else next.add(s.id);
                      setAppearsIn(next);
                    }}
                    className={on ? 'pill pill-accent' : 'pill pill-faint'}
                    style={{ cursor: 'pointer' }}
                  >
                    {on ? '✓' : '+'} {String(i + 1).padStart(2, '0')}
                  </span>
                );
              })}
            </div>
            <div
              className="mono"
              style={{ fontSize: 9, color: 'var(--ink-faint)', marginTop: 6 }}
            >
              in {appearsIn.size} of {shots.length} shots
            </div>
          </>
        )}

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
          <button
            onClick={() => void del()}
            disabled={busy}
            className="cta cta-ghost"
            style={{ color: 'var(--accent)', borderColor: 'var(--accent)' }}
          >
            ⌫ delete
          </button>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => void regenerate()}
              disabled={busy}
              className="cta cta-ghost"
            >
              ↻ regenerate sheet
            </button>
            <button
              onClick={() => void save()}
              disabled={busy}
              className="cta"
            >
              {busy ? '…' : '✓ save'}
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

const textareaStyle = {
  width: '100%',
  fontFamily: 'var(--hand)',
  fontSize: 14,
  padding: 8,
  border: '1px dashed var(--rule)',
  background: 'var(--paper-tint)',
  resize: 'vertical' as const,
  boxSizing: 'border-box' as const,
};

const inputStyle = {
  width: '100%',
  fontFamily: 'var(--hand)',
  fontSize: 14,
  padding: 8,
  border: '1px dashed var(--rule)',
  background: 'var(--paper-tint)',
  boxSizing: 'border-box' as const,
};
