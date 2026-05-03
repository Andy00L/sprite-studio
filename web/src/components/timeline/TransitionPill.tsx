import type { JSX, MouseEvent } from 'react';
import type { ShotTransition } from '../../types/sprite';

interface Props {
  current: ShotTransition;
  onClick: () => void;
}

export function TransitionPill({ current, onClick }: Props): JSX.Element {
  const handle = (e: MouseEvent): void => {
    e.stopPropagation();
    onClick();
  };
  return (
    <div
      className="pill"
      onClick={handle}
      title={`transition: ${current} (click to edit)`}
      style={{
        fontSize: 7,
        padding: '2px 6px',
        borderStyle: 'dashed',
        background: 'var(--paper)',
        color: 'var(--ink-soft)',
        cursor: 'pointer',
        borderColor: 'var(--accent)',
        textAlign: 'center',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {`↦ ${current}`} <span style={{ opacity: 0.5, fontSize: 7 }}>✎</span>
    </div>
  );
}
