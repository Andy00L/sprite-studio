import type { JSX } from 'react';
import type { ShotKind } from '../../lib/design';

interface Props {
  kind?: ShotKind;
  size?: { w: number; h: number };
  dim?: boolean;
}

export function ShotStill({
  kind = 'wide',
  size = { w: 120, h: 70 },
  dim = false,
}: Props): JSX.Element {
  const W = size.w;
  const H = size.h;
  const ink = 'var(--ink-soft)';
  const tint = 'var(--paper-tint)';
  const opacity = dim ? 0.5 : 1;
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', opacity }}>
      <rect x="0" y="0" width={W} height={H} fill={tint} />
      <line x1={W / 3} y1="0" x2={W / 3} y2={H} stroke="var(--rule-soft)" strokeWidth="0.5" />
      <line x1={(W * 2) / 3} y1="0" x2={(W * 2) / 3} y2={H} stroke="var(--rule-soft)" strokeWidth="0.5" />
      <line x1="0" y1={(H * 2) / 3} x2={W} y2={(H * 2) / 3} stroke="var(--rule-soft)" strokeWidth="0.5" />
      {kind === 'wide' && (
        <>
          <rect x="0" y={H * 0.62} width={W} height={H * 0.38} fill="var(--paper-deep)" />
          <rect x={W * 0.12} y={H * 0.45} width={W * 0.18} height={H * 0.2} fill={ink} opacity="0.7" />
          <rect x={W * 0.66} y={H * 0.5} width={W * 0.1} height={H * 0.13} fill={ink} opacity="0.5" />
          <circle cx={W * 0.18} cy={H * 0.85} r="2" fill="var(--accent)" opacity="0.0" />
        </>
      )}
      {kind === 'two' && (
        <>
          <rect x="0" y={H * 0.65} width={W} height={H * 0.35} fill="var(--paper-deep)" />
          <circle cx={W * 0.34} cy={H * 0.5} r={H * 0.18} fill={ink} opacity="0.55" />
          <circle cx={W * 0.66} cy={H * 0.5} r={H * 0.18} fill={ink} opacity="0.55" />
        </>
      )}
      {kind === 'close' && (
        <>
          <defs>
            <radialGradient id="cls" cx="0.5" cy="0.5" r="0.6">
              <stop offset="0%" stopColor={ink} stopOpacity="0.72" />
              <stop offset="80%" stopColor={ink} stopOpacity="0" />
            </radialGradient>
          </defs>
          <circle cx={W * 0.5} cy={H * 0.55} r={H * 0.55} fill="url(#cls)" />
          <circle cx={W * 0.5} cy={H * 0.5} r="2" fill="var(--accent)" opacity="0.7" />
        </>
      )}
      {kind === 'over' && (
        <>
          <rect x="0" y={H * 0.65} width={W} height={H * 0.35} fill="var(--paper-deep)" />
          <circle cx={W * 0.78} cy={H * 0.45} r={H * 0.2} fill={ink} opacity="0.55" />
          <path
            d={`M0 ${H} L0 ${H * 0.25} Q ${W * 0.18} ${H * 0.2} ${W * 0.32} ${H * 0.45} L ${W * 0.32} ${H} Z`}
            fill={ink}
            opacity="0.85"
          />
        </>
      )}
      <rect x="0.5" y="0.5" width={W - 1} height={H - 1} fill="none" stroke="var(--rule)" strokeWidth="1" />
    </svg>
  );
}
