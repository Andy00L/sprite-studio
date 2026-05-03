import type { JSX } from 'react';
import type { ToneKey } from '../../lib/design';
import { SpriteCell } from './SpriteCell';

interface Props {
  tone?: ToneKey;
  size?: number;
  gap?: number;
}

export function SpriteSheet({ tone = 'a', size = 72, gap = 1 }: Props): JSX.Element {
  const cell = (size - gap * 4) / 3;
  return (
    <div
      style={{
        width: size,
        height: size,
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gridTemplateRows: 'repeat(3, 1fr)',
        gap: `${gap}px`,
        padding: `${gap}px`,
        background: 'var(--paper-tint)',
        border: '1px solid var(--rule-soft)',
        borderRadius: '3px 5px 4px 6px / 5px 4px 6px 3px',
      }}
    >
      {Array.from({ length: 9 }).map((_, i) => (
        <div
          key={i}
          style={{
            background: 'var(--paper)',
            display: 'grid',
            placeItems: 'center',
            overflow: 'hidden',
          }}
        >
          <SpriteCell tone={tone} frame={i} size={cell} />
        </div>
      ))}
    </div>
  );
}
