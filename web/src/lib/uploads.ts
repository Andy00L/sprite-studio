import { assetBase } from './assets';

export interface UploadResult {
  path: string;
  server_path: string;
  bytes: number;
  mime: string;
  width: number;
  height: number;
}

export type UploadErrorCode =
  | 'invalid_mime'
  | 'too_large'
  | 'too_big_dim'
  | 'corrupt'
  | 'missing_field'
  | 'unauthorized'
  | 'no_project'
  | 'internal'
  | 'network'
  | 'aborted';

export interface UploadError {
  code: UploadErrorCode;
  message: string;
}

export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
export const ACCEPT_MIMES: ReadonlySet<string> = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
]);

export interface UploadHandle {
  promise: Promise<UploadResult>;
  abort: () => void;
}

// XHR is used (not `fetch`) because XHR exposes upload-progress events as a
// stable browser API; fetch requires the streams API for that and isn't yet
// reliable across the browsers the dev canvas targets.
export function uploadReference(
  projectId: string,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
): UploadHandle {
  const xhr = new XMLHttpRequest();
  let aborted = false;

  const promise = new Promise<UploadResult>((resolve, reject) => {
    if (!ACCEPT_MIMES.has(file.type)) {
      reject({
        code: 'invalid_mime',
        message: `unsupported type: ${file.type || '(none)'}`,
      } satisfies UploadError);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      reject({
        code: 'too_large',
        message: `file is ${(file.size / 1024 / 1024).toFixed(1)}MB; limit is 5MB`,
      } satisfies UploadError);
      return;
    }

    const apiKey = import.meta.env.VITE_SPRITE_BRIDGE_KEY as string | undefined;
    if (!apiKey) {
      reject({
        code: 'unauthorized',
        message: 'VITE_SPRITE_BRIDGE_KEY missing in .env.local',
      } satisfies UploadError);
      return;
    }

    const url = `${assetBase()}/${encodeURIComponent(projectId)}/refs/upload`;
    const form = new FormData();
    form.append('file', file);

    xhr.open('POST', url);
    xhr.setRequestHeader('Authorization', `Bearer ${apiKey}`);
    if (onProgress) {
      xhr.upload.onprogress = (e: ProgressEvent) => {
        if (e.lengthComputable) onProgress(e.loaded, e.total);
      };
    }
    xhr.onerror = () => {
      reject({
        code: aborted ? 'aborted' : 'network',
        message: aborted ? 'upload aborted' : 'network error',
      } satisfies UploadError);
    };
    xhr.ontimeout = () => {
      reject({ code: 'network', message: 'timeout' } satisfies UploadError);
    };
    xhr.onabort = () => {
      reject({ code: 'aborted', message: 'upload aborted' } satisfies UploadError);
    };
    xhr.onload = () => {
      let json: unknown;
      try {
        json = JSON.parse(xhr.responseText);
      } catch {
        reject({
          code: 'internal',
          message: `bad response: HTTP ${xhr.status}`,
        } satisfies UploadError);
        return;
      }
      if (xhr.status === 200) {
        resolve(json as UploadResult);
        return;
      }
      const body = json as { error?: string; code?: string };
      reject({
        code: (body.code as UploadErrorCode) ?? 'internal',
        message: body.error ?? `HTTP ${xhr.status}`,
      } satisfies UploadError);
    };
    xhr.timeout = 60_000;
    xhr.send(form);
  });

  return {
    promise,
    abort: () => {
      aborted = true;
      try {
        xhr.abort();
      } catch {
        // already finished
      }
    },
  };
}
