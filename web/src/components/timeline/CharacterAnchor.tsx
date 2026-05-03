// React 19 ref-as-prop, per the v19 release notes
// (https://react.dev/blog/2024/12/05/react-19). forwardRef is no longer
// required; ref is just a prop on function components.

import type { JSX, Ref } from 'react';
import type { Character } from '../../types/sprite';
import { SpriteSheet } from '../sprites/SpriteSheet';
import { characterSheetUrl } from '../../lib/assets';
import { toneForOrdinal } from '../../lib/design';

interface Props {
  character: Character;
  projectId: string;
  onClick: () => void;
  ref?: Ref<HTMLDivElement>;
}

export function CharacterAnchor({ character, projectId, onClick, ref }: Props): JSX.Element {
  const tone = toneForOrdinal(character.ordinal);
  const hasReal = Boolean(character.master_sheet_path);
  const persona = character.persona ?? '';
  return (
    <div
      ref={ref}
      className="box-hand pressy"
      onClick={onClick}
      style={{ padding: 8, display: 'flex', gap: 8, background: 'var(--paper)', flex: '0 0 auto' }}
    >
      {hasReal ? (
        <img
          src={characterSheetUrl(projectId, character.id, character.updated_at ?? undefined)}
          alt={character.name}
          width={48}
          height={48}
          style={{ display: 'block', borderRadius: 3 }}
        />
      ) : (
        <SpriteSheet tone={tone} size={48} />
      )}
      <div style={{ minWidth: 0 }}>
        <div className="serif-it" style={{ fontSize: 18, lineHeight: 1 }}>
          {character.name}
        </div>
        <div className="mono" style={{ fontSize: 8, marginTop: 2 }}>
          {character.role ?? ''}
        </div>
        <div
          className="hand"
          style={{
            fontSize: 11,
            color: 'var(--ink-soft)',
            lineHeight: 1.1,
            marginTop: 2,
            maxWidth: 130,
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {persona.slice(0, 80)}
          {persona.length > 80 ? '...' : ''}
        </div>
        <div style={{ display: 'flex', gap: 3, marginTop: 4 }}>
          <span className="pill pill-faint" style={{ fontSize: 7, padding: '1px 5px' }}>
            {'♪ voice'}
          </span>
        </div>
      </div>
    </div>
  );
}
