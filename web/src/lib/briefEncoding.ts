// Pack the BriefScreen's [genre, cast, arc] chip selections into the brief
// text before /sprite_new is called. The brief_clarifier prompt sees these as
// trailing tags and folds them into auto_decisions / skips redundant questions.

export interface BriefTags {
  genre?: string;
  castSize?: string;
  arcShape?: string;
  customStyle?: string;
}

export function packBrief(text: string, tags: BriefTags): string {
  const parts: string[] = [];
  if (tags.genre && tags.genre !== 'auto') parts.push(`[genre: ${tags.genre}]`);
  if (tags.castSize && tags.castSize !== 'auto') parts.push(`[cast: ${tags.castSize}]`);
  if (tags.arcShape && tags.arcShape !== 'auto') parts.push(`[arc: ${tags.arcShape}]`);
  if (tags.customStyle && tags.customStyle.trim()) {
    parts.push(`[style: ${tags.customStyle.trim()}]`);
  }
  const trimmed = text.trim();
  if (parts.length === 0) return trimmed;
  return `${trimmed}\n\n${parts.join(' ')}`;
}
