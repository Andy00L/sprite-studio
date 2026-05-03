// Maps a backend style preset to one of six canonical visual recipes used by
// the StyleSwatch postage-stamp. Heuristic over name/descriptor so any new
// preset gets a non-broken look without code change; falls back to "custom"
// (a question-mark stamp) when nothing matches.

import type { StylePreset } from '../types/sprite';

export type VisualKey = 'noir' | 's8' | 'vhs' | 'hd' | 'bw' | 'custom';

export function presetVisualKey(
  preset: StylePreset | { id?: string; name?: string; descriptor?: string } | null,
): VisualKey {
  if (!preset) return 'custom';
  const text = `${preset.id ?? ''} ${preset.name ?? ''} ${preset.descriptor ?? ''}`.toLowerCase();
  if (text.includes('noir') || text.includes('hardboiled') || text.includes('comic')) return 'noir';
  if (
    text.includes('super 8') ||
    text.includes('vintage') ||
    text.includes('sun-bleach') ||
    text.includes('storybook') ||
    text.includes('watercolor')
  )
    return 's8';
  if (
    text.includes('vhs') ||
    text.includes('analog') ||
    text.includes('retro') ||
    text.includes('cyberpunk') ||
    text.includes('neon')
  )
    return 'vhs';
  if (text.includes('b&w') || text.includes('black and white') || text.includes('monochrome'))
    return 'bw';
  if (
    text.includes('clean') ||
    text.includes('digital') ||
    text.includes('daylight') ||
    text.includes('realism') ||
    text.includes('pixar') ||
    text.includes('3d')
  )
    return 'hd';
  if (
    text.includes('cartoon') ||
    text.includes('anime') ||
    text.includes('ghibli') ||
    text.includes('hand-drawn') ||
    text.includes('pixel')
  )
    return 's8';
  return 'custom';
}
