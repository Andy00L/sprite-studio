// Shared timeline-strip layout math. Mirrors the constants in the design
// reference (web/_design_reference/HERMES HIGH/src/phases.jsx:567) so the
// real TimelineScreen and the future PreviewScreen lay out shots the same.

import type { Shot } from '../types/sprite';

export const PX_PER_SEC = 36;
export const MIN_SHOT_W = 110;
export const SHOT_GAP = 8;
export const PAD_X = 24;
export const CARD_H = 188;
export const TIMELINE_AXIS_H = 32;
export const ADD_SHOT_W = 110;

export interface PlacedShot extends Shot {
  x: number;
  w: number;
  cx: number;
  t0: number;
}

export function placeShots(shots: Shot[]): {
  placed: PlacedShot[];
  stripWidth: number;
  totalSeconds: number;
} {
  let acc = 0;
  let x = PAD_X;
  const placed = shots.map((s): PlacedShot => {
    const naturalW = s.duration_seconds * PX_PER_SEC;
    const w = Math.max(naturalW, MIN_SHOT_W);
    const myX = x;
    x += w + SHOT_GAP;
    const myAcc = acc;
    acc += s.duration_seconds;
    return { ...s, x: myX, w, cx: myX + w / 2, t0: myAcc };
  });
  const stripWidth = x + ADD_SHOT_W + PAD_X;
  return { placed, stripWidth, totalSeconds: acc };
}
