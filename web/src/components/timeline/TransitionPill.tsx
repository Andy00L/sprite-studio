import type { JSX, MouseEvent } from 'react';
import type { ShotTransition } from '../../types/sprite';

interface Props {
  current: ShotTransition;
  onClick: () => void;
  // P19a-22: drops the click handler + edit affordance for past-phase views.
  readOnly?: boolean;
}

export function TransitionPill({ current, onClick, readOnly = false }: Props): JSX.Element {
  const handle = (e: MouseEvent): void => {
    if (readOnly) return;
    e.stopPropagation();
    onClick();
  };
  return (
    <div
      className="pill"
      onClick={readOnly ? undefined : handle}
      title={
        readOnly ? `transition: ${current}` : `transition: ${current} (click to edit)`
      }
      style={{
        fontSize: 7,
        padding: '2px 6px',
        borderStyle: 'dashed',
        background: 'var(--paper)',
        color: 'var(--ink-soft)',
        cursor: readOnly ? 'default' : 'pointer',
        borderColor: 'var(--accent)',
        textAlign: 'center',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {`↦ ${current}`}
      {!readOnly && <span style={{ opacity: 0.5, fontSize: 7 }}> ✎</span>}
    </div>
  );
}
