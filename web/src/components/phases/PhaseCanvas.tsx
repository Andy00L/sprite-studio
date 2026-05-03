import type { JSX, ReactNode, RefObject } from 'react';

interface Props {
  phase: string;
  children?: ReactNode;
  showGrid?: boolean;
  canvasRef?: RefObject<HTMLDivElement | null>;
}

export function PhaseCanvas({ phase, children, showGrid = false, canvasRef }: Props): JSX.Element {
  return (
    <div
      ref={canvasRef ?? undefined}
      className={`paper-bg ${showGrid ? 'wf-grid' : ''}`}
      data-phase={phase}
      style={{ flex: 1, position: 'relative', overflow: 'hidden' }}
    >
      {children}
    </div>
  );
}
