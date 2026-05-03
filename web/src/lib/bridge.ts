// Sprite Bridge client. Talks to the local Python sidecar at /api (proxied to
// http://127.0.0.1:8643 in dev) which imports the sprite-studio plugin and
// exposes its slash command handlers as POST /slash.
//
// Why a sidecar instead of POST /v1/chat/completions: Hermes 0.12.0 only
// dispatches plugin slash commands when messages arrive via the gateway's
// chat router (Telegram/Discord/Slack/CLI). The OpenAI-compatible API server
// runs the message through the LLM as conversation, so /sprite_status sent
// there returns "I don't have context for that" instead of the JSON we need.

export interface BridgeError {
  status: number;
  message: string;
}

export interface SlashResult<T = unknown> {
  ok: boolean;
  data: T | null;
  raw: string;
  parseError: string | null;
}

const DEFAULT_BASE = '/api';
const REQUEST_TIMEOUT_MS = 600_000;

export class SpriteBridgeClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string = DEFAULT_BASE, apiKey: string) {
    if (!apiKey) {
      throw new Error(
        'SpriteBridgeClient: apiKey is required (set VITE_SPRITE_BRIDGE_KEY in .env.local)',
      );
    }
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.apiKey = apiKey;
  }

  async health(): Promise<{ status: string; plugin_loaded: boolean }> {
    const resp = await fetch(`${this.baseUrl}/health`);
    if (!resp.ok) {
      throw { status: resp.status, message: `health failed: ${resp.status}` } as BridgeError;
    }
    return resp.json();
  }

  async deleteProject(projectId: string): Promise<{ freed_bytes: number }> {
    const resp = await fetch(`${this.baseUrl}/projects/${projectId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${this.apiKey}` },
    });
    // 404 means already gone. Idempotent success path so a double-click on
    // the trash button doesn't surface a phantom error.
    if (resp.status === 404) return { freed_bytes: 0 };
    if (resp.status === 401 || resp.status === 403) {
      throw { status: resp.status, message: 'Invalid API key' } as BridgeError;
    }
    if (resp.status === 409) {
      const body = (await resp.json().catch(() => ({}))) as { reason?: string };
      throw {
        status: 409,
        message: `project busy: ${body.reason ?? 'unknown'}`,
      } as BridgeError;
    }
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw {
        status: resp.status,
        message: `delete failed (${resp.status}): ${text.slice(0, 300)}`,
      } as BridgeError;
    }
    const body = (await resp.json().catch(() => ({}))) as {
      freed_bytes?: number;
    };
    return { freed_bytes: body.freed_bytes ?? 0 };
  }

  async sendSlash<T = unknown>(command: string, args = ''): Promise<SlashResult<T>> {
    const cleanCommand = command.replace(/^\//, '').trim();
    if (!cleanCommand) {
      throw { status: 0, message: 'sendSlash: command is empty' } as BridgeError;
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    let resp: Response;
    try {
      resp = await fetch(`${this.baseUrl}/slash`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ command: cleanCommand, args }),
        signal: controller.signal,
      });
    } catch (e: unknown) {
      clearTimeout(timeout);
      if (e instanceof DOMException && e.name === 'AbortError') {
        throw { status: 0, message: 'Request timed out' } as BridgeError;
      }
      throw {
        status: 0,
        message:
          'Sprite bridge unreachable. Start with: python /home/drew/sprite-studio/bridge/server.py',
      } as BridgeError;
    }
    clearTimeout(timeout);

    if (resp.status === 401 || resp.status === 403) {
      throw { status: resp.status, message: 'Invalid API key' } as BridgeError;
    }
    if (resp.status === 404) {
      throw {
        status: 404,
        message: `Unknown slash command: /${cleanCommand}`,
      } as BridgeError;
    }
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw {
        status: resp.status,
        message: `Bridge error ${resp.status}: ${text.slice(0, 300)}`,
      } as BridgeError;
    }

    const body = (await resp.json()) as SlashResult<T>;
    return body;
  }
}

let _client: SpriteBridgeClient | null = null;

export function getSpriteBridge(): SpriteBridgeClient {
  if (_client) return _client;
  const key = import.meta.env.VITE_SPRITE_BRIDGE_KEY as string | undefined;
  if (!key) {
    throw new Error(
      'VITE_SPRITE_BRIDGE_KEY not set in .env.local. Copy it from ~/.hermes/.env (API_SERVER_KEY).',
    );
  }
  _client = new SpriteBridgeClient('/api', key);
  return _client;
}
