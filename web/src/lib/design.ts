// Tone palettes for the SVG character placeholder when no master_sheet_path
// exists. Mirrors web/_design_reference/HERMES HIGH/src/data.jsx:TONE_PALETTES.
export const TONE_PALETTES = {
  a: { skin: '#c89a73', hair: '#5a3d2c', cloth: '#8a4a3a', accent: '#d8a079', tag: 'warm' },
  b: { skin: '#8a8fa6', hair: '#2c3140', cloth: '#3f4759', accent: '#a8aebd', tag: 'cool' },
  c: { skin: '#b09075', hair: '#4a3324', cloth: '#6a4f3a', accent: '#c8a786', tag: 'sepia' },
  d: { skin: '#9a948a', hair: '#3d3830', cloth: '#5a544c', accent: '#b0aaa0', tag: 'desat' },
} as const;

export type ToneKey = keyof typeof TONE_PALETTES;

const TONE_KEYS: ToneKey[] = ['a', 'b', 'c', 'd'];

// Deterministic tone derived from character ordinal so the same character keeps
// the same SVG coloring across renders.
export function toneForOrdinal(ordinal: number): ToneKey {
  if (!Number.isFinite(ordinal) || ordinal < 1) return 'a';
  return TONE_KEYS[(ordinal - 1) % TONE_KEYS.length];
}

export type ShotKind = 'wide' | 'two' | 'close' | 'over';

// Shot kind heuristic for the SVG placeholder when no reference_still_path exists.
export function kindForShot(args: {
  characters_present: string[];
  camera?: string | null;
}): ShotKind {
  const cam = (args.camera ?? '').toLowerCase();
  const n = args.characters_present.length;
  if (cam.includes('wide') || cam.includes('overhead') || cam.includes('reveal')) return 'wide';
  if (cam.includes('push') || cam.includes('close')) return 'close';
  if (n >= 2) return 'two';
  if (n === 1) return 'close';
  return 'wide';
}
