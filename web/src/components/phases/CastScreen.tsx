import type { JSX } from 'react';
import { useStore } from '../../state/store';
import { PhaseCanvas } from './PhaseCanvas';
import { CharacterCard } from '../chrome/CharacterCard';

export function CastScreen(): JSX.Element {
  const characters = useStore((s) => s.characters);
  const project = useStore((s) => s.project);
  const openPopover = useStore((s) => s.openPopover);

  const projectId = project?.id ?? '';

  return (
    <PhaseCanvas phase="cast">
      <div
        style={{
          position: 'absolute',
          top: 24,
          left: 36,
          right: 36,
          bottom: 200,
          display: 'flex',
          gap: 16,
          flexWrap: 'wrap',
          alignItems: 'flex-start',
          alignContent: 'flex-start',
          overflowY: 'auto',
          paddingRight: 8,
        }}
      >
        {characters.map((c) => (
          <CharacterCard
            key={c.id}
            character={c}
            projectId={projectId}
            onClick={() =>
              openPopover({ kind: 'character-edit', characterId: c.id })
            }
          />
        ))}
        <div
          className="box-soft pressy"
          onClick={() => openPopover({ kind: 'character-add' })}
          style={{
            width: 160,
            display: 'grid',
            placeItems: 'center',
            padding: 16,
            color: 'var(--ink-faint)',
            textAlign: 'center',
            minHeight: 140,
            cursor: 'pointer',
          }}
        >
          <div className="serif-it" style={{ fontSize: 22, color: 'var(--accent)' }}>
            + add character
          </div>
        </div>
      </div>

      <div
        style={{
          position: 'absolute',
          left: '50%',
          transform: 'translateX(-50%)',
          bottom: 80,
          textAlign: 'center',
          maxWidth: 600,
        }}
      >
        {characters.length === 0 ? (
          <div className="sticky-note" style={{ display: 'inline-block', maxWidth: 420 }}>
            cast generation in progress. check the chat below for status; when characters appear,
            they show up here.
          </div>
        ) : (
          <>
            <div className="serif-it" style={{ fontSize: 36, marginBottom: 6 }}>
              approve cast →
            </div>
            <div className="hand" style={{ fontSize: 18, color: 'var(--ink-soft)' }}>
              I'll write a shot list and reference stills.
            </div>
            <div className="sticky-note" style={{ display: 'inline-block', marginTop: 16 }}>
              ↗ click a character to edit persona,
              <br />
              or type /sprite_edit_character below.
            </div>
          </>
        )}
      </div>
    </PhaseCanvas>
  );
}
