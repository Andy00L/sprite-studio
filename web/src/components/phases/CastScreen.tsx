import { useState, type JSX } from 'react';
import {
  useStore,
  selectIsCastIncomplete,
  selectIsReadOnlyView,
} from '../../state/store';
import { PhaseCanvas } from './PhaseCanvas';
import { CharacterCard } from '../chrome/CharacterCard';

export function CastScreen(): JSX.Element {
  const characters = useStore((s) => s.characters);
  const project = useStore((s) => s.project);
  const openPopover = useStore((s) => s.openPopover);
  const readOnly = useStore(selectIsReadOnlyView);
  const castIncomplete = useStore(selectIsCastIncomplete);
  const castErrors = useStore((s) => s.castErrors);
  const repairCast = useStore((s) => s.repairCast);
  const [repairing, setRepairing] = useState(false);

  const projectId = project?.id ?? '';

  const onRepair = async (): Promise<void> => {
    if (repairing || !projectId) return;
    setRepairing(true);
    try {
      await repairCast(projectId);
    } finally {
      setRepairing(false);
    }
  };

  return (
    <PhaseCanvas phase="cast">
      {!readOnly && castIncomplete && (
        <div
          className="box-soft"
          style={{
            position: 'absolute',
            top: 8,
            left: 36,
            right: 36,
            padding: '10px 14px',
            background: '#fff4d6',
            border: '1px solid #d6b25a',
            display: 'flex',
            gap: 12,
            alignItems: 'center',
            justifyContent: 'space-between',
            zIndex: 2,
          }}
          role="alert"
        >
          <div style={{ fontSize: 14, color: 'var(--ink)' }}>
            <strong>{castErrors.length}</strong> sprite-sheet
            {castErrors.length === 1 ? '' : 's'} failed to write to disk
            {castErrors.length > 0 && (
              <>
                {' '}
                ({castErrors.map((e) => e.name).join(', ')})
              </>
            )}
            . Click <em>repair cast</em> to regenerate.
          </div>
          <button
            type="button"
            className="pressy"
            onClick={onRepair}
            disabled={repairing}
            style={{
              padding: '6px 14px',
              fontSize: 14,
              cursor: repairing ? 'wait' : 'pointer',
            }}
          >
            {repairing ? 'repairing…' : 'repair cast'}
          </button>
        </div>
      )}
      <div
        style={{
          position: 'absolute',
          top: castIncomplete && !readOnly ? 64 : 24,
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
            onClick={
              readOnly
                ? undefined
                : () => openPopover({ kind: 'character-edit', characterId: c.id })
            }
          />
        ))}
        {!readOnly && (
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
        )}
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
        {readOnly ? (
          <div
            className="hand"
            style={{ fontSize: 14, color: 'var(--ink-faint)' }}
          >
            {characters.length} cast member{characters.length === 1 ? '' : 's'} ·
            this is the snapshot at the time of render.
          </div>
        ) : characters.length === 0 ? (
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
