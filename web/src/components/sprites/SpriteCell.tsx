import type { JSX } from 'react';
import { TONE_PALETTES, type ToneKey } from '../../lib/design';

interface Props {
  tone?: ToneKey;
  frame?: number;
  size?: number;
}

export function SpriteCell({ tone = 'a', frame = 0, size = 24 }: Props): JSX.Element {
  const p = TONE_PALETTES[tone];
  const tilt = ((frame % 3) - 1) * 4;
  const eyeY = 11 + Math.floor(frame / 3) * 0.6;
  const mouthW = 4 + ((frame * 7) % 3);
  const mouthCurve = frame % 4 === 0 ? 1 : frame % 5 === 0 ? -1 : 0;
  const eyeOpen = frame === 5 || frame === 8 ? 0.6 : 1;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{ display: 'block' }}>
      <rect x="0" y="0" width="24" height="24" fill={p.skin} opacity="0.18" />
      <g transform={`translate(12 13) rotate(${tilt}) translate(-12 -13)`}>
        <path d={`M5 9 Q12 ${2 + (frame % 2)} 19 9 L19 13 Q12 8 5 13 Z`} fill={p.hair} />
        <ellipse cx="12" cy="13" rx="6" ry="6.5" fill={p.skin} />
        <path d="M3 24 Q12 17 21 24 Z" fill={p.cloth} />
        <ellipse cx="9.6" cy={eyeY} rx="0.7" ry={0.9 * eyeOpen} fill="#1a1814" />
        <ellipse cx="14.4" cy={eyeY} rx="0.7" ry={0.9 * eyeOpen} fill="#1a1814" />
        <path d="M12 13 L11.6 15" stroke={p.hair} strokeWidth="0.5" strokeLinecap="round" opacity="0.5" />
        <path
          d={`M${12 - mouthW / 2} 16.6 Q12 ${16.6 + mouthCurve * 0.6} ${12 + mouthW / 2} 16.6`}
          stroke="#1a1814"
          strokeWidth="0.7"
          fill="none"
          strokeLinecap="round"
        />
        {frame === 4 && <circle cx="17" cy="11" r="0.6" fill={p.accent} opacity="0.8" />}
      </g>
    </svg>
  );
}
