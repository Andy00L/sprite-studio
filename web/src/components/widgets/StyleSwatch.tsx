import type { JSX } from 'react';
import type { VisualKey } from '../../lib/styleVisuals';

interface Recipe {
  bg: string;
  ink: string;
  accent: string;
  grain: 'heavy' | 'medium' | 'light' | 'scanline' | 'none';
  overlay: 'venetian' | 'flare' | 'tracking' | 'window' | 'shadow' | 'none';
  figureFill: string;
  label: string;
}

const RECIPES: Record<VisualKey, Recipe> = {
  noir: {
    bg: '#2a2422',
    ink: '#e8d8b8',
    accent: '#d97a4a',
    grain: 'heavy',
    overlay: 'venetian',
    figureFill: '#1a1614',
    label: 'NOIR',
  },
  s8: {
    bg: '#f0d8a8',
    ink: '#5a3a20',
    accent: '#c46a2c',
    grain: 'medium',
    overlay: 'flare',
    figureFill: '#a87a48',
    label: 'S-8',
  },
  vhs: {
    bg: '#0a1226',
    ink: '#7aa8d8',
    accent: '#d04060',
    grain: 'scanline',
    overlay: 'tracking',
    figureFill: '#1c2a4a',
    label: 'VHS',
  },
  hd: {
    bg: '#e8ecee',
    ink: '#2a3438',
    accent: '#3a7a8a',
    grain: 'none',
    overlay: 'window',
    figureFill: '#94a4ac',
    label: 'HD',
  },
  bw: {
    bg: '#f4f1ea',
    ink: '#0a0a0a',
    accent: '#0a0a0a',
    grain: 'light',
    overlay: 'shadow',
    figureFill: '#0a0a0a',
    label: 'B&W',
  },
  custom: {
    bg: 'transparent',
    ink: 'var(--ink-faint)',
    accent: 'var(--accent)',
    grain: 'none',
    overlay: 'none',
    figureFill: 'var(--ink-faint)',
    label: '?',
  },
};

interface Props {
  visual: VisualKey;
  size?: number;
  selected?: boolean;
}

export function StyleSwatch({ visual, size = 56, selected = false }: Props): JSX.Element {
  const r = RECIPES[visual];
  const w = size;
  const h = Math.round(size * 0.62);
  const borderColor = selected ? 'var(--accent)' : 'var(--rule)';
  const borderStyle = visual === 'custom' ? 'dashed' : 'solid';

  const grainDots: JSX.Element[] = [];
  if (r.grain !== 'none') {
    const n =
      r.grain === 'heavy'
        ? 22
        : r.grain === 'medium'
          ? 14
          : r.grain === 'light'
            ? 8
            : 0;
    for (let i = 0; i < n; i++) {
      const x = ((i * 37) % w) + ((i * 13) % 5) - 2;
      const y = ((i * 53) % h) + ((i * 7) % 4) - 2;
      grainDots.push(<rect key={i} x={x} y={y} width={1} height={1} fill={r.ink} opacity={0.4} />);
    }
  }

  return (
    <div
      style={{
        width: w,
        height: h,
        position: 'relative',
        border: `1.5px ${borderStyle} ${borderColor}`,
        borderRadius: '3px 5px 4px 6px / 5px 4px 6px 3px',
        overflow: 'hidden',
        background: r.bg,
        flex: '0 0 auto',
      }}
    >
      <svg width={w} height={h} style={{ display: 'block' }} viewBox={`0 0 ${w} ${h}`}>
        {visual !== 'custom' && (
          <g opacity={0.92}>
            <ellipse cx={w * 0.38} cy={h * 0.55} rx={w * 0.08} ry={w * 0.08} fill={r.figureFill} />
            <path
              d={`M${w * 0.26},${h} Q${w * 0.26},${h * 0.7} ${w * 0.38},${h * 0.68} Q${w * 0.5},${h * 0.7} ${w * 0.5},${h} Z`}
              fill={r.figureFill}
            />
            <ellipse cx={w * 0.7} cy={h * 0.6} rx={w * 0.06} ry={w * 0.06} fill={r.figureFill} opacity={0.7} />
            <path
              d={`M${w * 0.6},${h} Q${w * 0.6},${h * 0.78} ${w * 0.7},${h * 0.74} Q${w * 0.8},${h * 0.78} ${w * 0.8},${h} Z`}
              fill={r.figureFill}
              opacity={0.7}
            />
          </g>
        )}
        {r.overlay === 'venetian' && (
          <g opacity={0.55}>
            {[0, 1, 2, 3].map((i) => (
              <rect
                key={i}
                x={w * 0.35 + i * 5}
                y={-2}
                width={2}
                height={h * 0.7}
                fill={r.accent}
                transform={`rotate(-22 ${w * 0.5} 0)`}
              />
            ))}
          </g>
        )}
        {r.overlay === 'flare' && (
          <g>
            <circle cx={w * 0.78} cy={h * 0.18} r={w * 0.18} fill={r.accent} opacity={0.45} />
            <circle cx={w * 0.78} cy={h * 0.18} r={w * 0.08} fill="#ffe6c0" opacity={0.85} />
          </g>
        )}
        {r.overlay === 'tracking' && (
          <g>
            {Array.from({ length: Math.floor(h / 2) }).map((_, i) => (
              <line key={i} x1={0} x2={w} y1={i * 2} y2={i * 2} stroke={r.ink} strokeWidth={0.3} opacity={0.25} />
            ))}
            <rect x={0} y={h * 0.4} width={w} height={2} fill={r.accent} opacity={0.6} />
            <rect x={0} y={h * 0.42} width={w} height={1} fill="#7af" opacity={0.5} />
          </g>
        )}
        {r.overlay === 'window' && (
          <rect x={w * 0.55} y={0} width={w * 0.45} height={h * 0.55} fill="#fff" opacity={0.45} />
        )}
        {r.overlay === 'shadow' && (
          <polygon points={`${w * 0.55},0 ${w},0 ${w},${h * 0.7} ${w * 0.7},${h}`} fill={r.ink} opacity={0.85} />
        )}
        {r.overlay === 'none' && visual === 'custom' && (
          <text
            x={w / 2}
            y={h / 2 + 4}
            textAnchor="middle"
            fontFamily="var(--serif)"
            fontStyle="italic"
            fontSize={14}
            fill={r.ink}
          >
            ?
          </text>
        )}
        {grainDots}
      </svg>
      <span
        className="mono"
        style={{
          position: 'absolute',
          left: 3,
          bottom: 2,
          fontSize: 7,
          letterSpacing: 0.5,
          color: r.ink,
          background: 'rgba(0,0,0,0)',
          textShadow: r.bg !== 'transparent' ? `0 0 2px ${r.bg}` : 'none',
          opacity: 0.85,
        }}
      >
        {r.label}
      </span>
    </div>
  );
}
