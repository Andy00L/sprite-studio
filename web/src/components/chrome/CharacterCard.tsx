import type { JSX } from 'react';
import { SpriteSheet } from '../sprites/SpriteSheet';
import { characterSheetUrl } from '../../lib/assets';
import { toneForOrdinal } from '../../lib/design';
import type { Character } from '../../types/sprite';

interface Props {
  character: Character;
  projectId: string;
  onClick?: () => void;
  compact?: boolean;
}

export function CharacterCard({
  character,
  projectId,
  onClick,
  compact = false,
}: Props): JSX.Element {
  const tone = toneForOrdinal(character.ordinal);
  const hasReal = Boolean(character.master_sheet_path);
  const sheetSize = compact ? 56 : 64;
  const persona = character.persona ?? '';
  return (
    <div
      className="box-hand pressy"
      onClick={onClick}
      style={{
        padding: compact ? 10 : 12,
        display: 'flex',
        gap: 10,
        background: 'var(--paper)',
        width: compact ? 200 : 220,
      }}
    >
      {hasReal ? (
        <img
          src={characterSheetUrl(projectId, character.id, character.updated_at ?? undefined)}
          alt={character.name}
          width={sheetSize}
          height={sheetSize}
          style={{ display: 'block', borderRadius: 3 }}
        />
      ) : (
        <SpriteSheet tone={tone} size={sheetSize} />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="serif-it" style={{ fontSize: 22, lineHeight: 1, marginBottom: 4 }}>
          {character.name}
        </div>
        <div className="mono" style={{ fontSize: 8, marginBottom: 4 }}>
          {character.role ?? ''}
        </div>
        <div
          className="hand"
          style={{ fontSize: 13, lineHeight: 1.2, color: 'var(--ink-soft)' }}
        >
          {persona.slice(0, 80)}
          {persona.length > 80 ? '...' : ''}
        </div>
        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          <span className="pill pill-faint" style={{ fontSize: 8, padding: '2px 6px' }}>
            ♪ voice
          </span>
          {character.source === 'reference_image' && (
            <span
              className="pill pill-accent"
              style={{ fontSize: 8, padding: '2px 6px' }}
              title="anchored to a reference image"
            >
              ref
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
