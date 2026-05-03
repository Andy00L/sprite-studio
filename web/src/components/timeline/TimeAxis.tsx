import type { JSX } from 'react';
import { PAD_X, PX_PER_SEC, TIMELINE_AXIS_H, type PlacedShot } from '../../lib/shotMath';

interface Props {
  stripWidth: number;
  totalSeconds: number;
  placed: PlacedShot[];
}

export function TimeAxis({ stripWidth, totalSeconds, placed }: Props): JSX.Element {
  const tickCount = Math.ceil(totalSeconds) + 1;
  return (
    <svg width={stripWidth} height={TIMELINE_AXIS_H} style={{ display: 'block' }}>
      <line
        x1={PAD_X}
        x2={stripWidth - PAD_X}
        y1={TIMELINE_AXIS_H - 12}
        y2={TIMELINE_AXIS_H - 12}
        stroke="var(--rule-soft)"
        strokeWidth={0.8}
        strokeDasharray="2 3"
      />
      {Array.from({ length: tickCount }).map((_, i) => {
        const tx = PAD_X + i * PX_PER_SEC;
        const isMajor = i % 5 === 0;
        return (
          <g key={i}>
            <line
              x1={tx}
              x2={tx}
              y1={TIMELINE_AXIS_H - 12}
              y2={TIMELINE_AXIS_H - (isMajor ? 4 : 8)}
              stroke="var(--rule)"
              strokeWidth={isMajor ? 1 : 0.6}
              opacity={isMajor ? 0.7 : 0.4}
            />
            {isMajor && (
              <text
                x={tx}
                y={TIMELINE_AXIS_H - 18}
                fontSize={8}
                fontFamily="var(--mono)"
                fill="var(--ink-faint)"
                textAnchor="middle"
              >
                {i}s
              </text>
            )}
          </g>
        );
      })}
      {placed.map((s) => (
        <circle key={`m-${s.id}`} cx={s.x + 6} cy={TIMELINE_AXIS_H - 12} r={1.6} fill="var(--accent)" />
      ))}
    </svg>
  );
}
