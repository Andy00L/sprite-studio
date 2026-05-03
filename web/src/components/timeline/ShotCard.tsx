import type { JSX, Ref, HTMLAttributes } from 'react';
import type { Shot, Character } from '../../types/sprite';
import { ShotStill } from '../sprites/ShotStill';
import { shotReferenceUrl } from '../../lib/assets';
import { kindForShot } from '../../lib/design';

interface Props {
  shot: Shot;
  ordinal: number;
  width: number;
  height: number;
  characters: Character[];
  projectId: string;
  onClick: () => void;
  ref?: Ref<HTMLDivElement>;
  dragHandle?: HTMLAttributes<HTMLDivElement>;
}

export function ShotCard({
  shot,
  ordinal,
  width,
  height,
  characters,
  projectId,
  onClick,
  ref,
  dragHandle,
}: Props): JSX.Element {
  const hasReal = Boolean(shot.reference_still_path);
  const kind = kindForShot({ characters_present: shot.characters_present, camera: shot.camera });
  const firstLine = shot.character_dialog?.[0];
  const speakerName = firstLine?.char_id
    ? (characters.find((c) => c.id === firstLine.char_id)?.name ?? '')
    : '';

  return (
    <div
      ref={ref}
      className="box-hand pressy"
      onClick={onClick}
      {...dragHandle}
      style={{
        position: 'absolute',
        left: 0,
        top: 4,
        width,
        height: height - 16,
        background: 'var(--paper)',
        padding: 6,
        cursor: 'pointer',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span className="mono" style={{ fontSize: 8, color: 'var(--accent)', fontWeight: 600 }}>
          SHOT {String(ordinal).padStart(2, '0')}
        </span>
        <span className="mono" style={{ fontSize: 8 }}>
          {shot.duration_seconds}s
        </span>
      </div>
      {hasReal ? (
        <img
          src={shotReferenceUrl(projectId, shot.id, shot.updated_at ?? undefined)}
          alt={`shot ${ordinal} reference`}
          style={{
            width: width - 12,
            height: 56,
            objectFit: 'cover',
            display: 'block',
            border: '1px solid var(--rule)',
          }}
        />
      ) : (
        <ShotStill kind={kind} size={{ w: width - 12, h: 56 }} />
      )}
      <div
        className="hand"
        style={{
          fontSize: 12,
          marginTop: 4,
          color: 'var(--ink)',
          lineHeight: 1.15,
          overflow: 'hidden',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
        }}
      >
        {shot.action.slice(0, 80)}
      </div>
      {firstLine && width >= 130 && (
        <div
          style={{
            marginTop: 6,
            padding: '4px 6px',
            border: '1.5px dashed var(--accent)',
            borderRadius: '4px 6px 5px 7px / 6px 5px 7px 4px',
            background: 'var(--accent-tint)',
          }}
        >
          <div className="mono" style={{ fontSize: 7, color: 'var(--accent)' }}>
            {`♪ DIALOG · ${speakerName}`}
          </div>
          <div
            className="hand"
            style={{
              fontSize: 12,
              color: 'var(--ink)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {`"${firstLine.line}"`}
          </div>
        </div>
      )}
    </div>
  );
}
