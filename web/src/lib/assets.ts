// Asset URL builder for the static asset server (port 9120 by default).
// The bridge can't serve files (it's a JSON command sidecar), so we run a
// separate aiohttp process for binary artifacts and bypass the Vite proxy.

import type { Shot } from '../types/sprite';

const DEFAULT_ASSET_BASE = 'http://127.0.0.1:9120';

export function assetBase(): string {
  return import.meta.env.VITE_ASSET_BASE_URL || DEFAULT_ASSET_BASE;
}

// `version` defeats the browser cache after a regenerate. Pass
// character.updated_at (or shot.updated_at) so the URL changes when the
// artifact does, even if the path is identical.
export function characterSheetUrl(
  projectId: string,
  charId: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/cast/${charId}/sheet.png${v}`;
}

export function shotReferenceUrl(
  projectId: string,
  shotId: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/shots/${shotId}/reference.png${v}`;
}

// Disk layout: projects/<projectId>/shots/<shotId>/<filename>.mp4 — the
// inner filename is the external video provider's job ULID (assigned by
// services/seedance.py at save time), not the shot id. Callers must
// extract the basename from `shot.rendered_video_path` and pass it as
// `videoFilename`.
export function shotVideoUrl(
  projectId: string,
  shotId: string,
  videoFilename: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/shots/${shotId}/${videoFilename}${v}`;
}

export function projectFinalVideoUrl(
  projectId: string,
  version?: number | null,
): string {
  const v = version ? `?v=${version}` : '';
  return `${assetBase()}/${projectId}/output/final.mp4${v}`;
}

export async function checkAssetServer(): Promise<boolean> {
  try {
    const r = await fetch(`${assetBase()}/health`, {
      signal: AbortSignal.timeout(1500),
    });
    return r.ok;
  } catch {
    return false;
  }
}

// Extract the inner MP4 filename (a seedance ULID) from a shot's full
// rendered_video_path. Returns null if the shot has not been rendered yet.
// Use this with shotVideoUrl(projectId, shotId, filename).
export function videoFilenameForShot(shot: Shot): string | null {
  const path = shot.rendered_video_path;
  if (!path) return null;
  const parts = path.split('/');
  const last = parts[parts.length - 1];
  return last && last.endsWith('.mp4') ? last : null;
}
