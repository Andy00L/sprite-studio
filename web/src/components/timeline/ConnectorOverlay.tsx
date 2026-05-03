// SVG overlay drawn above the entire phase canvas. Lines run from each
// character row anchor down to each shot card it appears in. Recomputes on
// scroll, resize, and data-shape change so paths track the strip when the
// user scrolls horizontally. Pointer-events stay disabled so clicks fall
// through to the shot cards underneath.
//
// Refs are read inside useLayoutEffect (the only place React allows it
// without lint complaints), and the resulting paths are stored in state.

import { useLayoutEffect, useState } from 'react';
import type { JSX, RefObject } from 'react';
import type { Character, Shot } from '../../types/sprite';
import { connectorPath } from './connectorPath';

interface Props {
  canvasRef: RefObject<HTMLDivElement | null>;
  scrollRef: RefObject<HTMLDivElement | null>;
  charRefs: { current: Record<string, HTMLElement | null> };
  shotRefs: { current: Record<string, HTMLElement | null> };
  characters: Character[];
  shots: Shot[];
}

interface PathItem {
  key: string;
  d: string;
  isSpeaker: boolean;
  ox: number;
  oy: number;
  tx: number;
  ty: number;
}

function buildShapeKey(characters: Character[], shots: Shot[]): string {
  const c = characters.map((x) => x.id).join('|');
  const s = shots
    .map((x) => {
      const speakers = (x.character_dialog ?? []).map((d) => d.char_id).join(',');
      return `${x.id}:${x.characters_present.join(',')}:${speakers}`;
    })
    .join('|');
  return `${c}::${s}`;
}

function pathsEqual(a: PathItem[], b: PathItem[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i].key !== b[i].key || a[i].d !== b[i].d || a[i].isSpeaker !== b[i].isSpeaker) {
      return false;
    }
  }
  return true;
}

export function ConnectorOverlay({
  canvasRef,
  scrollRef,
  charRefs,
  shotRefs,
  characters,
  shots,
}: Props): JSX.Element {
  const [paths, setPaths] = useState<PathItem[]>([]);
  const shapeKey = buildShapeKey(characters, shots);

  useLayoutEffect(() => {
    const compute = () => {
      const canvas = canvasRef.current;
      if (!canvas) {
        setPaths((prev) => (prev.length === 0 ? prev : []));
        return;
      }
      const canvasRect = canvas.getBoundingClientRect();
      const localOf = (el: HTMLElement | null) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {
          x: r.left - canvasRect.left,
          y: r.top - canvasRect.top,
          w: r.width,
          h: r.height,
        };
      };

      const charAnchors: { id: string; x: number; y: number }[] = [];
      for (const c of characters) {
        const r = localOf(charRefs.current[c.id]);
        if (r) charAnchors.push({ id: c.id, x: r.x + r.w / 2, y: r.y + r.h - 4 });
      }

      const next: PathItem[] = [];
      for (const s of shots) {
        const r = localOf(shotRefs.current[s.id]);
        if (!r) continue;
        const sx = r.x + r.w / 2;
        const sy = r.y + 4;
        const speakers = new Set((s.character_dialog ?? []).map((d) => d.char_id));
        const n = s.characters_present.length;
        s.characters_present.forEach((cid, ci) => {
          const c = charAnchors.find((a) => a.id === cid);
          if (!c) return;
          const slot = (ci + 1) / (n + 1);
          const tx = sx - r.w / 2 + Math.min(r.w - 8, Math.max(8, slot * r.w));
          const ty = sy;
          const isSpeaker = speakers.has(cid);
          next.push({
            key: `${s.id}-${cid}`,
            d: connectorPath('curved', c.x, c.y, tx, ty),
            isSpeaker,
            ox: c.x,
            oy: c.y,
            tx,
            ty,
          });
        });
      }

      setPaths((prev) => (pathsEqual(prev, next) ? prev : next));
    };

    compute();

    const sc = scrollRef.current;
    sc?.addEventListener('scroll', compute, { passive: true });
    window.addEventListener('resize', compute);

    let ro: ResizeObserver | null = null;
    const canvas = canvasRef.current;
    if (typeof ResizeObserver !== 'undefined' && canvas) {
      ro = new ResizeObserver(compute);
      ro.observe(canvas);
    }

    return () => {
      sc?.removeEventListener('scroll', compute);
      window.removeEventListener('resize', compute);
      ro?.disconnect();
    };
    // characters/shots are read through closure inside compute(); shapeKey
    // captures every geometry-affecting change so a stable dep list is enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvasRef, scrollRef, charRefs, shotRefs, shapeKey]);

  return (
    <svg
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        overflow: 'hidden',
      }}
    >
      {paths.map((p) => (
        <path
          key={p.key}
          d={p.d}
          stroke={p.isSpeaker ? 'var(--accent)' : 'var(--ink-faint)'}
          strokeWidth={p.isSpeaker ? 1.4 : 0.9}
          strokeDasharray={p.isSpeaker ? '0' : '2 3'}
          fill="none"
          opacity={p.isSpeaker ? 0.85 : 0.5}
        />
      ))}
      {paths.map((p) => (
        <g key={`cap-${p.key}`}>
          <circle
            cx={p.ox}
            cy={p.oy}
            r={1.6}
            fill={p.isSpeaker ? 'var(--accent)' : 'var(--ink-faint)'}
            opacity={0.7}
          />
          <circle
            cx={p.tx}
            cy={p.ty}
            r={p.isSpeaker ? 2.2 : 1.4}
            fill={p.isSpeaker ? 'var(--accent)' : 'var(--ink-faint)'}
          />
        </g>
      ))}
    </svg>
  );
}
